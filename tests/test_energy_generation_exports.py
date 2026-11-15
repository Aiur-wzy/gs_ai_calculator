"""New particle sources remain inspectable, immutable and offline in both books."""
from __future__ import annotations

import copy
import json
import sqlite3

import pytest
from test_action_resource_export import NS, sheets
from test_energy_generation_sources import confirm_energy, energy_plan
from test_pipeline import fake_repo  # noqa: F401
from test_runtime_energy_dispatch import trigger_db, trigger_repo  # noqa: F401

from genshin_app.equipment import run_plan_calc
from genshin_app.equipment.files import fingerprint
from genshin_app.equipment.service import result_bytes
from genshin_app.export import energy_generation_rows
from genshin_app.formula_workbook import (
    FormulaWorkbookError,
    build_formula_workbook,
    formula_workbook_support,
)

# ruff: noqa: F811


@pytest.fixture
def calculated(trigger_db):
    return run_plan_calc(confirm_energy(energy_plan(trigger_db), trigger_db), trigger_db)


def test_both_books_keep_source_and_actual_ledger_offline(calculated, monkeypatch):
    before = copy.deepcopy(calculated)
    monkeypatch.setattr(sqlite3, "connect", lambda *a, **kw: pytest.fail("export reopened the database"))
    formula = sheets(build_formula_workbook(calculated))
    snapshot = sheets(result_bytes(calculated["result"], calculated["result_receipt"], "xlsx"))
    for workbook in (formula, snapshot):
        page = workbook["产球来源"]
        assert page.find("s:sheetProtection", NS) is not None
        assert not page.findall(".//s:f", NS)
        text = "".join(page.itertext())
        assert all(part in text for part in ("zero_hitlag_reference", "calibrated_reference", "partial",
                    "spawned_particles", "credited_particles", "9", "cast0", "cast1", "cast2"))
        assert "能量流水" in workbook
        assert not any(cell.get("t") == "e" for sheet in workbook.values() for cell in sheet.findall(".//s:c", NS))
    assert calculated == before


def test_fixed_source_fields_are_visible_and_guarded(calculated):
    support = formula_workbook_support(calculated)
    assert support["editable"]["energy_generation_sources"] == "regenerate_required"
    assert support["editable"]["energy_generation_provenance_and_ledger"] == "readonly_snapshot"
    workbook = sheets(build_formula_workbook(calculated))
    rows = {row.find("s:c/s:is/s:t", NS).text: row for row in workbook["参数"].findall("s:sheetData/s:row", NS)
            if row.find("s:c/s:is/s:t", NS) is not None}
    for path in ("energy_generation_sources[0].scenario.hitlag_policy",
                 "energy_generation_sources[0].calibration.travel_seconds",
                 "energy_generation_sources[0].replaces_spawn_ids",
                 "energy_generation_compilations[0].resource_projection.count",
                 "energy_generation_compilations[0].parent_links[0].absolute_tick"):
        cells = rows[path].findall("s:c", NS)
        assert cells[2].get("s") == "5"
        assert "<>" in cells[8].find("s:f", NS).text


def test_chunked_full_source_roundtrip_preserves_long_unicode(calculated):
    record = copy.deepcopy(calculated["result"]["energy_generation_compilations"][0])
    record["description"] = '=HYPERLINK("https://example.invalid","原文仅文本")' + "产球💧" * 18000
    rows = energy_generation_rows({"energy_generation_compilations": [record]})
    parts = [row[1] for row in rows if row and row[0] in ("完整来源、校准、接管清单与执行规则", "续")]
    assert len(parts) > 1
    assert json.loads("".join(parts)) == record
    assert all(len(value.encode("utf-16-le")) // 2 <= 30000 for value in parts)


@pytest.mark.parametrize("mutation", [
    lambda data: data["plan"]["energy_generation_sources"][0]["calibration"].update(travel_seconds=.3),
    lambda data: data["result_receipt"].pop("energy_generation"),
    lambda data: data["result"]["energy_generation_compilations"][0]["parent_links"][0].update(absolute_tick=1),
    lambda data: data["result"]["energy_generation_summary"].update(credited_particles=999),
])
def test_changed_particle_inputs_or_history_cannot_export(calculated, mutation):
    mutation(calculated)
    # Rehashing a changed outer result cannot bypass the separate source/ledger proof.
    calculated["result_receipt"]["result_fingerprint"] = fingerprint(calculated["result"])
    with pytest.raises(FormulaWorkbookError):
        build_formula_workbook(calculated)


def test_stripped_particle_metadata_cannot_leave_unproven_generated_history(calculated):
    for group in (calculated["result"], calculated["report"]):
        group.pop("energy_generation_compilations")
        group.pop("energy_generation_summary")
    calculated["result_receipt"].pop("energy_generation")
    calculated["result_receipt"]["result_fingerprint"] = fingerprint(calculated["result"])
    with pytest.raises(ValueError):
        result_bytes(calculated["result"], calculated["result_receipt"], "xlsx")
    with pytest.raises(FormulaWorkbookError):
        build_formula_workbook(calculated)
