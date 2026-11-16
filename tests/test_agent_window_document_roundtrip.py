"""Actual detached HTML transport feeds the same candidate review contract."""
from __future__ import annotations

import copy
import json
import shutil
import subprocess
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from genshin_app.agent_chat.native_dsh_workspace import NativeDshWorkspaceRegistry
from genshin_app.equipment.files import fingerprint, input_view
from genshin_app.gui.agent_window import AgentWindowBridge, AgentWindowController
from genshin_app.gui.api import GuiApi
from tests.test_calculation_delivery import delivery_plan
from tests.test_queries import db_path as db_path  # noqa: PLC0414


def test_first_bind_in_detached_window_then_review_in_main_preserves_identity(tmp_path, db_path):
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node is needed to execute the actual detached Renderer document bridge")
    resources = Path(__file__).parents[1] / "src/genshin_app/gui/web"
    document = copy.deepcopy(delivery_plan())
    document["manual_template"]["characters"][0]["stats"].update(em=0.0, energy_recharge=1.0)
    snapshot = json.dumps({"document_kind": "native", "document": document}, ensure_ascii=False)
    registry = NativeDshWorkspaceRegistry(tmp_path / "workspaces", database_path=db_path, workflow="calculate")
    api = GuiApi(tmp_path, database_path=db_path, dsh_workspace_registry=registry)
    api._window = Mock(evaluate_js=Mock(return_value=snapshot))
    controller = AgentWindowController(api, SimpleNamespace(), resources)
    controller._identity = "detached"
    bridge = AgentWindowBridge(controller, Mock(), "detached")
    try:
        # Execute the production HTML's document() method, including its own
        # lossless parser, rather than simulating an int/float conversion.
        script = r"""
const fs=require('node:fs'),vm=require('node:vm');
const web=process.argv[1],snapshot=fs.readFileSync(0,'utf8');
const html=fs.readFileSync(web+'/agent-window.html','utf8');
if(!html.includes('src="json-transport.js"'))throw Error('transport_not_loaded');
const context=vm.createContext({TextEncoder,console,document:{createElement:()=>({})}});
context.window=context;
context.pywebview={api:{dsh_window_document:async()=>snapshot}};
vm.runInContext(fs.readFileSync(web+'/json-transport.js','utf8'),context);
for(const block of html.matchAll(/<script>([\s\S]*?)<\/script>/g))vm.runInContext(block[1],context);
vm.runInContext("GenshinEditorBridge.document().then(value=>JSONTransport.stringify([{...value,dsh_session_id:'detached-one'}]))",context)
 .then(value=>process.stdout.write(value)).catch(error=>{console.error(error);process.exitCode=1;});
"""
        wire = subprocess.run([node, "-e", script, str(resources)], input=bridge.dsh_window_document(),
                              text=True, encoding="utf-8", capture_output=True, check=True).stdout
        first = json.loads(bridge.document_json_call("dsh_client_bind_document", wire))
        assert first["binding"]["dsh_session_id"] == "detached-one"
        scope = registry.activate("detached-one")
        assert scope.candidate_session.document_fingerprint == fingerprint(input_view(document))
        scope.candidate_session.calculate(0)
        summary = scope.candidate_session.summary()
        projection = registry.domain_projection("detached-one")["projection"]
        request = {"conversation_id": scope.conversation_id, "command_id": "candidate.review",
                   "expected_projection_revision": projection["projection_revision"],
                   "arguments": {"kind": "native", "document": document, "expected_revision": 0,
                                 "expected_receipt": summary["receipt_fingerprint"]}}
        result = json.loads(api.document_json_call("dsh_client_candidate_command", json.dumps([request])))
        assert result["code"] == 0, result
        assert result["data"]["result"]["plan"]["plan_id"] == document["plan_id"]
        # Actual edits are still refused; preserving transport is not a relaxed CAS.
        request["arguments"]["document"] = copy.deepcopy(document)
        request["arguments"]["document"]["manual_template"]["characters"][0]["stats"]["em"] = 1.0
        denied = json.loads(api.document_json_call("dsh_client_candidate_command", json.dumps([request])))
        assert denied["reason"] == "agent_document_changed"
    finally:
        registry.close()
