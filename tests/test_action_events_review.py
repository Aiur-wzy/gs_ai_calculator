"""Independent EC09A source, assembly and state oracles; isolated fixture DB only."""
from __future__ import annotations

import copy
import json

import pytest
from test_freeze_review import EVIDENCE, fresh_model
from test_mechanics_execution import add, fixture, rule, second_character

from genshin_app.equipment import (
    compose_equipment_plan,
    new_plan,
    run_plan_calc,
    validate_plan,
)
from genshin_app.equipment.mechanics import enable_mechanics
from genshin_app.equipment.schema import PlanError
from genshin_db.runtime_queries import query_runtime
from tests import fixture_candidate_builder as fixture_builder
from tests.test_pipeline import (
    fake_repo as fake_repo,  # noqa: PLC0414 -- pytest fixture
)
from tests.test_pipeline import (
    stub_fetcher as stub_fetcher,  # noqa: PLC0414 -- pytest fixture
)


@pytest.fixture
def review_action_db(tmp_path, fake_repo, stub_fetcher):
    """The independent E scalar is 1.25, not an answer obtained from the compiler."""
    path = fake_repo / "ExcelBinOutput/AvatarSkillDepotExcelConfigData.json"
    depots = json.loads(path.read_text(encoding="utf8"))
    depots[0]["skills"][1] = 90012
    path.write_text(json.dumps(depots), encoding="utf8")
    path = fake_repo / "ExcelBinOutput/AvatarSkillExcelConfigData.json"
    skills = json.loads(path.read_text(encoding="utf8"))
    skills.append({"id": 90012, "nameTextMapHash": 200, "descTextMapHash": 201,
                   "proudSkillGroupId": 92002, "triggerID": 2})
    path.write_text(json.dumps(skills), encoding="utf8")
    path = fake_repo / "ExcelBinOutput/ProudSkillExcelConfigData.json"
    proud = json.loads(path.read_text(encoding="utf8"))
    proud.extend({"proudSkillId": 9200200 + level, "proudSkillGroupId": 92002,
                  "level": level, "paramList": [value], "paramDescList": [201]}
                 for level, value in ((9, 1.1), (10, 1.25), (13, 1.5)))
    path.write_text(json.dumps(proud), encoding="utf8")
    fixture_builder.build_database(tmp_path)
    return tmp_path / "genshin.db"


def review_action_plan(db, count=3):
    template, rotation, model = fixture((0,))
    template["characters"][0]["stats"].update(energy_recharge=1, crit_dmg=1)
    template["hits"][0].update(id="prototype", t=0, scaling_stat="atk")
    template["hits"][0].pop("multiplier")
    rotation["abilities"][0]["variant"] = "tap"
    cast = rotation["actions"][0]
    rotation["actions"] = [{**cast, "id": f"cast{i}", "start": round(.3*i, 6),
                             "occupation_end": round(.3*i + .25, 6), "repeat": {"mode": "once"}}
                            for i in range(count)]
    model.update(hit_events=[], elemental_model=fresh_model())
    plan = enable_mechanics(new_plan(template))
    plan.update(rotation_model=rotation, mechanics_model=model)
    selection = {"character_id": 90000001, "slot": "skill", "constellation": 0}
    parameter = query_runtime(db, {"action": "action_parameter", "param_index": 0, **selection})
    plan["hit_parameter_sources"] = [{
        "hit_id": "prototype", "version_line": "release", "selection": selection,
        "param_index": 0, "scaling_stat": "atk", "purpose": "single_stat_damage_multiplier",
        "status": "assumed", "source": "独立测试指定E的ATK倍率；数据库不证明派发用途",
        "expected_snapshot_id": parameter["snapshot"]["snapshot_id"],
        "expected_node_sha256": parameter["source"]["node_sha256"],
    }]
    plan["action_event_profiles"] = [{
        "id": "review-profile", "kind": "calibrated_single_contact_v1", "prototype_hit_id": "prototype",
        "variant": "tap", "calibration": {"offset_seconds": .2, "precision_seconds": .01,
            "status": "assumed", "source": "独立工程假设：固定状态/无卡肉/无攻速变化，从动作start起0.2秒命中"},
        "application_profile_id": "cryo1", "contact_profile_id": "safe", "default_landed": True,
        "self_sampling": "current", "accepted_profile_fingerprint": None,
    }]
    plan["action_event_bindings"] = [{"ability_id": "e", "profile_id": "review-profile"}]
    plan["action_event_overrides"] = []
    return plan


