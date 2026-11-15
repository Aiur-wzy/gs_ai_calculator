// Historical compatibility units below do not qualify the production DSH UI.
// Production ownership assertions and migrated App-CAS tests use the current entry.
'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

function load() {
  const world = {}; world.window = world; vm.createContext(world);
  for (const name of ['agent-workbench-contract.js','agent-workbench-store.js','agent-workbench-selectors.js']) {
    vm.runInContext(fs.readFileSync(path.join(__dirname,'../src/genshin_app/gui/web',name),'utf8'),world,{filename:name});
  }
  return world;
}
const plain = value => JSON.parse(JSON.stringify(value));
const envelope = (after, overrides = {}) => ({
  session_id:'selector-session', requested_after_seq:after, next_seq:after,
  reset_required:false, events:[], state:'completed', active:false,
  credential_state:'saved', provider_state:'verified', runtime_state:'ready',
  conversation_state:'idle', turn_state:'completed', document_state:'candidate_ready', inbox_state:'empty',
  ...overrides,
});
const stage = (store, after, data) => store.stage({
  expected_session_id:data.session_id, requested_after_seq:after, envelope:data,
});

test('selectors derive frozen compatible Chat, Trajectory and seven-axis models from one snapshot', () => {
  const world=load(),store=world.AgentWorkbenchStore.createTransactionalLegacyStore();
  const tx=stage(store,0,envelope(0,{
    next_seq:4,reset_required:true,snapshot_seq:4,run_id:'run-one',events:[],conversation_state:'action_required',
    baseline:{baseline_seq:4,completeness:'complete',partial_reasons:[],
      messages:[{message_id:'m1',role:'user',message:'开始',run_id:'run-one'},{message_id:'m2',role:'assistant',message:'完成',run_id:'run-one'}],
      tools:[{tool_id:'t1',name:'计算',state:'completed',message:'已完成',run_id:'run-one',last_source_seq:3}],
      usage:{input_tokens:10,output_tokens:5,total_tokens:15},budget_statuses:[],
      app_action_refs:[{action_id:'a1',action:'save_as',status:'queued',workspace:{path:'C:/private'}}]},
    candidate:{revision:7,ready:true,receipt_fingerprint:'receipt',calculated:{large:'x'.repeat(1000)}},
    application_intent:{
      conversation_id:'selector-session',status:'prepared',recovery_required:false,
      intent:{state:'prepared',kind:'whiteboard',revision:7,receipt_fingerprint:'receipt'},
    },
  }));
  const receipt=store.commit(tx);assert.equal(receipt.ok,true);
  const snapshot=receipt.snapshot,selectors=world.AgentWorkbenchSelectors;
  const session=selectors.selectSession(snapshot);assert.equal(session,selectors.selectSession(snapshot));assert.equal(Object.isFrozen(session),true);
  assert.deepEqual(plain(session.messages.map(row=>row.key)),['message:m1','message:m2']);
  assert.equal(session.tools[0].key,'tool:t1');assert.equal(session.candidate.revision,7);assert.equal(session.appActions[0].action_id,'a1');
  const chat=selectors.selectChat(snapshot);assert.equal(chat,selectors.selectChat(snapshot));assert.equal(chat.messages,session.messages);
  const trajectory=selectors.selectTrajectory(snapshot,{query:'计算'});assert.equal(trajectory,selectors.selectTrajectory(snapshot,{query:'计算'}));assert.deepEqual(plain(trajectory.map(row=>row.id)),['tool:t1']);
  const model=selectors.selectWorkbench(snapshot,{client:{connection:'ready',connection_matches:true,selection_ready:true}});
  assert.deepEqual(plain(model.axes),{credential_state:'saved',provider_state:'verified',runtime_state:'ready',conversation_state:'action_required',turn_state:'completed',document_state:'candidate_ready',inbox_state:'empty'});
  assert.equal(model.turn_outline[0].id,'run-one');assert.equal(model.actions.send,false);
  const encoded=JSON.stringify(model);assert.doesNotMatch(encoded,/calculated|C:\/private|workspace|large/);
});

test('selector caches preserve unchanged entity identity and replace only updated rows', () => {
  const world=load(),store=world.AgentWorkbenchStore.createTransactionalLegacyStore(),selectors=world.AgentWorkbenchSelectors;
  let tx=stage(store,0,envelope(0,{next_seq:1,state:'running',active:true,events:[{seq:1,run_id:'run',type:'message',message_id:'m1',role:'assistant',message:'开'}]}));assert.equal(store.commit(tx).ok,true);
  const first=selectors.selectSession(store.snapshot('selector-session')),firstMessage=first.messages[0];
  tx=stage(store,1,envelope(1,{next_seq:2,state:'running',active:true,events:[{seq:2,run_id:'run',type:'tool',tool_id:'t1',name:'读取',state:'running'}]}));assert.equal(store.commit(tx).ok,true);
  const secondSnapshot=store.snapshot('selector-session'),second=selectors.selectSession(secondSnapshot);
  assert.equal(second.messages[0],firstMessage);assert.equal(second.tools[0].id,'t1');assert.notEqual(second,first);
  assert.equal(selectors.selectSession(store.snapshot(),'selector-session'),second);
  tx=stage(store,2,envelope(2,{next_seq:3,state:'running',active:true,events:[{seq:3,run_id:'run',type:'delta',message_id:'m1',delta:'始'}]}));assert.equal(store.commit(tx).ok,true);
  const third=selectors.selectSession(store.snapshot('selector-session'));assert.notEqual(third.messages[0],firstMessage);assert.equal(third.messages[0].text,'开始');assert.equal(third.messages[0].content_revision,3);
});

