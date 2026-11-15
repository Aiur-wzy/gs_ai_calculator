"""Result-intent and execution-binding checks for whiteboard handoffs."""
from __future__ import annotations

import json

from genshin_app.calculation_capacity import CALCULATED_ARTIFACT_BYTES
from genshin_app.equipment.files import fingerprint
from genshin_app.equipment.schema import validate_plan
from genshin_app.whiteboard_handoff import _context_state
from genshin_app.whiteboard_identity import (
    board_fingerprint,
    legacy_board_fingerprint,
    v2_board_fingerprint,
)
from genshin_app.whiteboard_schema import (
    ELEMENTS,
    PANEL_KEYS,
    SCHEMA_VERSION,
    _fail,
    _fields,
    _whiteboard_ref,
    validate_whiteboard,
)
from genshin_app.whiteboard_store import _read, _upgrade_loaded_board
from genshin_runtime.diagnostics import diagnosed


def _equal(actual, expected, path):
    def same(left, right):
        if type(left) in (int, float) and type(right) in (int, float):
            return left == right
        if type(left) is not type(right):
            return False
        if isinstance(left, dict):
            return left.keys() == right.keys() and all(same(left[key], right[key]) for key in left)
        if isinstance(left, list):
            return len(left) == len(right) and all(same(a, b) for a, b in zip(left, right, strict=True))
        return left == right

    if not same(actual, expected):
        _fail("计算结果修改了用户白板意图；请作为独立候选查看", path, "whiteboard_intent_mismatch")


def _check_typed_identities(board, plan):
    """Only registered, executable source selections count as character evidence."""
    by_database = {actor["db_character_id"]: actor for actor in board["team"]}
    by_actor = {actor["id"]: actor for actor in board["team"]}
    for family in ("hit_parameter_sources", "action_resource_sources", "attack_metadata_sources",
                   "periodic_attack_sources", "action_admission_sources"):
        for source in plan.get(family, []):
            selection = source["selection"]
            actor = by_database.get(selection["character_id"])
            if actor is None:
                _fail("计算资料引用了用户队伍以外的角色", family, "whiteboard_intent_mismatch")
            _equal(source["version_line"], board["version_line"], family + ".version_line")
            _equal(selection["constellation"], actor["constellation"], family + ".selection.constellation")
    for source in plan.get("mechanism_sources", []):
        actor = by_actor.get(source["actor"])
        if actor is None:
            _fail("机制来源引用了用户队伍以外的角色", "mechanism_sources", "whiteboard_intent_mismatch")
        _equal(source["version_line"], board["version_line"], "mechanism_sources.version_line")
        selection = source.get("cast_binding", {}).get("selection") or {}
        if "constellation" in selection:
            _equal(selection["constellation"], actor["constellation"], "mechanism_sources.cast_binding.selection.constellation")


