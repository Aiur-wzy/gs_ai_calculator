from __future__ import annotations

import copy
import json
from types import SimpleNamespace

import pytest

from genshin_app.equipment import new_plan
from genshin_app.equipment.files import fingerprint, input_view, json_bytes
from genshin_app.equipment.schema import PlanError
from genshin_app.task_evidence import (
    prepare_record,
    review_records,
    session_review,
    source_notebook,
    source_record,
)
from genshin_app.task_evidence_scope import evidence_scope
from genshin_app.workflow import new_audit, validate_audit
from genshin_app.workflow_workspace import export_sources, workspace


def test_original_text_stays_separate_from_assumptions_and_input_changes():
    plan = new_plan()
    source = prepare_record(source_record("必须维护冰底，不能固定假定。", "dsh:s:event", authority="dsh_user_message"), plan)
    requirement = prepare_record({"id": "r1", "type": "requirement", "description": "核实动态冰底", "disposition": "pending"}, plan)
    plan["revision"] += 1
    view = review_records([source, requirement], plan, task_id="task")
    assert view["task_sources"][0]["description"] == "必须维护冰底，不能固定假定。"
    assert view["notes"][0]["freshness"] == "current"
    assert view["notes"][0]["reference_states"][0]["state"] == "supplied_text"
    assert [d["id"] for d in view["differences"]] == ["r1"]
    assert view["authority"] == "advisory_only" and view["acceptance_effect"] == "none"


def test_dependencies_invalidate_only_changed_section_and_no_task_no_auto_action():
    plan = new_plan()
    plan["manual_template"]["characters"] = [{"id": "a"}]
    note = prepare_record({"id": "n", "type": "source_note", "description": "按当前角色",
                           "input_refs": ["/manual_template/characters"]}, plan)
    plan["revision"] += 1
    assert review_records([note], plan)["differences"] == []
    plan["manual_template"]["characters"][0]["id"] = "b"
    view = review_records([note], plan)
    assert view["notes"][0]["freshness"] == "stale"
    assert view["differences"][0]["actionable"] is False


def test_supersedes_retains_history_without_resurrecting_old_conclusion():
    plan = new_plan()
    records = [{"id": "old", "type": "requirement", "description": "未确定", "disposition": "pending"},
               {"id": "new", "type": "requirement", "description": "已在别处实现", "disposition": "implemented_elsewhere", "supersedes": ["old"]}]
    audit = new_audit(plan)
    audit["records"] = records
    validate_audit(audit)
    view = review_records(records, plan)
    assert len(audit["records"]) == 2 and view["superseded_ids"] == ["old"]
    assert len(view["requirements"]) == 1 and not view["differences"]
    records[0]["supersedes"] = ["new"]
    with pytest.raises(PlanError, match="循环"):
        validate_audit(audit)


def test_cli_workspace_preserves_plan_and_source_text_and_unknown_references(tmp_path):
    plan = new_plan()
    plan_path, audit_path, task = tmp_path / "plan.json", tmp_path / "work.json", tmp_path / "task.txt"
    plan_path.write_bytes(json_bytes(plan))
    task.write_text("两轮，动态反应。\n允许必要的等待。", encoding="utf-8")
    before = plan_path.read_bytes()
    result = workspace(plan_path, audit_path, {"operation": "append", "records": [
        {"id": "unknown", "type": "source_note", "description": "需核对", "source_refs": [{"reference": "missing"}]}]}, task_file=task)
    assert result["record_count"] == 2
    assert result["task_review"]["task_sources"][0]["description"] == task.read_text(encoding="utf-8")
    assert result["task_review"]["notes"][1]["reference_states"][0]["state"] == "unresolved"
    assert plan_path.read_bytes() == before
    assert workspace(plan_path, audit_path)["record_count"] == 2


def test_lossless_nested_refinement_and_links_export_notes_survive(tmp_path):
    value = {"snapshot": {"version": "test"}, "items": [{"entity_type": "weapon", "id": 42,
        "data": {"effects": [{"refinements": [{"refinement": 1, "description": "条件触发{LINK#42}，替换而非追加", "params": [1.5]}]}]}}]}
    notebook = source_notebook(value, reference="q")
    assert notebook["raw"] == value
    assert any("{LINK#42}" in row["text"] for row in notebook["text_sections"])
    result = export_sources(value, tmp_path)
    notes = tmp_path / result["sources"][0]["notes"]
    notes.write_text("我的解释", encoding="utf-8")
    export_sources(value, tmp_path, overwrite=True)
    assert notes.read_text(encoding="utf-8") == "我的解释"
    assert json.loads((tmp_path / result["sources"][0]["source"]).read_text(encoding="utf-8"))["raw"] == value["items"][0]


