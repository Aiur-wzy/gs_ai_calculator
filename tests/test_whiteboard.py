"""Whiteboard drafts never impersonate complete calculations or overwrite inputs."""
from __future__ import annotations

import copy
import json

import pytest

from genshin_app.cli import main
from genshin_app.equipment import compose_equipment_plan
from genshin_app.equipment.files import file_sha
from genshin_app.equipment.provisional_entities import (
    ATTESTATION_CONTRACT,
    LOADOUT_VALUES_CONTRACT,
    authorization_basis_fingerprint,
    build_usage_receipt,
    entity_payload_fingerprint,
    resolve_entity_ref,
    validate_usage_receipt,
)
from genshin_app.equipment.provisional_entities import (
    CONTRACT as PROVISIONAL_ENTITY_CONTRACT,
)
from genshin_app.equipment.schema import PlanError, new_plan, validate_plan
from genshin_app.gui.api import GuiApi
from genshin_app.whiteboard import (
    board_fingerprint,
    default_panel,
    export_handoff,
    load_whiteboard,
    make_handoff,
    new_whiteboard,
    prepare_whiteboard_edit,
    read_whiteboard_result,
    save_whiteboard,
    validate_result_intent,
    validate_whiteboard,
)


def board_fixture():
    board = new_whiteboard()
    board["team"] = [{"id": "actor.a", "db_character_id": 1, "name": "工程角色",
                      "level": 90, "constellation": 0, "weapon": None, "artifact_sets": [],
                      "panel": default_panel(), "notes": "虚构测试，不代表游戏事实"}]
    board["actions"] = [{"id": "action.e", "character": "actor.a", "action_type_id": "action.elemental_skill",
                         "label": "元素战技", "start": 1.25, "variant": "tap", "notes": ""}]
    return board


def provisional_entity(entity_type, provisional_id, values):
    return {
        "contract": PROVISIONAL_ENTITY_CONTRACT,
        "provisional_id": provisional_id,
        "entity_type": entity_type,
        "authorization_kind": "database_missing",
        "display_name": "临时" + entity_type,
        "version_claim": {"version_line": "release", "game_version": "7.0"},
        "values": values,
        "source_refs": [{
            "kind": "agent_research", "reference": "fixture://reviewed",
            "status": "verified", "fingerprint": "a" * 64,
            "note": "工程测试夹具",
        }],
        "reason": "数据库缺失", "necessity": "当前方案需要",
        "impact": "影响当前配装", "risk": "仅限当前方案",
        "alternatives": ["等待数据库更新"], "database_lookup": None,
        "public_data_diff": None, "scope": "current_plan_only",
        "database_authority": False, "payload_fingerprint": None,
        "attestation": None, "approval": None,
    }


def provisional_board_fixture():
    board = new_whiteboard()
    board["provisional_entities"] = [
        provisional_entity("character", "local.character.a", {
            "contract": LOADOUT_VALUES_CONTRACT, "weapon_type": "claymore",
            "panel_basis": [{
                "level": 90, "base_atk": 300, "base_hp": 12000,
                "base_def": 700,
            }], "extensions": {},
        }),
        provisional_entity("weapon", "local.weapon.a", {
            "contract": LOADOUT_VALUES_CONTRACT, "weapon_type": "claymore",
            "panel_basis": [{"level": 90, "ascension": 6, "base_atk": 600}],
            "passive_treatment": "included_in_panel",
            "passive_note": "固定面板已经包含武器常驻效果", "extensions": {},
        }),
        provisional_entity("artifact_set", "local.set.a", {
            "contract": LOADOUT_VALUES_CONTRACT,
            "thresholds": [{
                "required_count": 2, "treatment": "included_in_panel",
                "note": "固定面板已经包含两件套常驻效果",
            }], "extensions": {},
        }),
    ]
    board["team"] = [{
        "id": "actor.a", "db_character_id": None,
        "character_ref": {"kind": "provisional", "entity_type": "character",
                          "provisional_id": "local.character.a"},
        "name": "临时角色", "level": 90, "constellation": 0,
        "weapon": {
            "weapon_id": None,
            "entity_ref": {"kind": "provisional", "entity_type": "weapon",
                           "provisional_id": "local.weapon.a"},
            "name": "临时武器", "level": 90, "ascension": 6,
            "refinement": 1,
        },
        "artifact_sets": [{
            "set_id": None,
            "entity_ref": {"kind": "provisional", "entity_type": "artifact_set",
                           "provisional_id": "local.set.a"},
            "name": "临时套装", "count": 2,
        }],
        "panel": {**default_panel(), "atk": 2000, "base_atk": 900,
                  "base_hp": 12000, "base_def": 700},
        "notes": "临时公共资料必须由原生Agent当前会话重新授权",
    }]
    board["actions"] = [{
        "id": "action.e", "character": "actor.a",
        "action_type_id": "action.elemental_skill", "label": "元素战技",
        "start": 1, "variant": "tap", "notes": "",
    }]
    return board


