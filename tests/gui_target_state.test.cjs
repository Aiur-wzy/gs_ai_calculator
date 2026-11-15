const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');
const Python = require('./helpers/python_command.cjs');
const helpers={module:{exports:{}},require,__dirname,console};
const harness=fs.readFileSync(path.join(__dirname,'gui_hit_parameters_review.test.cjs'),'utf8').split('\ntest(')[0];
vm.runInNewContext(harness+'\nmodule.exports={setup,press,control,clone,Element};',helpers);
const {setup,press,control,Element}=helpers.module.exports;
const clone=value=>JSON.parse(JSON.stringify(value));
const all=n=>[n,...n.children.flatMap(all)];
const caps=JSON.parse(Python.execFileSync(['-X','utf8','-c',
  'import json; from genshin_engine.runtime_target_state_compiler import target_state_compiler_caps; from genshin_engine.mechanics_schema import mechanics_capabilities; print(json.dumps([target_state_compiler_caps(),mechanics_capabilities()]))'],
  {cwd:path.join(__dirname,'..'),encoding:'utf8'}));

function targetSetup(draft=true){
  const env=setup(),{world,PL}=env;
  PL.state.caps.upstream_target_state_contract=clone(caps[0]);env.MC.caps=clone(caps[1]);
  if(draft)PL.state.draft=clone(PL.state.plan);
  const original=world.EQ.plan(),binding={binding_id:'target-source',provider_character_id:'a',
    owner:{kind:'artifact_set',set_id:14001,required_count:4,affix_id:2140011},source_refs:[],source_fingerprint:null,
    coverage:{state:'unreviewed',reviewed_by:null,source_fingerprint:null,note:null},parts:[]};
  original.effect_bindings.push(binding);let stamp='one';const sent=[];
  const response=plan=>{const b=plan.effect_bindings.find(b=>b.binding_id===binding.binding_id),s=b.upstream_recipe?.scenario;
    const fp=s?`${stamp}:${s.status}:${s.source}`:null;
    return {code:0,data:{state:'pending',missing_inputs:[{message:'完整轴仍有独立待办'}],plan:{...clone(plan),mechanics_model:{injected:'must not save'}},
      upstream_compilations:s?[{binding_id:binding.binding_id,status:s.accepted_assumptions_fingerprint===fp&&s.status!=='pending'?'ready':'pending',
        projection_status:s.accepted_assumptions_fingerprint===fp&&s.status!=='pending'?'ready':'pending',whole_source_status:'partial',
        required_confirmation:{accepted_assumptions_fingerprint:fp},
        issues:s.status==='pending'?[{code:'scenario_evidence_pending',message:'用途尚待核实'}]:s.accepted_assumptions_fingerprint===fp?[]:[{code:'semantic_profile_acceptance_required',message:'需确认来源参考假设'}],
        branches:[{name_zh:'经典暴率条件',status:'supported_in_scope'},{name_zh:'月曜及特殊反应分支',status:'excluded_by_scope',reason:'声明范围外不执行'}],
        fixed_effect_sources:[],parts:[],coverage_certificate:{source_complete:false}}]:[]}};};
  world.pywebview.api.compose_equipment_plan=async p=>{sent.push(clone(p));return response(p);};
  const open=()=>world.EF.editTargetSource(binding),field=(label,value)=>{const node=control(env.dialog(),label);assert.ok(node,label);node.value=value;node.emit('change');};
  const ready=()=>{open();field('场景与面板政策核实状态','assumed');field('用途与条件暴率未含面板的依据','两件已含、四件条件暴率未含；接受普通目标参考');};
  return {...env,original,binding,sent,response,open,field,ready,changeSource:()=>stamp='two'};
}

