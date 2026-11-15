"""Independent EC04 App/OOXML tests: sample membership must survive formula export."""
from __future__ import annotations

import ast
import copy
import operator
import re

import pytest
from test_formula_workbook import read_sheet
from test_mechanics_execution import add, rule
from test_plan_mechanics import native as binding_fixture
from test_plan_mechanics import review
from test_reaction_sources_app import confirmed, source_api  # noqa: F401
from test_swirl_review import inputs

from genshin_app.equipment import new_plan, run_plan_calc
from genshin_app.equipment.files import fingerprint
from genshin_app.equipment.mechanics import enable_mechanics
from genshin_app.formula_workbook import (
    build_formula_workbook,
    formula_workbook_support,
)


def expression_plan(db):
    template, rotation, model = inputs((.2, .3))
    template["characters"][0]["stats"]["energy_recharge"] = 1
    model["elemental_model"]["swirl_damage"]["icd"] = {"kind": "unknown"}
    add(model,
        rule("ratio", "benefit.reaction_bonus.swirl", {"kind": "stat", "subject": "owner",
             "stat": "atk", "factor": .006, "sample": "current"}, domain="provider"),
        rule("post_attack", "benefit.stat_add.atk", 100, trigger={"event": "hit", "phase": "post"}),
        rule("shred", "benefit.res_shred.cryo", .4, domain="enemy",
             trigger={"event": "elemental_reaction", "phase": "post", "reaction_elements": ["cryo"]}))
    plan = enable_mechanics(new_plan(template))
    plan.update(rotation_model=rotation, mechanics_model=model)
    binding = binding_fixture()["effect_bindings"][0]
    part = binding["parts"][0]
    binding["parts"] = []
    for item in model["rules"]:
        item["source_id"] = "binding:" + item["id"]
        row = copy.deepcopy(part)
        row.update(part_id=item["id"], rule={"kind": "mechanics", "rule_id": item["id"]})
        binding["parts"].append(row)
    plan["effect_bindings"] = [binding]
    plan["reaction_damage_sources"] = [{"reaction_type": "swirl", "version_line": "release",
        "source": "Independent source binding; ordinary origin-target swirl only",
        "expected_snapshot_id": None, "expected_profile_fingerprint": None}]
    review(plan, db)
    return confirmed(plan, db)


def evaluate_numeric_cell(sheets, sheet, address, edits=None):
    """Evaluate only the exported arithmetic subset, never its cached formula values.

    This is an independent restricted expression interpreter, not Excel automation.
    Unknown syntax fails the test instead of falling back to a cached engine answer.
    """
    address = address.replace("$", "")
    if (sheet, address) in (edits or {}):
        return edits[sheet, address]
    cell = sheets[sheet][address]
    if not cell["formula"]:
        assert isinstance(cell["value"], (int, float)), cell
        return cell["value"]
    formula = re.sub(r"'([^']+)'!\$?([A-Z]+)\$?(\d+)",
        lambda m: repr(evaluate_numeric_cell(sheets, m[1], m[2] + m[3], edits)), cell["formula"])
    formula = re.sub(r"(?<![A-Za-z0-9_])\$?([A-Z]+)\$?(\d+)",
        lambda m: repr(evaluate_numeric_cell(sheets, sheet, m[1] + m[2], edits)), formula).replace("^", "**")
    operations = {ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul,
                  ast.Div: operator.truediv, ast.Pow: operator.pow, ast.Lt: operator.lt,
                  ast.Gt: operator.gt, ast.LtE: operator.le, ast.GtE: operator.ge}

    def visit(node):
        if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
            return node.value
        if isinstance(node, ast.BinOp):
            return operations[type(node.op)](visit(node.left), visit(node.right))
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.USub):
            return -visit(node.operand)
        if isinstance(node, ast.Compare) and len(node.ops) == 1:
            return operations[type(node.ops[0])](visit(node.left), visit(node.comparators[0]))
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            if node.func.id == "IF":
                return visit(node.args[1] if visit(node.args[0]) else node.args[2])
            return {"MIN": min, "MAX": max}[node.func.id](*map(visit, node.args))
        raise AssertionError(f"unsupported formula syntax: {ast.dump(node)}")
    return visit(ast.parse(formula, mode="eval").body)


@pytest.mark.usefixtures("source_api")
def test_review_excel_recomputes_frozen_provider_dependencies_and_current_enemy(tmp_path):
    calculated = run_plan_calc(expression_plan(tmp_path / "absent.db"), tmp_path / "absent.db")
    support = formula_workbook_support(calculated)
    assert support["status"] == "supported"
    assert support["editable"]["declared_stat_expression"] == "supported_fixed_samples"
    assert support["editable"]["database_reaction_damage"] == "regenerate_required"
    raw = build_formula_workbook(calculated)
    sheets = {"参数": read_sheet(raw, 2), "逐事件": read_sheet(raw, 3)}
    paths = {cell["value"]: "C" + ref[1:] for ref, cell in sheets["参数"].items() if ref.startswith("A")}
    rows = [i for i, hit in enumerate(calculated["result"]["hits"], 5) if hit["damage_system"] == "transformative"]
    assert len(rows) == 2
    damage = lambda edits: [evaluate_numeric_cell(sheets, "逐事件", f"V{i}", edits) for i in rows]
    # 1446.85*.6*(1+.006*100 or 200)*(1-(.1-.4)/2).
    assert damage({}) == pytest.approx([1597.3224, 2196.3183])
    # Editing the post-hit source cannot retroactively enter the first sample.
    attack = paths["mechanics_model.rules[1].effects[0].value"]
    assert damage({("参数", attack): 200}) == pytest.approx([1597.3224, 2795.3142])
    # Base panel changes both fixed samples; newly acquired enemy shred applies now.
    assert damage({("参数", paths["characters[0].stats.atk"]): 200}) == pytest.approx([2196.3183, 2795.3142])
    shred = paths["mechanics_model.rules[2].effects[0].value"]
    assert damage({("参数", shred): 0}) == pytest.approx([1250.0784, 1718.8578])
    assert calculated["plan"]["mechanics_model"]["elemental_model"]["swirl_damage"]["icd"] == {"kind": "unknown"}


@pytest.mark.usefixtures("source_api")
def test_review_missing_provider_dependency_never_claims_live_excel_support(tmp_path):
    calculated = run_plan_calc(expression_plan(tmp_path / "absent.db"), tmp_path / "absent.db")
    samples = calculated["result"]["sampling_trace"]
    sample = next(row for row in samples if row.get("event_id", "").endswith(":reaction_damage:0")
                  and row.get("dependencies"))
    sample["dependencies"][0]["rule_id"] = "missing_source_rule"
    calculated["result_receipt"]["result_fingerprint"] = fingerprint(calculated["result"])
    support = formula_workbook_support(calculated)
    assert support["status"] == "partial"
    assert support["editable"]["character_stats"] == "regenerate_required"
    assert support["editable"]["declared_stat_expression"] == "regenerate_required"
    assert any(row["code"] == "mechanics_dependency_unavailable" for row in support["limitations"])
