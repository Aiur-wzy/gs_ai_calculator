const GUI_TEST_RUNTIME = require('./helpers/gui_runtime.cjs');
const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');
const clone=value=>JSON.parse(JSON.stringify(value));
class Element{
  constructor(tag='div',ownerDocument=null){this.tagName=tag;this.ownerDocument=ownerDocument;this.children=[];this.parentNode=null;this.listeners={};this.attributes={};this.style={};this.dataset={};this.value='';this.hidden=false;}
  append(...nodes){for(const node of nodes){node.parentNode=this;this.children.push(node);}}remove(){if(this.parentNode)this.parentNode.children=this.parentNode.children.filter(node=>node!==this);this.parentNode=null;}
  contains(node){for(let current=node;current;current=current.parentNode)if(current===this)return true;return false;}
  set innerHTML(value){for(const child of this.children)child.parentNode=null;this.children=[];this.text=value;}set textContent(value){for(const child of this.children)child.parentNode=null;this.children=[];this.text=String(value);}
  get textContent(){return(this.text||'')+this.children.map(n=>n.textContent).join('');}setAttribute(key,value){this.attributes[key]=String(value);}getAttribute(key){return this.attributes[key]??null;}removeAttribute(key){delete this.attributes[key];}
  addEventListener(key,fn){(this.listeners[key]||=[]).push(fn);}removeEventListener(key,fn){this.listeners[key]=(this.listeners[key]||[]).filter(value=>value!==fn);}
  emit(key,extra={}){const event={target:this,currentTarget:this,preventDefault(){},stopPropagation(){},...extra};return(this.listeners[key]||[]).map(fn=>fn(event)).at(-1);}focus(){if(this.ownerDocument)this.ownerDocument.activeElement=this;}
}
const all=node=>[node,...node.children.flatMap(all)];
function actor(){return {id:'a',db_character_id:10000002,name:'神里绫华',level:90,constellation:0,weapon:null,artifact_sets:[],panel_basis_mode:'manual',panel:{atk:2000,hp:null,base_atk:null,base_hp:null,base_def:null,crit_rate:.66,crit_dmg:2.2,energy_recharge:1.4,dmg_bonus:{cryo:.61}},notes:''};}
function weapon(id=42){return {weapon:{id,name:`武器${id}`,weapon_type:'WEAPON_SWORD_ONE_HAND',max_level:90,available_states:[{ascension:0,min_level:1,max_level:20},{ascension:6,min_level:80,max_level:90}]},
  effects:[{source_index:0,affix_group_id:111401,refinements:[{refinement:1,name:'第一被动',desc_template:'提供第一收益'},{refinement:5,name:'第一被动',desc_template:'提供最高收益'}]},
    {source_index:3,affix_group_id:99999,refinements:[{refinement:1,name:'第二被动',desc_template:'提供第二收益'},{refinement:2,name:'第二被动',desc_template:'提供第二收益二阶'}]}],data_version:{version_line:'release',upstream_commit:'one'}};}
function basis(level=90,w=null){const supported=[90,100].includes(level),atk=supported?(level===100?380:340):null,weaponAtk=w?.level===90&&w?.ascension===6?674:null;
  return {state:atk!=null&&weaponAtk!=null?'ready':'partial',level,character_base_atk:atk,character_base_hp:supported?level*100:null,character_base_def:supported?level*10:null,
    panel_base:{base_atk:atk!=null&&weaponAtk!=null?atk+weaponAtk:null,base_hp:supported?level*100:null,base_def:supported?level*10:null},
    weapon_contribution:{base_atk:weaponAtk},missing_inputs:weaponAtk==null?[{message:'请选择已知等级及突破的武器'}]:[],source_refs:[{kind:'database',level}],
    data_version:{version_line:'release',upstream_commit:'one'}};}
