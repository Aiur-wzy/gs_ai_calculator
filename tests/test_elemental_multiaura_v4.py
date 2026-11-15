"""Adversarial v4 multi-aura checks with hand-authored state expectations."""
from __future__ import annotations

import copy

import pytest

from genshin_engine.elemental import (
    ElementalPending,
    ElementalRuntime,
    validate_elemental_model,
)
from genshin_engine.elemental_multiaura import MultiAuraState
from genshin_engine.elemental_schema import MULTI_AURA_RULESET, elemental_capabilities

EVIDENCE = {"status": "assumed", "source": "controlled v4 adversarial fixture"}


def aura(element, gu, owner=None):
    return {"element": element, "remaining_gu": gu,
            "decay_gu_per_second": .001, "source": "controlled initial layer",
            **({"source_owner": owner} if owner else {})}


def model(initial=(), *, freezable=True, resolution=None, legacy_mode=None):
    freeze = {"freezable": freezable, "resistance": 0, **EVIDENCE}
    if resolution is not None:
        freeze["unfreezable_resolution"] = resolution
    if legacy_mode is not None:
        freeze["mode"] = legacy_mode
    return {
        "schema_version": 1,
        "ruleset": MULTI_AURA_RULESET,
        "target": {"id": "enemy", "kind": "ordinary", "knowledge": "known",
                   "initial_auras": list(initial), "freeze": freeze, **EVIDENCE},
        "profiles": [
            {"id": element, "element": element, "application": "apply",
             "gauge_units": 1, "icd": {"kind": "none"}, **EVIDENCE}
            for element in ("pyro", "hydro", "cryo", "electro", "anemo", "geo")
        ],
        "swirl_damage": {"icd": {"kind": "sequence", "reset_seconds": .5,
                                  "sequence": [1, 1, 0]}, **EVIDENCE},
    }


def attempt(runtime, element, t=0, *, actor="a", event_id=None):
    time = round(t * 1_000_000)
    return runtime.attempt(
        event_id=event_id or f"{element}:{len(runtime.trace)}",
        stable_event_id=event_id,
        action_id="action",
        actor=actor,
        time=time,
        application={"profile_id": element, "target_id": "enemy", "landed": True},
        trigger_stat_sample=({
            "contract": "ordinary-crystallize-trigger-stat-v1",
            "character": actor, "phase": "reaction_created",
            "time_tick": time, "level": 90, "em": 0.0,
            "source": "controlled v4 adversarial fixture",
        } if element == "geo" else None),
    )


def layers(row):
    return {item["element"]: item["remaining_gu"] for item in row["after"]["auras"]}


def test_owner_residual_ledger_uses_max_overlap_and_consumes_each_owner_equally():
    state = MultiAuraState(
        [aura("electro", .2, "A"), aura("hydro", .7, "B")],
        freezable=False)
    state.add_or_refresh("electro", 1, "C-electro", "C")  # 0.8U
    state.add_or_refresh("hydro", .375, "C-hydro", "C")   # 0.3U
    assert state.auras["electro"]["remaining_gu"] == pytest.approx(.8)
    assert state.auras["hydro"]["remaining_gu"] == pytest.approx(.7)
    assert sum(item["remaining_gu"] for item in
               state.contributor_allocations("electro")) == pytest.approx(1.0)

    # One lunar-Electro attack removes 0.4U from every owner ledger entry,
    # rather than distributing one shared 0.4U budget between owners.
    state.consume("hydro", .4)
    state.consume("electro", .4)
    assert state.auras["electro"]["remaining_gu"] == pytest.approx(.4)
    assert state.auras["hydro"]["remaining_gu"] == pytest.approx(.3)
    assert state.auras["electro"]["contributor_ids"] == ["C"]
    assert state.auras["hydro"]["contributor_ids"] == ["B"]


def test_owner_residuals_share_first_aura_decay_clock_and_same_owner_refresh_is_max():
    state = MultiAuraState([aura("electro", .2, "A")], freezable=False)
    original_rate = state.auras["electro"]["decay_gu_per_second"]
    state.add_or_refresh("electro", 1, "C-electro", "C")
    state.add_or_refresh("electro", .5, "C-weaker-refresh", "C")
    assert state.auras["electro"]["decay_gu_per_second"] == original_rate
    before = {row["owner"]: row["remaining_gu"]
              for row in state.contributor_allocations("electro")}
    assert before == pytest.approx({"A": .2, "C": .8})
    state.advance(1_000_000)
    after = {row["owner"]: row["remaining_gu"]
             for row in state.contributor_allocations("electro")}
    assert after["A"] == pytest.approx(.2-original_rate)
    assert after["C"] == pytest.approx(.8-original_rate)


