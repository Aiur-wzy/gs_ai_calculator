const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const R = require('./helpers/gui_runtime.cjs');

const tick = () => new Promise(resolve => setImmediate(resolve));
const deferred = () => {
  let resolve, reject;
  const promise = new Promise((yes, no) => { resolve = yes; reject = no; });
  return {promise, resolve, reject};
};

function commandRecorder(handlers = {}) {
  const commands = [];
  return {
    commands,
    api: async (name, ...args) => {
      commands.push({name, args});
      const handler = handlers[name];
      if (!handler) return {code:1, reason:`unexpected_${name}`};
      return typeof handler === 'function' ? handler(...args) : handler;
    },
    count: name => commands.filter(row => row.name === name).length,
    first: name => commands.find(row => row.name === name),
  };
}

function harness(overrides = {}) {
  const context = {window:null, console, setTimeout, clearTimeout};
  context.window = context;
  vm.createContext(context);
  R.loadWebFile(context, path.join(__dirname, '../src/genshin_app/gui/web/agent-turn-controller.js'));

  const localState = {
    busy:null,
    request:0,
    connection:'ready',
    credential:{saved:true},
    hostUi:{credential_mode:'saved', runtime_phase:'checking_runtime'},
    runtimePrepare:null,
    runtimeStatusGeneration:0,
    workbenchCapabilities:{durable_inbox:true},
    models:[{model:'deepseek-chat'}],
    ...(overrides.state || {}),
  };
  const nodeState = {
    input:{value:'完成这轮计算'},
    kind:{value:'whiteboard'},
    workflow:{value:'calculate'},
    model:{value:'deepseek-chat'},
    effort:{value:'high'},
    inboxMode:{value:'queue'},
    ...(overrides.nodes || {}),
  };
  const notices = [], scheduled = [], polled = [], history = [], rejected = [], invalidated = [];
  const sessions = new Map();
  let selectedSession = overrides.selected || null;
  let activeSession = overrides.active || null;
  if (selectedSession?.session_id) sessions.set(selectedSession.session_id, selectedSession);
  let projection = overrides.projection || null;
  let serial = 0;
  const recorder = overrides.recorder || commandRecorder({
    agent_start:{code:0, data:{session_id:'session-one'}},
  });
  const transport = {
    rejectExternal:(session, reason) => rejected.push({session, reason}),
    invalidate:session => invalidated.push(session),
  };
  const controller = context.AgentTurnController.create({
    api:recorder.api,
    state:() => localState,
    nodes:() => nodeState,
    notice:(message, error=false) => notices.push({message, error}),
    refresh:() => {},
    selected:() => selectedSession,
    active:() => activeSession,
    isActive:session => session === activeSession && session?.live === true,
    loadFoundation:async () => true,
    foundation:() => projection,
    schedule:session => scheduled.push(session),
    mutationId:prefix => `${prefix}-${++serial}`,
    requestId:prefix => `${prefix}-request-id`,
    errorText:(response, fallback) => response?.reason || response?.message || fallback,
    hasRuntimeCapability:name => name === 'runtime_prepare' && overrides.runtimePrepare !== false,
    applyRuntimeStatus:value => { localState.appliedRuntimeStatus = value; },
    checkpointFrozen:() => false,
    workbenchTarget:() => ({turn_id:'turn-one', segment_id:'segment-one'}),
    composer:() => ({clearIf:message => { if (nodeState.input.value === message) nodeState.input.value = ''; }}),
    inboxDock:() => ({setBusy:() => {}}),
    pendingApplicationSession:() => null,
    hasUnsettledAppAction:() => false,
    cliConnected:() => true,
    sessionProviderMatches:() => true,
    workbenchProjection:() => projection,
    capture:overrides.capture || (kind => ({kind, title:'测试方案', document:{kind}, applicationPayload:{revision:1}})),
    same:() => true,
    workflow:() => 'calculate',
    noReasoningModel:() => false,
    validEffort:() => true,
    workspace:base => ({document_kind:base.kind}),
    runtimePhaseText:() => '正在检查本地 AI 服务',
    loadHistory:() => history.push('load'),
    poll:(session, force) => { polled.push({session, force}); },
    getSession:id => {
      if (!sessions.has(id)) sessions.set(id, {session_id:id, epoch:0, live:false});
      return sessions.get(id);
    },
    receiveCommand:overrides.receiveCommand || (() => ({accepted:true})),
    transport:() => transport,
    receiveProtocolText:reason => `协议拒绝：${reason}`,
    isAuthoring:() => false,
    activateSession:({session}) => { selectedSession = session; },
    finishAcceptedSession:() => {},
  });
  return {
    controller, recorder, state:localState, nodes:nodeState, notices, scheduled,
    polled, history, rejected, invalidated, sessions,
    setProjection:value => { projection = value; },
    setSelected:value => { selectedSession = value; },
    setActive:value => { activeSession = value; },
  };
}

