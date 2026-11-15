"""Independent EC20 qualification checks; no fresh authority or game claims."""
from __future__ import annotations

import copy

import pytest

from genshin_runtime.mechanism_semantics import descriptor_fingerprint, fingerprint
from tests.mechanism_source_fixtures import (  # noqa: F401
    fake_repo,
    fixture_qualified_catalog,
    mechanism_db,
    mechanism_repo,
)
from tests.test_mechanism_descriptor_review import (
    compile_input,
    pending,
    rehash,
    review_material,
)
from tests.test_mechanism_parent_graph import addition_graph  # noqa: F401

# ruff: noqa: F811


def qualified_material():
    pair = review_material()
    descriptor, inputs = pair
    claim = {
        "schema_version": 1,
        "parent": {
            "profile_kind": "calibrated_single_contact_v1",
            "selection": {"slot": "skill", "constellation": 0, "talent_level": 10,
                          "skill_id": 123, "variant": "hold"},
            "required_receipts": ["action_event", "hit_parameter"],
            "parameter_binding": {"node_id": "parent-proud", "pointer": "/paramList/1", "param_index": 1},
            "hit": {"damage_systems": ["classic"], "count": 1, "scaling_stats": ["atk"],
                    "elements": ["cryo"], "damage_type_ids": ["damage.elemental_skill"]},
        },
        "coverage": {"action_filter": {"action_type_ids": ["action.elemental_skill"]},
                     "application_elements": [], "reject_unknown_application": True},
        "consumer": {"damage_systems": ["classic"], "count": 1,
                     "damage_type_ids": ["damage.normal_attack", "damage.charged_attack",
                                         "damage.plunging_attack", "damage.elemental_skill", "damage.elemental_burst"]},
        "scenario_requirements": {"initial_effect": "none", "all_party_alive": True,
                                  "hitlag_policy": "zero_hitlag_reference"},
        "duplicate_identity": {"kind": "skill", "source_id": 123, "parts": ["addition"]},
    }
    fact = {"id": "review-qualification", "revision": 1, "kind": "execution_qualification",
            "claim": claim, "evidence_tier": "experimental_reference", "evidence_refs": ["parent-proud"]}
    raw = {"proudSkillId": 12310, "paramList": [2, 2, 2]}
    proof = inputs["source_proof"]
    proof["nodes"].append({"id": "parent-proud", "source_path": "synthetic/parent.json",
                           "source_sha256": fingerprint(raw), "json_pointer": "/0",
                           "node_sha256": fingerprint(raw), "payload": raw})
    proof["evidence"].append({"id": "parent-proud", "kind": "runtime_node",
                              "locator": "synthetic/parent.json#/0", "sha256": fingerprint(raw)})
    inputs["facts"].append(fact)
    descriptor["evidence_fact_ids"].append("review-qualification@1")
    proof["review"]["fact_statuses"]["review-qualification@1"] = "approved"
    rehash(*pair)
    return pair


def qualification(pair):
    return next(f for f in pair[1]["facts"] if f["kind"] == "execution_qualification")["claim"]


def refresh_claim_hash(pair):
    # Canonical hash only: malformed typed fields must reach public validation,
    # not be rejected by a fixture's validate_fact call before the actual test.
    for fact in pair[1]["facts"]:
        if fact["kind"] == "execution_qualification":
            pair[1]["source_proof"]["fact_fingerprints"][f"{fact['id']}@{fact['revision']}"] = fingerprint(fact)


def assert_qualification_pending(out):
    pending(out)
    assert out["execution_qualification"] is None
    assert out["qualification_status"] != "consistent"


def test_review_legacy_unbound_ready_is_explicitly_not_qualified():
    out = compile_input(review_material())
    assert out["status"] == "ready"
    assert out["qualification_status"] == "missing"
    assert out["execution_qualification"] is None
    assert out["execution_status"] == "not_evaluated"


