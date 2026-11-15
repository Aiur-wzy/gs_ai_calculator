"""D18 expectations are hand calculated from the evidence note, not engine output."""
from __future__ import annotations

import copy

import pytest
from test_mechanics_execution import EVIDENCE, action, add, fixture, rule

from genshin_engine.elemental import ElementalRuntime
from genshin_engine.mechanics import execute_mechanics


def model():
    return {"schema_version": 1, "ruleset": "gauge-basic-v1",
            "target": {"id": "enemy", "kind": "ordinary", "knowledge": "known", "initial_aura": None, **EVIDENCE},
            "profiles": [profile(e) for e in ("pyro", "hydro", "cryo")]}


def profile(element, gu=1, group=None):
    return {"id": element, "element": element, "application": "apply", "gauge_units": gu,
            "icd": {"kind": "standard", "group": group} if group else {"kind": "none"}, **EVIDENCE}


def attempt(rt, element, t=0, actor="a", eid=None, landed=True):
    return rt.attempt(event_id=eid or f"{element}:{len(rt.trace)}", action_id="action", actor=actor,
                      time=round(t*1_000_000), application={"profile_id": element, "target_id": "enemy", "landed": landed})


def setup(elements=("hydro", "pyro", "pyro", "pyro"), times=None):
    template, rotation, mechanics = fixture(times or tuple(.2 for _ in elements))
    mechanics["elemental_model"] = model()
    for hit, descriptor, element in zip(template["hits"], mechanics["hit_events"], elements, strict=True):
        hit["element"] = element
        descriptor["application"] = {"profile_id": element, "target_id": "enemy", "landed": True}
    return template, rotation, mechanics


def test_weak_vaporize_consumes_without_leaving_trigger_aura_and_changes_damage():
    t, r, m = setup()
    before = copy.deepcopy((t, r, m))
    out = execute_mechanics(t, r, m)
    assert out["status"] in ("passed", "unproven"), out["issues"]
    assert [h["expected"] for h in out["result"]["hits"]] == pytest.approx([45, 67.5, 67.5, 45])
    trace = out["elemental_trace"][:4]
    assert trace[0]["after"]["aura"]["remaining_gu"] == .8
    assert trace[1]["after"]["aura"]["remaining_gu"] == pytest.approx(.3)
    assert trace[2]["after"]["aura"] is None
    assert trace[3]["after"]["aura"]["element"] == "pyro"
    assert out["resolved_template"]["hits"][1]["reaction"] == {"type": "amplify", "factor": 1.5}
    assert (t, r, m) == before


@pytest.mark.parametrize(("elements", "factor"), [(("pyro", "hydro"), 2), (("cryo", "pyro"), 2), (("pyro", "cryo"), 1.5)])
def test_other_amplify_directions(elements, factor):
    out = execute_mechanics(*setup(elements))
    assert [h["expected"] for h in out["result"]["hits"]] == pytest.approx([45, 45*factor])


def test_standard_icd_timer_does_not_restart_on_hit_counter_and_is_unbounded():
    m = model()
    m["profiles"][1] = profile("hydro", group="normal")
    rt = ElementalRuntime(m)
    values = [attempt(rt, "hydro", time)["allowed"] for time in (0, .1, .2, 2.4, 2.5, 2.6, 2.7, 2.8)]
    assert values == [True, False, False, True, True, False, False, True]
    rt = ElementalRuntime(m)
    assert [i+1 for i in range(30) if attempt(rt, "hydro", i*.01)["allowed"]] == [
        1, 4, 7, 10, 13, 16, 19, 22, 25, 28]
    assert attempt(rt, "hydro", 2.5)["reason"] == "icd_timer_reset"


def test_miss_does_not_use_icd_or_do_damage_and_no_application_does_do_damage():
    t, r, m = setup(("hydro", "hydro", "hydro"), (.2, .3, .4))
    m["elemental_model"]["profiles"][1] = profile("hydro", group="normal")
    m["hit_events"][0]["application"]["landed"] = False
    out = execute_mechanics(t, r, m)
    assert len(out["result"]["hits"]) == 2
    assert [row["reason"] for row in out["elemental_trace"][:3]] == ["missed", "first_hit", "icd_blocked"]
    m["elemental_model"]["profiles"][1] = {"id": "hydro", "element": "hydro", "application": "none", **EVIDENCE}
    out = execute_mechanics(t, r, m)
    assert [h["expected"] for h in out["result"]["hits"]] == [45, 45]
    assert out["elemental_trace"][1]["after"]["aura"] is None


def test_decay_refresh_rules_differ_for_pyro_and_hydro():
    for element, expiry in (("hydro", 20), ("pyro", 13)):
        rt = ElementalRuntime(model())
        attempt(rt, element)
        rt.profiles[element]["gauge_units"] = 2
        attempt(rt, element, 1)
        rt.advance(round((expiry-.01)*1_000_000))
        assert rt.aura["remaining_gu"] > 0
        rt.advance(expiry*1_000_000)
        assert rt.aura is None


