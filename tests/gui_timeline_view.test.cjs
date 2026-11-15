const GUI_TEST_RUNTIME = require('./helpers/gui_runtime.cjs');
// Run headlessly with node tests/gui_timeline_view.test.cjs.
const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');
const all=n=>[n,...n.children.flatMap(all)],clone=v=>JSON.parse(JSON.stringify(v));
class Element {
  constructor(tag='div'){
    this.tagName=tag;this.children=[];this.attributes={};this.dataset={};this.listeners=new Map();this.style={};
    this.clientWidth=1000;this.clientHeight=280;this.clientLeft=0;this.scrollLeft=0;this.scrollTop=0;this.value='';
    const classes=new Set();this.classList={add:(...cs)=>cs.forEach(c=>classes.add(c)),remove:(...cs)=>cs.forEach(c=>classes.delete(c)),
      toggle:(c,on)=>on?classes.add(c):classes.delete(c),contains:c=>classes.has(c)};
  }
  append(...nodes){for(const n of nodes){n.parent=this;this.children.push(n);}}
  appendChild(n){this.append(n);return n;}
  replaceChildren(...nodes){this.children=[];this.swaps=(this.swaps||0)+1;this.append(...nodes);}
  set innerHTML(v){this.children=[];this.text=v;} set textContent(v){this.children=[];this.text=String(v);}
  get textContent(){return (this.text||'')+this.children.map(n=>n.textContent).join('');}
  setAttribute(k,v){this.attributes[k]=v;if(k.startsWith('data-'))this.dataset[k.slice(5).replace(/-([a-z])/g,(_,c)=>c.toUpperCase())]=String(v);}
  removeAttribute(k){delete this.attributes[k];}
  addEventListener(type,fn){if(!this.listeners.has(type))this.listeners.set(type,new Set());this.listeners.get(type).add(fn);}
  removeEventListener(type,fn){this.listeners.get(type)?.delete(fn);}
  emit(type,values={}){const e={button:0,pointerId:1,clientX:0,clientY:0,target:this,preventDefault(){this.prevented=true;},stopPropagation(){this.stopped=true;},...values};
    for(const fn of [...(this.listeners.get(type)||[])])fn(e);return e;}
  closest(selector){const classes=String(this.attributes.class||'').split(' ');
    return selector.split(', ').some(s=>classes.includes(s.slice(1)))?this:this.parent?.closest(selector)||null;}
  getBoundingClientRect(){return {left:0,top:0,width:this.clientWidth,height:this.clientHeight};}
  setPointerCapture(id){this.captured=id;}hasPointerCapture(id){return this.captured===id;}releasePointerCapture(){this.captured=null;}
  contains(node){return all(this).includes(node);}
  focus(){this.focused=true;}
  remove(){if(!this.parent)return;this.parent.children=this.parent.children.filter(node=>node!==this);this.parent=null;}
}
const event=(id,kind,start,extra={})=>({id,kind,start,character:'a',sourceKey:'source-a',sourceLabel:'甲角色 · 元素战技',label:'元素战技',detail:{},...extra});
const projection=(views={})=>({duration:120,characters:[{id:'a',label:'甲角色'},{id:'b',label:'乙角色'}],views:{
  actions:{events:[],notes:[]},effects:{events:[],notes:[]},damage:{events:[],notes:[]},...views}});

function setup({data=projection(),native=true,actual=false,result={},template:provided,plan:providedPlan,surfaceKernel=false}={}){
  const nodes=new Map(),world=new Element('window'),frames=[],stats={build:0,current:0,inputs:0,invalidations:0,actionEdits:[]};
  const get=s=>{if(!nodes.has(s))nodes.set(s,new Element(s==='#timeline'?'svg':'div'));return nodes.get(s);};
  world.window=world;world.console=console;world.$=get;world.setStatus=()=>{};
  const documentListeners=new Map();
  world.document={body:new Element('body'),createElement:t=>new Element(t),createElementNS:(_,t)=>new Element(t),querySelector:get,
    querySelectorAll:()=>all(get('#timeline')).filter(n=>n.dataset.selectKind),
    addEventListener(type,fn){if(!documentListeners.has(type))documentListeners.set(type,new Set());documentListeners.get(type).add(fn);},
    removeEventListener(type,fn){documentListeners.get(type)?.delete(fn);}};
  world.requestAnimationFrame=fn=>{frames.push(fn);return frames.length;};
  vm.createContext(world);const web=path.join(__dirname,'../src/genshin_app/gui/web');
  for(const name of ['rotation','timeline-view','timeline','timeline-data'])GUI_TEST_RUNTIME.loadWebFile(world, path.join(web,`${name}.js`));
  if(surfaceKernel)GUI_TEST_RUNTIME.loadWebFile(world,path.join(web,'ui.js'));
  const template=provided||{version:1,loop_duration:120,characters:[{id:'a',name:'甲角色'},{id:'b',name:'乙角色'}],
    hits:[{id:'h',t:2,character:'a',skill:'skill',hit_name:'元素战技'}],buffs:[],switches:[]};
  const plan=native?(providedPlan||{manual_template:template,revision:1}):null;
  let current=true;const original=world.TD.build;
  world.TD.build=args=>{stats.build++;return actual?original(args):data;};
  world.RS={state:{result,snapshot:{executionModel:plan?.mechanics_model}},isCurrent:()=>{stats.current++;return current;},
    invalidate:()=>{stats.invalidations++;current=false;world.TV.invalidateData();}};
  world.PL={state:{plan},currentInputJson:()=>{stats.inputs++;return JSON.stringify(plan||world.RT.tpl());},onEdit:()=>{if(plan)plan.revision++;}};
  world.RC={render(){},editAction:index=>stats.actionEdits.push(index)};
  world.RT.state.template=template;world.RT.state.templateJson=JSON.stringify(plan||template);
  world.RT.renderTeamPanel=world.RT.renderPropPanel=()=>{};
  const flush=()=>{const work=frames.splice(0);for(const fn of work)fn();};
  world.RT.renderTimeline();
  return {world,nodes,get,stats,template,plan,flush,frames,setCurrent:value=>{current=value;}};
}
const marks=ctx=>all(ctx.get('#timeline')).filter(n=>String(n.attributes.class||'').includes('tl-view-event'));
const control=(node,label)=>all(node).find(n=>n.attributes['aria-label']===label);

