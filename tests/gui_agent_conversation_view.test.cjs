const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const test = require("node:test");
const assert = require("node:assert/strict");

class Node {
  constructor(tag) {
    this.tagName=tag; this.children=[]; this.dataset={}; this.listeners={};
    this.textContent=""; this.className=""; this.hidden=false; this.disabled=false;
    this.scrollTop=0; this.clientHeight=40; this.offsetTop=0; this.offsetHeight=10;
  }
  append(...children) { this.children.push(...children); }
  replaceChildren(...children) { this.children=[...children]; }
  addEventListener(name, callback) { this.listeners[name]=callback; }
  setAttribute(name, value) { this[name]=String(value); }
}

function load(options={}) {
  const document={createElement:tag=>new Node(tag)};
  const window={document, requestAnimationFrame:callback=>{callback();return 1;}};
  window.window=window;
  const context=vm.createContext({window,document,Object,Set,Map,Array,Number,String,TypeError,Promise,JSON,Math});
  vm.runInContext(fs.readFileSync(path.join(__dirname,"../src/genshin_app/gui/web/agent-history-window.js"),"utf8"),context);
  vm.runInContext(fs.readFileSync(path.join(__dirname,"../src/genshin_app/gui/web/agent-conversation-view.js"),"utf8"),context);
  const host=new Node("section");
  const rendered=[];
  const view=window.AgentConversationView.create(document,host,{
    schedule:callback=>callback(),
    renderNode:row=>{const node=new Node("article");node.textContent=row.label||row.text;node.offsetTop=row.source_seq*10;rendered.push(row.stable_node_id);return node;},
    ...options,
  });
  Object.defineProperty(host,"scrollHeight",{get:()=>view.list.children.length*10+50});
  return {window,host,view,rendered};
}

const outline=(items=[
  {turn_id:"turn-1",start_seq:1,end_seq:500},
  {turn_id:"turn-2",start_seq:501,end_seq:1000},
])=>({session_id:"session-1",items});

function page({turn="turn-2",start=501,count=3,hasOlder=false,hasNewer=false,partial=false}={}) {
  const nodes=Array.from({length:count},(_,index)=>{
    const seq=start+index, kind=index%5===1?"tool":index%5===2?"candidate":index%5===3?"app_action":"message";
    return {stable_node_id:`${kind}:node-${seq}`,turn_id:turn,source_seq:seq,kind,
      role:kind==="message"?(index%2?"assistant":"user"):null,label:`节点 ${seq}`,text:`正文 ${seq}`,
      state:kind==="candidate"?"ready_for_application":kind==="app_action"?"completed":kind==="tool"?"completed":null};
  });
  return {contract:"agent-history-window-v1",session_id:"session-1",projection_revision:9,source_cursor:1000,
    completeness:partial?"partial":"complete",partial_reasons:partial?["legacy_gap"]:[],
    anchor:{turn_id:turn,source_seq:start,stable_node_id:null},nodes,
    has_older:hasOlder,has_newer:hasNewer,
    older_cursor:hasOlder?start:null,newer_cursor:hasNewer?start+count-1:null,total_nodes:1000};
}

test("renders one bounded, ordered Host timeline and exposes only actionable index rows",()=>{
  const {view}=load({domLimit:20,cacheLimit:40});
  assert.equal(view.activate("session-1"),true);
  assert.equal(view.setOutline(outline()).ok,true);
  const result=view.updatePage(page({count:8}),{session_id:"session-1"},{direction:"replace"});
  assert.equal(result.ok,true);
  assert.equal(view.snapshot().cached,8);assert.equal(view.snapshot().materialized,8);
  assert.deepEqual(Array.from(view.actionIndex(),row=>row.kind),["candidate","candidate"]);
  assert.equal(view.list.children[0].dataset.sourceSeq,"501");
  assert.equal(view.status.dataset.completeness,"complete");
});

