"""Independent EC15 checks: strict fresh envelope and native input identity.

Expected arithmetic is a level-90 1000 ATK physical hit against level 90,
10% resistance: 1000 * 1 * .5 * .9 = 450, with no crit bonus.
No formal database or historical calculated fixture is used.
"""
from __future__ import annotations

import copy
import io
import json
import sqlite3
import xml.etree.ElementTree as ET
import zipfile
from contextlib import closing
from pathlib import Path

import pytest

from genshin_app.equipment import delivery, service
from genshin_app.equipment.files import fingerprint, input_view, json_bytes, read_plan
from genshin_app.equipment.schema import PlanError, new_plan, validate_plan
from tests.test_action_resources_app import confirm
from tests.test_automatic_energy_app import automatic_plan
from tests.test_runtime_action_resource import (  # noqa: F401
    fake_repo,
    resource_db,
    stub_fetcher,
)

# ruff: noqa: F811


@pytest.fixture
def review_calculation(tmp_path):
    db = tmp_path / "isolated.db"
    with closing(sqlite3.connect(db)) as conn, conn:
        conn.executescript("""
            CREATE TABLE character(id INTEGER,version_line TEXT,name TEXT);
            INSERT INTO character VALUES(10000002,'release','捕获时中文角色');
        """)
    plan = new_plan()
    plan["manual_template"].update(
        characters=[{"id": "10000002", "level": 90, "stats": {"atk": 1000}}],
        hits=[{"id": "review.hit", "damage_type_ids": ["damage.other"], "t": 1,
               "character": "10000002", "skill": "独立工程伤害", "damage_system": "classic",
               "element": "physical", "multiplier": 1}],
    )
    calculated = service.run_plan_calc(plan, db, capture_display=True)
    assert calculated["result"]["summary"]["total_damage"] == pytest.approx(450)
    return calculated, plan, db


def test_review_strict_validation_is_offline_and_preserves_all_input(review_calculation, monkeypatch):
    calculated, _, _ = review_calculation
    before = copy.deepcopy(calculated)

    def forbidden(*args, **kwargs):
        pytest.fail("Delivery validation attempted late database or numerical execution")

    monkeypatch.setattr(sqlite3, "connect", forbidden)
    monkeypatch.setattr(service, "compose_equipment_plan", forbidden)
    monkeypatch.setattr(service, "calculate", forbidden)
    delivery.validate_delivery_calculated(calculated)
    assert calculated == before


@pytest.mark.parametrize("key", ["plan", "result", "report", "result_receipt",
                                "equipment_summary", "presentation_issues", "display_context"])
def test_review_complete_calculated_requires_each_layer(review_calculation, key):
    calculated = copy.deepcopy(review_calculation[0])
    del calculated[key]
    with pytest.raises((PlanError, ValueError)):
        delivery.validate_delivery_calculated(calculated)


@pytest.mark.parametrize("key", ["input_fingerprint", "output_fingerprint", "source_fingerprint",
                                "result_fingerprint", "plan_id", "revision", "calculated_at"])
def test_review_plain_manual_path_cannot_omit_proof_header(review_calculation, key):
    calculated = copy.deepcopy(review_calculation[0])
    del calculated["result_receipt"][key]
    with pytest.raises((PlanError, ValueError)):
        delivery.validate_delivery_calculated(calculated)


@pytest.mark.parametrize("mutation", ["detached_report_plan", "result", "resolved_template",
                                     "source_identity", "catalog", "summary", "presentation"])
def test_review_single_layer_change_cannot_be_accepted_by_strict_builder(review_calculation, mutation):
    calculated = copy.deepcopy(review_calculation[0])
    if mutation == "detached_report_plan":
        calculated["report"]["plan"] = copy.deepcopy(calculated["plan"])
        calculated["report"]["plan"]["revision"] += 1
    elif mutation == "result":
        calculated["result"]["hits"][0]["expected"] += 1
    elif mutation == "resolved_template":
        calculated["plan"]["resolved_template"]["hits"][0]["multiplier"] = 2
    elif mutation == "source_identity":
        calculated["result_receipt"]["source_fingerprint"] = "0" * 64
    elif mutation == "catalog":
        calculated["display_context"]["catalog"]["review_unverified_extra"] = "别的目录"
    elif mutation == "summary":
        calculated["equipment_summary"] = copy.deepcopy(calculated["equipment_summary"])
        calculated["equipment_summary"]["review_unverified_extra"] = 1
    else:
        calculated["presentation_issues"] = [{"code": "not_from_this_report"}]
    with pytest.raises((PlanError, ValueError)):
        delivery.validate_delivery_calculated(calculated)


