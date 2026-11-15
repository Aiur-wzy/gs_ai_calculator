"""Independent EC10 ownership, fresh-source and calibrated execution boundaries."""
from __future__ import annotations

import copy
import json
import sqlite3

import pytest
from test_freeze_review import EVIDENCE, fresh_model
from test_mechanics_execution import fixture
from test_runtime_attack_metadata import metadata_db as metadata_db  # noqa: PLC0414

from genshin_app.equipment import (
    compose_equipment_plan,
    new_plan,
    run_plan_calc,
    validate_plan,
)
from genshin_app.equipment.mechanics import enable_mechanics
from genshin_app.equipment.schema import PlanError
from genshin_db.runtime_queries import query_runtime
from tests import fixture_candidate_builder as fixture_builder
from tests.test_pipeline import fake_repo as fake_repo  # noqa: PLC0414
from tests.test_pipeline import stub_fetcher as stub_fetcher  # noqa: PLC0414

FIXTURE_SCALAR = 4.305600166320801
EXPECTED_HIT = 193.75200748443605  # 100 * 4.305600166320801 * .5 * .9, independent of projected output.


@pytest.fixture
def review_metadata_db(metadata_db):
    return metadata_db


def metadata_plan(candidate_id="independent-candidate", *, character_id=10000002, count=3):
    template, rotation, model = fixture((0,))
    template["characters"][0]["stats"].update(energy_recharge=1, crit_dmg=1)
    template["hits"][0].update(id="prototype", t=0, scaling_stat="atk")
    rotation["abilities"][0]["variant"] = "tap"
    cast = rotation["actions"][0]
    rotation["actions"] = [{**cast, "id": f"cast{i}", "start": round(.3*i, 6),
                             "occupation_end": round(.3*i + .25, 6), "repeat": {"mode": "once"}}
                            for i in range(count)]
    model.update(hit_events=[], elemental_model=fresh_model())
    model["elemental_model"]["profiles"] = []
    plan = enable_mechanics(new_plan(template))
    plan.update(rotation_model=rotation, mechanics_model=model)
    plan["manual_template"]["hits"][0].pop("element")
    plan["manual_template"]["hits"][0].pop("multiplier")
    plan["action_event_profiles"] = [{
        "id": "metadata-owner", "kind": "calibrated_single_contact_v1", "prototype_hit_id": "prototype",
        "variant": "tap", "calibration": {"offset_seconds": .2, "precision_seconds": .01,
            "status": "assumed", "source": "独立工程校准，不从原生modifier时长推断命中时点"},
        "application_profile_id": "owned-application", "contact_profile_id": "safe", "default_landed": True,
        "self_sampling": "current", "accepted_profile_fingerprint": None,
    }]
    plan["action_event_bindings"] = [{"ability_id": "e", "profile_id": "metadata-owner"}]
    plan["action_event_overrides"] = []
    plan["attack_metadata_sources"] = [{
        "action_event_profile_id": "metadata-owner", "version_line": "release",
        "selection": {"character_id": character_id, "slot": "skill", "constellation": 0},
        "candidate_id": candidate_id, "counter_tag": "explicit-review-shared-state",
        "application_mode": "sequence",
        "purpose": "single_stat_damage_multiplier", "status": "confirmed",
        "source": "独立工程夹具逐攻击段用途说明；不证明完整游戏派发",
        "verification": {"status": "confirmed", "locator": "tests/test_attack_metadata_review.py metadata_plan",
            "version": "commit 342084292ba4c3c6ec2bc34c045de4de72ae8d53", "reviewed_at": "2026-10-18",
            "component_scope": "selected candidate exact attack component and declared shared counter",
            "confirmed_fields": ["application_mode", "counter_tag", "shared_scope"]},
    }]
    return plan


