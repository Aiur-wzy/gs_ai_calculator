const GUI_TEST_RUNTIME = require('./helpers/gui_runtime.cjs');
const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');
const {spawn}=require('node:child_process'),readline=require('node:readline'),{webcrypto}=require('node:crypto');
const helpers={module:{exports:{}},require,__dirname,console};
vm.runInNewContext(fs.readFileSync(path.join(__dirname,'gui_hit_parameters_review.test.cjs'),'utf8').split('\ntest(')[0]+'\nmodule.exports={setup,press,control,Element};',helpers);
const {setup,press,control,Element}=helpers.module.exports,clone=x=>JSON.parse(JSON.stringify(x)),all=n=>[n,...n.children.flatMap(all)];
let child,initial,origins,channels,serial=0,stderr='';const waiting=new Map();
const send=(operation,extra={})=>new Promise((resolve,reject)=>{const id=++serial;waiting.set(id,{resolve,reject});child.stdin.write(JSON.stringify({id,operation,...extra})+'\n');});
test.before(async()=>{
  child=spawn(process.env.PYTHON||'python',['-X','utf8',path.join(__dirname,'periodic_assembly_gui_bridge.py'),path.join(__dirname,'../outputs/engine-continuation-20261002/ec24-research/gui-author-temp-'+Date.now())],{cwd:path.join(__dirname,'..'),windowsHide:true,stdio:['pipe','pipe','pipe']});
  child.stderr.on('data',s=>stderr+=s);child.on('error',e=>{for(const p of waiting.values())p.reject(e);});child.on('exit',code=>{for(const p of waiting.values())p.reject(Error(`Bridge exit ${code}: ${stderr}`));waiting.clear();});
  readline.createInterface({input:child.stdout}).on('line',line=>{const r=JSON.parse(line),p=waiting.get(r.id);waiting.delete(r.id);p?.resolve(r);});
  let r=await send('init');assert.equal(r.code,0,r.message);initial=r.data;
  r=await send('query',{request:{action:'periodic_attack_profile',version_line:'release',...initial.request.selection}});assert.equal(r.code,0,r.message);origins=r.data;
  r=await send('query',{request:{action:'periodic_attack_profile',version_line:'release',...initial.request.selection,origin_reference_id:initial.request.origin_reference_id}});assert.equal(r.code,0,r.message);channels=r.data;
});
test.after(()=>child?.stdin.end());
function env(base=initial.plan){
  const e=setup(),w=e.world;w.crypto=webcrypto;w.TextEncoder=TextEncoder;
  for(const file of ['action-events','attack-metadata','attack-assembly','periodic-attacks','periodic-assembly'])GUI_TEST_RUNTIME.loadWebFile(w, path.join(__dirname,`../src/genshin_app/gui/web/${file}.js`), {filename:file});
  e.PL.state.caps=clone(initial.caps);e.PL.replace(clone(base),'periodic-assembly.json','native-sha',true);e.RT.syncRevision();
  const statuses=[],calls=[];w.setStatus=s=>statuses.push(s);
  const api=async(name,...args)=>{calls.push({name,args:clone(args)});
    if(name==='query_runtime')return{code:0,data:clone(args[0].origin_reference_id?channels:origins)};
    if(name==='assemble_periodic_source')return send('assemble',{plan:args[0],request:args[1]});
    if(name==='compose_equipment_plan')return send('compose',{plan:args[0]});
    throw Error('Unexpected API '+name);
  };
  w.callApi=api;
  const field=async(label,value,node=e.dialog())=>{const c=control(node,label);c.value=value===null?'':String(value);await c.emit('change');};
  return{...e,w,statuses,calls,api,field,open:()=>w.PAS.edit(),cards:()=>all(e.dialog()).filter(n=>n.tagName==='section'&&n.children[0]?.textContent?.startsWith('攻击通道 '))};
}
async function select(e){
  e.open();await e.field('采用来源的爆发能力','e');await e.field('数据库角色身份',90000001);await e.field('实际命座',0);
  await press(e.dialog(),'发现相关创建来源');assert.equal(control(e.dialog(),'精确创建来源').value,'');
  await e.field('精确创建来源',initial.request.origin_reference_id);await press(e.dialog(),'读取所选来源的攻击通道');
}
async function choose(e,which){
  const c=channels.candidates.find(c=>c.channel_kind===which),i=channels.candidates.indexOf(c),checkbox=control(e.dialog(),`采用候选 ${i+1} · ${e.w.PA.kindName(which)}`);checkbox.checked=true;await checkbox.emit('change');return c;
}
async function fill(e,{sampling='action_start',unknown=false,applicationMode='sequence'}={}){
  await select(e);await choose(e,'periodic_callback_attack');await choose(e,'ability_start_attack');
  for(const [i,initialCard] of e.cards().entries()){
    let card=initialCard;
    const fields=i===0?[['首击距动作开始（秒）',unknown?null:.2],['本次实例接触次数',unknown?null:4]]:[['本次攻击距动作开始（秒）',unknown?null:1.1]];
    fields.push(['通道记录精度（秒）',unknown?null:.01],['通道校准状态',unknown?'pending':'assumed'],['通道校准依据','工程独立测量声明'],['接触档案','safe'],['默认命中判断',unknown?'':'true'],['自身属性取样',sampling]);
    for(const [label,value] of fields)await e.field(label,value,card);
    await e.field('元素附着模式',applicationMode,e.cards()[i]);card=e.cards()[i];if(applicationMode==='sequence')await e.field('附着计数标签（ICD Tag）','Elemental_Burst',card);await e.field('逐段结构化证据状态','confirmed',card);card=e.cards()[i];
    for(const [label,value] of [['证据类型与定位（URL、本地路径或原始 pointer）','fixture://periodic-channel'],['适用游戏版本或固定 commit','fixture-commit'],['检索/复核日期（YYYY-MM-DD）','2026-10-18'],['具体攻击 component_scope 与共享范围',`候选 ${i+1} 的全部接触沿用数据库确认的 attenuationTag`]])await e.field(label,value,card);
  }
  for(const [label,value] of [['伤害分类声明','damage.elemental_burst'],['倍率用途声明','atk'],['实例建立距动作开始（秒）',unknown?null:.1],['实例结束距动作开始（秒）',unknown?null:1.2],['生命周期记录精度（秒）',unknown?null:.01],['生命周期核实状态',unknown?'pending':'assumed'],['生命周期依据','工程生命周期声明'],['持有者与目标在实例内存活',unknown?'':'true'],['没有提前中断',unknown?'':'true'],['没有刷新替换或提前消失',unknown?'':'true'],['来源采用状态',unknown?'pending':'assumed'],['来源用途与共享关系依据','工程共享关系，不认定游戏原生派发']])await e.field(label,value);
}
async function preview(e){await press(e.dialog(),'预览多通道装配草稿');assert.match(e.dialog().textContent,/已选择 2 个通道/,e.dialog().textContent);}
async function accepted(e){
  e.w.PA.editProfile(0);await press(e.dialog(),'读取所选来源的攻击通道');await press(e.dialog(),'读取并预览动作档案');await press(e.dialog(),'确认所示档案指纹');await press(e.dialog(),'应用档案草稿');
  assert.match(e.PL.state.plan.action_event_profiles[0].accepted_profile_fingerprint,/^[0-9a-f]{64}$/,e.dialog()?.textContent||'dialog closed without accepted fingerprint');
}

