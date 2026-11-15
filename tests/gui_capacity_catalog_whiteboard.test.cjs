'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const WEB = process.env.GENSHIN_CAPACITY_GUI_WEB
  ? path.resolve(process.env.GENSHIN_CAPACITY_GUI_WEB)
  : path.resolve(__dirname, '../src/genshin_app/gui/web');

class Element {
  constructor(tag = 'div') {
    this.tagName = tag;
    this.children = [];
    this.attributes = {};
    this.listeners = {};
    this.style = {setProperty() {}};
    this.value = '';
    this.checked = false;
    this.disabled = false;
    this.open = false;
    this.className = '';
    this.classList = {add() {}, remove() {}, toggle() {}};
  }
  append(...nodes) { this.children.push(...nodes); }
  appendChild(node) { this.append(node); return node; }
  set innerHTML(value) { this.children = []; this.text = String(value || ''); }
  set textContent(value) { this.children = []; this.text = String(value ?? ''); }
  get textContent() { return (this.text || '') + this.children.map(node => node.textContent).join(''); }
  setAttribute(key, value) { this.attributes[key] = String(value); }
  addEventListener(key, value) { this.listeners[key] = value; }
  removeEventListener() {}
  emit(key, detail = {}) { return this.listeners[key]?.({target: this, preventDefault() {}, ...detail}); }
  showModal() { this.open = true; }
  close() { this.open = false; }
  remove() {}
}

const all = node => [node, ...node.children.flatMap(all)];
const button = (node, label) => all(node).find(item => item.tagName === 'button' && item.textContent === label);
const field = (node, label) => all(node).find(item => item.attributes['aria-label'] === label);
const clone = value => JSON.parse(JSON.stringify(value));
const settle = async () => { await Promise.resolve(); await Promise.resolve(); };

function source(name) {
  return fs.readFileSync(path.join(WEB, name), 'utf8');
}

function environment({whiteboard = false} = {}) {
  const body = new Element('body');
  const root = new Element();
  root.id = 'tab-whiteboard';
  body.append(root);
  const status = [];
  const world = {
    console,
    Date,
    Math,
    JSON,
    setTimeout,
    clearTimeout,
    structuredClone,
    crypto: {randomUUID: () => '00000000-0000-4000-8000-000000000001'},
    setStatus: message => status.push(message),
    confirm: () => true,
    addEventListener() {},
  };
  world.window = world;
  world.globalThis = world;
  world.document = {
    body,
    createElement: tag => new Element(tag),
    createElementNS: (_ns, tag) => new Element(tag),
    querySelector: selector => selector === '#tab-whiteboard' ? root : null,
    querySelectorAll: () => [],
  };
  world.$ = selector => world.document.querySelector(selector);
  world.DS = {
    raw(label, value) {
      const details = new Element('details');
      details.append(Object.assign(new Element('summary'), {textContent: label}));
      const pre = new Element('pre');
      pre.textContent = JSON.stringify(value, null, 2);
      details.append(pre);
      return details;
    },
    textBlock(message) { const node = new Element(); node.textContent = message; return node; },
    character: value => String(value),
  };
  world.RC = {
    owner: value => String(value),
    abilityName: ability => ability.id,
    sourceEpoch: () => world.PL.state.sourceEpoch || 0,
    touch: () => { world.PL.state.sourceEpoch = (world.PL.state.sourceEpoch || 0) + 1; },
  };
  world.RT = {state: {path: 'plan.json'}};
  world.PL = {state: {plan: null, fileSha: 'sha', draft: null, sourceEpoch: 0}, currentInputJson() {
    return world.JSONTransport.stringify(this.state.plan);
  }};
  vm.createContext(world);
  for (const name of ['json-transport.js', 'gui-foundation-v2.js', 'ui.js', 'action-capacities.js'])
    vm.runInContext(source(name), world, {filename: name});
  if (whiteboard){vm.runInContext(source('business-ui.js'),world,{filename:'business-ui.js'});vm.runInContext(source('whiteboard.js'),world,{filename:'whiteboard.js'});}
  return {world, body, root, status};
}

const ordinaryContext = () => ({
  contract: 'static-action-capacity-context-v1',
  mode: 'ordinary_open_world',
  external_capacity_effects: 'none',
  window_changes: 'none',
});

