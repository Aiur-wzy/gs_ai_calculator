"""Synthetic action-admission model, fresh profile, and App compiler checks."""

from __future__ import annotations

import copy
import hashlib
import json
import shutil
import sqlite3
import uuid
from pathlib import Path

import pytest
from test_pipeline import MINIMAL_FILES
from test_runtime_object_index import build
from test_semantic_store import descriptor_bundle

from genshin_app.equipment.action_admission_catalog import (
    PREVIEW_REQUEST_CONTRACT,
    action_admission_catalog_capabilities,
    action_admission_model_candidates,
    action_admission_preview,
    catalog_filter_from_plan,
)
from genshin_app.equipment.action_admission_schema import CONTEXT
from genshin_app.equipment.action_admissions import compile_action_admissions
from genshin_app.equipment.schema import PlanError, new_plan, validate_plan
from genshin_db.runtime_action_admission import (
    CATALOG_ORDER,
    CATALOG_QUERY_CONTRACT,
    ActionAdmissionError,
    get_action_admission_profile,
    list_action_admission_model_candidates,
)
from genshin_db.runtime_action_resource import get_runtime_action_resource_profile
from genshin_db.runtime_queries import query_runtime, runtime_capabilities
from genshin_db.semantic_store import HEAD_TABLE, TABLES, import_bundle
from genshin_engine.rotation import check_rotation, validate_rotation_model
from genshin_runtime.action_admission_model import (
    CONTRACT,
    FIELD_AUTHORITY,
    QUALIFICATION,
    SCOPE,
    UNKNOWNS,
    evidence_for,
)
from genshin_runtime.action_resource_semantics import (
    snapshot as validate_action_snapshot,
)
from genshin_runtime.mechanism_semantics import fingerprint, validate_record

CID, DEPOT, SKILL = 90000001, 9001, 90013
COMMIT = "342084292ba4c3c6ec2bc34c045de4de72ae8d53"
EVIDENCE = {"status": "assumed", "source": "仅用于合成动作接纳测试"}


@pytest.fixture
def admission_db():
    root = (
        Path("outputs/engine-automation-20261015/action-admission-v2-test-fixtures")
        / uuid.uuid4().hex
    )
    fake_repo = root / "fake-repo"
    for relative, data in MINIMAL_FILES.items():
        target = fake_repo / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(data, ensure_ascii=False), "utf-8")
    skill_path = fake_repo / "ExcelBinOutput/AvatarSkillExcelConfigData.json"
    skills = json.loads(skill_path.read_text("utf-8"))
    next(row for row in skills if row["id"] == SKILL)["maxChargeNum"] = 1
    skill_path.write_text(json.dumps(skills, ensure_ascii=False), "utf-8")
    db = root / "action-admission.db"
    build(db, fake_repo, upstream_commit=COMMIT)
    profile = get_runtime_action_resource_profile(
        db, character_id=CID, slot="burst", skill_depot_id=DEPOT
    )
    claim = {
        "contract": CONTRACT,
        "qualification": QUALIFICATION,
        "snapshot": profile["snapshot"],
        "target": {
            "character_id": CID,
            "skill_depot_id": DEPOT,
            "skill_id": SKILL,
            "slot": "burst",
        },
        "domain": {
            "constellations": [0],
            "variant": "default",
            "action_type_id": "action.elemental_burst",
        },
        "resource_binding": {
            "resource_fingerprint": profile["resource_fingerprint"],
            "cooldown_start": "action_start",
            "cooldown_recovery": "parallel",
            "cooldown_group": f"skill:{DEPOT}:{SKILL}",
            "energy_mode": "fixed_equal_cost",
            "energy_delivery": "action_start",
        },
        "field_authority": copy.deepcopy(FIELD_AUTHORITY),
        "unknowns": copy.deepcopy(UNKNOWNS),
        "prior_decision": {
            key: value * 64
            for key, value in (
                ("proposal_sha256", "a"),
                ("decision_sha256", "b"),
                ("report_sha256", "c"),
            )
        },
        "scope": SCOPE,
    }
    evidence = evidence_for(claim)
    assert [item["id"] for item in evidence] == [
        "action-admission-resource-profile",
        "action-admission-review-decision",
        "action-admission-independent-review",
    ]
    assert evidence[-1]["sha256"] == claim["prior_decision"]["report_sha256"]
    record = {
        "id": "fixture.action-admission",
        "revision": 1,
        "subject": {"target_type": "character", "target_id": CID},
        "payload": {
            "id": "fixture.action-admission",
            "revision": 1,
            "kind": "action_admission_model",
            "claim": claim,
            "evidence_tier": "experimental_reference",
            "evidence_refs": [item["id"] for item in evidence],
        },
        "evidence": evidence,
        "applicability": {
            "version_line": "release",
            "upstream_commit": COMMIT,
            "normalization_fingerprint": fingerprint(
                profile["snapshot"]["normalization"]
            ),
        },
        "review": {
            "status": "approved",
            "reviewer": "synthetic-fixture",
            "reviewed_at": "2026-10-15T00:00:00+00:00",
        },
    }
    validate_record(record, "fact")
    import_bundle(
        db,
        {
            "schema_version": 1,
            "id": "action-admission-fixture",
            "facts": [record],
            "descriptors": [],
            "heads": [
                {
                    "record_kind": "fact",
                    "record_id": record["id"],
                    "revision": 1,
                    "expected_head_revision": None,
                }
            ],
        },
        dry_run=False,
    )
    try:
        yield db, record
    finally:
        shutil.rmtree(root, ignore_errors=True)


