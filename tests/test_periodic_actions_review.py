"""Independent EC13b native/dispatch cases; expected values are not engine outputs."""
from __future__ import annotations

import copy
import json

import pytest

from genshin_app.equipment import (
    compose_equipment_plan,
    enable_mechanics,
    new_plan,
    run_plan_calc,
)
from genshin_app.equipment.schema import PlanError, validate_plan
from tests import fixture_candidate_builder as fixture_builder
from tests.test_freeze_runtime import model as freeze_model
from tests.test_mechanics_execution import EVIDENCE, ability, action, fixture
from tests.test_pipeline import fake_repo, stub_fetcher  # noqa: F401
from tests.test_runtime_periodic_attack import periodic_db  # noqa: F401

# ruff: noqa: F811

KIND = "calibrated_multi_contact_v1"


def draft_periodic_plan():
    """Complete abstract native draft; no DB helper or product dispatcher used."""
    template, rotation, mechanics = fixture((0,))
    template["loop_duration"] = rotation["period"] = 22
    template["characters"][0]["stats"]["energy_recharge"] = 1
    plan = enable_mechanics(new_plan(template))
    prototype = plan["manual_template"]["hits"][0]
    prototype.update(id="slice.proto", damage_type_ids=["damage.elemental_burst"], scaling_stat="atk", count=1)
    prototype.pop("element")
    prototype.pop("multiplier")
    second = copy.deepcopy(prototype)
    second["id"] = "blast.proto"
    plan["manual_template"]["hits"].append(second)
    rotation["abilities"] = [{**ability("q", kind="action.elemental_burst", energy={"required": 0, "amount": 0}), "variant": "default"}]
    rotation["actions"] = [action(f"q{i+1}", "q", start=start, end=start+.5, repeat={"mode": "once"})
                           for i, start in enumerate((0, 10, 20))]
    rotation["events"] = [{"id": f"spend{i+1}", "type": "energy_spend", "action_id": f"q{i+1}",
        "character": "a", "t": start, "order": 1, "required": 0, "amount": 0,
        "repeat": {"mode": "once"}, **EVIDENCE} for i, start in enumerate((0, 10, 20))]
    mechanics["hit_events"] = []
    mechanics["elemental_model"] = freeze_model()
    mechanics["elemental_model"]["profiles"] = []
    mechanics["elemental_model"]["contact_profiles"] = [
        {"id": "contact.slice", "freeze_interaction": "none", **EVIDENCE},
        {"id": "contact.blast", "freeze_interaction": "none", **EVIDENCE},
    ]
    plan.update(rotation_model=rotation, mechanics_model=mechanics)
    plan["action_event_profiles"] = [{
        "id": "periodic", "kind": KIND, "variant": "default",
        "lifecycle": {"establishment_offset_seconds": .1, "instance_end_offset_seconds": 1.2,
                      "precision_seconds": .01, "owner_and_target_survive": True,
                      "no_interrupt": True, "no_refresh_replacement_or_early_despawn": True, **EVIDENCE},
        "channels": [
            {"channel_id": "slice", "prototype_hit_id": "slice.proto", "application_profile_id": "application.slice",
             "contact_profile_id": "contact.slice", "default_landed": True, "self_sampling": "current",
             "schedule": {"kind": "periodic", "first_offset_seconds": .2, "count": 4,
                          "precision_seconds": .01, **EVIDENCE}},
            {"channel_id": "blast", "prototype_hit_id": "blast.proto", "application_profile_id": "application.blast",
             "contact_profile_id": "contact.blast", "default_landed": True, "self_sampling": "current",
             "schedule": {"kind": "single", "offset_seconds": 1.1, "precision_seconds": .01, **EVIDENCE}},
        ], "accepted_profile_fingerprint": None,
    }]
    plan["action_event_bindings"] = [{"ability_id": "q", "profile_id": "periodic"}]
    plan["action_event_overrides"] = []
    verification = {"status": "confirmed", "locator": "tests/test_periodic_actions_review.py exact channel fixture",
        "version": "fixture-v1", "reviewed_at": "2026-10-18", "component_scope": "one selected periodic attack channel",
        "confirmed_fields": ["application_mode", "counter_tag", "shared_scope"]}
    plan["periodic_attack_sources"] = [{"action_event_profile_id": "periodic", "version_line": "release",
        "selection": {"character_id": 90000001, "slot": "burst", "constellation": 0},
        "origin_reference_id": None, "channels": [
            {"channel_id": "slice", "candidate_id": None, "counter_tag": "Elemental_Burst",
             "application_mode": "sequence", "verification": copy.deepcopy(verification)},
            {"channel_id": "blast", "candidate_id": None, "counter_tag": "Elemental_Burst",
             "application_mode": "sequence", "verification": copy.deepcopy(verification)}],
        "purpose": "calibrated_periodic_attack", **EVIDENCE}]
    return plan


