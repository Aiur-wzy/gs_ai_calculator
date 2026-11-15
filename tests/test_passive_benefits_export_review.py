"""Independent EC12 materialized receipts and emitted arithmetic checks."""
from __future__ import annotations

import copy
import sqlite3

import pytest
from test_freeze_export_review import workbook_sheets
from test_passive_benefits_app_review import switch_to_receiver
from test_passive_sources_app import accept
from test_passive_sources_app import (
    passive_attack_db as passive_attack_db,  # noqa: PLC0414
)
from test_passive_sources_app import passive_plan as passive_plan  # noqa: PLC0414
from test_reaction_sources_review import evaluate_numeric_cell

from genshin_app.equipment import compose_equipment_plan, run_plan_calc
from genshin_app.equipment.schema import PlanError
from genshin_app.equipment.service import result_bytes
from genshin_app.formula_workbook import (
    FormulaWorkbookError,
    build_formula_workbook,
    formula_workbook_support,
)


@pytest.fixture
def passive_output(passive_plan):
    plan, db = passive_plan
    accept(plan, db)
    switch_to_receiver(plan)
    return run_plan_calc(plan, db)


def test_review_workbook_recalculates_recipient_and_swirl_and_locks_source_factor_without_db(passive_output, monkeypatch):
    before = copy.deepcopy(passive_output)
    monkeypatch.setattr(sqlite3, "connect", lambda *a, **k: pytest.fail("export opened DB"))
    monkeypatch.setattr(sqlite3.dbapi2, "connect", lambda *a, **k: pytest.fail("export opened DB"))
    support = formula_workbook_support(passive_output)
    assert support["editable"]["passive_sources_progression_and_stat_basis"] == "regenerate_required"
    assert support["editable"]["character_stats"] == "supported_except_passive_provider_em"
    assert support["editable"]["passive_provider_elemental_mastery"] == "regenerate_required"
    sheets = workbook_sheets(build_formula_workbook(passive_output))
    assert "固有天赋来源" in sheets
    assert all(not c["formula"] for c in sheets["固有天赋来源"].values())
    hits = passive_output["result"]["hits"]
    receiver = next(i for i, h in enumerate(hits, 5) if h["id"] == "cryo.damage")
    swirl = next(i for i, h in enumerate(hits, 5) if h["damage_system"] == "transformative")
    # Independent base atk=100, multiplier=1, defense=.5, resistance=.9;
    # source factor is the exact raw IEEE-754 value, not rounded .0004.
    assert evaluate_numeric_cell(sheets, "逐事件", f"V{receiver}") == pytest.approx(61.19999959075358)
    assert evaluate_numeric_cell(sheets, "逐事件", f"V{swirl}") == pytest.approx(1446.85 * .6 * (1 + 16 * 900 / 2900) * .9)
    cells = sheets["参数"]
    source_rows = [address[1:] for address, cell in cells.items() if address.startswith("A") and isinstance(cell["value"], str)
        and cell["value"].startswith("mechanics_model.rules[") and cell["value"].endswith(".value.factor")]
    assert len(source_rows) == 2
    assert all(cells["E" + row]["value"] == "回 App 重新生成" for row in source_rows)
    assert all(cells["I" + row]["formula"] not in (None, "0") for row in source_rows)
    em_row = next(address[1:] for address, cell in cells.items() if address.startswith("A") and cell["value"] == "characters[0].stats.em")
    assert cells["E" + em_row]["value"] == "回 App 重新生成"
    assert cells["I" + em_row]["formula"] == f"IF(C{em_row}<>D{em_row},1,0)"
    assert cells["D" + em_row]["value"] == 900
    for path in ("characters[0].stats.atk", "characters[1].stats.em", "characters[1].stats.atk"):
        row = next(address[1:] for address, cell in cells.items() if address.startswith("A") and cell["value"] == path)
        assert cells["E" + row]["value"] == "数值公式重算"
        assert cells["I" + row]["formula"] == "0"
    # This checks dependency linkage only: altering this fixed input lights the
    # guard and requires regeneration. It is not a newly supported edit mode.
    edits = {("参数", "C" + em_row): 500}
    assert evaluate_numeric_cell(sheets, "逐事件", f"V{receiver}", edits) == pytest.approx(45 * (1 + 500 * .00039999998989515007))
    assert evaluate_numeric_cell(sheets, "逐事件", f"V{swirl}", edits) == pytest.approx(1446.85 * .6 * (1 + 16 * 500 / 2500) * .9)
    for fmt in ("json", "csv", "xlsx"):
        assert result_bytes(passive_output["result"], passive_output["result_receipt"], fmt)
    assert passive_output == before


@pytest.mark.parametrize("change", ["source", "unlock", "accept", "scope", "coverage", "rule_domain", "rule_factor", "initial",
    "native_progression", "native_basis", "receipt_model", "receipt_sources"])
def test_review_formula_rejects_single_changed_layer_without_changed_damage(passive_output, change):
    # Detach each outer layer; deepcopying the whole object preserves aliasing.
    out = {k: copy.deepcopy(v) for k, v in passive_output.items()}
    record = out["report"]["passive_compilations"][0]
    if change == "source":
        record["source"]["selection"]["proud_skill_id"] += 1
    elif change == "unlock":
        record["unlock"]["status"] = "locked"
    elif change == "accept":
        record["accepted_fingerprint"] = "0" * 64
    elif change == "scope":
        record["scope"]["provider_parent_events"] = []
    elif change == "coverage":
        record["coverage_certificate"]["source_complete"] = True
    elif change == "rule_domain":
        out["report"]["mechanics_model"]["rules"][0]["effects"][0]["domain"] = "provider"
    elif change == "rule_factor":
        out["report"]["mechanics_model"]["rules"][0]["effects"][0]["value"]["factor"] = .0005
    elif change == "initial":
        out["report"]["mechanics_model"]["initial_state"]["rules"][0]["cooldown_remaining"] = 1
    elif change == "native_progression":
        out["plan"]["equipment_inputs"]["a"]["progression"]["ascension"] = 5
    elif change == "native_basis":
        out["plan"]["passive_sources"][0]["scenario"]["stat_basis"] = "unknown"
    elif change == "receipt_model":
        out["result_receipt"]["passives"]["compiled_mechanics_model_fingerprint"] = "0" * 64
    else:
        out["result_receipt"]["passives"]["input_sources_fingerprint"] = "0" * 64
    with pytest.raises(FormulaWorkbookError):
        build_formula_workbook(out)