test('uncalculated effects show a direct calculation action without inventing buff intervals',()=>{
  const ctx=setup({actual:true,result:null});let called=0;ctx.world.RS.runCalc=()=>called++;
  ctx.world.TV.changeMode('effects');ctx.world.TV.effectFocus='coverage';ctx.world.TV.rebuildRows();ctx.world.TV.paintProjection();
  assert.equal(ctx.world.TV.rows.length,0);assert.match(ctx.get('#timeline-note').textContent,/尚未计算当前方案/);
  assert.equal(ctx.get('#timeline-empty-state').hidden,false);
  const button=all(ctx.get('#timeline-empty-state')).find(n=>n.tagName==='button'&&n.textContent==='计算当前方案，展开持续状态');
  assert.ok(button);button.emit('click');assert.equal(called,1);
});

test('recorded exhausted interval has gray marker and Chinese read-only status details',()=>{
  const fx=event('empty','coverage',2,{end:8,detail:{receipt_id:'receipt',state_status:'exhausted',remaining_quota:0,layers:1,end_reason:'natural_expiry',
    states:[{start:2,end:8,status:'exhausted',remaining_quota:0,layers:1,end_reason:'natural_expiry'}]}});
  const ctx=setup({data:projection({effects:{events:[fx],notes:[]}})}),{TV}=ctx.world;TV.changeMode('effects');
  assert.equal(marks(ctx)[0].children[0].attributes.fill,'#89919c');TV.showDetail(fx);
  assert.match(ctx.get('#timeline-detail').textContent,/区间状态：次数耗尽/);assert.match(ctx.get('#timeline-detail').textContent,/区间结束原因：自然到期/);
  assert.match(ctx.get('#timeline-detail').textContent,/剩余 0 次/);assert.doesNotMatch(ctx.get('#timeline-detail').textContent,/编辑此事件/);
});

test('overlapping active and exhausted recipients cannot merge into a falsely active bar',()=>{
  const a=event('active','coverage',1,{end:10,detail:{state_status:'active'}}),b=event('exhausted','coverage',3,{end:8,detail:{state_status:'exhausted'}});
  const ctx=setup({data:projection({effects:{events:[a,b],notes:[]}})});ctx.world.TV.changeMode('effects');
  assert.equal(ctx.world.TV.rows.length,2);assert.equal(marks(ctx).length,2);
  assert.deepEqual(new Set(marks(ctx).map(n=>n.children[0].attributes.fill)),new Set(['#56b6c2','#89919c']));
});

