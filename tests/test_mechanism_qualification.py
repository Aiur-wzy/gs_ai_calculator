"""Author tests: typed execution scope is data, never a freshness authority."""
import copy

import pytest

from genshin_engine.mechanism_descriptor_compiler import compile_mechanism_descriptor
from genshin_runtime import mechanism_qualification as qualification
from genshin_runtime.mechanism_semantics import (
    descriptor_fingerprint,
    fact_fingerprint,
    fingerprint,
    mechanism_semantics_capabilities,
    record_payload_fingerprint,
    validate_fact,
    validate_record,
)
from tests.test_mechanism_descriptor_compiler import case
from tests.test_mechanism_semantics import fact_record


def claim(kind="flat"):
    reaction = kind == "swirl"
    return {"schema_version": 1,
        "parent": {"profile_kind": "calibrated_single_contact_v1",
            "selection": {"slot": "skill", "constellation": 0, "talent_level": 10},
            "required_receipts": ["action_event", "attack_metadata" if reaction else "hit_parameter"],
            "parameter_binding": None if reaction else {"node_id": "parent", "pointer": "/paramList/1", "param_index": 1},
            "hit": {"damage_systems": ["classic"], "count": 1, "scaling_stats": ["atk"],
                    "elements": ["anemo" if reaction else "cryo"], "damage_type_ids": ["damage.elemental_skill"]}},
        "coverage": {"action_filter": {"damage_type_ids": ["damage.elemental_skill"]},
                     "application_elements": ["anemo"] if reaction else [], "reject_unknown_application": True},
        "consumer": None if reaction else {"damage_systems": ["classic"], "count": 1,
                     "damage_type_ids": ["damage.normal_attack", "damage.elemental_skill"]},
        "scenario_requirements": {"target_kind": "ordinary_non_animal_monster", "all_party_alive": True,
                                  "no_external_source_changes": True},
        "duplicate_identity": {"kind": "skill", "source_id": 15, "parts": ["finite_add" if not reaction else "reaction_bonus"]}}


def refresh(d, args):
    p = args["source_proof"]
    d["evidence_fact_ids"] = [f["id"] + "@" + str(f["revision"]) for f in args["facts"]]
    p["descriptor_fingerprint"] = descriptor_fingerprint(d)
    p["fact_fingerprints"] = {f["id"] + "@" + str(f["revision"]): fact_fingerprint(f) for f in args["facts"]}
    p["review"]["fact_statuses"] = {ref: "approved" for ref in d["evidence_fact_ids"]}


def qualified_case(kind="flat"):
    d, args = case(kind)
    raw = {"id": 100, "paramList": [2.5, 2.5, 2.5]}
    args["source_proof"]["nodes"].append({"id": "parent", "source_path": "parent.json", "source_sha256": fingerprint(raw),
        "json_pointer": "/3", "node_sha256": fingerprint(raw), "payload": raw})
    args["source_proof"]["evidence"].append({"id": "parent", "kind": "runtime_node", "locator": "parent.json#/3", "sha256": fingerprint(raw)})
    args["facts"].append({"id": "qualification", "revision": 1, "kind": "execution_qualification",
        "claim": claim(kind), "evidence_tier": "experimental_reference", "evidence_refs": ["parent", "source"]})
    refresh(d, args)
    return d, args


@pytest.mark.parametrize("kind", ["flat", "swirl", "def"])
def test_qualification_is_isolated_consistency_not_execution_authority(kind):
    d, args = qualified_case(kind)
    original = copy.deepcopy((d, args))
    result = compile_mechanism_descriptor(d, **args)
    assert result["status"] == "ready", result["issues"]
    assert result["qualification_status"] == "consistent"
    assert result["execution_status"] == "not_evaluated"
    assert result["execution_qualification"] == claim(kind)
    result["execution_qualification"]["parent"]["selection"]["constellation"] = 6
    assert (d, args) == original
    assert compile_mechanism_descriptor(d, **args)["execution_qualification"] == claim(kind)


