const GUI_TEST_RUNTIME = require('./helpers/gui_runtime.cjs');
const Python = require('./helpers/python_command.cjs');
const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),vm=require('node:vm'),crypto=require('node:crypto');
const helpers={module:{exports:{}},require,__dirname,console};
vm.runInNewContext(fs.readFileSync(path.join(__dirname,'gui_hit_parameters_review.test.cjs'),'utf8').split('\ntest(')[0]+'\nmodule.exports={setup,press,control,Element};',helpers);
const {setup:base,press,control,Element}=helpers.module.exports,clone=x=>JSON.parse(JSON.stringify(x));
const caps=JSON.parse(Python.execFileSync(['-X','utf8','-c',
  'import json; from genshin_engine.runtime_passive_benefit_compiler import passive_benefit_compiler_caps; print(json.dumps(passive_benefit_compiler_caps()))'],
  {cwd:path.join(__dirname,'..'),encoding:'utf8'}));
caps.confirmable_issue_codes=['passive_confirmation_required'];
function setup(){
  const e=base(),w=e.world;e.PL.state.caps.passive_source_contract=clone(caps);e.PL.state.draft=clone(e.PL.state.plan);
  const p=w.EQ.plan();p.equipment_inputs.a.character_ref.db_character_id=10000047;p.mechanics_model.elemental_model={target:{id:'enemy'}};
  GUI_TEST_RUNTIME.loadWebFile(w, path.join(__dirname,'../src/genshin_app/gui/web/passive-sources.js'));
  const calls=[];let stamp=1;
  const candidates={code:0,data:{status:'pending',candidates:[{proud_skill_id:472201,name:{text:'风物之诗咏'},threshold:{status:'ready',required_ascension:4}}]}};
  const response=plan=>{const r=plan.passive_sources?.[0];let record;
    if(r){const source=clone(r);delete source.accepted_fingerprint;
      const fp=crypto.createHash('sha256').update(JSON.stringify([stamp,source,plan.equipment_inputs.a.progression])).digest('hex');
      const ready=r.scenario.status!=='pending'&&r.accepted_fingerprint===fp;
      record={...clone(r),profile_id:caps.profile_id,status:ready?'ready':'pending',source:{descriptions:{name:{text:'风物之诗咏'},description:{text:'依照精通提升对应元素伤害。'},unlock_description:{status:'pending'}}},
        unlock:{status:'eligible_under_declared_progression'},stat_basis:{panel_value:900},required_confirmation:{accepted_fingerprint:fp},
        rules:[],preview_rules:[{name_zh:'冰元素伤害加成',timing:{duration:8},effects:[{value:{factor:.0004}}]}],
        issues:r.scenario.status==='pending'?[{code:'scenario_evidence_pending',message:'场景仍未知'}]:ready?[]:[{code:'passive_confirmation_required',message:'需接受当前来源'}]};}
    return {code:0,data:{state:'pending',plan:{...clone(plan),mechanics_model:{injected:true}},passive_compilations:record?[record]:[]}};};
  w.callApi=async(name,payload)=>{calls.push({name,payload:clone(payload)});return name==='query_runtime'?clone(candidates):response(payload);};
  const open=()=>w.PB.edit('a'),select=async()=>{await press(e.dialog(),'读取角色固有天赋候选');e.field('采用的固有天赋',472201);};
  const ready=async()=>{open();await select();for(const [key,value] of Object.entries({'实际命座':0,'角色突破阶段':6,'突破声明状态':'assumed','突破依据':'显式突破6阶',
    '对应元素目标':'enemy','原目标范围':'within_source_range','全队受益范围':'all_party_within_source_range','窗口前的该源增伤':'none',
    '各角色初始面板':'not_included','提供者固定精通口径':'fixed_panel_nonextra_em_equals_total_em','原子事件顺序':'declared_atomic_hit_order',
    '场景声明状态':'assumed','精通组成、初态及范围依据':'给定900精通，无转换；全队范围内，轴前无实例'}))e.field(key,value);};
  return {...e,p,calls,candidates,response,open,select,ready,changeSource:()=>stamp++};
}
async function applied(e){await e.ready();await press(e.dialog(),'预览固有来源与实际资格');await press(e.dialog(),'接受本次来源与声明');await press(e.dialog(),'应用固有天赋草稿');}

