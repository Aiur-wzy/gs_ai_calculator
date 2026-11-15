"""Session-owned D2 authority ledgers for plan-local public-data fallbacks.

This module is the App trust boundary.  Model-authored JSON can propose a
fallback, but cannot mint a database lookup, Agent attestation, user approval,
or formal usage receipt.  Missing upstream data and corrections to existing
facts deliberately follow different paths.
"""
from __future__ import annotations

import copy
import sqlite3
import uuid
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from typing import Any

from genshin_app.equipment.files import fingerprint, json_bytes
from genshin_app.equipment.provisional_entities import (
    APPROVAL_CONTRACT,
    ATTESTATION_CONTRACT,
    FIELD,
    LOOKUP_RECEIPT_CONTRACT,
    approval_request,
    attestation_request,
    authorization_basis_fingerprint,
    build_public_data_diff,
    build_usage_receipt,
    entity_payload_fingerprint,
    validate_entity,
)
from genshin_app.equipment.schema import PlanError
from genshin_db import queries

STATE_CONTRACT = "provisional-public-entity-authority-state-v1"
QUESTION_PREFIX = "d2-public-patch-"
MAX_LEDGER_ROWS = 256
MAX_STATE_BYTES = 512 * 1024
MAX_HOST_QUESTION_DETAIL_CHARS = 4096
_APPROVE_LABEL = "同意此资料修正"
_REJECT_LABEL = "不采用此资料修正"
_STATE_KEYS = {
    "contract", "session_id", "database_snapshot_fingerprint",
    "authorization_basis_fingerprint",
    "lookup_ledger", "attestation_ledger", "approval_ledger",
    "pending_approvals", "trusted_usage_receipt",
}


def _stamp() -> str:
    return datetime.now(UTC).isoformat()


def _error(message: str, reason: str, details: Any = None) -> None:
    raise PlanError(message, reason, details)


def new_state(
    session_id: str, database_snapshot_fingerprint: str,
    candidate: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "contract": STATE_CONTRACT,
        "session_id": session_id,
        "database_snapshot_fingerprint": database_snapshot_fingerprint,
        "authorization_basis_fingerprint": (
            authorization_basis_fingerprint(candidate)
            if isinstance(candidate, Mapping) else None
        ),
        "lookup_ledger": {},
        "attestation_ledger": {},
        "approval_ledger": {},
        "pending_approvals": {},
        "trusted_usage_receipt": None,
    }


def validate_state(
    value: Any, *, session_id: str, database_snapshot_fingerprint: str,
    candidate: Mapping[str, Any],
) -> dict[str, Any]:
    if (not isinstance(value, dict) or set(value) != _STATE_KEYS
            or value.get("contract") != STATE_CONTRACT
            or value.get("session_id") != session_id
            or value.get("database_snapshot_fingerprint")
            != database_snapshot_fingerprint):
        _error("临时公共资料权限账与当前会话或数据库不一致",
               "agent_checkpoint_invalid")
    if len(json_bytes(value)) > MAX_STATE_BYTES:
        _error("临时公共资料权限账超过大小限制", "agent_checkpoint_invalid")
    for field in ("lookup_ledger", "attestation_ledger", "approval_ledger",
                  "pending_approvals"):
        rows = value[field]
        if (not isinstance(rows, dict) or len(rows) > MAX_LEDGER_ROWS
                or any(not isinstance(key, str) or not isinstance(row, dict)
                       for key, row in rows.items())):
            _error("临时公共资料权限账结构不合法", "agent_checkpoint_invalid")
    if (value["trusted_usage_receipt"] is not None
            and not isinstance(value["trusted_usage_receipt"], dict)):
        _error("临时公共资料使用回执不合法", "agent_checkpoint_invalid")
    if value["authorization_basis_fingerprint"] != authorization_basis_fingerprint(
            candidate):
        _error("临时公共资料权限账与当前候选内容不一致",
               "agent_checkpoint_invalid")
    return copy.deepcopy(value)


