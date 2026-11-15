const GUI_TEST_RUNTIME = require('./helpers/gui_runtime.cjs');
const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');
const harness=fs.readFileSync(path.join(__dirname,'gui_checks.test.cjs'),'utf8').split('\ntest(')[0],helpers={module:{exports:{}},require,__dirname,console};
vm.runInNewContext(harness+'\nmodule.exports={setup,control,button,edit,toggle,clone,Element};',helpers);
const {setup:base,control,button,edit,toggle,Element}=helpers.module.exports,clone=x=>JSON.parse(JSON.stringify(x));
function setup(){
  const h=base(),w=h.world,p=h.PL.state.plan;p.plan_version=3;
  p.rotation_model.period=8;p.rotation_model.actions=[0,2,4].map((t,i)=>({id:`cast${i}`,ability_id:'e',start:t,occupation_end:t+1,order:0,repeat:{mode:'once'}}));
  p.mechanics_model={hit_events:p.rotation_model.actions.map((a,i)=>({id:`hit${i}`,action_id:a.id})),hit_patterns:[]};
  p.action_event_profiles=[{id:'profile',kind:'calibrated_single_contact_v1',self_sampling:'current',accepted_profile_fingerprint:'a'.repeat(64)}];
  p.action_event_bindings=[{ability_id:'e',profile_id:'profile'}];
  w.AE={name:()=> '既有动作档案'};w.MC={caps:{snapshot_captures:{}},words:{},touch(){p.revision++;},render(){}};
  h.PL.state.caps={mechanics_contract:{snapshot_captures:{contract:'action-snapshot-capture-v1'}}};h.PL.state.fileSha='file-sha';w.RT.state={path:'native.plan.json'};
  let calls=0;w.callApi=async(method,proposal)=>{assert.equal(method,'compose_equipment_plan');calls++;return{code:0,data:{state:'pending',plan:clone(proposal),missing_inputs:[]}};};
  GUI_TEST_RUNTIME.loadWebFile(w, path.join(__dirname,'../src/genshin_app/gui/web/snapshot-captures.js'));
  return {...h,w,p,SC:w.SC,calls:()=>calls};
}
function fill(h){h.SC.edit();const d=h.dialog();edit(d,'采用取样的能力','e');edit(d,'从动作开始到面板取样（秒）','0.1');edit(d,'记录精度（秒）','0.000001');edit(d,'取样依据状态','assumed');edit(d,'取样校准来源与适用范围','工程校准，非实测');return d;}
function draft(extra={}){return{ability_id:'e',offset:.1,precision:.000001,status:'assumed',source:'工程校准',links:{},profiles:{},...extra};}

test('snapshot editor uses the shared dialog lifecycle and refuses a busy replacement safely',()=>{const h=setup(),before=h.PL.currentInputJson();
  const blocker=h.w.AppDialogKernel.create({title:'另一项操作正在提交',busy:true});assert.ok(blocker);h.SC.edit();
  assert.equal(h.w.AppDialogKernel.getActive(),blocker);assert.match(h.w.lastStatus,/先完成或关闭/);assert.equal(h.PL.currentInputJson(),before);blocker.finish('test-cleanup');
});

