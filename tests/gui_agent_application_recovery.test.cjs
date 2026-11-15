"use strict";
const test=require("node:test"),assert=require("node:assert/strict"),path=require("node:path"),vm=require("node:vm"),R=require("./helpers/gui_runtime.cjs");

function load(){const world={console};world.window=world;vm.createContext(world);R.loadWebFile(world,path.join(__dirname,"../src/genshin_app/gui/web/agent-application-recovery.js"));return world.AgentApplicationRecovery;}
const copy=value=>JSON.parse(JSON.stringify(value));
const deferred=()=>{let resolve,reject;const promise=new Promise((yes,no)=>{resolve=yes;reject=no;});return{promise,resolve,reject};};
const capability=letter=>`wdc_v1_${letter.repeat(43)}`;

function harness(changes={}){
  const Module=load(),calls=[],notices=[];
  let board={kind:"genshin-whiteboard",board_id:"board-one",title:"原白板",revision:1,team:[],actions:[]},calculated=null,tab="whiteboard",appAction=false,pending=false,frozen=false,reconciles=0,confirms=0,rendererWrites=0;
  const state={selected:"session-one",busy:null,closing:false,request:0,viewEpoch:3},session={session_id:"session-one",epoch:4,kind:"whiteboard",title:"原白板",live:false,canResume:true,error:null};
  const stringify=JSON.stringify;
  const capture=()=>{const document=copy(board),applicationPayload={kind:"whiteboard",board:copy(board),calculated:copy(calculated)};return{kind:"whiteboard",document,applicationPayload,json:stringify(document),payloadJson:stringify(applicationPayload),title:board.title,key:board.board_id};};
  const currentMatches=base=>!!base&&base.kind==="whiteboard"&&base.json===stringify(board)&&base.payloadJson===stringify({kind:"whiteboard",board,calculated});
  const renderer={
    restoreAgentResultOnly(delivery){rendererWrites++;calculated=copy(delivery.calculated);return true;},
    applyAgentDraft(next,_issues,delivery){rendererWrites++;board=copy(next);calculated=delivery?copy(delivery.calculated):null;},
  };
  const defaults={
    state,api:async(name,...args)=>{calls.push([name,...copy(args)]);if(name==="agent_resume")return{data:{}};throw new Error(`unexpected:${name}`);},
    selected:()=>session,active:()=>null,checkpointFrozen:()=>frozen,hasUnsettledAppAction:()=>appAction,hasPendingApplication:()=>pending,
    pendingApplicationSession:()=>pending?session:null,capture,workspace:()=>({document_kind:"whiteboard"}),currentMatches,
    clone:copy,stringify,whiteboard:()=>renderer,currentTab:()=>tab,confirm:()=>{confirms++;return true;},
    notice:(message,error=false)=>notices.push({message,error}),refresh:()=>{},errorText:(_response,fallback)=>fallback,
    receiveCommand:()=>({accepted:true}),receiveProtocolText:reason=>`协议失败：${reason}`,rejectExternal:()=>{},reconcile:async()=>{reconciles++;return{status:"read"};},
  };
  const options={...defaults,...changes};
  const controller=Module.create(options);
  return{Module,controller,state,session,calls,notices,renderer,capture,currentMatches,
    board:()=>board,result:()=>calculated,setBoard:value=>board=copy(value),setTab:value=>tab=value,setAppAction:value=>appAction=value,setPending:value=>pending=value,setFrozen:value=>frozen=value,
    confirms:()=>confirms,reconciles:()=>reconciles,rendererWrites:()=>rendererWrites};
}

