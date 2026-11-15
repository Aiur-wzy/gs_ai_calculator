const GUI_TEST_RUNTIME = require('./helpers/gui_runtime.cjs');
const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');
const {spawn}=require('node:child_process'),readline=require('node:readline'),{webcrypto,createHash}=require('node:crypto');
const helper={module:{exports:{}},require,__dirname,console};
vm.runInNewContext(fs.readFileSync(path.join(__dirname,'gui_hit_parameters_review.test.cjs'),'utf8').split('\ntest(')[0]+'\nmodule.exports={setup,press,control,Element};',helper);
const {setup,press,control,Element}=helper.module.exports,clone=x=>JSON.parse(JSON.stringify(x));
let child,initial,serial=0,queryData;const pending=new Map();let stderr='';
const send=(operation,extra={})=>new Promise((resolve,reject)=>{const id=++serial;pending.set(id,{resolve,reject});child.stdin.write(JSON.stringify({id,operation,...extra})+'\n');});
test.before(async()=>{
  child=spawn(process.env.PYTHON||'python',['-X','utf8',path.join(__dirname,'attack_assembly_gui_bridge.py'),path.join(__dirname,'../outputs/engine-continuation-20261002/ec21-research/gui-author-temp-'+Date.now())],
    {cwd:path.join(__dirname,'..'),windowsHide:true,stdio:['pipe','pipe','pipe']});
  child.stderr.on('data',data=>stderr+=data);child.on('error',error=>{for(const p of pending.values())p.reject(error);});
  child.on('exit',code=>{for(const p of pending.values())p.reject(Error(`Bridge exit ${code}: ${stderr}`));pending.clear();});
  readline.createInterface({input:child.stdout}).on('line',line=>{const r=JSON.parse(line),p=pending.get(r.id);pending.delete(r.id);p?.resolve(r);});
  let r=await send('init');assert.equal(r.code,0,r.message);initial=r.data;
  assert.deepEqual(initial.caps.attack_source_assembly_contract,initial.contract);
  r=await send('query',{request:{action:'attack_metadata_profile',version_line:'release',character_id:90000001,slot:'skill',constellation:0}});assert.equal(r.code,0,r.message);queryData=r.data;
});
test.after(()=>child?.stdin.end());
function response(plan,req){
  const suffix=createHash('sha256').update(JSON.stringify(req.profile_id)).digest('hex'),hid='assembly.hit.'+suffix,app='assembly.application.'+suffix;
  const draft=clone(plan),ability=plan.rotation_model.abilities.find(a=>a.id===req.ability_ids[0]),ids=[...req.ability_ids].sort();
  const hit={id:hid,damage_type_ids:clone(req.damage_type_ids),t:0,character:ability.character,skill:'元素战技',hit_name:{default:'默认形态命中',tap:'点按命中',hold:'长按命中'}[ability.variant],damage_system:'classic',scaling_stat:req.scaling_stat,count:1};
  draft.manual_template.hits.push(hit);(draft.action_event_profiles||=[]).push({id:req.profile_id,kind:'calibrated_single_contact_v1',prototype_hit_id:hid,variant:ability.variant,
    calibration:clone(req.calibration),application_profile_id:app,contact_profile_id:req.contact_profile_id,default_landed:req.default_landed,self_sampling:req.self_sampling,accepted_profile_fingerprint:null});
  (draft.action_event_bindings||=[]).push(...ids.map(ability_id=>({ability_id,profile_id:req.profile_id})));draft.action_event_overrides||=[];
  const metadata={action_event_profile_id:req.profile_id,version_line:req.version_line,selection:clone(req.selection),candidate_id:req.candidate_id,application_mode:req.application_mode,purpose:'single_stat_damage_multiplier',status:req.status,source:req.source};
  if(req.application_mode==='sequence')metadata.counter_tag=req.counter_tag;if(req.verification)metadata.verification=clone(req.verification);if(req.research_report)metadata.research_report=clone(req.research_report);(draft.attack_metadata_sources||=[]).push(metadata);
  draft.resolution={state:'pending'};draft.resolved_template=null;
  return {code:0,data:{plan:draft,assembly:{schema_version:1,status:'created',profile_id:req.profile_id,prototype_hit_id:hid,application_profile_id:app,ability_ids:ids,actor:ability.character,variant:ability.variant,
    created:{hits:1,profiles:1,bindings:ids.length,sources:1},base_input_fingerprint:'a'.repeat(64),input_fingerprint:'b'.repeat(64)},report:{state:'pending',plan:clone(draft),missing_inputs:[],
    action_event_compilations:draft.action_event_profiles.map(p=>({profile_id:p.id,status:'pending',issues:[],generated:[]})),
    attack_metadata_compilations:draft.attack_metadata_sources.map(s=>({action_event_profile_id:s.action_event_profile_id,status:'pending',issues:[]}))}}};
}
function env(base=initial.plan){
  const e=setup(),w=e.world;w.crypto=webcrypto;w.TextEncoder=TextEncoder;
  for(const file of ['action-events','attack-metadata','attack-assembly'])GUI_TEST_RUNTIME.loadWebFile(w, path.join(__dirname,`../src/genshin_app/gui/web/${file}.js`), {filename:file});
  e.PL.state.caps=clone(initial.caps);e.PL.replace(clone(base),'assembly.json','input-sha',true);e.RT.syncRevision();
  const statuses=[],calls=[];w.setStatus=v=>statuses.push(v);w.callApi=async(name,...args)=>{calls.push({name,args:clone(args)});return name==='query_runtime'?{code:0,data:clone(queryData)}:response(...clone(args));};
  const field=async(label,value)=>{const n=control(e.dialog(),label);n.value=String(value);await n.emit('change');};
  return {...e,w,statuses,calls,field,open:()=>w.ASA.edit()};
}
async function fill(e,{candidate=true,ability='Avatar_Ayaka_Icespine_Strike_New'}={}){
  e.open();await e.field('采用来源的战技能力','e');await e.field('数据库角色身份',90000001);await e.field('实际命座',0);await press(e.dialog(),'读取攻击候选');
  if(candidate)await e.field('本次采用的精确攻击候选',queryData.candidates.find(c=>c.ability_name===ability&&c.status==='ready').candidate_id);
  for(const [label,value] of [['伤害分类声明','damage.elemental_skill'],['倍率用途声明','atk'],['从动作开始到接触的秒数',.2],['记录精度（秒）',.01],
    ['校准依据状态','assumed'],['校准来源、固定状态和适用范围','工程固定时序'],['接触档案','safe'],['元素附着模式','sequence'],['附着计数标签（ICD Tag）','shared.review'],['默认命中判断','true'],['自身收益取值时点','current'],
    ['用途与共享关系的核实状态','assumed'],['用途与逐攻击段附着依据（版本、来源定位、检索日期与采纳理由）','明确单ATK用途与共享身份']])await e.field(label,value);
}
test('new entry is wired and does not choose abilities, candidate, ATK use or landed automatically',()=>{
  const e=env(),box=new Element();e.w.AE.render(box);assert.match(box.textContent,/从数据库建立动作档案/);e.open();
  for(const name of ['采用来源的战技能力','伤害分类声明','倍率用途声明','默认命中判断','自身收益取值时点'])assert.equal(control(e.dialog(),name).value,'');
  const html=fs.readFileSync(path.join(__dirname,'../src/genshin_app/gui/web/index.html'),'utf8');assert.ok(html.indexOf('attack-assembly.js')<html.indexOf('src="app.js"'));
});
test('preview and explicit fresh apply add only native declarations and never accept/save',async()=>{
  const e=env(),before=clone(e.PL.state.plan);await fill(e);await press(e.dialog(),'应用装配草稿');assert.equal(e.calls.filter(c=>c.name==='assemble_attack_source').length,0);
  await press(e.dialog(),'预览装配草稿');assert.deepEqual(clone(e.PL.state.plan),before);await press(e.dialog(),'应用装配草稿');
  const p=e.PL.state.plan;assert.equal(p.action_event_profiles[0].accepted_profile_fingerprint,null);assert.equal(p.manual_template.hits.length,before.manual_template.hits.length+1);
  assert.equal(p.manual_template.hits.at(-1).multiplier,undefined);assert.equal(p.manual_template.hits.at(-1).element,undefined);
  assert.deepEqual(clone(p.rotation_model),before.rotation_model);assert.deepEqual(clone(p.mechanics_model),before.mechanics_model);assert.deepEqual(clone(p.equipment_inputs),before.equipment_inputs);
  assert.equal(p.attack_metadata_sources[0].status,'assumed');assert.equal(e.calls.filter(c=>c.name==='assemble_attack_source').length,2);assert.equal(e.calls.some(c=>/save|confirm/.test(c.name)),false);
});
test('unknown candidate and landing remain pending native declarations',async()=>{
  const e=env();await fill(e,{candidate:false});await e.field('默认命中判断','');await e.field('用途与共享关系的核实状态','pending');
  await press(e.dialog(),'预览装配草稿');await press(e.dialog(),'应用装配草稿');assert.equal(e.PL.state.plan.attack_metadata_sources[0].candidate_id,null);assert.equal(e.PL.state.plan.action_event_profiles[0].default_landed,null);
});
test('same actor variant abilities share one profile and all existing actions are untouched',async()=>{
  const base=clone(initial.plan);base.rotation_model.abilities.push({...clone(base.rotation_model.abilities[0]),id:'second'});const e=env(base);await fill(e);
  const box=e.dialog(),all=n=>[n,...n.children.flatMap(all)],checkbox=all(box).find(n=>n.attributes['aria-label']?.startsWith('同时关联：'));assert.ok(checkbox);checkbox.checked=true;await checkbox.emit('change');
  await press(box,'预览装配草稿');await press(box,'应用装配草稿');assert.equal(e.PL.state.plan.action_event_profiles.length,1);assert.deepEqual(clone(e.PL.state.plan.action_event_bindings.map(b=>b.ability_id)),['e','second']);
});
for(const mutation of ['epoch','sha','path','caps','input','draft','replacement','closed'])test(`late assembly cannot apply after ${mutation}`,async()=>{
  const e=env();await fill(e);await press(e.dialog(),'预览装配草稿');let finish;const old=e.PL.state.plan;
  e.w.callApi=async(name,...args)=>new Promise(resolve=>finish=()=>resolve(response(...clone(args))));const flight=press(e.dialog(),'应用装配草稿');
  if(mutation==='epoch')e.PL.state.sourceEpoch++;if(mutation==='sha')e.PL.state.fileSha='changed';if(mutation==='path')e.RT.state.path='changed';
  if(mutation==='caps')e.PL.state.caps.attack_source_assembly_contract.contract='changed';if(mutation==='input')old.rotation_model.actions[0].start=.001;
  if(mutation==='draft')e.PL.state.draft={pending:true};if(mutation==='replacement')e.PL.replace(clone(old),'other.json','other',true);if(mutation==='closed')await press(e.dialog(),'放弃并关闭');
  const before=JSON.stringify(old),current=JSON.stringify(e.PL.state.plan);finish();await flight;assert.equal(JSON.stringify(old),before);assert.equal(JSON.stringify(e.PL.state.plan),current);
});
for(const [name,mutate] of Object.entries({
  action:r=>r.plan.rotation_model.actions[0].start=99,equipment:r=>r.plan.equipment_inputs.a={mode:'stats'},oldHit:r=>r.plan.manual_template.hits[0].multiplier=99,
  extraField:r=>r.plan.backdoor=true,derived:r=>r.plan.mechanics_model.hit_patterns.push({id:'injected'}),acceptance:r=>r.plan.action_event_profiles[0].accepted_profile_fingerprint='c'.repeat(64),
  multiplier:r=>r.plan.manual_template.hits.at(-1).multiplier=999,element:r=>r.plan.manual_template.hits.at(-1).element='pyro',identity:r=>r.plan.manual_template.hits.at(-1).character='other',
  extraSource:r=>r.plan.attack_metadata_sources.push(clone(r.plan.attack_metadata_sources[0])),mapping:r=>r.assembly.prototype_hit_id='assembly.hit.'+'f'.repeat(64),
  missingReport:r=>r.report.action_event_compilations=[],reportPlan:r=>r.report.plan.rotation_model.actions[0].start=999,
}))test(`untrusted returned ${name} is rejected atomically`,async()=>{
  const e=env();await fill(e);const before=JSON.stringify(e.PL.state.plan);e.w.callApi=async(name,...args)=>{const r=response(...clone(args));mutate(r.data);return r;};
  await press(e.dialog(),'预览装配草稿');assert.equal(JSON.stringify(e.PL.state.plan),before);assert.match(e.dialog().textContent,/未应用/);await press(e.dialog(),'应用装配草稿');assert.equal(JSON.stringify(e.PL.state.plan),before);
});
test('out-of-order previews and local edits reject old proposals',async()=>{
  const e=env();await fill(e);const calls=[];e.w.callApi=(name,...args)=>new Promise(resolve=>calls.push({args:clone(args),resolve}));
  const first=press(e.dialog(),'预览装配草稿');await e.field('从动作开始到接触的秒数',.3);const second=press(e.dialog(),'预览装配草稿');
  calls[1].resolve(response(...calls[1].args));await second;calls[0].resolve(response(...calls[0].args));await first;
  assert.match(e.dialog().textContent,/0.3 秒/);assert.equal(e.PL.state.plan.action_event_profiles,undefined);
});
for(const [ability,variant] of [['Avatar_Ayaka_Icespine_Strike_New','tap'],['Avatar_Kazuha_ElementalArt_FlyAway','hold']])test(`real Python ${variant} source discovery, assembly and application`,async()=>{
  const base=clone(initial.plan);base.rotation_model.abilities[0].variant=variant;const e=env(base);e.w.callApi=async(name,...args)=>send(name==='query_runtime'?'query':'assemble',name==='query_runtime'?{request:args[0]}:{plan:args[0],request:args[1]});
  await fill(e,{ability});await press(e.dialog(),'预览装配草稿');assert.match(e.dialog().textContent,/装配草稿/);await press(e.dialog(),'应用装配草稿');
  assert.equal(e.PL.state.plan.action_event_profiles?.length,1,e.dialog().textContent);assert.equal(e.PL.state.plan.action_event_profiles[0].accepted_profile_fingerprint,null);
  assert.equal(e.PL.state.plan.action_event_profiles[0].variant,variant);
});
test('changing variant clears earlier calibration and interpretation without changing native actions',async()=>{
  const base=clone(initial.plan);base.rotation_model.abilities.push({...clone(base.rotation_model.abilities[0]),id:'hold',variant:'hold'});const e=env(base);await fill(e);
  await e.field('采用来源的战技能力','hold');for(const label of ['伤害分类声明','倍率用途声明','从动作开始到接触的秒数','用途与逐攻击段附着依据（版本、来源定位、检索日期与采纳理由）'])assert.equal(control(e.dialog(),label).value,'');
  assert.equal(control(e.dialog(),'用途与共享关系的核实状态').value,'pending');assert.deepEqual(clone(e.PL.state.plan.rotation_model),base.rotation_model);
});
test('wrong database selection reply cannot fill candidates',async()=>{
  const e=env();e.open();await e.field('采用来源的战技能力','e');await e.field('数据库角色身份',90000001);await e.field('实际命座',0);
  e.w.callApi=async()=>{const data=clone(queryData);data.selection.character_id=999;return{code:0,data};};await press(e.dialog(),'读取攻击候选');
  assert.match(e.dialog().textContent,/角色、技能、资料线不一致/);assert.equal(e.PL.state.plan.action_event_profiles,undefined);
});
test('failed fresh apply cannot publish an earlier successful preview',async()=>{
  const e=env();await fill(e);await press(e.dialog(),'预览装配草稿');const before=JSON.stringify(e.PL.state.plan);e.w.callApi=async()=>({code:1,message:'当前来源读取失败'});
  await press(e.dialog(),'应用装配草稿');assert.equal(JSON.stringify(e.PL.state.plan),before);assert.match(e.dialog().textContent,/读取失败/);
});
test('asynchronous mapping verification also observes the original plan guard',async()=>{
  const e=env();await fill(e);await press(e.dialog(),'预览装配草稿');let finish;const digest=e.w.ASA.digest;
  e.w.ASA.digest=async value=>{const output=await digest(value);return new Promise(resolve=>finish=()=>resolve(output));};
  const flight=press(e.dialog(),'应用装配草稿');while(!finish)await new Promise(resolve=>setTimeout(resolve,1));const old=e.PL.state.plan;
  e.PL.state.fileSha='changed-during-digest';const before=JSON.stringify(old);finish();await flight;assert.equal(JSON.stringify(old),before);
});
test('exact idempotent response retains previously accepted fingerprints without new confirmation',async()=>{
  const e=env();await fill(e);await press(e.dialog(),'预览装配草稿');const invocation=e.calls.find(c=>c.name==='assemble_attack_source'),r=response(...invocation.args),base=r.data.plan;
  base.action_event_profiles[0].accepted_profile_fingerprint='c'.repeat(64);r.data.plan=clone(base);r.data.report.plan=clone(base);r.data.assembly.status='existing';r.data.assembly.created={hits:0,profiles:0,bindings:0,sources:0};
  const result=await e.w.ASA.checkedProposal(clone(base),invocation.args[1],r.data);assert.equal(result.action_event_profiles[0].accepted_profile_fingerprint,'c'.repeat(64));assert.deepEqual(clone(base),r.data.plan);
});
test('checked proposal preserves a bounded research report without treating it as confirmation',async()=>{
  const e=env();await fill(e);await press(e.dialog(),'预览装配草稿');const invocation=e.calls.find(c=>c.name==='assemble_attack_source'),request=clone(invocation.args[1]);
  request.research_report={report_version:1,execution_authority:'none',actual_queries:['exact component query'],sources_checked:[],adopted_findings:[],rejected_findings:[],remaining_unknowns:['timing'],database_patch_plan:[]};
  const r=response(invocation.args[0],request),out=await e.w.ASA.checkedProposal(invocation.args[0],request,r.data),saved=out.attack_metadata_sources[0];
  assert.deepEqual(clone(saved.research_report),request.research_report);assert.equal(saved.research_report.execution_authority,'none');assert.equal(saved.verification,undefined);assert.equal(saved.status,'assumed');
});
