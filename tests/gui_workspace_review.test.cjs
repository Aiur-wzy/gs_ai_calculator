const GUI_TEST_RUNTIME = require('./helpers/gui_runtime.cjs');
// Independent review of file commands used by the desktop workspace shell.
const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');
const helpers={module:{exports:{}},require,__dirname,console,setImmediate};
vm.runInNewContext(fs.readFileSync(path.join(__dirname,'gui_equipment.test.cjs'),'utf8').split('const mechanicsCaps=')[0]
  +'\nmodule.exports={setup,all};',helpers);
const {setup,all}=helpers.module.exports,clone=value=>JSON.parse(JSON.stringify(value));
function environment(kind){const env=setup();
  if(kind==='legacy'){const template=clone(env.RT.tpl());env.PL.state.plan=null;env.PL.state.fileSha=null;
    env.RT.replaceTemplate(template,'plans/manual.json');env.PL.state.savedJson=env.PL.currentInputJson();}
  return env;
}
function deferredSave(env,kind){let finish,submitted;env.world.pywebview.api[kind==='legacy'?'save_template':'save_plan']=(...args)=>{
  submitted=args;return new Promise(resolve=>{finish=resolve;});};return {get submitted(){return submitted;},finish:path=>finish({code:0,data:{path,file_sha:'saved-sha'}})};}
function button(env,label){const item=all(env.world.document.body).find(node=>node.tagName==='button'&&node.textContent===label);
  assert.ok(item,`Expected ${label}`);return item;
}

test('resolved loader keeps its own exact SHA without borrowing native file identity',async()=>{
  const env=environment('legacy'),template=clone(env.RT.tpl());
  env.world.pywebview.api.inspect_plan_file=async()=>({code:0,data:{kind:null}});
  env.world.pywebview.api.load_template=async()=>({code:0,data:{template,path:'external.json',file_sha:'loaded-resolved-sha'}});
  assert.equal(await env.RT.loadTemplate('external.json'),true);
  assert.equal(env.RT.state.fileSha,'loaded-resolved-sha');assert.equal(env.PL.state.fileSha,null);
});

test('resolved saves compare the loaded SHA only when writing the current path',async()=>{
  const env=environment('legacy'),calls=[];env.RT.state.fileSha='loaded-sha';
  env.world.pywebview.api.save_template=async(...args)=>{calls.push(args);return {code:0,data:{path:args[0],file_sha:'saved-sha'}};};
  env.nodes.get('#tpl-path').value=env.RT.state.path;assert.equal(await env.RT.saveTemplate(),true);
  assert.equal(calls[0][2],'loaded-sha');assert.equal(env.RT.state.fileSha,'saved-sha');
  env.nodes.get('#tpl-path').value='new-copy.json';assert.equal(await env.RT.saveTemplate(),true);assert.equal(calls[1][2],null);
});

for(const kind of ['legacy','native'])test(`${kind} saves a snapshot and keeps edits made during writing dirty`,async()=>{
  const env=environment(kind),{RT,PL}=env,before=PL.currentInputJson(),io=deferredSave(env,kind);
  const pending=RT.saveTemplate();RT.tpl().meta.name='写盘期间新增的修改';RT.syncRevision();
  const submitted=kind==='legacy'?io.submitted[1]:io.submitted[1].manual_template;
  assert.notEqual(submitted.meta.name,'写盘期间新增的修改');
  io.finish('plans/saved.json');assert.equal(await pending,true);
  assert.equal(PL.state.savedJson,before);assert.equal(PL.dirty(),true);assert.equal(RT.state.path,'plans/saved.json');
});

test('legacy save cannot call a reverted original input clean after a different snapshot reached disk',async()=>{
  const env=environment('legacy'),{RT,PL}=env,original=RT.tpl().meta.name;
  RT.tpl().meta.name='将要写盘的内容';RT.syncRevision();const saved=PL.currentInputJson(),io=deferredSave(env,'legacy');
  const pending=RT.saveTemplate();RT.tpl().meta.name=original;RT.syncRevision();io.finish('plans/manual.json');await pending;
  assert.equal(PL.state.savedJson,saved);assert.equal(PL.dirty(),true);
});