test("collapses only adjacent successful tool and research rows within one Turn scope",()=>{
  const {view}=load({domLimit:20,cacheLimit:40});view.activate("session-1");view.setOutline(outline());
  const value=page({count:12}),row=(id,seq,kind,state,stage="event_topology",segment="segment-one")=>({
    stable_node_id:`${kind}:${id}`,turn_id:"turn-2",source_seq:seq,kind,role:null,label:`步骤 ${id}`,text:`正文 ${id}`,state,
    workflow_stage:stage,segment_id:segment,result_code:state==="completed"?"code_0":null,
  });
  value.nodes=[
    row("read",501,"tool","completed"),row("search",502,"research","completed"),
    row("failed",503,"tool","failed"),row("running",504,"tool","running"),
    row("waiting",505,"activity","waiting"),row("question",506,"question","open"),
    row("candidate",507,"candidate","ready_for_application"),row("app",508,"app_action","presented"),
    row("stage-one",509,"tool","completed"),row("stage-two",510,"tool","completed","numeric_model"),
    row("stage-three",511,"research","completed","numeric_model"),row("segment-two",512,"tool","completed","numeric_model","segment-two"),
  ];
  assert.equal(view.updatePage(value,{session_id:"session-1"},{direction:"replace"}).ok,true);
  assert.equal(view.snapshot().materialized,12,"projection still counts durable events rather than display groups");
  assert.equal(view.list.children.length,10);
  const first=view.list.children[0],disclosure=first.children[0];assert.equal(first.tagName,"article");assert.equal(disclosure.tagName,"details");assert.equal(disclosure.open===true,false);assert.equal(first.dataset.memberCount,"2");assert.match(disclosure.children[0].textContent,/已完成 2 个资料与工具步骤/);assert.deepEqual(disclosure.children.slice(1).map(node=>node.dataset.stableNodeId),["tool:read","research:search"]);
  assert.deepEqual(view.list.children.map(node=>node.dataset.stableNodeId),["tool:read","tool:failed","tool:running","activity:waiting","question:question","candidate:candidate","app_action:app","tool:stage-one","tool:stage-two","tool:segment-two"]);
  assert.deepEqual(Array.from(view.actionIndex(),row=>row.kind),["question","candidate","app_action"]);
  disclosure.open=true;view.render();assert.equal(view.list.children[0],first);assert.equal(view.list.children[0].children[0].open,true,"stable groups preserve user disclosure state");
});

test("conversation projection can keep the complete timeline while materializing chat messages only",()=>{
  const {view,rendered}=load({domLimit:20,cacheLimit:40,includeRow:row=>row.kind==="message"});
  view.activate("session-1");view.setOutline(outline());
  const value=page({count:8});
  assert.equal(view.updatePage(value,{session_id:"session-1"},{direction:"replace"}).ok,true);
  assert.equal(view.snapshot().cached,8,"non-chat facts remain in the bounded durable projection");
  assert.deepEqual(rendered,["message:node-501","message:node-505","message:node-506"]);
  assert.deepEqual(view.list.children.map(node=>node.dataset.stableNodeId),rendered);
  assert.deepEqual(Array.from(view.actionIndex(),row=>row.kind),["candidate","candidate"],"hidden actionable facts still drive the attention shelf");
});

test("conversation projection rejects a non-function row filter",()=>{
  assert.throws(()=>load({includeRow:"message"}),/conversation_view_filter_invalid/);
});

test("external conversation toolbar owns compact sync and unread controls without adding a status row to chat",()=>{
  const controlsHost = new Node("nav"), {view}=load({controlsHost});
  view.activate("session-1"); view.setOutline(outline());
  view.updatePage(page({count:2}), {session_id:"session-1"}, {direction:"replace"});
  assert.equal(view.status.textContent, "已同步");
  assert.match(view.status.title, /已缓存 2 项/);
  assert.equal(controlsHost.children.includes(view.status), true);
  assert.equal(view.host.children.includes(view.status), false);
  view.updatePage(page({start:503,count:1,partial:true}), {session_id:"session-1"}, {direction:"append"});
  assert.equal(view.status.textContent, "历史未完整加载");
});

