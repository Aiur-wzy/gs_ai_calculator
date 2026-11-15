"""Fresh reviewed static action admission profiles on one SQLite snapshot."""

from __future__ import annotations

import base64
import binascii
import copy
import json
import re
import sqlite3

from genshin_db.runtime_action_resource import read_action_resource_profile
from genshin_db.runtime_catalog import dumps, fingerprint
from genshin_db.runtime_fact_inventory import (
    KNOWN_FACT_KINDS,
    MAX_CLASSIFICATION_BYTES,
    MAX_CLASSIFICATION_FACTS,
    MAX_CLASSIFICATION_RECORD_BYTES,
    read_current_fact_inventory,
)
from genshin_db.runtime_queries import _read
from genshin_db.semantic_store import (
    _COLUMNS,
    _decode,
    read_current_reviewed_record,
    read_record,
    validate_component,
)
from genshin_runtime.action_admission_model import (
    FIELD_AUTHORITY,
    MAX_FACTS,
    PROFILE_CONTRACT,
    evidence_for,
    ref,
    same,
    selection_matches,
    validate_model,
    validate_selection,
)
from genshin_runtime.action_root_semantics import bounded

MAX_PROFILE_BYTES = 512 * 1024
_PUBLIC_FIELDS = (
    "id",
    "revision",
    "subject",
    "payload",
    "evidence",
    "applicability",
    "review",
)
CATALOG_QUERY_CONTRACT = "action-admission-model-catalog-query-v1"
CATALOG_PAGE_CONTRACT = "action-admission-model-catalog-page-v1"
CATALOG_CURSOR_CONTRACT = "action-admission-model-catalog-cursor-v1"
CATALOG_ORDER = "review_priority_then_skill_identity_v1"
MAX_CATALOG_PAGE_SIZE = 32
DEFAULT_CATALOG_PAGE_SIZE = 16
MAX_CATALOG_QUERY_BYTES = 64 * 1024
MAX_CATALOG_CURSOR_BYTES = 8192
MAX_CATALOG_RESPONSE_BYTES = 512 * 1024
_SHA256 = re.compile(r"[0-9a-f]{64}")
_CURSOR_TEXT = re.compile(r"[A-Za-z0-9_-]+")
_CATALOG_PATHS = (
    "domain.action_type_id",
    "domain.constellation",
    "domain.variant",
    "target.skill_depot_id",
    "target.skill_id",
    "target.slot",
)
_CATALOG_GROUPS = (
    "exact",
    "partial",
    "pending",
    "revoked",
    "stale",
    "conflict",
    "not_evaluated",
)
_CATALOG_RANK = {name: index for index, name in enumerate(_CATALOG_GROUPS)}


class ActionAdmissionError(ValueError):
    def __init__(self, code, message, details=None):
        super().__init__(message)
        self.code = code
        self.details = details


def _require(value, message, code="action_admission_material"):
    if not value:
        raise ActionAdmissionError(code, message)


def capabilities():
    return {
        "contract": PROFILE_CONTRACT,
        "qualification": "reviewed_static_action_gate_mapping",
        "read_only": True,
        "version_lines": ["release"],
        "constellations": [0],
        "slots": ["burst"],
        "variants": ["default"],
        "action_type_ids": ["action.elemental_burst"],
        "resource_modes": ["single_charge_fixed_equal_cost"],
        "owned_fields": list(FIELD_AUTHORITY),
        "max_current_models_per_character": MAX_FACTS,
        "max_profile_bytes": MAX_PROFILE_BYTES,
        "execution_authority": False,
        "limits": [
            "no_skill",
            "no_switch",
            "no_alias",
            "no_multi_charge",
            "no_capacity_combination",
        ],
        "model_catalog": {
            "query_contract": CATALOG_QUERY_CONTRACT,
            "page_contract": CATALOG_PAGE_CONTRACT,
            "cursor_contract": CATALOG_CURSOR_CONTRACT,
            "order": CATALOG_ORDER,
            "groups": list(_CATALOG_GROUPS),
            "max_page_size": MAX_CATALOG_PAGE_SIZE,
            "max_response_bytes": MAX_CATALOG_RESPONSE_BYTES,
            "complete_current_fact_classification": True,
            "returns_compiled_cooldown_or_energy": False,
            "execution_authority": False,
        },
    }


def _record(current):
    return {key: copy.deepcopy(current["record"][key]) for key in _PUBLIC_FIELDS}


