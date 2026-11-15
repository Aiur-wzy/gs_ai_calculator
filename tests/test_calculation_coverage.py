from __future__ import annotations

import hashlib
import json

from genshin_app.agent_chat.calculation_coverage import (
    PROVISIONAL_RECEIPT_CONTRACT,
    qualify_calculation_coverage,
)
from genshin_app.agent_chat.calculation_qualification import qualify_calculation
from genshin_app.equipment import run_plan_calc
from genshin_app.equipment.calculation_overrides.ownership_receipt import (
    FIELD as OWNERSHIP_PROOF_FIELD,
)
from genshin_app.equipment.calculation_overrides.ownership_receipt import (
    build_ownership_result_proof,
)
from genshin_app.equipment.calculation_overrides.receipt import build_execution_receipt
from genshin_app.equipment.provisional_entities import (
    PROVISIONAL_RESULT_FIDELITY,
    build_usage_receipt,
    resolve_entity_ref,
)
from genshin_app.task_capability import (
    build_trusted_issue_resolution,
    classify_task_issues,
)
from tests.test_external_damage import confirm, external_plan
from tests.test_provisional_entities import (
    DB_FP,
    approved,
    attested,
    conflict_entity,
    lookup_ledger,
    provisional_ref,
    used_plan,
)


def _sha(value):
    return hashlib.sha256(json.dumps(
        value, ensure_ascii=False, sort_keys=True, allow_nan=False,
        separators=(",", ":"),
    ).encode()).hexdigest()


def _calculated(plan=None):
    return {
        "plan": plan or {},
        "report": {"missing_inputs": []},
        "result_receipt": {},
        "result": {
            'summary': {'total_damage': 0, 'dps': 0},
            "rotation_check": {
                "status": "unproven",
                "action_check": {"status": "passed"},
                "energy_check": {"status": "unproven", "opening_status": "passed",
                                 "proven_cycle": None},
                "damage_interpretation": {"kind": "planned_damage",
                                          "complete_first_cycle": True},
            },
            "mechanics_status": "unproven",
            "mechanics": {"complete_first_cycle": True, "execution_status": "unproven"},
            "action_materialization_receipt": {
                "status": "unknown", "calculation_class": "diagnostic",
            },
        },
    }


def _attach_provisional_fidelity(value):
    for key in ("result", "report", "result_receipt"):
        value[key]["result_fidelity"] = PROVISIONAL_RESULT_FIDELITY


def _attach_ownership_proof(value):
    if (
        isinstance(value.get("result"), dict)
        and OWNERSHIP_PROOF_FIELD in value["result"]
        and isinstance(value.get("result_receipt"), dict)
        and OWNERSHIP_PROOF_FIELD in value["result_receipt"]
    ):
        return
    report = value["report"]
    report.setdefault("plan", value.get("plan", {}))
    report.setdefault("input_fingerprint", _sha(value.get("plan", {})))
    claims = []
    override = value["result"].get("calculation_override_receipt")
    if isinstance(override, dict) and override.get("status") == "applied":
        for row in override.get("coverage", []):
            claims.append({
                "owner_id": "override:" + row["override_id"],
                "owner_kind": "override",
                "coverage_key": row["coverage_key"],
                "target_identity": "test-target:" + row["override_id"],
                "source_fingerprint": _sha(row),
            })
        claims.sort(key=lambda row: row["coverage_key"])
        override["ownership_ledger_fingerprint"] = _sha(claims)
        override["receipt_fingerprint"] = _sha({
            key: item for key, item in override.items()
            if key != "receipt_fingerprint"
        })
        value["result_receipt"]["calculation_override_receipt"] = override
    report["calculation_ownership_audit"] = {
        "contract": "calculation-coverage-ownership-audit-v1",
        "status": "verified",
        "claims": claims,
        "ownership_ledger_fingerprint": _sha(claims),
    }
    proof = build_ownership_result_proof(report)
    report[OWNERSHIP_PROOF_FIELD] = proof
    value["result"][OWNERSHIP_PROOF_FIELD] = proof
    value["result_receipt"][OWNERSHIP_PROOF_FIELD] = proof


