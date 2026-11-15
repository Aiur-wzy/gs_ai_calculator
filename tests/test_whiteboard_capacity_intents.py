"""Whiteboard capacity choices remain drafts; discovery never grants execution."""
from __future__ import annotations

import copy
import hashlib
import json

import pytest

from genshin_app.agent_chat.board_candidate import whiteboard_schema
from genshin_app.equipment.action_capacity_catalog import (
    catalog_filter_from_whiteboard,
    whiteboard_action_capacity_model_candidates,
)
from genshin_app.equipment.action_capacity_schema import SLOTS
from genshin_app.equipment.schema import PlanError
from genshin_app.gui.api import GuiApi
from genshin_app.whiteboard import (
    board_fingerprint,
    default_panel,
    legacy_board_fingerprint,
    load_whiteboard,
    make_handoff,
    new_whiteboard,
    pending_items,
    save_whiteboard,
    validate_result_intent,
    validate_whiteboard,
    whiteboard_capabilities,
)

CONTEXT = {
    "contract": "static-action-capacity-context-v1",
    "mode": "ordinary_open_world",
    "external_capacity_effects": "none",
    "window_changes": "none",
}


def board_fixture(*, actions=1, damage_window=True):
    board = new_whiteboard()
    if not damage_window:
        # Old-schema fixtures predate the optional reporting-window intent.
        board.pop("damage_report")
    board["team"] = [{
        "id": "actor.a", "db_character_id": 1001, "name": "工程角色",
        "level": 90, "ascension": 6, "constellation": 0,
        "talent_base_levels": {"skill": 10},
        "weapon": {"weapon_id": 2001, "name": "工程武器", "level": 90,
                   "ascension": 6, "refinement": None},
        "artifact_sets": [], "artifact_presence": dict.fromkeys(SLOTS, "empty"),
        "panel": default_panel(), "notes": "",
    }]
    board["actions"] = [{
        "id": f"skill.{index}", "character": "actor.a",
        "action_type_id": "action.elemental_skill", "label": "仅供显示的中文名",
        "start": index, "variant": "tap", "notes": "",
    } for index in range(actions)]
    return board


def intent(action_ids=("skill.0",), *, ident="capacity.0", context=None):
    return {
        "id": ident, "actor": "actor.a", "action_ids": list(action_ids),
        "version_line": "release",
        "model_ref": {"id": "model.fixture", "revision": 3},
        "target": {"character_id": 1001, "skill_depot_id": 101,
                   "skill_id": 102, "variant": "tap"},
        "cooldown": None, "initial_ready_in": None,
        "context": copy.deepcopy(context),
    }


def legacy(board):
    assert "damage_report" not in board
    old = copy.deepcopy(board)
    old["schema_version"] = 1
    old.pop("capacity_intents")
    old.pop("action_admission_intents")
    return old


def test_new_whiteboard_v3_has_empty_intents_and_null_is_the_only_default():
    board = new_whiteboard()
    assert board["schema_version"] == 3
    assert board["capacity_intents"] == []
    assert board["action_admission_intents"] == []
    schema = whiteboard_schema()
    caps = whiteboard_capabilities()
    assert schema["schema_version"] == 3
    assert schema["capacity_intent"]["context"] is None
    assert caps["capacity_intents"]["new_context"] is None
    assert caps["capacity_intents"]["execution_authority"] is False
    assert caps["capacity_intents"]["result_binding"]["status"] == "enforced_on_complete_result"


def test_v1_load_upgrades_only_in_memory_keeps_raw_sha_and_explicit_save_writes_v3(tmp_path):
    raw_board = legacy(board_fixture(damage_window=False))
    raw = json.dumps(raw_board, ensure_ascii=False, indent=1).encode("utf-8")
    path = tmp_path / "legacy.whiteboard.json"
    path.write_bytes(raw)

    loaded = load_whiteboard(path)
    assert path.read_bytes() == raw
    assert loaded["file_sha"] == hashlib.sha256(raw).hexdigest()
    assert loaded["source_schema_version"] == 1
    assert loaded["board"]["schema_version"] == 3
    assert loaded["board"]["capacity_intents"] == []
    assert loaded["board"]["action_admission_intents"] == []

    saved = save_whiteboard(path, loaded["board"], loaded["file_sha"])
    persisted = json.loads(path.read_text("utf-8"))
    assert persisted["schema_version"] == 3
    assert persisted["capacity_intents"] == []
    assert persisted["action_admission_intents"] == []
    assert saved["file_sha"] != loaded["file_sha"]


