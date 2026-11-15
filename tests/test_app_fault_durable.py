"""Real durable receiver, cold readback, correlation and receiver failure."""
import json

import pytest

from genshin_app.app_fault_store import fault_scope, read_system_diagnostic
from genshin_app.app_system_fault import system_failure, validate_system_failure
from genshin_app.cli import main


def test_fault_is_readable_after_context_exit_and_via_public_cli(tmp_path, capsys):
    with fault_scope(tmp_path, conversation_id="conversation-1", turn_id="dsh-turn-2",
            call_id="provider-call", candidate_revision=5):
        result = system_failure(TypeError("private-content-never-record"),
            component="app_domain_tools", operation="gs_query_runtime")
    validate_system_failure(result)
    assert result["diagnostic_storage"] == {"state": "persisted"}
    identifier = result["system_fault"]["diagnostic_id"]
    record = read_system_diagnostic(tmp_path, identifier)
    assert record["context"] == {"conversation_id": "conversation-1", "turn_id": "dsh-turn-2",
                                  "call_id": "provider-call", "candidate_revision": 5}
    assert record["operation"] == "gs_query_runtime"
    assert "private-content" not in json.dumps(record)
    assert main(["--data-dir", str(tmp_path), "diagnostics", "show", "--id", identifier]) == 0
    output = capsys.readouterr()
    assert not output.err and json.loads(output.out)["data"] == record


def test_store_failure_preserves_fault_and_never_returns_a_fake_id(tmp_path, monkeypatch):
    from genshin_app import app_fault_store
    def fail(**_kwargs):
        raise OSError("private receiver location")
    monkeypatch.setattr(app_fault_store, "persist_system_diagnostic", fail)
    with fault_scope(tmp_path):
        result = system_failure(ImportError("private dependency"),
            component="python_engine_runtime", operation="gs_calculate")
    validate_system_failure(result)
    assert result["reason"] == "app_component_unavailable"
    assert result["system_fault"]["diagnostic_id"] is None
    assert result["diagnostic_storage"] == {"state": "failed", "reason": "diagnostic_persistence_failed"}
    assert "private" not in json.dumps(result)


def test_real_write_fsync_failure_does_not_claim_saved(tmp_path, monkeypatch):
    import os
    def fail(_fd):
        raise OSError("disk unavailable")
    monkeypatch.setattr(os, "fsync", fail)
    with fault_scope(tmp_path):
        response = system_failure(TypeError("sensitive args"), component="app_domain_tools", operation="gs_query_context")
    assert response["diagnostic_storage"]["state"] == "failed"
    assert response["system_fault"]["diagnostic_id"] is None
    assert not list((tmp_path / "logs/app-system-faults").glob("*.json"))


@pytest.mark.parametrize("identifier", ["../path", "C:\\secret", "", "A" * 64])
def test_lookup_rejects_paths_not_just_missing_files(tmp_path, identifier):
    from genshin_app.equipment.schema import PlanError
    with pytest.raises(PlanError) as caught:
        read_system_diagnostic(tmp_path, identifier)
    assert caught.value.reason == "invalid_diagnostic_id"


def test_tampered_diagnostic_extra_content_is_not_returned(tmp_path):
    with fault_scope(tmp_path):
        result = system_failure(TypeError(), component="app_domain_tools", operation="gs_read_context")
    identifier = result["system_fault"]["diagnostic_id"]
    path = tmp_path / "logs/app-system-faults" / (identifier + ".json")
    record = json.loads(path.read_text("utf-8"))
    record["private"] = "must-not-be-published"
    path.write_text(json.dumps(record), encoding="utf-8")
    from genshin_app.equipment.schema import PlanError
    with pytest.raises(PlanError, match="身份或结构损坏"):
        read_system_diagnostic(tmp_path, identifier)
