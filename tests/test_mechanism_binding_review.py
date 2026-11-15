"""Independent generic App binding probes; isolated source DB, no game oracle lookup."""
from __future__ import annotations

import copy
import sqlite3
from contextlib import closing

import pytest

from genshin_app.equipment import compose_equipment_plan
from genshin_db.runtime_queries import query_runtime
from genshin_db.semantic_store import import_bundle, read_record, review_record
from tests.mechanism_source_fixtures import (  # noqa: F401
    fake_repo,
    fixture_catalog,
    fixture_qualified_catalog,
    mechanism_db,
    mechanism_repo,
)
from tests.test_action_events_review import (
    attach_mechanics_rules,
    review_manual_sources,
)
from tests.test_limited_hit_benefit_sources import benefit_plan
from tests.test_mechanics_execution import (
    EVIDENCE,
    ability,
    action,
    hit,
    rule,
    second_character,
)

# ruff: noqa: F811
ADDITION = "skill.current-atk-limited-addition"


def source_profile(db, ident=ADDITION):
    return query_runtime(db, {"action": "mechanism_profile", "mechanism_id": ident})


def declaration(profile, ident="independent-source"):
    q = profile["compiler_result"]["execution_qualification"]
    return {"id": ident, "actor": "a", "version_line": "release",
        "mechanism_id": profile["descriptor"]["mechanism_id"], "revision": None,
        "scenario": {"target_id": "enemy", "values": copy.deepcopy(q["scenario_requirements"]), **EVIDENCE},
        "calibration": {"activation_offset_seconds": .1, "precision_seconds": .01, **EVIDENCE},
        "accepted_fingerprint": None}


@pytest.fixture
def generic_case(mechanism_db):
    assert import_bundle(mechanism_db, fixture_qualified_catalog(mechanism_db), dry_run=False)["applied"]
    plan = benefit_plan(mechanism_db)
    plan.pop("limited_hit_benefit_sources")
    plan["mechanism_sources"] = [declaration(source_profile(mechanism_db))]
    return plan, mechanism_db


def withdrawn(plan, db):
    report = compose_equipment_plan(plan, db)
    records = report["mechanism_compilations"]
    assert records and report["state"] == "pending"
    assert all(r["status"] == "pending" and r["required_confirmation"] is None for r in records)
    assert all(r[k] == [] for r in records for k in ("rules", "initial_rules", "parts"))
    assert not any(r["id"].startswith("auto.mechanism.") for r in report["mechanics_model"]["rules"])
    return report


def add_normal_contacts(plan, *, count=1, element="cryo", system="classic"):
    plan["rotation_model"]["abilities"].append(ability("normal", kind="action.normal_attack"))
    plan["rotation_model"]["actions"].append(action("normal", "normal", 1.1, 2.1, repeat={"mode": "once"}))
    for i in range(count):
        h = hit(f"normal.{i}", round(1.2 + i / 10, 6), kind="damage.normal_attack")
        h.update(element=element, damage_system=system)
        plan["manual_template"]["hits"].append(h)
        plan["mechanics_model"]["hit_events"].append({"hit_id": h["id"], "action_id": "normal", "order": 20 + i,
            "application": {"profile_id": "cryo", "target_id": "enemy", "landed": True},
            "contact": {"kind": "profile", "profile_id": "safe"}})


@pytest.mark.parametrize("kind", ["new_alias", "legacy_native", "typed_mechanics"])
def test_review_same_source_cannot_stack_under_another_native_id(generic_case, kind, monkeypatch):
    plan, db = generic_case
    if kind == "new_alias":
        alias = copy.deepcopy(plan["mechanism_sources"][0])
        alias["id"] = "same-fact-other-local-id"
        plan["mechanism_sources"].append(alias)
    elif kind == "legacy_native":
        plan["limited_hit_benefit_sources"] = benefit_plan(db)["limited_hit_benefit_sources"]
    else:
        from genshin_db import queries
        # This tiny runtime DB has no curated character tables. The old typed
        # binding's catalog membership is an explicit fixture fact, not an
        # alternative source of mechanism data or a bypass of fresh materialization.
        monkeypatch.setattr(queries, "get_meta", lambda *a, **k: {"datasets": {}})
        monkeypatch.setattr(queries, "get_character_detail", lambda *a, **k: {
            "character": {"id": 10000063}, "skills": [{"id": 10632}], "talents": [],
        })
        attach_mechanics_rules(plan, rule("explicit-old-delivery", "benefit.base_add.all", 123))
        plan["effect_bindings"][-1]["owner"] = {
            "kind": "character_skill", "character_id": 10000063, "version_line": "release", "skill_id": 10632,
        }
        review_manual_sources(plan, db)
    withdrawn(plan, db)


