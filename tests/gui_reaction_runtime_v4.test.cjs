const GUI_TEST_RUNTIME = require('./helpers/gui_runtime.cjs');
const test = require('node:test');
const assert = require('node:assert/strict');
const path = require('node:path');
const vm = require('node:vm');

function timeline(trace, specialTrace = []) {
  const world = {window:{}};
  vm.createContext(world);
  GUI_TEST_RUNTIME.loadWebFile(
    world, path.join(__dirname, '../src/genshin_app/gui/web/timeline-data.js'));
  const template = {loop_duration:10, characters:[{id:'actor', name:'测试角色'}], hits:[], buffs:[]};
  const plan = {plan_version:3, manual_template:template,
    rotation_model:{period:10, max_cycles:1, abilities:[], actions:[]},
    mechanics_model:{rules:[], hit_events:[], hit_patterns:[], initial_state:{rules:[]}}};
  const result = {hits:[], mechanics_trace:[], sampling_trace:[],
    rotation_check:{event_log:[]}, elemental:{mode:'simulated', trace},
    special_reactions:{trace:specialTrace}};
  return JSON.parse(JSON.stringify(
    world.window.TD.build({template, plan, result, executionModel:plan.mechanics_model, current:true})));
}

test('v4 ordinary crystallize and Electro Swirl use Chinese effect-axis names', () => {
  const out = timeline([
    {type:'application', event_id:'geo', profile_id:'profile.geo', character:'actor', t:1, element:'geo', reason:'no_icd',
      reactions:[{id:'geo:reaction:0', type:'crystallize', reaction_element:'hydro'}]},
    {type:'application', event_id:'anemo', profile_id:'profile.anemo', character:'actor', t:2, element:'anemo', reason:'no_icd',
      reactions:[{id:'anemo:reaction:0', type:'swirl', reaction_element:'electro'}]},
  ]);
  const reactions = out.views.effects.events.filter(event => event.kind === 'reaction');
  assert.deepEqual(reactions.map(event => event.label), ['结晶（参考模型）', '雷扩散（参考模型）']);
  assert.deepEqual(reactions.map(event => event.element), ['hydro', 'electro']);
  assert.equal(out.views.damage.events.length, 0);
});

test('all five special reaction decisions retain their type and a Chinese decision label', () => {
  const names = new Map([
    ['stellar_superconduct','星超导：替换普通反应'],
    ['stellar_swirl','星扩散：替换普通反应'],
    ['lunar_electro','月感电：替换普通反应'],
    ['lunar_bloom','月绽放：替换普通反应'],
    ['lunar_crystallize','月结晶：替换普通反应'],
  ]);
  const trace = [...names.keys()].map((special_reaction_type, index) => ({
    type:'special_reaction_decision', event_id:`decision:${index}`, t:index+1,
    special_reaction_type, decision:'replace', reason:'enabled_and_qualified', status:'executed',
  }));
  const out = timeline(trace);
  const decisions = out.views.effects.events.filter(event => event.kind === 'reaction');
  assert.deepEqual(decisions.map(event => event.label), [...names.values()]);
  assert.deepEqual(decisions.map(event => event.detail.special_reaction_type), [...names.keys()]);
  assert.ok(decisions.every(event => !/stellar_|lunar_|\breplace\b/u.test(event.label)));
  assert.equal(out.views.damage.events.length, 0);
});

test('special reaction state and schedule operations map to readable effect-axis events', () => {
  const trace = [
    {type:'special_reaction_state',event_id:'field',t:1,reaction_type:'stellar_superconduct',operation:'establish_or_refresh_field'},
    {type:'special_reaction_state',event_id:'vortex',t:2,reaction_type:'stellar_swirl',operation:'create_or_upgrade_vortex'},
    {type:'special_reaction_schedule',event_id:'cloud-extend',t:3,reaction_type:'lunar_electro',operation:'extend_cloud_without_phase_reset'},
    {type:'special_reaction_state',event_id:'dew',t:4,reaction_type:'lunar_bloom',operation:'enqueue_party_dew_fill'},
    {type:'special_reaction_state',event_id:'cage',t:5,reaction_type:'lunar_crystallize',operation:'create_moondrift_set'},
    {type:'special_reaction_state',event_id:'cage-expiry',t:6,reaction_type:'lunar_crystallize',operation:'expire_moondrift_set'},
    {type:'special_reaction_state',event_id:'cloud-expiry',t:7,reaction_type:'lunar_electro',operation:'expire_area_thundercloud'},
    {type:'special_reaction_state',event_id:'dew-boundary',t:8,reaction_type:'lunar_bloom',operation:'resolve_full_cap_queue_boundary'},
  ];
  const out = timeline(trace), conditions = out.views.effects.events.filter(event => event.kind === 'condition');
  assert.deepEqual(conditions.map(event => event.label), [
    '建立或刷新星超导领域','建立或升级星扩散风旋','延长月感电雷云（保留原跳数相位）',
    '登记草露填充','生成月笼','月笼到期','月感电雷云结束','处理草露满额后的排队边界',
  ]);
  assert.ok(conditions.every(event => event.detail.interpretation.includes('专用状态机')));
  assert.equal(out.views.damage.events.length, 0);
});

test('dedicated special reaction trace is merged once into the effect axis', () => {
  const row = {type:'special_reaction_state',event_id:'field',t:1,
    reaction_type:'stellar_superconduct',operation:'establish_or_refresh_field'};
  const onlyDedicated = timeline([], [row]).views.effects.events
    .filter(event => event.label === '建立或刷新星超导领域');
  assert.equal(onlyDedicated.length, 1);
  const transitionalDuplicate = timeline([row], [row]).views.effects.events
    .filter(event => event.label === '建立或刷新星超导领域');
  assert.equal(transitionalDuplicate.length, 1);
});

