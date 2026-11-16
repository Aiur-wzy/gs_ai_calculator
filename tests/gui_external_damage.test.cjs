const GUI_TEST_RUNTIME = require('./helpers/gui_runtime.cjs');
const Python = require('./helpers/python_command.cjs');
const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),vm=require('node:vm'),crypto=require('node:crypto');
const helpers={module:{exports:{}},require,__dirname,console};
vm.runInNewContext(fs.readFileSync(path.join(__dirname,'gui_hit_parameters_review.test.cjs'),'utf8').split('\ntest(')[0]+'\nmodule.exports={setup,press,control,Element};',helpers);
const {setup:base,press,control,Element}=helpers.module.exports,clone=value=>JSON.parse(JSON.stringify(value)),all=node=>[node,...node.children.flatMap(all)];
const root=path.join(__dirname,'..'),web=path.join(root,'src/genshin_app/gui/web');
const caps=JSON.parse(Python.execFileSync(['-X','utf8','-c',
  'import json,runpy; d=runpy.run_path("src/genshin_app/equipment/external_damage_schema.py"); print(json.dumps(d["external_damage_contract"]()))'],{cwd:root,encoding:'utf8',windowsHide:true}));
function setup(){
  const e=base(),w=e.world,p=e.PL.state.plan,calls=[],statuses=[];let stamp=1;
  e.PL.state.caps.external_damage_contract=clone(caps);
  for(const file of ['action-events','external-damage','timeline-data','result'])GUI_TEST_RUNTIME.loadWebFile(w, path.join(web,file+'.js'));
  w.setStatus=message=>statuses.push(message);
  const response=plan=>{
    const items=(plan.external_damage_supplements||[]).map(item=>{
      const content=clone(item);delete content.accepted_fingerprint;
      const fp=crypto.createHash('sha256').update(JSON.stringify([content,stamp])).digest('hex');
      return {id:item.id,status:item.accepted_fingerprint===fp?'ready':'pending',accepted_fingerprint:fp,
        required_confirmation:{accepted_fingerprint:fp},issues:item.accepted_fingerprint===fp?[]:[{code:'external_damage_confirmation_required',message:'需复核'}]};
    });
    return {code:0,data:{state:'pending',plan:{poison:true},external_damage_review:items.length?{
      contract:'external-damage-v1',status:items.every(item=>item.status==='ready')?'ready':'pending',items,issues:items.flatMap(item=>item.issues)}:null}};
  };
  w.callApi=async(name,plan)=>{calls.push({name,plan:clone(plan)});return response(plan);};
  const ready=()=>{w.XD.edit();for(const [key,value] of Object.entries({'补充显示名':'独立伤害','伤害归属角色':'a','技能或伤害类别名称':'独立计算',
    '发生时点（秒）':1,'外部期望伤害（非负）':12,'覆盖范围唯一键':'coverage.unique','补充伤害事件唯一标识':'external.unique',
    '为何需要外部补充':'引擎范围之外','计算过程与假设':'独立算术 3 × 4','与引擎及其他补充不重复的依据':'该事件不在引擎命中账',
    '来源引用（每行一项）':'事实文件#一\n公开来源#二'}))e.field(key,value);};
  return {...e,w,p,calls,statuses,response,ready,sourceChanged:()=>stamp++};
}
async function applied(e){e.ready();await press(e.dialog(),'预览外部伤害复核');await press(e.dialog(),'确认本次外部计算');await press(e.dialog(),'应用外部补充草稿');}

