"""Independent public-export checks; no database or author formula interpreter."""
from __future__ import annotations

from copy import deepcopy

import pytest

from genshin_app.equipment import (
    compose_equipment_plan,
    enable_mechanics,
    new_plan,
    run_plan_calc,
)
from genshin_app.equipment.files import fingerprint
from genshin_app.equipment.service import result_bytes
from genshin_app.export import export_result
from genshin_app.formula_workbook import FormulaWorkbookError, build_formula_workbook
from tests.test_external_damage import confirm, external_plan, supplement
from tests.test_freeze_export_review import workbook_sheets


def case_sensitive_calculated(db, *, two_engine_actors=False):
    """Real manual public calculation with two distinct native actor identities."""
    plan = external_plan()
    plan["manual_template"]["characters"][0]["name"] = "小写工程角色"
    other = deepcopy(plan["manual_template"]["characters"][0])
    other.update(id="A", name="大写工程角色")
    plan["manual_template"]["characters"].append(other)
    if two_engine_actors:
        hit = deepcopy(plan["manual_template"]["hits"][0])
        hit.update(id="upper.hit", character="A", t=1.1)
        plan["manual_template"]["hits"].append(hit)
    plan["external_damage_supplements"] = [
        supplement(expected=2, skill="burst"),
        supplement(id="upper", event_id="upper.event", coverage_key="upper.coverage",
                   character="A", expected=7, skill="元素爆发"),
    ]
    return run_plan_calc(confirm(plan, db), db)


@pytest.mark.parametrize("both", [False, True])
def test_review_case_sensitive_ownership_is_encoded_in_actual_formulas(tmp_path, both):
    calculated = case_sensitive_calculated(tmp_path / "absent.db", two_engine_actors=both)
    result = calculated["result"]
    assert result["calculation_totals"]["by_character"] == {"a": 47, "A": 52 if both else 7}
    assert result["calculation_totals"]["by_skill"] == {
        "手工工程命中": 90 if both else 45, "burst": 2, "元素爆发": 7,
    }
    sheets = workbook_sheets(build_formula_workbook(calculated))
    for address, key, own, other in (("B12", 1, "5", "6"), ("B13", 2, "6", "5")):
        formula = sheets["汇总"][address]["formula"]
        assert f"SUMIFS('外部补充伤害'!F5:F6,'外部补充伤害'!L5:L6,{key})" in formula
        assert sheets["外部补充伤害"]["L" + own]["value"] == key
        assert sheets["外部补充伤害"]["L" + other]["value"] != key
        assert f"SUMIFS('逐事件'!V5:V{6 if both else 5},'逐事件'!AD5:AD{6 if both else 5},{key})" in formula
        assert sheets["逐事件"]["AD5"]["value"] == 1
        if both:
            assert sheets["逐事件"]["AD6"]["value"] == 2
    assert 'EXACT(K5,"a")' in sheets["外部补充伤害"]["J5"]["formula"]
    assert 'EXACT(K6,"A")' in sheets["外部补充伤害"]["J6"]["formula"]
    snapshot = workbook_sheets(result_bytes(result, calculated["result_receipt"], "xlsx"))
    assert snapshot["汇总"]["B2"]["value"] == (99 if both else 54)
    assert not (tmp_path / "absent.db").exists()


@pytest.mark.parametrize("field", ["by_character", "by_skill"])
def test_review_resigned_wrong_subtotals_are_rejected_before_export(tmp_path, field):
    calculated = case_sensitive_calculated(tmp_path / "absent.db")
    result, receipt = calculated["result"], calculated["result_receipt"]
    subtotal = result["calculation_totals"][field]
    subtotal[next(iter(subtotal))] += 1
    receipt["result_fingerprint"] = fingerprint(result)
    target = tmp_path / "incorrect.xlsx"
    with pytest.raises(ValueError):
        export_result(result, "xlsx", target, receipt=receipt)
    with pytest.raises(FormulaWorkbookError):
        build_formula_workbook(calculated)
    assert not target.exists()


@pytest.mark.parametrize("change", ["value", "source"])
def test_review_changed_native_external_claim_cannot_reuse_old_confirmation(tmp_path, change):
    db = tmp_path / "absent.db"
    plan = confirm(external_plan(), db)
    item = plan["external_damage_supplements"][0]
    if change == "value":
        item["expected"] += 1
    else:
        item["source_refs"] = ["不同的独立工程依据"]
    review = compose_equipment_plan(plan, db)["external_damage_review"]
    assert review["status"] == "pending"
    assert any(issue["code"] == "external_damage_confirmation_required"
               for item in review["items"] for issue in item["issues"])
    with pytest.raises(ValueError):
        run_plan_calc(plan, db)


def test_review_all_miss_and_zero_external_remain_numeric_zero_without_fake_hits(tmp_path):
    from tests.test_verified_empty_damage_review import miss_case

    db = tmp_path / "absent.db"
    template, rotation, model = miss_case()
    template["characters"][0]["stats"]["energy_recharge"] = 1
    plan = enable_mechanics(new_plan(template))
    plan.update(rotation_model=rotation, mechanics_model=model,
                external_damage_supplements=[supplement(expected=0)])
    calculated = run_plan_calc(confirm(plan, db), db)
    result = calculated["result"]
    assert result["hits"] == []
    assert result["calculation_totals"]["total_damage"] == 0
    assert result["calculation_totals"]["dps"] == 0
    assert result["calculation_totals"]["external_count"] == 1
    snapshot = workbook_sheets(result_bytes(result, calculated["result_receipt"], "xlsx"))
    formula = workbook_sheets(build_formula_workbook(calculated))
    assert snapshot["汇总"]["B2"]["value"] == 0
    assert formula["汇总"]["B6"]["value"] == 0
    assert formula["汇总"]["B7"]["value"] == 0
    assert "V5" not in formula["逐事件"]
    assert formula["外部补充伤害"]["F5"]["value"] == 0
