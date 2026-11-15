'use strict';

const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');
const WEB=path.resolve(__dirname,'../src/genshin_app/gui/web');
class Element{
  constructor(tag='div'){this.tagName=tag;this.children=[];this.attributes={};this.listeners={};this.style={setProperty(){}};this.value='';this.checked=false;this.disabled=false;this.open=false;this.className='';this.classList={add(){},remove(){},toggle(){}};}
  append(...nodes){this.children.push(...nodes);}appendChild(node){this.append(node);return node;}
  set innerHTML(value){this.children=[];this.text=String(value||'');}set textContent(value){this.children=[];this.text=String(value??'');}
  get textContent(){return(this.text||'')+this.children.map(node=>node.textContent).join('');}
  setAttribute(key,value){this.attributes[key]=String(value);}addEventListener(key,value){this.listeners[key]=value;}removeEventListener(){}
  emit(key,detail={}){return this.listeners[key]?.({target:this,preventDefault(){},...detail});}showModal(){this.open=true;}close(){this.open=false;}remove(){}
}
const all=node=>[node,...node.children.flatMap(all)],button=(node,label)=>all(node).find(item=>item.tagName==='button'&&item.textContent===label),field=(node,label)=>all(node).find(item=>item.attributes['aria-label']===label),clone=value=>JSON.parse(JSON.stringify(value)),settle=async()=>{await Promise.resolve();await Promise.resolve();};
function source(name){return fs.readFileSync(path.join(WEB,name),'utf8');}
function environment(){const body=new Element('body'),statuses=[];const world={console,Date,Math,JSON,setTimeout,clearTimeout,structuredClone,
  crypto:{randomUUID:()=> '00000000-0000-4000-8000-000000000001'},confirm:()=>true,setStatus:value=>statuses.push(value),addEventListener(){}};
  world.window=world;world.globalThis=world;world.document={body,createElement:tag=>new Element(tag),createElementNS:(_ns,tag)=>new Element(tag),querySelector:()=>null,querySelectorAll:()=>[]};world.$=()=>null;
  world.DS={raw(label,value){const d=new Element('details');d.append(Object.assign(new Element('summary'),{textContent:label}),Object.assign(new Element('pre'),{textContent:JSON.stringify(value,null,2)}));return d;},
    character:value=>String(value),prose:value=>String(value??'')};
  world.RT={state:{path:'plan.json'},tpl:()=>world.PL.state.plan?.manual_template,syncRevision(){},renderTimeline(){}};
  world.PL={state:{plan:null,draft:null,fileSha:'sha',sourceEpoch:0},currentInputJson(){return JSON.stringify(this.state.plan);},renderStatus(){}};
  world.RC={sourceEpoch:()=>world.PL.state.sourceEpoch,abilityName:ability=>ability?.id||'能力',model:()=>world.PL.state.plan?.rotation_model,
    touch(){world.PL.state.sourceEpoch++;},render(){}};
  vm.createContext(world);for(const name of ['json-transport.js','gui-foundation-v2.js','ui.js','action-admissions.js'])vm.runInContext(source(name),world,{filename:name});
  return {world,body,statuses};}
const counts=(overrides={})=>({current_action_admission_facts:7,current_models:7,exact:1,partial:1,pending:1,revoked:1,stale:1,conflict:1,not_evaluated:1,filtered_models:7,returned:7,...overrides});
function item(id,group){return {model_ref:{id,revision:1},current_receipt:{payload_fingerprint:'a'.repeat(64)},review:{status:group==='pending'?'pending':group==='revoked'?'revoked':'approved'},
  catalog_group:group,status_reason:group==='stale'?'snapshot_or_evidence_changed':group==='conflict'?'competing_current_models':group==='not_evaluated'?'declared_input_mismatch':null,
  selection_status:['exact','partial'].includes(group)?'reference_selectable_for_fresh_preview':'reference_not_selectable',target:{character_id:1001,skill_depot_id:2001,skill_id:3001,slot:'burst'},
  domain:{constellations:[0],variant:'default',action_type_id:'action.elemental_burst'},declared_filter:{status:group==='exact'?'exact_match':group==='partial'?'partial_match':'not_evaluated',matched_paths:[],mismatched:[],unknown_input_paths:[]},
  display:{character:{value:'测试角色',status:'present'},skill:{value:'测试爆发',status:'present'}},material_status:'not_evaluated',applicability:'declared_only',execution_authority:false};}
