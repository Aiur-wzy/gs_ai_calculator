const GUI_TEST_RUNTIME = require('./helpers/gui_runtime.cjs');
const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');
const fixture=JSON.parse(fs.readFileSync(path.join(__dirname,'fixtures/gui_equipment_queries.json'),'utf8'));
class Element{
  constructor(tag='div'){this.tagName=tag;this.children=[];this.attributes={};this.listeners={};this.style={};this.value='';this.clientWidth=1000;
    this.classList={add(){},remove(){},toggle(){}};}
  append(...nodes){this.children.push(...nodes);} appendChild(n){this.append(n);return n;}
  replaceChildren(...nodes){this.children=[...nodes];}
  insertRow(){return this.appendChild(new Element('tr'));}insertCell(){return this.appendChild(new Element('td'));}
  set innerHTML(v){this.children=[];this.text=v;} set textContent(v){this.children=[];this.text=String(v);}get textContent(){return(this.text||'')+this.children.map(n=>n.textContent).join('');}
  setAttribute(k,v){this.attributes[k]=v;}addEventListener(k,v){this.listeners[k]=v;}removeEventListener(){}
  emit(k){return this.listeners[k]?.({target:this,preventDefault(){}});}get childElementCount(){return this.children.length;}
  showModal(){}close(){}remove(){} }
const all=n=>[n,...n.children.flatMap(all)],clone=x=>JSON.parse(JSON.stringify(x));
const expandResult=(nodes,section)=>{const node=all(nodes.get('#result-body')).find(row=>row.className?.includes(`result-section-${section}`));assert.ok(node);node.open=true;node.emit('toggle');};
function setup({resultRows=false,workbench=false}={}){const nodes=new Map(),body=new Element('body'),world={console,Date,Math,setTimeout:globalThis.setTimeout,clearTimeout:globalThis.clearTimeout,confirm:()=>true,prompt:()=>null,addEventListener(){}};
  world.window=world;world.document={body,createElement:t=>new Element(t),createElementNS:(_,t)=>new Element(t),
    querySelector:s=>{if(s.startsWith('#')){const found=[...nodes.values(),body].flatMap(all).find(n=>n.id===s.slice(1));if(found)return found;}
      if(!nodes.has(s))nodes.set(s,new Element());return nodes.get(s);},querySelectorAll:()=>[],getElementById:id=>[...nodes.values(),body].flatMap(all).find(n=>n.id===id)};
  world.pywebview={api:Object.fromEntries(Object.entries(fixture).map(([k,v])=>[k,async()=>clone(v)]))};vm.createContext(world);
  const web=path.join(__dirname,'../src/genshin_app/gui/web');
  for(const file of ['display','rotation','panel','chars','timeline-view','timeline','result','format','data','ui','plans','equipment-data','equipment','effect-editor','checks','mechanics','app'])
    GUI_TEST_RUNTIME.loadWebFile(world, path.join(web,`${file}.js`), {filename:`${file}.js`});
  if(workbench)for(const file of ['equipment-workbench','equipment-workbench-native'])
    GUI_TEST_RUNTIME.loadWebFile(world, path.join(web,`${file}.js`), {filename:`${file}.js`});
  const displayFixture=JSON.parse(fs.readFileSync(path.join(__dirname,'fixtures/gui_display.json'),'utf8'));
  world.DS.configure(displayFixture.display);world.DS.catalogs.set('release',displayFixture.catalog);
  world.RT.state.enums={elements:['physical','pyro'],damage_systems:['classic'],scaling_stats:['atk','hp','def','em'],buff_zones:['dmg_bonus'],buff_scopes:['team','frontline'],
    reaction_types:{classic:['amplify']},engine_v2:fixture.plan_capabilities.data.engine_v2};
  world.PL.state.caps=fixture.plan_capabilities.data;world.ED.caps=fixture.db_capabilities.data;
  world.RS.renderSummary=()=>new Element();
  if(!resultRows)world.RS.renderHits=world.RS.renderCoverage=()=>new Element();
  world.RT.renderTimeline=()=>{};world.RT.refreshPlans=async()=>{};
  world.RT.state.plansDir='plans';
  const plan=clone(fixture.new_plan.data);plan.manual_template.characters=[{id:'c1',level:90,stats:{atk:1000,crit_rate:0,crit_dmg:0}}];
  plan.manual_template.hits=[{id:'h1',damage_type_ids:['damage.elemental_burst'],t:1,character:'c1',skill:'burst',damage_system:'classic',element:'physical',multiplier:1}];
  plan.resolution={state:'ready'};plan.resolved_template=clone(plan.manual_template);world.PL.replace(plan,'plans/p.json','sha1',true);
  return {world,nodes,...Object.fromEntries(['PL','EQ','EF','ED','RT','RS','UI'].map(k=>[k,world[k]]))};}
const control=(node,label)=>all(node).find(n=>n.attributes['aria-label']===label);

function capacitySetup(){const env=setup(),{world,PL}=env;
  GUI_TEST_RUNTIME.loadWebFile(world,path.join(__dirname,'../src/genshin_app/gui/web/action-capacities.js'),{filename:'action-capacities.js'});
  const plan=PL.state.plan;plan.plan_version=3;plan.rotation_model={characters:[{id:'c1'}],abilities:[{id:'e',character:'c1',action_type_id:'action.elemental_skill',variant:'tap',cooldown:{group:'e',duration:10,start:'action_start',charges:1,recovery:'sequential'}}],actions:[],events:[]};
  return {...env,row:{id:'capacity',actor:'c1',ability_ids:['e'],version_line:'release',model_ref:{id:'reviewed',revision:1},target:{character_id:1,skill_depot_id:2,skill_id:3,variant:'tap'},accepted_fingerprint:null}};
}
const capacityCatalogItem=()=>({model_ref:{id:'reviewed',revision:1},target:{character_id:1,skill_depot_id:2,skill_id:3,variant:'tap'},
  review:{status:'approved'},selection_status:'reference_selectable_for_fresh_preview',declared_filter:{status:'exact_match'},
  display:{character:{value:'测试角色'},skill:{value:'测试战技'},weapon:{value:'测试武器'}}});
const capacityCatalogPage=()=>({code:0,data:{items:[capacityCatalogItem()],counts:{filtered_models:1},has_more:false,next_cursor:null}});
test('capacity binding requires explicit field ownership and scenario, without creating ready queues or moving actions',()=>{
  const {world,PL,row}=capacitySetup(),original=clone(PL.state.plan),opts={takeover:true,contextConfirmed:true};
  assert.throws(()=>world.CAP.propose(original,row),/手填次数/);
  assert.throws(()=>world.CAP.propose(original,row,undefined,{takeover:true}),/确认/);
  const p=world.CAP.propose(original,row,undefined,opts);assert.equal(p.rotation_model.abilities[0].cooldown.charges,undefined);assert.equal(p.rotation_model.initial_cooldowns,undefined);
  assert.deepEqual(clone(p.rotation_model.actions),original.rotation_model.actions);assert.equal(original.rotation_model.abilities[0].cooldown.charges,1);
  const explicit=world.CAP.propose(original,row,undefined,{...opts,initial:[]});assert.deepEqual(clone(explicit.rotation_model.initial_cooldowns),[{character:'c1',group:'e',ready_in:[]}]);
});
test('capacity UI rejects omitted pool members, wrong action variants and competing source ownership',()=>{
  const {world,PL,row}=capacitySetup(),original=clone(PL.state.plan),opts={takeover:true,contextConfirmed:true};
  original.rotation_model.abilities.push({...clone(original.rotation_model.abilities[0]),id:'normal',action_type_id:'action.normal'});
  assert.throws(()=>world.CAP.propose(original,row,undefined,opts),/列全/);row.ability_ids.push('normal');assert.throws(()=>world.CAP.propose(original,row,undefined,opts),/点按/);
  original.rotation_model.abilities.pop();row.ability_ids.pop();original.action_resource_sources=[{ability_id:'e'}];assert.throws(()=>world.CAP.propose(original,row,undefined,opts),/双重拥有/);
  delete original.action_resource_sources;original.rotation_model.abilities[0].variant='press';assert.throws(()=>world.CAP.propose(original,row,undefined,opts),/点按/);
});
test('capacity preview needs a stable fresh server fingerprint and never copies compiled charges into native inputs',async()=>{
  const {world,PL}=capacitySetup();await new Promise(resolve=>setImmediate(resolve));let fingerprint='a'.repeat(64),count=0;
  world.callApi=async(method,plan)=>{if(method==='action_capacity_model_candidates')return capacityCatalogPage();assert.equal(method,'compose_equipment_plan');count++;const row=plan.action_capacity_sources[0];return {code:0,data:{state:'pending',action_capacity_compilations:[{id:row.id,status:row.accepted_fingerprint===fingerprint?'ready':'pending',projection:{capacity:2,pool:{actor:'c1',group:'e',members:[{cooldown:{duration:10,start:'action_start',recovery:'sequential'}}],initial:{ready_in:[]}},profile:{model_record:{id:'reviewed',revision:1,payload:{claim:{base:{value:1}}}}}},issues:[],required_confirmation:{accepted_fingerprint:fingerprint}}]}};};
  world.CAP.edit();const dialog=world.document.body.children.at(-1),button=label=>all(dialog).find(n=>n.tagName==='button'&&n.textContent===label),check=label=>{const input=control(dialog,label);input.checked=true;input.emit('change');};
  await new Promise(resolve=>setImmediate(resolve));check('将所选能力的手填次数交由受审模型填写');await button('一键使用普通开放世界预设').emit('click');await new Promise(resolve=>setImmediate(resolve));await button('选择此模型').emit('click');check('明确开场次数全部就绪');
  await button('确认所示来源').emit('click');assert.equal(count,0);await button('读取并预览').emit('click');assert.equal(count,1,dialog.textContent);assert.match(dialog.textContent,/最大次数：2/);fingerprint='b'.repeat(64);
  await button('确认所示来源').emit('click');assert.match(dialog.textContent,/已变化/);assert.equal(PL.state.plan.action_capacity_sources,undefined);
  await button('确认所示来源').emit('click');await button('应用次数来源').emit('click');assert.equal(PL.state.plan.action_capacity_sources[0].accepted_fingerprint,fingerprint);
  assert.equal(PL.state.plan.rotation_model.abilities[0].cooldown.charges,undefined);
});
test('capacity UI ignores late responses after current source epoch changes',async()=>{
  const {world,PL}=capacitySetup();await new Promise(resolve=>setImmediate(resolve));let finish;world.callApi=async(method,...args)=>{if(method==='action_capacity_model_candidates')return capacityCatalogPage();return await new Promise(resolve=>finish=resolve);};world.CAP.edit();const dialog=world.document.body.children.at(-1);
  await new Promise(resolve=>setImmediate(resolve));const takeover=control(dialog,'将所选能力的手填次数交由受审模型填写');takeover.checked=true;takeover.emit('change');const preset=all(dialog).find(n=>n.tagName==='button'&&n.textContent==='一键使用普通开放世界预设');assert.ok(preset,dialog.textContent);await preset.emit('click');await new Promise(resolve=>setImmediate(resolve));const choose=all(dialog).find(n=>n.tagName==='button'&&n.textContent==='选择此模型');assert.ok(choose,dialog.textContent);await choose.emit('click');
  const reading=all(dialog).find(n=>n.tagName==='button'&&n.textContent==='读取并预览').emit('click');PL.state.sourceEpoch=(PL.state.sourceEpoch||0)+1;
  finish({code:0,data:{state:'ready',action_capacity_compilations:[{id:'forged',status:'ready'}]}});await reading;assert.doesNotMatch(dialog.textContent,/forged/);assert.equal(PL.state.plan.action_capacity_sources,undefined);
});
test('capacity result is a separate read-only resource detail with committed, rejected and unchecked records',()=>{
  const {world}=capacitySetup(),box=new Element();world.CAP.renderResult(box,{rotation_check:{action_capacity_consumption:{planned_actions:2,committed_actions:1,rejected_actions:1,receipts:[{t:0,character:'c1',action_id:'a',ability_id:'e',capacity:2,before:1,after:0,ready_at:10,next_ready_at:10,queue_before:[1],queue_after:[1,10],status:'committed',binding_id:'cap'}]},event_log:[{t:2,id:'b',capacity_admission:{status:'not_verified'}}]}},world.RT.tpl());
  assert.match(box.textContent,/1 → 0 次/);assert.match(box.textContent,/后续未验证/);assert.match(box.textContent,/不是伤害/);assert.equal(all(box).filter(n=>n.tagName==='input').length,0);
  assert.deepEqual(all(box).filter(n=>n.tagName==='button').map(n=>n.textContent),['上一页','下一页']);
  assert.equal(world.RC.eventName('mechanics_hit'),'机制命中处理');assert.equal(world.RC.eventName('mechanics_end'),'动作结束处理');assert.equal(world.RC.eventName('mystery'),'待补充');
});
const mechanicsCaps=JSON.parse(require('node:child_process').execFileSync('python',['-X','utf8','-c',
  'import json; from genshin_engine.mechanics_schema import mechanics_capabilities; print(json.dumps(mechanics_capabilities(),ensure_ascii=False))'],
  {cwd:path.join(__dirname,'..'),encoding:'utf8'}));
function mechanicsSetup(){const setupResult=setup(),{world,PL}=setupResult;
  const plan=JSON.parse(fs.readFileSync(path.join(__dirname,'../examples/declared-mechanics-plan.json'),'utf8'));
  world.MC.caps=clone(mechanicsCaps);PL.replace(plan,'engineering.json','original-sha',true);
  world.pywebview.api.validate_mechanics_model=async()=>({code:0,data:{status:'passed',issues:[]}});
  return {...setupResult,MC:world.MC,dialog:()=>world.document.body.children.at(-1)};}
