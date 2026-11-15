"""Source-backed multi-channel dispatch for the ordinary dynamic elemental runtime."""
from __future__ import annotations

import copy

import pytest

from genshin_app.equipment import compose_equipment_plan, run_plan_calc
from genshin_app.equipment.action_event_schema import (
    action_event_capabilities,
    action_event_receipt_compiler,
    validate_action_event_fields,
)
from genshin_app.equipment.periodic_schema import (
    APPLICATION_KIND,
    KIND,
    periodic_attack_capabilities,
)
from genshin_app.equipment.schema import PlanError
from tests.test_elemental_swirl import swirl_model
from tests.test_mechanics_execution import EVIDENCE, action
from tests.test_periodic_actions import confirm_periodic, periodic_plan
from tests.test_pipeline import fake_repo, stub_fetcher  # noqa: F401
from tests.test_runtime_periodic_attack import periodic_db  # noqa: F401

# ruff: noqa: F811


def dynamic_plan(db):
    plan = periodic_plan(db)
    profile = plan["action_event_profiles"][0]
    profile["kind"] = APPLICATION_KIND
    for channel in profile["channels"]:
        channel.pop("contact_profile_id")
    elemental = swirl_model("pyro", 3.2)
    elemental["ruleset"] = "gauge-swirl-dynamic-v3"
    plan["mechanics_model"]["elemental_model"] = elemental
    return plan


def test_capabilities_publish_distinct_dynamic_multi_contract_without_contact():
    action = action_event_capabilities()
    periodic = periodic_attack_capabilities()
    assert APPLICATION_KIND in action["profile_kinds"]
    assert APPLICATION_KIND in periodic["profile_kinds"]
    contract = periodic["profile_contracts"][APPLICATION_KIND]
    assert contract["ruleset"] == "gauge-swirl-dynamic-v3"
    assert contract["contact"] == "not_supported"
    channel = periodic["profile_schemas"][APPLICATION_KIND]["properties"]["channels"]["items"]
    assert "contact_profile_id" not in channel["properties"]


def test_dynamic_multi_source_generates_hits_applications_and_runtime_reaction(periodic_db):
    plan = confirm_periodic(dynamic_plan(periodic_db), periodic_db)
    output = run_plan_calc(plan, periodic_db)
    patterns = output["report"]["mechanics_model"]["hit_patterns"]
    record = output["result"]["action_event_compilations"][0]
    assert record["kind"] == APPLICATION_KIND
    assert record["compiler"] == "calibrated-action-events-v2"
    assert record["stats"]["generated_hits"] == 15
    assert len(patterns) == 15
    assert all(pattern["application"]["profile_id"] in {"slice.app", "blast.app"}
               for pattern in patterns)
    assert all("contact" not in pattern for pattern in patterns)
    assert all("contact" not in channel for channel in record["scope"]["channels"])
    attempts = [row for row in output["result"]["elemental"]["trace"]
                if row["type"] == "elemental_application"]
    assert len(attempts) == 15
    assert attempts[0]["reaction"]["type"] == "melt"
    assert output["result_receipt"]["action_events"]["compiler"] == "calibrated-action-events-v2"


def test_dynamic_multi_accepts_explicit_nonzero_constellation_effective_level(
        periodic_db, fake_repo):
    from tests.test_runtime_periodic_attack import change

    path = "ExcelBinOutput/ProudSkillExcelConfigData.json"

    def add_level(rows):
        level = copy.deepcopy(rows[0])
        level["proudSkillId"] = 9100213
        level["level"] = 13
        rows.append(level)

    change(periodic_db, fake_repo, path, add_level)
    plan = dynamic_plan(periodic_db)
    plan["periodic_attack_sources"][0]["selection"].update(
        constellation=1, talent_level=13)
    output = run_plan_calc(confirm_periodic(plan, periodic_db), periodic_db)
    record = output["result"]["action_event_compilations"][0]
    assert record["status"] == "ready"
    assert {channel["parameter"]["level"] for channel in record["scope"]["channels"]} == {13}
    assert len(output["result"]["hits"]) == 15


