"""CLI composition is a thin adapter over the existing GUI candidate owner."""
# ruff: noqa: F811 - imported pytest fixtures are injected by name.
from __future__ import annotations

import copy
import json
import os
import subprocess
import sys

import pytest

from genshin_app.agent_chat.native_cli_cancellation import (
    CliRequestCancellation,
    cli_request_scope,
)
from genshin_app.agent_chat.native_cli_link import (
    call_live_candidate,
    workspace_cli_call,
)
from genshin_app.agent_chat.native_dsh_mux_client import NativeDshMuxError
from genshin_app.agent_chat.native_dsh_workspace import NativeDshWorkspaceRegistry
from genshin_app.equipment.files import fingerprint
from genshin_app.equipment.schema import PlanError
from genshin_engine.execution_control import ExecutionCancelled
from tests.test_common_input_editing import plan
from tests.test_energy_trials_provisional_readonly import (
    authorize_missing,
)
from tests.test_energy_trials_provisional_readonly import (
    session as provisional_session,  # noqa: F401 - imported fixture
)
from tests.test_equipment_plan import binding
from tests.test_native_dsh_candidate_application import command, ready_registry
from tests.test_provisional_authority import _normalized_question
from tests.test_queries import db_path as db_path  # noqa: PLC0414


@pytest.fixture
def owner(tmp_path, db_path):
    registry = NativeDshWorkspaceRegistry(tmp_path / "workspaces", database_path=db_path)
    registry.prepare_document("same-owner", "native", plan())
    scope = registry.activate("same-owner")
    try:
        yield registry, scope.candidate_session
    finally:
        registry.close()


def read(registry, pointer="/effect_requirements", **kwargs):
    return workspace_cli_call(registry, "read", "same-owner", {
        "resource": "report", "pointer": pointer, **kwargs,
    })


def test_gs_cli_compose_read_edit_share_report_revision_and_source_requirements(owner):
    registry, session = owner
    original = copy.deepcopy(session.candidate)
    original["effect_bindings"] = []
    for index in range(12):
        item = binding([])
        item.update(binding_id=f"source.{index}", provider_character_id="a")
        if index == 0:
            item["source_refs"][0]["note"] = "controlled source " * 1200
        original["effect_bindings"].append(item)
    session.replace_candidate(original, 0, "Install independent synthetic source requirements")
    before = fingerprint(session.candidate)
    assert session.call_tool("gs_compose", {"expected_revision": 1})["code"] == 0
    from_gs = session.call_tool("gs_read_context", {
        "resource": "report", "pointer": "/effect_requirements", "limit": 5,
    })
    first = read(registry, limit=5)
    assert from_gs == {"code": 0, "data": first}
    assert first["total"] == 12 and first["next_offset"] == 5
    assert first["items"][0]["value"]["reference"]["pointer"] == "/effect_requirements/0"
    assert read(registry, "/effect_requirements/0/source_fingerprint")["text"] == session.report["effect_requirements"][0]["source_fingerprint"]
    combined = [*first["items"], *read(registry, offset=5, limit=5)["items"],
                *read(registry, offset=10, limit=5)["items"]]
    assert [row["key"] for row in combined] == list(range(12))
    old_input = session.report["input_fingerprint"]
    assert workspace_cli_call(registry, "compose", "same-owner", {"expected_revision": 1})["revision"] == 1
    assert fingerprint(session.candidate) == before and session.calculated is None
    assert workspace_cli_call(registry, "edit", "same-owner", {
        "expected_revision": 1, "reason": "CLI changes the original input", "operations": [{
            "op": "replace", "path": "/manual_template/meta/name", "value": "new input",
        }],
    })["revision"] == 2
    assert session.report is None
    workspace_cli_call(registry, "compose", "same-owner", {"expected_revision": 2})
    assert session.report["input_fingerprint"] != old_input
    assert session.call_tool("gs_patch_candidate", {
        "expected_revision": 2, "reason": "GS continues the same candidate", "operations": [{
            "op": "replace", "path": "/manual_template/meta/name", "value": "GS input",
        }],
    })["code"] == 0
    assert workspace_cli_call(registry, "read", "same-owner", {})["summary"]["revision"] == 3
    assert len(registry._workspaces) == 1


@pytest.mark.parametrize("payload,reason", [
    ({"expected_revision": 1}, "agent_stale_revision"),
    ({"expected_revision": True}, "agent_tool_input"),
    ({"expected_revision": -1}, "agent_tool_input"),
    ({"expected_revision": "0"}, "agent_tool_input"),
    ({}, "cli_request_invalid"),
    ({"expected_revision": 0, "apply": True}, "cli_request_invalid"),
])
def test_compose_rejects_invalid_or_stale_revision_without_a_new_owner(owner, payload, reason):
    registry, session = owner
    before = fingerprint(session.candidate)
    with pytest.raises(PlanError) as failure:
        workspace_cli_call(registry, "compose", "same-owner", payload)
    assert failure.value.reason == reason
    assert session.revision == 0 and session.report is None
    assert fingerprint(session.candidate) == before and len(registry._workspaces) == 1


