"""Native integration tests: actual upstream payloads and independent damage expectations."""
from __future__ import annotations

import copy
import json
import sqlite3
from pathlib import Path

import pytest
from test_mechanics_execution import fixture

from genshin_app.equipment import (
    compose_equipment_plan,
    new_plan,
    run_plan_calc,
    validate_plan,
)
from genshin_app.equipment.execution_verification import ExecutionVerificationSession
from genshin_app.equipment.files import fingerprint, input_view, read_plan
from genshin_app.equipment.mechanics import enable_mechanics
from genshin_app.equipment.schema import PlanError
from genshin_engine.runtime_compiler import compiler_caps


@pytest.fixture
def setup_runtime(tmp_path, monkeypatch):
    from genshin_db import queries, runtime_queries
    db = tmp_path / "genshin.db"
    sqlite3.connect(db).close()
    bundles = json.loads((Path(__file__).parent / "fixtures/runtime-source-bundles.json").read_text(encoding="utf-8"))["sources"]
    version = {"version_line": "release", "upstream_commit": bundles["2150071"]["source"]["upstream_commit"],
               "equipment_schema_version": 1, "built_at": "test-build"}
    monkeypatch.setattr(queries, "get_meta", lambda *a, **k: {"datasets": {"equipment": {"lines": {
        "release": {"state": "ready", "upstream_commit": version["upstream_commit"], "schema_version": 1, "built_at": "test-build"}}}}})
    monkeypatch.setattr(queries, "get_character_detail", lambda *a, **k: {
        "character": {"id": 10000002, "name": "神里绫华"},
        "skills": [{"id": 10019, "slot": "burst", "energy_cost": 10, "cooldown": 20, "max_charge_num": 1},
                   {"id": 10018, "slot": "skill"}], "annotations": []})
    monkeypatch.setattr(queries, "get_artifact_set_detail", lambda *a, **k: {
        "artifact_set": {"name": "受测来源"}, "effects": [{"required_count": 4,
        "effect": {"affix_id": int(a[1]), "name": "真实上游配置单项", "source_refs": [{"kind": "database"}]}}],
        "annotations": []})
    monkeypatch.setattr(runtime_queries, "assemble_runtime_source", lambda path, affix, line: copy.deepcopy(bundles[str(affix)]))

    def build(affix=2150071):
        version["upstream_commit"] = bundles["2150071"]["source"]["upstream_commit"]
        template, rotation, model = fixture()
        template["characters"][0]["stats"].update(atk=2000, base_atk=1000, energy_recharge=1)
        plan = enable_mechanics(new_plan(template))
        plan["rotation_model"], plan["mechanics_model"] = rotation, model
        rotation["abilities"][0]["action_type_id"] = "action.elemental_burst"
        rotation["abilities"][0]["id"] = "q"
        rotation["abilities"][0]["energy"] = {"required": 10, "amount": 10}
        rotation["abilities"][0]["cooldown"] = {"group": "q", "duration": 20, "charges": 1,
            "start": "action_start", "recovery": "sequential"}
        rotation["initial_cooldowns"] = [{"character": "a", "group": "q", "ready_in": []}]
        rotation["actions"][0]["ability_id"] = "q"
        rotation["actions"][0]["repeat"] = {"mode": "once"}
        rotation["events"] = [{"id": "spend", "type": "energy_spend", "t": 0, "order": 1,
            "action_id": "cast", "character": "a", "required": 10, "amount": 10,
            "repeat": {"mode": "once"}, "status": "assumed", "source": "显式工程能量门槛"}]
        for h in template["hits"]:
            h["damage_type_ids"] = ["damage.elemental_burst"]
        plan["manual_template"] = template
        plan["equipment_inputs"] = {"a": {"mode": "panel", "panel_policy": "static_included",
            "character_ref": {"db_character_id": 10000002, "version_line": "release"},
            "weapon": None, "artifact_sets": [{"set_id": affix, "count": 4}],
            "source_refs": [{"kind": "manual", "note": "测试此单项来源，其他属性给定；分类增伤未含"}],
            "data_version": copy.deepcopy(version)}}
        req = compose_equipment_plan(plan, db)["effect_requirements"][0]
        binding = copy.deepcopy(req["proposed_binding"])
        scenario = compiler_caps()["default_scenario"]
        scenario["static_policy"] = "execute"
        binding["upstream_recipe"] = {"schema_version": 1, "scenario": scenario,
                                       "ability_refs": [{"ability_id": "q", "skill_id": 10019}]}
        plan["effect_bindings"] = [binding]
        return plan
    return db, build, bundles


def accept(plan, db):
    preview = compose_equipment_plan(plan, db)
    compiled = preview["upstream_compilations"][0]
    plan["effect_bindings"][0]["upstream_recipe"]["scenario"]["accepted_assumptions_fingerprint"] = compiled["assumptions_fingerprint"]
    return preview