def test_review_native_draft_is_valid_without_filling_owned_numbers():
    plan = draft_periodic_plan()
    before = copy.deepcopy(plan)
    validate_plan(plan)
    assert plan == before
    assert all(not ({"element", "multiplier"} & hit.keys()) for hit in plan["manual_template"]["hits"])
    assert plan["mechanics_model"]["elemental_model"]["profiles"] == []
    assert plan["mechanics_model"]["hit_patterns"] == []


def test_review_two_channels_may_reference_one_explicit_contact_profile():
    plan = draft_periodic_plan()
    plan["action_event_profiles"][0]["channels"][1]["contact_profile_id"] = "contact.slice"
    plan["mechanics_model"]["elemental_model"]["contact_profiles"].pop()
    before = copy.deepcopy(plan)
    validate_plan(plan)
    assert plan == before


@pytest.mark.parametrize("field,value", [("element", "cryo"), ("multiplier", 0), ("multiplier", None)])
def test_review_source_owned_fields_cannot_be_reintroduced_even_as_zero_or_null(field, value):
    plan = draft_periodic_plan()
    plan["manual_template"]["hits"][0][field] = value
    with pytest.raises(PlanError):
        validate_plan(plan)


@pytest.mark.parametrize("field", ["prototype_hit_id", "application_profile_id"])
def test_review_distinct_channels_cannot_share_generated_ownership(field):
    plan = draft_periodic_plan()
    channels = plan["action_event_profiles"][0]["channels"]
    channels[1][field] = channels[0][field]
    with pytest.raises(PlanError):
        validate_plan(plan)


def test_review_nullable_candidate_draft_does_not_permit_duplicate_selected_candidate():
    plan = draft_periodic_plan()
    for channel in plan["periodic_attack_sources"][0]["channels"]:
        channel["candidate_id"] = "periodic.same.candidate"
    with pytest.raises(PlanError):
        validate_plan(plan)


@pytest.mark.parametrize("path", [
    ("lifecycle", "establishment_offset_seconds"), ("lifecycle", "instance_end_offset_seconds"),
    ("lifecycle", "precision_seconds"), (0, "first_offset_seconds"), (0, "count"),
    (0, "precision_seconds"), (1, "offset_seconds"), (1, "precision_seconds"),
])
def test_review_each_unknown_calibration_is_a_preserved_nullable_draft(path):
    plan = draft_periodic_plan()
    profile = plan["action_event_profiles"][0]
    owner = profile["lifecycle"] if path[0] == "lifecycle" else profile["channels"][path[0]]["schedule"]
    owner[path[1]] = None
    before = copy.deepcopy(plan)
    validate_plan(plan)
    assert plan == before and owner[path[1]] is None


@pytest.mark.parametrize("value", [True, 0, -1, 257, 1.5, 10**400])
def test_review_count_rejects_non_integer_and_out_of_range_native_values(value):
    plan = draft_periodic_plan()
    plan["action_event_profiles"][0]["channels"][0]["schedule"]["count"] = value
    with pytest.raises(PlanError):
        validate_plan(plan)


