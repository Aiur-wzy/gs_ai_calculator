"""Complete non-executable topology contracts reach the real Artifact tools."""
from __future__ import annotations

import copy
import json
import uuid

import pytest

from genshin_app.agent_chat import tools
from genshin_app.agent_chat.artifact_candidate import ArtifactCandidateSession
from genshin_app.agent_chat.native_event_topology_contracts import (
    event_topology_contracts,
)
from genshin_app.agent_chat.native_tool_contracts import RESOURCE_NAMES
from genshin_app.equipment import new_plan
from genshin_app.equipment.event_topology import (
    SOURCE_DISPOSITIONS,
    event_topology_capabilities,
    validate_event_topology,
)
from genshin_app.equipment.event_topology_binding import (
    TARGET_ROOTS,
    lint_event_topology_bindings,
    validate_event_topology_bindings,
)
from genshin_app.equipment.files import fingerprint
from genshin_app.whiteboard import new_whiteboard
from tests.test_native_app_tool_host import call, host
from tests.test_native_dsh_gui_workflow_runner import calculation_fixture
from tests.test_queries import db_path as db_path  # noqa: PLC0414


def _object(page):
    assert page["kind"] == "object" and page["next_offset"] is None
    return {row["key"]: row["value"] for row in page["items"]}


def _read(session, resource, pointer=""):
    response = session.call_tool("gs_read_context", {"resource": resource, "pointer": pointer, "limit": 200})
    assert response["code"] == 0, response
    return response["data"]


def _state(session):
    return copy.deepcopy({name: getattr(session, name) for name in (
        "candidate", "revision", "resources", "stage_state", "provisional_authority", "report", "calculated")})


@pytest.fixture(params=["whiteboard", "native"])
def session(request, tmp_path):
    return ArtifactCandidateSession(uuid.uuid4().hex, tmp_path / "session", tmp_path / "absent.db", request.param,
        new_whiteboard() if request.param == "whiteboard" else new_plan())


def test_all_fixed_routes_come_from_actual_getters_and_topology_navigation_is_readable(session):
    before = _state(session)
    checkpoint = (session.root / "checkpoint.json").read_bytes()
    index = _object(_read(session, "capability_index"))
    expected = {*tools._fixed_context_getters(session), *RESOURCE_NAMES, *tools._CONTEXT_BOARD_RESOURCES,
                *tools._request_context_getters()}
    assert set(index["resources"]) == set(index["resource_refs"]) == expected
    assert index["fixed_resources_complete"] is True
    assert index["resources_exhaustive"] is False and index["resources_are_permission_allowlist"] is False
    for name, reference in index["resource_refs"].items():
        assert reference == {"resource": name, "pointer": ""}
    for reference in index["event_topology"].values():
        _read(session, **reference)
    usage = index["publication_usage"]
    failure = session.call_tool("gs_read_context", usage["read"])
    assert failure["reason"] == "database_missing"
    # Some named resources are legitimate but not generated/applicable yet.
    assert session.call_tool("gs_read_context", index["resource_refs"]["calculated"])["code"] == 1
    assert _state(session) == before
    assert (session.root / "checkpoint.json").read_bytes() == checkpoint
    assert not session.db_path.exists()


def test_new_fixed_getter_is_discovered_and_read_from_same_table_without_maintaining_second_list(session, monkeypatch):
    before = _state(session)
    original = tools._fixed_context_getters
    calls = []
    def added_getters(current):
        def future():
            calls.append(True)
            return {"future": "read-only"}
        return {**original(current), "future_contract": future}
    monkeypatch.setattr(tools, "_fixed_context_getters", added_getters)
    index = _object(_read(session, "capability_index"))
    assert "future_contract" in index["resources"] and calls == []
    assert _object(_read(session, **index["resource_refs"]["future_contract"])) == {"future": "read-only"}
    assert calls == [True] and _state(session) == before


def test_root_and_item_contracts_reuse_existing_components_and_match_factories():
    resource = event_topology_contracts()
    caps = event_topology_capabilities()
    assert resource["topology_schema"]["$defs"] == caps["component_json_schema"]["$defs"]
    assert set(resource["topology_schema"]["required"]) == set(resource["examples"]["draft"])
    assert set(resource["topology_schema"]["properties"]) == set(resource["topology_schema"]["required"])
    example = resource["examples"]["single_damage"]
    assert set(resource["execution_bindings_schema"]["required"]) == set(example["execution_bindings"])
    assert set(resource["execution_bindings_schema"]["properties"]) == set(resource["execution_bindings_schema"]["required"])
    item = resource["execution_bindings_schema"]["$defs"]["binding"]
    assert set(item["required"]) == set(example["execution_bindings"]["bindings"][0])
    assert set(item["properties"]) == set(item["required"])
    assert set(item["properties"]["target_kind"]["enum"]) == set(TARGET_ROOTS)
    assert set(resource["topology_schema"]["$defs"]["source"]["properties"]["disposition"]["enum"]) == SOURCE_DISPOSITIONS
    validate_event_topology(resource["examples"]["draft"])
    validate_event_topology(example["topology"])
    validate_event_topology_bindings(example["execution_bindings"])
    assert lint_event_topology_bindings(example["target_input_fragment"], example["topology"],
                                       example["execution_bindings"])["status"] == "complete"
    resource["topology_schema"]["$defs"]["source"]["properties"]["disposition"]["enum"].clear()
    assert event_topology_contracts()["topology_schema"]["$defs"] == caps["component_json_schema"]["$defs"]