def _finalize_profile(result):
    """Apply the same structural and byte budget to every profile outcome."""
    try:
        bounded(result)
        encoded_size = len(dumps(result).encode("utf-8"))
    except (
        ValueError,
        TypeError,
        KeyError,
        RecursionError,
        OverflowError,
        UnicodeError,
    ) as exc:
        raise ActionAdmissionError(
            "action_admission_budget", "动作接纳资料响应无法安全编码"
        ) from exc
    _require(
        encoded_size <= MAX_PROFILE_BYTES,
        "动作接纳资料响应超过字节预算",
        "action_admission_budget",
    )
    return copy.deepcopy(result)


def _target_key(claim):
    return {
        "target": copy.deepcopy(claim["target"]),
        "variant": claim["domain"]["variant"],
        "action_type_id": claim["domain"]["action_type_id"],
        "owned_fields": sorted(FIELD_AUTHORITY),
    }


def _current_fact_inventory(conn, state):
    """Validate and retain every current fact before domain classification."""
    _require(
        conn.in_transaction,
        "动作接纳资料须在调用者事务内读取",
        "action_admission_transaction",
    )
    _require(
        conn.execute("PRAGMA encoding").fetchone()[0] == "UTF-8",
        "当前事实字节分类仅支持 UTF-8 数据库",
        "action_admission_current",
    )
    _require(
        validate_component(conn, state["version_line"])["status"] == "ready",
        "语义目录未就绪",
        "action_admission_current",
    )
    inventory = read_current_fact_inventory(
        conn,
        state["version_line"],
        columns=_COLUMNS,
        decode=_decode,
        require=_require,
        current_code="action_admission_current",
        budget_code="action_admission_budget",
        max_facts=MAX_CLASSIFICATION_FACTS,
        max_record_bytes=MAX_CLASSIFICATION_RECORD_BYTES,
        max_total_bytes=MAX_CLASSIFICATION_BYTES,
        known_fact_kinds=KNOWN_FACT_KINDS,
    )
    for item in inventory:
        if item["envelope"]["fact_kind"] != "action_admission_model":
            continue
        record = item["record"]
        try:
            claim = record["payload"]["claim"]
            validate_model(claim)
        except (ValueError, TypeError, KeyError, RecursionError, OverflowError) as exc:
            raise ActionAdmissionError(
                "action_admission_current", "当前动作接纳事实合同无效，不能排除"
            ) from exc
        _require(
            record["subject"]
            == {
                "target_type": "character",
                "target_id": claim["target"]["character_id"],
            },
            "当前动作接纳事实主体与目标不一致",
            "action_admission_current",
        )
        item["envelope"]["target_key_fingerprint"] = fingerprint(_target_key(claim))
    return inventory


def _catalog_inventory(inventory, character_id):
    entries, refs = [], []
    for item in inventory:
        record = item["record"]
        envelope = copy.deepcopy(item["envelope"])
        candidate = envelope["fact_kind"] == "action_admission_model" and record[
            "subject"
        ] == {"target_type": "character", "target_id": character_id}
        envelope["disposition"] = (
            "catalog_candidate" if candidate else "classified_non_competing"
        )
        entries.append(envelope)
        if candidate:
            refs.append({"id": record["id"], "revision": record["revision"]})
            _require(
                len(refs) <= MAX_FACTS,
                "角色当前动作接纳模型超过数量预算",
                "action_admission_budget",
            )
    return entries, refs


def _catalog_fail(code, message, details=None):
    raise ActionAdmissionError(code, message, details)


def _catalog_shape(value):
    stack, nodes = [(value, 0)], 0
    while stack:
        item, depth = stack.pop()
        nodes += 1
        if nodes > 256 or depth > 12:
            _catalog_fail(
                "action_admission_catalog_query", "动作接纳目录请求超过结构预算"
            )
        if type(item) is dict:
            if len(item) > 24:
                _catalog_fail(
                    "action_admission_catalog_query", "动作接纳目录请求字段过多"
                )
            for key, child in item.items():
                if type(key) is not str or not 1 <= len(key) <= 128:
                    _catalog_fail(
                        "action_admission_catalog_query", "动作接纳目录字段名无效"
                    )
                stack.append((child, depth + 1))
        elif type(item) is list:
            if len(item) > 32:
                _catalog_fail("action_admission_catalog_query", "动作接纳目录数组过长")
            stack.extend((child, depth + 1) for child in item)
        elif type(item) is str:
            if len(item) > MAX_CATALOG_CURSOR_BYTES:
                _catalog_fail(
                    "action_admission_catalog_query", "动作接纳目录字符串过长"
                )
        elif item is None or type(item) in (bool, int):
            continue
        else:
            _catalog_fail(
                "action_admission_catalog_query", "动作接纳目录仅接受有限JSON值"
            )
    try:
        size = len(dumps(value).encode("utf-8"))
    except (TypeError, ValueError, RecursionError, OverflowError, UnicodeError) as exc:
        raise ActionAdmissionError(
            "action_admission_catalog_query", "动作接纳目录请求不是规范JSON"
        ) from exc
    if size > MAX_CATALOG_QUERY_BYTES:
        _catalog_fail("action_admission_catalog_query", "动作接纳目录请求超过64KiB")


