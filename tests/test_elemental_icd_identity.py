"""Adversarial contracts for attack application identity and rule provenance.

These tests exercise declared attack-component profiles.  Action names are
deliberately misleading in places: the elemental runtime must never infer an
ICD tag, rule group, or no-ICD exemption from an action type/name.
"""
from __future__ import annotations

import copy

import pytest
from test_mechanics_execution import fixture

from genshin_engine.elemental import (
    ElementalPending,
    ElementalRuntime,
    validate_elemental_model,
)
from genshin_engine.mechanics import execute_mechanics

EVIDENCE = {"status": "confirmed", "source": "adversarial reviewed fixture"}
DEFAULT_SEQUENCE = [1, 0, 0] * 8
DILUC_SEQUENCE = [1, 0, 0, 0, 0] * 6


def profile(profile_id, *, tag=None, rule="default", sequence=None, no_icd=False):
    if no_icd:
        icd = {"kind": "none"}
    elif sequence is None:
        icd = {"kind": "standard", "counter_tag": tag, "rule_group": rule}
    else:
        icd = {"kind": "sequence", "counter_tag": tag, "rule_group": rule,
               "reset_seconds": 5 if rule == "Diluc" else 2.5, "sequence": list(sequence)}
    return {"id": profile_id, "element": "pyro", "application": "apply", "gauge_units": 1,
            "icd": icd, **EVIDENCE}


def cryo_profile():
    return {"id": "reviewed-cryo-contact", "element": "cryo", "application": "apply",
            "gauge_units": 1, "icd": {"kind": "none"}, **EVIDENCE}


def model(profiles, *, aura=None):
    return {"schema_version": 1, "ruleset": "gauge-basic-v1",
            "target": {"id": "enemy", "kind": "ordinary", "knowledge": "known",
                       "initial_aura": copy.deepcopy(aura), **EVIDENCE},
            "profiles": profiles}


def attempt(runtime, profile_id, index, *, t=None, action_id=None, landed=True):
    return runtime.attempt(
        event_id=f"event-{index}", action_id=action_id or f"action-{index}", actor="actor",
        time=index if t is None else t,
        application={"profile_id": profile_id, "target_id": "enemy", "landed": landed},
        damage_element="pyro")


def cryo(runtime, index, t):
    return runtime.attempt(
        event_id=f"cryo-{index}", action_id="opaque-provider-action", actor="cryo-provider",
        time=t, application={"profile_id": "reviewed-cryo-contact", "target_id": "enemy", "landed": True},
        damage_element="cryo")


def test_default_rule_different_counter_tags_are_independent():
    value = model([
        profile("normal-component", tag="normal-attack"),
        profile("skill-component", tag="elemental-skill"),
    ])
    assert validate_elemental_model(value) == []
    runtime = ElementalRuntime(value)
    rows = [
        attempt(runtime, "normal-component", 0),
        attempt(runtime, "normal-component", 1),
        attempt(runtime, "skill-component", 2),
    ]
    assert [row["application_allowed"] for row in rows] == [True, False, True]
    assert [row["icd_audit"]["counter_tag"] for row in rows] == [
        "normal-attack", "normal-attack", "elemental-skill"]
    assert all(row["profile_status"] == "confirmed" and row["source"] == EVIDENCE["source"]
               for row in rows)
    assert {key[1] for key in runtime.icd} == {"normal-attack", "elemental-skill"}


def test_same_counter_tag_shares_across_profiles_and_actions():
    value = model([
        profile("normal-first-swing", tag="normal-attack"),
        profile("normal-second-swing", tag="normal-attack"),
    ])
    runtime = ElementalRuntime(value)
    rows = [
        attempt(runtime, "normal-first-swing", 0, action_id="cast-a"),
        attempt(runtime, "normal-second-swing", 1, action_id="cast-b"),
        attempt(runtime, "normal-first-swing", 2, action_id="action.elemental_burst"),
        attempt(runtime, "normal-second-swing", 3, action_id="action.elemental_skill"),
    ]
    assert [row["application_allowed"] for row in rows] == [True, False, False, True]
    assert [row["icd_audit"]["sequence_index"] for row in rows] == [0, 1, 2, 3]
    assert len(runtime.icd) == 1


