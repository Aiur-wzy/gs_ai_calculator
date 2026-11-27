"""Issue #1: one position-pool charge covers all its contributors to one target."""
from __future__ import annotations

import copy
import json
from collections import Counter

import pytest
from test_direct_stellar_swirl import direct_fixture
from test_elemental_multiaura_v4 import aura
from test_elemental_world_stellar_swirl_deadline_adversarial import (
    area_damage,
    generated_since,
    seed_vortex,
    target_model,
    world_with_targets,
)
from test_mechanics_execution import (
    ability,
    action,
    add,
    condition,
    rule,
    second_character,
)
from test_special_reaction_mechanics_projection import _application_fixture, _enable

from genshin_engine.elemental import ElementalPending
from genshin_engine.mechanics import _Mechanics, execute_mechanics
from genshin_engine.rotation_port import TICKS
from genshin_engine.special_reactions import direct_stellar_damage_profile_content_hash


def _switch(template, rotation, outgoing, incoming, t):
    ident = f"switch-{outgoing}-{incoming}"
    rotation["abilities"].append(ability(ident, outgoing, "action.switch"))
    rotation["actions"].append(action(
        ident, ident, t, round(t + .1, 6), order=5, switch_to=incoming,
        repeat={"mode": "once"},
    ))
    template["switches"].append({"t": t, "character": incoming})


def _quill(name, scope, capacity, *, group=True, trigger=None, duration=30, amount=100):
    quota = {
        "capacity": capacity,
        "scope": "team",
        "refresh": "reset",
        "consume": {"event": "reaction_damage", "phase": "pre", "reaction_types": ["stellar_swirl"]},
    }
    if group:
        quota["unit"] = "damage_component_target"
    return rule(
        name, "benefit.reaction_flat.all", amount,
        trigger=trigger, domain="provider", duration=duration,
        beneficiaries={"mode": "team", "scope": scope}, quota=quota,
    )


def _delayed_fixture(*, third_contributor):
    template, rotation, model = _application_fixture(
        "anemo", initial=[aura("cryo", 1.4, "b")], times=(.2, .9) if third_contributor else (.2,),
    )
    for cid in ("b", "c"):
        second_character(template, rotation, cid)
    if third_contributor:
        _switch(template, rotation, "a", "c", .5)
        rotation["abilities"].append(ability("wind-c", "c"))
        rotation["actions"].append(action("second-wind", "wind-c", .7, .8, order=6, repeat={"mode": "once"}))
        template["hits"][1].update(character="c", element="anemo")
        model["hit_events"][1].update(
            action_id="second-wind", application=copy.deepcopy(model["hit_events"][0]["application"]),
        )
        _switch(template, rotation, "c", "a", 1.7)
        rotation["actions"].append(action("quills-ready", "e", 2, 2.1, order=30,
                                            repeat={"mode": "once"}))
    else:
        _switch(template, rotation, "a", "c", 1)
        condition(model, "quills-ready", 2)
    _enable(model, "stellar_swirl")
    # Arm the pools after the immediate Anemo events so each baseline compares
    # one delayed Cryo component with known starting balances.
    return template, rotation, model


def _execute(*inputs):
    output = execute_mechanics(*inputs)
    assert output["status"] in ("passed", "unproven"), output["issues"]
    return output


def _quota_rows(output, rule_id=None):
    return [
        row for row in output["mechanics_trace"]
        if row["type"] == "quota" and row["cycle"] == 0
        and (rule_id is None or row["rule_id"] == rule_id)
    ]


def _ice_hits(output):
    return {
        h["character"]: h for h in output["result"]["hits"]
        if h["damage_system"] == "stellar" and ":detonation:cryo:" in h["id"]
    }


def test_legacy_delayed_swirl_does_not_debit_absent_frontline_contributor():
    template, rotation, model = _delayed_fixture(third_contributor=False)
    add(model, _quill("front", "current_frontline", 2, group=False, trigger={
        "event": "condition", "phase": "pre", "event_ids": ["quills-ready"],
    }))
    output = _execute(template, rotation, model)
    assert {role: hit["zones"]["reaction_flat"] for role, hit in _ice_hits(output).items()} == {"a": 0, "b": 0}
    assert not any(row["consumed"] for row in _quota_rows(output, "front"))


