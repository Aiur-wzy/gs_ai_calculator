const GUI_TEST_RUNTIME = require('./helpers/gui_runtime.cjs');
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const world = {window:{}};
vm.createContext(world);
GUI_TEST_RUNTIME.loadWebFile(world, path.join(__dirname, '../src/genshin_app/gui/web/effect-description.js'));
const names = {a:'甲角色', b:'乙角色', c:'丙角色'};
const describe = event => JSON.parse(JSON.stringify(world.window.EFX.describe(event, {characterName:id=>names[id]})));
const flat = result => JSON.stringify(result);
const effect = (zone, stat='all', value=.15, extra={}) => ({id:'e', benefit_type_id:`benefit.${zone}.${stat}`, value, domain:'self', ...extra});
const coverage = (effects, detail={}) => ({kind:'coverage', character:'a', start:.6, end:12.6,
  detail:{owner:'b', effects, source_name:'同名合并说明', ...detail}});

test('same source title is split by actual benefits without interpreting source IDs', () => {
  const shred=describe(coverage([effect('res_shred','cryo',.15,{domain:'enemy'}),effect('res_shred','physical',.15,{id:'p',domain:'enemy'})]));
  const bonus=describe(coverage([effect('dmg_bonus','cryo')]));
  assert.equal(shred.title,'冰／物理减抗'); assert.equal(bonus.title,'冰伤加成');
  assert.equal(shred.source, bonus.source); assert.match(shred.source,/乙角色.*同名合并说明/);
  assert.equal(shred.effects.length,2); assert.match(shred.values.join(' '),/15%/);
  assert.equal(describe(coverage([], {source_id:'shenhe.skill.3'})).source.includes('申鹤'),false);
});

test('registered ratio, flat, multiplier and base-panel meanings differ', () => {
  const r=describe(coverage([effect('base_add','all',123), effect('stat_pct','atk',.18,{id:'s'}), effect('base_mult','all',1.2,{id:'m'})]));
  assert.match(r.effects.join(' '),/不增加角色面板攻击力/);
  assert.match(r.effects.join(' '),/按对应基础面板/);
  assert.match(r.values.join(' '),/123。/); assert.match(r.values.join(' '),/18%/); assert.match(r.values.join(' '),/×1.2/);
  assert.doesNotMatch(r.values.join(' '),/12300%|120%/);
});

test('attribute formula retains subject, factor, offset, bounds and sampling without estimating stats', () => {
  const r=describe(coverage([effect('base_add','all',{kind:'stat',subject:'owner',stat:'atk',factor:.822,add:10,min:0,max:4000,sample:'trigger'})]));
  const values=r.values.join(' ');
  assert.match(values,/乙角色.*攻击力 × 0.822 \+ 10/); assert.match(values,/下限 0，上限 4000/);
  assert.match(values,/按触发时面板取样/); assert.doesNotMatch(values,/822%|实际取值/);
});

test('dynamic ratio conversion is clearly a formula, not a fabricated fixed value', () => {
  const r=describe(coverage([effect('dmg_bonus','cryo',{kind:'stat',subject:'owner',stat:'em',factor:.0004,sample:'current'})]));
  assert.match(r.values.join(' '),/元素精通 × 0.0004/); assert.match(r.values.join(' '),/1 = 100%/);
  assert.doesNotMatch(r.values.join(' '),/40%/);
});

test('projected samples are already layered, bounded and differentiated from sampling state time', () => {
  const r=describe(coverage([effect('dmg_bonus','cryo')],{layers:2,applied_value_count:80,
    applied_values:[{effect_id:'e',value:.3,layers:2,t:8,sample_t:2,character:'a'},{effect_id:'e',value:.4,layers:2,t:9,sample_t:3}]}));
  const values=r.values.join(' ');
  assert.match(values,/每层声明值 15%/); assert.match(values,/投影值：30% ～ 40%/);
  assert.match(r.samples.join(' '),/8 秒命中，使用 2 秒的收益状态/); assert.doesNotMatch(values,/60%|80%/);
  assert.doesNotMatch(values,/秒命中/); assert.match(values,/不等于该次伤害公式实际使用/);
  assert.match(r.notes.join(' '),/共 80 条.*前 2 条/);
});

