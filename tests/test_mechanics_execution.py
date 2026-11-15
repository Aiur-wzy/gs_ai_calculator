"""D20 deterministic engineering cases, intentionally not a benchmark answer key."""
from __future__ import annotations

import copy

import pytest

from genshin_engine.mechanics import execute_mechanics, validate_mechanics_model
from genshin_engine.rotation import check_rotation

EVIDENCE = {"status": "assumed", "source": "虚构确定性工程情景"}
APPLICATION_VERIFICATION = {
    "status": "confirmed",
    "locator": "tests/test_mechanics_execution.py::APPLICATION_VERIFICATION",
    "version": "fixture-v1",
    "reviewed_at": "2026-10-18",
    "component_scope": "the exact selected synthetic attack component and its declared counter",
    "confirmed_fields": ["application_mode", "counter_tag", "shared_scope"],
}
REPEAT = {"mode": "each_cycle"}


def ability(aid="e", cid="a", kind="action.elemental_skill", energy=None):
    return {"id": aid, "character": cid, "action_type_id": kind, "cooldown": None, "energy": energy, **EVIDENCE}


def action(aid="cast", bid="e", start=0, end=.1, order=0, **kwargs):
    return {"id": aid, "ability_id": bid, "start": start, "occupation_end": end, "order": order, "repeat": REPEAT, **EVIDENCE, **kwargs}


def hit(hid, t, cid="a", kind="damage.elemental_skill"):
    return {"id": hid, "t": t, "character": cid, "skill": "工程技能", "hit_name": hid,
            "damage_type_ids": [kind], "damage_system": "classic", "element": "cryo", "multiplier": 1}


def fixture(times=(.2, .3, .4)):
    t = {"version": 2, "form": "resolved", "id_registry_version": 1, "type_definitions": {"damage_types": []},
         "enemy": {"level": 90, "resistance": {"default": .1}},
         "characters": [{"id": "a", "level": 90, "stats": {"atk": 100, "base_atk": 50, "em": 0, "crit_rate": 0, "crit_dmg": .5}}],
         "hits": [hit(f"h{i}", time) for i, time in enumerate(times)], "buffs": [], "switches": [], "loop_duration": 10}
    r = {"schema_version": 1, "period": 10, "max_cycles": 3, "initial_frontline": "a",
         "characters": [{"id": "a", "element": "cryo", "energy_recharge": 1, "energy_capacity": 10, "initial_energy": 10, **EVIDENCE}],
         "abilities": [ability()], "actions": [action()], "events": [], "initial_cooldowns": []}
    m = {"schema_version": 1, "rules": [], "hit_events": [{"hit_id": h["id"], "action_id": "cast", "order": 10+i} for i, h in enumerate(t["hits"])],
         "hit_patterns": [], "condition_events": [], "initial_state": {"rules": []}}
    return t, r, m


def rule(rid="boost", benefit="benefit.dmg_bonus.all", value=1, trigger=None, domain="self", duration=2, **kw):
    return {"id": rid, "name_zh": "工程收益", "owner": "a", "source_id": rid,
            "trigger": trigger or {"event": "action_start", "phase": "pre", "action_ids": ["cast"]},
            "beneficiaries": {"mode": "team", "scope": "locked"},
            "timing": {"delay": 0, "duration": duration, "cooldown": 0, "cooldown_anchor": "success"},
            "stacking": {"max": 1, "add": 1, "policy": "refresh"},
            "effects": [{"id": "value", "benefit_type_id": benefit, "value": value, "domain": domain}], **EVIDENCE, **kw}


def add(m, *rules):
    m["rules"].extend(rules)
    m["initial_state"]["rules"].extend({"rule_id": r["id"], "cooldown_remaining": 0, "instances": []} for r in rules)


def run(t, r, m, matches=None):
    out = execute_mechanics(t, r, m, matches)
    assert out["status"] in ("passed", "failed", "unproven", "pending"), out["issues"]
    return out


def damages(out):
    return [h["expected"] for h in out["result"]["hits"]]


