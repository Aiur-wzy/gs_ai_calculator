"""Independent EC07b wiring checks with predeclared 15s / 60-energy facts."""
from __future__ import annotations

import copy

import pytest

from genshin_app.equipment import compose_equipment_plan, new_plan, run_plan_calc
from genshin_app.equipment.action_resources import compile_action_resources
from genshin_app.equipment.files import fingerprint, input_view
from genshin_app.equipment.schema import PlanError
from genshin_db import runtime_action_resource

EVIDENCE = {"status": "assumed", "source": "独立工程输入，不是人物实战结论"}
ONCE = {"mode": "once"}


def source_profile():
    fields = {"base_cooldown_seconds": 15, "base_charge_count": 1, "base_energy_cost": 60, "cost_element": "geo"}
    return {"status": "ready", "projection_version": 1,
            "snapshot": {"snapshot_id": "snapshot-a", "upstream_commit": "a" * 40, "version_line": "release"},
            "selection": {"character_id": 90000001, "skill_depot_id": 9001, "skill_id": 90013,
                          "slot": "burst", "matching_slots": ["burst"]},
            "projection": {key: {"status": "ready", "value": value, "origin": {"present": True}}
                           for key, value in fields.items()},
            "descriptions": {"status": "ready", "name": {"status": "ready", "text": "独立工程爆发"},
                             "description": {"status": "ready", "text": "基础冷却15秒，固定耗能60；普通模式工程证据。"}},
            "identity_sources": {"skill": {"source_path": "ExcelBinOutput/AvatarSkillExcelConfigData.json",
                                          "json_pointer": "/1", "node_sha256": "b" * 64}},
            "resource_fingerprint": "c" * 64}


@pytest.fixture
def source(monkeypatch):
    state = {"profile": source_profile(), "calls": []}

    def query(db, **selection):
        state["calls"].append(copy.deepcopy(selection))
        return copy.deepcopy(state["profile"])

    monkeypatch.setattr(runtime_action_resource, "get_runtime_action_resource_profile", query)
    return state


def native(second_at=15, initial=120):
    times = [0] if second_at is None else [0, second_at]
    template = {"version": 2, "form": "resolved", "id_registry_version": 1,
                "type_definitions": {"damage_types": []},
                "enemy": {"level": 90, "resistance": {"default": .1}},
                "characters": [{"id": "a", "level": 90, "stats": {
                    "atk": 100, "base_atk": 50, "em": 0, "crit_rate": 0,
                    "crit_dmg": .5, "energy_recharge": 1}}],
                "hits": [{"id": f"h{i}", "t": t+.2, "character": "a", "skill": "工程Q", "hit_name": "工程命中",
                          "damage_type_ids": ["damage.elemental_burst"], "damage_system": "classic",
                          "element": "geo", "multiplier": 1} for i, t in enumerate(times)],
                "buffs": [], "switches": [], "loop_duration": 20}
    plan = new_plan(template)
    plan.update(plan_version=3, rotation_model={"schema_version": 1, "period": 20, "max_cycles": 3,
        "initial_frontline": "a", "characters": [{"id": "a", "element": "geo", "energy_capacity": 120,
            "energy_recharge": 99, "initial_energy": initial, **EVIDENCE}],
        "abilities": [{"id": "q", "character": "a", "action_type_id": "action.elemental_burst", "variant": "普通Q",
                       "cooldown": {"group": "q", "start": "action_start", "recovery": "sequential"},
                       "energy": {}, "rules": [], **EVIDENCE}],
        "actions": [{"id": f"q{i}", "ability_id": "q", "start": t, "occupation_end": t+.1,
                     "order": 0, "repeat": ONCE, **EVIDENCE} for i, t in enumerate(times)],
        "events": [{"id": f"s{i}", "action_id": f"q{i}", "character": "a", "type": "energy_spend",
                    "t": t, "order": 1, "repeat": ONCE, **EVIDENCE} for i, t in enumerate(times)],
        "initial_cooldowns": [{"character": "a", "group": "q", "ready_in": []}]},
        mechanics_model={"schema_version": 1, "rules": [], "hit_patterns": [], "condition_events": [],
            "initial_state": {"rules": []}, "hit_events": [
                {"hit_id": f"h{i}", "action_id": f"q{i}", "order": 10} for i in range(len(times))]},
        action_resource_sources=[{"ability_id": "q", "version_line": "release",
            "selection": {"character_id": 90000001, "slot": "burst", "constellation": 0},
            "purpose": "ordinary_fixed_burst_base_resources", "variant": "普通Q", **EVIDENCE,
            "expected_snapshot_id": None, "expected_resource_fingerprint": None, "expected_scope_fingerprint": None}])
    return plan


