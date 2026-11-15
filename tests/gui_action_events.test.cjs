const GUI_TEST_RUNTIME = require('./helpers/gui_runtime.cjs');
const Python = require('./helpers/python_command.cjs');
const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),vm=require('node:vm'),crypto=require('node:crypto');
const helpers={module:{exports:{}},require,__dirname,console};
const harness=fs.readFileSync(path.join(__dirname,'gui_checks.test.cjs'),'utf8').split('\ntest(')[0];
vm.runInNewContext(harness+'\nmodule.exports={setup,control,button,edit,clone,Element};',helpers);
const {setup,control,button,edit,Element}=helpers.module.exports,clone=v=>JSON.parse(JSON.stringify(v));
const all=n=>[n,...n.children.flatMap(all)];
const press=(e,label)=>{const node=button(e.dialog(),label);assert.ok(node,label);return node.emit('click');};
function fixture(){
  const e=setup(),w=e.world,p=e.PL.state.plan;p.plan_version=3;w.RT.state={path:'native.json'};e.PL.state.fileSha='file-sha';e.PL.state.caps={action_event_contract:{compiler:'calibrated-action-events-v1'}};
  p.manual_template.hits=[{id:'prototype',character:'a',skill:'战技',hit_name:'单次战技命中',t:0,damage_system:'classic',element:'cryo',scaling_stat:'atk',count:1}];
  p.hit_parameter_sources=[{hit_id:'prototype',version_line:'release',selection:{character_id:10000002,slot:'skill',constellation:0}}];
  p.mechanics_model={schema_version:1,hit_patterns:[],hit_events:[],elemental_model:{ruleset:'gauge-freeze-reference-v1',profiles:[{id:'ice',element:'cryo',application:'apply'}],contact_profiles:[{id:'safe',freeze_interaction:'none'}]}};
  p.rotation_model.abilities=[{id:'e',character:'a',variant:'tap',action_type_id:'action.elemental_skill'}];
  p.rotation_model.actions=[0,2,4].map((t,i)=>({id:`a${i+1}`,ability_id:'e',start:t,occupation_end:t+1,order:1,repeat:{mode:'once'}}));
  w.MC={label:v=>({cryo:'冰',apply:'施加元素'}[v]||v),touch(){p.revision++;},render(){}};w.RS={state:{snapshot:null}};
  GUI_TEST_RUNTIME.loadWebFile(w, path.join(__dirname,'../src/genshin_app/gui/web/action-events.js'));
  const calls=[];let stamp='one';
  const response=plan=>({code:0,data:{state:'pending',plan:{...clone(plan),mechanics_model:{injected:'never-save'}},missing_inputs:[{message:'其他输入尚待完善'}],action_event_compilations:(plan.action_event_profiles||[]).map(profile=>{
    const semantic=clone(profile);delete semantic.accepted_profile_fingerprint;
    const fp=crypto.createHash('sha256').update(JSON.stringify([stamp,semantic])).digest('hex');
    const abilities=(plan.action_event_bindings||[]).filter(b=>b.profile_id===profile.id).map(b=>b.ability_id),actions=plan.rotation_model.actions.filter(a=>abilities.includes(a.ability_id));
    const generated=actions.map((a,i)=>{const o=plan.action_event_overrides?.find(o=>o.action_id===a.id);return{action_id:a.id,hit_id:`generated-${i}`,t:a.start+profile.calibration.offset_seconds,order:o?.order??0,landed:o&&Object.hasOwn(o,'landed')?o.landed:profile.default_landed};});
    return{profile_id:profile.id,prototype_hit_id:profile.prototype_hit_id,status:profile.accepted_profile_fingerprint===fp&&profile.calibration.status!=='pending'&&profile.default_landed!==null?'ready':'pending',dispatch_mode:'calibrated',skill_coverage:'partial',resource_coverage:'not_generated',required_confirmation:{accepted_profile_fingerprint:fp},generated,stats:{matched_actions:actions.length,planned_hits:actions.length,generated_hits:actions.length},issues:[]};})}});
  w.callApi=async(name,plan)=>{assert.equal(name,'compose_equipment_plan');calls.push(clone(plan));return response(plan);};
  const field=(label,value)=>edit(e.dialog(),label,value);
  const filled=()=>{w.AE.editProfile();field('共享命中原型','prototype');field('动作变体声明','tap');field('从动作开始到接触的秒数',.4);field('记录精度（秒）',.01);field('校准来源、固定状态和适用范围','工程校准：固定状态，无卡肉，不代表游戏帧数');field('附着档案','ice');field('接触档案','safe');};
  return{...e,p,AE:w.AE,calls,response,field,filled,changeSource:()=>stamp='two'};
}
async function profile(e){e.filled();await press(e,'应用档案草稿');return e.p.action_event_profiles[0];}
async function binding(e){await profile(e);e.AE.editBinding();e.field('绑定的战技能力','e');e.field('采用的动作档案',e.p.action_event_profiles[0].id);await press(e,'应用档案草稿');}

