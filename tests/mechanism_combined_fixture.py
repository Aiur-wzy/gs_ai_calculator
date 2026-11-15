"""Two actual source families in one tiny repo; no formal database or game oracle."""
import copy
import json

import pytest

from genshin_app.equipment import compose_equipment_plan
from genshin_db import queries
from genshin_db.runtime_queries import query_runtime
from genshin_db.semantic_store import import_bundle
from genshin_runtime.energy_trigger_identity import recipe
from tests.mechanism_source_fixtures import (  # noqa: F401
    fake_repo,
    fixture_catalog,
    fixture_qualified_catalog,
    mechanism_repo,
)
from tests.test_limited_hit_benefit_sources import benefit_plan
from tests.test_mechanics_execution import (
    APPLICATION_VERIFICATION,
    EVIDENCE,
    ability,
    action,
    hit,
)
from tests.test_mechanism_binding_review import declaration
from tests.test_runtime_object_index import build, write

# ruff: noqa: F811
ADDITION = "skill.current-atk-limited-addition"
REACTION = "passive.swirl-elemental-bonus"


@pytest.fixture
def combined_db(tmp_path, mechanism_repo):
    evidence = recipe()
    # Add only absent identities; existing complete raw records must agree.
    for entry in evidence["fixture_evidence"]["records"]:
        path, raw = entry["source_path"], entry["raw"]
        rows = json.loads((mechanism_repo / path).read_text("utf8"))
        key = "proudSkillId" if "proudSkillId" in raw else "id"
        old = next((r for r in rows if r[key] == raw[key]), None)
        if old is not None:
            assert old == raw
        else:
            rows.append(copy.deepcopy(raw))
            write(mechanism_repo, path, rows)
    for parent in evidence["parents"].values():
        path = parent["source_path"]
        rows = json.loads((mechanism_repo / path).read_text("utf8"))
        if not any(r["Default"] == parent["new_raw"] for r in rows):
            rows.append({"Default": copy.deepcopy(parent["new_raw"])})
            write(mechanism_repo, path, rows)
    for talent in evidence["fixture_evidence"]["talents"]:
        path = talent["source_path"]
        data = json.loads((mechanism_repo / path).read_text("utf8"))
        if talent["name"] in data:
            assert data[talent["name"]] == talent["raw"]
        else:
            data[talent["name"]] = copy.deepcopy(talent["raw"])
            write(mechanism_repo, path, data)
    write(mechanism_repo, "ExcelBinOutput/AttackAttenuationExcelConfigData.json", [
        {"group": "default", "resetCycle": 2.5, "durabilitySequence": [1, 0, 0], "damageSequence": [1, 1, 1]}])
    db = tmp_path / "combined.db"
    build(db, mechanism_repo, upstream_commit=evidence["commit"])
    for seed in (fixture_catalog(db), fixture_qualified_catalog(db)):
        assert import_bundle(db, seed, dry_run=False)["applied"]
    return db


