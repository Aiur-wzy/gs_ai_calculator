const GUI_TEST_RUNTIME = require('./helpers/gui_runtime.cjs');
const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');
const {spawn}=require('node:child_process'),readline=require('node:readline'),{webcrypto}=require('node:crypto');
// DOM facilities are shared; all database/source/compose responses are public Python calls.
const harness={module:{exports:{}},require,__dirname,console};
vm.runInNewContext(fs.readFileSync(path.join(__dirname,'gui_hit_parameters_review.test.cjs'),'utf8').split('\ntest(')[0]+'\nmodule.exports={setup,press,control,Element};',harness);
const {setup,press,control,Element}=harness.module.exports,clone=x=>JSON.parse(JSON.stringify(x)),all=n=>[n,...n.children.flatMap(all)];
let child,initial,origins,channels,serial=0,stderr='';const waiting=new Map();
function send(operation,extra={}){return new Promise((resolve,reject)=>{const id=++serial;waiting.set(id,{resolve,reject});child.stdin.write(JSON.stringify({id,operation,...extra})+'\n');});}
test.before(async()=>{
  child=spawn(process.env.PYTHON||'python',['-X','utf8',path.join(__dirname,'periodic_assembly_gui_bridge.py'),path.join(__dirname,'../outputs/engine-continuation-20261002/ec24-research/gui-independent-temp-'+Date.now())],{cwd:path.join(__dirname,'..'),windowsHide:true,stdio:['pipe','pipe','pipe']});
  child.stderr.on('data',s=>stderr+=s);child.on('error',e=>{for(const p of waiting.values())p.reject(e);});child.on('exit',code=>{for(const p of waiting.values())p.reject(Error(`Bridge exit ${code}: ${stderr}`));waiting.clear();});
  readline.createInterface({input:child.stdout}).on('line',line=>{const r=JSON.parse(line),p=waiting.get(r.id);waiting.delete(r.id);p?.resolve(r);});
  let r=await send('init');assert.equal(r.code,0,r.message);initial=r.data;
  r=await send('query',{request:{action:'periodic_attack_profile',version_line:'release',...initial.request.selection}});assert.equal(r.code,0,r.message);origins=r.data;
  r=await send('query',{request:{action:'periodic_attack_profile',version_line:'release',...initial.request.selection,origin_reference_id:initial.request.origin_reference_id}});assert.equal(r.code,0,r.message);channels=r.data;
});
test.after(()=>child?.stdin.end());
function environment(base=initial.plan){
  const e=setup(),w=e.world;w.crypto=webcrypto;w.TextEncoder=TextEncoder;
  for(const file of ['action-events','attack-metadata','attack-assembly','periodic-attacks','periodic-assembly'])GUI_TEST_RUNTIME.loadWebFile(w, path.join(__dirname,`../src/genshin_app/gui/web/${file}.js`), {filename:file});
  e.PL.state.caps=clone(initial.caps);e.PL.replace(clone(base),'independent-periodic.json','original-sha',true);e.RT.syncRevision();
  const calls=[],statuses=[];w.setStatus=s=>statuses.push(s);
  const actual=async(name,...args)=>{calls.push({name,args:clone(args)});
    if(name==='query_runtime')return send('query',{request:args[0]});
    if(name==='assemble_periodic_source')return send('assemble',{plan:args[0],request:args[1]});
    if(name==='compose_equipment_plan')return send('compose',{plan:args[0]});
    throw Error('Unexpected API '+name);
  };w.callApi=actual;
  const field=async(label,value,node=e.dialog())=>{const c=control(node,label);c.value=value===null?'':String(value);await c.emit('change');};
  return {...e,w,calls,statuses,actual,field,cards:()=>all(e.dialog()).filter(n=>n.tagName==='section'&&n.children[0]?.textContent.startsWith('攻击通道 '))};
}
async function identity(e,level){
  const box=new Element();e.w.AE.render(box);await press(box,'从数据库建立多通道档案');
  await e.field('采用来源的爆发能力','e');await e.field('数据库角色身份',90000001);await e.field('实际命座',0);
  if(level!==undefined)await e.field('有效天赋等级（零命留空默认10级）',level);
}
async function select(e){
  await identity(e);await press(e.dialog(),'发现相关创建来源');await e.field('精确创建来源',initial.request.origin_reference_id);await press(e.dialog(),'读取所选来源的攻击通道');
}
async function choose(e,kind){const i=channels.candidates.findIndex(c=>c.channel_kind===kind),c=control(e.dialog(),`采用候选 ${i+1} · ${e.w.PA.kindName(kind)}`);c.checked=true;await c.emit('change');}
async function fill(e,{unknown=false}={}){
  await select(e);await choose(e,'ability_start_attack');await choose(e,'periodic_callback_attack');
  // Select in reverse semantic order deliberately. The complete card remains attached to its candidate.
  for(const card of e.cards()){
    const periodic=card.children[0].textContent.includes('周期接触');
    const fields=periodic?[['首击距动作开始（秒）',unknown?null:.2],['本次实例接触次数',unknown?null:4]]:[['本次攻击距动作开始（秒）',unknown?null:1.1]];
    fields.push(['通道记录精度（秒）',unknown?null:.01],['通道校准状态',unknown?'pending':'assumed'],['通道校准依据','独立工程接触校准'],['接触档案','safe'],['默认命中判断',unknown?'':'true'],['自身属性取样','action_start']);
    for(const [name,value] of fields)await e.field(name,value,card);
    let evidenceCard=e.cards().find(c=>c.children[0].textContent===card.children[0].textContent);await e.field('元素附着模式','sequence',evidenceCard);evidenceCard=e.cards().find(c=>c.children[0].textContent===card.children[0].textContent);await e.field('附着计数标签（ICD Tag）','Elemental_Burst',evidenceCard);await e.field('逐段结构化证据状态','confirmed',evidenceCard);evidenceCard=e.cards().find(c=>c.children[0].textContent===card.children[0].textContent);
    for(const [name,value] of [['证据类型与定位（URL、本地路径或原始 pointer）','fixture://periodic-independent'],['适用游戏版本或固定 commit','fixture-commit'],['检索/复核日期（YYYY-MM-DD）','2026-10-18'],['具体攻击 component_scope 与共享范围','当前候选接触沿用数据库确认的 attenuationTag']])await e.field(name,value,evidenceCard);
  }
  for(const [name,value] of [['伤害分类声明','damage.elemental_burst'],['倍率用途声明','atk'],['实例建立距动作开始（秒）',unknown?null:.1],['实例结束距动作开始（秒）',unknown?null:1.2],['生命周期记录精度（秒）',unknown?null:.01],['生命周期核实状态',unknown?'pending':'assumed'],['生命周期依据','独立工程生命周期，非客户端帧证据'],['持有者与目标在实例内存活',unknown?'':'true'],['没有提前中断',unknown?'':'true'],['没有刷新替换或提前消失',unknown?'':'true'],['来源采用状态',unknown?'pending':'assumed'],['来源用途与共享关系依据','独立显式用途声明']])await e.field(name,value);
}
async function preview(e){await press(e.dialog(),'预览多通道装配草稿');assert.match(e.dialog().textContent,/已选择 2 个通道/,e.dialog().textContent);}