test('three views are exclusive, colors agree with legend and action/effect surfaces omit damage values',()=>{
  const a=event('a','action',1,{end:3,edit:{kind:'action',index:0},detail:{execution_status:'failed'}});
  const fx=event('fx','coverage',2,{end:8,label:'共享增益',detail:{interpretation:'条件与前台限制仍需核对'}});
  const ctx=setup({data:projection({actions:{events:[a],notes:['动作声明']},effects:{events:[fx,event('trigger','activation',2)],notes:[]},
    damage:{events:[event('d','damage',2,{value:123456}),event('c','calculation',2)],notes:[]}})});
  const {TV}=ctx.world;assert.equal(TV.mode,'actions');assert.equal(marks(ctx).length,1);
  assert.match(marks(ctx)[0].attributes['aria-label'],/执行失败/);
  assert.doesNotMatch(ctx.get('#timeline').textContent,/123456|123,456/);
  TV.showDetail(a);assert.match(ctx.get('#timeline-detail').textContent,/执行状态：执行失败/);
  assert.equal(ctx.stats.actionEdits.length,0);
  all(ctx.get('#timeline-detail')).find(n=>n.tagName==='button'&&n.textContent==='编辑此动作').emit('click');
  assert.deepEqual(ctx.stats.actionEdits,[0]);
  TV.changeMode('effects');assert.deepEqual(new Set(marks(ctx).map(n=>n.attributes.class)),new Set(['tl-view-event tl-coverage','tl-view-event tl-activation']));
  TV.showDetail(fx);assert.match(ctx.get('#timeline-detail').textContent,/条件与前台限制/);
  const colors=marks(ctx).map(n=>n.children[0].attributes.fill);assert.equal(new Set(colors).size,2);
  const legendColors=all(ctx.get('#timeline-legend')).filter(n=>n.className==='tl-legend-symbol').map(n=>n.style.color);
  assert.deepEqual(new Set(legendColors),new Set(colors));
  assert.doesNotMatch(ctx.get('#timeline-detail').textContent,/123456|123,456/);
  TV.changeMode('damage');TV.showDetail(TV.rows.find(r=>r.kind==='damage').events[0]);
  assert.match(ctx.get('#timeline-detail').textContent,/123,456/);
  assert.match(ctx.get('#timeline-legend').textContent,/不是额外游戏事件/);
  TV.changeMode('effects');assert.doesNotMatch(ctx.get('#timeline-detail').textContent,/123456|123,456/);
});

test('character, source and kind controls filter records without recomputing or mutating inputs',()=>{
  const events=[event('1','action',1,{end:2}),event('2','switch',3),event('3','action',5,{end:6,sourceKey:'other',sourceLabel:'甲角色 · 爆发'}),
    event('4','action',7,{end:8,character:'b',sourceKey:'b',sourceLabel:'乙角色 · 战技'})];
  const ctx=setup({data:projection({actions:{events,notes:[]}})}),before=JSON.stringify(ctx.plan),initial={...ctx.stats};
  let c=control(ctx.get('#timeline-filters'),'角色');c.value='a';c.emit('change');
  assert.equal(ctx.world.TV.filteredCount,3);assert.ok(ctx.world.TV.rows.every(r=>r.character==='a'));
  c=control(ctx.get('#timeline-filters'),'来源');c.value='source-a';c.emit('change');
  assert.equal(ctx.world.TV.filteredCount,2);
  c=control(ctx.get('#timeline-filters'),'切换角色');c.checked=false;c.emit('change');
  assert.equal(ctx.world.TV.filteredCount,1);
  assert.equal(JSON.stringify(ctx.plan),before);assert.equal(ctx.stats.build,initial.build);
  assert.equal(ctx.stats.current,initial.current);assert.equal(ctx.stats.inputs,initial.inputs);assert.equal(ctx.stats.invalidations,0);
});

test('repeated source windows share a fixed row and zoom never changes lane count or height',()=>{
  const windows=Array.from({length:30},(_,i)=>event(`w${i}`,'coverage',i*3,{end:i*3+1}));
  const ctx=setup({data:projection({effects:{events:windows,notes:[]}})});ctx.world.TV.changeMode('effects');
  assert.equal(ctx.world.TV.rows.length,1);assert.equal(ctx.world.TV.rows[0].items.length,30);
  const height=ctx.get('#timeline').attributes.height;
  for(const factor of [2,2,.5,.5,1.5,1/1.5]){ctx.world.TV.zoomAt(factor);assert.equal(ctx.world.TV.rows.length,1);assert.equal(ctx.get('#timeline').attributes.height,height);}
  assert.equal(ctx.world.TV.rows[0].items.reduce((sum,i)=>sum+i.events.length,0),30);
});

test('dense point aggregation keeps every identity, supports paged drilldown and defers raw serialization',()=>{
  let serialized=0;
  const detail={toJSON(){serialized++;return {evidence:'仅展开时读取'};}};
  const events=Array.from({length:1000},(_,i)=>event(`point${i}`,'damage',i*.0001,{detail}));
  const ctx=setup({native:false,data:projection({damage:{events,notes:[]}})}),{TV}=ctx.world;
  assert.equal(TV.mode,'damage');assert.equal(TV.rows.length,1);
  assert.equal(TV.rows[0].items.length,1);assert.equal(TV.rows[0].items[0].events.length,1000);
  assert.equal(new Set(TV.rows[0].items.flatMap(i=>i.events.map(e=>e.id))).size,1000);
  assert.equal(serialized,0);marks(ctx)[0].emit('click');
  assert.match(ctx.get('#timeline-detail').textContent,/1000 条聚合记录/);
  const firstButtons=all(ctx.get('#timeline-detail')).filter(n=>n.tagName==='button');assert.equal(firstButtons.length,41);
  firstButtons.find(n=>n.textContent==='下一页').emit('click');assert.match(ctx.get('#timeline-detail').textContent,/第 2\/25 页/);
  all(ctx.get('#timeline-detail')).find(n=>n.tagName==='button'&&n.textContent.includes('元素战技')).emit('click');
  assert.equal(serialized,0);
  const raw=all(ctx.get('#timeline-detail')).find(n=>n.tagName==='details');raw.open=true;raw.emit('toggle');raw.emit('toggle');
  assert.equal(serialized,1);assert.match(raw.textContent,/仅展开时读取/);
});

