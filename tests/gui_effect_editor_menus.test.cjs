const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');
const helpers={module:{exports:{}},require,__dirname,console};
const harness=fs.readFileSync(path.join(__dirname,'gui_equipment.test.cjs'),'utf8').split('\ntest(')[0];
vm.runInNewContext(harness+'\nmodule.exports={setup,Element};',helpers);
const {setup,Element}=helpers.module.exports;
const all=node=>[node,...node.children.flatMap(all)];
const buttons=node=>all(node).filter(child=>child.tagName==='button');
const event=(target,extra={})=>({target,key:'',shiftKey:false,preventDefault(){this.prevented=true;},stopPropagation(){this.stopped=true;},...extra});
const status=env=>env.nodes.get('#status-bar')?.textContent||'';
const menuItems=node=>typeof node.contextMenuConfig?.items==='function'?node.contextMenuConfig.items():node.contextMenuConfig?.items||[];

function installMenu(world){
  const state={opened:[]},menu={
    open(config){state.opened.push(config);state.current=config;return true;},
    bind(target,config){
      target.contextMenuConfig=config;
      const show=ev=>{ev.preventDefault?.();ev.stopPropagation?.();return menu.open({...config,target,trigger:target,returnFocus:target,items:typeof config.items==='function'?config.items():config.items});};
      target.addEventListener('contextmenu',show);
      target.addEventListener('keydown',ev=>{if(ev.key==='ContextMenu'||ev.key==='F10'&&ev.shiftKey===true||ev.keyCode===93)show(ev);});
      return()=>{};
    },
  };
  world.AppContextMenu={forDocument:()=>menu};return state;
}

const binding=(id,kind='artifact_set')=>({binding_id:id,provider_character_id:'c1',owner:{kind,...(kind==='artifact_set'?{set_id:15000,required_count:4,affix_id:2150001}:{})},
  source_refs:[{kind:'manual'}],source_fingerprint:null,coverage:{state:'unreviewed',reviewed_by:null,source_fingerprint:null,note:null},parts:[]});
const condition=(id,intervals=[])=>({condition_id:id,description:`条件${id}`,state:'pending',scope:intervals.length?'intervals':'always',intervals,evidence:[{kind:'manual',note:'依据'}]});
const part=(id,conditions=[])=>({part_id:id,label:`分项${id}`,state:'excluded',rule:{kind:'constant',value:0},delivery:{kind:'panel'},conditions,
  dependencies:{mode:'source_only',context_fingerprint:null,note:'依据'},reason:'已明确排除'});
const activate=env=>{env.PL.state.draft=env.PL.state.plan;return env.PL.state.draft;};

test('effect binding card moves source switches and deletion into the shared menu with fresh object lookup',()=>{
  const env=setup(),menu=installMenu(env.world),plan=activate(env);plan.plan_version=3;plan.manual_effect_dependencies=[];
  const first=binding('first'),second=binding('second');plan.effect_bindings=[first,second];env.PL.state.caps.upstream_contract={default_scenario:{profile_id:'test'}};env.EQ.render=()=>{};
  const box=new Element();env.EF.render(box);const cards=all(box).filter(node=>node.contextMenuConfig?.label?.startsWith('效果规则'));
  assert.equal(cards.length,2);assert.equal(cards[0].tabIndex,0);
  for(const label of ['尝试上游自动装配','移除这份规则'])assert.equal(buttons(cards[0]).some(node=>node.textContent===label),false);
  assert.equal(buttons(cards[0]).some(node=>node.textContent==='添加效果分项'),true);
  cards[0].listeners.contextmenu(event(cards[0]));assert.equal(menu.current.items.some(item=>item.label==='尝试上游自动装配'),true);
  plan.effect_bindings.reverse();menu.current.items.find(item=>item.label==='移除这份规则').run();assert.deepEqual(plan.effect_bindings.map(row=>row.binding_id),['second']);
  menu.current.items.find(item=>item.label==='移除这份规则').run();assert.match(status(env),/已变化/);
  cards[1].listeners.keydown(event(cards[1],{key:'F10',shiftKey:true}));assert.equal(menu.current.label.includes('操作'),true);
  buttons(cards[1]).find(node=>node.textContent==='⋯').emit('click');assert.ok(menu.opened.length>=3);
});