test('independent actual UI entry -> public assembly -> old confirmation -> fifteen literal contacts',async()=>{
  const e=environment(),before=clone(e.PL.state.plan);await fill(e);await preview(e);assert.deepEqual(clone(e.PL.state.plan),before);await press(e.dialog(),'应用多通道装配草稿');
  const p=e.PL.state.plan;assert.equal(p.action_event_profiles[0].accepted_profile_fingerprint,null);assert.equal(p.manual_template.hits.length,2);
  assert.ok(p.manual_template.hits.every(h=>h.multiplier===undefined&&h.element===undefined));
  for(const key of ['rotation_model','mechanics_model','equipment_inputs','effect_bindings'])assert.deepEqual(clone(p[key]),before[key]);
  e.w.PA.editProfile(0);await press(e.dialog(),'读取所选来源的攻击通道');await press(e.dialog(),'读取并预览动作档案');await press(e.dialog(),'确认所示档案指纹');
  assert.equal(p.action_event_profiles[0].accepted_profile_fingerprint,null);await press(e.dialog(),'应用档案草稿');assert.match(p.action_event_profiles[0].accepted_profile_fingerprint,/^[0-9a-f]{64}$/);
  const r=await send('calc',{plan:clone(p)});assert.equal(r.code,0,r.message);assert.equal(r.data.result.hits.length,15);
  const times=[.2,.45,.7,.95,1.1,10.2,10.45,10.7,10.95,11.1,20.2,20.45,20.7,20.95,21.1];assert.deepEqual(r.data.result.hits.map(h=>h.t),times);
  for(const [i,h] of r.data.result.hits.entries())assert.ok(Math.abs(h.expected-(i%5===4?136.44449830055237:90.96299886703491))<1e-9);
  assert.ok(Math.abs(r.data.result.summary.total_damage-1500.889481306076)<1e-9);assert.equal(e.calls.some(c=>/save|confirm/.test(c.name)),false);
});

