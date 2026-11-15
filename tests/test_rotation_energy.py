"""D16 的数值/时序独立预期值回归，不拿旧伤害表当能量真值。"""

from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from genshin_engine.rotation import (
    check_rotation,
    rotation_capabilities,
    validate_rotation_model,
)


def _evidence():
    return {"status": "assumed", "source": "独立数值情景，用于验证事件模型"}


def _repeat(mode="each_cycle", **kw):
    return {"mode": mode, **kw}


def _ability(aid, kind, *, character="a", cd=None, energy=None, rules=None):
    return {"id": aid, "character": character, "action_type_id": kind,
            "cooldown": cd, "energy": energy, "rules": rules or [], **_evidence()}


def _action(aid, ability, start, end, *, order=0, **kw):
    return {"id": aid, "ability_id": ability, "start": start, "occupation_end": end,
            "order": order, "repeat": _repeat(), **_evidence(), **kw}


def _event(eid, kind, t, *, order=0, **kw):
    return {"id": eid, "type": kind, "credit_at" if kind == "energy_credit" else "t": t,
            "order": order, "repeat": _repeat(), **_evidence(), **kw}


def _model():
    return {"schema_version": 1, "period": 10, "max_cycles": 3,
            "initial_frontline": "a",
            "characters": [{"id": "a", "element": "cryo", "energy_recharge": 1,
                            "energy_capacity": 10, "initial_energy": 10, **_evidence()}],
            "abilities": [_ability("e", "action.elemental_skill", cd={
                "group": "e", "duration": 10, "start": "action_start", "charges": 1,
                "recovery": "sequential"}, rules=[{"id": "particles", "kind": "energy_spawn"}]),
                _ability("q", "action.elemental_burst", energy={"required": 10, "amount": 10})],
            "actions": [_action("e1", "e", 0, 0.5, rule_outcomes=[{
                "rule_id": "particles", "status": "triggered", "event_ids": ["spawn"]}]),
                        _action("q1", "q", 1, 1.5)],
            "events": [_event("spawn", "energy_spawn", .3, action_id="e1", particle_type="particle",
                              element="cryo", count=4, source_kind="skill"),
                       _event("spend", "energy_spend", 1, order=1, action_id="q1", character="a",
                              required=10, amount=10),
                       _event("credit", "energy_credit", 1.2, spawn_id="spawn", count=4)],
            "initial_cooldowns": [{"character": "a", "group": "e", "ready_in": []}],
            "assumptions": ["忽略命中与距离；这里只检验能量账本"]}


def _template(model):
    return {"version": 2, "characters": [{"id": c["id"]} for c in model["characters"]],
            "hits": [], "switches": []}


def _check(model):
    return check_rotation(_template(model), model)


def _row(result, eid, cycle=0):
    return next(row for row in result["event_log"] if row["id"] == eid and row["source_cycle"] == cycle)


def _cooldown_cancel_case(*, cooldown_start="occupation_end", recovery="parallel",
                          switch_at=.5, initial_ready_in=(), charges=None, next_at=5.6):
    """A character-neutral cancellation fixture for the core rotation ledger."""
    repeat = _repeat("once")
    if charges is None:
        charges = len(initial_ready_in) + 1
    model = {
        "schema_version": 1,
        "period": 12,
        "max_cycles": 3,
        "initial_frontline": "a",
        "characters": [
            {"id": "a", "element": "cryo", "energy_recharge": 1,
             "energy_capacity": 0, "initial_energy": 0, **_evidence()},
            {"id": "b", "element": "anemo", "energy_recharge": 1,
             "energy_capacity": 0, "initial_energy": 0, **_evidence()},
        ],
        "abilities": [
            _ability("skill", "action.elemental_skill", cd={
                "group": "skill", "duration": 5, "start": cooldown_start,
                "charges": charges, "recovery": recovery}),
            _ability("to-b", "action.switch"),
            _ability("to-a", "action.switch", character="b"),
        ],
        "actions": [
            {**_action("cast", "skill", 0, 1), "switch_allowed_at": .5, "repeat": repeat},
            {**_action("switch-b", "to-b", switch_at, round(switch_at + .1, 6), order=1,
                       switch_to="b"), "repeat": repeat},
            {**_action("switch-a", "to-a", 1.1, 1.2, order=2, switch_to="a"),
             "repeat": repeat},
            {**_action("next-cast", "skill", next_at, round(next_at + .1, 6), order=3),
             "repeat": repeat},
        ],
        "events": [],
        "initial_cooldowns": [{"character": "a", "group": "skill",
                              "ready_in": list(initial_ready_in)}],
    }
    template = {
        "version": 2,
        "characters": [{"id": "a"}, {"id": "b"}],
        "hits": [],
        "switches": [
            {"t": switch_at, "character": "b"},
            {"t": 1.1, "character": "a"},
        ],
        "loop_duration": 12,
    }
    return template, model


