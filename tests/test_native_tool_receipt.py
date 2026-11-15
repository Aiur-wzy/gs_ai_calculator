"""Focused Phase 3C receipt tests; no provider, pipe, or GUI is used."""

from __future__ import annotations

import copy
import json

import pytest

from genshin_app.agent_chat import tools
from genshin_app.agent_chat.native_tool_receipt import (
    DIRECT_RESULT_MAX_BYTES,
    RECEIPT_CONTRACT,
    build_native_tool_receipt,
    canonical_receipt_bytes,
    sanitize_native_response,
)
from genshin_app.equipment.files import fingerprint, json_bytes


def _scope(**changes):
    value = {
        "session_scope_token": "b" * 64,
        "candidate_revision": 7,
        "stage_revision": 12,
        "tool_manifest_hash": "c" * 64,
    }
    value.update(changes)
    return value


class _Session:
    workflow = "staged"

    def __init__(self, *, phase="numeric_model", resources=None):
        self.workflow_stage = phase
        self.resources = copy.deepcopy(resources or {})
        self.stage_state = {
            "resource_stages": {},
            "resource_descriptors": {},
        }
        for resource_id, value in self.resources.items():
            self.register(resource_id, value, stage="numeric_model")

    def register(self, resource_id, value, *, stage=None, query_kind="context"):
        stage = stage or self.workflow_stage
        self.resources[resource_id] = copy.deepcopy(value)
        self.stage_state["resource_stages"][resource_id] = stage
        self.stage_state["resource_descriptors"][resource_id] = {
            "id": resource_id,
            "stage": stage,
            "query_kind": query_kind,
            "action": None,
            "entities": [],
            "selectors": [],
            "fingerprint": fingerprint(value),
            "bytes": len(json_bytes(value)),
        }


def test_small_result_stays_direct_and_reserves_settlement_budget():
    receipt = build_native_tool_receipt(
        _Session(),
        tool="gs_read_context",
        arguments={"resource": "workflow_stage"},
        response={"code": 0, "data": {"phase": "numeric_model"}},
        policy="pure_read",
        resource_scope=_scope(),
    )

    assert receipt.resource_ids == ()
    assert receipt.result["code"] == 0
    assert receipt.result["native_receipt"] == {
        "contract": RECEIPT_CONTRACT,
        "mode": "direct",
        "tool": "gs_read_context",
        "resources": [],
    }
    assert len(canonical_receipt_bytes(receipt.result)) <= DIRECT_RESULT_MAX_BYTES


def test_sanitizer_normalizes_ordered_python_schema_containers():
    result = sanitize_native_response({
        "code": 0,
        "data": {
            "sections": ("skills", "talents", "annotations"),
            "nested": ({"levels": (90, 100)},),
        },
    })

    assert result == {
        "code": 0,
        "data": {
            "sections": ["skills", "talents", "annotations"],
            "nested": [{"levels": [90, 100]}],
        },
    }
    canonical_receipt_bytes(result)


def test_registered_query_resource_is_the_only_authoritative_reference():
    value = {"characters": [{"id": 10000002, "name": "神里绫华"}]}
    session = _Session(resources={"query-1": value})
    response = {
        "code": 0,
        "data": {
            "resource": "query-1",
            "items": [],
            "source_ref": {
                "kind": "database",
                "reference": "query-1",
                "fingerprint": fingerprint(value),
                "status": "verified",
            },
        },
    }
    receipt = build_native_tool_receipt(
        session,
        tool="gs_query_context",
        arguments={"request": {}},
        response=response,
        policy="session_resource_read",
        resource_scope=_scope(),
    )

    assert receipt.resource_ids == ("query-1",)
    assert receipt.result["native_receipt"]["resources"] == [{"id": "query-1"}]
    assert "session_scope_token" not in json.dumps(receipt.result)
    assert "descriptor_identity" not in json.dumps(receipt.result)
    references = receipt.issued_references
    assert len(references) == 1
    assert references[0]["id"] == "query-1"
    assert references[0]["fingerprint"] == fingerprint(value)
    assert references[0]["bytes"] == len(json_bytes(value))
    assert references[0]["session_scope_token"] == _scope()["session_scope_token"]
    assert len(references[0]["descriptor_identity"]) == 64


