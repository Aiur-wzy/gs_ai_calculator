"""Shared, non-executing game-input edits for CLI and native candidates.

Storage validity is deliberately weaker than execution validity. This module
never manufactures a calculated result, a database fact or a user approval.
"""
from __future__ import annotations

import copy
import hashlib
import json
import uuid
from pathlib import Path

from genshin_app.equipment.files import fingerprint, input_view
from genshin_app.equipment.schema import PlanError, _json_guard, validate_plan

CONTRACT = "game-input-edit-v1"
SYSTEM_ROOTS = frozenset({"kind", "plan_id", "plan_version", "revision", "board_id",
    "schema_version", "version_line", "resolution", "resolved_template"})
DERIVED_EXTENSIONS = frozenset({"event_topology_receipt", "event_topology_execution_binding_receipt",
    "agent_execution_materialization_receipt", "agent_execution_effective_input",
    "agent_execution_runtime_receipt", "provisional_usage_receipt", "static_inclusion_receipt",
    "result_receipt", "execution_receipt"})


def input_version_line(value):
    """Match the existing native compiler's omitted-meta release default."""
    if value.get("kind") == "genshin-whiteboard":
        return value.get("version_line")
    return (value.get("manual_template", {}).get("meta") or {}).get("version_line", "release")


def tokens(pointer):
    if not isinstance(pointer, str) or not pointer.startswith("/"):
        raise PlanError("输入位置必须是JSON Pointer", "input_edit_request")
    return [part.replace("~1", "/").replace("~0", "~") for part in pointer[1:].split("/")]


def at(value, path):
    for part in path:
        try:
            value = value[int(part)] if isinstance(value, list) else value[part]
        except (KeyError, IndexError, ValueError, TypeError) as exc:
            raise PlanError("输入位置不存在", "input_edit_reference", {"path": "/" + "/".join(path)}) from exc
    return value


def validate_editable_input(value):
    """Check storage/identity only; unfinished game fragments remain writable."""
    _json_guard(value)
    if not isinstance(value, dict):
        raise PlanError("输入必须为JSON对象", "input_edit_request")
    if value.get("kind") == "genshin-whiteboard":
        from genshin_app.whiteboard import validate_whiteboard
        # Whiteboard's existing schema already permits unknown panels/times.
        validate_whiteboard(value)
        return
    if value.get("kind") != "genshin-plan" or value.get("plan_version") not in (1, 2, 3):
        raise PlanError("不支持的原生输入格式", "input_edit_request")
    try:
        uuid.UUID(value["plan_id"])
    except (KeyError, ValueError, TypeError, AttributeError) as exc:
        raise PlanError("原生输入身份不合法", "input_edit_identity") from exc
    if type(value.get("revision")) is not int or value["revision"] < 0:
        raise PlanError("输入修订不合法", "input_edit_identity")
    template = value.get("manual_template")
    if not isinstance(template, dict) or "meta" in template and not isinstance(template["meta"], dict):
        raise PlanError("原生输入需要保留模板与版本身份", "input_edit_identity")
    if input_version_line(value) not in ("release", "beta"):
        raise PlanError("输入版本线不合法", "input_edit_identity")
    for name in ("rotation_model", "mechanics_model", "equipment_inputs", "extensions"):
        if name in value and value[name] is not None and not isinstance(value[name], dict):
            raise PlanError("输入容器必须是对象或待补的null", "input_edit_request", {"path": name})
    if value.get("extensions") is None and "extensions" in value:
        raise PlanError("扩展容器必须是对象", "input_edit_request", {"path": "extensions"})


def execution_issues(value):
    """Read-only strict diagnostics; never silently repairs an unfinished input."""
    try:
        if value.get("kind") == "genshin-whiteboard":
            validate_editable_input(value)
            return []
        validate_plan(value)
        from genshin_app.equipment.action_authoring_projection import (
            native_action_board,
        )
        return native_action_board(value, allow_incomplete=True).get("issues", [])
    except PlanError as exc:
        details = exc.details
        rows = details if isinstance(details, list) else [details] if isinstance(details, dict) else []
        return [dict(row) for row in rows] or [{"code": exc.reason, "message": str(exc), "path": "input"}]
    return []