def accepted(plan, db):
    preview = compose_equipment_plan(plan, db)
    for record in preview["action_event_compilations"]:
        profile = next(p for p in plan["action_event_profiles"] if p["id"] == record["profile_id"])
        profile.update(record["required_confirmation"])
    return preview


def damages(output):
    return [h["expected"] for h in output["result"]["hits"]]


def assert_not_executable(plan, db):
    with pytest.raises(PlanError):
        run_plan_calc(plan, db)


def review_manual_sources(plan, db):
    for requirement in compose_equipment_plan(plan, db)["effect_requirements"]:
        binding = next(b for b in plan["effect_bindings"] if b["binding_id"] == requirement["binding_id"])
        binding["source_fingerprint"] = requirement["source_fingerprint"]
        binding["coverage"] = {"state": "complete", "reviewed_by": "独立测试",
            "note": "仅声明工程控制变量", "source_fingerprint": requirement["source_fingerprint"]}


def attach_mechanics_rules(plan, *rules):
    for item in rules:
        bid = "source-" + item["id"]
        item["source_id"] = bid + ":value"
        add(plan["mechanics_model"], item)
        plan["effect_bindings"].append({"binding_id": bid, "provider_character_id": "a",
            "owner": {"kind": "manual", "note": "独立工程收益"},
            "source_refs": [{"kind": "manual", "note": "受控常数"}], "source_fingerprint": None,
            "coverage": {"state": "unreviewed", "reviewed_by": None, "source_fingerprint": None, "note": None},
            "parts": [{"part_id": "value", "label": "工程收益", "state": "resolved",
                "conditions": [], "rule": {"kind": "mechanics", "rule_id": item["id"]},
                "delivery": {"kind": "mechanics"}, "reason": None,
                "dependencies": {"mode": "source_only", "context_fingerprint": None, "note": "控制变量"}}]})


@pytest.mark.parametrize("case", ["extra", "id-too-long", "id-pattern", "source-too-long", "blank-source",
                                  "offset-bool", "offset-large", "precision-negative",
                                  "precision-large", "empty-override", "order-bool", "order-large",
                                  "too-many-overrides", "bad-fingerprint"])
def test_review_real_native_validator_enforces_new_finite_fields(review_action_db, case):
    plan = review_action_plan(review_action_db)
    profile = plan["action_event_profiles"][0]
    override = {"action_id": "cast0", "landed": False, **EVIDENCE}
    if case == "extra":
        profile["execute_script"] = "ignored-is-unsafe"
    elif case == "id-too-long":
        profile["id"] = "a" * 129
    elif case == "id-pattern":
        profile["id"] = "bad/id"
    elif case == "source-too-long":
        profile["calibration"]["source"] = "证" * 2049
    elif case == "blank-source":
        profile["calibration"]["source"] = " \t "
    elif case.startswith("offset-"):
        profile["calibration"]["offset_seconds"] = {
            "offset-bool": True,
            "offset-large": 60.000001,
        }[case]
    elif case.startswith("precision-"):
        profile["calibration"]["precision_seconds"] = -1 if case == "precision-negative" else 1.01
    elif case == "empty-override":
        override.pop("landed")
        plan["action_event_overrides"] = [override]
    elif case.startswith("order-"):
        override["order"] = True if case == "order-bool" else 1000000001
        plan["action_event_overrides"] = [override]
    elif case == "too-many-overrides":
        plan["action_event_overrides"] = [{**override, "action_id": f"future{i}"} for i in range(257)]
    else:
        profile["accepted_profile_fingerprint"] = "A" * 64
    with pytest.raises(PlanError):
        validate_plan(plan)


def test_review_zero_offset_is_valid_and_runs_after_each_parent_action(review_action_db):
    plan = review_action_plan(review_action_db)
    plan["action_event_profiles"][0]["calibration"]["offset_seconds"] = 0
    assert validate_plan(plan)["valid"]
    accepted(plan, review_action_db)

    output = run_plan_calc(plan, review_action_db)

    actions = {row["id"]: row for row in plan["rotation_model"]["actions"]}
    generated = output["report"]["action_event_compilations"][0]["generated"]
    assert [row["t"] for row in generated] == [0, .3, .6]
    assert all(row["order"] == actions[row["action_id"]]["order"] + 1 for row in generated)
    assert damages(output) == pytest.approx([56.25] * 3)


