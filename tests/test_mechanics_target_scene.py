"""Native-plan mechanics accept explicit static targets and keep per-target state."""
from __future__ import annotations

import copy
import json

import pytest
from test_direct_stellar_swirl import direct_fixture
from test_elemental_multiaura_v4 import aura
from test_elemental_multiaura_v4 import model as elemental_model
from test_elemental_stellar_packet_adversarial import stellar_runtime
from test_mechanics_execution import (
    EVIDENCE,
    ability,
    action,
    add,
    rule,
    second_character,
)
from test_plan_mechanics import native
from test_special_reaction_mechanics_projection import _application_fixture, _enable
from test_stellar_swirl_position_quota import _quill

from genshin_engine.damage_report_window import CONTRACT
from genshin_engine.elemental import ElementalPending
from genshin_engine.elemental_world import ElementalWorldPending
from genshin_engine.mechanics import _Mechanics, execute_mechanics
from genshin_engine.target_scene import TargetSceneRuntime

AREA = "battlefield.primary"
PRIMARY = "enemy"
SECONDARY = "enemy.b"


def _static_scene(element, *, primary_auras=(), secondary_auras=(), times=(.2, .2), end=1):
    template, rotation, model = _application_fixture(element, initial=primary_auras, times=times)
    for hit in template["hits"]:
        hit["element"] = element
    primary = model["elemental_model"]["target"]
    secondary = {**copy.deepcopy(primary), "id": SECONDARY, "initial_auras": list(secondary_auras)}
    model["target_scene"] = {
        "schema_version": 1,
        "primary_area_ids": [AREA],
        "additional_targets": [{"target": secondary, "area_ids": [AREA]}],
    }
    for index, descriptor in enumerate(model["hit_events"]):
        target = (PRIMARY, SECONDARY)[index % 2]
        descriptor["target_id"] = target
        descriptor["application"] = {
            "profile_id": element, "target_id": target, "area_id": AREA,
            "landed": True, "atomic_parent_id": f"contact.{index // 2}",
        }
    rotation["damage_report"] = {"contract": CONTRACT, "mode": "explicit", "end": end}
    return template, rotation, model


def _execute(*data):
    result = execute_mechanics(*data)
    assert result["status"] in ("passed", "unproven"), result["issues"]
    assert result["result"]["damage_report_window"]["required_validation"]["status"] == "complete"
    return result


def _applications(output):
    return [
        row for row in output["result"]["elemental"]["trace"]
        if row["type"] == "elemental_application"
    ]


def test_same_attack_keeps_melt_and_plain_hit_on_their_own_targets():
    data = _static_scene("pyro", primary_auras=[aura("cryo", .8, "a")])
    template, rotation, _ = data
    rotation["abilities"][0] = ability(kind="action.elemental_burst", energy={"required": 10, "amount": 10})
    rotation["events"] = [{
        "id": "burst-cost", "type": "energy_spend", "action_id": "cast", "character": "a",
        "required": 10, "amount": 10, "t": 0, "order": 2, "repeat": {"mode": "once"}, **EVIDENCE,
    }]
    for hit in template["hits"]:
        hit["damage_type_ids"] = ["damage.elemental_burst"]
    baseline_input = copy.deepcopy(data)
    output = _execute(*data)
    rows = {h["target_id"]: h for h in output["result"]["hits"]}
    assert set(rows) == {PRIMARY, SECONDARY}
    assert rows[PRIMARY]["expected"] == pytest.approx(90)
    assert rows[SECONDARY]["expected"] == pytest.approx(45)
    contacts = {r["target_id"]: r for r in _applications(output)}
    assert [r["type"] for r in contacts[PRIMARY]["reactions"]] == ["melt"]
    assert contacts[SECONDARY]["reactions"] == []
    assert [row["element"] for row in contacts[SECONDARY]["after"]["auras"]] == ["pyro"]
    spent = [event for event in output["rotation_check"]["event_log"] if event["type"] == "energy_spend"]
    assert len(spent) == 1 and spent[0]["before"] == 10 and spent[0]["after"] == 0
    assert output["result"]["summary"]["total_damage"] == pytest.approx(135)
    assert output["result"]["summary"]["by_target"] == pytest.approx({PRIMARY: 90, SECONDARY: 45})
    assert data == baseline_input
    assert _execute(*json.loads(json.dumps(data)))["result"] == output["result"]