def compile_plan(plan):
    issues = []
    rotation, records, sources = compile_action_resources(plan, "unused.db", lambda *args: issues.append(args))
    return rotation, records, sources, issues


def confirm(plan):
    _, records, _, _ = compile_plan(plan)
    for binding, record in zip(plan["action_resource_sources"], records, strict=True):
        binding.update(record["required_confirmation"])
    return plan


def test_review_compiler_never_mutates_native_and_one_binding_fills_every_spend_once(source):
    plan = confirm(native())
    before, count = copy.deepcopy(plan), len(source["calls"])
    rotation, records, sources, issues = compile_plan(plan)
    assert plan == before and len(source["calls"]) == count + 1
    assert issues == [] and records[0]["status"] == "ready" and len(sources) == 1
    assert rotation["abilities"][0]["cooldown"] == {"group": "q", "start": "action_start", "recovery": "sequential", "duration": 15, "charges": 1}
    assert rotation["abilities"][0]["energy"] == {"required": 60, "amount": 60}
    assert [(event["required"], event["amount"]) for event in rotation["events"]] == [(60, 60), (60, 60)]
    assert records[0]["interpretation_status"] == "assumed"
    assert records[0]["gameplay_interpretation"] == "declared_not_verified"
    assert len(rotation["actions"]) == len(plan["rotation_model"]["actions"]) == 2
    rotation["events"][0]["amount"] = -1
    records[0]["projection"]["base_energy_cost"]["value"] = -2
    assert plan == before and source["profile"]["projection"]["base_energy_cost"]["value"] == 60


@pytest.mark.parametrize("target,key,value", [
    ("cooldown", "duration", 0), ("cooldown", "charges", None),
    ("energy", "required", None), ("energy", "amount", 60),
    ("spend", "required", 0), ("spend", "amount", None),
])
def test_review_any_explicit_owned_number_is_duplicate_even_if_zero_null_or_equal(source, target, key, value):
    plan = native()
    obj = plan["rotation_model"]["events"][0] if target == "spend" else plan["rotation_model"]["abilities"][0][target]
    obj[key] = value
    before = copy.deepcopy(plan)
    with pytest.raises(PlanError) as exc:
        compile_plan(plan)
    assert exc.value.reason == "duplicate_delivery" and plan == before


def test_review_missing_spend_does_not_partially_fill_the_other_action(source):
    plan = native()
    plan["rotation_model"]["events"].pop()
    _, records, _, _ = compile_plan(plan)
    plan["action_resource_sources"][0].update(records[0]["required_confirmation"])
    rotation, records, sources, issues = compile_plan(plan)
    assert records[0]["status"] == "pending" and records[0]["compiled_values"] is None
    assert sources == [] and any("q1" in issue[2] for issue in issues)
    assert "duration" not in rotation["abilities"][0]["cooldown"]
    assert "amount" not in rotation["events"][0]


def test_review_resource_ownership_cannot_remove_a_bound_spend(source):
    plan = native()
    plan["mechanics_model"]["resource_ownership"] = [{"rule_id": "anything", "source_part": "part",
        "action_id": "q0", "replaces_event_ids": ["s0"]}]
    with pytest.raises(PlanError) as exc:
        compile_plan(plan)
    assert exc.value.reason == "duplicate_delivery"


@pytest.mark.parametrize("change", ["variant", "group", "mode", "evidence", "snapshot", "source"])
def test_review_source_or_scope_change_never_reuses_old_confirmations(source, change):
    plan = confirm(native())
    if change == "variant":
        plan["action_resource_sources"][0]["variant"] = "另一个明确变体"
        plan["rotation_model"]["abilities"][0]["variant"] = "另一个明确变体"
    elif change == "group":
        plan["rotation_model"]["abilities"][0]["cooldown"]["group"] = "different"
    elif change == "mode":
        plan["action_resource_sources"][0]["purpose"] = "unresolved_resource_mode"
    elif change == "evidence":
        plan["action_resource_sources"][0]["source"] += "，修改声明"
    elif change == "snapshot":
        source["profile"]["snapshot"]["snapshot_id"] = "snapshot-b"
    else:
        source["profile"]["resource_fingerprint"] = "d" * 64
        source["profile"]["projection"]["base_energy_cost"]["value"] = 70
    rotation, records, sources, issues = compile_plan(plan)
    assert records[0]["status"] == "pending" and issues and sources == []
    assert "amount" not in rotation["events"][0] and rotation["abilities"][0]["energy"] == {}


