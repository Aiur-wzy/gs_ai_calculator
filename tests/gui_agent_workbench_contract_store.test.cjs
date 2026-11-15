'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

function load() {
  const world = {}; world.window = world; vm.createContext(world);
  for (const name of ['agent-workbench-contract.js', 'agent-workbench-store.js']) {
    vm.runInContext(fs.readFileSync(path.join(__dirname, '../src/genshin_app/gui/web', name), 'utf8'), world);
  }
  return world;
}
const plain = value => JSON.parse(JSON.stringify(value));
const stage = (store, input) => store.stage({
  ...input,
  envelope:{requested_after_seq:input.requested_after_seq, ...input.envelope},
});
const base = (overrides = {}) => ({
  session_id:'session-one', next_seq:4, reset_required:false, events:[],
  state:'running', active:true, kind:'whiteboard', workflow:'staged', title:'测试会话',
  provider_id:'deepseek-api', connection_kind:'native_api',
  ...overrides,
});

test('LegacyAgentPollV1 produces a bounded partial projection without invented identities', () => {
  const world = load();
  const result = world.AgentWorkbenchContract.adaptLegacyAgentPollV1({
    expected_session_id:'session-one', requested_after_seq:0,
    envelope:base({next_seq:4, events:[
      {seq:1,run_id:'run-one',type:'message',message_id:'m1',role:'user',message:'开始'},
      {seq:2,run_id:'run-one',type:'tool',tool_id:'t1',name:'读取资料',state:'completed',message:'完成'},
      {seq:3,run_id:'run-one',type:'usage',usage:{input_tokens:10,output_tokens:5,total_tokens:15}},
      {seq:4,run_id:'run-one',type:'future.additive',opaque:{anything:true}},
    ]}),
  });
  assert.equal(result.ok, true); assert.equal(result.contract_version, 'legacy_agent_poll_v1'); assert.equal(result.partial, true);
  const events = plain(result.projection.events);
  assert.equal(events[0].source_identity.message_id, 'm1');
  assert.equal(events[2].semantic_payload.total_tokens, 15);
  assert.equal(events[3].semantic_payload, null); assert.equal(events[3].identity_status, 'missing');
  assert.equal(Object.hasOwn(events[0], 'event_id'), false); assert.equal(Object.hasOwn(events[0], 'turn_id'), false);
  assert.ok(result.diagnostics.includes('unknown_event_type:future.additive'));
});

test('adapter strips path, workspace and calculated payloads while keeping reviewed refs', () => {
  const world = load(), huge = 'x'.repeat(200000);
  const result = world.AgentWorkbenchContract.adaptLegacyAgentPollV1({
    expected_session_id:'session-one', requested_after_seq:0,
    envelope:base({candidate:{revision:7,ready:true,receipt_fingerprint:'receipt',issues:[1,2],calculated:{huge},diff:[{path:'C:/secret'}]},app_actions:[{
      action_id:'action-one',action:'save_as',status:'queued',execution_mode:'two_phase',candidate_revision:7,
      workspace:{path:'C:/private/project'},origin_workspace:{display_name:'private'},current_binding:{path:'C:/private/input'},
    }]}),
  });
  assert.equal(result.ok, true);
  assert.deepEqual(plain(result.projection.candidate_ref), {revision:7,ready:true,stale:false,receipt_fingerprint:'receipt',issues_count:2});
  assert.equal(result.projection.app_action_refs[0].action_id, 'action-one');
  const text = JSON.stringify(result);
  assert.doesNotMatch(text, /C:\/private|calculated|workspace|huge/); assert.ok(text.length < 10000);
});

test('invalid identity, cursor, sequence, shape and protected fields reject the whole batch', () => {
  const world = load(), adapt = envelope => world.AgentWorkbenchContract.adaptLegacyAgentPollV1({expected_session_id:'session-one',requested_after_seq:3,envelope});
  const cases = [
    [base({session_id:'other'}), 'session_id_mismatch'],
    [base({next_seq:2}), 'cursor_regression'],
    [base({events:{}}), 'events_invalid'],
    [base({events:[{seq:4,type:'status'},{seq:4,type:'status'}]}), 'event_sequence_not_strict'],
    [base({events:[{seq:5,type:'status'}]}), 'event_sequence_ahead'],
    [base({next_seq:3,reset_required:true,snapshot_seq:4}), 'snapshot_seq_ahead'],
    [base({events:[{seq:4,type:'tool',api_key:'forbidden'}]}), 'event_invalid_or_protected'],
    [base({raw_reasoning:'forbidden'}), 'envelope_protected_field'],
  ];
  for (const [envelope, code] of cases) {
    const result = adapt(envelope); assert.equal(result.ok, false); assert.equal(result.rejection_code, code);
  }
});

