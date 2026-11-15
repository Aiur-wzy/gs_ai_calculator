const GUI_TEST_RUNTIME = require('./helpers/gui_runtime.cjs');
const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');
const helpers={module:{exports:{}},require,__dirname,console};
vm.runInNewContext(fs.readFileSync(path.join(__dirname,'gui_hit_parameters_review.test.cjs'),'utf8').split('\ntest(')[0]+'\nmodule.exports={setup,Element};',helpers);
const {setup:base,Element}=helpers.module.exports;
function setup(){const e=base();GUI_TEST_RUNTIME.loadWebFile(e.world, path.join(__dirname,'../src/genshin_app/gui/web/reaction-em.js'));return e;}
function record(){return {binding_id:'reaction-source',profile_id:'ordinary-reaction-flat-team-em-reference-v1',status:'ready',app_scope:{status:'ready'},
  rules:[{id:'r',owner:'a',name_zh:'反应触发全队精通',timing:{duration:8},effects:[{id:'v',value:120}]}],issues:[]};}
test('source shows provider flat EM and duration without burst identity editor',()=>{
  const e=setup(),box=new Element('div'),r=record();e.PL.state.report={upstream_compilations:[r]};
  e.world.REM.render(box,{binding_id:r.binding_id});
  assert.match(box.textContent,/全队精通 \+120，持续 8 秒/);assert.match(box.textContent,/无需选择爆发动作/);
  assert.doesNotMatch(box.textContent,/选择真实爆发技能|接受所列适配假设/);
});
test('source readiness alone is not final-model approval',()=>{
  const e=setup(),box=new Element('div'),r=record();r.app_scope.status='awaiting_final_model';
  e.world.REM.renderRecord(box,r,e.PL.state.plan.manual_template);
  assert.match(box.textContent,/仍待核实/);assert.doesNotMatch(box.textContent,/当前声明范围内就绪/);
});
test('actual occurrence uses Chinese reaction and separates reported vs extra window',()=>{
  const e=setup(),box=new Element('div'),r=record();const row={t:.3,source_window:0,owner:'a',reaction_type:'swirl',target_id:'enemy',parent_run_id:'0:h1',reaction_run_id:'0:h1:reaction:0'};
  e.world.REM.renderResult(box,{reaction_em_compilations:[r],reaction_em_execution:{activations:[row,{...row,source_window:1,parent_run_id:'1:h1',reaction_run_id:'1:h1:reaction:0'}]}},e.PL.state.plan.manual_template);
  assert.match(box.textContent,/2 次实际触发/);assert.match(box.textContent,/扩散/);assert.match(box.textContent,/完整输入/);assert.match(box.textContent,/附加检查 1/);
});