def test_post_hit_boost_does_not_affect_parent_and_same_tick_next_hit_sees_it():
    t, r, m = fixture((.2, .2, .4))
    add(m, rule(trigger={"event": "hit", "phase": "post", "event_ids": ["h0"]}, duration=.2))
    original = copy.deepcopy((t, r, m))
    out = run(t, r, m)
    assert damages(out) == pytest.approx([45, 90, 45])
    assert (t, r, m) == original
    assert out["status"] == "passed"


def test_pre_hit_boost_can_affect_its_parent_and_expiration_is_half_open():
    t, r, m = fixture((.2, .3, .4))
    add(m, rule(trigger={"event": "hit", "phase": "pre", "event_ids": ["h0"]}, duration=.2))
    assert damages(run(t, r, m)) == pytest.approx([90, 90, 45])


def test_quota_shared_across_attack_categories_and_excluded_damage_still_consumes():
    t, r, m = fixture((.2, .3, .4, .5))
    for h, kind in zip(t["hits"], ["normal_attack", "elemental_burst", "elemental_skill", "elemental_burst"]):
        h["damage_type_ids"] = ["damage."+kind]
    m["hit_events"][0]["report"] = False
    add(m, rule(quota={"capacity": 3, "scope": "per_beneficiary", "refresh": "reset", "consume": {"event": "hit", "phase": "pre"}}))
    out = run(t, r, m)
    assert damages(out) == pytest.approx([90, 90, 45])
    assert [x["after"] for x in out["mechanics_trace"] if x["type"] == "quota" and x["cycle"] == 0] == [2, 1, 0, 0]


def test_match_cannot_bypass_consumption_filter_or_quota_and_exclude_keeps_consumption():
    t, r, m = fixture((.2, .3, .4))
    add(m, rule(quota={"capacity": 1, "scope": "team", "refresh": "reset", "consume": {"event": "hit", "phase": "pre", "event_ids": ["h0", "h1"]}}))
    matches = [{"rule_id": "boost", "effect_id": "value", "hit_id": "h0", "operation": "exclude"},
               {"rule_id": "boost", "effect_id": "value", "hit_id": "h1", "operation": "include"},
               {"rule_id": "boost", "effect_id": "value", "hit_id": "h2", "operation": "include"}]
    assert damages(run(t, r, m, matches)) == pytest.approx([45, 45, 45])


@pytest.mark.parametrize(("policy", "expected"), [("refresh", [90, 90, 90]), ("independent", [90, 135, 90])])
def test_refresh_does_not_add_layers_but_independent_layers_expire_separately(policy, expected):
    t, r, m = fixture((.2, .4, 1.2))
    add(m, rule(trigger={"event": "hit", "phase": "pre", "event_ids": ["h0", "h1"]}, duration=1,
                stacking={"max": 2, "add": 1, "policy": policy}))
    assert damages(run(t, r, m)) == pytest.approx(expected)


def second_character(t, r, cid="b"):
    t["characters"].append({"id": cid, "level": 90, "stats": {"atk": 200, "base_atk": 100, "em": 0, "crit_rate": 0, "crit_dmg": .5}})
    r["characters"].append({"id": cid, "element": "cryo", "energy_recharge": 1, "energy_capacity": 10, "initial_energy": 10, **EVIDENCE})


def condition(m, eid, t, order=1):
    m["condition_events"].append({"id": eid, "t": t, "order": order, "condition": eid, "value": True, "repeat": REPEAT, **EVIDENCE})


