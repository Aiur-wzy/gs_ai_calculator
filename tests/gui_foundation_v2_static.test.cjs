'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const crypto = require('node:crypto');

const WEB = path.join(__dirname, '../src/genshin_app/gui/web');
const ROOT = path.join(__dirname, '..');

function envelope(revision = 1) {
  return {
    envelope_id:`fixture-${revision}`,
    projection:{
      contract:'native-harness-projection-v3', projection_revision:revision,
      runtime:{state:'healthy'}, turn:{state:'idle'}, focus:null, artifacts:[], candidate:null,
      interruptions:[], blocking_conditions:[], required_actions:[], issues:[], recommended_command:'conversation.send',
      available_commands:[{command_id:'conversation.send',enabled:true,scope:{},risk:'local',reason_code:null,expected_projection_revision:revision}],
    },
  };
}

test('production page isolates its App overlay and Foundation beneath the DSH theme owner', () => {
  const html = fs.readFileSync(path.join(WEB, 'index.html'), 'utf8');
  assert.equal((html.match(/id="app-overlay-root"/gu) || []).length, 1);
  assert.ok(html.indexOf('src="gui-foundation-v2.js"') < html.indexOf('src="ui.js"'));
  assert.ok(html.indexOf('href="legacy-island.css"') < html.indexOf('href="dsh-production.css"'));
  assert.ok(html.indexOf('href="dsh-production.css"') < html.indexOf('href="dsh-shell.css"'));
  assert.equal(html.includes('href="theme.css"'),false);
});

test('production manifest covers the only synchronized Foundation assets', () => {
  const manifest = JSON.parse(fs.readFileSync(path.join(WEB, 'gui-foundation-manifest.json'), 'utf8'));
  assert.equal(manifest.contract, 'gui-foundation-production-assets-v1');
  assert.deepEqual(manifest.assets.map(row => row.name), ['gui-foundation-v2.js', 'gui-foundation-tokens.css']);
  for (const asset of manifest.assets) {
    const bytes = fs.readFileSync(path.join(WEB, asset.name));
    assert.equal(asset.bytes, bytes.length);
    assert.equal(asset.sha256, crypto.createHash('sha256').update(bytes).digest('hex'));
  }
});

test('package metadata ships synchronized assets without tracking development output', () => {
  const pyproject = fs.readFileSync(path.join(ROOT, 'pyproject.toml'), 'utf8');
  const frozenSpec = fs.readFileSync(path.join(ROOT, 'genshin_app.spec'), 'utf8');
  const frontendPackage = JSON.parse(fs.readFileSync(path.join(ROOT, 'frontend/package.json'), 'utf8'));
  const runtimePackage = JSON.parse(fs.readFileSync(path.join(ROOT, 'agent-runtime/package.json'), 'utf8'));
  const frontendIgnore = fs.readFileSync(path.join(ROOT, 'frontend/.gitignore'), 'utf8').split(/\r?\n/u);
  assert.match(pyproject, /gui\/web\/\*\.json/u);
  assert.match(frozenSpec, /\("src\/genshin_app\/gui\/web", "genshin_app\/gui\/web"\)/u);
  assert.equal(frontendPackage.packageManager, runtimePackage.packageManager);
  assert.equal(frontendPackage.packageManager, 'pnpm@11.28.5');
  assert.ok(frontendIgnore.includes('node_modules/'));
  assert.ok(frontendIgnore.includes('dist/'));
});

test('Foundation bundle exposes one V2 owner without writing classic global owners', () => {
  const source = fs.readFileSync(path.join(WEB, 'gui-foundation-v2.js'), 'utf8');
  for (const forbidden of ['window.AC=', 'window.WS=', 'window.WB=', 'window.PL=', 'window.RT=', 'window.RS=']) {
    assert.equal(source.includes(forbidden), false, forbidden);
  }
  class NodeStub {}
  const world = {console, Node:NodeStub, AbortController, Promise, WeakSet, Object, Array, Map, Set};
  world.window = world;
  vm.createContext(world);
  vm.runInContext(source, world, {filename:'gui-foundation-v2.js'});
  assert.equal(world.GenshinGuiFoundationV2.contract, 'genshin-gui-foundation-v2');
  const first = world.GenshinGuiFoundationV2.acceptEnvelope(envelope(1));
  assert.equal(first.status, 'accepted');
  assert.equal(world.GenshinGuiFoundationV2.runtime().store.getState().revision, 1);
  const duplicate = world.GenshinGuiFoundationV2.acceptEnvelope(envelope(1));
  assert.equal(duplicate.status, 'duplicate');
  assert.equal(world.GenshinGuiFoundationV2.runtime().store.getState().revision, 1);
});
