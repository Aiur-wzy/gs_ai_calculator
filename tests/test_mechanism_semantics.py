"""Catalog content typing, source consistency and bounded atomic compilation."""
import copy

import pytest

from genshin_engine.mechanism_descriptor_compiler import compile_mechanism_descriptor
from genshin_runtime import mechanism_semantics as semantics
from tests.test_mechanism_descriptor_compiler import case


def fact_record():
    return {"id": "scope", "revision": 1, "subject": {"target_type": "character", "target_id": 1},
        "payload": {"id": "scope", "revision": 1, "kind": "execution_requirement", "claim": {"requirement": "single_target"},
                    "evidence_tier": "experimental_reference", "evidence_refs": ["source"]},
        "evidence": [{"id": "source", "kind": "primary_test", "locator": "fixture://engineering", "sha256": "a"*64}],
        "applicability": {"version_line": "release", "upstream_commit": "b"*40, "normalization_fingerprint": "c"*64},
        "review": {"status": "approved", "reviewer": "engineering-fixture", "reviewed_at": "2026-10-03"}}


def test_wrapper_authorization_fingerprint_is_separate_and_payload_scope_sensitive():
    a = fact_record()
    b = copy.deepcopy(a)
    b["review"]["reviewer"] = "other-reviewer"
    assert semantics.validate_record(a, "fact") == a
    assert semantics.record_payload_fingerprint(a, "fact") == semantics.record_payload_fingerprint(b, "fact")
    assert semantics.record_review_fingerprint(a, "fact") != semantics.record_review_fingerprint(b, "fact")
    b["subject"]["target_id"] = 2
    assert semantics.record_payload_fingerprint(a, "fact") != semantics.record_payload_fingerprint(b, "fact")


@pytest.mark.parametrize("where,value", [("id", "other"), ("revision", True), ("subject", {"target_type": "character", "target_id": True}),
    ("review", {"status": "approved", "reviewer": "", "reviewed_at": "2026-10-03"}),
    ("review", {"status": "pending", "reviewer": "actor", "reviewed_at": None}),
    ("applicability", {"version_line": "release", "upstream_commit": "b"*40}),
    ("evidence", [{"id": "unrelated", "kind": "primary_test", "locator": "fixture", "sha256": "a"*64}])])
def test_record_invalid_review_or_scope_cannot_enter_store(where, value):
    record = fact_record()
    record[where] = value
    with pytest.raises(ValueError):
        semantics.validate_record(record, "fact")


def test_changing_display_name_preserves_semantic_identity():
    d, a = case()
    original = semantics.descriptor_fingerprint(d)
    original_fact = semantics.fact_fingerprint(a["facts"][0])
    d["rule_templates"][0]["rule"]["name_zh"] = "更好的显示名称"
    a["facts"][0]["claim"]["rule"]["name_zh"] = "其他显示名称"
    assert semantics.descriptor_fingerprint(d) == original
    assert semantics.fact_fingerprint(a["facts"][0]) == original_fact
    assert compile_mechanism_descriptor(d, **a)["status"] == "ready"


@pytest.mark.parametrize("change", ["rule_changed", "usage_swapped", "node_wrong", "missing_evidence", "wrong_locator"])
def test_rehashed_material_still_needs_complete_semantics_and_exact_numeric_origin(change):
    d, a = case()
    if change == "rule_changed":
        d["rule_templates"][0]["rule"]["effects"][0]["value"]["sample"] = "trigger"
        a["source_proof"]["descriptor_fingerprint"] = semantics.descriptor_fingerprint(d)
    if change == "usage_swapped":
        a["facts"][1]["claim"]["targets"][0]["target"] = "timing.cooldown"
        a["source_proof"]["fact_fingerprints"]["duration@1"] = semantics.fact_fingerprint(a["facts"][1])
    if change == "node_wrong":
        a["source_proof"]["nodes"][0]["payload"]["duration"] = 14
        a["source_proof"]["nodes"][0]["node_sha256"] = semantics.fingerprint(a["source_proof"]["nodes"][0]["payload"])
        a["source_proof"]["evidence"][0]["sha256"] = a["source_proof"]["nodes"][0]["node_sha256"]
    if change == "missing_evidence": a["source_proof"]["evidence"] = []
    if change == "wrong_locator": a["source_proof"]["evidence"][0]["locator"] = "unrelated#"
    assert compile_mechanism_descriptor(d, **a)["status"] == "pending"


@pytest.mark.parametrize("name", ["descriptor_bytes", "input_bytes", "output_bytes", "parameters", "facts", "nodes"])
def test_budget_failures_never_return_executable_partial_output(monkeypatch, name):
    d, a = case()
    monkeypatch.setitem(semantics.LIMITS, name, 0 if name == "nodes" else 1)
    value = compile_mechanism_descriptor(d, **a)
    assert value["status"] == "pending"
    assert value["rule_templates"] == value["initial_rule_templates"] == value["execution_ports"] == []


def test_json_depth_and_cyclic_inputs_are_bounded():
    value = []
    value.append(value)
    with pytest.raises(ValueError, match="预算"):
        semantics.finite_json(value)


def test_wholly_self_consistent_proof_is_not_claimed_as_fresh_authority():
    d, a = case()
    # The pure layer cannot establish that a caller's approvals came from our DB.
    value = compile_mechanism_descriptor(d, **a)
    assert value["status"] == "ready"
    assert value["source_status"] == value["fact_status"] == "consistent"
    assert value["verification_scope"] == "materialized_consistency_only"
    assert value["execution_status"] == "not_evaluated"


def test_omitting_actual_trigger_route_is_not_global_trigger_permission():
    d, a = case()
    d["rule_templates"][0]["execution_ports"] = [p for p in d["rule_templates"][0]["execution_ports"] if p["target"] != "trigger.action_ids"]
    assert compile_mechanism_descriptor(d, **a)["status"] == "pending"