def test_one_recipient_missing_does_not_advance_other_targets_icd_counter():
    template, rotation, model = _static_scene("pyro", times=(.2, .2, .3, .3, .4, .4))
    profile = next(p for p in model["elemental_model"]["profiles"] if p["id"] == "pyro")
    profile["icd"] = {
        "kind": "timer_counter", "counter_tag": "same-attack", "rule_group": "three-hits-or-timer",
        "hit_interval": 3, "reset_seconds": 2.5,
    }
    model["hit_events"][1]["application"]["landed"] = False
    output = _execute(template, rotation, model)
    seen = {target: [] for target in (PRIMARY, SECONDARY)}
    for contact in _applications(output):
        seen[contact["target_id"]].append(contact["application_allowed"])
    assert seen == {PRIMARY: [True, False, False], SECONDARY: [False, True, False]}
    actual = [h["id"] for h in output["result"]["hits"]]
    assert set(actual) == {"h0", "h2", "h3", "h4", "h5"}
    assert output["result"]["summary"]["total_damage"] == pytest.approx(225)


def test_ordinary_swirl_keeps_reaction_damage_cooldowns_and_elements_separate():
    template, rotation, model = _static_scene(
        "anemo", primary_auras=[aura("cryo", 2.8, "b")],
        secondary_auras=[aura("hydro", 2.8, "b")], times=(.2, .2, .3, .3, .4, .4),
    )
    second_character(template, rotation, "b")
    output = _execute(template, rotation, model)
    reactions = [hit for hit in output["result"]["hits"] if hit["damage_system"] == "transformative"]
    assert [(hit["t"], hit["target_id"], hit["hit_name"]) for hit in reactions] == [
        (time, target, name) for time in (.2, .3)
        for target, name in ((PRIMARY, "冰扩散"), (SECONDARY, "水扩散"))
    ]
    attempts = [row for row in output["result"]["elemental"]["trace"] if row["type"] == "reaction_damage_attempt"]
    for target in (PRIMARY, SECONDARY):
        assert [row["allowed"] for row in attempts if row["target_id"] == target] == [True, True, False]


def test_scene_rejects_unknown_target_before_assigning_damage_to_primary():
    template, rotation, model = _static_scene("pyro")
    descriptor = model["hit_events"][1]
    descriptor["target_id"] = descriptor["application"]["target_id"] = "missing"
    output = execute_mechanics(template, rotation, model)
    assert output["status"] in ("pending", "invalid"), output["issues"]
    assert output.get("result") is None
    assert output["issues"]


def test_scene_rejects_hit_outside_target_area():
    template, rotation, model = _static_scene("pyro")
    model["target_scene"]["additional_targets"][0]["area_ids"] = ["off-axis"]
    output = execute_mechanics(template, rotation, model)
    assert output.get("result") is None
    assert any(row["code"] == "target_scene_area" for row in output["issues"])


def test_scene_rejects_conflicting_damage_and_application_targets():
    template, rotation, model = _static_scene("pyro")
    model["hit_events"][1]["target_id"] = PRIMARY
    output = execute_mechanics(template, rotation, model)
    assert output.get("result") is None
    assert any(row["code"] == "target_identity_conflict" for row in output["issues"])


def test_scene_rejects_duplicate_target_identity_at_declaration():
    template, rotation, model = _static_scene("pyro")
    model["target_scene"]["additional_targets"][0]["target"]["id"] = PRIMARY
    output = execute_mechanics(template, rotation, model)
    assert output.get("result") is None
    assert any(row["code"] == "target_scene_duplicate_target" for row in output["issues"])


def test_scene_requires_a_finite_report_window():
    template, rotation, model = _static_scene("pyro")
    rotation.pop("damage_report")
    output = execute_mechanics(template, rotation, model)
    assert output.get("result") is None
    assert any(row["code"] == "target_scene_finite_window" for row in output["issues"])


