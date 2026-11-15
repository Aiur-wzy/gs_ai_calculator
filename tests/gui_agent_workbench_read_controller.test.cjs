const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const source = fs.readFileSync(
  path.join(__dirname, '../src/genshin_app/gui/web/agent-workbench-read-controller.js'),
  'utf8',
);

const deferred = () => {
  let resolve;
  const promise = new Promise(done => { resolve = done; });
  return {promise, resolve};
};

const node = () => ({
  children: [], hidden: false, dataset: {}, textContent: '',
  append(...rows) { this.children.push(...rows); },
  replaceChildren(...rows) { this.children = [...rows]; },
});

function setup(overrides = {}) {
  const world = {console, setTimeout, clearTimeout}; world.window = world;
  vm.createContext(world); vm.runInContext(source, world, {filename:'agent-workbench-read-controller.js'});
  const state = {
    sessions:new Map(), selected:null, active:null, history:[], viewEpoch:0,
    connectionGeneration:0,
    workbenchCapabilities:{turn_outline:true,conversation_search:true,history_window:true,telemetry_bundle:true,workbench_foundation:true,session_lifecycle:true},
  };
  const session = {session_id:'session-one',epoch:0,workbench:{view:'chat',query:'',turn_id:null}};
  state.sessions.set(session.session_id, session); state.selected = session.session_id;
  const calls = [], notices = [], outlineUpdates = [], pageUpdates = [];
  const nodes = {
    hostSearchResults:node(), messages:node(), historyWindowHost:node(), telemetryHost:node(),
    trajectoryList:node(), telemetryStatus:node(), inboxSection:node(),
    telemetryFilter:{value:'all'},telemetryTurnFilter:{value:''},telemetryStageFilter:{value:''},
    telemetrySourceFilter:{value:'all'},telemetryStateFilter:{value:'all'},telemetryDurationFilter:{value:'all'},telemetryQuery:{value:''},
  };
  const outline = {list:node(),notice:node(),update(value,id){outlineUpdates.push([value,id]);return {ok:true,projection:value};},select(){}};
  const conversation = {
    list:node(), setOutline(){}, hasTimeline(){return false;}, ensureLatest(){}, requestLatest(){},
    updatePage(value,expected,options){pageUpdates.push({value,expected,options});return {ok:true,projection:value};},
  };
  const telemetry = {
    accepted:{trajectory:null}, clears:0, filters:null,
    updateBundle(value,id){if(value.session_id!==id)return {ok:false,reason:'telemetry_cross_session'};this.accepted={trajectory:{session_id:id}};return {ok:true,projection:value};},
    snapshot(){return this.accepted;}, clear(){this.clears++;this.accepted={trajectory:null};},
    setFilters(value){this.filters=value;return true;},
  };
  const parseSearch = (value, expected) => value.session_id === expected.session_id && value.query_digest === expected.query_digest
    ? {ok:true,projection:value} : {ok:false,reason:'search_identity_invalid'};
  const parseFoundation = (value, id) => value.session_id === id
    ? {ok:true,projection:value} : {ok:false,reason:'foundation_identity_invalid'};
  const api = overrides.api || (async(name,...args) => { calls.push({name,args}); return {code:1,message:'unhandled'}; });
  const controller = world.AgentWorkbenchReadController.create({
    api:async(name,...args)=>{calls.push({name,args});return api(name,...args);}, state:()=>state,
    selected:()=>state.sessions.get(state.selected)||null, readModel:value=>value,
    errorText:(_value,fallback)=>fallback, notice:(...args)=>notices.push(args), capabilities:()=>state.workbenchCapabilities,
    nodes:()=>nodes,sessionNavigation:()=>null,pendingApplicationSession:()=>null,currentDocumentId:()=>null,
    isActive:value=>value?.active===true,hasPendingApplication:()=>false,hasUnsettledAppAction:()=>false,
    turnOutline:()=>outline,conversationView:()=>conversation,historyWindowView:()=>({update(){return {ok:true,projection:{completeness:'complete'}};}}),
    telemetryView:()=>telemetry,questionController:()=>null,inboxDock:()=>null,readingAnchors:()=>null,
    workbenchState:value=>value.workbench,renderWorkbench(){},renderMessages(){},clone:value=>structuredClone(value),
    element:(tag,text,className)=>({tag,text,className,children:[],dataset:{},append(...rows){this.children.push(...rows);}}),
    button:(text,onClick,className)=>({text,onClick,className,dataset:{}}),
    queryDigest:overrides.queryDigest || (async value=>`${value}-digest`),parseSearch,parseFoundation,
    setTimer:overrides.setTimer,clearTimer:overrides.clearTimer,nextFrame:callback=>callback(),
  });
  return {world,state,session,calls,notices,nodes,outline,outlineUpdates,conversation,pageUpdates,telemetry,controller};
}

test('late Host outline and foundation responses are fenced by session, view epoch and generation', async () => {
  const outlinePending=deferred(),foundationPending=deferred();
  const e=setup({api:async name=>name==='agent_turn_outline'?outlinePending.promise:foundationPending.promise});
  const outline=e.controller.loadHostOutline(e.session),foundation=e.controller.loadWorkbenchFoundation(e.session,{force:true});
  const other={session_id:'session-two',epoch:0};e.state.sessions.set(other.session_id,other);e.state.selected=other.session_id;e.state.viewEpoch++;
  outlinePending.resolve({code:0,data:{session_id:e.session.session_id,source_cursor:1}});
  foundationPending.resolve({code:0,data:{session_id:e.session.session_id,source_cursor:1,inbox:[]}});
  assert.equal(await outline,false);assert.equal(await foundation,false);
  assert.equal(e.outlineUpdates.length,0);assert.equal(e.session.workbenchFoundation,undefined);
  assert.equal(e.session.workbenchFoundationLoading,false);
});