def _duplicate_ready_restore_case(*, recovery="parallel", duration=5, include_followups=True):
    template, model = _cooldown_cancel_case(
        recovery=recovery, initial_ready_in=[6], charges=2, next_at=5.6,
    )
    model["abilities"][0]["cooldown"]["duration"] = duration
    model["events"] = [{**_event(
        "restore", "cooldown_change", .25,
        character="a", group="skill", operation="restore", amount=1,
    ), "repeat": _repeat("once")}]
    if include_followups:
        model["actions"][3]["order"] = 4
        model["actions"].insert(3, {
            **_action("consume", "skill", 1.3, 1.4, order=3),
            "repeat": _repeat("once"),
        })
    else:
        model["actions"] = model["actions"][:3]
    return template, model


def _switch_shared_cooldown_case(*, recovery, initial_ready_in=()):
    """A cancelling switch also consumes the cancelled action's cooldown pool."""
    charges = len(initial_ready_in) + 2
    template, model = _cooldown_cancel_case(
        recovery=recovery, initial_ready_in=initial_ready_in, charges=charges,
    )
    shared = copy.deepcopy(model["abilities"][0]["cooldown"])
    model["abilities"][1]["cooldown"] = shared
    model["actions"] = model["actions"][:2]
    model["actions"][1]["occupation_end"] = 1
    model["period"] = .75
    model["max_cycles"] = 3
    template["switches"] = template["switches"][:1]
    template["loop_duration"] = .75
    return template, model


def test_old_damage_template_has_no_implicit_energy_certificate():
    result = check_rotation({"hits": [{"skill": "EQ"}]})
    assert result["status"] == "not_checked"
    assert result["energy_check"]["status"] == "not_checked"


def test_periodic_energy_is_proven_only_after_three_cycles_and_no_mutation():
    model = _model()
    original = copy.deepcopy(model)
    result = _check(model)
    assert result["status"] == "passed"
    assert result["energy_check"]["checked_cycles"] == 3
    assert result["energy_check"]["proven_cycle"] == 2
    assert _row(result, "credit")["energy"][0]["overflow"] == 2
    assert result["determinacy"] == "conditional"
    assert "体力消耗" in result["unchecked_constraints"]
    assert model == original


def test_structure_can_save_pending_data_without_ready_damage_template():
    model = _model()
    del model["characters"][0]["energy_recharge"]
    model["actions"][0]["occupation_end"] = None
    assert validate_rotation_model(model)["status"] == "valid"
    result = _check(model)
    assert result["status"] == "pending"
    assert result["event_log"] == []


@pytest.mark.parametrize("mutate", [
    lambda m: m.update(extensions={"applications": []}),
    lambda m: m["events"][2].update(t=1.2),
    lambda m: m["events"][0].update(count=1.5),
    lambda m: m.update(max_cycles=2),
    lambda m: m["events"][0].update(count=True),
    lambda m: m["characters"][0].update(energy_recharge=float("nan")),
    lambda m: m["actions"][0].update(repeat={"mode": "unknown"}),
])
def test_unsupported_or_non_json_semantics_are_rejected(mutate):
    model = _model()
    mutate(model)
    assert _check(model)["status"] == "invalid"


