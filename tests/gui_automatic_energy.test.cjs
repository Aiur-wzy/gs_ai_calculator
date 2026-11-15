const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),vm=require('node:vm'),crypto=require('node:crypto');
const context={module:{exports:{}},require,__dirname,console};
vm.runInNewContext(fs.readFileSync(path.join(__dirname,'gui_action_resources.test.cjs'),'utf8').split('\ntest(')[0]+'\nmodule.exports={prepared,press,control,edit,bind};',context);
const {prepared,press,control,edit,bind}=context.module.exports,clone=v=>JSON.parse(JSON.stringify(v));
const review='我已核对上述事件，仅将这些扣能事件改为动作开始自动交付';
const all=n=>[n,...n.children.flatMap(all)],digest=v=>crypto.createHash('sha256').update(JSON.stringify(v)).digest('hex');
function fixture(){
  const e=prepared();e.p=e.PL.state.plan;e.calls=[];let generation='one';
  Object.defineProperty(e,'model',{get:()=>e.p.rotation_model});
  e.PL.state.fileSha='file-sha';e.PL.state.caps.action_resource_source_contract.energy_delivery={modes:['existing_events','action_start'],default:'existing_events',contract:'automatic-energy-delivery-v1',phase:'action_admission',limits:{max_receipts:40000,max_receipt_bytes:8388608}};
  const oldResponse=e.response;
  e.response=proposed=>{const response=oldResponse(proposed);
    for(const record of response.data.action_resource_compilations){const binding=proposed.action_resource_sources.find(b=>b.ability_id===record.ability_id),mode=e.AR.mode(binding),actions=proposed.rotation_model.actions.filter(a=>a.ability_id===binding.ability_id);
      record.required_confirmation={expected_snapshot_id:'current-snapshot',expected_resource_fingerprint:'current-resource',expected_scope_fingerprint:digest([generation,mode,actions.map(a=>a.id)])};
      record.status=e.AR.confirmKeys.every(k=>binding[k]===record.required_confirmation[k])?'ready':'pending';
      if(mode==='action_start'){record.action_spend_links=[];record.energy_delivery={mode,contract:'automatic-energy-delivery-v1',phase:'action_admission',links:actions.map(a=>({action_id:a.id,ability_id:a.ability_id,receipt_id:'@energy_spend:'+digest(['automatic-energy-delivery-v1',a.id])}))};}
    }return response;
  };
  e.world.callApi=async(name,input)=>{e.calls.push({name,input:clone(input)});return e.response(input);};
  e.setMode=mode=>edit(e.dialog(),'扣能交付',mode);
  e.check=()=>{const box=control(e.dialog(),review);assert.ok(box,'explicit migration review');box.checked=true;box.emit('change');};
  e.newAuto=()=>{e.model.events=e.model.events.filter(v=>v.type!=='energy_spend');e.open();e.setMode('action_start');};
  e.changeSource=()=>generation='two';return e;
}
async function existing(e){await bind(e);e.calls.length=0;e.AR.edit(0);}
async function automatic(e){e.newAuto();await press(e,'应用资源来源');}

test('legacy default stays omitted and no automatic data is added',async()=>{
  const e=fixture();await bind(e);assert.equal(Object.hasOwn(e.p.action_resource_sources[0],'energy_delivery'),false);assert.equal(e.p.rotation_model.events.some(v=>v.type==='energy_spend'),true);
});

test('new automatic draft with zero spends saves only native declarations',async()=>{
  const e=fixture();e.newAuto();const actions=clone(e.model.actions),events=clone(e.model.events);
  e.world.callApi=async(_,input)=>{const out=e.response(input);input.rotation_model.actions[0].start=999;out.data.plan.rotation_model.events.push({id:'injected',type:'energy_spend'});out.data.plan.rotation_model.abilities.find(a=>a.id==='q').energy={amount:999,delivery:'action_start'};return out;};
  await press(e,'应用资源来源');const row=e.p.action_resource_sources[0],q=e.p.rotation_model.abilities.find(a=>a.id==='q');
  assert.equal(row.energy_delivery,'action_start');for(const k of e.AR.confirmKeys)assert.equal(row[k],null);
  assert.deepEqual(clone(q.energy),{});assert.deepEqual(clone(e.p.rotation_model.actions),actions);assert.deepEqual(clone(e.p.rotation_model.events),events);
});