def selection():
    return {
        "character_id": CID,
        "skill_depot_id": DEPOT,
        "skill_id": SKILL,
        "slot": "burst",
        "constellation": 0,
    }


def test_action_fact_snapshot_accepts_versioned_source_capabilities_and_rejects_tamper(
    admission_db,
):
    db, _record = admission_db
    current = get_runtime_action_resource_profile(
        db, character_id=CID, slot="burst", skill_depot_id=DEPOT
    )["snapshot"]

    validate_action_snapshot(current)
    assert current["source_capability_contract"] == (
        "genshin-runtime-source-capabilities-v1"
    )
    assert current["source_capability_state"] in ("ready", "degraded")

    # Existing reviewed facts keep their historical closed envelope so they
    # can be classified stale instead of corrupt after a database migration.
    legacy = copy.deepcopy(current)
    for key in (
        "normalization_state",
        "source_capability_contract",
        "source_capability_state",
        "source_capabilities",
    ):
        legacy.pop(key)
    validate_action_snapshot(legacy)

    invalid = []
    unknown = copy.deepcopy(current)
    unknown["unexpected"] = True
    invalid.append(unknown)

    bad_contract = copy.deepcopy(current)
    bad_contract["source_capability_contract"] = "forged"
    invalid.append(bad_contract)

    bad_normalization = copy.deepcopy(current)
    bad_normalization["normalization_state"] = "unavailable"
    invalid.append(bad_normalization)

    nested_unknown = copy.deepcopy(current)
    next(iter(nested_unknown["source_capabilities"].values()))["unexpected"] = True
    invalid.append(nested_unknown)

    inconsistent = copy.deepcopy(current)
    inconsistent["source_capability_state"] = (
        "degraded" if current["source_capability_state"] == "ready" else "ready"
    )
    invalid.append(inconsistent)

    required_unavailable = copy.deepcopy(current)
    required = next(
        row
        for row in required_unavailable["source_capabilities"].values()
        if row["requirement"] == "required"
    )
    required["state"] = "unavailable"
    required_unavailable["source_capability_state"] = "degraded"
    invalid.append(required_unavailable)

    for value in invalid:
        with pytest.raises(ValueError):
            validate_action_snapshot(value)


def model_ref():
    return {"id": "fixture.action-admission", "revision": 1}


def _install_fact(db, record, bundle_id):
    validate_record(record, "fact")
    import_bundle(
        db,
        {
            "schema_version": 1,
            "id": bundle_id,
            "facts": [record],
            "descriptors": [],
            "heads": [
                {
                    "record_kind": "fact",
                    "record_id": record["id"],
                    "revision": record["revision"],
                    "expected_head_revision": None,
                }
            ],
        },
        dry_run=False,
    )


def _unrelated_fact(source, record_id="fixture.unrelated.fact"):
    record = copy.deepcopy(source)
    record["id"] = record["payload"]["id"] = record_id
    record["payload"].update(
        kind="execution_requirement", claim={"requirement": "single_target"}
    )
    return record