def test_review_valid_qualification_is_evidence_not_execution_authorization():
    pair = qualified_material()
    original = copy.deepcopy(pair)
    out = compile_input(pair)
    assert out["status"] == "ready", out
    assert out["qualification_status"] == "consistent"
    assert out["execution_status"] == "not_evaluated"
    assert out["execution_qualification"] == qualification(pair)
    assert pair == original
    out["execution_qualification"]["parent"]["selection"]["variant"] = "tap"
    assert compile_input(pair)["execution_qualification"]["parent"]["selection"]["variant"] == "hold"


@pytest.mark.parametrize("pointer,index", [("/paramList/0", 1), ("/paramList/01", 1),
                                           ("/paramList/1", 0), ("/paramList/64", 64)])
def test_review_parent_pointer_and_index_must_agree_even_for_equal_values(pointer, index):
    pair = qualified_material()
    qualification(pair)["parent"]["parameter_binding"].update(pointer=pointer, param_index=index)
    refresh_claim_hash(pair)
    assert_qualification_pending(compile_input(pair))


@pytest.mark.parametrize("case", ["missing_node", "unreferenced_node", "nonscalar", "bool_scalar"])
def test_review_parent_parameter_must_have_exact_audited_scalar_evidence(case):
    pair = qualified_material()
    if case == "missing_node":
        qualification(pair)["parent"]["parameter_binding"]["node_id"] = "not-in-proof"
    elif case == "unreferenced_node":
        pair[1]["facts"][-1]["evidence_refs"] = ["review-reference"]
    else:
        node = pair[1]["source_proof"]["nodes"][-1]
        node["payload"]["paramList"][1] = [2] if case == "nonscalar" else True
        node["node_sha256"] = node["source_sha256"] = fingerprint(node["payload"])
        pair[1]["source_proof"]["evidence"][-1]["sha256"] = node["node_sha256"]
    refresh_claim_hash(pair)
    assert_qualification_pending(compile_input(pair))


@pytest.mark.parametrize("case", ["no_consumer", "no_hit_parameter", "no_parameter_binding", "unknown_application",
                                  "parent_count", "consumer_count", "bool_constellation", "scene_false"])
def test_review_qualification_cannot_remove_required_execution_boundaries(case):
    pair = qualified_material()
    claim = qualification(pair)
    if case == "no_consumer":
        claim["consumer"] = None
    elif case == "no_hit_parameter":
        claim["parent"]["required_receipts"] = ["action_event"]
    elif case == "no_parameter_binding":
        claim["parent"]["parameter_binding"] = None
    elif case == "unknown_application":
        claim["coverage"]["reject_unknown_application"] = False
    elif case == "parent_count":
        claim["parent"]["hit"]["count"] = 2
    elif case == "consumer_count":
        claim["consumer"]["count"] = 2
    elif case == "bool_constellation":
        claim["parent"]["selection"]["constellation"] = False
    else:
        claim["scenario_requirements"]["all_party_alive"] = False
    refresh_claim_hash(pair)
    assert_qualification_pending(compile_input(pair))


def test_review_two_qualifications_are_not_merged_or_first_selected():
    pair = qualified_material()
    duplicate = copy.deepcopy(pair[1]["facts"][-1])
    duplicate["id"] = "other-qualification"
    pair[1]["facts"].append(duplicate)
    pair[0]["evidence_fact_ids"].append("other-qualification@1")
    pair[1]["source_proof"]["review"]["fact_statuses"]["other-qualification@1"] = "approved"
    pair[1]["source_proof"]["descriptor_fingerprint"] = descriptor_fingerprint(pair[0])
    refresh_claim_hash(pair)
    assert_qualification_pending(compile_input(pair))


def test_review_revocation_cannot_leave_executable_qualification_residue():
    pair = qualified_material()
    pair[1]["source_proof"]["review"]["fact_statuses"]["review-qualification@1"] = "revoked"
    assert_qualification_pending(compile_input(pair))