def test_optional_scene_preserves_existing_single_target_arithmetic():
    template, rotation, model = _static_scene("pyro", primary_auras=[aura("cryo", .8, "a")], times=(.2,))
    scene_plan = _execute(template, rotation, model)
    model.pop("target_scene")
    previous = _execute(template, rotation, model)
    prev_hit, = previous["result"]["hits"]
    world_hit, = scene_plan["result"]["hits"]
    assert prev_hit["id"] == world_hit["id"]
    assert "by_target" not in previous["result"]["summary"]
    assert world_hit["target_id"] == PRIMARY
    assert prev_hit["expected"] == world_hit["expected"] == pytest.approx(90)


def _star_scene(times=(.2, .2)):
    template, rotation, model = _static_scene(
        "anemo", primary_auras=[aura("cryo", .8, "b")],
        secondary_auras=[aura("cryo", .8, "b")], times=times, end=3.3,
    )
    second_character(template, rotation, "b")
    additional = model["target_scene"]["additional_targets"]
    outside = {**copy.deepcopy(additional[0]["target"]), "id": "outside"}
    additional.append({"target": outside, "area_ids": ["off-axis"]})
    _enable(model, "stellar_swirl")
    add(model, _quill("lead", "current_frontline", 4), _quill("chorus", "current_offfield", 3))
    return template, rotation, model


def test_live_world_star_swirl_selects_targets_at_detonation():
    output = _execute(*_star_scene(times=(.2,)))
    stellar = [hit for hit in output["result"]["hits"] if hit["damage_system"] == "stellar"]
    assert {hit["target_id"] for hit in stellar if hit["t"] == .2} == {PRIMARY}
    assert {hit["target_id"] for hit in stellar if hit["t"] == 3.2} == {PRIMARY, SECONDARY}
    assert len(stellar) == 6
    summary = output["result"]["summary"]
    assert summary["by_target"]["outside"] == 0
    assert sum(summary["by_target"].values()) == pytest.approx(summary["total_damage"])


def test_live_world_star_swirl_partitions_independent_contacts_and_detonation_by_target():
    template, rotation, model = _star_scene()
    model["hit_events"][1]["application"]["atomic_parent_id"] = "contact.1"
    output = _execute(template, rotation, model)
    stellar = [h for h in output["result"]["hits"] if h["damage_system"] == "stellar"]
    assert {(hit["t"], hit["target_id"], hit["character"]) for hit in stellar} == {
        (time, target, actor) for time in (.2, 3.2)
        for target in (PRIMARY, SECONDARY) for actor in ("a", "b")
    }
    assert len(stellar) == 8
    for hit in stellar:
        expected = 0 if (hit["t"], hit["target_id"], hit["character"]) == (3.2, SECONDARY, "b") else 100
        assert hit["zones"]["reaction_flat"] == pytest.approx(expected)
        winner = "a" if hit["t"] == .2 else "b"
        assert hit["result_weight"] == pytest.approx(.6 if hit["character"] == winner else .3)
    evidence = output["mechanics_trace"]
    quota = [row for row in evidence if row["type"] == "quota" and row.get("unit") == "damage_component_target"]
    assert [(row["target_id"], row["before"], row["after"]) for row in quota if row["rule_id"] == "chorus"] == [
        (PRIMARY, 3, 2), (SECONDARY, 2, 1), (PRIMARY, 1, 0), (SECONDARY, 0, 0),
    ]
    selected = next(row for row in evidence if row["type"] == "world_area_target_selection")
    assert selected["target_ids"] == [PRIMARY, SECONDARY]
    delayed = [row for row in _applications(output) if row["t"] == 3.2]
    assert [row["target_id"] for row in delayed] == [PRIMARY, SECONDARY]  # exactly one 1U application
    assert all(row["application_allowed"] for row in delayed)