def test_current_observation_matches_but_stale_saved_result_cannot_prove_new_input():
    plan = new_plan()
    record = {"id": "verify", "type": "observation", "description": "检查执行次数",
              "expectation": {"source": {"resource": "plan", "pointer": "/manual_template/hits"},
                              "check": {"kind": "count", "min": 0, "max": 0}}}
    scope = evidence_scope({"release": "test"})
    saved = scoped_calculation(plan, scope)
    observed = review_records([record], plan, calculated=saved, scope=scope)["observations"][0]
    assert observed["status"] == "observed_match"
    plan["revision"] += 1
    stale = review_records([record], plan, calculated=saved, scope=scope)["observations"][0]
    assert stale == {"id": "verify", "status": "unknown", "reason": "no_current_calculation"}


def test_task_source_without_checklist_reports_unknown_not_game_failure():
    plan = new_plan()
    source = prepare_record(source_record("完成两轮", "file:task"), plan)
    result = review_records([source], plan, task_id="t")
    assert result["differences"][0]["state"] == "unknown"
    assert result["differences"][0]["id"] == "system.task_review_not_declared"
    assert result["acceptance_effect"] == "none"


def test_previously_unresolved_dependency_cannot_become_unchanged_proof():
    plan = new_plan()
    note = prepare_record({"id": "n", "type": "requirement", "description": "未知依赖",
                           "disposition": "adopted", "input_refs": ["/unavailable"]}, plan)
    plan["unavailable"] = [1]
    view = review_records([note], plan)
    assert view["requirements"][0]["freshness"] == "stale"
    assert view["differences"]


def test_source_reference_resolves_to_saved_original_and_supersession_never_hides_it():
    plan = new_plan()
    source = prepare_record(source_record("四次都要核对", "file:task"), plan)
    replacement = {"id": "interpretation", "type": "requirement", "description": "只做一次",
                   "disposition": "adopted", "source_refs": [{"reference": source["id"]}],
                   "supersedes": [source["id"]]}
    view = review_records([source, replacement], plan)
    assert view["task_sources"] == [source]
    assert view["requirements"][0]["reference_states"][0]["state"] == "resolved"


def test_caller_cannot_claim_original_host_message_authority():
    plan = new_plan()
    record = source_record("作者转述", "dsh:s:1", authority="dsh_user_message")
    record["basis"] = {"source_origin": "dsh_user_message"}
    declared = prepare_record(record, plan)
    assert declared["basis"]["source_origin"] == "author_declared"
    assert declared["source_refs"][0]["origin"] == "author_declared"
    captured = prepare_record(record, plan, task_source_origin="dsh_user_message")
    assert captured["basis"]["source_origin"] == "dsh_user_message"


def test_observation_cancellation_propagates_both_review_layers(monkeypatch):
    from genshin_engine.execution_control import ExecutionCancelled
    plan = new_plan()
    record = {"id": "verify", "type": "observation", "description": "检查次数",
              "expectation": {"source": {"resource": "plan", "pointer": "/manual_template/hits"},
                              "check": {"kind": "count", "min": 0}}}
    snapshot = {"release": "test"}
    scope = evidence_scope(snapshot)
    calculated = scoped_calculation(plan, scope)
    calls = []
    def cancel(*args, **kwargs):
        calls.append(1)
        raise ExecutionCancelled()
    monkeypatch.setattr("genshin_app.calculation_assistance.inspect_calculation_assistance", cancel)
    with pytest.raises(ExecutionCancelled):
        review_records([record], plan, calculated=calculated, scope=scope)
    session = SimpleNamespace(findings=[record], candidate=plan, session_id="s", calculated=calculated,
                              database_snapshot=snapshot)
    with pytest.raises(ExecutionCancelled):
        session_review(session, observe=True, database_current=True)
    assert len(calls) == 2


def test_optional_calculated_read_failure_does_not_commit_workspace_record(tmp_path):
    plan_path, audit_path = tmp_path / "plan.json", tmp_path / "work.json"
    plan_path.write_bytes(json_bytes(new_plan()))
    with pytest.raises(PlanError) as failure:
        workspace(plan_path, audit_path, {"operation": "append", "records": [
            {"id": "r", "type": "requirement", "description": "检查原题"}]},
            calculated_path=tmp_path / "missing.calculated.json")
    assert failure.value.reason == "input_file_not_found"
    assert not audit_path.exists()


