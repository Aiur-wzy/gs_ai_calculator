'use strict';
const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path');
const {JSDOM}=require('../frontend/node_modules/jsdom');
const web=path.join(__dirname,'../src/genshin_app/gui/web');
const turn=()=>new Promise(resolve=>setImmediate(resolve));
const deferred=()=>{let resolve;const promise=new Promise(done=>{resolve=done;});return {promise,resolve};};
function setup(){
  const dom=new JSDOM('<body><main id="genshin-legacy-editor"><button id="opener">open</button><input id="tpl-path"></main></body>',{runScripts:'outside-only',url:'https://local.invalid/'}),w=dom.window;
  w.HTMLDialogElement.prototype.showModal=function(){this.open=true;};
  w.HTMLDialogElement.prototype.close=function(){this.open=false;};
  w.$=selector=>w.document.querySelector(selector);w.setStatus=()=>{};w.DS={prose:value=>value};w.callApi=async()=>({code:0,data:{}});
  let template={meta:{name:'original'},characters:[]};
  w.RT={state:{path:'original.json',revision:0,saveRequest:0,acceptedSaveRequest:0,plansDir:'.'},tpl:()=>template,
    replaceTemplate(value,file){template=value;this.state.path=file;this.state.revision++;},saveTemplate:async()=>false,renderAll(){}};
  w.EQ={closeView(){},lastReport:null};w.RS={invalidate(){}};
  for(const name of ['json-transport.js','gui-foundation-v2.js','ui.js','business-ui.js','plans.js','whiteboard.js','workspace-kernel.js','workspace.js','agent-document-adapter.js','app-file-actions.js'])w.eval(fs.readFileSync(path.join(web,name),'utf8'));
  w.PL.renderStatus=()=>{};w.WB.render=()=>{};w.WB.editCharacter=()=>{};
  w.PL.state.plan={manual_template:template};w.PL.state.savedJson=w.PL.currentInputJson();
  w.WB.state.board={board_id:'one',revision:0,title:'original board',team:[],actions:[],duration:20};w.WB.state.savedJson=w.WB.json();
  const escape=()=>w.document.dispatchEvent(new w.KeyboardEvent('keydown',{key:'Escape',bubbles:true,cancelable:true}));
  return {w,escape,close:()=>dom.window.close()};
}
const mutations={
  'plan input'(w){w.PL.state.plan.manual_template.meta.name='new unsaved input';},
  'board input'(w){w.WB.state.board.title='new board input';},
  'equipment draft'(w){w.PL.state.draft={manual_template:{meta:{name:'new equipment'}}};},
  'file identity'(w){w.RT.state.path='saved-elsewhere.json';w.PL.state.fileSha='new';},
  'same-id replacement'(w){w.WB.state.board={...w.WB.state.board};},
  'new open request'(w){w.AppDocumentOpenIntents.begin();},
};
for(const loader of ['native','resolved','whiteboard'])for(const phase of ['inspect','leave','read'])for(const [label,mutate] of Object.entries(mutations)){
  test(`${loader} open preserves current input after ${label} during ${phase}`,async()=>{
    const env=setup(),{w}=env,waiting=deferred(),calls=[];
    const kind=loader==='native'?'genshin-plan':loader==='whiteboard'?'genshin-whiteboard':null;
    w.callApi=async name=>{calls.push(name);if((phase==='inspect'&&name==='inspect_plan_file')||(phase==='read'&&name!=='inspect_plan_file'))await waiting.promise;
      if(name==='inspect_plan_file')return {code:0,data:{kind,file_sha:'a'.repeat(64)}};
      return {code:0,data:{plan:{manual_template:{meta:{name:'late file'},characters:[]}},template:{meta:{name:'late file'},characters:[]},
        board:{board_id:'loaded',revision:0,title:'late file',team:[],actions:[],duration:20},path:'selected.json',file_sha:'b'.repeat(64)}};};
    if(phase==='leave'){w.PL.leave=w.WB.leave=()=>waiting.promise;}
    const pending=loader==='whiteboard'?w.WB.load('selected.json'):w.RT.loadTemplate('selected.json');await turn();
    mutate(w);const before=w.PL.currentInputJson(),board=w.WB.json(),source=w.RT.state.path;
    waiting.resolve(true);assert.equal(await pending,false);
    assert.equal(w.PL.currentInputJson(),before);assert.equal(w.WB.json(),board);assert.equal(w.RT.state.path,source);
    if(phase==='leave')assert.deepEqual(calls,['inspect_plan_file']);env.close();
  });
}
for(const loader of ['native','resolved','whiteboard'])test(`${loader} unchanged open still succeeds`,async()=>{
  const env=setup(),{w}=env;
  w.callApi=async name=>({code:0,data:name==='inspect_plan_file'?{kind:loader==='native'?'genshin-plan':loader==='whiteboard'?'genshin-whiteboard':null}:
    {plan:{manual_template:{meta:{name:'loaded'},characters:[]}},template:{meta:{name:'loaded'},characters:[]},board:{board_id:'loaded',revision:0,title:'loaded',team:[],actions:[],duration:20},path:'selected.json',file_sha:'b'.repeat(64)}});
  assert.equal(await (loader==='whiteboard'?w.WB.load('selected.json'):w.RT.loadTemplate('selected.json')),true);env.close();
});
test('resolved edit followed by undo still invalidates the older open request revision',async()=>{
  const env=setup(),{w}=env,waiting=deferred();w.PL.state.plan=null;w.PL.state.savedJson=w.PL.currentInputJson();
  w.callApi=async name=>{if(name==='inspect_plan_file')return {code:0,data:{kind:null}};await waiting.promise;return {code:0,data:{template:{meta:{name:'late'}},path:'late.json'}};};
  const pending=w.RT.loadTemplate('late.json');await turn();const original=w.RT.tpl().meta.name;
  w.RT.tpl().meta.name='temporary edit';w.RT.state.revision++;w.RT.tpl().meta.name=original;w.RT.state.revision++;
  waiting.resolve();assert.equal(await pending,false);assert.equal(w.RT.tpl().meta.name,original);env.close();
});
for(const operation of ['plan-leave','plan-draft','board-leave','board-settings'])for(const dismissal of ['Escape','destroy']){
  test(`${operation} settles once through real ${dismissal} close and permits another dialog`,async()=>{
    const env=setup(),{w}=env;
    if(operation==='plan-leave')w.PL.state.plan.manual_template.meta.name='unsaved';
    if(operation==='board-leave')w.WB.state.board.title='unsaved';
    if(operation==='board-settings')w.WB.state.equipmentDrafts={one:{draft:{notes:'keep'}}};
    const promise=operation==='plan-leave'?w.PL.leave():operation==='plan-draft'?w.PL.chooseDraftSave():w.WB.leave();
    const active=w.AppDialogKernel.getActive();assert.ok(active);let closures=0;active.onClosed(()=>closures++);
    if(dismissal==='Escape')env.escape();else active.destroy('owner-unloaded');
    assert.equal(await promise,operation==='plan-draft'?'cancel':false);active.destroy();env.escape();assert.equal(closures,1);
    if(operation==='board-settings')assert.equal(w.WB.state.equipmentDrafts.one.draft.notes,'keep');
    const next=w.AppDialogKernel.create({title:'next'});assert.ok(next);next.destroy();env.close();
  });
}
test('real busy/veto dismissal does not settle a decision or close its Owner',()=>{
  const env=setup(),{w}=env;let closures=0,allow=false;
  const d=w.AppDialogKernel.create({title:'busy',busy:true,onClosed(){closures++;},onRequestClose:()=>allow});env.escape();assert.equal(closures,0);
  d.setBusy(false);env.escape();assert.equal(closures,0);allow=true;env.escape();assert.equal(closures,1);env.close();
});
test('native cancel cannot bypass a busy pending decision',async()=>{
  const env=setup(),{w}=env;w.PL.state.plan.manual_template.meta.name='unsaved';let settled=false;
  const promise=w.PL.leave().then(value=>{settled=true;return value;}),dialog=w.AppDialogKernel.getActive();dialog.setBusy(true);
  dialog.dispatchEvent(new w.Event('cancel',{cancelable:true}));await turn();assert.equal(settled,false);assert.equal(dialog.open,true);
  dialog.setBusy(false);dialog.dispatchEvent(new w.Event('cancel',{cancelable:true}));assert.equal(await promise,false);env.close();
});
test('a child drawer Escape does not settle the parent leave decision',async()=>{
  const env=setup(),{w}=env;w.PL.state.plan.manual_template.meta.name='unsaved';let settled=false;
  const promise=w.PL.leave().then(value=>{settled=true;return value;}),parent=w.AppDialogKernel.getActive(),trigger=w.document.createElement('button'),surface=w.document.createElement('section');
  parent.appBody.append(trigger,surface);w.AppSurfaceKernel.create({surface,trigger,kind:'drawer',removeOnClose:false});env.escape();await turn();
  assert.equal(settled,false);assert.equal(parent.open,true);env.escape();assert.equal(await promise,false);env.close();
});
test('closing a pending plan-close prompt releases closing guard',async()=>{
  const env=setup(),{w}=env;w.PL.state.plan.manual_template.meta.name='unsaved';assert.equal(w.PL.requestClose(),false);assert.equal(w.PL.state.closing,true);
  env.escape();await turn();assert.equal(w.PL.state.closing,false);assert.equal(w.PL.requestClose(),false);env.escape();await turn();assert.equal(w.PL.state.closing,false);env.close();
});
test('Agent file dialog observes Foundation dismissal exactly once and preserves busy refusal',async()=>{
  const env=setup(),{w}=env,results=[];
  const dialog=w.WS.bindAgentFileDialog(w.AppDialogKernel.create({title:'file'}),{agentActionId:'action',onAgentActionResult:value=>results.push(value)});
  dialog.setBusy(true);env.escape();dialog.agentActionCancel();assert.equal(results.length,0);assert.equal(dialog.open,true);
  dialog.setBusy(false);env.escape();dialog.agentActionCancel();assert.equal(results.length,1);assert.equal(results[0].status,'cancelled');env.close();
});
test('releasing a file dialog for the real operation does not claim cancellation',()=>{
  const env=setup(),{w}=env,results=[];
  const dialog=w.WS.bindAgentFileDialog(w.AppDialogKernel.create({title:'file'}),{agentActionId:'action',onAgentActionResult:value=>results.push(value)});
  assert.equal(dialog.releaseForOperation(),true);assert.equal(results.length,0);dialog.agentActionComplete();dialog.agentActionFail();assert.equal(results.length,1);assert.equal(results[0].status,'completed');env.close();
});
test('forced destruction during a file write waits for the real outcome, never reports not executed',()=>{
  const env=setup(),{w}=env,results=[];
  const dialog=w.WS.bindAgentFileDialog(w.AppDialogKernel.create({title:'saving'}),{agentActionId:'action',onAgentActionResult:value=>results.push(value)});
  dialog.beginOperation();env.escape();assert.equal(dialog.open,true);dialog.destroy('owner-unloaded');assert.equal(results.length,0);
  dialog.agentActionComplete('所提交副本确已保存。');dialog.endOperation();assert.equal(results.length,1);assert.equal(results[0].status,'completed');env.close();
});