@diagnosed('whiteboard', 'validate-result-intent-v1')
def validate_result_intent(board, plan, *, _executed=False):
    """Compare actual calculation inputs; a copied declared digest is insufficient."""
    validate_whiteboard(board)
    if _executed:
        from genshin_app.equipment.schema import validate_executed_plan
        validate_executed_plan(plan)
    else:
        validate_plan(plan)
    if plan.get("extensions", {}).get("whiteboard_handoff", {}).get("input_owner") == "native-game-input-v1":
        from genshin_app.native_input_projection import canonical_projection_valid
        return canonical_projection_valid(board, plan)
    from genshin_app.calculation_options import option_request
    _equal(option_request(plan), option_request(board), 'calculation_options')
    handoff = plan.get("extensions", {}).get("whiteboard_handoff")
    if not isinstance(handoff, dict):
        _fail("结果没有白板交接信息", reason="whiteboard_intent_mismatch")
    handoff_version = handoff.get("schema_version")
    saved_original = handoff.get("board")
    if handoff_version == 1:
        # Existing v1 results stay bound only to the exact v1 canonical view.
        # Adding any capacity intent creates a new business input and fails closed.
        expected_fingerprint = legacy_board_fingerprint(board)
        _equal(handoff.get("board_fingerprint"), expected_fingerprint, "handoff.board_fingerprint")
        original, source_version = _upgrade_loaded_board(saved_original)
        _equal(source_version, 1, "handoff.board.schema_version")
        _equal(legacy_board_fingerprint(original), expected_fingerprint, "handoff.board")
    elif handoff_version == 2:
        expected_fingerprint = v2_board_fingerprint(board)
        _equal(handoff.get("board_fingerprint"), expected_fingerprint, "handoff.board_fingerprint")
        original, source_version = _upgrade_loaded_board(saved_original)
        _equal(source_version, 2, "handoff.board.schema_version")
        _equal(v2_board_fingerprint(original), expected_fingerprint, "handoff.board")
        _equal(handoff.get("capacity_intents"), board["capacity_intents"], "handoff.capacity_intents")
        _equal(handoff.get("capacity_context_state"), _context_state(board["capacity_intents"]),
               "handoff.capacity_context_state")
    elif handoff_version == SCHEMA_VERSION:
        expected_fingerprint = board_fingerprint(board)
        _equal(handoff.get("board_fingerprint"), expected_fingerprint, "handoff.board_fingerprint")
        original, source_version = _upgrade_loaded_board(saved_original)
        _equal(source_version, SCHEMA_VERSION, "handoff.board.schema_version")
        _equal(board_fingerprint(original), expected_fingerprint, "handoff.board")
        _equal(handoff.get("capacity_intents"), board["capacity_intents"], "handoff.capacity_intents")
        _equal(handoff.get("capacity_context_state"), _context_state(board["capacity_intents"]),
               "handoff.capacity_context_state")
        _equal(handoff.get("action_admission_intents"), board["action_admission_intents"],
               "handoff.action_admission_intents")
        _equal(handoff.get("action_admission_context_state"),
               _context_state(board["action_admission_intents"]),
               "handoff.action_admission_context_state")
        proposals = handoff.get("action_admission_proposals")
        if not isinstance(proposals, list) or len(proposals) > 64:
            _fail("动作接纳交接提案缺失或超限", "handoff.action_admission_proposals",
                  "whiteboard_intent_mismatch")
        for proposal in proposals:
            encoded = json.dumps(proposal, ensure_ascii=False, sort_keys=True)
            if "accepted_fingerprint" in encoded or "action_admission_source" in encoded:
                _fail("白板交接提案不能保存执行确认或私有标记",
                      "handoff.action_admission_proposals", "whiteboard_intent_mismatch")
    else:
        _fail("白板交接版本不受支持", "handoff.schema_version", "whiteboard_intent_mismatch")
    original_team = {actor["id"]: actor for actor in original["team"]}
    for actor in board["team"]:
        if "panel_basis_mode" in actor:
            _equal(original_team[actor["id"]].get("panel_basis_mode"), actor["panel_basis_mode"],
                   "handoff.board." + actor["id"] + ".panel_basis_mode")
    template = plan["manual_template"]
    if board.get("damage_report") is not None:
        requested = board["damage_report"]
        actual = (plan.get("rotation_model") or {}).get("damage_report") or {}
        _equal(actual.get("mode"), requested["mode"], "damage_report.mode")
        if requested["mode"] == "explicit":
            _equal(actual.get("end"), requested["end"], "damage_report.end")
    _equal(template.get("loop_duration"), board["duration"], "manual_template.loop_duration")
    _equal(template.get("meta", {}).get("version_line"), board["version_line"], "manual_template.meta.version_line")
    team = {actor["id"]: actor for actor in template["characters"]}
    _equal(sorted(team), sorted(actor["id"] for actor in board["team"]), "manual_template.characters")
    _equal(sorted(plan["equipment_inputs"]), sorted(team), "equipment_inputs")
    _equal(handoff.get("constellations"), {actor["id"]: actor["constellation"] for actor in board["team"]}, "handoff.constellations")
    needs_notes = False
    registry = {
        (row["entity_type"], row["provisional_id"])
        for row in board.get("provisional_entities", [])
    }
    for actor in board["team"]:
        cid, stats = actor["id"], team[actor["id"]]["stats"]
        _equal(team[cid]["level"], actor["level"], cid + ".level")
        equipment = plan["equipment_inputs"][cid]
        _equal(equipment.get("mode"), "panel", cid + ".equipment.mode")
        _equal(equipment.get("panel_policy"), "static_included", cid + ".panel_policy")
        if "panel_basis_mode" in actor:
            _equal(equipment.get("panel_basis_mode"), actor["panel_basis_mode"], cid + ".panel_basis_mode")
        expected_character_ref = _whiteboard_ref(
            actor.get("character_ref"), entity_type="character",
            database_id=actor.get("db_character_id"),
            version_line=board["version_line"],
            path=cid + ".character_ref", registry=registry,
        )
        actual_character_ref = equipment.get("character_entity_ref")
        if actual_character_ref is None and expected_character_ref["kind"] == "database":
            _equal(
                equipment.get("character_ref"),
                {"db_character_id": actor["db_character_id"],
                 "version_line": board["version_line"]},
                cid + ".character_ref",
            )
        else:
            _equal(actual_character_ref, expected_character_ref,
                   cid + ".character_entity_ref")
        for key in PANEL_KEYS:
            if actor["panel"].get(key) is not None:
                _equal(stats.get(key), actor["panel"][key], cid + ".panel." + key)
            elif key in stats:
                needs_notes = True
        for element in ELEMENTS:
            value = actor["panel"]["dmg_bonus"].get(element)
            if value is not None:
                _equal(stats.get("dmg_bonus", {}).get(element), value, cid + ".dmg_bonus." + element)
            elif element in stats.get("dmg_bonus", {}):
                needs_notes = True
        if handoff.get("equipment_input_contract") == "equipment-presence-v1":
            progression = equipment.get("progression") or {}
            _equal(progression.get("constellation"), actor["constellation"], cid + ".constellation")
            if actor.get("ascension") is not None:
                _equal(progression.get("ascension"), actor["ascension"], cid + ".ascension")
            for key, value in actor.get("talent_base_levels", {}).items():
                _equal(progression.get("talent_base_levels", {}).get(key), value, cid + ".talent_base_levels." + key)
        if "artifact_presence" in actor:
            _equal(equipment.get("artifact_presence"), actor["artifact_presence"], cid + ".artifact_presence")
        selected = actor["weapon"]
        actual_weapon = equipment.get("weapon")
        if selected is not None:
            if not isinstance(actual_weapon, dict):
                _fail("已选武器被删除", cid + ".weapon", "whiteboard_intent_mismatch")
            expected_weapon_ref = _whiteboard_ref(
                selected.get("entity_ref"), entity_type="weapon",
                database_id=selected.get("weapon_id"),
                version_line=board["version_line"],
                path=cid + ".weapon.entity_ref", registry=registry,
            )
            actual_weapon_ref = actual_weapon.get("entity_ref")
            if actual_weapon_ref is None and expected_weapon_ref["kind"] == "database":
                _equal(actual_weapon.get("weapon_id"), selected["weapon_id"],
                       cid + ".weapon.weapon_id")
            else:
                _equal(actual_weapon_ref, expected_weapon_ref,
                       cid + ".weapon.entity_ref")
            for key in ("weapon_id", "level", "ascension"):
                if selected[key] is not None:
                    _equal(actual_weapon.get(key), selected[key], cid + ".weapon." + key)
                elif actual_weapon.get(key) is not None:
                    needs_notes = True
            if selected["refinement"] is not None:
                if handoff.get("equipment_input_contract") == "equipment-presence-v1" or "refinement" in actual_weapon:
                    _equal(actual_weapon.get("refinement"), selected["refinement"], cid + ".weapon.refinement")
                for row in actual_weapon.get("refinements", []):
                    _equal(row.get("refinement"), selected["refinement"], cid + ".weapon.refinement")
            elif any(row.get("refinement") is not None for row in actual_weapon.get("refinements", [])):
                needs_notes = True
        elif actual_weapon is not None:
            needs_notes = True
        expected_sets = []
        for item in actor["artifact_sets"]:
            expected_sets.append({
                "entity_ref": _whiteboard_ref(
                    item.get("entity_ref"), entity_type="artifact_set",
                    database_id=item.get("set_id"),
                    version_line=board["version_line"],
                    path=cid + ".artifact_sets.entity_ref", registry=registry,
                ),
                "count": item["count"],
            })
        actual_sets = []
        for item in equipment.get("artifact_sets", []):
            actual_sets.append({
                "entity_ref": _whiteboard_ref(
                    item.get("entity_ref"), entity_type="artifact_set",
                    database_id=item.get("set_id"),
                    version_line=board["version_line"],
                    path=cid + ".artifact_sets.entity_ref", registry=registry,
                ),
                "count": item["count"],
            })
        set_key = lambda item: json.dumps(item, ensure_ascii=False, sort_keys=True)
        if expected_sets:
            _equal(sorted(actual_sets, key=set_key), sorted(expected_sets, key=set_key),
                   cid + ".artifact_sets")
        elif actual_sets:
            needs_notes = True
    for source in plan.get("passive_sources", []):
        if source.get("constellation") is not None:
            _equal(source["constellation"], handoff["constellations"][source["actor"]], "passive_sources.constellation")
    _check_typed_identities(board, plan)
    rotation = plan.get("rotation_model")
    if not isinstance(rotation, dict):
        _fail("计算结果缺少实际动作模型", reason="whiteboard_intent_mismatch")
    _equal(rotation.get("period"), board["duration"], "rotation_model.period")
    _equal(sorted(row["id"] for row in rotation["characters"]), sorted(team), "rotation_model.characters")
    abilities = {row["id"]: row for row in rotation["abilities"]}
    actual_actions = {row["id"]: row for row in rotation["actions"]}
    mapping = handoff.get("action_map")
    if not isinstance(mapping, list) or len(mapping) != len(board["actions"]):
        _fail("白板动作映射缺失", reason="whiteboard_intent_mismatch")
    mapped, sequence = set(), []
    for source, pair in zip(board["actions"], mapping, strict=True):
        _fields(pair, {"board_action_id", "native_action_id"}, "action_map")
        _equal(pair["board_action_id"], source["id"], "action_map.board_action_id")
        aid = pair["native_action_id"]
        if aid not in actual_actions or aid in mapped:
            _fail("用户动作缺失或被重复映射", "action_map", "whiteboard_intent_mismatch")
        mapped.add(aid)
        action = actual_actions[aid]
        ability = abilities[action["ability_id"]]
        _equal(action.get("start"), source["start"], aid + ".start")
        _equal(action.get("repeat"), {"mode": "once"}, aid + ".repeat")
        if source["action_type_id"] != "action.switch":
            _equal(ability["character"], source["character"], aid + ".character")
        elif ability["character"] not in team:
            _fail("切换动作的执行者不在原队伍中", aid + ".character", "whiteboard_intent_mismatch")
        _equal(ability["action_type_id"], source["action_type_id"], aid + ".action_type_id")
        if source["variant"]:
            _equal(ability.get("variant", ""), source["variant"], aid + ".variant")
        elif ability.get("variant", ""):
            needs_notes = True
        if source["action_type_id"] == "action.switch":
            _equal(action.get("switch_to"), source["character"], aid + ".switch_to")
        sequence.append((action["start"], action["order"]))
    if sequence != sorted(sequence) or len(set(sequence)) != len(sequence):
        _fail("用户同秒动作顺序被改变", reason="whiteboard_intent_mismatch")
    auxiliaries = handoff.get("auxiliary_action_ids")
    if not isinstance(auxiliaries, list) or any(not isinstance(value, str) for value in auxiliaries) or len(set(auxiliaries)) != len(auxiliaries):
        _fail("辅助动作声明不合法", reason="whiteboard_intent_mismatch")
    _equal(sorted(set(actual_actions) - mapped), sorted(auxiliaries), "auxiliary_action_ids")
    for aid in auxiliaries:
        action = actual_actions[aid]
        if abilities[action["ability_id"]]["action_type_id"] not in ("action.switch", "action.wait"):
            _fail("辅助动作只能是切人或等待；新的攻击动作须另建候选", aid, "whiteboard_intent_mismatch")
        _equal(action.get("repeat"), {"mode": "once"}, aid + ".repeat")
    notes = handoff.get("completion_notes")
    if not isinstance(notes, list) or len(notes) > 100 or any(not isinstance(note, str) or not note.strip() or len(note) > 4096 for note in notes):
        _fail("Agent补充说明需要有界文本列表", reason="whiteboard_intent_mismatch")
    if (needs_notes or auxiliaries) and not notes:
        _fail("补齐未知字段或辅助动作需要说明", reason="whiteboard_intent_mismatch")
    return {"board_fingerprint": board_fingerprint(board), "user_action_count": len(mapped), "auxiliary_action_count": len(auxiliaries)}