test('protected-field inspection fails closed when a domain object exceeds depth or node budgets', () => {
  const world = load(), adapt = envelope => world.AgentWorkbenchContract.adaptLegacyAgentPollV1({expected_session_id:'session-one',requested_after_seq:0,envelope});
  let deep = {api_key:'must-not-enter-sidecar'};
  for (let index = 0; index < 10; index++) deep = {child:deep};
  const deepResult = adapt(base({candidate:{revision:1,ready:true,deep}}));
  assert.equal(deepResult.ok, false); assert.equal(deepResult.rejection_code, 'candidate_protected_field');
  const many = {};
  for (let index = 0; index < 2050; index++) many[`node_${index}`] = {value:index};
  const manyResult = adapt(base({candidate:{revision:1,ready:true,many}}));
  assert.equal(manyResult.ok, false); assert.equal(manyResult.rejection_code, 'candidate_protected_field');
});

test('candidate sources and domain transaction shapes cannot diverge within an admitted batch', () => {
  const world = load(), adapt = envelope => world.AgentWorkbenchContract.adaptLegacyAgentPollV1({expected_session_id:'session-one',requested_after_seq:0,envelope});
  const dual = adapt(base({next_seq:1,candidate:{revision:1,receipt_fingerprint:'same',ready:true},events:[{seq:1,type:'candidate',candidate:{revision:1,receipt_fingerprint:'same',ready:false}}]}));
  assert.equal(dual.ok, false); assert.equal(dual.rejection_code, 'candidate_source_conflict');
  const repeated = adapt(base({next_seq:2,events:[{seq:1,type:'candidate',candidate:{revision:1,receipt_fingerprint:'same',ready:true}},{seq:2,type:'candidate',candidate:{revision:1,receipt_fingerprint:'same',ready:false}}]}));
  assert.equal(repeated.ok, false); assert.equal(repeated.rejection_code, 'candidate_source_conflict');
  for (const [key, value] of [['application_intent', []], ['application_intent', 'bad'], ['activation', []], ['recovery', 'bad']]) {
    const result = adapt(base({[key]:value})); assert.equal(result.ok, false); assert.equal(result.rejection_code, `${key}_invalid`);
  }
  for (const [value, code] of [[{},'application_intent_status_invalid'],[{status:'typo'},'application_intent_status_invalid'],[{status:'prepared',recovery_required:'true'},'application_intent_recovery_required_invalid'],[{status:'prepared',deferred_while_active:1},'application_intent_deferred_while_active_invalid']]) {
    const result=adapt(base({application_intent:value}));assert.equal(result.ok,false);assert.equal(result.rejection_code,code);
  }
  for (const [value, code] of [
    [{conversation_id:'session-one',status:'committed',intent:null},'application_intent_identity_missing'],
    [{conversation_id:'session-one',status:'aborted'},'application_intent_identity_missing'],
    [{conversation_id:'session-one',status:'committed',intent:{state:'prepared',kind:'whiteboard',revision:1,receipt_fingerprint:'receipt'}},'application_intent_state_mismatch'],
    [{conversation_id:'session-one',status:'committed',intent:{state:'committed',kind:'whiteboard',revision:1,receipt_fingerprint:'receipt'},recovery_required:true},'application_intent_recovery_state_invalid'],
    [{conversation_id:'other',status:'none',intent:null},'application_intent_conversation_invalid'],
    [{conversation_id:'session-one',status:'none',intent:{state:'none'}},'application_intent_none_invalid'],
  ]) { const result=adapt(base({application_intent:value}));assert.equal(result.ok,false);assert.equal(result.rejection_code,code); }
  assert.equal(adapt(base({application_intent:{conversation_id:'session-one',status:'prepared',intent:null,recovery_required:true}})).ok,true);
  assert.equal(adapt(base({application_intent:{conversation_id:'session-one',status:'committed',intent:{state:'committed',kind:'whiteboard',revision:1,receipt_fingerprint:'receipt'}}})).ok,true);
  assert.equal(adapt(base({application_intent:null})).rejection_code,'application_intent_invalid');
  assert.equal(adapt(base({activation:null,recovery:null})).ok, true);
});

test('namespaces and returned projections are frozen', () => {
  const world = load(); assert.equal(Object.isFrozen(world.AgentWorkbenchContract), true); assert.equal(Object.isFrozen(world.AgentWorkbenchStore), true);
  const result = world.AgentWorkbenchContract.adaptLegacyAgentPollV1({expected_session_id:'session-one',requested_after_seq:0,envelope:base()});
  assert.equal(Object.isFrozen(result), true); assert.equal(Object.isFrozen(result.projection), true);
  const store = world.AgentWorkbenchStore.createTransactionalLegacyStore(); assert.equal(Object.isFrozen(store), true);
});

