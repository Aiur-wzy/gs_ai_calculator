const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');
// Reuse the DOM fixture, not its assertions or a model/network connection.
const sandbox={module:{exports:{}},require,__dirname,console};
const source=fs.readFileSync(path.join(__dirname,'gui_talent_levels.test.cjs'),'utf8').split('\ntest(')[0];
vm.runInNewContext(source+'\nmodule.exports={levelEnv,press,control,clone};',sandbox);
const {levelEnv,press,control,clone}=sandbox.module.exports;

test('independent: changed level fingerprint during confirmation clears all source approval',async()=>{
  const e=levelEnv();e.open();e.field('已激活命座数',3);e.field('倍率用途核实状态','assumed');
  await press(e.dialog(),'读取并预览当前技能参数');
  e.world.pywebview.api.compose_equipment_plan=async p=>{const out=e.response(p);
    out.data.hit_parameter_compilations[0].required_confirmation.expected_level_fingerprint='different-current-level';return out;};
  await press(e.dialog(),'确认所示数值来源');
  assert.match(e.dialog().textContent,/来源已变化/);
  await press(e.dialog(),'应用倍率来源绑定');
  const row=e.PL.state.plan.hit_parameter_sources[0];
  assert.equal(row.expected_snapshot_id,null);assert.equal(row.expected_node_sha256,null);assert.equal(row.expected_level_fingerprint,null);
  assert.equal(row.status,'assumed');assert.ok(!('multiplier' in e.PL.state.plan.manual_template.hits[0]));
});

test('independent: a legacy binding remains legacy after reopening and applying with new capability',async()=>{
  const e=levelEnv();e.open();e.field('参数等级模式','legacy');await press(e.dialog(),'应用倍率来源绑定');
  const before=clone(e.PL.state.plan.hit_parameter_sources);
  e.MC.editHitParameterSource(0);assert.equal(control(e.dialog(),'参数等级模式').value,'legacy');
  await press(e.dialog(),'应用倍率来源绑定');
  assert.deepEqual(clone(e.PL.state.plan.hit_parameter_sources),before);
  assert.ok(!('parameter_contract' in before[0]));assert.ok(!('expected_level_fingerprint' in before[0]));
});

test('independent: a ready scalar without the versioned level contract is not confirmable',async()=>{
  const e=levelEnv();e.open();e.world.pywebview.api.compose_equipment_plan=async p=>{const out=e.response(p);
    delete out.data.hit_parameter_compilations[0].parameter_contract;return out;};
  await press(e.dialog(),'读取并预览当前技能参数');
  const nodes=n=>[n,...n.children.flatMap(nodes)];
  assert.equal(nodes(e.dialog()).find(n=>n.tagName==='button'&&n.textContent==='确认所示数值来源').disabled,true);
});
