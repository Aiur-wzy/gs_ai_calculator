const GUI_TEST_RUNTIME = require('./helpers/gui_runtime.cjs');
const Python = require('./helpers/python_command.cjs');
// Independent G15 checks using real engine receipts, not guessed trace fixtures.
// Run: node --test tests/gui_timeline_review.test.cjs
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const root = path.resolve(__dirname, '..');
const python = String.raw`
import sys,runpy,json,copy
sys.path.insert(0,'src')
x=runpy.run_path('tests/test_mechanics_execution.py')
cases={}
def save(name,t,r,m):
    original=copy.deepcopy((t,r,m))
    out=x['run'](t,r,m)
    assert original == (t,r,m)
    assert out['result'] is not None, (name,out['status'],out['issues'])
    result={**out['result'], 'rotation_check':out['rotation_check'],
      'mechanics_trace':out['mechanics_trace'], 'sampling_trace':out['sampling_trace']}
    cases[name]={'template':t,'plan':{'plan_version':3,'manual_template':t,
       'rotation_model':r,'mechanics_model':m},'executionModel':m,'result':result,'current':True}
t,r,m=x['fixture']((.2,.4,1.2))
x['add'](m,x['rule'](trigger={'event':'hit','phase':'pre','event_ids':['h0','h1']},
    duration=1,stacking={'max':2,'add':1,'policy':'independent'}))
save('independent',t,r,m)
for refresh in ('keep','reset'):
    t,r,m=x['fixture']((.2,1.2))
    r['actions'].append(x['action']('cast2',start=1,end=1.1,order=50))
    x['add'](m,x['rule'](trigger={'event':'action_start','phase':'pre','action_ids':['cast','cast2']},
      duration=5,quota={'capacity':1,'scope':'per_beneficiary','refresh':refresh,
      'consume':{'event':'hit','phase':'pre'}}))
    save('quota_'+refresh,t,r,m)
t,r,m=x['fixture']((.2,.5))
x['add'](m,x['rule'](trigger={'event':'action_end','phase':'pre','action_ids':['cast']},duration=.1))
for descriptor in m['hit_events']: descriptor['self_snapshot_event']='cast:end'
save('snapshot',t,r,m)
t,r,m=x['fixture']((.2,.4,.6))
m['hit_events'][0]['report']=False
x['add'](m,x['rule'](quota={'capacity':2,'scope':'team','refresh':'reset',
  'consume':{'event':'hit','phase':'pre'}}))
save('unreported',t,r,m)
t,r,m=x['fixture']()
r['abilities'][0]=x['ability'](kind='action.elemental_burst',energy={'required':10,'amount':10})
r['characters'][0]['initial_energy']=0
r['events']=[{'id':'spend','type':'energy_spend','t':0,'order':1,'action_id':'cast',
 'character':'a','required':10,'amount':10,'repeat':x['REPEAT'],**x['EVIDENCE']}]
x['add'](m,x['rule']())
save('failed',t,r,m)
t,r,m=x['fixture']((.2,))
x['add'](m,x['rule'](duration=0,effects=[],resources=[{'id':'instant_energy','kind':'energy_direct',
 'delay':0,'amounts':{'a':1},'source_part':'instant_energy'}]))
m['resource_ownership']=[{'rule_id':'boost','action_id':'cast','source_part':'instant_energy','replaces_event_ids':[]}]
save('instant',t,r,m)
t,r,m=x['fixture']((.2,.4))
x['add'](m,x['rule'](
 quota={'capacity':2,'scope':'per_beneficiary','refresh':'reset','consume':{'event':'hit','phase':'pre'}}))
m['initial_state']['rules'][0]['cooldown_remaining']=5
m['initial_state']['rules'][0]['instances']=[{'beneficiaries':['a'],'remaining':[2],'quota':{'a':1}}]
save('initial',t,r,m)
print(json.dumps(cases,ensure_ascii=False))
`;
const receiptRun = Python.spawnSync(['-c', python], {
  cwd:root, encoding:'utf8', windowsHide:true,
  env:{...process.env, PYTHONUTF8:'1', PYTHONIOENCODING:'utf-8'}, maxBuffer:4 * 1024 * 1024,
});
assert.equal(receiptRun.status, 0, receiptRun.stderr);
const fixtures = JSON.parse(receiptRun.stdout);

