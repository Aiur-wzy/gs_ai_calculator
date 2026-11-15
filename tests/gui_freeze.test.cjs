const GUI_TEST_RUNTIME = require('./helpers/gui_runtime.cjs');
const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');
const helpers={module:{exports:{}},require,__dirname,console};
const harness=fs.readFileSync(path.join(__dirname,'gui_hit_parameters_review.test.cjs'),'utf8').split('\ntest(')[0];
vm.runInNewContext(harness+'\nmodule.exports={setup,press,control,clone,Element};',helpers);
const {setup,press,control,Element}=helpers.module.exports;
const clone=value=>JSON.parse(JSON.stringify(value));

function reference(){const env=setup();env.model=env.PL.state.plan.mechanics_model.elemental_model={ruleset:'gauge-freeze-reference-v1',
  target:{status:'assumed'},swirl_damage:{icd:{kind:'unknown'},status:'pending',source:'待核实伤害档案'}};
  env.contract={contract_id:'reference:1',contract_fingerprint:'current-content',assumptions:['同父同元素聚合；未模拟碎冰'],sources:['fixed evidence']};
  env.MC.caps={elemental:{reference_contract:env.contract},schema:{properties:{}}};
  env.box=new Element();env.MC.renderReferenceModel(env.box);return env;}

test('reference acceptance requires a reason and never promotes evidence status',()=>{
  const env=reference();press(env.box,'接受所示参考约定');assert.equal(env.model.reference_acceptance,undefined);
  const input=control(env.box,'本方案采用参考模型的依据');input.value='采用此有限参考范围';input.emit('change');
  press(env.box,'接受所示参考约定');assert.deepEqual(clone(env.model.reference_acceptance),{
    contract_id:'reference:1',contract_fingerprint:'current-content',acknowledged:true,source:'采用此有限参考范围'});
  assert.equal(env.model.target.status,'assumed');assert.equal(env.model.swirl_damage.status,'pending');
  assert.deepEqual(clone(env.model.swirl_damage.icd),{kind:'unknown'});
});

test('stale reference card cannot confirm a replacement plan',()=>{
  const env=reference(),input=control(env.box,'本方案采用参考模型的依据');input.value='旧方案依据';input.emit('change');
  const previous=env.PL.state.plan,replacement=clone(previous);env.PL.replace(replacement,'new.json','new-sha',true);
  const old=JSON.stringify(previous),next=JSON.stringify(replacement);press(env.box,'接受所示参考约定');
  assert.equal(JSON.stringify(previous),old);assert.equal(JSON.stringify(replacement),next);
});

test('frozen state remains visible when ordinary aura is absent',()=>{
  const env=reference(),text=env.MC.elementalStateText({knowledge:'known',ordinary_aura:null,
    frozen:{remaining_gu:1.25},freeze_history_seconds:2});
  assert.match(text,/冻结量 1.25 GU/);assert.match(text,/冻结历史 2 秒/);assert.doesNotMatch(text,/未冻结/);
  assert.equal(env.MC.elementalStateText({knowledge:'unknown'}),'未知');
});

test('freeze model can bind DB swirl sequences without accepting reference assumptions implicitly',()=>{
  const env=reference();env.MC.editReactionDamageSource();assert.match(env.dialog().textContent,/数据库扩散伤害来源/);
  assert.equal(env.model.reference_acceptance,undefined);
});