test('independent explicit valid effective level uses the real level record, not a nonexistent selection field',async()=>{
  const e=environment();await identity(e,10);await press(e.dialog(),'发现相关创建来源');
  const options=control(e.dialog(),'精确创建来源').children;assert.ok(options.length>1,e.dialog().textContent);
  await e.field('精确创建来源',initial.request.origin_reference_id);await press(e.dialog(),'读取所选来源的攻击通道');assert.match(e.dialog().textContent,/采用候选/);
});

test('independent channel discovery from another actual origin is rejected despite matching character and level',async()=>{
  const e=environment();await identity(e);await press(e.dialog(),'发现相关创建来源');await e.field('精确创建来源',initial.request.origin_reference_id);
  const alternate=origins.creation_candidates.find(o=>o.origin_reference_id!==initial.request.origin_reference_id);assert.ok(alternate);
  const wrong=await send('query',{request:{action:'periodic_attack_profile',version_line:'release',...initial.request.selection,origin_reference_id:alternate.origin_reference_id}});assert.equal(wrong.code,0,wrong.message);assert.ok(wrong.data.candidates.length>0);
  e.w.callApi=async(name,...args)=>name==='query_runtime'?clone(wrong):e.actual(name,...args);await press(e.dialog(),'读取所选来源的攻击通道');
  assert.equal(all(e.dialog()).filter(n=>n.attributes['aria-label']?.startsWith('采用候选 ')).length,0,'foreign origin must not become selectable');assert.match(e.dialog().textContent,/未采用/);
});

test('independent unknown schedule and landing stays complete pending draft without implicit confirmation',async()=>{
  const e=environment();await fill(e,{unknown:true});await preview(e);await press(e.dialog(),'应用多通道装配草稿');const p=e.PL.state.plan,profile=p.action_event_profiles[0];
  assert.equal(profile.accepted_profile_fingerprint,null);assert.equal(profile.channels.length,2);assert.ok(profile.channels.every(c=>c.default_landed===null));assert.equal(profile.channels.find(c=>c.schedule.kind==='periodic').schedule.count,null);
  const r=await send('compose',{plan:clone(p)});assert.equal(r.code,0,r.message);assert.equal(r.data.state,'pending');assert.equal(r.data.action_event_compilations[0].generated.length,0);
});