def test_same_parent_star_swirl_creates_once_and_settles_each_target(tmp_path):
    from genshin_app.equipment.files import json_bytes, read_plan

    template, rotation, model = _star_scene()
    for character in template["characters"]:
        character["stats"]["energy_recharge"] = 1
    output = _execute(template, rotation, model)
    # One contact packet starts one vortex. A second same-time independent
    # application would instead advance the already created vortex.
    states = [row for row in output["mechanics_trace"]
              if row["type"] == "special_reaction_state" and row["t"] == .2]
    creation, = [row for row in states if row["operation"] == "create_vortex_from_packet"]
    assert (creation["packet_size"], creation["counted_children"], creation["count"]) == (2, 1, 1)
    assert not any(row["operation"] == "advance_existing_vortex_from_packet_child" for row in states)
    hits = [row for row in output["result"]["hits"] if row["damage_system"] == "stellar"]
    assert len(hits) == len({row["id"] for row in hits}) == 8
    assert {(row["target_id"], row["character"]) for row in hits if row["t"] == .2} == {
        (target, character) for target in (PRIMARY, SECONDARY) for character in ("a", "b")
    }
    spent = [row for row in output["mechanics_trace"] if row["type"] == "quota" and row["rule_id"] == "chorus"]
    assert [(row["target_id"], row["before"], row["after"]) for row in spent] == [
        (PRIMARY, 3, 2), (SECONDARY, 2, 1), (PRIMARY, 1, 0), (SECONDARY, 0, 0),
    ]
    assert output["result"]["summary"]["by_target"]["outside"] == 0

    plan = native()
    plan.update(manual_template=template, rotation_model=rotation, mechanics_model=model)
    path = tmp_path / "atomic-target-scene.plan.json"
    path.write_bytes(json_bytes(plan))
    restored = read_plan(path)["plan"]
    replayed = _execute(restored["manual_template"], restored["rotation_model"], restored["mechanics_model"])
    assert replayed["result"] == output["result"]
    assert replayed["mechanics_trace"] == output["mechanics_trace"]


def test_second_same_parent_packet_advances_existing_vortex_for_each_target():
    template, rotation, mechanics = _star_scene(times=(.2, .2, .9, .9))
    mechanics["elemental_model"]["target"]["initial_auras"] = [aura("cryo", 2.8, "b")]
    mechanics["target_scene"]["additional_targets"][0]["target"]["initial_auras"] = [aura("cryo", 2.8, "b")]
    output = _execute(template, rotation, mechanics)
    states = [row for row in output["mechanics_trace"] if row["type"] == "special_reaction_state"]
    advances = [row for row in states if row["operation"] == "advance_existing_vortex_from_packet_child"]
    assert [(row["t"], row["count"], row["vortex_level"]) for row in advances] == [(.9, 2, 1), (.9, 3, 2)]
    detonation, = [row for row in output["mechanics_trace"]
                   if row.get("component") == "cryo_vortex_level_2_detonation"
                   and row["type"] == "special_reaction_damage_request"]
    assert detonation["resolved_target_ids"] == [PRIMARY, SECONDARY]
    detonations = [row for row in output["result"]["hits"] if row["t"] == 3.2 and row["damage_system"] == "stellar"]
    assert {row["target_id"] for row in detonations} == {PRIMARY, SECONDARY}
    assert {row["t"] for row in detonations} == {3.2}
    assert len(detonations) == 4


def test_packet_order_decides_who_gets_a_remaining_layer():
    template, rotation, mechanics = _star_scene()
    mechanics["hit_events"][1]["order"] = 9
    next(row for row in mechanics["rules"] if row["id"] == "chorus")["quota"]["capacity"] = 1
    output = _execute(template, rotation, mechanics)
    spends = [row for row in output["mechanics_trace"]
              if row["type"] == "quota" and row["rule_id"] == "chorus" and row["t"] == .2]
    assert [(row["target_id"], row["before"], row["after"]) for row in spends] == [
        (SECONDARY, 1, 0), (PRIMARY, 0, 0),
    ]
    assert {row["target_id"]: row["zones"]["reaction_flat"]
            for row in output["result"]["hits"]
            if row["damage_system"] == "stellar" and row["character"] == "b" and row["t"] == .2} == {
        SECONDARY: 100, PRIMARY: 0,
    }


def test_repeated_contact_group_keeps_cycle_instances_unique():
    template, rotation, mechanics = _star_scene()
    rotation["actions"][0]["repeat"] = {"mode": "each_cycle"}
    mechanics["elemental_model"]["target"]["initial_auras"] = [aura("cryo", 2.8, "b")]
    mechanics["target_scene"]["additional_targets"][0]["target"]["initial_auras"] = [aura("cryo", 2.8, "b")]
    output = _execute(template, rotation, mechanics)
    creates = [row for row in output["mechanics_trace"] if row.get("operation") == "create_vortex_from_packet"]
    assert [(row["t"], row["cycle"], row["counted_children"]) for row in creates] == [
        (.2, 0, 1), (10.2, 1, 1), (20.2, 2, 1),
    ]
    assert len({row["event_id"] for row in creates}) == 3
    assert len(output["result"]["hits"]) == 10
    assert all(hit["t"] <= 3.3 for hit in output["result"]["hits"])


