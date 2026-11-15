const GUI_TEST_RUNTIME = require('./helpers/gui_runtime.cjs');
const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');
const helpers={module:{exports:{}},require,__dirname,console};
const harness=fs.readFileSync(path.join(__dirname,'gui_checks.test.cjs'),'utf8').split('\ntest(')[0];
vm.runInNewContext(harness+'\nmodule.exports={setup,control,button,edit,clone};',helpers);
const {setup,control,button,edit}=helpers.module.exports,clone=value=>JSON.parse(JSON.stringify(value));
const confirmation={expected_snapshot_id:'current-snapshot',expected_resource_fingerprint:'current-resource',expected_scope_fingerprint:'current-scope'};
function prepared(){const env=setup(),world=env.world,plan=env.PL.state.plan,q=env.model.abilities.find(a=>a.id==='q');
  world.RT.state={path:'native.json'};plan.plan_version=3;plan.equipment_inputs={a:{character_ref:{db_character_id:10000002,version_line:'release'}}};
  env.PL.state.caps={action_resource_source_contract:{}};q.variant='普通元素爆发';q.cooldown={group:'q',duration:20,charges:1,start:'action_start',recovery:'sequential'};
  GUI_TEST_RUNTIME.loadWebFile(world, path.join(__dirname,'../src/genshin_app/gui/web/action-resources.js'));
  const response=proposed=>({code:0,data:{state:'pending',plan:clone(proposed),action_resource_compilations:(proposed.action_resource_sources||[]).map(row=>({
    ability_id:row.ability_id,interpretation_status:row.status,required_confirmation:confirmation,
    projection:{base_cooldown_seconds:{status:'ready',value:20},base_charge_count:{status:'ready',value:1},
      base_energy_cost:{status:'ready',value:80},cost_element:{status:'ready',value:'cryo'}},
    descriptions:{name:{text:'神里流·霜灭'},description:{text:'独立GUI完整技能原文'}},
    action_spend_links:[{action_id:'q1',spend_id:'spend'}],target_paths:['ability:q.energy.amount','event:spend.amount'],issues:[]
  }))}});
  world.callApi=async(name,proposed)=>response(proposed);
  return {...env,AR:world.AR,response,open(){world.AR.edit();edit(env.dialog(),'本动作采用基础资源的依据','核对原文后采用固定基础值');}};
}
const press=(env,label)=>{const found=button(env.dialog(),label);assert.ok(found,`Missing ${label}`);return found.emit('click');};
async function bind(env){env.open();edit(env.dialog(),'用途核实状态','assumed');await press(env,'确认当前来源及用途范围');await press(env,'应用资源来源');}

test('resource editor uses the shared dialog lifecycle and refuses a busy replacement without mutation',()=>{
  const env=prepared(),before=env.PL.currentInputJson(),blocker=env.world.AppDialogKernel.create({title:'另一项操作正在提交',busy:true});
  assert.ok(blocker);env.AR.edit();assert.equal(env.world.AppDialogKernel.getActive(),blocker);
  assert.match(env.world.lastStatus,/先完成或关闭/);assert.equal(env.PL.currentInputJson(),before);blocker.finish('test-cleanup');
});

test('resource preview and confirmation do not mutate native values or promote applicability',async()=>{
  const env=prepared(),before=JSON.stringify(env.PL.state.plan);env.open();await press(env,'读取并预览当前资源');
  assert.match(env.dialog().textContent,/独立GUI完整技能原文/);assert.match(env.dialog().textContent,/基础冷却（秒）：20/);
  await press(env,'确认当前来源及用途范围');assert.equal(JSON.stringify(env.PL.state.plan),before);
  await press(env,'应用资源来源');const plan=env.PL.state.plan,row=plan.action_resource_sources[0];
  assert.equal(row.status,'pending');assert.equal(row.expected_scope_fingerprint,'current-scope');
  const q=plan.rotation_model.abilities.find(a=>a.id==='q'),spend=plan.rotation_model.events.find(e=>e.id==='spend');
  assert.equal(Object.hasOwn(q.cooldown,'duration'),false);assert.deepEqual(clone(q.energy),{});
  assert.equal(Object.hasOwn(spend,'required'),false);assert.equal(Object.hasOwn(spend,'amount'),false);
  assert.deepEqual(clone(plan.rotation_model.actions),JSON.parse(before).rotation_model.actions);
});

