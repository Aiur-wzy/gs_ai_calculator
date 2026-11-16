const GUI_TEST_RUNTIME = require('./helpers/gui_runtime.cjs');
const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');
const clone=value=>JSON.parse(JSON.stringify(value)),web=path.join(__dirname,'../src/genshin_app/gui/web');
class Element {
  constructor(tag='div'){this.tagName=tag;this.children=[];this.attributes={};this.dataset={};this.listeners={};this.style={setProperty(){}};this.value='';this.clientWidth=900;this.scrollLeft=0;this.scrollTop=0;
    this.classList={add(){},remove(){},toggle(){}};}
  append(...nodes){for(const n of nodes){n.parent=this;n.parentNode=this;this.children.push(n);}} appendChild(node){this.append(node);return node;}
  set innerHTML(v){this.children=[];this.text=v;}set textContent(v){this.children=[];this.text=String(v);}get textContent(){return(this.text||'')+this.children.map(n=>n.textContent).join('');}
  setAttribute(key,value){this.attributes[key]=String(value);}removeAttribute(key){delete this.attributes[key];} addEventListener(key,value){(this.listeners[key]||=[]).push(value);}removeEventListener(key,value){this.listeners[key]=(this.listeners[key]||[]).filter(row=>row!==value);}
  contains(node){for(let current=node;current;current=current.parent||current.parentNode)if(current===this)return true;return false;}
  querySelector(){return null;}querySelectorAll(){return [];}
  emit(key,extra={}){const e={target:this,button:0,pointerId:1,clientX:0,preventDefault(){},stopPropagation(){},...extra};return(this.listeners[key]||[]).map(fn=>fn(e)).at(-1);}
  getBoundingClientRect(){return {left:0,width:this.clientWidth};}setPointerCapture(){}releasePointerCapture(){}showModal(){this.open=true;}close(){this.open=false;}
  focus(){if(this.ownerDocument)this.ownerDocument.activeElement=this;this.focused=true;}
  remove(){if(this.parent)this.parent.children=this.parent.children.filter(n=>n!==this);this.parent=null;this.parentNode=null;}
}
const all=node=>[node,...node.children.flatMap(all)];
function actor(id='a',name='甲'){return {id,db_character_id:id==='a'?10000002:10000054,name,level:90,constellation:0,weapon:null,artifact_sets:[],panel_basis_mode:'manual',notes:'',
  panel:{atk:2000,hp:null,def:null,crit_rate:.5,crit_dmg:1.5,em:0,energy_recharge:1.4,base_atk:null,base_hp:null,base_def:null,dmg_bonus:{}}};}
function board(){return {kind:'genshin-whiteboard',schema_version:1,board_id:'46c1047c-5f55-4b74-9ad8-6e0c149f97fd',revision:0,title:'人工白板',version_line:'release',duration:20,team:[actor(),actor('b','乙')],notes:'',actions:[]};}
function setup({initialBoard=true,reference=false}={}){const root=new Element(),body=new Element('body');root.id='tab-whiteboard';body.append(root);const calls=[],messages=[];
  const w={console,Date,Math,setTimeout:globalThis.setTimeout,clearTimeout:globalThis.clearTimeout,crypto:{randomUUID:()=>`id-${calls.length}-${Math.random()}`},confirm:()=>true,addEventListener(){},RT:{state:{plansDir:'plans'}},setStatus:(text,...args)=>messages.push({text,args})};
  w.window=w;const documentListeners={};w.document={body,activeElement:null,createElement:null,createElementNS:null,querySelector:selector=>all(body).find(n=>n.id===selector.slice(1))||null,querySelectorAll:()=>[],
    addEventListener(type,fn){(documentListeners[type]||=[]).push(fn);},removeEventListener(type,fn){documentListeners[type]=(documentListeners[type]||[]).filter(row=>row!==fn);}};
  const element=tag=>Object.assign(new Element(tag),{ownerDocument:w.document});w.document.createElement=element;w.document.createElementNS=(_,tag)=>element(tag);body.ownerDocument=w.document;root.ownerDocument=w.document;
  w.$=selector=>w.document.querySelector(selector);w.DS={raw:(label,value)=>{const n=new Element('details');n.append(Object.assign(new Element('summary'),{textContent:label}),Object.assign(new Element('pre'),{textContent:JSON.stringify(value)}));return n;},textBlock:text=>Object.assign(new Element(),{textContent:text})};
  w.callApi=async(name,...args)=>{calls.push({name,args:clone(args)});if(name==='whiteboard_new')return {code:0,data:{board:board(),pending:[]}};
    if(name==='inspect_plan_file')return {code:0,data:{kind:'genshin-whiteboard',file_sha:'inspected',display_name:'工程白板'}};
    if(name==='whiteboard_save')return {code:0,data:{path:args[0],file_sha:'saved',pending:[]}};
    if(name==='whiteboard_validate')return {code:0,data:{board:args[0],pending:[]}};
    if(name==='character_detail')return {code:0,data:{character:{weapon_type:'WEAPON_SWORD_ONE_HAND'}}};
    if(name==='character_panel_basis')return {code:0,data:{state:'partial',character_base_atk:args[1]===100?380:340,character_base_hp:args[1]*100,character_base_def:args[1]*10,
      panel_base:{base_atk:null,base_hp:args[1]*100,base_def:args[1]*10},weapon_contribution:{base_atk:null},missing_inputs:[{message:'武器待选择'}],source_refs:[]}};
    if(name==='weapon_detail')return {code:0,data:{weapon:{id:args[0],name:'资料甲',weapon_type:'WEAPON_SWORD_ONE_HAND',max_level:90,available_states:[{ascension:6,min_level:80,max_level:90}]},effects:[{source_index:0,refinements:[{refinement:1,name:'武器被动',desc_template:'工程说明'}]}]}};
    if(name==='artifact_set_detail')return {code:0,data:{artifact_set:{id:args[0],name:'资料甲'},effects:[]}};
    if(name.startsWith('list_'))return {code:0,data:{total:1,items:[{id:42,name:'资料甲',rarity:5}]}};
    return {code:1,message:'未实现测试接口'};};vm.createContext(w);
  for(const file of ['ui.js','equipment-workbench.js','timeline-data.js','whiteboard.js']){
    if(reference&&file==='whiteboard.js')vm.runInContext(fs.readFileSync(path.join(__dirname,'reference/gui-whiteboard-v1.js'),'utf8'),w,{filename:'frozen-whiteboard.js'});
    else GUI_TEST_RUNTIME.loadWebFile(w, path.join(web,file), {filename:file});
  }
  if(initialBoard)w.WB.replace({board:board()},true);
  const dialog=()=>body.children.filter(n=>n.tagName==='dialog').at(-1),button=(label,node=body)=>{const b=all(node).find(n=>n.tagName==='button'&&n.textContent===label);assert.ok(b,`button ${label}`);return b;},
    press=(label,node=body)=>button(label,node).emit('click'),field=(label,node=root)=>{const n=all(node).find(n=>n.attributes['aria-label']===label);assert.ok(n,`field ${label}`);return n;},
    change=(label,value,node=root)=>{const n=field(label,node);n.value=String(value);return n.emit('change');};
  return {w,WB:w.WB,root,body,calls,messages,dialog,button,press,field,change};}

test('cold start stays document-free until the user explicitly creates a whiteboard',async()=>{
  const e=setup({initialBoard:false});await e.WB.init();
  assert.equal(e.WB.state.board,null);assert.equal(e.WB.dirty(),false);assert.equal(e.calls.length,0);
  assert.match(e.root.textContent,/尚未打开创作白板/);assert.match(e.root.textContent,/第一次向 Agent 发送有效消息后才会建立聊天历史/);
  await e.press('新建创作白板');
  assert.equal(e.calls.filter(call=>call.name==='whiteboard_new').length,1);assert.equal(e.WB.state.board.board_id,board().board_id);
  assert.doesNotMatch(e.root.textContent,/尚未打开创作白板/);assert.match(e.root.textContent,/创作白板/);
  assert.ok(e.calls.every(call=>!['agent_start','agent_send','agent_connect'].includes(call.name)));
  await e.WB.newBoard();
  assert.equal(e.calls.filter(call=>call.name==='whiteboard_new').length,2);
  assert.ok(e.calls.every(call=>!['agent_start','agent_send','agent_connect'].includes(call.name)));
});

