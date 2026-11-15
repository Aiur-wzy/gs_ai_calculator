const GUI_TEST_RUNTIME = require('./helpers/gui_runtime.cjs');
const Python = require('./helpers/python_command.cjs');
const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),vm=require('node:vm'),crypto=require('node:crypto');
const helpers={module:{exports:{}},require,__dirname,console};
vm.runInNewContext(fs.readFileSync(path.join(__dirname,'gui_hit_parameters_review.test.cjs'),'utf8').split('\ntest(')[0]+'\nmodule.exports={setup,press,control,Element};',helpers);
const {setup:base,press,control,Element}=helpers.module.exports,clone=value=>JSON.parse(JSON.stringify(value));
const all=n=>[n,...n.children.flatMap(all)];
const caps=JSON.parse(Python.execFileSync(['-X','utf8','-c',
  'import json; from genshin_engine.runtime_reaction_benefit_compiler import reaction_benefit_compiler_caps; print(json.dumps(reaction_benefit_compiler_caps()))'],
  {cwd:path.join(__dirname,'..'),encoding:'utf8'}));
function setup(){
  const e=base(),w=e.world;e.PL.state.caps.upstream_reaction_benefit_contract=clone(caps);
  e.PL.state.draft=clone(e.PL.state.plan);const p=w.EQ.plan();p.mechanics_model.elemental_model={target:{id:'enemy'}};
  const binding={binding_id:'reaction-source',provider_character_id:'a',owner:{kind:'artifact_set',set_id:15002,required_count:4,affix_id:2150021},
    source_refs:[],source_fingerprint:null,coverage:{state:'unreviewed',reviewed_by:null,source_fingerprint:null,note:null},parts:[]};p.effect_bindings.push(binding);
  GUI_TEST_RUNTIME.loadWebFile(w, path.join(__dirname,'../src/genshin_app/gui/web/reaction-benefits.js'));
  const calls=[];let stamp=1;
  const response=plan=>{const b=plan.effect_bindings.find(x=>x.binding_id===binding.binding_id),scenario=b.upstream_recipe?.scenario;
    const s=scenario&&clone(scenario);if(s)delete s.accepted_assumptions_fingerprint;
    const fp=s?crypto.createHash('sha256').update(JSON.stringify([stamp,s])).digest('hex'):null;
    const ready=s&&s.status!=='pending'&&scenario.accepted_assumptions_fingerprint===fp;
    return {code:0,data:{state:'pending',missing_inputs:[{message:'其它轴输入仍待核实'}],plan:{...clone(plan),mechanics_model:{injected:true}},
      upstream_compilations:s?[{binding_id:binding.binding_id,profile_id:caps.profile_id,status:ready?'ready':'pending',projection_status:ready?'ready':'pending',
        whole_source_status:'partial',required_confirmation:{accepted_assumptions_fingerprint:fp},
        issues:s.status==='pending'?[{code:'scenario_evidence_pending',message:'场景仍待核实'}]:ready?[]:[{code:'semantic_profile_acceptance_required',message:'待确认当前参考'}],
        rules:[{name_zh:'冰扩散减抗',timing:{duration:10},effects:[{value:.4}]}],static_parts:[{rule:{value:.6}}],
        branches:[{name_zh:'星烁及传播',status:'excluded_by_scope'}],parts:[],coverage_certificate:{source_complete:false}}]:[]}};};
  w.callApi=async(name,plan)=>{calls.push({name,plan:clone(plan)});return response(plan);};
  const open=()=>w.RB.edit(binding),ready=()=>{open();e.field('对应的元素目标','enemy');e.field('原目标是否满足来源范围','within_source_range');
    e.field('窗口开始前的该源减抗','none');e.field('输入命中的原子顺序','declared_atomic_hit_order');e.field('场景核实状态','assumed');
    e.field('范围、初态与事件顺序依据','工程范围已明确，开始前无减抗，原子事件顺序明确');};
  return {...e,p,binding,calls,response,open,ready,changeSource:()=>stamp++};
}
async function applied(e){e.ready();await press(e.dialog(),'读取并预览反应收益来源');await press(e.dialog(),'确认所示来源与参考假设');await press(e.dialog(),'应用反应收益草稿');}