test('action editor uses the shared dialog lifecycle and fails safely when another dialog is busy',()=>{
  const e=fixture(),before=e.PL.currentInputJson(),blocker=e.world.AppDialogKernel.create({title:'另一项操作正在提交',busy:true});
  assert.ok(blocker);e.AE.editProfile();assert.equal(e.world.AppDialogKernel.getActive(),blocker);
  assert.match(e.world.lastStatus,/先完成或关闭/);assert.equal(e.PL.currentInputJson(),before);blocker.finish('test-cleanup');
});

test('new dynamic action drafts use an explicit application contract without contact fields',async()=>{
  const e=fixture();e.PL.state.caps.action_event_contract.profile_kinds=['calibrated_single_contact_v1','calibrated_single_application_v2'];
  e.p.mechanics_model.elemental_model.ruleset='gauge-swirl-dynamic-v3';
  const before=clone(e.p.mechanics_model);e.AE.editProfile();
  for(const [label,value] of [['共享命中原型','prototype'],['动作变体声明','tap'],['从动作开始到接触的秒数',.4],['记录精度（秒）',.01],['校准来源、固定状态和适用范围','明确校准'],['附着档案','ice']])e.field(label,value);
  assert.match(e.dialog().textContent,/单次伤害与附着档案/);assert.equal(control(e.dialog(),'接触档案'),undefined);
  await press(e,'应用档案草稿');const row=e.p.action_event_profiles[0];
  assert.equal(row.kind,'calibrated_single_application_v2');assert.equal(Object.hasOwn(row,'contact_profile_id'),false);
  assert.equal(row.accepted_profile_fingerprint,null);assert.deepEqual(clone(e.p.mechanics_model),before);
});

test('editing an old action under a changed elemental model never upgrades its contract',async()=>{
  const e=fixture();const row=await profile(e);e.PL.state.caps.action_event_contract.profile_kinds=['calibrated_single_contact_v1','calibrated_single_application_v2'];
  e.p.mechanics_model.elemental_model.ruleset='gauge-swirl-dynamic-v3';e.AE.editProfile(0);
  assert.match(e.dialog().textContent,/当前尚非冻结参考模型/);assert.ok(control(e.dialog(),'接触档案'));
  await press(e,'应用档案草稿');assert.equal(e.p.action_event_profiles[0].kind,'calibrated_single_contact_v1');
  assert.equal(e.p.action_event_profiles[0].contact_profile_id,row.contact_profile_id);
});