const press=(node,label)=>{const button=all(node).find(n=>n.tagName==='button'&&n.textContent===label);assert.ok(button,`Missing ${label}`);return button.emit('click');};
test('native workbench edits finished panel without adding static gear or changing actions',async()=>{
  const env=setup({workbench:true}),{PL,EQ,world,nodes}=env,before=clone(PL.state.plan.manual_template.hits);
  await PL.begin('c1');const editor=nodes.get('#equipment-editor'),attack=control(editor,'攻击力'),crit=control(editor,'暴击率 %');
  attack.value='2400';attack.emit('input');crit.value='66';crit.emit('input');
  assert.equal(PL.state.draft.manual_template.characters[0].stats.atk,2400);
  assert.equal(PL.state.draft.manual_template.characters[0].stats.crit_rate,.66);
  assert.equal(PL.state.draft.equipment_inputs.c1.mode,'panel');
  assert.equal(PL.state.draft.equipment_inputs.c1.panel_policy,'static_included');
  assert.deepEqual(clone(PL.state.draft.manual_template.hits),before);
  attack.value='';attack.emit('input');assert.equal(Object.hasOwn(PL.state.draft.manual_template.characters[0].stats,'atk'),false);
  assert.equal(PL.state.plan.manual_template.characters[0].stats.atk,1000);
  assert.equal(EQ.validateWorkbench(),true);
});
test('native equipment footer keeps one primary path and moves secondary save into overflow',async()=>{
  const {PL,nodes}=setup({workbench:true});await PL.begin('c1');const editor=nodes.get('#equipment-editor'),footer=all(editor).find(node=>String(node.className||'').includes('ew-actions'));
  assert.ok(footer);assert.deepEqual(footer.children.filter(node=>node.tagName==='button').map(node=>node.textContent),['应用并计算','预览变化','放弃本次修改']);
  const overflow=footer.children.find(node=>node.tagName==='details');assert.ok(overflow);assert.match(overflow.textContent,/更多.*保存副本交给 Agent/);
});
test('legacy equipment rows keep creation visible and relocate destructive commands by stable reference',async()=>{
  const {world,PL,EQ,UI}=setup(),opened=[],statuses=[];world.AppContextMenu={forDocument:()=>({open:config=>{opened.push(config);return true;},bind(){}})};world.setStatus=message=>statuses.push(message);
  await PL.begin('c1');EQ.characterId='c1';const input=EQ.blankInput(90);input.basis.ascension_contributions=[
    {contribution_id:'first',stat:'atk',operation:'flat',value:1,source_refs:[]},{contribution_id:'second',stat:'atk',operation:'flat',value:2,source_refs:[]}];
  PL.state.draft.equipment_inputs.c1=input;const box=UI.el('div');EQ.contributions(box,'角色突破贡献',input.basis.ascension_contributions,'ascension');
  const buttons=all(box).filter(node=>node.tagName==='button');assert.deepEqual(buttons.map(node=>node.textContent),['⋯','⋯','添加角色突破贡献']);
  buttons[0].emit('click');input.basis.ascension_contributions.reverse();opened.at(-1).items.find(item=>item.id==='remove').run();
  assert.deepEqual(input.basis.ascension_contributions.map(row=>row.contribution_id),['second'],statuses.join(' | '));opened.length=0;
  const stale=UI.el('div');EQ.contributions(stale,'角色突破贡献',input.basis.ascension_contributions,'ascension');all(stale).find(node=>node.tagName==='button'&&node.textContent==='⋯').emit('click');
  input.basis.ascension_contributions=[];opened.at(-1).items.find(item=>item.id==='remove').run();assert.match(statuses.at(-1),/已变化或移除/);
});
test('action and energy definitions expose row operations through one contextual trigger',()=>{
  const {world}=setup(),items=[{id:'ability-one'}],list=world.RC.list('技能定义',items,row=>row.id,()=>{},()=>{}),row=all(list).find(node=>node.className==='eq-toolbar rc-definition-row');
  assert.ok(row);assert.deepEqual(row.children.filter(node=>node.tagName==='button').map(node=>node.textContent),['⋯']);
  assert.deepEqual(Array.from(world.RC.definitionMenuItems('技能定义',items,0,()=>{}).filter(item=>item.id),item=>item.label),['编辑','删除']);
});
test('native equipment preview passes immutable prior inputs even after repeated previews',async()=>{
  const {PL,EQ,world}=setup({workbench:true});await PL.begin('c1');const calls=[];
  world.pywebview.api.preview_equipment_edit=async(plan,previous)=>{calls.push(clone(previous));plan.resolution={state:'pending',missing_inputs:[{message:'旧效果需复核'}]};return {code:0,data:{plan,state:'pending',missing_inputs:plan.resolution.missing_inputs}};};
  PL.state.draft.manual_template.characters[0].stats.atk=2200;PL.touch();
  assert.equal(await PL.preview(),true);assert.equal(await PL.preview(),true);
  assert.equal(calls[0].manual_template.characters[0].stats.atk,1000);assert.deepEqual(calls[0],calls[1]);
  assert.match(world.document.querySelector('#equipment-preview').textContent,/旧效果需复核/);
});

test('native database basis alignment is draft-only and preview remount keeps resolved inputs intact',async()=>{
  const {PL,EQ,world,nodes}=setup({workbench:true}),version={version_line:'release',upstream_commit:'fixture'};
  const input=EQ.blankPanelInput();input.character_ref={db_character_id:10000002,version_line:'release'};input.data_version=version;
  PL.state.plan.equipment_inputs.c1=input;let queries=0;
  world.pywebview.api.character_panel_basis=async(_id,level)=>{queries++;return {code:0,data:{state:'partial',data_version:version,character_base_atk:level===100?380:340,
    character_base_hp:level*100,character_base_def:level*10,panel_base:{base_atk:null,base_hp:level*100,base_def:level*10},weapon_contribution:{base_atk:null},source_refs:[],missing_inputs:[]}};};
  await PL.begin('c1');await new Promise(resolve=>setImmediate(resolve));
  assert.equal(PL.state.draft.manual_template.characters[0].stats.base_hp,9000);assert.equal(PL.state.plan.manual_template.characters[0].stats.base_hp,undefined);
  assert.equal(PL.state.draft.equipment_inputs.c1.panel_basis_mode,'database');
  world.pywebview.api.preview_equipment_edit=async plan=>({code:0,data:{plan:{...plan,resolution:{state:'ready'},resolved_template:clone(plan.manual_template)},state:'ready'}});
  assert.equal(await PL.preview(),true);assert.equal(queries,1);assert.equal(PL.state.report.state,'ready');assert.equal(PL.state.draft.resolution.state,'ready');
  assert.equal(PL.state.draft.manual_template.characters[0].stats.base_hp,9000);
  press(nodes.get('#equipment-editor'),'100 级');await new Promise(resolve=>setImmediate(resolve));assert.equal(PL.state.draft.manual_template.characters[0].stats.base_hp,10000);
  assert.equal(PL.state.draft.manual_template.characters[0].stats.atk,1000);
});

test('native manual basis mode persists while rebinding clears obsolete bases and restores database mode',async()=>{
  const {PL,EQ,world,nodes}=setup({workbench:true});await PL.begin('c1');let editor=nodes.get('#equipment-editor');
  const base=control(editor,'基础生命值');base.value='9999';base.emit('input');assert.equal(PL.state.draft.equipment_inputs.c1.panel_basis_mode,'manual');
  EQ.render();editor=nodes.get('#equipment-editor');assert.equal(Number(control(editor,'基础生命值').value),9999);
  let finish;world.pywebview.api.character_panel_basis=()=>new Promise(resolve=>finish=resolve);
  const binding=control(editor,'数据库角色');binding.value='10000002';binding.emit('change');assert.equal(PL.state.draft.equipment_inputs.c1.panel_basis_mode,'database');
  assert.equal(PL.state.draft.manual_template.characters[0].stats.base_hp,undefined);assert.equal(EQ.validateWorkbench(),false);
  finish({code:0,data:{state:'partial',panel_base:{base_atk:null,base_hp:9000,base_def:900},source_refs:[],missing_inputs:[]}});await new Promise(resolve=>setImmediate(resolve));
  assert.equal(PL.state.draft.manual_template.characters[0].stats.base_hp,9000);assert.equal(PL.state.draft.manual_template.characters[0].stats.atk,1000);
});

test('production character card is a readonly panel summary and routes level/base edits through the shared draft editor',async()=>{
  const {PL,EQ,RT,nodes}=setup({workbench:true});PL.state.plan.equipment_inputs.c1=EQ.blankPanelInput();RT.renderAll();const box=nodes.get('#team-panel');
  assert.equal(control(box,'角色等级'),undefined);assert.equal(control(box,'基础攻击力'),undefined);assert.equal(control(box,'攻击力'),undefined);
  assert.match(box.textContent,/已应用面板摘要/);await press(box,'打开装备与初始面板');
  assert.ok(PL.state.draft);assert.equal(EQ.showing,true);assert.ok(control(nodes.get('#equipment-editor'),'角色等级'));
});
test('native gear panel confirmation survives remount and never implicitly confirms another change',async()=>{
  const {PL,EQ,world,nodes}=setup({workbench:true});await PL.begin('c1');
  PL.state.draft.equipment_inputs.c1=EQ.blankPanelInput();PL.state.draft.equipment_inputs.c1.artifact_sets=[{set_id:15015,count:4}];PL.touch();EQ.render();
  assert.equal(EQ.validateWorkbench(),false);
  const check=control(nodes.get('#equipment-editor'),'已核对当前面板对应所选装备');check.checked=true;check.emit('change');
  assert.equal(EQ.validateWorkbench(),true);EQ.render();assert.equal(EQ.validateWorkbench(),true);
  PL.state.draft.equipment_inputs.c1.artifact_sets=[{set_id:15002,count:4}];EQ.render();assert.equal(EQ.validateWorkbench(),false);
});
test('native pending copy preserves current file and does not mark current changes saved',async()=>{
  const {PL,EQ,world,RT}=setup({workbench:true});await PL.begin('c1');
  PL.state.draft.manual_template.characters[0].stats.atk=2200;PL.touch();const saved=PL.state.savedJson,calls=[];
  world.pywebview.api.preview_equipment_edit=async plan=>({code:0,data:{state:'pending',plan:{...plan,resolution:{state:'pending'}},missing_inputs:[{message:'复核旧装备来源'}]}});
  world.prompt=()=> 'plans/review-copy.plan.json';world.pywebview.api.save_plan=async(...args)=>{calls.push(args);return {code:0,data:{path:args[0],file_sha:'new'}};};
  assert.equal(await EQ.saveAgentCopy(),true);assert.equal(calls[0][2],null);assert.equal(RT.state.path,'plans/p.json');assert.equal(PL.state.savedJson,saved);
  const followup=world.AppDialogKernel.getActive();assert.ok(followup);assert.match(followup.className,/app-dialog/);
  assert.match(world.document.body.textContent,/需要 Agent 完善计算/);assert.equal(PL.draftChanged(),true);
});
test('native late Agent copy save cannot present old issues against a new document',async()=>{
  const {PL,EQ,world}=setup({workbench:true});let finish;
  world.prompt=()=> 'plans/old-copy.plan.json';world.pywebview.api.save_plan=()=>new Promise(resolve=>finish=resolve);
  const saving=EQ.saveAgentCopy(),next=clone(PL.state.plan);next.plan_id='new';next.manual_template.meta.name='新方案';PL.replace(next,'plans/new.plan.json','sha2',true);
  finish({code:0,data:{path:'plans/old-copy.plan.json',file_sha:'copy-sha'}});assert.equal(await saving,true);
  assert.equal(world.document.body.children.some(n=>n.tagName==='dialog'),false);assert.equal(PL.state.plan.plan_id,'new');
});
test('native Agent task retains nested engine execution diagnostics',()=>{
  const {EQ,PL}=setup({workbench:true});const message='动作投影需要明确周期、初始前台及动作开始/结束';
  assert.match(EQ.agentTask(PL.state.plan,{missing_inputs:{issues:[{message,path:'rotation_model.initial_frontline'}]}}),new RegExp(message));
});
function elementalSourceSetup(){const env=mechanicsSetup(),{world,PL}=env;
  GUI_TEST_RUNTIME.loadWebFile(world,path.join(__dirname,'../src/genshin_app/gui/web/attack-metadata.js'),{filename:'attack-metadata.js'});
  PL.state.plan.mechanics_model.elemental_model={schema_version:1,ruleset:'gauge-basic-v1',
    target:{id:'enemy',kind:'ordinary',knowledge:'known',initial_aura:null,status:'assumed',source:'工程目标'},
    profiles:[{id:'fire',element:'pyro',application:'apply',gauge_units:2,icd:{kind:'none'},status:'assumed',source:'手工核实的元素量'}]};
  world.RT.syncRevision();
  let snapshot='current-snapshot',node='current-node';const sent=[];
  const response=plan=>{const rows=(plan.elemental_sources||[]).map(s=>({profile_id:s.profile_id,
    status:s.expected_snapshot_id===snapshot&&s.expected_node_sha256===node?'ready':'pending',
    required_confirmation:{expected_snapshot_id:snapshot,expected_node_sha256:node},
    source:{source_path:'ExcelBinOutput/AttackAttenuationExcelConfigData.json',json_pointer:'/391',node_sha256:node},
    snapshot:{snapshot_id:snapshot},execution:{status:'ready'},rule_group:{status:'database_confirmed',group:s.rule_group||s.attenuation_group},
    projection:{reset_cycle_seconds_raw:2.5,reset_cycle_seconds_rounded:2.5,durability_sequence:[1,0,0]}}));
    return {code:0,data:{state:'pending',plan:clone(plan),elemental_compilations:rows}};};
  world.pywebview.api.compose_equipment_plan=async plan=>{sent.push(clone(plan));return response(plan);};
  const field=(label,value)=>{const node=control(env.dialog(),label);assert.ok(node,`Missing ${label}`);node.value=value;node.emit('change');};
  const open=()=>{env.MC.editElementalSource();field('元素附着模式','sequence');field('附着规则组（区分大小写）','default');field('附着计数标签（ICD Tag）','normal.pyro');field('附着模式与计数关系依据（来源、版本）','明确的攻击来源和共享依据');
    field('逐段结构化证据状态','confirmed');field('证据类型与定位（URL、本地路径或原始 pointer）','fixture://attack/component');field('适用游戏版本或固定 commit','fixture-v1');field('检索/复核日期（YYYY-MM-DD）','2026-10-18');field('具体攻击 component_scope 与共享范围','仅此攻击段，与同技能其他段不共享');};
  return {...env,sent,response,field,open,changeSource:(s,n)=>{snapshot=s;node=n;}};
}
function hitParameterSetup(){const env=mechanicsSetup(),{world,PL}=env;
  PL.state.plan.manual_template.hits[0].scaling_stat='hp';
  PL.state.plan.equipment_inputs.a={character_ref:{db_character_id:10000054,version_line:'release'}};
  world.RT.syncRevision();let snapshot='parameter-snapshot',node='parameter-node';const sent=[];
  const response=plan=>{const rows=(plan.hit_parameter_sources||[]).map(s=>({hit_id:s.hit_id,
    status:s.status!=='pending'&&s.expected_snapshot_id===snapshot&&s.expected_node_sha256===node?'ready':'pending',
    scaling_stat:s.scaling_stat,interpretation_status:s.status,gameplay_interpretation:'declared_not_verified',
    required_confirmation:{expected_snapshot_id:snapshot,expected_node_sha256:node},
    source:{source_path:'ExcelBinOutput/ProudSkillExcelConfigData.json',json_pointer:'/3975',parameter_pointer:'/3975/paramList/0',node_sha256:node},
    scalar_projection:{status:'ready',value:.18748800456523895,issues:[]},level:{effective_level:10},
    parameter_descriptions:{status:'partial',matched:[{text:'技能伤害|{param1:F1P}生命值上限'}],unresolved:[{code:'missing_description_text'}]}}));
    return {code:0,data:{state:'pending',plan:clone(plan),hit_parameter_compilations:rows,
      missing_inputs:[{message:'其他轴内条件仍需核实'}]}};};
  world.pywebview.api.compose_equipment_plan=async plan=>{sent.push(clone(plan));return response(plan);};
  const field=(label,value)=>{const n=control(env.dialog(),label);assert.ok(n,`Missing ${label}`);n.value=String(value);n.emit('change');};
  const open=()=>{env.MC.editHitParameterSource();field('技能槽','burst');field('作为单属性直接伤害倍率的依据','原文明确按生命值上限造成爆发伤害');};
  return {...env,sent,response,field,open,changeSource:(s,n)=>{snapshot=s;node=n;}};
}

