'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

function eventTarget(value = {}) {
  value.listeners = {};
  value.addEventListener = (type, handler) => (value.listeners[type] ||= []).push(handler);
  value.removeEventListener = (type, handler) => value.listeners[type] = (value.listeners[type] || []).filter(row => row !== handler);
  value.emit = (type, init = {}) => {
    const event = {target:value,currentTarget:value,key:'',shiftKey:false,clientX:0,clientY:0,defaultPrevented:false,preventDefault(){this.defaultPrevented=true;},stopPropagation(){this.stopped=true;},...init};
    for (const handler of [...(value.listeners[type] || [])]) handler(event);
    return event;
  };
  return value;
}

class Element {
  constructor(tag, document) {
    eventTarget(this); this.tagName=tag; this.ownerDocument=document; this.children=[]; this.parentNode=null;
    this.attributes={}; this.dataset={}; this.style={}; this.className=''; this.disabled=false; this.tabIndex=0;
    this._text=''; this.offsetWidth=180; this.offsetHeight=120; this.rect={left:260,top:160,right:300,bottom:190};
  }
  append(...children){for(const child of children){child.parentNode=this;this.children.push(child);}}
  remove(){if(!this.parentNode)return;this.parentNode.children=this.parentNode.children.filter(row=>row!==this);this.parentNode=null;}
  contains(node){return node===this||this.children.some(child=>child.contains(node));}
  setAttribute(name,value){this.attributes[name]=String(value);}
  getAttribute(name){return this.attributes[name];}
  removeAttribute(name){delete this.attributes[name];}
  focus(){this.ownerDocument.activeElement=this;this.focused=true;}
  click(){this.emit('click');}
  getBoundingClientRect(){return this.rect;}
  set textContent(value){this._text=String(value);this.children=[];}
  get textContent(){return this._text+this.children.map(child=>child.textContent).join('');}
  get isConnected(){let node=this;while(node){if(node===this.ownerDocument.body)return true;node=node.parentNode;}return false;}
}

function all(node){return [node,...node.children.flatMap(all)];}

function setup() {
  const document=eventTarget({activeElement:null,documentElement:{clientWidth:320,clientHeight:200}});
  document.createElement=tag=>new Element(tag,document);document.body=document.createElement('body');
  const observers=[];
  class MutationObserver {constructor(callback){this.callback=callback;observers.push(this);}observe(){}disconnect(){this.disconnected=true;}}
  const world=eventTarget({document,innerWidth:320,innerHeight:200,MutationObserver,JSONTransport:{clone:value=>JSON.parse(JSON.stringify(value))},setStatus(){}});world.window=world;document.defaultView=world;
  vm.createContext(world);for(const name of ['gui-foundation-v2.js','ui.js','context-menu.js'])vm.runInContext(fs.readFileSync(path.join(__dirname,'../src/genshin_app/gui/web',name),'utf8'),world,{filename:name});
  return {world,document,observers,menu:world.AppContextMenu.create({window:world,document})};
}

test('context menu supports pointer and keyboard entry without invoking the target',()=>{
  const {menu,document}=setup(),target=document.createElement('article'),calls=[];document.body.append(target);
  menu.bind(target,{label:'任务操作',items:()=>[{id:'open',label:'打开',run:()=>calls.push('open')},{id:'blocked',label:'删除',enabled:false,disabledReason:'正在运行',run:()=>calls.push('bad')}]});
  const pointer=target.emit('contextmenu',{clientX:310,clientY:195});assert.equal(pointer.defaultPrevented,true);assert.equal(menu.isOpen(),true);assert.deepEqual(calls,[]);
  let popup=all(document.body).find(node=>node.attributes?.role==='menu');assert.ok(parseInt(popup.style.left,10)>=8);assert.ok(parseInt(popup.style.top,10)>=8);assert.ok(parseInt(popup.style.left,10)+popup.offsetWidth<=312);assert.equal(all(popup).find(node=>node.dataset?.command==='blocked').disabled,true);
  document.emit('pointerdown',{target:document.body});assert.equal(menu.isOpen(),false);
  const keyboard=target.emit('keydown',{key:'F10',shiftKey:true,target});assert.equal(keyboard.defaultPrevented,true);popup=all(document.body).find(node=>node.attributes?.role==='menu');assert.equal(document.activeElement.dataset.command,'open');popup.emit('keydown',{key:'Enter',target:document.activeElement});assert.deepEqual(calls,['open']);assert.equal(menu.isOpen(),false);assert.equal(document.activeElement,target);
});

test('context menu closes for every desktop lifecycle boundary and restores focus on Escape/Tab',()=>{
  const {menu,document,world,observers}=setup(),target=document.createElement('button');document.body.append(target);
  const show=()=>menu.open({target,returnFocus:target,items:[{id:'one',label:'一',run(){}}]});
  show();let popup=all(document.body).find(node=>node.attributes?.role==='menu');popup.emit('keydown',{key:'Escape'});assert.equal(menu.isOpen(),false);assert.equal(document.activeElement,target);
  show();popup=all(document.body).find(node=>node.attributes?.role==='menu');popup.emit('keydown',{key:'Tab'});assert.equal(menu.isOpen(),false);assert.equal(document.activeElement,target);
  for(const [owner,event] of [[document,'scroll'],[world,'resize'],[world,'blur']]){show();owner.emit(event);assert.equal(menu.isOpen(),false,event);}
  show();document.emit('pointerdown',{target:document.body});assert.equal(menu.isOpen(),false);
  show();target.remove();observers.at(-1).callback([]);assert.equal(menu.isOpen(),false);assert.equal(all(document.body).some(node=>node.attributes?.role==='menu'),false);
});

test('document-scoped factory returns one reusable command surface',()=>{
  const {world,document}=setup();
  assert.equal(world.AppContextMenu.forDocument(document,world),world.AppContextMenu.forDocument(document,world));
  assert.equal(world.AppContextMenu.menuKey({key:'ContextMenu'}),true);
  assert.equal(world.AppContextMenu.menuKey({key:'F10',shiftKey:true}),true);
});

test('an editor inside a row keeps its native mouse and keyboard context menu',()=>{
  const {menu,document}=setup(),row=document.createElement('article'),input=document.createElement('input');
  row.append(input);document.body.append(row);
  menu.bind(row,{items:[{id:'one',label:'一',run(){}}]});
  assert.equal(row.emit('contextmenu',{target:input}).defaultPrevented,false);
  assert.equal(row.emit('keydown',{target:input,key:'ContextMenu'}).defaultPrevented,false);
  assert.equal(menu.isOpen(),false);
  assert.equal(row.emit('keydown',{target:row,key:'ContextMenu'}).defaultPrevented,true);
  assert.equal(menu.isOpen(),true);
});