def test_zero_duration_single_callback_uses_unified_lifecycle_projection(periodic_db):
    plan = one_action(source_bound_plan(periodic_db))
    profile = plan["action_event_profiles"][0]
    profile["lifecycle"]["instance_end_offset_seconds"] = .1
    profile["channels"][0]["schedule"].update(
        first_offset_seconds=.1, count=1)
    profile["channels"][1]["schedule"]["offset_seconds"] = .1
    plan["action_event_overrides"] = [{"kind": profile["kind"],
        "action_id": "q1", "contacts": [
            {"channel_id": "slice", "ordinal": 1, "order": 1, **EVIDENCE},
            {"channel_id": "blast", "ordinal": 1, "order": 2, **EVIDENCE},
        ]}]
    accept_periodic(plan, periodic_db)
    report = compose_equipment_plan(plan, periodic_db)
    record = report["action_event_compilations"][0]
    assert report["state"] == "ready", report["missing_inputs"]
    assert [(row["channel_id"], row["expected_time_ticks"], row["order"])
            for row in record["generated"]] == [
                ("slice", 100000, 1), ("blast", 100000, 2)]
    assert record["lifecycle_projection"]["summary"] == {
        "generations": 1, "materialized_occurrences": 2,
        "cancelled_occurrences": 0, "deferred_intervals": 0,
        "skipped_controls": 0}


def test_nonzero_establishment_same_tick_first_hit_follows_establishment_order(periodic_db):
    plan = one_action(source_bound_plan(periodic_db))
    profile = plan["action_event_profiles"][0]
    establish = profile["lifecycle"]["establishment_offset_seconds"]
    assert establish == .1
    profile["channels"][0]["schedule"].update(
        first_offset_seconds=establish, count=4)
    accept_periodic(plan, periodic_db)

    report = compose_equipment_plan(plan, periodic_db)

    assert report["state"] == "ready", report["missing_inputs"]
    action = plan["rotation_model"]["actions"][0]
    first = next(row for row in report["action_event_compilations"][0]["generated"]
                 if row["channel_id"] == "slice" and row["ordinal"] == 1)
    projected = next(
        row for row in report["action_event_compilations"][0]["lifecycle_projection"]["occurrences"]
        if row["channel_id"] == "slice" and row["ordinal"] == 1)
    assert first["expected_time_ticks"] == 100000
    assert first["order"] > action["order"]
    assert (projected["time_ticks"], projected["order"]) == (
        first["expected_time_ticks"], first["order"])


@pytest.mark.parametrize("value", [True, 0, -.1, .0000001, 1_000_000_001, float("nan"), float("inf")])
def test_review_offsets_reject_bool_rounding_and_nonfinite_before_source_query(value):
    plan = draft_periodic_plan()
    plan["action_event_profiles"][0]["lifecycle"]["instance_end_offset_seconds"] = value
    with pytest.raises(PlanError):
        validate_plan(plan)


@pytest.mark.parametrize("target", ["profile_channels", "source_channels", "override_contacts"])
def test_review_required_arrays_are_not_allowed_empty_by_an_ignored_minitems_keyword(target):
    plan = draft_periodic_plan()
    if target == "profile_channels":
        plan["action_event_profiles"][0]["channels"] = []
    elif target == "source_channels":
        plan["periodic_attack_sources"][0]["channels"] = []
    else:
        plan["action_event_overrides"] = [{"kind": KIND, "action_id": "q1", "contacts": []}]
    with pytest.raises(PlanError):
        validate_plan(plan)


@pytest.mark.parametrize("field,value", [("interval_seconds", .25), ("source_key", "slice_interval"), ("offset_seconds", .2)])
def test_review_periodic_schedule_cannot_override_source_timer_or_mix_single_shape(field, value):
    plan = draft_periodic_plan()
    plan["action_event_profiles"][0]["channels"][0]["schedule"][field] = value
    with pytest.raises(PlanError):
        validate_plan(plan)


