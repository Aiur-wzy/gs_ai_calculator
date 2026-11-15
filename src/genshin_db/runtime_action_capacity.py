"""Current, bounded complete capacity admission on the caller's SQLite snapshot."""

from __future__ import annotations

import base64
import binascii
import copy
import json
import re

from genshin_db.runtime_action_sources import _delta, fresh_delta_material
from genshin_db.runtime_capacity_delta import CapacityError
from genshin_db.runtime_catalog import dumps, fingerprint
from genshin_db.runtime_fact_inventory import (
    MAX_CLASSIFICATION_BYTES,
    MAX_CLASSIFICATION_FACTS,
    MAX_CLASSIFICATION_RECORD_BYTES,
    read_current_fact_inventory,
)
from genshin_db.runtime_queries import _Nodes, _read, _row
from genshin_db.semantic_store import (
    _COLUMNS,
    HEAD_TABLE,
    TABLES,
    _decode,
    read_current_reviewed_record,
    read_record,
    validate_component,
)
from genshin_runtime.action_capacity_model import (
    MAX_FACTS,
    PROFILE_CONTRACT,
    evidence_for,
    ref,
    same,
    selection_matches,
    validate_context,
    validate_model,
    validate_selection,
    verify_material,
)
from genshin_runtime.action_root_semantics import bounded

_PUBLIC_FIELDS = (
    "id",
    "revision",
    "subject",
    "payload",
    "evidence",
    "applicability",
    "review",
)
_KNOWN_OTHER_KINDS = (
    "related_action_root",
    "action_admission_model",
    "rule_template",
    "parameter_role",
    "execution_requirement",
    "execution_qualification",
    "attribute_read",
    "attribute_output",
    "attribute_sample",
)
_CAPACITY_KINDS = ("action_capacity_delta", "action_capacity_model")
CATALOG_QUERY_CONTRACT = "action-capacity-model-catalog-query-v1"
CATALOG_VIEWS = ("all", "matching", "mismatching", "not_evaluated")
CATALOG_PAGE_CONTRACT = "action-capacity-model-catalog-page-v1"
CATALOG_CURSOR_CONTRACT = "action-capacity-model-catalog-cursor-v1"
MAX_CATALOG_PAGE_SIZE = 32
DEFAULT_CATALOG_PAGE_SIZE = 16
MAX_CATALOG_QUERY_BYTES = 64 * 1024
MAX_CATALOG_CURSOR_BYTES = 8192
MAX_CATALOG_RESPONSE_BYTES = 512 * 1024
_SHA256 = re.compile(r"[0-9a-f]{64}")
_CURSOR_TEXT = re.compile(r"[A-Za-z0-9_-]+")
_CATALOG_PATHS = (
    "artifacts",
    "context",
    "progression.ascension",
    "progression.base_level",
    "progression.character_level",
    "progression.constellation",
    "target.skill_depot_id",
    "target.skill_id",
    "target.variant",
    "team",
    "weapon.id",
    "weapon.level",
    "weapon.refinement",
)


def capabilities():
    return {
        "contract": PROFILE_CONTRACT,
        "max_current_facts": MAX_FACTS,
        "owned_fields": ["cooldown.charges"],
        "qualification": "reviewed_game_resource_model",
        "native_dispatch": "unproven",
        "read_only": True,
        "initial_state": "explicit_initial_cooldowns_required",
        "variants": ["default", "tap", "hold"],
        "classification": {
            "scope": "all_current_facts_in_same_version_line_and_read_transaction",
            "max_facts": MAX_CLASSIFICATION_FACTS,
            "max_record_bytes": MAX_CLASSIFICATION_RECORD_BYTES,
            "max_total_bytes": MAX_CLASSIFICATION_BYTES,
            "byte_unit": "stored_utf8",
            "database_encoding": "UTF-8_required",
            "overflow_code": "capacity_classification_budget",
            "sql_vm_steps": "not_bounded_here",
            "exclusion": "complete_valid_record_before_kind_or_actor_exclusion",
        },
        "model_catalog": {
            "query_contract": CATALOG_QUERY_CONTRACT,
            "page_contract": CATALOG_PAGE_CONTRACT,
            "cursor_contract": CATALOG_CURSOR_CONTRACT,
            "max_page_size": MAX_CATALOG_PAGE_SIZE,
            "max_response_bytes": MAX_CATALOG_RESPONSE_BYTES,
            "execution_authority": False,
            "material_status": "not_evaluated",
            "applicability": "not_evaluated",
        },
    }


