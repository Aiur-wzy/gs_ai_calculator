const GUI_TEST_RUNTIME = require('./helpers/gui_runtime.cjs');
const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),vm=require('node:vm'),crypto=require('node:crypto');
const helpers={module:{exports:{}},require,__dirname,console};
vm.runInNewContext(fs.readFileSync(path.join(__dirname,'gui_action_events.test.cjs'),'utf8').split('\ntest(')[0]+'\nmodule.exports={fixture,press,control,button,edit,Element};',helpers);
const {fixture:base,press,control,button,Element}=helpers.module.exports,clone=value=>JSON.parse(JSON.stringify(value));
function setup(){
  const e=base(),w=e.world,p=e.p;e.PL.state.caps.attack_metadata_contract={compiler:'database-attack-metadata-v1'};
  p.action_event_profiles=[{id:'profile',kind:'calibrated_single_contact_v1',prototype_hit_id:'prototype',variant:'tap',calibration:{offset_seconds:.5,precision_seconds:.01,status:'assumed',source:'工程校准'},application_profile_id:'ice',contact_profile_id:'safe',default_landed:true,self_sampling:'current',accepted_profile_fingerprint:null}];
  p.action_event_bindings=[{ability_id:'e',profile_id:'profile'}];p.action_event_overrides=[];
  p.elemental_sources=[{profile_id:'ice',shared_group:'declared-shared'}];p.hit_parameter_sources[0].param_index=0;
  GUI_TEST_RUNTIME.loadWebFile(w, path.join(__dirname,'../src/genshin_app/gui/web/attack-metadata.js'));
  const projection={parameter:{value:4.3,param_index:0},element:{value:'cryo'},gauge:{value:2,raw_durability:50,tax_applied:false},attenuation:{group:'default'}};
  const queried={status:'pending',whole_source_status:'partial',candidates:[{candidate_id:'attack.1',status:'ready',projection}],issues:[]};
  const calls=[];let stamp=1;
  const response=plan=>{const r=e.response(plan),records=plan.attack_metadata_sources||[];
    r.data.attack_metadata_compilations=records.map(source=>({action_event_profile_id:source.action_event_profile_id,status:'ready',projection:clone(projection),issues:[]}));
    r.data.action_event_compilations.forEach(record=>{const profile=clone(plan.action_event_profiles.find(p=>p.id===record.profile_id));delete profile.accepted_profile_fingerprint;
      const fp=crypto.createHash('sha256').update(JSON.stringify([stamp,profile,records])).digest('hex');record.required_confirmation={accepted_profile_fingerprint:fp};});return r;};
  w.callApi=async(name,payload)=>{calls.push({name,payload:clone(payload)});return name==='query_runtime'?{code:0,data:clone(queried)}:response(payload);};
  const open=()=>w.AM.edit('profile');
  const filled=async()=>{open();e.field('元素附着模式','sequence');e.field('用途与逐攻击段附着依据（版本、来源定位、检索日期与采纳理由）','单ATK用途及本次共享计数的明确声明');await press(e,'读取攻击候选');e.field('本次采用的精确攻击候选','attack.1');};
  return{...e,AM:w.AM,open,filled,queried,calls,response,changeSource:()=>stamp++};
}
async function converted(e){await e.filled();await press(e,'读取并预览动作档案');await press(e,'确认所示档案指纹');await press(e,'应用档案草稿');}