def test_empty_draft_save_cas_external_change_and_roundtrip(tmp_path):
    board = new_whiteboard()
    path = tmp_path / "空白.whiteboard.json"
    saved = save_whiteboard(path, board)
    assert load_whiteboard(path)["board"] == board
    assert {row["code"] for row in saved["pending"]} == {"empty_team", "empty_actions", "agent_model_required"}
    board = prepare_whiteboard_edit(board, {**board, "title": "我的轴"})
    save_whiteboard(path, board, saved["file_sha"])
    with pytest.raises(PlanError, match="外部修改"):
        save_whiteboard(path, board, saved["file_sha"])
    assert path.with_name(path.name + ".previous").is_file()


def test_revision_title_do_not_stale_but_business_change_does():
    board = board_fixture()
    before = board_fingerprint(board)
    board.update(revision=5, title="仅改名")
    assert board_fingerprint(board) == before
    board["actions"][0]["start"] = 2
    assert board_fingerprint(board) != before


@pytest.mark.parametrize(("path", "value"), [
    (("duration",), 0), (("duration",), True), (("duration",), float("nan")),
    (("duration",), 4000), (("actions", 0, "start"), 20), (("actions", 0, "start"), 0.0000001),
    (("actions", 0, "character"), "unknown"), (("actions", 0, "action_type_id"), "event.damage"),
    (("team", 0, "constellation"), 7), (("team", 0, "panel", "atk"), -1),
    (("team", 0, "panel", "atk"), False), (("team", 0, "panel", "dmg_bonus", "ice"), 1),
])
def test_reject_nonfinite_outside_axis_and_event_edits(path, value):
    board = board_fixture()
    target = board
    for key in path[:-1]:
        target = target[key]
    target[path[-1]] = value
    with pytest.raises(PlanError):
        validate_whiteboard(board)


def test_duplicate_actor_action_and_unsorted_time_rejected():
    board = board_fixture()
    board["team"].append(copy.deepcopy(board["team"][0]))
    with pytest.raises(PlanError, match="重复"):
        validate_whiteboard(board)
    board["team"].pop()
    board["actions"].append(copy.deepcopy(board["actions"][0]))
    with pytest.raises(PlanError, match="重复"):
        validate_whiteboard(board)
    board["actions"][-1].update(id="second", start=0)
    with pytest.raises(PlanError, match="排序"):
        validate_whiteboard(board)


def test_native_and_database_cannot_be_overwritten_even_with_known_sha(tmp_path):
    path = tmp_path / "original.json"
    path.write_text(json.dumps(new_plan()), encoding="utf8")
    before = path.read_bytes()
    with pytest.raises(PlanError):
        save_whiteboard(path, board_fixture(), file_sha(path))
    assert path.read_bytes() == before
    database = tmp_path / "database.json"
    save_whiteboard(database, board_fixture())
    with pytest.raises(PlanError, match="不能覆盖"):
        save_whiteboard(database, board_fixture(), file_sha(database), db_path=database)


def test_handoff_preserves_unknowns_and_is_not_ready(tmp_path):
    board = board_fixture()
    board["team"][0]["panel"].update(atk=1234, crit_rate=.6)
    plan = make_handoff(board)
    assert validate_plan(plan)["valid"]
    assert plan["plan_version"] == 3 and plan["mechanics_model"] is None
    assert plan["manual_template"]["hits"] == []
    assert plan["manual_template"]["characters"][0]["stats"] == {"atk": 1234, "crit_rate": .6, "dmg_bonus": {}}
    rotation = plan["rotation_model"]
    assert rotation["abilities"][0]["cooldown"] is None
    assert rotation["abilities"][0]["energy"] is None
    assert rotation["actions"][0]["occupation_end"] is None
    assert rotation["characters"][0]["energy_capacity"] is None
    assert rotation["actions"][0]["repeat"] == {"mode": "once"}
    assert compose_equipment_plan(plan, tmp_path / "missing.db")["state"] == "pending"
    assert validate_result_intent(board, plan)["user_action_count"] == 1


