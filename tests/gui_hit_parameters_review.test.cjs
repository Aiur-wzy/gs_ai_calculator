const GUI_TEST_RUNTIME = require('./helpers/gui_runtime.cjs');
const test=require('node:test'), assert=require('node:assert/strict'), fs=require('node:fs'), path=require('node:path'), vm=require('node:vm');
// Independent state-transition oracles. Use the shared fixture, never author test assertions.
const clone=value=>JSON.parse(JSON.stringify(value));
class Element {
  constructor(tag='div'){this.tagName=tag;this.children=[];this.attributes={};this.listeners={};this.style={};this.dataset={};this.value='';this.clientWidth=1000;this.classList={add(){},remove(){},toggle(){}};}
  append(...nodes){this.children.push(...nodes);} appendChild(node){this.append(node);return node;}
  insertRow(){return this.appendChild(new Element('tr'));} insertCell(){return this.appendChild(new Element('td'));}
  set innerHTML(v){this.children=[];this.text=v;} set textContent(v){this.children=[];this.text=String(v);}
  get textContent(){return(this.text||'')+this.children.map(node=>node.textContent).join('');}
  setAttribute(key,value){this.attributes[key]=value;} addEventListener(key,value){this.listeners[key]=value;} removeEventListener(){}
  emit(key){return this.listeners[key]?.({target:this,preventDefault(){}});} get childElementCount(){return this.children.length;}
  showModal(){} close(){} remove(){}
}
const all=node=>[node,...node.children.flatMap(all)];
const control=(node,label)=>{const value=all(node).find(n=>n.attributes['aria-label']===label);assert.ok(value,`Missing ${label}`);return value;};
const press=(node,label)=>{const button=all(node).find(n=>n.tagName==='button'&&n.textContent===label);assert.ok(button,`Missing ${label}`);return button.emit('click');};
function setup(){
  const fixture=JSON.parse(fs.readFileSync(path.join(__dirname,'fixtures/gui_equipment_queries.json'),'utf8'));
  const nodes=new Map(),body=new Element('body'),world={console,Date,Math,confirm:()=>true,prompt:()=>null,addEventListener(){}};
  const activeTab=new Element('button');activeTab.dataset.tab='rotation';nodes.set('.tab.active',activeTab);
  world.window=world;world.document={body,createElement:tag=>new Element(tag),createElementNS:(_,tag)=>new Element(tag),
    querySelector:selector=>{if(selector.startsWith('#')){const found=[...nodes.values(),body].flatMap(all).find(n=>n.id===selector.slice(1));if(found)return found;}
      if(!nodes.has(selector))nodes.set(selector,new Element());return nodes.get(selector);},querySelectorAll:()=>[],getElementById:id=>[...nodes.values(),body].flatMap(all).find(n=>n.id===id)};
  world.pywebview={api:Object.fromEntries(Object.entries(fixture).map(([key,value])=>[key,async()=>clone(value)]))};vm.createContext(world);
  for(const file of ['display','rotation','panel','chars','timeline-view','timeline','result','format','data','ui','plans','equipment-data','equipment','effect-editor','checks','mechanics','app'])
    GUI_TEST_RUNTIME.loadWebFile(world, path.join(__dirname,`../src/genshin_app/gui/web/${file}.js`), {filename:file});
  const display=JSON.parse(fs.readFileSync(path.join(__dirname,'fixtures/gui_display.json'),'utf8'));world.DS.configure(display.display);world.DS.catalogs.set('release',display.catalog);
  world.RT.state.enums={elements:['physical','pyro'],damage_systems:['classic','transformative'],scaling_stats:['atk','hp','def','em'],buff_zones:['dmg_bonus'],buff_scopes:['team'],reaction_types:{classic:['amplify']},engine_v2:fixture.plan_capabilities.data.engine_v2};
  world.PL.state.caps=fixture.plan_capabilities.data;world.ED.caps=fixture.db_capabilities.data;
  world.RS.renderSummary=world.RS.renderHits=world.RS.renderCoverage=()=>new Element();world.RT.renderTimeline=()=>{};world.RT.refreshPlans=async()=>{};
  const plan=JSON.parse(fs.readFileSync(path.join(__dirname,'../examples/declared-mechanics-plan.json'),'utf8'));
  plan.manual_template.hits[0].scaling_stat='hp';plan.equipment_inputs.a={character_ref:{db_character_id:10000054,version_line:'release'}};
  world.PL.replace(plan,'review.json','review-sha',true);
  const dialog=()=>body.children.at(-1),field=(label,value)=>{const n=control(dialog(),label);n.value=String(value);n.emit('change');};
  const response=proposed=>({code:0,data:{state:'pending',plan:clone(proposed),missing_inputs:[],hit_parameter_compilations:(proposed.hit_parameter_sources||[]).map(source=>({
    hit_id:source.hit_id,status:'pending',interpretation_status:source.status,scaling_stat:source.scaling_stat,
    scalar_projection:{status:'ready',value:.125},level:{effective_level:10},
    parameter_descriptions:{status:'ready',matched:[{text:`独立审核参数 ${source.param_index}`}],unresolved:[]},
    required_confirmation:{expected_snapshot_id:'snapshot',expected_node_sha256:`node-${source.param_index}`}}))}});
  world.pywebview.api.compose_equipment_plan=async proposed=>response(proposed);
  const open=()=>{world.MC.editHitParameterSource();field('作为单属性直接伤害倍率的依据','审核用途依据');};
  return {world,PL:world.PL,RT:world.RT,MC:world.MC,dialog,field,response,open};
}
async function bind(env,status='pending'){env.open();env.field('倍率用途核实状态',status);await press(env.dialog(),'应用倍率来源绑定');}
function deferred(env){let finish,submitted;env.world.pywebview.api.compose_equipment_plan=plan=>{submitted=clone(plan);return new Promise(resolve=>finish=resolve);};return()=>finish(env.response(submitted));}