function load() {
  const world = {console}; world.window=world;
  vm.createContext(world);
  GUI_TEST_RUNTIME.loadWebFile(world, path.join(root,'src/genshin_app/gui/web/timeline-data.js'));
  return world.TD;
}
function plain(value) { return JSON.parse(JSON.stringify(value)); }
function projected(name, override={}) { return plain(load().build({...fixtures[name], ...override})); }
function events(output, view, kind) { return output.views[view].events.filter(event=>event.kind===kind); }
function segments(output, status='active') {
  return events(output,'effects','coverage').filter(e=>(e.detail.state_status||'active')===status)
    .map(e=>[e.start,e.end]).sort((a,b)=>a[0]-b[0]);
}
function union(parts) {
  const merged=[];
  for(const item of parts){const last=merged.at(-1);
    if(last && item[0]<=last[1])last[1]=Math.max(last[1],item[1]);else merged.push([...item]);}
  return merged;
}

test('real independent layers have no fictitious gap at partial expiration and no expanded check cycles',()=>{
  const out=projected('independent');
  assert.equal(out.duration,10);
  assert.deepEqual(union(segments(out)),[[.2,1.4]]);
  assert.equal(events(out,'actions','action').length,1);
  assert.equal(events(out,'damage','damage').length,3);
  assert.ok(Object.values(out.views).every(view=>view.events.every(event=>event.start<10)));
});

test('quota keep refresh cannot resurrect an exhausted pool; reset refresh can',()=>{
  const keep=projected('quota_keep'),reset=projected('quota_reset');
  assert.deepEqual(union(segments(keep)),[[0,.2]]);
  assert.deepEqual(union(segments(reset)),[[0,.2],[1,1.2]]);
  assert.deepEqual(union(segments(keep,'exhausted')),[[.2,6]]);
  assert.deepEqual(union(segments(reset,'exhausted')),[[.2,1],[1.2,6]]);
  for(const out of [keep,reset])assert.ok(events(out,'effects','coverage')
    .filter(e=>e.detail.state_status==='exhausted').every(e=>e.detail.remaining_quota===0));
});

test('snapshot-applied late damage remains traceable after the live interval expired',()=>{
  const out=projected('snapshot');
  assert.deepEqual(union(segments(out)),[[.1,.2]]);
  const late=events(out,'damage','calculation').find(event=>event.start===.5);
  assert.ok(late);
  assert.ok(late.detail.effects.some(row=>row.type==='effect_applied' && row.sample_t===.1));
  assert.equal(late.detail.hit.expected,90);
});

test('unreported hits remain visible as effect points and consume quota but do not become damage',()=>{
  const out=projected('unreported');
  assert.equal(events(out,'effects','hit').length,3);
  assert.equal(events(out,'damage','damage').length,2);
  const hidden=events(out,'effects','hit').find(event=>event.start===.2);
  assert.equal(hidden.detail.reported_damage,false);
  assert.ok(events(out,'effects','quota').some(event=>event.start===.2 && event.detail.consumed));
});

test('failed burst shows its failed action rather than hypothetical damage or buffs',()=>{
  const out=projected('failed');
  assert.equal(events(out,'actions','action')[0].detail.execution_status,'failed');
  assert.equal(events(out,'effects','hit').length,0);
  assert.equal(events(out,'effects','activation').length,0);
  assert.equal(events(out,'damage','damage').length,0);
});

test('instant rule retains an activation point without manufacturing duration',()=>{
  const out=projected('instant');
  assert.equal(events(out,'effects','activation').length,1);
  assert.equal(events(out,'effects','coverage').length,0);
});

test('explicit initial pool identity aligns with real first consumption and stops its window',()=>{
  const out=projected('initial');
  assert.deepEqual(union(segments(out)),[[0,.2]]);
  assert.deepEqual(union(segments(out,'exhausted')),[[.2,2]]);
  const quota=events(out,'effects','quota').find(event=>event.start===.2);
  const bar=events(out,'effects','coverage')[0];
  assert.equal(bar.detail.instance_id,quota.detail.instance_id);
  assert.equal(quota.detail.before,1);
  assert.equal(quota.detail.after,0);
});

