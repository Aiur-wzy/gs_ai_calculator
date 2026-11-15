from __future__ import annotations

import copy
import uuid

import pytest

from genshin_app.agent_chat.native_tool_receipt import sanitize_native_response
from genshin_app.app_system_fault import (
    AppComponentInitializationError,
    safe_exception_diagnostic,
    system_failure,
    validate_system_failure,
)
from genshin_app.equipment.schema import PlanError
from genshin_db.validation import QueryValidationError, positive_int


@pytest.mark.parametrize(("error", "cause", "reason"), [
    (ModuleNotFoundError("schema unavailable secret=NEVER"), "module_unavailable", "app_component_unavailable"),
    (AppComponentInitializationError("sensitive path"), "dependency_initialization_failed", "app_component_unavailable"),
    (TypeError("internal schema error"), "internal_exception", "app_internal_error"),
])
def test_fault_is_not_a_fillable_task_issue(error, cause, reason, caplog):
    response = system_failure(error, component="python_engine_runtime", operation="gs_read_context")
    validate_system_failure(response)
    assert sanitize_native_response(response) == response
    assert response["reason"] == reason
    assert response["system_fault"]["cause_code"] == cause
    assert uuid.UUID(response["system_fault"]["diagnostic_id"])
    assert response["system_fault"]["diagnostic_id"] in caplog.text
    assert "NEVER" not in str(response) + caplog.text
    assert "category" not in response and "blocking_scope" not in response
    assert response["recovery"] == {"action": "report_system_fault", "retry_same_request": False}


@pytest.mark.parametrize("field,value", [
    ("category", "request_error"), ("blocking_scope", "request"),
    ("retryable", True), ("reason", "missing_input"), ("code", True),
    ("recovery", {"action": "correct_arguments", "retry_same_request": True}),
    ("recovery", {"action": "report_system_fault", "retry_same_request": 0}),
    ("system_fault", {"contract": "unknown"}), ("failure_kind", "request_error"),
])
def test_fault_rejects_shape_drift_in_native_receipt(field, value):
    response = system_failure(RuntimeError(), component="app_domain_tools", operation="gs_compose")
    response[field] = copy.deepcopy(value)
    with pytest.raises(PlanError, match="格式无效"):
        sanitize_native_response(response)


def test_legacy_request_validation_keeps_valueerror_abi_and_typed_reason():
    with pytest.raises(QueryValidationError) as caught:
        positive_int(None, "id")
    assert isinstance(caught.value, ValueError)
    assert caught.value.reason == "invalid_query_request"


def test_safe_diagnostic_never_reads_source_or_exception_body(monkeypatch, caplog):
    import linecache
    import traceback

    from genshin_app.agent_chat.native_pipe import _timeout_ms

    try:
        _timeout_ms(7200)
    except ValueError as error:
        captured = error

    def forbidden(*_args, **_kwargs):
        pytest.fail("safe diagnostic attempted a source/linecache read")

    with monkeypatch.context() as guard:
        guard.setattr(linecache, "getline", forbidden)
        guard.setattr(linecache, "getlines", forbidden)
        guard.setattr(traceback, "extract_tb", forbidden)
        diagnostic = safe_exception_diagnostic(captured)
        response = system_failure(captured, component="app_domain_binding", operation="gui_workflow.transport_boot")
    validate_system_failure(response)
    assert diagnostic["exception_type"] == "ValueError"
    assert diagnostic["frames"][-1]["module"] == "genshin_app.agent_chat.native_pipe"
    assert diagnostic["frames"][-1]["function"] == "_timeout_ms"
    assert "timeout must" not in str(diagnostic) + caplog.text


def test_untrusted_dynamic_code_and_exception_class_names_are_not_published(caplog):
    secret = "DO_NOT_PUBLISH_KEY_OR_PATH"
    namespace = {"__name__": "external_" + secret,
                 "RuntimeError": type(secret, (RuntimeError,), {})}
    try:
        # Fixed test source simulates an untrusted filename/type; no user code.
        exec(compile("raise RuntimeError('private body')", "C:/private/" + secret + ".py", "exec"), namespace)  # noqa: S102
    except RuntimeError as error:
        diagnostic = safe_exception_diagnostic(error)
        response = system_failure(error, component="app_domain_binding", operation="gui_workflow.app_factory")
    assert diagnostic == {"exception_type": "RuntimeError", "frames": []}
    validate_system_failure(response)
    assert secret not in str(response) + str(diagnostic) + caplog.text
    assert "private body" not in str(response) + str(diagnostic) + caplog.text