test('contact choice preserves a referenced profile and uses contact-specific labels',()=>{
  const env=reference(),schema={oneOf:[
    {type:'object','x-label':'直接声明不会削冻或碎冰',required:['kind'],properties:{kind:{const:'none','x-const-label':'不会削冻或碎冰'}}},
    {type:'object','x-label':'引用接触档案',required:['kind','profile_id'],properties:{kind:{const:'profile'},profile_id:{type:'string'}}}
  ]};
  let value={kind:'profile',profile_id:'safe-contact'};const box=new Element();
  assert.equal(env.MC.branch(schema,value),1);env.MC.form(box,schema,value,v=>{value=v;},'接触方式',()=>{});
  const select=control(box,'接触方式 · 表达方式');assert.equal(select.value,'1');
  assert.match(box.textContent,/引用接触档案/);assert.doesNotMatch(box.textContent,/固定数值|属性比例/);
  assert.equal(control(box,'附着档案标识').value,'safe-contact');assert.deepEqual(value,{kind:'profile',profile_id:'safe-contact'});
  select.value='0';select.emit('change');assert.deepEqual(clone(value),{kind:'none'});
  const direct=new Element();env.MC.form(direct,schema,value,()=>{},'接触方式',()=>{});
  assert.match(direct.textContent,/不会削冻或碎冰/);assert.doesNotMatch(direct.textContent,/无附着/);
});

test('fresh frozen null state is displayed as a fixed value and creates no editable input',()=>{
  const env=reference(),box=new Element(),schema={type:'null','x-null-label':'初始无冻结量'};
  assert.equal(env.MC.default(schema),null);env.MC.form(box,schema,null,()=>assert.fail('null input changed'),'冻结量',()=>{});
  assert.equal(box.textContent,'冻结量：初始无冻结量');assert.equal(box.children.length,1);assert.equal(box.children[0].tagName,'p');
});

test('reaction package and expiry stay in effects; layers never create extra damage',()=>{
  const world={window:{}};vm.createContext(world);GUI_TEST_RUNTIME.loadWebFile(world, path.join(__dirname,'../src/genshin_app/gui/web/timeline-data.js'));
  const template={loop_duration:6,characters:[{id:'a',name:'风角色'}],hits:[],buffs:[]};
  const model={rules:[],hit_events:[],hit_patterns:[],initial_state:{rules:[]}};
  const plan={plan_version:3,manual_template:template,mechanics_model:model,rotation_model:{period:6,abilities:[],actions:[]}};
  const reactions=['hydro','cryo'].map(element=>({id:`h:reaction:swirl:${element}`,type:'swirl',reaction_element:element}));
  const result={hits:[{id:'h:reaction_damage:swirl:cryo',t:1,character:'a',skill:'扩散',hit_name:'冰扩散',expected:781.299,
    damage_system:'transformative',damage_type_ids:['damage.reaction']}],mechanics_trace:[{type:'reaction_damage',event_id:'h:reaction_damage:swirl:cryo',t:1,
      character:'a',reaction_element:'cryo',action_id:'cast',parent_event_id:'h',cycle:0}],elemental:{ruleset:'gauge-freeze-reference-v1',trace:[
      {type:'elemental_application',event_id:'0:h',t:1,character:'a',profile_id:'wind',element:'anemo',reactions,
        package_id:'0:h:package',layer_consumptions:[{layer:'hydro',consumed_gu:.3},{layer:'frozen',consumed_gu:.2}]},
      {type:'elemental_application',event_id:'0:ice',t:2,character:'a',profile_id:'ice',element:'cryo',reactions:[{id:'ice:reaction:freeze',type:'freeze'}]},
      {type:'elemental_state_transition',event_id:'state:1',target_id:'enemy',t:5,reason:'frozen_expired',before:{frozen:{remaining_gu:.1}},after:{frozen:null}}
    ]}};
  const args={template,plan,result,executionModel:model,current:true},before=JSON.stringify(args);
  const out=clone(world.window.TD.build(args));assert.equal(JSON.stringify(args),before);
  assert.equal(out.views.effects.events.filter(row=>row.kind==='reaction').length,3);
  const damage=out.views.damage.events.filter(row=>row.kind==='damage');assert.equal(damage.length,1);assert.equal(damage[0].value,781.299);
  assert.equal(out.views.effects.events.filter(row=>row.label==='元素底 / 冻结状态变化').length,1);
  assert.equal(out.views.actions.events.length,0);
});