def _require(value, message, code="capacity_material"):
    if not value:
        raise CapacityError(code, message)


def _record(current):
    return {key: copy.deepcopy(current["record"][key]) for key in _PUBLIC_FIELDS}


def _classified_capacity_refs(conn, line, character_id):
    """Validate all bounded current envelopes before declaring any irrelevant.

    SQL projects only bounded head identifiers and storage metadata. The record
    bytes are charged before a full row is fetched or JSON is decoded. This is
    an application data budget, not a claim about SQLite's internal VM work.
    """
    inventory = read_current_fact_inventory(
        conn,
        line,
        columns=_COLUMNS,
        decode=_decode,
        require=_require,
        current_code="capacity_current",
        budget_code="capacity_classification_budget",
        max_facts=MAX_CLASSIFICATION_FACTS,
        max_record_bytes=MAX_CLASSIFICATION_RECORD_BYTES,
        max_total_bytes=MAX_CLASSIFICATION_BYTES,
        known_fact_kinds=(*_KNOWN_OTHER_KINDS, *_CAPACITY_KINDS),
    )
    refs = []
    for item in inventory:
        record = item["record"]
        if item["envelope"]["fact_kind"] in _CAPACITY_KINDS and record["subject"] == {
            "target_type": "character",
            "target_id": character_id,
        }:
            refs.append({"id": record["id"], "revision": record["revision"]})
            _require(
                len(refs) <= MAX_FACTS,
                "角色当前容量事实超过32条，不能截断后执行",
                "capacity_budget",
            )
    return refs


def current_capacity_refs(conn, line, character_id):
    """Only completely valid unrelated records may leave the candidate inventory."""
    _require(conn.in_transaction, "次数资料须在同一读取事务", "capacity_transaction")
    _require(
        conn.execute("PRAGMA encoding").fetchone()[0] == "UTF-8",
        "当前事实字节分类仅支持 UTF-8 数据库",
        "capacity_current",
    )
    _require(
        validate_component(conn, line)["status"] == "ready",
        "语义目录未就绪",
        "capacity_current",
    )
    classified = _classified_capacity_refs(conn, line, character_id)
    join = (
        f"FROM {HEAD_TABLE} h LEFT JOIN {TABLES['fact']} f ON f.version_line=h.version_line "
        "AND f.record_id=h.record_id AND f.revision=h.revision WHERE h.version_line=? AND h.record_kind='fact' "
    )
    rows = conn.execute(
        "SELECT h.record_id,h.revision "
        + join
        + "AND json_extract(f.subject_json,'$.target_type')='character' "
        "AND json_extract(f.subject_json,'$.target_id')=? "
        "AND json_extract(f.payload_json,'$.kind') IN ('action_capacity_delta','action_capacity_model') "
        "ORDER BY h.record_id LIMIT ?",
        (line, character_id, MAX_FACTS + 1),
    ).fetchall()
    _require(
        len(rows) <= MAX_FACTS,
        "角色当前容量事实超过32条，不能截断后执行",
        "capacity_budget",
    )
    _require(
        len({row[0] for row in rows}) == len(rows),
        "当前容量事实身份重复",
        "capacity_current",
    )
    result = [{"id": row[0], "revision": row[1]} for row in rows]
    _require(
        result == classified, "当前容量事实定位与完整分类不一致", "capacity_current"
    )
    return result


def _catalog_fail(code, message):
    raise CapacityError(code, message)


def _catalog_shape(value):
    """Reject deep/large/non-JSON queries before copying or normalizing them."""
    stack, nodes = [(value, 0)], 0
    while stack:
        item, depth = stack.pop()
        nodes += 1
        if nodes > 512 or depth > 16:
            _catalog_fail("capacity_catalog_query", "目录请求结构超过有界预算")
        if type(item) is dict:
            if len(item) > 32:
                _catalog_fail("capacity_catalog_query", "目录请求对象字段过多")
            for key, child in item.items():
                if type(key) is not str or not 1 <= len(key) <= 128:
                    _catalog_fail("capacity_catalog_query", "目录请求字段名无效")
                stack.append((child, depth + 1))
        elif type(item) is list:
            if len(item) > 64:
                _catalog_fail("capacity_catalog_query", "目录请求数组过长")
            stack.extend((child, depth + 1) for child in item)
        elif type(item) is str:
            if len(item) > MAX_CATALOG_CURSOR_BYTES:
                _catalog_fail("capacity_catalog_query", "目录请求字符串过长")
        elif item is None or type(item) in (bool, int):
            continue
        else:
            _catalog_fail("capacity_catalog_query", "目录请求必须为有限JSON类型")
    try:
        size = len(dumps(value).encode("utf-8"))
    except (TypeError, ValueError, RecursionError, OverflowError, UnicodeError) as exc:
        raise CapacityError(
            "capacity_catalog_query", "目录请求不是有限规范JSON"
        ) from exc
    if size > MAX_CATALOG_QUERY_BYTES:
        _catalog_fail("capacity_catalog_query", "目录请求超过字节预算")


