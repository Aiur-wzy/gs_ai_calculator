const test = require('node:test');
const assert = require('node:assert/strict');
const path = require('node:path');
const vm = require('node:vm');
const R = require('./helpers/gui_runtime.cjs');

function load() {
  const world = {console, Date, Math, setTimeout, clearTimeout};
  world.window = world;
  vm.createContext(world);
  for (const name of ['agent-workbench-client.js', 'agent-workbench-state-adapter.js', 'agent-workbench-transport-controller.js']) {
    R.loadWebFile(world, path.join(__dirname, '../src/genshin_app/gui/web', name), {filename:name});
  }
  return world;
}

function harness(overrides = {}) {
  const world = load(), calls = [], localState = {
    open:true, connectionGeneration:1, connection:'ready', hostUi:{}, active:null,
  };
  const store = overrides.eventStore || {
    stage(request) {
      calls.push(['stage', request]);
      return {ok:true, token:'transaction'};
    },
    commit(transaction) {
      calls.push(['commit', transaction]);
      return {ok:true, ack_cursor:4, store_revision:2, snapshot_revision:2, snapshot:{cursor:4}};
    },
  };
  const api = overrides.api || (async (...args) => {
    calls.push(['api', ...args]);
    return {data:{session_id:'session-a', requested_after_seq:0, next_seq:0, events:[]}};
  });
  const sidecars=world.AgentWorkbenchStateAdapter.create({state:localState,eventStore:{snapshot:()=>null},selectors:{selectSession:value=>value},clone:value=>structuredClone(value)});
  const controller = world.AgentWorkbenchTransportController.create({
    client:world.AgentWorkbenchClient,
    eventStore:store,
    sidecars,
    api,
    state:() => localState,
    clone:value => structuredClone(value),
    ackCursor:() => 0,
    readModel:() => ({active:false, connectionKind:'native_api'}),
    isActive:() => true,
    refresh:() => calls.push(['refresh']),
    currentMatches:() => true,
    selected:() => null,
    errorText:() => 'safe error',
    connectionPayloadMatches:() => false,
    mergeHostUi:value => value,
    connectionHostUiFields:[],
    testMode:() => true,
    setTimer:(callback, delay) => ({callback, delay}),
    clearTimer:() => {},
    ...overrides.options,
  });
  const session = {
    session_id:'session-a', epoch:1, live:true, candidate:{revision:1, receipt_fingerprint:'old'},
    appActions:[], applicationIntent:null, activation:null, recovery:null, canResume:false,
    pendingApplicationAck:null, applicationRecoveryPending:false, coldPendingApplication:false,
    applied:false, appliedReceipt:null, error:null, pollFailureActive:false,
  };
  return {world, controller, calls, localState, session};
}

test('requires an explicit client, Store and every authority callback', () => {
  const world = load();
  assert.throws(() => world.AgentWorkbenchTransportController.create(), /dependencies_missing/);
  assert.throws(() => world.AgentWorkbenchTransportController.create({
    client:world.AgentWorkbenchClient,
    eventStore:{stage() {}, commit() {}},
  }), /_api_missing/);
});

test('publishes the domain sidecar only after one successful Store CAS', () => {
  let observedCandidate = null;
  const h = harness({eventStore:{
    stage(request) { h.calls.push(['stage', request]); return {ok:true, token:'tx'}; },
    commit(transaction) {
      observedCandidate = h.session.candidate.receipt_fingerprint;
      h.calls.push(['commit', transaction]);
      return {ok:true, ack_cursor:7, store_revision:3, snapshot_revision:3, snapshot:{cursor:7}};
    },
  }});
  const receipt = h.controller.receive(h.session, {
    session_id:'session-a', requested_after_seq:0, next_seq:7, events:[],
    candidate:{revision:2, receipt_fingerprint:'new'},
  }, {production:true, requestedAfterSeq:0});
  assert.equal(receipt.accepted, true);
  assert.equal(observedCandidate, 'old');
  assert.equal(h.session.candidate.receipt_fingerprint, 'new');
  assert.deepEqual(h.calls.filter(row => ['stage', 'commit'].includes(row[0])).map(row => row[0]), ['stage', 'commit']);
});

test('provider-ready gap cannot stop polling before an explicit Host settlement', () => {
  const h = harness();
  h.session.awaitingTurnSettlement = true;
  const idle = h.controller.receive(h.session, {
    session_id:'session-a', requested_after_seq:0, next_seq:4,
    // Router deliberately projects this coordinator gap as `ready`: the
    // provider is idle, but the Host has not yet sealed the candidate.
    state:'ready', active:false, events:[],
  }, {production:true, requestedAfterSeq:0});
  assert.equal(idle.accepted, true);
  assert.equal(h.session.awaitingTurnSettlement, true);

  const completed = h.controller.receive(h.session, {
    session_id:'session-a', requested_after_seq:4, next_seq:4,
    state:'completed', terminal_status:'completed', active:false, events:[],
  }, {production:true, requestedAfterSeq:4});
  assert.equal(completed.accepted, true);
  assert.equal(h.session.awaitingTurnSettlement, false);
});