def _catalog_int(value, name, low=1, high=2**53 - 1):
    if type(value) is not int or not low <= value <= high:
        _catalog_fail(
            "action_admission_catalog_query", f"{name}必须为{low}–{high}的精确整数"
        )


def _catalog_observed_paths(observed):
    return sorted(
        f"{group}.{key}"
        for group in ("target", "domain")
        for key in observed.get(group, {})
    )


def _validate_catalog_request(request):
    _catalog_shape(request)
    required = {
        "contract",
        "version_line",
        "character_id",
        "observed",
        "unknown_paths",
        "view",
        "order",
        "page_size",
        "cursor",
    }
    if (
        type(request) is not dict
        or set(request) != required
        or request["contract"] != CATALOG_QUERY_CONTRACT
    ):
        _catalog_fail(
            "action_admission_catalog_query", "动作接纳目录请求合同或字段无效"
        )
    if request["version_line"] != "release":
        _catalog_fail(
            "action_admission_catalog_query", "动作接纳目录首片只支持release资料线"
        )
    _catalog_int(request["character_id"], "character_id")
    observed = request["observed"]
    if type(observed) is not dict or not set(observed) <= {"target", "domain"}:
        _catalog_fail("action_admission_catalog_query", "observed字段无效")
    target = observed.get("target", {})
    if type(target) is not dict or not set(target) <= {
        "skill_depot_id",
        "skill_id",
        "slot",
    }:
        _catalog_fail("action_admission_catalog_query", "observed.target字段无效")
    for key in ("skill_depot_id", "skill_id"):
        if key in target:
            _catalog_int(target[key], "target." + key)
    if "slot" in target and target["slot"] != "burst":
        _catalog_fail("action_admission_catalog_query", "target.slot只支持burst")
    domain = observed.get("domain", {})
    if type(domain) is not dict or not set(domain) <= {
        "constellation",
        "variant",
        "action_type_id",
    }:
        _catalog_fail("action_admission_catalog_query", "observed.domain字段无效")
    if "constellation" in domain:
        _catalog_int(domain["constellation"], "domain.constellation", 0, 6)
    if "variant" in domain and domain["variant"] != "default":
        _catalog_fail("action_admission_catalog_query", "domain.variant只支持default")
    if (
        "action_type_id" in domain
        and domain["action_type_id"] != "action.elemental_burst"
    ):
        _catalog_fail(
            "action_admission_catalog_query", "domain.action_type_id只支持元素爆发"
        )
    unknown = request["unknown_paths"]
    if (
        type(unknown) is not list
        or unknown != sorted(set(unknown))
        or any(type(path) is not str or path not in _CATALOG_PATHS for path in unknown)
    ):
        _catalog_fail(
            "action_admission_catalog_query",
            "unknown_paths必须有序、唯一且属于受支持字段",
        )
    expected_unknown = sorted(
        set(_CATALOG_PATHS) - set(_catalog_observed_paths(observed))
    )
    if unknown != expected_unknown:
        _catalog_fail(
            "action_admission_catalog_query", "unknown_paths必须精确描述未提供字段"
        )
    if request["view"] not in ("all", *_CATALOG_GROUPS):
        _catalog_fail("action_admission_catalog_query", "动作接纳目录view无效")
    if request["order"] != CATALOG_ORDER:
        _catalog_fail("action_admission_catalog_query", "动作接纳目录排序合同无效")
    _catalog_int(request["page_size"], "page_size", 1, MAX_CATALOG_PAGE_SIZE)
    cursor = request["cursor"]
    if cursor is not None and (
        type(cursor) is not str or not 1 <= len(cursor) <= MAX_CATALOG_CURSOR_BYTES
    ):
        _catalog_fail("action_admission_catalog_cursor", "动作接纳目录游标长度无效")
    return copy.deepcopy(request)


def _cursor_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            _catalog_fail(
                "action_admission_catalog_cursor", "动作接纳目录游标含重复字段"
            )
        result[key] = value
    return result