def test_review_one_source_dispatches_only_explicit_actions_and_preserves_native(review_action_db):
    plan = review_action_plan(review_action_db)
    assert validate_plan(plan)["valid"]
    first = accepted(plan, review_action_db)
    assert first["state"] == "pending"
    before = copy.deepcopy(plan)
    output = run_plan_calc(plan, review_action_db)
    # 100 ATK * 1.25 * .5 DEF * .9 RES; no crit or reaction.
    assert damages(output) == pytest.approx([56.25] * 3)
    assert plan == before
    assert output["plan"]["mechanics_model"]["hit_patterns"] == []
    assert "multiplier" not in output["plan"]["manual_template"]["hits"][0]
    assert len(output["report"]["mechanics_model"]["hit_patterns"]) == 3
    record = output["report"]["action_event_compilations"][0]
    assert record["dispatch_mode"] == "calibrated"
    assert record["gameplay_interpretation"] == "declared_not_verified"
    assert record["skill_coverage"] == "partial"
    plan["rotation_model"]["max_cycles"] = 5
    assert damages(run_plan_calc(plan, review_action_db)) == pytest.approx([56.25] * 3)


def test_review_move_reorder_delete_use_stable_action_identity_without_ghost_hits(review_action_db):
    plan = review_action_plan(review_action_db)
    accepted(plan, review_action_db)
    first = run_plan_calc(plan, review_action_db)
    old_rows = {r["action_id"]: r for r in first["report"]["action_event_compilations"][0]["generated"]}
    plan["rotation_model"]["actions"].reverse()
    second = next(a for a in plan["rotation_model"]["actions"] if a["id"] == "cast1")
    second.update(start=.31, occupation_end=.56)
    moved = run_plan_calc(plan, review_action_db)
    new_rows = {r["action_id"]: r for r in moved["report"]["action_event_compilations"][0]["generated"]}
    assert {k: r["hit_id"] for k, r in new_rows.items()} == {k: r["hit_id"] for k, r in old_rows.items()}
    assert {k: r["t"] for k, r in new_rows.items()} == pytest.approx({"cast0": .2, "cast1": .51, "cast2": .8})
    plan["rotation_model"]["actions"] = [a for a in plan["rotation_model"]["actions"] if a["id"] != "cast1"]
    assert damages(run_plan_calc(plan, review_action_db)) == pytest.approx([56.25, 56.25])
    plan["rotation_model"]["actions"] = []
    assert_not_executable(plan, review_action_db)


def test_review_shared_icd_is_not_reset_by_action_instance_and_miss_does_not_count(review_action_db):
    plan = review_action_plan(review_action_db, 4)
    elem = plan["mechanics_model"]["elemental_model"]
    next(p for p in elem["profiles"] if p["id"] == "cryo1")["icd"] = {"kind": "standard", "group": "one-shared-group"}
    accepted(plan, review_action_db)
    output = run_plan_calc(plan, review_action_db)
    applications = [r for r in output["result"]["elemental"]["trace"] if r["type"] == "elemental_application"]
    assert [r["allowed"] for r in applications] == [True, False, False, True]
    plan["action_event_overrides"] = [{"action_id": "cast1", "landed": False, **EVIDENCE}]
    missed = run_plan_calc(plan, review_action_db)
    applications = [r for r in missed["result"]["elemental"]["trace"] if r["type"] == "elemental_application"]
    assert [r["allowed"] for r in applications] == [True, False, False, False]
    assert [r["reason"] for r in applications][1] == "missed"
    assert damages(missed) == pytest.approx([56.25] * 3)


@pytest.mark.parametrize("change", ["calibration", "precision", "gu", "icd", "contact-source", "purpose-source"])
def test_review_same_ids_with_changed_meaning_require_fresh_acceptance(review_action_db, change):
    plan = review_action_plan(review_action_db)
    accepted(plan, review_action_db)
    profile = plan["action_event_profiles"][0]
    elem = plan["mechanics_model"]["elemental_model"]
    app = next(p for p in elem["profiles"] if p["id"] == "cryo1")
    if change == "calibration":
        profile["calibration"]["source"] += " 修订证据"
    elif change == "precision":
        profile["calibration"]["precision_seconds"] = .02
    elif change == "gu":
        app["gauge_units"] = 2
    elif change == "icd":
        app["icd"] = {"kind": "standard", "group": "different-semantic-group"}
    elif change == "contact-source":
        next(p for p in elem["contact_profiles"] if p["id"] == "safe")["source"] += " 修订接触证据"
    else:
        plan["hit_parameter_sources"][0]["source"] += " 修订用途证据"
    report = compose_equipment_plan(plan, review_action_db)
    record = report["action_event_compilations"][0]
    assert report["state"] == "pending"
    assert record["profile_fingerprint"] != profile["accepted_profile_fingerprint"]
    assert_not_executable(plan, review_action_db)


