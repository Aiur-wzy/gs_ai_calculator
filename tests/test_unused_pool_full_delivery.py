"""A real unused bound pool stays explicit through the complete App delivery.

The database fixture is synthetic.  This test does not open the production or
candidate gameplay database and does not treat engineering cooldown inputs as
game facts.
"""
# ruff: noqa: F811 - pytest fixture injection uses imported names.
from __future__ import annotations

import copy
import hashlib
import io
import json
import os
import re
import sqlite3
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path

import pytest
from test_capacity_inputs import input_plan
from test_capacity_model import (  # noqa: F401
    action_resource_synthetic_db_only,
    fake_repo,
    ready,
    source_db,
)

from genshin_app.equipment import service
from genshin_app.equipment.delivery import (
    build_calculation_delivery,
    validate_delivery_calculated,
)
from genshin_app.equipment.files import read_plan, save_plan

pytestmark = pytest.mark.usefixtures("action_resource_synthetic_db_only")


def _confirmed_unused_plan(db):
    """Keep the reviewed skill in the model, but execute only an unrelated action."""
    from genshin_app.equipment.composer import compose_equipment_plan
    from genshin_app.equipment.data_version import equipment_data_version
    from genshin_db.queries import get_meta

    plan = input_plan()
    actor = plan["manual_template"]["characters"][0]["id"]
    plan["rotation_model"]["max_cycles"] = 3
    bound = plan["rotation_model"]["abilities"][0]
    assert bound["id"] == "e" and plan["action_capacity_sources"][0]["ability_ids"] == ["e"]

    # A normal attack makes the calculation and workbook path non-empty without
    # consuming or impersonating the reviewed elemental-skill capacity pool.
    plan["rotation_model"]["abilities"].append({
        "id": "normal", "character": actor,
        "action_type_id": "action.normal_attack", "cooldown": None, "energy": None,
        "status": "assumed", "source": "零消费完整交付工程动作",
    })
    action = plan["rotation_model"]["actions"][0]
    action.update(ability_id="normal", repeat={"mode": "once"})
    for hit in plan["manual_template"]["hits"]:
        hit.update(skill="零消费验收普通攻击", damage_type_ids=["damage.normal_attack"])
    plan["equipment_inputs"][actor]["data_version"] = equipment_data_version(
        get_meta(db), "release"
    )

    first = compose_equipment_plan(plan, db)
    records = first.get("action_capacity_compilations") or []
    assert len(records) == 1 and records[0]["required_confirmation"]
    plan["action_capacity_sources"][0].update(records[0]["required_confirmation"])
    confirmed = compose_equipment_plan(plan, db)
    assert confirmed["state"] == "ready", confirmed["missing_inputs"]
    return confirmed["plan"], records[0]["required_confirmation"]


