'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

function load() {
  const world = {console}; world.window = world;
  vm.createContext(world);
  vm.runInContext(
    fs.readFileSync(path.join(__dirname, '../src/genshin_app/gui/web/agent-workbench-bootstrap.js'), 'utf8'),
    world,
    {filename:'agent-workbench-bootstrap.js'},
  );
  return world.AgentWorkbenchBootstrap;
}

function harness() {
  const calls = [], nodes = {}, required = [
    'input','send','activeSend','stop','sendMode','composerContext','composerRoot','root','resizer','inspectorResizer','inspectorScrim',
    'controls','detailsPane','chatPane','inspectorPane','settingsToggle','detailsToggle','chatToggle','inspectorToggle','modeToggle',
  ];
  for (const name of required) nodes[name] = {name};
  Object.assign(nodes, {settingsSummary:{},taskSummary:{},inspectorActivityTab:{},badge:{}});
  const composer = {clearIf(){},save(){},autoGrow(){}};
  const turnController = {send(){},stop(){}};
  const agent = {
    nodes, state:{}, sessionController:{activateAcceptedSession(){calls.push('activate');},finishAcceptedSession(){calls.push('finish');}},
    shell:{bind:value=>calls.push(['bind',value]),setSection(){}}, composerContextKey:()=> 'new:whiteboard',
    send(){},enqueueActiveMessage(){},stop(){},notice(){},refresh(){},selected(){},active(){},isActive(){},
    loadWorkbenchFoundation(){},workbenchFoundation(){},schedule(){},workbenchMutationId:prefix=>prefix,
    errorText(){},hasRuntimeCapability(){},applyRuntimeStatus(){},workbenchTarget(){},pendingApplicationSession(){},
    hasUnsettledAppAction(){},cliConnected(){},sessionProviderMatches(){},workbenchProjection(){},capture(){},same(){},
    workflow(){},noReasoningModel(){},validEffort(){},workspace(){},runtimePhaseText(){},loadHistory(){},poll(){},
    session(){},receiveCommand(){},transport:{},receiveProtocolText(){},isAuthoring(){},inboxDock:{},
    installResumeSupervisor:()=>calls.push('resume'),applyPanelLayout:()=>calls.push('layout'),
    renderSettings:()=>calls.push('settings'),
  };
  const factories = {composer:0,turn:0,window:0};
  const modules = {
    composer:{createController(options){factories.composer++;calls.push(['composer-options',options]);return composer;}},
    turnController:{create(options){factories.turn++;calls.push(['turn-options',options]);return turnController;}},
    conversationWindow:{create(options){factories.window++;return {options:options||null};}},
  };
  const controller = load().create({agent,window:{localStorage:{}},document:{body:{}},ui:{id:value=>`${value}-1`},api:async()=>({}),modules});
  return {agent,calls,factories,controller,composer,turnController};
}

test('composition is idempotent and creates one command owner', () => {
  const h=harness(),first=h.controller.wire(),second=h.controller.wire();
  assert.equal(first,second);assert.equal(first.composer,h.composer);assert.equal(first.turnController,h.turnController);
  assert.deepEqual(h.factories,{composer:1,turn:1,window:2});
  assert.equal(h.calls.filter(row=>Array.isArray(row)&&row[0]==='bind').length,1);
  assert.deepEqual(h.calls.filter(row=>typeof row==='string'),['resume','layout','settings']);
});
test('turn admission delegates UI activation to the session controller', () => {
  const h=harness(),wired=h.controller.wire(),options=h.calls.find(row=>Array.isArray(row)&&row[0]==='turn-options')[1];
  options.activateSession({session:{session_id:'one'},message:'x'});options.finishAcceptedSession({message:'x'});
  assert.deepEqual(h.calls.filter(row=>typeof row==='string').slice(-2),['activate','finish']);
  assert.equal(wired.turnController,h.turnController);
});
test('missing capabilities and incomplete DOM fail before installing any controller', () => {
  const Bootstrap=load();
  assert.throws(()=>Bootstrap.create({}),/workbench_bootstrap_agent_missing/);
  const h=harness();delete h.agent.nodes.input;
  assert.throws(()=>h.controller.wire(),/workbench_bootstrap_node_input_missing/);
  assert.deepEqual(h.factories,{composer:0,turn:0,window:0});
});

test('factory failure is sticky and never installs or retries a second command owner', () => {
  const h=harness();let attempts=0;
  h.agent.nodes.input={};
  const Bootstrap=load(),controller=Bootstrap.create({
    agent:h.agent,window:{localStorage:{}},document:{body:{}},ui:{id:value=>value},api:async()=>({}),
    modules:{composer:{createController(){attempts++;return h.composer;}},turnController:{create(){attempts++;throw new Error('turn_factory_failed');}},conversationWindow:{create(){attempts++;return {};}}},
  });
  assert.throws(()=>controller.wire(),/turn_factory_failed/);assert.equal(h.agent.composer,undefined);assert.equal(h.agent.turnController,undefined);
  assert.throws(()=>controller.wire(),/workbench_bootstrap_failed/);assert.equal(attempts,2);
});

test('session controller composition injects callbacks without reading global AC', () => {
  const Bootstrap=load(),h=harness();let received=null;
  h.agent.eventStore={};h.agent.messageNodes=new Map();h.agent.toolNodes=new Map();h.agent.actionNodes=new Map();
  h.agent.storeModel=()=>null;h.agent.clone=value=>value;h.agent.label=value=>value;
  const value={selected(){}};
  const result=Bootstrap.createSessionController({agent:h.agent,window:{confirm:()=>true},ui:{el(){return {}; }},api:async()=>({}),selectors:{createUiState:()=>({})},module:{create(options){received=options;return value;}}});
  assert.equal(result,value);assert.equal(received.state,h.agent.state);assert.equal(received.eventStore,h.agent.eventStore);
  assert.deepEqual(received.createWorkbenchState(),{});assert.equal(typeof received.receiveCommand,'function');assert.equal(typeof received.composer,'function');
});

test('read controller composition owns only injected Host read and presentation ports', () => {
  const Bootstrap=load(),h=harness();let received=null;
  Object.assign(h.agent,{
    clone:value=>value,state:{workbenchCapabilities:{}},readModel:()=>null,errorText:()=>'',pendingApplicationSession:()=>null,
    currentKind:()=> 'whiteboard',hasPendingApplication:()=>false,hasUnsettledAppAction:()=>false,workbenchState:()=>({}),
    renderWorkbench(){},renderMessages(){},selected:()=>null,isActive:()=>false,
  });
  const value={loadHostOutline(){}};
  const result=Bootstrap.createReadController({agent:h.agent,window:{setTimeout,clearTimeout,WB:{state:{board:{board_id:'board'}}}},ui:{el(){},button(){}},api:async()=>({}),
    module:{create(options){received=options;return value;}},outline:{queryDigest(){},parseSearch(){}},foundation:{parseProjection(){}}});
  assert.equal(result,value);assert.equal(typeof received.api,'function');assert.equal(typeof received.selected,'function');assert.equal(typeof received.queryDigest,'function');
  assert.equal(received.currentDocumentId(),'board');assert.equal(Object.hasOwn(received,'send'),false);assert.equal(Object.hasOwn(received,'executeAppAction'),false);
});
