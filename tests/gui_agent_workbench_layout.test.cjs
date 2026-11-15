'use strict';

const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');

class Element{
  constructor(tag='div'){this.tagName=tag;this.children=[];this.attributes={};this.dataset={};this.listeners={};this.value='';this.hidden=false;this.classList={add(){},remove(){},toggle(){}};}
  append(...nodes){for(const node of nodes){node.parent=this;this.children.push(node);}}
  setAttribute(key,value){this.attributes[key]=String(value);}
  addEventListener(key,fn){(this.listeners[key]||=[]).push(fn);}
}

function harness({surfaceKernel=false}={}){
  const body=new Element('body'),root=new Element('aside'),badge=new Element('button');root.id='agent-chat';badge.id='agent-chat-toggle';body.append(root,badge);
  const document={body,createElement:tag=>new Element(tag),getElementById:id=>id==='agent-chat'?root:id==='agent-chat-toggle'?badge:null};
  const ui={
    el(tag,text=null,classes=''){const node=new Element(tag);node.textContent=text==null?'':String(text);node.className=classes;return node;},
    button(label,handler,classes=''){const node=this.el('button',label,classes);node.addEventListener('click',handler);return node;},
    select(items,value,onChange){const node=this.el('select');node.value=value;node.items=items;node.addEventListener('change',()=>onChange(node.value));return node;},
    input(value,onInput){const node=this.el('input');node.value=value;node.addEventListener('input',()=>onInput(node.value));return node;},
    field(label,control){const node=this.el('label',label);node.append(control);return node;},
  };
  const surfaceBindings=[];
  const world={console,document,localStorage:{},requestAnimationFrame:fn=>fn()};
  if(surfaceKernel)world.AppSurfaceKernel={bindDisclosure:(details,options)=>{const binding={details,options};surfaceBindings.push(binding);return binding;}};
  world.window=world;vm.createContext(world);
  vm.runInContext(fs.readFileSync(path.join(__dirname,'../src/genshin_app/gui/web/agent-workbench-layout.js'),'utf8'),world,{filename:'agent-workbench-layout.js'});
  const agent={nodes:{},state:{cliId:'',models:[],selected:null},currentKind:()=> 'whiteboard',defaultWorkflow:()=> 'staged',workbenchState:()=>({}),composerContextKey:()=> 'new:whiteboard'};
  return {world,agent,root,badge,surfaceBindings,layout:world.AgentWorkbenchLayout.create({agent,window:world,document,ui})};
}

test('layout builds the complete semantic shell without Host or controller dependencies',()=>{
  const h=harness();assert.equal(Object.isFrozen(h.layout),true);assert.notEqual(h.layout.nodes,h.agent.nodes);assert.deepEqual(h.agent.nodes,{});
  for(const name of ['resizer','settingsToggle','detailsToggle','chatToggle','controls','detailsPane','chatPane','messages','telemetryHost','appActionsBody','input','send','stop'])assert.ok(h.layout.nodes[name],name);
  assert.equal(h.layout.nodes.resizer.attributes.role,'separator');assert.equal(h.layout.nodes.messages.attributes.role,'log');
  assert.equal(h.layout.nodes.chatView.attributes.role,'tabpanel');assert.equal(h.layout.nodes.trajectoryView.attributes.role,'tabpanel');
  assert.equal(h.layout.views.panes.chat,h.layout.nodes.chatPane);assert.equal(h.layout.views.projections.trajectory,h.layout.nodes.trajectoryView);
  assert.equal(h.layout.nodes.workbenchViews.parent,h.layout.nodes.conversationTools);assert.equal(h.layout.nodes.conversationTools.open,undefined);
  assert.equal(h.layout.nodes.composerOptions.open,undefined);assert.match(h.layout.nodes.headerTitle.textContent,/AI 排轴助手/);
});

test('layout delegates transient header and status disclosures to the shared surface kernel',()=>{
  const h=harness({surfaceKernel:true}),{nodes}=h.layout;
  assert.equal(h.surfaceBindings.length,4);
  assert.equal(h.surfaceBindings[0].details,nodes.moreMenu);assert.equal(h.surfaceBindings[0].options.ariaLabel,'AI 助手显示选项');
  assert.equal(h.surfaceBindings[1].details,nodes.statusHub);assert.equal(h.surfaceBindings[1].options.ariaLabel,'AI 助手状态详情');
  assert.equal(nodes.moreSurfaceBinding,h.surfaceBindings[0]);assert.equal(nodes.statusSurfaceBinding,h.surfaceBindings[1]);
  assert.equal(h.surfaceBindings[2].details,nodes.searchTools);assert.equal(h.surfaceBindings[2].options.initialFocus,nodes.search);
  assert.equal(h.surfaceBindings[3].details,nodes.turnTools);assert.equal(h.surfaceBindings[3].options.ariaLabel,'会话轮次');
  assert.equal(nodes.searchToolsPanel.parent,nodes.searchTools);assert.equal(nodes.search.parent,nodes.searchToolsPanel);
});

test('layout source is a pure DOM factory with no Host request or domain-controller construction',()=>{
  const source=fs.readFileSync(path.join(__dirname,'../src/genshin_app/gui/web/agent-workbench-layout.js'),'utf8');
  assert.equal(source.includes('callApi('),false);assert.doesNotMatch(source,/Agent[A-Za-z]+\.create/);
  assert.doesNotMatch(source,/agent\.state\b/);assert.doesNotMatch(source,/workbenchState\([^)]*\)\.[A-Za-z_$][\w$]*\s*=/);
  for(const endpoint of ['agent_start','agent_send','agent_accept','agent_resolve_app_action','agent_submit_question_batch_answer'])assert.equal(source.includes(endpoint),false);
});

test('agent-chat is a thin compatibility facade and production assembly exists only in bootstrap',()=>{
  const web=path.join(__dirname,'../src/genshin_app/gui/web'),chat=fs.readFileSync(path.join(web,'agent-chat.js'),'utf8'),bootstrap=fs.readFileSync(path.join(web,'agent-workbench-bootstrap.js'),'utf8');
  assert.equal(chat.includes('callApi('),false);assert.doesNotMatch(chat,/Agent[A-Za-z]+\.create\(/);
  assert.doesNotMatch(chat,/createShell\(/);assert.doesNotMatch(chat,/\bAC\.state\.[A-Za-z_$][\w$]*\s*=/);assert.doesNotMatch(chat,/session\.workbench\s*=/);
  assert.match(bootstrap,/stateAdapter\.createShell/);assert.match(bootstrap,/Object\.defineProperty\(agent,name/);
  assert.match(chat,/AgentWorkbenchBootstrap\.createProduction/);assert.match(bootstrap,/function createProduction/);
  for(const owner of ['modules.provider.create','modules.transport.create','createSessionController({agent','createReadController({agent','modules.checkpointIntegration.create','modules.workflowReview.create','modules.recovery.create','modules.questionAnswer.create','modules.lifecycle.create'])assert.ok(bootstrap.includes(owner),owner);
});
