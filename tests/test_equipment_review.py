"""Independent integration review regressions for native plan trust and publication."""

import copy
from contextlib import contextmanager

import pytest

from genshin_app.equipment import (
    PlanError,
    apply_data_update,
    compose_equipment_plan,
    new_plan,
    run_plan_calc,
)
from genshin_app.equipment import files as plan_files
from genshin_app.equipment.files import publish_files
from genshin_db import queries
from tests.test_equipment_plan import (  # noqa: F401
    binding,
    confirm_bindings,
    database,
    matching_plan,
    part,
    plan_fixture,
)


def _match():
    return {"id": "axis.match", "effect_ref": {"buff_id": "b", "effect_id": "e"},
            "mode": "adjust", "include_hit_ids": ["extra"], "exclude_hit_ids": [],
            "reason": "review fixture", "source_refs": [{"kind": "manual", "note": "verified fixture"}],
            "state": "pending", "basis_fingerprint": None}


def _manual_binding(ident):
    b = binding([part("gain", {"kind": "constant", "value": 0.1}, {
        "kind": "buff", "start": 0, "end": 10, "scope": "team", "target": "c",
        "effect": {"id": "gain", "benefit_type_id": "benefit.dmg_bonus.all", "zone": "dmg_bonus",
                   "stat": "all", "applies_to": {"mode": "all"}}})])
    b.update(binding_id=ident, provider_character_id="c", owner={"kind": "manual", "note": ident})
    return b


def test_match_confirmation_survives_reordering_independent_bindings(tmp_path):
    p = matching_plan()
    db = tmp_path / "unused.db"
    p["effect_bindings"] = [_manual_binding("source.a"), _manual_binding("source.b")]
    confirm_bindings(p, db)
    m = _match()
    p["rotation_tuning"]["buff_hit_matches"] = [m]
    report = compose_equipment_plan(p, db)
    old_basis = report["match_previews"][0]["basis_fingerprint"]
    m.update(state="confirmed", basis_fingerprint=old_basis)
    assert compose_equipment_plan(p, db)["state"] == "ready"
    p["effect_bindings"].reverse()
    p["revision"] += 1
    after = compose_equipment_plan(p, db)
    assert after["match_previews"][0]["basis_fingerprint"] == old_basis
    assert after["state"] == "ready"


def test_character_skill_owner_cannot_confirm_unrelated_parameter_group(tmp_path, monkeypatch):
    p = matching_plan()
    db = tmp_path / "db.sqlite"
    db.touch()
    monkeypatch.setattr(queries, "get_meta", lambda *args: {})
    monkeypatch.setattr(queries, "get_character_detail", lambda *args: {
        "character": {"id": 100}, "skills": [{"id": 101, "proud_skill_group_id": 123}],
        "talents": [], "extra_info": [], "annotations": []})
    b = _manual_binding("skill.source")
    b["owner"] = {"kind": "character_skill", "character_id": 100,
                  "version_line": "release", "proud_skill_group_id": 999999}
    p["effect_bindings"] = [b]
    try:
        confirm_bindings(p, db)
        report = compose_equipment_plan(p, db)
    except PlanError:
        return
    assert report["state"] == "pending", "Unrelated source gid must not become ready after confirmation"


def test_direct_none_scope_requires_scenario_reason(tmp_path):
    p = matching_plan()
    p["manual_template"]["buffs"][0]["effects"][0]["applies_to"] = {"mode": "none"}
    try:
        report = compose_equipment_plan(p, tmp_path / "unused.db")
    except PlanError:
        return
    assert report["state"] == "pending", "Explicit none needs a recorded scenario reason"


def test_backup_path_cannot_alias_another_output_and_destroy_its_previous_content(tmp_path):
    main, secondary = tmp_path / "plan.json", tmp_path / "plan.json.previous"
    main.write_bytes(b"previous plan")
    secondary.write_bytes(b"independent existing requested output")
    with pytest.raises(PlanError) as exc:
        publish_files([(main, b"new plan"), (secondary, b"new secondary")], overwrite=True)
    assert exc.value.reason == "file_conflict"
    assert main.read_bytes() == b"previous plan"
    assert secondary.read_bytes() == b"independent existing requested output"


