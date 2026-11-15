"""Small end-to-end checks for special-runtime -> mechanics -> v2 projection."""
from __future__ import annotations

from types import SimpleNamespace

import pytest
from test_elemental_multiaura_v4 import EVIDENCE, aura, model
from test_mechanics_execution import add, fixture, rule, second_character
from test_special_reaction_integration import equipment_snapshot, replacement
from test_special_reactions import direct_lunar_bloom_profile, direct_lunar_profile
from test_special_reactions import reaction as special_reaction
from test_special_reactions import runtime as special_runtime

from genshin_engine.elemental import ElementalPending
from genshin_engine.elemental_bloom_core import TICKS, BloomCoreState
from genshin_engine.mechanics import _Mechanics, execute_mechanics
from genshin_engine.mechanics_elemental_bridge import (
    MechanicsElementalBridge,
    MechanicsElementalPorts,
)
from genshin_engine.mechanics_special_damage import (
    _apply_special_damage_weights,
    _direct_profile_evidence_label,
)
from genshin_engine.special_reaction_capabilities import (
    load_reviewed_special_reaction_capabilities,
    project_special_reaction_party_capabilities,
)
from genshin_engine.special_reactions import (
    SpecialReactionPending,
    direct_lunar_damage_profile_content_hash,
)


def _enable(mechanics, reaction_type, *, provider="a"):
    mechanics["special_reaction_model"] = {
        "schema_version": 1,
        "replacements": {reaction_type: replacement(reaction_type)},
    }
    qualification = mechanics["special_reaction_model"]["replacements"][reaction_type]["qualification"]
    qualification["provider_ids"] = [provider]
    catalog = load_reviewed_special_reaction_capabilities()
    entry = next(row for row in catalog["entries"]
                 if qualification["capability_tag"] in row["capability_tags"])
    mechanics["special_reaction_party_capabilities"] = (
        project_special_reaction_party_capabilities({provider: equipment_snapshot(
            entry["character_id"], skill_depot_id=entry["required_skill_depot_id"]) }))
    mechanics["special_reaction_events"] = []


def _application_fixture(element, *, initial, times=(.2,)):
    template, rotation, mechanics = fixture(times)
    rotation["actions"][0]["repeat"] = {"mode": "once"}
    mechanics["elemental_model"] = model(initial, freezable=False)
    for index, hit in enumerate(template["hits"]):
        hit["element"] = element if index == 0 else hit["element"]
    mechanics["hit_events"][0]["application"] = {
        "profile_id": element, "target_id": "enemy", "landed": True,
        "area_id": "battlefield.primary",
    }
    return template, rotation, mechanics


@pytest.mark.parametrize(("status", "authority", "expected"), [
    ("assumed", "explicit_assumption", "显式假设能力profile"),
    ("confirmed", "reviewed_database_projection", "受审数据库能力profile"),
    ("assumed", "reviewed_database_projection", "带审计状态的能力profile"),
])
def test_direct_profile_evidence_label_never_promotes_assumption(
        status, authority, expected):
    assert _direct_profile_evidence_label({
        "status": status,
        "authority": authority,
    }) == expected


def test_stellar_swirl_request_projects_contributors_then_ranks_independently():
    template, rotation, mechanics = _application_fixture(
        "anemo", initial=[aura("cryo", .7, "b")])
    second_character(template, rotation, "b")
    template["characters"][0]["stats"].update(em=0, crit_rate=0)
    template["characters"][1]["stats"].update(em=800, crit_rate=1, crit_dmg=1)
    _enable(mechanics, "stellar_swirl")

    out = execute_mechanics(template, rotation, mechanics)

    assert out["status"] in ("passed", "unproven"), out["issues"]
    generated = [row for row in out["result"]["hits"]
                 if row["damage_system"] == "stellar"
                 and ":immediate:anemo:" in row["id"]]
    assert len(generated) == 2
    by_character = {row["character"]: row for row in generated}
    # The immediate Anemo component fixes the Anemo trigger in the 0.6 slot;
    # a stronger Cryo contributor cannot displace it.
    assert by_character["a"]["result_weight"] == pytest.approx(.6)
    assert by_character["b"]["result_weight"] == pytest.approx(.3)
    resolved = [row for row in out["resolved_template"]["hits"]
                if row["damage_system"] == "stellar"
                and ":immediate:anemo:" in row["id"]]
    assert all(row["damage_type_ids"] == ["damage.reaction"] for row in resolved)
    assert all(row["skill"] == "星扩散" and row["reaction"]["type"] == "stellar_swirl_anemo"
               for row in resolved)
    weighting = [row for row in out["special_reactions"]["damage_weighting"]
                 if row["reaction_type"] == "stellar_swirl_anemo"]
    assert len(weighting) == 1
    assert [row["result_weight"] for row in weighting[0]["ranking"]] == [.6, .3]
    assert weighting[0]["policy"] == (
        "stellar_swirl_role_slots_then_independent_actual_damage")
    assert weighting[0]["role_provenance"]["anemo_trigger_ids"] == ["a"]
    types = {row["type"] for row in out["mechanics_trace"]}
    assert {"special_reaction_decision", "special_reaction_damage_request",
            "special_reaction_weighting"} <= types


