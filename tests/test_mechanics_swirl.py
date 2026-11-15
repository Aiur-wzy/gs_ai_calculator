"""Ordinary swirl's numerical projection and causal scheduling contract."""
from __future__ import annotations

import copy

import pytest
from test_elemental_swirl import swirl_model
from test_mechanics_execution import (
    ability,
    action,
    add,
    fixture,
    rule,
    second_character,
)

from genshin_engine.mechanics import execute_mechanics


def setup(times=(.2,)):
    t, r, m = fixture(times)
    r["actions"][0]["repeat"] = {"mode": "once"}
    r["max_cycles"] = 3
    r["characters"][0]["element"] = "anemo"
    m["elemental_model"] = swirl_model()
    for hit, descriptor in zip(t["hits"], m["hit_events"], strict=True):
        hit["element"] = "anemo"
        descriptor["application"] = {"profile_id": "anemo", "target_id": "enemy", "landed": True}
    return t, r, m


def swirls(out):
    assert out["result"] is not None, out["issues"]
    return [row for row in out["result"]["hits"] if row["damage_system"] == "transformative"]


def test_hand_damage_and_source_identity_are_explicit_and_input_is_unchanged():
    values = setup()
    original = copy.deepcopy(values)
    out = execute_mechanics(*values)
    assert swirls(out)[0]["expected"] == pytest.approx(781.299)
    assert values == original
    hit = out["resolved_template"]["hits"][1]
    assert hit["id"] == "h0:reaction_damage:0"
    assert hit["damage_type_ids"] == ["damage.reaction"]
    ext = hit["extensions"]["elemental"]
    assert ext["run_id"] == "0:h0:reaction_damage:0"
    assert ext["reaction_id"] == "h0:reaction:0" and ext["parent_event_id"] == "h0"
    assert ext["provider_sample_phase"] == "reaction_created"
    assert ext["enemy_sample_phase"] == "reaction_damage_pre"
    assert ext["damage_icd"]["sequence_index"] == 0
    assert out["result"]["elemental"]["event_counts"]["synchronous"] == {"elemental_reaction": 1, "reaction_damage": 1}


def test_preexisting_bonus_and_same_reaction_shred_affect_child_not_parent():
    t, r, m = setup()
    add(m, rule("static_bonus", "benefit.reaction_bonus.swirl", .6),
        rule("shred", "benefit.res_shred.cryo", .4, domain="enemy",
             trigger={"event": "elemental_reaction", "phase": "post", "elements": ["anemo"], "reaction_elements": ["cryo"]}))
    out = execute_mechanics(t, r, m)
    assert [h["expected"] for h in out["result"]["hits"]] == pytest.approx([45, 1597.3224])


@pytest.mark.parametrize("trigger_event", ["hit", "elemental_reaction"])
@pytest.mark.parametrize("domain", ["self", "provider"])
def test_reaction_created_snapshot_defers_new_bonus_but_not_enemy_shred(trigger_event, domain):
    t, r, m = setup((.2, .3))
    trigger = {"event": trigger_event, "phase": "post"}
    add(m, rule("bonus", "benefit.reaction_bonus.swirl", .6, domain=domain, trigger=trigger),
        rule("shred", "benefit.res_shred.cryo", .4, domain="enemy",
             trigger={"event": "elemental_reaction", "phase": "post"}))
    out = execute_mechanics(t, r, m)
    assert [h["expected"] for h in swirls(out)] == pytest.approx([998.3265, 1597.3224])
    applied = [x for x in out["mechanics_trace"] if x["type"] == "effect_applied" and x["rule_id"] == "bonus" and x["event_id"].endswith("reaction_damage:0")]
    assert [x["event_id"] for x in applied] == ["h1:reaction_damage:0"]


def test_provider_expression_is_sampled_from_reaction_created_state():
    t, r, m = setup((.2, .3))
    add(m, rule("bonus", "benefit.reaction_bonus.swirl", {"kind": "stat", "subject": "owner", "stat": "atk", "factor": .001, "sample": "current"}, domain="provider"),
        rule("change", "benefit.stat_add.atk", 100, trigger={"event": "elemental_reaction", "phase": "post"}))
    assert [h["expected"] for h in swirls(execute_mechanics(t, r, m))] == pytest.approx([781.299*1.1, 781.299*1.2])


def test_positive_delay_does_not_intervene_in_reaction_damage():
    t, r, m = setup((.2, .3))
    shred = rule("shred", "benefit.res_shred.cryo", .4, domain="enemy", trigger={"event": "elemental_reaction", "phase": "post"})
    shred["timing"]["delay"] = .000001
    add(m, shred)
    assert [h["expected"] for h in swirls(execute_mechanics(t, r, m))] == pytest.approx([781.299, 998.3265])


