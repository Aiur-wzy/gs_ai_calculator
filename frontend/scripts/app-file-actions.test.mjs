import assert from "node:assert/strict";
import {test} from "node:test";
import {legacyDomainFixture} from "./legacy-domain-test-fixture.mjs";
async function fixture(t){
  let view="whiteboard",picked="D:\\test\\chosen.whiteboard.json";const calls=[],input={value:"retained.plan.json"};
  const {context,close}=await legacyDomainFixture(["whiteboard.js","agent-document-adapter.js","app-file-actions.js"],
    {PL:{state:{plan:null,sourceEpoch:0},draftChanged:()=>false},RT:{state:{},tpl:()=>null},RS:{},WS:{tab:()=>view},
      callApi:async(name,args)=>{calls.push([name,args]);return {code:0,data:{cancelled:picked===null,path:picked}};}});
  t.after(close);
  context.WB.render=()=>{};context.WB.replace({board:{board_id:"board-one",revision:0,title:"白板",team:[],actions:[]},path:"",file_sha:null},false,false);
  const shows=[],port=context.AppEditorFileActions.create({root:{hidden:false},show:async page=>{view=page;shows.push(page);}});
  return {context,port,calls,shows,input,setPicked:value=>{picked=value;}};
}
test("user Save As uses the native picker and existing whiteboard save consumer",async t=>{
  const {context:c,port,calls}=await fixture(t);let saved;c.WB.save=async path=>{saved=path;return true;};const result=await port.execute({action:"save_as",suggested_name:"safe.whiteboard.json"},port.capture());
  assert.equal(result.status,"completed");assert.equal(saved,"D:\\test\\chosen.whiteboard.json");assert.equal(calls[0][0],"dsh_client_file_dialog");assert.equal(calls[0][1].action,"save");assert.equal(calls[0][1].suggested_name,"safe.whiteboard.json");assert.equal(c.AC,undefined);
});
test("picker cancellation never invokes save and reports cancellation",async t=>{
  const {context:c,port,setPicked}=await fixture(t);setPicked(null);let saved=false;c.WB.save=async()=>{saved=true;return true;};
  const result=await port.execute({action:"save_as"},port.capture());assert.equal(result.status,"cancelled");assert.equal(saved,false);
});
test("a user edit while choosing a path preserves the document and does not save",async t=>{
  const {context:c,port}=await fixture(t);let saved=false;c.WB.save=async()=>{saved=true;return true;};c.callApi=async()=>{c.WB.state.board.title="human edit";c.WB.invalidateAgentPayload();return {code:0,data:{cancelled:false,path:"chosen.json"}};};
  const result=await port.execute({action:"save_as"},port.capture());assert.equal(result.status,"failed");assert.equal(saved,false);assert.equal(c.WB.state.board.title,"human edit");
});
test("navigation completion is based on the real App display port",async t=>{
  const {port,shows}=await fixture(t);const result=await port.execute({action:"navigate_result"},port.capture());assert.equal(result.status,"completed");assert.deepEqual(shows,["result"]);
});
test("export invokes the existing verified result consumer and does not report a failed export as success",async t=>{
  const {context:c,port,shows}=await fixture(t);let format;c.RS.exportResult=async value=>{format=value;return false;};
  const result=await port.execute({action:"export_result"},port.capture());assert.equal(format,"json");assert.equal(result.status,"failed");assert.equal(shows.at(-1),"result");
});

test("a save error with unconfirmed side effects stays unknown instead of being reported failed",async t=>{
  const {context:c,port}=await fixture(t);
  c.WB.save=async(_path,options)=>{options.onResponse({code:1,reason:"file_conflict",blocking_scope:"request",
    details:{published_paths:["chosen.json"],unknown_paths:[],pending_paths:[]}});return false;};
  await assert.rejects(port.execute({action:"save_as"},port.capture()),/app_action_execution_outcome_unknown/);
});

test("the publisher can prove a pre-write conflict without creating an unknown outcome",async t=>{
  const {context:c,port}=await fixture(t);
  c.WB.save=async(_path,options)=>{options.onResponse({code:1,reason:"file_conflict",
    details:{published_paths:[],unknown_paths:[],pending_paths:["chosen.json"]}});return false;};
  assert.equal((await port.execute({action:"save_as"},port.capture())).status,"failed");
});