def test_special_weighting_ranks_each_actual_crit_outcome_not_expected_values():
    def hit(hit_id, character):
        return {
            "id": hit_id, "character": character, "result_weight": 1.0,
            "extensions": {"special_reaction": {}},
        }
    resolved = {"hits": [hit("a-hit", "A"), hit("b-hit", "B")]}
    first = {"hits": [
        {"id": "a-hit", "expected": 100.0, "crit": 100.0,
         "zones": {"crit_expected": 1.0}},
        {"id": "b-hit", "expected": 120.0, "crit": 180.0,
         "zones": {"crit_expected": 2.0}},
    ]}
    groups = {"request": {
        "hit_ids": ["a-hit", "b-hit"], "cycle": 0,
        "request": {"event_id": "request", "reaction_type": "lunar_electro",
                    "t": 1},
    }}

    receipts = _apply_special_damage_weights(resolved, first, groups)

    weights = {row["id"]: row["result_weight"] for row in resolved["hits"]}
    assert weights == pytest.approx({"a-hit": .45, "b-hit": .525})
    receipt = receipts[0]
    assert receipt["policy"] == "independent_crit_then_actual_damage_desc"
    assert receipt["joint_outcome_count"] == 4
    assert receipt["joint_probability_total"] == pytest.approx(1)
    assert receipt["expected"] == pytest.approx(108)
    assert all(row["result_weight_semantics"] == "expected_equivalent_projection"
               for row in receipt["ranking"])


def _weighting_fixture(rows, reaction_type, *, role_provenance=None):
    def hit(hit_id, character):
        return {
            "id": hit_id, "character": character, "result_weight": 1.0,
            "extensions": {"special_reaction": {}},
        }

    resolved = {"hits": [hit(row[0]+"-hit", row[0]) for row in rows]}
    first = {"hits": [{
        "id": row[0]+"-hit", "expected": row[1], "crit": row[2],
        "zones": {"crit_expected": row[3]},
    } for row in rows]}
    request = {
        "event_id": "weighted-request", "reaction_type": reaction_type,
        "t": 1,
    }
    if role_provenance is not None:
        request["role_provenance"] = role_provenance
    return resolved, first, {"request": {
        "hit_ids": [row[0]+"-hit" for row in rows], "cycle": 0,
        "request": request,
    }}


def _stellar_roles(policy, participants, *, anemo, cryo):
    return {
        "contract": "stellar-swirl-role-provenance-v1",
        "component_policy": policy,
        "participant_ids": list(participants),
        "anemo_trigger_ids": list(anemo),
        "cryo_applier_ids": list(cryo),
        "status": "ready",
    }


def test_production_weighting_keeps_immediate_anemo_trigger_fixed_then_ranks_remainder_per_crit():
    # anemo=10 fixed; cryo.fixed=100 fixed; cryo.volatile=60 non-Crit /
    # 180 Crit at 50%.  Only the two remainder slots swap per actual outcome.
    rows = [
        ("anemo", 10.0, 10.0, 1.0),
        ("cryo.fixed", 100.0, 100.0, 1.0),
        ("cryo.volatile", 120.0, 180.0, 2.0),
    ]
    roles = _stellar_roles(
        "immediate_anemo_trigger_fixed_first",
        [row[0] for row in rows], anemo=("anemo",),
        cryo=("cryo.fixed", "cryo.volatile"))
    resolved, first, groups = _weighting_fixture(
        rows, "stellar_swirl_anemo", role_provenance=roles)

    receipt = _apply_special_damage_weights(resolved, first, groups)[0]

    weights = {row["character"]: row["result_weight"]
               for row in resolved["hits"]}
    assert weights == pytest.approx({
        "anemo": .6, "cryo.fixed": .175, "cryo.volatile": .2375})
    assert receipt["expected"] == pytest.approx(52)
    assert receipt["policy"] == (
        "stellar_swirl_role_slots_then_independent_actual_damage")
    assert [row["contributor_id"] for row in receipt["joint_outcomes"][0]["ranking"]] == [
        "anemo", "cryo.fixed", "cryo.volatile"]
    assert [row["contributor_id"] for row in receipt["joint_outcomes"][4]["ranking"]] == [
        "anemo", "cryo.volatile", "cryo.fixed"]


