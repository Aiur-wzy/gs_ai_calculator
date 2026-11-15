const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');
const helpers={module:{exports:{}},require,__dirname,console};
const harness=fs.readFileSync(path.join(__dirname,'gui_hit_parameters_review.test.cjs'),'utf8').split('\ntest(')[0];
vm.runInNewContext(harness+'\nmodule.exports={setup,Element,all};',helpers);
const {setup,Element,all}=helpers.module.exports;

function render(rows,version=1){const env=setup(),box=new Element(),result={shared_pools:{schema_version:version,state:{balances:{p:{team:0}}},trace:rows},
  mechanism_execution:{model:{shared_pools:{pools:[{id:'p',name_zh:'受审共享资源'}]}}}};
  const before=JSON.stringify(result);env.MC.renderSharedPools(box,result,env.PL.state.plan.manual_template);
  assert.equal(JSON.stringify(result),before);return box;}
const receipt={type:'consume',t:2,cycle:0,pool_id:'p',key:'team',requested:1,accepted:1,before:1,after:0,reason:'consumed',source_id:'reviewed:part',event_id:'hit'};
test('shared resource UI displays actual consumption in Chinese without editable controls',()=>{
  const box=render([receipt,{...receipt,t:3,accepted:0,before:0,reason:'insufficient'}]);
  for(const text of ['共享资源流水','受审共享资源','已消费','余额不足','1 → 0','附加检查轮不增加伤害'])assert.ok(box.textContent.includes(text),text);
  assert.ok(!all(box).some(e=>['input','select','textarea','button'].includes(e.tagName)));
  const table=all(box).find(e=>e.tagName==='table');assert.equal(table.children.length,3);
});
test('shared resource UI reads pool names from the current calculation snapshot',()=>{
  const env=setup(),box=new Element(),result={shared_pools:{schema_version:2,state:{balances:{p:{team:0}}},trace:[receipt]}};
  env.world.RS.state.snapshot={executionModel:{shared_pools:{pools:[{id:'p',name_zh:'当前计算共享库存'}]}}};
  env.MC.renderSharedPools(box,result,env.PL.state.plan.manual_template);
  assert.match(box.textContent,/当前计算共享库存/);
});
test('resource trace limits rows and retains the full receipt in details',()=>{
  const rows=Array.from({length:251},(_,i)=>({...receipt,event_id:'h'+i})),box=render(rows);
  assert.equal(all(box).find(e=>e.tagName==='table').children.length,251);
  assert.ok(box.textContent.includes('已显示前 250 条，共 251 条'));assert.ok(box.textContent.includes('h250'));
});
test('old result without a shared pool has no extra resource section',()=>{
  const env=setup(),box=new Element();env.MC.renderSharedPools(box,{hits:[]},{});assert.equal(box.children.length,0);
});
test('scaled spend displays integer atoms, conversion scale and unfulfilled separately',()=>{
  const box=render([{...receipt,type:'operation',operation:'spend',unit_scale:10,quantity_unit:'atoms',requested:8,accepted:3,before:3,after:0,unfulfilled:5,reason:'spent'}],2);
  for(const text of ['数量均为整数原子','每单位原子数','扣除资源','8 / 3','3 → 0','5 / —','已扣除'])assert.ok(box.textContent.includes(text),text);
  const cells=all(box).find(e=>e.tagName==='table').children[1].children;
  assert.equal(cells[7].textContent,'10');assert.equal(cells[8].textContent,'5 / —');
});
