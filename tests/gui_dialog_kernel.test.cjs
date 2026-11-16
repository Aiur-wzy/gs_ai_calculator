'use strict';

const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');

function target(value={}){
  value.listeners={};value.addEventListener=(type,handler)=>(value.listeners[type]||=[]).push(handler);
  value.removeEventListener=(type,handler)=>{value.listeners[type]=(value.listeners[type]||[]).filter(item=>item!==handler);};
  value.emit=(type,init={})=>{const event={target:value,currentTarget:value,defaultPrevented:false,preventDefault(){this.defaultPrevented=true;},...init};
    for(const handler of value.listeners[type]||[])handler(event);return event;};return value;
}
class Element{
  constructor(tag,document){target(this);this.tagName=tag;this.ownerDocument=document;this.children=[];this.parentNode=null;this.attributes={};this.className='';this.id='';this.hidden=false;this.open=false;this.isConnected=false;this._text='';}
  append(...children){for(const child of children){child.parentNode=this;child.setConnected?.(this.isConnected);this.children.push(child);}}
  replaceChildren(...children){this.children=[];this.append(...children);}
  setConnected(value){this.isConnected=value;for(const child of this.children)child.setConnected?.(value);}
  remove(){if(this.parentNode)this.parentNode.children=this.parentNode.children.filter(child=>child!==this);this.parentNode=null;this.setConnected(false);}
  contains(target){for(let node=target;node;node=node.parentNode)if(node===this)return true;return false;}
  setAttribute(name,value){this.attributes[name]=String(value);}
  removeAttribute(name){delete this.attributes[name];}
  getAttribute(name){return Object.hasOwn(this.attributes,name)?this.attributes[name]:null;}
  hasAttribute(name){return Object.hasOwn(this.attributes,name);}
  get tabIndex(){return this.hasAttribute('tabindex')?Number(this.attributes.tabindex):['button','input','select','textarea','a','summary'].includes(this.tagName)?0:-1;}
  set tabIndex(value){this.attributes.tabindex=String(value);}
  focus(){this.ownerDocument.activeElement=this;this.focused=true;}
  showModal(){this.open=true;}
  close(){this.open=false;}
  set textContent(value){this._text=String(value);this.children=[];}
  get textContent(){return this._text+this.children.map(child=>child.textContent).join('');}
}
const all=node=>[node,...node.children.flatMap(all)];
function setup(){
  const document=target({activeElement:null,hidden:false}),body=new Element('body',document);body.setConnected(true);document.body=body;document.createElement=tag=>new Element(tag,document);
  // The shipped business root and modal portal are siblings. Inert must not
  // accidentally cover the modal itself in this lightweight DOM fixture.
  const app=document.createElement('main'),portal=document.createElement('aside');app.id='genshin-application-root';portal.id='app-overlay-root';body.append(app,portal);
  document.getElementById=id=>all(body).find(node=>node.id===id)||null;
  const world=target({document,console,Math,Date,JSONTransport:{clone:value=>JSON.parse(JSON.stringify(value))},setStatus(){}});world.window=world;world.visualViewport=target({});document.defaultView=world;vm.createContext(world);
  vm.runInContext(fs.readFileSync(path.join(__dirname,'../src/genshin_app/gui/web/gui-foundation-v2.js'),'utf8'),world,{filename:'gui-foundation-v2.js'});
  vm.runInContext(fs.readFileSync(path.join(__dirname,'../src/genshin_app/gui/web/ui.js'),'utf8'),world,{filename:'ui.js'});return {world,document,body};
}

