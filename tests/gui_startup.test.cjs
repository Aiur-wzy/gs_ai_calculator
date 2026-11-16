const GUI_TEST_RUNTIME = require('./helpers/gui_runtime.cjs');
// Headless startup test: real app shell, rotation/plan loaders and mechanics init.
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const clone = value => JSON.parse(JSON.stringify(value));
const fixture = JSON.parse(fs.readFileSync(path.join(__dirname, 'fixtures/gui_equipment_queries.json'), 'utf8'));

class Element {
  constructor(tag = 'div') {
    this.tagName = tag; this.children = []; this.listeners = new Map(); this.value = ''; this.dataset = {};
    this.hidden = false; this.attributes = new Map();
    const classes = new Set();
    this.classList = {add: c => classes.add(c), remove: c => classes.delete(c), contains: c => classes.has(c),
      toggle: (c, on) => on ? classes.add(c) : classes.delete(c)};
  }
  addEventListener(type, fn, options = {}) {
    const listeners = this.listeners.get(type) || [];
    listeners.push({fn, once: options.once}); this.listeners.set(type, listeners);
  }
  async emit(type) {
    const listeners = [...(this.listeners.get(type) || [])];
    this.listeners.set(type, (this.listeners.get(type) || []).filter(x => !x.once));
    await Promise.all(listeners.map(({fn}) => fn({target: this})));
  }
  click() { return this.emit('click'); }
  append(...nodes) { for (const node of nodes) {
    if (node.parentNode) node.parentNode.children = node.parentNode.children.filter(child => child !== node);
    node.parentNode = this; this.children.push(node);
  } }
  appendChild(node) { this.append(node); return node; }
  closest(selector) { for (let node = this; node; node = node.parentNode) {
    if (selector === '[hidden]' && node.hidden) return node;
    if (selector.startsWith('#') && node.id === selector.slice(1)) return node;
  } return null; }
  set innerHTML(value) { this.children = []; this.text = value; }
  set textContent(value) { this.children = []; this.text = String(value); }
  get textContent() { return (this.text || '') + this.children.map(n => n.textContent).join(''); }
  setAttribute(name, value) { this.attributes.set(name, String(value)); }
}

function deferred() { let resolve, reject; const promise = new Promise((done, fail) => { resolve = done; reject = fail; }); return {promise, resolve, reject}; }
const turn = () => new Promise(resolve => setImmediate(resolve));

