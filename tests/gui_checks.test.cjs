const GUI_TEST_RUNTIME = require('./helpers/gui_runtime.cjs');
const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');
class Element {
  constructor(tag='div'){this.tagName=tag;this.children=[];this.attributes={};this.listeners={};this._value='';this.style={};this.classList={toggle(){},add(){},remove(){}};}
  append(...nodes){for(const n of nodes){n.parent=this;this.children.push(n);}}appendChild(n){this.append(n);return n;}
  replaceChildren(...nodes){this.children=[];this.append(...nodes);}
  set innerHTML(v){this.children=[];this.text=v;}set textContent(v){this.children=[];this.text=String(v);}get textContent(){return(this.text||'')+this.children.map(n=>n.textContent).join('');}
  set value(v){this._value=String(v);}get value(){return this._value;}
  setAttribute(k,v){this.attributes[k]=v;}addEventListener(k,v){this.listeners[k]=v;}
  emit(k){return this.listeners[k]?.({target:this,preventDefault(){}});}showModal(){}close(){}remove(){if(this.parent)this.parent.children=this.parent.children.filter(n=>n!==this);}
}
const all=n=>[n,...n.children.flatMap(all)],clone=x=>JSON.parse(JSON.stringify(x));
const control=(node,label)=>all(node).find(n=>n.attributes['aria-label']===label);
const button=(node,label)=>all(node).find(n=>n.tagName==='button'&&n.textContent===label);
const edit=(node,label,value)=>{const input=control(node,label);assert.ok(input,`Missing ${label}`);input.value=value;input.emit('change');};
const toggle=(node,label,value)=>{const input=control(node,label);assert.ok(input,`Missing ${label}`);input.checked=value;input.emit('change');};
function setup(){
  const nodes=new Map(),body=new Element('body'),world={console,Date,Math,confirm:()=>true,prompt:()=>null};world.window=world;
  world.document={body,createElement:t=>new Element(t),createElementNS:(_,t)=>new Element(t)};
  world.$=s=>{if(!nodes.has(s))nodes.set(s,new Element());return nodes.get(s);};world.setStatus=v=>world.lastStatus=v;
  const sample=JSON.parse(fs.readFileSync(path.join(__dirname,'../examples/rotation-energy-check.json'),'utf8'));
  const plan={kind:'genshin-plan',plan_version:2,revision:0,manual_template:sample.template,rotation_model:sample.rotation_model};
  world.PL={state:{plan,draft:null,sourceEpoch:0},currentInputJson:()=>JSON.stringify(world.PL.state.plan),renderStatus(){},replace(p){world.PL.state.plan=p;}};
  world.RT={tpl:()=>world.PL.state.plan?.manual_template,syncRevision(){},charColor:()=>'blue'};
  world.DS={character:id=>id||'未知角色',prose:v=>v,label:(_,v)=>v,hit:h=>h.skill,
    raw:(title,v)=>{const n=new Element();n.textContent=title+JSON.stringify(v);return n;},textBlock:t=>{const n=new Element();n.textContent=t;return n;}};
  world.callApi=async()=>({code:0,data:{status:'passed'}});vm.createContext(world);
  for(const name of ['ui','checks'])GUI_TEST_RUNTIME.loadWebFile(world, path.join(__dirname,`../src/genshin_app/gui/web/${name}.js`));
  world.RC.caps={action_types:[{id:'action.elemental_skill',name_zh:'元素战技'},{id:'action.elemental_burst',name_zh:'元素爆发'}],event_types:['cooldown_change']};
  world.RC.shown=true; // These tests explicitly exercise the opened editor.
  return {world,RC:world.RC,PL:world.PL,body,nodes,model:plan.rotation_model,dialog:()=>body.children.at(-1)};
}

test('valid ability without optional rules is editable',()=>{
  const {RC,model,dialog,world}=setup();delete model.abilities[0].rules;
  RC.editAbility(0);assert.equal(world.AppDialogKernel.getActive(),dialog());button(dialog(),'应用修改').emit('click');
  assert.equal(model.abilities[0].id,'e');assert.equal(model.abilities[0].rules,undefined);
});

