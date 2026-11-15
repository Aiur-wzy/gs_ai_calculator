"""Unanticipated projection faults retain safe code locations and settlement."""
from __future__ import annotations

import json

import pytest

from genshin_app.agent_chat.dsh_gui_workflow_runner import (
    GuiWorkflowError,
    run_gui_agent_workflow,
)
from genshin_app.app_system_fault import validate_system_failure


@pytest.mark.parametrize("error", [ValueError("SENSITIVE_KEY_123 path=C:/private/task"),
                                  ModuleNotFoundError("SENSITIVE_KEY_123 private.module")])
def test_unknown_factory_fault_is_published_with_safe_diagnostic(tmp_path, caplog, error):
    def factory(_data):
        raise error

    target = tmp_path / "output"
    receipt = run_gui_agent_workflow(data_dir=tmp_path / "data", prompt="offline fault probe",
                                    output_dir=target, api_factory=factory)
    expected = "app_component_unavailable" if isinstance(error, ImportError) else "app_internal_error"
    assert receipt["status"] == "failed" and receipt["reason"] == expected
    details = receipt["details"]
    assert details["workflow_stage"] == "app_factory"
    validate_system_failure(details["system_failure"])
    fault = details["system_failure"]["system_fault"]
    assert fault["component"] == "app_domain_binding"
    assert fault["operation"] == "gui_workflow.app_factory"
    diagnostic = details["diagnostic"]
    assert diagnostic["exception_type"] == type(error).__name__
    assert diagnostic["frames"] and len(diagnostic["frames"]) <= 16
    assert all(set(frame) == {"module", "function", "line"} and type(frame["line"]) is int
               for frame in diagnostic["frames"])
    assert diagnostic["frames"][-1]["module"] == "genshin_app.agent_chat.dsh_gui_workflow_runner"
    assert diagnostic["frames"][-1]["function"] == "run_gui_agent_workflow"
    assert receipt["receipt_publication"]["status"] == "published"
    persisted = (target / "gui-workflow-receipt.json").read_text(encoding="utf-8")
    assert json.loads(persisted) == receipt
    assert "SENSITIVE_KEY_123" not in persisted + caplog.text
    assert "C:/private" not in persisted + caplog.text
    assert str(tmp_path) not in json.dumps(details) + caplog.text
    assert fault["diagnostic_id"] in caplog.text
    assert "board" not in receipt and "calculated" not in receipt


def test_unknown_boot_fault_has_exact_stage_without_session_or_replay(tmp_path):
    calls = []

    class BootFailureApi:
        def document_json_call(self, method, _arguments):
            calls.append(method)
            pytest.fail("transport boot failed before any document operation")

        def dsh_client_boot(self):
            calls.append("boot")
            raise ValueError("private 7200 timeout detail")

        def _close_dsh_shell(self):
            calls.append("close")

    receipt = run_gui_agent_workflow(data_dir=tmp_path / "data", prompt="offline boot probe",
                                    output_dir=tmp_path / "output", api_factory=lambda _: BootFailureApi())
    assert receipt["details"]["workflow_stage"] == "transport_boot"
    assert receipt["details"]["system_failure"]["system_fault"]["operation"] == "gui_workflow.transport_boot"
    assert receipt["details"]["diagnostic"]["exception_type"] == "ValueError"
    assert calls == ["boot", "close"]
    assert "session_id" not in receipt and "candidate" not in receipt
    assert "private 7200" not in json.dumps(receipt)


@pytest.mark.parametrize("details", [None, {"published_paths": ["isolated/result.json"], "pending_paths": []}])
def test_typed_fault_retains_exact_reason_and_details(tmp_path, details):
    def factory(_data):
        raise GuiWorkflowError("typed producer message", reason="known_domain_rejection", details=details)

    receipt = run_gui_agent_workflow(data_dir=tmp_path / "data", prompt="offline typed probe",
                                    output_dir=tmp_path / "output", api_factory=factory)
    assert receipt["reason"] == "known_domain_rejection"
    assert receipt["details"] == details
    assert receipt["receipt_publication"]["status"] == "published"


def test_untyped_publication_failure_retains_primary_typed_settlement(tmp_path, monkeypatch):
    from genshin_app import output_policy

    def factory(_data):
        raise GuiWorkflowError("typed primary", reason="candidate_not_reviewable", details={"revision": 5})

    def fail_report(*_args, **_kwargs):
        raise OSError("SENSITIVE_KEY_123 inaccessible private path")

    monkeypatch.setattr(output_policy, "publish_ordinary_files", fail_report)
    receipt = run_gui_agent_workflow(data_dir=tmp_path / "data", prompt="offline report probe",
                                    output_dir=tmp_path / "output", api_factory=factory)
    assert receipt["reason"] == "candidate_not_reviewable" and receipt["details"] == {"revision": 5}
    publication = receipt["receipt_publication"]
    assert publication["status"] == "failed" and publication["reason"] == "app_internal_error"
    assert publication["details"]["workflow_stage"] == "receipt_publication"
    assert publication["details"]["diagnostic"]["exception_type"] == "OSError"
    assert "SENSITIVE_KEY_123" not in json.dumps(receipt)
