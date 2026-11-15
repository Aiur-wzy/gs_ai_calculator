"""Whiteboard document schema and validation.

This module owns the user document shape only. Persistence, handoff and
result binding are separate services exposed through genshin_app.whiteboard.
"""
from __future__ import annotations

import copy
import json
import math
import re
import uuid

from genshin_app.equipment.files import json_bytes
from genshin_app.equipment.provisional_entities import (
    validate_entity_ref,
    validate_registry,
)
from genshin_app.equipment.schema import PlanError, _json_guard
from genshin_engine.rotation_schema import ACTION_TYPES

KIND = "genshin-whiteboard"
SCHEMA_VERSION = 3
MAX_BYTES = 1024 * 1024
MAX_ACTIONS = 1000
MAX_REVISION = 1_000_000_000
PANEL_KEYS = ("atk", "hp", "def", "crit_rate", "crit_dmg", "em", "energy_recharge",
              "base_atk", "base_hp", "base_def")
ELEMENTS = ("all", "physical", "pyro", "hydro", "electro", "cryo", "anemo", "geo", "dendro")
_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}\Z")
_V1_BOARD_KEYS = {"kind", "schema_version", "board_id", "revision", "title", "version_line",
                  "duration", "team", "actions", "notes"}
_V2_BOARD_KEYS = _V1_BOARD_KEYS | {"capacity_intents"}
_BOARD_KEYS = _V2_BOARD_KEYS | {"action_admission_intents"}
_CAPACITY_VARIANTS = {"default", "tap", "hold"}
_CAPACITY_STARTS = {"action_start", "occupation_end"}
_CAPACITY_RECOVERY = {"sequential", "parallel"}


def _board_fingerprint(board):
    # Local import prevents schema/identity import cycles while keeping
    # fingerprint ownership in the identity module.
    from genshin_app.whiteboard_identity import board_fingerprint
    return board_fingerprint(board)


def _fail(message, path="", reason="invalid_whiteboard"):
    raise PlanError(message, reason, {"path": path})


def _fields(value, required, path, optional=()):
    if not isinstance(value, dict) or set(value) - set(required) - set(optional) or set(required) - set(value):
        _fail("白板字段缺失或含未知字段", path)


def _text(value, path, maximum=4096, empty=True):
    if not isinstance(value, str) or len(value) > maximum or (not empty and not value.strip()):
        _fail("文本为空或超过长度上限", path)


def _id(value, path):
    if not isinstance(value, str) or not _ID.fullmatch(value):
        _fail("身份需要不超过128字符的英文字母、数字、点、下划线或短横线", path)


def _number(value, path, low=0, high=1_000_000_000, nullable=False, integer=False):
    if value is None and nullable:
        return
    if type(value) not in ((int,) if integer else (int, float)) or not math.isfinite(value) or not low <= value <= high:
        _fail("数值超出允许范围或不是有限数值", path)


def _time(value, path, high):
    _number(value, path, high=high)
    if abs(value * 1_000_000 - round(value * 1_000_000)) > 0.0001:
        _fail("时间最多保留六位小数", path)


def default_panel():
    return {**dict.fromkeys(PANEL_KEYS), "dmg_bonus": dict.fromkeys(ELEMENTS)}


def new_whiteboard():
    return {"kind": KIND, "schema_version": SCHEMA_VERSION, "board_id": str(uuid.uuid4()),
            "revision": 0, "title": "新建排轴白板", "version_line": "release", "duration": 20,
            "team": [], "actions": [], "capacity_intents": [],
            "action_admission_intents": [], "provisional_entities": [], "notes": "",
            "damage_report": {"contract": "damage-report-window-v1", "mode": "last_active_damage"}}


def _database_ref(entity_type, database_id, version_line):
    return {"kind": "database", "entity_type": entity_type,
            "database_id": database_id, "version_line": version_line}


