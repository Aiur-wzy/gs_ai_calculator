"""Independent D21 cross-layer counterexamples; DB query boundary is a test double.

The payloads are frozen genuine upstream nodes, but these tests do not certify
the production import or unknown gameplay semantics.
"""
# Imported fixtures are deliberately registered for pytest's named injection.
# ruff: noqa: F811
from __future__ import annotations

import copy
import hashlib
import sqlite3
from contextlib import closing

import pytest
from test_plan_upstream import accept, setup_runtime  # noqa: F401

from genshin_app.equipment import compose_equipment_plan, run_plan_calc
from genshin_app.equipment.execution_verification import ExecutionVerificationSession
from genshin_app.equipment.files import fingerprint
from genshin_app.equipment.schema import PlanError
from genshin_engine.runtime_compiler import compile_source
from tests.test_runtime_store import fake_repo, runtime_db, stub_fetcher  # noqa: F401


def test_real_database_reference_shape_cannot_alias_automatic_source(setup_runtime):
    db, build, bundles = setup_runtime
    plan = build(2150070)
    accept(plan, db)
    automatic = plan["effect_bindings"][0]
    compiled = compile_source(bundles["2150070"], "a", automatic["binding_id"] + ":upstream",
                              automatic["upstream_recipe"]["scenario"], 10)
    alias = copy.deepcopy(automatic)
    del alias["upstream_recipe"]
    alias.update(binding_id="separate-manual-name", owner={"kind": "manual", "note": "旧手工来源"},
                 source_refs=[{"table": "EquipAffixExcelConfigData", "key": {"affixId": 2150070},
                               "field": "paramList", "upstream_commit": "test-snapshot"}],
                 parts=copy.deepcopy(compiled["parts"]))
    source_fp = fingerprint({"owner": alias["owner"], "source_refs": alias["source_refs"]})
    alias["source_fingerprint"] = source_fp
    alias["coverage"] = {"state": "complete", "reviewed_by": "independent-review",
                         "source_fingerprint": source_fp, "note": "显式同一上游来源；应冲突"}
    plan["effect_bindings"].append(alias)
    with pytest.raises(PlanError) as exc:
        run_plan_calc(plan, db)
    assert exc.value.reason == "duplicate_delivery"


@pytest.mark.parametrize("field,value", [("energy", {"required": 0, "amount": 0}),
                                        ("cooldown", None)])
def test_actual_burst_identity_does_not_legalize_forged_resources(setup_runtime, field, value):
    db, build, _ = setup_runtime
    plan = build()
    accept(plan, db)
    plan["rotation_model"]["abilities"][0][field] = value
    if field == "energy":
        plan["rotation_model"]["events"][0].update(required=0, amount=0)
    else:
        plan["rotation_model"]["initial_cooldowns"] = []
    report = compose_equipment_plan(plan, db)
    assert report["state"] == "pending"
    assert not report["mechanics_model"]["rules"]
    assert any(i["code"] == "source_rule_mismatch" for i in report["missing_inputs"])


def test_parameter_only_update_recompiles_but_invalidates_confirmed_match(setup_runtime):
    db, build, bundles = setup_runtime
    plan = build()
    accept(plan, db)
    compiled = compose_equipment_plan(plan, db)["upstream_compilations"][0]
    rule = compiled["rules"][0]
    match = {"id": "exclude-middle", "effect_ref": {"buff_id": rule["id"], "effect_id": rule["effects"][0]["id"]},
             "reason": "独立测试明确排除一次收益", "source_refs": [{"kind": "manual"}],
             "state": "pending", "basis_fingerprint": None, "mode": "adjust", "include_hit_ids": [], "exclude_hit_ids": ["h1"]}
    plan["rotation_tuning"]["buff_hit_matches"] = [match]
    preview = compose_equipment_plan(plan, db)
    match["basis_fingerprint"] = preview["match_previews"][0]["basis_fingerprint"]
    match["state"] = "confirmed"
    first = run_plan_calc(plan, db)
    assert [h["expected"] for h in first["result"]["hits"]] == pytest.approx([990, 900, 990])
    assert compose_equipment_plan(first["plan"], db)["state"] == "ready"
    before_ack = plan["effect_bindings"][0]["upstream_recipe"]["scenario"]["accepted_assumptions_fingerprint"]
    bundles["2150071"]["param_list"][0] = .25
    bundles["2150071"]["affix"]["record"]["paramList"][0] = .25
    changed = compose_equipment_plan(first["plan"], db)
    assert changed["upstream_compilations"][0]["status"] == "ready"
    assert changed["upstream_compilations"][0]["assumptions_fingerprint"] == before_ack
    assert changed["state"] == "pending"
    assert changed["source_fingerprint"] != first["report"]["source_fingerprint"]
    assert changed["plan"]["resolved_template"] is None
    assert any(i["code"] == "dependency_stale" for i in changed["missing_inputs"])