function plan(){return {plan_version:3,manual_template:{characters:[{id:'a'}]},equipment_inputs:{a:{character_ref:{db_character_id:1001,version_line:'release'}}},
  rotation_model:{characters:[{id:'a'}],abilities:[{id:'q',character:'a',action_type_id:'action.elemental_burst',variant:'default',cooldown:{group:'manual',duration:20,start:'action_start',charges:1,recovery:'parallel'},energy:{required:80,amount:80}}],
    actions:[{id:'cast',ability_id:'q'}],events:[{id:'spend',type:'energy_spend',action_id:'cast'}],initial_cooldowns:[]}};}

test('document transport exposes action admission catalog, preview, and reserved whiteboard methods',()=>{const {world}=environment();
  for(const method of ['action_admission_model_candidates','action_admission_preview','whiteboard_action_admission_model_candidates'])assert.equal(world.JSONTransport.usesMethod(method),true);});

test('catalog renders all seven groups, allows only reviewed exact/partial, and copies no gate values',async()=>{const {world}=environment(),holder=new Element(),items=['exact','partial','pending','revoked','stale','conflict','not_evaluated'].map(group=>item(`model.${group}`,group));let selected=null;
  world.callApi=async()=>({code:0,data:{items,counts:counts(),has_more:false,next_cursor:null}});
  const browser=world.AA.catalogBrowser(holder,{document:()=>({}),actor:()=> 'a',ability:()=> 'q',current:()=>true,onSelect:value=>{selected=clone(value);}});
  assert.equal(await browser.reload(),true);assert.match(holder.textContent,/完全匹配/);assert.match(holder.textContent,/资料已变化/);assert.match(holder.textContent,/当前模型冲突/);
  assert.equal(all(holder).filter(node=>node.tagName==='button'&&node.textContent==='选择此模型').length,2);
  await all(holder).find(node=>node.tagName==='button'&&node.textContent==='选择此模型').emit('click');
  const row={model_ref:{id:'old',revision:1},selection:{},accepted_fingerprint:'b'.repeat(64)};selected.cooldown={duration:99};selected.energy={amount:99};world.AA.applySelection(row,selected);
  assert.equal(row.model_ref.id,'model.exact');assert.deepEqual(clone(row.selection),{character_id:1001,skill_depot_id:2001,skill_id:3001,slot:'burst',constellation:0});
  assert.equal(row.accepted_fingerprint,null);assert.equal(Object.hasOwn(row,'cooldown'),false);assert.equal(Object.hasOwn(row,'energy'),false);
});

