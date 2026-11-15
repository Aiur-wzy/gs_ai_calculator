const GUI_TEST_RUNTIME = require('./helpers/gui_runtime.cjs');
// Synthetic projection cases: no game answers, DOM or live UI are needed.
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const world = {window:{}};
vm.createContext(world);
GUI_TEST_RUNTIME.loadWebFile(world, path.join(__dirname, '../src/genshin_app/gui/web/timeline-data.js'));
const build = args => JSON.parse(JSON.stringify(world.window.TD.build(args)));
const clone = value => JSON.parse(JSON.stringify(value));
function fixture() {
  const template = {loop_duration:20, characters:[{id:'a', name:'甲角色'}, {id:'b', name:'乙角色'}],
    hits:[{id:'h1', t:2, character:'a', skill:'a.e', hit_name:'元素战技命中', element:'cryo', damage_type_ids:['damage.elemental_skill']}], buffs:[]};
  const rule = {id:'rule', name_zh:'共享增益', owner:'b', beneficiaries:{mode:'team', scope:'locked'},
    effects:[{id:'effect', filter:{event:'hit', phase:'pre', condition:'on_ice'}}]};
  const plan = {plan_version:3, manual_template:template,
    rotation_model:{period:20, max_cycles:3, abilities:[{id:'e', character:'a', action_type_id:'action.elemental_skill', variant:'点按'}],
      actions:[{id:'act', ability_id:'e', start:1, occupation_end:2, order:3, repeat:{mode:'once'}}]},
    mechanics_model:{rules:[rule], hit_events:[{hit_id:'h1', action_id:'act'}], hit_patterns:[], initial_state:{rules:[]}}};
  const result = {hits:[{id:'h1', t:2, character:'a', expected:123, zones:{base:100}, matched_effect_refs:[{effect_id:'effect'}]}],
    mechanics_trace:[], sampling_trace:[], rotation_check:{event_log:[]}};
  return {template, plan, result, executionModel:plan.mechanics_model, current:true};
}
function activation(t, end, extra={}) {
  return {type:'activation', t, rule_id:'rule', instance_id:'instance', event_id:'trigger', cycle:0,
    name_zh:'共享增益', beneficiaries:['a', 'b'], expires_at:[end], quota:{}, ...extra};
}
const coverage = value => value.views.effects.events.filter(e => e.kind === 'coverage');

test('simulated reactions and ICD denials are effects only, with aura detail', () => {
  const f=fixture(); f.template.hits[0].reaction={type:'amplify',factor:2};
  f.result.elemental={mode:'simulated',trace:[
    {event_id:'0:h1',action_id:'act',profile_id:'p',character:'a',t:2,element:'pyro',reason:'no_icd',
      reaction:{type:'melt',factor:2},before:{aura:{element:'cryo',remaining_gu:.8}},after:{aura:null}},
    {event_id:'0:nondamage',action_id:'act',profile_id:'p',profile_status:'confirmed',character:'a',t:3,element:'pyro',
      reason:'icd_blocked',application_allowed:false,reaction_occurred:false,reaction:null,
      icd_audit:{mode:'counter',counter_tag:'normal-attack',rule_group:'default',shared_key:['a','normal-attack','enemy'],
        sequence_index:1,attempt_number:2,coefficient:0,sequence_length:24,consumed:true}}
  ]};
  const out=build(f),reactions=out.views.effects.events.filter(e=>e.kind==='reaction');
  assert.equal(reactions.length,1);assert.match(reactions[0].label,/融化（引擎模拟）/);
  assert.equal(reactions[0].detail.before.aura.remaining_gu,.8);
  assert.ok(out.views.effects.events.some(e=>e.label==='附着：附着冷却阻止'));
  const blocked=out.views.effects.events.find(e=>e.label==='附着：附着冷却阻止');
  assert.equal(blocked.detail.icd_audit.counter_tag,'normal-attack');assert.equal(blocked.detail.icd_audit.rule_group,'default');
  assert.equal(blocked.detail.application_allowed,false);assert.equal(blocked.detail.reaction_occurred,false);
  assert.equal(out.views.damage.events.filter(e=>e.kind==='damage').length,1);
  assert.ok(!JSON.stringify(out).includes('（输入声明）'));
});

