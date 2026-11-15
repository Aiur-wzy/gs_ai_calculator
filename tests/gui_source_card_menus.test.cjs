const GUI_TEST_RUNTIME = require('./helpers/gui_runtime.cjs');
const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');
const helpers={module:{exports:{}},require,__dirname,console};
const harness=fs.readFileSync(path.join(__dirname,'gui_equipment.test.cjs'),'utf8').split('\ntest(')[0];
vm.runInNewContext(harness+'\nmodule.exports={setup,Element};',helpers);
const {setup,Element}=helpers.module.exports;
const all=node=>[node,...node.children.flatMap(all)];
const buttons=node=>all(node).filter(child=>child.tagName==='button');
const rows=node=>all(node).filter(child=>String(child.className||'').includes('rc-definition-row'));
const event=(target,extra={})=>({target,key:'',shiftKey:false,preventDefault(){this.prevented=true;},stopPropagation(){this.stopped=true;},...extra});

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

function sourceEnv(file){
  const env=setup(),menu=installMenu(env.world),statuses=[];
  env.world.setStatus=value=>statuses.push(String(value));
  env.world.AE={profiles:plan=>plan?.action_event_profiles||[],name:profile=>profile.name||profile.id};
  GUI_TEST_RUNTIME.loadWebFile(env.world,path.join(__dirname,`../src/genshin_app/gui/web/${file}.js`),{filename:file});
  env.PL.state.plan.plan_version=3;
  return {...env,menu,statuses};
}

test('mechanism source cards expose editing through pointer menu and resolve the current object after reorder',()=>{
  const env=sourceEnv('mechanism-sources'),plan=env.PL.state.plan;
  env.PL.state.caps.mechanism_source_contract={limits:{max_sources:4}};
  const first={id:'first',actor:'c1'},second={id:'second',actor:'c1'};plan.mechanism_sources=[first,second];
  const calls=[];env.world.MS.edit=index=>calls.push(index);const box=new Element();env.world.MS.render(box);const rendered=rows(box);
  assert.equal(rendered.length,2);assert.equal(rendered[0].tabIndex,0);assert.deepEqual(buttons(rendered[0]).map(node=>node.textContent),['⋯']);
  rendered[0].listeners.contextmenu(event(rendered[0]));assert.equal(env.menu.current.items[0].label,'编辑通用机制来源');
  plan.mechanism_sources.reverse();env.menu.current.items[0].run();assert.deepEqual(calls,[1]);
  plan.mechanism_sources.splice(plan.mechanism_sources.indexOf(first),1);env.menu.current.items[0].run();assert.match(env.statuses.at(-1),/已变化/);assert.deepEqual(calls,[1]);
});

test('energy source cards support Shift+F10 without retaining a stale array index',()=>{
  const env=sourceEnv('energy-generation'),plan=env.PL.state.plan;
  env.PL.state.caps.energy_generation_contract={};
  const first={id:'first',action_event_profile_id:'profile'},second={id:'second',action_event_profile_id:'profile'};
  plan.action_event_profiles=[{id:'profile',name:'战技命中'}];plan.energy_generation_sources=[first,second];
  const calls=[];env.world.EG.edit=index=>calls.push(index);const box=new Element();env.world.EG.render(box);const rendered=rows(box);
  assert.equal(rendered.length,2);assert.deepEqual(buttons(rendered[0]).map(node=>node.textContent),['⋯']);
  rendered[0].listeners.keydown(event(rendered[0],{key:'F10',shiftKey:true}));plan.energy_generation_sources.reverse();
  env.menu.current.items.find(item=>item.label==='编辑产球来源').run();assert.deepEqual(calls,[1]);
  plan.energy_generation_sources=[{...first}];env.menu.current.items[0].run();assert.match(env.statuses.at(-1),/已变化/);assert.deepEqual(calls,[1]);
});

test('limited-hit source cards retain add as a primary action and open editing from the ellipsis menu',()=>{
  const env=sourceEnv('limited-hit-benefit'),plan=env.PL.state.plan;
  env.PL.state.caps.limited_hit_benefit_contract={limits:{max_sources:3}};
  const first={id:'first',action_event_profile_id:'profile'},second={id:'second',action_event_profile_id:'profile'};
  plan.action_event_profiles=[{id:'profile',name:'战技命中'}];plan.limited_hit_benefit_sources=[first,second];
  const calls=[];env.world.LHB.edit=index=>calls.push(index);const box=new Element();env.world.LHB.render(box);const rendered=rows(box);
  assert.equal(rendered.length,2);assert.equal(buttons(box).some(node=>node.textContent==='添加有限次数收益来源'),true);
  assert.deepEqual(buttons(rendered[0]).map(node=>node.textContent),['⋯']);buttons(rendered[0])[0].emit('click');
  assert.equal(env.menu.current.items[0].label,'编辑有限次数收益来源');plan.limited_hit_benefit_sources.reverse();env.menu.current.items[0].run();assert.deepEqual(calls,[1]);
  plan.limited_hit_benefit_sources=[];env.menu.current.items[0].run();assert.match(env.statuses.at(-1),/已变化/);assert.deepEqual(calls,[1]);
});
