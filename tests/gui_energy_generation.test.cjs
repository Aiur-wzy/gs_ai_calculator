const GUI_TEST_RUNTIME = require('./helpers/gui_runtime.cjs');
const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),vm=require('node:vm'),crypto=require('node:crypto');
const helpers={module:{exports:{}},require,__dirname,console};
vm.runInNewContext(fs.readFileSync(path.join(__dirname,'gui_hit_parameters_review.test.cjs'),'utf8').split('\ntest(')[0]+'\nmodule.exports={setup,press,control,Element};',helpers);
const {setup:base,press,control}=helpers.module.exports,clone=x=>JSON.parse(JSON.stringify(x));
const caps=JSON.parse(require('node:child_process').execFileSync('python',['-X','utf8','-c',
  'import json,runpy; d=runpy.run_path("src/genshin_app/equipment/energy_generation_schema.py"); print(json.dumps(d["energy_generation_contract"]()))'],
  {cwd:path.join(__dirname,'..'),encoding:'utf8'}));
function setup(){
  const e=base(),w=e.world,p=e.PL.state.plan;e.PL.state.caps.energy_generation_contract=clone(caps);
  p.action_event_profiles=[{id:'profile',kind:'calibrated_single_contact_v1',prototype_hit_id:p.manual_template.hits[0].id,variant:'tap'}];
  p.action_event_bindings=[{ability_id:'e',profile_id:'profile'}];
  p.rotation_model={abilities:[{id:'e',character:'a'}],actions:[{id:'a1',ability_id:'e',start:0}],events:[]};
  w.RT.syncRevision(true); // Finish constructing the fixture before its asynchronous catalog redraw.
  for(const file of ['action-events','energy-generation'])GUI_TEST_RUNTIME.loadWebFile(w, path.join(__dirname,`../src/genshin_app/gui/web/${file}.js`));
  const calls=[];let stamp=1;
  const response=plan=>{const row=plan.energy_generation_sources?.[0];let record;
    if(row){const semantic=clone(row);delete semantic.accepted_fingerprint;
      const fp=crypto.createHash('sha256').update(JSON.stringify([stamp,semantic])).digest('hex');
      const unknown=row.scenario.hitlag_policy!=='zero_hitlag_reference';
      const ready=!unknown&&row.accepted_fingerprint===fp;
      record={...clone(row),compiler:caps.compiler,status:ready?'ready':'pending',required_confirmation:{accepted_fingerprint:fp},
        trigger_certificate:{hitlag_policy:row.scenario.hitlag_policy,isolation:{isolation_ticks:200001}},resource_projection:{count:3,element:'anemo',particle_type:'particle'},
        parent_links:[{action_id:'a1',hit_id:'hit',t:1,order:0,landed:true}],
        issues:unknown?[{code:'energy_scene_pending',message:'未选择计时模型'}]:ready?[]:[{code:'energy_generation_confirmation_required',message:'需要接受来源'}]};}
    return {code:0,data:{state:'pending',plan:{...clone(plan),mechanics_model:{injected:true}},energy_generation_compilations:record?[record]:[]}};
  };
  w.callApi=async(name,payload)=>{calls.push({name,payload:clone(payload)});return response(payload);};
  const open=()=>w.EG.edit(),ready=()=>{open();for(const [key,value] of Object.entries({'关联的战技命中档案':'profile','数据库产球来源标识':'a'.repeat(64),
    '目标类型':'ordinary_non_animal_monster','场景允许技能掉落':'true','轴开始前的产球状态':'inactive','拾取方式':'all_at_once',
    '没有额外控制器重置或延寿':'true','所有队员存活':'true','受击停顿的计时模型':'zero_hitlag_reference','场景声明状态':'assumed','场景与初态依据':'参考工程单目标初态',
    '命中到产球（秒）':0,'产球到收取（秒）':.6,'时间记录精度（秒）':.01,'时间校准状态':'assumed','时间校准依据':'独立参考校准'}))e.field(key,value);};
  return {...e,p,calls,open,ready,response,changeSource:()=>stamp++};
}
async function applied(e){e.ready();await press(e.dialog(),'预览产球来源与命中');await press(e.dialog(),'接受本次产球来源与场景');await press(e.dialog(),'应用产球来源');}