def test_fresh_bundle_must_match_requested_equipment_identity(setup_runtime):
    db, build, bundles = setup_runtime
    plan = build()
    # A stale/wrong query result remains internally consistent, but is a different source.
    bundles["2150071"] = copy.deepcopy(bundles["2150070"])
    preview = compose_equipment_plan(plan, db)
    ready_candidate = preview["upstream_compilations"][0]
    if ready_candidate.get("assumptions_fingerprint"):
        plan["effect_bindings"][0]["upstream_recipe"]["scenario"]["accepted_assumptions_fingerprint"] = ready_candidate["assumptions_fingerprint"]
    report = compose_equipment_plan(plan, db)
    assert report["state"] == "pending"
    assert not report["upstream_compilations"][0].get("parts")


def test_burst_aliases_cannot_split_one_database_cooldown(setup_runtime):
    db, build, _ = setup_runtime
    plan = build()
    accept(plan, db)
    rotation = plan["rotation_model"]
    other_ability = copy.deepcopy(rotation["abilities"][0])
    other_ability["id"] = "q-second-name"
    other_ability["cooldown"]["group"] = "independent-q-cooldown"
    rotation["abilities"].append(other_ability)
    rotation["initial_cooldowns"].append({"character": "a", "group": "independent-q-cooldown", "ready_in": []})
    rotation["characters"][0].update(energy_capacity=100, initial_energy=100)
    other_action = copy.deepcopy(rotation["actions"][0])
    other_action.update(id="cast-second", ability_id="q-second-name", start=5, occupation_end=5.1)
    rotation["actions"].append(other_action)
    other_spend = copy.deepcopy(rotation["events"][0])
    other_spend.update(id="spend-second", action_id="cast-second", t=5)
    rotation["events"].append(other_spend)
    plan["effect_bindings"][0]["upstream_recipe"]["ability_refs"].append({"ability_id": "q-second-name", "skill_id": 10019})
    report = compose_equipment_plan(plan, db)
    assert report["state"] == "pending"
    assert not report["upstream_compilations"][0].get("rules")


def test_runtime_resolved_target_cannot_point_to_wrong_existing_kind(runtime_db):
    from genshin_db.runtime_catalog import RuntimeConfigError
    from genshin_db.runtime_queries import assemble_runtime_source
    with closing(sqlite3.connect(runtime_db)) as conn, conn:
        wrong = conn.execute("SELECT definition_id FROM runtime_definition WHERE definition_kind='excel_record' LIMIT 1").fetchone()[0]
        conn.execute("UPDATE runtime_reference SET resolved_definition_id=? WHERE target_kind='ability' AND resolution_status='resolved'", (wrong,))
    with pytest.raises(RuntimeConfigError):
        assemble_runtime_source(runtime_db, 917)


