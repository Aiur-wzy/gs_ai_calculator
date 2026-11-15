const GUI_TEST_RUNTIME = require('./helpers/gui_runtime.cjs');
// Run with: node --test tests/gui_frontend.test.cjs (Node built-ins only).
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

class Element {
  constructor(tagName = 'div') {
    this.tagName = tagName;
    this.children = []; this.listeners = new Map(); this.attributes = {};
    this.style = {}; this.clientWidth = 1000; this.clientHeight = 500; this.clientLeft = 0;
    this.scrollLeft = 0; this.scrollTop = 0;
    const classes = new Set();
    this.classList = {add: (...items) => items.forEach((item) => classes.add(item)),
      remove: (...items) => items.forEach((item) => classes.delete(item)),
      toggle: (item, enabled) => enabled ? classes.add(item) : classes.delete(item)};
  }
  get childElementCount() { return this.children.length; }
  addEventListener(type, fn) {
    if (!this.listeners.has(type)) this.listeners.set(type, new Set());
    this.listeners.get(type).add(fn);
  }
  removeEventListener(type, fn) { this.listeners.get(type)?.delete(fn); }
  emit(type, values = {}) {
    const event = {button: 0, pointerId: 1, clientX: 0, clientY: 0, target: this,
      preventDefault() { this.defaultPrevented = true; }, stopPropagation() { this.stopped = true; }, ...values};
    [...(this.listeners.get(type) || [])].forEach((fn) => fn(event));
    return event;
  }
  appendChild(child) { this.children.push(child); return child; }
  append(...children) { this.children.push(...children); }
  replaceChildren(...children) { this.children = [...children]; }
  insertRow() { return this.appendChild(new Element('tr')); }
  insertCell() { return this.appendChild(new Element('td')); }
  set innerHTML(value) { this.children = []; this.text = value; }
  set textContent(value) { this.children = []; this.text = value; }
  get textContent() { return (this.text || '') + this.children.map((c) => c.textContent).join(''); }
  setAttribute(key, value) { this.attributes[key] = value; }
  removeAttribute(key) { delete this.attributes[key]; }
  setPointerCapture(id) { this.captured = id; }
  hasPointerCapture(id) { return this.captured === id; }
  releasePointerCapture() { this.captured = null; }
  focus() { this.focused = true; }
  getBoundingClientRect() { return {left: 0, top: 0, width: this.clientWidth, height: this.clientHeight}; }
  closest(selector) {
    const classes = String(this.attributes.class || '').split(' ');
    return selector.split(', ').some((item) => classes.includes(item.slice(1))) ? this : null;
  }
}

function template(name = '方案 A') {
  return {version: 1, form: 'resolved', meta: {name},
    enemy: {level: 90, resistance: {default: 0.1}},
    characters: [{id: 'char1', level: 90, stats: {atk: 2000}}],
    hits: [{t: 1, character: 'char1', skill: 'skill'}], buffs: [], switches: [],
    loop_duration: 20};
}

function result(total = 100) {
  return {code: 0, data: {result: {summary: {total_damage: total, dps: total / 20},
    hits: [], buffs_coverage: [], assumptions: []}}};
}

function descendants(node) {
  return [node, ...node.children.flatMap(descendants)];
}

function setup({renderPanels = false, renderResults = false} = {}) {
  const nodes = new Map();
  const world = new Element();
  world.window = world;
  world.console = console;
  const body = new Element();
  world.document = {
    body,
    getElementById(id) {
      return [...nodes.values(), body].flatMap(descendants).find((node) => node.id === id);
    },
    querySelector(selector) {
      if (!nodes.has(selector)) nodes.set(selector, new Element());
      return nodes.get(selector);
    },
    querySelectorAll() { return []; },
    createElement(tag) { return new Element(tag); },
    createElementNS(ns, tag) { return new Element(tag); },
  };
  world.pywebview = {api: {run_calc: async () => result()}};
  vm.createContext(world);
  const web = path.join(__dirname, '..', 'src', 'genshin_app', 'gui', 'web');
  for (const file of ['display.js', 'rotation.js', 'panel.js', 'chars.js', 'timeline-view.js', 'timeline.js', 'result.js', 'format.js', 'data.js', 'app.js']) {
    GUI_TEST_RUNTIME.loadWebFile(world, path.join(web, file), {filename: file});
  }
  const displayFixture = JSON.parse(fs.readFileSync(path.join(__dirname,'fixtures/gui_display.json'),'utf8'));
  world.DS.configure(displayFixture.display); world.DS.catalogs.set('release', displayFixture.catalog);
  const {RT, RS} = world;
  RT.state.enums = {elements: ['physical'], damage_systems: ['classic'],
    scaling_stats: ['atk', 'hp', 'def', 'em'], buff_zones: ['dmg_bonus'],
    buff_scopes: ['team', 'frontline'], reaction_types: {classic: ['amplify']}};
  if (!renderPanels) {
    RT.renderTeamPanel = RT.renderPropPanel = () => {};
    RT.renderTimeline = () => { world.redraws = (world.redraws || 0) + 1; };
  }
  if (!renderResults) RS.renderSummary = RS.renderHits = RS.renderCoverage = () => new Element();
  RT.replaceTemplate(template(), 'A.json');
  return {world, nodes, RT, RS};
}

