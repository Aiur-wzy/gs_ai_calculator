'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const capacityPath = path.join(__dirname, 'gui_capacity_catalog_whiteboard.test.cjs');
const helpers = {
  module: {exports: {}}, require, __dirname, console, process,
  setTimeout, clearTimeout, structuredClone,
};
const prefix = fs.readFileSync(capacityPath, 'utf8').split("\ntest('")[0];
vm.runInNewContext(prefix + '\nmodule.exports={environment,button,field,clone,settle,all};', helpers);
const {environment, button, clone, settle, all} = helpers.module.exports;

const admissionContext = () => ({
  contract: 'static-action-admission-context-v1',
  mode: 'ordinary_open_world',
  external_cooldown_effects: 'none',
  external_energy_cost_effects: 'none',
});

function board() {
  return {
    kind: 'genshin-whiteboard', schema_version: 3,
    board_id: '46c1047c-5f55-4b74-9ad8-6e0c149f97fd', revision: 0,
    title: '动作接纳白板', version_line: 'release', duration: 20, notes: '',
    team: [{id: 'a', db_character_id: 1001, name: '工程角色', level: 90, constellation: 0,
      weapon: null, artifact_sets: [], panel: {dmg_bonus: {}}, notes: ''}],
    actions: [{id: 'q1', character: 'a', action_type_id: 'action.elemental_burst',
      label: '工程元素爆发', start: 0, variant: 'default', notes: ''}],
    capacity_intents: [], action_admission_intents: [],
  };
}

function item() {
  return {
    catalog_group: 'exact', selection_status: 'reference_selectable_for_fresh_preview',
    model_ref: {id: 'model.burst', revision: 1},
    target: {character_id: 1001, skill_depot_id: 2001, skill_id: 3001, slot: 'burst'},
    display: {character: {value: '工程角色'}, skill: {value: '工程元素爆发'}},
  };
}

function dialog(body) {
  return [...all(body)].reverse().find(node => node.tagName === 'dialog' && node.open);
}

test('v3 admission editor keeps null defaults and explicit preset writes context plus empty queue', async () => {
  const {world, body} = environment({whiteboard: true});
  world.WB.state.board = board();
  world.WB.state.selectedActor = 'a';
  const calls = [];
  world.callApi = async (method, document, actor, action, cursor, pageSize, view) => {
    calls.push({method, document: clone(document), actor, action, cursor, pageSize, view});
    return {code: 0, data: {items: [item()], counts: {filtered_models: 1},
      has_more: false, next_cursor: null}};
  };

  world.WB.editAdmissionIntent();
  await settle();
  const editor = dialog(body);
  assert.ok(editor);
  assert.equal(calls[0].method, 'whiteboard_action_admission_model_candidates');
  assert.equal(calls[0].actor, 'a');
  assert.equal(calls[0].action, 'q1');
  assert.equal(JSON.stringify(calls[0].document.action_admission_intents), '[]');
  assert.match(editor.textContent, /均保持 null/);

  button(editor, '选择此模型引用').emit('click');
  button(editor, '使用普通开放世界预设（开场全部就绪）').emit('click');
  button(editor, '保存动作接纳意图').emit('click');

  const saved = world.WB.state.board.action_admission_intents[0];
  assert.equal(JSON.stringify(saved.context), JSON.stringify(admissionContext()));
  assert.equal(JSON.stringify(saved.initial_ready_in), '[]');
  assert.equal(saved.selection.skill_id, 3001);
  assert.equal(Object.hasOwn(saved, 'accepted_fingerprint'), false);
});

test('clearing the preset restores both fields to null without changing the model reference', async () => {
  const {world, body} = environment({whiteboard: true});
  const value = board();
  value.action_admission_intents = [{id: 'admission-1', actor: 'a', action_id: 'q1',
    version_line: 'release', selection: {character_id: 1001, skill_depot_id: 2001,
      skill_id: 3001, slot: 'burst', constellation: 0}, variant: 'default',
    model_ref: {id: 'model.burst', revision: 1}, initial_ready_in: [],
    context: admissionContext()}];
  world.WB.state.board = value;
  world.WB.state.selectedActor = 'a';
  world.callApi = async () => ({code: 0, data: {items: [item()],
    counts: {filtered_models: 1}, has_more: false, next_cursor: null}});

  world.WB.editAdmissionIntent(0);
  await settle();
  const editor = dialog(body);
  button(editor, '清除为未知').emit('click');
  button(editor, '保存动作接纳意图').emit('click');

  const saved = world.WB.state.board.action_admission_intents[0];
  assert.equal(saved.context, null);
  assert.equal(saved.initial_ready_in, null);
  assert.equal(saved.model_ref.id, 'model.burst');
});

test('owned burst locks action identity and prevents deletion until the intent is removed', () => {
  const {world, root, status} = environment({whiteboard: true});
  const value = board();
  value.action_admission_intents = [{id: 'admission-1', actor: 'a', action_id: 'q1',
    version_line: 'release', selection: {character_id: 1001, skill_depot_id: 2001,
      skill_id: 3001, slot: 'burst', constellation: 0}, variant: 'default',
    model_ref: {id: 'model.burst', revision: 1}, initial_ready_in: null, context: null}];
  world.WB.state.board = value;
  world.WB.state.selectedActor = 'a';
  world.WB.state.selectedAction = 'q1';
  world.WB.render();

  const controls = all(root).filter(node => ['select', 'input'].includes(node.tagName));
  assert.ok(controls.some(node => node.disabled));
  world.WB.removeAction('q1');
  assert.equal(value.actions.length, 1);
  assert.match(status.at(-1), /动作接纳意图/);
});
