"""Native writer descriptions never advertise compiler-only engine fields."""
from __future__ import annotations

import copy
import json
import uuid

import pytest

from genshin_app.agent_chat.artifact_candidate import ArtifactCandidateSession
from genshin_app.cli import main
from genshin_app.equipment import validate_plan
from genshin_app.equipment.action_admission_schema import (
    validate_action_admission_bindings,
)
from genshin_app.equipment.action_capacity_schema import validate_capacity_bindings
from genshin_app.equipment.action_resources import validate_action_resource_bindings
from genshin_app.equipment.plan_description import (
    app_mechanics_capabilities,
    native_mechanics_schema,
    plan_description_schema,
)
from genshin_app.equipment.rotation_authoring import (
    ACTION_ADMISSION_SOURCE,
    CAPACITY_SOURCE,
    COMPILER_OWNED_PATHS,
    ENERGY_DELIVERY,
    app_rotation_capabilities,
    has_compiler_field,
)
from genshin_app.equipment.schema import PLAN_SCHEMA, PlanError
from genshin_engine.rotation import (
    _schema_errors,
    check_rotation,
    rotation_capabilities,
)
from tests.test_action_resources_app import resource_plan
from tests.test_agent_artifact_workflow import native_plan
from tests.test_attribute_app_sources import attribute_plan, known_classification
from tests.test_automatic_energy_delivery import automatic_fixture


def test_native_schema_excludes_exact_guard_paths_and_keeps_engine_contract_unchanged():
    original = rotation_capabilities()
    actual = app_rotation_capabilities()
    assert actual["schema"] == original["schema"]
    assert actual["schema_scope"] == "engine_execution_and_independent_rotation_check"
    contract = actual["native_authoring"]
    assert contract["scope"] == "native_plan.rotation_model"
    assert contract["contract_id"] == "native-rotation-authoring-v1"
    schema = copy.deepcopy(original["schema"])
    ability = schema["properties"]["abilities"]["items"]["properties"]
    del ability["energy"]["oneOf"][0]["properties"]["delivery"]
    del ability["action_admission_source"]
    del ability["cooldown"]["oneOf"][0]["properties"]["capacity_source"]
    schema["title"] = contract["schema"]["title"]
    assert contract["schema"] == schema
    assert {row["path"] for row in contract["compiler_owned_fields"]} == {
        "abilities[]." + ".".join(path) for path in COMPILER_OWNED_PATHS}
    contract["schema"]["properties"]["abilities"]["items"]["properties"].clear()
    assert rotation_capabilities() == original


def test_full_native_description_uses_the_same_projection_without_mutating_runtime_schema():
    original = copy.deepcopy(PLAN_SCHEMA)
    standalone = app_rotation_capabilities()["native_authoring"]["schema"]
    full = plan_description_schema()
    assert full["properties"]["rotation_model"]["anyOf"] == [standalone, {"type": "null"}]
    assert PLAN_SCHEMA == original


def test_native_mechanics_and_attribute_description_use_author_shape_with_existing_hard_ceilings():
    from genshin_engine.attribute_budget import attribute_budget_capabilities
    from genshin_engine.attribute_contract import attribute_model_schema
    from genshin_engine.mechanics_schema import MECHANICS_SCHEMA, mechanics_capabilities

    original_schema, original_caps = copy.deepcopy(MECHANICS_SCHEMA), mechanics_capabilities()
    native = native_mechanics_schema()
    budget = attribute_budget_capabilities()
    assert "execution_options" not in native["properties"]
    assert "calculation_options" in native["description"]
    assert native["properties"]["attribute_model"] == attribute_model_schema(author=True, limits=budget["hard_limits"])
    assert native["x-genshin-authoring-budget"]["schema_limits"] == "absolute_hard_limits_not_current_effective"
    assert native["x-genshin-authoring-budget"]["defaults"] == budget["defaults"]
    assert native["x-genshin-authoring-budget"]["hard_limits"] == budget["hard_limits"]
    app = app_mechanics_capabilities()
    assert app["schema"] == original_caps["schema"]
    assert app["schema_scope"] == "engine_execution"
    assert app["native_authoring"]["schema"] == native
    assert app["attribute_ledger"]["native_schema"] == original_caps["attribute_ledger"]["native_schema"]
    assert app["attribute_ledger"]["native_schema_scope"] == "engine_normalized_execution"
    assert app["attribute_ledger"]["author_schema"] == native["properties"]["attribute_model"]
    assert MECHANICS_SCHEMA == original_schema and mechanics_capabilities() == original_caps


