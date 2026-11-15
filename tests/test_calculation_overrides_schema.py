from __future__ import annotations

import copy

import pytest

from genshin_app.equipment.calculation_overrides import (
    OverrideDeclarationError,
    capabilities,
    fingerprint,
    validate_declarations,
)
from genshin_app.equipment.schema import PlanError, new_plan, validate_plan


def target(**changes):
    value = {
        "domain": "manual_hit",
        "entity_id": "hit.ayaka.q.01",
        "scope_id": "template.hits",
        "topology_node_id": "topology.ayaka.q.01",
        "execution_binding_id": "binding.ayaka.q.01",
        "action_id": "action.ayaka.q",
        "component_id": "component.cut",
        "occurrence_id": "occurrence.01",
        "target_id": "enemy.0",
    }
    value.update(changes)
    return value


def basis(**changes):
    value = {
        "plan_input_fingerprint": "1" * 64,
        "automatic_baseline_fingerprint": "2" * 64,
        "database_snapshot_fingerprint": "3" * 64,
        "engine_contract_fingerprint": "4" * 64,
        "topology_fingerprint": "5" * 64,
        "execution_bindings_fingerprint": "6" * 64,
    }
    value.update(changes)
    return value


def item(**changes):
    value = {
        "override_id": "override.ayaka.q.01",
        "mode": "replace_event",
        "target": target(),
        "coverage_key": "coverage.ayaka.q.01",
        "supersedes": ["automatic:ayaka.q.01"],
        "reason_code": "engine_disagreement",
        "reason": "自动派生遗漏了当前段的独立附着身份。",
        "source_refs": [{
            "source_id": "source.research.01",
            "kind": "web",
            "locator": "https://example.invalid/evidence",
            "summary": "逐段机制证据摘要",
            "fingerprint": None,
        }],
        "impact_domains": ["damage", "elemental_application", "reaction"],
        "payload": {"event": {"id": "hit.ayaka.q.01", "element": "cryo"}},
        "automatic_target_fingerprint": fingerprint({"old": True}),
        "accepted_fingerprint": None,
        "applies_to_version": "7.0.0",
        "unknowns": [],
        "invalidation_conditions": ["数据库快照、引擎合同或目标事件变化"],
    }
    value.update(changes)
    return value


def declaration(*items, **basis_changes):
    return {
        "contract": "calculation-event-overrides-v1",
        "basis": basis(**basis_changes),
        "items": list(items),
    }


def native_plan(overrides):
    plan = new_plan()
    plan.update(plan_version=3, rotation_model=None, mechanics_model=None)
    plan["calculation_event_overrides"] = overrides
    return plan


def test_closed_declaration_and_connected_execution_capability_are_published():
    value = declaration(item())
    assert validate_declarations(value, plan_version=3) == {
        "valid": True,
        "contract": "calculation-event-overrides-v1",
        "item_count": 1,
    }
    assert validate_plan(native_plan(value))["valid"] is True
    caps = capabilities()
    assert caps["execution_connected"] is True
    assert caps["review_applies_payload"] is False
    assert caps["modes"]["replace_event"] == "implemented"
    assert caps["execution_targets"]["add_event"]["domains"] == [
        "mechanics_hit_event"
    ]
    assert "rotation_action" not in caps["execution_targets"]["replace_event"][
        "domains"
    ]
    assert caps["modes"]["replace_state_transition"] == "implemented"
    assert caps["modes"]["replace_computed_value"] == "implemented"
    assert caps["modes"]["provisional_entity"] == "not_implemented"


@pytest.mark.parametrize("forbidden", ["result", "summary", "total_damage", "dps", "trace", "receipt"])
def test_payload_cannot_write_final_or_derived_outputs(forbidden):
    value = declaration(item(payload={forbidden: 1}))
    with pytest.raises(OverrideDeclarationError) as exc:
        validate_declarations(value, plan_version=3)
    assert exc.value.reason == "override_final_result_forbidden"
    with pytest.raises(PlanError):
        validate_plan(native_plan(value))


def test_contract_is_closed_and_requires_native_v3():
    value = declaration(item())
    value["items"][0]["target"]["pointer"] = "/hits/0"
    with pytest.raises(OverrideDeclarationError) as exc:
        validate_declarations(value, plan_version=3)
    assert exc.value.reason == "override_shape_invalid"
    clean = declaration(item())
    with pytest.raises(OverrideDeclarationError) as exc:
        validate_declarations(clean, plan_version=2)
    assert exc.value.reason == "override_plan_version_invalid"