test('new entry requires ability, source, candidate, interpretation and sampling without prebuilt hits',async()=>{
  const e=env(),box=new Element();e.w.AE.render(box);assert.match(box.textContent,/从数据库建立多通道档案/);e.open();
  for(const label of ['采用来源的爆发能力','伤害分类声明','倍率用途声明','实例结束距动作开始（秒）'])assert.equal(control(e.dialog(),label).value,'');
  assert.equal(e.PL.state.plan.manual_template.hits.length,0);await press(e.dialog(),'放弃并关闭');await select(e);
  assert.equal(e.cards().length,0);await choose(e,'periodic_callback_attack');const card=e.cards()[0];
  assert.equal(control(card,'元素附着模式').value,'pending');
  for(const label of ['首击距动作开始（秒）','本次实例接触次数','默认命中判断','自身属性取样'])assert.equal(control(card,label).value,'');
  assert.equal(all(card).some(n=>n.attributes['aria-label']==='共享伤害原型'),false);
  const html=fs.readFileSync(path.join(__dirname,'../src/genshin_app/gui/web/index.html'),'utf8');assert.ok(html.indexOf('periodic-attacks.js')<html.indexOf('periodic-assembly.js')&&html.indexOf('periodic-assembly.js')<html.indexOf('src="app.js"'));
});

