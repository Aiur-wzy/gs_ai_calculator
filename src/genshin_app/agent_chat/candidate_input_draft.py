"""Portable *input* snapshots, never Session backups or execution authority."""
from __future__ import annotations

import copy
import json
import math
import re
import uuid
from datetime import UTC, datetime

from genshin_app.equipment.files import fingerprint, input_view, json_bytes
from genshin_app.equipment.schema import PlanError, _json_guard
from genshin_app.whiteboard_schema import validate_whiteboard

KIND = "genshin-candidate-input-draft"
SUFFIX = ".gencdraft.json"
MAX_BYTES = 128 * 1024 * 1024
_HASH = re.compile(r"[0-9a-f]{64}\Z")
_RESOURCE = re.compile(r"(?:query-[1-9][0-9]{0,15}|note-[0-9a-f-]{36})\Z")
_FOCUS = {"loadout_draft", "action_checkpoint", "event_topology", "numeric_model", "calculation_closure"}
# These are control objects, not user-created effect names or generic 'status'.
_FORBIDDEN = {"writer_fence", "writer_takeover", "ui_receipt", "api_key", "authorization",
              "raw_reasoning", "reasoning_content", "dsh_session_id", "provisional_authority",
              "access_token", "delivery_capability", "session_ref"}
_DERIVED_EXTENSIONS = {"agent_execution_materialization_receipt", "provisional_usage_receipt",
                       "static_inclusion_receipt", "result_receipt", "execution_receipt"}


def _fail(message, path="", reason="candidate_input_draft_invalid"):
    raise PlanError(message, reason, {"path": path})


def _closed(value, fields, path):
    if type(value) is not dict or set(value) != set(fields):
        _fail("候选输入草稿字段缺失或含未知字段", path)


def _digest(value, path):
    if type(value) is not str or _HASH.fullmatch(value) is None:
        _fail("输入指纹不合法", path)


def _resource_name(value):
    if type(value) is not str or not _RESOURCE.fullmatch(value):
        return False
    if value.startswith("query-"):
        return int(value[6:]) <= 2**53-1
    try:
        uuid.UUID(value[5:])
    except ValueError:
        return False
    return True


def _control_guard(value, path=""):
    if isinstance(value, dict):
        for key, item in value.items():
            here = path + "/" + key.replace("~", "~0").replace("/", "~1")
            if key.lower() in _FORBIDDEN:
                _fail("输入资料混入会话、凭据或控制权限，未导出", here)
            _control_guard(item, here)
    elif isinstance(value, list):
        for index, item in enumerate(value):
            _control_guard(item, path + "/" + str(index))


def portable_document(document):
    """Explicit format-owned projection: keep domain declarations, remove proofs."""
    candidate_input = input_view(document) if document.get("kind") == "genshin-plan" else document
    _json_guard(candidate_input)
    value = copy.deepcopy(candidate_input)
    representation = "whiteboard" if value.get("kind") == "genshin-whiteboard" else "native"
    if representation == "native":
        value = input_view(value)
        extensions = value.get("extensions", {})
        for name in _DERIVED_EXTENSIONS:
            extensions.pop(name, None)
        for source in value.get("action_capacity_sources", []):
            if "accepted_fingerprint" in source:
                source["accepted_fingerprint"] = None
        for source in value.get("action_admission_sources", []):
            if "accepted_fingerprint" in source:
                source["accepted_fingerprint"] = None
    # Provisional values and justification remain; trusted lookup/approval does not.
    for entity in value.get("provisional_entities", []):
        for key in ("database_lookup", "attestation", "approval", "payload_fingerprint"):
            if key in entity:
                entity[key] = None
    board = value.get("extensions", {}).get("whiteboard_handoff", {}).get("board")
    if isinstance(board, dict):
        for entity in board.get("provisional_entities", []):
            for key in ("database_lookup", "attestation", "approval", "payload_fingerprint"):
                if key in entity:
                    entity[key] = None
    _control_guard(value)
    _validate_document(representation, value)
    return {"representation": representation, "payload": value}