test('target source confirmation is fresh and native-only in equipment draft',async()=>{
  const e=targetSetup(true),native=clone(e.original.mechanics_model);e.ready();
  await press(e.dialog(),'确认所示来源与参考假设');assert.equal(e.sent.length,0);
  await press(e.dialog(),'读取并预览目标条件来源');assert.match(e.dialog().textContent,/仅部分支持/);assert.match(e.dialog().textContent,/月曜及特殊反应分支/);
  await press(e.dialog(),'确认所示来源与参考假设');assert.equal(e.binding.upstream_recipe,undefined);
  await press(e.dialog(),'应用目标条件来源');assert.equal(e.binding.upstream_recipe.scenario.status,'assumed');
  assert.ok(e.binding.upstream_recipe.scenario.accepted_assumptions_fingerprint);assert.deepEqual(clone(e.binding.upstream_recipe.ability_refs),[]);
  assert.deepEqual(clone(e.original.mechanics_model),native);assert.deepEqual(clone(e.binding.parts),[]);
  assert.equal(e.PL.state.plan.effect_bindings.some(b=>b.binding_id==='target-source'),false);
});

test('confirmation never promotes pending scenario evidence',async()=>{
  const e=targetSetup();e.open();e.field('用途与条件暴率未含面板的依据','仍待核实的声明');
  await press(e.dialog(),'读取并预览目标条件来源');await press(e.dialog(),'确认所示来源与参考假设');await press(e.dialog(),'应用目标条件来源');
  assert.equal(e.binding.upstream_recipe.scenario.status,'pending');assert.ok(e.binding.upstream_recipe.scenario.accepted_assumptions_fingerprint);
});

test('source change invalidates shown acceptance before application',async()=>{
  const e=targetSetup();e.ready();await press(e.dialog(),'读取并预览目标条件来源');e.changeSource();
  await press(e.dialog(),'确认所示来源与参考假设');assert.equal(e.binding.upstream_recipe,undefined);assert.match(e.dialog().textContent,/来源或假设已变化/);
  await press(e.dialog(),'确认所示来源与参考假设');await press(e.dialog(),'应用目标条件来源');
  assert.match(e.binding.upstream_recipe.scenario.accepted_assumptions_fingerprint,/^two:/);
});

for(const mutation of ['input','live','epoch','path','sha','replacement','close'])test(`target source stale ${mutation} response cannot commit`,async()=>{
  const e=targetSetup(true);e.ready();let finish,submitted;
  e.world.pywebview.api.compose_equipment_plan=p=>{submitted=clone(p);return new Promise(r=>finish=r);};
  const pending=press(e.dialog(),'应用目标条件来源');
  if(mutation==='input')e.original.manual_template.meta={name:'other-draft'};
  if(mutation==='live')e.PL.state.plan.manual_template.meta={name:'other-live'};
  if(mutation==='epoch')e.PL.state.sourceEpoch++;
  if(mutation==='path')e.RT.state.path='changed.json';
  if(mutation==='sha')e.PL.state.fileSha='changed-sha';
  if(mutation==='replacement')e.PL.state.draft=clone(e.original);
  if(mutation==='close')press(e.dialog(),'放弃并关闭');
  finish(e.response(submitted));await pending;assert.equal(e.binding.upstream_recipe,undefined);
});

test('newer local editing defeats out-of-order source responses',async()=>{
  const e=targetSetup();e.ready();const calls=[];e.world.pywebview.api.compose_equipment_plan=p=>new Promise(resolve=>calls.push({p:clone(p),resolve}));
  const first=press(e.dialog(),'读取并预览目标条件来源');e.field('用途与条件暴率未含面板的依据','新版依据');
  const second=press(e.dialog(),'读取并预览目标条件来源');calls[1].resolve(e.response(calls[1].p));await second;
  calls[0].resolve(e.response(calls[0].p));await first;assert.match(control(e.dialog(),'用途与条件暴率未含面板的依据').value,/新版/);
  e.world.pywebview.api.compose_equipment_plan=async p=>e.response(p);await press(e.dialog(),'确认所示来源与参考假设');await press(e.dialog(),'应用目标条件来源');
  assert.match(e.binding.upstream_recipe.scenario.accepted_assumptions_fingerprint,/新版依据/);
});