test('multi-stage cast sequence can be edited and is named explicitly',()=>{
  const {RC,model,dialog}=setup();RC.editAbility(0);
  toggle(dialog(),'此动作属于多段连携',true);edit(dialog(),'连携组','skill-chain');
  edit(dialog(),'当前段数','2');edit(dialog(),'总段数','3');edit(dialog(),'续段窗口（秒）','4');
  button(dialog(),'应用修改').emit('click');
  assert.deepEqual(clone(model.abilities[0].cast_sequence),{
    group:'skill-chain',stage:2,max_stages:3,continuation_window:4,cooldown_start:'sequence_start'});
  assert.match(RC.abilityName(model.abilities[0]),/连携第 2\/3 段/);
});

const reviewedSequence=()=>({contract:'cast-sequence-source-v1',status:'ready',profile_id:'reviewed-three-stage',
  value:{group:'reviewed-skill-chain',stage:1,max_stages:3,continuation_window:4,cooldown_start:'sequence_start'},
  stages:[
    {stage:1,skill_id:10161,ability_name:'FireBlade1',raw_cooldown_seconds:10,continuation_window:4},
    {stage:2,skill_id:10162,ability_name:'FireBlade2',raw_cooldown_seconds:2,continuation_window:4},
    {stage:3,skill_id:10163,ability_name:'FireBlade3',raw_cooldown_seconds:2,continuation_window:null}],
  resource_policy:{sequence_cooldown_owner_stage:1,sequence_cooldown_seconds:10,sequence_cooldown_charges:1,
    sequence_cooldown_recovery:'sequential',continuations_bypass_active_cooldown:true,
    continuation_window_is_latest_deadline_not_minimum_wait:true,raw_followup_cdtime_is_not_an_independent_cast_cooldown:true,
    raw_followup_cdtime_is_not_a_minimum_stage_delay:true,terminal_stage_restarts_cooldown:false},
  evidence:[{source_path:'Ability/reviewed.json',json_pointer:'/Switch1To2'}],review:{status:'reviewed'},
  content_fingerprint:'a'.repeat(64)});
const reviewedEnvelope=()=>({selection:{character_id:10000016,skill_id:10161},
  snapshot:{version_line:'release',snapshot_id:'b'.repeat(64),upstream_commit:'c'.repeat(40)},
  resource_fingerprint:'d'.repeat(64),cast_sequence:reviewedSequence()});

test('reviewed database sequence requires explicit stage choice and never treats raw cdTime as a wait',async()=>{
  const {RC,PL,model,dialog,world}=setup();PL.state.plan.equipment_inputs={a:{character_ref:{db_character_id:10000016,version_line:'release'}}};
  const calls=[],beforeActions=clone(model.actions);world.callApi=async(name,payload)=>{calls.push({name,payload:clone(payload)});
    return {code:0,data:reviewedEnvelope()};};
  RC.editAbility(0);await button(dialog(),'读取受审连携资料').emit('click');
  assert.deepEqual(calls,[{name:'query_runtime',payload:{action:'action_resource_profile',character_id:10000016,slot:'skill',version_line:'release'}}]);
  assert.match(dialog().textContent,/释放后 4 秒内可接下一段（可以立即接续）/);
  assert.match(dialog().textContent,/原始 cdTime 2 仅作来源证据，不是续段等待/);
  button(dialog(),'应用所选阶段到当前技能定义').emit('click');assert.match(world.lastStatus,/明确选择/);
  edit(dialog(),'应用的受审阶段','2');button(dialog(),'应用所选阶段到当前技能定义').emit('click');
  assert.match(world.lastStatus,/续段仍声明了普通冷却/);assert.equal(model.abilities[0].cast_sequence,undefined);
  toggle(dialog(),'此动作有技能冷却',false);button(dialog(),'应用所选阶段到当前技能定义').emit('click');
  button(dialog(),'应用修改').emit('click');
  const sequence=clone(model.abilities[0].cast_sequence);assert.deepEqual({...sequence,source_binding:undefined},
    {group:'reviewed-skill-chain',stage:2,max_stages:3,continuation_window:4,cooldown_start:'sequence_start',source_binding:undefined});
  assert.deepEqual(sequence.source_binding,{contract:'cast-sequence-source-binding-v1',profile_id:'reviewed-three-stage',
    character_id:10000016,selected_skill_id:10162,selected_stage:2,version_line:'release',snapshot_id:'b'.repeat(64),
    upstream_commit:'c'.repeat(40),content_fingerprint:'a'.repeat(64),resource_fingerprint:'d'.repeat(64),
    sequence_cooldown_seconds:10,sequence_cooldown_charges:1,sequence_cooldown_recovery:'sequential',verification_mode:'saved_metadata_only'});
  assert.equal(model.abilities[0].cooldown,null);assert.deepEqual(clone(model.actions),beforeActions);
});

