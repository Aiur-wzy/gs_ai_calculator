const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const source = fs.readFileSync(path.join(__dirname, '../src/genshin_app/gui/web/agent-checkpoint-integration.js'), 'utf8');

class Node {
  constructor(tag='div',text=null,className=''){this.tag=tag;this.text=text==null?'':String(text);this.className=className;this.children=[];this.listeners={};this.hidden=false;}
  append(...children){this.children.push(...children);}
  addEventListener(name,callback){(this.listeners[name]||=[]).push(callback);}
  emit(name,extra={}){return(this.listeners[name]||[]).map(callback=>callback({currentTarget:this,preventDefault(){},...extra}));}
  get textContent(){return this.text+this.children.map(child=>child?.textContent||'').join('');}
  set textContent(value){this.text=String(value);this.children=[];}
  showModal(){this.open=true;} close(){this.open=false;} remove(){this.removed=true;}
}

function setup(options={}) {
  const world = {console}; world.window = world;
  world.JSONTransport = {stringify:JSON.stringify};
  vm.createContext(world); vm.runInContext(source, world, {filename:'agent-checkpoint-integration.js'});
  const nodes = {checkpointSection:{hidden:false}}, notices = [], states = [], apiCalls = [], dialogs=[];
  world.AppDialogKernel={create:options=>{const dialog=new Node('dialog'),title=new Node('h3',options.title),body=new Node('section');dialog.appBody=body;
    dialog.append(title,body);dialog.setActions=(...actions)=>dialog.append(...actions);dialog.finish=()=>{dialog.removed=true;return true;};dialogs.push(dialog);return dialog;}};
  const session = {session_id:'session-one', kind:'whiteboard', base:null};
  const sessionTwo = {session_id:'session-two', kind:'whiteboard', base:null};
  let selected = session;
  const agent = {
    state:{selected:'session-one', workbenchCapabilities:{document_checkpoints:true}}, nodes,
    documentAdapter:{}, clone:value=>structuredClone(value), capture:()=>({kind:'whiteboard', key:'board-one'}),
    currentKind:()=> 'whiteboard', selected:()=>selected, notice:(...args)=>notices.push(args), errorText:(_r,f)=>f,
    compatibilityPrompt:'固定兼容提示词，不含旧会话内容',copyCompatibilityPrompt:async()=>{options.copied?.push('固定兼容提示词，不含旧会话内容');return true;},refresh(){}, documentChanged(){},
  };
  const view = {clear(){}, update(){}, setBusy(){}, setFrozen(){}, setStatus(){}};
  const controller = {
    snapshot:()=>({sessionId:'session-one', documentId:'board-one', projection:{items:[]}, frozen:false}),
    bind:async value=>{states.push(value); return true;}, createCheckpoint:async()=>true, renameCheckpoint:async()=>true,
    deleteCheckpoint:async()=>true, restoreCheckpoint:async()=>true, previewCheckpoint:async()=>options.previewRaw||null, loadMore:async()=>true,
  };
  const integration = world.AgentCheckpointIntegration.create({agent, window:world,
    document:{body:{append(node){dialogs.push(node);}}}, ui:{el:(tag,text,className)=>new Node(tag,text,className),button:(text,fn,className)=>{const node=new Node('button',text,className);node.addEventListener('click',fn);return node;}},
    api:async(...args)=>{apiCalls.push(args);return{code:0};},
    checkpoints:{create:()=>view, parseDiff:()=>options.parsed||({ok:false})}, controller:{create:()=>controller}});
  return {world,agent,session,sessionTwo,nodes,notices,states,apiCalls,dialogs,integration,view,controller,
    select(value){selected=value;agent.state.selected=value?.session_id||null;}};
}