test('unknown source branch cannot be confirmed as covered',async()=>{
  const e=targetSetup();e.ready();e.world.pywebview.api.compose_equipment_plan=async p=>{const r=e.response(p);r.data.upstream_compilations[0].issues=[{code:'unknown_field',message:'未解释新增字段'}];return r;};
  await press(e.dialog(),'读取并预览目标条件来源');await press(e.dialog(),'确认所示来源与参考假设');
  assert.equal(e.binding.upstream_recipe,undefined);assert.match(e.dialog().textContent,/未解释新增字段/);
});

test('manual fixed source part and tuning use fixed identities without buff alias',()=>{
  const e=targetSetup(),s={id:'fixed-id',name_zh:'固定冰条件',owner:'a',source_id:'manual:part',status:'assumed',source:'测试',effects:[{id:'ice',benefit_type_id:'benefit.crit_rate.all'}]};
  e.original.mechanics_model.fixed_effect_sources=[s];e.original.rotation_tuning={buff_hit_matches:[{id:'m',effect_ref:{buff_id:'',effect_id:''},mode:'adjust',include_hit_ids:[],exclude_hit_ids:[],reason:'',state:'pending',basis_fingerprint:null}]};
  let box=new Element();e.world.EF.renderTuning(box);const selection=control(box,'增益');selection.value='fixed-id';selection.emit('change');
  const ref=e.original.rotation_tuning.buff_hit_matches[0].effect_ref;assert.equal(ref.fixed_source_id,'fixed-id');assert.equal(ref.buff_id,undefined);
  box=new Element();e.world.EF.renderTuning(box);assert.match(box.textContent,/不能绕过未知目标状态/);
  const part={part_id:'part',label:'来源',state:'pending',rule:{kind:'fixed_effect_source',fixed_source_id:''},delivery:{kind:'fixed_effect_source'},
    conditions:[],dependencies:{mode:'source_only',context_fingerprint:null,note:'依据'},reason:null};
  e.binding.parts=[part];box=new Element();e.world.EF.renderPart(box,e.binding,part,0);
  const fixed=control(box,'关联固定条件来源');fixed.value='fixed-id';fixed.emit('change');
  assert.equal(s.source_id,'target-source:part');assert.equal(part.rule.fixed_source_id,'fixed-id');assert.equal(all(box).find(n=>n.attributes['aria-label']==='交付方式'),undefined);
});

function fixedSourceBranch(e,profile,scope){
  const schema=e.MC.caps.schema.properties.fixed_effect_sources.items;
  assert.ok(Array.isArray(schema.oneOf),'current fixed sources explicitly publish versioned alternatives');
  const matches=schema.oneOf.filter(branch=>branch.properties?.target_state_profile?.const===profile&&branch.properties?.damage_scope?.const===scope);
  assert.equal(matches.length,1,`expected exactly one semantic fixed source branch for ${profile}/${scope}`);
  return matches[0];
}

test('target predicate form preserves any branch and provides Chinese grouping labels',()=>{
  const e=targetSetup(),source=fixedSourceBranch(e,'freeze-reference-target-v1','classic_hits_only'),schema=source.properties.effects.items.properties.target_predicate;
  let value={kind:'any',children:[{kind:'target_aura_in',elements:['cryo']}]};const box=new Element();
  assert.equal(e.MC.branch(schema,value),2);e.MC.form(box,schema,value,v=>value=v,'命中前目标条件',()=>{});
  assert.match(box.textContent,/组合条件/);assert.match(box.textContent,/至少一项子条件成立/);assert.doesNotMatch(box.textContent,/不限制前后台/);
});