def test_self_snapshot_provider_current_and_enemy_current_are_separate_domains():
    t, r, m = fixture((.2, 1.2, 1.5))
    second_character(t, r)
    for desc in m["hit_events"]:
        desc["self_snapshot_event"] = "cast"
    condition(m, "change", 1)
    after = {"event": "condition", "phase": "pre", "event_ids": ["change"]}
    add(m,
        rule("self_change", "benefit.stat_add.atk", 100, trigger=after, beneficiaries={"mode": "characters", "characters": ["a"], "scope": "locked"}),
        rule("provider_change", "benefit.stat_add.atk", 200, trigger=after, beneficiaries={"mode": "characters", "characters": ["b"], "scope": "locked"}),
        rule("quill", "benefit.base_add.all", {"kind": "stat", "subject": "owner", "stat": "atk", "factor": .1, "sample": "current"}, domain="provider", owner="b"),
        rule("enemy", "benefit.res_shred.cryo", .1, domain="enemy", duration=1.5))
    out = run(t, r, m)
    assert damages(out) == pytest.approx([60, 70, 63])
    assert [x["source_value"] for x in out["sampling_trace"] if x["cycle"] == 0] == [200, 400, 400]
    assert out["sampling_trace"][1]["dependencies"][0]["layers"] == 1
    assert out["sampling_trace"][1]["projected_effect_id"] == "quill:value:0"


def test_actual_switch_recipient_and_internal_cooldown():
    t, r, m = fixture((.2, 1.2, 2.2))
    second_character(t, r)
    t["hits"][1]["character"] = "b"
    r["abilities"].extend([ability("ab", "a", "action.switch"), ability("be", "b"), ability("ba", "b", "action.switch")])
    r["actions"].extend([action("switch_b", "ab", 1, 1.01, switch_to="b"), action("b_cast", "be", 1.1, 1.11),
                         action("switch_a", "ba", 2, 2.01, switch_to="a")])
    m["hit_events"][1]["action_id"] = "b_cast"
    add(m, rule(trigger={"event": "switch", "phase": "post"},
                beneficiaries={"mode": "next_switch", "scope": "locked"},
                timing={"delay": 0, "duration": 5, "cooldown": 20, "cooldown_anchor": "success"}))
    out = run(t, r, m)
    assert damages(out) == pytest.approx([45, 180, 45])
    activations = [x for x in out["mechanics_trace"] if x["type"] == "activation"]
    assert activations[0]["beneficiaries"] == ["b"]
    assert any(x.get("reason") == "internal_cooldown" for x in out["mechanics_trace"])


def test_failed_burst_never_emits_buff_or_damage_and_does_not_double_spend():
    t, r, m = fixture()
    r["abilities"][0] = ability(kind="action.elemental_burst", energy={"required": 10, "amount": 10})
    r["characters"][0]["initial_energy"] = 0
    r["events"] = [{"id": "spend", "type": "energy_spend", "t": 0, "order": 1, "action_id": "cast", "character": "a", "required": 10, "amount": 10, "repeat": REPEAT, **EVIDENCE}]
    add(m, rule())
    out = run(t, r, m)
    assert out["status"] == "failed"
    assert out["mechanics_trace"] == []
    assert damages(out) == []
    r["characters"][0]["initial_energy"] = 10
    out = run(t, r, m)
    assert out["rotation_check"]["energy_check"]["failed_cycle"] == 1
    assert damages(out) == pytest.approx([90, 90, 90])


def test_generated_particles_use_same_frontline_and_er_clock_and_carry_across_boundary():
    t, r, m = fixture((.2,))
    r["characters"][0]["initial_energy"] = 0
    add(m, rule(resources=[{"id": "particles", "kind": "particles", "delay": 9, "travel_time": 2,
                            "count": 2, "particle_type": "particle", "element": "cryo", "source_part": "particles"}]))
    m["resource_ownership"] = [{"rule_id": "boost", "action_id": "cast", "source_part": "particles", "replaces_event_ids": []}]
    out = run(t, r, m)
    credits = [row for row in out["rotation_check"]["event_log"] if row["type"] == "energy_credit"]
    assert credits[0]["t"] == 11
    assert credits[0]["source_cycle"] == 0
    assert credits[0]["energy"][0]["gain"] == 6
    assert out["rotation_check"]["cycles"][0]["boundary_state"]["in_flight"]


