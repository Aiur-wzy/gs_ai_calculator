"""D1B: reviewed mechanism knowledge is not current-plan execution authority."""

import copy

import pytest

from genshin_app.equipment.mechanism_receipt import _replay
from genshin_app.equipment.mechanism_source_schema import (
    LEGACY_PROFILE,
    SUPERVISED_PROFILE,
    mechanism_source_contract,
)
from genshin_app.equipment.mechanism_sources import (
    _compile_historical_mechanism_replay,
    compile_mechanism_sources,
    make_replay_context,
)
from genshin_app.equipment.schema import PlanError
from genshin_app.whiteboard import make_handoff
from tests.test_mechanism_cast_graph import identity
from tests.test_mechanism_cast_integration import scene
from tests.test_whiteboard import board_fixture


def compose(plan, template, material, requirements):
    issues = []
    result = compile_mechanism_sources(
        plan,
        template,
        None,
        equipment_requirements=requirements,
        missing=lambda *args: issues.append(args),
        source_provider=lambda _binding: copy.deepcopy(material),
        cast_identity_provider=identity,
    )
    return (*result, issues)


def test_contract_publishes_knowledge_only_authority_and_legacy_boundary():
    contract = mechanism_source_contract()
    assert contract["execution_authority"] == "none"
    assert contract["governance_profile"] == SUPERVISED_PROFILE
    assert contract["execution_support"] == "agent_materialization_required"
    assert contract["candidate_issue_codes"] == ["mechanism_materialization_required"]
    assert contract["confirmable_issue_codes"] == []
    assert contract["legacy_profile"] == {
        "id": LEGACY_PROFILE,
        "status": "historical_receipt_replay_only",
        "execution_authority": "trusted_receipt_replay",
    }


def test_new_whiteboard_handoff_explicitly_uses_supervised_governance():
    plan = make_handoff(board_fixture())
    assert plan["extensions"]["governance_profile"] == SUPERVISED_PROFILE


def test_supervised_profile_returns_candidate_but_never_mutates_execution_model():
    plan, template, material, requirements = scene()
    plan.setdefault("extensions", {})["governance_profile"] = SUPERVISED_PROFILE
    original_model = copy.deepcopy(plan["mechanics_model"])

    model, records, sources, issues = compose(plan, template, material, requirements)

    assert model is None and sources == []
    assert plan["mechanics_model"] == original_model
    assert [item[0] for item in issues] == ["mechanism_materialization_required"]
    row = records[0]
    assert row["status"] == "pending" and row["candidate_status"] == "ready"
    assert row["execution_authority"] == "none"
    assert row["governance_profile"] == SUPERVISED_PROFILE
    assert row["rules"] == [] and row["initial_rules"] == [] and row["parts"] == []
    assert row["preview_rules"]
    assert row["required_confirmation"] is None
    assert row["required_materialization"] == {
        "contract": "mechanism-materialization-candidate-v1",
        "candidate_fingerprint": row["candidate_fingerprint"],
        "source_fingerprint": row["source_fingerprint"],
        "scope": "current_plan_only",
    }


def test_old_acceptance_fingerprint_cannot_unlock_supervised_candidate():
    plan, template, material, requirements = scene()
    plan.setdefault("extensions", {})["governance_profile"] = SUPERVISED_PROFILE
    _, first, _, _ = compose(plan, template, material, requirements)
    plan["mechanism_sources"][0]["accepted_fingerprint"] = first[0]["acceptance_fingerprint"]

    model, second, sources, issues = compose(plan, template, material, requirements)

    assert model is None and sources == []
    assert [item[0] for item in issues] == ["mechanism_materialization_required"]
    assert second[0]["status"] == "pending"
    assert second[0]["accepted_fingerprint"] == second[0]["acceptance_fingerprint"]
    assert second[0]["execution_authority"] == "none"


def test_candidate_fingerprint_binds_current_knowledge_authorization():
    plan, template, material, requirements = scene()
    plan.setdefault("extensions", {})["governance_profile"] = SUPERVISED_PROFILE
    _, first, _, _ = compose(plan, template, material, requirements)

    changed = copy.deepcopy(material)
    changed["authorization_fingerprint"] = "c" * 64
    _, second, _, _ = compose(plan, template, changed, requirements)

    assert first[0]["source_fingerprint"] == second[0]["source_fingerprint"]
    assert first[0]["candidate_fingerprint"] != second[0]["candidate_fingerprint"]
    assert first[0]["required_materialization"] != second[0]["required_materialization"]


def test_missing_profile_is_supervised_instead_of_implicitly_legacy():
    plan, template, material, requirements = scene()

    model, records, sources, issues = compose(plan, template, material, requirements)

    assert model is None and sources == []
    assert [item[0] for item in issues] == ["mechanism_materialization_required"]
    assert records[0]["governance_profile"] == SUPERVISED_PROFILE
    assert records[0]["execution_authority"] == "none"


def test_candidate_cannot_self_grant_legacy_execution_authority():
    plan, template, material, requirements = scene()
    plan.setdefault("extensions", {})["governance_profile"] = LEGACY_PROFILE

    with pytest.raises(PlanError) as error:
        compose(plan, template, material, requirements)

    assert error.value.reason == "legacy_mechanism_governance_forbidden"


def test_trusted_historical_receipt_replay_remains_available_read_only():
    plan, template, material, requirements = scene()
    issues = []

    def compile_historical():
        return _compile_historical_mechanism_replay(
            plan,
            template,
            None,
            equipment_requirements=requirements,
            missing=lambda *args: issues.append(args),
            source_provider=lambda _binding: copy.deepcopy(material),
            cast_identity_provider=identity,
        )

    _, preview, _ = compile_historical()
    plan["mechanism_sources"][0].update(preview[0]["required_confirmation"])
    issues.clear()
    model, records, sources = compile_historical()

    context = make_replay_context(
        plan,
        template,
        records=records,
        action_event_compilations=[],
        attack_metadata_compilations=[],
        parameter_compilations=[],
        equipment_requirements=requirements,
    )

    assert not issues and model is not None and sources
    assert records[0]["status"] == "ready"
    assert records[0]["governance_profile"] == LEGACY_PROFILE
    assert records[0]["execution_authority"] == "legacy_app_compiler"
    assert _replay(records, context) == model
