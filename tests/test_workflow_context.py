"""RF04/RF06: cache invalidation, durable audit views and real CLI file boundaries."""

# ruff: noqa: F811 - pytest resolves imported fixtures through function arguments.

import copy
import json
import sqlite3
from pathlib import Path

import pytest

pytestmark = pytest.mark.usefixtures("isolated_cli_database_admission")

from genshin_app import query_cache, workflow
from genshin_app.cli import main
from genshin_app.equipment import new_plan
from genshin_app.equipment.files import fingerprint, input_view
from genshin_app.equipment.schema import PlanError
from tests.test_context_query import context_db  # noqa: F401
from tests.test_query_descriptions import description_db, legacy_db  # noqa: F401


def _write(path, data):
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")


def _call(args, capsys):
    status = main(args)
    captured = capsys.readouterr()
    return status, json.loads(captured.err if status else captured.out)


@pytest.fixture
def workflow_plan(tmp_path):
    plan = new_plan()
    plan["manual_template"]["characters"] = [{"id": "c", "level": 90, "stats": {"atk": 100}}]
    path = tmp_path / "plan.json"
    _write(path, plan)
    return plan, path


def _lookup():
    return {"id": "lookup.1", "type": "lookup", "description": "核对通用产球条件。",
            "gap_type": "missing", "local_checked": ["角色技能正文及追加说明"],
            "source_refs": [{"url": "https://example.org/skill", "locator": "Particles", "version": "1.0"}],
            "adopted_refs": ["mechanics_model.rules[0]"]}


def test_cache_reuses_query_and_refresh_bypasses_it(context_db, tmp_path, monkeypatch):
    request = {"characters": [{"id": 1, "sections": ["skills"], "levels": [10]}]}
    cache = tmp_path / "cache"
    calls, original = [], query_cache.query_context

    def counted(*args):
        calls.append(args[1])
        return original(*args)

    monkeypatch.setattr(query_cache, "query_context", counted)
    first = query_cache.cached_context(context_db, request, cache)
    second = query_cache.cached_context(context_db, request, cache)
    assert len(calls) == 1 and not first["cache"]["hit"] and second["cache"]["hit"]
    assert first["items"] == second["items"]
    refreshed = query_cache.cached_context(context_db, request, cache, refresh=True)
    assert len(calls) == 2 and not refreshed["cache"]["hit"]


def test_manual_edit_and_old_annotation_deletion_both_invalidate_cache(context_db, tmp_path):
    request = {"characters": [{"id": 1}]}
    cache = tmp_path / "cache"
    first = query_cache.cached_context(context_db, request, cache)
    with sqlite3.connect(context_db) as conn:
        conn.execute("UPDATE annotation SET content='人工修正正文' WHERE id=1")
    changed = query_cache.cached_context(context_db, request, cache)
    assert not changed["cache"]["hit"] and changed["cache"]["key"] != first["cache"]["key"]
    assert any(a["content"] == "人工修正正文" for a in changed["items"][0]["data"]["annotations"])
    with sqlite3.connect(context_db) as conn:
        conn.execute("DELETE FROM annotation WHERE id=1")
    removed = query_cache.cached_context(context_db, request, cache)
    assert not removed["cache"]["hit"] and removed["cache"]["key"] != changed["cache"]["key"]
    assert all(a["id"] != 1 for a in removed["items"][0]["data"]["annotations"])
    assert first["snapshot"]["upstream_commit"] == removed["snapshot"]["upstream_commit"]


def test_same_entity_different_selection_has_distinct_files_and_repairs_tampered_projections(context_db, tmp_path):
    request = {"characters": [{"id": 1, "sections": ["skills"], "levels": [1]},
                               {"id": 1, "sections": ["talents"], "levels": [10]}]}
    result = query_cache.cached_context(context_db, request, tmp_path / "cache")
    files = [Path(item["path"]) for item in result["cache"]["entity_files"]]
    assert len(set(files)) == 2
    expected = [p.read_bytes() for p in files]
    assert "skills" in json.loads(expected[0])["data"]
    assert "skills" not in json.loads(expected[1])["data"]
    files[0].write_bytes(b"tampered")
    files[1].unlink()
    repaired = query_cache.cached_context(context_db, request, tmp_path / "cache")
    assert repaired["cache"]["hit"]
    assert [p.read_bytes() for p in files] == expected