test('empty initial UI and absent result remain valid empty projections', () => {
  for (const args of [{}, {template:null,plan:null,result:null,executionModel:null,current:false},
    {template:null,plan:null,result:null,executionModel:null,current:true}]) {
    const out=build(args);
    assert.equal(out.duration,10);
    assert.deepEqual(out.characters,[]);
    assert.ok(Object.values(out.views).every(view=>view.events.length===0));
  }
});

test('explicit actions and pattern offsets do not display material time or check cycles', () => {
  const f = fixture(); f.current = false; f.result = null;
  f.template.hits[0].t = 0;
  f.plan.mechanics_model.hit_events = [];
  f.plan.mechanics_model.hit_patterns = [{id:'pattern', prototype_hit_id:'h1', action_id:'act', offsets:[.2,.4], orders:[4,5]}];
  const out = build(f);
  assert.equal(out.duration,20);
  assert.equal(out.views.actions.events.length,1);
  assert.equal(out.views.actions.events[0].start,1);
  assert.deepEqual(out.views.effects.events.filter(e => e.kind === 'hit').map(e => e.start),[1.2,1.4]);
  assert.equal(out.views.damage.events.length,2);
  assert.ok(out.views.damage.events.every(e => !('value' in e) && !e.edit && /待计算/.test(e.label)));
});

test('stale results and traces are ignored without mutating input', () => {
  const f = fixture(); f.current = false;
  f.result.mechanics_trace = [activation(0,19)];
  const before = clone(f), out = build(f);
  assert.deepEqual(f,before);
  assert.equal(coverage(out).length,0);
  assert.ok(out.views.damage.events.every(e => !('value' in e) && e.kind !== 'calculation'));
  assert.ok(out.views.damage.notes.some(n => n.includes('已失效')));
});

test('current special-reaction projection owns its merged ledger and nested output details', () => {
  const f=fixture();
  f.result.elemental={mode:'simulated',trace:[{type:'application',event_id:'0:h1',t:2,
    character:'a',element:'cryo',reason:'no_icd',before:{aura:null},after:{aura:{element:'cryo',remaining_gu:.8}}}]};
  f.result.special_reactions={trace:[{type:'special_reaction_state',event_id:'special:1',t:3,
    reaction_type:'stellar_superconduct',operation:'settle_four_second_window',layers:3,
    detail:{source_ids:['source-a']}}]};
  const before=clone(f);
  const freeze=value=>{if(value&&typeof value==='object'){Object.freeze(value);for(const item of Object.values(value))freeze(item);}return value;};
  freeze(f);
  const first=build(f), second=build(f);
  assert.deepEqual(f,before);
  assert.deepEqual(first,second);
  assert.equal(f.result.elemental.trace.length,1);
  const special=first.views.effects.events.find(row=>row.detail.event_id==='special:1');
  assert.ok(special);
  special.detail.detail.source_ids.push('view-only');
  assert.deepEqual(f.result.special_reactions.trace[0].detail.source_ids,['source-a']);
  assert.deepEqual(second.views.effects.events.find(row=>row.detail.event_id==='special:1').detail.detail.source_ids,['source-a']);
});

test('reaction contacts without reported hits do not amend input hit prototypes', () => {
  const f=fixture();
  f.result.hits=[];
  f.result.mechanics_trace=[{type:'reaction_damage',event_id:'h1',action_id:'act',t:2,cycle:0,
    reaction_element:'anemo',status:'executed'}];
  const before=clone(f);
  build(f);
  assert.deepEqual(f,before);
});