@pytest.mark.parametrize("extra", [
    {"limit": 0}, {"limit": 201}, {"offset": -1}, {"offset": True},
    {"pointer": 2}, {"unexpected": True},
])
def test_nonempty_cli_read_uses_gs_schema_and_empty_read_still_returns_snapshot(owner, extra):
    registry, session = owner
    request = {"resource": "candidate", **extra}
    with pytest.raises(PlanError) as failure:
        workspace_cli_call(registry, "read", "same-owner", request)
    gs = session.call_tool("gs_read_context", request)
    assert failure.value.reason == gs["reason"] == "agent_tool_input"
    assert workspace_cli_call(registry, "read", "same-owner", {})["input"] == session.candidate


def test_unprepared_whiteboard_is_not_implicitly_converted(tmp_path, db_path):
    registry = NativeDshWorkspaceRegistry(tmp_path / "blank", database_path=db_path)
    registry.prepare_document("blank", None, None)
    try:
        with pytest.raises(PlanError) as failure:
            workspace_cli_call(registry, "compose", "blank", {"expected_revision": 0})
        assert failure.value.reason == "agent_native_artifact_required"
        assert registry.activate("blank").candidate_session._is_board()
    finally:
        registry.close()


def test_real_cli_process_compose_read_and_edit_without_implicit_calculation(owner, tmp_path):
    registry, session = owner
    registry.enable_cli_link()
    connection = registry.cli_connection("same-owner")
    base = [sys.executable, "-m", "genshin_app", "agent", "candidate"]
    environment = {**os.environ, "PYTHONUTF8": "1"}

    def invoke(operation, request=None):
        arguments = [*base, operation, "--connection", connection, "--session-id", "same-owner"]
        if request is not None:
            path = tmp_path / (operation + ".json")
            # A real UTF-8 BOM written by Windows PowerShell remains valid JSON input.
            path.write_text(json.dumps(request), encoding="utf-8-sig")
            arguments.extend(["--request", str(path)])
        result = subprocess.run(arguments, capture_output=True, text=True, encoding="utf-8",
            timeout=30, check=False, env=environment)
        assert result.returncode == 0, (result.stdout, result.stderr)
        return json.loads(result.stdout)["data"]

    assert invoke("compose", {"expected_revision": 0})["revision"] == 0
    assert invoke("read", {"resource": "report", "pointer": "/effect_requirements"}) == read(registry)
    assert invoke("edit", {"expected_revision": 0, "reason": "actual CLI subprocess edit", "operations": [{
        "op": "replace", "path": "/manual_template/meta/name", "value": "CLI subprocess",
    }]})["revision"] == 1
    invoke("compose", {"expected_revision": 1})
    assert session.report and session.calculated is None and session.revision == 1
    assert registry.application_port.candidate_application_intent(
        registry.lookup_binding("same-owner")["conversation_id"])["status"] == "none"
    with pytest.raises(PlanError, match="原会话"):
        call_live_candidate(connection, "compose", "other-owner", {"expected_revision": 1})
    with pytest.raises(PlanError) as failure:
        workspace_cli_call(registry, "candidate.apply", "same-owner", {})
    assert failure.value.reason == "cli_request_invalid"


def test_prepared_formal_application_rejects_derived_compose_but_allows_report_read(tmp_path, db_path):
    registry, scope = ready_registry(tmp_path / "prepared", db_path)
    try:
        session = scope.candidate_session
        base = {"kind": "native", "expected_revision": 0,
                "expected_receipt": session.summary()["receipt_fingerprint"]}
        reviewed = command(registry, "one", "candidate.review", {**base, "document": session.document})
        target = reviewed["result"]["plan"]
        command(registry, "one", "candidate.apply.prepare", {**base, "document": target,
            "baseline_payload": {"kind": "native", "plan": session.document},
            "target_payload": {"kind": "native", "plan": target}})
        with pytest.raises(NativeDshMuxError, match="dsh_candidate_application_pending"):
            workspace_cli_call(registry, "compose", "one", {"expected_revision": 0})
        assert workspace_cli_call(registry, "read", "one", {"resource": "report", "pointer": "/state"})["text"] == "ready"
    finally:
        registry.close()


def test_compose_observes_existing_execution_cancellation_before_mutation(owner):
    registry, session = owner
    control = CliRequestCancellation()
    with cli_request_scope(control):
        control.cancel()
        with pytest.raises(ExecutionCancelled):
            workspace_cli_call(registry, "compose", "same-owner", {"expected_revision": 0})
    assert session.report is None and session.revision == 0