function setup({startupPath = 'D:\\中文 空格\\方案\'s.json', kind = 'genshin-plan', overrides = {}, dataInit,
  dshReady = Promise.resolve(), autoShow = true, missingDsh = false, apiInitiallyReady = true} = {}) {
  const world = new Element(), nodes = new Map(), calls = [];
  const tabs = ['whiteboard', 'rotation', 'result', 'data'].map(name => {
    const tab = new Element('button'); tab.dataset.tab = name;
    if (name === 'result') tab.classList.add('active');
    nodes.set(`[data-tab="${name}"]`, tab); return tab;
  });
  const panels = tabs.map(tab => { const panel = new Element(); panel.id = `tab-${tab.dataset.tab}`;
    nodes.set(`#${panel.id}`, panel); return panel; });
  world.window = world; world.console = console;
  world.setTimeout = setTimeout; world.clearTimeout = clearTimeout;
  world.Event=Event;world.CustomEvent=CustomEvent;
  const documentEvents = new EventTarget();
  world.document = {body: new Element('body'), createElement: tag => new Element(tag),
    querySelector: selector => { if (selector === '.tab.active') return tabs.find(tab => tab.classList.contains('active'));
      if (!nodes.has(selector)) nodes.set(selector, new Element()); return nodes.get(selector); },
    querySelectorAll: selector => selector === '.tab' ? tabs : selector === '.panel' ? panels : [],
    getElementById:id=>{if(!nodes.has('#'+id))nodes.set('#'+id,new Element());return nodes.get('#'+id);},
    addEventListener:documentEvents.addEventListener.bind(documentEvents),
    removeEventListener:documentEvents.removeEventListener.bind(documentEvents),
    dispatchEvent:event=>{calls.push({name:`event:${event.type}`,args:[event.detail]});return documentEvents.dispatchEvent(event);}};
  // The actual HTML starts the business editor hidden in a display:none parking
  // container. Legacy tab activation alone does not make the DSH page visible.
  const editorRoot = world.document.getElementById('genshin-legacy-editor'); editorRoot.hidden = true;
  const parking = world.document.getElementById('genshin-editor-parking'); parking.id = 'genshin-editor-parking'; parking.append(editorRoot);
  const editorHost = new Element();
  world.WS = {tab:()=>tabs.find(tab=>tab.classList.contains('active'))?.dataset.tab,
    navigate:page=>nodes.get(`[data-tab="${page}"]`).click(),refresh(){}};
  const show = page => {
    editorHost.append(editorRoot); editorRoot.hidden = false;
    world.GenshinEditorBridge.navigate(page);
    world.document.dispatchEvent(new CustomEvent('genshin-editor-visible',{detail:{page}}));
  };
  if (!missingDsh) {
    world.GenshinDshClient = {ready:dshReady};
    // Simulate only the existing public DSH navigation event and mounted-page
    // ACK, including delayed boot and delayed React mounting. No loader stub.
    void dshReady.then(()=>world.document.addEventListener('genshin-open-editor',event=>{
      if (autoShow) show(event.detail.page);
    }),()=>{});
  }
  // The isolated startup contract does not exercise candidate/file actions.
  // Provide their explicit adapter constructors; full domain/DOM integration
  // is covered by the actual production IIFE private-pipe tests.
  world.CandidateEditorApplication={create:()=>({})};
  world.AppEditorFileActions={create:()=>({})};
  const template = {version: 1, form: 'resolved', meta: {name: '启动方案'}, characters: [], hits: [], buffs: [], switches: [], loop_duration: 20};
  const plan = clone(fixture.new_plan.data); plan.manual_template = template;
  plan.resolution = {state: 'pending'};
  let claimed = false;
  const handlers = {
    ping: () => ({code: 0, data: {version: 'test'}}), check_update: () => ({code: 0, data: {}}),
    get_enums: () => ({code: 0, data: {display: {labels: {}}}}),
    get_display_catalog: () => ({code: 0, data: {version_line: 'release', characters: []}}),
    list_plan_details: () => ({code: 0, data: {dir: 'plans', items: []}}),
    plan_capabilities: () => clone(fixture.plan_capabilities), editor_initialized: () => ({code: 0, data: {}}),
    mechanics_capabilities: () => ({code: 0, data: {schema_version: 1}}),
    get_startup_file: () => { const selected = claimed ? null : startupPath; claimed = true; return {code: 0, data: {path: selected}}; },
    inspect_plan_file: () => ({code: 0, data: {kind}}),
    load_plan: selected => ({code: 0, data: {plan: clone(plan), path: selected, file_sha: 'loaded-sha'}}),
    load_template: selected => ({code: 0, data: {template: clone(template), path: selected}}),
    ...overrides,
  };
  const pywebview = {api: new Proxy({}, {get(_target, name) { return async (...args) => {
    calls.push({name, args});
    if (!handlers[name]) throw new Error(`Unexpected API: ${name}`);
    return handlers[name](...args);
  }; }})};
  if (apiInitiallyReady) world.pywebview = pywebview;
  vm.createContext(world);
  const web = path.join(__dirname, '../src/genshin_app/gui/web');
  for (const file of ['display', 'rotation', 'ui', 'plans', 'mechanics', 'app'])
    GUI_TEST_RUNTIME.loadWebFile(world, path.join(web, `${file}.js`), {filename: `${file}.js`});
  world.RT.renderTeamPanel = world.RT.renderPropPanel = world.RT.renderTimeline = () => {};
  world.EQ = {closeView() {}};
  world.RS = {init() {}, invalidate() {}};
  world.ED = {async init() {}};
  world.RC = {async init() {}, render() {}};
  world.MC.render = () => {};
  world.DV = {async init() { if (dataInit) await dataInit(); world.setStatus('资料页初始化完成'); }};
  world.WB = {state: {board: null}, async init() {}, render() {}};
  return {world, nodes, calls, template, plan, editorRoot, parking, editorHost, show,
    injectApi: () => { world.pywebview = pywebview; },
    start: () => world.emit('pywebviewready')};
}

test('API injected before app.js starts the editor without needing another readiness event', async () => {
  const e = setup({startupPath: null});
  await turn();
  assert.ok(e.world.GenshinEditorBridge);
  assert.equal(e.calls.filter(c => c.name === 'get_startup_file').length, 1);
  await e.start(); await e.start();
  assert.equal(e.calls.filter(c => c.name === 'editor_initialized').length, 1);
  assert.equal(e.calls.filter(c => c.name === 'ping').length, 1);
});

test('API injected after app.js waits for readiness and initializes exactly once', async () => {
  const e = setup({startupPath: null, apiInitiallyReady: false});
  await turn(); assert.equal(e.calls.length, 0); assert.equal(e.world.GenshinEditorBridge, undefined);
  e.injectApi(); await e.start(); await e.start();
  assert.ok(e.world.GenshinEditorBridge);
  assert.equal(e.calls.filter(c => c.name === 'editor_initialized').length, 1);
  assert.equal(e.calls.filter(c => c.name === 'get_startup_file').length, 1);
});