def test_runtime_mixed_global_commit_is_rejected_without_repair(runtime_db):
    from genshin_db.runtime_catalog import RuntimeConfigError
    from genshin_db.runtime_queries import assemble_runtime_source
    with closing(sqlite3.connect(runtime_db)) as conn, conn:
        conn.execute("UPDATE meta SET value='different-global-commit' WHERE key='upstream_commit:release'")
    before = hashlib.sha256(runtime_db.read_bytes()).hexdigest()
    with pytest.raises(RuntimeConfigError) as exc:
        assemble_runtime_source(runtime_db, 917)
    assert exc.value.reason == "mixed_snapshot"
    assert hashlib.sha256(runtime_db.read_bytes()).hexdigest() == before


def test_runtime_gui_rejects_malformed_action_with_structured_error(tmp_path):
    from genshin_app.gui.api import GuiApi
    result = GuiApi(tmp_path).query_runtime({"action": []})
    assert result["code"] == 1
    assert "运行资料查询失败" in result["message"]


@pytest.mark.parametrize("affix,path", [(2150071, "mechanics_model.rules[0].effects[0].value"),
                                      (2150070, "mechanics_input.buffs[0].effects[0].value")])
def test_formula_export_keeps_source_locked_and_conditional_on_first_sheet(setup_runtime, affix, path):
    from genshin_app.export import summary_rows
    from genshin_app.formula_workbook import _Projection
    db, build, _ = setup_runtime
    plan = build(affix)
    accept(plan, db)
    calculated = run_plan_calc(plan, db)
    before = copy.deepcopy(calculated)
    with ExecutionVerificationSession() as session:
        projection = _Projection(calculated, _session=session).finish()
    assert calculated == before
    generated_row = projection.rows_by_path[path]
    assert projection.inputs.rows[generated_row - 1][2].style == 5
    assert generated_row in projection.structural_rows
    atk_row = projection.rows_by_path["characters[0].stats.atk"]
    assert projection.inputs.rows[atk_row - 1][2].style == 2
    assert any("游戏语义" in str(c.value) and "假设" in str(c.value)
               for row in projection.summary.rows[:4] for c in row)
    assert any("游戏语义" in str(value) for row in summary_rows(calculated["result"]) for value in row)
    assert any(c.value == "compiled_mechanics_model" for row in projection.sources.rows for c in row)
    assert any(c.value == "upstream_compilations" for row in projection.sources.rows for c in row)


def test_two_pending_unknown_payloads_have_distinct_actual_source_fingerprints(setup_runtime):
    db, build, bundles = setup_runtime
    plan = build()
    accept(plan, db)
    source = bundles["2150071"]
    claimed = source["content_fingerprint"]
    source["abilities"][0]["payload"]["unknown_event"] = {"variant": 1}
    first = compose_equipment_plan(plan, db)
    source["abilities"][0]["payload"]["unknown_event"] = {"variant": 2}
    second = compose_equipment_plan(plan, db)
    assert source["content_fingerprint"] == claimed
    assert first["state"] == second["state"] == "pending"
    assert first["source_fingerprint"] != second["source_fingerprint"]
    for report in (first, second):
        compiled = report["upstream_compilations"][0]
        assert compiled["provenance"]["actual_bundle_fingerprint"]
        assert not compiled["parts"] and not report["mechanics_model"]["rules"]


@pytest.mark.parametrize("affix", [2150071, 2150070])
def test_saved_match_for_unavailable_automatic_source_stays_pending(setup_runtime, affix):
    from genshin_app.equipment.tuning import generated_id
    db, build, bundles = setup_runtime
    plan = build(affix)
    accept(plan, db)
    compiled = compose_equipment_plan(plan, db)["upstream_compilations"][0]
    if compiled["rules"]:
        rule = compiled["rules"][0]
        ref = {"buff_id": rule["id"], "effect_id": rule["effects"][0]["id"]}
    else:
        ref = {"buff_id": generated_id("buff", plan["effect_bindings"][0]["binding_id"], "upstream"),
               "effect_id": compiled["parts"][0]["delivery"]["effect"]["id"]}
    plan["rotation_tuning"]["buff_hit_matches"] = [{"id": "saved-missing-match", "effect_ref": ref,
        "reason": "来源后续不可用仍保留草稿", "source_refs": [{"kind": "manual"}], "state": "confirmed",
        "basis_fingerprint": "stale", "mode": "adjust", "include_hit_ids": [], "exclude_hit_ids": ["h1"]}]
    bundles[str(affix)]["abilities"][0]["payload"]["unrecognized"] = True
    report = compose_equipment_plan(plan, db)
    assert report["state"] == "pending"
    preview = next(p for p in report["match_previews"] if p["id"] == "saved-missing-match")
    assert preview["basis_fingerprint"] is None
    assert report["plan"]["rotation_tuning"] == plan["rotation_tuning"]