@pytest.mark.parametrize("kind", ["action_relative", "instant", "action_start"])
def test_invented_schedule_values_remain_rejected_by_original_validator(kind):
    example = event_topology_contracts()["examples"]["single_damage"]
    example["topology"]["nodes"][1]["schedule"]["kind"] = kind
    with pytest.raises(ValueError, match="unsupported schedule"):
        validate_event_topology(example["topology"])


@pytest.mark.parametrize("disposition", ["materialized", "adopted"])
def test_invented_source_dispositions_remain_rejected_by_original_validator(disposition):
    document = event_topology_contracts()["examples"]["single_damage"]["topology"]
    document["sources"][0]["disposition"] = disposition
    with pytest.raises(ValueError, match="unsupported source disposition"):
        validate_event_topology(document)


def test_stale_or_leaf_execution_binding_never_becomes_complete():
    example = event_topology_contracts()["examples"]["single_damage"]
    for kind in ("stale", "leaf"):
        document = copy.deepcopy(example["execution_bindings"])
        row = document["bindings"][0]
        if kind == "stale":
            row["target_fingerprint"] = "0" * 64
        else:
            row["pointer"] += "/id"
            row["target_fingerprint"] = fingerprint(example["target_input_fragment"]["manual_template"]["hits"][0]["id"])
        assert lint_event_topology_bindings(example["target_input_fragment"], example["topology"], document)["status"] != "complete"


def test_discovered_example_can_be_adapted_to_actual_artifact_host_and_formal_engine_input(tmp_path, db_path):
    board, script = calculation_fixture(tmp_path / "fixture", db_path)
    session = ArtifactCandidateSession(uuid.uuid4().hex, tmp_path / "active", db_path, "whiteboard", board)
    current = host(tmp_path / "ledger", session, lambda s, name, args: s.call_tool(name, args))
    sequence = 0
    def run(tool, args):
        nonlocal sequence
        sequence += 1
        result = call(current, call_id=f"contract-{sequence}", tool=tool, raw=json.dumps(args, ensure_ascii=False))
        assert result["terminal_state"] == "settled_success", result
        assert result["result"]["code"] == 0, result
        return result["result"]["data"]
    run(script[0]["tool"], script[0]["arguments"])
    index = _object(run("gs_read_context", {"resource": "capability_index", "limit": 200}))
    examples = _object(run("gs_read_context", {**index["event_topology"]["examples"], "limit": 200}))
    example = examples["single_damage"]
    topology = example["topology"]
    actual = _object(run("gs_read_context", {"resource": "event_topology", "limit": 200}))
    topology["action_checkpoint"] = actual["action_checkpoint"]
    topology["dependencies"] = actual["dependencies"]
    topology["sources"][0]["owner_ref"] = "a"
    topology["nodes"][0]["schedule"]["anchor"]["ref_id"] = "e"
    patch = copy.deepcopy(script[1]["arguments"])
    hit = patch["operations"][0]["value"][0]
    bindings = example["execution_bindings"]
    bindings["topology_fingerprint"] = run("gs_hash_input", {"kind": "canonical_json", "value": topology})["content_hash"]
    bindings["bindings"][0]["target_fingerprint"] = run("gs_hash_input", {"kind": "canonical_json", "value": hit})["content_hash"]
    for operation in patch["operations"]:
        if operation["path"] == "/extensions/event_topology": operation["value"] = topology
        if operation["path"] == "/extensions/event_topology_bindings": operation["value"] = bindings
    run("gs_patch_candidate", patch)
    run(script[2]["tool"], script[2]["arguments"])
    run("gs_validate_topology", {"expected_revision": session.revision})
    run("gs_validate_execution_bindings", {"expected_revision": session.revision})
    run("gs_calculate", {"expected_revision": session.revision})
    assert session.calculation_acceptance()["status"] == "accepted"
    assert session.calculated["result"]["summary"]["total_damage"] > 0