test('stale result cannot leak actual damage, trace activation or calculation records',()=>{
  const out=projected('independent',{current:false});
  assert.equal(events(out,'damage','calculation').length,0);
  assert.ok(events(out,'damage','damage').every(event=>!Object.hasOwn(event,'value')));
  assert.equal(events(out,'effects','activation').length,0);
  assert.ok(out.views.damage.notes.some(note=>note.includes('失效')));
});

test('projection is read-only and old receipts without instance identity degrade safely',()=>{
  const input=plain(fixtures.independent),before=JSON.stringify(input);
  load().build(input);
  assert.equal(JSON.stringify(input),before);
  // A historical package has no authoritative state receipt. Mutating only its
  // obsolete trace cannot override a current engine receipt by design.
  delete input.result.buff_state_timeline;
  input.result.mechanics_trace.forEach(row=>{if(row.type==='activation')delete row.instance_id;});
  const out=plain(load().build(input));
  assert.equal(events(out,'effects','activation').length,2);
  assert.equal(events(out,'effects','coverage').length,0);
  assert.ok(out.views.effects.notes.some(note=>note.includes('旧流水')));
});

class Node {
  constructor(tag='div') {
    this.tagName=tag;this.children=[];this.attributes={};this.listeners=new Map();this.style={};
    this.clientWidth=900;this.clientHeight=280;this.clientLeft=0;this.scrollLeft=0;this.scrollTop=0;
    const classes=new Set();this.classList={add:(x)=>classes.add(x),remove:(x)=>classes.delete(x),
      contains:(x)=>classes.has(x),toggle:(x,v)=>v?classes.add(x):classes.delete(x)};
  }
  set textContent(value){this.text=String(value);this.children=[];}
  get textContent(){return (this.text||'')+this.children.map(c=>c.textContent).join('');}
  set innerHTML(value){this.text=value;this.children=[];}
  setAttribute(k,v){this.attributes[k]=v;}
  removeAttribute(k){delete this.attributes[k];}
  appendChild(n){this.children.push(n);return n;}
  append(...children){this.children.push(...children);}
  replaceChildren(...children){this.children=children;}
  addEventListener(kind,handler){if(!this.listeners.has(kind))this.listeners.set(kind,[]);this.listeners.get(kind).push(handler);}
  removeEventListener(){}
  getBoundingClientRect(){return {left:0,top:0,width:this.clientWidth,height:this.clientHeight};}
}
function renderer(input,reference=false) {
  const world=new Node();world.window=world;world.console=console;
  const nodes=new Map();world.$=key=>{if(!nodes.has(key))nodes.set(key,new Node());return nodes.get(key);};
  world.document={createElement:tag=>new Node(tag),createElementNS:(ns,tag)=>new Node(tag),querySelectorAll:()=>[]};
  world.RT={state:{revision:1},tpl:()=>input.template,sel:()=>null,round2:x=>Math.round(x*100)/100,
    syncRevision:()=>{throw new Error('navigation entered input revision check');},
    select:()=>{throw new Error('read-only navigation changed selection input');}};
  world.PL={state:{plan:input.plan||null}};
  let currentCalls=0;
  world.RS={state:{result:input.result||null,snapshot:{executionModel:input.executionModel||null}},
    isCurrent:()=>{currentCalls++;return !!input.current;}};
  vm.createContext(world);
  for(const file of ['timeline-view.js','timeline-data.js','timeline.js']){
    if(reference && file!=='timeline-view.js')vm.runInContext(fs.readFileSync(path.join(__dirname,'reference',file==='timeline.js'?'gui-timeline-modern-v1.js':'gui-timeline-data-v1.js'),'utf8'),world,{filename:'frozen-'+file});
    else GUI_TEST_RUNTIME.loadWebFile(world, path.join(root,'src/genshin_app/gui/web',file), {filename:file});
  }
  world.RT.renderTimeline();
  return {world,nodes,get currentCalls(){return currentCalls;}};
}
function descendants(node) {return [node,...node.children.flatMap(descendants)];}