test('whiteboard is a separate default tab; no advanced model or fake result is created',()=>{
  const e=setup();assert.equal(e.WB.dirty(),false);assert.equal(e.WB.state.result,null);assert.equal(e.w.PL,undefined);
  const html=fs.readFileSync(path.join(web,'index.html'),'utf8');assert.match(html,/class="tab active" data-tab="whiteboard"/);assert.ok(html.indexOf('whiteboard.js')<html.indexOf('app.js'));
  for(const label of ['创作白板','动作轴','效果轴','伤害轴','可编辑'])assert.match(e.root.textContent,new RegExp(label));
  assert.doesNotMatch(e.root.textContent,/\+事件|\+增益|机制规则与过程/);
});
test('Agent payload materialization is cached and invalidated by board and result generations',()=>{
  const e=setup(),initial=e.WB.agentPayloadSnapshot();assert.equal(e.WB.agentPayloadSnapshot(),initial);assert.equal(e.WB.agentPayloadMatches(initial),true);
  const result={result:{summary:{total_damage:123}}};e.WB.setAgentResult(result);const withResult=e.WB.agentPayloadSnapshot();
  assert.notEqual(withResult,initial);assert.equal(withResult.payloadRevision,initial.payloadRevision+1);assert.equal(withResult.boardGeneration,initial.boardGeneration);assert.equal(withResult.resultGeneration,initial.resultGeneration+1);assert.deepEqual(clone(withResult.applicationPayload.calculated),result);assert.equal(e.WB.agentPayloadSnapshot(),withResult);
  e.WB.change(current=>current.title='缓存失效');const changed=e.WB.agentPayloadSnapshot();assert.notEqual(changed,withResult);assert.equal(changed.payloadRevision,withResult.payloadRevision+1);assert.equal(changed.boardGeneration,withResult.boardGeneration);assert.equal(changed.resultGeneration,withResult.resultGeneration+1);assert.equal(changed.applicationPayload.calculated,null);assert.equal(e.WB.agentPayloadMatches(withResult),false);
  e.WB.replace({board:board()},false,false);const replaced=e.WB.agentPayloadSnapshot();assert.notEqual(replaced,changed);assert.equal(replaced.boardGeneration,changed.boardGeneration+1);assert.equal(replaced.resultGeneration,changed.resultGeneration+1);assert.equal(e.WB.agentPayloadMatches(replaced),true);
});
test('all nine actions preserve selected actor, stable ID and explicit starting time',()=>{
  const e=setup();e.WB.state.cursor=1.25;for(const [type] of e.WB.types)e.WB.addAction(type);
  assert.equal(e.WB.state.board.actions.length,9);assert.equal(new Set(e.WB.state.board.actions.map(a=>a.id)).size,9);
  assert.ok(e.WB.state.board.actions.every(a=>a.character==='a'&&a.start===1.25&&a.variant===''));
  assert.equal(e.WB.dirty(),true);assert.equal(e.WB.state.board.hits,undefined);
});
test('move action changes real order by swapping time slots; same-time order is stable',()=>{
  const e=setup();e.WB.addAction('action.elemental_skill');e.WB.state.cursor=3;e.WB.addAction('action.elemental_burst');
  const [a,b]=e.WB.state.board.actions;e.WB.moveOrder(b.id,-1);assert.deepEqual(e.WB.state.board.actions.map(x=>[x.id,x.start]),[[b.id,0],[a.id,3]]);
  e.WB.setTime(a.id,0);e.WB.moveOrder(a.id,-1);assert.deepEqual(e.WB.state.board.actions.map(x=>x.id),[a.id,b.id]);
  e.WB.setTime(a.id,2);assert.deepEqual(e.WB.state.board.actions.map(x=>x.id),[b.id,a.id]);
});
test('shrinking refuses to truncate or stretch actions; illegal time leaves input unchanged',()=>{
  const e=setup();e.WB.state.cursor=19;e.WB.addAction('action.elemental_burst');const before=e.WB.json();
  for(const duration of [19,10,0,-1,Infinity,3601])assert.equal(e.WB.setDuration(duration),false);
  for(const time of [-1,20,Infinity,null])assert.equal(e.WB.setTime(e.WB.state.selectedAction,time),false);
  assert.equal(e.WB.json(),before);assert.equal(e.WB.setDuration(30),true);assert.equal(e.WB.state.board.actions[0].start,19);
});
test('character deletion blocks orphaned actions, allows explicit empty actor removal',()=>{
  const e=setup();e.WB.addAction('action.elemental_skill');e.WB.removeCharacter('a');assert.equal(e.WB.state.board.team.length,2);
  e.WB.removeCharacter('b');assert.equal(e.WB.state.board.team.length,1);assert.equal(e.WB.state.board.actions.length,1);
});
test('character and action commands use contextual menus without permanent destructive buttons',()=>{
  const e=setup();e.WB.addAction('action.elemental_skill');const id=e.WB.state.selectedAction;
  const character=e.WB.characterMenuItems('a'),removeCharacter=character.find(item=>item.id==='remove-character');
  assert.deepEqual(Array.from(character.filter(item=>item.id),item=>item.id),['select-character','edit-character','remove-character']);
  assert.equal(removeCharacter.enabled,false);assert.match(removeCharacter.disabledReason,/动作|意图/);
  const action=e.WB.actionMenuItems(id),removeAction=action.find(item=>item.id==='remove-action');
  assert.deepEqual(Array.from(action.filter(item=>item.id),item=>item.id),['edit-action','move-action-earlier','move-action-later','duplicate-action','remove-action']);
  assert.equal(action.find(item=>item.id==='move-action-earlier').enabled,false);assert.equal(action.find(item=>item.id==='move-action-later').enabled,false);
  assert.equal(removeAction.enabled,true);assert.doesNotMatch(e.root.textContent,/删除动作|移除角色/);assert.match(e.root.textContent,/动作操作…|更多…/);
  action.find(item=>item.id==='duplicate-action').run();assert.equal(e.WB.state.board.actions.length,2);
});
test('capacity and admission intent cards expose edit and delete through contextual menus',()=>{
  const e=setup(),b=e.WB.state.board;
  b.capacity_intents=[{id:'capacity-one',actor:'a',action_ids:[],model_ref:{id:'model-one',revision:1},context:null}];
  b.action_admission_intents=[{id:'admission-one',actor:'a',action_id:'missing',model_ref:{id:'model-two',revision:1},context:null,initial_ready_in:null}];
  const capacity=e.WB.capacityIntentMenuItems(0),admission=e.WB.admissionIntentMenuItems(0);
  assert.deepEqual(Array.from(capacity.filter(item=>item.id),item=>item.id),['edit-capacity-intent','remove-capacity-intent']);
  assert.deepEqual(Array.from(admission.filter(item=>item.id),item=>item.id),['edit-admission-intent','remove-admission-intent']);
  e.WB.render();assert.doesNotMatch(e.root.textContent,/删除次数意图|删除动作接纳意图/);
  assert.ok(all(e.root).filter(node=>node.className==='wb-menu-trigger'&&node.textContent==='⋯').length>=2);
});
test('action variant and notes are saved, duplication creates new ID and preserves declaration',()=>{
  const e=setup();e.WB.addAction('action.elemental_skill');e.change('操作变体','hold');e.change('操作细节 / 段数 / 取消方式','长按后接下落攻击');
  const a=clone(e.WB.state.board.actions[0]);e.WB.duplicateAction(a.id);const b=e.WB.state.board.actions[1];
  assert.notEqual(a.id,b.id);assert.equal(b.start,.1);assert.equal(b.variant,'hold');assert.equal(b.notes,a.notes);
});
test('variant starts unspecified and offers explicit default without inventing tap',()=>{
  const e=setup();e.WB.addAction('action.elemental_skill');assert.equal(e.WB.state.board.actions[0].variant,'');
  const select=e.field('操作变体');assert.match(select.textContent,/未指定（由 Agent 核实）/);assert.ok(select.children.some(option=>option.value==='default'&&option.textContent==='默认释放'));
  e.change('操作变体','default');assert.equal(e.WB.state.board.actions[0].variant,'default');
  e.change('操作变体','');assert.equal(e.WB.state.board.actions[0].variant,'');assert.notEqual(e.WB.state.board.actions[0].variant,'tap');
});
test('drag commits only on release and clamps inside axis, cancel keeps old time',()=>{
  const e=setup();e.WB.state.cursor=2;e.WB.addAction('action.elemental_skill');const id=e.WB.state.selectedAction,pps=e.WB.state.geometry.pps;
  const event=x=>({button:0,pointerId:1,clientX:x,preventDefault(){},stopPropagation(){}});
  e.WB.beginPointer(event(50),id);e.WB.movePointer(event(50+pps*3));assert.equal(e.WB.action(id).start,2);e.WB.endPointer(event(50+pps*3));assert.equal(e.WB.action(id).start,5);
  e.WB.beginPointer(event(50),id);e.WB.movePointer(event(5000));e.WB.endPointer(event(5000),true);assert.equal(e.WB.action(id).start,5);
  e.WB.beginPointer(event(50),id);e.WB.movePointer(event(50000));e.WB.endPointer(event(50000));assert.equal(e.WB.action(id).start,19.999);
});
test('drag begun before draft mutation cannot later overwrite action',()=>{
  const e=setup();e.WB.addAction('action.elemental_skill');const id=e.WB.state.selectedAction,event={button:0,pointerId:1,clientX:50,preventDefault(){},stopPropagation(){}};
  e.WB.beginPointer(event,id);e.WB.movePointer({...event,clientX:200});e.WB.setTime(id,7);e.WB.endPointer({...event,clientX:200});assert.equal(e.WB.action(id).start,7);
});
test('zoom and empty drag pan never change document or dirty state',()=>{
  const e=setup(),before=e.WB.json();e.WB.zoom(4,200);assert.equal(e.WB.state.scale,4);assert.ok(e.WB.state.scroll>0);
  const event={button:0,pointerId:1,clientX:200,preventDefault(){},stopPropagation(){}};e.WB.beginPointer(event,null);e.WB.movePointer({...event,clientX:100});e.WB.endPointer({...event,clientX:100});
  assert.equal(e.WB.json(),before);assert.equal(e.WB.dirty(),false);
});
test('native result-only recovery preserves the live board and viewport state',()=>{
  const e=setup(),live=e.WB.state.board,saved=e.WB.state.savedJson;
  e.WB.state.path='kept.whiteboard.json';e.WB.state.fileSha='kept-sha';e.WB.state.selectedActor='b';e.WB.state.selectedAction='selection';
  e.WB.state.selectedDerived='derived';e.WB.state.scale=3;e.WB.state.scroll=412;e.WB.state.cursor=7.25;e.WB.state.page=2;
  const calculated=returned(),capability=`wdc_v1_${'r'.repeat(43)}`;
  assert.equal(e.WB.restoreAgentResultOnly({calculated},{sessionId:'native-session',deliveryCapability:capability}),true);
  assert.equal(e.WB.state.board,live);assert.equal(e.WB.state.savedJson,saved);assert.equal(e.WB.state.path,'kept.whiteboard.json');assert.equal(e.WB.state.fileSha,'kept-sha');
  assert.equal(e.WB.state.selectedActor,'b');assert.equal(e.WB.state.selectedAction,'selection');assert.equal(e.WB.state.selectedDerived,'derived');
  assert.equal(e.WB.state.scale,3);assert.equal(e.WB.state.scroll,412);assert.equal(e.WB.state.cursor,7.25);assert.equal(e.WB.state.page,2);
  assert.equal(e.WB.state.mode,'damage');assert.deepEqual(clone(e.WB.state.result),calculated);
  assert.deepEqual(clone(e.WB.state.reusableResult),{board:clone(live),sessionId:'native-session',deliveryCapability:capability});
  assert.equal(e.WB.restoreAgentResultOnly(null,{sessionId:'native-session',deliveryCapability:capability}),false);
});
test('save passes stale-file guard and preserves nulls without automatic stat changes',async()=>{
  const e=setup();e.WB.state.path='my.whiteboard.json';e.WB.state.fileSha='old';e.WB.touch();await e.WB.save('my.whiteboard.json');
  const call=e.calls.find(c=>c.name==='whiteboard_save');assert.equal(call.args[2],'old');assert.equal(call.args[1].team[0].panel.hp,null);assert.equal(call.args[1].team[0].panel.atk,2000);
  assert.equal(e.WB.dirty(),false);assert.equal(e.WB.state.fileSha,'saved');
});
test('Agent whiteboard save-as keeps its action id until App-confirmed completion',async()=>{
  const e=setup(),results=[];GUI_TEST_RUNTIME.loadWebFile(e.w,path.join(web,'workspace.js'),{filename:'workspace.js'});
  const actionId='a'.repeat(32),dialog=e.WB.fileDialog("save",{agentActionId:actionId,onAgentActionResult:result=>results.push(result)});assert.equal(dialog.dataset.agentActionId,actionId);
  e.field('白板文件路径',dialog).value='plans/agent.whiteboard.json';await e.press('另存为',dialog);
  assert.deepEqual(clone(results),[{status:'completed',message:'App已确认白板副本保存完成。'}]);assert.equal(dialog.open,false);
});
test('Agent whiteboard open returns completion only after the selected board loads',async()=>{
  const e=setup(),results=[],old=e.w.callApi;GUI_TEST_RUNTIME.loadWebFile(e.w,path.join(web,'workspace.js'),{filename:'workspace.js'});
  e.w.callApi=async(name,...args)=>name==='whiteboard_load'?{code:0,data:{board:board(),path:args[0],file_sha:'loaded',pending:[]}}:old(name,...args);
  const actionId='b'.repeat(32),dialog=e.WB.fileDialog("open",{agentActionId:actionId,onAgentActionResult:result=>results.push(result)});e.field('白板文件路径',dialog).value='plans/open.whiteboard.json';await e.press('打开',dialog);
  assert.equal(dialog.dataset.agentActionId,actionId);assert.deepEqual(clone(results),[{status:'completed',message:'App已确认打开所选白板。'}]);assert.equal(e.WB.state.path,'plans/open.whiteboard.json');
});
test('Agent whiteboard file dialog cancellation returns a terminal receipt',()=>{
  const e=setup(),results=[];GUI_TEST_RUNTIME.loadWebFile(e.w,path.join(web,'workspace.js'),{filename:'workspace.js'});
  const dialog=e.WB.fileDialog("save",{agentActionId:'c'.repeat(32),onAgentActionResult:result=>results.push(result)});e.press('返回白板',dialog);
  assert.deepEqual(clone(results),[{status:'cancelled',message:'用户关闭了文件操作界面。'}]);assert.equal(dialog.open,false);
});
test('whiteboard shared dialog refuses unsafe replacement without starting the requested workflow',()=>{
  const e=setup(),blocker=e.w.AppDialogKernel.create({title:'保留中的窗口',onRequestClose:()=>false});
  assert.equal(e.WB.importResultPathDialog(),null);assert.equal(e.w.AppDialogKernel.getActive(),blocker);
  assert.match(e.messages.at(-1).text,/已有窗口/);blocker.destroy();e.WB.importResultPathDialog();const whiteboardDialog=e.dialog();
  assert.equal(e.WB.handoff(),null);assert.equal(e.w.AppDialogKernel.getActive(),whiteboardDialog);e.WB.closeDialog(whiteboardDialog);
});
test('whiteboard close is idempotent and restores focus to the opener',()=>{
  const e=setup(),opener=e.w.document.createElement('button');e.body.append(opener);opener.focus();
  e.WB.importResultPathDialog();const dialog=e.dialog();assert.equal(e.w.AppDialogKernel.getActive(),dialog);e.WB.closeDialog(dialog);e.WB.closeDialog(dialog);
  assert.equal(e.w.AppDialogKernel.getActive(),null);assert.equal(e.w.document.activeElement,opener);
});
test('whiteboard leave cancellation settles once even when close is repeated',async()=>{
  const e=setup();e.WB.touch();const leaving=e.WB.leave(),dialog=e.dialog();dialog.emit('cancel');dialog.emit('cancel');
  assert.equal(await leaving,false);assert.equal(e.WB.dirty(),true);assert.equal(e.w.AppDialogKernel.getActive(),null);
});
for(const mutation of ['edit','replace','new-request'])test(`late save after ${mutation} cannot mark current draft saved`,async()=>{
  const e=setup();e.WB.touch();let finish;e.w.callApi=()=>new Promise(resolve=>finish=resolve);const pending=e.WB.save('file.json');
  if(mutation==='edit')e.WB.change(b=>b.notes='new');if(mutation==='replace')e.WB.replace({board:board()},false);if(mutation==='new-request')e.WB.token();
  finish({code:0,data:{path:'file.json',file_sha:'stale',pending:[]}});assert.equal(await pending,false);assert.equal(e.WB.dirty(),true);
  if(mutation==='edit')assert.equal(e.WB.state.fileSha,'stale');else assert.notEqual(e.WB.state.fileSha,'stale');
});
test('save after an edit during prior write uses the new disk SHA without losing latest changes',async()=>{
  const e=setup();e.WB.state.path='file.json';e.WB.state.fileSha='initial';e.WB.touch();let finish;e.w.callApi=()=>new Promise(resolve=>finish=resolve);
  const first=e.WB.save('file.json');e.WB.change(b=>b.notes='latest edit');finish({code:0,data:{path:'file.json',file_sha:'written-v1'}});assert.equal(await first,false);
  let submitted;e.w.callApi=async(name,...args)=>{submitted={name,args};return {code:0,data:{path:'file.json',file_sha:'written-v2',pending:[]}};};
  assert.equal(await e.WB.save('file.json'),true);assert.equal(submitted.args[2],'written-v1');assert.equal(submitted.args[1].notes,'latest edit');assert.equal(e.WB.dirty(),false);
});
test('new or load cancellation leaves all input untouched',async()=>{
  const e=setup();e.WB.touch();const before=e.WB.json(),newPending=e.WB.newBoard();e.press('继续编辑',e.dialog());assert.equal(await newPending,false);assert.equal(e.WB.json(),before);
  const loadPending=e.WB.load('file.json');await new Promise(resolve=>setImmediate(resolve));e.press('继续编辑',e.dialog());assert.equal(await loadPending,false);
  assert.deepEqual(e.calls.map(call=>call.name),['inspect_plan_file']);assert.equal(e.WB.json(),before);
});
test('window close considers whiteboard then advanced editor, no implicit close after cancel',async()=>{
  const e=setup();e.WB.touch();let advanced=0;e.w.PL={dirty:()=>true,leave:async()=>{advanced++;return false;}};
  assert.equal(e.WB.requestClose(),false);e.press('放弃修改',e.dialog());await new Promise(resolve=>setImmediate(resolve));assert.equal(advanced,1);assert.ok(e.calls.every(c=>c.name!=='confirm_close'));
  assert.equal(e.WB.state.closing,false);
});
test('character editor has percentage conversion, null unknowns, and return retains unapplied draft',async()=>{
  const e=setup(),before=e.WB.json();e.WB.editCharacter('a');const d=e.dialog();e.change('暴击率 %','66',d);e.change('生命值','',d);e.press('← 返回白板',d);assert.equal(e.WB.json(),before);
  e.WB.editCharacter('a');e.change('暴击率 %','66',e.dialog());await e.press('保存角色设置',e.dialog());assert.equal(e.WB.actor('a').panel.crit_rate,.66);assert.equal(e.WB.actor('a').panel.hp,null);
});
test('2+2 selection starts from empty sets and never changes fixed panel',async()=>{
  const e=setup(),before=clone(e.WB.actor('a').panel);e.WB.editCharacter('a');const d=e.dialog();e.change('套装组合','2+2',d);
  assert.ok(e.button('选择第 2 套',d));e.press('选择第 1 套',d);await new Promise(resolve=>setImmediate(resolve));await e.press('资料甲 · 5星');
  const confirm=e.field('已核对当前面板对应所选装备',d);confirm.checked=true;confirm.emit('change');
  await e.press('保存角色设置',d);assert.deepEqual(clone(e.WB.actor('a').artifact_sets),[{set_id:42,name:'资料甲',count:2}]);assert.deepEqual(clone(e.WB.actor('a').panel),before);
});
test('weapon picker uses character weapon type and does not recalculate fixed panel',async()=>{
  const e=setup(),before=clone(e.WB.actor('a').panel);e.WB.editCharacter('a');const d=e.dialog();await e.press('选择或更换武器',d);await new Promise(resolve=>setImmediate(resolve));
  const list=e.calls.find(c=>c.name==='list_weapons');assert.equal(list.args[1],'WEAPON_SWORD_ONE_HAND');await e.press('资料甲 · 5星');
  const confirm=e.field('已核对当前面板对应所选装备',d);confirm.checked=true;confirm.emit('change');await e.press('保存角色设置',d);
  assert.equal(e.WB.actor('a').weapon.weapon_id,42);assert.deepEqual(clone(e.WB.actor('a').panel),before);
});
test('changing dialog fields during asynchronous validation does not apply unvalidated data',async()=>{
  const e=setup(),before=e.WB.json();e.WB.editCharacter('a');const d=e.dialog();let finish;e.w.callApi=()=>new Promise(resolve=>finish=resolve);
  const pending=e.press('保存角色设置',d);e.change('攻击力','99999',d);finish({code:0,data:{pending:[]}});await pending;
  assert.equal(e.WB.json(),before);assert.match(d.textContent,/检查期间变化/);
});
test('handoff publishes explicit files and says Agent is not automatically invoked',async()=>{
  const e=setup();e.WB.handoff();const d=e.dialog();assert.match(d.textContent,/不会自动启动或发送/);
  e.w.callApi=async()=>({code:0,data:{instructions_path:'out/交给Agent.md',board_path:'out/whiteboard.json',native_path:'out/native.pending.plan.json'}});
  await e.press('生成交接文件',d);assert.equal(e.field('任务说明',d).value,'out/交给Agent.md');assert.equal(e.field('任务说明',d).readOnly,true);
});
function returned(){return {plan:{manual_template:{characters:[{id:'a',name:'甲'}],loop_duration:20,hits:[],buffs:[]}},report:{},
  result:{summary:{total_damage:30,dps:1.5},hits:[{hit_id:'h',character:'a',skill:'伤害',t:2,expected:30}],mechanics_status:'pending',mechanics_issues:[{message:'待核实'}],
    rotation_check:{status:'pending',action_check:{status:'passed'},energy_check:{status:'failed'},event_log:[]}}};}