def test_legacy_direct_swirl_uses_hitting_actor_position_at_resolution():
    template, rotation, model = direct_fixture()
    second_character(template, rotation, "b")
    _switch(template, rotation, "a", "b", .5)
    model["special_reaction_events"][0]["t"] = 1
    add(model, _quill("front", "current_frontline", 1, group=False))
    output = _execute(template, rotation, model)
    hit, = [h for h in output["result"]["hits"] if h["damage_system"] == "stellar"]
    assert hit["character"] == "a" and hit["zones"]["reaction_flat"] == 0
    assert not any(row["consumed"] for row in _quota_rows(output, "front"))


@pytest.mark.parametrize("front_enabled,back_enabled", [(True, True), (True, False), (False, True), (False, False)])
def test_delayed_component_debits_independent_position_pools(
        front_enabled, back_enabled):
    template, rotation, model = _delayed_fixture(third_contributor=True)
    baseline = _ice_hits(_execute(*copy.deepcopy((template, rotation, model))))
    assert set(baseline) == {"a", "b", "c"}
    activation = {"event": "action_start", "phase": "pre", "action_ids": ["quills-ready"]}
    if front_enabled:
        add(model, _quill("front", "current_frontline", 1, trigger=activation))
    if back_enabled:
        add(model, _quill("back", "current_offfield", 2, trigger=activation))
    output = _execute(template, rotation, model)
    hits = _ice_hits(output)
    assert set(hits) == set(baseline)
    for role, receives in (("a", front_enabled), ("b", back_enabled), ("c", back_enabled)):
        assert hits[role]["zones"]["reaction_flat"] == (100 if receives else 0)
        # With the neutral fixture's zero Crit, system resistance is the
        # only multiplier after the raw flat amount.
        new, old = hits[role], baseline[role]
        assert new["expected"] / new["result_weight"] - old["expected"] / old["result_weight"] == pytest.approx(
            90 if receives else 0
        )
    rows = _quota_rows(output)
    assert Counter(row["rule_id"] for row in rows) == {
        name: 1 for enabled, name in ((front_enabled, "front"), (back_enabled, "back")) if enabled
    }
    for row in rows:
        assert row["target_id"] == "enemy"
        assert row["frontline"] == "a"
        members = {"a"} if row["rule_id"] == "front" else {"b", "c"}
        assert set(row["beneficiaries"]) == members
        assert set(row["hit_ids"]) == {hits[role]["id"] for role in members}
        assert row["consumed"] and row["before"] - row["after"] == 1
    winner = "c" if back_enabled and not front_enabled else "a"
    assert hits["b"]["result_weight"] == pytest.approx(.6)
    assert hits[winner]["result_weight"] == pytest.approx(.3)
    assert hits[{"a": "c", "c": "a"}[winner]]["result_weight"] == pytest.approx(.05)


def test_grouped_charge_belongs_to_concrete_target_and_ignores_area_members_not_hit(monkeypatch):
    template, rotation, model = _delayed_fixture(third_contributor=True)
    activation = {"event": "action_start", "phase": "pre", "action_ids": ["quills-ready"]}
    add(model, _quill("front", "current_frontline", 3, trigger=activation))
    add(model, _quill("back", "current_offfield", 2, trigger=activation))
    world = world_with_targets([(target, (), "known") for target in ("enemy.c", "enemy", "enemy.b")])
    world.register_target(target_model("unhit"), area_ids=("outside",))
    seed_vortex(world)
    world.advance(3 * TICKS)
    world_damage = area_damage(generated_since(world))
    project = _Mechanics._project_special_damage

    def project_world_targets(runtime, request, cycle, parent):
        if request["reaction_type"].startswith("stellar_swirl_cryo_"):
            # Exercise the settlement boundary with real world selection
            # evidence and the rotation's own contributor provenance.
            request = {
                **request,
                **{key: world_damage[key] for key in (
                    "target_id", "target_selection_policy", "target_snapshot_source",
                    "target_snapshot_event_id", "selection_audit_id", "resolved_target_ids",
                )},
            }
        return project(runtime, request, cycle, parent)

    monkeypatch.setattr(_Mechanics, "_project_special_damage", project_world_targets)
    output = _execute(template, rotation, model)
    rows = _quota_rows(output)
    for name, beneficiaries, charges in (
        ("front", {"a"}, [(3, 2), (2, 1), (1, 0)]),
        ("back", {"b", "c"}, [(2, 1), (1, 0), (0, 0)]),
    ):
        pool = [row for row in rows if row["rule_id"] == name]
        assert [row["target_id"] for row in pool] == ["enemy", "enemy.b", "enemy.c"]
        assert [(row["before"], row["after"]) for row in pool] == charges
        assert [set(row["beneficiaries"]) for row in pool] == [
            beneficiaries if before > 0 else set() for before, _ in charges
        ]
    hits = {hit["id"]: hit for hit in output["result"]["hits"]}
    for row in rows:
        assert {hits[hit_id]["zones"]["reaction_flat"] for hit_id in row["hit_ids"]} == (
            {100} if row["consumed"] else {0}
        )


