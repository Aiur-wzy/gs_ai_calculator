"""Independent EC18 D20 primitive checks; these do not certify source compilation."""
from __future__ import annotations

import copy
from decimal import Decimal

import pytest
from test_freeze_review import SAFE_CONTACT, fresh_model
from test_mechanics_execution import (
    action,
    add,
    condition,
    fixture,
    rule,
    second_character,
)

from genshin_engine.mechanics import execute_mechanics

FACTOR = Decimal("0.8218079805374146")
ONCE = {"mode": "once"}


def quill_fixture(times):
    template, rotation, model = fixture(times)
    rotation["max_cycles"] = 3
    rotation["actions"][0]["repeat"] = dict(ONCE)
    template["characters"][0]["stats"]["atk"] = 1000
    selector = {"event": "hit", "phase": "pre", "elements": ["cryo"],
                "damage_type_ids": ["damage.elemental_skill", "damage.elemental_burst"]}
    benefit = rule("limited", "benefit.base_add.all", {
        "kind": "stat", "subject": "owner", "stat": "atk",
        "factor": float(FACTOR), "sample": "current"}, domain="provider", duration=15,
        quota={"capacity": 7, "scope": "per_beneficiary", "refresh": "reset",
               "consume": selector})
    benefit["effects"][0]["filter"] = copy.deepcopy(selector)
    add(model, benefit)
    return template, rotation, model


def calculate(template, rotation, model, matches=None):
    out = execute_mechanics(template, rotation, model, matches)
    assert out["status"] in ("passed", "unproven"), out["issues"]
    assert out["result"] is not None
    return out


def expected(provider_atk, recipient_atk=1000):
    return float((Decimal(recipient_atk) + Decimal(provider_atk) * FACTOR) * Decimal(".45"))


def rows(out, kind):
    return [row for row in out["mechanics_trace"] if row["type"] == kind
            and row.get("rule_id") == "limited"]


@pytest.mark.parametrize("hit_order,first_bonus", [(-1, False), (0, False), (1, True)])
def test_review_delayed_activation_uses_real_causal_key(hit_order, first_bonus):
    template, rotation, model = quill_fixture((.2, .3))
    model["rules"][0]["timing"]["delay"] = .2
    model["hit_events"][0]["order"] = hit_order
    out = calculate(template, rotation, model)
    assert [h["expected"] for h in out["result"]["hits"]] == pytest.approx(
        [expected(1000) if first_bonus else 450, expected(1000)])
    assert rows(out, "activation")[0]["t"] == .2
    assert len(rows(out, "quota")) == (2 if first_bonus else 1)


def test_review_report_and_exclude_consume_but_include_cannot_restore_eighth():
    template, rotation, model = quill_fixture(tuple(round(.2 + i * .1, 6) for i in range(8)))
    model["hit_events"][0]["report"] = False
    matches = [{"rule_id": "limited", "effect_id": "value", "hit_id": "h1", "operation": "exclude"},
               {"rule_id": "limited", "effect_id": "value", "hit_id": "h7", "operation": "include"}]
    out = calculate(template, rotation, model, matches)
    assert [r["after"] for r in rows(out, "quota")] == [6, 5, 4, 3, 2, 1, 0, 0]
    assert [h["expected"] for h in out["result"]["hits"]] == pytest.approx(
        [450, *([expected(1000)] * 5), 450])


def test_review_refresh_reuses_instance_but_resets_from_activation_ledger():
    template, rotation, model = quill_fixture((.2, .3, .6, .7))
    rotation["actions"].append(action("recast", "e", .5, .55, repeat=dict(ONCE)))
    model["rules"][0]["trigger"]["action_ids"].append("recast")
    for descriptor in model["hit_events"][2:]:
        descriptor["action_id"] = "recast"
    out = calculate(template, rotation, model)
    activations = rows(out, "activation")
    assert [row["event_id"] for row in activations] == ["cast", "recast"]
    assert [row["quota"] for row in activations] == [{"a": 7}, {"a": 7}]
    assert activations[0]["instance_id"] == activations[1]["instance_id"]
    assert [row["after"] for row in rows(out, "quota")] == [6, 5, 6, 5]
    assert [row["expires_at"] for row in activations] == [[15], [15.5]]