def test_workspace_keeps_unbound_original_then_explicitly_binds_same_task(tmp_path, capsys):
    from genshin_app.cli import main

    task_path, audit_path, plan_path = (tmp_path / name for name in ("task.txt", "work.json", "plan.json"))
    task_path.write_text("完成两轮排轴。", encoding="utf-8")
    args = ["--data-dir", str(tmp_path), "workflow"]
    assert main([*args, "workspace", "--audit", str(audit_path), "--task-file", str(task_path)]) == 0
    first = json.loads(capsys.readouterr().out)["data"]
    original = first["task_review"]["task_sources"][0]
    task_id = first["task_review"]["task_id"]
    assert first["task_review"]["input_fingerprint"] is None
    assert not plan_path.exists()
    assert json.loads(audit_path.read_bytes())["binding"] == {"state": "unbound"}

    intent = {"id": "execution", "type": "decision", "description": "已核对原题",
              "execution_intent": "execute", "source_refs": [{"reference": original["id"]}]}
    unbound = workspace(None, audit_path, {"operation": "append", "records": [intent]})["task_review"]
    assert unbound["execution_intent"] == "execute"
    assert unbound["differences"][-1]["id"] == "system.task_plan_unbound"

    plan_path.write_bytes(json_bytes(new_plan()))
    assert workspace(plan_path, audit_path)["task_review"]["binding"] == "unbound"
    assert main([*args, "bind", "--audit", str(audit_path), "--plan", str(plan_path)]) == 0
    bound = json.loads(capsys.readouterr().out)["data"]
    assert bound["task_review"]["binding"] == "bound"
    assert bound["task_review"]["task_id"] == task_id
    assert bound["task_review"]["task_sources"] == [original]
    another_plan = tmp_path / "other.json"
    another_plan.write_bytes(json_bytes(new_plan()))
    with pytest.raises(PlanError) as failure:
        workspace(another_plan, audit_path, {"operation": "bind"})
    assert failure.value.reason == "audit_plan_mismatch"


def test_v1_workspace_read_is_unchanged_and_explicit_append_upgrades(tmp_path):
    plan, plan_path, audit_path = new_plan(), tmp_path / "plan.json", tmp_path / "work.json"
    plan_path.write_bytes(json_bytes(plan))
    audit = new_audit(plan)
    audit["records"].append({"id": "old", "type": "issue", "description": "旧意见"})
    old = json_bytes(audit)
    audit_path.write_bytes(old)
    assert workspace(plan_path, audit_path)["workspace_schema_version"] == 1
    assert audit_path.read_bytes() == old
    workspace(plan_path, audit_path, {"operation": "append", "records": [
        {"id": "new", "type": "decision", "description": "已替换旧意见", "supersedes": ["old"]}]})
    upgraded = json.loads(audit_path.read_bytes())
    assert upgraded["schema_version"] == 2
    assert upgraded["task_id"] == plan["plan_id"]
    assert upgraded["records"][0] == audit["records"][0]


def test_source_parts_keep_adoption_observation_and_new_database_separate(tmp_path):
    plan = new_plan()
    old, changed = evidence_scope({"release": "r1"}), evidence_scope({"release": "r2"})
    source = {"raw": {"desc": "自己攻击增益；全队获得精通；恢复能量"}, "source_version": "v1"}
    records = [prepare_record({"id": label, "type": "source_note", "description": label,
        "source_part": {"id": label, "label": label, "source_ref": {"reference": "weapon", "pointer": "/raw/desc"}},
        "disposition": disposition, "input_refs": ["/manual_template/hits"], "observation_refs": ["verify"]},
        plan, {"weapon": source}, scope=old) for label, disposition in (
        ("自己攻击增益", "adopted"), ("全队获得精通", "pending"), ("恢复能量", "not_applicable"))]
    records.append({"id": "verify", "type": "observation", "description": "检查次数",
                    "expectation": {"source": {"resource": "plan", "pointer": "/manual_template/hits"},
                                    "check": {"kind": "count", "min": 0, "max": 0}}})
    calculation = scoped_calculation(plan, old)
    view = review_records(records, plan, resources={"weapon": source}, calculated=calculation, scope=old)
    assert [row["disposition"] for row in view["notes"][:3]] == ["adopted", "pending", "not_applicable"]
    assert all(row["source_coverage"] == "declared_part" for row in view["notes"][:3])
    assert view["notes"][0]["observation_links"][0]["status"] == "observed_match"

    stale = review_records(records, plan, resources={"weapon": {**source, "source_version": "v2"}},
                           calculated=calculation, scope=changed)
    assert stale["notes"][0]["freshness"] == "stale"
    assert stale["notes"][0]["reference_states"][0]["state"] == "changed"
    assert stale["observations"][0]["reason"] == "calculation_database_changed"
    assert stale["report_basis"]["calculation_currency"] == "calculation_database_changed"


