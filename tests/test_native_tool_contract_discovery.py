"""On-demand discovery follows native catalogs without granting authority."""
from __future__ import annotations

from genshin_app.agent_chat.native_app_tool_catalog import (
    build_native_app_tool_snapshot,
)
from genshin_app.agent_chat.native_tool_contracts import (
    capability_index,
    tool_contracts,
)
from tests.test_agent_artifact_workflow import board as board  # noqa: PLC0414


def test_contracts_match_actual_host_catalog_and_all_focuses(board):
    discovered = tool_contracts(board)["tools"]
    actual = build_native_app_tool_snapshot(board, document_id=board.document["board_id"],
        database_fingerprint="a" * 64, tool_manifest_hash="b" * 64,
        runtime_generation="dsh_v2", writer_fence="contract-fence")
    assert set(discovered) == {row["name"] for row in actual["tools"]}
    for row in actual["tools"]:
        assert discovered[row["name"]]["input_schema"] == row["inputSchema"]
        assert discovered[row["name"]]["effect"] == actual["policies"][row["name"]]["effect"]
    assert "gs_weapon_stats" in discovered
    assert "gs_reference_panel" in discovered
    assert capability_index(board)["domain_tools"]


def test_discovery_uses_normal_read_context_pointer_and_no_resources_mutation(board):
    before = dict(board.resources)
    result = board.call_tool("gs_read_context", {"resource": "runtime_contracts",
        "pointer": "/actions/action_admission_model_candidates/input_schema/properties/request"})
    assert result["code"] == 0, result
    assert "contract" in str(result["data"])
    assert board.resources == before and board.revision == 0
    wrong = board.call_tool("gs_query_runtime", {"request": {"kinds": ["candidate"]}})
    assert wrong["code"] == 1 and wrong["category"] == "request_error"
    assert wrong["recovery"]["action"] == "correct_arguments"
    assert wrong["contract_ref"]["pointer"] == "/tools/gs_query_runtime"


def test_imported_resource_is_only_reverified_by_actual_query_and_gaps_do_not_overwrite(board):
    from genshin_app.agent_chat.tools import _query
    from genshin_app.equipment.files import fingerprint

    board.resources["query-3"] = {"value": 1}
    calls = []

    def fetch(*_):
        calls.append(True)
        return {"value": 1}

    same = _query(board, {"request": {"characters": []}}, fetch, "context")
    assert same["resource"] == "query-3" and len(calls) == 1
    assert board.stage_state["resource_descriptors"]["query-3"]["fingerprint"] == fingerprint({"value": 1})
    different = _query(board, {"request": {"characters": []}}, lambda *_: {"value": 1.0}, "context")
    assert different["resource"] != "query-3"
    assert type(board.resources["query-3"]["value"]) is int
    descriptor = board.stage_state["resource_descriptors"][different["resource"]]
    assert descriptor["fingerprint"] == fingerprint(board.resources[different["resource"]])


def test_native_capability_pointer_recovers_from_independent_cli_wrapper(board):
    navigation = capability_index(board)["plan_contract"]
    assert navigation["native_details"]["references"]["engine_registry"] == {
        "resource": "capabilities", "pointer": "/engine_v2"}
    wrong = board.call_tool("gs_read_context", {"resource": "capabilities",
        "pointer": "/data/plan_contract/engine_v2"})
    assert wrong["code"] == 1
    assert wrong["details"]["path"] == "/data"
    assert wrong["details"]["recovery"]["pointer"] == ""
    assert wrong["details"]["recovery"]["resource"] == "capabilities"


def test_context_pointer_length_has_actionable_bound(board):
    reply = board.call_tool("gs_read_context", {"resource": "runtime_contracts", "pointer": "/" + "x" * 4200})
    assert reply["reason"] == "agent_size_limit"
    assert reply["details"] == {"path": "/pointer", "actual": 4201, "maximum": 4096, "unit": "characters"}


def test_large_context_error_does_not_reference_a_nonexistent_report(board):
    reply = board.call_tool("gs_read_context", {"resource": "runtime_contracts", "pointer": "/" + "🙂" * 4050})
    assert reply["reason"] == "agent_tool_input"
    assert reply["details"]["detail_omitted"]
    assert reply["recovery"]["resource"] == "runtime_contracts"
    assert reply["recovery"]["pointer"] == ""
    assert reply["details"].get("resource") != "report"


def test_native_context_pagination_bounds_actual_utf8_and_recovers_child_reference(board):
    from genshin_app.agent_chat.native_tool_receipt import DIRECT_RESULT_MAX_BYTES
    from genshin_app.equipment.files import json_bytes

    values = ["词条\\\"" * 1200] * 9 + ["附录" * 26000]
    board.resources["query-123"] = {"a/b~c": values}
    found, offset = [], 0
    while offset is not None:
        response = board.call_tool("gs_read_context", {"resource": "query-123",
            "pointer": "/a~1b~0c", "offset": offset, "limit": 50})
        assert response["code"] == 0, response
        assert len(json_bytes(response)) <= DIRECT_RESULT_MAX_BYTES
        offset = response["data"]["next_offset"]
        found.extend(response["data"]["items"])
    assert [row["key"] for row in found] == list(range(len(values)))
    assert [row["value"] for row in found[:-1]] == values[:-1]
    reference = found[-1]["value"]["reference"]
    assert reference["pointer"] == "/a~1b~0c/9"
    text_parts, offset = [], 0
    while offset is not None:
        response = board.call_tool("gs_read_context", {**reference, "offset": offset})
        assert response["code"] == 0, response
        assert len(json_bytes(response)) <= DIRECT_RESULT_MAX_BYTES
        offset = response["data"]["next_offset"]
        text_parts.append(response["data"]["text"])
    assert "".join(text_parts) == values[-1]


def test_large_compose_receipt_keeps_status_and_precise_detail_references(board):
    from genshin_app.agent_chat.native_tool_receipt import DIRECT_RESULT_MAX_BYTES
    from genshin_app.equipment.files import json_bytes

    reply = board.compact_tool_response({"revision": 5, "representation": "native",
        "calculation_acceptance": {"status": "pending"},
        "task_capability": {"status": "blocked", "counts": {"total": 1}, "issues": ["长问题" * 20000]},
        "event_topology_coverage_receipt": {"payload": "长说明" * 10000}})
    assert len(json_bytes({"code": 0, "data": reply})) <= DIRECT_RESULT_MAX_BYTES
    assert reply["revision"] == 5 and reply["calculation_acceptance"]["status"] == "pending"
    assert reply["task_capability"]["counts"] == {"total": 1}
    assert reply["additional_details"] == {
        "event_topology_coverage_receipt": {"resource": "event_topology_receipt", "pointer": ""},
        "task_capability": {"resource": "report", "pointer": "/missing_inputs"}}
