// Actual result view, with declared backend result fixtures; Python tests cover
// the complete source-backed execution and proof validation separately.
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const helpers = {module: {exports: {}}, require, __dirname, console};
vm.runInNewContext(fs.readFileSync(path.join(__dirname, 'gui_frontend.test.cjs'), 'utf8').split('\ntest(')[0]
  + '\nmodule.exports={setup,Element,descendants};', helpers);
const {setup, Element, descendants} = helpers.module.exports;

function zero() {
  return {summary: {total_damage: 0, dps: 0, by_character: {}, by_skill: {}}, hits: [], buffs_coverage: [], assumptions: [],
    rotation_check: {status: 'unproven'}, damage_reporting: {contract: 'verified-empty-damage-v1', status: 'verified_zero',
      reason: 'all_declared_contacts_missed', scope: {origin: 0, start_tick: 0, end_tick: 20000000, tick_unit: 'microsecond'},
      planned_contacts: 2, missed_contacts: 2, reported_damage_events: 0, contacts: []}};
}
async function show(result = zero()) {
  const e = setup({renderResults: true});
  e.world.RC = {render: () => {}, renderResult: value => { const n = new Element(); n.textContent = '循环检查：' + value.status; return n; }};
  e.calls = [];
  e.picks = [];
  e.world.WB = {pickUserFile: async request => {
    e.picks.push(request);
    return {cancelled: false, path: `fixture/${request.suggested_name}`};
  }};
  e.world.callApi = async (name, ...args) => {e.calls.push([name, ...args]);
    return name === 'run_calc' ? {code: 0, data: {result}} : {code: 0, data: {path: 'export.xlsx'}};};
  e.world.setStatus = message => { e.status = message; };
  await e.RS.runCalc();
  return e;
}

function expandReportSection(body, name) {
  const section = descendants(body).find(n => n.classList.contains(`result-section-${name}`));
  assert.ok(section);
  section.open = true;
  section.emit('toggle');
}

test('verified empty result renders numeric zero, complete window proof and distinct unproven check', async () => {
  const e = await show();
  const body = e.nodes.get('#result-body'), text = body.textContent;
  assert.match(text, /伤害报告证明/);
  assert.match(text, /2 个已声明接触全部未命中/);
  assert.match(text, /窗口 \[0, 20\) 秒/);
  assert.match(text, /循环与机制/);
  assert.match(text, /伤害明细（0）/);
  assert.doesNotMatch(text, /循环检查：unproven|引擎逐伤害事件明细/);
  expandReportSection(body, 'mechanics');
  expandReportSection(body, 'damage');
  assert.match(body.textContent, /循环检查：unproven/);
  assert.match(body.textContent, /引擎逐伤害事件明细（0）/);
  const values = descendants(body).filter(n => n.className === 'stat-value');
  assert.deepEqual(Array.from(values, n => n.textContent), ['0', '0']);
  assert.match(e.status, /总伤 0，每秒伤害 0/);
  assert.equal(e.RS.state.result.hits.length, 0);
});

test('CSV disabled and direct click blocked, JSON and Excel remain usable', async () => {
  const e = await show();
  assert.equal(e.nodes.get('#btn-exp-csv').disabled, true);
  assert.match(e.nodes.get('#btn-exp-csv').title, /证明/);
  assert.equal(e.nodes.get('#btn-exp-json').disabled, false);
  assert.equal(e.nodes.get('#btn-exp-xlsx').disabled, false);
  e.calls.length = 0;
  await e.RS.exportResult('csv');
  assert.equal(e.calls.length, 0); assert.equal(e.picks.length, 0); assert.match(e.status, /CSV 不支持/);
  await e.RS.exportResult('json'); await e.RS.exportResult('xlsx');
  assert.deepEqual(e.picks.map(pick => [pick.action, pick.document_kind]), [['export', 'report_json'], ['export', 'report_xlsx']]);
  assert.deepEqual(e.calls.map(c => [c[0], c[2], c[4]]), [
    ['export_result', 'json', 'fixture/方案 A.json'], ['export_result', 'xlsx', 'fixture/方案 A.xlsx'],
  ]);
});

test('zero scalar ordinary result does not fabricate all-miss proof or disable CSV', async () => {
  const result = zero(); delete result.damage_reporting;
  const e = await show(result);
  assert.doesNotMatch(e.nodes.get('#result-body').textContent, /伤害报告证明|全部未命中/);
  assert.equal(e.nodes.get('#btn-exp-csv').disabled, false);
});

test('new ordinary result resets zero-specific tooltip and preserves older rendering', async () => {
  const e = await show(), result = zero(); delete result.damage_reporting;
  result.summary.total_damage = 200; result.summary.dps = 10;
  e.world.callApi = async () => ({code: 0, data: {result}});
  await e.RS.runCalc();
  assert.equal(e.nodes.get('#btn-exp-csv').disabled, false);
  assert.equal(e.nodes.get('#btn-exp-csv').title, '');
  assert.doesNotMatch(e.nodes.get('#result-body').textContent, /伤害报告证明/);
  assert.match(e.status, /总伤 200，每秒伤害 10/);
});

test('editing after verified zero marks old display stale and prevents every export', async () => {
  const e = await show();
  e.RT.state.revision += 1; e.RS.invalidate();
  assert.match(e.nodes.get('#result-body').textContent, /结果已失效/);
  for (const format of ['json', 'xlsx', 'csv']) assert.equal(e.nodes.get('#btn-exp-' + format).disabled, true);
  e.calls.length = 0; await e.RS.exportResult('xlsx');
  assert.equal(e.calls.length, 0);
});

test('unknown proof contract does not claim verified execution', async () => {
  const result = zero(); result.damage_reporting.contract = 'future-unknown';
  const e = await show(result);
  assert.doesNotMatch(e.nodes.get('#result-body').textContent, /伤害报告证明|全部未命中/);
});
