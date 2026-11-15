"""Generic add-and-refresh queue semantics, without equipment/source approval."""
from __future__ import annotations

import copy

import pytest

from genshin_engine.mechanics import execute_mechanics, validate_mechanics_model
from genshin_engine.mechanics_schema import mechanics_capabilities
from tests.test_mechanics_execution import add, condition, damages, fixture, rule


def layers_rule(*, phase="pre", events=None, duration=1, maximum=2, increment=1, **kwargs):
    trigger = {"event": "hit", "phase": phase}
    if events is not None:
        trigger["event_ids"] = events
    return rule("generic_layers", trigger=trigger, duration=duration,
                stacking={"max": maximum, "add": increment, "policy": "add_and_refresh"}, **kwargs)


def execute(t, r, m):
    before = copy.deepcopy((t, r, m))
    out = execute_mechanics(t, r, m)
    assert out["status"] == "passed", out["issues"]
    assert (t, r, m) == before
    return out


def activations(out):
    return [row for row in out["mechanics_trace"]
            if row["type"] == "activation" and row["rule_id"] == "generic_layers" and row["cycle"] == 0]


def test_growth_cap_refresh_and_half_open_expiry_restart():
    t, r, m = fixture((.2, .4, .6, 1.2, 1.599999, 1.6, 1.8, 2.8))
    add(m, layers_rule(events=["h0", "h1", "h2", "h6"]))
    out = execute(t, r, m)
    assert damages(out) == pytest.approx([90, 135, 135, 135, 135, 45, 90, 45])
    rows = activations(out)
    assert [x["layers"] for x in rows] == [1, 2, 2, 1]
    assert [x["expires_at"] for x in rows] == [[1.2], [1.4, 1.4], [1.6, 1.6], [2.8]]
    assert rows[0]["instance_id"] == rows[1]["instance_id"] == rows[2]["instance_id"]
    assert rows[3]["instance_id"] != rows[2]["instance_id"]


def test_exact_expiry_trigger_starts_at_one_layer():
    t, r, m = fixture((.2, .4, 1.4, 1.5))
    add(m, layers_rule(events=["h0", "h1", "h2"]))
    out = execute(t, r, m)
    assert damages(out) == pytest.approx([90, 135, 90, 90])
    assert [x["layers"] for x in activations(out)] == [1, 2, 1]
    at_boundary = [x for x in out["mechanics_trace"] if x.get("t") == 1.4 and x["type"] in ("expiry", "activation")]
    assert [x["type"] for x in at_boundary] == ["expiry", "activation"]


def test_increment_greater_than_one_clamps_and_refreshes_at_cap():
    t, r, m = fixture((.2, .4, .6, 1.599999, 1.6))
    add(m, layers_rule(events=["h0", "h1", "h2"], maximum=3, increment=2))
    out = execute(t, r, m)
    assert damages(out) == pytest.approx([135, 180, 180, 180, 45])
    assert [x["layers"] for x in activations(out)] == [2, 3, 3]
    assert activations(out)[-1]["expires_at"] == [1.6, 1.6, 1.6]


@pytest.mark.parametrize("anchor", ["success", "attempt"])
def test_internal_cooldown_rejection_neither_adds_nor_refreshes(anchor):
    t, r, m = fixture((.2, .399999, .4, .599999, .6, 1.6))
    stack = layers_rule(events=["h0", "h1", "h2", "h3", "h4"])
    stack["timing"].update(cooldown=.2, cooldown_anchor=anchor)
    add(m, stack)
    out = execute(t, r, m)
    assert damages(out) == pytest.approx([90, 90, 135, 135, 135, 45])
    assert [x["t"] for x in activations(out)] == [.2, .4, .6]
    assert activations(out)[-1]["expires_at"] == [1.6, 1.6]
    rejected = [x for x in out["mechanics_trace"] if x["type"] == "trigger_rejected" and x["t"] < 10]
    assert [(x["event_id"], x["reason"]) for x in rejected] == [("h1", "internal_cooldown"), ("h3", "internal_cooldown")]


