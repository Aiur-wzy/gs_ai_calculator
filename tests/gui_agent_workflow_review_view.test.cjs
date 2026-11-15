'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const R = require('./helpers/gui_runtime.cjs');

class Element {
  constructor(tag = 'div') {
    this.tagName = tag; this.children = []; this.listeners = {}; this.attributes = {}; this.text = '';
    const classes = new Set(); this.classList = {add:value => classes.add(value), contains:value => classes.has(value)};
  }
  append(...nodes) { for (const node of nodes) { node.parent = this; this.children.push(node); } }
  replaceChildren(...nodes) { this.children = []; this.text = ''; this.append(...nodes); }
  set textContent(value) { this.children = []; this.text = String(value ?? ''); }
  get textContent() { return this.text + this.children.map(node => node.textContent).join(''); }
  setAttribute(name, value) { this.attributes[name] = String(value); }
  addEventListener(name, fn) { (this.listeners[name] ||= []).push(fn); }
  emit(name, event = {}) { return (this.listeners[name] || []).map(fn => fn({preventDefault(){}, ...event})).at(-1); }
  showModal() { this.open = true; }
  close() { this.open = false; }
  remove() { if (this.parent) this.parent.children = this.parent.children.filter(node => node !== this); }
}
const all = node => [node, ...node.children.flatMap(all)];
const tick = () => new Promise(resolve => setImmediate(resolve));

function setup(apiImpl = async () => ({code:1, message:'unexpected'})) {
  const body = new Element('body'), document = {body, createElement:tag => new Element(tag)};
  const world = {console, document}; world.window = world; vm.createContext(world);
  R.loadWebScripts(world, ['agent-domain-transactions.js', 'agent-workflow-review-view.js']);
  const state = {viewEpoch:3, busy:null, selected:'session-one'}, nodes = {kind:{value:'whiteboard'}, workflow:{value:'staged'}};
  const candidate = {revision:2, receipt_fingerprint:'receipt-two', ready:true, can_accept:true,
    application_kind:'whiteboard_draft', has_calculation:false, diff_total:1, diff:[]};
  const session = {session_id:'session-one', epoch:5, kind:'whiteboard', workflow:'build', candidate,
    base:{document:{kind:'genshin-whiteboard', version_line:'release', team:[{id:'a', name:'角色甲'}], actions:[]}}};
  let selected = session, active = null, applyCalls = 0; const calls = [], notices = [];
  const api = async (...args) => { calls.push(args); return apiImpl(...args); };
  const transactions = world.AgentDomainTransactions.createController();
  const loadCandidate = async owner => { const response = await api('agent_candidate', owner.session_id); if (!response?.code) { const incoming = response.data?.candidate || response.data; if (incoming && typeof incoming === 'object') owner.candidate = JSON.parse(JSON.stringify(incoming)); return {ok:true,candidate:JSON.parse(JSON.stringify(owner.candidate))}; } return {ok:false,response}; };
  const readCandidate = async (owner, current, resource, pointer, offset, limit) => { const response = await api('agent_candidate_read', owner.session_id, resource, pointer, offset, limit); return {ok:!response?.code,response}; };
  const view = world.AgentWorkflowReviewView.create({
    document, ui:world.UI, state, nodes, selected:() => selected, readModel:value => value,
    active:() => active, refresh:() => {}, notice:(...args) => notices.push(args), errorText:(value, fallback) => value?.message || fallback,
    clone:value => JSON.parse(JSON.stringify(value)), text:value => typeof value === 'string' ? value : JSON.stringify(value, null, 2),
    defaultWorkflow:kind => kind === 'whiteboard' ? 'staged' : 'calculate', checkpointFrozen:() => false,
    hasUnsettledAppAction:() => false, appliedCandidate:() => false,
    reviewCandidate:(value, operation) => transactions.reviewCandidate(value, operation),
    loadCandidate, readCandidate,
    onApply:async () => { applyCalls++; return true; }, catalogName:() => null, actionTypeName:() => null,
  });
  return {world, body, state, nodes, session, candidate, view, calls, notices, transactions,
    select:value => { selected = value; }, activate:value => { active = value; }, applyCalls:() => applyCalls};
}

test('candidate value viewer uses the shared dialog lifecycle and reports a busy replacement safely',async()=>{
  const e=setup(),blocker=e.world.AppDialogKernel.create({title:'另一项操作正在提交',busy:true});assert.ok(blocker);
  assert.equal(await e.view.exploreCandidate(e.session,e.candidate,'candidate'),false);
  assert.equal(e.world.AppDialogKernel.getActive(),blocker);assert.match(String(e.notices[0]?.[0]),/先完成或关闭/);blocker.finish('test-cleanup');
});