def test_auto_source_recompiles_and_preserves_original_across_save_and_forged_cache(setup_runtime, tmp_path):
    db, build, _ = setup_runtime
    plan = build()
    assert accept(plan, db)["state"] == "pending"
    before = copy.deepcopy(plan)
    out = run_plan_calc(plan, db)
    assert plan == before
    assert out["plan"]["effect_bindings"][0]["parts"] == []
    assert out["plan"]["mechanics_model"]["rules"] == []
    assert len(out["report"]["mechanics_model"]["rules"]) == 1
    assert out["result_receipt"]["input_fingerprint"] == fingerprint(input_view(plan))
    assert [h["expected"] for h in out["result"]["hits"]] == pytest.approx([990] * 3)
    assert out["equipment_summary"]["characters"][0]["effect_statuses"][0]["automatic"]
    assert out["result"]["upstream_semantics"]["conditional_semantics"] is True
    assert out["report"]["mechanics_input_template"]["buffs"] == []
    saved = tmp_path / "plan.json"
    stale = out["plan"]
    stale["resolved_template"]["characters"][0]["stats"]["atk"] = 999999
    saved.write_text(json.dumps(stale), encoding="utf-8")
    assert run_plan_calc(read_plan(saved)["plan"], db)["result"] == out["result"]


@pytest.mark.parametrize("affix,expected", [(2150070, 1080), (2150320, 900), (2150390, 900)])
def test_static_filter_runs_only_matching_class(setup_runtime, affix, expected):
    db, build, _ = setup_runtime
    plan = build(affix)
    accept(plan, db)
    out = run_plan_calc(plan, db)
    assert [h["expected"] for h in out["result"]["hits"]] == pytest.approx([expected] * 3)
    assert out["plan"]["manual_template"]["buffs"] == []
    assert out["report"]["mechanics_input_template"]["buffs"]


def test_given_panel_cannot_silently_swallow_type_specific_bonus(setup_runtime):
    db, build, _ = setup_runtime
    plan = build(2150070)
    plan["effect_bindings"][0]["upstream_recipe"]["scenario"]["static_policy"] = "included"
    out = compose_equipment_plan(plan, db)
    assert out["state"] == "pending"
    assert not out["upstream_compilations"][0]["parts"]


def test_real_burst_identity_required_and_forged_skill_is_pending(setup_runtime):
    db, build, _ = setup_runtime
    plan = build()
    accept(plan, db)
    plan["effect_bindings"][0]["upstream_recipe"]["ability_refs"][0]["skill_id"] = 10018
    report = compose_equipment_plan(plan, db)
    assert report["state"] == "pending"
    assert not report["mechanics_model"]["rules"]
    assert any("爆发技能" in i["message"] for i in report["missing_inputs"])


def test_source_change_unknown_or_unaccepted_profile_never_reuses_old_output(setup_runtime):
    db, build, bundles = setup_runtime
    plan = build()
    accept(plan, db)
    first = run_plan_calc(plan, db)
    bundles["2150071"]["abilities"][0]["payload"]["newUnknownMechanic"] = True
    preview = compose_equipment_plan(first["plan"], db)
    assert preview["state"] == "pending"
    assert preview["plan"]["resolved_template"] is None
    assert not preview["mechanics_model"]["rules"]
    assert preview["source_fingerprint"] != first["report"]["source_fingerprint"]


def test_source_unselected_is_inactive_and_withdraws_rules(setup_runtime):
    db, build, _ = setup_runtime
    plan = build()
    accept(plan, db)
    plan["equipment_inputs"]["a"]["artifact_sets"] = []
    out = run_plan_calc(plan, db)
    assert out["report"]["upstream_compilations"][0]["status"] == "inactive"
    assert not out["report"]["mechanics_model"]["rules"]
    assert [h["expected"] for h in out["result"]["hits"]] == pytest.approx([900] * 3)


def test_old_versions_and_client_generated_parts_rejected(setup_runtime):
    db, build, _ = setup_runtime
    plan = build()
    plan["plan_version"] = 2
    del plan["mechanics_model"]
    with pytest.raises(PlanError, match="上游自动来源"):
        validate_plan(plan)
    plan = build()
    accept(plan, db)
    plan["effect_bindings"][0]["parts"] = run_plan_calc(plan, db)["report"]["upstream_compilations"][0]["parts"]
    with pytest.raises(PlanError, match="parts必须为空"):
        validate_plan(plan)