def test_same_tick_post_uses_previous_layers_for_parent_hit():
    t, r, m = fixture((.2, .2, .2, .4))
    add(m, layers_rule(phase="post", events=["h0", "h1", "h2"]))
    out = execute(t, r, m)
    assert damages(out) == pytest.approx([45, 90, 135, 135])
    assert [x["layers"] for x in activations(out)] == [1, 2, 2]
    assert [x["event_id"] for x in activations(out)] == ["h0", "h1", "h2"]


@pytest.mark.parametrize(("sample", "expected"), [("trigger", [54, 72, 72]), ("current", [54, 72, 90])])
def test_non_attack_property_sampling_refreshes_all_layer_samples(sample, expected):
    t, r, m = fixture((.2, .4, .6))
    t["characters"][0]["stats"]["def"] = 40
    condition(m, "first_change", .3)
    condition(m, "second_change", .5)
    add(m,
        rule("def_first", "benefit.stat_add.def", 20,
             trigger={"event": "condition", "phase": "pre", "event_ids": ["first_change"]}),
        rule("def_second", "benefit.stat_add.def", 40,
             trigger={"event": "condition", "phase": "pre", "event_ids": ["second_change"]}),
        layers_rule(events=["h0", "h1"], benefit="benefit.base_add.all", domain="provider",
                    value={"kind": "stat", "subject": "owner", "stat": "def", "factor": .5, "sample": sample}))
    out = execute(t, r, m)
    assert damages(out) == pytest.approx(expected)
    samples = [x for x in out["sampling_trace"] if x["rule_id"] == "generic_layers" and x["cycle"] == 0]
    assert [x["source_value"] for x in samples] == ([40, 60, 60, 60, 60] if sample == "trigger" else [40, 60, 100])


def test_delayed_activation_refresh_uses_trigger_panel_not_activation_panel():
    t, r, m = fixture((.25, .4, .65, .8))
    t["characters"][0]["stats"]["def"] = 40
    for ident, time in (("first_trigger", .1), ("first_change", .2), ("second_trigger", .5), ("second_change", .6)):
        condition(m, ident, time)
    stack = layers_rule(benefit="benefit.base_add.all", domain="provider",
                        value={"kind": "stat", "subject": "owner", "stat": "def", "factor": .5, "sample": "trigger"})
    stack["trigger"] = {"event": "condition", "phase": "pre", "event_ids": ["first_trigger", "second_trigger"]}
    stack["timing"]["delay"] = .2
    add(m,
        rule("def_first", "benefit.stat_add.def", 20,
             trigger={"event": "condition", "phase": "pre", "event_ids": ["first_change"]}),
        rule("def_second", "benefit.stat_add.def", 40,
             trigger={"event": "condition", "phase": "pre", "event_ids": ["second_change"]}), stack)
    out = execute(t, r, m)
    assert damages(out) == pytest.approx([45, 54, 54, 72])
    assert [x["t"] for x in activations(out)] == [.3, .7]
    assert [x["expires_at"] for x in activations(out)] == [[1.3], [1.7, 1.7]]
    samples = [x for x in out["sampling_trace"] if x["rule_id"] == "generic_layers" and x["cycle"] == 0]
    assert [x["source_value"] for x in samples] == [40, 40, 60, 60]


def test_cap_refresh_replaces_every_layer_trigger_sample():
    t, r, m = fixture((.2, .4, .6))
    t["characters"][0]["stats"]["def"] = 40
    condition(m, "new_panel", .5)
    add(m,
        rule("def_change", "benefit.stat_add.def", 60,
             trigger={"event": "condition", "phase": "pre", "event_ids": ["new_panel"]}),
        layers_rule(benefit="benefit.base_add.all", domain="provider",
                    value={"kind": "stat", "subject": "owner", "stat": "def", "factor": .5, "sample": "trigger"}))
    out = execute(t, r, m)
    assert damages(out) == pytest.approx([54, 63, 90])
    assert [x["layers"] for x in activations(out)] == [1, 2, 2]
    samples = [x for x in out["sampling_trace"] if x["rule_id"] == "generic_layers" and x["cycle"] == 0]
    assert [x["source_value"] for x in samples] == [40, 40, 40, 100, 100]