def selected_plan(db, *, count=3, ability="Avatar_Ayaka_Icespine_Strike_New"):
    found = query_runtime(db, {"action": "attack_metadata_profile", "character_id": 90000001,
                               "slot": "skill", "constellation": 0})
    selected = [c for c in found["candidates"] if c["ability_name"] == ability and c["status"] == "ready"]
    assert len(selected) == 1, found["issues"]
    return metadata_plan(selected[0]["candidate_id"], character_id=90000001, count=count)


def confirm_metadata(plan, db):
    report = compose_equipment_plan(plan, db)
    for record in report["action_event_compilations"]:
        profile = next(p for p in plan["action_event_profiles"] if p["id"] == record["profile_id"])
        profile.update(record["required_confirmation"])
    return report


def confirmed_metadata_output(db):
    plan = selected_plan(db)
    confirm_metadata(plan, db)
    return run_plan_calc(plan, db)


def hit_values(output):
    return [hit["expected"] for hit in output["result"]["hits"]]


def test_review_one_parent_acceptance_materializes_three_hits_and_tax_once(review_metadata_db):
    plan = selected_plan(review_metadata_db)
    before = copy.deepcopy(plan)
    preview = compose_equipment_plan(plan, review_metadata_db)
    assert preview["state"] == "pending"
    assert preview["attack_metadata_compilations"][0]["status"] == "ready"
    assert plan == before
    with pytest.raises(PlanError):
        run_plan_calc(plan, review_metadata_db)
    confirm_metadata(plan, review_metadata_db)
    accepted_native = copy.deepcopy(plan)
    output = run_plan_calc(plan, review_metadata_db)
    assert hit_values(output) == pytest.approx([EXPECTED_HIT]*3)
    assert plan == accepted_native
    native = output["plan"]
    assert not native.get("hit_parameter_sources") and not native.get("elemental_sources")
    assert native["mechanics_model"]["elemental_model"]["profiles"] == []
    assert native["mechanics_model"]["hit_patterns"] == []
    assert not ({"element", "multiplier"} & native["manual_template"]["hits"][0].keys())
    applications = [row for row in output["result"]["elemental"]["trace"] if row["type"] == "elemental_application"]
    assert [row["allowed"] for row in applications] == [True, False, False]
    assert applications[0]["after"]["ordinary_aura"]["remaining_gu"] == pytest.approx(1.6)
    assert output["report"]["action_event_compilations"][0]["skill_coverage"] == "partial"


def test_review_metadata_ready_never_upgrades_pending_use_and_does_not_trust_native_cache(review_metadata_db):
    output = confirmed_metadata_output(review_metadata_db)
    plan = copy.deepcopy(output["plan"])
    plan["resolved_template"]["hits"][0]["multiplier"] = 999
    plan["resolution"]["attack_metadata_compilations"] = [{"status": "ready", "projection": {"element": {"value": "hydro"}}}]
    fresh = run_plan_calc(plan, review_metadata_db)
    assert hit_values(fresh) == pytest.approx([EXPECTED_HIT]*3)
    plan["attack_metadata_sources"][0]["status"] = "pending"
    report = compose_equipment_plan(plan, review_metadata_db)
    assert report["state"] == "pending" and report["plan"]["resolved_template"] is None
    assert report["plan"]["attack_metadata_sources"][0]["status"] == "pending"
    with pytest.raises(PlanError):
        run_plan_calc(plan, review_metadata_db)


def test_review_metadata_materialization_is_fresh_but_execution_has_no_late_sqlite(review_metadata_db, monkeypatch):
    from genshin_app.equipment import service
    from genshin_db.equipment_queries import borrow_read_connection

    plan = selected_plan(review_metadata_db)
    confirm_metadata(plan, review_metadata_db)
    compose = service.compose_equipment_plan

    def frozen(*args, **kwargs):
        report = compose(*args, **kwargs)
        assert borrow_read_connection(review_metadata_db) is not None
        monkeypatch.setattr(sqlite3, "connect", lambda *a, **k: pytest.fail("late DB connection after compose"))
        monkeypatch.setattr(sqlite3.dbapi2, "connect", lambda *a, **k: pytest.fail("late DB connection after compose"))
        return report

    monkeypatch.setattr(service, "compose_equipment_plan", frozen)
    output = service.run_plan_calc(plan, review_metadata_db)
    assert borrow_read_connection(review_metadata_db) is None
    assert hit_values(output) == pytest.approx([EXPECTED_HIT]*3)