def test_review_def_is_a_supported_stat_combination_not_an_atk_name_alias():
    pair = qualified_material()
    item = pair[0]["rule_templates"][0]
    item["rule"]["effects"][0]["value"]["stat"] = "def"
    pair[1]["facts"][0]["claim"] = copy.deepcopy(item)
    qualification(pair)["parent"]["hit"]["scaling_stats"] = ["def"]
    rehash(*pair)
    out = compile_input(pair)
    assert out["status"] == "ready", out
    assert out["execution_qualification"]["parent"]["hit"]["scaling_stats"] == ["def"]
    assert out["rule_templates"][0]["rule"]["effects"][0]["value"]["stat"] == "def"


@pytest.mark.parametrize("metadata", [True, False])
def test_review_reaction_qualification_is_distinct_and_requires_attack_evidence(metadata):
    pair = qualified_material()
    item = pair[0]["rule_templates"][0]
    item["rule"].pop("quota")
    item["rule"]["trigger"] = {"event": "elemental_reaction", "phase": "post", "reaction_types": ["swirl"],
                                "reaction_elements": ["cryo"], "owner_presence": "any"}
    item["rule"]["effects"][0].update(benefit_type_id="benefit.dmg_bonus.cryo", domain="self")
    item["rule"]["effects"][0]["value"].update(stat="em", sample="trigger")
    item["value_bindings"] = [b for b in item["value_bindings"] if b["parameter_ref"] != "capacity"]
    item["execution_ports"][1] = {"target": "trigger.characters", "role": "provider_characters"}
    item["requirements"] = ["qualified_reaction_origin", "no_dynamic_em", "fixed_nonextra_em_equality"]
    pair[1]["facts"][0]["claim"] = copy.deepcopy(item)
    pair[1]["facts"] = [f for f in pair[1]["facts"] if f["id"] != "role-capacity"]
    pair[1]["parameters"] = [p for p in pair[1]["parameters"] if p["ref"] != "capacity"]
    pair[0]["evidence_fact_ids"].remove("role-capacity@1")
    pair[1]["source_proof"]["review"]["fact_statuses"].pop("role-capacity@1")
    claim = qualification(pair)
    claim["parent"]["parameter_binding"] = None
    claim["parent"]["required_receipts"] = ["action_event"] + (["attack_metadata"] if metadata else [])
    claim["parent"]["hit"]["elements"] = ["anemo"]
    claim["coverage"]["application_elements"] = ["anemo"]
    claim["consumer"] = None
    claim["scenario_requirements"].update(stat_basis="fixed_panel_nonextra_equals_total",
                                          recipient_bonus_policy="not_included")
    rehash(*pair)
    out = compile_input(pair)
    if metadata:
        assert out["status"] == "ready", out
        assert out["qualification_status"] == "consistent"
        assert out["execution_qualification"]["consumer"] is None
    else:
        assert_qualification_pending(out)


def test_review_two_real_sources_gain_data_qualification_without_changing_algorithms(mechanism_db):
    from genshin_db.runtime_queries import query_runtime
    from genshin_db.semantic_store import import_bundle

    ids = ("skill.current-atk-limited-addition", "passive.swirl-elemental-bonus")
    def query(ident):
        return query_runtime(mechanism_db, {"action": "mechanism_profile", "mechanism_id": ident})
    old = [query(ident) for ident in ids]
    assert all(x["compiler_result"]["qualification_status"] == "missing" for x in old)
    assert import_bundle(mechanism_db, fixture_qualified_catalog(mechanism_db), dry_run=False)["applied"]
    current = [query(ident) for ident in ids]
    for before, after in zip(old, current, strict=True):
        result = after["compiler_result"]
        assert after["status"] == "ready"
        assert result["qualification_status"] == "consistent"
        assert result["execution_status"] == "not_evaluated"
        assert result["rule_templates"] == before["compiler_result"]["rule_templates"]
    addition, reaction = [x["compiler_result"]["execution_qualification"] for x in current]
    assert addition["parent"]["parameter_binding"]["pointer"] == "/paramList/1"
    assert addition["consumer"]["count"] == 1
    assert reaction["parent"]["parameter_binding"] is None and reaction["consumer"] is None
    assert "attack_metadata" in reaction["parent"]["required_receipts"]