test('returned damage and effects are strictly read-only and show unresolved checks',async()=>{
  const e=setup();e.WB.importResultPathDialog();const d=e.dialog();e.w.callApi=async()=>({code:0,data:{calculated:returned(),path:'calculated.json'}});await e.press('校验并显示结果',d);
  assert.equal(e.WB.state.mode,'damage');assert.match(e.root.textContent,/能量检查：失败/);assert.match(e.root.textContent,/机制检查：待核实/);
  assert.doesNotMatch(e.root.textContent,/释放时点 \/ 秒|添加动作 ·|删除动作/);const before=e.WB.json();e.WB.beginPointer({button:0},'h');assert.equal(e.WB.json(),before);
  e.WB.selectEvent({id:'derived',label:'命中',start:2,value:30,detail:{edit:'never'}});assert.doesNotMatch(e.root.textContent,/删除动作/);
  e.WB.change(b=>b.notes='change');assert.equal(e.WB.state.result,null);assert.match(e.root.textContent,/尚无与当前白板匹配/);
  assert.match(e.root.textContent,/原计算结果已失效/);assert.match(e.root.textContent,/calculated.json/);
});
test('unknown or incomplete DPS stays unknown and actual report mechanics model feeds projection',()=>{
  const e=setup(),r=returned();r.result.summary.dps=null;r.report.mechanics_model={rules:[{id:'compiled-rule',name_zh:'自动规则'}]};
  let captured;e.w.TD.build=args=>{captured=args;return {views:{damage:{events:[]},effects:{events:[]}}};};
  e.WB.state.result=r;e.WB.state.mode='damage';e.WB.render();assert.match(e.root.textContent,/DPS —/);assert.doesNotMatch(e.root.textContent,/DPS 0/);
  assert.equal(captured.executionModel,r.report.mechanics_model);assert.equal(e.WB.number(null),'—');assert.equal(e.WB.number(NaN),'—');
});
test('sub-millisecond rounding cannot move event to excluded axis endpoint',()=>{
  const e=setup();e.WB.addAction('action.elemental_skill');e.WB.setDuration(1);assert.equal(e.WB.setTime(e.WB.state.selectedAction,.9999),false);
  assert.equal(e.WB.state.board.actions[0].start,0);
});
test('database element values are presented in Chinese in picker',async()=>{
  const e=setup();e.w.callApi=async()=>({code:0,data:{total:1,items:[{id:1,name:'角色名',element:'Ice',rarity:5}]}});
  e.WB.pick({title:'角色',kind:'character',choose(){}});await new Promise(resolve=>setImmediate(resolve));assert.match(e.dialog().textContent,/角色名 · 冰 · 5星/);assert.doesNotMatch(e.dialog().textContent,/Ice/);
});
for(const mutation of ['edit','replace','close'])test(`stale returned calculation after ${mutation} cannot become current`,async()=>{
  const e=setup();e.WB.importResultPathDialog();const d=e.dialog();let finish;e.w.callApi=()=>new Promise(resolve=>finish=resolve);const pending=e.press('校验并显示结果',d);
  if(mutation==='edit')e.WB.change(b=>b.notes='new');if(mutation==='replace')e.WB.replace({board:board()},true);if(mutation==='close')e.press('返回白板',d);
  finish({code:0,data:{calculated:returned(),path:'old.json'}});await pending;assert.equal(e.WB.state.result,null);
});
test('action order and dense timeline preserve access to all actions through pages',()=>{
  const e=setup();for(let i=0;i<1000;i++)e.WB.state.board.actions.push({id:`a.${i}`,character:'a',action_type_id:'action.normal_attack',label:'普攻',start:1,variant:'',notes:''});e.WB.render();
  assert.equal(all(e.root).filter(n=>n.className==='wb-order-row').length,40);assert.equal(all(e.WB.state.svg).filter(n=>n.attributes.class==='wb-action').length,2);assert.doesNotMatch(e.root.textContent,/先显示 800 项/);
  const more=all(e.WB.state.svg).find(n=>n.attributes['aria-label']==='查看同区域 998 个动作');assert.ok(more);more.emit('click');assert.match(e.dialog().textContent,/密集动作/);
  const chosen=all(e.dialog()).find(n=>n.tagName==='button'&&n.textContent==='1 秒 · 甲 · 普攻');chosen.emit('click');assert.equal(e.WB.state.selectedAction,'a.2');
});
test('coincident action cards occupy distinct visual rows without changing timestamps',()=>{
  const e=setup();e.WB.addAction('action.elemental_skill');e.WB.addAction('action.elemental_burst');const before=e.WB.json();e.WB.render();
  const cards=all(e.WB.state.svg).filter(n=>n.attributes.class==='wb-action'),ys=cards.map(n=>n.children.find(c=>c.tagName==='rect').attributes.y);
  assert.equal(new Set(ys).size,2);assert.equal(e.WB.json(),before);cards[0].emit('click');assert.equal(e.WB.state.selectedAction,e.WB.state.board.actions[0].id);
});


