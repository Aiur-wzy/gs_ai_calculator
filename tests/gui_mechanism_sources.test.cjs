const GUI_TEST_RUNTIME = require('./helpers/gui_runtime.cjs');
const Python = require('./helpers/python_command.cjs');
const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');
const readline=require('node:readline');
// Author UI integration: real public caps/query/compose over one isolated tiny DB.
// The independently owned bridge contains raw fixtures, not GUI decision oracles.
const helpers={module:{exports:{}},require,__dirname,console};
vm.runInNewContext(fs.readFileSync(path.join(__dirname,'gui_hit_parameters_review.test.cjs'),'utf8').split('\ntest(')[0]+
  '\nmodule.exports={setup,press,control,Element};',helpers);
const {setup,press,control,Element}=helpers.module.exports,clone=x=>JSON.parse(JSON.stringify(x));
let child,initial,serial=0;const pending=new Map();let stderr='';
function request(operation,extra={}){return new Promise((resolve,reject)=>{const id=++serial;pending.set(id,{resolve,reject});child.stdin.write(JSON.stringify({id,operation,...extra})+'\n');});}
test.before(async()=>{
  const workspace=path.join(__dirname,'../outputs/engine-continuation-20261002/ec20-gui-author-'+Date.now());
  child=Python.spawn(['-X','utf8',path.join(__dirname,'mechanism_gui_review_bridge.py'),workspace],{cwd:path.join(__dirname,'..'),windowsHide:true,stdio:['pipe','pipe','pipe']});
  child.stderr.on('data',data=>stderr+=data);child.on('error',error=>{for(const p of pending.values())p.reject(error);});
  child.on('exit',code=>{for(const p of pending.values())p.reject(Error(`Bridge exited ${code}: ${stderr}`));pending.clear();});
  readline.createInterface({input:child.stdout}).on('line',line=>{const response=JSON.parse(line),p=pending.get(response.id);pending.delete(response.id);p?.resolve(response);});
  const response=await request('init');assert.equal(response.code,0,response.message);initial=response.data;
});
test.after(()=>{child?.stdin.end();});
function env(plan=initial.plan){
  const e=setup(),w=e.world;for(const file of ['action-events','mechanism-sources'])GUI_TEST_RUNTIME.loadWebFile(w, path.join(__dirname,`../src/genshin_app/gui/web/${file}.js`), {filename:file});
  e.PL.state.caps=clone(initial.caps);e.PL.replace(clone(plan),'mechanism.json','input-file-sha',true);
  const replies=[],statuses=[];w.setStatus=value=>statuses.push(value);w.callApi=async(name,data)=>{const reply=await request(name==='compose_equipment_plan'?'compose':'query',name==='compose_equipment_plan'?{plan:data}:{request:data});replies.push(reply);return reply;};
  const field=async(label,value)=>{const node=control(e.dialog(),label);node.value=String(value);await node.emit('change');};
  return {...e,w,replies,statuses,field,open:index=>w.MS.edit(index)};
}

test('cast selection UI binds existing abilities and keeps optional identity explicit',async()=>{
  const e=env();e.open(0);const original=e.w.callApi;
  e.w.callApi=async(name,payload)=>{const reply=await original(name,payload);
    if(name==='query_runtime'&&payload.action==='mechanism_profile'){
      const q=reply.data.compiler_result.execution_qualification;q.schema_version=2;
      q.target_dependency={mode:'none'};q.parent.activation={offset_policy:'calibrated'};
    }return reply;};
  await press(e.dialog(),'读取当前引用资料');
  assert.match(e.dialog().textContent,/关联成功施放/);assert.match(e.dialog().textContent,/无需敌方目标/);
  const ability=e.PL.state.plan.rotation_model.abilities.find(a=>a.character===e.PL.state.plan.mechanism_sources[0].actor);
  await e.field('添加施放能力',ability.id);await e.field('补充命座',0);
  assert.equal(control(e.dialog(),'施放能力 1').value,ability.id);
  assert.equal(control(e.dialog(),'补充命座').value,'0');
  assert.equal(e.PL.state.plan.mechanism_sources[0].cast_binding,undefined);
  await press(e.dialog(),'移除施放能力 1');assert.match(e.dialog().textContent,/添加施放能力/);
});

