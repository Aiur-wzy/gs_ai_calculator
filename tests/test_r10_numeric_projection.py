"""Generic regression/oracles for weighting, live projections and saved binding."""
from __future__ import annotations

import copy
import itertools
import json
import math

import pytest
from formula_workbook_fixtures import envelope, probe_template
from test_elemental_multiaura_v4 import aura
from test_formula_workbook import read_sheet
from test_mechanics_execution import second_character
from test_special_reaction_mechanics_projection import _application_fixture, _enable

from genshin_app.calculation_integrity import (
    result_transport_parts,
    validate_result_binding,
)
from genshin_app.calculation_review import read_bounded_json, review_calculation
from genshin_app.equipment.files import fingerprint
from genshin_app.equipment.schema import PlanError
from genshin_app.equipment.service import result_bytes
from genshin_app.export import export_result, formal_calculation_audit_payload
from genshin_app.formula_workbook import (
    FormulaWorkbookError,
    build_formula_workbook,
    formula_workbook_support,
)
from genshin_engine.constants import LUNAR_FACTOR, STELLAR_FACTOR
from genshin_engine.mechanics import execute_mechanics
from genshin_engine.mechanics_special_damage import _apply_special_damage_weights

FAMILIES = [("classic", None), ("transformative", "swirl")]
FAMILIES += [("lunar", r) for r in LUNAR_FACTOR]
FAMILIES += [("stellar", r) for r in [*STELLAR_FACTOR, "stellar_superconduct"]]


@pytest.mark.parametrize("system,reaction", FAMILIES)
@pytest.mark.parametrize("weight", [None, .3, .8, 1])
@pytest.mark.parametrize("count,rate", [(1, 0), (3, .5)])
def test_all_formula_families_apply_fixed_weight_to_expected_and_critical(system, reaction, weight, count, rate):
    template = probe_template()
    hit = copy.deepcopy(template["hits"][0])
    hit.update(damage_system=system, count=count)
    if reaction:
        hit["reaction"] = {"type": reaction, **({"layers": 4} if reaction == "stellar_superconduct" else {})}
    if weight is not None:
        hit["result_weight"] = weight
    template["hits"] = [hit]
    template["characters"][0]["stats"]["crit_rate"] = rate
    calculated = envelope(template)
    sheet = read_sheet(build_formula_workbook(calculated), 3)
    actual = calculated["result"]["hits"][0]
    assert sheet["V5"]["value"] == pytest.approx(actual["expected"])
    if actual["crit"] is not None:
        assert sheet["W5"]["value"] == pytest.approx(actual["crit"])
    else:
        assert sheet["W5"]["value"] == "不适用"
    assert sheet["AF5"]["value"] == pytest.approx(weight if weight is not None else 1)


def weighted_inputs(*, zero=False):
    template, rotation, model = _application_fixture("anemo", initial=[aura("cryo", .7, "b")])
    second_character(template, rotation, "b")
    template["characters"][0]["stats"].update(em=0, crit_rate=.2, crit_dmg=1)
    template["characters"][1]["stats"].update(em=800, crit_rate=.5, crit_dmg=1)
    _enable(model, "stellar_swirl")
    if zero:
        template.setdefault("buffs", []).append({"id": "zero", "start": 0, "end": 10,
            "scope": "team", "effects": [{"id": "zero.base", "zone": "stellar_base", "stat": "all", "value": -1,
                "benefit_type_id": "benefit.stellar_base.all", "applies_to": {"mode": "all"}}]})
    return template, rotation, model


def weighted_calculation():
    template, rotation, model = weighted_inputs()
    executed = execute_mechanics(template, rotation, model)
    assert executed["result"] is not None, executed["issues"]
    result = executed["result"]
    plan = {"plan_id": "generic-weighted-workbook", "revision": 1,
        "resolved_template": executed["resolved_template"], "mechanics_model": model, "rotation_model": rotation}
    return {"plan": plan, "result": result, "result_receipt": {"plan_id": plan["plan_id"], "revision": 1,
        "output_fingerprint": fingerprint(plan["resolved_template"]), "result_fingerprint": fingerprint(result)},
        "report": {"mechanics_model": model, "mechanics_input_template": template}}