test('agent-chat keeps only compatibility facades for turn command orchestration', () => {
  const source = fs.readFileSync(path.join(__dirname, '../src/genshin_app/gui/web/agent-chat.js'), 'utf8');
  for (const method of [
    'agent_start', 'agent_send', 'agent_stop', 'agent_prepare_runtime',
    'agent_wait_runtime_prepare', 'agent_cancel_runtime_prepare',
    'agent_enqueue_workbench_inbox', 'agent_promote_workbench_inbox',
    'agent_revise_workbench_inbox', 'agent_cancel_workbench_inbox',
    'agent_reorder_workbench_inbox',
  ]) assert.doesNotMatch(source, new RegExp(`callApi\\(\\"${method}\\"`));
  for (const name of [
    'inboxRequest', 'enqueueActiveMessage', 'handleInboxAction',
    'canPrepareColdRuntime', 'prepareColdRuntime', 'send', 'stop',
  ]) assert.match(source, new RegExp(`AC\\.${name}=.*AC\\.turnController`));
});

test('new and continuation commands preserve start/send snapshot and idempotency contracts', async () => {
  let recorder = commandRecorder({agent_start:{code:0, data:{session_id:'session-one'}}});
  let h = harness({recorder});
  assert.equal(await h.controller.send(), true);
  assert.deepEqual(JSON.parse(JSON.stringify(recorder.first('agent_start').args)), [{
    kind:'whiteboard',
    workflow:'calculate',
    document:{kind:'whiteboard'},
    workspace:{document_kind:'whiteboard'},
    message:'完成这轮计算',
    model:'deepseek-chat',
    effort:'high',
    client_request_id:'agent-request-request-id',
  }]);

  const current = {session_id:'session-existing', live:true, epoch:2, kind:'whiteboard', workflow:'calculate'};
  recorder = commandRecorder({agent_send:{code:0, data:{session_id:current.session_id}}});
  h = harness({recorder, selected:current});
  assert.equal(await h.controller.send(), true);
  assert.deepEqual(JSON.parse(JSON.stringify(recorder.first('agent_send').args)), [
    current.session_id,
    '完成这轮计算',
    'agent-request-request-id',
    {kind:'whiteboard'},
    {document_kind:'whiteboard'},
    {revision:1},
  ]);
});

test('a document-free composer cannot create Agent history or prepare the provider runtime', async () => {
  const recorder = commandRecorder({});
  const h = harness({recorder, projection:{axes:{runtime_state:'cold'}}, capture:() => { throw new Error('请先新建或打开创作白板。'); }});
  assert.equal(await h.controller.send(), false);
  assert.equal(recorder.count('agent_prepare_runtime'), 0);
  assert.equal(recorder.count('agent_start'), 0);
  assert.equal(recorder.count('agent_send'), 0);
  assert.match(h.notices.at(-1).message, /请先新建或打开创作白板/);
  assert.equal(h.history.length, 0);
});

test('a rejected first message leaves no local Agent history', async () => {
  const recorder = commandRecorder({agent_start:{code:1, message:'任务未被 App 接收'}});
  const h = harness({recorder});
  assert.equal(await h.controller.send(), false);
  assert.equal(recorder.count('agent_start'), 1);
  assert.equal(h.sessions.size, 0);
  assert.equal(h.scheduled.length, 0);
  assert.match(h.notices.at(-1).message, /任务未被 App 接收/);
  assert.doesNotMatch(h.notices.map(row => row.message).join('\n'), /Host|runtime|Queue|lineage|revision|cursor/i);
});

test('active-turn message preserves Host binding and open question forces Queue', async () => {
  const session = {session_id:'session-active', live:true};
  const binding = {document_kind:'whiteboard', document_id:'board-one', revision:7, fingerprint:'sha'};
  const recorder = commandRecorder({agent_enqueue_workbench_inbox:{code:0, data:{status:'queued'}}});
  const h = harness({selected:session, active:session, recorder, nodes:{inboxMode:{value:'steer'}}, projection:{
    projection_revision:9,
    document_binding:binding,
    questions:[{question_id:'question-one', state:'open'}],
    inbox:[],
  }});

  assert.equal(await h.controller.enqueueActiveMessage(), true);
  assert.equal(recorder.count('agent_enqueue_workbench_inbox'), 1);
  const command = recorder.first('agent_enqueue_workbench_inbox');
  assert.equal(command.args[0], session.session_id);
  assert.equal(command.args[1].kind, 'queue');
  assert.strictEqual(command.args[1].document_binding, binding);
  assert.equal(recorder.count('agent_start'), 0);
  assert.equal(recorder.count('agent_send'), 0);
  assert.doesNotMatch(h.notices.map(row => row.message).join('\n'), /Host|runtime|Queue|lineage|revision|cursor/i);
});