test('standard owned spends require explicit review and migrate in the same dialog after fresh confirmation',async()=>{
  const e=fixture();await existing(e);const original=clone(e.p),dialog=e.dialog();e.setMode('action_start');
  assert.match(dialog.textContent,/移除 spend.*来源动作 q1/);await press(e,'应用资源来源');assert.equal(e.calls.length,0);assert.match(dialog.textContent,/请先核对并勾选/);
  e.check();await press(e,'读取并预览当前资源');assert.equal(e.calls.length,2);assert.equal(e.AR.mode(e.calls[0].input.action_resource_sources[0]),'existing_events');assert.equal(e.calls[0].input.rotation_model.events.some(v=>v.id==='spend'),true);
  for(const k of e.AR.confirmKeys)assert.equal(e.calls[1].input.action_resource_sources[0][k],null);
  await press(e,'确认当前来源及用途范围');assert.equal(e.dialog(),dialog);assert.deepEqual(clone(e.p),original);
  await press(e,'应用资源来源');assert.equal(e.p.action_resource_sources[0].energy_delivery,'action_start');assert.ok(e.p.action_resource_sources[0].expected_scope_fingerprint);
  assert.deepEqual(clone(e.p.rotation_model.actions),original.rotation_model.actions);
  assert.deepEqual(clone(e.p.rotation_model.events),original.rotation_model.events.filter(v=>v.id!=='spend'));
});

test('migration can save an explicitly reviewed but unconfirmed pending draft',async()=>{
  const e=fixture();await existing(e);e.setMode('action_start');e.check();await press(e,'应用资源来源');
  assert.equal(e.p.action_resource_sources[0].energy_delivery,'action_start');for(const k of e.AR.confirmKeys)assert.equal(e.p.action_resource_sources[0][k],null);
});

for(const change of ['ability_number','ability_marker','spend_number','spend_null','delay','order','actor','repeat','unknown','missing','duplicate'])test(`migration refuses ${change} without sanitizing original input`,async()=>{
  const e=fixture();await bind(e);const q=e.model.abilities.find(a=>a.id==='q'),spend=e.model.events.find(v=>v.id==='spend'),action=e.model.actions.find(a=>a.id==='q1');
  if(change==='ability_number')q.energy.required=80;if(change==='ability_marker')q.energy.delivery='action_start';
  if(change==='spend_number')spend.amount=80;if(change==='spend_null')spend.required=null;
  if(change==='delay')spend.t+=.01;if(change==='order')spend.order=action.order;if(change==='actor')spend.character='different';
  if(change==='repeat')spend.repeat={mode:'once'};if(change==='unknown')spend.extra=true;
  if(change==='missing')e.model.actions.push({...clone(action),id:'q2'});if(change==='duplicate')e.model.events.push({...clone(spend),id:'duplicate'});
  const before=clone(e.p);e.AR.edit(0);e.setMode('action_start');e.calls.length=0;await press(e,'应用资源来源');assert.equal(e.calls.length,0);assert.equal(control(e.dialog(),review),undefined);assert.deepEqual(clone(e.p),before);assert.match(e.dialog().textContent,/迁移|冲突|重复/);
});

for(const kind of ['ownership','outcome','selector','expression','snapshot','spawn'])test(`migration cannot delete a spend referenced by ${kind}`,async()=>{
  const e=fixture();await bind(e);e.p.mechanics_model={};
  if(kind==='ownership')e.p.mechanics_model.resource_ownership=[{replaces_event_ids:['spend']}];
  if(kind==='outcome')e.model.actions[0].rule_outcomes=[{event_ids:['spend']}];
  if(kind==='selector')e.p.mechanics_model.rules=[{trigger:{event_ids:['spend']}}];
  if(kind==='expression')e.p.mechanics_model.rules=[{effects:[{value:{event_id:'spend'}}]}];
  if(kind==='snapshot')e.p.mechanics_model.hit_patterns=[{self_snapshot_event:'spend'}];
  if(kind==='spawn')e.model.events.push({id:'other',type:'energy_credit',spawn_id:'spend'});
  const before=clone(e.p);e.AR.edit(0);e.setMode('action_start');e.calls.length=0;await press(e,'应用资源来源');assert.equal(e.calls.length,0);assert.match(e.dialog().textContent,/引用或接管/);assert.deepEqual(clone(e.p),before);
});

