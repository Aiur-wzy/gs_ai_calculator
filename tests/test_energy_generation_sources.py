"""EC16b App-owned particle delivery boundaries, independent of formal DB."""
from __future__ import annotations

import copy

import pytest
from test_freeze_runtime import model as freeze_model
from test_mechanics_execution import (
    APPLICATION_VERIFICATION,
    EVIDENCE,
    action,
    fixture,
    rule,
)
from test_pipeline import fake_repo  # noqa: F401
from test_runtime_energy_dispatch import (  # noqa: F401
    identity,
    origin,
    trigger_db,
    trigger_repo,
)

from genshin_app.equipment import (
    compose_equipment_plan,
    enable_mechanics,
    new_plan,
    run_plan_calc,
)
from genshin_app.equipment.energy_generation_receipt import (
    _ledger,
    energy_generation_summary,
)
from genshin_app.equipment.energy_generation_schema import (
    DEFAULT_SOURCE,
    energy_generation_contract,
)
from genshin_app.equipment.energy_generation_sources import (
    _delivery,
    _migration,
    _Pending,
    _ticks,
)
from genshin_app.equipment.schema import PlanError, validate_plan
from genshin_app.rotation_check import describe_rotation_check
from genshin_db.runtime_queries import query_runtime
from genshin_engine.mechanics import execute_mechanics

# ruff: noqa: F811


def source_declaration():
    source = copy.deepcopy(DEFAULT_SOURCE)
    source.update(id="source", action_event_profile_id="contact", origin_reference_id="a" * 64)
    source["scenario"].update(target_kind="ordinary_non_animal_monster", drop_allowed=True,
        initial_controller="inactive", pickup="all_at_once", hitlag_policy="zero_hitlag_reference",
        no_external_controller_changes=True, all_party_alive=True, **EVIDENCE)
    source["calibration"].update(spawn_delay_seconds=.1, travel_seconds=.2, precision_seconds=.01, **EVIDENCE)
    return source


def energy_plan(db, *, variant="tap", local=False):
    template, rotation, mechanics = fixture((0,))
    template["characters"][0]["stats"]["energy_recharge"] = 1
    rotation["characters"][0].update(initial_energy=0, energy_capacity=100, element="cryo" if local else "anemo")
    plan = enable_mechanics(new_plan(template))
    prototype = plan["manual_template"]["hits"][0]
    prototype.pop("multiplier")
    prototype.update(scaling_stat="atk", element="cryo" if local else "anemo")
    rotation["abilities"][0]["variant"] = variant
    rotation["actions"] = [action(f"cast{i}", start=i*2, end=i*2+1, repeat={"mode": "once"}) for i in range(3)]
    mechanics["hit_events"] = []
    mechanics["elemental_model"] = freeze_model()
    mechanics["elemental_model"]["contact_profiles"] = [{"id": "safe", "freeze_interaction": "none", **EVIDENCE}]
    plan.update(rotation_model=rotation, mechanics_model=mechanics)
    ident = identity(db, variant, local)
    selection = ident["selection"]
    plan["action_event_profiles"] = [{"id": "contact", "kind": "calibrated_single_contact_v1", "prototype_hit_id": "h0",
        "variant": variant, "calibration": {"offset_seconds": .2, "precision_seconds": .01, **EVIDENCE},
        "application_profile_id": "cryo" if local else "from.metadata", "contact_profile_id": "safe", "default_landed": True,
        "self_sampling": "current", "accepted_profile_fingerprint": None}]
    plan["action_event_bindings"] = [{"ability_id": "e", "profile_id": "contact"}]
    plan["action_event_overrides"] = []
    if local:
        q = query_runtime(db, {"action": "action_parameter", "param_index": ident["param_index"], **selection})
        plan["hit_parameter_sources"] = [{"hit_id": "h0", "version_line": "release", "selection": selection,
            "param_index": ident["param_index"], "scaling_stat": "atk", "purpose": "single_stat_damage_multiplier", **EVIDENCE,
            "expected_snapshot_id": q["snapshot"]["snapshot_id"], "expected_node_sha256": q["source"]["node_sha256"]}]
    else:
        prototype.pop("element")
        mechanics["elemental_model"]["profiles"] = []
        plan["attack_metadata_sources"] = [{"action_event_profile_id": "contact", "version_line": "release",
            "selection": selection, "candidate_id": ident["metadata_candidate_id"],
            "counter_tag": "declared.shared", "application_mode": "sequence",
            "verification": copy.deepcopy(APPLICATION_VERIFICATION),
            "purpose": "single_stat_damage_multiplier", **EVIDENCE}]
    preview = compose_equipment_plan(plan, db)
    plan["action_event_profiles"][0].update(preview["action_event_compilations"][0]["required_confirmation"])
    binding = source_declaration()
    binding["origin_reference_id"] = origin(db, variant, local)
    plan["energy_generation_sources"] = [binding]
    return plan