test('dense point aggregation retains all 3000 records through bounded detail pages',()=>{
  const template={characters:[{id:'a',name:'甲角色'}],loop_duration:20,buffs:[],hits:Array.from({length:3000},(_,i)=>({
    id:`hit:${i}`,t:2,character:'a',skill:'元素爆发',hit_name:`冰伤命中 ${i}`}))};
  const {world,nodes}=renderer({template});
  assert.equal(world.TV.rows.length,1);
  assert.equal(world.TV.markerCount,1);
  const cluster=world.TV.rows[0].items[0];
  assert.equal(cluster.events.length,3000);
  const seen=[];
  for(let page=0;page<75;page++){
    world.TV.showDetail(cluster,null,page);
    const buttons=nodes.get('#timeline-detail').children.filter(n=>n.tagName==='button');
    const records=buttons.filter(n=>n.textContent.startsWith('2秒'));
    assert.equal(records.length,40);assert.ok(buttons.length<=42);
    seen.push(...records.map(n=>n.textContent));
  }
  assert.equal(new Set(seen).size,3000);
  world.TV.showDetail(cluster.events[2999],{selection:cluster,page:74});
  const back=nodes.get('#timeline-detail').children.find(n=>n.tagName==='button');
  back.listeners.get('click')[0]();
  assert.match(nodes.get('#timeline-detail').textContent,/第 75\/75 页/);
});

test('many source lanes virtualize vertically and navigation never inspects or changes input',()=>{
  const input=plain(fixtures.independent);
  const {world,nodes}=renderer(input),before=JSON.stringify(input);
  const initialCalls=world.RS.isCurrent;
  world.RS.isCurrent=()=>{throw new Error('navigation reserialized current input');};
  for(const mode of ['effects','damage','actions']){
    world.TV.changeMode(mode);world.TV.zoomAt(1.5);world.TV.zoomAt(1/1.5);
    world.TV.characterFilter='a';world.TV.rebuildRows();world.TV.paintProjection();
    world.TV.fit();nodes.get('#timeline-wrap').scrollTop=40;world.TV.requestPaint();
  }
  assert.equal(JSON.stringify(input),before);
  world.RS.isCurrent=initialCalls;
  const dense={characters:[{id:'a',name:'甲角色'}],loop_duration:20,buffs:[],hits:Array.from({length:400},(_,i)=>({
    id:`source:${i}`,t:2,character:'a',skill:`技能 ${i}`,hit_name:`命中 ${i}`}))};
  const many=renderer({template:dense});
  assert.equal(many.world.TV.rows.length,400);
  const svg=many.nodes.get('#timeline');
  const markers=()=>descendants(svg).filter(n=>Object.hasOwn(n.attributes,'data-event-id'));
  assert.ok(markers().length<20);
  many.nodes.get('#timeline-wrap').scrollTop=3600;many.world.TV.requestPaint();
  assert.ok(markers().length>0 && markers().length<20);
  assert.ok(descendants(svg).length<250);
});

test('actual failed action and coverage interpretation are visible without opening raw JSON',()=>{
  const failed=renderer(plain(fixtures.failed));
  const action=failed.world.TV.projection.views.actions.events[0];
  failed.world.TV.showDetail(action);
  assert.match(failed.nodes.get('#timeline-detail').textContent,/失败/);
  const live=renderer(plain(fixtures.independent));live.world.TV.changeMode('effects');
  const bar=live.world.TV.projection.views.effects.events.find(e=>e.kind==='coverage');
  live.world.TV.showDetail(bar);
  assert.match(live.nodes.get('#timeline-detail').textContent,/规则有效不代表每次命中都受益/);
});

test('same-time cluster retains the execution trace order after two-digit trace indices',()=>{
  const input=plain(fixtures.independent);
  const first=input.result.mechanics_trace.find(row=>row.type==='activation');
  input.result.mechanics_trace=Array.from({length:12},(_,i)=>({...first,t:1,event_id:`trigger:${i}`}));
  const {world}=renderer(input);world.TV.changeMode('effects');
  const row=world.TV.rows.find(row=>row.events.some(event=>event.kind==='activation'));
  assert.deepEqual(plain(row.events.map(event=>event.detail.trace_index)),Array.from({length:12},(_,i)=>i));
  assert.deepEqual(plain(row.items[0].events.map(event=>event.detail.trace_index)),Array.from({length:12},(_,i)=>i));
});