function setup(options={}){
  const document={activeElement:null,listeners:{},addEventListener(type,fn){(this.listeners[type]||=[]).push(fn);},removeEventListener(type,fn){this.listeners[type]=(this.listeners[type]||[]).filter(value=>value!==fn);},
    emit(type,event={}){for(const fn of this.listeners[type]||[])fn({target:this,preventDefault(){},stopPropagation(){},...event});}};
  document.createElement=tag=>new Element(tag,document);document.body=new Element('body',document);document.documentElement={clientWidth:1280,clientHeight:800};
  const root=new Element('div',document),calls=[],changes=[],timers=new Map();document.body.append(root);let timer=0;
  const world={console,Math,Date,innerWidth:1280,innerHeight:800,setTimeout:fn=>{timers.set(++timer,fn);return timer;},clearTimeout:id=>timers.delete(id),setStatus(){},document,
    listeners:{},addEventListener(type,fn){(this.listeners[type]||=[]).push(fn);},removeEventListener(type,fn){this.listeners[type]=(this.listeners[type]||[]).filter(value=>value!==fn);}};world.window=world;
  world.DS={raw:(title,value)=>{const n=new Element('details');n.append(Object.assign(new Element('summary'),{textContent:title}),Object.assign(new Element('pre'),{textContent:JSON.stringify(value)}));return n;}};
  world.callApi=async(method,...args)=>{calls.push({method,args:clone(args)});if(method==='character_detail')return {code:0,data:{character:{weapon_type:'WEAPON_SWORD_ONE_HAND'}}};
    if(method==='character_panel_basis')return {code:0,data:basis(args[1],args[3])};
    if(method==='weapon_detail')return {code:0,data:weapon(args[0])};if(method==='artifact_set_detail')return {code:0,data:{artifact_set:{id:args[0],name:`套装${args[0]}`},effects:[
      {required_count:2,effect:{desc_template:'二件常驻收益'}},{required_count:4,effect:{desc_template:'四件条件收益'}}],data_version:{version_line:'release',upstream_commit:'one'}}};
    return {code:0,data:{items:[{id:42,name:'装备甲',rarity:5},{id:43,name:'装备乙',rarity:4}],total:2}};};
  vm.createContext(world);for(const file of ['ui.js','context-menu.js','equipment-workbench.js'])GUI_TEST_RUNTIME.loadWebFile(world, path.join(__dirname,'../src/genshin_app/gui/web',file), {filename:file});
  const source=options.actorDraft||actor(),editor=world.EW.mount(root,{actorDraft:source,versionLine:'release',onChange:(value,meta)=>changes.push({value:clone(value),meta:clone(meta)}),...options});
  const field=label=>{const node=all(document.body).find(n=>n.attributes['aria-label']===label);assert.ok(node,`field ${label}`);return node;},
    button=label=>{const node=all(document.body).find(n=>n.tagName==='button'&&n.textContent===label);assert.ok(node,`button ${label}`);return node;},
    change=(label,value)=>{const node=field(label);node.value=String(value);return node.emit('input');},select=(label,value)=>{const node=field(label);node.value=String(value);return node.emit('change');};
  const menu=label=>{const trigger=all(root).find(n=>n.tagName==='button'&&n.attributes['aria-label']===label);assert.ok(trigger,`menu ${label}`);trigger.emit('click');};
  return {world,document,root,editor,source,calls,changes,timers,field,button,menu,change,select,press:label=>button(label).emit('click'),flush:()=>{const pending=[...timers.values()];timers.clear();pending.forEach(fn=>fn());}};
}
const tick=()=>new Promise(resolve=>setImmediate(resolve));

test('equipment editor keeps growth, base values, destructive choices and search progressively disclosed',async()=>{
  const e=setup(),nodes=all(e.root),identity=nodes.find(node=>node.className==='ew-identity-section'),basisCard=nodes.find(node=>String(node.className||'').includes('ew-basis-card')),
    overflows=nodes.filter(node=>node.className==='ew-overflow-trigger'),search=nodes.find(node=>String(node.className||'').includes('ew-search-drawer'));
  assert.equal(identity?.tagName,'details');assert.equal(basisCard?.tagName,'details');assert.equal(overflows.length,2);
  assert.equal(search?.attributes.role,'dialog');assert.equal(search.hidden,true);
  const opener=e.button('选择或更换武器');await e.press('选择或更换武器');assert.equal(search.hidden,false);e.press('关闭装备搜索');assert.equal(search.hidden,true);assert.equal(e.document.activeElement,opener);
  await e.press('选择或更换武器');e.document.emit('pointerdown',{target:e.document.body});assert.equal(search.hidden,true);assert.equal(e.document.activeElement,opener);
});

