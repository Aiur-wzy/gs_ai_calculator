const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');
const helpers={module:{exports:{}},require,__dirname,console};
const harness=fs.readFileSync(path.join(__dirname,'gui_hit_parameters_review.test.cjs'),'utf8').split('\ntest(')[0];
vm.runInNewContext(harness+'\nmodule.exports={setup,all,press};',helpers);
const {setup,all,press}=helpers.module.exports;
function fixture(){const e=setup();e.PL.state.plan.mechanics_model.attribute_model={contract:'attribute-ledger-v1',initial_basis:[],effect_classifications:[]};e.RT.syncRevision();
  e.MC.adoptAttributes();const area=all(e.dialog()).find(n=>n.attributes['aria-label']==='核对依据');
  const setNote=value=>{area.value=value;area.emit('change');area.emit('input');};setNote('核对初始面板与来源');
  e.world.pywebview.api.prepare_attribute_review=async(plan,note)=>response(plan,note);return {...e,setNote};}
function response(plan,note){const p=JSON.parse(JSON.stringify(plan));p.attribute_review={mode:'manual_context',context_fingerprint:'current',note};
 return {code:0,data:{state:'ready',plan:p,attribute_source_adoption:{contract:'attribute-source-adoption-v1'}}};}
test('attribute review adopts explicit notes and keeps original file saved state',async()=>{
 const e=fixture(),saved=e.PL.state.savedJson;let called=0;e.world.pywebview.api.prepare_attribute_review=async(p,n)=>{called++;return response(p,n);};await press(e.dialog(),'核对完毕，采用当前声明');
 assert.equal(called,1,e.dialog().textContent);
 assert.ok(e.PL.state.plan.attribute_review,e.dialog().textContent);assert.equal(e.PL.state.plan.attribute_review.note,'核对初始面板与来源');assert.equal(e.PL.state.savedJson,saved);
 assert.equal(e.RT.state.path,'review.json');assert.ok(e.PL.dirty());
});
for(const change of ['candidate','path','close','note'])test('late attribute review is not applied after '+change,async()=>{
 const e=fixture();let finish;e.world.pywebview.api.prepare_attribute_review=(p,n)=>new Promise(r=>finish=()=>r(response(p,n)));
 const pending=press(e.dialog(),'核对完毕，采用当前声明');
 if(change==='candidate')e.PL.state.plan.manual_template.loop_duration+=1;
 if(change==='path')e.RT.state.path='new.json';
 if(change==='close')press(e.dialog(),'← 返回机制列表');
 if(change==='note')e.setNote('修改后的依据');finish();await pending;
 assert.equal(e.PL.state.plan.attribute_review,undefined);
});
test('blank note and backend failure keep candidate untouched',async()=>{
 const e=fixture();let calls=0;e.world.pywebview.api.prepare_attribute_review=async()=>{calls++;return {code:1,message:'待核实'};};
 const before=e.PL.currentInputJson();e.setNote(' ');await press(e.dialog(),'核对完毕，采用当前声明');assert.equal(calls,0);
 e.setNote('明确核对');await press(e.dialog(),'核对完毕，采用当前声明');assert.equal(calls,1);assert.equal(e.PL.currentInputJson(),before);
});
