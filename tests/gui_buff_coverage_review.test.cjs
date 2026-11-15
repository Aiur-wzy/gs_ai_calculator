const GUI_TEST_RUNTIME = require('./helpers/gui_runtime.cjs');
// Independent presentation review: only recorded intervals can become Buff bars.
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const crypto = require('node:crypto');
const zlib = require('node:zlib');
const root = path.resolve(__dirname, '..');
const world = {window:{}};
vm.createContext(world);
GUI_TEST_RUNTIME.loadWebFile(world, path.join(root, 'src/genshin_app/gui/web/timeline-data.js'));
const plain = value => JSON.parse(JSON.stringify(value));
const group = events => plain(world.window.TD.groupCoverage(events));
const event = (recipient, extra={}) => ({id:`buff:${recipient}`,kind:'coverage',character:recipient,
  sourceKey:'support:field',sourceLabel:'辅助角色 · 有限时领域',label:'有限时领域 · 规则有效',start:2,end:12,
  detail:{owner:'support',rule_id:'field',instance_id:'instance',remaining_quota:null,layers:1,
    states:[{start:2,end:12,remaining_quota:null,layers:1,activation_trace_index:0}]},...extra});

test('recipient aggregation preserves every receipt without modifying original projection', () => {
  const input = [event('carry'),event('support')], before=plain(input), out=group(input);
  assert.deepEqual(input,before);
  assert.equal(out.length,1);
  assert.equal(out[0].character,'support');
  assert.deepEqual(out[0].characters,['carry','support']);
  assert.deepEqual(out[0].detail.recipients.map(row=>[row.character,row.event_id]),
    [['carry','buff:carry'],['support','buff:support']]);
  out[0].detail.recipients[0].detail.states[0].end=99;
  assert.deepEqual(input,before);
});

test('same display name does not combine different rules or owners', () => {
  const first=event('carry'), second=event('support',{sourceKey:'support:field-passive'});
  assert.equal(group([first,second]).length,2);
  second.sourceKey=first.sourceKey; second.detail.owner='other';
  assert.equal(group([first,second]).length,2);
});

test('different recipient quota and refresh histories stay separate even for equal time bounds', () => {
  const first=event('carry'), second=event('support');
  first.detail.remaining_quota=1;second.detail.remaining_quota=2;
  assert.equal(group([first,second]).length,2);
  second.detail.remaining_quota=1;second.detail.states[0].activation_trace_index=8;
  assert.equal(group([first,second]).length,2);
  second.detail.states[0].activation_trace_index=0;second.detail.instance_id='independent';
  assert.equal(group([first,second]).length,2);
});

test('gaps, shortened recipient windows and point events are never expanded by grouping', () => {
  const first=event('carry'), second=event('support',{start:14,end:24}), shortened=event('healer',{end:8});
  const point={id:'hit',kind:'hit',sourceKey:'support:field',start:4,character:'carry',label:'命中'};
  const out=group([first,second,shortened,point]);
  assert.deepEqual(out.map(e=>[e.kind,e.start,e.end??null]),
    [['coverage',2,12],['coverage',14,24],['coverage',2,8],['hit',4,null]]);
});

test('unproven overall energy check alone does not erase recorded activation windows; fixed predicates get no bars', () => {
  const args={template:{loop_duration:20,characters:[{id:'carry',name:'输出角色'},{id:'support',name:'辅助角色'}],hits:[],buffs:[]},
    plan:{plan_version:3,rotation_model:{period:20,actions:[],abilities:[]},mechanics_model:{rules:[
      {id:'field',owner:'support',name_zh:'有限时领域',beneficiaries:{mode:'team'},effects:[]}]}},
    result:{rotation_check:{status:'unproven',energy_check:{status:'unproven'},event_log:[]},hits:[],
      fixed_effect_sources:[{id:'self-condition',owner:'carry',availability:'fixed',effects:[
        {id:'crit',target_predicate:{kind:'target_status',status_id:'element_freeze'},value:.2}]}],
      mechanics_trace:[{type:'activation',rule_id:'field',instance_id:'i',t:2,expires_at:[12],
        beneficiaries:['carry','support'],quota:{}}]},current:true};
  const out=plain(world.window.TD.build(args)), bars=out.views.effects.events.filter(e=>e.kind==='coverage');
  assert.deepEqual(bars.map(e=>[e.character,e.start,e.end]),[['carry',2,12],['support',2,12]]);
  assert.ok(!JSON.stringify(bars).includes('self-condition'));
  assert.equal(group(bars).length,1);
});

const realPath=path.join(__dirname,'fixtures/ayaka_028_calculated.json.gz');
// Public fixture retains the original calculation and replaces only two source-machine path strings.
const readFixture=()=>{
  const compressed=fs.readFileSync(realPath);
  assert.equal(crypto.createHash('sha256').update(compressed).digest('hex'),
    '753046bb188755df1d5980deffccb8b0c1fa66d387b2976deae0759189a0fd9e');
  const bytes=zlib.gunzipSync(compressed,{maxOutputLength:16169952});
  assert.equal(bytes.length,16169952);
  return bytes;
};
test('retained Ayaka package exposes recorded team windows without claiming energy proof',
  () => {
  const bytes=readFixture(), sha=crypto.createHash('sha256').update(bytes).digest('hex');
  assert.equal(sha,'97856d63be97198539720bf067a80cf7291751a01b4895ef040646e65c51cb87');
  const c=JSON.parse(bytes).calculated;
  const out=plain(world.window.TD.build({template:c.plan.resolved_template,plan:c.plan,result:c.result,
    executionModel:c.report.mechanics_model,current:true}));
  const bars=out.views.effects.events.filter(e=>e.kind==='coverage');
  const forRule=id=>bars.filter(e=>e.character==='ayaka'&&e.detail.rule_id===id).map(e=>[e.start,e.end]);
  assert.equal(c.result.rotation_check.status,'unproven');
  assert.equal(out.duration,72);
  assert.deepEqual(forRule('kazuha-a4-cryo'),[[3.3,26.55],[27.3,50.55],[51.3,72]]);
  assert.deepEqual(forRule('upstream.4047f67fc28df19446928cb6'),[[3.3,16.1],[18.55,40.1],[42.55,64.1],[66.55,72]]);
  assert.deepEqual(forRule('shenhe-field'),[[.6,14.6],[24.6,38.6],[48.6,62.6]]);
  assert.deepEqual(forRule('shenhe-field-a1-snapshot'),[[.6,12.6],[24.6,36.6],[48.6,60.6]]);
  const grouped=group(bars);
  assert.ok(grouped.length<bars.length);
  const a4=grouped.filter(e=>e.detail.rule_id==='kazuha-a4-cryo');
  assert.equal(a4.length,3);
  assert.ok(a4.every(e=>e.characters.length===4));
  const fixed=new Set(c.result.fixed_effect_sources.map(s=>s.id));
  assert.ok(bars.every(e=>!fixed.has(e.detail.rule_id)));
  assert.equal(crypto.createHash('sha256').update(readFixture()).digest('hex'),sha);
});
