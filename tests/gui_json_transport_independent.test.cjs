// Independent numeric transport regressions against the formal source tree.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const crypto = require('node:crypto');
const modulePath = require('node:path').resolve(__dirname, '../src/genshin_app/gui/web/json-transport.js');
global.window = {};
const source = fs.readFileSync(modulePath, 'utf8');
vm.runInThisContext(source, {filename: modulePath});
const T = window.JSONTransport;
const test = require('node:test');

function transportWithBudget(bytes) {
  const context = vm.createContext({}); context.window = context;
  const limited = source.replace('192 * 1024 * 1024', String(bytes));
  vm.runInContext(limited, context, {filename: modulePath});
  return context.JSONTransport;
}

test('GUI envelope has headroom around a 96 MiB calculated artifact', () => {
  assert.equal(T.MAX_BYTES, 192 * 1024 * 1024);
});

test('Incompatible synchronization user decisions use the public document wire only', () => {
  assert.equal(T.usesMethod('dsh_client_incompatible_applications'), true);
  assert.equal(T.usesMethod('dsh_client_abandon_incompatible_application'), true);
  const request = [{conversation_id:'old-conversation',expected_fingerprint:'a'.repeat(64),decision:'abandon_recovery'}];
  assert.deepEqual(T.parse(T.stringify(request)), request);
});

test('Parse, stringify and clone accept the exact byte budget and reject one extra byte', () => {
  const exact = `{"x":"${'a'.repeat(24)}"}`;
  assert.equal(Buffer.byteLength(exact), 32);
  const limited = transportWithBudget(32), value = limited.parse(exact);
  assert.equal(limited.stringify(value), exact);
  assert.equal(limited.stringify(limited.clone(value)), exact);
  assert.throws(() => limited.parse(`${exact} `), /传输上限/u);
  value.x += 'a';
  assert.throws(() => limited.stringify(value), /传输上限/u);
  assert.throws(() => limited.clone(value), /传输上限/u);
});

test('UTF-8 byte budget is enforced independently from JavaScript character count', () => {
  const exact = '{"x":"\u6c49"}', maximum = Buffer.byteLength(exact);
  assert(exact.length < maximum);
  const limited = transportWithBudget(maximum);
  assert.equal(limited.stringify(limited.clone(limited.parse(exact))), exact);
  assert.throws(() => limited.parse('{"x":"\u6c49\u6c49"}'), /传输上限/u);
});