test('reaction editor uses the shared dialog lifecycle and refuses a busy replacement safely',()=>{
  const e=setup(),before=clone(e.binding),blocker=e.world.AppDialogKernel.create({title:'另一项操作正在提交',busy:true});
  assert.ok(blocker);e.open();assert.equal(e.world.AppDialogKernel.getActive(),blocker);
  assert.deepEqual(clone(e.binding),before);blocker.finish('test-cleanup');
});

test('reaction editor commits one native recipe, never generated rules or server plan',async()=>{
  const e=setup(),before=clone(e.p.mechanics_model);e.ready();await press(e.dialog(),'确认所示来源与参考假设');assert.equal(e.calls.length,0);
  await press(e.dialog(),'读取并预览反应收益来源');assert.match(e.dialog().textContent,/60.00%/);assert.match(e.dialog().textContent,/40.00%/);assert.match(e.dialog().textContent,/10 秒/);
  await press(e.dialog(),'确认所示来源与参考假设');assert.equal(e.binding.upstream_recipe,undefined);
  await press(e.dialog(),'应用反应收益草稿');assert.equal(e.binding.upstream_recipe.scenario.status,'assumed');assert.ok(e.binding.upstream_recipe.scenario.accepted_assumptions_fingerprint);
  assert.deepEqual(clone(e.binding.parts),[]);assert.deepEqual(clone(e.p.mechanics_model),before);assert.equal(e.PL.state.plan.effect_bindings.some(x=>x.binding_id===e.binding.binding_id),false);
});
test('unknown initial range and event ordering remain explicit pending drafts',async()=>{
  const e=setup();e.open();await press(e.dialog(),'读取并预览反应收益来源');await press(e.dialog(),'确认所示来源与参考假设');await press(e.dialog(),'应用反应收益草稿');
  const s=e.binding.upstream_recipe.scenario;assert.equal(s.status,'pending');assert.equal(s.initial_source_effects,'unknown');assert.equal(s.origin_target_range,'unknown');assert.equal(s.event_order_policy,'unknown');assert.equal(s.target_id,null);
});
test('changed source during confirmation cannot be accepted from an old preview',async()=>{
  const e=setup();e.ready();await press(e.dialog(),'读取并预览反应收益来源');e.changeSource();await press(e.dialog(),'确认所示来源与参考假设');assert.match(e.dialog().textContent,/来源或场景已变化/);assert.equal(e.binding.upstream_recipe,undefined);
  await press(e.dialog(),'确认所示来源与参考假设');await press(e.dialog(),'应用反应收益草稿');assert.ok(e.binding.upstream_recipe.scenario.accepted_assumptions_fingerprint);
});
for(const mutation of ['input','live','epoch','path','sha','replacement','close'])test(`reaction source stale ${mutation} reply cannot commit`,async()=>{
  const e=setup();e.ready();let finish,submitted;e.world.callApi=(_,p)=>{submitted=clone(p);return new Promise(r=>finish=r);};const pending=press(e.dialog(),'应用反应收益草稿');
  if(mutation==='input')e.p.manual_template.meta={name:'other-draft'};if(mutation==='live')e.PL.state.plan.manual_template.meta={name:'other-live'};
  if(mutation==='epoch')e.PL.state.sourceEpoch++;if(mutation==='path')e.RT.state.path='changed.json';if(mutation==='sha')e.PL.state.fileSha='changed';
  if(mutation==='replacement')e.PL.state.draft=clone(e.p);if(mutation==='close')press(e.dialog(),'放弃并关闭');finish(e.response(submitted));await pending;assert.equal(e.binding.upstream_recipe,undefined);
});
test('out-of-order source replies retain only the newer scene',async()=>{
  const e=setup();e.ready();const calls=[];e.world.callApi=(_,p)=>new Promise(resolve=>calls.push({p:clone(p),resolve}));
  const first=press(e.dialog(),'读取并预览反应收益来源');e.field('范围、初态与事件顺序依据','新版依据');const second=press(e.dialog(),'读取并预览反应收益来源');
  calls[1].resolve(e.response(calls[1].p));await second;calls[0].resolve(e.response(calls[0].p));await first;
  e.world.callApi=async(_,p)=>e.response(p);await press(e.dialog(),'确认所示来源与参考假设');await press(e.dialog(),'应用反应收益草稿');assert.equal(e.binding.upstream_recipe.scenario.source,'新版依据');
});
test('unknown raw structure cannot be confirmed by GUI',async()=>{
  const e=setup();e.ready();e.world.callApi=async(_,p)=>{const r=e.response(p);r.data.upstream_compilations[0].issues=[{code:'unsupported_control',message:'新控制未解释'}];return r;};
  await press(e.dialog(),'读取并预览反应收益来源');await press(e.dialog(),'确认所示来源与参考假设');assert.equal(e.binding.upstream_recipe,undefined);assert.match(e.dialog().textContent,/新控制未解释/);
});
test('cached record never confirms a newly opened source',async()=>{
  const e=setup();e.PL.state.report={upstream_compilations:[{profile_id:caps.profile_id,binding_id:e.binding.binding_id,required_confirmation:{accepted_assumptions_fingerprint:'cached'}}]};
  e.ready();await press(e.dialog(),'确认所示来源与参考假设');assert.equal(e.calls.length,0);
});
test('failed removal is atomic and successful removal invents no manual effects',async()=>{
  const e=setup();await applied(e);const old=clone(e.binding),model=clone(e.p.mechanics_model);e.open();e.world.callApi=async()=>({code:1,message:'拒绝当前修改'});
  await press(e.dialog(),'解除自动来源并返回手工处理');assert.deepEqual(clone(e.binding),old);
  e.world.callApi=async(_,p)=>e.response(p);await press(e.dialog(),'解除自动来源并返回手工处理');assert.equal(e.binding.upstream_recipe,undefined);assert.deepEqual(clone(e.p.mechanics_model),model);
});
test('structural four-piece entry does not hardcode a set identity',()=>{
  const e=setup();e.p.effect_bindings=[e.binding];e.p.manual_effect_dependencies=[];e.binding.owner.set_id=99999;let box=new Element();e.world.EF.render(box);
  e.world.EF.bindingMenuItems(e.binding).find(item=>item.label==='选择普通扩散反应收益来源').run();assert.equal(control(e.dialog(),'场景核实状态').value,'pending');
  press(e.dialog(),'放弃并关闭');e.binding.owner.required_count=2;box=new Element();e.world.EF.render(box);
  assert.equal(e.world.EF.bindingMenuItems(e.binding).some(item=>item.label==='选择普通扩散反应收益来源'),false);
});
test('missing or wrong-profile receipt blocks applying a draft',async()=>{
  const e=setup();e.ready();e.world.callApi=async(_,p)=>{const r=e.response(p);r.data.upstream_compilations[0].profile_id='other';return r;};await press(e.dialog(),'应用反应收益草稿');assert.equal(e.binding.upstream_recipe,undefined);assert.match(e.dialog().textContent,/缺少可核验/);
});
test('server payload mutation cannot change the locally proposed recipe',async()=>{
  const e=setup();e.ready();const expected=control(e.dialog(),'范围、初态与事件顺序依据').value;e.world.callApi=async(_,p)=>{const reply=e.response(p);p.effect_bindings.find(b=>b.binding_id===e.binding.binding_id).upstream_recipe.scenario.source='injected';reply.data.plan.mechanics_model={injected:true};return reply;};
  await press(e.dialog(),'应用反应收益草稿');assert.equal(e.binding.upstream_recipe.scenario.source,expected);assert.equal(e.p.mechanics_model.injected,undefined);
});
test('source display separates frontline trigger from persistent owner bonus',()=>{
  const e=setup();e.binding.upstream_recipe={schema_version:1,scenario:clone(caps.default_scenario)};e.PL.state.report=e.response(e.p).data;
  const card=new Element();e.world.EF.renderUpstream(card,e.binding);assert.match(card.textContent,/新减抗要求持有者在场/);assert.match(card.textContent,/常驻扩散加成也可在后台生效/);assert.doesNotMatch(card.textContent,/爆发能力/);
});
