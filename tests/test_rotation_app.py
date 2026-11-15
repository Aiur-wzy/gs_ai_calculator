"""Cross-boundary regression: stored action inputs, composed ER and audited exports."""
import copy
import io
import json
import zipfile
from pathlib import Path

import pytest

from genshin_app.cli import main
from genshin_app.equipment import (
    load_plan,
    new_plan,
    run_plan_calc,
    save_plan,
    validate_plan,
)
from genshin_app.equipment.schema import PlanError
from genshin_app.equipment.service import plan_capabilities, result_bytes
from genshin_app.gui.api import GuiApi
from genshin_app.rotation_check import check_document, resolve_rotation_model
from genshin_engine import calculate

EXAMPLE = Path(__file__).resolve().parents[1] / "examples" / "rotation-energy-check.json"


@pytest.fixture(autouse=True)
def _runtime_database_already_admitted(monkeypatch):
    monkeypatch.setattr(
        "genshin_app.cli._require_runtime_database",
        lambda _data_dir: {"can_start": True, "state": "test_ready"},
    )


@pytest.fixture
def document():
    return json.loads(EXAMPLE.read_text(encoding="utf-8"))


def native(document):
    template = copy.deepcopy(document["template"])
    template["characters"][0]["stats"]["energy_recharge"] = 1
    plan = new_plan(template)
    plan.update(plan_version=2, rotation_model=copy.deepcopy(document["rotation_model"]))
    return plan


def test_old_plan_cannot_hide_new_semantics_and_future_version_rejected(document):
    plan = native(document)
    assert validate_plan(plan)["plan_version"] == 2
    plan["plan_version"] = 1
    with pytest.raises(PlanError, match="版本 2"):
        validate_plan(plan)
    plan["plan_version"] = 4
    with pytest.raises(PlanError, match="不支持"):
        validate_plan(plan)
    plan["plan_version"] = 2
    del plan["rotation_model"]
    with pytest.raises(PlanError, match="明确"):
        validate_plan(plan)
    plan["rotation_model"] = None
    assert validate_plan(plan)["valid"]


def test_structurally_valid_pending_model_can_be_saved(document, tmp_path):
    plan = native(document)
    plan["rotation_model"]["characters"][0]["initial_energy"] = None
    path = tmp_path / "pending.plan.json"
    save_plan(path, plan)
    loaded = load_plan(path)["plan"]
    assert loaded["rotation_model"] == plan["rotation_model"]
    assert check_document(loaded, tmp_path / "missing.db")["status"] == "pending"


def test_native_composed_er_is_authoritative_but_initial_energy_not_inferred(document, tmp_path):
    plan = native(document)
    plan["rotation_model"]["characters"][0]["energy_recharge"] = 99
    result = run_plan_calc(plan, tmp_path / "not-needed.db")
    check = result["result"]["rotation_check"]
    assert check["status"] == "passed"
    assert check["panel_bindings"][0]["declared_value"] == 99
    assert check["panel_bindings"][0]["value"] == 1
    assert plan["rotation_model"]["characters"][0]["energy_recharge"] == 99
    plan["manual_template"]["characters"][0]["stats"]["energy_recharge"] = 0.5
    changed = run_plan_calc(plan, tmp_path / "not-needed.db")["result"]
    assert changed["rotation_check"]["status"] == "failed"
    assert changed["rotation_check"]["input_fingerprint"] != check["input_fingerprint"]
    assert changed["summary"] == result["result"]["summary"]  # The planned Q is preserved.
    assert changed["rotation_check"]["damage_interpretation"]["executable_dps"] is None
    plan["rotation_model"]["characters"][0]["initial_energy"] = None
    assert run_plan_calc(plan, tmp_path / "not-needed.db")["result"]["rotation_check"]["status"] == "pending"


def test_missing_panel_er_does_not_default_to_one(document):
    resolved, bindings = resolve_rotation_model(document["template"], document["rotation_model"])
    assert resolved["characters"][0]["energy_recharge"] is None
    assert bindings[0]["value"] is None


def test_native_preflight_does_not_execute_mechanics(document, tmp_path, monkeypatch):
    plan = native(document)
    resolved = {**plan, "resolved_template": document["template"]}
    monkeypatch.setattr("genshin_app.equipment.compose_equipment_plan",
                        lambda *args: {"state": "ready", "plan": resolved})
    monkeypatch.setattr("genshin_app.equipment.service.run_plan_calc",
                        lambda *args: pytest.fail("preflight ran the full engine"))
    report = check_document({**plan, "plan_version": 3}, tmp_path / "test.db", preflight=True)
    assert report["status"] == "unproven"
    assert report["preflight"]["mechanics_executed"] is False
    assert report["preflight"]["closure_verified"] is False