def _protected(path):
    if not path or path[0] in SYSTEM_ROOTS:
        return True
    if path[:3] == ["manual_template", "meta", "version_line"]:
        return True
    if path[0] == "extensions" and len(path) > 1 and path[1] in DERIVED_EXTENSIONS:
        return True
    # Public fact declarations are editable; their App-issued proofs are not.
    if path[0] == "provisional_entities" and len(path) > 2:
        return path[2] in {"approval", "attestation", "database_lookup", "payload_fingerprint"}
    return False


def resolve_operation(value, operation):
    """Stable-ID selectors are exact list-local identities, not a query language."""
    item = copy.deepcopy(operation)
    selector = item.pop("selector", None)
    if selector is None:
        return item
    if not isinstance(selector, dict) or set(selector) - {"collection", "id", "id_field"}:
        raise PlanError("稳定ID定位只接受collection/id/id_field", "input_edit_request")
    collection = selector.get("collection")
    rows = at(value, tokens(collection))
    field, identity = selector.get("id_field", "id"), selector.get("id")
    if not isinstance(rows, list) or not isinstance(field, str) or not field or not isinstance(identity, str) or not identity:
        raise PlanError("稳定ID必须定位数组中的明确对象", "input_edit_reference")
    matches = [n for n, row in enumerate(rows) if isinstance(row, dict) and row.get(field) == identity]
    if len(matches) != 1:
        raise PlanError("稳定ID不存在或不唯一，未修改输入", "input_edit_reference",
                        {"collection": collection, "id": identity, "matches": len(matches)})
    suffix = item.get("path", "")
    if suffix and not suffix.startswith("/"):
        raise PlanError("相对字段位置必须为空或以/开头", "input_edit_request")
    item["path"] = collection + "/" + str(matches[0]) + suffix
    return item


def patch_input(original, operations):
    """Atomic pure edit; all entry points use the same protected field rules."""
    value = copy.deepcopy(original)
    if not isinstance(operations, list) or not operations:
        raise PlanError("请提供至少一项输入修改", "input_edit_request")
    for raw in operations:
        item = resolve_operation(value, raw)
        if item.get("op") not in {"add", "replace", "remove"}:
            raise PlanError("不支持的修改操作", "input_edit_request")
        path = tokens(item.get("path"))
        if _protected(path):
            raise PlanError("不能编辑系统身份、批准或计算产物", "agent_patch_forbidden", {"path": item.get("path")})
        if item["op"] != "remove" and "value" not in item:
            raise PlanError("添加或替换需要value", "input_edit_request")
        parent, key = at(value, path[:-1]), path[-1]
        if isinstance(parent, list):
            if key == "-" and item["op"] == "add":
                index = len(parent)
            elif key.isdecimal() and str(int(key)) == key:
                index = int(key)
            else:
                raise PlanError("数组位置不合法", "input_edit_reference")
            if index > len(parent) or item["op"] != "add" and index == len(parent):
                raise PlanError("数组位置不存在", "input_edit_reference")
            if item["op"] == "add":
                parent.insert(index, copy.deepcopy(item["value"]))
            elif item["op"] == "remove":
                parent.pop(index)
            else:
                parent[index] = copy.deepcopy(item["value"])
        elif isinstance(parent, dict):
            if item["op"] != "add" and key not in parent:
                raise PlanError("修改位置不存在", "input_edit_reference", {"path": item["path"]})
            if item["op"] == "remove":
                del parent[key]
            else:
                parent[key] = copy.deepcopy(item["value"])
        else:
            raise PlanError("修改父节点不是对象或数组", "input_edit_reference")
    validate_editable_input(value)
    if original.get("kind") == "genshin-plan" and input_version_line(value) != input_version_line(original):
        raise PlanError("不能通过替换父节点修改版本身份", "agent_patch_forbidden")
    old_extensions = original.get("extensions") or {}
    new_extensions = value.get("extensions") or {}
    if any(old_extensions.get(key) != new_extensions.get(key) for key in DERIVED_EXTENSIONS):
        raise PlanError("不能通过替换父节点写入计算或批准产物", "agent_patch_forbidden")
    old_entities = {row.get("provisional_id"): row for row in original.get("provisional_entities", []) if isinstance(row, dict)}
    for row in value.get("provisional_entities", []):
        if not isinstance(row, dict):
            continue
        old = old_entities.get(row.get("provisional_id"), {})
        if any(row.get(key) != old.get(key) for key in ("approval", "attestation", "database_lookup", "payload_fingerprint")):
            raise PlanError("公共事实证明必须由受信服务签发", "agent_patch_forbidden")
    return value