test('refresh replaces the old expiration while preserving source and recipients', () => {
  const f = fixture();
  f.result.mechanics_trace = [activation(1,11),activation(5,15),
    {type:'expiry',t:15,checked_at:17,instance_id:'instance',rule_id:'rule',layers_after:0}];
  const out = build(f), bars = coverage(out).filter(e => e.character === 'a');
  assert.deepEqual(bars.map(e => [e.start,e.end]),[[1,15]]);
  assert.ok(bars.every(e => e.detail.owner === 'b' && e.sourceLabel.includes('乙角色') && e.sourceLabel.includes('共享增益')));
  assert.equal(new Set(coverage(out).map(e => e.sourceKey)).size,1);
  assert.deepEqual(new Set(coverage(out).map(e => e.character)),new Set(['a','b']));
  assert.equal(bars[0].detail.filters[0].filter.condition,'on_ice');
  assert.deepEqual(bars[0].detail.states.map(s=>[s.start,s.end]),[[1,5],[5,15]]);
});

test('quota depletion clips availability; keep remains empty and reset uses receipt', () => {
  const f = fixture();
  f.result.mechanics_trace = [activation(1,11,{quota:{a:1,b:2}}),
    {type:'quota',t:3,rule_id:'rule',instance_id:'instance',pool:'a',before:1,after:0,consumed:true,event_id:'h1'},
    activation(5,15,{quota:{a:0,b:2}}),activation(8,18,{quota:{a:2,b:2}})];
  const bars = coverage(build(f)).filter(e => e.character === 'a');
  assert.deepEqual(bars.map(e => [e.start,e.end]),[[1,3],[8,18]]);
  assert.equal(bars[0].detail.rule_expires_at,11);
  assert.equal(bars[1].detail.remaining_quota,2);
});

test('shared-pool stock changes become readable effect points without audit noise', () => {
  const f=fixture(),model=f.plan.mechanics_model;
  model.rules[0].resource_gate={consumer_id:'consume'};
  model.shared_pools={schema_version:2,
    pools:[{id:'stock',name_zh:'共享星刃库存',owner:'b',scope:'team',capacity:10,unit_scale:1,status:'confirmed',source:'规则原文'}],
    operations:[{id:'set',pool_id:'stock',operation:'set',status:'confirmed'},
      {id:'grant',pool_id:'stock',operation:'grant',status:'confirmed'}],
    consumers:[{id:'consume',pool_id:'stock',eligibility_rule_id:'rule',actor_relation:'other_frontline',
      filter:{event:'hit',phase:'pre',damage_type_ids:['damage.elemental_skill']},status:'confirmed'}]};
  f.result.shared_pools={trace:[
    {type:'operation',operation:'set',operation_id:'set',pool_id:'stock',source_id:'source',event_id:'cast',t:1,cycle:0,before:0,after:10,requested:10,accepted:10,succeeded:true,reason:'set'},
    {type:'consume',consumer_id:'consume',pool_id:'stock',source_id:'source',event_id:'h1',actor:'a',t:2,cycle:0,before:10,after:9,requested:1,accepted:1,qualified:true,eligibility_reason:'eligible',reason:'consumed'},
    {type:'consume',consumer_id:'consume',pool_id:'stock',source_id:'source',event_id:'later',actor:'a',t:3,cycle:0,before:0,after:0,requested:1,accepted:0,qualified:true,eligibility_reason:'eligible',reason:'insufficient'},
    {type:'consume',consumer_id:'consume',pool_id:'stock',source_id:'source',event_id:'owner',actor:'b',t:4,cycle:0,before:9,after:9,requested:1,accepted:0,qualified:false,eligibility_reason:'actor_relation',reason:'ineligible'},
    {type:'operation',operation:'grant',operation_id:'grant',pool_id:'stock',source_id:'source',event_id:'cooldown',t:5,cycle:0,before:9,after:9,requested:3,accepted:0,succeeded:false,reason:'cooldown'},
    {type:'operation',operation:'grant',operation_id:'grant',pool_id:'stock',source_id:'source',event_id:'check',t:6,cycle:1,before:9,after:10,requested:3,accepted:1,succeeded:true,reason:'granted'},
  ]};
  const before=clone(f),out=build(f),events=out.views.effects.events.filter(e=>e.kind==='resource');
  assert.deepEqual(events.map(e=>[e.start,e.label]),[[1,'共享星刃库存 · 设置库存（0→10）'],
    [2,'共享星刃库存 · 消费 1（10→9）'],[3,'共享星刃库存 · 库存不足（0→0）']]);
  assert.equal(events[1].detail.rule_id,'rule');assert.equal(events[1].detail.effects[0].id,'effect');
  assert.equal(events[1].character,'a');assert.match(events[1].sourceLabel,/乙角色.*共享增益/);
  assert.ok(out.views.effects.notes.some(note=>note.includes('不合格检查')));
  assert.deepEqual(f,before);
});

