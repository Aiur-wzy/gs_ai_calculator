const GUI_TEST_RUNTIME = require('./helpers/gui_runtime.cjs');
const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');
const web=path.join(__dirname,'../src/genshin_app/gui/web'),clone=value=>JSON.parse(JSON.stringify(value));
class Element {
  constructor(tag='div'){this.tagName=tag;this.children=[];this.attributes={};this.listeners={};this.style={setProperty(){}};this.value='';this.clientWidth=900;this.scrollLeft=0;this.scrollTop=0;this.classList={add(){},remove(){},toggle(){}};}
  append(...nodes){for(const n of nodes){n.parent=this;this.children.push(n);}}appendChild(node){this.append(node);return node;}
  set innerHTML(v){this.children=[];this.text=v;}set textContent(v){this.children=[];this.text=String(v);}get textContent(){return(this.text||'')+this.children.map(n=>n.textContent).join('');}
  setAttribute(key,value){this.attributes[key]=String(value);}addEventListener(key,value){(this.listeners[key]||=[]).push(value);}
  emit(key,extra={}){const e={target:this,button:0,pointerId:1,clientX:0,preventDefault(){},stopPropagation(){},...extra};return(this.listeners[key]||[]).map(fn=>fn(e)).at(-1);}
  getBoundingClientRect(){return{left:0,width:this.clientWidth};}setPointerCapture(){}releasePointerCapture(){}showModal(){}close(){}remove(){}
}
const all=node=>[node,...node.children.flatMap(all)];
function setup(events){const root=new Element(),body=new Element('body');root.id='tab-whiteboard';body.append(root);
  const w={console,Date,Math,crypto:{randomUUID:()=>String(Math.random())},addEventListener(){},RT:{state:{}},setStatus(){},callApi:async()=>({code:1})};w.window=w;
  w.document={body,createElement:tag=>new Element(tag),createElementNS:(_,tag)=>new Element(tag),querySelector:selector=>all(body).find(n=>n.id===selector.slice(1))||null,querySelectorAll:()=>[]};
  w.$=selector=>w.document.querySelector(selector);w.DS={raw:(name,value)=>{const n=new Element('details');n.textContent=name+JSON.stringify(value);return n;}};
  vm.createContext(w);for(const file of ['ui.js','timeline-data.js','whiteboard.js'])GUI_TEST_RUNTIME.loadWebFile(w, path.join(web,file), {filename:file});
  const team=[{id:'support',name:'辅助'},{id:'a',name:'队友甲'},{id:'b',name:'队友乙'}].map(c=>({...c,level:90,constellation:0,weapon:null,artifact_sets:[],panel:{atk:2000,dmg_bonus:{}}}));
  const WB=w.WB;WB.replace({board:{kind:'genshin-whiteboard',revision:0,title:'持续增益展示',version_line:'release',duration:20,team,actions:[],notes:''}});
  WB.state.result={plan:{},result:{summary:{total_damage:10,dps:.5}}};WB.state.resultProjectionOwner=WB.state.result;WB.state.resultProjection={views:{effects:{events},damage:{events:[]}}};WB.state.mode='effects';WB.render();
  return{WB,w,root,click:label=>{const button=all(root).find(n=>n.tagName==='button'&&n.textContent===label);assert.ok(button,label);button.emit('click');}};
}
function coverage(recipient='a',start=2,end=10,overrides={}){return{id:`coverage-${recipient}-${start}-${end}`,kind:'coverage',character:recipient,sourceKey:'support-rule',sourceLabel:'辅助 · 天赋增益',label:'天赋增益 · 规则有效',start,end,detail:{owner:'support',instance_id:'one',remaining_quota:null},...overrides};}
const point={id:'activation',kind:'activation',character:'support',sourceKey:'support-rule',sourceLabel:'辅助 · 天赋增益',label:'天赋增益 · 激活',start:2,detail:{}};