function reactionSourceSetup(){const env=elementalSourceSetup(),{PL,world}=env;let snapshot='swirl-snapshot',fp='swirl-profile';const sent=[];
  PL.state.plan.mechanics_model.elemental_model.ruleset='gauge-swirl-v2';
  PL.state.plan.mechanics_model.elemental_model.swirl_damage={icd:{kind:'sequence',reset_seconds:.5,sequence:[1,1,0]},status:'assumed',source:'普通模式范围声明'};
  world.RT.syncRevision();
  const response=plan=>({code:0,data:{state:'pending',plan:clone(plan),missing_inputs:[{message:'完整轴另有待核实项'}],reaction_damage_compilations:(plan.reaction_damage_sources||[]).map(s=>({
    reaction_type:'swirl',status:s.expected_profile_fingerprint===fp?'ready':'pending',
    required_confirmation:{expected_snapshot_id:snapshot,expected_profile_fingerprint:fp},
    profiles:['hydro','cryo'].map(element=>({selection:{element},snapshot:{snapshot_id:snapshot},projection:{damage_sequence:[1,1,0,0],reset_cycle_seconds_rounded:.5},execution:{sequence_status:'ready',ability_execution:'pending'},issues:[]}))}))}});
  world.pywebview.api.compose_equipment_plan=async plan=>{sent.push(clone(plan));return response(plan);};
  const open=()=>{env.MC.editReactionDamageSource();env.field('普通扩散适用范围与来源复核依据','明确普通冰水原目标模式，来源已核对');};
  return {...env,open,response,sent,changeSource:(s,p)=>{snapshot=s;fp=p;}};
}

test('swirl source explicit preview and confirmation preserves scope and unknown native placeholder',async()=>{
  const env=reactionSourceSetup(),before=env.PL.currentInputJson();env.open();await press(env.dialog(),'确认所示扩散来源');assert.equal(env.sent.length,0);
  await press(env.dialog(),'读取并预览扩散来源');assert.match(env.dialog().textContent,/水扩散/);assert.match(env.dialog().textContent,/冰扩散/);assert.match(env.dialog().textContent,/1、1、0、0/);
  await press(env.dialog(),'确认所示扩散来源');assert.equal(env.PL.currentInputJson(),before);
  await press(env.dialog(),'应用扩散来源绑定');const plan=env.PL.state.plan;
  assert.equal(plan.reaction_damage_sources[0].expected_profile_fingerprint,'swirl-profile');
  assert.equal(plan.mechanics_model.elemental_model.swirl_damage.icd.kind,'unknown');assert.equal(plan.mechanics_model.elemental_model.swirl_damage.status,'assumed');
});
test('swirl source update requires another visible confirmation without updating native state',async()=>{
  const env=reactionSourceSetup();env.open();await press(env.dialog(),'读取并预览扩散来源');const before=env.PL.currentInputJson();
  env.changeSource('new-snapshot','new-profile');await press(env.dialog(),'确认所示扩散来源');assert.equal(env.PL.currentInputJson(),before);assert.match(env.dialog().textContent,/扩散来源已变化/);
  await press(env.dialog(),'确认所示扩散来源');await press(env.dialog(),'应用扩散来源绑定');assert.equal(env.PL.state.plan.reaction_damage_sources[0].expected_profile_fingerprint,'new-profile');
});
for(const mutate of ['input','database','path','plan','cancel'])test(`swirl source stale ${mutate} response cannot apply a binding`,async()=>{
  const env=reactionSourceSetup();env.open();let finish,submitted;env.world.pywebview.api.compose_equipment_plan=plan=>{submitted=clone(plan);return new Promise(resolve=>finish=resolve);};
  const pending=press(env.dialog(),'应用扩散来源绑定');
  if(mutate==='input')env.PL.state.plan.manual_template.meta.name='changed';
  if(mutate==='database')env.PL.state.sourceEpoch++;
  if(mutate==='path')env.RT.state.path='different.json';
  if(mutate==='plan')env.PL.replace(clone(env.PL.state.plan),'replacement.json','replacement-sha',true);
  if(mutate==='cancel')await press(env.dialog(),'放弃并关闭');
  finish(env.response(submitted));await pending;assert.equal(env.PL.state.plan.reaction_damage_sources,undefined);
});
test('swirl source removal leaves unknown rather than restoring derived cached parameters',async()=>{
  const env=reactionSourceSetup();env.open();await press(env.dialog(),'应用扩散来源绑定');
  env.MC.editReactionDamageSource();await press(env.dialog(),'移除扩散来源绑定');assert.equal(env.PL.state.plan.reaction_damage_sources.length,0);
  assert.equal(env.PL.state.plan.mechanics_model.elemental_model.swirl_damage.icd.kind,'unknown');
});
test('swirl source refuses unsupported sequence confirmation and preserves draft evidence status',async()=>{
  const env=reactionSourceSetup();env.open();env.world.pywebview.api.compose_equipment_plan=async plan=>{const r=env.response(plan);r.data.reaction_damage_compilations[0].profiles[1].execution.sequence_status='pending';return r;};
  await press(env.dialog(),'读取并预览扩散来源');await press(env.dialog(),'确认所示扩散来源');await press(env.dialog(),'应用扩散来源绑定');
  assert.equal(env.PL.state.plan.reaction_damage_sources[0].expected_snapshot_id,null);assert.equal(env.PL.state.plan.mechanics_model.elemental_model.swirl_damage.status,'assumed');
});

test('hit parameter editor previews complete Chinese text and only applies an explicit fresh source',async()=>{
  const {PL,open,dialog,sent,field}=hitParameterSetup();const before=PL.currentInputJson();
  PL.state.report={hit_parameter_compilations:[{hit_id:'h0',required_confirmation:{expected_snapshot_id:'old',expected_node_sha256:'old'}}]};
  open();await press(dialog(),'确认所示数值来源');assert.equal(sent.length,0);assert.equal(PL.currentInputJson(),before);
  field('倍率用途核实状态','confirmed');await press(dialog(),'读取并预览当前技能参数');
  assert.equal(sent.length,1);assert.equal(sent[0].manual_template.hits[0].multiplier,undefined);
  assert.equal(sent[0].manual_template.hits[0].scaling_stat,'hp');
  assert.match(dialog().textContent,/技能伤害\|\{param1:F1P\}生命值上限/);assert.match(dialog().textContent,/1 条描述缺文/);
  assert.match(dialog().textContent,/引擎尚未验证游戏语义/);assert.match(dialog().textContent,/其他轴内条件仍需核实/);
  assert.equal(all(dialog()).filter(n=>n.tagName==='input'||n.tagName==='textarea').some(n=>String(n.value).includes('parameter-node')),false);
  await press(dialog(),'确认所示数值来源');assert.equal(PL.currentInputJson(),before);
  await press(dialog(),'应用倍率来源绑定');assert.equal(sent.length,3);
  const source=PL.state.plan.hit_parameter_sources[0];assert.equal(source.expected_snapshot_id,'parameter-snapshot');
  assert.equal(source.expected_node_sha256,'parameter-node');assert.equal(source.status,'confirmed');
  assert.equal(source.purpose,'single_stat_damage_multiplier');assert.equal(source.scaling_stat,'hp');
  assert.equal(PL.state.plan.manual_template.hits[0].multiplier,undefined);
  assert.equal(PL.state.plan.manual_template.hits[0].scaling_stat,'hp');assert.equal(PL.dirty(),true);
  assert.equal(PL.state.plan.mechanics_model.hit_patterns.length,JSON.parse(before).mechanics_model.hit_patterns.length);
});

test('parameter source confirmation never silently upgrades pending gameplay interpretation',async()=>{
  const {PL,open,dialog,sent}=hitParameterSetup();open();await press(dialog(),'读取并预览当前技能参数');
  await press(dialog(),'确认所示数值来源');await press(dialog(),'应用倍率来源绑定');
  assert.equal(PL.state.plan.hit_parameter_sources[0].status,'pending');
  assert.equal(PL.state.plan.hit_parameter_sources[0].expected_node_sha256,'parameter-node');
  assert.equal(PL.state.plan.resolution.state,'pending');assert.equal(sent[2].hit_parameter_sources[0].status,'pending');
});

test('unconfirmed parameter source applies only pending and leaves existing hit times and pattern offsets unchanged',async()=>{
  const {PL,open,dialog}=hitParameterSetup(),patterns=clone(PL.state.plan.mechanics_model.hit_patterns),hits=clone(PL.state.plan.manual_template.hits);
  open();await press(dialog(),'应用倍率来源绑定');const row=PL.state.plan.hit_parameter_sources[0];
  assert.equal(row.expected_snapshot_id,null);assert.equal(row.expected_node_sha256,null);
  assert.equal(PL.state.plan.manual_template.hits.length,hits.length);assert.equal(PL.state.plan.manual_template.hits[0].t,hits[0].t);
  assert.deepEqual(clone(PL.state.plan.mechanics_model.hit_patterns),patterns);
});

test('parameter source requires another explicit confirmation when database changes during confirmation',async()=>{
  const {PL,open,dialog,sent,changeSource}=hitParameterSetup();open();await press(dialog(),'读取并预览当前技能参数');
  changeSource('new-parameter-snapshot','new-parameter-node');await press(dialog(),'确认所示数值来源');
  assert.equal(PL.state.plan.hit_parameter_sources,undefined);assert.match(dialog().textContent,/来源已变化/);
  await press(dialog(),'确认所示数值来源');await press(dialog(),'应用倍率来源绑定');
  assert.equal(sent[2].hit_parameter_sources[0].expected_node_sha256,'new-parameter-node');
  assert.equal(PL.state.plan.hit_parameter_sources[0].expected_node_sha256,'new-parameter-node');
});

test('parameter edit invalidates confirmation and delayed preview without overwriting local selection',async()=>{
  const {PL,world,open,dialog,field,response,sent}=hitParameterSetup();open();await press(dialog(),'读取并预览当前技能参数');
  await press(dialog(),'确认所示数值来源');field('参数序号（从0开始）','1');
  await press(dialog(),'确认所示数值来源');assert.equal(sent.length,2);
  let finish,submitted;world.pywebview.api.compose_equipment_plan=plan=>{submitted=clone(plan);return new Promise(r=>finish=r);};
  const pending=press(dialog(),'读取并预览当前技能参数');field('参数序号（从0开始）','2');finish(response(submitted));await pending;
  assert.doesNotMatch(dialog().textContent,/数据库原始数值：/);assert.equal(PL.state.plan.hit_parameter_sources,undefined);
});

for(const mutation of ['input','epoch','identity','equipment-draft','path','sha'])test(`parameter source apply rejects concurrent ${mutation}`,async()=>{
  const {world,PL,RT,open,dialog,response}=hitParameterSetup();open();let finish,submitted;
  world.pywebview.api.compose_equipment_plan=plan=>{submitted=clone(plan);return new Promise(r=>finish=r);};
  const pending=press(dialog(),'应用倍率来源绑定');
  if(mutation==='input')PL.state.plan.manual_template.meta.name='后续修改';
  if(mutation==='epoch')PL.state.sourceEpoch++;
  if(mutation==='identity')PL.state.plan=clone(PL.state.plan);
  if(mutation==='equipment-draft')PL.state.draft=clone(PL.state.plan);
  if(mutation==='path')RT.state.path='other.json';
  if(mutation==='sha')PL.state.fileSha='external';
  finish(response(submitted));await pending;assert.equal(PL.state.plan.hit_parameter_sources,undefined);
  assert.equal(PL.state.plan.manual_template.hits[0].multiplier,1);
});

test('closing parameter source dialog discards an inflight apply',async()=>{
  const {world,PL,open,dialog,response}=hitParameterSetup();open();let finish,submitted;
  world.pywebview.api.compose_equipment_plan=plan=>{submitted=clone(plan);return new Promise(r=>finish=r);};
  const pending=press(dialog(),'应用倍率来源绑定');press(dialog(),'← 返回机制列表');finish(response(submitted));await pending;
  assert.equal(PL.state.plan.hit_parameter_sources,undefined);assert.equal(PL.state.plan.manual_template.hits[0].multiplier,1);
});

