"""Publish immutable runtime fact generations, making the index visible last.

This is not a filesystem transaction. Failed publication can leave unreferenced
files; the previous index continues to select the previous complete generation.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

from genshin_app.equipment.files import file_sha, fingerprint, json_bytes, publish_files
from genshin_app.equipment.schema import PlanError
from genshin_app.query_cache import protect_inputs

MAX_DELIVERY_BYTES = 8 * 1024 * 1024


@dataclass(frozen=True)
class BatchDirectory:
    requested_directory: Path
    directory: Path
    index_path: Path
    expected_sha: str | None
    protected_paths: tuple[Path, ...]


def _check_target(state):
    if (state.requested_directory.resolve() != state.directory
            or state.index_path.resolve() != state.index_path):
        raise PlanError("批量资料目录或索引路径已改变", "file_conflict")
    protect_inputs([state.index_path], state.protected_paths)
    if file_sha(state.index_path) != state.expected_sha:
        raise PlanError("资料索引已被外部修改，请重新查询或另存", "file_conflict",
                        {"path": str(state.index_path), "published_paths": []})


def prepare_batch_directory(output_dir, *, overwrite=False, protected_paths=()):
    """Capture the index SHA before querying; this operation writes no files."""
    requested = Path(output_dir).absolute()
    directory = requested.resolve()
    index = directory / "index.json"
    protected = tuple(Path(p).resolve() for p in protected_paths)
    try:
        if directory.exists() and not directory.is_dir():
            raise PlanError("批量资料输出目录不是目录", "file_conflict")
        # Do not follow an existing index symlink to an unrelated document.
        if index.resolve() != index:
            raise PlanError("资料索引不能是其他文件的链接", "file_conflict")
        protect_inputs([index], protected)
        if index.exists() and not overwrite:
            raise PlanError("资料索引已存在，需要明确允许覆盖", "file_conflict",
                            {"path": str(index), "published_paths": []})
        if index.exists() and index.stat().st_size > MAX_DELIVERY_BYTES:
            raise PlanError("已有资料索引超过读取限额，请使用新目录", "file_conflict")
        return BatchDirectory(requested, directory, index, file_sha(index), protected)
    except OSError as exc:
        raise PlanError("无法准备批量资料目录", "file_conflict",
                        {"cause": str(exc), "published_paths": []}) from exc


def publish_batch_directory(state, data):
    """Write bounded per-item files and commit their manifest with the saved CAS."""
    published = []
    index_content = None
    generation = state.directory / f"generation-{uuid4().hex}"
    try:
        _check_target(state)
        items = data.get("items")
        if not isinstance(items, list) or not 1 <= len(items) <= 8:
            raise PlanError("批量资料交付需要1至8条查询回执", "invalid_request")
        outputs, entries = [], []
        for position, item in enumerate(items):
            # The user-facing ID is metadata only, never a filename.
            request_sha = fingerprint(item["request"])
            path = generation / f"item-{position:02d}-{request_sha[:16]}.json"
            content = json_bytes({"batch_version": data["batch_version"],
                                  "snapshot": data["snapshot"], **item})
            outputs.append((path, content))
            entries.append({"id": item["id"], "request": item["request"],
                            "status": item["status"],
                            "path": path.relative_to(state.directory).as_posix(),
                            "file_sha": hashlib.sha256(content).hexdigest(),
                            "bytes": len(content)})
        index = {"file_index_version": 1, "batch_version": data["batch_version"],
                 "status": data["status"], "snapshot": data["snapshot"],
                 "counts": data["counts"], "limits": data["limits"],
                 "content_fingerprint": data["content_fingerprint"], "items": entries}
        index_content = json_bytes(index)
        total = len(index_content) + sum(len(content) for _, content in outputs)
        if total > MAX_DELIVERY_BYTES:
            raise PlanError("批量资料文件及索引超过8MiB交付限额", "response_budget_exceeded",
                            {"actual_bytes": total, "max_bytes": MAX_DELIVERY_BYTES})
        all_paths = [path for path, _ in outputs] + [state.index_path]
        protect_inputs(all_paths, state.protected_paths)
        state.directory.mkdir(parents=True, exist_ok=True)
        _check_target(state)
        # Exclusive creation means a UUID collision cannot overwrite an old fact.
        generation.mkdir()
        if generation.resolve() != generation:
            raise PlanError("资料生成代路径已改变", "file_conflict")
        report = publish_files(outputs, input_paths=state.protected_paths)
        published.extend(report["published_paths"])
        for (path, content), entry in zip(outputs, entries, strict=True):
            if path.stat().st_size != len(content) or file_sha(path) != entry["file_sha"]:
                raise PlanError("资料文件在索引提交前被修改", "file_conflict", {"path": str(path)})
        _check_target(state)
        protect_inputs(all_paths, state.protected_paths)
        # Only the index is replaced; old generations are never repaired/deleted.
        from genshin_app.output_policy import ordinary_output_scope
        with ordinary_output_scope(protected_files=state.protected_paths):
            final = publish_files([(state.index_path, index_content)], input_paths=state.protected_paths,
                                  expected_shas={state.index_path: state.expected_sha})
        published.extend(final["published_paths"])
        return {"path": str(state.index_path), "file_sha": final["file_shas"][str(state.index_path)],
                "index": index, "generation_path": str(generation),
                "published_paths": published,
                "file_shas": {**report["file_shas"], **final["file_shas"]},
                "bytes": total}
    except (OSError, PlanError) as exc:
        details = getattr(exc, "details", None)
        if isinstance(details, dict):
            published.extend(p for p in details.get("published_paths", []) if p not in published)
        # A replace may succeed before a subsequent receipt read/lock cleanup fails.
        # Report the observed index instead of claiming every exception rolled it back.
        try:
            observed = file_sha(state.index_path)
            intended = hashlib.sha256(index_content).hexdigest() if index_content is not None else None
            if intended is not None and observed == intended:
                committed, index_state = True, "new_generation"
                if str(state.index_path) not in published:
                    published.append(str(state.index_path))
            elif observed == state.expected_sha:
                committed, index_state = False, "previous_index"
            else:
                committed, index_state = None, "externally_changed"
        except OSError:
            committed, index_state = None, "unreadable"
        raise PlanError(str(exc), getattr(exc, "reason", "file_conflict"),
                        {"published_paths": published, "index_committed": committed,
                         "observed_index_state": index_state,
                         "index_path": str(state.index_path), "generation_path": str(generation),
                         "cause": details if details is not None else str(exc)}) from exc