def test_legacy_scope_is_not_upgraded_to_execution_qualification():
    d, args = case()
    result = compile_mechanism_descriptor(d, **args)
    assert result["status"] == "ready"
    assert result["execution_qualification"] is None
    assert result["qualification_status"] == "missing"
    assert result["execution_status"] == "not_evaluated"
    assert "execution_qualification" in mechanism_semantics_capabilities()["fact_kinds"]


def test_application_only_qualification_keeps_action_filter_disabled_in_compilation():
    d, args = qualified_case("swirl")
    args["facts"][-1]["claim"]["coverage"]["action_filter"] = None
    refresh(d, args)
    result = compile_mechanism_descriptor(d, **args)
    assert result["status"] == "ready", result["issues"]
    assert result["execution_qualification"]["coverage"] == {
        "action_filter": None, "application_elements": ["anemo"], "reject_unknown_application": True}
    assert result["execution_status"] == "not_evaluated"


def test_disabled_filter_requires_application_scope_but_empty_filter_still_means_all():
    q = claim("swirl")
    q["coverage"].update(action_filter=None, application_elements=[])
    with pytest.raises(ValueError, match="覆盖范围不能全部关闭"):
        qualification.validate_execution_qualification(q)
    q["coverage"]["action_filter"] = {}
    assert qualification.validate_execution_qualification(q)["coverage"]["action_filter"] == {}


@pytest.mark.parametrize("path,value", [
    (("schema_version",), True), (("parent", "hit", "count"), True),
    (("parent", "hit", "count"), 2), (("parent", "selection", "constellation"), False),
    (("parent", "selection", "talent_level"), 0), (("parent", "selection", "skill_id"), 2**53),
    (("parent", "selection", "character_id"), 1), (("parent", "hit", "damage_type_ids"), ["damage.fake"]),
    (("parent", "parameter_binding", "param_index"), 64),
    (("parent", "parameter_binding", "param_index"), True),
    (("parent", "parameter_binding", "pointer"), "/paramList/2"),
    (("parent", "parameter_binding", "pointer"), "/paramList/01"),
    (("parent", "required_receipts"), ["hit_parameter"]),
    (("consumer", "damage_systems"), ["classic", "classic"]),
    (("consumer", "count"), 1.0), (("consumer", "damage_type_ids"), []),
    (("coverage", "action_filter", "event_ids"), ["local"]),
    (("coverage", "reject_unknown_application"), 1),
    (("scenario_requirements", "all_party_alive"), 1),
    (("scenario_requirements", "target_kind"), "all_targets"),
    (("scenario_requirements",), {}),
    (("duplicate_identity", "source_id"), True), (("duplicate_identity", "parts"), ["part", "part"]),
])
def test_claim_rejects_widened_or_untyped_qualification(path, value):
    q = claim()
    target = q
    for key in path[:-1]:
        target = target[key]
    target[path[-1]] = value
    with pytest.raises(ValueError):
        qualification.validate_execution_qualification(q)


