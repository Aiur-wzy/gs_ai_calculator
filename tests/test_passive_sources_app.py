"""EC12 integration with isolated pinned raw sources, never the formal DB."""
from __future__ import annotations

import copy
import hashlib
import json
import sqlite3
from contextlib import closing
from importlib import resources

import pytest
from test_freeze_review import fresh_model
from test_mechanics_execution import (
    APPLICATION_VERIFICATION,
    EVIDENCE,
    action,
    fixture,
    hit,
)

from genshin_app.equipment import (
    compose_equipment_plan,
    enable_mechanics,
    new_plan,
    run_plan_calc,
)
from genshin_app.equipment.files import fingerprint
from genshin_app.equipment.schema import PlanError, validate_plan
from genshin_db import queries
from genshin_db.raw_store import RAW_SOURCES
from genshin_db.runtime_catalog import EXACT_FAMILIES
from genshin_db.runtime_queries import query_runtime
from genshin_db.runtime_store import build_runtime_layer
from genshin_engine.runtime_passive_benefit_compiler import (
    compile_passive_benefit_source,
    passive_benefit_compiler_caps,
)
from tests.historical_runtime_sources import (
    HISTORICAL_REVIEWED_COMMIT,
    read_source_bytes,
)

HISTORICAL_PAIR_COMMIT = HISTORICAL_REVIEWED_COMMIT


@pytest.fixture(scope="module")
def passive_attack_db(tmp_path_factory):
    """Exact historical raw fixture; never label the current f11 checkout old."""
    resource = json.loads(resources.files("genshin_runtime").joinpath("resources/passive_benefit_profiles.json").read_text("utf8"))
    assert resource["commit"] == HISTORICAL_REVIEWED_COMMIT
    work = tmp_path_factory.mktemp("passive_attack_integration")
    repo = work / "repo"
    paths = set(RAW_SOURCES) | {p for values in EXACT_FAMILIES.values() for p in values}
    paths |= {"BinOutput/Talent/Fixture.json", "BinOutput/Ability/Fixture.json", "BinOutput/Avatar/Fixture.json",
              "BinOutput/Gadget/Fixture.json", "BinOutput/AnimatorConfig/Fixture.json"}
    for rel in paths:
        p = repo / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("{}" if rel.startswith("TextMap/") or rel.startswith("BinOutput/") and not rel.startswith("BinOutput/Ability/") else "[]", "utf8")
    for source in resource["files"]:
        p = repo / source["source_path"]
        p.parent.mkdir(parents=True, exist_ok=True)
        raw = read_source_bytes(source["source_path"])
        assert hashlib.sha256(raw).hexdigest() == source["workspace_source_sha256"]
        p.write_bytes(raw)
    for rel in ("ExcelBinOutput/AvatarSkillExcelConfigData.json", "ExcelBinOutput/AttackAttenuationExcelConfigData.json"):
        (repo / rel).write_bytes(read_source_bytes(rel))
    db = work / "fixture.db"
    with closing(sqlite3.connect(db)) as conn, conn:
        conn.execute("CREATE TABLE meta(key TEXT PRIMARY KEY,value TEXT)")
    build_runtime_layer(db, repo, version_line="release", upstream_commit=resource["commit"])
    return db


def test_historical_reviewed_passive_source_compiles_without_semantic_drift(passive_attack_db):
    source = query_runtime(passive_attack_db, {"action": "passive_source_profile",
        "character_id": 10000047, "skill_depot_id": 4701, "proud_skill_id": 472201,
        "character_level": 90, "ascension": 6})
    assert source["status"] == source["source_status"] == "ready"
    assert source["source_bundle"]["source_identity"]["upstream_commit"] == HISTORICAL_PAIR_COMMIT
    assert source["entry_records"]["proud_skill"]["json_pointer"] == "/3535"

    scenario = passive_benefit_compiler_caps()["default_scenario"]
    scenario.update(target_id="enemy", origin_target_range="within_source_range",
        recipient_range="all_party_within_source_range", initial_source_effects="none",
        recipient_bonus_policy="not_included",
        stat_basis="fixed_panel_nonextra_em_equals_total_em",
        event_order_policy="declared_atomic_hit_order", status="assumed",
        source="Historical reviewed 224708 snapshot; no assertion of current f11 profile support")
    compiled = compile_passive_benefit_source(source, owner="a", source_id="historical-reviewed-pair",
                                               scenario=scenario)
    assert compiled["status"] == "ready", compiled["issues"]
    assert compiled["profile_version"] == 2
    assert {rule["timing"]["duration"] for rule in compiled["rules"]} == {8}
    assert {rule["effects"][0]["value"]["factor"] for rule in compiled["rules"]} == {
        .00039999998989515007}