test('caps default is preserved without auto selection or acceptance; valid draft remains unconfirmed',async()=>{
  const e=setup();e.w.XD.edit();assert.equal(control(e.dialog(),'伤害归属角色').value,'');
  assert.equal(control(e.dialog(),'外部期望伤害（非负）').value,0);assert.equal(e.p.external_damage_supplements,undefined);
  press(e.dialog(),'放弃并关闭');e.ready();await press(e.dialog(),'应用外部补充草稿');
  assert.equal(e.p.external_damage_supplements[0].accepted_fingerprint,null);assert.equal(e.p.resolution.state,'pending');
});
test('fresh preview and confirmation save only native item, preserve all other inputs and order',async()=>{
  const e=setup(),before=clone(e.p);await applied(e);const after=clone(e.p),items=after.external_damage_supplements;
  assert.match(items[0].accepted_fingerprint,/^[0-9a-f]{64}$/);assert.deepEqual(items[0].source_refs,['事实文件#一','公开来源#二']);
  assert.equal(items[0].poison,undefined);delete after.external_damage_supplements;
  before.resolution={state:'pending'};before.resolved_template=null;delete before.revision;delete after.revision;assert.deepEqual(after,before);
});
test('confirmation cannot occur without shown preview and source change invalidates it',async()=>{
  const e=setup();e.ready();await press(e.dialog(),'确认本次外部计算');assert.equal(e.calls.length,0);
  await press(e.dialog(),'预览外部伤害复核');e.sourceChanged();await press(e.dialog(),'确认本次外部计算');
  assert.match(e.dialog().textContent,/复核已失效/);assert.equal(e.p.external_damage_supplements,undefined);
  await press(e.dialog(),'确认本次外部计算');await press(e.dialog(),'应用外部补充草稿');assert.ok(e.p.external_damage_supplements[0].accepted_fingerprint);
});
test('editing a value clears local acceptance; reopening unchanged preserves it',async()=>{
  const e=setup();await applied(e);const accepted=e.p.external_damage_supplements[0].accepted_fingerprint;
  e.w.XD.edit(0);await press(e.dialog(),'应用外部补充草稿');assert.equal(e.p.external_damage_supplements[0].accepted_fingerprint,accepted);
  e.w.XD.edit(0);e.field('外部期望伤害（非负）',18);await press(e.dialog(),'应用外部补充草稿');assert.equal(e.p.external_damage_supplements[0].accepted_fingerprint,null);
});
test('unknown native fields are preserved for backend rejection, never silently removed',async()=>{
  const e=setup();await applied(e);e.p.external_damage_supplements[0].future={meaning:'unrecognized'};e.w.XD.edit(0);
  let sent;e.w.callApi=async(_,plan)=>{sent=clone(plan);return {code:1,message:'未知字段'};};await press(e.dialog(),'应用外部补充草稿');
  assert.deepEqual(sent.external_damage_supplements[0].future,{meaning:'unrecognized'});assert.equal(e.p.external_damage_supplements.length,1);
});
for(const mutation of ['input','path','sha','epoch','caps','replacement','draft','close','cancel'])test(`stale ${mutation} response cannot commit`,async()=>{
  const e=setup();e.ready();let finish,submitted;e.w.callApi=(_,p)=>{submitted=clone(p);return new Promise(resolve=>finish=resolve);};
  const waiting=press(e.dialog(),'应用外部补充草稿');
  if(mutation==='input')e.p.manual_template.meta.name='changed';if(mutation==='path')e.RT.state.path='new';
  if(mutation==='sha')e.PL.state.fileSha='new';if(mutation==='epoch')e.PL.state.sourceEpoch++;
  if(mutation==='caps')e.PL.state.caps.external_damage_contract.contract='new';if(mutation==='replacement')e.PL.state.plan=clone(e.p);
  if(mutation==='draft')e.PL.state.draft=clone(e.p);if(mutation==='close')press(e.dialog(),'放弃并关闭');if(mutation==='cancel')e.dialog().emit('cancel');
  finish(e.response(submitted));await waiting;assert.equal(e.p.external_damage_supplements,undefined);
});
test('newer preview wins over delayed response after item edit',async()=>{
  const e=setup();e.ready();const pending=[];e.w.callApi=(_,plan)=>new Promise(resolve=>pending.push({plan:clone(plan),resolve}));
  const first=press(e.dialog(),'预览外部伤害复核');e.field('外部期望伤害（非负）',23);const second=press(e.dialog(),'预览外部伤害复核');
  pending[1].resolve(e.response(pending[1].plan));await second;pending[0].resolve(e.response(pending[0].plan));await first;
  e.w.callApi=async(_,plan)=>e.response(plan);await press(e.dialog(),'确认本次外部计算');await press(e.dialog(),'应用外部补充草稿');assert.equal(e.p.external_damage_supplements[0].expected,23);
});
for(const fault of ['contract','missing','duplicate','wrongid','invalid','unknownissue','nullhash'])test(`malformed or unconfirmable ${fault} review never accepts`,async()=>{
  const e=setup();e.ready();e.w.callApi=async(_,plan)=>{const r=e.response(plan),review=r.data.external_damage_review,row=review.items[0];
    if(fault==='contract')review.contract='future';if(fault==='missing')review.items=[];if(fault==='duplicate')review.items.push(clone(row));
    if(fault==='wrongid')row.id='wrong';if(fault==='invalid')r.data.state='invalid';
    if(fault==='unknownissue')row.issues=[{code:'unsupported',message:'不支持'}];if(fault==='nullhash')row.required_confirmation=null;return r;};
  await press(e.dialog(),'预览外部伤害复核');await press(e.dialog(),'确认本次外部计算');assert.equal(e.p.external_damage_supplements,undefined);
});
test('two supplement confirmations are independent and deletion is atomic including orphan',async()=>{
  const e=setup();await applied(e);const first=clone(e.p.external_damage_supplements[0]);
  e.ready();e.field('覆盖范围唯一键','other');e.field('补充伤害事件唯一标识','other');
  await press(e.dialog(),'预览外部伤害复核');await press(e.dialog(),'确认本次外部计算');await press(e.dialog(),'应用外部补充草稿');
  assert.deepEqual(clone(e.p.external_damage_supplements[0]),first);e.p.external_damage_supplements[1].character='removed';
  const before=clone(e.p);e.w.XD.edit(1);e.w.callApi=async()=>({code:1,message:'失败'});await press(e.dialog(),'移除此外部补充');assert.deepEqual(clone(e.p),before);
  e.w.callApi=async(_,plan)=>e.response(plan);await press(e.dialog(),'移除此外部补充');assert.deepEqual(clone(e.p.external_damage_supplements),[first]);
  e.w.XD.edit(0);await press(e.dialog(),'移除此外部补充');assert.deepEqual(clone(e.p.external_damage_supplements),[]);
});
test('result keeps engine details and displays separately merged totals and external rows',()=>{
  const e=setup(),engine={total_damage:40,dps:4,by_character:{a:40},by_skill:{skill:40}},
    totals={total_damage:52,dps:5.2,engine_total_damage:40,external_total_damage:12,external_count:1,by_character:{a:52},by_skill:{skill:40,external:12}},
    item={...clone(caps.default_item),id:'x',label:'独立伤害',character:'a',skill:'外部',t:1,expected:12,source_refs:['依据'],reason:'独立'},
    result={summary:engine,hits:[],calculation_totals:totals,external_damage:{items:[item]}};
  const before=clone(result),box=new Element();e.w.XD.renderResult(box,result);
  assert.match(box.textContent,/外部计算伤害明细（1）/);assert.match(box.textContent,/不会产生增益/);
  const summary=e.w.RS.renderSummary(e.w.RS.totalSummary(result));
  for(const text of ['含外部计算补充','引擎计算伤害','外部计算伤害','外部补充项数','52','5'])assert.ok(summary.textContent.includes(text),text);
  assert.deepEqual(result,before);assert.equal(e.w.RS.totalSummary({summary:engine}),engine);
});
test('timeline external values are distinct read-only damage points, never hits or buffs; stale hides them',()=>{
  const e=setup(),args={plan:e.p,template:e.p.manual_template,current:true,result:{hits:[],external_damage:{items:[{id:'ext',label:'独立值',character:'a',skill:'独立',t:1,expected:12}]}}};
  const built=clone(e.w.TD.build(args)),points=built.views.damage.events.filter(x=>x.kind==='external_damage');assert.equal(points.length,1);assert.equal(points[0].value,12);assert.equal(points[0].edit,undefined);
  assert.ok(built.views.effects.events.every(x=>x.id!=='external:ext'));assert.ok(built.views.actions.events.every(x=>x.id!=='external:ext'));
  assert.equal(e.w.TD.build({...args,current:false}).views.damage.events.filter(x=>x.kind==='external_damage').length,0);
});
test('CSV and lossy manual copy are blocked before calling API when external ledger exists',async()=>{
  const e=setup();e.w.RS.isCurrent=()=>true;e.w.RS.state.result={external_damage:{items:[{}]}};
  await e.w.RS.exportResult('csv');assert.equal(e.calls.length,0);assert.match(e.statuses.at(-1),/CSV 不支持/);
  e.p.external_damage_supplements=[{}];await e.PL.manualCopy();assert.equal(e.calls.length,0);assert.match(e.statuses.at(-1),/丢失补充账/);
});
test('registered Chinese skill aliases merge only in display, keeping owner scope and all raw keys',()=>{
  const e=setup(),values={burst:20,'元素爆发':30,unregistered:4,another_unknown:5,'a.burst':7,'a.元素爆发':8};
  const before=clone(values),rows=clone(e.w.RS.skillGroups(values));
  assert.equal(rows.length,4);assert.deepEqual(rows.find(row=>row.value===50).raw_keys,['burst','元素爆发']);
  assert.deepEqual(rows.find(row=>row.value===15).raw_keys,['a.burst','a.元素爆发']);
  assert.equal(rows.filter(row=>row.name==='技能类别待补').length,2);assert.deepEqual(values,before);
  const table=e.w.RS.miniTable('按技能',values,'skill');assert.match(table.textContent,/技能汇总原始键/);assert.match(table.textContent,/unregistered/);
});
test('whole page loads editor before app and both result and native checks expose entry',()=>{
  const e=setup(),html=fs.readFileSync(path.join(web,'index.html'),'utf8');assert.ok(html.indexOf('external-damage.js')<html.indexOf('app.js'));
  e.w.RC.shown=true;e.w.RC.render();assert.match(e.w.document.querySelector('#rotation-checks').textContent,/添加外部伤害补充/);
  e.w.RS.renderAll();assert.match(e.w.document.querySelector('#result-body').textContent,/添加外部伤害补充/);assert.match(e.w.document.querySelector('#result-body').textContent,/当前计算模型尚未计算。点击「计算」查看结果。/);
});
test('external supplement rows use one contextual command and relocate by stable id',()=>{
  const e=setup(),opened=[],calls=[];e.w.AppContextMenu={forDocument:()=>({open:config=>{opened.push(config);return true;},bind(){}})};
  e.p.external_damage_supplements=[{id:'first',label:'第一项'},{id:'second',label:'第二项'}];e.w.XD.edit=index=>calls.push(index);
  const box=new Element();e.w.XD.render(box);const buttons=all(box).filter(node=>node.tagName==='button');
  assert.deepEqual(buttons.map(node=>node.textContent),['添加外部伤害补充','⋯','⋯']);buttons[1].emit('click');
  e.p.external_damage_supplements.reverse();opened.at(-1).items.find(item=>item.id==='edit').run();assert.deepEqual(calls,[1]);
  e.p.external_damage_supplements=e.p.external_damage_supplements.filter(row=>row.id!=='first');opened.at(-1).items.find(item=>item.id==='edit').run();
  assert.deepEqual(calls,[1]);assert.match(e.statuses.at(-1),/已变化或移除/);
});

