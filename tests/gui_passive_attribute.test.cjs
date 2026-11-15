const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');
const Python = require('./helpers/python_command.cjs');
const helpers={module:{exports:{}},require,__dirname,console};
vm.runInNewContext(fs.readFileSync(path.join(__dirname,'gui_passive_sources.test.cjs'),'utf8').split('\ntest(')[0]+'\nmodule.exports={setup,press,Element};',helpers);
const {setup,press,Element}=helpers.module.exports;
const dynamic=JSON.parse(Python.execFileSync(['-X','utf8','-c',
 'import json;from genshin_engine.runtime_passive_attribute_compiler import passive_attribute_compiler_caps;print(json.dumps(passive_attribute_compiler_caps()))'],
 {cwd:path.join(__dirname,'..'),encoding:'utf8'}));
dynamic.confirmable_issue_codes=['passive_confirmation_required'];
test('dynamic mode changes explicit contract, resets acknowledgements and preserves visible common fields',async()=>{
 const e=setup();e.PL.state.caps.passive_attribute_source_contract=dynamic;await e.ready();
 e.field('属性取样模式',dynamic.profile_id);
 e.field('提供者非额外精通口径','attribute_ledger_nonextra');e.field('同次反应取样资格','no_same_proc_em_changes');
 let submitted;e.world.callApi=async(_,p)=>{submitted=p;const result=e.response(p);result.data.passive_compilations[0].profile_id=dynamic.profile_id;return result;};
 await press(e.dialog(),'预览固有来源与实际资格');
 const source=submitted.passive_sources[0];assert.equal(source.scenario.profile_id,dynamic.profile_id);
 assert.equal(source.scenario.target_state_profile,'gauge-swirl-dynamic-v3');assert.equal(source.scenario.target_id,'enemy');
 assert.equal(source.scenario.stat_basis,'attribute_ledger_nonextra');assert.equal(source.accepted_fingerprint,null);
 assert.equal(e.p.passive_sources,undefined);assert.match(e.dialog().textContent,/实际值见计算取样账/);assert.doesNotMatch(e.dialog().textContent,/36\.00%/);
 e.field('属性取样模式',e.world.PB.profile);await press(e.dialog(),'预览固有来源与实际资格');
 assert.equal(submitted.passive_sources[0].scenario.stat_basis,'unknown');assert.equal(submitted.passive_sources[0].scenario.same_proc_em_policy,undefined);
});
test('dynamic readonly preview never multiplies initial total EM into a fixed bonus',()=>{
 const e=setup(),box=new Element();e.world.PB.recordView(box,{profile_id:dynamic.profile_id,status:'pending',stat_basis:{panel_value:900},
  preview_rules:[{name_zh:'冰元素伤害加成',timing:{duration:8},effects:[{value:{factor:.0004}}]}]});
 assert.match(box.textContent,/非额外精通/);assert.match(box.textContent,/8 秒/);assert.doesNotMatch(box.textContent,/36\.00%/);
});