test('reviewed first stage refuses a wrong cooldown instead of applying auditable metadata',async()=>{
  const {RC,PL,model,dialog,world}=setup();PL.state.plan.equipment_inputs={a:{character_ref:{db_character_id:10000016,version_line:'release'}}};
  world.callApi=async()=>({code:0,data:reviewedEnvelope()});RC.editAbility(0);
  await button(dialog(),'读取受审连携资料').emit('click');edit(dialog(),'应用的受审阶段','1');
  edit(dialog(),'冷却时间（秒）','8');button(dialog(),'应用所选阶段到当前技能定义').emit('click');
  assert.match(world.lastStatus,/应为 10 秒，当前为 8 秒/);assert.equal(model.abilities[0].cast_sequence,undefined);
  edit(dialog(),'冷却时间（秒）','10');button(dialog(),'应用所选阶段到当前技能定义').emit('click');button(dialog(),'应用修改').emit('click');
  assert.equal(model.abilities[0].cast_sequence.source_binding.sequence_cooldown_seconds,10);
  assert.equal(model.abilities[0].cooldown.duration,10);
});

test('reviewed sequence without an auditable cooldown policy is rejected',async()=>{
  const {RC,PL,dialog,world}=setup();PL.state.plan.equipment_inputs={a:{character_ref:{db_character_id:10000016,version_line:'release'}}};
  const envelope=reviewedEnvelope();delete envelope.cast_sequence.resource_policy.sequence_cooldown_seconds;
  world.callApi=async()=>({code:0,data:envelope});RC.editAbility(0);await button(dialog(),'读取受审连携资料').emit('click');
  assert.match(dialog().textContent,/基础冷却策略不完整/);assert.doesNotMatch(dialog().textContent,/应用的受审阶段/);
});

test('late reviewed sequence response cannot cross a database source epoch',async()=>{
  const {RC,PL,model,dialog,world}=setup();PL.state.plan.equipment_inputs={a:{character_ref:{db_character_id:10000016,version_line:'release'}}};
  let resolve;world.callApi=()=>new Promise(done=>{resolve=done;});RC.editAbility(0);
  const pending=button(dialog(),'读取受审连携资料').emit('click');PL.state.sourceEpoch++;
  resolve({code:0,data:reviewedEnvelope()});await pending;
  assert.equal(model.abilities[0].cast_sequence,undefined);assert.match(world.lastStatus,/旧连携资料响应已忽略/);
  assert.doesNotMatch(dialog().textContent,/应用的受审阶段/);
});

test('sequence database bridge refuses to guess a character identity',async()=>{
  const {RC,dialog,world}=setup();let calls=0;world.callApi=async()=>{calls++;return {code:0,data:{}};};RC.editAbility(0);
  await button(dialog(),'读取受审连携资料').emit('click');assert.equal(calls,0);
  assert.match(dialog().textContent,/没有明确的数据库 character_ref/);
});

test('removing the last cooldown owner clears its orphan initial state',()=>{
  const {RC,model,dialog}=setup();RC.editAbility(0);
  toggle(dialog(),'此动作有技能冷却',false);button(dialog(),'应用修改').emit('click');
  assert.equal(model.abilities[0].cooldown,null);assert.equal(model.initial_cooldowns.length,0);
});

test('shared cooldown initial state remains while another variant uses the group',()=>{
  const {RC,model,dialog}=setup();model.abilities.push({...clone(model.abilities[0]),id:'hold'});
  RC.editAbility(0);toggle(dialog(),'此动作有技能冷却',false);button(dialog(),'应用修改').emit('click');
  assert.equal(model.initial_cooldowns.length,1);assert.equal(model.initial_cooldowns[0].group,'e');
});

test('moving to a new cooldown group never silently copies old ready charges',()=>{
  const {RC,model,dialog}=setup();RC.editAbility(0);edit(dialog(),'共享冷却组','new_group');button(dialog(),'应用修改').emit('click');
  assert.equal(model.abilities[0].cooldown.group,'new_group');assert.equal(model.initial_cooldowns.length,0);
});

