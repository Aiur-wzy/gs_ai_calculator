const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');
const helpers={module:{exports:{}},require,__dirname,console};
const harness=fs.readFileSync(path.join(__dirname,'gui_hit_parameters_review.test.cjs'),'utf8').split('\ntest(')[0];
vm.runInNewContext(harness+'\nmodule.exports={setup,Element,all};',helpers);
const {setup,Element,all}=helpers.module.exports;

test('threshold observations actual activation and source identity remain distinct and read only',()=>{
 const e=setup(),box=new Element(),base={before:0,after:5,threshold_atoms:5,comparison:'enter_gte',phase:'pre'},result={
  resource_threshold_observers:{trace:[{...base,type:'observer_evaluation',t_us:0,event_id:'hit',run_id:'0:hit',crossed:true,observer_id:'watch',source_id:'source:watch'}]},
  resource_threshold_triggers:{source_adoptions:[{id:'bound',name_zh:'阈值增益',source_id:'source:buff',owner:'a',status:'ready'}],trace:[
   {...base,type:'threshold_dispatch_completed',binding_id:'bound',time_us:0,source_event_id:'hit',source_run_id:'0:hit',outcome:'activated'},
   {...base,type:'threshold_dispatch_completed',outcome:'not_activated'}]}};
 const before=JSON.stringify(result);e.MC.renderResourceThresholds(box,result,e.PL.state.plan.manual_template);
 for(const text of ['已跨越阈值','收益已激活','未激活（条件不满足或冷却中）','0 / 事件前','hit / 0:hit','阈值增益（bound） / source:buff','向上达到 5 / 0 → 5'])assert.ok(box.textContent.includes(text),text);
 assert.equal(JSON.stringify(result),before);
 assert.ok(!all(box).some(n=>['input','select','textarea','button'].includes(n.tagName)));
});

test('issued alone is not an actual row and unknown trace is not activated; tail retained',()=>{
 const e=setup(),box=new Element(),result={resource_threshold_triggers:{authority:{issued:[{outcome:'activated'}]},trace:Array.from({length:251},(_,i)=>({type:'future',source_run_id:'tail-'+i}))}};
 e.MC.renderResourceThresholds(box,result,e.PL.state.plan.manual_template);
 assert.ok(box.textContent.includes('执行过程未知'));
 assert.ok(box.textContent.includes('— / 阶段未知'));
 assert.ok(box.textContent.includes('tail-250'));
 assert.ok(box.textContent.includes('已显示前 250 条，共 251 条'));
 const tables=all(box).filter(n=>n.tagName==='table');
 assert.equal(tables.length,1);assert.equal(tables[0].children.length,251);
});

test('absent threshold record has no section',()=>{
 const e=setup(),box=new Element();e.MC.renderResourceThresholds(box,{},e.PL.state.plan.manual_template);assert.equal(box.children.length,0);
});

test('counter values are not presented as remaining pool balance',()=>{
 const e=setup(),box=new Element(),result={resource_threshold_observers:{trace:[{type:'observer_evaluation',input_kind:'counter_value_key',counter_id:'count',before:8,after:16,crossed:true,threshold_atoms:16,comparison:'enter_gte'}]},
  resource_threshold_triggers:{trace:[{type:'threshold_dispatch_completed',counter_observation:{counter_id:'count'},outcome:'activated',before:8,after:16,threshold_atoms:16,comparison:'enter_gte'}]}};
 e.MC.renderResourceThresholds(box,result,e.PL.state.plan.manual_template);
 const tables=all(box).filter(n=>n.tagName==='table');
 assert.equal(tables.length,2);
 for(const table of tables)assert.ok(table.textContent.includes('消费累计 向上达到 16 / 8 → 16'));
});