// The frozen implementation is deliberately unchanged. Only the NEXT-04
// additions are derived here from the independent engine input: exact parent
// action/ability identity, the already-produced explanation references, and
// consumption proven by each engine hit's final compiled-effect references.
// Every pre-existing value, timestamp, effect, ordering and numerical row still
// participates in the complete deep comparison below.
function next04FrozenProjection(frozen,input) {
  const out=plain(frozen),result=input.current?input.result:null;
  const doc=result?.damage_explanations?.contract==='damage-explanation-v1'
    && result.damage_explanations.schema_version===1?result.damage_explanations:null;
  out.explanationDocument=doc?plain(doc):null;
  out.explanationFingerprint=doc?result.damage_explanation_fingerprint??null:null;
  const rotation=result?.rotation_check?.resolved_rotation_model||input.plan?.rotation_model||{};
  const actions=new Map((rotation.actions||[]).map(a=>[a.id,a]));
  const abilities=new Map((rotation.abilities||[]).map(a=>[a.id,a]));
  const model=input.executionModel||input.plan?.mechanics_model||{};
  const descriptors=new Map((model.hit_events||[]).map(row=>[row.hit_id,row]));
  const calculated=new Map((result?.hits||[]).map(hit=>[hit.id,hit]));
  for(const view of Object.values(out.views))for(const event of view.events){
    const detail=event.detail,hitId=detail?.hit_id;
    if(!hitId)continue;
    const actionId=descriptors.get(hitId)?.action_id??null;
    const action=actions.get(actionId),ability=abilities.get(action?.ability_id);
    // These frozen fixtures contain ordinary E/Q hits only. Do not silently
    // extend this oracle to special profiles, pattern inference or new kinds.
    assert.ok(action&&ability,`fixture parent must be explicit for ${hitId}`);
    const names={'action.elemental_skill':'元素战技','action.elemental_burst':'元素爆发'};
    assert.ok(names[ability.action_type_id],'explicit migration needs an audited action type');
    const owner=out.characters.find(c=>c.id===event.character)?.label||'全队';
    event.sourceKey=JSON.stringify([event.character,'skill',ability.id]);
    event.sourceLabel=`${owner} · ${names[ability.action_type_id]}`;
    const calc=calculated.get(hitId),record=doc?.events?.[calc?.explanation_ref];
    const source=doc?.sources?.[record?.source_ref];
    detail.source_lineage={character:event.character,action_id:actionId,ability_id:ability.id,
      component_id:null,source_event_id:null,request_id:null,reaction_type:null,
      component_name_zh:null,source_skill:source?.skill??null};
    if(event.kind==='calculation')detail.action_id=actionId;
    if(event.kind==='calculation')for(const row of detail.applied_effects||[]){
      // These independent real-engine fixtures contain consumed ordinary
      // benefits only. An added nonconsuming case must have its own migration;
      // never infer consumption from the old renderer's "applied" label.
      assert.ok(Array.isArray(calc.matched_effect_refs),'engine consumer proof is required');
      assert.equal(typeof row.projected_effect_id,'string','compiled identity must exist');
      assert.equal(calc.matched_effect_refs.filter(ref=>ref.effect_id===row.projected_effect_id
        &&typeof ref.buff_id==='string').length,1,'fixture row must have one real formula consumer');
      row.consumption_status='consumed';
    }
    if(calc?.explanation_ref){
      detail.explanation_ref=calc.explanation_ref;
      detail.explanation_fingerprint=out.explanationFingerprint;
    }
  }
  return out;
}

function migratedBenefitDom(box,event){
  const count=event.kind==='calculation'?event.detail.applied_effects?.length||0:0;
  let titles=0,notes=0;
  for(const node of descendants(box)){
    if(node.tagName==='summary'&&node.text===`本次实际生效的机制收益（${count} 项）`){
      node.text=`本次公式实际消费的收益（${count} 项）`;titles++;
    }
    if(node.tagName==='p'&&node.text==='以上来自该次命中的引擎实际应用回执；共享库存的获得、消费和补充见效果轴。'){
      node.text='按本次计算保存的公式消费记录精确关联；消费表示公式读取，不代表必定带来正向边际收益。';notes++;
    }
  }
  assert.equal(titles,Number(count>0),'only the explicitly migrated benefit title may differ');
  assert.equal(notes,Number(count>0),'only the explicitly migrated benefit note may differ');
  return box;
}

function migratedCoverageRecipientHeading(box,event,projection){
  const knownRecipient=event.kind==='coverage'&&projection.characters.some(character=>
    character.id===event.character&&Boolean(character.label||character.name));
  if(knownRecipient){
    const headings=descendants(box).filter(node=>node.tagName==='dt'&&node.text==='角色：');
    assert.equal(headings.length,1,'only the coverage recipient heading may change');
    headings[0].text='接收角色：';
  }
  return box;
}