def _bind_action_admission_result(board, calculated, bound):
    """Bind every v3 admission intent to one committed native execution."""
    from genshin_app.equipment.action_admission_receipt import FIELD
    from genshin_app.equipment.action_admission_schema import CONTEXT

    plan = calculated["plan"]
    result = calculated["result"]
    receipt = calculated["result_receipt"]
    intents = board["action_admission_intents"]
    sources = plan.get("action_admission_sources", [])

    def mismatch(message, path):
        _fail(message, path, "whiteboard_action_admission_result_mismatch")

    if not intents:
        if sources:
            mismatch("计算结果含白板未声明的动作接纳来源", "action_admission_sources")
        return {**bound, "action_admission_intent_count": 0,
                "action_admission_source_count": 0,
                "action_admission_committed_count": 0}
    if len(sources) != len(intents):
        mismatch("动作接纳意图与实际来源数量不一致", "action_admission_sources")
    if any(intent["context"] is None for intent in intents):
        mismatch("完整计算前必须逐项显式选择动作接纳场景",
                 "action_admission_intents.context")
    if any(intent["initial_ready_in"] is None for intent in intents):
        mismatch("完整计算前必须逐项显式选择动作接纳初始冷却队列",
                 "action_admission_intents.initial_ready_in")
    if plan.get("action_admission_context") != CONTEXT:
        mismatch("原生方案的动作接纳场景与白板意图不一致", "action_admission_context")

    handoff = plan["extensions"]["whiteboard_handoff"]
    action_map = {row["board_action_id"]: row["native_action_id"]
                  for row in handoff["action_map"]}
    rotation = plan["rotation_model"]
    native_actions = {row["id"]: row for row in rotation["actions"]}
    abilities = {row["id"]: row for row in rotation["abilities"]}
    records = result.get("action_admission_compilations")
    if not isinstance(records, list) or len(records) != len(intents):
        mismatch("完整计算缺少一一对应的动作接纳编译记录",
                 "result.action_admission_compilations")
    record_by_id = {row.get("id"): row for row in records}
    source_by_id = {row.get("id"): row for row in sources}
    if len(record_by_id) != len(records) or len(source_by_id) != len(sources):
        mismatch("动作接纳来源或编译记录身份重复", "action_admission_sources")
    initial_rows = rotation.get("initial_cooldowns")
    if not isinstance(initial_rows, list):
        mismatch("完整计算缺少显式初始冷却队列", "rotation_model.initial_cooldowns")

    check = result.get("rotation_check") or {}
    audit = check.get("action_admission_execution")
    parents = [row for row in check.get("event_log", [])
               if row.get("type") == "action" and isinstance(row.get("action_admission"), dict)]
    runtime_receipts = audit.get("receipts", []) if isinstance(audit, dict) else []
    if (not isinstance(audit, dict) or not isinstance(runtime_receipts, list)
            or not isinstance(receipt.get("action_admissions"), dict)
            or not isinstance(result.get(FIELD), dict)):
        mismatch("完整计算缺少动作接纳执行证明、离线回执或运行时账本",
                 "result.rotation_check.action_admission_execution")

    used_sources, used_abilities = set(), set()
    for intent in intents:
        base = "action_admission_intents." + intent["id"]
        native_id = action_map.get(intent["action_id"])
        action = native_actions.get(native_id)
        ability = abilities.get(action.get("ability_id")) if isinstance(action, dict) else None
        if ability is None:
            mismatch("动作接纳意图没有映射到实际原生能力", base + ".action_id")
        ability_id = ability["id"]
        if (ability_id in used_abilities or ability.get("character") != intent["actor"]
                or ability.get("action_type_id") != "action.elemental_burst"
                or ability.get("variant") != "default"):
            mismatch("动作接纳意图必须唯一映射到声明角色的default元素爆发能力",
                     base + ".action_id")
        used_abilities.add(ability_id)
        ability_actions = [row for row in rotation["actions"] if row.get("ability_id") == ability_id]
        if len(ability_actions) != 1 or ability_actions[0].get("id") != native_id:
            mismatch("动作接纳首片要求意图、动作与能力一一对应", base + ".action_id")

        candidates = [row for row in sources if row.get("ability_id") == ability_id]
        if len(candidates) != 1 or candidates[0]["id"] in used_sources:
            mismatch("动作接纳意图没有唯一对应的原生来源", base + ".action_id")
        source = candidates[0]
        used_sources.add(source["id"])
        expected_source = {
            "id": intent["id"], "ability_id": ability_id,
            "version_line": intent["version_line"],
            "selection": intent["selection"], "variant": intent["variant"],
            "model_ref": intent["model_ref"],
        }
        for key, value in expected_source.items():
            if fingerprint(source.get(key)) != fingerprint(value):
                mismatch("实际动作接纳来源与白板意图字段不一致", base + "." + key)
        record = record_by_id.get(source["id"])
        if (not isinstance(record, dict) or record.get("status") != "ready"
                or record.get("ability_id") != ability_id):
            mismatch("动作接纳编译尚未就绪或能力映射不一致",
                     "result.action_admission_compilations." + source["id"])
        source_fingerprint = record.get("source_fingerprint")
        required = (record.get("required_confirmation") or {}).get("accepted_fingerprint")
        if (not isinstance(source_fingerprint, str)
                or source.get("accepted_fingerprint") != source_fingerprint
                or required != source_fingerprint):
            mismatch("动作接纳来源确认与本次编译指纹不一致",
                     "action_admission_sources." + source["id"] + ".accepted_fingerprint")
        group = ((record.get("compiled_values") or {}).get("cooldown") or {}).get("group")
        initial = [row for row in initial_rows
                   if row.get("character") == intent["actor"] and row.get("group") == group]
        if (not isinstance(group, str) or len(initial) != 1
                or fingerprint(initial[0].get("ready_in"))
                != fingerprint(intent["initial_ready_in"])):
            mismatch("动作接纳初始冷却队列与白板意图不一致", base + ".initial_ready_in")

        parent_rows = [row for row in parents
                       if row.get("action_id") == native_id and row.get("ability_id") == ability_id]
        if len(parent_rows) != 1:
            mismatch("动作接纳意图没有唯一运行时父动作记录", base + ".action_id")
        admission = parent_rows[0]["action_admission"]
        if admission.get("status") != "committed" or admission.get("binding_id") != source["id"]:
            mismatch("动作接纳意图对应动作未成功提交", base + ".action_id")
        certificate = admission.get("certificate") or {}
        matched_receipts = [row for row in runtime_receipts
                            if row.get("run_id") == certificate.get("run_id")
                            and row.get("action_id") == native_id
                            and row.get("binding_id") == source["id"]
                            and row.get("status") == "committed"]
        if len(matched_receipts) != 1:
            mismatch("动作接纳意图没有唯一成功运行时回执", base + ".action_id")
    if used_sources != set(source_by_id):
        mismatch("计算结果含无法对应白板意图的额外动作接纳来源",
                 "action_admission_sources")
    return {**bound, "action_admission_intent_count": len(intents),
            "action_admission_source_count": len(sources),
            "action_admission_committed_count": len(intents)}


