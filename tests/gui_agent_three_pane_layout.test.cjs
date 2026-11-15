'use strict';

const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');

class Element{
  constructor(tag='div'){
    this.tagName=tag;this.children=[];this.attributes={};this.dataset={};this.listeners={};
    this.value='';this.hidden=false;this.className='';this.tabIndex=0;
    const classes=new Set();
    this.classList={
      add:(...values)=>values.forEach(value=>classes.add(value)),
      remove:(...values)=>values.forEach(value=>classes.delete(value)),
      toggle:(value,force)=>{const next=force===undefined?!classes.has(value):!!force;if(next)classes.add(value);else classes.delete(value);return next;},
      contains:value=>classes.has(value),
    };
  }
  append(...nodes){for(const node of nodes){node.parent=this;this.children.push(node);}}
  setAttribute(key,value){this.attributes[key]=String(value);}
  addEventListener(key,fn){(this.listeners[key]||=[]).push(fn);}
  emit(key,event={}){for(const fn of this.listeners[key]||[])fn({...event,currentTarget:this,target:this});}
}

function build(){
  const body=new Element('body'),root=new Element('aside'),badge=new Element('button');
  root.id='agent-chat';badge.id='agent-chat-toggle';body.append(root,badge);
  const document={body,createElement:tag=>new Element(tag),getElementById:id=>id==='agent-chat'?root:id==='agent-chat-toggle'?badge:null};
  const ui={
    el(tag,text=null,classes=''){const node=new Element(tag);node.textContent=text==null?'':String(text);node.className=classes;return node;},
    button(label,handler,classes=''){const node=this.el('button',label,classes);node.addEventListener('click',handler);return node;},
    select(items,value,onChange){const node=this.el('select');node.value=value;node.items=items;node.addEventListener('change',()=>onChange(node.value));return node;},
    input(value,onInput){const node=this.el('input');node.value=value;node.addEventListener('input',()=>onInput(node.value));return node;},
    field(label,control){const node=this.el('label',label);node.append(control);return node;},
  };
  const world={console,document,localStorage:{},requestAnimationFrame:fn=>fn()};world.window=world;vm.createContext(world);
  vm.runInContext(fs.readFileSync(path.join(__dirname,'../src/genshin_app/gui/web/agent-workbench-layout.js'),'utf8'),world,{filename:'agent-workbench-layout.js'});
  const agent={currentKind:()=> 'whiteboard',defaultWorkflow:()=> 'staged',openDock(){},openPage(){},close(){},togglePanelSection(){}};
  return world.AgentWorkbenchLayout.create({agent,window:world,document,ui});
}

test('page workbench leaves history to the global sidebar and owns chat plus Inspector',()=>{
  const {nodes:n,views}=build();
  assert.equal(views.panes.details,n.detailsPane);
  assert.equal(views.panes.chat,n.chatPane);
  assert.equal(views.panes.inspector,n.inspectorPane);
  assert.equal(n.inspectorActivity.parent,n.inspectorPane);
  assert.equal(n.inspectorFiles.parent,n.inspectorPane);
  assert.equal(n.inspectorDetails.parent,n.inspectorPane);
  assert.equal(n.inspectorResizer.parent,n.root);
  assert.equal(n.inspectorScrim.parent,n.root);
  assert.equal(n.taskSettings.parent,n.inspectorDetails);
  assert.equal(n.statusDock.parent,n.inspectorActivity);
  assert.equal(n.tools.parent,n.inspectorActivity);
  assert.notEqual(n.tools.parent,n.chatView);
  assert.equal(n.checkpointSection.parent,n.inspectorFiles);
  assert.equal(n.technicalDetails.parent,n.inspectorDetails);
});

test('Inspector tabs switch local presentation while activity is the default',()=>{
  const {nodes:n}=build();
  assert.equal(n.inspectorPane.dataset.view,'activity');
  assert.equal(n.inspectorActivity.hidden,false);
  assert.equal(n.inspectorFiles.hidden,true);
  assert.equal(n.inspectorActivityTab.attributes['aria-selected'],'true');
  n.inspectorFilesTab.emit('click');
  assert.equal(n.inspectorPane.dataset.view,'files');
  assert.equal(n.inspectorActivity.hidden,true);
  assert.equal(n.inspectorFiles.hidden,false);
  assert.equal(n.inspectorFilesTab.attributes['aria-selected'],'true');
});

test('model and reasoning controls are a compact composer chip instead of a permanent settings row',()=>{
  const {nodes:n}=build();
  assert.equal(n.composerOptions.parent,n.composerRoot.children.find(child=>child.className==='ac-composer-actions'));
  assert.equal(n.model.parent.parent,n.composerOptions.children[1]);
  n.model.value='DeepSeek-V4.1-Flash';n.effort.value='high';n.refreshModelPicker();
  assert.equal(n.modelPickerText.textContent,'DeepSeek-V4.1-Flash · high');
  assert.equal(n.send.textContent,'发送');
});
