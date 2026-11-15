'use strict';

const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');

function eventTarget(value={}){
  value.listeners={};
  value.addEventListener=(type,handler)=>(value.listeners[type]||=[]).push(handler);
  value.removeEventListener=(type,handler)=>{value.listeners[type]=(value.listeners[type]||[]).filter(item=>item!==handler);};
  value.emit=(type,init={})=>{const event={target:value,currentTarget:value,defaultPrevented:false,preventDefault(){this.defaultPrevented=true;},stopPropagation(){this.stopped=true;},...init};
    for(const handler of [...(value.listeners[type]||[])])handler(event);return event;};return value;
}
class Element{
  constructor(tag,document){eventTarget(this);this.tagName=tag;this.ownerDocument=document;this.children=[];this.parentNode=null;this.attributes={};this.className='';this.id='';this.hidden=false;this.isConnected=false;this._text='';
    this.classList={add:(...items)=>{const values=new Set(this.className.split(/\s+/).filter(Boolean));items.forEach(item=>values.add(item));this.className=[...values].join(' ');}};}
  append(...children){for(const child of children){child.parentNode=this;child.setConnected?.(this.isConnected);this.children.push(child);}}
  setConnected(value){this.isConnected=value;for(const child of this.children)child.setConnected?.(value);}
  remove(){if(this.parentNode)this.parentNode.children=this.parentNode.children.filter(child=>child!==this);this.parentNode=null;this.setConnected(false);}
  contains(target){for(let node=target;node;node=node.parentNode)if(node===this)return true;return false;}
  setAttribute(name,value){this.attributes[name]=String(value);}
  getAttribute(name){return Object.hasOwn(this.attributes,name)?this.attributes[name]:null;}
  removeAttribute(name){delete this.attributes[name];}
  focus(){this.ownerDocument.activeElement=this;this.focused=true;}
  set textContent(value){this._text=String(value);this.children=[];}
  get textContent(){return this._text+this.children.map(child=>child.textContent).join('');}
}
function setup(){
  const document=eventTarget({activeElement:null,hidden:false}),body=new Element('body',document);body.setConnected(true);document.body=body;document.createElement=tag=>new Element(tag,document);
  const window=eventTarget({});window.visualViewport=eventTarget({});
  const observers=[];window.MutationObserver=class{constructor(callback){this.callback=callback;observers.push(this);}observe(){}disconnect(){this.disconnected=true;}trigger(){this.callback([{type:'childList'}]);}};
  const world={document,console,Math,Date,JSONTransport:{clone:value=>JSON.parse(JSON.stringify(value))},setStatus(){},MutationObserver:window.MutationObserver};
  Object.assign(world,window);world.window=world;world.visualViewport=window.visualViewport;eventTarget(world);document.defaultView=world; // window listeners live on the VM global.
  vm.createContext(world);vm.runInContext(fs.readFileSync(path.join(__dirname,'../src/genshin_app/gui/web/gui-foundation-v2.js'),'utf8'),world,{filename:'gui-foundation-v2.js'});
  vm.runInContext(fs.readFileSync(path.join(__dirname,'../src/genshin_app/gui/web/ui.js'),'utf8'),world,{filename:'ui.js'});
  return {world,document,body,observers};
}

test('surface kernel applies ARIA, closes on outside pointer and restores focus',()=>{
  const {world,document,body}=setup(),trigger=document.createElement('button'),outside=document.createElement('button'),surface=document.createElement('section'),field=document.createElement('input');
  body.append(trigger,outside);surface.append(field);trigger.focus();
  const opened=world.AppSurfaceKernel.create({surface,trigger,kind:'drawer',ariaLabel:'装备搜索',initialFocus:field,removeOnClose:false});
  assert.equal(opened,surface);assert.equal(surface.hidden,false);assert.equal(surface.attributes.role,'dialog');assert.equal(surface.attributes['aria-modal'],'false');
  assert.equal(trigger.attributes['aria-expanded'],'true');assert.equal(trigger.attributes['aria-controls'],surface.id);assert.equal(document.activeElement,field);
  document.emit('pointerdown',{target:outside});
  assert.equal(surface.hidden,true);assert.equal(trigger.attributes['aria-expanded'],'false');assert.equal(trigger.getAttribute('aria-controls'),null);
  assert.equal(document.activeElement,trigger);assert.equal(world.AppSurfaceKernel.getActive(),null);
});