test('source removal requires an explicit manual multiplier and never restores a cached value',async()=>{
  const {PL,MC,open,dialog,field,sent}=hitParameterSetup();open();await press(dialog(),'应用倍率来源绑定');
  MC.editHitParameterSource(0);await press(dialog(),'解除来源并采用手填倍率');assert.equal(sent.length,1);
  assert.equal(control(dialog(),'解除绑定后采用的手填倍率（不自动恢复）').value,'');
  field('解除绑定后采用的手填倍率（不自动恢复）','2.5');await press(dialog(),'解除来源并采用手填倍率');
  assert.equal(PL.state.plan.hit_parameter_sources.length,0);assert.equal(PL.state.plan.manual_template.hits[0].multiplier,2.5);
  assert.equal(PL.state.plan.manual_template.hits[0].scaling_stat,'hp');
});

test('undefined scaling stat requires a visible explicit selection and does not default to atk',async()=>{
  const {PL,RT,MC,dialog,field,sent}=hitParameterSetup();delete PL.state.plan.manual_template.hits[0].scaling_stat;RT.syncRevision();
  MC.editHitParameterSource();assert.equal(control(dialog(),'明确选择本命中的倍率属性').value,'');
  field('明确选择本命中的倍率属性','hp');field('作为单属性直接伤害倍率的依据','显式HP依据');
  await press(dialog(),'应用倍率来源绑定');assert.equal(sent[0].manual_template.hits[0].scaling_stat,'hp');
  assert.equal(PL.state.plan.manual_template.hits[0].scaling_stat,'hp');
});

test('explicit skill id selection and optional depot survive candidate assembly',async()=>{
  const {open,dialog,field,sent}=hitParameterSetup();open();field('技能定位方式','skill_id');
  field('数据库技能编号','10545');field('技能组编号（多技能组角色须填写）','5401');
  await press(dialog(),'读取并预览当前技能参数');const selection=sent[0].hit_parameter_sources[0].selection;
  assert.equal(selection.skill_id,10545);assert.equal(selection.skill_depot_id,5401);assert.equal(selection.slot,undefined);
});

test('unsupported scalar and missing descriptions stay explicit and cannot acquire confirmation',async()=>{
  const {world,PL,open,dialog,response}=hitParameterSetup();open();world.pywebview.api.compose_equipment_plan=async plan=>{
    const r=response(plan),row=r.data.hit_parameter_compilations[0];row.scalar_projection={status:'pending',value:.8,issues:[{message:'13级命座证据尚未验证'}]};
    row.parameter_descriptions={status:'pending',matched:[],unresolved:[]};return r;};
  await press(dialog(),'读取并预览当前技能参数');assert.match(dialog().textContent,/没有找到引用此参数的中文原文/);
  assert.match(dialog().textContent,/13级命座证据尚未验证/);await press(dialog(),'确认所示数值来源');
  await press(dialog(),'应用倍率来源绑定');assert.equal(PL.state.plan.hit_parameter_sources[0].expected_node_sha256,null);
});

test('bound hit panel blocks numeric replacement and identity changes while leaving timeline inputs editable',async()=>{
  const {world,PL,RT,UI,open,dialog}=hitParameterSetup();open();await press(dialog(),'应用倍率来源绑定');
  const box=UI.el('div');world.renderHitPanel(box,0);const hit=PL.state.plan.manual_template.hits[0];
  for(const [name,value] of [['角色','other'],['伤害体系','lunar'],['倍率依据','atk'],['倍率','0']]){
    const node=control(box,name);assert.equal(node.disabled,true);node.value=value;node.emit('change');}
  assert.equal(hit.character,'a');assert.equal(hit.damage_system,'classic');assert.equal(hit.scaling_stat,'hp');assert.equal(hit.multiplier,undefined);
  assert.equal(control(box,'倍率').placeholder,'由数据库来源编译');assert.match(box.textContent,/数据库倍率来源/);
  const time=control(box,'时刻（秒）');time.value='3';time.emit('change');assert.equal(hit.t,3);
  await press(box,'删除事件');assert.equal(RT.tpl().hits.length,1);
});

test('missing unbound multiplier renders blank and does not create a default number',()=>{
  const {world,PL,UI}=hitParameterSetup();delete PL.state.plan.manual_template.hits[0].multiplier;
  const box=UI.el('div');world.renderHitPanel(box,0);assert.equal(control(box,'倍率').value,'');
  assert.equal(control(box,'倍率').placeholder,'倍率待填写');assert.equal(control(box,'倍率').disabled,false);
  assert.equal(PL.state.plan.manual_template.hits[0].multiplier,undefined);
});

test('stale ordinary multiplier control cannot write after a source was attached',async()=>{
  const {world,PL,UI,open,dialog}=hitParameterSetup();const box=UI.el('div');world.renderHitPanel(box,0);const old=control(box,'倍率');
  open();await press(dialog(),'应用倍率来源绑定');old.value='1';old.emit('change');
  assert.equal(PL.state.plan.manual_template.hits[0].multiplier,undefined);
});

test('hit parameter card exposes current pending diagnostics and hides them after subsequent edits',async()=>{
  const {PL,MC,UI,open,dialog}=hitParameterSetup();open();await press(dialog(),'应用倍率来源绑定');
  const box=UI.el('div');MC.renderHitParameterSources(box);assert.match(box.textContent,/其他轴内条件仍需核实/);
  PL.state.plan.manual_template.meta.name='后续编辑';const newer=UI.el('div');MC.renderHitParameterSources(newer);
  assert.doesNotMatch(newer.textContent,/其他轴内条件仍需核实/);
});

test('elemental source dialog explicitly confirms fresh database facts without hash text inputs',async()=>{
  const {PL,MC,open,dialog,sent}=elementalSourceSetup();const baseline=PL.currentInputJson();
  PL.state.report={elemental_compilations:[{profile_id:'fire',status:'ready',required_confirmation:{expected_snapshot_id:'stale',expected_node_sha256:'stale'}}]};
  open();assert.equal(PL.currentInputJson(),baseline);
  await press(dialog(),'确认所示来源用于此档案');assert.equal(sent.length,0);
  await press(dialog(),'读取并预览当前数据库规则');assert.equal(sent.length,1);
  assert.equal(sent[0].elemental_sources[0].expected_snapshot_id,null);
  assert.equal(sent[0].mechanics_model.elemental_model.profiles[0].icd.kind,'unknown');
  assert.match(dialog().textContent,/附着重置：2.5 秒/);assert.match(dialog().textContent,/1、0、0/);
  assert.equal(all(dialog()).filter(n=>n.tagName==='input'||n.tagName==='textarea').some(n=>String(n.value).includes('current-node')),false);
  await press(dialog(),'确认所示来源用于此档案');assert.equal(sent.length,2);assert.equal(PL.currentInputJson(),baseline);
  await press(dialog(),'应用来源绑定');assert.equal(sent.length,3);
  const binding=PL.state.plan.elemental_sources[0];assert.equal(binding.expected_snapshot_id,'current-snapshot');assert.equal(binding.expected_node_sha256,'current-node');
  assert.equal(PL.state.plan.mechanics_model.elemental_model.profiles[0].gauge_units,2);
  assert.equal(PL.state.plan.mechanics_model.elemental_model.profiles[0].icd.kind,'unknown');
  assert.equal(PL.state.fileSha,'original-sha');assert.equal(PL.dirty(),true);assert.equal(MC.label('reset_seconds'),'附着重置秒数');
});

test('unconfirmed elemental source can be applied as pending without silently accepting receipt hashes',async()=>{
  const {PL,open,dialog,sent}=elementalSourceSetup();open();await press(dialog(),'应用来源绑定');
  assert.equal(sent.length,1);assert.equal(PL.state.plan.elemental_sources[0].expected_snapshot_id,null);
  assert.equal(PL.state.plan.elemental_sources[0].expected_node_sha256,null);assert.equal(PL.state.plan.resolution.state,'pending');
});

test('database change during source confirmation refreshes evidence but requires a new explicit acceptance',async()=>{
  const {PL,open,dialog,sent,changeSource}=elementalSourceSetup();open();const baseline=PL.currentInputJson();
  await press(dialog(),'读取并预览当前数据库规则');changeSource('new-snapshot','new-node');
  await press(dialog(),'确认所示来源用于此档案');assert.equal(PL.currentInputJson(),baseline);
  assert.match(dialog().textContent,/数据库来源已变化/);assert.equal(sent[1].elemental_sources[0].expected_snapshot_id,'current-snapshot');
  await press(dialog(),'确认所示来源用于此档案');assert.equal(sent[2].elemental_sources[0].expected_snapshot_id,'new-snapshot');
  await press(dialog(),'应用来源绑定');assert.equal(PL.state.plan.elemental_sources[0].expected_snapshot_id,'new-snapshot');
});

test('database change after confirmation cannot apply an obsolete accepted source',async()=>{
  const {PL,open,dialog,changeSource}=elementalSourceSetup();open();const baseline=PL.currentInputJson();
  await press(dialog(),'读取并预览当前数据库规则');await press(dialog(),'确认所示来源用于此档案');
  changeSource('changed-at-apply','changed-node');await press(dialog(),'应用来源绑定');
  assert.equal(PL.currentInputJson(),baseline);assert.match(dialog().textContent,/尚未确认或应用/);
  await press(dialog(),'应用来源绑定');assert.equal(PL.state.plan.elemental_sources[0].expected_snapshot_id,null);
});

test('editing the source draft invalidates its preview and confirmation',async()=>{
  const {PL,open,dialog,sent,field}=elementalSourceSetup();open();await press(dialog(),'读取并预览当前数据库规则');
  await press(dialog(),'确认所示来源用于此档案');field('附着计数标签（ICD Tag）','different-group');
  await press(dialog(),'确认所示来源用于此档案');assert.equal(sent.length,2);
  await press(dialog(),'应用来源绑定');assert.equal(PL.state.plan.elemental_sources[0].expected_snapshot_id,null);
  assert.equal(PL.state.plan.elemental_sources[0].counter_tag,'different-group');assert.equal(Object.hasOwn(PL.state.plan.elemental_sources[0],'shared_group'),false);
});

test('late source preview never replaces a newer local dialog edit',async()=>{
  const {world,open,dialog,field,response,sent}=elementalSourceSetup();open();let finish,submitted;
  world.pywebview.api.compose_equipment_plan=plan=>{submitted=clone(plan);return new Promise(resolve=>{finish=resolve;});};
  const pending=press(dialog(),'读取并预览当前数据库规则');field('附着规则组（区分大小写）','Diluc');finish(response(submitted));await pending;
  assert.doesNotMatch(dialog().textContent,/附着重置：2.5 秒/);assert.match(dialog().textContent,/请读取当前数据库/);
  await press(dialog(),'确认所示来源用于此档案');assert.equal(sent.length,0);
});

for(const mutation of ['input','epoch','identity','equipment-draft','path','sha'])test(`source apply rejects concurrent ${mutation} changes`,async()=>{
  const {world,PL,RT,open,dialog,response}=elementalSourceSetup();open();let finish,submitted;
  world.pywebview.api.compose_equipment_plan=plan=>{submitted=clone(plan);return new Promise(resolve=>{finish=resolve;});};
  const pending=press(dialog(),'应用来源绑定');
  if(mutation==='input')PL.state.plan.manual_template.meta.name='后续编辑';
  if(mutation==='epoch')PL.state.sourceEpoch++;
  if(mutation==='identity')PL.state.plan=clone(PL.state.plan);
  if(mutation==='equipment-draft')PL.state.draft=clone(PL.state.plan);
  if(mutation==='path')RT.state.path='different.json';
  if(mutation==='sha')PL.state.fileSha='new-sha';
  finish(response(submitted));await pending;assert.equal(PL.state.plan.elemental_sources,undefined);
});

test('closing source dialog discards an in-flight apply and never writes to the plan',async()=>{
  const {world,PL,open,dialog,response}=elementalSourceSetup();open();const baseline=PL.currentInputJson();let finish,submitted;
  world.pywebview.api.compose_equipment_plan=plan=>{submitted=clone(plan);return new Promise(resolve=>{finish=resolve;});};
  const pending=press(dialog(),'应用来源绑定');press(dialog(),'← 返回机制列表');finish(response(submitted));await pending;
  assert.equal(PL.currentInputJson(),baseline);
});

test('unsupported source preview has no implicit confirmation and backend failure does not apply',async()=>{
  const {world,PL,open,dialog,response}=elementalSourceSetup();open();const baseline=PL.currentInputJson();let calls=0;
  world.pywebview.api.compose_equipment_plan=async plan=>{calls++;const result=response(plan);result.data.elemental_compilations[0].execution.status='pending';return result;};
  await press(dialog(),'读取并预览当前数据库规则');await press(dialog(),'确认所示来源用于此档案');assert.equal(calls,1);
  assert.match(dialog().textContent,/附着模式待核实，确定性计算已阻止/);
  world.pywebview.api.compose_equipment_plan=async()=>({code:1,message:'重复档案绑定'});await press(dialog(),'应用来源绑定');
  assert.equal(PL.currentInputJson(),baseline);assert.match(dialog().textContent,/重复档案绑定/);
});

test('removing database binding leaves the profile unresolved rather than restoring a stale manual rule',async()=>{
  const {PL,MC,open,dialog}=elementalSourceSetup();open();await press(dialog(),'应用来源绑定');
  MC.editElementalSource(0);await press(dialog(),'移除此来源绑定');
  assert.equal(PL.state.plan.elemental_sources.length,0);assert.equal(PL.state.plan.mechanics_model.elemental_model.profiles[0].icd.kind,'unknown');
});