def test_deep_json_draft_is_rejected_as_plan_error_not_raw_copy_exception(tmp_path):
    p = matching_plan()
    node = []
    for _ in range(600):
        node = [node]
    p["manual_template"]["meta"] = {"nested": node}
    with pytest.raises(PlanError):
        compose_equipment_plan(p, tmp_path / "unused.db")


def test_matching_record_order_does_not_change_other_confirmed_basis(tmp_path):
    p = matching_plan()
    db = tmp_path / "unused.db"
    second = copy.deepcopy(p["manual_template"]["buffs"][0]["effects"][0])
    second.update(id="e2", value=0.2)
    p["manual_template"]["buffs"][0]["effects"].append(second)
    one = _match()
    two = _match()
    two.update(id="axis.other", effect_ref={"buff_id": "b", "effect_id": "e2"})
    p["rotation_tuning"]["buff_hit_matches"] = [one, two]
    preview = compose_equipment_plan(p, db)
    for match, row in zip([one, two], preview["match_previews"], strict=True):
        match.update(state="confirmed", basis_fingerprint=row["basis_fingerprint"])
    assert compose_equipment_plan(p, db)["state"] == "ready"
    p["rotation_tuning"]["buff_hit_matches"].reverse()
    assert compose_equipment_plan(p, db)["state"] == "ready"


def test_none_reason_is_preserved_and_match_empty_needs_no_second_reason(tmp_path):
    p = matching_plan()
    db = tmp_path / "unused.db"
    e = p["manual_template"]["buffs"][0]["effects"][0]
    e["applies_to"] = {"mode": "none"}
    e["extensions"] = {"exclusion_reason": "此场景明确不计该收益"}
    result = run_plan_calc(p, db)
    assert all(not h["matched_effect_refs"] for h in result["result"]["hits"])
    assert any("此场景明确不计该收益" in text for text in result["result"]["assumptions"])
    e["applies_to"] = None
    e.pop("extensions")
    m = _match()
    m.update(mode="replace", hit_ids=[])
    m.pop("include_hit_ids")
    m.pop("exclude_hit_ids")
    p["rotation_tuning"]["buff_hit_matches"] = [m]
    preview = compose_equipment_plan(p, db)
    m.update(state="confirmed", basis_fingerprint=preview["match_previews"][0]["basis_fingerprint"])
    assert run_plan_calc(p, db)["plan"]["resolved_template"]["buffs"][0]["effects"][0]["extensions"]["exclusion_reason"] == m["reason"]


def test_generated_none_can_use_part_reason(tmp_path):
    p = matching_plan()
    b = _manual_binding("excluded.effect")
    b["parts"][0]["delivery"]["effect"]["applies_to"] = {"mode": "none"}
    b["parts"][0]["reason"] = "该轴未触发此条件"
    p["effect_bindings"] = [b]
    db = tmp_path / "unused.db"
    confirm_bindings(p, db)
    report = compose_equipment_plan(p, db)
    assert report["state"] == "ready"
    assert any("该轴未触发此条件" in s for s in report["assumptions"])


@pytest.mark.parametrize("group,skill,valid", [(123, None, True), (321, None, True), (321, 101, False)])
def test_parameter_group_can_belong_to_extra_but_must_agree_with_explicit_skill(tmp_path, monkeypatch, group, skill, valid):
    p = matching_plan()
    db = tmp_path / "fixture.db"
    db.touch()
    monkeypatch.setattr(queries, "get_meta", lambda *args: {})
    monkeypatch.setattr(queries, "get_character_detail", lambda *args: {
        "character": {"id": 100}, "skills": [{"id": 101, "proud_skill_group_id": 123}],
        "extra_info": [{"source_id": 222, "proud_skill_group_id": 321}], "talents": [], "annotations": []})
    b = _manual_binding("exact.source")
    b["owner"] = {"kind": "character_skill", "character_id": 100,
                  "version_line": "release", "proud_skill_group_id": group}
    if skill is not None:
        b["owner"]["skill_id"] = skill
    p["effect_bindings"] = [b]
    if valid:
        confirm_bindings(p, db)
        assert compose_equipment_plan(p, db)["state"] == "ready"
    else:
        with pytest.raises(PlanError, match="不一致"):
            compose_equipment_plan(p, db)