test('surface kernel keeps a refusing surface active and replaces it after permission',()=>{
  const {world,document,body}=setup(),firstTrigger=document.createElement('button'),secondTrigger=document.createElement('button');body.append(firstTrigger,secondTrigger);let permit=false;
  const first=world.AppSurfaceKernel.create({trigger:firstTrigger,onRequestClose:()=>permit});
  const refused=world.AppSurfaceKernel.create({trigger:secondTrigger});
  assert.equal(refused,null);assert.equal(world.AppSurfaceKernel.getActive(),first);assert.equal(world.AppSurfaceKernel.getLastCreateResult().reason,'active_refused_close');
  permit=true;const second=world.AppSurfaceKernel.create({trigger:secondTrigger});
  assert.equal(first.parentNode,null);assert.equal(world.AppSurfaceKernel.getActive(),second);assert.equal(firstTrigger.attributes['aria-expanded'],'false');
  document.emit('keydown',{key:'Escape',target:second});assert.equal(second.parentNode,null);assert.equal(document.activeElement,secondTrigger);
});

test('surface kernel dismisses ambient lifecycle events and a destroyed trigger',()=>{
  for(const [owner,event] of [['document','scroll'],['window','resize'],['window','blur'],['window','pagehide'],['document','visibilitychange'],['document','workspacechange'],['viewport','resize']]){
    const {world,document,body}=setup(),trigger=document.createElement('button'),outside=document.createElement('div');body.append(trigger,outside);
    const surface=world.AppSurfaceKernel.create({trigger});assert.ok(surface);
    const target=owner==='document'?document:owner==='viewport'?world.visualViewport:world;
    target.emit(event,{target:outside});assert.equal(world.AppSurfaceKernel.getActive(),null,`${owner}.${event} should dismiss`);
  }
  const {world,document,body,observers}=setup(),trigger=document.createElement('button');body.append(trigger);
  const surface=world.AppSurfaceKernel.create({trigger});trigger.remove();observers.at(-1).trigger();
  assert.equal(surface.parentNode,null);assert.equal(world.AppSurfaceKernel.getActive(),null);
});

test('surface kernel destroy and close are idempotent',()=>{
  const {world,document,body}=setup(),trigger=document.createElement('button');body.append(trigger);
  const surface=world.AppSurfaceKernel.create({trigger});assert.equal(surface.destroy(),true);assert.equal(surface.destroy(),true);
  assert.equal(world.AppSurfaceKernel.close(),true);assert.equal(world.AppSurfaceKernel.getActive(),null);
});

test('details disclosure binding shares dismissal and focus without deleting static content',()=>{
  const {world,document,body}=setup(),details=document.createElement('details'),summary=document.createElement('summary'),panel=document.createElement('section'),outside=document.createElement('button');
  details.append(summary,panel);body.append(details,outside);const binding=world.AppSurfaceKernel.bindDisclosure(details,{panel,ariaLabel:'筛选与工具'});
  assert.ok(binding);details.open=true;details.emit('toggle');
  assert.equal(binding.getSurface(),panel);assert.equal(world.AppSurfaceKernel.getActive(),panel);
  assert.equal(summary.attributes['aria-expanded'],'true');assert.equal(panel.hidden,false);
  document.emit('pointerdown',{target:outside});
  assert.equal(details.open,false);assert.equal(panel.hidden,true);assert.equal(panel.parentNode,details);
  assert.equal(summary.attributes['aria-expanded'],'false');assert.equal(document.activeElement,summary);
  details.open=true;details.emit('toggle');assert.equal(panel.hidden,false);
  assert.equal(binding.destroy(),true);assert.equal(binding.destroy(),true);assert.equal(world.AppSurfaceKernel.getActive(),null);
});

test('application binds page status and data tools to one shared transient surface owner',()=>{
  const {world,document,body}=setup(),nodes=new Map(),make=(detailsSelector,panelSelector)=>{
    const details=document.createElement('details'),summary=document.createElement('summary'),panel=document.createElement('section');
    details.append(summary,panel);body.append(details);nodes.set(detailsSelector,details);nodes.set(panelSelector,panel);return {details,summary,panel};
  },status=make('#workspace-status-hub','#workspace-notice-list'),tools=make('#data-tools-panel','.data-tools-popover'),help=make('.axis-help','.axis-help-panel');
  document.querySelector=selector=>nodes.get(selector)||null;document.querySelectorAll=()=>[];
  vm.runInContext(fs.readFileSync(path.join(__dirname,'../src/genshin_app/gui/web/app.js'),'utf8'),world,{filename:'app.js'});
  world.bindSharedDisclosureSurfaces();
  assert.ok(status.details.appSurfaceController);assert.ok(tools.details.appSurfaceController);
  status.details.open=true;status.details.emit('toggle');assert.equal(world.AppSurfaceKernel.getActive(),status.panel);
  tools.details.open=true;tools.details.emit('toggle');
  assert.equal(status.details.open,false);assert.equal(status.panel.hidden,true);
  assert.equal(world.AppSurfaceKernel.getActive(),tools.panel);assert.equal(tools.summary.attributes['aria-expanded'],'true');
  help.details.open=true;help.details.emit('toggle');
  assert.equal(tools.details.open,false);assert.equal(world.AppSurfaceKernel.getActive(),help.panel);
  assert.equal(help.panel.attributes['aria-label'],'时间轴操作与说明');
});
