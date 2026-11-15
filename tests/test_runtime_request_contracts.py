"""The contracts read by Agent are the shapes validated before DB work."""
from __future__ import annotations

import copy

import pytest

from genshin_db.runtime_queries import runtime_capabilities
from genshin_db.runtime_request_contracts import (
    ACTION_FIELDS,
    REQUIRED,
    RuntimeRequestError,
    runtime_action_schema,
    runtime_contracts,
    validate_runtime_request,
)


def test_all_runtime_actions_have_the_same_fields_and_required_contract():
    capabilities = runtime_capabilities()
    contracts = runtime_contracts()
    assert set(capabilities["actions"]) == set(contracts["actions"]) == set(ACTION_FIELDS)
    for name, value in contracts["actions"].items():
        schema = value["input_schema"]
        assert set(schema["properties"]) == ACTION_FIELDS[name] | {"action", "version_line", "snapshot_id"}
        assert schema["required"] == ["action", *REQUIRED.get(name, [])]
    changed = runtime_action_schema("action_level_context")
    changed["properties"].clear()
    assert "character_id" in runtime_action_schema("action_level_context")["properties"]


@pytest.mark.parametrize("name", ["action_admission_model_candidates", "action_capacity_model_candidates"])
def test_catalog_example_is_accepted_by_actual_semantic_validator(name):
    from genshin_db import runtime_action_admission, runtime_action_capacity
    request = runtime_contracts()["actions"][name]["example"]
    assert validate_runtime_request(request) == request
    module = runtime_action_admission if name.startswith("action_admission") else runtime_action_capacity
    assert module._validate_catalog_request(request["request"])


@pytest.mark.parametrize(("payload", "path"), [
    ({"request": {"kinds": ["candidate"]}}, "/action"),
    ({"action": "whiteboard_action_admission_model_candidates"}, "/action"),
    ({"action": "action_level_context"}, "/character_id"),
    ({"action": "action_level_context", "character_id": True}, "/character_id"),
    ({"action": "action_admission_model_candidates", "character_id": 1}, "/request"),
])
def test_wrong_calls_point_to_correct_contract_without_accessing_database(payload, path):
    with pytest.raises(RuntimeRequestError) as caught:
        validate_runtime_request(payload)
    assert caught.value.details["path"] == path
    assert caught.value.details["recovery"]["action"] == "correct_arguments"
    assert caught.value.details["contract_ref"]["resource"] == "runtime_contracts"


def test_opaque_reference_ids_are_text_not_integer_ids():
    request = {"action": "energy_generation_profile", "origin_reference_id": "a" * 64}
    assert validate_runtime_request(copy.deepcopy(request)) == request


@pytest.mark.parametrize(("action", "error_type", "code"), [
    ("action_capacity_model_candidates", "capacity", "capacity_catalog_query"),
    ("action_admission_model_candidates", "admission", "action_admission_catalog_query"),
])
def test_catalog_preflight_preserves_existing_typed_failure_and_adds_field_guidance(action, error_type, code):
    from genshin_db.runtime_action_admission import ActionAdmissionError
    from genshin_db.runtime_capacity_delta import CapacityError

    request = runtime_contracts()["actions"][action]["example"]
    request["request"]["page_size"] = True
    expected_type = CapacityError if error_type == "capacity" else ActionAdmissionError
    with pytest.raises(expected_type) as caught:
        validate_runtime_request(request)
    assert caught.value.code == caught.value.reason == code
    assert caught.value.details["path"] == "/request/page_size"
    assert caught.value.details["contract_ref"]["pointer"] == "/actions/" + action
    assert caught.value.details["recovery"]["action"] == "correct_arguments"