function committed(base,total=654){return{code:0,data:{status:"committed",kind:"whiteboard",application_kind:"whiteboard_calculated",revision:2,receipt_fingerprint:"receipt-two",application_intent:{status:"committed",intent:{state:"committed",kind:"whiteboard",revision:2,receipt_fingerprint:"receipt-two"}},board:copy(base.document),calculated_delivery:{delivery_capability:capability("D"),calculated:{result:{summary:{total_damage:total}}}}}};}
function lastApplied(base,{mode="result_only",board=base.document,total=4321,kind="whiteboard_calculated"}={}){const projection={board_id:board.board_id,revision:7,receipt_fingerprint:"b".repeat(64),application_kind:kind};return{code:0,data:{status:"recovered",kind:"whiteboard",recovery_mode:mode,application_kind:kind,revision:7,receipt_fingerprint:"b".repeat(64),projection,board:copy(board),calculated_delivery:kind==="whiteboard_calculated"?{delivery_capability:capability("R"),calculated:{result:{summary:{total_damage:total}}}}:null}};}

test("cold committed recovery is the sole recover-render-resume owner and never invokes a provider",async()=>{
  let base;const h=harness({api:async(name,...args)=>{h.calls.push([name,...copy(args)]);if(name==="agent_recover_candidate_application")return committed(base);if(name==="agent_resume")return{data:{next_seq:0,events:[]}};throw new Error(`provider replay:${name}`);}});base=h.capture();
  assert.equal(await h.controller.recoverCommitted(h.session,base),true);assert.equal(h.rendererWrites(),1);assert.equal(h.result().result.summary.total_damage,654);assert.equal(h.session.live,true);assert.equal(h.session.appliedReceipt,"receipt-two");assert.deepEqual(h.calls.map(row=>row[0]),["agent_recover_candidate_application","agent_resume"]);assert.equal(h.state.busy,null);assert.doesNotMatch(h.notices.map(row=>row.message).join("\n"),/Host|投影|lineage|revision|cursor/i);
});

test("result-only last-applied recovery is automatic and preserves the board",async()=>{
  let base;const h=harness({api:async(name,...args)=>{h.calls.push([name,...copy(args)]);if(name==="agent_recover_last_applied_whiteboard_projection")return lastApplied(base);if(name==="agent_resume")return{data:{}};throw new Error(name);}});base=h.capture();const before=h.board();
  assert.equal(await h.controller.recoverLastApplied(h.session,base,false),true);assert.equal(h.confirms(),0);assert.deepEqual(h.board(),before);assert.equal(h.result().result.summary.total_damage,4321);assert.equal(h.session.live,true);
});

test("unsaved-target recovery requires explicit confirmation and a declined prompt writes nothing",async()=>{
  const h=harness({confirm:()=>false});const base=h.capture();
  assert.equal(await h.controller.recoverLastApplied(h.session,base,true),false);assert.equal(h.rendererWrites(),0);assert.equal(h.calls.length,0);assert.deepEqual(h.board(),base.document);
});

test("confirmed unsaved-target recovery restores a same-id board as dirty Renderer state",async()=>{
  let base;const target={kind:"genshin-whiteboard",board_id:"board-one",title:"Host未保存目标",revision:2,team:[],actions:[]};const h=harness({api:async(name,...args)=>{h.calls.push([name,...copy(args)]);if(name==="agent_recover_last_applied_whiteboard_projection")return lastApplied(base,{mode:"restore_unsaved_target",board:target,total:7654});if(name==="agent_resume")return{data:{}};throw new Error(name);}});base=h.capture();
  assert.equal(await h.controller.recoverLastApplied(h.session,base,true),true);assert.equal(h.confirms(),1);assert.equal(h.board().title,target.title);assert.equal(h.result().result.summary.total_damage,7654);assert.equal(h.session.lastAppliedRestorePending,false);
});

test("concurrent duplicate recovery clicks join one Host command and one Renderer write",async()=>{
  const wait=deferred();let base;const h=harness({api:async(name,...args)=>{h.calls.push([name,...copy(args)]);if(name==="agent_recover_last_applied_whiteboard_projection")return wait.promise;if(name==="agent_resume")return{data:{}};throw new Error(name);}});base=h.capture();
  const first=h.controller.recoverLastApplied(h.session,base,false),second=h.controller.recoverLastApplied(h.session,base,false);assert.equal(first,second);assert.equal(h.controller.owns(h.session),true);wait.resolve(lastApplied(base));assert.equal(await first,true);assert.equal(h.calls.filter(row=>row[0]==="agent_recover_last_applied_whiteboard_projection").length,1);assert.equal(h.rendererWrites(),1);assert.equal(h.controller.owns(h.session),false);
});