function catalogItem(id, filter, {review = 'approved', selection = 'reference_selectable_for_fresh_preview'} = {}) {
  return {
    model_ref: {id, revision: 3},
    target: {character_id: 1001, skill_depot_id: 2001, skill_id: 3001, variant: 'tap'},
    domain: {context: ordinaryContext(), constellations: [0, 1], character_level: 90, ascension: 6,
      base_level: 10, weapon: {id: 4001, level: 90, refinement: 1}, artifacts: 'not_all_empty', team: 'not_self_only'},
    display: {character: {value: '同名角色', status: 'present'}, skill: {value: '同名战技', status: 'present'}, weapon: {value: '测试武器', status: 'present'}},
    current_receipt: {payload_fingerprint: `${id}-payload`},
    review: {status: review, reason: review === 'approved' ? null : `${review}_review`},
    selection_status: selection,
    declared_filter: {status: filter, matched_paths: [], mismatched: filter === 'mismatch' ? [{path: 'weapon.id', expected: 4001, actual: 9}] : [], unknown_input_paths: filter === 'partial_match' ? ['context'] : []},
    reviewed_constellations: [0, 1], material_status: 'not_evaluated', applicability: 'not_evaluated', execution_authority: false,
  };
}

test('document transport exposes both read-only catalog methods', () => {
  const {world} = environment();
  assert.equal(world.JSONTransport.usesMethod('action_capacity_model_candidates'), true);
  assert.equal(world.JSONTransport.usesMethod('whiteboard_action_capacity_model_candidates'), true);
});

test('catalog reads every all-view page, prioritizes selectable exact and partial groups, and keeps raw ids reachable', async () => {
  const {world} = environment();
  const items = [
    catalogItem('model.same.exact', 'exact_match'),
    catalogItem('model.same.partial', 'partial_match'),
    catalogItem('model.pending', 'exact_match', {review: 'pending', selection: 'review_not_approved'}),
    catalogItem('model.revoked', 'exact_match', {review: 'revoked', selection: 'review_not_approved'}),
    catalogItem('model.invalid', 'exact_match', {selection: 'model_contract_invalid'}),
    catalogItem('model.not-evaluated', 'not_evaluated', {selection: 'reference_not_selectable'}),
    catalogItem('model.mismatch', 'mismatch'),
  ];
  const calls = [];
  world.callApi = async (_method, _document, _actor, _members, cursor, pageSize, view) => {
    calls.push({cursor, pageSize, view});
    const offset = cursor ? 4 : 0;
    return {code: 0, data: {items: items.slice(offset, offset + 4), counts: {filtered_models: 7},
      has_more: offset === 0, next_cursor: offset === 0 ? 'page-2' : null}};
  };
  const holder = new Element();
  let selected = null;
  const browser = world.CAP.catalogBrowser(holder, {document: () => ({}), actor: () => 'a', memberIds: () => ['e'],
    method: 'action_capacity_model_candidates', current: () => true, onSelect: item => { selected = clone(item); }});
  await browser.reload();
  assert.deepEqual(calls.map(call => [call.cursor, call.pageSize, call.view]), [[null, 16, 'all'], ['page-2', 16, 'all']]);
  assert.match(holder.textContent, /已审核 · 完全匹配（1）/);
  assert.match(holder.textContent, /已审核 · 部分匹配（1）/);
  assert.match(holder.textContent, /待审核（1）|待审核或待处理（1）/);
  assert.match(holder.textContent, /已撤销（1）/);
  assert.match(holder.textContent, /合同无效（1）/);
  assert.match(holder.textContent, /尚未评估（1）/);
  assert.match(holder.textContent, /条件不匹配（1）/);
  for (const item of items) assert.match(holder.textContent, new RegExp(item.model_ref.id.replaceAll('.', '\\.')));
  const groups = all(holder).filter(node => node.className.includes('capacity-catalog-group'));
  assert.equal(groups.filter(node => node.open).length, 2);
  assert.equal(all(holder).filter(node => node.tagName === 'button' && node.textContent === '选择此模型').length, 2);
  await all(holder).find(node => node.tagName === 'button' && node.textContent === '选择此模型').emit('click');
  assert.equal(selected.model_ref.id, 'model.same.exact');
  assert.match(holder.textContent, /同名战技/);
  assert.match(holder.textContent, /高级详情与原始 ID/);
  const unknown = catalogItem('model.unknown', 'future_filter_status');
  assert.equal(world.CAP.catalogSelectable(unknown), false);
  assert.equal(world.CAP.catalogGroup(unknown), 'invalid');
});