test('legacy shared-pool variants normalize receipt kind and filter disallowed coverage recipients', () => {
  const f=fixture(),model=f.plan.mechanics_model;
  model.rules[0].resource_gate={consumer_id:'consume'};
  model.shared_pools={schema_version:2,
    pools:[{id:'stock',name_zh:'共享库存',owner:'b',scope:'team',capacity:10,unit_scale:1,status:'confirmed'}],
    operations:[],consumers:[{id:'consume',pool_id:'stock',eligibility_rule_id:'rule',actor_relation:'other_frontline',
      filter:{event:'hit',phase:'pre',characters:['a','b']},status:'confirmed'}]};
  f.result.mechanics_trace=[activation(1,11)];
  f.result.shared_pools={trace:[{kind:'consume',consumer_id:'consume',pool_id:'stock',event_id:'h1',actor:'a',
    t:2,cycle:0,before:1,after:0,requested:1,accepted:1,qualified:true,eligibility_reason:'eligible',reason:'consumed'}]};
  const out=build(f),resource=out.views.effects.events.find(event=>event.kind==='resource');
  assert.equal(resource.detail.type,'consume');
  assert.deepEqual(coverage(out).map(event=>event.character),['a']);
});

test('damage calculation detail names actual applied effects from their source rule', () => {
  const f=fixture();
  f.plan.mechanics_model.rules[0].effects[0].benefit_type_id='benefit.base_add.all';
  f.result.hits[0].matched_effect_refs=[{buff_id:'mechanics:h1',effect_id:'rule:effect:0'}];
  f.result.mechanics_trace=[{type:'effect_applied',t:2,event_id:'h1',rule_id:'rule',effect_id:'effect',
    instance_id:'instance',source_id:'source',sample_t:2,layers:1,value:2800,domain:'provider',projected_effect_id:'rule:effect:0'}];
  const calculation=build(f).views.damage.events.find(event=>event.kind==='calculation');
  assert.deepEqual(calculation.detail.applied_effects,[{
    type:'effect_applied',t:2,event_id:'h1',rule_id:'rule',effect_id:'effect',instance_id:'instance',source_id:'source',
    sample_t:2,layers:1,value:2800,domain:'provider',trace_index:0,owner:'b',source_name:'共享增益',
    projected_effect_id:'rule:effect:0',consumption_status:'consumed',benefit_type_id:'benefit.base_add.all',
  }]);
});

test('damage calculation detail preserves a receipt benefit type when the saved rule is incomplete', () => {
  const f=fixture();f.plan.mechanics_model.rules[0].effects=[];
  f.result.hits[0].matched_effect_refs=[{buff_id:'mechanics:h1',effect_id:'rule:legacy:0'}];
  f.result.mechanics_trace=[{type:'effect_applied',t:2,event_id:'h1',rule_id:'rule',effect_id:'legacy',
    value:2800,benefit_type_id:'benefit.base_add.all',projected_effect_id:'rule:legacy:0'}];
  const calculation=build(f).views.damage.events.find(event=>event.kind==='calculation');
  assert.equal(calculation.detail.applied_effects[0].benefit_type_id,'benefit.base_add.all');
});