test('independent source selection change discards every source-dependent channel before a delayed actual response',async()=>{
  const e=environment();await select(e);let release,entered;const pending=new Promise(r=>entered=r);
  e.w.callApi=async(name,...args)=>{const r=await e.actual(name,...args);entered();return new Promise(resolve=>release=()=>resolve(r));};
  const flight=press(e.dialog(),'读取所选来源的攻击通道');await pending;await e.field('数据库角色身份',90000002);release();await flight;
  assert.equal(e.cards().length,0);assert.equal(all(e.dialog()).filter(n=>n.attributes['aria-label']?.startsWith('采用候选 ')).length,0);assert.match(e.statuses.at(-1),/旧周期来源响应/);
});

test('independent actual apply response cannot cross a capability change during asynchronous digest',async()=>{
  const e=environment();await fill(e);await preview(e);let release,entered;const pending=new Promise(r=>entered=r),digest=e.w.ASA.digest;
  e.w.ASA.digest=async x=>{const r=await digest(x);if(!release){entered();return new Promise(resolve=>release=()=>resolve(r));}return r;};
  const before=JSON.stringify(e.PL.state.plan),flight=press(e.dialog(),'应用多通道装配草稿');await pending;e.PL.state.caps.periodic_source_assembly_contract.schema_version=2;release();await flight;assert.equal(JSON.stringify(e.PL.state.plan),before);
});

test('independent matching response/report injected action and manual hit are rejected atomically',async()=>{
  const e=environment();await fill(e);const before=JSON.stringify(e.PL.state.plan);e.w.callApi=async(name,...args)=>{const r=await e.actual(name,...args);assert.equal(r.code,0,r.message);
    for(const p of [r.data.plan,r.data.report.plan]){p.rotation_model.actions[0].start=.123;p.manual_template.hits.push({id:'foreign'});}return r;};
  await press(e.dialog(),'预览多通道装配草稿');assert.match(e.dialog().textContent,/未应用/);await press(e.dialog(),'应用多通道装配草稿');assert.equal(JSON.stringify(e.PL.state.plan),before);assert.equal(e.calls.filter(c=>c.name==='assemble_periodic_source').length,1);
});

test('independent selected candidate reorder validates complete mappings while existing accepted array order stays untouched',async()=>{
  const request=clone(initial.request),r=await send('assemble',{plan:clone(initial.plan),request});assert.equal(r.code,0,r.message);const base=r.data.plan;
  Object.assign(base.action_event_profiles[0],r.data.report.action_event_compilations[0].required_confirmation);base.action_event_profiles[0].channels.reverse();base.periodic_attack_sources[0].channels.reverse();base.manual_template.hits.reverse();request.channels.reverse();
  const e=environment(base),before=clone(e.PL.state.plan),out=await send('assemble',{plan:clone(before),request});assert.equal(out.code,0,out.message);assert.equal(out.data.assembly.status,'existing');
  assert.deepEqual(clone(await e.w.PAS.checkedProposal(clone(before),request,out.data)),before);assert.deepEqual(clone(e.PL.state.plan),before);
});

test('independent index dependency ordering and equipment draft guard preserve the previous action editor',()=>{
  const html=fs.readFileSync(path.join(__dirname,'../src/genshin_app/gui/web/index.html'),'utf8');
  for(const name of ['action-events','attack-assembly','periodic-attacks'])assert.ok(html.indexOf(`src="${name}.js"`)<html.indexOf('src="periodic-assembly.js"'));
  assert.ok(html.indexOf('src="periodic-assembly.js"')<html.indexOf('src="app.js"'));
  const e=environment();e.PL.state.draft={uncommitted:true};const before=JSON.stringify(e.PL.state.plan),count=e.w.document.body.children.length;e.w.PAS.edit();assert.equal(e.w.document.body.children.length,count);assert.equal(JSON.stringify(e.PL.state.plan),before);assert.match(e.statuses.at(-1),/装备草稿/);
});