test('selectors reject mutable/raw input and expose a frozen, read-only namespace', () => {
  const world=load(),selectors=world.AgentWorkbenchSelectors;
  assert.equal(Object.isFrozen(selectors),true);assert.equal(selectors.selectSession({contract_version:'legacy_agent_poll_v1',snapshot_revision:1}),null);
  assert.equal(Object.isFrozen(selectors.selectTrajectory({},{})),true);
});

test('retired selectors and reducers are not loaded by the production DSH composition',()=>{
 const html=fs.readFileSync(path.join(__dirname,'../src/genshin_app/gui/web/index.html'),'utf8');
 const scripts=[...html.matchAll(/<script\b[^>]*src="([^"]+)"/g)].map(match=>match[1]);
 assert.deepEqual(scripts.filter(name=>name.startsWith('agent-')),['agent-document-adapter.js']);
 assert.equal(scripts.filter(name=>name==='dsh-production.js').length,1);
 assert.ok(scripts.indexOf('agent-document-adapter.js')<scripts.indexOf('candidate-application.js'));
 assert.ok(scripts.indexOf('candidate-application.js')<scripts.indexOf('dsh-production.js'));
});

test('legacy workbench name is one compatibility alias for the selectors namespace', () => {
  const world = load();
  assert.equal(world.AgentWorkbench, world.AgentWorkbenchSelectors);
});

test('seven-axis projection keeps cold runtime distinct until lazy start is explicit', () => {
  const selectors = load().AgentWorkbenchSelectors;
  const input = {
    client: {connection: 'ready', credential: {status: 'saved'}},
    host: {runtime_state: 'cold', runtime_incarnation: 'runtime-one', runtime_health: 'healthy'},
    session: {state: 'ready', active: false, live: true, messages: [], tools: []},
    facts: {documentDirty: false},
  };
  const projection = selectors.project(input);
  assert.deepEqual(plain(projection.axes), {
    credential_state: 'saved', provider_state: 'verified', runtime_state: 'cold',
    conversation_state: 'idle', turn_state: 'none', document_state: 'clean', inbox_state: null,
  });
  assert.equal(projection.summary, '服务已核验');
  assert.equal(projection.runtime_incarnation, 'runtime-one');
  assert.equal(selectors.axisRows(projection).length, 7);
  assert.equal(selectors.axisRows(projection).at(-1).label, '未知');
  assert.equal(projection.actions.send, false);
  const lazy = selectors.project({...input, client: {...input.client, runtime_lazy_start: true}});
  assert.equal(lazy.summary, '已配置，可发送');
  assert.equal(lazy.actions.send, true);
});

test('unknown Host facts are not presented as supported selector states', () => {
  const selectors = load().AgentWorkbenchSelectors;
  const projection = selectors.project({
    client: {connection: 'ready'},
    host: {runtime_state: 'magic_forever', inbox_state: 'already_sent'},
  });
  assert.equal(projection.axes.runtime_state, null);
  assert.equal(projection.axes.inbox_state, null);
  assert.equal(selectors.label('runtime_state', projection.axes.runtime_state), '未知');
});

test('provider mismatch cannot inherit an old runtime identity', () => {
  const selectors = load().AgentWorkbenchSelectors;
  const projection = selectors.project({
    client: {connection: 'disconnected', connection_matches: false, selection_ready: false},
    host: {provider_state: 'verified', runtime_state: 'ready', runtime_incarnation: 'old-runtime'},
    session: {ui_state: {provider_state: 'verified', runtime_state: 'ready', runtime_incarnation: 'old-session-runtime'}},
  });
  assert.equal(projection.axes.provider_state, 'unverified');
  assert.equal(projection.axes.credential_state, null);
  assert.equal(projection.axes.runtime_state, null);
  assert.equal(projection.runtime_incarnation, null);
  assert.notEqual(projection.summary, '已配置，可发送');
});

test('connection runtime facts override stale fields retained by an old conversation', () => {
  const selectors = load().AgentWorkbenchSelectors;
  const projection = selectors.project({
    client: {connection: 'ready', connection_matches: true, selection_ready: true},
    host: {provider_state: 'verified', runtime_state: 'failed', runtime_incarnation: 'runtime-new', failure_code: 'pipe_closed'},
    session: {ui_state: {provider_state: 'verified', runtime_state: 'ready', runtime_incarnation: 'runtime-old'}, live: true},
  });
  assert.equal(projection.axes.runtime_state, 'failed');
  assert.equal(projection.runtime_incarnation, 'runtime-new');
  assert.equal(projection.failure_code, 'pipe_closed');
  assert.equal(projection.actions.send, false);
});

test('turn outline and trajectory are derived from visible message and tool facts', () => {
  const selectors = load().AgentWorkbenchSelectors;
  const session = {
    run_id: 'run-two', active: true,
    messages: [
      {id:'m1', run_id:'run-one', role:'user', text:'建立队伍', seq:1},
      {id:'m2', run_id:'run-two', role:'assistant', text:'正在计算', seq:4},
    ],
    tools: [{id:'t1', run_id:'run-two', name:'读取角色资料', message:'完成', state:'completed', seq:3}],
  };
  const outline = selectors.turnOutline(session);
  assert.equal(outline.length, 2);
  assert.equal(outline[1].active, true);
  assert.deepEqual(plain(selectors.trajectory(session, {view:'trajectory', query:'', turn_id:'run-two'}).map(row => row.id)), ['tool:t1', 'message:m2']);
  assert.deepEqual(plain(selectors.trajectory(session, {view:'trajectory', query:'角色', turn_id:null}).map(row => row.id)), ['tool:t1']);
});