test('conversion previews exact removed fields and writes only native ownership declarations',async()=>{
  const e=setup(),before=clone(e.p);await e.filled();assert.match(e.dialog().textContent,/原型的手工元素/);assert.match(e.dialog().textContent,/独立倍率来源/);
  assert.deepEqual(clone(e.p),before);await press(e,'读取并预览动作档案');await press(e,'确认所示档案指纹');assert.deepEqual(clone(e.p),before);
  await press(e,'应用档案草稿');assert.equal(e.p.attack_metadata_sources.length,1);assert.equal(e.p.attack_metadata_sources[0].candidate_id,'attack.1');assert.equal(e.p.attack_metadata_sources[0].status,'pending');
  assert.equal(Object.hasOwn(e.p.manual_template.hits[0],'element'),false);assert.equal(Object.hasOwn(e.p.manual_template.hits[0],'multiplier'),false);
  assert.deepEqual(clone(e.p.hit_parameter_sources),[]);assert.deepEqual(clone(e.p.elemental_sources),[]);assert.deepEqual(clone(e.p.mechanics_model.elemental_model.profiles),[]);
  assert.deepEqual(clone(e.p.rotation_model),before.rotation_model);assert.equal(e.p.mechanics_model.injected,undefined);assert.ok(e.p.action_event_profiles[0].accepted_profile_fingerprint);
});
test('reading a source never automatically chooses its first candidate or confirms gameplay',async()=>{
  const e=setup();e.open();e.field('用途与逐攻击段附着依据（版本、来源定位、检索日期与采纳理由）','待核实');await press(e,'读取攻击候选');assert.equal(control(e.dialog(),'本次采用的精确攻击候选').value,'');
  await press(e,'应用档案草稿');assert.equal(e.p.attack_metadata_sources[0].candidate_id,null);assert.equal(e.p.attack_metadata_sources[0].status,'pending');assert.equal(e.p.attack_metadata_sources[0].application_mode,'pending');
  assert.equal(Object.hasOwn(e.p.attack_metadata_sources[0],'shared_group'),false);assert.equal(e.p.action_event_profiles[0].accepted_profile_fingerprint,null);
});
test('missing explicit actor and constellation do not become valid defaults',async()=>{
  const e=setup();delete e.p.hit_parameter_sources;e.open();e.field('用途与逐攻击段附着依据（版本、来源定位、检索日期与采纳理由）','资料待核实');await press(e,'读取攻击候选');assert.equal(e.calls.length,0);assert.match(e.dialog().textContent,/明确数据库角色/);
  e.field('数据库角色身份',10000002);await press(e,'读取攻击候选');assert.equal(e.calls.length,0);assert.match(e.dialog().textContent,/填写实际命座/);
});
test('changed selection invalidates the candidate list and must be freshly queried',async()=>{
  const e=setup();await e.filled();e.field('数据库角色身份',10000047);assert.equal(control(e.dialog(),'本次采用的精确攻击候选'),undefined);
  await press(e,'应用档案草稿');assert.equal(e.p.attack_metadata_sources,undefined);assert.match(e.dialog().textContent,/先读取当前技能/);
  await press(e,'读取攻击候选');assert.equal(control(e.dialog(),'本次采用的精确攻击候选').value,'');
});
test('extra server plan fields and mutations never replace native gameplay input',async()=>{
  const e=setup();await e.filled();const rotation=clone(e.p.rotation_model);
  e.world.callApi=async(_,plan)=>{const r=e.response(plan);plan.rotation_model.actions[0].start=123;r.data.plan.attack_metadata_sources=[{injected:true}];r.data.plan.manual_template.hits[0].element='pyro';return r;};
  await press(e,'应用档案草稿');assert.deepEqual(clone(e.p.rotation_model),rotation);assert.equal(e.p.attack_metadata_sources[0].injected,undefined);assert.equal(e.p.manual_template.hits[0].element,undefined);
});
test('source changed during confirmation requires a new review before acceptance',async()=>{
  const e=setup();await e.filled();await press(e,'读取并预览动作档案');e.changeSource();await press(e,'确认所示档案指纹');assert.match(e.dialog().textContent,/依赖已变化/);
  assert.equal(e.p.attack_metadata_sources,undefined);await press(e,'确认所示档案指纹');await press(e,'应用档案草稿');assert.ok(e.p.action_event_profiles[0].accepted_profile_fingerprint);
});
for(const mutation of ['plan','input','epoch','path','sha','draft','close'])test(`late metadata ${mutation} query cannot update the dialog or plan`,async()=>{
  const e=setup();e.open();let finish;e.world.callApi=()=>new Promise(resolve=>finish=resolve);const pending=press(e,'读取攻击候选');
  if(mutation==='plan')e.PL.state.plan=clone(e.p);if(mutation==='input')e.p.manual_template.meta={name:'changed'};if(mutation==='epoch')e.PL.state.sourceEpoch++;
  if(mutation==='path')e.world.RT.state.path='changed.json';if(mutation==='sha')e.PL.state.fileSha='new-sha';if(mutation==='draft')e.PL.state.draft=clone(e.p);if(mutation==='close')press(e,'放弃并关闭');
  finish({code:0,data:e.queried});await pending;assert.equal(e.p.attack_metadata_sources,undefined);if(mutation!=='close')assert.equal(control(e.dialog(),'本次采用的精确攻击候选'),undefined);
});
test('out-of-order source reads retain only the current selection',async()=>{
  const e=setup();e.open();const waiting=[];e.world.callApi=(_,p)=>new Promise(resolve=>waiting.push({p:clone(p),resolve}));
  const first=press(e,'读取攻击候选');e.field('数据库角色身份',10000047);const second=press(e,'读取攻击候选');
  const latest=clone(e.queried);latest.candidates[0].candidate_id='new-candidate';waiting[1].resolve({code:0,data:latest});await second;
  waiting[0].resolve({code:0,data:e.queried});await first;const values=control(e.dialog(),'本次采用的精确攻击候选').children.map(c=>c.value);assert.ok(values.includes('new-candidate'));assert.ok(!values.includes('attack.1'));
});
test('missing metadata receipt blocks applying a superficially valid action receipt',async()=>{
  const e=setup();await e.filled();e.world.callApi=async(_,plan)=>{const r=e.response(plan);delete r.data.attack_metadata_compilations;return r;};await press(e,'应用档案草稿');assert.equal(e.p.attack_metadata_sources,undefined);assert.match(e.dialog().textContent,/完整攻击来源回执/);
});
test('source removal requires an explicit element and leaves other gameplay for later repair',async()=>{
  const e=setup();await converted(e);e.open();await press(e,'改为移除该攻击来源');await press(e,'应用档案草稿');assert.equal(e.p.attack_metadata_sources.length,1);assert.match(e.dialog().textContent,/明确选择原型元素/);
  e.field('移除来源时补回的原型元素','cryo');await press(e,'应用档案草稿');assert.deepEqual(clone(e.p.attack_metadata_sources),[]);assert.equal(e.p.manual_template.hits[0].element,'cryo');assert.equal(e.p.action_event_profiles[0].accepted_profile_fingerprint,null);assert.equal(e.p.manual_template.hits[0].multiplier,undefined);assert.equal(e.p.rotation_model.actions.length,3);
});
test('owned references cannot be changed or removed through the old profile editor',async()=>{
  const e=setup();await converted(e);e.AE.editProfile(0);assert.equal(control(e.dialog(),'附着档案').tagName,'span');assert.equal(control(e.dialog(),'共享命中原型').tagName,'span');await press(e,'放弃并关闭');
  e.AE.drop(0,0);assert.match(e.world.lastStatus,/先在攻击来源编辑器/);assert.equal(e.p.action_event_profiles.length,1);
});
test('read-only result presents raw GU and the remaining declaration boundary',()=>{
  const e=setup(),box=new Element();e.AM.renderReceipt(box,{attack_metadata_compilations:[{status:'ready',projection:{element:{value:'cryo'},gauge:{value:2},parameter:{value:4.3}}}]});
  assert.match(box.textContent,/原始附着量：2 GU/);assert.match(box.textContent,/底税由元素引擎处理一次/);assert.match(box.textContent,/整技能仅部分覆盖/);
  assert.match(box.textContent,/附着计数标签（ICD Tag）：待核实/);assert.match(box.textContent,/附着规则组：待核实/);
});

