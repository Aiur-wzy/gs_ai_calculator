'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const WEB = path.join(__dirname, '../src/genshin_app/gui/web');

class ClassList {
  constructor() { this.values = new Set(['hidden']); }
  add(value) { this.values.add(value); }
  remove(value) { this.values.delete(value); }
  toggle(value, enabled) { enabled ? this.values.add(value) : this.values.delete(value); }
  contains(value) { return this.values.has(value); }
}

test('provider acceptance identity shows real request and fee warning', () => {
  const nodes = new Map();
  const node = selector => {
    if (!nodes.has(selector)) nodes.set(selector, {textContent: '', classList: new ClassList()});
    return nodes.get(selector);
  };
  const world = {
    console,
    document: {querySelector: node},
    addEventListener() {},
    JSONTransport: {usesMethod: () => false},
    DS: {prose: String, raw: () => ({})},
  };
  world.window = world;
  vm.createContext(world);
  vm.runInContext(fs.readFileSync(path.join(WEB, 'app.js'), 'utf8'), world);

  world.applyAppIdentity({
    mode: 'provider_acceptance',
    build_channel: 'provider_acceptance',
    label: '真实 DeepSeek 开发联调',
    network_policy: 'deepseek_provider_only',
    service_mode: 'skipped_external_database',
  });

  assert.equal(node('#app-build-badge').textContent, '真实 DeepSeek 开发联调');
  assert.match(node('#development-acceptance-title').textContent, /真实网络请求与费用/);
  assert.match(node('#development-acceptance-message').textContent, /可能产生真实费用/);
  assert.match(node('#development-acceptance-message').textContent, /只能在 Agent 设置中输入/);
  assert.match(node('#development-acceptance-message').textContent, /共享资料库以只读方式引用/);
  assert.equal(node('#development-acceptance-banner').classList.contains('provider-live'), true);
  assert.equal(node('#development-acceptance-banner').classList.contains('hidden'), false);
});

test('native Agent development identity shows the official DeepSeek endpoint', () => {
  const nodes = new Map();
  const node = selector => {
    if (!nodes.has(selector)) nodes.set(selector, {textContent: '', classList: new ClassList()});
    return nodes.get(selector);
  };
  const world = {
    console,
    document: {querySelector: node},
    addEventListener() {},
    JSONTransport: {usesMethod: () => false},
    DS: {prose: String, raw: () => ({})},
  };
  world.window = world;
  vm.createContext(world);
  vm.runInContext(fs.readFileSync(path.join(WEB, 'app.js'), 'utf8'), world);

  world.applyAppIdentity({
    mode: 'native_agent_development',
    network_policy: 'deepseek_provider_only',
    host_network_policy: 'app_and_database_remote_disabled',
    agent_network_policy: 'deepseek_official_llm_search_public_http_fetch',
  });

  assert.equal(node('#app-build-badge').textContent, 'DeepSeek原生Agent开发版');
  assert.equal(node('#development-acceptance-title').textContent, 'DeepSeek原生Agent开发版 · 宿主受限，Agent联网研究开启');
  assert.match(node('#development-acceptance-message').textContent, /宿主App的更新检查和数据库联网已关闭/);
  assert.match(node('#development-acceptance-message').textContent, /模型请求与web_search访问受审DeepSeek官方服务/);
  assert.match(node('#development-acceptance-message').textContent, /web_fetch可访问符合公开地址约束的HTTP\(S\)目标/);
  assert.match(node('#development-acceptance-message').textContent, /可能产生真实费用/);
  assert.equal(node('#development-acceptance-banner').classList.contains('provider-live'), true);
  assert.equal(node('#development-acceptance-banner').classList.contains('hidden'), false);
});
