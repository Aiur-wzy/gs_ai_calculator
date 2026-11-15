'use strict';
const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),vm=require('node:vm'),R=require('./helpers/gui_runtime.cjs');
const web=path.join(__dirname,'../src/genshin_app/gui/web');
function load(){const world={console};world.window=world;vm.createContext(world);R.loadWebFile(world,path.join(web,'agent-workbench-state-adapter.js'));return world.AgentWorkbenchStateAdapter;}

test('state adapter owns shell creation and the Store plus sidecar read projection',()=>{
  const Adapter=load(),shell=Adapter.createShell({createUiState:()=>({view:'chat'})});
  assert.equal(shell.state.sessions?.constructor?.name,'Map');assert.deepEqual(shell.state.workbench,{view:'chat'});
  const projected={messages:[{id:'message'}],tools:[],cursor:7,projectionRevision:3,store_revision:3,connectionKind:'native_api'};
  const adapter=Adapter.create({state:shell.state,eventStore:{snapshot:id=>id==='session-one'?projected:null},selectors:{selectSession:value=>value,createUiState:()=>({view:'chat'})},clone:value=>structuredClone(value)});
  const session={session_id:'session-one',candidate:{revision:2,receipt_fingerprint:'receipt-two'},appActions:[],applicationIntent:null};
  const model=adapter.readModel(session);assert.equal(model.cursor,7);assert.equal(model.candidate.receipt_fingerprint,'receipt-two');assert.equal(adapter.ackCursor(session),7);
  assert.deepEqual(adapter.workbenchState(session),{view:'chat'});assert.equal(adapter.workbenchState(session),session.workbench);
});

test('candidate and App-action sidecars mutate through one adapter owner',()=>{
  const Adapter=load(),state={};const adapter=Adapter.create({state,eventStore:{snapshot:()=>null},selectors:{selectSession:value=>value},clone:value=>structuredClone(value)}),session={appActions:[],candidate:null};
  assert.equal(adapter.publishCandidate(session,{revision:1,receipt_fingerprint:'one'}),true);assert.equal(session.candidate.revision,1);
  assert.equal(adapter.recordAppAction(session,{action_id:'action-one',status:'queued'}),true);assert.equal(adapter.recordAppAction(session,{action_id:'action-one',status:'completed'}),true);assert.equal(session.appActions.length,1);assert.equal(session.appActions[0].status,'completed');
});

test('production facade contains no state construction, read merge, or external raw AC dependency',()=>{
  const chat=fs.readFileSync(path.join(web,'agent-chat.js'),'utf8');
  assert.doesNotMatch(chat,/state:\{open:false|const result=\{\.\.\.session,\.\.\.projected|createShell\(/);
  assert.doesNotMatch(chat,/\bAC\.state\.[A-Za-z_$][\w$]*\s*=/);assert.doesNotMatch(chat,/session\.workbench\s*=/);
  for(const file of ['app.js','workspace.js','whiteboard.js','plans.js'])assert.doesNotMatch(fs.readFileSync(path.join(web,file),'utf8'),/window\.AC|\bAC\./,file);
  const files=fs.readdirSync(web).filter(name=>name.endsWith('.js')),writers=[];
  for(const file of files){const source=fs.readFileSync(path.join(web,file),'utf8');if(/session\.(?:candidate|appActions)\s*=/.test(source))writers.push(file);}
  assert.deepEqual(writers,['agent-workbench-state-adapter.js']);
});