@pytest.mark.parametrize("mutate", [
    lambda m: m["characters"][0].update(initial_energy=None),
    lambda m: m["characters"][0].update(element=None),
    lambda m: m["events"][0].update(count=None),
    lambda m: m["events"][0].update(status="pending"),
    lambda m: m["abilities"][0].pop("cooldown"),
    lambda m: m.pop("initial_cooldowns"),
    lambda m: m["actions"][0].pop("source"),
    lambda m: m["actions"][0].pop("rule_outcomes"),
    lambda m: m["events"].pop(1),
    lambda m: m.pop("initial_frontline"),
])
def test_missing_is_never_zero_or_an_implicit_pass(mutate):
    model = _model()
    mutate(model)
    assert _check(model)["status"] == "pending"


def test_missing_or_duplicate_spend_cannot_hide_burst_cost():
    model = _model()
    model["events"].append({**model["events"][1], "id": "spend2", "order": 2})
    result = _check(model)
    assert result["status"] == "invalid"
    assert any(x["code"] == "duplicate_spend" for x in result["issues"])


def test_rules_require_correct_typed_generated_events_or_reasoned_nontrigger():
    model = _model()
    model["actions"][0]["rule_outcomes"][0]["event_ids"] = ["spend"]
    assert _check(model)["status"] == "invalid"
    model = _model()
    model["actions"][0]["rule_outcomes"] = [{"rule_id": "particles", "status": "not_triggered", "reason": "本情景未命中"}]
    model["events"] = [model["events"][1]]
    assert _check(model)["status"] == "failed"  # 第二轮缺能，而非把不触发当产球。


def test_order_is_explicit_not_array_or_id_order():
    model = _model()
    model["events"][2]["credit_at"] = 1
    model["events"][2]["order"] = 2
    after = _check(model)
    model["events"].reverse()
    assert _check(model) == after
    model["events"][0]["order"] = 1
    assert _check(model)["status"] == "invalid"


def test_microsecond_precision_is_not_silently_rounded():
    model = _model()
    model["events"][2]["credit_at"] = 1.2000001
    result = _check(model)
    assert any(x["code"] == "time_precision" for x in result["issues"])


def test_early_credit_overflows_and_initial_full_energy_does_not_save_later_cycle():
    model = _model()
    model["events"][2]["credit_at"] = 0.4
    result = _check(model)
    assert result["status"] == "passed"  # 下一轮先到账再 Q，仍可行。
    assert _row(result, "credit")["energy"][0]["overflow"] == 12
    model["events"][0]["count"] = model["events"][2]["count"] = 1
    result = _check(model)
    assert result["status"] == "failed"
    assert _row(result, "spend", 1)["deficit"] == 7
    assert result["energy_check"]["opening_status"] == "passed"


def test_first_prefix_can_fail_even_when_later_cycle_total_would_be_enough():
    model = _model()
    model["characters"][0]["initial_energy"] = 0
    result = _check(model)
    assert result["status"] == "failed"
    assert _row(result, "spend")["after"] == 0
    assert _row(result, "credit")["status"] == "not_verified"
    assert not any(row.get("after", 0) < 0 for row in result["event_log"])


def test_threshold_and_amount_are_independent_and_no_negative_balance():
    model = _model()
    model["abilities"][1]["energy"]["amount"] = model["events"][1]["amount"] = 0
    assert _row(_check(model), "spend")["after"] == 10
    model["characters"][0]["initial_energy"] = 5
    assert _check(model)["status"] == "failed"


@pytest.mark.parametrize(("particle_type", "element", "base"), [
    ("particle", "cryo", 3), ("particle", "pyro", 1), ("particle", "clear", 2),
    ("orb", "cryo", 9), ("orb", "pyro", 3), ("orb", "clear", 6),
])
def test_base_particle_orb_values(particle_type, element, base):
    model = _model()
    model["characters"][0]["energy_capacity"] = 100
    model["events"][0].update(particle_type=particle_type, element=element, count=1)
    model["events"][2]["count"] = 1
    row = _row(_check(model), "credit")
    assert row["energy"][0]["raw"] == base