@pytest.mark.parametrize("ordinary_child", ["water", "bare", "miss"])
def test_same_parent_only_routes_qualifying_cryo_contacts_to_star_swirl(ordinary_child):
    template, rotation, mechanics = _star_scene()
    secondary = mechanics["target_scene"]["additional_targets"][0]["target"]
    secondary["initial_auras"] = [aura("hydro", .8, "b")] if ordinary_child == "water" else []
    if ordinary_child == "miss":
        mechanics["hit_events"][1]["application"]["landed"] = False
    output = _execute(template, rotation, mechanics)
    immediate = [row for row in output["result"]["hits"] if row["t"] == .2]
    assert {row["target_id"] for row in immediate if row["damage_system"] == "stellar"} == {PRIMARY}
    assert all(row["zones"]["reaction_flat"] == 100 for row in immediate if row["damage_system"] == "stellar")
    assert [(row["target_id"], row["hit_name"]) for row in immediate if row["damage_system"] == "transformative"] == (
        [(SECONDARY, "水扩散")] if ordinary_child == "water" else []
    )
    assert ("h1" in {row["id"] for row in immediate}) == (ordinary_child != "miss")
    contacts = {row["target_id"]: row for row in _applications(output) if row["t"] == .2}
    assert [row["type"] for row in contacts[PRIMARY]["reactions"]] == ["stellar_swirl"]
    assert [row["type"] for row in contacts[SECONDARY]["reactions"]] == (
        ["swirl"] if ordinary_child == "water" else []
    )
    assert contacts[SECONDARY]["application_allowed"] == (ordinary_child != "miss")
    used = [row for row in output["mechanics_trace"] if row["type"] == "quota" and row["t"] == .2]
    assert {row["target_id"] for row in used} == {PRIMARY}
    control = {**mechanics, "rules": [], "initial_state": {"rules": []}}
    control_hits = _execute(template, rotation, control)["result"]["hits"]
    control_ordinary = {row["id"]: row["expected"] for row in control_hits
                        if row["t"] == .2 and row["damage_system"] != "stellar"}
    assert {row["id"]: row["expected"] for row in immediate if row["damage_system"] != "stellar"} == control_ordinary


@pytest.mark.parametrize(("switch_order", "expected_frontline"), [(5, "b"), (20, "a")])
def test_same_time_switch_outside_contact_group_changes_both_targets(switch_order, expected_frontline):
    template, rotation, mechanics = _star_scene()
    rotation["abilities"].append(ability("swap", "a", "action.switch"))
    rotation["actions"].append(action(
        "same-time-swap", "swap", .2, .3, order=switch_order, switch_to="b", repeat={"mode": "once"},
    ))
    template["switches"].append({"t": .2, "character": "b"})
    first_damage = [row for row in _execute(template, rotation, mechanics)["mechanics_trace"]
                    if row["type"] == "quota" and row["t"] == .2]
    assert len(first_damage) == 4
    assert all(row["frontline"] == expected_frontline for row in first_damage)
    for pool, beneficiary in (("lead", expected_frontline), ("chorus", "b" if expected_frontline == "a" else "a")):
        assert [(row["target_id"], row["beneficiaries"]) for row in first_damage if row["rule_id"] == pool] == [
            (PRIMARY, [beneficiary]), (SECONDARY, [beneficiary]),
        ]


def test_switch_between_simultaneous_children_rejects_ambiguous_atomic_order():
    template, rotation, mechanics = _star_scene()
    mechanics["hit_events"][1]["order"] = 30
    rotation["abilities"].append(ability("swap", "a", "action.switch"))
    rotation["actions"].append(action(
        "same-time-swap", "swap", .2, .3, order=20, switch_to="b", repeat={"mode": "once"},
    ))
    template["switches"].append({"t": .2, "character": "b"})
    outcome = execute_mechanics(template, rotation, mechanics)
    assert outcome.get("result") is None
    assert any(row["code"] == "target_scene_atomic_contact_interleaved" for row in outcome["issues"])