def test_weaker_same_owner_application_refreshes_recency_without_adding_gauge():
    state = MultiAuraState([aura("electro", .8, "C")], freezable=False)
    state.advance(1_000_000)
    before = state.contributor_allocations("electro")[0]

    state.add_or_refresh("electro", .1, "C-weaker-reapplication", "C")

    after = state.contributor_allocations("electro")[0]
    assert after["remaining_gu"] == pytest.approx(before["remaining_gu"])
    assert after["last_application_at"] == pytest.approx(1.0)
    assert after["source"] == "C-weaker-reapplication"


def test_unfreezable_default_resolution_is_coexist_and_fingerprinted():
    value = model(freezable=False)
    assert "unfreezable_resolution" not in value["target"]["freeze"]
    assert validate_elemental_model(value) == []
    runtime = ElementalRuntime(value)
    state = runtime.state()
    assert state["freeze_capability"] == "unfreezable"
    assert state["unfreezable_resolution"] == "coexist"
    assert state["unfreezable_resolution_origin"] == "default_scenario"
    assert len(state["freeze_scenario_contract"]["fingerprint"]) == 64
    contract = elemental_capabilities()["multi_aura_contract"]
    assert contract["ruleset"] == MULTI_AURA_RULESET
    assert "默认coexist" in contract["unfreezable_resolution"]


def test_manual_same_resolution_has_distinct_origin_and_contract_fingerprint():
    default = ElementalRuntime(model(freezable=False)).state()["freeze_scenario_contract"]
    manual = ElementalRuntime(model(freezable=False, resolution="coexist")).state()["freeze_scenario_contract"]
    assert default["unfreezable_resolution"] == manual["unfreezable_resolution"] == "coexist"
    assert default["resolution_origin"] == "default_scenario"
    assert manual["resolution_origin"] == "manual_override"
    assert default["fingerprint"] != manual["fingerprint"]


def test_legacy_mode_is_only_a_traced_migration_alias():
    value = model(freezable=False, legacy_mode="mutual_cancel")
    assert validate_elemental_model(value) == []
    runtime = ElementalRuntime(value)
    contract = runtime.state()["freeze_scenario_contract"]
    assert contract["unfreezable_resolution"] == "mutual_cancel"
    assert contract["resolution_origin"] == "legacy_mode_migration"
    assert contract["legacy_mode"] == "mutual_cancel"


def test_resolved_freeze_scenario_is_present_on_every_application_trace():
    runtime = ElementalRuntime(model(freezable=False))
    row = attempt(runtime, "electro")
    assert row["freeze_scenario"]["resolution_origin"] == "default_scenario"
    assert row["freeze_scenario"]["fingerprint"] == row["after"]["freeze_scenario_contract"]["fingerprint"]


def test_missing_target_freezable_is_never_silently_treated_as_unfreezable():
    value = model()
    del value["target"]["freeze"]["freezable"]
    assert validate_elemental_model(value)
    with pytest.raises(ValueError, match="是否可冻结"):
        ElementalRuntime(value)


def test_electro_swirl_consumes_aura_and_uses_element_scoped_damage_identity():
    runtime = ElementalRuntime(model([aura("electro", .7)]))
    row = attempt(runtime, "anemo", event_id="wind")
    reaction = row["reaction"]
    assert reaction["type"] == "swirl"
    assert reaction["reaction_element"] == "electro"
    assert reaction["consumed_gu"] == pytest.approx(.5)
    assert layers(row)["electro"] == pytest.approx(.2)
    damage = runtime.attempt_reaction_damage(reaction)
    assert damage["allowed"] is True
    assert damage["shared_key"] == ["a", "enemy", "swirl", "electro"]


def test_freezable_target_creates_real_frozen_state_instead_of_coexistence():
    runtime = ElementalRuntime(model([aura("hydro", .7)]))
    row = attempt(runtime, "cryo")
    assert row["reaction"]["type"] == "freeze"
    assert row["reaction"]["effective_frozen_gu"] == pytest.approx(1.4)
    assert row["after"]["frozen"]["remaining_gu"] == pytest.approx(1.4)
    assert row["after"]["auras"] == []
    assert row["after"]["coexisting_hydro_cryo"] is False


