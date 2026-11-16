"""Shared GUI/CLI compare-and-swap file publication and deterministic fingerprints."""
from __future__ import annotations

import contextlib
import hashlib
import json
import os
import tempfile
from pathlib import Path

from genshin_db.locking import file_lock

from .schema import PlanError, validate_plan


def json_bytes(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False,
                      separators=(",", ":")).encode("utf-8")


def fingerprint(value):
    return hashlib.sha256(json_bytes(value)).hexdigest()


def input_view(plan):
    return {
        k: v for k, v in plan.items()
        if k not in ("resolution", "resolved_template")
        # An explicitly empty external supplement account has exactly the
        # same calculation meaning as an absent account. Canonicalizing it
        # here keeps every input-bound receipt and ownership proof stable.
        and not (k == "external_damage_supplements" and v == [])
    }


def native_input(plan):
    """Detach editable game input; execution caches never become source hits."""
    import copy

    if not isinstance(plan, dict):
        raise PlanError("原生方案输入必须为对象", "invalid_plan")
    value = copy.deepcopy(input_view(plan))
    value.update(resolution={"state": "pending"}, resolved_template=None)
    validate_plan(value)
    return value


def execution_input_view(plan):
    """External numerical supplements do not participate in engine execution."""
    return {k: v for k, v in input_view(plan).items() if k != "external_damage_supplements"}


def context_view(value, _path=()):
    """Exclude acknowledgements and scoped UI preferences, retaining game inputs."""
    from .materialization_input_context import materialization_fact_context
    materialization = materialization_fact_context(value, _path)
    if materialization is not None:
        return materialization
    if isinstance(value, dict):
        editor_panel = (len(_path) == 2 and _path[0] == "equipment_inputs" and value.get("mode") == "panel")
        editor_board_actor = (len(_path) == 5 and _path[:4] == ("extensions", "whiteboard_handoff", "board", "team"))
        execution_owner = (not _path or _path == ('extensions', 'whiteboard_handoff', 'board'))
        return {k: context_view(v, (*_path, k)) for k, v in value.items() if k not in (
            "resolution", "resolved_template", "revision", "plan_id", "coverage", "reviewed_by",
            "context_fingerprint", "basis_fingerprint", "source_fingerprint", "selected_by",
        ) and not (k == 'calculation_options' and execution_owner)
                and not (k == 'board_fingerprint' and _path == ('extensions', 'whiteboard_handoff'))
                and not (k == "accepted_fingerprint" and _path and _path[0] == "action_capacity_sources")
                and not (k == "panel_basis_mode" and (editor_panel or editor_board_actor))
                and not (k == "external_damage_supplements" and value.get("kind") == "genshin-plan")
                and not (k == "state" and v in ("confirmed", "pending") and
                   ("effect_ref" in value or "condition_id" in value))}
    if isinstance(value, list):
        return [context_view(v, (*_path, index)) for index, v in enumerate(value)]
    return value


def file_sha(path):
    path = Path(path)
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else None


@contextlib.contextmanager
def input_file_errors(*, field="/input"):
    """Type known input-file failures at the read site, never all OSError."""
    try:
        yield
    except (FileNotFoundError, NotADirectoryError) as exc:
        raise PlanError("找不到指定的输入文件，请核对路径后重新读取。", "input_file_not_found",
                        {"path": field, "repair_hint": "提供已存在的输入文件；不会新建或覆盖输入。",
                         "recovery": {"action": "select_input_file", "retry_same_request": False}}) from exc
    except (PermissionError, IsADirectoryError) as exc:
        raise PlanError("无法读取指定的输入文件，请检查是否选中了目录或没有读取权限。", "input_file_not_readable",
                        {"path": field, "repair_hint": "选择可读取的普通文件，再重试读取。",
                         "recovery": {"action": "select_input_file", "retry_same_request": False}}) from exc