test('actual editor renders omitted optional lists and supports adding and saving', async () => {
  for (const omitted of [['buffs'], ['switches'], ['buffs', 'switches']]) {
    const {world, nodes, RT} = setup({renderPanels: true});
    const input = template();
    omitted.forEach((key) => delete input[key]);
    world.pywebview.api.load_template = async () => ({code: 0, data: {template: input, path: 'optional.json'}});
    world.pywebview.api.list_plans = async () => ({code: 0, data: {dir: '.', plans: []}});
    let saved;
    world.pywebview.api.save_template = async (path, value) => {
      saved = JSON.parse(JSON.stringify(value));
      return {code: 0, data: {path}};
    };
    await RT.loadTemplate('optional.json');
    assert.match(nodes.get('#status-bar').textContent, /已加载.*增益 0 条/);
    assert.ok(descendants(nodes.get('#team-panel')).some((node) => node.attributes['aria-label'] === '暴击率'));
    await RT.saveTemplate();
    omitted.forEach((key) => assert.equal(key in saved, false));
    RT.addBuff(); RT.addBuff(); RT.addSwitch();
    assert.equal(RT.tpl().buffs.length, 2);
    assert.equal(new Set(RT.tpl().buffs.map((buff) => buff.id)).size, 2);
    await RT.saveTemplate();
    assert.equal(saved.buffs.length, 2);
    assert.equal(saved.switches.length, 1);
  }
});

test('actual character panel displays zero for omitted crit fields without inventing stats', async () => {
  const {world, nodes, RT, RS} = setup({renderPanels: true});
  world.pywebview.api.load_template = async () => ({code: 0, data: {template: template(), path: 'no-crit.json'}});
  await RT.loadTemplate('no-crit.json');
  for (const label of ['暴击率', '暴击伤害']) {
    const control = descendants(nodes.get('#team-panel')).find((node) => node.attributes['aria-label'] === label);
    assert.equal(Number(control.value), 0);
  }
  assert.equal('crit_rate' in RT.tpl().characters[0].stats, false);
  assert.equal('crit_dmg' in RT.tpl().characters[0].stats, false);
  await RS.runCalc();
  assert.equal(RS.isCurrent(), true);
  RT.select('hit', 0);
  assert.equal(RS.isCurrent(), true);
});

test('actual new-template panel preserves explicit starting crit stats', async () => {
  const {world, nodes, RT} = setup({renderPanels: true});
  const input = template();
  Object.assign(input.characters[0].stats, {crit_rate: 0.05, crit_dmg: 0.5});
  world.pywebview.api.new_template = async () => ({code: 0, data: {template: input}});
  await RT.newTemplate();
  for (const [label, value] of [['暴击率', 0.05], ['暴击伤害', 0.5]]) {
    const control = descendants(nodes.get('#team-panel')).find((node) => node.attributes['aria-label'] === label);
    assert.equal(Number(control.value), value);
  }
  assert.equal(RT.tpl().characters[0].stats.crit_rate, 0.05);
  assert.equal(RT.tpl().characters[0].stats.crit_dmg, 0.5);
});

test('edited values invalidate displayed results and block every export path', async () => {
  const {world, nodes, RT, RS} = setup();
  let exports = 0;
  world.pywebview.api.export_result = async () => { exports++; return {code: 0, data: {path: 'x'}}; };
  await RS.runCalc();
  assert.equal(RS.isCurrent(), true);
  RT.tpl().characters[0].stats.atk = 4000;
  RT.renderAll();
  assert.equal(RS.isCurrent(), false);
  assert.match(nodes.get('#result-body').textContent, /结果已失效/);
  for (const fmt of ['json', 'xlsx', 'csv']) {
    assert.equal(nodes.get(`#btn-exp-${fmt}`).disabled, true);
    await RS.exportResult(fmt);
  }
  assert.equal(exports, 0);
  assert.equal(RS.state.result.gui_input.template.characters[0].stats.atk, 2000);
});

test('hidden result page defers table rendering and reuses the unchanged snapshot', async () => {
  const {nodes, RT, RS}=setup();
  let visible=false, renders=0;
  const tab=nodes.get('#tab-result'); tab.classList.contains=()=>visible;
  RS.renderHits=()=>{renders++;return new Element();};
  await RS.runCalc(); assert.equal(renders,0);
  const openDamage=()=>{const section=nodes.get('#result-body').children.find(node=>node.className?.includes('result-section-damage'));
    section.open=true;section.emit('toggle');};
  visible=true; RS.renderAll(); assert.equal(renders,0);
  openDamage();assert.equal(renders,1);
  RS.renderAll(); assert.equal(renders,1);
  RT.tpl().characters[0].stats.atk=3000; RT.renderAll();
  assert.equal(renders,1); assert.match(nodes.get('#result-body').textContent,/结果已失效/);
  openDamage();assert.equal(renders,2);
});

