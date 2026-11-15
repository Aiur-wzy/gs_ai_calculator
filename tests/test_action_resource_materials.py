"""Exact existing source claims, strict new wrappers and non-execution boundaries."""
import copy
import hashlib

import pytest

from genshin_db.runtime_catalog import fingerprint
from genshin_runtime.action_root_semantics import (
    BaseBindingError,
    match_related_root_material,
)
from genshin_runtime.mechanism_semantics import validate_record, verify_mechanism_source
from tests.action_resource_fixtures import (
    action_resource_synthetic_db_only,  # noqa: F401
    load_material,
    material_bytes,
)
from tests.test_mechanism_descriptor_compiler import case

pytestmark = pytest.mark.usefixtures("action_resource_synthetic_db_only")


def load(name):
    return load_material(name)


def records():
    return load("pending-record-bundle.json")["facts"]


@pytest.mark.parametrize("index", range(15))
def test_wrappers_are_valid_but_pending_without_heads(index):
    record = records()[index]
    assert validate_record(record, "fact") == record
    assert record["review"] == {"status": "pending", "reviewer": None, "reviewed_at": None}
    assert load("pending-record-bundle.json")["heads"] == []


@pytest.mark.parametrize("index", range(11))
def test_original_claim_and_review_remain_exact(index):
    old = load("capacity-fact-review__fresh-source-proposals.json")["proposals"][index]["fact"]
    new = records()[index]["payload"]["claim"]
    report = load("capacity-fact-review__independent__source-evidence.json")
    decision = next(x for x in report["decisions"] if x["fact_id"] == old["id"])
    assert fingerprint(new["source_claim"]) == fingerprint(old["claim"]) == decision["claim_sha256"]
    assert new["prior_review"]["original_fact_sha256"] == fingerprint(old)
    assert new["prior_review"]["decision_sha256"] == fingerprint(decision)
    assert new["prior_review"]["report_sha256"] == hashlib.sha256(material_bytes("capacity-fact-review__independent__review.md")).hexdigest()
    assert new["prior_review"]["assumptions"] == old["assumptions"]
    expected = "same_parent_explicit_subskill_operation" if decision["sub_skill"] else "direct_skill_description"
    assert new["evidence_route"] == expected


@pytest.mark.parametrize("value", [True, 1.0, "1", -1, 100, None])
def test_delta_value_is_strict_and_bounded(value):
    record = records()[0]
    c = record["payload"]["claim"]
    c["source_claim"]["amount"] = value
    c["prior_review"]["source_claim_sha256"] = fingerprint(c["source_claim"])
    with pytest.raises(ValueError):
        validate_record(record, "fact")


@pytest.mark.parametrize("field,value", [("scope", "whole_skill"), ("operation_pointer", None),
                                         ("operation_pointer", "/bad~escape"), ("character_id", True)])
def test_delta_cannot_widen_scope_or_coerce_material(field, value):
    record = records()[0]
    c = record["payload"]["claim"]
    c["source_claim"][field] = value
    c["prior_review"]["source_claim_sha256"] = fingerprint(c["source_claim"])
    with pytest.raises(ValueError):
        validate_record(record, "fact")


@pytest.mark.parametrize("kind", ["action_capacity_delta", "related_action_root"])
def test_d20_explicitly_rejects_schema_valid_action_facts(kind):
    descriptor, args = case()
    fact = next(r["payload"] for r in records() if r["payload"]["kind"] == kind)
    args["facts"].append(fact)
    with pytest.raises(ValueError, match="动作来源事实不得由D20"):
        verify_mechanism_source(descriptor, **args)


def weak_case(index=0):
    material = load("action-base-binding__pending-proposals.json")["proposals"][index]["material"]
    record = records()[11 + index]
    record["review"] = {"status": "approved", "reviewer": "synthetic-material-only",
                        "reviewed_at": "2026-10-08T00:00:00+00:00"}
    head = {"record_id": record["id"], "revision": record["revision"],
            "record_fingerprint": fingerprint({"record": record})}
    return material, record, head


@pytest.mark.parametrize("index", range(4))
def test_four_related_materials_do_not_become_default_roots(index):
    material, record, head = weak_case(index)
    before = copy.deepcopy((material, record, head))
    out = match_related_root_material(material, record, head)
    assert out["binding_status"] == "reviewed_material_match"
    assert out["default_mount"] == "unproven" and out["execution_authority"] is False
    assert out["capacity_closure_status"] == out["action_dispatch"] == "pending"
    assert out["requires_fresh_registry_admission"] is True
    assert before == (material, record, head)
    old = load("action-base-binding__pending-proposals.json")["proposals"][index]["record"]
    assert record["id"] != old["id"] and old["review"]["status"] == "pending"


@pytest.mark.parametrize("kind", ["strong_role", "strong_scope", "execution", "raw_parent", "wrong_anchor",
                                 "missing_node", "missing_mount", "malformed_reference", "snapshot"])
def test_weak_relations_reject_authority_and_material_changes(kind):
    material, record, head = weak_case()
    c = record["payload"]["claim"]
    if kind == "strong_role":
        c["resource_role"] = "combat_avatar"
    elif kind == "strong_scope":
        c["scope"] = "base_root_for_dependency_enumeration"
    elif kind == "execution":
        c["limits_of_claim"]["execution_authority"] = True
    elif kind == "raw_parent":
        material["nodes"][0]["raw"]["unreviewed"] = 1
    elif kind == "wrong_anchor":
        c["anchors"][0]["skill"] = c["roles"]["avatar"]
    elif kind == "missing_node":
        material["nodes"].pop()
    elif kind == "missing_mount":
        c["mounts"].pop()
    elif kind == "malformed_reference":
        c["anchors"][0]["talent_reference"]["source_order"] = True
    else:
        material["snapshot"]["snapshot_id"] = "0" * 64
    with pytest.raises(BaseBindingError):
        match_related_root_material(material, record, head)
