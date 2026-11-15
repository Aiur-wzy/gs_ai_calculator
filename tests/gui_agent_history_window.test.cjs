const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const test = require("node:test");
const assert = require("node:assert/strict");

class Node {
  constructor(tag) { this.tagName=tag; this.children=[]; this.dataset={}; this.listeners={}; this.textContent=""; this.className=""; this.tabIndex=-1; this.focused=false; }
  append(...children) { this.children.push(...children); }
  replaceChildren(...children) { this.children=[...children]; }
  addEventListener(name, callback) { this.listeners[name]=callback; }
  focus() { this.focused=true; }
}
const document = {createElement: tag => new Node(tag)};
const window = {document};
const context = vm.createContext({window, document, Object, Set, Array, Number, String, TypeError});
vm.runInContext(fs.readFileSync(path.join(__dirname, "../src/genshin_app/gui/web/agent-history-window.js"), "utf8"), context);
const API = window.AgentHistoryWindow;

const sample = () => ({
  contract:"agent-history-window-v1", session_id:"session-1",
  projection_revision:3, source_cursor:9, completeness:"partial",
  partial_reasons:["older_history_evicted"],
  anchor:{turn_id:"turn-1",source_seq:4,stable_node_id:"message-1"},
  has_older:false, has_newer:false,
  nodes:[
    {stable_node_id:"message-1",turn_id:"turn-1",source_seq:4,kind:"message",role:"user",label:"用户",text:"请计算",state:null},
    {stable_node_id:"tool-1",turn_id:"turn-1",source_seq:5,kind:"tool",role:null,label:"查询资料",text:"已完成",state:"completed"},
  ],
});

test("strictly parses a bounded read-only history window", () => {
  const result = API.parse(sample(), {session_id:"session-1",turn_id:"turn-1",source_seq:4,stable_node_id:"message-1"});
  assert.equal(result.ok, true);
  assert.equal(result.projection.nodes.length, 2);
  assert.equal(Object.isFrozen(result.projection.nodes[0]), true);
});

test("rejects identity drift, unsorted rows, duplicate ids and missing anchors", () => {
  assert.equal(API.parse(sample(), {session_id:"other"}).reason, "history_window_identity_invalid");
  const unsorted=sample();unsorted.nodes[1].source_seq=3;
  assert.equal(API.parse(unsorted).reason, "history_window_node_invalid");
  const duplicate=sample();duplicate.nodes[1].stable_node_id="message-1";
  assert.equal(API.parse(duplicate).reason, "history_window_node_invalid");
  const duplicateSeq=sample();duplicateSeq.nodes[1].source_seq=4;
  assert.equal(API.parse(duplicateSeq).ok, true);
  const missing=sample();missing.anchor.stable_node_id="not-present";
  assert.equal(API.parse(missing).reason, "history_window_anchor_missing");
});

test("preserves reviewed structured-card fields and rejects local resource paths", () => {
  const value=sample();Object.assign(value.nodes[1],{segment_id:"segment-1",operation_id:"call-1",category:"app_tool",result_code:"ok",resource_ref:"tool:call-1",occurred_at:"2026-10-25T00:00:00Z",workflow_stage:"event_topology"});
  const parsed=API.parse(value,{session_id:"session-1"});assert.equal(parsed.ok,true);assert.equal(parsed.projection.nodes[1].category,"app_tool");assert.equal(parsed.projection.nodes[1].resource_ref,"tool:call-1");
  value.nodes[1].resource_ref="C:\\private\\result.json";assert.equal(API.parse(value).reason,"history_window_node_invalid");
});

test("accepts a provably partial window when the requested stable anchor was not loaded", () => {
  const value=sample();value.nodes=[];value.anchor.stable_node_id="message-missing";
  value.partial_reasons=["stable_anchor_not_loaded"];
  const result=API.parse(value,{session_id:"session-1",stable_node_id:"message-missing"});
  assert.equal(result.ok,true);assert.equal(result.projection.nodes.length,0);
});

test("accepts the exact Host message-node shape and preserves workflow stage", () => {
  const value=sample();value.completeness="complete";value.partial_reasons=[];
  value.nodes=[{stable_node_id:"message-1",turn_id:"turn-1",source_seq:4,kind:"message",role:"assistant",text:"计算完成",workflow_stage:"strict_calculation"}];
  const result=API.parse(value,{session_id:"session-1"});
  assert.equal(result.ok,true);assert.equal(result.projection.nodes[0].workflow_stage,"strict_calculation");
  assert.equal(result.projection.nodes[0].label,"");assert.equal(result.projection.nodes[0].state,null);
});

test("renders only text nodes and keeps the window outside the live store", () => {
  const host=new Node("section");let closed=0,selected=null;
  const view=API.create(document,host,{onClose:()=>closed++,onSelect:row=>{selected=row.stable_node_id;}});
  assert.equal(view.update(sample(),{session_id:"session-1"}).ok,true);
  assert.equal(host.children.length,2);
  const body=host.children[1];assert.equal(body.children.length,2);
  body.children[0].listeners.click();assert.equal(selected,"message-1");
  host.children[0].children.at(-1).listeners.click();assert.equal(closed,1);
  view.clear();assert.equal(host.children.length,0);
});

test("renders bidirectional Host pagination without merging an unbounded history", () => {
  const value=sample();value.has_older=true;value.has_newer=true;
  value.older_cursor=4;value.newer_cursor=5;value.total_nodes=10000;
  const host=new Node("section"),pages=[];
  const view=API.create(document,host,{onPage:request=>pages.push(request)});
  assert.equal(view.update(value,{session_id:"session-1"}).ok,true);
  assert.equal(host.children.length,3);
  const pager=host.children[2];pager.children[0].listeners.click();pager.children[1].listeners.click();
  assert.equal(pages[0].before_seq,4);assert.equal(pages[1].after_seq,5);
  assert.equal(view.snapshot().total_nodes,10000);
});
