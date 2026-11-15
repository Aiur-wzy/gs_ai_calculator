const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const source = fs.readFileSync(path.join(__dirname, '../src/genshin_app/gui/web/theme.js'), 'utf8');
const tokenCss = fs.readFileSync(path.join(__dirname, '../src/genshin_app/gui/web/gui-foundation-tokens.css'), 'utf8');
const componentCss = fs.readFileSync(path.join(__dirname, '../src/genshin_app/gui/web/theme.css'), 'utf8');
const css = `${tokenCss}\n${componentCss}`;

function load({stored = null, dark = false, storageThrows = false, bodyOnly = false} = {}) {
  const events = [], listeners = [], values = new Map();
  if (stored !== null) values.set('genshin.theme.v1', stored);
  const media = {matches: dark, addEventListener(type, fn) { if (type === 'change') listeners.push(fn); }};
  const root = {dataset: {}, style: {}};
  class CustomEvent { constructor(type, options) { this.type = type; this.detail = options.detail; } }
  const localStorage = {
    getItem(key) { if (storageThrows) throw new Error('disabled'); return values.get(key) ?? null; },
    setItem(key, value) { if (storageThrows) throw new Error('disabled'); values.set(key, value); }
  };
  const document = {dispatchEvent(event) { events.push(event); },getElementById:id=>id==='genshin-legacy-editor'?root:null};
  if (bodyOnly) document.body = root; else document.documentElement = root;
  const world = {console, CustomEvent, localStorage, matchMedia: () => media, document,
    getComputedStyle: () => ({getPropertyValue: key => key === '--color-accent' ? ' #123456 ' : ''})};
  world.window = world;
  vm.createContext(world); vm.runInContext(source, world, {filename: 'theme.js'});
  return {world, root, media, listeners, events, values};
}

test('compatibility facade cannot replace DSH theme ownership at startup', () => {
  const {world, root, events} = load({stored: 'dark'});
  assert.equal(root.dataset.theme, undefined);
  assert.equal(root.dataset.themeResolved, undefined);
  assert.equal(root.style.colorScheme, undefined);
  assert.equal(world.ThemeController.current().preference,'system');
  assert.equal(events.length, 0);
  assert.deepEqual([...world.ThemeController.choices], ['system', 'light', 'dark']);
});

test('DSH accepted snapshots notify legacy consumers without a second OS listener', () => {
  const env = load({dark: false});
  assert.equal(env.listeners.length,0);
  env.world.ThemeController.accept({preference:'system',resolved:'dark'});
  assert.equal(env.world.ThemeController.current().resolved,'dark');
  assert.equal(env.events.at(-1).type,'genshin-theme-change');
  assert.equal(env.events.at(-1).detail.preference, 'system');
  assert.equal(env.events.at(-1).detail.resolved, 'dark');
});

test('user theme requests go to DSH and do not persist a competing preference', () => {
  const env = load({dark: true});
  const applied = env.world.ThemeController.set('light');
  assert.equal(applied.preference, 'system');
  assert.equal(env.values.has('genshin.theme.v1'),false);
  assert.equal(env.events.at(-1).type,'genshin-theme-request');
  assert.equal(env.events.at(-1).detail.preference,'light');
  assert.equal(env.world.ThemeController.token('color-accent'), '#123456');
});

test('invalid requests and unavailable storage do not create a theme side effect', () => {
  const invalid = load({stored: 'sepia', dark: true});
  invalid.world.ThemeController.set('sepia');assert.equal(invalid.events.length,0);
  const locked = load({storageThrows: true, dark: false});
  assert.doesNotThrow(() => locked.world.ThemeController.set('dark'));
  assert.equal(locked.root.dataset.theme, undefined);
  assert.equal(locked.events.at(-1).detail.preference,'dark');
});

test('offline body-only projections can receive an upstream theme snapshot', () => {
  const env = load({stored: 'light', bodyOnly: true});
  env.world.ThemeController.accept({preference:'light',resolved:'light'});
  assert.equal(env.world.ThemeController.resolve(),'light');
  assert.equal(env.root.dataset.theme, undefined);
});

test('theme stylesheet publishes semantic, legacy and accessibility contracts', () => {
  for (const selector of [':root[data-theme="light"]', ':root[data-theme="dark"]', ':root[data-theme="system"]'])
    assert.match(css, new RegExp(selector.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')));
  for (const token of ['--color-canvas', '--color-sidebar', '--color-surface', '--color-text-primary',
    '--color-focus-ring', '--color-success', '--color-warning', '--color-danger', '--color-axis-damage',
    '--color-axis-buff', '--color-axis-reaction', '--color-axis-lane-even', '--color-axis-lane-odd',
    '--color-axis-coverage-lane', '--color-axis-cursor', '--color-axis-event-border', '--color-axis-character-6',
    '--surface-sidebar', '--surface-input', '--border-subtle', '--control-selected', '--bg', '--panel', '--border', '--text', '--muted', '--accent'])
    assert.ok(css.includes(token), token);
  assert.match(css, /prefers-color-scheme:\s*light/);
  assert.match(css, /forced-colors:\s*active/);
  assert.match(css, /data-theme-resolved="light"[^\n]*:where\(\.agent-chat/);
  assert.match(css, /data-theme-resolved="light"[^\n]*:where\(\.ws-left-pane/);
  assert.doesNotMatch(componentCss, /#[0-9a-f]{3,8}\b/iu);
});