@pytest.mark.parametrize("change", ["quota_without_consumer", "base_add_transformative", "action_without_parameter", "reaction_without_metadata", "two_qualifications", "revoked", "missing_parent", "parent_not_param_node", "parent_bool", "parent_not_runtime_evidence"])
def test_rule_source_and_qualification_fail_atomically(change):
    d, args = qualified_case("swirl" if change == "reaction_without_metadata" else "flat")
    fact = args["facts"][-1]
    if change == "quota_without_consumer":
        fact["claim"]["consumer"] = None
    elif change == "base_add_transformative":
        fact["claim"]["consumer"]["damage_systems"] = ["transformative"]
    elif change == "action_without_parameter":
        fact["claim"]["parent"]["parameter_binding"] = None
        fact["claim"]["parent"]["required_receipts"] = ["action_event"]
    elif change == "reaction_without_metadata":
        fact["claim"]["parent"]["required_receipts"] = ["action_event"]
    elif change == "two_qualifications":
        other = copy.deepcopy(fact)
        other["id"] = "second"
        args["facts"].append(other)
    elif change == "missing_parent":
        args["source_proof"]["nodes"].pop()
    elif change in ("parent_not_param_node", "parent_bool"):
        node = args["source_proof"]["nodes"][-1]
        node["payload"] = {"value": 2.5} if change == "parent_not_param_node" else {"paramList": [2.5, True]}
        node["node_sha256"] = fingerprint(node["payload"])
        args["source_proof"]["evidence"][-1]["sha256"] = node["node_sha256"]
    elif change == "parent_not_runtime_evidence":
        args["source_proof"]["evidence"][-1]["kind"] = "primary_test"
    refresh(d, args)
    if change == "revoked":
        args["source_proof"]["review"]["fact_statuses"]["qualification@1"] = "revoked"
    result = compile_mechanism_descriptor(d, **args)
    assert result["status"] == "pending", change
    assert result["execution_qualification"] is None and result["qualification_status"] == "pending"
    for key in ("rule_templates", "initial_rule_templates", "execution_ports", "parameter_resolutions", "fact_resolutions"):
        assert result[key] == []
    assert result["mechanism_fingerprint"] is None


def test_equal_value_wrong_pointer_cannot_reach_ready_even_when_outer_proof_is_rehashed():
    d, args = qualified_case()
    fact = args["facts"][-1]
    fact["claim"]["parent"]["parameter_binding"]["pointer"] = "/paramList/2"
    # All three raw numbers coincide: the origin contract, not equality, must reject.
    args["source_proof"]["fact_fingerprints"]["qualification@1"] = fingerprint(fact)
    result = compile_mechanism_descriptor(d, **args)
    assert result["status"] == "pending"
    assert "指针与序号" in result["issues"][0]["message"]


def test_reviewed_parent_slot_change_changes_mechanism_identity_not_numeric_template():
    d, args = qualified_case()
    original = compile_mechanism_descriptor(d, **args)
    binding = args["facts"][-1]["claim"]["parent"]["parameter_binding"]
    binding.update(pointer="/paramList/2", param_index=2)
    refresh(d, args)
    changed = compile_mechanism_descriptor(d, **args)
    assert changed["status"] == "ready"
    assert changed["rule_templates"] == original["rule_templates"]
    assert changed["mechanism_fingerprint"] != original["mechanism_fingerprint"]


def test_fact_catalog_wrapper_retains_qualification_identity_and_review_lifecycle():
    _, args = qualified_case()
    record = fact_record()
    record["id"] = "qualification"
    record["payload"] = args["facts"][-1]
    record["evidence"] = args["source_proof"]["evidence"]
    validated = validate_record(record, "fact")
    assert validated == record
    assert validate_fact(record["payload"])["kind"] == "execution_qualification"
    oldfp = record_payload_fingerprint(record, "fact")
    record["payload"]["claim"]["duplicate_identity"]["parts"] = ["other"]
    assert record_payload_fingerprint(record, "fact") != oldfp
    record["payload"]["evidence_refs"] = ["source"]
    with pytest.raises(ValueError, match="资格事实证据引用"):
        validate_record(record, "fact")


def test_qualification_budget_and_public_verifier_are_bounded_and_isolated(monkeypatch):
    d, args = qualified_case()
    q = args["facts"][-1]["claim"]
    original = copy.deepcopy(q)
    verified = qualification.verify_execution_qualification(q, descriptor=d, source_proof=args["source_proof"], evidence_refs=["source", "parent"])
    verified["duplicate_identity"]["parts"].append("other")
    assert q == original
    monkeypatch.setattr(qualification, "MAX_QUALIFICATION_BYTES", 100)
    result = compile_mechanism_descriptor(d, **args)
    assert result["status"] == "pending" and result["execution_qualification"] is None