@pytest.mark.parametrize(("party_size", "discount"), [(2, .8), (3, .7), (4, .6)])
def test_party_size_discount_and_each_characters_own_er(party_size, discount):
    model = _model()
    for index in range(1, party_size):
        model["characters"].append({"id": str(index), "element": "pyro", "energy_recharge": 2,
                                    "energy_capacity": 100, "initial_energy": 0, **_evidence()})
    credit = _row(_check(model), "credit")
    assert credit["energy"][1]["raw"] == pytest.approx(4 * 1 * discount * 2)
    assert credit["energy"][0]["raw"] == 12


def test_energy_samples_frontline_at_credit_not_spawn_and_flat_energy_er_scaled_once():
    model = _model()
    model["characters"][0]["energy_recharge"] = 2
    model["characters"].append({"id": "b", "element": "hydro", "energy_recharge": 1,
                                "energy_capacity": 100, "initial_energy": 0, **_evidence()})
    model["abilities"].extend([_ability("s", "action.switch"), _ability("sb", "action.switch", character="b")])
    model["actions"].extend([_action("switch", "s", 1.1, 1.15, switch_to="b"),
                              _action("back", "sb", 2, 2.1, switch_to="a")])
    model["actions"][1]["switch_allowed_at"] = 1.1
    template = _template(model)
    template["switches"] = [{"t": 1.1, "character": "b"}, {"t": 2, "character": "a"}]
    model["events"].append(_event("flat", "energy_direct", 1.3, amounts={"b": 6}))
    result = check_rotation(template, model)
    credit = _row(result, "credit")
    assert credit["frontline"] == "b"
    assert credit["energy"][0]["raw"] == pytest.approx(4 * 3 * .8 * 2)
    assert credit["energy"][1]["raw"] == 4
    flat = _row(result, "flat")["energy"][0]
    assert flat['base_amount'] == 6
    assert flat['energy_recharge'] == 1
    assert flat['raw'] == flat['theoretical_amount'] == flat['gain'] == 6
    assert flat['overflow'] == 0
    assert flat['settlement_contract'] == 'energy-direct-base-er-v1'

    # Preserve the original input above; a second receiver configuration makes
    # one ER multiplier and capacity overflow independently observable.
    scaled_model = copy.deepcopy(model)
    scaled_model['characters'][1].update(energy_recharge=2, energy_capacity=10)
    scaled = _row(check_rotation(template, scaled_model), 'flat')['energy'][0]
    assert scaled['before'] == 8  # Earlier hydro credit: four base energy × ER2.
    assert scaled['base_amount'] == 6 and scaled['energy_recharge'] == 2
    assert scaled['raw'] == scaled['theoretical_amount'] == 12
    assert scaled['gain'] == 2 and scaled['overflow'] == 10 and scaled['after'] == 10


def test_temporary_er_is_sampled_at_credit_and_interval_is_half_open():
    model = _model()
    model["energy_recharge_windows"] = [{"id": "er", "character": "a", "start": 0,
        "end": 1.2, "delta": 1, "repeat": _repeat(), **_evidence()}]
    assert _row(_check(model), "credit")["energy"][0]["raw"] == 12
    model["energy_recharge_windows"][0]["end"] = 1.3
    assert _row(_check(model), "credit")["energy"][0]["raw"] == 24


def test_two_actions_cannot_overlap_and_late_cancel_is_enforced():
    model = _model()
    model["actions"][0]["occupation_end"] = 1.1
    result = _check(model)
    assert result["status"] == "failed"
    assert any(x["code"] == "action_overlap" for x in result["issues"])


def test_shared_variant_cd_and_early_use_fail():
    model = _model()
    model["abilities"].append({**model["abilities"][0], "id": "hold", "variant": "hold", "rules": []})
    model["actions"].append(_action("e2", "hold", 5, 5.5))
    result = _check(model)
    assert result["status"] == "failed"
    assert _row(result, "e2")["next_ready_at"] == 10