test('G4 pure stages equal the independent frozen projection on real receipts and stale inputs',()=>{
  const reference={console};reference.window=reference;vm.createContext(reference);
  vm.runInContext(fs.readFileSync(path.join(__dirname,'reference/gui-timeline-data-v1.js'),'utf8'),reference);
  for(const [name,input] of Object.entries(fixtures))for(const overrides of [{},{current:false},{executionModel:null},{result:null,current:false}]){
    const before=JSON.stringify(input),args={...input,...overrides};
    const actual=plain(load().build(args));
    // These immutable engine fixtures predate NEXT-06 and contain no report
    // window. Migrate only that declared new output slot; all older fields
    // still participate in the independent frozen comparison unchanged.
    assert.equal(Object.hasOwn(args.result||{},'damage_report_window'),false,'legacy oracle fixtures must not consume a real new report window');
    assert.equal(Object.hasOwn(actual,'reportWindow'),true);assert.equal(actual.reportWindow,null);
    delete actual.reportWindow;
    assert.deepEqual(actual,next04FrozenProjection(reference.TD.build(args),args),name+JSON.stringify(overrides));
    assert.equal(JSON.stringify(input),before,'projectors must not mutate the caller input');
  }
});

test('NEXT-06 real report window retains inclusive damage and full effect timing outside the legacy oracle',()=>{
  const input=plain(fixtures.independent),window={contract:'damage-report-window-v1',end:.4,status:'complete'};
  input.result.damage_report_window=window;const before=JSON.stringify(input),actual=plain(load().build(input));
  assert.deepEqual(actual.reportWindow,window);
  assert.deepEqual(actual.views.damage.events.filter(row=>row.kind==='damage').map(row=>row.start),[.2,.4]);
  assert.ok(actual.views.effects.events.some(row=>row.start===1.2),'tail timing stays visible outside the damage report');
  assert.ok(actual.views.damage.notes.some(note=>note.includes('DPS统计截止 0.4 秒')));
  assert.equal(JSON.stringify(input),before,'the report projection must not rewrite engine inputs');
});

test('G4 execution members and detail facts survive the N07 presentation replacement',()=>{
  const summarize=node=>({tag:node.tagName,attrs:Object.fromEntries(Object.entries(node.attributes).map(([key,value])=>[key,String(value)])),text:node.text||'',children:node.children.map(summarize)});
  for(const name of ['independent','snapshot','failed','quota_keep','initial']){
    const input=plain(fixtures[name]),before=JSON.stringify(input),actual=renderer(input),expected=renderer(plain(input),true);
    // Feed the independently migrated frozen read model to the old renderer;
    // source names are intentionally changed, but layout/packing are not.
    expected.world.TV.projection=next04FrozenProjection(expected.world.TV.projection,input);
    for(const mode of ['actions','effects','damage']){
      actual.world.TV.changeMode(mode);expected.world.TV.changeMode(mode);
      for(const pair of [actual,expected]){pair.world.TV.sourceFilter='';pair.world.TV.rebuildRows();pair.world.TV.paintProjection();}
      // N07 deliberately replaces row identities, stacking, geometry and point
      // clustering. The independent execution projection remains the oracle;
      // new DOM/geometry interactions are covered by axis-layout.test.ts.
      const visible=actual.world.TV.rows.flatMap(row=>row.events),referenceEvents=expected.world.TV.projection.views[mode].events
        .filter(event=>event.kind!=='calculation_sampling'&&(mode!=='damage'||event.kind!=='calculation'));
      assert.deepEqual(plain(visible.map(e=>e.id).sort()),plain(referenceEvents.map(e=>e.id).sort()),name+':'+mode+' all original members');
      const events=actual.world.TV.projection.views[mode].events;
      for(const event of events.slice(0,4)){
        actual.world.TV.showDetail(event);expected.world.TV.showDetail(expected.world.TV.projection.views[mode].events.find(e=>e.id===event.id));
        const box=actual.nodes.get('#timeline-detail');
        const added=box.children.filter(n=>n.tagName==='section'&&n.className==='tl-damage-explanation');
        const needsExplanation=mode==='damage'&&['damage','calculation','external_damage'].includes(event.kind);
        assert.equal(added.length,Number(needsExplanation),name+':'+mode+' new explanation section');
        if(needsExplanation){
          const section=added[0];
          assert.equal(section.children[0].textContent,'本段面板与倍率组成');
          const record=input.result.damage_explanations.events[event.detail.explanation_ref];
          assert.ok(record,'new detail must resolve its own hit reference');
          const panel=input.result.damage_explanations.panels[record.panel_ref];
          assert.ok(section.textContent.includes(`本段攻击力${panel.inputs.scaling}`));
          assert.ok(section.textContent.includes('结果来源引擎公式'));
          assert.ok(section.children.some(n=>n.tagName==='details'),'new proof details stay collapsible');
        }
        // Remove exactly the newly asserted section, not old zones, damage,
        // quota/effect/sampling detail or any other renderer output.
        const actualDetail=summarize({...box,children:box.children.filter(n=>!added.includes(n)&&n.className!=='tl-name-details')});
        const expectedEvent=expected.world.TV.projection.views[mode].events.find(e=>e.id===event.id);
        const expectedBox=migratedBenefitDom(expected.nodes.get('#timeline-detail'),expectedEvent);
        assert.deepEqual(actualDetail,summarize(migratedCoverageRecipientHeading(expectedBox,expectedEvent,expected.world.TV.projection)),name+':'+mode+' detail');
      }
    }
    assert.equal(JSON.stringify(input),before);
  }
});