def test_legacy_damage_unchanged_but_checks_explicit(document, tmp_path):
    template = document["template"]
    output = GuiApi(tmp_path).run_calc(template)["data"]["result"]
    assert output.pop("rotation_check")["status"] == "not_checked"
    assert output == calculate(template)


def test_native_reload_gui_cli_and_excel_share_check(document, tmp_path, capsys):
    plan = native(document)
    path = tmp_path / "demo.plan.json"
    save_plan(path, plan)
    reopened = load_plan(path)["plan"]
    gui = GuiApi(tmp_path).check_rotation(reopened)
    assert gui["code"] == 0 and gui["data"]["status"] == "passed"
    assert main(["--data-dir", str(tmp_path), "engine", "check-rotation", str(path)]) == 0
    cli = json.loads(capsys.readouterr().out)["data"]
    assert cli == gui["data"]
    calc = run_plan_calc(reopened, tmp_path / "genshin.db")
    assert calc["result"]["rotation_check"] == cli
    workbook = result_bytes(calc["result"], calc["result_receipt"], "xlsx",
                            equipment_summary=calc["equipment_summary"])
    with zipfile.ZipFile(io.BytesIO(workbook)) as archive:
        names = archive.read("xl/workbook.xml").decode("utf-8")
        for name in ("动作清单", "能量流水", "循环边界", "角色与装备"):
            assert name in names
        texts = b"".join(archive.read(name) for name in archive.namelist()).decode("utf-8")
        assert cli["input_fingerprint"] in texts


def test_unknown_fields_not_silently_ignored(document):
    document["version"] = 2
    with pytest.raises(ValueError, match="不支持"):
        check_document(document)
    document["version"] = 1
    document["aura_model"] = {}
    with pytest.raises(ValueError, match="不支持"):
        check_document(document)


def test_capabilities_publish_native_and_standalone_contract():
    from genshin_engine.rotation import rotation_capabilities

    caps = plan_capabilities()
    assert caps["supported_plan_versions"] == [1, 2, 3]
    assert caps["rotation_contract"]["rotation_schema_version"] == 1
    assert caps["schema"]["properties"]["rotation_model"]["anyOf"][0] == caps["rotation_contract"]["native_authoring"]["schema"]
    assert caps["rotation_contract"]["schema"] == rotation_capabilities()["schema"]
    assert caps["rotation_contract"]["schema_scope"] == "engine_execution_and_independent_rotation_check"


def test_cli_check_refuses_input_overwrite_and_exports_xlsx(document, tmp_path, capsys):
    path = tmp_path / "check.json"
    path.write_text(json.dumps(document), encoding="utf-8")
    before = path.read_bytes()
    assert main(["engine", "check-rotation", str(path), "--output", str(path), "--overwrite"]) == 1
    capsys.readouterr()
    assert path.read_bytes() == before
    output = tmp_path / "check.xlsx"
    assert main(["engine", "check-rotation", str(path), "--format", "xlsx", "--output", str(output)]) == 0
    capsys.readouterr()
    assert zipfile.is_zipfile(output)


def test_cli_standalone_preflight_needs_no_database(document, tmp_path, capsys, monkeypatch):
    def blocked(_path):
        raise AssertionError("standalone preflight must not require an active database")

    monkeypatch.setattr("genshin_app.cli._require_runtime_database", blocked)
    source = tmp_path / "check.json"
    source.write_text(json.dumps(document), encoding="utf-8")
    args = ["--data-dir", str(tmp_path), "engine", "check-rotation", str(source), "--preflight"]
    assert main(args) == 0
    result = json.loads(capsys.readouterr().out)["data"]
    assert result["status"] == "unproven"
    assert result["preflight"]["declared_status"] == "passed"
    assert not result["preflight"]["closure_verified"]
    assert not (tmp_path / "genshin.db").exists()


def test_xlsx_generation_does_not_require_a_temporary_directory(document, monkeypatch):
    import tempfile

    def blocked(*args, **kwargs):
        raise PermissionError("模拟临时目录不可用")

    monkeypatch.setattr(tempfile, "TemporaryDirectory", blocked)
    content = result_bytes(calculate(document["template"]), {}, "xlsx")
    assert zipfile.is_zipfile(io.BytesIO(content))
