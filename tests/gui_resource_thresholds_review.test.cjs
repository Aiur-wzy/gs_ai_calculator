const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');
const helpers={module:{exports:{}},require,__dirname,console};
const harness=fs.readFileSync(path.join(__dirname,'gui_hit_parameters_review.test.cjs'),'utf8').split('\ntest(')[0];
vm.runInNewContext(harness+'\nmodule.exports={setup,Element,all};',helpers);
const {setup,Element,all}=helpers.module.exports;

test('ambiguous source joins remain unknown and failed/claimed rows are never activated',()=>{
 const e=setup(),box=new Element(),result={resource_threshold_triggers:{source_adoptions:[
  {id:'bound',status:'ready',source_id:'wrong-one',name_zh:'猜测名称'},
  {id:'bound',status:'ready',source_id:'wrong-two'}],trace:[
  {binding_id:'bound',type:'threshold_dispatch_failed',outcome:'pending',time_us:200000,source_event_id:'h0'},
  {binding_id:'bound',type:'claimed',outcome:'activated',time_us:200000,source_event_id:'h0'}]}};
 const original=JSON.stringify(result);e.MC.renderResourceThresholds(box,result,e.PL.state.plan.manual_template);
 assert.ok(box.textContent.includes('派发失败：待核实'));
 assert.ok(box.textContent.includes('执行过程未知'));
 assert.ok(!box.textContent.includes('猜测名称')||!all(box).filter(n=>n.tagName==='table')[0].textContent.includes('猜测名称'));
 const table=all(box).filter(n=>n.tagName==='table')[0];
 assert.ok(!table.textContent.includes('收益已激活'));
 assert.ok(!table.textContent.includes('wrong-one'));
 assert.equal(JSON.stringify(result),original);
});

test('unsafe timestamps show unknown rather than rounded time and raw zero fields stay visible',()=>{
 const e=setup(),box=new Element(),result={resource_threshold_observers:{trace:[
  {t_us:0,observer_id:'o',before:0,after:0,threshold_atoms:0,crossed:false,phase:'post'},
  {t_us:Number.MAX_SAFE_INTEGER+1,observer_id:'o',crossed:true,phase:'pre'}]}};
 e.MC.renderResourceThresholds(box,result,e.PL.state.plan.manual_template);
 const table=all(box).filter(n=>n.tagName==='table')[0];
 assert.ok(table.textContent.includes('0 / 事件后'));
 assert.ok(table.textContent.includes('— / 事件前'));
 assert.ok(table.textContent.includes('0 / 0 → 0'));
 assert.ok(!all(box).some(n=>['input','select','textarea','button'].includes(n.tagName)));
});