test('equal single-layer constant samples do not repeat the declared value', () => {
  const detail={layers:1,applied_values:[{effect_id:'e',value:.15,layers:1,t:8,sample_t:2}]};
  const r=describe(coverage([effect('res_shred','physical')],detail));
  assert.match(r.values.join(' '),/每层声明值 15%/);
  assert.doesNotMatch(r.values.join(' '),/投影值/); assert.equal(r.samples.length,0);
  detail.applied_values[0].layers=2;
  assert.match(describe(coverage([effect('res_shred','physical')],detail)).values.join(' '),/投影值/);
  const dynamic=describe(coverage([effect('dmg_bonus','cryo',{kind:'stat',subject:'owner',stat:'em',factor:.0004,sample:'current'})],detail));
  assert.equal(dynamic.samples.length,1);
});

test('activation recipients come from receipt rather than the source owner; coverage grouping wins', () => {
  const event={kind:'activation',character:'b',start:0,detail:{owner:'b',beneficiaries:['a','b','c'],effects:[effect('stat_pct','atk',.2)]}};
  const r=describe(event);
  assert.match(r.conditions.join(' '),/实例接收角色：甲角色、乙角色、丙角色/);
  assert.match(r.notes.join(' '),/本记录/); assert.doesNotMatch(r.notes.join(' '),/持续条/);
  event.kind='coverage';event.beneficiary_names=['甲角色','丙角色'];
  assert.match(describe(event).conditions.join(' '),/实例接收角色：甲角色、丙角色/);
  delete event.beneficiary_names;event.kind='activation';event.detail.beneficiaries=[];
  assert.match(describe(event).conditions.join(' '),/没有接收角色/);
});

test('existing owner-prefixed source labels are not prefixed a second time', () => {
  const r=describe({kind:'hit',character:'b',label:'爆发命中',sourceLabel:'乙角色 · 元素爆发',start:0,detail:{}});
  assert.equal(r.source,'乙角色 · 元素爆发');
});

test('exact source bindings identify category and declared references without guessing equipment IDs', () => {
  const r=describe(coverage([effect('stat_pct','atk',.2)],{source_bindings:[
    {owner:{kind:'artifact_set',set_id:99999,required_count:4},label:'声明套装加攻',source_refs:[{note:'已封存资料',path:'facts/set.md',table:'EquipAffixExcelConfigData'}]},
    {owner:{kind:'manual',note:'由技能原文与固有天赋逐项校准'},source_refs:[]},
    {owner:{kind:'weapon',weapon_id:11509},source_refs:[]},
    {owner:{kind:'character_skill',character_id:10000000},source_refs:[]},
    {owner:{kind:'unknown_future'},source_refs:[]},
  ]}));
  assert.match(r.source,/圣遗物 4 件套.*人工规则.*武器.*角色技能／天赋.*来源类别未提供/);
  assert.match(r.evidence.join(' '),/技能原文与固有天赋/);
  assert.match(r.evidence.join(' '),/不代表本次已读取.*facts\/set.md.*EquipAffixExcelConfigData/);
  assert.doesNotMatch(flat(r),/雾切|宗室|99999|11509/);
});

test('grouped recipients and selectors are explicit; domains never choose recipients', () => {
  const event=coverage([effect('dmg_bonus','cryo',.15,{domain:'provider',filter:{event:'hit',phase:'pre',damage_type_ids:['damage.elemental_burst'],elements:['cryo'],condition:'unknown_name'}})],
    {beneficiaries_scope:{mode:'characters',characters:['a','c'],scope:'current_frontline'}, trigger:{event:'elemental_reaction',phase:'post',reaction_types:['swirl'],owner_presence:'frontline'}});
  event.beneficiary_names=['甲角色','丙角色'];
  const r=describe(event);
  assert.match(r.conditions.join(' '),/实例接收角色：甲角色、丙角色/);
  assert.match(r.conditions.join(' '),/当前在前台/); assert.match(r.conditions.join(' '),/元素爆发伤害/);
  assert.match(r.conditions.join(' '),/外部条件成立.*中文名称未提供/);
  assert.match(r.conditions.join(' '),/反应类型：扩散.*提供者必须在前台/);
  assert.match(r.effects.join(' '),/受益角色由范围另行决定/);
});