for(const kind of ['legacy','native'])test(`${kind} save followed by switching document never overwrites its new identity`,async()=>{
  const env=environment(kind),{RT,PL}=env,io=deferredSave(env,kind),pending=RT.saveTemplate();
  if(kind==='native'){const sameId=clone(PL.state.plan);sameId.manual_template.meta.name='重新载入的同标识方案';
    PL.replace(sameId,'plans/reloaded.json','new-sha',true);}
  else{const replacement=clone(RT.tpl());replacement.meta.name='另一个文档';RT.replaceTemplate(replacement,'plans/reloaded.json');
    PL.state.savedJson=PL.currentInputJson();}
  const saved=PL.state.savedJson;io.finish('plans/old-write.json');assert.equal(await pending,false);
  assert.equal(RT.state.path,'plans/reloaded.json');assert.equal(env.nodes.get('#tpl-path').value,'plans/reloaded.json');
  assert.equal(PL.state.savedJson,saved);assert.equal(PL.dirty(),false);
  if(kind==='native')assert.equal(PL.state.fileSha,'new-sha');
});

for(const kind of ['legacy','native'])test(`${kind} save exposes the real disk acknowledgement separately from current document eligibility`,async()=>{
  const env=environment(kind),io=deferredSave(env,kind),written=[];
  const pending=env.RT.saveTemplate({onWritten:value=>written.push(value)});
  const replacement=clone(env.RT.tpl());replacement.meta.name='new document';env.PL.state.plan=null;env.RT.replaceTemplate(replacement,'new-document.json');
  io.finish('actual-saved-copy.json');assert.equal(await pending,false);assert.equal(written.length,1);
  assert.equal(written[0].path,'actual-saved-copy.json');assert.equal(written[0].current,false);assert.equal(env.RT.state.path,'new-document.json');
});

for(const kind of ['legacy','native'])test(`${kind} save responses arriving out of order retain the latest save identity`,async()=>{
  const env=environment(kind),{RT,PL}=env,finishes=[];
  env.world.pywebview.api[kind==='legacy'?'save_template':'save_plan']=()=>new Promise(resolve=>finishes.push(resolve));
  env.nodes.get('#tpl-path').value='plans/first.json';const first=RT.saveTemplate();
  RT.tpl().meta.name='第二次保存内容';RT.syncRevision();env.nodes.get('#tpl-path').value='plans/second.json';const second=RT.saveTemplate();
  finishes[1]({code:0,data:{path:'plans/second.json',file_sha:'second-sha'}});assert.equal(await second,true);
  finishes[0]({code:0,data:{path:'plans/first.json',file_sha:'first-sha'}});assert.equal(await first,false);
  assert.equal(RT.state.path,'plans/second.json');assert.equal(env.nodes.get('#tpl-path').value,'plans/second.json');assert.equal(PL.dirty(),false);
  if(kind==='native')assert.equal(PL.state.fileSha,'second-sha');
});

for(const kind of ['legacy','native'])test(`${kind} save-and-leave does not approve leaving when writing missed a later edit`,async()=>{
  const env=environment(kind),{RT,PL}=env;RT.tpl().meta.name='需要保存';RT.syncRevision();const io=deferredSave(env,kind);
  let resolved=false;const leaving=PL.leave().then(value=>{resolved=true;return value;});const saving=button(env,'保存后继续').emit('click');
  RT.tpl().meta.name='保存期间的新内容';RT.syncRevision();io.finish('plans/p.json');await saving;
  assert.equal(PL.dirty(),true);assert.equal(resolved,false);button(env,'继续编辑').emit('click');assert.equal(await leaving,false);
});

test('plan leave cancellation is idempotent and preserves the dirty draft',async()=>{
  const env=environment('native'),{RT,PL}=env;RT.tpl().meta.name='需要保留的修改';RT.syncRevision();
  const leaving=PL.leave(),dialog=env.world.document.body.children.filter(node=>node.tagName==='dialog').at(-1);dialog.emit('cancel');dialog.emit('cancel');
  assert.equal(await leaving,false);assert.equal(PL.dirty(),true);assert.equal(env.world.AppDialogKernel.getActive(),null);
});

