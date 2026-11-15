from genshin_app.domain_errors import (
    BUSINESS_ERROR_CATEGORIES,
    RELEASE_ERROR_CATEGORY,
    classify_domain_failure,
    normalize_issue,
)


def test_business_categories_are_closed_and_release_is_separate():
    assert BUSINESS_ERROR_CATEGORIES == {
        "request_error",
        "core_data_missing",
        "knowledge_missing",
        "engine_capability_missing",
    }
    assert RELEASE_ERROR_CATEGORY not in BUSINESS_ERROR_CATEGORIES


def test_operation_context_disambiguates_not_found():
    assert classify_domain_failure(
        "not_found", operation="gs_query_appendix"
    )["category"] == "knowledge_missing"
    assert classify_domain_failure(
        "weapon_not_found", operation="gs_search_catalog"
    )["category"] == "core_data_missing"


def test_engine_and_release_failures_have_distinct_scopes():
    engine = classify_domain_failure("unsupported_target_domain")
    assert engine == {
        "category": "engine_capability_missing",
        "blocking_scope": "engine_capability",
        "retryable": False,
    }
    release = classify_domain_failure("release_pair_mismatch")
    assert release == {
        "category": "release_identity_failure",
        "blocking_scope": "release_identity",
        "retryable": False,
    }
    assert classify_domain_failure(
        "database_release_identity_unmanifested"
    )["category"] == "release_identity_failure"
    assert classify_domain_failure(
        "database_live_receipt_manifest_mismatch"
    )["category"] == "release_identity_failure"


def test_nested_issue_category_wins_and_normalization_preserves_detail():
    issue = {
        "code": "agent_stage_incomplete",
        "message": "资料待补",
        "details": [{"category": "knowledge_missing", "path": "appendix.skill"}],
    }
    normalized = normalize_issue(issue, operation="gs_transition_phase")
    assert normalized["details"] == issue["details"]
    assert normalized["category"] == "knowledge_missing"
    assert normalized["blocking_scope"] == "agent_materialization"
    assert normalized["retryable"] is True


def test_classification_uses_codes_not_free_form_diagnostic_words():
    assert classify_domain_failure("unsupported_target_domain", details={"message": "schema error"})["category"] == "engine_capability_missing"
    assert classify_domain_failure("agent_tool_input", operation="gs_query_appendix")["category"] == "request_error"


def test_request_error_is_safe_default_without_hiding_reason():
    classified = classify_domain_failure("custom_validation_failure")
    assert classified["category"] == "request_error"
    assert classified["blocking_scope"] == "request"


def test_request_schema_marker_wins_over_broad_engine_unsupported_marker():
    classified = classify_domain_failure("unsupported_schema")
    assert classified == {
        "category": "request_error",
        "blocking_scope": "request",
        "retryable": False,
    }