def test_review_local_aliases_and_panel_changes_keep_metadata_acceptance_portable(review_metadata_db):
    plan = selected_plan(review_metadata_db)
    confirm_metadata(plan, review_metadata_db)
    old_acceptance = plan["action_event_profiles"][0]["accepted_profile_fingerprint"]
    actor, hit, rotation = plan["manual_template"]["characters"][0], plan["manual_template"]["hits"][0], plan["rotation_model"]
    actor.update(id="renamed-actor")
    actor["stats"]["atk"] = 200
    hit.update(id="renamed-prototype", character="renamed-actor", hit_name="中文显示名")
    rotation["characters"][0]["id"] = rotation["initial_frontline"] = "renamed-actor"
    rotation["abilities"][0].update(id="renamed-e", character="renamed-actor")
    for action in rotation["actions"]:
        action["ability_id"] = "renamed-e"
    profile = plan["action_event_profiles"][0]
    profile.update(id="renamed-profile", prototype_hit_id="renamed-prototype", application_profile_id="renamed-application")
    plan["action_event_bindings"][0].update(profile_id="renamed-profile", ability_id="renamed-e")
    plan["attack_metadata_sources"][0]["action_event_profile_id"] = "renamed-profile"
    output = run_plan_calc(plan, review_metadata_db)
    assert output["report"]["action_event_compilations"][0]["profile_fingerprint"] == old_acceptance
    assert hit_values(output) == pytest.approx([EXPECTED_HIT*2]*3)
    assert output["plan"]["mechanics_model"]["elemental_model"]["profiles"] == []


def test_review_unit_contract_change_revokes_parent_acceptance_even_when_numbers_match(review_metadata_db, monkeypatch):
    from genshin_db import runtime_attack_metadata
    from genshin_db.runtime_catalog import fingerprint

    plan = selected_plan(review_metadata_db)
    confirm_metadata(plan, review_metadata_db)
    recipe = copy.deepcopy(runtime_attack_metadata._recipe())
    recipe["unit_contract"]["independent_reference_revision"] = "changed-with-same-divisor"
    recipe["unit_fingerprint"] = fingerprint(recipe["unit_contract"])
    # Recipe identity independently changes; no output is accepted merely because GU remains two.
    recipe["recipe_fingerprint"] = fingerprint({k: v for k, v in recipe.items()
                                              if k not in {"recipe_fingerprint", "unit_fingerprint"}})
    monkeypatch.setattr(runtime_attack_metadata, "_recipe", lambda: recipe)
    report = compose_equipment_plan(plan, review_metadata_db)
    assert report["attack_metadata_compilations"][0]["status"] == "ready"
    assert report["state"] == "pending"
    assert report["action_event_compilations"][0]["profile_fingerprint"] != plan["action_event_profiles"][0]["accepted_profile_fingerprint"]
    with pytest.raises(PlanError):
        run_plan_calc(plan, review_metadata_db)


