'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

class Element {
  constructor(tag = 'div', text = null, className = '') {
    this.tagName = tag; this.children = []; this.attributes = {}; this.dataset = {};
    this.hidden = false; this.value = ''; this.title = ''; this.className = className; this.replaceCount = 0; this.listeners = {}; this.focusCount = 0;
    if (text !== null && text !== undefined) this.text = String(text);
  }
  append(...nodes) { this.children.push(...nodes); }
  replaceChildren(...nodes) { this.replaceCount++; this.children = [...nodes]; this.text = ''; }
  setAttribute(key, value) { this.attributes[key] = String(value); }
  addEventListener(key, handler) { (this.listeners[key] ||= []).push(handler); }
  emit(key, event = {}) { for (const handler of this.listeners[key] || []) handler({preventDefault(){}, ...event}); }
  focus() { this.focusCount++; }
  set textContent(value) { this.children = []; this.text = String(value); }
  get textContent() { return (this.text || '') + this.children.map(child => child.textContent).join(''); }
}

function loadView() {
  const world = {JSON}; world.window = world;
  world.UI = {
    el: (tag, text, className) => new Element(tag, text, className),
    button: (label, handler, className) => { const node = new Element('button', label, className); node.handler = handler; return node; },
  };
  vm.createContext(world);
  for (const name of ['agent-workbench-selectors.js', 'agent-workbench-view.js']) {
    vm.runInContext(fs.readFileSync(path.join(__dirname, '../src/genshin_app/gui/web', name), 'utf8'), world);
  }
  return world;
}

const nodes = () => ({
  workbenchViews: new Element(), statusAxes: new Element(), status: new Element(),
  chatTab: new Element('button'), trajectoryTab: new Element('button'),
  chatView: new Element(), trajectoryView: new Element(), turnRail: new Element(),
  search: new Element('input'), trajectoryList: new Element(), trajectoryMeta: new Element(),
});

test('view namespace is frozen and unknown axes render honestly without rebuilding stable chips', () => {
  const world = loadView();
  assert.equal(Object.isFrozen(world.AgentWorkbenchView), true);
  const projection = world.AgentWorkbenchView.project({client:{connection:'ready'}, host:{runtime_state:'future_state'}});
  const host = new Element();
  world.AgentWorkbenchView.renderStatusAxes({host, projection});
  const firstChip = host.children[0], runtime = host.children.find(node => node.dataset.axis === 'runtime_state');
  assert.equal(host.children.length, 7); assert.equal(runtime.dataset.state, 'unknown'); assert.match(runtime.textContent, /未知/);
  world.AgentWorkbenchView.renderStatusAxes({host, projection});
  assert.equal(host.children[0], firstChip); assert.equal(host.replaceCount, 1);
});

test('status hub derives one headline and deduplicated attention count from existing Host facts', () => {
  const world = loadView();
  const session = {candidate:{ready:true}, applied:false, appActions:[
    {action_id:'action-one', status:'queued'},
    {action_id:'action-two', status:'completed'},
  ]};
  const projection = world.AgentWorkbenchSelectors.project({session, host:{
    provider_state:'verified', runtime_state:'ready', conversation_state:'action_required',
    turn_state:'awaiting_input_live', document_state:'candidate_ready', inbox_state:'empty',
  }});
  const hub = world.AgentWorkbenchView.statusHubProjection(session, projection);
  assert.equal(hub.headline, '等待回答');
  assert.equal(hub.attention_count, 3);
  assert.deepEqual(Array.from(hub.attention, row => row.id), ['question', 'app:action-one', 'candidate']);
  assert.equal(hub.severity, 'attention');
});

test('single action shelf shows only its highest priority item until explicitly expanded', () => {
  const world = loadView(), make = (text = '') => new Element('section', text), body = new Element();
  const nodes = {
    actionShelf:new Element(), actionShelfBody:body, actionShelfHeadline:new Element(), actionShelfCount:new Element(), actionShelfMore:new Element('button'),
    error:make(''), contextWarning:make('请先恢复文档'), questionHost:make(), appActions:make(), appActionsBody:make(), candidate:make('候选'),
  };
  nodes.contextWarning.dataset.attentionEligible = 'true'; nodes.questionHost.append(make('问题'));
  nodes.appActionsBody.append(make('操作')); nodes.candidate.append(make('候选内容')); nodes.candidate.dataset.attentionEligible = 'true';
  body.append(nodes.contextWarning, nodes.questionHost, nodes.appActions, nodes.candidate, nodes.error);
  const result = world.AgentWorkbenchView.renderAttentionShelf({nodes});
  assert.equal(result.count, 4); assert.equal(result.primary, 'warning');
  assert.equal(nodes.contextWarning.hidden, false); assert.equal(nodes.questionHost.hidden, true);
  assert.equal(nodes.actionShelfMore.textContent, '还有 3 项');
});

