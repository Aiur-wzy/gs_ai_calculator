const test=require('node:test'),assert=require('node:assert/strict'),path=require('node:path'),vm=require('node:vm'),fs=require('node:fs');
const GUI_TEST_RUNTIME=require('./helpers/gui_runtime.cjs');

class Node{
  constructor(){this.hidden=false;this.disabled=false;this.attributes={};this.style={};this.listeners={};this.focused=false;this.focusRows=[];const values=new Set();this.classList={toggle:(name,on)=>on?values.add(name):values.delete(name),contains:name=>values.has(name)};}
  setAttribute(key,value){this.attributes[key]=String(value);}
  getAttribute(key){return this.attributes[key]??null;}
  removeAttribute(key){delete this.attributes[key];}
  addEventListener(key,fn){(this.listeners[key]||=[]).push(fn);}
  emit(key,extra={}){for(const fn of this.listeners[key]||[])fn({preventDefault(){},...extra});}
  focus(){this.focused=true;}
  contains(node){return node===this.child||this.focusRows.includes(node);}
  querySelectorAll(){return this.focusRows;}
  closest(){return null;}
  append(...nodes){for(const node of nodes){if(node.parent)node.parent.children=(node.parent.children||[]).filter(child=>child!==node);this.children||=[];node.parent=this;this.children.push(node);}}
}
const storage=initial=>{const rows=new Map(Object.entries(initial||{}));return {rows,getItem:key=>rows.get(key)??null,setItem:(key,value)=>rows.set(key,String(value))};};
const load=()=>{const world={console};world.window=world;world.globalThis=world;vm.createContext(world);GUI_TEST_RUNTIME.loadWebFile(world,path.join(__dirname,'../src/genshin_app/gui/web/agent-workbench-shell.js'));return world.AgentWorkbenchShell;};

test('shell preferences migrate legacy layout and only persist a versioned UI allowlist',()=>{
  const Shell=load(),store=storage({'genshin.agent.layout.v1':JSON.stringify({width:612,settingsVisible:true,chatVisible:false,apiKey:'secret',runtime:{state:'ready'},candidate:{payload:'large'},path:'C:/private'})});
  const state=Shell.readPreferences(store);assert.deepEqual(JSON.parse(JSON.stringify(state)),{width:612,inspectorWidth:340,inspectorVisible:true,settingsVisible:true,detailsVisible:false,chatVisible:false,viewMode:'docked'});
  state.settingsVisible=false;state.detailsVisible=true;assert.equal(Shell.writePreferences(store,state),true);const written=JSON.parse(store.rows.get(Shell.STORAGE_KEY));
  assert.deepEqual(written,{version:1,preferences:{width:612,inspectorWidth:340,inspectorVisible:true,settingsVisible:false,detailsVisible:false,chatVisible:false,viewMode:'docked'}});assert.doesNotMatch(JSON.stringify(written),/secret|runtime|candidate|private/i);
});

test('shell controller owns drawers, resize, focus return, responsive clamp and page mode',()=>{
  const Shell=load(),store=storage(),listeners={},target={innerWidth:1440,localStorage:store,addEventListener:(key,fn)=>(listeners[key]||=[]).push(fn)},body=new Node(),pageHost=new Node(),root=new Node(),settings=new Node(),details=new Node(),chat=new Node(),inspector=new Node(),settingsToggle=new Node(),detailsToggle=new Node(),chatToggle=new Node(),inspectorToggle=new Node(),modeToggle=new Node(),resizer=new Node(),inspectorResizer=new Node(),composer=new Node(),active=new Node(),document={body,activeElement:null};details.child=active;body.append(pageHost,root);
  let reflows=0;const controller=Shell.createController({window:target,document,storage:store,onReflow:()=>reflows++});controller.bind({root,body,dockHost:body,pageHost,resizer,inspectorResizer,settingsPane:settings,detailsPane:details,chatPane:chat,inspectorPane:inspector,settingsToggle,detailsToggle,chatToggle,inspectorToggle,modeToggle,composerInput:composer});
  assert.equal(settings.hidden,true);assert.equal(details.hidden,true);assert.equal(chat.hidden,false);assert.equal(body.style['--agent-chat-width'],'400px');
  controller.toggleSection('settings');assert.equal(settings.hidden,false);controller.toggleSection('settings');assert.equal(settings.hidden,true);
  resizer.emit('pointerdown',{button:0,clientX:700,pointerId:4});for(const fn of listeners.pointermove)fn({clientX:600,pointerId:4,preventDefault(){}});assert.equal(controller.state.width,500);for(const fn of listeners.pointerup)fn({clientX:600,pointerId:4});
  assert.equal(JSON.parse(store.rows.get(Shell.STORAGE_KEY)).preferences.width,500);resizer.emit('keydown',{key:'End'});assert.equal(controller.state.width,880);
  target.innerWidth=480;for(const fn of listeners.resize)fn({});assert.equal(controller.state.width,456);assert.equal(resizer.attributes['aria-valuemax'],'456');target.innerWidth=1440;
  controller.toggleMode();assert.equal(controller.state.viewMode,'page');assert.equal(root.classList.contains('ac-page-mode'),true);assert.equal(root.parent,pageHost);assert.equal(composer.focused,true);assert.ok(reflows>0);
  inspectorResizer.emit('pointerdown',{button:0,clientX:1000,pointerId:8});for(const fn of listeners.pointermove)fn({clientX:960,pointerId:8,preventDefault(){}});assert.equal(controller.state.inspectorWidth,380);for(const fn of listeners.pointerup)fn({clientX:960,pointerId:8});
  inspectorResizer.emit('keydown',{key:'ArrowRight'});assert.equal(controller.state.inspectorWidth,364);controller.toggleSection('inspector');assert.equal(inspector.hidden,true);assert.equal(root.classList.contains('ac-inspector-collapsed'),true);
  assert.equal(resizer.attributes.role,'separator');assert.equal(resizer.attributes['aria-orientation'],'vertical');assert.equal(root.attributes.role,'region');assert.equal(root.attributes['aria-modal'],undefined);
  controller.setOpen(false);assert.equal(root.hidden,true);assert.equal(body.classList.contains('agent-chat-open'),false);
});