def _qualified(value, **kwargs):
    _attach_ownership_proof(value)
    topology = {"status": "numeric_ready", "counts": {"active_nodes": 1},
                "active_node_kinds": ["action_anchor"],
                "active_node_kind_counts": {"action_anchor": 1}}
    return qualify_calculation(
        value, topology_receipt=topology,
        topology_binding_receipt={"status": "complete"},
        energy_intent="multi_round_legal",
        **kwargs,
    )


def _coverage(value, **kwargs):
    _attach_ownership_proof(value)
    topology = {"status": "numeric_ready", "counts": {"active_nodes": 1},
                "active_node_kinds": ["action_anchor"],
                "active_node_kind_counts": {"action_anchor": 1}}
    return qualify_calculation_coverage(
        value, topology_receipt=topology,
        topology_binding_receipt={"status": "complete"},
        energy_intent="multi_round_legal",
        **kwargs,
    )


def _override_declaration():
    return {"items": [{"override_id": "override.one",
                       "coverage_key": "hit:h1", "mode": "replace_event"}]}


def test_acceptance_derives_scoped_receipt_without_claiming_unknown_dimensions():
    result = _qualified(_calculated())
    assert result["status"] == "accepted"
    coverage = result["calculation_coverage"]
    assert coverage["status"] == "partial" and coverage["complete"] is False
    assert coverage["scope"] == (
        "current_declared_effective_graph_generic_verification_dimensions"
    )
    assert coverage["automatic_verified"] == 5
    assert coverage["unresolved"] == 2
    assert coverage["blocking_issues"] == []
    receipt = coverage["automatic_coverage_receipt"]
    assert receipt["scope"] == coverage["scope"]
    assert receipt["source_fingerprints"]


def _failed_optional_tail():
    calculated = _calculated()
    result = calculated['result']
    result['mechanics_status'] = 'failed'
    result['mechanics']['execution_status'] = 'failed'
    result['rotation_check'].update(status='failed', action_check={'status': 'failed'},
        energy_check={'status': 'failed', 'opening_status': 'passed'})
    result['damage_report_window'] = {'contract': 'damage-report-window-v1', 'status': 'complete', 'end': 1,
        'start': 0, 'execution_period': 2, 'boundary': 'inclusive_time',
        'required_validation': {'status': 'complete'}, 'optional_audit': {'status': 'failed'}}
    result['mechanics_issues'] = [{'code': 'optional_cycle_failed', 'severity': 'error', 'cycle': 1}]
    return calculated


def test_finite_scope_qualification_does_not_require_optional_tail_success():
    calculated = _failed_optional_tail()
    qualified = _qualified(calculated)
    assert qualified['status'] == 'accepted', qualified['issues']


def test_optional_tail_cannot_excuse_failed_required_opening():
    calculated = _failed_optional_tail()
    calculated['result']['rotation_check']['energy_check']['opening_status'] = 'failed'
    qualified = _qualified(calculated)
    assert qualified['status'] == 'rejected'
    assert 'calculation_incomplete' in {row['code'] for row in qualified['issues']}


def test_explicit_automatic_receipt_counts_only_verified_unique_coverage():
    value = _calculated()
    value["result"].update(
        mechanics_status="passed",
        mechanics={"complete_first_cycle": True, "execution_status": "passed"},
        action_materialization_receipt={"status": "complete", "calculation_class": "normal"},
    )
    first = _coverage(value)
    value["result"]["automatic_coverage_receipt"] = first["automatic_coverage_receipt"]
    coverage = _coverage(value)
    assert coverage["status"] == "complete"
    assert coverage["automatic_verified"] == 7
    assert coverage["unresolved"] == 0


def test_unknown_or_high_impact_automatic_coverage_fails_closed():
    value = _calculated()
    value["result"]["rotation_check"]["action_check"]["status"] = "failed"
    result = _qualified(value)
    assert result["status"] == "rejected"
    assert "automatic_coverage_high_impact_unresolved" in {
        row["code"] for row in result["issues"]
    }

    value = _calculated()
    value["result"]["automatic_coverage_receipt"] = {
        "contract": "future-coverage-v2",
    }
    assert _qualified(value)["status"] == "rejected"