def test_scheduled_rule_activation_cannot_interleave_two_children():
    template, rotation, mechanics = _star_scene()
    # The activation is emitted by a preceding action. It isn't in the
    # original set of authored hits and would sort between the two contacts.
    rotation["actions"][0]["order"] = 20
    mechanics["hit_events"][1]["order"] = 30
    delayed = rule("activate-between")
    delayed["timing"]["delay"] = .2
    add(mechanics, delayed)
    outcome = execute_mechanics(template, rotation, mechanics)
    assert outcome.get("result") is None
    assert any(row["code"] == "target_scene_atomic_contact_interleaved" for row in outcome["issues"])
    assert not any(row["type"] in {"hit", "reaction_damage", "quota"}
                   for row in outcome["mechanics_trace"])


def test_same_parent_rejects_duplicate_target_identity():
    template, rotation, mechanics = _star_scene()
    child = mechanics["hit_events"][1]
    child["target_id"] = child["application"]["target_id"] = PRIMARY
    outcome = execute_mechanics(template, rotation, mechanics)
    assert outcome.get("result") is None
    assert any(row["code"] == "target_scene_atomic_contact_invalid" for row in outcome["issues"])


def test_atomic_parent_rollback_on_second_targets_immediate_damage_failure(monkeypatch):
    template, rotation, model = _star_scene()
    original_hit = _Mechanics.hit
    original_init = _Mechanics.__init__
    live_runtimes = []
    visited = []

    def observe_runtime(runtime, *args, **kwargs):
        original_init(runtime, *args, **kwargs)
        live_runtimes.append(runtime)

    def fail_second_target(runtime, event, payload):
        target = event.get("target_id") or payload["hit"].get("extensions", {}).get("elemental", {}).get("target_id")
        if event.get("reaction_type") == "stellar_swirl_anemo":
            visited.append(target)
            if target == SECONDARY:
                raise ElementalPending("second_target_damage_test", "controlled late failure", event["id"])
        return original_hit(runtime, event, payload)

    with monkeypatch.context() as scoped:
        scoped.setattr(_Mechanics, "__init__", observe_runtime)
        scoped.setattr(_Mechanics, "hit", fail_second_target)
        failed = execute_mechanics(template, rotation, model)
    assert failed.get("result") is None and failed["status"] == "pending"
    assert any(row["code"] == "second_target_damage_test" for row in failed["issues"])
    assert visited == [PRIMARY, PRIMARY, SECONDARY]
    runtime = live_runtimes[0]
    assert {pool: runtime.state[pool]["instances"][0]["quota"]["team"] for pool in ("lead", "chorus")} == {
        "lead": 4, "chorus": 3,
    }
    assert runtime.hit_rows == [] and runtime.committed_component_ids == set()
    assert not any(row["type"] in {"hit", "reaction_damage", "quota", "elemental_application"}
                   for row in failed["mechanics_trace"])
    world = runtime.target_scene.world.state()
    assert world["generated_event_cursor"] == 0
    assert all(world["targets"][target]["auras"][0]["element"] == "cryo" for target in (PRIMARY, SECONDARY))
    assert sum(row["damage_system"] == "stellar" for row in _execute(template, rotation, model)["result"]["hits"]) == 8