test('plan dialog refusal returns a cancelled decision instead of replacing the active draft window',async()=>{
  const env=environment('native'),{RT,PL}=env;RT.tpl().meta.name='需要保留的修改';RT.syncRevision();
  const blocker=env.world.AppDialogKernel.create({title:'正在编辑',onRequestClose:()=>false});
  assert.equal(await PL.leave(),false);assert.equal(env.world.AppDialogKernel.getActive(),blocker);blocker.destroy();
});

test('closing plan leave while save is pending never changes the cancelled navigation decision',async()=>{
  const env=environment('native'),{RT,PL}=env;RT.tpl().meta.name='异步保存内容';RT.syncRevision();const io=deferredSave(env,'native');
  const leaving=PL.leave(),dialog=env.world.document.body.children.filter(node=>node.tagName==='dialog').at(-1),saving=button(env,'保存后继续').emit('click');
  dialog.emit('cancel');io.finish('plans/async-saved.json');await saving;
  assert.equal(await leaving,false);assert.equal(env.world.AppDialogKernel.getActive(),null);assert.equal(RT.state.path,'plans/async-saved.json');
});

for(const kind of ['legacy','native'])test(`${kind} failed newer save does not discard an earlier successful disk write`,async()=>{
  const env=environment(kind),{RT,PL}=env,finishes=[];
  env.world.pywebview.api[kind==='legacy'?'save_template':'save_plan']=()=>new Promise(resolve=>finishes.push(resolve));
  RT.tpl().meta.name='实际写盘内容';RT.syncRevision();const saved=PL.currentInputJson(),first=RT.saveTemplate();
  RT.tpl().meta.name='第二份尚未写盘的内容';RT.syncRevision();const second=RT.saveTemplate();
  finishes[1]({code:1,message:'文件已被更新'});assert.equal(await second,false);
  finishes[0]({code:0,data:{path:RT.state.path,file_sha:'written-sha'}});assert.equal(await first,true);
  assert.equal(PL.state.savedJson,saved);assert.equal(PL.dirty(),true);
  if(kind==='native')assert.equal(PL.state.fileSha,'written-sha');
});

for(const kind of ['legacy','native'])test(`${kind} failed save preserves file path and never marks input saved`,async()=>{
  const env=environment(kind),{RT,PL}=env,oldPath=RT.state.path,oldSaved=PL.state.savedJson;RT.tpl().meta.name='未写盘';RT.syncRevision();
  env.world.pywebview.api[kind==='legacy'?'save_template':'save_plan']=async()=>({code:1,message:'无法写入'});
  assert.equal(await RT.saveTemplate(),false);assert.equal(RT.state.path,oldPath);assert.equal(PL.state.savedJson,oldSaved);assert.equal(PL.dirty(),true);
});