def test_review_two_candidates_share_one_read_context_but_next_compose_reads_fresh(review_metadata_db, monkeypatch):
    from genshin_db import runtime_action_parameter, runtime_attenuation
    from genshin_db.equipment_queries import borrow_read_connection

    plan = selected_plan(review_metadata_db, count=2)
    other = selected_plan(review_metadata_db, ability="Avatar_Kazuha_ElementalArt_FlyAway")
    hit = copy.deepcopy(plan["manual_template"]["hits"][0])
    hit["id"] = "second-prototype"
    plan["manual_template"]["hits"].append(hit)
    profile = copy.deepcopy(plan["action_event_profiles"][0])
    profile.update(id="second-profile", prototype_hit_id=hit["id"], application_profile_id="second-application", variant="hold")
    plan["action_event_profiles"].append(profile)
    ability = copy.deepcopy(plan["rotation_model"]["abilities"][0])
    ability.update(id="second-e", variant="hold")
    plan["rotation_model"]["abilities"].append(ability)
    plan["rotation_model"]["actions"][1]["ability_id"] = "second-e"
    plan["action_event_bindings"].append({"ability_id": "second-e", "profile_id": "second-profile"})
    source = copy.deepcopy(other["attack_metadata_sources"][0])
    source["action_event_profile_id"] = "second-profile"
    plan["attack_metadata_sources"].append(source)
    counts, connections = {"parameter": 0, "attenuation": 0}, []
    parameter, attenuation = runtime_action_parameter.get_action_parameter_context, runtime_attenuation.get_runtime_attenuation_profile

    def observe(kind, function):
        def wrapped(*args, **kwargs):
            counts[kind] += 1
            borrowed = borrow_read_connection(review_metadata_db)
            assert borrowed is not None
            connections.append(id(borrowed.connection))
            return function(*args, **kwargs)
        return wrapped

    monkeypatch.setattr(runtime_action_parameter, "get_action_parameter_context", observe("parameter", parameter))
    monkeypatch.setattr(runtime_attenuation, "get_runtime_attenuation_profile", observe("attenuation", attenuation))
    for run in (1, 2):
        connections.clear()
        report = compose_equipment_plan(plan, review_metadata_db)
        assert all(r["status"] == "ready" for r in report["attack_metadata_compilations"])
        assert len(report["attack_metadata_compilations"]) == 2
        assert counts == {"parameter": run, "attenuation": run}
        assert len(set(connections)) == 1
        assert borrow_read_connection(review_metadata_db) is None


def test_review_source_parent_change_revokes_old_acceptance_without_changing_damage_scalar(review_metadata_db, fake_repo):
    from test_runtime_attack_metadata import SOURCE


    plan = selected_plan(review_metadata_db)
    initial = confirm_metadata(plan, review_metadata_db)
    old_source = initial["attack_metadata_compilations"][0]["source_fingerprint"]
    untouched = copy.deepcopy(plan)
    source_path = fake_repo / SOURCE
    raw = json.loads(source_path.read_text("utf8"))
    ability = next(row["Default"] for row in raw if row["Default"]["IIBMNPFLNMJ"] == "Avatar_Ayaka_Icespine_Strike_New")
    ability["GPPBBDEICKP"]["Icespine_Strike_Delay"]["JPEAFDFPKAN"] = .6
    source_path.write_text(json.dumps(raw, ensure_ascii=False), "utf8")
    fixture_builder.build_database(review_metadata_db.parent, force=True)
    changed = compose_equipment_plan(plan, review_metadata_db)
    metadata = changed["attack_metadata_compilations"][0]
    assert metadata["status"] == "ready"
    assert metadata["source_fingerprint"] != old_source
    assert metadata["projection"]["parameter"]["value"] == FIXTURE_SCALAR
    assert changed["state"] == "pending" and plan == untouched
    with pytest.raises(PlanError):
        run_plan_calc(plan, review_metadata_db)
    confirm_metadata(plan, review_metadata_db)
    assert hit_values(run_plan_calc(plan, review_metadata_db)) == pytest.approx([EXPECTED_HIT]*3)