def _other_actor_admission(source, record_id="fixture.other-actor.action-admission"):
    record = copy.deepcopy(source)
    record["id"] = record["payload"]["id"] = record_id
    record["subject"]["target_id"] = CID + 1
    record["payload"]["claim"]["target"]["character_id"] = CID + 1
    record["evidence"] = evidence_for(record["payload"]["claim"])
    record["payload"]["evidence_refs"] = [item["id"] for item in record["evidence"]]
    return record


def plan():
    template = {
        "version": 2,
        "form": "resolved",
        "id_registry_version": 1,
        "type_definitions": {"damage_types": []},
        "enemy": {"level": 90, "resistance": {"default": 0.1}},
        "characters": [
            {
                "id": "a",
                "level": 90,
                "stats": {
                    "atk": 100,
                    "base_atk": 50,
                    "em": 0,
                    "crit_rate": 0.05,
                    "crit_dmg": 0.5,
                    "energy_recharge": 1,
                },
            }
        ],
        "hits": [
            {
                "id": "h",
                "t": 0.2,
                "character": "a",
                "skill": "工程爆发",
                "hit_name": "工程命中",
                "damage_type_ids": ["damage.elemental_burst"],
                "damage_system": "classic",
                "element": "geo",
                "multiplier": 1,
            }
        ],
        "buffs": [],
        "switches": [],
        "loop_duration": 20,
    }
    value = new_plan(template)
    value.update(
        plan_version=3,
        mechanics_model={
            "schema_version": 1,
            "rules": [],
            "hit_patterns": [],
            "condition_events": [],
            "initial_state": {"rules": []},
            "hit_events": [{"hit_id": "h", "action_id": "cast", "order": 10}],
        },
        rotation_model={
            "schema_version": 1,
            "period": 20,
            "max_cycles": 3,
            "initial_frontline": "a",
            "characters": [
                {
                    "id": "a",
                    "element": "geo",
                    "energy_recharge": 1,
                    "energy_capacity": 60,
                    "initial_energy": 60,
                    **EVIDENCE,
                }
            ],
            "abilities": [
                {
                    "id": "q",
                    "character": "a",
                    "action_type_id": "action.elemental_burst",
                    "variant": "default",
                    "cooldown": None,
                    "energy": None,
                    "rules": [],
                    **EVIDENCE,
                }
            ],
            "actions": [
                {
                    "id": "cast",
                    "ability_id": "q",
                    "start": 0,
                    "occupation_end": 0.1,
                    "order": 0,
                    "repeat": {"mode": "once"},
                    **EVIDENCE,
                }
            ],
            "events": [],
            "initial_cooldowns": [
                {"character": "a", "group": f"skill:{DEPOT}:{SKILL}", "ready_in": []}
            ],
            "action_hit_links": [{"action_id": "cast", "hit_id": "h"}],
        },
        action_admission_context=copy.deepcopy(CONTEXT),
        action_admission_sources=[
            {
                "id": "admission.q",
                "ability_id": "q",
                "version_line": "release",
                "selection": selection(),
                "variant": "default",
                "model_ref": model_ref(),
                "accepted_fingerprint": None,
            }
        ],
    )
    validate_plan(value)
    return value


def compile_plan(value, db):
    issues = []
    rotation, records, sources = compile_action_admissions(
        value, db, lambda *args: issues.append(args)
    )
    return rotation, records, sources, issues


def test_fresh_profile_is_read_only_and_complete(admission_db):
    db, _ = admission_db
    before = hashlib.sha256(db.read_bytes()).hexdigest()
    result = get_action_admission_profile(
        db,
        selection=selection(),
        variant="default",
        action_type_id="action.elemental_burst",
        model_ref=model_ref(),
    )
    assert result["status"] == "ready" and result["compiled_values"] == {
        "cooldown": {
            "group": f"skill:{DEPOT}:{SKILL}",
            "duration": 15,
            "start": "action_start",
            "charges": 1,
            "recovery": "parallel",
        },
        "energy": {"required": 60, "amount": 60, "delivery": "action_start"},
    }
    assert result["declared_not_verified"] == UNKNOWNS
    assert (
        result["competitor_inventory"]["entries"][0]["disposition"] == "selected_model"
    )
    assert set(result["competitor_inventory"]["entries"][0]) >= {
        "record_kind",
        "id",
        "revision",
        "head_updated_at",
        "payload_fingerprint",
        "review_fingerprint",
        "fact_kind",
        "review_status",
        "disposition",
        "target_key_fingerprint",
    }
    assert hashlib.sha256(db.read_bytes()).hexdigest() == before


