"""Whiteboard reference/export and shared revision contracts preserve domain ownership."""
from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path

import pytest

from genshin_app.equipment.schema import PlanError
from genshin_app.whiteboard import (
    board_fingerprint,
    load_whiteboard,
    make_handoff,
    new_whiteboard,
    prepare_whiteboard_edit,
    save_whiteboard,
    validate_whiteboard_revision_transition,
)
from genshin_app.whiteboard_reference_gateway import WhiteboardReferenceGateway
from genshin_app.whiteboard_revision import revision_transition_capabilities
from tests.test_whiteboard import board_fixture, provisional_board_fixture


def _independent_board_hash(board):
    intent = {key: value for key, value in board.items() if key not in ("revision", "title")}
    intent["team"] = [{key: value for key, value in actor.items() if key != "panel_basis_mode"}
                      for actor in board["team"]]
    raw = json.dumps(intent, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(raw).hexdigest()


def _handoff_before_r13_defaults(plan, board):
    """Project only the approved new defaults, retaining the original hashes.

    Accepted 4cb10a8 was replayed independently. Its complete handoff differs
    only in these two defaults and the two dependent board identities.
    Check current identities before reconstructing their old preimage; never
    ignore the rest of the embedded board, source references or rotation.
    """
    legacy = copy.deepcopy(plan)
    assert legacy.pop("direct_energy_policy") == "energy-direct-base-er-v1"
    cutoff = {"contract": "damage-report-window-v1", "mode": "last_active_damage"}
    assert legacy["rotation_model"].pop("damage_report") == cutoff
    handoff = legacy["extensions"]["whiteboard_handoff"]
    assert handoff["board"] == board
    current_hash = _independent_board_hash(board)
    assert board_fingerprint(board) == current_hash
    assert handoff["board_fingerprint"] == current_hash
    assert handoff["board"].pop("damage_report") == cutoff
    old_hash = _independent_board_hash(handoff["board"])
    assert old_hash != current_hash
    handoff["board_fingerprint"] = old_hash
    refs = legacy["equipment_inputs"]["actor.a"]["source_refs"]
    assert len(refs) == 1 and refs[0]["board_fingerprint"] == current_hash
    refs[0]["board_fingerprint"] = old_hash
    return legacy


def _handoff_before_panel_provenance(plan):
    """Verify r17 provenance corrections, then recover the old fixture view."""
    legacy = copy.deepcopy(plan)
    assumptions = legacy["manual_template"]["meta"]["assumptions"]
    assert assumptions[0] == "白板交接：固定面板按已包含武器和套装常驻属性处理，不重复加算；数值来源见各角色的面板来源记录。"
    assumptions[0] = "用户白板交接：固定面板已包含武器和套装常驻属性，不重复加算；其余机制待Agent核实。"
    for gear in legacy["equipment_inputs"].values():
        assert gear.pop("panel_provenance") == {"origin": "unknown", "entered_by": "unknown"}
        if gear.get("weapon"):
            assert gear["weapon"]["selected_by"] == "unknown"
            gear["weapon"]["selected_by"] = "user"
        assert gear["progression"]["source"] == "白板角色成长输入"
        gear["progression"]["source"] = "用户白板角色成长输入"
        for source in gear["source_refs"]:
            assert source["kind"] == "whiteboard"
            assert source["note"] == "白板保存的固定面板；未声明来源时保持未知，未知数值不等于零"
            source.update(kind="user_whiteboard", note="用户给定固定面板；未知数值未填写，不等于零")
    return legacy


@pytest.mark.parametrize("factory,expected", [
    (board_fixture, "453cef596b119bbdf1dada3c3d72ba23cd24e99de663012dae004c9f02272c80"),
    (provisional_board_fixture, "eec26f45194369777943e1d30e7e484cd472e6e149e80606a5e045188dd152fd"),
])
def test_handoff_goldens_do_not_change_after_gateway_extraction(factory, expected):
    board = factory()
    board["board_id"] = "00000000-0000-0000-0000-000000000001"
    plan = make_handoff(board)
    plan["plan_id"] = "plan.golden"
    raw = json.dumps(_handoff_before_panel_provenance(_handoff_before_r13_defaults(plan, board)), ensure_ascii=False,
                     sort_keys=True, separators=(",", ":")).encode()
    assert hashlib.sha256(raw).hexdigest() == expected


@pytest.mark.parametrize("field", ["origin", "selected_by", "note", "assumption"])
def test_historical_provenance_projection_does_not_hide_different_authority(field):
    plan = make_handoff(provisional_board_fixture() if field == "selected_by" else board_fixture())
    gear = plan["equipment_inputs"]["actor.a"]
    if field == "origin":
        gear["panel_provenance"]["origin"] = "user_panel"
    elif field == "selected_by":
        gear["weapon"]["selected_by"] = "agent"
    elif field == "note":
        gear["source_refs"][0]["note"] = "不可抹掉的另一种声明"
    else:
        plan["manual_template"]["meta"]["assumptions"][0] = "不同的场景假设"
    with pytest.raises(AssertionError):
        _handoff_before_panel_provenance(plan)


@pytest.mark.parametrize("field", ["cutoff", "handoff_identity", "equipment_identity"])
def test_handoff_historical_projection_rejects_wrong_default_or_identity(field):
    board = board_fixture()
    plan = make_handoff(board)
    if field == "cutoff":
        plan["rotation_model"]["damage_report"]["mode"] = "explicit"
    elif field == "handoff_identity":
        plan["extensions"]["whiteboard_handoff"]["board_fingerprint"] = "0" * 64
    else:
        plan["equipment_inputs"]["actor.a"]["source_refs"][0]["board_fingerprint"] = "0" * 64
    with pytest.raises(AssertionError):
        _handoff_before_r13_defaults(plan, board)


def test_gateway_only_reads_exact_source_queries(tmp_path, monkeypatch):
    from genshin_db import queries, runtime_action_admission
    path = tmp_path / "knowledge.db"
    path.write_bytes(b"not opened by the test query substitutes")
    seen = []
    def weapon(database, weapon_id, **options):
        seen.append((database, weapon_id, options))
        return {"effects": [], "source": "knowledge-only"}
    def admission(database, line, **options):
        seen.append((database, line, options))
        return {"status": "ready", "source": "knowledge-only"}
    monkeypatch.setattr(queries, "get_weapon_detail", weapon)
    monkeypatch.setattr(runtime_action_admission, "get_action_admission_profile", admission)
    gateway = WhiteboardReferenceGateway(path)
    assert gateway.weapon_available and gateway.admission_available
    assert gateway.weapon_detail(123, "release")["source"] == "knowledge-only"
    intent = {"version_line": "release", "selection": {"character_id": 1},
              "variant": "default", "model_ref": {"id": "proposal", "revision": 1}}
    assert "accepted_fingerprint" not in gateway.action_admission_profile(intent)
    assert seen[1][2] == {"selection": intent["selection"], "variant": "default",
                          "action_type_id": "action.elemental_burst", "model_ref": intent["model_ref"]}
    assert path.read_bytes() == b"not opened by the test query substitutes"


def test_revision_preparation_is_detached_and_title_does_not_change_business_hash():
    previous = board_fixture()
    candidate = copy.deepcopy(previous)
    candidate["title"] = "用户改名"
    originals = copy.deepcopy((previous, candidate))
    prepared = prepare_whiteboard_edit(previous, candidate)
    assert prepared["revision"] == previous["revision"] + 1
    assert board_fingerprint(prepared) == board_fingerprint(previous)
    assert (previous, candidate) == originals
    receipt = validate_whiteboard_revision_transition(previous, prepared, allow_revision_jump=False)
    assert receipt["fields"] == ["title"] and receipt["changed"] is True
    assert prepare_whiteboard_edit(previous, previous) == previous


@pytest.mark.parametrize("field,value", [("revision", True), ("revision", -1), ("revision", float("nan"))])
def test_invalid_revision_never_becomes_an_accepted_edit(field, value):
    previous = board_fixture()
    candidate = copy.deepcopy(previous)
    candidate[field] = value
    with pytest.raises(PlanError):
        prepare_whiteboard_edit(previous, candidate)


def test_batched_gui_proposals_and_undo_are_allowed_but_stale_changes_are_refused():
    previous = board_fixture()
    candidate = copy.deepcopy(previous)
    candidate.update(title="多次编辑", revision=10)
    validate_whiteboard_revision_transition(previous, candidate)
    candidate["title"] = previous["title"]
    assert validate_whiteboard_revision_transition(previous, candidate)["changed"] is False
    candidate.update(title="未推进版本", revision=0)
    with pytest.raises(PlanError) as caught:
        validate_whiteboard_revision_transition(previous, candidate)
    assert caught.value.reason == "whiteboard_revision_conflict"
    other = board_fixture()
    with pytest.raises(PlanError) as caught:
        prepare_whiteboard_edit(previous, other)
    assert caught.value.reason == "whiteboard_identity_conflict"


def test_store_checks_revision_after_real_sha_cas_and_preserves_explicit_file_replacement(tmp_path):
    path = tmp_path / "board.json"
    board = board_fixture()
    saved = save_whiteboard(path, board)
    original = path.read_bytes()
    changed = {**board, "title": "未推进版本"}
    with pytest.raises(PlanError) as caught:
        save_whiteboard(path, changed, saved["file_sha"])
    assert caught.value.reason == "whiteboard_revision_conflict"
    assert path.read_bytes() == original
    with pytest.raises(PlanError) as caught:
        save_whiteboard(path, changed, "0" * 64)
    assert caught.value.reason == "file_conflict"
    other = new_whiteboard()
    replaced = save_whiteboard(path, other, saved["file_sha"])
    assert replaced["document_replacement"]["previous_board_id"] == board["board_id"]
    assert "revision_transition" not in replaced
    assert load_whiteboard(path)["board"] == other
    assert replaced["revision_policy"] == revision_transition_capabilities()


def test_handoff_builder_has_no_database_or_file_write_imports():
    source = Path(__file__).parents[1] / "src/genshin_app/whiteboard_handoff.py"
    text = source.read_text("utf8")
    assert "from genshin_db" not in text
    assert "publish_files" not in text
def test_whiteboard_capabilities_describe_native_artifacts_separately_from_external_cli():
    from genshin_app.whiteboard import whiteboard_capabilities

    capabilities = whiteboard_capabilities()
    assert capabilities["default_chat_workflow"] == "artifact"
    native = capabilities["chat_workflows"]["artifact"]
    assert native["authority"] == "DSH Session Controller"
    assert native["empty_board_allowed"] is True
    assert native["focus_is_advisory"] is True
    assert "requires" not in native and "gui_method" not in native
    assert capabilities["chat_workflows"]["build"]["transport"] == "external_cli"
    assert capabilities["chat_workflows"]["calculate"]["transport"] == "external_cli"