test("deduplicates exact pages and rejects a conflicting stable identity",()=>{
  const {view}=load({domLimit:20,cacheLimit:40});view.activate("session-1");view.setOutline(outline());
  const first=page({count:5});assert.equal(view.updatePage(first,{session_id:"session-1"}).ok,true);
  assert.equal(view.updatePage(first,{session_id:"session-1"},{direction:"append"}).ok,true);
  assert.equal(view.snapshot().cached,5);
  const conflict=page({count:5});conflict.nodes[0].text="被篡改";
  assert.equal(view.updatePage(conflict,{session_id:"session-1"}).reason,"conversation_node_conflict");
  assert.equal(view.snapshot().cached,5);
  const atomic=page({count:2});atomic.nodes=[
    {stable_node_id:"message:new-before-conflict",turn_id:"turn-2",source_seq:506,kind:"message",role:"assistant",label:"新节点",text:"不得泄漏",state:null},
    {...first.nodes[0],source_seq:507,text:"冲突"},
  ];
  assert.equal(view.updatePage(atomic,{session_id:"session-1"},{direction:"append"}).reason,"conversation_node_conflict");
  assert.equal(view.snapshot().cached,5);
});

test("keeps multiple stable projection items that share one raw DSH event sequence",()=>{
  const {view}=load({domLimit:20,cacheLimit:40});view.activate("session-1");view.setOutline(outline());
  const value=page({count:2});value.nodes=[
    {stable_node_id:"message:reasoning-one",turn_id:"turn-2",source_seq:501,kind:"message",role:"assistant",label:"推理过程",text:"",state:"completed",operation_id:"reasoning:one"},
    {stable_node_id:"message:answer-one",turn_id:"turn-2",source_seq:501,kind:"message",role:"assistant",label:"Agent",text:"完成",state:"completed",operation_id:"answer:one"},
  ];
  const result=view.updatePage(value,{session_id:"session-1"},{direction:"replace"});
  assert.equal(result.ok,true);assert.equal(view.snapshot().cached,2);assert.equal(view.snapshot().materialized,2);
  assert.deepEqual(view.list.children.map(node=>node.dataset.stableNodeId),["message:reasoning-one","message:answer-one"]);
});

test("requests exact within-turn cursors and crosses outline turns at boundaries",async()=>{
  const requests=[],{view}=load({onPage:(request,context)=>{requests.push({request,context});}});
  view.activate("session-1");view.setOutline(outline());
  await view.ensureLatest();
  assert.deepEqual({...requests[0].request},{turn_id:"turn-2",source_seq:501,stable_node_id:null,before_seq:1001,limit:500});
  view.updatePage(page({hasOlder:true,hasNewer:true}),{session_id:"session-1"},{direction:"replace"});
  await view.requestOlder();await view.requestNewer();
  assert.equal(requests[1].request.before_seq,501);assert.equal(requests[2].request.after_seq,503);
  view.updatePage(page({turn:"turn-2",start:501,count:3}),{session_id:"session-1"},{direction:"replace"});
  await view.requestOlder();
  assert.deepEqual({...requests.at(-1).request},{turn_id:"turn-1",source_seq:1,stable_node_id:null,before_seq:501,limit:500});
});

test("bounds ten thousand Host events without materializing or retaining them all",()=>{
  const {view}=load({domLimit:50,cacheLimit:300});view.activate("session-1");view.setOutline(outline([{turn_id:"turn-1",start_seq:1,end_seq:10000}]));
  for(let pageIndex=0;pageIndex<20;pageIndex++){
    view.updatePage(page({turn:"turn-1",start:pageIndex*500+1,count:500}),{session_id:"session-1"},{direction:"append"});
  }
  assert.equal(view.snapshot().cached,300);assert.equal(view.snapshot().materialized,50);
  assert.equal(view.list.children.length,50);
  assert.equal(view.list.children.at(-1).dataset.sourceSeq,"10000");
});