test('viewport culls both axes, coalesces scrolls and never rechecks large input validity during navigation',()=>{
  const events=[];
  for(let lane=0;lane<160;lane++)for(let i=0;i<120;i++)events.push(event(`${lane}:${i}`,'damage',i,{sourceKey:`s${String(lane).padStart(3,'0')}`,sourceLabel:`来源 ${lane}`}));
  const ctx=setup({native:false,data:projection({damage:{events,notes:[]}})}),{TV}=ctx.world,wrap=ctx.get('#timeline-wrap'),svg=ctx.get('#timeline');
  assert.equal(TV.rows.length,160);assert.ok(marks(ctx).length<500);const height=svg.attributes.height;
  TV.zoomAt(8);assert.ok(marks(ctx).length<200);
  const counts={...ctx.stats},swaps=svg.swaps;
  for(let i=0;i<60;i++){wrap.scrollTop=600+i;wrap.scrollLeft=700+i;wrap.emit('scroll');}
  assert.equal(svg.swaps,swaps);assert.equal(ctx.frames.length,1);ctx.flush();assert.equal(svg.swaps,swaps+1);
  assert.equal(svg.attributes.height,height);assert.ok(marks(ctx).length<200);
  assert.equal(ctx.stats.build,counts.build);assert.equal(ctx.stats.current,counts.current);assert.equal(ctx.stats.inputs,counts.inputs);
  assert.ok(marks(ctx).every(n=>Number(n.attributes['data-event-id'].split(':')[1])<40));
});

test('zoom anchors, view switches, panning and filters preserve input and current result',()=>{
  const events=[event('h','damage',50)],ctx=setup({native:false,data:projection({damage:{events,notes:[]},effects:{events:[event('f','activation',50)],notes:[]}})});
  const {TV,RT}=ctx.world,wrap=ctx.get('#timeline-wrap'),before=JSON.stringify(ctx.template),revision=RT.state.revision;
  const anchor=600,sec=(anchor-210)/TV.pixelsPerSecond;TV.zoomAt(2,anchor);
  assert.ok(Math.abs((wrap.scrollLeft+anchor-210)/TV.pixelsPerSecond-sec)<1e-9);
  const zoom=TV.zoom,left=wrap.scrollLeft;TV.changeMode('effects');assert.equal(TV.zoom,zoom);assert.equal(wrap.scrollLeft,left);
  wrap.emit('pointerdown',{target:ctx.get('#timeline'),clientX:500,clientY:100});
  ctx.world.emit('pointermove',{clientX:450,clientY:80});ctx.world.emit('pointerup',{clientX:450,clientY:80});ctx.flush();
  assert.equal(wrap.scrollLeft,left+50);assert.equal(TV.cancelPan,null);
  assert.equal(JSON.stringify(ctx.template),before);assert.equal(RT.state.revision,revision);assert.equal(ctx.stats.invalidations,0);
  assert.equal(ctx.world.RS.isCurrent(),true);
});

test('real TD integration drops stale coverage and damage, displays action status and respects explicit window',()=>{
  const template={loop_duration:20,characters:[{id:'a',name:'甲角色'}],hits:[{id:'h',t:2,character:'a',hit_name:'战技命中'}],buffs:[]};
  const model={rules:[{id:'r',name_zh:'增益',owner:'a',effects:[]}],hit_events:[{hit_id:'h',action_id:'a'}],initial_state:{rules:[]}};
  const plan={manual_template:template,rotation_model:{period:20,max_cycles:3,abilities:[{id:'e',character:'a',action_type_id:'action.elemental_skill'}],
    actions:[{id:'a',ability_id:'e',start:1,occupation_end:2,repeat:{mode:'once'}}]},mechanics_model:model};
  const result={hits:[{id:'h',t:2,character:'a',expected:1234}],mechanics_trace:[{type:'activation',t:1,rule_id:'r',instance_id:'i',beneficiaries:['a'],expires_at:[11],quota:{}}],
    sampling_trace:[],rotation_check:{event_log:[{type:'action',action_id:'a',id:'a',t:1,status:'passed'}]}};
  const ctx=setup({actual:true,template,plan,result}),{TV,RT}=ctx.world;
  assert.equal(TV.projection.duration,20);assert.equal(TV.projection.views.actions.events.length,1);
  TV.changeMode('effects');assert.ok(TV.rows.some(r=>r.kind==='coverage'));
  const before=JSON.stringify(plan);ctx.setCurrent(false);TV.invalidateData();ctx.flush();
  assert.equal(TV.mode,'effects');assert.equal(TV.rows.some(r=>r.kind==='coverage'),false);
  TV.changeMode('damage');assert.ok(TV.rows.every(r=>r.kind!=='calculation'));assert.ok(TV.rows.flatMap(r=>r.events).every(e=>e.value==null));
  assert.equal(JSON.stringify(plan),before);assert.equal(RT.state.revision,0);
});