def test_review_legacy_result_output_is_not_complete_calculated(review_calculation):
    calculated = review_calculation[0]
    old_output = json.loads(service.result_bytes(
        calculated["result"], calculated["result_receipt"], "json",
        equipment_summary=calculated["equipment_summary"],
    ))
    with pytest.raises((PlanError, ValueError)):
        delivery.validate_delivery_calculated(old_output)


def test_review_valid_input_view_needs_explicit_pending_cache_slots(review_calculation):
    calculated = review_calculation[0]
    native = copy.deepcopy(input_view(calculated["plan"]))
    with pytest.raises(PlanError) as caught:
        validate_plan(native)
    assert {row["path"] for row in caught.value.details} >= {
        "plan.resolution", "plan.resolved_template",
    }
    native.update(resolution={"state": "pending"}, resolved_template=None)
    validate_plan(native)
    assert fingerprint(input_view(native)) == calculated["result_receipt"]["input_fingerprint"]
    assert native["manual_template"]["hits"][0]["multiplier"] == 1


def test_review_cached_source_size_does_not_reapply_native_input_limit(review_calculation):
    calculated = copy.deepcopy(review_calculation[0])
    padding = "x" * (8 * 1024 * 1024 + 1)
    calculated["plan"]["resolution"]["review_cache_payload"] = padding
    calculated["report"]["review_cache_payload"] = padding
    assert len(json_bytes(calculated["plan"])) > 8 * 1024 * 1024
    checked = delivery.validate_delivery_calculated(calculated)
    assert len(json_bytes(checked["native"])) < 8 * 1024 * 1024
    assert "review_cache_payload" not in checked["native"]["resolution"]