def prepare_replacement(original, incoming):
    """Import author input, never another document's cached result/authority."""
    value = copy.deepcopy(incoming)
    validate_editable_input(value)
    if value.get("kind") != original.get("kind"):
        raise PlanError("输入表示不同，请先准备对应的白板或原生输入", "input_edit_representation")
    if value["kind"] == "genshin-plan":
        if input_version_line(value) != input_version_line(original):
            raise PlanError("文件版本线与当前候选不同", "input_edit_identity")
        value["plan_id"], value["revision"] = original["plan_id"], original["revision"]
        value.update(resolution={"state": "pending"}, resolved_template=None)
        previous_handoff = original.get("extensions", {}).get("whiteboard_handoff")
        if isinstance(previous_handoff, dict) and isinstance(previous_handoff.get("board"), dict):
            # This is the current document's App-owned projection, not an
            # imported file's authority to select another formal whiteboard.
            value.setdefault("extensions", {})["whiteboard_handoff"] = copy.deepcopy(previous_handoff)
        for name in DERIVED_EXTENSIONS:
            value.get("extensions", {}).pop(name, None)
        for family in ("action_capacity_sources", "action_admission_sources"):
            for source in value.get(family, []) or []:
                if isinstance(source, dict) and "accepted_fingerprint" in source:
                    source["accepted_fingerprint"] = None
        boards = [value, value.get("extensions", {}).get("whiteboard_handoff", {}).get("board")]
        for board in boards:
            for entity in (board.get("provisional_entities", []) or []) if isinstance(board, dict) else []:
                if not isinstance(entity, dict):
                    continue
                for key in ("approval", "attestation", "database_lookup", "payload_fingerprint"):
                    if key in entity:
                        entity[key] = None
    else:
        for key in ("board_id", "revision", "schema_version", "version_line"):
            value[key] = original[key]
    validate_editable_input(value)
    return value


def read_input_snapshot(path, expected_file_sha=None, *, maximum_bytes=64 * 1024 * 1024):
    """One stable read; do not import partial bytes or silently accept file drift."""
    path = Path(path).resolve()
    from genshin_app.equipment.files import input_file_errors, read_input_bytes
    with input_file_errors():
        before = path.stat()
        if before.st_size > maximum_bytes:
            raise PlanError("输入文件超过允许字节数", "input_edit_size", {"actual": before.st_size, "maximum": maximum_bytes})
        raw = read_input_bytes(path, maximum_bytes=maximum_bytes)
        if len(raw) > maximum_bytes:
            raise PlanError("输入文件超过允许字节数", "input_edit_size", {"actual": len(raw), "maximum": maximum_bytes})
        after = path.stat()
    sha = hashlib.sha256(raw).hexdigest()
    if (before.st_size, before.st_mtime_ns, before.st_ino) != (after.st_size, after.st_mtime_ns, after.st_ino):
        raise PlanError("读取期间文件已改变，候选未修改", "file_conflict")
    if expected_file_sha is not None and sha != expected_file_sha:
        raise PlanError("输入文件已被修改，候选未修改", "file_conflict", {"expected": expected_file_sha, "actual": sha})
    return decode_input_snapshot(raw, path=str(path), file_sha=sha)


def decode_input_snapshot(raw, *, path, file_sha):
    """Parse exact captured bytes; neither a native import nor this parser opens paths."""
    try:
        def pairs(items):
            result = {}
            for key, item in items:
                if key in result:
                    raise ValueError("duplicate JSON key")
                result[key] = item
            return result
        value = json.loads(raw.decode("utf-8-sig"), object_pairs_hook=pairs,
                           parse_constant=lambda _: (_ for _ in ()).throw(ValueError("nonfinite")))
        _json_guard(value)
    except (ValueError, UnicodeError, RecursionError) as exc:
        raise PlanError("输入文件尚不是完整JSON，候选未修改", "input_edit_request") from exc
    return {"input": value, "path": path, "file_sha": file_sha, "bytes": len(raw)}