test('desktop file actions keep path entry out of the canvas and preserve guarded save semantics',async()=>{
  const e=setup();assert.equal(e.w.$('#wb-path'),null);e.WB.state.path='original.whiteboard.json';e.WB.state.fileSha='previous';
  e.WB.fileDialog("save");const d=e.dialog();assert.equal(e.field('白板文件路径',d).value,'original.whiteboard.json');
  e.change('白板文件路径','copy.whiteboard.json',d);await e.press('另存为',d);
  const save=e.calls.find(c=>c.name==='whiteboard_save');assert.equal(save.args[0],'copy.whiteboard.json');assert.equal(save.args[2],null);
  assert.equal(e.WB.state.path,'copy.whiteboard.json');assert.equal(e.w.$('#wb-path'),null);
});

test('opening from the file menu still asks before discarding the current dirty board',async()=>{
  const e=setup();e.WB.change(b=>b.notes='unsaved intent');const before=e.WB.json();e.WB.fileDialog("open");e.change('白板文件路径','another.whiteboard.json',e.dialog());
  const pending=e.press('打开',e.dialog());await new Promise(resolve=>setImmediate(resolve));assert.match(e.dialog().textContent,/白板尚未保存/);e.press('继续编辑',e.dialog());await pending;
  assert.equal(e.WB.json(),before);assert.ok(e.calls.every(c=>c.name!=='whiteboard_load'));
});