for(const mutation of ["tab","view","session","document","app action","pending application","checkpoint"])test(`recovery CAS rejects concurrent ${mutation} before Renderer mutation`,async()=>{
  const wait=deferred();let base;const h=harness({api:async(name,...args)=>{h.calls.push([name,...copy(args)]);if(name==="agent_recover_last_applied_whiteboard_projection")return wait.promise;if(name==="agent_resume")return{data:{}};throw new Error(name);}});base=h.capture();const operation=h.controller.recoverLastApplied(h.session,base,false);
  if(mutation==="tab")h.setTab("rotation");else if(mutation==="view")h.state.viewEpoch++;else if(mutation==="session")h.session.epoch++;else if(mutation==="document")h.setBoard({...h.board(),title:"用户并发编辑"});else if(mutation==="app action")h.setAppAction(true);else if(mutation==="pending application")h.setPending(true);else h.setFrozen(true);
  wait.resolve(lastApplied(base));assert.equal(await operation,false);assert.equal(h.rendererWrites(),0);assert.equal(h.calls.filter(row=>row[0]==="agent_resume").length,0);
});

for(const blocker of ["pending candidate","App action","checkpoint freeze"])test(`${blocker} blocks recovery admission without a Host request`,async()=>{
  const h=harness();if(blocker==="pending candidate")h.setPending(true);else if(blocker==="App action")h.setAppAction(true);else h.setFrozen(true);
  assert.equal(await h.controller.recoverLastApplied(h.session,h.capture(),false),false);assert.equal(h.calls.length,0);assert.equal(h.rendererWrites(),0);
});

test("conflicting Host projection never overwrites the current document",async()=>{
  let base;const h=harness({api:async(name,...args)=>{h.calls.push([name,...copy(args)]);if(name==="agent_recover_last_applied_whiteboard_projection"){const value=lastApplied(base);value.data.projection.board_id="other-board";return value;}throw new Error(name);}});base=h.capture();
  assert.equal(await h.controller.recoverLastApplied(h.session,base,false),false);assert.equal(h.rendererWrites(),0);assert.deepEqual(h.board(),base.document);assert.match(h.notices.at(-1).message,/版本信息不一致/);assert.doesNotMatch(h.notices.at(-1).message,/Host|投影|lineage|revision|cursor/i);
});

test("lost resume acknowledgement performs one read-only reconciliation and never replays provider or write",async()=>{
  let base;const h=harness({api:async(name,...args)=>{h.calls.push([name,...copy(args)]);if(name==="agent_recover_candidate_application")return committed(base);if(name==="agent_resume")return{code:1,reason:"transport_lost"};throw new Error(`provider replay:${name}`);}});base=h.capture();
  assert.equal(await h.controller.recoverCommitted(h.session,base),false);assert.equal(h.rendererWrites(),1);assert.equal(h.reconciles(),1);assert.equal(h.session.live,false);assert.equal(h.session.canResume,true);assert.deepEqual(h.calls.map(row=>row[0]),["agent_recover_candidate_application","agent_resume"]);
});

test("malformed resume receipt is reconciled read-only without a second resume",async()=>{
  let base,rejected=0;const h=harness({api:async(name,...args)=>{h.calls.push([name,...copy(args)]);if(name==="agent_recover_candidate_application")return committed(base);if(name==="agent_resume")return{data:{malformed:true}};throw new Error(name);},receiveCommand:()=>({accepted:false,reason:"bad_cursor"}),rejectExternal:()=>{rejected++;}});base=h.capture();
  assert.equal(await h.controller.recoverCommitted(h.session,base),false);assert.equal(rejected,1);assert.equal(h.reconciles(),1);assert.equal(h.calls.filter(row=>row[0]==="agent_resume").length,1);
});

test("recovery controller fails closed when mandatory ownership ports are omitted",()=>{const Module=load();assert.throws(()=>Module.create({state:{}}),/application_recovery_api_missing/);});