test('pending source assembly displays diagnostics after apply and invalidates them on later input changes',async()=>{
  const {world,PL,MC,RT,open,dialog,response}=elementalSourceSetup();open();
  await press(dialog(),'读取并预览当前数据库规则');await press(dialog(),'确认所示来源用于此档案');
  const issue={code:'icd_group_conflict',message:'同一共享组的重置时间或序列不一致。'};
  world.pywebview.api.compose_equipment_plan=async plan=>{const result=response(plan),record=result.data.elemental_compilations[0];
    record.status='pending';record.assembly_issues=[issue];result.data.missing_inputs=[issue];return result;};
  await press(dialog(),'应用来源绑定');assert.equal(PL.state.plan.resolution.state,'pending');
  const box=new Element();MC.renderElementalSources(box);assert.match(box.textContent,/同一共享组/);
  assert.match(box.textContent,/方案待完善/);assert.match(box.textContent,/规则组合后仍有冲突/);
  assert.doesNotMatch(box.textContent,/规则参数可用于计算/);
  PL.state.plan.manual_template.meta.name='另一版';RT.syncRevision();const changed=new Element();MC.renderElementalSources(changed);
  assert.doesNotMatch(changed.textContent,/同一共享组的重置时间/);
});

test('elemental source editing is blocked while an equipment draft is active',()=>{
  const {world,PL,MC}=elementalSourceSetup();const count=world.document.body.children.length;PL.state.draft=clone(PL.state.plan);
  MC.editElementalSource();assert.equal(world.document.body.children.length,count);
});

test('direct elemental source writes canonical groups and complete verification only',async()=>{
  const {PL,open,dialog}=elementalSourceSetup();open();await press(dialog(),'应用来源绑定');const row=PL.state.plan.elemental_sources[0];
  assert.equal(row.rule_group,'default');assert.equal(row.counter_tag,'normal.pyro');assert.equal(row.verification.status,'confirmed');
  assert.deepEqual(clone(row.verification.confirmed_fields),['application_mode','counter_tag','shared_scope']);
  assert.equal(Object.hasOwn(row,'attenuation_group'),false);assert.equal(Object.hasOwn(row,'shared_group'),false);
});

test('direct confirmed application mode without structured evidence is blocked locally',async()=>{
  const env=elementalSourceSetup();env.MC.editElementalSource();env.field('元素附着模式','no_icd');env.field('附着模式与计数关系依据（来源、版本）','只有自由文本');
  await press(env.dialog(),'应用来源绑定');assert.equal(env.PL.state.plan.elemental_sources,undefined);assert.match(env.dialog().textContent,/需要逐攻击段结构化证据/);
});

test('editing legacy direct groups migrates them to canonical fields without coexistence',async()=>{
  const env=elementalSourceSetup(),p=env.PL.state.plan;p.elemental_sources=[{profile_id:'fire',version_line:'release',application_mode:'sequence',attenuation_group:'default',shared_group:'legacy.counter',source:'旧来源',expected_snapshot_id:null,expected_node_sha256:null}];
  env.MC.editElementalSource(0);assert.equal(control(env.dialog(),'附着规则组（区分大小写）').value,'default');assert.equal(control(env.dialog(),'附着计数标签（ICD Tag）').value,'legacy.counter');
  for(const [label,value] of [['逐段结构化证据状态','confirmed'],['证据类型与定位（URL、本地路径或原始 pointer）','fixture://legacy'],['适用游戏版本或固定 commit','fixture-v1'],['检索/复核日期（YYYY-MM-DD）','2026-10-18'],['具体攻击 component_scope 与共享范围','仅此段']])env.field(label,value);
  await press(env.dialog(),'应用来源绑定');for(let i=0;i<20&&!env.sent.length;i++)await new Promise(resolve=>setTimeout(resolve,5));
  const submitted=env.sent[0].elemental_sources[0];assert.equal(submitted.rule_group,'default');assert.equal(submitted.counter_tag,'legacy.counter');assert.equal(Object.hasOwn(submitted,'attenuation_group'),false);assert.equal(Object.hasOwn(submitted,'shared_group'),false);
});

test('direct elemental work order shows every concrete use and explicit unresolved/no-reference states',()=>{
  const env=elementalSourceSetup(),box=new Element(),base={status:'pending',query:{},verification_task:{attack_identity:{character_id:10000054,profile_id:'fire',version_line:'release'},next_action:'定位所有引用',known_fields:{application_mode:'pending'},missing_fields:['counter_tag'],conflicts:[],local_checks:['profile exists'],targeted_research_questions:['每个使用点的附着模式是什么？'],keywords:['fire','component'],database_patch_targets:['elemental_sources'],research_report_template:{actual_queries:[],sources_checked:[],adopted_or_rejected:[],remaining_unknowns:[],database_patch_plan:[]}}};
  base.verification_task.uses=[{actor:'a',ability_id:'q',action_id:'q1',hit_id:'h1',application_event_id:'apply-1',component:'main',variant:'default',constellation:0,target:'enemy'},{actor:'a',ability_id:'q',action_id:'q2',pattern_id:'pattern-2',component_id:'component-2',variant:'hold',constellation:1,target_id:'enemy-2'}];base.verification_task.unresolved_identity_fields=['second.hit_id'];env.MC.renderElementalCompilation(box,base);
  assert.match(box.textContent,/具体使用点（2）/);assert.match(box.textContent,/action_id=q1/);assert.match(box.textContent,/pattern_id=pattern-2/);assert.match(box.textContent,/未解析身份字段：second.hit_id/);assert.match(box.textContent,/actual_queries/);
  const empty=new Element();base.verification_task.uses=[];env.MC.renderElementalCompilation(empty,base);assert.match(empty.textContent,/后端未找到引用/);
});

test('current report renders a saved direct elemental research audit through the shared work-order renderer',()=>{
  const env=elementalSourceSetup(),box=new Element(),task={attack_identity:{character_id:10000054,actor:'a',profile_id:'fire',component_profile_id:'fire-main',version_line:'release'},next_action:'完成直接附着来源复核',known_fields:{application_mode:'sequence'},missing_fields:['shared_scope'],conflicts:[],local_checks:['profile exists'],targeted_research_questions:['该具体攻击段与哪些段共享计数？'],keywords:['fire','attenuationTag'],database_patch_targets:['elemental_sources.fire'],uses:[{actor:'a',action_id:'q1',hit_id:'h1',component:'main'}],research_report:{report_version:1,execution_authority:'none',actual_queries:['direct fire component ICD'],sources_checked:[{locator:'fixture://direct-fire',version:'v1',accessed_at:'2026-10-18',supported_fields:['application_mode','counter_tag'],decision:'adopted',reason:'逐段原始配置明确'}],adopted_findings:['counter_tag=normal.pyro'],rejected_findings:['不能按元素爆发类别推断'],remaining_unknowns:['与追加攻击的共享范围'],database_patch_plan:[{target:'elemental_sources.fire',fields:['counter_tag','shared_scope'],evidence_locators:['fixture://direct-fire'],status:'proposed',note:'回填具体分量'}]},research_report_template:{actual_queries:[],sources_checked:[],remaining_unknowns:[],database_patch_plan:[]}};
  env.MC.renderElementalVerificationTasks(box,{elemental_compilations:[{profile_id:'fire',verification_task:task}],elemental_counter_verification_tasks:[clone(task)]});
  const text=box.textContent;assert.match(text,/元素附着身份与共享计数核实（1）/);for(const expected of ['完成直接附着来源复核','已保存的 Agent 查证记录（无执行权）','报告版本：1；执行权：none','direct fire component ICD','fixture:\/\/direct-fire','访问 2026-10-18','支持字段 application_mode；counter_tag','adopted（已采纳）','逐段原始配置明确','采纳结论：counter_tag=normal.pyro','拒绝结论：不能按元素爆发类别推断','仍未确定：与追加攻击的共享范围','elemental_sources.fire','字段 counter_tag；shared_scope','proposed（拟议）','回填具体分量','不能代替 verification'])assert.match(text,new RegExp(expected));
});

test('mechanics source list retains a pending elemental work order without assembly conflicts',()=>{
  const env=elementalSourceSetup(),box=new Element(),record={profile_id:'fire',status:'pending',application_mode:'pending',assembly_issues:[],verification_task:{attack_identity:{character_id:10000054,profile_id:'fire',version_line:'release'},next_action:'核实该档案具体使用点',known_fields:{application_mode:'pending'},missing_fields:['counter_tag'],conflicts:[],local_checks:['profile exists'],targeted_research_questions:['该分量是否尝试附着？'],keywords:['fire','attenuationTag'],database_patch_targets:['elemental_sources.fire'],research_report_template:{actual_queries:[],sources_checked:[],remaining_unknowns:[],database_patch_plan:[]}}};
  env.MC.elementalDiagnostics={plan:env.PL.state.plan,input:env.PL.currentInputJson(),epoch:env.PL.state.sourceEpoch,path:env.RT.state.path,sha:env.PL.state.fileSha,state:'pending',issues:[],record};env.MC.renderElementalSources(box);
  assert.match(box.textContent,/核实该档案具体使用点/);assert.match(box.textContent,/character_id=10000054/);assert.match(box.textContent,/缺失：counter_tag/);assert.match(box.textContent,/actual_queries/);
});

test('persistent elemental diagnostics show both sides of a counter-tag rule-group conflict',()=>{
  const env=elementalSourceSetup(),box=new Element(),task={attack_identity:{actor:'a',profile_id:'fire',component_profile_id:'fire-main',version_line:'release'},next_action:'审阅同角色计数标签冲突',known_fields:{counter_tag:'shared.attack'},missing_fields:[],conflicts:[{counter_tag:'shared.attack',left:{profile_id:'fire',path:'elemental_sources.0',rule_group:'default'},right:{profile_id:'ice',path:'elemental_sources.1',rule_group:'special'}}],local_checks:['actor-local comparison'],targeted_research_questions:['两段是否真的共享计数？'],keywords:['shared.attack'],database_patch_targets:['elemental_sources.0','elemental_sources.1'],research_report_template:{actual_queries:[],sources_checked:[],remaining_unknowns:[],database_patch_plan:[]}},record={profile_id:'fire',status:'pending',application_mode:'sequence',assembly_issues:[],verification_tasks:[task]};
  env.MC.elementalDiagnostics={plan:env.PL.state.plan,input:env.PL.currentInputJson(),epoch:env.PL.state.sourceEpoch,path:env.RT.state.path,sha:env.PL.state.fileSha,state:'pending',issues:[],record};env.MC.renderElementalSources(box);
  assert.match(box.textContent,/profile_id.*fire/);assert.match(box.textContent,/profile_id.*ice/);assert.match(box.textContent,/elemental_sources\.0/);assert.match(box.textContent,/elemental_sources\.1/);assert.match(box.textContent,/default/);assert.match(box.textContent,/special/);
});

test('top-level elemental counter conflicts remain visible for manual profiles and are deduplicated',()=>{
  const env=elementalSourceSetup(),box=new Element(),task={attack_identity:{actor:'a',profile_id:'manual-fire',component_profile_id:'manual-fire-main',target_id:'enemy'},next_action:'逐攻击分量核实真实共享关系',known_fields:{counter_tag:'shared.manual'},missing_fields:[],conflicts:[{counter_tag:'shared.manual',left:{profile_id:'manual-fire',rule_group:'default'},right:{profile_id:'manual-ice',rule_group:'special'}}],local_checks:[{kind:'actor-local comparison'}],targeted_research_questions:['这两个具体命中分量是否共享计数？'],keywords:['shared.manual'],database_patch_targets:['mechanics_model.elemental_model.profiles'],research_report_template:{actual_queries:[],sources_checked:[],remaining_unknowns:[],database_patch_plan:[]}};
  env.MC.renderElementalVerificationTasks(box,{elemental_counter_verification_tasks:[task,clone(task)]});
  assert.match(box.textContent,/元素附着身份与共享计数核实（1）/);
  assert.match(box.textContent,/不能用普攻\/战技\/爆发类别/);assert.match(box.textContent,/manual-fire/);assert.match(box.textContent,/manual-ice/);
});

test('sequence fields are editable with Chinese labels through the normal mechanism form',async()=>{
  const {PL,MC,RT,dialog}=elementalSourceSetup();PL.state.plan.mechanics_model.elemental_model.profiles[0].icd={kind:'sequence',group:'shared',reset_seconds:2.5,sequence:[0,1]};RT.syncRevision();
  MC.edit('elemental_model');const field=control(dialog(),'附着重置秒数');assert.ok(field);field.value='5';field.emit('change');
  const first=control(dialog(),'有限附着序列 1');assert.ok(first);assert.equal(first.value,'0');
  await press(dialog(),'应用修改');assert.equal(PL.state.plan.mechanics_model.elemental_model.profiles[0].icd.reset_seconds,5);
  assert.deepEqual(clone(PL.state.plan.mechanics_model.elemental_model.profiles[0].icd.sequence),[0,1]);
});

test('automatic source requires explicit scene input and never populates editable rules',async()=>{
  const {PL,EQ,EF,world}=mechanicsSetup();await PL.begin('a');
  const plan=EQ.plan();plan.effect_bindings=[{binding_id:'auto',provider_character_id:'a',owner:{kind:'artifact_set',set_id:15007,required_count:4,affix_id:2150071},
    source_refs:[{kind:'database'}],source_fingerprint:null,coverage:{state:'unreviewed',note:null},parts:[]}];
  plan.equipment_inputs.a=EQ.blankPanelInput();
  PL.state.caps.upstream_contract={default_scenario:{profile_id:'test',profile_version:1,static_policy:'included',accepted_assumptions_fingerprint:null}};
  EQ.render=()=>{};const card=new Element();EF.render(card);
  const b=plan.effect_bindings[0];EF.bindingMenuItems(b).find(item=>item.label==='尝试上游自动装配').run();
  assert.equal(b.upstream_recipe.scenario.single_player_fixed,false);assert.equal(b.upstream_recipe.scenario.max_party_distance,null);
  assert.equal(b.parts.length,0);assert.equal(b.coverage.state,'unreviewed');
  const detail=new Element();EF.renderUpstream(detail,b);
  assert.match(detail.textContent,/尚无当前输入的有效预览/);
  assert.equal(all(detail).some(n=>n.textContent==='接受所列适配假设并重新预览'),false);
});