test('mobile page opens the Inspector as a dismissible overlay without changing desktop preference',()=>{
  const Shell=load(),listeners={},target={innerWidth:800,localStorage:storage(),addEventListener:(key,fn)=>(listeners[key]||=[]).push(fn)},body=new Node(),pageHost=new Node(),root=new Node(),inspector=new Node(),inspectorToggle=new Node(),inspectorScrim=new Node(),inspectorResizer=new Node(),document={body,activeElement:null};body.append(pageHost,root);
  const controller=Shell.createController({window:target,document,storage:target.localStorage});controller.bind({root,body,dockHost:body,pageHost,inspectorPane:inspector,inspectorToggle,inspectorScrim,inspectorResizer});controller.setMode('page');
  assert.equal(inspector.hidden,true);assert.equal(inspectorScrim.hidden,true);assert.equal(controller.state.inspectorVisible,true);
  controller.toggleSection('inspector');assert.equal(inspector.hidden,false);assert.equal(inspectorScrim.hidden,false);assert.equal(root.classList.contains('ac-inspector-mobile-open'),true);
  inspectorScrim.emit('click');assert.equal(inspector.hidden,true);assert.equal(inspectorScrim.hidden,true);assert.equal(controller.state.inspectorVisible,true);
});

test('drawers are non-modal application regions and closing returns to the launcher',()=>{
  const Shell=load(),listeners={},target={innerWidth:1280,localStorage:storage(),addEventListener:(key,fn)=>(listeners[key]||=[]).push(fn)},body=new Node(),root=new Node(),settings=new Node(),details=new Node(),chat=new Node(),badge=new Node(),settingsToggle=new Node(),detailsToggle=new Node(),chatToggle=new Node(),modeToggle=new Node(),resizer=new Node(),first=new Node(),last=new Node(),document={body,activeElement:null};
  first.focus=()=>{first.focused=true;document.activeElement=first;};last.focus=()=>{last.focused=true;document.activeElement=last;};badge.focus=()=>{badge.focused=true;document.activeElement=badge;};
  settings.focusRows=[first,last];root.focusRows=[first,last];
  const controller=Shell.createController({window:target,document,storage:target.localStorage});controller.bind({root,body,badge,resizer,settingsPane:settings,detailsPane:details,chatPane:chat,settingsToggle,detailsToggle,chatToggle,modeToggle,settingsFocus:first,composerInput:first});
  controller.setOpen(true);controller.setSection('settings',true);assert.equal(settings.attributes.role,'region');assert.equal(settings.attributes['aria-modal'],undefined);
  document.activeElement=last;let prevented=false;for(const fn of listeners.keydown)fn({key:'Tab',preventDefault(){prevented=true;}});assert.equal(prevented,false);assert.equal(document.activeElement,last);
  controller.setSection('settings',false);controller.toggleMode();document.activeElement=first;prevented=false;for(const fn of listeners.keydown)fn({key:'Tab',shiftKey:true,preventDefault(){prevented=true;}});assert.equal(prevented,false);assert.equal(document.activeElement,first);
  controller.setOpen(false);assert.equal(document.activeElement,badge);assert.equal(badge.attributes['aria-expanded'],'false');
});

test('corrupt, oversized and unknown-version shell storage fail closed to safe defaults',()=>{
  const Shell=load();for(const raw of ['{',JSON.stringify({version:99,preferences:{width:700}}),'x'.repeat(Shell.MAX_STORAGE_CHARS+1)]){
    const value=Shell.readPreferences(storage({[Shell.STORAGE_KEY]:raw}));assert.deepEqual(JSON.parse(JSON.stringify(value)),JSON.parse(JSON.stringify(Shell.DEFAULTS)));
  }
});

test('workbench CSS keeps reduced motion, forced colors, responsive layouts and selectable evidence',()=>{
  const css=fs.readFileSync(path.join(__dirname,'../src/genshin_app/gui/web/agent-chat.css'),'utf8');
  assert.match(css,/@media\(prefers-reduced-motion:reduce\)/);assert.match(css,/@media\(forced-colors:active\)/);
  assert.match(css,/\.agent-chat :focus-visible \{ outline:2px solid Highlight!important/);
  assert.match(css,/@media\(max-width:700px\)/);assert.match(css,/@media\(max-height:760px\)/);
  assert.match(css,/\.ac-metric-card,\.ac-trajectory-span[^}]*user-select:text/);
  assert.match(css,/\.ac-message-text[^}]*user-select:text/);
});
