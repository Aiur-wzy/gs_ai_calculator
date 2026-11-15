const test=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const vm=require('node:vm');

class Node {
  constructor(tag='div'){this.tagName=tag;this.children=[];this.dataset={};this.attributes={};this.hidden=false;this.text='';}
  append(...items){this.children.push(...items);}appendChild(item){this.children.push(item);return item;}
  replaceChildren(...items){this.children=[...items];this.text='';}
  setAttribute(key,value){this.attributes[key]=String(value);}
  set textContent(value){this.text=String(value);this.children=[];}
  get textContent(){return this.text+this.children.map(item=>item.textContent||'').join('');}
}

function load(){
  const nodes=new Map(),document={createElement:tag=>new Node(tag),getElementById:id=>nodes.get(id)||null,querySelector:selector=>selector.startsWith('#')?nodes.get(selector.slice(1))||null:null};
  for(const id of ['status-bar','workspace-notice-count','workspace-notice-list','workspace-attention-shelf','workspace-attention-message','workspace-attention-count'])nodes.set(id,new Node());
  const world={window:null,document,console,DS:{prose:value=>String(value)}};world.window=world;vm.createContext(world);
  vm.runInContext(fs.readFileSync(path.join(__dirname,'../src/genshin_app/gui/web/workspace-kernel.js'),'utf8'),world,{filename:'workspace-kernel.js'});
  return {world,nodes};
}

test('command registry is the single fresh preflight and execution authority',async()=>{
  const {world}=load(),reports=[],state={ready:false},calls=[];
  const registry=world.AppWorkspaceKernel.createCommandRegistry({report:record=>reports.push(record)});
  registry.register({id:'document.save',group:'file',order:2,label:context=>context.ready?'保存':'保存（不可用）',enabled:context=>context.ready,disabledReason:'没有可保存的文档',run:context=>calls.push(context.revision)});
  registry.register({id:'document.open',group:'file',order:1,label:'打开',run:()=>calls.push('open')});
  assert.throws(()=>registry.register({id:'document.open',run(){}}),/command_id_duplicate/);
  assert.deepEqual(Array.from(registry.list(state),item=>item.id),['document.open','document.save']);
  assert.equal(registry.resolve('document.save',state).enabled,false);
  assert.equal((await registry.invoke('document.save',state)).status,'disabled');
  assert.equal(reports[0].message,'没有可保存的文档');assert.deepEqual(calls,[]);
  state.ready=true;state.revision=7;
  const result=await registry.invoke('document.save',state);assert.equal(result.status,'success');assert.deepEqual(calls,[7]);
});

test('status center replaces equal facts, bounds history and keeps local errors out of the footer',()=>{
  const {world,nodes}=load(),center=world.AppWorkspaceKernel.createStatusCenter({document:world.document,limit:4});
  center.publish({factKey:'save',kind:'info',message:'正在保存'});
  center.publish({factKey:'save',kind:'success',message:'保存完成',details:{receipt:'r1'}});
  assert.equal(center.snapshot().length,1);assert.equal(center.snapshot()[0].message,'保存完成');
  assert.equal(nodes.get('status-bar').textContent,'保存完成');assert.equal(nodes.get('workspace-notice-count').textContent,'1 条动态');
  center.publish({factKey:'field.name',kind:'error',message:'名称不能为空',projectToFooter:false});
  assert.equal(nodes.get('status-bar').textContent,'保存完成');assert.equal(center.snapshot().length,2);
  for(let index=0;index<5;index++)center.publish({factKey:`task.${index}`,message:`任务 ${index}`});
  assert.equal(center.snapshot().length,4);assert.equal(center.snapshot()[0].message,'任务 4');
});

test('attention shelf exposes one highest-priority item and a compact remainder count',()=>{
  const {world,nodes}=load(),shelf=world.AppWorkspaceKernel.createAttentionShelf({document:world.document});
  shelf.set([{message:'普通提示',priority:1},{message:'必须先保存',priority:9},{message:'另一个提示',priority:2}]);
  assert.equal(nodes.get('workspace-attention-shelf').hidden,false);
  assert.equal(nodes.get('workspace-attention-message').textContent,'必须先保存');
  assert.equal(nodes.get('workspace-attention-count').textContent,'另有 2 项');
  shelf.clear();assert.equal(nodes.get('workspace-attention-shelf').hidden,true);
});

test('desktop shell owns one page chrome and loads its kernel before workspace integration',()=>{
  const html=fs.readFileSync(path.join(__dirname,'../src/genshin_app/gui/web/index.html'),'utf8');
  assert.equal((html.match(/id="workspace-page-chrome"/g)||[]).length,1);
  assert.equal((html.match(/id="workspace-quick"/g)||[]).length,1);
  assert.ok(html.indexOf('workspace-kernel.js')<html.indexOf('workspace.js'));
  const nav=html.slice(html.indexOf('<nav class="workspace-nav"'),html.indexOf('</nav>')+6);
  assert.doesNotMatch(nav,/workspace-quick/);
});
