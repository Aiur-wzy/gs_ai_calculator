const GUI_TEST_RUNTIME = require('./helpers/gui_runtime.cjs');
const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');
const helpers={module:{exports:{}},require,__dirname,console};
const harness=fs.readFileSync(path.join(__dirname,'gui_checks.test.cjs'),'utf8').split('\ntest(')[0];
vm.runInNewContext(harness+'\nmodule.exports={setup,Element};',helpers);
const {setup,Element}=helpers.module.exports;
const all=node=>[node,...node.children.flatMap(all)];

function installMenu(world){
  const state={opened:[]},menu={
    open(config){state.opened.push(config);state.current=config;return true;},
    bind(target,config){
      const show=event=>{event.preventDefault?.();event.stopPropagation?.();return menu.open({...config,target,trigger:target,returnFocus:target,items:typeof config.items==='function'?config.items():config.items});};
      target.addEventListener('contextmenu',show);
      target.addEventListener('keydown',event=>{if(event.key==='ContextMenu'||event.key==='F10'&&event.shiftKey===true||event.keyCode===93)show(event);});
      return()=>{};
    },
  };
  world.AppContextMenu={forDocument:()=>menu};return state;
}
const event=(target,extra={})=>({target,key:'',shiftKey:false,preventDefault(){this.prevented=true;},stopPropagation(){this.stopped=true;},...extra});
const rows=box=>all(box).filter(node=>String(node.className||'').includes('rc-definition-row'));
const buttons=node=>all(node).filter(child=>child.tagName==='button');

test('action profile rows expose pointer, keyboard and ellipsis menus while resolving the current object index',()=>{
  const env=setup(),{world,PL}=env,menu=installMenu(world),plan=PL.state.plan;plan.plan_version=3;
  PL.state.caps={action_event_contract:{}};plan.action_event_profiles=[
    {id:'first',kind:'calibrated_single_contact_v1',prototype_hit_id:'',calibration:{status:'pending'}},
    {id:'second',kind:'calibrated_single_contact_v1',prototype_hit_id:'',calibration:{status:'pending'}},
  ];
  GUI_TEST_RUNTIME.loadWebFile(world,path.join(__dirname,'../src/genshin_app/gui/web/action-events.js'));
  const calls=[];world.AE.editProfile=index=>calls.push(['edit',index]);world.AE.drop=(kind,index)=>calls.push(['drop',kind,index]);
  const box=new Element();world.AE.render(box);const rendered=rows(box);assert.equal(rendered.length,2);
  assert.deepEqual(buttons(rendered[0]).map(button=>button.textContent),['⋯']);
  rendered[0].listeners.contextmenu(event(rendered[0]));assert.deepEqual(calls,[]);assert.equal(menu.current.items.some(item=>item.label==='删除动作档案'),true);
  plan.action_event_profiles.reverse();menu.current.items.find(item=>item.label==='编辑动作档案').run();assert.deepEqual(calls,[['edit',1]]);
  rendered[1].listeners.keydown(event(rendered[1],{key:'F10',shiftKey:true}));assert.equal(menu.current.label.includes('操作'),true);
  buttons(rendered[1])[0].emit('click');assert.equal(menu.opened.length,3);
});

test('action resource and snapshot rows keep destructive or edit commands out of the permanent row',()=>{
  const resource=setup(),resourceMenu=installMenu(resource.world),resourcePlan=resource.PL.state.plan;resourcePlan.plan_version=3;
  resource.PL.state.caps={action_resource_source_contract:{}};resourcePlan.action_resource_sources=[{ability_id:'q',status:'pending'},{ability_id:'e',status:'pending'}];
  GUI_TEST_RUNTIME.loadWebFile(resource.world,path.join(__dirname,'../src/genshin_app/gui/web/action-resources.js'));
  const resourceCalls=[];resource.world.AR.edit=index=>resourceCalls.push(['edit',index]);resource.world.AR.drop=index=>resourceCalls.push(['drop',index]);
  const resourceBox=new Element();resource.world.AR.render(resourceBox);const resourceRows=rows(resourceBox);assert.equal(resourceRows.length,2);
  assert.deepEqual(buttons(resourceRows[0]).map(button=>button.textContent),['⋯']);resourceRows[0].listeners.contextmenu(event(resourceRows[0]));
  resourcePlan.action_resource_sources.reverse();resourceMenu.current.items.find(item=>item.label?.startsWith('移除来源')).run();assert.deepEqual(resourceCalls,[['drop',1]]);

  const snapshot=setup(),snapshotMenu=installMenu(snapshot.world),snapshotPlan=snapshot.PL.state.plan;snapshotPlan.plan_version=3;
  snapshotPlan.mechanics_model={snapshot_captures:[{id:'one',action_id:'a1',offset:0,precision:.01,status:'pending',source:'一'},{id:'two',action_id:'a2',offset:0,precision:.01,status:'pending',source:'二'}]};
  snapshot.PL.state.caps={mechanics_contract:{snapshot_captures:{}}};snapshot.world.MC={caps:{snapshot_captures:{}},words:{}};
  GUI_TEST_RUNTIME.loadWebFile(snapshot.world,path.join(__dirname,'../src/genshin_app/gui/web/snapshot-captures.js'));
  const snapshotCalls=[];snapshot.world.SC.edit=index=>snapshotCalls.push(index);const snapshotBox=new Element();snapshot.world.SC.render(snapshotBox);const snapshotRows=rows(snapshotBox);assert.equal(snapshotRows.length,2);
  assert.deepEqual(buttons(snapshotRows[0]).map(button=>button.textContent),['⋯']);snapshotRows[0].listeners.contextmenu(event(snapshotRows[0]));
  snapshotPlan.mechanics_model.snapshot_captures.reverse();snapshotMenu.current.items[0].run();assert.deepEqual(snapshotCalls,[1]);
});

test('periodic nested object menus and identity resolver never act on a stale array position',()=>{
  const env=setup(),menu=installMenu(env.world);GUI_TEST_RUNTIME.loadWebFile(env.world,path.join(__dirname,'../src/genshin_app/gui/web/periodic-attacks.js'));
  const first={id:'first'},second={id:'second'},items=[first,second],calls=[];
  const card=new Element('section'),commands=()=>[{id:'remove',label:'移除攻击通道',danger:true,run:()=>env.world.PA.withCurrent(items,first,'攻击通道',index=>calls.push(index))}];
  env.world.PA.attachCommandMenu(card,'攻击通道操作',commands);assert.equal(card.tabIndex,0);assert.deepEqual(buttons(card).map(button=>button.textContent),['⋯']);
  card.listeners.contextmenu(event(card));items.reverse();menu.current.items[0].run();assert.deepEqual(calls,[1]);
  items.splice(items.indexOf(first),1);menu.current.items[0].run();assert.match(env.world.lastStatus,/已变化/);assert.deepEqual(calls,[1]);
});