test('transactional store stages without visible mutation and advances ACK only after commit', () => {
  const world=load(),store=world.AgentWorkbenchStore.createTransactionalLegacyStore();
  const envelope=base({next_seq:3,reset_required:true,snapshot_seq:3,messages:[{message_id:'m1',role:'user',message:'基线'}],events:[
    {seq:1,run_id:'run',type:'tool',tool_id:'tool-one',name:'读取',state:'running'},
    {seq:2,run_id:'run',type:'tool',tool_id:'tool-one',state:'completed',message:'完成'},
    {seq:3,run_id:'run',type:'usage',usage:{input_tokens:11,output_tokens:4,total_tokens:15}},
  ]});
  const tx=stage(store,{expected_session_id:'session-one',requested_after_seq:0,envelope});assert.equal(tx.ok,true);assert.equal(Object.isFrozen(tx),true);assert.equal(store.snapshot('session-one'),null);
  const committed=store.commit(tx);assert.equal(committed.ok,true);assert.equal(committed.ack_cursor,3);assert.equal(committed.store_revision,1);assert.equal(Object.isFrozen(committed.snapshot),true);
  const state=plain(store.snapshot('session-one'));assert.equal(state.messages[0].text,'基线');assert.equal(state.tools[0].state,'completed');assert.equal(state.usage.total_tokens,15);assert.equal(Object.hasOwn(state.events[0],'event_id'),false);assert.equal(Object.hasOwn(state.events[0],'turn_id'),false);
});

test('reset baseline never appends an already represented delta twice', () => {
  const world=load(),store=world.AgentWorkbenchStore.createTransactionalLegacyStore(),tx=stage(store,{expected_session_id:'session-one',requested_after_seq:0,envelope:base({next_seq:1,reset_required:true,snapshot_seq:1,messages:[{message_id:'answer',role:'assistant',message:'完成'}],events:[{seq:1,type:'delta',message_id:'answer',delta:'完成'}]})});
  assert.equal(store.commit(tx).ok,true);const state=plain(store.snapshot('session-one'));assert.equal(state.messages[0].text,'完成');assert.ok(state.diagnostics.includes('reset_delta_history_partial'));
});

test('transactional incremental reducer joins deltas and keeps bounded reviewed refs only', () => {
  const world=load(),store=world.AgentWorkbenchStore.createTransactionalLegacyStore();
  let tx=stage(store,{expected_session_id:'session-one',requested_after_seq:0,envelope:base({next_seq:1,reset_required:true,snapshot_seq:1,messages:[],events:[{seq:1,run_id:'run',type:'message',message_id:'answer',role:'assistant',message:'开'}]})});assert.equal(store.commit(tx).ok,true);
  tx=stage(store,{expected_session_id:'session-one',requested_after_seq:1,envelope:base({next_seq:4,candidate:{revision:2,ready:true,receipt_fingerprint:'receipt',calculated:{path:'C:/private'}},app_actions:[{action_id:'action',action:'save_as',status:'queued',workspace:{path:'C:/private'}}],events:[
    {seq:2,run_id:'run',type:'delta',message_id:'answer',delta:'始'},
    {seq:3,run_id:'run',type:'budget_status',status:'budget_progress_70',level:'notice',percent:70,message:'70%'},
    {seq:4,run_id:'run',type:'future.additive',opaque:{path:'C:/private'}},
  ]})});assert.equal(tx.ok,true);assert.equal(store.commit(tx).ok,true);
  const state=plain(store.snapshot('session-one')),encoded=JSON.stringify(state);assert.equal(state.messages[0].text,'开始');assert.equal(state.budget_statuses[0].status,'budget_progress_70');assert.equal(state.candidate_ref.revision,2);assert.equal(state.app_action_refs[0].action_id,'action');assert.ok(state.diagnostics.includes('unknown_event_type:future.additive'));assert.doesNotMatch(encoded,/C:\/private|calculated|workspace|opaque/);
});

test('transactional first legacy envelope imports its available baseline without requiring reset', () => {
  const world=load(),store=world.AgentWorkbenchStore.createTransactionalLegacyStore();
  const tx=stage(store,{expected_session_id:'session-one',requested_after_seq:0,envelope:base({next_seq:1,messages:[{message_id:'legacy-base',role:'user',message:'首次基线'}],events:[{seq:1,type:'status'}]})});
  assert.equal(store.commit(tx).ok,true);const state=plain(store.snapshot('session-one'));
  assert.equal(state.messages[0].message_id,'legacy-base');assert.ok(state.diagnostics.includes('initial_increment_partial'));
});

