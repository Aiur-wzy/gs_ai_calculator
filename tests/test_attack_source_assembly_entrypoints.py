"""Assembly transport: native file publication, input protection and GUI sharing."""
from __future__ import annotations

import copy
import json

import pytest

from genshin_app.cli import main
from genshin_app.equipment.files import fingerprint, input_view
from genshin_app.equipment.schema import new_plan
from genshin_app.gui.api import GuiApi


@pytest.fixture(autouse=True)
def _runtime_database_already_admitted(monkeypatch):
    monkeypatch.setattr(
        "genshin_app.cli._require_runtime_database",
        lambda _data_dir: {"can_start": True, "state": "test_ready"},
    )


@pytest.fixture
def transport(tmp_path, monkeypatch):
    from genshin_app.equipment import attack_source_assembly as module

    plan = new_plan()
    input_path = tmp_path / "input.plan.json"
    request_path = tmp_path / "request.json"
    input_path.write_text(json.dumps(plan), encoding="utf8")
    request_path.write_text('{"schema_version":1}', encoding="utf8")
    calls = []

    def assemble(value, request, db):
        calls.append((copy.deepcopy(value), copy.deepcopy(request), db))
        draft = copy.deepcopy(value)
        draft["revision"] += 1
        return {"plan": draft, "assembly": {"status": "created", "profile_id": "demo"},
                "report": {"state": "pending", "missing_inputs": [
                    {"code": "needs_review", "message": "待核实来源", "path": f"item.{i}"}
                    for i in range(25)], "large_source_evidence": "x" * 100_000}}

    monkeypatch.setattr(module, "assemble_attack_source", assemble)
    argv = ["--data-dir", str(tmp_path), "equipment", "assemble-attack",
            "--plan", str(input_path), "--request", str(request_path)]
    return plan, input_path, request_path, calls, argv


def test_cli_publishes_native_draft_and_bounded_summary(transport, tmp_path, capsys):
    plan, original, _, calls, argv = transport
    output = tmp_path / "装配方案.json"
    before = original.read_bytes()
    assert main([*argv, "--output", str(output)]) == 0
    printed = capsys.readouterr()
    response = json.loads(printed.out)["data"]
    native = json.loads(output.read_text("utf8"))
    assert native["kind"] == "genshin-plan" and native["plan_id"] == plan["plan_id"]
    assert "report" not in native and "large_source_evidence" not in native
    assert response["summary_only"] is True and response["state"] == "pending"
    assert response["missing_input_count"] == 25 and len(response["missing_inputs"]) == 20
    assert response["input_fingerprint"] == fingerprint(input_view(native))
    assert len(printed.out) < 8000
    assert original.read_bytes() == before and len(calls) == 1


def test_cli_without_output_only_previews(transport, tmp_path, capsys):
    _, original, _, calls, argv = transport
    names = sorted(p.name for p in tmp_path.iterdir())
    before = original.read_bytes()
    assert main(argv) == 0
    data = json.loads(capsys.readouterr().out)["data"]
    assert set(data) == {"plan", "assembly", "report"}
    assert "large_source_evidence" in data["report"]
    assert sorted(p.name for p in tmp_path.iterdir()) == names
    assert original.read_bytes() == before and len(calls) == 1


def test_cli_inplace_requires_overwrite_and_keeps_backup(transport, capsys):
    _, original, _, calls, argv = transport
    before = original.read_bytes()
    assert main([*argv, "--output", str(original)]) == 1
    assert "原地" in json.loads(capsys.readouterr().err)["message"]
    assert not calls and original.read_bytes() == before
    assert main([*argv, "--output", str(original), "--overwrite"]) == 0
    capsys.readouterr()
    assert original.with_name(original.name + ".previous").read_bytes() == before


def test_cli_inplace_detects_external_edit(transport, monkeypatch, capsys):
    from genshin_app.equipment import attack_source_assembly as module

    _, original, _, _, argv = transport
    assemble = module.assemble_attack_source

    def racing(*args):
        result = assemble(*args)
        original.write_text('{"external":"edited"}', encoding="utf8")
        return result

    monkeypatch.setattr(module, "assemble_attack_source", racing)
    assert main([*argv, "--output", str(original), "--overwrite"]) == 1
    assert json.loads(capsys.readouterr().err)["reason"] == "file_conflict"
    assert json.loads(original.read_text("utf8")) == {"external": "edited"}
    assert not original.with_name(original.name + ".previous").exists()