def _whiteboard_ref(value, *, entity_type, database_id, version_line, path, registry):
    """Validate a new ref or project a legacy database ID without rewriting it."""

    if value is None:
        if type(database_id) is not int or database_id < 1:
            _fail("公共实体需要数据库身份或当前白板临时引用", path)
        return _database_ref(entity_type, database_id, version_line)
    try:
        ref = validate_entity_ref(value, expected_type=entity_type)
    except Exception as exc:
        raise PlanError(str(exc), getattr(exc, "reason", "entity_ref_invalid"),
                        {"path": path}) from exc
    if ref["kind"] == "database":
        if database_id != ref["database_id"]:
            _fail("数据库实体引用与兼容ID不一致", path, "entity_ref_database_mismatch")
        if ref["version_line"] != version_line:
            _fail("数据库实体引用资料线与白板不一致", path, "entity_ref_version_mismatch")
    else:
        if database_id is not None:
            _fail("临时实体不得占用或伪造数据库ID", path, "entity_ref_fake_database_id")
        if (entity_type, ref["provisional_id"]) not in registry:
            _fail("临时实体引用不属于当前白板登记表", path, "entity_ref_registry_missing")
    return ref


def _validate_capacity_intents(board, actor_by_id, action_by_id):
    intents = board["capacity_intents"]
    if not isinstance(intents, list) or len(intents) > 64:
        _fail("容量意图最多64项", "capacity_intents")
    intent_ids, owned_actions, declared_contexts = set(), set(), []
    for index, intent in enumerate(intents):
        path = f"capacity_intents[{index}]"
        _fields(intent, {"id", "actor", "action_ids", "version_line", "model_ref", "target",
                         "cooldown", "initial_ready_in", "context"}, path)
        _id(intent["id"], path + ".id")
        if intent["id"] in intent_ids:
            _fail("容量意图身份重复", path + ".id")
        intent_ids.add(intent["id"])
        actor = actor_by_id.get(intent["actor"])
        if actor is None:
            _fail("容量意图引用未知角色", path + ".actor")
        action_ids = intent["action_ids"]
        if (not isinstance(action_ids, list) or not 1 <= len(action_ids) <= 64
                or any(not isinstance(value, str) for value in action_ids)
                or len(set(action_ids)) != len(action_ids)):
            _fail("容量意图需要1至64个不重复动作ID", path + ".action_ids")
        for action_id in action_ids:
            action = action_by_id.get(action_id)
            if action is None or action["character"] != intent["actor"]:
                _fail("容量意图动作必须存在并属于声明角色", path + ".action_ids")
            if action["action_type_id"] != "action.elemental_skill":
                _fail("容量意图只接受元素战技动作", path + ".action_ids")
            if action_id in owned_actions:
                _fail("一个白板动作不能由多个容量意图重复拥有", path + ".action_ids")
            owned_actions.add(action_id)
        if intent["version_line"] != board["version_line"]:
            _fail("容量意图资料线与白板不一致", path + ".version_line")
        _fields(intent["model_ref"], {"id", "revision"}, path + ".model_ref")
        _text(intent["model_ref"]["id"], path + ".model_ref.id", 128, False)
        if intent["model_ref"]["id"].strip() != intent["model_ref"]["id"]:
            _fail("容量模型身份首尾不能含空白", path + ".model_ref.id")
        _number(intent["model_ref"]["revision"], path + ".model_ref.revision", 1, 2**31 - 1, integer=True)
        target = intent["target"]
        _fields(target, {"character_id", "skill_depot_id", "skill_id", "variant"}, path + ".target")
        for key in ("character_id", "skill_depot_id", "skill_id"):
            _number(target[key], path + ".target." + key, 1, 2**53 - 1, integer=True)
        if target["character_id"] != actor["db_character_id"]:
            _fail("容量目标数据库角色与意图角色不一致", path + ".target.character_id")
        if target["variant"] not in _CAPACITY_VARIANTS:
            _fail("容量目标动作变体不受支持", path + ".target.variant")
        for action_id in action_ids:
            variant = action_by_id[action_id]["variant"]
            if variant and variant != target["variant"]:
                _fail("容量目标变体与白板动作变体冲突", path + ".target.variant")
        cooldown = intent["cooldown"]
        if cooldown is not None:
            _fields(cooldown, {"duration", "start", "recovery"}, path + ".cooldown")
            _time(cooldown["duration"], path + ".cooldown.duration", 3600)
            if cooldown["start"] not in _CAPACITY_STARTS or cooldown["recovery"] not in _CAPACITY_RECOVERY:
                _fail("容量冷却起点或恢复模式不受支持", path + ".cooldown")
        ready = intent["initial_ready_in"]
        if ready is not None:
            if not isinstance(ready, list) or len(ready) > 100:
                _fail("初始可用队列最多100项", path + ".initial_ready_in")
            previous = -1
            for queue_index, value in enumerate(ready):
                _time(value, f"{path}.initial_ready_in[{queue_index}]", 3600)
                if value < previous:
                    _fail("初始可用队列必须按时间升序", path + ".initial_ready_in")
                previous = value
        context = intent["context"]
        if context is not None:
            from genshin_runtime.action_capacity_model import validate_context
            try:
                validate_context(context)
            except (ValueError, TypeError, KeyError) as exc:
                raise PlanError("容量意图场景合同无效", "invalid_whiteboard", {"path": path + ".context"}) from exc
            declared_contexts.append(context)
    if declared_contexts and any(value != declared_contexts[0] for value in declared_contexts[1:]):
        _fail("同一白板的非空容量场景必须完全一致", "capacity_intents.context")