test('catalog ignores a late page when its document guard changes', async () => {
  const {world} = environment();
  let finish;
  world.callApi = () => new Promise(resolve => { finish = resolve; });
  const holder = new Element();
  let current = true;
  const browser = world.CAP.catalogBrowser(holder, {document: () => ({}), actor: () => 'a', memberIds: () => ['e'],
    method: 'action_capacity_model_candidates', current: () => current, onSelect() {}});
  const pending = browser.reload();
  current = false;
  finish({code: 0, data: {items: [catalogItem('late.model', 'exact_match')], counts: {filtered_models: 1}, has_more: false, next_cursor: null}});
  await pending;
  assert.doesNotMatch(holder.textContent, /late\.model/);
});

test('catalog fails closed when any page contradicts the first page count snapshot', async () => {
  const {world} = environment();
  const holder = new Element();
  world.callApi = async (_method, _document, _actor, _members, cursor) => cursor
    ? {code: 0, data: {items: [catalogItem('second.item', 'exact_match')],
      counts: {current_models: 3, filtered_models: 3, returned: 1}, has_more: false, next_cursor: null}}
    : {code: 0, data: {items: [catalogItem('first.item', 'exact_match')],
      counts: {current_models: 3, filtered_models: 2, returned: 1}, has_more: true, next_cursor: 'page-2'}};
  const browser = world.CAP.catalogBrowser(holder, {document: () => ({}), actor: () => 'a', memberIds: () => ['e'],
    method: 'action_capacity_model_candidates', current: () => true, onSelect() { throw new Error('must not select'); }});
  assert.equal(await browser.reload(), false);
  assert.match(holder.textContent, /分页计数不一致|容量目录无法使用/);
  assert.equal(all(holder).filter(node => node.tagName === 'button' && node.textContent === '选择此模型').length, 0);
});

test('catalog validates every page count, cursor progress, and final accumulated total', async () => {
  const cases = [
    {name: 'missing filtered total', pages: [{items: [], counts: {}, has_more: false, next_cursor: null}], message: /计数元数据无效/},
    {name: 'returned count mismatch', pages: [{items: [catalogItem('one', 'exact_match')], counts: {filtered_models: 1, returned: 0}, has_more: false, next_cursor: null}], message: /计数元数据无效/},
    {name: 'repeated cursor', pages: [
      {items: [catalogItem('one', 'exact_match')], counts: {filtered_models: 3}, has_more: true, next_cursor: 'same'},
      {items: [catalogItem('two', 'exact_match')], counts: {filtered_models: 3}, has_more: true, next_cursor: 'same'},
    ], message: /分页游标无效/},
    {name: 'incomplete accumulated total', pages: [{items: [catalogItem('one', 'exact_match')], counts: {filtered_models: 2}, has_more: false, next_cursor: null}], message: /计数不一致/},
    {name: 'empty advancing page', pages: [{items: [], counts: {filtered_models: 1}, has_more: true, next_cursor: 'next'}], message: /分页游标无效/},
  ];
  for (const scenario of cases) {
    const {world} = environment(); const holder = new Element(); let page = 0;
    world.callApi = async () => ({code: 0, data: scenario.pages[Math.min(page++, scenario.pages.length - 1)]});
    const browser = world.CAP.catalogBrowser(holder, {document: () => ({}), actor: () => 'a', memberIds: () => ['e'],
      method: 'action_capacity_model_candidates', current: () => true, onSelect() { throw new Error('must not select'); }});
    assert.equal(await browser.reload(), false, scenario.name);
    assert.match(holder.textContent, scenario.message, scenario.name);
    assert.equal(all(holder).filter(node => node.tagName === 'button' && node.textContent === '选择此模型').length, 0, scenario.name);
  }
});