def test_oversized_result_uses_registered_reference_instead_of_truncation():
    session = _Session(resources={"query-1": {"payload": "saved"}})
    receipt = build_native_tool_receipt(
        session,
        tool="gs_query_context",
        arguments={"request": {}},
        response={
            "code": 0,
            "data": {"resource": "query-1", "inline": "x" * DIRECT_RESULT_MAX_BYTES},
        },
        policy="session_resource_read",
        resource_scope=_scope(),
    )

    assert receipt.result["code"] == 0
    assert receipt.result["native_receipt"]["mode"] == "referenced"
    assert receipt.resource_ids == ("query-1",)
    assert "inline" not in receipt.result["data"]
    assert receipt.result["data"]["resources"] == [{"id": "query-1"}]
    public_result = json.dumps(receipt.result, ensure_ascii=False)
    for private_field in (
        "session_scope_token",
        "fingerprint",
        "descriptor_identity",
        "stage_revision",
        "tool_manifest_hash",
    ):
        assert private_field not in public_result
    assert receipt.result["data"]["continuations"] == [
        {
            "tool": "gs_read_context",
            "arguments": {
                "resource": "query-1",
                "pointer": "",
                "offset": 0,
                "limit": 50,
            },
            "authorization": "host_issued_resource_reference",
        }
    ]


def test_direct_budget_accounts_for_json_string_escaping_in_settlement():
    session = _Session(resources={"query-1": {"payload": "saved"}})
    receipt = build_native_tool_receipt(
        session,
        tool="gs_read_context",
        arguments={"resource": "query-1"},
        response={
            "code": 0,
            "data": {"resource": "query-1", "inline": '"' * 23000},
        },
        policy="pure_read",
        resource_scope=_scope(),
    )

    # The raw response fits 48KiB, but embedding it as result_json would not
    # leave enough room for the settlement metadata.
    assert receipt.result["native_receipt"]["mode"] == "referenced"
    assert receipt.resource_ids == ("query-1",)


def test_oversized_failure_exposes_only_public_resource_ids():
    session = _Session(resources={"query-1": {"payload": "saved"}})
    receipt = build_native_tool_receipt(
        session,
        tool="gs_query_context",
        arguments={"request": {}},
        response={
            "code": 1,
            "reason": "read_failed",
            "message": "x" * DIRECT_RESULT_MAX_BYTES,
            "data": {"resource": "query-1"},
        },
        policy="pure_read",
        resource_scope=_scope(),
    )

    assert receipt.result["native_receipt"]["mode"] == "referenced"
    assert receipt.result["details"]["resources"] == [{"id": "query-1"}]
    public_result = json.dumps(receipt.result, ensure_ascii=False)
    assert "session_scope_token" not in public_result
    assert "descriptor_identity" not in public_result
    assert receipt.issued_references[0]["session_scope_token"] == "b" * 64


def test_oversized_result_without_trusted_reference_fails_explicitly():
    receipt = build_native_tool_receipt(
        _Session(),
        tool="gs_read_skill",
        arguments={},
        response={"code": 0, "data": {"text": "x" * DIRECT_RESULT_MAX_BYTES}},
        policy="pure_read",
        resource_scope=_scope(),
    )

    assert receipt.resource_ids == ()
    assert receipt.result["code"] == 1
    assert receipt.result["reason"] == "native_result_requires_resource"
    assert receipt.result["native_receipt"]["mode"] == "rejected"


def test_resource_producing_read_fails_closed_for_forged_or_missing_reference():
    session = _Session()
    forged = build_native_tool_receipt(
        session,
        tool="gs_query_runtime",
        arguments={"request": {}},
        response={"code": 0, "data": {"resource": "query-99", "items": []}},
        policy="session_resource_read",
        resource_scope=_scope(),
    )
    missing = build_native_tool_receipt(
        session,
        tool="gs_query_runtime",
        arguments={"request": {}},
        response={"code": 0, "data": {"items": []}},
        policy="session_resource_read",
        resource_scope=_scope(),
    )

    assert forged.result["reason"] == "native_resource_receipt_invalid"
    assert missing.result["reason"] == "native_resource_receipt_missing"
    assert forged.resource_ids == missing.resource_ids == ()
    encoded = json.dumps(forged.result, ensure_ascii=False, sort_keys=True)
    assert "query-99" not in encoded
    assert forged.result["details"]["claimed_resource_count"] == 1
    assert len(forged.result["details"]["claimed_resource_hash"]) == 64


