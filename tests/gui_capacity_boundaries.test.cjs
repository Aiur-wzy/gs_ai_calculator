'use strict';
const test=require('node:test'),assert=require('node:assert/strict'),vm=require('node:vm');
const runtime=require('./helpers/gui_runtime.cjs');
class Element{
  constructor(tag='div'){this.tagName=tag;this.children=[];this.attributes={};this.listeners={};}
  append(...nodes){this.children.push(...nodes);}set textContent(value){this.text=String(value);this.children=[];}
  get textContent(){return(this.text||'')+this.children.map(n=>n.textContent).join('');}
  set innerHTML(value){this.text=value;this.children=[];}setAttribute(k,v){this.attributes[k]=v;}
  addEventListener(k,fn){this.listeners[k]=fn;}remove(){}
}
const all=node=>[node,...node.children.flatMap(all)];
const visible=node=>node.className==='display-raw'?'':(node.text||'')+node.children.map(visible).join('\n');
function setup(){const world={document:{createElement:tag=>new Element(tag)},console};world.window=world;
  world.DS={character:id=>id||'角色未记录',raw:(title,value)=>{const n=new Element('details');n.className='display-raw';
    const pre=new Element('pre');pre.textContent=JSON.stringify(value,null,2);n.append(pre);return n;}};
  world.RC={owner:id=>id};vm.createContext(world);runtime.loadWebScripts(world,['action-capacities.js']);return world.CAP;}
function source(){return {id:'cap',status:'ready',projection:{capacity:2,pool:{actor:'a',group:'e',initial:{ready_in:[5]},
  members:[{cooldown:{duration:10,start:'action_start',recovery:'sequential'}}]},profile:{model_record:{id:'model',revision:1,
  payload:{claim:{base:{value:1},delta_refs:[{id:'delta',revision:1}]}}},competitor_inventory:{entries:[{id:'delta',revision:1,
  status:'ready',disposition:'model_dependency_active',projection:{amount:1},record:{payload:{claim:{activation:{rank:1}}}}}]}}}};}
function preview(CAP,record){const box=new Element();CAP.preview(box,record,{});return box;}
const receipt=(overrides={})=>({t:0,character:'a',action_id:'a1',ability_id:'e',binding_id:'cap',status:'committed',
  capacity:2,before:2,after:1,queue_before:[],queue_after:[10],ready_at:10,next_ready_at:10,...overrides});
function detail(CAP,row,kind='receipt'){return CAP.entryDetail({kind,row,key:'test'}, {},new Map());}

test('capacity display never treats prototype names, nonstrings, or another status domain as labels',()=>{
  const CAP=setup();for(const value of ['toString','constructor','__proto__','hasOwnProperty',['committed'],{},true,'ready']){
    const box=detail(CAP,receipt({status:value}));assert.match(visible(box),/待核实/);assert.doesNotMatch(visible(box),/function|native code|\[object Object\]|已确认/);
  }
  for(const value of ['toString','constructor','__proto__','hasOwnProperty',['tap']]){
    const text=CAP.conditionText({variant:value},{}).join('\n');assert.match(text,/动作变体待核实/);assert.doesNotMatch(text,/function|native code|\[object Object\]/);
  }
  const r=source();r.status='committed';assert.doesNotMatch(visible(preview(CAP,r)),/技能次数来源 · 已消费/);
  assert.doesNotMatch(visible(detail(CAP,{capacity_admission:{status:'ready'}},'parent')),/已确认/);
});
test('missing or unqualified receipt values never invent consumption or availability',()=>{
  const CAP=setup();for(const status of ['future_state','committed','rejected']){
    const row={status},box=detail(CAP,row),text=visible(box);assert.doesNotMatch(text,/undefined|当前可用|未消费/);assert.match(text,/待核实|未记录/);
    assert.equal(all(box).find(n=>n.tagName==='pre').textContent,JSON.stringify(row,null,2));
  }
  const text=visible(detail(CAP,receipt({status:'future_state',ready_at:null,next_ready_at:null})));
  assert.doesNotMatch(text,/未消费|当前可用/);
});
test('invalid initial queues and capacities do not produce numeric opening availability',()=>{
  const CAP=setup();for(const [capacity,queue] of [[2,[5,6,7]],[2,[6,5]],[2,[-1]],[2,[true]],[2,['5']],[0,[]],[true,[]],[1.5,[]],[101,[]]]){
    const r=source();r.projection.capacity=capacity;r.projection.pool.initial.ready_in=queue;const before=JSON.stringify(r),box=preview(CAP,r);
    assert.doesNotMatch(visible(box),/开场可用 -?\d+(\.\d+)? 次/);assert.match(visible(box),/待核实/);assert.equal(JSON.stringify(r),before);
    assert.equal(all(box).filter(n=>n.tagName==='pre').at(-1).textContent,JSON.stringify(r,null,2));
  }
});
test('inactive nonzero and active zero deltas report inconsistent status rather than applied gain',()=>{
  const CAP=setup();for(const [disposition,amount] of [['model_dependency_inactive',1],['model_dependency_active',0]]){
    const r=source(),entry=r.projection.profile.competitor_inventory.entries[0];entry.disposition=disposition;entry.projection.amount=amount;
    const text=visible(preview(CAP,r));assert.match(text,/不一致|待核实/);assert.doesNotMatch(text,/本次增加 [01] 次/);
  }
});
test('delta and constellation display require integer values inside the contract range',()=>{
  const CAP=setup();for(const amount of [1.5,true,-1,100,'1']){
    const r=source();r.projection.profile.competitor_inventory.entries[0].projection.amount=amount;
    assert.match(visible(preview(CAP,r)),/本次增加 待核实 次/);
  }
  for(const rank of [true,1.5,0,7,'1']){
    const r=source();r.projection.profile.competitor_inventory.entries[0].record.payload.claim.activation.rank=rank;
    const text=visible(preview(CAP,r));assert.doesNotMatch(text,/来源条件为第/);assert.match(text,/激活条件待核实/);
  }
});
test('valid explicit numbers, inactive zero, and qualified null receipt times retain their meanings',()=>{
  const CAP=setup(),r=source();assert.match(visible(preview(CAP,r)),/开场可用 1 次/);assert.match(visible(preview(CAP,r)),/本次增加 1 次/);
  r.projection.pool.initial.ready_in=[];assert.match(visible(preview(CAP,r)),/开场可用 2 次/);
  const entry=r.projection.profile.competitor_inventory.entries[0];entry.disposition='model_dependency_inactive';entry.projection.amount=0;
  assert.match(visible(preview(CAP,r)),/本次未激活；本次增加 0 次/);
  const row=receipt({status:'rejected',before:0,after:0,queue_before:[5,10],queue_after:[5,10],ready_at:null,next_ready_at:5});
  assert.match(visible(detail(CAP,row)),/0 → 0 次/);assert.match(visible(detail(CAP,row)),/本次恢复时刻 未消费/);
});
test('prototype-named statuses remain accessible in ledger filters without inherited labels',()=>{
  const CAP=setup(),box=new Element();CAP.ledger(box,{action_capacity_consumption:{receipts:[receipt({status:'__proto__'})]}},{});
  const options=all(box).filter(n=>n.tagName==='option');assert.ok(options.some(n=>n.value==='"__proto__"'));
  assert.ok(options.filter(n=>n.value==='"__proto__"').every(n=>/待核实/.test(n.textContent)));
  assert.doesNotMatch(visible(box),/function|\[object Object\]/);assert.equal(all(box).filter(n=>['input','textarea'].includes(n.tagName)).length,0);
});