def _state(session: Any) -> dict[str, Any]:
    value = getattr(session, "provisional_authority", None)
    if not isinstance(value, dict):
        _error("当前会话没有临时公共资料权限账", "agent_checkpoint_invalid")
    return value


def _save_state_transaction(session: Any, mutate: Any) -> None:
    """Persist one authority-only mutation or restore the exact prior state."""

    previous = copy.deepcopy(_state(session))
    try:
        mutate(_state(session))
        session.save()
    except Exception:
        session.provisional_authority = previous
        raise


def _stage_revision(session: Any) -> int:
    stage = getattr(session, "stage_state", None)
    return stage.get("stage_revision", 0) if isinstance(stage, dict) else 0


def _require_native_edit_stage(session: Any) -> None:
    # Reading focus is not execution permission in the artifact workflow.
    # A real native representation, rather than an advisory phase, owns D2.
    if (getattr(session, "workflow", None) == "artifact"
            and not session._is_board()):
        return
    if getattr(session, "workflow", None) != "staged" or getattr(
            session, "workflow_stage", None) not in {"event_topology", "numeric_model"}:
        _error("临时公共资料只能在原生事件或数值阶段核对；请先完成白板交接",
               "agent_stage_tool_forbidden")
    if getattr(session, "_is_board", lambda: True)():
        _error("当前仍是白板表示，尚不能写入原生临时公共资料",
               "agent_stage_tool_forbidden")


def _find_entity(plan: Mapping[str, Any], provisional_id: str) -> tuple[int, dict[str, Any]]:
    rows = plan.get(FIELD)
    if not isinstance(rows, list):
        _error("当前候选没有临时公共资料提案", "provisional_entity_not_found")
    matches = [(index, row) for index, row in enumerate(rows)
               if isinstance(row, dict) and row.get("provisional_id") == provisional_id]
    if len(matches) != 1:
        _error("当前候选没有唯一的临时公共资料提案",
               "provisional_entity_not_found")
    return matches[0][0], copy.deepcopy(matches[0][1])


def _lookup_matches_entity(entity: Mapping[str, Any], lookup: Mapping[str, Any]) -> bool:
    query = lookup.get("query")
    if not isinstance(query, Mapping):
        return False
    if (query.get("kind") != entity.get("entity_type")
            or query.get("version_line")
            != entity.get("version_claim", {}).get("version_line")):
        return False
    if "exact_name" in query:
        return query.get("exact_name") == entity.get("display_name")
    return type(query.get("database_id")) is int and query["database_id"] > 0


def _detail(db_path: Any, entity_type: str, database_id: int, line: str) -> dict[str, Any]:
    function = {
        "character": queries.get_character_detail,
        "weapon": queries.get_weapon_detail,
        "artifact_set": queries.get_artifact_set_detail,
    }[entity_type]
    return function(db_path, database_id, line)


