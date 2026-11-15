const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');
const helpers={module:{exports:{}},require,__dirname,console};
const harness=fs.readFileSync(path.join(__dirname,'gui_hit_parameters_review.test.cjs'),'utf8').split('\ntest(')[0];
vm.runInNewContext(harness+'\nmodule.exports={setup,all,press,control};',helpers);
const {setup,all,press,control}=helpers.module.exports;
const clone=value=>JSON.parse(JSON.stringify(value));
function fixture(){
  const e=setup(),plan=e.PL.state.plan,t=plan.manual_template;
  plan.equipment_inputs={};
  delete t.meta;delete t.enemy.resistance;
  for(const c of t.characters)delete c.stats.dmg_bonus;
  plan.attribute_review={mode:'manual_context',context_fingerprint:'reviewed-context',note:'已核对的属性事实'};
  plan.mechanics_review={mode:'manual_context',context_fingerprint:'reviewed-mechanics',note:'已核对的机制事实'};
  plan.resolution={state:'ready'};
  e.RT.state.templateJson=e.PL.currentInputJson();e.PL.state.savedJson=e.PL.currentInputJson();
  return {...e,t,panel:()=>e.world.document.querySelector('#team-panel')};
}
test('independent: repeated complete team renders preserve absent native defaults and confirmed context',()=>{
  const e=fixture(),before=e.PL.currentInputJson(),revision=e.RT.state.revision,epoch=e.PL.state.sourceEpoch;
  for(let i=0;i<4;i++){e.RT.renderTeamPanel();e.RT.renderAll();}
  assert.equal(e.PL.currentInputJson(),before);
  assert.equal(e.RT.state.revision,revision);assert.equal(e.PL.state.sourceEpoch,epoch);
  assert.equal(e.PL.state.plan.resolution.state,'ready');assert.equal(e.PL.dirty(),false);
  assert.equal(e.PL.state.plan.attribute_review.context_fingerprint,'reviewed-context');
  assert.equal(Object.hasOwn(e.t,'meta'),false);assert.equal(Object.hasOwn(e.t.enemy,'resistance'),false);
  assert.ok(e.t.characters.every(c=>!Object.hasOwn(c.stats,'dmg_bonus')));
});
test('independent: meta edit materializes only edited meta and makes calculated state pending',()=>{
  const e=fixture();e.RT.renderTeamPanel();
  const input=control(e.panel(),'名称');input.value='明确修改方案名称';input.emit('change');
  assert.equal(e.t.meta.name,'明确修改方案名称');
  assert.equal(Object.hasOwn(e.t.enemy,'resistance'),false);
  assert.ok(e.t.characters.every(c=>!Object.hasOwn(c.stats,'dmg_bonus')));
  assert.equal(e.PL.state.plan.resolution.state,'pending');assert.equal(e.PL.dirty(),true);
});
test('independent: damage dictionary edit materializes its owner without creating unrelated defaults',()=>{
  const e=fixture();e.RT.renderTeamPanel();press(e.panel(),'+ 添加加成');
  assert.deepEqual(clone(e.t.characters[0].stats.dmg_bonus),{all:0});
  assert.equal(Object.hasOwn(e.t,'meta'),false);assert.equal(Object.hasOwn(e.t.enemy,'resistance'),false);
  assert.equal(e.PL.state.plan.resolution.state,'pending');
});
test('independent: missing enemy dictionary is display-only until explicit resistance edit',()=>{
  const e=fixture();e.RT.renderTeamPanel();
  const resistanceRow=all(e.panel()).find(n=>n.children.some(c=>c.tagName==='select'&&c.value==='default'));
  assert.ok(resistanceRow);const input=resistanceRow.children.find(c=>c.tagName==='input');
  input.value='0.2';input.emit('change');
  assert.deepEqual(clone(e.t.enemy.resistance),{default:.2});
  assert.equal(Object.hasOwn(e.t,'meta'),false);
  assert.ok(e.t.characters.every(c=>!Object.hasOwn(c.stats,'dmg_bonus')));
  assert.equal(e.PL.state.plan.resolution.state,'pending');
});