def test_hit_patterns_expand_stable_ids_and_cast_only_once():
    t, r, m = fixture((.2,))
    m["hit_events"] = []
    m["hit_patterns"] = [{"id": "ticks", "prototype_hit_id": "h0", "action_id": "cast", "offsets": [.2, .4, .6], "orders": [1, 2, 3]}]
    out = run(t, r, m)
    assert [h["id"] for h in out["result"]["hits"]] == ["ticks:1", "ticks:2", "ticks:3"]
    assert sum(x["type"] == "action" and x["source_cycle"] == 0 for x in out["rotation_check"]["event_log"]) == 1


def test_dependency_and_duplicate_delivery_and_unknown_fields_rejected():
    _, _, m = fixture()
    add(m, rule("one", "benefit.stat_add.atk", {"kind": "stat", "subject": "owner", "stat": "atk", "factor": .1, "sample": "current"}))
    assert any(i["code"] == "unsupported_dependency" for i in validate_mechanics_model(m)["issues"])
    m["rules"][0]["effects"][0]["value"] = 1
    add(m, rule("two", source_id="one"))
    assert any(i["code"] == "duplicate_delivery" for i in validate_mechanics_model(m)["issues"])
    m["arbitrary_script"] = "x"
    assert any(i["code"] == "unknown_field" for i in validate_mechanics_model(m)["issues"])


def test_switch_diagnostic_reports_expected_actual_and_action():
    t, r, _ = fixture(())
    second_character(t, r)
    r["abilities"].append(ability("switch", "a", "action.switch"))
    r["actions"].append(action("go_b", "switch", 1, 1.1, switch_to="b"))
    t["switches"] = [{"t": 1.1, "character": "b"}]
    out = check_rotation(t, r)
    issue = next(i for i in out["issues"] if i["code"] == "switch_conflict")
    assert issue["differences"][0]["action_id"] == "go_b"
    assert issue["differences"][0]["expected"] == {"t": 1, "character": "b"}
    assert issue["differences"][0]["actual"] == {"t": 1.1, "character": "b"}


def test_action_end_trigger_and_end_snapshot_are_real_events():
    t, r, m = fixture((.2, .5))
    add(m, rule(trigger={"event": "action_end", "phase": "pre", "action_ids": ["cast"]}, duration=.1))
    for descriptor in m["hit_events"]:
        descriptor["self_snapshot_event"] = "cast:end"
    out = run(t, r, m)
    assert damages(out) == pytest.approx([90, 90])
    activation = next(x for x in out["mechanics_trace"] if x["type"] == "activation")
    assert activation["event_id"] == "cast:end"
    assert activation["t"] == .1


def test_cancelling_action_moves_actual_end_and_does_not_trigger_old_end_twice():
    t, r, m = fixture((.8, 1.2))
    second_character(t, r)
    r["actions"][0].update(occupation_end=1, switch_allowed_at=.5)
    r["abilities"].extend([ability("ab", "a", "action.switch"), ability("ba", "b", "action.switch")])
    r["actions"].extend([action("switch_b", "ab", .5, .6, switch_to="b", order=1), action("switch_a", "ba", 2, 2.1, switch_to="a", order=2)])
    add(m, rule(trigger={"event": "action_end", "phase": "pre", "action_ids": ["cast"]}, duration=.4))
    out = run(t, r, m)
    assert damages(out) == pytest.approx([90, 45])
    assert [x["t"] for x in out["mechanics_trace"] if x["type"] == "activation"] == [.5, 10.5, 20.5]


def test_each_beneficiary_has_own_pool_and_team_mode_shares_one():
    t, r, m = fixture((.2, .3, 1.2, 1.3))
    second_character(t, r)
    for h in t["hits"][2:]:
        h["character"] = "b"
    r["abilities"].extend([ability("ab", "a", "action.switch"), ability("be", "b"), ability("ba", "b", "action.switch")])
    r["actions"].extend([action("switch_b", "ab", 1, 1.01, switch_to="b"), action("b_cast", "be", 1.1, 1.11), action("switch_a", "ba", 2, 2.01, switch_to="a")])
    for d in m["hit_events"][2:]:
        d["action_id"] = "b_cast"
    add(m, rule(quota={"capacity": 1, "scope": "per_beneficiary", "refresh": "reset", "consume": {"event": "hit", "phase": "pre"}}))
    assert damages(run(t, r, m)) == pytest.approx([90, 45, 180, 90])
    m["rules"][0]["quota"]["scope"] = "team"
    assert damages(run(t, r, m)) == pytest.approx([90, 45, 90, 90])