test('explicit no-ICD mode removes counter input and is preserved as a sourced declaration',async()=>{
  const e=setup();e.open();e.field('元素附着模式','no_icd');assert.equal(control(e.dialog(),'附着计数标签（ICD Tag）'),undefined);
  assert.match(e.dialog().textContent,/每次命中均允许附着/);e.field('用途与逐攻击段附着依据（版本、来源定位、检索日期与采纳理由）','逐攻击组件无 ICD 证据');
  await press(e,'读取攻击候选');e.field('本次采用的精确攻击候选','attack.1');await press(e,'应用档案草稿');
  assert.equal(e.p.attack_metadata_sources[0].application_mode,'no_icd');assert.equal(Object.hasOwn(e.p.attack_metadata_sources[0],'shared_group'),false);
});

test('application receipts distinguish no ICD, no application and pending evidence',()=>{
  const e=setup(),box=new Element();e.AM.renderReceipt(box,{attack_metadata_compilations:[
    {status:'ready',application_mode:{value:'no_icd'},projection:{}},
    {status:'ready',application_mode:{value:'no_application'},projection:{}},
    {status:'pending',application_mode:{value:'pending'},projection:{}}]});
  assert.match(box.textContent,/每次均附着（无 ICD）/);assert.match(box.textContent,/明确不施加元素/);
  assert.match(box.textContent,/Agent须查询这一具体攻击段/);assert.match(box.textContent,/附着规则组：不适用/);
});

