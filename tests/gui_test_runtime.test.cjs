'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const vm = require('node:vm');
const {webSource, loadWebScripts, installDocumentBridge} = require('./helpers/gui_runtime.cjs');

function context() {
  const world = vm.createContext({});
  world.window = world;
  loadWebScripts(world, []);
  return world;
}

test('script prerequisites load once per VM without replacing numeric metadata', () => {
  const world = context();
  vm.runInContext('window.original=JSONTransport.parse(\'{"x":1.0}\')', world);
  loadWebScripts(world, ['json-transport.js', 'ui.js']);
  assert.equal(vm.runInContext('JSONTransport.stringify(UI.clone(original))', world), '{"x":1.0}');
});

test('source reader rejects paths outside the fixed GUI directory', () => {
  for (const name of ['../other.js', 'x/y.js', '/tmp/file.js', 'x\\y.js'])
    assert.throws(() => webSource(name));
});

test('test bridge resolves current handlers without retry or error reinterpretation', async () => {
  const world = context(), calls = [];
  const handlers = {new_plan: value => ({code: 17, reason: 'stub_denial', data: value})};
  const api = installDocumentBridge(handlers, world.JSONTransport.methods, row => calls.push(row));
  assert.deepEqual(JSON.parse(await api.document_json_call('new_plan', '[null]')),
    {code: 17, reason: 'stub_denial', data: null});
  handlers.new_plan = () => ({code: 0, data: 'replacement'});
  assert.equal(JSON.parse(await api.document_json_call('new_plan', '[]')).data, 'replacement');
  assert.equal(calls.length, 2);
});

test('test bridge rejects unlisted or invalid parameter envelopes before endpoint', async () => {
  const world = context();
  let called = 0;
  const api = installDocumentBridge({new_plan: () => called++}, world.JSONTransport.methods);
  for (const [method, text] of [['__getattribute__', '[]'], ['document_json_call', '[]'], ['new_plan', '{}']])
    await assert.rejects(api.document_json_call(method, text));
  assert.equal(called, 0);
});

test('non-document stubs remain direct and backend exceptions are not retried', async () => {
  const world = context();
  let attempts = 0;
  const handlers = {query_runtime: () => 'data', new_plan() {attempts++; throw new Error('backend failure');}};
  const api = installDocumentBridge(handlers, world.JSONTransport.methods);
  assert.equal(api.query_runtime, handlers.query_runtime);
  await assert.rejects(api.document_json_call('new_plan', '[]'), /backend failure/u);
  assert.equal(attempts, 1);
});