def edit_contract():
    return {"contract": CONTRACT, "modes": ["patch", "replace", "import"],
        "identity": "current candidate revision and source file SHA are independent conflict checks",
        "drafts": "unfinished references are saveable; calculation still validates real executable input",
        "selector": {"collection": "/rotation_model/actions", "id": "actual-action-id", "id_field": "id"},
        "authority": "input edits never produce a calculation or a user approval"}


def prepare_input_edit(original, request, *, source_receipt=None):
    """Common CLI/native pure preparation; the caller commits with revision CAS."""
    mode = request.get("mode", "patch")
    if mode not in {"patch", "replace", "import"}:
        raise PlanError("输入编辑模式不受支持", "input_edit_request")
    expected = request.get("expected_input_fingerprint")
    if expected is not None and expected != fingerprint(input_view(original)):
        raise PlanError("当前输入已变化，候选未修改", "agent_stale_revision")
    if mode == "patch":
        candidate = patch_input(original, request.get("operations"))
    else:
        if mode == "import":
            from genshin_app.native_input_snapshot import native_input_snapshot
            captured = native_input_snapshot(request)
            source = (read_input_snapshot(request["file_path"], request.get("expected_file_sha")) if captured is None else
                      decode_input_snapshot(captured["raw"], path=captured["path"], file_sha=captured["file_sha"]))
        else:
            source = {"input": request.get("input")}
        if mode == "import" and source_receipt is not None:
            source_receipt.update({key: source[key] for key in ("path", "file_sha", "bytes")})
        if original.get("kind") == "genshin-whiteboard" and isinstance(source["input"], dict) and source["input"].get("kind") == "genshin-plan":
            if input_version_line(original) != input_version_line(source["input"]):
                raise PlanError("原生文件版本线与当前白板不同，候选未修改", "input_edit_identity")
            candidate = copy.deepcopy(source["input"])
            candidate.update(plan_id=str(uuid.uuid4()), revision=original["revision"],
                             resolution={"state": "pending"}, resolved_template=None)
            candidate.setdefault("extensions", {})["whiteboard_handoff"] = {
                "schema_version": original["schema_version"], "board": copy.deepcopy(original),
                "action_map": [], "auxiliary_action_ids": [], "completion_notes": [], "action_admission_proposals": []}
            candidate = prepare_replacement(candidate, candidate)
        else:
            candidate = prepare_replacement(original, source["input"])
    synchronize_database_refs(original, candidate)
    if input_view(candidate) != input_view(original) and candidate.get("kind") == "genshin-plan":
        from genshin_app.native_input_projection import synchronize_native_projection
        synchronize_native_projection(candidate, original)
    return candidate


def edit_candidate(session, request):
    """Session is the existing writer/authority port, not a GUI-specific flow."""
    session._expect(request["expected_revision"])
    source = {}
    candidate = prepare_input_edit(session.candidate, request, source_receipt=source)
    if request.get("preview"):
        changed = sorted(key for key in candidate.keys() | session.candidate.keys()
                         if candidate.get(key) != session.candidate.get(key))
        return {"revision": session.revision, "edit_status": "preview", "changed_roots": changed,
                "input_fingerprint": fingerprint(input_view(session.candidate)), "writes": False}
    previous_calculation = session.calculated is not None
    result = session.replace_candidate(candidate, request["expected_revision"], request["reason"])
    return {**result, "source_file": source or None,
            "calculation_invalidated": previous_calculation and session.calculated is None}