test('calculation report leads with trust and summary, then progressively discloses detail groups', async () => {
  const {world,nodes, RS}=setup({renderResults:true});
  world.pywebview.api.run_calc=async()=>({code:0,data:{result:{summary:{total_damage:100,dps:5,by_character:{char1:100},by_skill:{'char1.skill':100}},hits:[],buffs_coverage:[],assumptions:[]}}});
  await RS.runCalc();
  const body=nodes.get('#result-body'),classes=body.children.map(node=>node.className||'');
  assert.match(classes[0],/result-trust/);
  assert.match(classes[1],/result-block/);
  assert.equal(body.children[0].textContent.includes('当前结果'),true);
  assert.deepEqual(body.children.filter(node=>(node.className||'').includes('result-section')).map(node=>node.children[0].textContent),
    ['伤害明细（0）','效果与覆盖','循环与机制','诊断与来源']);
  const diagnostics=body.children.find(node=>(node.className||'').includes('result-section-diagnostics'));
  assert.doesNotMatch(diagnostics.textContent,/原始数据 · 计算输入/);
  diagnostics.open=true;diagnostics.emit('toggle');
  assert.match(diagnostics.textContent,/原始数据 · 计算输入/);
  assert.ok(body.children.indexOf(diagnostics)>body.children.findIndex(node=>(node.className||'').includes('result-section-damage')));
});

test('new and loaded identical templates still invalidate the previous result', async () => {
  const {world, RT, RS} = setup();
  world.pywebview.api.new_template = async () => ({code: 0, data: {template: template()}});
  world.pywebview.api.load_template = async () => ({code: 0, data: {template: template(), path: 'A.json'}});
  await RS.runCalc();
  await RT.newTemplate();
  assert.equal(RS.isCurrent(), false);
  await RS.runCalc();
  await RT.loadTemplate('A.json');
  assert.equal(RS.isCurrent(), false);
});

test('calculation takes a copy and discards a response after input edits', async () => {
  const {world, RT, RS} = setup();
  let resolve, captured;
  world.pywebview.api.run_calc = (input) => {
    captured = input;
    return new Promise((done) => { resolve = done; });
  };
  const pending = RS.runCalc();
  RT.tpl().characters[0].stats.atk = 4000;
  RT.renderAll();
  assert.equal(captured.characters[0].stats.atk, 2000);
  resolve(result(100));
  await pending;
  assert.equal(RS.state.result, null);
  assert.equal(RS.state.pending, false);
});

test('out of order responses cannot overwrite the later current result', async () => {
  const {world, RT, RS} = setup();
  const pending = [];
  world.pywebview.api.run_calc = () => new Promise((done) => pending.push(done));
  const first = RS.runCalc();
  RT.tpl().meta.name = '方案 B';
  RT.renderAll();
  const second = RS.runCalc();
  pending[1](result(200));
  await second;
  pending[0](result(100));
  await first;
  assert.equal(RS.state.result.summary.total_damage, 200);
  assert.equal(RS.state.snapshot.name, '方案 B');
  assert.equal(RS.isCurrent(), true);
});

test('selection preserves validity and export receives the correct input provenance', async () => {
  const {world, RT, RS} = setup();
  await RS.runCalc();
  RT.select('hit', 0);
  assert.equal(RS.isCurrent(), true);
  world.WB={pickUserFile:async()=>({path:'chosen.json'})};
  let sent;
  world.pywebview.api.export_result = async (...args) => {
    sent = args; return {code: 0, data: {path: 'A.json'}};
  };
  await RS.exportResult('json');
  assert.equal(sent[2], '方案 A');
  assert.equal(sent[3], 'chosen.json');
  assert.equal(sent[0].gui_input.path, 'A.json');
  assert.equal(sent[0].gui_input.template.characters[0].stats.atk, 2000);
});

test('delete then add does not reuse an existing Buff ID', () => {
  const {RT} = setup();
  RT.addBuff(); RT.addBuff();
  RT.tpl().buffs.splice(0, 1);
  RT.addBuff();
  const ids = RT.tpl().buffs.map((buff) => buff.id);
  assert.equal(new Set(ids).size, ids.length);
});

test('drag selects without rebuilding its element and commits through a stable target', () => {
  const {world, nodes, RT} = setup();
  const el = new Element(), visual = new Element();
  RT.pxPerSec = 10;
  let commits = 0;
  RT.enableDrag(el, 1, (delta) => { commits++; RT.tpl().hits[0].t += delta; },
    visual, null, {kind: 'hit', index: 0});
  const before = world.redraws;
  el.emit('pointerdown', {clientX: 10});
  assert.equal(world.redraws, before);
  assert.equal(nodes.get('#timeline').captured, 1);
  world.emit('pointermove', {clientX: 30});
  assert.equal(visual.attributes.transform, 'translate(20,0)');
  world.emit('pointerup', {clientX: 30});
  world.emit('pointerup', {clientX: 40});
  assert.equal(commits, 1);
  assert.equal(RT.tpl().hits[0].t, 3);
  assert.equal(RT.cancelDrag, null);
});

test('cancel and lost capture remove preview without committing', () => {
  for (const event of ['pointercancel', 'lostpointercapture']) {
    const {world, nodes, RT} = setup();
    const el = new Element(), visual = new Element();
    RT.pxPerSec = 10;
    let commits = 0;
    RT.enableDrag(el, 1, () => commits++, visual, null, {kind: 'hit', index: 0});
    el.emit('pointerdown');
    world.emit('pointermove', {clientX: 20});
    (event === 'pointercancel' ? world : nodes.get('#timeline')).emit(event);
    world.emit('pointerup', {clientX: 20});
    assert.equal(commits, 0);
    assert.equal(visual.attributes.transform, undefined);
    assert.equal(RT.cancelDrag, null);
  }
});