def test_failed_atomic_parent_does_not_leave_a_spawn_in_the_rotation_event_map(monkeypatch):
    template, rotation, model = _star_scene()
    add(model, rule("h0-spawn", trigger={"event": "hit", "phase": "post", "event_ids": ["h0"]}, resources=[{
        "id": "particles", "kind": "particles", "delay": 0, "travel_time": .05,
        "count": 2, "particle_type": "particle", "element": "cryo", "source_part": "particles",
    }]))
    model["resource_ownership"] = [{
        "rule_id": "h0-spawn", "action_id": "cast", "source_part": "particles", "replaces_event_ids": [],
    }]
    original_hit = _Mechanics.hit
    original_init = _Mechanics.__init__
    observed = {}
    spawn_id = "h0~h0-spawn:particles:spawn"

    def observe_runtime(runtime, *args, **kwargs):
        original_init(runtime, *args, **kwargs)
        observed.update(runtime=runtime, events=runtime.data["events"])

    def fail_second_target(runtime, event, payload):
        target = event.get("target_id") or payload["hit"].get("extensions", {}).get("elemental", {}).get("target_id")
        if event.get("reaction_type") == "stellar_swirl_anemo" and target == SECONDARY:
            assert spawn_id in runtime.data["events"]
            raise ElementalPending("test_abort_particle_spawn", "controlled late failure", event["id"])
        return original_hit(runtime, event, payload)

    with monkeypatch.context() as scoped:
        scoped.setattr(_Mechanics, "__init__", observe_runtime)
        scoped.setattr(_Mechanics, "hit", fail_second_target)
        failure = execute_mechanics(template, rotation, model)
    assert failure["status"] == "pending" and failure["result"] is None
    assert any(row["code"] == "test_abort_particle_spawn" for row in failure["issues"])
    assert spawn_id not in observed["events"]
    assert observed["runtime"].data["events"] is observed["events"]
    assert not any(row["type"] in {"energy_spawn", "energy_credit"}
                   for row in failure["rotation_check"]["event_log"])
    retried = _execute(template, rotation, model)
    assert [row["type"] for row in retried["rotation_check"]["event_log"] if row["id"].startswith("h0~")] == [
        "energy_spawn", "energy_credit",
    ]


def test_delayed_star_swirl_reports_unsupported_generated_reaction_callback():
    template, rotation, mechanics = _static_scene(
        "anemo", primary_auras=[aura("cryo", .8, "b")],
        secondary_auras=[aura("hydro", .8, "b")], times=(.2,), end=3.3,
    )
    second_character(template, rotation, "b")
    mechanics["target_scene"]["additional_targets"][0]["target"]["freeze"] = elemental_model()["target"]["freeze"]
    _enable(mechanics, "stellar_swirl")
    baseline = _execute(template, rotation, mechanics)
    freeze = next(row for row in _applications(baseline) if row["target_id"] == SECONDARY)
    assert [reaction["type"] for reaction in freeze["reactions"]] == ["freeze"]
    add(mechanics, rule(trigger={"event": "elemental_reaction", "phase": "post"}))
    output = execute_mechanics(template, rotation, mechanics)
    assert output["status"] == "pending" and output.get("result") is None
    issue, = [row for row in output["issues"] if row["code"] == "target_scene_generated_reaction_callback_pending"]
    assert issue["target_id"] == SECONDARY and issue["reaction_type"] == "freeze"


def test_direct_star_swirl_shares_pool_but_does_not_apply_to_other_targets():
    template, rotation, model = _static_scene("electro", times=(.2,))
    rotation["abilities"][0].update(variant="tap", effective_level=10)
    _enable(model, "stellar_swirl")
    add(model, _quill("lead", "current_frontline", 1))
    first = direct_fixture()[2]["special_reaction_events"][0]
    second = {**copy.deepcopy(first), "id": "direct-secondary", "target_id": SECONDARY, "order": first["order"] + 1}
    model["special_reaction_events"] = [first, second]
    output = _execute(template, rotation, model)
    rows = [row for row in output["result"]["hits"] if row["damage_system"] == "stellar"]
    assert [(row["target_id"], row["zones"]["reaction_flat"]) for row in rows] == [(PRIMARY, 100), (SECONDARY, 0)]
    quota = [row for row in output["mechanics_trace"] if row["type"] == "quota"]
    assert [(row["target_id"], row["before"], row["after"], row["reason"]) for row in quota] == [
        (PRIMARY, 1, 0, "consumed"), (SECONDARY, 0, 0, "empty"),
    ]
    assert all(row["target_id"] == PRIMARY for row in _applications(output))