test('capacity inputs remain unknown until explicitly entered and survive the shared draft',()=>{
  const e=setup();assert.equal(e.editor.draft().artifact_presence,undefined);assert.equal(e.editor.draft().talent_base_levels,undefined);
  assert.equal(e.field('生之花装备状态').value,'unknown');e.select('角色突破','6');e.change('战技天赋基级','10');e.select('命座','1');
  assert.equal(e.editor.draft().ascension,6);assert.equal(e.editor.draft().constellation,1);assert.deepEqual(clone(e.editor.draft().talent_base_levels),{skill:10});
  e.change('战技天赋基级','');assert.deepEqual(clone(e.editor.draft().talent_base_levels),{});
  e.change('战技天赋基级','13');assert.match(e.editor.validate().join(),/战技天赋基级/);
});
test('explicit empty-five state clears selected sets; set changes reset all presence to unknown',async()=>{
  const e=setup();e.menu('套装更多操作');e.press('确认五槽均未装备（清除套装）');assert.deepEqual(clone(Object.values(e.editor.draft().artifact_presence)),['empty','empty','empty','empty','empty']);
  e.select('套装组合','4');assert.deepEqual(clone(Object.values(e.editor.draft().artifact_presence)),['unknown','unknown','unknown','unknown','unknown']);
  await e.press('选择第 1 套');await e.press('装备甲 · 5星');e.select('生之花装备状态','empty');e.select('死之羽装备状态','empty');
  assert.match(e.editor.validate().join(),/槽位冲突/);e.select('套装组合','none');assert.deepEqual(clone(Object.values(e.editor.draft().artifact_presence)),['unknown','unknown','unknown','unknown','unknown']);
});
test('no-affix native weapon has explicit overall refinement; changing it does not invent effect slots',()=>{
  const source=actor();source.weapon={weapon_id:11,level:1,ascension:0,refinements:[],selected_by:'user'};
  const e=setup({actorDraft:source,weaponShape:'native'});assert.equal(e.editor.draft().weapon.refinement,undefined);
  e.select('武器整体精炼','5');assert.equal(e.editor.draft().weapon.refinement,5);assert.deepEqual(clone(e.editor.draft().weapon.refinements),[]);
  e.select('武器整体精炼','');assert.equal(e.editor.draft().weapon.refinement,undefined);
});
test('native overall refinement explicitly aligns legacy effect groups and weapon replacement rebuilds slots',async()=>{
  const source=actor();source.weapon={weapon_id:99,level:90,ascension:6,refinement:5,refinements:[{source_index:9,affix_group_id:999,refinement:5}]};
  const e=setup({actorDraft:source,weaponShape:'native'});await e.press('选择或更换武器');await e.press('装备甲 · 5星');
  assert.equal(e.editor.draft().weapon.refinement,1);assert.deepEqual(clone(e.editor.draft().weapon.refinements.map(row=>row.source_index)),[0,3]);
  e.select('武器整体精炼','2');assert.deepEqual(clone(e.editor.draft().weapon.refinements.map(row=>row.refinement)),[2,2]);
});