test('dialog kernel keeps a draft open when cancellation is refused and restores focus after explicit close',()=>{
  const {world,document,body}=setup(),opener=document.createElement('button');body.append(opener);opener.focus();let permit=false;
  const dialog=world.AppDialogKernel.create({title:'编辑草稿',description:'未确认的更改仍在这里。',onRequestClose:()=>permit});
  assert.equal(dialog.open,true);assert.equal(dialog.children[0].className,'app-dialog-header');assert.equal(dialog.appBody.className,'app-dialog-body');
  assert.equal(dialog.appFooter.className,'app-dialog-footer');assert.equal(dialog.attributes['aria-labelledby'],dialog.children[0].children[0].id);
  const action=document.createElement('button');dialog.setActions(action);assert.equal(dialog.appFooter.hidden,false);assert.equal(dialog.appFooter.children[0],action);
  assert.equal(document.activeElement,action);
  const backdrop=dialog.emit('click',{target:dialog});assert.equal(backdrop.defaultPrevented,true);assert.equal(dialog.open,true);
  const refused=dialog.emit('cancel');assert.equal(refused.defaultPrevented,true);assert.equal(dialog.open,true);assert.equal(opener.focused,true);
  opener.focused=false;permit=true;dialog.emit('cancel');assert.equal(dialog.open,false);assert.equal(dialog.parentNode,null);assert.equal(document.activeElement,opener);assert.equal(opener.focused,true);
});

test('dialog kernel owns one modal, reports refused replacement and supports explicit busy completion',()=>{
  const {world,document,body}=setup(),opener=document.createElement('button');body.append(opener);opener.focus();let permit=false;
  const guarded=world.AppDialogKernel.create({title:'受保护的草稿',onRequestClose:()=>permit});
  const refused=world.AppDialogKernel.create({title:'不能覆盖'});
  assert.equal(refused,null);assert.equal(world.AppDialogKernel.getActive(),guarded);
  assert.equal(world.AppDialogKernel.getLastCreateResult().reason,'active_refused_close');
  permit=true;const replacement=world.AppDialogKernel.create({title:'替代弹窗'});
  assert.equal(guarded.parentNode,null);assert.equal(world.AppDialogKernel.getActive(),replacement);
  replacement.setBusy(true);const escape=replacement.emit('cancel');
  assert.equal(escape.defaultPrevented,true);assert.equal(replacement.open,true);
  assert.equal(world.AppDialogKernel.create({title:'忙碌时不能替代'}),null);
  assert.equal(world.AppDialogKernel.getLastCreateResult().reason,'active_busy');
  assert.equal(replacement.finish('completed'),true);assert.equal(replacement.finish('completed'),true);
  assert.equal(replacement.destroy(),true);assert.equal(world.AppDialogKernel.getActive(),null);
});

test('dialog kernel applies initial focus and traps tab navigation at both edges',()=>{
  const {world,document}=setup();let first,last;
  const dialog=world.AppDialogKernel.create({title:'键盘弹窗',initialFocus:current=>{
    first=document.createElement('button');last=document.createElement('button');current.appBody.append(first,last);return last;
  }});
  assert.equal(document.activeElement,last);
  const forward=dialog.emit('keydown',{key:'Tab'});assert.equal(forward.defaultPrevented,true);assert.equal(document.activeElement,first);
  const backward=dialog.emit('keydown',{key:'Tab',shiftKey:true});assert.equal(backward.defaultPrevented,true);assert.equal(document.activeElement,last);
  dialog.destroy();
});

test('dialog and transient surfaces share one coordinated owner and restore the original trigger',()=>{
  const {world,document,body}=setup(),trigger=document.createElement('button');body.append(trigger);trigger.focus();
  const popover=world.AppSurfaceKernel.create({trigger});assert.equal(world.AppSurfaceKernel.getActive(),popover);
  const dialog=world.AppDialogKernel.create({title:'统一生命周期'});
  assert.ok(dialog);assert.equal(popover.parentNode,null);assert.equal(trigger.getAttribute('aria-expanded'),'false');
  assert.equal(world.AppSurfaceKernel.getActive(),dialog);assert.equal(world.AppDialogKernel.getActive(),dialog);
  assert.equal(document.activeElement,dialog);
  document.emit('keydown',{key:'Escape',target:dialog});
  assert.equal(world.AppSurfaceKernel.getActive(),null);assert.equal(world.AppDialogKernel.getActive(),null);
  assert.equal(dialog.parentNode,null);assert.equal(document.activeElement,trigger);
});