test('real public service: preview/apply then old confirmation produces fifteen contacts and preserves the axis',async()=>{
  const e=env(),before=clone(e.PL.state.plan);await fill(e);await press(e.dialog(),'应用多通道装配草稿');assert.equal(e.calls.some(c=>c.name==='assemble_periodic_source'),false);
  await preview(e);assert.deepEqual(clone(e.PL.state.plan),before);await press(e.dialog(),'应用多通道装配草稿');const p=e.PL.state.plan;
  assert.equal(p.action_event_profiles[0].accepted_profile_fingerprint,null);assert.equal(p.manual_template.hits.length,2);assert.equal(p.manual_template.hits.every(h=>h.element===undefined&&h.multiplier===undefined),true);
  for(const key of ['rotation_model','mechanics_model','equipment_inputs','effect_bindings'])assert.deepEqual(clone(p[key]),before[key]);
  assert.equal(e.calls.filter(c=>c.name==='assemble_periodic_source').length,2);assert.equal(e.calls.some(c=>/save|confirm/.test(c.name)),false);
  await accepted(e);const r=await send('calc',{plan:clone(p)});assert.equal(r.code,0,r.message);assert.equal(r.data.result.hits.length,15);
  assert.ok(Math.abs(r.data.result.summary.total_damage-1500.889481306076)<1e-9);assert.deepEqual(r.data.result.hits.map(h=>h.t),[0,10,20].flatMap(t=>[.2,.45,.7,.95,1.1].map(x=>t+x)));
});

test('unknown times/count/landing remain complete pending native draft, never inherited engineering defaults',async()=>{
  const e=env();await fill(e,{unknown:true});await preview(e);await press(e.dialog(),'应用多通道装配草稿');const p=e.PL.state.plan.action_event_profiles[0];
  assert.equal(p.lifecycle.instance_end_offset_seconds,null);assert.equal(p.accepted_profile_fingerprint,null);assert.equal(p.channels.find(c=>c.schedule.kind==='periodic').schedule.count,null);assert.ok(p.channels.every(c=>c.default_landed===null));
});
for(const mode of ['no_icd','no_application'])test(`real public assembly accepts structured periodic ${mode} without sequence fields`,async()=>{
  const e=env();await fill(e,{applicationMode:mode});for(const card of e.cards()){assert.equal(all(card).some(n=>n.attributes['aria-label']==='附着计数标签（ICD Tag）'),false);assert.match(card.textContent,/计数标签与规则组：此附着模式不适用/);}await preview(e);await press(e.dialog(),'应用多通道装配草稿');const mappings=e.PL.state.plan.periodic_attack_sources[0].channels;assert.ok(mappings.every(m=>m.application_mode===mode&&!Object.hasOwn(m,'counter_tag')&&m.verification?.status==='confirmed'));assert.ok(mappings.every(m=>JSON.stringify(m.verification.confirmed_fields)===JSON.stringify(['application_mode','shared_scope'])));
});