def test_freezable_initial_hydro_cryo_is_pending_at_validation_and_runtime():
    value = model([aura("hydro", .4), aura("cryo", .3)])
    assert "freezable_frozen_state_required" in {
        issue["code"] for issue in validate_elemental_model(value)}
    runtime = ElementalRuntime(value)
    before = copy.deepcopy(runtime.state())
    with pytest.raises(ElementalPending) as error:
        attempt(runtime, "anemo")
    assert error.value.issue["code"] == "freezable_frozen_state_required"
    assert runtime.state() == before


def test_unfreezable_default_coexist_adds_layer_without_fake_freeze_reaction():
    runtime = ElementalRuntime(model([aura("hydro", .7)], freezable=False))
    row = attempt(runtime, "cryo")
    assert row["reaction"] is None and row["reactions"] == []
    assert row["reaction_occurred"] is False
    assert row["freeze_resolution"]["resolution"] == "coexist"
    assert row["freeze_resolution"]["origin"] == "default_scenario"
    assert row["freeze_resolution"]["reaction_occurred"] is False
    assert layers(row) == pytest.approx({"hydro": .7, "cryo": .8})
    assert row["after"]["coexisting_hydro_cryo"] is True


def test_nonfreezable_target_mutually_cancels_and_preserves_stronger_old_layer():
    runtime = ElementalRuntime(model([aura("hydro", 1.4)], freezable=False,
                                     resolution="mutual_cancel"))
    row = attempt(runtime, "cryo")
    assert row["reaction"] is None and row["reaction_occurred"] is False
    assert row["freeze_resolution"]["resolution"] == "mutual_cancel"
    assert row["freeze_resolution"]["origin"] == "manual_override"
    assert row["freeze_resolution"]["surviving_aura"] == "hydro"
    assert layers(row) == pytest.approx({"hydro": .6})
    assert row["after"]["coexisting_hydro_cryo"] is False
    assert row["after"]["unfreezable_resolution"] == "mutual_cancel"


def test_nonfreezable_target_can_leave_stronger_incoming_layer():
    runtime = ElementalRuntime(model([aura("hydro", .3)], freezable=False,
                                     resolution="mutual_cancel"))
    runtime.profiles["cryo"]["gauge_units"] = 2
    row = attempt(runtime, "cryo")
    assert row["freeze_resolution"]["surviving_aura"] == "cryo"
    assert layers(row) == pytest.approx({"cryo": 1.3})


@pytest.mark.parametrize("hydro_gu, cryo_gu", [(.6, .3), (.2, .7)])
def test_pyro_fixed_route_starts_hydro_regardless_of_gauge(hydro_gu, cryo_gu):
    runtime = ElementalRuntime(model([aura("hydro", hydro_gu), aura("cryo", cryo_gu)], freezable=False))
    row = attempt(runtime, "pyro")
    assert row["reaction"]["type"] == "vaporize"
    assert row["reaction"]["factor"] == 1.5
    assert row["reaction"]["aura_element"] == "hydro"
    assert row["reaction_priority"]["route"] == ["hydro", "cryo"]
    assert row["reaction_priority"]["gauge_independent"] is True
    assert [item["rank"] for item in row["reaction_priority"]["ranked_candidates"]] == [1, 2]
    assert all("skip_reason" in item for item in row["reaction_priority"]["ranked_candidates"])


def test_remaining_pyro_trigger_continues_from_hydro_to_cryo():
    runtime = ElementalRuntime(model([aura("hydro", .2), aura("cryo", .3)], freezable=False))
    row = attempt(runtime, "pyro", event_id="chain")
    assert [item["type"] for item in row["reactions"]] == ["vaporize", "melt"]
    assert [item["aura_element"] for item in row["reactions"]] == ["hydro", "cryo"]
    assert row["reactions"][0]["trigger_gu_before"] == 1
    assert row["reactions"][0]["trigger_gu_after"] == pytest.approx(.6)
    assert row["reactions"][1]["trigger_gu_before"] == pytest.approx(.6)
    assert row["reactions"][1]["trigger_gu_after"] == pytest.approx(.45)
    assert layers(row) == {}


def test_reaction_receipt_keeps_trigger_and_aura_provider_owners():
    runtime = ElementalRuntime(model([aura("electro", .7, "electro-provider")]))
    row = attempt(runtime, "anemo", actor="wind-provider")
    assert row["reaction"]["trigger_owner"] == "wind-provider"
    assert row["reaction"]["owner"] == "wind-provider"
    assert row["reaction"]["aura_owner"] == "electro-provider"