@pytest.mark.parametrize("change", ["source", "unlock", "accept", "scope", "rule"])
def test_review_snapshot_rejects_changed_source_record(passive_output, change):
    out = {k: copy.deepcopy(v) for k, v in passive_output.items()}
    record = out["result"]["passive_compilations"][0]
    if change == "source":
        record["source"]["selection"]["proud_skill_id"] += 1
    elif change == "unlock":
        record["unlock"]["status"] = "locked"
    elif change == "accept":
        record["accepted_fingerprint"] = "0" * 64
    elif change == "scope":
        record["scope"]["provider_parent_events"] = []
    else:
        record["rules"][0]["effects"][0]["value"]["factor"] = .0005
    for fmt in ("json", "csv", "xlsx"):
        with pytest.raises(PlanError):
            result_bytes(out["result"], out["result_receipt"], fmt)


@pytest.mark.parametrize("key", ["compiled_mechanics_model_fingerprint", "mechanics_input_template_fingerprint"])
def test_review_snapshot_rejects_conflicting_shared_execution_receipts(passive_output, key):
    out = {k: copy.deepcopy(v) for k, v in passive_output.items()}
    # Both blocks describe the same final execution input. This contradiction is
    # observable offline without native inputs, a DB, or replaying mechanics.
    assert out["result_receipt"]["passives"][key] == out["result_receipt"]["action_events"][key]
    out["result_receipt"]["passives"][key] = "0" * 64
    for fmt in ("json", "csv", "xlsx"):
        with pytest.raises(PlanError):
            result_bytes(out["result"], out["result_receipt"], fmt)


def test_review_vv_and_passive_coexist_with_distinct_bonus_domains_and_exported_arithmetic(passive_plan, monkeypatch):
    from test_reaction_benefits_review import bundle, scope

    from genshin_db import queries, runtime_queries

    plan, db = passive_plan
    raw_vv = bundle()
    # Only VV assembly/curated equipment lookup is fixture-provided. E metadata
    # and inherent talent selection continue through the real isolated DB.
    original_assemble = runtime_queries.assemble_runtime_source

    def assemble(*args, **kwargs):
        request = args[1] if len(args) > 1 else kwargs
        if request == 2150021:
            return copy.deepcopy(raw_vv)
        return original_assemble(*args, **kwargs)

    monkeypatch.setattr(runtime_queries, "assemble_runtime_source", assemble)
    monkeypatch.setattr(queries, "get_artifact_set_detail", lambda *a, **k: {
        "artifact_set": {"name": "Independent four-piece VV fixture"}, "effects": [
            {"required_count": 4, "effect": {"affix_id": 2150021, "name": "Ordinary swirl",
                "source_refs": [{"kind": "database"}]}}], "annotations": []})
    plan["equipment_inputs"]["a"]["artifact_sets"] = [{"set_id": 15002, "count": 4}]
    first = compose_equipment_plan(plan, db)
    binding = copy.deepcopy(first["effect_requirements"][0]["proposed_binding"])
    binding["parts"] = []
    binding["upstream_recipe"] = {"schema_version": 1, "scenario": scope()}
    plan["effect_bindings"] = [binding]
    proposed = compose_equipment_plan(plan, db)
    binding["upstream_recipe"]["scenario"].update(proposed["upstream_compilations"][0]["required_confirmation"])
    accept(plan, db)
    switch_to_receiver(plan)
    out = run_plan_calc(plan, db)
    assert len(out["report"]["mechanics_model"]["rules"]) == 4
    assert len(out["report"]["mechanics_input_template"]["buffs"]) == 1
    assert plan["mechanics_model"]["rules"] == [] and plan["manual_template"]["buffs"] == []
    receiver = next((i, h) for i, h in enumerate(out["result"]["hits"], 5) if h["id"] == "cryo.damage")
    swirl = next((i, h) for i, h in enumerate(out["result"]["hits"], 5) if h["damage_system"] == "transformative")
    # VV shred is .4 -> resistance multiplier1.15; ordinary bonus is 900*f.
    # Swirl gets VV's .6 reaction bonus, but never the ordinary A4 damage bonus.
    resistance = 1 - (.1 - .4000000059604645) / 2
    expected_receiver = 50 * (1 + 900 * .00039999998989515007) * resistance
    expected_swirl = 1446.85 * .6 * (1 + 16 * 900 / 2900 + .6000000238418579) * resistance
    assert receiver[1]["expected"] == pytest.approx(expected_receiver)
    assert swirl[1]["expected"] == pytest.approx(expected_swirl)
    sheets = workbook_sheets(build_formula_workbook(out))
    assert {"固有天赋来源", "反应收益来源"} <= sheets.keys()
    assert evaluate_numeric_cell(sheets, "逐事件", f"V{receiver[0]}") == pytest.approx(expected_receiver)
    assert evaluate_numeric_cell(sheets, "逐事件", f"V{swirl[0]}") == pytest.approx(expected_swirl)
