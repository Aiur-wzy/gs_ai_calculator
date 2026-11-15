"""The official DSH ToolRuntime reaches a real App Host for every JSON root."""
from __future__ import annotations

import copy
import hashlib
import json
import subprocess
import threading
import uuid
from pathlib import Path

import pytest

from genshin_app.agent_chat.agent_hash_input import hash_input
from genshin_app.agent_chat.bridge_protocol import raw_json_hash
from genshin_app.agent_chat.native_app_tool_host import (
    _derived_view,
    _durable_candidate_view,
)
from genshin_app.agent_chat.native_dsh_workspace import NativeDshWorkspaceRegistry
from genshin_app.agent_chat.tools import MAX_ARGUMENTS, SPECS, definitions_for_session
from genshin_app.whiteboard import new_whiteboard
from tests.pinned_native_node import pinned_native_node
from tests.test_direct_stellar_damage_contract import direct_stellar_profile
from tests.test_queries import db_path as db_path  # noqa: PLC0414
from tests.test_special_reactions import direct_lunar_profile

ROOT = Path(__file__).resolve().parents[1]


def test_real_dsh_tool_runtime_executes_three_hash_kinds_and_all_finite_json_roots(tmp_path, db_path):
    node = str(pinned_native_node())
    database_hash = hashlib.sha256(db_path.read_bytes()).hexdigest()
    registry = NativeDshWorkspaceRegistry(tmp_path / "workspace", database_path=db_path)
    registry.prepare_document("hash-sdk-session", "whiteboard", new_whiteboard())
    scope = registry.activate("hash-sdk-session")
    session = scope.candidate_session
    before = copy.deepcopy({"derived": _derived_view(session), "durable": _durable_candidate_view(session)})
    checkpoint = (session.root / "checkpoint.json").read_bytes()
    definition = next(row for row in definitions_for_session(session) if row["name"] == "gs_hash_input")
    cases = [{"kind": "canonical_json", "value": value} for value in (
        None, True, False, 1, 1.25, "冰\U0001f9ca", [], [1.25, {"冰": None}], {}, {"冰": {"value": .125}})]
    cases += [{"kind": "direct_stellar_damage_profile", "value": direct_stellar_profile()},
              {"kind": "direct_lunar_damage_profile", "value": direct_lunar_profile()}]
    process = subprocess.Popen([node, str(ROOT / "tests/native_hash_input_tool_runtime_probe.mjs")],
        cwd=ROOT, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, encoding="utf-8")
    # Offline engineering protocol watchdog, not an Agent/provider time limit.
    watchdog = threading.Timer(60, process.kill)
    watchdog.daemon = True
    watchdog.start()
    observed, terminals, completed = [], [], None
    try:
        process.stdin.write(json.dumps({"definition": definition, "cases": cases}, ensure_ascii=True, allow_nan=False) + "\n")
        process.stdin.flush()
        for line in process.stdout:
            if not line.startswith("HASH_RUNTIME "):
                continue
            packet = json.loads(line.removeprefix("HASH_RUNTIME "))
            if packet["type"] == "invoke":
                args = packet["args"]
                raw = json.dumps(args, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
                with registry._tool_boundary(scope, "gs_hash_input"):
                    snapshot = scope.tool_host.current_snapshot()
                    bound = {name: snapshot[name] for name in (
                        "document_id", "candidate_revision", "stage_revision", "database_fingerprint",
                        "tool_manifest_hash", "runtime_generation", "writer_fence", "catalog_hash")}
                    terminal = scope.tool_host.execute(conversation_id=scope.conversation_id, turn_id="hash-turn",
                        step_id="hash-step", epoch_id="hash-epoch", call_id="hash-call-" + uuid.uuid4().hex,
                        tool_name="gs_hash_input", arguments_json=raw, raw_arguments_hash=raw_json_hash(raw), **bound)
                terminals.append(terminal)
                assert terminal["terminal_state"] == "settled_success", terminal
                assert terminal["state_changes"] == []
                expected = hash_input(**args, maximum_bytes=MAX_ARGUMENTS)
                assert terminal["result"]["data"] == expected
                process.stdin.write(json.dumps(terminal, ensure_ascii=True) + "\n")
                process.stdin.flush()
            elif packet["type"] == "case":
                observed.append(packet)
            elif packet["type"] == "done":
                completed = packet
        process.wait(timeout=30)
        assert process.returncode == 0, process.stderr.read()
        assert len(observed) == len(terminals) == len(cases), observed
        assert all(not row["result"].get("isError", False) for row in observed), observed
        assert completed["invalid_rejected_before_host"] == 6 and completed["network_calls"] == 0
        assert {"derived": _derived_view(session), "durable": _durable_candidate_view(session)} == before
        assert (session.root / "checkpoint.json").read_bytes() == checkpoint
        assert session.resources == {} and hashlib.sha256(db_path.read_bytes()).hexdigest() == database_hash
    finally:
        watchdog.cancel()
        if process.poll() is None:
            process.kill()
            process.wait(timeout=10)
        for stream in (process.stdin, process.stdout, process.stderr):
            stream.close()
        registry.close()


def _assert_no_annotation_only_nodes(schema, path):
    """Guard this adapter's known missing-type trap, not a second validator.

    The public compiler accepts JSON annotation nodes but the App execution
    adapter accepts unconstrained JSON only as exactly {}. Other schema
    semantics remain owned by the real adapter and pinned DSH runtime tests.
    """
    assert not schema or any(key in schema for key in ("type", "enum", "const", "anyOf", "oneOf")), path
    for name, child in schema.get("properties", {}).items():
        _assert_no_annotation_only_nodes(child, path + "/" + name)
    if isinstance(schema.get("items"), dict):
        _assert_no_annotation_only_nodes(schema["items"], path + "/*")
    for union in ("anyOf", "oneOf"):
        for index, child in enumerate(schema.get(union, [])):
            _assert_no_annotation_only_nodes(child, path + "/" + union + "/" + str(index))


def test_every_published_app_schema_avoids_the_annotation_only_execution_trap():
    for name, (_description, schema) in SPECS.items():
        _assert_no_annotation_only_nodes(schema, name)


def test_guard_rejects_the_original_hash_schema_at_its_exact_property():
    legacy = copy.deepcopy(SPECS["gs_hash_input"][1])
    legacy["properties"]["value"] = {"description": "Original annotation-only JSON value"}
    with pytest.raises(AssertionError, match="gs_hash_input/value"):
        _assert_no_annotation_only_nodes(legacy, "gs_hash_input")


@pytest.mark.parametrize("schema", [
    {}, {"enum": ["known"]}, {"const": None},
    {"type": "object", "properties": {"extra": {}}},
    {"type": "array", "items": {"const": True}},
    {"anyOf": [{"type": "object"}, {"type": "null"}], "description": "Nullable value"},
    {"oneOf": [{"type": "string"}, {"type": "number"}]},
])
def test_guard_preserves_existing_unconstrained_enum_const_and_union_schema_nodes(schema):
    _assert_no_annotation_only_nodes(schema, "valid")
