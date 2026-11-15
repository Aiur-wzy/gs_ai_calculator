"""The CLI and native tools edit one live candidate, without an alternate owner."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
from http.client import HTTPConnection
from pathlib import Path

import pytest

from genshin_app.agent_chat.native_cli_link import (
    LiveCandidateCliLink,
    call_live_candidate,
    workspace_cli_call,
)
from genshin_app.agent_chat.native_dsh_workspace import NativeDshWorkspaceRegistry
from genshin_app.equipment.schema import PlanError
from tests.test_queries import db_path as db_path  # noqa: PLC0414


def test_shared_live_owner_alternating_edits_and_stale_revision(tmp_path, db_path):
    registry = NativeDshWorkspaceRegistry(tmp_path / "workspaces", database_path=db_path)
    registry.prepare_document("one", None, None)
    scope = registry.activate("one")
    try:
        first = workspace_cli_call(registry, "read", "one", {})
        assert first["summary"]["revision"] == 0
        result = workspace_cli_call(registry, "edit", "one", {
            "expected_revision": 0, "reason": "CLI edits the actual candidate", "mode": "patch",
            "operations": [{"op": "replace", "path": "/title", "value": "CLI修改"}],
        })
        assert result["revision"] == 1
        assert scope.candidate_session.candidate["title"] == "CLI修改"
        from genshin_app.input_editing import edit_candidate
        edit_candidate(scope.candidate_session, {
            "expected_revision": 1, "reason": "native tool continues", "mode": "patch",
            "operations": [{"op": "replace", "path": "/title", "value": "原生继续"}],
        })
        with pytest.raises(PlanError) as failure:
            workspace_cli_call(registry, "edit", "one", {
                "expected_revision": 1, "reason": "stale CLI snapshot", "mode": "patch",
                "operations": [{"op": "replace", "path": "/title", "value": "不能覆盖"}],
            })
        assert failure.value.reason == "agent_stale_revision"
        assert workspace_cli_call(registry, "read", "one", {})["input"]["title"] == "原生继续"
        assert len(registry._workspaces) == 1
        with pytest.raises(PlanError):
            workspace_cli_call(registry, "candidate.apply", "one", {})
    finally:
        registry.close()


def test_loopback_transport_auth_origin_no_retry_and_cleanup(tmp_path):
    calls = []
    def dispatch(operation, sid, request):
        calls.append((operation, sid, request))
        return {"revision": 7}
    link = LiveCandidateCliLink(tmp_path, dispatch)
    path = link.descriptor_for("same-session")
    try:
        assert call_live_candidate(path, "read", "same-session") == {"revision": 7}
        assert len(calls) == 1
        with pytest.raises(PlanError) as failure:
            call_live_candidate(path, "read", "other-session")
        assert failure.value.reason == "cli_session_mismatch"
        descriptor = json.loads(path.read_text())
        conn = HTTPConnection("127.0.0.1", descriptor["port"])
        conn.request("POST", "/candidate", body=b"{}", headers={
            "Authorization": "Bearer " + descriptor["token"], "Origin": "https://not-app.invalid"})
        response = conn.getresponse()
        assert response.status == 403
        response.read()
        conn.close()
        assert len(calls) == 1
        descriptor["token"] = "not-the-capability"
        wrong = tmp_path / "wrong.json"
        wrong.write_text(json.dumps(descriptor))
        with pytest.raises(PlanError, match="当前App连接"):
            call_live_candidate(wrong, "edit", "same-session", {"expected_revision": 7})
        assert len(calls) == 1
    finally:
        link.close()
    assert not path.exists()


def test_registry_connection_does_not_create_new_session_and_closes(tmp_path, db_path):
    registry = NativeDshWorkspaceRegistry(tmp_path / "workspaces", database_path=db_path)
    assert registry.cli_connection() is None
    registry.enable_cli_link()
    path = registry.cli_connection()
    assert registry.cli_connection() == path
    assert not registry._workspaces
    with pytest.raises(PlanError):
        call_live_candidate(path, "read", "not-bound")
    assert not registry._workspaces
    registry.close()


@pytest.mark.parametrize("hostname_lookup_unavailable", [False, True])
def test_actual_cli_process_edits_the_existing_gui_owner(
    tmp_path, db_path, monkeypatch, hostname_lookup_unavailable,
):
    if hostname_lookup_unavailable:
        def unavailable_hostname(*_args, **_kwargs):
            raise AssertionError("Local candidate IPC must not perform hostname lookups")

        monkeypatch.setattr("socket.getfqdn", unavailable_hostname)
    registry = NativeDshWorkspaceRegistry(tmp_path / "workspaces", database_path=db_path)
    registry.prepare_document("actual-session", None, None)
    scope = registry.activate("actual-session")
    registry.enable_cli_link()
    connection = registry.cli_connection("actual-session")
    descriptor = json.loads(Path(connection).read_text(encoding="utf-8"))
    assert registry._cli_link._server.server_name == descriptor["host"] == "127.0.0.1"
    assert registry._cli_link._server.server_port == descriptor["port"]
    request = tmp_path / "edit.json"
    request.write_text(json.dumps({"expected_revision": 0, "reason": "actual CLI process",
        "operations": [{"op": "replace", "path": "/title", "value": "真实进程修改"}]}), encoding="utf-8")
    base = [sys.executable, "-m", "genshin_app", "agent", "candidate"]
    environment = {**os.environ, "PYTHONUTF8": "1"}
    try:
        changed = subprocess.run([*base, "edit", "--connection", connection, "--session-id", "actual-session",
                                  "--request", str(request)], capture_output=True, text=True,
                                 encoding="utf-8", timeout=30, env=environment, check=False)
        assert changed.returncode == 0, changed.stderr
        assert json.loads(changed.stdout)["data"]["revision"] == 1
        assert scope.candidate_session.candidate["title"] == "真实进程修改"
        read = subprocess.run([*base, "read", "--connection", connection, "--session-id", "actual-session"],
                              capture_output=True, text=True, encoding="utf-8", timeout=30, env=environment, check=False)
        assert read.returncode == 0, read.stderr
        assert json.loads(read.stdout)["data"]["input"]["title"] == "真实进程修改"
        assert len(registry._workspaces) == 1
    finally:
        registry.close()


def test_disconnected_cli_cancels_existing_engine_execution_scope(tmp_path):
    from genshin_engine.execution_control import (
        ExecutionCancelled,
        check_execution_cancelled,
    )

    started, cancelled = threading.Event(), threading.Event()
    def dispatch(_operation, _sid, _request):
        started.set()
        try:
            while True:
                check_execution_cancelled()
                cancelled.wait(0.01)
        except ExecutionCancelled:
            cancelled.set()
            raise
    link = LiveCandidateCliLink(tmp_path, dispatch)
    descriptor = json.loads(link.descriptor_for("one").read_text())
    connection = HTTPConnection("127.0.0.1", descriptor["port"])
    try:
        body = json.dumps({"instance": descriptor["instance"], "operation": "calculate",
                           "session_id": "one", "request": {}})
        connection.request("POST", "/candidate", body=body,
            headers={"Authorization": "Bearer " + descriptor["token"]})
        assert started.wait(2)
        connection.close()
        assert cancelled.wait(3), "disconnect must reach the actual engine cancellation scope"
    finally:
        connection.close()
        link.close()


def test_retirement_closes_listener_even_if_mux_cancel_delivery_failed(tmp_path, db_path):
    registry = NativeDshWorkspaceRegistry(tmp_path, database_path=db_path)
    closed = []
    class Link:
        def close(self):
            closed.append(True)
    def fail(_request):
        raise RuntimeError("fixture cancel send failed")
    registry.enable_cli_link(settings_cancel=fail)
    registry._cli_link = Link()
    registry._cli_settings_calls.add("one-request")
    with pytest.raises(RuntimeError, match="fixture cancel send failed"):
        registry.close_cli_link()
    assert closed == [True]
    assert registry._cli_link is None
    registry._cli_settings_calls.clear()
    registry.close()


def test_real_live_metadata_crosses_python_prompt_and_node_domain_binding(tmp_path, db_path):
    """Actual metadata producer, transport decoder and full DSH assembly; no model/API."""
    from pathlib import Path

    from genshin_app.agent_chat.native_dsh_domain_calls import NativeDshDomainCalls
    from tests.pinned_native_node import pinned_native_node
    node = pinned_native_node(required=False)
    if node is None:
        pytest.skip("Pinned Node runtime is not installed")
    root = Path(__file__).resolve().parents[1]
    registry = NativeDshWorkspaceRegistry(tmp_path / "domain", database_path=db_path)
    registry.prepare_document("metadata-session", None, None)
    registry.enable_cli_link()
    connection = registry.cli_connection("metadata-session")
    try:
        registry.activate("metadata-session")
        bound = NativeDshDomainCalls(registry.resolve_target)("dsh_app_bind_request", {
            "contract": "genshin-native-dsh-domain-bind-v1",
            "request_id": "metadata-request", "session_id": "metadata-session"})
        assert bound["runtime_context"]["candidate_cli"]["connection_file"] == connection
        descriptor = json.loads(Path(connection).read_text("utf-8"))
        payload = tmp_path / "bound.json"
        payload.write_text(json.dumps(bound), encoding="utf-8")
        script = r"""
