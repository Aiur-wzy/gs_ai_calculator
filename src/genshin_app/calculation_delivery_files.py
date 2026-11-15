"""Publish one immutable calculation generation and commit its index last.

This is index-level atomic visibility, not a filesystem transaction. Old
generations and failed unreferenced files are retained, never repaired/deleted.
"""
from __future__ import annotations

import copy
import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

from genshin_app.calculation_capacity import (
    CALCULATED_ARTIFACT_BYTES,
    CALCULATION_DELIVERY_TOTAL_BYTES,
)
from genshin_app.equipment.files import json_bytes, publish_files
from genshin_app.equipment.schema import PlanError
from genshin_app.query_cache import protect_inputs

DELIVERY_SCHEMA = "genshin.calculation-delivery.v1"
PACKAGE_SCHEMA = "genshin.calculated-package.v1"
DIAGNOSTIC_MESSAGE = "完整交付已保存，但计算包含未完成或失败状态"
LIMITS = {"native_plan_bytes": 8 * 1024 * 1024, "calculated_bytes": CALCULATED_ARTIFACT_BYTES,
          "workbook_bytes": 32 * 1024 * 1024, "manifest_bytes": 64 * 1024,
          "index_bytes": 64 * 1024, "summary_bytes": 64 * 1024,
          "total_bytes": CALCULATION_DELIVERY_TOTAL_BYTES}
ARTIFACT_NAMES = {"native_plan": "native.plan.json", "calculated": "calculated.json",
                  "result_workbook": "result.xlsx", "formula_workbook": "formula.xlsx"}
_SHA = re.compile(r"[0-9a-f]{64}\Z")
_GENERATION = re.compile(r"generation-[0-9a-f]{32}\Z")


@dataclass(frozen=True)
class DeliveryDirectory:
    requested_directory: Path
    directory: Path
    index_path: Path
    expected_sha: str | None
    protected_paths: tuple[Path, ...]
    protected_requests: tuple[tuple[Path, Path], ...]
    generation_name: str

    @property
    def generation_path(self):
        return self.directory / self.generation_name


def _sha(content):
    return hashlib.sha256(content).hexdigest()


def _budget(label, size, maximum):
    if size > maximum:
        raise PlanError("完整交付超过字节限额", "response_budget_exceeded",
                        {"phase": label, "actual_bytes": size, "max_bytes": maximum})


def _digest_file(path, maximum):
    """Hash only bounded regular contents; never read an external replacement whole."""
    try:
        with path.open("rb") as stream:
            digest, count = hashlib.sha256(), 0
            while chunk := stream.read(min(1024 * 1024, maximum + 1 - count)):
                count += len(chunk)
                if count > maximum:
                    raise PlanError("交付关联文件在读取期间超过限额", "file_conflict", {"path": str(path)})
                digest.update(chunk)
            return digest.hexdigest()
    except FileNotFoundError:
        return None


def _protected(paths):
    requests, protected = [], set()
    for item in paths:
        requested = Path(item).absolute()
        resolved = requested.resolve()
        requests.append((requested, resolved))
        protected.add(resolved)
        # Lock/sidecar names are reserved even before a writer creates them.
        protected.update(resolved.with_name(resolved.name + suffix) for suffix in ("-journal", "-wal", "-shm"))
        protected.add(resolved.with_name(f".{resolved.name}.write.lock"))
        protected.add(resolved.with_name(f".{resolved.name}.plan.lock"))
    return tuple(sorted(protected, key=str)), tuple(requests)


def _entry_valid(entry, role, path):
    limit = LIMITS["native_plan_bytes" if role == "native_plan" else
                   "calculated_bytes" if role == "calculated" else "workbook_bytes"]
    return (isinstance(entry, dict) and set(entry) == {"role", "path", "bytes", "sha256"}
            and entry["role"] == role and entry["path"] == path
            and type(entry["bytes"]) is int and 0 <= entry["bytes"] <= limit
            and isinstance(entry["sha256"], str) and _SHA.fullmatch(entry["sha256"]))