def test_explicit_automatic_receipt_is_bound_to_exact_generic_evidence():
    value = _calculated()
    receipt = _coverage(value)["automatic_coverage_receipt"]
    value["result"]["automatic_coverage_receipt"] = receipt
    value["result"]["rotation_check"]["energy_check"]["opening_status"] = "failed"
    result = _coverage(value)
    assert result["status"] == "unresolved"
    assert "automatic_coverage_receipt_stale" in {
        row["code"] for row in result["blocking_issues"]
    }


def test_declared_override_without_effective_receipt_is_rejected():
    value = _calculated({"calculation_event_overrides": _override_declaration()})
    result = _qualified(value)
    assert result["status"] == "rejected"
    assert result["calculation_coverage"]["unresolved"] >= 1
    assert "calculation_override_unresolved" in {row["code"] for row in result["issues"]}


def test_fresh_applied_override_receipt_counts_agent_coverage():
    declaration = {"items": [{
        "override_id": "override.one", "coverage_key": "hit:h1",
        "mode": "replace_input",
    }]}
    value = _calculated({"calculation_event_overrides": declaration})
    basis = {"automatic_baseline_fingerprint": "a" * 64}
    review = {"review_fingerprint": "b" * 64, "items": [{
        "override_id": "override.one", "coverage_key": "hit:h1",
        "mode": "replace_input", "status": "declaration_ready",
    }]}
    ownership = {"ownership_ledger_fingerprint": "c" * 64,
                 "released": [{"owner_id": "automatic:hit.h1"}],
                 "installed": [{"owner_id": "override:override.one"}]}
    receipt = build_execution_receipt(
        basis, review, status="applied", ownership=ownership,
        applied=[{"override_id": "override.one", "coverage_key": "hit:h1",
                  "mode": "replace_input", "impact_domains": []}],
        effective_execution_fingerprint="d" * 64,
    )
    value["report"].update(
        calculation_override_basis=basis,
        effective_execution_fingerprint="d" * 64,
    )
    value["result"]["calculation_override_receipt"] = receipt
    result = _qualified(value)
    assert result["status"] == "accepted"
    assert result["calculation_coverage"]["agent_overridden"] == 1


def test_fresh_task_capability_resolution_survives_final_coverage_gate():
    declaration = {"items": [{
        "override_id": "override.one", "coverage_key": "hit:h1",
        "mode": "replace_input",
    }]}
    value = _calculated({"calculation_event_overrides": declaration})
    basis = {"automatic_baseline_fingerprint": "a" * 64}
    review = {"review_fingerprint": "b" * 64, "items": [{
        "override_id": "override.one", "coverage_key": "hit:h1",
        "mode": "replace_input", "status": "declaration_ready",
    }]}
    ownership = {"ownership_ledger_fingerprint": "c" * 64,
                 "released": [{"owner_id": "automatic:hit.h1"}],
                 "installed": [{"owner_id": "override:override.one"}]}
    override_receipt = build_execution_receipt(
        basis, review, status="applied", ownership=ownership,
        applied=[{"override_id": "override.one", "coverage_key": "hit:h1",
                  "mode": "replace_input", "impact_domains": []}],
        effective_execution_fingerprint="d" * 64,
    )
    value["report"].update(
        calculation_override_basis=basis,
        effective_execution_fingerprint="d" * 64,
        plan={}, mechanics_model=None, mechanics_matches=None,
    )
    value["result"]["calculation_override_receipt"] = override_receipt
    _attach_ownership_proof(value)
    issue = {
        "code": "missing_input", "path": "hits.h1.multiplier",
        "message": "缺倍率", "consumer": "hit:h1",
        "required": True, "consumed": True,
    }
    graph_fingerprint = _sha({
        "template": None, "rotation_model": None,
        "mechanics_model": None, "mechanics_matches": None,
    })
    trusted = build_trusted_issue_resolution(
        issue, authority="verified_override_receipt",
        resolved_by="override:override.one",
        proof_fingerprint=_sha({
            "authority": "verified_override_receipt",
            "coverage_key": "hit:h1",
            "source_proof_fingerprint": override_receipt["receipt_fingerprint"],
            "effective_graph_fingerprint": graph_fingerprint,
        }),
    )
    capability = classify_task_issues([issue], trusted_resolutions=[trusted])
    capability["derivation"]["effective_graph_fingerprint"] = graph_fingerprint
    value["report"].update(missing_inputs=[issue], task_capability=capability)

    coverage = _coverage(value)
    assert coverage["blocking_issues"] == []
    assert coverage["agent_overridden"] == 1

    value["report"]["task_capability"]["resolved"][0]["resolution"][
        "proof_fingerprint"] = "0" * 64
    invalid = _coverage(value)
    assert "task_capability_receipt_invalid" in {
        row["code"] for row in invalid["blocking_issues"]
    }