def test_legacy_handoff_fingerprint_remains_bound_after_load_and_v2_save(tmp_path):
    current = board_fixture(damage_window=False)
    old = legacy(current)
    old_plan = make_handoff(current)
    old_handoff = old_plan["extensions"]["whiteboard_handoff"]
    old_handoff["schema_version"] = 1
    old_handoff["board"] = copy.deepcopy(old)
    old_handoff["board_fingerprint"] = legacy_board_fingerprint(current)
    old_handoff.pop("capacity_intents")
    old_handoff.pop("capacity_context_state")
    old_handoff.pop("action_admission_intents")
    old_handoff.pop("action_admission_context_state")
    old_handoff.pop("action_admission_proposals")
    path = tmp_path / "legacy.json"
    path.write_text(json.dumps(old, ensure_ascii=False), "utf-8")
    upgraded = load_whiteboard(path)["board"]

    assert validate_result_intent(upgraded, old_plan)["user_action_count"] == 1
    saved = save_whiteboard(path, upgraded, hashlib.sha256(path.read_bytes()).hexdigest())
    assert validate_result_intent(saved["board"], old_plan)["user_action_count"] == 1
    changed = copy.deepcopy(saved["board"])
    changed["capacity_intents"] = [intent()]
    with pytest.raises(PlanError, match="容量|意图"):
        validate_result_intent(changed, old_plan)


@pytest.mark.parametrize("mutation", [
    lambda b: b["capacity_intents"][0].update(actor="missing"),
    lambda b: b["capacity_intents"][0].update(action_ids=[]),
    lambda b: b["capacity_intents"][0].update(action_ids=["missing"]),
    lambda b: b["capacity_intents"][0].update(version_line="beta"),
    lambda b: b["capacity_intents"][0]["model_ref"].update(revision=0),
    lambda b: b["capacity_intents"][0]["target"].update(character_id=1002),
    lambda b: b["capacity_intents"][0]["target"].update(variant="hold"),
    lambda b: b["capacity_intents"][0].update(cooldown={"duration": -1, "start": "action_start", "recovery": "sequential"}),
    lambda b: b["capacity_intents"][0].update(cooldown={"duration": 10, "start": "cast", "recovery": "sequential"}),
    lambda b: b["capacity_intents"][0].update(initial_ready_in=[2, 1]),
    lambda b: b["capacity_intents"][0].update(context={**CONTEXT, "mode": "abyss"}),
])
def test_capacity_intent_strict_fields_fail_closed(mutation):
    board = board_fixture()
    board["capacity_intents"] = [intent()]
    mutation(board)
    with pytest.raises(PlanError):
        validate_whiteboard(board)


def test_action_ownership_and_non_null_context_are_globally_consistent():
    board = board_fixture(actions=2)
    board["capacity_intents"] = [
        intent(("skill.0",), ident="capacity.0", context=CONTEXT),
        intent(("skill.0",), ident="capacity.1", context=CONTEXT),
    ]
    with pytest.raises(PlanError, match="重复|拥有"):
        validate_whiteboard(board)

    board["capacity_intents"][1]["action_ids"] = ["skill.1"]
    board["capacity_intents"][1]["context"] = {**CONTEXT, "window_changes": "dynamic"}
    with pytest.raises(PlanError, match="场景"):
        validate_whiteboard(board)


def test_mixed_null_and_declared_context_stays_a_pending_draft_and_is_not_promoted():
    board = board_fixture(actions=2)
    board["capacity_intents"] = [
        intent(("skill.0",), ident="capacity.0", context=CONTEXT),
        intent(("skill.1",), ident="capacity.1", context=None),
    ]
    validate_whiteboard(board)
    assert "capacity_context_incomplete" in {row["code"] for row in pending_items(board)}
    plan = make_handoff(board)
    handoff = plan["extensions"]["whiteboard_handoff"]
    assert handoff["capacity_intents"] == board["capacity_intents"]
    assert handoff["capacity_context_state"] == "mixed_pending"
    assert not plan.get("action_capacity_sources")
    assert "action_capacity_context" not in plan