def _catalog_int(value, name, low=1, high=2**53 - 1):
    if type(value) is not int or not low <= value <= high:
        _catalog_fail("capacity_catalog_query", f"{name}必须为{low}–{high}的精确整数")


def _catalog_group(value, allowed, name):
    if type(value) is not dict or not set(value) <= allowed:
        _catalog_fail("capacity_catalog_query", f"{name}字段无效")


def _observed_paths(observed):
    paths = []
    for group in ("target", "progression", "weapon"):
        paths.extend(f"{group}.{key}" for key in observed.get(group, {}))
    paths.extend(key for key in ("artifacts", "team", "context") if key in observed)
    return sorted(paths)


def _validate_catalog_request(request):
    _catalog_shape(request)
    required = {
        "contract",
        "version_line",
        "character_id",
        "observed",
        "unknown_paths",
        "view",
        "page_size",
        "cursor",
    }
    if (
        type(request) is not dict
        or set(request) != required
        or request["contract"] != CATALOG_QUERY_CONTRACT
    ):
        _catalog_fail("capacity_catalog_query", "容量模型目录请求合同或字段无效")
    line = request["version_line"]
    if type(line) is not str or not re.fullmatch(r"[a-z][a-z0-9_-]{0,31}", line):
        _catalog_fail("capacity_catalog_query", "version_line无效")
    _catalog_int(request["character_id"], "character_id")
    observed = request["observed"]
    _catalog_group(
        observed,
        {"target", "progression", "weapon", "artifacts", "team", "context"},
        "observed",
    )
    if "target" in observed:
        _catalog_group(
            observed["target"],
            {"skill_depot_id", "skill_id", "variant"},
            "observed.target",
        )
        for key in ("skill_depot_id", "skill_id"):
            if key in observed["target"]:
                _catalog_int(observed["target"][key], "target." + key)
        if "variant" in observed["target"] and observed["target"]["variant"] not in (
            "default",
            "tap",
            "hold",
        ):
            _catalog_fail("capacity_catalog_query", "target.variant枚举无效")
    if "progression" in observed:
        _catalog_group(
            observed["progression"],
            {"character_level", "ascension", "constellation", "base_level"},
            "observed.progression",
        )
        limits = {
            "character_level": (1, 100),
            "ascension": (0, 6),
            "constellation": (0, 6),
            "base_level": (1, 10),
        }
        for key, value in observed["progression"].items():
            _catalog_int(value, "progression." + key, *limits[key])
    if "weapon" in observed:
        _catalog_group(
            observed["weapon"], {"id", "level", "refinement"}, "observed.weapon"
        )
        limits = {"id": (1, 2**53 - 1), "level": (1, 100), "refinement": (1, 5)}
        for key, value in observed["weapon"].items():
            _catalog_int(value, "weapon." + key, *limits[key])
    if "artifacts" in observed and observed["artifacts"] not in (
        "all_empty",
        "not_all_empty",
    ):
        _catalog_fail("capacity_catalog_query", "artifacts枚举无效")
    if "team" in observed and observed["team"] not in ("self_only", "not_self_only"):
        _catalog_fail("capacity_catalog_query", "team枚举无效")
    if "context" in observed:
        try:
            validate_context(observed["context"])
        except (ValueError, TypeError, KeyError, RecursionError, OverflowError) as exc:
            raise CapacityError("capacity_catalog_query", "context合同无效") from exc
    unknown = request["unknown_paths"]
    if (
        type(unknown) is not list
        or any(type(path) is not str or path not in _CATALOG_PATHS for path in unknown)
        or unknown != sorted(set(unknown))
    ):
        _catalog_fail(
            "capacity_catalog_query", "unknown_paths必须是有序、唯一、受支持路径"
        )
    expected_unknown = sorted(set(_CATALOG_PATHS) - set(_observed_paths(observed)))
    if unknown != expected_unknown:
        _catalog_fail("capacity_catalog_query", "unknown_paths必须精确描述未提供字段")
    if request["view"] not in CATALOG_VIEWS:
        _catalog_fail("capacity_catalog_query", "view枚举无效")
    _catalog_int(request["page_size"], "page_size", 1, MAX_CATALOG_PAGE_SIZE)
    if request["cursor"] is not None and (
        type(request["cursor"]) is not str
        or not 1 <= len(request["cursor"]) <= MAX_CATALOG_CURSOR_BYTES
    ):
        _catalog_fail("capacity_catalog_cursor", "目录游标长度无效")
    return copy.deepcopy(request)