test('visible length, declared per-trigger duration, refresh records and cutoff remain distinct', () => {
  const event=coverage([effect('dmg_bonus')],{rule:{timing:{duration:12,delay:.6}},end_reason:'execution_limit',rule_expires_at:19,
    states:[{start:.6,end:7,activation_time:.6,rule_expires_at:12.6},{start:7,end:18,activation_time:7,rule_expires_at:19}]});
  event.end=18;
  const r=describe(event);
  assert.match(r.duration,/显示段 17.4 秒/); assert.match(r.duration,/每次触发声明持续 12 秒/);
  assert.match(r.duration,/执行检查中断/); assert.match(r.duration,/合并了 2 条/);
  assert.match(r.notes.join(' '),/激活／刷新 7 秒 → 到期 19 秒/);
});

test('initial remaining time is not invented into full skill duration', () => {
  const r=describe(coverage([effect('dmg_bonus')],{states:[{origin:'initial_state',activation_time:0,rule_expires_at:6}]}));
  assert.match(r.notes.join(' '),/剩余时间.*不能用于推断/); assert.doesNotMatch(r.duration,/声明持续 6/);
});

test('quota exhaustion and non-monotonic states are preserved', () => {
  const r=describe(coverage([effect('base_add','all',600)],{state_status:'exhausted',remaining_quota:0,layers:null,
    states:[{layers:1},{layers:2},{layers:1}],quota_pool:'team',rule:{quota:{capacity:5,scope:'team',refresh:'reset'}}}));
  assert.match(r.title,/次数耗尽/); assert.match(r.effects.join(' '),/不再计作可用收益/);
  assert.match(r.values.join(' '),/1 层 → 2 层 → 1 层/); assert.match(r.values.join(' '),/剩余次数：0 次（全队共享）/);
  const point=describe({kind:'quota',start:4,detail:{before:5,after:4,consumed:true}});
  assert.match(point.values.join(' '),/5 → 4/); assert.doesNotMatch(point.conditions.join(' '),/元素底/);
});

test('shared-pool receipts explain stock change and the benefit on successful consumption', () => {
  const base={kind:'resource',character:'a',start:2,label:'共享库存',detail:{owner:'b',source_name:'一命共享库存',
    source:'已核实机制说明',pool_name:'星刃库存',type:'consume',before:10,after:9,requested:1,accepted:1,
    reason:'consumed',consumer:{actor_relation:'other_frontline',filter:{event:'hit',phase:'pre',damage_type_ids:['damage.elemental_skill']}},
    trigger_hit:{character:'a',name_zh:'元素战技命中'},effects:[effect('base_add','all',{kind:'stat',subject:'owner',stat:'em',factor:2,sample:'current'})]}};
  const r=describe(base);
  assert.equal(r.title,'星刃库存 · 实际消费');
  assert.match(r.source,/乙角色.*一命共享库存/);assert.match(r.effects.join(' '),/实际消费.*伤害基础区/);
  assert.match(r.values.join(' '),/元素精通 × 2.*库存余额：10 → 9.*请求 1，实际 1/);
  assert.match(r.conditions.join(' '),/元素战技伤害.*仅提供者以外的当前前台角色.*甲角色的元素战技命中/);
  assert.match(r.duration,/2 秒发生的时间点/);assert.match(r.evidence.join(' '),/已核实机制说明/);

  base.detail={...base.detail,before:0,after:0,accepted:0,reason:'insufficient',effects:[]};
  const empty=describe(base);assert.equal(empty.title,'星刃库存 · 库存不足');
  assert.match(empty.effects.join(' '),/没有进入该次伤害/);
});