test('only manual records drag and commit once; derived records remain read-only',()=>{
  const manual=event('h','damage',2,{edit:{kind:'hit',index:0}}),derived=event('d','damage',20);
  const ctx=setup({native:false,data:projection({damage:{events:[manual,derived],notes:[]}})}),{RT,TV}=ctx.world;
  const group=marks(ctx).find(n=>n.attributes['data-event-id']==='h'),readonly=marks(ctx).find(n=>n.attributes['data-event-id']==='d');
  assert.equal(readonly.listeners.has('pointerdown'),false);
  const pps=TV.pixelsPerSecond;group.emit('pointerdown',{clientX:300});ctx.world.emit('pointermove',{clientX:300+pps});
  assert.equal(ctx.template.hits[0].t,2);ctx.world.emit('pointerup',{clientX:300+pps});ctx.world.emit('pointerup',{clientX:300+pps});
  assert.equal(ctx.template.hits[0].t,3);assert.equal(RT.state.revision,1);assert.equal(ctx.stats.invalidations,1);
});

test('read-only event details clear an old manual editor without editing or invalidating the plan',()=>{
  const derived=event('derived','coverage',2,{end:10});
  const ctx=setup({data:projection({effects:{events:[derived],notes:[]}})}),{RT,TV}=ctx.world;
  RT.state.selection={kind:'hit',index:0};let propRenders=0;
  RT.renderPropPanel=()=>propRenders++;
  const before=JSON.stringify(ctx.plan),revision=RT.state.revision;
  TV.showDetail(derived);
  assert.equal(RT.sel(),null);assert.equal(propRenders,1);
  assert.equal(JSON.stringify(ctx.plan),before);assert.equal(RT.state.revision,revision);assert.equal(ctx.stats.invalidations,0);
});

test('real refreshed coverage explains each activation and expiry without counting quota cuts as refreshes',()=>{
  const template={loop_duration:20,characters:[{id:'a',name:'甲角色'}],hits:[],buffs:[]};
  const plan={manual_template:template,mechanics_model:{rules:[{id:'r',name_zh:'测试增益',owner:'a',effects:[]}],initial_state:{rules:[]}}};
  const activation=(t,expiry)=>({type:'activation',t,rule_id:'r',instance_id:'i',beneficiaries:['a'],expires_at:[expiry],quota:{a:3}});
  const quota=t=>({type:'quota',t,rule_id:'r',instance_id:'i',pool:'a',before:3,after:2,consumed:true});
  const result={hits:[],mechanics_trace:[activation(3,13),quota(4),activation(5,15),quota(6)],rotation_check:{event_log:[]}};
  const ctx=setup({actual:true,template,plan,result}),{TV}=ctx.world;
  TV.changeMode('effects');const coverage=TV.rows.find(r=>r.kind==='coverage').events[0];
  assert.equal(coverage.start,3);assert.equal(coverage.end,15);assert.equal(coverage.detail.states.length,4);
  TV.showDetail(coverage);const box=ctx.get('#timeline-detail');
  assert.match(box.textContent,/2 次激活\/刷新合并/);assert.match(box.textContent,/整段长度 12 秒包含后续刷新/);
  const list=all(box).find(n=>n.tagName==='ol');assert.equal(list.children.length,2);
  assert.equal(list.children[0].textContent,'3秒：激活，规则到期 13秒。');
  assert.equal(list.children[1].textContent,'5秒：刷新，规则到期 15秒。');
  assert.equal(list.parent.open,true);assert.match(box.textContent,/次数或层数等状态变化；这些变化不计为刷新/);
  const single={...coverage,end:5,detail:{...coverage.detail,states:coverage.detail.states.slice(0,2)}};
  TV.showDetail(single);assert.equal(all(box).some(n=>n.tagName==='ol'),false);
  assert.match(box.textContent,/3秒激活，规则到期 13秒；本段未合并后续刷新/);
  assert.doesNotMatch(box.textContent,/2 次激活\/刷新/);
});

test('actual executed hit trace is normal and translated in both effects and damage views',()=>{
  const template={loop_duration:20,characters:[{id:'a',name:'甲角色'}],hits:[{id:'h',t:2,character:'a',hit_name:'战技命中'}],buffs:[]};
  const result={hits:[{id:'h',t:2,character:'a',expected:1234}],mechanics_trace:[{type:'hit',t:2,event_id:'h',cycle:0,status:'executed',reported_damage:true}]};
  const ctx=setup({actual:true,native:false,template,result}),{TV}=ctx.world;
  for(const mode of ['effects','damage']){
    TV.changeMode(mode);const kind=mode==='effects'?'hit':'damage';
    const mark=marks(ctx).find(n=>n.attributes.class===`tl-view-event tl-${kind}`);
    assert.match(mark.attributes['aria-label'],/已执行/);assert.doesNotMatch(mark.textContent,/状态待解释|未验证/);
    assert.equal(mark.children[0].attributes['stroke-dasharray'],undefined);
    assert.equal(mark.children[0].attributes.opacity,undefined);
    TV.showDetail(TV.rows.find(r=>r.kind===kind).events[0]);
    assert.match(ctx.get('#timeline-detail').textContent,/执行状态：已执行/);
  }
});