def confirm_energy(plan, db):
    report = compose_equipment_plan(plan, db)
    record = report["energy_generation_compilations"][0]
    assert record["required_confirmation"], (record, report["missing_inputs"])
    plan["energy_generation_sources"][0].update(record["required_confirmation"])
    return plan


@pytest.mark.parametrize("variant,local,count", [("tap", False, 3), ("hold", True, 4), ("hold", False, 4)])
def test_actual_sources_automatically_dispatch_three_explicit_initial_hits(trigger_db, variant, local, count):
    plan = confirm_energy(energy_plan(trigger_db, variant=variant, local=local), trigger_db)
    before = copy.deepcopy(plan)
    calculated = run_plan_calc(plan, trigger_db)
    assert plan == before
    assert calculated["plan"]["mechanics_model"]["rules"] == []
    assert calculated["plan"]["rotation_model"]["events"] == []
    result, report = calculated["result"], calculated["report"]
    assert result["energy_generation_summary"]["spawned_particles"] == 3 * count
    assert result["energy_generation_summary"]["credited_particles"] == 3 * count
    assert len(report["mechanics_model"]["rules"]) == 1
    assert len(result["hits"]) == 3
    assert report["energy_generation_compilations"][0]["trigger_certificate"]["predicate_semantics"] == (
        "reference_adapted_not_decoded" if local else "decoded_local_shape")


@pytest.mark.parametrize("field,value", [("hitlag_policy", "unknown"), ("drop_allowed", None),
    ("initial_controller", "unknown"), ("target_kind", "unknown"), ("all_party_alive", False), ("source", " ")])
def test_unknown_scene_never_becomes_confirmable(trigger_db, field, value):
    plan = energy_plan(trigger_db)
    plan["energy_generation_sources"][0]["scenario"][field] = value
    out = compose_equipment_plan(plan, trigger_db)
    record = out["energy_generation_compilations"][0]
    assert out["state"] == "pending" and record["required_confirmation"] is None
    assert record["rules"] == record["parts"] == record["resource_ownership"] == []


def test_miss_suppresses_particles_but_attachment_icd_does_not(trigger_db):
    plan = energy_plan(trigger_db)
    plan["action_event_overrides"] = [{"action_id": "cast2", "landed": False, "order": 20, **EVIDENCE}]
    plan = confirm_energy(plan, trigger_db)
    out = run_plan_calc(plan, trigger_db)
    summary = out["result"]["energy_generation_summary"]
    assert summary["missed_parent_hits"] == 1 and summary["spawned_particles"] == 6
    applications = [r for r in out["result"]["elemental"]["trace"] if r["type"] == "elemental_application"]
    assert [r["allowed"] for r in applications] == [True, False, False]


def test_instance_time_and_order_change_rechecks_execution_without_reaccepting_source(trigger_db):
    plan = confirm_energy(energy_plan(trigger_db), trigger_db)
    before = compose_equipment_plan(plan, trigger_db)["energy_generation_compilations"][0]
    plan["rotation_model"]["actions"][1].update(start=2.1, occupation_end=3.1)
    after = compose_equipment_plan(plan, trigger_db)["energy_generation_compilations"][0]
    assert after["status"] == "ready"
    assert before["acceptance_fingerprint"] == after["acceptance_fingerprint"]
    assert before["execution_fingerprint"] != after["execution_fingerprint"]