def test_new_aura_layer_records_effective_provider_owner():
    runtime = ElementalRuntime(model())
    row = attempt(runtime, "electro", actor="electro-provider")
    assert row["after"]["auras"][0]["source_owner"] == "electro-provider"


@pytest.mark.parametrize("hydro_gu, cryo_gu", [(.1, .7), (.7, .1)])
def test_anemo_route_is_hydro_then_cryo_even_when_gu_order_flips(hydro_gu, cryo_gu):
    runtime = ElementalRuntime(model([aura("hydro", hydro_gu), aura("cryo", cryo_gu)], freezable=False))
    runtime.profiles["anemo"]["gauge_units"] = 4
    row = attempt(runtime, "anemo")
    assert [item["reaction_element"] for item in row["reactions"]][:2] == ["hydro", "cryo"]
    assert row["reaction_priority"]["route_id"] == "anemo:hydro>cryo"
    assert all(item["status"] in ("reacted", "skipped") for item in row["reaction_priority"]["ranked_candidates"])


def test_route_trace_marks_later_candidate_skipped_when_trigger_is_exhausted():
    runtime = ElementalRuntime(model([aura("hydro", .8), aura("cryo", .8)], freezable=False))
    row = attempt(runtime, "anemo")
    steps = row["reaction_priority"]["ranked_candidates"]
    assert steps[0]["status"] == "reacted"
    assert steps[0]["trigger_gu_before"] == 1
    assert steps[0]["trigger_gu_after"] == 0
    assert steps[1]["status"] == "skipped"
    assert steps[1]["skip_reason"] == "trigger_exhausted"


@pytest.mark.parametrize("hydro_gu, cryo_gu", [(.8, .2), (.2, .8)])
def test_unknown_electro_pair_keeps_hydro_first_route_when_gu_order_flips(hydro_gu, cryo_gu):
    runtime = ElementalRuntime(model([aura("hydro", hydro_gu), aura("cryo", cryo_gu)], freezable=False))
    before = copy.deepcopy(runtime.state())
    with pytest.raises(ElementalPending) as error:
        attempt(runtime, "electro")
    assert error.value.issue["code"] == "unsupported_reaction"
    assert error.value.issue["reaction_priority"]["route"] == ["hydro", "cryo"]
    assert runtime.state() == before


@pytest.mark.parametrize("hydro_gu, cryo_gu", [(.6, .2), (.2, .6)])
def test_geo_uses_fixed_hydro_then_cryo_route_and_creates_hydro_shard(hydro_gu, cryo_gu):
    runtime = ElementalRuntime(model([aura("hydro", hydro_gu), aura("cryo", cryo_gu)], freezable=False))
    row = attempt(runtime, "geo")
    assert row["reaction_priority"]["route"] == ["hydro", "cryo"]
    assert row["reaction"]["type"] == "crystallize"
    assert row["reaction"]["reaction_element"] == "hydro"
    assert row["crystallize_shards"][0]["element"] == "hydro"


@pytest.mark.parametrize("mutation, code", [
    (lambda value: value["target"].update(initial_aura=None), "duplicate_state"),
    (lambda value: value["target"]["initial_auras"].append(aura("pyro", .4)),
     "unsupported_coexistence"),
    (lambda value: value["target"]["freeze"].update(unfreezable_resolution="coexist"),
     "unused_field"),
    (lambda value: value["target"]["freeze"].update(mode="coexist", unfreezable_resolution="coexist"),
     "freeze_resolution_duplicate"),
])
def test_invalid_or_unimplemented_state_contracts_are_rejected(mutation, code):
    value = model([aura("hydro", .6), aura("cryo", .3)])
    mutation(value)
    assert code in {issue["code"] for issue in validate_elemental_model(value)}


def test_v3_does_not_silently_gain_electro_swirl_support():
    from test_elemental_swirl import swirl_model

    value = swirl_model("hydro")
    value["profiles"].append({"id": "electro", "element": "electro",
                              "application": "apply", "gauge_units": 1,
                              "icd": {"kind": "none"}, **EVIDENCE})
    runtime = ElementalRuntime(value)
    with pytest.raises(ElementalPending) as error:
        attempt(runtime, "electro")
    assert error.value.issue["code"] == "unsupported_element"