test('materialization without final consumer evidence is retained but never called actual formula benefit',()=>{
  const f=fixture();delete f.result.hits[0].matched_effect_refs;
  f.result.mechanics_trace=[{type:'effect_applied',t:2,event_id:'h1',rule_id:'rule',effect_id:'legacy',
    value:2800,benefit_type_id:'benefit.base_add.all',projected_effect_id:'rule:legacy:0'}];
  let detail=build(f).views.damage.events.find(event=>event.kind==='calculation').detail;
  assert.deepEqual(detail.applied_effects,[]);
  assert.equal(detail.materialized_effects[0].consumption_status,'unknown');
  assert.equal(detail.materialized_effects[0].value,2800);
  f.result.hits[0].matched_effect_refs=[];
  detail=build(f).views.damage.events.find(event=>event.kind==='calculation').detail;
  assert.equal(detail.materialized_effects[0].consumption_status,'not_consumed');
  assert.equal(detail.effects[0].value,2800);
});

test('old activation without instance identity is a point only', () => {
  const f = fixture(), old = activation(1,11); delete old.instance_id;
  f.result.mechanics_trace = [old,{type:'expiry',t:11,instance_id:'guess',rule_id:'rule',layers_after:0}];
  const out = build(f);
  assert.equal(coverage(out).length,0);
  assert.equal(out.views.effects.events.filter(e => e.kind === 'activation').length,1);
  assert.ok(out.views.effects.notes.some(n => n.includes('不猜测')));
});

test('initial instances use explicit remaining time and current-frontline caveat', () => {
  const f = fixture();
  f.executionModel.rules[0].beneficiaries.scope = 'current_frontline';
  f.executionModel.initial_state.rules = [{rule_id:'rule', cooldown_remaining:0,
    instances:[{beneficiaries:['a','b'],remaining:[4],quota:{a:0,b:2}}]}];
  const out = build(f), bars = coverage(out);
  assert.deepEqual(bars.map(e => [e.character,e.start,e.end]),[['b',0,4]]);
  assert.equal(bars[0].detail.beneficiaries_scope.scope,'current_frontline');
  assert.match(bars[0].detail.interpretation,/非每击必定受益/);
});

test('same timestamp retains trace order and zero duration stays a point', () => {
  const f = fixture();
  f.result.mechanics_trace = [{type:'quota',t:1,rule_id:'rule',instance_id:'previous',pool:'a',before:1,after:0,consumed:true},
    activation(1,1),activation(1,3,{instance_id:'next'})];
  const events = build(f).views.effects.events.filter(e => e.start === 1 && ['quota','activation'].includes(e.kind));
  assert.deepEqual(events.map(e => e.kind),['quota','activation','activation']);
  assert.equal(coverage(build(f)).filter(e => e.detail.instance_id === 'instance').length,0);
});

test('per-hit numeric projections never become duration bars and manual input is editable', () => {
  const f = fixture(); f.plan = null; f.current = false; f.result = null;
  f.template.buffs = [{id:'mechanics:h1',name:'机制派生收益·测试',start:2,end:2.000001,effects:[]},
    {id:'manual',name:'手工增益',start:1,end:4,effects:[]}];
  const out = build(f);
  assert.equal(coverage(out).length,1);
  assert.deepEqual(coverage(out)[0].edit,{kind:'buff',index:1});
  assert.deepEqual(out.views.effects.events.find(e => e.kind === 'hit').edit,{kind:'hit',index:0});
  assert.ok(out.views.effects.events.every(e => !e.label.startsWith('机制派生收益')));
});

test('declared reactions and one calculation record per hit retain post-expiry snapshot evidence', () => {
  const f = fixture();
  f.template.hits[0].reaction = {type:'swirl'};
  f.result.mechanics_trace = [activation(0,1),
    {type:'effect_applied',t:2,event_id:'h1',rule_id:'rule',effect_id:'effect',instance_id:'instance',sample_t:.5},
    {type:'hit',t:2,event_id:'h1',cycle:0,status:'executed',reported_damage:true}];
  f.result.sampling_trace = [{t:2,event_id:'h1',rule_id:'rule',effect_id:'effect',sample_t:.5,source_value:100,value:10}];
  const out = build(f), calc = out.views.damage.events.filter(e => e.kind === 'calculation');
  assert.equal(calc.length,1);
  assert.equal(calc[0].detail.sampling[0].sample_t,.5);
  assert.equal(calc[0].detail.effects[0].sample_t,.5);
  assert.equal(out.views.damage.events.find(e => e.kind === 'damage').value,123);
  assert.match(out.views.effects.events.find(e => e.kind === 'reaction').label,/输入声明/);
  assert.match(out.views.effects.notes.join(''),/不代表引擎模拟/);
  assert.equal(coverage(out)[0].end,1);
});