def _cursor_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            _catalog_fail("capacity_catalog_cursor", "目录游标含重复字段")
        result[key] = value
    return result


def _decode_catalog_cursor(value):
    if value is None:
        return None
    if not _CURSOR_TEXT.fullmatch(value):
        _catalog_fail("capacity_catalog_cursor", "目录游标编码无效")
    try:
        raw = base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))
        if len(raw) > MAX_CATALOG_CURSOR_BYTES:
            _catalog_fail("capacity_catalog_cursor", "目录游标超过字节预算")
        if base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=") != value:
            _catalog_fail("capacity_catalog_cursor", "目录游标不是规范base64url编码")
        wrapper = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=_cursor_object,
            parse_constant=lambda _: _catalog_fail(
                "capacity_catalog_cursor", "目录游标含非有限数值"
            ),
        )
    except CapacityError:
        raise
    except (
        binascii.Error,
        UnicodeError,
        json.JSONDecodeError,
        ValueError,
        TypeError,
    ) as exc:
        raise CapacityError("capacity_catalog_cursor", "目录游标无法解码") from exc
    if (
        type(wrapper) is not dict
        or set(wrapper) != {"payload", "sha256"}
        or fingerprint(wrapper["payload"]) != wrapper["sha256"]
    ):
        _catalog_fail("capacity_catalog_cursor", "目录游标完整性校验失败")
    payload = wrapper["payload"]
    fields = {
        "contract",
        "runtime_snapshot_id",
        "capacity_inventory_fingerprint",
        "catalog_fingerprint",
        "query_fingerprint",
        "page_size",
        "after",
    }
    if (
        type(payload) is not dict
        or set(payload) != fields
        or payload["contract"] != CATALOG_CURSOR_CONTRACT
    ):
        _catalog_fail("capacity_catalog_cursor", "目录游标合同无效")
    for key in (
        "capacity_inventory_fingerprint",
        "catalog_fingerprint",
        "query_fingerprint",
    ):
        if type(payload[key]) is not str or not _SHA256.fullmatch(payload[key]):
            _catalog_fail("capacity_catalog_cursor", "目录游标指纹无效")
    if (
        type(payload["runtime_snapshot_id"]) is not str
        or not 1 <= len(payload["runtime_snapshot_id"]) <= 128
    ):
        _catalog_fail("capacity_catalog_cursor", "目录游标快照无效")
    if (
        type(payload["page_size"]) is not int
        or not 1 <= payload["page_size"] <= MAX_CATALOG_PAGE_SIZE
    ):
        _catalog_fail("capacity_catalog_cursor", "目录游标页大小无效")
    after = payload["after"]
    if (
        type(after) is not list
        or len(after) != 6
        or type(after[0]) is not int
        or after[0] not in (0, 1)
        or any(type(after[index]) is not int for index in (1, 2, 3, 5))
        or any(after[index] < 0 for index in (1, 2, 3))
        or after[5] < 1
        or type(after[4]) is not str
        or not re.fullmatch(r"[a-z0-9][a-z0-9._-]{0,79}", after[4])
    ):
        _catalog_fail("capacity_catalog_cursor", "目录游标位置无效")
    if dumps(wrapper).encode("utf-8") != raw:
        _catalog_fail("capacity_catalog_cursor", "目录游标不是规范编码")
    return payload


def _encode_catalog_cursor(payload):
    wrapper = {"payload": payload, "sha256": fingerprint(payload)}
    return (
        base64.urlsafe_b64encode(dumps(wrapper).encode("utf-8"))
        .decode("ascii")
        .rstrip("=")
    )


def _display_name(conn, sql, params):
    rows = conn.execute(sql + " LIMIT 2", params).fetchall()
    if not rows:
        return {"value": None, "status": "missing"}
    if len(rows) != 1:
        return {"value": None, "status": "ambiguous"}
    value = rows[0][0]
    if type(value) is str and value.strip():
        return {"value": value, "status": "present"}
    return {"value": None, "status": "missing" if value is None else "invalid"}