def _exact_name_candidates(
    db_path: Any, entity_type: str, name: str, line: str,
) -> list[dict[str, Any]]:
    function = {
        "character": queries.list_characters,
        "weapon": queries.list_weapons,
        "artifact_set": queries.list_artifact_sets,
    }[entity_type]
    kwargs: dict[str, Any] = {
        "name": name, "page": 1, "page_size": 100,
    }
    if entity_type in {"weapon", "artifact_set"}:
        kwargs["catalog_scope"] = "all"
    result = function(db_path, line, **kwargs)
    total = result["total"]
    pages = min(100, (total + 99) // 100)
    items = list(result["items"])
    for page in range(2, pages + 1):
        kwargs["page"] = page
        items.extend(function(db_path, line, **kwargs)["items"])
    return [copy.deepcopy(row) for row in items if row.get("name") == name]


def _lookup(
    session: Any, entity: Mapping[str, Any], *, database_id: int | None,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    entity_type = entity["entity_type"]
    line = entity["version_claim"]["version_line"]
    query = {
        "kind": entity_type,
        "version_line": line,
        **({"database_id": database_id} if database_id is not None
           else {"exact_name": entity["display_name"]}),
    }
    candidates: list[dict[str, Any]] = []
    unavailable: Exception | None = None
    try:
        with session._read_db():
            if not session.db_path.is_file():
                raise FileNotFoundError("数据库不存在")
            if database_id is not None:
                try:
                    candidates = [_detail(session.db_path, entity_type, database_id, line)]
                except queries.NotFoundError:
                    candidates = []
            else:
                matches = _exact_name_candidates(
                    session.db_path, entity_type, entity["display_name"], line,
                )
                # Exact-name discovery first returns bounded identities. Fetch
                # full records so a public-data diff is never approved against
                # an incomplete catalog projection.
                candidates = [
                    _detail(session.db_path, entity_type, row["id"], line)
                    for row in matches[:16]
                ]
                if len(matches) > 16:
                    _error("同名数据库候选超过16条，请按真实数据库ID重新核对",
                           "provisional_entity_lookup_ambiguous")
            if entity.get("authorization_kind") == "public_data_patch":
                from genshin_app.equipment.provisional_baseline import (
                    typed_loadout_baseline,
                )
                candidates = [typed_loadout_baseline(session.db_path, entity, row) for row in candidates]
    except PlanError:
        raise
    except (OSError, sqlite3.Error, ValueError) as exc:
        unavailable = exc
    status = "unavailable" if unavailable is not None else (
        "not_found" if not candidates else "conflict"
    )
    receipt = {
        "contract": LOOKUP_RECEIPT_CONTRACT,
        "receipt_id": "lookup." + uuid.uuid4().hex,
        "issued_by": "app_database_query",
        "status": status,
        "query": query,
        "database_snapshot_fingerprint": fingerprint(session.database_snapshot),
        "observed_candidates": candidates,
        "message": (
            "数据库查询暂不可用，不能据此认定资料缺失或错误"
            if unavailable is not None else
            "当前固定数据库快照没有精确匹配"
            if not candidates else
            "当前固定数据库快照已有精确匹配，任何改动都需要用户确认"
        ),
        "issued_at": _stamp(),
    }
    return receipt, candidates


def verify_lookup(
    session: Any, *, provisional_id: str, expected_revision: int,
    database_id: int | None, reason: str,
) -> dict[str, Any]:
    """Query the fixed DB and atomically install one App-minted lookup receipt."""

    _require_native_edit_stage(session)
    session._expect(expected_revision)
    index, entity = _find_entity(session.candidate, provisional_id)
    # Never mutate the live candidate before ``replace_candidate`` captures
    # its rollback snapshot.  The candidate row and authority ledger form one
    # transaction; a failed checkpoint must leave both exactly unchanged.
    entity = copy.deepcopy(entity)
    receipt, candidates = _lookup(session, entity, database_id=database_id)
    if receipt["status"] == "unavailable":
        _error(receipt["message"], "provisional_entity_lookup_unavailable", {
            "database_lookup": receipt,
        })
    authorization = entity["authorization_kind"]
    if authorization == "database_missing" and candidates:
        _error("数据库已存在精确资料；如确有错误，必须改为公共数据纠错并取得用户批准",
               "provisional_entity_database_present", {"database_lookup": receipt})
    if authorization == "public_data_patch" and not candidates:
        _error("数据库没有对应资料；应使用数据库缺失型兜底，不得伪装成纠错",
               "provisional_entity_database_missing", {"database_lookup": receipt})
    if authorization == "public_data_patch" and len(candidates) != 1:
        _error("公共数据纠错需要按真实数据库ID选定唯一基线",
               "provisional_entity_lookup_ambiguous", {"database_lookup": receipt})
    entity["database_lookup"] = receipt
    entity["public_data_diff"] = (
        build_public_data_diff(receipt, entity["values"])
        if authorization == "public_data_patch" else None
    )
    entity["payload_fingerprint"] = None
    entity["attestation"] = None
    entity["approval"] = None
    validate_entity(entity)
    prepared = copy.deepcopy(session.candidate)
    prepared[FIELD][index] = entity
    # Commit the lookup evidence and the candidate mutation as one authority
    # transaction.  A rejected or unavailable lookup is diagnostic only: it
    # must not clear an existing trusted result receipt or leave an orphaned
    # ledger row behind.
    state = _state(session)
    previous_state = copy.deepcopy(state)
    state["lookup_ledger"][receipt["receipt_id"]] = copy.deepcopy(receipt)
    state["trusted_usage_receipt"] = None
    try:
        summary = session.replace_candidate(prepared, expected_revision, reason)
    except Exception:
        session.provisional_authority = previous_state
        raise
    return {
        "status": "verified",
        "authorization_kind": authorization,
        "database_lookup": receipt,
        "candidate": summary,
        "next_step": (
            "调用数据库缺失资料登记工具，绑定已核实来源"
            if authorization == "database_missing" else
            "调用资料修正确认请求工具，并把返回的问题原样提交给用户"
        ),
    }


def attest_database_missing(
    session: Any, *, provisional_id: str, expected_revision: int, reason: str,
) -> dict[str, Any]:
    """Seal a sourced DB-missing fallback without impersonating user approval."""

    _require_native_edit_stage(session)
    session._expect(expected_revision)
    index, entity = _find_entity(session.candidate, provisional_id)
    entity = copy.deepcopy(entity)
    if entity["authorization_kind"] != "database_missing":
        _error("该提案不是数据库缺失型资料", "provisional_entity_attestation_not_applicable")
    lookup = entity["database_lookup"]
    if not isinstance(lookup, Mapping):
        _error("数据库缺失尚未取得受信App查询回执",
               "provisional_entity_lookup_untrusted")
    trusted = _state(session)["lookup_ledger"].get(lookup.get("receipt_id"))
    if not isinstance(trusted, Mapping) or dict(trusted) != lookup:
        _error("数据库缺失没有受信App查询回执", "provisional_entity_lookup_untrusted")
    if not _lookup_matches_entity(entity, lookup):
        _error("数据库查询回执不属于当前资料身份或版本线",
               "provisional_entity_lookup_stale")
    if lookup.get("status") != "not_found":
        _error("只有数据库明确未找到时才能登记缺失资料",
               "provisional_entity_lookup_unavailable")
    if not any(row.get("status") == "verified" for row in entity["source_refs"]):
        _error("数据库缺失资料至少需要一条已核实来源",
               "provisional_entity_verified_source_required")
    entity["payload_fingerprint"] = entity_payload_fingerprint(entity)
    post_revision = session.revision + 1
    request = attestation_request(
        session.candidate, entity,
        candidate_revision=post_revision,
        stage_revision=_stage_revision(session),
        database_snapshot_fingerprint=fingerprint(session.database_snapshot),
    )
    attestation = {
        "contract": ATTESTATION_CONTRACT,
        "attestation_id": "attestation." + uuid.uuid4().hex,
        "status": "agent_reported",
        "issued_by": "app_agent_materialization",
        "plan_id": request["plan_id"],
        "candidate_revision": post_revision,
        "stage_revision": request["stage_revision"],
        "authorization_basis_fingerprint": request[
            "authorization_basis_fingerprint"
        ],
        "database_snapshot_fingerprint": request["database_snapshot_fingerprint"],
        "payload_fingerprint": request["payload_fingerprint"],
        "disclosed_at": _stamp(),
    }
    entity["attestation"] = attestation
    validate_entity(entity)
    state = _state(session)
    previous_state = copy.deepcopy(state)
    state["attestation_ledger"][attestation["attestation_id"]] = copy.deepcopy(attestation)
    state["trusted_usage_receipt"] = None
    prepared = copy.deepcopy(session.candidate)
    prepared[FIELD][index] = entity
    try:
        summary = session.replace_candidate(prepared, expected_revision, reason)
    except Exception:
        session.provisional_authority = previous_state
        raise
    return {
        "status": "attested",
        "authority": "agent_reported_database_missing",
        "source_disclosure_required": True,
        "candidate": summary,
    }


def _question(entity: Mapping[str, Any], question_id: str) -> dict[str, Any]:
    from .native_session_store import redact_visible_text

    diff = entity["public_data_diff"]
    exact = json_bytes({
        "before": diff["before"],
        "after": diff["after"],
    }).decode("utf-8")
    comparison_note = ""
    if entity.get("values", {}).get("contract") == "provisional-loadout-entity-values-v1":
        comparison_note = ("比较口径：前值由App按真实数据库查询整理；contract等是结构标签，"
            "被动／套装处理策略属于本次人工解释，不是数据库原字段。原查询与数值来源保留在当前提案的database_lookup中。\n")
    detail = (
        f"数据库已有“{entity['display_name']}”资料。以下是完整精确差异：\n{comparison_note}{exact}\n"
        "这项决定只作用于当前方案，不会写回公共数据库；候选或数据库变化后自动失效。"
    )
    if len(detail) > MAX_HOST_QUESTION_DETAIL_CHARS:
        _error("资料修正差异超过单个确认卡可完整展示的范围；请拆成更小的精确字段提案",
               "provisional_entity_patch_diff_too_large", {
                   "detail_chars": len(detail),
                   "host_limit": MAX_HOST_QUESTION_DETAIL_CHARS,
                   "changed_paths": copy.deepcopy(diff["changed_paths"]),
               })
    if redact_visible_text(detail) != detail:
        _error("资料修正差异包含Host不能安全展示的内容；请拆分并改用可核对的公开字段",
               "provisional_entity_patch_diff_not_displayable")
    return {
        "id": question_id,
        "question": f"是否允许当前方案采用对“{entity['display_name']}”的资料修正？",
        "detail": detail,
        "header": "确认资料修正",
        "options": [
            {"label": _APPROVE_LABEL, "description": "采用上方精确差异，仅用于当前方案。"},
            {"label": _REJECT_LABEL, "description": "保留数据库原值，当前提案不生效。"},
        ],
        "multi_select": False,
        "allow_skip": True,
    }


def request_public_patch_approval(
    session: Any, *, provisional_id: str, expected_revision: int, reason: str,
) -> dict[str, Any]:
    """Prepare, but never self-answer, one exact Host user question."""

    _require_native_edit_stage(session)
    session._expect(expected_revision)
    index, entity = _find_entity(session.candidate, provisional_id)
    entity = copy.deepcopy(entity)
    if entity["authorization_kind"] != "public_data_patch":
        _error("只有修正数据库现存事实才需要用户批准",
               "provisional_entity_approval_not_applicable")
    lookup = entity["database_lookup"]
    if not isinstance(lookup, Mapping):
        _error("资料修正尚未取得受信App数据库查询回执",
               "provisional_entity_lookup_untrusted")
    trusted = _state(session)["lookup_ledger"].get(lookup.get("receipt_id"))
    if not isinstance(trusted, Mapping) or dict(trusted) != lookup:
        _error("资料修正没有受信App数据库查询回执",
               "provisional_entity_lookup_untrusted")
    if not _lookup_matches_entity(entity, lookup):
        _error("数据库查询回执不属于当前资料身份或版本线",
               "provisional_entity_lookup_stale")
    if lookup.get("status") != "conflict":
        _error("数据库查询不可用或没有现存基线，不能请求纠错批准",
               "provisional_entity_lookup_unavailable")
    entity["payload_fingerprint"] = entity_payload_fingerprint(entity)
    entity["approval"] = None
    post_revision = session.revision + 1
    prepared = copy.deepcopy(session.candidate)
    prepared[FIELD][index] = entity
    request = approval_request(
        prepared, entity,
        candidate_revision=post_revision,
        stage_revision=_stage_revision(session),
        database_snapshot_fingerprint=fingerprint(session.database_snapshot),
    )
    question_id = QUESTION_PREFIX + uuid.uuid4().hex
    question = _question(entity, question_id)
    pending = {
        "question_id": question_id,
        "state": "pending",
        "entity_type": entity["entity_type"],
        "provisional_id": provisional_id,
        "payload_fingerprint": request["payload_fingerprint"],
        "candidate_revision": post_revision,
        "stage_revision": request["stage_revision"],
        "authorization_basis_fingerprint": request[
            "authorization_basis_fingerprint"
        ],
        "database_snapshot_fingerprint": request["database_snapshot_fingerprint"],
        "question": question,
        "batch_id": None,
        "call_id": None,
        "approve_value_id": None,
        "reject_value_id": None,
        "answer_receipt": None,
        "created_at": _stamp(),
    }
    state = _state(session)
    previous_state = copy.deepcopy(state)
    state["pending_approvals"][question_id] = pending
    state["trusted_usage_receipt"] = None
    try:
        summary = session.replace_candidate(prepared, expected_revision, reason)
    except Exception:
        session.provisional_authority = previous_state
        raise
    return {
        "status": "waiting_for_user",
        "candidate": summary,
        "question": question,
        "instruction": (
            "资料修正确认已显示在App候选区；请用户核对差异并明确批准。不要自行回答或用通用提问代替此批准。"
            if getattr(session, "workflow", None) == "artifact"
            else "请把question对象原样交给request_user_input；只有Host收到用户回答后才会签发批准。"
        ),
    }


def _question_projection(question: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "id": question["question_id"],
        "question": question["prompt"],
        "detail": question.get("detail"),
        "header": question.get("header"),
        "options": [{
            "label": option["label"], "description": option.get("description"),
        } for option in question.get("options", [])],
        "multi_select": question.get("kind") == "multiple",
        "allow_skip": question.get("allow_skip") is True,
    }


def bind_question_batch(
    session: Any, *, batch_id: str, call_id: str,
    items: Sequence[Mapping[str, Any]],
) -> dict[str, Any] | None:
    """Bind an exact D2 question before the Host displays it."""

    d2_items = [item for item in items
                if isinstance(item, Mapping)
                and str(item.get("question_id", "")).startswith(QUESTION_PREFIX)]
    if not d2_items:
        return None
    if len(d2_items) != 1:
        _error("一次只能确认一项公共资料修正", "provisional_entity_question_invalid")
    item = d2_items[0]
    pending = _state(session)["pending_approvals"].get(item["question_id"])
    if not isinstance(pending, dict) or pending.get("state") not in {"pending", "bound"}:
        _error("资料修正确认问题没有对应的App提案",
               "provisional_entity_question_untrusted")
    if (_question_projection(item) != pending["question"]
            or authorization_basis_fingerprint(session.candidate)
            != pending["authorization_basis_fingerprint"]
            or _stage_revision(session) != pending["stage_revision"]
            or fingerprint(session.database_snapshot)
            != pending["database_snapshot_fingerprint"]):
        _error("资料修正确认问题已被修改或候选已变化",
               "provisional_entity_question_stale")
    if (pending["state"] == "bound"
            and (pending.get("batch_id") != batch_id
                 or pending.get("call_id") != call_id)):
        _error("资料修正确认问题已绑定其他Host调用",
               "provisional_entity_question_untrusted")
    labels = {option["label"]: option["value_id"] for option in item["options"]}
    if set(labels) != {_APPROVE_LABEL, _REJECT_LABEL}:
        _error("资料修正确认选项被修改", "provisional_entity_question_invalid")
    question_id = pending["question_id"]

    def mutate(state: dict[str, Any]) -> None:
        target = state["pending_approvals"][question_id]
        target.update(
            state="bound", batch_id=batch_id, call_id=call_id,
            approve_value_id=labels[_APPROVE_LABEL],
            reject_value_id=labels[_REJECT_LABEL],
        )

    _save_state_transaction(session, mutate)
    return {"status": "bound", "question_id": question_id}


def settle_question_answer(
    session: Any, *, question: Mapping[str, Any], reason: str,
) -> dict[str, Any] | None:
    """Consume a Host-persisted answer; the model cannot call this boundary."""

    items = question.get("items") if isinstance(question, Mapping) else None
    if not isinstance(items, list):
        return None
    d2_items = [item for item in items
                if str(item.get("question_id", "")).startswith(QUESTION_PREFIX)]
    if not d2_items:
        return None
    if len(d2_items) != 1:
        _error("资料修正确认批次不合法", "provisional_entity_question_invalid")
    item = d2_items[0]
    state = _state(session)
    pending = state["pending_approvals"].get(item["question_id"])
    if (not isinstance(pending, dict)
            or pending.get("state") not in {"bound", "approved", "rejected", "stale"}):
        _error("资料修正确认没有受信Host绑定", "provisional_entity_question_untrusted")
    if pending["state"] in {"approved", "rejected", "stale"}:
        if (question.get("question_id") != pending.get("batch_id")
                or question.get("answer_receipt") != pending.get("answer_receipt")):
            _error("资料修正确认重放与原Host答案回执不一致",
                   "provisional_entity_question_untrusted")
        return {
            "status": "already_approved" if pending["state"] == "approved"
            else pending["state"],
            "question_id": pending["question_id"],
        }
    if (question.get("question_id") != pending["batch_id"]
            or question.get("answer_receipt") is None):
        _error("资料修正确认答案没有绑定原问题", "provisional_entity_question_untrusted")
    answers = question.get("answer", {}).get("items", [])
    answer = next((row for row in answers
                   if row.get("question_id") == pending["question_id"]), None)
    approved = (
        isinstance(answer, dict) and answer.get("skipped") is False
        and answer.get("value_ids") == [pending["approve_value_id"]]
    )
    if not approved:
        _save_state_transaction(session, lambda current: current[
            "pending_approvals"][pending["question_id"]].update(
                state="rejected", answer_receipt=question["answer_receipt"]
            ))
        return {"status": "rejected", "question_id": pending["question_id"]}
    if (authorization_basis_fingerprint(session.candidate)
            != pending["authorization_basis_fingerprint"]
            or _stage_revision(session) != pending["stage_revision"]
            or fingerprint(session.database_snapshot)
            != pending["database_snapshot_fingerprint"]):
        _save_state_transaction(session, lambda current: current[
            "pending_approvals"][pending["question_id"]].update(
                state="stale", answer_receipt=question["answer_receipt"]
            ))
        return {"status": "stale", "question_id": pending["question_id"]}
    index, entity = _find_entity(session.candidate, pending["provisional_id"])
    entity = copy.deepcopy(entity)
    if entity_payload_fingerprint(entity) != pending["payload_fingerprint"]:
        _save_state_transaction(session, lambda current: current[
            "pending_approvals"][pending["question_id"]].update(
                state="stale", answer_receipt=question["answer_receipt"]
            ))
        return {"status": "stale", "question_id": pending["question_id"]}
    post_revision = session.revision + 1
    request = approval_request(
        session.candidate, entity,
        candidate_revision=post_revision,
        stage_revision=_stage_revision(session),
        database_snapshot_fingerprint=fingerprint(session.database_snapshot),
    )
    approval = {
        "contract": APPROVAL_CONTRACT,
        "approval_id": "approval." + uuid.uuid4().hex,
        "status": "user_confirmed",
        "issued_by": "user_decision_ledger",
        "plan_id": request["plan_id"],
        "candidate_revision": post_revision,
        "stage_revision": request["stage_revision"],
        "authorization_basis_fingerprint": request[
            "authorization_basis_fingerprint"
        ],
        "database_snapshot_fingerprint": request["database_snapshot_fingerprint"],
        "payload_fingerprint": request["payload_fingerprint"],
        "question_id": pending["question_id"],
        "answer_receipt": question["answer_receipt"],
        "approved_at": _stamp(),
    }
    entity["approval"] = approval
    validate_entity(entity)
    previous_state = copy.deepcopy(state)
    state["approval_ledger"][approval["approval_id"]] = copy.deepcopy(approval)
    pending.update(
        state="approved", answer_receipt=question["answer_receipt"],
        result_candidate_revision=post_revision,
    )
    state["trusted_usage_receipt"] = None
    prepared = copy.deepcopy(session.candidate)
    prepared[FIELD][index] = entity
    try:
        summary = session.replace_candidate(prepared, session.revision, reason)
    except Exception:
        session.provisional_authority = previous_state
        raise
    return {
        "status": "approved", "question_id": pending["question_id"],
        "candidate": summary,
    }


def invalidate_after_candidate_change(session: Any) -> None:
    """Invalidate proofs on business edits, not on sibling authority writes."""

    state = _state(session)
    state["trusted_usage_receipt"] = None
    current_basis = authorization_basis_fingerprint(session.candidate)
    previous_basis = state.get("authorization_basis_fingerprint")
    if previous_basis is None:
        state["authorization_basis_fingerprint"] = current_basis
    elif previous_basis != current_basis:
        # Preserve database lookup audit rows, but never let an old Agent
        # attestation or user approval revive if the user edits away and then
        # later reconstructs the same bytes.
        state["authorization_basis_fingerprint"] = current_basis
        state["attestation_ledger"] = {}
        state["approval_ledger"] = {}
        for pending in state["pending_approvals"].values():
            if pending.get("state") in {"pending", "bound"}:
                pending["state"] = "stale"
        return
    for pending in state["pending_approvals"].values():
        if (pending.get("state") in {"pending", "bound"}
                and pending.get("authorization_basis_fingerprint")
                != current_basis):
            pending["state"] = "stale"


def reset_for_document_restore(session: Any) -> None:
    session.provisional_authority = new_state(
        session.session_id, fingerprint(session.database_snapshot), session.candidate,
    )


def trusted_usage_receipt(session: Any) -> dict[str, Any] | None:
    value = _state(session).get("trusted_usage_receipt")
    return copy.deepcopy(value) if isinstance(value, dict) else None


def prepare_readonly_projection(session, trial, changes):
    """Derive a non-writing calculation scope from the original authority."""
    from genshin_app.equipment.provisional_readonly import _prepare_readonly_projection

    from .candidate import CandidateSession
    if not isinstance(session, CandidateSession):
        _error("只读临时事实试算需要真实原App会话，不能以手工对象代替受信账",
               "energy_trial_trust_scope_unavailable")
    return _prepare_readonly_projection(session, trial, changes)


def publish_usage_receipt(
    session: Any, *, resolved_uses: list[Mapping[str, Any]],
) -> dict[str, Any]:
    """Publish the exact out-of-band receipt from real execution consumers.

    This is deliberately not exposed as an Agent tool.  A Composer/runtime
    consumer calls it only with values returned by ``resolve_entity_ref``.
    Merely declaring a registry row can never create formal result authority.
    """

    receipt = build_usage_receipt(
        session.candidate, resolved_uses,
        candidate_revision=session.revision,
        stage_revision=_stage_revision(session),
        database_snapshot_fingerprint=fingerprint(session.database_snapshot),
    )
    _save_state_transaction(
        session,
        lambda state: state.update(
            trusted_usage_receipt=copy.deepcopy(receipt)
        ),
    )
    return copy.deepcopy(receipt)


__all__ = [
    "STATE_CONTRACT",
    "attest_database_missing",
    "bind_question_batch",
    "invalidate_after_candidate_change",
    "new_state",
    "prepare_readonly_projection",
    "publish_usage_receipt",
    "request_public_patch_approval",
    "reset_for_document_restore",
    "settle_question_answer",
    "trusted_usage_receipt",
    "validate_state",
    "verify_lookup",
]
