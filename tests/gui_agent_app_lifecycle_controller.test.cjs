const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const web = path.join(__dirname, '../src/genshin_app/gui/web');
const clone = value => JSON.parse(JSON.stringify(value));
const tick = () => new Promise(resolve => setImmediate(resolve));
const deferred = () => {
  let resolve;
  const promise = new Promise(done => { resolve = done; });
  return {promise, resolve};
};

function load() {
  const world = {console};
  world.window = world;
  vm.createContext(world);
  vm.runInContext(
    fs.readFileSync(path.join(web, 'agent-app-lifecycle-controller.js'), 'utf8'),
    world,
    {filename:'agent-app-lifecycle-controller.js'},
  );
  return world.AgentAppLifecycleController;
}

function harness(overrides = {}) {
  const module = load();
  const local = {closing:false, busy:null, request:4};
  const rows = [
    {session_id:'one', timer:11, active:true, epoch:2},
    {session_id:'two', timer:null, active:false, epoch:5},
  ];
  const calls = [], notices = [];
  const options = {
    state:() => local,
    api:async name => { calls.push(name); return {code:0, data:{}}; },
    leaveWhiteboard:async () => { calls.push('leave-whiteboard'); return true; },
    leavePlan:async () => { calls.push('leave-plan'); return true; },
    active:() => null,
    confirm:message => { calls.push(['confirm', message]); return true; },
    stopRuntimeStatusMonitor:() => calls.push('stop-monitor'),
    invalidateTransport:value => calls.push(['invalidate', value]),
    clearStore:() => calls.push('clear-store'),
    clearTimer:value => calls.push(['clear-timer', value]),
    sessions:() => rows.values(),
    notice:(message, error = false) => notices.push({message, error}),
    errorText:(value, fallback) => value?.message || fallback,
    reopen:() => calls.push('reopen'),
    ...overrides,
  };
  const controller = module.create(options);
  return {controller, local, rows, calls, notices};
}

test('close gates the document, shuts down once, fences polls, cleans state, and closes natively', async () => {
  const e = harness();
  assert.equal(e.controller.requestClose(), false);
  assert.equal(await e.controller.settle(), true);
  assert.deepEqual(clone(e.calls), [
    'leave-whiteboard', 'leave-plan', 'agent_shutdown', 'stop-monitor',
    ['invalidate', {drop:true}], 'clear-store', ['clear-timer', 11], 'confirm_close',
  ]);
  assert.equal(e.local.request, 5);
  assert.equal(e.local.closing, false);
  assert.deepEqual(e.rows.map(row => ({timer:row.timer, active:row.active, epoch:row.epoch})), [
    {timer:null, active:false, epoch:3}, {timer:null, active:false, epoch:6},
  ]);
  e.controller.requestClose();
  assert.equal(await e.controller.settle(), true);
  assert.equal(e.calls.filter(value => value === 'agent_shutdown').length, 1);
  assert.equal(e.calls.filter(value => value === 'confirm_close').length, 1);
});

test('repeated close clicks share one owner while shutdown is pending', async () => {
  const gate = deferred();
  const e = harness({api:async name => { e.calls.push(name); return name === 'agent_shutdown' ? gate.promise : {code:0}; }});
  e.controller.requestClose();
  e.controller.requestClose();
  e.controller.requestClose();
  await tick();
  assert.equal(e.calls.filter(value => value === 'agent_shutdown').length, 1);
  gate.resolve({code:0});
  assert.equal(await e.controller.settle(), true);
  assert.equal(e.calls.filter(value => value === 'confirm_close').length, 1);
});

test('cancelling either document gate has no runtime or native-window side effect', async () => {
  const first = harness({leaveWhiteboard:async () => false});
  first.controller.requestClose();
  assert.equal(await first.controller.settle(), false);
  assert.deepEqual(first.calls, []);

  const second = harness({leavePlan:async () => false});
  second.controller.requestClose();
  assert.equal(await second.controller.settle(), false);
  assert.deepEqual(second.calls, ['leave-whiteboard']);
});

test('an active turn requires confirmation and cancellation keeps it running', async () => {
  const e = harness({active:() => ({session_id:'one'}), confirm:message => { e.calls.push(['confirm', message]); return false; }});
  e.controller.requestClose();
  assert.equal(await e.controller.settle(), false);
  assert.deepEqual(e.calls.map(value => Array.isArray(value) ? value[0] : value), ['leave-whiteboard', 'leave-plan', 'confirm']);
  assert.equal(e.rows[0].active, true);
});

test('shutdown failure keeps the window open and performs no cleanup', async () => {
  const e = harness({api:async name => { e.calls.push(name); return {code:'shutdown_failed', message:'仍在退出'}; }});
  e.controller.requestClose();
  assert.equal(await e.controller.settle(), false);
  assert.deepEqual(e.calls, ['leave-whiteboard', 'leave-plan', 'agent_shutdown', 'reopen']);
  assert.equal(e.local.request, 4);
  assert.equal(e.rows[0].active, true);
  assert.match(e.notices[0].message, /仍在退出/);
});

test('native close failure retries only native close and never repeats shutdown or cleanup', async () => {
  let closes = 0;
  const e = harness({api:async name => {
    e.calls.push(name);
    if (name === 'confirm_close' && closes++ === 0) return {code:'close_failed', message:'窗口仍在'};
    return {code:0};
  }});
  e.controller.requestClose();
  assert.equal(await e.controller.settle(), false);
  e.controller.requestClose();
  assert.equal(await e.controller.settle(), true);
  assert.equal(e.calls.filter(value => value === 'agent_shutdown').length, 1);
  assert.equal(e.calls.filter(value => value === 'stop-monitor').length, 1);
  assert.equal(e.calls.filter(value => value === 'confirm_close').length, 2);
  assert.equal(e.calls.filter(value => value === 'leave-whiteboard').length, 2);
});

test('agent-chat keeps only the lifecycle facade and explicit controller wiring', () => {
  const source = fs.readFileSync(path.join(web, 'agent-chat.js'), 'utf8');
  const bootstrap = fs.readFileSync(path.join(web, 'agent-workbench-bootstrap.js'), 'utf8');
  assert.doesNotMatch(source, /callApi\("agent_shutdown"/);
  assert.doesNotMatch(source, /callApi\("confirm_close"/);
  assert.match(source, /AC\.requestClose=\(\)=>AC\.lifecycleController\?\.requestClose/);
  assert.doesNotMatch(source, /AgentAppLifecycleController\.create\(\{/);
  assert.match(bootstrap, /modules\.lifecycle\.create\(\{/);
});