def test_global_non_competing_inventory_changes_confirmation_without_blocking(
    admission_db,
):
    db, original = admission_db
    first = get_action_admission_profile(
        db,
        selection=selection(),
        variant="default",
        action_type_id="action.elemental_burst",
        model_ref=model_ref(),
    )
    catalog_before = list_action_admission_model_candidates(db, _catalog_request())
    value = plan()
    _, initial_records, _, _ = compile_plan(value, db)
    previous_confirmation = initial_records[0]["source_fingerprint"]
    value["action_admission_sources"][0]["accepted_fingerprint"] = previous_confirmation

    unrelated = _unrelated_fact(original)
    _install_fact(db, unrelated, "action-admission-global-unrelated")
    second = get_action_admission_profile(
        db,
        selection=selection(),
        variant="default",
        action_type_id="action.elemental_burst",
        model_ref=model_ref(),
    )
    catalog_middle = list_action_admission_model_candidates(db, _catalog_request())
    assert second["status"] == "ready"
    assert second["source_fingerprint"] != first["source_fingerprint"]
    assert second["current_head_fingerprint"] != first["current_head_fingerprint"]
    assert (
        catalog_middle["admission_inventory_fingerprint"]
        != catalog_before["admission_inventory_fingerprint"]
    )
    assert (
        next(
            row
            for row in second["competitor_inventory"]["entries"]
            if row["id"] == unrelated["id"]
        )["disposition"]
        == "classified_non_competing"
    )
    _, changed_records, changed_sources, _ = compile_plan(value, db)
    assert changed_records[0]["status"] == "pending" and changed_sources == []
    assert changed_records[0]["source_fingerprint"] != previous_confirmation
    assert {issue["code"] for issue in changed_records[0]["issues"]} == {
        "source_changed"
    }

    other_actor = _other_actor_admission(original)
    _install_fact(db, other_actor, "action-admission-global-other-actor")
    third = get_action_admission_profile(
        db,
        selection=selection(),
        variant="default",
        action_type_id="action.elemental_burst",
        model_ref=model_ref(),
    )
    catalog_after = list_action_admission_model_candidates(db, _catalog_request())
    assert third["status"] == "ready"
    assert third["source_fingerprint"] != second["source_fingerprint"]
    assert (
        catalog_after["admission_inventory_fingerprint"]
        != catalog_middle["admission_inventory_fingerprint"]
    )
    other_entry = next(
        row
        for row in third["competitor_inventory"]["entries"]
        if row["id"] == other_actor["id"]
    )
    assert other_entry["disposition"] == "classified_non_competing"
    assert "target_key_fingerprint" in other_entry


def test_global_malformed_fact_fails_closed(admission_db):
    db, original = admission_db
    unrelated = _unrelated_fact(original)
    _install_fact(db, unrelated, "action-admission-global-malformed")
    with sqlite3.connect(db) as conn:
        conn.execute(
            f"UPDATE {TABLES['fact']} SET payload_fingerprint=? WHERE record_id=?",
            ("0" * 64, unrelated["id"]),
        )
    with pytest.raises(ActionAdmissionError) as error:
        get_action_admission_profile(
            db,
            selection=selection(),
            variant="default",
            action_type_id="action.elemental_burst",
            model_ref=model_ref(),
        )
    assert error.value.code == "action_admission_current"


def test_preview_confirm_compiles_static_certificate_without_mutating_native(
    admission_db,
):
    db, _ = admission_db
    value = plan()
    native = copy.deepcopy(value)
    rotation, records, sources, issues = compile_plan(value, db)
    assert value == native and not sources and issues
    assert records[0]["required_confirmation"]["accepted_fingerprint"]
    value["action_admission_sources"][0]["accepted_fingerprint"] = records[0][
        "source_fingerprint"
    ]
    rotation, records, sources, issues = compile_plan(value, db)
    assert not issues and records[0]["status"] == "ready" and len(sources) == 1
    ability = rotation["abilities"][0]
    assert (
        ability["cooldown"]["charges"] == 1
        and ability["energy"]["delivery"] == "action_start"
    )
    assert (
        ability["action_admission_source"]["contract"]
        == "static-action-admission-source-v1"
    )
    certificate = records[0]["static_certificate"]
    assert certificate["aliases"] == certificate["capacity_dependencies"] == []
    assert certificate["initial_cooldown"]["ready_in"] == []
    assert validate_rotation_model(rotation)["status"] == "valid"
    checked = check_rotation(value["manual_template"], rotation)
    assert "action_admission_execution" in checked, checked
    assert checked["action_admission_execution"]["committed_actions"] == 1