test('72-second dense timeline zoom anchors the pointer, pins labels and does not invalidate calculations', async () => {
  const {world, nodes, RT, RS} = setup({renderPanels: true});
  const input = template();
  input.loop_duration = 72;
  input.hits = Array.from({length: 252}, (_, i) => ({t: i * 0.28, character: 'char1', skill: 'skill'}));
  input.buffs = Array.from({length: 252}, (_, i) => ({id: `b${i}`, name: '增益', start: i * 0.28,
    end: i * 0.28 + 0.01, scope: 'team', effects: []}));
  RT.replaceTemplate(input, 'dense.json');
  let calculations = 0;
  world.pywebview.api.run_calc = async () => { calculations++; return result(); };
  await RS.runCalc();
  const before = JSON.stringify(RT.tpl()), revision = RT.state.revision;
  const wrap = nodes.get('#timeline-wrap'), svg = nodes.get('#timeline');
  assert.equal(svg.attributes.width, wrap.clientWidth);
  assert.equal(world.TV.projection.views.damage.events.filter(event=>event.kind==='damage').length,252);
  assert.equal(world.TV.projection.views.effects.events.filter(event=>event.kind==='coverage').length,252);
  assert.ok(descendants(svg).filter(n=>n.attributes.class?.includes('tl-view-event')).length<=252,'dense viewport renders a bounded subset of the complete read model');
  const anchor = 600, seconds = (anchor - 210) / RT.pxPerSec;
  const wheel = wrap.emit('wheel', {ctrlKey: true, deltaY: -100, clientX: anchor});
  assert.equal(wheel.defaultPrevented, true);
  assert.ok(Math.abs((wrap.scrollLeft + anchor - 210) / RT.pxPerSec - seconds) < 1e-9);
  assert.equal(world.TV.zoom, 1.2);
  assert.ok(world.TV.tickStep(RT.pxPerSec) * RT.pxPerSec >= 64);
  wrap.scrollTop = 450; wrap.emit('scroll');
  assert.equal(world.TV.labels.attributes.transform, `translate(${wrap.scrollLeft},0)`);
  assert.equal(world.TV.ruler.attributes.transform, 'translate(0,450)');
  assert.equal(world.TV.corner.attributes.transform, `translate(${wrap.scrollLeft},450)`);
  RT.renderAll();
  assert.equal(world.TV.zoom, 1.2);
  assert.equal(wrap.scrollTop, 450);
  assert.equal(wrap.listeners.get('wheel').size, 1);
  assert.equal(nodes.get('#timeline-zoom-in').listeners.get('click').size, 1);
  nodes.get('#timeline-fit').emit('click');
  assert.equal(world.TV.zoom, 1);
  assert.equal(wrap.scrollLeft, 0); assert.equal(wrap.scrollTop, 0);
  assert.equal(svg.attributes.width, wrap.clientWidth);
  assert.equal(JSON.stringify(RT.tpl()), before);
  assert.equal(RT.state.revision, revision);
  assert.equal(calculations, 1);
  assert.equal(RS.isCurrent(), true);
});

test('background/middle-button/pan-mode navigation preserves event data and ordinary selection', async () => {
  const {world, nodes, RT, RS} = setup({renderPanels: true});
  RT.tpl().hits[0].t=12;RT.renderAll(); // keep the selectable marker in the virtualized viewport
  await RS.runCalc();
  const before = JSON.stringify(RT.tpl()), revision = RT.state.revision;
  const wrap = nodes.get('#timeline-wrap');
  nodes.get('#timeline-zoom-in').emit('click');
  wrap.scrollLeft = 300; wrap.scrollTop = 400;
  assert.equal(wrap.emit('pointerdown').stopped, undefined); // 原生滚动条交还浏览器。
  wrap.emit('pointerdown', {target: nodes.get('#timeline'), clientX: 200, clientY: 200});
  world.emit('pointermove', {clientX: 120, clientY: 170});
  assert.equal(wrap.scrollLeft, 380); assert.equal(wrap.scrollTop, 430);
  world.emit('pointerup', {clientX: 120, clientY: 170});
  assert.equal(world.TV.cancelPan, null);
  // The partial DOM does not clamp overscroll like a browser; bring the only
  // source row back into view before interacting with its virtualized marker.
  wrap.scrollTop=0;world.TV.paintProjection();
  const hit = descendants(nodes.get('#timeline')).find(n => n.attributes.class?.includes('tl-damage'));
  assert.equal(wrap.emit('pointerdown', {target: hit}).stopped, undefined);
  hit.emit('pointerdown', {clientX: 500});
  world.emit('pointerup', {clientX: 500});
  assert.equal(RT.sel().kind, 'hit');
  assert.equal(RS.isCurrent(), true);
  const selection = JSON.stringify(RT.sel());
  assert.equal(wrap.emit('pointerdown', {button: 1, target: hit}).stopped, true);
  world.emit('pointercancel');
  nodes.get('#timeline-pan').emit('click');
  assert.equal(nodes.get('#timeline-pan').attributes['aria-pressed'], 'true');
  assert.equal(wrap.emit('pointerdown', {target: hit}).stopped, true);
  wrap.emit('lostpointercapture');
  assert.equal(world.TV.cancelPan, null);
  assert.equal(JSON.stringify(RT.sel()), selection);
  wrap.emit('wheel', {shiftKey: true, deltaY: 3, deltaMode: 1});
  assert.equal(wrap.scrollLeft, 452);
  assert.equal(wrap.emit('wheel', {deltaY: 100}).defaultPrevented, undefined);
  assert.equal(JSON.stringify(RT.tpl()), before);
  assert.equal(RT.state.revision, revision);
  assert.equal(RS.isCurrent(), true);
});