def test_provisional_loadout_roundtrip_handoff_and_fail_closed_without_session(tmp_path):
    board = provisional_board_fixture()
    path = tmp_path / "provisional.whiteboard.json"
    save_whiteboard(path, board)
    assert load_whiteboard(path)["board"] == board

    plan = make_handoff(board, tmp_path / "missing.db")
    assert validate_plan(plan)["valid"] is True
    equipment = plan["equipment_inputs"]["actor.a"]
    assert equipment["character_ref"] is None
    assert equipment["character_entity_ref"]["provisional_id"] == "local.character.a"
    assert equipment["weapon"]["weapon_id"] is None
    assert equipment["weapon"]["entity_ref"]["provisional_id"] == "local.weapon.a"
    assert equipment["artifact_sets"][0]["set_id"] is None
    assert validate_result_intent(board, plan)["user_action_count"] == 1

    report = compose_equipment_plan(plan, tmp_path / "missing.db")
    assert report["state"] == "pending"
    assert {row["code"] for row in report["missing_inputs"]} >= {
        "provisional_entity_session_required",
    }
    assert all(
        row["code"] != "missing_input" or not row["path"].startswith("equipment_inputs")
        for row in report["missing_inputs"]
    )


def test_provisional_whiteboard_rejects_forged_database_id_and_cross_board_ref():
    board = provisional_board_fixture()
    board["team"][0]["db_character_id"] = 10000001
    with pytest.raises(PlanError) as caught:
        validate_whiteboard(board)
    assert caught.value.reason == "entity_ref_fake_database_id"

    board = provisional_board_fixture()
    board["team"][0]["character_ref"]["provisional_id"] = "other.plan.character"
    with pytest.raises(PlanError) as caught:
        validate_whiteboard(board)
    assert caught.value.reason == "entity_ref_registry_missing"


def test_mixed_database_and_provisional_loadouts_preserve_discriminated_refs():
    provisional_character = provisional_board_fixture()
    provisional_character["team"][0]["weapon"] = {
        "weapon_id": 123,
        "entity_ref": {"kind": "database", "entity_type": "weapon",
                       "database_id": 123, "version_line": "release"},
        "name": "数据库武器", "level": 90, "ascension": 6,
        "refinement": 1,
    }
    first = make_handoff(provisional_character)
    assert validate_plan(first)["valid"] is True
    assert first["equipment_inputs"]["actor.a"]["character_entity_ref"]["kind"] == "provisional"
    assert first["equipment_inputs"]["actor.a"]["weapon"]["entity_ref"]["kind"] == "database"

    database_character = provisional_board_fixture()
    actor = database_character["team"][0]
    actor["db_character_id"] = 456
    actor["character_ref"] = {
        "kind": "database", "entity_type": "character",
        "database_id": 456, "version_line": "release",
    }
    second = make_handoff(database_character)
    assert validate_plan(second)["valid"] is True
    assert second["equipment_inputs"]["actor.a"]["character_entity_ref"]["kind"] == "database"
    assert second["equipment_inputs"]["actor.a"]["weapon"]["entity_ref"]["kind"] == "provisional"


