"""Actual reaction listeners must have a dispatched phase and event producer."""
from __future__ import annotations

import copy

import pytest
from test_elemental_execution import setup as basic_setup
from test_freeze_runtime import inputs as freeze_setup
from test_mechanics_execution import add, rule
from test_mechanics_swirl import setup as swirl_setup
from test_shared_pools import config
from test_swirl_dynamic_v3 import setup as dynamic_setup

from genshin_engine.mechanics import execute_mechanics, validate_mechanics_model
from genshin_engine.mechanics_schema import mechanics_capabilities


def with_pool(values, *, event="elemental_reaction", phase="post", version=1):
    template, rotation, model = values
    pools = config()
    pools["pools"][0]["initial"] = {"team": 0}
    pools["consumers"][0]["actor_relation"] = "party_member"
    pools["operations"][0]["trigger"] = {"event": event, "phase": phase}
    if version == 2:
        pools["schema_version"] = 2
        pools["pools"][0]["unit_scale"] = 1
    add(model, rule("benefit", benefit="benefit.base_add.all", value=100, source_id="binding:benefit",
                    resource_gate={"consumer_id": "consumer"}))
    model["shared_pools"] = pools
    return template, rotation, model


def setup(*, event="elemental_reaction", phase="post", has_model=True, version=1):
    template, rotation, model = with_pool(dynamic_setup((.2, .3, .4)), event=event, phase=phase, version=version)
    if not has_model:
        del model["elemental_model"]
        for descriptor in model["hit_events"]:
            del descriptor["application"]
        for hit in template["hits"]:
            hit.update(element="cryo", reaction={"type": "amplify", "factor": 2})
    return template, rotation, model


def execute(values):
    original = copy.deepcopy(values)
    result = execute_mechanics(*values)
    assert values == original
    return result


def operations(result):
    return [row for row in result.get("shared_pool_trace", []) if row["type"] == "operation" and row["cycle"] == 0]


@pytest.mark.parametrize("version", [1, 2])
@pytest.mark.parametrize("event,phase,has_model,code", [
    ("elemental_reaction", "pre", True, "pool_reaction_phase_unsupported"),
    ("elemental_reaction", "post", False, "pool_reaction_event_unavailable"),
    ("reaction_damage", "pre", True, "pool_reaction_phase_unsupported"),
    ("reaction_damage", "post", False, "pool_reaction_event_unavailable"),
])
def test_unsupported_listener_cannot_silently_publish_damage(version, event, phase, has_model, code):
    values = setup(event=event, phase=phase, has_model=has_model, version=version)
    output = execute(values)
    assert output["result"] is None
    assert output["status"] in ("invalid", "pending")
    issues = [item for item in output["issues"] if item["code"] == code]
    assert len(issues) == 1
    assert issues[0]["path"] == "shared_pools.operations.refill.trigger"
    assert code in [item["code"] for item in validate_mechanics_model(values[2])["issues"]]
    assert not operations(output)


@pytest.mark.parametrize("event,expected_count", [("elemental_reaction", 3), ("reaction_damage", 2)])
def test_supported_post_uses_actual_reaction_or_allowed_damage_occurrences(event, expected_count):
    output = execute(setup(event=event))
    assert output["result"] is not None and not output["issues"]
    rows = operations(output)
    assert len(rows) == expected_count
    assert [row["accepted"] for row in rows] == [3] * expected_count
    assert len({row["run_id"] for row in rows}) == expected_count
    assert [row["expected"] for row in output["result"]["hits"] if row["damage_system"] == "classic"] == [45, 90, 90]


def test_complete_model_with_no_actual_reaction_is_valid_zero_income():
    values = setup()
    values[2]["elemental_model"]["target"]["initial_aura"] = None
    output = execute(values)
    assert output["result"] is not None and not output["issues"]
    assert operations(output) == []
    assert [row["expected"] for row in output["result"]["hits"]] == [45, 45, 45]


def test_pre_damage_effect_filter_is_not_a_pre_listener():
    values = setup()
    del values[2]["shared_pools"]
    benefit = values[2]["rules"][0]
    del benefit["resource_gate"]
    benefit["effects"][0]["filter"] = {"event": "reaction_damage", "phase": "pre"}
    output = execute(values)
    assert output["result"] is not None and not output["issues"]


@pytest.mark.parametrize("kind,expected_count", [("basic", 1), ("swirl-v2", 3), ("freeze", 1), ("freeze-damage", 1)])
def test_legacy_models_keep_their_supported_callbacks(kind, expected_count):
    if kind == "basic":
        values = basic_setup(("cryo", "pyro"), (.2, .3))
        values[1]["actions"][0]["repeat"] = {"mode": "once"}
    elif kind == "swirl-v2":
        values = swirl_setup((.2, .3, .4))
    else:
        values = freeze_setup()
    values = with_pool(values, event="reaction_damage" if kind == "freeze-damage" else "elemental_reaction")
    if kind == "freeze":
        values[2]["shared_pools"]["operations"][0]["trigger"]["reaction_types"] = ["freeze"]
    output = execute(values)
    assert output["result"] is not None and not output["issues"]
    assert len(operations(output)) == expected_count


@pytest.mark.parametrize("event,phase,has_model,message", [
    ("elemental_reaction", "pre", True, "自动反应触发需要元素模拟，且只支持反应后置相位。"),
    ("elemental_reaction", "post", False, "自动反应触发需要元素模拟，且只支持反应后置相位。"),
    ("reaction_damage", "pre", True, "反应伤害触发仅支持post相位。"),
    ("reaction_damage", "post", False, "reaction_damage需要普通扩散v2。"),
])
def test_existing_rule_diagnostics_keep_exact_shape(event, phase, has_model, message):
    values = setup(event=event, phase=phase, has_model=has_model)
    del values[2]["shared_pools"]
    benefit = values[2]["rules"][0]
    del benefit["resource_gate"]
    benefit["trigger"] = {"event": event, "phase": phase}
    output = execute(values)
    assert output["result"] is None and output["status"] == "invalid"
    assert output["issues"] == [{"code": "unsupported", "message": message,
                                 "path": "mechanics_model.rules.benefit", "severity": "error"}]


def test_declared_amplify_without_automatic_listener_remains_supported():
    values = setup(has_model=False)
    del values[2]["shared_pools"]
    del values[2]["rules"][0]["resource_gate"]
    output = execute(values)
    assert output["result"] is not None and not output["issues"]
    assert [row["expected"] for row in output["result"]["hits"]] == [180, 180, 180]


def test_self_description_distinguishes_listeners_from_effect_filters():
    capabilities = mechanics_capabilities()["reaction_event_listeners"]
    assert capabilities["applies_to"] == ["mechanics_model.rules[*].trigger", "mechanics_model.shared_pools.operations[*].trigger"]
    assert all(row["phases"] == ["post"] for row in capabilities["events"].values())
    assert "gauge-basic-v1" in capabilities["events"]["elemental_reaction"]["rulesets"]
    assert "gauge-basic-v1" not in capabilities["events"]["reaction_damage"]["rulesets"]
    assert "gauge-freeze-reference-v1" in capabilities["events"]["reaction_damage"]["rulesets"]
    assert "reaction_damage/pre" in capabilities["effect_filter"]
    capabilities["events"]["reaction_damage"]["rulesets"].clear()
    assert mechanics_capabilities()["reaction_event_listeners"]["events"]["reaction_damage"]["rulesets"]
