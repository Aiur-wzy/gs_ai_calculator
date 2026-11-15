import assert from "node:assert/strict";
import {test} from "node:test";
import {legacyDomainFixture} from "./legacy-domain-test-fixture.mjs";
async function fixture(t){
  const {context,close}=await legacyDomainFixture(["whiteboard.js","agent-document-adapter.js","candidate-application.js"],
    {PL:{state:{plan:null,request:0,sourceEpoch:0},draftChanged:()=>false},RT:{state:{}},RS:{},WS:{tab:()=>"whiteboard"}});
  t.after(close);
  context.WB.render=()=>{};
  return {context,port:context.CandidateEditorApplication.create()};
}
test("real WB apply and pure adapter return the actual payload without a legacy Agent owner",async t=>{
  const {context:c,port}=await fixture(t);c.WB.replace({board:{board_id:"board",revision:0,title:"original",team:[],actions:[]},path:"retained.json",file_sha:"original-sha"},false,false);
  const before=port.capture("whiteboard"),calculated={plan:{},result:{summary:{total_damage:12345}}};
  const accepted={kind:"whiteboard",board:{board_id:"board",revision:2,title:"candidate",team:[],actions:[]},calculated_delivery:{delivery_capability:"trusted-test-cap",calculated}};
  const actual=await port.applyReviewed(accepted,before,"exact-app-conversation");
  assert.equal(actual.document.title,"candidate");assert.equal(actual.applicationPayload.calculated.result.summary.total_damage,12345);assert.equal(c.WB.state.path,"retained.json");assert.equal(c.WB.state.fileSha,"original-sha");
  assert.equal(c.WB.state.reusableResult.owner,"native_dsh");assert.equal(c.WB.state.reusableResult.sessionId,"exact-app-conversation");assert.equal(c.AC,undefined);assert.equal(c.AgentWorkbenchApp,undefined);
});
test("real document CAS rejects changed formal content and new-document absence races",async t=>{
  const {context:c,port}=await fixture(t),stamp=port.absence("whiteboard");assert.equal(port.capture("whiteboard"),null);
  const seed={board_id:"seed",revision:0,team:[],actions:[]};const before=port.createDocument("whiteboard",seed,stamp);assert.equal(c.WB.state.board.board_id,"seed");assert.equal(c.WB.state.savedJson,null);
  assert.throws(()=>port.createDocument("whiteboard",seed,stamp));c.WB.state.board.title="human edit";c.WB.invalidateAgentPayload();
  await assert.rejects(port.applyReviewed({board:{...seed,title:"candidate"}},before,"conversation"));assert.equal(c.WB.state.board.title,"human edit");
});