test('panel input is local, percentage values round-trip, unknowns remain unknown, and focus nodes survive changes',()=>{
  const e=setup(),original=clone(e.source),attack=e.field('攻击力');e.change('攻击力','3200');e.change('暴击率 %','77');e.change('生命值','');e.change('冰伤害加成 %','46.6');
  assert.equal(e.editor.draft().panel.atk,3200);assert.equal(e.editor.draft().panel.crit_rate,.77);assert.equal(e.editor.draft().panel.hp,null);
  assert.equal(e.editor.draft().panel.dmg_bonus.cryo,.466);assert.deepEqual(e.source,original);assert.equal(e.field('攻击力'),attack);assert.deepEqual(clone(e.editor.validate()),[]);
  e.change('冰伤害加成 %','');assert.equal(e.editor.draft().panel.dmg_bonus.cryo,undefined);
});
test('invalid numbers cannot silently become a valid old panel and block apply until corrected',()=>{
  const e=setup();e.change('攻击力','-3');assert.match(e.editor.validate().join(),/攻击力/);assert.equal(e.editor.draft().panel.atk,2000);
  e.change('攻击力','3');assert.deepEqual(clone(e.editor.validate()),[]);e.change('角色等级','1.5');assert.match(e.editor.validate().join(),/整数/);
  e.change('角色等级','90');const field=e.field('攻击力');field.validity={badInput:true};e.change('攻击力','');assert.match(e.editor.validate().join(),/攻击力/);
});
test('native weapon choice keeps every source-index refinement group and does not infer panel',async()=>{
  const e=setup({weaponShape:'native'}),panel=clone(e.editor.draft().panel);await e.press('选择或更换武器');assert.equal(e.calls.find(c=>c.method==='list_weapons').args[1],'WEAPON_SWORD_ONE_HAND');
  await e.press('装备甲 · 5星');assert.deepEqual(clone(e.editor.draft().weapon.refinements),[{source_index:0,affix_group_id:111401,refinement:1},{source_index:3,affix_group_id:99999,refinement:1}]);
  e.select('精炼 · 效果组 4','2');assert.equal(e.editor.draft().weapon.refinements[1].refinement,2);assert.deepEqual(clone(e.editor.draft().panel),panel);
  assert.match(e.editor.validate().join(),/勾选确认/);const confirm=e.field('已核对当前面板对应所选装备');confirm.checked=true;confirm.emit('change');assert.deepEqual(clone(e.editor.validate()),[]);
  assert.equal(e.changes.find(c=>c.meta.dataVersion).meta.dataVersion.upstream_commit,'one');
});
test('whiteboard weapon selection uses scalar refinement and correct low-rarity level limits',async()=>{
  const e=setup({weaponShape:'whiteboard'}),old=e.world.callApi;e.world.callApi=async(method,...args)=>method==='weapon_detail'?{code:0,data:{...weapon(args[0]),weapon:{...weapon(args[0]).weapon,max_level:70,available_states:[{ascension:4,min_level:60,max_level:70}]}}}:old(method,...args);
  await e.press('选择或更换武器');await e.press('装备甲 · 5星');const w=e.editor.draft().weapon;assert.equal(w.level,70);assert.equal(w.ascension,4);assert.equal(w.refinement,1);assert.equal(w.refinements,undefined);
});
test('selecting the already selected weapon preserves refinement and panel without false name-based confirmation',async()=>{
  const source=actor();source.weapon={weapon_id:42,level:80,ascension:6,refinements:[{source_index:0,refinement:5},{source_index:3,refinement:2}]};
  const e=setup({actorDraft:source,weaponShape:'native'});await tick();await e.press('选择或更换武器');await e.press('装备甲 · 5星 · 当前选择');
  assert.equal(e.editor.draft().weapon.level,80);assert.equal(e.editor.draft().weapon.refinements[0].refinement,5);assert.deepEqual(clone(e.editor.validate()),[]);
});
test('2+2 cannot duplicate a set and changing combination never rewrites given stats',async()=>{
  const e=setup(),panel=clone(e.editor.draft().panel);e.select('套装组合','2+2');assert.equal(e.button('选择第 2 套').disabled,true);
  await e.press('选择第 1 套');await e.press('装备甲 · 5星');await e.press('选择第 2 套');await e.press('装备甲 · 5星');assert.match(e.root.textContent,/两个不同套装/);
  await e.press('装备乙 · 4星');assert.deepEqual(clone(e.editor.draft().artifact_sets.map(s=>[s.set_id,s.count])),[[42,2],[43,2]]);
  assert.doesNotMatch(all(e.root).find(n=>n.className==='ew-set-selections').textContent,/四件条件收益/);
  e.select('套装组合','4');assert.deepEqual(clone(e.editor.draft().artifact_sets.map(s=>[s.set_id,s.count])),[[42,4]]);
  assert.match(e.root.textContent,/四件条件收益/);e.select('套装组合','none');assert.deepEqual(clone(e.editor.draft().artifact_sets),[]);assert.deepEqual(clone(e.editor.draft().panel),panel);
});
test('search is debounced, keeps its input element, and applies the latest Chinese query',async()=>{
  const e=setup();e.select('套装组合','4');await e.press('选择第 1 套');const input=e.field('按中文名搜索');input.value='冰';input.emit('input');input.value='冰风';input.emit('input');
  assert.equal(e.timers.size,1);e.flush();await tick();assert.equal(e.calls.filter(c=>c.method==='list_artifact_sets').at(-1).args[0],'冰风');assert.equal(e.field('按中文名搜索'),input);
});
test('late search responses cannot override a newer query',async()=>{
  const e=setup();e.select('套装组合','4');await e.press('选择第 1 套');const finishes=[];e.world.callApi=()=>new Promise(resolve=>finishes.push(resolve));
  e.field('按中文名搜索').value='旧';e.press('搜索');e.field('按中文名搜索').value='新';e.press('搜索');
  finishes[1]({code:0,data:{items:[{id:99,name:'新结果'}],total:1}});await tick();finishes[0]({code:0,data:{items:[{id:98,name:'旧结果'}],total:1}});await tick();
  assert.match(e.document.body.textContent,/新结果/);assert.doesNotMatch(e.document.body.textContent,/旧结果/);
});
test('query waits for character weapon type even if the user types immediately',async()=>{
  const e=setup();let finish;const calls=[];e.world.callApi=async(method,...args)=>{calls.push({method,args});if(method==='character_detail')return new Promise(resolve=>finish=resolve);return {code:0,data:{items:[],total:0}};};
  const opening=e.press('选择或更换武器');e.field('按中文名搜索').value='西风';e.press('搜索');assert.equal(calls.filter(c=>c.method==='list_weapons').length,0);
  finish({code:0,data:{character:{weapon_type:'WEAPON_SWORD_ONE_HAND'}}});await opening;assert.equal(calls.at(-1).args[0],'西风');assert.equal(calls.at(-1).args[1],'WEAPON_SWORD_ONE_HAND');
});
test('closing a search or destroying the editor cancels late item selection',async()=>{
  const e=setup();await e.press('选择或更换武器');let finish;e.world.callApi=()=>new Promise(resolve=>finish=resolve);const selecting=e.press('装备甲 · 5星');e.press('关闭装备搜索');
  finish({code:0,data:weapon()});await selecting;assert.equal(e.editor.draft().weapon,null);
  const field=e.field('攻击力');e.editor.destroy();field.value='9999';field.emit('input');assert.equal(e.editor.draft().panel.atk,2000);
});
test('late detail hydration updates descriptions without replacing input nodes or typed values',async()=>{
  const source=actor();source.weapon={weapon_id:42,level:90,ascension:6,refinement:1};let finish;
  const e=setup({actorDraft:source,api:()=>new Promise(resolve=>finish=resolve)}),input=e.field('攻击力');e.change('攻击力','3400');
  finish({code:0,data:weapon()});await tick();assert.equal(e.field('攻击力'),input);assert.equal(e.editor.draft().panel.atk,3400);assert.match(e.root.textContent,/提供第一收益/);
});
test('pinned data version prevents silently mixing latest equipment',async()=>{
  const e=setup({dataVersion:{version_line:'release',upstream_commit:'older'}});await e.press('选择或更换武器');await e.press('装备甲 · 5星');
  assert.equal(e.editor.draft().weapon,null);assert.match(e.root.textContent,/资料版本不同/);
});
test('confirmation requirement survives remount while unknown weapon states can be saved pending',()=>{
  const source=actor();source.weapon={weapon_id:42,level:null,ascension:null,refinements:[{source_index:0,refinement:null}]};
  const e=setup({actorDraft:source,weaponShape:'native',requirePanelConfirmation:true,panelConfirmed:false});assert.match(e.editor.validate().join(),/勾选确认/);
  const confirm=e.field('已核对当前面板对应所选装备');confirm.checked=true;confirm.emit('change');assert.deepEqual(clone(e.editor.validate()),[]);
  const second=setup({actorDraft:e.editor.draft(),weaponShape:'native',requirePanelConfirmation:true,panelConfirmed:true});assert.deepEqual(clone(second.editor.validate()),[]);
});