def _decode_catalog_cursor(value):
    if value is None:
        return None
    if not _CURSOR_TEXT.fullmatch(value):
        _catalog_fail("action_admission_catalog_cursor", "动作接纳目录游标编码无效")
    try:
        raw = base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))
        if len(raw) > MAX_CATALOG_CURSOR_BYTES:
            _catalog_fail(
                "action_admission_catalog_cursor", "动作接纳目录游标超过字节预算"
            )
        if base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=") != value:
            _catalog_fail(
                "action_admission_catalog_cursor", "动作接纳目录游标不是规范base64url"
            )
        wrapper = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=_cursor_object,
            parse_constant=lambda _value: _catalog_fail(
                "action_admission_catalog_cursor", "动作接纳目录游标含非有限值"
            ),
        )
    except ActionAdmissionError:
        raise
    except (
        binascii.Error,
        UnicodeError,
        json.JSONDecodeError,
        ValueError,
        TypeError,
    ) as exc:
        raise ActionAdmissionError(
            "action_admission_catalog_cursor", "动作接纳目录游标无法解码"
        ) from exc
    if (
        type(wrapper) is not dict
        or set(wrapper) != {"payload", "sha256"}
        or fingerprint(wrapper["payload"]) != wrapper["sha256"]
    ):
        _catalog_fail(
            "action_admission_catalog_cursor", "动作接纳目录游标完整性校验失败"
        )
    payload = wrapper["payload"]
    fields = {
        "contract",
        "runtime_snapshot_id",
        "admission_inventory_fingerprint",
        "catalog_fingerprint",
        "query_fingerprint",
        "view",
        "order",
        "page_size",
        "after",
    }
    if (
        type(payload) is not dict
        or set(payload) != fields
        or payload["contract"] != CATALOG_CURSOR_CONTRACT
    ):
        _catalog_fail("action_admission_catalog_cursor", "动作接纳目录游标合同无效")
    for key in (
        "admission_inventory_fingerprint",
        "catalog_fingerprint",
        "query_fingerprint",
    ):
        if type(payload[key]) is not str or not _SHA256.fullmatch(payload[key]):
            _catalog_fail("action_admission_catalog_cursor", "动作接纳目录游标指纹无效")
    if (
        type(payload["runtime_snapshot_id"]) is not str
        or not 1 <= len(payload["runtime_snapshot_id"]) <= 128
    ):
        _catalog_fail("action_admission_catalog_cursor", "动作接纳目录游标快照无效")
    if (
        payload["view"] not in ("all", *_CATALOG_GROUPS)
        or payload["order"] != CATALOG_ORDER
    ):
        _catalog_fail(
            "action_admission_catalog_cursor", "动作接纳目录游标筛选或排序无效"
        )
    if (
        type(payload["page_size"]) is not int
        or not 1 <= payload["page_size"] <= MAX_CATALOG_PAGE_SIZE
    ):
        _catalog_fail("action_admission_catalog_cursor", "动作接纳目录游标页大小无效")
    after = payload["after"]
    if (
        type(after) is not list
        or len(after) != 6
        or type(after[0]) is not int
        or not 0 <= after[0] < len(_CATALOG_GROUPS)
        or any(type(after[index]) is not int or after[index] < 0 for index in (1, 2))
        or type(after[3]) is not str
        or len(after[3]) > 16
        or type(after[4]) is not str
        or not re.fullmatch(r"[a-z0-9][a-z0-9._-]{0,79}", after[4])
        or type(after[5]) is not int
        or not 1 <= after[5] <= 2**31 - 1
    ):
        _catalog_fail("action_admission_catalog_cursor", "动作接纳目录游标位置无效")
    if dumps(wrapper).encode("utf-8") != raw:
        _catalog_fail("action_admission_catalog_cursor", "动作接纳目录游标不是规范编码")
    return payload


def _encode_catalog_cursor(payload):
    wrapper = {"payload": payload, "sha256": fingerprint(payload)}
    return (
        base64.urlsafe_b64encode(dumps(wrapper).encode("utf-8"))
        .decode("ascii")
        .rstrip("=")
    )


def _display_name(conn, sql, params):
    try:
        rows = conn.execute(sql + " LIMIT 2", params).fetchall()
    except sqlite3.Error:
        return {"value": None, "status": "unavailable"}
    if not rows:
        return {"value": None, "status": "missing"}
    if len(rows) != 1:
        return {"value": None, "status": "ambiguous"}
    value = rows[0][0]
    if type(value) is str and value.strip():
        return {"value": value, "status": "present"}
    return {"value": None, "status": "missing" if value is None else "invalid"}


def _catalog_display(conn, line, target):
    return {
        "character": _display_name(
            conn,
            "SELECT name FROM character WHERE version_line=? AND id=?",
            (line, target["character_id"]),
        ),
        "skill": _display_name(
            conn,
            "SELECT name FROM skill WHERE version_line=? AND character_id=? AND id=?",
            (line, target["character_id"], target["skill_id"]),
        ),
    }


