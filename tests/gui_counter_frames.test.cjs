const GUI_TEST_RUNTIME = require('./helpers/gui_runtime.cjs');
const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');
const root=path.resolve(__dirname,'..'),helpers={module:{exports:{}},require,__dirname:path.join(root,'tests'),console};
vm.runInNewContext(fs.readFileSync(path.join(root,'tests/gui_hit_parameters_review.test.cjs'),'utf8').split('\ntest(')[0]+'\nmodule.exports={setup,Element,all};',helpers);
const {setup:base,Element,all}=helpers.module.exports;
function setup(){const e=base();GUI_TEST_RUNTIME.loadWebFile(e.world, path.join(root,'src/genshin_app/gui/web/counter-frames.js'));return e;}
function result(name){return {resource_counter_views:JSON.parse(fs.readFileSync(path.join(__dirname,'fixtures/counter_self_frame_reporting.json'),'utf8'))[name]};}
test('actual frozen layers and current gate remain readonly and separately labelled',()=>{
 const e=setup(),box=new Element(),data=result('frozen_two_layers'),before=JSON.stringify(data);
 assert.equal(e.world.CF.render(box,data,e.PL.state.plan.manual_template),true);
 assert.match(box.textContent,/输出快照/);assert.match(box.textContent,/捕获时冻结层数 2 \/ 1.6/);assert.match(box.textContent,/不需要次数门槛/);assert.match(box.textContent,/累计输出快照/);
 assert.equal(JSON.stringify(data),before);assert.ok(!all(box).some(n=>['input','button','select','textarea'].includes(n.tagName)));
});
test('current self without reference has no fabricated frame',()=>{
 const e=setup(),box=new Element(),data=result('current_no_reference');e.world.CF.render(box,data,e.PL.state.plan.manual_template);
 assert.match(box.textContent,/当前累计（未指定输出快照）/);assert.match(box.textContent,/累计输出快照（0 次/);assert.match(box.textContent,/当前层数 1 \/ 1.9/);
});
test('mixed provider/current has no invented self-frame gate requirement',()=>{
 const e=setup(),data=result('historical'),row=structuredClone(data.resource_counter_views.trace[0]);
 for(const key of ['frame_selection','frame_id','capture_context','gate_qualification'])delete row[key];
 row.domain='provider';row.sample='current';data.resource_counter_views.trace.push(row);
 const box=new Element();e.world.CF.render(box,data,e.PL.state.plan.manual_template);
 assert.match(box.textContent,/提供者当前取样（资格见原资源读数）/);
 for(const extra of [{gate_qualification:null},{gate_qualification:{}},{gate_qualification:false},{gate_qualification:[]},
                    {frame_selection:null},{frame_selection:false},{domain:'self'},{sample:'self_frame'}]){
  assert.equal(e.world.CF.gate({...row,...extra}),'门槛回执不完整');
 }
});
test('zero time and missing or duplicate frame are explicit',()=>{
 const e=setup(),data=result('historical').resource_counter_views,row=data.trace[0],frame=data.historical_capture.capture_trace[0];row.capture_context.time_us=frame.context.time_us=0;
 assert.match(e.world.CF.capture(row,e.world.CF.index([frame])),/^0秒/);
 assert.equal(e.world.CF.capture(row,new Map()),'捕获回执缺失');assert.equal(e.world.CF.capture(row,e.world.CF.index([frame,frame])),'捕获回执不唯一');
});
test('old report stays outside new rendering, long table bounds retain complete raw tail',()=>{
 const e=setup(),box=new Element();assert.equal(e.world.CF.render(box,{resource_counter_views:{contract:'resource-counter-views-current-v1'}},{}),false);assert.equal(box.children.length,0);
 const data=result('historical'),row=data.resource_counter_views.trace[0];data.resource_counter_views.trace=Array.from({length:251},(_,i)=>({...row,receipt_id:'receipt-'+i}));
 e.world.CF.render(box,data,e.PL.state.plan.manual_template);assert.match(box.textContent,/已显示前 250 条，共 251 条/);assert.match(box.textContent,/receipt-250/);
 const tables=all(box).filter(n=>n.tagName==='table');assert.equal(tables[0].children.length,251);
});
