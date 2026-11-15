const GUI_TEST_RUNTIME = require('./helpers/gui_runtime.cjs');
// node tests/gui_data.test.cjs — real data.js renderers against the query-shaped preview fixture.
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

class Element {
  constructor(tag = 'div') {
    this.tagName = tag; this.children = []; this.attributes = {}; this.listeners = {};
    this.style = {}; this.classList = {add() {}, remove() {}};
  }
  appendChild(child) { this.children.push(child); return child; }
  append(...children) { this.children.push(...children); }
  set textContent(text) { this.children = []; this.text = String(text); }
  get textContent() { return (this.text || '') + this.children.map((child) => child.textContent).join(''); }
  set innerHTML(text) {
    assert.equal(text, '', 'data-bearing render paths must not assign raw HTML');
    this.children = []; this.text = '';
  }
  setAttribute(key, value) { this.attributes[key] = value; }
  addEventListener(name, callback) { this.listeners[name] = callback; }
  emit(name) { return this.listeners[name]?.({target: this}); }
}

const descendants = (node) => [node, ...node.children.flatMap(descendants)];
const byClass = (node, cls) => descendants(node).filter((child) => (child.className || '').split(' ').includes(cls));
const web = path.join(__dirname, '..', 'src', 'genshin_app', 'gui', 'web');

function setup() {
  const nodes = new Map();
  const world = {console, setInterval, clearInterval, addEventListener() {}};
  world.window = world;
  world.document = {
    createElement: (tag) => new Element(tag),
    querySelector: (selector) => {
      if (!nodes.has(selector)) nodes.set(selector, new Element());
      return nodes.get(selector);
    },
  };
  world.$ = world.document.querySelector;
  world.setStatus = (value) => { world.status = value; };
  world.callApi = async (method, ...args) => world.pywebview.api[method](...args);
  vm.createContext(world);
  for (const name of ['display.js', 'format.js', 'data.js', 'ui.js']) {
    GUI_TEST_RUNTIME.loadWebFile(world, path.join(web, name), {filename: name});
  }
  const displayFixture=JSON.parse(fs.readFileSync(path.join(__dirname,'fixtures/gui_display.json'),'utf8'));
  world.DS.configure(displayFixture.display);world.DS.catalogs.set('release',displayFixture.catalog);
  const preview = fs.readFileSync(path.join(web, 'dev_mock.html'), 'utf8');
  vm.runInContext(preview.match(/<script>([\s\S]*?)<\/script>/)[1], world, {filename: 'dev_mock.html'});
  return {world, nodes, DV: world.DV, DF: world.DF};
}

test('explicit numeric formats preserve units and use the documented integer rounding', () => {
  const {DF} = setup();
  for (const [template, value, expected] of [
    ['{param1}', 0.2, '0.2'], ['{param1:P}', 0.2, '20%'],
    ['{param1:F1P}', 0.235, '23.5%'], ['{param1:F2}', 5, '5.00'],
    ['{param1:F1}秒', 0.1, '0.1秒'], ['{param1:I}次', 2, '2次'],
    ['{param1:I}', 1270.2417, '1270'], ['{param1:I}', 2.5, '3'],
    ['{param1:I}', -2.5, '-3'], ['{param1:P}', -0.005, '-0.5%'],
    ['{param1:P}', 2.021399974822998, '202.14%'], ['{param1:P}', 1.0080000162124634, '100.8%'],
    ['{param1:P}', 0, '0%'], ['{param1:P}', -0.000001, '0%'],
    ['{param1:I}', 0, '0'], ['{param1:F0}', 5.4, '5'],
  ]) assert.equal(DF.format(template, [value]).text, expected);
  assert.equal(DF.format('治疗|{param1:F1P}最大生命值+{param2:I}', [0.12, 1270.2417]).text,
    '治疗|12.0%最大生命值+1270');
});

test('unsupported formats, missing slots and non-finite values remain explicit', () => {
  const {DF} = setup();
  for (const [template, values] of [
    ['{param1:Q}', [0.2]], ['{param2:P}', [0]], ['{param1:F1}', [Infinity]],
    ['{param1:P}', [Number.MAX_VALUE]], ['{param1}', [null]],
  ]) {
    const result = DF.format(template, values);
    assert.equal(result.text, template);
    assert.equal(result.unresolved.length, 1);
  }
});