@pytest.fixture
def combined_plan(combined_db, monkeypatch):
    db = combined_db
    plan = benefit_plan(db)
    plan.pop("limited_hit_benefit_sources")
    template, rotation, model = plan["manual_template"], plan["rotation_model"], plan["mechanics_model"]
    template["characters"].append({"id": "b", "level": 90, "stats": {
        "atk": 100, "em": 900, "crit_rate": 0, "crit_dmg": .5, "energy_recharge": 1}})
    prototype = hit("wind", 0, "b")
    prototype.pop("multiplier")
    prototype.pop("element")
    prototype["scaling_stat"] = "atk"
    template["hits"].extend([prototype, hit("cryo.normal", 2.5, kind="damage.normal_attack")])
    template["switches"] = [{"t": .8, "character": "b"}, {"t": 2, "character": "a"}]
    rotation["characters"].append({"id": "b", "element": "anemo", "energy_recharge": 1,
        "energy_capacity": 10, "initial_energy": 10, **EVIDENCE})
    rotation["actions"][0]["occupation_end"] = .7
    rotation["abilities"].extend([ability("wind.e", "b"), ability("switch.b", kind="action.switch"),
        ability("switch.a", "b", "action.switch"), ability("normal", kind="action.normal_attack")])
    rotation["abilities"][1]["variant"] = "tap"
    rotation["actions"].extend([
        action("to.b", "switch.b", .8, .9, switch_to="b", repeat={"mode": "once"}),
        action("wind.cast", "wind.e", 1.2, 1.8, repeat={"mode": "once"}),
        action("to.a", "switch.a", 2, 2.1, switch_to="a", repeat={"mode": "once"}),
        action("normal.cast", "normal", 2.3, 2.8, repeat={"mode": "once"})])
    model["hit_events"] = [{"hit_id": "cryo.normal", "action_id": "normal.cast", "order": 20,
        "application": {"profile_id": "cryo", "target_id": "enemy", "landed": True},
        "contact": {"kind": "profile", "profile_id": "safe"}}]
    found = query_runtime(db, {"action": "attack_metadata_profile", "character_id": 10000047, "slot": "skill", "constellation": 0})
    candidate = next(c for c in found["candidates"] if c["status"] == "ready" and c["ability_name"].endswith("EmptyJump"))
    plan["action_event_profiles"].append({"id": "wind.contact", "kind": "calibrated_single_contact_v1",
        "prototype_hit_id": "wind", "variant": "tap", "calibration": {
            "offset_seconds": .2, "precision_seconds": .01, **EVIDENCE},
        "application_profile_id": "metadata.wind", "contact_profile_id": "safe", "default_landed": True,
        "self_sampling": "current", "accepted_profile_fingerprint": None})
    plan["action_event_bindings"].append({"ability_id": "wind.e", "profile_id": "wind.contact"})
    plan["attack_metadata_sources"] = [{"action_event_profile_id": "wind.contact", "version_line": "release",
        "selection": {"character_id": 10000047, "slot": "skill", "constellation": 0},
        "candidate_id": candidate["candidate_id"], "counter_tag": "wind.skill.shared",
        "application_mode": "sequence", "verification": copy.deepcopy(APPLICATION_VERIFICATION),
        "purpose": "single_stat_damage_multiplier", **EVIDENCE}]
    version = {"version_line": "release", "upstream_commit": found["snapshot"]["upstream_commit"],
               "equipment_schema_version": 1, "built_at": "isolated"}
    # This tiny runtime fixture has no curated UI tables. Only display/panel identity is stubbed.
    monkeypatch.setattr(queries, "get_meta", lambda *a, **k: {"datasets": {"equipment": {"lines": {"release": {
        "state": "ready", "upstream_commit": version["upstream_commit"], "schema_version": 1, "built_at": "isolated"}}}}})
    monkeypatch.setattr(queries, "get_character_detail", lambda *a, **k: {"character": {
        "id": 10000047, "name": "工程风角色", "weapon_type": "WEAPON_SWORD_ONE_HAND"}})
    plan["equipment_inputs"]["b"] = {"mode": "panel", "panel_policy": "static_included", "weapon": None,
        "artifact_sets": [], "character_ref": {"db_character_id": 10000047, "version_line": "release"},
        "progression": {"ascension": 6, **EVIDENCE}, "source_refs": [{"kind": "manual", "note": "900固定精通，未含额外转换"}],
        "data_version": version}
    plan["mechanism_sources"] = []
    for ident, actor in ((ADDITION, "a"), (REACTION, "b")):
        material = query_runtime(db, {"action": "mechanism_profile", "mechanism_id": ident})
        assert material["status"] == "ready", material["issues"]
        binding = declaration(material, "source." + actor)
        binding["actor"] = actor
        if actor == "b":
            binding["calibration"] = None
        plan["mechanism_sources"].append(binding)
    # Freeze both real parent profiles before compound mechanism scope acceptance.
    parent_report = compose_equipment_plan(plan, db)
    for record in parent_report["action_event_compilations"]:
        profile = next(p for p in plan["action_event_profiles"] if p["id"] == record["profile_id"])
        profile.update(record["required_confirmation"] or {})
    return plan, db