test('damage labels prioritize the full value and hide it rather than truncate when space is insufficient',()=>{
  const longName='这是一个很长的伤害来源与技能名称'.repeat(15);
  const ctx=setup({native:false,data:projection({damage:{events:[event('d','damage',5,{value:123456,label:longName})],notes:[]}})});
  const mark=()=>marks(ctx)[0],label=()=>mark().children.find(n=>n.attributes.class==='tl-view-label');
  assert.match(label().textContent,/^123,456 /);assert.match(label().textContent,/…$/);
  assert.match(mark().children.find(n=>n.tagName==='title').textContent,/期望伤害：123,456/);
  // At the right edge the marker fits but the complete 7-character value does not.
  ctx.world.TV.rows[0].events[0].start=119;ctx.world.TV.packKey=null;ctx.world.TV.paintProjection();
  assert.equal(label(),undefined);
  assert.match(mark().attributes['aria-label'],/期望伤害：123,456/);
  ctx.world.TV.showDetail(ctx.world.TV.rows[0].events[0]);
  assert.match(ctx.get('#timeline-detail').textContent,/期望伤害：123,456/);
});

test('effects lead with shared buff windows, retain recipients and filter without editing or recalculating',()=>{
  const detail={owner:'b',rule_id:'shared',instance_id:'instance',remaining_quota:null,states:[{start:1,end:9}]};
  const shared=character=>event(`buff-${character}`,'coverage',1,{character,end:9,sourceKey:'buff',sourceLabel:'乙角色 · 队伍增伤',detail});
  const data=projection({effects:{events:[event('h','hit',2),shared('a'),shared('b')],notes:[]}});
  const original=JSON.stringify(data),ctx=setup({data}),{TV,RT}=ctx.world;
  TV.changeMode('effects');assert.equal(TV.rows[0].kind,'coverage');
  assert.equal(TV.rows[0].events.length,1);assert.equal(TV.rows[0].events[0].characters.length,2);
  TV.showDetail(TV.rows[0].events[0]);assert.match(ctx.get('#timeline-detail').textContent,/接收角色：甲角色、乙角色/);
  const focus=label=>all(ctx.get('#timeline-filters')).find(n=>n.tagName==='button'&&n.textContent===label).emit('click');
  const builds=ctx.stats.build,before=JSON.stringify(ctx.plan),revision=RT.state.revision;
  focus('持续Buff');assert.equal(TV.rows.map(r=>r.kind).join(','),'coverage');
  focus('命中与触发');assert.equal(TV.rows.map(r=>r.kind).join(','),'hit');
  focus('全部效果');assert.equal(TV.rows.length,2);
  assert.equal(ctx.stats.build,builds);assert.equal(JSON.stringify(ctx.plan),before);assert.equal(RT.state.revision,revision);
  assert.equal(JSON.stringify(data),original);
  TV.characterFilter='a';TV.rebuildRows();assert.equal(TV.rows[0].events[0].characters.length,1);
  assert.equal(TV.rows[0].events[0].characters[0],'a');
});

test('action lanes group by character while sources and switches remain distinct records',()=>{
  const events=[event('e','action',1,{end:2,edit:{kind:'action',index:0}}),
    event('q','action',3,{end:4,sourceKey:'burst',label:'元素爆发',sourceLabel:'甲角色 · 元素爆发'}),
    event('s','switch',5),event('b','action',6,{end:7,character:'b'})];
  const ctx=setup({data:projection({actions:{events,notes:[]}})}),{TV}=ctx.world;
  assert.equal(TV.rows.length,2);assert.equal(TV.rows[0].events.length,3);
  assert.equal(marks(ctx).filter(n=>n.attributes.class==='tl-view-event tl-switch').length,1);
  assert.equal(all(ctx.get('#timeline-modes')).filter(n=>n.tagName==='button').map(n=>n.textContent).join(','),'动作轴,效果轴,伤害轴');
  assert.match(ctx.get('#timeline-modes').textContent,/选择动作可编辑/);
  const source=control(ctx.get('#timeline-filters'),'来源');source.value='burst';source.emit('change');
  assert.equal(TV.filteredCount,1);assert.equal(TV.rows[0].events[0].id,'q');
});

test('filter menu starts collapsed, retains state through changes, and Escape closes with focus',()=>{
  const ctx=setup({data:projection({actions:{events:[event('e','action',1),event('s','switch',2)],notes:[]}})}),{TV}=ctx.world;
  const before=JSON.stringify(ctx.plan),initial={...ctx.stats};
  assert.equal(TV.filterDetails.open,false);
  let menu=TV.filterDetails,summary=menu.children.find(n=>n.tagName==='summary');
  assert.equal(summary.attributes['aria-expanded'],'false');
  menu.open=true;menu.emit('toggle');assert.equal(summary.attributes['aria-expanded'],'true');
  const type=control(ctx.get('#timeline-filters'),'切换角色');type.checked=false;type.emit('change');
  menu=TV.filterDetails;summary=menu.children.find(n=>n.tagName==='summary');
  assert.equal(menu.open,true);assert.match(summary.textContent,/筛选 · 1/);
  const ev=menu.emit('keydown',{key:'Escape'});assert.equal(menu.open,false);assert.equal(ev.prevented,true);assert.equal(ev.stopped,true);
  assert.equal(summary.attributes['aria-expanded'],'false');assert.equal(summary.focused,true);
  assert.equal(JSON.stringify(ctx.plan),before);assert.equal(ctx.stats.build,initial.build);assert.equal(ctx.stats.invalidations,0);
});