test('concurrent pages accept only the current generation for one direction', async () => {
  const first=deferred(),second=deferred();let count=0;
  const e=setup({api:async name=>name==='agent_load_turn_window'?(++count===1?first.promise:second.promise):{code:1}});
  const locator={turn_id:'turn-one',source_seq:1,stable_node_id:null,before_seq:5,limit:500};
  const olderOne=e.controller.loadConversationPage(e.session,locator,{session_id:e.session.session_id,direction:'older'});
  const olderTwo=e.controller.loadConversationPage(e.session,locator,{session_id:e.session.session_id,direction:'older'});
  second.resolve({code:0,data:{window:{page:'second'}}});assert.equal(await olderTwo,true);
  first.resolve({code:0,data:{window:{page:'first'}}});assert.equal(await olderOne,false);
  assert.deepEqual(e.pageUpdates.map(row=>row.value.page),['second']);
});

test('debounced search digest cannot publish an older query and uses a bounded Host page', async () => {
  const timers=new Map();let nextTimer=0,resolveFirst;
  const firstDigest=new Promise(done=>{resolveFirst=done;});
  const e=setup({
    setTimer:(callback)=>{timers.set(++nextTimer,callback);return nextTimer;},clearTimer:id=>timers.delete(id),
    queryDigest:value=>value==='first'?firstDigest:Promise.resolve('b'.repeat(64)),
    api:async(name,_id,payload)=>name==='agent_search_conversation'?{code:0,data:{session_id:'session-one',query_digest:payload.query_digest,projection_revision:1,source_cursor:1,completeness:'complete',results:[],has_more:false,next_cursor:null}}:{code:1},
  });
  assert.equal(e.controller.scheduleHostSearch(e.session,'first'),true);const firstRun=timers.get(1)();
  assert.equal(e.controller.scheduleHostSearch(e.session,'second'),true);await timers.get(2)();
  resolveFirst('a'.repeat(64));await firstRun;
  const searches=e.calls.filter(row=>row.name==='agent_search_conversation');assert.equal(searches.length,1);
  assert.equal(searches[0].args[1].query,'second');assert.equal(searches[0].args[1].limit,50);
});

test('hidden search pane stays empty and bounded search keeps only stable latest identities for a 10k source', async () => {
  let page=0;
  const e=setup({api:async(name,_id,payload)=>{
    if(name!=='agent_search_conversation')return {code:1};
    const start=page===0?0:page===1?199:399,count=200;page++;
    return {code:0,data:{session_id:'session-one',query_digest:payload.query_digest,projection_revision:4,source_cursor:10000,completeness:'complete',has_more:page<3,next_cursor:page<3?start+count-1:null,
      results:Array.from({length:count},(_,index)=>({stable_node_id:`node-${start+index}`,turn_id:`turn-${start+index}`,source_seq:start+index,kind:'message',excerpt:'hit'}))}};
  }});
  assert.equal(e.controller.renderHostSearchResults(null),true);assert.equal(e.nodes.hostSearchResults.hidden,true);assert.equal(e.nodes.hostSearchResults.children.length,0);
  e.session.hostSearchRequest=1;e.session.hostSearchState={query:'hit',digest:'d'.repeat(64),request:1,loading:false};
  await e.controller.loadHostSearchPage(e.session);await e.controller.loadHostSearchPage(e.session,{after_seq:200,append:true});await e.controller.loadHostSearchPage(e.session,{after_seq:399,append:true});
  assert.equal(e.session.hostSearchProjection.results.length,400);assert.equal(new Set(e.session.hostSearchProjection.results.map(row=>row.stable_node_id)).size,400);
  assert.equal(e.session.hostSearchProjection.results[0].stable_node_id,'node-199');
  assert.ok(e.calls.filter(row=>row.name==='agent_search_conversation').every(row=>row.args[1].limit===50));
  const before=e.calls.length;assert.equal(await e.controller.loadHostOutline(e.session,{before_seq:1,limit:10000}),false);assert.equal(await e.controller.loadTelemetry(e.session,{after_seq:1,limit:10000}),false);assert.equal(e.calls.length,before);
});

test('telemetry rejects a cross-session bundle without clearing the last accepted projection', async () => {
  const e=setup({api:async name=>name==='agent_telemetry_bundle'?{code:0,data:{session_id:'other-session'}}:{code:1}});
  e.telemetry.accepted={trajectory:{session_id:e.session.session_id}};
  assert.equal(await e.controller.loadTelemetry(e.session),false);
  assert.equal(e.telemetry.accepted.trajectory.session_id,e.session.session_id);assert.equal(e.telemetry.clears,0);
  assert.match(e.nodes.telemetryStatus.textContent,/拒绝跨会话/);
});

test('controller source is read-only and exposes no provider or domain-write endpoint', () => {
  for(const endpoint of ['agent_start','agent_send','agent_stop','agent_submit_question_batch_answer','agent_candidate_applied','agent_claim_app_action','agent_settle_app_action']){
    assert.equal(source.includes(`"${endpoint}"`),false,endpoint);
  }
  assert.match(source,/agent_turn_outline/);assert.match(source,/agent_workbench_projection/);
});