test('workspace layout and inspector focus affect presentation only',()=>{
  const e=setup(),mounts=[],reveals=[];e.w.WS={mount:(root,options)=>mounts.push({root,options}),reveal:(...args)=>reveals.push(args)};
  const before=e.WB.json();e.WB.render();assert.equal(mounts.at(-1).options.key,'whiteboard');assert.equal(mounts.at(-1).options.left,'.wb-team');assert.equal(mounts.at(-1).options.right,'.wb-inspector');
  e.WB.openAdd();assert.equal(e.WB.state.addOpen,true);assert.deepEqual(reveals.at(-1),['whiteboard','right']);assert.equal(e.WB.json(),before);
  assert.equal(e.WB.setMode('effects'),true);assert.match(e.root.textContent,/等待计算结果/);assert.ok(e.button('交给 Agent 完善'));assert.equal(e.WB.state.wrap,null);
  assert.equal(e.WB.setMode('unknown'),false);assert.equal(e.WB.state.mode,'effects');assert.equal(e.WB.json(),before);
});

test('topology workspace separates structure, execution binding and calculation acceptance',()=>{
  const e=setup(),b=clone(e.WB.state.board);b.extensions={event_topology:{status:'partial',nodes:[{node_id:'action'} ,{node_id:'hit'}],edges:[{edge_id:'causes'}],pending_items:[{node_id:'buff'}]},
    event_topology_execution_binding_receipt:{status:'incomplete',counts:{expected_nodes:3,bound_nodes:1,resolved_bindings:1},coverage:{missing_node_ids:['node.hit','node.buff']},issues:[{code:'event_topology_execution_binding_missing',message:'活跃节点尚未绑定当前输入'}],character_semantics:'agent_declared_unverified'}};
  e.WB.replace({board:b},true);e.WB.setMode('effects');
  assert.match(e.root.textContent,/拓扑结构/);assert.match(e.root.textContent,/事件节点 2 · 拓扑关系 1 · 待完善 1/);
  assert.match(e.root.textContent,/执行绑定/);assert.match(e.root.textContent,/已绑定 1\/3 · 已解析 1/);assert.match(e.root.textContent,/缺失节点：node\.hit、node\.buff/);
  assert.match(e.root.textContent,/首个问题：活跃节点尚未绑定当前输入/);assert.match(e.root.textContent,/计算接纳/);assert.match(e.root.textContent,/尚未请求/);
  assert.match(e.root.textContent,/角色特有语义仍由 Agent 声明.*未由 App 或引擎验证/);assert.doesNotMatch(e.root.textContent,/角色特有语义.*已验证/);
});