def test_production_weighting_selects_vortex_cryo_slot_per_joint_crit_outcome():
    rows = [
        ("cryo.fixed", 100.0, 100.0, 1.0),
        ("cryo.volatile", 120.0, 180.0, 2.0),
        ("anemo", 10.0, 10.0, 1.0),
        ("other", 200.0, 200.0, 1.0),
    ]
    roles = _stellar_roles(
        "vortex_cryo_best_then_anemo_best",
        [row[0] for row in rows], anemo=("anemo",),
        cryo=("cryo.fixed", "cryo.volatile"))
    resolved, first, groups = _weighting_fixture(
        rows, "stellar_swirl_cryo_lv3", role_provenance=roles)

    receipt = _apply_special_damage_weights(resolved, first, groups)[0]

    weights = {row["character"]: row["result_weight"]
               for row in resolved["hits"]}
    assert weights == pytest.approx({
        "cryo.fixed": .325, "cryo.volatile": .4625,
        "anemo": .3, "other": .05})
    assert receipt["expected"] == pytest.approx(101)
    assert [row["contributor_id"] for row in receipt["joint_outcomes"][0]["ranking"]] == [
        "cryo.fixed", "anemo", "other", "cryo.volatile"]
    assert [row["contributor_id"] for row in receipt["joint_outcomes"][2]["ranking"]] == [
        "cryo.volatile", "anemo", "other", "cryo.fixed"]


def test_production_stellar_swirl_projection_fails_before_emitting_hits_without_roles():
    runner = object.__new__(_Mechanics)
    runner.time = 0
    runner.data = {"chars": {"anemo": {}, "cryo": {}}}
    request = {
        "event_id": "missing-roles", "time_tick": 0, "t": 0,
        "reaction_base": True, "reaction_type": "stellar_swirl_anemo",
        "contributor_ids": ["anemo", "cryo"],
    }

    with pytest.raises(SpecialReactionPending) as error:
        runner._project_special_damage(request, 0, {"order": 0, "causal": ()})

    assert error.value.issue["code"] == "stellar_swirl_role_provenance_missing"
    assert not hasattr(runner, "hit_rows")


def test_stellar_swirl_detonation_application_reenters_queue_and_enables_later_melt():
    template, rotation, mechanics = _application_fixture(
        "anemo", initial=[aura("cryo", .5, "b")], times=(.2, 3.3))
    second_character(template, rotation, "b")
    template["hits"][1]["element"] = "pyro"
    mechanics["hit_events"][1]["application"] = {
        "profile_id": "pyro", "target_id": "enemy", "landed": True,
        "area_id": "battlefield.primary",
    }
    _enable(mechanics, "stellar_swirl")

    out = execute_mechanics(template, rotation, mechanics)

    assert out["status"] in ("passed", "unproven"), out["issues"]
    generated = [row for row in out["elemental_trace"]
                 if str(row.get("event_id", "")).endswith(":application")]
    assert len(generated) == 1
    assert generated[0]["element"] == "cryo" and generated[0]["allowed"] is True
    assert generated[0]["before"]["auras"] == []
    assert generated[0]["after"]["auras"][0]["element"] == "cryo"
    later = next(row for row in out["resolved_template"]["hits"] if row["id"] == "h1")
    assert later["reaction"] == {"type": "amplify", "factor": 2.0}
    trace_types = [row.get("type") for row in out["mechanics_trace"]]
    damage_index = trace_types.index("reaction_damage")
    application_index = next(index for index, row in enumerate(out["mechanics_trace"])
                             if str(row.get("event_id", "")).endswith(":application")
                             and row.get("type") == "elemental_application")
    assert damage_index < application_index