def test_target_scene_native_roundtrip_can_reverify_without_database(tmp_path):
    from genshin_app.equipment.delivery import validate_delivery_calculated
    from genshin_app.equipment.files import json_bytes, read_plan
    from genshin_app.equipment.service import run_plan_calc

    template, rotation, model = _static_scene("pyro", primary_auras=[aura("cryo", .8, "a")])
    template["characters"][0]["stats"]["energy_recharge"] = 1
    plan = native()
    plan.update(manual_template=template, rotation_model=rotation, mechanics_model=model, effect_bindings=[])
    file = tmp_path / "target-scene.plan.json"
    file.write_bytes(json_bytes(plan))
    db = tmp_path / "unused.db"
    reopened = read_plan(file)["plan"]
    assert reopened["mechanics_model"]["target_scene"] == model["target_scene"]
    calculated = run_plan_calc(reopened, db, capture_display=True)
    assert [(h["target_id"], h["expected"]) for h in calculated["result"]["hits"]] == [
        (PRIMARY, 90), (SECONDARY, 45),
    ]
    validate_delivery_calculated(json.loads(json_bytes(calculated)))
    assert not db.exists()


def _bloom_runtime():
    _, _, model = _static_scene("dendro", primary_auras=[aura("hydro", .8, "a")], times=(.2,))
    model["elemental_model"]["profiles"].append({
        "id": "dendro", "element": "dendro", "application": "apply", "gauge_units": 1,
        "icd": {"kind": "timer_counter", "counter_tag": "bloom", "rule_group": "three-hits-or-timer",
                "hit_interval": 3, "reset_seconds": 2.5}, **EVIDENCE,
    })
    scene = TargetSceneRuntime(model, stellar_runtime())
    scene.advance(200_000)
    args = {
        "event_id": "0:contact", "stable_event_id": "contact", "action_id": "cast", "actor": "a",
        "time": 200_000, "application": model["hit_events"][0]["application"], "damage_element": "dendro",
    }
    return scene, args


def test_local_contact_rolls_back_core_aura_and_icd_if_publication_fails(monkeypatch):
    scene, args = _bloom_runtime()
    before = scene.world.state()
    traces = [copy.deepcopy(runtime.trace) for runtime in scene.targets.values()]

    def fail_publication(events):
        assert [row["type"] for row in events] == ["ordinary_bloom_core_created"]
        assert scene.primary.icd  # the local attempt has already mutated the counter
        assert scene.world.bloom_core_state.state()["cores"]
        raise ElementalWorldPending("fixture_publish", "controlled publication failure")

    with monkeypatch.context() as scoped:
        scoped.setattr(scene.world, "publish_generated_events", fail_publication)
        with pytest.raises(ElementalPending, match="controlled publication failure"):
            scene.attempt(args, "contact", 0)

    assert scene.world.state() == before
    assert [runtime.trace for runtime in scene.targets.values()] == traces
    assert not scene.primary.icd and not scene.primary.counter_bindings
    assert scene.trace == [] and scene.drain_generated_events() == []
    receipt = scene.attempt(args, "contact", 0)
    assert receipt["application_allowed"] and receipt["reactions"][0]["type"] == "bloom"
    assert [event["type"] for event in scene.drain_generated_events()] == ["ordinary_bloom_core_created"]


def test_advance_rolls_back_all_targets_and_ledger_on_second_target_failure(monkeypatch):
    scene, args = _bloom_runtime()
    scene.attempt(args, "contact", 0)
    assert len(scene.drain_generated_events()) == 1
    before = scene.world.state()
    traces = [copy.deepcopy(runtime.trace) for runtime in scene.targets.values()]
    second = scene.targets[SECONDARY]
    original = second.advance

    def fail_second_advance(time):
        assert scene.primary.time == time
        assert not scene.world.bloom_core_state.state()["cores"]
        original(time)
        raise ElementalWorldPending("fixture_advance", "controlled second target failure")

    with monkeypatch.context() as scoped:
        scoped.setattr(second, "advance", fail_second_advance)
        with pytest.raises(ElementalPending, match="controlled second target failure"):
            scene.advance(6_200_000)

    assert scene.world.state() == before
    assert [runtime.trace for runtime in scene.targets.values()] == traces
    assert {runtime.time for runtime in scene.targets.values()} == {200_000}
    assert scene.world.time == scene.world.special_runtime.time == 200_000
    assert scene.drain_generated_events() == []
    scene.advance(6_200_000)
    event, = scene.drain_generated_events()
    assert (event["type"], event["target_id"], event["t"]) == ("ordinary_bloom_damage_request", PRIMARY, 6.2)