def test_formal_composer_resolves_mixed_loadout_and_emits_usage_audit(tmp_path):
    board = provisional_board_fixture()
    # Keep the character and set provisional while using an ordinary database
    # weapon ref only at the schema boundary in a separate assertion below.
    plan = make_handoff(board, tmp_path / "missing.db")
    database_fingerprint = "d" * 64
    lookup_ledger = {}
    for index, row in enumerate(plan["provisional_entities"]):
        lookup = {
            "contract": "public-entity-database-lookup-receipt-v1",
            "receipt_id": f"lookup.{index}", "issued_by": "app_database_query",
            "status": "not_found",
            "query": {
                "kind": row["entity_type"], "version_line": "release",
                "exact_name": row["display_name"],
            },
            "database_snapshot_fingerprint": database_fingerprint,
            "observed_candidates": [], "message": "精确查询无结果",
            "issued_at": "2026-10-28T00:00:00Z",
        }
        row["database_lookup"] = lookup
        row["payload_fingerprint"] = entity_payload_fingerprint(row)
        lookup_ledger[lookup["receipt_id"]] = copy.deepcopy(lookup)
    basis = authorization_basis_fingerprint(plan)
    attestation_ledger = {}
    for index, row in enumerate(plan["provisional_entities"]):
        attestation = {
            "contract": ATTESTATION_CONTRACT,
            "attestation_id": f"attestation.{index}",
            "status": "agent_reported",
            "issued_by": "app_agent_materialization",
            "plan_id": plan["plan_id"], "candidate_revision": 0,
            "stage_revision": 0,
            "authorization_basis_fingerprint": basis,
            "database_snapshot_fingerprint": database_fingerprint,
            "payload_fingerprint": row["payload_fingerprint"],
            "disclosed_at": "2026-10-28T00:00:00Z",
        }
        row["attestation"] = attestation
        attestation_ledger[attestation["attestation_id"]] = copy.deepcopy(attestation)

    class Session:
        def resolve_provisional_entity_ref(self, *, ref, consumer):
            return resolve_entity_ref(
                plan, ref, consumer=consumer,
                database_snapshot_fingerprint=database_fingerprint,
                candidate_revision=0, stage_revision=0, approval_ledger={},
                database_lookup_ledger=lookup_ledger,
                agent_attestation_ledger=attestation_ledger,
            )

    resolved_uses = []
    report = compose_equipment_plan(
        plan, tmp_path / "missing.db", candidate_session=Session(),
        _formal_provisional=True, _resolved_provisional_uses=resolved_uses,
    )
    assert len(resolved_uses) == 3
    assert report["result_fidelity"] == (
        "approximate_with_provisional_entities"
    )
    assert len(report["provisional_loadout_audit"]) == 3
    assert {row["code"] for row in report["missing_inputs"]}.isdisjoint({
        "provisional_entity_session_required", "provisional_entity_unresolved",
    })

    receipt = build_usage_receipt(
        plan, resolved_uses, candidate_revision=0, stage_revision=0,
        database_snapshot_fingerprint=database_fingerprint,
    )
    assert len(receipt["entities"]) == 3
    assert validate_usage_receipt(
        receipt, plan, candidate_revision=0, stage_revision=0,
        database_snapshot_fingerprint=database_fingerprint,
    ) == receipt


def test_handoff_outputs_are_saved_together_and_never_replace_previous(tmp_path):
    board = board_fixture()
    result = export_handoff(board, tmp_path / "handoff")
    assert len(result["published_paths"]) == 3
    assert load_whiteboard(result["board_path"])["board"] == board
    with pytest.raises(PlanError, match="已存在"):
        export_handoff(board, tmp_path / "handoff")
    with pytest.raises(PlanError, match="至少"):
        export_handoff(new_whiteboard(), tmp_path / "empty")
    assert not (tmp_path / "empty").exists()


@pytest.mark.parametrize("mode", ["database", "manual"])
def test_basis_mode_roundtrip_and_handoff_preserve_fixed_numbers(tmp_path, mode):
    board = board_fixture()
    original_fingerprint = board_fingerprint(board)
    actor = board["team"][0]
    actor["panel_basis_mode"] = mode
    assert board_fingerprint(board) == original_fingerprint
    actor["panel"].update(atk=2000, base_atk=123.456, base_hp=12000, base_def=700)
    path = tmp_path / "basis.whiteboard.json"
    save_whiteboard(path, board)
    assert load_whiteboard(path)["board"] == board
    plan = make_handoff(board)
    assert validate_plan(plan)["valid"]
    assert plan["equipment_inputs"][actor["id"]]["panel_basis_mode"] == mode
    assert plan["manual_template"]["characters"][0]["stats"]["base_atk"] == 123.456
    assert plan["manual_template"]["characters"][0]["stats"]["atk"] == 2000
    validate_result_intent(board, plan)
    plan["equipment_inputs"][actor["id"]]["panel_basis_mode"] = "manual" if mode == "database" else "database"
    with pytest.raises(PlanError, match="用户"):
        validate_result_intent(board, plan)


@pytest.mark.parametrize("other_mode", [None, "database"])
def test_handoff_snapshot_cannot_contradict_explicit_manual_mode(other_mode):
    board = board_fixture()
    board["team"][0]["panel_basis_mode"] = "manual"
    plan = make_handoff(board)
    actor = plan["extensions"]["whiteboard_handoff"]["board"]["team"][0]
    if other_mode is None:
        actor.pop("panel_basis_mode")
    else:
        actor["panel_basis_mode"] = other_mode
    with pytest.raises(PlanError, match="用户"):
        validate_result_intent(board, plan)