for(const entry of ['native-picker','legacy-dialog','Agent-card'])test(`${entry} reports an acknowledged save even if later edits keep the board dirty`,async()=>{
  const env=setup(),{w}=env,waiting=deferred(),results=[];let writes=0;
  w.callApi=async name=>{if(name==='dsh_client_file_dialog')return {code:0,data:{cancelled:false,path:'copy.whiteboard.json'}};
    if(name==='whiteboard_save'){writes++;await waiting.promise;return {code:0,data:{path:'copy.whiteboard.json',file_sha:'new-disk-sha'}};}throw new Error(name);};
  let pending;
  if(entry==='native-picker')pending=w.WB.saveAs({onAgentActionResult:value=>results.push(value)});
  else if(entry==='legacy-dialog'){
    const dialog=w.WB.fileDialog('save',{agentActionId:'action',onAgentActionResult:value=>results.push(value)});
    [...dialog.querySelectorAll('button')].find(button=>button.textContent==='另存为').click();
  }else{
    w.WS.tab=()=> 'whiteboard';const actions=w.AppEditorFileActions.create({root:w.document.body,show:async()=>{}});
    pending=actions.execute({action:'save_as'},actions.capture()).then(result=>results.push(result));
  }
  await turn();assert.equal(writes,1);w.WB.state.board.title='edited during disk write';waiting.resolve();if(pending)await pending;await turn();
  assert.equal(results.length,1);assert.equal(results[0].status,'completed');assert.match(results[0].message,/已保存/);
  assert.equal(w.WB.dirty(),true);assert.equal(w.WB.state.fileSha,'new-disk-sha');assert.equal(writes,1);env.close();
});