test('selected action fields precede folded add controls and folded action order',()=>{
  const e=setup();e.WB.addAction('action.elemental_skill');const inspector=all(e.root).find(n=>n.className==='wb-inspector'),text=inspector.textContent;
  assert.ok(text.indexOf('释放时点 / 秒')<text.indexOf('添加动作 ·'));
  const folds=all(inspector).filter(n=>n.tagName==='details');assert.ok(folds.some(n=>n.textContent.startsWith('添加动作 ·')&&!n.open));assert.ok(folds.some(n=>n.textContent.startsWith('动作顺序 ·')&&!n.open));
  e.WB.openAdd();assert.ok(all(e.root).some(n=>n.tagName==='details'&&n.open&&n.textContent.startsWith('添加动作 ·')));
});

test('readonly event inspector presents Chinese facts without eagerly rendering raw payload',()=>{
  const e=setup();e.WB.state.result=returned();e.WB.setMode('damage');e.WB.selectEvent({id:'one',kind:'external_damage',label:'外部补充',start:2,value:1234,sourceLabel:'甲 · 补充计算',detail:{private_machine_key:'RAW_SENTINEL',interpretation:'独立外部计算值。'}});
  assert.match(e.root.textContent,/外部补充伤害/);assert.match(e.root.textContent,/1,234/);assert.doesNotMatch(e.root.textContent,/RAW_SENTINEL|删除动作|释放时点/);
  const raw=all(e.root).find(n=>n.tagName==='details'&&n.textContent==='展开原始记录');assert.ok(raw);raw.open=true;raw.emit('toggle');assert.match(raw.textContent,/RAW_SENTINEL/);
});

test('empty team gets an actionable onboarding state without covering a populated timeline',()=>{
  const e=setup(),empty=board();empty.team=[];e.WB.replace({board:empty});
  assert.match(e.root.textContent,/添加角色，开始排轴/);assert.equal(e.WB.state.wrap,null);
  const center=all(e.root).find(n=>n.className==='wb-center');assert.ok(e.button('＋ 添加角色',center));
  e.WB.replace({board:board()});assert.doesNotMatch(e.root.textContent,/添加角色，开始排轴/);assert.ok(e.WB.state.wrap);assert.ok(e.WB.state.svg);
});


test('damage view defaults to one marker per hit while preserving associated calculation detail',()=>{
  const e=setup(),damage={id:'d',kind:'damage',character:'a',sourceKey:'source',label:'命中',start:2,value:30,detail:{hit_id:'h'}},
    calculation={id:'c',kind:'calculation',character:'a',sourceKey:'source',label:'命中计算',start:2,detail:{hit_id:'h',hit:{expected:30,crit:60},zones:{base:20}}},events=[damage,calculation],before=JSON.stringify(events);
  e.WB.state.result=returned();e.WB.state.resultProjectionOwner=e.WB.state.result;e.WB.state.resultProjection={views:{damage:{events},effects:{events:[]}}};e.WB.setMode('damage');
  assert.equal(e.WB.project().events.length,1);assert.equal(all(e.WB.state.svg).filter(n=>n.attributes.class==='wb-derived').length,1);
  assert.doesNotMatch(e.WB.state.svg.textContent,/2 条相邻记录/);e.WB.selectEvent(e.WB.project().events[0]);assert.match(e.root.textContent,/暴击伤害/);assert.match(e.root.textContent,/60/);assert.match(e.root.textContent,/本次命中计算明细/);
  e.press('含计算记录');assert.equal(e.WB.project().events.length,2);e.press('伤害事件');assert.equal(e.WB.project().events.length,1);assert.equal(JSON.stringify(events),before);assert.equal(e.WB.dirty(),false);
});

test('dense damage group total counts damage events but never the calculation companion',()=>{
  const e=setup(),events=[{id:'d1',kind:'damage',character:'a',sourceKey:'s',label:'命中甲',start:2,value:30,detail:{hit_id:'h1'}},
    {id:'d2',kind:'external_damage',character:'a',sourceKey:'s',label:'外部补充',start:2.001,value:5,detail:{}},
    {id:'c1',kind:'calculation',character:'a',sourceKey:'s',label:'计算记录',start:2,value:999,detail:{hit_id:'h1'}}];
  e.WB.state.result=returned();e.WB.state.resultProjectionOwner=e.WB.state.result;e.WB.state.resultProjection={views:{damage:{events},effects:{events:[]}}};e.WB.setMode('damage');e.press('含计算记录');
  assert.equal(all(e.WB.state.svg).filter(n=>n.attributes.class==='wb-derived').length,3,'external and computed kinds retain separate markers');
  e.WB.selectEvent({events});assert.match(e.root.textContent,/组内合计：35（2 条伤害事件/);assert.doesNotMatch(e.root.textContent,/组内合计：1034/);
});


test('returned actor draft remains recoverable and explicit discard restores original',()=>{
  const e=setup(),before=e.WB.json();e.WB.editCharacter('a');e.change('攻击力','3100',e.dialog());e.press('← 返回白板',e.dialog());
  assert.equal(e.WB.json(),before);assert.equal(e.WB.unappliedEditor(),true);assert.equal(e.WB.dirty(),true);
  e.WB.editCharacter('a');assert.equal(Number(e.field('攻击力',e.dialog()).value),3100);e.press('放弃本次修改',e.dialog());
  assert.equal(e.WB.json(),before);assert.equal(e.WB.unappliedEditor(),false);assert.equal(e.WB.dirty(),false);
});
test('equipment change requires panel acknowledgement and the requirement survives returning',async()=>{
  const e=setup(),before=e.WB.json();e.WB.editCharacter('a');let d=e.dialog();await e.press('选择或更换武器',d);await e.press('资料甲 · 5星');
  await e.press('保存角色设置',d);assert.equal(e.WB.json(),before);assert.match(d.textContent,/请核对当前面板并勾选确认/);
  e.press('← 返回白板',d);e.WB.editCharacter('a');d=e.dialog();await e.press('保存角色设置',d);assert.equal(e.WB.json(),before);
  const confirm=e.field('已核对当前面板对应所选装备',d);confirm.checked=true;confirm.emit('change');await e.press('保存角色设置',d);
  assert.equal(e.WB.actor('a').weapon.weapon_id,42);assert.equal(e.WB.unappliedEditor(),false);
});
test('apply and recalculate reuses trusted original return and keeps actions and old file unchanged',async()=>{
  const e=setup();e.WB.addAction('action.elemental_skill');const original=clone(e.WB.state.board),oldApi=e.w.callApi;
  e.WB.importResultPathDialog();e.w.callApi=async(name,...args)=>name==='whiteboard_read_result'?{code:0,data:{calculated:returned(),path:'trusted-calculated.json'}}:oldApi(name,...args);
  await e.press('校验并显示结果',e.dialog());const calls=[];
  e.w.callApi=async(name,...args)=>{if(name==='whiteboard_apply_equipment'){calls.push(clone(args));const calculated=returned();calculated.result.summary.total_damage=45;
    return {code:0,data:{state:'ready',calculated,missing_inputs:[],agent_followup:[{message:'元素能量循环仍需核实'}]}};}return oldApi(name,...args);};
  e.WB.editCharacter('a');e.change('攻击力','3000',e.dialog());await e.press('应用并计算',e.dialog());
  assert.equal(calls[0][0].team[0].panel.atk,3000);assert.deepEqual(calls[0][1],original);assert.equal(calls[0][2],'trusted-calculated.json');
  assert.deepEqual(clone(e.WB.state.board.actions),original.actions);assert.equal(e.WB.state.result.result.summary.total_damage,45);assert.equal(e.WB.state.resultPath,null);
  assert.match(e.root.textContent,/元素能量循环仍需核实/);
  e.WB.editCharacter('a');e.change('攻击力','3100',e.dialog());await e.press('应用并计算',e.dialog());assert.deepEqual(calls[1][1],original);
  assert.equal(calls[1][0].team[0].panel.atk,3100);assert.match(e.root.textContent,/尚未保存为交付文件/);
});
test('native Agent result recalculates equipment through opaque Host capability',async()=>{
  const e=setup(),original=clone(e.WB.state.board),calls=[];
  e.WB.applyAgentDraft(original,[],{calculated:returned()},{sessionId:'native-session',deliveryCapability:'wdc_v1_test-capability'});
  e.w.callApi=async(name,...args)=>{calls.push({name,args:clone(args)});if(name==='agent_whiteboard_apply_equipment')return {code:0,data:{state:'ready',source_result:{validation:'host_verified_delivery_capability'},calculated:returned(),missing_inputs:[]}};return {code:1,message:'unexpected'};};
  e.WB.state.board.team[0].panel.atk=3333;e.WB.state.request++;
  await e.WB.recalculateEquipment();
  assert.equal(calls.length,1);assert.equal(calls[0].name,'agent_whiteboard_apply_equipment');assert.equal(calls[0].args[0],'native-session');assert.equal(calls[0].args[1],'wdc_v1_test-capability');assert.equal(calls[0].args[2].team[0].panel.atk,3333);
  assert.equal(JSON.stringify(calls).includes('trusted-calculated.json'),false);assert.equal(e.WB.state.result.result.summary.total_damage,30);
});
test('recalculate without trusted return reports actionable Agent work and never displays old DPS',async()=>{
  const e=setup();let args;e.w.callApi=async(name,...values)=>{args=values;return {code:0,data:{state:'pending',missing_inputs:[{message:'需要先让 Agent 补齐动作计算规则。'}]}};};
  await e.WB.recalculateEquipment();assert.equal(args[1],null);assert.equal(args[2],null);assert.equal(e.WB.state.result,null);
  assert.match(e.root.textContent,/需要 Agent 二次处理/);assert.match(e.root.textContent,/补齐动作计算规则/);assert.ok(e.button('保存交接文件给 Agent'));
});
test('late recalculation cannot replace a newly edited board',async()=>{
  const e=setup();let finish;e.w.callApi=()=>new Promise(resolve=>finish=resolve);const task=e.WB.recalculateEquipment();
  e.WB.change(b=>b.team[0].panel.atk=9999);finish({code:0,data:{state:'ready',calculated:returned(),missing_inputs:[]}});await task;
  assert.equal(e.WB.state.result,null);assert.equal(e.WB.state.equipmentReport,null);assert.equal(e.WB.actor('a').panel.atk,9999);
});
test('pending reusable candidate exports its existing rules instead of generating an empty handoff',async()=>{
  const e=setup(),plan={kind:'test-candidate',mechanics_model:{rules:[{id:'keep'}]}};e.WB.state.equipmentReport={state:'pending',plan,missing_inputs:[{message:'需要重新核对新武器被动'}]};e.WB.render();
  e.press('保存待完善方案给 Agent');const d=e.dialog();e.change('新方案文件路径','candidate.plan.json',d);let submitted;
  e.w.callApi=async(name,...args)=>{submitted={name,args:clone(args)};return {code:0,data:{path:args[0]}};};await e.press('保存待完善方案',d);
  assert.equal(submitted.name,'save_plan');assert.deepEqual(submitted.args,['candidate.plan.json',plan,null]);assert.match(d.textContent,/新武器被动/);
  assert.equal(e.field('交给 Agent 的方案路径',d).value,'candidate.plan.json');
});