def test_lunar_electro_tick_uses_live_per_owner_residuals_and_commits_exactly_once():
    special = special_runtime("lunar_electro")
    elemental_model = model([
        aura("electro", .2, "trigger.actor"),
        aura("hydro", .7, "qualification.provider"),
    ], freezable=False)
    from genshin_engine.elemental import ElementalRuntime
    elemental = ElementalRuntime(elemental_model, special_runtime=special)
    elemental.multi_state.add_or_refresh(
        "electro", 1, "consumer electro fixture", "consumer")
    elemental.multi_state.add_or_refresh(
        "hydro", .375, "consumer hydro fixture", "consumer")
    special.replace(special_reaction("lunar_electro"), time=0)

    elemental.advance(250_000)

    admission = next(row for row in elemental.generated_events
                     if row.get("type") == "special_reaction_tick_admission")
    assert set(admission["contributor_ids"]) == {
        "trigger.actor", "qualification.provider", "consumer"}
    assert admission["owner_residual_contract"] == (
        "same-element-owner-residual-max-v1")
    state = elemental._lunar_electro_aura_snapshot()
    assert state["components"]["electro"]["contributor_ids"] == ["consumer"]
    assert state["components"]["hydro"]["contributor_ids"] == [
        "qualification.provider"]
    assert state["components"]["electro"]["remaining_gu"] == pytest.approx(.39975)
    assert state["components"]["hydro"]["remaining_gu"] == pytest.approx(.29975)


def test_ordinary_crystallize_shard_pickup_is_queue_driven_and_inherits_live_sample():
    template, rotation, mechanics = _application_fixture(
        "geo", initial=[aura("hydro", .8, "hydro-owner")], times=(.2,))
    second_character(template, rotation, "hydro-owner")
    template["characters"][0]["stats"]["em"] = 1400
    mechanics["elemental_state_events"] = [{
        "id": "pickup-hydro-shard",
        "kind": "crystallize_shard_pickup",
        "t": .3,
        "order": 20,
        "target_id": "enemy",
        "shard_id": "h0:reaction:0:shard",
        "character": "a",
        "repeat": {"mode": "once"},
        "status": "confirmed",
        "source": "controlled crystallize pickup fixture",
    }]

    out = execute_mechanics(template, rotation, mechanics)

    assert out["status"] in ("passed", "unproven"), out["issues"]
    pickup = next(row for row in out["mechanics_trace"]
                  if row.get("type") == "crystallize_shard_pickup")
    assert pickup["shield_scope"] == "party"
    assert pickup["shield"]["source_owner"] == "a"
    assert pickup["shield"]["absorption"] > 1851
    assert pickup["shield"]["trigger_stat_sample"]["level"] == 90
    assert pickup["shield"]["trigger_stat_sample"]["em"] == 1400
    application = next(row for row in out["elemental_trace"]
                       if row.get("event_id") == "0:h0")
    shard = application["crystallize_shards"][0]
    assert shard["shield_absorption_sample"]["absorption"] == pytest.approx(
        pickup["shield"]["absorption"])
    assert shard["trigger_stat_sample"]["phase"] == "reaction_created"


def test_lunar_crystallize_matches_one_ordinary_crystallize_listener_via_stable_alias():
    template, rotation, mechanics = _application_fixture(
        "geo", initial=[aura("hydro", .8, "hydro-owner")], times=(.2,))
    second_character(template, rotation, "hydro-owner")
    _enable(mechanics, "lunar_crystallize")
    add(mechanics, rule(
        "ordinary-crystallize-listener",
        trigger={"event": "elemental_reaction", "phase": "post",
                 "reaction_types": ["crystallize"]},
    ), rule(
        "both-crystallize-tags-listener",
        trigger={"event": "elemental_reaction", "phase": "post",
                 "reaction_types": ["crystallize", "lunar_crystallize"]},
    ))

    out = execute_mechanics(template, rotation, mechanics)

    assert out["status"] in ("passed", "unproven"), out["issues"]
    reaction = next(row for row in out["mechanics_trace"]
                    if row.get("type") == "elemental_reaction"
                    and row.get("reaction_type") == "lunar_crystallize")
    assert reaction["reaction_trigger_tags"] == [
        "crystallize", "lunar_crystallize"]
    activations = [row for row in out["mechanics_trace"]
                   if row.get("type") == "activation"
                   and row.get("rule_id") == "ordinary-crystallize-listener"]
    assert len(activations) == 1
    assert activations[0]["event_id"] == reaction["event_id"]
    both = [row for row in out["mechanics_trace"]
            if row.get("type") == "activation"
            and row.get("rule_id") == "both-crystallize-tags-listener"]
    assert len(both) == 1
    assert both[0]["event_id"] == reaction["event_id"]