test('accepting auto assumptions uses fresh preview and scene edit revokes it',async()=>{
  const {PL,EQ,EF}=mechanicsSetup();await PL.begin('a');EQ.render=()=>{};
  const b={binding_id:'auto',provider_character_id:'a',parts:[],upstream_recipe:{schema_version:1,ability_refs:[],scenario:{static_policy:'included',max_party_distance:20}}};
  EQ.plan().equipment_inputs.a=EQ.blankPanelInput();
  const preview={binding_id:'auto',status:'pending',assumptions_fingerprint:'current',semantic_assumptions:[{assumption:'精确原值假设'}]};
  PL.state.report={upstream_compilations:[preview]};let calls=0;PL.preview=async()=>{calls++;};
  const card=new Element();EF.renderUpstream(card,b);await press(card,'接受所列适配假设并重新预览');
  assert.equal(b.upstream_recipe.scenario.accepted_assumptions_fingerprint,'current');assert.equal(calls,1);
  const field=control(card,'队友与提供者的最大距离（米，需明确）');field.value='15';field.emit('change');
  assert.equal(b.upstream_recipe.scenario.accepted_assumptions_fingerprint,null);
  await press(card,'接受所列适配假设并重新预览');assert.equal(calls,1);
});

test('mechanics editor changes numeric input only after validation and invalidates both result and saved state',async()=>{
  const {world,PL,MC,RT,dialog}=mechanicsSetup();const old=PL.currentInputJson();MC.edit('rules');
  const field=control(dialog(),'持续秒数');field.value='3';field.emit('change');assert.equal(PL.currentInputJson(),old);
  await press(dialog(),'应用修改');assert.equal(PL.state.plan.mechanics_model.rules[0].timing.duration,3);
  assert.equal(PL.state.plan.resolution.state,'pending');assert.equal(PL.dirty(),true);assert.equal(PL.state.fileSha,'original-sha');
  assert.equal(RT.tpl(),PL.state.plan.manual_template);assert.equal(world.RC.state.result,null);
});
test('mechanics editor refuses invalid and late asynchronous validation results',async()=>{
  const {world,PL,MC,dialog}=mechanicsSetup();const before=PL.currentInputJson();MC.edit('rules');
  world.pywebview.api.validate_mechanics_model=async()=>({code:0,data:{status:'invalid',issues:[{path:'rules[0].timing.duration'}]}});
  await press(dialog(),'应用修改');assert.equal(PL.currentInputJson(),before);
  let finish;world.pywebview.api.validate_mechanics_model=()=>new Promise(resolve=>{finish=resolve;});
  const action=press(dialog(),'应用修改');PL.state.plan.manual_template.meta.name='稍后的修改';
  finish({code:0,data:{status:'passed',issues:[]}});await action;
  assert.equal(PL.state.plan.manual_template.meta.name,'稍后的修改');assert.equal(PL.state.plan.mechanics_model.rules[0].timing.duration,2);
});
test('new mechanical source is pending, uniquely linked and not silently approved',()=>{
  const {PL,MC}=mechanicsSetup();MC.newRule();const rule=PL.state.plan.mechanics_model.rules.at(-1),binding=PL.state.plan.effect_bindings.at(-1);
  assert.equal(rule.status,'pending');assert.equal(binding.coverage.state,'unreviewed');assert.equal(binding.parts[0].state,'pending');
  assert.equal(rule.source_id,`${binding.binding_id}:${binding.parts[0].part_id}`);assert.equal(rule.owner,binding.provider_character_id);
  assert.equal(PL.state.plan.mechanics_model.initial_state.rules.at(-1).rule_id,rule.id);
});
test('mechanical match editor selects expanded stable hits and original rules',async()=>{
  const {PL,EF}=mechanicsSetup();await PL.begin('a');
  assert.deepEqual(clone(EF.candidateHits().map(h=>h.id)),['ticks:1','ticks:2','ticks:3']);
  assert.ok(EF.currentBuffs().some(b=>b.id==='boost'&&b.name==='工程两次增伤'));
  assert.equal(EF.currentBuffs().some(b=>b.id.startsWith('mechanics:')),false);
});
test('changing action model retains version 3 and mechanical rules',async()=>{
  const {world,PL}=mechanicsSetup();const original=clone(PL.state.plan.mechanics_model);
  assert.equal(await world.RC.setModel(clone(PL.state.plan.rotation_model)),true);
  assert.equal(PL.state.plan.plan_version,3);assert.deepEqual(clone(PL.state.plan.mechanics_model),original);
});
test('renaming a character updates mechanics recipients, provider and initial resource maps',()=>{
  const {PL}=mechanicsSetup();const m=PL.state.plan.mechanics_model;
  PL.state.plan.mechanism_sources=[{id:'source',actor:'a',mechanism_id:'typed.example',accepted_fingerprint:'old'},
    {id:'other',actor:'b',mechanism_id:'typed.other',accepted_fingerprint:'keep'}];
  PL.state.plan.manual_template.buffs=[{extensions:{owner:'a',source_id:'manual:independent'}}];
  m.rules[0].beneficiaries={mode:'characters',characters:['a'],scope:'locked'};
  m.rules[0].resources=[{amounts:{a:1}}];m.initial_state.rules[0].instances=[{beneficiaries:['a'],remaining:[2],quota:{a:2}}];
  m.attribute_model={contract:'attribute-ledger-v1',initial_basis:[{actor:'a',nonextra:{mode:'explicit',value:120},
    included_components:[{component:{provider:'a',effect_id:'a',source_fingerprint:'old'},value:20}]}],
    effect_classifications:[{component:{provider:'a',effect_id:'a'}},{component:{provider:'b',effect_id:'other'}}]};
  PL.state.plan.attribute_review={mode:'manual_context',context_fingerprint:'old',note:'keep evidence'};
  PL.renameCharacter('a','renamed');assert.equal(m.rules[0].owner,'renamed');assert.deepEqual(clone(m.rules[0].beneficiaries.characters),['renamed']);
  assert.deepEqual(clone(m.initial_state.rules[0].instances[0].quota),{renamed:2});assert.deepEqual(clone(m.rules[0].resources[0].amounts),{renamed:1});
  assert.equal(PL.state.plan.manual_template.buffs[0].extensions.owner,'renamed');
  assert.equal(m.attribute_model.initial_basis[0].actor,'renamed');
  assert.equal(m.attribute_model.initial_basis[0].nonextra.value,120);
  assert.equal(m.attribute_model.initial_basis[0].included_components[0].component.provider,'renamed');
  assert.deepEqual(clone(m.attribute_model.effect_classifications.map(r=>r.component.provider)),['renamed','b']);
  assert.equal(m.attribute_model.effect_classifications[0].component.effect_id,'a');
  assert.equal(PL.state.plan.attribute_review.context_fingerprint,null);
  assert.equal(PL.state.plan.attribute_review.note,'keep evidence');
  assert.deepEqual(clone(PL.state.plan.mechanism_sources),[
    {id:'source',actor:'renamed',mechanism_id:'typed.example',accepted_fingerprint:null},
    {id:'other',actor:'b',mechanism_id:'typed.other',accepted_fingerprint:'keep'}]);
});
test('formula button calls dedicated native export only for a current result',async()=>{
  const {world,PL,MC,RS,RT}=mechanicsSetup();let calls=0;
  world.pywebview.api.export_formula_workbook=async p=>{calls++;assert.equal(p.plan_id,PL.state.plan.plan_id);return{code:0,data:{path:'result.xlsx'}};};
  await MC.exportWorkbook();assert.equal(calls,0);
  RS.state.result={summary:{total_damage:225},hits:[],buffs_coverage:[]};RS.state.snapshot={revision:RT.state.revision,json:PL.currentInputJson(),name:'工程',template:clone(PL.state.plan)};
  await MC.exportWorkbook();assert.equal(calls,1);
  PL.state.plan.mechanics_model.rules[0].timing.duration++;RT.syncRevision();await MC.exportWorkbook();assert.equal(calls,1);
});

test('mechanics results show actual trace and scope without projecting fake buff uptime',async()=>{
  const {world,PL,RS,nodes}=mechanicsSetup();let coverageCalls=0;RS.renderCoverage=()=>{coverageCalls++;return new Element();};
  world.pywebview.api.run_plan_calc=async p=>({code:0,data:{plan:p,result:{summary:{total_damage:225},hits:[],buffs_coverage:[{uptime:.0000001}],
    rotation_check:{status:'passed',damage_interpretation:'计划伤害；检查范围有限'},mechanics_status:'passed',
    mechanics_trace:[{type:'quota',rule_id:'boost',t:.2,before:2,after:1},{type:'expiry',rule_id:'boost',t:2,layers_before:1,layers_after:0}],
    coverage_interpretation:{kind:'hit_projection',message:'增益投影不代表机制覆盖率'}}}});
  await RS.runCalc();expandResult(nodes,'mechanics');expandResult(nodes,'effects');const text=nodes.get('#result-body').textContent;
  assert.equal(coverageCalls,0);assert.match(text,/检查范围有限/);assert.doesNotMatch(text,/当前结果未验证动作/);
  assert.match(text,/剩余次数 2 → 1/);assert.match(text,/层数 1 → 0/);assert.match(text,/不代表机制覆盖率/);
  assert.equal(PL.state.plan.plan_version,3);
  for(const code of ['not_active','recipient_mismatch','not_frontline','no_consumption_receipt','agent_excluded','filter_mismatch'])
    assert.match(world.MC.label(code),/[\u4e00-\u9fff]/);
});
test('new native calculation submits the authoritative plan and exports its exact report handle',async()=>{
  const {world,PL,RS}=setup();let sent,exported;
  world.pywebview.api.run_plan_calc=async p=>{sent=clone(p);return{code:0,data:{plan:p,report_handle:'accepted',result:{summary:{total_damage:100},hits:[],buffs_coverage:[]},result_receipt:{revision:p.revision}}};};
  world.WB={pickUserFile:async()=>({path:'export.xlsx'})};
  world.pywebview.api.export_viewed_report=async(handle,fmt,path)=>{exported={handle,fmt,path};return{code:0,data:{path}};};
  await RS.runCalc();assert.equal(sent.kind,'genshin-plan');assert.ok('resolution'in sent);assert.ok('resolved_template'in sent);
  assert.equal(RS.isCurrent(),true);assert.equal(await RS.exportResult('xlsx'),true);assert.equal(exported.handle,'accepted');assert.equal(exported.fmt,'xlsx');
});

test('saved report first renders the stored source and loads only the requested damage page',async()=>{
  const {world,PL,RS,nodes}=setup({resultRows:true}),calls=[];
  const hit={t:0,character:'c1',skill:'burst',damage_system:'classic',expected:100,buffs:[],zones:{}};
  let tab='result';world.WS={tab:()=>tab};
  world.pywebview.api.open_saved_calculation=async(path,sha)=>{
    calls.push([path,sha]);
    return {code:0,data:{path,report_handle:'saved',report_projection:'summary',
      result:{summary:{total_damage:20300},reported_hit_count:203},
      result_receipt:{engine_version:'0.46.0'},plan:{manual_template:{meta:{name:'保存的长轴'},characters:[]}}}};
  };
  world.pywebview.api.read_viewed_report=async(handle,section,offset)=>{
    assert.equal(handle,'saved');calls.push([section,offset]);
    return {code:0,data:section==='damage'?{}:{items:Array.from({length:Math.min(100,203-offset)},(_,index)=>({...hit,t:offset+index})),total:203}};
  };
  await RS.openSavedReport('long.calculated.json',{expectedFileSha:'chosen'});
  const report=nodes.get('#result-body');
  assert.deepEqual(calls,[['long.calculated.json','chosen']]);
  assert.match(report.textContent,/保存的长轴/);assert.match(report.textContent,/计算版本 0\.46\.0/);
  assert.match(report.textContent,/伤害明细（203）/);assert.equal(RS.view().kind,'saved');
  assert.notEqual(RS.resultTemplate(),PL.state.plan.manual_template);
  expandResult(nodes,'damage');await new Promise(setImmediate);
  assert.deepEqual(calls.slice(1),[['damage',0],['hits',0]]);
  assert.equal(all(report).filter(row=>row.tagName==='tr').length,101);
  all(report).find(row=>row.tagName==='button'&&row.textContent==='下一页').emit('click');
  await new Promise(setImmediate);
  assert.deepEqual(calls.at(-1),['hits',100]);
  assert.equal(all(report).filter(row=>row.tagName==='tr').length,101);
  tab='rotation';assert.equal(RS.view().kind,'editor');assert.equal(RS.view().result,null);
  tab='whiteboard';assert.equal(RS.view().kind,'whiteboard');assert.equal(RS.view().result,null);
  tab='result';assert.equal(RS.view().kind,'saved');assert.equal(RS.view().result.reported_hit_count,203);
});

test('opening an invalid calculated report preserves its typed failure and current report',async()=>{
  const {world,RS}=setup();
  const existing={kind:'saved',result:{summary:{total_damage:20300}},snapshot:{name:'原报告'}};
  const failure={code:1,reason:'invalid_calculation_package',message:'计算报告完整性校验失败。',details:{field:'integrity'}};
  RS.reportOrigin=existing;
  world.pywebview.api.open_saved_calculation=async()=>failure;
  const errors=[];
  assert.equal(await RS.openSavedReport('invalid.calculated.json',{onError:error=>errors.push(error)}),false);
  assert.deepEqual(clone(errors),[failure]);
  assert.equal(RS.reportOrigin,existing);
});

test('formal whiteboard reports export by applied capability and reject a document switch at the picker',async()=>{
  const {world,RS,nodes}=setup();let tab='result',choose;const fingerprint='c'.repeat(64),requests=[];
  const board={title:'原生DSH正式计算链'};
  world.WS={tab:()=>tab};
  RS.reportOrigin={kind:'whiteboard'};
  world.WB={state:{board,result:{result:{summary:{total_damage:100}},result_receipt:{result_fingerprint:fingerprint}},
    reusableResult:{owner:'native_dsh',sessionId:'applied-session',deliveryCapability:'applied-capability'}},
    pickUserFile:()=>new Promise(resolve=>{choose=resolve;})};
  world.pywebview.api.dsh_client_publish_whiteboard_result=async request=>{requests.push(clone(request));return {code:0,data:{path:request.output_path}};};
  RS.renderAll();assert.match(nodes.get('#result-body').textContent,/原生DSH正式计算链/);
  assert.equal(nodes.get('#btn-exp-xlsx').disabled,false);
  const stale=RS.exportResult('csv');tab='rotation';choose({path:'not-written.csv'});
  assert.equal(await stale,false);assert.equal(requests.length,0);
  tab='result';const confirmed=RS.exportResult('xlsx');choose({path:'export.xlsx',file_sha:'b'.repeat(64)});
  assert.equal(await confirmed,true);assert.deepEqual(requests,[{conversation_id:'applied-session',
    delivery_capability:'applied-capability',output_path:'export.xlsx',format:'xlsx',
    expected_file_sha:'b'.repeat(64),expected_result_fingerprint:fingerprint}]);
  assert.match(nodes.get('#result-body').textContent,/结果已导出 · export\.xlsx/);
});

