// Independent user-intent review cases; shared DOM harness, separate assertions.
const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');
const helpers={module:{exports:{}},require,__dirname,console};
vm.runInNewContext(fs.readFileSync(path.join(__dirname,'gui_whiteboard.test.cjs'),'utf8').split('\ntest(')[0]
  +'\nmodule.exports={setup,Element};',helpers);
const {setup}=helpers.module.exports;
function result(){return {plan:{plan_version:3,manual_template:{characters:[{id:'a',name:'甲'}],loop_duration:20,hits:[],buffs:[]},
  mechanics_model:{rules:[],hit_events:[],hit_patterns:[],initial_state:{rules:[]}}},report:{},
  result:{summary:{total_damage:45,dps:null},hits:[],mechanics_status:'failed',mechanics_trace:[],
    rotation_check:{status:'failed',action_check:{status:'failed'},energy_check:{status:'unproven'},event_log:[]}}};}

test('an unavailable DPS is not displayed as a computed zero',()=>{
  const e=setup();e.WB.state.result=result();e.WB.render();
  assert.match(e.root.textContent,/DPS —/);assert.doesNotMatch(e.root.textContent,/DPS 0/);
  assert.match(e.root.textContent,/动作检查：失败/);
});

test('derived projection reads compiled mechanics rather than incomplete native rules',()=>{
  const e=setup(),r=result();
  r.report.mechanics_model={rules:[{id:'compiled',name_zh:'数据库编译收益',owner:'a',
    beneficiaries:{mode:'team',scope:'locked'},effects:[]}],hit_events:[],hit_patterns:[],initial_state:{rules:[]}};
  r.result.mechanics_trace=[{type:'activation',t:1,rule_id:'compiled',instance_id:'instance',event_id:'cast',cycle:0,
    beneficiaries:['a'],expires_at:[4],quota:{}}];
  e.WB.state.result=r;e.WB.state.mode='effects';
  const projected=e.WB.project(),bar=projected.events.find(item=>item.kind==='coverage');
  assert.ok(bar);assert.match(bar.label,/数据库编译收益/);assert.equal(bar.detail.owner,'a');
  assert.equal(bar.start,1);assert.equal(bar.end,4);
});

test('closing the window cannot silently discard unapplied character settings',()=>{
  const e=setup();e.WB.editCharacter('a');e.change('攻击力','3210',e.dialog());
  assert.equal(e.WB.actor('a').panel.atk,2000);
  assert.equal(e.WB.requestClose(),false);
  assert.ok(e.calls.every(call=>call.name!=='confirm_close'));
});

test('save and leave stays on the changed document when edit happens during save',async()=>{
  const e=setup();e.WB.touch();let finish;
  e.w.callApi=()=>new Promise(resolve=>finish=resolve);
  let left=false;const leaving=e.WB.leave().then(value=>{left=value;return value;});
  const dialog=e.dialog(),saving=e.press('保存后继续',dialog);
  e.WB.change(board=>board.notes='保存过程中用户新增的要求');
  finish({code:0,data:{path:'saved.whiteboard.json',file_sha:'snapshot',pending:[]}});
  await saving;assert.equal(left,false);assert.equal(e.WB.dirty(),true);
  e.press('继续编辑',dialog);assert.equal(await leaving,false);
});

test('failed save cannot approve leaving or replace the existing file identity',async()=>{
  const e=setup();e.WB.state.path='existing.whiteboard.json';e.WB.state.fileSha='original';e.WB.touch();
  e.w.callApi=async()=>({code:1,message:'文件已被外部修改',reason:'file_conflict'});
  let left=false;const leaving=e.WB.leave().then(value=>{left=value;return value;});const dialog=e.dialog();
  await e.press('保存后继续',dialog);
  assert.equal(left,false);assert.equal(e.WB.dirty(),true);assert.equal(e.WB.state.fileSha,'original');
  e.press('继续编辑',dialog);assert.equal(await leaving,false);
});
