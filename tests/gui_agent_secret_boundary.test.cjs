'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const WEB = path.join(__dirname, '../src/genshin_app/gui/web');

function loadApp(api) {
  const nodes = new Map();
  const node = (selector) => {
    if (!nodes.has(selector)) nodes.set(selector, {
      textContent: '', innerHTML: '', appendChild() {}, classList: {
        add() {}, remove() {}, toggle() {},
      },
    });
    return nodes.get(selector);
  };
  const world = {
    console,
    document: {querySelector: node},
    addEventListener() {},
    JSONTransport: {usesMethod: () => false},
    DS: {prose: String, raw: () => ({})},
    pywebview: {api},
  };
  world.window = world;
  vm.createContext(world);
  vm.runInContext(fs.readFileSync(path.join(WEB, 'app.js'), 'utf8'), world);
  return world;
}

test('agent_connect bridge request drops its key before the pending call settles', async () => {
  let received;
  let settle;
  const world = loadApp({
    agent_connect(request) {
      received = request;
      return new Promise((resolve) => { settle = resolve; });
    },
  });
  const request = {
    id: 'deepseek-api',
    kind: 'native_api',
    api_key: 'TEST_ONLY_RENDERER_SECRET',
    remember_credential: true,
  };

  const pending = world.callApi('agent_connect', request);

  assert.equal(received, request);
  assert.equal(request.api_key, '');
  settle({code: 1, reason: 'test'});
  assert.deepEqual(await pending, {code: 1, reason: 'test'});
});

test('agent_connect synchronous bridge failures never project request details', async () => {
  const sentinel = 'TEST_ONLY_SYNC_BRIDGE_SECRET';
  const world = loadApp({
    agent_connect(request) {
      throw new Error(`serialization failed for ${JSON.stringify(request)} ${sentinel}`);
    },
  });
  const request = {
    id: 'deepseek-api',
    kind: 'native_api',
    api_key: sentinel,
    remember_credential: true,
  };

  const response = await world.callApi('agent_connect', request);

  assert.equal(request.api_key, '');
  assert.equal(response.code, 1);
  assert.equal(response.message, 'Agent 凭据接口调用失败');
  assert.equal(response.details.error, 'agent_connect_failed');
  assert.equal(JSON.stringify(response).includes(sentinel), false);
});
