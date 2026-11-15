'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

function deferred() {
  let resolve;
  const promise = new Promise(done => { resolve = done; });
  return {promise, resolve};
}

function setup(overrides = {}) {
  const calls = [], received = [], failures = [], states = [], timers = new Map();
  let nextTimer = 0;
  const world = {
    setTimeout(callback, delay) { const id = ++nextTimer; timers.set(id, {callback, delay}); return id; },
    clearTimeout(id) { timers.delete(id); },
  };
  world.window = world;
  vm.createContext(world);
  vm.runInContext(
    fs.readFileSync(path.join(__dirname, '../src/genshin_app/gui/web/agent-workbench-client.js'), 'utf8'),
    world,
    {filename:'agent-workbench-client.js'},
  );
  const options = {
    request: async (method, ...args) => {
      calls.push({method, args});
      return {code:0, data:{session_id:args[0], next_seq:0, events:[]}};
    },
    receive: (session, envelope) => {
      received.push(envelope);
      if (Number.isSafeInteger(envelope?.next_seq)) session.cursor = envelope.next_seq;
      if (typeof envelope?.active === 'boolean') session.active = envelope.active;
      return {accepted:true,ack_cursor:envelope.next_seq};
    },
    isActive: session => session.active === true,
    setTimer: world.setTimeout,
    clearTimer: world.clearTimeout,
    delay: (_session, state) => state.errors ? 1000 * state.errors : state.mode === 'wait' ? 25 : 650,
    onFailure: (_session, detail) => failures.push(detail),
    onState: (_session, state) => states.push(state),
    ...overrides,
  };
  return {client:world.AgentWorkbenchClient.create(options), calls, received, failures, states, timers};
}

test('native wait uses the acknowledged cursor and commits only after the reducer accepts the whole envelope', async () => {
  const envelope = {session_id:'s', next_seq:4, active:false, events:[], subscription_generation:1, transport_mode:'wait'};
  const h = setup({request:async(method, ...args) => { h.calls.push({method,args}); return {code:0,data:envelope}; }});
  const session = {session_id:'s', cursor:2, active:true};
  const result = await h.client.poll(session, {preferWait:true, waitTimeoutMs:12000});
  assert.equal(result.status, 'accepted');
  assert.deepEqual(h.calls, [{method:'agent_wait_events', args:['s',2,1,12000]}]);
  assert.equal(session.cursor, 4);
  assert.equal(h.client.inspect('s').cursor, 4);
  assert.equal(h.client.inspect('s').errors, 0);
});

test('a valid long-poll timeout is an accepted no-op rather than an error', async () => {
  const h = setup({request:async(method, ...args) => {
    h.calls.push({method,args});
    return {code:0,data:{session_id:'s',next_seq:3,events:[],active:true,subscription_generation:1,transport_mode:'wait',wait_timed_out:true}};
  }});
  const session = {session_id:'s', cursor:3, active:true};
  const result = await h.client.poll(session, {preferWait:true});
  assert.equal(result.status, 'timeout');
  assert.equal(h.failures.length, 0);
  assert.equal(h.client.inspect('s').cursor, 3);
  assert.equal(h.client.inspect('s').errors, 0);
});

test('an explicit zero wait timeout is preserved for an immediate transport probe', async () => {
  const h = setup({request:async(method, ...args) => {
    h.calls.push({method,args});
    return {code:0,data:{session_id:'s',next_seq:0,events:[],active:true,subscription_generation:1,wait_timed_out:true}};
  }});
  const session = {session_id:'s',cursor:0,active:true};
  assert.equal((await h.client.poll(session,{preferWait:true,waitTimeoutMs:0})).status,'timeout');
  assert.deepEqual(h.calls[0],{method:'agent_wait_events',args:['s',0,1,0]});
});

test('unsupported long-poll falls back once and remains on legacy poll for that session', async () => {
  let waits = 0;
  const h = setup({request:async(method, ...args) => {
    h.calls.push({method,args});
    if (method === 'agent_wait_events') { waits++; return {code:1,reason:'agent_wait_events_unsupported'}; }
    return {code:0,data:{session_id:'s',next_seq:args[1] + 1,events:[],active:true}};
  }});
  const session = {session_id:'s', cursor:0, active:true};
  await h.client.poll(session, {preferWait:true});
  await h.client.poll(session, {preferWait:true});
  assert.equal(waits, 1);
  assert.deepEqual(h.calls.map(row => row.method), ['agent_wait_events','agent_poll','agent_poll']);
  assert.equal(h.client.inspect('s').fallback, true);
  assert.equal(h.client.inspect('s').cursor, 2);
});

test('a rejected envelope retains the acknowledged cursor and enters backoff', async () => {
  const h = setup({
    request:async(method, ...args) => {
      h.calls.push({method,args});
      return {code:0,data:{session_id:'s',next_seq:9,events:[]}};
    },
    receive:() => ({accepted:false,protocol:true,reason:'event_sequence_ahead'}),
  });
  const session = {session_id:'s', cursor:5, active:true};
  const result = await h.client.poll(session, {preferWait:false});
  assert.equal(result.status, 'protocol_error');
  assert.equal(h.client.inspect('s').cursor, 5);
  assert.equal(h.client.inspect('s').errors, 1);
  assert.equal(h.failures[0].reason, 'event_sequence_ahead');
  assert.ok([...h.timers.values()].some(timer => timer.delay === 1000));
});