def _catalog_display(conn, line, target, weapon):
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
        "weapon": _display_name(
            conn,
            "SELECT name FROM weapon WHERE version_line=? AND id=?",
            (line, weapon["id"]),
        ),
    }


def _declared_values(claim):
    target, domain = claim["target"], claim["domain"]
    return {
        "target.skill_depot_id": target["skill_depot_id"],
        "target.skill_id": target["skill_id"],
        "target.variant": domain["variant"],
        "progression.character_level": domain["character_level"],
        "progression.ascension": domain["ascension"],
        "progression.constellation": domain["constellations"],
        "progression.base_level": domain["base_level"],
        "weapon.id": domain["weapon"]["id"],
        "weapon.level": domain["weapon"]["level"],
        "weapon.refinement": domain["weapon"]["refinement"],
        "artifacts": domain["artifacts"],
        "team": domain["team"],
        "context": domain["context"],
    }


def _observed_values(observed):
    result = {}
    for group in ("target", "progression", "weapon"):
        result.update(
            {f"{group}.{key}": value for key, value in observed.get(group, {}).items()}
        )
    result.update(
        {
            key: observed[key]
            for key in ("artifacts", "team", "context")
            if key in observed
        }
    )
    return result


def _compare_declared_domain(claim, request):
    expected, actual = _declared_values(claim), _observed_values(request["observed"])
    matched, mismatched = [], []
    for path in sorted(actual):
        wanted, got = expected[path], actual[path]
        agrees = (
            got in wanted if path == "progression.constellation" else same(got, wanted)
        )
        if agrees:
            matched.append(path)
        else:
            mismatched.append(
                {
                    "path": path,
                    "expected": copy.deepcopy(wanted),
                    "actual": copy.deepcopy(got),
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


def _catalog_sort_key(item):
    target, domain = item.get("target"), item.get("domain")
    if target is None or domain is None:
        return (1, 0, 0, 0, item["model_ref"]["id"], item["model_ref"]["revision"])
    return (
        0,
        target["skill_depot_id"],
        target["skill_id"],
        domain["weapon"]["id"],
        item["model_ref"]["id"],
        item["model_ref"]["revision"],
    )


def _catalog_identity(item):
    return {
        key: copy.deepcopy(item[key])
        for key in (
            "model_ref",
            "current_receipt",
            "review",
            "selection_status",
            "target",
            "domain",
            "reviewed_constellations",
            "display",
            "material_status",
            "applicability",
            "execution_authority",
        )
    }


def read_action_capacity_model_candidates(conn, state, request):
    """List current model references without materializing or admitting them."""
    request = _validate_catalog_request(request)
    cursor = _decode_catalog_cursor(request["cursor"])
    _require(
        conn.in_transaction, "容量模型目录须在调用者事务内读取", "capacity_transaction"
    )
    line, character_id = state["version_line"], request["character_id"]
    _require(
        line == request["version_line"],
        "目录版本线与读取事务不符",
        "capacity_catalog_query",
    )
    refs = current_capacity_refs(conn, line, character_id)
    inventory, items = [], []
    for item_ref in refs:
        raw = read_record(conn, line, "fact", item_ref["id"], item_ref["revision"])
        current = read_current_reviewed_record(
            conn, line, "fact", item_ref["id"], item_ref["revision"]
        )
        _require(
            raw.get("status") != "missing" and current["receipt"] is not None,
            "目录当前记录在同一事务中缺失",
            "capacity_current",
        )
        kind = raw["payload"]["kind"]
        inventory.append(
            {
                "id": item_ref["id"],
                "revision": item_ref["revision"],
                "kind": kind,
                "head_updated_at": current["receipt"]["head_updated_at"],
                "payload_fingerprint": current["receipt"]["payload_fingerprint"],
                "review_fingerprint": current["receipt"]["review_fingerprint"],
            }
        )
        if kind != "action_capacity_model":
            continue
        model_ref = {"id": item_ref["id"], "revision": item_ref["revision"]}
        review = {"status": current["review_status"], "reason": current["reason"]}
        base = {
            "model_ref": model_ref,
            "current_receipt": copy.deepcopy(current["receipt"]),
            "review": review,
            "material_status": "not_evaluated",
            "applicability": "not_evaluated",
            "execution_authority": False,
        }
        try:
            claim = raw["payload"]["claim"]
            validate_model(claim)
        except (ValueError, TypeError, KeyError, RecursionError, OverflowError):
            items.append(
                {
                    **base,
                    "selection_status": "model_contract_invalid",
                    "target": None,
                    "domain": None,
                    "reviewed_constellations": [],
                    "declared_filter": {
                        "status": "not_evaluated",
                        "matched_paths": [],
                        "mismatched": [],
                        "unknown_input_paths": copy.deepcopy(request["unknown_paths"]),
                    },
                    "display": {
                        key: {"value": None, "status": "not_evaluated"}
                        for key in ("character", "skill", "weapon")
                    },
                }
            )
            continue
        target = {
            "character_id": claim["target"]["character_id"],
            "skill_depot_id": claim["target"]["skill_depot_id"],
            "skill_id": claim["target"]["skill_id"],
            "variant": claim["domain"]["variant"],
        }
        domain = {
            key: copy.deepcopy(claim["domain"][key])
            for key in (
                "constellations",
                "character_level",
                "ascension",
                "base_level",
                "weapon",
                "artifacts",
                "team",
                "context",
            )
        }
        selection_status = (
            "reference_selectable_for_fresh_preview"
            if current["status"] == "ready"
            else "review_not_approved"
        )
        items.append(
            {
                **base,
                "selection_status": selection_status,
                "target": target,
                "domain": domain,
                "reviewed_constellations": [
                    case["constellation"] for case in claim["reviewed_cases"]
                ],
                "declared_filter": _compare_declared_domain(claim, request),
                "display": _catalog_display(conn, line, target, domain["weapon"]),
            }
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
            "page_size",
        )
    }
    query_fingerprint = fingerprint(query_view)
    view = request["view"]
    filtered = [
        item
        for item in items
        if (
            view == "all"
            or view == "matching"
            and item["declared_filter"]["status"] in ("exact_match", "partial_match")
            or view == "mismatching"
            and item["declared_filter"]["status"] == "mismatch"
            or view == "not_evaluated"
            and item["declared_filter"]["status"] == "not_evaluated"
        )
    ]
    keys = [list(_catalog_sort_key(item)) for item in filtered]
    start = 0
    if cursor is not None:
        changed = (
            cursor["runtime_snapshot_id"] != state["snapshot_id"]
            or cursor["capacity_inventory_fingerprint"] != inventory_fingerprint
            or cursor["catalog_fingerprint"] != catalog_fingerprint
        )
        if changed:
            _catalog_fail(
                "capacity_catalog_changed",
                "目录库存、审核、显示或运行快照已变化，请从第一页重读",
            )
        if (
            cursor["query_fingerprint"] != query_fingerprint
            or cursor["page_size"] != request["page_size"]
        ):
            _catalog_fail("capacity_catalog_cursor", "目录游标与本次查询不一致")
        if cursor["after"] not in keys:
            _catalog_fail("capacity_catalog_cursor", "目录游标位置不存在")
        start = keys.index(cursor["after"]) + 1
    page = filtered[start : start + request["page_size"]]
    has_more = start + len(page) < len(filtered)
    next_cursor = None
    if has_more:
        payload = {
            "contract": CATALOG_CURSOR_CONTRACT,
            "runtime_snapshot_id": state["snapshot_id"],
            "capacity_inventory_fingerprint": inventory_fingerprint,
            "catalog_fingerprint": catalog_fingerprint,
            "query_fingerprint": query_fingerprint,
            "page_size": request["page_size"],
            "after": list(_catalog_sort_key(page[-1])),
        }
        next_cursor = _encode_catalog_cursor(payload)
    result = {
        "contract": CATALOG_PAGE_CONTRACT,
        "version_line": line,
        "character_id": character_id,
        "runtime_snapshot_id": state["snapshot_id"],
        "capacity_inventory_fingerprint": inventory_fingerprint,
        "catalog_fingerprint": catalog_fingerprint,
        "query_fingerprint": query_fingerprint,
        "counts": {
            "current_capacity_facts": len(refs),
            "current_models": len(items),
            "filtered_models": len(filtered),
            "returned": len(page),
        },
        "items": copy.deepcopy(page),
        "has_more": has_more,
        "next_cursor": next_cursor,
        "material_status": "not_evaluated",
        "applicability": "not_evaluated",
        "execution_authority": False,
    }
    try:
        response_bytes = len(dumps(result).encode("utf-8"))
    except (TypeError, ValueError, RecursionError, OverflowError, UnicodeError) as exc:
        raise CapacityError("capacity_catalog_budget", "目录响应无法安全编码") from exc
    if response_bytes > MAX_CATALOG_RESPONSE_BYTES:
        _catalog_fail("capacity_catalog_budget", "目录响应超过512KiB预算")
    return result


def list_action_capacity_model_candidates(
    db_path, request, *, version_line=None, snapshot_id=None
):
    request = _validate_catalog_request(request)
    if version_line is not None and version_line != request["version_line"]:
        _catalog_fail(
            "capacity_catalog_query",
            "顶层version_line必须与目录request.version_line一致",
        )
    # Cursor freshness is checked against both the runtime state and semantic
    # receipts after this single read transaction is open.
    with _read(db_path, request["version_line"], snapshot_id) as (conn, state):
        return read_action_capacity_model_candidates(conn, state, request)


def fresh_model_material(conn, state, record):
    claim = record["payload"]["claim"]
    validate_model(claim)
    _require(same(state, claim["snapshot"]), "容量模型快照已变化", "capacity_stale")
    _require(same(record["evidence"], evidence_for(claim)), "容量模型完整证据不符")
    nodes = _Nodes(conn, state["version_line"])
    raw_sources, sources = {}, []
    for expected in claim["materials"]["sources"]:
        origin, raw = nodes.source(expected["source_path"])
        actual = {
            "id": expected["id"],
            "source_path": expected["source_path"],
            "source_sha256": origin["source_sha256"],
            "canonical_sha256": fingerprint(raw),
        }
        _require(
            same(actual, expected), "容量完整来源与受审材料不一致", "capacity_stale"
        )
        raw_sources[expected["source_path"]] = raw
        sources.append(actual)
    material_nodes = []
    for expected in claim["materials"]["nodes"]:
        row = _row(conn, state["version_line"], expected["definition_id"])
        raw, origin = nodes.node(row)
        actual = {
            "id": expected["id"],
            "definition_id": row["definition_id"],
            "definition_kind": row["definition_kind"],
            "source_path": row["source_path"],
            "source_sha256": origin["source_sha256"],
            "json_pointer": row["json_pointer"],
            "node_sha256": fingerprint(raw),
        }
        _require(same(actual, expected), "容量索引节点与原节点不一致", "capacity_stale")
        material_nodes.append({"binding": actual, "raw": raw})
    texts = []
    for item in claim["text_inventory"]:
        source = raw_sources[item["source_path"]]
        _require(type(source) is dict, "原文原源不是对象")
        key = str(item["text_id"])
        present = key in source
        _require(
            present == (item["status"] == "present"),
            "原文presence已改变",
            "capacity_stale",
        )
        value = source[key] if present else None
        _require(not present or type(value) is str, "原文不是字符串")
        texts.append({"binding": copy.deepcopy(item), "text": value})
    material = {
        "snapshot": state,
        "sources": sources,
        "nodes": material_nodes,
        "talent_parent": raw_sources[claim["materials"]["talent_parent"]],
        "texts": texts,
    }
    verify_material(claim, material)
    return material


def read_action_capacity_profile(conn, state, *, selection, context, model_ref):
    validate_selection(selection, context)
    ref(model_ref)
    _require(
        conn.in_transaction, "容量资料须在调用者事务内读取", "capacity_transaction"
    )
    line = state["version_line"]
    refs = current_capacity_refs(conn, line, selection["character_id"])
    current = read_current_reviewed_record(
        conn, line, "fact", model_ref["id"], model_ref["revision"]
    )
    result = {
        "contract": PROFILE_CONTRACT,
        "status": "pending",
        "reason": current["reason"],
        "capacity": None,
        "selection": copy.deepcopy(selection),
        "context": copy.deepcopy(context),
        "snapshot": copy.deepcopy(state),
        "model_ref": copy.deepcopy(model_ref),
        "model_receipt": current["receipt"],
        "model_record": None,
        "model_material": None,
        "delta_materials": [],
        "other_model_materials": [],
        "competitor_inventory": None,
        "native_dispatch": "unproven",
        "execution_authority": False,
    }
    if current["status"] != "ready":
        return result
    model = _record(current)
    _require(
        model["payload"]["kind"] == "action_capacity_model"
        and model["subject"]
        == {"target_type": "character", "target_id": selection["character_id"]},
        "模型类型或角色不符",
    )
    claim = model["payload"]["claim"]
    result.update(
        model_record=model, model_material=fresh_model_material(conn, state, model)
    )
    if not selection_matches(claim, selection, context):
        result["reason"] = "capacity_model_out_of_scope"
        return result
    required = {x["id"]: x["revision"] for x in claim["delta_refs"]}
    _require(
        {model_ref["id"], *required} <= {x["id"] for x in refs},
        "模型或必需增量不在完整当前库存",
        "capacity_current",
    )
    entries, deltas, seen_ops, blocked = [], [], set(), False
    for item in refs:
        cur = read_current_reviewed_record(
            conn, line, "fact", item["id"], item["revision"]
        )
        entry = {
            "id": item["id"],
            "revision": item["revision"],
            "receipt": cur["receipt"],
            "status": cur["status"],
            "reason": cur["reason"],
            "disposition": "unresolved_current_fact",
            "record": None,
            "projection": None,
        }
        if cur["status"] != "ready":
            blocked = True
        else:
            record = _record(cur)
            entry["record"] = record
            kind = record["payload"]["kind"]
            if item["id"] == model_ref["id"]:
                _require(
                    item["revision"] == model_ref["revision"] and same(record, model),
                    "模型头在库存中不一致",
                )
                entry["disposition"] = "selected_model"
            elif kind == "action_capacity_delta":
                projected = _delta(conn, state, record, selection["constellation"])
                entry["projection"] = projected
                inv, row, declaration = fresh_delta_material(conn, state, record)
                result["delta_materials"].append(
                    {
                        "record": record,
                        "inventory": inv,
                        "row": row,
                        "declaration": declaration,
                    }
                )
                sc = record["payload"]["claim"]["source_claim"]
                target_same = (
                    sc["skill_depot_id"] == selection["skill_depot_id"]
                    and sc["target_skill_id"] == selection["skill_id"]
                )
                if item["id"] in required:
                    _require(
                        item["revision"] == required[item["id"]] and target_same,
                        "必需增量当前修订/目标变化",
                        "capacity_current",
                    )
                    op = (
                        sc["source_path"],
                        sc["operation_pointer"],
                        record["payload"]["claim"]["activation"]["rank"],
                    )
                    _require(op not in seen_ops, "同一激活原操作重复计数")
                    seen_ops.add(op)
                    deltas.append(projected["amount"])
                    entry["disposition"] = (
                        "model_dependency_active"
                        if projected["amount"]
                        else "model_dependency_inactive"
                    )
                elif not target_same:
                    entry["disposition"] = "different_target_verified"
                else:
                    entry["disposition"] = (
                        "additional_active_conflict"
                        if projected["amount"]
                        else "unresolved_current_fact"
                    )
                    blocked = True
            elif kind == "action_capacity_model":
                # Other models are never interpreted as numeric deltas.
                other = record["payload"]["claim"]
                result["other_model_materials"].append(
                    {
                        "record": record,
                        "material": fresh_model_material(conn, state, record),
                    }
                )
                if (
                    other["target"]["skill_id"] != selection["skill_id"]
                    or other["target"]["skill_depot_id"] != selection["skill_depot_id"]
                ):
                    entry["disposition"] = "different_target_verified"
                else:
                    blocked = True
            else:
                raise CapacityError("capacity_current", "当前容量库存存在未知合同")
        entries.append(entry)
    inventory = {
        "contract": "current-capacity-competitors-v1",
        "complete": True,
        "limit": MAX_FACTS,
        "selection": copy.deepcopy(selection),
        "version_line": line,
        "entries": entries,
    }
    result["competitor_inventory"] = inventory
    result["capacity_competitor_inventory_fingerprint"] = fingerprint(inventory)
    if blocked:
        result["reason"] = "capacity_competitor_unresolved"
    else:
        capacity = claim["base"]["value"] + sum(deltas)
        reviewed = next(
            x["capacity"]
            for x in claim["reviewed_cases"]
            if x["constellation"] == selection["constellation"]
        )
        _require(
            type(capacity) is int and 1 <= capacity <= 100 and capacity == reviewed,
            "基础与当前增量不同于已审完整上限",
        )
        result.update(status="ready", reason=None, capacity=capacity)
    bounded(result)
    result["material_fingerprint"] = fingerprint(result)
    return result


def get_action_capacity_profile(
    db_path, version_line="release", *, selection, context, model_ref, snapshot_id=None
):
    validate_selection(selection, context)
    ref(model_ref)
    with _read(db_path, version_line, snapshot_id) as (conn, state):
        return read_action_capacity_profile(
            conn, state, selection=selection, context=context, model_ref=model_ref
        )