def test_cd_begins_at_declared_end_not_always_at_start():
    model = _model()
    model["abilities"][0]["cooldown"]["start"] = "occupation_end"
    result = _check(model)
    assert result["status"] == "failed"
    assert _row(result, "e1", 1)["next_ready_at"] == 10.5


@pytest.mark.parametrize("recovery", ["parallel", "sequential"])
def test_plain_rotation_cancel_at_allowed_boundary_moves_occupation_end_cooldown(recovery):
    template, model = _cooldown_cancel_case(recovery=recovery)
    result = check_rotation(template, model)
    cancelled = _row(result, "switch-b")["cancelled_cooldown"]
    assert cancelled == {"group": json.dumps(["a", "skill"], separators=(",", ":")),
                         "before": 6, "after": 5.5}
    assert _row(result, "next-cast")["status"] == "passed"


def test_plain_rotation_natural_end_does_not_move_occupation_end_cooldown():
    template, model = _cooldown_cancel_case(switch_at=1, next_at=5.6)
    # The return switch must follow the first switch's own occupation.
    model["actions"][2].update(start=1.2, occupation_end=1.3)
    template["switches"][1]["t"] = 1.2
    result = check_rotation(template, model)
    assert "cancelled_cooldown" not in _row(result, "switch-b")
    next_cast = _row(result, "next-cast")
    assert next_cast["status"] == "failed"
    assert next_cast["next_ready_at"] == 6
    assert all("cooldown_anchor" not in cycle["boundary_state"] for cycle in result["cycles"])


def test_plain_rotation_switch_before_allowed_boundary_is_rejected_without_moving_cooldown():
    template, model = _cooldown_cancel_case(switch_at=.49, next_at=5.6)
    result = check_rotation(template, model)
    switch = _row(result, "switch-b")
    assert switch["status"] == "failed"
    assert any(issue["code"] == "action_overlap" and issue["path"] == "switch-b"
               for issue in result["issues"])
    assert "cancelled_cooldown" not in switch
    assert _row(result, "cast")["next_ready_at"] == 6
    assert _row(result, "next-cast")["status"] == "not_verified"


@pytest.mark.parametrize(("initial_ready_in", "expected_before", "expected_after"), [
    ([.8], 6, 5.8),
    ([4], 9, 9),
])
def test_plain_rotation_sequential_cancel_preserves_existing_recovery_tail(
        initial_ready_in, expected_before, expected_after):
    template, model = _cooldown_cancel_case(
        recovery="sequential", initial_ready_in=initial_ready_in,
        charges=2, next_at=9.1,
    )
    result = check_rotation(template, model)
    cancelled = _row(result, "switch-b")["cancelled_cooldown"]
    assert cancelled["before"] == expected_before
    assert cancelled["after"] == expected_after


@pytest.mark.parametrize("recovery", ["parallel", "sequential"])
def test_plain_rotation_action_start_cooldown_is_unchanged_by_cancel(recovery):
    template, model = _cooldown_cancel_case(
        cooldown_start="action_start", recovery=recovery, next_at=4.9,
    )
    result = check_rotation(template, model)
    assert "cancelled_cooldown" not in _row(result, "switch-b")
    next_cast = _row(result, "next-cast")
    assert next_cast["status"] == "failed"
    assert next_cast["next_ready_at"] == 5


def test_plain_rotation_dynamic_reduction_keeps_cancel_anchor_in_sync():
    template, model = _cooldown_cancel_case(next_at=4.6)
    model["events"] = [{**_event(
        "reduce", "cooldown_change", .25,
        character="a", group="skill", operation="reduce", amount=1,
    ), "repeat": _repeat("once")}]
    result = check_rotation(template, model)
    assert _row(result, "reduce")["ready_at"] == [5]
    assert _row(result, "switch-b")["cancelled_cooldown"] == {
        "group": json.dumps(["a", "skill"], separators=(",", ":")),
        "before": 5,
        "after": 4.5,
    }
    assert _row(result, "next-cast")["status"] == "passed"


