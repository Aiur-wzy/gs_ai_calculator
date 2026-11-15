'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

class Node {
  constructor(tag = 'div') {
    this.tagName = tag;
    this.children = [];
    this.value = '';
    this.hidden = false;
    this.textContent = '';
  }
  append(...children) { this.children.push(...children); }
  replaceChildren(...children) { this.children = [...children]; }
  focus() { this.focused = true; }
}

function deferred() {
  let resolve;
  const promise = new Promise(done => { resolve = done; });
  return {promise, resolve};
}

const nextTurn = () => new Promise(resolve => setImmediate(resolve));

function setup(overrides = {}) {
  const world = {};
  world.window = world;
  Object.defineProperty(world, 'AC', {get() { throw new Error('controller_must_not_read_window_AC'); }});
  vm.createContext(world);
  vm.runInContext(
    fs.readFileSync(path.join(__dirname, '../src/genshin_app/gui/web/agent-session-controller.js'), 'utf8'),
    world,
    {filename: 'agent-session-controller.js'},
  );
  const state = {
    sessions: new Map(), selected: null, active: null, history: [], busy: null,
    request: 0, viewEpoch: 0, connection: 'ready', connectionGeneration: 0, questionTakeover: false,
    workbenchCapabilities: {session_lifecycle: false},
  };
  const nodes = {
    history: new Node('select'), messages: new Node(), toolsBody: new Node(),
    appActionsBody: new Node(), newMessages: new Node(), input: new Node('input'),
    historyWindowHost: new Node(), telemetryHost: new Node(), trajectoryList: new Node(),
    inboxSection: new Node(), checkpointSection: new Node(), workflowExplicit: true,
  };
  const calls = [];
  const notices = [];
  const drops = [];
  const api = overrides.api || (async (name, ...args) => {
    calls.push({name, args});
    if (name === 'agent_sessions') return {code: 0, data: {sessions: [], workbench_capabilities: {session_lifecycle: false}}};
    return {code: 0, data: {}};
  });
  const options = {
    state, nodes, eventStore: {drop: id => drops.push(id)},
    messageNodes: new Map(), toolNodes: new Map(), actionNodes: new Map(), api,
    storeModel: session => session?._model || null,
    isActive: session => session?.active === true || ['running', 'starting', 'preparing', 'stopping', 'cancelling'].includes(session?.state),
    createWorkbenchState: () => ({revision: 0}),
    pendingApplicationSession: overrides.pendingApplicationSession || (() => null),
    saveReadingAnchor: () => {}, switchComposerContext: () => {}, renderModelControls: () => {},
    refresh: () => {}, poll: overrides.poll || (async () => {}),
    loadHostOutline: overrides.loadHostOutline || (async () => {}),
    loadWorkbenchFoundation: overrides.loadWorkbenchFoundation || (async () => {}),
    bindCheckpoints: overrides.bindCheckpoints || (async () => {}),
    restoreReadingAnchor: overrides.restoreReadingAnchor || (() => {}),
    notice: (message, error = false) => notices.push({message, error}), currentKind: () => 'whiteboard',
    cliConnected: () => true, sessionProviderMatches: () => true,
    capture: () => ({kind: 'whiteboard', title: '方案', document: {board_id: 'board-one'}, applicationPayload: null}),
    workspace: () => ({root: 'workspace'}), recoverCommittedApplication: overrides.recoverCommittedApplication,
    recoverLastAppliedProjection: overrides.recoverLastAppliedProjection,
    receiveCommand: overrides.receiveCommand || (() => ({accepted: true})), transportReject: () => {},
    receiveProtocolText: reason => String(reason || 'protocol_error'), workbenchCapabilities: value => value || {session_lifecycle: false},
    clone: value => value == null ? value : JSON.parse(JSON.stringify(value)), schedule: () => {},
    renderSessionNavigation: () => {}, mutationId: kind => `${kind}-one`,
    errorText: (response, fallback) => response?.message || fallback, label: value => String(value || 'ready'),
    uiElement: (tag, text) => Object.assign(new Node(tag), {textContent: text}),
    confirm: overrides.confirm || (() => true), transportInvalidate: () => {}, dropComposerContext: () => {},
    historyWindowView: () => ({clear() {}}), telemetryView: () => ({clear() {}}),
    conversationView: overrides.conversationView || (() => null), composer: overrides.composer || (() => null),
    shellSetSection: overrides.shellSetSection || (() => {}),
    questionController: () => ({clear() {}}), inboxDock: () => ({clear() {}}), checkpointView: () => ({clear() {}}),
  };
  const controller = world.AgentSessionController.create(options);
  return {world, state, nodes, calls, notices, drops, controller};
}