@pytest.mark.parametrize("case", ["equal_scalar_wrong_pointer", "different_depot", "unknown_application", "empty_filter_extra_action"])
def test_review_parent_graph_exact_identity_and_full_coverage(addition_graph, case):
    from genshin_app.equipment.mechanism_parent_graph import (
        build_mechanism_parent_graph,
    )

    plan, template, args = copy.deepcopy(addition_graph)
    assert build_mechanism_parent_graph(plan, template, **args)["status"] == "ready"
    if case == "equal_scalar_wrong_pointer":
        # Leave multiplier and all other receipt fields unchanged.
        args["parameter_records"][0]["source"]["parameter_pointer"] = "/0/paramList/0"
    elif case == "different_depot":
        args["parameter_records"][0]["resolved_selection"]["skill_depot_id"] += 1
    elif case == "unknown_application":
        model = plan["mechanics_model"]
        q = args["qualification"]
        q["coverage"]["application_elements"] = ["anemo"]
        model["elemental_model"]["profiles"].append({"id": "unknown-extra", "application": "unknown"})
        # This event belongs to an otherwise unrelated actor action; an unknown
        # application still cannot prove it is outside the coverage union.
        ab = copy.deepcopy(plan["rotation_model"]["abilities"][0])
        ab.update(id="unrelated", action_type_id="action.normal_attack")
        plan["rotation_model"]["abilities"].append(ab)
        action = copy.deepcopy(plan["rotation_model"]["actions"][0])
        action.update(id="unrelated-cast", ability_id="unrelated", start=2, occupation_end=3)
        plan["rotation_model"]["actions"].append(action)
        model.setdefault("application_events", []).append({"id": "unknown-event", "action_id": action["id"],
            "t": 2.2, "order": 3, "application": {"profile_id": "unknown-extra", "target_id": "enemy", "landed": True}})
    else:
        args["qualification"]["coverage"]["action_filter"] = {}
        action = copy.deepcopy(plan["rotation_model"]["actions"][0])
        action.update(id="uncovered-no-hit", start=2, occupation_end=3)
        plan["rotation_model"]["actions"].append(action)
    out = build_mechanism_parent_graph(plan, template, **args)
    assert out["status"] == "pending", out
    assert out["parents"] == out["dependencies"] == []
    assert out["graph_fingerprint"] is None


def test_review_null_filter_cannot_disable_both_coverage_branches():
    pair = qualified_material()
    qualification(pair)["coverage"]["action_filter"] = None
    refresh_claim_hash(pair)
    assert_qualification_pending(compile_input(pair))


@pytest.mark.parametrize("application", ["none", "unknown"])
def test_review_application_only_coverage_distinguishes_known_none_from_unknown(addition_graph, application):
    from genshin_app.equipment.mechanism_parent_graph import (
        build_mechanism_parent_graph,
    )

    plan, template, args = copy.deepcopy(addition_graph)
    model = plan["mechanics_model"]
    main_id = plan["action_event_profiles"][0]["application_profile_id"]
    main = next(p for p in model["elemental_model"]["profiles"] if p["id"] == main_id)
    args["qualification"]["coverage"] = {
        "action_filter": None, "application_elements": [main["element"]], "reject_unknown_application": True,
    }
    # The actual accepted source parent remains unchanged. A separate anemo
    # contact has no qualified parent; only proven non-application is irrelevant.
    extra = copy.deepcopy(template["hits"][0])
    extra.update(id="wind-extra", element="anemo", damage_type_ids=["damage.normal_attack"])
    template["hits"].append(extra)
    model["elemental_model"]["profiles"].append({"id": "extra-app", "application": application})
    model["hit_events"].append({"id": "extra-event", "hit_id": extra["id"],
        "action_id": plan["rotation_model"]["actions"][0]["id"], "t": .3, "order": 99,
        "application": {"profile_id": "extra-app", "target_id": model["elemental_model"]["target"]["id"], "landed": True}})
    out = build_mechanism_parent_graph(plan, template, **args)
    assert out["status"] == ("ready" if application == "none" else "pending"), out
    if application == "unknown":
        assert out["parents"] == out["dependencies"] == []