def _declared_filter(claim, request):
    declared = {
        "target.skill_depot_id": claim["target"]["skill_depot_id"],
        "target.skill_id": claim["target"]["skill_id"],
        "target.slot": claim["target"]["slot"],
        "domain.constellation": claim["domain"]["constellations"],
        "domain.variant": claim["domain"]["variant"],
        "domain.action_type_id": claim["domain"]["action_type_id"],
    }
    observed = {
        f"{group}.{key}": value
        for group in ("target", "domain")
        for key, value in request["observed"].get(group, {}).items()
    }
    matched, mismatched = [], []
    for path in sorted(observed):
        expected, actual = declared[path], observed[path]
        agrees = (
            actual in expected
            if path == "domain.constellation"
            else same(expected, actual)
        )
        if agrees:
            matched.append(path)
        else:
            mismatched.append(
                {
                    "path": path,
                    "expected": copy.deepcopy(expected),
                    "actual": copy.deepcopy(actual),
                }
            )
    status = (
        "mismatch"
        if mismatched
        else "partial_match"
        if request["unknown_paths"]
        else "exact_match"
    )
    return {
        "status": status,
        "matched_paths": matched,
        "mismatched": mismatched,
        "unknown_input_paths": copy.deepcopy(request["unknown_paths"]),
    }


def _catalog_competition_key(claim):
    return fingerprint(
        {
            "target": claim["target"],
            "domain": claim["domain"],
            "owned_fields": sorted(FIELD_AUTHORITY),
        }
    )


def _catalog_sort_key(item):
    target = item.get("target") or {}
    return (
        _CATALOG_RANK[item["catalog_group"]],
        target.get("skill_depot_id", 0),
        target.get("skill_id", 0),
        target.get("slot", ""),
        item["model_ref"]["id"],
        item["model_ref"]["revision"],
    )


def _catalog_identity(item):
    keys = (
        "model_ref",
        "current_receipt",
        "review",
        "catalog_group",
        "status_reason",
        "selection_status",
        "target",
        "domain",
        "declared_filter",
        "display",
        "material_status",
        "applicability",
        "execution_authority",
    )
    return {key: copy.deepcopy(item[key]) for key in keys}