test('confirmed attenuation tag is visible and a conflicting plan counter tag is rejected before preview',async()=>{
  const e=setup(),projection=e.queried.candidates[0].projection;
  projection.application_identity={status:'ready',tag:'Fixture_Icespine_Counter',evidence:'upstream_attenuation_tag',source:{source_path:'fixture.json',json_pointer:'/attackInfo/attenuationTag'}};
  projection.attack_tag={status:'observed',value:'Elemental_Art',application_identity:false};
  e.open();e.field('元素附着模式','sequence');e.field('用途与逐攻击段附着依据（版本、来源定位、检索日期与采纳理由）','逐攻击段来源');await press(e,'读取攻击候选');e.field('本次采用的精确攻击候选','attack.1');
  assert.match(e.dialog().textContent,/数据库确认 · 附着计数标签（ICD Tag）：Fixture_Icespine_Counter/);
  assert.match(e.dialog().textContent,/attackTag：Elemental_Art；它不是计数标签/);
  assert.equal(control(e.dialog(),'附着计数标签（ICD Tag）').value,'Fixture_Icespine_Counter');
  e.field('附着计数标签（ICD Tag）','wrong-counter');
  await press(e,'应用档案草稿');assert.equal(e.p.attack_metadata_sources,undefined);assert.match(e.dialog().textContent,/必须与数据库确认的 attenuationTag/);
  e.field('附着计数标签（ICD Tag）','Fixture_Icespine_Counter');await press(e,'应用档案草稿');
  assert.equal(e.p.attack_metadata_sources[0].counter_tag,'Fixture_Icespine_Counter');assert.equal(Object.hasOwn(e.p.attack_metadata_sources[0],'shared_group'),false);
});