import assert from 'node:assert/strict';
import {readFile, mkdir} from 'node:fs/promises';
import {join} from 'node:path';
import {randomUUID} from 'node:crypto';
const root = REPO;
const {createNativeDshSessionHost} = await import(root+'/agent-runtime/src/native-dsh-session-host.mjs');
const {NativeDshDomainTransport} = await import(root+'/agent-runtime/src/native-dsh-domain-transport.mjs');
const bound=JSON.parse(await readFile(PAYLOAD,'utf8'));
let requests=0,host,peer;
const transport = new NativeDshDomainTransport({send: async packet=>{
 const request=JSON.parse(packet.payload.toString('utf8'));
 assert.equal(packet.payloadKind,'dsh_app_bind_request');
 const value={...bound,request_id:request.request_id,session_id:request.session_id};
 transport.accept({header:{lane:'remote_unary',payload_kind:'dsh_app_bind_result',correlation_id:packet.correlationId},
  payload:Buffer.from(JSON.stringify(value))});
}});
globalThis.fetch=async()=>{requests++;throw new Error('unexpected_external_fetch');};
try {
 const profile=PROFILE;await mkdir(join(profile,'media'),{recursive:true});
 host=await createNativeDshSessionHost({profileRoot:profile,mirrorRoot:join(profile,'media'),domainTransport:transport});
 peer=host.createPeer();
 const result=await host.handler.fetch(new Request('https://genshin-shell.invalid/api/session/create',{
 method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify({type:'client-request',rpcId:randomUUID(),
 method:'session/create',payload:{args:{request:{sessionId:'metadata-session'}}}})}),peer);
 const response=await result.json();assert.equal(response.result.ok,true,JSON.stringify(response));
 const agent=host.context.agents.get('metadata-session');
 const assembled=await host.context.systemPrompt.assemble({scope:agent});
 const serialized=JSON.stringify(assembled);
 assert.ok(serialized.includes('agent candidate edit'));
 assert.ok(serialized.includes('agent candidate compose'));
 assert.ok(serialized.includes('connection_file'));
 assert.ok(serialized.includes('DSH原生权限'));
 assert.equal(serialized.includes('你没有 shell'),false);
 assert.equal(serialized.includes(bound.snapshot.writer_fence),false);
 assert.equal(serialized.includes(TOKEN),false);
 const app=JSON.parse(assembled.contexts.find(row=>row.name==='genshin:app-domain').text);
 assert.equal(app.app_context.candidate_cli.connection_file,CONNECTION);
 assert.ok(assembled.tools.some(tool=>tool.name==='gs_patch_whiteboard'));
 assert.ok(assembled.tools.some(tool=>!tool.name.startsWith('gs_')));
 assert.equal(requests,0);console.log(JSON.stringify({status:'passed',tool_count:assembled.tools.length}));
}finally{transport.close();await peer?.dispose();await host?.dispose();}
"""
        substitutions = {"REPO": root.as_uri(), "PAYLOAD": str(payload), "PROFILE": str(tmp_path / "node-profile"),
                         "TOKEN": descriptor["token"], "CONNECTION": connection}
        for marker, value in substitutions.items():
            script = script.replace(marker, json.dumps(value))
        result = subprocess.run([node, "--input-type=module", "-e", script], cwd=root / "agent-runtime",
            capture_output=True, text=True, encoding="utf-8", check=False, timeout=90)
        assert result.returncode == 0, result.stderr
        assert json.loads(result.stdout.strip().splitlines()[-1])["status"] == "passed"
    finally:
        registry.close()


def test_actual_cli_settings_defaults_and_scoped_connection_use_official_remote(tmp_path, db_path, capsys):
    from genshin_app.cli import main
    calls = []
    def remote(request):
        calls.append(request)
        return {"ok": True, "value": {"official_remote_called": request["endpoint"]}}
    registry = NativeDshWorkspaceRegistry(tmp_path / "domain", database_path=db_path)
    registry.prepare_document("settings-session", None, None)
    registry.enable_cli_link(settings_remote=remote)
    connection = registry.cli_connection("settings-session")
    source = tmp_path / "config-request.json"
    value = {"entry_id": "an-entry", "expected_fingerprint": "a" * 64,
             "operations": [{"op": "unset", "path": "/a-field"}]}
    source.write_text(json.dumps(value), encoding="utf-8")
    try:
        for operation, arguments in [("show", ["--entry", "an-entry"]),
                                     ("set", ["--input", str(source)]), ("reset", ["--input", str(source)])]:
            assert main(["--data-dir", str(tmp_path), "agent", "dsh-settings", operation,
                "--connection", connection, *arguments]) == 0
            response = json.loads(capsys.readouterr().out)["data"]
            expected = "genshinProfile/configDescribe" if operation == "show" else "genshinProfile/configRequestEdit"
            assert response["official_remote_called"] == expected
        assert calls[0]["payload"]["args"]["request"] == {"entry_id": "an-entry"}
        assert calls[1]["payload"]["args"]["agentId"] == "settings-session"
        assert calls[2]["payload"]["args"]["agentId"] == "settings-session"
        assert calls[1]["payload"]["args"]["request"] == value
        assert calls[2]["payload"]["args"]["request"] == value
        assert not registry._workspaces
    finally:
        registry.close()
