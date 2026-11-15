const test=require('node:test'),assert=require('node:assert/strict'),path=require('node:path'),vm=require('node:vm');
const GUI_TEST_RUNTIME=require('./helpers/gui_runtime.cjs');

class Node{
  constructor(tag='div'){this.tagName=tag;this.value='';this.textContent='';this.hidden=false;this.disabled=false;this.style={};this.dataset={};this.attributes={};this.children=[];this.listeners={};this.scrollHeight=70;this.focused=false;}
  addEventListener(key,fn){(this.listeners[key]||=[]).push(fn);}
  emit(key,extra={}){let prevented=false;for(const fn of this.listeners[key]||[])fn({preventDefault(){prevented=true;},...extra});return prevented;}
  setAttribute(key,value){this.attributes[key]=String(value);}
  append(...nodes){this.children.push(...nodes);}
  replaceChildren(...nodes){this.children=[...nodes];}
  focus(){this.focused=true;}
}
const storage=initial=>{const rows=new Map(Object.entries(initial||{}));return {rows,getItem:key=>rows.get(key)??null,setItem:(key,value)=>rows.set(key,String(value))};};
const load=()=>{const world={console};world.window=world;world.globalThis=world;vm.createContext(world);GUI_TEST_RUNTIME.loadWebFile(world,path.join(__dirname,'../src/genshin_app/gui/web/agent-composer.js'));return world.AgentComposer;};
const fixture=(initial={},contextKey='new:whiteboard')=>{const Composer=load(),store=storage(initial),input=new Node('textarea'),send=new Node('button'),stop=new Node('button'),mode=new Node('select'),context=new Node(),contextSummary=new Node('summary'),root=new Node(),document={createElement:tag=>new Node(tag)},calls=[];stop.hidden=true;let now=100;
  const controller=Composer.createController({storage:store,document,input,sendButton:send,stopButton:stop,sendModeSelect:mode,contextHost:context,contextSummaryHost:contextSummary,root,contextKey,now:()=>++now,onSend:()=>calls.push('send'),onStop:()=>calls.push('stop')});return {Composer,store,input,send,stop,mode,context,contextSummary,root,calls,controller};};

test('composer keeps bounded versioned drafts per conversation and restores exact unsent text',()=>{
  const e=fixture();e.input.value='新任务草稿\n第二行';e.input.emit('input');e.controller.switchContext('session:first');assert.equal(e.input.value,'');e.input.value='会话一草稿';e.input.emit('input');e.controller.switchContext('new:whiteboard');assert.equal(e.input.value,'新任务草稿\n第二行');
  const written=JSON.parse(e.store.rows.get(e.Composer.STORAGE_KEY));assert.equal(written.version,1);assert.deepEqual(Object.keys(written).sort(),['drafts','preference','version']);assert.equal(written.drafts.length,2);assert.ok(written.drafts.every(row=>Object.keys(row).sort().join(',')==='key,text,updatedAt'));
  const restored=fixture({[e.Composer.STORAGE_KEY]:JSON.stringify(written)});assert.equal(restored.input.value,'新任务草稿\n第二行');assert.equal(restored.controller.dropContext('session:first'),true);assert.equal(JSON.parse(restored.store.rows.get(e.Composer.STORAGE_KEY)).drafts.some(row=>row.key==='session:first'),false);
});

test('composer storage discards forbidden fields and prunes oversized or excess drafts',()=>{
  const Composer=load(),drafts=Array.from({length:40},(_,index)=>({key:`s:${index}`,text:`draft-${index}`,updatedAt:index,apiKey:'secret'}));drafts.push({key:'oversized',text:'x'.repeat(Composer.MAX_DRAFT_CHARS+1),updatedAt:100});
  const store=storage({[Composer.STORAGE_KEY]:JSON.stringify({version:1,preference:{sendMode:'enter',runtime:{state:'ready'}},drafts,apiKey:'secret',candidate:{},inbox:[],path:'C:/private',result:{}})}),state=Composer.readStore(store);
  assert.equal(state.drafts.length,Composer.MAX_DRAFTS);assert.equal(state.drafts.some(row=>row.key==='oversized'),false);assert.doesNotMatch(JSON.stringify(state),/secret|runtime|candidate|inbox|private|result/i);Composer.writeStore(store,state);assert.doesNotMatch(store.rows.get(Composer.STORAGE_KEY),/secret|runtime|candidate|inbox|private|result/i);
});

test('composer keyboard is Enter-send, Shift-newline, Ctrl-send and IME safe with an optional Ctrl-only mode',()=>{
  const e=fixture();assert.equal(e.input.emit('keydown',{key:'Enter'}),true);assert.deepEqual(e.calls,['send']);assert.equal(e.input.emit('keydown',{key:'Enter',shiftKey:true}),false);assert.deepEqual(e.calls,['send']);
  e.input.emit('compositionstart');assert.equal(e.input.emit('keydown',{key:'Enter',isComposing:false}),false);e.input.emit('compositionend');assert.equal(e.input.emit('keydown',{key:'Enter',keyCode:229}),false);assert.equal(e.input.emit('keydown',{key:'Enter',ctrlKey:true}),true);assert.deepEqual(e.calls,['send','send']);
  e.mode.value='ctrl_enter';e.mode.emit('change');assert.equal(e.input.emit('keydown',{key:'Enter'}),false);assert.equal(e.input.emit('keydown',{key:'Enter',ctrlKey:true}),true);assert.deepEqual(e.calls,['send','send','send']);
  e.controller.setPrimary({active:true});assert.equal(e.send.hidden,true);assert.equal(e.stop.hidden,false);assert.equal(e.input.emit('keydown',{key:'Enter',ctrlKey:true}),false);assert.equal(e.calls.at(-1),'send');
});

test('composer auto grows, renders bounded text-only context chips and clears only the accepted draft',()=>{
  const e=fixture();e.input.scrollHeight=400;e.input.value='保留我';e.input.emit('input');assert.equal(e.input.style.height,'220px');assert.equal(e.input.style.overflowY,'auto');e.controller.setContextChips([{kind:'document',label:'白板',value:'神鹤万心',title:'当前对象'},{kind:'dirty',label:'文档',value:'有未保存修改'}]);
  assert.equal(e.context.children.length,2);assert.equal(e.context.children[0].textContent,'白板：神鹤万心');assert.equal(e.context.children[1].dataset.kind,'dirty');assert.equal(e.controller.clearIf('其他文本'),false);assert.equal(e.input.value,'保留我');assert.equal(e.controller.clearIf('保留我'),true);assert.equal(e.input.value,'');
  assert.equal(e.contextSummary.textContent,'神鹤万心 · 有未保存修改');assert.match(e.contextSummary.title,/白板：神鹤万心/);
});

test('optimistic echo remains unavailable without an explicit Host client-request identity capability',()=>{
  const Composer=load();assert.equal(Composer.prepareOptimisticEcho({clientRequestId:'request-1',message:'hello'}),null);assert.equal(Composer.prepareOptimisticEcho({hostSupportsClientRequestId:true,message:'hello'}),null);
  assert.deepEqual({...Composer.prepareOptimisticEcho({hostSupportsClientRequestId:true,clientRequestId:'request-1',message:'hello'})},{kind:'local_user_echo',clientRequestId:'request-1',message:'hello',status:'awaiting_host_acceptance'});
});