def test_provisional_entities_cannot_self_authorize_with_plan_and_self_hash():
    entity = {"entity_type": "weapon", "provisional_id": "weapon.local",
              "payload_fingerprint": "a" * 64,
              "approval": {"approval_id": "approval.one", "status": "user_confirmed",
                           "issued_by": "user_decision_ledger"}}
    value = _calculated({
        "provisional_entities": [entity],
        "extensions": {"selected_entity": {
            "kind": "provisional", "entity_type": "weapon",
            "provisional_id": "weapon.local",
        }},
    })
    missing = _qualified(value)
    assert missing["status"] == "rejected"
    assert {row["code"] for row in missing["issues"]} >= {
        "provisional_entity_usage_receipt_untrusted",
    }

    unsigned = {
        "contract": PROVISIONAL_RECEIPT_CONTRACT,
        "status": "verified",
        "entities": [{"entity_type": "weapon", "provisional_id": "weapon.local",
                      "payload_fingerprint": "a" * 64, "approval_id": "approval.one",
                      "status": "user_confirmed"}],
    }
    value["result"]["provisional_entity_usage_receipt"] = {
        **unsigned, "receipt_fingerprint": _sha(unsigned),
    }
    result = _qualified(value)
    assert result["status"] == "rejected"
    assert result["calculation_coverage"]["user_confirmed_provisional"] == 0
    assert {row["code"] for row in result["issues"]} >= {
        "provisional_entity_usage_receipt_untrusted",
    }


def test_unregistered_provisional_reference_still_cannot_bypass_live_ledger_gate():
    value = _calculated({
        "extensions": {"selected_entity": {
            "contract": "provisional-entity-registry-reference-v1",
            "entity_type": "weapon",
            "provisional_id": "weapon.missing",
        }},
    })
    result = _qualified(value)
    assert result["status"] == "rejected"
    assert {row["code"] for row in result["issues"]} >= {
        "provisional_entity_usage_receipt_untrusted",
    }


def test_unused_provisional_registry_does_not_require_usage_receipt():
    entity = {"entity_type": "weapon", "provisional_id": "weapon.local",
              "payload_fingerprint": "a" * 64, "approval": None}
    result = _qualified(_calculated({"provisional_entities": [entity]}))
    assert result["status"] == "accepted"


def test_whiteboard_handoff_provisional_ref_is_provenance_not_formal_use():
    entity = {"entity_type": "weapon", "provisional_id": "weapon.local",
              "payload_fingerprint": "a" * 64, "approval": None}
    plan = {
        "provisional_entities": [entity],
        "extensions": {
            "whiteboard_handoff": {
                "retained_equipment_ref": provisional_ref(),
            },
        },
    }

    result = _qualified(_calculated(plan))

    assert result["status"] == "accepted"
    assert result["calculation_coverage"]["provisional_entity_total"] == 0
    assert result["calculation_coverage"]["user_confirmed_provisional"] == 0


def _missing_usage():
    row, attestation = attested(used=True)
    plan = used_plan(row)
    resolved = resolve_entity_ref(
        plan, provisional_ref(), consumer="agent_materialization",
        database_snapshot_fingerprint=DB_FP,
        candidate_revision=4, stage_revision=2, approval_ledger={},
        database_lookup_ledger=lookup_ledger(row),
        agent_attestation_ledger={attestation["attestation_id"]: attestation},
        supported_consumers=frozenset({"agent_materialization"}),
    )
    return plan, build_usage_receipt(
        plan, [resolved], candidate_revision=4, stage_revision=2,
        database_snapshot_fingerprint=DB_FP,
    )