test('workflow stages keep saveability and diagnostics independent, and diagnostics cannot apply', () => {
  const e = setup(), receipt = {status:'incomplete', counts:{expected_nodes:4, bound_nodes:2, resolved_bindings:2},
    coverage:{missing_node_ids:['loop.2', 'loop.3']}, issues:[{message:'仍有节点未绑定'}]};
  const staged = {...e.candidate, workflow_stage:'event_topology', stage_status:'partial', saveable:true,
    event_topology_coverage_receipt:{status:'topology_ready'}, event_topology_execution_binding_receipt:receipt};
  const value = e.view.workflowState(e.session, staged);
  assert.equal(value.stage, 'event_topology'); assert.equal(value.saveable, true); assert.equal(value.diagnostic, false);
  assert.equal(e.view.canApply(e.session, staged), true);
  const tracker = e.view.renderStageTracker(value);
  assert.match(tracker.textContent, /事件拓扑.*仍有缺项.*草稿保存：允许/);
  assert.match(tracker.textContent, /执行绑定：待完善 · 已绑定 2\/4 · 已解析 2/);
  const diagnostic = {...staged, workflow_stage:'strict_calculation', calculation_acceptance:'diagnostic_only'};
  assert.equal(e.view.workflowState(e.session, diagnostic).diagnostic, true);
  assert.equal(e.view.canApply(e.session, diagnostic), false);
});

test('event-topology disclosure hides damage fields and nested values', () => {
  const e = setup(), candidate = {...e.candidate, workflow_stage:'event_topology', saveable:true,
    summary:{event_topology:{node_count:2}, total_damage:999},
    diff:[{path:'/event_topology/nodes/0', before:null, after:{node_id:'hit-one', kind:'damage', parameter_slots:[{value:777}]}, reason:'命中'},
      {path:'/result/calculation_totals/total_damage', before:0, after:999}]};
  const box = new Element(); e.view.renderDiff(box, candidate, e.session);
  assert.match(box.textContent, /事件节点 hit-one/); assert.match(box.textContent, /已隐藏 1 项伤害数值差异/);
  assert.doesNotMatch(box.textContent, /777|999|查看完整候选输入/);
  assert.equal(e.world.AgentWorkflowReviewView.damageDisclosurePath({path:'/result/dps'}), true);
});

test('candidate pagination ignores late pages after a view epoch change and rejects cross-resource references', async () => {
  let finishPage;
  const e = setup(async (name, sessionId, resource, pointer, offset) => {
    assert.equal(name, 'agent_candidate_read'); assert.equal(sessionId, 'session-one');
    if (offset === 0) return {code:0, data:{kind:'array', total:2, next_offset:1, items:[
      {key:0, value:{reference:{resource:'baseline', pointer:'/secret'}, length:20}},
    ]}};
    return new Promise(resolve => { finishPage = resolve; });
  });
  const dialog = await e.view.exploreCandidate(e.session, e.candidate, 'candidate', '/hits');
  assert.match(dialog.textContent, /越过当前候选资源边界/);
  assert.equal(all(dialog).some(node => node.tagName === 'button' && node.textContent === '展开此项'), false);
  const next = all(dialog).find(node => node.tagName === 'button' && node.textContent === '下一页');
  const pending = next.emit('click'); await tick(); e.state.viewEpoch++;
  finishPage({code:0, data:{kind:'array', total:2, next_offset:null, items:[{key:1, value:'late-secret'}]}});
  await pending; assert.doesNotMatch(dialog.textContent, /late-secret/);
  const callCount = e.calls.length;
  assert.equal(await e.view.exploreCandidate(e.session, e.candidate, 'unknown-resource', ''), false);
  assert.equal(e.calls.length, callCount);
});

test('duplicate review requests share the transaction gate and create one read and one dialog', async () => {
  let finish;
  const e = setup(name => {
    assert.equal(name, 'agent_candidate');
    return new Promise(resolve => { finish = resolve; });
  });
  const first = e.view.review(), second = e.view.review(); await tick();
  assert.equal(e.calls.length, 1); assert.equal(e.transactions.gate.activeCount(), 1);
  finish({code:0, data:e.candidate}); await Promise.all([first, second]);
  assert.equal(e.body.children.filter(node => node.tagName === 'dialog').length, 1);
  assert.equal(e.transactions.gate.activeCount(), 0); assert.equal(e.applyCalls(), 0);
});

test('review view is read-only at the Host boundary and does not own candidate application or recovery', () => {
  const source = fs.readFileSync(path.join(__dirname, '../src/genshin_app/gui/web/agent-workflow-review-view.js'), 'utf8');
  assert.doesNotMatch(source, /\bapi\s*\(|agent_candidate|session\.candidate\s*=/);
  assert.doesNotMatch(source, /agent_accept|agent_candidate_applied|agent_recover_candidate_application|agent_abort_candidate_application/);
});