def _validate_document(representation, payload):
    if representation == "whiteboard":
        validate_whiteboard(payload)
    elif representation == "native":
        if any(key in payload for key in ("resolution", "resolved_template")):
            _fail("输入草稿不能携带计算结果", "/input")
        from genshin_app.input_editing import validate_editable_input
        validate_editable_input({**copy.deepcopy(payload), "resolution": {"state": "pending"}, "resolved_template": None})
    else:
        _fail("未知候选输入表示", "/input")


def _hashes(inputs, evidence):
    return {"source_document_input": fingerprint(inputs["source_document"]),
            "candidate_input": fingerprint(inputs["candidate"]), "evidence": fingerprint(evidence),
            "input_snapshot": fingerprint({"input": inputs, "evidence": evidence})}


def build_draft(session, *, workspace_identity, database_release_id, producer):
    """Caller holds candidate lock and has verified current compatibility."""
    state = getattr(session, "stage_state", {})
    inputs = {"source_document": portable_document(session.document),
              "candidate": portable_document(session.candidate),
              "work_focus": state.get("phase", "loadout_draft"),
              "energy_intent": state.get("energy_intent")}
    resources = []
    from .candidate_input_draft_store import historical_provenance
    historical = historical_provenance(session)
    descriptors = {**historical["descriptors"], **state.get("resource_descriptors", {})}
    for name, data in session.resources.items():
        if not _resource_name(name):
            continue  # Generated review/calculation projections are not knowledge.
        _json_guard(data, "resource")
        _control_guard(data, "/evidence/resources/" + name)
        descriptor = copy.deepcopy(descriptors.get(name))
        if descriptor is None:
            descriptor = {"kind": "agent_note", "schema_version": 1, "title": name,
                          "source_refs": [], "used_at": []}
        resources.append({"resource_id": name, "descriptor": descriptor,
                          "data": copy.deepcopy(data), "fingerprint": fingerprint(data)})
    focuses = state.get("finding_stages", [])
    evidence = {"resources": resources,
                "findings": [{"record": copy.deepcopy(record), "work_focus":
                    focuses[index] if index < len(focuses) else inputs["work_focus"]}
                    for index, record in enumerate(session.findings)],
                "unresolved_references": copy.deepcopy(historical["unresolved_references"])}
    _control_guard(evidence)
    # Preserve unresolved provenance rather than pretending its body was saved.
    names = {row["resource_id"] for row in resources}
    def scan(value, path):
        if isinstance(value, dict):
            for key, item in value.items():
                pointer = path + "/" + key.replace("~", "~0").replace("/", "~1")
                if key == "reference" and type(item) is str and item.startswith("query-") and item not in names:
                    pending = {"owner_pointer": pointer, "reference": item, "reason": "not_registered"}
                    if pending not in evidence["unresolved_references"]:
                        evidence["unresolved_references"].append(pending)
                scan(item, pointer)
        elif isinstance(value, list):
            for index, item in enumerate(value):
                scan(item, path + "/" + str(index))
    scan(inputs, "/input")
    value = {"kind": KIND, "schema_version": 1, "artifact_id": str(uuid.uuid4()),
        "created_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"), "producer": producer,
        "compatibility": {"workspace_identity": copy.deepcopy(workspace_identity),
            "database_release_id": database_release_id, "database_fingerprint": fingerprint(session.database_snapshot),
            "whiteboard_schema_version": session.document.get("schema_version") if session.kind == "whiteboard" else None,
            "native_plan_version": session.candidate.get("plan_version"), "evidence_schema_version": 1},
        "source": {"document_kind": session.kind,
            "document_id": session.document.get("board_id", session.document.get("plan_id")),
            "candidate_revision": session.revision, "stage_revision": state.get("stage_revision", 0),
            "candidate_fingerprint": fingerprint(session.candidate)},
        "input": inputs, "evidence": evidence, "fingerprints": _hashes(inputs, evidence)}
    validate_draft(value)
    return value