def test_normal_gui_bridge_and_workbook_keep_conditional_semantics(setup_runtime, tmp_path):
    from genshin_app.formula_workbook import _Projection
    from genshin_app.gui.api import GuiApi

    db, build, _ = setup_runtime
    plan = build()
    accept(plan, db)
    out = GuiApi(tmp_path).run_plan_calc(plan)
    assert out["code"] == 0, out
    calculated = out["data"]
    with ExecutionVerificationSession() as session:
        book = _Projection(calculated, _session=session).finish()
    assert any("游戏语义未完全验证" in str(c.value) for row in book.sources.rows for c in row)
    assert any("compiled_mechanics_model" == c.value for row in book.sources.rows for c in row)


@pytest.fixture
def setup_mapped_runtime(setup_runtime):
    from test_runtime_mapped_catalog_compiler import obfuscate

    from genshin_runtime.normalization import for_commit

    db, build, bundles = setup_runtime
    commit = "342084292ba4c3c6ec2bc34c045de4de72ae8d53"
    profile = for_commit(commit)
    assert profile is not None
    fields = {canonical: raw for raw, canonical in profile.fields.items()}
    types = {canonical: raw for raw, canonical in profile.types.items()}
    for bundle in bundles.values():
        bundle["source"]["upstream_commit"] = bundle["snapshot"]["upstream_commit"] = commit
        bundle["talent"]["operations"] = obfuscate(bundle["talent"]["operations"], fields, types)
        for ability in bundle["abilities"]:
            ability["payload"] = obfuscate(ability["payload"], fields, types)
        bundle["normalization"] = profile.receipt(commit)
    assert "modifiers" not in bundles["2150071"]["abilities"][0]["payload"]
    return db, build, bundles


def _second_provider(plan, *, automatic):
    from test_mechanics_execution import second_character

    second_character(plan["manual_template"], plan["rotation_model"])
    plan["manual_template"]["characters"][1]["stats"]["energy_recharge"] = 1
    alias = copy.deepcopy(plan["effect_bindings"][0])
    alias.update(binding_id="second-provider", provider_character_id="b")
    if automatic:
        plan["equipment_inputs"]["b"] = copy.deepcopy(plan["equipment_inputs"]["a"])
        ability = copy.deepcopy(plan["rotation_model"]["abilities"][0])
        ability.update(id="bq", character="b")
        ability["cooldown"]["group"] = "bq"
        plan["rotation_model"]["abilities"].append(ability)
        action = copy.deepcopy(plan["rotation_model"]["actions"][0])
        action.update(id="bcast", ability_id="bq", start=1, occupation_end=1.1, order=20)
        plan["rotation_model"]["actions"].append(action)
        plan["rotation_model"]["initial_cooldowns"].append({"character": "b", "group": "bq", "ready_in": []})
        alias["upstream_recipe"]["ability_refs"] = [{"ability_id": "bq", "skill_id": 10019}]
    else:
        del alias["upstream_recipe"]
        alias["owner"] = {"kind": "manual", "note": "另一提供者手工声明同源唯一效果"}
        alias["source_refs"] = [{"table": "EquipAffixExcelConfigData", "key": {"affixId": 2150071},
                                 "version_line": "release"}]
    plan["effect_bindings"].append(alias)
    return alias


def test_mapped_single_provider_uses_fresh_unique_keys_and_preserves_damage(setup_mapped_runtime):
    db, build, bundles = setup_mapped_runtime
    plan = build()
    accept(plan, db)
    before = copy.deepcopy((plan, bundles))
    out = run_plan_calc(plan, db)
    compiled = out["report"]["upstream_compilations"][0]
    assert compiled["status"] == "ready"
    assert compiled["unique_keys"] == ["UNIQUE_Relic_TeamAtkupAfterElementalBurst"]
    assert [h["expected"] for h in out["result"]["hits"]] == pytest.approx([990] * 3)
    assert (plan, bundles) == before


@pytest.mark.parametrize("automatic", [True, False], ids=["two-automatic", "automatic-and-manual"])
@pytest.mark.parametrize("forged_cache", [False, True], ids=["fresh", "forged-saved-report"])
def test_mapped_cross_provider_unique_conflict_cannot_use_saved_reports(setup_mapped_runtime, automatic, forged_cache):
    db, build, _ = setup_mapped_runtime
    plan = build()
    accept(plan, db)
    _second_provider(plan, automatic=automatic)
    if forged_cache:
        plan["resolution"] = {"state": "ready", "upstream_compilations": [
            {"binding_id": b["binding_id"], "status": "ready", "unique_keys": [], "issues": []}
            for b in plan["effect_bindings"]]}
    before = copy.deepcopy(plan)
    with pytest.raises(PlanError) as exc:
        compose_equipment_plan(plan, db)
    assert exc.value.reason == "unsupported_unique_scope", exc.value.details
    assert set(exc.value.details["bindings"]) == {b["binding_id"] for b in plan["effect_bindings"]}
    assert exc.value.details["modifier"] == "UNIQUE_Relic_TeamAtkupAfterElementalBurst"
    assert plan == before