test('discarding a resource source draft leaves all native input unchanged',async()=>{
  const env=prepared(),before=JSON.stringify(env.PL.state.plan);env.open();await press(env,'确认当前来源及用途范围');
  press(env,'← 返回动作与能量');assert.equal(JSON.stringify(env.PL.state.plan),before);
});

test('late resource response cannot overwrite a newly loaded native plan',async()=>{
  const env=prepared();env.open();let finish;env.world.callApi=(_,proposed)=>new Promise(resolve=>{finish=()=>resolve(env.response(proposed));});
  const old=env.PL.state.plan,before=JSON.stringify(old),pending=press(env,'应用资源来源');
  const replacement=clone(old);replacement.revision++;env.PL.replace(replacement);const next=JSON.stringify(replacement);
  finish();await pending;assert.equal(JSON.stringify(old),before);assert.equal(JSON.stringify(replacement),next);
});

test('latest preview wins and edited scope clears every prior confirmation',async()=>{
  const env=prepared();env.open();const waiting=[];env.world.callApi=(_,proposed)=>new Promise(resolve=>waiting.push({proposed,resolve}));
  const first=press(env,'读取并预览当前资源');edit(env.dialog(),'已激活命座数',1);const second=press(env,'读取并预览当前资源');
  const recent=env.response(waiting[1].proposed);recent.data.action_resource_compilations[0].descriptions.description.text='最新资料';
  waiting[1].resolve(recent);await second;waiting[0].resolve(env.response(waiting[0].proposed));await first;
  assert.match(env.dialog().textContent,/最新资料/);assert.doesNotMatch(env.dialog().textContent,/独立GUI完整技能原文/);
  let sent;env.world.callApi=async(_,proposed)=>{sent=clone(proposed);return env.response(proposed);};
  await press(env,'确认当前来源及用途范围');edit(env.dialog(),'已激活命座数',0);await press(env,'应用资源来源');
  for(const key of env.AR.confirmKeys)assert.equal(sent.action_resource_sources[0][key],null);
});

test('server materialization or mutation never writes numbers back to native source input',async()=>{
  const env=prepared();env.open();env.world.callApi=async(_,proposed)=>{const q=proposed.rotation_model.abilities.find(a=>a.id==='q');
    q.cooldown.duration=999;q.energy.required=999;proposed.rotation_model.actions[0].start=999;return env.response(proposed);};
  const originalStart=env.PL.state.plan.rotation_model.actions[0].start;await press(env,'应用资源来源');
  const plan=env.PL.state.plan;assert.equal(plan.rotation_model.actions[0].start,originalStart);
  assert.equal(Object.hasOwn(plan.rotation_model.abilities.find(a=>a.id==='q').cooldown,'duration'),false);
});

test('unbind requires explicit numbers and restores matching spend values without defaults',async()=>{
  const env=prepared();await bind(env);env.AR.edit(0);const before=JSON.stringify(env.PL.state.plan);
  await press(env,'解除绑定并采用手填资源');assert.equal(JSON.stringify(env.PL.state.plan),before);assert.match(env.dialog().textContent,/不会默认恢复0或1/);
  for(const [label,value] of [['解绑后冷却秒数',0],['解绑后可用次数',1],['解绑后能量门槛',0],['解绑后实际扣能',0]])edit(env.dialog(),label,value);
  await press(env,'解除绑定并采用手填资源');const plan=env.PL.state.plan,q=plan.rotation_model.abilities.find(a=>a.id==='q');
  assert.equal(plan.action_resource_sources.length,0);assert.equal(q.cooldown.duration,0);assert.deepEqual(clone(q.energy),{required:0,amount:0});
  assert.equal(plan.rotation_model.events.find(e=>e.id==='spend').amount,0);
});