test('whiteboard shares display rows while retaining equal recipient records',()=>{
  const events=[coverage(),coverage('b'),point],original=clone(events),e=setup(events),projection=e.WB.project();
  assert.equal(projection.events.length,3);assert.equal(projection.lanes.length,2);
  assert.deepEqual(clone(projection.lanes.map(l=>l.kind)),['coverage','points']);
  const bar=projection.events.find(x=>x.kind==='coverage');assert.deepEqual(Array.from(projection.lanes[0].events,e=>e.character),['a','b']);
  assert.notEqual(bar.lane,projection.events.find(x=>x.kind==='activation').lane);assert.deepEqual(events,original);
});

test('filtering continuous buffs and triggers only changes view and keeps return package read-only',()=>{
  const e=setup([coverage(),coverage('b'),point]),before=e.WB.json(),result=JSON.stringify(e.WB.state.result);
  e.click('持续 Buff');assert.ok(e.WB.project().events.every(x=>x.kind==='coverage'));assert.equal(e.WB.project().events.length,2);
  assert.doesNotMatch(e.root.textContent,/添加动作时点|释放时点 \/ 秒|删除动作/);
  const g=all(e.WB.state.svg).find(n=>n.attributes.class==='wb-derived wb-coverage');assert.ok(g);
  g.emit('click');assert.equal(e.WB.state.selectedDerived.events.length,2);
  all(e.root).find(n=>n.className==='wb-detail-choice').emit('click');assert.match(e.root.textContent,/接收角色：队友甲/);assert.match(e.root.textContent,/区间长度：8 秒/);assert.match(e.root.textContent,/来源：辅助 · 天赋增益/);
  assert.match(g.attributes['aria-label'],/2 → 10 秒/);
  e.WB.beginPointer({button:0},'coverage-a-2-10');assert.equal(e.WB.state.pointer,undefined);
  e.click('命中与触发');assert.deepEqual(clone(e.WB.project().events.map(x=>x.id)),['activation']);assert.equal(e.WB.state.selectedDerived,null);
  e.click('全部效果');assert.equal(e.WB.project().events.length,3);assert.equal(e.WB.json(),before);assert.equal(e.WB.dirty(),false);assert.equal(JSON.stringify(e.WB.state.result),result);
});

test('overlapping windows share a row with exact pieces and retained gap',()=>{
  const e=setup([coverage('a',2,10),coverage('b',3,9),coverage('a',12,16)]),p=e.WB.project();
  assert.equal(p.events.length,3);assert.equal(p.lanes.length,1);
  assert.equal(p.events[0].lane,p.events[1].lane);assert.equal(p.events[0].lane,p.events[2].lane);
  assert.deepEqual(clone(p.events.map(x=>[x.start,x.end])),[[2,10],[3,9],[12,16]]);
  assert.equal(all(e.WB.state.svg).filter(n=>n.attributes.class==='wb-derived wb-coverage').length,4);
});

test('quota states at identical time remain separately selectable',()=>{
  const e=setup([coverage('a',2,10,{detail:{owner:'support',instance_id:'one',remaining_quota:2}}),coverage('b',2,10,{detail:{owner:'support',instance_id:'one',remaining_quota:5}})]),p=e.WB.project();
  assert.equal(p.events.length,2);assert.equal(p.events[0].lane,p.events[1].lane);
  assert.deepEqual(clone(p.events.map(x=>x.detail.remaining_quota)),[2,5]);
});

test('instantaneous conditional effects do not acquire a guessed duration',()=>{
  const condition={...point,id:'condition',kind:'condition',label:'自身条件判定'},e=setup([condition]);
  assert.equal(all(e.WB.state.svg).filter(n=>n.attributes.class==='wb-derived wb-coverage').length,0);
  e.click('持续 Buff');assert.equal(e.WB.project().events.length,0);e.click('命中与触发');assert.equal(e.WB.project().events[0].end,undefined);
});

