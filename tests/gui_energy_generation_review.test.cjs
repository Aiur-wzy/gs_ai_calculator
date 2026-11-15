const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');
// Reuse only DOM/input builders; all transition expectations below are independent.
const helpers={module:{exports:{}},require,__dirname,console};
vm.runInNewContext(fs.readFileSync(path.join(__dirname,'gui_energy_generation.test.cjs'),'utf8').split('\ntest(')[0]+'\nmodule.exports={setup,applied,press,control};',helpers);
const {setup,applied,press,control}=helpers.module.exports,clone=value=>JSON.parse(JSON.stringify(value));
const all=node=>[node,...node.children.flatMap(all)];

test('review: escape during pending acceptance cannot alter the existing native plan',async()=>{
  const e=setup();e.ready();await press(e.dialog(),'预览产球来源与命中');
  const before=e.PL.currentInputJson();let finish,submitted;
  e.world.callApi=(_,plan)=>{submitted=clone(plan);return new Promise(resolve=>finish=resolve);};
  const waiting=press(e.dialog(),'接受本次产球来源与场景');
  e.dialog().emit('cancel');finish(e.response(submitted));await waiting;
  assert.equal(e.PL.currentInputJson(),before);assert.equal(e.p.energy_generation_sources,undefined);
});

test('review: valid confirmation hash and allowed issue code cannot override a pending fresh record',async()=>{
  const e=setup();e.ready();await press(e.dialog(),'预览产球来源与命中');
  e.world.callApi=async(_,plan)=>{const reply=e.response(plan),row=reply.data.energy_generation_compilations[0];
    row.status='pending';row.issues=[{code:'energy_generation_confirmation_required',message:'not executable'}];return reply;};
  await press(e.dialog(),'接受本次产球来源与场景');
  assert.match(e.dialog().textContent,/未记录接受/);
  await press(e.dialog(),'应用产球来源');
  assert.equal(e.p.energy_generation_sources[0].accepted_fingerprint,null);
});

test('review: an existing accepted source cannot be reapplied after a fresh unsupported result',async()=>{
  const e=setup();await applied(e);const before=e.PL.currentInputJson();e.world.EG.edit(0);
  e.world.callApi=async(_,plan)=>{const reply=e.response(plan),row=reply.data.energy_generation_compilations[0];
    row.status='pending';row.issues=[{code:'energy_trigger_unmatched',message:'new parent branch'}];return reply;};
  await press(e.dialog(),'应用产球来源');
  assert.match(e.dialog().textContent,/无法执行，未应用/);assert.equal(e.PL.currentInputJson(),before);
});

test('review: changing the origin after local acceptance clears the accepted fingerprint without native side effects',async()=>{
  const e=setup();e.ready();const before=e.PL.currentInputJson();
  await press(e.dialog(),'预览产球来源与命中');await press(e.dialog(),'接受本次产球来源与场景');
  e.field('数据库产球来源标识','b'.repeat(64));assert.equal(e.PL.currentInputJson(),before);
  await press(e.dialog(),'应用产球来源');
  const row=e.p.energy_generation_sources[0];assert.equal(row.origin_reference_id,'b'.repeat(64));assert.equal(row.accepted_fingerprint,null);
  assert.equal(row.rules,undefined);assert.equal(row.resource_projection,undefined);assert.equal(row.parent_links,undefined);
});

test('review: removal of a source with a missing parent leaves all original events and unrelated sources intact',async()=>{
  const e=setup();await applied(e);const source=clone(e.p.energy_generation_sources[0]);
  e.p.energy_generation_sources.push({...clone(source),id:'other',action_event_profile_id:'unrelated'});
  e.p.energy_generation_sources[0].replaces_spawn_ids=['old'];
  e.p.rotation_model.events=[{id:'old',type:'energy_spawn',action_id:'a1',t:1,count:3},
    {id:'credit',type:'energy_credit',spawn_id:'old',t:2,count:3},{id:'refund',type:'energy_direct',t:3,amounts:{a:7}}];
  e.p.action_event_profiles=[];e.p.action_event_bindings=[];
  const rotation=clone(e.p.rotation_model),other=clone(e.p.energy_generation_sources[1]);e.world.EG.edit(0);
  await press(e.dialog(),'移除产球来源');
  assert.deepEqual(clone(e.p.energy_generation_sources),[other]);assert.deepEqual(clone(e.p.rotation_model),rotation);
});

test('review: late removal result cannot overwrite a user event edit',async()=>{
  const e=setup();await applied(e);e.world.EG.edit(0);let finish,submitted;
  e.world.callApi=(_,plan)=>{submitted=clone(plan);return new Promise(resolve=>finish=resolve);};
  const waiting=press(e.dialog(),'移除产球来源');
  e.p.rotation_model.events.push({id:'new-refund',type:'energy_direct',t:3,amounts:{a:9}});
  const before=e.PL.currentInputJson();finish(e.response(submitted));await waiting;
  assert.equal(e.PL.currentInputJson(),before);assert.equal(e.p.energy_generation_sources.length,1);
});

test('review: migration controls offer only spawn roots and allow repair of an invalid typed reference',async()=>{
  const e=setup();await applied(e);e.p.rotation_model.events=[
    {id:'own-spawn',type:'energy_spawn',action_id:'a1',t:1,count:3},
    {id:'other-spawn',type:'energy_spawn',action_id:'different-action',t:1,count:99},
    {id:'spend',type:'energy_spend',action_id:'a1',t:0,amount:60},
    {id:'credit',type:'energy_credit',spawn_id:'own-spawn',t:2,count:3},
    {id:'refund',type:'energy_direct',action_id:'a1',t:3,amounts:{a:9}}];
  e.p.energy_generation_sources[0].replaces_spawn_ids=['spend'];const rotation=clone(e.p.rotation_model);e.world.EG.edit(0);
  assert.match(e.dialog().textContent,/旧产球引用已失效：spend/);
  const checks=all(e.dialog()).filter(node=>node.tagName==='input'&&node.type==='checkbox');
  assert.equal(checks.length,1,'Only the eligible spawn root is a selection control');
  await press(e.dialog(),'移除这条失效接管声明');await press(e.dialog(),'应用产球来源');
  assert.deepEqual(clone(e.p.energy_generation_sources[0].replaces_spawn_ids),[]);
  assert.deepEqual(clone(e.p.rotation_model),rotation);
});