test('an explicitly declared new cooldown initial state is retained',()=>{
  const {RC,model,dialog}=setup();RC.editAbility(0);edit(dialog(),'共享冷却组','new_group');
  toggle(dialog(),'初始次数全部就绪',true);button(dialog(),'应用修改').emit('click');
  assert.equal(model.initial_cooldowns[0].group,'new_group');assert.deepEqual(clone(model.initial_cooldowns[0].ready_in),[]);
});

test('cooldown reset editor removes the inapplicable amount field',()=>{
  const {RC,model,dialog}=setup();RC.editEvent(undefined,'cooldown_change');edit(dialog(),'秒数或次数','10');
  button(dialog(),'应用修改').emit('click');const event=model.events.at(-1);
  assert.equal(event.operation,'reset');assert.equal('amount' in event,false);
});

test('changing cooldown reset to reduce preserves its numeric amount',()=>{
  const {RC,model,dialog}=setup();RC.editEvent(undefined,'cooldown_change');edit(dialog(),'操作','reduce');
  edit(dialog(),'秒数或次数','5');button(dialog(),'应用修改').emit('click');assert.equal(model.events.at(-1).amount,5);
});

test('new action and energy timestamps stay unknown until entered',()=>{
  const {RC,model,dialog}=setup();RC.editAction();button(dialog(),'应用修改').emit('click');
  assert.equal(model.actions.at(-1).start,null);assert.equal(model.actions.at(-1).occupation_end,null);
  RC.editEvent(undefined,'energy_direct');button(dialog(),'应用修改').emit('click');assert.equal(model.events.at(-1).t,null);
});

test('editing period updates the authoritative damage template at the same time',()=>{
  const {RC,model,nodes,PL}=setup();RC.render();edit(nodes.get('#rotation-checks'),'单轮时长（秒）','24');
  assert.equal(model.period,24);assert.equal(PL.state.plan.manual_template.loop_duration,24);
});

test('late check response from a different database source epoch is discarded',async()=>{
  const {RC,PL,world}=setup();let resolve;world.callApi=()=>new Promise(r=>resolve=r);
  const work=RC.run();PL.state.sourceEpoch++;resolve({code:0,data:{status:'passed'}});await work;
  assert.equal(RC.state.result,null);
});

test('changing source epoch hides an already completed check even with identical JSON input',async()=>{
  const {RC,PL,world,nodes}=setup();world.callApi=async()=>({code:0,data:{status:'passed'}});
  await RC.run();assert.match(nodes.get('#rotation-checks').textContent,/动作与能量检查：通过/);
  PL.state.sourceEpoch++;RC.render();assert.doesNotMatch(nodes.get('#rotation-checks').textContent,/动作与能量检查：通过/);
});

test('invalidation prevents an in-flight check from repopulating cleared results',async()=>{
  const {RC,world}=setup();let resolve;world.callApi=()=>new Promise(r=>resolve=r);
  const work=RC.run();RC.invalidate();resolve({code:0,data:{status:'passed'}});await work;assert.equal(RC.state.result,null);
});

test('editing energy checks never silently applies an equipment draft',()=>{
  const {RC,PL,body}=setup();PL.state.draft={changed:true};RC.editAction();assert.equal(body.children.length,0);
});

test('new model creation is refused without any party member',async()=>{
  const {RC,PL,world}=setup();PL.state.plan.manual_template.characters=[];let calls=0;world.callApi=async()=>{calls++;return{code:0};};
  await RC.create();assert.equal(calls,0);assert.match(world.lastStatus,/添加角色/);
});

test('switch track derives initial frontline and applicable first-cycle actions in explicit order',()=>{
  const {RC,PL,model}=setup();PL.state.plan.manual_template.characters.push({id:'b',stats:{}});
  model.abilities.push({id:'swap',character:'a',action_type_id:'action.switch'});
  model.actions.push({id:'late',ability_id:'swap',start:2,order:3,switch_to:'a',repeat:{mode:'each_cycle'}},
    {id:'first',ability_id:'swap',start:2,order:2,switch_to:'b',repeat:{mode:'once'}},
    {id:'future',ability_id:'swap',start:1,order:0,switch_to:'b',repeat:{mode:'cycles',cycles:[1]}},
    {id:'unknown',ability_id:'swap',start:null,order:0,switch_to:'b',repeat:{mode:'each_cycle'}});
  RC.syncSwitches();assert.deepEqual(clone(PL.state.plan.manual_template.switches),[
    {t:0,character:'a'},{t:2,character:'b'},{t:2,character:'a'}]);
});