test('database-confirmed sequence reaches assembly without duplicate external verification',async()=>{
  const e=env();let submitted=null;e.w.callApi=async(name,...args)=>{if(name==='assemble_periodic_source'){submitted=clone(args[1]);return{code:1,message:'captured after GUI validation'};}
    const response=await e.api(name,...args);if(name==='query_runtime'&&args[0].origin_reference_id)response.data.candidates.forEach(candidate=>{candidate.projection={application_identity:{status:'ready',tag:'Elemental_Burst',evidence:'upstream_attenuation_tag',source:{source_path:'fixture.json',json_pointer:'/attackInfo/attenuationTag'}},attenuation:{group:'Default'}};});return response;};
  await fill(e);for(let i=0;i<e.cards().length;i++)await e.field('逐段结构化证据状态','pending',e.cards()[i]);
  assert.ok(e.cards().every(card=>/数据库已确认计数身份（无需重复外部证据）/.test(card.textContent)));await press(e.dialog(),'预览多通道装配草稿');
  assert.ok(submitted);assert.ok(submitted.channels.every(channel=>channel.application_mode==='sequence'&&channel.counter_tag==='Elemental_Burst'&&!Object.hasOwn(channel,'verification')));
});

test('sequence without a database counter identity still requires positive component evidence',async()=>{
  const e=env();e.w.callApi=async(name,...args)=>{const response=await e.api(name,...args);if(name==='query_runtime'&&args[0].origin_reference_id)response.data.candidates.forEach(candidate=>{if(candidate.projection)delete candidate.projection.application_identity;if(candidate.projection_summary)delete candidate.projection_summary.application_identity;});return response;};
  await fill(e);for(let i=0;i<e.cards().length;i++)await e.field('逐段结构化证据状态','pending',e.cards()[i]);const count=e.calls.filter(c=>c.name==='assemble_periodic_source').length;
  await press(e.dialog(),'预览多通道装配草稿');assert.equal(e.calls.filter(c=>c.name==='assemble_periodic_source').length,count);assert.match(e.dialog().textContent,/数据库未确认该附着结论.*结构化逐段证据/);
});

test('existing action_capture declarations are retained and consumed; missing captures stay pending without fallback',async()=>{
  const base=clone(initial.plan);base.mechanics_model.snapshot_captures=base.rotation_model.actions.map(a=>({id:'capture.'+a.id,action_id:a.id,offset:.1,precision:.000001,status:'assumed',source:'工程取样，不是游戏帧数'}));
  const e=env(base);await fill(e,{sampling:'action_capture'});await preview(e);await press(e.dialog(),'应用多通道装配草稿');assert.deepEqual(clone(e.PL.state.plan.mechanics_model),base.mechanics_model);await accepted(e);
  const r=await send('calc',{plan:clone(e.PL.state.plan)});assert.equal(r.code,0,r.message);assert.equal(r.data.result.snapshot_captures.trace.filter(t=>t.type==='snapshot_capture').length,3);
  const missing=env();await fill(missing,{sampling:'action_capture'});await preview(missing);await press(missing.dialog(),'应用多通道装配草稿');const report=await send('compose',{plan:clone(missing.PL.state.plan)});
  assert.equal(report.data.state,'pending');assert.equal(report.data.action_event_compilations[0].generated.length,0);assert.ok(missing.PL.state.plan.action_event_profiles[0].channels.every(c=>c.self_sampling==='action_capture'));
});

test('candidate removal/reselection and source switches clear calibration, never retarget another card',async()=>{
  const e=env();await fill(e);const first=channels.candidates.find(c=>c.channel_kind==='periodic_callback_attack'),i=channels.candidates.indexOf(first),label=`采用候选 ${i+1} · ${e.w.PA.kindName(first.channel_kind)}`;
  let c=control(e.dialog(),label);c.checked=false;await c.emit('change');assert.equal(e.cards().length,1);assert.equal(Number(control(e.cards()[0],'本次攻击距动作开始（秒）').value),1.1);
  c=control(e.dialog(),label);c.checked=true;await c.emit('change');assert.equal(control(e.cards()[1],'本次实例接触次数').value,'');assert.equal(control(e.cards()[1],'自身属性取样').value,'');
  await e.field('精确创建来源','');assert.equal(e.cards().length,0);await press(e.dialog(),'预览多通道装配草稿');assert.equal(e.calls.some(c=>c.name==='assemble_periodic_source'),false);
});