test('unknown defaults never create or associate a capture',async()=>{const h=setup(),before=clone(h.p);h.SC.edit();const d=h.dialog();
  assert.equal(control(d,'从动作开始到面板取样（秒）').value,'');assert.equal(control(d,'记录精度（秒）').value,'');assert.equal(control(d,'取样依据状态').value,'pending');
  await button(d,'应用取样草稿').emit('click');assert.deepEqual(clone(h.p),before);assert.equal(h.calls(),0);
});
test('one calibration expands existing actions only without implicit consumers',async()=>{const h=setup(),axis=clone(h.p.rotation_model),d=fill(h);
  await button(d,'应用取样草稿').emit('click');assert.deepEqual(clone(h.p.rotation_model),axis);assert.equal(h.p.mechanics_model.snapshot_captures.length,3);
  assert.deepEqual(clone(h.p.mechanics_model.snapshot_captures.map(c=>c.id)),['capture.action.cast0','capture.action.cast1','capture.action.cast2']);
  assert.ok(h.p.mechanics_model.hit_events.every(x=>!x.self_snapshot_event));assert.equal(h.p.action_event_profiles[0].self_sampling,'current');assert.equal(h.p.action_event_profiles[0].accepted_profile_fingerprint,'a'.repeat(64));
});
test('explicit manual and profile association clears acceptance but never accepts',async()=>{const h=setup(),d=fill(h);
  const labels=[];const walk=n=>{if(n.attributes['aria-label'])labels.push(n.attributes['aria-label']);n.children.forEach(walk);};walk(d);
  toggle(d,labels.find(x=>x.startsWith('关联手工命中 1')),true);toggle(d,'关联动作档案 · 既有动作档案',true);
  await button(d,'应用取样草稿').emit('click');assert.equal(h.p.mechanics_model.hit_events[0].self_snapshot_event,'capture.action.cast0');
  assert.ok(!h.p.mechanics_model.hit_events[1].self_snapshot_event);assert.equal(h.p.action_event_profiles[0].self_sampling,'action_capture');assert.equal(h.p.action_event_profiles[0].accepted_profile_fingerprint,null);
});
test('bulk expansion preserves existing different calibration and ID',()=>{const h=setup(),old={id:'preserved',action_id:'cast0',offset:.05,precision:.002,status:'confirmed',source:'先前来源'};
  h.p.mechanics_model.snapshot_captures=[old];const proposal=h.SC.propose(h.p,draft());assert.deepEqual(clone(proposal.mechanics_model.snapshot_captures[0]),old);assert.equal(proposal.mechanics_model.snapshot_captures.length,3);assert.equal(h.p.mechanics_model.snapshot_captures.length,1);
});
test('unrelated existing snapshot reference cannot be overwritten',()=>{const h=setup();h.p.mechanics_model.hit_events[0].self_snapshot_event='other';
  assert.throws(()=>h.SC.propose(h.p,draft({links:{'hit_events:0':true}})),/不能覆盖/);assert.equal(h.p.mechanics_model.hit_events[0].self_snapshot_event,'other');
});
test('associated multi channel only changes explicitly chosen channel',()=>{const h=setup();h.p.action_event_profiles[0].channels=[{channel_id:'slice',self_sampling:'current'},{channel_id:'blast',self_sampling:'action_start'}];
  const p=h.SC.propose(h.p,draft({profiles:{'profile:slice':true}}));assert.equal(p.action_event_profiles[0].channels[0].self_sampling,'action_capture');assert.equal(p.action_event_profiles[0].channels[1].self_sampling,'action_start');assert.equal(p.action_event_profiles[0].accepted_profile_fingerprint,null);
});
test('removing capture requires explicit manual unlink and invalidates auto profile',()=>{const h=setup();h.p=h.PL.state.plan=h.SC.propose(h.p,draft({links:{'hit_events:0':true},profiles:{profile:true}}));h.p.action_event_profiles[0].accepted_profile_fingerprint='b'.repeat(64);
  assert.throws(()=>h.SC.propose(h.p,draft({index:0,remove:true})),/先明确取消/);
  const out=h.SC.propose(h.p,draft({index:0,remove:true,links:{'hit_events:0':false}}));assert.equal(out.mechanics_model.snapshot_captures.length,2);assert.ok(!out.mechanics_model.hit_events[0].self_snapshot_event);assert.equal(out.action_event_profiles[0].self_sampling,'action_capture');assert.equal(out.action_event_profiles[0].accepted_profile_fingerprint,null);
});
test('profile spanning another ability cannot acquire partial capture association',()=>{const h=setup();h.p.rotation_model.actions.push({id:'other',ability_id:'other.e',start:6,occupation_end:7});h.p.action_event_bindings.push({ability_id:'other.e',profile_id:'profile'});
  assert.throws(()=>h.SC.propose(h.p,draft({profiles:{profile:true}})),/其他能力/);
});
for(const [label,change] of Object.entries({epoch:h=>h.PL.state.sourceEpoch++,sha:h=>h.PL.state.fileSha='new',path:h=>h.w.RT.state.path='new',draft:h=>h.PL.state.draft={},plan:h=>h.PL.state.plan=clone(h.p),caps:h=>h.PL.state.caps.mechanics_contract.snapshot_captures.contract='changed',close:h=>button(h.dialog(),'放弃并关闭').emit('click'),edit:h=>edit(h.dialog(),'记录精度（秒）','0.002')})){
  test(`late compose is ignored after ${label}`,async()=>{const h=setup(),d=fill(h),before=clone(h.p);let resolve;h.w.callApi=(_,p)=>new Promise(r=>resolve=()=>r({code:0,data:{state:'pending',plan:clone(p)}}));
    const job=button(d,'应用取样草稿').emit('click');change(h);resolve();await job;assert.deepEqual(clone(h.p),before);assert.match(h.w.lastStatus,/旧面板取样响应/);
  });
}
test('fallback capability changes also invalidate pending response',async()=>{const h=setup();delete h.PL.state.caps.mechanics_contract;const d=fill(h),before=clone(h.p);let resolve;
  h.w.callApi=(_,p)=>new Promise(r=>resolve=()=>r({code:0,data:{state:'pending',plan:clone(p)}}));const job=button(d,'应用取样草稿').emit('click');h.w.MC.caps.snapshot_captures={contract:'new-fallback'};resolve();await job;assert.deepEqual(clone(h.p),before);
});
test('server cannot replace native axis or inject materialization',async()=>{const h=setup(),d=fill(h),before=clone(h.p);h.w.callApi=async(_,p)=>{p.rotation_model.actions[0].start=.2;return{code:0,data:{state:'ready',plan:p}};};
  await button(d,'应用取样草稿').emit('click');assert.deepEqual(clone(h.p),before);assert.match(d.textContent,/未应用修改/);
});
test('only local native delta commits even if server supplies compiled cache',async()=>{const h=setup(),d=fill(h);h.w.callApi=async(_,p)=>{p.resolved_template={server:'compiled'};p.resolution={server:'cache'};return{code:0,data:{state:'pending',plan:p}};};
  await button(d,'应用取样草稿').emit('click');assert.equal(h.p.resolved_template,undefined);assert.equal(h.p.resolution,undefined);assert.equal(h.p.mechanics_model.snapshot_captures.length,3);
});
test('preview and cancel never change native plan',async()=>{const h=setup(),d=fill(h),before=clone(h.p);await button(d,'预览面板取样').emit('click');assert.deepEqual(clone(h.p),before);button(d,'放弃并关闭').emit('click');assert.deepEqual(clone(h.p),before);});
test('decimal microseconds reject bool, rounding, zero precision and bounds',()=>{const h=setup();assert.equal(h.SC.ticks(.000001),1);assert.equal(h.SC.ticks(1e-6),1);
  for(const value of [true,null,.0000001,Infinity])assert.throws(()=>h.SC.ticks(value));for(const extra of [{precision:0},{offset:1.000001},{offset:-.1}])assert.throws(()=>h.SC.propose(h.p,draft(extra)));
});
test('read-only details distinguish observations from damage and leave old result alone',()=>{const h=setup(),box=new Element();h.SC.renderTrace(box,{hits:[]});assert.equal(box.children.length,0);
  h.SC.renderTrace(box,{snapshot_captures:{trace:[{type:'snapshot_capture',t:.1,status:'captured',capture_id:'c'},{type:'snapshot_capture_reference',t:.2,usage:'hit_self',capture_id:'c'}]}});
  assert.match(box.textContent,/已完成面板取样/);assert.match(box.textContent,/引用面板取样/);assert.match(box.textContent,/不添加伤害/);assert.doesNotMatch(box.textContent,/总伤害|DPS/);
});