@diagnosed('whiteboard', 'validate-whiteboard-action-admission-result-v1')
def validate_whiteboard_action_admission_result(board, calculated, *, _session=None):
    """Validate a complete delivery, then bind the v3 action-admission chain."""
    from genshin_app.equipment.delivery import validate_delivery_calculated

    delivery = validate_delivery_calculated(calculated, _session=_session)
    bound = validate_result_intent(board, calculated["plan"], _executed=True)
    return {**_bind_action_admission_result(board, calculated, bound),
            "_delivery_integrity": delivery["integrity"]}


@diagnosed('whiteboard', 'validate-whiteboard-capacity-result-v1')
def validate_whiteboard_capacity_result(board, calculated, *, _session=None):
    """Prove that every saved capacity intent was executed by one typed source.

    ``validate_result_intent`` deliberately remains suitable for a draft native
    candidate.  This stricter check is only for a value that claims to be a
    complete calculation: it first replays the ordinary delivery validators,
    then binds the whiteboard's action-level intent to the actual native ability
    pool and its capacity receipt.  Copied handoff summaries never satisfy this
    contract on their own.
    """
    from genshin_app.equipment.delivery import validate_delivery_calculated

    # Establish the generic result, compilation, execution receipt and ledger
    # proof before interpreting any whiteboard metadata.
    delivery = validate_delivery_calculated(calculated, _session=_session)
    plan = calculated["plan"]
    bound = _bind_action_admission_result(
        board, calculated, validate_result_intent(board, plan, _executed=True))
    intents = board["capacity_intents"]
    sources = plan.get("action_capacity_sources", [])

    def mismatch(message, path):
        _fail(message, path, "whiteboard_capacity_result_mismatch")

    if not intents:
        if sources:
            mismatch("计算结果含白板未声明的次数来源", "action_capacity_sources")
        return {**bound, "capacity_intent_count": 0, "capacity_source_count": 0,
                "_delivery_integrity": delivery["integrity"]}
    if len(sources) != len(intents):
        mismatch("容量意图与实际次数来源数量不一致", "action_capacity_sources")

    handoff = plan["extensions"]["whiteboard_handoff"]
    action_map = {row["board_action_id"]: row["native_action_id"]
                  for row in handoff["action_map"]}
    rotation = plan["rotation_model"]
    native_actions = {row["id"]: row for row in rotation["actions"]}
    abilities = {row["id"]: row for row in rotation["abilities"]}
    used_sources = set()
    source_for_intent = {}

    contexts = [intent["context"] for intent in intents]
    if any(context is None for context in contexts):
        mismatch("完整计算前必须逐项明确容量计算场景", "capacity_intents.context")
    if any(fingerprint(context) != fingerprint(contexts[0]) for context in contexts[1:]):
        mismatch("完整计算中的容量场景必须完全一致", "capacity_intents.context")
    if "action_capacity_context" not in plan or fingerprint(plan["action_capacity_context"]) != fingerprint(contexts[0]):
        mismatch("原生方案的容量场景与白板意图不一致", "action_capacity_context")

    initial_rows = rotation.get("initial_cooldowns")
    if not isinstance(initial_rows, list):
        mismatch("完整计算缺少显式初始冷却队列", "rotation_model.initial_cooldowns")

    for intent in intents:
        base = "capacity_intents." + intent["id"]
        member_ids = []
        for board_action_id in intent["action_ids"]:
            native_id = action_map.get(board_action_id)
            action = native_actions.get(native_id)
            ability = abilities.get(action.get("ability_id")) if isinstance(action, dict) else None
            if ability is None:
                mismatch("容量意图动作没有映射到实际原生能力", base + ".action_ids")
            if ability.get("character") != intent["actor"] or ability.get("action_type_id") != "action.elemental_skill":
                mismatch("容量意图实际能力必须是声明角色的元素战技", base + ".action_ids")
            actual_variant = ability.get("variant") or "default"
            if actual_variant != intent["target"]["variant"]:
                mismatch("容量意图实际能力变体与类型化目标不一致", base + ".target.variant")
            member_ids.append(ability["id"])
        member_set = set(member_ids)
        candidates = [index for index, source in enumerate(sources)
                      if set(source["ability_ids"]) == member_set]
        if len(candidates) != 1 or candidates[0] in used_sources:
            mismatch("容量意图没有唯一对应的实际次数来源", base + ".action_ids")
        source_index = candidates[0]
        used_sources.add(source_index)
        source = sources[source_index]
        source_for_intent[intent["id"]] = source
        for key in ("actor", "version_line", "model_ref", "target"):
            if fingerprint(source[key]) != fingerprint(intent[key]):
                mismatch("实际次数来源与容量意图字段不一致", base + "." + key)

        cooldown = intent["cooldown"]
        if cooldown is None:
            mismatch("完整计算前必须明确容量冷却语义", base + ".cooldown")
        groups = set()
        for ability_id in member_set:
            native_cooldown = abilities[ability_id].get("cooldown")
            group = native_cooldown.get("group") if isinstance(native_cooldown, dict) else None
            if not isinstance(group, str) or not group:
                mismatch("容量池成员必须声明同一个非空冷却组", base + ".cooldown")
            groups.add(group)
            actual_cooldown = {key: native_cooldown.get(key) for key in ("duration", "start", "recovery")}
            if fingerprint(actual_cooldown) != fingerprint(cooldown):
                mismatch("容量池成员冷却语义与白板意图不一致", base + ".cooldown")
        if len(groups) != 1:
            mismatch("容量池成员不属于同一个冷却组", base + ".cooldown")
        if intent["initial_ready_in"] is None:
            mismatch("完整计算前必须明确容量初始可用队列", base + ".initial_ready_in")
        group = next(iter(groups))
        matching_initial = [row for row in initial_rows
                            if row.get("character") == intent["actor"] and row.get("group") == group]
        if len(matching_initial) != 1 or fingerprint(matching_initial[0].get("ready_in")) != fingerprint(intent["initial_ready_in"]):
            mismatch("容量初始可用队列与原生方案不一致", base + ".initial_ready_in")

    if len(used_sources) != len(sources):
        mismatch("计算结果含无法对应白板意图的额外次数来源", "action_capacity_sources")

    records = calculated["result"].get("action_capacity_compilations")
    if not isinstance(records, list) or len(records) != len(sources):
        mismatch("完整计算缺少一一对应的容量编译记录", "result.action_capacity_compilations")
    by_id = {record.get("id"): record for record in records}
    if len(by_id) != len(records) or set(by_id) != {source["id"] for source in sources}:
        mismatch("容量编译记录与实际次数来源身份不一致", "result.action_capacity_compilations")
    for intent in intents:
        source = source_for_intent[intent["id"]]
        record = by_id[source["id"]]
        if record.get("status") != "ready":
            mismatch("容量编译尚未就绪", "result.action_capacity_compilations." + source["id"])
        source_fingerprint = record.get("source_fingerprint")
        required = (record.get("required_confirmation") or {}).get("accepted_fingerprint")
        if (not isinstance(source_fingerprint, str)
                or source.get("accepted_fingerprint") != source_fingerprint
                or required != source_fingerprint):
            mismatch("容量来源确认与本次编译指纹不一致", "action_capacity_sources." + source["id"] + ".accepted_fingerprint")
    result = calculated["result"]
    receipt = calculated["result_receipt"]
    check = result.get("rotation_check") or {}
    if (not isinstance(result.get("action_capacity_execution"), dict)
            or not isinstance(receipt.get("action_capacities"), dict)
            or not isinstance(check.get("action_capacity_consumption"), dict)):
        mismatch("完整计算缺少容量执行证明或消费账", "result.action_capacity_execution")
    return {**bound, "capacity_intent_count": len(intents), "capacity_source_count": len(sources),
            "_delivery_integrity": delivery["integrity"]}


@diagnosed('whiteboard', 'read-whiteboard-result-v1')
def read_whiteboard_result(board, path, expected_file_sha=None):
    from genshin_app.equipment.delivery import (
        PACKAGE_KIND,
        PACKAGE_SCHEMA,
    )
    package, sha, path = _read(path, CALCULATED_ARTIFACT_BYTES)
    if expected_file_sha is not None and sha != expected_file_sha:
        _fail("预览后文件已更改，请重新打开报告。", reason="file_changed")
    if not isinstance(package, dict) or package.get("package_schema") != PACKAGE_SCHEMA or package.get("kind") != PACKAGE_KIND:
        _fail("白板回流只接受equipment deliver生成的完整calculated.json", reason="invalid_calculation_package")
    calculated = package["calculated"]
    bound = validate_whiteboard_capacity_result(board, calculated)
    _equal(package.get("integrity"), bound.pop("_delivery_integrity"), "package.integrity")
    return {**bound, "path": path, "file_sha": sha, "calculated": calculated, "readonly": True,
            "validation": "full_calculation_package_and_whiteboard_intent",
            "scope": "历史计算与白板意图绑定；不保证游戏机制正确、能量检查通过或当前数据库最新"}