test('catalog selection writes only exact reference and target and clears old confirmation', () => {
  const {world} = environment();
  const row = {id: 'capacity', actor: 'a', ability_ids: ['e'], model_ref: {id: 'old', revision: 1},
    target: {character_id: 1, skill_depot_id: 2, skill_id: 3, variant: 'tap'}, accepted_fingerprint: 'a'.repeat(64)};
  const item = catalogItem('selected.model', 'exact_match');
  item.capacity = 99;
  item.domain.capacity = 88;
  world.CAP.applyCatalogSelection(row, item);
  assert.deepEqual(clone(row.model_ref), item.model_ref);
  assert.deepEqual(clone(row.target), item.target);
  assert.equal(row.accepted_fingerprint, null);
  assert.equal(Object.hasOwn(row, 'capacity'), false);
  assert.equal(Object.hasOwn(row.target, 'capacity'), false);
});

function whiteboard() {
  const intent = (id, action, context = null) => ({id, actor: 'a', action_ids: [action], version_line: 'release',
    model_ref: {id: `model.${id}`, revision: 1}, target: {character_id: 1001, skill_depot_id: 2001, skill_id: 3001, variant: 'tap'},
    cooldown: null, initial_ready_in: null, context});
  return {kind: 'genshin-whiteboard', schema_version: 2, board_id: '46c1047c-5f55-4b74-9ad8-6e0c149f97fd', revision: 0,
    title: '容量白板', version_line: 'release', duration: 20, notes: '',
    team: [{id: 'a', db_character_id: 1001, name: '甲', level: 90, constellation: 0, weapon: null, artifact_sets: [],
      panel: {atk: 2000, hp: null, def: null, em: null, crit_rate: null, crit_dmg: null, energy_recharge: null,
        base_atk: null, base_hp: null, base_def: null, dmg_bonus: {}} , notes: ''}],
    actions: [
      {id: 'e1', character: 'a', action_type_id: 'action.elemental_skill', label: '战技一', start: 0, variant: 'tap', notes: ''},
      {id: 'e2', character: 'a', action_type_id: 'action.elemental_skill', label: '战技二', start: 2, variant: 'tap', notes: ''},
      {id: 'e3', character: 'a', action_type_id: 'action.elemental_skill', label: '战技三', start: 4, variant: 'tap', notes: ''},
    ], capacity_intents: [intent('i1', 'e1'), intent('i2', 'e2')]};
}

test('native advanced editor mounts the all-view catalog with the full plan and keeps preview, confirm, apply separate', async () => {
  const {world, body} = environment();
  const oldFingerprint = '1'.repeat(64), fingerprint = 'a'.repeat(64), item = catalogItem('model.selected', 'exact_match');
  const plan = {plan_version: 3, revision: 4, equipment_inputs: {c1: {character_ref: {db_character_id: 1001, version_line: 'release'}}},
    rotation_model: {characters: [{id: 'c1'}], abilities: [{id: 'e', character: 'c1', action_type_id: 'action.elemental_skill', variant: 'tap',
      cooldown: {group: 'shared-e', duration: 10, start: 'action_start', recovery: 'sequential'}}], actions: [], events: []},
    action_capacity_context: ordinaryContext(), action_capacity_sources: [{id: 'capacity.old', actor: 'c1', ability_ids: ['e'], version_line: 'release',
      model_ref: {id: 'model.old', revision: 1}, target: {character_id: 1001, skill_depot_id: 2, skill_id: 3, variant: 'tap'}, accepted_fingerprint: oldFingerprint}]};
  world.PL.state.plan = plan;
  const calls = [], composed = [];
  world.callApi = async (method, ...args) => {
    calls.push({method, args: clone(args)});
    if (method === 'action_capacity_model_candidates') return {code: 0, data: {items: [item], counts: {filtered_models: 1}, has_more: false, next_cursor: null}};
    assert.equal(method, 'compose_equipment_plan');
    const proposed = args[0], row = proposed.action_capacity_sources[0]; composed.push(clone(proposed));
    return {code: 0, data: {state: row.accepted_fingerprint === fingerprint ? 'ready' : 'pending', action_capacity_compilations: [{id: row.id,
      status: row.accepted_fingerprint === fingerprint ? 'ready' : 'pending', projection: {capacity: 2, pool: {actor: 'c1', group: 'shared-e',
        members: [{cooldown: {duration: 10, start: 'action_start', recovery: 'sequential'}}], initial: {ready_in: []}}, profile: {model_record: {id: row.model_ref.id,
          revision: row.model_ref.revision, payload: {claim: {base: {value: 1}}}}}}, issues: [], required_confirmation: {accepted_fingerprint: fingerprint}}]}};
  };
  world.CAP.edit(0);
  const dialog = body.children.at(-1);
  await new Promise(resolve => setImmediate(resolve));
  const catalogCall = calls.find(call => call.method === 'action_capacity_model_candidates');
  assert.ok(catalogCall);
  assert.deepEqual(catalogCall.args.slice(1), ['c1', ['e'], null, 16, 'all']);
  assert.deepEqual(catalogCall.args[0], plan);
  assert.ok(button(dialog, '选择此模型'), dialog.textContent);
  await button(dialog, '选择此模型').emit('click');
  assert.equal(plan.action_capacity_sources[0].model_ref.id, 'model.old');
  assert.match(dialog.textContent, /model\.selected/);
  await button(dialog, '确认所示来源').emit('click');
  assert.equal(composed.length, 0);
  await button(dialog, '读取并预览').emit('click');
  assert.equal(composed[0].action_capacity_sources[0].model_ref.id, 'model.selected');
  assert.equal(composed[0].action_capacity_sources[0].accepted_fingerprint, null);
  assert.equal(plan.action_capacity_sources[0].accepted_fingerprint, oldFingerprint);
  await button(dialog, '确认所示来源').emit('click');
  assert.equal(plan.action_capacity_sources[0].accepted_fingerprint, oldFingerprint);
  await button(dialog, '应用次数来源').emit('click');
  assert.equal(plan.action_capacity_sources[0].model_ref.id, 'model.selected');
  assert.equal(plan.action_capacity_sources[0].accepted_fingerprint, fingerprint);
});

