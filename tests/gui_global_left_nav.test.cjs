'use strict';

const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');

class Classes{
  constructor(node){this.node=node;this.values=new Set();}
  sync(){this.node._className=[...this.values].join(' ');}
  add(...values){values.forEach(value=>this.values.add(value));this.sync();}
  remove(...values){values.forEach(value=>this.values.delete(value));this.sync();}
  toggle(value,force){const next=force===undefined?!this.values.has(value):!!force;next?this.add(value):this.remove(value);return next;}
  contains(value){return this.values.has(value);}
}
function matches(node,selector){
  const id=selector.match(/^#([\w-]+)$/);if(id)return node.id===id[1];
  const tag=selector.match(/^[a-z]+$/i);if(tag)return node.tagName.toLowerCase()===tag[0].toLowerCase();
  const parsed=selector.match(/^(?:([a-z]+))?(?:\.([\w-]+))?(?:\[data-([\w-]+)(?:="([^"]+)")?\])?$/i);
  if(!parsed)return false;const [,name,klass,dataKey,dataValue]=parsed;if(name&&node.tagName.toLowerCase()!==name.toLowerCase())return false;
  if(klass&&!node.classList.contains(klass))return false;if(dataKey){const key=dataKey.replace(/-([a-z])/g,(_,letter)=>letter.toUpperCase());if(!(key in node.dataset))return false;if(dataValue!==undefined&&node.dataset[key]!==dataValue)return false;}return true;
}
const descendants=node=>node.children.flatMap(child=>[child,...descendants(child)]);
class Element{
  constructor(tag,document){this.tagName=tag;this.ownerDocument=document;this.children=[];this.parentNode=null;this.id='';this.dataset={};this.attributes={};this.listeners={};this.hidden=false;this.disabled=false;this._className='';this.classList=new Classes(this);this.style={setProperty(){}};this._text='';}
  append(...children){for(const child of children){if(child.parentNode)child.parentNode.children=child.parentNode.children.filter(row=>row!==child);child.parentNode=this;this.children.push(child);}}
  appendChild(child){this.append(child);return child;}
  insertBefore(child,before){if(child.parentNode)child.parentNode.children=child.parentNode.children.filter(row=>row!==child);const index=this.children.indexOf(before);child.parentNode=this;index<0?this.children.push(child):this.children.splice(index,0,child);return child;}
  replaceChildren(...children){for(const child of this.children)child.parentNode=null;this.children=[];this._text='';this.append(...children);}
  querySelector(selector){return this.querySelectorAll(selector)[0]||null;}
  querySelectorAll(selector){return descendants(this).filter(node=>matches(node,selector));}
  setAttribute(name,value){this.attributes[name]=String(value);}
  getAttribute(name){return this.attributes[name]??null;}
  addEventListener(type,handler){(this.listeners[type]||=[]).push(handler);}
  emit(type,init={}){const event={target:this,currentTarget:this,preventDefault(){},...init};for(const handler of this.listeners[type]||[])handler(event);return event;}
  click(){return this.emit('click');}
  focus(){this.ownerDocument.activeElement=this;}
  set className(value){this._className=String(value);this.classList.values=new Set(this._className.split(/\s+/).filter(Boolean));}
  get className(){return this._className;}
  set textContent(value){this._text=String(value);this.children=[];}
  get textContent(){return this._text+this.children.map(child=>child.textContent).join('');}
  get isConnected(){let node=this;while(node){if(node===this.ownerDocument.body)return true;node=node.parentNode;}return false;}
}
function setup({narrow=false}={}){
  const stored=new Map(),themeCalls=[],document={activeElement:null};document.createElement=tag=>new Element(tag,document);document.body=document.createElement('body');
  document.querySelector=selector=>document.body.querySelector(selector);document.querySelectorAll=selector=>document.body.querySelectorAll(selector);document.getElementById=id=>document.querySelector(`#${id}`);document.addEventListener=()=>{};
  const add=(tag,{id='',className=''}={})=>{const node=document.createElement(tag);node.id=id;if(className)node.classList.add(...className.split(' '));document.body.append(node);return node;};
  const header=add('header',{className:'desktop-header'}),nav=add('nav',{className:'workspace-nav'});for(const tab of ['whiteboard','rotation','result','data','agent']){const button=document.createElement('button');button.classList.add('tab');if(tab==='whiteboard')button.classList.add('active');button.dataset.tab=tab;button.textContent=tab;nav.append(button);}
  const chrome=add('section',{id:'workspace-page-chrome'}),attention=add('section',{id:'workspace-attention-shelf'}),main=add('main'),footer=add('footer');
  const events=[],world={document,console,Math,CustomEvent:class{constructor(type,init){this.type=type;this.detail=init?.detail;}},localStorage:{getItem:key=>stored.get(key)||null,setItem:(key,value)=>stored.set(key,value)},
    matchMedia:()=>({matches:narrow}),dispatchEvent:event=>events.push(event),addEventListener(){},setStatus(){},ThemeController:{current:()=>({preference:'system'}),set:value=>themeCalls.push(value)},DS:{prose:value=>value},RT:{tpl:()=>null,state:{}},PL:{dirty:()=>false},WB:{state:{board:null},dirty:()=>false},RS:{state:{pending:false}}};
  world.window=world;vm.createContext(world);vm.runInContext(fs.readFileSync(path.join(__dirname,'../src/genshin_app/gui/web/workspace.js'),'utf8'),world,{filename:'workspace.js'});world.WS.loadPreferences();
  return {world,WS:world.WS,document,stored,events,themeCalls,header,nav,chrome,attention,main,footer};
}

test('global shell moves the existing navigation and document surfaces without cloning their authority',()=>{
  const env=setup(),shell=env.WS.mountGlobalShell(),sidebar=env.document.getElementById('workspace-global-sidebar');
  assert.ok(shell);assert.equal(shell.children[0],sidebar);assert.equal(env.nav.parentNode,sidebar);
  assert.equal(env.chrome.parentNode.className,'workspace-content-shell');assert.equal(env.attention.parentNode,env.chrome.parentNode);assert.equal(env.main.parentNode,env.chrome.parentNode);assert.equal(env.footer.parentNode,env.chrome.parentNode);
  assert.deepEqual(env.nav.children.map(button=>button.querySelector('.workspace-sidebar-label').textContent),['排轴白板','计算模型（高级）','计算报告','资料库','AI 助手']);
  assert.equal(env.WS.pageChromeState().title,'排轴白板');
  assert.equal(env.events[0].type,'workspace:sidebar-mounted');assert.equal(env.events[0].detail.historyHost,env.document.getElementById('workspace-contextual-history'));
});

test('sidebar collapse is a view preference and contextual history reuses the supplied live node',()=>{
  const env=setup(),shell=env.WS.mountGlobalShell(),history=env.document.createElement('section');history.textContent='真实会话列表';
  assert.equal(env.WS.attachContextHistory(history,{owner:'whiteboard'}),true);assert.equal(history.parentNode,env.document.getElementById('workspace-contextual-history'));assert.equal(history.hidden,false);
  env.WS.toggleGlobalSidebar();assert.equal(shell.classList.contains('workspace-sidebar-collapsed'),true);assert.equal(env.WS.sidebarPreference().collapsed,true);assert.match(env.stored.get('genshin.workspace.v1'),/globalSidebar/);
  env.WS.toggleGlobalSidebar();assert.equal(shell.classList.contains('workspace-sidebar-collapsed'),false);assert.equal(history.parentNode,env.document.getElementById('workspace-contextual-history'));
});

test('narrow navigation uses an overlay lifecycle instead of changing the persisted desktop width',()=>{
  const env=setup({narrow:true}),shell=env.WS.mountGlobalShell();env.WS.toggleGlobalSidebar();
  assert.equal(shell.classList.contains('workspace-sidebar-overlay-open'),true);assert.equal(env.WS.sidebarPreference().collapsed,false);
  env.WS.navigate('whiteboard');assert.equal(shell.classList.contains('workspace-sidebar-overlay-open'),false);
});

test('sidebar exposes persisted 220-280px resizing and the shared theme authority',()=>{
  const env=setup(),shell=env.WS.mountGlobalShell(),resizer=shell.querySelector('.workspace-sidebar-resizer'),theme=shell.querySelector('.workspace-theme-select');
  assert.equal(env.WS.sidebarPreference().width,240);resizer.emit('keydown',{key:'End'});assert.equal(env.WS.sidebarPreference().width,280);
  resizer.emit('keydown',{key:'ArrowLeft'});assert.equal(env.WS.sidebarPreference().width,268);assert.match(env.stored.get('genshin.workspace.v1'),/"width":268/);
  theme.value='light';theme.emit('change');assert.deepEqual(env.themeCalls,['light']);
  assert.deepEqual(theme.children.map(option=>option.textContent),['跟随系统','浅色','深色']);
});

test('Agent new action uses the public workbench API instead of the internal compatibility object',async()=>{
  const env=setup();let created=0;env.world.AgentWorkbenchApp={newTask(){created+=1;return true;}};
  env.WS.tab=()=> 'agent';
  assert.equal(await env.WS.sidebarNewAction(),true);assert.equal(created,1);
  assert.doesNotMatch(fs.readFileSync(path.join(__dirname,'../src/genshin_app/gui/web/workspace.js'),'utf8'),/window\.AC|\bAC\./);
});

test('workspace CSS exposes desktop rail, compact rail and mobile overlay breakpoints',()=>{
  const css=fs.readFileSync(path.join(__dirname,'../src/genshin_app/gui/web/workspace.css'),'utf8');
  assert.match(css,/\.workspace-app-shell\s*\{[^}]*grid-template-columns\s*:\s*var\(--workspace-sidebar-width,240px\)/s);
  assert.match(css,/@media\(max-width:1279px\) and \(min-width:960px\)/);assert.match(css,/@media\(max-width:959px\)/);
  assert.match(css,/workspace-sidebar-overlay-open/);assert.match(css,/\.workspace-contextual-history/);assert.match(css,/\.workspace-sidebar-resizer/);assert.match(css,/\.workspace-theme-control/);
});
