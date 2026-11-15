"""Agent-visible schemas stay executable and aligned with authoritative validators."""

from __future__ import annotations

import copy
import json
import shutil
import subprocess
from pathlib import Path

import pytest

from genshin_app.agent_chat import tools
from genshin_app.agent_chat.native_app_tool_catalog import (
    build_native_app_tool_snapshot,
)
from genshin_app.equipment.schema import PlanError
from genshin_app.workflow import audit_record_schema, validate_audit
from genshin_db.context_query import context_request_schema, validate_context_request


def _audit(records):
    return {
        "schema_version": 1,
        "plan_id": "schema-contract",
        "input_fingerprint": "f" * 64,
        "records": records,
        "scope": {"included": [], "excluded": [], "pending": []},
    }


def test_query_context_tool_exposes_validator_derived_schema_and_example():
    request_schema = tools.SPECS["gs_query_context"][1]["properties"]["request"]

    assert request_schema == context_request_schema()
    assert request_schema["properties"]["characters"]["maxItems"] == 4
    assert request_schema["properties"]["weapons"]["items"]["properties"][
        "refinement"
    ]["maximum"] == 5
    assert validate_context_request(request_schema["examples"][0]) == {
        "version_line": "release",
        "characters": [{
            "id": 10000133,
            "sections": ["character", "skills", "talents", "extra_info", "annotations"],
            "levels": [10, 13],
        }],
        "weapons": [],
        "artifact_sets": [],
    }


def test_record_finding_tool_exposes_executable_audit_examples():
    record_schema = tools.SPECS["gs_record_finding"][1]["properties"]["record"]

    assert record_schema == audit_record_schema()
    for example in record_schema["examples"]:
        assert validate_audit(_audit([copy.deepcopy(example)]))["records"][0] == example


def test_record_validator_and_tool_schema_both_reject_unpublished_fields():
    example = copy.deepcopy(audit_record_schema()["examples"][0])
    example["private_path"] = r"C:\Users\alice\secret.json"

    with pytest.raises(PlanError, match="未知字段"):
        validate_audit(_audit([example]))
    published = tools.SPECS["gs_record_finding"][1]["properties"]["record"]
    assert published["additionalProperties"] is False
    assert "private_path" not in published["properties"]


def test_tool_schema_factories_return_isolated_copies():
    context = context_request_schema()
    finding = audit_record_schema()
    context["properties"].clear()
    finding["examples"].clear()

    assert "characters" in context_request_schema()["properties"]
    assert audit_record_schema()["examples"]


@pytest.mark.parametrize(
    "phase",
    [
        "loadout_draft",
        "action_checkpoint",
        "event_topology",
        "numeric_model",
        "calculation_closure",
    ],
)
def test_real_native_tool_snapshot_compiles_in_production_node_runtime(phase):
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node is required for the production schema parity check")

    class Session:
        workflow = "staged"
        workflow_stage = phase
        revision = 0

        def __init__(self):
            self.stage_state = {"stage_revision": 0}

        @staticmethod
        def _is_board():
            return True

    snapshot = build_native_app_tool_snapshot(
        Session(),
        document_id="document-schema-parity",
        database_fingerprint="a" * 64,
        tool_manifest_hash="b" * 64,
        runtime_generation="dsh-v2",
        writer_fence="schema-parity-fence",
    )
    root = Path(__file__).resolve().parents[1]
    source = """
import fs from 'node:fs'
import { validateAppToolsSnapshot } from './agent-runtime/src/native-app-tool-catalog.mjs'
const snapshot = JSON.parse(fs.readFileSync(0, 'utf8'))
validateAppToolsSnapshot(snapshot)
process.stdout.write('ok')
"""
    completed = subprocess.run(
        [node, "--input-type=module", "--eval", source],
        cwd=root,
        input=json.dumps(snapshot, ensure_ascii=False),
        text=True,
        encoding="utf-8",
        capture_output=True,
        timeout=30,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    assert completed.stdout == "ok"