def test_damage_is_atomic_before_same_timestamp_queued_resource_callbacks(monkeypatch):
    from genshin_engine import mechanics
    t, r, m = setup()
    refund = rule("refund", trigger={"event": "elemental_reaction", "phase": "post"})
    refund["effects"] = []
    refund["resources"] = [{"id": "energy", "kind": "energy_direct", "delay": 0, "amounts": {"a": 1}, "source_part": "refund"}]
    add(m, refund)
    m["resource_ownership"] = [{"rule_id": "refund", "action_id": "cast", "source_part": "refund", "replaces_event_ids": []}]
    seen = []
    original = mechanics._Mechanics.on_event
    def observe(self, ev, *args):
        if ev["kind"] == "energy_direct":
            seen.append([row["type"] for row in self.trace])
        return original(self, ev, *args)
    monkeypatch.setattr(mechanics._Mechanics, "on_event", observe)
    out = execute_mechanics(t, r, m)
    assert seen and "reaction_damage" in seen[0]
    resource = next(e for e in out["rotation_check"]["event_log"] if e["type"] == "energy_direct")
    assert resource["id"].startswith("h0:reaction:0~")


def test_no_damage_application_emits_swirl_and_damage_listener_is_separate():
    t, r, m = setup()
    t["hits"] = []
    m["hit_events"] = []
    m["application_events"] = [{"id": "wind", "action_id": "cast", "t": .2, "order": 10,
                                "application": {"profile_id": "anemo", "target_id": "enemy", "landed": True}}]
    add(m, rule("ordinary", trigger={"event": "hit", "phase": "post"}),
        rule("reaction", trigger={"event": "elemental_reaction", "phase": "post"}),
        rule("damage", trigger={"event": "reaction_damage", "phase": "post", "reaction_elements": ["cryo"]}))
    out = execute_mechanics(t, r, m)
    assert [h["expected"] for h in swirls(out)] == pytest.approx([781.299])
    assert [x["rule_id"] for x in out["mechanics_trace"] if x["type"] == "activation"] == ["reaction", "damage"]


def test_empty_application_plan_without_generated_damage_does_not_publish_zero_dps():
    t, r, m = setup()
    t["hits"] = []
    m["hit_events"] = []
    m["elemental_model"]["target"]["initial_aura"] = None
    m["application_events"] = [{"id": "wind", "action_id": "cast", "t": .2, "order": 10,
                                "application": {"profile_id": "anemo", "target_id": "enemy", "landed": True}}]
    out = execute_mechanics(t, r, m)
    assert out["status"] == "pending"
    assert out["result"] is None and out["resolved_template"] is None
    assert out["elemental_trace"][0]["outcome"] == "anemo_without_aura"
    assert out["issues"][-1]["code"] == "no_reported_damage"
    from genshin_engine.template import TemplateError, validate_template
    with pytest.raises(TemplateError, match="hits"):
        validate_template(t)
    validate_template(t, allow_empty_hits=True)
    m.pop("application_events")
    assert execute_mechanics(t, r, m)["status"] == "invalid"


def test_application_miss_and_application_icd_rejection_do_not_consume_damage_icd():
    t, r, m = setup((.2, .3, .4, .5, .6))
    m["hit_events"][0]["application"]["landed"] = False
    m["elemental_model"]["profiles"][-1]["icd"] = {"kind": "standard", "group": "wind"}
    out = execute_mechanics(t, r, m)
    attempts = [x for x in out["elemental_trace"] if x["type"] == "reaction_damage_attempt"]
    assert [x["t"] for x in attempts] == [.3, .6]
    assert [x["sequence_index"] for x in attempts] == [0, 1]


def test_reaction_damage_never_consumes_ordinary_quota_or_matches_hit_filter():
    t, r, m = setup((.2, .3))
    quill = rule("quill", "benefit.base_add.all", 100,
                 quota={"capacity": 3, "scope": "team", "refresh": "reset", "consume": {"event": "hit", "phase": "pre"}})
    only_damage = rule("bonus", "benefit.reaction_bonus.swirl", .6)
    only_damage["effects"][0]["filter"] = {"event": "reaction_damage", "phase": "pre"}
    add(m, quill, only_damage)
    out = execute_mechanics(t, r, m)
    assert [h["expected"] for h in swirls(out)] == pytest.approx([1250.0784]*2)
    assert [x["after"] for x in out["mechanics_trace"] if x["type"] == "quota"] == [2, 1]