for(const mutation of ['caps','file','epoch','input','replacement','draft','close'])test(`pending fresh apply is rejected after ${mutation}`,async()=>{
  const e=env();await fill(e);await preview(e);let finish,started;
  const requestStarted=new Promise(r=>started=r);e.w.callApi=async(name,...args)=>{const r=await e.api(name,...args);started();return new Promise(resolve=>finish=()=>resolve(r));};
  const flight=press(e.dialog(),'应用多通道装配草稿');await requestStarted;const old=e.PL.state.plan;
  if(mutation==='caps')e.PL.state.caps.periodic_source_assembly_contract.contract='changed';if(mutation==='file')e.PL.state.fileSha='different';if(mutation==='epoch')e.PL.state.sourceEpoch++;
  if(mutation==='input')old.rotation_model.actions[0].start=.000001;if(mutation==='replacement')e.PL.replace(clone(old),'new.json','new-sha',true);if(mutation==='draft')e.PL.state.draft={pending:true};if(mutation==='close')await press(e.dialog(),'放弃并关闭');
  const before=JSON.stringify(old),current=JSON.stringify(e.PL.state.plan);finish();await flight;assert.equal(JSON.stringify(old),before);assert.equal(JSON.stringify(e.PL.state.plan),current);
});

for(const [name,mutate] of Object.entries({
  action:r=>{r.plan.rotation_model.actions[0].start=99;r.report.plan.rotation_model.actions[0].start=99;},
  oldModel:r=>{r.plan.mechanics_model.rules.push({id:'injected'});},acceptance:r=>r.plan.action_event_profiles[0].accepted_profile_fingerprint='f'.repeat(64),
  missingChannel:r=>r.plan.periodic_attack_sources[0].channels.pop(),mapping:r=>r.assembly.channels[0].prototype_hit_id='fake',
  missingSources:r=>r.report.periodic_attack_compilations=[],missingProfiles:r=>r.report.action_event_compilations=[],
  extraBinding:r=>r.plan.action_event_bindings.push({ability_id:'other',profile_id:r.assembly.profile_id}),
  missingReport:r=>delete r.report.plan,fullCoverage:r=>r.assembly.source_summary.whole_source_status='complete',
  falseOmission:r=>{r.assembly.source_summary.omission_status='not_evaluated';r.assembly.source_summary.omitted_candidate_ids=null;},
}))test(`altered ${name} response never applies or changes original`,async()=>{
  const e=env();await fill(e);const before=JSON.stringify(e.PL.state.plan);e.w.callApi=async(name,...args)=>{const r=await e.api(name,...args);assert.equal(r.code,0,r.message);mutate(r.data);return r;};
  await press(e.dialog(),'预览多通道装配草稿');assert.match(e.dialog().textContent,/未应用/);await press(e.dialog(),'应用多通道装配草稿');assert.equal(JSON.stringify(e.PL.state.plan),before);
});

test('digest await cannot bypass caps guard',async()=>{
  const e=env();await fill(e);await preview(e);let finish,entered;const pending=new Promise(r=>entered=r),digest=e.w.ASA.digest;
  e.w.ASA.digest=async value=>{const out=await digest(value);if(!finish){entered();return new Promise(r=>finish=()=>r(out));}return out;};
  const old=JSON.stringify(e.PL.state.plan),flight=press(e.dialog(),'应用多通道装配草稿');await pending;e.PL.state.caps.periodic_attack_contract.compiler='changed';finish();await flight;assert.equal(JSON.stringify(e.PL.state.plan),old);
});

test('existing exact request with reversed arrays retains acceptance and whole original ordering',async()=>{
  const e=env(),request=clone(initial.request),created=await send('assemble',{plan:initial.plan,request});assert.equal(created.code,0,created.message);
  const base=created.data.plan,report=await send('compose',{plan:base});Object.assign(base.action_event_profiles[0],report.data.action_event_compilations[0].required_confirmation);
  base.action_event_profiles[0].channels.reverse();base.periodic_attack_sources[0].channels.reverse();base.manual_template.hits.reverse();request.channels.reverse();
  const r=await send('assemble',{plan:base,request});assert.equal(r.code,0,r.message);assert.equal(r.data.assembly.status,'existing');const out=await e.w.PAS.checkedProposal(clone(base),request,r.data);assert.deepEqual(clone(out),base);
});