def test_corrupted_primary_cache_requeries_and_partial_entity_error_remains(context_db, tmp_path, monkeypatch):
    request = {"characters": [{"id": 1}, {"id": 999}]}
    result = query_cache.cached_context(context_db, request, tmp_path / "cache")
    assert result["items"][1]["status"] == "error"
    Path(result["cache"]["path"]).write_text("{broken", encoding="utf-8")
    repaired = query_cache.cached_context(context_db, request, tmp_path / "cache")
    assert not repaired["cache"]["hit"]
    assert repaired["items"] == result["items"]


def test_cache_cannot_repair_over_protected_request_file(context_db, tmp_path):
    request = {"characters": [{"id": 1}]}
    cache = tmp_path / "cache"
    result = query_cache.cached_context(context_db, request, cache)
    protected = Path(result["cache"]["entity_files"][0]["path"])
    _write(protected, request)
    original = protected.read_bytes()
    with pytest.raises(PlanError, match="不能覆盖"):
        query_cache.cached_context(context_db, request, cache, protected_paths=[protected])
    assert protected.read_bytes() == original


def test_workflow_views_keep_source_and_input_binding_then_mark_stale(workflow_plan):
    plan, _ = workflow_plan
    audit = workflow.new_audit(plan)
    audit["records"] = [_lookup(), {"id": "issue.1", "type": "issue", "description": "具体动作占用尚未确认。"}]
    audit["scope"] = {"included": ["单角色技能"], "excluded": ["有理由排除的额外事件"], "pending": ["精确帧数"]}
    views = workflow.audit_views(audit, plan)
    assert views["issues.json"]["review_status"] == "declared"
    assert views["web_findings.json"]["items"][0] == _lookup()
    assert views["lookups.jsonl"][0]["adopted_refs"] == ["mechanics_model.rules[0]"]
    assert "精确帧数" in views["rotation.md"]
    changed = copy.deepcopy(plan)
    changed["manual_template"]["characters"][0]["stats"]["atk"] = 101
    stale = workflow.audit_views(audit, changed)
    assert stale["issues.json"]["review_status"] == "stale"
    assert stale["issues.json"]["items"][-1]["id"] == "system.audit_stale"
    assert stale["lookups.jsonl"][0]["review_status"] == "stale"
    assert audit["records"][0] == _lookup() and len(audit["records"]) == 2
    changed["plan_id"] = "another-plan"
    with pytest.raises(PlanError) as caught:
        workflow.audit_views(audit, changed)
    assert caught.value.reason == "audit_plan_mismatch"


@pytest.mark.parametrize("mutation", [
    {"review_status": "declared"}, {"input_fingerprint": "forged"}, {"revision": 0},
    {"not_adopted_reason": ["not a string"]}, {"id": "system.audit_stale"},
    {"local_checked": "not a list"}, {"source_refs": []},
    {"adopted_refs": [], "not_adopted_reason": ""},
])
def test_audit_rejects_invalid_records_or_system_header_override(workflow_plan, mutation):
    plan, _ = workflow_plan
    audit = workflow.new_audit(plan)
    audit["records"] = [{**_lookup(), **mutation}]
    with pytest.raises(PlanError):
        workflow.validate_audit(audit)


def test_audit_schema_boolean_and_duplicate_records_are_rejected(workflow_plan):
    plan, _ = workflow_plan
    audit = workflow.new_audit(plan)
    audit["schema_version"] = True
    with pytest.raises(PlanError):
        workflow.validate_audit(audit)
    audit["schema_version"] = 1
    audit["records"] = [_lookup(), _lookup()]
    with pytest.raises(PlanError):
        workflow.validate_audit(audit)


def test_cli_context_output_cache_and_direct_input_protection(context_db, tmp_path, capsys):
    path = tmp_path / "request.json"
    _write(path, {"characters": [{"id": 1}]})
    cache, output = tmp_path / "cache", tmp_path / "result.json"
    args = ["--data-dir", str(context_db.parent), "db", "context", "--input", str(path), "--cache-dir", str(cache)]
    code, body = _call([*args, "--output", str(output)], capsys)
    assert code == 0 and Path(body["data"]["published_paths"][0]) == output
    assert json.loads(output.read_text("utf-8"))["items"][0]["id"] == 1
    assert _call(args, capsys)[1]["data"]["cache"]["hit"]
    request_before, database_before = path.read_bytes(), context_db.read_bytes()
    for target in (path, context_db):
        code, error = _call([*args, "--output", str(target), "--overwrite"], capsys)
        assert code == 1 and error["reason"] == "file_conflict"
    assert path.read_bytes() == request_before and context_db.read_bytes() == database_before


