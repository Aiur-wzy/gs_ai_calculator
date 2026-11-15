"""Independent D20 boundary review; engineering cases, not model benchmark data."""
from __future__ import annotations

from test_mechanics_execution import (
    EVIDENCE,
    ability,
    action,
    add,
    fixture,
    rule,
    second_character,
)
from test_rotation_energy import (
    _cooldown_cancel_case,
    _cross_cycle_dynamic_cancel_case,
    _duplicate_ready_restore_case,
    _row,
    _switch_shared_cooldown_case,
)

from genshin_engine.mechanics import execute_mechanics
from genshin_engine.rotation import check_rotation


def test_event_sampled_delayed_hit_is_part_of_periodic_boundary_state():
    template, rotation, model = fixture((25.2,))
    rotation["max_cycles"] = 8
    model["manual_buff_repeat"] = "once"
    template["buffs"] = [{"id": "opening-panel", "start": 0, "end": 35, "scope": "team", "effects": [
        {"id": "attack", "benefit_type_id": "benefit.stat_add.atk", "zone": "stat_add", "stat": "atk", "value": 100,
         "applies_to": {"mode": "all"}}]}]
    add(model, rule("event-sample", "benefit.base_add.all", {
        "kind": "stat", "subject": "owner", "stat": "atk", "factor": .1,
        "sample": "event", "event_id": "cast"}, domain="provider", duration=100))
    out = execute_mechanics(template, rotation, model)
    assert out["status"] == "passed", out["issues"]
    # At t=50 the t=30 cast still has an old-panel event queued for t=55.2.
    # That boundary cannot equal the t=60 state, whose pending samples are all 100.
    assert out["rotation_check"]["energy_check"]["proven_cycle"] >= 6


def test_nonpositive_effective_er_never_produces_negative_particle_energy():
    template, rotation, model = fixture((.2,))
    rotation["characters"][0]["initial_energy"] = 0
    rotation["events"] = [
        {"id": "drop", "type": "energy_spawn", "t": .3, "order": 20, "action_id": "cast", "count": 1,
         "element": "cryo", "particle_type": "particle", "source_kind": "skill", "repeat": {"mode": "each_cycle"}, **EVIDENCE},
        {"id": "credit", "type": "energy_credit", "credit_at": .4, "order": 21, "spawn_id": "drop", "count": 1,
         "repeat": {"mode": "each_cycle"}, **EVIDENCE}]
    add(model, rule("negative-er", "benefit.energy_recharge.all", -2))
    out = execute_mechanics(template, rotation, model)
    assert out["status"] in ("invalid", "failed", "pending"), out
    assert not any(credit.get("gain", 0) < 0 for row in out["rotation_check"].get("event_log", []) for credit in row.get("energy", []))


def test_cancelled_occupation_end_is_the_cooldown_anchor():
    template, rotation, model = fixture((.2,))
    template["loop_duration"] = rotation["period"] = 12
    second_character(template, rotation)
    rotation["abilities"][0]["cooldown"] = {"group": "skill", "duration": 5, "start": "occupation_end", "charges": 1, "recovery": "parallel"}
    rotation["initial_cooldowns"] = [{"character": "a", "group": "skill", "ready_in": []}]
    rotation["actions"][0].update(occupation_end=1, switch_allowed_at=.5)
    rotation["abilities"].extend([ability("ab", "a", "action.switch"), ability("ba", "b", "action.switch")])
    rotation["actions"].extend([action("to_b", "ab", .5, .6, order=1, switch_to="b"),
                               action("to_a", "ba", 1, 1.1, order=2, switch_to="a"),
                               action("next_cast", "e", 5.6, 5.7, order=3)])
    out = execute_mechanics(template, rotation, model)
    next_cast = next(row for row in out["rotation_check"]["event_log"] if row["id"] == "next_cast")
    assert next_cast["status"] == "passed", out["issues"]


def test_plain_rotation_and_mechanics_share_cancelled_cooldown_state():
    template, rotation, model = fixture((.2,))
    template["loop_duration"] = rotation["period"] = 12
    second_character(template, rotation)
    rotation["abilities"][0]["cooldown"] = {
        "group": "skill", "duration": 5, "start": "occupation_end",
        "charges": 1, "recovery": "parallel",
    }
    rotation["initial_cooldowns"] = [{"character": "a", "group": "skill", "ready_in": []}]
    rotation["actions"][0].update(occupation_end=1, switch_allowed_at=.5)
    rotation["abilities"].extend([
        ability("ab", "a", "action.switch"),
        ability("ba", "b", "action.switch"),
    ])
    rotation["actions"].extend([
        action("to_b", "ab", .5, .6, order=1, switch_to="b"),
        action("to_a", "ba", 1, 1.1, order=2, switch_to="a"),
        action("next_cast", "e", 5.6, 5.7, order=3),
    ])
    rotation["action_hit_links"] = [{"action_id": "cast", "hit_id": "h0"}]
    template["switches"] = [
        {"t": 0, "character": "a"},
        {"t": .5, "character": "b"},
        {"t": 1, "character": "a"},
    ]

    plain = check_rotation(template, rotation)
    mechanics = execute_mechanics(template, rotation, model)["rotation_check"]
    for result in (plain, mechanics):
        next_cast = next(row for row in result["event_log"] if row["id"] == "next_cast")
        assert next_cast["status"] == "passed", result["issues"]
        switch = next(row for row in result["event_log"] if row["id"] == "to_b")
        assert switch["cancelled_cooldown"]["before"] == 6
        assert switch["cancelled_cooldown"]["after"] == 5.5


