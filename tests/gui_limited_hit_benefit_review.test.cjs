const GUI_TEST_RUNTIME = require('./helpers/gui_runtime.cjs');
const Python = require('./helpers/python_command.cjs');
const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');
const root=path.join(__dirname,'..'),clone=value=>JSON.parse(JSON.stringify(value)),descendants=node=>[node,...node.children.flatMap(descendants)];
const helpers={module:{exports:{}},require,__dirname,console};
vm.runInNewContext(fs.readFileSync(path.join(__dirname,'gui_hit_parameters_review.test.cjs'),'utf8').split('\ntest(')[0]+'\nmodule.exports={setup,press,control,Element};',helpers);
const {setup:base,press,control,Element}=helpers.module.exports;
// Actual service capabilities and actual source-backed compose receipts. Only a
// fresh TemporaryDirectory database is opened; the project data path is unused.
const real=JSON.parse(Python.execFileSync(['-X','utf8','-c',`
import copy,json,sys,tempfile
from pathlib import Path
sys.path.insert(0,'tests')
from test_pipeline import fake_repo
from test_runtime_limited_hit_benefit import benefit_repo,benefit_db
from test_limited_hit_benefit_sources import benefit_plan
from genshin_app.equipment import compose_equipment_plan
from genshin_app.equipment.service import plan_capabilities
from genshin_app.equipment.limited_hit_benefit_schema import DEFAULT_SOURCE
with tempfile.TemporaryDirectory(prefix='genshin-ec18-gui-review-') as d:
 p=Path(d); repo=benefit_repo.__wrapped__(fake_repo.__wrapped__(p)); db=benefit_db.__wrapped__(p,repo)
 plan=benefit_plan(db); pending=compose_equipment_plan(plan,db)
 readyplan=copy.deepcopy(plan); readyplan['limited_hit_benefit_sources'][0].update(pending['limited_hit_benefit_compilations'][0]['required_confirmation'])
 ready=compose_equipment_plan(readyplan,db)
 empty=copy.deepcopy(plan);empty['limited_hit_benefit_sources']=[]
 removed=compose_equipment_plan(empty,db)
 draft=copy.deepcopy(plan);draft['limited_hit_benefit_sources']=[copy.deepcopy(DEFAULT_SOURCE)];draft['limited_hit_benefit_sources'][0]['id']='quill'
 unknown=compose_equipment_plan(draft,db)
 assert ready['limited_hit_benefit_compilations'][0]['status']=='ready'
 print(json.dumps({'caps':plan_capabilities(),'plan':plan,'pending':pending,'ready':ready,'removed':removed,'draft':draft,'unknown':unknown}))
`],{cwd:root,encoding:'utf8',maxBuffer:8*1024*1024}));
function setup(plan=real.plan){
 const e=base(),w=e.world;e.PL.state.caps=clone(real.caps);e.PL.state.plan=clone(plan);w.RT.state.template=e.PL.state.plan.manual_template;w.RT.syncRevision(true);
 for(const file of ['action-events','limited-hit-benefit'])GUI_TEST_RUNTIME.loadWebFile(w, path.join(root,`src/genshin_app/gui/web/${file}.js`));
 const calls=[];w.callApi=async(name,submitted)=>{calls.push(clone(submitted));const row=submitted.limited_hit_benefit_sources?.[0];
   return {code:0,data:clone(!row?real.removed:row.accepted_fingerprint?real.ready:row.action_event_profile_id?real.pending:real.unknown)};};
 return {...e,calls,plan:e.PL.state.plan};
}
test('independent GUI: actual service caps and unknown Python draft remain schema-compatible',async()=>{
 const e=setup(real.draft);e.world.LHB.edit(0);
 assert.equal(control(e.dialog(),'卡肉计时模型').value,'unknown');assert.equal(control(e.dialog(),'施放到生效（秒）').value,'');
 await press(e.dialog(),'应用有限次数收益');
 assert.deepEqual(clone(e.plan.limited_hit_benefit_sources),real.draft.limited_hit_benefit_sources);
 const schema=real.caps.limited_hit_benefit_contract.schema;
 assert.deepEqual(Object.keys(e.plan.limited_hit_benefit_sources[0]).sort(),schema.required.slice().sort());
 assert.equal(e.plan.limited_hit_benefit_sources[0].accepted_fingerprint,null);
});
test('independent GUI: actual pending-to-ready receipt accepts once and keeps only native fields',async()=>{
 const e=setup(),before=clone(e.plan);e.world.LHB.edit(0);
 await press(e.dialog(),'预览来源与生效顺序');await press(e.dialog(),'接受本次来源与场景');await press(e.dialog(),'应用有限次数收益');
 const expected=clone(before);expected.limited_hit_benefit_sources[0].accepted_fingerprint=real.ready.limited_hit_benefit_compilations[0].accepted_fingerprint;
 delete expected.resolution;delete expected.resolved_template;delete expected.revision;
 const actual=JSON.parse(e.PL.currentInputJson());delete actual.revision;
 assert.deepEqual(actual,expected);assert.equal(e.calls.length,3);
 assert.equal(e.plan.mechanics_model.rules.length,0);assert.equal(e.plan.limited_hit_benefit_sources[0].benefit_certificate,undefined);
});
test('independent GUI: actual Python links, certificate and rejected draft render without invented timing',()=>{
 const e=setup(),box=new Element();e.world.LHB.renderReceipt(box,real.pending,e.plan);
 assert.match(box.textContent,/当前攻击力 × 0\.8218079805374146/);assert.match(box.textContent,/独立 7 次/);assert.match(box.textContent,/持续 15 秒/);
 const rows=real.pending.limited_hit_benefit_compilations[0].parent_links;
 assert.ok(rows.length);for(const row of rows){assert.ok(box.textContent.includes(String(row.activation_tick/1000000)));assert.ok(box.textContent.includes(String(row.t)));}
 const unknown=new Element();e.world.LHB.renderReceipt(unknown,real.unknown,e.plan);assert.match(unknown.textContent,/待核实/);
});
test('independent GUI: real checks entry opens the editor and back leaves input unchanged',()=>{
 const e=setup(),before=e.PL.currentInputJson();e.world.RC.shown=true;e.world.EQ.showing=false;e.world.RC.render();
  const box=e.world.document.querySelector('#rotation-checks'),sourceRow=descendants(box).find(node=>String(node.className||'').includes('rc-definition-row')&&/草稿待确认|已接受/.test(node.textContent));
  assert.ok(sourceRow,'Missing limited-hit source command row');press(sourceRow,'⋯');
 assert.match(e.dialog().textContent,/有限次数收益来源/);e.field('施放到生效（秒）',.12);
 press(e.dialog(),'← 返回动作与能量');assert.equal(e.PL.currentInputJson(),before);
});
test('independent GUI: two concurrent editors cannot commit the earlier stale response',async()=>{
 const e=setup();e.world.LHB.edit(0);const first=e.dialog();let finish;
 e.world.callApi=()=>new Promise(resolve=>finish=resolve);const waiting=press(first,'应用有限次数收益');
 e.world.LHB.edit(0);const second=e.dialog();e.field('场景与初态依据','另一个未确认草稿');
 e.world.callApi=async()=>({code:0,data:clone(real.pending)});await press(second,'应用有限次数收益');const after=e.PL.currentInputJson();
 finish({code:0,data:clone(real.pending)});await waiting;assert.equal(e.PL.currentInputJson(),after);
 assert.equal(e.plan.limited_hit_benefit_sources[0].scenario.source,'另一个未确认草稿');
});
test('independent GUI: missing parent and character reference remain explicitly removable',async()=>{
 const e=setup();e.plan.action_event_profiles=[];e.plan.equipment_inputs={};e.world.LHB.edit(0);
 const before=clone(e.plan);await press(e.dialog(),'移除有限次数收益来源');assert.deepEqual(clone(e.plan.limited_hit_benefit_sources),[]);
 assert.deepEqual(clone(e.plan.rotation_model),before.rotation_model);assert.deepEqual(clone(e.plan.manual_template),before.manual_template);
});
