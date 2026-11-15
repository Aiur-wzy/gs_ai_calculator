"""Complete whiteboard returns must prove their declared capacity intents.

All gameplay records in this module come from the existing synthetic capacity
database fixture.  They are contract probes, not claims about a live character.
"""
# ruff: noqa: F811 - pytest fixture injection uses imported names.
from __future__ import annotations

import copy
import uuid

import pytest
from capacity_fixture import context
from test_capacity_inputs import input_plan
from test_capacity_model import (  # noqa: F401
    action_resource_synthetic_db_only,
    fake_repo,
    ready,
    source_db,
)

from genshin_app.agent_chat.candidate import CandidateSession
from genshin_app.calculation_completion import require_calculation_complete
from genshin_app.equipment.action_capacity_schema import SLOTS
from genshin_app.equipment.composer import compose_equipment_plan
from genshin_app.equipment.data_version import equipment_data_version
from genshin_app.equipment.schema import PlanError
from genshin_app.equipment.service import run_plan_calc
from genshin_app.whiteboard import (
    default_panel,
    make_handoff,
    new_whiteboard,
    validate_result_intent,
    validate_whiteboard_capacity_result,
)
from genshin_app.whiteboard_equipment import apply_whiteboard_equipment
from genshin_db.queries import get_meta
from tests.test_mechanics_execution import EVIDENCE

pytestmark = pytest.mark.usefixtures("action_resource_synthetic_db_only")


def _board():
    board = new_whiteboard()
    board.update(title="容量回流工程白板", duration=10)
    board["team"] = [{
        "id": "a", "db_character_id": 90000001, "name": "工程角色",
        "level": 90, "ascension": 6, "constellation": 1,
        "talent_base_levels": {"skill": 10},
        "weapon": {"weapon_id": 11401, "name": "工程武器", "level": 1,
                   "ascension": 0, "refinement": 1},
        "artifact_sets": [], "artifact_presence": dict.fromkeys(SLOTS, "empty"),
        "panel": default_panel(), "panel_basis_mode": "manual", "notes": "",
    }]
    board["actions"] = [{
        "id": "cast", "character": "a", "action_type_id": "action.elemental_skill",
        "label": "工程元素战技", "start": 0, "variant": "tap", "notes": "",
    }]
    board["capacity_intents"] = [{
        "id": "capacity.e", "actor": "a", "action_ids": ["cast"],
        "version_line": "release",
        "model_ref": {"id": "fixture.capacity.model", "revision": 1},
        "target": {"character_id": 90000001, "skill_depot_id": 9001,
                   "skill_id": 90012, "variant": "tap"},
        "cooldown": {"duration": 10, "start": "action_start", "recovery": "sequential"},
        "initial_ready_in": [], "context": context(),
    }]
    return board


def _calculated(db, mutation=None):
    board, plan = _board(), input_plan()
    plan["manual_template"].setdefault("meta", {}).update(
        name=board["title"], version_line="release", assumptions=[])
    plan["rotation_model"]["actions"][0]["repeat"] = {"mode": "once"}
    plan["rotation_model"]["abilities"][0]["energy"] = {"required": 0, "amount": 0}
    plan["rotation_model"]["events"] = [{
        "id": "engineering.cost", "type": "energy_spend", "t": 0, "order": 1,
        "action_id": "cast", "character": "a", "required": 0, "amount": 0,
        "repeat": {"mode": "once"}, **EVIDENCE,
    }]
    plan["equipment_inputs"]["a"]["data_version"] = equipment_data_version(
        get_meta(db), "release"
    )
    if mutation is not None:
        mutation(board, plan)
    handoff_plan = make_handoff(board)
    handoff = handoff_plan["extensions"]["whiteboard_handoff"]
    handoff["completion_notes"] = ["补齐白板未知面板并使用合成工程容量资料"]
    plan["extensions"] = {"whiteboard_handoff": handoff}
    # Preserve the actual new user's reporting intent; Agent-derived direct
    # hit references refine it without changing its mode or deadline.
    window = copy.deepcopy(handoff_plan["rotation_model"]["damage_report"])
    window["active_hit_ids"] = [row["hit_id"]
                                for row in plan["mechanics_model"]["hit_events"]
                                if row["action_id"] == "cast"]
    plan["rotation_model"]["damage_report"] = window
    # Draft binding remains usable before a source has a fresh confirmation.
    validate_result_intent(board, plan)
    first = compose_equipment_plan(plan, db)
    plan["action_capacity_sources"][0].update(
        first["action_capacity_compilations"][0]["required_confirmation"])
    confirmed = compose_equipment_plan(plan, db)
    assert confirmed["state"] == "ready", confirmed["missing_inputs"]
    calculated = run_plan_calc(confirmed["plan"], db, capture_display=True)
    assert require_calculation_complete(calculated)["calculation_status"] == "complete"
    assert calculated["result"]["damage_report_window"]["end"] == .4
    return board, calculated


