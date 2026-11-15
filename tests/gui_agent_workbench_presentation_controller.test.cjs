const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const sourcePath=path.join(__dirname,'../src/genshin_app/gui/web/agent-workbench-presentation-controller.js');
const source=fs.readFileSync(sourcePath,'utf8');

function setup() {
  let current={kind:'whiteboard',key:'board-one',document:{board_id:'board-one',revision:1},applicationPayload:{kind:'whiteboard'}};
  const session={session_id:'session-one',kind:'whiteboard',live:true,base:current,pendingApplicationAck:{base:current}};
  const sessions=new Map([[session.session_id,session]]), opened=[];
  const world={console}; world.window=world; world.navigator={clipboard:{writeText(){}}};
  world.AgentWorkbenchSelectors={}; world.AgentWorkbenchView={}; world.AgentMessageRenderer={};
  world.AgentTimelineNodeRenderer={renderNode(_document,row,ports){return{row,ports};}};
  vm.createContext(world);vm.runInContext(source,world,{filename:'agent-workbench-presentation-controller.js'});
  const agent={state:{sessions},capture:()=>current,same:(a,b)=>a===b,
    sameContent:(a,b)=>a.document.board_id===b.document.board_id&&a.document.revision===b.document.revision,
    clone:value=>structuredClone(value),labels:{},nodes:{},messageNodes:new Map(),toolNodes:new Map(),
    refresh(){throw new Error('facade refresh should not be used by document bridge');},
  };
  const controller=world.AgentWorkbenchPresentationController.create({agent,window:world,document:{},ui:{el(){return{};}},
    openExternal:async request=>{opened.push(request);return true;}});
  return{world,agent,session,controller,opened,setCurrent:value=>{current=value;}};
}

test('document save bridge rebinds only an unchanged captured document and ignores a late stale completion', () => {
  const h=setup(), snapshot=h.controller.beforeDocumentSave('whiteboard');
  const saved={...snapshot.base,document:{...snapshot.base.document}};h.setCurrent(saved);
  assert.doesNotThrow(()=>h.controller.documentSaved(snapshot));
  assert.equal(h.session.base,saved);assert.equal(h.session.pendingApplicationAck.base,saved);
  const second=h.controller.beforeDocumentSave('whiteboard');
  h.setCurrent({...saved,document:{...saved.document,revision:2}});
  h.controller.documentSaved(second);
  assert.equal(h.session.base,saved,'late save must not absorb a newer document revision');
});

test('timeline links use the injected external-open port after explicit confirmation', async () => {
  const h=setup();h.world.confirm=()=>true;
  const node=h.controller.renderTimelineNode({kind:'research',source_seq:1,text:'source'});
  assert.equal(await node.ports.openExternal({url:'https://example.com'}),true);
  assert.deepEqual(h.opened,[{url:'https://example.com'}]);
});

test('presentation module owns no Host commands and facade keeps only one injected external-link port', () => {
  const chat=fs.readFileSync(path.join(__dirname,'../src/genshin_app/gui/web/agent-chat.js'),'utf8');
  const bootstrap=fs.readFileSync(path.join(__dirname,'../src/genshin_app/gui/web/agent-workbench-bootstrap.js'),'utf8');
  assert.equal((chat.match(/open_external_link/g)||[]).length,0);
  assert.equal((bootstrap.match(/open_external_link/g)||[]).length,1);
  assert.equal(source.includes('callApi('),false);
  assert.equal(source.includes('agent_accept'),false);
  assert.equal(source.includes('agent_resolve_app_action'),false);
});