test('transactional budget statuses reset when the run changes at session or event level', () => {
  const world=load(),store=world.AgentWorkbenchStore.createTransactionalLegacyStore();
  let tx=stage(store,{expected_session_id:'session-one',requested_after_seq:0,envelope:base({run_id:'run-one',next_seq:1,events:[{seq:1,run_id:'run-one',type:'budget_status',status:'budget_closure_90',message:'旧轮'}]})});assert.equal(store.commit(tx).ok,true);
  tx=stage(store,{expected_session_id:'session-one',requested_after_seq:1,envelope:base({run_id:'run-two',next_seq:2,events:[{seq:2,type:'status'}]})});assert.equal(store.commit(tx).ok,true);
  assert.equal(store.snapshot('session-one').budget_statuses.length,0);
  tx=stage(store,{expected_session_id:'session-one',requested_after_seq:2,envelope:base({run_id:'run-two',next_seq:3,events:[{seq:3,run_id:'run-two',type:'budget_status',status:'budget_progress_70',message:'新轮'}]})});assert.equal(store.commit(tx).ok,true);
  tx=stage(store,{expected_session_id:'session-one',requested_after_seq:3,envelope:base({next_seq:4,events:[{seq:4,run_id:'run-three',type:'budget_status',status:'budget_progress_70',message:'事件切轮'}]})});assert.equal(store.commit(tx).ok,true);
  const rows=plain(store.snapshot('session-one').budget_statuses);assert.equal(rows.length,1);assert.equal(rows[0].run_id,'run-three');assert.equal(rows[0].message,'事件切轮');
});

test('top-level aggregate usage cannot be rolled back by per-turn usage events', () => {
  const world=load(),store=world.AgentWorkbenchStore.createTransactionalLegacyStore();
  let tx=stage(store,{expected_session_id:'session-one',requested_after_seq:0,envelope:base({next_seq:1,reset_required:true,snapshot_seq:1,usage:{input_tokens:80,output_tokens:20,total_tokens:100},events:[{seq:1,type:'usage',usage:{input_tokens:80,output_tokens:20,total_tokens:100}}]})});assert.equal(store.commit(tx).ok,true);assert.equal(store.snapshot('session-one').usage.total_tokens,100);
  tx=stage(store,{expected_session_id:'session-one',requested_after_seq:1,envelope:base({next_seq:3,usage:{input_tokens:120,output_tokens:30,total_tokens:150},events:[{seq:2,type:'usage',usage:{input_tokens:30,output_tokens:10,total_tokens:40}},{seq:3,type:'usage',usage:{input_tokens:10,output_tokens:0,total_tokens:10}}]})});assert.equal(store.commit(tx).ok,true);
  const state=plain(store.snapshot('session-one'));assert.equal(state.usage.total_tokens,150);assert.equal(state.events.filter(row=>row.source_type==='usage').length,3);
});

test('transactional diagnostics remain bounded with a stable truncation marker', () => {
  const world=load(),store=world.AgentWorkbenchStore.createTransactionalLegacyStore(),limit=world.AgentWorkbenchStore.transactionalLimits.diagnostics;
  let cursor=0;
  for(let batch=0;batch<3;batch++){
    const events=Array.from({length:120},(_,index)=>({seq:cursor+index+1,type:`future.${batch}.${index}`}));
    const tx=stage(store,{expected_session_id:'session-one',requested_after_seq:cursor,envelope:base({next_seq:cursor+events.length,events})});assert.equal(store.commit(tx).ok,true);cursor+=events.length;
  }
  const diagnostics=plain(store.snapshot('session-one').diagnostics);assert.equal(diagnostics.length,limit);assert.equal(diagnostics.at(-1),'diagnostics_trimmed');
});