def validate_draft(value):
    # The envelope adds nesting. Guard each domain payload at its *own* root,
    # so an input legal at the existing 100-level limit remains legal here.
    try:
        raw = json_bytes(value)
    except (ValueError, TypeError, RecursionError, OverflowError) as exc:
        raise PlanError("候选输入草稿不是有限 JSON", "candidate_input_draft_invalid") from exc
    if len(raw) > MAX_BYTES:
        _fail("候选输入草稿超过容量限制", reason="candidate_input_draft_too_large")
    _closed(value, ("kind", "schema_version", "artifact_id", "created_at", "producer", "compatibility",
                    "source", "input", "evidence", "fingerprints"), "")
    if value["kind"] != KIND or type(value["schema_version"]) is not int or value["schema_version"] != 1:
        _fail("不支持的候选输入草稿版本", reason="candidate_input_draft_version_unsupported")
    try:
        uuid.UUID(value["artifact_id"])
        stamp = datetime.fromisoformat(value["created_at"])
        if stamp.utcoffset() != UTC.utcoffset(stamp):
            raise ValueError()
    except (ValueError, TypeError, AttributeError) as exc:
        raise PlanError("候选输入草稿身份或时间不合法", "candidate_input_draft_invalid") from exc
    _closed(value["producer"], ("app_version", "app_release_id"), "/producer")
    if any(type(item) is not str or not item for item in value["producer"].values()):
        _fail("生产版本不合法", "/producer")
    compatibility = value["compatibility"]
    _closed(compatibility, ("workspace_identity", "database_release_id", "database_fingerprint",
        "whiteboard_schema_version", "native_plan_version", "evidence_schema_version"), "/compatibility")
    from .native_dsh_workspace_compatibility import _valid
    if (not _valid(compatibility["workspace_identity"]) or type(compatibility["evidence_schema_version"]) is not int
            or compatibility["evidence_schema_version"] != 1
            or type(compatibility["database_release_id"]) is not str or not compatibility["database_release_id"]):
        _fail("草稿版本来源不合法", "/compatibility")
    _digest(compatibility["database_fingerprint"], "/compatibility/database_fingerprint")
    source = value["source"]
    _closed(source, ("document_kind", "document_id", "candidate_revision", "stage_revision",
                    "candidate_fingerprint"), "/source")
    for key in ("candidate_revision", "stage_revision"):
        if type(source[key]) is not int or not 0 <= source[key] <= 2**53-1:
            _fail("来源修订不合法", "/source/" + key)
    _digest(source["candidate_fingerprint"], "/source/candidate_fingerprint")
    inputs = value["input"]
    _closed(inputs, ("source_document", "candidate", "work_focus", "energy_intent"), "/input")
    for name in ("source_document", "candidate"):
        _closed(inputs[name], ("representation", "payload"), "/input/" + name)
        _validate_document(inputs[name]["representation"], inputs[name]["payload"])
        if portable_document(inputs[name]["payload"]) != inputs[name]:
            _fail("输入草稿携带不可移植的结果或权限", "/input/" + name)
    original = inputs["source_document"]
    original_id = original["payload"].get("board_id", original["payload"].get("plan_id"))
    if source["document_kind"] != original["representation"] or source["document_id"] != original_id:
        _fail("来源文档身份与输入不一致", "/source")
    current = inputs["candidate"]
    if (compatibility["whiteboard_schema_version"] != (original["payload"].get("schema_version")
            if original["representation"] == "whiteboard" else None)
            or compatibility["native_plan_version"] != current["payload"].get("plan_version")):
        _fail("输入结构版本与声明不一致", "/compatibility")
    if original["representation"] == "whiteboard":
        current_board = (current["payload"] if current["representation"] == "whiteboard" else
                         current["payload"].get("extensions", {}).get("whiteboard_handoff", {}).get("board"))
        validate_whiteboard(current_board)
        if any(current_board[key] != original["payload"][key] for key in ("kind", "schema_version", "board_id", "version_line")):
            _fail("原始意图与候选的文档身份不一致", "/input/candidate")
    else:
        if current["representation"] != "native" or any(current["payload"][key] != original["payload"][key]
                for key in ("kind", "plan_id", "plan_version")):
            _fail("原生文档与候选身份不一致", "/input/candidate")
    from .calculation_qualification import ENERGY_INTENTS
    if (type(inputs["work_focus"]) is not str or inputs["work_focus"] not in _FOCUS
            or (inputs["energy_intent"] is not None and
                (type(inputs["energy_intent"]) is not str or inputs["energy_intent"] not in ENERGY_INTENTS))):
        _fail("输入草稿工作重点或能量范围不合法", "/input")
    evidence = value["evidence"]
    _closed(evidence, ("resources", "findings", "unresolved_references"), "/evidence")
    if any(type(evidence[key]) is not list for key in evidence):
        _fail("领域证据必须为数组", "/evidence")
    names = set()
    total = 0
    for row in evidence["resources"]:
        _closed(row, ("resource_id", "descriptor", "data", "fingerprint"), "/evidence/resources")
        name = row["resource_id"]
        if not _resource_name(name) or name in names:
            _fail("领域资源逻辑名不合法或重复", "/evidence/resources")
        names.add(name)
        _json_guard(row["data"], "resource")
        _json_guard(row["descriptor"], "descriptor")
        size = len(json_bytes(row["data"]))
        total += size
        if size > 4*1024*1024 or row["fingerprint"] != fingerprint(row["data"]):
            _fail("领域资料大小或指纹不一致", "/evidence/resources/" + name)
        from .staged_candidate import _valid_resource_descriptor
        descriptor = row["descriptor"]
        if isinstance(descriptor, dict) and descriptor.get("kind") == "agent_note":
            _closed(descriptor, ("kind", "schema_version", "title", "source_refs", "used_at"), "/evidence/descriptor")
            if descriptor["schema_version"] != 1 or type(descriptor["title"]) is not str or not descriptor["title"] or len(descriptor["title"]) > 4096:
                _fail("领域资料说明不合法", "/evidence/descriptor")
            if type(descriptor["source_refs"]) is not list or type(descriptor["used_at"]) is not list:
                _fail("领域资料说明引用不合法", "/evidence/descriptor")
            if any(type(pointer) is not str or pointer and not pointer.startswith("/") for pointer in descriptor["used_at"]):
                _fail("领域资料说明只能引用文件内字段", "/evidence/descriptor/used_at")
        elif (not _valid_resource_descriptor(name, descriptor) or descriptor["fingerprint"] != row["fingerprint"]
              or descriptor["bytes"] != size):
            _fail("领域查询描述与正文不一致", "/evidence/descriptor")
    if len(names) > 128 or total > 64*1024*1024:
        _fail("领域资料超过当前资源容量", "/evidence/resources")
    for row in evidence["findings"]:
        _closed(row, ("record", "work_focus"), "/evidence/findings")
        if type(row["record"]) is not dict or type(row["work_focus"]) is not str or row["work_focus"] not in _FOCUS:
            _fail("领域记录不合法", "/evidence/findings")
    from genshin_app.workflow import validate_audit
    validate_audit({"schema_version": 1, "plan_id": source["document_id"],
                    "input_fingerprint": fingerprint(inputs["candidate"]),
                    "records": [row["record"] for row in evidence["findings"]]})
    if len(evidence["findings"]) > 500 or len(json_bytes([row["record"] for row in evidence["findings"]])) > 256*1024:
        _fail("领域记录超过当前容量", "/evidence/findings")
    for row in evidence["unresolved_references"]:
        _closed(row, ("owner_pointer", "reference", "reason"), "/evidence/unresolved_references")
        if type(row["owner_pointer"]) is not str or not row["owner_pointer"].startswith("/"):
            _fail("待核查引用位置不合法", "/evidence/unresolved_references")
        if type(row["reason"]) is not str or row["reason"] not in {"not_registered", "nonportable_control_reference", "body_unavailable", "historical_claim_only"}:
            _fail("待核查引用原因不合法", "/evidence/unresolved_references")
        if row["reference"] is not None and not _resource_name(row["reference"]):
            _fail("待核查引用不能包含权限或路径", "/evidence/unresolved_references")
    _control_guard(inputs)
    _control_guard(evidence)
    if value["fingerprints"] != _hashes(inputs, evidence):
        _fail("候选输入草稿完整性校验失败", "/fingerprints")
    return value


