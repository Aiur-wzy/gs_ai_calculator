"""Whiteboard v3 action-admission intent and result binding tests."""
from __future__ import annotations

import copy
import json

import pytest
from test_action_admission_sources import (
    admission_db as _admission_db_factory,
)
from test_action_admission_sources import (
    model_ref,
    selection,
)
from test_action_admission_sources import (
    plan as native_action_admission_plan,
)

from genshin_app.equipment import service
from genshin_app.equipment.action_admission_catalog import (
    catalog_filter_from_whiteboard,
    whiteboard_action_admission_model_candidates,
)
from genshin_app.equipment.action_admission_receipt import FIELD
from genshin_app.equipment.action_admission_schema import CONTEXT
from genshin_app.equipment.action_admissions import compile_action_admissions
from genshin_app.equipment.schema import PlanError
from genshin_app.whiteboard import (
    default_panel,
    load_whiteboard,
    make_handoff,
    new_whiteboard,
    pending_items,
    v2_board_fingerprint,
    validate_result_intent,
    validate_whiteboard,
    validate_whiteboard_action_admission_result,
)


@pytest.fixture
def whiteboard_admission_db():
    generator = _admission_db_factory.__wrapped__()
    try:
        yield next(generator)
    finally:
        generator.close()


def board_fixture(*, damage_window=True):
    board = new_whiteboard()
    if not damage_window:
        # Legacy-file comparisons deliberately start without the newer optional
        # reporting intent; never strip a real saved user's requested window.
        board.pop("damage_report")
    board["team"] = [{
        "id": "a", "db_character_id": selection()["character_id"], "name": "工程角色",
        "level": 90, "constellation": 0, "weapon": None, "artifact_sets": [],
        "panel": default_panel(), "notes": "",
    }]
    board["actions"] = [{
        "id": "cast", "character": "a", "action_type_id": "action.elemental_burst",
        "label": "工程元素爆发", "start": 0, "variant": "default", "notes": "",
    }]
    return board


def intent(*, context=None, initial=None):
    return {
        "id": "admission.q", "actor": "a", "action_id": "cast",
        "version_line": "release", "selection": selection(), "variant": "default",
        "model_ref": model_ref(), "initial_ready_in": copy.deepcopy(initial),
        "context": copy.deepcopy(context),
    }


def test_v3_defaults_and_v2_file_upgrade_keep_unknown_admission_state(tmp_path):
    board = new_whiteboard()
    assert board["schema_version"] == 3
    assert board["action_admission_intents"] == []
    board.pop("damage_report")
    old = copy.deepcopy(board)
    old["schema_version"] = 2
    old.pop("action_admission_intents")
    path = tmp_path / "v2.whiteboard.json"
    path.write_text(json.dumps(old, ensure_ascii=False), "utf-8")

    loaded = load_whiteboard(path)

    assert loaded["source_schema_version"] == 2
    assert loaded["board"]["schema_version"] == 3
    assert loaded["board"]["action_admission_intents"] == []
    assert json.loads(path.read_text("utf-8"))["schema_version"] == 2


def test_v2_handoff_fingerprint_reopens_only_while_admission_intents_stay_empty():
    board = board_fixture(damage_window=False)
    plan = make_handoff(board)
    saved_v2 = copy.deepcopy(board)
    saved_v2["schema_version"] = 2
    saved_v2.pop("action_admission_intents")
    handoff = plan["extensions"]["whiteboard_handoff"]
    handoff.update(
        schema_version=2,
        board=saved_v2,
        board_fingerprint=v2_board_fingerprint(board),
    )
    handoff.pop("action_admission_intents")
    handoff.pop("action_admission_context_state")
    handoff.pop("action_admission_proposals")

    assert validate_result_intent(board, plan)["user_action_count"] == 1

    board["action_admission_intents"] = [intent()]
    with pytest.raises(PlanError, match="接纳|意图"):
        validate_result_intent(board, plan)


@pytest.mark.parametrize("schema_version", [1, 2])
def test_legacy_file_cannot_silently_drop_new_reporting_intent(tmp_path, schema_version):
    old = board_fixture()
    old["schema_version"] = schema_version
    old.pop("action_admission_intents")
    if schema_version == 1:
        old.pop("capacity_intents")
    path = tmp_path / "not-a-legacy-document.json"
    path.write_text(json.dumps(old, ensure_ascii=False), "utf-8")
    with pytest.raises(PlanError) as caught:
        load_whiteboard(path)
    assert caught.value.reason == "invalid_whiteboard"
    assert json.loads(path.read_text("utf-8"))["damage_report"] == old["damage_report"]


