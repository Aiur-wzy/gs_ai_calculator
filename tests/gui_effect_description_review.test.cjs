const GUI_TEST_RUNTIME = require('./helpers/gui_runtime.cjs');
// Independent semantic/DOM checks. Fixtures use public receipt shapes and do
// not depend on retained experiment outputs or a particular character ID.
const test=require('node:test'),assert=require('node:assert/strict');
const fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');
const web=path.join(__dirname,'../src/genshin_app/gui/web');
const copy=value=>JSON.parse(JSON.stringify(value));
const all=node=>[node,...node.children.flatMap(all)];
class Element {
  constructor(tag='div'){this.tagName=tag;this.children=[];this.attributes={};this.listeners={};this.dataset={};this.style={};
    this.classList={add(){},remove(){},toggle(){},contains(){return false;}};}
  append(...nodes){for(const node of nodes){node.parent=this;this.children.push(node);}}
  appendChild(node){this.append(node);return node;}
  set textContent(value){this.children=[];this.text=String(value);}get textContent(){return(this.text||'')+this.children.map(n=>n.textContent).join('');}
  set innerHTML(value){this.children=[];this.text=String(value);}
  setAttribute(key,value){this.attributes[key]=String(value);}
  addEventListener(key,fn){(this.listeners[key]||=[]).push(fn);}
}
function setup(extra=[]){
  const nodes=new Map(),get=selector=>{if(!nodes.has(selector))nodes.set(selector,new Element());return nodes.get(selector);};
  const w={console,addEventListener(){},setStatus(){},$:get,DV:{refreshList(){},renderDetail(){}}};w.window=w;
  w.document={createElement:tag=>new Element(tag),createElementNS:(_,tag)=>new Element(tag),querySelector:get,querySelectorAll:()=>[]};
  vm.createContext(w);
  for(const name of ['display','effect-description','timeline-data',...extra])
    GUI_TEST_RUNTIME.loadWebFile(w, path.join(web,`${name}.js`), {filename:`${name}.js`});
  return{w,get};
}
const characters=[{id:'support',name:'辅助角色'},{id:'lead',name:'输出角色'}];
const names=id=>characters.find(c=>c.id===id)?.name||'未指定角色';
function savedState(id,effects,end=12){return{
  id:`state:${id}`,origin:'mechanics_rule',rule_id:id,source_id:`source:${id}`,owner:'support',
  name_zh:'同名领域效果',source:'KQM 机制记录：减抗续留 2 秒；绝对起点仍为情景假设。',evidence_status:'assumed',
  instance_id:`instance:${id}`,beneficiary:'lead',beneficiaries:['lead'],
  beneficiaries_scope:{mode:'characters',characters:['lead'],scope:'current_frontline'},
  trigger:{event:'action_start',phase:'post',characters:['support'],action_type_ids:['action.elemental_burst']},
  effects,start:0.6,end:end+0.6,slices:[{start:0.6,end:end+0.6,status:'active',layers:1,remaining_quota:null,
    activation_time:0.6,activation_trace_index:0,activation_event_id:'cast',rule_expires_at:end+0.6,end_reason:'natural_expiry'}]
};}
function savedFixture(){return{template:{loop_duration:24,characters,hits:[],buffs:[]},current:true,result:{
  buff_state_timeline:{contract:'buff-state-timeline-v1',version:1,duration:24,states:[
    savedState('resistance',[{id:'cryo',benefit_type_id:'benefit.res_shred.cryo',domain:'enemy',value:0.15},
      {id:'physical',benefit_type_id:'benefit.res_shred.physical',domain:'enemy',value:0.15}],14),
    savedState('damage',[{id:'cryo',benefit_type_id:'benefit.dmg_bonus.cryo',domain:'self',value:0.15}])],notes:[],excluded:[]}
}};}
const text=description=>JSON.stringify(description);
test('equipment reference panel and effect explanations keep distinct global namespaces',()=>{
  const{w}=setup(['equipment-data']);
  assert.equal(w.ED.category,'character');assert.equal(typeof w.ED.unit,'function');
  assert.equal(typeof w.EFX.describe,'function');
  assert.notEqual(w.EFX,w.ED);
});
test('standalone saved receipt distinguishes identically named benefits and retains source evidence',()=>{
  const{w}=setup(),fixture=savedFixture(),before=copy(fixture),projection=w.TD.build(fixture);
  const bars=projection.views.effects.events.filter(e=>e.kind==='coverage');assert.equal(bars.length,2);
  assert.notEqual(bars[0].label,bars[1].label);assert.deepEqual(fixture,before);
  const descriptions=bars.map(event=>w.EFX.describe(event,{characterName:names}));
  assert.match(text(descriptions[0]),/减抗|抗性/);assert.match(text(descriptions[1]),/冰.*伤|冰.*增伤/);
  for(let i=0;i<bars.length;i++){
    assert.ok(bars[i].detail.effects.length);assert.match(bars[i].detail.source,/KQM/);
    assert.match(text(descriptions[i]),/15%/);assert.match(text(descriptions[i]),/KQM/);
  }
  assert.match(text(descriptions[0].duration),/14/);assert.match(text(descriptions[1].duration),/12/);
});
test('legacy execution trace enriches activation and quota points with exact rule effects and origin action',()=>{
  const{w}=setup(),rule={id:'limited',owner:'support',name_zh:'共享基础区加成',source:'本地原文与机制校准',status:'assumed',
    source_id:'manual:limited',beneficiaries:{mode:'team',scope:'locked'},timing:{delay:0,duration:10},
    trigger:{event:'action_start',phase:'post',action_ids:['cast']},
    effects:[{id:'flat',benefit_type_id:'benefit.base_add.all',domain:'provider',value:100}]};
  const plan={plan_version:3,mechanics_model:{rules:[rule]},rotation_model:{period:20,
    abilities:[{id:'skill',character:'support',name_zh:'辅助战技',action_type_id:'action.elemental_skill'}],
    actions:[{id:'cast',ability_id:'skill',start:1,occupation_end:1.5}]}};
  const result={mechanics_trace:[
    {type:'activation',t:1,rule_id:'limited',instance_id:'instance',event_id:'cast',beneficiaries:['lead'],expires_at:[11],quota:{lead:2}},
    {type:'quota',t:2,rule_id:'limited',instance_id:'instance',event_id:'hit',pool:'lead',before:2,after:1,consumed:true}],
    rotation_check:{event_log:[]}};
  const projection=w.TD.build({template:{loop_duration:20,characters,hits:[],buffs:[]},plan,result,current:true});
  for(const kind of ['activation','quota']){
    const point=projection.views.effects.events.find(e=>e.kind===kind);assert.ok(point,kind);
    assert.equal(point.detail.effects[0].value,100);assert.equal(point.detail.source,rule.source);
    const description=w.EFX.describe(point,{characterName:names});assert.match(text(description),/100/);
    assert.match(text(description),/本地原文/);
  }
  const activation=projection.views.effects.events.find(e=>e.kind==='activation');
  assert.equal(activation.detail.trigger_action.id,'cast');
  assert.match(text(w.EFX.describe(activation,{characterName:names}).conditions),/实例接收角色：输出角色/);
  assert.doesNotMatch(text(w.EFX.describe(activation,{characterName:names}).conditions),/实例接收角色：辅助角色/);
});
test('a refreshed interval reports continuous span separately from individual duration',()=>{
  const{w}=setup(),fixture=savedFixture(),state=fixture.result.buff_state_timeline.states[1];
  state.end=18;state.slices=[
    {start:0.6,end:6,status:'active',layers:1,activation_time:0.6,activation_trace_index:0,rule_expires_at:12.6,end_reason:'refresh'},
    {start:6,end:18,status:'active',layers:1,activation_time:6,activation_trace_index:4,rule_expires_at:18,end_reason:'natural_expiry'}];
  fixture.result.buff_state_timeline.states=[state];
  const event=w.TD.build(fixture).views.effects.events.find(e=>e.kind==='coverage');
  assert.equal(event.end-event.start,17.4);
  const description=w.EFX.describe(event,{characterName:names});
  assert.match(text(description.duration),/17\.4/);assert.match(text(description),/刷新/);
  assert.match(text(description),/12\.6/);assert.match(text(description.duration),/不是单次/);
});
test('flat values and unknown values are not formatted as percentage or silently converted to zero',()=>{
  const{w}=setup(),state=savedState('unknown',[{id:'flat',benefit_type_id:'benefit.base_add.all',domain:'provider',value:100}]);
  const event={kind:'coverage',label:'基础区加成',sourceLabel:'辅助角色 · 基础区加成',character:'lead',start:1,end:11,
    detail:{...state,layers:1,states:state.slices}};
  const flat=w.EFX.describe(event,{characterName:names});assert.match(text(flat.values),/100/);assert.doesNotMatch(text(flat.values),/100%/);
  delete event.detail.effects[0].value;const unknown=w.EFX.describe(event,{characterName:names});
  assert.match(text(unknown.values),/未(?:完整)?提供|未知|待.*核|缺少/);assert.doesNotMatch(text(unknown.values),/0%|[+＋]0(?:\D|$)/);
});
test('formula and actual sample keep provider identity, sample time and occurrence time separate',()=>{
  const{w}=setup(),state=savedState('formula',[{id:'flat',benefit_type_id:'benefit.base_add.all',domain:'provider',
    value:{kind:'stat',subject:'owner',stat:'atk',factor:0.8,add:5,sample:'trigger'}}]);
  const event={kind:'coverage',label:'属性比例收益',character:'lead',start:1,end:11,detail:{...state,layers:1,
    applied_value_count:1,applied_values:[{effect_id:'flat',value:1605,layers:1,sample_t:2,t:7,event_id:'hit',character:'lead'}]}};
  const description=w.EFX.describe(event,{characterName:names});
  assert.match(text(description),/辅助角色/);assert.match(text(description),/攻击力/);
  assert.match(text(description),/80%|0\.8/);assert.match(text(description),/1605|1,605/);
  assert.match(text(description),/触发/);assert.match(text(description.samples),/7 秒命中.*2 秒的收益状态/);
  assert.match(text(description.values),/投影值.*1605/);assert.match(text(description.values),/投影不等于该次伤害公式实际使用/);
  assert.doesNotMatch(text(description.values),/实际取值/);
  assert.doesNotMatch(text(description),/仅提供者本人受益|只给提供者本人/);
});
test('attachment permission is not described as permission to deal reaction damage',()=>{
  const{w}=setup(),description=w.EFX.describe({kind:'condition',character:'support',start:2,label:'附着：首次附着',detail:{
    type:'elemental_application',reason:'first_hit',allowed:true,application_gu:1,element:'cryo',
    before:{ordinary_aura:null},after:{ordinary_aura:{element:'cryo',remaining_gu:0.8}}}},{characterName:names});
  assert.match(text(description),/附着/);assert.doesNotMatch(text(description.effects),/允许产生反应伤害/);
  assert.match(text(description.values),/1.*GU/);assert.match(text(description),/0\.8.*GU/);
});
test('quota before and after are counts, never elemental gauge snapshots',()=>{
  const{w}=setup(),description=w.EFX.describe({kind:'quota',start:3,character:'lead',label:'次数消费',detail:{
    before:2,after:1,consumed:true,effects:[{id:'flat',benefit_type_id:'benefit.base_add.all',domain:'provider',value:100}]}},{characterName:names});
  assert.match(text(description.values),/2.*→.*1/);assert.doesNotMatch(text(description.conditions),/事件前元素底|事件后元素底/);
});
test('unknown aura knowledge cannot be presented as proof of an empty aura',()=>{
  const{w}=setup(),description=w.EFX.describe({kind:'condition',start:4,label:'元素底判定',detail:{
    before:{knowledge:'unknown',ordinary_aura:null,frozen:null},
    after:{knowledge:'unknown',ordinary_aura:null,frozen:null}}},{characterName:names});
  assert.match(text(description.conditions),/未知|未.*核|不明确/);
  assert.doesNotMatch(text(description.conditions),/无普通元素底|无冻结量/);
});
test('both workspaces display the same four essential facts outside raw-data disclosure',()=>{
  const{w,get}=setup(['ui','rotation','timeline-view','timeline','whiteboard']);
  const event=w.TD.build(savedFixture()).views.effects.events.find(e=>e.kind==='coverage');
  const original=w.DS.renderEffectDescription,calls=[];
  w.DS.renderEffectDescription=description=>{calls.push(copy(description));return original(description);};
  w.TV.mode='effects';w.TV.characterNames=new Map(characters.map(c=>[c.id,c.name]));w.TV.updateDetailSelection=()=>{};
  w.TV.showDetail(event);
  w.WB.state.mode='effects';w.WB.state.board={team:characters,actions:[]};w.WB.state.selectedDerived=event;
  const whiteboard=new Element();w.WB.renderDerived(whiteboard);
  assert.equal(calls.length,2);assert.deepEqual(calls[0],calls[1]);
  for(const root of [get('#timeline-detail'),whiteboard]){
    const section=all(root).find(node=>node.className==='effect-description');assert.ok(section);
    const labels=all(section).filter(node=>node.tagName==='dt').map(node=>node.textContent);
    assert.deepEqual(labels,['来源','效果','持续','数值']);
    for(let parent=section.parent;parent;parent=parent.parent)assert.notEqual(parent.tagName,'details');
    assert.match(section.textContent,/15%/);assert.match(section.textContent,/KQM/);
  }
});
test('dynamic projected samples fold separately while the four facts and numeric formula stay visible',()=>{
  const{w}=setup(),description=w.EFX.describe({kind:'coverage',start:1,end:11,character:'lead',detail:{owner:'support',layers:2,
    effects:[{id:'flat',benefit_type_id:'benefit.base_add.all',domain:'provider',
      value:{kind:'stat',subject:'owner',stat:'atk',factor:0.8,sample:'current'}}],
    applied_values:[{effect_id:'flat',value:3200,layers:2,t:7,sample_t:2,character:'lead'}]}},{characterName:names});
  const section=w.DS.renderEffectDescription(description),facts=all(section).find(n=>n.className==='effect-description-facts');
  assert.deepEqual(all(facts).filter(n=>n.tagName==='dt').map(n=>n.textContent),['来源','效果','持续','数值']);
  assert.match(facts.textContent,/攻击力.*0\.8/);assert.match(facts.textContent,/投影值.*3200/);
  assert.match(facts.textContent,/投影不等于该次伤害公式实际使用/);
  const fold=all(section).find(n=>n.tagName==='details');assert.ok(fold);assert.notEqual(fold.open,true);
  assert.match(fold.textContent,/7 秒命中.*2 秒的收益状态/);assert.match(fold.textContent,/是否进入该次伤害公式需查看伤害明细/);
  assert.doesNotMatch(facts.textContent,/7 秒命中/);
  for(let parent=facts.parent;parent;parent=parent.parent)assert.notEqual(parent.tagName,'details');
});
test('unchanged single-layer declarations omit duplicate samples but retain their visible value',()=>{
  const{w}=setup(),description=w.EFX.describe({kind:'coverage',start:1,end:11,character:'lead',detail:{owner:'support',layers:1,
    effects:[{id:'res',benefit_type_id:'benefit.res_shred.physical',domain:'enemy',value:0.15}],
    applied_values:[{effect_id:'res',value:0.15,layers:1,t:7,sample_t:7,character:'lead'}]}},{characterName:names});
  assert.equal(description.samples.length,0);assert.match(text(description.values),/15%/);
  assert.doesNotMatch(text(description.values),/投影值/);
  assert.equal(all(w.DS.renderEffectDescription(description)).filter(n=>n.tagName==='details').length,0);
});