test('unbound manual spends are not silently combined with source migration',async()=>{
  const e=fixture(),before=clone(e.p);e.open();e.setMode('action_start');await press(e,'应用资源来源');assert.equal(e.calls.length,0);assert.match(e.dialog().textContent,/先完成既有资源来源接管/);assert.deepEqual(clone(e.p),before);
});

test('invalid fresh original source prevents migration before querying the new draft',async()=>{
  const e=fixture();await existing(e);const before=clone(e.p);e.setMode('action_start');e.check();e.world.callApi=async(_,p)=>{e.calls.push(clone(p));return{code:0,data:{state:'invalid',action_resource_compilations:[{ability_id:'q'}]}};};
  await press(e,'应用资源来源');assert.equal(e.calls.length,1);assert.deepEqual(clone(e.p),before);assert.match(e.dialog().textContent,/原始来源绑定未通过校验/);
});

test('late original preflight cannot continue a migration after the file changes',async()=>{
  const e=fixture();await existing(e);const before=clone(e.p);e.setMode('action_start');e.check();let finish;
  e.world.callApi=(_,input)=>{e.calls.push(clone(input));return new Promise(resolve=>finish=()=>resolve(e.response(input)));};
  const work=press(e,'应用资源来源');e.world.RT.state.path='other.json';finish();await work;
  assert.equal(e.calls.length,1);assert.deepEqual(clone(e.p),before);
});

test('automatic confirmation requires preview and rejects a changed fresh source',async()=>{
  const e=fixture();e.newAuto();await press(e,'确认当前来源及用途范围');assert.equal(e.calls.length,0);
  await press(e,'读取并预览当前资源');e.changeSource();await press(e,'确认当前来源及用途范围');assert.match(e.dialog().textContent,/来源或扣能范围已变化/);assert.equal(e.p.action_resource_sources,undefined);
  await press(e,'确认当前来源及用途范围');await press(e,'应用资源来源');assert.ok(e.p.action_resource_sources[0].expected_scope_fingerprint);
});

test('an accepted source that changes before apply is not silently published',async()=>{
  const e=fixture();e.newAuto();await press(e,'读取并预览当前资源');await press(e,'确认当前来源及用途范围');e.changeSource();await press(e,'应用资源来源');
  assert.equal(e.p.action_resource_sources,undefined);assert.match(e.dialog().textContent,/已接受的扣能来源发生变化/);
});

for(const change of ['mode','epoch','file','sha','plan','close'])test(`late automatic ${change} reply cannot publish native changes`,async()=>{
  const e=fixture();e.newAuto();const original=e.p,before=clone(original);let finish;
  e.world.callApi=(_,p)=>new Promise(resolve=>finish=()=>resolve(e.response(p)));const work=press(e,'应用资源来源');
  if(change==='mode')e.setMode('existing_events');if(change==='epoch')e.PL.state.sourceEpoch++;
  if(change==='file')e.world.RT.state.path='different.json';if(change==='sha')e.PL.state.fileSha='different';
  if(change==='plan')e.PL.replace(clone(e.p));if(change==='close')press(e,'放弃并关闭');
  finish();await work;assert.deepEqual(clone(original),before);assert.equal(e.PL.state.plan.action_resource_sources,undefined);
});

for(const corruption of ['missing','duplicate','other_actor_route','receipt'])test(`incomplete automatic ${corruption} mapping cannot apply`,async()=>{
  const e=fixture();e.newAuto();e.world.callApi=async(_,p)=>{const out=e.response(p),record=out.data.action_resource_compilations[0];
    if(corruption==='missing')delete record.energy_delivery;if(corruption==='duplicate')record.energy_delivery.links.push(clone(record.energy_delivery.links[0]));
    if(corruption==='other_actor_route')record.energy_delivery.links[0].ability_id='other';if(corruption==='receipt')record.energy_delivery.links[0].receipt_id='spend';return out;};
  await press(e,'应用资源来源');assert.equal(e.p.action_resource_sources,undefined);assert.match(e.dialog().textContent,/完整的自动扣能模式与动作映射/);
});

