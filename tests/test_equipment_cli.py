"""Observable CLI/bridge parity, stale inputs and atomic output publication."""
import csv
import io
import json
import zipfile
from pathlib import Path

import pytest

from genshin_app.cli import main
from genshin_app.equipment import new_plan, validate_plan
from genshin_app.equipment.files import publish_files
from genshin_app.equipment.schema import PlanError
from genshin_app.gui.api import GuiApi


@pytest.fixture(autouse=True)
def _runtime_database_already_admitted(monkeypatch):
    """Keep this unit suite focused on equipment transport semantics.

    The real CLI startup gate has its own integration coverage.  These tests
    intentionally use an empty temporary data directory, so admitting a test
    profile here prevents the new current-only gate from masking the file and
    calculation behavior under test.
    """

    monkeypatch.setattr(
        "genshin_app.cli._require_runtime_database",
        lambda _data_dir: {"can_start": True, "state": "test_ready"},
    )


@pytest.fixture
def ready_plan(tmp_path):
    plan = new_plan()
    plan["manual_template"].update(
        characters=[{"id": "c", "level": 90, "stats": {"atk": 100}}],
        hits=[{"id": "h", "damage_type_ids": ["damage.other"], "t": 1, "character": "c",
               "skill": '技能,"引号"\n第二行', "damage_system": "classic", "element": "physical", "multiplier": 1}])
    validate_plan(plan)
    path = tmp_path / "plan.json"
    path.write_text(json.dumps(plan, ensure_ascii=False), encoding="utf-8")
    return path, plan


def call(args, capsys):
    code = main(args)
    captured = capsys.readouterr()
    return code, json.loads(captured.err if code else captured.out)


def test_standalone_capabilities_example_is_complete(capsys):
    code, payload = call(["capabilities"], capsys)
    assert code == 0
    contract = payload["data"]["plan_contract"]
    assert validate_plan(contract["examples"]["pending_plan"])["valid"]
    assert "schema" in contract and len(contract["engine_v2"]["benefit_types"]) > 20


def test_cli_gui_same_input_output_fingerprints(ready_plan, capsys, tmp_path):
    path, plan = ready_plan
    code, payload = call(["--data-dir", str(tmp_path), "equipment", "calc", "--plan", str(path)], capsys)
    assert code == 0
    api = GuiApi(tmp_path)
    bridge = api.run_plan_calc(plan)
    assert bridge["code"] == 0, bridge
    assert payload["data"]["result"] == bridge["data"]["result"]
    for key in ("input_fingerprint", "output_fingerprint", "source_fingerprint", "result_fingerprint"):
        assert payload["data"]["result_receipt"][key] == bridge["data"]["result_receipt"][key]


def test_compose_native_output_reopens_and_refuses_unapproved_overwrite(ready_plan, capsys, tmp_path):
    path, _ = ready_plan
    output = tmp_path / "composed.json"
    args = ["--data-dir", str(tmp_path), "equipment", "compose", "--plan", str(path), "--output", str(output)]
    assert call(args, capsys)[0] == 0
    assert json.loads(output.read_text("utf-8"))["kind"] == "genshin-plan"
    original = output.read_bytes()
    code, payload = call(args, capsys)
    assert code == 1 and payload["reason"] == "file_conflict"
    assert output.read_bytes() == original
    assert call([*args, "--overwrite"], capsys)[0] == 0


def test_pending_resolved_request_writes_neither_target(ready_plan, capsys, tmp_path):
    path, plan = ready_plan
    plan["manual_template"]["hits"][0]["damage_type_ids"] = None
    path.write_text(json.dumps(plan), encoding="utf-8")
    output, resolved = tmp_path / "pending.json", tmp_path / "resolved.json"
    args = ["--data-dir", str(tmp_path), "equipment", "compose", "--plan", str(path),
            "--output", str(output), "--export-resolved", str(resolved)]
    code, payload = call(args, capsys)
    assert code == 1 and payload["reason"] == "missing_input"
    assert not output.exists() and not resolved.exists()
    code, payload = call(args[:-2], capsys)
    assert code == 0 and payload["data"]["state"] == "pending"
    assert output.exists()


@pytest.mark.parametrize("fmt", ["json", "csv", "xlsx"])
def test_export_embeds_source_and_match_receipt(ready_plan, capsys, tmp_path, fmt):
    path, _ = ready_plan
    output = tmp_path / f"result.{fmt}"
    code, payload = call(["--data-dir", str(tmp_path), "equipment", "export", "--plan", str(path),
                          "--format", fmt, "--output", str(output)], capsys)
    assert code == 0, payload
    if fmt == "json":
        data = json.loads(output.read_text("utf-8"))
        assert set(data) == {"result", "result_receipt", "equipment_summary"}
    elif fmt == "csv":
        rows = list(csv.DictReader(io.StringIO(output.read_text("utf-8-sig"), newline="")))
        assert len(rows) == 1
        assert rows[0]["plan_id"] and rows[0]["matched_effect_refs_json"] == "[]"
        assert rows[0]["技能"] == '技能,"引号"\n第二行'
    else:
        with zipfile.ZipFile(output) as archive:
            assert "来源与假设" in archive.read("xl/workbook.xml").decode("utf-8")
            assert "matched_effect_refs_json" in archive.read("xl/worksheets/sheet2.xml").decode("utf-8")


def test_output_path_clash_and_input_replacement_rejected(ready_plan, capsys, tmp_path):
    path, _ = ready_plan
    old = path.read_bytes()
    args = ["--data-dir", str(tmp_path), "equipment", "calc", "--plan", str(path), "--output", str(path), "--overwrite"]
    code, payload = call(args, capsys)
    assert code == 1 and payload["reason"] == "file_conflict" and path.read_bytes() == old
    with pytest.raises(PlanError):
        publish_files([(tmp_path / "a.json", b"a"), (tmp_path / "." / "a.json", b"b")])


def test_partial_publish_reports_actual_paths(tmp_path, monkeypatch):
    from genshin_app.equipment import files
    original_replace = files.os.replace
    first, second = tmp_path / "first", tmp_path / "second"

    def fail_second(source, target):
        if Path(target) == second:
            raise OSError("simulated device failure")
        return original_replace(source, target)

    monkeypatch.setattr(files.os, "replace", fail_second)
    with pytest.raises(PlanError) as caught:
        publish_files([(first, b"first"), (second, b"second")])
    assert caught.value.details["published_paths"] == [str(first)]
    assert first.read_bytes() == b"first" and not second.exists()
