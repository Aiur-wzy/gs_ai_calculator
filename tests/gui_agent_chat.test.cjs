// Historical compatibility units below do not qualify the production DSH UI.
// Production ownership assertions and migrated App-CAS tests use the current entry.
const GUI_TEST_RUNTIME = require('./helpers/gui_runtime.cjs');
const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');
const clone=value=>JSON.parse(JSON.stringify(value)),tick=()=>new Promise(resolve=>setImmediate(resolve));
const statusEvents=(from,to,state='running')=>Array.from({length:Math.max(0,to-from+1)},(_,index)=>({seq:from+index,type:'status',state}));
class Element{
  constructor(tag='div'){this.tagName=tag;this.children=[];this.attributes={};this.dataset={};this.listeners={};this.style={};this.value='';this.hidden=false;this.scrollTop=0;this.scrollHeight=200;this.clientHeight=200;
    const classes=new Set();this.classList={add:v=>classes.add(v),remove:v=>classes.delete(v),toggle:(v,on)=>on?classes.add(v):classes.delete(v),contains:v=>classes.has(v)};}
  append(...nodes){for(const node of nodes){if(node.parent)node.parent.children=node.parent.children.filter(child=>child!==node);node.parent=this;this.children.push(node);}}appendChild(node){this.append(node);return node;}replaceChildren(...nodes){for(const child of this.children)child.parent=null;this.children=[];this.text='';this.append(...nodes);}
  set innerHTML(value){this.children=[];this.text=value;}set textContent(value){this.children=[];this.text=String(value);}get textContent(){return(this.text||'')+this.children.map(n=>n.textContent).join('');}
  setAttribute(key,value){this.attributes[key]=String(value);}addEventListener(key,fn){(this.listeners[key]||=[]).push(fn);}
  querySelector(tag){return all(this).slice(1).find(node=>node.tagName===tag)||null;}
  emit(key,extra={}){return(this.listeners[key]||[]).map(fn=>fn({preventDefault(){},stopPropagation(){},...extra})).at(-1);}focus(){}showModal(){this.open=true;}close(){this.open=false;}
  remove(){if(this.parent)this.parent.children=this.parent.children.filter(n=>n!==this);}
}
const all=node=>[node,...node.children.flatMap(all)];
const plan=()=>({kind:'genshin-plan',plan_id:'native-one',revision:0,manual_template:{meta:{name:'原生测试'},characters:[{id:'a',stats:{atk:2000}}]},resolution:{state:'ready'}});
const board=()=>({kind:'genshin-whiteboard',board_id:'board-one',revision:0,title:'白板测试',team:[{id:'a',panel:{atk:2000}}],actions:[{id:'skill',start:0}],duration:20});
const candidate=()=>({revision:2,receipt_fingerprint:'receipt-two',ready:true,summary:'补齐计算事件',diff:[{path:'hits',before:0,after:2,reason:'由数据库补充'}],issues:[]});
const budgetDefaults={wall_clock_seconds:7200,provider_steps:4096,app_tool_calls:3072,web_tool_calls:1024,total_tool_calls:4096,web_search_calls:256,web_search_queries:1024,web_fetch_calls:384,per_request_output_tokens:256000,cumulative_output_tokens:2000000,cumulative_input_soft_tokens:64000000,cumulative_input_hard_tokens:128000000,cumulative_uncached_input_tokens:4000000,retry_count:0,read_tool_calls:2048,mutation_tool_calls:1024};
const budgetMin={wall_clock_seconds:60,provider_steps:1,app_tool_calls:1,web_tool_calls:0,total_tool_calls:1,web_search_calls:0,web_search_queries:0,web_fetch_calls:0,per_request_output_tokens:1024,cumulative_output_tokens:1024,cumulative_input_soft_tokens:262144,cumulative_input_hard_tokens:1000000,cumulative_uncached_input_tokens:65536,read_tool_calls:0,mutation_tool_calls:0};
const budgetMax={wall_clock_seconds:86400,provider_steps:32768,app_tool_calls:16384,web_tool_calls:16384,total_tool_calls:16384,web_search_calls:4096,web_search_queries:16384,web_fetch_calls:4096,per_request_output_tokens:256000,cumulative_output_tokens:32000000,cumulative_input_soft_tokens:1000000000,cumulative_input_hard_tokens:2000000000,cumulative_uncached_input_tokens:256000000,read_tool_calls:16384,mutation_tool_calls:16384};
const budgetSnapshot=(overrides={},generation=0)=>({contract:'genshin-native-turn-budget-policy-v1',policy_id:'complete_table_v1',policy_version:5,generation,defaults:clone(budgetDefaults),overrides:clone(overrides),effective:{...clone(budgetDefaults),...clone(overrides)},follows_defaults:Object.keys(overrides).length===0,source:Object.keys(overrides).length?'gui':'default',updated_at:'2026-10-30T00:00:00Z',apply_scope:'next_turn',limits:Object.fromEntries(Object.keys(budgetDefaults).map(key=>[key,key==='retry_count'?{min:0,max:0,adjustable:false}:{min:budgetMin[key],max:budgetMax[key],adjustable:true}]))});
function setup(options={}){
  const body=new Element('body'),root=new Element('aside'),badge=new Element('button'),pageHost=new Element('div'),pageTab=new Element('button');root.id='agent-chat';root.hidden=true;badge.id='agent-chat-toggle';pageHost.id='agent-page-host';pageTab.id='agent-page-tab';body.append(pageHost,root,badge,pageTab);
  const calls=[],timers=new Map(),scheduledDelays=[],orders=[],fileActions=[],windowListeners={},stored=new Map(Object.entries(options.storage||{}));let timer=0,tab='whiteboard',wbDraft=false,nativeDraft=false,uuid=0;
  const w={console,Math,Date,innerWidth:options.innerWidth??1440,__GENSHIN_AGENT_TEST__:options.agentTest!==false,crypto:{randomUUID:()=>`request-${++uuid}`},setTimeout:(fn,delay)=>{scheduledDelays.push(delay);timers.set(++timer,fn);return timer;},clearTimeout:id=>timers.delete(id),setStatus(){},confirm:()=>true,
    localStorage:{getItem:key=>stored.has(key)?stored.get(key):null,setItem:(key,value)=>stored.set(key,String(value))},addEventListener:(key,fn)=>(windowListeners[key]||=[]).push(fn)};w.window=w;
  w.document={body,createElement:tag=>new Element(tag),createTextNode:value=>{const node=new Element('#text');node.textContent=value;return node;},getElementById:id=>all(body).find(n=>n.id===id)||null};
  w.WS={isBoard:()=>tab==='whiteboard',tab:()=>tab,navigate:value=>{orders.push(`navigate:${value}`);tab=value;},attachContextHistory:(node,settings)=>{w.attachedContextHistory={node,settings};return true;},openFile:options=>{orders.push('open-file');fileActions.push(options);return {agentActionCancel:()=>options?.onAgentActionResult?.({status:'cancelled',message:'测试取消打开'})};},saveAs:options=>{orders.push('save-as');fileActions.push(options);return {agentActionCancel:()=>options?.onAgentActionResult?.({status:'cancelled',message:'测试取消另存'})};},refresh(){}};
  pageTab.addEventListener('click',()=>{if(tab!=='agent')body.dataset.agentReturnTab=tab;tab='agent';});
  w.WB={state:{board:board(),path:'board.json',fileSha:'board-sha',result:null},dirty:()=>true,unappliedEditor:()=>wbDraft,save:async()=>{orders.push('wb-save');return true;},saveAs:()=>orders.push('wb-save-as'),leave:async()=>{orders.push('wb-leave');return true;},render(){orders.push('wb-render');}};
  w.RT={state:{path:'native.plan.json',revision:0},tpl:()=>w.PL.state.plan.manual_template,saveTemplate:async()=>{orders.push('rt-save');return true;}};
  w.PL={state:{plan:plan(),fileSha:'native-sha',sourceEpoch:0,savedJson:'original-saved'},draftChanged:()=>nativeDraft,
    currentInputJson:()=>{const value=clone(w.PL.state.plan);delete value.resolution;delete value.resolved_template;return JSON.stringify(value);},
    dirty:()=>true,leave:async()=>{orders.push('pl-leave');return true;},replace:(next,file,sha)=>{orders.push('pl-replace');w.PL.state.plan=next;w.RT.state.path=file;w.PL.state.fileSha=sha;w.PL.state.savedJson=null;},renderStatus(){}};
  w.RS={state:{result:null,pending:false},isCurrent:()=>true,runCalc:async()=>{orders.push('calc');return {};},exportResult:async format=>{orders.push(`export:${format}`);return true;}};
  w.callApi=async(name,...args)=>{calls.push({name,args:clone(args)});
    if(name==='agent_detect')return {code:0,data:{candidates:[{id:'codex-one',provider:'Codex',path:'C:/safe/codex.exe',version:'0.test',supported:true}],others:[]}};
    if(name==='agent_connect')return {code:0,data:{state:'ready',models:[
      {model:'chosen-model',displayName:'指定模型',supportedReasoningEfforts:[{reasoningEffort:'off'},{reasoningEffort:'low'},{reasoningEffort:'high'},{reasoningEffort:'max'}],defaultReasoningEffort:'high'},
      {model:'other-model',displayName:'另一模型',supportedReasoningEfforts:[{reasoningEffort:'low'},{reasoningEffort:'max'}],defaultReasoningEffort:'max'}
    ],default:{model:'chosen-model',reasoningEffort:'high',available:true,reason:null},account:{type:'chatgpt'},limits:{ordinary_remaining_percent:50}}};
    if(name==='agent_budget_settings')return {code:0,data:budgetSnapshot()};
    if(name==='agent_update_budget_settings')return {code:0,data:budgetSnapshot(args[0],1)};
    if(name==='agent_reset_budget_settings')return {code:0,data:budgetSnapshot({},1)};
    if(name==='agent_start')return {code:0,data:{session_id:'session-one',run_id:'run-one',kind:args[0].kind,workflow:args[0].workflow,state:'running',active:true,next_seq:1,events:[{seq:1,type:'message',run_id:'run-one',message_id:'user-one',role:'user',message:args[0].message}],candidate:null}};
    if(name==='agent_send')return {code:0,data:{session_id:args[0],run_id:'run-two',state:'running',active:true,next_seq:4,events:[{seq:4,type:'message',run_id:'run-two',message_id:'user-two',role:'user',message:args[1]}]}};
    if(name==='agent_poll')return {code:0,data:{session_id:args[0],state:'completed',active:false,next_seq:3,events:[{seq:2,type:'delta',run_id:'run-one',message_id:'answer',delta:'已补'}, {seq:3,type:'delta',run_id:'run-one',message_id:'answer',delta:'齐'}],candidate:candidate()}};
    if(name==='agent_stop')return {code:0,data:{session_id:args[0],state:'stopped',active:false,next_seq:4,events:[]}};
    if(name==='agent_resolve_app_action'){const existing=[...(w.AC?.state.sessions.values?.()||[])].flatMap(s=>s.appActions||[]).find(row=>row.action_id===args[1])||{};return {code:0,data:{action_id:args[1],action:existing.action,status:args[2],result_message:args[4],workspace:args[3]}};}
    if(name==='agent_present_app_action'){const existing=[...(w.AC?.state.sessions.values?.()||[])].flatMap(s=>s.appActions||[]).find(row=>row.action_id===args[1])||{},card={...existing,status:'presented',ui_receipt:`ui_${'r'.repeat(43)}`,dirty_open_guard_required:existing.action==='open_document'&&args[2]?.dirty===true};return {code:0,data:{status:'presented_for_user',card}};}
    if(name==='agent_claim_app_action'){const existing=[...(w.AC?.state.sessions.values?.()||[])].flatMap(s=>s.appActions||[]).find(row=>row.action_id===args[1])||{};return {code:0,data:{status:'claimed_for_external_execution',card:{...existing,status:'presented',ui_receipt:args[2]},request:{action_id:args[1],action:existing.action,reason:existing.reason||'',suggested_name:existing.suggested_name||null,document_kind:args[3]?.document_kind,display_name:args[3]?.display_name,current_view:args[3]?.current_view,dirty_open_guard_required:existing.action==='open_document'&&args[3]?.dirty===true}}};}
    if(name==='agent_settle_app_action'){const existing=[...(w.AC?.state.sessions.values?.()||[])].flatMap(s=>s.appActions||[]).find(row=>row.action_id===args[1])||{},outcome=args[3],detached=existing.action==='open_document'&&outcome.status==='completed';return {code:0,data:{status:'settled',card:{...existing,status:outcome.status,result_message:outcome.message,ui_receipt:args[2]},...(detached?{session_detached:true,can_continue:false,can_resume:true,activation:{state:'cold',recovery:null}}:{})}};}
    if(name==='agent_recover_app_action_without_replay'){const existing=[...(w.AC?.state.sessions.values?.()||[])].flatMap(s=>s.appActions||[]).find(row=>row.action_id===args[1])||{};return {code:0,data:{status:'recovered_without_replay',card:{...existing,status:'failed',result_message:'未知结果已解除且未重放。',ui_receipt:args[2]},request:null}};}
    if(name==='agent_sessions')return {code:0,data:{sessions:[]}};
    if(name==='agent_candidate')return {code:0,data:candidate()};
    if(name==='inspect_plan_file')return {code:0,data:{file_sha:'native-sha'}};
    if(name==='agent_accept'){const next=plan();next.manual_template.hits=[{id:'new-hit'}];return {code:0,data:args[1]==='whiteboard'?{kind:'whiteboard',board:clone(args[2]),revision:args[3],receipt_fingerprint:args[4],result_path:'deliveries/stable/calculated.json'}:{kind:'native',revision:args[3],receipt_fingerprint:args[4],plan:next}};}
    if(name==='agent_candidate_applied')return {code:0,data:{status:'applied',kind:args[1],revision:args[3],receipt_fingerprint:args[4],app_actions:[]}};
    if(name==='agent_abort_candidate_application'){
      const session=w.AC?.state.sessions.get(args[0]),pending=session?.pendingApplicationAck,prior=session?.applicationIntent?.intent;
      const kind=pending?.base?.kind||prior?.kind||(w.WS.isBoard()?'whiteboard':'native'),revision=pending?.revision??prior?.revision??session?.candidate?.revision??2,receipt=pending?.receipt??prior?.receipt_fingerprint??session?.candidate?.receipt_fingerprint??'receipt-two';
      return {code:0,data:{session_id:args[0],state:'ready',active:false,kind,workflow:'staged',next_seq:0,events:[],application_intent:{conversation_id:args[0],status:'aborted',intent:{state:'aborted',kind,revision,receipt_fingerprint:receipt}},application_aborted:true}};
    }
    if(name==='whiteboard_read_result')return {code:0,data:{path:args[1],calculated:{plan:plan(),result:{summary:{total_damage:100}}}}};
    if(name==='agent_forget'||name==='agent_shutdown'||name==='confirm_close'){orders.push(name);return {code:0,data:{}};}
    return {code:1,message:'unknown test call'};
  };
  vm.createContext(w);for(const file of ['ui.js','agent-workbench-contract.js','agent-workbench-store.js','agent-workbench-client.js','agent-workbench-transport-controller.js','agent-workbench-selectors.js','agent-workbench-state-adapter.js','agent-workbench-view.js','agent-workbench-shell.js','agent-activity-feedback.js','agent-turn-controller.js','agent-provider-settings.js','agent-composer.js','agent-message-renderer.js','agent-timeline-node-renderer.js','context-menu.js','agent-session-list.js','agent-session-controller.js','agent-turn-outline.js','agent-history-window.js','agent-conversation-window.js','agent-conversation-view.js','agent-question-inbox.js','agent-question-answer-controller.js','agent-telemetry.js','agent-workbench-read-controller.js','agent-checkpoints.js','agent-checkpoint-controller.js','agent-domain-transactions.js','agent-workflow-review-view.js','agent-document-adapter.js','agent-application-recovery.js','agent-app-lifecycle-controller.js','agent-checkpoint-integration.js','agent-workbench-presentation-controller.js','agent-workbench-layout.js','agent-workbench-bootstrap.js','agent-chat.js'])GUI_TEST_RUNTIME.loadWebFile(w, path.join(__dirname,'../src/genshin_app/gui/web',file), {filename:file});w.AC.init();
  const ready=async()=>{await w.AC.detect();await w.AC.connect();w.AC.nodes.model.value='chosen-model';w.AC.nodes.model.emit('change');w.AC.nodes.effort.value='high';w.AC.nodes.effort.emit('change');};
  const start=async(kind='whiteboard')=>{await ready();w.AC.nodes.kind.value=kind;if(kind==='whiteboard'){w.AC.nodes.workflow.value='calculate';w.AC.nodes.workflowExplicit=true;}w.AC.nodes.input.value='补齐计算但保留用户动作';await w.AC.send();return w.AC.selected();};
  const completed=async(kind='whiteboard')=>{const session=await start(kind);await w.AC.poll(session,true);return session;};
  const result={w,AC:w.AC,body,root,badge,pageHost,pageTab,calls,timers,scheduledDelays,orders,fileActions,stored,ready,start,completed,tab:value=>tab=value,draft:(kind,value)=>kind==='whiteboard'?wbDraft=value:nativeDraft=value,
    emitWindow:(key,extra={})=>(windowListeners[key]||[]).map(fn=>fn({preventDefault(){},...extra}))};installRealDraftEditor(result);return result;
}

const readyFileAction=(session,action,overrides={})=>{const current=candidate();session.candidate=current;session.applied=true;session.appliedReceipt=current.receipt_fingerprint;
  return {action_id:'f'.repeat(32),action,status:'queued',candidate_revision:current.revision,receipt_fingerprint:current.receipt_fingerprint,requires_applied_candidate:action!=='open_document',
    preconditions:{contract:'agent-app-action-preconditions-v1',status:'ready',issues:[],candidate_revision:current.revision,receipt_fingerprint:current.receipt_fingerprint,requires_applied_candidate:action!=='open_document'},...overrides};};
const twoPhaseAction=(session,action,overrides={})=>readyFileAction(session,action,{execution_mode:'two_phase',...overrides});
const claimedResponse=(action,receipt,overrides={})=>({code:0,data:{status:'claimed_for_external_execution',card:{...action,reason:action.reason||'',suggested_name:action.suggested_name||null,status:'presented',ui_receipt:receipt},request:{
  action_id:action.action_id,action:action.action,reason:action.reason||'',suggested_name:action.suggested_name||null,document_kind:'whiteboard',display_name:'白板测试',current_view:'whiteboard',dirty_open_guard_required:action.action==='open_document',
},...overrides}});