def _decode_draft_json(raw):
    """Bound and parse JSON syntax; this does not interpret a format's payload."""
    if len(raw) > MAX_BYTES:
        _fail("候选输入草稿超过容量限制", reason="candidate_input_draft_too_large")
    def closed_pairs(pairs):
        output = {}
        for key, item in pairs:
            if key in output:
                _fail("候选输入草稿含重复字段")
            output[key] = item
        return output
    def finite_float(text):
        value = float(text)
        if not math.isfinite(value):
            _fail("输入必须为有限 JSON")
        return value
    try:
        value = json.loads(raw.decode("utf-8-sig"), object_pairs_hook=closed_pairs,
                           parse_float=finite_float,
                           parse_constant=lambda _: _fail("输入必须为有限 JSON"))
    except PlanError:
        raise
    except (UnicodeError, ValueError, RecursionError) as exc:
        raise PlanError("无法读取候选输入草稿", "candidate_input_draft_invalid") from exc
    return value


def decode_draft(raw):
    return validate_draft(_decode_draft_json(raw))


def _safe_metadata_text(value, maximum):
    if (type(value) is not str or not 0 < len(value) <= maximum
            or any(ord(char) < 32 or ord(char) == 127 for char in value)):
        return False
    try:
        value.encode("utf-8")
    except UnicodeError:
        return False
    return True