test('dense trigger lists cannot hide continuous buff bars behind render cap',()=>{
  const e=setup([...Array.from({length:900},(_,i)=>({...point,id:`point-${i}`,start:1})),coverage()]);
  assert.equal(all(e.WB.state.svg).filter(n=>n.attributes.class==='wb-derived wb-coverage').length,1);
  e.click('持续 Buff');assert.doesNotMatch(e.root.textContent,/先显示 800 项/);assert.equal(e.WB.project().events.length,1);
});

test('derived left press keeps the node for native click; middle press can bubble to pan',()=>{
  const e=setup([coverage(),point]);
  for(const node of all(e.WB.state.svg).filter(n=>n.attributes.class?.startsWith('wb-derived'))){
    let stopped=false,prevented=false;e.WB.state.dragMoved=true;
    node.emit('pointerdown',{button:0,stopPropagation(){stopped=true;},preventDefault(){prevented=true;}});
    assert.equal(stopped,true);assert.equal(prevented,false);assert.equal(e.WB.state.dragMoved,false);assert.equal(e.WB.state.pointer,undefined);
    node.emit('click');assert.ok(e.WB.state.selectedDerived);
    stopped=false;node.emit('pointerdown',{button:1,stopPropagation(){stopped=true;}});assert.equal(stopped,false);
  }
});


test('dense nearby effect points remain individually accessible through bounded detail pages',()=>{
  const events=Array.from({length:91},(_,i)=>({...point,id:`near-${i}`,label:`触发 ${i}`,start:2+i*.0001})),e=setup(events),before=e.WB.json();
  const markers=all(e.WB.state.svg).filter(n=>n.attributes.class==='wb-derived');assert.equal(markers.length,1);assert.match(markers[0].attributes['aria-label'],/91 条记录/);
  markers[0].emit('click');assert.equal(e.WB.state.selectedDerived.events.length,91);
  assert.equal(all(e.root).filter(n=>n.className==='wb-detail-choice').length,40);e.click('下一页');assert.equal(all(e.root).filter(n=>n.className==='wb-detail-choice').length,40);
  const selected=all(e.root).find(n=>n.className==='wb-detail-choice');selected.emit('click');assert.equal(e.WB.state.selectedDerived.id,'near-40');e.click('← 返回相邻记录');assert.equal(e.WB.state.selectedDerived.events.length,91);
  assert.equal(e.WB.json(),before);assert.equal(e.WB.dirty(),false);
});

test('refreshed coverage distinguishes total interval from individual activation expiry',()=>{
  const e=setup([coverage('a',2,18,{detail:{owner:'support',instance_id:'refresh',states:[{start:2,rule_expires_at:10,activation_trace_index:0},{start:8,rule_expires_at:18,activation_trace_index:1}]}})]);
  const g=all(e.WB.state.svg).find(n=>n.attributes.class==='wb-derived wb-coverage');g.emit('click');assert.match(e.root.textContent,/不是单次技能持续时间/);assert.match(e.root.textContent,/规则到期 10 秒/);assert.match(e.root.textContent,/规则到期 18 秒/);
});

test('engine exhausted state renders gray, explains counts and remains read-only',()=>{
  const e=setup([coverage('a',3,10,{label:'天赋增益 · 次数耗尽',detail:{owner:'support',instance_id:'receipt',receipt_id:'state',state_status:'exhausted',
    remaining_quota:0,layers:1,end_reason:'natural_expiry',states:[{start:3,end:10,status:'exhausted',remaining_quota:0,layers:1,end_reason:'natural_expiry'}]}})]),before=e.WB.json();
  const g=all(e.WB.state.svg).find(n=>n.attributes.class==='wb-derived wb-coverage');
  assert.ok(all(g).some(n=>n.tagName==='rect'&&n.attributes.fill==='#89919c'));g.emit('click');
  assert.match(e.root.textContent,/次数耗尽/);assert.match(e.root.textContent,/自然到期/);assert.match(e.root.textContent,/剩余 0 次/);
  assert.doesNotMatch(e.root.textContent,/释放时点 \/ 秒/);assert.equal(e.WB.json(),before);
});