test('unified pointer controller accounts for horizontal scrolling, commits once and cancels on viewport changes', () => {
  const {world, nodes, RT} = setup({renderPanels: true});
  RT.addBuff();
  nodes.get('#timeline-zoom-in').emit('click');
  const wrap = nodes.get('#timeline-wrap'), svg = nodes.get('#timeline');
  wrap.scrollLeft=0;world.TV.paintProjection();
  const pps = RT.pxPerSec, revision = RT.state.revision;
  const hit = descendants(svg).find(n => n.attributes.class?.includes('tl-damage'));
  assert.ok(hit,'the selected manual marker must be in the visible viewport');
  hit.emit('pointerdown', {clientX: 400});
  wrap.scrollLeft += pps;
  world.emit('pointermove', {clientX: 400 + 2 * pps});
  world.emit('pointerup', {clientX: 400 + 2 * pps});
  assert.equal(RT.tpl().hits[0].t, 4);
  assert.equal(RT.state.revision, revision + 1);
  world.emit('pointerup', {clientX: 500});
  assert.equal(RT.state.revision, revision + 1);
  // Buff-edge handles were exclusive to the removed non-production renderer.
  // Exercise the retained generic pointer contract against an explicit field.
  const edge=new Element();RT.enableDrag(edge,RT.tpl().buffs[0].end,delta=>{RT.tpl().buffs[0].end=RT.round2(RT.tpl().buffs[0].end+delta);},edge,null,{kind:'buff',index:0});
  edge.emit('pointerdown', {clientX: 500});
  world.emit('pointerup', {clientX: 500 + pps});
  assert.equal(RT.tpl().buffs[0].end, 6);
  const current = descendants(svg).find(n => n.attributes.class?.includes('tl-damage'));
  current.emit('pointerdown', {clientX: 400});
  world.emit('pointermove', {clientX: 500});
  nodes.get('#timeline-zoom-in').emit('click');
  world.emit('pointerup', {clientX: 500});
  assert.equal(RT.tpl().hits[0].t, 4);
  assert.equal(RT.cancelDrag, null);
});

test('view bounds, resize, keyboard navigation and replacement do not leak view state into a template', () => {
  const {world, nodes, RT} = setup({renderPanels: true});
  const wrap = nodes.get('#timeline-wrap');
  world.TV.zoomAt(1000);
  assert.equal(world.TV.zoom, 32);
  assert.equal(nodes.get('#timeline-zoom-in').disabled, true);
  world.TV.zoomAt(0.0001);
  assert.equal(world.TV.zoom, 1);
  assert.equal(nodes.get('#timeline-zoom-out').disabled, true);
  wrap.clientWidth = 750; RT.renderTimeline();
  assert.equal(nodes.get('#timeline').attributes.width, 750);
  wrap.emit('keydown', {key: '+'});
  assert.equal(world.TV.zoom, 1.5);
  wrap.emit('keydown', {key: 'ArrowDown'});
  assert.equal(wrap.scrollTop, 48);
  RT.replaceTemplate(template('方案 B'), 'B.json');
  assert.equal(world.TV.zoom, 1);
  assert.equal(wrap.scrollLeft, 0); assert.equal(wrap.scrollTop, 0);
  assert.equal(Object.keys(RT.tpl()).some(k => /zoom|scroll|viewport/.test(k)), false);
});

test('expected bridge rejection is shown and does not leave calculation pending', async () => {
  const {world, nodes, RS} = setup();
  world.pywebview.api.run_calc = async () => { throw new Error('bridge disconnected'); };
  await RS.runCalc();
  assert.equal(RS.state.pending, false);
  assert.match(nodes.get('#status-bar').textContent, /bridge disconnected/);
});