def test_lunar_bloom_operation_resolves_actor_and_consumes_resource_without_damage():
    template, rotation, mechanics = _application_fixture(
        "hydro", initial=[aura("dendro", .7, "a")])
    _enable(mechanics, "lunar_bloom")
    mechanics["special_reaction_events"] = [{
        "id": "consume-dew", "kind": "lunar_bloom_consume", "action_id": "cast",
        "target_id": "enemy", "area_id": "battlefield.primary", "t": 3,
        "order": 20, "dew": 1, "status": "confirmed", "source": "consumer fixture",
    }]

    out = execute_mechanics(template, rotation, mechanics)

    assert out["status"] in ("passed", "unproven"), out["issues"]
    direct = [row for row in out["resolved_template"]["hits"]
              if row.get("reaction", {}).get("type") == "lunar_bloom"
              and row.get("damage_system") == "lunar"]
    assert direct == []
    resource = [row for row in out["special_reaction_trace"]
                if row["type"] == "special_reaction_resource"
                and row.get("operation") == "consume_dew"]
    assert len(resource) == 1 and resource[0]["status"] == "executed"
    assert resource[0]["lunar_damage_emitted"] is False


@pytest.mark.parametrize(("reaction_type", "trigger", "initial", "element"), [
    ("lunar_electro", "electro", [aura("hydro", .8, "a")], "electro"),
    ("lunar_bloom", "hydro", [aura("dendro", .8, "a")], "dendro"),
    ("lunar_crystallize", "geo", [aura("hydro", .8, "a")], "geo"),
])
def test_public_direct_lunar_damage_operation_projects_one_unweighted_owner_hit(
        reaction_type, trigger, initial, element):
    template, rotation, mechanics = _application_fixture(
        trigger, initial=initial, times=(.2,))
    template["characters"][0]["stats"]["em"] = 500
    rotation["abilities"][0].update(variant="tap", effective_level=10)
    _enable(mechanics, reaction_type)
    profile = direct_lunar_profile(reaction_type)
    profile["ability_binding"] = {
        "character_id": "a",
        "ability_id": "e",
        "component_id": profile["parameters"]["component"],
        "variant_id": "tap",
        "effective_level": 10,
        "database_fingerprint": "1" * 64,
    }
    profile["profile_content_hash"] = direct_lunar_damage_profile_content_hash(
        profile)
    mechanics["special_reaction_events"] = [{
        "id": f"direct-{reaction_type}", "kind": "direct_lunar_damage",
        "action_id": "cast", "target_id": "enemy",
        "area_id": "battlefield.primary", "t": 3, "order": 20,
        "ability_profile": profile,
        "status": profile["status"], "source": "reviewed operation fixture",
    }]

    out = execute_mechanics(template, rotation, mechanics)

    assert out["status"] in ("passed", "unproven"), out["issues"]
    resolved = [row for row in out["resolved_template"]["hits"]
                if row.get("extensions", {}).get("special_reaction", {}).get(
                    "reaction_base") is False]
    assert len(resolved) == 1
    assert resolved[0]["character"] == "a"
    assert resolved[0]["element"] == element
    assert resolved[0]["reaction"]["type"] == reaction_type
    assert resolved[0]["description"].startswith("由显式假设能力profile生成；")
    assert "result_weight" not in resolved[0]
    result = next(row for row in out["result"]["hits"]
                  if row["id"] == resolved[0]["id"])
    assert result["result_weight"] == 1
    projection = next(row for row in out["special_reaction_trace"]
                      if row.get("type") == "special_reaction_damage_projection"
                      and row.get("request_id") == f"0:direct-{reaction_type}")
    assert projection["reaction_type"] == reaction_type
    assert projection["contributor_weighting"] == "not_applicable"


def test_public_legacy_lunar_profile_cannot_attach_direct_damage_to_unqualified_action():
    template, rotation, mechanics = _application_fixture(
        "hydro", initial=[aura("dendro", .8, "a")], times=(.2,))
    second_character(template, rotation, "b")
    rotation["abilities"][0]["character"] = "b"
    rotation["initial_frontline"] = "b"
    template["hits"][0]["character"] = "b"
    _enable(mechanics, "lunar_bloom", provider="a")
    profile = direct_lunar_profile("lunar_bloom")
    profile.pop("ability_binding")
    profile["profile_content_hash"] = direct_lunar_damage_profile_content_hash(
        profile)
    mechanics["special_reaction_events"] = [{
        "id": "legacy-direct-lunar",
        "kind": "direct_lunar_damage",
        "action_id": "cast",
        "target_id": "enemy",
        "area_id": "battlefield.primary",
        "t": 3,
        "order": 20,
        "ability_profile": profile,
        "status": profile["status"],
        "source": "legacy migration adversarial fixture",
    }]

    out = execute_mechanics(template, rotation, mechanics)

    assert out["status"] == "pending"
    migration = next(row for row in out["issues"]
                     if row["code"]
                     == "direct_lunar_profile_ability_binding_required")
    assert migration["severity"] == "pending"
    assert out["result"] is None and out["resolved_template"] is None