@pytest.mark.parametrize("protected", ["request", "database", "request_as_backup", "request_as_lock"])
def test_cli_protects_inputs_before_query(transport, tmp_path, capsys, protected):
    _, original, request, calls, argv = transport
    if protected == "request":
        output = request
    elif protected == "database":
        output = tmp_path / "genshin.db"
        output.write_bytes(b"preserve database bytes")
    else:
        output = tmp_path / "draft.json"
        shifted = output.with_name(output.name + ".previous" if protected == "request_as_backup"
                                   else "." + output.name + ".plan.lock")
        shifted.write_bytes(request.read_bytes())
        argv = [*argv[:-1], str(shifted)]
    before = {p: p.read_bytes() for p in tmp_path.iterdir() if p.is_file()}
    assert main([*argv, "--output", str(output), "--overwrite"]) == 1
    assert json.loads(capsys.readouterr().err)["reason"] == "file_conflict"
    assert not calls and original in before
    assert all(p.read_bytes() == value for p, value in before.items())


@pytest.mark.parametrize("body", [b"x" * (32768 + 1), b"\xff", b"{"],
                         ids=["oversized", "invalid-utf8", "invalid-json"])
def test_cli_bad_or_oversized_request_has_no_output(transport, tmp_path, capsys, body):
    _, _, request, calls, argv = transport
    request.write_bytes(body)
    output = tmp_path / "no-output.json"
    assert main([*argv, "--output", str(output)]) == 1
    assert json.loads(capsys.readouterr().err)["code"] == 1
    assert not output.exists() and not calls


@pytest.mark.parametrize("filename", ["genshin.db.previous", ".genshin.db.write.lock",
    "genshin.db-wal", "genshin.db-shm", "genshin.db-journal", ".genshin.db.plan.lock",
    "request.json.previous", ".request.json.plan.lock", "input.plan.json.previous", ".input.plan.json.plan.lock"])
def test_cli_rejects_reserved_input_side_files(transport, tmp_path, capsys, filename):
    _, _, _, calls, argv = transport
    target = tmp_path / filename
    target.write_bytes(b"preserved side file")
    assert main([*argv, "--output", str(target), "--overwrite"]) == 1
    assert json.loads(capsys.readouterr().err)["reason"] == "file_conflict"
    assert target.read_bytes() == b"preserved side file" and not calls


def test_gui_and_capabilities_use_shared_public_contract(tmp_path, monkeypatch):
    from genshin_app import equipment
    from genshin_app.equipment.attack_source_assembly import (
        attack_source_assembly_capabilities,
    )

    calls = []
    marker = {"plan": {"native": True}, "assembly": {}, "report": {"state": "pending"}}

    def assemble(*args):
        calls.append(args)
        return marker

    monkeypatch.setattr(equipment, "assemble_attack_source", assemble)
    gui = GuiApi(tmp_path)
    assert gui.assemble_attack_source({"plan": "input"}, {"request": "input"}) == {"code": 0, "data": marker}
    assert calls == [({"plan": "input"}, {"request": "input"}, tmp_path / "genshin.db")]
    assert equipment.plan_capabilities()["attack_source_assembly_contract"] == attack_source_assembly_capabilities()


@pytest.mark.parametrize('reason,known', [('attack_source_assembly_conflict', True), ('assembly_conflict', False)])
def test_gui_preserves_structured_assembly_errors(tmp_path, monkeypatch, reason, known):
    from genshin_app import equipment

    def fail(*args):
        raise equipment.PlanError("已有关联不能覆盖", reason, {"ability_id": "e"})

    monkeypatch.setattr(equipment, "assemble_attack_source", fail)
    assert GuiApi(tmp_path).assemble_attack_source({}, {}) == {
        "code": 1, "message": "已有关联不能覆盖", "reason": reason, "details": {"ability_id": "e"},
        "operation": "gui.assemble_attack_source", "category": "request_error", "blocking_scope": "request",
        "retryable": known, "contract_ref": {},
        **({} if known else {"classification_basis": "legacy_fallback", "classification_confidence": "unknown"}),
        "recovery": {"action": "correct_arguments" if known else "inspect_error_details", "retry_same_request": False}}