def test_initial_expired_layers_are_not_counted_on_refresh():
    t, r, m = fixture((.2, .799999, .8))
    add(m, layers_rule(events=["h0"], duration=.6))
    m["initial_state"]["rules"][0]["instances"] = [{"beneficiaries": ["a"], "remaining": [.1, .5]}]
    out = execute(t, r, m)
    assert damages(out) == pytest.approx([135, 135, 45])
    assert activations(out)[0]["expires_at"] == [.8, .8]


@pytest.mark.parametrize(("quota_policy", "expected", "before"), [
    ("reset", [90, 135, 135], [2, 2, 1]),
    ("add", [90, 135, 135], [2, 2, 1]),
    ("keep", [90, 135, 45], [2, 1, 0]),
])
def test_quota_refresh_remains_an_independent_declared_policy(quota_policy, expected, before):
    t, r, m = fixture()
    add(m, layers_rule(events=["h0", "h1"], quota={"capacity": 2, "scope": "team", "refresh": quota_policy,
                                                "consume": {"event": "hit", "phase": "pre"}}))
    out = execute(t, r, m)
    assert damages(out) == pytest.approx(expected)
    rows = [x for x in out["mechanics_trace"] if x["type"] == "quota" and x["cycle"] == 0]
    assert [x["before"] for x in rows] == before


@pytest.mark.parametrize("remaining", [[], [.5, .5, .5]])
def test_invalid_initial_layer_count_remains_rejected(remaining):
    _, _, m = fixture()
    add(m, layers_rule())
    m["initial_state"]["rules"][0]["instances"] = [{"beneficiaries": ["a"], "remaining": remaining}]
    out = validate_mechanics_model(m)
    assert out["status"] == "invalid"
    assert any(x["code"] == "initial_state" for x in out["issues"])


def test_initial_trigger_snapshot_is_not_invented():
    _, _, m = fixture()
    add(m, layers_rule(benefit="benefit.base_add.all", domain="provider",
                      value={"kind": "stat", "subject": "owner", "stat": "def", "factor": .5, "sample": "trigger"}))
    m["initial_state"]["rules"][0]["instances"] = [{"beneficiaries": ["a"], "remaining": [.5]}]
    out = validate_mechanics_model(m)
    assert out["status"] == "invalid"
    assert any(x["code"] == "unsupported" for x in out["issues"])


@pytest.mark.parametrize("quota", [{}, {"team": 3}])
def test_initial_quota_is_still_required_and_bounded(quota):
    _, _, m = fixture()
    add(m, layers_rule(quota={"capacity": 2, "scope": "team", "refresh": "reset",
                             "consume": {"event": "hit", "phase": "pre"}}))
    m["initial_state"]["rules"][0]["instances"] = [{"beneficiaries": ["a"], "remaining": [.5], "quota": quota}]
    assert validate_mechanics_model(m)["status"] == "invalid"


@pytest.mark.parametrize(("policy", "expected"), [
    ("refresh", [90, 90, 90]), ("independent", [90, 135, 90]), ("add_and_refresh", [90, 135, 135]),
])
def test_old_policies_remain_distinct(policy, expected):
    t, r, m = fixture((.2, .4, 1.2))
    stack = layers_rule(events=["h0", "h1"])
    stack["stacking"]["policy"] = policy
    add(m, stack)
    assert damages(execute(t, r, m)) == pytest.approx(expected)


def test_capabilities_expose_new_policy_and_reject_increment_above_max():
    policies = mechanics_capabilities()["schema"]["properties"]["rules"]["items"]["properties"]["stacking"]["properties"]["policy"]["enum"]
    assert "add_and_refresh" in policies
    _, _, m = fixture()
    add(m, layers_rule(maximum=2, increment=3))
    assert validate_mechanics_model(m)["status"] == "invalid"