test('G4 NEXT-04 references and source mapping are exact, read-only and never leak stale explanations',()=>{
  const input=plain(fixtures.independent),before=JSON.stringify(input),out=load().build(input);
  assert.equal(out.explanationDocument,input.result.damage_explanations,'one shared document, not per-event copies');
  assert.equal(out.explanationFingerprint,input.result.damage_explanation_fingerprint);
  for(const view of ['effects','damage'])for(const event of out.views[view].events.filter(e=>e.detail?.hit_id)){
    assert.equal(event.sourceKey,'["a","skill","e"]');
    assert.equal(event.sourceLabel,'角色 1 · 元素战技');
    assert.equal(event.detail.source_lineage.action_id,'cast');
    assert.equal(event.detail.source_lineage.ability_id,'e');
    assert.equal(event.detail.source_lineage.component_id,null,'no invented component identity');
    assert.equal(event.detail.explanation_ref,`hit:${event.detail.hit_id}`);
    assert.equal(event.detail.explanation_fingerprint,input.result.damage_explanation_fingerprint);
    assert.ok(input.result.damage_explanations.events[event.detail.explanation_ref]);
  }
  const stale=load().build({...input,current:false});
  assert.equal(stale.explanationDocument,null);assert.equal(stale.explanationFingerprint,null);
  assert.ok(Object.values(stale.views).every(view=>view.events.every(event=>!event.detail?.explanation_ref)));
  assert.equal(JSON.stringify(input),before);
});

test('G4 consumed-benefit fields are separately bound to final engine refs without dropping runtime values',()=>{
  for(const [name,fixture] of Object.entries(fixtures)){
    const input=plain(fixture),before=JSON.stringify(input),out=plain(load().build(input));
    for(const event of events(out,'damage','calculation')){
      const hit=input.result.hits.find(hit=>hit.id===event.detail.hit_id);
      const rows=input.result.mechanics_trace.filter(row=>row.event_id===hit.id&&row.t===hit.t
        &&['effect_applied','manual_effect_applied'].includes(row.type));
      const matches=new Set(hit.matched_effect_refs.map(ref=>ref.effect_id));
      const consumed=rows.filter(row=>matches.has(row.projected_effect_id));
      assert.deepEqual(event.detail.applied_effects.map(row=>[row.projected_effect_id,row.value,row.consumption_status]),
        consumed.map(row=>[row.projected_effect_id,row.value,'consumed']),name+':exact consumer fields');
      assert.equal(rows.length,consumed.length,'these frozen fixtures contain no unconsumed materialization');
      assert.equal(event.detail.materialized_effects,undefined);
      assert.deepEqual(event.detail.effects.filter(row=>['effect_applied','manual_effect_applied'].includes(row.type))
        .map(row=>[row.projected_effect_id,row.value]),rows.map(row=>[row.projected_effect_id,row.value]),name+':full raw materialization');
    }
    assert.equal(JSON.stringify(input),before);
  }
});