def _validate_old_index(raw):
    """Recognize our exact commit-pointer schema, not a user JSON lookalike."""
    try:
        value = json.loads(raw.decode("utf-8"))
        expected = {"delivery_schema", "generation", "manifest", "input_fingerprint",
                    "result_fingerprint", "artifacts", "calculation_status"}
        generation = value.get("generation") if isinstance(value, dict) else None
        if (not isinstance(value, dict) or set(value) != expected
                or value["delivery_schema"] != DELIVERY_SCHEMA
                or not isinstance(generation, str) or not _GENERATION.fullmatch(generation)
                or value["calculation_status"] not in ("complete", "diagnostic")
                or any(not isinstance(value[k], str) or not _SHA.fullmatch(value[k])
                       for k in ("input_fingerprint", "result_fingerprint"))):
            raise ValueError("index schema")
        manifest = value["manifest"]
        if (not isinstance(manifest, dict) or set(manifest) != {"path", "bytes", "sha256"}
                or manifest["path"] != f"{generation}/manifest.json"
                or type(manifest["bytes"]) is not int or not 0 < manifest["bytes"] <= LIMITS["manifest_bytes"]
                or not isinstance(manifest["sha256"], str) or not _SHA.fullmatch(manifest["sha256"])):
            raise ValueError("manifest identity")
        entries = value["artifacts"]
        if (not isinstance(entries, list) or not 1 <= len(entries) <= len(ARTIFACT_NAMES)
                or any(not isinstance(entry, dict) for entry in entries)):
            raise ValueError("artifact identity")
        roles = [entry.get("role") for entry in entries]
        selected = [(role, name) for role, name in ARTIFACT_NAMES.items() if role in roles]
        if (len(selected) != len(entries)
                or any(not _entry_valid(entry, role, f"{generation}/{name}")
                       for entry, (role, name) in zip(entries, selected, strict=True))):
            raise ValueError("artifact identity")
    except (UnicodeError, ValueError, TypeError, RecursionError) as exc:
        raise PlanError("已有索引不是受支持的完整交付索引，请使用新目录", "file_conflict") from exc


def _paths(state):
    return [state.generation_path / name for name in (*ARTIFACT_NAMES.values(), "manifest.json")] + [state.index_path]


def _check_environment(state):
    if (not _GENERATION.fullmatch(state.generation_name)
            or state.index_path != state.directory / "index.json"
            or state.requested_directory.resolve() != state.directory
            or any(requested.resolve() != resolved for requested, resolved in state.protected_requests)):
        raise PlanError("交付目录或受保护输入路径已改变", "file_conflict")
    if state.directory.exists() and not state.directory.is_dir():
        raise PlanError("完整交付输出目录不是目录", "file_conflict")
    targets = _paths(state)
    touched = [item for path in targets for item in
               (path, path.with_name(path.name + ".previous"), path.with_name(f".{path.name}.plan.lock"))]
    if any(path.is_symlink() or path.resolve() != path for path in touched):
        raise PlanError("交付路径不能被替换为文件或目录链接", "file_conflict")
    protect_inputs(targets, state.protected_paths)
    if any(path.exists() and path.stat().st_nlink > 1 for path in touched):
        raise PlanError("交付文件、备份或锁存在硬链接别名", "file_conflict")
    backup = state.index_path.with_name("index.json.previous")
    if backup.exists():
        with backup.open("rb") as stream:
            raw = stream.read(LIMITS["index_bytes"] + 1)
        _budget("existing_backup", len(raw), LIMITS["index_bytes"])
        _validate_old_index(raw)
    if _digest_file(state.index_path, LIMITS["index_bytes"]) != state.expected_sha:
        raise PlanError("交付索引已被外部修改，请重新计算或另存", "file_conflict")


def prepare_delivery_directory(output_dir, *, overwrite=False, protected_paths=()):
    """Read-only preflight: capture the previous index before expensive calculation."""
    try:
        requested = Path(output_dir).absolute()
        directory = requested.resolve()
        index = directory / "index.json"
        protected, requests = _protected(protected_paths)
        if directory.exists() and not directory.is_dir():
            raise PlanError("完整交付输出目录不是目录", "file_conflict")
        if index.is_symlink() or index.resolve() != index:
            raise PlanError("完整交付索引不能是文件链接", "file_conflict")
        protect_inputs([index], protected)
        if index.exists() and not overwrite:
            raise PlanError("交付索引已存在，需要明确允许覆盖", "file_conflict")
        raw = None
        if index.exists():
            with index.open("rb") as stream:
                raw = stream.read(LIMITS["index_bytes"] + 1)
            _budget("existing_index", len(raw), LIMITS["index_bytes"])
            _validate_old_index(raw)
        state = DeliveryDirectory(requested, directory, index, _sha(raw) if raw is not None else None,
                                  protected, requests, f"generation-{uuid4().hex}")
        if state.generation_path.exists() or state.generation_path.is_symlink():
            raise PlanError("生成代已存在，不能重写旧文件", "file_conflict")
        _check_environment(state)
        return state
    except (OSError, RuntimeError, TypeError) as exc:
        raise PlanError("无法准备完整交付目录", "file_conflict", {"cause": str(exc)[:512]}) from exc