function python(operation,plan){return JSON.parse(Python.execFileSync(['-X','utf8','-m','tests.external_damage_gui_bridge'],
  {cwd:root,encoding:'utf8',input:JSON.stringify({operation,plan}),windowsHide:true}));}
test('real compose→GUI confirmation→actual calculation displays 45 engine + 12 external, then panel edit stales acceptance',async()=>{
  const e=setup(),initial=python('init');assert.equal(initial.code,0);e.PL.replace(clone(initial.data.plan),'real-native.json','sha',true);
  e.PL.state.caps.external_damage_contract=initial.data.contract;
  e.w.callApi=async(name,plan)=>python(name==='compose_equipment_plan'?'compose':'calc',clone(plan));
  e.w.XD.edit(0);await press(e.dialog(),'预览外部伤害复核');assert.match(e.dialog().textContent,/需核对当前输入/);
  await press(e.dialog(),'确认本次外部计算');await press(e.dialog(),'应用外部补充草稿');
  const plan=e.PL.state.plan;assert.match(plan.external_damage_supplements[0].accepted_fingerprint,/^[a-f0-9]{64}$/);
  const actual=python('calc',plan);assert.equal(actual.code,0,actual.message);assert.equal(actual.data.result.summary.total_damage,45);
  assert.equal(actual.data.result.calculation_totals.total_damage,57);assert.equal(actual.data.result.calculation_totals.dps,28.5);
  e.w.RS.state.snapshot={template:clone(plan)};
  const summary=e.w.RS.renderSummary(e.w.RS.totalSummary(actual.data.result)),box=new Element();e.w.XD.renderResult(box,actual.data.result);
  assert.match(summary.textContent,/57/);assert.match(summary.textContent,/45/);assert.match(summary.textContent,/12/);assert.match(box.textContent,/工程常量3乘4/);
  const timeline=e.w.TD.build({plan,template:plan.manual_template,result:actual.data.result,current:true});
  assert.equal(timeline.views.damage.events.filter(x=>x.kind==='external_damage').length,1);
  plan.manual_template.characters[0].stats.atk=101;e.w.XD.edit(0);await press(e.dialog(),'应用外部补充草稿');
  assert.match(e.dialog().textContent,/复核已失效/);await press(e.dialog(),'应用外部补充草稿');
  assert.equal(plan.external_damage_supplements[0].accepted_fingerprint,null);
});
test('actual backend rejects duplicate coverage, invalid actor, negative value and unrecognized fields without native mutation',async()=>{
  const initial=python('init').data;
  for(const mutation of ['duplicate','actor','negative','unknown']){
    const e=setup(),plan=clone(initial.plan);e.PL.replace(plan,'native.json','sha',true);
    if(mutation==='duplicate')plan.external_damage_supplements.push({...clone(plan.external_damage_supplements[0]),id:'other',event_id:'other'});
    if(mutation==='actor')plan.external_damage_supplements[0].character='missing';
    if(mutation==='negative')plan.external_damage_supplements[0].expected=-1;
    if(mutation==='unknown')plan.external_damage_supplements[0].unknown='retained';
    e.RT.syncRevision();const before=clone(plan);e.w.callApi=async(_,value)=>python('compose',value);e.w.XD.edit(0);
    await press(e.dialog(),'预览外部伤害复核');await press(e.dialog(),'确认本次外部计算');assert.deepEqual(clone(plan),before);
    assert.equal(plan.external_damage_supplements[0].accepted_fingerprint,null);
  }
});