def test_derived_weight_builds_baseline_and_invalidates_rows_when_distribution_changes():
    calculated = weighted_calculation()
    support = formula_workbook_support(calculated)
    assert support["status"] == "partial"
    assert support["special_weighting"]["edit_policy"] == "readonly_numeric_baseline"
    sheet = read_sheet(build_formula_workbook(calculated), 3)
    expected = calculated["result"]["hits"]
    for i, actual in enumerate(expected, 5):
        assert sheet[f"V{i}"]["value"] == pytest.approx(actual["expected"])
        assert "需重新生成" in sheet[f"V{i}"]["formula"]
        if actual["crit"] is not None:
            assert "需重新生成" in sheet[f"W{i}"]["formula"]


def resolved_weighted_calculation(*, other_hit=False):
    calculated = weighted_calculation()
    # This is a resolved numeric-input scenario, no promise to replay mechanics.
    calculated["plan"].pop("mechanics_model")
    calculated["plan"].pop("rotation_model")
    calculated["report"] = {}
    template = calculated["plan"]["resolved_template"]
    template["characters"].append({"id": "other", "name": "无关角色", "level": 90,
        "stats": {"atk": 100, "em": 0, "crit_rate": 0, "crit_dmg": .5, "energy_recharge": 1}})
    if other_hit:
        template["hits"].append({"id": "other.hit", "t": .8, "character": "other", "skill": "独立普通伤害",
            "hit_name": "独立普通伤害", "damage_type_ids": ["damage.elemental_skill"],
            "damage_system": "classic", "element": "physical", "multiplier": 1})
        template.setdefault("buffs", []).append({"id": "other.buff", "start": 0, "end": 2,
            "scope": "team", "target": "other", "effects": [{"id": "other.atk", "zone": "stat_add", "stat": "atk", "value": 20,
                "benefit_type_id": "benefit.stat_add.atk", "applies_to": {"mode": "all"}}]})
    # Recompute the original engine-only fixture through the shared core, while
    # retaining the actual special distribution proof and its unchanged rows.
    from genshin_engine import calculate
    fresh = calculate(template)
    for key in ("special_reactions", "elemental", "mechanics", "buff_state_timeline"):
        if key in calculated["result"]:
            fresh[key] = calculated["result"][key]
    calculated["result"] = fresh
    calculated["result_receipt"].update(output_fingerprint=fingerprint(template), result_fingerprint=fingerprint(fresh))
    return calculated


def test_known_resolved_group_only_guards_affected_inputs():
    calculated = resolved_weighted_calculation()
    support = formula_workbook_support(calculated)
    assert support["special_weighting"]["edit_policy"] == "regenerate_affected_contribution_group"
    assert support["editable"]["character_stats"] == "supported_except_special_weighting_dependencies"
    raw = build_formula_workbook(calculated)
    inputs = read_sheet(raw, 2)
    actor_row = next(int(cell[1:]) for cell, row in inputs.items() if cell.startswith("A") and row["value"] == "characters[2].stats.atk")
    assert inputs[f"E{actor_row}"]["value"] == "数值公式重算"