test('independent: out-of-order previews retain newest local selection and newest source confirmation',async()=>{
  const env=setup();env.open();const calls=[];env.world.pywebview.api.compose_equipment_plan=plan=>new Promise(resolve=>calls.push({plan:clone(plan),resolve}));
  const first=press(env.dialog(),'读取并预览当前技能参数');env.field('参数序号（从0开始）',1);
  const second=press(env.dialog(),'读取并预览当前技能参数');calls[1].resolve(env.response(calls[1].plan));await second;
  calls[0].resolve(env.response(calls[0].plan));await first;
  assert.match(env.dialog().textContent,/独立审核参数 1/);assert.doesNotMatch(env.dialog().textContent,/独立审核参数 0/);
  env.world.pywebview.api.compose_equipment_plan=async plan=>env.response(plan);
  await press(env.dialog(),'确认所示数值来源');await press(env.dialog(),'应用倍率来源绑定');
  assert.equal(env.PL.state.plan.hit_parameter_sources[0].expected_node_sha256,'node-1');
});

test('independent: failed unbind is atomic; explicit zero unbind preserves all unrelated native input',async()=>{
  const env=setup();await bind(env);const plan=env.PL.state.plan,hit=plan.manual_template.hits[0];
  plan.hit_parameter_sources.push({...clone(plan.hit_parameter_sources[0]),hit_id:'unrelated'});
  const input=JSON.parse(env.PL.currentInputJson()),source=clone(plan.hit_parameter_sources[1]);env.MC.editHitParameterSource(0);
  env.field('解除绑定后采用的手填倍率（不自动恢复）',0);env.world.pywebview.api.compose_equipment_plan=async()=>({code:1,message:'deliberate failed validation'});
  await press(env.dialog(),'解除来源并采用手填倍率');assert.deepEqual(JSON.parse(env.PL.currentInputJson()),input);assert.equal(hit.multiplier,undefined);
  env.world.pywebview.api.compose_equipment_plan=async plan=>env.response(plan);await press(env.dialog(),'解除来源并采用手填倍率');
  const expected=clone(input);expected.hit_parameter_sources=[source];expected.manual_template.hits[0].multiplier=0;
  const actual=JSON.parse(env.PL.currentInputJson());delete expected.revision;delete actual.revision;assert.deepEqual(actual,expected);
});

for(const phase of ['confirm','remove'])test(`independent: plan replacement rejects old ${phase} completion without touching either plan`,async()=>{
  const env=setup();await bind(env,'assumed');env.MC.editHitParameterSource(0);
  if(phase==='confirm')await press(env.dialog(),'读取并预览当前技能参数');else env.field('解除绑定后采用的手填倍率（不自动恢复）',3);
  const old=env.PL.state.plan,oldInput=JSON.stringify(old),finish=deferred(env);
  const pending=press(env.dialog(),phase==='confirm'?'确认所示数值来源':'解除来源并采用手填倍率');
  const replacement=clone(old);replacement.manual_template.meta.name='replacement';env.PL.replace(replacement,'replacement.json','replacement-sha',true);
  const newInput=JSON.stringify(replacement);finish();await pending;
  assert.equal(JSON.stringify(old),oldInput);assert.equal(JSON.stringify(replacement),newInput);
});