function workspace(){
  const nodes=new Map(),listeners=new Map(),calls=[],stored=new Map();let tab='whiteboard',modal=false;
  class Node {
    constructor(tag='div'){this.tagName=tag;this.children=[];this.dataset={};this.listeners={};this.style={setProperty(){}};this.classList={add(){},remove(){},toggle(){}};this.value='';}
    append(...items){items.forEach(item=>{item.parent=this;this.children.push(item);});}appendChild(item){this.append(item);return item;}
    replaceChildren(...items){this.children=[];this.append(...items);}setAttribute(key,value){(this.attributes??={})[key]=value;}
    addEventListener(type,fn){this.listeners[type]=fn;}emit(type,event={}){return this.listeners[type]?.({target:this,preventDefault(){},...event});}
    click(){return this.emit('click');}focus(){}showModal(){this.open=true;this.showModalCalls=(this.showModalCalls||0)+1;}close(){this.open=false;}
    remove(){if(this.parent)this.parent.children=this.parent.children.filter(item=>item!==this);}
    querySelector(selector){return this.querySelectorAll(selector)[0]||null;}
    querySelectorAll(selector){const descendants=node=>node.children.flatMap(child=>[child,...descendants(child)]),matches=(node,part)=>
      part.startsWith('#')?node.id===part.slice(1):part.startsWith('.')?String(node.className||'').split(' ').includes(part.slice(1)):
      part.startsWith('[data-resize-side=')?node.dataset.resizeSide===part.match(/"([^"]+)"/)[1]:false;
      return descendants(this).filter(node=>selector.split(',').some(part=>matches(node,part)));}
    set textContent(value){this.text=String(value);this.children=[];}get textContent(){return(this.text||'')+this.children.map(item=>item.textContent).join('');}
  }
  const body=new Node('body'),world={console,Math,localStorage:{getItem:key=>stored.get(key),setItem:(key,value)=>stored.set(key,value)},
    addEventListener:(type,fn)=>listeners.set(type,fn),setStatus:message=>calls.push(['status',message]),DS:{prose:value=>value}};
  world.window=world;world.document={body,createElement:tag=>new Node(tag),getElementById:id=>nodes.get(id)||null,
    querySelector:selector=>selector==='.tab.active'?{dataset:{tab}}:selector==='dialog[open]'?(modal?{}:null):
      /^\.tab\[data-tab="/.test(selector)?{click(){const name=selector.match(/"([^"]+)"/)[1];calls.push(['navigate',name]);tab=name;}}:null,
    querySelectorAll:()=>[],addEventListener(){}};
  world.RT={tpl:()=>world.RT.state.template,state:{plansDir:'plans',path:'old.json',template:{meta:{name:'待检验方案'}}},saveTemplate:()=>calls.push(['save-plan'])};
  world.PL={dirty:()=>false,startNew(){calls.push(['new-plan']);},begin:()=>calls.push(['equipment-plan'])};
  world.RS={state:{pending:false},runCalc:()=>calls.push(['calculate'])};world.TV={changeMode:mode=>calls.push(['plan-mode',mode])};
  world.WB={json:()=>JSON.stringify(world.WB.state.board),pickUserFile:async()=>({cancelled:false,path:'D:/中文路径/草稿.whiteboard.json'}),state:{board:{title:'白板'},path:'existing.whiteboard.json',selectedActor:'actor'},save:()=>calls.push(['save-board']),
    setMode:mode=>calls.push(['board-mode',mode]),handoff:()=>calls.push(['handoff']),importResult:()=>calls.push(['return']),
    load:async path=>{calls.push(['load-board',path]);return true;}};
  vm.createContext(world);for(const file of ['ui.js','workspace-kernel.js','workspace.js'])GUI_TEST_RUNTIME.loadWebFile(world, path.join(__dirname,'../src/genshin_app/gui/web',file), {filename:file});
  nodes.set('workspace-menubar',new Node());
  for(const id of ['workspace-page-chrome','workspace-page-title','workspace-document-summary','workspace-status-summary','workspace-notice-count','workspace-notice-list','workspace-attention-shelf','workspace-attention-message','workspace-attention-count','workspace-quick','status-bar'])nodes.set(id,new Node());
  return {world,WS:world.WS,calls,nodes,listeners,stored,body,Node,setTab:value=>{tab=value;},setModal:value=>{modal=value;}};
}

test('workspace actions are evaluated against the current page rather than the previously opened menu',async()=>{
  const env=workspace(),{WS,calls}=env;
  const oldCommand=WS.commands().flatMap(item=>item.items).find(item=>item?.id==='calculate');assert.equal(oldCommand.enabled,false);
  await WS.invoke('save');assert.deepEqual(calls,[['save-board']]);calls.length=0;
  env.setTab('rotation');await WS.invoke('save');await WS.invoke('calculate');assert.deepEqual(calls,[['save-plan'],['calculate']]);
  env.setTab('whiteboard');calls.length=0;assert.equal(await WS.invoke('calculate'),false);assert.ok(calls.every(item=>item[0]!=='calculate'));
  assert.equal(await WS.invoke('export-json'),false);
});