def test_no_icd_contact_never_consumes_a_named_counter():
    value = model([
        profile("counted", tag="elemental-skill"),
        profile("always-applies", no_icd=True),
    ])
    runtime = ElementalRuntime(value)
    first = attempt(runtime, "counted", 0)
    free = attempt(runtime, "always-applies", 1, action_id="action.elemental_skill")
    second = attempt(runtime, "counted", 2)
    assert [first["application_allowed"], free["application_allowed"], second["application_allowed"]] == [True, True, False]
    assert free["icd_audit"] == {"mode": "none", "consumed": False, "counter_tag": None,
                                  "rule_group": None, "shared_key": None,
                                  "sequence_index": None, "attempt_number": None, "coefficient": 1}
    assert runtime.state()["icd"][0]["attempts"] == 2


def test_diluc_five_hit_rule_blocks_fourth_and_allows_sixth_then_resets_at_five_seconds():
    value = model([profile("burst-contact", tag="elemental-burst", rule="Diluc",
                           sequence=DILUC_SEQUENCE)])
    assert validate_elemental_model(value) == []
    runtime = ElementalRuntime(value)
    rows = [attempt(runtime, "burst-contact", index, t=index * 100_000) for index in range(6)]
    assert [row["application_allowed"] for row in rows] == [True, False, False, False, False, True]
    assert rows[3]["icd_audit"]["attempt_number"] == 4
    assert rows[3]["icd_audit"]["coefficient"] == 0
    assert rows[5]["icd_audit"]["attempt_number"] == 6
    assert rows[5]["icd_audit"]["coefficient"] == 1
    assert rows[5]["icd_audit"]["rule_group"] == "Diluc"
    reset_runtime = ElementalRuntime(value)
    assert attempt(reset_runtime, "burst-contact", 0, t=0)["application_allowed"]
    assert not attempt(reset_runtime, "burst-contact", 1, t=4_999_999)["application_allowed"]
    reset = attempt(reset_runtime, "burst-contact", 2, t=5_000_000)
    assert reset["application_allowed"] and reset["reason"] == "icd_timer_reset"


def test_declared_e_a_q_e_a1_a2_e_a3_a4_uses_three_independent_application_contracts():
    """The user-specified sequence is reproduced from explicit component facts.

    Cryo contacts are explicit test inputs before the expected melts.  Neither
    profile selection nor aura restoration is inferred from the action label.
    """
    cryo_aura = {"element": "cryo", "remaining_gu": .8, "decay_gu_per_second": .01,
                 "source": "controlled initial cryo contact"}
    value = model([
        profile("skill-e", no_icd=True),
        profile("normal-a", tag="normal-attack", rule="default"),
        profile("burst-q", tag="elemental-burst", rule="Diluc", sequence=DILUC_SEQUENCE),
        cryo_profile(),
    ], aura=cryo_aura)
    assert validate_elemental_model(value) == []
    runtime = ElementalRuntime(value)

    # A is deliberately more than 2.5 s before A1 so A1 starts a fresh
    # standard window; A2/A3 are positions 2/3 and A4 is position 4.
    contacts = []
    contacts.append(attempt(runtime, "skill-e", 0, t=0, action_id="opaque-action"))
    cryo(runtime, 0, 50_000)
    contacts.append(attempt(runtime, "normal-a", 1, t=100_000, action_id="opaque-action"))
    cryo(runtime, 1, 150_000)
    contacts.append(attempt(runtime, "burst-q", 2, t=200_000, action_id="opaque-action"))
    cryo(runtime, 2, 250_000)
    contacts.append(attempt(runtime, "skill-e", 3, t=300_000, action_id="opaque-action"))
    cryo(runtime, 3, 2_650_000)
    contacts.append(attempt(runtime, "normal-a", 4, t=2_700_000, action_id="opaque-action"))
    cryo(runtime, 4, 2_750_000)
    contacts.append(attempt(runtime, "normal-a", 5, t=2_800_000, action_id="opaque-action"))
    contacts.append(attempt(runtime, "skill-e", 6, t=2_900_000, action_id="opaque-action"))
    cryo(runtime, 5, 2_950_000)
    contacts.append(attempt(runtime, "normal-a", 7, t=3_000_000, action_id="opaque-action"))
    contacts.append(attempt(runtime, "normal-a", 8, t=3_100_000, action_id="opaque-action"))

    assert [row["application_allowed"] for row in contacts] == [
        True, True, True, True, True, False, True, False, True]
    assert ["M" if row["reaction_occurred"] and row["reaction"]["type"] == "melt" else "-"
            for row in contacts] == ["M", "M", "M", "M", "M", "-", "M", "-", "M"]
    assert [row["icd_audit"]["counter_tag"] for row in contacts] == [
        None, "normal-attack", "elemental-burst", None,
        "normal-attack", "normal-attack", None, "normal-attack", "normal-attack"]
    assert [row["icd_audit"]["rule_group"] for row in contacts] == [
        None, "default", "Diluc", None, "default", "default", None, "default", "default"]