@pytest.fixture
def passive_plan(passive_attack_db, monkeypatch):
    db = passive_attack_db
    q = query_runtime(db, {"action": "attack_metadata_profile", "character_id": 10000047, "slot": "skill", "constellation": 0})
    candidate = next(c for c in q["candidates"] if c["ability_name"] == "Avatar_Kazuha_ElementalArt_EmptyJump" and c["status"] == "ready")
    template, rotation, model = fixture((0,))
    template["characters"][0]["stats"].update(em=900, energy_recharge=1)
    template["characters"].append({"id": "b", "level": 90, "stats": {"atk": 100, "em": 0, "crit_rate": 0, "crit_dmg": .5, "energy_recharge": 1}})
    template["hits"][0]["scaling_stat"] = "atk"
    template["hits"].append(hit("cryo.damage", 1, "b"))
    rotation["max_cycles"] = 3
    rotation["characters"][0]["element"] = "anemo"
    rotation["characters"].append({"id": "b", "element": "cryo", "energy_recharge": 1, "energy_capacity": 10, "initial_energy": 10, **EVIDENCE})
    rotation["abilities"][0]["variant"] = "tap"
    rotation["actions"] = [action(start=0, end=.5, repeat={"mode": "once"})]
    rotation["abilities"].append({"id": "b.skill", "character": "b", "action_type_id": "action.elemental_skill", "cooldown": None, "energy": None, **EVIDENCE})
    rotation["actions"].append(action("b.cast", "b.skill", 1, 1.1, order=10, repeat={"mode": "once"}))
    model["hit_events"] = [{"hit_id": "cryo.damage", "action_id": "b.cast", "order": 11,
        "application": {"profile_id": "no.application", "target_id": "enemy", "landed": True},
        "contact": {"kind": "none", **EVIDENCE}}]
    model["elemental_model"] = fresh_model({"element": "cryo", "remaining_gu": 2, "decay_gu_per_second": .1, "source": EVIDENCE["source"]})
    model["elemental_model"]["profiles"] = [{"id": "no.application", "element": "cryo", "application": "none", **EVIDENCE}]
    plan = enable_mechanics(new_plan(template))
    plan.update(rotation_model=rotation, mechanics_model=model)
    plan["action_event_profiles"] = [{"id": "contact", "kind": "calibrated_single_contact_v1", "prototype_hit_id": "h0",
        "variant": "tap", "calibration": {"offset_seconds": .2, "precision_seconds": .01, **EVIDENCE},
        "application_profile_id": "metadata.wind", "contact_profile_id": "safe", "default_landed": True,
        "self_sampling": "current", "accepted_profile_fingerprint": None}]
    plan["action_event_bindings"] = [{"ability_id": "e", "profile_id": "contact"}]
    plan["action_event_overrides"] = []
    plan["attack_metadata_sources"] = [{"action_event_profile_id": "contact", "version_line": "release",
        "selection": {"character_id": 10000047, "slot": "skill", "constellation": 0},
        "candidate_id": candidate["candidate_id"], "counter_tag": "explicit.skill.shared",
        "application_mode": "sequence", "verification": copy.deepcopy(APPLICATION_VERIFICATION),
        "purpose": "single_stat_damage_multiplier", **EVIDENCE}]
    plan["manual_template"]["hits"][0].pop("multiplier")
    plan["manual_template"]["hits"][0].pop("element")
    version = {"version_line": "release", "upstream_commit": q["snapshot"]["upstream_commit"], "equipment_schema_version": 1, "built_at": "isolated"}
    monkeypatch.setattr(queries, "get_meta", lambda *a, **k: {"datasets": {"equipment": {"lines": {"release": {
        "state": "ready", "upstream_commit": version["upstream_commit"], "schema_version": 1, "built_at": "isolated"}}}}})
    monkeypatch.setattr(queries, "get_character_detail", lambda *a, **k: {"character": {"id": 10000047, "name": "工程提供者", "weapon_type": "WEAPON_SWORD_ONE_HAND"}})
    plan["equipment_inputs"]["a"] = {"mode": "panel", "panel_policy": "static_included", "weapon": None,
        "artifact_sets": [], "character_ref": {"db_character_id": 10000047, "version_line": "release"},
        "progression": {"ascension": 6, **EVIDENCE}, "source_refs": [{"kind": "manual", "note": "900固定精通，未含额外转换"}], "data_version": version}
    scenario = passive_benefit_compiler_caps()["default_scenario"]
    scenario.update(target_id="enemy", origin_target_range="within_source_range", recipient_range="all_party_within_source_range",
        initial_source_effects="none", recipient_bonus_policy="not_included", stat_basis="fixed_panel_nonextra_em_equals_total_em",
        event_order_policy="declared_atomic_hit_order", **EVIDENCE)
    plan["passive_sources"] = [{"id": "passive.a", "actor": "a", "proud_skill_id": 472201, "constellation": 0,
                                "scenario": scenario, "accepted_fingerprint": None}]
    return plan, db


