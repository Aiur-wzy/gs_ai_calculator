"""Technical hashes never grant game-fact authority or mutate App state."""
from __future__ import annotations

import copy
import hashlib
import json

import pytest

from genshin_app.agent_chat import agent_hash_input as service
from genshin_app.agent_chat import tools
from genshin_app.agent_chat.native_app_tool_host import (
    _derived_view,
    _durable_candidate_view,
)
from genshin_app.agent_chat.native_read_dispatch import build_native_read_snapshot
from genshin_app.equipment.schema import PlanError
from genshin_engine.special_reaction_profiles import (
    direct_lunar_damage_profile_content_hash,
    direct_stellar_damage_profile_content_hash,
    validate_direct_lunar_damage_profile,
    validate_direct_stellar_damage_profile,
)
from tests.test_direct_stellar_damage_contract import direct_stellar_profile
from tests.test_native_app_tool_host import Session, call, host
from tests.test_special_reactions import direct_lunar_profile


def invoke(kind, value):
    return service.hash_input(kind, value, maximum_bytes=tools.MAX_ARGUMENTS)


@pytest.mark.parametrize("value", [None, True, 1, 1.25, "冰", [], {"冰": [1.25, None]}])
def test_canonical_json_hashes_the_same_finite_json_without_changing_it(value):
    before = copy.deepcopy(value)
    expected = hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
        separators=(",", ":"), allow_nan=False).encode("utf-8")).hexdigest()
    result = invoke("canonical_json", value)
    assert result == {"kind": "canonical_json", "algorithm": "sha256",
        "content_hash": expected, "excluded_fields": [],
        "game_facts_verified": False, "authority": "none"}
    assert value == before


@pytest.mark.parametrize("kind,factory,compute", [
    ("direct_stellar_damage_profile", direct_stellar_profile, direct_stellar_damage_profile_content_hash),
    ("direct_lunar_damage_profile", direct_lunar_profile, direct_lunar_damage_profile_content_hash),
])
def test_direct_profiles_use_the_existing_public_hash_and_exclude_only_the_self_hash(kind, factory, compute):
    value = factory()
    value["profile_content_hash"] = "old-self-field-is-excluded"
    before = copy.deepcopy(value)
    result = invoke(kind, value)
    assert result["content_hash"] == compute(value)
    assert result["excluded_fields"] == ["profile_content_hash"]
    assert result["authority"] == "none" and result["game_facts_verified"] is False
    assert value == before
    changed = copy.deepcopy(value)
    changed["source"] += " independently changed evidence"
    assert invoke(kind, changed)["content_hash"] != result["content_hash"]


@pytest.mark.parametrize("kind,factory,validate", [
    ("direct_stellar_damage_profile", direct_stellar_profile, validate_direct_stellar_damage_profile),
    ("direct_lunar_damage_profile", direct_lunar_profile, validate_direct_lunar_damage_profile),
])
def test_computing_a_hash_cannot_confirm_an_unreviewed_game_profile(kind, factory, validate):
    value = factory(status="confirmed")
    before = copy.deepcopy(value)
    result = invoke(kind, value)
    assert value == before
    submitted = {**value, "profile_content_hash": result["content_hash"]}
    assert any(row["code"] == "reviewed_profile" for row in validate(submitted))
    assert result["game_facts_verified"] is False and result["authority"] == "none"


@pytest.mark.parametrize("kind,value", [
    ("unknown", {}), ([], {}), ("canonical_json", float("nan")),
    ("canonical_json", {"nested": float("inf")}), ("canonical_json", {1: "non-json key"}),
    ("canonical_json", {"set": {1}}), ("canonical_json", (1, 2)),
    ("direct_stellar_damage_profile", []), ("direct_lunar_damage_profile", None),
])
def test_invalid_kind_or_non_json_input_is_rejected(kind, value):
    with pytest.raises(PlanError):
        invoke(kind, value)


def test_cyclic_value_and_existing_argument_capacity_are_enforced():
    value = {}
    value["self"] = value
    with pytest.raises(PlanError):
        invoke("canonical_json", value)
    with pytest.raises(PlanError) as error:
        invoke("canonical_json", "x" * tools.MAX_ARGUMENTS)
    assert error.value.reason == "agent_size_limit"
    assert len(invoke("canonical_json", "x" * (tools.MAX_ARGUMENTS - 200))["content_hash"]) == 64


def test_host_hash_tool_is_published_pure_read_and_has_no_domain_side_effects(tmp_path):
    session = Session()
    before = copy.deepcopy({"derived": _derived_view(session), "durable": _durable_candidate_view(session)})
    current = host(tmp_path, session, lambda s, name, arguments: tools.call_tool(s, name, arguments))
    snapshot = build_native_read_snapshot(session)
    assert snapshot["read_policies"]["gs_hash_input"] == "pure_read"
    assert current.snapshot["policies"]["gs_hash_input"] == {"policy": "domain_read", "effect": "none"}
    terminal = call(current, tool="gs_hash_input", raw='{"kind":"canonical_json","value":{"冰":1.25}}')
    assert terminal["terminal_state"] == "settled_success"
    assert terminal["state_changes"] == [] and terminal["disallowed_state_changes"] == []
    assert terminal["result"]["data"]["game_facts_verified"] is False
    assert {"derived": _derived_view(session), "durable": _durable_candidate_view(session)} == before
    assert session.resources == {} and session.findings == []


def test_hash_tool_schema_is_value_only_and_disallows_paths_or_extra_options():
    schema = tools.SPECS["gs_hash_input"][1]
    assert set(schema["properties"]) == set(schema["required"]) == {"kind", "value"}
    assert schema["additionalProperties"] is False
    assert schema["properties"]["kind"]["enum"] == [
        "canonical_json", "direct_stellar_damage_profile", "direct_lunar_damage_profile"]
    assert [branch["type"] for branch in schema["properties"]["value"]["anyOf"]] == [
        "object", "array", "string", "number", "boolean", "null"]
    session = Session()
    result = tools.call_tool(session, "gs_hash_input", {"kind": "canonical_json", "value": {}, "path": "ignored"})
    assert result["code"] == 1 and result["reason"] == "agent_tool_input"
