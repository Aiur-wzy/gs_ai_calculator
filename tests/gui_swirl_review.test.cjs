const GUI_TEST_RUNTIME = require('./helpers/gui_runtime.cjs');
const test=require('node:test'), assert=require('node:assert/strict'), fs=require('node:fs'), path=require('node:path'), vm=require('node:vm');
// Reuse only this reviewer's DOM harness, stopping before its test registrations.
const helpers={module:{exports:{}},require,__dirname,console};
const harness=fs.readFileSync(path.join(__dirname,'gui_hit_parameters_review.test.cjs'),'utf8').split('\ntest(')[0];
vm.runInNewContext(harness+'\nmodule.exports={setup,press,control,clone};',helpers);
const {setup,press}=helpers.module.exports;
const clone=value=>JSON.parse(JSON.stringify(value));

function environment(ruleset='gauge-swirl-v2'){const env=setup(),model=env.PL.state.plan.mechanics_model.elemental_model={schema_version:1,ruleset,
  target:{id:'enemy',kind:'ordinary',knowledge:'known',initial_aura:null,status:'assumed',source:'independent scope'},profiles:[],
  swirl_damage:{icd:{kind:'unknown'},status:'pending',source:'scope deliberately unconfirmed'}};
  env.RT.renderAll();const response=plan=>({code:0,data:{state:'pending',plan:clone(plan),missing_inputs:[],reaction_damage_compilations:[{
    reaction_type:'swirl',status:plan.reaction_damage_sources?.[0]?.expected_profile_fingerprint==='profile'?'ready':'pending',required_confirmation:{expected_snapshot_id:'snapshot',expected_profile_fingerprint:'profile'},
    profiles:(ruleset==='gauge-swirl-dynamic-v3'?['hydro','cryo','pyro']:['hydro','cryo']).map(element=>({selection:{element},snapshot:{snapshot_id:'snapshot'},projection:{damage_sequence:[1,1,0],reset_cycle_seconds_rounded:.5},execution:{sequence_status:'ready',ability_execution:'pending'}}))}]}});
  env.world.pywebview.api.compose_equipment_plan=async plan=>response(plan);
  const open=()=>{env.MC.editReactionDamageSource();env.field('普通扩散适用范围与来源复核依据','independent scope confirmation');};
  return {...env,model,response,open};}

test('dynamic swirl UI confirms the complete three-element source set',async()=>{
  const env=environment('gauge-swirl-dynamic-v3');env.open();
  await press(env.dialog(),'读取并预览扩散来源');await press(env.dialog(),'确认所示扩散来源');await press(env.dialog(),'应用扩散来源绑定');
  assert.equal(env.PL.state.plan.reaction_damage_sources[0].expected_profile_fingerprint,'profile');
  assert.equal(env.model.swirl_damage.status,'pending');
});

for(const failure of ['absent','duplicate','sequence','timer','snapshot'])test(`dynamic swirl UI refuses invalid third source: ${failure}`,async()=>{
  const env=environment('gauge-swirl-dynamic-v3');env.open();env.world.pywebview.api.compose_equipment_plan=async plan=>{
    const r=env.response(plan),p=r.data.reaction_damage_compilations[0].profiles;
    if(failure==='absent')p.pop();
    if(failure==='duplicate')p[2].selection.element='cryo';
    if(failure==='sequence')p[2].projection.damage_sequence=[1,0];
    if(failure==='timer')p[2].projection.reset_cycle_seconds_rounded=1;
    if(failure==='snapshot')p[2].snapshot.snapshot_id='other';return r;};
  await press(env.dialog(),'读取并预览扩散来源');await press(env.dialog(),'确认所示扩散来源');await press(env.dialog(),'应用扩散来源绑定');
  assert.equal(env.PL.state.plan.reaction_damage_sources[0].expected_profile_fingerprint,null);
});

for(const conflict of ['sequence','timer','same-element'])test(`independent: unusable two-source ${conflict} cannot acquire confirmation`,async()=>{
  const env=environment();env.open();env.world.pywebview.api.compose_equipment_plan=async plan=>{const result=env.response(plan),record=result.data.reaction_damage_compilations[0];
    if(conflict==='sequence')record.profiles[1].projection.damage_sequence=[1,0,0];
    if(conflict==='timer')record.profiles[1].projection.reset_cycle_seconds_rounded=2.5;
    if(conflict==='same-element')record.profiles[1].selection.element='hydro';
    result.data.missing_inputs=[{code:'reaction_damage_group_conflict',message:'Cannot assemble this source pair'}];return result;};
  await press(env.dialog(),'读取并预览扩散来源');await press(env.dialog(),'确认所示扩散来源');await press(env.dialog(),'应用扩散来源绑定');
  assert.equal(env.PL.state.plan.reaction_damage_sources[0].expected_profile_fingerprint,null);
  assert.equal(env.PL.state.plan.mechanics_model.elemental_model.swirl_damage.status,'pending');
});