test('hidden trajectory remains untouched while turn rail stays available', () => {
  const world = loadView(), n = nodes(), sentinel = new Element('p', 'keep'); n.trajectoryList.append(sentinel);
  const session = {messages:[{id:'m1',run_id:'r1',role:'user',text:'一轮',seq:1}],tools:[]};
  world.AgentWorkbenchView.render({
    nodes:n, session, projection:world.AgentWorkbenchSelectors.project({session}),
    workbench:{view:'chat',query:'',turn_id:null}, onAllTurns(){}, onSelectTurn(){},
  });
  const firstTurnButton = n.turnRail.children[0];
  world.AgentWorkbenchView.render({
    nodes:n, session, projection:world.AgentWorkbenchSelectors.project({session}),
    workbench:{view:'chat',query:'',turn_id:null}, onAllTurns(){}, onSelectTurn(){},
  });
  assert.equal(n.trajectoryList.children[0], sentinel); assert.equal(n.trajectoryList.replaceCount, 0);
  assert.equal(n.turnRail.children.length, 2); assert.equal(n.chatView.hidden, false); assert.equal(n.trajectoryView.hidden, true);
  assert.equal(n.turnRail.children[0], firstTurnButton); assert.equal(n.turnRail.replaceCount, 1);
});

test('trajectory uses the selected turn and renders only admitted message and tool facts', () => {
  const world = loadView(), n = nodes();
  const session = {
    run_id:'r2', active:true,
    messages:[
      {id:'m1',run_id:'r1',role:'user',text:'旧轮次',seq:1},
      {id:'m2',run_id:'r2',role:'assistant',text:'新轮次',seq:3},
    ],
    tools:[{id:'t1',run_id:'r2',name:'读取资料',message:'完成',state:'completed',seq:2}],
  };
  world.AgentWorkbenchView.render({
    nodes:n, session, projection:world.AgentWorkbenchSelectors.project({session}),
    workbench:{view:'trajectory',query:'',turn_id:'r2'}, labels:{completed:'已完成'},
    onAllTurns(){}, onSelectTurn(){},
  });
  const firstTrajectory = n.trajectoryList.children[0];
  world.AgentWorkbenchView.render({
    nodes:n, session, projection:world.AgentWorkbenchSelectors.project({session}),
    workbench:{view:'trajectory',query:'',turn_id:'r2'}, labels:{completed:'已完成'},
    onAllTurns(){}, onSelectTurn(){},
  });
  assert.equal(n.turnRail.children.length, 3); assert.equal(n.trajectoryList.children.length, 2);
  assert.equal(n.trajectoryList.children[0], firstTrajectory); assert.equal(n.trajectoryList.replaceCount, 1);
  assert.match(n.trajectoryList.textContent, /读取资料/); assert.match(n.trajectoryList.textContent, /新轮次/);
  assert.doesNotMatch(n.trajectoryList.textContent, /旧轮次/); assert.match(n.trajectoryMeta.textContent, /2 条真实事件/);
});

test('unchanged trajectory refreshes do not rescan or stringify large visible text', () => {
  const world = loadView(), n = nodes(); let reads = 0, text = 'x'.repeat(2 * 1024 * 1024);
  const message = {id:'large',run_id:'r1',role:'assistant',seq:1,content_revision:1};
  Object.defineProperty(message, 'text', {get(){reads++; return text;}, configurable:true});
  const session = {session_id:'large-session',projectionRevision:1,messages:[message],tools:[]};
  const input = {nodes:n,session,projection:world.AgentWorkbenchSelectors.project({session}),workbench:{view:'trajectory',query:'',turn_id:null},onAllTurns(){},onSelectTurn(){}};
  world.AgentWorkbenchView.render(input);const first=n.trajectoryList.children[0],firstReads=reads;
  for(let index=0;index<20;index++)world.AgentWorkbenchView.render(input);
  assert.equal(reads,firstReads);assert.equal(n.trajectoryList.children[0],first);assert.equal(n.trajectoryList.replaceCount,1);
  text='y'.repeat(2 * 1024 * 1024);message.content_revision++;session.projectionRevision++;world.AgentWorkbenchView.render(input);
  assert.ok(reads>firstReads);assert.notEqual(n.trajectoryList.children[0],first);assert.match(n.trajectoryList.textContent,/^Agent 回复#1y/);
});

test('tabs use roving focus with arrows and Home or End', () => {
  const world = loadView(), chat = new Element('button'), trajectory = new Element('button'), selected = [];
  world.AgentWorkbenchView.bindTabs({chatTab:chat, trajectoryTab:trajectory, onSelect:view => selected.push(view)});
  chat.emit('keydown', {key:'ArrowRight'}); trajectory.emit('keydown', {key:'Home'}); chat.emit('keydown', {key:'End'});
  assert.deepEqual(selected, ['trajectory', 'chat', 'trajectory']);
  assert.equal(chat.focusCount, 1); assert.equal(trajectory.focusCount, 2);
  const n = nodes(); n.chatTab = chat; n.trajectoryTab = trajectory;
  world.AgentWorkbenchView.render({nodes:n, session:null, projection:world.AgentWorkbenchSelectors.project({}), workbench:{view:'trajectory',query:'',turn_id:null}, onAllTurns(){}, onSelectTurn(){}});
  assert.equal(chat.tabIndex, -1); assert.equal(trajectory.tabIndex, 0);
  assert.match(n.trajectoryList.textContent, /App 已确认/);
  assert.doesNotMatch(n.trajectoryList.textContent, /Host|runtime|Queue|lineage|revision|cursor|运行实例/i);
});