test('bound legacy character reads only database base fields, preserving finished stats and source object',async()=>{
  const source=actor();delete source.panel_basis_mode;source.panel.base_atk=999;source.panel.base_hp=123;const before=clone(source),e=setup({actorDraft:source});
  assert.match(e.editor.validate().join(),/仍在读取/);assert.equal(e.editor.draft().panel.base_atk,null);assert.equal(e.field('基础生命值').readOnly,true);
  const attack=e.field('攻击力');e.change('攻击力','3200');await tick();
  assert.equal(e.editor.draft().panel.base_atk,null);assert.equal(e.editor.draft().panel.base_hp,9000);assert.equal(e.editor.draft().panel.base_def,900);
  assert.equal(e.editor.draft().panel.atk,3200);assert.equal(e.editor.draft().panel.hp,null);assert.equal(e.editor.draft().panel.crit_rate,.66);assert.equal(e.field('攻击力'),attack);
  assert.deepEqual(source,before);assert.match(e.root.textContent,/角色白攻 340 \+ 武器白攻 未知 = 基础攻击 未知/);assert.match(e.root.textContent,/草稿基础值变化/);
  assert.match(e.root.textContent,/数据库基础属性来源/);assert.deepEqual(clone(e.editor.validate()),[]);
});

test('90 and 100 quick choices recalculate matching character and weapon white attack without changing finished panel',async()=>{
  const source=actor();source.panel_basis_mode='database';source.weapon={weapon_id:42,level:90,ascension:6,refinement:1};const e=setup({actorDraft:source});await tick();
  assert.equal(e.editor.draft().panel.base_atk,1014);e.press('100 级');assert.equal(e.editor.draft().level,100);assert.equal(e.editor.draft().panel.base_atk,null);await tick();
  assert.equal(e.editor.draft().panel.base_atk,1054);assert.equal(e.editor.draft().panel.base_hp,10000);assert.equal(e.editor.draft().panel.atk,2000);
  const before=e.calls.filter(c=>c.method==='character_panel_basis').length;e.select('精炼','5');e.select('套装组合','none');await tick();
  assert.equal(e.calls.filter(c=>c.method==='character_panel_basis').length,before);
  e.change('武器等级','100');await tick();assert.equal(e.editor.draft().panel.base_atk,null);assert.equal(e.editor.draft().panel.base_hp,10000);
  e.change('武器等级','90');await tick();assert.equal(e.editor.draft().panel.base_atk,1054);
  e.menu('武器更多操作');e.press('清除武器选择');await tick();assert.equal(e.editor.draft().panel.base_atk,null);assert.equal(e.editor.draft().panel.base_hp,10000);
});