def test_review_unrelated_panel_changes_do_not_change_resource_scope(source):
    plan = confirm(native())
    scope = plan["action_resource_sources"][0]["expected_scope_fingerprint"]
    plan["manual_template"]["characters"][0]["stats"].update(atk=200, energy_recharge=2)
    rotation, records, _, issues = compile_plan(plan)
    assert not issues and records[0]["scope_fingerprint"] == scope
    assert rotation["abilities"][0]["energy"]["amount"] == 60


def test_review_actor_identity_conflict_is_not_hidden_by_explicit_coverage(source):
    plan = native()
    plan["equipment_inputs"]["a"] = {"character_ref": {"db_character_id": 90000002, "version_line": "release"}}
    with pytest.raises(PlanError) as exc:
        compile_plan(plan)
    assert exc.value.reason == "character_identity"


def test_review_unknown_manual_ability_stays_manual_but_known_alias_cannot_split_cd(source):
    plan = native()
    other = copy.deepcopy(plan["rotation_model"]["abilities"][0])
    other.update(id="manual", variant="手工技能")
    other["cooldown"].update(group="manual", duration=7, charges=1)
    other["energy"] = {"required": 12, "amount": 12}
    plan["rotation_model"]["abilities"].append(other)
    confirm(plan)
    rotation, records, _, issues = compile_plan(plan)
    assert not issues and rotation["abilities"][1] == other
    assert records[0]["identity_coverage"] == "explicit_sources_only"
    plan["effect_bindings"] = [{"binding_id": "known", "provider_character_id": "a", "owner": {"kind": "manual"},
        "upstream_recipe": {"ability_refs": [{"ability_id": "manual", "skill_id": 90013}]}}]
    with pytest.raises(PlanError) as exc:
        compile_plan(plan)
    assert exc.value.reason == "resource_alias_conflict"


def test_review_full_calculation_uses_one_compiled_model_and_preserves_native_input(source, tmp_path):
    plan = confirm(native())
    original = copy.deepcopy(plan)
    calculated = run_plan_calc(plan, tmp_path / "unused.db")
    assert plan == original and calculated["plan"]["rotation_model"] == original["rotation_model"]
    assert calculated["result_receipt"]["input_fingerprint"] == fingerprint(input_view(original))
    execution = calculated["result"]["rotation_check"]["resolved_rotation_model"]
    assert execution["abilities"][0]["energy"] == {"required": 60, "amount": 60}
    assert execution["characters"][0]["energy_recharge"] == 1  # ER bound after resource compile.
    assert calculated["report"]["compiled_rotation_model"]["characters"][0]["energy_recharge"] == 99
    assert calculated["result"]["summary"]["total_damage"] == pytest.approx(90)
    events = [e for e in calculated["result"]["rotation_check"]["event_log"] if e["type"] == "energy_spend"]
    assert len(events) == 2 and [(e["before"], e["after"]) for e in events] == [(120, 60), (60, 0)]
    assert "compiled_rotation_model" not in calculated["plan"]


def test_review_refund_and_cooldown_reset_are_retained_and_applied_once(source, tmp_path):
    plan = native(second_at=2, initial=60)
    plan["rotation_model"]["events"] += [
        {"id": "refund", "type": "energy_direct", "t": 1, "order": 1, "amounts": {"a": 60}, "repeat": ONCE, **EVIDENCE},
        {"id": "reset", "type": "cooldown_change", "t": 1, "order": 2, "character": "a", "group": "q",
         "operation": "reset", "repeat": ONCE, **EVIDENCE},
    ]
    confirm(plan)
    result = run_plan_calc(plan, tmp_path / "unused.db")
    assert result["result"]["summary"]["total_damage"] == pytest.approx(90)
    record = result["report"]["action_resource_compilations"][0]
    assert record["explicit_modifiers"] == [{"id": "refund", "type": "energy_direct"}, {"id": "reset", "type": "cooldown_change"}]
    spent = [e for e in result["result"]["rotation_check"]["event_log"] if e["type"] == "energy_spend"]
    assert [(e["before"], e["after"]) for e in spent] == [(60, 0), (60, 0)]


def test_review_cd_failure_stops_second_damage_despite_ready_database_numbers(source, tmp_path):
    plan = confirm(native(second_at=10))
    result = run_plan_calc(plan, tmp_path / "unused.db")["result"]
    assert result["summary"]["total_damage"] == pytest.approx(45)
    assert result["summary"]["dps"] is None and result["rotation_check"]["status"] == "failed"