test('resource-gated duration names its source and does not promise per-hit availability', () => {
  const event=coverage([effect('base_add','all',600)],{source_name:'一命共享库存',resource_gate:{consumer_id:'consume',
    actor_relation:'other_frontline',filter:{event:'hit',phase:'pre'},pool:{name_zh:'星刃库存',capacity:10}},
    state_status:'exhausted',resource_balance:{remaining:0,required:1,unit_scale:1}});
  const r=describe(event);
  assert.match(r.title,/一命共享库存.*伤害基础平值加算.*共享库存耗尽.*受共享库存门控/);
  assert.match(r.conditions.join(' '),/星刃库存.*玩法容量 10.*实际是否受益.*仅提供者以外/);
  assert.match(r.values.join(' '),/此段共享库存：0.*下一次消费需要 1/);
  assert.match(r.effects.join(' '),/共享库存不足.*规则期限尚未结束/);
  assert.match(r.notes.join(' '),/持续条只表示规则期限.*库存为零/);
});

test('resource-gated duration distinguishes an unbounded gameplay pool from a finite cap', () => {
  const r=describe(coverage([effect('base_add','all',600)],{resource_gate:{consumer_id:'consume',
    pool:{name_zh:'共享库存',capacity:'unbounded'}}}));
  assert.match(r.conditions.join(' '),/未声明玩法容量上限.*引擎安全界/);
});

test('applied benefit formatter keeps zone units readable for damage details', () => {
  const applied=record=>JSON.parse(JSON.stringify(world.window.EFX.describeAppliedBenefit(record)));
  assert.deepEqual(applied({benefit_type_id:'benefit.base_add.all',value:2800}),
    {label:'伤害基础平值加算',value:'2800'});
  assert.deepEqual(applied({benefit_type_id:'benefit.dmg_bonus.pyro',value:.4}),
    {label:'火伤加成',value:'40%'});
});

test('manual values have no invented layer and explicit hand declaration', () => {
  const r=describe({kind:'coverage',start:1,end:11,character:'a',detail:{origin:'declared_interval',evidence_status:'declared',
    buff:{name:'手工增益',source:'本地校准记录',scope:'frontline',target:'a',effects:[{zone:'crit_rate',stat:'all',value:.2,applies_to:{mode:'none'}}]}}});
  assert.match(r.values.join(' '),/声明值 20%/); assert.doesNotMatch(r.values.join(' '),/每层/);
  assert.match(r.conditions.join(' '),/不应用于任何命中/); assert.match(r.evidence.join(' '),/手工声明.*本地校准记录/);
});

test('unknown and missing values never become zero or percentages', () => {
  const r=describe(coverage([effect('unknown','all',.5),{id:'missing',benefit_type_id:'benefit.dmg_bonus.cryo',domain:'self'}]));
  assert.match(r.values.join(' '),/0.5（单位未确认）/); assert.doesNotMatch(r.values.join(' '),/50%|0%/);
  assert.match(r.values.join(' '),/未完整提供/); assert.match(r.notes.join(' '),/不推断百分比/);
});

test('activation-only particle and direct energy rules have descriptive values', () => {
  const r=describe({kind:'activation',start:3,detail:{rule:{name_zh:'资源规则',owner:'b',effects:[],resources:[
    {kind:'particles',element:'cryo',particle_type:'particle',count:3,delay:.5,travel_time:1.2},
    {kind:'energy_direct',amounts:{a:6,b:2},delay:0}]}}});
  assert.match(r.title,/元素微粒生成.*直接回能/); assert.match(r.values.join(' '),/3 个元素微粒/);
  assert.match(r.values.join(' '),/甲角色 6 点、乙角色 2 点/); assert.doesNotMatch(r.effects.join(' '),/收益内容未提供/);
  assert.match(r.conditions.join(' '),/触发事件后 0.5 秒.*分别计时/);
});

