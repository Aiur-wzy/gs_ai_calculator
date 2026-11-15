from __future__ import annotations

import copy

import pytest
from test_calculation_overrides_schema import item, target

from genshin_app.equipment.calculation_overrides import (
    OverrideDeclarationError,
    apply_ownership_transaction,
    fingerprint,
    ownership_registry,
    stable_target_identity,
)


def automatic_claim():
    return {
        "owner_id": "automatic:ayaka.q.01",
        "owner_kind": "automatic",
        "coverage_key": "coverage.ayaka.q.01",
        "target_identity": stable_target_identity(target()),
        "source_fingerprint": fingerprint({"automatic": True}),
    }


def accepted_item(**changes):
    value = item(accepted_fingerprint="f" * 64)
    value.update(changes)
    return value


def test_exact_supersedes_releases_old_owner_and_installs_one_override_owner():
    out = apply_ownership_transaction([automatic_claim()], [accepted_item()])
    assert out["status"] == "committed"
    assert out["released"][0]["owner_id"] == "automatic:ayaka.q.01"
    assert out["claims"] == out["installed"]
    assert out["claims"][0]["owner_id"] == "override:override.ayaka.q.01"
    assert ownership_registry(out["claims"])["coverage.ayaka.q.01"]["owner_kind"] == "override"


def test_transaction_failure_does_not_mutate_callers_or_partially_install():
    claims = [automatic_claim()]
    before_claims = copy.deepcopy(claims)
    first = accepted_item()
    second = accepted_item(
        override_id="override.second",
        coverage_key="coverage.second",
        supersedes=["automatic:missing"],
        target={**target(), "entity_id": "hit.second"},
    )
    items = [first, second]
    before_items = copy.deepcopy(items)
    with pytest.raises(OverrideDeclarationError) as exc:
        apply_ownership_transaction(claims, items)
    assert exc.value.reason == "override_coverage_hole"
    assert claims == before_claims
    assert items == before_items


def test_add_requires_empty_range_and_replace_requires_exact_current_owner():
    add = accepted_item(
        override_id="override.new",
        mode="add_event",
        coverage_key="coverage.new",
        supersedes=[],
        target={**target(), "entity_id": "hit.new"},
    )
    result = apply_ownership_transaction([automatic_claim()], [add])
    assert {claim["coverage_key"] for claim in result["claims"]} == {
        "coverage.ayaka.q.01",
        "coverage.new",
    }
    wrong = accepted_item(supersedes=["automatic:other"])
    with pytest.raises(OverrideDeclarationError) as exc:
        apply_ownership_transaction([automatic_claim()], [wrong])
    assert exc.value.reason == "override_supersedes_mismatch"


def test_computed_value_mode_atomically_replaces_exact_owner():
    computed = accepted_item(mode="replace_computed_value")
    result = apply_ownership_transaction([automatic_claim()], [computed])
    assert result["released"][0]["owner_id"] == "automatic:ayaka.q.01"
    assert result["installed"][0]["owner_id"] == "override:override.ayaka.q.01"