def accept(plan, db):
    report = compose_equipment_plan(plan, db)
    plan["action_event_profiles"][0].update(report["action_event_compilations"][0]["required_confirmation"] or {})
    report = compose_equipment_plan(plan, db)
    plan["passive_sources"][0].update(report["passive_compilations"][0]["required_confirmation"] or {})
    return report


def withdrawn(report):
    assert report["state"] == "pending", report["missing_inputs"]
    record = report["passive_compilations"][0]
    assert record["status"] == "pending"
    assert all(record[k] == [] for k in ("parts", "rules", "initial_rules", "rule_ids"))
    assert not any(r["source_id"].startswith("passive.a:") for r in report["mechanics_model"]["rules"])


def test_fresh_metadata_action_and_passive_one_way_acceptance(passive_plan):
    plan, db = passive_plan
    before = copy.deepcopy(plan)
    withdrawn(compose_equipment_plan(plan, db))
    assert plan == before
    withdrawn(accept(plan, db))
    final = compose_equipment_plan(plan, db)
    assert final["state"] == "ready", final["missing_inputs"]
    assert final["action_event_compilations"][0]["profile_fingerprint"] == plan["action_event_profiles"][0]["accepted_profile_fingerprint"]
    assert len(final["mechanics_model"]["rules"]) == 2
    assert final["plan"]["mechanics_model"]["rules"] == []
    record = final["passive_compilations"][0]
    assert len(record["rules"]) == len(record["initial_rules"]) == 2
    assert record["scope_fingerprint"] == fingerprint(record["scope"])
    assert record["stat_basis"]["panel_value"] == 900


@pytest.mark.parametrize("change", ["unaccepted_parent", "missing_progression", "pending_progression", "ascension", "level", "constellation",
    "initial", "recipient", "basis", "range", "em", "em_write", "manual_wind", "stale_acceptance"])
def test_scope_changes_never_partially_deliver(passive_plan, change):
    plan, db = passive_plan
    accept(plan, db)
    if change == "unaccepted_parent":
        plan["action_event_profiles"][0]["accepted_profile_fingerprint"] = None
    elif change == "missing_progression":
        plan["equipment_inputs"]["a"].pop("progression")
    elif change == "pending_progression":
        plan["equipment_inputs"]["a"]["progression"]["status"] = "pending"
    elif change == "ascension":
        plan["equipment_inputs"]["a"]["progression"]["ascension"] = 5
    elif change == "level":
        plan["manual_template"]["characters"][0]["level"] = 80
    elif change == "constellation":
        plan["passive_sources"][0]["constellation"] = 1
    elif change in ("initial", "recipient", "basis", "range"):
        key = {"initial": "initial_source_effects", "recipient": "recipient_bonus_policy", "basis": "stat_basis", "range": "recipient_range"}[change]
        plan["passive_sources"][0]["scenario"][key] = "unknown"
    elif change == "em":
        plan["manual_template"]["characters"][0]["stats"]["em"] = 901
    elif change == "em_write":
        plan["manual_template"]["buffs"].append({"id": "late.em", "start": 8, "end": 9, "target": "a",
            "effects": [{"id": "v", "benefit_type_id": "benefit.em.all", "zone": "em", "stat": "all", "value": 100, "applies_to": {"mode": "all"}}],
            "extensions": {"owner": "a", "source_id": "manual.independent"}})
    elif change == "manual_wind":
        plan["rotation_model"]["abilities"].append({"id": "manual.skill", "character": "a", "action_type_id": "action.elemental_skill", "cooldown": None, "energy": None, **EVIDENCE})
        plan["rotation_model"]["actions"].append(action("manual.cast", "manual.skill", .6, .9, order=20, repeat={"mode": "once"}))
        plan["mechanics_model"]["elemental_model"]["profiles"].append({"id": "manual.wind", "element": "anemo", "application": "apply", "gauge_units": 1,
            "icd": {"kind": "none"}, **EVIDENCE})
        plan["mechanics_model"]["application_events"] = [{"id": "manual.app", "action_id": "manual.cast", "t": .8, "order": 21,
            "application": {"profile_id": "manual.wind", "target_id": "enemy", "landed": False}, "contact": {"kind": "none", **EVIDENCE}}]
    else:
        plan["passive_sources"][0]["accepted_fingerprint"] = "0"*64
    withdrawn(compose_equipment_plan(plan, db))