test('versioned compiler receipts and percentage stat labels stay readable',()=>{
  const e=env();assert.equal(e.w.MS.supportsCompiler('typed-mechanism-app-v1'),true);
  assert.equal(e.w.MS.supportsCompiler('typed-mechanism-app-v2'),true);
  assert.equal(e.w.MS.supportsCompiler('typed-mechanism-app-v3'),true);
  assert.equal(e.w.MS.supportsCompiler('unregistered-compiler'),false);
  const description=e.w.MS.ruleText({effects:[{benefit_type_id:'benefit.stat_pct.def',value:.2}]});
  assert.match(description,/基础防御力百分比加成/);
});
test('contact source form keeps event routes server owned and makes reaction scope explicit',async()=>{
  const e=env(),plan=clone(e.PL.state.plan);plan.mechanism_sources[0].calibration=null;
  e.PL.replace(plan,'mechanism.json','input-file-sha',true);e.open(0);const original=e.w.callApi;
  e.w.callApi=async(name,payload)=>{const reply=await original(name,payload);
    if(name==='query_runtime'&&payload.action==='mechanism_profile'){
      const q=reply.data.compiler_result.execution_qualification;q.schema_version=3;
      q.parent.profile_kind='actual_contact_v1';q.parent.activation={phase:'post',offset_policy:'fixed_zero'};
      q.scenario_requirements.no_provider_transformative_damage=true;
    }return reply;};
  await press(e.dialog(),'读取当前引用资料');
  assert.match(e.dialog().textContent,/所有相关真实命中/);
  assert.match(e.dialog().textContent,/转化反应伤害（计算时复核）/);
  assert.doesNotMatch(e.dialog().textContent,/添加施放能力|添加生效时间校准/);
  assert.equal(e.PL.state.plan.mechanism_sources[0].cast_binding,undefined);
  assert.equal(e.PL.state.plan.mechanism_sources[0].calibration,null);
  assert.equal(e.PL.state.plan.mechanism_sources[0].scenario.values.no_provider_transformative_damage,undefined);
});
test('contact preview shows candidate time separately from confirmed benefit activation',()=>{
  const e=env(),box=new Element('div');
  const row={actor:'a',status:'pending',projection:{execution_qualification:{schema_version:3}},
    parent_graph:{parents:[{action_id:'cast0',action_tick:0,absolute_tick:200000}]},issues:[]};
  e.w.MS.preview(box,row,e.PL.state.plan);
  assert.match(box.textContent,/候选命中时点（秒）/);
  assert.match(box.textContent,/候选命中不等于收益已触发/);
  assert.match(box.textContent,/0.2/);
});
test('real caps and two native sources remain non-executable knowledge candidates',async()=>{
  const e=env(),native=clone(e.PL.state.plan);assert.equal(e.w.MS.caps().compiler,'typed-mechanism-app-v1');
  for(let index=0;index<2;index++){
    e.open(index);await press(e.dialog(),'读取当前引用资料');assert.match(e.dialog().textContent,/模板就绪不代表本轴已可执行/);
    await press(e.dialog(),'预览本轴机制');
    assert.doesNotMatch(e.dialog().textContent,/接受本次来源与场景/);
    await press(e.dialog(),'应用机制引用');
    assert.equal(e.PL.state.plan.mechanism_sources[index].accepted_fingerprint,null);
  }
  const reply=await request('compose',{plan:e.PL.state.plan});assert.equal(reply.code,0);assert.equal(reply.data.state,'pending',JSON.stringify(reply.data.missing_inputs));
  assert.equal(reply.data.mechanism_compilations.length,2);assert.deepEqual(clone(e.PL.state.plan.manual_template),native.manual_template);
  assert.ok(reply.data.mechanism_compilations.every(row=>row.execution_authority==='none'&&row.required_confirmation===null));
  assert.deepEqual(clone(e.PL.state.plan.mechanics_model),native.mechanics_model);
  assert.equal(e.PL.state.plan.mechanism_sources.some(s=>'rules' in s||'projection' in s||'source_material' in s),false);
});
test('real discovery uses explicit actor source identity and never picks a catalog item automatically',async()=>{
  const plan=clone(initial.plan);plan.mechanism_sources=[];const e=env(plan);e.open();await e.field('机制提供者','a');
  await press(e.dialog(),'发现受审机制来源');assert.match(e.dialog().textContent,/单项也需要明确选择/,JSON.stringify([e.replies,e.statuses]));
  assert.equal(control(e.dialog(),'目录中的来源').value,'');assert.equal(e.PL.state.plan.mechanism_sources.length,0);
  await e.field('目录中的来源','skill.current-atk-limited-addition');assert.match(e.dialog().textContent,/基础区加算/);
  assert.match(e.dialog().textContent,/提供者当前攻击力/);assert.match(e.dialog().textContent,/独立 7 次/);
  assert.equal(control(e.dialog(),'目标类型').value,'unknown');assert.equal(control(e.dialog(),'所有队员存活').value,'unknown');
  await press(e.dialog(),'应用机制引用');assert.equal(e.PL.state.plan.mechanism_sources.length,1);
  assert.equal(e.PL.state.plan.mechanism_sources[0].accepted_fingerprint,null);assert.deepEqual(clone(e.PL.state.plan.mechanism_sources[0].scenario.values),{});
});
test('real forms preserve unknown and expose no direct knowledge confirmation',async()=>{
  const e=env();e.open(0);await press(e.dialog(),'读取当前引用资料');await e.field('所有队员存活','unknown');
  await press(e.dialog(),'预览本轴机制');assert.doesNotMatch(e.dialog().textContent,/接受本次来源与场景/);
  await press(e.dialog(),'应用机制引用');assert.equal(e.PL.state.plan.mechanism_sources[0].scenario.values.all_party_alive,null);
  assert.equal(e.PL.state.plan.mechanism_sources[0].accepted_fingerprint,null);
});
test('actual DEF holdout is described by typed stat, not provider/character algorithm',()=>{
  const e=env(),box=new Element();e.w.MS.profilePreview(box,initial.profiles['review.def-addition']);
  assert.match(box.textContent,/提供者当前防御力/);assert.doesNotMatch(box.textContent,/提供者当前攻击力/);
  const reaction=new Element();e.w.MS.profilePreview(reaction,initial.profiles['passive.swirl-elemental-bonus']);
  assert.match(reaction.textContent,/元素反应发生时/);assert.match(reaction.textContent,/提供者触发时元素精通/);
  assert.doesNotMatch(reaction.textContent,/独立 \d+ 次/);
});
test('actual missing actor can be removed atomically without any discovery',async()=>{
  const plan=clone(initial.plan);plan.mechanism_sources[0].actor='orphan';const e=env(plan),second=clone(e.PL.state.plan.mechanism_sources[1]);
  e.open(0);assert.match(e.dialog().textContent,/提供者已移除/);await press(e.dialog(),'移除机制引用');
  assert.equal(e.PL.state.plan.mechanism_sources.length,1,JSON.stringify(e.replies.map(r=>({code:r.code,message:r.message,state:r.data?.state,records:r.data?.mechanism_compilations?.map(s=>({id:s.id,actor:s.actor,status:s.status,required:s.required_confirmation})),plan:r.data?.plan?.mechanism_sources}))));assert.deepEqual(clone(e.PL.state.plan.mechanism_sources[0]),second);
});
test('last source removal accepts the real no-record report without creating derived input',async()=>{
  const plan=clone(initial.plan);plan.mechanism_sources=plan.mechanism_sources.slice(0,1);const e=env(plan);e.open(0);
  await press(e.dialog(),'移除机制引用');assert.equal(e.PL.state.plan.mechanism_sources.length,0,JSON.stringify([e.statuses,e.replies.map(r=>({code:r.code,message:r.message,state:r.data?.state,records:r.data?.mechanism_compilations,plan:r.data?.plan?.mechanism_sources}))]));
});
for(const mutation of ['epoch','sha','path','caps','draft','input','replacement'])test(`late actual compose ignored after ${mutation}`,async()=>{
  const e=env();e.open(0);const old=e.PL.state.plan;let finish;const original=e.w.callApi;
  e.w.callApi=async(name,payload)=>{const reply=await original(name,payload);return new Promise(resolve=>finish=()=>resolve(reply));};
  const flight=press(e.dialog(),'应用机制引用');while(!finish)await new Promise(resolve=>setTimeout(resolve,5));
  if(mutation==='epoch')e.PL.state.sourceEpoch++;
  if(mutation==='sha')e.PL.state.fileSha='new';if(mutation==='path')e.RT.state.path='new.json';
  if(mutation==='caps')e.PL.state.caps.mechanism_source_contract.compiler='changed';
  if(mutation==='draft')e.PL.state.draft={pending:true};if(mutation==='input')old.mechanism_sources[0].scenario.source+=' changed';
  if(mutation==='replacement')e.PL.replace(clone(old),'replacement.json','other',true);
  const expected=JSON.stringify(old),fresh=JSON.stringify(e.PL.state.plan);finish();await flight;
  assert.equal(JSON.stringify(old),expected);assert.equal(JSON.stringify(e.PL.state.plan),fresh);
});
test('malformed catalog reply cannot authorize another subject or choose a source',async()=>{
  const plan=clone(initial.plan);plan.mechanism_sources=[];const e=env(plan);e.open();await e.field('机制提供者','a');
  const original=e.w.callApi;e.w.callApi=async(name,payload)=>{const reply=await original(name,payload);reply.data.items[0].subject.target_id=999;return reply;};
  await press(e.dialog(),'发现受审机制来源');assert.match(e.dialog().textContent,/目录回复的范围/);assert.equal(e.PL.state.plan.mechanism_sources.length,0);
});
test('malformed source reply cannot replace selected revision or line',async()=>{
  const e=env();e.open(0);const original=e.w.callApi;e.w.callApi=async(name,payload)=>{const reply=await original(name,payload);reply.data.snapshot.version_line='beta';return reply;};
  await press(e.dialog(),'读取当前引用资料');assert.match(e.dialog().textContent,/资料回复与所选来源/);
});
test('failed remove preserves native; service plan mutation is never copied',async()=>{
  const e=env();e.open(0);const before=JSON.stringify(e.PL.state.plan);e.w.callApi=async()=>({code:1,message:'synthetic refusal'});
  await press(e.dialog(),'移除机制引用');assert.equal(JSON.stringify(e.PL.state.plan),before);
  e.w.callApi=async(name,data)=>{const reply=await request('compose',{plan:data});reply.data.plan.manual_template.hits=[];return reply;};
  await press(e.dialog(),'应用机制引用');assert.deepEqual(clone(e.PL.state.plan.manual_template.hits),JSON.parse(before).manual_template.hits);
});
test('native input and both pre-existing editor paths remain wired alongside generic entry',()=>{
  const e=env(),box=new Element();e.w.MS.render(box);assert.match(box.textContent,/添加通用机制来源/);
  assert.match(fs.readFileSync(path.join(__dirname,'../src/genshin_app/gui/web/checks.js'),'utf8'),/window\.MS\?\.render\(box\)/);
  const index=fs.readFileSync(path.join(__dirname,'../src/genshin_app/gui/web/index.html'),'utf8');
  assert.ok(index.indexOf('mechanism-sources.js')<index.indexOf('src="app.js"'));
  assert.match(index,/limited-hit-benefit\.js/);assert.match(index,/energy-generation\.js/);
});
test('readonly execution rows keep actual activation, quota and stat sampling values',()=>{
  const e=env(),box=new Element();
  // Synthetic display-only ledger; the actual query/compose cases above prove source shapes.
  const report={mechanism_compilations:[{id:'local',actor:'a',rules:[{id:'generated'}],issues:[]}],mechanism_summary:{activations:1,consumed_hits:1,sampled_events:1,applied_benefits:1},
    mechanics_trace:[{rule_id:'generated',type:'activation',t:.1,expires_at:[15.1]},{rule_id:'generated',type:'quota',t:.2,before:7,after:6,consumed:true}],
    sampling_trace:[{rule_id:'generated',character:'a',t:.2,sample_t:.2,stat:'def',source_value:500,value:50}]};
  e.w.MS.renderReceipt(box,report,e.PL.state.plan);assert.match(box.textContent,/7 → 6/);assert.match(box.textContent,/到期 15.1 秒/);
  assert.match(box.textContent,/防御力/);assert.match(box.textContent,/500/);assert.match(box.textContent,/换算收益/);
});
