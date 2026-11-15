"""Generic App wiring with real reviewed fixture data and actual parent compilation."""
import copy
import sqlite3
from contextlib import closing

import pytest

from genshin_app.equipment import compose_equipment_plan, run_plan_calc
from genshin_app.equipment.files import fingerprint, input_view
from genshin_app.equipment.mechanism_source_schema import DEFAULT_SOURCE
from genshin_app.equipment.schema import PlanError
from genshin_db.semantic_store import import_bundle, read_record, review_record
from tests.mechanism_source_fixtures import (  # noqa: F401
    fake_repo,
    fixture_qualified_catalog,
    mechanism_db,
    mechanism_repo,
)
from tests.test_limited_hit_benefit_sources import benefit_plan
from tests.test_mechanics_execution import EVIDENCE

# ruff: noqa: F811
MECHANISM = "skill.current-atk-limited-addition"


@pytest.fixture
def mechanism_plan(mechanism_db):
    bundle = fixture_qualified_catalog(mechanism_db)
    assert import_bundle(mechanism_db, bundle, dry_run=False)["applied"]
    plan = benefit_plan(mechanism_db)
    plan.pop("limited_hit_benefit_sources")
    claim = next(r["payload"]["claim"] for r in bundle["facts"] if r["id"] == MECHANISM + ".qualification")
    source = copy.deepcopy(DEFAULT_SOURCE)
    source.update(id="typed-benefit", actor="a", mechanism_id=MECHANISM)
    source["scenario"] = {"target_id": "enemy", "values": copy.deepcopy(claim["scenario_requirements"]), **EVIDENCE}
    source["calibration"] = {"activation_offset_seconds": .1, "precision_seconds": .01, **EVIDENCE}
    plan["mechanism_sources"] = [source]
    return plan, mechanism_db


def test_real_source_is_preview_only_until_agent_materializes_it(mechanism_plan):
    plan, db = mechanism_plan
    before = copy.deepcopy(plan)
    report = compose_equipment_plan(plan, db)
    assert plan == before
    assert input_view(report["plan"]) == input_view(before)
    record = report["mechanism_compilations"][0]
    assert report["state"] == "pending"
    assert record["status"] == "pending" and record["candidate_status"] == "ready"
    assert record["execution_authority"] == "none"
    assert record["whole_source_status"] == "partial"
    assert len(record["preview_rules"]) == 1
    assert not record["parts"] and not record["rules"] and not record["initial_rules"]
    assert record["required_materialization"]["candidate_fingerprint"] == record["candidate_fingerprint"]
    assert not report["mechanics_model"]["rules"]
    assert report["compiled_mechanics_model_fingerprint"] == fingerprint(report["mechanics_model"])
    assert any(row["code"] == "mechanism_materialization_required"
               for row in report["missing_inputs"])
    with pytest.raises(PlanError):
        run_plan_calc(plan, db)


def test_motion_rebuilds_preview_candidate_and_cache_is_not_authority(mechanism_plan):
    plan, db = mechanism_plan
    original_report = compose_equipment_plan(plan, db)
    original = original_report["mechanism_compilations"][0]
    plan["rotation_model"]["actions"][0].update(start=1, occupation_end=2)
    plan["resolution"] = {"state": "ready", "mechanism_compilations": [{"status": "ready", "rules": ["fake"]}]}
    plan["resolved_template"] = copy.deepcopy(original_report["plan"]["resolved_template"])
    if plan["resolved_template"] is not None:
        plan["resolved_template"]["hits"][0]["multiplier"] = 999
    moved = compose_equipment_plan(plan, db)
    assert moved["state"] == "pending"
    fresh = moved["mechanism_compilations"][0]
    assert fresh["acceptance_fingerprint"] == original["acceptance_fingerprint"]
    assert fresh["execution_fingerprint"] != original["execution_fingerprint"]
    assert fresh["candidate_fingerprint"] != original["candidate_fingerprint"]
    assert fresh["source_material"] == original["source_material"]
    assert not fresh["rules"] and len(fresh["preview_rules"]) == 1


def test_group_pending_withdraws_all_parts_and_tuning_cannot_restore_them(mechanism_plan):
    plan, db = mechanism_plan
    ready = compose_equipment_plan(plan, db)["mechanism_compilations"][0]
    extra = copy.deepcopy(plan["mechanism_sources"][0])
    extra.update(id="missing", mechanism_id="uninstalled.mechanism", accepted_fingerprint=None)
    plan["mechanism_sources"].append(extra)
    plan["rotation_tuning"]["buff_hit_matches"] = [{"id": "bypass", "mode": "replace",
        "effect_ref": {"buff_id": ready["preview_rules"][0]["id"],
                       "effect_id": ready["preview_rules"][0]["effects"][0]["id"]},
        "hit_ids": [], "reason": "明确排除，但不能越过待核来源",
        "source_refs": [{"kind": "manual", "note": "隔离测试"}], "state": "confirmed", "basis_fingerprint": "a" * 64}]
    report = compose_equipment_plan(plan, db)
    assert report["state"] == "pending"
    assert all(not r[key] for r in report["mechanism_compilations"] for key in ("parts", "rules", "initial_rules"))
    assert not report["mechanics_model"]["rules"] and not report["mechanics_matches"]
    assert any(i["code"] == "mechanism_pending" for i in report["missing_inputs"])
    with pytest.raises(PlanError):
        run_plan_calc(plan, db)


def test_fresh_revocation_invalidates_a_preview_candidate(mechanism_plan):
    plan, db = mechanism_plan
    with closing(sqlite3.connect(db)) as conn:
        row = read_record(conn, "release", "descriptor", MECHANISM)
    review_record(db, version_line="release", record_kind="descriptor", record_id=MECHANISM, revision=2,
                  expected_review_fingerprint=row["review_fingerprint"], status="revoked",
                  reviewer="isolated-review", reviewed_at="2026-10-03T00:00:00+00:00")
    report = compose_equipment_plan(plan, db)
    assert report["state"] == "pending"
    assert not report["mechanism_compilations"][0]["rules"]
    assert report["mechanism_compilations"][0]["candidate_status"] == "invalid"
    assert not report["mechanics_model"]["rules"]
