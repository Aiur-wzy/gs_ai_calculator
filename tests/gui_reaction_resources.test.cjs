const GUI_TEST_RUNTIME = require('./helpers/gui_runtime.cjs');
const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');
const root=path.resolve(__dirname,'..'),helpers={module:{exports:{}},require,__dirname:path.join(root,'tests'),console};
vm.runInNewContext(fs.readFileSync(path.join(root,'tests/gui_hit_parameters_review.test.cjs'),'utf8').split('\ntest(')[0]+'\nmodule.exports={setup,Element,all};',helpers);
const {setup:base,Element,all}=helpers.module.exports;
function setup(){const e=base();for(const p of [path.join(root,'src/genshin_app/gui/web/counter-frames.js'),path.join(root,'src/genshin_app/gui/web/reaction-resources.js')])GUI_TEST_RUNTIME.loadWebFile(e.world, p);return e;}
function sample(name='ordinary-swirl-income'){return JSON.parse(fs.readFileSync(path.join(__dirname,'fixtures/resource_reaction_reporting.json'),'utf8')).cases[name];}

test('actual resource income readonly, actor/provider distinct and no added controls',()=>{
 const e=setup(),result=sample(),box=new Element(),before=JSON.stringify(result);
 assert.equal(e.world.RR.render(box,result,{}),true);assert.match(box.textContent,/反应资源联动（3 次/);assert.match(box.textContent,/请求 3 \/ 接受 3 \/ 浪费 0/);
 assert.ok(!all(box).some(n=>['input','button','select','textarea'].includes(n.tagName)));assert.equal(JSON.stringify(result),before);
});
test('no match, zero occurrence and excluded source distinguish occurrence from income',()=>{
 const e=setup();for(const name of ['actual-selector-no-match','complete-zero-reaction']){const box=new Element();e.world.RR.render(box,sample(name),{});assert.doesNotMatch(box.textContent,/接受 3/);}
 const box=new Element();assert.equal(e.world.RR.render(box,{resource_reaction_source_adoption:{status:'excluded'}},{}),true);assert.match(box.textContent,/已排除.*无执行记录/);
});
test('different parent or duplicate span suppresses claimed amounts',()=>{
 const e=setup(),r=sample();r.shared_pools.trace[1].run_id='other';let rows=e.world.RR.rows(r);assert.equal(rows[0].issue,'资源流水与反应操作不一致');assert.equal(rows[0].receipts.length,0);
 const r2=sample();r2.resource_reactions.operations.push(structuredClone(r2.resource_reactions.operations[0]));rows=e.world.RR.rows(r2);assert.equal(rows[0].issue,'同一资源流水被重复引用');assert.equal(rows.at(-1).receipts.length,0);
});
test('zero time preserved and pending cannot appear as grant',()=>{
 const e=setup(),r=sample();r.resource_reactions.occurrences[0].time_us=r.shared_pools.trace[1].t_us=0;const box=new Element();e.world.RR.render(box,r,{});assert.match(box.textContent,/0 \/ h0/);
 r.resource_reactions.operations[0].state='planned';assert.equal(e.world.RR.rows(r)[0].receipts.length,0);
});
test('old results unchanged and 250 row cap keeps raw evidence',()=>{
 const e=setup(),box=new Element();assert.equal(e.world.RR.render(box,{hits:[]},{}),false);assert.equal(box.children.length,0);
 const r=sample('actual-selector-no-match'),op=r.resource_reactions.operations[0];r.resource_reactions.operations=Array.from({length:251},(_,i)=>({...op,id:i}));e.world.RR.render(box,r,{});assert.match(box.textContent,/已显示前 250 条，共 251 条/);assert.match(box.textContent,/完整只读反应/);
});
test('actual incomplete execution takes precedence over older audit window label',()=>{
 const e=setup();
 for(const path of ['mechanics','rotation']){
  const r=sample(),box=new Element();
  if(path==='mechanics')r.mechanics.complete_first_cycle=false;
  else r.rotation_check={damage_interpretation:{kind:'executed_prefix'}};
  e.world.RR.render(box,r,{});assert.match(box.textContent,/核验范围：已执行前缀/);
  assert.doesNotMatch(box.textContent,/核验范围：本次完整声明窗口/);
 }
});