for(const status of ['pending','assumed'])test(`independent: editing and confirming existing ${status} interpretation never upgrades it`,async()=>{
  const env=setup();await bind(env,status);env.MC.editHitParameterSource(0);await press(env.dialog(),'读取并预览当前技能参数');
  await press(env.dialog(),'确认所示数值来源');await press(env.dialog(),'应用倍率来源绑定');
  assert.equal(env.PL.state.plan.hit_parameter_sources[0].status,status);assert.equal(env.PL.state.plan.manual_template.hits[0].multiplier,undefined);
});

test('independent: discard unbind draft and server-derived mutations never leak into native input',async()=>{
  const env=setup();await bind(env);let before=env.PL.currentInputJson();env.MC.editHitParameterSource(0);env.field('解除绑定后采用的手填倍率（不自动恢复）',999);
  press(env.dialog(),'放弃并关闭');assert.equal(env.PL.currentInputJson(),before);
  env.MC.editHitParameterSource(0);env.world.pywebview.api.compose_equipment_plan=async proposed=>{const reply=env.response(proposed);
    reply.data.plan.manual_template.hits[0].multiplier=98765;reply.data.plan.manual_template.hits[0].t=999;return reply;};
  await press(env.dialog(),'应用倍率来源绑定');const actual=JSON.parse(env.PL.currentInputJson()),expected=JSON.parse(before);
  delete actual.revision;delete expected.revision;assert.deepEqual(actual,expected);
});

test('independent: repeated bound editor renders cannot materialize a cached multiplier',async()=>{
  const env=setup();await bind(env);const plan=env.PL.state.plan;
  plan.resolved_template=clone(plan.manual_template);plan.resolved_template.hits[0].multiplier=1;plan.resolution.state='ready';
  const before=env.PL.currentInputJson();for(let i=0;i<3;i++){const box=new Element();env.world.renderHitPanel(box,0);
    assert.equal(control(box,'倍率').value,'');assert.equal(control(box,'倍率').disabled,true);
    for(const [label,value] of [['倍率','1'],['伤害体系','classic'],['倍率依据','atk'],['角色','b']]){const n=control(box,label);n.value=value;n.emit('change');}}
  assert.equal(env.PL.currentInputJson(),before);
});

for(const target of ['different_actor_with_ref','different_actor_without_ref','same_actor'])test(`independent: hit selection resets only unsafe cross-actor source declarations (${target})`,async()=>{
  const env=setup(),plan=env.PL.state.plan,sameActor=target==='same_actor',withRef=target==='different_actor_with_ref';
  plan.manual_template.characters.push({...clone(plan.manual_template.characters[0]),id:'b'});
  plan.manual_template.hits.push({...clone(plan.manual_template.hits[0]),id:'h2',character:sameActor?'a':'b',scaling_stat:'atk'});
  if(withRef)plan.equipment_inputs.b={character_ref:{db_character_id:10000002,version_line:'beta'}};
  env.RT.renderAll();const before=env.PL.currentInputJson();env.open();
  env.field('技能定位方式','skill_id');env.field('数据库技能编号',10545);env.field('技能组编号（多技能组角色须填写）',5401);
  env.field('参数序号（从0开始）',3);env.field('倍率用途核实状态','confirmed');
  await press(env.dialog(),'读取并预览当前技能参数');await press(env.dialog(),'确认所示数值来源');
  env.field('命中或连续命中原型','h2');assert.equal(env.PL.currentInputJson(),before);
  let proposed;env.world.pywebview.api.compose_equipment_plan=async value=>{proposed=clone(value);return env.response(value);};
  await press(env.dialog(),'读取并预览当前技能参数');const source=proposed.hit_parameter_sources[0];
  assert.equal(source.hit_id,'h2');assert.equal(source.scaling_stat,'atk');
  assert.equal(source.expected_snapshot_id,null);assert.equal(source.expected_node_sha256,null);
  assert.equal(proposed.manual_template.hits[0].multiplier,1);assert.equal(proposed.manual_template.hits[1].multiplier,undefined);
  if(sameActor){assert.deepEqual(source.selection,{character_id:10000054,constellation:0,talent_level:10,skill_id:10545,skill_depot_id:5401});
    assert.equal(source.param_index,3);assert.equal(source.status,'confirmed');assert.equal(source.source,'审核用途依据');assert.equal(source.version_line,'release');}
  else {assert.deepEqual(source.selection,{character_id:withRef?10000002:null,slot:'skill',constellation:0,talent_level:10});
    assert.equal(source.param_index,0);assert.equal(source.status,'pending');assert.equal(source.source,'');assert.equal(source.version_line,withRef?'beta':'release');
    assert.equal(control(env.dialog(),'数据库角色编号').value,withRef?10000002:'');}
});