def test_missing_initial_or_null_context_never_becomes_ready(admission_db):
    db, _ = admission_db
    value = plan()
    value["rotation_model"]["initial_cooldowns"] = []
    value["action_admission_context"] = None
    _, records, _, _ = compile_plan(value, db)
    value["action_admission_sources"][0]["accepted_fingerprint"] = records[0][
        "source_fingerprint"
    ]
    rotation, records, sources, _ = compile_plan(value, db)
    codes = {item["code"] for item in records[0]["issues"]}
    assert {
        "action_admission_context_pending",
        "action_admission_initial_pending",
    } <= codes
    assert records[0]["status"] == "pending" and sources == []
    assert (
        rotation["initial_cooldowns"] == []
        and rotation["abilities"][0]["cooldown"] is None
    )


@pytest.mark.parametrize(
    "conflict", ["manual", "resource", "capacity", "private_marker"]
)
def test_ownership_conflicts_reject_before_database_query(conflict, monkeypatch):
    value = plan()
    if conflict == "manual":
        value["rotation_model"]["abilities"][0]["cooldown"] = {
            "group": "q",
            "duration": 15,
            "start": "action_start",
            "charges": 1,
            "recovery": "parallel",
        }
        value["rotation_model"]["abilities"][0]["energy"] = {
            "required": 60,
            "amount": 60,
        }
    elif conflict == "resource":
        value["action_resource_sources"] = [
            {
                "ability_id": "q",
                "version_line": "release",
                "selection": {"character_id": CID, "slot": "burst", "constellation": 0},
                "purpose": "ordinary_fixed_burst_base_resources",
                "variant": "default",
                "status": "confirmed",
                "source": "合成冲突",
                "expected_snapshot_id": None,
                "expected_resource_fingerprint": None,
                "expected_scope_fingerprint": None,
            }
        ]
    elif conflict == "capacity":
        value["action_capacity_context"] = {
            "contract": "static-action-capacity-context-v1",
            "mode": "ordinary_open_world",
            "external_capacity_effects": "none",
            "window_changes": "none",
        }
        value["action_capacity_sources"] = [
            {
                "id": "capacity",
                "actor": "a",
                "ability_ids": ["q"],
                "version_line": "release",
                "model_ref": {"id": "fixture.capacity", "revision": 1},
                "target": {
                    "character_id": CID,
                    "skill_depot_id": DEPOT,
                    "skill_id": SKILL,
                    "variant": "tap",
                },
                "accepted_fingerprint": None,
            }
        ]
    else:
        value["rotation_model"]["abilities"][0]["action_admission_source"] = {}
    import genshin_db.runtime_action_admission as runtime

    monkeypatch.setattr(
        runtime,
        "get_action_admission_profile",
        lambda *_args, **_kwargs: pytest.fail("DB queried"),
    )
    with pytest.raises(PlanError):
        validate_plan(value)


def _catalog_plan():
    value = plan()
    validate_plan(value)
    return value


def _catalog_request(
    *, observed=None, unknown=None, page_size=16, cursor=None, view="all"
):
    observed = observed or {
        "target": {"skill_depot_id": DEPOT, "skill_id": SKILL, "slot": "burst"},
        "domain": {
            "constellation": 0,
            "variant": "default",
            "action_type_id": "action.elemental_burst",
        },
    }
    return {
        "contract": CATALOG_QUERY_CONTRACT,
        "version_line": "release",
        "character_id": CID,
        "observed": observed,
        "unknown_paths": [] if unknown is None else unknown,
        "view": view,
        "order": CATALOG_ORDER,
        "page_size": page_size,
        "cursor": cursor,
    }