test('pair discovery without an installer stays read-only and a late result cannot enable update', async () => {
  const {world, nodes} = setup();
  const {DV} = world;
  let done, refreshes = 0, updateCalls = 0;
  DV.loadOverview = async () => refreshes++;
  world.pywebview.api.check_database_update = () => new Promise((resolve) => { done = resolve; });
  world.pywebview.api.update_database = async () => { updateCalls++; return {code: 0, data: {updated: true}}; };
  const checking = DV.checkDatabaseUpdate();
  done({code: 0, data: {available: true, initialized: true, authoritative: true,
    coordinator_available: false, update_action_available: false,
    pair_policy: 'current_only', current_app_version: '0.45.0',
    current_app_release_id: 'app-0.45.0', current_release_pair_id: 'pair-current',
    current_database_release_id: 'db-current', actual_database_game_version: '7.1.51',
    actual_database_upstream_commit: 'a'.repeat(40), pair_status: 'current_dev_ready',
    remote_release_pair_id: 'pair-next', remote_database_release_id: 'db-next',
    remote_game_version: '7.1.53',
    message: '发现已验证的应用与数据库联合更新；当前版本尚未接入联合安装协调器'}});
  await checking;
  await DV.updateDatabase();
  assert.equal(refreshes, 0);
  assert.equal(updateCalls, 0);
  assert.match(nodes.get('#db-update-status').textContent, /尚未接入联合安装协调器/);
  assert.match(nodes.get('#db-version-details').textContent, /开发版当前组合/);
  assert.match(nodes.get('#db-version-details').textContent, /7\.1\.51/);
  assert.match(nodes.get('#db-version-details').textContent, /pair-next/);
  assert.equal(nodes.get('#data-update').disabled, true);
});

test('database update failures keep the button usable and show the reason', async () => {
  const {world, nodes} = setup();
  world.confirm=()=>true;
  world.DV.state.databaseUpdate = {update_action_available: true,confirmation_digest:'confirmed-pair'};
  world.pywebview.api.update_database = async () => { throw new Error('network unavailable'); };
  await world.DV.updateDatabase();
  assert.equal(nodes.get('#data-update').disabled, false);
  assert.match(nodes.get('#db-update-status').textContent, /network unavailable/);
});

test('already-current pair preserves accepted plan and editing context without mutating the live database', async () => {
  const {world, nodes} = setup();
  const {DV, DS} = world;
  let invalidated = 0, refreshes = 0;
  const equipmentDetail = {id: 'unchanged'}, dataDetail = {id: 'selected'};
  const catalog = DS.catalogs.get('release');
  world.PL = {state: {plan: null}, sourceChanged: () => invalidated++,
    currentInputJson: () => JSON.stringify(world.RT.tpl()), canCalculate: () => true, onEdit: () => {}};
  world.ED = {detailRequest: 3, request: 7, detail: equipmentDetail, dataVersion: 'same-source'};
  DV.state.detail = dataDetail;
  DV.state.databaseUpdate = {update_action_available: true,confirmation_digest:'confirmed-pair'};
  DS.catalogs.set('release', catalog);
  DV.loadOverview = async () => refreshes++;
  world.confirm=()=>true;
  world.pywebview.api.check_database_update = async () => ({code:0,data:{initialized:true,available:false,update_action_available:false,message:'当前版本没有可用更新'}});
  world.pywebview.api.update_database = async () => ({code: 0, data: {updated: false, action: 'already_current'}});
  await DV.updateDatabase();
  assert.equal(invalidated, 0);
  assert.equal(world.ED.detail, equipmentDetail);
  assert.equal(world.ED.dataVersion, 'same-source');
  assert.equal(world.ED.detailRequest, 3); assert.equal(world.ED.request, 7);
  assert.equal(DV.state.detail, dataDetail);
  assert.equal(DS.catalogs.get('release'), catalog);
  assert.equal(refreshes, 0);
  assert.equal(nodes.get('#db-update-status').textContent, '当前版本没有可用更新');
  assert.equal(nodes.get('#data-update').disabled, true);
});

test('an unknown updater action fails closed without invalidating in-memory plan sources', async () => {
  const {world, nodes} = setup();
  let invalidated = 0;
  world.PL = {state: {plan: null}, sourceChanged: () => invalidated++,
    currentInputJson: () => JSON.stringify(world.RT.tpl()), canCalculate: () => true, onEdit: () => {}};
  world.DV.loadOverview = async () => {};
  world.confirm=()=>true;
  world.DV.state.databaseUpdate = {update_action_available: true,confirmation_digest:'confirmed-pair'};
  world.pywebview.api.update_database = async () => ({code: 0, data: {updated: true, action: 'future-action'}});
  await world.DV.updateDatabase();
  assert.equal(invalidated, 0);
  assert.equal(nodes.get('#db-update-status').textContent, '更新尚未进入安装阶段。');
});


const visibleText = node => node.className === 'display-raw' ? ''
  : (node.text || '') + node.children.map(visibleText).join('');
const b04Template = () => JSON.parse(fs.readFileSync(path.join(__dirname,
  '../benchmarks/bm01/evidence/p3-results/deliveries/B04-r1-20260913/B04_resolved_template.json'),'utf8'));

test('B04 displays database Chinese names and source buff labels without changing the submitted model', async () => {
  const {world,RT,nodes} = setup({renderPanels:true});
  const input=b04Template(), before=JSON.stringify(input);
  RT.replaceTemplate(input,'B04.json');await Promise.resolve();
  for(const name of ['神里绫华','申鹤','枫原万叶','珊瑚宫心海'])assert.ok(visibleText(nodes.get('#timeline')).includes(name));
  world.TV.changeMode('effects');assert.match(visibleText(nodes.get('#timeline')),/翠绿之影减抗/);
  assert.doesNotMatch(visibleText(nodes.get('#timeline')),/ayaka|shenheq1|vv1|team|cryo/);
  RT.select('hit',input.hits.findIndex(h=>h.element==='cryo'));const options=descendants(nodes.get('#prop-panel')).filter(n=>n.tagName==='option');
  assert.ok(options.some(n=>n.value==='cryo'&&n.textContent==='冰元素'));
  assert.ok(options.some(n=>n.value==='classic'&&n.textContent==='传统伤害'));
  assert.equal(JSON.stringify(input),before);
  RT.select('buff',0);assert.match(visibleText(nodes.get('#prop-panel')),/增益名称|来源说明|敌人抗性降低/);
  assert.equal(JSON.stringify(input),before,'selection and labels cannot rewrite benchmark data');
  assert.equal(world.DS.buff('vv1'),input.buffs[0].source);
});