def test_dynamic_multi_accepts_pyro_projection_without_role_specific_branch(
        periodic_db, fake_repo):
    from tests.test_runtime_periodic_attack import SOURCE, change

    def set_pyro(rows):
        for row in rows:
            raw = row.get("Default", {})
            for modifier in raw.get("GPPBBDEICKP", {}).values():
                for operation in modifier.get("FMBHOKCHKOH", []):
                    attack = (operation.get("IDCIIIFMDCM") or {}).get("LGHDGGJIJOK")
                    if isinstance(attack, dict) and isinstance(attack.get("EHKJIDFINEK"), dict):
                        attack["EHKJIDFINEK"]["EPPIDAILPBN"] = "Fire"

    change(periodic_db, fake_repo, SOURCE, set_pyro)
    plan = dynamic_plan(periodic_db)
    output = run_plan_calc(confirm_periodic(plan, periodic_db), periodic_db)
    assert "pyro" in {hit["element"] for hit in output["plan"]["resolved_template"]["hits"]}


def test_dynamic_kind_rejects_even_null_contact_and_legacy_freeze_output_is_unchanged(periodic_db):
    dynamic = dynamic_plan(periodic_db)
    dynamic["action_event_profiles"][0]["channels"][0]["contact_profile_id"] = None
    with pytest.raises(PlanError):
        validate_action_event_fields(dynamic)

    legacy = run_plan_calc(confirm_periodic(periodic_plan(periodic_db), periodic_db), periodic_db)
    record = legacy["result"]["action_event_compilations"][0]
    assert record["kind"] == KIND
    assert record["compiler"] == "calibrated-action-events-v1"
    assert record["scope"]["ruleset"] == "gauge-freeze-reference-v1"
    assert all(pattern["contact"] == {"kind": "profile", "profile_id": "safe"}
               for pattern in legacy["report"]["mechanics_model"]["hit_patterns"])


def test_legacy_acceptance_fingerprint_cannot_authorize_dynamic_kind(periodic_db):
    legacy = periodic_plan(periodic_db)
    old_confirmation = compose_equipment_plan(legacy, periodic_db)["action_event_compilations"][0][
        "required_confirmation"]
    plan = dynamic_plan(periodic_db)
    plan["action_event_profiles"][0].update(old_confirmation)
    preview = compose_equipment_plan(plan, periodic_db)
    record = preview["action_event_compilations"][0]
    assert record["status"] == "pending"
    assert record["profile_fingerprint"] != old_confirmation["accepted_profile_fingerprint"]
    assert record["generated"] == []


def test_mixed_dynamic_and_legacy_multi_profiles_withdraw_atomically(periodic_db):
    plan = confirm_periodic(dynamic_plan(periodic_db), periodic_db)
    dynamic = plan["action_event_profiles"][0]
    legacy = copy.deepcopy(dynamic)
    legacy.update(id="legacy.profile", kind=KIND, accepted_profile_fingerprint=None)
    for channel in legacy["channels"]:
        old_id = channel["channel_id"]
        channel.update(channel_id="legacy." + old_id,
                       prototype_hit_id="legacy." + channel["prototype_hit_id"],
                       application_profile_id="legacy." + channel["application_profile_id"],
                       contact_profile_id="safe")
    plan["action_event_profiles"].append(legacy)
    plan["action_event_bindings"].append({"ability_id": "legacy.q", "profile_id": legacy["id"]})
    source = copy.deepcopy(plan["periodic_attack_sources"][0])
    source["action_event_profile_id"] = legacy["id"]
    for channel in source["channels"]:
        channel["channel_id"] = "legacy." + channel["channel_id"]
    plan["periodic_attack_sources"].append(source)
    for template in (plan["manual_template"],):
        template["hits"].extend({**copy.deepcopy(hit), "id": "legacy." + hit["id"]}
                                for hit in list(template["hits"]))
    rotation = plan["rotation_model"]
    rotation["abilities"].append({**copy.deepcopy(rotation["abilities"][0]), "id": "legacy.q"})
    rotation["actions"].append(action("legacy.cast", "legacy.q", start=5, end=5.15,
                                      repeat={"mode": "once"}))
    rotation["events"].append({"id": "legacy.spend", "type": "energy_spend", "action_id": "legacy.cast",
        "character": "a", "t": 5, "order": 1, "required": 0, "amount": 0,
        "repeat": {"mode": "once"}, **EVIDENCE})
    preview = compose_equipment_plan(plan, periodic_db)
    records = preview["action_event_compilations"]
    assert {record["kind"] for record in records} == {APPLICATION_KIND, KIND}
    assert all(record["status"] == "pending" and record["generated"] == [] for record in records)
    assert any(issue["code"] == "periodic_action_group_pending"
               for record in records for issue in record["issues"])
    assert preview["mechanics_model"]["hit_patterns"] == []
    assert action_event_receipt_compiler(records) == "calibrated-action-events-v2"