def read_input_bytes(path, *, maximum_bytes=None, field="/input"):
    """Read raw bytes for existing format-specific validators and budgets."""
    if not isinstance(path, (str, os.PathLike)) or (isinstance(path, str) and (not path or "\0" in path)):
        raise PlanError("请提供有效的输入文件路径。", "input_file_invalid", {"path": field})
    with input_file_errors(field=field), Path(path).open("rb") as stream:
        return stream.read() if maximum_bytes is None else stream.read(maximum_bytes + 1)


def read_input_json(path, *, field="/input"):
    """Legacy plain JSON input; specialized strict readers keep their parsers."""
    raw = read_input_bytes(path, field=field)
    try:
        return json.loads(raw.decode("utf-8-sig"))
    except (ValueError, UnicodeError, RecursionError) as exc:
        details = {"path": field, "repair_hint": "将输入保存为有效的UTF-8 JSON后重新读取。"}
        if isinstance(exc, json.JSONDecodeError):
            details.update(line=exc.lineno, column=exc.colno)
        raise PlanError("输入文件不是有效的UTF-8 JSON。", "input_json_invalid", details) from exc


def read_input_text(path, *, field="/input"):
    raw = read_input_bytes(path, field=field)
    try:
        # Match pathlib.read_text's universal newline behavior for task prose.
        return raw.decode("utf-8-sig").replace("\r\n", "\n").replace("\r", "\n")
    except UnicodeError as exc:
        raise PlanError("输入文本必须使用UTF-8编码。", "input_text_invalid", {"path": field}) from exc


def read_plan(path):
    path = Path(path).resolve()
    raw = read_input_bytes(path, field="/plan")
    try:
        plan = json.loads(raw.decode("utf-8-sig"))
    except (UnicodeError, ValueError, RecursionError) as exc:
        raise PlanError("无法读取原生方案 JSON") from exc
    validate_plan(plan)
    return {"plan": plan, "file_sha": hashlib.sha256(raw).hexdigest(), "path": str(path)}


def _validate_publication_roles(paths):
    """Every output, backup and lock must own a distinct physical file.

    In particular two hard-linked lock files otherwise self-deadlock before
    the publisher reaches any output. Recheck as part of the final commit.
    """
    roles = [(path, "output") for path in paths]
    roles += [(path.with_name(path.name + ".previous"), "backup") for path in paths]
    roles += [(path.with_name(f".{path.name}.plan.lock"), "lock") for path in paths]
    keys, identities = {}, {}
    for path, role in roles:
        resolved = path.resolve()
        key = os.path.normcase(str(resolved)).casefold()
        try:
            stat = resolved.stat()
            identity = (stat.st_dev, stat.st_ino) if stat.st_ino else None
        except (FileNotFoundError, NotADirectoryError):
            identity = None
        previous = keys.get(key) or (identities.get(identity) if identity is not None else None)
        if previous is not None:
            raise PlanError("发布文件、备份或锁指向同一文件", "file_conflict",
                            {"conflicting_roles": [previous, role], "published_paths": []})
        keys[key] = role
        if identity is not None:
            identities[identity] = role