def test_shared_icd_survives_different_actions_but_actor_keys_do_not_share():
    m = model()
    m["profiles"][1] = profile("hydro", group="shared")
    rt = ElementalRuntime(m)
    assert attempt(rt, "hydro", actor="a")["allowed"]
    assert not rt.attempt(event_id="new", action_id="another_cast", actor="a", time=1,
                         application={"profile_id": "hydro", "target_id": "enemy", "landed": True})["allowed"]
    assert attempt(rt, "hydro", .001, actor="b")["allowed"]


@pytest.mark.parametrize("mutate", [
    lambda m: m["elemental_model"]["target"].update(knowledge="unknown"),
    lambda m: m["elemental_model"]["profiles"][1].update(status="pending"),
    lambda m: m["elemental_model"]["profiles"][1]["icd"].update(kind="unknown"),
])
def test_unknown_input_does_not_publish_authoritative_result(mutate):
    t, r, m = setup()
    mutate(m)
    out = execute_mechanics(t, r, m)
    assert out["status"] == "pending"
    assert out["result"] is None
    assert out["resolved_template"] is None
    assert out["elemental_trace"][-1]["status"] == "pending"


def test_freeze_unsupported_in_first_ruleset_is_not_silent_no_reaction():
    out = execute_mechanics(*setup(("hydro", "cryo")))
    assert out["status"] == "pending"
    assert out["result"] is None
    assert out["issues"][-1]["code"] == "unsupported_reaction"


def test_pending_reaction_is_atomic_and_does_not_consume_icd_for_later_hit():
    m = model()
    m["profiles"][2] = profile("cryo", group="shared")
    rt = ElementalRuntime(m)
    attempt(rt, "hydro", .05)
    with pytest.raises(ValueError) as exc:
        attempt(rt, "cryo", .1, eid="unsupported-freeze")
    assert getattr(exc.value, "issue", {})["code"] == "unsupported_reaction"
    pending = rt.trace[-1]
    assert pending["application_allowed"] is None
    assert pending["reaction_occurred"] is False
    assert pending["before"]["aura"] == pending["after"]["aura"]
    assert rt.state()["icd"] == []

    # Continuing at the same timestamp is a direct-runtime adversarial probe.
    # The unsupported hit must not occupy sequence position 1.
    rt.aura = None
    recovered = attempt(rt, "cryo", .1, eid="supported-after-repair")
    assert recovered["allowed"] is True
    assert recovered["reason"] == "first_hit"
    assert recovered["icd_audit"]["attempt_number"] == 1
    assert recovered["icd_audit"]["sequence_index"] == 0


def test_manual_reaction_or_missing_application_rejected_in_simulation_mode():
    t, r, m = setup()
    t["hits"][1]["reaction"] = {"type": "amplify", "factor": 99}
    assert execute_mechanics(t, r, m)["status"] == "invalid"
    del t["hits"][1]["reaction"]
    del m["hit_events"][2]["application"]
    assert execute_mechanics(t, r, m)["status"] == "invalid"


def test_no_damage_application_and_reaction_triggered_energy_share_action_queue():
    t, r, m = setup(("pyro",), (.2,))
    r["characters"][0]["initial_energy"] = 0
    m["application_events"] = [{"id": "wet", "action_id": "cast", "t": .1, "order": 20,
                                "application": {"profile_id": "hydro", "target_id": "enemy", "landed": True}}]
    gain = rule("reaction_energy", trigger={"event": "elemental_reaction", "phase": "post", "reaction_types": ["vaporize"]})
    gain["effects"] = []
    gain["resources"] = [{"id": "refund", "kind": "energy_direct", "delay": 0, "amounts": {"a": 10}, "source_part": "refund"}]
    add(m, gain)
    m["resource_ownership"] = [{"rule_id": "reaction_energy", "action_id": "cast", "source_part": "refund", "replaces_event_ids": []}]
    out = execute_mechanics(t, r, m)
    assert [h["expected"] for h in out["result"]["hits"]] == [67.5]
    assert out["rotation_check"]["cycles"][0]["end_energy"]["a"] == 10
    energies = [e for e in out["rotation_check"]["event_log"] if e["type"] == "energy_direct"]
    assert energies[0]["t"] == .2


def test_reaction_buff_only_applies_to_following_hit():
    t, r, m = setup()
    add(m, rule(trigger={"event": "elemental_reaction", "phase": "post", "reaction_types": ["vaporize"]}))
    out = execute_mechanics(t, r, m)
    assert [h["expected"] for h in out["result"]["hits"]] == pytest.approx([45, 67.5, 135, 90])


def test_one_action_pattern_uses_shared_profile_without_per_hit_reactions():
    t, r, m = setup(("hydro",), (.2,))
    m["elemental_model"]["profiles"][1] = profile("hydro", group="burst")
    descriptor = m["hit_events"].pop()
    m["hit_patterns"] = [{"id": "burst", "prototype_hit_id": "h0", "action_id": "cast", "offsets": [.2, .3, .4, .5],
                          "orders": [10, 11, 12, 13], "application": descriptor["application"]}]
    r["actions"] = [action(repeat={"mode": "once"})]
    out = execute_mechanics(t, r, m)
    assert len(out["result"]["hits"]) == 4
    assert [x["allowed"] for x in out["elemental_trace"]] == [True, False, False, True]