def test_owner_presence_checks_provider_not_actor_and_does_not_revoke_old_effect():
    t, r, m = setup((.2, .4))
    second_character(t, r)
    r["abilities"].append(ability("switch", "a", "action.switch"))
    r["actions"].append(action("swap", "switch", .3, .3, 5, switch_to="b", repeat={"mode": "once"}))
    add(m, rule("shred", "benefit.res_shred.cryo", .4, domain="enemy", trigger={"event": "elemental_reaction", "phase": "post", "owner_presence": "frontline"}))
    out = execute_mechanics(t, r, m)
    assert [h["expected"] for h in swirls(out)] == pytest.approx([998.3265]*2)
    assert len([x for x in out["mechanics_trace"] if x["type"] == "activation"]) == 1
    m["rules"][0]["owner"] = "b"
    out = execute_mechanics(t, r, m)
    assert [h["expected"] for h in swirls(out)] == pytest.approx([781.299, 998.3265])


@pytest.mark.parametrize("change", ["missing_em", "level", "rule_em", "manual_em"])
def test_missing_or_dynamic_fixed_em_scope_stops_authoritative_damage(change):
    t, r, m = setup()
    if change == "missing_em":
        del t["characters"][0]["stats"]["em"]
    elif change == "level":
        t["characters"][0]["level"] = 89
    elif change == "rule_em":
        add(m, rule("em", "benefit.em.all", 100, trigger={"event": "hit", "phase": "post"}))
    else:
        t["buffs"] = [{"id": "em", "name": "精通", "start": 0, "end": 3, "effects": [{"id": "em", "zone": "em", "stat": "all", "value": 100,
                           "benefit_type_id": "benefit.em.all", "applies_to": {"mode": "all"}}]}]
    out = execute_mechanics(t, r, m)
    assert out["status"] == "pending", out["issues"]
    assert out["result"] is None
    assert out["issues"][-1]["code"].startswith("swirl_")


def test_reading_em_for_other_benefits_is_allowed_and_empty_aura_needs_no_swirl_evidence():
    t, r, m = setup()
    add(m, rule("em_read", "benefit.dmg_bonus.all", {"kind": "stat", "subject": "owner", "stat": "em", "factor": .001, "sample": "current"}))
    assert swirls(execute_mechanics(t, r, m))[0]["expected"] == pytest.approx(781.299)
    m["elemental_model"]["target"]["initial_aura"] = None
    m["elemental_model"]["swirl_damage"]["icd"] = {"kind": "unknown"}
    del t["characters"][0]["stats"]["em"]
    m["rules"] = []
    m["initial_state"]["rules"] = []
    out = execute_mechanics(t, r, m)
    assert out["result"] is not None
    assert swirls(out) == []


@pytest.mark.parametrize("suffix", [":reaction:0", ":reaction_damage:0"])
def test_reserved_ids_and_unsupported_match_are_rejected(suffix):
    t, r, m = setup((.2, .3))
    t["hits"][1]["id"] = "h0"+suffix
    m["hit_events"][1]["hit_id"] = "h0"+suffix
    assert execute_mechanics(t, r, m)["status"] == "invalid"
    t, r, m = setup()
    add(m, rule())
    out = execute_mechanics(t, r, m, [{"rule_id": "boost", "effect_id": "value", "hit_id": "h0:reaction_damage:0", "operation": "include"}])
    assert out["status"] == "invalid"


def test_synchronous_events_share_budget_with_enqueued_events(monkeypatch):
    from genshin_engine import mechanics
    monkeypatch.setattr(mechanics, "MAX_MECHANICS_EVENTS", 4)
    out = execute_mechanics(*setup())
    assert out["status"] == "invalid"
    assert any("总事件数" in row["message"] for row in out["issues"])


@pytest.mark.parametrize("change", ["damage_pre", "presence_filter", "reaction_elements_hit", "v1_damage_event"])
def test_selector_boundaries_are_semantically_rejected(change):
    t, r, m = setup()
    value = rule()
    if change == "damage_pre":
        value["trigger"] = {"event": "reaction_damage", "phase": "pre"}
    elif change == "presence_filter":
        value["effects"][0]["filter"] = {"event": "reaction_damage", "phase": "pre", "owner_presence": "frontline"}
    elif change == "reaction_elements_hit":
        value["trigger"] = {"event": "hit", "phase": "post", "reaction_elements": ["cryo"]}
    else:
        value["trigger"] = {"event": "reaction_damage", "phase": "post"}
        m["elemental_model"].update(ruleset="gauge-basic-v1")
        m["elemental_model"].pop("swirl_damage")
    add(m, value)
    assert execute_mechanics(t, r, m)["status"] == "invalid"


def test_explicit_manual_reaction_damage_is_not_double_delivered():
    t, r, m = setup()
    t["hits"][0].update(damage_system="transformative", element="cryo", reaction={"type": "swirl"})
    m["hit_events"][0]["application"]["profile_id"] = "cryo"
    out = execute_mechanics(t, r, m)
    assert out["status"] == "invalid"
    assert any(i["code"] == "duplicate_reaction" for i in out["issues"])