test('checked periodic proposal preserves one no-authority research report per channel',async()=>{
  const e=env(),request=clone(initial.request);request.channels.forEach((channel,index)=>channel.research_report={report_version:1,execution_authority:'none',actual_queries:[`component ${index+1}`],sources_checked:[],adopted_findings:[],rejected_findings:[],remaining_unknowns:['calibrated timing'],database_patch_plan:[]});
  const r=await send('assemble',{plan:initial.plan,request});assert.equal(r.code,0,r.message);const out=await e.w.PAS.checkedProposal(initial.plan,request,r.data),saved=out.periodic_attack_sources[0].channels;
  assert.deepEqual(clone(saved.map(row=>row.research_report)),clone([...request.channels].sort((a,b)=>a.candidate_id.localeCompare(b.candidate_id)).map(row=>row.research_report)));assert.ok(saved.every(row=>row.research_report.execution_authority==='none'));
});

test('selected subset shows known omission while null origin result remains explicitly unassessed',async()=>{
  const e=env(),request=clone(initial.request);request.channels.pop();let r=await send('assemble',{plan:initial.plan,request});assert.equal(r.code,0,r.message);assert.equal(r.data.assembly.source_summary.omitted_candidate_ids.length,1);await e.w.PAS.checkedProposal(initial.plan,request,r.data);
  request.origin_reference_id=null;r=await send('assemble',{plan:initial.plan,request});assert.equal(r.code,0,r.message);assert.equal(r.data.assembly.source_summary.omitted_candidate_ids,null);await e.w.PAS.checkedProposal(initial.plan,request,r.data);
});

test('old periodic confirmation ignores a compose response when caps changed',async()=>{
  const e=env(),created=await send('assemble',{plan:initial.plan,request:initial.request});assert.equal(created.code,0,created.message);
  e.PL.replace(created.data.plan,'accepted.json','native-sha',true);e.w.PA.editProfile(0);await press(e.dialog(),'读取所选来源的攻击通道');let finish,entered;const pending=new Promise(r=>entered=r);
  e.w.callApi=async(name,...args)=>{const r=await e.api(name,...args);entered();return new Promise(resolve=>finish=()=>resolve(r));};
  const before=JSON.stringify(e.PL.state.plan),flight=press(e.dialog(),'读取并预览动作档案');await pending;e.PL.state.caps.action_event_contract.compiler='changed';finish();await flight;assert.equal(JSON.stringify(e.PL.state.plan),before);assert.match(e.statuses.at(-1),/旧动作档案响应/);
});

test('explicit talent level is checked against returned level, not absent selection.talent_level',async()=>{
  const e=env();e.w.callApi=async(name,...args)=>name==='query_runtime'?send('query',{request:args[0]}):e.api(name,...args);
  e.open();await e.field('采用来源的爆发能力','e');await e.field('数据库角色身份',90000001);await e.field('实际命座',0);await e.field('有效天赋等级（零命留空默认10级）',10);
  await press(e.dialog(),'发现相关创建来源');assert.ok(control(e.dialog(),'精确创建来源').children.length>1,e.dialog().textContent);
  await e.field('精确创建来源',initial.request.origin_reference_id);await press(e.dialog(),'读取所选来源的攻击通道');await choose(e,'periodic_callback_attack');assert.equal(e.cards().length,1);
});

for(const mutation of ['snapshot','origin','duplicate','mode'])test(`wrong ${mutation} query data cannot install channel candidates`,async()=>{
  const e=env();await select(e);e.w.callApi=async()=>{const d=clone(channels);if(mutation==='snapshot')d.snapshot.version_line='beta';if(mutation==='origin')d.source_graph.creation_bundle.origin.legacy_reference.reference_id='0'.repeat(64);if(mutation==='duplicate')d.candidates.push(clone(d.candidates[0]));if(mutation==='mode')d.mode='selected';return{code:0,data:d};};
  await press(e.dialog(),'读取所选来源的攻击通道');assert.match(e.dialog().textContent,/未采用/);assert.equal(e.cards().length,0);assert.equal(all(e.dialog()).some(n=>n.attributes['aria-label']?.startsWith('采用候选 ')),false);
});