test('timeline snapshot retains composed execution rules and rejects a later pending calculation',async()=>{
  const {world,PL,RS}=setup();let invalidations=0;
  world.TV={invalidateData:()=>{invalidations++;}};
  const compiled={rules:[{id:'compiled',name_zh:'上游规则'}]};
  world.pywebview.api.run_plan_calc=async p=>({code:0,data:{plan:p,report:{mechanics_model:compiled},
    result:{summary:{total_damage:1},hits:[],buffs_coverage:[]}}});
  await RS.runCalc();assert.equal(RS.isCurrent(),true);
  compiled.rules[0].name_zh='后续修改';
  assert.equal(RS.state.snapshot.executionModel.rules[0].name_zh,'上游规则');
  world.pywebview.api.run_plan_calc=async()=>({code:1,message:'来源已失效',details:[{path:'effect_bindings'}]});
  await RS.runCalc();assert.equal(PL.state.plan.resolution.state,'pending');
  assert.equal(RS.isCurrent(),false);assert.ok(invalidations>0);
});
test('equipment draft changes do not mutate current plan and cancellation restores valid result',async()=>{
  const {world,PL,RS}=setup();world.pywebview.api.run_plan_calc=async p=>({code:0,data:{plan:p,result:{summary:{total_damage:1},hits:[],buffs_coverage:[]}}});
  await RS.runCalc();const before=PL.currentInputJson();await PL.begin('c1');PL.state.draft.manual_template.meta.name='changed';PL.touch();
  assert.equal(PL.currentInputJson(),before);assert.equal(RS.isCurrent(),false);
  world.pywebview.api.load_plan=async()=>({code:0,data:{file_sha:'sha1'}});await PL.cancel();assert.equal(RS.isCurrent(),true);
});
test('cancel cannot resurrect a result after source changes',async()=>{
  const {world,PL,RS}=setup();world.pywebview.api.run_plan_calc=async p=>({code:0,data:{plan:p,result:{summary:{total_damage:1},hits:[],buffs_coverage:[]}}});
  await RS.runCalc();await PL.begin('c1');PL.sourceChanged();world.pywebview.api.load_plan=async()=>({code:0,data:{file_sha:'sha1'}});await PL.cancel();
  assert.equal(RS.isCurrent(),false);assert.equal(PL.state.plan.resolution.state,'pending');
});
test('late compose response does not overwrite later draft input',async()=>{
  const {world,PL}=setup();await PL.begin('c1');let finish;world.pywebview.api.compose_equipment_plan=()=>new Promise(r=>finish=r);
  const pending=PL.preview();PL.state.draft.manual_template.meta.name='latest';PL.touch();
  finish({code:0,data:{plan:{manual_template:{meta:{name:'old'}}},state:'ready'}});assert.equal(await pending,false);
  assert.equal(PL.state.draft.manual_template.meta.name,'latest');assert.equal(PL.state.report,null);
});
test('save uses loaded SHA and does not mark a concurrent edit as saved',async()=>{
  const {world,PL,RT}=setup();let finish,expected;world.pywebview.api.save_plan=(p,plan,sha)=>{expected=sha;return new Promise(r=>finish=r);};
  const work=RT.saveTemplate();PL.state.plan.manual_template.meta.name='later';RT.syncRevision();
  finish({code:0,data:{path:'plans/p.json',file_sha:'sha2'}});await work;assert.equal(expected,'sha1');assert.equal(PL.dirty(),true);
});
test('real weapon query rendering uses ratio units, actual refinements and source text',async()=>{
  const {world,ED,nodes}=setup();ED.category='weapon';ED.dataVersion=fixture.weapon_detail.data.data_version;await ED.open(11401);await Promise.resolve();
  const box=nodes.get('#data-detail');assert.match(box.textContent,/西风剑/);assert.match(box.textContent,/精炼/);
  assert.match(box.textContent,/%/);assert.equal(ED.unit(.078,'ratio'),'7.8%');assert.match(box.textContent,/原文与来源/);
});
test('real artifact data presents source slots and confirmed substat values in percent',async()=>{
  const {PL,EQ,UI}=setup();await PL.begin('c1');const a={substats:[{prop_type:'FIGHT_PROP_CRITICAL',value:.078,source:{note:'理论'}}]};
  const box=UI.el('div');EQ.substats(box,a);const value=control(box,'累计值 %');assert.equal(Number(value.value),7.8);
  value.value='10.5';value.emit('change');assert.equal(a.substats[0].value,.105);
});
test('weapon replacement preserves only valid level/phase and cannot pretend a R1-only weapon supports R5',async()=>{
  const {world,PL,EQ}=setup();await PL.begin('c1');PL.state.draft.equipment_inputs.c1=EQ.blankInput(90);
  PL.state.draft.equipment_inputs.c1.weapon={weapon_id:1,level:90,ascension:6,refinements:[{source_index:0,affix_group_id:1,refinement:5}]};
  const d=clone(fixture.weapon_detail);d.data.weapon.available_states=[{ascension:0,min_level:1,max_level:20}];d.data.effects[0].refinements=d.data.effects[0].refinements.slice(0,1);
  world.pywebview.api.weapon_detail=async()=>d;await EQ.chooseWeapon(11401);const w=EQ.input().weapon;
  assert.equal(w.level,null);assert.equal(w.ascension,null);assert.equal(w.refinements[0].refinement,null);
});
test('empty tuning remains empty and no full buff hit matrix is synthesized',async()=>{
  const {PL,EF,UI}=setup();await PL.begin('c1');const box=UI.el('div');EF.renderTuning(box);
  assert.equal(PL.state.draft.rotation_tuning.buff_hit_matches.length,0);assert.match(box.textContent,/默认没有额外微调/);
});
test('tuning removal changes only the selected exception, not manual Buff effects',async()=>{
  const {PL,EF,UI}=setup();await PL.begin('c1');const p=PL.state.draft;p.manual_template.buffs=[{id:'b',effects:[{id:'a'},{id:'b'}]}];
  p.rotation_tuning.buff_hit_matches=[{id:'m',effect_ref:{buff_id:'b',effect_id:'a'},mode:'replace',hit_ids:['h1'],reason:'例外',state:'pending'}];
  const before=JSON.stringify(p.manual_template.buffs),box=UI.el('div');EF.renderTuning(box);
  assert.equal(all(box).some(n=>n.tagName==='button'&&n.textContent==='撤销此微调'),false);
  EF.tuningMenuItems(p.rotation_tuning.buff_hit_matches[0]).find(item=>item.label==='撤销此微调').run();
  assert.equal(p.rotation_tuning.buff_hit_matches.length,0);assert.equal(JSON.stringify(p.manual_template.buffs),before);
});
test('managed character panel does not edit historical final stats',()=>{
  const {PL,EQ,RT,nodes}=setup();PL.state.plan.equipment_inputs.c1=EQ.blankInput(90);RT.renderAll();
  assert.match(nodes.get('#team-panel').textContent,/最终面板由明确输入生成/);
  assert.equal(control(nodes.get('#team-panel'),'攻击力'),undefined);
});

test('opening another plan cannot reuse previous effect requirements',async()=>{
  const {PL,EQ}=setup();EQ.lastReport={effect_requirements:[{binding_id:'old'}]};
  await PL.begin('c1');assert.equal(EQ.lastReport,null);
  EQ.lastReport={effect_requirements:[{binding_id:'old'}]};PL.replace(clone(PL.state.plan));assert.equal(EQ.lastReport,null);
});
test('direct none effect exposes an explicit exclusion reason',()=>{
  const {EF,UI}=setup(),box=UI.el('div'),effect={applies_to:{mode:'none'}};
  EF.scope(box,effect,()=>{});const reason=control(box,'明确不关联的场景理由');reason.value='此轴不触发';reason.emit('change');
  assert.equal(effect.extensions.exclusion_reason,'此轴不触发');
});
test('annotation management uses total count and requests the selected page and orphan state',async()=>{
  const {world,nodes}=setup(),DV=world.DV;let args;
  world.pywebview.api.list_annotations=async(...a)=>{args=a;return{code:0,data:{items:[],total:205}};};
  DV.state.annManager=true;DV.state.annTarget={target_type:'equip_affix',target_id:0,label:'全库'};DV.state.annPage=2;DV.state.annStatus='orphan';
  await DV.refreshAnnotations();assert.deepEqual(args,['equip_affix',0,'orphan','',2,100]);
  assert.match(nodes.get('#annotation-panel').textContent,/共 205 条 · 第 2 页/);
});
test('annotations are read-only and direct database mutation controls are absent',()=>{
  const {world}=setup(),DV=world.DV;
  const form=DV.renderAnnotationForm(null);
  assert.match(form.textContent,/只读/);assert.match(form.textContent,/source/);assert.match(form.textContent,/完整新数据库版本/);
  assert.equal(all(form).some(n=>n.tagName==='textarea'||n.tagName==='input'||n.tagName==='button'),false);
  const item=DV.renderAnnotationItem({id:1,status:'approved',title:'已审资料',category:'',content:'内容',target_type:'equip_affix',target_id:1114010,version_line:'release',updated_at:'2026-10-28'});
  assert.equal(all(item).some(n=>n.tagName==='button'),false);assert.match(item.textContent,/内容/);
});
test('manual copy is backend validated and does not reuse the original native file path',async()=>{
  const {world,PL,RT}=setup(),original=PL.state.plan;
  world.pywebview.api.run_plan_calc=async p=>({code:0,data:{plan:{...p,resolved_template:clone(p.manual_template)}}});
  await PL.manualCopy();assert.equal(PL.state.plan,null);assert.equal(RT.state.path,'');
  assert.match(RT.tpl().meta.name,/手工数值副本/);assert.ok(original.plan_id);
});

test('open and back without editing keeps saved input and result current',async()=>{
  const {world,PL,RS,EQ}=setup();world.pywebview.api.run_plan_calc=async p=>({code:0,data:{plan:p,result:{summary:{total_damage:1},hits:[],buffs_coverage:[]}}});
  await RS.runCalc();const before=PL.currentInputJson();await PL.begin('c1');
  assert.equal(PL.dirty(),false);assert.equal(RS.isCurrent(),true);PL.back();
  assert.equal(PL.state.draft,null);assert.equal(EQ.showing,false);assert.equal(PL.currentInputJson(),before);
  assert.equal(PL.dirty(),false);assert.equal(RS.isCurrent(),true);
});

test('back keeps genuine draft and context without applying or saving',async()=>{
  const {PL,EQ,RT,nodes}=setup();const before=PL.currentInputJson();nodes.get('#team-panel').scrollTop=137;
  await PL.begin('c1');PL.state.draft.manual_template.meta.name='草稿方案';PL.touch();PL.back();
  assert.equal(PL.currentInputJson(),before);assert.equal(PL.draftChanged(),true);
  assert.match(nodes.get('#plan-status').textContent,/继续编辑装备草稿/);assert.equal(nodes.get('#team-panel').scrollTop,137);
  await PL.begin('c1');assert.equal(PL.state.draft.manual_template.meta.name,'草稿方案');
  RT.state.revision++;assert.equal(await PL.apply(),false);assert.equal(PL.currentInputJson(),before);
});

test('explicit discard confirms genuine changes but does not confirm read-only browsing',async()=>{
  const {world,PL}=setup();let confirmed=0;world.confirm=()=>{confirmed++;return false;};
  await PL.begin('c1');await PL.discard();assert.equal(confirmed,0);assert.equal(PL.state.draft,null);
  await PL.begin('c1');PL.state.draft.manual_template.meta.name='修改';PL.touch();
  assert.equal(await PL.discard(),false);assert.equal(confirmed,1);assert.ok(PL.state.draft);
});

test('save applied only preserves draft and never submits its unpublished changes',async()=>{
  const {world,PL,RT}=setup();let sent;
  world.pywebview.api.save_plan=async(path,p)=>{sent=clone(p);return{code:0,data:{path,file_sha:'sha2'}};};
  await PL.begin('c1');PL.state.draft.manual_template.meta.name='未应用名称';PL.touch();
  PL.chooseDraftSave=async()=> 'applied';assert.equal(await RT.saveTemplate(),true);
  assert.notEqual(sent.manual_template.meta.name,'未应用名称');assert.equal(PL.state.draft.manual_template.meta.name,'未应用名称');
  assert.equal(PL.dirty(),true);
});

test('equipment snapshot names and selections survive current plan edits',async()=>{
  const {world,PL,RS,nodes}=setup();const summary={context:{kind:'calculation_snapshot',input_fingerprint:'old'},characters:[{
    actor_id:'c1',display_name:'旧角色',level:90,input_mode:'equipment',source_status:'current',
    weapon:{state:'selected',display_name:'旧武器',weapon_id:1,level:90,ascension:6,refinements:[{source_index:0,refinement:1}]},
    artifact_slots:[],artifact_sets:[],effect_statuses:[],panel_stages:{}}]};
  world.pywebview.api.run_plan_calc=async p=>({code:0,data:{plan:p,equipment_summary:summary,result:{summary:{total_damage:1},hits:[],buffs_coverage:[]}}});
  await RS.runCalc();summary.characters[0].weapon.display_name='后续变化';PL.state.appliedSummary.characters[0].weapon.display_name='当前武器';
  RS.renderAll();expandResult(nodes,'diagnostics');assert.match(nodes.get('#result-body').textContent,/旧武器/);assert.doesNotMatch(nodes.get('#result-body').textContent,/当前武器|后续变化/);
});