def test_manual_spawn_requires_explicit_migration_and_keeps_native_events(trigger_db):
    plan = energy_plan(trigger_db)
    original = [{"id": "old", "type": "energy_spawn", "t": .3, "order": 30, "action_id": "cast0", "count": 3,
        "element": "anemo", "particle_type": "particle", "source_kind": "skill", "repeat": {"mode": "once"}, **EVIDENCE},
        {"id": "collect", "type": "energy_credit", "credit_at": .5, "order": 31, "action_id": "cast0", "spawn_id": "old",
         "count": 3, "repeat": {"mode": "once"}, **EVIDENCE}]
    plan["rotation_model"]["events"] = copy.deepcopy(original)
    report = compose_equipment_plan(plan, trigger_db)
    assert report["state"] == "pending"
    assert report["energy_generation_compilations"][0]["required_confirmation"] is None
    plan["energy_generation_sources"][0]["replaces_spawn_ids"] = ["old"]
    out = run_plan_calc(confirm_energy(plan, trigger_db), trigger_db)
    assert out["plan"]["rotation_model"]["events"] == original
    assert not any(r["id"] in ("old", "collect") for r in out["result"]["rotation_check"]["event_log"])
    assert out["result"]["energy_generation_summary"]["credited_particles"] == 9


def test_receipt_limit_preserves_every_native_identity_and_publishes_no_rules(trigger_db, monkeypatch):
    from genshin_app.equipment import energy_generation_sources as compiler
    plan = energy_plan(trigger_db)
    tail = copy.deepcopy(plan["energy_generation_sources"][0])
    tail.update(id="tail", action_event_profile_id=None, origin_reference_id=None)
    plan["energy_generation_sources"].append(tail)
    monkeypatch.setattr(compiler, "MAX_RECEIPT_BYTES", 1024)
    out = compose_equipment_plan(plan, trigger_db)
    records = out["energy_generation_compilations"]
    assert [r["id"] for r in records] == ["source", "tail"]
    assert all(r["status"] == "pending" and r["source_bundle"] is None and r["rules"] == [] for r in records)
    assert records[0]["issues"][0]["code"] == "energy_generation_receipt_limit"


def test_unknown_draft_can_be_saved_without_reading_missing_source(trigger_db):
    plan = energy_plan(trigger_db)
    plan["energy_generation_sources"] = [copy.deepcopy(DEFAULT_SOURCE)]
    validate_plan(plan)
    record = compose_equipment_plan(plan, trigger_db)["energy_generation_compilations"][0]
    assert record["source_bundle"] is None and record["required_confirmation"] is None


@pytest.mark.parametrize("local,spacing,ready", [(False, .200001, False), (False, .200002, True),
                                               (True, .5, False), (True, .500001, True)])
def test_shared_isolation_uses_actual_hit_ticks_and_strict_reference_boundary(trigger_db, local, spacing, ready):
    plan = energy_plan(trigger_db, variant="hold" if local else "tap", local=local)
    plan["action_event_profiles"][0]["calibration"]["offset_seconds"] = .05
    plan["rotation_model"]["actions"] = [
        action(f"cast{i}", start=i*spacing, end=i*spacing+.1, repeat={"mode": "once"}) for i in range(2)]
    profile = plan["action_event_profiles"][0]
    parent = compose_equipment_plan(plan, trigger_db)["action_event_compilations"][0]
    profile.update(parent["required_confirmation"])
    report = compose_equipment_plan(plan, trigger_db)
    record = report["energy_generation_compilations"][0]
    assert bool(record["required_confirmation"]) is ready
    if ready:
        out = run_plan_calc(confirm_energy(plan, trigger_db), trigger_db)
        assert out["result"]["energy_generation_summary"]["credited_batches"] == 2


