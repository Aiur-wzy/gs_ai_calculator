const GUI_TEST_RUNTIME = require('./helpers/gui_runtime.cjs');
const Python = require('./helpers/python_command.cjs');
const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),vm=require('node:vm'),crypto=require('node:crypto');
const helpers={module:{exports:{}},require,__dirname,console};
vm.runInNewContext(fs.readFileSync(path.join(__dirname,'gui_hit_parameters_review.test.cjs'),'utf8').split('\ntest(')[0]+'\nmodule.exports={setup,press,control,Element};',helpers);
const {setup:base,press,control,Element}=helpers.module.exports,clone=value=>JSON.parse(JSON.stringify(value));
const caps=JSON.parse(Python.execFileSync(['-X','utf8','-c',
  'import json,runpy; d=runpy.run_path("src/genshin_app/equipment/limited_hit_benefit_schema.py"); print(json.dumps(d["limited_hit_benefit_contract"]()))'],
  {cwd:path.join(__dirname,'..'),encoding:'utf8'}));
function setup(){
  const e=base(),w=e.world,p=e.PL.state.plan;e.PL.state.caps.limited_hit_benefit_contract=clone(caps);
  p.action_event_profiles=[{id:'profile',kind:'calibrated_single_contact_v1',prototype_hit_id:p.manual_template.hits[0].id,variant:'hold'},
    {id:'tap',kind:'calibrated_single_contact_v1',prototype_hit_id:p.manual_template.hits[0].id,variant:'tap'}];
  p.action_event_bindings=[{ability_id:'e',profile_id:'profile'}];
  p.rotation_model={abilities:[{id:'e',character:'a'}],actions:[{id:'a1',ability_id:'e',start:0}],events:[]};
  w.RT.syncRevision(true);
  for(const file of ['action-events','limited-hit-benefit'])GUI_TEST_RUNTIME.loadWebFile(w, path.join(__dirname,`../src/genshin_app/gui/web/${file}.js`));
  const calls=[];let stamp=1;
  const response=plan=>{const row=plan.limited_hit_benefit_sources?.[0];let record;
    if(row){const semantic=clone(row);delete semantic.accepted_fingerprint;
      const fp=crypto.createHash('sha256').update(JSON.stringify([stamp,semantic])).digest('hex');
      const unknown=row.scenario.hitlag_policy!=='zero_hitlag_reference',ready=!unknown&&row.accepted_fingerprint===fp;
      record={...clone(row),compiler:caps.compiler,status:ready?'ready':'pending',required_confirmation:{accepted_fingerprint:fp},
        benefit_certificate:{variant:plan.action_event_profiles.find(profile=>profile.id===row.action_event_profile_id)?.variant,coefficient:.8218079805374146,capacity:7,duration_seconds:15},
        parent_links:[{action_id:'a1',hit_id:'hit',activation_tick:250000,t:.3,order:1,landed:true}],
        issues:unknown?[{code:'limited_hit_benefit_pending',message:'未选择计时模型'}]:ready?[]:[{code:'limited_hit_benefit_confirmation_required',message:'需接受来源'}]};}
    return {code:0,data:{state:'pending',plan:{...clone(plan),mechanics_model:{injected:true}},limited_hit_benefit_compilations:record?[record]:[]}};
  };
  w.callApi=async(name,payload)=>{calls.push({name,payload:clone(payload)});return response(payload);};
  const open=()=>w.LHB.edit(),ready=()=>{open();for(const [key,value] of Object.entries({
    '关联的战技命中档案':'profile','目标类型':'ordinary_non_animal_monster','队伍模式':'single_player_fixed',
    '所有队员存活':'true','施加时满足来源范围条件':'true','保有期间满足来源范围条件':'true','轴开始前的冰翎':'none',
    '没有额外冰翎施加、移除或延寿':'true','卡肉计时模型':'zero_hitlag_reference','场景声明状态':'assumed','场景与初态依据':'独立场景',
    '施放到生效（秒）':0,'记录精度（秒）':.01,'生效校准状态':'assumed','生效校准依据':'逐帧校准说明'}))e.field(key,value);};
  return {...e,p,calls,open,ready,response,changeSource:()=>stamp++};
}
// Explicit hypothetical caps exercise UI composition only. They do not certify
// tap source semantics or change the real hold-only product contract above.
function setupWithHypotheticalVariantCaps(){
  const e=setup();e.PL.state.caps.limited_hit_benefit_contract.supported_variants=['tap','hold'];return e;
}
async function applied(e){e.ready();await press(e.dialog(),'预览来源与生效顺序');await press(e.dialog(),'接受本次来源与场景');await press(e.dialog(),'应用有限次数收益');}