@pytest.mark.parametrize("bad_targets", [["enemy", "enemy"], ["enemy", ""]])
def test_invalid_world_selection_fails_before_first_target_can_debit(monkeypatch, bad_targets):
    template, rotation, model = _delayed_fixture(third_contributor=True)
    add(model, _quill("front", "current_frontline", 3, trigger={
        "event": "action_start", "phase": "pre", "action_ids": ["quills-ready"],
    }))
    project = _Mechanics._project_special_damage
    observed = []

    def inject_invalid_selection(runtime, request, cycle, parent):
        if not request["reaction_type"].startswith("stellar_swirl_cryo_"):
            return project(runtime, request, cycle, parent)
        request = {
            **request, "target_id": None,
            "resolved_target_ids": bad_targets,
            "target_snapshot_source": "static_world_registry_v1",
            "selection_audit_id": "invalid-selection",
            "target_snapshot_event_id": "invalid-selection",
        }
        try:
            return project(runtime, request, cycle, parent)
        finally:
            instance, = runtime.state["front"]["instances"]
            observed.append(instance["quota"]["team"])

    monkeypatch.setattr(_Mechanics, "_project_special_damage", inject_invalid_selection)
    out = execute_mechanics(template, rotation, model)
    assert any(issue["code"] == "stellar_swirl_target_selection_invalid" for issue in out["issues"])
    assert observed == [3]


def test_duplicate_dispatch_does_not_repeat_component_damage_or_quota(monkeypatch):
    template, rotation, model = _delayed_fixture(third_contributor=True)
    activation = {"event": "action_start", "phase": "pre", "action_ids": ["quills-ready"]}
    add(model, _quill("back", "current_offfield", 2, trigger=activation))
    baseline = _execute(*copy.deepcopy((template, rotation, model)))
    project = _Mechanics._project_special_damage

    def repeat_dispatch(runtime, request, cycle, parent):
        project(runtime, request, cycle, parent)
        if request["reaction_type"].startswith("stellar_swirl_cryo_"):
            project(runtime, request, cycle, parent)

    monkeypatch.setattr(_Mechanics, "_project_special_damage", repeat_dispatch)
    output = _execute(template, rotation, model)
    assert _quota_rows(output) == _quota_rows(baseline)
    assert output["result"]["hits"] == baseline["result"]["hits"]


def test_same_tick_vortex_deadline_reads_position_before_switch():
    template, rotation, model = _application_fixture(
        "anemo", initial=[aura("cryo", .7, "b")],
    )
    for cid in ("b", "c"):
        second_character(template, rotation, cid)
    _switch(template, rotation, "a", "c", 3.2)
    _enable(model, "stellar_swirl")
    add(model, _quill("front", "current_frontline", 2),
        _quill("back", "current_offfield", 2))
    output = _execute(template, rotation, model)
    rows = {
        row["rule_id"]: row for row in _quota_rows(output) if row["t"] == 3.2
    }
    assert all(row["frontline"] == "a" for row in rows.values())
    assert rows["front"]["beneficiaries"] == ["a"]
    assert rows["back"]["beneficiaries"] == ["b"]


def test_two_components_at_the_same_time_share_the_balance_but_each_consume():
    template, rotation, model = direct_fixture()
    first, = model["special_reaction_events"]
    second = {**copy.deepcopy(first), "id": "direct-swirl-other", "order": first["order"] + 1}
    profile = second["ability_profile"]
    profile["ability_binding"]["component_id"] += "-other"
    profile["parameters"]["component"] = profile["ability_binding"]["component_id"]
    profile["profile_content_hash"] = direct_stellar_damage_profile_content_hash(profile)
    model["special_reaction_events"].append(second)
    add(model, _quill("front", "current_frontline", 1))
    output = _execute(template, rotation, model)
    rows = _quota_rows(output, "front")
    assert len(rows) == 2 and rows[0]["event_id"] != rows[1]["event_id"]
    assert rows[0]["t"] == rows[1]["t"]
    assert [(row["before"], row["after"]) for row in rows] == [(1, 0), (0, 0)]