@pytest.mark.parametrize("mode", [None, "auto", 0, {}, []])
def test_invalid_basis_mode_rejected_by_both_contracts(mode):
    board = board_fixture()
    plan = make_handoff(board)
    board["team"][0]["panel_basis_mode"] = mode
    with pytest.raises(PlanError):
        validate_whiteboard(board)
    plan["equipment_inputs"]["actor.a"]["panel_basis_mode"] = mode
    with pytest.raises(PlanError):
        validate_plan(plan)


@pytest.mark.parametrize("target", ["time", "actor", "duration", "panel", "constellation", "repeat", "type", "variant"])
def test_copied_handoff_hash_does_not_authorize_actual_input_edits(target):
    board = board_fixture()
    board["team"][0]["panel"]["atk"] = 100
    plan = make_handoff(board)
    if target == "time":
        plan["rotation_model"]["actions"][0]["start"] = 2
    elif target == "actor":
        plan["equipment_inputs"]["actor.a"]["character_ref"]["db_character_id"] = 2
    elif target == "duration":
        plan["manual_template"]["loop_duration"] = 21
    elif target == "panel":
        plan["manual_template"]["characters"][0]["stats"]["atk"] = 200
    elif target == "constellation":
        plan["extensions"]["whiteboard_handoff"]["constellations"]["actor.a"] = 1
    elif target == "repeat":
        plan["rotation_model"]["actions"][0]["repeat"] = {"mode": "each_cycle"}
    else:
        plan["rotation_model"]["abilities"][0]["action_type_id" if target == "type" else "variant"] = (
            "action.elemental_burst" if target == "type" else "hold")
    with pytest.raises(PlanError):
        validate_result_intent(board, plan)


def test_same_time_order_and_additional_attack_rejected_but_reordered_storage_allowed():
    board = board_fixture()
    board["actions"].append({**board["actions"][0], "id": "action.second"})
    plan = make_handoff(board)
    plan["rotation_model"]["actions"].reverse()
    assert validate_result_intent(board, plan)["user_action_count"] == 2
    plan["rotation_model"]["actions"][0]["order"] = -1
    with pytest.raises(PlanError, match="顺序"):
        validate_result_intent(board, plan)
    plan = make_handoff(board)
    plan["rotation_model"]["actions"].append({**plan["rotation_model"]["actions"][0], "id": "extra", "start": 3, "order": 10})
    plan["extensions"]["whiteboard_handoff"]["auxiliary_action_ids"] = ["extra"]
    with pytest.raises(PlanError, match="新的攻击"):
        validate_result_intent(board, plan)


def test_equivalent_number_encoding_is_allowed_but_microsecond_change_is_not():
    board = board_fixture()
    board["actions"][0]["start"] = 1
    board["team"][0]["panel"]["atk"] = 100
    board["team"][0]["panel"]["dmg_bonus"]["cryo"] = 0
    plan = make_handoff(board)
    plan["manual_template"]["loop_duration"] = float(board["duration"])
    plan["manual_template"]["characters"][0]["stats"].update(atk=100.0, dmg_bonus={"cryo": 0.0})
    plan["rotation_model"]["period"] = float(board["duration"])
    plan["rotation_model"]["actions"][0]["start"] = 1.0
    assert validate_result_intent(board, plan)["user_action_count"] == 1
    plan["rotation_model"]["actions"][0]["start"] += 0.000001
    with pytest.raises(PlanError):
        validate_result_intent(board, plan)


def test_unknown_weapon_state_completion_requires_notes():
    board = board_fixture()
    board["team"][0]["weapon"] = {"weapon_id": 1, "name": "工程武器", "level": None, "ascension": None, "refinement": None}
    plan = make_handoff(board)
    plan["equipment_inputs"]["actor.a"]["weapon"].update(level=90, ascension=6)
    with pytest.raises(PlanError, match="补齐未知"):
        validate_result_intent(board, plan)
    plan["extensions"]["whiteboard_handoff"]["completion_notes"] = ["工程测试：为未知武器等级/突破采用90/6"]
    assert validate_result_intent(board, plan)["user_action_count"] == 1