test('incremental omission preserves prior top-level facts while explicit empty collections clear them', () => {
  const world=load(),store=world.AgentWorkbenchStore.createTransactionalLegacyStore();
  let tx=stage(store,{expected_session_id:'session-one',requested_after_seq:0,envelope:base({next_seq:1,reset_required:true,snapshot_seq:1,runtime_state:'ready',runtime_health:'healthy',candidate:{revision:3,ready:true},application_intent:{conversation_id:'session-one',status:'prepared',intent:{state:'prepared',kind:'whiteboard',revision:3,receipt_fingerprint:'receipt-three'}},app_actions:[{action_id:'a1',action:'save_as',status:'queued'}],usage:{total_tokens:20},events:[{seq:1,type:'status'}]})});assert.equal(store.commit(tx).ok,true);
  tx=stage(store,{expected_session_id:'session-one',requested_after_seq:1,envelope:{session_id:'session-one',next_seq:2,reset_required:false,events:[{seq:2,type:'status'}]}});assert.equal(store.commit(tx).ok,true);
  let state=plain(store.snapshot('session-one'));assert.equal(state.connection.runtime_state,'ready');assert.equal(state.candidate_ref.revision,3);assert.equal(state.application_ref.status,'prepared');assert.equal(state.app_action_refs.length,1);assert.equal(state.usage.total_tokens,20);assert.equal(state.session.title,'测试会话');
  tx=stage(store,{expected_session_id:'session-one',requested_after_seq:2,envelope:{session_id:'session-one',next_seq:3,reset_required:false,events:[{seq:3,type:'status'}],app_actions:[],candidate:null,application_intent:{conversation_id:'session-one',status:'none',intent:null},usage:null}});assert.equal(store.commit(tx).ok,true);
  state=plain(store.snapshot('session-one'));assert.equal(state.app_action_refs.length,0);assert.equal(state.candidate_ref,null);assert.equal(state.application_ref.status,'none');assert.equal(state.usage,null);assert.equal(state.connection.runtime_state,'ready');
});

test('durable terminal state replaces a stale turn axis even when terminal envelope omits turn_state', () => {
  const world=load(),store=world.AgentWorkbenchStore.createTransactionalLegacyStore();
  let tx=stage(store,{expected_session_id:'session-one',requested_after_seq:0,envelope:base({next_seq:1,turn_state:'using_tool',events:[{seq:1,type:'status'}]})});
  assert.equal(store.commit(tx).ok,true);assert.equal(store.snapshot('session-one').session.turn_state,'using_tool');
  tx=stage(store,{expected_session_id:'session-one',requested_after_seq:1,envelope:base({next_seq:2,state:'running',active:true,events:[{seq:2,type:'status'}]})});
  assert.equal(store.commit(tx).ok,true);assert.equal(store.snapshot('session-one').session.turn_state,'using_tool');
  tx=stage(store,{expected_session_id:'session-one',requested_after_seq:2,envelope:base({next_seq:3,state:'completed',terminal_status:'completed',active:false,events:[{seq:3,type:'status'}]})});
  assert.equal(store.commit(tx).ok,true);
  const session=plain(store.snapshot('session-one').session);
  assert.equal(session.state,'completed');assert.equal(session.terminal_status,'completed');assert.equal(session.turn_state,'completed');
});

test('deferred App action projection preserves reviewed refs until an authoritative list arrives', () => {
  const world=load(),store=world.AgentWorkbenchStore.createTransactionalLegacyStore();
  let tx=stage(store,{expected_session_id:'session-one',requested_after_seq:0,envelope:base({next_seq:1,app_actions:[{action_id:'a1',action:'save_as',status:'queued'}],events:[{seq:1,type:'status'}]})});assert.equal(store.commit(tx).ok,true);
  tx=stage(store,{expected_session_id:'session-one',requested_after_seq:1,envelope:base({next_seq:2,reset_required:true,snapshot_seq:2,app_actions:[],app_actions_deferred:true,events:[{seq:2,type:'status'}]})});assert.equal(store.commit(tx).ok,true);
  let state=plain(store.snapshot('session-one'));assert.deepEqual(state.app_action_refs.map(row=>row.action_id),['a1']);assert.ok(state.diagnostics.includes('app_actions_deferred'));
  tx=stage(store,{expected_session_id:'session-one',requested_after_seq:2,envelope:base({next_seq:3,app_actions:[],app_actions_deferred:false,events:[{seq:3,type:'status'}]})});assert.equal(store.commit(tx).ok,true);
  state=plain(store.snapshot('session-one'));assert.equal(state.app_action_refs.length,0);
});

test('malformed deferred App action flag is rejected without clearing accepted refs or ACK', () => {
  const world=load(),store=world.AgentWorkbenchStore.createTransactionalLegacyStore();
  let tx=stage(store,{expected_session_id:'session-one',requested_after_seq:0,envelope:base({next_seq:1,app_actions:[{action_id:'a1',action:'save_as',status:'queued'}],events:[{seq:1,type:'status'}]})});assert.equal(store.commit(tx).ok,true);
  const accepted=store.snapshot('session-one');
  const bad=stage(store,{expected_session_id:'session-one',requested_after_seq:1,envelope:base({next_seq:2,app_actions:[],app_actions_deferred:'true',events:[{seq:2,type:'status'}]})});
  assert.equal(bad.ok,false);assert.equal(bad.rejection_code,'app_actions_deferred_invalid');assert.equal(store.snapshot('session-one'),accepted);assert.equal(store.snapshot('session-one').source.next_seq,1);assert.deepEqual(plain(store.snapshot('session-one').app_action_refs).map(row=>row.action_id),['a1']);
});