def test_independent_layers_retain_their_own_trigger_snapshots():
    t, r, m = fixture((.2, .6, 1.2))
    condition(m, "new_panel", .3)
    condition(m, "new_layer", .4)
    add(m, rule("panel", "benefit.stat_add.atk", 100, trigger={"event": "condition", "phase": "pre", "event_ids": ["new_panel"]}),
        rule("layers", "benefit.base_add.all", {"kind": "stat", "subject": "owner", "stat": "atk", "factor": 1, "sample": "trigger"},
             trigger={"event": "condition", "phase": "pre"}, domain="provider", duration=.6,
             stacking={"max": 2, "add": 1, "policy": "independent"}))
    # First layer snapshots the same pre-state as the panel change: 100; second 200.
    out = run(t, r, m)
    assert damages(out) == pytest.approx([45, 225, 90])
    samples = [x for x in out["sampling_trace"] if x["cycle"] == 0]
    assert [x["source_value"] for x in samples] == [100, 200]
    assert [x["projected_effect_id"] for x in samples] == ["layers:value:0:layer0", "layers:value:0:layer1"]


def test_unknown_condition_never_triggers_benefit_but_returns_pending():
    t, r, m = fixture((.2,))
    condition(m, "unknown", .1, 2)
    m["condition_events"][0]["value"] = None
    add(m, rule(trigger={"event": "condition", "phase": "pre", "event_ids": ["unknown"]}))
    out = run(t, r, m)
    assert out["status"] == "pending"
    assert damages(out) == [45]
    assert not any(x["type"] == "activation" for x in out["mechanics_trace"])


def test_generated_direct_energy_arrives_after_atomic_burst_spend_and_enables_next_cycle():
    t, r, m = fixture((.2,))
    r["abilities"][0] = ability(kind="action.elemental_burst", energy={"required": 10, "amount": 10})
    r["events"] = [{"id": "spend", "type": "energy_spend", "t": 0, "order": 1, "action_id": "cast", "character": "a", "required": 10, "amount": 10, "repeat": REPEAT, **EVIDENCE}]
    add(m, rule(resources=[{"id": "refund", "kind": "energy_direct", "delay": 0, "amounts": {"a": 10}, "source_part": "refund"}]))
    m["resource_ownership"] = [{"rule_id": "boost", "action_id": "cast", "source_part": "refund", "replaces_event_ids": []}]
    out = run(t, r, m)
    assert out["status"] == "passed"
    credit = next(x for x in out["rotation_check"]["event_log"] if x["type"] == "energy_direct")
    assert credit["energy"][0]["before"] == 0
    assert credit["energy"][0]["gain"] == 10


def test_resource_ownership_rejects_duplicate_and_replaces_old_spawn_and_credit():
    t, r, m = fixture((.2,))
    r["characters"][0]["initial_energy"] = 0
    r["abilities"][0]["rules"] = [{"id": "particles", "kind": "energy_spawn"}]
    r["actions"][0]["rule_outcomes"] = [{"rule_id": "particles", "status": "triggered", "event_ids": ["old_spawn"]}]
    r["events"] = [
        {"id": "old_spawn", "type": "energy_spawn", "t": .3, "order": 1, "action_id": "cast", "count": 2, "element": "cryo", "particle_type": "particle", "source_kind": "skill", "repeat": REPEAT, **EVIDENCE},
        {"id": "old_credit", "type": "energy_credit", "credit_at": .4, "order": 1, "spawn_id": "old_spawn", "count": 2, "repeat": REPEAT, **EVIDENCE}]
    add(m, rule(resources=[{"id": "particles", "kind": "particles", "delay": .3, "travel_time": .1, "count": 2, "element": "cryo", "particle_type": "particle", "source_part": "particles"}]))
    m["resource_ownership"] = [{"rule_id": "boost", "action_id": "cast", "source_part": "particles", "replaces_event_ids": []}]
    out = execute_mechanics(t, r, m)
    assert out["status"] == "invalid"
    assert any(i["code"] == "duplicate_delivery" for i in out["issues"])
    m["resource_ownership"][0]["replaces_event_ids"] = ["old_spawn"]
    out = run(t, r, m)
    events = out["rotation_check"]["event_log"]
    assert not any(x["id"] in ("old_spawn", "old_credit") for x in events)
    assert next(x for x in events if x["type"] == "energy_credit")["energy"][0]["gain"] == 6


