const GUI_TEST_RUNTIME = require('./helpers/gui_runtime.cjs');
const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),vm=require('node:vm'),crypto=require('node:crypto');
const helpers={module:{exports:{}},require,__dirname,console};
vm.runInNewContext(fs.readFileSync(path.join(__dirname,'gui_checks.test.cjs'),'utf8').split('\ntest(')[0]+'\nmodule.exports={setup,control,button,edit,Element};',helpers);
const {setup,control,button,edit,Element}=helpers.module.exports,clone=x=>JSON.parse(JSON.stringify(x)),all=n=>[n,...n.children.flatMap(all)];
const origin='a'.repeat(64),slice='periodic.'+'b'.repeat(64),blast='periodic.'+'c'.repeat(64);
const press=(e,label)=>{const b=button(e.dialog(),label);assert.ok(b,label);return b.emit('click');};
const cards=e=>all(e.dialog()).filter(n=>n.tagName==='section'&&n.children[0]?.textContent?.startsWith('攻击通道 '));
function fixture(){
  const e=setup(),w=e.world,p=e.PL.state.plan;p.plan_version=3;w.RT.state={path:'periodic.json'};e.PL.state.fileSha='sha';e.PL.state.caps={action_event_contract:{},periodic_attack_contract:{},attack_metadata_contract:{}};
  w.AppContextMenu={forDocument:()=>({open:config=>{w.lastContextMenu=config;return true;},bind:(target,config)=>{target.contextMenuConfig=config;}})};
  p.manual_template.hits=['slice','blast'].map((id,i)=>({id,character:'a',skill:i?'独立终结':'连续切割',hit_name:i?'独立终结':'连续切割',t:0,element:'cryo',multiplier:i?3:2,damage_system:'classic',scaling_stat:'atk',count:1,damage_type_ids:['damage.elemental_burst']}));
  p.rotation_model.abilities=[{id:'q',character:'a',variant:'default',action_type_id:'action.elemental_burst'},{id:'e',character:'a',variant:'tap',action_type_id:'action.elemental_skill'}];
  p.rotation_model.actions=[0,10,20].map((t,i)=>({id:`q${i}`,ability_id:'q',start:t,occupation_end:t+1,order:0,repeat:{mode:'once'}}));
  p.mechanics_model={hit_patterns:[],hit_events:[],elemental_model:{profiles:[],contact_profiles:[{id:'safe',freeze_interaction:'none',source:'明确无钝击的工程接触声明'}]}};
  p.hit_parameter_sources=[{hit_id:'slice',version_line:'release',selection:{character_id:10000002,slot:'burst',constellation:0}}];
  w.MC={touch(){p.revision++;},render(){}};w.RS={state:{snapshot:null}};
  for(const name of ['action-events','attack-metadata','periodic-attacks'])GUI_TEST_RUNTIME.loadWebFile(w, path.join(__dirname,`../src/genshin_app/gui/web/${name}.js`));
  const calls=[];let generation='one';
  const response=plan=>({code:0,data:{state:'pending',missing_inputs:[],action_event_compilations:(plan.action_event_profiles||[]).map(profile=>{
    const sem=clone(profile);delete sem.accepted_profile_fingerprint;const fp=crypto.createHash('sha256').update(JSON.stringify([generation,sem,plan.periodic_attack_sources])).digest('hex');
    return{profile_id:profile.id,status:'pending',generated:[],stats:{matched_actions:0,planned_hits:0,generated_hits:0},required_confirmation:{accepted_profile_fingerprint:fp},issues:[]};}),
    periodic_attack_compilations:(plan.periodic_attack_sources||[]).map(source=>({action_event_profile_id:source.action_event_profile_id,status:'pending',query:{selected_candidates:[]},issues:[]})),plan:{injected:true}}});
  const sourceResponse=req=>({code:0,data:{mode:req.origin_reference_id?'channel_discovery':'origin_discovery',status:'pending',selection:{character_id:req.character_id,constellation:req.constellation,slot:'burst'},
    ...(req.origin_reference_id?{candidates:[{candidate_id:slice,channel_kind:'periodic_callback_attack',status:'ready'},{candidate_id:blast,channel_kind:'ability_start_attack',status:'ready'}]}:{creation_candidates:[{origin_reference_id:origin,gadget_id:41002001,association:'parameter_related_only'}]}),issues:[]}});
  w.callApi=async(name,input)=>{calls.push({name,input:clone(input)});return name==='query_runtime'?sourceResponse(input):response(input);};
  const field=(label,value,node=e.dialog())=>edit(node,label,value);
  const fillChannel=(i,id,kind='periodic')=>{let card=cards(e)[i];field('共享伤害原型',id,card);card=cards(e)[i];field('接触档案','safe',card);field('调度方式',kind,card);card=cards(e)[i];field('通道校准依据','工程校准：实际次数与首末时点均独立声明',card);};
  const filled=()=>{w.PA.editProfile();fillChannel(0,'slice');field('数据库角色身份',10000002);field('实际命座',0);field('生命周期依据','存活与结束为工程假设，不是数据库控制器证明');field('来源用途与共享关系依据','当前爆发主来源与明确共享计数');};
  const selectSource=async()=>{await press(e,'发现相关创建来源');field('精确创建来源',origin);await press(e,'读取所选来源的攻击通道');field('通道 1 的数据库攻击候选',slice);};
  return{...e,p,PA:w.PA,AE:w.AE,calls,response,sourceResponse,field,filled,fillChannel,selectSource,changeSource:()=>generation='two'};
}
test('new periodic native draft keeps unknowns, replaces only owned source fields and never saves generated data',async()=>{
  const e=fixture(),actions=clone(e.p.rotation_model);e.filled();await press(e,'应用档案草稿');
  const profile=e.p.action_event_profiles[0],source=e.p.periodic_attack_sources[0];assert.equal(profile.kind,e.PA.kind);assert.equal(profile.lifecycle.instance_end_offset_seconds,null);assert.equal(profile.channels[0].schedule.count,null);assert.equal(profile.channels[0].default_landed,null);assert.equal(profile.accepted_profile_fingerprint,null);assert.equal(source.origin_reference_id,null);assert.equal(source.channels[0].candidate_id,null);assert.equal(source.selection.constellation,0);
  assert.equal(e.p.manual_template.hits[0].element,undefined);assert.equal(e.p.manual_template.hits[0].multiplier,undefined);assert.equal(e.p.manual_template.hits[1].element,'cryo');assert.deepEqual(clone(e.p.hit_parameter_sources),[]);assert.deepEqual(clone(e.p.rotation_model),actions);assert.deepEqual(clone(e.p.mechanics_model.hit_patterns),[]);assert.equal(e.p.injected,undefined);
});
test('two distinct channels may reuse the same declared contact profile and retain stable local routes',async()=>{
  const e=fixture();e.filled();press(e,'添加攻击通道');e.fillChannel(1,'blast','single');await e.selectSource();e.field('通道 2 的数据库攻击候选',blast);await press(e,'应用档案草稿');
  const channels=e.p.action_event_profiles[0].channels;assert.equal(channels.length,2);assert.equal(channels[0].contact_profile_id,channels[1].contact_profile_id);assert.notEqual(channels[0].application_profile_id,channels[1].application_profile_id);assert.equal(channels[1].schedule.kind,'single');assert.equal(Object.hasOwn(channels[0].schedule,'interval'),false);assert.equal(e.p.manual_template.hits.every(h=>!Object.hasOwn(h,'element')),true);
});
test('origin and channel discovery never auto-select a sole candidate or send a null origin',async()=>{
  const e=fixture();e.filled();await press(e,'发现相关创建来源');assert.equal(control(e.dialog(),'精确创建来源').value,'');assert.equal(Object.hasOwn(e.calls[0].input,'origin_reference_id'),false);const n=e.calls.length;await press(e,'读取所选来源的攻击通道');assert.equal(e.calls.length,n);
  e.field('精确创建来源',origin);await press(e,'读取所选来源的攻击通道');assert.equal(control(e.dialog(),'通道 1 的数据库攻击候选').value,'');assert.equal(e.calls.at(-1).input.origin_reference_id,origin);assert.equal(Object.hasOwn(e.calls.at(-1).input,'gadget_id'),false);
});
test('fresh source must be read before preview; confirmation is separate and does not promote calibration',async()=>{
  const e=fixture();e.filled();await e.selectSource();await press(e,'确认所示档案指纹');assert.equal(e.calls.filter(c=>c.name==='compose_equipment_plan').length,0);await press(e,'读取并预览动作档案');await press(e,'确认所示档案指纹');await press(e,'应用档案草稿');
  assert.match(e.p.action_event_profiles[0].accepted_profile_fingerprint,/^[0-9a-f]{64}$/);assert.equal(e.p.action_event_profiles[0].lifecycle.status,'pending');assert.equal(e.p.periodic_attack_sources[0].status,'pending');
});
test('source change between preview and confirmation requires another explicit review',async()=>{
  const e=fixture();e.filled();await e.selectSource();await press(e,'读取并预览动作档案');e.changeSource();await press(e,'确认所示档案指纹');assert.match(e.dialog().textContent,/已变化/);assert.equal(e.p.action_event_profiles,undefined);await press(e,'确认所示档案指纹');await press(e,'应用档案草稿');assert.ok(e.p.action_event_profiles[0].accepted_profile_fingerprint);
});
for(const mutation of ['selection','file','close'])test(`late ${mutation} source query cannot publish stale candidates`,async()=>{
  const e=fixture();e.filled();let finish,input;e.world.callApi=(_,p)=>{input=clone(p);return new Promise(r=>finish=r);};const pending=press(e,'发现相关创建来源');
  if(mutation==='selection')e.field('实际命座',1);if(mutation==='file')e.world.RT.state.path='another.json';if(mutation==='close')press(e,'放弃并关闭');finish(e.sourceResponse(input));await pending;assert.equal(e.p.periodic_attack_sources,undefined);assert.match(e.world.lastStatus,/旧周期来源响应已忽略/);
});
for(const corruption of ['duplicate','identity','mode'])test(`malformed ${corruption} source response is not adopted`,async()=>{
  const e=fixture();e.filled();e.world.callApi=async(_,p)=>{const r=e.sourceResponse(p);if(corruption==='duplicate')r.data.creation_candidates.push(clone(r.data.creation_candidates[0]));if(corruption==='identity')r.data.selection.character_id++;if(corruption==='mode')r.data.mode='selected';return r;};await press(e,'发现相关创建来源');assert.match(e.dialog().textContent,/未采用/);assert.equal(control(e.dialog(),'精确创建来源').children.length,1);
});
test('decimal microseconds are checked exactly without multiplying floating point values',()=>{
  const e=fixture();for(const n of [null,.000249,.000001,.123456,1e9])assert.equal(e.PA.timeValid(n),true,String(n));for(const n of [undefined,0,-1,1e-7,.1234567,Infinity,NaN,1e9+1,true])assert.equal(e.PA.timeValid(n),false,String(n));
});
test('duplicate prototypes or candidate selections are rejected before composition',async()=>{
  const e=fixture();e.filled();press(e,'添加攻击通道');e.fillChannel(1,'slice');await press(e,'应用档案草稿');assert.equal(e.calls.length,0);assert.match(e.dialog().textContent,/独立标识/);e.field('共享伤害原型','blast',cards(e)[1]);await e.selectSource();e.field('通道 2 的数据库攻击候选',slice);await press(e,'应用档案草稿');assert.equal(e.calls.filter(c=>c.name==='compose_equipment_plan').length,0);assert.match(e.dialog().textContent,/重复采用/);
});
test('Q ability binding keeps all actual actions and does not expose multi sources to E editor',async()=>{
  const e=fixture();e.filled();await press(e,'应用档案草稿');e.AE.editBinding();e.field('绑定的战技或爆发能力','q');e.field('采用的动作档案',e.p.action_event_profiles[0].id);await press(e,'应用档案草稿');assert.equal(e.p.action_event_bindings[0].ability_id,'q');assert.equal(e.p.rotation_model.actions.length,3);
  const box=new Element();e.AE.render(box);assert.equal(all(box).filter(n=>n.tagName==='button'&&n.textContent.startsWith('选择数据库攻击来源')).length,0);assert.match(box.textContent,/新增周期动作档案/);
});
test('per-contact overrides retain explicit action/channel/ordinal and allow order-only entries',async()=>{
  const e=fixture();e.filled();await press(e,'应用档案草稿');e.p.action_event_bindings=[{ability_id:'q',profile_id:e.p.action_event_profiles[0].id}];e.PA.editOverride();e.field('本轴实际周期动作','q1');press(e,'添加接触例外');e.field('攻击通道',e.p.action_event_profiles[0].channels[0].channel_id);e.field('接触序号（从1开始）',2);e.field('本次命中例外','inherit');e.field('本次同刻顺序（空白不覆盖）',4);e.field('本次例外的依据','同刻顺序明确校准');await press(e,'应用档案草稿');const o=e.p.action_event_overrides[0];assert.equal(o.kind,e.PA.kind);assert.equal(o.action_id,'q1');assert.equal(o.contacts[0].ordinal,2);assert.equal(o.contacts[0].order,4);assert.equal(Object.hasOwn(o.contacts[0],'landed'),false);
});
test('removing a periodic profile requires explicit element restoration and preserves actions and prototypes',async()=>{
  const e=fixture();e.filled();await press(e,'应用档案草稿');e.p.action_event_bindings=[{ability_id:'q',profile_id:e.p.action_event_profiles[0].id}];e.AE.drop(0,0);const n=e.calls.length;await press(e,'应用档案草稿');assert.equal(e.calls.length,n);assert.match(e.dialog().textContent,/逐个明确补回/);e.field('原型 slice 的手工元素','cryo');await press(e,'应用档案草稿');assert.deepEqual(clone(e.p.periodic_attack_sources),[]);assert.deepEqual(clone(e.p.action_event_profiles),[]);assert.deepEqual(clone(e.p.action_event_bindings),[]);assert.equal(e.p.manual_template.hits.length,2);assert.equal(e.p.manual_template.hits[0].element,'cryo');assert.equal(e.p.manual_template.hits[0].multiplier,undefined);assert.equal(e.p.rotation_model.actions.length,3);
});
test('missing periodic compilation prevents draft application',async()=>{
  const e=fixture();e.filled();e.world.callApi=async(_,p)=>{const r=e.response(p);r.data.periodic_attack_compilations=[];return r;};await press(e,'应用档案草稿');assert.equal(e.p.action_event_profiles,undefined);assert.match(e.dialog().textContent,/完整周期来源回执/);
});
test('periodic result view shows source/calibration boundary and channel ordinals without editable controls',()=>{
  const e=fixture(),box=new Element();e.PA.renderReceipt(box,{periodic_attack_compilations:[{status:'ready',issues:[],query:{selected_candidates:[{channel_kind:'periodic_callback_attack',projection:{element:{value:'cryo'},gauge:{value:1},parameter:{value:2},timer:{status:'ready',seconds:.25}}}]}}]});assert.match(box.textContent,/来源周期 0.25 秒/);assert.match(box.textContent,/不证明动作能原生启动/);assert.equal(all(box).some(n=>['input','select','textarea'].includes(n.tagName)),false);
});
test('GUI-created unknown draft passes real Python ownership and native field validation',async()=>{
  const e=fixture();e.filled();const script=`import json,sys
from genshin_app.equipment.action_event_schema import validate_action_event_fields
from genshin_app.equipment.periodic_schema import validate_periodic_sources
p=json.load(sys.stdin)
validate_action_event_fields(p)
validate_periodic_sources(p)
print('valid')`;
  e.world.callApi=async(_,p)=>{const result=require('node:child_process').execFileSync(process.env.PYTHON||'python',['-X','utf8','-c',script],{cwd:path.join(__dirname,'..'),input:JSON.stringify(p),encoding:'utf8'});assert.equal(result.trim(),'valid');return e.response(p);};
  await press(e,'应用档案草稿');assert.equal(e.p.periodic_attack_sources.length,1);assert.equal(e.p.action_event_profiles[0].channels[0].schedule.count,null);
});
test('finite periodic channel writes canonical counter tag and complete structured verification',async()=>{
  const e=fixture();e.filled();let card=cards(e)[0];e.field('元素附着模式','sequence',card);card=cards(e)[0];e.field('附着计数标签（ICD Tag）','periodic.q',card);e.field('逐段结构化证据状态','confirmed',card);card=cards(e)[0];
  for(const [label,value] of [['证据类型与定位（URL、本地路径或原始 pointer）','fixture://periodic/q'],['适用游戏版本或固定 commit','fixture-commit'],['检索/复核日期（YYYY-MM-DD）','2026-10-18'],['具体攻击 component_scope 与共享范围','该周期通道全部接触共享 periodic.q']])e.field(label,value,card);
  await press(e,'应用档案草稿');const mapping=e.p.periodic_attack_sources[0].channels[0];assert.equal(mapping.application_mode,'sequence');assert.equal(mapping.counter_tag,'periodic.q');assert.equal(mapping.verification.status,'confirmed');assert.deepEqual(clone(mapping.verification.confirmed_fields),['application_mode','counter_tag','shared_scope']);assert.equal(Object.hasOwn(mapping,'shared_group'),false);
});
test('database-confirmed periodic sequence reuses its exact counter tag without duplicate external evidence',async()=>{
  const e=fixture();e.world.callApi=async(name,input)=>{if(name!=='query_runtime')return e.response(input);const r=e.sourceResponse(input);
    if(input.origin_reference_id)r.data.candidates.forEach(candidate=>{candidate.projection={application_identity:{status:'ready',tag:'periodic.q',evidence:'upstream_attenuation_tag',source:{source_path:'fixture.json',json_pointer:'/attackInfo/attenuationTag'}},attenuation:{group:'Default'}};});return r;};
  e.filled();await e.selectSource();e.field('元素附着模式','sequence',cards(e)[0]);let card=cards(e)[0];
  assert.equal(control(card,'附着计数标签（ICD Tag）').value,'periodic.q');assert.match(card.textContent,/数据库已确认计数身份（无需重复外部证据）/);
  e.field('附着计数标签（ICD Tag）','wrong',card);await press(e,'应用档案草稿');assert.equal(e.p.periodic_attack_sources,undefined);assert.match(e.dialog().textContent,/必须与数据库确认的 attenuationTag/);
  e.field('附着计数标签（ICD Tag）','periodic.q',cards(e)[0]);await press(e,'应用档案草稿');const mapping=e.p.periodic_attack_sources[0].channels[0];
  assert.equal(mapping.application_mode,'sequence');assert.equal(mapping.counter_tag,'periodic.q');assert.equal(Object.hasOwn(mapping,'verification'),false);
});
test('finite periodic channel without complete evidence is blocked while pending needs no fake evidence',async()=>{
  const e=fixture();e.filled();let card=cards(e)[0];e.field('元素附着模式','sequence',card);card=cards(e)[0];e.field('附着计数标签（ICD Tag）','periodic.q',card);e.field('逐段结构化证据状态','confirmed',card);await press(e,'应用档案草稿');assert.equal(e.p.periodic_attack_sources,undefined);assert.match(e.dialog().textContent,/结构化证据/);
  e.field('元素附着模式','pending',cards(e)[0]);await press(e,'应用档案草稿');const mapping=e.p.periodic_attack_sources[0].channels[0];assert.equal(mapping.application_mode,'pending');assert.equal(Object.hasOwn(mapping,'verification'),false);
});
for(const mode of ['no_icd','no_application'])test(`periodic ${mode} hides sequence fields and requires positive structured evidence`,async()=>{
  const e=fixture();e.filled();e.field('元素附着模式',mode,cards(e)[0]);let card=cards(e)[0];assert.equal(control(card,'附着计数标签（ICD Tag）'),undefined);assert.match(card.textContent,/计数标签与规则组：此附着模式不适用/);await press(e,'应用档案草稿');assert.equal(e.p.periodic_attack_sources,undefined);assert.match(e.dialog().textContent,/结构化逐段证据/);
  e.field('逐段结构化证据状态','confirmed',card);card=cards(e)[0];for(const [label,value] of [['证据类型与定位（URL、本地路径或原始 pointer）',`fixture://${mode}`],['适用游戏版本或固定 commit','fixture-commit'],['检索/复核日期（YYYY-MM-DD）','2026-10-18'],['具体攻击 component_scope 与共享范围',`该通道逐段确认 ${mode}`]])e.field(label,value,card);await press(e,'应用档案草稿');const mapping=e.p.periodic_attack_sources[0].channels[0];assert.equal(mapping.application_mode,mode);assert.equal(Object.hasOwn(mapping,'counter_tag'),false);assert.deepEqual(clone(mapping.verification.confirmed_fields),['application_mode','shared_scope']);
});
test('legacy periodic shared_group is visibly migrated and never coexists with counter_tag on apply',async()=>{
  const e=fixture();e.filled();await press(e,'应用档案草稿');const old=e.p.periodic_attack_sources[0].channels[0];delete old.application_mode;old.shared_group='legacy.periodic';delete old.counter_tag;e.PA.editProfile(0);assert.match(e.dialog().textContent,/已加载旧计数标签 legacy\.periodic/);
  e.field('元素附着模式','sequence',cards(e)[0]);let card=cards(e)[0];assert.equal(control(card,'附着计数标签（ICD Tag）').value,'legacy.periodic');e.field('逐段结构化证据状态','confirmed',card);card=cards(e)[0];for(const [label,value] of [['证据类型与定位（URL、本地路径或原始 pointer）','fixture://legacy-periodic'],['适用游戏版本或固定 commit','fixture-commit'],['检索/复核日期（YYYY-MM-DD）','2026-10-18'],['具体攻击 component_scope 与共享范围','旧通道审阅后迁移']])e.field(label,value,card);
  await press(e,'应用档案草稿');const mapping=e.p.periodic_attack_sources[0].channels[0];assert.equal(mapping.application_mode,'sequence');assert.equal(mapping.counter_tag,'legacy.periodic');assert.equal(Object.hasOwn(mapping,'shared_group'),false);
});
test('periodic receipt keeps backend research template, completed audit and exact per-channel work items on the first screen',()=>{
  const e=fixture(),box=new Element(),task=channel=>({attack_identity:{character_id:10000002,actor:'a',ability_name:'fixture.burst',action_event_profile_id:'periodic-profile',profile_id:'periodic-profile',channel_id:channel,component_profile_id:`component-${channel}`,parent_pointer:`/burst/${channel}`,version_line:'release'},next_action:'核对周期分量',known_fields:{application_mode:'sequence'},missing_fields:['counter_tag'],conflicts:['tag mismatch'],local_checks:['candidate selected'],targeted_research_questions:['该分量使用哪个 attenuationTag？'],keywords:['periodic','attenuationTag'],database_patch_targets:['AttackAttenuationExcelConfigData'],research_report:{report_version:1,execution_authority:'none',actual_queries:[`query-${channel}`],sources_checked:[{locator:`fixture://${channel}`,version:'v1',accessed_at:'2026-10-18',supported_fields:['counter_tag'],decision:'inconclusive',reason:'共享范围未确认'}],adopted_findings:[],rejected_findings:['不能按爆发类别推断'],remaining_unknowns:['shared_scope'],database_patch_plan:[{target:`periodic.${channel}`,fields:['counter_tag'],evidence_locators:[`fixture://${channel}`],status:'deferred',note:'等待逐段证据'}]},research_report_template:{actual_queries:[],sources_checked:[],remaining_unknowns:[],database_patch_plan:[]}});e.PA.renderReceipt(box,{periodic_attack_compilations:[{status:'pending',query:{selected_candidates:[]},issues:[],verification_tasks:[task('channel-a'),task('channel-b')]}]});
  assert.match(box.textContent,/character_id=10000002/);assert.match(box.textContent,/ability_name=fixture.burst/);assert.match(box.textContent,/channel_id=channel-a/);assert.match(box.textContent,/channel_id=channel-b/);assert.match(box.textContent,/parent_pointer=\/burst\/channel-a/);assert.match(box.textContent,/核对周期分量/);assert.match(box.textContent,/actual_queries/);assert.match(box.textContent,/已保存的 Agent 查证记录（无执行权）/);assert.match(box.textContent,/执行权：none/);assert.match(box.textContent,/query-channel-a/);assert.match(box.textContent,/支持字段 counter_tag/);assert.match(box.textContent,/拒绝结论：不能按爆发类别推断/);assert.match(box.textContent,/不能代替 verification/);assert.match(box.textContent,/原始核实工作单载荷/);
});
test('removing one established channel restores only its explicit element before relinquishing ownership',async()=>{
  const e=fixture();e.filled();press(e,'添加攻击通道');e.fillChannel(1,'blast','single');await press(e,'应用档案草稿');e.PA.editProfile(0);button(cards(e)[1],'⋯').emit('click');e.world.lastContextMenu.items[0].run();const n=e.calls.length;await press(e,'应用档案草稿');assert.equal(e.calls.length,n);assert.match(e.dialog().textContent,/退出来源接管/);
  e.field('退出来源接管的原型元素：独立终结','cryo');await press(e,'应用档案草稿');assert.equal(e.p.action_event_profiles[0].channels.length,1);assert.equal(e.p.periodic_attack_sources[0].channels.length,1);assert.equal(e.p.manual_template.hits[1].element,'cryo');assert.equal(e.p.manual_template.hits[0].element,undefined);
});
test('saved selected origins are re-read before editing and applying calibration',async()=>{
  const e=fixture();e.filled();await e.selectSource();await press(e,'应用档案草稿');e.PA.editProfile(0);e.field('实例结束距动作开始（秒）',2);const n=e.calls.length;await press(e,'应用档案草稿');assert.equal(e.calls.length,n);assert.match(e.dialog().textContent,/请先读取当前创建来源/);
  await press(e,'读取所选来源的攻击通道');await press(e,'应用档案草稿');assert.equal(e.p.action_event_profiles[0].lifecycle.instance_end_offset_seconds,2);
});