for(const entry of ['workspace-picker','Agent-card'])test(`${entry} reports a native plan copy saved after the user switches documents`,async()=>{
  const env=setup(),{w}=env,waiting=deferred(),results=[];w.PL.state.plan.plan_id='native-one';w.RT.refreshPlans=()=>{};w.WS.tab=()=> 'rotation';let writes=0;
  w.callApi=async name=>{if(name==='dsh_client_file_dialog')return {code:0,data:{cancelled:false,path:'saved.plan.json'}};
    if(name==='save_plan'){writes++;await waiting.promise;return {code:0,data:{path:'saved.plan.json',file_sha:'new-disk-sha'}};}throw new Error(name);};
  let pending;if(entry==='Agent-card'){
    const actions=w.AppEditorFileActions.create({root:w.document.body,show:async()=>{}});pending=actions.execute({action:'save_as'},actions.capture()).then(result=>results.push(result));
  }else{
    const actions=w.GenshinBusinessUI.createWorkspaceFileActions({board:w.WB,editor:w.RT,workspace:{isBoard:()=>false,isPlan:()=>true,refresh(){}},api:(...args)=>w.callApi(...args),query:selector=>w.document.querySelector(selector),serialize:JSON.stringify,status(){},error(){}});
    pending=actions.saveAs({onAgentActionResult:value=>results.push(value)});
  }
  await turn();assert.equal(writes,1);w.PL.state.plan={plan_id:'new-document',manual_template:{meta:{name:'current document'},characters:[]}};w.RT.state.path='new-document.plan.json';w.document.querySelector('#tpl-path').value='new-document.plan.json';
  waiting.resolve();await pending;assert.equal(results.length,1);assert.equal(results[0].status,'completed');assert.match(results[0].message,/已保存/);assert.equal(w.RT.state.path,'new-document.plan.json');assert.equal(writes,1);env.close();
});