def synchronize_database_refs(previous, candidate):
    """Keep legacy database IDs and D2 database refs one atomic edit.

    The public patch tool historically exposed the numeric catalog fields. D2
    added discriminated entity refs without removing those fields.  A caller
    that changes only the historical database ID must not leave the candidate
    internally split across two identities.  We update an unchanged database
    ref in the same in-memory transaction; an explicitly edited ref is never
    overwritten and the normal schema validator will reject any mismatch.
    """

    def sync_ref(old_owner, new_owner, *, id_key, ref_key, entity_type, line):
        if not isinstance(old_owner, dict) or not isinstance(new_owner, dict):
            return
        old_id, new_id = old_owner.get(id_key), new_owner.get(id_key)
        if old_id == new_id or type(new_id) is not int or new_id < 1:
            return
        old_ref, new_ref = old_owner.get(ref_key), new_owner.get(ref_key)
        if new_ref != old_ref:
            return
        if old_ref is not None and (
            not isinstance(old_ref, dict) or old_ref.get("kind") != "database"
        ):
            return
        new_owner[ref_key] = {
            "kind": "database",
            "entity_type": entity_type,
            "database_id": new_id,
            "version_line": line,
        }

    def sync_board(old_board, new_board):
        if not isinstance(old_board, dict) or not isinstance(new_board, dict):
            return
        line = new_board.get("version_line")
        old_actors = {
            row.get("id"): row for row in old_board.get("team", [])
            if isinstance(row, dict) and isinstance(row.get("id"), str)
        }
        for actor in new_board.get("team", []):
            if not isinstance(actor, dict):
                continue
            old_actor = old_actors.get(actor.get("id"))
            sync_ref(
                old_actor, actor, id_key="db_character_id",
                ref_key="character_ref", entity_type="character", line=line,
            )
            old_weapon = old_actor.get("weapon") if isinstance(old_actor, dict) else None
            new_weapon = actor.get("weapon")
            sync_ref(
                old_weapon, new_weapon, id_key="weapon_id", ref_key="entity_ref",
                entity_type="weapon", line=line,
            )
            old_sets = old_actor.get("artifact_sets", []) if isinstance(old_actor, dict) else []
            new_sets = actor.get("artifact_sets", [])
            for index, selected in enumerate(new_sets if isinstance(new_sets, list) else []):
                old_selected = old_sets[index] if index < len(old_sets) else None
                sync_ref(
                    old_selected, selected, id_key="set_id", ref_key="entity_ref",
                    entity_type="artifact_set", line=line,
                )

    def sync_native(old_plan, new_plan):
        if not isinstance(old_plan, dict) or not isinstance(new_plan, dict):
            return
        line = new_plan.get("manual_template", {}).get("meta", {}).get(
            "version_line", new_plan.get("version_line")
        )
        old_inputs = old_plan.get("equipment_inputs", {})
        new_inputs = new_plan.get("equipment_inputs", {})
        if isinstance(old_inputs, dict) and isinstance(new_inputs, dict):
            for actor_id, equipment in new_inputs.items():
                old_equipment = old_inputs.get(actor_id)
                if not isinstance(old_equipment, dict) or not isinstance(equipment, dict):
                    continue
                old_legacy = old_equipment.get("character_ref")
                new_legacy = equipment.get("character_ref")
                if isinstance(old_legacy, dict) and isinstance(new_legacy, dict):
                    proxy_old = {
                        "database_id": old_legacy.get("db_character_id"),
                        "entity_ref": old_equipment.get("character_entity_ref"),
                    }
                    proxy_new = {
                        "database_id": new_legacy.get("db_character_id"),
                        "entity_ref": equipment.get("character_entity_ref"),
                    }
                    sync_ref(
                        proxy_old, proxy_new, id_key="database_id",
                        ref_key="entity_ref", entity_type="character", line=line,
                    )
                    equipment["character_entity_ref"] = proxy_new["entity_ref"]
                sync_ref(
                    old_equipment.get("weapon"), equipment.get("weapon"),
                    id_key="weapon_id", ref_key="entity_ref",
                    entity_type="weapon", line=line,
                )
                old_sets = old_equipment.get("artifact_sets", [])
                new_sets = equipment.get("artifact_sets", [])
                for index, selected in enumerate(
                    new_sets if isinstance(new_sets, list) else []
                ):
                    old_selected = old_sets[index] if index < len(old_sets) else None
                    sync_ref(
                        old_selected, selected, id_key="set_id",
                        ref_key="entity_ref", entity_type="artifact_set", line=line,
                    )
        old_handoff = old_plan.get("extensions", {}).get("whiteboard_handoff", {}).get("board")
        new_handoff = new_plan.get("extensions", {}).get("whiteboard_handoff", {}).get("board")
        sync_board(old_handoff, new_handoff)

    if candidate.get("kind") == "genshin-whiteboard":
        sync_board(previous, candidate)
    else:
        sync_native(previous, candidate)