test('native capacity editor fails closed when the shared modal owner is busy', () => {
  const {world,body,status}=environment(),before=body.children.length;
  world.PL.state.plan={plan_version:3,equipment_inputs:{c1:{character_ref:{db_character_id:1001,version_line:'release'}}},
    rotation_model:{abilities:[{id:'e',character:'c1',action_type_id:'action.elemental_skill',variant:'tap',
      cooldown:{group:'e',duration:10,start:'action_start',recovery:'sequential'}}],initial_cooldowns:[]}};
  world.AppDialogKernel={create:()=>null};world.CAP.edit();
  assert.equal(body.children.length,before);assert.match(status.at(-1),/窗口正在处理/);
});

test('empty v2 whiteboard creates a shared-pool intent from skill actions, blocks duplicate ownership, and delete invalidates stale work', async () => {
  const {world, body} = environment({whiteboard: true});
  const board = whiteboard(); board.capacity_intents = [];
  world.WB.state.board = board; world.WB.state.selectedActor = 'a'; world.WB.state.result = {old: true}; world.WB.state.resultPath = 'old-calculated.json'; world.WB.render = () => {};
  const catalogCalls = [];
  world.callApi = async (method, document, actor, actionIds, cursor, pageSize, view) => {
    assert.equal(method, 'whiteboard_action_capacity_model_candidates'); catalogCalls.push({document: clone(document), actor, actionIds: clone(actionIds), cursor, pageSize, view});
    return {code: 0, data: {items: [catalogItem(`model.${actionIds.join('-')}`, 'exact_match')], counts: {filtered_models: 1}, has_more: false, next_cursor: null}};
  };
  const list = new Element(); world.WB.renderCapacityIntents(list);
  assert.match(list.textContent, /当前没有次数意图/);
  await button(list, '＋ 新建次数意图').emit('click');
  let dialog = body.children.at(-1);
  assert.ok(all(dialog).some(node => node.tagName === 'fieldset'));
  assert.ok(all(dialog).some(node => node.tagName === 'legend' && /共用此次数池/.test(node.textContent)));
  for (const id of ['e1', 'e2']) { const input = all(dialog).find(node => node.attributes['aria-label']?.includes(`动作 ID ${id}`)); input.checked = true; await input.emit('change'); }
  await Promise.resolve(); await Promise.resolve();
  const lastQuery = catalogCalls.at(-1);
  assert.deepEqual(lastQuery.actionIds, ['e1', 'e2']);
  assert.deepEqual(lastQuery.document.capacity_intents, []);
  assert.equal(lastQuery.actor, 'a'); assert.equal(lastQuery.view, 'all');
  await button(dialog, '一键使用普通开放世界预设').emit('click');
  await settle();
  await button(dialog, '选择此模型').emit('click');
  await button(dialog, '保存次数意图').emit('click');
  assert.deepEqual(clone(board.capacity_intents[0].action_ids), ['e1', 'e2']);
  assert.equal(board.capacity_intents[0].model_ref.id, 'model.e1-e2');
  assert.deepEqual(clone(board.capacity_intents[0].context), ordinaryContext());
  assert.equal(Object.hasOwn(board.capacity_intents[0], 'capacity'), false);
  world.WB.state.selectedAction = 'e1'; const inspector = new Element(); world.WB.renderInspector(inspector);
  for (const label of ['动作角色', '动作类型', '操作变体']) assert.equal(field(inspector, label).disabled, true);
  assert.match(inspector.textContent, /请先修改或删除该次数意图/);

  world.WB.editCapacityIntent(); dialog = body.children.at(-1);
  for (const id of ['e1', 'e2']) assert.equal(all(dialog).find(node => node.attributes['aria-label']?.includes(`动作 ID ${id}`)).disabled, true);
  const e3 = all(dialog).find(node => node.attributes['aria-label']?.includes('动作 ID e3')); e3.checked = true; await e3.emit('change');
  await Promise.resolve(); await Promise.resolve(); await button(dialog, '选择此模型').emit('click'); await button(dialog, '保存次数意图').emit('click');
  assert.equal(board.capacity_intents.length, 2); assert.deepEqual(clone(board.capacity_intents[1].action_ids), ['e3']);

  let finish;
  world.callApi = () => new Promise(resolve => { finish = resolve; });
  world.WB.editCapacityIntent(0); const staleDialog = body.children.at(-1);
  assert.equal(world.WB.removeCapacityIntent(0), true);
  finish({code: 0, data: {items: [catalogItem('late.deleted.intent', 'exact_match')], counts: {filtered_models: 1}, has_more: false, next_cursor: null}});
  await Promise.resolve(); await Promise.resolve();
  assert.equal(board.capacity_intents.length, 1); assert.equal(board.capacity_intents[0].action_ids[0], 'e3');
  assert.doesNotMatch(staleDialog.textContent, /late\.deleted\.intent/);
  assert.equal(world.WB.state.result, null); assert.equal(world.WB.state.staleResultPath, 'old-calculated.json');
});

