const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');
const helpers={module:{exports:{}},require,__dirname,console};
const harness=fs.readFileSync(path.join(__dirname,'gui_hit_parameters_review.test.cjs'),'utf8').split('\ntest(')[0];
vm.runInNewContext(harness+'\nmodule.exports={setup,Element,all};',helpers);
const {setup,Element,all}=helpers.module.exports;

test('callback plans prepared reads and actual commits remain distinct and read only',()=>{
 const e=setup(),box=new Element(),result={attribute_callbacks:{source_authority:'caller_assumed_order_not_game_source_approval',trace:[
  {type:'callback_plan',time_us:0,origin:0,parent_event_id:'hit-0',parent_run_id:'0:hit-0'},
  {type:'callback_batch_prepared',time_us:0,origin:0,parent_event_id:'hit-0',parent_run_id:'0:hit-0',batch:0,frame_id:'frame-1'},
  {type:'callback_committed',time_us:0,origin:0,parent_event_id:'hit-0',parent_run_id:'0:hit-0',batch:0,frame_id:'frame-1',rule_id:'rule',source_id:'binding:part',owner:'a'}]}};
 const before=JSON.stringify(result);e.MC.renderAttributeCallbacks(box,result,e.PL.state.plan.manual_template);
 assert.ok(box.textContent.includes('计划已生成'));
 assert.ok(box.textContent.includes('批次取样已准备'));
 assert.ok(box.textContent.includes('回调已提交'));
 assert.ok(box.textContent.includes('0 / 完整输入（0）'));
 assert.ok(box.textContent.includes('0 / frame-1'));
 assert.ok(box.textContent.includes('rule / binding:part'));
 assert.ok(box.textContent.includes('不是数据库已认证的游戏事实'));
 assert.equal(JSON.stringify(result),before);
 assert.ok(!all(box).some(n=>['input','select','textarea','button'].includes(n.tagName)));
});

test('unknown time window and stage are not called zero or committed; full tail retained',()=>{
 const e=setup(),box=new Element(),result={attribute_callbacks:{trace:Array.from({length:251},(_,i)=>({type:'future-kind',parent_run_id:'tail-'+i}))}};
 e.MC.renderAttributeCallbacks(box,result,e.PL.state.plan.manual_template);
 assert.ok(box.textContent.includes('— / 窗口未知'));
 assert.ok(box.textContent.includes('过程未知'));
 assert.ok(box.textContent.includes('tail-250'));
 assert.ok(box.textContent.includes('已显示前 250 条，共 251 条'));
});

test('absent callback receipt adds no section',()=>{
 const e=setup(),box=new Element();e.MC.renderAttributeCallbacks(box,{},e.PL.state.plan.manual_template);
 assert.equal(box.children.length,0);
});