test('rejected, gap and conflicting transactional stages leave state and ACK unchanged', () => {
  const world=load(),store=world.AgentWorkbenchStore.createTransactionalLegacyStore();
  const first=stage(store,{expected_session_id:'session-one',requested_after_seq:0,envelope:base({next_seq:2,reset_required:true,snapshot_seq:2,messages:[],events:[{seq:2,type:'status',state:'running'}]})});assert.equal(store.commit(first).ok,true);const accepted=store.snapshot('session-one');
  const bad=stage(store,{expected_session_id:'session-one',requested_after_seq:2,envelope:base({next_seq:3,events:[{seq:4,type:'status'}]})});assert.equal(bad.ok,false);
  const gap=stage(store,{expected_session_id:'session-one',requested_after_seq:4,envelope:base({next_seq:4,events:[]})});assert.equal(gap.ok,false);assert.equal(gap.rejection_code,'source_cursor_gap');
  const duplicate=stage(store,{expected_session_id:'session-one',requested_after_seq:2,envelope:base({next_seq:3,events:[{seq:2,type:'status'}]})});assert.equal(duplicate.ok,false);assert.equal(duplicate.rejection_code,'event_coverage_gap');
  assert.equal(store.snapshot('session-one'),accepted);assert.equal(store.snapshot('session-one').source.next_seq,2);
});

test('transaction commits are one-shot CAS and stale or forged commits never replace accepted state', () => {
  const world=load(),store=world.AgentWorkbenchStore.createTransactionalLegacyStore();
  const initial=stage(store,{expected_session_id:'session-one',requested_after_seq:0,envelope:base({next_seq:1,reset_required:true,snapshot_seq:1,messages:[],events:[{seq:1,type:'status'}]})});assert.equal(store.commit(initial).ok,true);
  const one=stage(store,{expected_session_id:'session-one',requested_after_seq:1,envelope:base({next_seq:2,events:[{seq:2,type:'status',state:'running'}]})});
  const two=stage(store,{expected_session_id:'session-one',requested_after_seq:1,envelope:base({next_seq:2,events:[{seq:2,type:'status',state:'completed'}]})});
  assert.equal(store.commit(one).ok,true);const accepted=store.snapshot('session-one');const stale=store.commit(two);assert.equal(stale.ok,false);assert.equal(stale.rejection_code,'transaction_stale');assert.equal(store.snapshot('session-one'),accepted);
  assert.equal(store.commit(one).rejection_code,'transaction_invalid');assert.equal(store.commit(Object.freeze({...one})).rejection_code,'transaction_invalid');assert.equal(store.snapshot('session-one'),accepted);
});

test('transactional reset replaces prior entities and establishes a new revision atomically', () => {
  const world=load(),store=world.AgentWorkbenchStore.createTransactionalLegacyStore();
  let tx=stage(store,{expected_session_id:'session-one',requested_after_seq:0,envelope:base({next_seq:2,events:[{seq:1,type:'message',message_id:'old',role:'assistant',message:'旧'},{seq:2,type:'tool',tool_id:'old-tool',name:'旧工具',state:'completed'}]})});assert.equal(store.commit(tx).ok,true);
  tx=stage(store,{expected_session_id:'session-one',requested_after_seq:2,envelope:base({next_seq:5,reset_required:true,snapshot_seq:5,messages:[{message_id:'new',role:'user',message:'新'}],events:[{seq:5,type:'status',state:'running'}]})});assert.equal(tx.ok,true);assert.equal(store.snapshot('session-one').messages[0].message_id,'old');assert.equal(store.commit(tx).ok,true);
  const state=plain(store.snapshot('session-one'));assert.deepEqual(state.messages.map(row=>row.message_id),['new']);assert.equal(state.tools.length,0);assert.deepEqual(state.events.map(row=>row.source_seq),[5]);assert.equal(state.store_revision,2);assert.ok(state.diagnostics.includes('reset_tool_history_partial'));
});

test('transactional entities and audit events stay within maintained memory limits', () => {
  const world=load(),store=world.AgentWorkbenchStore.createTransactionalLegacyStore(),batch=(start,count)=>Array.from({length:count},(_,index)=>{const seq=start+index;return {seq,type:'message',message_id:`m${seq}`,role:'assistant',message:'x'};});
  let tx=stage(store,{expected_session_id:'session-one',requested_after_seq:0,envelope:base({next_seq:600,events:batch(1,600)})});assert.equal(store.commit(tx).ok,true);
  tx=stage(store,{expected_session_id:'session-one',requested_after_seq:600,envelope:base({next_seq:1200,events:batch(601,600)})});assert.equal(store.commit(tx).ok,true);
  const state=store.snapshot('session-one'),limits=world.AgentWorkbenchStore.transactionalLimits;assert.equal(state.events.length,limits.events);assert.equal(state.events[0].source_seq,201);assert.equal(state.messages.length,limits.messages);assert.equal(state.messages[0].message_id,'m701');assert.ok(state.diagnostics.includes('event_history_trimmed'));assert.ok(state.diagnostics.includes('message_history_trimmed'));
});