test('checkpoint integration binds one view/controller and leaves Host transactions in the controller', async () => {
  const h=setup(), host={};
  const first=h.integration.wire(host), second=h.integration.wire(host);
  assert.equal(first.view,h.view); assert.equal(first.controller,h.controller);
  assert.equal(second.view,h.view); assert.equal(await h.integration.bind(h.session),true);
  assert.equal(h.states.length,0,'an already-bound exact projection must not issue a second bind');
  assert.equal(h.apiCalls.length,0,'integration does not issue checkpoint Host commands itself');
});

test('checkpoint integration rejects a second host and keeps raw Renderer ports private', async () => {
  const h=setup(), host={}; h.integration.wire(host);
  assert.throws(()=>h.integration.wire({}),/already_wired/);
  assert.equal(Object.hasOwn(h.integration,'applyTarget'),false);
  assert.equal(Object.hasOwn(h.integration,'presentState'),false);
});

test('checkpoint integration rejects an out-of-order bind completion after selection changes', async () => {
  const h=setup(), gate={}; let release; gate.promise=new Promise(resolve=>{release=resolve;});
  h.integration.wire({}); h.controller.snapshot=()=>({sessionId:null,documentId:null,projection:null,frozen:false,bindingGeneration:0});
  let calls=0;h.controller.bind=async value=>{h.states.push(value);calls++;if(calls===1)return gate.promise;return true;};
  const oldBind=h.integration.bind(h.session);h.select(h.sessionTwo);assert.equal(await h.integration.bind(h.sessionTwo),true);release(true);
  assert.equal(await oldBind,false);assert.deepEqual(h.states.map(value=>value.sessionId),['session-one','session-two']);
});

test('facade contains no checkpoint transaction endpoint', () => {
  const chat=fs.readFileSync(path.join(__dirname,'../src/genshin_app/gui/web/agent-chat.js'),'utf8');
  for(const endpoint of ['agent_prepare_document_restore','agent_mark_document_restore_applied','agent_commit_document_restore'])
    assert.equal(chat.includes(endpoint),false);
  assert.equal(chat.includes('applyCheckpointTarget'),false);
  assert.equal(chat.includes('presentCheckpointState'),false);
  const bootstrap=fs.readFileSync(path.join(__dirname,'../src/genshin_app/gui/web/agent-workbench-bootstrap.js'),'utf8');
  assert.equal(bootstrap.includes('applyCheckpointTarget'),false);
  assert.equal(bootstrap.includes('presentCheckpointState'),false);
});

test('incompatible checkpoint preview refuses restore and offers copy-only guidance', async () => {
  const copied=[],projection={source_lineage_epoch:1,source_revision:4,target_lineage_epoch:1,target_revision:2,document:{changed:false,changed_key_count:0,truncated:false,added_keys:[],removed_keys:[],changed_keys:[]},application_payload:{changed:true,changed_key_count:1,truncated:false,added_keys:[],removed_keys:[],changed_keys:['calculated']},application_payload_reusable:false};
  const h=setup({copied,previewRaw:{value:true},parsed:{ok:true,projection}});h.integration.wire({});
  assert.equal(await h.integration.previewCheckpoint({item:{checkpoint_id:'checkpoint-one',name:'旧结果'}}),true);
  const dialog=h.dialogs.at(-1);assert.match(dialog.textContent,/不能直接恢复/);assert.doesNotMatch(dialog.textContent,/Host|runtime|Queue|lineage|revision|cursor|修订/i);assert.match(dialog.textContent,/固定兼容提示词/);const titled=[];const collect=node=>{if(node?.title)titled.push(node);for(const child of node?.children||[])collect(child);};collect(dialog);assert.match(titled[0].title,/source lineage 1 \/ revision 4; target lineage 1 \/ revision 2/);
  const buttons=[];const walk=node=>{if(node?.tag==='button')buttons.push(node);for(const child of node?.children||[])walk(child);};walk(dialog);const copy=buttons.find(node=>/复制给 Agent/.test(node.textContent));assert.ok(copy);await copy.emit('click',{currentTarget:copy}).at(-1);assert.deepEqual(copied,['固定兼容提示词，不含旧会话内容']);assert.equal(h.apiCalls.length,0);
});