@pytest.mark.parametrize("change", ["repeat", "after-occupation", "window", "submicro", "unknown-landed", "unknown-contact"])
def test_review_unsupported_scope_does_not_publish_a_partial_axis(review_action_db, change):
    plan = review_action_plan(review_action_db)
    accepted(plan, review_action_db)
    if change == "repeat":
        plan["rotation_model"]["actions"][1]["repeat"] = {"mode": "each_cycle"}
    elif change == "after-occupation":
        plan["rotation_model"]["actions"][1]["occupation_end"] = .4
    elif change == "window":
        plan["manual_template"]["loop_duration"] = .7
        plan["rotation_model"]["period"] = .7
    elif change == "submicro":
        plan["action_event_profiles"][0]["calibration"]["offset_seconds"] = .2000001
    elif change == "unknown-landed":
        plan["action_event_overrides"] = [{"action_id": "cast1", "landed": None, **EVIDENCE}]
    else:
        next(p for p in plan["mechanics_model"]["elemental_model"]["contact_profiles"] if p["id"] == "safe")["freeze_interaction"] = "unknown"
    assert_not_executable(plan, review_action_db)


def test_review_alias_reconnection_and_panel_change_do_not_revoke_gameplay_acceptance(review_action_db):
    plan = review_action_plan(review_action_db)
    accepted(plan, review_action_db)
    old_fp = plan["action_event_profiles"][0]["accepted_profile_fingerprint"]
    profile = plan["action_event_profiles"][0]
    profile.update(id="renamed-profile", prototype_hit_id="renamed-prototype",
                   application_profile_id="renamed-application", contact_profile_id="renamed-contact")
    hit = plan["manual_template"]["hits"][0]
    hit.update(id="renamed-prototype", character="new-actor", skill="中文显示重命名", hit_name="原型显示名")
    char = plan["manual_template"]["characters"][0]
    char["id"] = "new-actor"
    char["stats"]["atk"] = 200
    plan["hit_parameter_sources"][0]["hit_id"] = "renamed-prototype"
    plan["action_event_bindings"][0].update(profile_id="renamed-profile", ability_id="new-ability")
    rotation = plan["rotation_model"]
    rotation["characters"][0]["id"] = rotation["initial_frontline"] = "new-actor"
    rotation["abilities"][0].update(id="new-ability", character="new-actor")
    for i, cast in enumerate(rotation["actions"]):
        cast.update(id=f"different-axis-cast{i}", ability_id="new-ability")
    elem = plan["mechanics_model"]["elemental_model"]
    next(p for p in elem["profiles"] if p["id"] == "cryo1")["id"] = "renamed-application"
    next(p for p in elem["contact_profiles"] if p["id"] == "safe")["id"] = "renamed-contact"
    output = run_plan_calc(plan, review_action_db)
    assert output["report"]["action_event_compilations"][0]["profile_fingerprint"] == old_fp
    assert damages(output) == pytest.approx([112.5] * 3)


def test_review_action_start_snapshot_is_after_pre_but_before_post_benefits(review_action_db):
    plan = review_action_plan(review_action_db, 1)
    attach_mechanics_rules(plan,
        rule("pre-atk", "benefit.stat_add.atk", 50,
             trigger={"event": "action_start", "phase": "pre", "action_ids": ["cast0"]}),
        rule("post-atk", "benefit.stat_add.atk", 100,
             trigger={"event": "action_start", "phase": "post", "action_ids": ["cast0"]}))
    plan["action_event_profiles"][0]["self_sampling"] = "action_start"
    accepted(plan, review_action_db)
    review_manual_sources(plan, review_action_db)
    snap = run_plan_calc(plan, review_action_db)
    assert damages(snap) == pytest.approx([84.375])  # (100+50)*1.25*.5*.9
    plan["action_event_profiles"][0]["self_sampling"] = "current"
    accepted(plan, review_action_db)
    current = run_plan_calc(plan, review_action_db)
    assert damages(current) == pytest.approx([140.625])  # (100+50+100)*1.25*.5*.9