def test_read_only_receipt_never_needs_a_database_after_calculation(trigger_db, monkeypatch):
    import sqlite3

    from genshin_app.equipment.energy_generation_receipt import (
        validate_energy_generation_receipt,
    )
    from genshin_app.equipment.service import result_bytes
    out = run_plan_calc(confirm_energy(energy_plan(trigger_db), trigger_db), trigger_db)
    def no_db(*args, **kwargs):
        raise AssertionError("离线导出重新访问数据库")
    monkeypatch.setattr(sqlite3, "connect", no_db)
    validate_energy_generation_receipt(out["plan"], out["result"], out["report"], out["result_receipt"])
    assert b'energy_generation_summary' in result_bytes(out["result"], out["result_receipt"], "json")


def test_resource_receipt_accepts_actual_dynamic_er_and_explicit_er_windows(trigger_db):
    from test_action_events import confirm_action_events
    from test_plan_mechanics import native, review

    from genshin_app.equipment.service import result_bytes
    plan = energy_plan(trigger_db)
    extra = native()
    plan["effect_bindings"] = copy.deepcopy(extra["effect_bindings"])
    boost = rule(source_id="binding:part", benefit="benefit.energy_recharge.all", value=.5,
        trigger={"event": "hit", "phase": "post", "action_ids": ["cast0"]}, duration=1)
    plan["mechanics_model"]["rules"] = [boost]
    plan["mechanics_model"]["initial_state"]["rules"] = [{"rule_id": boost["id"], "cooldown_remaining": 0, "instances": []}]
    plan["rotation_model"]["energy_recharge_windows"] = [{"id": "window", "character": "a", "start": 0, "end": 1,
        "delta": .25, "repeat": {"mode": "once"}, **EVIDENCE}]
    review(plan, trigger_db)
    confirm_action_events(plan, trigger_db)
    out = run_plan_calc(confirm_energy(plan, trigger_db), trigger_db)
    credit = next(r for r in out["result"]["rotation_check"]["event_log"] if r["type"] == "energy_credit")
    assert credit["energy"][0]["energy_recharge"] == 1.75
    assert credit["energy"][0]["raw"] == 15.75
    assert result_bytes(out["result"], out["result_receipt"], "xlsx")[:2] == b"PK"


def test_resource_ledger_binds_actual_er_even_if_local_arithmetic_is_rewritten(trigger_db):
    from genshin_app.equipment.files import fingerprint
    from genshin_app.equipment.service import result_bytes
    out = run_plan_calc(confirm_energy(energy_plan(trigger_db), trigger_db), trigger_db)
    rows = out["result"]["rotation_check"]["event_log"]
    first = next(r for r in rows if r["type"] == "energy_credit")["energy"][0]
    first.update(energy_recharge=2, raw=18, gain=18, after=18)
    out["result_receipt"]["result_fingerprint"] = fingerprint(out["result"])
    with pytest.raises(PlanError):
        result_bytes(out["result"], out["result_receipt"], "xlsx")


def particle_execution(*, travel=.2, switch=False):
    template, rotation, mechanics = fixture((.2,))
    rotation["actions"][0]["repeat"] = {"mode": "once"}
    rotation["characters"][0].update(initial_energy=0, energy_capacity=100)
    mechanics["elemental_model"] = freeze_model()
    mechanics["hit_events"][0].update(application={"profile_id": "cryo", "target_id": "enemy", "landed": True},
        contact={"kind": "none", **EVIDENCE})
    binding = source_declaration()
    binding["calibration"]["travel_seconds"] = travel
    link = {"action_id": "cast", "hit_id": "h0", "order": 10, "absolute_tick": 200000, "landed": True}
    projection = {"count": 3, "element": "cryo", "particle_type": "particle"}
    stage = _delivery(binding, "a", [link], projection, {"events": []})
    mechanics["rules"] = stage["rules"]
    mechanics["initial_state"]["rules"] = stage["initial_rules"]
    mechanics["resource_ownership"] = stage["resource_ownership"]
    if switch:
        template["characters"].append({"id": "b", "level": 90, "stats": {"atk": 1, "em": 0}})
        rotation["characters"].append({"id": "b", "element": "cryo", "energy_recharge": 1,
            "initial_energy": 0, "energy_capacity": 100, **EVIDENCE})
        template["switches"] = [{"t": .4, "character": "b"}]
    out = execute_mechanics(template, rotation, mechanics)
    assert out["result"] is not None, out
    result = out["result"]
    result["rotation_check"] = describe_rotation_check(out["rotation_check"], template, rotation, [], mechanics_model=mechanics)
    record = {"actor": "a", "parent_links": [link], "resource_projection": projection,
              "calibration": binding["calibration"], **stage}
    return result, record