def _sheet_rows(book_bytes, sheet_name):
    namespace = {"s": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
    relationship_id = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id"
    with zipfile.ZipFile(io.BytesIO(book_bytes)) as archive:
        assert archive.testzip() is None
        relationships = {
            node.attrib["Id"]: node.attrib["Target"]
            for node in ET.fromstring(archive.read("xl/_rels/workbook.xml.rels"))
        }
        sheets = {
            node.attrib["name"]: relationships[node.attrib[relationship_id]]
            for node in ET.fromstring(archive.read("xl/workbook.xml")).findall(
                "s:sheets/s:sheet", namespace
            )
        }
        assert {"技能次数来源", "技能次数消费"} <= set(sheets)
        target = sheets[sheet_name].lstrip("/")
        if not target.startswith("xl/"):
            target = "xl/" + target
        document = ET.fromstring(archive.read(target))
        rows = []
        for row in document.findall(".//s:row", namespace):
            values = {}
            for cell in row.findall("s:c", namespace):
                column = re.match(r"[A-Z]+", cell.attrib["r"]).group()
                values[column] = "".join(cell.itertext())
            rows.append(values)
        return list(sheets), rows


def _sha(content):
    return hashlib.sha256(content).hexdigest()


def _write_evidence(output, *, calculated, reopened, saved_path, built, ledger,
                    confirmation, workbook_evidence):
    if not output:
        return
    target = Path(output)
    target.mkdir(parents=True, exist_ok=False)
    saved_copy = target / "saved-native.plan.json"
    saved_copy.write_bytes(Path(saved_path).read_bytes())
    encoded_calculated = json.dumps(calculated, ensure_ascii=False, indent=2, allow_nan=False).encode("utf-8")
    encoded_reopened = json.dumps(reopened, ensure_ascii=False, indent=2, allow_nan=False).encode("utf-8")
    files = {
        "initial-calculated.json": encoded_calculated,
        "reopened-calculated.json": encoded_reopened,
        "delivery-native.plan.json": built["artifacts"]["native_plan"],
        "calculated-package.json": built["artifacts"]["calculated"],
        "result.xlsx": built["artifacts"]["result_workbook"],
        "formula.xlsx": built["artifacts"]["formula_workbook"],
    }
    for name, content in files.items():
        (target / name).write_bytes(content)
    manifest = {
        "contract": "unused-action-capacity-full-delivery-evidence-v1",
        "database_scope": "engineering_synthetic_sqlite_only",
        "production_database_accessed": False,
        "confirmation": confirmation,
        "ledger": copy.deepcopy(ledger),
        "save_reopen": {
            "result_equal": calculated["result"] == reopened["result"],
            "source_fingerprint_equal": calculated["report"]["source_fingerprint"]
            == reopened["report"]["source_fingerprint"],
        },
        "offline_delivery": {
            "roles": sorted(built["artifacts"]),
            "diagnostic": built["diagnostic"],
            "metadata": built["metadata"],
        },
        "workbooks": workbook_evidence,
        "artifacts": {
            "saved-native.plan.json": {
                "bytes": saved_copy.stat().st_size,
                "sha256": _sha(saved_copy.read_bytes()),
            },
            **{name: {"bytes": len(content), "sha256": _sha(content)} for name, content in files.items()},
        },
        "limitations": [
            "合成受审数据库仅证明 App 合同，不是角色或游戏事实。",
            "未调用原生 Excel 重算或真实 GUI；工作簿证据为离线 OOXML 结构与值。",
        ],
    }
    (target / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8"
    )


def test_unused_bound_pool_full_delivery(ready, tmp_path, monkeypatch):
    db = ready[0]
    plan, confirmation = _confirmed_unused_plan(db)
    calculated = service.run_plan_calc(plan, db, capture_display=True)
    compilation = calculated["result"]["action_capacity_compilations"]
    assert len(compilation) == 1 and compilation[0]["status"] == "ready"
    resolved_ability = next(
        ability for ability in calculated["result"]["rotation_check"]["resolved_rotation_model"]["abilities"]
        if ability["id"] == "e"
    )
    assert resolved_ability["cooldown"]["capacity_source"]["contract"] == "action-capacity-source-v1"

    ledger = calculated["result"]["rotation_check"]["action_capacity_consumption"]
    zero_fields = (
        "max_cycles_planned_actions", "planned_actions", "unreported_actions",
        "committed_actions", "rejected_actions", "not_checked_actions",
        "not_committed_actions", "not_verified_actions", "receipt_count",
    )
    assert {field: ledger[field] for field in zero_fields} == dict.fromkeys(zero_fields, 0)
    assert ledger["receipts"] == []
    assert not any(
        "capacity_admission" in row
        for row in calculated["result"]["rotation_check"]["event_log"]
    )

    saved_path = tmp_path / "unused-pool.plan.json"
    saved = save_plan(saved_path, calculated["plan"])
    loaded = read_plan(saved_path)
    assert loaded["file_sha"] == saved["file_sha"]
    reopened = service.run_plan_calc(loaded["plan"], db, capture_display=True)
    assert calculated["result"] == reopened["result"]
    assert calculated["report"]["source_fingerprint"] == reopened["report"]["source_fingerprint"]

    # Everything below is an offline verifier/exporter path.  Any database use
    # or hidden fresh calculation is a test failure.
    monkeypatch.setattr(sqlite3, "connect", lambda *a, **k: pytest.fail("offline path opened SQLite"))
    monkeypatch.setattr(service, "run_plan_calc", lambda *a, **k: pytest.fail("offline path recalculated"))
    validate_delivery_calculated(calculated)
    validate_delivery_calculated(reopened)
    built = build_calculation_delivery(calculated, input_file_sha=saved["file_sha"])
    assert set(built["artifacts"]) == {
        "native_plan", "calculated", "result_workbook", "formula_workbook"
    }
    packaged = json.loads(built["artifacts"]["calculated"])
    assert packaged["calculated"] == calculated
    assert json.loads(built["artifacts"]["native_plan"])["resolution"] == {"state": "pending"}

    workbook_evidence = {}
    expected_summary = {"已报告动作": "0", "已消费动作": "0", "被拒动作": "0", "次数审计回执": "0"}
    for role in ("result_workbook", "formula_workbook"):
        names, source_rows = _sheet_rows(built["artifacts"][role], "技能次数来源")
        _, consumption_rows = _sheet_rows(built["artifacts"][role], "技能次数消费")
        assert len(source_rows) == 3
        source = source_rows[2]
        assert source["D"] == "2" and source["I"] == "[]" and source["J"] == "2"
        summaries = {row.get("A"): row.get("B") for row in consumption_rows[2:]}
        assert expected_summary.items() <= summaries.items()
        assert not any((row.get("A") or "").replace(".", "", 1).isdigit() for row in consumption_rows[2:])
        workbook_evidence[role] = {
            "sheets": names,
            "source": {key: source[key] for key in ("D", "I", "J")},
            "summary": {key: summaries[key] for key in expected_summary},
        }

    _write_evidence(
        os.environ.get("GENSHIN_UNUSED_POOL_EVIDENCE_DIR"),
        calculated=calculated, reopened=reopened, saved_path=saved_path,
        built=built, ledger=ledger, confirmation=confirmation,
        workbook_evidence=workbook_evidence,
    )