def _validate_action_admission_intents(board, actor_by_id, action_by_id):
    """Validate the user-owned v3 reference without granting execution authority."""
    from genshin_app.equipment.action_admission_schema import CONTEXT
    from genshin_runtime.action_admission_model import validate_selection

    intents = board["action_admission_intents"]
    if not isinstance(intents, list) or len(intents) > 64:
        _fail("动作接纳意图最多64项", "action_admission_intents")
    intent_ids, owned_actions = set(), set()
    for index, intent in enumerate(intents):
        path = f"action_admission_intents[{index}]"
        _fields(intent, {"id", "actor", "action_id", "version_line", "selection", "variant",
                         "model_ref", "initial_ready_in", "context"}, path)
        _id(intent["id"], path + ".id")
        if intent["id"] in intent_ids:
            _fail("动作接纳意图身份重复", path + ".id")
        intent_ids.add(intent["id"])
        actor = actor_by_id.get(intent["actor"])
        if actor is None:
            _fail("动作接纳意图引用未知角色", path + ".actor")
        _id(intent["action_id"], path + ".action_id")
        action = action_by_id.get(intent["action_id"])
        if action is None or action["character"] != intent["actor"]:
            _fail("动作接纳意图动作必须存在并属于声明角色", path + ".action_id")
        if intent["action_id"] in owned_actions:
            _fail("一个白板动作不能由多个动作接纳意图重复拥有", path + ".action_id")
        owned_actions.add(intent["action_id"])
        if action["action_type_id"] != "action.elemental_burst" or action["variant"] != "default":
            _fail("动作接纳首片只接受default元素爆发动作", path + ".action_id")
        if intent["version_line"] != "release" or board["version_line"] != "release":
            _fail("动作接纳首片只支持release资料线", path + ".version_line")
        if intent["variant"] != "default":
            _fail("动作接纳首片只支持default变体", path + ".variant")
        try:
            validate_selection(intent["selection"])
        except (ValueError, TypeError, KeyError, RecursionError, OverflowError) as exc:
            raise PlanError("动作接纳意图类型化技能身份无效", "invalid_whiteboard",
                            {"path": path + ".selection"}) from exc
        selection = intent["selection"]
        if (selection["character_id"] != actor["db_character_id"]
                or selection["constellation"] != 0 or actor["constellation"] != 0
                or selection["slot"] != "burst"):
            _fail("动作接纳首片要求与白板角色一致的C0元素爆发身份", path + ".selection")
        _fields(intent["model_ref"], {"id", "revision"}, path + ".model_ref")
        _text(intent["model_ref"]["id"], path + ".model_ref.id", 128, False)
        if intent["model_ref"]["id"].strip() != intent["model_ref"]["id"]:
            _fail("动作接纳模型身份首尾不能含空白", path + ".model_ref.id")
        _number(intent["model_ref"]["revision"], path + ".model_ref.revision",
                1, 2**31 - 1, integer=True)
        ready = intent["initial_ready_in"]
        if ready is not None:
            if not isinstance(ready, list) or len(ready) > 1:
                _fail("动作接纳首片初始恢复队列只能为空或包含一个时刻",
                      path + ".initial_ready_in")
            for queue_index, value in enumerate(ready):
                _time(value, f"{path}.initial_ready_in[{queue_index}]", 3600)
        context = intent["context"]
        if context is not None and context != CONTEXT:
            _fail("动作接纳场景只能为空或精确普通开放世界预设", path + ".context")