@pytest.mark.parametrize("field,value", [("constellation", True), ("proud_skill_id", True), ("accepted_fingerprint", "bad"), ("id", "a"*129)])
def test_passive_schema_rejects_invalid_before_query(passive_plan, field, value):
    plan, _ = passive_plan
    plan["passive_sources"][0][field] = value
    with pytest.raises(PlanError):
        validate_plan(plan)


def test_once_and_unrelated_panel_edit_preserve_acceptance(passive_plan):
    plan, db = passive_plan
    accept(plan, db)
    accepted = plan["passive_sources"][0]["accepted_fingerprint"]
    plan["mechanics_model"]["manual_buff_repeat"] = "once"
    plan["manual_template"]["characters"][1]["stats"]["atk"] = 200
    report = compose_equipment_plan(plan, db)
    assert report["state"] == "ready", report["missing_inputs"]
    assert report["passive_compilations"][0]["required_confirmation"]["accepted_fingerprint"] == accepted


def test_source_change_keeps_ec09_parent_fp_and_native_unmodified(passive_plan):
    plan, db = passive_plan
    accept(plan, db)
    parent = plan["action_event_profiles"][0]["accepted_profile_fingerprint"]
    plan["passive_sources"][0]["scenario"]["source"] += " 修改依据"
    before = copy.deepcopy(plan)
    report = compose_equipment_plan(plan, db)
    withdrawn(report)
    assert report["action_event_compilations"][0]["profile_fingerprint"] == parent
    assert plan == before


def test_pending_peer_withdraws_ready_rules(passive_plan):
    plan, db = passive_plan
    accept(plan, db)
    other = copy.deepcopy(plan["passive_sources"][0])
    other.update(id="second", actor="b")
    plan["passive_sources"].append(other)
    withdrawn(compose_equipment_plan(plan, db))


def test_runtime_trigger_sample_and_recipient_damage(passive_plan):
    plan, db = passive_plan
    accept(plan, db)
    plan["rotation_model"]["abilities"].append({"id": "switch.b", "character": "a", "action_type_id": "action.switch", "cooldown": None, "energy": None, **EVIDENCE})
    plan["rotation_model"]["actions"].append(action("switch", "switch.b", .6, .7, order=30, switch_to="b", repeat={"mode": "once"}))
    out = run_plan_calc(plan, db)
    damage = next(h for h in out["result"]["hits"] if h["id"] == "cryo.damage")
    assert damage["expected"] == pytest.approx(45*(1+900*.00039999998989515007))
    record = out["result"]["passive_compilations"][0]
    samples = [s for s in out["result"]["sampling_trace"] if s["rule_id"] in record["rule_ids"]]
    assert samples and all(s["sample_t"] == .2 and s["source_value"] == 900 for s in samples)
    assert out["plan"]["mechanics_model"]["rules"] == []
    assert out["result_receipt"]["passives"]["compilations_fingerprint"] == fingerprint(out["result"]["passive_compilations"])


def test_old_empty_sources_are_unchanged(tmp_path):
    template, rotation, model = fixture()
    plan = enable_mechanics(new_plan(template))
    plan.update(rotation_model=rotation, mechanics_model=model)
    first = compose_equipment_plan(plan, tmp_path / "absent.db")
    plan["passive_sources"] = []
    second = compose_equipment_plan(plan, tmp_path / "absent.db")
    assert "passive_compilations" not in first and "passive_compilations" not in second
    assert first["mechanics_model"] == second["mechanics_model"]


def test_full_receipt_budget_fails_without_native_or_partial_delivery(passive_plan, monkeypatch):
    from genshin_app.equipment import passive_sources

    plan, db = passive_plan
    accept(plan, db)
    before = copy.deepcopy(plan)
    monkeypatch.setattr(passive_sources, "MAX_RECEIPT_BYTES", 256)
    with pytest.raises(PlanError) as caught:
        compose_equipment_plan(plan, db)
    assert caught.value.reason == "passive_receipt_limit"
    assert plan == before