test('viewing an idle conversation never steals the running execution owner', async () => {
  const e = setup();
  const running = e.controller.session('running');
  running.active = true;
  running.state = 'running';
  e.state.active = running.session_id;
  const idle = e.controller.session('idle');
  idle.state = 'completed';
  e.state.selected = running.session_id;

  assert.equal(await e.controller.selectSession(idle.session_id), true);
  assert.equal(e.controller.selected(), idle);
  assert.equal(e.controller.active(), running);
  assert.equal(e.state.active, running.session_id);
  assert.equal(e.nodes.reviewSignature, null);
});

test('only the newest history request may publish its catalog', async () => {
  const stale = deferred();
  let requests = 0;
  const e = setup({api: async name => {
    assert.equal(name, 'agent_sessions');
    requests++;
    if (requests === 1) return stale.promise;
    return {code: 0, data: {sessions: [{session_id: 'new'}], workbench_capabilities: {session_lifecycle: true}}};
  }});

  const older = e.controller.loadHistory();
  assert.equal(await e.controller.loadHistory(), true);
  stale.resolve({code: 0, data: {sessions: [{session_id: 'old'}], workbench_capabilities: {session_lifecycle: true}}});

  assert.equal(await older, false);
  assert.deepEqual(e.state.history.map(row => row.session_id), ['new']);
  assert.equal(e.state.sessions.has('old'), false);
});

test('history response from an obsolete connection generation cannot publish', async () => {
  const response = deferred();
  const e = setup({api: async name => {
    assert.equal(name, 'agent_sessions');
    return response.promise;
  }});

  const pending = e.controller.loadHistory();
  e.state.connectionGeneration++;
  response.resolve({code: 0, data: {sessions: [{session_id: 'old-connection'}], workbench_capabilities: {session_lifecycle: true}}});

  assert.equal(await pending, false);
  assert.deepEqual(e.state.history, []);
  assert.equal(e.state.sessions.has('old-connection'), false);
});

test('selection hydration stops when a newer view wins during an await', async () => {
  const foundation = deferred();
  const bound = [];
  const e = setup({
    loadWorkbenchFoundation: async session => session.session_id === 'one' ? foundation.promise : undefined,
    bindCheckpoints: async session => { bound.push(session.session_id); },
  });
  e.controller.session('one');
  e.controller.session('two');

  const older = e.controller.selectSession('one');
  await nextTurn();
  assert.equal(await e.controller.selectSession('two'), true);
  foundation.resolve();

  assert.equal(await older, false);
  assert.equal(e.state.selected, 'two');
  assert.deepEqual(bound, ['two']);
});

test('pending application ownership blocks both browsing away and new-task creation', async () => {
  let owner;
  const e = setup({pendingApplicationSession: () => owner});
  owner = e.controller.session('transaction-owner');
  e.controller.session('other');
  e.state.selected = owner.session_id;
  e.nodes.history.value = owner.session_id;

  assert.equal(await e.controller.selectSession('other'), false);
  assert.equal(e.state.selected, owner.session_id);
  assert.equal(e.nodes.history.value, owner.session_id);
  assert.equal(e.controller.newTask(), false);
  assert.match(e.notices.at(-1).message, /候选应用事务/);
});

test('rename becomes visible only from the Host-acknowledged catalog refresh', async () => {
  let e;
  const api = async (name, ...args) => {
    e.calls.push({name, args});
    if (name === 'agent_rename_session') {
      assert.equal(e.controller.session('one').title, '旧名称');
      return {code: 0, data: {metadata_revision: 2}};
    }
    if (name === 'agent_sessions') return {code: 0, data: {sessions: [{session_id: 'one', title: 'Host名称', metadata_revision: 2}], workbench_capabilities: {session_lifecycle: true}}};
    throw new Error(`unexpected:${name}`);
  };
  e = setup({api});
  e.state.workbenchCapabilities.session_lifecycle = true;
  e.controller.session('one').title = '旧名称';

  assert.equal(await e.controller.renameSession('one', '请求名称', 1), true);
  assert.equal(e.controller.session('one').title, 'Host名称');
  assert.deepEqual(e.calls.map(row => row.name), ['agent_rename_session', 'agent_sessions']);
});