def test_hidden_or_stale_descriptor_cannot_become_a_native_resource_ref():
    value = {"payload": "saved"}
    hidden_session = _Session(phase="numeric_model")
    hidden_session.register("query-1", value, stage="calculation_closure")
    hidden = build_native_tool_receipt(
        hidden_session,
        tool="gs_query_context",
        arguments={"request": {}},
        response={"code": 0, "data": {"resource": "query-1"}},
        policy="session_resource_read",
        resource_scope=_scope(),
    )

    stale_session = _Session(resources={"query-1": value})
    stale_session.stage_state["resource_descriptors"]["query-1"]["bytes"] += 1
    stale = build_native_tool_receipt(
        stale_session,
        tool="gs_query_context",
        arguments={"request": {}},
        response={"code": 0, "data": {"resource": "query-1"}},
        policy="session_resource_read",
        resource_scope=_scope(),
    )

    assert hidden.result["reason"] == "native_resource_receipt_invalid"
    assert stale.result["reason"] == "native_resource_receipt_invalid"


def test_direct_and_failure_messages_redact_windows_unc_and_uri_paths():
    message = (
        r"failed at C:\Users\alice\secret.json and \\server\share\plan.json "
        "then file:///C:/Users/alice/secret.json"
    )
    direct = build_native_tool_receipt(
        _Session(),
        tool="gs_read_skill",
        arguments={},
        response={"code": 0, "data": {"message": message}},
        policy="pure_read",
        resource_scope=_scope(),
    )
    failure = build_native_tool_receipt(
        _Session(),
        tool="gs_read_skill",
        arguments={},
        response={"code": 1, "reason": "read_failure", "message": message},
        policy="pure_read",
        resource_scope=_scope(),
    )

    for receipt in (direct, failure):
        encoded = json.dumps(receipt.result, ensure_ascii=False)
        assert "alice" not in encoded
        assert "server" not in encoded
        assert "file:///" not in encoded
        assert "[redacted-" in encoded


@pytest.mark.parametrize(
    "message,secret",
    [
        (r'failed at "C:\Program Files\Private Agent\token.json"', "Private Agent"),
        (r"failed at C:\Program Files\Private Agent\token.json", "Private Agent"),
        (r"failed at \\\\server\private share\token.json", "private share"),
        ("failed at file:///C:/private host/secret", "private host"),
    ],
)
def test_message_redaction_cannot_leak_path_suffix_after_spaces(message, secret):
    receipt = build_native_tool_receipt(
        _Session(),
        tool="gs_read_skill",
        arguments={},
        response={"code": 0, "data": {"message": message}},
        policy="pure_read",
        resource_scope=_scope(),
    )

    encoded = json.dumps(receipt.result, ensure_ascii=False)
    assert secret not in encoded
    assert "token.json" not in encoded
    assert "[redacted-" in encoded


def test_all_nested_response_strings_are_scrubbed_but_public_web_urls_remain():
    receipt = build_native_tool_receipt(
        _Session(),
        tool="gs_read_skill",
        arguments={},
        response={
            "code": 0,
            "data": {
                "source_path": r"C:\Users\alice\private skill.json",
                "nested": [
                    {"raw": r"\\server\private share\skill.json"},
                    {"uri": "file:///C:/Users/alice/private.json"},
                ],
                "documentation": "https://example.com/public/guide?q=1",
            },
        },
        policy="pure_read",
        resource_scope=_scope(),
    )

    encoded = json.dumps(receipt.result, ensure_ascii=False)
    assert "alice" not in encoded
    assert "server" not in encoded
    assert "private share" not in encoded
    assert "file:///" not in encoded
    assert "https://example.com/public/guide?q=1" in encoded


def test_path_scrubber_covers_forward_unc_and_posix_without_damaging_http_urls():
    public_url = "https://example.com/C:/public/guide"
    receipt = build_native_tool_receipt(
        _Session(),
        tool="gs_read_skill",
        arguments={},
        response={
            "code": 0,
            "data": {
                "forward_unc": "//server/private share/secret.json",
                "posix": "/home/alice/secret.json",
                "documentation": public_url,
            },
        },
        policy="pure_read",
        resource_scope=_scope(),
    )

    encoded = json.dumps(receipt.result, ensure_ascii=False)
    assert "server" not in encoded
    assert "/home/alice" not in encoded
    assert encoded.count("[redacted-path]") >= 2
    assert public_url in encoded