def test_complete_capacity_result_binds_intent_source_and_execution_receipt(ready):
    board, calculated = _calculated(ready[0])

    checked = validate_whiteboard_capacity_result(board, calculated)

    assert checked["capacity_intent_count"] == checked["capacity_source_count"] == 1
    assert checked["user_action_count"] == 1
    assert checked["_delivery_integrity"]["calculated_fingerprint"]


def test_capacity_native_plan_cannot_drop_the_users_reporting_window(ready):
    board, calculated = _calculated(ready[0])
    changed = copy.deepcopy(calculated["plan"])
    changed["rotation_model"].pop("damage_report")
    with pytest.raises(PlanError) as caught:
        validate_result_intent(board, changed)
    assert caught.value.reason == "whiteboard_intent_mismatch"
    assert caught.value.details["path"] == "damage_report.mode"


@pytest.mark.parametrize("mutation", [
    lambda board, plan: board["capacity_intents"][0]["cooldown"].update(duration=11),
    lambda board, plan: board["capacity_intents"][0].update(initial_ready_in=[1]),
    lambda board, plan: board["capacity_intents"][0].update(context=None),
    lambda board, plan: board["capacity_intents"][0]["model_ref"].update(revision=2),
    lambda board, plan: board["capacity_intents"][0]["target"].update(skill_id=90013),
])
def test_complete_capacity_result_rejects_whiteboard_native_drift(ready, mutation):
    board, calculated = _calculated(ready[0], mutation)

    with pytest.raises(PlanError) as exc:
        validate_whiteboard_capacity_result(board, calculated)

    assert exc.value.reason == "whiteboard_capacity_result_mismatch"


def test_complete_capacity_result_rejects_source_with_an_unmapped_extra_ability(ready):
    def add_unmapped_member(_board, plan):
        original = plan["rotation_model"]["abilities"][0]
        other = copy.deepcopy(original)
        other.update(id="other-skill")
        plan["rotation_model"]["abilities"].append(other)
        plan["action_capacity_sources"][0]["ability_ids"].append("other-skill")

    board, calculated = _calculated(ready[0], add_unmapped_member)

    with pytest.raises(PlanError, match="唯一|次数来源") as exc:
        validate_whiteboard_capacity_result(board, calculated)
    assert exc.value.reason == "whiteboard_capacity_result_mismatch"


def test_generic_delivery_proof_is_checked_before_whiteboard_capacity_summary(ready):
    board, calculated = _calculated(ready[0])
    tampered = copy.deepcopy(calculated)
    tampered["result"]["rotation_check"].pop("action_capacity_consumption")

    with pytest.raises((PlanError, ValueError)):
        validate_whiteboard_capacity_result(board, tampered)


def test_whiteboard_candidate_accept_checkpoint_and_equipment_recalc_use_complete_contract(ready, tmp_path):
    db = ready[0]
    board, seed = _calculated(db)
    session = CandidateSession(uuid.uuid4().hex, tmp_path / "session", db, "whiteboard", board,
                               delivery_root=tmp_path / "deliveries")
    candidate = copy.deepcopy(seed["plan"])
    candidate["plan_id"] = session.candidate["plan_id"]
    session.replace_candidate(candidate, 0, "应用已核验的合成容量来源与计算输入")

    summary = session.calculate(1)
    accepted = session.accept("whiteboard", board, 1, summary["receipt_fingerprint"])
    loaded = CandidateSession.load(session.session_id, session.root, db, tmp_path / "deliveries")

    assert loaded.calculated is not None
    assert loaded.accept("whiteboard", board, 1, summary["receipt_fingerprint"])["result_path"] == accepted["result_path"]
    reapplied = apply_whiteboard_equipment(
        board, board, accepted["result_path"], db_path=db)
    assert reapplied["state"] == "ready", reapplied["missing_inputs"]