test('settlement fence participates in the production active-session selector', () => {
  const world = load(), state = {sessions:new Map()};
  const adapter = world.AgentWorkbenchStateAdapter.create({
    state,
    eventStore:{snapshot:() => null},
    selectors:{selectSession:value => value},
    clone:value => structuredClone(value),
  });
  const session = {session_id:'session-a', state:'idle', active:false, awaitingTurnSettlement:true};
  assert.equal(adapter.isActive(session), true);
  session.awaitingTurnSettlement = false;
  assert.equal(adapter.isActive(session), false);
});

test('Store stage or commit rejection and exceptions cannot leak domain drafts', () => {
  for (const failure of ['stage', 'stage_throw', 'commit', 'commit_throw']) {
    const h = harness({eventStore:{
      stage() {
        if (failure === 'stage_throw') throw new Error('stage exploded');
        return failure === 'stage' ? {ok:false, rejection_code:'stage_denied'} : {ok:true, token:'tx'};
      },
      commit() {
        if (failure === 'commit_throw') throw new Error('commit exploded');
        return {ok:false, rejection_code:'commit_denied'};
      },
    }});
    const receipt = h.controller.receive(h.session, {
      session_id:'session-a', requested_after_seq:0, next_seq:1, events:[],
      candidate:{revision:9, receipt_fingerprint:'forged'},
      app_actions:[{action_id:'action-a', action:'save_as', status:'queued'}],
    }, {production:true, requestedAfterSeq:0});
    assert.equal(receipt.accepted, false);
    assert.equal(h.session.candidate.receipt_fingerprint, 'old');
    assert.deepEqual(h.session.appActions, []);
  }
});

test('reset is admitted as a complete batch while a gap remains fail-closed', () => {
  let expected = 4;
  const h = harness({eventStore:{
    stage(request) {
      h.calls.push(['stage', request]);
      if (request.envelope.reset_required === true) return {ok:true, token:'reset-tx'};
      if (request.requested_after_seq !== expected) return {ok:false, rejection_code:'source_cursor_gap'};
      return {ok:true, token:'delta-tx'};
    },
    commit(transaction) {
      h.calls.push(['commit', transaction]);
      expected = 9;
      return {ok:true, ack_cursor:9, store_revision:2, snapshot_revision:2, snapshot:{cursor:9}};
    },
  }});
  const reset = h.controller.receive(h.session, {
    session_id:'session-a', requested_after_seq:0, reset_required:true, snapshot_seq:9,
    next_seq:9, events:[], candidate:{revision:2, receipt_fingerprint:'reset'},
  }, {production:true, requestedAfterSeq:0});
  assert.equal(reset.accepted, true);
  assert.equal(h.session.candidate.receipt_fingerprint, 'reset');
  assert.equal(h.calls.filter(row => row[0] === 'commit').length, 1);

  const gap = h.controller.receive(h.session, {
    session_id:'session-a', requested_after_seq:12, reset_required:false,
    next_seq:13, events:[], candidate:{revision:3, receipt_fingerprint:'gap'},
  }, {production:true, requestedAfterSeq:12});
  assert.equal(gap.accepted, false);
  assert.equal(gap.reason, 'source_cursor_gap');
  assert.equal(h.session.candidate.receipt_fingerprint, 'reset');
  assert.equal(h.calls.filter(row => row[0] === 'commit').length, 1);
});

test('an accepted external command advances the client exactly once after admission', () => {
  let commits = 0;
  const fakeClient = {create(options) {
    return {
      commitExternal(session, receipt) { commits++; assert.equal(receipt.ack_cursor, 4); return true; },
      schedule() {}, poll() {},
    };
  }};
  const h = harness({options:{client:fakeClient}});
  // The explicit top-level client option is authoritative.
  const controller = h.world.AgentWorkbenchTransportController.create({
    client:fakeClient, eventStore:{stage:() => ({ok:true}), commit:() => ({ok:true, ack_cursor:4, store_revision:1, snapshot_revision:1, snapshot:{}})},
    sidecars:h.world.AgentWorkbenchStateAdapter.create({state:h.localState,eventStore:{snapshot:()=>null},selectors:{selectSession:value=>value},clone:value=>structuredClone(value)}),
    api:async () => ({}), state:() => h.localState, clone:value => structuredClone(value), ackCursor:() => 0,
    readModel:() => ({active:false}), isActive:() => true, refresh:() => {}, currentMatches:() => true,
    selected:() => null, errorText:() => 'error', connectionPayloadMatches:() => false, mergeHostUi:value => value,
    testMode:() => true,
  });
  assert.equal(controller.receiveCommand(h.session, {session_id:'session-a', events:[]}, 0).accepted, true);
  assert.equal(commits, 1);
});