test('filter dismissal distinguishes inside pointers, resets filters and does not alter inputs',()=>{
  const ctx=setup({data:projection({actions:{events:[event('e','action',1),event('s','switch',2)],notes:[]}})}),{TV}=ctx.world;
  let menu=TV.filterDetails;menu.open=true;menu.emit('toggle');
  ctx.world.emit('pointerdown',{target:menu.children[0]});assert.equal(menu.open,true);
  ctx.world.emit('pointerdown',{target:ctx.get('#timeline')});assert.equal(menu.open,false);
  const filter=control(ctx.get('#timeline-filters'),'角色');filter.value='a';filter.emit('change');
  all(ctx.get('#timeline-filters')).find(n=>n.tagName==='button'&&n.textContent==='重置筛选').emit('click');
  assert.equal(TV.characterFilter,'');assert.equal(TV.sourceFilter,'');assert.equal(TV.filteredCount,2);assert.equal(ctx.stats.invalidations,0);
});

test('filter menu uses the shared surface lifecycle when the application kernel is available',()=>{
  const ctx=setup({surfaceKernel:true,data:projection({actions:{events:[event('e','action',1)],notes:[]}})}),
    {TV,AppSurfaceKernel}=ctx.world;
  const menu=TV.filterDetails,summary=menu.children.find(node=>node.tagName==='summary');
  menu.open=true;menu.emit('toggle');
  assert.equal(AppSurfaceKernel.getActive(),TV.filterSurface);assert.equal(summary.attributes['aria-expanded'],'true');
  AppSurfaceKernel.close('outside');
  assert.equal(menu.open,false);assert.equal(TV.filterOpen,false);assert.equal(TV.filterSurface,null);
  assert.equal(summary.attributes['aria-expanded'],'false');assert.equal(summary.focused,true);
});

test('selected records remain highlighted after zoom and paging without serializing input',()=>{
  const ctx=setup({native:false,data:projection({damage:{events:[event('d','damage',5,{value:234}),event('other','damage',25)],notes:[]}})}),{TV}=ctx.world;
  const before=JSON.stringify(ctx.template),initial={...ctx.stats};let reveals=0;
  ctx.world.WS={reveal:(key,side)=>{assert.equal(key,'rotation');assert.equal(side,'right');reveals++;}};
  marks(ctx)[0].emit('click');assert.equal(reveals,1);
  assert.equal(marks(ctx)[0].classList.contains('tl-event-selected'),true);
  assert.equal(marks(ctx)[0].attributes['aria-pressed'],'true');
  TV.zoomAt(2,210+5*TV.pixelsPerSecond);assert.equal(marks(ctx).find(n=>n.attributes['data-event-id']==='d').classList.contains('tl-event-selected'),true);
  assert.match(ctx.get('#timeline-detail').textContent,/角色：甲角色/);assert.match(ctx.get('#timeline-detail').textContent,/时间：5秒/);
  TV.changeMode('effects');assert.equal(TV.detailSelection,null);
  assert.equal(JSON.stringify(ctx.template),before);assert.equal(ctx.stats.build,initial.build);assert.equal(ctx.stats.inputs,initial.inputs);assert.equal(ctx.stats.invalidations,0);
});

test('damage detail links matching calculation with human readable zones and can return',()=>{
  const damage=event('d','damage',5,{value:2345,detail:{hit_id:'hit-a'}}),calculation=event('c','calculation',5,
    {detail:{hit_id:'hit-a',hit:{expected:2345},zones:{base:3000,res:0.9}}});
  const ctx=setup({native:false,data:projection({damage:{events:[damage,calculation],notes:[]}})}),{TV}=ctx.world;
  ctx.world.DS={label:(group,key)=>({base:'基础伤害',res:'抗性系数'}[key]||key)};
  TV.showDetail(damage);all(ctx.get('#timeline-detail')).find(n=>n.tagName==='button'&&n.textContent==='查看这次伤害的计算明细').emit('click');
  assert.match(ctx.get('#timeline-detail').textContent,/期望伤害：2,345/);
  assert.match(ctx.get('#timeline-detail').textContent,/基础伤害3,000/);
  const raw=all(ctx.get('#timeline-detail')).find(n=>n.tagName==='details');assert.notEqual(raw.open,true);
  all(ctx.get('#timeline-detail')).find(n=>n.tagName==='button'&&n.textContent==='← 返回伤害事件').emit('click');
  assert.equal(TV.detailSelection.id,'d');
});