test('general target source keeps its declared damage systems and real expanded predicate form',()=>{
  const e=targetSetup(),source=fixedSourceBranch(e,'target-state-observation-v2','declared_damage_systems');
  assert.deepEqual(clone(source.properties.damage_systems.items.enum),['classic','transformative','lunar','stellar']);
  const schema=source.properties.effects.items.properties.target_predicate;
  let value={kind:'any',children:[{kind:'target_aura_in',elements:['electro','hydro']}]};const before=clone(value),box=new Element();
  const combination=schema.oneOf.findIndex(branch=>branch.properties?.kind?.enum?.includes('any'));
  assert.ok(combination>=0);assert.equal(e.MC.branch(schema,value),combination);
  e.MC.form(box,schema,value,v=>value=v,'命中前目标条件',()=>{});
  assert.match(box.textContent,/组合条件/);assert.match(box.textContent,/至少一项子条件成立/);assert.match(box.textContent,/雷/);
  assert.deepEqual(clone(value),before,'rendering must not replace the declared general predicate');
});

test('condition details are read only and distinguish automatic false from manual include',()=>{
  const e=targetSetup(),box=new Element(),result={fixed_effect_sources:[{id:'s',name_zh:'冻结条件来源'}],target_condition_trace:[{
    t:1,cycle:0,fixed_source_id:'s',event_id:'h',automatic_value:false,override:'include',applied:true,reason:'agent_included',
    target_sample:{status:'known',state:{ordinary_aura:null,frozen:null,freeze_history_seconds:0}}}]};
  e.MC.renderTargetConditions(box,result,e.original.manual_template);assert.match(box.textContent,/不成立/);assert.match(box.textContent,/手工计入/);
  assert.match(box.textContent,/只读/);assert.match(box.textContent,/冻结条件来源/);assert.equal(all(box).filter(n=>['input','select','textarea'].includes(n.tagName)).length,0);
});

test('target source entry is structural four-piece scope, never an artifact ID lookup',()=>{
  const e=targetSetup();e.original.effect_bindings=[e.binding];e.original.manual_effect_dependencies=[];e.binding.owner.set_id=999999;
  let box=new Element();e.world.EF.render(box);e.world.EF.bindingMenuItems(e.binding).find(item=>item.label==='选择经典命中目标条件投影').run();
  assert.equal(control(e.dialog(),'场景与面板政策核实状态').value,'pending');assert.equal(e.binding.upstream_recipe,undefined);
  assert.equal(e.sent.length,0);press(e.dialog(),'放弃并关闭');e.binding.owner.required_count=2;
  box=new Element();e.world.EF.render(box);assert.equal(e.world.EF.bindingMenuItems(e.binding).some(item=>item.label==='选择经典命中目标条件投影'),false);
});

test('cached compilation cannot confirm a newly opened target source',async()=>{
  const e=targetSetup();e.PL.state.report={upstream_compilations:[{binding_id:e.binding.binding_id,status:'ready',required_confirmation:{accepted_assumptions_fingerprint:'stale'}}]};
  e.ready();await press(e.dialog(),'确认所示来源与参考假设');assert.equal(e.sent.length,0);assert.equal(e.binding.upstream_recipe,undefined);
});

test('failed removal is atomic and successful removal creates no manual fixed effect',async()=>{
  const e=targetSetup();e.ready();await press(e.dialog(),'应用目标条件来源');const original=clone(e.binding),model=clone(e.original.mechanics_model);
  e.open();e.world.pywebview.api.compose_equipment_plan=async()=>({code:1,message:'本次校验失败'});
  await press(e.dialog(),'解除自动来源并返回手工处理');assert.deepEqual(clone(e.binding),original);
  e.world.pywebview.api.compose_equipment_plan=async p=>e.response(p);await press(e.dialog(),'解除自动来源并返回手工处理');
  assert.equal(e.binding.upstream_recipe,undefined);assert.deepEqual(clone(e.binding.parts),[]);assert.deepEqual(clone(e.original.mechanics_model),model);
});

test('generic fixed-source form defaults to pending evidence and requires explicit editing',()=>{
  const e=targetSetup(),item=e.MC.caps.schema.properties.fixed_effect_sources.items,value=e.MC.default(item);
  assert.equal(value.status,'pending');assert.deepEqual(clone(value.effects),[]);assert.equal(value.availability,'fixed');
});