for(const kind of ['whiteboard','native','template'])test(`first user Save uses Save As for ${kind} and cancellation never writes`,async()=>{
  const env=workspace();if(kind!=='whiteboard')env.setTab('rotation');
  env.world.WB.state.path='';env.world.RT.state.path='';env.world.PL.state={plan:kind==='native'?{plan_id:'native'}:null};
  let picks=0;env.WS.saveAs=async()=>{picks++;return false;};
  await env.WS.invoke('save');
  assert.equal(picks,1);assert.ok(env.calls.every(row=>!['save-board','save-plan'].includes(row[0])));
  assert.equal(env.world.WB.state.path,'');assert.equal(env.world.RT.state.path,'');
});

for(const kind of ['whiteboard','native','template'])test(`existing ${kind} Save commits once without opening another picker`,async()=>{
  const env=workspace();if(kind!=='whiteboard')env.setTab('rotation');
  env.world.PL.state={plan:kind==='native'?{plan_id:'native'}:null};
  env.WS.saveAs=()=>{throw new Error('A current file must retain its existing SHA-guarded saver');};
  await env.WS.invoke('save');
  assert.deepEqual(env.calls,[kind==='whiteboard'?['save-board']:['save-plan']]);
});

test('workspace permits no hidden plan writes while browsing the data library',async()=>{
  const env=workspace();env.setTab('data');for(const id of ['save','save-as','equipment','calculate','axis-effects','handoff'])assert.equal(await env.WS.invoke(id),false,id);
  assert.ok(env.calls.every(item=>item[0]==='status'));
});

test('workspace shortcuts respect modal input and switch only the active axis',()=>{
  const env=workspace();env.WS.init();const key=env.listeners.get('keydown');env.setModal(true);
  key({key:'s',ctrlKey:true,preventDefault(){throw new Error('Modal shortcut must remain untouched');}});assert.equal(env.calls.length,0);
  env.setModal(false);key({key:'2',ctrlKey:true,preventDefault(){}});assert.deepEqual(env.calls,[['board-mode','effects']]);
  env.setTab('rotation');key({key:'3',ctrlKey:true,preventDefault(){}});assert.deepEqual(env.calls.at(-1),['plan-mode','damage']);
});

test('workspace layout controls preserve independent view preferences without saving document inputs',()=>{
  const env=workspace();env.WS.preference('whiteboard');env.WS.preference('rotation');
  env.WS.toggle('whiteboard','right');assert.equal(env.WS.preference('whiteboard').rightHidden,true);
  assert.equal(env.WS.preference('rotation').rightHidden,false);env.WS.reveal('whiteboard','right');assert.equal(env.WS.preference('whiteboard').rightHidden,false);
  env.WS.resizePane('whiteboard','left',99999);assert.equal(env.WS.preference('whiteboard').left,340);
  env.WS.resizePane('rotation','right',-10);assert.equal(env.WS.preference('rotation').right,230);
  env.WS.resizePane('rotation','right',Infinity);assert.equal(env.WS.preference('rotation').right,230);
  assert.equal(env.calls.length,0);assert.ok(env.stored.has('genshin.workspace.v1'));
});

test('workspace native picker routes inspected whiteboards without touching native loader',async()=>{
 const env=workspace();env.world.callApi=async(method,path)=>{env.calls.push([method,path]);return {code:0,data:{kind:'genshin-whiteboard'}};};
 env.world.RT.loadTemplate=()=>{throw new Error('Native draft must remain untouched');};
 assert.equal(await env.WS.openFile(),true);assert.deepEqual(env.calls.slice(0,2),[['inspect_plan_file','D:/中文路径/草稿.whiteboard.json'],['load-board','D:/中文路径/草稿.whiteboard.json']]);
 assert.equal(env.world.AppDialogKernel.getActive(),null);
});
test('workspace cancelled loader does not reopen a path form or report completion',async()=>{
 const env=workspace(),results=[];env.world.callApi=async()=>({code:0,data:{kind:'genshin-whiteboard'}});env.world.WB.load=async()=>false;
 assert.equal(await env.WS.openFile({onAgentActionResult:result=>results.push(result)}),false);assert.equal(results[0].status,'cancelled');assert.equal(env.world.AppDialogKernel.getActive(),null);
});
test('native file cancellation settles callback once and never inspects or loads a file',async()=>{
 const env=workspace(),results=[];env.world.WB.pickUserFile=async()=>({cancelled:true,path:null});env.world.callApi=()=>{throw new Error('Cancelled picker must not call loader');};
 assert.equal(await env.WS.openFile({onAgentActionResult:result=>results.push(result)}),false);assert.equal(results.length,1);assert.equal(results[0].status,'cancelled');
});