@pytest.mark.parametrize("location", ["root", "sibling-mixin"])
def test_review_unknown_whole_ability_controls_cannot_hide_behind_unchanged_attack_leaf(review_metadata_db, fake_repo, location):
    from test_runtime_attack_metadata import SOURCE


    plan = selected_plan(review_metadata_db)
    selected = plan["attack_metadata_sources"][0]
    source_path = fake_repo / SOURCE
    raw = json.loads(source_path.read_text("utf8"))
    ability = next(row["Default"] for row in raw if row["Default"]["IIBMNPFLNMJ"] == "Avatar_Ayaka_Icespine_Strike_New")
    if location == "root":
        ability["independentUnknownOwnerControl"] = {"elementOverride": "Water"}
    else:
        ability["FBHJJNMDIPC"] = [{"$type": "IndependentUnknownOwnerElementModifier", "element": "Water"}]
    source_path.write_text(json.dumps(raw, ensure_ascii=False), "utf8")
    fixture_builder.build_database(review_metadata_db.parent, force=True)
    result = query_runtime(review_metadata_db, {"action": "attack_metadata_profile",
        "candidate_id": selected["candidate_id"], **selected["selection"]})
    assert result["status"] == "pending"
    assert result["selected_candidate"]["status"] == "pending"
    assert result["selected_candidate"]["projection"]["attenuation"]["profile"] is None


def test_review_candidate_from_another_character_cannot_fall_back_to_an_equal_scalar(review_metadata_db, fake_repo):
    from test_runtime_attack_metadata import TALENT


    original = selected_plan(review_metadata_db)["attack_metadata_sources"][0]["candidate_id"]

    def append_clone(path, fields):
        target = fake_repo / path
        rows = json.loads(target.read_text("utf8"))
        row = copy.deepcopy(rows[-1] if "AvatarSkillExcel" in path else rows[0])
        row.update(fields)
        rows.append(row)
        target.write_text(json.dumps(rows, ensure_ascii=False), "utf8")

    append_clone("ExcelBinOutput/AvatarExcelConfigData.json", {"id": 90000002, "skillDepotId": 902})
    depot_path = fake_repo / "ExcelBinOutput/AvatarSkillDepotExcelConfigData.json"
    depot = json.loads(depot_path.read_text("utf8"))[0]
    skills = list(depot["skills"])
    skills[1] = 99013
    append_clone("ExcelBinOutput/AvatarSkillDepotExcelConfigData.json", {"id": 902, "skills": skills})
    append_clone("ExcelBinOutput/AvatarSkillExcelConfigData.json", {"id": 99013, "proudSkillGroupId": 993})
    append_clone("ExcelBinOutput/ProudSkillExcelConfigData.json", {
        "proudSkillId": 99310, "proudSkillGroupId": 993, "openConfig": "OtherMetadataSkill"})
    talent_path = fake_repo / TALENT
    talents = json.loads(talent_path.read_text("utf8"))
    talents["OtherMetadataSkill"] = [{"$type": "BIAHEMMGABC", "GBPFBNHLOFD": True,
        "IIBMNPFLNMJ": "Avatar_Kazuha_ElementalArt_FlyAway", "PCABAADALND": "%1",
        "PEOJKHKHAKL": "ElementalArt_FlyAway_Damage"}]
    talent_path.write_text(json.dumps(talents, ensure_ascii=False), "utf8")
    fixture_builder.build_database(review_metadata_db.parent, force=True)
    request = {"action": "attack_metadata_profile", "character_id": 90000002, "slot": "skill", "constellation": 0}
    alternatives = query_runtime(review_metadata_db, request)
    assert any(c["status"] == "ready" and c["projection"]["parameter"]["value"] == FIXTURE_SCALAR
               for c in alternatives["candidates"])
    mistaken = query_runtime(review_metadata_db, {**request, "candidate_id": original})
    assert mistaken["status"] == "pending" and mistaken["selected_candidate"] is None
    assert any(issue["code"] == "attack_candidate_not_found" for issue in mistaken["issues"])


def test_review_metadata_owned_missing_fields_are_a_narrow_native_draft_exception():
    plan = metadata_plan(candidate_id=None)
    original = copy.deepcopy(plan)
    validate_plan(plan)
    assert plan == original
    assert plan["mechanics_model"]["elemental_model"]["profiles"] == []
    plan.pop("attack_metadata_sources")
    with pytest.raises(PlanError):
        validate_plan(plan)


@pytest.mark.parametrize("field,value", [("element", "cryo"), ("element", None),
                                         ("multiplier", 0), ("multiplier", None), ("multiplier", 1.25)])