test('an accepted flag without a transactional ACK receipt is rejected without advancing', async () => {
  const h = setup({
    request:async(method, ...args) => {
      h.calls.push({method,args});
      return {code:0,data:{session_id:'s',next_seq:9,events:[]}};
    },
    receive:() => ({accepted:true}),
  });
  const session = {session_id:'s', cursor:5, active:true};
  assert.equal(h.client.commitExternal(session,{accepted:true}),false);
  // Seed the authoritative cursor through a real receipt, not session.cursor.
  assert.equal(h.client.commitExternal(session,{accepted:true,ack_cursor:5}),true);
  const result = await h.client.poll(session, {preferWait:false});
  assert.equal(result.status, 'protocol_error');
  assert.equal(h.client.inspect('s').cursor, 5);
  assert.equal(h.failures[0].reason, 'receipt_ack_cursor_invalid');
});

test('a mismatched subscription generation is discarded before the reducer', async () => {
  const h = setup({request:async(method, ...args) => {
    h.calls.push({method,args});
    return {code:0,data:{session_id:'s',next_seq:7,events:[],subscription_generation:99}};
  }});
  const session = {session_id:'s', cursor:1, active:true};
  const result = await h.client.poll(session, {preferWait:true});
  assert.equal(result.status, 'stale_generation');
  assert.equal(h.received.length, 0);
  assert.equal(h.client.inspect('s').cursor, 1);
  assert.equal(h.client.inspect('s').errors, 0);
});

test('a wait response without its subscription generation is rejected without ACK', async () => {
  const h = setup({request:async(method, ...args) => {
    h.calls.push({method,args});
    return {code:0,data:{session_id:'s',next_seq:7,events:[]}};
  }});
  const session = {session_id:'s', cursor:1, active:true};
  const result = await h.client.poll(session, {preferWait:true});
  assert.equal(result.status, 'protocol_error');
  assert.equal(h.received.length, 0);
  assert.equal(h.client.inspect('s').cursor, 1);
  assert.equal(h.client.inspect('s').errors, 1);
  assert.equal(h.failures[0].reason, 'subscription_generation_invalid');
});

test('an accepted command response supersedes a pending wait and the late wait cannot replay or regress it', async () => {
  const first = deferred();
  let waitCount = 0;
  const h = setup({request:async(method, ...args) => {
    h.calls.push({method,args});
    if (method === 'agent_wait_events' && waitCount++ === 0) return first.promise;
    return {code:0,data:{session_id:'s',next_seq:6,events:[],active:false,subscription_generation:2}};
  }});
  const session = {session_id:'s', cursor:1, active:true};
  const pending = h.client.poll(session, {preferWait:true});
  await Promise.resolve();

  // AC.receive has accepted a command envelope and advanced the production
  // session before it tells the transport to adopt that cursor.
  session.cursor = 5;
  assert.equal(h.client.commitExternal(session, {accepted:true,ack_cursor:5}), true);
  first.resolve({code:0,data:{session_id:'s',next_seq:2,events:[{seq:2,type:'message'}],active:true,subscription_generation:1}});
  assert.equal((await pending).status, 'stale');
  assert.equal(h.received.length, 0);
  assert.equal(session.cursor, 5);
  assert.equal(h.client.inspect('s').cursor, 5);

  session.active = true;
  await h.client.poll(session, {preferWait:true});
  assert.deepEqual(h.calls.at(-1), {method:'agent_wait_events',args:['s',5,2,12000]});
  assert.equal(session.cursor, 6);
  assert.equal(h.client.inspect('s').cursor, 6);
});

test('invalidation drops a late wait without retrying any provider command', async () => {
  const waiting = deferred();
  const h = setup({request:async(method, ...args) => { h.calls.push({method,args}); return waiting.promise; }});
  const session = {session_id:'s', cursor:0, active:true};
  const pending = h.client.poll(session, {preferWait:true});
  await Promise.resolve();
  assert.equal(h.client.invalidate(session), true);
  waiting.resolve({code:0,data:{session_id:'s',next_seq:1,events:[],active:true,subscription_generation:1}});
  assert.equal((await pending).status, 'stale');
  assert.equal(h.received.length, 0);
  assert.deepEqual(h.calls.map(row => row.method), ['agent_wait_events']);
});

test('a stale generation guard is rejected before any Host request', async () => {
  const h = setup();
  const session = {session_id:'s',cursor:0,active:true};
  const result = await h.client.poll(session,{preferWait:false,guard:()=>false},true);
  assert.equal(result.status,'stale');
  assert.equal(h.calls.length,0);
  assert.equal(h.client.inspect('s'),null);
});