test('busy modal refuses a competing surface and the shared host installs one escape owner',()=>{
  const {world,document,body}=setup(),trigger=document.createElement('button');body.append(trigger);trigger.focus();
  const dialog=world.AppDialogKernel.create({title:'处理中',busy:true});assert.ok(dialog);
  assert.equal((document.listeners.keydown||[]).length,1);
  const refused=world.AppSurfaceKernel.create({trigger,kind:'drawer'});
  assert.equal(refused,null);assert.equal(world.AppSurfaceKernel.getLastCreateResult().reason,'active_refused_close');
  assert.equal(world.AppSurfaceKernel.getActive(),dialog);assert.equal(world.AppDialogKernel.getActive(),dialog);
  document.emit('keydown',{key:'Escape',target:dialog});assert.equal(dialog.open,true);
  dialog.setBusy(false);document.emit('keydown',{key:'Escape',target:dialog});assert.equal(dialog.parentNode,null);
  const replacement=world.AppSurfaceKernel.create({trigger});assert.ok(replacement);
  assert.equal((document.listeners.keydown||[]).length,1);
  replacement.destroy();assert.equal((document.listeners.keydown||[]).length,1);
});

test('modal child drawer shares outside click, escape ordering and focus with its parent modal',()=>{
  const {world,document,body}=setup(),opener=document.createElement('button');body.append(opener);opener.focus();
  const dialog=world.AppDialogKernel.create({title:'装备编辑'}),trigger=document.createElement('button'),outside=document.createElement('button'),
    panel=document.createElement('section'),field=document.createElement('input');panel.append(field);dialog.appBody.append(trigger,outside,panel);trigger.focus();
  let drawer=world.AppSurfaceKernel.create({surface:panel,trigger,kind:'drawer',initialFocus:field,removeOnClose:false});assert.ok(drawer);
  assert.equal(world.AppDialogKernel.getActive(),dialog);assert.equal(world.AppSurfaceKernel.getActive(),panel);
  assert.deepEqual(Array.from(world.GenshinGuiFoundationV2.runtime().store.getState().surfaces,row=>row.group),['modal','modal-child']);
  document.emit('pointerdown',{target:outside});assert.equal(world.AppSurfaceKernel.getActive(),dialog);assert.equal(dialog.open,true);assert.equal(document.activeElement,trigger);
  drawer=world.AppSurfaceKernel.create({surface:panel,trigger,kind:'drawer',initialFocus:field,removeOnClose:false});assert.ok(drawer);
  document.emit('keydown',{key:'Escape',target:field});assert.equal(world.AppSurfaceKernel.getActive(),dialog);assert.equal(dialog.open,true);assert.equal(document.activeElement,trigger);
  document.emit('keydown',{key:'Escape',target:dialog});assert.equal(world.AppDialogKernel.getActive(),null);assert.equal(document.activeElement,opener);
});

test('dialog shell and workspace CSS provide compact, zoom-safe and accessible baselines',()=>{
  const css=fs.readFileSync(path.join(__dirname,'../src/genshin_app/gui/web/workspace.css'),'utf8');
  assert.doesNotMatch(css,/body\s*\{[^}]*min-width\s*:\s*760px/s);
  assert.match(css,/body\s*\{[^}]*min-width\s*:\s*0/s);assert.match(css,/@media\(max-width:780px\)/);
  assert.match(css,/\.app-dialog\[open\][^{]*\{[^}]*grid-template-rows/s);assert.match(css,/\.app-dialog-body[^}]*overflow\s*:\s*auto/s);
  assert.match(css,/@media\(prefers-reduced-motion:reduce\)/);assert.match(css,/@media\(forced-colors:active\)/);
});