@pytest.mark.parametrize('area_id', ['battlefield.primary', 'arbitrary.area'])
@pytest.mark.parametrize('same_area', [True, False])
def test_stellar_superconduct_uses_settled_area_snapshot_for_layers_and_ordinary_buffs(area_id, same_area):
    template, rotation, mechanics = _application_fixture(
        "electro", initial=[aura("cryo", .8, "a")],
        times=(.2, 1.0, 4.5, 4.6, 4.7))
    _enable(mechanics, "stellar_superconduct")
    mechanics["hit_events"][1]["application"] = {
        "profile_id": "cryo", "target_id": "enemy", "landed": True,
        "area_id": "battlefield.primary",
    }
    template["hits"][1]["element"] = "cryo"
    # These later hits deal damage without a separate elemental application.
    # Keep them as ordinary inputs: stellar damage itself must be generated by
    # the runtime rather than prefilled beside the elemental state machine.
    mechanics["elemental_model"]["profiles"].extend([
        {"id": "no-cryo", "element": "cryo", "application": "none", **EVIDENCE},
        {"id": "no-physical", "element": "physical", "application": "none", **EVIDENCE},
    ])
    template["hits"][2]["element"] = "cryo"
    template["hits"][3]["element"] = "cryo"
    template["hits"][4]["element"] = "physical"
    for index in (2, 3):
        mechanics["hit_events"][index]["application"] = {
            "profile_id": "no-cryo", "target_id": "enemy", "landed": True,
        }
    mechanics["hit_events"][4]["application"] = {
        "profile_id": "no-physical", "target_id": "enemy", "landed": True,
    }
    for index, descriptor in enumerate(mechanics['hit_events']):
        descriptor['application']['area_id'] = area_id if same_area or index < 2 else 'other.unestablished.area'

    out = execute_mechanics(template, rotation, mechanics)

    assert out["status"] in ("passed", "unproven"), out["issues"]
    projected = {row["id"]: row for row in out["result"]["hits"]}
    assert projected["h3"]["expected"] == pytest.approx(58.05 if same_area else 45)
    assert projected["h4"]["expected"] == pytest.approx(57.5 if same_area else 45)
    applied = [row for row in out['mechanics_trace'] if row.get('type') == 'special_reaction_effect_applied']
    # The hit at 1s now correctly consumes the field foundation before the
    # first settlement, independently of the later hits' area.
    assert next(row for row in applied if row['event_id'] == 'h1')['value'] == pytest.approx(.2)
    assert bool([row for row in applied if row['event_id'] in ('h3', 'h4')]) is same_area
    assert all(row['area_id'] == area_id for row in applied)
    settlement = next(row for row in out["special_reaction_trace"]
                      if row.get("operation") == "settle_four_second_window")
    assert settlement["layers"] == 1


def test_direct_stellar_superconduct_hit_samples_runtime_area_layers():
    class _Runtime:
        def stellar_superconduct_snapshot(self, area_id):
            assert area_id == "battlefield.primary"
            return {"layers": 3, "settled_buff_active": True}

    runner = object.__new__(_Mechanics)
    runner.elemental = SimpleNamespace(special_runtime=_Runtime())
    runner.trace = []
    runner.time = 5_000_000
    hit = {
        "id": "direct-stellar", "damage_system": "stellar", "element": "cryo",
        "reaction": {"type": "stellar_superconduct", "layers": 0},
        "extensions": {"elemental": {"area_id": "battlefield.primary"}},
    }

    runner._sample_stellar_superconduct(hit, {"origin": 0})

    assert hit["reaction"]["layers"] == 3
    assert hit["extensions"]["special_reaction"]["layer_source"] == "runtime_area_snapshot"
    assert runner.trace[0]["declared_layers"] == 0


def _projection_runner(*, owner="hydro", time=0):
    runner = object.__new__(_Mechanics)
    runner.time = time
    runner.data = {
        "chars": {owner: {}},
        "actions": {"cast": {"ability_id": "skill"}},
        "abilities": {"skill": {
            "character": owner, "variant": "default", "effective_level": 10,
        }},
    }
    runner.elemental = SimpleNamespace(target="enemy", trace=[])
    runner.trace = []
    runner.elemental_bridge = MechanicsElementalBridge(MechanicsElementalPorts(
        get_elemental=runner._current_elemental,
        get_special_runtime=runner._special_runtime,
        get_time=runner._current_time,
        append_trace=runner._append_runtime_trace,
        count_event=lambda kind=None: None,
        dispatch_generated=lambda row, cycle, parent: False,
    ))
    runner.ordinary_bloom_damage_windows = {}
    runner.conditions = {}
    runner.captured_hits = []
    runner._count_event = lambda kind=None: None
    runner.snapshot = dict
    runner.phase = lambda event, phase, parent: None
    runner.hit = lambda event, payload: runner.captured_hits.append(payload["hit"])
    return runner