def test_action_admission_catalog_is_read_only_grouped_and_never_returns_gate_values(
    admission_db,
):
    db, _ = admission_db
    before = hashlib.sha256(db.read_bytes()).hexdigest()
    page = list_action_admission_model_candidates(db, _catalog_request())
    assert page["counts"]["exact"] == 1 and page["counts"]["filtered_models"] == 1
    item = page["items"][0]
    assert item["catalog_group"] == "exact"
    assert item["selection_status"] == "reference_selectable_for_fresh_preview"
    encoded = json.dumps(page, ensure_ascii=False)
    assert (
        "compiled_values" not in encoded
        and '"cooldown"' not in encoded
        and '"energy"' not in encoded
    )
    assert page["execution_authority"] is False
    assert hashlib.sha256(db.read_bytes()).hexdigest() == before

    partial_observed = {
        "target": {"slot": "burst"},
        "domain": {
            "constellation": 0,
            "variant": "default",
            "action_type_id": "action.elemental_burst",
        },
    }
    partial = list_action_admission_model_candidates(
        db,
        _catalog_request(
            observed=partial_observed,
            unknown=["target.skill_depot_id", "target.skill_id"],
        ),
    )
    assert partial["items"][0]["catalog_group"] == "partial"


def test_action_admission_catalog_service_uses_typed_plan_and_runtime_dispatch(
    admission_db,
):
    db, _ = admission_db
    value = _catalog_plan()
    query = catalog_filter_from_plan(value, "a", "q")
    assert query["observed"]["target"] == {
        "skill_depot_id": DEPOT,
        "skill_id": SKILL,
        "slot": "burst",
    }
    page = action_admission_model_candidates(value, "a", "q", db)
    assert page["items"][0]["catalog_group"] == "exact"
    runtime_page = query_runtime(
        db,
        {"action": "action_admission_model_candidates", "request": _catalog_request()},
    )
    assert runtime_page["catalog_fingerprint"] == page["catalog_fingerprint"]
    caps = runtime_capabilities()
    assert caps["required"]["action_admission_model_candidates"] == ["request"]
    assert (
        caps["action_admission_model_candidates"]["returns_compiled_cooldown_or_energy"]
        is False
    )
    assert action_admission_catalog_capabilities()["execution_authority"] is False


def test_action_admission_preview_confirm_and_native_proposal_are_detached(
    admission_db,
):
    db, _ = admission_db
    value = _catalog_plan()
    original = copy.deepcopy(value)
    request = {
        "contract": PREVIEW_REQUEST_CONTRACT,
        "actor_id": "a",
        "ability_id": "q",
        "binding_id": "admission.q",
        "model_ref": model_ref(),
        "selection": selection(),
        "context": copy.deepcopy(CONTEXT),
        "initial_ready_in": [],
        "takeover": False,
        "accepted_fingerprint": None,
    }
    first = action_admission_preview(value, request, db)
    assert value == original
    assert first["compilation"]["status"] == "pending"
    accepted = first["compilation"]["required_confirmation"]["accepted_fingerprint"]
    request["accepted_fingerprint"] = accepted
    confirmed = action_admission_preview(value, request, db)
    assert confirmed["compilation"]["status"] == "ready"
    proposal = confirmed["proposal"]
    assert proposal["action_admission_sources"][0]["accepted_fingerprint"] == accepted
    assert proposal["rotation_model"]["abilities"][0]["cooldown"] is None
    assert "action_admission_source" not in proposal["rotation_model"]["abilities"][0]
    assert proposal["rotation_model"]["initial_cooldowns"] == [
        {"character": "a", "group": f"skill:{DEPOT}:{SKILL}", "ready_in": []}
    ]
    assert confirmed["execution_authority"] is False


def test_action_admission_catalog_request_and_cursor_fail_closed(admission_db):
    db, _ = admission_db
    bad = _catalog_request()
    bad["order"] = "client_order"
    with pytest.raises(ActionAdmissionError) as error:
        list_action_admission_model_candidates(db, bad)
    assert error.value.code == "action_admission_catalog_query"
    bad = _catalog_request()
    bad["unknown_paths"] = ["target.skill_id"]
    with pytest.raises(ActionAdmissionError):
        list_action_admission_model_candidates(db, bad)