def publish_files(outputs, *, overwrite=False, expected_shas=None, input_paths=()):
    """Prepare every byte before publishing; surface partial publication, keep last file backup.

    Cooperative processes share sorted locks. External editors that ignore those locks
    are detected at the last pre-replace SHA check, but cannot be made transactional.
    """
    from genshin_app.output_policy import validate_output_paths
    outputs = list(outputs)
    paths = [Path(path).resolve() for path, _ in outputs]
    cas_paths = tuple((expected_shas or {}).keys())
    try:
        validate_output_paths(paths, input_paths=input_paths, cas_paths=cas_paths)
        _validate_publication_roles(paths)
    except (OSError, PlanError) as exc:
        # No lock, temporary file or destination write has started. Later
        # preparation/commit failures keep their existing partial/unknown truth.
        cause = getattr(exc, "details", str(exc))
        details = dict(cause) if isinstance(cause, dict) else {"cause": cause}
        details.update(publication_state="not_written", published_paths=[], unknown_paths=[],
                       pending_paths=[str(path) for path in paths])
        raise PlanError(str(exc), getattr(exc, "reason", "file_conflict"), details) from exc
    expected = {os.path.normcase(str(Path(p).resolve())): sha
                for p, sha in (expected_shas or {}).items()}
    prepared, published = [], []
    replacing = None
    try:
        with contextlib.ExitStack() as stack:
            for path in sorted(paths, key=lambda p: os.path.normcase(str(p))):
                stack.enter_context(file_lock(path.with_name(f".{path.name}.plan.lock")))
            observed = {}
            for path in paths:
                sha = file_sha(path)
                key = os.path.normcase(str(path))
                observed[key] = sha
                if key in expected and sha != expected[key]:
                    raise PlanError("文件已被外部修改，请重新载入或另存", "file_conflict",
                                    {"path": str(path), "expected_file_sha": expected[key], "actual_file_sha": sha})
                if sha is not None and not overwrite and key not in expected:
                    raise PlanError("目标文件已存在，需要明确允许覆盖", "file_conflict", {"path": str(path)})
            try:
                for path, (_, content) in zip(paths, outputs, strict=True):
                    validate_output_paths([path], input_paths=input_paths, cas_paths=cas_paths)
                    path.parent.mkdir(parents=True, exist_ok=True)
                    fd, name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
                    temp_path = Path(name)
                    prepared.append((path, temp_path))
                    with os.fdopen(fd, "wb") as stream:
                        validate_output_paths([temp_path], input_paths=input_paths, include_side_effects=False)
                        stream.write(content)
                        stream.flush()
                        os.fsync(stream.fileno())
                for path, temp_path in prepared:
                    validate_output_paths([path], input_paths=input_paths, cas_paths=cas_paths)
                    _validate_publication_roles(paths)
                    current = file_sha(path)
                    if current != observed[os.path.normcase(str(path))]:
                        raise PlanError("发布前发现外部文件修改", "file_conflict", {"path": str(path)})
                    if current is not None:
                        backup = path.with_name(path.name + ".previous")
                        # Do not truncate/follow a backup symlink or hard link.
                        # Backups belong to this publisher, not a separate store.
                        backup_fd, backup_name = tempfile.mkstemp(prefix=f".{path.name}.backup.", suffix=".tmp", dir=path.parent)
                        try:
                            with os.fdopen(backup_fd, "wb") as stream:
                                stream.write(path.read_bytes())
                                stream.flush()
                                os.fsync(stream.fileno())
                            validate_output_paths([path], input_paths=input_paths, cas_paths=cas_paths)
                            _validate_publication_roles(paths)
                            os.replace(backup_name, backup)
                        finally:
                            Path(backup_name).unlink(missing_ok=True)
                    replacing = str(path)
                    os.replace(temp_path, path)
                    published.append(str(path))
                    replacing = None
            finally:
                for _, temp_path in prepared:
                    temp_path.unlink(missing_ok=True)
        observed_shas = {str(p): file_sha(p) for p in paths}
    except (OSError, PlanError) as exc:
        unknown = [replacing] if replacing is not None and replacing not in published else []
        pending = [str(path) for path in paths if str(path) not in published and str(path) not in unknown]
        raise PlanError(str(exc), getattr(exc, "reason", "file_conflict"),
                        {"published_paths": published, "pending_paths": pending, "unknown_paths": unknown,
                         "cause": getattr(exc, "details", str(exc))}) from exc
    return {"published_paths": published, "file_shas": observed_shas}


def save_plan(path, plan, expected_file_sha=None, *, overwrite=False):
    plan = native_input(plan)
    output = json.dumps(plan, ensure_ascii=False, indent=2, allow_nan=False).encode("utf-8")
    # A missing SHA means a new file, unless an explicit overwrite was requested.
    expected = {path: expected_file_sha} if expected_file_sha is not None or not overwrite else None
    report = publish_files([(path, output)], overwrite=overwrite, expected_shas=expected)
    actual = str(Path(path).resolve())
    return {"path": actual, "file_sha": report["file_shas"][actual], "plan": plan}