test("reuses keyed rows, keeps the virtual feed quiet and exposes bounded set positions",()=>{
  const {view,rendered,host}=load({domLimit:20,cacheLimit:60});view.activate("session-1");view.setOutline(outline());
  view.updatePage(page({start:501,count:20}),{session_id:"session-1"},{direction:"replace"});
  const firstNodes=[...view.list.children],firstRenderCount=rendered.length;
  assert.equal(view.list["aria-live"],"off");assert.equal(view.list["aria-busy"],"false");
  assert.equal(firstNodes[0].role,"article");assert.equal(firstNodes[0]["aria-posinset"],"501");assert.equal(firstNodes[0]["aria-setsize"],"1000");
  host.scrollTop=10;
  view.updatePage(page({start:521,count:3}),{session_id:"session-1"},{direction:"append"});
  assert.equal(rendered.length,firstRenderCount);assert.equal(view.list.children[0],firstNodes[0]);
});

test("keeps a reader away from the bottom anchored and announces appended events",()=>{
  const {view,host}=load({domLimit:20,cacheLimit:60});view.activate("session-1");view.setOutline(outline());
  view.updatePage(page({start:501,count:20}),{session_id:"session-1"},{direction:"replace"});
  host.scrollTop=10;
  view.updatePage(page({start:521,count:3}),{session_id:"session-1"},{direction:"append"});
  assert.equal(view.snapshot().unread,3);assert.equal(view.unread.hidden,false);
  view.unread.listeners.click();assert.equal(view.snapshot().unread,0);assert.equal(view.unread.hidden,true);
});

test("prepended Host pages preserve a semantic node anchor while exposing older cached rows",()=>{
  const {view,host}=load({domLimit:20,cacheLimit:80});view.activate("session-1");view.setOutline(outline());
  view.updatePage(page({start:501,count:20,hasOlder:true}),{session_id:"session-1"},{direction:"replace"});
  host.scrollTop=100;
  view.updatePage(page({start:481,count:20,hasOlder:false}),{session_id:"session-1"},{direction:"prepend"});
  assert.equal(host.scrollTop,100);
  assert.equal(view.list.children[0].dataset.sourceSeq,"491");
  assert.ok(view.list.children.some(node=>node.dataset.stableNodeId==="tool:node-502"));
});

test("page controls traverse cached virtual rows before asking Host for another page",async()=>{
  const requests=[],{view}=load({domLimit:20,cacheLimit:80,onPage:request=>requests.push(request)});
  view.activate("session-1");view.setOutline(outline());
  view.updatePage(page({start:501,count:40,hasOlder:true}),{session_id:"session-1"},{direction:"replace"});
  assert.equal(view.list.children[0].dataset.sourceSeq,"521");
  await view.requestOlder();assert.equal(requests.length,0);assert.equal(view.list.children[0].dataset.sourceSeq,"502");
  await view.requestOlder();assert.equal(requests.length,0);assert.equal(view.list.children[0].dataset.sourceSeq,"501");
  await view.requestOlder();assert.equal(requests.length,1);assert.equal(requests[0].before_seq,501);
});

test("marks partial Host evidence without inventing missing nodes",()=>{
  const {view}=load();view.activate("session-1");view.setOutline(outline());
  view.updatePage(page({count:1,partial:true}),{session_id:"session-1"});
  const state=view.snapshot();assert.equal(state.completeness,"partial");
  assert.deepEqual(Array.from(state.partial_reasons),["legacy_gap"]);
  assert.match(view.status.textContent,/部分数据/);
  view.updatePage(page({start:510,count:1,partial:false}),{session_id:"session-1"},{direction:"append"});
  assert.equal(view.snapshot().completeness,"partial");
  assert.deepEqual(Array.from(view.snapshot().partial_reasons),["legacy_gap"]);
});

test("a failed latest page remains retryable and clears the loading state",async()=>{
  let attempts=0;
  const {view}=load({onPage:()=>{attempts+=1;return false;}});
  view.activate("session-1");view.setOutline(outline());
  assert.equal(await view.ensureLatest(),false);
  assert.equal(view.snapshot().loading,null);
  assert.equal(await view.ensureLatest(),false);
  assert.equal(attempts,2);
});