def test_mechanics_early_switch_rejection_does_not_cancel_or_move_cooldown():
    link_template, rotation = _cooldown_cancel_case(switch_at=.49, next_at=5.6)
    template, _, model = fixture((.2,))
    template["characters"].append({
        "id": "b", "level": 90,
        "stats": {"atk": 100, "base_atk": 50, "em": 0, "crit_rate": 0, "crit_dmg": .5},
    })
    template["switches"] = link_template["switches"]
    template["loop_duration"] = link_template["loop_duration"]
    rotation["action_hit_links"] = [{"action_id": "cast", "hit_id": "h0"}]

    result = execute_mechanics(template, rotation, model)["rotation_check"]
    switch = _row(result, "switch-b")
    assert switch["status"] == "failed"
    assert "cancelled_cooldown" not in switch
    assert _row(result, "cast")["next_ready_at"] == 6
    assert _row(result, "next-cast")["status"] == "not_verified"


def test_plain_rotation_and_mechanics_share_cross_cycle_dynamic_cancel_anchor():
    link_template, rotation = _cross_cycle_dynamic_cancel_case()
    template, _, model = fixture((1.6,))
    template["characters"].append({
        "id": "b", "level": 90,
        "stats": {"atk": 100, "base_atk": 50, "em": 0, "crit_rate": 0, "crit_dmg": .5},
    })
    template["switches"] = link_template["switches"]
    template["loop_duration"] = link_template["loop_duration"]

    plain = check_rotation(link_template, rotation)
    mechanics = execute_mechanics(template, rotation, model)["rotation_check"]
    expected_anchor = {
        "group": '["a","skill"]',
        "ready_in": 7_000_000,
        "end_in": 500_000,
        "prior_tail_in": 2_000_000,
    }
    assert "cooldown_anchor" not in plain["cycles"][0]["boundary_state"]
    assert mechanics["cycles"][0]["boundary_state"]["mechanics_cooldown_anchor"] == expected_anchor
    for result in (plain, mechanics):
        assert _row(result, "reduce", 1)["ready_at"] == [5]
        assert _row(result, "switch-b", 1)["cancelled_cooldown"] == {
            "group": '["a","skill"]', "before": 5, "after": 4.7,
        }
        assert _row(result, "next-cast", 2)["status"] == "passed"


def test_plain_shape_stays_implicit_while_mechanics_keeps_legacy_null_anchor():
    template, rotation, model = fixture((.2,))
    result = execute_mechanics(template, rotation, model)["rotation_check"]
    assert result["cycles"]
    assert all("cooldown_anchor" not in cycle["boundary_state"] for cycle in result["cycles"])
    assert all(cycle["boundary_state"]["mechanics_cooldown_anchor"] is None
               for cycle in result["cycles"])


def test_plain_rotation_and_mechanics_share_duplicate_ready_entry_identity():
    link_template, rotation = _duplicate_ready_restore_case()
    template, _, model = fixture((.2,))
    template["characters"].append({
        "id": "b", "level": 90,
        "stats": {"atk": 100, "base_atk": 50, "em": 0, "crit_rate": 0, "crit_dmg": .5},
    })
    template["switches"] = link_template["switches"]
    template["loop_duration"] = link_template["loop_duration"]

    plain = check_rotation(link_template, rotation)
    mechanics = execute_mechanics(template, rotation, model)["rotation_check"]
    for result in (plain, mechanics):
        assert _row(result, "restore")["ready_at"] == [6]
        assert _row(result, "switch-b")["cancelled_cooldown"] == {
            "group": '["a","skill"]', "before": 6, "after": 5.5,
        }
        assert _row(result, "consume")["status"] == "passed"
        assert _row(result, "next-cast")["status"] == "passed"


def test_mechanics_same_ready_entries_keep_current_switch_anchor_by_identity():
    link_template, rotation = _switch_shared_cooldown_case(
        recovery="parallel", initial_ready_in=[6],
    )
    template, _, model = fixture((.2,))
    template["characters"].append({
        "id": "b", "level": 90,
        "stats": {"atk": 100, "base_atk": 50, "em": 0, "crit_rate": 0, "crit_dmg": .5},
    })
    template["switches"] = link_template["switches"]
    template["loop_duration"] = link_template["loop_duration"]
    rotation["action_hit_links"] = [{"action_id": "cast", "hit_id": "h0"}]

    result = execute_mechanics(template, rotation, model)["rotation_check"]
    switch = _row(result, "switch-b")
    assert switch["cancelled_cooldown"] == {
        "group": '["a","skill"]', "before": 6, "after": 5.5,
    }
    assert switch["next_ready_at"] == 6
    assert result["cycles"][0]["boundary_state"]["mechanics_cooldown_anchor"] == {
        "group": '["a","skill"]', "ready_in": 5_250_000,
        "end_in": 250_000, "prior_tail_in": 0,
    }


def test_initial_instance_cannot_reference_a_nonexistent_beneficiary():
    template, rotation, model = fixture((.2,))
    add(model, rule())
    model["initial_state"]["rules"][0]["instances"] = [{"beneficiaries": ["ghost"], "remaining": [1]}]
    out = execute_mechanics(template, rotation, model)
    assert out["status"] == "invalid"
    assert any(issue["code"] in ("reference", "initial_state") for issue in out["issues"])
