'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const WEB = path.resolve(__dirname, '../src/genshin_app/gui/web');

function productionScriptOrder() {
  const html = fs.readFileSync(path.join(WEB, 'index.html'), 'utf8');
  return [...html.matchAll(/<script\s+src="([^"]+)"\s*><\/script>/gu)].map(match => match[1]);
}

test('production script order keeps action admission and attack source assembly namespaces independent', () => {
  const order = productionScriptOrder();
  const selected = order.filter(name => ['action-admissions.js', 'attack-assembly.js'].includes(name));
  assert.deepEqual(selected, ['action-admissions.js', 'attack-assembly.js']);

  const world = {console};
  world.window = world;
  world.globalThis = world;
  vm.createContext(world);
  for (const name of selected) {
    vm.runInContext(fs.readFileSync(path.join(WEB, name), 'utf8'), world, {filename: name});
  }

  assert.notEqual(world.AA, world.ASA);
  for (const name of ['catalogBrowser', 'edit', 'render', 'renderResult']) {
    assert.equal(typeof world.AA?.[name], 'function', `动作接纳缺少 ${name}`);
  }
  for (const name of ['caps', 'checkedProposal', 'digest', 'edit', 'equal', 'need', 'selectionValid']) {
    assert.equal(typeof world.ASA?.[name], 'function', `攻击来源装配缺少 ${name}`);
  }
});
