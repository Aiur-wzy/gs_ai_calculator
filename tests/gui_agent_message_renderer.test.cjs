'use strict';
const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');

class Node {
  constructor(tag='#text',value=''){this.tagName=tag;this.children=[];this.attributes={};this.dataset={};this.listeners={};this._text=value;}
  append(...rows){this.children.push(...rows);} replaceChildren(...rows){this.children=[...rows];this._text='';}
  set textContent(value){this.children=[];this._text=String(value);} get textContent(){return this._text+this.children.map(row=>row.textContent).join('');}
  setAttribute(key,value){this.attributes[key]=String(value);} addEventListener(key,fn){this.listeners[key]=fn;}
  async emit(key){return this.listeners[key]?.({preventDefault(){}});}
}
function load(){const document={createElement:tag=>new Node(tag),createTextNode:value=>new Node('#text',String(value))},world={document,URL,location:{href:'https://app.invalid/'},navigator:{}};world.window=world;vm.createContext(world);vm.runInContext(fs.readFileSync(path.join(__dirname,'../src/genshin_app/gui/web/agent-message-renderer.js'),'utf8'),world);const all=node=>[node,...node.children.flatMap(all)];return {world,document,all};}

test('safe markdown creates text nodes and never executes raw html or unsafe links',()=>{
  const {world,document,all}=load(),root=world.AgentMessageRenderer.render(document,'<img src=x onerror=alert(1)>\n\n[本地](file:///C:/secret) [相对](docs/help) [凭据](https://user:pass@example.com/a) [网页](https://example.com/a)');
  assert.match(root.textContent,/<img src=x onerror=alert\(1\)>/);assert.equal(all(root).some(node=>node.tagName==='img'),false);
  const links=all(root).filter(node=>node.tagName==='a');assert.equal(links.length,1);assert.equal(links[0].href,'https://example.com/a');assert.equal(links[0].rel,'noopener noreferrer');assert.match(root.textContent,/\[本地\]\(file:/);assert.match(root.textContent,/\[相对\]\(docs\/help\)/);assert.match(root.textContent,/\[凭据\]/);
});

test('renderer supports fenced code, bounded tables, lists and inline emphasis',()=>{
  const {world,document,all}=load(),source='# 标题\n\n| 项 | 值 |\n| --- | ---: |\n| A | `1` |\n\n- **一**\n- 二\n\n```json\n{"ok":true}\n```',root=world.AgentMessageRenderer.render(document,source),nodes=all(root);
  assert.equal(nodes.some(node=>node.tagName==='table'),true);assert.equal(nodes.some(node=>node.tagName==='ul'),true);assert.equal(nodes.some(node=>node.tagName==='pre'),true);assert.equal(nodes.some(node=>node.tagName==='code'&&node.dataset.language==='json'),true);assert.equal(nodes.some(node=>node.tagName==='strong'&&node.textContent==='一'),true);
});

test('message copy uses the exact bounded source and exposes no business state',async()=>{
  const {world,document}=load();let copied='';const card=world.AgentMessageRenderer.createMessage(document,{role:'assistant',text:'答案 **完成**',copyText:async value=>copied=value});
  await card.copy.emit('click');assert.equal(copied,'答案 **完成**');assert.equal(card.copy.textContent,'已复制');assert.equal(card.getText(),copied);assert.equal(Object.hasOwn(card,'session'),false);
  card.setText('更新');assert.equal(card.getText(),'更新');assert.equal(card.body.textContent,'更新');
});

test('streaming text stays plain until the terminal projection promotes markdown once',()=>{
  const {world,document}=load(),card=world.AgentMessageRenderer.createMessage(document,{role:'assistant',text:'**未完',streaming:true});
  assert.equal(card.article.dataset.streaming,'true');assert.equal(card.article.attributes['aria-busy'],'true');assert.equal(card.body.attributes['aria-live'],'off');assert.equal(card.body.children[0].tagName,'pre');assert.equal(card.body.textContent,'**未完');
  card.setText('**完成**',{streaming:false});assert.equal(card.article.dataset.streaming,'false');assert.equal(card.article.attributes['aria-busy'],'false');assert.equal(card.body.children[0].tagName,'div');assert.equal(card.body.textContent,'完成');
});

test('reasoning is a separate collapsible disclosure for live and historical text',()=>{
  const {world,document,all}=load(),card=world.AgentMessageRenderer.createMessage(document,{role:'assistant',text:'先核对资料',streaming:true,reasoning:true});
  assert.equal(card.disclosure.tagName,'details');assert.equal(card.disclosure.open,true);assert.match(card.article.textContent,/推理过程.*正在推理.*5 字符/s);assert.doesNotMatch(card.article.textContent,/Agent/);
  card.setText('先核对资料，再计算',{streaming:false});assert.equal(card.disclosure.open,false);assert.match(card.article.textContent,/推理过程.*9 字符/s);assert.equal(all(card.article).some(node=>node.tagName==='summary'),true);assert.equal(card.body.textContent,'先核对资料，再计算');
  const history=world.AgentMessageRenderer.createMessage(document,{role:'assistant',text:'历史推理',reasoning:true});assert.equal(history.disclosure.open,undefined);assert.match(history.article.textContent,/推理过程.*4 字符/s);
});

test('oversized messages are truncated with an explicit marker',()=>{
  const {world,document}=load(),limit=world.AgentMessageRenderer.MAX_MESSAGE_CHARS,root=world.AgentMessageRenderer.render(document,'x'.repeat(limit+100));
  assert.match(root.textContent,/内容过长/);assert.ok(root.textContent.length<=limit+100);
});

test('external links require explicit confirmation before reviewed open',async()=>{
  const {world,document,all}=load();let approved=false,opened=[];
  const root=world.AgentMessageRenderer.render(document,'[资料](https://example.com/source)',{
    confirmExternal:async request=>{assert.equal(request.url,'https://example.com/source');return approved;},
    openExternal:async request=>opened.push(request.url),
  });
  const link=all(root).find(node=>node.tagName==='a');
  await link.emit('click');assert.equal(link.dataset.openState,'denied');assert.deepEqual(opened,[]);
  approved=true;await link.emit('click');assert.equal(link.dataset.openState,'opened');assert.deepEqual(opened,['https://example.com/source']);
});

test('each fenced code block has an independent exact copy action',async()=>{
  const {world,document,all}=load();const copied=[];
  const root=world.AgentMessageRenderer.render(document,'```json\n{"ok":true}\n```\n\n```\n第二段\n```',{copyCode:async value=>copied.push(value)});
  const buttons=all(root).filter(node=>node.tagName==='button'&&node.attributes['aria-label']==='复制此代码块');
  assert.equal(buttons.length,2);await buttons[1].emit('click');assert.deepEqual(copied,['第二段']);assert.equal(buttons[1].textContent,'已复制');
});