test('double-clicking active-turn send cannot enqueue the same text twice', async () => {
  const pending = deferred();
  const session = {session_id:'session-active', live:true};
  const recorder = commandRecorder({agent_enqueue_workbench_inbox:() => pending.promise});
  const h = harness({selected:session, active:session, recorder, projection:{
    projection_revision:2,
    document_binding:{document_kind:'whiteboard', document_id:'board-one', revision:1},
    questions:[],
    inbox:[],
  }});

  const first = h.controller.enqueueActiveMessage();
  await tick();
  assert.equal(await h.controller.enqueueActiveMessage(), false);
  assert.equal(recorder.count('agent_enqueue_workbench_inbox'), 1);
  pending.resolve({code:0, data:{status:'queued'}});
  assert.equal(await first, true);
  assert.equal(recorder.count('agent_enqueue_workbench_inbox'), 1);
});

test('duplicate send click records only one provider-bearing Host command', async () => {
  const pending = deferred();
  const recorder = commandRecorder({agent_start:() => pending.promise});
  const h = harness({recorder});

  const first = h.controller.send();
  const second = await h.controller.send();
  assert.equal(second, false);
  assert.equal(recorder.count('agent_start'), 1);
  pending.resolve({code:0, data:{session_id:'session-one'}});
  assert.equal(await first, true);
  assert.equal(recorder.count('agent_start'), 1);
  assert.match(h.notices.find(row => /当前操作尚未结束/.test(row.message))?.message || '', /当前操作尚未结束/);
});

test('cancel before cold prepare identity arrives fences the turn and cancels the late operation', async () => {
  const preparing = deferred();
  const recorder = commandRecorder({
    agent_prepare_runtime:() => preparing.promise,
    agent_cancel_runtime_prepare:{code:0, data:{status:'cancelled'}},
  });
  const h = harness({recorder, projection:{axes:{runtime_state:'cold'}}});

  const sending = h.controller.send();
  assert.equal(h.state.busy, 'runtime-start');
  assert.equal(await h.controller.stop(), true);
  preparing.resolve({code:0, data:{operation_id:'prepare-one', status:'preparing'}});
  assert.equal(await sending, false);
  assert.equal(recorder.count('agent_prepare_runtime'), 1);
  assert.equal(recorder.count('agent_cancel_runtime_prepare'), 1);
  assert.equal(recorder.count('agent_start'), 0);
  assert.equal(recorder.count('agent_send'), 0);
});

test('cancel while waiting for cold runtime ignores a late ready result', async () => {
  const waited = deferred();
  const recorder = commandRecorder({
    agent_prepare_runtime:{code:0, data:{operation_id:'prepare-one', status:'preparing'}},
    agent_wait_runtime_prepare:() => waited.promise,
    agent_cancel_runtime_prepare:{code:0, data:{status:'ready'}},
  });
  const h = harness({recorder, projection:{axes:{runtime_state:'cold'}}});

  const sending = h.controller.send();
  await tick();
  assert.equal(h.state.runtimePrepare.operation_id, 'prepare-one');
  assert.equal(await h.controller.stop(), true);
  waited.resolve({code:0, data:{status:'ready'}});
  assert.equal(await sending, false);
  assert.equal(recorder.count('agent_cancel_runtime_prepare'), 1);
  assert.equal(recorder.count('agent_runtime_status'), 0);
  assert.equal(recorder.count('agent_start'), 0);
});

test('provider-possibly-accepted protocol failure refreshes read-only and never resends', async () => {
  const recorder = commandRecorder({agent_start:{code:0, data:{session_id:'session-one'}}});
  const h = harness({recorder, receiveCommand:() => ({accepted:false, reason:'command_cursor_gap'})});

  assert.equal(await h.controller.send(), false);
  assert.equal(recorder.count('agent_start'), 1);
  assert.equal(h.rejected.length, 1);
  assert.equal(h.polled.length, 1);
  assert.equal(h.history.length, 1);
  assert.match(h.notices.at(-1).message, /避免重复发送/);
});

test('possibly-executed stop protocol failure refreshes and never repeats stop', async () => {
  const session = {session_id:'session-active', live:true, epoch:0, state:'running'};
  const recorder = commandRecorder({agent_stop:{code:0, data:{session_id:session.session_id}}});
  const h = harness({selected:session, active:session, recorder, receiveCommand:() => ({accepted:false, reason:'command_cursor_gap'})});

  assert.equal(await h.controller.stop(), false);
  assert.equal(recorder.count('agent_stop'), 1);
  assert.equal(h.invalidated.length, 1);
  assert.equal(h.rejected.length, 1);
  assert.equal(h.polled.length, 1);
  assert.match(h.notices.at(-1).message, /不会重复停止命令/);
});
