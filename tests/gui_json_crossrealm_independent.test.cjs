const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const crypto = require('node:crypto');
global.window = {};
const modulePath = require('node:path').resolve(__dirname, '../src/genshin_app/gui/web/json-transport.js'), source = fs.readFileSync(modulePath,'utf8');
vm.runInThisContext(source,{filename:modulePath});
const T=window.JSONTransport, foreign=vm.createContext({});
const make=code=>vm.runInContext(code,foreign);
const test = require('node:test');

test('Other realm ordinary object preserves standard JSON value semantics',()=>{
  const value=make('({x:1, nested:{y:2.5}, a:[0,false,null]})');
  assert.equal(T.stringify(value),'{"x":1,"nested":{"y":2.5},"a":[0,false,null]}');
});
test('Other realm arrays and mixed realm descendants are ordinary JSON',()=>{
  const value={a:make('[{x:1},2]'),b:3};
  assert.equal(T.stringify(value),'{"a":[{"x":1},2],"b":3}');
});
test('Other realm null-prototype object is valid JSON data',()=>{
  const value=make('Object.assign(Object.create(null),{x:1, a:[2]})');
  assert.equal(T.stringify(value),'{"x":1,"a":[2]}');
});
test('Other realm null-prototype array remains array',()=>{
  const value=make('(()=>{const a=[1,2];Object.setPrototypeOf(a,null);return a;})()');
  assert.equal(T.stringify(value),'[1,2]');
});
test('Frozen foreign ordinary JSON remains serializable and clonable',()=>{
  const value=make('Object.freeze({x:1,a:Object.freeze([2,3])})');
  assert.equal(T.stringify(T.clone(value)),'{"x":1,"a":[2,3]}');
});
test('Other realm custom class is rejected',()=>{
  assert.throws(()=>T.stringify(make('(new (class Sample {constructor(){this.x=1;}})())')));
});
test('Other realm subclass of Array is rejected',()=>{
  assert.throws(()=>T.stringify(make('(new (class Special extends Array {})(1,2))')));
});
test('Class named Object cannot impersonate native Object',()=>{
  assert.throws(()=>T.stringify(make('(new (class Object {constructor(){this.x=1;}})())')));
});
test('Forged constructor on null-root prototype cannot impersonate Object.prototype',()=>{
  const value=make('(()=>{const p=Object.create(null);p.constructor=Object;return Object.assign(Object.create(p),{x:1});})()');
  assert.throws(()=>T.stringify(value));
});
test('Foreign Date, RegExp and typed array remain excluded',()=>{
  for(const code of ['new Date(0)','/abc/','new Uint8Array([1,2])']) assert.throws(()=>T.stringify(make(code)));
});
test('Object branding must not execute Symbol.toStringTag getter',()=>{
  const value=make('(()=>{globalThis.tagCalls=0;const x={};Object.defineProperty(x,Symbol.toStringTag,{get(){tagCalls++;return "Object";}});return x;})()');
  assert.throws(()=>T.stringify(value));assert.equal(make('tagCalls'),0);
});
test('Custom prototype constructor accessor is never invoked',()=>{
  const value=make('(()=>{globalThis.ctorCalls=0;const p=Object.create(null);Object.defineProperty(p,"constructor",{get(){ctorCalls++;return Object;}});return Object.assign(Object.create(p),{x:1});})()');
  assert.throws(()=>T.stringify(value));assert.equal(make('ctorCalls'),0);
});
test('Own foreign accessor does not execute while validating values',()=>{
  const value=make('(()=>{globalThis.getCalls=0;return Object.defineProperty({},"x",{enumerable:true,get(){getCalls++;return 1;}});})()');
  assert.throws(()=>T.stringify(value));assert.equal(make('getCalls'),0);
});
test('Foreign toJSON is not executed as serialization authority',()=>{
  const value=make('(()=>{globalThis.toJSONCalls=0;return {x:1,toJSON(){toJSONCalls++;return {x:2};}};})()');
  assert.throws(()=>T.stringify(value));assert.equal(make('toJSONCalls'),0);
});
test('Local numeric provenance still works when a foreign sibling is added',()=>{
  const value=T.parse('{"x":1.0}');value.foreign=make('({y:2})');
  assert.equal(T.stringify(value),'{"x":1.0,"foreign":{"y":2}}');
});