def test_review_order_override_preserves_same_tick_pre_effect_without_inventing_order(review_action_db):
    plan = review_action_plan(review_action_db, 1)
    accepted(plan, review_action_db)
    plan["mechanics_model"]["condition_events"] = [{"id": "other-event", "t": .2, "order": 0,
        "condition": "other-event", "value": True, "repeat": {"mode": "once"}, **EVIDENCE}]
    assert_not_executable(plan, review_action_db)
    plan["action_event_overrides"] = [{"action_id": "cast0", "order": 1, **EVIDENCE}]
    output = run_plan_calc(plan, review_action_db)
    pattern = output["report"]["mechanics_model"]["hit_patterns"][0]
    assert pattern["orders"] == [1]
    assert plan["mechanics_model"]["condition_events"][0]["order"] == 0
    assert damages(output) == pytest.approx([56.25])


def test_review_actual_early_switch_is_rejected_but_merely_cancelable_cast_is_allowed(review_action_db):
    plan = review_action_plan(review_action_db, 1)
    plan["rotation_model"]["actions"][0]["switch_allowed_at"] = .1
    accepted(plan, review_action_db)
    assert damages(run_plan_calc(plan, review_action_db)) == pytest.approx([56.25])
    second_character(plan["manual_template"], plan["rotation_model"])
    plan["manual_template"]["characters"][1]["stats"]["energy_recharge"] = 1
    plan["rotation_model"]["abilities"].append({"id": "swap", "character": "a", "action_type_id": "action.switch",
                                               "cooldown": None, "energy": None, **EVIDENCE})
    plan["rotation_model"]["actions"].append({"id": "early-switch", "ability_id": "swap", "start": .15,
        "occupation_end": .15, "order": 0, "switch_to": "b", "repeat": {"mode": "once"}, **EVIDENCE})
    assert_not_executable(plan, review_action_db)


def test_review_manual_buff_prototype_selector_cannot_silently_lose_its_benefit(review_action_db):
    plan = review_action_plan(review_action_db)
    accepted(plan, review_action_db)
    plan["manual_template"]["buffs"] = [{"id": "manual-bonus", "start": 0, "end": 9,
        "extensions": {"owner": "a", "source_id": "manual-bonus-source"},
        "effects": [{"id": "bonus", "benefit_type_id": "benefit.dmg_bonus.all", "zone": "dmg_bonus",
            "stat": "all", "value": .5, "applies_to": {"mode": "hits", "hit_ids": ["prototype"]}}]}]
    # Either explicitly reject unresolved prototype targeting, or map it with a receipt.
    # A successful 56.25 silently dropping the 50% benefit is never acceptable.
    try:
        output = run_plan_calc(plan, review_action_db)
    except PlanError as exc:
        assert any(token in str(exc).lower() + json.dumps(exc.details, ensure_ascii=False).lower()
                   for token in ("prototype", "原型", "selector", "命中"))
    else:
        assert damages(output) == pytest.approx([84.375] * 3)


@pytest.mark.parametrize("delivery", ["hit", "pattern", "application", "link"])
def test_review_bound_action_cannot_keep_a_second_manually_named_delivery(review_action_db, delivery):
    plan = review_action_plan(review_action_db, 1)
    accepted(plan, review_action_db)
    model = plan["mechanics_model"]
    if delivery == "hit":
        model["hit_events"] = [{"hit_id": "prototype", "action_id": "cast0", "order": 5,
            "application": {"profile_id": "cryo1", "target_id": "enemy", "landed": True},
            "contact": {"kind": "profile", "profile_id": "safe"}}]
    elif delivery == "pattern":
        model["hit_patterns"] = [{"id": "manual-renamed-copy", "prototype_hit_id": "prototype",
            "action_id": "cast0", "offsets": [.2], "orders": [5],
            "application": {"profile_id": "cryo1", "target_id": "enemy", "landed": True},
            "contact": {"kind": "profile", "profile_id": "safe"}}]
    elif delivery == "application":
        model["application_events"] = [{"id": "manual-application", "action_id": "cast0", "t": .2,
            "order": 5, "application": {"profile_id": "cryo1", "target_id": "enemy", "landed": True},
            "contact": {"kind": "profile", "profile_id": "safe"}}]
    else:
        plan["rotation_model"]["action_hit_links"] = [{"action_id": "cast0", "hit_id": "prototype"}]
    assert_not_executable(plan, review_action_db)


