const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');
const helpers={module:{exports:{}},require,__dirname,console};
const harness=fs.readFileSync(path.join(__dirname,'gui_hit_parameters_review.test.cjs'),'utf8').split('\ntest(')[0];
vm.runInNewContext(harness+'\nmodule.exports={setup,Element,all};',helpers);
const {setup,Element,all}=helpers.module.exports;
function sample(stat='atk') {return {receipt_id:'receipt-total',query:{actor:'a',stat,basis:'total',frame:{t_us:1000000,phase:'pre',origin:0}},baseline:100,value:115,status:'known',contributions:[{
 id:'ratio',stat:'atk',component:{provider:'b',part_id:'actual-source',effect_id:'actual-effect'},application:{kind:'recipient_base_fraction'},raw_evaluated_value:.3,application_base_value:50,evaluated_value:15,input_receipt_id:'input-real',application_base_receipt_id:'base-real'}]};}
function render(receipts){const e=setup(),box=new Element(),result={attribute_ledger:{contract:'attribute-ledger-v3',receipts}},before=JSON.stringify(result);
 e.MC.renderAttributeLedger(box,result,e.PL.state.plan.manual_template);
 assert.equal(JSON.stringify(result),before);assert.ok(!all(box).some(n=>['input','select','textarea','button'].includes(n.tagName)));return box;}
test('v3 identifies each stat and preserves sampled ratio, application BASE and flat value separately',()=>{
 const em=sample('em');em.contributions=[];const box=render([sample(),em]);
 for(const text of ['攻击力','元素精通','总量','实际比例（1为100%）','使用的基础攻击力','乘后平值','actual-source','input-real','base-real'])assert.ok(box.textContent.includes(text),text);
 const tables=all(box).filter(n=>n.tagName==='table');assert.equal(tables.length,2);
 assert.equal(tables[1].children[1].children.slice(2,5).map(n=>n.textContent).join(','),'0.3,50,15');
});
test('v3 unresolved percentage evidence remains unknown rather than becoming zero',()=>{
 const row=sample();row.value=null;row.status='pending';for(const k of ['raw_evaluated_value','application_base_value','evaluated_value'])row.contributions[0][k]=null;
 const box=render([row]),tables=all(box).filter(n=>n.tagName==='table');
 assert.equal(tables[0].children[1].children[5].textContent,'—');
 assert.equal(tables[1].children[1].children.slice(2,5).map(n=>n.textContent).join(','),'—,—,—');
});
test('v3 limits tables independently while keeping the full immutable ledger available',()=>{
 const rows=Array.from({length:251},(_,i)=>({...sample(),receipt_id:'receipt-'+i}));const box=render(rows),tables=all(box).filter(n=>n.tagName==='table');
 assert.equal(tables[0].children.length,251);assert.equal(tables[1].children.length,101);
 assert.ok(box.textContent.includes('receipt-250'));assert.ok(box.textContent.includes('前250条取样回执中的至多100项'));
});
test('source result hook runs once from the complete mechanics trace and is optional',()=>{
 const e=setup();let calls=0;const result={};const template=e.PL.state.plan.manual_template;
 e.world.REM={renderResult:(section,received,actualTemplate)=>{assert.equal(received,result);assert.equal(actualTemplate,template);calls++;}};
 e.MC.renderTrace(result,template);assert.equal(calls,1);delete e.world.REM;e.MC.renderTrace(result,template);
});