test('limited benefit default fields remain unknown including zero-valued calibration',async()=>{
  const e=setup();e.open();assert.equal(control(e.dialog(),'关联的战技命中档案').value,'');
  assert.equal(control(e.dialog(),'卡肉计时模型').value,'unknown');await press(e.dialog(),'应用有限次数收益');
  const row=clone(e.p.limited_hit_benefit_sources[0]),expected=clone(caps.default_source);expected.id=row.id;
  assert.deepEqual(row,expected);assert.equal(row.calibration.activation_offset_seconds,null);
});
test('limited benefit confirms only native declaration, never generated rules or pools',async()=>{
  const e=setup(),before=clone(e.p);await applied(e);const row=e.p.limited_hit_benefit_sources[0];
  assert.match(row.accepted_fingerprint,/^[a-f0-9]{64}$/);assert.equal(row.calibration.activation_offset_seconds,0);
  assert.deepEqual(clone(e.p.mechanics_model),before.mechanics_model);assert.deepEqual(clone(e.p.rotation_model),before.rotation_model);
  assert.deepEqual(clone(e.p.manual_template),before.manual_template);assert.equal(row.benefit_certificate,undefined);
});
test('limited benefit unknown scope cannot be accepted through a confirmation hash',async()=>{
  const e=setup();e.ready();e.world.callApi=async(_,plan)=>{const r=e.response(plan);r.data.limited_hit_benefit_compilations[0].issues=[{code:'unsupported_atk',message:'属性依赖未知'}];return r;};
  await press(e.dialog(),'预览来源与生效顺序');await press(e.dialog(),'接受本次来源与场景');await press(e.dialog(),'应用有限次数收益');
  assert.equal(e.p.limited_hit_benefit_sources[0].accepted_fingerprint,null);
});
test('limited benefit needs a shown preview and fresh matching confirmation',async()=>{
  const e=setup();e.ready();await press(e.dialog(),'接受本次来源与场景');assert.equal(e.calls.length,0);
  await press(e.dialog(),'预览来源与生效顺序');e.changeSource();await press(e.dialog(),'接受本次来源与场景');
  assert.equal(e.p.limited_hit_benefit_sources,undefined);assert.match(e.dialog().textContent,/来源或校准已变化/);
  await press(e.dialog(),'接受本次来源与场景');await press(e.dialog(),'应用有限次数收益');assert.ok(e.p.limited_hit_benefit_sources[0].accepted_fingerprint);
});
for(const mutation of ['input','epoch','path','sha','replacement','close','cancel','draft','caps'])test(`limited benefit stale ${mutation} cannot commit`,async()=>{
  const e=setup();e.ready();let finish,submitted;e.world.callApi=(_,plan)=>{submitted=clone(plan);return new Promise(resolve=>finish=resolve);};
  const waiting=press(e.dialog(),'应用有限次数收益');
  if(mutation==='input')e.p.manual_template.meta.name='changed';if(mutation==='epoch')e.PL.state.sourceEpoch++;
  if(mutation==='path')e.RT.state.path='changed';if(mutation==='sha')e.PL.state.fileSha='changed';
  if(mutation==='replacement')e.PL.state.plan=clone(e.p);if(mutation==='close')press(e.dialog(),'放弃并关闭');
  if(mutation==='cancel')e.dialog().emit('cancel');if(mutation==='draft')e.PL.state.draft=clone(e.p);
  if(mutation==='caps')e.PL.state.caps.limited_hit_benefit_contract.compiler='new';
  finish(e.response(submitted));await waiting;assert.equal(e.p.limited_hit_benefit_sources,undefined);
});
test('limited benefit edited response supersedes an older in-flight preview',async()=>{
  const e=setup();e.ready();const requests=[];e.world.callApi=(_,plan)=>new Promise(resolve=>requests.push({plan:clone(plan),resolve}));
  const old=press(e.dialog(),'预览来源与生效顺序');e.field('施放到生效（秒）',.2);const latest=press(e.dialog(),'预览来源与生效顺序');
  requests[1].resolve(e.response(requests[1].plan));await latest;requests[0].resolve(e.response(requests[0].plan));await old;
  e.world.callApi=async(_,plan)=>e.response(plan);await press(e.dialog(),'接受本次来源与场景');await press(e.dialog(),'应用有限次数收益');
  assert.equal(e.p.limited_hit_benefit_sources[0].calibration.activation_offset_seconds,.2);
});
for(const mutation of ['compiler','profile','missing','invalid'])test(`limited benefit rejects ${mutation} response`,async()=>{
  const e=setup();e.ready();e.world.callApi=async(_,plan)=>{const r=e.response(plan),row=r.data.limited_hit_benefit_compilations[0];
    if(mutation==='compiler')row.compiler='wrong';if(mutation==='profile')row.action_event_profile_id='wrong';
    if(mutation==='missing')r.data.limited_hit_benefit_compilations=[];if(mutation==='invalid')r.data.state='invalid';return r;};
  await press(e.dialog(),'应用有限次数收益');assert.equal(e.p.limited_hit_benefit_sources,undefined);
});
test('limited benefit semantic edit clears prior acceptance but unchanged reopened source does not',async()=>{
  const e=setup();await applied(e);const accepted=e.p.limited_hit_benefit_sources[0].accepted_fingerprint;
  e.p.rotation_model.actions[0].start=3;e.world.LHB.edit(0);await press(e.dialog(),'应用有限次数收益');
  assert.equal(e.p.limited_hit_benefit_sources[0].accepted_fingerprint,accepted);
  e.world.LHB.edit(0);e.field('生效校准依据','新依据');await press(e.dialog(),'应用有限次数收益');
  assert.equal(e.p.limited_hit_benefit_sources[0].accepted_fingerprint,null);
});
test('limited benefit max one source and orphan remains removable atomically',async()=>{
  const e=setup();await applied(e);const count=e.world.document.body.children.length;e.open();assert.equal(e.world.document.body.children.length,count);
  e.p.action_event_profiles=[];e.world.LHB.edit(0);const before=clone(e.p);e.world.callApi=async()=>({code:1,message:'失败'});
  await press(e.dialog(),'移除有限次数收益来源');assert.deepEqual(clone(e.p),before);
  e.world.callApi=async(_,plan)=>e.response(plan);await press(e.dialog(),'移除有限次数收益来源');assert.deepEqual(clone(e.p.limited_hit_benefit_sources),[]);
  assert.deepEqual(clone(e.p.rotation_model),before.rotation_model);
});
test('limited benefit Chinese preview exposes automatic values and actual timing without manual numerics',async()=>{
  const e=setup();e.ready();await press(e.dialog(),'预览来源与生效顺序');
  for(const text of ['当前攻击力 × 0.8218079805374146','独立 7 次','持续 15 秒','0.25','记录精度','重置次数','仅覆盖技能的一部分'])assert.ok(e.dialog().textContent.includes(text),text);
  const report=e.response(e.calls[0].payload).data,box=new Element();e.world.LHB.renderReceipt(box,report,e.p);assert.match(box.textContent,/当前攻击力/);
  const select=control(e.dialog(),'关联的战技命中档案');assert.ok(!select.children.some(option=>option.value==='tap'));
  assert.match(e.dialog().textContent,/来源认证变体：长按/);
});
test('limited benefit actual product caps permit hold and exclude tap',()=>{
  const e=setup();assert.deepEqual(clone(caps.supported_variants),['hold']);e.open();
  const options=control(e.dialog(),'关联的战技命中档案').children;
  assert.ok(options.some(option=>option.value==='profile'&&option.textContent.includes('长按')));
  assert.ok(!options.some(option=>option.value==='tap'));
  assert.match(e.dialog().textContent,/当前接口支持：长按/);
  assert.ok(!e.dialog().textContent.includes('当前接口支持：点按'));
});
test('limited benefit hypothetical caps filter UI variants without proving tap support',()=>{
  const e=setupWithHypotheticalVariantCaps();
  e.p.action_event_profiles.push({id:'other',kind:'calibrated_single_contact_v1',variant:'default'},
    {id:'periodic',kind:'calibrated_periodic_contact_v1',variant:'tap'});
  e.open();const options=control(e.dialog(),'关联的战技命中档案').children;
  assert.ok(options.some(option=>option.value==='profile'&&option.textContent.includes('长按')));
  assert.ok(options.some(option=>option.value==='tap'&&option.textContent.includes('点按')));
  assert.ok(!options.some(option=>['other','periodic'].includes(option.value)));
  assert.deepEqual(clone(caps.supported_variants),['hold']);
});
test('limited benefit old caps without supported variants remain hold-only',()=>{
  const e=setup();delete e.PL.state.caps.limited_hit_benefit_contract.supported_variants;e.open();
  assert.ok(control(e.dialog(),'关联的战技命中档案').children.some(option=>option.value==='profile'));
  assert.ok(!control(e.dialog(),'关联的战技命中档案').children.some(option=>option.value==='tap'));
});
test('limited benefit explicit empty or invalid variant caps do not invent support',()=>{
  for(const value of [[],['unknown'],null,'tap']){
    const e=setup();e.PL.state.caps.limited_hit_benefit_contract.supported_variants=value;
    const count=e.world.document.body.children.length;e.open();assert.equal(e.world.document.body.children.length,count);
    assert.equal(e.calls.length,0);
  }
});
test('limited benefit hypothetical tap UI reads mock certificate values and saves only native declarations',async()=>{
  const e=setupWithHypotheticalVariantCaps();e.ready();e.field('关联的战技命中档案','tap');const before=clone(e.p);
  e.world.callApi=async(_,plan)=>{const r=e.response(plan);r.data.limited_hit_benefit_compilations[0].benefit_certificate={variant:'tap',coefficient:.93,capacity:4,duration_seconds:11};return r;};
  await press(e.dialog(),'预览来源与生效顺序');
  for(const text of ['来源认证变体：点按','当前攻击力 × 0.93','独立 4 次','持续 11 秒','混合点按与长按仍不支持','排除怪物装备部件（MonsterEquip）','提前切人仍待支持'])assert.ok(e.dialog().textContent.includes(text),text);
  await press(e.dialog(),'接受本次来源与场景');await press(e.dialog(),'应用有限次数收益');
  assert.equal(e.p.limited_hit_benefit_sources.length,1);assert.equal(e.p.limited_hit_benefit_sources[0].action_event_profile_id,'tap');
  assert.match(e.p.limited_hit_benefit_sources[0].accepted_fingerprint,/^[a-f0-9]{64}$/);
  assert.deepEqual(Object.keys(e.p.limited_hit_benefit_sources[0]).sort(),Object.keys(caps.default_source).sort());
  for(const key of ['mechanics_model','rotation_model','manual_template','action_event_profiles'])assert.deepEqual(clone(e.p[key]),before[key]);
});
test('limited benefit hypothetical variant switch invalidates acceptance and ignores the older profile response',async()=>{
  const e=setupWithHypotheticalVariantCaps();await applied(e);e.world.LHB.edit(0);let finish;
  e.world.callApi=(_,plan)=>new Promise(resolve=>finish=()=>resolve(e.response(plan)));
  const stale=press(e.dialog(),'预览来源与生效顺序');e.field('关联的战技命中档案','tap');finish();await stale;
  assert.ok(!e.dialog().textContent.includes('来源认证变体：长按'));
  e.world.callApi=async(_,plan)=>e.response(plan);await press(e.dialog(),'应用有限次数收益');
  assert.equal(e.p.limited_hit_benefit_sources.length,1);assert.equal(e.p.limited_hit_benefit_sources[0].action_event_profile_id,'tap');
  assert.equal(e.p.limited_hit_benefit_sources[0].accepted_fingerprint,null);
});
test('limited benefit hypothetical mixed-axis rejection cannot be accepted or create a second pool',async()=>{
  const e=setupWithHypotheticalVariantCaps();e.ready();e.field('关联的战技命中档案','tap');
  e.world.callApi=async(_,plan)=>{const r=e.response(plan);r.data.limited_hit_benefit_compilations[0].issues=[{code:'limited_hit_benefit_scope_pending',message:'同一提供者全轴须固定一种变体'}];return r;};
  const before=clone(e.p);await press(e.dialog(),'预览来源与生效顺序');await press(e.dialog(),'接受本次来源与场景');
  assert.equal(e.p.limited_hit_benefit_sources,undefined);await press(e.dialog(),'应用有限次数收益');
  assert.equal(e.p.limited_hit_benefit_sources.length,1);assert.equal(e.p.limited_hit_benefit_sources[0].accepted_fingerprint,null);
  assert.deepEqual(clone(e.p.mechanics_model),before.mechanics_model);
});
test('limited benefit entry and receipt are registered with existing native GUI',()=>{
  const root=path.join(__dirname,'../src/genshin_app/gui/web');
  assert.match(fs.readFileSync(path.join(root,'index.html'),'utf8'),/src="limited-hit-benefit\.js"/);
  assert.match(fs.readFileSync(path.join(root,'checks.js'),'utf8'),/window\.LHB\?\.render\(box\)/);
  assert.match(fs.readFileSync(path.join(root,'action-events.js'),'utf8'),/LHB\.renderReceipt\(box,report,plan\)/);
});