def decode_draft_for_preview(raw):
    """Unknown future versions expose only inert, whitelisted header metadata.

    The bounded JSON document is syntax-decoded, including duplicate/finite
    checks. Its unknown business payload is never validated, projected, stored
    for browsing, or interpreted as V1 input/authority.
    """
    value = _decode_draft_json(raw)
    if (type(value) is not dict or value.get("kind") != KIND
            or type(value.get("schema_version")) is not int
            or not 2 <= value["schema_version"] <= 2**53-1):
        return {"draft": validate_draft(value)}
    metadata = {"kind": KIND, "schema_version": value["schema_version"]}
    for key in ("artifact_id", "created_at"):
        item = value.get(key)
        if _safe_metadata_text(item, 64):
            metadata[key] = item
    producer = value.get("producer")
    if type(producer) is dict:
        metadata["producer"] = {key: producer[key] for key in ("app_version", "app_release_id")
            if _safe_metadata_text(producer.get(key), 256)}
    return {"metadata": metadata}


def intent_alignment_issues(payload):
    """Objective input consistency, not character-specific mechanism judgement."""
    handoff = payload.get("extensions", {}).get("whiteboard_handoff")
    if payload.get("kind") != "genshin-plan" or not isinstance(handoff, dict):
        return []
    from genshin_app.whiteboard_result_binding import validate_result_intent

    try:
        validate_result_intent(handoff.get("board"), {**copy.deepcopy(payload),
            "resolution": {"state": "pending"}, "resolved_template": None})
    except PlanError as error:
        details = error.details if isinstance(error.details, dict) else {}
        return [{"code": "intent_alignment_pending", "path": details.get("path", "extensions.whiteboard_handoff"),
                 "message": "当前白板意图与原生输入尚未对齐；两份输入均已保留，请核对后修复。", "cause": error.reason}]
    return []


def preview_draft(value, *, workspace_identity, database_fingerprint):
    validate_draft(value)
    compatible = (value["compatibility"]["workspace_identity"] == workspace_identity
                  and value["compatibility"]["database_fingerprint"] == database_fingerprint)
    candidate = value["input"]["candidate"]
    payload = candidate["payload"]
    actors = payload.get("team", payload.get("manual_template", {}).get("characters", []))
    actions = payload.get("actions", (payload.get("rotation_model") or {}).get("actions", []))
    return {"artifact_id": value["artifact_id"], "compatible": compatible, "read_only": not compatible,
            "representation": candidate["representation"], "characters": len(actors), "actions": len(actions),
            "source_revision": value["source"]["candidate_revision"], "has_calculation": False,
            "accepted": False, "can_apply": False, "pending_verification": True,
            "input_snapshot_fingerprint": value["fingerprints"]["input_snapshot"],
            "input_issues": intent_alignment_issues(payload),
            "message": "输入草稿已打开，尚未开始 AI 任务。" if compatible else "此草稿来自其他版本，仅供查看；请让 Agent 按当前资料重新整理。"}