function hitPanel(e,index=0){
  const w=e.world;w.hdr=text=>w.UI.el('h3',text);w.DS.options=(_,values)=>values.map(value=>({value,label:value}));
  w.DS.characters=()=>[{value:'a',label:'角色甲'}];w.DS.hasChinese=value=>/[\u4e00-\u9fff]/.test(value||'');
  w.RT.state.enums={elements:['cryo'],damage_systems:['classic'],scaling_stats:['atk'],reaction_types:{classic:['amplify']}};
  GUI_TEST_RUNTIME.loadWebFile(w, path.join(__dirname,'../src/genshin_app/gui/web/panel.js'));
  const box=new Element();w.renderHitPanel(box,index);return box;
}

test('real hit panel source button opens the owning periodic editor at its actual profile index',async()=>{
  const e=fixture();e.filled();await press(e,'应用档案草稿');
  e.p.action_event_profiles.unshift({id:'other-single',prototype_hit_id:'blast'});
  const before=e.PL.currentInputJson(),box=hitPanel(e),open=button(box,'查看或编辑数据库攻击来源');
  assert.ok(open);assert.equal(control(box,'倍率').disabled,true);open.emit('click');
  assert.ok(e.dialog(),'periodic source editor remains open');assert.match(e.dialog().textContent,/编辑周期动作档案/);
  assert.equal(control(e.dialog(),'数据库角色身份').value,'10000002');
  assert.equal(control(cards(e)[0],'共享伤害原型').value,'slice');
  assert.ok(button(e.dialog(),'读取所选来源的攻击通道'));assert.equal(button(e.dialog(),'读取攻击候选'),undefined);
  press(e,'放弃并关闭');assert.equal(e.PL.currentInputJson(),before);
});

test('real hit panel source button retains the single-hit metadata editor route',()=>{
  const e=fixture();e.p.action_event_profiles=[{id:'single',prototype_hit_id:'slice',application_profile_id:'application'}];
  e.p.attack_metadata_sources=[{action_event_profile_id:'single',version_line:'release',selection:{character_id:10000002,slot:'skill',constellation:0},candidate_id:null,shared_group:'e.shared',purpose:'single_stat_damage_multiplier',status:'pending',source:'单次来源草稿'}];
  delete e.p.manual_template.hits[0].element;delete e.p.manual_template.hits[0].multiplier;
  const before=e.PL.currentInputJson(),box=hitPanel(e),open=button(box,'查看或编辑数据库攻击来源');
  assert.ok(open);open.emit('click');assert.match(e.dialog().textContent,/选择数据库攻击来源/);
  assert.equal(control(e.dialog(),'数据库角色身份').value,'10000002');
  assert.ok(button(e.dialog(),'读取攻击候选'));assert.equal(button(e.dialog(),'读取所选来源的攻击通道'),undefined);
  press(e,'放弃并关闭');assert.equal(e.PL.currentInputJson(),before);
});