def read_action_admission_model_candidates(conn, state, request):
    """List every current model reference without compiling cooldown or energy."""
    request = _validate_catalog_request(request)
    cursor = _decode_catalog_cursor(request["cursor"])
    _require(
        conn.in_transaction,
        "动作接纳目录须在调用者事务内读取",
        "action_admission_transaction",
    )
    line, character_id = state["version_line"], request["character_id"]
    _require(
        line == request["version_line"],
        "动作接纳目录版本线与事务不符",
        "action_admission_catalog_query",
    )
    complete_inventory = _current_fact_inventory(conn, state)
    inventory, refs = _catalog_inventory(complete_inventory, character_id)
    items, valid_fresh = [], {}
    for item_ref in refs:
        raw = read_record(conn, line, "fact", item_ref["id"], item_ref["revision"])
        current = read_current_reviewed_record(
            conn, line, "fact", item_ref["id"], item_ref["revision"]
        )
        _require(
            raw.get("status") != "missing" and current["receipt"] is not None,
            "动作接纳目录当前记录缺失",
            "action_admission_current",
        )
        model_ref = {"id": item_ref["id"], "revision": item_ref["revision"]}
        review_status = raw.get("review", {}).get("status")
        base = {
            "model_ref": model_ref,
            "current_receipt": copy.deepcopy(current["receipt"]),
            "review": {"status": review_status, "reason": current["reason"]},
            "material_status": "not_evaluated",
            "applicability": "declared_only",
            "execution_authority": False,
        }
        try:
            claim = raw["payload"]["claim"]
            validate_model(claim)
            _require(
                raw["payload"]["kind"] == "action_admission_model",
                "动作接纳目录事实种类错误",
                "action_admission_current",
            )
            contract_valid = True
        except (
            ActionAdmissionError,
            ValueError,
            TypeError,
            KeyError,
            RecursionError,
            OverflowError,
        ):
            claim, contract_valid = None, False
        if not contract_valid:
            items.append(
                {
                    **base,
                    "catalog_group": "not_evaluated",
                    "status_reason": "model_contract_invalid",
                    "selection_status": "reference_not_selectable",
                    "target": None,
                    "domain": None,
                    "declared_filter": {
                        "status": "not_evaluated",
                        "matched_paths": [],
                        "mismatched": [],
                        "unknown_input_paths": copy.deepcopy(request["unknown_paths"]),
                    },
                    "display": {
                        key: {"value": None, "status": "not_evaluated"}
                        for key in ("character", "skill")
                    },
                }
            )
            continue
        target = copy.deepcopy(claim["target"])
        domain = {
            "constellations": copy.deepcopy(claim["domain"]["constellations"]),
            "variant": claim["domain"]["variant"],
            "action_type_id": claim["domain"]["action_type_id"],
        }
        declared = _declared_filter(claim, request)
        fresh = (
            raw["subject"] == {"target_type": "character", "target_id": character_id}
            and same(state, claim["snapshot"])
            and same(raw["evidence"], evidence_for(claim))
        )
        if review_status == "revoked":
            group, reason = "revoked", "review_revoked"
        elif review_status != "approved":
            group, reason = "pending", "review_not_approved"
        elif not fresh:
            group, reason = "stale", "snapshot_or_evidence_changed"
        elif declared["status"] == "exact_match":
            group, reason = "exact", None
        elif declared["status"] == "partial_match":
            group, reason = "partial", None
        else:
            group, reason = "not_evaluated", "declared_input_mismatch"
        entry = {
            **base,
            "catalog_group": group,
            "status_reason": reason,
            "selection_status": (
                "reference_selectable_for_fresh_preview"
                if group in ("exact", "partial")
                else "reference_not_selectable"
            ),
            "target": target,
            "domain": domain,
            "declared_filter": declared,
            "display": _catalog_display(conn, line, target),
        }
        items.append(entry)
        if review_status == "approved" and fresh:
            valid_fresh.setdefault(_catalog_competition_key(claim), []).append(entry)
    for competing in valid_fresh.values():
        if len(competing) > 1:
            refs_for_reason = [copy.deepcopy(item["model_ref"]) for item in competing]
            for item in competing:
                item.update(
                    catalog_group="conflict",
                    status_reason="competing_current_models",
                    selection_status="reference_not_selectable",
                    conflict_refs=refs_for_reason,
                )
    items.sort(key=_catalog_sort_key)
    inventory_fingerprint = fingerprint(inventory)
    catalog_fingerprint = fingerprint([_catalog_identity(item) for item in items])
    query_view = {
        key: copy.deepcopy(request[key])
        for key in (
            "contract",
            "version_line",
            "character_id",
            "observed",
            "unknown_paths",
            "view",
            "order",
            "page_size",
        )
    }
    query_fingerprint = fingerprint(query_view)
    filtered = (
        items
        if request["view"] == "all"
        else [item for item in items if item["catalog_group"] == request["view"]]
    )
    keys = [list(_catalog_sort_key(item)) for item in filtered]
    start = 0
    if cursor is not None:
        changed = (
            cursor["runtime_snapshot_id"] != state["snapshot_id"]
            or cursor["admission_inventory_fingerprint"] != inventory_fingerprint
            or cursor["catalog_fingerprint"] != catalog_fingerprint
        )
        if changed:
            _catalog_fail(
                "action_admission_catalog_changed",
                "动作接纳目录库存、审核或运行快照已变化",
            )
        if (
            cursor["query_fingerprint"] != query_fingerprint
            or cursor["view"] != request["view"]
            or cursor["order"] != request["order"]
            or cursor["page_size"] != request["page_size"]
        ):
            _catalog_fail(
                "action_admission_catalog_cursor",
                "动作接纳目录游标与筛选、排序或页大小不一致",
            )
        if cursor["after"] not in keys:
            _catalog_fail(
                "action_admission_catalog_cursor", "动作接纳目录游标位置不存在"
            )
        start = keys.index(cursor["after"]) + 1
    page = filtered[start : start + request["page_size"]]
    has_more = start + len(page) < len(filtered)
    next_cursor = None
    if has_more:
        next_cursor = _encode_catalog_cursor(
            {
                "contract": CATALOG_CURSOR_CONTRACT,
                "runtime_snapshot_id": state["snapshot_id"],
                "admission_inventory_fingerprint": inventory_fingerprint,
                "catalog_fingerprint": catalog_fingerprint,
                "query_fingerprint": query_fingerprint,
                "view": request["view"],
                "order": request["order"],
                "page_size": request["page_size"],
                "after": list(_catalog_sort_key(page[-1])),
            }
        )
    group_counts = {
        group: sum(item["catalog_group"] == group for item in items)
        for group in _CATALOG_GROUPS
    }
    result = {
        "contract": CATALOG_PAGE_CONTRACT,
        "version_line": line,
        "character_id": character_id,
        "runtime_snapshot_id": state["snapshot_id"],
        "admission_inventory_fingerprint": inventory_fingerprint,
        "catalog_fingerprint": catalog_fingerprint,
        "query_fingerprint": query_fingerprint,
        "view": request["view"],
        "order": request["order"],
        "counts": {
            "current_action_admission_facts": len(refs),
            "current_models": len(items),
            "current_fact_heads": len(inventory),
            **group_counts,
            "filtered_models": len(filtered),
            "returned": len(page),
        },
        "items": copy.deepcopy(page),
        "has_more": has_more,
        "next_cursor": next_cursor,
        "material_status": "not_evaluated",
        "applicability": "declared_only",
        "returns_compiled_cooldown_or_energy": False,
        "execution_authority": False,
    }
    try:
        response_bytes = len(dumps(result).encode("utf-8"))
    except (TypeError, ValueError, RecursionError, OverflowError, UnicodeError) as exc:
        raise ActionAdmissionError(
            "action_admission_catalog_budget", "动作接纳目录响应无法安全编码"
        ) from exc
    if response_bytes > MAX_CATALOG_RESPONSE_BYTES:
        _catalog_fail("action_admission_catalog_budget", "动作接纳目录响应超过512KiB")
    return result


