import assert from "node:assert/strict";
import {spawnSync} from "node:child_process";
import {fileURLToPath} from "node:url";
import {legacyDomainFixture} from "./legacy-domain-test-fixture.mjs";
import {test} from "node:test";

const root=fileURLToPath(new URL("../../",import.meta.url));
function python(code,input){
  const result=spawnSync("python",["-c",code],{cwd:root,input,encoding:"utf8",env:{...process.env,PYTHONUTF8:"1"}});
  assert.equal(result.status,0,result.stderr);return JSON.parse(result.stdout);
}
async function fixture(t){
  const provided=python("import json; from genshin_app.whiteboard import new_whiteboard; from genshin_app.whiteboard_revision import revision_transition_capabilities; print(json.dumps({'board':new_whiteboard(),'revision_policy':revision_transition_capabilities()}))");
  const {context,close}=await legacyDomainFixture(["whiteboard.js"]);t.after(close);
  context.WB.render=()=>{};context.WB.replace(JSON.parse(JSON.stringify(provided)),false,false);
  return {context,provided};
}

test("synchronous GUI edits propose the Host revision contract and Python accepts the batched edit",async t=>{
  const {context:c,provided}=await fixture(t);
  c.WB.change(board=>{board.notes="第一次编辑";});
  c.WB.change(board=>{board.notes="第二次编辑";});
  assert.equal(c.WB.state.board.revision,2);
  const receipt=python("import json,sys; from genshin_app.whiteboard_revision import validate_whiteboard_revision_transition; p=json.load(sys.stdin); print(json.dumps(validate_whiteboard_revision_transition(p['before'],p['after'])))",JSON.stringify({before:provided.board,after:c.WB.state.board}));
  assert.equal(receipt.contract_id,provided.revision_policy.contract_id);
  assert.equal(receipt.previous_revision,0);assert.equal(receipt.next_revision,2);
  assert.deepEqual(receipt.fields,["notes"]);
  // The renderer has only an in-memory proposal; no tool/save/approval RPC exists here.
  assert.equal(c.callApi,undefined);
});

test("an invalid or incompatible revision policy rejects before changing the board",async t=>{
  const {context:c}=await fixture(t),before=c.WB.state.board.notes;
  c.WB.state.revisionPolicy.maximum=0;
  assert.throws(()=>c.WB.change(board=>{board.notes="must not run";}));
  assert.equal(c.WB.state.board.notes,before);assert.equal(c.WB.state.board.revision,0);
  c.WB.state.revisionPolicy.contract_id="unknown-future-contract";
  assert.throws(()=>c.WB.change(board=>{board.notes="must not run";}));
  assert.equal(c.WB.state.board.notes,before);
});