test('provider-generation guard drops a late wait before Store admission and never duplicates the request', async () => {
  let resolve, requests = 0, stages = 0;
  const pending = new Promise(done => { resolve = done; });
  const h = harness({
    api:async () => { requests++; return pending; },
    eventStore:{stage() { stages++; return {ok:true}; }, commit() { return {ok:true, ack_cursor:1, store_revision:1, snapshot_revision:1, snapshot:{}}; }},
  });
  const running = h.controller.poll(h.session, true);
  h.localState.connectionGeneration++;
  resolve({data:{session_id:'session-a', requested_after_seq:0, next_seq:1, subscription_generation:1, events:[]}});
  const result = await running;
  assert.equal(result.status, 'stale');
  assert.equal(requests, 1);
  assert.equal(stages, 0);
});

test('in-flight forced polls coalesce instead of issuing a second Host request', async () => {
  let resolve, requests = 0;
  const pending = new Promise(done => { resolve = done; });
  const h = harness({api:async () => { requests++; return pending; }});
  const first = h.controller.poll(h.session, true);
  const second = await h.controller.poll(h.session, true);
  assert.equal(second.status, 'in_flight');
  assert.equal(requests, 1);
  h.localState.connectionGeneration++;
  resolve({data:{session_id:'session-a', requested_after_seq:0, next_seq:0, subscription_generation:1, events:[]}});
  await first;
  assert.equal(requests, 1);
});

test('reconcile supersedes a concurrent long wait and admits only the forced refresh', async () => {
  let resolveWait;
  const pendingWait = new Promise(done => { resolveWait = done; });
  const h = harness({api:async (method, ...args) => {
    h.calls.push(['api', method, ...args]);
    if (method === 'agent_wait_events') return pendingWait;
    assert.equal(method, 'agent_poll');
    return {data:{session_id:'session-a', requested_after_seq:0, next_seq:4, events:[], candidate:{revision:2,receipt_fingerprint:'reconciled'}}};
  }});
  const waiting = h.controller.poll(h.session, true);
  await Promise.resolve();
  const refreshed = await h.controller.reconcile(h.session);
  assert.equal(refreshed.status, 'accepted');
  assert.equal(h.session.candidate.receipt_fingerprint, 'reconciled');
  resolveWait({data:{session_id:'session-a',requested_after_seq:0,next_seq:3,subscription_generation:1,events:[],candidate:{revision:9,receipt_fingerprint:'late'}}});
  assert.equal((await waiting).status, 'stale');
  assert.equal(h.session.candidate.receipt_fingerprint, 'reconciled');
  assert.deepEqual(h.calls.filter(row => row[0] === 'api').map(row => row[1]), ['agent_wait_events','agent_poll']);
  assert.equal(h.calls.filter(row => row[0] === 'stage').length, 1);
});

test('reconcile drops a late provider generation before sending the Host refresh', async () => {
  let generation = 1, polls = 0;
  const fakeClient = {create() { return {
    invalidate() { generation++; return true; },
    poll() { polls++; return Promise.resolve({status:'accepted'}); },
    schedule() {}, commitExternal() {},
  }; }};
  const h = harness({options:{client:fakeClient,state:()=>({open:true,connectionGeneration:generation})}});
  const result = await h.controller.reconcile(h.session);
  assert.equal(result.status, 'stale');
  assert.equal(polls, 0);
});

test('duplicate reconciliations share one forced Host refresh', async () => {
  let resolve;
  const pending = new Promise(done => { resolve = done; });
  const h = harness({api:async (method, ...args) => { h.calls.push(['api', method, ...args]); return pending; }});
  const first = h.controller.reconcile(h.session), second = h.controller.reconcile(h.session);
  assert.equal(first, second);
  assert.equal(h.calls.filter(row => row[0] === 'api').length, 1);
  resolve({data:{session_id:'session-a',requested_after_seq:0,next_seq:4,events:[]}});
  assert.equal((await first).status, 'accepted');
  assert.equal((await second).status, 'accepted');
});

test('transport controller exposes only reviewed orchestration entry points', () => {
  const h = harness(), keys = Object.keys(h.controller);
  for (const internal of ['prepareEnvelope','actionPatch','patchActionList','prepareDomainSidecar','commitDomainSidecar','transportContext']) {
    assert.equal(keys.includes(internal), false);
  }
  for (const entry of ['receive','receiveCommand','schedule','poll','reconcile']) assert.equal(keys.includes(entry), true);
});
