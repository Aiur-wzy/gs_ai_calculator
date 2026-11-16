// Current public DOM/Host guard fixtures, not a DSH renderer substitute.
// Full index/IIFE -> Python GuiApi -> private pipe -> DSH -> engine acceptance:
// frontend/scripts/webview-smoke-dsh-browser.test.mjs.
const test=require('node:test'),assert=require('node:assert/strict'),path=require('node:path');
const {JSDOM}=require(require.resolve('jsdom',{paths:[path.join(__dirname,'../frontend')]}));
const R=require('./helpers/gui_runtime.cjs');
const CONFIG={prompt:'fixture public prompt',board_path:'C:/out/result.whiteboard.json',
  calculated_path:'C:/out/result.calculated.json',workflow:'calculate',timeout_ms:5000,
  provider_mode:'deterministic_provider_transport'};

function harness(options={}){
  const dom=new JSDOM('<div id="dsh-app-root"></div><div id="genshin-legacy-editor"></div><div id="workspace-menubar"></div><section id="tab-whiteboard"></section>',
    {url:'https://guard-fixture.invalid/',runScripts:'outside-only'}),w=dom.window,d=w.document;
  // Accelerate polling only; this fixture has no Session/Turn reducer or loop.
  w.setTimeout=(fn,delay)=>setTimeout(fn,Math.min(delay,1));
  const calls=[],effects={sent:0,review:0,apply:0,save:0,load:0,resultSave:0,resultImport:0,newSession:0};
  let sent=false,statusReads=0,savedBoard=null,page='whiteboard';
  const clone=value=>JSON.parse(JSON.stringify(value));
  const board=()=>({kind:'genshin-whiteboard',schema_version:3,board_id:'board-one',revision:0,
    title:'fixture',team:[],actions:[{id:'action-one',action_type_id:'skill'}]});
  const calculated={result_receipt:{plan_id:'plan-one',revision:1,input_fingerprint:'f'.repeat(64)},
    result:{summary:{total_damage:123},hits:[{id:'hit-one'}]},plan:{},report:{}};
  const add=(parent,label,run)=>{const n=d.createElement('button');n.textContent=label;n.addEventListener('click',run);parent.append(n);return n;};
  const app=d.getElementById('dsh-app-root'),editor=d.getElementById('genshin-legacy-editor');
  w.GenshinDshClient={ready:Promise.resolve()};w.GenshinEditorBridge={};
  w.WB={state:{board:null,path:'',fileSha:null,result:null,mode:'actions'}};
  w.WS={tab:()=>page};
  const showWhiteboard=()=>{editor.hidden=false;page='whiteboard';};
  add(app,'排轴白板',showWhiteboard);
  const embedded=d.createElement('div');embedded.className='gs-editor-assistant';app.append(embedded);
  add(embedded,'完整页面',()=>{
    embedded.remove();page='agent';editor.hidden=true;const conversation=d.createElement('div');conversation.dataset.conversationContent='';
    conversation.dataset.conversationSession='session-one';app.append(conversation);
    const input=d.createElement('div');input.setAttribute('contenteditable','true');app.append(input);
    input.addEventListener('paste',event=>{input.textContent=event.clipboardData.getData('text/plain');});
    input.addEventListener('keydown',event=>{if(event.key==='Enter'){
      effects.sent++;sent=true;const message=d.createElement('article');message.textContent=input.textContent;conversation.append(message);
    }});
    add(app,'新会话',()=>{effects.newSession++;conversation.dataset.conversationSession='session-two';});
    // Deliberately stale text: durable last_turn must decide completion.
    const badge=d.createElement('span');badge.textContent='正在使用工具';app.append(badge);
  });
  function popup(group){
    d.querySelector('.workspace-menu-popup')?.remove();const menu=d.createElement('div');menu.className='workspace-menu-popup';d.body.append(menu);
    const command=(id,run)=>{const n=add(menu,id,()=>{menu.remove();run();});n.dataset.command=id;};
    if(group==='文件'){
      command('new-board',()=>{w.WB.state.board=board();});
      command('save-as',()=>{effects.save++;w.WB.state.path=CONFIG.board_path;w.WB.state.fileSha='b'.repeat(64);savedBoard=clone(w.WB.state.board);});
      command('open',()=>{effects.load++;w.WB.state.board=clone(savedBoard);w.WB.state.path=CONFIG.board_path;w.WB.state.result=null;});
    }else command('return',()=>{effects.resultImport++;w.WB.state.result=calculated;w.WB.state.mode='damage';d.getElementById('tab-whiteboard').hidden=options.missingResultDom===true;});
  }
  for(const label of ['文件','计算与交接'])add(d.getElementById('workspace-menubar'),label,()=>popup(label));
  add(app,'审核候选',()=>{
    effects.review++;
    if(options.missingReviewDom)return;
    const review=d.createElement('div');review.setAttribute('role','dialog');review.setAttribute('aria-modal','true');d.body.append(review);
    add(review,'同意并同步',()=>{
      effects.apply++;review.remove();w.WB.state.board={...board(),revision:1};w.WB.state.result=calculated;showWhiteboard();
      add(app,'计算结果另存为…',()=>{effects.resultSave++;const p=d.createElement('p');p.textContent=CONFIG.calculated_path;app.append(p);});
      add(app,'查看正式白板',showWhiteboard);
    });
  });
  w.pywebview={api:new Proxy({}, {get(_target,method){return async(...args)=>{
    calls.push([method,...args]);
    if(method==='dsh_client_rpc'){
      const endpoint=args[0].endpoint;
      if(endpoint==='session/list')return {ok:true,value:{items:[{sessionId:'session-one',blank:true}]}};
      if(endpoint==='session/projections')return {ok:true,value:{values:{userQuestions:{active:options.pendingQuestion?[{id:'question-one'}]:[]}}}};
      throw new Error(`Unexpected public endpoint ${endpoint}`);
    }
    if(method==='dsh_client_session_status'){
      if(!sent)return {last_turn:null,agent_running:false,admission:{state:'idle'}};
      statusReads++;return options.status?options.status(statusReads):{last_turn:{turn:1,reason:{kind:'completed'}},agent_running:false,admission:{state:'idle'}};
    }
    if(method==='dsh_client_domain_binding')return {binding:{conversation_id:'domain-one'}};
    if(method==='dsh_client_candidate')return {kind:'whiteboard',has_calculation:true,revision:3,receipt_fingerprint:options.unsealed?null:'receipt-three'};
    if(method==='whiteboard_read_result')return {code:0,data:{path:CONFIG.calculated_path,file_sha:'c'.repeat(64),calculated}};
    throw new Error(`Unexpected Host method ${method}`);
  };}})};
  w.eval(R.webSource('webview-smoke.js'));
  return {w,dom,calls,effects,get statusReads(){return statusReads;}};
}