test('independent: source confirmation never upgrades pending ordinary-mode evidence',async()=>{
  const env=environment();env.open();await press(env.dialog(),'读取并预览扩散来源');await press(env.dialog(),'确认所示扩散来源');
  await press(env.dialog(),'应用扩散来源绑定');assert.equal(env.model.status,undefined);
  assert.equal(env.PL.state.plan.reaction_damage_sources[0].expected_profile_fingerprint,'profile');
  assert.equal(env.model.swirl_damage.status,'pending');assert.equal(env.model.swirl_damage.source,'scope deliberately unconfirmed');
  assert.deepEqual(clone(env.model.swirl_damage.icd),{kind:'unknown'});
});

test('independent: failed removal and late removal after plan replacement preserve native inputs',async()=>{
  const env=environment();env.open();await press(env.dialog(),'应用扩散来源绑定');env.MC.editReactionDamageSource();
  const before=env.PL.currentInputJson();env.world.pywebview.api.compose_equipment_plan=async()=>({code:1,message:'controlled failure'});
  await press(env.dialog(),'移除扩散来源绑定');assert.equal(env.PL.currentInputJson(),before);
  let finish,submitted;env.world.pywebview.api.compose_equipment_plan=plan=>{submitted=clone(plan);return new Promise(resolve=>finish=resolve);};
  const request=press(env.dialog(),'移除扩散来源绑定'),old=env.PL.state.plan,oldJson=JSON.stringify(old);
  const newer=clone(old);newer.manual_template.meta.name='different';env.PL.replace(newer,'new.json','new-sha',true);const newJson=JSON.stringify(newer);
  finish(env.response(submitted));await request;assert.equal(JSON.stringify(old),oldJson);assert.equal(JSON.stringify(newer),newJson);
});

function timeline(){const world={window:{}};vm.createContext(world);GUI_TEST_RUNTIME.loadWebFile(world, path.join(__dirname,'../src/genshin_app/gui/web/timeline-data.js'));
  const template={loop_duration:2,characters:[{id:'a',name:'风角色'}],hits:[{id:'h0',t:.2,character:'a',element:'anemo',skill:'战技',damage_type_ids:['damage.elemental_skill']}],buffs:[]};
  const model={rules:[],hit_events:[{hit_id:'h0',action_id:'cast'}],hit_patterns:[],initial_state:{rules:[]}};
  const plan={plan_version:3,manual_template:template,mechanics_model:model,rotation_model:{period:2,abilities:[{id:'e',character:'a',action_type_id:'action.elemental_skill'}],actions:[{id:'cast',ability_id:'e',start:0,occupation_end:.1,repeat:{mode:'once'}}]}};
  const result={hits:[{id:'h0',t:.2,character:'a',skill:'战技',expected:45,damage_type_ids:['damage.elemental_skill']},
    {id:'h0:reaction_damage:0',t:.2,character:'a',skill:'扩散',hit_name:'冰扩散',expected:781.299,damage_type_ids:['damage.reaction'],damage_system:'transformative'}],
    mechanics_trace:[{type:'hit',event_id:'h0',t:.2,cycle:0,status:'executed'},
      {type:'reaction_damage',event_id:'h0:reaction_damage:0',t:.2,cycle:0,status:'executed',reaction_element:'cryo',action_id:'cast',reaction_id:'h0:reaction:0',parent_event_id:'h0'}],
    elemental:{trace:[{type:'elemental_application',event_id:'0:h0',t:.2,element:'anemo',character:'a',profile_id:'wind',reaction:{type:'swirl'},before:{aura:{element:'cryo',remaining_gu:.8}},after:{aura:{element:'cryo',remaining_gu:.3}}},
      {type:'reaction_damage_attempt',event_id:'h0:reaction_damage:0',t:.2,allowed:true,reaction_element:'cryo',character:'a'},
      {type:'reaction_damage_attempt',event_id:'suppressed:reaction_damage:0',t:.3,allowed:false,reaction_element:'cryo',character:'a'}]}};
  return {build:current=>clone(world.window.TD.build({template,plan,result,executionModel:model,current}))};}

test('independent: derived swirl damage retains element and causal action without double counting',()=>{
  const {build}=timeline(),value=build(true),damage=value.views.damage.events.filter(e=>e.kind==='damage');
  assert.equal(damage.length,2);assert.equal(damage.reduce((sum,row)=>sum+row.value,0),826.299);
  const swirl=damage.find(e=>e.detail.hit_id==='h0:reaction_damage:0');assert.equal(swirl.element,'cryo');assert.equal(swirl.detail.action_id,'cast');
  assert.equal(value.views.effects.events.filter(e=>e.label==='扩散出伤受冷却限制').length,1);
  assert.ok(!damage.some(e=>e.detail.hit_id==='suppressed:reaction_damage:0'));
  const stale=build(false);assert.ok(!JSON.stringify(stale).includes('781.299'));assert.equal(stale.views.damage.events.filter(e=>e.kind==='damage').length,1);
});