test('failed execution clips coverage and patterns omit actions outside the input window', () => {
  const f = fixture();
  f.result.mechanics_trace=[activation(1,19)];
  f.result.rotation_check.event_log=[{type:'action',t:4,status:'failed',id:'late'}];
  const out = build(f);
  assert.ok(coverage(out).every(e => e.end === 4));
  f.current=false; f.result=null;
  f.plan.rotation_model.actions[0].repeat={mode:'cycles',cycles:[1,2]};
  assert.equal(build(f).views.actions.events.length,0);
  assert.equal(build(f).views.effects.events.filter(e=>e.kind==='hit').length,0);
});

test('outputs are detached data and labels do not leak opaque identifiers', () => {
  const f = fixture(), before=clone(f);
  f.result.mechanics_trace = [activation(1,11)];
  const original=clone(f), out=build(f);
  out.views.damage.events.find(e=>e.kind==='calculation').detail.zones.base=999;
  out.characters[0].label='changed';
  assert.deepEqual(f,original);
  assert.equal(before.template.characters[0].name,'甲角色');
  assert.ok(out.views.actions.events.every(e=>!e.label.includes('a.e')));
});

test('visible labels remove derived prefixes and use character names from the input', () => {
  const f=fixture();
  f.plan.mechanics_model.rules[0].name_zh='机制派生 · a元素战技产球';
  f.result.mechanics_trace=[activation(1,11)];
  const before=clone(f), events=build(f).views.effects.events;
  assert.ok(events.some(e=>e.label.includes('甲角色元素战技产球')));
  assert.ok(events.every(e=>!e.label.includes('机制派生')));
  assert.deepEqual(f,before);
});

function receiptState(extra={}) {
  return {id:'state-a',rule_id:'recorded-rule',owner:'b',source_id:'source',name_zh:'记录中的共享增益',instance_id:'receipt-instance',beneficiary:'a',
    origin:'mechanics',evidence_status:'verified',beneficiaries_scope:{mode:'team'},filters:[],
    slices:[{start:1,end:3,status:'active',layers:1,remaining_quota:1,activation_time:1,activation_trace_index:0,rule_expires_at:11,end_reason:'quota_change'},
      {start:3,end:5,status:'exhausted',layers:1,remaining_quota:0,activation_time:1,activation_trace_index:0,rule_expires_at:11,end_reason:'refresh'},
      {start:5,end:15,status:'active',layers:1,remaining_quota:2,activation_time:5,activation_trace_index:4,rule_expires_at:15,end_reason:'natural_expiry'}],...extra};
}
function receipt(states=[receiptState()]) {return {contract:'buff-state-timeline-v1',version:1,duration:20,states,
  excluded:[{name_zh:'无时长条件',code:'fixed',message:'无独立持续时间'}],notes:[]};}

test('authoritative state receipt works without compiled model and preserves exhausted interval',()=>{
  const f=fixture();f.plan=null;f.executionModel=null;f.result.buff_state_timeline=receipt();f.result.mechanics_trace=[activation(0,19)];
  const before=clone(f),out=build(f),bars=coverage(out);
  assert.deepEqual(bars.map(e=>[e.start,e.end,e.detail.state_status]),[[1,3,'active'],[3,5,'exhausted'],[5,15,'active']]);
  assert.ok(bars.every(e=>e.sourceLabel==='乙角色 · 记录中的共享增益'&&!e.edit));
  assert.equal(bars[1].detail.remaining_quota,0);assert.equal(bars[1].detail.end_reason,'refresh');
  assert.equal(out.views.effects.coverage.origin,'engine');assert.equal(out.views.effects.coverage.excluded[0].message,'无独立持续时间');
  assert.deepEqual(f,before);
});

