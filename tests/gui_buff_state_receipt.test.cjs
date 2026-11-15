const GUI_TEST_RUNTIME = require('./helpers/gui_runtime.cjs');
const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');
class Element {
  constructor(tag='div'){this.tagName=tag;this.children=[];this.listeners={};this.style={};this.attributes={};}
  append(...nodes){this.children.push(...nodes);}appendChild(node){this.append(node);return node;}
  insertRow(){return this.appendChild(new Element('tr'));}insertCell(){return this.appendChild(new Element('td'));}
  set innerHTML(value){this.children=[];this.text=value;}set textContent(value){this.children=[];this.text=String(value);}
  get textContent(){return(this.text||'')+this.children.map(n=>n.textContent).join('');}
  addEventListener(key,fn){this.listeners[key]=fn;}setAttribute(key,value){this.attributes[key]=String(value);}
}
const all=node=>[node,...node.children.flatMap(all)];
function setup(){const w={console,Date,Math};w.window=w;w.document={createElement:tag=>new Element(tag)};
  w.DS={prose:(value,fallback)=>value||fallback};w.RT={tpl:()=>({})};w.setStatus=()=>{};
  vm.createContext(w);for(const file of ['ui.js','timeline-data.js','result.js'])GUI_TEST_RUNTIME.loadWebFile(w, path.join(__dirname,'../src/genshin_app/gui/web',file));
  w.RS.state.snapshot={template:{loop_duration:12,characters:[{id:'a',name:'输出角色'},{id:'b',name:'辅助角色'}],buffs:[]}};
  w.RS.state.result={coverage_interpretation:{kind:'hit_projection'},hits:[],buff_state_timeline:{contract:'buff-state-timeline-v1',version:1,duration:12,
    states:[{id:'one',rule_id:'effect',name_zh:'有限增益',owner:'b',beneficiary:'a',instance_id:'instance',slices:[
      {start:1,end:3,status:'active',layers:1,remaining_quota:1,end_reason:'quota_change'},
      {start:3,end:9,status:'exhausted',layers:1,remaining_quota:0,end_reason:'natural_expiry'}]}],
    excluded:[{name_zh:'自身条件收益',message:'无独立持续时间'}],notes:[]}};return w;
}
test('result persistent status panel renders hit-projection calculation and navigable state rows',()=>{
  const w=setup(),before=JSON.stringify(w.RS.state.result),panel=w.RS.renderBuffStates(true);
  assert.match(panel.textContent,/持续 Buff 状态/);assert.match(panel.textContent,/规则有效/);assert.match(panel.textContent,/次数耗尽/);
  assert.match(panel.textContent,/辅助角色 · 有限增益/);assert.match(panel.textContent,/输出角色/);assert.match(panel.textContent,/无独立持续时间/);
  const buttons=all(panel).filter(n=>n.tagName==='button');assert.equal(buttons.length,2);let selected;
  w.RS.openBuffState=event=>selected=event;buttons[1].listeners.click();assert.equal(selected.detail.state_status,'exhausted');
  assert.equal(JSON.stringify(w.RS.state.result),before);
});
test('stale persistent panel hides prior intervals and offers current calculation',()=>{
  const w=setup();let invoked=0;w.RS.runCalc=()=>invoked++;
  const panel=w.RS.renderBuffStates(false);assert.match(panel.textContent,/结果已失效/);assert.doesNotMatch(panel.textContent,/有限增益|次数耗尽/);
  all(panel).find(n=>n.tagName==='button').listeners.click();assert.equal(invoked,1);
});
test('empty authoritative receipt explains excluded sources instead of rendering hit ratios as duration',()=>{
  const w=setup();w.RS.state.result.buff_state_timeline.states=[];w.RS.state.result.buffs_coverage=[{id:'hidden',uptime:.9}];
  const panel=w.RS.renderBuffStates(true);assert.match(panel.textContent,/没有可绘制的有限持续 Buff/);assert.match(panel.textContent,/未绘制的来源/);
  assert.doesNotMatch(panel.textContent,/90%|hidden/);assert.equal(all(panel).filter(n=>n.tagName==='button').length,0);
});