def test_handoff_preserves_intent_but_never_copies_catalog_value_or_confirmation():
    board = board_fixture()
    value = intent(context=CONTEXT)
    value["cooldown"] = {"duration": 10, "start": "action_start", "recovery": "sequential"}
    value["initial_ready_in"] = []
    board["capacity_intents"] = [value]
    plan = make_handoff(board)
    saved = plan["extensions"]["whiteboard_handoff"]["capacity_intents"][0]
    assert saved == value
    assert "accepted_fingerprint" not in saved and "capacity" not in saved
    assert not plan.get("action_capacity_sources")
    assert "action_capacity_context" not in plan


def test_whiteboard_catalog_filter_uses_only_observed_typed_fields_and_keeps_zero():
    board = board_fixture()
    board["capacity_intents"] = [intent()]
    query = catalog_filter_from_whiteboard(board, "actor.a", ["skill.0"])
    assert query["version_line"] == "release" and query["character_id"] == 1001
    observed = query["observed"]
    assert observed["progression"] == {"character_level": 90, "ascension": 6,
                                       "constellation": 0, "base_level": 10}
    assert observed["weapon"] == {"id": 2001, "level": 90}
    assert observed["artifacts"] == "all_empty" and observed["team"] == "self_only"
    assert observed["target"] == board["capacity_intents"][0]["target"]
    assert "context" not in observed
    assert {"weapon.refinement", "context"} <= set(query["unknown_paths"])
    assert "target.skill_id" not in query["unknown_paths"]


def test_whiteboard_catalog_reports_multi_member_team_and_equipped_artifacts():
    board = board_fixture()
    second = copy.deepcopy(board["team"][0])
    second.update(id="actor.b", db_character_id=1002, name="队友", weapon=None)
    board["team"].append(second)
    board["team"][0]["artifact_presence"][SLOTS[0]] = "equipped"
    board["capacity_intents"] = [intent(context=CONTEXT)]

    query = catalog_filter_from_whiteboard(board, "actor.a", ["skill.0"])

    assert query["observed"]["team"] == "not_self_only"
    assert query["observed"]["artifacts"] == "not_all_empty"
    assert query["observed"]["context"] == CONTEXT
    assert "team" not in query["unknown_paths"]


def test_whiteboard_catalog_never_infers_skill_from_label_and_requires_exact_pool_ownership():
    board = board_fixture(actions=2)
    query = catalog_filter_from_whiteboard(board, "actor.a", ["skill.0"])
    assert "target" not in query["observed"]
    assert {"target.skill_id", "target.skill_depot_id", "target.variant"} <= set(query["unknown_paths"])

    board["capacity_intents"] = [intent(("skill.0", "skill.1"))]
    with pytest.raises(PlanError, match="精确|次数池"):
        catalog_filter_from_whiteboard(board, "actor.a", ["skill.0"])


def test_whiteboard_catalog_service_and_gui_preserve_structured_errors(monkeypatch, tmp_path):
    board = board_fixture()
    board["capacity_intents"] = [intent(context=CONTEXT)]
    captured = {}

    def fake(db_path, request):
        captured.update(request)
        return {"contract": "action-capacity-model-catalog-page-v1", "items": [], "next_cursor": None}

    monkeypatch.setattr(
        "genshin_app.equipment.action_capacity_catalog.list_action_capacity_model_candidates", fake)
    page = whiteboard_action_capacity_model_candidates(
        board, "actor.a", ["skill.0"], tmp_path / "synthetic.db", page_size=7, view="matching")
    assert page["items"] == []
    assert captured["observed"]["context"] == CONTEXT
    assert (captured["page_size"], captured["view"]) == (7, "matching")

    api = GuiApi(tmp_path)
    failed = api.whiteboard_action_capacity_model_candidates(board, "missing", ["skill.0"])
    assert failed["code"] == 1 and failed["reason"] == "capacity_catalog_input"
    assert isinstance(failed["details"], dict)


def test_non_skill_action_cannot_enter_whiteboard_capacity_catalog_or_intent():
    board = board_fixture()
    board["actions"][0]["action_type_id"] = "action.elemental_burst"
    with pytest.raises(PlanError):
        catalog_filter_from_whiteboard(board, "actor.a", ["skill.0"])
    board["capacity_intents"] = [intent()]
    with pytest.raises(PlanError):
        validate_whiteboard(board)


def test_capacity_intents_change_current_business_fingerprint():
    board = board_fixture()
    before = board_fingerprint(board)
    board["capacity_intents"] = [intent()]
    assert board_fingerprint(board) != before