test('startup waits for RT, PL, mechanics and data initialization; repeated readiness loads only once', async () => {
  const enums = deferred(), plans = deferred(), mechanics = deferred(), data = deferred();
  const {world, nodes, calls, start} = setup({overrides: {
    get_enums: () => enums.promise, plan_capabilities: () => plans.promise,
    mechanics_capabilities: () => mechanics.promise,
  }, dataInit: () => data.promise});
  const startup = start(); await start(); await turn();
  assert.equal(calls.some(c => c.name === 'plan_capabilities'), false);
  assert.equal(calls.some(c => c.name === 'get_startup_file'), false);
  enums.resolve({code: 0, data: {display: {labels: {}}}}); await turn();
  assert.ok(calls.some(c => c.name === 'plan_capabilities'));
  assert.equal(calls.some(c => c.name === 'get_startup_file'), false);
  plans.resolve(clone(fixture.plan_capabilities)); await turn();
  assert.ok(calls.some(c => c.name === 'mechanics_capabilities'));
  assert.equal(calls.some(c => c.name === 'get_startup_file'), false);
  mechanics.resolve({code: 0, data: {schema_version: 1}}); await turn();
  assert.equal(calls.some(c => c.name === 'get_startup_file'), false);
  data.resolve(); await startup; await start();
  assert.equal(calls.filter(c => c.name === 'get_startup_file').length, 1);
  assert.equal(calls.filter(c => c.name === 'load_plan').length, 1);
  assert.equal(calls.some(c => c.name === 'load_template'), false);
  assert.deepEqual(calls.filter(c => ['inspect_plan_file', 'load_plan'].includes(c.name)).map(c => c.args),
    [["D:\\中文 空格\\方案's.json"], ["D:\\中文 空格\\方案's.json"], ["D:\\中文 空格\\方案's.json"]]);
  assert.ok(world.MC.caps);
  assert.equal(world.PL.state.fileSha, 'loaded-sha');
  assert.equal(nodes.get('[data-tab="rotation"]').classList.contains('active'), true);
  assert.equal(nodes.get('[data-tab="result"]').classList.contains('active'), false);
  assert.match(nodes.get('#status-bar').textContent, /已打开所选计算方案/);
  assert.equal(calls.some(c => /calc|^new_/.test(c.name)), false);
});

test('legacy template follows actual inspect-to-load_template route and startup state is window-local', async () => {
  const first = setup({kind: null}), second = setup({startupPath: null});
  await Promise.all([first.start(), second.start()]);
  assert.equal(first.world.PL.state.plan, null);
  assert.equal(first.world.RT.tpl().meta.name, '启动方案');
  assert.equal(first.calls.filter(c => c.name === 'load_template').length, 1);
  assert.equal(first.calls.some(c => c.name === 'load_plan'), false);
  assert.equal(second.world.RT.tpl(), null);
  assert.equal(second.calls.some(c => /inspect_plan_file|load_plan|load_template|calc|^new_/.test(c.name)), false);
});

for (const failure of ['missing', 'malformed', 'legacy-schema', 'native-schema', 'bridge']) {
  test(`${failure} startup failure keeps existing input and displays the original error`, async () => {
    const message = '启动文件错误：无法读取或校验该文件';
    const name = failure === 'bridge' ? 'get_startup_file' : failure === 'legacy-schema' ? 'load_template'
      : failure === 'native-schema' ? 'load_plan' : 'inspect_plan_file';
    const {world, nodes, calls, plan, start} = setup({kind: failure === 'legacy-schema' ? null : 'genshin-plan',
      overrides: {[name]: () => { if (failure === 'bridge') throw new Error(message); return {code: 1, message}; }}});
    world.PL.replace(plan, 'previous.json', 'previous-sha', true);
    const before = world.PL.currentInputJson();
    await start();
    assert.equal(world.PL.currentInputJson(), before);
    assert.equal(world.RT.state.path, 'previous.json');
    assert.equal(world.PL.state.fileSha, 'previous-sha');
    assert.match(nodes.get('#status-bar').textContent, /启动文件错误/);
    assert.equal(calls.some(c => /calc|^new_|save_/.test(c.name)), false);
    assert.equal(calls.filter(c => c.name === 'get_startup_file').length, 1);
  });
}

test('initialization exceptions are visible and cannot silently skip startup loading', async () => {
  const {nodes, calls, start} = setup({dataInit: () => {throw new Error('初始化测试错误');}});
  await start();
  assert.match(nodes.get('#status-bar').textContent, /界面初始化失败/);
  assert.match(nodes.get('#status-bar').textContent, /初始化测试错误/);
  assert.equal(calls.some(c => c.name === 'get_startup_file'), false);
});