test('transactional prepare validates the Host cursor echo and only commit returns a trusted ACK', () => {
  const world=load(),store=world.AgentWorkbenchStore.createTransactionalLegacyStore();
  const missing=store.stage({expected_session_id:'session-one',requested_after_seq:0,envelope:base({next_seq:0})});
  assert.equal(missing.ok,false);assert.equal(missing.rejection_code,'requested_cursor_missing');
  const mismatch=store.stage({expected_session_id:'session-one',requested_after_seq:3,envelope:base({requested_after_seq:2,next_seq:3})});
  assert.equal(mismatch.ok,false);assert.equal(mismatch.rejection_code,'requested_cursor_mismatch');
  const tx=stage(store,{expected_session_id:'session-one',requested_after_seq:0,envelope:base({next_seq:1,events:[{seq:1,type:'status'}]})});
  assert.equal(tx.ok,true);assert.equal(tx.snapshot_revision,1);assert.equal(Object.hasOwn(tx,'ack_cursor'),false);
  const receipt=store.commit(tx);assert.equal(receipt.ok,true);assert.equal(receipt.ack_cursor,1);assert.equal(receipt.snapshot_revision,1);assert.equal(receipt.snapshot.snapshot_revision,1);
});

test('reviewed reset baseline restores every supported read family without carrying large domain bodies', () => {
  const world=load(),store=world.AgentWorkbenchStore.createTransactionalLegacyStore();
  const tx=stage(store,{expected_session_id:'session-one',requested_after_seq:4,envelope:base({
    next_seq:9,reset_required:true,snapshot_seq:9,events:[],messages:[],app_actions:[],
    baseline:{baseline_seq:9,completeness:{messages:'complete',tools:'complete',usage:'complete',budget_statuses:'complete',app_action_refs:'complete'},partial_reasons:[],
      messages:[{message_id:'m9',role:'assistant',message:'已恢复',run_id:'run-nine'}],
      tools:[{tool_id:'t9',name:'恢复工具',state:'completed',message:'完成',run_id:'run-nine',last_source_seq:8}],
      usage:{input_tokens:20,output_tokens:5,total_tokens:25},
      budget_statuses:[{status:'budget_progress_70',level:'notice',percent:70,message:'预算',run_id:'run-nine',last_source_seq:7}],
      app_action_refs:[{action_id:'a9',action:'save_as',status:'queued',workspace:{path:'C:/not-projected'}}]},
  })});
  assert.equal(tx.ok,true);assert.equal(store.commit(tx).ok,true);
  const state=plain(store.snapshot('session-one'));assert.equal(state.messages[0].message_id,'m9');assert.equal(state.tools[0].tool_id,'t9');assert.equal(state.usage.total_tokens,25);assert.equal(state.budget_statuses[0].status,'budget_progress_70');assert.equal(state.app_action_refs[0].action_id,'a9');assert.equal(state.baseline_ref.baseline_seq,9);assert.doesNotMatch(JSON.stringify(state),/C:\/not-projected|workspace/);
});

test('production ACK requires complete event coverage after the request cursor or explicit reset baseline', () => {
  const world=load(),store=world.AgentWorkbenchStore.createTransactionalLegacyStore();
  let tx=stage(store,{expected_session_id:'session-one',requested_after_seq:0,envelope:base({next_seq:5,reset_required:true,snapshot_seq:5,events:[],baseline:{baseline_seq:5,completeness:'complete',partial_reasons:[],messages:[],tools:[{tool_id:'kept',name:'工具',state:'completed',message:'完成'}],usage:{total_tokens:100},budget_statuses:[],app_action_refs:[]}})});assert.equal(store.commit(tx).ok,true);
  const accepted=store.snapshot('session-one');
  for(const envelope of [
    base({next_seq:10,events:[]}),
    base({next_seq:10,events:[{seq:10,type:'status'}]}),
    base({next_seq:10,reset_required:true,snapshot_seq:5,events:[],baseline:{baseline_seq:5,completeness:'complete',partial_reasons:[],messages:[],tools:[],usage:null,budget_statuses:[],app_action_refs:[]}}),
    base({next_seq:10,reset_required:true,snapshot_seq:5,events:[{seq:4,type:'tool',tool_id:'kept',state:'running'},{seq:5,type:'usage',usage:{total_tokens:20}},{seq:10,type:'status'}],baseline:{baseline_seq:5,completeness:'complete',partial_reasons:[],messages:[],tools:[{tool_id:'kept',name:'工具',state:'completed',message:'完成'}],usage:{total_tokens:100},budget_statuses:[],app_action_refs:[]}}),
  ]){
    const rejected=stage(store,{expected_session_id:'session-one',requested_after_seq:5,envelope});assert.equal(rejected.ok,false);assert.equal(rejected.rejection_code,'event_coverage_gap');assert.equal(store.snapshot('session-one'),accepted);assert.equal(store.snapshot('session-one').source.next_seq,5);
  }
  tx=stage(store,{expected_session_id:'session-one',requested_after_seq:5,envelope:base({next_seq:7,events:[{seq:6,type:'status'},{seq:7,type:'status'}]})});assert.equal(tx.ok,true);assert.equal(store.commit(tx).ack_cursor,7);
});