def test_native_description_allows_existing_author_preview_but_rejects_private_execution_options():
    from genshin_engine.mechanics_schema import MECHANICS_SCHEMA

    plan = attribute_plan()
    row = known_classification(plan)
    row.pop("component")
    plan["mechanics_model"]["attribute_model"]["effect_classifications"] = [row]
    validate_plan(plan)
    issues, engine_issues = [], []
    _schema_errors(plan["mechanics_model"], native_mechanics_schema(), "mechanics_model", issues)
    _schema_errors(plan["mechanics_model"], MECHANICS_SCHEMA, "mechanics_model", engine_issues)
    assert issues == [] and engine_issues
    plan["mechanics_model"]["execution_options"] = {"schema_version": 1}
    issues = []
    _schema_errors(plan["mechanics_model"], native_mechanics_schema(), "mechanics_model", issues)
    assert issues and "execution_options" in str(issues)
    with pytest.raises(PlanError, match="calculation_options"):
        validate_plan(plan)


@pytest.mark.parametrize("mode", ["default", "expanded", "lowered", "over_hard"])
def test_native_generic_budget_does_not_replace_runtime_effective_options(mode):
    from genshin_engine.attribute_budget import DEFAULT_LIMITS, HARD_LIMITS

    plan = attribute_plan()
    default, hard = DEFAULT_LIMITS["initials"], HARD_LIMITS["initials"]
    count = 1 if mode == "default" else default + 1
    if mode == "expanded":
        plan["calculation_options"] = {"schema_version": 1, "attribute_limits": {"initials": count}}
    elif mode == "lowered":
        count = 3
        plan["calculation_options"] = {"schema_version": 1, "attribute_limits": {"initials": 2}}
    elif mode == "over_hard":
        plan["calculation_options"] = {"schema_version": 1, "attribute_limits": {"initials": hard + 1}}
    basis = plan["mechanics_model"]["attribute_model"]["initial_basis"][0]
    plan["mechanics_model"]["attribute_model"]["initial_basis"] = [
        {**copy.deepcopy(basis), "actor": "actor." + str(index)} for index in range(count)]
    schema = native_mechanics_schema()["properties"]["attribute_model"]
    issues = []
    _schema_errors(plan["mechanics_model"]["attribute_model"], schema, "attribute_model", issues)
    assert issues == []  # General shape intentionally does not claim effective admission.
    if mode in ("default", "expanded"):
        validate_plan(plan)
    else:
        with pytest.raises(PlanError) as rejected:
            validate_plan(plan)
        if mode == "lowered":
            assert rejected.value.reason == "attribute_declaration_limit"
    if mode == "expanded":
        plan.pop("calculation_options")
        with pytest.raises(PlanError) as rejected:
            validate_plan(plan)
        assert rejected.value.reason == "attribute_declaration_limit"
        model = copy.deepcopy(plan["mechanics_model"]["attribute_model"])
        model["initial_basis"] = [{**copy.deepcopy(basis), "actor": "hard." + str(index)} for index in range(hard + 1)]
        issues = []
        _schema_errors(model, schema, "attribute_model", issues)
        assert issues


def test_independent_engine_accepts_private_delivery_but_native_schema_does_not():
    template, rotation, _ = automatic_fixture()
    caps = app_rotation_capabilities()
    engine_issues, native_issues = [], []
    _schema_errors(rotation, caps["schema"], "rotation_model", engine_issues)
    _schema_errors(rotation, caps["native_authoring"]["schema"], "rotation_model", native_issues)
    assert engine_issues == []
    assert native_issues and "delivery" in str(native_issues)
    checked = check_rotation(template, rotation)
    assert checked["characters"][0]["final_energy"] == 0
    assert checked["automatic_energy_delivery"]["committed_actions"] == 1


@pytest.mark.parametrize("path,guard,reason", [
    (ENERGY_DELIVERY, validate_action_resource_bindings, "private_execution_field"),
    (ACTION_ADMISSION_SOURCE, validate_action_admission_bindings, "action_admission_ownership"),
    (CAPACITY_SOURCE, validate_capacity_bindings, "invalid_plan"),
])
@pytest.mark.parametrize("marker", [None, {}])
def test_guard_presence_and_old_rejection_code_are_preserved_with_precise_recovery(path, guard, reason, marker):
    plan = resource_plan()
    ability = plan["rotation_model"]["abilities"][0]
    value = ability
    for key in path[:-1]:
        value = value.setdefault(key, {})
    value[path[-1]] = marker
    assert has_compiler_field(ability, path)
    with pytest.raises(PlanError) as error:
        guard(plan)
    assert error.value.reason == reason
    details = error.value.details
    assert details["ability_id"] == "q"
    assert details["native_field_path"] == "/rotation_model/abilities/0/" + "/".join(path)
    assert details["schema_reference"].endswith("rotation_contract.native_authoring.schema")
    if path == ENERGY_DELIVERY:
        assert details["path"] == "q"
        assert details["native_source"] == "action_resource_sources[].energy_delivery"
        assert "energy_spend" in details["recovery"]


