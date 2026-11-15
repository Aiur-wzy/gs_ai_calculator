from __future__ import annotations

import copy

from test_calculation_overrides_schema import basis, declaration, item, target

from genshin_app.equipment.calculation_overrides import (
    acceptance_fingerprint,
    build_declaration_receipt,
    fingerprint,
    review_declarations,
    stable_target_identity,
)


def _accepted(value, current_basis, target_value):
    value = copy.deepcopy(value)
    row = value["items"][0]
    row["automatic_target_fingerprint"] = fingerprint(target_value)
    row["accepted_fingerprint"] = acceptance_fingerprint(value["basis"], row)
    return value


def test_stable_identity_is_independent_from_array_position_and_object_key_order():
    left = target()
    right = {key: left[key] for key in reversed(tuple(left))}
    assert stable_target_identity(left) == stable_target_identity(right)
    assert "/" not in stable_target_identity(left)


def test_fresh_exact_accepted_declaration_is_ready_for_connected_materializer():
    current_basis = basis()
    target_value = {"id": "hit.ayaka.q.01", "element": "cryo", "t": 4.2}
    value = _accepted(declaration(item()), current_basis, target_value)
    review = review_declarations(
        value,
        current_basis=current_basis,
        current_targets=[{"target": target(), "value": target_value}],
    )
    assert review["status"] == "declaration_ready"
    assert review["execution_connected"] is True
    assert review["review_applies_payload"] is False
    assert review["items"][0]["status"] == "declaration_ready"
    receipt = build_declaration_receipt(review)
    assert receipt["receipt_kind"] == "declaration_review_only"
    assert receipt["execution_connected"] is False
    assert receipt["override_coverage"][0]["override_id"] == "override.ayaka.q.01"


def test_any_basis_or_target_change_makes_acceptance_stale():
    current_basis = basis()
    target_value = {"id": "hit.ayaka.q.01", "t": 4.2}
    value = _accepted(declaration(item()), current_basis, target_value)
    changed_basis = basis(engine_contract_fingerprint="a" * 64)
    review = review_declarations(
        value,
        current_basis=changed_basis,
        current_targets=[{"target": target(), "value": target_value}],
    )
    assert review["items"][0]["status"] == "stale"
    assert "override_baseline_stale" in {issue["code"] for issue in review["items"][0]["issues"]}
    target_review = review_declarations(
        value,
        current_basis=current_basis,
        current_targets=[{"target": target(), "value": {**target_value, "t": 4.3}}],
    )
    assert target_review["items"][0]["status"] == "stale"
    assert "override_target_stale" in {issue["code"] for issue in target_review["items"][0]["issues"]}


def test_state_transition_mode_is_reviewable_with_matching_acceptance():
    current_basis = basis()
    target_value = {"id": "hit.ayaka.q.01"}
    state_target = target(domain="state_transition")
    row = item(
        mode="replace_state_transition",
        target=state_target,
        payload={"transition": {"id": state_target["entity_id"], "value": True}},
    )
    value = _accepted(declaration(row), current_basis, target_value)
    review = review_declarations(
        value,
        current_basis=current_basis,
        current_targets=[{"target": state_target, "value": target_value}],
    )
    assert review["items"][0]["execution_support"] == "implemented"
    assert review["items"][0]["status"] == "declaration_ready"
    assert review["items"][0]["issues"] == []
