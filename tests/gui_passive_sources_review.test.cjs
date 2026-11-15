const GUI_TEST_RUNTIME = require('./helpers/gui_runtime.cjs');
const Python = require('./helpers/python_command.cjs');
const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');
const helpers={module:{exports:{}},require,__dirname,console};
vm.runInNewContext(fs.readFileSync(path.join(__dirname,'gui_hit_parameters_review.test.cjs'),'utf8').split('\ntest(')[0]+'\nmodule.exports={setup,press,control,Element};',helpers);
const {setup:base,press,control,Element}=helpers.module.exports;
const clone=x=>JSON.parse(JSON.stringify(x)),all=n=>[n,...n.children.flatMap(all)];
// Consume the real public caps and immutable query fixture; no database connection.
const real=JSON.parse(Python.execFileSync(['-X','utf8','-c',[
  'import json',
  'from pathlib import Path',
  'from genshin_app.equipment.service import plan_capabilities',
  'from genshin_engine.runtime_passive_benefit_compiler import compile_passive_benefit_source',
  'caps=plan_capabilities()["passive_source_contract"]',
  'q=json.loads(Path("tests/fixtures/runtime_passive_kazuha_current.json").read_text("utf8"))',
  's=dict(caps["default_scenario"])',
  's.update(target_id="enemy",origin_target_range="within_source_range",recipient_range="all_party_within_source_range",initial_source_effects="none",recipient_bonus_policy="not_included",stat_basis="fixed_panel_nonextra_em_equals_total_em",event_order_policy="declared_atomic_hit_order",status="assumed",source="独立GUI工程声明")',
  'c=compile_passive_benefit_source(q,owner="a",source_id="passive.a",scenario=s)',
  'print(json.dumps(dict(caps=caps,query=q,compiled=c,scenario=s)))',
].join(';')],{cwd:path.join(__dirname,'..'),encoding:'utf8',maxBuffer:1024*1024}));

function setup(){
  const e=base(),w=e.world;
  w.PL.state.caps.passive_source_contract=clone(real.caps);
  w.PL.state.draft=clone(w.PL.state.plan);
  const plan=w.PL.state.draft;
  plan.equipment_inputs.a={mode:'panel',panel_policy:'static_included',character_ref:{db_character_id:10000047,version_line:'release'},
    progression:{ascension:6,status:'assumed',source:'独立GUI声明'},weapon:null,artifact_sets:[],source_refs:[],data_version:null};
  plan.mechanics_model.elemental_model={target:{id:'enemy'}};
  w.EQ.characterId='a';
  GUI_TEST_RUNTIME.loadWebFile(w, path.join(__dirname,'../src/genshin_app/gui/web/passive-sources.js'));
  const calls=[];
  w.callApi=async(name,value)=>{calls.push({name,value:clone(value)});return name==='query_runtime'?{code:0,data:clone(real.query)}:
    {code:0,data:{state:'pending',plan:{...clone(value),mechanics_model:{untrusted:true}},passive_compilations:[]}};};
  return {...e,plan,calls};
}

test('independent passive GUI consumes real candidate and caps shapes without auto-selection',async()=>{
  const e=setup();e.world.PB.edit('a');await press(e.dialog(),'读取角色固有天赋候选');
  const choices=control(e.dialog(),'采用的固有天赋');
  assert.equal(choices.value,'');assert.equal(e.plan.passive_sources,undefined);
  const candidate=real.query.candidates.find(c=>c.proud_skill_id===472201);
  assert.ok(choices.children.some(o=>o.value==='472201'&&o.textContent.includes(candidate.name.text)
    &&o.textContent.includes(String(candidate.threshold.required_ascension))));
  assert.deepEqual(e.calls[0].value,{action:'passive_source_profile',character_id:10000047,version_line:'release'});
});

test('independent actual source preview preserves description and distinct snapshot semantics',()=>{
  const e=setup(),box=new Element(),compiled=clone(real.compiled);
  assert.equal(compiled.status,'ready');
  e.world.PB.recordView(box,{...compiled,status:'pending',source:clone(real.query),unlock:clone(real.query.unlock),
    stat_basis:{panel_value:900},rules:[],preview_rules:compiled.rules});
  assert.ok(box.textContent.includes(real.query.descriptions.name.text));
  assert.match(box.textContent,/36\.00%/);assert.match(box.textContent,/8 秒/);
  assert.match(box.textContent,/自身快照/);assert.match(box.textContent,/未交付的预览/);
});