def test_review_provider_current_is_not_recipient_snapshot_or_activation_sample():
    template, rotation, model = quill_fixture((.2, 1.2))
    second_character(template, rotation)
    template["characters"][1]["stats"]["atk"] = 2000
    model["rules"][0]["owner"] = "b"
    for descriptor in model["hit_events"]:
        descriptor["self_snapshot_event"] = "cast"
    condition(model, "change", 1)
    model["condition_events"][0]["repeat"] = dict(ONCE)
    trigger = {"event": "condition", "phase": "pre", "event_ids": ["change"]}
    add(model,
        rule("provider_panel", "benefit.stat_add.atk", 500, trigger=trigger,
             beneficiaries={"mode": "characters", "characters": ["b"], "scope": "locked"}),
        rule("recipient_panel", "benefit.stat_add.atk", 900, trigger=trigger,
             beneficiaries={"mode": "characters", "characters": ["a"], "scope": "locked"}))
    out = calculate(template, rotation, model)
    assert [h["expected"] for h in out["result"]["hits"]] == pytest.approx(
        [1189.62718248367314, 1374.533978104591425])
    samples = [row for row in out["sampling_trace"] if row["rule_id"] == "limited"]
    assert [row["source_value"] for row in samples] == [2000, 2500]
    assert [row["sample_t"] for row in samples] == [.2, 1.2]
    assert samples[1]["dependencies"][0]["rule_id"] == "provider_panel"


def test_review_half_open_expiry_uses_actual_delayed_activation():
    template, rotation, model = quill_fixture((15.199999, 15.2))
    template["loop_duration"] = rotation["period"] = 16
    model["rules"][0]["timing"]["delay"] = .2
    out = calculate(template, rotation, model)
    assert [h["expected"] for h in out["result"]["hits"]] == pytest.approx([expected(1000), 450])
    assert rows(out, "activation")[0]["expires_at"] == [15.2]
    assert [row["after"] for row in rows(out, "quota")] == [6]


def test_review_missed_initial_hit_keeps_cast_activation_and_blocked_icd_consumes():
    template, rotation, model = quill_fixture((.2, .3, .4))
    model["elemental_model"] = fresh_model()
    profile = next(p for p in model["elemental_model"]["profiles"] if p["id"] == "cryo1")
    profile["icd"] = {"kind": "sequence", "group": "review", "reset_seconds": 2.5,
                      "sequence": [1, 0, 0]}
    for index, descriptor in enumerate(model["hit_events"]):
        descriptor["application"] = {"profile_id": "cryo1", "target_id": "enemy", "landed": index != 0}
        descriptor["contact"] = copy.deepcopy(SAFE_CONTACT)
    out = calculate(template, rotation, model)
    assert len(rows(out, "activation")) == 1
    assert [row["event_id"] for row in rows(out, "quota")] == ["h1", "h2"]
    assert [row["after"] for row in rows(out, "quota")] == [6, 5]
    assert [h["expected"] for h in out["result"]["hits"]] == pytest.approx([expected(1000)] * 2)


def test_review_failed_action_admission_does_not_activate_or_reset_pool():
    template, rotation, model = quill_fixture((.2,))
    rotation["abilities"][0]["cooldown"] = {
        "group": "skill", "duration": 10, "charges": 1, "start": "action_start", "recovery": "parallel"}
    rotation["initial_cooldowns"] = [{"character": "a", "group": "skill", "ready_in": [1]}]
    out = execute_mechanics(template, rotation, model)
    assert out["status"] == "failed", out["issues"]
    assert rows(out, "activation") == []
    assert rows(out, "quota") == []
