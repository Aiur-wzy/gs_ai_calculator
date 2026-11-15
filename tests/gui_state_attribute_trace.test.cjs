const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');
const helpers={module:{exports:{}},require,__dirname,console};
const harness=fs.readFileSync(path.join(__dirname,'gui_hit_parameters_review.test.cjs'),'utf8').split('\ntest(')[0];
vm.runInNewContext(harness+'\nmodule.exports={setup,Element,all};',helpers);
const {setup,Element,all}=helpers.module.exports;
const dispatch={time_us:1000000,lifecycle_id:'field',timer_id:'pulse',ordinal:2,outcome:'pending',payload:{kind:'dispatch_rule',ref_id:'gain'},source_event_id:'cast'};
const sample={query:{actor:'a',basis:'nonextra',frame:{t_us:1000000,phase:'pre'}},baseline:60,value:null,status:'pending'};
function render(kind,rows){const e=setup(),box=new Element(),result=kind==='state'?{
 state_runtime:{execution_trace:rows,issued_trace:[{type:'lifecycle_end',time_us:2000000}]},
 mechanism_execution:{model:{state_runtime:{lifecycles:[{id:'field',name_zh:'受审领域'}]}}}}:{attribute_ledger:{receipts:rows}};
 const before=JSON.stringify(result);e.MC[kind==='state'?'renderStateRuntime':'renderAttributeLedger'](box,result,e.PL.state.plan.manual_template);
 assert.equal(JSON.stringify(result),before);assert.ok(!all(box).some(n=>['input','select','textarea','button'].includes(n.tagName)));return box;}
test('state trace describes actual dispatch separately from domain lifetime',()=>{
 const box=render('state',[dispatch,{...dispatch,time_us:2000000,outcome:'activated'}]);
 for(const s of ['受审领域','待核实','收益已激活','已有收益的残留时间','cast'])assert.ok(box.textContent.includes(s),s);
 assert.equal(all(box).find(n=>n.tagName==='table').children.length,3);
});
test('attribute unknown value is shown as unknown, not numeric zero',()=>{
 const box=render('attribute',[sample]);for(const s of ['非额外精通','60','待核实','—'])assert.ok(box.textContent.includes(s),s);
 const row=all(box).find(n=>n.tagName==='table').children[1];assert.equal(row.children[4].textContent,'—');
});
for(const kind of ['state','attribute'])test(kind+' limits rendered rows but retains full immutable receipt',()=>{
 const rows=Array.from({length:251},(_,i)=>kind==='state'?{...dispatch,source_event_id:'event-'+i}:{...sample,receipt_id:'receipt-'+i});
 const box=render(kind,rows);assert.equal(all(box).find(n=>n.tagName==='table').children.length,251);
 assert.ok(box.textContent.includes('已显示前 250 条，共 251 条'));assert.ok(box.textContent.includes(kind==='state'?'event-250':'receipt-250'));
});
test('legacy results do not get empty new sections',()=>{
 const e=setup(),box=new Element();e.MC.renderStateRuntime(box,{});e.MC.renderAttributeLedger(box,{},{});assert.equal(box.children.length,0);
});
test('attribute phases use sampling terms rather than a resource balance label',()=>{
 const box=render('attribute',['initial','reaction_damage_pre','capture'].map(phase=>({...sample,query:{...sample.query,frame:{t_us:0,phase}}})));
 for(const text of ['初始状态','反应出伤前','面板取样'])assert.ok(box.textContent.includes(text),text);
 assert.ok(!box.textContent.includes('初始余额'));
});

test('attribute projection identifies input and extra check windows without claiming reported damage',()=>{
 const box=render('attribute',[0,2,undefined].map(origin=>({...sample,query:{...sample.query,frame:{origin,t_us:10200000,phase:'damage_projection'}}})));
 for(const text of ['出伤属性投影','完整输入（0）','附加检查（2）','窗口未知','取样本身不代表已计入伤害'])assert.ok(box.textContent.includes(text),text);
 const cells=all(box).find(n=>n.tagName==='table').children.slice(1).map(row=>row.children[0].textContent);
 assert.ok(cells[1].includes('10.2 / 附加检查（2）'));assert.ok(!cells[0].includes('已报告'));
});

test('resource counters distinguish requested, debited and counted atoms and remain readonly',()=>{
 const e=setup(),box=new Element(),result={resource_counters:{counter_units:{count:10},trace:[
  {type:'counter_reset',counter_id:'count',before:{total:0},after:{total:0},phase:'created',time_us:0},
  {type:'counter_accumulation',counter_id:'count',requested:8,resource_accepted:3,accepted:0,before:19,after:19,reason:'outside_window',time_us:500000}]}};
 const before=JSON.stringify(result);e.MC.renderResourceCounters(box,result);
 for(const text of ['重置累计','8 / 3 / 0','19 → 19','累计窗口外','尚未配置累计转伤害','10'])assert.ok(box.textContent.includes(text),text);
 assert.equal(JSON.stringify(result),before);assert.ok(!all(box).some(n=>['input','button','select','textarea'].includes(n.tagName)));
 const empty=new Element();e.MC.renderResourceCounters(empty,{});assert.equal(empty.children.length,0);
});

test('counter reads distinguish raw units, transformed value and final layer composition',()=>{
 const e=setup(),box=new Element(),result={resource_counter_views:{trace:[{t:.3,rule_id:'boost',effect_id:'v',character:'a',
  source_value:1.6,single_value:.16,value:.16,layer_count:3,combined_value:.48,
  reading:{counter_id:'count',view_id:'read',raw_atoms:19,unit_scale:10,cap_applied:true,transform:{kind:'scaled',max_atoms:16}}}]}};
 const before=JSON.stringify(result);e.MC.renderResourceCounterViews(box,result,e.PL.state.plan.manual_template);
 for(const text of ['19 / 10','1.6','0.16 / 3 / 0.48','原始原子数','不额外增加伤害','max_atoms'])assert.ok(box.textContent.includes(text),text);
 assert.equal(JSON.stringify(result),before);assert.ok(!all(box).some(n=>['input','select','button','textarea'].includes(n.tagName)));
 const empty=new Element();e.MC.renderResourceCounterViews(empty,{},{});assert.equal(empty.children.length,0);
});