test('independent obsolete discovery after database epoch change never installs candidate controls',async()=>{
  const e=setup();e.world.PB.edit('a');let finish;
  e.world.callApi=()=>new Promise(resolve=>finish=resolve);
  const pending=press(e.dialog(),'读取角色固有天赋候选');e.PL.state.sourceEpoch++;
  finish({code:0,data:clone(real.query)});await pending;
  assert.ok(!all(e.dialog()).some(n=>n.attributes['aria-label']==='采用的固有天赋'));
  assert.equal(e.plan.passive_sources,undefined);
});

for(const missingGear of [false,true])test(`independent missing identity permits atomic passive removal without rebinding (missingGear=${missingGear})`,async()=>{
  const e=setup();e.plan.passive_sources=[{id:'passive.a',actor:'a',proud_skill_id:472201,constellation:0,
    scenario:clone(real.scenario),accepted_fingerprint:'a'.repeat(64)}];
  const model=clone(e.plan.mechanics_model),progression=clone(e.plan.equipment_inputs.a.progression);
  const basis=new Element();e.world.EQ.renderPanel(basis);
  const ref=control(basis,'数据库角色（核对技能来源，保留给定面板）');ref.value='';ref.emit('change');
  assert.equal(e.plan.equipment_inputs.a.character_ref,null);
  if(missingGear)delete e.plan.equipment_inputs.a;
  const box=new Element();e.world.PB.render(box);
  const accessible=all(box).find(n=>n.tagName==='button'&&n.textContent==='⋯');
  assert.ok(accessible,'Existing source must remain removable after its database identity is cleared');
  await accessible.emit('click');
  await press(e.dialog(),'读取角色固有天赋候选');
  assert.equal(e.calls.length,0,'No database query may invent an identity for the detached source');
  const before=clone(e.plan),ok=e.world.callApi;
  e.world.callApi=async()=>({code:1,message:'独立删除失败'});
  await press(e.dialog(),'移除此固有来源');
  assert.deepEqual(clone(e.plan),before,'A rejected removal must not partially mutate native inputs');
  e.world.callApi=ok;await press(e.dialog(),'移除此固有来源');
  assert.deepEqual(clone(e.plan.passive_sources),[]);
  assert.deepEqual(clone(e.plan.mechanics_model),model);
  if(missingGear)assert.equal(e.plan.equipment_inputs.a,undefined);
  else{
    assert.deepEqual(clone(e.plan.equipment_inputs.a.progression),progression);
    assert.equal(e.plan.equipment_inputs.a.character_ref,null);
  }
});

test('passive source cards use one contextual command and relocate by stable actor and id',()=>{
  const e=setup(),opened=[],calls=[],statuses=[];e.world.AppContextMenu={forDocument:()=>({open:config=>{opened.push(config);return true;},bind(){}})};
  e.world.setStatus=message=>statuses.push(message);const first={id:'first',actor:'a',scenario:clone(real.scenario)},second={id:'second',actor:'a',scenario:clone(real.scenario)};
  e.plan.passive_sources=[first,second];e.world.PB.edit=(actor,id)=>calls.push([actor,id]);const box=new Element();e.world.PB.render(box);
  assert.equal(all(box).some(node=>node.tagName==='button'&&node.textContent==='编辑固有天赋来源'),false);
  const more=all(box).filter(node=>node.tagName==='button'&&node.textContent==='⋯');assert.equal(more.length,2);more[0].emit('click');
  e.plan.passive_sources.reverse();opened.at(-1).items.find(item=>item.id==='edit').run();assert.deepEqual(calls,[['a','first']]);
  e.plan.passive_sources=e.plan.passive_sources.filter(row=>row.id!=='first');opened.at(-1).items.find(item=>item.id==='edit').run();
  assert.deepEqual(calls,[['a','first']]);assert.match(statuses.at(-1),/已变化或移除/);
});