def test_removing_cryo_before_preburst_normal_keeps_application_allowed_but_removes_melt():
    cryo_aura = {"element": "cryo", "remaining_gu": .8, "decay_gu_per_second": .01,
                 "source": "controlled initial cryo contact"}
    runtime = ElementalRuntime(model([
        profile("skill-e", no_icd=True),
        profile("normal-a", tag="normal-attack", rule="default"),
    ], aura=cryo_aura))
    e = attempt(runtime, "skill-e", 0, t=0, action_id="action.normal_attack")
    # No cryo provider event is inserted here.  The action label is also
    # deliberately misleading; only the explicitly selected profile matters.
    a = attempt(runtime, "normal-a", 1, t=100_000, action_id="action.elemental_skill")
    assert e["reaction_occurred"] is True
    assert a["application_allowed"] is True
    assert a["reaction_occurred"] is False and a["reaction"] is None
    assert a["outcome"] == "aura_applied"


def test_application_permission_and_reaction_outcome_are_separate_without_cryo_aura():
    runtime = ElementalRuntime(model([profile("skill-contact", no_icd=True)]))
    row = attempt(runtime, "skill-contact", 0)
    assert row["application_allowed"] is True
    assert row["reaction_occurred"] is False
    assert row["reaction"] is None
    assert row["outcome"] == "aura_applied"


def test_application_permission_and_reaction_outcome_are_separate_with_cryo_aura():
    cryo = {"element": "cryo", "remaining_gu": 3.2, "decay_gu_per_second": .1,
            "source": "controlled cryo aura"}
    runtime = ElementalRuntime(model([profile("normal-contact", tag="normal-attack")], aura=cryo))
    melt = attempt(runtime, "normal-contact", 0)
    blocked = attempt(runtime, "normal-contact", 1)
    assert melt["application_allowed"] is True and melt["reaction_occurred"] is True
    assert melt["reaction"]["type"] == "melt"
    assert blocked["application_allowed"] is False and blocked["reaction_occurred"] is False
    assert blocked["reaction"] is None
    assert blocked["before"]["aura"]["element"] == "cryo"