test('initialization performs no connection or inference, drawer open only detects and lists',async()=>{
  const e=setup();assert.equal(e.calls.length,0);e.AC.open();await tick();assert.ok(e.calls.some(c=>c.name==='agent_detect'));assert.ok(e.calls.some(c=>c.name==='agent_sessions'));
  assert.ok(e.calls.some(c=>c.name==='agent_budget_settings'));
  assert.ok(e.calls.every(c=>!['agent_connect','agent_start','agent_send'].includes(c.name)));assert.equal(e.root.hidden,false);e.AC.close();assert.equal(e.root.hidden,true);
});
test('duplicate initialization returns the exact production composition without adding listeners or DOM',()=>{
  const e=setup(),first=e.AC.bootstrap.snapshot(),children=e.root.children.length,badgeListeners=e.badge.listeners.click.length;
  const second=e.AC.init();assert.equal(second,first);assert.equal(e.root.children.length,children);assert.equal(e.badge.listeners.click.length,badgeListeners);
});
test('accepted turn remains active through provider idle until the Host publishes an explicit terminal state',async()=>{
  const e=setup(),s=await e.start();assert.equal(s.awaitingTurnSettlement,true);assert.equal(e.AC.isActive(s),true);
  e.AC.receive(s,{session_id:s.session_id,next_seq:2,state:'idle',active:false,events:[{seq:2,type:'status',state:'idle'}]});
  assert.equal(s.awaitingTurnSettlement,true);assert.equal(e.AC.isActive(s),true);
  e.AC.receive(s,{session_id:s.session_id,next_seq:3,state:'completed',terminal_status:'completed',active:false,events:[{seq:3,type:'turn.completed'}],candidate:candidate()});
  assert.equal(s.awaitingTurnSettlement,false);assert.equal(e.AC.isActive(s),false);
});
test('Host-owned session rename archive and read state never use optimistic local truth',async()=>{
  const e=setup(),old=e.w.callApi;let row={session_id:'managed-session',title:'旧名称',state:'completed',updated_at:'2026-10-24T00:00:00Z',unread:true,archived:false,metadata_revision:2,can_archive:true};let failRead=true;
  e.w.callApi=async(name,...args)=>{e.calls.push({name,args:clone(args)});if(name==='agent_sessions')return {code:0,data:{sessions:[clone(row)],workbench_capabilities:{session_lifecycle:true}}};if(name==='agent_rename_session'){row={...row,title:args[1].title,metadata_revision:3};return {code:0,data:{status:'applied'}};}if(name==='agent_archive_session'){row={...row,archived:args[1].archived,state:args[1].archived?'archived':'completed',metadata_revision:4};return {code:0,data:{status:'applied'}};}if(name==='agent_mark_session_read'){if(failRead)return {code:1,reason:'native_session_metadata_stale'};row={...row,unread:false,metadata_revision:5};return {code:0,data:{status:'applied'}};}return old(name,...args);};
  await e.AC.loadHistory();e.AC.state.selected='managed-session';e.AC.state.viewEpoch++;assert.equal(e.AC.session('managed-session').unread,true);assert.equal(await e.AC.markSessionRead('managed-session'),false);assert.equal(e.AC.session('managed-session').unread,true);
  failRead=false;assert.equal(await e.AC.markSessionRead('managed-session'),true);assert.equal(e.AC.session('managed-session').unread,false);assert.equal(await e.AC.renameSession('managed-session','新名称',5),true);assert.equal(e.AC.session('managed-session').title,'新名称');assert.equal(await e.AC.archiveSession('managed-session',true,3),true);assert.equal(e.AC.session('managed-session').archived,true);
});
test('workbench exposes seven independent state axes without inventing missing runtime facts',async()=>{
  const e=setup();assert.equal(e.AC.nodes.connect.textContent,'验证 API 设置');assert.equal(e.AC.nodes.statusAxes.children.length,7);
  await e.ready();assert.equal(e.AC.nodes.status.textContent,'已配置，可发送');
  const runtime=e.AC.nodes.statusAxes.children.find(node=>node.dataset.axis==='runtime_state');assert.equal(runtime.dataset.state,'unknown');assert.match(runtime.textContent,/未知/);
  const s=e.AC.session('axis-session');s.live=true;e.AC.state.selected=s.session_id;
  e.AC.receive(s,{session_id:s.session_id,provider_id:'codex-one',next_seq:0,events:[],credential_state:'saved',provider_state:'verified',runtime_state:'ready',runtime_health:'healthy',runtime_incarnation:'runtime-axis',conversation_state:'running',turn_state:'using_tool',document_state:'dirty',inbox_state:'empty'});
  const byAxis=Object.fromEntries(e.AC.nodes.statusAxes.children.map(node=>[node.dataset.axis,node]));assert.equal(byAxis.runtime_state.dataset.state,'ready');assert.match(byAxis.turn_state.textContent,/使用工具/);assert.equal(e.AC.nodes.statusAxes.title,'本地服务 healthy');
});
test('workbench does not claim send readiness for an unselected provider or any explicit non-ready runtime',async()=>{
  const e=setup();await e.ready();e.AC.state.candidates.push({id:'other',provider:'Other',supported:false});e.AC.nodes.cli.value='other';e.AC.nodes.cli.emit('change');
  assert.notEqual(e.AC.nodes.status.textContent,'已配置，可发送');assert.equal(e.AC.nodes.send.disabled,true);assert.equal(e.AC.nodes.statusAxes.children.find(node=>node.dataset.axis==='provider_state').dataset.state,'unverified');assert.equal(e.AC.nodes.statusAxes.children.find(node=>node.dataset.axis==='credential_state').dataset.state,'unknown');
  e.AC.nodes.cli.value='codex-one';e.AC.nodes.cli.emit('change');const s=e.AC.session('failed-runtime');s.live=true;e.AC.state.selected=s.session_id;
  e.AC.receive(s,{session_id:s.session_id,provider_id:'codex-one',next_seq:0,events:[],runtime_state:'failed',runtime_health:'unhealthy',runtime_failure_code:'pipe_read_timeout'});
  assert.equal(e.AC.nodes.send.disabled,true);assert.match(e.AC.nodes.statusAxes.title,/pipe_read_timeout/);e.AC.nodes.input.value='不应发送';await e.AC.send();assert.equal(e.calls.some(call=>call.name==='agent_send'),false);
  e.AC.receive(s,{session_id:s.session_id,provider_id:'codex-one',next_seq:0,events:[],runtime_state:'stopped',runtime_health:'stopped',failure_code:null});
  assert.equal(e.AC.nodes.send.disabled,true);assert.notEqual(e.AC.nodes.status.textContent,'已配置，可发送');e.AC.nodes.input.value='停止后仍不应发送';await e.AC.send();assert.equal(e.calls.some(call=>call.name==='agent_send'),false);
  assert.equal(Object.hasOwn(s.ui_state,'runtime_state'),false);assert.equal(e.AC.state.hostUi.runtime_state,'stopped');
});
test('saved-key cold native runtime is prepared asynchronously before the provider turn and exposes phases',async()=>{
  const e=setup(),old=e.w.callApi;e.AC.state.connection='ready';e.AC.state.candidates=[{id:'deepseek-api',kind:'native_api',provider:'DeepSeek API',supported:true}];e.AC.state.cliId='deepseek-api';e.AC.state.connectedCliId='deepseek-api';e.AC.state.credential={saved:true,status:'saved'};e.AC.state.capabilities={runtime_prepare:{contract:'native-runtime-prepare-v1'},runtime_status_wait:{contract:'native-runtime-status-v1'},system_resume_notify:{contract:'native-system-resume-v1'}};e.AC.state.models=[{model:'chosen-model',displayName:'指定模型',supportedReasoningEfforts:[{reasoningEffort:'high'}],defaultReasoningEffort:'high'}];e.AC.state.model='chosen-model';e.AC.state.effort='high';e.AC.nodes.model.value='chosen-model';e.AC.nodes.effort.value='high';e.AC.state.hostUi={runtime_state:'cold',runtime_health:'stopped',credential_mode:'saved',runtime_phase:'cold'};e.AC.nodes.input.value='冷启动后发送';e.AC.refresh();assert.equal(e.AC.nodes.send.disabled,false);
  let release;const order=[];e.w.callApi=async(name,...args)=>{e.calls.push({name,args:clone(args)});order.push(name);if(name==='agent_prepare_runtime')return {code:0,data:{operation_id:'runtime-prepare-cold-one',purpose:'create',status:'preparing'}};if(name==='agent_wait_runtime_prepare')return new Promise(resolve=>release=resolve);if(name==='agent_runtime_status')return {code:0,data:{contract:'native-runtime-status-v1',requested_after_seq:0,next_seq:3,gap:false,baseline:{runtime_state:'ready',runtime_health:'healthy',runtime_incarnation:'runtime-cold-one',failure_code:null,credential_mode:'saved',phase:'preparing_request',operation_id:'runtime-prepare-cold-one',purpose:'create',warm_lease_ms_remaining:300000,system_resume_generation:0},events:[],closed:false,system_resume_generation:0,subscription_generation:0}};return old(name,...args);};
  const sending=e.AC.send();await tick();assert.ok(order.includes('agent_prepare_runtime'));assert.ok(order.includes('agent_wait_runtime_prepare'));assert.equal(order.includes('agent_start'),false);assert.equal(e.AC.state.busy,'runtime-start');assert.equal(e.AC.nodes.stop.hidden,false);
  e.AC.applyRuntimeStatus({contract:'native-runtime-status-v1',requested_after_seq:0,next_seq:2,gap:false,baseline:{runtime_state:'starting',runtime_health:'unknown',runtime_incarnation:null,failure_code:null,credential_mode:'saved',phase:'named_pipe_handshake',operation_id:'runtime-prepare-cold-one',purpose:'create',warm_lease_ms_remaining:null,system_resume_generation:0},events:[],closed:false,system_resume_generation:0});assert.match(e.AC.nodes.status.textContent,/建立本地连接/);assert.doesNotMatch(e.AC.nodes.status.textContent,/Named Pipe|runtime|Host|Queue/i);assert.equal(e.AC.state.hostUi.runtime_phase,'named_pipe_handshake');
  release({code:0,data:{contract:'native-runtime-prepare-v1',operation_id:'runtime-prepare-cold-one',purpose:'create',status:'ready',cancellation_requested:false,failure_code:null,provider_request_admitted:false,timed_out:false}});await sending;
  assert.ok(order.indexOf('agent_prepare_runtime')<order.indexOf('agent_start'));assert.ok(order.indexOf('agent_runtime_status')<order.indexOf('agent_start'));assert.equal(e.AC.state.hostUi.runtime_state,'ready');
});
test('saved-key cold runtime prepares an existing live conversation before its followup',async()=>{
  const e=setup(),old=e.w.callApi,s=e.AC.session('cold-existing');e.AC.state.connection='ready';e.AC.state.candidates=[{id:'deepseek-api',kind:'native_api',supported:true}];e.AC.state.cliId=e.AC.state.connectedCliId='deepseek-api';e.AC.state.credential={saved:true,status:'saved'};e.AC.state.capabilities={runtime_prepare:{contract:'native-runtime-prepare-v1'}};e.AC.state.models=[{model:'chosen-model',supportedReasoningEfforts:[{reasoningEffort:'high'}],defaultReasoningEffort:'high'}];e.AC.state.model=e.AC.nodes.model.value='chosen-model';e.AC.state.effort=e.AC.nodes.effort.value='high';e.AC.state.hostUi={runtime_state:'cold',runtime_health:'stopped',credential_mode:'saved'};
  s.live=true;s.providerId='deepseek-api';s.connectionKind='native_api';s.kind='whiteboard';s.workflow='calculate';s.model='chosen-model';s.effort='high';s.base=e.AC.capture('whiteboard');e.AC.state.selected=s.session_id;e.AC.nodes.input.value='恢复后继续';const order=[];
  e.w.callApi=async(name,...args)=>{order.push(name);if(name==='agent_prepare_runtime')return {code:0,data:{operation_id:'runtime-prepare-cold-followup',purpose:'send',status:'preparing'}};if(name==='agent_wait_runtime_prepare')return {code:0,data:{operation_id:args[0],purpose:'send',status:'ready',cancellation_requested:false,provider_request_admitted:false,timed_out:false}};if(name==='agent_runtime_status')return {code:0,data:{contract:'native-runtime-status-v1',requested_after_seq:0,next_seq:1,gap:false,baseline:{runtime_state:'ready',runtime_health:'healthy',runtime_incarnation:'runtime-followup',failure_code:null,credential_mode:'saved',phase:'preparing_request',operation_id:null,purpose:null,warm_lease_ms_remaining:300000,system_resume_generation:0},events:[],closed:false,system_resume_generation:0}};if(name==='agent_send')return {code:0,data:{session_id:s.session_id,run_id:'followup-run',state:'running',active:true,next_seq:1,events:[{seq:1,type:'message',run_id:'followup-run',message_id:'followup-user',role:'user',message:args[1]}]}};return old(name,...args);};
  await e.AC.send();assert.ok(order.indexOf('agent_prepare_runtime')<order.indexOf('agent_send'));assert.equal(order.includes('agent_start'),false);assert.equal(order.filter(name=>name==='agent_send').length,1);
});
test('cold runtime startup can be cancelled before any provider turn is admitted',async()=>{
  const e=setup(),old=e.w.callApi;e.AC.state.connection='ready';e.AC.state.candidates=[{id:'deepseek-api',kind:'native_api',supported:true}];e.AC.state.cliId=e.AC.state.connectedCliId='deepseek-api';e.AC.state.credential={saved:true};e.AC.state.capabilities={runtime_prepare:{contract:'native-runtime-prepare-v1'}};e.AC.state.models=[{model:'chosen-model',supportedReasoningEfforts:[{reasoningEffort:'high'}],defaultReasoningEffort:'high'}];e.AC.state.model=e.AC.nodes.model.value='chosen-model';e.AC.state.effort=e.AC.nodes.effort.value='high';e.AC.state.hostUi={runtime_state:'cold',runtime_health:'stopped',credential_mode:'saved'};e.AC.nodes.input.value='不要在取消后发送';
  let release;const names=[];e.w.callApi=async(name,...args)=>{names.push(name);if(name==='agent_prepare_runtime')return {code:0,data:{operation_id:'runtime-prepare-cancel-one',purpose:'create',status:'preparing'}};if(name==='agent_wait_runtime_prepare')return new Promise(resolve=>release=resolve);if(name==='agent_cancel_runtime_prepare'){release({code:0,data:{operation_id:args[0],purpose:'create',status:'cancelling',cancellation_requested:true,provider_request_admitted:false,timed_out:false}});return {code:0,data:{operation_id:args[0],purpose:'create',status:'cancelling',cancellation_requested:true,provider_request_admitted:false}};}return old(name,...args);};
  const sending=e.AC.send();await tick();assert.equal(e.AC.state.busy,'runtime-start');await e.AC.stop();await sending;assert.equal(names.includes('agent_cancel_runtime_prepare'),true);assert.equal(names.includes('agent_start'),false);assert.match(e.AC.nodes.notice.textContent,/不会发送模型请求/);
});
test('runtime-start stop fences a concurrently ready operation before provider admission',async()=>{
  const e=setup(),old=e.w.callApi;e.AC.state.connection='ready';e.AC.state.candidates=[{id:'deepseek-api',kind:'native_api',supported:true}];e.AC.state.cliId=e.AC.state.connectedCliId='deepseek-api';e.AC.state.credential={saved:true};e.AC.state.capabilities={runtime_prepare:{contract:'native-runtime-prepare-v1'}};e.AC.state.models=[{model:'chosen-model',supportedReasoningEfforts:[{reasoningEffort:'high'}],defaultReasoningEffort:'high'}];e.AC.state.model=e.AC.nodes.model.value='chosen-model';e.AC.state.effort=e.AC.nodes.effort.value='high';e.AC.state.hostUi={runtime_state:'cold',runtime_health:'stopped',credential_mode:'saved'};e.AC.nodes.input.value='ready竞态也不得发送';
  let release;const names=[];e.w.callApi=async(name,...args)=>{names.push(name);if(name==='agent_prepare_runtime')return {code:0,data:{operation_id:'runtime-prepare-ready-race',purpose:'create',status:'preparing'}};if(name==='agent_wait_runtime_prepare')return new Promise(resolve=>release=resolve);if(name==='agent_cancel_runtime_prepare'){release({code:0,data:{operation_id:args[0],purpose:'create',status:'ready',cancellation_requested:false,provider_request_admitted:false,timed_out:false}});return {code:0,data:{operation_id:args[0],purpose:'create',status:'ready',cancellation_requested:false,provider_request_admitted:false}};}return old(name,...args);};
  const sending=e.AC.send();await tick();assert.equal(e.AC.state.busy,'runtime-start');assert.equal(await e.AC.stop(),true);await sending;assert.equal(names.includes('agent_start'),false);assert.equal(names.includes('agent_send'),false);assert.equal(e.AC.state.busy,null);assert.equal(e.AC.state.runtimePrepare,null);assert.match(e.AC.nodes.notice.textContent,/消息已取消.*不会发送模型请求/);
});
test('runtime-start stop locally fences send even when cancel acknowledgement fails',async()=>{
  const e=setup(),old=e.w.callApi;e.AC.state.connection='ready';e.AC.state.candidates=[{id:'deepseek-api',kind:'native_api',supported:true}];e.AC.state.cliId=e.AC.state.connectedCliId='deepseek-api';e.AC.state.credential={saved:true};e.AC.state.capabilities={runtime_prepare:{contract:'native-runtime-prepare-v1'}};e.AC.state.models=[{model:'chosen-model',supportedReasoningEfforts:[{reasoningEffort:'high'}],defaultReasoningEffort:'high'}];e.AC.state.model=e.AC.nodes.model.value='chosen-model';e.AC.state.effort=e.AC.nodes.effort.value='high';e.AC.state.hostUi={runtime_state:'cold',runtime_health:'stopped',credential_mode:'saved'};e.AC.nodes.input.value='ACK丢失也不得发送';
  let release;const names=[];e.w.callApi=async(name,...args)=>{names.push(name);if(name==='agent_prepare_runtime')return {code:0,data:{operation_id:'runtime-prepare-cancel-lost',purpose:'create',status:'preparing'}};if(name==='agent_wait_runtime_prepare')return new Promise(resolve=>release=resolve);if(name==='agent_cancel_runtime_prepare'){release({code:0,data:{operation_id:args[0],purpose:'create',status:'ready',cancellation_requested:false,provider_request_admitted:false,timed_out:false}});return {code:1,reason:'bridge_timeout'};}return old(name,...args);};
  const sending=e.AC.send();await tick();assert.equal(await e.AC.stop(),false);await sending;assert.equal(names.includes('agent_start'),false);assert.equal(names.includes('agent_send'),false);assert.equal(e.AC.state.busy,null);assert.equal(e.AC.state.runtimePrepare,null);assert.match(e.AC.nodes.notice.textContent,/本地取消.*不会继续发送模型请求/);
});
test('memory-only credential loss keeps cold send blocked and never starts recovery',async()=>{
  const e=setup();e.AC.state.connection='ready';e.AC.state.candidates=[{id:'deepseek-api',kind:'native_api',supported:true}];e.AC.state.cliId=e.AC.state.connectedCliId='deepseek-api';e.AC.state.credential={saved:false,status:'memory_only'};e.AC.state.capabilities={runtime_prepare:{contract:'native-runtime-prepare-v1'}};e.AC.state.models=[{model:'chosen-model',supportedReasoningEfforts:[{reasoningEffort:'high'}],defaultReasoningEffort:'high'}];e.AC.state.model=e.AC.nodes.model.value='chosen-model';e.AC.state.effort=e.AC.nodes.effort.value='high';e.AC.state.hostUi={runtime_state:'cold',runtime_health:'stopped',credential_mode:'required'};e.AC.nodes.input.value='不应发送';e.AC.refresh();assert.equal(e.AC.nodes.send.disabled,true);await e.AC.send();assert.equal(e.calls.some(row=>['agent_prepare_runtime','agent_start','agent_send'].includes(row.name)),false);assert.match(e.AC.nodes.notice.textContent,/内存中的 API Key 已清除/);assert.doesNotMatch(e.AC.nodes.notice.textContent,/runtime|Host|Queue|lineage|revision|cursor/i);
});
test('explicit resume notification invalidates local generation without replaying provider requests',async()=>{
  const e=setup(),old=e.w.callApi;e.AC.state.connection='ready';e.AC.state.candidates=[{id:'deepseek-api',kind:'native_api',supported:true}];e.AC.state.cliId=e.AC.state.connectedCliId='deepseek-api';e.AC.state.capabilities={system_resume_notify:{contract:'native-system-resume-v1'}};const names=[];e.w.callApi=async(name,...args)=>{names.push(name);if(name==='agent_notify_system_resume')return {code:0,data:{system_resume_generation:1,runtime_state:'cold',automatic_provider_retry:false}};if(name==='agent_runtime_status')return {code:0,data:{contract:'native-runtime-status-v1',requested_after_seq:0,next_seq:1,gap:false,baseline:{runtime_state:'cold',runtime_health:'stopped',runtime_incarnation:null,failure_code:null,credential_mode:'saved',phase:'cold',operation_id:null,purpose:null,warm_lease_ms_remaining:null,system_resume_generation:1},events:[],closed:false,system_resume_generation:1}};return old(name,...args);};assert.equal(await e.AC.notifySystemResume(),true);assert.equal(e.AC.state.runtimeStatusGeneration,1);assert.deepEqual(names,['agent_notify_system_resume','agent_runtime_status']);assert.equal(names.some(name=>['agent_start','agent_send'].includes(name)),false);
});
test('late runtime wait from an older resume generation cannot rewind phase or cursor',()=>{
  const e=setup();e.AC.state.runtimeStatusGeneration=2;e.AC.state.runtimeStatusCursor=9;e.AC.state.hostUi={runtime_state:'ready',runtime_health:'healthy',runtime_phase:'ready',runtime_incarnation:'runtime-new'};
  const accepted=e.AC.applyRuntimeStatus({contract:'native-runtime-status-v1',requested_after_seq:4,next_seq:10,gap:false,baseline:null,events:[{seq:10,runtime_state:'reconnecting',runtime_health:'unknown',runtime_incarnation:'runtime-old',failure_code:null,credential_mode:'saved',phase:'resume_detected',operation_id:null,purpose:null,warm_lease_ms_remaining:0,system_resume_generation:1}],closed:false,system_resume_generation:1,subscription_generation:1});
  assert.equal(accepted,false);assert.equal(e.AC.state.runtimeStatusCursor,9);assert.equal(e.AC.state.runtimeStatusGeneration,2);assert.equal(e.AC.state.hostUi.runtime_state,'ready');assert.equal(e.AC.state.hostUi.runtime_incarnation,'runtime-new');
});
test('revalidating the same provider cannot pretend to rebuild a failed runtime',async()=>{
  const e=setup();await e.ready();const s=e.AC.session('same-provider-failed');s.live=true;e.AC.state.selected=s.session_id;e.AC.receive(s,{session_id:s.session_id,provider_id:'codex-one',next_seq:0,events:[],runtime_state:'failed',runtime_health:'unhealthy',failure_code:'pipe_closed'});
  const connects=e.calls.filter(call=>call.name==='agent_connect').length;await e.AC.connect();assert.equal(e.calls.filter(call=>call.name==='agent_connect').length,connects);assert.match(e.AC.nodes.notice.textContent,/不能在同一连接内重建.*重启 App/);assert.equal(e.AC.nodes.send.disabled,true);
});
test('current connection runtime is global and cannot be shadowed by a stale conversation runtime',async()=>{
  const e=setup();await e.ready();const s=e.AC.session('stale-session-runtime');s.live=true;s.ui_state={runtime_state:'ready',runtime_incarnation:'old-runtime'};e.AC.state.selected=s.session_id;
  e.AC.receive(s,{session_id:s.session_id,provider_id:'codex-one',next_seq:0,events:[],provider_state:'verified',runtime_state:'failed',runtime_incarnation:'new-runtime',runtime_health:'unhealthy',failure_code:'pipe_closed'});
  const projection=e.AC.workbenchProjection(s);assert.equal(projection.axes.runtime_state,'failed');assert.equal(projection.runtime_incarnation,'new-runtime');assert.equal(projection.failure_code,'pipe_closed');assert.equal(e.AC.nodes.send.disabled,true);
});
test('a cold or foreign conversation response cannot overwrite current connection facts',async()=>{
  const e=setup();await e.ready();e.AC.state.hostUi={provider_state:'verified',runtime_state:'ready',runtime_incarnation:'current-runtime'};const s=e.AC.session('foreign-runtime');s.live=false;e.AC.state.selected=s.session_id;
  e.AC.receive(s,{session_id:s.session_id,provider_id:'other-provider',connection_state:'disconnected',next_seq:0,events:[],runtime_state:'failed',runtime_incarnation:'foreign-runtime'});
  assert.equal(e.AC.state.connection,'ready');assert.equal(e.AC.state.hostUi.runtime_state,'ready');assert.equal(e.AC.state.hostUi.runtime_incarnation,'current-runtime');assert.equal(Object.hasOwn(s.ui_state,'runtime_state'),false);
});
test('successful provider replacement clears runtime identity omitted by the new provider',async()=>{
  const e=setup(),old=e.w.callApi;await e.ready();e.AC.state.hostUi={runtime_state:'ready',runtime_incarnation:'old-runtime',runtime_health:'healthy'};e.AC.state.candidates.push({id:'other-ready',provider:'Other',supported:true});e.AC.nodes.cli.value='other-ready';
  e.w.callApi=async(name,...args)=>name==='agent_connect'?{code:0,data:{state:'ready',models:[],default:{available:false},connection:{id:'other-ready'}}}:old(name,...args);await e.AC.connect();
  assert.equal(Object.hasOwn(e.AC.state.hostUi,'runtime_incarnation'),false);assert.equal(e.AC.workbenchProjection(null).axes.runtime_state,null);
});
test('a poll started for an old provider generation cannot mutate the replacement connection',async()=>{
  const e=setup(),old=e.w.callApi;await e.ready();const s=e.AC.session('old-provider-poll');s.live=true;s.state='completed';s.active=false;e.AC.state.selected=s.session_id;
  let finishPoll;e.w.callApi=async(name,...args)=>{if(name==='agent_poll')return new Promise(resolve=>finishPoll=resolve);if(name==='agent_connect')return {code:0,data:{state:'ready',models:[],default:{available:false},connection:{id:'other-ready'},runtime_state:'ready',runtime_health:'healthy',runtime_incarnation:'runtime-new'}};return old(name,...args);};
  const polling=e.AC.poll(s,true);await tick();e.AC.state.candidates.push({id:'other-ready',provider:'Other',supported:true});e.AC.nodes.cli.value='other-ready';e.AC.nodes.cli.emit('change');await e.AC.connect();
  finishPoll({code:0,data:{session_id:s.session_id,provider_id:'codex-one',connection_state:'disconnected',runtime_state:'failed',runtime_health:'unhealthy',runtime_incarnation:'runtime-old',next_seq:1,events:[{seq:1,type:'error',error_code:'late_old_provider'}]}});await polling;
  assert.equal(e.AC.state.connectedCliId,'other-ready');assert.equal(e.AC.state.connection,'ready');assert.equal(e.AC.state.hostUi.runtime_incarnation,'runtime-new');assert.equal(e.AC.state.hostUi.runtime_state,'ready');assert.equal(s.cursor,0);assert.equal(s.error,undefined);
});
test('a session remains bound to its creating provider after another provider connects',async()=>{
  const e=setup(),old=e.w.callApi;await e.ready();const s=e.AC.session('provider-bound');s.providerId='codex-one';s.model='chosen-model';s.effort='high';s.kind='whiteboard';s.live=true;s.state='completed';s.base=e.AC.capture('whiteboard');
  e.AC.state.selected=null;e.AC.state.candidates.push({id:'other-ready',provider:'Other',supported:true});e.AC.nodes.cli.value='other-ready';e.AC.nodes.cli.emit('change');e.w.callApi=async(name,...args)=>name==='agent_connect'?{code:0,data:{state:'ready',models:[],default:{available:false},connection:{id:'other-ready'},runtime_state:'ready'}}:old(name,...args);await e.AC.connect();
  e.AC.state.selected=s.session_id;e.AC.refresh();assert.equal(e.AC.workbenchProjection(s).axes.provider_state,'unverified');assert.equal(e.AC.nodes.send.disabled,true);assert.match(e.AC.nodes.contextWarning.textContent,/不是创建该会话的Agent提供方/);
  const sends=e.calls.filter(call=>call.name==='agent_send').length;e.AC.nodes.input.value='不得跨提供方继续';await e.AC.send();assert.equal(e.calls.filter(call=>call.name==='agent_send').length,sends);assert.match(e.AC.nodes.notice.textContent,/切回原提供方/);
  s.live=false;s.canResume=true;e.AC.refresh();assert.equal(e.AC.nodes.resume.disabled,true);const resumes=e.calls.filter(call=>call.name==='agent_resume').length;await e.AC.resumeSession();assert.equal(e.calls.filter(call=>call.name==='agent_resume').length,resumes);assert.match(e.AC.nodes.notice.textContent,/切回原提供方/);
});
test('chat and trajectory share real message and tool facts with turn and search filters',()=>{
  const e=setup(),s=e.AC.session('trajectory-session');s.live=true;e.AC.state.selected=s.session_id;
  e.AC.receive(s,{session_id:s.session_id,run_id:'run-two',state:'running',active:true,next_seq:3,events:[
    {seq:1,run_id:'run-one',type:'message',message_id:'m1',role:'user',message:'建立队伍'},
    {seq:2,run_id:'run-two',type:'tool',tool_id:'t1',name:'读取角色资料',state:'completed',message:'完成'},
    {seq:3,run_id:'run-two',type:'message',message_id:'m2',role:'assistant',message:'正在计算'},
  ]});
  assert.equal(e.AC.nodes.turnRail.hidden,false);assert.equal(e.AC.nodes.turnRail.children.length,3);assert.equal(e.AC.nodes.chatView.attributes['aria-labelledby'],'agent-chat-tab');assert.equal(e.AC.nodes.trajectoryView.attributes['aria-labelledby'],'agent-trajectory-tab');assert.equal(e.AC.nodes.chatView.tabIndex,0);assert.equal(e.AC.nodes.trajectoryView.tabIndex,0);
  e.AC.nodes.trajectoryTab.emit('click');assert.equal(e.AC.nodes.chatView.hidden,true);assert.equal(e.AC.nodes.trajectoryView.hidden,false);assert.equal(e.AC.nodes.trajectoryList.children.length,3);
  e.AC.nodes.search.value='角色';e.AC.nodes.search.emit('input');assert.equal(e.AC.nodes.trajectoryList.children.length,1);assert.match(e.AC.nodes.trajectoryList.textContent,/读取角色资料/);
  e.AC.nodes.chatTab.emit('click');assert.equal(e.AC.nodes.chatView.hidden,false);assert.equal(e.AC.messageNodes.has('m1'),false);assert.equal(e.AC.toolNodes.get('t1').hidden,false);
});
test('conversation filters and selected workbench view are isolated per session',()=>{
  const e=setup(),first=e.AC.session('filter-one'),second=e.AC.session('filter-two');
  e.AC.receive(first,{session_id:first.session_id,next_seq:1,events:[{seq:1,type:'message',message_id:'one',role:'assistant',message:'第一会话'}]});
  e.AC.receive(second,{session_id:second.session_id,next_seq:1,events:[{seq:1,type:'message',message_id:'two',role:'assistant',message:'第二会话'}]});
  e.AC.state.selected=first.session_id;e.AC.refresh();e.AC.nodes.search.value='第一';e.AC.nodes.search.emit('input');e.AC.setWorkbenchView('trajectory');assert.equal(first.workbench.query,'第一');assert.equal(first.workbench.view,'trajectory');
  e.AC.state.selected=second.session_id;e.AC.messageNodes.clear();e.AC.toolNodes.clear();e.AC.nodes.messages.replaceChildren();e.AC.refresh();assert.equal(e.AC.nodes.search.value,'');assert.equal(second.workbench.view,'chat');
  e.AC.setWorkbenchView('trajectory');e.AC.nodes.search.value='第二';e.AC.nodes.search.emit('input');e.AC.state.selected=first.session_id;e.AC.refresh();assert.equal(e.AC.nodes.search.value,'第一');assert.equal(first.workbench.view,'trajectory');
});
test('long conversations materialize a bounded stable message window and expand on demand',()=>{
  const e=setup(),s=e.AC.session('long-conversation');s.live=true;e.AC.state.selected=s.session_id;
  const messages=Array.from({length:350},(_,index)=>({message_id:`message-${index}`,role:index%2?'assistant':'user',message:`消息 ${index}`,run_id:`run-${Math.floor(index/2)}`}));
  const accepted=e.AC.receive(s,{session_id:s.session_id,reset_required:true,snapshot_seq:0,next_seq:0,messages,events:[]});assert.equal(accepted.accepted,true);
  assert.equal(e.AC.messageNodes.size,200);assert.equal(e.AC.nodes.olderMessages.hidden,false);assert.match(e.AC.nodes.olderMessages.textContent,/200\/350/);
  e.AC.nodes.olderMessages.emit('click');assert.equal(e.AC.messageNodes.size,300);assert.match(e.AC.nodes.olderMessages.textContent,/300\/350/);
});
test('integrated session navigation keeps viewed execution and transaction owners separate',()=>{
  const e=setup(),viewed=e.AC.session('session-viewed'),running=e.AC.session('session-running'),owner=e.AC.session('session-owner');
  assert.equal(e.w.attachedContextHistory.node,e.AC.nodes.sessionNavigationHost);assert.equal(e.w.attachedContextHistory.settings.owner,'agent');
  viewed.title='查看会话';running.title='运行会话';owner.title='事务会话';running.active=true;owner.applicationRecoveryPending=true;
  e.AC.state.selected=viewed.session_id;e.AC.state.active=running.session_id;e.AC.state.history=[
    {session_id:viewed.session_id,title:viewed.title,state:'completed',updated_at:'2026-10-24T03:00:00Z',recent_user_input:'检查元素附着',document_binding:{document_id:'other-board',document_revision:4,document_lineage_epoch:2}},
    {session_id:running.session_id,title:running.title,state:'running',active:true,updated_at:'2026-10-24T04:00:00Z'},
    {session_id:owner.session_id,title:owner.title,state:'cold',pending_application:true,updated_at:'2026-10-24T02:00:00Z'},
  ];e.AC.renderSessionNavigation();const rows=Object.values(e.AC.sessionNavigation.getView().groups).flat();
  assert.equal(rows.find(row=>row.id===viewed.session_id).viewed,true);assert.equal(rows.find(row=>row.id===running.session_id).execution_owner,true);
  assert.equal(rows.find(row=>row.id===owner.session_id).transaction_owner,true);assert.equal(rows.find(row=>row.id===viewed.session_id).execution_owner,false);
  assert.equal(rows.find(row=>row.id===viewed.session_id).recent_user_input,'检查元素附着');assert.equal(rows.find(row=>row.id===viewed.session_id).document_detached,true);
  e.AC.state.history[0].recent_user_input='更新后的用户要求';e.AC.renderSessionNavigation();assert.equal(Object.values(e.AC.sessionNavigation.getView().groups).flat().find(row=>row.id===viewed.session_id).recent_user_input,'更新后的用户要求');
});
test('Host turn outline is capability gated and accepts only the strict projection contract',async()=>{
  const e=setup(),session=e.AC.session('session-outline');e.AC.state.selected=session.session_id;
  assert.equal(await e.AC.loadHostOutline(session),false);assert.equal(e.calls.some(call=>call.name==='agent_turn_outline'),false);
  e.AC.state.workbenchCapabilities={turn_outline:true,conversation_search:false,history_window:false};const original=e.w.callApi;
  e.w.callApi=async(name,...args)=>name==='agent_turn_outline'?{code:0,data:{contract:'agent-turn-outline-v1',session_id:session.session_id,projection_revision:1,source_cursor:4,completeness:'complete',partial_reasons:[],items:[{turn_id:'turn-one',start_seq:1,end_seq:4,status:'completed',excerpt:'完成首轮',markers:[],unread:false}]}}:original(name,...args);
  assert.equal(await e.AC.loadHostOutline(session),true);assert.equal(e.AC.turnOutline.getProjection().items[0].turn_id,'turn-one');
  e.w.callApi=async(name,...args)=>name==='agent_turn_outline'?{code:0,data:{contract:'wrong',session_id:session.session_id}}:original(name,...args);
  assert.equal(await e.AC.loadHostOutline(session),false);assert.match(e.AC.turnOutline.notice.textContent,/无法核验/);
});
test('ordinary Chat shows only messages while Inspector retains the ordered Host action index',async()=>{
  const e=setup(),session=e.AC.session('session-unified-timeline'),seen=[];session.live=true;e.AC.state.selected=session.session_id;e.AC.conversationView.activate(session.session_id);
  e.AC.state.workbenchCapabilities={turn_outline:true,conversation_search:true,history_window:true};const original=e.w.callApi;
  e.w.callApi=async(name,...args)=>{seen.push({name,args:clone(args)});if(name==='agent_turn_outline')return {code:0,data:{contract:'agent-turn-outline-v1',session_id:session.session_id,projection_revision:5,source_cursor:4,completeness:'complete',partial_reasons:[],items:[{turn_id:'turn-unified',start_seq:1,end_seq:4,status:'completed',excerpt:'统一时间线',markers:[],unread:false}]}};if(name==='agent_load_turn_window')return {code:0,data:{window:{contract:'agent-history-window-v1',session_id:session.session_id,projection_revision:5,source_cursor:4,completeness:'complete',partial_reasons:[],anchor:{turn_id:'turn-unified',source_seq:1,stable_node_id:null},nodes:[
    {stable_node_id:'message:user-one',turn_id:'turn-unified',source_seq:1,kind:'message',role:'user',label:'用户',text:'开始任务',state:'accepted'},
    {stable_node_id:'tool:read-one',turn_id:'turn-unified',source_seq:2,kind:'tool',role:null,label:'读取数据库',text:'完成',state:'completed'},
    {stable_node_id:'message:assistant-one',turn_id:'turn-unified',source_seq:3,kind:'message',role:'assistant',label:'Agent',text:'已读取',state:'completed'},
    {stable_node_id:'candidate:one',turn_id:'turn-unified',source_seq:4,kind:'candidate',role:null,label:'候选方案',text:'等待审核',state:'ready_for_application'},
  ],has_older:false,has_newer:false,older_cursor:1,newer_cursor:4,total_nodes:4}}};return original(name,...args);};
  assert.equal(await e.AC.loadHostOutline(session),true);await tick();await tick();
  assert.deepEqual({...seen.find(row=>row.name==='agent_load_turn_window').args[1]},{turn_id:'turn-unified',source_seq:1,stable_node_id:null,before_seq:5,limit:500});
  assert.deepEqual(e.AC.conversationView.list.children.map(node=>node.dataset.sourceSeq),['1','3']);
  assert.equal(e.AC.conversationView.snapshot().materialized,4);assert.equal(e.AC.nodes.tools.hidden,true);
  assert.deepEqual(Array.from(e.AC.conversationView.actionIndex(),row=>row.stable_node_id),['candidate:one']);
});
test('Host history window stays read-only and renders a strict partial projection outside the live Store',async()=>{
  const e=setup(),session=e.AC.session('session-window');e.AC.state.selected=session.session_id;
  e.AC.state.workbenchCapabilities={turn_outline:true,conversation_search:true,history_window:true};
  const original=e.w.callApi;e.w.callApi=async(name,...args)=>name==='agent_load_turn_window'?{code:0,data:{window:{contract:'agent-history-window-v1',session_id:session.session_id,projection_revision:2,source_cursor:8,completeness:'partial',partial_reasons:['stable_anchor_not_loaded'],anchor:{turn_id:'turn-old',source_seq:4,stable_node_id:'message:old'},nodes:[],has_older:false,has_newer:false}}}:original(name,...args);
  const before=e.AC.eventStore.snapshot(session.session_id);assert.equal(await e.AC.locateHostTurn({turn_id:'turn-old',source_seq:4,stable_node_id:'message:old'}),true);
  assert.equal(e.AC.nodes.historyWindowHost.hidden,false);assert.equal(e.AC.historyWindowView.snapshot().completeness,'partial');
  assert.equal(e.AC.eventStore.snapshot(session.session_id),before);assert.match(e.AC.nodes.notice.textContent,/不会被猜造/);
});
test('Host telemetry is capability gated, atomically replaces the compatibility trajectory and cannot cross sessions',async()=>{
  const e=setup(),session=e.AC.session('telemetry-session');session.live=true;e.AC.state.selected=session.session_id;e.AC.state.workbenchCapabilities={telemetry_bundle:true};
  const bundle={
    trajectory:{contract:'agent-trajectory-v1',session_id:session.session_id,projection_revision:2,source_cursor:3,completeness:'complete',partial_reasons:[],spans:[{span_id:'turn:one',parent_span_id:null,turn_id:'turn-one',segment_id:null,category:'turn',state:'completed',label:'任务轮次',summary:'完成',result_code:null,resource_ref:null,seq_start:1,seq_end:3,elapsed_ms:800,timing_source:'host',timing_precision:'exact'},{span_id:'engine:one',parent_span_id:'turn:one',turn_id:'turn-one',segment_id:null,workflow_stage:'numeric_closure',category:'engine',state:'failed',label:'伤害结算',summary:'元素附着缺失',result_code:'aura_missing',resource_ref:'engine:event-one',seq_start:2,seq_end:2,elapsed_ms:12000,timing_source:'engine',timing_precision:'exact'}]},
    context:{contract:'agent-context-metrics-v1',session_id:session.session_id,projection_revision:2,source_cursor:3,completeness:'partial',partial_reasons:['context_component_breakdown_unavailable'],metrics:[]},
    performance:{contract:'agent-performance-metrics-v1',session_id:session.session_id,projection_revision:2,source_cursor:3,completeness:'partial',partial_reasons:['provider_phase_timings_unavailable'],metrics:[]},
  };
  const original=e.w.callApi;e.w.callApi=async(name,...args)=>name==='agent_telemetry_bundle'?{code:0,data:clone(bundle)}:original(name,...args);
  assert.equal(await e.AC.loadTelemetry(session),true);assert.equal(e.AC.nodes.telemetryHost.hidden,false);assert.equal(e.AC.nodes.trajectoryList.hidden,true);assert.match(e.AC.nodes.telemetryStatus.textContent,/部分数据未知/);
  e.AC.nodes.telemetryFilter.value='engine';e.AC.nodes.telemetryTurnFilter.value='turn-one';e.AC.nodes.telemetryStageFilter.value='numeric_closure';e.AC.nodes.telemetrySourceFilter.value='engine';e.AC.nodes.telemetryStateFilter.value='failed';e.AC.nodes.telemetryDurationFilter.value='gte10s';e.AC.nodes.telemetryQuery.value='附着';assert.equal(e.AC.applyTelemetryFilters(),true);assert.equal(e.AC.telemetryView.snapshot().filters.query,'附着');
  let resolve;const pending=new Promise(done=>{resolve=done;});e.w.callApi=async(name,...args)=>name==='agent_telemetry_bundle'?pending:original(name,...args);const late=e.AC.loadTelemetry(session);e.AC.state.selected='another-session';e.AC.state.viewEpoch++;resolve({code:0,data:clone(bundle)});assert.equal(await late,false);assert.equal(e.AC.telemetryView.snapshot().trajectory.session_id,session.session_id);
});
test('Host outline history and telemetry pagination requests stay bounded and explicit',async()=>{
  const e=setup(),session=e.AC.session('paged-session');session.live=true;e.AC.state.selected=session.session_id;e.AC.state.workbenchCapabilities={turn_outline:true,history_window:true,telemetry_bundle:true};const seen=[];
  const trajectory={contract:'agent-trajectory-v1',session_id:session.session_id,projection_revision:3,source_cursor:20,completeness:'complete',partial_reasons:[],total_spans:10000,has_older:true,has_newer:false,older_cursor:10,newer_cursor:20,spans:[{span_id:'timeline:one',parent_span_id:null,turn_id:'turn-one',segment_id:null,category:'engine',state:'completed',label:'计算',summary:'完成',result_code:null,resource_ref:null,seq_start:20,seq_end:20,elapsed_ms:0,timing_source:'engine',timing_precision:'exact'}]};
  const metrics=contract=>({contract,session_id:session.session_id,projection_revision:3,source_cursor:20,completeness:'partial',partial_reasons:['unknown'],metrics:[]});
  const old=e.w.callApi;e.w.callApi=async(name,...args)=>{seen.push({name,args:clone(args)});if(name==='agent_turn_outline')return {code:0,data:{contract:'agent-turn-outline-v1',session_id:session.session_id,projection_revision:3,source_cursor:20,completeness:'complete',partial_reasons:[],total_turns:1000,has_older:true,has_newer:false,older_cursor:10,newer_cursor:20,items:[{turn_id:'turn-one',start_seq:10,end_seq:20,status:'completed',excerpt:'完成',markers:[],unread:false}]}};if(name==='agent_load_turn_window')return {code:0,data:{window:{contract:'agent-history-window-v1',session_id:session.session_id,projection_revision:3,source_cursor:20,completeness:'complete',partial_reasons:[],anchor:{turn_id:'turn-one',source_seq:10,stable_node_id:null},nodes:[{stable_node_id:'message:one',turn_id:'turn-one',source_seq:11,kind:'message',role:'user',label:'用户',text:'任务',state:'accepted'}],has_older:false,has_newer:true,older_cursor:11,newer_cursor:11,total_nodes:20}}};if(name==='agent_telemetry_bundle')return {code:0,data:{trajectory:clone(trajectory),context:metrics(e.w.AgentTelemetry.CONTEXT),performance:metrics(e.w.AgentTelemetry.PERFORMANCE)}};return old(name,...args);};
  assert.equal(await e.AC.loadHostOutline(session,{before_seq:10,limit:200}),true);assert.equal(await e.AC.locateHostTurn({turn_id:'turn-one',source_seq:10,stable_node_id:null,after_seq:11,limit:500}),true);assert.equal(await e.AC.loadTelemetry(session,{before_seq:10,limit:500}),true);
  assert.deepEqual(seen.filter(row=>row.name==='agent_turn_outline').at(-1).args,[session.session_id,{before_seq:10,limit:200}]);assert.deepEqual(seen.filter(row=>row.name==='agent_load_turn_window').at(-1).args[1],{turn_id:'turn-one',source_seq:10,stable_node_id:null,after_seq:11,limit:500});assert.deepEqual(seen.filter(row=>row.name==='agent_telemetry_bundle').at(-1).args,[session.session_id,{before_seq:10,limit:500}]);
});
test('Host search load-more merges by stable node id and retains only a bounded window',async()=>{
  const e=setup(),session=e.AC.session('search-pages');e.AC.state.selected=session.session_id;const digest='d'.repeat(64);session.hostSearchRequest=1;session.hostSearchState={query:'反应',digest,request:1,loading:false};let page=0;const seen=[];
  e.w.callApi=async(name,...args)=>{if(name!=='agent_search_conversation')return {code:1,message:'unexpected'};seen.push(clone(args[1]));const start=page===0?0:page===1?199:399,count=200;page++;return {code:0,data:{contract:'agent-search-results-v1',session_id:session.session_id,projection_revision:4,source_cursor:10000,query_digest:digest,completeness:'complete',partial_reasons:[],has_more:page<3,next_cursor:page<3?`dsh-fts-page-${page}`:null,results:Array.from({length:count},(_,index)=>({stable_node_id:`message:${start+index}`,turn_id:`turn-${Math.floor((start+index)/10)}`,source_seq:start+index+1,kind:'message.assistant',excerpt:`反应 ${start+index}`}))}};};
  assert.equal(await e.AC.loadHostSearchPage(session),true);assert.equal(await e.AC.loadHostSearchPage(session,{cursor:'dsh-fts-page-1',append:true}),true);assert.equal(await e.AC.loadHostSearchPage(session,{cursor:'dsh-fts-page-2',append:true}),true);
  const rows=session.hostSearchProjection.results;assert.equal(rows.length,400);assert.equal(new Set(rows.map(row=>row.stable_node_id)).size,400);assert.equal(rows[0].stable_node_id,'message:199');assert.equal(session.hostSearchProjection.has_more,false);
  assert.deepEqual(seen.map(row=>row.cursor??null),[null,'dsh-fts-page-1','dsh-fts-page-2']);assert.equal(seen.some(row=>Object.hasOwn(row,'after_seq')),false);
});
test('reasoning history is metadata-only until the user expands it and requests the exact raw read',async()=>{
  const e=setup(),session=e.AC.session('reasoning-history');session.live=true;e.AC.state.selected=session.session_id;e.AC.state.workbenchCapabilities={history_window:true};e.AC.conversationView.activate(session.session_id);
  e.AC.conversationView.setOutline({session_id:session.session_id,items:[{turn_id:'dsh-turn-1',start_seq:1,end_seq:2}]});const seen=[],reasoning={stable_node_id:'dsh:reasoning-one',turn_id:'dsh-turn-1',source_seq:2,kind:'message',category:'provider',role:'assistant',label:'推理过程',text:'',state:'completed',segment_id:null,operation_id:'reasoning:reasoning-one',result_code:null,resource_ref:null,workflow_stage:null,occurred_at:'2026-10-30T00:00:00Z'};
  e.w.callApi=async(name,...args)=>{if(name!=='agent_load_turn_window')return {code:1,message:'unexpected'};const request=clone(args[1]);seen.push(request);const include=request.include_raw_reasoning===true;return {code:0,data:{window:{contract:'agent-history-window-v1',session_id:session.session_id,projection_revision:2,source_cursor:2,completeness:'complete',partial_reasons:[],anchor:{turn_id:request.turn_id,source_seq:request.source_seq,stable_node_id:request.stable_node_id},nodes:[{...reasoning,text:include?'先核对数据库，再计算。':''}],has_older:false,has_newer:false,older_cursor:null,newer_cursor:null,total_nodes:1}}};};
  assert.equal(await e.AC.loadConversationPage(session,{turn_id:'dsh-turn-1',source_seq:1,stable_node_id:null,before_seq:3,limit:500},{direction:'replace'}),true);assert.equal(seen[0].include_raw_reasoning,undefined);assert.doesNotMatch(e.AC.conversationView.list.textContent,/先核对数据库/);
  const disclosure=e.AC.conversationView.list.querySelector('details');assert.ok(disclosure);disclosure.open=true;disclosure.emit('toggle');for(let attempt=0;attempt<12&&!/先核对数据库/.test(e.AC.conversationView.list.textContent);attempt++)await tick();
  assert.equal(seen.length,2);assert.equal(seen[1].include_raw_reasoning,true);assert.equal(seen[1].stable_node_id,'dsh:reasoning-one');assert.match(e.AC.conversationView.list.textContent,/先核对数据库，再计算/);
});
test('production admission requires the exact requested cursor echo while the explicit test facade may adapt legacy envelopes',()=>{
  const e=setup({agentTest:false}),s=e.AC.session('strict-production');
  const missing=e.AC.receive(s,{session_id:s.session_id,next_seq:0,reset_required:false,events:[]},{production:true,requestedAfterSeq:0});
  assert.equal(missing.accepted,false);assert.equal(missing.reason,'requested_cursor_missing');assert.equal(s.cursor,0);assert.equal(e.AC.eventStore.snapshot(s.session_id),null);
  const mismatched=e.AC.receive(s,{session_id:s.session_id,requested_after_seq:1,next_seq:1,reset_required:false,events:[]},{production:true,requestedAfterSeq:0});
  assert.equal(mismatched.accepted,false);assert.equal(mismatched.reason,'requested_cursor_mismatch');assert.equal(e.AC.eventStore.snapshot(s.session_id),null);
  const accepted=e.AC.receive(s,{session_id:s.session_id,requested_after_seq:0,next_seq:1,reset_required:false,events:[{seq:1,type:'message',message_id:'strict-one',role:'assistant',message:'严格接纳'}]},{production:true,requestedAfterSeq:0});
  assert.equal(accepted.accepted,true);assert.equal(accepted.ack_cursor,1);assert.equal(s.cursor,1);assert.equal(s.messages[0].text,'严格接纳');
});
test('a rejected batch cannot leak candidate actions or application intent into the domain sidecar',()=>{
  const e=setup({agentTest:false}),s=e.AC.session('atomic-sidecar'),oldAction={action_id:'old-action',action:'save_current',status:'queued'},oldCandidate={revision:1,receipt_fingerprint:'old-receipt',ready:true},oldIntent={conversation_id:s.session_id,status:'prepared',intent:{state:'prepared',kind:'whiteboard',revision:1,receipt_fingerprint:'old-receipt'}};
  const seed=e.AC.receive(s,{session_id:s.session_id,requested_after_seq:0,next_seq:1,reset_required:false,candidate:oldCandidate,app_actions:[oldAction],application_intent:oldIntent,events:[{seq:1,type:'status',state:'completed'}]},{production:true,requestedAfterSeq:0});assert.equal(seed.accepted,true);
  const beforeStore=e.AC.eventStore.snapshot(s.session_id),before={candidate:clone(s.candidate),actions:clone(s.appActions),intent:clone(s.applicationIntent),cursor:s.cursor};
  const rejected=e.AC.receive(s,{session_id:s.session_id,requested_after_seq:1,next_seq:2,reset_required:false,candidate:{revision:2,receipt_fingerprint:'new-receipt',ready:true},app_actions:[{action_id:'new-list-action',action:'open_document',status:'queued'}],application_intent:oldIntent,events:[{seq:3,type:'app_action',action:{action_id:'new-event-action',action:'save_as',status:'queued'}}]},{production:true,requestedAfterSeq:1});
  assert.equal(rejected.accepted,false);assert.equal(rejected.reason,'event_sequence_ahead');assert.equal(e.AC.eventStore.snapshot(s.session_id),beforeStore);assert.equal(s.cursor,before.cursor);assert.deepEqual(clone(s.candidate),before.candidate);assert.deepEqual(clone(s.appActions),before.actions);assert.deepEqual(clone(s.applicationIntent),before.intent);
});

