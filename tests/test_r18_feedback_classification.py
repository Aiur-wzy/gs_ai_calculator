import pytest

from genshin_app.domain_errors import classify_domain_failure, tool_failure_guidance


@pytest.mark.parametrize("reason,operation", [
    ("invalid_query_request", "gs_query_appendix"),
    ("invalid_query_request", "gs_query_runtime"),
    ("invalid_observation_request", "gs_query_calculation"),
    ("invalid_energy_trial_request", "cli.equipment.assist"),
    ("damage_report_requires_causal_execution", "gs_calculate"),
])
def test_known_contract_error_does_not_become_missing_knowledge_or_global_engine_gap(reason, operation):
    value = classify_domain_failure(reason, operation=operation)
    assert value["category"] == "request_error"
    assert "classification_confidence" not in value


def test_version_specific_migration_recovery_is_kept_without_retrying_old_calculation():
    from genshin_app.equipment.damage_report_window_receipt import (
        require_window_plan_support,
    )
    from genshin_app.equipment.schema import PlanError

    with pytest.raises(PlanError) as caught:
        require_window_plan_support({"plan_version": 2, "rotation_model": {"damage_report": {"end": 12}}})
    error = caught.value
    feedback = tool_failure_guidance(error.reason, operation="gs_calculate", details=error.details)
    assert feedback["recovery"]["action"] == "enable_mechanics_and_review_bindings"
    assert feedback["recovery"]["retry_same_request"] is False
    assert feedback["path"] == "/rotation_model/damage_report"