@pytest.mark.parametrize("where", ["count", "system"])
def test_review_potential_consumer_is_individual_classic_not_aggregated(generic_case, where):
    plan, db = generic_case
    add_normal_contacts(plan)
    victim = plan["manual_template"]["hits"][-1]
    if where == "count":
        victim["count"] = 2
    else:
        victim.update(damage_system="transformative", reaction={"type": "swirl"})
        victim.pop("multiplier", None)
    withdrawn(plan, db)


@pytest.mark.parametrize("offset,order", [(.2, 0), (.200001, 20)])
def test_review_activation_must_precede_actual_initial_contact(generic_case, offset, order):
    plan, db = generic_case
    plan["mechanism_sources"][0]["calibration"]["activation_offset_seconds"] = offset
    plan["action_event_overrides"] = [{"action_id": "cast", "landed": True, "order": order, **EVIDENCE}]
    withdrawn(plan, db)


def test_review_early_switch_cannot_be_accepted_by_scene_claim(generic_case):
    plan, db = generic_case
    second_character(plan["manual_template"], plan["rotation_model"])
    plan["rotation_model"]["actions"][0]["switch_allowed_at"] = .05
    plan["rotation_model"]["abilities"].append(ability("switch", kind="action.switch"))
    plan["rotation_model"]["actions"].append(action("cancel", "switch", .05, .06, switch_to="b", repeat={"mode": "once"}))
    plan["manual_template"]["switches"] = [{"t": .05, "character": "b"}]
    withdrawn(plan, db)


def test_review_fresh_revocation_invalidates_knowledge_candidate(generic_case):
    _, db = generic_case
    profile = source_profile(db)
    assert profile["status"] == "ready"
    fact = next(f for f in profile["facts"] if f["kind"] == "execution_qualification")
    with closing(sqlite3.connect(db)) as conn:
        stored = read_record(conn, "release", "fact", fact["id"], fact["revision"])
    review_record(db, version_line="release", record_kind="fact", record_id=fact["id"], revision=fact["revision"],
        status="revoked", reviewer="independent-fixture", reviewed_at="2026-10-03T01:00:00+00:00",
        expected_review_fingerprint=stored["review_fingerprint"])
    revoked = source_profile(db)
    assert revoked["status"] == "pending"
    assert revoked["compiler_result"] is None
    assert any(row["code"] == "mechanism_current_review_pending"
               and row["record_kind"] == "fact" for row in revoked["issues"])


def def_descriptor(db):
    migration = fixture_qualified_catalog(db)
    descriptor = copy.deepcopy(next(r for r in migration["descriptors"] if r["id"] == ADDITION))
    descriptor["id"] = descriptor["payload"]["descriptor"]["mechanism_id"] = "review.def-addition"
    descriptor["revision"] = descriptor["payload"]["descriptor"]["revision"] = 1
    template = descriptor["payload"]["descriptor"]["rule_templates"][0]
    template["rule"]["effects"][0]["value"]["stat"] = "def"
    old = next(f for f in fixture_catalog(db)["facts"] if f["payload"]["kind"] == "rule_template"
               and f["payload"]["claim"]["key"] == template["key"])
    fact = copy.deepcopy(old)
    fact["id"] = fact["payload"]["id"] = "review.def-rule"
    fact["payload"]["claim"] = copy.deepcopy(template)
    refs = descriptor["payload"]["descriptor"]["evidence_fact_ids"]
    refs[refs.index(old["id"] + "@1")] = "review.def-rule@1"
    bundle = {"schema_version": 1, "id": "independent-def-held-out", "facts": [fact], "descriptors": [descriptor],
        "heads": [{"record_kind": kind, "record_id": record["id"], "revision": 1, "expected_head_revision": None}
                  for kind, record in (("fact", fact), ("descriptor", descriptor))]}
    assert import_bundle(db, bundle, dry_run=False)["applied"]
    return descriptor["id"]


def test_review_filtered_def_writer_invalidates_fixed_stat_candidate(generic_case):
    plan, db = generic_case
    ident = def_descriptor(db)
    plan["manual_template"]["characters"][0]["stats"].update({"def": 500, "base_def": 100})
    plan["mechanism_sources"] = [declaration(source_profile(db, ident))]
    contribution = rule("def-panel", "benefit.stat_add.def", 100)
    contribution["effects"][0]["filter"] = {"event": "hit", "phase": "pre"}
    attach_mechanics_rules(plan, contribution)
    review_manual_sources(plan, db)
    withdrawn(plan, db)