def test_path_scrubber_preserves_markdown_api_routes_and_json_pointers():
    message = (
        "POST /v1/context/query，参见 [说明](references/data/query.md)，"
        "JSON Pointer 为 /skills/0，环境变量为 %LOCALAPPDATA%。"
    )
    receipt = build_native_tool_receipt(
        _Session(),
        tool="gs_read_skill",
        arguments={},
        response={"code": 0, "data": {"message": message}},
        policy="pure_read",
        resource_scope=_scope(),
    )

    assert receipt.result["data"]["message"] == message


def test_path_scrubber_keeps_suffix_and_pagination_offsets_stable():
    message = (
        r'before "C:\Users\alice\Private Agent\secret.json" and after '
        "POST /v1/context/query\nnext page"
    )
    receipt = build_native_tool_receipt(
        _Session(),
        tool="gs_read_skill",
        arguments={"offset": 41, "max_chars": 200},
        response={
            "code": 0,
            "data": {
                "text": message,
                "offset": 41,
                "next_offset": 41 + len(message),
                "total": 500,
            },
        },
        policy="pure_read",
        resource_scope=_scope(),
    )

    sanitized = receipt.result["data"]["text"]
    assert "alice" not in sanitized
    assert "Private Agent" not in sanitized
    assert "POST /v1/context/query\nnext page" in sanitized
    assert len(sanitized) == len(message)
    assert receipt.result["data"]["next_offset"] == 41 + len(message)


def test_real_page_pointer_survives_receipt_and_drives_recursive_read():
    value = {
        "large": {f"field-{index}": "x" * 200 for index in range(100)},
    }
    session = _Session(resources={"query-1": value})
    first_response = tools.call_tool(
        session,
        "gs_read_context",
        {"resource": "query-1"},
    )
    first = build_native_tool_receipt(
        session,
        tool="gs_read_context",
        arguments={"resource": "query-1"},
        response=first_response,
        policy="pure_read",
        resource_scope=_scope(),
    )

    reference = first.result["data"]["items"][0]["value"]["reference"]
    assert first.result["data"]["pointer"] == ""
    assert reference == {"resource": "query-1", "pointer": "/large"}

    second_response = tools.call_tool(
        session,
        "gs_read_context",
        {
            "resource": reference["resource"],
            "pointer": reference["pointer"],
            "offset": 0,
            "limit": 50,
        },
    )
    second = build_native_tool_receipt(
        session,
        tool="gs_read_context",
        arguments={
            "resource": reference["resource"],
            "pointer": reference["pointer"],
            "offset": 0,
            "limit": 50,
        },
        response=second_response,
        policy="pure_read",
        resource_scope=_scope(),
    )

    assert second.result["code"] == 0
    assert second.result["data"]["pointer"] == "/large"
    assert second.result["data"]["items"][0]["key"] == "field-0"


def test_page_does_not_preserve_continuation_for_path_bearing_object_key():
    secret_key = r"C:\Users\alice\private"
    value = {secret_key: {"blob": "x" * 17000}}
    session = _Session(resources={"query-1": value})
    receipt = build_native_tool_receipt(
        session,
        tool="gs_read_context",
        arguments={"resource": "query-1"},
        response={"code": 0, "data": tools.page(value, "query-1")},
        policy="pure_read",
        resource_scope=_scope(),
    )

    encoded = json.dumps(receipt.result, ensure_ascii=False)
    assert "alice" not in encoded
    assert "private" not in encoded
    assert "[redacted-path]" in encoded


def test_path_bearing_object_key_fails_closed_without_leaking_key():
    receipt = build_native_tool_receipt(
        _Session(),
        tool="gs_read_skill",
        arguments={},
        response={"code": 0, r"C:\Users\alice\secret.txt": "value"},
        policy="pure_read",
        resource_scope=_scope(),
    )

    encoded = json.dumps(receipt.result, ensure_ascii=False)
    assert receipt.result["code"] == 1
    assert receipt.result["reason"] == "native_tool_result_invalid"
    assert "alice" not in encoded
    assert "secret.txt" not in encoded


@pytest.mark.parametrize(
    "response",
    (
        {"code": 0, "reason": "read_failed"},
        {"code": 1, "reason": 123},
        {"code": 1, "reason": "outcome_unknown"},
    ),
)
def test_contradictory_terminal_fields_are_normalized_to_explicit_failure(response):
    receipt = build_native_tool_receipt(
        _Session(),
        tool="gs_read_skill",
        arguments={},
        response=response,
        policy="pure_read",
        resource_scope=_scope(),
    )

    assert receipt.result["code"] == 1
    assert receipt.result["reason"] == "native_tool_result_invalid"