def test_review_saved_cache_cannot_supply_missing_fresh_source(source, tmp_path):
    plan = confirm(native())
    calculated = run_plan_calc(plan, tmp_path / "unused.db")
    cached = copy.deepcopy(calculated["plan"])
    source["profile"]["snapshot"]["snapshot_id"] = "new-db-snapshot"
    report = compose_equipment_plan(cached, tmp_path / "unused.db")
    assert report["state"] == "pending" and report["plan"]["resolved_template"] is None
    assert "amount" not in report["plan"]["rotation_model"]["events"][0]
    with pytest.raises(PlanError):
        run_plan_calc(cached, tmp_path / "unused.db")


def test_review_empty_binding_keeps_manual_path_without_source_queries(source, tmp_path):
    plan = native(second_at=None, initial=60)
    plan["action_resource_sources"] = []
    plan["rotation_model"]["abilities"][0]["cooldown"].update(duration=15, charges=1)
    plan["rotation_model"]["abilities"][0]["energy"].update(required=60, amount=60)
    plan["rotation_model"]["events"][0].update(required=60, amount=60)
    result = run_plan_calc(plan, tmp_path / "unused.db")
    assert result["result"]["summary"]["total_damage"] == pytest.approx(45)
    assert source["calls"] == []


@pytest.mark.parametrize("field,value", [("t", .01), ("order", 0), ("repeat", {"mode": "each_cycle"})])
def test_review_narrow_scope_does_not_waive_actual_spend_causality(source, tmp_path, field, value):
    plan = confirm(native(second_at=None, initial=60))
    scope = plan["action_resource_sources"][0]["expected_scope_fingerprint"]
    plan["rotation_model"]["events"][0][field] = value
    report = compose_equipment_plan(plan, tmp_path / "unused.db")
    # The final contract deliberately confirms identity, not fixed action timing.
    # Changed event timing/repeat must still fail the executor's separate checks.
    assert report["action_resource_compilations"][0]["scope_fingerprint"] == scope
    with pytest.raises(PlanError) as exc:
        run_plan_calc(plan, tmp_path / "unused.db")
    assert exc.value.reason == "mechanics_pending"


def test_review_upstream_q_gate_consumes_materialized_profile_without_second_curated_read(source, monkeypatch):
    from genshin_app.equipment.upstream import _burst_actions
    from genshin_db import queries

    plan = confirm(native())
    plan["equipment_inputs"]["a"] = {"character_ref": {"db_character_id": 90000001, "version_line": "release"}}
    confirm(plan)  # Explicit identity evidence now belongs to the scope.
    rotation, records, _, issues = compile_plan(plan)
    assert not issues
    execution_plan = {**plan, "rotation_model": rotation}

    def forbidden(*args, **kwargs):
        pytest.fail("bound Q must reuse its materialized profile, not requery curated defaults")

    monkeypatch.setattr(queries, "get_character_detail", forbidden)
    binding = {"provider_character_id": "a", "binding_id": "buff",
               "upstream_recipe": {"ability_refs": [{"ability_id": "q", "skill_id": 90013}]}}
    actions, proof = _burst_actions(execution_plan, binding, "unused.db", lambda *a, **k: pytest.fail(str(a)),
                                    resource_compilations=records)
    assert actions == ["q0", "q1"]
    assert proof["action_resource_compilations"][0]["resource_fingerprint"] == "c" * 64


def test_review_nonfreeze_excel_uses_compiled_values_but_rejects_same_revision_stale_input(source, tmp_path):
    import io
    import zipfile

    from genshin_app.equipment.service import result_bytes
    from genshin_app.formula_workbook import (
        FormulaWorkbookError,
        build_formula_workbook,
        formula_workbook_support,
    )

    calculated = run_plan_calc(confirm(native()), tmp_path / "unused.db")
    support = formula_workbook_support(calculated)
    assert support["editable"]["database_action_resources"] == "regenerate_required"
    before = copy.deepcopy(calculated["plan"]["rotation_model"])
    for payload in (build_formula_workbook(calculated), result_bytes(calculated["result"], calculated["result_receipt"], "xlsx")):
        with zipfile.ZipFile(io.BytesIO(payload)) as archive:
            text = b"".join(archive.read(name) for name in archive.namelist() if name.endswith(".xml")).decode("utf8")
        assert "动作资源来源" in text and "普通模式工程证据" in text
        assert "declared_not_verified" in text and "explicit_sources_only" in text
    assert calculated["plan"]["rotation_model"] == before
    calculated["plan"]["rotation_model"]["actions"][1]["start"] = 14
    with pytest.raises(FormulaWorkbookError):
        build_formula_workbook(calculated)