def test_action_admission_catalog_cursor_binds_snapshot_filter_view_order_and_inventory(
    admission_db,
):
    db, original = admission_db
    second = copy.deepcopy(original)
    second["id"] = second["payload"]["id"] = "fixture.action-admission.second"
    second["payload"]["claim"]["target"]["skill_id"] = SKILL + 1
    second["evidence"] = evidence_for(second["payload"]["claim"])
    second["payload"]["evidence_refs"] = [item["id"] for item in second["evidence"]]
    validate_record(second, "fact")
    import_bundle(
        db,
        {
            "schema_version": 1,
            "id": "action-admission-catalog-second",
            "facts": [second],
            "descriptors": [],
            "heads": [
                {
                    "record_kind": "fact",
                    "record_id": second["id"],
                    "revision": 1,
                    "expected_head_revision": None,
                }
            ],
        },
        dry_run=False,
    )
    observed = {
        "target": {"slot": "burst"},
        "domain": {
            "constellation": 0,
            "variant": "default",
            "action_type_id": "action.elemental_burst",
        },
    }
    request = _catalog_request(
        observed=observed,
        unknown=["target.skill_depot_id", "target.skill_id"],
        page_size=1,
    )
    first = list_action_admission_model_candidates(db, request)
    assert first["has_more"] is True and first["next_cursor"]
    request["cursor"] = first["next_cursor"]
    second_page = list_action_admission_model_candidates(db, request)
    assert second_page["has_more"] is False
    assert {
        first["items"][0]["model_ref"]["id"],
        second_page["items"][0]["model_ref"]["id"],
    } == {"fixture.action-admission", "fixture.action-admission.second"}
    changed = copy.deepcopy(request)
    changed["view"] = "partial"
    with pytest.raises(ActionAdmissionError) as error:
        list_action_admission_model_candidates(db, changed)
    assert error.value.code == "action_admission_catalog_cursor"
    unrelated = _unrelated_fact(original, "fixture.cursor-unrelated.fact")
    _install_fact(db, unrelated, "action-admission-cursor-global-change")
    with pytest.raises(ActionAdmissionError) as error:
        list_action_admission_model_candidates(db, request)
    assert error.value.code == "action_admission_catalog_changed"


def _catalog_record(
    source, record_id, *, status="approved", skill_id=None, stale=False
):
    record = copy.deepcopy(source)
    record["id"] = record["payload"]["id"] = record_id
    if skill_id is not None:
        record["payload"]["claim"]["target"]["skill_id"] = skill_id
    if stale:
        record["payload"]["claim"]["snapshot"]["upstream_commit"] = "d" * 40
        record["payload"]["claim"]["snapshot"]["normalization"]["upstream_commit"] = (
            "d" * 40
        )
    claim = record["payload"]["claim"]
    record["evidence"] = evidence_for(claim)
    record["payload"]["evidence_refs"] = [item["id"] for item in record["evidence"]]
    record["applicability"] = {
        "version_line": "release",
        "upstream_commit": claim["snapshot"]["upstream_commit"],
        "normalization_fingerprint": fingerprint(claim["snapshot"]["normalization"]),
    }
    record["review"] = {
        "status": status,
        "reviewer": "synthetic-fixture" if status in ("approved", "revoked") else None,
        "reviewed_at": "2026-10-15T00:00:00+00:00"
        if status in ("approved", "revoked")
        else None,
    }
    validate_record(record, "fact")
    return record


def test_action_admission_catalog_separates_review_freshness_conflict_and_mismatch(
    admission_db,
):
    db, original = admission_db
    records = [
        _catalog_record(original, "fixture.action-admission.duplicate"),
        _catalog_record(
            original,
            "fixture.action-admission.pending",
            status="pending",
            skill_id=SKILL + 1,
        ),
        _catalog_record(
            original,
            "fixture.action-admission.revoked",
            status="revoked",
            skill_id=SKILL + 2,
        ),
        _catalog_record(
            original, "fixture.action-admission.stale", stale=True, skill_id=SKILL + 3
        ),
        _catalog_record(
            original, "fixture.action-admission.mismatch", skill_id=SKILL + 4
        ),
    ]
    import_bundle(
        db,
        {
            "schema_version": 1,
            "id": "action-admission-catalog-groups",
            "facts": records,
            "descriptors": [],
            "heads": [
                {
                    "record_kind": "fact",
                    "record_id": record["id"],
                    "revision": 1,
                    "expected_head_revision": None,
                }
                for record in records
            ],
        },
        dry_run=False,
    )
    page = list_action_admission_model_candidates(db, _catalog_request(page_size=16))
    groups = {item["model_ref"]["id"]: item["catalog_group"] for item in page["items"]}
    assert groups["fixture.action-admission"] == "conflict"
    assert groups["fixture.action-admission.duplicate"] == "conflict"
    assert groups["fixture.action-admission.pending"] == "pending"
    assert groups["fixture.action-admission.revoked"] == "revoked"
    assert groups["fixture.action-admission.stale"] == "stale"
    assert groups["fixture.action-admission.mismatch"] == "not_evaluated"
    assert all(
        item["selection_status"] == "reference_not_selectable" for item in page["items"]
    )