def test_failed_second_contributor_leaves_both_balances_available(monkeypatch):
    template, rotation, model = _delayed_fixture(third_contributor=True)
    activation = {"event": "action_start", "phase": "pre", "action_ids": ["quills-ready"]}
    add(model, _quill("front", "current_frontline", 1, trigger=activation))
    add(model, _quill("back", "current_offfield", 2, trigger=activation))
    hit = _Mechanics.hit
    project = _Mechanics._project_special_damage
    observed = {}

    def fail_contributor(runtime, event, payload):
        if event.get("reaction_type", "").startswith("stellar_swirl_cryo_") and event["character"] == "a":
            raise ElementalPending("test_attribute_unavailable", "Synthetic attribute failure", event["id"])
        return hit(runtime, event, payload)

    def observe_balances(runtime, request, cycle, parent):
        try:
            return project(runtime, request, cycle, parent)
        finally:
            if request["reaction_type"].startswith("stellar_swirl_cryo_"):
                for name in ("front", "back"):
                    instance, = runtime.state[name]["instances"]
                    observed[name] = instance["quota"]["team"]

    monkeypatch.setattr(_Mechanics, "hit", fail_contributor)
    monkeypatch.setattr(_Mechanics, "_project_special_damage", observe_balances)
    output = execute_mechanics(template, rotation, model)
    assert any(issue["code"] == "test_attribute_unavailable" for issue in output["issues"])
    assert observed == {"front": 1, "back": 2}


def test_direct_and_immediate_and_delayed_damage_share_one_balance_at_different_positions():
    template, rotation, model = _delayed_fixture(third_contributor=False)
    _, _, direct = direct_fixture()
    event = direct["special_reaction_events"][0]
    ahead = copy.deepcopy(event)
    ahead.update(id="direct-swirl-first", t=.2, order=1)
    event.update(t=1.5)
    model["special_reaction_events"] = [ahead, event]
    rotation["abilities"][0].update(variant="tap", effective_level=10)
    add(
        model,
        _quill("front", "current_frontline", 1),
        _quill("back", "current_offfield", 2),
    )
    output = _execute(template, rotation, model)
    hits = [h for h in output["result"]["hits"] if h["damage_system"] == "stellar"]
    direct_hits = {h["t"]: h for h in hits if h["id"].endswith(":direct:a")}
    assert direct_hits[.2]["zones"]["reaction_flat"] == 100
    assert direct_hits[1.5]["zones"]["reaction_flat"] == 100
    immediate = {h["character"]: h for h in hits if ":immediate:anemo:" in h["id"]}
    assert immediate["a"]["zones"]["reaction_flat"] == 0
    assert immediate["b"]["zones"]["reaction_flat"] == 100
    assert {role: h["zones"]["reaction_flat"] for role, h in _ice_hits(output).items()} == {"a": 0, "b": 0}
    for rule_id, charges in (
        ("front", [(1, 0), (0, 0)]),
        ("back", [(2, 1), (1, 0), (0, 0)]),
    ):
        rows = [row for row in _quota_rows(output, rule_id) if row["reason"] != "no_eligible_contributor"]
        assert [(row["before"], row["after"]) for row in rows] == charges


def test_grouped_quota_resets_on_recast_and_expires_at_tick_boundary():
    template, rotation, model = direct_fixture()
    rotation["actions"].append(action("recast", "e", 1, 1.1, order=2, repeat={"mode": "once"}))
    event = model["special_reaction_events"][0]
    model["special_reaction_events"] = [
        {**copy.deepcopy(event), "id": f"swirl-{index}", "t": t, "action_id": cast, "order": 20 + index}
        for index, (t, cast) in enumerate(((.1, "cast"), (.3, "cast"), (1.1, "recast"), (2, "recast")))
    ]
    add(model, _quill("front", "current_frontline", 1, duration=1, trigger={
        "event": "action_start", "phase": "pre", "action_ids": ["cast", "recast"],
    }))
    first = _execute(template, rotation, model)
    cold = _execute(*json.loads(json.dumps((template, rotation, model))))
    assert [(r["t"], r["before"], r["after"]) for r in _quota_rows(first)] == [
        (.1, 1, 0), (.3, 0, 0), (1.1, 1, 0),
    ]
    assert [h["zones"]["reaction_flat"] for h in first["result"]["hits"] if h["damage_system"] == "stellar"] == [
        100, 0, 100, 0,
    ]
    assert first["result"] == cold["result"]
    assert _quota_rows(first) == _quota_rows(cold)