test('adding a database character fills known bases only before publishing the new actor',async()=>{
  const e=setup();e.WB.addCharacter();await new Promise(resolve=>setImmediate(resolve));await e.press('资料甲 · 5星',e.dialog());
  const added=e.WB.state.board.team.at(-1);assert.equal(added.db_character_id,42);assert.equal(added.level,90);assert.equal(added.panel_basis_mode,'database');
  assert.equal(added.panel.base_hp,9000);assert.equal(added.panel.base_def,900);assert.equal(added.panel.base_atk,null);assert.equal(added.panel.atk,null);assert.equal(added.panel.hp,null);
  assert.equal(e.calls.find(c=>c.name==='character_panel_basis').args[3],null);
});

test('late basis lookup cannot add a character to a replaced board or closed picker',async()=>{
  for(const event of ['replace','close']){const e=setup(),old=e.w.callApi;let finish;
    e.w.callApi=(method,...args)=>method==='character_panel_basis'?new Promise(resolve=>finish=resolve):old(method,...args);
    e.WB.addCharacter();await new Promise(resolve=>setImmediate(resolve));const d=e.dialog(),adding=e.press('资料甲 · 5星',d);
    assert.equal(e.WB.state.board.team.length,2);if(event==='replace')e.WB.replace({board:board()},true);else e.WB.closeDialog(d);
    finish({code:0,data:{panel_base:{base_hp:9999,base_def:999,base_atk:null}}});await adding;assert.equal(e.WB.state.board.team.length,2);}
});

test('database bases are a draft until applied, and manual source mode survives actor save and reopening',async()=>{
  const e=setup();delete e.WB.actor('a').panel_basis_mode;const original=clone(e.WB.actor('a'));e.WB.editCharacter('a');
  await new Promise(resolve=>setImmediate(resolve));let d=e.dialog();assert.equal(Number(e.field('基础生命值',d).value),9000);assert.deepEqual(clone(e.WB.actor('a')),original);
  e.change('基础值来源','manual',d);e.change('基础攻击力（角色 + 武器白值）','888',d);await e.press('保存角色设置',d);
  assert.equal(e.WB.actor('a').panel_basis_mode,'manual');assert.equal(e.WB.actor('a').panel.base_atk,888);assert.equal(e.WB.actor('a').panel.atk,2000);
  const requests=e.calls.filter(c=>c.name==='character_panel_basis').length;e.WB.editCharacter('a');assert.equal(e.calls.filter(c=>c.name==='character_panel_basis').length,requests);
  assert.equal(Number(e.field('基础攻击力（角色 + 武器白值）',e.dialog()).value),888);
});

// The old source is test-only and executes independently, never as a production fallback.
test('G5 independent whiteboard oracle preserves document commands, unchanged shell/inspector, result and payload CAS',()=>{
 const actual=setup(),expected=setup({reference:true});
 for(const e of [actual,expected]){let n=0;e.w.crypto.randomUUID=()=>`stable-${++n}`;}
 // r20-next intentionally replaces team-card detail density and the two
 // advanced intent blocks with disclosures. Their new DOM is covered by
 // view-layout/geometry checks; the legacy oracle still compares every saved
 // input, command, identity, payload, unchanged shell and inspector.
 const summarize=(node,cutoff)=>({tag:node.tagName,attrs:node.attributes,text:node.text||'',children:node.children.filter(child=>child!==cutoff&&!['wb-center','wb-team'].includes(child.className)).map(child=>summarize(child,cutoff))});
 const next06Control=e=>{
   assert.equal(Object.hasOwn(e.WB.state.board,'damage_report'),false,'the frozen comparison only covers legacy default documents');
   const labels=all(e.root).filter(node=>node.tagName==='label'&&node.children.some(child=>child.tagName==='span'&&child.textContent==='DPS截止'));
   assert.equal(labels.length,1,'exactly one approved NEXT-06 control is added');
   const label=labels[0],select=label.children.find(child=>child.tagName==='select');
   assert.equal(label.children.length,2);assert.ok(select);assert.equal(select.attributes['aria-label'],'DPS截止');assert.equal(select.value,'last_active_damage');
   assert.deepEqual(select.children.map(option=>({tag:option.tagName,value:option.value,text:option.textContent})),[
     {tag:'option',value:'last_active_damage',text:'末次主动命中'},{tag:'option',value:'explicit',text:'用户指定时间'}]);
   assert.equal(all(e.root).some(node=>node.attributes['aria-label']==='截止 / 秒'),false);
   return label;
 };
 const snapshot=(e,migrateNext06=false)=>({board:clone(e.WB.state.board),json:e.WB.json(),identity:clone(e.WB.agentPayloadIdentity()),dirty:e.WB.dirty(),mode:e.WB.state.mode,root:summarize(e.root,migrateNext06?next06Control(e):null)});
 const steps=[e=>e.WB.addAction('action.elemental_skill'),e=>e.WB.setTime(e.WB.state.selectedAction,2.3),
   e=>e.WB.duplicateAction(e.WB.state.selectedAction),e=>e.WB.moveOrder(e.WB.state.selectedAction,-1),
   e=>e.WB.setDuration(18),e=>e.WB.change(b=>b.notes='保留用户动作与面板'),
   e=>e.WB.setAgentResult(returned()),e=>e.WB.setMode('effects'),e=>e.WB.setMode('damage'),
   e=>e.WB.setMode('actions'),e=>e.WB.removeAction(e.WB.state.selectedAction),
   e=>e.WB.replace({board:board(),path:'fresh.whiteboard.json',file_sha:'same'},true)];
 for(const [index,step] of steps.entries()){
   step(actual);step(expected);assert.deepEqual(snapshot(actual,true),snapshot(expected),'step '+index);
   const aa=actual.WB.agentPayloadSnapshot(),bb=expected.WB.agentPayloadSnapshot();
   assert.deepEqual(clone(aa.applicationPayload),clone(bb.applicationPayload));
   assert.equal(actual.WB.agentPayloadMatches(aa),true);assert.equal(expected.WB.agentPayloadMatches(bb),true);
 }
});