test('declining a model switch-track replacement leaves both model and template unchanged',async()=>{
  const {RC,PL,model,world}=setup();PL.state.plan.manual_template.characters.push({id:'b',stats:{}});
  const proposed=clone(model);proposed.initial_frontline='b';const before=JSON.stringify(PL.state.plan);
  world.confirm=()=>false;assert.equal(await RC.setModel(proposed),false);assert.equal(JSON.stringify(PL.state.plan),before);
});

test('adding a redundant default t=0 marker never asks for a switch-track replacement',async()=>{
  const {RC,PL,model,world}=setup();let asked=0;world.confirm=()=>{asked++;return true;};
  assert.equal(await RC.setModel(model),true);assert.equal(asked,0);
  assert.deepEqual(clone(PL.state.plan.manual_template.switches),[{t:0,character:'a'}]);
});

test('explicit initial-frontline edits immediately synchronize the damage template',()=>{
  const {RC,PL,nodes}=setup();PL.state.plan.manual_template.characters.push({id:'b',stats:{}});
  RC.render();edit(nodes.get('#rotation-checks'),'初始前台','b');
  assert.deepEqual(clone(PL.state.plan.manual_template.switches),[{t:0,character:'b'}]);
});

test('deleting a switch action clears its corresponding damage-track marker',()=>{
  const {RC,PL,model}=setup();PL.state.plan.manual_template.characters.push({id:'b',stats:{}});
  model.abilities.push({id:'swap',character:'a',action_type_id:'action.switch'});
  model.actions=[{id:'s',ability_id:'swap',start:2,order:0,switch_to:'b',repeat:{mode:'each_cycle'}}];RC.syncSwitches();
  const list=RC.list('施放动作',model.actions,()=>'',()=>{});assert.equal(button(list,'⋯').textContent,'⋯');
  RC.definitionMenuItems('施放动作',model.actions,0,()=>{}).find(item=>item.id?.startsWith('remove-')).run();
  assert.deepEqual(clone(PL.state.plan.manual_template.switches),[{t:0,character:'a'}]);
});

test('declared reactions without elemental replay show a concrete verification warning',()=>{
  const {RC}=setup(),box=RC.renderResult({status:'passed',damage_interpretation:{message:'已完成声明伤害计算',
    reaction_verification:{status:'unverified',mode:'declared_not_simulated',declared_reaction_hit_count:7,
      required_follow_up:'逐攻击分量补齐元素、GU、计数标签与规则组后重放'}}},{});
  assert.match(box.textContent,/反应尚未由元素引擎复核/);
  assert.match(box.textContent,/7 个反应命中来自输入声明/);
  assert.match(box.textContent,/命中前元素底.*附着资格.*ICD 计数身份.*反应顺序/);
  assert.match(box.textContent,/逐攻击分量补齐元素、GU、计数标签与规则组后重放/);
});

test('cast sequence receipt separates continuation admission from base cooldown',()=>{
  const {RC}=setup(),box=RC.renderResult({status:'passed',event_log:[{type:'action',t:3.5,status:'passed',cast_sequence:{
    group:'skill-chain',stage:2,max_stages:3,admission:'continuation_window',next_stage:3,
    continuation_expires_at:7.5,base_cooldown:{group:'skill',running:true,ready_at:10}}}]},{});
  assert.match(box.textContent,/连携第 2\/3 段：通过续段窗口接纳/);
  assert.match(box.textContent,/下一段为第 3 段，须在 7.5 秒前开始/);
  assert.match(box.textContent,/首段普通冷却正在运行，10 秒恢复/);
  assert.match(box.textContent,/普通冷却不阻止有效续段/);
});

test('failed cast sequence receipt never claims continuation-window admission',()=>{
  const {RC}=setup(),box=RC.renderResult({status:'failed',event_log:[{type:'action',t:7.5,status:'failed',reason:'cast_sequence_unavailable',cast_sequence:{
    group:'skill-chain',stage:2,max_stages:3,admission:'continuation_window',expected_stage:2,continuation_expires_at:7.5,
    base_cooldown:{group:'skill',running:true,ready_at:10}}}]},{});
  assert.match(box.textContent,/连携第 2\/3 段：续段未接纳/);
  assert.doesNotMatch(box.textContent,/通过续段窗口接纳/);
});