def test_new_identity_contract_rejects_mixed_or_partial_fields_and_enforces_rule_provenance():
    partial = model([profile("p", tag="normal")])
    del partial["profiles"][0]["icd"]["rule_group"]
    assert any(issue["code"] == "icd_identity" for issue in validate_elemental_model(partial))
    mixed = model([profile("p", tag="normal")])
    mixed["profiles"][0]["icd"]["group"] = "legacy"
    assert any(issue["code"] == "icd_identity" for issue in validate_elemental_model(mixed))
    conflict = model([
        profile("default", tag="shared"),
        profile("custom", tag="shared", rule="Diluc", sequence=DILUC_SEQUENCE),
    ])
    # A counter tag is actor-local; static profiles do not contain an actor and
    # therefore cannot be rejected globally merely for sharing a tag.
    assert validate_elemental_model(conflict) == []
    runtime = ElementalRuntime(conflict)
    attempt(runtime, "default", 0)
    with pytest.raises(ValueError, match="同一角色"):
        attempt(runtime, "custom", 1)

    named_rule_conflict = model([
        profile("standard", tag="normal", rule="same-rule-name"),
        profile("different-sequence", tag="burst", rule="same-rule-name", sequence=[1, 0, 0, 0, 0]),
        profile("same-as-standard", tag="skill", rule="same-rule-name"),
    ])
    named_rule_conflict["profiles"][1]["icd"]["reset_seconds"] = 5
    named_issue = next(issue for issue in validate_elemental_model(named_rule_conflict)
                       if issue["code"] == "icd_rule_group_conflict")
    assert named_issue["source_paths"] == [
        "elemental_model.profiles.standard", "elemental_model.profiles.different-sequence",
        "elemental_model.profiles.same-as-standard"]
    assert {binding["profile_id"] for binding in named_issue["bindings"]} == {
        "standard", "different-sequence", "same-as-standard"}
    assert {binding["signature"]["reset_microseconds"] for binding in named_issue["bindings"]} == {
        2_500_000, 5_000_000}
    standard_signatures = [binding["signature"] for binding in named_issue["bindings"]
                           if binding["signature"]["kind"] == "timer_counter"]
    sequence_signatures = [binding["signature"] for binding in named_issue["bindings"]
                           if binding["signature"]["kind"] == "sequence"]
    assert len(standard_signatures) == 2
    assert all(signature["hit_interval"] == 3 and signature["first_hit"] == 1
               for signature in standard_signatures)
    assert [tuple(signature["sequence"]) for signature in sequence_signatures] == [
        (1, 0, 0, 0, 0)]


def test_same_counter_tag_may_use_different_rules_for_different_actors():
    value = model([
        profile("default", tag="elemental-burst", rule="default"),
        profile("custom", tag="elemental-burst", rule="Diluc", sequence=DILUC_SEQUENCE),
    ])
    assert validate_elemental_model(value) == []
    runtime = ElementalRuntime(value)
    first = runtime.attempt(
        event_id="a-hit", action_id="opaque-a", actor="actor-a", time=0,
        application={"profile_id": "default", "target_id": "enemy", "landed": True},
        damage_element="pyro")
    second = runtime.attempt(
        event_id="b-hit", action_id="opaque-b", actor="actor-b", time=1,
        application={"profile_id": "custom", "target_id": "enemy", "landed": True},
        damage_element="pyro")
    assert first["application_allowed"] and second["application_allowed"]
    assert set(runtime.icd) == {
        ("actor-a", "elemental-burst", "enemy"),
        ("actor-b", "elemental-burst", "enemy"),
    }
    assert {row["rule_group"] for row in runtime.state()["icd"]} == {"default", "Diluc"}


def test_execute_mechanics_reports_same_actor_counter_rule_conflict_as_structured_invalid():
    template, rotation, mechanics = fixture((.2, .3))
    for hit in template["hits"]:
        hit["element"] = "pyro"
    mechanics["elemental_model"] = model([
        profile("default", tag="shared", rule="default"),
        profile("custom", tag="shared", rule="Diluc", sequence=DILUC_SEQUENCE),
    ])
    mechanics["hit_events"][0]["application"] = {
        "profile_id": "default", "target_id": "enemy", "landed": True}
    mechanics["hit_events"][1]["application"] = {
        "profile_id": "custom", "target_id": "enemy", "landed": True}
    result = execute_mechanics(template, rotation, mechanics)
    assert result["status"] == "invalid"
    issue = next(issue for issue in result["issues"] if issue["code"] == "icd_group_conflict")
    assert issue["actor"] == "a" and issue["counter_tag"] == "shared"
    assert {issue["rule_group"], issue["conflicting_rule_group"]} == {"default", "Diluc"}
    assert {binding["profile_id"] for binding in issue["bindings"]} == {"default", "custom"}
    assert {binding["rule_group"] for binding in issue["bindings"]} == {"default", "Diluc"}
    assert {binding["signature"]["reset_microseconds"] for binding in issue["bindings"]} == {
        2_500_000, 5_000_000}
    assert all(binding["profile_path"].startswith("mechanics_model.elemental_model.profiles.")
               and binding["usage_paths"] for binding in issue["bindings"])
    assert set(issue["source_paths"]) >= {
        "mechanics_model.elemental_model.profiles.default",
        "mechanics_model.elemental_model.profiles.custom",
    }


