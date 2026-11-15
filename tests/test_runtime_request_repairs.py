"""Production request/schema parity, including corrected calls after failures."""
import copy

import pytest

from genshin_db.runtime_queries import query_runtime, runtime_capabilities
from genshin_db.runtime_request_contracts import (
    EXCLUSIVE_SKILL_SELECTORS,
    RuntimeRequestError,
    runtime_action_schema,
    validate_runtime_request,
)


def test_mechanism_page_range_is_owned_by_the_actual_handler():
    from genshin_db.runtime_mechanism import CATALOG_PAGINATION
    field = runtime_action_schema("mechanism_catalog")["properties"]["page_size"]
    assert field["default"] == CATALOG_PAGINATION["default_page_size"] == 20
    assert field["maximum"] == CATALOG_PAGINATION["max_page_size"] == 50
    assert runtime_capabilities()["mechanism_semantics"]["pagination"] == CATALOG_PAGINATION
    with pytest.raises(RuntimeRequestError) as caught:
        query_runtime("does-not-exist.db", {"action": "mechanism_catalog", "page_size": 100})
    assert caught.value.details["path"] == "/page_size"
    assert {key: caught.value.details[key] for key in ("actual", "minimum", "maximum", "default")} == {
        "actual": 100, "minimum": 1, "maximum": 50, "default": 20}
    assert validate_runtime_request({"action": "mechanism_catalog", "page_size": 50})


@pytest.mark.parametrize("action", sorted(EXCLUSIVE_SKILL_SELECTORS))
def test_skill_selectors_are_publicly_mutually_exclusive_before_any_db(action):
    schema = runtime_action_schema(action)
    assert schema["oneOf"] == [{"required": ["slot"]}, {"required": ["skill_id"]}]
    payload = {"action": action, "character_id": 1, "slot": "skill", "skill_id": 10}
    if "param_index" in schema["required"]:
        payload["param_index"] = 0
    before = copy.deepcopy(payload)
    with pytest.raises(RuntimeRequestError) as caught:
        query_runtime("does-not-exist.db", payload)
    assert payload == before and caught.value.details["path"] == "/slot"
    assert caught.value.details["category"] == "request_error"
    payload.pop("skill_id")
    assert validate_runtime_request(payload) == payload


def test_dispatch_profile_still_requires_both_verified_identities():
    payload = {"action": "action_dispatch_profile", "character_id": 1, "skill_depot_id": 2,
        "skill_id": 3, "slot": "skill", "effective_level": 10, "constellation": 0, "variant": "tap"}
    assert validate_runtime_request(payload) == payload


def test_native_selector_error_is_not_reported_as_internal_fault(board):
    bad = board.call_tool("gs_query_runtime", {"request": {
        "action": "action_facts", "character_id": 1, "slot": "skill", "skill_id": 2}})
    assert bad["code"] == 1 and bad["category"] == "request_error"
    assert bad["reason"] == "runtime_request_invalid" and "system_fault" not in bad
    assert bad["path"] == "/slot"


from tests.test_agent_artifact_workflow import board as board  # noqa: PLC0414