test('binding checks never synthesize missing or duplicate spend events',async()=>{
  const env=prepared();env.model.events=env.model.events.filter(e=>e.type!=='energy_spend');env.open();let calls=0;
  env.world.callApi=async()=>{calls++;assert.fail('missing spend must be rejected in the native proposal');};
  await press(env,'应用资源来源');assert.equal(calls,0);assert.match(env.dialog().textContent,/恰好一条已有扣能事件/);
  assert.equal(env.PL.state.plan.action_resource_sources,undefined);
});

test('bound ability and spend numeric controls stay empty and read-only when edited',async()=>{
  const env=prepared();await bind(env);const plan=env.PL.state.plan;
  env.RC.editAbility(plan.rotation_model.abilities.findIndex(a=>a.id==='q'));
  for(const label of ['冷却时间（秒）','最大可用次数','释放所需能量','实际扣除能量']){
    const input=control(env.dialog(),label);assert.equal(input.disabled,true);assert.equal(input.value,'');input.value='999';input.emit('change');}
  await press(env,'应用修改');const q=plan.rotation_model.abilities.find(a=>a.id==='q');
  assert.equal(Object.hasOwn(q.cooldown,'charges'),false);assert.deepEqual(clone(q.energy),{});
  env.RC.editEvent(plan.rotation_model.events.findIndex(e=>e.id==='spend'));
  for(const label of ['要求能量','扣除能量']){const input=control(env.dialog(),label);assert.equal(input.disabled,true);input.value='999';input.emit('change');}
  await press(env,'应用修改');assert.equal(Object.hasOwn(plan.rotation_model.events.find(e=>e.id==='spend'),'amount'),false);
});

test('changed ability variant requires explicit source synchronization and fresh confirmation',async()=>{
  const env=prepared();await bind(env);env.PL.state.plan.rotation_model.abilities.find(a=>a.id==='q').variant='经核实的新变体';
  env.AR.edit(0);assert.match(env.dialog().textContent,/与来源声明不同/);
  const nativeBefore=JSON.stringify(env.PL.state.plan);await press(env,'采用当前技能变体并重新核实');
  assert.equal(JSON.stringify(env.PL.state.plan),nativeBefore);let sent;
  env.world.callApi=async(_,proposed)=>{sent=clone(proposed);return env.response(proposed);};
  await press(env,'应用资源来源');assert.equal(sent.action_resource_sources[0].variant,'经核实的新变体');
  for(const key of env.AR.confirmKeys)assert.equal(sent.action_resource_sources[0][key],null);
});

test('orphan source can be explicitly removed without deleting or inventing action data',async()=>{
  const env=prepared();await bind(env);const plan=env.PL.state.plan;
  plan.rotation_model.abilities=plan.rotation_model.abilities.filter(a=>a.id!=='q');
  const before=JSON.stringify(plan.rotation_model);env.AR.drop(0);await press(env,'确认移除，保留待补输入');
  assert.equal(plan.action_resource_sources.length,0);assert.equal(JSON.stringify(plan.rotation_model),before);
});

test('source removal protects concurrent edits and permits cancellation',async()=>{
  const env=prepared();await bind(env);const plan=env.PL.state.plan,before=JSON.stringify(plan);
  env.AR.drop(0);await press(env,'← 返回动作与能量');assert.equal(JSON.stringify(plan),before);
  env.AR.drop(0);plan.revision++;await press(env,'确认移除，保留待补输入');assert.equal(plan.action_resource_sources.length,1);
});

test('pending null cooldown can leave binding mode and recover manual editing',async()=>{
  const env=prepared();await bind(env);const plan=env.PL.state.plan,q=plan.rotation_model.abilities.find(a=>a.id==='q');
  q.cooldown=null;env.AR.drop(0);await press(env,'确认移除，保留待补输入');
  assert.equal(q.cooldown,null);assert.deepEqual(clone(q.energy),{});
  env.RC.editAbility(plan.rotation_model.abilities.indexOf(q));assert.match(env.dialog().textContent,/此动作有技能冷却/);
});
