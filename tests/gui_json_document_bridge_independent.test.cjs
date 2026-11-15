const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const crypto = require('node:crypto');
const {WEB: web, loadWebScripts} = require('./helpers/gui_runtime.cjs');
const c = vm.createContext({console}); c.window = c;
c.addEventListener = () => {};
c.document = {querySelector: () => ({innerHTML:'', appendChild(){}}), createElement: () => ({})};
c.DS = {prose:x => x};
// Current public document seam; the DSH production IIFE is exercised by the
// separate real Controller/JSDOM suite, never by a retired AC fixture.
const agentScripts=['agent-document-adapter.js'];
const bridgeScripts=['json-transport.js','ui.js',...agentScripts,'app.js'];
const sources = {};
for (const name of bridgeScripts) {
  const source = fs.readFileSync(path.join(web,name),'utf8');
  sources[name] = crypto.createHash('sha256').update(source).digest('hex');
  loadWebScripts(c, [name], {includeUi:false});
}
const nodeTest = require('node:test');
const test = (name, code) => nodeTest(name, async () => { await vm.runInContext(`(async()=>{${code}})()`,c); });

nodeTest('JSON document bridge fixture uses the current pure App adapter without an old Agent owner', () => {
  const html = fs.readFileSync(path.join(web, 'index.html'), 'utf8');
  const productionScripts = [...html.matchAll(/<script\b[^>]*\bsrc="([^"]+)"/g)].map(match => match[1]);
  assert.deepEqual(productionScripts.filter(name => /^agent-/.test(name)), agentScripts);
  assert.equal(productionScripts.filter(name=>name==='dsh-production.js').length,1);
  assert.ok(productionScripts.indexOf('agent-document-adapter.js')<productionScripts.indexOf('app.js'));
  assert.ok(productionScripts.indexOf('app.js')<productionScripts.indexOf('dsh-production.js'));
});

c.assert = assert;
(async()=>{
  await test('Document calls use string bridge and both clones preserve input and response', `
    let direct=0, calls=[];
    window.pywebview={api:{run_plan_calc(){direct++;},document_json_call(method,text){calls.push([method,text]);return '{"code":0,"data":{"x":1.0}}';}}};
    const doc=JSONTransport.parse('{"x":1.0,"child":[-0.0]}');
    const response=await callApi('run_plan_calc',JSONTransport.clone(UI.clone(doc)));
    assert.equal(direct,0); assert.equal(calls.length,1);
    assert.equal(calls[0][1],'[{"x":1.0,"child":[-0.0]}]');
    assert.equal(JSONTransport.stringify(UI.clone(response.data)),'{"x":1.0}');
  `);
  await test('Bridge failure does not retry or fall back to old API', `
    let direct=0, calls=0;
    window.pywebview={api:{save_plan(){direct++;},document_json_call(){calls++;throw new Error('bridge unavailable');}}};
    const response=await callApi('save_plan','x.plan.json',{});
    assert.equal(response.code,1);assert.equal(direct,0);assert.equal(calls,1);
  `);
  await test('Lossy object response is rejected without old API fallback', `
    let direct=0,calls=0;
    window.pywebview={api:{load_plan(){direct++;},document_json_call(){calls++;return {code:0,data:{x:1}};}}};
    const response=await callApi('load_plan','x.plan.json');
    assert.equal(response.code,1);assert.equal(direct,0);assert.equal(calls,1);
  `);
  await test('Existing backend denial stays denial without confirmation or resubmission', `
    let calls=0;
    window.pywebview={api:{document_json_call(){calls++;return '{"code":17,"reason":"source_context_stale","operation_may_have_completed":true}';}}};
    const response=await callApi('run_plan_calc',{});
    assert.equal(response.code,17);assert.equal(response.reason,'source_context_stale');
    assert.equal(response.operation_may_have_completed,true);assert.equal(calls,1);
  `);
  await test('Optional parameters and undefined properties keep original JSON semantics', `
    let calls=[];
    window.pywebview={api:{document_json_call(method,text){calls.push(text);return '{"code":0}';}}};
    await callApi('new_plan');await callApi('new_plan',undefined);
    await callApi('preview_equipment_edit',{optional:undefined,keep:null},undefined);
    assert.equal(JSON.stringify(calls),JSON.stringify(['[]','[null]','[{"keep":null},null]']));
  `);
  await test('Data queries use original endpoint and never wrap into document approval', `
    let bridge=0,direct=0;
    window.pywebview={api:{document_json_call(){bridge++;},query_runtime(x){direct++;return {code:0,data:x};}}};
    const response=await callApi('query_runtime',{search:'角色'});
    assert.equal(response.data.search,'角色');assert.equal(direct,1);assert.equal(bridge,0);
  `);
  await test('Out-of-order responses retain their own numeric metadata', `
    const pending=[];
    window.pywebview={api:{document_json_call(method,text){return new Promise(resolve=>pending.push({text,resolve}));}}};
    const p1=callApi('load_plan','first'),p2=callApi('load_plan','second');
    pending[1].resolve('{"code":0,"data":{"x":1}}');
    pending[0].resolve('{"code":0,"data":{"x":1.0}}');
    const first=await p1,second=await p2;
    assert.equal(JSONTransport.stringify(first.data),'{"x":1.0}');
    assert.equal(JSONTransport.stringify(second.data),'{"x":1}');
  `);
  await test('Array edit warnings stay outside plan and do not erase API errors', `
    let input;
    window.pywebview={api:{document_json_call(method,text){input=text;return '{"code":17,"data":{"plan":{"x":1.0}}}';}}};
    const value=JSONTransport.parse('{"a":[1.0,1]}');value.a.reverse();
    const response=await callApi('compose_equipment_plan',UI.clone(value));
    assert.equal(response.code,17);assert(response.transport_warnings.length>0);
    assert(!input.includes('transport_warnings'));
    assert.equal(JSONTransport.stringify(response.data.plan),'{"x":1.0}');
  `);

})();