test('direct Star-Superconduct projection explains runtime-owned settlement layers', () => {
  const out = timeline([], [{
    type:'special_reaction_damage_projection', event_id:'direct:projection',
    request_id:'direct', t:4, reaction_type:'stellar_superconduct',
    settled_layers:7, system_factor:1.75, owner:'actor', status:'executed',
  }]);
  const event = out.views.effects.events.find(row => row.detail?.request_id === 'direct');
  assert.equal(event.label, '星超导：能力直伤已投影（7层，系数1.75）');
  assert.match(event.detail.interpretation, /调用方不能手工提供层数或系数/u);
});

test('historical stellar swirl factor suffixes display real vortex levels', () => {
  const out = timeline([
    {type:'special_reaction_damage_request',event_id:'v1',t:1,reaction_type:'stellar_swirl_cryo_lv2',status:'ready_for_stat_sample'},
    {type:'special_reaction_damage_request',event_id:'v2',t:2,reaction_type:'stellar_swirl_cryo_lv3',status:'ready_for_stat_sample'},
  ]);
  const events = out.views.effects.events.filter(event => event.kind === 'condition');
  assert.deepEqual(events.map(event => event.label), [
    '星扩散·1级风旋爆发状态变化','星扩散·2级风旋爆发状态变化',
  ]);

  const world = {window:{}};
  vm.createContext(world);
  GUI_TEST_RUNTIME.loadWebFile(
    world, path.join(__dirname, '../src/genshin_app/gui/web/effect-description.js'));
  const describe = type => world.window.EFX.describe({kind:'reaction',label:'fallback',start:1,
    detail:{reaction:{type, reaction_element:'cryo'}}},{characterName:id=>id});
  assert.match(describe('stellar_swirl_cryo_lv2').effects.join('；'), /1级风旋爆发/u);
  assert.match(describe('stellar_swirl_cryo_lv3').effects.join('；'), /2级风旋爆发/u);
});

test('ordinary Bloom damage admission is readable and never becomes a damage duplicate', () => {
  const out = timeline([
    {type:'ordinary_bloom_damage_admission',event_id:'core.1:detonate',t:6,owner:'actor',
      target_id:'enemy',reaction_type:'bloom',allowed:true,reason:'first_damage_attempt'},
    {type:'ordinary_bloom_damage_admission',event_id:'core.3:detonate',t:6.1,owner:'actor',
      target_id:'enemy',reaction_type:'bloom',allowed:false,reason:'damage_sequence_blocked'},
  ]);
  const events = out.views.effects.events.filter(event => event.detail?.type === 'ordinary_bloom_damage_admission');
  assert.deepEqual(events.map(event => event.label), [
    '草原核爆炸伤害允许','草原核爆炸受伤害段限制',
  ]);
  assert.ok(events.every(event => event.detail.interpretation.includes('0.5秒内最多计入两段')));
  assert.equal(out.views.damage.events.length, 0);
});

test('ordinary Crystallize pickup and expiry receipts render as state effects', () => {
  const out = timeline([
    {type:'crystallize_shard_pickup',event_id:'pickup',shard_id:'s1',t:1,
      character:'actor',target_id:'enemy',element:'electro',shield_scope:'party',
      shield:{absorption:2400},replaced_shield:null},
    {type:'crystallize_shard_expired',event_id:'expire-s2',shard_id:'s2',t:2,
      target_id:'enemy',element:'hydro',reason:'lifetime'},
    {type:'crystallize_shield_expired',event_id:'expire-shield',t:3,
      target_id:'enemy',element:'electro',character:'actor'},
  ]);
  const events = out.views.effects.events.filter(event =>
    String(event.detail?.type||'').startsWith('crystallize_'));
  assert.deepEqual(events.map(event => event.label), [
    '拾取雷晶片并刷新全队护盾','结晶晶片消失','全队结晶护盾结束',
  ]);
  assert.match(events[0].detail.interpretation, /全队当前普通结晶护盾/u);
  assert.equal(out.views.damage.events.length, 0);
});

test('mechanism source text renders ordinary and special reaction IDs as Chinese', () => {
  const world = {console}; world.window = world;
  vm.createContext(world);
  GUI_TEST_RUNTIME.loadWebFile(
    world, path.join(__dirname, '../src/genshin_app/gui/web/mechanism-sources.js'));
  const cases = [
    ['swirl',['electro'],'雷元素；扩散'],
    ['crystallize',['geo'],'岩元素；结晶'],
    ['stellar_superconduct',['cryo','electro'],'冰、雷元素；星超导'],
    ['stellar_swirl',['anemo','cryo'],'风、冰元素；星扩散'],
    ['lunar_electro',['hydro','electro'],'水、雷元素；月感电'],
    ['lunar_bloom',['hydro','dendro'],'水、草元素；月绽放'],
    ['lunar_crystallize',['geo','hydro'],'岩、水元素；月结晶'],
  ];
  for (const [reaction, elements, expected] of cases) {
    const text = world.MS.ruleText({trigger:{event:'elemental_reaction',reaction_types:[reaction],reaction_elements:elements},
      timing:{duration:0}, effects:[], beneficiaries:{mode:'team'}, stacking:{policy:'refresh'}});
    assert.match(text, new RegExp(expected));
    assert.doesNotMatch(text, /stellar_|lunar_|crystallize|swirl/u);
  }
});