test('Python Host R2B reset JSON shape is accepted without usage shape conversion', () => {
  const world=load(),store=world.AgentWorkbenchStore.createTransactionalLegacyStore();
  const envelope=JSON.parse(JSON.stringify(base({
    requested_after_seq:0,next_seq:23,snapshot_seq:23,reset_required:true,events:[],messages:[],app_actions:[],
    baseline:{baseline_seq:23,completeness:'complete',partial_reasons:[],
      messages:[{message_id:'m-host',role:'assistant',text:'Host基线',run_id:'run-host',streaming:true,truncated:false}],
      tools:[{tool_id:'t-host',name:'读取',state:'completed',message:'',run_id:'run-host'}],
      usage:{input_tokens:20,output_tokens:5,total_tokens:25},
      budget_statuses:[{status:'budget_progress_70',level:'notice',percent:70,message:'预算',run_id:'run-host'}],
      app_action_refs:[{action_id:'a-host',action:'save_as',status:'queued',execution_mode:null,candidate_revision:1,stage_revision:2,execution_outcome_unknown:false}]},
  })));
  const tx=store.stage({expected_session_id:'session-one',requested_after_seq:0,envelope});
  assert.equal(tx.ok,true);assert.equal(store.commit(tx).ok,true);
  const state=plain(store.snapshot('session-one'));assert.equal(state.usage.total_tokens,25);assert.equal(state.messages[0].text,'Host基线');assert.equal(state.tools[0].tool_id,'t-host');assert.equal(state.baseline_ref.baseline_seq,23);
});

test('drop and clear bound session lifecycle and invalidate already prepared transactions', () => {
  const world=load(),store=world.AgentWorkbenchStore.createTransactionalLegacyStore(),limit=world.AgentWorkbenchStore.transactionalLimits.sessions;
  const stale=stage(store,{expected_session_id:'stale-session',requested_after_seq:0,envelope:base({session_id:'stale-session',next_seq:0})});assert.equal(stale.ok,true);
  const dropped=store.drop('stale-session');assert.equal(dropped.ok,true);assert.equal(dropped.dropped,false);assert.equal(store.commit(stale).rejection_code,'transaction_lifecycle_stale');
  for(let index=0;index<limit;index++){
    const id=`bounded-${index}`,tx=stage(store,{expected_session_id:id,requested_after_seq:0,envelope:base({session_id:id,next_seq:0})});assert.equal(tx.ok,true);assert.equal(store.commit(tx).ok,true);
  }
  assert.equal(store.snapshot().session_count,limit);assert.equal(store.snapshot().session_limit,limit);
  const overflow=stage(store,{expected_session_id:'bounded-overflow',requested_after_seq:0,envelope:base({session_id:'bounded-overflow',next_seq:0})});assert.equal(overflow.ok,false);assert.equal(overflow.rejection_code,'session_limit');
  assert.equal(store.drop('bounded-0').dropped,true);assert.equal(store.snapshot().session_count,limit-1);
  const replacement=stage(store,{expected_session_id:'bounded-replacement',requested_after_seq:0,envelope:base({session_id:'bounded-replacement',next_seq:0})});assert.equal(store.commit(replacement).ok,true);
  const prepared=stage(store,{expected_session_id:'bounded-1',requested_after_seq:0,envelope:base({session_id:'bounded-1',next_seq:0})});assert.equal(prepared.ok,true);
  const cleared=store.clear();assert.equal(cleared.ok,true);assert.equal(cleared.dropped,limit);assert.equal(store.commit(prepared).rejection_code,'transaction_lifecycle_stale');assert.equal(store.snapshot().session_count,0);
  assert.equal(store.drop('../invalid').rejection_code,'session_id_invalid');
});