test('mode permission describes legacy input editing and derived result read-only separately',()=>{
  const manual=event('manual','coverage',1,{end:5,edit:{kind:'buff',index:0}});
  const ctx=setup({native:false,data:projection({effects:{events:[manual],notes:[]},damage:{events:[event('d','damage',2)],notes:[]}})});
  ctx.world.TV.changeMode('effects');assert.match(ctx.get('#timeline-modes').textContent,/手工输入可编辑/);
  ctx.world.TV.changeMode('damage');assert.match(ctx.get('#timeline-modes').textContent,/计算结果 · 只读/);
});

test('damage view defaults to damage records with optional calculation lanes and exact cluster sums',()=>{
  const events=[event('d1','damage',5,{value:200}),event('d2','damage',5.1,{value:300}),
    event('c','calculation',5,{value:500,detail:{hit:{expected:500}}})];
  const ctx=setup({native:false,data:projection({damage:{events,notes:[]}})}),{TV}=ctx.world;
  const before=JSON.stringify(ctx.template);
  assert.equal(TV.rows.length,1);assert.equal(TV.filteredCount,2);
  assert.match(marks(ctx)[0].attributes['aria-label'],/本组期望伤害合计：500/);
  assert.match(marks(ctx)[0].textContent,/Σ 500/);
  marks(ctx)[0].emit('click');assert.match(ctx.get('#timeline-detail').textContent,/本组伤害合计：500/);
  all(ctx.get('#timeline-filters')).find(n=>n.tagName==='button'&&n.textContent==='含计算记录').emit('click');
  assert.equal(TV.rows.length,2);assert.equal(TV.filteredCount,3);
  all(ctx.get('#timeline-filters')).find(n=>n.tagName==='button'&&n.textContent==='伤害事件').emit('click');
  assert.equal(TV.rows.length,1);assert.equal(JSON.stringify(ctx.template),before);assert.equal(ctx.stats.invalidations,0);
});

test('partially uncalculated damage groups never expose a partial sum as complete damage',()=>{
  const events=[event('d1','damage',5,{value:200}),event('d2','damage',5.1)];
  const ctx=setup({native:false,data:projection({damage:{events,notes:[]}})});
  assert.doesNotMatch(marks(ctx)[0].textContent,/Σ|合计/);marks(ctx)[0].emit('click');
  assert.doesNotMatch(ctx.get('#timeline-detail').textContent,/本组伤害合计/);
});

test('action badges use declared types, stagger skill and burst, and group dense normal labels only',()=>{
  const abilities=[['skill','action.elemental_skill'],['burst','action.elemental_burst'],['normal','action.normal_attack']]
    .map(([id,action_type_id])=>({id,action_type_id,character:'a'}));
  const events=[event('e','action',1,{end:1.5,label:'角色自定技能名',detail:{ability_id:'skill'}}),
    event('q','action',2,{end:2.7,label:'角色自定爆发名',detail:{ability_id:'burst'}}),
    ...[10,10.15,10.3].map((t,i)=>event(`n${i}`,'action',t,{end:t+.1,label:'攻击序列',detail:{ability_id:'normal'}})),
    event('switch','switch',19,{label:'切换至乙角色'})];
  const ctx=setup({data:projection({actions:{events,notes:[]}}),plan:{rotation_model:{abilities}}}),{TV}=ctx.world;
  assert.equal(TV.rows[0].events.length,6);assert.equal(marks(ctx).length,6);
  const labels=TV.rows[0].actionLabels;
  assert.equal(labels.find(label=>label.caption==='战技').level,0);
  assert.equal(labels.find(label=>label.caption==='爆发').level,1);
  const normal=labels.find(label=>label.caption==='普攻');assert.equal(normal.text,'普攻×3');assert.equal(normal.events.length,3);
  assert.equal(labels.find(label=>label.caption.startsWith('切')).caption,'切→乙角色');
  assert.ok(Number(ctx.get('#timeline').attributes.height)>=84+44);
  assert.equal(events.map(event=>event.start).join(','),'1,2,10,10.15,10.3,19');
});

test('action label click opens original records without starting pan or editing actions',()=>{
  const events=[event('n1','action',10,{end:10.1,label:'普通攻击'}),event('n2','action',10.15,{end:10.25,label:'普通攻击'})];
  const ctx=setup({data:projection({actions:{events,notes:[]}})}),{TV}=ctx.world,before=JSON.stringify(ctx.plan);
  const badge=all(ctx.get('#timeline')).find(n=>n.attributes.class==='tl-action-caption');
  ctx.get('#timeline-wrap').emit('pointerdown',{target:badge.children[1]});assert.equal(TV.cancelPan,undefined);
  badge.emit('click');assert.match(ctx.get('#timeline-detail').textContent,/2 条聚合记录/);
  assert.equal(ctx.stats.actionEdits.length,0);assert.equal(JSON.stringify(ctx.plan),before);assert.equal(ctx.stats.invalidations,0);
});
