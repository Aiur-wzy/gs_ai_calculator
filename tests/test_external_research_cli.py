from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from genshin_app import cli
from genshin_app.agent_chat.native_research_ledger import NativeResearchLedger
from genshin_app.agent_chat.native_research_tools import LIST_TOOL
from genshin_app.agent_chat.native_research_tools import call as native_call
from genshin_app.equipment.files import fingerprint, input_view
from genshin_app.equipment.schema import PlanError, new_plan
from genshin_app.external_research import ExternalResearchService, research_capabilities
from genshin_app.output_policy import cli_output_scope, validate_output_paths


@pytest.fixture
def subject(tmp_path):
    plan = new_plan()
    path = tmp_path / "plan.json"
    path.write_text(json.dumps(plan), encoding="utf-8")
    service = ExternalResearchService(tmp_path / "profile", path, "test-task")
    return service, path, plan


def request(plan, *, request_id="lookup-1", ledger_revision=0):
    return {"contract": "external-research-request-v1", "request_id": request_id,
            "expected_ledger_revision": ledger_revision, "candidate_revision": plan["revision"],
            "query": "公开技能持续时间与同项刷新规则",
            "sources": [{"url": "https://example.com/mechanic", "title": "公开来源"}],
            "source_observed_at": "2026-11-01T12:00:00+08:00"}


def usage(research_id, candidate_id):
    return {"research_id": research_id, "disposition": "adopted", "claim_summary": "用于核对持续时间",
            "applied_to": [candidate_id], "system_gap_class": "database_missing", "reason": "当前资料未覆盖"}


def report(plan, rows, ledger_revision=1):
    return {"contract": "external-research-usage-report-v1", "candidate_revision": plan["revision"],
            "research_ledger_revision": ledger_revision, "entries": rows, "complete_for_turn": True}


def test_external_declaration_survives_fresh_open_and_does_not_forge_execution(subject):
    service, path, plan = subject
    result = service.call("append", request(plan))
    assert result["authority"] == "external_declared"
    fresh = ExternalResearchService(service.data_dir, path, "test-task")
    listed = fresh.call("list")
    row = listed["items"][0]
    assert row["status"] == "external_declared" and not listed["network_execution_verified"]
    assert row["source_observed_at"] != row["registered_at"]
    assert not {"epoch_id", "workflow_stage", "started_at", "completed_at", "tool"} & set(row)
    assert row["task_binding"]["candidate_fingerprint"] == fingerprint(input_view(plan))
    first = fresh._ledger().path.read_bytes()
    repeated = fresh.call("append", request(plan))
    assert repeated["status"] == "already_recorded" and repeated["research_id"] == result["research_id"]
    assert fresh._ledger().path.read_bytes() == first
    accepted = fresh.call("report", report(plan, [usage(result["research_id"], plan["plan_id"])]))
    assert accepted["status"] == "complete"
    saved = fresh._ledger().path.read_bytes()
    assert fresh.call("report", report(plan, [usage(result["research_id"], plan["plan_id"])]))["status"] == "complete"
    assert fresh._ledger().path.read_bytes() == saved


def test_candidate_advance_preserves_old_binding_and_requires_explicit_bind(subject):
    service, path, plan = subject
    created = service.call("append", request(plan))
    old_binding = service.call("list")["binding"]
    service.call("report", report(plan, [usage(created["research_id"], plan["plan_id"])]))
    plan["revision"] += 1
    plan["manual_template"]["meta"]["name"] = "修订后的候选"
    path.write_text(json.dumps(plan), encoding="utf-8")
    listed = service.call("list")
    assert listed["requires_candidate_rebind"]
    assert listed["items"][0]["binding_state"] == "previous_revision"
    assert listed["items"][0]["usage_binding"] == old_binding
    with pytest.raises(PlanError, match="先明确绑定"):
        service.call("append", request(plan, request_id="lookup-2", ledger_revision=1))
    service.call("bind", {"candidate_revision": plan["revision"], "expected_ledger_revision": 1})
    assert service.call("report", report(plan, [usage(created["research_id"], plan["plan_id"])]))["status"] == "complete"
    stored = service._ledger().read()
    assert stored["entries"][0]["task_binding"] == old_binding
    assert len(stored["reports"]) == 2
    assert service.call("list")["current_binding_reports"][0]["external_binding"]["candidate_revision"] == 1
    plan["revision"] = 0
    path.write_text(json.dumps(plan), encoding="utf-8")
    with pytest.raises(PlanError) as error:
        service.call("bind", {"candidate_revision": 0, "expected_ledger_revision": 1})
    assert error.value.reason == "stale_research_binding"


@pytest.mark.parametrize("mutation", ["candidate", "same_revision_change", "request_reuse", "native_fields", "bad_refs"])
def test_context_and_provenance_attacks_leave_ledger_unchanged(subject, mutation):
    service, path, plan = subject
    created = service.call("append", request(plan))
    previous = service._ledger().path.read_bytes()
    operation, body = "append", request(plan)
    if mutation == "candidate":
        plan["plan_id"] = "different-candidate"
        path.write_text(json.dumps(plan), encoding="utf-8")
    elif mutation == "same_revision_change":
        plan["manual_template"]["meta"]["name"] = "changed without revision"
        path.write_text(json.dumps(plan), encoding="utf-8")
    elif mutation == "request_reuse":
        body["query"] = "different query"
    elif mutation == "native_fields":
        body.update(authority="native", tool="web_search", status="completed", started_at="2026-11-01T00:00:00Z")
    else:
        operation, body = "report", report(plan, [usage(created["research_id"], "another-task.action")])
    with pytest.raises(PlanError):
        service.call(operation, body)
    assert service._ledger().path.read_bytes() == previous