def test_trusted_database_missing_usage_is_formally_eligible_without_user_approval():
    plan, receipt = _missing_usage()
    value = _calculated(plan)
    value["result"]["provisional_entity_usage_receipt"] = receipt
    _attach_provisional_fidelity(value)
    result = _qualified(
        value,
        trusted_provisional_usage_receipt=receipt,
        candidate_revision=4,
        stage_revision=2,
        database_snapshot_fingerprint=DB_FP,
    )
    assert result["status"] == "accepted"
    coverage = result["calculation_coverage"]
    assert coverage["provisional_entity_total"] == 1
    assert coverage["agent_reported_missing_entity"] == 1
    assert coverage["user_confirmed_public_data_patch"] == 0
    assert coverage["user_confirmed_provisional"] == 0
    assert coverage["provisional_entity_usage_receipt"] == receipt


def test_trusted_public_data_patch_usage_counts_exact_user_approval():
    row, approval = approved(conflict_entity(), used=True)
    plan = used_plan(row)
    resolved = resolve_entity_ref(
        plan, provisional_ref(), consumer="agent_materialization",
        database_snapshot_fingerprint=DB_FP,
        candidate_revision=4, stage_revision=2,
        approval_ledger={approval["approval_id"]: approval},
        database_lookup_ledger=lookup_ledger(row),
        supported_consumers=frozenset({"agent_materialization"}),
    )
    receipt = build_usage_receipt(
        plan, [resolved], candidate_revision=4, stage_revision=2,
        database_snapshot_fingerprint=DB_FP,
    )
    value = _calculated(plan)
    value["result"]["provisional_entity_usage_receipt"] = receipt
    _attach_provisional_fidelity(value)
    coverage = _coverage(
        value,
        trusted_provisional_usage_receipt=receipt,
        candidate_revision=4,
        stage_revision=2,
        database_snapshot_fingerprint=DB_FP,
    )
    assert coverage["blocking_issues"] == []
    assert coverage["user_confirmed_public_data_patch"] == 1
    assert coverage["user_confirmed_provisional"] == 1
    assert coverage["agent_reported_missing_entity"] == 0


def test_result_receipt_cannot_replace_out_of_band_app_authority_or_stale_context():
    plan, receipt = _missing_usage()
    value = _calculated(plan)
    value["result"]["provisional_entity_usage_receipt"] = receipt
    _attach_provisional_fidelity(value)
    assert _qualified(value)["status"] == "rejected"
    stale = _qualified(
        value,
        trusted_provisional_usage_receipt=receipt,
        candidate_revision=5,
        stage_revision=2,
        database_snapshot_fingerprint=DB_FP,
    )
    assert stale["status"] == "rejected"
    assert {row["code"] for row in stale["issues"]} >= {
        "provisional_entity_usage_receipt_stale",
    }


def test_provisional_usage_requires_consistent_approximate_result_fidelity():
    plan, receipt = _missing_usage()
    value = _calculated(plan)
    value["result"]["provisional_entity_usage_receipt"] = receipt
    _attach_provisional_fidelity(value)
    value["report"]["result_fidelity"] = "formal_without_provisional_entities"

    coverage = _coverage(
        value,
        trusted_provisional_usage_receipt=receipt,
        candidate_revision=4,
        stage_revision=2,
        database_snapshot_fingerprint=DB_FP,
    )

    assert {row["code"] for row in coverage["blocking_issues"]} >= {
        "calculation_result_fidelity_invalid",
    }


def test_external_fixed_events_need_their_existing_result_receipt(tmp_path):
    db = tmp_path / "absent.db"
    value = run_plan_calc(confirm(external_plan(), db), db)
    coverage = _coverage(value)
    assert coverage["external_fixed_value"] == 1
    assert coverage["agent_overridden"] == 0
    assert coverage["automatic_verified"] >= 1


def test_unreceipted_external_value_is_not_counted_as_covered():
    value = _calculated()
    value["result"]["external_damage"] = {"items": [{"id": "external.one"}]}
    coverage = _coverage(value)
    assert coverage["external_fixed_value"] == 0
    assert {row["code"] for row in coverage["blocking_issues"]} == {
        "external_fixed_value_receipt_invalid",
    }