def _projection_lunar_profile(*, owner="a"):
    profile = direct_lunar_bloom_profile()
    profile["ability_binding"] = {
        "character_id": owner,
        "ability_id": "skill",
        "component_id": profile["parameters"]["component"],
        "variant_id": "default",
        "effective_level": 10,
        "database_fingerprint": "1" * 64,
    }
    profile["profile_content_hash"] = direct_lunar_damage_profile_content_hash(
        profile)
    return profile


def test_ordinary_bloom_natural_expiry_samples_owner_panel_at_detonation_time():
    template, rotation, mechanics = _application_fixture(
        "hydro", initial=[aura("dendro", .7, "a")])
    _enable(mechanics, "lunar_bloom")
    template["buffs"].append({
        "id": "late-em", "source": "detonation-time fixture", "scope": "team",
        "target": "a", "start": 6, "end": 7,
        "effects": [{
            "id": "em", "benefit_type_id": "benefit.em.all",
            "zone": "em", "stat": "all", "value": 100,
            "applies_to": {"mode": "all"},
        }],
    })

    out = execute_mechanics(template, rotation, mechanics)

    assert out["status"] in ("passed", "unproven"), out["issues"]
    hit = next(row for row in out["result"]["hits"]
               if row["damage_system"] == "transformative"
               and str(row["id"]).endswith(":core:detonate"))
    expected = 1446.85 * 2 * (1 + 16 * 100 / 2100) * .9
    assert hit["t"] == pytest.approx(6.2)
    assert hit["character"] == "a"
    assert hit["expected"] == pytest.approx(expected)
    resolved = next(row for row in out["resolved_template"]["hits"] if row["id"] == hit["id"])
    assert resolved["extensions"]["elemental"]["owner_sample_phase"] == "core_detonation"
    assert resolved["extensions"]["elemental"]["target_sample_phase"] == "core_detonation"


def test_sixth_ordinary_core_and_simultaneous_expiry_obey_two_hit_sequence_limit():
    cores = BloomCoreState()
    generated = []
    for index, tick in enumerate((0, 10_000, 20_000, 30_000, 40_000, 50_000), 1):
        receipt = cores.create(
            time=tick, event_id=f"core.{index}", target_id="enemy",
            area_id="battlefield.primary", owner="hydro",
            parent_reaction_id=f"bloom.{index}", contributor_ids=["hydro"],
            source="six-core fixture",
        )
        generated.extend(receipt["generated_events"])
    generated.extend(cores.advance(6_050_000))
    assert len(generated) == 6
    assert generated[0]["reason"] == "field_limit_oldest"

    runner = _projection_runner(owner="hydro")
    parent = {"order": 0, "causal": ()}
    for request in sorted(generated, key=lambda row: row["time_tick"]):
        runner.time = request["time_tick"]
        runner._project_ordinary_bloom_damage(request, 0, parent)

    admissions = [row for row in runner.trace
                  if row["type"] == "ordinary_bloom_damage_admission"]
    assert len(admissions) == 6
    assert [row["allowed"] for row in admissions] == [True, True, True, False, False, False]
    assert admissions[0]["reason"] == "first_damage_attempt"
    assert admissions[1]["reason"] == "damage_sequence_reset"
    assert len(runner.captured_hits) == 3
    assert runner.captured_hits[0]["hit_name"] == "草原核爆炸（场上上限）"
    assert all(hit["character"] == "hydro" for hit in runner.captured_hits)


def test_bountiful_or_incomplete_core_contract_fails_closed_before_projection():
    core = BloomCoreState()
    core.create(
        time=0, event_id="core", target_id="enemy", area_id="battlefield.primary",
        owner="hydro", parent_reaction_id="bloom", contributor_ids=["hydro"],
        source="variant fixture",
    )
    request = core.advance(6*TICKS)[0]
    request["core_variant"] = "bountiful"
    runner = _projection_runner(owner="hydro", time=6*TICKS)

    with pytest.raises(ElementalPending) as error:
        runner._project_ordinary_bloom_damage(request, 0, {"order": 0, "causal": ()})

    assert error.value.issue["code"] == "bloom_core_variant_unreviewed"
    assert runner.trace == [] and runner.captured_hits == []