def test_complete_disjoint_numeric_inputs_recalculate_without_reusing_changed_group_weights():
    from formula_owner_oracle import evaluator, read

    from genshin_engine import calculate

    calculated = resolved_weighted_calculation(other_hit=True)
    before = copy.deepcopy(calculated)
    pages, _ = read(build_formula_workbook(calculated))
    paths = {cell["value"]: int(address[1:]) for address, cell in pages["参数"].items() if address.startswith("A")}
    total = calculated["result"]["summary"]["total_damage"]
    assert evaluator(pages)("汇总", "B6") == pytest.approx(total)
    for path, value in (("characters[2].stats.atk", 150), ("buffs[0].effects[0].value", 35)):
        row = paths[path]
        assert pages["参数"][f"E{row}"]["value"] == "数值公式重算"
        changed = copy.deepcopy(calculated["plan"]["resolved_template"])
        if path.startswith("characters"):
            changed["characters"][2]["stats"]["atk"] = value
        else:
            changed["buffs"][0]["effects"][0]["value"] = value
        fresh = calculate(changed)
        assert evaluator(pages, {("参数", f"C{row}"): value})("汇总", "B6") == pytest.approx(fresh["summary"]["total_damage"])
    for path in ("characters[0].stats.em", "characters[1].stats.crit_rate", "characters[2].stats.energy_recharge"):
        row = paths[path]
        assert pages["参数"][f"E{row}"]["value"] == "回 App 重新生成"
        assert pages["参数"][f"I{row}"]["formula"] != "0"
        changed = evaluator(pages, {("参数", f"C{row}"): pages["参数"][f"C{row}"]["value"] + .1})
        assert changed("参数", f"I{row}") == 1
        if path.startswith(("characters[0]", "characters[1]")):
            weighted_id = calculated["result"]["special_reactions"]["damage_weighting"][0]["ranking"][0]["hit_id"]
            event_row = next(i for i, hit in enumerate(calculated["plan"]["resolved_template"]["hits"], 5) if hit["id"] == weighted_id)
            assert "重新生成" in changed("逐事件", f"V{event_row}")
        else:
            assert "重新生成" in changed("汇总", "B6")
    assert calculated == before


@pytest.mark.parametrize("context", ["other_limitation", "state_runtime", "shared_pools", "attribute_ledger", "attribute_callbacks"])
def test_resolved_group_permission_does_not_bypass_other_dependency_owners(context):
    from genshin_app.formula_workbook import _Projection

    item = _Projection(resolved_weighted_calculation(other_hit=True), _session=None)
    # Exercise the same production parameter/guard owner; no invented execution
    # receipt is submitted to the public export validator as an accepted result.
    if context == "other_limitation":
        item.support["limitations"].append({"code": "mechanics_dependencies_unavailable"})
    else:
        item.result[context] = {"contract": "attribute-ledger-v4"} if context == "attribute_ledger" else {"dependency": "fixed"}
    item.prepare_inputs()
    row = item.inputs.rows[item.rows_by_path["characters[2].stats.atk"] - 1]
    assert row[4].value == "回 App 重新生成"


def test_resolved_weighting_locality_does_not_admit_unknown_stats_or_er():
    from genshin_app.formula_sources import local_numeric_edit

    for path in ("characters[2].stats.energy_recharge", "characters[2].stats.future_source",
                 "characters[2].level", "buffs[0].start", "mechanics_model.rules[0].effects[0].value"):
        assert not local_numeric_edit(path, resolved_weighting=True)


def test_actual_cross_stat_provider_sharing_still_requires_regeneration(tmp_path):
    from genshin_app.equipment import run_plan_calc
    from tests.test_attribute_stat_app import adopt, stat_plan

    database = tmp_path / "absent.db"
    calculated = run_plan_calc(adopt(stat_plan(), database), database)
    assert calculated["result"]["hits"][0]["expected"] == 117
    assert calculated["result"]["attribute_ledger"]["contract"] == "attribute-ledger-v3"
    params = read_sheet(build_formula_workbook(calculated), 2)
    paths = {cell["value"]: int(address[1:]) for address, cell in params.items() if address.startswith("A")}
    for path in ("characters[0].stats.hp", "characters[0].stats.atk", "characters[1].stats.em",
                 "mechanics_model.rules[0].effects[0].value.factor"):
        row = paths[path]
        assert params[f"E{row}"]["value"] == "回 App 重新生成"
        assert params[f"I{row}"]["formula"] == f"IF(C{row}<>D{row},1,0)"


def test_unknown_weighting_dependency_has_no_empty_editable_closure():
    calculated = envelope()
    hit = calculated["plan"]["resolved_template"]["hits"][0]
    hit["extensions"] = {"special_reaction": {"result_weight_semantics": "future_unresolved_contract"}}
    calculated["result_receipt"]["output_fingerprint"] = fingerprint(calculated["plan"]["resolved_template"])
    support = formula_workbook_support(calculated)
    assert support["special_weighting"]["edit_policy"] == "readonly_numeric_baseline"
    assert support["editable"]["character_stats"] == "regenerate_required"
    book = build_formula_workbook(calculated)
    inputs = read_sheet(book, 2)
    numeric_rows = [int(cell[1:]) for cell, row in inputs.items()
                    if cell.startswith("C") and type(row["value"]) in (int, float)]
    assert numeric_rows and all(inputs[f"E{row}"]["value"] == "回 App 重新生成" for row in numeric_rows)
    assert all("需重新生成" in row.get("formula", "") for cell, row in read_sheet(book, 3).items()
               if cell.startswith("V") and int(cell[1:]) >= 5)


