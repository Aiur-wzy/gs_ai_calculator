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

test('development identity renders an unmistakable no-secret offline banner', () => {
  const nodes = new Map();
  const node = (selector) => {
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
    mode: 'development_acceptance',
    label: '本地开发验收版',
    network_policy: 'disabled',
    service_mode: 'skipped_external_database',
  });

  assert.equal(node('#app-build-badge').textContent, '本地开发验收版');
  assert.equal(node('#app-build-badge').classList.contains('hidden'), false);
  assert.match(node('#development-acceptance-title').textContent, /外网禁用/);
  assert.match(node('#development-acceptance-message').textContent, /请勿输入真实 API Key/);
  assert.match(node('#development-acceptance-message').textContent, /旧CLI Agent资料查询通道未启动/);
  assert.equal(node('#development-acceptance-banner').classList.contains('hidden'), false);
});

test('release HTML contains a hidden acceptance warning until Host identifies the build', () => {
  const html = fs.readFileSync(path.join(WEB, 'index.html'), 'utf8');
  assert.match(html, /id="development-acceptance-banner"[^>]*class="development-acceptance-banner hidden"/);
  assert.match(html, /外网已禁用，请勿输入真实 API Key/);
});