def test_direct_lunar_bloom_uses_one_owner_live_scaling_and_never_enters_weighting():
    profile = _projection_lunar_profile()
    request = {
        "type": "special_reaction_damage_request", "event_id": "direct-lb",
        "t": 2, "time_tick": 2*TICKS, "target_id": "enemy", "area_id": None,
        "owner": "a", "action_id": "cast", "parent_reaction_id": None,
        "reaction_type": "lunar_bloom", "damage_system": "lunar",
        "reaction_base": False, "system_factor": 1, "element": "dendro",
        "factor_policy": "engine_constant", "application": None,
        "multiplier": 2.4, "scaling_stat": "em", "count": 2,
        "component": "reviewed_ability_direct_lunar_bloom",
        "contributor_ids": [], "contributor_sample_policy": None,
        "actor_sample_policy": "event_time_owner_attribute",
        "profile": {"profile_id": "native.lunar_bloom.v1"},
        "ability_profile": profile, "status": "ready_for_stat_sample",
    }
    runner = _projection_runner(owner="a", time=2*TICKS)
    runner._project_special_damage(request, 0, {"order": 0, "causal": ()})

    assert len(runner.captured_hits) == 1
    hit = runner.captured_hits[0]
    assert hit["reaction_base"] is False
    assert hit["character"] == "a" and hit["scaling_stat"] == "em"
    assert hit["multiplier"] == 2.4 and hit["count"] == 2
    assert "result_weight" not in hit
    assert hit["extensions"]["special_reaction"]["contributor_weighting"] == "not_applicable"


@pytest.mark.parametrize(("field", "value"), [
    ("system_factor", 99),
    ("factor_policy", "caller_supplied"),
    ("application", {"element": "dendro"}),
])
def test_direct_lunar_projection_rejects_bridge_tamper(field, value):
    profile = _projection_lunar_profile()
    request = {
        "type": "special_reaction_damage_request", "event_id": "direct-lb-tamper",
        "t": 2, "time_tick": 2*TICKS, "target_id": "enemy", "area_id": None,
        "owner": "a", "action_id": "cast", "parent_reaction_id": None,
        "reaction_type": "lunar_bloom", "damage_system": "lunar",
        "reaction_base": False, "system_factor": 1,
        "factor_policy": "engine_constant", "application": None,
        "element": "dendro", "multiplier": 2.4, "scaling_stat": "em", "count": 2,
        "component": "reviewed_ability_direct_lunar_bloom",
        "contributor_ids": [], "contributor_sample_policy": None,
        "actor_sample_policy": "event_time_owner_attribute",
        "profile": {"profile_id": "native.lunar_bloom.v1"},
        "ability_profile": profile, "status": "ready_for_stat_sample",
    }
    request[field] = value
    runner = _projection_runner(owner="a", time=2*TICKS)

    with pytest.raises(ElementalPending) as error:
        runner._project_special_damage(request, 0, {"order": 0, "causal": ()})

    assert error.value.issue["code"] == "direct_lunar_damage_contract_mismatch"
    assert runner.captured_hits == []


def test_bound_direct_lunar_projection_rejects_missing_action_ability_normally():
    profile = direct_lunar_bloom_profile()
    profile["ability_binding"] = {
        "character_id": "a",
        "ability_id": "skill",
        "component_id": profile["parameters"]["component"],
        "variant_id": "tap",
        "effective_level": 10,
        "database_fingerprint": "1" * 64,
    }
    profile["profile_content_hash"] = direct_lunar_damage_profile_content_hash(
        profile)
    request = {
        "type": "special_reaction_damage_request",
        "event_id": "direct-lb-missing-ability",
        "t": 2,
        "time_tick": 2*TICKS,
        "target_id": "enemy",
        "area_id": "battlefield.primary",
        "owner": "a",
        "action_id": "cast",
        "parent_reaction_id": None,
        "reaction_type": "lunar_bloom",
        "damage_system": "lunar",
        "reaction_base": False,
        "system_factor": 1,
        "factor_policy": "engine_constant",
        "application": None,
        "element": "dendro",
        "multiplier": 2.4,
        "scaling_stat": "em",
        "count": 2,
        "component": profile["parameters"]["component"],
        "contributor_ids": [],
        "contributor_sample_policy": None,
        "actor_sample_policy": "event_time_owner_attribute",
        "profile": {"profile_id": "native.lunar_bloom.v1"},
        "ability_profile": profile,
        "status": "ready_for_stat_sample",
    }
    runner = _projection_runner(owner="a", time=2*TICKS)
    runner.data["abilities"] = {}

    with pytest.raises(ElementalPending) as error:
        runner._project_special_damage(
            request, 0, {"order": 0, "causal": ()})

    assert error.value.issue["code"] == "direct_lunar_damage_contract_mismatch"
    assert runner.captured_hits == []