def _build_documents(state, built, input_file_sha):
    if (not isinstance(built, dict) or set(built) != {"artifacts", "metadata", "summary", "timings", "diagnostic"}
            or type(built["diagnostic"]) is not bool
            or any(not isinstance(built[k], dict) for k in ("artifacts", "metadata", "summary", "timings"))
            or not built["artifacts"] or set(built["artifacts"]) - set(ARTIFACT_NAMES)):
        raise PlanError("完整交付内部构建结果无效", "invalid_request")
    metadata, summary, timings = (copy.deepcopy(built[k]) for k in ("metadata", "summary", "timings"))
    if (metadata.get("package_schema") != PACKAGE_SCHEMA or metadata.get("input_file_sha") != input_file_sha
            or metadata.get("calculation_status") != ("diagnostic" if built["diagnostic"] else "complete")
            or any(not isinstance(metadata.get(k), str) or not _SHA.fullmatch(metadata[k])
                   for k in ("native_input_fingerprint", "input_fingerprint", "output_fingerprint", "source_fingerprint",
                             "result_fingerprint", "calculated_fingerprint"))):
        raise PlanError("完整交付元信息与本次输入不一致", "invalid_request")
    reserved = {"delivery_schema", "generation", "publication_status", "artifacts", "total_bytes", "limits", "summary", "timings"}
    if metadata.keys() & reserved:
        raise PlanError("完整交付元信息占用发布字段", "invalid_request")
    outputs, entries = [], []
    for role, name in ARTIFACT_NAMES.items():
        if role not in built["artifacts"]:
            continue
        content = built["artifacts"][role]
        if not isinstance(content, bytes):
            raise PlanError("完整交付产物必须是已编码字节", "invalid_request")
        limit_key = "native_plan_bytes" if role == "native_plan" else "calculated_bytes" if role == "calculated" else "workbook_bytes"
        _budget(role, len(content), LIMITS[limit_key])
        outputs.append((state.generation_path / name, content))
        entries.append({"role": role, "path": name, "bytes": len(content), "sha256": _sha(content)})
    artifact_bytes = sum(entry["bytes"] for entry in entries)
    _budget("total", artifact_bytes, LIMITS["total_bytes"])
    manifest = {"delivery_schema": DELIVERY_SCHEMA, "generation": state.generation_name,
                **metadata, "publication_status": "complete", "artifacts": entries,
                "summary": summary, "timings": timings, "limits": dict(LIMITS), "total_bytes": 0}
    # Only decimal digit widths feed back into total_bytes; hashes have fixed width.
    for _ in range(16):
        manifest_content = json_bytes(manifest)
        index = {"delivery_schema": DELIVERY_SCHEMA, "generation": state.generation_name,
                 "manifest": {"path": f"{state.generation_name}/manifest.json", "bytes": len(manifest_content),
                              "sha256": _sha(manifest_content)},
                 "input_fingerprint": metadata["input_fingerprint"], "result_fingerprint": metadata["result_fingerprint"],
                 "calculation_status": metadata["calculation_status"],
                 "artifacts": [{**entry, "path": f"{state.generation_name}/{entry['path']}"} for entry in entries]}
        index_content = json_bytes(index)
        total = artifact_bytes + len(manifest_content) + len(index_content)
        if total == manifest["total_bytes"]:
            break
        manifest["total_bytes"] = total
    else:
        raise PlanError("完整交付字节计数无法收敛", "invalid_request")
    _budget("manifest", len(manifest_content), LIMITS["manifest_bytes"])
    _budget("index", len(index_content), LIMITS["index_bytes"])
    _budget("total", total, LIMITS["total_bytes"])
    data = {"publication_status": "complete", "index_committed": True,
            "index_path": str(state.index_path), "index_sha256": _sha(index_content),
            "generation_path": str(state.generation_path), "manifest_path": str(state.generation_path / "manifest.json"),
            "artifacts": [{**entry, "path": str(state.generation_path / entry["path"])} for entry in entries],
            "metadata": {k: v for k, v in metadata.items() if k != "data_versions"},
            "summary": summary, "timings": timings, "total_bytes": total}
    success = {"code": 0, "message": "ok", "data": data}
    diagnostic = {"code": 1, "message": DIAGNOSTIC_MESSAGE, "reason": "calculation_diagnostic", "details": data}
    # Match both actual CLI serializers including the Windows CRLF worst case.
    for envelope in (success, diagnostic):
        for indent in (None, 2):
            emitted = json.dumps(envelope, ensure_ascii=False, indent=indent, allow_nan=False)
            encoded = (emitted.replace("\n", "\r\n") + "\r\n").encode("utf-8")
            _budget("summary", len(encoded), LIMITS["summary_bytes"])
    outputs.append((state.generation_path / "manifest.json", manifest_content))
    return outputs, index_content, data