test('attachment permission does not assert reaction damage and aura transitions stay explicit', () => {
  const event={kind:'condition',label:'附着判定',start:2,element:'cryo',detail:{type:'elemental_application',reason:'first_hit',allowed:true,application_gu:1,
    before:{ordinary_aura:null,frozen:null},after:{ordinary_aura:{element:'cryo',remaining_gu:.8},frozen:null}}};
  const r=describe(event); assert.match(r.effects.join(' '),/允许元素附着.*不代表必定发生反应/);
  assert.match(r.values.join(' '),/1 GU/); assert.match(r.conditions.join(' '),/冰底 0.8 GU/);
  event.detail={type:'reaction_damage_attempt',allowed:false};
  assert.match(describe(event).effects.join(' '),/反应伤害受冷却限制/);
});

test('application receipt explains component-specific counter identity, rule and reaction outcome', () => {
  const event={kind:'condition',label:'附着判定',start:2,detail:{type:'elemental_application',reason:'icd_blocked',
    application_allowed:false,reaction_occurred:false,profile_status:'confirmed',source:'固定版本机制资料',
    icd_audit:{mode:'counter',consumed:true,counter_tag:'normal-attack',rule_group:'default',
      shared_key:['a','normal-attack','enemy-1'],sequence_index:1,attempt_number:2,coefficient:0,
      sequence_length:24,anchor_t:1,reset_at:3.5,reset_seconds:2.5}}};
  const r=describe(event),conditions=r.conditions.join(' '),values=r.values.join(' ');
  assert.match(conditions,/甲角色.*计数标签「normal-attack」.*目标「enemy-1」独立计数/);
  assert.match(conditions,/规则组「default」只定义序列与重置规则/);
  assert.match(conditions,/当前轮起点 1 秒.*重置时刻 3.5 秒.*重置间隔 2.5 秒/);
  assert.match(values,/第 2 次尝试.*序列位置 2 \/ 24.*系数 0.*已推进/);
  assert.match(r.effects.join(' '),/未通过附着判定.*不会.*触发元素反应/);
  assert.match(r.evidence.join(' '),/已确认.*固定版本机制资料/);
});

test('unknown and no-ICD component profiles remain visibly distinct', () => {
  const unknown=describe({kind:'condition',detail:{type:'elemental_application',profile_status:'pending',
    icd_audit:{mode:'unknown',consumed:false}}});
  assert.match(unknown.conditions.join(' '),/具体攻击分量.*待核实.*计数标签.*规则组.*命中序列.*重置时间/);
  assert.match(unknown.evidence.join(' '),/待核实/);
  const none=describe({kind:'condition',detail:{type:'elemental_application',application_allowed:true,
    reaction_occurred:false,icd_audit:{mode:'none',consumed:false}}});
  assert.match(none.conditions.join(' '),/具体攻击分量.*无附着冷却.*不创建或推进/);
  assert.match(none.effects.join(' '),/允许元素附着.*没有发生元素反应.*命中前元素底/);
});

test('hit and reaction explain operation without promoting effects to automatic damage', () => {
  const hit=describe({kind:'hit',start:1,element:'cryo',detail:{input:{multiplier:1.25,scaling_stat:'atk',count:1,damage_type_ids:['damage.elemental_burst']},reported_damage:false}});
  assert.match(hit.values.join(' '),/125% × 攻击力/); assert.match(hit.notes.join(' '),/未纳入伤害合计/);
  const reaction=describe({kind:'reaction',start:2,element:'pyro',detail:{reaction:{type:'melt',factor:2}}});
  assert.match(reaction.effects.join(' '),/融化/); assert.match(reaction.values.join(' '),/×2（不是最终伤害倍率）/);
});

test('source evidence remains exact readable data, input stays unchanged and null is safe', () => {
  const event=coverage([effect('dmg_bonus')],{source:'KQM 原始测试：https://example.invalid/?q=rule.1',evidence_status:'assumed',
    trigger_action:{name_zh:'元素爆发',character:'b',start:0}});
  const before=JSON.stringify(event), r=describe(event);
  assert.match(r.source,/乙角色的元素爆发（0 秒）/); assert.match(r.evidence.join(' '),/KQM 原始测试：https:\/\/example.invalid\/\?q=rule.1/);
  assert.equal(JSON.stringify(event),before);
  for(const input of [undefined,null,{}, {kind:'coverage',detail:null}]) assert.equal(typeof describe(input).duration,'string');
});