def test_review_four_arrays_share_one_native_budget_not_four_independent_allowances():
    plan = draft_periodic_plan()
    original_profile = plan["action_event_profiles"][0]
    original_source = plan["periodic_attack_sources"][0]
    original_hits = plan["manual_template"]["hits"]
    plan["action_event_profiles"], plan["periodic_attack_sources"] = [], []
    plan["action_event_bindings"], plan["manual_template"]["hits"] = [], []
    for i in range(32):
        profile, source = copy.deepcopy(original_profile), copy.deepcopy(original_source)
        profile["id"] = source["action_event_profile_id"] = f"profile.{i}"
        for channel, original_hit in zip(profile["channels"], original_hits, strict=True):
            hit = copy.deepcopy(original_hit)
            hit["id"] += f".{i}"
            channel["prototype_hit_id"] = hit["id"]
            channel["application_profile_id"] += f".{i}"
            channel["schedule"]["source"] = "x" * 500
            plan["manual_template"]["hits"].append(hit)
        profile["lifecycle"]["source"] = "x" * 500
        source["source"] = "x" * 100
        plan["action_event_profiles"].append(profile)
        plan["periodic_attack_sources"].append(source)
    # Same legal draft before the addition: proves the rejection below is not
    # merely from an unrelated ownership/type failure in this synthetic plan.
    validate_plan(plan)
    for source in plan["periodic_attack_sources"]:
        source["source"] = "x" * 2048
    original_arrays = {key: plan[key] for key in ("action_event_profiles", "action_event_bindings", "action_event_overrides")}
    added_array = {"periodic_attack_sources": plan["periodic_attack_sources"]}
    size = lambda value: len(json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf8"))
    assert size(original_arrays) < 128 * 1024 and size(added_array) < 128 * 1024
    assert size({**original_arrays, **added_array}) > 128 * 1024
    with pytest.raises(PlanError):
        validate_plan(plan)


def source_bound_plan(db):
    from genshin_db.runtime_queries import query_runtime

    plan = draft_periodic_plan()
    request = {"action": "periodic_attack_profile", "character_id": 90000001, "slot": "burst"}
    origins = query_runtime(db, request)
    origin = next(c["origin_reference_id"] for c in origins["creation_candidates"] if c["gadget_id"] == 41002001)
    candidates = query_runtime(db, {**request, "origin_reference_id": origin})["candidates"]
    by_kind = {c["channel_kind"]: c["candidate_id"] for c in candidates}
    source = plan["periodic_attack_sources"][0]
    source["origin_reference_id"] = origin
    source["channels"][0]["candidate_id"] = by_kind["periodic_callback_attack"]
    source["channels"][1]["candidate_id"] = by_kind["ability_start_attack"]
    return plan


def accept_periodic(plan, db):
    report = compose_equipment_plan(plan, db)
    record = report["action_event_compilations"][0]
    assert record["required_confirmation"], report["missing_inputs"]
    plan["action_event_profiles"][0].update(record["required_confirmation"])
    return plan


def test_review_three_actions_produce_independent_15_hit_decimal_ledger(periodic_db):
    plan = source_bound_plan(periodic_db)
    preview = compose_equipment_plan(plan, periodic_db)
    assert preview["state"] == "pending"
    assert preview["periodic_attack_compilations"][0]["status"] == "ready"
    assert preview["action_event_compilations"][0]["generated"] == []
    accept_periodic(plan, periodic_db)
    native = copy.deepcopy(plan)
    try:
        out = run_plan_calc(plan, periodic_db)
    except PlanError as exc:
        pytest.fail(str(exc.details))
    assert plan == native
    assert out["plan"]["mechanics_model"]["hit_patterns"] == []
    assert out["plan"]["mechanics_model"]["elemental_model"]["profiles"] == []
    assert all(not ({"element", "multiplier"} & h.keys()) for h in out["plan"]["manual_template"]["hits"])
    hits = sorted(out["result"]["hits"], key=lambda h: h["t"])
    assert [h["t"] for h in hits] == [.2, .45, .7, .95, 1.1, 10.2, 10.45, 10.7, 10.95, 11.1,
                                          20.2, 20.45, 20.7, 20.95, 21.1]
    assert sum(h["expected"] for h in hits) == pytest.approx(1500.889481306076015, rel=1e-13)
    assert [h["expected"] for h in hits] == pytest.approx(([90.96299886703491] * 4 + [136.44449830055237]) * 3)


def test_review_one_miss_keeps_all_emission_ids_and_subtracts_only_its_damage(periodic_db):
    plan = accept_periodic(source_bound_plan(periodic_db), periodic_db)
    original = run_plan_calc(plan, periodic_db)
    plan["action_event_overrides"] = [{"kind": KIND, "action_id": "q2", "contacts": [
        {"channel_id": "slice", "ordinal": 2, "landed": False, **EVIDENCE}]}]
    out = run_plan_calc(plan, periodic_db)
    before = original["report"]["action_event_compilations"][0]
    after = out["report"]["action_event_compilations"][0]
    key = lambda g: (g["action_id"], g["channel_id"], g["ordinal"])
    assert {key(g): g["hit_id"] for g in before["generated"]} == {key(g): g["hit_id"] for g in after["generated"]}
    assert before["profile_fingerprint"] == after["profile_fingerprint"]
    assert before["instance_fingerprint"] != after["instance_fingerprint"]
    assert sum(h["expected"] for h in out["result"]["hits"]) == pytest.approx(1409.926482439041105, rel=1e-13)
    assert after["stats"]["generated_hits"] == 15 and after["stats"]["missed_hits"] == 1


def test_review_shared_icd_crosses_channels_and_actions_without_suffix_reset(periodic_db):
    plan = source_bound_plan(periodic_db)
    plan["rotation_model"]["period"] = plan["manual_template"]["loop_duration"] = 5
    for act, spend, start in zip(plan["rotation_model"]["actions"], plan["rotation_model"]["events"], (0, 1.3, 2.6), strict=True):
        act.update(start=start, occupation_end=start+.5)
        spend["t"] = start
    out = run_plan_calc(accept_periodic(plan, periodic_db), periodic_db)
    applications = sorted((r for r in out["result"]["elemental"]["trace"] if r["type"] == "elemental_application"), key=lambda r: r["t"])
    assert [r["allowed"] for r in applications] == [True, False, False, True, False,
        False, True, False, False, True, True, False, False, True, False]
    # The first contact of action 2 inherits contact count 6; its blast does
    # not gain a new group either. All original damage still exists.
    assert len(out["result"]["hits"]) == 15


def refreshing_plan(db):
    plan = source_bound_plan(db)
    rotation = plan["rotation_model"]
    rotation["actions"] = rotation["actions"][:2]
    rotation["events"] = rotation["events"][:2]
    for act, spend, start in zip(rotation["actions"], rotation["events"], (0, .6), strict=True):
        act.update(start=start, occupation_end=start + .5)
        spend["t"] = start
    rotation["period"] = plan["manual_template"]["loop_duration"] = 3
    profile = plan["action_event_profiles"][0]
    profile["lifecycle"].update(no_refresh_replacement_or_early_despawn=False,
        replacement_policy="replace_previous", no_early_despawn=True)
    schedule = profile["channels"][0]["schedule"]
    schedule.update(count=None, derive_count="through_instance_end_inclusive")
    return plan


def test_review_refresh_replaces_future_old_generation_and_derives_finite_count(periodic_db):
    plan = accept_periodic(refreshing_plan(periodic_db), periodic_db)
    out = run_plan_calc(plan, periodic_db)
    record = out["report"]["action_event_compilations"][0]
    assert record["replacement_contract"] == {
        "trigger": "new_instance_establishment", "old_instance_end": "inclusive",
        "cancel_old_events": "at_or_after_trigger", "past_events": "preserved",
        "execution": "compile_time_planned",
    }
    assert record["replacements"] == [{"replaced_action_id": "q1", "replacing_action_id": "q2",
        "replaced_generation": 1, "replacing_generation": 2,
        "replacement_time_ticks": 700000, "replacement_order": 0,
        "cancelled_hits": 4}]
    assert record["stats"] == {"matched_actions": 2, "planned_hits": 12, "generated_hits": 8,
        "landed_hits": 8, "missed_hits": 0, "cancelled_hits": 4}
    assert sorted(hit["t"] for hit in out["result"]["hits"]) == [.2, .45, .8, 1.05, 1.3, 1.55, 1.7, 1.8]
    generated = record["generated"]
    assert {row["instance_generation"] for row in generated} == {1, 2}
    assert all(row["action_id"] != "q1" or row["t"] < .7 for row in generated)


def test_review_derived_count_refuses_fractional_tail(periodic_db):
    plan = refreshing_plan(periodic_db)
    plan["action_event_profiles"][0]["lifecycle"]["instance_end_offset_seconds"] = 1.21
    out = compose_equipment_plan(plan, periodic_db)
    assert out["state"] == "pending"
    assert out["action_event_compilations"][0]["generated"] == []
    assert "不能整除" in str(out["action_event_compilations"][0]["issues"])


def test_review_refresh_requires_independent_no_early_despawn_fact(periodic_db):
    plan = refreshing_plan(periodic_db)
    plan["action_event_profiles"][0]["lifecycle"]["no_early_despawn"] = False
    with pytest.raises(PlanError, match="提前消失"):
        validate_plan(plan)


@pytest.mark.parametrize("start,period,ready", [(10, 11, False), (20, 21.2, False), (20, 21.200001, True)])
def test_review_second_third_round_use_absolute_end_bounds(periodic_db, start, period, ready):
    plan = source_bound_plan(periodic_db)
    rotation = plan["rotation_model"]
    rotation["actions"] = [{**rotation["actions"][0], "start": start, "occupation_end": start+.5}]
    rotation["events"] = [{**rotation["events"][0], "t": start}]
    rotation["period"] = plan["manual_template"]["loop_duration"] = period
    accept_periodic(plan, periodic_db)
    out = compose_equipment_plan(plan, periodic_db)
    record = out["action_event_compilations"][0]
    if ready:
        assert out["state"] == "ready", out["missing_inputs"]
        assert [g["expected_time_ticks"] for g in record["generated"]] == [start*1_000_000+t for t in (200000,450000,700000,950000,1100000)]
    else:
        assert out["state"] == "pending" and record["generated"] == []
        assert "绝对结束" in str(record["issues"])


@pytest.mark.parametrize("edit", ["label", "route", "array_order", "shared_contact"])
def test_review_nonsemantic_aliases_keep_one_acceptance_but_routes_keep_stable_meaning(periodic_db, edit):
    plan = accept_periodic(source_bound_plan(periodic_db), periodic_db)
    before = run_plan_calc(plan, periodic_db)
    profile = plan["action_event_profiles"][0]
    if edit == "label":
        plan["manual_template"]["hits"][0]["hit_name"] = "本地中文别名"
    elif edit == "route":
        profile["channels"][0]["channel_id"] = "renamed.slice"
        plan["periodic_attack_sources"][0]["channels"][0]["channel_id"] = "renamed.slice"
    elif edit == "array_order":
        profile["channels"].reverse()
        plan["periodic_attack_sources"][0]["channels"].reverse()
    else:
        profile["channels"][1]["contact_profile_id"] = "contact.slice"
        plan["mechanics_model"]["elemental_model"]["contact_profiles"].pop()
    after = run_plan_calc(plan, periodic_db)
    original = before["report"]["action_event_compilations"][0]
    current = after["report"]["action_event_compilations"][0]
    assert current["profile_fingerprint"] == original["profile_fingerprint"]
    assert sorted((h["t"], h["expected"]) for h in before["result"]["hits"]) == sorted((h["t"], h["expected"]) for h in after["result"]["hits"])
    if edit == "route":
        assert current["instance_fingerprint"] != original["instance_fingerprint"]
    else:
        assert {g["hit_id"] for g in current["generated"]} == {g["hit_id"] for g in original["generated"]}


@pytest.mark.parametrize("edit", ["lifetime", "contact_basis", "shared_icd", "candidate_pair"])
def test_review_semantic_changes_never_reuse_old_acceptance(periodic_db, edit):
    plan = accept_periodic(source_bound_plan(periodic_db), periodic_db)
    profile = plan["action_event_profiles"][0]
    if edit == "lifetime":
        profile["lifecycle"]["instance_end_offset_seconds"] = 1.3
    elif edit == "contact_basis":
        plan["mechanics_model"]["elemental_model"]["contact_profiles"][0]["source"] += " 新依据"
    elif edit == "shared_icd":
        plan["periodic_attack_sources"][0]["channels"][0]["counter_tag"] = "another.explicit.group"
    else:
        channels = plan["periodic_attack_sources"][0]["channels"]
        channels[0]["candidate_id"], channels[1]["candidate_id"] = channels[1]["candidate_id"], channels[0]["candidate_id"]
    out = compose_equipment_plan(plan, periodic_db)
    assert out["state"] == "pending"
    assert all(r["generated"] == [] for r in out["action_event_compilations"])


def test_review_same_tick_contacts_require_explicit_distinct_order(periodic_db):
    plan = source_bound_plan(periodic_db)
    plan["action_event_profiles"][0]["channels"][1]["schedule"]["offset_seconds"] = .95
    accept_periodic(plan, periodic_db)
    pending = compose_equipment_plan(plan, periodic_db)
    assert pending["state"] == "pending" and pending["action_event_compilations"][0]["generated"] == []
    plan["action_event_overrides"] = [{"kind": KIND, "action_id": f"q{i}", "contacts": [
        {"channel_id": "slice", "ordinal": 4, "order": 3, **EVIDENCE},
        {"channel_id": "blast", "ordinal": 1, "order": 4, **EVIDENCE}]} for i in (1,2,3)]
    out = run_plan_calc(plan, periodic_db)
    assert len(out["result"]["hits"]) == 15
    plan["action_event_overrides"][1]["contacts"][1]["order"] = 3
    pending = compose_equipment_plan(plan, periodic_db)
    assert pending["state"] == "pending" and pending["action_event_compilations"][0]["generated"] == []


def one_action(plan):
    plan["rotation_model"]["actions"] = plan["rotation_model"]["actions"][:1]
    plan["rotation_model"]["events"] = plan["rotation_model"]["events"][:1]
    plan["rotation_model"]["period"] = plan["manual_template"]["loop_duration"] = 3
    return plan


@pytest.mark.parametrize("sampling,phase,expected", [
    ("action_start", "pre", 990), ("action_start", "post", 495),
    ("current", "condition", 900), ("action_start", "condition", 495),
    ("action_start", "enemy", 540),
])
def test_review_snapshot_phase_is_distinct_from_later_self_and_enemy_sampling(periodic_db, fake_repo, sampling, phase, expected):
    from tests.test_action_events_review import (
        attach_mechanics_rules,
        review_manual_sources,
    )
    from tests.test_mechanics_execution import rule

    # Only fixture scalar values are changed; the real parameter/attack parent
    # relation still has to compile. Independent oracle is 4*2+3, not DB output.
    path = fake_repo / "ExcelBinOutput/ProudSkillExcelConfigData.json"
    raw = json.loads(path.read_text("utf8"))
    raw[0]["paramList"] = [2, 3, 5]
    path.write_text(json.dumps(raw), "utf8")
    fixture_builder.build_database(periodic_db.parent, force=True)
    plan = one_action(source_bound_plan(periodic_db))
    plan["rotation_model"]["actions"][0]["occupation_end"] = .1
    profile, model = plan["action_event_profiles"][0], plan["mechanics_model"]
    profile["lifecycle"]["instance_end_offset_seconds"] = 1.3
    profile["channels"][0]["schedule"]["first_offset_seconds"] = .25
    profile["channels"][1]["schedule"]["offset_seconds"] = 1.25
    for channel in profile["channels"]:
        channel["self_sampling"] = sampling
    if phase in ("pre", "post"):
        trigger = {"event": "action_start", "phase": phase, "action_ids": ["q1"]}
    else:
        trigger = {"event": "condition", "phase": "pre", "event_ids": ["panel_change"]}
        model["condition_events"] = [{"id": "panel_change", "t": .5, "order": -1,
            "condition": "declared", "value": True, "repeat": {"mode": "once"}, **EVIDENCE}]
        plan["action_event_overrides"] = [{"kind": KIND, "action_id": "q1", "contacts": [
            {"channel_id": "slice", "ordinal": 2, "order": 10, **EVIDENCE}]}]
    attach_mechanics_rules(plan, rule("panel_change", "benefit.res_shred.cryo" if phase == "enemy" else "benefit.stat_add.atk",
        .1 if phase == "enemy" else 100, trigger=trigger, domain="enemy" if phase == "enemy" else "self", duration=2))
    review_manual_sources(plan, periodic_db)
    accept_periodic(plan, periodic_db)
    report = compose_equipment_plan(plan, periodic_db)
    assert report["state"] == "ready", report["missing_inputs"]
    out = run_plan_calc(plan, periodic_db)
    assert sum(h["expected"] for h in out["result"]["hits"]) == pytest.approx(expected)


@pytest.mark.parametrize("start,allowed", [(.4, False), (.5, True)])
def test_review_switch_before_occupation_cancels_but_after_completion_keeps_offfield_hits(periodic_db, start, allowed):
    from tests.test_mechanics_execution import second_character

    plan = one_action(source_bound_plan(periodic_db))
    second_character(plan["manual_template"], plan["rotation_model"])
    plan["manual_template"]["characters"][1]["stats"]["energy_recharge"] = 1
    plan["rotation_model"]["abilities"].append(ability("switch", cid="b", kind="action.switch"))
    plan["rotation_model"]["actions"].append(action("switch", "switch", start=start, end=start,
        order=3, repeat={"mode": "once"}, switch_to="b"))
    accept_periodic(plan, periodic_db)
    out = compose_equipment_plan(plan, periodic_db)
    if allowed:
        assert out["state"] == "ready", out["missing_inputs"]
        assert len(out["action_event_compilations"][0]["generated"]) == 5
    else:
        assert out["state"] == "pending"
        assert "提前切人" in str(out["action_event_compilations"][0]["issues"])


def test_review_same_skill_alias_establishing_at_inclusive_instance_end_is_overlap(periodic_db):
    plan = one_action(source_bound_plan(periodic_db))
    alias = copy.deepcopy(plan["rotation_model"]["abilities"][0])
    alias["id"] = "q.alias"
    plan["rotation_model"]["abilities"].append(alias)
    # Establishment is +0.1: a 1.1 cast establishes exactly at the old
    # inclusive 1.2 end and therefore still needs an explicit replacement policy.
    plan["rotation_model"]["actions"].append(action("q.alias.cast", "q.alias", start=1.1, end=1.6, repeat={"mode": "once"}))
    plan["action_event_bindings"].append({"ability_id": "q.alias", "profile_id": "periodic"})
    accept_periodic(plan, periodic_db)
    out = compose_equipment_plan(plan, periodic_db)
    assert out["state"] == "pending"
    assert out["action_event_compilations"][0]["generated"] == []
    assert "生命周期重叠" in str(out["action_event_compilations"][0]["issues"])


def test_review_overlap_uses_establishment_time_not_action_start(periodic_db):
    plan = one_action(source_bound_plan(periodic_db))
    alias = copy.deepcopy(plan["rotation_model"]["abilities"][0])
    alias["id"] = "q.alias"
    plan["rotation_model"]["abilities"].append(alias)
    # The action starts at the old end, but the new instance establishes at
    # 1.3, strictly after the old inclusive 1.2 end.
    plan["rotation_model"]["actions"].append(action(
        "q.alias.cast", "q.alias", start=1.2, end=1.7, repeat={"mode": "once"}))
    plan["action_event_bindings"].append({"ability_id": "q.alias", "profile_id": "periodic"})
    accept_periodic(plan, periodic_db)
    out = compose_equipment_plan(plan, periodic_db)
    assert out["state"] == "ready", out["missing_inputs"]


def test_review_periodic_source_budget_preserves_tail_pending_identity_without_materializing(periodic_db, monkeypatch):
    from genshin_app.equipment import periodic_sources

    plan = source_bound_plan(periodic_db)
    duplicate = copy.deepcopy(plan["action_event_profiles"][0])
    duplicate["id"] = "tail"
    source = copy.deepcopy(plan["periodic_attack_sources"][0])
    source["action_event_profile_id"] = "tail"
    for channel in duplicate["channels"]:
        hit = copy.deepcopy(next(h for h in plan["manual_template"]["hits"] if h["id"] == channel["prototype_hit_id"]))
        hit["id"] += ".tail"
        channel["prototype_hit_id"] = hit["id"]
        channel["application_profile_id"] += ".tail"
        plan["manual_template"]["hits"].append(hit)
    plan["action_event_profiles"].append(duplicate)
    plan["periodic_attack_sources"].append(source)
    template = copy.deepcopy(plan["manual_template"])
    before = copy.deepcopy((plan, template))
    monkeypatch.setattr(periodic_sources, "MAX_RECEIPT_BYTES", 1024)
    issues = []
    prepared = periodic_sources.prepare_periodic_sources(plan, template, periodic_db, lambda *args: issues.append(args))
    assert (plan, template) == before and prepared.sources == []
    assert prepared.parameter_contexts == {} and prepared.attenuation_profiles == {}
    assert [r["action_event_profile_id"] for r in prepared.records] == ["periodic", "tail"]
    assert all(r["status"] == "pending" and not r["compiled_fields"] and not r["query"] for r in prepared.records)
    assert issues