def test_mode_specific_supersedes_and_provisional_rules_are_fail_closed():
    with pytest.raises(OverrideDeclarationError) as exc:
        validate_declarations(declaration(item(mode="add_event")), plan_version=3)
    assert exc.value.reason == "override_supersedes_invalid"
    provisional = item(
        mode="provisional_entity",
        target=target(
            domain="public_entity",
            topology_node_id=None,
            execution_binding_id=None,
        ),
        reason_code="provisional_public_entity",
        payload={
            "contract": "provisional-entity-registry-reference-v1",
            "entity_type": "character",
            "provisional_id": "hit.ayaka.q.01",
            "authorization_kind": "public_data_patch",
            "payload_fingerprint": "7" * 64,
            "lookup_receipt_id": "lookup.temporary.01",
            "attestation_id": None,
            "approval_id": "approval.temporary.01",
        },
    )
    assert validate_declarations(declaration(provisional), plan_version=3)["valid"]
    wrong = copy.deepcopy(provisional)
    wrong["reason_code"] = "user_scenario"
    with pytest.raises(OverrideDeclarationError) as exc:
        validate_declarations(declaration(wrong), plan_version=3)
    assert exc.value.reason == "override_provisional_invalid"


def test_duplicate_ids_coverage_sources_impacts_and_supersedes_are_rejected():
    duplicate = item(
        supersedes=["automatic:one", "automatic:one"],
        impact_domains=["damage", "damage"],
    )
    with pytest.raises(OverrideDeclarationError):
        validate_declarations(declaration(duplicate), plan_version=3)
    a = item()
    b = item(override_id="override.other")
    with pytest.raises(OverrideDeclarationError) as exc:
        validate_declarations(declaration(a, b), plan_version=3)
    assert exc.value.reason == "override_duplicate"


@pytest.mark.parametrize(
    ("mode", "domain"),
    [
        ("replace_event", "parameter_input"),
        ("replace_input", "manual_hit"),
        ("replace_state_transition", "manual_hit"),
        ("replace_computed_value", "manual_hit"),
    ],
)
def test_mode_cannot_claim_an_unrelated_target_domain(mode, domain):
    row = item(mode=mode, target=target(domain=domain))
    with pytest.raises(OverrideDeclarationError) as exc:
        validate_declarations(declaration(row), plan_version=3)
    assert exc.value.reason == "override_target_invalid"


def test_provisional_entity_must_remain_plan_local_and_non_database_authoritative():
    row = item(
        mode="provisional_entity",
        target=target(domain="public_entity", topology_node_id=None, execution_binding_id=None),
        reason_code="provisional_public_entity",
        payload={
            "contract": "provisional-entity-registry-reference-v1",
            "entity_type": "weapon",
            "provisional_id": "hit.ayaka.q.01",
            "authorization_kind": "public_data_patch",
            "payload_fingerprint": "7" * 64,
            "lookup_receipt_id": "lookup.temporary.01",
            "attestation_id": None,
            "approval_id": "approval.temporary.01",
            "extra_embedded_value": {"base_atk": 500},
        },
    )
    with pytest.raises(OverrideDeclarationError) as exc:
        validate_declarations(declaration(row), plan_version=3)
    assert exc.value.reason == "override_provisional_invalid"


def test_missing_and_correction_provisional_authorities_cannot_be_mixed():
    base = item(
        mode="provisional_entity",
        target=target(domain="public_entity", topology_node_id=None,
                      execution_binding_id=None),
        reason_code="provisional_public_entity",
        payload={
            "contract": "provisional-entity-registry-reference-v1",
            "entity_type": "weapon",
            "provisional_id": "hit.ayaka.q.01",
            "authorization_kind": "database_missing",
            "payload_fingerprint": "7" * 64,
            "lookup_receipt_id": "lookup.temporary.01",
            "attestation_id": "attestation.temporary.01",
            "approval_id": None,
        },
    )
    assert validate_declarations(declaration(base), plan_version=3)["valid"]
    mixed = copy.deepcopy(base)
    mixed["payload"]["approval_id"] = "approval.must.not.exist"
    with pytest.raises(OverrideDeclarationError) as exc:
        validate_declarations(declaration(mixed), plan_version=3)
    assert exc.value.reason == "override_provisional_invalid"