test('upstream source switch returns to manual only through the menu and rejects a replaced binding',()=>{
  const env=setup(),menu=installMenu(env.world),plan=activate(env);plan.plan_version=3;plan.manual_effect_dependencies=[];env.EQ.render=()=>{};
  const row=binding('upstream');plan.effect_bindings=[row];env.PL.state.caps.upstream_contract={default_scenario:{profile_id:'test',static_policy:'included'}};
  env.EF.startUpstream(row);assert.ok(row.upstream_recipe);let box=new Element();env.EF.render(box);
  const card=all(box).find(node=>node.contextMenuConfig?.label?.startsWith('效果规则'));assert.ok(card);
  assert.equal(buttons(card).some(node=>node.textContent==='返回手工处理'),false);card.listeners.contextmenu(event(card));
  assert.equal(menu.current.items.some(item=>item.label==='返回手工处理'),true);const command=menu.current.items.find(item=>item.label==='返回手工处理');
  plan.effect_bindings=[JSON.parse(JSON.stringify(row))];command.run();assert.match(status(env),/已变化/);assert.ok(plan.effect_bindings[0].upstream_recipe);
});

test('nested part, condition and interval menus resolve object identity after reordering and refuse stale commands',()=>{
  const env=setup(),menu=installMenu(env.world),plan=activate(env);plan.plan_version=3;env.EQ.render=()=>{};
  const intervalA={start:0,end:1},intervalB={start:2,end:3},conditionA=condition('a',[intervalA,intervalB]),conditionB=condition('b');
  const partA=part('a',[conditionA,conditionB]),partB=part('b'),owner=binding('owner');owner.parts=[partA,partB];plan.effect_bindings=[owner];
  const box=new Element();env.EF.renderPart(box,owner,partA,0);const targets=all(box).filter(node=>node.contextMenuConfig);
  const partCard=targets.find(node=>menuItems(node).some(item=>item.label==='删除分项'));
  const conditionCard=targets.find(node=>menuItems(node).some(item=>item.label==='删除条件'));
  const intervalCard=targets.find(node=>menuItems(node).some(item=>item.label==='删除区间'));
  assert.ok(partCard&&conditionCard&&intervalCard);for(const label of ['删除分项','删除条件','删除区间'])assert.equal(buttons(box).some(node=>node.textContent===label),false);
  intervalCard.listeners.contextmenu(event(intervalCard));conditionA.intervals.reverse();const intervalRemove=menu.current.items.find(item=>item.label==='删除区间');intervalRemove.run();
  assert.deepEqual(conditionA.intervals,[intervalB]);intervalRemove.run();assert.match(status(env),/已变化/);
  conditionCard.listeners.keydown(event(conditionCard,{key:'F10',shiftKey:true}));partA.conditions.reverse();menu.current.items.find(item=>item.label==='删除条件').run();
  assert.deepEqual(partA.conditions,[conditionB]);partCard.listeners.contextmenu(event(partCard));owner.parts.reverse();menu.current.items.find(item=>item.label==='删除分项').run();
  assert.deepEqual(owner.parts,[partB]);
});

test('tuning cards retain preview confirmation and move only removal into a fresh menu command',()=>{
  const env=setup(),menu=installMenu(env.world),plan=activate(env);env.EQ.render=()=>{};plan.manual_template.buffs=[];
  const first={id:'first',effect_ref:{buff_id:'',effect_id:''},mode:'adjust',include_hit_ids:[],exclude_hit_ids:[],reason:'',state:'pending',basis_fingerprint:null};
  const second={...JSON.parse(JSON.stringify(first)),id:'second'};plan.rotation_tuning={buff_hit_matches:[first,second]};
  const box=new Element();env.EF.renderTuning(box);const cards=all(box).filter(node=>node.contextMenuConfig?.label?.startsWith('轴例外微调'));
  assert.equal(cards.length,2);assert.equal(buttons(cards[0]).some(node=>node.textContent==='确认本次预览匹配'),true);
  assert.equal(buttons(cards[0]).some(node=>node.textContent==='撤销此微调'),false);cards[0].listeners.contextmenu(event(cards[0]));
  plan.rotation_tuning.buff_hit_matches.reverse();const remove=menu.current.items.find(item=>item.label==='撤销此微调');remove.run();
  assert.deepEqual(plan.rotation_tuning.buff_hit_matches.map(row=>row.id),['second']);remove.run();assert.match(status(env),/已变化/);
});
