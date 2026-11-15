"use strict";

const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

class Node {
  constructor(tag = "#text", value = "") {
    this.tagName = tag;
    this.children = [];
    this.attributes = {};
    this.dataset = {};
    this.listeners = {};
    this._text = value;
    this.disabled = false;
  }
  append(...rows) { this.children.push(...rows); }
  replaceChildren(...rows) { this.children = [...rows]; this._text = ""; }
  set textContent(value) { this.children = []; this._text = String(value); }
  get textContent() { return this._text + this.children.map(row => row.textContent).join(""); }
  setAttribute(key, value) { this.attributes[key] = String(value); }
  addEventListener(key, callback) { this.listeners[key] = callback; }
  async emit(key, event = {}) { return this.listeners[key]?.({preventDefault() {}, ...event}); }
}

function load() {
  const document = {
    createElement: tag => new Node(tag),
    createTextNode: value => new Node("#text", String(value)),
  };
  const world = {document, URL, location:{href:"https://app.invalid/"}, navigator:{}};
  world.window = world;
  vm.createContext(world);
  for (const name of ["agent-message-renderer.js", "agent-timeline-node-renderer.js"]) {
    vm.runInContext(
      fs.readFileSync(path.join(__dirname, "../src/genshin_app/gui/web", name), "utf8"),
      world,
    );
  }
  const all = node => [node, ...node.children.flatMap(all)];
  return {world, document, all};
}

const base = (kind, overrides = {}) => ({
  stable_node_id:`${kind}:node-one`,
  turn_id:"turn-one",
  segment_id:"segment-one",
  operation_id:"operation-one",
  source_seq:12,
  kind,
  category:kind === "research" ? "web" : "engine",
  role:null,
  label:`${kind} 节点`,
  text:null,
  state:"completed",
  result_code:null,
  resource_ref:"resource-one",
  workflow_stage:"numeric_closure",
  occurred_at:"2026-10-25T00:00:00Z",
  ...overrides,
});

test("tool card exposes reviewed source, duration, input/result and recovery state", () => {
  const {world, document} = load();
  const card = world.AgentTimelineNodeRenderer.renderNode(document, base("tool"), {
    details:{
      source:"engine",
      elapsed_ms:843,
      input_summary:"读取角色条目：神里绫华",
      result_summary:"返回 6 个倍率组件",
      recovery_state:"settled",
      ignored_secret:"sk-not-rendered",
    },
  });
  assert.equal(card.dataset.stableNodeId, "tool:node-one");
  assert.match(card.textContent, /来源engine/);
  assert.match(card.textContent, /耗时843 ms/);
  assert.match(card.textContent, /参数摘要读取角色条目/);
  assert.match(card.textContent, /结果摘要返回 6 个倍率组件/);
  assert.match(card.textContent, /恢复状态settled/);
  assert.doesNotMatch(card.textContent, /sk-not-rendered/);
  assert.equal(card.dataset.density, "summary");
  assert.equal(card.children.some(node => node.tagName === "details"), true);
});

test("research and failure cards show provenance and safe recovery choices", async () => {
  const {world, document, all} = load();
  const research = world.AgentTimelineNodeRenderer.renderNode(document, base("research"), {
    details:{query:"元素附着 ICD", domains:["wiki.example"], sources:["规则页", "测试记录"]},
  });
  assert.match(research.textContent, /查询元素附着 ICD/);
  assert.match(research.textContent, /检索域wiki.example/);
  assert.match(research.textContent, /来源规则页、测试记录/);

  const actions = [];
  const failure = world.AgentTimelineNodeRenderer.renderNode(document, base("tool", {
    state:"failed", result_code:"engine_binding_missing", text:"计算未闭合",
  }), {
    details:{
      root_cause:"缺少 damage_event 绑定",
      failure_stage:"numeric_closure",
      provider_disposition:"请求可能已经由 provider 接收",
      recovery_actions:[{action_id:"retry-validation", label:"重新校验"}],
    },
    onRecoveryAction:value => actions.push(value),
  });
  assert.match(failure.textContent, /首因缺少 damage_event 绑定/);
  assert.match(failure.textContent, /失败阶段numeric_closure/);
  assert.match(failure.textContent, /请求可能已经由 provider 接收/);
  assert.equal(failure.dataset.density, undefined);
  const button = all(failure).find(node => node.dataset.actionId === "retry-validation");
  await button.emit("click");
  assert.equal(actions.length, 1);
  assert.equal(actions[0].action_id, "retry-validation");
  assert.equal(actions[0].stable_node_id, "tool:node-one");
  assert.equal(actions[0].turn_id, "turn-one");
});

test("successful zero-code tool and App settlements never render failure recovery", () => {
  const {world, document} = load();
  const tool = world.AgentTimelineNodeRenderer.renderNode(document, base("tool", {
    state:"settled_success", result_code:"code_0", text:"读取完成",
  }));
  const action = world.AgentTimelineNodeRenderer.renderNode(document, base("app_action", {
    state:"settled_success", result_code:"code_0", text:"工作区读取完成",
  }), {details:{reason:"只读验收"}});
  for (const card of [tool, action]) {
    assert.match(card.textContent, /已完成/);
    assert.doesNotMatch(card.textContent, /settled_success/);
    assert.doesNotMatch(card.textContent, /失败与恢复/);
    assert.doesNotMatch(card.textContent, /首因/);
    assert.doesNotMatch(card.textContent, /不能据此判断是否计费/);
  }

  const failed = world.AgentTimelineNodeRenderer.renderNode(document, base("tool", {
    state:"failed", result_code:"code_0", text:"上游状态冲突",
  }));
  assert.match(failed.textContent, /失败与恢复/);
  assert.match(failed.textContent, /上游状态冲突/);
});

