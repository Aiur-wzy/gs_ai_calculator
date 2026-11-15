const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');
const helpers={module:{exports:{}},require,__dirname,console};
const harness=fs.readFileSync(path.join(__dirname,'gui_hit_parameters_review.test.cjs'),'utf8').split('\ntest(')[0];
vm.runInNewContext(harness+'\nmodule.exports={setup,press,control,clone};',helpers);
const {setup,press,control,clone}=helpers.module.exports;
function levelEnv(){
 const e=setup();e.PL.state.caps={hit_parameter_source_contract:{level_contract:'level-bound-parameter-v1'}};
 const original=e.response;e.response=p=>{const out=original(p);out.data.hit_parameter_compilations.forEach((r,i)=>{
   const source=p.hit_parameter_sources[i];if(source.parameter_contract){r.parameter_contract=source.parameter_contract;
    r.level={base_level:source.selection.base_level??10,extra_level:3,effective_level:13};r.level_profile={status:'ready',issues:[]};
    r.required_confirmation.expected_level_fingerprint='level-'+source.selection.constellation;}
 });return out;};
 e.world.pywebview.api.compose_equipment_plan=async p=>e.response(p);return e;
}
test('new supported binding defaults to source-bound base level and confirms the full profile',async()=>{
 const e=levelEnv();e.open();e.field('已激活命座数',3);e.field('倍率用途核实状态','assumed');
 assert.equal(control(e.dialog(),'基础天赋等级（1至10，留空采用10级）').value,10);
 await press(e.dialog(),'读取并预览当前技能参数');assert.match(e.dialog().textContent,/其它命座修改尚未执行/);
 await press(e.dialog(),'确认所示数值来源');await press(e.dialog(),'应用倍率来源绑定');
 const row=e.PL.state.plan.hit_parameter_sources[0];assert.equal(row.parameter_contract,'level-bound-parameter-v1');
 assert.equal(row.expected_level_fingerprint,'level-3');assert.equal(row.selection.base_level,10);assert.ok(!('talent_level' in row.selection));
 assert.equal(row.status,'assumed');
});
test('changing mode invalidates an in-flight new-level preview and drops only version fields',async()=>{
 const e=levelEnv();e.open();let finish,proposed;
 e.world.pywebview.api.compose_equipment_plan=p=>{proposed=clone(p);return new Promise(r=>finish=r);};
 const pending=press(e.dialog(),'读取并预览当前技能参数');e.field('参数等级模式','legacy');finish(e.response(proposed));await pending;
 e.world.pywebview.api.compose_equipment_plan=async p=>e.response(p);await press(e.dialog(),'应用倍率来源绑定');
 const row=e.PL.state.plan.hit_parameter_sources[0];assert.ok(!('parameter_contract' in row));assert.ok(!('expected_level_fingerprint' in row));
 assert.deepEqual(JSON.parse(JSON.stringify(row.selection)),{character_id:10000054,slot:'skill',constellation:0,talent_level:10});
 assert.equal(row.expected_snapshot_id,null);
});
test('legacy binding reopens as legacy even when new backend capability exists',async()=>{
 const e=setup();e.open();await press(e.dialog(),'应用倍率来源绑定');const before=JSON.stringify(e.PL.state.plan.hit_parameter_sources);
 e.PL.state.caps={hit_parameter_source_contract:{level_contract:'level-bound-parameter-v1'}};e.MC.editHitParameterSource(0);
 assert.equal(control(e.dialog(),'参数等级模式').value,'legacy');await press(e.dialog(),'放弃并关闭');
 assert.equal(JSON.stringify(e.PL.state.plan.hit_parameter_sources),before);
});
test('a ready scalar with pending level profile cannot be confirmed',async()=>{
 const e=levelEnv();e.open();e.world.pywebview.api.compose_equipment_plan=async p=>{const out=e.response(p);out.data.hit_parameter_compilations[0].level_profile.status='pending';return out;};
 await press(e.dialog(),'读取并预览当前技能参数');
 assert.equal(e.dialog().children.length>0,true);
 const find=n=>[n,...n.children.flatMap(find)];
 assert.equal(find(e.dialog()).find(n=>n.tagName==='button'&&n.textContent==='确认所示数值来源').disabled,true);
});