def test_plain_rotation_sequential_dynamic_reduction_moves_ready_and_prior_tail_together():
    template, model = _cooldown_cancel_case(
        recovery="sequential", initial_ready_in=[4], charges=2, next_at=4.6,
    )
    model["events"] = [{**_event(
        "reduce", "cooldown_change", .25,
        character="a", group="skill", operation="reduce", amount=4,
    ), "repeat": _repeat("once")}]
    result = check_rotation(template, model)
    assert _row(result, "reduce")["ready_at"] == [5]
    assert _row(result, "switch-b")["cancelled_cooldown"] == {
        "group": json.dumps(["a", "skill"], separators=(",", ":")),
        "before": 5,
        "after": 4.5,
    }
    assert _row(result, "next-cast")["status"] == "passed"


def test_restore_uses_stable_identity_when_parallel_entries_share_ready_time():
    template, model = _duplicate_ready_restore_case()
    result = check_rotation(template, model)
    assert _row(result, "restore")["ready_at"] == [6]
    assert _row(result, "switch-b")["cancelled_cooldown"] == {
        "group": json.dumps(["a", "skill"], separators=(",", ":")),
        "before": 6,
        "after": 5.5,
    }
    assert _row(result, "consume")["status"] == "passed"
    assert _row(result, "next-cast")["status"] == "passed"


def test_restore_uses_stable_identity_for_sequential_duplicate_ready_time():
    template, model = _duplicate_ready_restore_case(
        recovery="sequential", duration=0, include_followups=False,
    )
    result = check_rotation(template, model)
    assert _row(result, "restore")["ready_at"] == [6]
    assert _row(result, "switch-b")["cancelled_cooldown"] == {
        "group": json.dumps(["a", "skill"], separators=(",", ":")),
        "before": 6,
        "after": 6,
    }


def test_sequential_switch_in_same_pool_rebases_its_new_entry_after_cancel():
    template, model = _switch_shared_cooldown_case(recovery="sequential")
    result = check_rotation(template, model)
    switch = _row(result, "switch-b")
    assert switch["cancelled_cooldown"] == {
        "group": json.dumps(["a", "skill"], separators=(",", ":")),
        "before": 6,
        "after": 5.5,
    }
    assert switch["next_ready_at"] == 10.5
    assert result["cycles"][0]["boundary_state"]["cooldown_ready_in"] == {
        json.dumps(["a", "skill"], separators=(",", ":")): [4_750_000, 9_750_000],
    }


@pytest.mark.parametrize("recovery", ["parallel", "sequential"])
def test_dynamic_reduce_collapse_removes_active_entry_by_identity(recovery):
    template, model = _cooldown_cancel_case(
        recovery=recovery, initial_ready_in=[4], charges=2, next_at=1.3,
    )
    model["events"] = [{**_event(
        "reduce", "cooldown_change", .25,
        character="a", group="skill", operation="reduce", amount=10,
    ), "repeat": _repeat("once")}]
    result = check_rotation(template, model)
    assert _row(result, "reduce")["ready_at"] == []
    assert "cancelled_cooldown" not in _row(result, "switch-b")
    assert _row(result, "next-cast")["status"] == "passed"


@pytest.mark.parametrize(("operation", "amount"), [("reset", None), ("restore", 2)])
def test_plain_rotation_dynamic_removal_drops_active_cancel_anchor(operation, amount):
    template, model = _cooldown_cancel_case(
        recovery="sequential", initial_ready_in=[4], charges=2, next_at=1.3,
    )
    change = _event(
        "change", "cooldown_change", .25,
        character="a", group="skill", operation=operation,
    )
    if amount is not None:
        change["amount"] = amount
    model["events"] = [{**change, "repeat": _repeat("once")}]
    result = check_rotation(template, model)
    assert _row(result, "change")["ready_at"] == []
    assert "cancelled_cooldown" not in _row(result, "switch-b")
    assert _row(result, "next-cast")["status"] == "passed"