test('mark-read retries one stale Host revision and never clears unread optimistically', async () => {
  let e;
  let markCalls = 0;
  let historyCalls = 0;
  const api = async (name, ...args) => {
    e.calls.push({name, args});
    if (name === 'agent_mark_session_read') {
      markCalls++;
      assert.equal(e.controller.session('one').unread, true);
      return markCalls === 1 ? {code: 1, reason: 'native_session_metadata_stale'} : {code: 0, data: {}};
    }
    if (name === 'agent_sessions') {
      historyCalls++;
      const unread = historyCalls === 1;
      return {code: 0, data: {sessions: [{session_id: 'one', unread, metadata_revision: historyCalls + 1}], workbench_capabilities: {session_lifecycle: true}}};
    }
    throw new Error(`unexpected:${name}`);
  };
  e = setup({api});
  e.state.workbenchCapabilities.session_lifecycle = true;
  const session = e.controller.session('one');
  session.unread = true;
  e.state.selected = session.session_id;
  e.state.history = [{session_id: 'one', unread: true, metadata_revision: 1}];

  assert.equal(await e.controller.markSessionRead('one'), true);
  assert.equal(markCalls, 2);
  assert.equal(e.controller.session('one').unread, false);
});

test('mark-read neither publishes nor retries after the viewed session changes', async () => {
  const stale = deferred();
  let markCalls = 0;
  const e = setup({api: async name => {
    if (name === 'agent_mark_session_read') {
      markCalls++;
      return stale.promise;
    }
    throw new Error(`unexpected:${name}`);
  }});
  e.state.workbenchCapabilities.session_lifecycle = true;
  const one = e.controller.session('one');
  e.controller.session('two');
  one.unread = true;
  e.state.selected = 'one';
  e.state.history = [{session_id: 'one', unread: true, metadata_revision: 1}];

  const pending = e.controller.markSessionRead('one');
  await nextTurn();
  e.state.selected = 'two';
  e.state.viewEpoch++;
  stale.resolve({code: 1, reason: 'native_session_metadata_stale'});

  assert.equal(await pending, false);
  assert.equal(markCalls, 1);
  assert.equal(one.unread, true);
});

test('resume projection blocker delegates recovery without marking the shell live', async () => {
  let recovered = 0;
  const e = setup({
    api: async name => name === 'agent_resume'
      ? {code: 1, reason: 'native_candidate_application_projection_required'}
      : {code: 0, data: {}},
    recoverCommittedApplication: async () => { recovered++; return true; },
  });
  const session = e.controller.session('one');
  session.kind = 'whiteboard';
  e.state.selected = session.session_id;

  assert.equal(await e.controller.resumeSession(), true);
  assert.equal(recovered, 1);
  assert.equal(session.live, false);
  assert.equal(e.state.active, null);
});

test('targeted resume does not change the viewed session while another execution owns the runtime', async () => {
  const e=setup();const running=e.controller.session('running'),cold=e.controller.session('cold'),viewed=e.controller.session('viewed');running.active=true;running.state='running';cold.canResume=true;cold.kind='whiteboard';e.state.active='running';e.state.selected='viewed';
  assert.equal(await e.controller.resumeSession('cold'),false);assert.equal(e.state.selected,'viewed');assert.equal(e.calls.some(row=>row.name==='agent_resume'),false);assert.equal(viewed.session_id,'viewed');
});

test('deletion drops renderer state only after the Host acknowledges forget', async () => {
  let e;
  const api = async (name, ...args) => {
    e.calls.push({name, args});
    if (name === 'agent_forget') {
      assert.equal(e.state.sessions.has('one'), true);
      return {code: 0, data: {}};
    }
    if (name === 'agent_sessions') return {code: 0, data: {sessions: [], workbench_capabilities: {session_lifecycle: true}}};
    throw new Error(`unexpected:${name}`);
  };
  e = setup({api});
  e.state.workbenchCapabilities.session_lifecycle = true;
  e.controller.session('one').state = 'completed';
  e.state.selected = 'one';

  assert.equal(await e.controller.forget(), true);
  assert.equal(e.state.sessions.has('one'), false);
  assert.deepEqual(e.drops, ['one']);
  assert.deepEqual(e.calls.map(row => row.name), ['agent_forget', 'agent_sessions']);
});