test('description markup is rendered through safe nodes while unknown text is preserved', () => {
  const {DV} = setup();
  const text = '第一行\\n·第二行\n<i>斜体</i><b>重点</b><color=#AABBCC>元素</color>'
    + '{LINK#N123}词条{/LINK}<br>下一段<custom>未知</custom>{OTHER#x}'
    + '<img src=x onerror=alert(1)><script>alert(1)</script>';
  const rendered = DV.richText(text);
  assert.match(rendered.textContent, /第一行\n·第二行\n斜体重点元素词条\n下一段/);
  assert.ok(descendants(rendered).some((node) => node.tagName === 'em'));
  assert.ok(descendants(rendered).some((node) => node.tagName === 'strong'));
  assert.ok(descendants(rendered).some((node) => node.style.color === '#AABBCC'));
  assert.ok(!descendants(rendered).some((node) => ['script', 'img', 'a'].includes(node.tagName)));
  assert.match(rendered.textContent, /<custom>未知<\/custom>\{OTHER#x\}/);
  assert.match(rendered.textContent, /<script>alert\(1\)<\/script>/);
});

test('real detail renderer displays canonical descriptions and all skill groups from the preview contract', async () => {
  const {world, nodes, DV} = setup();
  const response = await world.pywebview.api.character_detail(10000002, 'release');
  response.data.skills[0].desc = 'wrong legacy field must not win';
  DV.state.detail = response.data;
  DV.state.annTarget = {target_type: 'character', target_id: 10000002, label: '神里绫华'};
  DV.renderDetail();
  const view = nodes.get('#data-detail');
  for (const heading of ['战斗技能（1）', '附属技能（1）', '固有天赋（1）', '命座（1）', '追加机制说明']) {
    assert.ok(view.textContent.includes(heading), heading);
  }
  const visibleDescriptions = byClass(view, 'data-description').map((node) => node.textContent).join('\n');
  assert.match(visibleDescriptions, /持续造成冰元素伤害/);
  assert.match(visibleDescriptions, /触发时有50%的概率/);
  assert.match(visibleDescriptions, /提升20%伤害/);
  assert.doesNotMatch(view.textContent, /wrong legacy field/);
  assert.ok(view.textContent.includes('202.0%'));
  assert.ok(view.textContent.includes('5.0秒'));
  assert.ok(view.textContent.includes('来源表'));
  const targets = descendants(view).filter((node) => node.tagName === 'option').map((node) => node.value);
  for (const value of ['skill:90011', 'skill_param:900', 'inherent_skill:901']) assert.ok(targets.includes(value));
});

test('level selection updates description variants, matching parameters and source together', () => {
  const {DV} = setup();
  const record = {name: '分等级天赋', desc_template: '通用',
    params: {'1': {param_list: [0.1]}, '2': {param_list: [0.25]}},
    description_variants: [
      {level: 1, desc_template: '初级{param1:P}', desc_status: 'present', desc_source: {record_id: 101}},
      {level: 2, desc_template: '进阶{param1:P}', desc_status: 'present', desc_source: {record_id: 102}},
    ]};
  const card = DV.renderRecord(record, record.name);
  assert.equal(byClass(card, 'data-description')[0].textContent, '初级10%');
  const select = descendants(card).find((node) => node.tagName === 'select');
  select.value = '2'; select.emit('change');
  assert.equal(byClass(card, 'data-description')[0].textContent, '进阶25%');
  assert.ok(byClass(card, 'data-source')[0].textContent.includes('102'));
  assert.ok(!byClass(card, 'data-source')[0].textContent.includes('101'));
});

test('selecting a character returns to its title while changing a skill level retains scroll position', async () => {
  const {DV, nodes, world} = setup();
  await DV.openCharacter(10000002);
  const view = nodes.get('#data-detail');
  view.scrollTop = 700;
  await DV.openCharacter(10000032);
  assert.equal(view.scrollTop, 0);
  assert.match(view.textContent, /班尼特/);
  view.scrollTop = 500;
  const level = descendants(view).find((node) => node.tagName === 'select'
    && node.attributes['aria-label']?.includes('技能等级'));
  level.value = '13'; level.emit('change');
  assert.equal(view.scrollTop, 500);
  world.pywebview.api.character_detail = async () => ({code: 1, message: 'not found'});
  await DV.openCharacter(999);
  assert.equal(view.scrollTop, 500, 'failed selections must retain the existing detail position');
});

test('weapon types show the five known Chinese names and retain unknown source values', async () => {
  const {world, DV, nodes} = setup();
  const {data} = await world.pywebview.api.character_detail(10000002, 'release');
  DV.state.detail = data;
  for (const [weapon, label] of [
    ['WEAPON_SWORD_ONE_HAND', '单手剑'], ['WEAPON_CLAYMORE', '双手剑'],
    ['WEAPON_POLE', '长柄武器'], ['WEAPON_BOW', '弓'], ['WEAPON_CATALYST', '法器'],
    ['UNKNOWN_WEAPON', '武器类型待补'], ['__proto__', '武器类型待补'],
  ]) {
    data.character.weapon_type = weapon;
    DV.renderDetail();
    assert.ok(nodes.get('#data-detail').children[0].textContent.includes(label));
    assert.ok(nodes.get('#data-detail').textContent.includes(weapon), 'original weapon identifier remains in collapsed data');
  }
});

test('missing descriptions report distinct causes and keep source hashes including zero', () => {
  const {DV} = setup();
  for (const [status, reason] of [
    ['source_empty', '上游未提供'], ['textmap_missing', '文本映射缺失'],
    ['legacy_unverified', '旧版数据尚未核验'],
  ]) {
    const card = DV.renderRecord({desc_template: '', desc_status: status,
      desc_source: {text_map_hash: '0', record_id: 15}}, '缺失记录');
    assert.match(byClass(card, 'data-notice')[0].textContent, new RegExp(reason));
    assert.ok(byClass(card, 'data-source')[0].textContent.includes('文本 Hash0'));
  }
});

test('legacy extras retain their complete content and new raw parameters retain every slot', () => {
  const {DV} = setup();
  const legacy = DV.renderExtra({info_type: 'sub_skill', desc_template: null,
    desc_status: 'legacy_unverified', content: '旧版子技能\n完整原文\nLv1: 0, 2, 0'});
  assert.equal(byClass(legacy, 'data-description')[0].textContent, '旧版子技能\n完整原文\n1 级: 0, 2, 0');
  assert.match(byClass(legacy, 'data-source')[0].textContent, /Lv1: 0, 2, 0/);
  const params = DV.renderParameters({param_list: [0, null, 0.2, 0],
    param_desc: ['未知|{param3:Q}']}, '10');
  const raw = byClass(params, 'data-raw-params')[0];
  assert.equal(raw.open, true);
  assert.match(params.textContent, /\{param3:Q\}/);
  const rows = descendants(raw).filter((node) => node.tagName === 'tr');
  assert.deepEqual(rows.map((row) => row.textContent), ['param10', 'param2缺失', 'param30.2', 'param40']);
});

test('real missing text never borrows metadata while complete extra records stay available', () => {
  const {DV} = setup();
  const content = '解锁条件：突破等级2\n参数组123\nLv1: 0, 0, 15';
  const missing = DV.renderExtra({info_type: 'inherent_skill', desc_template: null,
    desc_status: 'textmap_missing', content});
  assert.ok(byClass(missing, 'data-notice')[0].textContent.includes('文本映射缺失'));
  assert.equal(byClass(byClass(missing, 'data-record-body')[0], 'data-extra-full')[0].textContent,
    '完整补充记录' + content);
  const present = DV.renderExtra({info_type: 'inherent_skill', desc_template: '天赋正文',
    desc_status: 'present', content});
  assert.equal(byClass(present, 'data-description')[0].textContent, '天赋正文');
  assert.ok(byClass(present, 'data-extra-full')[0].textContent.includes('解锁条件：突破等级2'));
});

test('blank parameter descriptions create no empty rows and raw-only slots start collapsed', () => {
  const {DV} = setup();
  const values = Array(25).fill(0);
  values[14] = 15;
  const params = {param_list: values, param_desc: ['', ' ', '\n', '']};
  const before = JSON.stringify(params);
  const card = DV.renderParameters(params, '1');
  assert.equal(byClass(card, 'data-param-row').length, 0);
  assert.equal(card.open, false);
  const rows = descendants(card).filter((node) => node.tagName === 'tr');
  assert.equal(rows.length, 25);
  assert.equal(rows[14].textContent, 'param1515');
  assert.equal(rows[15].textContent, 'param160');
  assert.equal(JSON.stringify(params), before);
});

test('ascension values use known property units and retain unknown or fixed values', () => {
  const {DF} = setup();
  assert.equal(DF.ascension('暴击伤害', 0.3840000033378601), '38.4%');
  assert.equal(DF.ascension('攻击力%', 0.24), '24.0%');
  assert.equal(DF.ascension('元素精通', 115.2), '115.2');
  assert.equal(DF.ascension('元素精通', 0.2), '0.2');
  assert.equal(DF.ascension('未知属性', 0.2), '0.2');
});

test('captured real query responses render descriptions, healing and legacy supplements', () => {
  // Captured from the candidate database via queries; arrays retain original padding/indices.
  const cases = JSON.parse(fs.readFileSync(path.join(__dirname, 'fixtures', 'gui_description_queries.json'), 'utf8'));
  for (const [name, detail] of Object.entries(cases)) {
    const {DV, nodes, DF} = setup();
    DV.state.detail = detail;
    DV.state.annTarget = {target_type: 'character', target_id: detail.character.id, label: detail.character.name};
    DV.renderDetail();
    const view = nodes.get('#data-detail');
    const skill = detail.skills[0];
    const rendered = byClass(view, 'data-description').map((node) => node.textContent).join('\n');
    assert.ok(rendered.includes(skill.desc_template.split('\\n')[0].replace(/<[^>]+>/g, '')));
    assert.equal(view.textContent.includes('undefined'), false);
    const parameterText = byClass(view, 'data-param-value').map((node) => node.textContent).join('\n');
    if (name === 'ayaka') assert.match(parameterText, /202\.14%/);
    if (name === 'bennett') {
      assert.match(parameterText, /1270/);
      assert.match(parameterText, /100\.8%/);
      assert.doesNotMatch(parameterText, /\{param/);
    }
    for (const extra of detail.extra_info.filter((item) => item.desc_status === 'legacy_unverified')) {
      assert.ok(rendered.includes(DF.tokens(extra.content).filter((token) => token.type === 'text').map((token) => token.value).join('')));
    }
    assert.ok(view.textContent.includes(DF.ascension(detail.character.ascension_prop, detail.character.ascension_value)));
  }
});

test('preview fixtures use the public description contract rather than the old desc alias', () => {
  const {world} = setup();
  for (const record of [...world.__mockSkills, ...world.__mockTalents, ...world.__mockExtra]) {
    assert.equal(typeof record.desc_template, 'string');
    assert.equal(record.desc_status, 'present');
    assert.equal('desc' in record, false);
    assert.equal(typeof record.desc_source.text_map_hash, 'string');
    for (const key of ['table', 'record_id', 'language', 'upstream_commit']) assert.ok(key in record.desc_source);
  }
  for (const entry of ['index.html', 'dev_mock.html']) {
    const html = fs.readFileSync(path.join(web, entry), 'utf8');
    assert.ok(html.indexOf('src="format.js"') < html.indexOf('src="data.js"'));
  }
});

test('annotation row is selectable read-only content without mutation controls', () => {
  const {DV}=setup(),item=DV.renderAnnotationItem({id:7,status:'approved',title:'附着规则',category:'机制',content:'说明',target_type:'skill',target_id:9,version_line:'release',updated_at:'now'});
  assert.doesNotMatch(item.textContent,/编辑|删除/);
  const buttons=descendants(item).filter(node=>node.tagName==='button');
  assert.equal(buttons.length,0);assert.match(item.textContent,/附着规则/);assert.match(item.textContent,/说明/);
});

test('data page keeps category and search primary while filters and maintenance live in one popover', () => {
  const html=fs.readFileSync(path.join(web,'index.html'),'utf8'),toolbar=html.match(/<section id="tab-data"[\s\S]*?<div class="data-layout">/u)[0];
  const panel=toolbar.indexOf('class="data-tools-popover"');
  assert.ok(toolbar.indexOf('id="data-category"')<panel);
  assert.ok(toolbar.indexOf('id="data-q"')<panel);
  for(const id of ['data-element','data-rarity','data-version-line','data-update','data-annotations','db-version-panel'])assert.ok(toolbar.indexOf(`id="${id}"`)>panel,id);
  assert.match(toolbar,/id="data-filter-summary"/);
});

test('pair update confirms the exact digest and exits only after external handoff', async () => {
  const {world,DV,nodes}=setup(),calls=[];
  world.confirm=message=>{assert.match(message,/同时换版并自动重启/);return true;};
  world.pywebview.api.update_database=async(...args)=>{calls.push(['update',...args]);return {code:0,data:{action:'handoff_launched',requires_exit:true}};};
  world.pywebview.api.confirm_close=async()=>{calls.push(['close']);return {code:0,data:{closed:true}};};
  DV.state.databaseUpdate={update_action_available:true,confirmation_digest:'d'.repeat(64),remote_app_release_id:'app-next',remote_database_release_id:'db-next',remote_game_version:'7.2',artifacts:{app:{size_bytes:1024},database:{size_bytes:2048}}};
  await DV.updateDatabase();
  assert.deepEqual(calls,[['update','d'.repeat(64),true],['close']]);
  assert.match(nodes.get('#db-update-status').textContent,/更新器已接管/);
});

test('the update remains pending when the user declines to close the App', async () => {
  const {world, DV, nodes} = setup();
  world.confirm = () => true;
  world.pywebview.api.update_database = async () => ({code: 0, data: {action: 'handoff_launched'}});
  world.AgentWorkbenchApp = {ready: true, requestClose() {}, lifecycleController: {settle: async () => false}};
  DV.state.databaseUpdate = {update_action_available: true, confirmation_digest: 'd'.repeat(64)};
  await DV.updateDatabase();
  assert.match(nodes.get('#db-update-status').textContent, /保持打开，更新尚未安装/);
});

test('cancel during final install reports the update is still in progress', async () => {
  const {world, DV, nodes} = setup();
  world.pywebview.api.cancel_database_update = async () => ({code: 0, data: {cancel_requested: false}});
  DV.state.databaseUpdateRunning = true;
  await DV.updateDatabase();
  assert.match(nodes.get('#db-update-status').textContent, /正在完成安装和校验，此时无法取消/);
});