test('the ordinary hit panel never recreates owned element or multiplier from a stale cache',()=>{
  const context={module:{exports:{}},require,__dirname,console};
  vm.runInNewContext(fs.readFileSync(path.join(__dirname,'gui_hit_parameters_review.test.cjs'),'utf8').split('\ntest(')[0]+'\nmodule.exports={setup,control,Element,press};',context);
  const h=context.module.exports,e=h.setup(),p=e.PL.state.plan,hit=p.manual_template.hits[0];
  p.plan_version=3;p.action_event_profiles=[{id:'owned',prototype_hit_id:hit.id}];p.attack_metadata_sources=[{action_event_profile_id:'owned'}];
  delete hit.element;delete hit.multiplier;p.hit_parameter_sources=[];let opened;
  e.world.AM={edit:id=>opened=id};p.resolved_template=clone(p.manual_template);p.resolved_template.hits[0].element='pyro';p.resolved_template.hits[0].multiplier=999;
  e.RT.syncRevision(true); // Establish the loaded native state before testing forbidden edits.
  const before=e.PL.currentInputJson(),box=new h.Element();e.world.renderHitPanel(box,0);
  for(const [label,value] of [['元素','cryo'],['倍率','2'],['角色','wrong'],['倍率依据','hp'],['伤害体系','lunar']]){
    const input=h.control(box,label);assert.equal(input.disabled,true);input.value=value;input.emit('change');}
  assert.equal(e.PL.currentInputJson(),before);h.press(box,'查看或编辑数据库攻击来源');assert.equal(opened,'owned');
});

test('confirmed non-default application writes complete structured evidence while pending needs none',async()=>{
  const e=setup();e.open();e.field('元素附着模式','no_icd');e.field('单攻击力倍率与共享关系的核实状态','confirmed');
  e.field('用途与逐攻击段附着依据（版本、来源定位、检索日期与采纳理由）','逐段核实结论');
  for(const [label,value] of [['逐段证据定位（URL、本地资料路径或原始 pointer）','fixture://component/1'],['适用游戏版本或固定提交','fixture-v1'],
    ['检索/复核日期（YYYY-MM-DD）','2026-10-18'],['具体攻击分量与共享范围','仅当前攻击段']])e.field(label,value);
  await press(e,'读取攻击候选');e.field('本次采用的精确攻击候选','attack.1');await press(e,'应用档案草稿');
  const row=e.p.attack_metadata_sources[0];assert.equal(row.application_mode,'no_icd');assert.equal(row.verification.status,'confirmed');
  assert.deepEqual(clone(row.verification.confirmed_fields),['application_mode','shared_scope']);assert.equal(Object.hasOwn(row,'counter_tag'),false);
  const pending=setup();pending.open();pending.field('用途与逐攻击段附着依据（版本、来源定位、检索日期与采纳理由）','待研究');await press(pending,'读取攻击候选');await press(pending,'应用档案草稿');
  assert.equal(pending.p.attack_metadata_sources[0].application_mode,'pending');assert.equal(Object.hasOwn(pending.p.attack_metadata_sources[0],'verification'),false);
});

test('confirmed external conclusion without structured evidence is blocked before composition',async()=>{
  const e=setup();e.open();e.field('元素附着模式','no_application');e.field('单攻击力倍率与共享关系的核实状态','confirmed');
  e.field('用途与逐攻击段附着依据（版本、来源定位、检索日期与采纳理由）','自由文本不足以确认');await press(e,'读取攻击候选');
  await press(e,'应用档案草稿');assert.equal(e.p.attack_metadata_sources,undefined);assert.match(e.dialog().textContent,/必须填写证据定位/);
});

test('legacy metadata counter field migrates to canonical counter_tag on apply',async()=>{
  const e=setup();e.p.attack_metadata_sources=[{action_event_profile_id:'profile',version_line:'release',selection:{character_id:10000002,slot:'skill',constellation:0},candidate_id:'attack.1',application_mode:'sequence',shared_group:'legacy.counter',purpose:'single_stat_damage_multiplier',status:'pending',source:'旧来源'}];
  e.open();assert.equal(control(e.dialog(),'附着计数标签（ICD Tag）').value,'legacy.counter');await press(e,'读取攻击候选');await press(e,'应用档案草稿');
  const row=e.p.attack_metadata_sources[0];assert.equal(row.counter_tag,'legacy.counter');assert.equal(Object.hasOwn(row,'shared_group'),false);
});