test('switching back keeps the binding, leaves a missing-spend draft and permits source-owned event editing',async()=>{
  const e=fixture();await automatic(e);const actions=clone(e.p.rotation_model.actions);e.AR.edit(0);e.setMode('existing_events');await press(e,'应用资源来源');
  assert.equal(e.AR.mode(e.p.action_resource_sources[0]),'existing_events');assert.equal(e.p.rotation_model.events.some(v=>v.type==='energy_spend'),false);assert.deepEqual(clone(e.p.rotation_model.actions),actions);
  e.RC.editEvent(undefined,'energy_spend');edit(e.dialog(),'关联动作','q1');edit(e.dialog(),'事件时刻（秒）',2);edit(e.dialog(),'同刻先后序号',7);press(e,'应用修改');
  const spend=e.p.rotation_model.events.find(v=>v.type==='energy_spend');assert.equal(spend.order,7);assert.equal(Object.hasOwn(spend,'required'),false);assert.equal(Object.hasOwn(spend,'amount'),false);
});

test('automatic source can explicitly unbind without materializing a receipt as an input event',async()=>{
  const e=fixture();await automatic(e);const actions=clone(e.model.actions),events=clone(e.model.events);e.AR.edit(0);
  for(const [label,value] of [['解绑后冷却秒数',20],['解绑后可用次数',1],['解绑后能量门槛',80],['解绑后实际扣能',80]])edit(e.dialog(),label,value);
  await press(e,'解除绑定并采用手填资源');assert.equal(e.p.action_resource_sources.length,0);
  assert.deepEqual(clone(e.model.abilities.find(a=>a.id==='q').energy),{required:80,amount:80});
  assert.deepEqual(clone(e.model.actions),actions);assert.deepEqual(clone(e.model.events),events);
});

test('event editor does not open a manual spend dialog when every action is automatic',async()=>{
  const e=fixture();await automatic(e);e.model.actions=e.model.actions.filter(a=>a.ability_id==='q');
  const before=clone(e.p);e.RC.editEvent(undefined,'energy_spend');
  assert.match(e.world.lastStatus,/所有已有动作均使用自动扣能/);assert.deepEqual(clone(e.p),before);
});

test('event editor blocks a forged manual spend selection for an automatic action without closing or saving',async()=>{
  const e=fixture();await automatic(e);const before=clone(e.p);e.RC.editEvent(undefined,'energy_spend');const dialog=e.dialog();
  assert.equal(control(dialog,'关联动作').children.some(n=>n.value==='q1'),false);edit(dialog,'关联动作','q1');press(e,'应用修改');
  assert.equal(e.dialog(),dialog);assert.deepEqual(clone(e.p),before);assert.match(e.world.lastStatus,/不能同时交付/);
});

test('automatic result is read-only and explains historical admission, missing rows and finite scope',()=>{
  const e=fixture(),before=clone(e.p),check={status:'failed',automatic_energy_delivery:{planned_actions:3,committed_actions:1,not_committed_actions:1,not_verified_actions:1,max_cycles_planned_actions:5,unreported_actions:2},event_log:[
    {id:'q1',type:'action',t:1,status:'passed',energy_delivery:{status:'committed',receipt_id:'@energy_spend:test'}},
    {id:'@energy_spend:test',type:'energy_spend',delivery:'automatic_action_start',committed_at_action_start:true,action_id:'q1',parent_run_id:'0:q1',character:'a',t:1,status:'passed',before:80,required:80,amount:80,deficit:0,after:0},
    {id:'q2',type:'action',t:2,status:'failed',energy_delivery:{status:'not_committed',receipt_id:'@energy_spend:later',reason:'insufficient_energy'}}]};
  const box=e.RC.renderResult(check,e.p.manual_template);assert.match(box.textContent,/系统扣能（动作接纳）/);assert.match(box.textContent,/不是后续退款后的当前能量/);assert.match(box.textContent,/未报告 2/);assert.match(box.textContent,/自动扣能：未提交/);assert.match(box.textContent,/原因：动作开始时能量不足/);
  assert.equal(all(box).some(n=>['input','select','button','textarea'].includes(n.tagName)),false);assert.deepEqual(clone(e.p),before);
});