test('forget preserves a newer selection and an older history response cannot resurrect deletion', async () => {
  const oldHistory = deferred();
  const forgot = deferred();
  let historyCalls = 0;
  const e = setup({api: async name => {
    if (name === 'agent_sessions') {
      historyCalls++;
      if (historyCalls === 1) return oldHistory.promise;
      return {code: 0, data: {sessions: [{session_id: 'two'}], workbench_capabilities: {session_lifecycle: true}}};
    }
    if (name === 'agent_forget') return forgot.promise;
    throw new Error(`unexpected:${name}`);
  }});
  e.state.workbenchCapabilities.session_lifecycle = true;
  e.controller.session('one').state = 'completed';
  e.controller.session('two').state = 'completed';
  e.state.selected = 'one';
  const staleLoad = e.controller.loadHistory();
  const deletion = e.controller.forget();
  await nextTurn();
  assert.equal(await e.controller.selectSession('two'), true);
  forgot.resolve({code: 0, data: {}});

  assert.equal(await deletion, true);
  assert.equal(e.state.selected, 'two');
  assert.equal(e.state.sessions.has('one'), false);
  oldHistory.resolve({code: 0, data: {sessions: [{session_id: 'one'}], workbench_capabilities: {session_lifecycle: true}}});
  assert.equal(await staleLoad, false);
  assert.equal(e.state.sessions.has('one'), false);
});

test('targeted deletion never selects or marks the context-menu target before Host approval', async () => {
  let e, confirmed = '';
  const api = async (name, ...args) => {
    e.calls.push({name,args});
    if (name === 'agent_forget') return {code:0,data:{}};
    if (name === 'agent_sessions') return {code:0,data:{sessions:[{session_id:'viewed',title:'仍在查看'}],workbench_capabilities:{session_lifecycle:true}}};
    throw new Error(`unexpected:${name}`);
  };
  e=setup({api,confirm:message=>{confirmed=message;return true;}});e.state.workbenchCapabilities.session_lifecycle=true;
  e.controller.session('target').state='completed';e.controller.session('viewed').state='completed';e.state.selected='viewed';e.state.history=[{session_id:'target',title:'精确目标',metadata_revision:4,can_forget:true},{session_id:'viewed',title:'仍在查看'}];
  assert.equal(await e.controller.forgetSession('target',4),true);assert.equal(e.state.selected,'viewed');assert.equal(e.state.sessions.has('target'),false);assert.match(confirmed,/精确目标/);
  assert.deepEqual(e.calls.map(row=>row.name),['agent_forget','agent_sessions']);assert.equal(e.calls[0].args[0],'target');
});

test('targeted deletion honors the latest Host can_forget projection and never calls forget when vetoed', async () => {
  const e=setup();e.state.workbenchCapabilities.session_lifecycle=true;e.controller.session('locked').state='completed';e.state.history=[{session_id:'locked',title:'锁定',metadata_revision:3,can_forget:false}];
  assert.equal(await e.controller.forgetSession('locked',3),false);assert.equal(e.state.sessions.has('locked'),true);assert.equal(e.calls.some(row=>row.name==='agent_forget'),false);assert.match(e.notices.at(-1).message,/不能删除/);
});

test('accepted turn activation is a single session-controller projection transition', () => {
  const activated=[],cleared=[],sections=[],composer={clearIf:value=>cleared.push(['draft',value]),save:()=>cleared.push(['save']),autoGrow:()=>cleared.push(['grow'])};
  const e=setup({conversationView:()=>({activate:id=>activated.push(id)}),composer:()=>composer,shellSetSection:(...args)=>sections.push(args)});
  const session=e.controller.session('one');e.nodes.input.value=' exact message ';
  assert.equal(e.controller.activateAcceptedSession({session,message:'exact message'}),true);
  assert.equal(e.state.selected,'one');assert.equal(e.state.viewEpoch,1);assert.deepEqual(activated,['one']);
  assert.equal(e.controller.finishAcceptedSession({message:'exact message'}),true);
  assert.equal(e.nodes.input.value,'');assert.deepEqual(cleared,[['draft','exact message'],['save'],['grow']]);
  assert.deepEqual(sections,[['settings',false,false],['details',false,true]]);
});