test('whiteboard persists per-intent null scenes and only the explicitly edited intent receives or clears the preset', async () => {
  const {world, body} = environment({whiteboard: true});
  world.WB.state.board = whiteboard();
  world.WB.state.selectedActor = 'a';
  world.WB.render = () => {};
  world.callApi = async (_method, document) => ({code: 0, data: {items: [catalogItem(document.capacity_intents?.[0]?.context ? 'model.scene' : 'model.no-scene', 'exact_match')], counts: {filtered_models: 1}, has_more: false, next_cursor: null}});
  const box = new Element();
  world.WB.renderCapacityIntents(box);
  assert.match(box.textContent, /次数场景未确认/);
  assert.match(box.textContent, /2 个次数意图/);

  world.WB.editCapacityIntent(0);
  let dialog = body.children.at(-1);
  await button(dialog, '一键使用普通开放世界预设').emit('click');
  assert.match(dialog.textContent, /尚未从目录选择模型/);
  await settle();
  await button(dialog, '选择此模型').emit('click');
  await button(dialog, '保存次数意图').emit('click');
  assert.deepEqual(clone(world.WB.state.board.capacity_intents[0].context), ordinaryContext());
  assert.equal(world.WB.state.board.capacity_intents[1].context, null);
  assert.match(world.WB.json(), /"capacity_intents"/);

  world.WB.editCapacityIntent(0);
  dialog = body.children.at(-1);
  await button(dialog, '清除场景').emit('click');
  assert.match(dialog.textContent, /尚未从目录选择模型/);
  await settle();
  await button(dialog, '选择此模型').emit('click');
  await button(dialog, '保存次数意图').emit('click');
  assert.equal(world.WB.state.board.capacity_intents[0].context, null);
  assert.equal(world.WB.state.board.capacity_intents[1].context, null);
});