def test_external_rows_cannot_use_native_register_settle_list_or_report(subject):
    service, _, plan = subject
    created = service.call("append", request(plan))
    store = service._ledger()
    before = store.path.read_bytes()
    for action in (
        lambda: store.register(expected_revision=1, turn_id="native-turn", epoch_id="epoch-1", workflow_stage="artifact",
            call_id="native-call", tool="web_search", arguments={"queries": ["mechanism"]}, raw_arguments_hash="f" * 64),
        lambda: store.settle(research_id=created["research_id"], status="completed", content_hash="a" * 64,
            presentation_meta={"sources": [], "truncated": False}),
        lambda: native_call(LIST_TOOL, {}, ledger=store, turn_id="native-turn"),
        lambda: store.report_usage(turn_id="native-turn", research_ledger_revision=1, entries=[], complete_for_turn=True),
    ):
        with pytest.raises(PlanError) as error:
            action()
        assert error.value.reason == "research_authority_mismatch"
        assert store.path.read_bytes() == before


def test_existing_native_facts_are_immutable_to_external_api(tmp_path):
    native = NativeResearchLedger(tmp_path / "native", conversation_id="native-conversation")
    result = native.register(expected_revision=0, turn_id="turn-1", epoch_id="epoch-1", workflow_stage="artifact",
        call_id="call-1", tool="web_search", arguments={"queries": ["already searched"]}, raw_arguments_hash="f" * 64)
    native.settle(research_id=result["research_id"], status="completed", content_hash="a" * 64,
        presentation_meta={"sources": [], "truncated": False})
    before = native.path.read_bytes()
    context = {"task_id": "task-1", "candidate_id": "candidate-1", "candidate_revision": 0, "candidate_fingerprint": "a" * 64}
    with pytest.raises(PlanError):
        native.bind_external(context=context, expected_revision=1)
    assert native.path.read_bytes() == before


def test_cli_capabilities_schema_and_true_fresh_process_chain(subject, tmp_path, capsys, monkeypatch):
    service, path, plan = subject
    monkeypatch.setattr(cli, "_require_runtime_database", lambda *_: pytest.fail("research must work while DB is unavailable"))
    assert cli.main(["research", "schema"]) == 0
    capability = json.loads(capsys.readouterr().out)["data"]
    assert capability["storage_owner"] == "NativeResearchLedger"
    assert cli._capabilities_doc()["external_research_contract"] == capability
    capability["schemas"]["append"]["properties"]["request_id"]["maxLength"] = 1
    assert research_capabilities()["schemas"]["append"]["properties"]["request_id"]["maxLength"] == 128
    source = tmp_path / "request.json"
    source.write_text(json.dumps(request(plan)), encoding="utf-8")
    base = ["--data-dir", str(service.data_dir), "research"]
    assert cli.main([*base, "append", "--plan", str(path), "--task-id", "test-task", "--input", str(source)]) == 0
    appended = json.loads(capsys.readouterr().out)["data"]
    env = {**os.environ, "PYTHONPATH": str(Path("src").resolve())}
    process = subprocess.run([sys.executable, "-m", "genshin_app", *base, "list", "--plan", str(path), "--task-id", "test-task"],
        env=env, capture_output=True, text=True, encoding="utf-8", check=False, timeout=15,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    assert process.returncode == 0, process.stderr
    fresh = json.loads(process.stdout)["data"]
    assert fresh["items"][0]["research_id"] == appended["research_id"]
    report_file = tmp_path / "report.json"
    report_file.write_text(json.dumps(report(plan, [])), encoding="utf-8")
    assert cli.main([*base, "report", "--plan", str(path), "--task-id", "test-task", "--input", str(report_file)]) == 1
    assert json.loads(capsys.readouterr().err)["reason"] == "research_report_incomplete"
    report_file.write_text(json.dumps(report(plan, [usage(appended["research_id"], plan["plan_id"])])), encoding="utf-8")
    assert cli.main([*base, "report", "--plan", str(path), "--task-id", "test-task", "--input", str(report_file)]) == 0
    assert json.loads(capsys.readouterr().out)["data"]["status"] == "complete"


@pytest.mark.parametrize("bad", [{"applied_to": [{}]}, {"disposition": "native_verified"}, {"research_id": "forged-id"}])
def test_invalid_report_is_domain_input_failure(subject, bad):
    service, _, plan = subject
    created = service.call("append", request(plan))
    row = {**usage(created["research_id"], plan["plan_id"]), **bad}
    with pytest.raises(PlanError):
        service.call("report", report(plan, [row]))


def test_research_owner_scope_cannot_write_other_managed_files_or_inputs(subject, tmp_path):
    service, path, _ = subject
    args = cli.build_parser().parse_args(["--data-dir", str(service.data_dir), "research", "append",
        "--plan", str(path), "--task-id", "test-task", "--input", str(tmp_path / "request.json")])
    owned = service._ledger().path
    with cli_output_scope(args):
        validate_output_paths([owned])
        for forbidden in (path, tmp_path / "request.json", service.data_dir / "genshin.db",
                          service.data_dir / "native_agent_sessions_v2/other.json",
                          service.data_dir / "native-dsh-research/native-conversation/native-research-ledger-v1.json"):
            with pytest.raises(PlanError):
                validate_output_paths([forbidden])

    def impostor(_):
        return None
    impostor.__name__ = "_cmd_research"
    args._handler = impostor
    with cli_output_scope(args), pytest.raises(PlanError):
        validate_output_paths([owned])
    args._handler = cli._cmd_research
    args._command_path = ("equipment", "calc")
    with cli_output_scope(args), pytest.raises(PlanError):
        validate_output_paths([owned])
