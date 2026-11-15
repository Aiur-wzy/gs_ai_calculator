const test=require('node:test');
const assert=require('node:assert/strict');
const path=require('node:path');
const vm=require('node:vm');
const R=require('./helpers/gui_runtime.cjs');

class ClassList{constructor(){this.values=new Set();}add(...v){v.forEach(x=>this.values.add(x));}remove(...v){v.forEach(x=>this.values.delete(x));}toggle(v,on){if(on===undefined)on=!this.values.has(v);on?this.values.add(v):this.values.delete(v);return on;}contains(v){return this.values.has(v);}}
class E{
  constructor(tag='div'){this.tagName=tag.toUpperCase();this.children=[];this.dataset={};this.listeners={};this.classList=new ClassList();this.hidden=false;this.parentNode=null;this._text='';}
  append(...values){for(const value of values){value.parentNode=this;this.children.push(value);}}
  replaceChildren(...values){for(const child of this.children)child.parentNode=null;this.children=[];this._text='';this.append(...values);}
  remove(){if(!this.parentNode)return;const rows=this.parentNode.children,index=rows.indexOf(this);if(index>=0)rows.splice(index,1);this.parentNode=null;}
  set textContent(value){this._text=String(value);this.children=[];}
  get textContent(){return this._text+this.children.map(child=>child.textContent||'').join('');}
  addEventListener(name,listener){this.listeners[name]=listener;}
  emit(name,event={}){return this.listeners[name]?.({preventDefault(){},...event});}
}
class Storage{constructor(value=null){this.value=value;}getItem(){return this.value;}setItem(_key,value){this.value=value;}}
function harness(stored=null){
  let now=1_000,ticket=0;const timers=new Map();
  const window={console,window:null,Date,localStorage:new Storage(stored)};window.window=window;
  const context=vm.createContext(window);R.loadWebFile(context,path.join(__dirname,'../src/genshin_app/gui/web/agent-activity-feedback.js'));
  const document={createElement:tag=>new E(tag)},host=new E(),live=new E(),toasts=new E(),opened=[],preferences=[];
  const feedback=window.AgentActivityFeedback.create({document,host,liveHost:live,toastHost:toasts,storage:window.localStorage,clock:()=>now,setTimer:(callback,delay)=>{const id=++ticket;timers.set(id,{at:now+delay,callback});return id;},clearTimer:id=>timers.delete(id),onOpenSession:id=>opened.push(id),onPreferences:value=>preferences.push({...value})});
  const advance=milliseconds=>{const target=now+milliseconds;while(true){const next=[...timers.entries()].sort((a,b)=>a[1].at-b[1].at)[0];if(!next||next[1].at>target)break;timers.delete(next[0]);now=next[1].at;next[1].callback();}now=target;};
  return {window,host,live,toasts,feedback,advance,opened,preferences,storage:window.localStorage,now:()=>now,timers};
}

