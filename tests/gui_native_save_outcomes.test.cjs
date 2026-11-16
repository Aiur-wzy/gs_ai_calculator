'use strict';
const {readFileSync}=require('node:fs'),path=require('node:path');
const {JSDOM}=require('../frontend/node_modules/jsdom');
const test=require('node:test'),assert=require('node:assert/strict');
function setup(){
  const dom=new JSDOM('<body><main id="genshin-legacy-editor"><input id="tpl-path"></main></body>',{runScripts:'outside-only',url:'https://local.invalid/'}),w=dom.window;
  w.HTMLDialogElement.prototype.showModal=function(){this.open=true;};
  w.HTMLDialogElement.prototype.close=function(){this.open=false;};
  w.$=selector=>w.document.querySelector(selector);w.setStatus=()=>{};w.DS={prose:value=>value};w.callApi=async()=>({code:0,data:{}});
  let template={meta:{name:'original'},characters:[]};
  w.RT={state:{path:'original.json',revision:0,saveRequest:0,acceptedSaveRequest:0,plansDir:'.'},tpl:()=>template,
    replaceTemplate(value,file){template=value;this.state.path=file;this.state.revision++;},saveTemplate:async()=>false,renderAll(){}};
  w.EQ={closeView(){},lastReport:null};w.RS={invalidate(){}};
  for(const name of ['json-transport.js','gui-foundation-v2.js','ui.js','business-ui.js','plans.js','whiteboard.js','workspace-kernel.js','workspace.js','agent-document-adapter.js','app-file-actions.js'])w.eval(readFileSync(path.join(__dirname,'../src/genshin_app/gui/web',name),'utf8'));
  w.PL.renderStatus=()=>{};w.WB.render=()=>{};w.UI.error=()=>{};
  w.PL.state.plan={plan_id:'native-save-test',manual_template:template};w.PL.state.savedJson=w.PL.currentInputJson();
  w.WS.tab=()=> 'rotation';
  return {w,close:()=>dom.window.close()};
}
for(const [name,response,expected] of [
  ['written unverified',{code:1,details:{publication_state:'written_unverified'}},'unknown'],
  ['unknown paths',{code:1,details:{published_paths:[],unknown_paths:['selected.json']}},'unknown'],
  ['published despite validation reason',{code:1,reason:'invalid_plan',details:{publication_state:'written'}},'unknown'],
  ['known not written',{code:1,details:{publication_state:'not_written'}},'failed'],
])test('actual native save_plan receipt: '+name,async()=>{
 const env=setup(),{w}=env,calls=[];
 try{const before=w.PL.currentInputJson();w.callApi=async method=>{calls.push(method);return method==='dsh_client_file_dialog'?{code:0,data:{path:'selected.json'}}:method==='save_plan'?response:{code:0,data:{}};};
 const actions=w.AppEditorFileActions.create({root:w.document.body,show:async()=>{}});let outcome,error;
 try{outcome=await actions.execute({action:'save_as'},actions.capture());}catch(e){error=e.message;}
 assert.deepEqual(calls,['dsh_client_file_dialog','save_plan']);assert.equal(w.PL.currentInputJson(),before);
 if(expected==='failed'){assert.equal(outcome?.status,'failed');assert.equal(error,undefined);}
 else{assert.ok(error,'uncertain publication must enter Host recovery instead of retryable failed');assert.equal(outcome,undefined);if(name==='written unverified')assert.equal(error,'app_action_written_unverified');}
 }finally{env.close();}
});
for(const picker of ['nonzero','throw','cancel'])test('save picker '+picker+' finishes before any write',async()=>{
 const env=setup(),{w}=env,calls=[];try{w.callApi=async method=>{calls.push(method);if(method!=='dsh_client_file_dialog')throw new Error('must not write');if(picker==='throw')throw new Error('picker unavailable');return picker==='cancel'?{code:0,data:{cancelled:true}}:{code:1,message:'picker unavailable'};};
 const actions=w.AppEditorFileActions.create({root:w.document.body,show:async()=>{}}),outcome=await actions.execute({action:'save_as'},actions.capture());
 assert.equal(outcome.status,picker==='cancel'?'cancelled':'failed');assert.deepEqual(calls,['dsh_client_file_dialog']);
 }finally{env.close();}
});
test('read-only inspection transport failure is a failed open, with current input retained',async()=>{
 const env=setup(),{w}=env,calls=[];try{const before=w.PL.currentInputJson();w.callApi=async method=>{calls.push(method);if(method==='dsh_client_file_dialog')return {code:0,data:{path:'selected.plan.json'}};if(method==='inspect_plan_file')throw new Error('inspection transport failed');throw new Error('must not write');};
 const actions=w.AppEditorFileActions.create({root:w.document.body,show:async()=>{}}),outcome=await actions.execute({action:'open_document'},actions.capture());
 assert.equal(outcome.status,'failed');assert.equal(w.PL.currentInputJson(),before);assert.deepEqual(calls,['dsh_client_file_dialog','inspect_plan_file']);
 }finally{env.close();}
});
test('failed document loader is not reported as user cancellation',async()=>{
 const env=setup(),{w}=env;try{const before=w.PL.currentInputJson();w.callApi=async method=>method==='dsh_client_file_dialog'?{code:0,data:{path:'selected.plan.json'}}:{code:0,data:{kind:'genshin-plan'}};
 w.RT.loadTemplate=async(_path,options)=>{options.onError({code:1,message:'Selected file is inaccessible'});return false;};
 const actions=w.AppEditorFileActions.create({root:w.document.body,show:async()=>{}}),outcome=await actions.execute({action:'open_document'},actions.capture());
 assert.equal(outcome.status,'failed');assert.match(outcome.message,/inaccessible/);assert.equal(w.PL.currentInputJson(),before);
 }finally{env.close();}
});
test('candidate save picker failure settles before publication',async()=>{
 const env=setup(),{w}=env,calls=[];try{w.callApi=async method=>{calls.push(method);throw new Error('native file dialog unavailable');};
 const actions=w.AppEditorFileActions.create({root:w.document.body,show:async()=>{}}),outcome=await actions.execute({action:'export_candidate_input_draft',target:{kind:'candidate_input_draft',workspace_id:'work'},ui_receipt:'receipt',action_id:'action'},actions.capture(),'session');
 assert.equal(outcome.status,'failed');assert.deepEqual(calls,['dsh_client_file_dialog']);
 }finally{env.close();}
});
for(const decline of ['no-template','cancel-draft','apply-failed'])test('save preflight '+decline+' reports no publication',async()=>{
 const env=setup(),{w}=env,calls=[];try{w.callApi=async method=>{calls.push(method);throw new Error('must not publish');};
 if(decline==='no-template'){w.PL.state.plan=null;w.RT.replaceTemplate(null,'');}
 else{w.PL.draftChanged=()=>true;w.PL.chooseDraftSave=async()=>decline==='cancel-draft'?'cancel':'apply';w.PL.apply=async()=>false;}
 let response;assert.equal(await w.RT.saveTemplate({onResponse:value=>{response=value;}}),false);
 assert.equal(w.GenshinBusinessUI.confirmedNoFileWrite(response),true);assert.deepEqual(calls,[]);
 }finally{env.close();}
});
test('save false without a publication receipt remains unknown',async()=>{
 const env=setup(),{w}=env;try{w.callApi=async()=>({code:0,data:{path:'selected.plan.json'}});w.RT.saveTemplate=async()=>false;
 const actions=w.AppEditorFileActions.create({root:w.document.body,show:async()=>{}});
 await assert.rejects(actions.execute({action:'save_as'},actions.capture()),/app_action_execution_outcome_unknown/);
 }finally{env.close();}
});
test('confirmed native file publication stays completed if later rendering fails',async()=>{
 const env=setup(),{w}=env,calls=[];try{w.callApi=async method=>{calls.push(method);return method==='dsh_client_file_dialog'?{code:0,data:{path:'selected.plan.json'}}:{code:0,data:{path:'selected.plan.json',file_sha:'published-sha'}};};
 w.PL.renderStatus=()=>{throw new Error('UI refresh failure after confirmed write');};
 const actions=w.AppEditorFileActions.create({root:w.document.body,show:async()=>{}}),outcome=await actions.execute({action:'save_as'},actions.capture());
 assert.equal(outcome.status,'completed');assert.deepEqual(calls,['dsh_client_file_dialog','save_plan']);assert.match(outcome.message,/已保存/);
 }finally{env.close();}
});
