const GUI_TEST_RUNTIME = require('./helpers/gui_runtime.cjs');
const test = require('node:test');
const assert = require('node:assert/strict');
const path = require('node:path');
const vm = require('node:vm');

class Element {
  constructor(tag = 'div') { this.tagName = tag; this.children = []; this.attributes = {}; this.className = ''; }
  append(...nodes) { this.children.push(...nodes); }
  appendChild(node) { this.append(node); return node; }
  set textContent(value) { this.children = []; this.text = String(value); }
  get textContent() { return (this.text || '') + this.children.map(node => node.textContent).join(''); }
  setAttribute(key, value) { this.attributes[key] = String(value); }
}

const all = node => [node, ...node.children.flatMap(all)];

function setup() {
  const world = {console, Math, Date};
  world.window = world;
  world.document = {createElement: tag => new Element(tag)};
  world.UI = {
    clone: value => JSON.parse(JSON.stringify(value)),
    details(label, value) {
      const node = new Element('details');
      const summary = new Element('summary'); summary.textContent = label;
      const raw = new Element('pre'); raw.textContent = JSON.stringify(value, null, 2);
      node.append(summary, raw); return node;
    },
  };
  vm.createContext(world);
  GUI_TEST_RUNTIME.loadWebFile(world, path.join(__dirname, '../src/genshin_app/gui/web/result.js'));
  return world;
}

function partialReceipt() {
  return {contract: 'action-materialization-receipt-v1', status: 'partial', calculation_class: 'diagnostic',
    summary: {expected_components: 4, materialized_components: 2, expected_occurrences: 8, materialized_occurrences: 5},
    actions: [{action_id: 'odetta.special-e', ability_id: 'skill.e2', profile_id: 'profile.e2',
      coverage_status: 'partial', expected_components: ['loop.1', 'loop.2', 'loop.3', 'finale'],
      materialized_components: ['loop.1', 'finale'],
      expected_occurrences: ['hit.1', 'hit.2', 'hit.3', 'hit.4'],
      materialized_occurrences: ['hit.1', 'hit.4'],
      unresolved_components: [{component_id: 'loop.2'}, {component_id: 'loop.3'}],
      unresolved_occurrences: [{occurrence_id: 'hit.2', component_id: 'loop.2'}],
      spawned_channels: []}]};
}

test('diagnostic receipt renders a compact Chinese warning with action-level gaps and raw details', () => {
  const world = setup(), receipt = partialReceipt(), before = JSON.stringify(receipt);
  const card = world.RS.renderActionMaterializationReceipt(receipt);
  assert.match(card.className, /incomplete/u);
  assert.equal(card.attributes.role, 'alert');
  assert.match(card.textContent, /动作计算不完整/u);
  assert.match(card.textContent, /动作分量 2\/4；发生实例 5\/8/u);
  assert.match(card.textContent, /来源动作 odetta\.special-e/u);
  assert.match(card.textContent, /分量 2\/4；实例 2\/4/u);
  assert.match(card.textContent, /未解决分量 loop\.2、loop\.3/u);
  assert.match(card.textContent, /未解决实例 hit\.2/u);
  assert.ok(all(card).some(node => node.tagName === 'details' && /动作物化原始回执/u.test(node.textContent)));
  assert.equal(JSON.stringify(receipt), before);
});

test('complete and unknown receipts stay distinct and never claim game-mechanism completeness', () => {
  const world = setup();
  const complete = world.RS.renderActionMaterializationReceipt({contract:'action-materialization-receipt-v1', status:'complete',
    actions:[{action_id:'cast', coverage_status:'complete', expected_components:['one'], materialized_components:['one'],
      expected_occurrences:['one@1'], materialized_occurrences:['one@1'], unresolved_components:[], unresolved_occurrences:[]}]});
  assert.match(complete.className, /complete/u); assert.equal(complete.attributes.role, 'status');
  assert.match(complete.textContent, /动作物化完成/u);
  assert.match(complete.textContent, /不证明数据库已经覆盖游戏内全部机制/u);
  assert.doesNotMatch(complete.textContent, /动作计算不完整/u);
  const unknown = world.RS.renderActionMaterializationReceipt({contract:'action-materialization-receipt-v1', status:'unknown'});
  assert.match(unknown.className, /unknown/u); assert.match(unknown.textContent, /动作物化状态未知/u);
  assert.match(unknown.textContent, /不能据此认为动作计算完整/u);
});

test('receipt lookup accepts formal result field, audit nesting, and backend report persistence source only', () => {
  const world = setup(), receipt = partialReceipt();
  assert.equal(world.RS.actionMaterializationReceipt({action_materialization_receipt:receipt}), receipt);
  assert.equal(world.RS.actionMaterializationReceipt({action_automation_audit:{materialization_receipt:receipt}}), receipt);
  assert.equal(world.RS.actionMaterializationReceipt({result:{action_automation_audit:{materialization_receipt:receipt}}}), receipt);
  assert.equal(world.RS.actionMaterializationReceipt({report:{action_materialization_receipt:receipt}}), receipt);
  assert.equal(world.RS.actionMaterializationReceipt({unrelated:{action_materialization_receipt:receipt}}), null);
});

test('legacy single-row diagnostic remains visible and long unresolved lists stay compact', () => {
  const world = setup(), unresolved = Array.from({length: 12}, (_, index) => `loop.${index + 1}`);
  const card = world.RS.renderActionMaterializationReceipt({status:'complete', calculation_class:'diagnostic_only',
    source_action:'special-e', expected_components:12, materialized_components:0,
    unresolved_components:unresolved});
  assert.match(card.className, /incomplete/u);
  assert.match(card.textContent, /来源动作 special-e：分量 0\/12/u);
  assert.match(card.textContent, /loop\.1、loop\.2/u);
  assert.match(card.textContent, /另有 4 项，见原始回执/u);
});

test('contradictory counts fail closed and unknown action coverage cannot render as complete', () => {
  const world = setup();
  const mismatch = world.RS.renderActionMaterializationReceipt({contract:'action-materialization-receipt-v1',
    status:'complete', actions:[{action_id:'cast', coverage_status:'complete',
      expected_components:['a', 'b'], materialized_components:['a'],
      expected_occurrences:['a:1'], materialized_occurrences:['a:1'],
      unresolved_components:[], unresolved_occurrences:[]}]});
  assert.match(mismatch.className, /incomplete/u);
  const unknown = world.RS.renderActionMaterializationReceipt({contract:'action-materialization-receipt-v1',
    status:'complete', actions:[{action_id:'cast', coverage_status:'unknown',
      expected_components:[], materialized_components:[], expected_occurrences:[], materialized_occurrences:[],
      unresolved_components:[], unresolved_occurrences:[]}]});
  assert.match(unknown.className, /unknown/u);
  assert.doesNotMatch(unknown.textContent, /动作物化完成/u);
});

test('top-level completion label is downgraded for incomplete or unknown action coverage', () => {
  const world = setup();
  assert.equal(world.RS.materializationResultLabel(partialReceipt()),
    '计算完成（诊断结果：动作计算不完整）');
  assert.equal(world.RS.materializationResultLabel({contract:'action-materialization-receipt-v1',
    status:'unknown', calculation_class:'diagnostic', actions:[]}),
    '计算完成（诊断结果：动作覆盖未知）');
  assert.equal(world.RS.materializationResultLabel({contract:'action-materialization-receipt-v1',
    status:'complete', calculation_class:'normal', actions:[]}), '计算完成');
  assert.equal(world.RS.materializationResultLabel(null), '计算完成');
});