def test_plain_rotation_restore_of_prior_charge_preserves_historical_sequential_anchor():
    template, model = _cooldown_cancel_case(
        recovery="sequential", initial_ready_in=[4], charges=2, next_at=9.1,
    )
    model["events"] = [{**_event(
        "restore", "cooldown_change", .25,
        character="a", group="skill", operation="restore", amount=1,
    ), "repeat": _repeat("once")}]
    result = check_rotation(template, model)
    assert _row(result, "restore")["ready_at"] == [9]
    assert _row(result, "switch-b")["cancelled_cooldown"] == {
        "group": json.dumps(["a", "skill"], separators=(",", ":")),
        "before": 9,
        "after": 9,
    }


def test_plain_rotation_zero_second_cooldown_cancel_removes_ready_now_entry():
    template, model = _cooldown_cancel_case(next_at=1.3)
    model["abilities"][0]["cooldown"]["duration"] = 0
    result = check_rotation(template, model)
    assert _row(result, "switch-b")["cancelled_cooldown"] == {
        "group": json.dumps(["a", "skill"], separators=(",", ":")),
        "before": 1,
        "after": .5,
    }
    assert _row(result, "next-cast")["status"] == "passed"


def _cross_cycle_dynamic_cancel_case():
    template, model = _cooldown_cancel_case(
        recovery="sequential", initial_ready_in=[4], charges=2, next_at=1.1,
    )
    model.update(period=2, max_cycles=5)
    template["loop_duration"] = 2
    model["actions"][0].update(
        start=1.5, occupation_end=2.5, switch_allowed_at=2.2,
        repeat=_repeat("once"),
    )
    model["actions"][1].update(
        start=.2, occupation_end=.3, switch_allowed_at=.3,
        repeat=_repeat("cycles", cycles=[1]),
    )
    model["actions"][2].update(
        start=.4, occupation_end=.5, switch_allowed_at=.5,
        repeat=_repeat("cycles", cycles=[1]),
    )
    model["actions"][3].update(
        start=1.1, occupation_end=1.2, switch_allowed_at=1.2,
        repeat=_repeat("cycles", cycles=[2]),
    )
    model["events"] = [{**_event(
        "reduce", "cooldown_change", .1,
        character="a", group="skill", operation="reduce", amount=4,
    ), "repeat": _repeat("cycles", cycles=[1])}]
    # These finite switches occur only in source cycle 1, so they are not part
    # of the cycle-0 damage-template switch projection.
    template["switches"] = []
    return template, model


def test_plain_rotation_keeps_cross_cycle_cancel_anchor_and_dynamic_update():
    template, model = _cross_cycle_dynamic_cancel_case()
    result = check_rotation(template, model)
    assert "cooldown_anchor" not in result["cycles"][0]["boundary_state"]
    assert _row(result, "reduce", 1)["ready_at"] == [5]
    assert _row(result, "switch-b", 1)["cancelled_cooldown"] == {
        "group": json.dumps(["a", "skill"], separators=(",", ":")),
        "before": 5,
        "after": 4.7,
    }
    assert _row(result, "next-cast", 2)["status"] == "passed"


@pytest.mark.parametrize(("recovery", "ready"), [("sequential", 20), ("parallel", 15)])
def test_multi_charge_recovery_queues(recovery, ready):
    model = _model()
    model["abilities"][0]["cooldown"].update(charges=2, recovery=recovery)
    model["abilities"][0]["rules"] = []
    model["actions"][0].pop("rule_outcomes")
    model["actions"].append(_action("e2", "e", 5, 5.5))
    assert _row(_check(model), "e2")["next_ready_at"] == ready


@pytest.mark.parametrize(("operation", "extra"), [("reset", {}), ("reduce", {"amount": 5}), ("restore", {"amount": 1})])
def test_explicit_cd_changes_enable_second_actual_cast(operation, extra):
    model = _model()
    model["abilities"][0]["rules"] = []
    model["actions"][0].pop("rule_outcomes")
    model["actions"].append(_action("e2", "e", 5, 5.5))
    model["events"].append(_event("reset", "cooldown_change", 5, order=-1,
                                  character="a", group="e", operation=operation, **extra))
    result = _check(model)
    assert _row(result, "e2")["status"] == "passed"
    # 第二次动作真正在 5s 施放，所以它自己的 10s CD 可使下一轮 0s 的 E 失败。
    assert _row(result, "e1", 1)["status"] == "failed"