def test_competing_target_still_blocks_profile(admission_db):
    db, original = admission_db
    competing = _catalog_record(original, "fixture.action-admission.competing")
    _install_fact(db, competing, "action-admission-profile-competing")
    result = get_action_admission_profile(
        db,
        selection=selection(),
        variant="default",
        action_type_id="action.elemental_burst",
        model_ref=model_ref(),
    )
    assert result["status"] == "pending"
    assert result["reason"] == "action_admission_competitor_unresolved"
    dispositions = {
        row["id"]: row["disposition"]
        for row in result["competitor_inventory"]["entries"]
    }
    assert dispositions == {
        "fixture.action-admission": "selected_model",
        "fixture.action-admission.competing": "competing_target",
    }


@pytest.mark.parametrize(
    "exit_kind", ["current_nonready", "out_of_scope", "competition"]
)
def test_every_pending_profile_exit_enforces_response_budget(
    admission_db, monkeypatch, exit_kind
):
    db, original = admission_db
    selected = selection()
    selected_ref = model_ref()
    if exit_kind == "current_nonready":
        selected_ref["revision"] = 2
    elif exit_kind == "out_of_scope":
        selected["skill_id"] = SKILL + 1
    else:
        competing = _catalog_record(
            original, "fixture.action-admission.budget-competitor"
        )
        _install_fact(db, competing, "action-admission-budget-competitor")
    monkeypatch.setattr("genshin_db.runtime_action_admission.MAX_PROFILE_BYTES", 1)
    with pytest.raises(ActionAdmissionError) as error:
        get_action_admission_profile(
            db,
            selection=selected,
            variant="default",
            action_type_id="action.elemental_burst",
            model_ref=selected_ref,
        )
    assert error.value.code == "action_admission_budget"


@pytest.mark.parametrize("bad_kind", ["bogus", None, 7])
def test_invalid_semantic_head_kind_cannot_be_filtered_out(admission_db, bad_kind):
    db, _ = admission_db
    with sqlite3.connect(db) as conn:
        conn.execute(f"CREATE TABLE damaged_head AS SELECT * FROM {HEAD_TABLE}")
        conn.execute(f"DROP TABLE {HEAD_TABLE}")
        conn.execute(f"ALTER TABLE damaged_head RENAME TO {HEAD_TABLE}")
        conn.execute(
            f"INSERT INTO {HEAD_TABLE} VALUES (?,?,?,?,?)",
            ("release", bad_kind, "fixture.invalid-head-kind", 1, "now"),
        )
    with pytest.raises(ActionAdmissionError) as error:
        get_action_admission_profile(
            db,
            selection=selection(),
            variant="default",
            action_type_id="action.elemental_burst",
            model_ref=model_ref(),
        )
    assert error.value.code == "action_admission_current"


def test_legal_descriptor_head_does_not_enter_fact_inventory_fingerprint(admission_db):
    db, _ = admission_db
    material = descriptor_bundle()
    import_bundle(
        db,
        {
            "schema_version": 1,
            "id": "action-admission-descriptor-fact",
            "facts": material["facts"],
            "descriptors": [],
            "heads": [row for row in material["heads"] if row["record_kind"] == "fact"],
        },
        dry_run=False,
    )
    before = get_action_admission_profile(
        db,
        selection=selection(),
        variant="default",
        action_type_id="action.elemental_burst",
        model_ref=model_ref(),
    )
    catalog_before = list_action_admission_model_candidates(db, _catalog_request())
    import_bundle(
        db,
        {
            "schema_version": 1,
            "id": "action-admission-descriptor-head",
            "facts": [],
            "descriptors": material["descriptors"],
            "heads": [
                row for row in material["heads"] if row["record_kind"] == "descriptor"
            ],
        },
        dry_run=False,
    )
    after = get_action_admission_profile(
        db,
        selection=selection(),
        variant="default",
        action_type_id="action.elemental_burst",
        model_ref=model_ref(),
    )
    catalog_after = list_action_admission_model_candidates(db, _catalog_request())
    assert after["status"] == "ready"
    assert after["current_head_fingerprint"] == before["current_head_fingerprint"]
    assert after["source_fingerprint"] == before["source_fingerprint"]
    assert (
        catalog_after["admission_inventory_fingerprint"]
        == catalog_before["admission_inventory_fingerprint"]
    )
