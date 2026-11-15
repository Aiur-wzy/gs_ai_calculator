"""Provider diagnostics must remain useful without retaining raw service text."""
from __future__ import annotations

import json

import pytest

from genshin_app.agent_chat.provider_errors import describe_provider_error


@pytest.mark.parametrize("variant", ["httpConnectionFailed", "responseStreamConnectionFailed",
                                    "responseStreamDisconnected", "responseTooManyFailedAttempts"])
def test_structured_stream_and_http_failures_have_status_and_chinese_explanation(variant):
    result = describe_provider_error({"codexErrorInfo": {variant: {"httpStatusCode": 403}}}, will_retry=False)
    assert result["error_type"] == variant and result["http_status"] == 403
    assert "HTTP 403" in result["message"] and "访问权限" in result["hint"]
    assert result["cli_will_retry"] is False


@pytest.mark.parametrize("variant", ["contextWindowExceeded", "sessionBudgetExceeded", "usageLimitExceeded",
                                    "serverOverloaded", "cyberPolicy", "internalServerError", "unauthorized",
                                    "badRequest", "threadRollbackFailed", "sandboxError", "other"])
def test_known_simple_discriminants_are_retained_without_service_text(variant):
    result = describe_provider_error({"codexErrorInfo": variant, "message": "SECRET_TOKEN and private request",
                                      "additionalDetails": "C:/private/config"})
    assert result["error_type"] == variant and result["http_status"] is None
    assert "SECRET_TOKEN" not in json.dumps(result) and "private" not in json.dumps(result)


@pytest.mark.parametrize("value", [None, 0, 99, 600, 65535, True, "401", 401.0, {"value": 401}])
def test_invalid_or_absent_status_does_not_become_http_evidence(value):
    result = describe_provider_error({"codexErrorInfo": {"httpConnectionFailed": {"httpStatusCode": value}}})
    assert result["error_type"] == "httpConnectionFailed" and result["http_status"] is None
    assert "HTTP 401" not in result["message"]


@pytest.mark.parametrize("error", [None, "HTTP 401 TOKEN", [], {}, {"message": "HTTP 429 secret"},
                                   {"codexErrorInfo": "custom-token-secret"},
                                   {"codexErrorInfo": {"custom-token-secret": {"httpStatusCode": 401}}},
                                   {"codexErrorInfo": {"httpConnectionFailed": {}, "responseStreamDisconnected": {}}},
                                   {"codexErrorInfo": {"httpConnectionFailed": "SECRET"}}])
def test_unknown_and_ambiguous_shapes_are_not_inferred_from_raw_message(error):
    result = describe_provider_error(error)
    assert result["error_type"] == "unknown" and result["http_status"] is None
    assert "secret" not in json.dumps(result).lower()


def test_nested_unknown_values_cannot_escape_through_diagnostic_metadata():
    error = {"message": "TOKEN-123", "additionalDetails": "TOKEN-123",
             "codexErrorInfo": {"responseStreamDisconnected": {
                 "httpStatusCode": 502, "headers": {"Authorization": "TOKEN-123"},
                 "endpoint": "https://TOKEN-123.invalid/private"}}}
    result = describe_provider_error(error, will_retry="TOKEN-123")
    assert result["http_status"] == 502
    assert "TOKEN-123" not in json.dumps(result) and result["cli_will_retry"] is None


def test_retry_flag_is_metadata_and_does_not_imply_retry_has_run():
    result = describe_provider_error({"codexErrorInfo": "serverOverloaded"}, will_retry=True)
    assert result["cli_will_retry"] is True
    assert "由用户决定" in result["hint"]
    assert "正在重试" not in result["message"]


def test_429_leaves_quota_and_reset_decisions_to_user():
    result = describe_provider_error({"codexErrorInfo": {"httpConnectionFailed": {"httpStatusCode": 429}}})
    assert "由用户管理" in result["hint"] and "等待用户决定" in result["hint"]
    assert "5%" not in result["hint"]


def test_nonsteerable_kind_is_not_used_as_untrusted_display_text():
    result = describe_provider_error({"codexErrorInfo": {"activeTurnNotSteerable": {"turnKind": "SECRET"}}})
    assert result["error_type"] == "activeTurnNotSteerable"
    assert "SECRET" not in json.dumps(result)


def test_error_summary_can_be_built_from_failed_turn_without_raw_log():
    turn = {"id": "turn", "status": "failed", "error": {
        "message": "PRIVATE RAW DETAILS", "codexErrorInfo": {"responseStreamConnectionFailed": {"httpStatusCode": None}}}}
    result = describe_provider_error(turn["error"])
    assert "无法建立模型回复数据流" in result["message"]
    assert result["http_status"] is None and "PRIVATE" not in json.dumps(result)