test('out-of-order database responses cannot restore old level values or replace focused finished input',async()=>{
  const source=actor();source.panel_basis_mode='database';const responses=[],e=setup({actorDraft:source,api:(method,...args)=>new Promise(resolve=>responses.push({method,args,resolve}))});
  e.press('100 级');const attack=e.field('攻击力');e.change('攻击力','3500');
  responses[1].resolve({code:0,data:basis(100)});await tick();responses[0].resolve({code:0,data:basis(90)});await tick();
  assert.equal(e.editor.draft().panel.base_hp,10000);assert.equal(e.editor.draft().panel.atk,3500);assert.equal(e.field('攻击力'),attack);
});

test('switching to manual overrides pending database work and persists that mode across reopening',async()=>{
  const source=actor();source.panel_basis_mode='database';let finish;const e=setup({actorDraft:source,api:()=>new Promise(resolve=>finish=resolve)});
  e.select('基础值来源','manual');e.change('基础生命值','12345');e.change('基础攻击力（角色 + 武器白值）','888');finish({code:0,data:basis()});await tick();
  assert.equal(e.editor.draft().panel.base_hp,12345);assert.equal(e.editor.draft().panel.base_atk,888);assert.equal(e.editor.draft().panel_basis_mode,'manual');
  const reopened=setup({actorDraft:e.editor.draft()});assert.equal(reopened.calls.filter(c=>c.method==='character_panel_basis').length,0);assert.equal(reopened.editor.draft().panel.base_atk,888);
  reopened.press('100 级');assert.equal(reopened.editor.draft().panel.base_atk,null);assert.equal(reopened.editor.draft().panel.base_hp,null);assert.match(reopened.root.textContent,/旧来源数值已清空/);
});