def _check_input(path, expected):
    if not isinstance(expected, str) or not _SHA.fullmatch(expected):
        raise PlanError("完整交付需要精确输入文件SHA", "invalid_request")
    if _digest_file(path, LIMITS["native_plan_bytes"]) != expected:
        raise PlanError("原生输入文件在计算期间已改变，请重新计算", "file_conflict")


def publish_calculation_delivery(state, built, *, input_path, input_file_sha):
    """Publish immutable artifacts, then compare-and-swap the bounded commit pointer."""
    published, index_content = [], None
    try:
        if not isinstance(state, DeliveryDirectory) or not isinstance(input_path, (str, Path)):
            raise PlanError("完整交付需要本次准备状态与原生输入路径", "invalid_request")
        input_path = Path(input_path).resolve()
        # Enforce input protection even if an internal caller omitted it at prepare.
        input_protected, _ = _protected([input_path])
        _check_input(input_path, input_file_sha)
        _check_environment(state)
        protect_inputs(_paths(state), [*state.protected_paths, *input_protected])
        outputs, index_content, data = _build_documents(state, built, input_file_sha)
        state.directory.mkdir(parents=True, exist_ok=True)
        _check_environment(state)
        state.generation_path.mkdir()  # Exclusive; no overwrite, including failed old generations.
        _check_environment(state)
        written = publish_files(outputs, input_paths=[*state.protected_paths, *input_protected])
        published.extend(written["published_paths"])
        for path, content in outputs:
            if (path.is_symlink() or path.resolve() != path or path.stat().st_size != len(content)
                    or _digest_file(path, len(content)) != _sha(content)):
                raise PlanError("生成代文件在索引提交前被修改", "file_conflict", {"path": str(path)})
        _check_input(input_path, input_file_sha)
        _check_environment(state)
        protect_inputs(_paths(state), [*state.protected_paths, *input_protected])
        from genshin_app.output_policy import ordinary_output_scope
        with ordinary_output_scope(protected_files=[*state.protected_paths, *input_protected]):
            final = publish_files([(state.index_path, index_content)], expected_shas={state.index_path: state.expected_sha},
                                  input_paths=[*state.protected_paths, *input_protected])
        published.extend(final["published_paths"])
        if _digest_file(state.index_path, LIMITS["index_bytes"]) != data["index_sha256"]:
            raise PlanError("索引提交后内容已改变", "file_conflict")
        return {"data": data, "diagnostic": built["diagnostic"]}
    except (OSError, ValueError, TypeError, RuntimeError) as exc:
        if not isinstance(state, DeliveryDirectory):
            raise PlanError("完整交付准备状态无效", "invalid_request") from exc
        allowed = {str(path) for path in _paths(state)}
        details = getattr(exc, "details", None)
        if isinstance(details, dict):
            published.extend(p for p in details.get("published_paths", []) if p in allowed and p not in published)
        try:
            observed = _digest_file(state.index_path, LIMITS["index_bytes"])
            if index_content is not None and observed == _sha(index_content):
                committed, observed_state = True, "new_generation"
                if str(state.index_path) not in published:
                    published.append(str(state.index_path))
            elif observed == state.expected_sha:
                committed, observed_state = False, "previous_index"
            else:
                committed, observed_state = None, "externally_changed"
        except (OSError, ValueError, RuntimeError):
            committed, observed_state = None, "unreadable"
        bounded_details = {"phase": "publish", "published_paths": published,
                           "index_committed": committed, "observed_index_state": observed_state,
                           "index_path": str(state.index_path), "generation_path": str(state.generation_path),
                           "cause": str(exc)[:512]}
        if isinstance(details, dict):
            bounded_details.update({k: details[k] for k in ("phase", "actual_bytes", "max_bytes") if k in details})
        raise PlanError("完整交付未正常完成，请核对实际索引提交状态", getattr(exc, "reason", "file_conflict"), bounded_details) from exc