def test_review_metadata_owned_fields_reject_equal_zero_or_null_manual_delivery(field, value):
    plan = metadata_plan()
    plan["manual_template"]["hits"][0][field] = value
    with pytest.raises(PlanError):
        validate_plan(plan)


@pytest.mark.parametrize("collision", ["native-application", "parameter-source", "elemental-source",
                                       "other-profile", "manual-application", "duplicate-source"])
def test_review_metadata_ownership_checks_real_targets_not_only_source_ids(collision):
    plan = metadata_plan()
    if collision == "native-application":
        plan["mechanics_model"]["elemental_model"]["profiles"] = [{
            "id": "owned-application", "element": "cryo", "application": "apply", "gauge_units": 2,
            "icd": {"kind": "unknown"}, **EVIDENCE}]
    elif collision == "parameter-source":
        plan["hit_parameter_sources"] = [{"hit_id": "prototype", "version_line": "release",
            "selection": {"character_id": 10000002, "slot": "skill", "constellation": 0},
            "param_index": 0, "scaling_stat": "atk", "purpose": "single_stat_damage_multiplier",
            **EVIDENCE, "expected_snapshot_id": None, "expected_node_sha256": None}]
    elif collision == "elemental-source":
        plan["elemental_sources"] = [{"profile_id": "owned-application", "version_line": "release",
            "application_mode": "sequence", "rule_group": "default", "counter_tag": "explicit-review-shared-state",
            "source": "duplicate ordinary source", "expected_snapshot_id": None, "expected_node_sha256": None}]
    elif collision == "other-profile":
        other = copy.deepcopy(plan["action_event_profiles"][0])
        other.update(id="unowned-profile", prototype_hit_id="manual-hit")
        plan["action_event_profiles"].append(other)
        hit = copy.deepcopy(plan["manual_template"]["hits"][0])
        hit.update(id="manual-hit", element="cryo", multiplier=1)
        plan["manual_template"]["hits"].append(hit)
    elif collision == "manual-application":
        plan["mechanics_model"]["application_events"] = [{
            "id": "borrowed-application", "action_id": "cast0", "t": .2, "order": 4,
            "application": {"profile_id": "owned-application", "target_id": "enemy", "landed": True},
            "contact": {"kind": "profile", "profile_id": "safe"}}]
    else:
        plan["attack_metadata_sources"].append(copy.deepcopy(plan["attack_metadata_sources"][0]))
    with pytest.raises(PlanError):
        validate_plan(plan)


@pytest.mark.parametrize("field,value", [("candidate_id", "x"*129), ("candidate_id", True),
                                         ("source", "  "), ("counter_tag", "  "),
                                         ("extra_untrusted_context", {"ready": True})])
def test_review_new_native_source_fields_use_actual_strict_validator(field, value):
    plan = metadata_plan()
    plan["attack_metadata_sources"][0][field] = value
    with pytest.raises(PlanError):
        validate_plan(plan)


@pytest.mark.parametrize("change", ["public-null-candidate", "boolean-character", "boolean-candidate", "oversize-candidate"])
def test_review_public_selection_errors_do_not_open_a_database(tmp_path, monkeypatch, change):
    from genshin_db import runtime_queries

    request = {"action": "attack_metadata_profile", "character_id": 10000002,
               "slot": "skill", "constellation": 0}
    if change == "public-null-candidate":
        request["candidate_id"] = None  # Public runtime omits it for discovery; native null remains legal.
    elif change == "boolean-character":
        request["character_id"] = True
    elif change == "boolean-candidate":
        request["candidate_id"] = True
    else:
        request["candidate_id"] = "x" * 129
    monkeypatch.setattr(runtime_queries, "_read", lambda *a, **k: pytest.fail("invalid selection reached DB read"))
    with pytest.raises(ValueError):
        runtime_queries.query_runtime(tmp_path / "must-not-be-created.db", request)
    assert not (tmp_path / "must-not-be-created.db").exists()
