const GUI_TEST_RUNTIME = require('./helpers/gui_runtime.cjs');
const Python = require('./helpers/python_command.cjs');
// Independent EC19 result/contract integration. Real Python App responses use
// one TemporaryDirectory fixture database; the formal project database is unused.
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const root = path.join(__dirname, '..');
const clone = value => JSON.parse(JSON.stringify(value));
const helpers = {module: {exports: {}}, require, __dirname, console};
vm.runInNewContext(fs.readFileSync(path.join(__dirname, 'gui_hit_parameters_review.test.cjs'), 'utf8')
  .split('\ntest(')[0] + '\nmodule.exports={setup,all};', helpers);
const {setup: base, all} = helpers.module.exports;
const real = JSON.parse(Python.execFileSync(['-X', 'utf8', '-c', `
import json,sys,tempfile
from pathlib import Path
sys.path.insert(0,'tests')
from test_pipeline import fake_repo
from test_runtime_limited_hit_benefit import benefit_repo,benefit_db
from test_limited_hit_benefit_sources import benefit_plan,confirm_benefit
from test_mechanics_execution import EVIDENCE
from genshin_app.equipment import run_plan_calc
from genshin_app.equipment.service import plan_capabilities
with tempfile.TemporaryDirectory(prefix='genshin-ec19-gui-review-') as folder:
 p=Path(folder); repo=benefit_repo.__wrapped__(fake_repo.__wrapped__(p)); db=benefit_db.__wrapped__(p,repo)
 plan=benefit_plan(db)
 plan['action_event_overrides']=[{'action_id':'cast','landed':False,'order':20,**EVIDENCE}]
 zero=run_plan_calc(confirm_benefit(plan,db),db)
 ordinary_plan=benefit_plan(db)
 ordinary_plan['manual_template']['characters'][0]['stats'].update(atk=0,base_atk=0)
 ordinary=run_plan_calc(confirm_benefit(ordinary_plan,db),db)
 assert zero['result']['hits']==[] and len(ordinary['result']['hits'])==1
 assert zero['result']['summary']['total_damage']==ordinary['result']['summary']['total_damage']==0
 print(json.dumps({'caps':plan_capabilities(),'zero':zero,'ordinary':ordinary},ensure_ascii=False))
`], {cwd: root, encoding: 'utf8', maxBuffer: 12 * 1024 * 1024}));

function setup(calculated = real.zero) {
  const e = base(), w = e.world;
  // Restore the actual result renderer; the shared editor fixture normally stubs it.
  GUI_TEST_RUNTIME.loadWebFile(w, path.join(root, 'src/genshin_app/gui/web/result.js'));
  w.RT.renderTeamPanel = () => {};
  w.EQ.summaryCard = null;
  w.MC.renderTrace = () => w.UI.el('div');
  w.PL.state.caps = clone(real.caps);
  w.PL.replace(clone(calculated.plan), 'independent-zero.plan.json', 'fixture-sha', true);
  e.RS = w.RS;
  e.calls = [];
  w.callApi = async (name, ...args) => {
    e.calls.push([name, ...clone(args)]);
    return name === 'run_plan_calc' ? {code: 0, data: clone(calculated)} : {code: 0, data: {path: 'out.xlsx'}};
  };
  w.setStatus = message => { e.status = message; };
  e.body = () => w.document.querySelector('#result-body');
  e.button = format => w.document.querySelector('#btn-exp-' + format);
  return e;
}

test('independent native GUI uses actual caps and actual known-zero response with real cycle labels', async () => {
  const contract = real.caps.damage_reporting_contract;
  assert.equal(contract.contract, 'verified-empty-damage-v1');
  assert.equal(contract.exports.csv, false);
  assert.equal(contract.exports.json, true);
  assert.equal(contract.exports.formula_xlsx, true);
  const e = setup();
  await e.RS.runCalc();
  assert.equal(e.calls[0][0], 'run_plan_calc');
  assert.equal(e.RS.isCurrent(), true);
  assert.deepEqual(Array.from(all(e.body()).filter(n => n.className === 'stat-value'), n => n.textContent), ['0', '0']);
  assert.match(e.body().textContent, /1 个已声明接触全部未命中/);
  assert.match(e.body().textContent, /尚未证明周期稳定/);
  assert.match(e.body().textContent, /循环是否成立另行检查/);
  assert.match(e.body().textContent, /逐伤害事件明细（0）/);
  assert.match(e.status, /总伤 0，每秒伤害 0/);
  assert.equal(e.RS.state.result.result_receipt.damage_reporting.contract, contract.contract);
});

test('independent native export routing blocks CSV before API and leaves JSON/XLSX enabled', async () => {
  const e = setup(); await e.RS.runCalc(); e.calls.length = 0;
  assert.equal(e.button('csv').disabled, true);
  assert.match(e.button('csv').title, /证明/);
  assert.equal(e.button('json').disabled, false);
  assert.equal(e.button('xlsx').disabled, false);
  assert.equal(e.button('formula').disabled, false);
  await e.RS.exportResult('csv');
  assert.equal(e.calls.length, 0);
  assert.match(e.status, /CSV 不支持/);
  await e.RS.exportResult('json'); await e.RS.exportResult('xlsx');
  assert.deepEqual(e.calls.map(c => [c[0], c[2]]), [['export_plan_result', 'json'], ['export_plan_result', 'xlsx']]);
  for (const call of e.calls) {
    assert.equal(call[1].action_event_overrides[0].landed, false);
    assert.equal(call[1].manual_template.hits.length, 1);
  }
});

test('independent real zero-valued hit stays a normal result and clears all-miss export restrictions', async () => {
  const e = setup(); await e.RS.runCalc();
  e.world.PL.replace(clone(real.ordinary.plan), 'ordinary.plan.json', 'ordinary-sha', true);
  e.world.callApi = async () => ({code: 0, data: clone(real.ordinary)});
  await e.RS.runCalc();
  assert.match(e.body().textContent, /逐伤害事件明细（1）/);
  assert.doesNotMatch(e.body().textContent, /伤害报告证明|全部未命中/);
  assert.deepEqual(Array.from(all(e.body()).filter(n => n.className === 'stat-value'), n => n.textContent), ['0', '0']);
  assert.equal(e.button('csv').disabled, false);
  assert.equal(e.button('csv').title, '');
  assert.equal(e.RS.state.result.hits[0].expected, 0);
});

test('independent native edit while zero response is pending cannot install stale proof', async () => {
  const e = setup(); let resolve;
  e.world.callApi = () => new Promise(done => { resolve = done; });
  const waiting = e.RS.runCalc();
  assert.equal(e.RS.state.pending, true);
  e.world.PL.state.plan.action_event_overrides[0].landed = true;
  resolve({code: 0, data: clone(real.zero)});
  await waiting;
  assert.equal(e.RS.state.result, null);
  assert.equal(e.RS.state.pending, false);
  for (const format of ['json', 'xlsx', 'csv', 'formula']) assert.equal(e.button(format).disabled, true);
  assert.doesNotMatch(e.body().textContent, /全部未命中/);
});