def test_public_manual_fallback_still_validates_without_any_automatic_binding():
    plan = resource_plan()
    plan.pop("action_resource_sources")
    ability = plan["rotation_model"]["abilities"][0]
    ability["cooldown"].update(duration=15, charges=1)
    ability["energy"].update(required=60, amount=60)
    plan["rotation_model"]["events"][0].update(required=60, amount=60)
    plan["rotation_model"]["action_hit_links"] = [
        {"action_id": "cast", "hit_id": hit["id"]} for hit in plan["manual_template"]["hits"]]
    frozen = copy.deepcopy(plan)
    validate_plan(plan)
    checked = check_rotation(plan["manual_template"], plan["rotation_model"])
    assert plan == frozen
    assert checked["characters"][0]["final_energy"] == 0
    assert "automatic_energy_delivery" not in checked
    schema = app_rotation_capabilities()["native_authoring"]["schema"]
    issues = []
    _schema_errors(plan["rotation_model"], schema, "rotation_model", issues)
    assert issues == []


def test_real_cli_and_native_gs_read_share_the_production_authoring_contract(tmp_path, capsys):
    assert main(["capabilities"]) == 0
    plan_contract = json.loads(capsys.readouterr().out)["data"]["plan_contract"]
    cli = plan_contract["rotation_contract"]
    session = ArtifactCandidateSession(str(uuid.uuid4()), tmp_path / "session", tmp_path / "absent.db",
                                       "native", native_plan())
    before = copy.deepcopy(session.candidate)
    for key in ("schema_scope", "native_authoring"):
        response = session.call_tool("gs_read_context", {
            "resource": "capabilities", "pointer": "/rotation_contract/" + key,
            "offset": 0, "limit": 50})
        assert response["code"] == 0, response
        page = response["data"]
        if key == "schema_scope":
            assert page["text"] == cli[key]
        else:
            assert page["next_offset"] is None
            fields = {row["key"]: row["value"] for row in page["items"]}
            for field, value in fields.items():
                if isinstance(value, dict) and "reference" in value:
                    assert value["reference"]["pointer"] == "/rotation_contract/native_authoring/" + field
                else:
                    assert value == cli[key][field]
    assert session.candidate == before and session.revision == 0
    assert not (tmp_path / "absent.db").exists()

    # Follow the actual /schema route advertised by capability_index, including
    # bounded nested references and pages; compare the whole native schema.
    def read_whole(pointer, resource="capabilities"):
        response = session.call_tool("gs_read_context", {
            "resource": resource, "pointer": pointer, "offset": 0, "limit": 50})
        assert response["code"] == 0, response
        page = response["data"]
        if page["kind"] == "scalar":
            return page["value"]
        if page["kind"] == "text":
            assert page["next_offset"] is None
            return page["text"]
        entries = list(page["items"])
        while page["next_offset"] is not None:
            response = session.call_tool("gs_read_context", {
                "resource": resource, "pointer": pointer,
                "offset": page["next_offset"], "limit": 50})
            assert response["code"] == 0, response
            page = response["data"]
            entries.extend(page["items"])
        values = {}
        for entry in entries:
            value = entry["value"]
            if isinstance(value, dict) and "reference" in value:
                assert value["reference"]["resource"] == resource
                value = read_whole(value["reference"]["pointer"], resource)
            values[entry["key"]] = value
        return list(values.values()) if page["kind"] == "array" else values

    full_native_schema = read_whole("/schema")
    assert full_native_schema == plan_contract["schema"]
    assert full_native_schema["properties"]["rotation_model"]["anyOf"][0] == cli["native_authoring"]["schema"]
    mechanics = plan_contract["mechanics_contract"]
    assert read_whole("/native_authoring/schema", "mechanics_capabilities") == mechanics["native_authoring"]["schema"]
    assert mechanics["native_authoring"]["schema"] == full_native_schema["properties"]["mechanics_model"]["anyOf"][0]
    assert read_whole("/schema_scope", "mechanics_capabilities") == "engine_execution"
    assert read_whole("/attribute_ledger/author_schema", "mechanics_capabilities") == mechanics["attribute_ledger"]["author_schema"]
    # Published native examples do not carry compiler-only ability fields.
    for example in plan_contract["examples"].values():
        for ability in (example.get("rotation_model") or {}).get("abilities", []):
            assert all(not has_compiler_field(ability, path) for path in COMPILER_OWNED_PATHS)
    assert session.candidate == before and session.revision == 0