test('energy source starts unknown and never invents profile or zero timing',async()=>{
  const e=setup();e.open();assert.equal(control(e.dialog(),'关联的战技命中档案').value,'');assert.equal(control(e.dialog(),'受击停顿的计时模型').value,'unknown');
  await press(e.dialog(),'应用产球来源');const row=e.p.energy_generation_sources[0];
  assert.equal(row.action_event_profile_id,null);assert.equal(row.calibration.spawn_delay_seconds,null);assert.equal(row.accepted_fingerprint,null);
  assert.equal(row.scenario.drop_allowed,null);assert.equal(row.scenario.hitlag_policy,'unknown');
});
test('energy native accepted source retains local declarations and explicit zero only',async()=>{
  const e=setup(),model=clone(e.p.mechanics_model),rotation=clone(e.p.rotation_model);await applied(e);const row=e.p.energy_generation_sources[0];
  assert.match(row.accepted_fingerprint,/^[0-9a-f]{64}$/);assert.equal(row.calibration.spawn_delay_seconds,0);assert.equal(row.scenario.status,'assumed');
  assert.deepEqual(clone(e.p.mechanics_model),model);assert.deepEqual(clone(e.p.rotation_model),rotation);assert.equal(row.resource_projection,undefined);
});
test('unknown or unsupported trigger cannot be made executable by acceptance',async()=>{
  const e=setup();e.ready();e.world.callApi=async(_,p)=>{const r=e.response(p);r.data.energy_generation_compilations[0].issues=[{code:'opaque_unmatched',message:'父关系未知'}];return r;};
  await press(e.dialog(),'预览产球来源与命中');await press(e.dialog(),'接受本次产球来源与场景');await press(e.dialog(),'应用产球来源');
  assert.equal(e.p.energy_generation_sources[0].accepted_fingerprint,null);
});
test('energy source requires current preview and rechecks after accepting',async()=>{
  const e=setup();e.ready();await press(e.dialog(),'接受本次产球来源与场景');assert.equal(e.calls.length,0);
  await press(e.dialog(),'预览产球来源与命中');e.changeSource();await press(e.dialog(),'接受本次产球来源与场景');
  assert.equal(e.p.energy_generation_sources,undefined);assert.match(e.dialog().textContent,/来源或校准已变化/);
  await press(e.dialog(),'接受本次产球来源与场景');await press(e.dialog(),'应用产球来源');assert.ok(e.p.energy_generation_sources[0].accepted_fingerprint);
});
for(const mutation of ['input','epoch','path','sha','replacement','close'])test(`energy stale ${mutation} cannot commit`,async()=>{
  const e=setup();e.ready();let finish,submitted;e.world.callApi=(_,p)=>{submitted=clone(p);return new Promise(r=>finish=r);};
  const waiting=press(e.dialog(),'应用产球来源');
  if(mutation==='input')e.p.manual_template.meta.name='other';if(mutation==='epoch')e.PL.state.sourceEpoch++;
  if(mutation==='path')e.RT.state.path='other';if(mutation==='sha')e.PL.state.fileSha='other';
  if(mutation==='replacement')e.PL.state.plan=clone(e.p);if(mutation==='close')press(e.dialog(),'放弃并关闭');
  finish(e.response(submitted));await waiting;assert.equal(e.p.energy_generation_sources,undefined);
});
test('energy newer edited response wins over a late earlier response',async()=>{
  const e=setup();e.ready();const calls=[];e.world.callApi=(_,p)=>new Promise(resolve=>calls.push({p:clone(p),resolve}));
  const first=press(e.dialog(),'预览产球来源与命中');e.field('产球到收取（秒）',1.2);const second=press(e.dialog(),'预览产球来源与命中');
  calls[1].resolve(e.response(calls[1].p));await second;calls[0].resolve(e.response(calls[0].p));await first;
  e.world.callApi=async(_,p)=>e.response(p);await press(e.dialog(),'接受本次产球来源与场景');await press(e.dialog(),'应用产球来源');
  assert.equal(e.p.energy_generation_sources[0].calibration.travel_seconds,1.2);
});
test('energy response with a wrong compiler cannot commit',async()=>{
  const e=setup();e.ready();e.world.callApi=async(_,p)=>{const r=e.response(p);r.data.energy_generation_compilations[0].compiler='wrong';return r;};
  await press(e.dialog(),'应用产球来源');assert.equal(e.p.energy_generation_sources,undefined);
});
test('energy removal is atomic and preserves original manual resource events',async()=>{
  const e=setup();await applied(e);e.p.energy_generation_sources[0].replaces_spawn_ids=['old'];
  e.p.rotation_model.events=[{id:'old',type:'energy_spawn',action_id:'a1',t:1,count:3}];const before=clone(e.p);
  e.world.EG.edit(0);e.world.callApi=async()=>({code:1,message:'拒绝'});await press(e.dialog(),'移除产球来源');assert.deepEqual(clone(e.p),before);
  e.world.callApi=async(_,p)=>e.response(p);await press(e.dialog(),'移除产球来源');assert.deepEqual(clone(e.p.energy_generation_sources),[]);
  assert.deepEqual(clone(e.p.rotation_model),before.rotation_model);
});
test('energy preview uses Chinese names and explains arrival and reference limits',async()=>{
  const e=setup();e.ready();await press(e.dialog(),'预览产球来源与命中');
  for(const text of ['元素微粒','到账按当时','不计受击停顿','200001 微秒','部分覆盖'])assert.ok(e.dialog().textContent.includes(text),text);
});