test('editing character and buff Chinese names preserves every stable reference', () => {
  const {RT,nodes}=setup({renderPanels:true});const input=b04Template();RT.replaceTemplate(input,'B04.json');
  const beforeHits=JSON.stringify(input.hits),beforeSwitches=JSON.stringify(input.switches);
  const char=descendants(nodes.get('#team-panel')).find(n=>n.attributes['aria-label']==='角色 1 中文名称');
  char.value='绫华测试名称';char.emit('change');assert.equal(input.characters[0].id,'ayaka');
  assert.equal(input.characters[0].name,'绫华测试名称');assert.equal(JSON.stringify(input.hits),beforeHits);
  assert.equal(JSON.stringify(input.switches),beforeSwitches);
  RT.select('buff',0);const buff=descendants(nodes.get('#prop-panel')).find(n=>n.attributes['aria-label']==='增益名称');
  buff.value='第一轮风套减抗';buff.emit('change');assert.equal(input.buffs[0].id,'vv1');
  assert.equal(input.buffs[0].name,'第一轮风套减抗');assert.equal(JSON.stringify(input.hits),beforeHits);
});

test('unknown text is explicitly untranslated and preserved in collapsed raw data',()=>{
  const {world,RT}=setup();const input=template();input.characters[0].id='customhero';
  RT.replaceTemplate(input,'unknown.json');
  assert.match(world.DS.character('customhero'),/角色 1（中文名待补）/);
  const block=world.DS.textBlock('uncertain custom mechanic');
  assert.match(visibleText(block),/中文说明待补/);assert.doesNotMatch(visibleText(block),/uncertain/);
  assert.ok(block.textContent.includes('uncertain custom mechanic'));
  assert.equal(world.DS.skill('made_up_skill'),'技能类别待补');
  assert.equal(world.DS.label('skills','__proto__'),'中文名称待补');
  input.buffs=[{id:'unknown_a',effects:[{zone:'dmg_bonus'}]},{id:'unknown_b',effects:[{zone:'dmg_bonus'}]}];
  assert.notEqual(world.DS.buff('unknown_a'),world.DS.buff('unknown_b'));
});

test('display identity respects exact database references, ambiguous aliases and version lines',()=>{
  const {world}=setup(),DS=world.DS,input=template();
  assert.equal(DS.normalize('Ｋａｍｉｓａｔｏ　Ａｙａｋａ'),'kamisatoayaka');
  assert.equal(DS.normalize("D'Artagnan"),"d'artagnan");
  assert.notEqual(DS.normalize("D'Artagnan"),DS.normalize('DArtagnan'));
  assert.equal(DS.normalize('AYAKA_- '),'ayaka');input.characters[0].id='ayaka';input.characters[0].name='自定义别称';
  const plan={manual_template:input,equipment_inputs:{ayaka:{character_ref:{db_character_id:10000063}}}};
  assert.equal(DS.character('ayaka',input,plan),'自定义别称');
  delete input.characters[0].name;assert.equal(DS.character('ayaka',input,plan),'申鹤');
  input.characters[0].name='自定义别称';
  plan.equipment_inputs.ayaka.character_ref.version_line='beta';
  assert.equal(DS.character('ayaka',input,plan),'自定义别称');
  delete input.characters[0].name;assert.match(DS.character('ayaka',input,plan),/资料版本线不符/);
  input.characters[0].name='自定义别称';
  plan.equipment_inputs.ayaka.character_ref={db_character_id:999999,version_line:'release'};
  delete input.characters[0].name;assert.match(DS.character('ayaka',input,plan),/数据库中文名待补/);
  input.characters[0].name='自定义别称';
  input.meta.version_line='beta';assert.equal(DS.character('ayaka',input,null),'自定义别称');
  delete input.characters[0].name;assert.match(DS.character('ayaka',input,null),/中文名待补/);
  input.meta.version_line='release';DS.catalogs.set('release',{characters:[
    {id:1,name:'甲',aliases:['ayaka']},{id:2,name:'乙',aliases:['ayaka']}]});
  assert.match(DS.character('ayaka',input,null),/中文名待补/);
});

test('late display catalog responses cannot replace a later refresh or leak to another line',async()=>{
  const {world}=setup(),DS=world.DS;let first,second;
  world.pywebview.api.get_display_catalog=()=>new Promise(r=>{if(!first)first=r;else second=r;});
  const one=DS.ensure('beta',true),two=DS.ensure('beta',true);
  second({code:0,data:{version_line:'beta',characters:[{id:1,name:'新资料'}]}});await two;
  first({code:0,data:{version_line:'beta',characters:[{id:1,name:'旧资料'}]}});await one;
  assert.equal(DS.catalogs.get('beta').characters[0].name,'新资料');
  assert.equal(DS.catalogs.get('release').characters[0].name,'神里绫华');
});

