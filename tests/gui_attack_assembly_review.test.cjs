const GUI_TEST_RUNTIME = require('./helpers/gui_runtime.cjs');
const Python = require('./helpers/python_command.cjs');
const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');
const readline=require('node:readline'),{webcrypto}=require('node:crypto');
// Reuse only the generic DOM facilities; source responses and calculation are real.
const harness={module:{exports:{}},require,__dirname,console};
vm.runInNewContext(fs.readFileSync(path.join(__dirname,'gui_hit_parameters_review.test.cjs'),'utf8').split('\ntest(')[0]+'\nmodule.exports={setup,press,control};',harness);
const {setup,press,control}=harness.module.exports,clone=value=>JSON.parse(JSON.stringify(value));
let child,initial,serial=0,stderr='';const waiting=new Map();
function send(operation,extra={}){return new Promise((resolve,reject)=>{const id=++serial;waiting.set(id,{resolve,reject});child.stdin.write(JSON.stringify({id,operation,...extra})+'\n');});}
test.before(async()=>{
  child=Python.spawn(['-X','utf8',path.join(__dirname,'attack_source_assembly_review_bridge.py'),path.join(__dirname,'../outputs/engine-continuation-20261002/ec21-research/gui-independent-temp-'+Date.now())],
    {cwd:path.join(__dirname,'..'),windowsHide:true,stdio:['pipe','pipe','pipe']});
  child.stderr.on('data',value=>stderr+=value);child.on('error',error=>{for(const p of waiting.values())p.reject(error);});
  child.on('exit',code=>{for(const p of waiting.values())p.reject(Error(`Bridge exit ${code}: ${stderr}`));waiting.clear();});
  readline.createInterface({input:child.stdout}).on('line',line=>{const response=JSON.parse(line),p=waiting.get(response.id);waiting.delete(response.id);p?.resolve(response);});
  const response=await send('init');assert.equal(response.code,0,response.message);initial=response.data;
});
test.after(async()=>{if(child&&!child.killed){try{assert.equal(child.exitCode,null,`Bridge exited before the final immutable DB check: ${stderr}`);const result=await send('database_sha');assert.equal(result.data.before,result.data.after);}finally{child.stdin.end();}}});
function environment(base=initial.plan){
  const e=setup(),w=e.world;w.crypto=webcrypto;w.TextEncoder=TextEncoder;
  for(const name of ['action-events','attack-metadata','attack-assembly'])GUI_TEST_RUNTIME.loadWebFile(w, path.join(__dirname,`../src/genshin_app/gui/web/${name}.js`), {filename:name});
  e.PL.state.caps=clone(initial.caps);e.PL.replace(clone(base),'independent-native.json','original-file-sha',true);e.RT.syncRevision();
  const calls=[],status=[];w.setStatus=text=>status.push(text);
  const actual=async(name,...args)=>{calls.push({name,args:clone(args)});
    if(name==='query_runtime')return send('query',{request:args[0]});
    if(name==='assemble_attack_source')return send('assemble',{plan:args[0],request:args[1]});
    if(name==='compose_equipment_plan')return send('compose',{plan:args[0]});
    throw Error('Unexpected API '+name);
  };
  w.callApi=actual;
  const field=async(label,value)=>{const input=control(e.dialog(),label);input.value=String(value);await input.emit('change');};
  return {...e,w,actual,calls,status,field,open:()=>w.ASA.edit()};
}
async function declare(e,ability='Avatar_Ayaka_Icespine_Strike_New'){
  await e.field('数据库角色身份',90000001);await e.field('实际命座',0);await press(e.dialog(),'读取攻击候选');
  const response=await send('query',{request:{action:'attack_metadata_profile',character_id:90000001,slot:'skill',constellation:0,version_line:'release'}});
  const selected=response.data.candidates.find(row=>row.ability_name===ability&&row.status==='ready');assert.ok(selected);
  await e.field('本次采用的精确攻击候选',selected.candidate_id);
  for(const [label,value] of [['伤害分类声明','damage.elemental_skill'],['倍率用途声明','atk'],['从动作开始到接触的秒数',.2],['记录精度（秒）',.01],
    ['校准依据状态','assumed'],['校准来源、固定状态和适用范围','Independent synthetic timing'],['接触档案','safe'],['元素附着模式','sequence'],['附着计数标签（ICD Tag）','review.gui.shared'],
    ['默认命中判断','true'],['自身收益取值时点','current'],['用途与共享关系的核实状态','confirmed'],['用途与逐攻击段附着依据（版本、来源定位、检索日期与采纳理由）','2026-10-18 fixed-commit component evidence: Independent declared scope'],
    ['逐段结构化证据状态','confirmed'],['证据类型与定位（URL、本地路径或原始 pointer）','fixture://attack-component'],['适用游戏版本或固定 commit','fixture-release'],
    ['检索/复核日期（YYYY-MM-DD）','2026-10-18'],['具体攻击 component_scope 与共享范围','Selected candidate component; shares review.gui.shared only where explicitly declared']])await e.field(label,value);
}
async function prepare(e){e.open();await e.field('采用来源的战技能力','e');await declare(e);}
async function apply(e){await press(e.dialog(),'预览装配草稿');await press(e.dialog(),'应用装配草稿');assert.equal(e.PL.state.plan.action_event_profiles?.length,1,e.dialog().textContent);}
async function confirmAndCalculate(e){
  e.w.AE.editProfile(0);await press(e.dialog(),'读取并预览动作档案');await press(e.dialog(),'确认所示档案指纹');
  assert.equal(e.PL.state.plan.action_event_profiles[0].accepted_profile_fingerprint,null,'modal confirmation is still local');
  await press(e.dialog(),'应用档案草稿');assert.match(e.PL.state.plan.action_event_profiles[0].accepted_profile_fingerprint,/^[a-f0-9]{64}$/);
  const result=await send('calc',{plan:clone(e.PL.state.plan)});assert.equal(result.code,0,result.message);return result.data;
}
test('independent first ability selection uses its explicit DB identity but does not invent constellation or candidate',async()=>{
  const base=clone(initial.plan);base.equipment_inputs.a={mode:'panel',panel_policy:'static_included',weapon:null,artifact_sets:[],source_refs:[],data_version:null,character_ref:{db_character_id:90000001,version_line:'release'}};
  const e=environment(base),before=JSON.stringify(e.PL.state.plan);e.open();await e.field('采用来源的战技能力','e');
  assert.equal(Number(control(e.dialog(),'数据库角色身份').value),90000001);assert.equal(control(e.dialog(),'实际命座').value,'');
  await press(e.dialog(),'读取攻击候选');assert.equal(e.calls.length,0);await e.field('实际命座',0);await press(e.dialog(),'读取攻击候选');
  assert.equal(e.calls[0].args[0].character_id,90000001);assert.equal(control(e.dialog(),'本次采用的精确攻击候选').value,'');
  assert.equal(JSON.stringify(e.PL.state.plan),before);
});
test('independent real empty native -> preview -> apply -> existing confirmation editor -> three calculated hits',async()=>{
  const e=environment(),before=clone(e.PL.state.plan);await prepare(e);await press(e.dialog(),'预览装配草稿');assert.deepEqual(clone(e.PL.state.plan),before);
  await press(e.dialog(),'应用装配草稿');assert.equal(e.PL.state.plan.action_event_profiles[0].accepted_profile_fingerprint,null);
  assert.equal(e.PL.state.plan.manual_template.hits.length,1);assert.equal(e.PL.state.plan.manual_template.hits[0].multiplier,undefined);
  const out=await confirmAndCalculate(e);assert.deepEqual(out.result.hits.map(row=>row.t),[.2,2.2,4.2]);
  for(const row of out.result.hits)assert.ok(Math.abs(row.expected-193.75200748443605)<1e-9);
  assert.deepEqual(clone(e.PL.state.plan.rotation_model),before.rotation_model);assert.deepEqual(clone(e.PL.state.plan.mechanics_model),before.mechanics_model);
  assert.equal(e.calls.some(row=>/save/.test(row.name)),false);assert.equal(e.PL.state.plan.mechanics_model.hit_patterns.length,0);
});
test('independent different variant requires new declarations and real hold source still reaches the same generic calculation',async()=>{
  const base=clone(initial.plan);base.rotation_model.abilities.push({...clone(base.rotation_model.abilities[0]),id:'hold',variant:'hold'});
  for(const action of base.rotation_model.actions)action.ability_id='hold';
  const e=environment(base),before=clone(e.PL.state.plan.rotation_model);await prepare(e);await e.field('采用来源的战技能力','hold');
  for(const name of ['从动作开始到接触的秒数','记录精度（秒）','默认命中判断','伤害分类声明'])assert.equal(control(e.dialog(),name).value,'');
  assert.equal(control(e.dialog(),'用途与共享关系的核实状态').value,'pending');await press(e.dialog(),'应用装配草稿');assert.equal(e.calls.filter(row=>row.name==='assemble_attack_source').length,0);
  await declare(e,'Avatar_Kazuha_ElementalArt_FlyAway');await apply(e);const out=await confirmAndCalculate(e);
  assert.equal(e.PL.state.plan.action_event_profiles[0].variant,'hold');for(const hit of out.result.hits)assert.ok(Math.abs(hit.expected-211.24799251556395)<1e-9);
  assert.deepEqual(clone(e.PL.state.plan.rotation_model),before);
});
test('independent pending equipment draft blocks the new modal without touching either input',()=>{
  const e=environment();e.PL.state.draft={source:'Uncommitted equipment choice'};const before=JSON.stringify(e.PL.state.plan),draft=JSON.stringify(e.PL.state.draft),count=e.w.document.body.children.length;e.open();
  assert.equal(e.w.document.body.children.length,count);assert.equal(e.calls.length,0);assert.equal(JSON.stringify(e.PL.state.plan),before);assert.equal(JSON.stringify(e.PL.state.draft),draft);
  assert.match(e.status.at(-1),/装备草稿/);
});
test('independent delayed real fresh apply cannot cross a file revision change',async t=>{
  const e=environment();await prepare(e);await press(e.dialog(),'预览装配草稿');let release,seen;
  const ready=new Promise(resolve=>seen=resolve);e.w.callApi=async(name,...args)=>{assert.equal(name,'assemble_attack_source');const result=await e.actual(name,...args);return new Promise(resolve=>{release=()=>resolve(result);seen();});};
  const inFlight=press(e.dialog(),'应用装配草稿');
  t.after(async()=>{release?.();await inFlight;});
  // Observe the actual callback or actual apply completion. A rejected preview
  // must fail this assertion instead of leaving an unbounded orphan waiter.
  const observed=await Promise.race([ready.then(()=>({state:'response-held'})),inFlight.then(()=>({state:'apply-completed'}),error=>({state:'apply-failed',error}))]);
  assert.equal(observed.state,'response-held',`Apply did not reach the real assembler: ${observed.error?.message||e.status.at(-1)||e.dialog()?.textContent}`);
  e.PL.state.fileSha='external-file-change';const before=JSON.stringify(e.PL.state.plan);release();await inFlight;
  assert.equal(JSON.stringify(e.PL.state.plan),before);assert.equal(e.PL.state.plan.action_event_profiles,undefined);assert.match(e.status.at(-1),/旧装配响应已忽略/);
});
test('independent real response with matching forged report and mutated action is rejected before native apply',async()=>{
  const e=environment();await prepare(e);const before=JSON.stringify(e.PL.state.plan);e.w.callApi=async(name,...args)=>{const result=await e.actual(name,...args);assert.equal(result.code,0,result.message);
    result.data.plan.rotation_model.actions[0].start=.01;result.data.report.plan.rotation_model.actions[0].start=.01;return result;};
  await press(e.dialog(),'预览装配草稿');assert.equal(JSON.stringify(e.PL.state.plan),before);assert.match(e.dialog().textContent,/未应用/);
  await press(e.dialog(),'应用装配草稿');assert.equal(JSON.stringify(e.PL.state.plan),before);assert.equal(e.calls.filter(row=>row.name==='assemble_attack_source').length,1);
});