@pytest.mark.parametrize("variant", ["tap", "hold", "default"])
def test_unspecified_variant_can_be_completed_with_notes_only(variant):
    board = board_fixture()
    board["actions"][0]["variant"] = ""
    plan = make_handoff(board)
    plan["rotation_model"]["abilities"][0]["variant"] = variant
    with pytest.raises(PlanError, match="补齐未知"):
        validate_result_intent(board, plan)
    plan["extensions"]["whiteboard_handoff"]["completion_notes"] = ["为用户未指定的动作变体采用" + variant + "，游戏依据另附"]
    assert validate_result_intent(board, plan)["user_action_count"] == 1
    assert plan["extensions"]["whiteboard_handoff"]["board"]["actions"][0]["variant"] == ""
    assert plan["extensions"]["whiteboard_handoff"]["board_fingerprint"] == board_fingerprint(board)


def test_explicit_variant_is_locked_even_with_completion_notes():
    board = board_fixture()
    plan = make_handoff(board)
    plan["rotation_model"]["abilities"][0]["variant"] = "hold"
    plan["extensions"]["whiteboard_handoff"]["completion_notes"] = ["这条说明不能授权修改用户点按意图"]
    with pytest.raises(PlanError, match="用户白板意图"):
        validate_result_intent(board, plan)


def test_raw_results_or_plan_never_accepted_as_completed_package(tmp_path):
    path = tmp_path / "result.json"
    for value in ({"result": {"summary": {"total_damage": 9000}}}, make_handoff(board_fixture())):
        path.write_text(json.dumps(value), encoding="utf8")
        with pytest.raises(PlanError, match="完整"):
            read_whiteboard_result(board_fixture(), path)


def test_gui_uses_same_contract_and_error_envelopes(tmp_path):
    api = GuiApi(tmp_path)
    board = api.whiteboard_new()["data"]["board"]
    path = str(tmp_path / "gui.whiteboard.json")
    saved = api.whiteboard_save(path, board)
    assert saved["code"] == 0
    assert api.whiteboard_load(path)["data"]["board"] == board
    assert api.whiteboard_save(path, board)["reason"] == "file_conflict"
    assert api.whiteboard_validate({})["code"] == 1
    assert api.whiteboard_handoff(board, str(tmp_path / "handoff"))["code"] == 1
    assert api.whiteboard_read_result(board, path)["code"] == 1


def test_gui_new_whiteboard_does_not_initialize_agent_history(tmp_path):
    initialized = []

    def agent_manager_factory(_data_dir):
        initialized.append(True)
        raise AssertionError("new whiteboard must not initialize Agent history")

    api = GuiApi(tmp_path, agent_manager_factory=agent_manager_factory)

    first = api.whiteboard_new()
    second = api.whiteboard_new()

    assert first["code"] == second["code"] == 0
    assert first["data"]["board"]["board_id"] != second["data"]["board"]["board_id"]
    assert initialized == []
    assert api._agent_manager is None


def test_cli_new_validate_handoff_and_capabilities(tmp_path, capsys, accepted_current_only_cli):
    board_path = tmp_path / "board.json"
    status, created = accepted_current_only_cli("whiteboard", "new", "--output", str(board_path))
    assert status == 0
    assert created["data"]["path"] == str(board_path.resolve())
    loaded = load_whiteboard(board_path)
    save_whiteboard(board_path, board_fixture(), loaded["file_sha"])
    assert main(["whiteboard", "validate", "--input", str(board_path)]) == 0
    assert "board" not in json.loads(capsys.readouterr().out)["data"]
    # New/save and handoff consume the accepted project database through the
    # real CLI gate, isolated from another package's pending build-py pair.
    status, handoff = accepted_current_only_cli("whiteboard", "handoff", "--input", str(board_path), "--output-dir", str(tmp_path / "handoff"))
    assert status == 0
    assert len(handoff["data"]["published_paths"]) == 3
    assert main(["whiteboard", "result", "--input", str(board_path)]) == 1
    assert "--result" in capsys.readouterr().err



def test_completed_package_gui_and_cli_use_identical_return_validation(tmp_path, monkeypatch, capsys):
    from genshin_app.equipment.files import json_bytes
    from tests.test_whiteboard_review import real_package
    board, _, package, _ = real_package.__wrapped__(tmp_path, monkeypatch)
    path = tmp_path / 'calculated.json'
    path.write_bytes(json_bytes(package))
    board_path = tmp_path / 'board.json'
    save_whiteboard(board_path, board)
    assert main(['whiteboard', 'result', '--input', str(board_path), '--result', str(path)]) == 0
    result = json.loads(capsys.readouterr().out)['data']
    assert result['summary']['total_damage'] == pytest.approx(82.4625) and 'calculated' not in result
    assert GuiApi(tmp_path).whiteboard_read_result(board, str(path))['code'] == 0