def test_same_time_switch_order_remains_semantic_after_match_confirmation(tmp_path):
    p = matching_plan()
    p["manual_template"]["characters"].append({"id": "off", "level": 90, "stats": {"atk": 100}})
    p["manual_template"]["switches"] = [{"t": 0, "character": "c"}, {"t": 0, "character": "off"}]
    p["manual_template"]["buffs"][0]["scope"] = "frontline"
    m = _match()
    p["rotation_tuning"]["buff_hit_matches"] = [m]
    db = tmp_path / "unused.db"
    first = compose_equipment_plan(p, db)
    m.update(state="confirmed", basis_fingerprint=first["match_previews"][0]["basis_fingerprint"])
    assert compose_equipment_plan(p, db)["state"] == "ready"
    p["manual_template"]["switches"].reverse()
    assert compose_equipment_plan(p, db)["state"] == "pending"


def test_unknown_match_target_is_not_silently_excluded(tmp_path):
    p = matching_plan()
    m = _match()
    m["include_hit_ids"] = ["never-created"]
    p["rotation_tuning"]["buff_hit_matches"] = [m]
    before = copy.deepcopy(p)
    with pytest.raises(PlanError, match="不存在的伤害事件"):
        compose_equipment_plan(p, tmp_path / "unused.db")
    assert before == p


def test_all_existing_output_conflicts_are_checked_before_first_publish(tmp_path):
    first, second = tmp_path / "new.json", tmp_path / "existing.json"
    second.write_bytes(b"previous")
    with pytest.raises(PlanError):
        publish_files([(first, b"new"), (second, b"replacement")])
    assert not first.exists() and second.read_bytes() == b"previous"


def test_external_change_after_first_publish_is_preserved_and_partial_result_reported(tmp_path, monkeypatch):
    first, second = tmp_path / "first.json", tmp_path / "second.json"
    second.write_bytes(b"old")
    real_replace = plan_files.os.replace

    def replace(source, target):
        real_replace(source, target)
        if target == first:
            second.write_bytes(b"external edit")

    monkeypatch.setattr(plan_files.os, "replace", replace)
    with pytest.raises(PlanError) as exc:
        publish_files([(first, b"new first"), (second, b"new second")], overwrite=True)
    assert exc.value.details["published_paths"] == [str(first.resolve())]
    assert first.read_bytes() == b"new first" and second.read_bytes() == b"external edit"


def test_compose_and_apply_share_equipment_read_session_without_creating_absent_database(request, monkeypatch):
    db, _ = request.getfixturevalue("database")
    real_session = queries.equipment_read_session
    entered = []

    @contextmanager
    def session(path):
        entered.append(path)
        with real_session(path):
            yield

    monkeypatch.setattr(queries, "equipment_read_session", session)
    assert compose_equipment_plan(plan_fixture(), db)["state"] == "ready"
    assert apply_data_update(plan_fixture(), db)["state"] == "ready"
    assert entered == [db, db]
    absent = db.parent / "absent.db"
    assert compose_equipment_plan(matching_plan(), absent)["state"] == "ready"
    assert not absent.exists() and len(entered) == 2


def test_oversized_numbers_and_new_plan_deep_input_use_domain_errors(tmp_path):
    p = matching_plan()
    p["manual_template"]["extensions"] = {"too_large": 10 ** 400}
    with pytest.raises(PlanError, match="溢出"):
        compose_equipment_plan(p, tmp_path / "unused.db")
    node = []
    for _ in range(600):
        node = [node]
    with pytest.raises(PlanError, match="深度"):
        new_plan({"version": 2, "meta": {"nested": node}})
