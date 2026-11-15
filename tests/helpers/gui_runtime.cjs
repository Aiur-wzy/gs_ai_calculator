// Shared VM test infrastructure. Always reads the real workspace src tree.
// The optional bridge stand-in exercises UI/API routing, not numeric fidelity,
// backend schema validation, source approval, filesystem permissions or game facts.
'use strict';
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const WEB = path.resolve(__dirname, '../../src/genshin_app/gui/web');
const loaded = new WeakMap();
const bridged = new WeakSet();

function webSource(name) {
  if (typeof name !== 'string' || !/^[a-z0-9-]+\.js$/u.test(name))
    throw new TypeError('Expected a local GUI JavaScript basename');
  return fs.readFileSync(path.join(WEB, name), 'utf8');
}

function loadWebScripts(context, names, {includeUi = true} = {}) {
  if (!vm.isContext(context)) throw new TypeError('Expected a vm context');
  if (!Array.isArray(names)) throw new TypeError('Expected a script list');
  const order = ['json-transport.js', ...(includeUi ? ['gui-foundation-v2.js', 'ui.js'] : []), ...names];
  const seen = loaded.get(context) || new Set();
  loaded.set(context, seen);
  for (const name of order) {
    if(['timeline.js','timeline-data.js','mechanics.js','whiteboard.js','workspace.js','plans.js'].includes(name))for(const dependency of (name==='timeline.js'?['business-ui.js','timeline-data.js']:['business-ui.js']))if(!seen.has(dependency)){
      vm.runInContext(webSource(dependency),context,{filename:path.join(WEB,dependency)});seen.add(dependency);
    }
    if (name === 'ui.js' && !seen.has('gui-foundation-v2.js')) {
      vm.runInContext(webSource('gui-foundation-v2.js'), context, {filename: path.join(WEB, 'gui-foundation-v2.js')});
      seen.add('gui-foundation-v2.js');
    }
    if (seen.has(name)) continue;
    vm.runInContext(webSource(name), context, {filename: path.join(WEB, name)});
    seen.add(name);
  }
  return context;
}

function installDocumentBridge(endpoints, documentMethods, onTransport = () => {}) {
  if (!endpoints || typeof endpoints !== 'object') throw new TypeError('Expected endpoint stubs');
  if (!Array.isArray(documentMethods)) throw new TypeError('Pass the actual codec method list');
  const allowed = new Set(documentMethods);
  return new Proxy(endpoints, {get(target, name, receiver) {
    if (name !== 'document_json_call') return Reflect.get(target, name, receiver);
    return async (method, text) => {
      if (typeof method !== 'string' || !allowed.has(method) || method === 'document_json_call')
        throw new Error('Unlisted test document method');
      if (typeof text !== 'string') throw new TypeError('Expected JSON argument text');
      const args = JSON.parse(text);
      if (!Array.isArray(args)) throw new TypeError('Expected an argument array');
      const handler = Reflect.get(target, method, target);
      if (typeof handler !== 'function') throw new Error(`Missing endpoint stub: ${method}`);
      onTransport({method, text});
      // Resolve each time so replacing a handler during a test remains visible.
      // Do not retry, approve requests, invoke another method, or alter errors.
      return JSON.stringify(await Reflect.apply(handler, target, args));
    };
  }});
}

function loadWebFile(context, filename, runOptions) {
  // Used by legacy setup factories, not by numeric-wire tests. Preserve each
  // requested script execution and its original options; add only prerequisites.
  if (!vm.isContext(context)) throw new TypeError('Expected a vm context');
  const resolved = path.resolve(filename);
  if (path.dirname(resolved) !== WEB) throw new Error('Expected a formal GUI source path');
  const name = path.basename(resolved), world = context.window;
  if (!world) throw new Error('GUI test context must define window');
  if (name === 'gui-foundation-v2.js' && world.GenshinGuiFoundationV2) return world.GenshinGuiFoundationV2;
  if(['timeline.js','timeline-data.js','mechanics.js','whiteboard.js','workspace.js','plans.js'].includes(name)){
    if(!world.GenshinBusinessUI)vm.runInContext(webSource('business-ui.js'),context,{filename:path.join(WEB,'business-ui.js')});
    if(name==='timeline.js'&&!world.TD)vm.runInContext(webSource('timeline-data.js'),context,{filename:path.join(WEB,'timeline-data.js')});
  }
  if (name === 'ui.js' || name === 'app.js') {
    if (!world.JSONTransport)
      vm.runInContext(webSource('json-transport.js'), context, {filename: path.join(WEB, 'json-transport.js')});
    if (!world.GenshinGuiFoundationV2)
      vm.runInContext(webSource('gui-foundation-v2.js'), context, {filename: path.join(WEB, 'gui-foundation-v2.js')});
    if (name === 'app.js' && !world.UI)
      vm.runInContext(webSource('ui.js'), context, {filename: path.join(WEB, 'ui.js')});
  }
  const result = vm.runInContext(webSource(name), context, runOptions);
  if (name === 'app.js' && world.pywebview?.api && !bridged.has(world.pywebview.api)) {
    const adapter = installDocumentBridge(world.pywebview.api, world.JSONTransport.methods);
    bridged.add(adapter);
    world.pywebview.api = adapter;
  }
  return result;
}

module.exports = {WEB, webSource, loadWebScripts, installDocumentBridge, loadWebFile};