test('result rows use their calculation snapshot labels after a different team is loaded',()=>{
  const {RT,RS}=setup({renderResults:true});const input=b04Template();
  RS.state.snapshot={template:input,name:input.meta.name};RT.replaceTemplate(template('另一个队伍'),'other.json');
  const hits=[{t:1,character:'ayaka',skill:'E',hit_name:'绫华战技',damage_system:'classic',count:1,
    expected:100,crit:120,buffs:['vv1'],zones:{defense:0.5,resistance:1.225}}];
  const view=RS.renderHits(hits);assert.match(visibleText(view),/神里绫华|元素战技|传统伤害|防御乘区|抗性乘区/);
  assert.match(visibleText(view),/翠绿之影减抗/);assert.doesNotMatch(visibleText(view),/ayaka|classic|defense|vv1/);
  const descendants=node=>[node,...node.children.flatMap(descendants)];
  const zones=descendants(view).find(node=>node.tagName==='details');zones.open=true;zones.emit('toggle');
  assert.ok(view.textContent.includes('"character": "ayaka"'));
  const summary=RS.renderSummary({total_damage:100,dps:5,by_character:{ayaka:100},by_skill:{E:100}});
  assert.match(visibleText(summary),/神里绫华/);assert.match(visibleText(summary),/元素战技/);
});


test('successful handoff leaves catalogs untouched and closes only after the external updater accepts the pair',async()=>{
  const {world,RT}=setup(),DS=world.DS,DV=world.DV;
  RT.tpl().meta.version_line='release';DV.state.versionLine='beta';
  let oldFinish,closed=0;const calls=[],updateArgs=[];
  world.pywebview.api.get_display_catalog=line=>{calls.push(line);
    if(calls.length===1)return new Promise(r=>oldFinish=r);
    return Promise.resolve({code:0,data:{version_line:line,characters:[{id:1,name:'更新后资料'}]}});};
  const old=DS.ensure('release',true);
  world.confirm=()=>true;DV.state.databaseUpdate={update_action_available:true,confirmation_digest:'confirmed-pair'};
  world.pywebview.api.update_database=async(...args)=>{updateArgs.push(args);return {code:0,data:{updated:true,action:'handoff_launched'}};};
  world.pywebview.api.confirm_close=async()=>{closed++;return {code:0,data:{}};};DV.loadOverview=async()=>{};
  await DV.updateDatabase();assert.equal(calls.filter(x=>x==='release').length,1);assert.equal(calls.includes('beta'),false);
  assert.deepEqual(updateArgs,[['confirmed-pair',true]]);assert.equal(closed,1);
  oldFinish({code:0,data:{version_line:'release',characters:[{id:1,name:'更新前资料'}]}});await old;
  assert.equal(DS.catalogs.get('release').characters[0].name,'更新前资料');
  assert.equal(DS.catalogs.has('beta'),false);
});


test('successful load and save remain explicit Chinese while original paths stay available',async()=>{
  const {world,RT,nodes}=setup();const file='D:/project/english-folder/team.json';
  world.pywebview.api.load_template=async()=>({code:0,data:{template:template(),path:file}});
  world.pywebview.api.save_template=async()=>({code:0,data:{path:file}});
  world.pywebview.api.list_plans=async()=>({code:0,data:{dir:'D:/project',plans:[]}});
  await RT.loadTemplate(file);assert.match(visibleText(nodes.get('#status-bar')),/已加载模板/);
  assert.doesNotMatch(visibleText(nodes.get('#status-bar')),/未翻译|english/);assert.ok(nodes.get('#status-bar').textContent.includes(file));
  await RT.saveTemplate();assert.match(visibleText(nodes.get('#status-bar')),/模板已保存/);
  assert.doesNotMatch(visibleText(nodes.get('#status-bar')),/未翻译|english/);assert.ok(nodes.get('#status-bar').textContent.includes(file));
});


test('built-in rotation handbook assumption is Chinese without altering its original text',()=>{
  const {world}=setup();const original='帧率未纳入（rotation.md 附录精神，默认原生系统）';
  const block=world.DS.textBlock(original);
  assert.equal(visibleText(block),'帧率未纳入（排轴手册 附录精神，默认原生系统）');
  assert.ok(block.textContent.includes(original));
  assert.match(world.DS.prose('未知文件 unrelatedrotation.md'),/中文说明待补/);
});

test('free-text enum terms with conflicting meanings require context instead of last-map wins',()=>{
  const {world}=setup();const DS=world.DS;
  DS.configure({labels:{one:{base:'基础伤害',all:'全部元素'},two:{base:'基础面板',all:'全部反应'},three:{base:'基础面板'}}});
  for(const word of ['base','all']){
    const block=DS.textBlock(`读取 ${word}`);
    assert.match(visibleText(block),/中文说明待补/);
    assert.ok(block.textContent.includes(`读取 ${word}`));
  }
  assert.equal(DS.label('one','base'),'基础伤害');assert.equal(DS.label('two','base'),'基础面板');
});
