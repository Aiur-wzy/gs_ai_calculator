const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');
const Python = require('./helpers/python_command.cjs');
const helpers={module:{exports:{}},require,__dirname,console};
const harness=fs.readFileSync(path.join(__dirname,'gui_passive_sources.test.cjs'),'utf8').split('\ntest(')[0];
vm.runInNewContext(harness+'\nmodule.exports={setup,press,control,clone};',helpers);
const {setup,press,control,clone}=helpers.module.exports;
const caps=JSON.parse(Python.execFileSync(['-X','utf8','-c',
 'import json; from genshin_engine.runtime_passive_attribute_compiler import passive_attribute_compiler_caps; print(json.dumps(passive_attribute_compiler_caps()))'],
 {cwd:path.join(__dirname,'..'),encoding:'utf8'}));
caps.confirmable_issue_codes=['passive_confirmation_required'];
function dynamic(){const e=setup();e.PL.state.caps.passive_attribute_source_contract=clone(caps);return e;}

test('dynamic mode creates an explicit unknown draft and never upgrades the native model',async()=>{
 const e=dynamic(),model=clone(e.p.mechanics_model);await e.ready();
 e.field('属性取样模式',caps.profile_id);
 assert.equal(control(e.dialog(),'提供者非额外精通口径').value,'unknown');
 assert.equal(control(e.dialog(),'同次反应取样资格').value,'unknown');
 assert.ok(e.dialog().textContent.includes('不会自动改写旧模型'));
 e.world.callApi=async(_,p)=>{const r=e.response(p);r.data.passive_compilations[0].profile_id=caps.profile_id;return r;};
 await press(e.dialog(),'应用固有天赋草稿');
 assert.equal(e.p.passive_sources[0].scenario.profile_id,caps.profile_id);
 assert.equal(e.p.passive_sources[0].scenario.stat_basis,'unknown');
 assert.equal(e.p.passive_sources[0].scenario.same_proc_em_policy,'unknown');
 assert.equal(e.p.passive_sources[0].accepted_fingerprint,null);
 assert.deepEqual(clone(e.p.mechanics_model),model);
});

test('a legacy profile receipt cannot authorize or save a dynamic source',async()=>{
 const e=dynamic();await e.ready();e.field('属性取样模式',caps.profile_id);
 await press(e.dialog(),'应用固有天赋草稿');
 assert.equal(e.p.passive_sources,undefined);
 assert.ok(e.dialog().textContent.includes('缺少可核验的固有来源回执'));
});

test('switching back restores legacy contract fields with no leftover sampling claim',async()=>{
 const e=dynamic();await e.ready();e.field('属性取样模式',caps.profile_id);
 e.field('提供者非额外精通口径','attribute_ledger_nonextra');
 e.field('同次反应取样资格','no_same_proc_em_changes');
 e.field('属性取样模式','inherent-ordinary-swirl-bonus-v1');
 await press(e.dialog(),'应用固有天赋草稿');
 const row=e.p.passive_sources[0];
 assert.equal(row.scenario.stat_basis,'unknown');
 assert.equal(row.scenario.target_state_profile,e.PL.state.caps.passive_source_contract.default_scenario.target_state_profile);
 assert.equal(row.scenario.same_proc_em_policy,undefined);
 assert.equal(row.accepted_fingerprint,null);
});