test('whiteboard startup routes the linked source to the whiteboard without composing it', async () => {
  const {world, calls, start} = setup({kind: 'genshin-whiteboard'});
  const loaded = [];
  world.WB = {async init() {}, render() {}, async load(path) { loaded.push(path); return true; }};
  await start();
  assert.deepEqual(loaded, ["D:\\中文 空格\\方案's.json"]);
  assert.equal(calls.some(c => /^(load_plan|load_template|run_plan_calc|compose_plan)$/.test(c.name)), false);
});

for (const kind of ['genshin-plan', null, 'genshin-whiteboard']) {
  test(`DSH startup ${kind || 'legacy'} waits for shell, imports once and confirms the matching visible page`, async () => {
    const dsh = deferred(), e = setup({kind, dshReady:dsh.promise, autoShow:false}), loadedBoards = [];
    const page = kind === 'genshin-whiteboard' ? 'whiteboard' : 'rotation';
    if (kind === 'genshin-whiteboard') e.world.WB = {async init() {}, render() {}, async load(path) {loadedBoards.push(path); return true;}};
    const startup = e.start(); await turn();
    assert.equal(e.calls.filter(call=>call.name==='get_startup_file').length,1);
    assert.equal(e.calls.some(call=>/^(inspect_plan_file|load_plan|load_template|event:genshin-open-editor)$/.test(call.name)),false);
    assert.equal(e.editorRoot.hidden,true); assert.equal(e.editorRoot.parentNode,e.parking);
    dsh.resolve(); await turn();
    const navigation = e.calls.filter(call=>call.name==='event:genshin-open-editor');
    assert.deepEqual(clone(navigation.map(call=>call.args[0])),[{page}]);
    assert.equal(e.calls.filter(call=>/^(load_plan|load_template)$/.test(call.name)).length,kind==='genshin-whiteboard'?0:1);
    assert.deepEqual(loadedBoards,kind==='genshin-whiteboard'?["D:\\中文 空格\\方案's.json"]:[]); assert.equal(e.editorRoot.hidden,true);
    // An unrelated mounted page must not acknowledge this requested page.
    e.world.document.dispatchEvent(new CustomEvent('genshin-editor-visible',{detail:{page:'data'}})); await turn();
    assert.equal(e.calls.filter(call=>/^(load_plan|load_template)$/.test(call.name)).length,kind==='genshin-whiteboard'?0:1);
    assert.deepEqual(loadedBoards,kind==='genshin-whiteboard'?["D:\\中文 空格\\方案's.json"]:[]);
    e.show(page); await startup;
    assert.equal(e.editorRoot.hidden,false); assert.equal(e.editorRoot.parentNode,e.editorHost);
    assert.equal(e.world.WS.tab(),page);
    assert.equal(e.calls.filter(call=>call.name==='get_startup_file').length,1);
    if (kind === 'genshin-whiteboard') assert.deepEqual(loadedBoards,["D:\\中文 空格\\方案's.json"]);
    else assert.equal(e.calls.filter(call=>call.name===(kind?'load_plan':'load_template')).length,1);
    assert.equal(e.calls.some(call=>/calc|compose|^new_|^save_/.test(call.name)),false);
  });
}

test('DSH startup cold no-file launch neither waits for DSH nor opens or creates a document', async () => {
  const dsh = deferred(), e = setup({startupPath:null,dshReady:dsh.promise});
  await e.start();
  assert.equal(e.editorRoot.hidden,true); assert.equal(e.editorRoot.parentNode,e.parking);
  assert.equal(e.calls.some(call=>/^(inspect_plan_file|load_plan|load_template|event:genshin-open-editor)$/.test(call.name)),false);
  assert.equal(e.calls.some(call=>/calc|compose|^new_|^save_/.test(call.name)),false);
});

for (const failure of ['missing-client','boot-rejected']) {
  test(`DSH startup ${failure} reports a visible failure without hidden imports`, async () => {
    const dsh = deferred(), e = setup({dshReady:dsh.promise,missingDsh:failure==='missing-client'});
    const startup = e.start(); await turn();
    if (failure === 'boot-rejected') dsh.reject(new Error('shell boot rejected'));
    await startup;
    assert.equal(e.editorRoot.hidden,true);
    assert.equal(e.calls.some(call=>/^(inspect_plan_file|load_plan|load_template|event:genshin-open-editor)$/.test(call.name)),false);
    const shell = e.nodes.get('#dsh-app-root');
    assert.ok(shell.children.some(node=>node.attributes.get('role')==='alert'&&/工作台未能启动，尚未读取方案文件/.test(node.textContent)));
    assert.match(shell.textContent,/中文 空格/);
  });
}