def validate_whiteboard(board):
    _json_guard(board, "whiteboard")
    if len(json_bytes(board)) > MAX_BYTES:
        _fail("白板超过1 MiB上限")
    _fields(board, _BOARD_KEYS, "whiteboard", {"provisional_entities", "calculation_options", "damage_report"})
    if "damage_report" in board:
        from genshin_engine.damage_report_window import REPORT_SCHEMA, validate_report
        from genshin_engine.rotation import _schema_errors
        errors = []
        _schema_errors(board["damage_report"], REPORT_SCHEMA, "damage_report", errors)
        if errors:
            _fail("伤害统计窗口格式不合法", "damage_report")
        if validate_report(board["damage_report"], period=board["duration"]):
            _fail("伤害统计窗口时间或模式不合法", "damage_report")
    if 'calculation_options' in board:
        from genshin_app.calculation_options import option_request
        option_request(board)
    if board["kind"] != KIND or type(board["schema_version"]) is not int or board["schema_version"] != SCHEMA_VERSION:
        _fail("不是受支持的排轴白板")
    try:
        uuid.UUID(board["board_id"])
    except (ValueError, TypeError, AttributeError) as exc:
        raise PlanError("白板身份不是UUID", "invalid_whiteboard") from exc
    _number(board["revision"], "revision", high=MAX_REVISION, integer=True)
    _text(board["title"], "title", 256, False)
    _text(board["notes"], "notes")
    if board["version_line"] not in ("release", "beta"):
        _fail("版本线应为release或beta", "version_line")
    _time(board["duration"], "duration", 3600)
    if board["duration"] <= 0:
        _fail("轴长必须大于零", "duration")
    try:
        registry_rows = validate_registry(board)
    except Exception as exc:
        raise PlanError(str(exc), getattr(exc, "reason", "invalid_whiteboard"),
                        getattr(exc, "details", {"path": "provisional_entities"})) from exc
    for index, row in enumerate(registry_rows):
        if any(row.get(key) is not None for key in (
                "database_lookup", "public_data_diff", "payload_fingerprint",
                "attestation", "approval")):
            _fail(
                "白板只能保存临时实体提案，数据库核对与授权必须在交接后的当前Agent方案中重新完成",
                f"provisional_entities[{index}]",
                "provisional_entity_authority_not_allowed_in_whiteboard",
            )
    registry = {(row["entity_type"], row["provisional_id"]) for row in registry_rows}
    team = board["team"]
    if not isinstance(team, list) or len(team) > 4:
        _fail("队伍最多四名角色", "team")
    actor_ids, character_identities = set(), set()
    for index, actor in enumerate(team):
        path = f"team[{index}]"
        _fields(actor, {"id", "db_character_id", "name", "level", "constellation", "weapon", "artifact_sets", "panel", "notes"}, path,
                {"character_ref", "panel_basis_mode", "ascension", "talent_base_levels", "artifact_presence", "panel_provenance"})
        if "panel_provenance" in actor:
            from genshin_app.equipment.panel_provenance import (
                SCHEMA as PROVENANCE_SCHEMA,
            )
            from genshin_app.equipment.schema import _check
            provenance_errors = []
            _check(actor["panel_provenance"], PROVENANCE_SCHEMA, path + ".panel_provenance", provenance_errors)
            if provenance_errors:
                _fail("面板来源记录格式不合法", path + ".panel_provenance")
        if "panel_basis_mode" in actor and (
                not isinstance(actor["panel_basis_mode"], str)
                or actor["panel_basis_mode"] not in ("database", "manual")):
            _fail("基础面板模式应为database或manual", path + ".panel_basis_mode")
        _id(actor["id"], path + ".id")
        _number(actor["db_character_id"], path + ".db_character_id", 1,
                nullable=True, integer=True)
        character_ref = _whiteboard_ref(
            actor.get("character_ref"), entity_type="character",
            database_id=actor["db_character_id"], version_line=board["version_line"],
            path=path + ".character_ref", registry=registry,
        )
        character_identity = json.dumps(character_ref, sort_keys=True, ensure_ascii=False)
        if actor["id"] in actor_ids or character_identity in character_identities:
            _fail("队伍角色身份不可重复", path)
        actor_ids.add(actor["id"])
        character_identities.add(character_identity)
        _text(actor["name"], path + ".name", 256, False)
        _text(actor["notes"], path + ".notes")
        _number(actor["level"], path + ".level", 1, 100, integer=True)
        _number(actor["constellation"], path + ".constellation", 0, 6, integer=True)
        if "ascension" in actor:
            _number(actor["ascension"], path + ".ascension", 0, 6, integer=True, nullable=True)
        if "talent_base_levels" in actor:
            _fields(actor["talent_base_levels"], set(), path + ".talent_base_levels", {"normal", "skill", "burst"})
            for key, value in actor["talent_base_levels"].items():
                _number(value, path + ".talent_base_levels." + key, 1, 10, integer=True)
        if "artifact_presence" in actor:
            from genshin_app.equipment.action_capacity_schema import SLOTS
            _fields(actor["artifact_presence"], set(SLOTS), path + ".artifact_presence")
            if any(value not in ("empty", "equipped", "unknown") for value in actor["artifact_presence"].values()):
                _fail("圣遗物槽位只能明确未装备、已装备或未知", path + ".artifact_presence")
        weapon = actor["weapon"]
        if weapon is not None:
            _fields(weapon, {"weapon_id", "name", "level", "ascension", "refinement"}, path + ".weapon",
                    {"entity_ref"})
            _text(weapon["name"], path + ".weapon.name", 256, False)
            for key, low, high in (("weapon_id", 1, 1_000_000_000), ("level", 1, 100), ("ascension", 0, 6), ("refinement", 1, 5)):
                _number(weapon[key], path + ".weapon." + key, low, high, nullable=True, integer=True)
            _whiteboard_ref(
                weapon.get("entity_ref"), entity_type="weapon",
                database_id=weapon["weapon_id"], version_line=board["version_line"],
                path=path + ".weapon.entity_ref", registry=registry,
            )
        sets = actor["artifact_sets"]
        if not isinstance(sets, list) or len(sets) > 5:
            _fail("套装选择最多五项", path)
        seen_sets, count = set(), 0
        for item in sets:
            _fields(item, {"set_id", "name", "count"}, path + ".artifact_sets", {"entity_ref"})
            _number(item["set_id"], path + ".artifact_sets.set_id", 1, nullable=True, integer=True)
            _number(item["count"], path + ".artifact_sets.count", 1, 5, integer=True)
            _text(item["name"], path + ".artifact_sets.name", 256, False)
            set_ref = _whiteboard_ref(
                item.get("entity_ref"), entity_type="artifact_set",
                database_id=item["set_id"], version_line=board["version_line"],
                path=path + ".artifact_sets.entity_ref", registry=registry,
            )
            set_identity = json.dumps(set_ref, sort_keys=True, ensure_ascii=False)
            if set_identity in seen_sets:
                _fail("套装身份重复", path)
            seen_sets.add(set_identity)
            count += item["count"]
        if count > 5:
            _fail("套装合计最多五件", path)
        if "artifact_presence" in actor and count > sum(value != "empty" for value in actor["artifact_presence"].values()):
            _fail("套装件数与未装备槽位声明冲突", path + ".artifact_presence")
        panel = actor["panel"]
        _fields(panel, {"dmg_bonus"}, path + ".panel", PANEL_KEYS)
        for key in PANEL_KEYS:
            _number(panel.get(key), path + ".panel." + key, nullable=True)
        _fields(panel["dmg_bonus"], set(), path + ".panel.dmg_bonus", ELEMENTS)
        for element, value in panel["dmg_bonus"].items():
            _number(value, path + ".panel.dmg_bonus." + element, nullable=True)
    actions = board["actions"]
    if not isinstance(actions, list) or len(actions) > MAX_ACTIONS:
        _fail("动作最多1000项", "actions")
    ids, last = set(), -1
    for index, action in enumerate(actions):
        path = f"actions[{index}]"
        _fields(action, {"id", "character", "action_type_id", "label", "start", "variant", "notes"}, path)
        _id(action["id"], path + ".id")
        if action["id"] in ids:
            _fail("动作身份重复", path)
        ids.add(action["id"])
        if action["character"] not in actor_ids or action["action_type_id"] not in ACTION_TYPES:
            _fail("动作引用未知角色或动作类型", path)
        _time(action["start"], path + ".start", board["duration"])
        if action["start"] >= board["duration"] or action["start"] < last:
            _fail("动作必须按开始时间排序，且位于[0,轴长)内", path)
        last = action["start"]
        _text(action["label"], path + ".label", 256, False)
        _text(action["variant"], path + ".variant", 256)
        _text(action["notes"], path + ".notes")
    _validate_capacity_intents(
        board,
        {actor["id"]: actor for actor in team},
        {action["id"]: action for action in actions},
    )
    _validate_action_admission_intents(
        board,
        {actor["id"]: actor for actor in team},
        {action["id"]: action for action in actions},
    )
    return {"valid": True, "board_fingerprint": _board_fingerprint(board), "pending": pending_items(board)}