def test_initial_quota_and_cooldown_must_be_explicit_and_survive_boundaries():
    t, r, m = fixture((.2,))
    add(m, rule(quota={"capacity": 2, "scope": "team", "refresh": "keep", "consume": {"event": "hit", "phase": "pre"}}, duration=100))
    m["initial_state"]["rules"][0].update(cooldown_remaining=100, instances=[{"beneficiaries": ["a"], "remaining": [100], "quota": {"team": 2}}])
    out = run(t, r, m)
    assert out["status"] == "unproven"
    assert [x["boundary_state"]["mechanics"]["rules"]["boost"]["instances"][0]["quota"]["team"] for x in out["rotation_check"]["cycles"]] == [1, 0, 0]
    del m["initial_state"]["rules"][0]["instances"][0]["quota"]
    assert validate_mechanics_model(m)["status"] == "invalid"


def test_event_before_its_cast_and_unrelated_end_order_collision_are_rejected():
    t, r, m = fixture((0,))
    m["hit_events"][0]["order"] = -1
    assert any(i["code"] == "causality" for i in execute_mechanics(t, r, m)["issues"])
    t["hits"][0]["t"] = .1
    m["hit_events"][0]["order"] = 0
    assert any(i["code"] == "ambiguous_order" for i in execute_mechanics(t, r, m)["issues"])


def test_manual_sources_and_mechanics_cannot_double_deliver_one_part():
    t, r, m = fixture((.2,))
    t["buffs"] = [{"id": "manual", "start": 0, "end": 1, "scope": "team", "extensions": {"owner": "a", "source_id": "boost"},
                   "effects": [{"id": "flat", "benefit_type_id": "benefit.stat_add.atk", "zone": "stat_add", "stat": "atk", "value": 100, "applies_to": {"mode": "all"}}]}]
    add(m, rule())
    assert any(i["code"] == "duplicate_delivery" for i in execute_mechanics(t, r, m)["issues"])


def test_missing_action_time_is_pending_not_zero_or_parse_failure():
    t, r, m = fixture((.2,))
    r["actions"][0]["occupation_end"] = None
    out = execute_mechanics(t, r, m)
    assert out["status"] == "pending"
    assert out["mechanics_trace"] == []


def test_expiry_trace_uses_actual_expiry_not_next_processing_time():
    t, r, m = fixture((.2, .4))
    add(m, rule(trigger={"event": "hit", "phase": "pre"}, duration=1,
                stacking={"max": 2, "add": 1, "policy": "independent"}))
    out = run(t, r, m)
    expired = [x for x in out["mechanics_trace"] if x["type"] == "expiry" and x["t"] < 10]
    assert [(x["t"], x["checked_at"], x["layers_before"], x["layers_after"]) for x in expired] == [(1.2, 10, 2, 1), (1.4, 10, 1, 0)]


def test_condition_not_yet_observed_is_unknown_not_implicitly_false():
    t, r, m = fixture((.2,))
    condition(m, "observed_later", 1)
    add(m, rule(trigger={"event": "action_start", "phase": "pre", "condition": "observed_later"}))
    out = run(t, r, m)
    assert out["status"] == "pending"
    assert damages(out) == [45]
    assert any(i["code"] == "unknown_condition" and i.get("event_id") == "cast" for i in out["issues"])
