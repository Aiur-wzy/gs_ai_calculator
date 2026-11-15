const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');
const helpers={module:{exports:{}},require,__dirname,console};
const harness=fs.readFileSync(path.join(__dirname,'gui_hit_parameters_review.test.cjs'),'utf8').split('\ntest(')[0];
vm.runInNewContext(harness+'\nmodule.exports={setup,all,press};',helpers);
const {setup,all,press}=helpers.module.exports;
const clone=value=>JSON.parse(JSON.stringify(value));
function prepared(){const e=setup();e.PL.state.plan.mechanics_model.attribute_model={contract:'attribute-ledger-v1',initial_basis:[],effect_classifications:[]};e.RT.syncRevision();
 e.MC.adoptAttributes();const note=all(e.dialog()).find(n=>n.attributes['aria-label']==='核对依据');note.value='独立核对';note.emit('change');note.emit('input');return e;}
function response(p,n){const plan=clone(p);plan.attribute_review={mode:'manual_context',note:n,context_fingerprint:'approved'};
 return {code:0,data:{plan,state:'pending',attribute_source_adoption:{contract:'attribute-source-adoption-v1'}}};}
for(const field of ['sourceEpoch','fileSha'])test('independent late review refuses changed '+field,async()=>{
 const e=prepared(),before=e.PL.currentInputJson();let finish;
 e.world.pywebview.api.prepare_attribute_review=(p,n)=>new Promise(r=>finish=()=>r(response(p,n)));
 const pending=press(e.dialog(),'核对完毕，采用当前声明');e.PL.state[field]=field==='sourceEpoch'?e.PL.state[field]+1:'different-file-sha';finish();await pending;
 assert.equal(e.PL.currentInputJson(),before);assert.equal(e.PL.state.plan.attribute_review,undefined);
});
test('independent double submit calls backend once and pending adoption remains a draft',async()=>{
 const e=prepared();let finish,calls=0;
 e.world.pywebview.api.prepare_attribute_review=(p,n)=>{calls++;return new Promise(r=>finish=()=>r(response(p,n)));};
 const first=press(e.dialog(),'核对完毕，采用当前声明');await press(e.dialog(),'核对完毕，采用当前声明');assert.equal(calls,1);finish();await first;
 assert.equal(e.PL.state.plan.attribute_review.note,'独立核对');assert.equal(e.PL.state.report.state,'pending');assert.ok(e.PL.dirty());
 assert.equal(e.PL.canCalculate(),false);
});
test('independent Unicode rename updates basis/provider references and invalidates scope only',()=>{
 const e=setup(),p=e.PL.state.plan,other='旁观者',replacement='新角色😀';
 const component=provider=>({provider,source_fingerprint:'old-source-fp',part_id:'binding:x',effect_id:'effect'});
 p.mechanics_model.attribute_model={contract:'attribute-ledger-v1',initial_basis:[
  {actor:'a',nonextra:{mode:'explicit',value:100},included_components:[{component:component('a')}]},
  {actor:other,included_components:[{component:component('a')},{component:component(other)}]}],
  effect_classifications:[{component:component('a'),effect_ref:{kind:'rule',rule_id:'rule-a',effect_id:'value'},nonextra_inclusion:'unknown',status:'pending'},
   {component:component(other),effect_ref:{kind:'rule',rule_id:'other',effect_id:'value'},nonextra_inclusion:'excluded',status:'assumed'}]};
 p.attribute_review={mode:'manual_context',note:'old-review',context_fingerprint:'old-context'};
 const old=clone(p.mechanics_model.attribute_model);e.PL.renameCharacter('a',replacement);
 const current=p.mechanics_model.attribute_model;
 assert.equal(current.initial_basis[0].actor,replacement);assert.equal(current.initial_basis[1].actor,other);
 assert.equal(current.initial_basis[1].included_components[0].component.provider,replacement);
 assert.equal(current.effect_classifications[0].component.provider,replacement);
 assert.deepEqual(clone(current.effect_classifications[1]),old.effect_classifications[1]);
 assert.equal(current.effect_classifications[0].component.source_fingerprint,'old-source-fp');
 assert.equal(current.effect_classifications[0].status,'pending');assert.equal(p.attribute_review.context_fingerprint,null);
 assert.equal(p.attribute_review.note,'old-review');
});
test('independent state view names actual resource completion outcome',()=>{
 const e=setup(),box=e.world.document.createElement('div');
 const result={state_runtime:{execution_trace:[{type:'state_dispatch_completed',outcome:'resource_operation',
  time_us:1000000,lifecycle_id:'field',payload:{kind:'resource_operation',ref_id:'grant'}}]}};
 const before=JSON.stringify(result);e.MC.renderStateRuntime(box,result);
 const row=all(box).find(n=>n.tagName==='table').children[1];
 assert.equal(row.children[3].textContent,'资源操作已处理');assert.equal(JSON.stringify(result),before);
});
test('independent state view marks actual failed dispatch type with absent outcome',()=>{
 const e=setup(),box=e.world.document.createElement('div');
 const result={state_runtime:{status:'failed',execution_trace:[{type:'state_dispatch_failed',run_id:'signed-dispatch',
  payload:{kind:'dispatch_rule',ref_id:'buff'},message:'工程来源已改变'}]}};
 const before=JSON.stringify(result);e.MC.renderStateRuntime(box,result);
 const row=all(box).find(n=>n.tagName==='table').children[1];
 assert.ok(row.children[3].textContent.startsWith('失败'));assert.ok(row.children[3].textContent.includes('工程来源已改变'));assert.equal(JSON.stringify(result),before);
});