def test_review_one_fresh_calculation_uses_captured_names_after_database_changes(review_calculation, tmp_path, monkeypatch):
    _, plan, db = review_calculation
    source = tmp_path / "requested.plan.json"
    source.write_bytes(json_bytes(plan))
    output = tmp_path / "one-calculation"
    counts = {"fresh": 0, "compose": 0, "calculate": 0}
    originals = {"fresh": service.run_plan_calc, "compose": service.compose_equipment_plan,
                 "calculate": service.calculate}
    original_connect = sqlite3.connect

    def forbidden(*args, **kwargs):
        pytest.fail("A detached export re-opened SQLite after fresh calculation")

    def counted_compose(*args, **kwargs):
        counts["compose"] += 1
        return originals["compose"](*args, **kwargs)

    def counted_calculate(*args, **kwargs):
        counts["calculate"] += 1
        return originals["calculate"](*args, **kwargs)

    def fresh_then_replace_names(*args, **kwargs):
        counts["fresh"] += 1
        assert kwargs["capture_display"] is True
        result = originals["fresh"](*args, **kwargs)
        with closing(original_connect(db)) as conn, conn:
            conn.execute("UPDATE character SET name='此后数据库新名称'")
        monkeypatch.setattr(sqlite3, "connect", forbidden)
        return result

    monkeypatch.setattr(service, "run_plan_calc", fresh_then_replace_names)
    monkeypatch.setattr(service, "compose_equipment_plan", counted_compose)
    monkeypatch.setattr(service, "calculate", counted_calculate)
    emitted = delivery.deliver_plan_file(source, db, output)
    assert counts == {"fresh": 1, "compose": 1, "calculate": 1}
    assert emitted["diagnostic"] is False
    artifacts = {row["role"]: Path(row["path"]) for row in emitted["data"]["artifacts"]}
    package = json.loads(artifacts["calculated"].read_bytes())
    assert package["package_schema"] == "genshin.calculated-package.v1"
    assert package["calculated"]["result"]["summary"]["total_damage"] == pytest.approx(450)
    assert package["integrity"]["calculated_fingerprint"] == fingerprint(package["calculated"])
    native = read_plan(artifacts["native_plan"])["plan"]
    assert native["resolution"] == {"state": "pending"} and native["resolved_template"] is None
    assert fingerprint(input_view(native)) == package["calculated"]["result_receipt"]["input_fingerprint"]
    for role in ("result_workbook", "formula_workbook"):
        with zipfile.ZipFile(io.BytesIO(artifacts[role].read_bytes())) as archive:
            all_xml = "\n".join(archive.read(name).decode("utf8") for name in archive.namelist() if name.endswith(".xml"))
        assert "捕获时中文角色" in all_xml
        assert "此后数据库新名称" not in all_xml
    with zipfile.ZipFile(artifacts["formula_workbook"]) as archive:
        events = ET.fromstring(archive.read("xl/worksheets/sheet3.xml"))
    ns = {"s": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
    cell = events.find(".//s:c[@r='V5']", ns)
    assert float(cell.find("s:v", ns).text) == pytest.approx(450)
    assert cell.find("s:f", ns) is not None
    monkeypatch.setattr(sqlite3, "connect", original_connect)
    reloaded = originals["fresh"](native, db, capture_display=True)
    assert reloaded["result"]["summary"]["total_damage"] == pytest.approx(450)
    assert "此后数据库新名称" in json.dumps(reloaded["display_context"], ensure_ascii=False)


def test_review_failed_second_exporter_does_not_publish_or_echo_sources(review_calculation, tmp_path, monkeypatch):
    from genshin_app import formula_workbook

    _, plan, db = review_calculation
    source = tmp_path / "requested.plan.json"
    source.write_bytes(json_bytes(plan))
    output = tmp_path / "should-not-exist"

    def fail_with_large_source(*args, **kwargs):
        raise PlanError("Exporter failed", "review_export_failure", {"source": "large raw source" * 200000})

    monkeypatch.setattr(formula_workbook, "build_formula_workbook", fail_with_large_source)
    with pytest.raises(PlanError) as caught:
        delivery.deliver_plan_file(source, db, output)
    assert caught.value.details["phase"] == "build"
    assert caught.value.details["index_committed"] is False
    assert len(json_bytes(caught.value.details)) < 2048
    assert not output.exists()


@pytest.mark.parametrize(("field", "value"), [("revision", False), ("template_version", 2.0)])
def test_review_header_integer_identity_is_not_python_numeric_coercion(review_calculation, field, value):
    calculated = copy.deepcopy(review_calculation[0])
    calculated["result_receipt"][field] = value
    with pytest.raises((PlanError, ValueError)):
        delivery.validate_delivery_calculated(calculated)


def test_review_automatic_source_is_native_placeholder_but_full_offline_audit(resource_db, monkeypatch):
    plan = confirm(automatic_plan(), resource_db)
    calculated = service.run_plan_calc(plan, resource_db, capture_display=True)

    def forbidden(*args, **kwargs):
        pytest.fail("Offline builder re-queried sources")

    monkeypatch.setattr(sqlite3, "connect", forbidden)
    built = delivery.build_calculation_delivery(calculated, input_file_sha="a" * 64)
    native = json.loads(built["artifacts"]["native_plan"])
    assert native["rotation_model"]["abilities"][0]["energy"] == {}
    assert native["rotation_model"]["events"] == []
    assert native["action_resource_sources"][0]["energy_delivery"] == "action_start"
    package = json.loads(built["artifacts"]["calculated"])
    check = package["calculated"]["result"]["rotation_check"]
    audits = [event for event in check["event_log"] if event.get("delivery") == "automatic_action_start"]
    assert len(audits) == 1
    assert audits[0]["amount"] == 60
    assert check["characters"][0]["final_energy"] == 0
    assert package["calculated"]["report"]["compiled_rotation_model"]["abilities"][0]["energy"]["delivery"] == "action_start"
    forged = copy.deepcopy(calculated)
    forged["result_receipt"]["action_resources"].pop("automatic_energy_delivery")
    with pytest.raises((PlanError, ValueError)):
        delivery.validate_delivery_calculated(forged)


def test_review_actual_failed_prefix_publishes_complete_diagnostic_but_cli_fails(
    resource_db, tmp_path, capsys, monkeypatch
):
    from genshin_app.cli import main

    monkeypatch.setattr("genshin_app.cli._require_runtime_database", lambda _data_dir: {})
    plan = automatic_plan()
    second = copy.deepcopy(plan["rotation_model"]["actions"][0])
    second.update(id="second.q", start=.25, occupation_end=.26, order=2)
    plan["rotation_model"]["actions"].append(second)
    confirm(plan, resource_db)
    source = tmp_path / "diagnostic.plan.json"
    source.write_bytes(json_bytes(plan))
    output = tmp_path / "diagnostic-delivery"
    code = main(["--data-dir", str(resource_db.parent), "equipment", "deliver",
                 "--plan", str(source), "--output-dir", str(output)])
    captured = capsys.readouterr()
    assert code == 1 and not captured.out
    envelope = json.loads(captured.err)
    assert len(captured.err.encode("utf8")) <= 64 * 1024
    assert envelope["reason"] == "calculation_diagnostic"
    assert envelope["details"]["index_committed"] is True
    assert envelope["details"]["metadata"]["calculation_status"] == "diagnostic"
    index = json.loads((output / "index.json").read_bytes())
    assert index["calculation_status"] == "diagnostic"
    package_path = output / next(row["path"] for row in index["artifacts"] if row["role"] == "calculated")
    package = json.loads(package_path.read_bytes())
    result = package["calculated"]["result"]
    assert result["summary"]["total_damage"] == pytest.approx(45)
    assert result["summary"]["dps"] is None
    assert result["rotation_check"]["damage_interpretation"]["kind"] == "executed_prefix"
    assert len(result["hits"]) == 1
    assert len(index["artifacts"]) == 4