@pytest.mark.parametrize("mutation", ["drop", "member", "weight", "joint", "duplicate"])
def test_invalid_required_weighting_proof_cannot_be_partial_baseline(mutation):
    calculated = weighted_calculation()
    groups = calculated["result"]["special_reactions"]["damage_weighting"]
    if mutation == "drop":
        groups.clear()
    elif mutation == "member":
        groups[0]["ranking"][0]["hit_id"] = "missing"
    elif mutation == "weight":
        groups[0]["ranking"][0]["result_weight"] = .123
    elif mutation == "joint":
        groups[0].pop("joint_outcomes")
    else:
        groups.append(copy.deepcopy(groups[0]))
    # Prove this is the distribution guard, not only the generic stale hash.
    calculated["result_receipt"]["result_fingerprint"] = fingerprint(calculated["result"])
    with pytest.raises(FormulaWorkbookError, match="贡献|分配|命中"):
        formula_workbook_support(calculated)
    with pytest.raises(FormulaWorkbookError):
        build_formula_workbook(calculated)


def test_zero_weighted_damage_retains_events_and_positive_canonical_weights():
    normal = execute_mechanics(*weighted_inputs())
    zero = execute_mechanics(*weighted_inputs(zero=True))
    assert zero["result"] is not None, zero["issues"]
    assert [h["id"] for h in zero["result"]["hits"]] == [h["id"] for h in normal["result"]["hits"]]
    special = [h for h in zero["result"]["hits"] if h["damage_system"] == "stellar"]
    assert special and all(h["expected"] == 0 and h["crit"] == 0 and h["result_weight"] > 0 for h in special)
    assert [r["type"] for r in zero["mechanics_trace"]].count("reaction_damage") == [r["type"] for r in normal["mechanics_trace"]].count("reaction_damage")


def allocation_fixture(size, scale=1):
    rates = (0, .2, .5, 1)
    hits, results = [], []
    for i in range(size):
        hid = f"g:{i}"
        base, rate, cd = (10 + 3 * i) * scale, rates[i], 1.5
        hits.append({"id": hid, "character": f"actor{i}", "extensions": {"special_reaction": {}}})
        results.append({"id": hid, "expected": base * (1 + rate * cd), "crit": base * (1 + cd), "zones": {"crit_expected": 1 + rate * cd}})
    return {"hits": hits}, {"hits": results}, {"g": {"hit_ids": [h["id"] for h in hits],
        "request": {"event_id": "g", "reaction_type": "lunar_electro", "t": 1}}}


@pytest.mark.parametrize("size", [1, 2, 3, 4])
@pytest.mark.parametrize("scale", [1, 1e-20, 1e-200, math.ldexp(1, -1022)/10])
def test_weight_allocation_matches_independent_joint_enumeration(size, scale):
    resolved, first, groups = allocation_fixture(size, scale)
    receipt = _apply_special_damage_weights(resolved, first, groups)[0]
    rates, weights = (0, .2, .5, 1), (.6, .3, .05, .05)
    totals = [0.0] * size
    for outcomes in itertools.product((False, True), repeat=size):
        probability = math.prod(rates[i] if c else 1 - rates[i] for i, c in enumerate(outcomes))
        # Work at unit scale: independent oracle never multiplies subnormals.
        damage = [(10 + 3*i) * (2.5 if crit else 1) for i, crit in enumerate(outcomes)]
        ranking = sorted(range(size), key=lambda i: (-damage[i], f"g:{i}"))
        for rank, i in enumerate(ranking):
            totals[i] += probability * damage[i] * weights[rank]
    rows = {r["hit_id"]: r for r in receipt["ranking"]}
    for i in range(size):
        row = rows[f"g:{i}"]
        assert row["result_weight"] == pytest.approx(totals[i] / ((10+3*i) * (1+rates[i]*1.5)), rel=1e-12)
        assert row["expected_weighted_damage"] / scale == pytest.approx(totals[i], rel=1e-12)
    assert receipt["joint_probability_total"] == pytest.approx(1)