test('activity animation is delayed 250ms and reports measured elapsed, phase and cursor',()=>{
  const h=harness();
  h.feedback.observeActivity({session_id:'chat-a',state:'streaming',phase:'provider_stream',label:'正在生成回复',cursor:12,active:true});
  h.advance(249);assert.equal(h.host.hidden,true);assert.equal(h.host.textContent,'');
  h.advance(1);assert.equal(h.host.hidden,false);assert.match(h.host.textContent,/本轮已用 0秒/);assert.match(h.host.textContent,/当前阶段 0秒/);assert.match(h.host.textContent,/同步位置 #12/);assert.doesNotMatch(h.host.textContent,/%|流位置|cursor/i);
  h.advance(2000);assert.match(h.host.textContent,/本轮已用 2秒/);
  h.feedback.observeActivity({session_id:'chat-a',state:'using_tool',phase:'gs_calculate',label:'正在运行计算工具',cursor:19,active:true});
  h.advance(1000);assert.match(h.host.textContent,/本轮已用 3秒/);assert.match(h.host.textContent,/当前阶段 1秒/);assert.match(h.host.textContent,/同步位置 #19/);
});

test('short work never flashes and terminal clears ticking state',()=>{
  const h=harness();
  h.feedback.observeActivity({session_id:'chat-a',state:'preparing',active:true,cursor:0});
  h.advance(100);h.feedback.observeActivity({session_id:'chat-a',state:'completed',active:false,cursor:1});h.advance(500);
  assert.equal(h.host.hidden,true);assert.equal(h.timers.size,0);assert.equal(h.feedback.snapshot().activity,null);
});

test('transport success is shown during reconnect without pretending a percentage',()=>{
  const h=harness();h.feedback.markTransportSuccess('chat-a',1000);
  h.feedback.observeActivity({session_id:'chat-a',state:'reconnecting',phase:'event_stream',label:'正在重新连接',active:true,cursor:22});h.advance(250);
  assert.match(h.host.textContent,/最近成功/);assert.match(h.host.textContent,/同步位置 #22/);assert.doesNotMatch(h.host.textContent,/%|流位置|cursor/i);
});

test('aria-live phase announcements are throttled while visual elapsed time continues',()=>{
  const h=harness();h.feedback.observeActivity({session_id:'chat-a',state:'streaming',phase:'provider',label:'正在生成',active:true,cursor:1});h.advance(250);
  h.feedback.observeActivity({session_id:'chat-a',state:'using_tool',phase:'tool:first',label:'正在运行第一个工具',active:true,cursor:2});assert.equal(h.live.textContent,'正在运行第一个工具');
  h.advance(100);h.feedback.observeActivity({session_id:'chat-a',state:'using_tool',phase:'tool:second',label:'正在运行第二个工具',active:true,cursor:3});assert.equal(h.live.textContent,'正在运行第一个工具');
  h.advance(649);assert.equal(h.live.textContent,'正在运行第一个工具');h.advance(1);assert.equal(h.live.textContent,'正在运行第二个工具');assert.match(h.host.textContent,/同步位置 #3/);assert.doesNotMatch(h.host.textContent,/流位置|cursor/i);
});

test('background completion failure and user wait use bounded in-App notifications',()=>{
  const h=harness();
  h.feedback.observeSession({session_id:'one',state:'running',title:'任务一',selected:false,panel_open:false});
  assert.equal(h.feedback.observeSession({session_id:'one',state:'completed',title:'任务一',selected:false,panel_open:false}),true);
  h.feedback.observeSession({session_id:'two',state:'running',title:'任务二',selected:false,panel_open:false});
  assert.equal(h.feedback.observeSession({session_id:'two',state:'failed',title:'任务二',selected:false,panel_open:false}),true);
  h.feedback.observeSession({session_id:'three',state:'running',title:'任务三',selected:false,panel_open:false});
  assert.equal(h.feedback.observeSession({session_id:'three',state:'running',title:'任务三',question_open:true,selected:false,panel_open:false}),true);
  h.feedback.observeSession({session_id:'four',state:'running',title:'任务四',selected:false,panel_open:false});
  h.feedback.observeSession({session_id:'four',state:'completed',title:'任务四',selected:false,panel_open:false});
  assert.equal(h.toasts.children.length,3);assert.equal(h.toasts.children[0].dataset.sessionId,'two');
  h.toasts.children[0].emit('click');assert.deepEqual(h.opened,['two']);
});

test('selected visible session does not notify and policy can limit or disable notifications',()=>{
  const h=harness();
  h.feedback.observeSession({session_id:'one',state:'running',selected:true,panel_open:true});
  h.feedback.observeSession({session_id:'one',state:'completed',selected:true,panel_open:true});assert.equal(h.toasts.children.length,0);
  assert.equal(h.feedback.setPreference('notifications','important'),true);
  h.feedback.observeSession({session_id:'two',state:'running',selected:false,panel_open:false});h.feedback.observeSession({session_id:'two',state:'completed',selected:false,panel_open:false});assert.equal(h.toasts.children.length,0);
  h.feedback.observeSession({session_id:'three',state:'running',selected:false,panel_open:false});h.feedback.observeSession({session_id:'three',state:'failed',selected:false,panel_open:false});assert.equal(h.toasts.children.length,1);
  h.feedback.setPreference('notifications','off');h.feedback.observeSession({session_id:'four',state:'running',selected:false,panel_open:false});h.feedback.observeSession({session_id:'four',state:'failed',selected:false,panel_open:false});assert.equal(h.toasts.children.length,1);
});

test('validated preferences persist locally and expose detail and motion overrides',()=>{
  const h=harness('{"details":"full","motion":"reduce","notifications":"important"}');
  assert.deepEqual(JSON.parse(JSON.stringify(h.feedback.preferences())),{details:'full',motion:'reduce',notifications:'important'});
  assert.equal(h.host.dataset.detailLevel,'full');assert.equal(h.host.dataset.motion,'reduce');
  assert.equal(h.feedback.setPreference('details','compact'),true);assert.equal(h.feedback.setPreference('motion','full'),true);assert.equal(h.feedback.setPreference('unknown','x'),false);
  assert.deepEqual(JSON.parse(h.storage.value),{details:'compact',motion:'full',notifications:'important'});
  assert.equal(h.feedback.setUnreadCount(7),7);assert.equal(h.feedback.snapshot().unread_count,7);
});

test('invalid stored preferences fail closed to defaults',()=>{
  const h=harness('{"details":"unsafe","motion":"spin","notifications":"network"}');
  assert.deepEqual(JSON.parse(JSON.stringify(h.feedback.preferences())),{details:'standard',motion:'system',notifications:'all'});
});
