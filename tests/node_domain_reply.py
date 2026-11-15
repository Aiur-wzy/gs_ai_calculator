"""Offline complete DSH provider/tool projection of an actual Python Host reply."""
from __future__ import annotations

import json
import subprocess

from tests.pinned_native_node import ROOT, pinned_native_node


def assert_node_domain_reply(tmp_path, snapshot, result, arguments):
    node = pinned_native_node()
    fixture = tmp_path / "node-domain-reply.json"
    fixture.write_text(json.dumps({"snapshot": snapshot, "result": result, "arguments": arguments}), encoding="utf-8")
    script = r"""
import assert from 'node:assert/strict';
import {readFile,mkdir} from 'node:fs/promises';
import {join} from 'node:path';
import {randomUUID} from 'node:crypto';
const root=REPO;
const {createNativeDshSessionHost}=await import(root+'/agent-runtime/src/native-dsh-session-host.mjs');
const {NativeDshDomainTransport}=await import(root+'/agent-runtime/src/native-dsh-domain-transport.mjs');
const {createNativeDshDomainFixtureFetch}=await import(root+'/agent-runtime/src/native-dsh-domain-fixture-fetch.mjs');
const input=JSON.parse(await readFile(FIXTURE,'utf8'));
assert.ok(Buffer.byteLength(JSON.stringify(input.result))>256*1024);
let host,peer,calls=0;
const transport=new NativeDshDomainTransport({send:async packet=>{
 const request=JSON.parse(packet.payload.toString('utf8'));
 let kind,value;
 if(packet.payloadKind==='dsh_app_bind_request'){
  kind='dsh_app_bind_result';value={contract:'genshin-native-dsh-domain-bound-v1',request_id:request.request_id,
   session_id:request.session_id,snapshot:input.snapshot};
 }else{
  assert.equal(packet.payloadKind,'dsh_app_call_request');calls++;
  kind='dsh_app_call_result';value={contract:'genshin-native-dsh-domain-result-v1',request_id:request.request_id,
   session_id:request.session_id,call_id:request.call_id,call_seq:request.call_seq,status:'settled_success',
   result:input.result,next_snapshot:input.snapshot};
 }
 transport.accept({header:{lane:'remote_unary',payload_kind:kind,correlation_id:packet.correlationId},
  payload:Buffer.from(JSON.stringify(value))});
}});
globalThis.fetch=createNativeDshDomainFixtureFetch({calls:[{tool:'gs_diagnose_result',arguments:input.arguments}]});
try{
 const profile=PROFILE;await mkdir(join(profile,'media'),{recursive:true});
 host=await createNativeDshSessionHost({profileRoot:profile,mirrorRoot:join(profile,'media'),domainTransport:transport});
 peer=host.createPeer();
 await host.kernel.secretController.set({provider:'deepseek-official',credential_generation:1,secret:'offline-fixture'});
 async function rpc(endpoint,request){
  const response=await host.handler.fetch(new Request(`https://genshin-shell.invalid/api/session/${endpoint}`,{
   method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify({type:'client-request',rpcId:randomUUID(),
    method:`session/${endpoint}`,payload:{args:{request}}})}),peer);
  const body=await response.json();assert.equal(body.result.ok,true,JSON.stringify(body));
 }
 await rpc('create',{sessionId:'large-reply'});
 await rpc('prompt',{sessionId:'large-reply',requestId:randomUUID(),mode:'queue',content:[{type:'text',text:'Exercise the declared tool'}]});
 await host.context.agents.get('large-reply').whenIdle();
 assert.equal(calls,1);
 const log=await host.context.sessionQuery.readSession('large-reply');
 const responses=log.events.filter(event=>event.type==='tool/result');
 assert.ok(responses.some(event=>JSON.stringify(event.data).includes('genshin-energy-trials-v1')));
 assert.equal(responses.some(event=>JSON.stringify(event.data).includes('domain_tool_result_requires_resource_projection')),false);
 assert.ok(log.events.some(event=>event.type==='assistant/message'));
 console.log(JSON.stringify({status:'passed',calls}));
}finally{transport.close();await peer?.dispose();await host?.dispose();}
"""
    for marker, value in {"REPO": ROOT.as_uri(), "FIXTURE": str(fixture), "PROFILE": str(tmp_path / "reply-node-profile")}.items():
        script = script.replace(marker, json.dumps(value))
    completed = subprocess.run([str(node), "--input-type=module", "-e", script], cwd=ROOT / "agent-runtime",
        capture_output=True, text=True, encoding="utf-8", check=False, timeout=90)
    assert completed.returncode == 0, completed.stderr
    assert json.loads(completed.stdout.strip().splitlines()[-1]) == {"status": "passed", "calls": 1}
