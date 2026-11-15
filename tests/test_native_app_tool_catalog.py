from __future__ import annotations

import copy
import json
import os
import subprocess
from pathlib import Path

import pytest

from genshin_app.agent_chat import tools
from genshin_app.agent_chat.native_app_tool_catalog import (
    MAX_APP_CALLS,
    build_native_app_tool_snapshot,
    validate_native_app_tool_snapshot,
)
from genshin_app.equipment.schema import PlanError

HASH = "a" * 64
PHASES = (
    "loadout_draft",
    "action_checkpoint",
    "event_topology",
    "numeric_model",
    "calculation_closure",
)
FIXED = {
    "gs_list_research_calls",
    "gs_report_research_usage",
    "gs_app_get_workspace",
    "gs_app_request_action",
}


class Session:
    workflow = "staged"

    def __init__(self, phase: str):
        self.workflow_stage = phase
        self.revision = 4
        self.stage_state = {"stage_revision": 7}

    def _is_board(self):
        return True


def snapshot(session):
    return build_native_app_tool_snapshot(
        session,
        document_id="document-1",
        database_fingerprint=HASH,
        tool_manifest_hash="b" * 64,
        runtime_generation="dsh-v2",
        writer_fence="fence-1",
    )


@pytest.mark.parametrize("phase", PHASES)
def test_five_stage_catalog_is_exact_definition_intersection(phase):
    session = Session(phase)
    value = snapshot(session)
    names = {row["name"] for row in value["tools"]}

    assert names == {
        *(row["name"] for row in tools.definitions_for_session(session)),
        *FIXED,
    }
    assert set(value["policies"]) == names
    assert not {"web_search", "web_fetch"} & names
    assert value["max_app_calls"] == MAX_APP_CALLS == 16_384
    assert validate_native_app_tool_snapshot(value) == value


def test_catalog_requires_explicit_policy_for_every_disclosed_tool(monkeypatch):
    session = Session("event_topology")
    monkeypatch.setattr(
        tools,
        "definitions_for_session",
        lambda _session: [{
            "type": "function",
            "name": "gs_missing_policy",
            "description": "x",
            "inputSchema": {
                "type": "object",
                "properties": {},
                "additionalProperties": False,
            },
        }],
    )
    with pytest.raises(PlanError) as caught:
        snapshot(session)
    assert caught.value.reason == "native_app_tool_catalog_invalid"


def test_catalog_tampering_is_detected_even_if_hash_is_not_updated():
    value = snapshot(Session("numeric_model"))
    changed = copy.deepcopy(value)
    changed["policies"]["gs_compose"]["effect"] = "none"
    with pytest.raises(PlanError):
        validate_native_app_tool_snapshot(changed)

    changed = copy.deepcopy(value)
    changed["candidate_revision"] += 1
    with pytest.raises(PlanError):
        validate_native_app_tool_snapshot(changed)


def test_catalog_policy_covers_every_domain_spec():
    assert set(tools.SPECS) == set(tools.NATIVE_V2_TOOL_POLICIES)


def test_actual_python_catalogs_are_accepted_by_node_including_new_tools():
    node = os.environ.get("GENSHIN_NATIVE_NODE")
    if not node:
        pytest.skip("set GENSHIN_NATIVE_NODE for the actual cross-language catalog check")
    artifact = Session("artifact")
    artifact.workflow = "artifact"
    snapshots = [snapshot(Session(phase)) for phase in PHASES] + [snapshot(artifact)]
    assert all(any(row["name"] == "gs_reference_panel" for row in value["tools"]) for value in snapshots)
    assert any(row["name"] == "gs_get_character_appendix_index" for row in snapshots[-1]["tools"])
    module = (Path(__file__).resolve().parents[1] / "agent-runtime/src/native-app-tool-catalog.mjs").as_uri()
    script = f"""
      import {{ readFileSync }} from 'node:fs';
      import assert from 'node:assert/strict';
      import {{ validateAppToolsSnapshot, compileAppToolDefinitions }} from {json.dumps(module)};
      const values = JSON.parse(readFileSync(0, 'utf8'));
      for (const value of values) {{
        validateAppToolsSnapshot(value);
        const tools = compileAppToolDefinitions(value, async () => ({{code:0}}));
        assert.deepEqual(tools.map(tool => tool.name).sort(), value.tools.map(tool => tool.name).sort());
        const navigation = tools.find(tool => tool.name === 'gs_get_character_appendix_index');
        if (navigation) {{
          assert.equal(navigation.parameters.properties.db_character_id.type, 'integer');
          assert.deepEqual(navigation.parameters.required, ['db_character_id']);
          assert.equal(value.tools.find(tool => tool.name === navigation.name).inputSchema.additionalProperties, false);
          await assert.rejects(navigation.execute({{db_character_id:1, unexpected:true}}, {{callId:'invalid-shape'}}));
        }}
      }}
      console.log(JSON.stringify({{accepted:values.length}}));
    """
    result = subprocess.run([node, "--input-type=module", "--eval", script],
        input=json.dumps(snapshots, ensure_ascii=False), text=True, encoding="utf-8", capture_output=True, check=False)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {"accepted": len(PHASES) + 1}