test('workspace cancelled new-document guards preserve the originating work area',async()=>{
  const env=workspace();env.world.PL.startNew=async()=>false;env.world.RT.newTemplate=async()=>false;
  await env.WS.invoke('new-plan');await env.WS.invoke('new-template');assert.equal(env.WS.tab(),'whiteboard');
  assert.ok(env.calls.every(item=>item[0]!=='navigate'));
  env.world.PL.startNew=async()=>{env.world.RT.state.template={meta:{name:'确实新建'}};};
  await env.WS.invoke('new-plan');assert.equal(env.WS.tab(),'rotation');
});

test('workspace rejects advanced event creation when no editable document is open',async()=>{
  const env=workspace();env.setTab('rotation');env.world.RT.state.template=null;
  for(const id of ['add-hit','add-buff','add-switch'])env.nodes.set(`btn-${id}`,new env.Node('button'));
  for(const id of ['add-hit','add-buff','add-switch'])assert.equal(await env.WS.invoke(id),false,id);
  assert.ok(env.calls.every(item=>item[0]==='status'));
});

test('workspace save shortcut commits the focused input before collecting its snapshot',()=>{
  const env=workspace();env.WS.init();env.world.document.activeElement={blur(){env.calls.push(['commit-input']);}};
  env.listeners.get('keydown')({key:'s',ctrlKey:true,preventDefault(){}});
  assert.deepEqual(env.calls,[['commit-input'],['save-board']]);
});

test('workspace remount restores cleared pane controls without accumulating resize handles',()=>{
  const env=workspace(),root=new env.Node(),left=new env.Node(),right=new env.Node();root.clientWidth=1280;left.id='left';right.id='right';root.append(left,right);
  const options={key:'rotation',left:'#left',right:'#right',repaint(){}};
  env.WS.mount(root,options);const original=root.querySelectorAll('.ws-resizer');assert.equal(original.length,2);
  env.WS.mount(root,options);assert.equal(root.querySelectorAll('.ws-resizer')[0],original[0]);
  left.replaceChildren();env.WS.mount(root,options);
  assert.equal(root.querySelectorAll('.ws-resizer').length,2);assert.equal(root.querySelectorAll('.ws-pane-close').length,2);
  assert.ok(left.querySelector('.ws-pane-close'));assert.ok(right.querySelector('.ws-pane-close'));assert.equal(env.calls.length,0);
});

test('shared page chrome keeps one title, at most two direct actions and one overflow entry',()=>{
  const env=workspace();env.WS.init();const quick=env.nodes.get('workspace-quick');
  assert.equal(env.nodes.get('workspace-page-title').textContent,'创作白板');
  assert.match(env.nodes.get('workspace-document-summary').textContent,/白板/);
  assert.ok(quick.children.length<=3);assert.ok(quick.children.some(item=>item.dataset.command==='add-action'));
  env.world.WB.state.board=null;env.WS.refresh();assert.deepEqual(quick.children.map(item=>item.dataset.command),['new-board','open']);
  env.world.WB.state.board={title:'白板'};env.setTab('rotation');env.world.PL.dirty=()=>true;env.WS.refresh();
  assert.equal(env.nodes.get('workspace-page-title').textContent,'高级编辑');assert.equal(quick.children.length,3);
  assert.deepEqual(quick.children.slice(0,2).map(item=>item.dataset.command),['save','calculate']);
  assert.equal(quick.children[2].textContent,'更多');
});