@pytest.mark.parametrize("size", [2, 3, 4])
@pytest.mark.parametrize("reaction", ["stellar_swirl_anemo", "stellar_swirl_cryo_lv2"])
@pytest.mark.parametrize("tied", [False, True])
def test_role_slot_allocation_matches_independent_oracle_with_ties_and_rank_changes(size, reaction, tied):
    resolved, first, groups = allocation_fixture(size)
    request = groups["g"]["request"]
    request.update(reaction_type=reaction, role_provenance={
        "contract": "stellar-swirl-role-provenance-v1", "status": "ready",
        "participant_ids": [f"actor{i}" for i in range(size)],
        "cryo_applier_ids": ["actor0"], "anemo_trigger_ids": ["actor1"],
        "component_policy": "immediate_anemo_trigger_fixed_first" if reaction.endswith("anemo") else "vortex_cryo_best_then_anemo_best"})
    rates, weights = (0, .2, .5, 1), (.6, .3, .05, .05)
    bases = [10 if tied else 10+3*i for i in range(size)]
    for i, row in enumerate(first["hits"]):
        row.update(expected=bases[i]*(1+rates[i]*1.5), crit=bases[i]*2.5)
    receipt = _apply_special_damage_weights(resolved, first, groups)[0]
    totals = [0.0]*size
    for critical in itertools.product((False, True), repeat=size):
        probability = math.prod(rates[i] if c else 1-rates[i] for i, c in enumerate(critical))
        damage = [bases[i]*(2.5 if c else 1) for i, c in enumerate(critical)]
        # Role slots precede numerical ranking. This oracle deliberately does
        # not call either production ranking or coefficient helpers.
        slots = [1] if reaction.endswith("anemo") else [0, 1]
        slots += sorted((i for i in range(size) if i not in slots), key=lambda i: (-damage[i], f"actor{i}"))
        for rank, i in enumerate(slots):
            totals[i] += probability*damage[i]*weights[rank]
    assert receipt["expected"] == pytest.approx(sum(totals))
    for row in receipt["ranking"]:
        i = int(row["contributor_id"].removeprefix("actor"))
        assert row["result_weight"] == pytest.approx(totals[i]/first["hits"][i]["expected"])


def test_exact_zero_is_not_underflow_zero_and_subnormal_failure_is_explicit():
    resolved, first, groups = allocation_fixture(1)
    row = first["hits"][0]
    row.update(expected=0, crit=0)
    row["zones"].update(base_in=0, system_factor=1, base_bonus=1, em_zone=1,
        big_mult=1, reaction_flat=0, resistance=1, elevate=1)
    result = _apply_special_damage_weights(resolved, first, groups)[0]
    assert result["ranking"][0]["result_weight"] == pytest.approx(.6)
    row["zones"].update(base_in=1e-300, system_factor=1e-100)
    with pytest.raises(ValueError, match="浮点表示范围"):
        _apply_special_damage_weights(resolved, first, groups)
    row.update(expected=5e-324, crit=1e-323, zones={"crit_expected": 1.5})
    with pytest.raises(ValueError, match="浮点"):
        _apply_special_damage_weights(resolved, first, groups)


@pytest.mark.parametrize("where", ["hit", "summary", "trace"])
def test_formal_export_helpers_reject_stale_result_before_publication(tmp_path, where):
    calculated = envelope()
    result, receipt = calculated["result"], calculated["result_receipt"]
    if where == "hit":
        result["hits"][0]["expected"] += 1
    elif where == "summary":
        result["summary"]["total_damage"] += 1
    else:
        result["unknown_trace"] = [{"t": 1}]
    for fmt in ("json", "csv", "xlsx"):
        path = tmp_path / (where + "." + fmt)
        with pytest.raises(ValueError, match="回执|审计|指纹"):
            export_result(result, fmt, path, receipt=receipt)
        assert not path.exists()
        with pytest.raises(ValueError):
            result_bytes(result, receipt, fmt)
    with pytest.raises(ValueError):
        formal_calculation_audit_payload(result, receipt)