def pending_items(board):
    items = []
    for key, label in (("team", "角色队伍"), ("actions", "动作轴")):
        if not board[key]:
            items.append({"code": "empty_" + key, "path": key, "message": "尚未添加" + label})
    for actor in board["team"]:
        unknown = [key for key in PANEL_KEYS[:7] if actor["panel"].get(key) is None]
        if unknown:
            items.append({"code": "unknown_panel", "path": "team." + actor["id"] + ".panel",
                          "message": actor["name"] + "有未填写面板，交由Agent补齐并说明"})
    contexts = []
    for intent in board.get("capacity_intents", []):
        base = "capacity_intents." + intent["id"]
        if intent["cooldown"] is None:
            items.append({"code": "capacity_cooldown_unknown", "path": base + ".cooldown",
                          "message": "容量意图尚未确认冷却语义"})
        if intent["initial_ready_in"] is None:
            items.append({"code": "capacity_initial_state_unknown", "path": base + ".initial_ready_in",
                          "message": "容量意图尚未确认初始可用队列"})
        if intent["context"] is None:
            items.append({"code": "capacity_context_unknown", "path": base + ".context",
                          "message": "容量意图尚未确认计算场景"})
        else:
            contexts.append(intent["context"])
    if contexts and len(contexts) != len(board.get("capacity_intents", [])):
        items.append({"code": "capacity_context_incomplete", "path": "capacity_intents.context",
                      "message": "部分容量意图已声明场景，仍有意图为未知；交接不得推广场景"})
    for intent in board.get("action_admission_intents", []):
        base = "action_admission_intents." + intent["id"]
        if intent["context"] is None:
            items.append({"code": "action_admission_context_unknown", "path": base + ".context",
                          "message": "动作接纳意图尚未明确计算场景；不会默认采用普通开放世界"})
        if intent["initial_ready_in"] is None:
            items.append({"code": "action_admission_initial_state_unknown",
                          "path": base + ".initial_ready_in",
                          "message": "动作接纳意图尚未明确初始冷却队列；不会把未知当作全部就绪"})
    items.append({"code": "agent_model_required", "path": "actions",
                  "message": "需Agent核实动作占用、冷却、产球、伤害和效果关系后计算；白板本身不是计算结果"})
    return items


def whiteboard_response(board):
    from genshin_app.whiteboard_revision import revision_transition_capabilities
    return {"board": copy.deepcopy(board), **validate_whiteboard(board),
            "revision_policy": revision_transition_capabilities()}