test('manual result equipment fallback uses old snapshot rather than newly loaded team',async()=>{
  const {world,PL,RS,RT,nodes}=setup();PL.state.plan=null;RT.tpl().characters[0].name='快照角色';
  world.pywebview.api.run_calc=async()=>({code:0,data:{result:{summary:{total_damage:1},hits:[],buffs_coverage:[]}}});
  await RS.runCalc();RT.tpl().characters[0].name='当前角色';RS.renderAll();
  expandResult(nodes,'diagnostics');assert.match(nodes.get('#result-body').textContent,/快照角色/);assert.match(nodes.get('#result-body').textContent,/装备来源未提供/);
  assert.doesNotMatch(nodes.get('#result-body').textContent,/当前角色/);
});

test('equipment panel preview exposes elemental bonus and separate stat stages',async()=>{
  const {world,PL,EQ}=setup();await PL.begin('c1');
  PL.state.report={state:'ready',contribution_report:{characters:{c1:{base:{atk:100},static:{atk:200,dmg_bonus:{cryo:.466}},
    final:{atk:300,dmg_bonus:{cryo:.616}},contributions:[]}}}};
  EQ.renderPreview();const text=world.document.querySelector('#equipment-preview').textContent;
  assert.match(text,/计算基础值/);assert.match(text,/静态组合阶段/);assert.match(text,/冰元素伤害加成/);assert.match(text,/61.6%/);
});

test('late equipment detail request from previous view cannot overwrite reopened data',async()=>{
  const {world,PL,EQ}=setup();await PL.begin('c1');let finish;
  world.pywebview.api.weapon_detail=()=>new Promise(resolve=>finish=resolve);
  EQ.loadDetail('weapon',1,'release');EQ.openView('c1');EQ.cache.set('weapon:1:release',{weapon:{name:'最新资料'}});
  finish({code:0,data:{weapon:{name:'过期资料'}}});await Promise.resolve();await Promise.resolve();
  assert.equal(EQ.getDetail('weapon',1,'release').weapon.name,'最新资料');
});

test('applying a returned draft rejects an externally changed file before composition',async()=>{
  const {world,PL}=setup();let composed=0;
  world.pywebview.api.inspect_plan_file=async()=>({code:0,data:{file_sha:'external-edit'}});
  world.pywebview.api.compose_equipment_plan=async()=>{composed++;throw Error('must not apply');};
  await PL.begin('c1');PL.state.draft.manual_template.meta.name='保留草稿';PL.touch();PL.back();
  assert.equal(await PL.apply(),false);assert.equal(composed,0);assert.equal(PL.state.draft.manual_template.meta.name,'保留草稿');
  assert.equal(PL.state.plan.resolution.state,'pending');
});

test('editing while the apply file check is pending cannot submit a newer draft without a new request',async()=>{
  const {world,PL}=setup();let finish,composed=0;
  world.pywebview.api.inspect_plan_file=()=>new Promise(r=>finish=r);
  world.pywebview.api.compose_equipment_plan=async()=>{composed++;};
  await PL.begin('c1');PL.state.draft.manual_template.meta.name='初稿';PL.touch();const applying=PL.apply();
  PL.state.draft.manual_template.meta.name='新修改';PL.touch();finish({code:0,data:{file_sha:'sha1'}});
  assert.equal(await applying,false);assert.equal(composed,0);assert.equal(PL.state.draft.manual_template.meta.name,'新修改');
});

test('failed legacy load or new-template request does not clear native inputs or retained draft',async()=>{
  const {world,PL,RT}=setup();const before=PL.currentInputJson();
  await PL.begin('c1');PL.state.draft.manual_template.meta.name='保留输入';PL.touch();PL.leave=async()=>true;
  world.pywebview.api.inspect_plan_file=async()=>({code:0,data:{kind:null}});
  world.pywebview.api.load_template=async()=>({code:1,message:'无效模板'});
  world.pywebview.api.new_template=async()=>({code:1,message:'创建失败'});
  await RT.loadTemplate('invalid.json');assert.equal(PL.currentInputJson(),before);assert.equal(PL.state.draft.manual_template.meta.name,'保留输入');
  await RT.newTemplate();assert.equal(PL.currentInputJson(),before);assert.equal(PL.state.draft.manual_template.meta.name,'保留输入');
});

test('new panel authoring edits initial stats without creating individual artifacts',async()=>{
  const {PL,EQ,RT,nodes,world}=setup();await PL.begin('c1');
  const editor=nodes.get('#equipment-editor');
  all(editor).find(n=>n.tagName==='button'&&n.textContent==='启用初始面板与套装选择').emit('click');
  assert.equal(EQ.input().mode,'panel');assert.equal(EQ.input().panel_policy,'static_included');
  assert.equal(EQ.input().artifacts,undefined);assert.equal(EQ.input().basis,undefined);assert.equal(EQ.input().level,undefined);
  assert.match(editor.textContent,/选择4件套/);assert.doesNotMatch(editor.textContent,/累计副词条|本方案空槽|五槽/);
  EQ.section='basis';EQ.render();const atk=control(editor,'攻击力');atk.value='2100';atk.emit('change');
  const crit=control(editor,'暴击率 %');crit.value='70';crit.emit('change');
  assert.equal(PL.state.draft.manual_template.characters[0].stats.atk,2100);
  assert.equal(PL.state.draft.manual_template.characters[0].stats.crit_rate,.7);
  assert.equal(RT.tpl().characters[0].stats.atk,1000);assert.equal(PL.draftChanged(),true);
  assert.match(world.document.querySelector('#equipment-preview').textContent,/预览尚未生成或已失效/);
});

test('direct set selections form four-piece or distinct two-plus-two while preserving the given panel',async()=>{
  const {PL,EQ}=setup();await PL.begin('c1');PL.state.draft.equipment_inputs.c1=EQ.blankPanelInput();
  const initial=JSON.stringify(PL.state.draft.manual_template.characters[0]),a=clone(fixture.artifact_set_detail.data),b=clone(a);
  b.artifact_set.id=15020;b.artifact_set.name='绝缘之旗印';
  EQ.setSelection(a,4,0);assert.deepEqual(clone(EQ.input().artifact_sets),[{set_id:a.artifact_set.id,count:4}]);
  EQ.setSelection(b,2,1);assert.deepEqual(clone(EQ.input().artifact_sets),[{set_id:a.artifact_set.id,count:2},{set_id:15020,count:2}]);
  const before=JSON.stringify(EQ.input().artifact_sets);EQ.setSelection(a,2,1);
  assert.equal(JSON.stringify(EQ.input().artifact_sets),before);
  const wrong=clone(b);wrong.data_version.built_at='changed';EQ.setSelection(wrong,4,0);
  assert.equal(JSON.stringify(EQ.input().artifact_sets),before);
  assert.equal(JSON.stringify(PL.state.draft.manual_template.characters[0]),initial);
});

test('set catalog offers the selected panel slot and cannot replace legacy per-piece inputs',async()=>{
  const {PL,EQ,ED,nodes}=setup();await PL.begin('c1');PL.state.draft.equipment_inputs.c1=EQ.blankPanelInput();
  ED.detail=clone(fixture.artifact_set_detail.data);EQ.picking=true;EQ.setPick={index:0,count:4};ED.renderDetail();
  const btn=all(nodes.get('#data-detail')).find(n=>n.tagName==='button'&&n.textContent==='选为当前4件套');
  assert.ok(btn);await btn.emit('click');assert.equal(EQ.input().artifact_sets[0].count,4);
  PL.state.draft.equipment_inputs.c1=EQ.blankInput(90);const legacy=JSON.stringify(EQ.input());
  await EQ.pickFromData(ED.detail,{count:4,index:0});assert.equal(JSON.stringify(EQ.input()),legacy);
});

test('panel character card edits source stats, invalidates resolution, and leaves historical results intact',async()=>{
  const {PL,EQ,RT,RS,world,nodes}=setup();PL.state.plan.equipment_inputs.c1=EQ.blankPanelInput();RT.renderAll();
  world.pywebview.api.run_plan_calc=async p=>({code:0,data:{plan:p,result:{summary:{total_damage:1},hits:[],buffs_coverage:[]}}});
  PL.state.plan.resolution={state:'ready'};await RS.runCalc();assert.equal(RS.isCurrent(),true);
  const revision=PL.state.plan.revision,atk=control(nodes.get('#team-panel'),'攻击力');
  atk.value='2400';atk.emit('change');
  assert.equal(PL.state.plan.manual_template.characters[0].stats.atk,2400);
  assert.ok(PL.state.plan.revision>revision);assert.equal(PL.state.plan.resolution.state,'pending');
  assert.equal(PL.state.plan.resolved_template,null);assert.equal(PL.state.appliedSummary,null);assert.equal(RS.isCurrent(),false);
  assert.doesNotMatch(nodes.get('#team-panel').textContent,/五槽|最终面板由明确输入生成/);
});

test('panel summaries use selected sets and initial stats without missing-slot warnings',async()=>{
  const {PL,EQ,UI}=setup();PL.state.plan.equipment_inputs.c1={...EQ.blankPanelInput(),artifact_sets:[{set_id:42,count:4}]};
  const summary=EQ.summaryCharacter('c1'),card=EQ.summaryCard(summary,'面板快照',true);
  assert.equal(summary.input_mode,'panel');assert.equal(summary.level,90);assert.deepEqual(clone(summary.artifact_slots),[]);
  assert.equal(summary.panel_stages.static.atk,1000);assert.match(card.textContent,/初始面板模式|4件|初始面板来源/);
  assert.doesNotMatch(card.textContent,/五槽|累计副词条|主词条/);
  const box=UI.el('div');await PL.begin('c1');EQ.renderGear(box);assert.match(box.textContent,/圣遗物套装/);
  assert.doesNotMatch(box.textContent,/生之花|本方案空槽/);
});

test('panel effect authoring keeps included static parts explicit and starts conditional effects as buffs',async()=>{
  const {PL,EQ,EF,UI}=setup();await PL.begin('c1');PL.state.draft.equipment_inputs.c1=EQ.blankPanelInput();
  const part=EF.newPart();assert.equal(part.state,'pending');assert.equal(part.delivery.kind,'buff');assert.equal(part.delivery.effect.applies_to,null);
  const box=UI.el('div');EF.render(box);assert.match(box.textContent,/相应分项选择“已含面板或明确不计入”并填写原因/);
  PL.state.draft.equipment_inputs.c1=EQ.blankInput(90);assert.equal(EF.newPart().delivery.kind,'panel');
});

test('invalid panel catalog actions do not silently enable or dirty equipment inputs',async()=>{
  const {PL,EQ}=setup();await PL.begin('c1');
  await EQ.pickFromData(clone(fixture.artifact_set_detail.data),{count:2,index:1});
  assert.equal(EQ.input(),undefined);assert.equal(PL.draftChanged(),false);
  await EQ.pickFromData({artifact:{id:1}},{level:20});
  assert.equal(EQ.input(),undefined);assert.equal(PL.draftChanged(),false);
});

test('switching an explicit hit back to a numeric effect respects the current panel or legacy mode',async()=>{
  const {PL,EQ,EF,UI}=setup();await PL.begin('c1');
  for(const mode of ['panel','legacy'])for(const kind of ['constant','stat_scale']){
    PL.state.draft.equipment_inputs.c1=mode==='panel'?EQ.blankPanelInput():EQ.blankInput(90);
    const part=EF.newPart(),binding={parts:[part]},box=UI.el('div');
    EF.renderPart(box,binding,part,0);let select=control(box,'有限规则');select.value='explicit_hit';select.emit('change');
    assert.equal(part.delivery.kind,'hit');
    box.innerHTML='';EF.renderPart(box,binding,part,0);select=control(box,'有限规则');select.value=kind;select.emit('change');
    assert.equal(part.rule.kind,kind);assert.equal(part.delivery.kind,mode==='panel'?'buff':'panel');
    assert.equal(part.dependencies.mode,kind==='stat_scale'?'stat_rule':'source_only');
    if(mode==='panel'){assert.equal(part.delivery.target,'c1');assert.equal(part.delivery.effect.applies_to,null);}
  }
});

test('native result buff labels come from the frozen resolved output without changing input identity or damage',async()=>{
  for(const mode of ['panel','legacy']){
    const {PL,EQ,RT,RS,world,nodes}=setup({resultRows:true});
    PL.state.plan.equipment_inputs.c1=mode==='panel'?EQ.blankPanelInput():EQ.blankInput(90);
    RT.syncRevision();PL.state.plan.resolution={state:'ready'};
    const before=PL.currentInputJson();let returned;
    world.pywebview.api.run_plan_calc=async p=>{
      returned=clone(p);returned.resolved_template=clone(p.manual_template);
      returned.resolved_template.buffs=[{id:'generated.buff.ice',source:'冰套四件',effects:[{id:'ice.crit',name:'冰套暴击率',zone:'crit_rate'}]}];
      return{code:0,data:{plan:returned,result:{summary:{total_damage:1398.02625},
        hits:[{id:'h1',t:1,character:'c1',skill:'burst',damage_system:'classic',count:1,expected:1398.02625,
          buffs:['generated.buff.ice'],zones:{defense:.5}}],
        buffs_coverage:[{id:'generated.buff.ice',source:'冰套四件',uptime:1}]}}};
    };
    await RS.runCalc();assert.equal(RS.isCurrent(),true);assert.equal(PL.currentInputJson(),before);
    assert.equal(RS.state.snapshot.json,before);assert.equal(RS.state.snapshot.template.manual_template.buffs.length,0);
    assert.equal(RS.state.result.gui_input.template,RS.state.snapshot.template);
    assert.match(RS.renderHits(RS.state.result.hits).textContent,/冰套四件/);
    assert.match(RS.renderCoverage(RS.state.result.buffs_coverage).textContent,/冰套四件/);
    assert.doesNotMatch(nodes.get('#result-body').textContent,/增益 未定位/);
    returned.resolved_template.buffs[0].source='后续来源';PL.state.plan.manual_template.characters[0].stats.atk++;
    RT.syncRevision();assert.equal(RS.isCurrent(),false);
    assert.equal(RS.state.result.summary.total_damage,1398.02625);
    assert.match(RS.renderHits(RS.state.result.hits).textContent,/冰套四件/);
    assert.doesNotMatch(RS.renderHits(RS.state.result.hits).textContent,/后续来源/);
  }
});