def test_source_notebook_snapshot_refresh_preserves_notes_and_marks_old_version(tmp_path):
    source = {"snapshot": {"version": "a"}, "items": [{"entity_type": "weapon", "id": 1, "data": {"text": "原文"}}]}
    created = export_sources(source, tmp_path)["sources"][0]
    notes_path = tmp_path / created["notes"]
    before = notes_path.read_bytes()
    source["snapshot"]["version"] = "b"
    updated = export_sources(source, tmp_path, overwrite=True)["sources"][0]
    assert updated["notes_freshness"] == "needs_review"
    assert updated["source_version"] != created["source_version"]
    assert notes_path.read_bytes() == before


def test_real_calculation_scope_does_not_change_result_admission(tmp_path):
    from genshin_app.equipment import run_plan_calc
    from genshin_app.task_evidence_scope import calculation_currency, evidence_scope
    from tests.test_calculation_overrides_execution import native_plan

    calculated = run_plan_calc(native_plan(), tmp_path / "absent.db")
    scope = evidence_scope({"state": "missing"})
    plan = calculated["plan"]
    assert calculated["result"]["summary"]["total_damage"] == 45
    assert calculation_currency(calculated, plan, scope) == "current"
    assert calculation_currency(calculated, plan, {**scope, "policy_fingerprint": "changed"}) == "calculation_policy_changed"
    calculated["result"]["summary"]["total_damage"] = 50
    assert calculation_currency(calculated, plan, scope) == "calculation_result_mismatch"


def scoped_calculation(plan, scope, **result_values):
    result = {"task_evidence_scope": scope, **result_values}
    return {"plan": copy.deepcopy(plan), "result": result, "result_receipt": {
        "task_evidence_scope": scope, "input_fingerprint": fingerprint(input_view(plan)),
        "result_fingerprint": fingerprint(result)}}


def test_workspace_checks_saved_result_and_input_identity_without_observations(tmp_path):
    plan = new_plan()
    plan_path, audit_path, calculated_path = (tmp_path / name for name in ("plan.json", "work.json", "calculated.json"))
    plan_path.write_bytes(json_bytes(plan))
    scope = evidence_scope({"state": "missing"})
    calculated = scoped_calculation(plan, scope, validation_scope={"summary": "已核实"})
    calculated["completion"] = {"task_admission": "accepted", "calculation_status": "complete"}

    def report():
        calculated_path.write_bytes(json_bytes({"calculated": calculated}))
        return workspace(plan_path, audit_path, calculated_path=calculated_path, data_dir=tmp_path)["task_review"]["report_basis"]

    valid = report()
    assert valid["calculation_currency"] == "current"
    assert valid["validation_summary"] == "已核实"
    assert valid["calculation_status"] == "diagnostic" and valid["task_admission"] is None
    calculated["result"]["validation_scope"]["summary"] = "未核实的改动"
    mismatch = report()
    assert mismatch["calculation_currency"] == "calculation_result_mismatch"
    assert all(mismatch[key] is None for key in ("validation_summary", "task_admission", "calculation_status", "result_fingerprint"))
    calculated["result_receipt"]["input_fingerprint"] = "0" * 64
    assert report()["calculation_currency"] == "calculation_input_unverified"


def test_legacy_record_without_scope_remains_readable_but_unverified():
    plan = new_plan()
    scope = evidence_scope({"release": "test"})
    legacy = prepare_record({"id": "old", "type": "source_note", "description": "旧版记录"}, plan)
    unverified = prepare_record({**legacy, "id": "unknown"}, plan, scope=evidence_scope(None))
    current = prepare_record({**legacy, "id": "verified"}, plan, scope=scope)
    review = review_records([legacy, unverified, current], plan, scope=scope)
    assert [row["freshness"] for row in review["notes"]] == ["unverified", "unverified", "current"]
    assert not review["differences"]


def test_optional_user_actions_use_actual_cards_and_preserve_result_status():
    from genshin_app.agent_chat.task_report_actions import project_task_actions

    context = {"workflow": "artifact", "candidate": {"task_review": {"report_basis": {"calculation_status": "complete"}}}}
    cards = [{"action_id": "a", "status": "presented", "action": "save_candidate"}]
    scope = SimpleNamespace(action_controller=SimpleNamespace(list_cards=lambda **kw: cards))
    project_task_actions(context, scope)
    basis = context["candidate"]["task_review"]["report_basis"]
    assert context["waiting_for_user"] is True
    assert basis["user_actions"][0]["action_id"] == cards[0]["action_id"]
    assert "文件操作卡" in basis["user_actions"][0]["user_step"]
    cards[0].update(status="completed", draft_publication={"path": "result.json"})
    project_task_actions(context, scope)
    assert context["waiting_for_user"] is False and not basis["user_actions"]
    assert basis["last_file_action"]["artifact_path"] == "result.json"
    assert basis["calculation_status"] == "complete"