test('whiteboard context change clears a selected model and cannot save it under the new scene', async () => {
  const {world, body} = environment({whiteboard: true});
  const board = whiteboard(); board.capacity_intents = [];
  world.WB.state.board = board; world.WB.state.selectedActor = 'a'; world.WB.render = () => {};
  world.callApi = async (_method, document) => { const selected = document.capacity_intents?.[0];
    const items = selected?.context ? [] : [catalogItem('model.selected-under-null-context', 'exact_match')];
    return {code: 0, data: {items, counts: {filtered_models: items.length}, has_more: false, next_cursor: null}};
  };
  world.WB.editCapacityIntent(); const dialog = body.children.at(-1);
  const action = all(dialog).find(node => node.attributes['aria-label']?.includes('动作 ID e1'));
  action.checked = true; await action.emit('change'); await settle();
  await button(dialog, '选择此模型').emit('click');
  assert.match(dialog.textContent, /model\.selected-under-null-context/);
  await button(dialog, '一键使用普通开放世界预设').emit('click'); await settle();
  assert.match(dialog.textContent, /尚未从目录选择模型/);
  await button(dialog, '保存次数意图').emit('click');
  assert.equal(board.capacity_intents.length, 0);
  assert.match(dialog.textContent, /请先从目录选择/);
});

test('native context change also clears the old model identity and confirmation', async () => {
  const {world, body} = environment();
  const plan = {plan_version: 3, revision: 1,
    equipment_inputs: {c1: {character_ref: {db_character_id: 1001, version_line: 'release'}}},
    rotation_model: {characters: [{id: 'c1'}], abilities: [{id: 'e', character: 'c1', action_type_id: 'action.elemental_skill', variant: 'tap',
      cooldown: {group: 'e', duration: 10, start: 'action_start', recovery: 'sequential'}}], actions: [], events: []},
    action_capacity_sources: [{id: 'capacity.old', actor: 'c1', ability_ids: ['e'], version_line: 'release',
      model_ref: {id: 'model.old', revision: 1}, target: {character_id: 1001, skill_depot_id: 2001, skill_id: 3001, variant: 'tap'}, accepted_fingerprint: 'a'.repeat(64)}]};
  world.PL.state.plan = plan;
  world.callApi = async method => { assert.equal(method, 'action_capacity_model_candidates');
    return {code: 0, data: {items: [], counts: {filtered_models: 0}, has_more: false, next_cursor: null}};
  };
  world.CAP.edit(0); const dialog = body.children.at(-1); await settle();
  assert.match(dialog.textContent, /model\.old/);
  await button(dialog, '一键使用普通开放世界预设').emit('click'); await settle();
  assert.match(dialog.textContent, /尚未从目录选择模型/);
  await button(dialog, '读取并预览').emit('click');
  assert.match(dialog.textContent, /请先从容量目录选择/);
  assert.equal(plan.action_capacity_sources[0].model_ref.id, 'model.old');
  assert.equal(plan.action_capacity_sources[0].accepted_fingerprint, 'a'.repeat(64));
});

test('whiteboard detects conflicting nonnull scenes and maps backend context conflict to a Chinese blocking message', () => {
  const {world} = environment({whiteboard: true});
  const board = whiteboard();
  board.capacity_intents[0].context = ordinaryContext();
  board.capacity_intents[1].context = {...ordinaryContext(), mode: 'unsupported_other_scene'};
  assert.equal(world.WB.capacityContextConflict(board), true);
  board.capacity_intents[1].context = {window_changes: 'none', external_capacity_effects: 'none', mode: 'ordinary_open_world', contract: 'static-action-capacity-context-v1'};
  assert.equal(world.WB.capacityContextConflict(board), false);
  assert.match(world.CAP.catalogError({reason: 'capacity_context_conflict', message: 'conflict'}), /场景.*不一致|不一致.*场景/);
});

test('catalog and whiteboard controls retain selectable text and compact 960 by 720 layout rules', () => {
  const css = fs.readFileSync(path.join(WEB, 'whiteboard.css'), 'utf8');
  assert.match(css, /\.capacity-catalog[^}]*user-select\s*:\s*text/s);
  assert.match(css, /@media\s*\([^)]*max-width\s*:\s*1000px[^)]*\)[\s\S]*max-height\s*:\s*720px|@media\s*\([^)]*max-height\s*:\s*720px[^)]*\)/);
});