def test_compose_running_real_pipeline_observes_request_cancellation(owner, monkeypatch):
    from genshin_app.agent_chat import candidate

    registry, session = owner
    original = candidate.compose_equipment_plan
    control = CliRequestCancellation()
    entered = []

    def cancel_after_admission(*args, **kwargs):
        entered.append(kwargs.get("candidate_session"))
        control.cancel()
        return original(*args, **kwargs)

    monkeypatch.setattr(candidate, "compose_equipment_plan", cancel_after_admission)
    with cli_request_scope(control), pytest.raises(ExecutionCancelled):
        workspace_cli_call(registry, "compose", "same-owner", {"expected_revision": 0})
    assert entered == [session] and session.report is None and session.revision == 0


def test_compose_preserves_same_session_d2_attestation_and_does_not_self_authorize(
    provisional_session, tmp_path,
):
    registry = NativeDshWorkspaceRegistry(tmp_path / "provisional-owner", database_path=provisional_session.db_path)
    registry.prepare_document("d2-owner", "native", provisional_session.candidate)
    session = registry.activate("d2-owner").candidate_session
    try:
        workspace_cli_call(registry, "compose", "d2-owner", {"expected_revision": session.revision})
        assert session.report["state"] == "pending"
        assert session.provisional_authority["attestation_ledger"] == {}
        assert session.provisional_authority["approval_ledger"] == {}
        authorize_missing(session)
        authority = copy.deepcopy(session.provisional_authority)
        gs = session.call_tool("gs_compose", {"expected_revision": session.revision})
        assert gs["code"] == 0, gs
        reference = copy.deepcopy(session.report)
        workspace_cli_call(registry, "compose", "d2-owner", {"expected_revision": session.revision})
        assert session.report == reference and session.report["state"] == "ready"
        assert session.provisional_authority == authority and session.calculated is None
    finally:
        registry.close()


def test_compose_keeps_existing_public_patch_user_approval_required(
    provisional_session, tmp_path, monkeypatch,
):
    from genshin_db import queries

    source = copy.deepcopy(provisional_session.candidate)
    source["provisional_entities"][0].update(
        authorization_kind="public_data_patch", display_name="数据库武器")
    monkeypatch.setattr(queries, "list_weapons", lambda *a, **kw: {
        "items": [{"id": 9001, "name": "数据库武器"}], "total": 1, "page": 1, "page_size": 100})
    monkeypatch.setattr(queries, "get_weapon_detail", lambda *a, **kw: {
        "weapon": {"id": 9001, "name": "数据库武器", "weapon_type": "sword", "base_atk": 740}})
    monkeypatch.setattr(queries, "get_weapon_stats", lambda *a, **kw: {"properties": [{
        "prop_type": "FIGHT_PROP_BASE_ATTACK", "unit": "flat", "value": 740,
        "source_refs": [{"kind": "database", "table": "weapon_property"}]}]})
    registry = NativeDshWorkspaceRegistry(tmp_path / "patch-owner", database_path=provisional_session.db_path)
    registry.prepare_document("patch-owner", "native", source)
    session = registry.activate("patch-owner").candidate_session
    try:
        session.verify_provisional_lookup(provisional_id="local.weapon", expected_revision=session.revision,
            database_id=None, reason="actual controlled existing fact lookup")
        workspace_cli_call(registry, "compose", "patch-owner", {"expected_revision": session.revision})
        assert session.report["state"] == "pending" and session.provisional_authority["approval_ledger"] == {}
        proposed = session.request_provisional_public_patch_approval(
            provisional_id="local.weapon", expected_revision=session.revision,
            reason="request exact controlled public fact correction")
        normalized = _normalized_question(proposed["question"])
        session.bind_provisional_approval_question(
            batch_id="approval-batch", call_id="approval-call", items=[normalized])
        session.settle_provisional_approval_question(question={
            "question_id": "approval-batch", "answer_receipt": "host-received-exact-answer", "items": [normalized],
            "answer": {"items": [{"question_id": normalized["question_id"],
                "value_ids": [normalized["options"][0]["value_id"]], "text": None, "skipped": False}]}},
            reason="Host settles the exact user decision")
        authority = copy.deepcopy(session.provisional_authority)
        workspace_cli_call(registry, "compose", "patch-owner", {"expected_revision": session.revision})
        assert session.report["state"] == "ready" and session.provisional_authority == authority
        assert session.calculated is None
    finally:
        registry.close()


def test_compose_is_discoverable_in_actual_cli_capabilities_and_needs_a_request(tmp_path, capsys):
    from genshin_app.cli import main

    assert main(["capabilities", "--command", "agent candidate compose"]) == 0
    capabilities = json.loads(capsys.readouterr().out)
    assert capabilities["code"] == 0
    serialized = json.dumps(capabilities, ensure_ascii=False)
    assert "compose" in serialized and "expected_revision" in serialized
    assert main(["--data-dir", str(tmp_path), "agent", "candidate", "compose",
        "--connection", "not-opened.json", "--session-id", "same-owner"]) != 0
    failure = json.loads(capsys.readouterr().err)
    assert "--request" in failure["message"]