def test_bound_transport_shapes_and_legacy_snapshot_are_distinct(tmp_path):
    calculated = envelope()
    result, receipt = calculated["result"], calculated["result_receipt"]
    for value in ({"result": result, "result_receipt": receipt}, {**result, "result_receipt": receipt}):
        actual, saved = result_transport_parts(value)
        assert actual == result and saved == receipt
        assert review_calculation(value, {"view": "summary"})["validation"] == "saved_result_fingerprint"
    with pytest.raises(ValueError):
        result_transport_parts({**result, "result": result, "result_receipt": receipt})
    for malformed in (None, "", 1, "f" * 64):
        with pytest.raises(ValueError):
            validate_result_binding(result, {"result_fingerprint": malformed})
    # Legacy calculate() snapshot carries no native proof and can still export.
    export_result(result, "json", tmp_path / "legacy.json")


@pytest.mark.parametrize("field", ["calculation_ownership_proof", "agent_execution_proof", "attribute_execution_budget",
                                  "calculation_producer_contract"])
def test_formal_proof_signal_cannot_downgrade_to_unbound_snapshot(tmp_path, field):
    result = copy.deepcopy(envelope()["result"])
    result[field] = None
    for receipt in (None, {}, {"result_fingerprint": None}):
        with pytest.raises(ValueError, match="回执|指纹"):
            export_result(result, "json", tmp_path / "invalid.json", receipt=receipt)
        assert not (tmp_path / "invalid.json").exists()


@pytest.mark.parametrize("shape", ["root", "nested", "package"])
def test_saved_result_transport_rejects_duplicate_json_fields(tmp_path, shape):
    calculated = envelope()
    result, receipt = calculated["result"], calculated["result_receipt"]
    value = ({**result, "result_receipt": receipt} if shape == "root" else
             {"result": result, "result_receipt": receipt} if shape == "nested" else
             {"calculated": calculated, "package_schema": "example"})
    raw = json.dumps(value, ensure_ascii=False)
    # A nested duplicate must not be normalized away before fingerprinting.
    raw = raw.replace('"result_fingerprint":', '"result_fingerprint": "ignored", "result_fingerprint":', 1)
    path = tmp_path / "duplicate.json"
    path.write_text(raw, encoding="utf-8")
    with pytest.raises(PlanError) as rejected:
        read_bounded_json(path, 64 * 1024 * 1024)
    assert rejected.value.reason == "invalid_review_input"


@pytest.mark.parametrize("strip_compiled_options", [False, True])
def test_new_full_producer_cannot_remove_entire_budget_family(tmp_path, strip_compiled_options):
    from genshin_app.equipment import run_plan_calc
    from genshin_app.equipment.attributes import prepare_attribute_review
    from genshin_app.equipment.delivery import validate_delivery_calculated
    from tests.test_attribute_app_sources import attribute_plan
    from tests.test_plan_mechanics import review

    database = tmp_path / "absent.db"
    plan = attribute_plan()
    review(plan, database)
    adopted = prepare_attribute_review(plan, "Explicit synthetic basis; unknown dynamic sources retained", database)
    calculated = run_plan_calc(adopted["plan"], database, capture_display=True)
    validate_delivery_calculated(calculated)
    # r13's composer adds the approved input/attribute producer contracts.
    assert calculated["report"]["composer_version"] == "1.33.0"
    result, receipt = calculated["result"], calculated["result_receipt"]
    for container in (result, receipt):
        container.pop("calculation_producer_contract")
        container.pop("attribute_execution_budget")
    if strip_compiled_options:
        calculated["report"]["mechanics_model"].pop("execution_options")
    # Even a recomputed generic hash cannot replace the required producer proof.
    receipt["result_fingerprint"] = fingerprint(result)
    with pytest.raises(PlanError, match="预算"):
        validate_delivery_calculated(calculated)
    with pytest.raises(FormulaWorkbookError, match="预算"):
        build_formula_workbook(calculated)