def test_known_manual_second_provider_cannot_stack_unique_automatic_modifier(setup_runtime):
    from test_mechanics_execution import second_character
    db, build, _ = setup_runtime
    plan = build()
    accept(plan, db)
    second_character(plan["manual_template"], plan["rotation_model"])
    plan["manual_template"]["characters"][1]["stats"]["energy_recharge"] = 1
    alias = copy.deepcopy(plan["effect_bindings"][0])
    del alias["upstream_recipe"]
    alias.update(binding_id="second-provider-manual", provider_character_id="b",
                 owner={"kind": "manual", "note": "另一提供者手工宗室四件"},
                 source_refs=[{"table": "EquipAffixExcelConfigData", "key": {"affixId": 2150071},
                               "field": "paramList", "upstream_commit": "test-snapshot"}])
    alias["parts"] = [{"part_id": "old-manual", "label": "旧手工全队攻击加成", "state": "resolved",
        "rule": {"kind": "constant", "value": .2}, "delivery": {"kind": "buff", "start": 0,
        "end": 10, "scope": "team", "target": None, "effect": {"id": "attack", "benefit_type_id": "benefit.stat_pct.atk",
        "zone": "stat_pct", "stat": "atk", "applies_to": {"mode": "all"}}},
        "conditions": [], "dependencies": {"mode": "source_only", "context_fingerprint": None, "note": "独立反例"}, "reason": None}]
    fp = fingerprint({"owner": alias["owner"], "source_refs": alias["source_refs"]})
    alias["source_fingerprint"] = fp
    alias["coverage"] = {"state": "complete", "reviewed_by": "independent-review", "source_fingerprint": fp,
                         "note": "已知同一种唯一增益由另一角色手工声明"}
    plan["effect_bindings"].append(alias)
    with pytest.raises(PlanError) as exc:
        run_plan_calc(plan, db)
    assert exc.value.reason in ("duplicate_delivery", "unsupported_unique_scope"), exc.value.details


def test_other_character_nonunique_static_source_remains_independent(setup_runtime):
    from test_mechanics_execution import second_character
    db, build, bundles = setup_runtime
    plan = build(2150070)
    accept(plan, db)
    second_character(plan["manual_template"], plan["rotation_model"])
    plan["manual_template"]["characters"][1]["stats"]["energy_recharge"] = 1
    alias = copy.deepcopy(plan["effect_bindings"][0])
    scenario = alias.pop("upstream_recipe")["scenario"]
    alias.update(binding_id="independent-static-provider", provider_character_id="b",
                 owner={"kind": "manual", "note": "另一个角色自己的分类增伤"},
                 source_refs=[{"table": "EquipAffixExcelConfigData", "key": {"affixId": 2150070}, "field": "paramList"}],
                 parts=compile_source(bundles["2150070"], "b", "independent-static-provider:upstream", scenario, 10)["parts"])
    fp = fingerprint({"owner": alias["owner"], "source_refs": alias["source_refs"]})
    alias["source_fingerprint"] = fp
    alias["coverage"] = {"state": "complete", "reviewed_by": "independent-review", "source_fingerprint": fp,
                         "note": "非唯一效果各自作用于自己的角色"}
    plan["effect_bindings"].append(alias)
    calculated = run_plan_calc(plan, db)
    assert [h["expected"] for h in calculated["result"]["hits"]] == pytest.approx([1080] * 3)