def test_execute_mechanics_allows_same_counter_tag_rules_to_differ_across_actors():
    template, rotation, mechanics = fixture((.2, .3))
    for hit in template["hits"]:
        hit["element"] = "pyro"
    second_character = copy.deepcopy(template["characters"][0])
    second_character["id"] = "b"
    template["characters"].append(second_character)
    template["hits"][1]["character"] = "b"
    second_rotation_character = copy.deepcopy(rotation["characters"][0])
    second_rotation_character["id"] = "b"
    rotation["characters"].append(second_rotation_character)
    second_ability = copy.deepcopy(rotation["abilities"][0])
    second_ability.update(id="b-cast-ability", character="b")
    rotation["abilities"].append(second_ability)
    switch_ability = copy.deepcopy(rotation["abilities"][0])
    switch_ability.update(id="switch-to-b", character="a", action_type_id="action.switch")
    rotation["abilities"].append(switch_ability)
    switch_action = copy.deepcopy(rotation["actions"][0])
    switch_action.update(id="switch-b", ability_id="switch-to-b", start=.21,
                         occupation_end=.22, order=1, switch_to="b")
    rotation["actions"].append(switch_action)
    second_action = copy.deepcopy(rotation["actions"][0])
    second_action.update(id="b-cast", ability_id="b-cast-ability", start=.23,
                         occupation_end=.24, order=2)
    rotation["actions"].append(second_action)
    switch_back_ability = copy.deepcopy(rotation["abilities"][0])
    switch_back_ability.update(id="switch-to-a", character="b", action_type_id="action.switch")
    rotation["abilities"].append(switch_back_ability)
    switch_back_action = copy.deepcopy(rotation["actions"][0])
    switch_back_action.update(id="switch-a", ability_id="switch-to-a", start=.4,
                              occupation_end=.41, order=3, switch_to="a")
    rotation["actions"].append(switch_back_action)
    mechanics["hit_events"][1]["action_id"] = "b-cast"
    mechanics["elemental_model"] = model([
        profile("default", tag="shared", rule="default"),
        profile("custom", tag="shared", rule="Diluc", sequence=DILUC_SEQUENCE),
    ])
    mechanics["hit_events"][0]["application"] = {
        "profile_id": "default", "target_id": "enemy", "landed": True}
    mechanics["hit_events"][1]["application"] = {
        "profile_id": "custom", "target_id": "enemy", "landed": True}
    result = execute_mechanics(template, rotation, mechanics)
    assert result["status"] == "passed", result["issues"]
    assert not any(issue["code"] == "icd_group_conflict" for issue in result["issues"])
    applications = [row for row in result["elemental_trace"]
                    if row["type"] == "elemental_application" and row["cycle"] == 0]
    assert [row["application_allowed"] for row in applications] == [True, True]
    assert [row["icd_audit"]["rule_group"] for row in applications] == ["default", "Diluc"]


def test_legacy_group_remains_a_counter_and_rule_group_compatibility_projection():
    legacy = profile("legacy", tag="unused")
    legacy["icd"] = {"kind": "standard", "group": "old-shared-name"}
    value = model([legacy])
    assert validate_elemental_model(value) == []
    row = attempt(ElementalRuntime(value), "legacy", 0)
    assert row["icd_audit"]["legacy_group"] is True
    assert row["icd_audit"]["counter_tag"] == "old-shared-name"
    assert row["icd_audit"]["rule_group"] == "old-shared-name"


def test_unknown_icd_remains_pending_and_does_not_create_counter_state():
    unknown = profile("unknown", tag="unused")
    unknown["icd"] = {"kind": "unknown"}
    value = model([unknown])
    assert validate_elemental_model(value) == []
    runtime = ElementalRuntime(value)
    with pytest.raises(ElementalPending):
        attempt(runtime, "unknown", 0, action_id="action.normal_attack")
    assert runtime.state()["icd"] == []
    assert runtime.trace[-1]["application_allowed"] is None
    assert runtime.trace[-1]["reaction_occurred"] is False