def test_cross_cycle_particle_credit_is_retained_and_not_duplicated():
    model = _model()
    model["events"][2]["credit_at"] = 10
    model["events"][2]["order"] = -1
    result = _check(model)
    assert result["status"] == "passed"
    credits = [r for r in result["event_log"] if r["id"] == "credit"]
    assert [(r["t"], r["source_cycle"]) for r in credits] == [(10, 0), (20, 1)]
    assert result["cycles"][0]["boundary_state"]["in_flight"] == [("spawn", -1, 4)]


def test_once_enemy_particles_do_not_repeat_to_hide_second_cycle_failure():
    model = _model()
    model["abilities"][0]["rules"] = []
    model["actions"][0].pop("rule_outcomes")
    for ev in (model["events"][0], model["events"][2]):
        ev["repeat"] = _repeat("once")
    model["events"][0].update(source_kind="enemy")
    model["events"][0].pop("action_id")
    result = _check(model)
    assert result["status"] == "failed"
    assert _row(result, "spend", 2)["deficit"] == 10


def test_future_finite_event_prevents_early_perpetual_certificate():
    model = _model()
    model["events"].append(_event("future", "energy_direct", 2, amounts={"a": 1},
                                  repeat=_repeat("cycles", cycles=[19])))
    result = _check(model)
    assert result["status"] == "unproven"
    assert result["energy_check"]["checked_cycles"] == 3


def test_slow_energy_change_is_unproven_even_when_three_cycles_succeed():
    model = _model()
    model["characters"][0]["energy_capacity"] = 100
    result = _check(model)
    assert result["status"] == "unproven"
    assert [c["end_energy"]["a"] for c in result["cycles"]] == [12, 14, 16]


def test_initial_cd_carries_across_the_opening():
    model = _model()
    model["initial_cooldowns"][0]["ready_in"] = [2]
    assert _row(_check(model), "e1")["next_ready_at"] == 2


def test_initial_cooldown_queue_must_remain_sorted_before_identity_assignment():
    model = _model()
    model["abilities"][0]["cooldown"]["charges"] = 2
    model["initial_cooldowns"][0]["ready_in"] = [2, 1]
    result = _check(model)
    assert result["status"] == "invalid"
    assert any(issue["code"] == "cooldown_initial" for issue in result["issues"])


def test_particle_conservation_checks_missing_and_excess_credit():
    model = _model()
    model["events"][2]["count"] = 3
    assert _check(model)["status"] == "pending"
    model["events"][0]["lost_count"] = 1
    assert _check(model)["status"] == "failed"  # 能量只回 9，第二次 Q 缺 1。
    model["events"][2]["count"] = 5
    assert _check(model)["status"] == "invalid"


def test_damage_links_must_reference_matching_explicit_actions_and_switches():
    model = _model()
    template = _template(model)
    template["hits"] = [{"id": "h", "character": "a", "t": 1.2}]
    assert check_rotation(template, model)["status"] == "pending"
    model["action_hit_links"] = [{"action_id": "q1", "hit_id": "h"}]
    assert check_rotation(template, model)["status"] == "passed"
    template["hits"][0]["count"] = 20
    assert check_rotation(template, model)["status"] == "pending"
    template["hits"] = []
    model["action_hit_links"] = []
    template["switches"] = [{"t": 1, "character": "a"}]
    assert check_rotation(template, model)["status"] == "invalid"


def test_capability_is_serializable_and_defensive():
    caps = rotation_capabilities()
    json.dumps(caps, allow_nan=False)
    caps["schema"]["properties"].clear()
    assert "events" in rotation_capabilities()["schema"]["properties"]


def test_shipped_example_is_self_contained_and_passes():
    path = Path(__file__).resolve().parents[1] / "examples" / "rotation-energy-check.json"
    package = json.loads(path.read_text(encoding="utf-8"))
    assert package["kind"] == "genshin-rotation-check"
    assert check_rotation(package["template"], package["rotation_model"])["status"] == "passed"