@pytest.mark.parametrize("mutate", [
    lambda board: board["action_admission_intents"][0].update(version_line="beta"),
    lambda board: board["action_admission_intents"][0].update(variant="tap"),
    lambda board: board["action_admission_intents"][0].update(initial_ready_in=[0, 1]),
    lambda board: board["action_admission_intents"][0].update(context={**CONTEXT, "mode": "abyss"}),
    lambda board: board["action_admission_intents"][0]["selection"].update(constellation=1),
    lambda board: board["actions"][0].update(variant=""),
])
def test_admission_intent_scope_is_strict(mutate):
    board = board_fixture()
    board["action_admission_intents"] = [intent()]
    mutate(board)
    with pytest.raises(PlanError):
        validate_whiteboard(board)


def test_null_is_pending_and_explicit_preset_is_the_only_complete_initial_state():
    board = board_fixture()
    board["action_admission_intents"] = [intent()]
    validate_whiteboard(board)
    codes = {row["code"] for row in pending_items(board)}
    assert {"action_admission_context_unknown", "action_admission_initial_state_unknown"} <= codes

    board["action_admission_intents"] = [intent(context=CONTEXT, initial=[])]
    validate_whiteboard(board)
    codes = {row["code"] for row in pending_items(board)}
    assert "action_admission_context_unknown" not in codes
    assert "action_admission_initial_state_unknown" not in codes


def test_catalog_uses_only_typed_whiteboard_identity_and_returns_exact_model(
        whiteboard_admission_db):
    db, _ = whiteboard_admission_db
    board = board_fixture()
    partial = catalog_filter_from_whiteboard(board, "a", "cast")
    assert partial["observed"]["target"] == {"slot": "burst"}
    assert {"target.skill_depot_id", "target.skill_id"} <= set(partial["unknown_paths"])

    board["action_admission_intents"] = [intent()]
    exact = catalog_filter_from_whiteboard(board, "a", "cast")
    assert exact["observed"]["target"] == {
        "slot": "burst", "skill_depot_id": selection()["skill_depot_id"],
        "skill_id": selection()["skill_id"],
    }
    page = whiteboard_action_admission_model_candidates(board, "a", "cast", db)
    assert page["counts"]["exact"] == 1
    assert page["items"][0]["model_ref"] == model_ref()


def test_handoff_derives_non_authoritative_proposal_and_only_explicit_initial_row(
        whiteboard_admission_db):
    db, _ = whiteboard_admission_db
    board = board_fixture()
    board["action_admission_intents"] = [intent()]

    missing_database = make_handoff(board)
    assert missing_database["extensions"]["whiteboard_handoff"][
        "action_admission_proposals"
    ][0]["status"] == "pending_database"
    invalid_database_path = db.parent / "invalid.db"
    invalid_database_path.write_bytes(b"")
    invalid_database = make_handoff(board, invalid_database_path)
    assert invalid_database["extensions"]["whiteboard_handoff"][
        "action_admission_proposals"
    ][0]["status"] == "pending_model"

    pending = make_handoff(board, db)
    pending_handoff = pending["extensions"]["whiteboard_handoff"]
    assert pending_handoff["action_admission_intents"] == board["action_admission_intents"]
    assert pending_handoff["action_admission_context_state"] == "unknown"
    assert pending_handoff["action_admission_proposals"][0]["status"] == "pending_context"
    assert pending_handoff["action_admission_proposals"][0]["cooldown_group"] is not None
    assert pending["rotation_model"]["initial_cooldowns"] == []
    assert pending.get("action_admission_context") is None
    assert not pending.get("action_admission_sources")

    board["action_admission_intents"] = [intent(context=CONTEXT)]
    pending_initial = make_handoff(board, db)
    assert pending_initial["extensions"]["whiteboard_handoff"][
        "action_admission_proposals"
    ][0]["status"] == "pending_initial_state"
    assert pending_initial["rotation_model"]["initial_cooldowns"] == []

    board["action_admission_intents"] = [intent(context=CONTEXT, initial=[])]
    proposed = make_handoff(board, db)
    handoff = proposed["extensions"]["whiteboard_handoff"]
    proposal = handoff["action_admission_proposals"][0]
    assert proposed["action_admission_context"] == CONTEXT
    assert proposed["rotation_model"]["initial_cooldowns"] == [proposal["initial_cooldown"]]
    assert proposal["initial_cooldown"]["ready_in"] == []
    encoded = json.dumps(proposal, ensure_ascii=False)
    assert "accepted_fingerprint" not in encoded
    assert "action_admission_source" not in encoded
    assert proposed["rotation_model"]["abilities"][0]["cooldown"] is None


