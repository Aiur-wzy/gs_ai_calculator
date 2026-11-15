const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');
const helpers={module:{exports:{}},require,__dirname,console};
const harness=fs.readFileSync(path.join(__dirname,'gui_hit_parameters_review.test.cjs'),'utf8').split('\ntest(')[0];
vm.runInNewContext(harness+'\nmodule.exports={setup,Element,all};',helpers);
const {setup,Element,all}=helpers.module.exports;

test('failed control identifies actual failure and committed control separately without mutation',()=>{
 const e=setup(),box=new Element(),result={state_runtime:{execution_trace:[
  {type:'state_control_completed',time_us:100000,lifecycle_id:'field',kind:'lifecycle_start'},
  {type:'state_control_failed',time_us:200000,lifecycle_id:'field',payload:{kind:'lifecycle_established'},message:'Independent control budget rejection'}]}};
 const before=JSON.stringify(result);e.MC.renderStateRuntime(box,result,e.PL.state.plan.manual_template);
 assert.ok(box.textContent.includes('累计控制已提交'));
 assert.ok(box.textContent.includes('失败：Independent control budget rejection'));
 assert.equal(JSON.stringify(result),before);
 assert.ok(!all(box).some(n=>['input','select','textarea','button'].includes(n.tagName)));
});

test('missing counter amounts are unknown while explicit zero stays zero and full receipt survives limit',()=>{
 const e=setup(),box=new Element(),rows=Array.from({length:251},(_,i)=>({
  type:'counter_accumulation',counter_id:'c',time_us:i,requested:8,resource_accepted:0,
  accepted:i===0?null:0,before:0,after:0,resource_receipt_id:'receipt-'+i}));
 const result={resource_counters:{counter_units:{c:10},trace:rows}},before=JSON.stringify(result);
 e.MC.renderResourceCounters(box,result);
 assert.ok(box.textContent.includes('8 / 0 / —'));
 assert.ok(box.textContent.includes('8 / 0 / 0'));
 assert.ok(box.textContent.includes('receipt-250'));
 assert.ok(box.textContent.includes('已显示前 250 条，共 251 条'));
 assert.equal(JSON.stringify(result),before);
 assert.ok(!all(box).some(n=>['input','select','textarea','button'].includes(n.tagName)));
});

test('counter view is a readonly separated raw single and combined display with full tail',()=>{
 const e=setup(),box=new Element(),rows=Array.from({length:251},(_,i)=>({
  t:i/10,rule_id:'reader',effect_id:'value',character:'a',source_value:i===0?0:1,
  single_value:i===0?0:1,layer_count:2,combined_value:i===0?0:2,receipt_id:'view-receipt-'+i,
  reading:{counter_id:'count',view_id:'read',raw_atoms:i===0?0:19,unit_scale:10,counter_cursor:{ledger_sequence:i}}}));
 const result={resource_counter_views:{trace:rows}},before=JSON.stringify(result);
 e.MC.renderResourceCounterViews(box,result,e.PL.state.plan.manual_template);
 assert.ok(box.textContent.includes('0 / 10'));
 assert.ok(box.textContent.includes('0 / 2 / 0'));
 assert.ok(box.textContent.includes('1 / 2 / 2'));
 assert.ok(box.textContent.includes('不额外增加伤害'));
 assert.ok(box.textContent.includes('view-receipt-250'));
 assert.ok(box.textContent.includes('已显示前 250 条，共 251 条'));
 assert.equal(JSON.stringify(result),before);
 assert.ok(!all(box).some(n=>['input','select','textarea','button'].includes(n.tagName)));
});