def test_review_same_microsecond_later_switch_cannot_evade_cancellation_scope(review_action_db):
    plan = review_action_plan(review_action_db, 1)
    plan["rotation_model"]["actions"][0]["switch_allowed_at"] = 0
    accepted(plan, review_action_db)
    second_character(plan["manual_template"], plan["rotation_model"])
    plan["manual_template"]["characters"][1]["stats"]["energy_recharge"] = 1
    plan["rotation_model"]["abilities"].append({"id": "swap", "character": "a", "action_type_id": "action.switch",
                                               "cooldown": None, "energy": None, **EVIDENCE})
    plan["rotation_model"]["actions"].append({"id": "same-tick-switch", "ability_id": "swap", "start": 0,
        "occupation_end": 0, "order": 1, "switch_to": "b", "repeat": {"mode": "once"}, **EVIDENCE})
    report = compose_equipment_plan(plan, review_action_db)
    assert report["state"] == "pending"
    assert any(issue["code"] == "action_event_cancel" for issue in report["missing_inputs"])
    assert_not_executable(plan, review_action_db)


def test_review_second_profile_pending_never_installs_first_profiles_partial_dispatch(review_action_db):
    plan = review_action_plan(review_action_db, 2)
    other_hit = copy.deepcopy(plan["manual_template"]["hits"][0])
    other_hit["id"] = "other-prototype"
    plan["manual_template"]["hits"].append(other_hit)
    source = copy.deepcopy(plan["hit_parameter_sources"][0])
    source["hit_id"] = "other-prototype"
    plan["hit_parameter_sources"].append(source)
    profile = copy.deepcopy(plan["action_event_profiles"][0])
    profile.update(id="second-profile", prototype_hit_id="other-prototype")
    plan["action_event_profiles"].append(profile)
    ability = copy.deepcopy(plan["rotation_model"]["abilities"][0])
    ability["id"] = "second-e"
    plan["rotation_model"]["abilities"].append(ability)
    plan["rotation_model"]["actions"][1]["ability_id"] = "second-e"
    plan["action_event_bindings"].append({"ability_id": "second-e", "profile_id": "second-profile"})
    accepted(plan, review_action_db)
    assert damages(run_plan_calc(plan, review_action_db)) == pytest.approx([56.25, 56.25])
    plan["action_event_overrides"] = [{"action_id": "cast1", "landed": None, **EVIDENCE}]
    out = compose_equipment_plan(plan, review_action_db)
    assert out["state"] == "pending"
    assert out["plan"]["resolved_template"] is None
    assert out["mechanics_model"]["hit_patterns"] == []
    assert all(not record["generated"] for record in out["action_event_compilations"])


@pytest.mark.parametrize("change", ["node", "normalization"])
def test_review_raw_parameter_source_change_cannot_hide_behind_same_scalar_and_interpretation(review_action_db, monkeypatch, change):
    from genshin_db import runtime_action_parameter

    plan = review_action_plan(review_action_db)
    accepted(plan, review_action_db)
    original = runtime_action_parameter.project_action_parameter

    def changed_origin(*args, **kwargs):
        result = copy.deepcopy(original(*args, **kwargs))
        if change == "node":
            result["source"]["node_sha256"] = "9" * 64
        else:
            result["snapshot"]["normalization"] = {
                "normalization_version": 2, "profile_id": "independent-test-map",
                "mapping_fingerprint": "8" * 64,
                "upstream_commit": result["snapshot"]["upstream_commit"],
            }
        return result

    # The scalar API is the trust boundary under test; no database contents are forged.
    monkeypatch.setattr(runtime_action_parameter, "project_action_parameter", changed_origin)
    if change == "node":
        plan["hit_parameter_sources"][0]["expected_node_sha256"] = "9" * 64
    report = compose_equipment_plan(plan, review_action_db)
    record = report["action_event_compilations"][0]
    assert report["hit_parameter_compilations"][0]["status"] == "ready"
    assert record["profile_fingerprint"] != plan["action_event_profiles"][0]["accepted_profile_fingerprint"]
    assert report["state"] == "pending"
    assert_not_executable(plan, review_action_db)