test('NEXT-06 cutoff controls edit only explicit report intent and preserve actions and valid revision flow',()=>{
 const e=setup(),actions=clone(e.WB.state.board.actions),before=e.WB.state.board.revision;
 assert.equal(e.field('DPS截止').value,'last_active_damage');assert.equal(e.WB.state.board.damage_report,undefined);
 e.change('DPS截止','explicit');assert.deepEqual(clone(e.WB.state.board.damage_report),{contract:'damage-report-window-v1',mode:'explicit',end:20});
 assert.equal(e.WB.state.board.revision,before+1);assert.equal(e.WB.dirty(),true);
 const valid=e.WB.json();for(const value of ['0','-1','21','NaN']){e.change('截止 / 秒',value);assert.equal(e.WB.json(),valid,'invalid cutoff must not mutate the document');}
 e.change('截止 / 秒','12');assert.equal(e.WB.state.board.damage_report.end,12);assert.equal(e.WB.state.board.revision,before+2);
 e.change('DPS截止','last_active_damage');assert.deepEqual(clone(e.WB.state.board.damage_report),{contract:'damage-report-window-v1',mode:'last_active_damage'});
 assert.equal(e.WB.state.board.revision,before+3);assert.deepEqual(clone(e.WB.state.board.actions),actions);
 assert.equal(all(e.root).some(node=>node.attributes['aria-label']==='截止 / 秒'),false);
 assert.equal(e.calls.length,0,'editing report intent must not calculate, save or create an Agent task');
});

test('G5 independent delayed-save oracle preserves on-disk SHA without adopting later draft edits',async()=>{
 const run=async reference=>{const e=setup({reference});let finish;
   e.w.callApi=()=>new Promise(resolve=>finish=resolve);
   const promise=e.WB.save('output.whiteboard.json');e.WB.change(b=>b.notes='save之后继续编辑');
   finish({code:0,data:{path:'output.whiteboard.json',file_sha:'written-version',pending:[]}});
   return {accepted:await promise,path:e.WB.state.path,sha:e.WB.state.fileSha,dirty:e.WB.dirty(),
     json:e.WB.json(),saved:e.WB.state.savedJson,messages:e.messages.map(row=>row.text)};
 };
 assert.deepEqual(await run(false),await run(true));
});

test('native whiteboard Save As uses one picker and Store SHA guard without a path form',async()=>{
 const e=setup(),base=e.w.callApi;e.WB.state.path='old.whiteboard.json';e.WB.state.fileSha='previous';
 e.w.callApi=async(name,...args)=>name==='dsh_client_file_dialog'?{code:0,data:{cancelled:false,path:'native-copy.whiteboard.json'}}:base(name,...args);
 assert.equal(await e.WB.saveAs(),true);assert.equal(e.WB.state.path,'native-copy.whiteboard.json');assert.equal(e.w.$('#wb-path'),null);
 assert.equal(e.calls.find(row=>row.name==='whiteboard_save').args[2],null);
});
test('native picker cancellation, pending duplicate and a changed draft never write',async()=>{
 const e=setup();let finish,pickers=0;e.w.callApi=async name=>{assert.equal(name,'dsh_client_file_dialog');pickers++;return new Promise(resolve=>finish=resolve);};
 const first=e.WB.saveAs();assert.equal(await e.WB.saveAs(),false);assert.equal(pickers,1);
 e.WB.change(board=>board.notes='picker opened then user edited');finish({code:0,data:{cancelled:false,path:'never.json'}});
 assert.equal(await first,false);assert.equal(e.WB.state.path,'');assert.equal(e.WB.state.userFilePickerPending,false);
 e.w.callApi=async()=>({code:0,data:{cancelled:true,path:null}});assert.equal(await e.WB.saveAs(),false);
});
test('native open still asks before discarding an unsaved board',async()=>{
 const e=setup();e.WB.change(board=>board.notes='unsaved user intent');const before=e.WB.json();
 e.w.callApi=async name=>{if(name==='inspect_plan_file')return {code:0,data:{kind:'genshin-whiteboard',file_sha:'chosen-sha'}};assert.equal(name,'dsh_client_file_dialog');return {code:0,data:{cancelled:false,path:'chosen.whiteboard.json'}};};
 const pending=e.WB.openFile();await new Promise(resolve=>setImmediate(resolve));assert.match(e.dialog().textContent,/白板尚未保存/);
 e.press('继续编辑',e.dialog());assert.equal(await pending,false);assert.equal(e.WB.json(),before);
});

for(const kind of ['genshin-whiteboard','genshin-candidate-input-draft'])for(const mutation of ['new-document','same-id-revision','dirty-edit','new-request'])test(`late file inspection cannot overwrite or navigate after ${mutation}: ${kind}`,async()=>{
 const e=setup(),base=e.w.callApi;let finishInspection,loadCalls=0,openerCalls=0;
 e.w.GenshinCandidateDraftFiles={openSelected:async()=>{openerCalls++;return {opened:true,message:'candidate opened'};}};
 e.w.callApi=async(name,...args)=>{
  if(name==='inspect_plan_file')return new Promise(resolve=>finishInspection=resolve);
  if(name==='whiteboard_load'){loadCalls++;return {code:0,data:{board:{...board(),title:'old delayed file'},path:args[0],file_sha:'loaded'}};}
  return base(name,...args);
 };
 const pending=e.WB.load('old.whiteboard.json');
 if(mutation==='new-document')e.WB.replace({board:{...board(),board_id:'f19237ad-a04e-4e39-a006-a9f816f81e44',title:'new clean board'}},true);
 if(mutation==='same-id-revision')e.WB.replace({board:{...board(),revision:1,title:'same id new revision'}},true);
 if(mutation==='dirty-edit')e.WB.change(value=>value.notes='new unsaved work');
 if(mutation==='new-request')e.WB.token();
 const latest=e.WB.json();finishInspection({code:0,data:{kind,file_sha:'inspected'}});
 assert.equal(await pending,false);assert.equal(e.WB.json(),latest);assert.equal(loadCalls,0);assert.equal(openerCalls,0);
});
test('native calculated import validates selected path and rejects a late return after edits',async()=>{
 const e=setup();let finish;e.w.callApi=async(name,...args)=>{
  if(name==='dsh_client_file_dialog'){assert.equal(args[0].document_kind,'calculated');return {code:0,data:{cancelled:false,path:'result.calculated.json'}};}
  assert.equal(name,'whiteboard_read_result');return new Promise(resolve=>finish=resolve);
 };
 const pending=e.WB.importResult();await new Promise(resolve=>setImmediate(resolve));e.WB.change(board=>board.notes='new input');
 finish({code:0,data:{calculated:returned(),path:'result.calculated.json'}});assert.equal(await pending,false);assert.equal(e.WB.state.result,null);
 e.w.callApi=async name=>name==='dsh_client_file_dialog'?{code:0,data:{cancelled:false,path:'result.calculated.json'}}:{code:0,data:{calculated:returned(),path:'result.calculated.json'}};
 assert.equal(await e.WB.importResult(),true);assert.equal(e.WB.state.mode,'damage');assert.equal(e.WB.state.resultPath,'result.calculated.json');
});