def test_cli_context_backup_cannot_overwrite_request(context_db, tmp_path, capsys):
    output = tmp_path / "output.json"
    path = tmp_path / "output.json.previous"
    _write(path, {"characters": [{"id": 1}]})
    output.write_bytes(b"old output")
    before = path.read_bytes()
    code, error = _call(["--data-dir", str(context_db.parent), "db", "context", "--input", str(path),
                         "--output", str(output), "--overwrite"], capsys)
    assert code == 1 and error["reason"] == "file_conflict"
    assert path.read_bytes() == before and output.read_bytes() == b"old output"


def test_cli_workflow_init_export_stale_then_explicit_bind(workflow_plan, tmp_path, capsys):
    plan, path = workflow_plan
    audit_path, output = tmp_path / "audit.json", tmp_path / "delivery"
    args = ["--plan", str(path), "--audit", str(audit_path)]
    assert _call(["workflow", "init", *args], capsys)[0] == 0
    assert _call(["workflow", "init", *args], capsys)[0] == 1
    audit = json.loads(audit_path.read_text("utf-8"))
    audit["records"] = [_lookup()]
    _write(audit_path, audit)
    code, body = _call(["workflow", "export", *args, "--output-dir", str(output)], capsys)
    assert code == 0 and body["data"]["review_status"] == "declared"
    assert {p.name for p in output.iterdir() if not p.name.startswith(".")} == {
        "issues.json", "web_findings.json", "lookups.jsonl", "rotation.md", "task-evidence.json"}
    plan["manual_template"]["characters"][0]["stats"]["atk"] += 1
    _write(path, plan)
    code, body = _call(["workflow", "export", *args, "--output-dir", str(output), "--overwrite"], capsys)
    assert code == 0 and body["data"]["review_status"] == "stale"
    assert json.loads((output / "lookups.jsonl").read_text("utf-8"))["review_status"] == "stale"
    assert _call(["workflow", "bind", *args], capsys)[0] == 0
    rebound = json.loads(audit_path.read_text("utf-8"))
    assert rebound["input_fingerprint"] == fingerprint(input_view(plan))
    assert rebound["records"] == [_lookup()]
    assert _call(["workflow", "export", *args, "--output-dir", str(output), "--overwrite"], capsys)[1]["data"]["review_status"] == "declared"


def test_cli_workflow_bind_detects_concurrent_audit_edit(workflow_plan, tmp_path, capsys, monkeypatch):
    from genshin_app.equipment import files
    plan, path = workflow_plan
    audit_path = tmp_path / "audit.json"
    _write(audit_path, workflow.new_audit(plan))
    original = files.publish_files

    def intervene(outputs, **kwargs):
        audit = json.loads(audit_path.read_text("utf-8"))
        audit["scope"]["pending"].append("外部编辑尚未核对")
        _write(audit_path, audit)
        return original(outputs, **kwargs)

    monkeypatch.setattr(files, "publish_files", intervene)
    code, body = _call(["workflow", "bind", "--plan", str(path), "--audit", str(audit_path)], capsys)
    assert code == 1 and body["reason"] == "file_conflict"
    assert json.loads(audit_path.read_text("utf-8"))["scope"]["pending"] == ["外部编辑尚未核对"]


@pytest.mark.parametrize("input_name", ["issues.json", "issues.json.previous", ".issues.json.plan.lock"])
def test_cli_workflow_export_protects_input_and_publication_sidecars(workflow_plan, tmp_path, capsys, input_name):
    plan, path = workflow_plan
    output = tmp_path / "delivery"
    output.mkdir()
    audit_path = output / input_name
    _write(audit_path, workflow.new_audit(plan))
    before = audit_path.read_bytes()
    if input_name != "issues.json":
        (output / "issues.json").write_bytes(b"old issues")
    code, _ = _call(["workflow", "export", "--plan", str(path), "--audit", str(audit_path),
                     "--output-dir", str(output), "--overwrite"], capsys)
    assert code == 1 and audit_path.read_bytes() == before
    assert not (output / "web_findings.json").exists()