def test_spawn_and_arrival_are_distinct_and_decimal_delay_addition_is_exact():
    result, record = particle_execution()
    _ledger(result, [record])
    rows = [r for r in result["rotation_check"]["event_log"] if r["type"] in ("energy_spawn", "energy_credit")]
    assert [(r["type"], r["t"], r["count"]) for r in rows] == [("energy_spawn", .3, 3), ("energy_credit", .5, 3)]
    summary = energy_generation_summary(result, [record])
    assert summary["eligible_parent_hits"] == 1
    assert summary["spawned_particles"] == summary["credited_particles"] == 3
    assert summary["in_flight_particles"] == 0


@pytest.mark.parametrize("remove", ["energy_spawn", "energy_credit"])
def test_audit_cannot_omit_a_generated_resource_inside_checked_window(remove):
    result, record = particle_execution()
    rows = result["rotation_check"]["event_log"]
    rows[:] = [r for r in rows if r["type"] != remove]
    with pytest.raises(ValueError):
        _ledger(result, [record])


def test_explicit_once_source_does_not_repeat_in_later_check_windows():
    result, record = particle_execution(travel=10.2)
    _ledger(result, [record])
    rows = result["rotation_check"]["event_log"]
    assert sum(r["type"] == "energy_spawn" for r in rows) == 1
    assert sum(r["type"] == "energy_credit" for r in rows) == 1
    assert energy_generation_summary(result, [record])["credited_particles"] == 3


def test_migration_preserves_unrelated_direct_refund_ownership():
    _, rotation, mechanics = fixture()
    direct = rule(resources=[{"id": "refund", "kind": "energy_direct", "delay": 0,
        "amounts": {"a": 5}, "source_part": "refund"}])
    mechanics["rules"] = [direct]
    mechanics["resource_ownership"] = [{"rule_id": direct["id"], "action_id": "cast",
        "source_part": "refund", "replaces_event_ids": []}]
    plan = {"rotation_model": rotation, "mechanics_model": mechanics}
    assert _migration(plan, source_declaration(), [{"action_id": "cast", "actor": "a"}])["events"] == []


@pytest.mark.parametrize("event_type", ["energy_direct", "energy_spend", "cooldown_change"])
def test_migration_cannot_take_over_other_event_types(event_type):
    _, rotation, mechanics = fixture()
    rotation["events"] = [{"id": "old", "type": event_type, "action_id": "cast"}]
    binding = source_declaration()
    binding["replaces_spawn_ids"] = ["old"]
    with pytest.raises(_Pending):
        _migration({"rotation_model": rotation, "mechanics_model": mechanics}, binding, [{"action_id": "cast", "actor": "a"}])


@pytest.mark.parametrize("value", [True, None, float("nan"), float("inf"), -.1, .0000001, 10**400])
def test_time_calibration_has_no_float_rounding_or_bool_fallback(value):
    with pytest.raises(ValueError):
        _ticks(value)


def test_defaults_are_unknown_and_capabilities_detached():
    caps = energy_generation_contract()
    assert caps["default_source"]["scenario"]["hitlag_policy"] == "unknown"
    caps["default_source"]["scenario"]["hitlag_policy"] = "zero_hitlag_reference"
    assert energy_generation_contract()["default_source"]["scenario"]["hitlag_policy"] == "unknown"


def test_generated_namespace_is_reserved_even_without_a_binding():
    template, _, _ = fixture()
    plan = new_plan(template)
    plan["manual_template"]["hits"][0]["id"] = "auto.energy.fake"
    with pytest.raises(PlanError, match="命名空间"):
        validate_plan(plan)