test('profile defaults remain unknown and pending; no unrequested generated data is saved',async()=>{
  const e=fixture(),model=clone(e.p.mechanics_model),hits=clone(e.p.manual_template.hits),sources=clone(e.p.hit_parameter_sources);e.filled();
  assert.equal(control(e.dialog(),'默认命中判断').value,'');assert.equal(control(e.dialog(),'校准依据状态').value,'pending');
  await press(e,'应用档案草稿');const row=e.p.action_event_profiles[0];assert.equal(row.default_landed,null);assert.equal(row.calibration.status,'pending');assert.equal(row.accepted_profile_fingerprint,null);
  assert.deepEqual(clone(e.p.mechanics_model),model);assert.deepEqual(clone(e.p.manual_template.hits),hits);assert.deepEqual(clone(e.p.hit_parameter_sources),sources);
  assert.deepEqual(clone(e.p.action_event_bindings),[]);assert.deepEqual(clone(e.p.action_event_overrides),[]);
});
test('profile requires independent preview then fresh confirmation; status is never promoted',async()=>{
  const e=fixture();e.filled();await press(e,'确认所示档案指纹');assert.equal(e.calls.length,0);
  await press(e,'读取并预览动作档案');await press(e,'确认所示档案指纹');assert.equal(e.p.action_event_profiles,undefined);
  await press(e,'应用档案草稿');const row=e.p.action_event_profiles[0];assert.match(row.accepted_profile_fingerprint,/^[0-9a-f]{64}$/);assert.equal(row.calibration.status,'pending');assert.equal(row.default_landed,null);assert.equal(e.calls.length,3);
});
test('a changed source requires reading the new receipt before accepting its fingerprint',async()=>{
  const e=fixture();e.filled();await press(e,'读取并预览动作档案');e.changeSource();await press(e,'确认所示档案指纹');assert.match(e.dialog().textContent,/依赖已变化/);
  await press(e,'确认所示档案指纹');await press(e,'应用档案草稿');assert.ok(e.p.action_event_profiles[0].accepted_profile_fingerprint);
});
test('source changes between confirmation and apply cannot commit a stale acceptance',async()=>{
  const e=fixture();e.filled();await press(e,'读取并预览动作档案');await press(e,'确认所示档案指纹');e.changeSource();await press(e,'应用档案草稿');
  assert.equal(e.p.action_event_profiles,undefined);assert.match(e.dialog().textContent,/旧确认已清除/);await press(e,'应用档案草稿');assert.equal(e.p.action_event_profiles[0].accepted_profile_fingerprint,null);
});
test('editing semantic fields clears confirmation and preview',async()=>{
  const e=fixture();e.filled();await press(e,'读取并预览动作档案');await press(e,'确认所示档案指纹');e.field('默认命中判断','true');await press(e,'确认所示档案指纹');assert.match(e.dialog().textContent,/请先读取并预览/);
  await press(e,'应用档案草稿');assert.equal(e.p.action_event_profiles[0].accepted_profile_fingerprint,null);assert.equal(e.p.action_event_profiles[0].default_landed,true);
});
for(const mutation of ['plan','input','epoch','path','sha','draft','close'])test(`late ${mutation} response cannot overwrite native input`,async()=>{
  const e=fixture();e.filled();const old=e.p;let finish,submitted;e.world.callApi=(_,p)=>{submitted=clone(p);return new Promise(resolve=>finish=resolve);};const pending=press(e,'应用档案草稿');
  if(mutation==='plan')e.PL.state.plan=clone(old);if(mutation==='input')old.manual_template.meta={name:'changed'};if(mutation==='epoch')e.PL.state.sourceEpoch++;
  if(mutation==='path')e.world.RT.state.path='other.json';if(mutation==='sha')e.PL.state.fileSha='other-sha';if(mutation==='draft')e.PL.state.draft=clone(old);if(mutation==='close')press(e,'放弃并关闭');
  finish(e.response(submitted));await pending;assert.equal(old.action_event_profiles,undefined);assert.equal(e.PL.state.plan.action_event_profiles,undefined);
});
test('out-of-order preview retains only the newest edited scope',async()=>{
  const e=fixture();e.filled();const waiting=[];e.world.callApi=(_,p)=>new Promise(resolve=>waiting.push({p:clone(p),resolve}));
  const first=press(e,'读取并预览动作档案');e.field('默认命中判断','false');const second=press(e,'读取并预览动作档案');
  waiting[1].resolve(e.response(waiting[1].p));await second;waiting[0].resolve({code:1,message:'old-error'});await first;assert.doesNotMatch(e.dialog().textContent,/old-error/);
  e.world.callApi=async(_,p)=>e.response(p);await press(e,'确认所示档案指纹');await press(e,'应用档案草稿');assert.equal(e.p.action_event_profiles[0].default_landed,false);
});
test('server mutation cannot modify native actions, prototype or declarations',async()=>{
  const e=fixture();e.filled();const before=clone(e.p);e.world.callApi=async(_,p)=>{const r=e.response(p);p.rotation_model.actions[0].start=999;p.action_event_profiles[0].default_landed=true;r.data.plan.action_event_profiles[0].default_landed=true;return r;};
  await press(e,'应用档案草稿');assert.deepEqual(clone(e.p.rotation_model),before.rotation_model);assert.deepEqual(clone(e.p.manual_template),before.manual_template);assert.equal(e.p.action_event_profiles[0].default_landed,null);
});
test('one binding covers existing actions without inserting patterns or touching variants',async()=>{
  const e=fixture();await binding(e);assert.equal(e.p.action_event_bindings.length,1);assert.equal(e.p.rotation_model.actions.length,3);assert.equal(e.p.rotation_model.abilities[0].variant,'tap');assert.deepEqual(clone(e.p.mechanics_model.hit_patterns),[]);
  const box=new Element();e.AE.render(box);assert.match(box.textContent,/匹配动作 3/);assert.match(box.textContent,/已生成 3/);assert.match(box.textContent,/实际执行尚待计算/);
});
test('per-action missed and order overrides are explicit and never copy actions',async()=>{
  const e=fixture();await binding(e);e.AE.editOverride();e.field('本轴实际动作','a2');e.field('本次命中例外','false');e.field('本次同刻顺序（空白不覆盖）',-2);e.field('本次例外的依据','本次明确未命中');
  await press(e,'应用档案草稿');assert.deepEqual(clone(e.p.action_event_overrides),[{action_id:'a2',landed:false,order:-2,status:'pending',source:'本次明确未命中'}]);assert.equal(e.p.rotation_model.actions.length,3);
  e.AE.editOverride(0);e.field('本次命中例外','inherit');await press(e,'应用档案草稿');assert.equal(Object.hasOwn(e.p.action_event_overrides[0],'landed'),false);assert.equal(e.p.action_event_overrides[0].order,-2);
});
test('only actual bound actions can be chosen for an override and empty changes are rejected',async()=>{
  const e=fixture();await binding(e);e.p.rotation_model.actions.push({id:'unbound',ability_id:'other',start:9});e.AE.editOverride();const options=control(e.dialog(),'本轴实际动作').children.map(c=>c.value);assert.equal(options.includes('unbound'),false);
  e.field('本轴实际动作','a2');e.field('本次命中例外','inherit');e.field('本次例外的依据','无有效覆盖');const count=e.calls.length;await press(e,'应用档案草稿');assert.equal(e.calls.length,count);assert.match(e.dialog().textContent,/至少应改变/);
});
test('deleting a profile explicitly removes its bindings and overrides but retains manual input',async()=>{
  const e=fixture();await binding(e);e.p.action_event_overrides=[{action_id:'a1',landed:false,status:'assumed',source:'例外'}];const template=clone(e.p.manual_template),rotation=clone(e.p.rotation_model);e.AE.drop(0,0);assert.match(e.dialog().textContent,/原型、倍率来源和实际动作仍保留/);
  await press(e,'应用档案草稿');assert.deepEqual(clone(e.p.action_event_profiles),[]);assert.deepEqual(clone(e.p.action_event_bindings),[]);assert.deepEqual(clone(e.p.action_event_overrides),[]);assert.deepEqual(clone(e.p.manual_template),template);assert.deepEqual(clone(e.p.rotation_model),rotation);
});
test('missing compilation or failed backend validation never applies a draft',async()=>{
  const e=fixture();e.filled();e.world.callApi=async()=>({code:0,data:{state:'ready',action_event_compilations:[]}});await press(e,'应用档案草稿');assert.equal(e.p.action_event_profiles,undefined);assert.match(e.dialog().textContent,/完整档案回执/);
  e.world.callApi=async()=>({code:1,message:'scope rejected'});await press(e,'应用档案草稿');assert.equal(e.p.action_event_profiles,undefined);
});
test('confirmation requires a real current hash, never an absent or cached record',async()=>{
  const e=fixture();e.filled();e.PL.state.report={action_event_compilations:[{required_confirmation:{accepted_profile_fingerprint:'f'.repeat(64)}}]};
  e.world.callApi=async(_,p)=>{const r=e.response(p);delete r.data.action_event_compilations[0].required_confirmation;return r;};await press(e,'读取并预览动作档案');await press(e,'确认所示档案指纹');assert.match(e.dialog().textContent,/尚无完整可确认指纹/);assert.equal(e.p.action_event_profiles,undefined);
});
test('read-only result distinguishes planned, missed and actual counted events',async()=>{
  const e=fixture();await binding(e);const report=e.response(e.p).data,row=report.action_event_compilations[0];row.generated[1].landed=false;
  const result={...report,hits:[{id:'generated-0'}],rotation_check:{event_log:[{id:'generated-1',cycle:0,status:'missed'},{id:'generated-2',cycle:0,status:'not_verified'}]}};
  const box=new Element();e.AE.renderReceipt(box,result,e.p,result);assert.match(box.textContent,/本次实际计伤 1/);assert.match(box.textContent,/未命中，保留施放记录/);assert.match(box.textContent,/后续未验证/);assert.equal(all(box).filter(n=>['input','select','textarea'].includes(n.tagName)).length,0);
});
test('legacy plans and unavailable capability do not display or mutate new fields',()=>{
  const e=fixture();delete e.PL.state.caps.action_event_contract;const box=new Element();e.AE.render(box);e.AE.editProfile();assert.equal(box.children.length,0);assert.equal(e.p.action_event_profiles,undefined);
  e.PL.state.caps.action_event_contract={};e.p.plan_version=2;e.AE.render(box);assert.equal(box.children.length,0);
});
test('equipment draft and invalid numeric input cannot overwrite the original plan',async()=>{
  const e=fixture();e.PL.state.draft=clone(e.p);e.AE.editProfile();assert.equal(e.body.children.length,0);e.PL.state.draft=null;e.filled();e.field('从动作开始到接触的秒数',0);await press(e,'应用档案草稿');assert.equal(e.calls.length,0);assert.match(e.dialog().textContent,/大于0/);
});
test('real compiler pending receipt saves an unbound profile, then accepts a separate ability binding draft',async()=>{
  const e=fixture(),receipts=[],python=`import json,sys
from genshin_app.equipment.action_events import compile_action_events
p=json.load(sys.stdin)
issues=[]
def missing(code,path,message,*args): issues.append(dict(code=code,path=path,message=message))
model,records,sources=compile_action_events(p,p['manual_template'],parameter_compilations=[],elemental_compilations=[],reaction_compilations=[],missing=missing)
print(json.dumps(dict(code=0,data=dict(state='pending',action_event_compilations=records,missing_inputs=issues)),ensure_ascii=False))`;
  e.world.callApi=async(name,p)=>{assert.equal(name,'compose_equipment_plan');const response=JSON.parse(Python.execFileSync(['-X','utf8','-c',python],{cwd:path.join(__dirname,'..'),input:JSON.stringify(p),encoding:'utf8'}));receipts.push(response.data);return response;};
  e.filled();await press(e,'应用档案草稿');assert.equal(e.p.action_event_profiles.length,1);assert.equal(e.p.action_event_bindings.length,0);
  const unbound=receipts[0].action_event_compilations[0];assert.equal(unbound.status,'pending');assert.equal(unbound.required_confirmation,null);assert.equal(unbound.profile_fingerprint,null);assert.equal(unbound.stats.matched_actions,0);assert.equal(unbound.issues[0].code,'action_event_no_actions');
  e.AE.editBinding();e.field('绑定的战技能力','e');e.field('采用的动作档案',e.p.action_event_profiles[0].id);await press(e,'应用档案草稿');
  assert.equal(e.p.action_event_bindings.length,1);const bound=receipts[1].action_event_compilations[0];assert.equal(bound.stats.matched_actions,3);assert.equal(bound.stats.planned_hits,3);assert.equal(bound.stats.generated_hits,0);assert.equal(bound.issues[0].code,'action_event_parameter_missing');
  assert.equal(e.p.action_event_profiles[0].calibration.status,'pending');assert.equal(e.p.action_event_profiles[0].default_landed,null);assert.deepEqual(clone(e.p.mechanics_model.hit_patterns),[]);
});
for(const key of ['hit_parameter_compilations','hit_parameter_sources'])test(`complete parameter description is displayed from ${key}`,async()=>{
  const e=fixture();await binding(e);const report=e.response(e.p).data;report[key]=[{hit_id:'prototype',parameter_descriptions:{matched:[{text:'完整参数原文，不裁剪多参数句。'}]}}];
  e.world.MC.renderHitParameterCompilation=(box,row)=>box.append(e.world.UI.el('p',row.parameter_descriptions.matched[0].text));
  const box=new Element();e.AE.renderReceipt(box,report,e.p);assert.match(box.textContent,/完整参数原文，不裁剪多参数句/);assert.match(box.textContent,/记录精度 0.01 秒/);assert.doesNotMatch(box.textContent,/误差 0.01/);
});