def list_action_admission_model_candidates(
    db_path, request, *, version_line=None, snapshot_id=None
):
    request = _validate_catalog_request(request)
    if version_line is not None and version_line != request["version_line"]:
        _catalog_fail(
            "action_admission_catalog_query", "顶层version_line与目录请求不一致",
            {"path": "/version_line", "actual": version_line, "expected": request["version_line"],
             "related_path": "/request/version_line",
             "repair_hint": "此目录当前只支持release。若查询release，把顶层与request.version_line同时设为release；若确需beta，请勿替换资料线冒充同一来源。",
             "recovery": {"action": "correct_arguments", "retry_same_request": False}}
        )
    with _read(db_path, request["version_line"], snapshot_id) as (conn, state):
        return read_action_admission_model_candidates(conn, state, request)


def _competition_inventory(inventory, selected_record):
    selected_key = _target_key(selected_record["payload"]["claim"])
    entries, blocked = [], False
    for item in inventory:
        stored = item["record"]
        entry = copy.deepcopy(item["envelope"])
        review_ready = entry["review_status"] == "approved"
        entry["status"] = "ready" if review_ready else "pending"
        entry["reason"] = None if review_ready else "review_not_approved"
        if entry["fact_kind"] == "action_admission_model":
            claim = stored["payload"]["claim"]
            competing = same(_target_key(claim), selected_key)
            selected = (
                stored["id"] == selected_record["id"]
                and stored["revision"] == selected_record["revision"]
            )
            if selected:
                entry["disposition"] = (
                    "selected_model" if review_ready else "selected_model_unavailable"
                )
                blocked |= not review_ready
            elif competing:
                entry["disposition"] = (
                    "competing_target"
                    if review_ready
                    else "competing_target_unreviewed"
                )
                blocked = True
            else:
                entry["disposition"] = "classified_non_competing"
        else:
            entry["disposition"] = "classified_non_competing"
        entries.append(entry)
    return {
        "contract": "current-action-admission-competitors-v1",
        "complete": True,
        "scope": "all_current_fact_heads_in_version_line",
        "selection_key": selected_key,
        "entries": entries,
    }, blocked