async function terminal(h,config={}){
  assert.equal(h.w.GenshinWebviewSmoke.start({...CONFIG,...config}),true);
  const until=Date.now()+10000;
  while(Date.now()<until){const receipt=h.w.GenshinWebviewSmoke.snapshot();if(receipt.status!=='running')return receipt;await new Promise(done=>setTimeout(done,2));}
  assert.fail(JSON.stringify(h.w.GenshinWebviewSmoke.snapshot()));
}
function noPublication(h){assert.equal(h.effects.review,0);assert.equal(h.effects.apply,0);assert.equal(h.effects.save,0);assert.equal(h.effects.resultSave,0);}

test('native publication and user picker retain reviewed JSON document transport',()=>{
  const dom=new JSDOM('',{runScripts:'outside-only'});try{
    dom.window.TextEncoder=TextEncoder;dom.window.eval(R.webSource('json-transport.js'));
    assert.equal(dom.window.JSONTransport.usesMethod('dsh_client_save_whiteboard_result'),true);
    assert.equal(dom.window.JSONTransport.usesMethod('dsh_client_file_dialog'),true);
  }finally{dom.window.close();}
});
test('invalid acceptance config fails before any public Host or user effect',async()=>{
  for(const config of [{prompt:''},{workflow:'invented'},{timeout_ms:999},{provider_mode:'untrusted'},{expectation:{unreviewed:true}}]){
    const h=harness();try{const receipt=await terminal(h,config);assert.equal(receipt.status,'failed');assert.equal(receipt.error_code,'webview_smoke_config_invalid');assert.equal(receipt.files_published,false);assert.equal(h.calls.length,0);noPublication(h);}finally{h.dom.window.close();}
  }
});
test('committed failed turn cannot use a visible stale review button',async()=>{
  const h=harness({status:()=>({last_turn:{turn:1,reason:{kind:'failed'}},agent_running:false,admission:{state:'idle'}})});
  try{const receipt=await terminal(h);assert.equal(receipt.status,'failed');assert.equal(receipt.error_code,'webview_smoke_agent_failed');assert.equal(receipt.real_webview_dom_verified,false);noPublication(h);assert.equal(h.calls.some(row=>row[0]==='dsh_client_candidate'),false);}finally{h.dom.window.close();}
});
test('idle physical provider is not a completed model turn',async()=>{
  const h=harness({status:count=>count<4?{last_turn:null,agent_running:false,admission:{state:'idle'}}:{last_turn:{turn:1,reason:{kind:'failed'}},agent_running:false,admission:{state:'idle'}}});
  try{const receipt=await terminal(h);assert.ok(h.statusReads>=4);assert.equal(receipt.error_code,'webview_smoke_agent_failed');noPublication(h);}finally{h.dom.window.close();}
});
test('durable completion reaches exact candidate lookup despite stale using-tool text',async()=>{
  const h=harness({unsealed:true});try{
    const receipt=await terminal(h);assert.equal(receipt.error_code,'webview_smoke_candidate_not_ready');
    const candidate=h.calls.find(row=>row[0]==='dsh_client_candidate');assert.equal(candidate[1].conversation_id,'domain-one');noPublication(h);
  }finally{h.dom.window.close();}
});
test('missing review confirmation cannot sync even after candidate acceptance',async()=>{
  const h=harness({missingReviewDom:true});try{
    const receipt=await terminal(h,{timeout_ms:1000});assert.equal(receipt.status,'failed');assert.equal(receipt.error_code,'webview_smoke_review_not_rendered');
    assert.equal(h.effects.review,1);assert.equal(h.effects.apply,0);assert.equal(h.effects.save,0);assert.equal(h.effects.resultSave,0);
  }finally{h.dom.window.close();}
});
test('missing result DOM cannot be reported as verified after file effects settle',async()=>{
  const h=harness({missingResultDom:true});try{
    const receipt=await terminal(h);assert.equal(receipt.status,'failed');assert.equal(receipt.error_code,'webview_smoke_result_not_displayed');
    assert.equal(receipt.real_webview_dom_verified,false);assert.equal(receipt.display_verified,false);
    assert.equal(h.effects.apply,1);assert.equal(h.effects.save,1);assert.equal(h.effects.resultSave,1);assert.equal(h.effects.load,1);
  }finally{h.dom.window.close();}
});
test('one acceptance invocation cannot duplicate prompt application or file effects',async()=>{
  const h=harness();try{
    assert.equal(h.w.GenshinWebviewSmoke.start(CONFIG),true);assert.equal(h.w.GenshinWebviewSmoke.start(CONFIG),false);
    const until=Date.now()+10000;let receipt;
    while(Date.now()<until){receipt=h.w.GenshinWebviewSmoke.snapshot();if(receipt.status!=='running')break;await new Promise(done=>setTimeout(done,2));}
    assert.equal(receipt.status,'verified');assert.equal(receipt.dom.damage_view,true);
    assert.deepEqual(h.effects,{sent:1,review:1,apply:1,save:1,load:1,resultSave:1,resultImport:1,newSession:0});
    assert.equal(h.w.GenshinWebviewSmoke.start(CONFIG),false);
  }finally{h.dom.window.close();}
});
test('public pending user question suspends without claiming publication',async()=>{
  const h=harness({pendingQuestion:true});try{
    const receipt=await terminal(h);assert.equal(receipt.status,'awaiting_user_answer');assert.equal(receipt.pending_question_count,1);
    assert.equal(receipt.files_published,false);assert.equal(receipt.real_webview_dom_verified,false);noPublication(h);
  }finally{h.dom.window.close();}
});