test('a malformed deferred App action flag cannot clear the Store or domain sidecar',()=>{
  const e=setup({agentTest:false}),s=e.AC.session('atomic-deferred-sidecar'),oldAction={action_id:'old-action',action:'save_current',status:'queued'};
  const seed=e.AC.receive(s,{session_id:s.session_id,requested_after_seq:0,next_seq:1,reset_required:false,app_actions:[oldAction],events:[{seq:1,type:'status',state:'completed'}]},{production:true,requestedAfterSeq:0});assert.equal(seed.accepted,true);
  const beforeStore=e.AC.eventStore.snapshot(s.session_id),beforeActions=clone(s.appActions),beforeCursor=s.cursor;
  const rejected=e.AC.receive(s,{session_id:s.session_id,requested_after_seq:1,next_seq:2,reset_required:false,app_actions:[],app_actions_deferred:'true',events:[{seq:2,type:'status',state:'completed'}]},{production:true,requestedAfterSeq:1});
  assert.equal(rejected.accepted,false);assert.equal(rejected.reason,'app_actions_deferred_invalid');assert.equal(e.AC.eventStore.snapshot(s.session_id),beforeStore);assert.equal(s.cursor,beforeCursor);assert.deepEqual(clone(s.appActions),beforeActions);
});
test('a malformed application intent cannot release an accepted pending transaction',()=>{
  const e=setup({agentTest:false}),s=e.AC.session('atomic-application-intent'),pending={conversation_id:'atomic-application-intent',status:'prepared',intent:{state:'prepared',kind:'whiteboard',revision:1,receipt_fingerprint:'receipt-one'}};
  const seed=e.AC.receive(s,{session_id:s.session_id,requested_after_seq:0,next_seq:1,reset_required:false,application_intent:pending,events:[{seq:1,type:'status',state:'completed'}]},{production:true,requestedAfterSeq:0});assert.equal(seed.accepted,true);assert.equal(s.applicationRecoveryPending,true);
  const beforeStore=e.AC.eventStore.snapshot(s.session_id),beforeIntent=clone(s.applicationIntent),beforeCursor=s.cursor;
  for(const application_intent of [{status:'typo'},{},{conversation_id:s.session_id,status:'prepared',recovery_required:'true'},{conversation_id:s.session_id,status:'committed',intent:null},{conversation_id:s.session_id,status:'committed',intent:{state:'committed',kind:'whiteboard',revision:1,receipt_fingerprint:'receipt-one'},recovery_required:true}]){
    const rejected=e.AC.receive(s,{session_id:s.session_id,requested_after_seq:1,next_seq:2,reset_required:false,application_intent,events:[{seq:2,type:'status',state:'completed'}]},{production:true,requestedAfterSeq:1});
    assert.equal(rejected.accepted,false);assert.match(rejected.reason,/^application_intent_/);assert.equal(e.AC.eventStore.snapshot(s.session_id),beforeStore);assert.equal(s.cursor,beforeCursor);assert.deepEqual(clone(s.applicationIntent),beforeIntent);assert.equal(s.applicationRecoveryPending,true);
  }
});
test('a terminal application intent must match the exact local pending application identity',()=>{
  const e=setup({agentTest:false}),s=e.AC.session('terminal-application-identity');s.kind='whiteboard';s.base=e.AC.capture('whiteboard');
  const seed=e.AC.receive(s,{session_id:s.session_id,requested_after_seq:0,next_seq:1,reset_required:false,application_intent:{conversation_id:s.session_id,status:'none',intent:null},events:[{seq:1,type:'status',state:'completed'}]},{production:true,requestedAfterSeq:0});assert.equal(seed.accepted,true);
  s.pendingApplicationAck={revision:1,receipt:'receipt-one',base:s.base};s.applicationRecoveryPending=true;
  const beforeStore=e.AC.eventStore.snapshot(s.session_id),beforePending=clone(s.pendingApplicationAck),beforeCursor=s.cursor;
  for(const status of ['committed','aborted']){
    const rejected=e.AC.receive(s,{session_id:s.session_id,requested_after_seq:1,next_seq:2,reset_required:false,application_intent:{conversation_id:s.session_id,status,intent:{state:status,kind:'whiteboard',revision:2,receipt_fingerprint:'receipt-two'}},events:[{seq:2,type:'status',state:'completed'}]},{production:true,requestedAfterSeq:1});
    assert.equal(rejected.accepted,false);assert.equal(rejected.reason,'application_intent_pending_identity_mismatch');assert.equal(e.AC.eventStore.snapshot(s.session_id),beforeStore);assert.equal(s.cursor,beforeCursor);assert.deepEqual(clone(s.pendingApplicationAck),beforePending);assert.equal(s.applicationRecoveryPending,true);
  }
});
test('a discovered Host pending application cannot be released by an unrelated poll transition',()=>{
  const e=setup({agentTest:false}),s=e.AC.session('host-pending-transition');s.kind='whiteboard';
  const prepared={conversation_id:s.session_id,status:'prepared',intent:{state:'prepared',kind:'whiteboard',revision:1,receipt_fingerprint:'receipt-one'}};
  const seed=e.AC.receive(s,{session_id:s.session_id,requested_after_seq:0,next_seq:1,reset_required:false,application_intent:prepared,events:[{seq:1,type:'status',state:'completed'}]},{production:true,requestedAfterSeq:0});assert.equal(seed.accepted,true);assert.equal(s.pendingApplicationAck==null,true);assert.equal(s.applicationRecoveryPending,true);
  const beforeStore=e.AC.eventStore.snapshot(s.session_id),beforeIntent=clone(s.applicationIntent),beforeCursor=s.cursor;
  for(const application_intent of [
    {conversation_id:s.session_id,status:'committed',intent:{state:'committed',kind:'whiteboard',revision:2,receipt_fingerprint:'receipt-two'}},
    {conversation_id:s.session_id,status:'aborted',intent:{state:'aborted',kind:'whiteboard',revision:2,receipt_fingerprint:'receipt-two'}},
    {conversation_id:s.session_id,status:'none',intent:null},
  ]){
    const rejected=e.AC.receive(s,{session_id:s.session_id,requested_after_seq:1,next_seq:2,reset_required:false,application_intent,events:[{seq:2,type:'status',state:'completed'}]},{production:true,requestedAfterSeq:1});
    assert.equal(rejected.accepted,false);assert.match(rejected.reason,/^application_intent_(recovery_identity_mismatch|pending_transition_unverified)$/);assert.equal(e.AC.eventStore.snapshot(s.session_id),beforeStore);assert.equal(s.cursor,beforeCursor);assert.deepEqual(clone(s.applicationIntent),beforeIntent);assert.equal(s.applicationRecoveryPending,true);
  }
});
test('cold pending application settlement requires an explicit reviewed abort command',()=>{
  const e=setup({agentTest:false}),s=e.AC.session('cold-pending-transition');s.kind='whiteboard';
  const cold={conversation_id:s.session_id,status:'prepared',intent:null,recovery_required:true};
  assert.equal(e.AC.receive(s,{session_id:s.session_id,requested_after_seq:0,next_seq:1,reset_required:false,application_intent:cold,events:[{seq:1,type:'status'}]},{production:true,requestedAfterSeq:0}).accepted,true);
  const aborted={conversation_id:s.session_id,status:'aborted',intent:{state:'aborted',kind:'whiteboard',revision:1,receipt_fingerprint:'receipt-one'}};
  const ordinary=e.AC.receive(s,{session_id:s.session_id,requested_after_seq:1,next_seq:2,reset_required:false,application_intent:aborted,events:[{seq:2,type:'status'}]},{production:true,requestedAfterSeq:1});assert.equal(ordinary.accepted,false);assert.equal(s.applicationRecoveryPending,true);assert.equal(s.cursor,1);
  const reviewed=e.AC.receiveCommand(s,{session_id:s.session_id,requested_after_seq:1,next_seq:2,reset_required:false,application_intent:aborted,events:[{seq:2,type:'status'}]},1,{applicationSettlement:'abort'});assert.equal(reviewed.accepted,true);assert.equal(s.applicationRecoveryPending,false);assert.equal(s.cursor,2);
});
test('reset baseline restores idempotent tool usage and budget facts without duplicating messages',()=>{
  const e=setup(),s=e.AC.session('reset-events');s.live=true;e.AC.state.selected=s.session_id;
  e.AC.receive(s,{session_id:s.session_id,provider_id:'codex-one',reset_required:true,snapshot_seq:5,next_seq:5,run_id:'run-reset',state:'running',active:true,
    messages:[{message_id:'baseline-message',run_id:'run-reset',role:'user',message:'基线消息'}],events:[
      {seq:1,run_id:'run-reset',type:'message',message_id:'baseline-message',role:'user',message:'基线消息'},
      {seq:2,run_id:'run-reset',type:'tool',tool_id:'tool-reset',name:'读取资料',state:'running'},
      {seq:3,run_id:'run-reset',type:'tool',tool_id:'tool-reset',state:'completed',message:'完成'},
      {seq:4,run_id:'run-reset',type:'usage',usage:{input_tokens:10,output_tokens:5,total_tokens:15}},
      {seq:5,run_id:'run-reset',type:'budget_status',status:'budget_progress_70',message:'已使用70%'}]});
  assert.equal(s.messages.length,1);assert.equal(s.messages[0].text,'基线消息');assert.equal(s.tools.length,1);assert.equal(s.tools[0].name,'读取资料');assert.equal(s.tools[0].state,'completed');assert.equal(s.usage.total_tokens,15);assert.equal(s.budgetStatuses.budget_progress_70.message,'已使用70%');assert.equal(s.cursor,5);
});
test('malformed reset or event cursors are rejected before mutating legacy session facts',()=>{
  const e=setup(),s=e.AC.session('malformed-cursor');s.live=true;e.AC.state.selected=s.session_id;e.AC.receive(s,{session_id:s.session_id,provider_id:'codex-one',next_seq:1,events:[{seq:1,type:'message',message_id:'kept',role:'assistant',message:'保留'}],runtime_state:'ready'});
  const before={cursor:s.cursor,messages:clone(s.messages),hostUi:clone(e.AC.state.hostUi),revision:s.projectionRevision};
  const invalid=[
    {session_id:s.session_id,provider_id:'codex-one',reset_required:true,snapshot_seq:4,next_seq:3,messages:[],events:[],runtime_state:'failed'},
    {session_id:s.session_id,provider_id:'codex-one',next_seq:2,events:[{seq:3,type:'error',error_code:'ahead'}],runtime_state:'failed'},
    {session_id:s.session_id,reset_required:true,snapshot_seq:'4',next_seq:4,events:[]},
    {session_id:s.session_id,next_seq:2,events:[null]},
    {session_id:s.session_id,next_seq:2,events:[{seq:2}]},
    {session_id:s.session_id,next_seq:2,events:[],messages:{}},
    {session_id:s.session_id,next_seq:3,events:[{seq:3,type:'status'},{seq:2,type:'status'}]},
  ];
  for(const envelope of invalid)assert.doesNotThrow(()=>e.AC.receive(s,envelope));
  assert.equal(s.cursor,before.cursor);assert.deepEqual(clone(s.messages),before.messages);assert.deepEqual(clone(e.AC.state.hostUi),before.hostUi);assert.equal(s.projectionRevision,before.revision);assert.equal(s.error,undefined);
});
test('malformed poll envelopes surface a stable protocol error and enter exponential backoff',async()=>{
  const e=setup(),s=e.AC.session('malformed-poll');s.live=true;e.AC.state.selected=s.session_id;e.AC.receive(s,{session_id:s.session_id,next_seq:1,state:'running',active:true,events:[{seq:1,type:'message',message_id:'kept',role:'assistant',message:'保留'}]});
  const before={cursor:s.cursor,messages:clone(s.messages),revision:s.projectionRevision};let attempt=0,old=e.w.callApi;e.w.callApi=async(name,...args)=>name==='agent_poll'?(++attempt===1?{code:0,data:{session_id:s.session_id,reset_required:true,snapshot_seq:4,next_seq:3,events:[]}}:attempt===2?{code:0,data:{session_id:s.session_id,next_seq:2,events:[{seq:3,type:'status',state:'completed'}]}}:attempt===3?{code:0,data:{session_id:'wrong-session',next_seq:2,events:[]}}:{code:0,data:{session_id:s.session_id,next_seq:2,state:'error',active:false,events:[{seq:2,type:'error',message:'新业务错误',reason:'native_turn_error'}]}}):old(name,...args);
  await e.AC.poll(s,true);assert.equal(s.pollErrors,1);assert.equal(e.scheduledDelays.at(-1),3000);assert.match(s.error,/App 返回的任务状态无法核验/);assert.equal(e.AC.errorDiagnosticFor(s.error).code,'native_agent_protocol_snapshot_seq_ahead');
  await e.AC.poll(s,true);assert.equal(s.pollErrors,2);assert.equal(e.scheduledDelays.at(-1),6000);assert.match(s.error,/App 返回的任务状态无法核验/);assert.equal(e.AC.errorDiagnosticFor(s.error).code,'native_agent_protocol_event_sequence_ahead');
  assert.equal(s.cursor,before.cursor);assert.deepEqual(clone(s.messages),before.messages);assert.equal(s.projectionRevision,before.revision);assert.equal(s.active,true);
  await e.AC.poll(s,true);assert.equal(s.pollErrors,3);assert.equal(e.scheduledDelays.at(-1),12000);assert.match(s.error,/App 返回的任务状态无法核验/);assert.equal(e.AC.errorDiagnosticFor(s.error).code,'native_agent_protocol_session_id_mismatch');assert.equal(s.cursor,before.cursor);assert.doesNotMatch(s.error,/native_agent_protocol|Host|runtime|Queue|lineage|revision|cursor/i);
  await e.AC.poll(s,true);assert.equal(s.pollErrors,0);assert.equal(s.pollFailureActive,false);assert.match(s.error,/本轮原生Agent运行失败/);assert.equal(e.AC.errorDiagnosticFor(s.error).code,'native_turn_error');assert.doesNotMatch(s.error,/native_agent_protocol/);
});
test('native API sessions prefer long-poll and keep a sticky legacy fallback when wait is unsupported',async()=>{
  const e=setup(),old=e.w.callApi,methods=[];const s=e.AC.session('native-wait-fallback');s.live=true;s.active=true;s.state='running';s.connectionKind='native_api';e.AC.state.selected=s.session_id;e.AC.state.active=s.session_id;
  let polls=0;e.w.callApi=async(name,...args)=>{if(name==='agent_wait_events'){methods.push({name,args:clone(args)});return {code:1,reason:'agent_wait_events_unsupported'};}if(name==='agent_poll'){methods.push({name,args:clone(args)});polls++;return {code:0,data:{session_id:s.session_id,next_seq:polls,events:[{seq:polls,type:'status',state:'running'}],state:'running',active:true}};}return old(name,...args);};
  await e.AC.poll(s,true);await e.AC.poll(s,true);
  assert.deepEqual(methods.map(row=>row.name),['agent_wait_events','agent_poll','agent_poll']);assert.deepEqual(methods[0].args,[s.session_id,0,1,12000]);assert.deepEqual(methods[2].args,[s.session_id,1]);assert.equal(s.transportFallback,true);assert.equal(s.cursor,2);
});
test('an accepted command envelope supersedes a pending wait in the integrated controller',async()=>{
  const e=setup(),old=e.w.callApi,waitCalls=[];const s=e.AC.session('native-command-cursor');s.live=true;s.active=true;s.state='running';s.connectionKind='native_api';e.AC.state.selected=s.session_id;e.AC.state.active=s.session_id;
  let finishFirst,attempt=0;e.w.callApi=async(name,...args)=>{if(name==='agent_wait_events'){waitCalls.push(clone(args));attempt++;if(attempt===1)return new Promise(resolve=>finishFirst=resolve);return {code:0,data:{session_id:s.session_id,next_seq:6,events:[{seq:6,type:'status',state:'completed'}],state:'completed',active:false,subscription_generation:2,transport_mode:'wait'}};}return old(name,...args);};
  const waiting=e.AC.poll(s,true);await tick();
  assert.equal(e.AC.receive(s,{session_id:s.session_id,next_seq:5,events:statusEvents(1,5),state:'running',active:true}).accepted,true);assert.equal(s.cursor,5);
  finishFirst({code:0,data:{session_id:s.session_id,next_seq:2,events:[{seq:2,type:'message',message_id:'late',message:'不得重放'}],state:'running',active:true,subscription_generation:1,transport_mode:'wait'}});assert.equal((await waiting).status,'stale');assert.equal(s.cursor,5);assert.equal(s.messages.some(row=>row.id==='late'),false);
  await e.AC.poll(s,true);assert.deepEqual(waitCalls,[ [s.session_id,0,1,12000], [s.session_id,5,2,12000] ]);assert.equal(s.cursor,6);
});
test('stop invalidates a pending native wait before accepting the stop response',async()=>{
  const e=setup(),old=e.w.callApi;const s=e.AC.session('native-stop-wait');s.live=true;s.active=true;s.state='running';s.connectionKind='native_api';e.AC.state.selected=s.session_id;e.AC.state.active=s.session_id;
  let finishWait;e.w.callApi=async(name,...args)=>name==='agent_wait_events'?new Promise(resolve=>finishWait=resolve):name==='agent_stop'?{code:0,data:{session_id:s.session_id,next_seq:3,events:statusEvents(1,3,'stopped'),state:'stopped',active:false}}:old(name,...args);
  const waiting=e.AC.poll(s,true);await tick();assert.equal(await e.AC.stop(s),true);assert.equal(s.cursor,3);assert.equal(s.active,false);
  finishWait({code:0,data:{session_id:s.session_id,next_seq:1,events:[{seq:1,type:'message',message_id:'late-stop',message:'不得重放'}],state:'running',active:true,subscription_generation:1}});assert.equal((await waiting).status,'stale');assert.equal(s.cursor,3);assert.equal(s.messages.some(row=>row.id==='late-stop'),false);
});
test('a malformed start response is reported as a protocol error instead of task progress',async()=>{
  const e=setup(),old=e.w.callApi;await e.ready();e.w.callApi=async(name,...args)=>name==='agent_start'?{code:0,data:{session_id:'bad-start',state:'running',active:true,reset_required:true,snapshot_seq:2,next_seq:1,events:[]}}:old(name,...args);
  e.AC.nodes.input.value='启动测试';await e.AC.send();const s=e.AC.selected();assert.equal(s.session_id,'bad-start');assert.equal(s.active,false);assert.equal(s.pollErrors,1);assert.equal(s.pollFailureActive,true);assert.match(s.error,/App 返回的任务状态无法核验/);assert.equal(e.AC.errorDiagnosticFor(s.error).code,'native_agent_protocol_snapshot_seq_ahead');assert.match(e.AC.nodes.notice.textContent,/任务状态无法核验/);assert.doesNotMatch(e.AC.nodes.notice.textContent,/Host|runtime|Queue|lineage|revision|cursor|正在分阶段完善/i);
});
test('settings and conversation panes have independent persisted visibility controls',()=>{
  const key='genshin.agent.workbench.shell.v1',e=setup({storage:{[key]:JSON.stringify({version:1,preferences:{width:540,settingsVisible:false,detailsVisible:false,chatVisible:true,viewMode:'docked'}})}});
  assert.equal(e.AC.nodes.controls.hidden,true);assert.equal(e.AC.nodes.chatPane.hidden,false);assert.equal(e.AC.nodes.settingsToggle.attributes['aria-pressed'],'false');assert.equal(e.AC.nodes.chatToggle.attributes['aria-pressed'],'true');assert.equal(e.AC.nodes.settingsToggle.attributes['aria-controls'],'agent-settings-pane');assert.equal(e.AC.nodes.chatToggle.attributes['aria-controls'],'agent-conversation-pane');
  e.AC.nodes.settingsToggle.emit('click');assert.equal(e.AC.nodes.controls.hidden,false);assert.equal(e.AC.nodes.chatPane.hidden,false);
  let focusReturned=false;e.w.document.activeElement=e.AC.nodes.input;e.AC.nodes.chatPane.contains=node=>node===e.AC.nodes.input;e.AC.nodes.chatToggle.focus=()=>focusReturned=true;
  e.AC.nodes.chatToggle.emit('click');assert.equal(e.AC.nodes.controls.hidden,false);assert.equal(e.AC.nodes.chatPane.hidden,true);assert.equal(focusReturned,true);
  e.AC.nodes.settingsToggle.emit('click');assert.equal(e.AC.nodes.controls.hidden,true);assert.equal(e.AC.nodes.chatPane.hidden,true);assert.equal(e.body.classList.contains('agent-chat-content-hidden'),true);
  e.AC.nodes.chatToggle.emit('click');assert.equal(e.AC.nodes.controls.hidden,true);assert.equal(e.AC.nodes.chatPane.hidden,false);assert.equal(e.body.classList.contains('agent-chat-content-hidden'),false);
  assert.deepEqual(JSON.parse(e.stored.get(key)),{version:1,preferences:{width:540,inspectorWidth:340,inspectorVisible:true,settingsVisible:false,detailsVisible:false,chatVisible:true,viewMode:'docked'}});
});
test('activity feedback preferences are locally persisted and applied to the workbench',()=>{
  const key='genshin.agent.workbench.feedback.v1',e=setup({storage:{[key]:JSON.stringify({details:'full',motion:'reduce',notifications:'important'})}});
  assert.equal(e.root.dataset.detailLevel,'full');assert.equal(e.root.dataset.motion,'reduce');assert.equal(e.AC.nodes.feedbackDetails.value,'full');assert.equal(e.AC.nodes.feedbackNotifications.value,'important');
  e.AC.nodes.feedbackDetails.value='compact';e.AC.nodes.feedbackDetails.emit('change');e.AC.nodes.feedbackMotion.value='system';e.AC.nodes.feedbackMotion.emit('change');e.AC.nodes.feedbackNotifications.value='off';e.AC.nodes.feedbackNotifications.emit('change');
  assert.deepEqual(JSON.parse(e.stored.get(key)),{details:'compact',motion:'system',notifications:'off'});assert.equal(e.root.dataset.detailLevel,'compact');assert.equal(e.root.dataset.motion,'system');
});
test('integrated activity feedback delays animation and renders cursor phase elapsed and reconnect success',()=>{
  const e=setup(),s=e.AC.session('activity-session');s.live=true;e.AC.state.selected=s.session_id;
  e.AC.receive(s,{session_id:s.session_id,next_seq:1,state:'running',active:true,events:[{seq:1,type:'status',state:'running'}]});assert.equal(e.AC.nodes.activityFeedbackHost.hidden,true);assert.equal(e.root.dataset.activityVisible,'false');
  const delayed=[...e.timers.values()].at(-1);delayed();assert.equal(e.AC.nodes.activityFeedbackHost.hidden,false);assert.equal(e.root.dataset.activityVisible,'true');assert.match(e.AC.nodes.activityFeedbackHost.textContent,/本轮已用/);assert.match(e.AC.nodes.activityFeedbackHost.textContent,/同步位置 #1/);
  e.AC.receive(s,{session_id:s.session_id,next_seq:2,state:'running',active:true,events:[{seq:2,type:'tool',tool_id:'tool-one',name:'gs_calculate',state:'running',message:'计算中'}]});assert.match(e.AC.nodes.activityFeedbackHost.textContent,/gs_calculate/);assert.match(e.AC.nodes.activityFeedbackHost.textContent,/同步位置 #2/);assert.doesNotMatch(e.AC.nodes.activityFeedbackHost.textContent,/流位置|cursor/i);
  s.pollFailureActive=true;e.AC.refresh();assert.match(e.AC.nodes.activityFeedbackHost.textContent,/重新连接/);assert.match(e.AC.nodes.activityFeedbackHost.textContent,/最近成功/);assert.doesNotMatch(e.AC.nodes.activityFeedbackHost.textContent,/%/);
});
test('background terminal transitions create App-only notifications and unread badge counts Host truth',()=>{
  const e=setup(),viewed=e.AC.session('viewed-session'),background=e.AC.session('background-session');viewed.live=true;background.live=true;e.AC.state.selected=viewed.session_id;
  e.AC.receive(background,{session_id:background.session_id,next_seq:1,state:'running',active:true,title:'后台任务',events:[{seq:1,type:'status',state:'running'}]});
  e.AC.receive(background,{session_id:background.session_id,next_seq:2,state:'completed',active:false,title:'后台任务',events:[{seq:2,type:'turn.completed'}]});assert.equal(e.AC.nodes.toastHost.children.length,1);assert.match(e.AC.nodes.toastHost.textContent,/任务已完成/);
  e.AC.state.history=[{session_id:background.session_id,title:'后台任务',state:'completed',unread:true}];e.AC.refresh();assert.match(e.badge.textContent,/1 未读/);
});
test('agent sidebar resizes by pointer and keyboard, clamps to the viewport and persists safely',()=>{
  const key='genshin.agent.workbench.shell.v1',e=setup({innerWidth:1440});let timelinePaint=0,boardPaint=0;e.w.TV={requestPaint:()=>timelinePaint++};e.w.WB.draw=()=>boardPaint++;assert.equal(e.body.style['--agent-chat-width'],'400px');
  e.AC.nodes.resizer.emit('pointerdown',{button:0,clientX:600,pointerId:7});e.emitWindow('pointermove',{clientX:500,pointerId:7});assert.equal(e.AC.state.panel.width,500);assert.equal(e.body.classList.contains('agent-chat-resizing'),true);
  e.emitWindow('pointerup',{clientX:500,pointerId:7});assert.equal(e.body.classList.contains('agent-chat-resizing'),false);assert.equal(JSON.parse(e.stored.get(key)).preferences.width,500);
  e.AC.nodes.resizer.emit('keydown',{key:'ArrowLeft'});assert.equal(e.AC.state.panel.width,516);e.AC.nodes.resizer.emit('keydown',{key:'End'});assert.equal(e.AC.state.panel.width,880);
  e.w.innerWidth=500;e.emitWindow('resize');assert.equal(e.AC.state.panel.width,476);assert.equal(e.AC.nodes.resizer.attributes['aria-valuemax'],'476');assert.equal(e.body.style['--agent-chat-width'],'476px');
  e.AC.resizePanel(10);assert.equal(e.AC.state.panel.width,320);assert.equal(e.AC.nodes.resizer.attributes['aria-valuenow'],'320');assert.ok(timelinePaint>0);assert.ok(boardPaint>0);
});
test('corrupt persisted panel state falls back while a valid all-hidden state remains recoverable',()=>{
  const key='genshin.agent.workbench.shell.v1',bad=setup({storage:{[key]:'not-json'}});assert.equal(bad.AC.state.panel.width,400);assert.equal(bad.AC.nodes.controls.hidden,true);assert.equal(bad.AC.nodes.chatPane.hidden,false);
  const empty=setup({storage:{[key]:JSON.stringify({version:1,preferences:{width:9999,settingsVisible:false,detailsVisible:false,chatVisible:false,viewMode:'docked'}})}});assert.equal(empty.AC.state.panel.width,880);assert.equal(empty.AC.nodes.controls.hidden,true);assert.equal(empty.AC.nodes.chatPane.hidden,true);assert.equal(empty.body.classList.contains('agent-chat-content-hidden'),true);
  empty.AC.nodes.chatToggle.emit('click');assert.equal(empty.AC.nodes.chatPane.hidden,false);
});
test('workbench shell keeps task details in the Inspector and the composer in chat',()=>{
  const e=setup();assert.equal(e.AC.nodes.controls.hidden,true);assert.equal(e.AC.nodes.detailsPane.hidden,true);assert.equal(e.AC.nodes.chatPane.hidden,false);assert.equal(e.AC.nodes.inspectorToggle.hidden,true);assert.equal(e.AC.nodes.detailsToggle.hidden,true);assert.equal(e.AC.nodes.composerRoot.parent,e.AC.nodes.chatPane);assert.equal(e.AC.nodes.model.parent.parent,e.AC.nodes.composerOptions.children[1]);assert.equal(e.AC.nodes.composerOptions.open,undefined);
  e.AC.nodes.modeToggle.emit('click');assert.equal(e.AC.state.panel.viewMode,'page');assert.equal(e.AC.nodes.inspectorToggle.hidden,false);assert.equal(e.AC.nodes.detailsToggle.hidden,false);
  e.AC.nodes.detailsToggle.emit('click');assert.equal(e.AC.nodes.detailsPane.hidden,true);assert.equal(e.AC.nodes.inspectorPane.dataset.view,'details');assert.equal(e.AC.nodes.inspectorDetails.hidden,false);assert.equal(e.AC.nodes.controls.hidden,true);e.AC.nodes.settingsToggle.emit('click');assert.equal(e.AC.nodes.controls.hidden,false);assert.equal(e.AC.nodes.detailsPane.hidden,true);
  assert.equal(e.root.classList.contains('ac-page-mode'),true);assert.equal(e.root.parent,e.pageHost);
  assert.ok(e.AC.nodes.composerContext.children.length>=3);assert.equal(e.AC.nodes.send.hidden,false);assert.equal(e.AC.nodes.stop.hidden,true);
});
test('full page and resizable dock move one workbench root without losing draft scroll or active session',()=>{
  const e=setup();e.tab('rotation');e.pageTab.emit('click');assert.equal(e.w.WS.tab(),'agent');assert.equal(e.body.dataset.agentReturnTab,'rotation');assert.equal(e.AC.currentKind(),'native');assert.equal(e.AC.nodes.kind.value,'native');assert.equal(e.root.parent,e.pageHost);assert.equal(e.AC.state.panel.viewMode,'page');
  const session=e.AC.session('kept-session');e.AC.state.selected=session.session_id;e.AC.nodes.input.value='不要丢失的草稿';e.AC.nodes.messages.scrollTop=137;
  e.badge.emit('click');assert.equal(e.w.WS.tab(),'rotation');assert.equal(e.root.parent,e.body);assert.equal(e.AC.state.panel.viewMode,'docked');assert.equal(e.AC.nodes.input.value,'不要丢失的草稿');assert.equal(e.AC.nodes.messages.scrollTop,137);assert.equal(e.AC.state.selected,'kept-session');
  e.AC.state.selected=null;e.tab('whiteboard');e.pageTab.emit('click');assert.equal(e.body.dataset.agentReturnTab,'whiteboard');assert.equal(e.AC.currentKind(),'whiteboard');assert.equal(e.AC.nodes.kind.value,'whiteboard');e.AC.close();assert.equal(e.w.WS.tab(),'whiteboard');assert.equal(e.root.hidden,true);
});
test('full-page Escape and public openPage keep workspace navigation synchronized with the single Agent root',()=>{
  const e=setup();e.tab('rotation');e.pageTab.emit('click');assert.equal(e.w.WS.tab(),'agent');assert.equal(e.root.parent,e.pageHost);assert.equal(e.AC.state.panel.viewMode,'page');
  e.emitWindow('keydown',{key:'Escape'});assert.equal(e.w.WS.tab(),'rotation');assert.equal(e.root.parent,e.body);assert.equal(e.AC.state.panel.viewMode,'docked');assert.equal(e.root.hidden,false);
  e.tab('whiteboard');assert.equal(e.w.AgentWorkbenchApp.openPage(),true);assert.equal(e.w.WS.tab(),'agent');assert.equal(e.body.dataset.agentReturnTab,'whiteboard');assert.equal(e.root.parent,e.pageHost);assert.equal(e.AC.state.panel.viewMode,'page');assert.equal(e.root.hidden,false);
});
test('integrated composer restores per-session drafts and persists no workbench business state',()=>{
  const e=setup(),key='genshin.agent.composer.v1';e.AC.nodes.input.value='新任务草稿';e.AC.nodes.input.emit('input');e.AC.composer.switchContext('session:first');e.AC.nodes.input.value='会话一草稿';e.AC.nodes.input.emit('input');e.AC.composer.switchContext('new:whiteboard');assert.equal(e.AC.nodes.input.value,'新任务草稿');
  const saved=JSON.parse(e.stored.get(key));assert.equal(saved.version,1);assert.deepEqual(Object.keys(saved).sort(),['drafts','preference','version']);assert.doesNotMatch(JSON.stringify(saved),/runtime_state|candidate|inbox|calculated|result_path|api_key/i);
});
test('turn budget settings render maintained defaults and save only sparse user overrides for the next turn',async()=>{
  const e=setup();e.AC.open();await tick();assert.equal(e.AC.state.budget.follows_defaults,true);assert.match(e.AC.nodes.budgetSummary.textContent,/跟随动态默认/);
  const input=e.AC.nodes.budgetInputs.wall_clock_seconds;assert.equal(input.value,7200);assert.match(e.AC.nodes.budgetBody.textContent,/单轮最长活跃工作时间/);assert.doesNotMatch(e.AC.nodes.budgetBody.textContent,/27分钟/);input.value='10800';input.emit('change');await e.AC.saveBudget();
  const call=e.calls.find(row=>row.name==='agent_update_budget_settings');assert.deepEqual(call.args,[{wall_clock_seconds:10800},0]);assert.equal(e.AC.state.budget.effective.wall_clock_seconds,10800);assert.equal(e.AC.state.budget.follows_defaults,false);assert.match(e.AC.nodes.notice.textContent,/下一次用户消息/);
});
test('turn budget settings reject out-of-range values locally and reset to future maintained defaults',async()=>{
  const e=setup();await e.AC.loadBudget();const input=e.AC.nodes.budgetInputs.wall_clock_seconds;input.value='90000';input.emit('change');await e.AC.saveBudget();assert.equal(e.calls.some(row=>row.name==='agent_update_budget_settings'),false);assert.match(e.AC.nodes.notice.textContent,/之间的整数/);
  input.value='10800';input.emit('change');await e.AC.saveBudget();await e.AC.resetBudget();const reset=e.calls.find(row=>row.name==='agent_reset_budget_settings');assert.deepEqual(reset.args,[1]);assert.equal(e.AC.state.budget.follows_defaults,true);assert.match(e.AC.nodes.notice.textContent,/跟随动态默认预算/);
});
test('turn budget conflict preserves the draft and keeps raw provider wording in technical details',async()=>{
  const e=setup(),old=e.w.callApi;e.w.callApi=async(name,...args)=>name==='agent_update_budget_settings'?{code:1,reason:'native_turn_budget_generation_conflict',message:'预算配置已被其他窗口修改，请重新载入'}:old(name,...args);
  await e.AC.loadBudget();e.AC.nodes.budgetInputs.wall_clock_seconds.value='10800';e.AC.nodes.budgetInputs.wall_clock_seconds.emit('change');await e.AC.saveBudget();assert.equal(e.AC.state.budgetDraft.wall_clock_seconds,10800);assert.match(e.AC.nodes.notice.textContent,/刷新后重试/);assert.doesNotMatch(e.AC.nodes.notice.textContent,/native_turn_budget/);assert.match(e.AC.nodes.technicalDetailsBody.textContent,/重新载入/);
});
test('connection selects only an available backend default and lists its complete reasoning catalog',async()=>{
  const e=setup();await e.AC.detect();await e.AC.connect();assert.equal(e.calls.find(c=>c.name==='agent_connect').args[0],'codex-one');assert.equal(e.AC.nodes.model.value,'chosen-model');assert.equal(e.AC.nodes.effort.value,'high');assert.equal(e.AC.nodes.settings.open,true);
  assert.deepEqual(e.AC.nodes.model.children.map(node=>node.value),['','chosen-model','other-model']);assert.deepEqual(e.AC.nodes.effort.children.map(node=>node.value),['','off','low','high','max']);assert.equal(e.AC.nodes.effort.children[1].textContent,'关闭推理');assert.equal(e.AC.nodes.connect.disabled,true);
  const connects=e.calls.filter(c=>c.name==='agent_connect').length;await e.AC.connect();assert.equal(e.calls.filter(c=>c.name==='agent_connect').length,connects);assert.match(e.AC.nodes.notice.textContent,/已经就绪/);
});
test('connection accepts a default model without reasoning tiers and sends an explicit null effort',async()=>{
  const e=setup(),old=e.w.callApi;e.w.callApi=async(name,...args)=>name==='agent_connect'?{code:0,data:{state:'ready',models:[{model:'basic-model',displayName:'基础模型',supportedReasoningEfforts:[],defaultReasoningEffort:null}],default:{model:'basic-model',reasoningEffort:null,available:true,reason:null},account:{type:'api_key'}}}:old(name,...args);
  await e.AC.detect();await e.AC.connect();assert.equal(e.AC.nodes.model.value,'basic-model');assert.equal(e.AC.state.effort,null);assert.equal(e.AC.nodes.effort.value,'');assert.equal(e.AC.nodes.effort.disabled,true);assert.deepEqual(e.AC.nodes.effort.children.map(node=>node.textContent),['该模型无推理档位']);
  e.AC.nodes.input.value='使用无推理档位模型';await e.AC.send();const sent=e.calls.find(c=>c.name==='agent_start').args[0];assert.equal(sent.model,'basic-model');assert.equal(sent.effort,null);
});
test('an unavailable or malformed backend default is not silently replaced by the first model',async()=>{
  const e=setup(),old=e.w.callApi;e.w.callApi=async(name,...args)=>name==='agent_connect'?{code:0,data:{state:'ready',models:[{model:'only-model',supportedReasoningEfforts:[{reasoningEffort:'high'}],defaultReasoningEffort:'high'}],default:{model:'missing-model',reasoningEffort:'high',available:false,reason:'configured_default_unavailable'},account:{type:'chatgpt'}}}:old(name,...args);
  await e.AC.detect();await e.AC.connect();assert.equal(e.AC.nodes.model.value,'');assert.equal(e.AC.nodes.effort.value,'');e.AC.nodes.input.value='测试';await e.AC.send();assert.ok(e.calls.every(c=>c.name!=='agent_start'));assert.match(e.AC.nodes.notice.textContent,/请选择模型/);
});
test('native API connection sends the key once as an opaque connection request and clears the field',async()=>{
  const e=setup(),old=e.w.callApi,secret='sk-session-only';e.w.callApi=async(name,...args)=>{
    if(name==='agent_detect')return {code:0,data:{candidates:[{id:'deepseek-api',provider:'DeepSeek API',kind:'native_api',supported:true,credential_saved:false,credential_store_available:true}],providers:[]}};
    if(name==='agent_connect'){e.calls.push({name,args:clone(args)});return {code:0,data:{state:'ready',models:[{model:'deepseek-flash',displayName:'DeepSeek Flash',supportedReasoningEfforts:[{reasoningEffort:'high'}]}],account:{type:'api_key'},credential:{available:true,saved:true,status:'saved',source:'input',remember_requested:true},connection:{id:'deepseek-api',kind:'native_api'}}};}
    return old(name,...args);
  };
  await e.AC.detect();e.AC.nodes.apiKey.value=secret;await e.AC.connect();const request=e.calls.find(c=>c.name==='agent_connect').args[0];
  assert.deepEqual(request,{id:'deepseek-api',kind:'native_api',api_key:secret,remember_credential:true});assert.equal(e.AC.nodes.apiKey.value,'');assert.equal(JSON.stringify(e.AC.state).includes(secret),false);assert.match(e.AC.nodes.connectionInfo.textContent,/Windows凭据库/);assert.equal(e.AC.nodes.apiKeyField.hidden,true);assert.doesNotMatch(e.AC.nodes.credentialStatus.textContent,/输入新Key并连接可安全替换/);
});
test('saved native credential can reconnect without renderer access and be deleted explicitly',async()=>{
  const e=setup(),old=e.w.callApi;e.w.callApi=async(name,...args)=>{
    if(name==='agent_detect')return {code:0,data:{candidates:[{id:'deepseek-api',provider:'DeepSeek API',kind:'native_api',supported:true,credential_saved:true,credential_store_available:true,credential:{available:true,saved:true,status:'saved'}}],providers:[]}};
    if(name==='agent_connect'){e.calls.push({name,args:clone(args)});return {code:0,data:{state:'ready',models:[],account:{type:'api_key'},credential:{available:true,saved:true,status:'saved',source:'windows_credential_manager'},connection:{id:'deepseek-api',kind:'native_api'}}};}
    if(name==='agent_forget_credential'){e.calls.push({name,args:clone(args)});return {code:0,data:{provider_id:'deepseek-api',deleted:true,credential:{available:true,saved:false,status:'empty'}}};}
    return old(name,...args);
  };
  await e.AC.detect();assert.equal(e.AC.nodes.deleteCredential.hidden,false);await e.AC.connect();
  assert.deepEqual(e.calls.find(c=>c.name==='agent_connect').args[0],{id:'deepseek-api',kind:'native_api',api_key:'',remember_credential:true});
  await e.AC.forgetCredential();assert.deepEqual(e.calls.find(c=>c.name==='agent_forget_credential').args,['deepseek-api']);
  assert.equal(e.AC.state.credential.saved,false);assert.equal(e.AC.state.credential.source,'active_memory');assert.equal(e.AC.nodes.deleteCredential.hidden,true);
});
test('native API key leaves the DOM immediately after submission even while connection is pending',async()=>{
  const e=setup(),old=e.w.callApi,secret='sk-clear-before-await';let finish;e.w.callApi=async(name,...args)=>{
    if(name==='agent_detect')return {code:0,data:{candidates:[{id:'deepseek-api',provider:'DeepSeek API',kind:'native_api',supported:true}],providers:[]}};
    if(name==='agent_connect'){e.calls.push({name,args:clone(args)});return new Promise(resolve=>finish=resolve);}return old(name,...args);
  };
  await e.AC.detect();e.AC.nodes.apiKey.value=secret;const pending=e.AC.connect();assert.equal(e.AC.nodes.apiKey.value,'');assert.equal(e.calls.find(c=>c.name==='agent_connect').args[0].api_key,secret);assert.deepEqual(e.AC.nodes.model.children.map(node=>node.value),['']);
  await e.AC.connect();assert.equal(e.calls.filter(c=>c.name==='agent_connect').length,1);finish({code:1,message:'测试连接失败'});await pending;assert.equal(e.AC.nodes.apiKey.value,'');assert.equal(JSON.stringify(e.AC.state).includes(secret),false);assert.equal(e.AC.state.connection,'disconnected');assert.deepEqual(e.AC.nodes.model.children.map(node=>node.value),['']);assert.deepEqual(e.AC.nodes.effort.children.map(node=>node.value),['']);
});
test('provider connection failures show friendly Chinese text and keep copyable diagnostics collapsed',async()=>{
  const e=setup();await e.AC.detect();const old=e.w.callApi;e.w.callApi=async(name,...args)=>name==='agent_connect'?{code:1,message:'内部文字不应替代稳定说明',reason:'account_catalog_auth'}:old(name,...args);
  await e.AC.connect();assert.match(e.AC.nodes.notice.textContent,/认证未通过/);assert.doesNotMatch(e.AC.nodes.notice.textContent,/account_catalog_auth|内部文字/);assert.equal(e.AC.nodes.technicalDetails.hidden,false);assert.match(e.AC.nodes.technicalDetailsBody.textContent,/account_catalog_auth/);assert.match(e.AC.nodes.technicalDetailsBody.textContent,/内部文字/);assert.equal(e.AC.state.connection,'disconnected');
});
test('version incompatibility reports directly and only copies a fixed Agent prompt',async()=>{
  const e=setup(),copied=[];e.w.navigator={clipboard:{writeText:async value=>copied.push(value)}};e.AC.nodes.input.value='用户尚未发送的原消息';
  const response={code:1,reason:'native_session_activation_blocked',message:'旧会话内部文本不得拼接',details:{compatibility:'version_incompatible',recovery_action:'agent_guided_current_file_compatibility',issues:[{code:'database_changed',field:'database_fingerprint'}]}};
  const message=e.AC.errorText(response);e.AC.notice(message,true);
  assert.match(message,/不兼容/);assert.equal(e.AC.nodes.compatibilityGuidance.hidden,false);assert.match(e.AC.nodes.compatibilityPrompt.textContent,/无法解析该文件/);assert.match(e.AC.nodes.compatibilityPrompt.textContent,/不要覆盖原文件/);assert.doesNotMatch(e.AC.nodes.compatibilityPrompt.textContent,/旧会话内部文本/);
  const sends=e.calls.filter(row=>['agent_start','agent_send'].includes(row.name)).length;
  const pending=e.AC.nodes.compatibilityCopy.emit('click',{currentTarget:e.AC.nodes.compatibilityCopy});await pending;
  assert.deepEqual(copied,[e.AC.compatibilityPrompt]);assert.equal(e.AC.nodes.input.value,'用户尚未发送的原消息');assert.equal(e.calls.filter(row=>['agent_start','agent_send'].includes(row.name)).length,sends);assert.equal(e.AC.state.sessions.size,0);
});
test('ordinary activation mismatch is not mislabeled as version incompatibility',()=>{
  const e=setup(),response={code:1,reason:'native_session_activation_blocked',details:{issues:[{code:'document_changed',field:'document_id'}]}};
  const message=e.AC.errorText(response);e.AC.notice(message,true);assert.match(message,/环境或当前文档已经变化/);assert.doesNotMatch(message,/数据库或引擎不兼容/);assert.equal(e.AC.nodes.compatibilityGuidance.hidden,true);
});
test('switching connections hides an old catalog and a failed replacement keeps the prior ready connection',async()=>{
  const e=setup();await e.ready();const old=e.w.callApi;e.AC.state.candidates.push({id:'other-ready',provider:'Other',path:'D:/other.exe',supported:true});e.AC.renderSettings();
  e.AC.nodes.cli.value='other-ready';e.AC.nodes.cli.emit('change');assert.deepEqual(e.AC.nodes.model.children.map(node=>node.value),['']);assert.deepEqual(e.AC.nodes.effort.children.map(node=>node.value),['']);assert.equal(e.AC.nodes.send.disabled,true);assert.equal(e.AC.nodes.connect.disabled,false);
  e.w.callApi=async(name,...args)=>name==='agent_connect'?{code:1,message:'替代连接失败'}:old(name,...args);await e.AC.connect();assert.equal(e.AC.state.connection,'ready');assert.equal(e.AC.state.connectedCliId,'codex-one');assert.deepEqual(e.AC.nodes.model.children.map(node=>node.value),['']);assert.equal(e.AC.nodes.send.disabled,true);
  e.AC.nodes.cli.value='codex-one';e.AC.nodes.cli.emit('change');assert.equal(e.AC.nodes.model.value,'chosen-model');assert.equal(e.AC.nodes.effort.value,'high');assert.equal(e.AC.nodes.send.disabled,false);assert.equal(e.AC.nodes.connect.disabled,true);
});
test('development acceptance mode never exposes or retains the API key controls',async()=>{
  const e=setup(),old=e.w.callApi,secret='TEST_ONLY_SHOULD_NEVER_STAY_IN_DOM';e.w.APP_IDENTITY={mode:'development_acceptance',network_policy:'disabled'};e.AC.nodes.apiKey.value=secret;
  e.w.callApi=async(name,...args)=>name==='agent_detect'?{code:0,data:{candidates:[{id:'deepseek-api',provider:'DeepSeek API',kind:'native_api',supported:false,status:'network_disabled',credential_saved:false,credential_store_available:true}],providers:[],scan:{complete:true,issues:[]}}}:old(name,...args);
  await e.AC.detect();assert.equal(e.AC.nodes.apiKeyField.hidden,true);assert.equal(e.AC.nodes.credentialControls.hidden,true);assert.equal(e.AC.nodes.apiKey.value,'');assert.equal(JSON.stringify(e.AC.state).includes(secret),false);assert.equal(e.AC.nodes.connect.disabled,true);
});
test('all discovered products remain inspectable while unsupported installs cannot connect or send through an old connection',async()=>{
  const e=setup(),old=e.w.callApi,rows=[
    {id:'codex-one',provider:'Codex',provider_id:'codex',path:'C:/npm/codex.exe',version:'0.147.0',supported:true,sources:['npm全局安装'],entry_paths:['C:/npm/codex.cmd'],status:'supported',kind:'native'},
    {id:'desktop',provider:'Codex',provider_id:'codex',path:'C:/desktop/codex.exe',supported:false,sources:['桌面应用内置'],reason:'当前程序文件尚未经过适配验证',status:'unverified',kind:'native'},
    {id:'doer',provider:'CodeArts Doer',provider_id:'doer',path:'C:/tools/doer.cmd',supported:false,sources:['PATH环境变量'],entry_paths:['C:/tools/doer.cmd','C:/tools/doer.ps1'],reason:'已经发现，但尚未适配连接协议',status:'unadapted',kind:'npm_wrapper'}];
  e.w.callApi=async(name,...args)=>name==='agent_detect'?{code:0,data:{candidates:rows,providers:[{id:'codex',name:'Codex'},{id:'doer',name:'CodeArts Doer'}],scan:{path_directories:['C:/npm','C:/tools'],scanned_directories:['C:/npm','C:/tools'],issues:[],complete:true}}}:old(name,...args);
  await e.ready();assert.equal(e.AC.nodes.cli.children.length,4);assert.ok(e.AC.nodes.cli.children.slice(1).every(n=>!n.disabled));assert.match(e.AC.nodes.discoverySummary.textContent,/发现 3 个.*可连接 1 个/);
  const codexOptions=e.AC.nodes.cli.children.filter(n=>/^Codex /.test(n.textContent));assert.equal(codexOptions.length,2);assert.notEqual(codexOptions[0].textContent,codexOptions[1].textContent);
  assert.match(e.AC.nodes.cliInfo.textContent,/关联命令入口/);assert.match(e.AC.nodes.cliInfo.textContent,/包装脚本仅按安装布局关联，未执行或核验/);assert.match(e.AC.nodes.cliInfo.textContent,/连接只使用上方已验证的原生程序/);
  e.AC.nodes.cli.value='doer';e.AC.nodes.cli.emit('change');assert.equal(e.AC.nodes.cliPath.textContent,'C:/tools/doer.cmd');assert.match(e.AC.nodes.cliInfo.textContent,/尚未适配连接协议/);assert.match(e.AC.nodes.cliInfo.textContent,/doer.ps1/);
  assert.equal(e.AC.nodes.connect.disabled,true);assert.equal(e.AC.nodes.send.disabled,true);const connects=e.calls.filter(c=>c.name==='agent_connect').length;
  assert.match(e.AC.nodes.cliInfo.textContent,/此项当前不可连接/);rows[2].entry_note='仅为布局关联线索，没有执行或验证这些脚本';e.AC.renderCliDetail();assert.match(e.AC.nodes.cliInfo.textContent,/仅为布局关联线索/);assert.doesNotMatch(e.AC.nodes.cliInfo.textContent,/连接只使用上方已验证/);
  await e.AC.connect();e.AC.nodes.input.value='不能误用此前连接';await e.AC.send();assert.equal(e.calls.filter(c=>c.name==='agent_connect').length,connects);assert.ok(e.calls.every(c=>c.name!=='agent_start'));
  e.AC.nodes.cli.value='codex-one';e.AC.nodes.cli.emit('change');assert.equal(e.AC.nodes.model.value,'chosen-model');assert.equal(e.AC.nodes.effort.value,'high');assert.equal(e.AC.nodes.send.disabled,false);
});
test('rescanning retains the selected install and connected model instead of silently selecting the first supported install',async()=>{
  const e=setup();await e.ready();const old=e.w.callApi;e.w.callApi=async(name,...args)=>name==='agent_detect'?{code:0,data:{candidates:[{id:'new-first',provider:'Codex',path:'D:/other/codex.exe',supported:true},{id:'codex-one',provider:'Codex',path:'C:/safe/codex.exe',supported:true}],scan:{complete:true,issues:[]}}}:old(name,...args);
  await e.AC.detect();assert.equal(e.AC.state.cliId,'codex-one');assert.equal(e.AC.state.connection,'ready');assert.equal(e.AC.nodes.model.value,'chosen-model');assert.equal(e.AC.nodes.effort.value,'high');assert.equal(e.AC.nodes.send.disabled,false);
});
test('disappeared connected install disables sending and does not transfer the old connection to a newly found install',async()=>{
  const e=setup();await e.ready();const old=e.w.callApi;e.w.callApi=async(name,...args)=>name==='agent_detect'?{code:0,data:{candidates:[{id:'other',provider:'Codex',path:'D:/other/codex.exe',supported:true}]}}:old(name,...args);
  await e.AC.detect();assert.equal(e.AC.state.cliId,'other');assert.equal(e.AC.state.connectedCliId,'codex-one');assert.equal(e.AC.nodes.send.disabled,true);e.AC.nodes.input.value='必须先重新连接';await e.AC.send();assert.ok(e.calls.every(c=>c.name!=='agent_start'));
});
test('discovery distinguishes no installation, unadapted installations and partially unreadable search directories',async()=>{
  const e=setup(),old=e.w.callApi;let result={candidates:[],providers:[],scan:{complete:true,path_directories:[],scanned_directories:[],issues:[]}};
  e.w.callApi=async(name,...args)=>name==='agent_detect'?{code:0,data:result}:old(name,...args);await e.AC.detect();assert.match(e.AC.nodes.notice.textContent,/范围内未发现/);
  result={candidates:[{id:'unadapted',provider:'Other CLI',path:'C:/bin/agent.exe',supported:false,reason:'尚未适配连接协议'}],scan:{complete:true,issues:[]}};await e.AC.detect();assert.equal(e.AC.nodes.cli.value,'unadapted');assert.match(e.AC.nodes.notice.textContent,/发现 1 个.*0 个可连接/);assert.doesNotMatch(e.AC.nodes.notice.textContent,/未发现/);
  result={candidates:[],scan:{complete:false,path_directories:['C:/denied'],scanned_directories:[],issues:[{path:'C:/denied',message:'目录访问被拒绝'}]}};await e.AC.detect();assert.match(e.AC.nodes.notice.textContent,/扫描有未完成/);assert.match(e.AC.nodes.discoveryBody.textContent,/目录访问被拒绝/);assert.match(e.AC.nodes.discoveryBody.textContent,/C:\/denied/);assert.match(e.AC.nodes.discoveryBody.textContent,/不对全盘/);
});
test('scan failures preserve existing results and connection; scanning never starts or completes over an active task',async()=>{
  const e=setup();await e.ready();const old=e.w.callApi;e.w.callApi=async(name,...args)=>name==='agent_detect'?{code:1,message:'检测服务暂不可用'}:old(name,...args);
  await e.AC.detect();assert.equal(e.AC.state.cliId,'codex-one');assert.equal(e.AC.state.connection,'ready');assert.match(e.AC.nodes.notice.textContent,/仍显示上一次/);
  let finish,count=0;e.w.callApi=async(name,...args)=>name==='agent_detect'?(count++,new Promise(resolve=>finish=resolve)):old(name,...args);
  const pending=e.AC.detect(),s=e.AC.session('running');s.active=true;s.state='running';e.AC.state.active=s.session_id;await e.AC.detect();assert.equal(count,1);
  finish({code:0,data:{candidates:[{id:'late',supported:true,path:'late.exe'}]}});await pending;assert.equal(e.AC.state.cliId,'codex-one');assert.equal(e.AC.state.connection,'ready');assert.equal(e.AC.state.busy,null);
});
test('a delayed discovery response cannot overwrite a newer connection or app closing state',async()=>{
  for(const mode of ['connection','closing']){const e=setup();await e.ready();const old=e.w.callApi;let finish;e.w.callApi=async(name,...args)=>name==='agent_detect'?new Promise(resolve=>finish=resolve):old(name,...args);
    const pending=e.AC.detect();if(mode==='connection'){e.AC.state.connectedCliId='newer';e.AC.state.connection='blocked';}else e.AC.state.closing=true;
    finish({code:0,data:{candidates:[{id:'late',supported:true,path:'late.exe'}]}});await pending;assert.equal(e.AC.state.cliId,'codex-one');assert.equal(e.AC.state.busy,null);if(mode==='connection')assert.equal(e.AC.state.connection,'blocked');}
});
test('model changes select that model default and every backend-returned effort remains selectable',async()=>{
  const e=setup();await e.ready();e.AC.state.models=[
    {model:'chosen-model',supportedReasoningEfforts:[{reasoningEffort:'off'},{reasoningEffort:'high'}],defaultReasoningEffort:'high'},
    {model:'other-model',supportedReasoningEfforts:[{reasoningEffort:'xhigh'},{reasoningEffort:'ultra'},{reasoningEffort:'max'}],defaultReasoningEffort:'ultra'}
  ];e.AC.renderSettings();e.AC.nodes.model.value='other-model';e.AC.nodes.model.emit('change');
  assert.equal(e.AC.nodes.effort.value,'ultra');assert.deepEqual(e.AC.nodes.effort.children.map(n=>n.value),['','xhigh','ultra','max']);
  e.AC.nodes.input.value='使用目录明确返回的档位';await e.AC.send();const sent=e.calls.find(c=>c.name==='agent_start').args[0];assert.equal(sent.model,'other-model');assert.equal(sent.effort,'ultra');
});
test('manual model selection supports a model without reasoning tiers and restores the control for other models',async()=>{
  const e=setup();await e.ready();e.AC.state.models.push({model:'basic-model',displayName:'基础模型',supportedReasoningEfforts:[],defaultReasoningEffort:null});e.AC.renderSettings();
  e.AC.nodes.model.value='basic-model';e.AC.nodes.model.emit('change');assert.equal(e.AC.state.effort,null);assert.equal(e.AC.nodes.effort.value,'');assert.equal(e.AC.nodes.effort.disabled,true);assert.equal(e.AC.nodes.effort.children[0].textContent,'该模型无推理档位');
  e.AC.nodes.input.value='手动选择无推理档位模型';await e.AC.send();const sent=e.calls.find(c=>c.name==='agent_start').args[0];assert.equal(sent.model,'basic-model');assert.equal(sent.effort,null);
  await e.AC.stop(e.AC.active());e.AC.newTask();e.AC.nodes.model.value='chosen-model';e.AC.nodes.model.emit('change');assert.equal(e.AC.nodes.effort.disabled,false);assert.equal(e.AC.nodes.effort.value,'high');
});
test('history shows its immutable locked selection while a new task restores the editable choice',async()=>{
  const e=setup();await e.ready();const old=e.w.callApi;e.w.callApi=async(name,...args)=>{
    if(name==='agent_sessions')return {code:0,data:{sessions:[{session_id:'locked-history',title:'锁定会话',kind:'whiteboard',workflow:'staged',state:'cold',model:'other-model',effort:'max',connection_kind:'native_api',can_resume:true}]}};
    if(name==='agent_poll')return {code:0,data:{session_id:args[0],state:'completed',kind:'whiteboard',workflow:'staged',active:false,next_seq:0,events:[],model:'other-model',effort:'max',can_resume:true}};
    return old(name,...args);
  };
  await e.AC.loadHistory();await e.AC.selectSession('locked-history');assert.equal(e.AC.nodes.model.value,'other-model');assert.equal(e.AC.nodes.effort.value,'max');assert.equal(e.AC.nodes.model.disabled,true);assert.equal(e.AC.nodes.effort.disabled,true);
  e.AC.newTask();assert.equal(e.AC.nodes.model.value,'chosen-model');assert.equal(e.AC.nodes.effort.value,'high');assert.equal(e.AC.nodes.model.disabled,false);assert.equal(e.AC.nodes.effort.disabled,false);
});
test('history preserves a locked model and effort that have left the current catalog',async()=>{
  const e=setup();await e.ready();const old=e.w.callApi;e.w.callApi=async(name,...args)=>{
    if(name==='agent_sessions')return {code:0,data:{sessions:[{session_id:'retired-history',title:'退役目录会话',kind:'whiteboard',workflow:'staged',state:'cold',model:'retired-model',effort:'xhigh',connection_kind:'native_api',can_resume:true}]}};
    if(name==='agent_poll')return {code:0,data:{session_id:args[0],state:'completed',kind:'whiteboard',workflow:'staged',active:false,next_seq:0,events:[],model:'retired-model',effort:'xhigh',can_resume:true}};
    return old(name,...args);
  };
  await e.AC.loadHistory();await e.AC.selectSession('retired-history');assert.equal(e.AC.nodes.model.value,'retired-model');assert.equal(e.AC.nodes.effort.value,'xhigh');assert.match(e.AC.nodes.model.children.at(-1).textContent,/会话锁定，当前目录不可用/);assert.match(e.AC.nodes.effort.children.at(-1).textContent,/会话锁定，当前目录不可用/);assert.equal(e.AC.nodes.model.disabled,true);assert.equal(e.AC.nodes.effort.disabled,true);
  e.AC.newTask();assert.equal(e.AC.nodes.model.value,'chosen-model');assert.equal(e.AC.nodes.effort.value,'high');
});
test('blocked provider connection explains isolation limit and never enables inference',async()=>{
  const e=setup();await e.AC.detect();const old=e.w.callApi;e.w.callApi=async(name,...args)=>name==='agent_connect'?{code:0,data:{state:'blocked',blocking_reason:'builtin_tool_isolation_unverified',models:[],account:{type:'chatgpt'},limits:{allowed:true,remaining_percent:48}}}:old(name,...args);
  await e.AC.connect();assert.equal(e.AC.nodes.send.disabled,true);assert.match(e.AC.nodes.notice.textContent,/内置工具隔离边界尚未完成验证/);assert.match(e.AC.nodes.connectionInfo.textContent,/48%/);
  e.AC.nodes.input.value='不要启动';await e.AC.send();assert.ok(e.calls.every(c=>c.name!=='agent_start'));
});
test('low or unknown quota stays informational and does not prevent a turn',async()=>{
  for(const limits of [{allowed:false,remaining_percent:4,message:'旧版额度门控'},{allowed:true,remaining_percent:null,known:false}]){const e=setup();await e.ready();e.AC.state.limits=limits;e.AC.refresh();assert.equal(e.AC.nodes.send.disabled,false);
    e.AC.nodes.input.value='继续';await e.AC.send();assert.ok(e.calls.some(c=>c.name==='agent_start'));}
});
test('start sends an immutable explicit workspace snapshot and idempotency key without changing editor',async()=>{
  const e=setup(),original=clone(e.w.WB.state.board),session=await e.start();const sent=e.calls.find(c=>c.name==='agent_start').args[0];
  assert.equal(sent.kind,'whiteboard');assert.deepEqual(sent.document,original);assert.equal(sent.model,'chosen-model');assert.equal(sent.effort,'high');assert.match(sent.client_request_id,/^[a-zA-Z0-9_-]{8,80}$/);
  assert.deepEqual(sent.workspace,{document_kind:'whiteboard',display_name:'白板测试',dirty:true,has_path:true,current_view:'whiteboard',can_save:true,can_open:true,can_export:false});assert.equal(JSON.stringify(sent.workspace).includes('board.json'),false);
  e.tab('rotation');e.w.WB.change(current=>current.team[0].panel.atk=3000);e.AC.refresh();assert.equal(session.base.document.team[0].panel.atk,2000);assert.equal(session.kind,'whiteboard');
  assert.match(e.AC.nodes.contextWarning.textContent,/当前输入已变化/);assert.equal(e.AC.nodes.input.value,'');assert.equal(e.orders.includes('pl-replace'),false);
});
test('native frozen input excludes previously calculated caches',async()=>{
  const e=setup();e.w.PL.state.plan.resolved_template={large_cached_output:'not sent'};await e.start('native');const document=e.calls.find(c=>c.name==='agent_start').args[0].document;
  assert.equal(document.resolved_template,undefined);assert.equal(document.resolution,undefined);assert.ok(document.manual_template);assert.ok(e.w.PL.state.plan.resolved_template);
});
for(const kind of ['whiteboard','native'])test(`unapplied ${kind} gear draft blocks sending instead of silently using old values`,async()=>{
  const e=setup();await e.ready();e.AC.nodes.kind.value=kind;e.draft(kind,true);e.AC.nodes.input.value='这是保留的任务';await e.AC.send();
  assert.ok(e.calls.every(c=>c.name!=='agent_start'));assert.equal(e.AC.nodes.input.value,'这是保留的任务');assert.match(e.AC.nodes.notice.textContent,/尚未应用/);
});
test('double send while start is pending cannot launch two turns',async()=>{
  const e=setup();await e.ready();e.AC.nodes.input.value='开始';const old=e.w.callApi;let finish,count=0;e.w.callApi=async(name,...args)=>name==='agent_start'?(count++,new Promise(resolve=>finish=resolve)):old(name,...args);
  const first=e.AC.send();await e.AC.send();assert.equal(count,1);assert.equal(e.AC.nodes.send.disabled,true);
  finish({code:0,data:{session_id:'one',run_id:'run',state:'running',active:true,next_seq:0,events:[]}});await first;
});
test('streaming deltas merge by message ID, duplicate sequences are ignored, and source strings are plain text',()=>{
  const e=setup(),s=e.AC.session('stream');e.AC.state.selected=s.session_id;s.kind='whiteboard';
  const events=[{seq:1,type:'delta',run_id:'r',message_id:'m',delta:'<script>evil</script>'},{seq:2,type:'delta',run_id:'r',message_id:'m',delta:' 中文'}];
  e.AC.receive(s,{session_id:s.session_id,state:'running',active:true,next_seq:2,events});const node=e.AC.messageNodes.get('m');e.AC.receive(s,{session_id:s.session_id,next_seq:2,events});
  assert.equal(s.messages[0].text,'<script>evil</script> 中文');assert.equal(e.AC.messageNodes.get('m'),node);assert.equal(all(node).some(n=>n.tagName==='script'),false);
});
test('turn errors keep the main message human and retain technical details for copying',()=>{
  const e=setup(),s=e.AC.session('stable-error');e.AC.state.selected=s.session_id;s.kind='whiteboard';
  e.AC.receive(s,{session_id:s.session_id,state:'error',active:false,next_seq:1,events:[{seq:1,type:'error',message:'本轮原生Agent运行失败',reason:'native_turn_error'}]});
  assert.match(s.error,/本轮原生Agent运行失败/);assert.doesNotMatch(s.error,/native_turn_error/);assert.equal(e.AC.nodes.error.textContent,s.error);assert.match(e.AC.nodes.technicalDetailsBody.textContent,/native_turn_error/);
  e.AC.receive(s,{session_id:s.session_id,next_seq:2,events:[{seq:2,type:'error',message:'不得显示的 provider payload',reason:'runtime_official_turn_failed:pipe_read_timeout'}]});assert.match(s.error,/本地 AI 服务通信中断/);assert.doesNotMatch(s.error,/DeepSeek|pipe_read_timeout|provider payload/);assert.match(e.AC.nodes.technicalDetailsBody.textContent,/runtime_official_turn_failed:pipe_read_timeout|provider payload/);
  e.AC.receive(s,{session_id:s.session_id,next_seq:3,events:[{seq:3,type:'error',message:'内部结算字段',reason:'runtime_official_turn_failed:native_app_settlement_effect_mask'}]});assert.match(s.error,/App 工具.*结算/);assert.match(s.error,/不要直接重复提交/);assert.doesNotMatch(s.error,/DeepSeek|effect_mask/);
  e.AC.receive(s,{session_id:s.session_id,next_seq:4,events:[{seq:4,type:'error',message:'安全提示',reason:'C:/private/key'}]});assert.equal(s.error,'安全提示');
});
test('reset snapshot replaces truncated history without appending already represented deltas twice',()=>{
  const e=setup(),s=e.AC.session('reset');e.AC.state.selected=s.session_id;s.kind='whiteboard';
  e.AC.receive(s,{session_id:s.session_id,reset_required:true,next_seq:20,messages:[{message_id:'answer',role:'assistant',text:'完整答案'}],events:[{seq:19,type:'delta',message_id:'answer',delta:'答案'}]});
  assert.equal(s.messages.length,1);assert.equal(s.messages[0].text,'完整答案');assert.equal(s.cursor,20);
});
test('native V2 persists present and claim before any Renderer side effect',async()=>{
  const e=setup(),s=e.AC.session('two-phase-save');e.AC.state.selected=s.session_id;s.kind='whiteboard';s.live=true;s.base=e.AC.capture('whiteboard');e.w.WB.save=async()=>{e.orders.push('guarded-save');return true;};
  const action=twoPhaseAction(s,'save_current',{action_id:'21'.repeat(16),reason:'两阶段保存'});e.AC.receive(s,{next_seq:1,events:[{seq:1,type:'app_action',action}]});
  const old=e.w.callApi;let finishClaim;e.w.callApi=async(name,...args)=>{if(name!=='agent_claim_app_action')return old(name,...args);e.calls.push({name,args:clone(args)});return new Promise(resolve=>finishClaim=resolve);};
  const running=all(e.AC.actionNodes.get(action.action_id)).find(node=>node.tagName==='button'&&node.textContent==='处理').emit('click');await tick();
  assert.deepEqual(e.calls.filter(row=>row.name.startsWith('agent_')&&row.name.includes('app_action')).map(row=>row.name),['agent_present_app_action','agent_claim_app_action']);assert.equal(e.orders.includes('guarded-save'),false);
  const receipt=`ui_${'r'.repeat(43)}`;finishClaim({code:0,data:{status:'claimed_for_external_execution',card:{...action,status:'presented',ui_receipt:receipt},request:{action_id:action.action_id,action:'save_current',reason:action.reason||'',suggested_name:action.suggested_name||null,document_kind:'whiteboard',display_name:'白板测试',current_view:'whiteboard',dirty_open_guard_required:false}}});await running;
  assert.equal(e.orders.includes('guarded-save'),true);assert.deepEqual(e.calls.filter(row=>row.name.includes('app_action')).map(row=>row.name),['agent_present_app_action','agent_claim_app_action','agent_settle_app_action']);
  const settle=e.calls.find(row=>row.name==='agent_settle_app_action');assert.equal(settle.args[2],receipt);assert.deepEqual(Object.keys(settle.args[3]).sort(),['dirty_open_guard_passed','message','status']);assert.equal(settle.args[3].status,'completed');assert.equal(settle.args[3].dirty_open_guard_passed,false);assert.equal(JSON.stringify(settle.args[4]).includes('board.json'),false);
  assert.equal(s.appActions[0].externalExecutionClaimed,false);assert.equal(s.appActions[0].execution_outcome_unknown,false);assert.equal(e.AC.hasUnsettledAppAction(s),false);
});
test('native V2 claims a dirty open before showing UI and settles its exact guarded outcome',async()=>{
  const e=setup(),s=e.AC.session('two-phase-open');e.AC.state.selected=s.session_id;s.kind='whiteboard';s.live=true;s.base=e.AC.capture('whiteboard');
  const action=twoPhaseAction(s,'open_document',{action_id:'22'.repeat(16),reason:'打开另一个文档',requires_applied_candidate:false,preconditions:{contract:'agent-app-action-preconditions-v1',status:'ready',issues:[],requires_applied_candidate:false}});e.AC.receive(s,{next_seq:1,events:[{seq:1,type:'app_action',action}]});
  await all(e.AC.actionNodes.get(action.action_id)).find(node=>node.tagName==='button'&&node.textContent==='处理').emit('click');const calls=e.calls.filter(row=>row.name.includes('app_action'));
  assert.deepEqual(calls.map(row=>row.name),['agent_present_app_action','agent_claim_app_action']);assert.equal(e.orders.includes('open-file'),true);const options=e.fileActions.at(-1);assert.equal(options.agentActionId,action.action_id);assert.equal(options.dirtyOpenGuardRequired,true);
  options.onAgentActionResult({status:'completed',message:'已通过未保存文档保护并打开'});await tick();const settle=e.calls.find(row=>row.name==='agent_settle_app_action');assert.deepEqual(settle.args[3],{status:'completed',message:'已通过未保存文档保护并打开',dirty_open_guard_passed:true});assert.equal(s.appActions[0].status,'completed');assert.equal(s.live,false);assert.equal(s.canResume,true);assert.equal(s.activation.state,'cold');assert.equal(s.activation.recovery,null);assert.match(e.AC.nodes.notice.textContent,/原会话已安全脱离/);
});
for(const [name,forge] of [
  ['wrong action id',({action,receipt})=>({status:'presented_for_user',card:{...action,action_id:'wrong-action',status:'presented',ui_receipt:receipt}})],
  ['changed action kind',({action,receipt})=>({status:'presented_for_user',card:{...action,action:'open_document',status:'presented',ui_receipt:receipt}})],
  ['conflicting receipt echo',({action,receipt})=>({status:'presented_for_user',ui_receipt:`ui_${'x'.repeat(43)}`,card:{...action,status:'presented',ui_receipt:receipt}})],
])test(`two-phase present rejects ${name} before claim or Renderer side effects`,async()=>{
  const e=setup(),s=e.AC.session(`forged-present-${name.replace(/[^A-Za-z0-9]+/g,'-')}`);e.AC.state.selected=s.session_id;s.kind='whiteboard';s.live=true;s.base=e.AC.capture('whiteboard');e.w.WB.save=async()=>{e.orders.push('guarded-save');return true;};
  const action=twoPhaseAction(s,'save_current',{action_id:`p${String(name.length).padStart(31,'0')}`,reason:'安全测试',suggested_name:'safe.json'});e.AC.receive(s,{next_seq:1,events:[{seq:1,type:'app_action',action}]});const old=e.w.callApi,receipt=`ui_${'p'.repeat(43)}`;
  e.w.callApi=async(api,...args)=>api==='agent_present_app_action'?(e.calls.push({name:api,args:clone(args)}),{code:0,data:forge({action,receipt})}):old(api,...args);
  await e.AC.executeTwoPhaseAppAction(s,s.appActions[0]);
  assert.equal(e.calls.filter(row=>row.name==='agent_claim_app_action').length,0);assert.equal(e.calls.filter(row=>row.name==='agent_settle_app_action').length,0);assert.equal(e.orders.includes('guarded-save'),false);assert.equal(e.orders.includes('open-file'),false);assert.equal(s.appActions[0].status,'queued');
});
for(const [name,forge] of [
  ['wrong card id',(data)=>data.card.action_id='wrong-action'],
  ['changed card action',(data)=>data.card.action='open_document'],
  ['wrong card receipt',(data)=>data.card.ui_receipt=`ui_${'z'.repeat(43)}`],
  ['wrong request id',(data)=>data.request.action_id='wrong-action'],
  ['changed request action',(data)=>data.request.action='open_document'],
  ['extra request path',(data)=>data.request.path='C:/private/forged.json'],
  ['wrong top receipt',(data)=>data.ui_receipt=`ui_${'z'.repeat(43)}`],
  ['unknown claim status',(data)=>{data.status='claim_state_forged';data.request=null;}],
])test(`claimed two-phase response rejects ${name} and settles failed without side effects`,async()=>{
  const e=setup(),s=e.AC.session(`forged-claim-${name.replace(/[^A-Za-z0-9]+/g,'-')}`);e.AC.state.selected=s.session_id;s.kind='whiteboard';s.live=true;s.base=e.AC.capture('whiteboard');e.w.WB.save=async()=>{e.orders.push('guarded-save');return true;};
  const action=twoPhaseAction(s,'save_current',{action_id:`c${String(name.length).padStart(31,'0')}`,reason:'安全测试',suggested_name:'safe.json'});e.AC.receive(s,{next_seq:1,events:[{seq:1,type:'app_action',action}]});const old=e.w.callApi;
  e.w.callApi=async(api,...args)=>{if(api!=='agent_claim_app_action')return old(api,...args);e.calls.push({name:api,args:clone(args)});const response=claimedResponse(action,args[2]);forge(response.data);return response;};
  await e.AC.executeTwoPhaseAppAction(s,s.appActions[0]);const claim=e.calls.find(row=>row.name==='agent_claim_app_action'),settle=e.calls.find(row=>row.name==='agent_settle_app_action');
  assert.ok(claim);assert.ok(settle);assert.equal(settle.args[2],claim.args[2]);assert.equal(settle.args[3].status,'failed');assert.match(settle.args[3].message,/未执行App操作/);assert.equal(e.orders.includes('guarded-save'),false);assert.equal(e.orders.includes('open-file'),false);assert.equal(e.orders.some(row=>row.startsWith('navigate:')),false);
});
for(const [change,mutate] of [
  ['document',e=>e.w.WB.change(current=>{current.title='等待期间已修改';})],
  ['path',e=>{e.w.WB.state.path='other-board.json';}],
  ['sha',e=>{e.w.WB.state.fileSha='other-sha';}],
  ['request epoch',e=>{e.w.WB.state.request++;}],
  ['tab',e=>e.tab('rotation')],
  ['selected session',e=>{const other=e.AC.session('other-during-claim');e.AC.state.selected=other.session_id;e.AC.state.viewEpoch++;}],
  ['candidate identity',(_e,s)=>{s.candidate={...s.candidate,revision:s.candidate.revision+1};}],
])test(`two-phase claim cannot act after ${change} changes while awaiting Host`,async()=>{
  const e=setup(),s=e.AC.session(`claim-race-${change.replace(/[^A-Za-z0-9]+/g,'-')}`);e.AC.state.selected=s.session_id;s.kind='whiteboard';s.live=true;s.base=e.AC.capture('whiteboard');e.w.WB.save=async()=>{e.orders.push('guarded-save');return true;};
  const action=twoPhaseAction(s,'save_current',{action_id:`r${String(change.length).padStart(31,'0')}`,reason:'安全测试',suggested_name:'safe.json'});e.AC.receive(s,{next_seq:1,events:[{seq:1,type:'app_action',action}]});const old=e.w.callApi;let finishClaim,claimArgs;
  e.w.callApi=async(api,...args)=>{if(api!=='agent_claim_app_action')return old(api,...args);e.calls.push({name:api,args:clone(args)});claimArgs=args;return new Promise(resolve=>finishClaim=resolve);};
  const running=e.AC.executeTwoPhaseAppAction(s,s.appActions[0]);await tick();assert.ok(finishClaim);mutate(e,s);finishClaim(claimedResponse(action,claimArgs[2]));await running;
  const settle=e.calls.find(row=>row.name==='agent_settle_app_action');assert.ok(settle);assert.equal(settle.args[2],claimArgs[2]);assert.equal(settle.args[3].status,'failed');assert.match(settle.args[3].message,/未执行App操作/);assert.equal(e.orders.includes('guarded-save'),false);assert.equal(e.orders.includes('open-file'),false);assert.equal(e.orders.some(row=>row.startsWith('navigate:')),false);
});
test('native V2 recovered claim never replays an outcome-unknown action',async()=>{
  const e=setup(),s=e.AC.session('two-phase-recovery');e.AC.state.selected=s.session_id;s.kind='whiteboard';s.live=true;s.base=e.AC.capture('whiteboard');e.w.WB.save=async()=>{e.orders.push('guarded-save');return true;};
  const action=twoPhaseAction(s,'save_current',{action_id:'23'.repeat(16)});e.AC.receive(s,{next_seq:1,events:[{seq:1,type:'app_action',action}]});const old=e.w.callApi;
  e.w.callApi=async(name,...args)=>name==='agent_claim_app_action'?(e.calls.push({name,args:clone(args)}),{code:0,data:{status:'recovered_without_replay',card:{...action,reason:action.reason||'',suggested_name:action.suggested_name||null,status:'failed',ui_receipt:args[2],result_message:'检测到既有执行标记，操作结果未知；系统未自动重试。'},request:null}}):old(name,...args);
  await all(e.AC.actionNodes.get(action.action_id)).find(node=>node.tagName==='button'&&node.textContent==='处理').emit('click');assert.equal(e.orders.includes('guarded-save'),false);assert.ok(e.calls.every(row=>row.name!=='agent_settle_app_action'));assert.equal(s.appActions[0].status,'failed');assert.match(e.AC.nodes.notice.textContent,/没有自动重放/);
});
test('native V2 settlement failure cannot re-execute an already claimed side effect',async()=>{
  const e=setup(),s=e.AC.session('two-phase-settle-failure');e.AC.state.selected=s.session_id;s.kind='whiteboard';s.live=true;s.base=e.AC.capture('whiteboard');e.w.WB.save=async()=>{e.orders.push('guarded-save');return true;};
  const action=twoPhaseAction(s,'save_current',{action_id:'24'.repeat(16)});e.AC.receive(s,{next_seq:1,events:[{seq:1,type:'app_action',action}]});const old=e.w.callApi;let failSettlement=true;e.w.callApi=async(name,...args)=>name==='agent_settle_app_action'?(e.calls.push({name,args:clone(args)}),failSettlement?{code:1,message:'模拟写回失败'}:{code:0,data:{status:'already_settled',card:{...action,status:'completed',result_message:'保存结果已补记'}}}):old(name,...args);
  await all(e.AC.actionNodes.get(action.action_id)).find(node=>node.tagName==='button'&&node.textContent==='处理').emit('click');assert.equal(e.orders.filter(row=>row==='guarded-save').length,1);assert.equal(s.appActions[0].status,'claimed_for_external_execution');
  e.AC.receive(s,{next_seq:2,events:[],app_actions:[{...action,status:'claimed_for_external_execution',execution_outcome_unknown:true}]});assert.ok(s.appActions[0].pendingSettlement);assert.equal(s.appActions[0].status,'claimed_for_external_execution');
  const retry=all(e.AC.actionNodes.get(action.action_id)).find(node=>node.tagName==='button'&&node.textContent==='重试写回结果');assert.equal(retry.disabled,false);failSettlement=false;await retry.emit('click');assert.equal(e.orders.filter(row=>row==='guarded-save').length,1);assert.equal(e.calls.filter(row=>row.name==='agent_settle_app_action').length,2);assert.equal(s.appActions[0].status,'completed');assert.equal(s.appActions[0].pendingSettlement,null);assert.equal(s.appActions[0].externalExecutionClaimed,false);assert.equal(s.appActions[0].execution_outcome_unknown,false);assert.equal(e.AC.hasUnsettledAppAction(s),false);
});
test('renderer reload can explicitly clear an unknown claim without replaying its side effect',async()=>{
  const e=setup(),s=e.AC.session('two-phase-renderer-reload');e.AC.state.selected=s.session_id;s.kind='whiteboard';s.live=true;s.base=e.AC.capture('whiteboard');e.w.WB.save=async()=>{e.orders.push('guarded-save');return true;};
  const action=twoPhaseAction(s,'save_current',{action_id:'25'.repeat(16)});e.AC.receive(s,{next_seq:1,events:[{seq:1,type:'app_action',action}]});const old=e.w.callApi;
  e.w.callApi=async(name,...args)=>{if(name==='agent_settle_app_action'){e.calls.push({name,args:clone(args)});return {code:1,message:'模拟写回失败'};}if(name==='agent_recover_app_action_without_replay'){e.calls.push({name,args:clone(args)});return {code:0,data:{status:'recovered_without_replay',card:{...action,status:'failed',ui_receipt:args[2],result_message:'未知结果已解除且未重放。'},request:null}};}return old(name,...args);};
  await all(e.AC.actionNodes.get(action.action_id)).find(node=>node.tagName==='button'&&node.textContent==='处理').emit('click');assert.equal(e.orders.filter(row=>row==='guarded-save').length,1);assert.ok(s.appActions[0].pendingSettlement);
  const receipt=s.appActions[0].ui_receipt;e.AC.receive(s,{reset_required:true,snapshot_seq:2,next_seq:2,messages:[],events:[],app_actions:[{...action,status:'claimed_for_external_execution',ui_receipt:receipt,execution_outcome_unknown:true}]});
  assert.equal(s.appActions[0].pendingSettlement,undefined);const recover=all(e.AC.actionNodes.get(action.action_id)).find(node=>node.tagName==='button'&&node.textContent==='解除未知操作');assert.equal(recover.disabled,false);await recover.emit('click');
  assert.equal(e.orders.filter(row=>row==='guarded-save').length,1);assert.equal(e.calls.filter(row=>row.name==='agent_present_app_action').length,1);assert.equal(e.calls.filter(row=>row.name==='agent_claim_app_action').length,1);assert.equal(e.calls.filter(row=>row.name==='agent_settle_app_action').length,1);const recovery=e.calls.filter(row=>row.name==='agent_recover_app_action_without_replay');assert.equal(recovery.length,1);assert.deepEqual(recovery[0].args,[s.session_id,action.action_id,receipt]);assert.equal(s.appActions[0].status,'failed');assert.equal(s.appActions[0].execution_outcome_unknown,false);assert.match(e.AC.nodes.notice.textContent,/没有重放/);
});
test('claimed or outcome-unknown App action blocks a new Agent message until settlement',async()=>{
  const e=setup();await e.ready();const s=e.AC.session('blocked-by-app-action');e.AC.state.selected=s.session_id;s.kind='whiteboard';s.workflow='staged';s.live=true;s.base=e.AC.capture('whiteboard');
  const action=twoPhaseAction(s,'save_current',{action_id:'26'.repeat(16),status:'claimed_for_external_execution',execution_outcome_unknown:true,ui_receipt:`ui_${'u'.repeat(43)}`});e.AC.receive(s,{next_seq:1,events:statusEvents(1,1),app_actions:[action]});
  assert.equal(e.AC.nodes.send.disabled,true);assert.match(e.AC.nodes.contextWarning.textContent,/先完成并写回|解除未知操作/);e.AC.nodes.input.value='不应提交的新消息';const before=e.calls.filter(row=>row.name==='agent_send').length;await e.AC.send();assert.equal(e.calls.filter(row=>row.name==='agent_send').length,before);assert.match(e.AC.nodes.notice.textContent,/先完成、写回或解除/);
  e.AC.receive(s,{next_seq:2,events:statusEvents(2,2,'completed'),app_actions:[{...action,status:'failed',execution_outcome_unknown:false}]});assert.equal(e.AC.nodes.send.disabled,false);
});
test('claimed App action also blocks candidate apply acknowledgement and cancellation',async()=>{
  const e=setup(),s=await e.completed();s.connectionKind='native_api';
  const action=twoPhaseAction(s,'save_current',{action_id:'27'.repeat(16),status:'claimed_for_external_execution',execution_outcome_unknown:true,ui_receipt:`ui_${'v'.repeat(43)}`});
  e.AC.receive(s,{next_seq:4,events:statusEvents(4,4,'completed'),app_actions:[action],candidate:clone(s.candidate)});
  assert.equal(e.AC.canApply(s,s.candidate),false);
  const acceptCalls=e.calls.filter(row=>row.name==='agent_accept').length;
  assert.equal(await e.AC.accept(s,clone(s.candidate)),false);
  assert.equal(e.calls.filter(row=>row.name==='agent_accept').length,acceptCalls);
  s.applicationRecoveryPending=true;s.applicationIntent={status:'prepared',intent:{kind:'whiteboard',revision:2,receipt_fingerprint:'receipt-two'}};
  const ackCalls=e.calls.filter(row=>row.name==='agent_candidate_applied').length,abortCalls=e.calls.filter(row=>row.name==='agent_abort_candidate_application').length;
  await e.AC.retryPendingApplication();assert.equal(e.calls.filter(row=>row.name==='agent_candidate_applied').length,ackCalls);assert.match(e.AC.nodes.notice.textContent,/先完成、写回或解除/);
  await e.AC.cancelPendingApplication();assert.equal(e.calls.filter(row=>row.name==='agent_abort_candidate_application').length,abortCalls);assert.match(e.AC.nodes.notice.textContent,/先完成、写回或解除/);
});
test('prepared candidate application blocks messages and explicit cancel rebinds the original document',async()=>{
  const e=setup();await e.ready();const s=e.AC.session('pending-application');e.AC.state.selected=s.session_id;s.kind='whiteboard';s.workflow='staged';s.live=true;s.base=e.AC.capture('whiteboard');s.candidate=candidate();
  e.AC.receive(s,{next_seq:1,events:statusEvents(1,1,'completed'),application_intent:{conversation_id:s.session_id,status:'prepared',intent:{kind:'whiteboard',revision:2,receipt_fingerprint:'receipt-two',document_digest:'d'.repeat(64),state:'prepared'}}});
  assert.equal(e.AC.nodes.send.disabled,true);assert.match(e.AC.nodes.contextWarning.textContent,/候选应用事务尚未完成/);e.AC.nodes.input.value='不应发送';await e.AC.send();assert.ok(e.calls.every(row=>row.name!=='agent_send'));
  const cancel=all(e.AC.nodes.candidate).find(node=>node.tagName==='button'&&node.textContent==='取消未应用事务');assert.ok(cancel);await cancel.emit('click');assert.equal(e.calls.filter(row=>row.name==='agent_abort_candidate_application').length,1);assert.equal(s.applicationRecoveryPending,false);assert.equal(s.pendingApplicationAck,null);assert.equal(e.AC.nodes.send.disabled,false);
});
test('pending candidate application owns the shared document and blocks new-task or session switching',async()=>{
  const e=setup(),owner=e.AC.session('application-owner'),other=e.AC.session('other-session');e.AC.state.selected=owner.session_id;owner.kind='whiteboard';owner.live=true;owner.base=e.AC.capture('whiteboard');owner.applicationRecoveryPending=true;owner.applicationIntent={status:'prepared',intent:{kind:'whiteboard',revision:2,receipt_fingerprint:'receipt-two'}};other.kind='whiteboard';other.live=false;
  e.AC.refresh();assert.equal(e.AC.nodes.newTask.disabled,true);e.AC.newTask();assert.equal(e.AC.state.selected,owner.session_id);assert.match(e.AC.nodes.notice.textContent,/先补记或取消/);
  e.AC.nodes.history.value=other.session_id;assert.equal(await e.AC.selectSession(other.session_id),false);assert.equal(e.AC.state.selected,owner.session_id);assert.equal(e.AC.nodes.history.value,owner.session_id);assert.match(e.AC.nodes.notice.textContent,/待处理会话/);
  e.AC.state.selected=null;e.AC.nodes.input.value='不能绕过事务';const before=e.calls.filter(row=>row.name==='agent_start').length;await e.AC.send();assert.equal(e.calls.filter(row=>row.name==='agent_start').length,before);assert.equal(e.AC.nodes.input.value,'不能绕过事务');
  assert.equal(await e.AC.selectSession(owner.session_id),true);assert.equal(e.AC.state.selected,owner.session_id);
});
test('large whiteboard result uses scalar CAS during repeated chat refreshes',()=>{
  const e=setup(),blob='x'.repeat(16*1024*1024),materialize=e.w.WB.agentPayloadSnapshot;let materializations=0;
  e.w.WB.agentPayloadSnapshot=()=>{materializations++;return materialize();};e.w.WB.setAgentResult({kind:'calculated',blob});
  const base=e.AC.capture('whiteboard'),session=e.AC.session('large-result');session.kind='whiteboard';session.workflow='calculate';session.state='completed';session.live=true;session.base=base;e.AC.state.selected=session.session_id;
  assert.ok(base.payloadJson.length>16*1024*1024);for(let index=0;index<8;index++)e.AC.refresh();
  assert.equal(materializations,1);assert.equal(e.AC.currentMatches(base),true);assert.equal(materializations,1);
});
test('cold history exposes the pending application owner before it is resumed',async()=>{
  const e=setup(),old=e.w.callApi;e.w.callApi=async(name,...args)=>name==='agent_sessions'?{code:0,data:{sessions:[{session_id:'cold-owner',title:'待恢复',kind:'whiteboard',workflow:'staged',state:'cold',connection_kind:'native_api',can_resume:true,pending_application:true,application_intent_status:'prepared',activation:{state:'cold',recovery:null}}]}}:name==='agent_poll'?{code:0,data:{session_id:'cold-owner',state:'ready',active:false,next_seq:0,events:[],application_intent:{conversation_id:'cold-owner',status:'prepared',intent:null,recovery_required:true}}}:name==='agent_resume'?(e.calls.push({name,args:clone(args)}),{code:1,reason:'native_candidate_application_recovery_required',message:'当前仍是应用前原文档'}):name==='agent_abort_candidate_application'?(e.calls.push({name,args:clone(args)}),{code:0,data:{session_id:'cold-owner',requested_after_seq:0,next_seq:0,reset_required:false,events:[],application_intent:{conversation_id:'cold-owner',status:'aborted',intent:{state:'aborted',kind:'whiteboard',revision:1,receipt_fingerprint:'receipt-one'}}}}):old(name,...args);
  await e.ready();await e.AC.loadHistory();const owner=e.AC.session('cold-owner');assert.equal(owner.applicationRecoveryPending,true);assert.equal(owner.coldPendingApplication,true);assert.equal(e.AC.nodes.newTask.disabled,true);assert.equal(e.AC.pendingApplicationSession(),owner);
  assert.equal(await e.AC.selectSession('cold-owner'),true);assert.equal(owner.applicationRecoveryPending,true);e.AC.refresh();assert.match(e.AC.nodes.candidate.textContent,/已停止的会话仍有尚未完成的方案应用/);assert.doesNotMatch(e.AC.nodes.candidate.textContent,/冷会话|Host|runtime|Queue|lineage|revision|cursor/i);let cancel=all(e.AC.nodes.candidate).find(node=>node.tagName==='button'&&node.textContent==='取消未应用事务');assert.ok(cancel);
  await e.AC.resumeSession();assert.equal(owner.live,false);assert.equal(owner.applicationRecoveryPending,true);assert.match(e.AC.nodes.notice.textContent,/当前仍是应用前原文档/);cancel=all(e.AC.nodes.candidate).find(node=>node.tagName==='button'&&node.textContent==='取消未应用事务');assert.ok(cancel);await cancel.emit('click');assert.equal(e.calls.filter(row=>row.name==='agent_abort_candidate_application').length,1);assert.equal(owner.applicationRecoveryPending,false);assert.equal(owner.coldPendingApplication,false);assert.equal(owner.live,true);assert.equal(e.AC.nodes.newTask.disabled,false);
  e.AC.newTask();assert.equal(e.AC.state.selected,null);
});
test('cold pending whiteboard can reproject its durable candidate and acknowledge without provider replay',async()=>{
  const e=setup(),s=e.AC.session('cold-recovery'),before=clone(e.w.WB.state.board),next={...before,title:'崩溃后恢复'},old=e.w.callApi;let appliedBeforeAck=false;
  e.AC.state.selected=s.session_id;s.kind='whiteboard';s.workflow='calculate';s.connectionKind='native_api';s.live=false;s.applicationRecoveryPending=true;s.coldPendingApplication=true;s.applicationIntent={conversation_id:s.session_id,status:'prepared',intent:null,recovery_required:true};
  e.w.callApi=async(name,...args)=>{if(name==='agent_recover_candidate_application'){e.calls.push({name,args:clone(args)});return {code:0,data:{conversation_id:s.session_id,status:'prepared',kind:'whiteboard',workflow:'calculate',application_kind:'whiteboard_calculated',revision:2,receipt_fingerprint:'receipt-two',issues:[],application_intent:{conversation_id:s.session_id,status:'prepared',intent:{kind:'whiteboard',revision:2,receipt_fingerprint:'receipt-two'}},board:clone(next),calculated_delivery:{readonly:true,validation:'full_calculation_package_and_whiteboard_intent',delivery_capability:'wdc_v1_RRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRR',calculated:{result:{summary:{total_damage:789}}}},app_actions:[]}};}
    if(name==='agent_candidate_applied'){e.calls.push({name,args:clone(args)});appliedBeforeAck=e.w.WB.state.board.title===next.title&&e.w.WB.state.result?.result?.summary?.total_damage===789;return {code:0,data:{status:'applied',kind:'whiteboard',revision:2,receipt_fingerprint:'receipt-two',app_actions:[]}};}return old(name,...args);};
  assert.equal(await e.AC.recoverPendingCandidateApplication(),true);assert.equal(appliedBeforeAck,true);const recovery=e.calls.find(row=>row.name==='agent_recover_candidate_application');assert.deepEqual(recovery.args[1],before);assert.deepEqual(recovery.args[3],{kind:'whiteboard',board:before,calculated:null});
  const ack=e.calls.find(row=>row.name==='agent_candidate_applied');assert.deepEqual(ack.args[2],next);assert.deepEqual(ack.args[6],{kind:'whiteboard',board:next,calculated:{result:{summary:{total_damage:789}}}});assert.equal(s.applicationRecoveryPending,false);assert.equal(s.pendingApplicationAck,null);assert.equal(s.live,true);assert.equal(s.applied,true);assert.ok(e.calls.every(row=>!['agent_start','agent_send'].includes(row.name)));
});
test('candidate recovery rejects a path-bearing calculated delivery before changing the whiteboard',async()=>{
  const e=setup(),s=e.AC.session('unsafe-recovery'),before=clone(e.w.WB.state.board),old=e.w.callApi;e.AC.state.selected=s.session_id;s.kind='whiteboard';s.workflow='calculate';s.connectionKind='native_api';s.applicationRecoveryPending=true;
  e.w.callApi=async(name,...args)=>name==='agent_recover_candidate_application'?{code:0,data:{kind:'whiteboard',workflow:'calculate',application_kind:'whiteboard_calculated',revision:2,receipt_fingerprint:'receipt-two',application_intent:{status:'prepared',intent:{kind:'whiteboard',revision:2,receipt_fingerprint:'receipt-two'}},board:{...before,title:'不应应用'},calculated_delivery:{path:'private/result.json',delivery_capability:'wdc_v1_RRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRR',calculated:{result:{}}}}}:old(name,...args);
  assert.equal(await e.AC.recoverPendingCandidateApplication(),false);assert.deepEqual(clone(e.w.WB.state.board),before);assert.equal(s.applicationRecoveryPending,true);assert.equal(s.pendingApplicationAck,undefined);assert.match(e.AC.nodes.notice.textContent,/无路径计算交付凭证/);
});
test('cold committed whiteboard rehydrates its unsaved calculation projection before resume',async()=>{
  const e=setup();await e.ready();const s=e.AC.session('committed-rehydrate'),before=clone(e.w.WB.state.board),old=e.w.callApi;let resumes=0;s.kind='whiteboard';s.workflow='calculate';s.connectionKind='native_api';s.live=false;s.canResume=true;s.candidate=candidate();e.AC.state.selected=s.session_id;
  e.w.callApi=async(name,...args)=>{if(name==='agent_resume'){e.calls.push({name,args:clone(args)});resumes++;if(resumes===1)return {code:1,reason:'native_candidate_application_projection_required',message:'需要恢复计算投影'};assert.equal(args[3].calculated.result.summary.total_damage,654);return {code:0,data:{session_id:s.session_id,state:'completed',active:false,kind:'whiteboard',workflow:'calculate',next_seq:0,events:[],candidate:clone(s.candidate),application_intent:{conversation_id:s.session_id,status:'committed',intent:{state:'committed',kind:'whiteboard',revision:2,receipt_fingerprint:'receipt-two'}}}};}
    if(name==='agent_recover_candidate_application'){e.calls.push({name,args:clone(args)});assert.equal(args[3].calculated,null);return {code:0,data:{conversation_id:s.session_id,status:'committed',kind:'whiteboard',workflow:'calculate',application_kind:'whiteboard_calculated',revision:2,receipt_fingerprint:'receipt-two',issues:[],application_intent:{conversation_id:s.session_id,status:'committed',intent:{state:'committed',kind:'whiteboard',revision:2,receipt_fingerprint:'receipt-two'}},board:clone(before),calculated_delivery:{delivery_capability:'wdc_v1_DDDDDDDDDDDDDDDDDDDDDDDDDDDDDDDDDDDDDDDDDDD',calculated:{result:{summary:{total_damage:654}}}},app_actions:[]}};}return old(name,...args);};
  await e.AC.resumeSession();assert.equal(resumes,2);assert.equal(e.calls.filter(row=>row.name==='agent_recover_candidate_application').length,1);assert.equal(e.w.WB.state.result.result.summary.total_damage,654);assert.equal(s.live,true);assert.equal(s.canResume,false);assert.equal(s.applied,true);assert.equal(s.appliedReceipt,'receipt-two');assert.equal(e.AC.currentMatches(s.base),true);assert.ok(e.calls.every(row=>!['agent_start','agent_send','agent_candidate_applied'].includes(row.name)));
});
test('cleared intent automatically restores only the durable result and preserves whiteboard viewport state',async()=>{
  const e=setup();await e.ready();const s=e.AC.session('last-result-only'),before=e.w.WB.state.board,old=e.w.callApi;let resumes=0;
  s.kind='whiteboard';s.workflow='calculate';s.connectionKind='native_api';s.live=false;s.canResume=true;e.AC.state.selected=s.session_id;
  e.w.WB.state.savedJson=JSON.stringify(before);e.w.WB.state.selectedActor='a';e.w.WB.state.selectedAction='skill';e.w.WB.state.selectedDerived='keep-derived';e.w.WB.state.scale=4;e.w.WB.state.scroll=377;e.w.WB.state.cursor=6.5;e.w.WB.state.page=3;
  const projection={board_id:before.board_id,revision:7,receipt_fingerprint:'b'.repeat(64),document_digest:'c'.repeat(64),application_payload_digest:'d'.repeat(64),application_kind:'whiteboard_calculated',has_calculated:true};
  e.w.callApi=async(name,...args)=>{
    if(name==='agent_resume'){e.calls.push({name,args:clone(args)});resumes++;if(resumes===1)return {code:1,reason:'native_last_applied_projection_required',message:'恢复结果'};assert.equal(args[3].calculated.result.summary.total_damage,4321);return {code:0,data:{session_id:s.session_id,state:'completed',active:false,kind:'whiteboard',workflow:'calculate',next_seq:0,events:[],application_intent:{conversation_id:s.session_id,status:'none',intent:null}}};}
    if(name==='agent_recover_last_applied_whiteboard_projection'){e.calls.push({name,args:clone(args)});assert.equal(args[4],false);return {code:0,data:{conversation_id:s.session_id,status:'recovered',kind:'whiteboard',recovery_mode:'result_only',application_kind:'whiteboard_calculated',revision:7,receipt_fingerprint:'b'.repeat(64),projection,board:clone(before),calculated_delivery:{delivery_capability:`wdc_v1_${'R'.repeat(43)}`,calculated:{result:{summary:{total_damage:4321}}}},app_actions:[]}};}
    return old(name,...args);
  };
  await e.AC.resumeSession();assert.equal(resumes,2);assert.equal(e.w.WB.state.board,before);assert.equal(e.w.WB.state.path,'board.json');assert.equal(e.w.WB.state.fileSha,'board-sha');assert.equal(e.w.WB.state.savedJson,JSON.stringify(before));assert.equal(e.w.WB.state.selectedActor,'a');assert.equal(e.w.WB.state.selectedAction,'skill');assert.equal(e.w.WB.state.selectedDerived,'keep-derived');assert.equal(e.w.WB.state.scale,4);assert.equal(e.w.WB.state.scroll,377);assert.equal(e.w.WB.state.cursor,6.5);assert.equal(e.w.WB.state.page,3);assert.equal(e.w.WB.state.mode,'damage');assert.equal(s.live,true);assert.ok(e.calls.every(row=>!['agent_start','agent_send'].includes(row.name)));
});
test('unsaved applied target is never automatic and restores only after explicit confirmation',async()=>{
  const e=setup();await e.ready();const s=e.AC.session('last-unsaved-target'),before=clone(e.w.WB.state.board),target={...before,title:'Host已应用白板',revision:1},old=e.w.callApi;let confirms=0,resumes=0;
  e.w.confirm=()=>{confirms++;return true;};s.kind='whiteboard';s.workflow='staged';s.connectionKind='native_api';s.live=false;s.canResume=true;e.AC.state.selected=s.session_id;e.w.WB.state.savedJson=JSON.stringify(before);
  const projection={board_id:before.board_id,revision:8,receipt_fingerprint:'e'.repeat(64),document_digest:'f'.repeat(64),application_payload_digest:'a'.repeat(64),application_kind:'whiteboard_calculated',has_calculated:true};
  e.w.callApi=async(name,...args)=>{
    if(name==='agent_resume'){e.calls.push({name,args:clone(args)});resumes++;if(resumes===1)return {code:1,reason:'native_last_applied_unsaved_target_restore_required',message:'需要确认'};assert.equal(args[1].title,target.title);assert.equal(args[3].calculated.result.summary.total_damage,7654);return {code:0,data:{session_id:s.session_id,state:'completed',active:false,kind:'whiteboard',workflow:'staged',next_seq:0,events:[],application_intent:{conversation_id:s.session_id,status:'none',intent:null}}};}
    if(name==='agent_recover_last_applied_whiteboard_projection'){e.calls.push({name,args:clone(args)});assert.equal(args[4],true);return {code:0,data:{conversation_id:s.session_id,status:'recovered',kind:'whiteboard',recovery_mode:'restore_unsaved_target',application_kind:'whiteboard_calculated',revision:8,receipt_fingerprint:'e'.repeat(64),projection,board:clone(target),calculated_delivery:{delivery_capability:`wdc_v1_${'S'.repeat(43)}`,calculated:{result:{summary:{total_damage:7654}}}},app_actions:[]}};}
    return old(name,...args);
  };
  await e.AC.resumeSession();assert.equal(resumes,1);assert.equal(confirms,0);assert.deepEqual(clone(e.w.WB.state.board),before);assert.equal(s.lastAppliedRestorePending,true);const restore=all(e.AC.nodes.candidate).find(node=>node.tagName==='button'&&node.textContent==='确认恢复上次未保存白板');assert.ok(restore);await restore.emit('click');assert.equal(confirms,1);assert.equal(resumes,2);assert.deepEqual(clone(e.w.WB.state.board),target);assert.equal(e.w.WB.state.path,'board.json');assert.equal(e.w.WB.state.fileSha,'board-sha');assert.equal(e.w.WB.state.savedJson,JSON.stringify(before));assert.equal(e.w.WB.dirty(),true);assert.equal(s.lastAppliedRestorePending,false);assert.equal(s.live,true);assert.equal(all(e.AC.nodes.candidate).some(node=>node.tagName==='button'&&node.textContent==='确认恢复上次未保存白板'),false);assert.ok(e.calls.every(row=>!['agent_start','agent_send'].includes(row.name)));
});
test('declining unsaved target recovery leaves the whiteboard untouched',async()=>{
  const e=setup(),s=e.AC.session('decline-unsaved'),before=clone(e.w.WB.state.board);s.kind='whiteboard';s.live=false;s.lastAppliedRestorePending=true;e.AC.state.selected=s.session_id;e.w.confirm=()=>false;
  assert.equal(await e.AC.rehydrateLastAppliedProjection(s,null,true),false);assert.deepEqual(clone(e.w.WB.state.board),before);assert.ok(e.calls.every(row=>row.name!=='agent_recover_last_applied_whiteboard_projection'));
});
test('last-applied result recovery uses tab CAS before changing UI',async()=>{
  const e=setup(),s=e.AC.session('late-last-result'),before=clone(e.w.WB.state.board),old=e.w.callApi;let finish;s.kind='whiteboard';s.live=false;e.AC.state.selected=s.session_id;
  e.w.callApi=async(name,...args)=>name==='agent_recover_last_applied_whiteboard_projection'?(e.calls.push({name,args:clone(args)}),new Promise(resolve=>finish=resolve)):old(name,...args);
  const pending=e.AC.rehydrateLastAppliedProjection(s,e.AC.capture('whiteboard'),false);e.tab('rotation');finish({code:0,data:{conversation_id:s.session_id,status:'recovered',kind:'whiteboard',recovery_mode:'result_only',application_kind:'whiteboard_calculated',revision:1,receipt_fingerprint:'b'.repeat(64),projection:{board_id:before.board_id,revision:1,receipt_fingerprint:'b'.repeat(64),application_kind:'whiteboard_calculated'},board:clone(before),calculated_delivery:{delivery_capability:`wdc_v1_${'T'.repeat(43)}`,calculated:{result:{summary:{total_damage:1}}}}}});
  assert.equal(await pending,false);assert.equal(e.w.WB.state.result,null);assert.deepEqual(clone(e.w.WB.state.board),before);assert.match(e.AC.nodes.notice.textContent,/页签发生变化/);
});
for(const mutation of ['view epoch','session epoch','document'])test(`last-applied recovery rejects changed ${mutation} before changing UI`,async()=>{
  const e=setup(),s=e.AC.session(`late-${mutation}`),before=clone(e.w.WB.state.board),old=e.w.callApi;let finish;s.kind='whiteboard';s.live=false;e.AC.state.selected=s.session_id;
  e.w.callApi=async(name,...args)=>name==='agent_recover_last_applied_whiteboard_projection'?(e.calls.push({name,args:clone(args)}),new Promise(resolve=>finish=resolve)):old(name,...args);
  const pending=e.AC.rehydrateLastAppliedProjection(s,e.AC.capture('whiteboard'),false);if(mutation==='view epoch')e.AC.state.viewEpoch++;else if(mutation==='session epoch')s.epoch++;else e.w.WB.change(current=>current.title='用户并发编辑');
  finish({code:0,data:{conversation_id:s.session_id,status:'recovered',kind:'whiteboard',recovery_mode:'result_only',application_kind:'whiteboard_calculated',revision:1,receipt_fingerprint:'b'.repeat(64),projection:{board_id:before.board_id,revision:1,receipt_fingerprint:'b'.repeat(64),application_kind:'whiteboard_calculated'},board:clone(before),calculated_delivery:{delivery_capability:`wdc_v1_${'U'.repeat(43)}`,calculated:{result:{summary:{total_damage:1}}}}}});
  assert.equal(await pending,false);assert.equal(e.w.WB.state.result,null);if(mutation==='document')assert.equal(e.w.WB.state.board.title,'用户并发编辑');else assert.deepEqual(clone(e.w.WB.state.board),before);
});
test('last-applied conflict is explicit and never requests recovery',async()=>{
  const e=setup();await e.ready();const s=e.AC.session('last-conflict'),old=e.w.callApi;s.kind='whiteboard';s.connectionKind='native_api';s.live=false;s.canResume=true;e.AC.state.selected=s.session_id;
  e.w.callApi=async(name,...args)=>name==='agent_resume'?(e.calls.push({name,args:clone(args)}),{code:1,reason:'native_last_applied_projection_conflict',message:'当前白板与上次投影冲突'}):old(name,...args);
  await e.AC.resumeSession();assert.equal(s.live,false);assert.match(e.AC.nodes.notice.textContent,/冲突/);assert.ok(e.calls.every(row=>row.name!=='agent_recover_last_applied_whiteboard_projection'));
});
test('committed transaction projection clears a stale pending acknowledgement without leaving send locked',async()=>{
  const e=setup();await e.ready();const s=e.AC.session('committed-application');e.AC.state.selected=s.session_id;s.kind='whiteboard';s.workflow='staged';s.live=true;s.base=e.AC.capture('whiteboard');s.candidate=candidate();s.pendingApplicationAck={revision:2,receipt:'receipt-two',base:s.base};
  e.AC.receive(s,{next_seq:1,events:statusEvents(1,1,'completed'),application_intent:{conversation_id:s.session_id,status:'committed',intent:{kind:'whiteboard',revision:2,receipt_fingerprint:'receipt-two',document_digest:'d'.repeat(64),state:'committed'}}});
  assert.equal(s.pendingApplicationAck,null);assert.equal(s.applicationRecoveryPending,false);assert.equal(s.applied,true);assert.equal(s.appliedReceipt,'receipt-two');assert.equal(e.AC.nodes.send.disabled,false);
});
test('lost abort response is recovered only from an explicit aborted tombstone',()=>{
  const e=setup(),s=e.AC.session('aborted-application');e.AC.state.selected=s.session_id;s.kind='whiteboard';s.live=true;s.base=e.AC.capture('whiteboard');s.pendingApplicationAck={revision:2,receipt:'receipt-two',base:s.base};s.applicationRecoveryPending=true;
  e.AC.receive(s,{next_seq:1,events:statusEvents(1,1,'completed'),application_intent:{conversation_id:s.session_id,status:'none',intent:null}});assert.ok(s.pendingApplicationAck);assert.equal(e.AC.hasPendingApplication(s),true);
  e.AC.receiveCommand(s,{next_seq:2,events:statusEvents(2,2,'completed'),application_intent:{conversation_id:s.session_id,status:'aborted',intent:{kind:'whiteboard',revision:2,receipt_fingerprint:'receipt-two',document_digest:'d'.repeat(64),baseline_document_digest:'b'.repeat(64),state:'aborted'}}},1,{applicationSettlement:'abort'});
  assert.equal(s.pendingApplicationAck,null);assert.equal(s.applicationRecoveryPending,false);assert.equal(s.applied,false);assert.equal(s.appliedReceipt,null);assert.equal(e.AC.hasPendingApplication(s),false);
});
test('queued App save uses the guarded existing-path save and reports completion without exposing a path',async()=>{
  const e=setup(),s=e.AC.session('actions');e.AC.state.selected=s.session_id;s.kind='whiteboard';s.live=true;s.base=e.AC.capture('whiteboard');e.w.WB.save=async()=>{e.orders.push('guarded-save');return true;};
  const action=readyFileAction(s,'save_current',{action_id:'a'.repeat(32),reason:'保存已审核草稿',workspace:{document_kind:'whiteboard',has_path:true,can_save:true}});
  e.AC.receive(s,{next_seq:1,events:[{seq:1,type:'app_action',action}]});const card=e.AC.actionNodes.get(action.action_id);assert.match(card.textContent,/保存已审核草稿/);
  await all(card).find(node=>node.tagName==='button'&&node.textContent==='处理').emit('click');const resolved=e.calls.find(c=>c.name==='agent_resolve_app_action');
  assert.equal(resolved.args[2],'completed');assert.equal(e.orders.includes('guarded-save'),true);assert.equal(resolved.args[3].has_path,true);assert.equal(JSON.stringify(resolved.args[3]).includes('board.json'),false);assert.equal(s.appActions[0].status,'completed');assert.equal(s.appActions[0].reason,'保存已审核草稿');
});
test('file picker actions are presented rather than falsely reported as completed',async()=>{
  const e=setup(),s=e.AC.session('picker');e.AC.state.selected=s.session_id;s.kind='whiteboard';s.live=true;s.base=e.AC.capture('whiteboard');
  const action={action_id:'b'.repeat(32),action:'open_document',reason:'选择另一个方案',status:'queued'};e.AC.receive(s,{next_seq:1,events:[{seq:1,type:'app_action',action}]});
  await all(e.AC.actionNodes.get(action.action_id)).find(node=>node.tagName==='button'&&node.textContent==='处理').emit('click');const resolved=e.calls.find(c=>c.name==='agent_resolve_app_action');
  assert.equal(e.orders.includes('open-file'),true);assert.equal(resolved.args[2],'presented');assert.match(resolved.args[4],/等待用户打开或取消/);assert.equal(s.appActions[0].status,'presented');assert.match(e.AC.actionNodes.get(action.action_id).textContent,/操作界面已呈现/);
});
test('export request opens the report choices but does not invent a format or claim a file export',async()=>{
  const e=setup();e.tab('rotation');e.w.RS.state.result={summary:{total_damage:1}};const s=e.AC.session('export');e.AC.state.selected=s.session_id;s.kind='native';s.live=true;s.base=e.AC.capture('native');
  const action=readyFileAction(s,'export_result',{action_id:'e'.repeat(32),reason:'导出本轮结果'});e.AC.receive(s,{next_seq:1,events:[{seq:1,type:'app_action',action}]});await all(e.AC.actionNodes.get(action.action_id)).find(node=>node.tagName==='button'&&node.textContent==='处理').emit('click');await tick();
  const resolved=e.calls.find(c=>c.name==='agent_resolve_app_action');assert.equal(e.orders.includes('navigate:result'),true);assert.equal(resolved.args[2],'presented');assert.match(resolved.args[4],/请求标识/);assert.ok(e.calls.every(c=>!['export_plan_result','export_result'].includes(c.name)));
  const dialog=e.body.children.find(node=>node.tagName==='dialog'&&node.dataset?.agentActionId===action.action_id);assert.ok(dialog);assert.equal(e.w.AppDialogKernel.getActive(),dialog);
  await all(dialog).find(node=>node.tagName==='button'&&node.textContent==='结构化结果').emit('click');await tick();
  const lifecycle=e.calls.filter(c=>c.name==='agent_resolve_app_action');assert.deepEqual(lifecycle.map(c=>c.args[2]),['presented','completed']);assert.equal(e.orders.includes('export:json'),true);
});
test('navigation actions execute through the App and can be declined explicitly',async()=>{
  const e=setup(),s=e.AC.session('navigation');e.AC.state.selected=s.session_id;s.kind='whiteboard';s.live=true;
  const move={action_id:'c'.repeat(32),action:'navigate_rotation',reason:'查看高级轴',status:'queued'},decline={action_id:'d'.repeat(32),action:'navigate_result',reason:'查看结果',status:'queued'};
  e.AC.receive(s,{next_seq:2,events:[{seq:1,type:'app_action',action:move},{seq:2,type:'app_action',action:decline}]});await all(e.AC.actionNodes.get(move.action_id)).find(node=>node.tagName==='button'&&node.textContent==='处理').emit('click');
  await all(e.AC.actionNodes.get(decline.action_id)).find(node=>node.tagName==='button'&&node.textContent==='不执行').emit('click');const calls=e.calls.filter(c=>c.name==='agent_resolve_app_action');
  assert.equal(e.orders.includes('navigate:rotation'),true);assert.equal(calls[0].args[2],'completed');assert.equal(calls[1].args[2],'cancelled');
});
test('navigation is completed only after the App confirms the target view',async()=>{
  const e=setup(),s=e.AC.session('navigation-failure');e.AC.state.selected=s.session_id;s.kind='whiteboard';s.live=true;e.w.WS.navigate=value=>e.orders.push(`ignored:${value}`);
  const action={action_id:'9'.repeat(32),action:'navigate_result',reason:'验证导航',status:'queued'};e.AC.receive(s,{next_seq:1,events:[{seq:1,type:'app_action',action}]});await all(e.AC.actionNodes.get(action.action_id)).find(node=>node.tagName==='button'&&node.textContent==='处理').emit('click');await tick();
  const resolved=e.calls.find(c=>c.name==='agent_resolve_app_action');assert.equal(resolved.args[2],'failed');assert.match(resolved.args[4],/未确认已切换/);assert.equal(s.appActions[0].status,'failed');
});
test('running Agent disables every file action while navigation remains available',async()=>{
  const e=setup(),s=e.AC.session('active-actions');e.AC.state.selected=s.session_id;s.kind='whiteboard';s.live=true;s.base=e.AC.capture('whiteboard');
  const save=readyFileAction(s,'save_current',{action_id:'1'.repeat(32),reason:'运行中不可写盘'}),open=readyFileAction(s,'open_document',{action_id:'2'.repeat(32),reason:'运行中不可换文档'}),move={action_id:'3'.repeat(32),action:'navigate_rotation',reason:'允许查看',status:'queued'};
  e.AC.receive(s,{state:'running',active:true,next_seq:3,events:[{seq:1,type:'app_action',action:save},{seq:2,type:'app_action',action:open},{seq:3,type:'app_action',action:move}]});
  for(const action of [save,open]){const card=e.AC.actionNodes.get(action.action_id),run=all(card).find(node=>node.tagName==='button'&&node.textContent==='处理');assert.equal(run.disabled,true);assert.match(card.textContent,/运行期间文件操作已暂停/);await e.AC.executeAppAction(s,s.appActions.find(row=>row.action_id===action.action_id));}
  const navigation=all(e.AC.actionNodes.get(move.action_id)).find(node=>node.tagName==='button'&&node.textContent==='处理');assert.equal(navigation.disabled,false);await navigation.emit('click');assert.equal(e.orders.includes('navigate:rotation'),true);assert.ok(e.orders.every(row=>!['guarded-save','open-file'].includes(row)));
});
test('candidate-bound file actions stay disabled until the exact candidate is applied and backend refreshes readiness',()=>{
  const e=setup(),s=e.AC.session('preconditions');e.AC.state.selected=s.session_id;s.kind='whiteboard';s.live=true;s.base=e.AC.capture('whiteboard');s.candidate=candidate();
  const action=readyFileAction(s,'save_as',{action_id:'4'.repeat(32),reason:'仅保存已接纳候选',preconditions:{contract:'agent-app-action-preconditions-v1',status:'blocked',issues:[{code:'candidate_not_applied',message:'候选尚未应用'}],candidate_revision:2,receipt_fingerprint:'receipt-two',requires_applied_candidate:true}});s.applied=false;s.appliedReceipt=null;
  e.AC.receive(s,{next_seq:1,events:[{seq:1,type:'app_action',action}]});let card=e.AC.actionNodes.get(action.action_id);assert.equal(all(card).find(node=>node.tagName==='button'&&node.textContent==='处理').disabled,true);assert.match(card.textContent,/候选尚未应用/);assert.match(card.textContent,/来源：候选修订 2/);
  s.applied=true;s.appliedReceipt='receipt-two';e.AC.recordAppAction(s,{...action,preconditions:{...action.preconditions,status:'ready',issues:[]}});e.AC.refresh();card=e.AC.actionNodes.get(action.action_id);assert.equal(all(card).find(node=>node.tagName==='button'&&node.textContent==='处理').disabled,false);
  s.candidate={...candidate(),revision:3,receipt_fingerprint:'new-receipt'};s.appliedReceipt='new-receipt';e.AC.refresh();card=e.AC.actionNodes.get(action.action_id);assert.equal(all(card).find(node=>node.tagName==='button'&&node.textContent==='处理').disabled,true);assert.match(card.textContent,/候选修订已变化/);
});
test('candidate acceptance merges refreshed backend action preconditions before enabling a file write',async()=>{
  const e=setup(),s=await e.completed(),action={action_id:'a1'.repeat(16),action:'save_current',reason:'应用后保存',status:'queued',candidate_revision:2,receipt_fingerprint:'receipt-two',requires_applied_candidate:true,workspace:{document_kind:'whiteboard',can_save:true},preconditions:{contract:'agent-app-action-preconditions-v1',status:'blocked',issues:[{code:'candidate_not_applied',message:'请先应用'}],candidate_revision:2,receipt_fingerprint:'receipt-two',requires_applied_candidate:true}};
  e.AC.recordAppAction(s,action);e.AC.refresh();const old=e.w.callApi;e.w.callApi=async(name,...args)=>{if(name==='agent_accept'){const response=await old(name,...args);response.data.app_actions=[{...action,preconditions:{...action.preconditions,status:'ready',issues:[]}}];return response;}return old(name,...args);};
  assert.equal(await e.AC.accept(s,clone(s.candidate)),true);const stored=s.appActions.find(row=>row.action_id===action.action_id),card=e.AC.actionNodes.get(action.action_id);assert.equal(stored.preconditions.status,'ready');assert.equal(all(card).find(node=>node.tagName==='button'&&node.textContent==='处理').disabled,false);
});
test('recovered history and changed live baselines cannot execute queued file actions',async()=>{
  for(const mode of ['history','changed']){const e=setup(),s=e.AC.session(`unsafe-${mode}`);e.AC.state.selected=s.session_id;s.kind='whiteboard';s.live=mode==='changed';s.base=mode==='changed'?e.AC.capture('whiteboard'):null;
    const action={action_id:(mode==='history'?'5':'6').repeat(32),action:'open_document',reason:'不得作用于无关文档',status:'queued',requires_applied_candidate:false,preconditions:{contract:'agent-app-action-preconditions-v1',status:'ready',issues:[],requires_applied_candidate:false}};
    if(mode==='changed')e.w.WB.change(current=>current.team[0].panel.atk=9876);e.AC.receive(s,{next_seq:1,events:[{seq:1,type:'app_action',action}]});const card=e.AC.actionNodes.get(action.action_id),run=all(card).find(node=>node.tagName==='button'&&node.textContent==='处理');assert.equal(run.disabled,true);assert.match(card.textContent,mode==='history'?/没有可核验的实时冻结工作区/:/不同于此操作绑定的工作区/);await e.AC.executeAppAction(s,s.appActions[0]);assert.equal(e.orders.includes('open-file'),false);
  }
});
test('open lifecycle carries its action id through presented then completed or cancelled receipts',async()=>{
  for(const finalStatus of ['completed','cancelled']){const e=setup(),s=e.AC.session(`open-${finalStatus}`);e.AC.state.selected=s.session_id;s.kind='whiteboard';s.live=true;s.base=e.AC.capture('whiteboard');
    const action={action_id:(finalStatus==='completed'?'7':'8').repeat(32),action:'open_document',reason:'生命周期测试',status:'queued',requires_applied_candidate:false,preconditions:{contract:'agent-app-action-preconditions-v1',status:'ready',issues:[],requires_applied_candidate:false}};
    e.AC.receive(s,{next_seq:1,events:[{seq:1,type:'app_action',action}]});await all(e.AC.actionNodes.get(action.action_id)).find(node=>node.tagName==='button'&&node.textContent==='处理').emit('click');await tick();const options=e.fileActions.at(-1);assert.equal(options.agentActionId,action.action_id);
    options.onAgentActionResult({status:finalStatus,message:finalStatus==='completed'?'App确认打开':'用户取消'});await tick();assert.deepEqual(e.calls.filter(c=>c.name==='agent_resolve_app_action').map(c=>c.args[2]),['presented',finalStatus]);assert.equal(s.appActions[0].status,finalStatus);
  }
});
test('missing file UI controller fails the request without claiming it was presented',async()=>{
  const e=setup(),s=e.AC.session('missing-file-ui');e.AC.state.selected=s.session_id;s.kind='whiteboard';s.live=true;s.base=e.AC.capture('whiteboard');e.w.WS.openFile=undefined;
  const action={action_id:'0'.repeat(32),action:'open_document',reason:'不存在的打开入口',status:'queued',requires_applied_candidate:false,preconditions:{contract:'agent-app-action-preconditions-v1',status:'ready',issues:[],requires_applied_candidate:false}};
  e.AC.receive(s,{next_seq:1,events:[{seq:1,type:'app_action',action}]});await all(e.AC.actionNodes.get(action.action_id)).find(node=>node.tagName==='button'&&node.textContent==='处理').emit('click');await tick();
  const calls=e.calls.filter(c=>c.name==='agent_resolve_app_action');assert.deepEqual(calls.map(c=>c.args[2]),['failed']);assert.match(calls[0].args[4],/没有确认文件操作界面/);assert.equal(s.appActions[0].status,'failed');
});
test('usage events expose token counts only and never render provider reasoning',()=>{
  const e=setup(),s=e.AC.session('usage');e.AC.state.selected=s.session_id;s.kind='whiteboard';e.AC.receive(s,{next_seq:1,events:[{seq:1,type:'usage',usage:{input_tokens:120,output_tokens:30,total_tokens:150,reasoning_tokens:18,cached_input_tokens:42},reasoning:'private chain'}]});
  assert.match(e.AC.nodes.usage.textContent,/本会话API用量.*输入 120.*输出 30.*合计 150.*推理 18.*缓存输入 42/);assert.doesNotMatch(e.root.textContent,/private chain/);assert.equal(e.AC.nodes.send.disabled,true);
});
test('reasoning message identities render as collapsible content instead of an ordinary answer',()=>{
  const e=setup(),s=e.AC.session('reasoning-visible');e.AC.state.selected=s.session_id;s.kind='whiteboard';e.AC.receive(s,{next_seq:2,events:[{seq:1,type:'delta',role:'assistant',message_id:'reasoning:abc',delta:'正在核对'},{seq:2,type:'message',role:'assistant',message_id:'reasoning:abc',message:'正在核对机制'}]});
  const node=e.AC.messageNodes.get('reasoning:abc');assert.ok(node);assert.ok(node.acMessage.disclosure);assert.match(node.textContent,/推理过程.*正在核对机制/s);assert.doesNotMatch(node.textContent,/Agent/);
});
test('runtime budget status visibly escalates at 70, 90 and 100 percent and resets for the next turn',()=>{
  const e=setup(),s=e.AC.session('budget-progress');e.AC.state.selected=s.session_id;s.kind='whiteboard';
  e.AC.receive(s,{run_id:'run-one',state:'running',active:true,next_seq:1,events:[{seq:1,run_id:'run-one',type:'budget_status',status:'budget_progress_70',level:'notice',percent:70,message:'本轮至少一项预算已使用 70%；Agent 仍会继续执行。'}]});
  assert.equal(e.AC.nodes.budgetProgress.hidden,false);assert.match(e.AC.nodes.budgetProgress.textContent,/预算提醒.*70%/);assert.equal(e.AC.nodes.budgetProgress.attributes['data-level'],'notice');
  e.AC.receive(s,{run_id:'run-one',state:'running',active:true,next_seq:2,events:[{seq:2,run_id:'run-one',type:'budget_status',status:'budget_closure_90',level:'warning',percent:90,message:'本轮至少一项预算已使用 90%；Agent 应优先闭合当前结果并保存候选。'}]});
  assert.match(e.AC.nodes.budgetProgress.textContent,/预算收尾提醒.*90%/);assert.doesNotMatch(e.AC.nodes.budgetProgress.textContent,/70%/);assert.equal(e.AC.nodes.budgetProgress.attributes['data-level'],'warning');
  e.AC.receive(s,{run_id:'run-one',state:'running',active:true,next_seq:3,events:[{seq:3,run_id:'run-one',type:'budget_status',status:'budget_context_prune_required',level:'warning',message:'本轮上下文已达到裁剪阈值。'}]});
  assert.match(e.AC.nodes.budgetProgress.textContent,/预算收尾提醒.*上下文整理待处理/s);assert.equal(e.AC.nodes.budgetProgress.attributes['data-compaction'],'true');
  e.AC.receive(s,{run_id:'run-one',state:'stopped',active:false,next_seq:4,events:[{seq:4,run_id:'run-one',type:'budget_status',status:'budget_exhausted_100',level:'error',percent:100,message:'本轮预算已用尽；系统正在停止本轮。'}]});
  assert.match(e.AC.nodes.budgetProgress.textContent,/预算上限.*用尽/);assert.doesNotMatch(e.AC.nodes.budgetProgress.textContent,/90%/);assert.equal(e.AC.nodes.budgetProgress.attributes['data-level'],'error');
  e.AC.receive(s,{run_id:'run-two',state:'running',active:true,next_seq:5,events:[{seq:5,run_id:'run-two',type:'status',state:'running'}]});
  assert.equal(e.AC.nodes.budgetProgress.hidden,true);assert.equal(e.AC.nodes.budgetProgress.textContent,'');
});
test('new messages do not pull the reader away from older content',()=>{
  const e=setup(),s=e.AC.session('scroll');s.kind='whiteboard';e.AC.state.selected=s.session_id;e.AC.nodes.messages.scrollHeight=1000;e.AC.nodes.messages.clientHeight=200;e.AC.nodes.messages.scrollTop=100;
  e.AC.receive(s,{next_seq:1,events:[{seq:1,type:'message',message_id:'new',message:'新消息'}]});assert.equal(e.AC.nodes.messages.scrollTop,100);assert.equal(e.AC.nodes.newMessages.hidden,false);
});
test('hide does not stop a task, stop waits for response, and a late poll cannot revive it',async()=>{
  const e=setup(),s=await e.start();e.AC.close();assert.ok(e.calls.every(c=>c.name!=='agent_stop'));
  let finish;const old=e.w.callApi;e.w.callApi=async(name,...args)=>name==='agent_poll'?new Promise(resolve=>finish=resolve):old(name,...args);const polling=e.AC.poll(s,true);
  await e.AC.stop(s);assert.equal(s.state,'stopped');assert.equal(s.active,false);finish({code:0,data:{session_id:s.session_id,state:'running',active:true,next_seq:3,events:[]}});await polling;assert.equal(s.active,false);
});
test('explicit followup sends the currently frozen input and has its own idempotency key',async()=>{
  const e=setup(),s=await e.completed();e.AC.nodes.input.value='继续检查附着';await e.AC.send();const call=e.calls.find(c=>c.name==='agent_send');assert.equal(call.args[0],s.session_id);assert.equal(call.args[1],'继续检查附着');assert.ok(call.args[2]);assert.deepEqual(call.args[3],clone(e.w.WB.state.board));
  assert.equal(call.args[4].document_kind,'whiteboard');assert.equal(call.args[4].has_path,true);assert.equal(JSON.stringify(call.args[4]).includes('board.json'),false);
  assert.deepEqual(call.args[5],{kind:'whiteboard',board:clone(e.w.WB.state.board),calculated:null});
  assert.equal(e.calls.filter(c=>c.name==='agent_start').length,1);assert.equal(s.base.document.team[0].panel.atk,2000);
});
test('calculation-only changes invalidate a frozen continuation before it reaches the Host',async()=>{
  const e=setup(),s=await e.completed();e.w.WB.setAgentResult({result:{summary:{total_damage:99}}});e.AC.nodes.input.value='不应发送旧基线';await e.AC.send();
  assert.ok(e.calls.every(c=>c.name!=='agent_send'));assert.equal(e.AC.nodes.input.value,'不应发送旧基线');assert.match(e.AC.nodes.notice.textContent,/当前输入已在聊天外变化/);assert.equal(e.AC.currentMatches(s.base),false);
});
test('cold history selection never infers and explicit resume safely rebinds the current document',async()=>{
  const e=setup();await e.ready();const old=e.w.callApi,s=e.AC.session('historic');s.kind='whiteboard';s.title='旧记录';s.canResume=true;
  e.w.callApi=async(name,...args)=>{if(name==='agent_poll')return {code:0,data:{session_id:args[0],state:'completed',active:false,next_seq:0,events:[],can_resume:true}};if(name==='agent_resume'){e.calls.push({name,args:clone(args)});return {code:0,data:{session_id:args[0],resumed:true,state:'ready',active:false,kind:'whiteboard',workflow:'staged',title:'旧记录',next_seq:4,events:statusEvents(1,4,'completed'),candidate:candidate(),activation:{conversation_id:args[0],activation_generation:1,resume_pending:true}}};}return old(name,...args);};
  await e.AC.selectSession(s.session_id);assert.ok(e.calls.every(c=>!['agent_start','agent_send'].includes(c.name)));assert.equal(e.AC.nodes.send.disabled,true);assert.match(e.AC.nodes.contextWarning.textContent,/冷会话记录/);
  await e.AC.resumeSession();const resume=e.calls.find(c=>c.name==='agent_resume');assert.equal(resume.args[0],s.session_id);assert.deepEqual(resume.args[1],clone(e.w.WB.state.board));assert.equal(resume.args[2].document_kind,'whiteboard');assert.equal(JSON.stringify(resume.args[2]).includes('board.json'),false);assert.deepEqual(resume.args[3],{kind:'whiteboard',board:clone(e.w.WB.state.board),calculated:null});
  assert.equal(s.live,true);assert.equal(e.AC.nodes.send.disabled,false);assert.ok(e.calls.every(c=>!['agent_start','agent_send'].includes(c.name)));assert.match(e.AC.nodes.notice.textContent,/没有向模型发送请求/);
  s.recovery={message:'旧恢复告警'};e.AC.receive(s,{session_id:s.session_id,activation:{conversation_id:s.session_id,activation_generation:1,resume_pending:false,recovery:null},next_seq:5,events:statusEvents(5,5,'completed')});assert.equal(s.recovery,null);
  e.AC.nodes.input.value='从检查点继续';await e.AC.send();assert.ok(e.calls.some(c=>c.name==='agent_send'));assert.ok(e.calls.every(c=>c.name!=='agent_start'));
});
test('WebView reload explicitly rebinds an idle warm service conversation before continuing',async()=>{
  const e=setup();await e.ready();const old=e.w.callApi;e.w.callApi=async(name,...args)=>{
    if(name==='agent_sessions')return {code:0,data:{sessions:[{session_id:'warm-history',title:'仍在服务中',kind:'whiteboard',workflow:'staged',state:'ready',active:false,can_continue:true,can_resume:true,activation:{state:'warm',recovery:null}}]}};
    if(name==='agent_poll')return {code:0,data:{session_id:args[0],state:'completed',kind:'whiteboard',workflow:'staged',active:false,next_seq:4,events:statusEvents(1,4,'completed'),candidate:candidate(),can_continue:true,can_resume:true,activation:{state:'active',recovery:null}}};
    if(name==='agent_resume'){e.calls.push({name,args:clone(args)});return {code:0,data:{session_id:args[0],state:'ready',active:false,kind:'whiteboard',workflow:'staged',title:'仍在服务中',next_seq:4,events:statusEvents(1,4,'completed'),candidate:candidate(),can_resume:false,activation:{state:'warm',recovery:null}}};}
    return old(name,...args);
  };
  await e.AC.loadHistory();const s=e.AC.session('warm-history');assert.equal(s.live,false);assert.equal(s.canResume,true);await e.AC.selectSession(s.session_id);assert.equal(s.canResume,true);assert.equal(e.AC.nodes.send.disabled,true);
  await e.AC.resumeSession();assert.equal(s.live,true);assert.equal(s.canResume,false);assert.ok(e.calls.some(row=>row.name==='agent_resume'));assert.ok(e.calls.every(row=>row.name!=='agent_send'));
});
test('active history cannot be deleted',async()=>{
  const e=setup();await e.start();await e.AC.forget();assert.ok(e.calls.every(c=>c.name!=='agent_forget'));
});
test('forgetting an idle session drops both its shell and committed event-store record',async()=>{
  const e=setup(),s=await e.completed();assert.ok(e.AC.eventStore.snapshot(s.session_id));await e.AC.forget();
  assert.equal(e.AC.state.sessions.has(s.session_id),false);assert.equal(e.AC.eventStore.snapshot(s.session_id),null);assert.ok(e.calls.some(c=>c.name==='agent_forget'));
});
test('candidate review displays differences but never applies without explicit acceptance',async()=>{
  const e=setup();await e.completed();await e.AC.review();const dialog=e.body.children.find(n=>n.tagName==='dialog');assert.ok(dialog);assert.match(dialog.textContent,/由数据库补充/);
  assert.ok(e.calls.every(c=>c.name!=='agent_accept'));assert.equal(e.orders.includes('pl-replace'),false);
});
test('provider disconnect and trimmed history are reflected without offering an unusable follow-up',async()=>{
  const e=setup(),s=await e.completed();e.AC.receive(s,{provider_id:'codex-one',next_seq:4,state:'stopped',active:false,connection_state:'disconnected',history_truncated:true,events:statusEvents(4,4,'stopped')});
  assert.equal(e.AC.state.connection,'disconnected');assert.equal(e.AC.nodes.send.disabled,true);assert.match(e.AC.nodes.contextWarning.textContent,/容量限制/);
});
test('large and truncated differences expose a paginated read-only full value instead of an uninspectable placeholder',async()=>{
  const e=setup(),s=await e.completed(),old=e.w.callApi;let reads=0;const c={...candidate(),diff:[{path:'/hits',before:[],after:{preview:'值较大，请定向展开',resource:'candidate',pointer:'/hits',count:51}}],diff_total:205,diff_truncated:true};s.candidate=c;
  e.w.callApi=async(name,...args)=>{if(name!=='agent_candidate_read')return old(name,...args);e.calls.push({name,args:clone(args)});reads++;return {code:0,data:{kind:'array',items:[{key:args[3],value:{damage:reads*10}}],total:51,next_offset:args[3]===0?50:null}};};
  const box=new Element();e.AC.renderDiff(box,c,s);assert.match(box.textContent,/共 205 项/);await all(box).find(n=>n.tagName==='button'&&n.textContent==='展开完整值').emit('click');
  const dialog=e.body.children.find(n=>n.tagName==='dialog');assert.match(dialog.textContent,/"damage": 10/);await all(dialog).find(n=>n.tagName==='button'&&n.textContent==='下一页').emit('click');
  assert.match(dialog.textContent,/"damage": 20/);assert.equal(e.calls.at(-1).args[3],50);assert.ok(e.calls.every(c=>!['agent_accept','agent_send'].includes(c.name)));
});
test('closing a full-value view ignores a late response and nested references retain resource boundaries',async()=>{
  const e=setup(),s=await e.completed(),old=e.w.callApi;let finish;e.w.callApi=async(name,...args)=>name==='agent_candidate_read'?new Promise(resolve=>finish=resolve):old(name,...args);
  const reading=e.AC.exploreCandidate(s,s.candidate,'candidate','/hits');const dialog=e.body.children.find(n=>n.tagName==='dialog');all(dialog).find(n=>n.tagName==='button'&&n.textContent==='返回差异审核').emit('click');
  finish({code:0,data:{kind:'array',items:[{key:0,value:'late'}],next_offset:null}});await reading;assert.equal(e.body.children.includes(dialog),false);assert.doesNotMatch(dialog.textContent,/late/);
});
test('accept refuses a candidate that changed after the user reviewed it',async()=>{
  const e=setup(),s=await e.completed(),reviewed=clone(s.candidate);s.candidate={...s.candidate,revision:3};assert.equal(await e.AC.accept(s,reviewed),false);
  assert.ok(e.calls.every(c=>c.name!=='agent_accept'));assert.match(e.AC.nodes.notice.textContent,/重新打开差异审核/);
});
test('native acceptance rechecks disk SHA and applies unsaved input through the original calculation path',async()=>{
  const e=setup(),s=await e.completed('native');assert.equal(await e.AC.accept(s,clone(s.candidate)),true);
  const call=e.calls.find(c=>c.name==='agent_accept');assert.equal(call.args[3],2);assert.equal(call.args[4],'receipt-two');
  assert.deepEqual(e.orders.filter(x=>['pl-replace','calc'].includes(x)),['pl-replace','calc']);assert.equal(e.w.PL.state.savedJson,'original-saved');
  assert.equal(e.w.PL.state.fileSha,'native-sha');assert.ok(e.calls.every(c=>!['save_plan','save_template'].includes(c.name)));assert.equal(s.applied,true);
});
test('disk changes block native application before accepting',async()=>{
  const e=setup(),s=await e.completed('native'),old=e.w.callApi;e.w.callApi=async(name,...args)=>name==='inspect_plan_file'?{code:0,data:{file_sha:'external-new'}}:old(name,...args);
  assert.equal(await e.AC.accept(s,clone(s.candidate)),false);assert.ok(e.calls.every(c=>c.name!=='agent_accept'));assert.equal(e.orders.includes('pl-replace'),false);
});
for(const kind of ['whiteboard','native'])test(`new ${kind} gear draft during accept cannot be overwritten`,async()=>{
  const e=setup(),s=await e.completed(kind),old=e.w.callApi;let finish;e.w.callApi=async(name,...args)=>name==='agent_accept'?new Promise(resolve=>finish=resolve):old(name,...args);
  const accepting=e.AC.accept(s,clone(s.candidate));await tick();e.draft(kind,true);finish({code:0,data:kind==='native'?{kind,revision:2,receipt_fingerprint:'receipt-two',plan:plan()}:{kind,revision:2,receipt_fingerprint:'receipt-two',board:clone(e.w.WB.state.board),result_path:'stable.json'}});
  assert.equal(await accepting,false);assert.equal(e.orders.includes('pl-replace'),false);assert.equal(e.w.WB.state.result,null);assert.match(e.AC.nodes.notice.textContent,/装备草稿发生变化/);
});
test('whiteboard accepts only the revalidated permanent delivery and keeps all human inputs unchanged',async()=>{
  const e=setup(),s=await e.completed(),before=clone(e.w.WB.state.board);assert.equal(await e.AC.accept(s,clone(s.candidate)),true);
  assert.deepEqual(clone(e.w.WB.state.board),before);assert.equal(e.w.WB.state.reusableResult.path,'deliveries/stable/calculated.json');assert.equal(e.w.WB.state.result.result.summary.total_damage,100);
  assert.ok(e.calls.some(c=>c.name==='whiteboard_read_result'));assert.ok(e.calls.every(c=>c.name!=='whiteboard_save'));
});
test('whiteboard late read_result cannot replace a newer human action',async()=>{
  const e=setup(),s=await e.completed(),old=e.w.callApi;let finish;e.w.callApi=async(name,...args)=>name==='whiteboard_read_result'?new Promise(resolve=>finish=resolve):old(name,...args);
  const accepting=e.AC.accept(s,clone(s.candidate));await tick();e.w.WB.change(current=>current.actions[0].start=7);
  finish({code:0,data:{path:'stable.json',calculated:{result:{summary:{total_damage:1}}}}});assert.equal(await accepting,false);assert.equal(e.w.WB.state.result,null);assert.equal(e.w.WB.state.board.actions[0].start,7);
});
test('app close processes both drafts before asking to stop and shuts down before native close',async()=>{
  const e=setup();await e.start();assert.equal(e.AC.eventStore.snapshot().session_count,1);e.w.confirm=()=>{e.orders.push('ask-stop');return true;};assert.equal(e.AC.requestClose(),false);await tick();
  assert.deepEqual(e.orders,['wb-leave','pl-leave','ask-stop','agent_shutdown','confirm_close']);
  assert.equal(e.AC.eventStore.snapshot().session_count,0);
});
test('cancelling document close never stops the Agent or invokes shutdown',async()=>{
  const e=setup();await e.start();e.w.PL.leave=async()=>false;e.AC.requestClose();await tick();assert.ok(e.calls.every(c=>!['agent_stop','agent_shutdown','confirm_close'].includes(c.name)));assert.equal(e.AC.active().active,true);
});
test('cancelling stop at close leaves the task running',async()=>{
  const e=setup();await e.start();e.w.confirm=()=>false;e.AC.requestClose();await tick();assert.ok(e.calls.every(c=>c.name!=='agent_shutdown'));assert.equal(e.AC.active().active,true);
});
test('shutdown failure keeps window open and retains the original task record',async()=>{
  const e=setup(),s=await e.start(),old=e.w.callApi;e.w.callApi=async(name,...args)=>name==='agent_shutdown'?{code:1,message:'自有进程尚未退出'}:old(name,...args);
  e.AC.requestClose();await tick();assert.ok(e.calls.every(c=>c.name!=='confirm_close'));assert.ok(e.AC.state.sessions.has(s.session_id));assert.match(e.AC.nodes.notice.textContent,/进程尚未退出/);
});
test('production uses one DSH root and the App close guard remains a domain-only adapter',()=>{
  const web=path.join(__dirname,'../src/genshin_app/gui/web'),html=fs.readFileSync(path.join(web,'index.html'),'utf8');
  assert.equal((html.match(/id="dsh-app-root"/g)||[]).length,1);
  assert.equal((html.match(/src="dsh-production.js"/g)||[]).length,1);
  assert.equal(html.includes('src="agent-chat.js"'),false);
  const surface=fs.readFileSync(path.join(__dirname,'../frontend/src/business/whiteboard/surfaces.ts'),'utf8');
  assert.match(surface,/services\.workbench\?\.ready/);assert.match(surface,/services\.workbench\.requestClose/);
  assert.match(surface,/WB\.dirty\(\)/);assert.match(surface,/PL|services\.plan/);
});


const buildCandidate=(receipt='draft-two',revision=2)=>({revision,receipt_fingerprint:receipt,ready:true,can_accept:true,application_kind:'whiteboard_draft',has_calculation:false,
  summary:{workflow:'build',has_calculation:false,character_count:1,action_count:0,duration:20},diff_total:1,diff:[{label:'队伍 · 新增角色',before:null,after:'神里绫华',reason:'按聊天要求添加'}],issues:[{message:'尚未添加动作，可继续构建'}]});
const installRealDraftEditor=e=>{const previous=e.w.WB;GUI_TEST_RUNTIME.loadWebFile(e.w, path.join(__dirname,'../src/genshin_app/gui/web/whiteboard.js'), {filename:'whiteboard.js'});
  e.w.WB.state={...e.w.WB.state,...previous.state};e.w.WB.render=previous.render;e.w.WB.leave=previous.leave;e.w.WB.unappliedEditor=previous.unappliedEditor;};
const buildSession=async e=>{await e.ready();e.AC.nodes.workflow.value='build';e.AC.nodes.workflowExplicit=true;e.AC.nodes.input.value='请先添加角色';await e.AC.send();const s=e.AC.selected();
  e.AC.receive(s,{state:'completed',active:false,next_seq:3,events:statusEvents(2,3,'completed'),candidate:buildCandidate()});return s;};
const wireBuildAccept=(e,next)=>{const old=e.w.callApi;e.w.callApi=async(name,...args)=>{if(name==='agent_accept'){e.calls.push({name,args:clone(args)});return {code:0,data:{kind:'whiteboard',workflow:'build',board:clone(next),revision:args[3],receipt_fingerprint:args[4]}};}return old(name,...args);};};

test('empty whiteboard defaults to construction and can send retained typed instructions without a role or action',async()=>{
  const e=setup();e.w.WB.state.board.team=[];e.w.WB.state.board.actions=[];e.AC.nodes.input.value='从零开始构建队伍';await e.ready();e.AC.refresh();assert.equal(e.AC.nodes.workflow.value,'staged');assert.match(e.AC.nodes.workflowNote.textContent,/从空白开始/);
  await e.AC.send();const request=e.calls.find(c=>c.name==='agent_start').args[0];assert.equal(request.workflow,'staged');assert.deepEqual(request.document.team,[]);assert.deepEqual(request.document.actions,[]);assert.equal(request.message,'从零开始构建队伍');assert.equal(e.AC.selected().workflow,'staged');
});
test('whiteboard stays staged after actions exist, supports explicit strict calculation, and native remains calculate only',async()=>{
  const e=setup();e.w.WB.state.board.actions=[];e.AC.refresh();assert.equal(e.AC.nodes.workflow.value,'staged');e.w.WB.state.board.actions=[{id:'a',start:0}];e.AC.refresh();assert.equal(e.AC.nodes.workflow.value,'staged');
  e.AC.nodes.workflow.value='build';e.AC.nodes.workflow.emit('change');e.AC.refresh();assert.equal(e.AC.nodes.workflow.value,'build');assert.match(e.AC.nodes.workflowNote.textContent,/审核/);
  e.AC.nodes.workflow.value='calculate';e.AC.nodes.workflow.emit('change');e.AC.refresh();assert.equal(e.AC.nodes.workflow.value,'calculate');
  e.AC.nodes.kind.value='native';e.AC.nodes.kind.emit('change');assert.equal(e.AC.nodes.workflow.value,'calculate');assert.equal(e.AC.nodes.workflow.disabled,true);await e.ready();e.AC.nodes.workflow.value='build';e.AC.nodes.input.value='计算原生方案';await e.AC.send();assert.equal(e.calls.find(c=>c.name==='agent_start').args[0].workflow,'calculate');
});
test('construction review displays human changes and incomplete draft status without claiming damage calculation',async()=>{
  const e=setup(),s=await buildSession(e),old=e.w.callApi;e.w.callApi=async(name,...args)=>name==='agent_candidate'?{code:0,data:s.candidate}:old(name,...args);
  await e.AC.review();const dialog=e.body.children.find(n=>n.tagName==='dialog');assert.match(dialog.textContent,/审核白板构建草稿/);assert.match(dialog.textContent,/尚未计算/);assert.match(dialog.textContent,/队伍 · 新增角色/);assert.match(dialog.textContent,/尚未添加动作/);assert.doesNotMatch(dialog.textContent,/默认保留用户动作|总伤害|每秒伤害/);assert.equal(all(dialog).find(n=>n.tagName==='button'&&n.textContent==='应用审核过的候选').disabled,false);
});
// Retired AC-specific construction assertions migrated to
// gui_candidate_editor_application_current.test.cjs + app-candidate-commands.test.ts.
// Current DSH Session history is never a second mutable document baseline.
test('an unaccepted construction followup sends the unchanged frozen board',async()=>{
  const e=setup(),s=await buildSession(e),original=clone(e.w.WB.state.board);e.AC.nodes.input.value='先不要应用，改用另一把武器';await e.AC.send();assert.deepEqual(e.calls.find(c=>c.name==='agent_send').args[3],original);assert.equal(s.base.json,JSON.stringify(original));
});
test('manual changes or pending equipment drafts block construction followup without losing the user message',async()=>{
  for(const change of ['panel','draft']){const e=setup();await buildSession(e);if(change==='panel')e.w.WB.change(current=>current.team[0].panel.atk=3000);else e.draft('whiteboard',true);e.AC.nodes.input.value='保留这条指令';await e.AC.send();assert.equal(e.AC.nodes.input.value,'保留这条指令');assert.ok(e.calls.every(c=>c.name!=='agent_send'));}
});
// Retired AC-specific construction assertions migrated to
// gui_candidate_editor_application_current.test.cjs + app-candidate-commands.test.ts.
// Current DSH Session history is never a second mutable document baseline.
test('empty diffs and wrong candidate application types cannot apply; moving to calculation never starts inference',async()=>{
  const e=setup(),s=await buildSession(e);assert.equal(e.AC.canApply(s,{...s.candidate,diff_total:0,diff:[]}),false);assert.equal(e.AC.canApply(s,{...s.candidate,has_calculation:true}),false);assert.equal(e.AC.canApply(s,{...s.candidate,application_kind:'native_plan'}),false);
  const before=e.calls.filter(c=>['agent_start','agent_send'].includes(c.name)).length;e.AC.startCalculation();assert.equal(e.AC.selected(),null);assert.equal(e.AC.nodes.workflow.value,'calculate');assert.equal(e.calls.filter(c=>['agent_start','agent_send'].includes(c.name)).length,before);
});

// Retired AC-specific construction assertions migrated to
// gui_candidate_editor_application_current.test.cjs + app-candidate-commands.test.ts.
// Current DSH Session history is never a second mutable document baseline.
test('construction diff uses Chinese panel and timing labels while raw field locations remain inspectable',async()=>{
  const e=setup(),s=await buildSession(e),box=new Element();const c={...s.candidate,diff:[{path:'/team/0/panel/crit_rate',before:0.05,after:0.7},{path:'/actions/0/start',before:0,after:1.5},{path:'/team/0/panel_basis_mode',before:'database',after:'manual'}]};
  e.AC.renderDiff(box,c,s);assert.match(box.textContent,/队伍 · 第 1 名角色 · 成品与基础面板 · 暴击率/);assert.match(box.textContent,/5%/);assert.match(box.textContent,/70%/);assert.match(box.textContent,/动作 · 第 1 个动作 · 释放时间（秒）/);assert.match(box.textContent,/数据库自动同步/);assert.match(box.textContent,/手动输入/);assert.ok(all(box).some(n=>n.tagName==='details'&&n.textContent.includes('/team/0/panel/crit_rate')));
});
test('large construction array previews resolve to human summaries and ignore a newer candidate',async()=>{
  for(const late of [false,true]){const e=setup(),s=await buildSession(e),box=new Element(),old=e.w.callApi;let finish;
    e.w.callApi=async(name,...args)=>name==='agent_candidate_read'?new Promise(resolve=>finish=resolve):old(name,...args);
    const c={...s.candidate,diff:[{path:'/team',before:[],after:{preview:'值较大，请定向展开',resource:'candidate',pointer:'/team',count:1}}]};s.candidate=c;e.AC.renderDiff(box,c,s);
    if(late)s.candidate={...c,revision:3,receipt_fingerprint:'new'};finish({code:0,data:{kind:'array',total:1,items:[{key:0,value:{id:'a',db_character_id:10000002,name:'神里绫华',level:90,constellation:0,weapon:null,artifact_sets:[],panel:{base_hp:12000,atk:null}}}],next_offset:null}});await tick();
    if(late){assert.match(box.textContent,/候选已变化/);assert.doesNotMatch(box.textContent,/神里绫华/);}else{assert.match(box.textContent,/神里绫华 · 90 级 · 0 命/);assert.match(box.textContent,/基础生命值 12,000/);assert.match(box.textContent,/展开完整值/);}
  }
});


test('calculation candidate can apply reviewed role/action edits together with matching result and continue same session',async()=>{
  const e=setup(),s=await e.completed(),before=clone(e.w.WB.state.board),saved=JSON.stringify(before),next={...before,revision:1,title:'已完善队伍',team:[...before.team,{id:'b',name:'申鹤',panel:{atk:3000}}],actions:[{...before.actions[0],start:2}]};e.w.WB.state.savedJson=saved;
  const old=e.w.callApi;let checkedBoard;e.w.callApi=async(name,...args)=>{if(name==='agent_accept')return {code:0,data:{kind:'whiteboard',board:clone(next),revision:2,receipt_fingerprint:'receipt-two',result_path:'new.calculated.json'}};
    if(name==='whiteboard_read_result'){checkedBoard=clone(args[0]);return {code:0,data:{path:args[1],calculated:{result:{summary:{total_damage:234}}}}};}return old(name,...args);};
  assert.equal(await e.AC.accept(s,clone(s.candidate)),true);assert.deepEqual(checkedBoard,next);assert.deepEqual(clone(e.w.WB.state.board),next);assert.equal(e.w.WB.state.result.result.summary.total_damage,234);assert.equal(e.w.WB.state.savedJson,saved);assert.equal(e.w.WB.state.path,'board.json');assert.equal(e.w.WB.state.fileSha,'board-sha');assert.equal(e.w.WB.state.reusableResult.board.team.length,2);assert.equal(e.AC.canApply(s,s.candidate),false);
  e.AC.nodes.input.value='再按要求调整并计算';await e.AC.send();assert.deepEqual(e.calls.find(c=>c.name==='agent_send').args[3],next);assert.equal(e.calls.filter(c=>c.name==='agent_start').length,1);
  e.AC.receive(s,{next_seq:5,state:'completed',active:false,candidate:{...candidate(),revision:3,receipt_fingerprint:'new-receipt'},events:statusEvents(5,5,'completed')});assert.equal(s.applied,false);assert.equal(e.AC.canApply(s,s.candidate),true);
});
test('native V2 applies the Host-verified path-free calculated delivery without reading a local result path',async()=>{
  const e=setup(),s=await e.completed(),before=clone(e.w.WB.state.board),next={...before,revision:1,title:'V2无路径结果'},old=e.w.callApi;let appliedBeforeAck=false,acceptArgs,ackArgs;s.connectionKind='native_api';
  e.w.callApi=async(name,...args)=>{if(name==='agent_accept'){acceptArgs=clone(args);return {code:0,data:{kind:'whiteboard',workflow:'calculate',board:clone(next),revision:2,receipt_fingerprint:'receipt-two',calculated_delivery:{readonly:true,validation:'full_calculation_package_and_whiteboard_intent',delivery_capability:'wdc_v1_AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA',calculated:{result:{summary:{total_damage:345}}}}}};}
    if(name==='agent_candidate_applied'){e.calls.push({name,args:clone(args)});appliedBeforeAck=e.w.WB.state.board.title===next.title&&e.w.WB.state.result?.result?.summary?.total_damage===345;ackArgs=clone(args);return {code:0,data:{status:'applied',kind:'whiteboard',revision:2,receipt_fingerprint:'receipt-two',app_actions:[]}};}return old(name,...args);};
  assert.equal(await e.AC.accept(s,clone(s.candidate)),true);assert.equal(appliedBeforeAck,true);assert.deepEqual(acceptArgs[5],{kind:'whiteboard',board:before,calculated:null});assert.deepEqual(ackArgs.slice(0,5),[s.session_id,'whiteboard',next,2,'receipt-two']);assert.deepEqual(ackArgs[6],{kind:'whiteboard',board:next,calculated:{result:{summary:{total_damage:345}}}});assert.equal(e.calls.some(row=>row.name==='whiteboard_read_result'),false);assert.deepEqual(clone(e.w.WB.state.board),next);assert.equal(e.w.WB.state.result.result.summary.total_damage,345);assert.equal(e.w.WB.state.reusableResult.sessionId,s.session_id);assert.equal(e.w.WB.state.reusableResult.deliveryCapability,'wdc_v1_AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA');assert.equal(s.applied,true);
});
test('native V2 reconciles a lost application acknowledgement without replaying the write or candidate',async()=>{
  const e=setup(),s=await e.completed(),before=clone(e.w.WB.state.board),next={...before,revision:1,title:'等待回执'},old=e.w.callApi;let acknowledgements=0,accepts=0,polls=0;s.connectionKind='native_api';
  e.w.callApi=async(name,...args)=>{if(name==='agent_accept'){accepts++;return {code:0,data:{kind:'whiteboard',workflow:'calculate',board:clone(next),revision:2,receipt_fingerprint:'receipt-two',calculated_delivery:{readonly:true,validation:'full_calculation_package_and_whiteboard_intent',delivery_capability:'wdc_v1_BBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBB',calculated:{result:{summary:{total_damage:456}}}}}};}
    if(name==='agent_candidate_applied'){acknowledgements++;return {code:1,message:'本地回执暂时不可用'};}
    if(name==='agent_poll'){polls++;return {code:0,data:{session_id:s.session_id,requested_after_seq:args[1],next_seq:args[1],state:'completed',active:false,events:[],candidate:clone(s.candidate),application_intent:{conversation_id:s.session_id,status:'committed',intent:{state:'committed',kind:'whiteboard',revision:2,receipt_fingerprint:'receipt-two'}},app_actions:[]}};}return old(name,...args);};
  assert.equal(await e.AC.accept(s,clone(s.candidate)),true);assert.equal(accepts,1);assert.equal(acknowledgements,1);assert.equal(polls,1);assert.equal(e.w.WB.state.board.title,'等待回执');assert.equal(s.pendingApplicationAck,null);assert.equal(s.applied,true);
});
for(const kind of ['whiteboard','native'])test(`${kind} committed acknowledgement releases the global lock when the editor changes in flight`,async()=>{
  const e=setup(),s=await e.completed(kind),old=e.w.callApi;let finish;s.connectionKind='native_api';
  e.w.callApi=async(name,...args)=>{if(name==='agent_accept'){if(kind==='native'){const next=plan();next.manual_template.hits=[{id:'committed'}];return {code:0,data:{kind:'native',revision:2,receipt_fingerprint:'receipt-two',plan:next}};}const next={...clone(e.w.WB.state.board),title:'已提交'};return {code:0,data:{kind:'whiteboard',workflow:'calculate',board:next,revision:2,receipt_fingerprint:'receipt-two',calculated_delivery:{delivery_capability:'wdc_v1_CCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCC',calculated:{result:{summary:{total_damage:321}}}}}};}
    if(name==='agent_candidate_applied')return new Promise(resolve=>finish=resolve);return old(name,...args);};
  const applying=e.AC.accept(s,clone(s.candidate));await tick();if(kind==='whiteboard'){e.w.WB.state.board.team[0].panel.atk++;e.w.WB.state.request++;}else{e.w.PL.state.plan.manual_template.characters[0].stats.atk++;e.w.PL.state.sourceEpoch++;}
  finish({code:0,data:{status:'applied',kind,revision:2,receipt_fingerprint:'receipt-two',app_actions:[]}});assert.equal(await applying,false);assert.equal(s.pendingApplicationAck,null);assert.equal(s.applicationRecoveryPending,false);assert.equal(s.applied,false);assert.equal(e.AC.pendingApplicationSession(),null);assert.match(e.AC.nodes.notice.textContent,/事务锁已解除/);
});
test('explicit discard recovers a Host candidate after an uncommitted acknowledgement and later user edit',async()=>{
  const e=setup(),s=await e.completed(),before=clone(e.w.WB.state.board),target={...before,title:'Host已审核候选'},old=e.w.callApi;let finishAck,ackCalls=0,confirmations=0;s.connectionKind='native_api';e.w.confirm=()=>{confirmations++;return true;};
  const intent={state:'prepared',kind:'whiteboard',revision:2,receipt_fingerprint:'receipt-two'};
  e.w.callApi=async(name,...args)=>{if(name==='agent_accept')return {code:0,data:{kind:'whiteboard',workflow:'calculate',board:clone(target),revision:2,receipt_fingerprint:'receipt-two',calculated_delivery:{delivery_capability:'wdc_v1_EEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEE',calculated:{result:{summary:{total_damage:111}}}}}};
    if(name==='agent_candidate_applied'){ackCalls++;if(ackCalls===1)return new Promise(resolve=>finishAck=resolve);assert.equal(e.w.WB.state.board.title,target.title);return {code:0,data:{status:'applied',kind:'whiteboard',revision:2,receipt_fingerprint:'receipt-two',app_actions:[]}};}
    if(name==='agent_poll')return {code:0,data:{session_id:s.session_id,state:'completed',active:false,next_seq:s.cursor,events:[],candidate:clone(s.candidate),application_intent:{conversation_id:s.session_id,status:'prepared',intent}}};
    if(name==='agent_recover_candidate_application'){e.calls.push({name,args:clone(args)});assert.equal(args[4],true);assert.equal(args[2].document_kind,'whiteboard');return {code:0,data:{conversation_id:s.session_id,status:'prepared',kind:'whiteboard',workflow:'calculate',application_kind:'whiteboard_calculated',revision:2,receipt_fingerprint:'receipt-two',issues:[],application_intent:{conversation_id:s.session_id,status:'prepared',intent},board:clone(target),calculated_delivery:{delivery_capability:'wdc_v1_FFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFF',calculated:{result:{summary:{total_damage:111}}}},app_actions:[]}};}return old(name,...args);};
  const accepting=e.AC.accept(s,clone(s.candidate));await tick();e.w.WB.state.board.title='ACK等待期间的用户编辑';e.w.WB.state.request++;finishAck({code:1,message:'Host未提交回执'});assert.equal(await accepting,false);assert.equal(s.applicationRecoveryPending,true);assert.ok(s.pendingApplicationAck);assert.equal(e.w.WB.state.board.title,'ACK等待期间的用户编辑');
  assert.equal(await e.AC.recoverPendingCandidateApplication(true),true);assert.equal(confirmations,1);assert.equal(e.w.WB.state.board.title,target.title);assert.equal(e.w.WB.state.result.result.summary.total_damage,111);assert.equal(s.applicationRecoveryPending,false);assert.equal(s.pendingApplicationAck,null);assert.equal(s.applied,true);assert.equal(ackCalls,2);
});
test('lost candidate delivery response refreshes the durable prepared transaction and blocks sending',async()=>{
  const e=setup(),s=await e.completed(),old=e.w.callApi;s.connectionKind='native_api';
  e.w.callApi=async(name,...args)=>{if(name==='agent_accept')return {code:1,message:'模拟候选响应丢失'};if(name==='agent_poll')return {code:0,data:{session_id:s.session_id,state:'completed',active:false,next_seq:s.cursor,events:[],candidate:clone(s.candidate),application_intent:{conversation_id:s.session_id,status:'prepared',intent:{kind:'whiteboard',revision:2,receipt_fingerprint:'receipt-two',document_digest:'d'.repeat(64),state:'prepared'}}}};return old(name,...args);};
  assert.equal(await e.AC.accept(s,clone(s.candidate)),false);assert.equal(s.applicationRecoveryPending,true);assert.equal(e.AC.nodes.send.disabled,true);assert.match(e.AC.nodes.contextWarning.textContent,/候选应用事务尚未完成/);
});
test('native V2 refuses a calculated candidate that only exposes a local result path',async()=>{
  const e=setup(),s=await e.completed(),old=e.w.callApi;s.connectionKind='native_api';
  e.w.callApi=async(name,...args)=>name==='agent_accept'?{code:0,data:{kind:'whiteboard',workflow:'calculate',board:clone(e.w.WB.state.board),revision:2,receipt_fingerprint:'receipt-two',result_path:'must-not-cross.json'}}:old(name,...args);
  assert.equal(await e.AC.accept(s,clone(s.candidate)),false);assert.match(e.AC.nodes.notice.textContent,/缺少Host已校验/);assert.equal(e.calls.some(row=>row.name==='whiteboard_read_result'),false);assert.equal(e.calls.filter(row=>row.name==='agent_abort_candidate_application').length,1);assert.equal(s.applicationRecoveryPending,false);
});
for(const cause of ['invalid-package','changed-board','new-draft','switched-session'])test(`changed calculation board is atomic when ${cause} prevents result acceptance`,async()=>{
  const e=setup(),s=await e.completed(),before=clone(e.w.WB.state.board),next={...before,title:'不得部分应用',actions:[{...before.actions[0],start:4}]},old=e.w.callApi;let finish;
  e.w.callApi=async(name,...args)=>name==='agent_accept'?{code:0,data:{kind:'whiteboard',board:next,revision:2,receipt_fingerprint:'receipt-two',result_path:'new.json'}}:name==='whiteboard_read_result'?new Promise(resolve=>finish=resolve):old(name,...args);
  const accepting=e.AC.accept(s,clone(s.candidate));await tick();if(cause==='changed-board')e.w.WB.change(current=>current.actions[0].start=8);if(cause==='new-draft')e.draft('whiteboard',true);if(cause==='switched-session')e.AC.state.viewEpoch++;
  finish(cause==='invalid-package'?{code:1,message:'计算包与候选白板不一致'}:{code:0,data:{path:'new.json',calculated:{result:{}}}});assert.equal(await accepting,false);assert.equal(e.w.WB.state.board.title,before.title);assert.equal(e.w.WB.state.result,null);assert.equal(s.base.document.title,before.title);
});
test('native calculation application supports continuing with the actually applied input',async()=>{
  const e=setup(),s=await e.completed('native');assert.equal(await e.AC.accept(s,clone(s.candidate)),true);assert.equal(e.AC.nodes.send.disabled,false);assert.equal(e.AC.currentMatches(s.base),true);e.AC.nodes.input.value='再调整这个方案';await e.AC.send();
  const send=e.calls.find(c=>c.name==='agent_send');assert.equal(send.args[0],s.session_id);assert.ok(send.args[3].manual_template.hits);assert.equal(send.args[3].resolution,undefined);assert.equal(send.args[3].resolved_template,undefined);assert.equal(send.args[5].kind,'native');assert.deepEqual(send.args[5].plan,clone(e.w.PL.state.plan));assert.equal(send.args[5].plan.resolution.state,'ready');
});
test('native V2 acknowledges the applied plan before optional recalculation can fail',async()=>{
  const e=setup(),s=await e.completed('native'),old=e.w.callApi;let accepts=0,acks=0,ackSawPlan=false;s.connectionKind='native_api';
  e.w.callApi=async(name,...args)=>{if(name==='agent_accept'){accepts++;const next=plan();next.manual_template.hits=[{id:'ack-first'}];return {code:0,data:{kind:'native',revision:args[3],receipt_fingerprint:args[4],plan:next}};}
    if(name==='agent_candidate_applied'){acks++;ackSawPlan=e.w.PL.state.plan.manual_template.hits?.[0]?.id==='ack-first';return {code:0,data:{status:'applied',kind:'native',revision:args[3],receipt_fingerprint:args[4],app_actions:[]}};}return old(name,...args);};
  e.w.RS.runCalc=async()=>{throw new Error('模拟自动计算失败');};
  assert.equal(await e.AC.accept(s,clone(s.candidate)),true);assert.equal(accepts,1);assert.equal(acks,1);assert.equal(ackSawPlan,true);assert.equal(s.applied,true);assert.equal(s.pendingApplicationAck,null);assert.match(e.AC.nodes.notice.textContent,/已经应用并确认.*自动打开或计算未完成/);
});
for(const kind of ['whiteboard','native'])test(`${kind} calculation snapshots can follow a successful save without accepting external changes`,async()=>{
  const e=setup(),s=await e.completed(kind);await e.AC.accept(s,clone(s.candidate));const capture=e.AC.beforeDocumentSave(kind);
  if(kind==='whiteboard'){e.w.WB.state.path='saved-board.json';e.w.WB.state.fileSha='new-sha';e.w.WB.state.request++;}else{e.w.RT.state.path='saved-native.json';e.w.PL.state.fileSha='new-sha';}
  e.AC.documentSaved(capture);assert.equal(e.AC.currentMatches(s.base),true);assert.equal(s.base.sha,'new-sha');
  const changed=e.AC.beforeDocumentSave(kind),oldBase=s.base;if(kind==='whiteboard')e.w.WB.change(current=>current.team[0].panel.atk++);else e.w.PL.state.plan.manual_template.characters[0].stats.atk++;
  e.AC.documentSaved(changed);assert.equal(s.base,oldBase);e.AC.nodes.input.value='保留消息';await e.AC.send();assert.ok(e.calls.every(c=>c.name!=='agent_send'));assert.equal(e.AC.nodes.input.value,'保留消息');
});
for(const kind of ['whiteboard','native'])test(`${kind} save completion cannot absorb a payload-only calculation change`,async()=>{
  const e=setup(),s=await e.completed(kind),snapshot=e.AC.beforeDocumentSave(kind),oldBase=s.base;
  if(kind==='whiteboard')e.w.WB.setAgentResult({result:{summary:{total_damage:999}}});else e.w.PL.state.plan.resolution={state:'ready',result:{summary:{total_damage:999}}};
  e.AC.documentSaved(snapshot);assert.equal(s.base,oldBase);assert.equal(e.AC.currentMatches(s.base),false);
});
test('workflow copy describes outputs and user-directed edits rather than prohibiting role/action changes',async()=>{
  const e=setup();e.AC.refresh();assert.match(e.AC.nodes.workflowNote.textContent,/明确要求修改角色、装备、面板和动作/);assert.doesNotMatch(e.AC.nodes.workflowNote.textContent,/保留已有队伍/);
  await e.completed();await e.AC.review();const d=e.body.children.find(n=>n.tagName==='dialog');assert.match(d.textContent,/明确要求可调整角色/);assert.doesNotMatch(d.textContent,/默认保留用户动作/);
});

const workbenchFoundation=(conversationId='session-one',overrides={})=>({
  contract:'genshin-agent-ui-projection-v2-foundation',conversation_id:conversationId,source_cursor:12,projection_revision:7,
  document_binding:{document_id:'document-one',document_lineage_epoch:2,document_revision:5,candidate_revision:3,stage_revision:4},
  turns:[{turn_id:'turn-active',state:'streaming',predecessor_inbox_id:null,created_seq:1,updated_seq:12,terminal_seq:null}],
  segments:[{turn_id:'turn-active',segment_id:'segment-active',epoch_id:'epoch-active',runtime_incarnation:'runtime-active',stage_epoch:2,state:'streaming',created_seq:1,updated_seq:12,terminal_seq:null}],
  questions:[],inbox:[],...overrides
});
const activateWorkbenchSession=e=>{const session=e.AC.session('session-one');session.live=true;session.active=true;session.state='running';session.kind='whiteboard';session.workflow='calculate';session.base=e.AC.capture('whiteboard');e.AC.state.selected=session.session_id;e.AC.state.active=session.session_id;e.AC.state.workbenchCapabilities={workbench_foundation:true,structured_question_batch:true,durable_inbox:true};return session;};

test('active-turn input uses the durable Queue endpoint with the exact Host document binding',async()=>{
  const e=setup(),session=activateWorkbenchSession(e),calls=[],foundation=workbenchFoundation();e.w.callApi=async(name,...args)=>{calls.push({name,args:clone(args)});if(name==='agent_workbench_projection')return{code:0,data:foundation};if(name==='agent_enqueue_workbench_inbox')return{code:0,data:{status:'queued'}};return{code:1,message:'unexpected'};};
  e.AC.nodes.input.value='下一轮检查元素附着';e.AC.nodes.inboxMode.value='queue';assert.equal(await e.AC.enqueueActiveMessage(),true);const call=calls.find(row=>row.name==='agent_enqueue_workbench_inbox');assert.ok(call);assert.deepEqual(call.args[1].document_binding,foundation.document_binding);assert.equal(call.args[1].kind,'queue');assert.equal(call.args[1].target,null);assert.equal(calls.some(row=>row.name==='agent_send'),false);assert.equal(e.AC.nodes.input.value,'');assert.equal(e.AC.workbenchFoundation(session).projection_revision,7);
});

test('Steer is bound to the exact active execution segment and never inferred from display text',async()=>{
  const e=setup();activateWorkbenchSession(e);const calls=[],foundation=workbenchFoundation();e.w.callApi=async(name,...args)=>{calls.push({name,args:clone(args)});if(name==='agent_workbench_projection')return{code:0,data:foundation};if(name==='agent_enqueue_workbench_inbox')return{code:0,data:{status:'pending_runtime_ack'}};return{code:1,message:'unexpected'};};
  e.AC.nodes.input.value='先核对当前元素底';e.AC.nodes.inboxMode.value='steer';assert.equal(await e.AC.enqueueActiveMessage(),true);const request=calls.find(row=>row.name==='agent_enqueue_workbench_inbox').args[1];assert.equal(request.kind,'steer');assert.deepEqual(request.target,{turn_id:'turn-active',segment_id:'segment-active',epoch_id:'epoch-active',stage_epoch:2,runtime_incarnation:'runtime-active'});
});

test('an open structured question forces ordinary composer input into Queue while preserving question identity',async()=>{
  const e=setup();activateWorkbenchSession(e);const question={question_id:'batch-one',batch_id:'batch-one',turn_id:'turn-active',segment_id:'segment-active',call_id:'call-one',kind:'batch',mode:'live',state:'open',items:[{question_id:'choice-one',kind:'confirmation',prompt:'是否继续？',detail:null,header:null,intent:null,options:[],allow_skip:false}],answer:null,answer_receipt:null,revision:0,created_seq:10,updated_seq:10},foundation=workbenchFoundation('session-one',{questions:[question]}),calls=[];e.w.callApi=async(name,...args)=>{calls.push({name,args:clone(args)});if(name==='agent_workbench_projection')return{code:0,data:foundation};if(name==='agent_enqueue_workbench_inbox')return{code:0,data:{status:'queued'}};return{code:1,message:'unexpected'};};
  e.AC.nodes.input.value='这是后续要求，不是问题答案';e.AC.nodes.inboxMode.value='steer';assert.equal(await e.AC.enqueueActiveMessage(),true);const request=calls.find(row=>row.name==='agent_enqueue_workbench_inbox').args[1];assert.equal(request.kind,'queue');assert.equal(request.target,null);assert.equal(e.AC.questionController.current().batch_id,'batch-one');
});

test('structured question answers use the global workbench revision rather than the question row revision',async()=>{
  const e=setup(),session=activateWorkbenchSession(e),calls=[],next=workbenchFoundation();e.w.callApi=async(name,...args)=>{calls.push({name,args:clone(args)});if(name==='agent_submit_question_batch_answer')return{code:0,data:{question_state:'answer_submitting'}};if(name==='agent_workbench_projection')return{code:0,data:next};return{code:1,message:'unexpected'};};
  assert.equal(await e.AC.submitQuestionBatch({session_id:session.session_id,batch_id:'batch-one',expected_revision:2,projection_revision:7,mutation_id:'answer-one',answer:{items:[{question_id:'choice-one',text:null,value_ids:['confirm'],skipped:false}]}}),true);const request=calls.find(row=>row.name==='agent_submit_question_batch_answer').args[1];assert.equal(request.expected_revision,7);assert.equal(Object.hasOwn(request,'projection_revision'),false);
});

test('malformed or cross-session workbench projections cannot replace the last accepted foundation',()=>{
  const e=setup(),session=activateWorkbenchSession(e),accepted=workbenchFoundation();assert.equal(e.AC.presentWorkbenchFoundation(session,accepted).ok,true);const bad=clone(accepted);bad.conversation_id='another-session';bad.document_binding.local_path='C:/secret.json';const result=e.AC.presentWorkbenchFoundation(session,bad);assert.equal(result.ok,false);assert.equal(e.AC.workbenchFoundation(session).conversation_id,undefined);assert.equal(e.AC.workbenchFoundation(session).session_id,'session-one');assert.equal(e.AC.workbenchFoundation(session).projection_revision,7);
});

test('domain controller gives duplicate candidate review requests one Host command and one dialog',async()=>{
  const e=setup();await e.completed();await Promise.all([e.AC.review(),e.AC.review()]);assert.equal(e.calls.filter(row=>row.name==='agent_candidate').length,1);assert.equal(e.body.children.filter(node=>node.tagName==='dialog').length,1);assert.equal(e.AC.domainTransactions.gate.activeCount(),0);
});

test('domain controller executes one App side effect for concurrent duplicate actions',async()=>{
  const e=setup(),session=e.AC.session('domain-action-session'),action={action_id:'domain-action-one',action:'navigate_result',status:'queued',preconditions:{status:'ready'},execution_mode:'legacy'};session.live=true;session.appActions=[action];await Promise.all([e.AC.executeAppAction(session,action),e.AC.executeAppAction(session,action)]);assert.equal(e.orders.filter(row=>row==='navigate:result').length,1);assert.equal(e.calls.filter(row=>row.name==='agent_resolve_app_action'&&row.args[1]===action.action_id).length,1);assert.equal(e.AC.domainTransactions.gate.activeCount(),0);
});

test('stage contract keeps saveability, stage progress, energy and calculation acceptance independent',()=>{
  const e=setup(),s=e.AC.session('staged');s.kind='whiteboard';s.workflow='build';s.base=e.AC.capture('whiteboard');
  const c={...buildCandidate(),workflow_stage:'event_topology',stage_status:'partial',saveable:true,energy_status:'not_checked',calculation_acceptance:'not_requested',
    event_topology_coverage_receipt:{status:'topology_ready'},event_topology_execution_binding_receipt:{status:'incomplete',counts:{expected_nodes:4,bound_nodes:2,resolved_bindings:2},coverage:{missing_node_ids:['loop.2','loop.3']},issues:[{code:'event_topology_execution_binding_missing',message:'仍有节点未绑定当前输入'}]}};
  const state=e.AC.workflowState(s,c);assert.equal(state.stage,'event_topology');assert.equal(state.stageStatus,'partial');assert.equal(state.saveable,true);
  assert.equal(state.energyStatus,'not_checked');assert.equal(state.calculationAcceptance,'not_requested');assert.equal(state.diagnostic,false);assert.equal(e.AC.canApply(s,c),true);
  const tracker=e.AC.renderStageTracker(state);assert.match(tracker.textContent,/事件拓扑/);assert.match(tracker.textContent,/能量：未检查/);assert.match(tracker.textContent,/草稿保存：允许/);
  assert.match(tracker.textContent,/拓扑结构：结构完整/);assert.match(tracker.textContent,/执行绑定：待完善 · 已绑定 2\/4 · 已解析 2/);assert.match(tracker.textContent,/计算接纳：尚未请求/);
  assert.match(tracker.textContent,/执行绑定缺失节点：loop\.2、loop\.3/);assert.match(tracker.textContent,/执行绑定首个问题：仍有节点未绑定当前输入/);
  assert.match(tracker.textContent,/角色特有语义仍由 Agent 声明.*未验证/);assert.doesNotMatch(tracker.textContent,/角色语义.*已验证/);
});

test('event topology review exposes structure but withholds premature damage values and full candidate browsing',()=>{
  const e=setup(),s=e.AC.session('topology');s.kind='whiteboard';s.workflow='build';s.base=e.AC.capture('whiteboard');
  const c={...buildCandidate(),workflow_stage:'event_topology',stage_status:'partial',saveable:true,
    event_topology_coverage_receipt:{contract_id:'event-topology-coverage-receipt-v1',status:'draft',counts:{actions:3,sources:4,nodes:12,edges:9,parameter_slots:6},stages:{topology:{status:'blocked'},numeric_binding:{status:'blocked'}}},
    event_topology_execution_binding_receipt:{contract_id:'event-topology-execution-coverage-v1',status:'incomplete',counts:{expected_nodes:12,bound_nodes:8,bindings:9,resolved_bindings:7},coverage:{missing_node_ids:['node.buff.2','node.hit.4']},issues:[{code:'event_topology_binding_target_missing',message:'目标路径未解析到当前执行输入'}],character_semantics:'agent_declared_unverified'},
    summary:{event_topology:{node_count:12,binding_count:9,unresolved_count:2,status:'partial'},total_damage:987654,dps:12345},
    diff:[{path:'/event_topology/nodes/0',before:null,after:{node_id:'hit-one',kind:'damage',status:'identified',schedule:{kind:'point'},parameter_slots:[{value:777}],source_refs:[{reference:'private research transcript'}]},reason:'识别命中'},
      {path:'/result/calculation_totals/total_damage',before:null,after:987654,reason:'尚未披露'}]};
  const box=new Element();e.AC.renderDiff(box,c,s);assert.match(box.textContent,/拓扑结构/);assert.match(box.textContent,/事件节点 12/);assert.match(box.textContent,/拓扑关系 9/);assert.match(box.textContent,/参数槽 6/);assert.match(box.textContent,/拓扑结构检查：blocked/);
  assert.match(box.textContent,/执行绑定/);assert.match(box.textContent,/待完善 · 已绑定 8\/12 · 已解析 7/);assert.match(box.textContent,/缺失节点：node\.buff\.2、node\.hit\.4/);assert.match(box.textContent,/首个问题：目标路径未解析到当前执行输入/);
  assert.match(box.textContent,/计算接纳尚未请求|计算接纳.*尚未请求/);assert.match(box.textContent,/角色特有语义仍由 Agent 声明.*未由 App 或引擎验证/);assert.doesNotMatch(box.textContent,/角色特有语义.*已验证/);
  assert.match(box.textContent,/事件节点 hit-one · damage · identified/);assert.match(box.textContent,/识别命中/);assert.match(box.textContent,/已隐藏 1 项伤害数值差异/);assert.doesNotMatch(box.textContent,/777|private research|987,?654|12345|查看完整候选输入/);
});

test('strict calculation diagnostic stays inspectable but cannot be applied',async()=>{
  const e=setup(),s=await e.completed();s.candidate={...candidate(),workflow_stage:'strict_calculation',stage_status:'failed',calculation_acceptance:'diagnostic_only',can_accept:true,
    summary:{total_damage:999,dps:50,calculation_acceptance:'diagnostic_only',failure_code:'energy_cycle_failed'}};e.AC.refresh();
  const state=e.AC.workflowState(s,s.candidate);assert.equal(state.diagnostic,true);assert.equal(e.AC.canApply(s,s.candidate),false);assert.match(e.AC.nodes.candidate.textContent,/仅诊断/);
  const box=new Element();e.AC.renderDiff(box,s.candidate,s);assert.match(box.textContent,/严格计算未通过/);assert.match(box.textContent,/energy_cycle_failed/);assert.doesNotMatch(box.textContent,/总伤害：999|每秒伤害：50/);
});