def read_action_admission_profile(
    conn, state, *, selection, variant, action_type_id, model_ref
):
    validate_selection(selection)
    ref(model_ref)
    _require(
        variant == "default" and action_type_id == "action.elemental_burst",
        "动作接纳首片只支持default元素爆发",
        "action_admission_selection",
    )
    _require(
        conn.in_transaction,
        "动作接纳资料须在调用者事务内读取",
        "action_admission_transaction",
    )
    _require(
        state["version_line"] == "release",
        "动作接纳首片只支持release资料线",
        "action_admission_selection",
    )
    complete_inventory = _current_fact_inventory(conn, state)
    _, refs = _catalog_inventory(complete_inventory, selection["character_id"])
    current = read_current_reviewed_record(
        conn, state["version_line"], "fact", model_ref["id"], model_ref["revision"]
    )
    result = {
        "contract": PROFILE_CONTRACT,
        "status": "pending",
        "reason": current["reason"],
        "selection": copy.deepcopy(selection),
        "variant": variant,
        "action_type_id": action_type_id,
        "snapshot": copy.deepcopy(state),
        "model_ref": copy.deepcopy(model_ref),
        "model_receipt": copy.deepcopy(current["receipt"]),
        "model_record": None,
        "resource_profile": None,
        "field_authority": copy.deepcopy(FIELD_AUTHORITY),
        "compiled_values": None,
        "declared_not_verified": [],
        "competitor_inventory": None,
        "current_head_fingerprint": None,
        "source_fingerprint": None,
        "execution_authority": False,
    }
    if current["status"] != "ready":
        return _finalize_profile(result)
    model = _record(current)
    _require(
        model["payload"]["kind"] == "action_admission_model"
        and model["subject"]
        == {"target_type": "character", "target_id": selection["character_id"]},
        "动作接纳模型类型或主体不符",
        "action_admission_selection",
    )
    claim = model["payload"]["claim"]
    validate_model(claim)
    result["model_record"] = model
    _require(
        same(state, claim["snapshot"]),
        "动作接纳模型快照已变化",
        "action_admission_stale",
    )
    _require(
        model["evidence"] == evidence_for(claim),
        "动作接纳模型证据不完整",
        "action_admission_stale",
    )
    if not selection_matches(claim, selection, variant, action_type_id):
        result["reason"] = "action_admission_model_out_of_scope"
        return _finalize_profile(result)
    _require(
        {model_ref["id"]: model_ref["revision"]}.items()
        <= {item["id"]: item["revision"] for item in refs}.items(),
        "动作接纳模型不在完整当前库存",
        "action_admission_current",
    )
    inventory, blocked = _competition_inventory(complete_inventory, model)
    result["competitor_inventory"] = inventory
    result["current_head_fingerprint"] = fingerprint(inventory)
    if blocked:
        result["reason"] = "action_admission_competitor_unresolved"
        return _finalize_profile(result)

    profile = read_action_resource_profile(
        conn,
        state,
        character_id=selection["character_id"],
        slot=selection["slot"],
        skill_depot_id=selection["skill_depot_id"],
    )
    result["resource_profile"] = profile
    resolved = profile["selection"]
    _require(
        resolved["character_id"] == selection["character_id"]
        and resolved["skill_depot_id"] == selection["skill_depot_id"]
        and resolved["skill_id"] == selection["skill_id"]
        and "burst" in resolved["matching_slots"],
        "动作接纳fresh技能身份不符",
        "action_admission_stale",
    )
    _require(
        profile["resource_fingerprint"]
        == claim["resource_binding"]["resource_fingerprint"],
        "动作接纳基础资源指纹已变化",
        "action_admission_stale",
    )
    projection = profile["projection"]
    required = (
        "base_cooldown_seconds",
        "base_charge_count",
        "base_energy_cost",
        "cost_element",
    )
    _require(
        all(projection[key]["status"] == "ready" for key in required)
        and profile["descriptions"]["status"] == "ready",
        "动作接纳基础资源或中文原文未完整就绪",
        "action_admission_resource",
    )
    cooldown = projection["base_cooldown_seconds"]["value"]
    charges = projection["base_charge_count"]["value"]
    energy = projection["base_energy_cost"]["value"]
    _require(
        type(cooldown) in (int, float)
        and not isinstance(cooldown, bool)
        and cooldown >= 0,
        "动作接纳基础冷却无效",
        "action_admission_resource",
    )
    _require(
        type(charges) is int and charges == 1,
        "动作接纳首片只支持基础单次数",
        "action_admission_resource",
    )
    _require(
        type(energy) in (int, float) and not isinstance(energy, bool) and energy > 0,
        "动作接纳首片只支持正值固定爆发耗能",
        "action_admission_resource",
    )
    binding = claim["resource_binding"]
    values = {
        "cooldown": {
            "group": binding["cooldown_group"],
            "duration": cooldown,
            "start": binding["cooldown_start"],
            "charges": charges,
            "recovery": binding["cooldown_recovery"],
        },
        "energy": {
            "required": energy,
            "amount": energy,
            "delivery": binding["energy_delivery"],
        },
    }
    source_material = {
        "model_receipt": current["receipt"],
        "model_payload_fingerprint": fingerprint(model),
        "resource_fingerprint": profile["resource_fingerprint"],
        "current_head_fingerprint": result["current_head_fingerprint"],
        "selection": selection,
        "variant": variant,
        "action_type_id": action_type_id,
        "compiled_values": values,
        "field_authority": FIELD_AUTHORITY,
        "declared_not_verified": claim["unknowns"],
    }
    result.update(
        status="ready",
        reason=None,
        compiled_values=values,
        declared_not_verified=copy.deepcopy(claim["unknowns"]),
        source_fingerprint=fingerprint(source_material),
    )
    return _finalize_profile(result)


def get_action_admission_profile(
    db_path,
    version_line="release",
    *,
    selection,
    variant,
    action_type_id,
    model_ref,
    snapshot_id=None,
):
    validate_selection(selection)
    ref(model_ref)
    with _read(db_path, version_line, snapshot_id) as (conn, state):
        return read_action_admission_profile(
            conn,
            state,
            selection=selection,
            variant=variant,
            action_type_id=action_type_id,
            model_ref=model_ref,
        )


__all__ = [
    "CATALOG_ORDER",
    "CATALOG_PAGE_CONTRACT",
    "CATALOG_QUERY_CONTRACT",
    "DEFAULT_CATALOG_PAGE_SIZE",
    "MAX_CATALOG_PAGE_SIZE",
    "ActionAdmissionError",
    "capabilities",
    "get_action_admission_profile",
    "list_action_admission_model_candidates",
    "read_action_admission_model_candidates",
    "read_action_admission_profile",
]