test('unsupported levels and failed or stale-snapshot reads never keep a previous level base',async()=>{
  const source=actor();source.panel_basis_mode='database';source.panel.base_hp=999;const e=setup({actorDraft:source});await tick();e.change('角色等级','80');await tick();
  assert.equal(e.editor.draft().panel.base_hp,null);assert.equal(e.calls.filter(c=>c.method==='character_panel_basis').at(-1).args[1],80);
  e.world.callApi=async()=>({code:1,message:'读取中断'});e.press('90 级');await tick();assert.equal(e.editor.draft().panel.base_hp,null);assert.match(e.root.textContent,/读取中断/);
  const stale=setup({actorDraft:source,dataVersion:{version_line:'release',upstream_commit:'old'}});await tick();assert.equal(stale.editor.draft().panel.base_hp,null);assert.match(stale.root.textContent,/资料版本不同/);
  const missing=setup({actorDraft:source,dataVersion:{version_line:'release',upstream_commit:'one'},api:async()=>({code:0,data:{...basis(),data_version:null}})});await tick();
  assert.equal(missing.editor.draft().panel.base_hp,null);assert.match(missing.root.textContent,/无法核验.*来源快照/);
});

test('destroyed or superseded editors ignore delayed basis results',async()=>{
  for(const invalidate of ['destroy','context']){const source=actor();source.panel_basis_mode='database';let finish,current=true;
    const e=setup({actorDraft:source,isCurrent:()=>current,api:()=>new Promise(resolve=>finish=resolve)});const before=e.changes.length;
    if(invalidate==='destroy')e.editor.destroy();else current=false;finish({code:0,data:basis()});await tick();
    assert.equal(e.editor.draft().panel.base_hp,null);assert.equal(e.changes.length,before);}
});

test('preview remount reuses a matching verified basis without clearing composed values or mutating the draft',async()=>{
  const source=actor();source.panel_basis_mode='database';const e=setup({actorDraft:source});await tick();
  const next=setup({actorDraft:e.editor.draft(),basisSnapshot:e.editor.basisSnapshot()});await tick();
  assert.equal(next.calls.filter(c=>c.method==='character_panel_basis').length,0);assert.equal(next.changes.length,0);assert.equal(next.editor.draft().panel.base_hp,9000);
  const changed=e.editor.draft();changed.level=100;const other=setup({actorDraft:changed,basisSnapshot:e.editor.basisSnapshot()});await tick();
  assert.equal(other.calls.filter(c=>c.method==='character_panel_basis').length,1);assert.equal(other.editor.draft().panel.base_hp,10000);
});

test('unbound custom characters stay manual and automatic mode cannot pretend to have known base values',()=>{
  const source=actor();delete source.db_character_id;delete source.panel_basis_mode;source.panel.base_atk=777;const e=setup({actorDraft:source});
  assert.equal(e.editor.draft().panel_basis_mode,'manual');assert.equal(e.editor.draft().panel.base_atk,777);assert.equal(e.calls.length,0);
  e.select('基础值来源','database');assert.equal(e.editor.draft().panel.base_atk,null);assert.match(e.root.textContent,/请绑定数据库角色/);assert.equal(e.editor.draft().panel.atk,2000);
});