test("candidate, App action, question and checkpoint remain display-only domain cards", () => {
  const {world, document} = load();
  const candidate = world.AgentTimelineNodeRenderer.renderNode(document, base("candidate"), {
    details:{revision:7, diff_count:3, can_apply:false},
  });
  assert.match(candidate.textContent, /Revision7/);
  assert.match(candidate.textContent, /差异数量3/);
  assert.match(candidate.textContent, /可应用否/);
  assert.equal(candidate.children.flatMap(row => row.children).some(row => row.tagName === "button"), false);

  const action = world.AgentTimelineNodeRenderer.renderNode(document, base("app_action", {text:"打开用户选择的文件"}), {details:{reason:"用户明确请求"}});
  const question = world.AgentTimelineNodeRenderer.renderNode(document, base("question", {state:"open", text:"选择冻结口径"}));
  const checkpoint = world.AgentTimelineNodeRenderer.renderNode(document, base("checkpoint"), {details:{integrity:"verified", source:"turn-one"}});
  assert.match(action.textContent, /请求原因用户明确请求/);
  assert.match(question.textContent, /问题状态等待回答/);
  assert.match(checkpoint.textContent, /完整性verified/);
});

test("primary status and raw gs tool labels are humanized while technical values remain attributes", () => {
  const {world, document, all} = load();
  const card = world.AgentTimelineNodeRenderer.renderNode(document, base("tool", {
    label:"gs_read_skill", state:"finish_tool_calls",
  }));
  assert.match(card.textContent, /读取拉表说明/);
  assert.match(card.textContent, /正在执行工具/);
  assert.doesNotMatch(card.textContent, /gs_read_skill|finish_tool_calls/);
  const titled = all(card).filter(node => node.attributes?.title);
  assert.equal(titled.some(node => node.attributes.title === "gs_read_skill"), true);
  assert.equal(titled.some(node => node.attributes.title === "技术状态：finish_tool_calls"), true);
});

test("durable reasoning timeline nodes stay separate and collapsed", () => {
  const {world, document, all} = load();
  const card = world.AgentTimelineNodeRenderer.renderNode(document, base("message", {
    operation_id:"reasoning:durable-one", role:"assistant", category:"provider",
    label:"Agent 回复", text:"历史推理文本", state:"completed",
  }));
  assert.match(card.textContent, /推理过程.*历史推理文本/s);
  assert.doesNotMatch(card.textContent, /Agent/);
  const details = all(card).find(node => node.tagName === "details");
  assert.ok(details); assert.equal(details.open, undefined);
});

test("process group renders bounded children in the same event order", () => {
  const {world, document, all} = load();
  const group = world.AgentTimelineNodeRenderer.renderNode(document, base("process_group", {state:"running"}), {
    details:{
      process_summary:"运行中 · 2 个步骤",
      children:[
        {node:base("tool", {stable_node_id:"tool:child-one", source_seq:13, label:"读取数据库"}), details:{result_summary:"完成"}},
        {node:base("research", {stable_node_id:"research:child-two", source_seq:14, label:"查询资料"}), details:{query:"规则", sources:["来源A"]}},
      ],
    },
  });
  assert.match(group.textContent, /运行中 · 2 个步骤/);
  const ids = all(group).map(node => node.dataset?.stableNodeId).filter(Boolean);
  assert.deepEqual(ids, ["process_group:node-one", "tool:child-one", "research:child-two"]);
});

test("message nodes reuse safe Markdown, code copy and reviewed external-open callbacks", async () => {
  const {world, document, all} = load();
  const copied = [], opened = [];
  const card = world.AgentTimelineNodeRenderer.renderNode(document, base("message", {
    role:"assistant", text:"[资料](https://example.com)\n\n```txt\n内容\n```",
  }), {
    copyCode:async value => copied.push(value),
    confirmExternal:async () => true,
    openExternal:async request => opened.push(request.url),
  });
  const link = all(card).find(node => node.tagName === "a");
  const codeCopy = all(card).find(node => node.className === "ac-code-copy");
  await link.emit("click");
  await codeCopy.emit("click");
  assert.deepEqual(opened, ["https://example.com/"]);
  assert.deepEqual(copied, ["内容"]);
});

test("invalid identities and unbounded structured details fail closed", () => {
  const {world, document} = load();
  assert.throws(
    () => world.AgentTimelineNodeRenderer.renderNode(document, base("tool", {stable_node_id:""})),
    /timeline_node_invalid/,
  );
  assert.throws(
    () => world.AgentTimelineNodeRenderer.renderNode(document, base("tool"), {details:{domains:Array(33).fill("x")}}),
    /timeline_detail_list_invalid/,
  );
});