test('native editor keeps catalog, fresh preview, confirmation, and apply as separate steps',async()=>{const {world,body}=environment(),original=plan(),fingerprint='f'.repeat(64);world.PL.state.plan=original;const calls=[];
  world.callApi=async(method,...args)=>{calls.push({method,args:clone(args)});if(method==='action_admission_model_candidates')return {code:0,data:{items:[item('model.q','exact')],counts:counts({current_action_admission_facts:1,current_models:1,exact:1,partial:0,pending:0,revoked:0,stale:0,conflict:0,not_evaluated:0,filtered_models:1,returned:1}),has_more:false,next_cursor:null}};
    assert.equal(method,'action_admission_preview');const request=args[1],ready=request.accepted_fingerprint===fingerprint;return {code:0,data:{contract:'action-admission-plan-preview-v1',state:ready?'ready':'pending',execution_authority:false,
      compilation:{id:request.binding_id,ability_id:'q',status:ready?'ready':'pending',model_ref:request.model_ref,issues:ready?[]:[{message:'请确认fresh来源'}],required_confirmation:{accepted_fingerprint:fingerprint},
        profile:{compiled_values:{cooldown:{group:'skill:2001:3001',duration:20,start:'action_start',charges:1,recovery:'parallel'},energy:{required:80,amount:80,delivery:'action_start'}},declared_not_verified:['hit_sequence']}},
      proposal:{action_admission_sources:[{id:request.binding_id,ability_id:'q',version_line:'release',selection:request.selection,variant:'default',model_ref:request.model_ref,accepted_fingerprint:request.accepted_fingerprint}],
        action_admission_context:request.context,action_resource_sources:[],action_capacity_sources:[],rotation_model:{...clone(original.rotation_model),abilities:[{id:'q',character:'a',action_type_id:'action.elemental_burst',variant:'default',cooldown:null,energy:null}],events:[],initial_cooldowns:[{character:'a',group:'skill:2001:3001',ready_in:request.initial_ready_in}]}}}};};
  world.AA.edit();const dialog=body.children.at(-1);await settle();await button(dialog,'使用普通开放世界预设').emit('click');await settle();await button(dialog,'选择此模型').emit('click');
  const takeover=field(dialog,'允许受审动作接纳接管现有冷却、耗能、旧资源及手工扣能');takeover.checked=true;await takeover.emit('change');
  const ready=field(dialog,'明确开场冷却全部就绪');ready.checked=true;await ready.emit('change');
  assert.equal(calls.filter(call=>call.method==='action_admission_preview').length,0);await button(dialog,'读取fresh预览').emit('click');
  assert.match(dialog.textContent,/基础冷却：20 秒/);assert.equal(original.action_admission_sources,undefined);
  await button(dialog,'确认所示来源').emit('click');assert.equal(original.action_admission_sources,undefined);
  await button(dialog,'清除场景').emit('click');assert.match(dialog.textContent,/model.q/);
  await button(dialog,'应用动作接纳').emit('click');assert.equal(original.action_admission_sources,undefined);
  await button(dialog,'使用普通开放世界预设').emit('click');
  await button(dialog,'读取fresh预览').emit('click');await button(dialog,'确认所示来源').emit('click');
  await button(dialog,'应用动作接纳').emit('click');assert.equal(original.action_admission_sources[0].accepted_fingerprint,fingerprint);
  assert.equal(original.rotation_model.abilities[0].cooldown,null);assert.equal(original.rotation_model.abilities[0].energy,null);assert.equal(Object.hasOwn(original.rotation_model.abilities[0],'action_admission_source'),false);
  assert.deepEqual(clone(original.rotation_model.initial_cooldowns),[{character:'a',group:'skill:2001:3001',ready_in:[]}]);
});

test('native editor fails closed when another shared dialog still owns the modal surface',()=>{
  const {world,body,statuses}=environment();world.PL.state.plan=plan();const before=body.children.length;
  world.AppDialogKernel={create:()=>null};world.AA.edit();
  assert.equal(body.children.length,before);assert.match(statuses.at(-1),/窗口正在处理/);
});

test('result separates static source from runtime action admission history',()=>{const {world}=environment(),box=new Element();world.AA.renderResult(box,{action_admission_compilations:[{id:'admission.q',ability_id:'q',status:'ready',model_ref:{id:'model.q',revision:1},issues:[],profile:{compiled_values:{cooldown:{duration:20,charges:1,start:'action_start',recovery:'parallel'},energy:{amount:80,delivery:'action_start'}},declared_not_verified:[]}}],
  rotation_check:{action_admission_execution:{reported_actions:2,committed_actions:1,rejected_actions:1,not_committed_actions:0,not_verified_actions:0},event_log:[{time:0,character:'a',action_id:'cast',action_admission:{status:'committed',binding_id:'admission.q'}}]}},{characters:[{id:'a'}]});
  assert.match(box.textContent,/静态动作来源与运行时接纳/);assert.match(box.textContent,/接纳 1/);assert.match(box.textContent,/完整动作接纳父记录/);assert.equal(all(box).filter(node=>node.tagName==='input').length,0);
});