test('authoritative resource-gated receipt preserves available and exhausted stock slices',()=>{
  const f=fixture();f.plan=null;f.executionModel=null;
  const gate={consumer_id:'consume',pool_id:'stock',actor_relation:'other_frontline',amount:1,
    filter:{event:'hit',phase:'pre'},status:'confirmed',pool:{id:'stock',name_zh:'共享库存',scope:'team',initial:{team:10},capacity:'unbounded'}};
  const state=receiptState({resource_gate:gate,slices:[
    {start:1,end:3,status:'active',layers:1,remaining_quota:null,resource_balance:{remaining:2,required:1,unit_scale:1,status:'available'},activation_trace_index:0,rule_expires_at:11,end_reason:'resource_change'},
    {start:3,end:5,status:'exhausted',layers:1,remaining_quota:null,resource_balance:{remaining:0,required:1,unit_scale:1,status:'exhausted'},activation_trace_index:0,rule_expires_at:11,end_reason:'resource_change'},
    {start:5,end:11,status:'active',layers:1,remaining_quota:null,resource_balance:{remaining:3,required:1,unit_scale:1,status:'available'},activation_trace_index:0,rule_expires_at:11,end_reason:'natural_expiry'}]});
  f.result.buff_state_timeline=receipt([state]);
  const bars=coverage(build(f));
  assert.deepEqual(bars.map(event=>[event.start,event.end,event.detail.state_status,event.detail.resource_balance.remaining]),
    [[1,3,'active',2],[3,5,'exhausted',0],[5,11,'active',3]]);
  assert.ok(bars.every(event=>event.detail.resource_gate.pool.capacity==='unbounded'));
});

test('receipt is ignored when stale, empty authoritative receipt cannot fall back to stale traces',()=>{
  const f=fixture();f.result.buff_state_timeline=receipt([]);f.result.mechanics_trace=[activation(1,19)];
  assert.equal(coverage(build(f)).length,0);
  f.result.buff_state_timeline=receipt();f.current=false;
  const out=build(f);assert.equal(coverage(out).length,0);assert.equal(out.views.effects.coverage.origin,'stale');
});

test('receipt replaces duplicate manual windows and keeps declared sources distinct',()=>{
  const f=fixture();f.template.buffs=[{id:'manual',name:'手工区间',start:1,end:3}];
  const state=receiptState({rule_id:null,origin:'declared_interval',buff_id:'manual',slices:[{start:1,end:3,status:'active',layers:1,remaining_quota:null,end_reason:'natural_expiry'}]});
  f.result.buff_state_timeline=receipt([state,{...state,id:'other',buff_id:'other'}]);
  const bars=coverage(build(f));assert.equal(bars.length,2);assert.ok(bars.every(e=>e.label.endsWith('手工声明')&&!e.edit));
  assert.notEqual(bars[0].sourceKey,bars[1].sourceKey);
});

test('legacy calculation input restores compiled finite windows without exposing edit action',()=>{
  const f=fixture();f.executionTemplate={buffs:[{id:'composed',name:'组合器增益',start:2,end:10,target:'a'}]};
  const bars=coverage(build(f));assert.equal(bars.length,1);assert.equal(bars[0].start,2);assert.equal(bars[0].end,10);assert.equal(bars[0].edit,undefined);
});

test('recipient-specific receipt identities do not duplicate identical team windows',()=>{
  const f=fixture(),a=receiptState();f.result.buff_state_timeline=receipt([a,{...a,id:'state-b',beneficiary:'b'}]);
  const out=build(f),bars=coverage(out),grouped=clone(world.window.TD.groupCoverage(bars,out.characters));
  assert.equal(grouped.length,3);assert.deepEqual(grouped[0].characters,['a','b']);
  assert.deepEqual(grouped[0].detail.recipients.map(row=>row.detail.receipt_id),['state-a','state-b']);
});
