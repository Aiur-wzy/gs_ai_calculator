const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');
const helpers={module:{exports:{}},require,__dirname,console};
const harness=fs.readFileSync(path.join(__dirname,'gui_hit_parameters_review.test.cjs'),'utf8').split('\ntest(')[0];
vm.runInNewContext(harness+'\nmodule.exports={setup,Element,all};',helpers);
const {setup,Element,all}=helpers.module.exports;

test('threshold lifecycle completion names target identity without claiming Buff activation',()=>{
 const e=setup(),box=new Element(),result={resource_threshold_triggers:{trace:
  ['lifecycle_started','lifecycle_kept','lifecycle_ended','no_active_instance'].map(outcome=>({
   type:'threshold_dispatch_completed',time_us:200000,phase:'post',outcome,lifecycle_id:'field_b',
   target_generation_uid:'actual-instance',source_event_id:'consume',source_run_id:'actual-consume',
   counter_observation:{counter_id:'spent'},before:8,after:16,threshold_atoms:16,comparison:'enter_gte'}))}};
 const before=JSON.stringify(result);e.MC.renderResourceThresholds(box,result,e.PL.state.plan.manual_template);
 const table=all(box).find(n=>n.tagName==='table');
 for(const text of ['领域已开始','保留原领域','领域已结束','没有活动领域，无需结束','field_b / actual-instance','consume / actual-consume','消费累计 向上达到 16 / 8 → 16'])assert.ok(table.textContent.includes(text),text);
 assert.ok(!table.textContent.includes('收益已激活'));assert.equal(JSON.stringify(result),before);
 assert.ok(!all(box).some(n=>['input','select','button'].includes(n.tagName)));
});

test('actual control rows distinguish steps, completed batches and failed prefix; issued is separate',()=>{
 const e=setup(),box=new Element(),result={state_runtime:{contract:'finite-state-mechanics-bridge-v2',
  issued_trace:[{type:'lifecycle_start',lifecycle_id:'issued-only'}],execution_trace:[
   {type:'state_control_completed',kind:'lifecycle_start',lifecycle_id:'field',control_sequence:1,event_run_id:'source-actual'},
   {type:'state_batch_completed',batch_id:'batch-1',control_sequence:1,step_count:1},
   {type:'state_delivery_failed',batch_id:'batch-2',control_sequence:3,applied_steps:2,message:'资格待核'}]}};
 const before=JSON.stringify(result);e.MC.renderStateRuntime(box,result);
 const table=all(box).find(n=>n.tagName==='table');
 for(const text of ['领域控制已提交','开始领域 / field','控制批次已完成','失败：资格待核','3 / batch-2 / 2','source-actual'])assert.ok(table.textContent.includes(text),text);
 assert.ok(!table.textContent.includes('累计控制')&&!table.textContent.includes('issued-only'));
 assert.equal(JSON.stringify(result),before);
});