test('verification work order exposes exact identity and actionable fields before raw payload',()=>{
  const e=setup(),box=new Element(),task={attack_identity:{character_id:10000002,actor:'actor',slot:'skill',skill_id:42,candidate_id:'candidate',component_profile_id:'profile',prototype_hit_id:'hit',version_line:'release'},
    known_fields:{element:'cryo'},missing_fields:['counter_tag'],conflicts:[{field:'counter_tag'}],local_checks:[{kind:'raw_pointer',reference:'fixture#/attack'}],
    targeted_research_questions:['这一段使用哪个 attenuationTag？'],keywords:['candidate','attenuationTag'],database_patch_targets:['attack_component.counter_tag'],acceptance_required:'逐段正向证据',
    uses:[{actor:'actor',character_id:10000002,slot:'burst',skill_id:43,candidate_id:'periodic.1',ability_id:'ability',action_id:'action',hit_id:'hit',pattern_id:'pattern',application_event_id:'application',action_event_profile_id:'event-profile',channel_id:'channel',component:'component',variant:'tap',constellation:2,target_id:'enemy',version_line:'release'}],unresolved_identity_fields:['skill_id'],
    research_report:{report_version:1,execution_authority:'none',actual_queries:['exact component ICD'],sources_checked:[{locator:'fixture://source',version:'v1',accessed_at:'2026-10-18',supported_fields:['application_mode','counter_tag'],decision:'inconclusive',reason:'共享范围仍缺证据'}],adopted_findings:['当前段为冰伤'],rejected_findings:['不能按技能类别推断'],remaining_unknowns:['shared scope'],database_patch_plan:[{target:'attack_component.candidate',fields:['counter_tag'],evidence_locators:['fixture://source'],status:'deferred',note:'待共享范围复核'}]},
    research_report_template:{actual_queries:'实际查询',sources_reviewed:'查看来源',version_and_date:'版本/日期',supported_fields:'支持字段',adoption_reason:'采用/拒绝原因',remaining_unknowns:'仍未知',database_patch_plan:'回填计划'}};
  e.AM.renderReceipt(box,{attack_metadata_compilations:[{status:'pending',application_mode:{value:'pending'},projection:{},application_identity:{status:'pending',next_action:'核实具体攻击段'},verification_task:task,issues:[]}]});
  const text=box.textContent;for(const expected of ['character_id=10000002','actor=actor','slot=skill','skill_id=42','candidate_id=candidate','component_profile_id=profile','prototype_hit_id=hit','version_line=release','下一步：核实具体攻击段','缺失：counter_tag','本地检查','定向检索问题','检索关键词','数据库回填目标：attack_component.counter_tag','具体使用点（1）','slot=burst','skill_id=43','candidate_id=periodic.1','ability_id=ability','action_id=action','pattern_id=pattern','application_event_id=application','action_event_profile_id=event-profile','channel_id=channel','component=component','variant=tap','constellation=2','target_id=enemy','version_line=release','未解析身份字段：skill_id','已保存的 Agent 查证记录','报告版本：1；执行权：none','不能代替 verification','exact component ICD','fixture://source','访问 2026-10-18','支持字段 application_mode；counter_tag','处理 inconclusive','共享范围仍缺证据','采纳结论：当前段为冰伤','拒绝结论：不能按技能类别推断','shared scope','字段 counter_tag','证据 fixture://source','待共享范围复核','attack_component.candidate','research_report_template','模板用于记录实际检索与回填计划','actual_queries：实际查询','remaining_unknowns：仍未知','原始核实工作单载荷'])assert.match(text,new RegExp(expected));
});

test('verification work order makes an empty direct-source use list explicit',()=>{
  const e=setup(),box=new Element();e.AM.renderVerificationTask(box,{attack_identity:{profile_id:'direct'},known_fields:{},missing_fields:[],conflicts:[],local_checks:[],targeted_research_questions:[],keywords:[],database_patch_targets:[],uses:[],unresolved_identity_fields:['actor','action_id']});
  assert.match(box.textContent,/后端未找到引用/);assert.match(box.textContent,/未解析身份字段：actor、action_id/);
});