test('Integral float, exponent, negative zero and nested clone preserve tokens', () => {
  const text = '{"v":1.0,"e":2e0,"z":-0.0,"i":1,"nested":{"v":1.0},"a":[1,1.0,-0.0]}';
  assert.equal(T.stringify(T.clone(T.parse(text))), text);
  assert.equal(T.stringify(T.clone(T.clone(T.parse(text)))), text);
});
test('Same-value assignment is an explicit edit, not unchanged identity', () => {
  const value = T.parse('{"x":1.0,"other":2.0}'); value.x = 1;
  assert.equal(T.stringify(value), '{"x":1,"other":2.0}');
});
test('delete/re-add cannot resurrect old token', () => {
  const value = T.parse('{"x":1.0}'); delete value.x; value.x = 1;
  assert.equal(T.stringify(value), '{"x":1}');
});
test('defineProperty same-value write clears only that property', () => {
  const value = T.parse('{"x":1.0,"y":2.0}');
  Object.defineProperty(value, 'x', {value:1, enumerable:true, writable:true, configurable:true});
  assert.equal(T.stringify(value), '{"x":1,"y":2.0}');
});
test('Actual changed numbers cannot reuse old spelling', () => {
  const value = T.parse('{"x":1.0,"z":-0.0}'); value.x = 2.25; value.z = 0;
  assert.equal(T.stringify(value), '{"x":2.25,"z":0}');
});
test('New subtree at same path cannot borrow old numbers', () => {
  const value = T.parse('{"child":{"x":1.0}}'); value.child = {x:1};
  assert.equal(T.stringify(value), '{"child":{"x":1}}');
});
test('Moving tracked child objects carries own provenance', () => {
  const value = T.parse('{"rows":[{"x":1.0},{"x":1}]}'); value.rows.reverse();
  assert.equal(T.stringify(value), '{"rows":[{"x":1},{"x":1.0}]}');
});
for (const [name, mutation] of [
  ['reverse', a => a.reverse()], ['splice', a => a.splice(0, 1, 1)],
  ['copyWithin', a => a.copyWithin(0, 1)], ['length', a => {a.length = 1;}],
]) test(`Primitive array ${name} invalidates all old positional tokens`, () => {
  const value = T.parse('{"a":[1.0,1]}'); mutation(value.a);
  const wire = T.encode(value);
  assert(!wire.text.includes('.0'));
  assert(wire.warnings.length > 0);
  assert(T.encode(T.clone(value)).warnings.length > 0);
});
test('Two documents never share token provenance', () => {
  const first = T.parse('{"x":1.0}'), second = T.parse('{"x":1}');
  assert.equal(T.stringify(first), '{"x":1.0}');
  assert.equal(T.stringify(second), '{"x":1}');
});
test('Editing clone does not mutate original or its token', () => {
  const original = T.parse('{"x":1.0}'), copy = T.clone(original); copy.x = 1;
  assert.equal(T.stringify(copy), '{"x":1}');
  assert.equal(T.stringify(original), '{"x":1.0}');
});
test('Own __proto__, constructor and escaped keys remain ordinary JSON data', () => {
  const text = '{"__proto__":{"polluted":1.0},"constructor":2.0,"a/b~c":3.0,"a\\\"b":4.0,"汉😀":5.0}';
  const value = T.parse(text);
  assert(Object.hasOwn(value, '__proto__'));
  assert.equal({}.polluted, undefined);
  assert.equal(T.stringify(value), text);
});
test('Decoded duplicate keys fail including nested and Unicode escapes', () => {
  for (const text of ['{"x":1,"x":2}', '{"x":1,"\\u0078":2}', '{"a":{"x":1,"x":1}}'])
    assert.throws(() => T.parse(text));
});
test('Unsafe integer literal never enters an editable rounded document', () => {
  for (const token of ['9007199254740993', '-9007199254740993', '9223372036854775807'])
    assert.throws(() => T.parse(`{"x":${token}}`));
  assert.throws(() => T.stringify({x: 9007199254740992}));
});
test('Finite large float token stays float until explicit editing', () => {
  const text = '{"x":9007199254740993.0,"tiny":1e-400}';
  assert.equal(T.stringify(T.parse(text)), text);
});
test('Nonfinite values are rejected on both paths', () => {
  for (const text of ['{"x":1e999}', '{"x":NaN}', '{"x":Infinity}']) assert.throws(() => T.parse(text));
  for (const x of [NaN, Infinity, -Infinity]) assert.throws(() => T.stringify({x}));
});
test('Depth checked before parsing and again before serialization', () => {
  assert.throws(() => T.parse('['.repeat(129) + '0' + ']'.repeat(129)));
  let value = 0; for (let i = 0; i < 129; i++) value = [value];
  assert.throws(() => T.stringify(value));
});
test('Escaped brackets inside strings do not count as depth', () => {
  const text = JSON.stringify({x: '[]{}'.repeat(300) + '\\"[]'});
  assert.equal(T.stringify(T.parse(text)), text);
});
test('Cycles, accessors, exotic objects and symbols cannot silently alter wire', () => {
  const cycle = {}; cycle.self = cycle;
  assert.throws(() => T.stringify(cycle));
  let invoked = false;
  const accessor = {}; Object.defineProperty(accessor, 'x', {enumerable:true, get(){invoked=true; return 1;}});
  assert.throws(() => T.stringify(accessor)); assert.equal(invoked, false);
  assert.throws(() => T.stringify({d:new Date(0)}));
  assert.throws(() => T.stringify({[Symbol('x')]:1}));
});
test('API selection is explicit, private and unknown methods excluded', () => {
  assert(T.usesMethod('run_plan_calc')); assert(T.usesMethod('save_plan'));
  assert(T.usesMethod('agent_recover_app_action_without_replay'));
  assert(T.usesMethod('agent_recover_candidate_application'));
  assert(T.usesMethod('agent_recover_last_applied_whiteboard_projection'));
  assert(!T.usesMethod('__getattribute__')); assert(!T.usesMethod('document_json_call'));
  assert(!T.usesMethod('update_database')); assert(!T.usesMethod('not_real'));
});
test('Reflective own descriptor cannot expose raw tracked child', () => {
  const value = T.parse('{"child":{"x":1.0}}');
  const child = Object.getOwnPropertyDescriptor(value, 'child').value;
  child.x = 1;
  assert.equal(T.stringify(value), '{"child":{"x":1}}');
});
test('Freezing ordinary JSON leaves its values and tokens unchanged', () => {
  const text = '{"x":1.0,"child":{"y":2.0},"a":[1.0,2.0]}';
  const value = T.parse(text);
  Object.freeze(value.a); Object.freeze(value.child); Object.freeze(value);
  assert.equal(T.stringify(value), text);
});
test('Descriptor-only flags preserve numbers but explicit value write is edit', () => {
  const value = T.parse('{"x":1.0,"y":2.0}');
  Object.defineProperty(value, 'x', {writable:false});
  Object.defineProperty(value, 'y', {value:2});
  assert.equal(T.stringify(value), '{"x":1.0,"y":2}');
});
test('Optional undefined transport arguments retain ordinary JSON null semantics', () => {
  assert.equal(T.stringify([]), '[]');
  assert.equal(T.stringify([undefined]), '[null]');
  assert.equal(T.stringify([null, undefined]), '[null,null]');
  assert.equal(T.stringify({x:undefined, y:null}), '{"y":null}');
  assert.equal(T.stringify([{x:undefined, y:null}]), '[{"y":null}]');
});