def _bound_calculated(board, db):
    plan = make_handoff(board, db)
    handoff = plan["extensions"]["whiteboard_handoff"]
    proposal = handoff["action_admission_proposals"][0]
    source_fingerprint = "a" * 64
    source = {**copy.deepcopy(proposal["binding"]),
              "accepted_fingerprint": source_fingerprint}
    plan["action_admission_sources"] = [source]
    ability_id = proposal["ability_id"]
    group = proposal["cooldown_group"]
    record = {
        "id": source["id"], "ability_id": ability_id, "status": "ready",
        "source_fingerprint": source_fingerprint,
        "required_confirmation": {"accepted_fingerprint": source_fingerprint},
        "compiled_values": {"cooldown": {"group": group}},
    }
    runtime = {"run_id": "0:certificate", "action_id": "cast",
               "binding_id": source["id"], "status": "committed"}
    parent = {"type": "action", "action_id": "cast", "ability_id": ability_id,
              "action_admission": {"status": "committed", "binding_id": source["id"],
                                   "certificate": {"run_id": runtime["run_id"]}}}
    calculated = {
        "plan": plan,
        "result": {
            "action_admission_compilations": [record], FIELD: {},
            "rotation_check": {"event_log": [parent],
                               "action_admission_execution": {"receipts": [runtime]}},
        },
        "result_receipt": {"action_admissions": {}},
    }
    return calculated


def test_complete_result_binds_intent_through_committed_runtime_receipt(
        whiteboard_admission_db, monkeypatch):
    db, _ = whiteboard_admission_db
    board = board_fixture()
    board["action_admission_intents"] = [intent(context=CONTEXT, initial=[])]
    calculated = _bound_calculated(board, db)
    monkeypatch.setattr(
        "genshin_app.equipment.delivery.validate_delivery_calculated",
        lambda *_args, **_kwargs: {"integrity": {"fixture": True}},
    )

    checked = validate_whiteboard_action_admission_result(board, calculated)
    assert checked["action_admission_committed_count"] == 1

    calculated["result"]["rotation_check"]["action_admission_execution"]["receipts"][0][
        "status"
    ] = "rejected"
    with pytest.raises(PlanError) as error:
        validate_whiteboard_action_admission_result(board, calculated)
    assert error.value.reason == "whiteboard_action_admission_result_mismatch"


@pytest.mark.parametrize(("context", "initial"), [
    (None, []),
    (CONTEXT, None),
])
def test_complete_result_cannot_close_with_unknown_context_or_initial_queue(
        whiteboard_admission_db, monkeypatch, context, initial):
    db, _ = whiteboard_admission_db
    board = board_fixture()
    board["action_admission_intents"] = [intent(context=context, initial=initial)]
    calculated = _bound_calculated(board, db)
    monkeypatch.setattr(
        "genshin_app.equipment.delivery.validate_delivery_calculated",
        lambda *_args, **_kwargs: {"integrity": {"fixture": True}},
    )

    with pytest.raises(PlanError) as error:
        validate_whiteboard_action_admission_result(board, calculated)

    assert error.value.reason == "whiteboard_action_admission_result_mismatch"


def test_real_calculation_replays_full_whiteboard_admission_chain(
        whiteboard_admission_db, monkeypatch):
    db, _ = whiteboard_admission_db
    board = board_fixture()
    native = native_action_admission_plan()
    native["manual_template"]["meta"] = {
        "name": board["title"], "version_line": "release",
    }
    for key, value in native["manual_template"]["characters"][0]["stats"].items():
        if key != "dmg_bonus":
            board["team"][0]["panel"][key] = value
    board["action_admission_intents"] = [intent(context=CONTEXT, initial=[])]
    handoff = make_handoff(board, db)
    native["extensions"] = copy.deepcopy(handoff["extensions"])
    native["equipment_inputs"] = copy.deepcopy(handoff["equipment_inputs"])
    native["rotation_model"]["damage_report"] = copy.deepcopy(
        handoff["rotation_model"]["damage_report"])
    equipment_version = {
        "version_line": "release", "upstream_commit": "f" * 40,
        "equipment_schema_version": 1, "built_at": "2026-10-15T00:00:00+00:00",
    }
    native["equipment_inputs"]["a"]["data_version"] = copy.deepcopy(equipment_version)
    monkeypatch.setattr(
        "genshin_app.equipment.composer.queries.get_meta",
        lambda _path: {"datasets": {"equipment": {"lines": {"release": {
            "state": "ready", "upstream_commit": equipment_version["upstream_commit"],
            "schema_version": 1, "built_at": equipment_version["built_at"],
        }}}}},
    )
    monkeypatch.setattr(
        "genshin_app.equipment.composer.queries.get_character_detail",
        lambda *_args, **_kwargs: {"character": {"id": selection()["character_id"]},
                                   "annotations": []},
    )

    _, records, _ = compile_action_admissions(native, db, lambda *_args: None)
    native["action_admission_sources"][0]["accepted_fingerprint"] = records[0][
        "source_fingerprint"
    ]
    calculated = service.run_plan_calc(native, db, capture_display=True)

    checked = validate_whiteboard_action_admission_result(board, calculated)

    assert checked["action_admission_committed_count"] == 1
    assert calculated["result"]["rotation_check"]["action_admission_execution"][
        "committed_actions"
    ] == 1