test('passive candidate discovery is explicit, scoped and never auto-selected',async()=>{
  const e=setup();e.open();await press(e.dialog(),'读取角色固有天赋候选');
  assert.equal(control(e.dialog(),'采用的固有天赋').value,'');assert.equal(e.p.equipment_inputs.a.progression,undefined);
  assert.deepEqual(e.calls[0].payload,{action:'passive_source_profile',character_id:10000047,version_line:'release'});
  await press(e.dialog(),'应用固有天赋草稿');assert.equal(e.calls.length,1);assert.equal(e.p.passive_sources,undefined);
});
test('passive accepted draft keeps native source and progression, never generated server rules',async()=>{
  const e=setup(),model=clone(e.p.mechanics_model);await applied(e);const row=e.p.passive_sources[0];
  assert.equal(row.proud_skill_id,472201);assert.equal(row.constellation,0);assert.equal(row.scenario.status,'assumed');assert.match(row.accepted_fingerprint,/^[0-9a-f]{64}$/);
  assert.equal(e.p.equipment_inputs.a.progression.ascension,6);assert.deepEqual(clone(e.p.mechanics_model),model);assert.equal(e.PL.state.plan.passive_sources,undefined);
});
test('passive unknown scenario and progression are saved only as unknown drafts',async()=>{
  const e=setup();e.open();await e.select();await press(e.dialog(),'预览固有来源与实际资格');await press(e.dialog(),'接受本次来源与声明');await press(e.dialog(),'应用固有天赋草稿');
  const row=e.p.passive_sources[0];assert.equal(row.accepted_fingerprint,null);assert.equal(row.constellation,null);assert.equal(row.scenario.stat_basis,'unknown');
  assert.equal(row.scenario.initial_source_effects,'unknown');assert.equal(e.p.equipment_inputs.a.progression.ascension,null);assert.equal(e.p.equipment_inputs.a.progression.status,'pending');
});
test('passive Chinese preview identifies sampling duration unlock and partial coverage',async()=>{
  const e=setup();await e.ready();await press(e.dialog(),'预览固有来源与实际资格');const text=e.dialog().textContent;
  for(const value of ['风物之诗咏','36.00%','8 秒','自身快照','后台','缺少解锁说明原文','仅部分支持'])assert.ok(text.includes(value),value);
});
for(const mutation of ['input','live','epoch','path','sha','replacement','close'])test(`passive stale ${mutation} cannot commit`,async()=>{
  const e=setup();await e.ready();let finish,submitted;e.world.callApi=(_,p)=>{submitted=clone(p);return new Promise(r=>finish=r);};const pending=press(e.dialog(),'应用固有天赋草稿');
  if(mutation==='input')e.p.manual_template.meta={name:'other'};if(mutation==='live')e.PL.state.plan.manual_template.meta={name:'other'};
  if(mutation==='epoch')e.PL.state.sourceEpoch++;if(mutation==='path')e.RT.state.path='other';if(mutation==='sha')e.PL.state.fileSha='other';
  if(mutation==='replacement')e.PL.state.draft=clone(e.p);if(mutation==='close')press(e.dialog(),'放弃并关闭');finish(e.response(submitted));await pending;
  assert.equal(e.p.passive_sources,undefined);assert.equal(e.p.equipment_inputs.a.progression,undefined);
});
test('passive out-of-order replies retain latest edited scene',async()=>{
  const e=setup();await e.ready();const calls=[];e.world.callApi=(_,p)=>new Promise(resolve=>calls.push({p:clone(p),resolve}));
  const first=press(e.dialog(),'预览固有来源与实际资格');e.field('精通组成、初态及范围依据','更新后的声明');const second=press(e.dialog(),'预览固有来源与实际资格');
  calls[1].resolve(e.response(calls[1].p));await second;calls[0].resolve(e.response(calls[0].p));await first;e.world.callApi=async(_,p)=>e.response(p);
  await press(e.dialog(),'接受本次来源与声明');await press(e.dialog(),'应用固有天赋草稿');assert.equal(e.p.passive_sources[0].scenario.source,'更新后的声明');
});
test('passive changing source while accepting requires a new preview',async()=>{
  const e=setup();await e.ready();await press(e.dialog(),'预览固有来源与实际资格');e.changeSource();await press(e.dialog(),'接受本次来源与声明');
  assert.equal(e.p.passive_sources,undefined);assert.match(e.dialog().textContent,/来源、面板或动作资格已变化/);
  await press(e.dialog(),'接受本次来源与声明');await press(e.dialog(),'应用固有天赋草稿');assert.ok(e.p.passive_sources[0].accepted_fingerprint);
});
test('passive unsupported source cannot be upgraded by confirmation',async()=>{
  const e=setup();await e.ready();e.world.callApi=async(_,p)=>{const r=e.response(p);r.data.passive_compilations[0].issues=[{code:'unsupported_source',message:'来源不支持'}];return r;};
  await press(e.dialog(),'预览固有来源与实际资格');await press(e.dialog(),'接受本次来源与声明');await press(e.dialog(),'应用固有天赋草稿');assert.equal(e.p.passive_sources[0].accepted_fingerprint,null);
});
test('passive absent or wrong profile receipt blocks draft commit',async()=>{
  const e=setup();await e.ready();e.world.callApi=async(_,p)=>{const r=e.response(p);r.data.passive_compilations[0].profile_id='other';return r;};await press(e.dialog(),'应用固有天赋草稿');
  assert.equal(e.p.passive_sources,undefined);assert.match(e.dialog().textContent,/缺少可核验/);
});
test('passive removal is atomic and preserves actual progression and mechanics',async()=>{
  const e=setup();await applied(e);const before=clone(e.p),row=e.p.passive_sources[0];e.world.PB.edit('a',row.id);e.world.callApi=async()=>({code:1,message:'拒绝'});
  await press(e.dialog(),'移除此固有来源');assert.deepEqual(clone(e.p),before);e.world.callApi=async(_,p)=>e.response(p);await press(e.dialog(),'移除此固有来源');
  assert.deepEqual(clone(e.p.passive_sources),[]);assert.deepEqual(clone(e.p.equipment_inputs.a.progression),before.equipment_inputs.a.progression);assert.deepEqual(clone(e.p.mechanics_model),before.mechanics_model);
});
test('passive invalid discovery candidates never populate selection',async()=>{
  const e=setup();e.open();e.world.callApi=async()=>({code:0,data:{candidates:[{proud_skill_id:1},{proud_skill_id:1}]}});await press(e.dialog(),'读取角色固有天赋候选');
  assert.match(e.dialog().textContent,/身份重复/);assert.equal(e.p.passive_sources,undefined);
});
test('passive locally proposed source survives server input mutation',async()=>{
  const e=setup();await e.ready();const expected=control(e.dialog(),'精通组成、初态及范围依据').value;e.world.callApi=async(_,p)=>{const reply=e.response(p);p.passive_sources[0].scenario.source='injected';return reply;};
  await press(e.dialog(),'应用固有天赋草稿');assert.equal(e.p.passive_sources[0].scenario.source,expected);assert.equal(e.p.mechanics_model.injected,undefined);
});
test('passive actor rename invalidates acceptance and no other source',()=>{
  const e=setup();e.PL.state.plan.passive_sources=[{id:'a-source',actor:'a',accepted_fingerprint:'old'},{id:'other',actor:'other',accepted_fingerprint:'keep'}];
  e.PL.renameCharacter('a','renamed');assert.equal(e.PL.state.plan.passive_sources[0].actor,'renamed');assert.equal(e.PL.state.plan.passive_sources[0].accepted_fingerprint,null);
  assert.equal(e.PL.state.plan.passive_sources[1].accepted_fingerprint,'keep');
});
