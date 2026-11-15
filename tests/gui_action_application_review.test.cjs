const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');
const helpers={module:{exports:{}},require,__dirname,console};
vm.runInNewContext(fs.readFileSync(path.join(__dirname,'gui_action_events.test.cjs'),'utf8').split('\ntest(')[0]+
 '\nmodule.exports={fixture,press,control,clone};',helpers);
const {fixture,press,control,clone}=helpers.module.exports;
function dynamic(){const e=fixture();e.PL.state.caps.action_event_contract.profile_kinds=['calibrated_single_contact_v1','calibrated_single_application_v2'];e.p.mechanics_model.elemental_model.ruleset='gauge-swirl-dynamic-v3';return e;}
function fill(e){e.AE.editProfile();e.field('共享命中原型','prototype');e.field('动作变体声明','tap');e.field('从动作开始到接触的秒数',.2);e.field('记录精度（秒）',.01);e.field('校准来源、固定状态和适用范围','Independent explicit calibration');e.field('附着档案','ice');}

test('new v3 profile is application-only and leaves actual model unchanged',async()=>{
 const e=dynamic(),model=clone(e.p.mechanics_model);fill(e);
 assert.equal(control(e.dialog(),'接触档案'),undefined);
 await press(e,'应用档案草稿');const row=e.p.action_event_profiles[0];
 assert.equal(row.kind,'calibrated_single_application_v2');
 assert.equal(Object.hasOwn(row,'contact_profile_id'),false);
 assert.equal(row.default_landed,null);assert.equal(row.accepted_profile_fingerprint,null);
 assert.deepEqual(clone(e.p.mechanics_model),model);
});

test('existing legacy profile stays legacy when editing on a v3 model',async()=>{
 const e=fixture();e.filled();await press(e,'应用档案草稿');
 const before=clone(e.p.action_event_profiles[0]);e.PL.state.caps.action_event_contract.profile_kinds=['calibrated_single_application_v2'];
 e.p.mechanics_model.elemental_model.ruleset='gauge-swirl-dynamic-v3';
 e.AE.editProfile(0);assert.ok(control(e.dialog(),'接触档案'));
 await press(e,'应用档案草稿');assert.deepEqual(clone(e.p.action_event_profiles[0]),before);
});

test('actual bound v2 action accepts explicit miss override while unbound action is not selectable',async()=>{
 const e=dynamic();fill(e);await press(e,'应用档案草稿');
 e.AE.editBinding();e.field('绑定的战技能力','e');e.field('采用的动作档案',e.p.action_event_profiles[0].id);await press(e,'应用档案草稿');
 e.p.rotation_model.actions.push({id:'unbound',ability_id:'other',start:9});e.AE.editOverride();
 assert.ok(!control(e.dialog(),'本轴实际动作').children.map(x=>x.value).includes('unbound'));
 e.field('本轴实际动作','a2');e.field('本次命中例外','false');e.field('本次例外的依据','Independent actual miss');
 await press(e,'应用档案草稿');assert.equal(e.p.action_event_overrides[0].action_id,'a2');
 assert.equal(e.p.action_event_overrides[0].landed,false);
 assert.equal(e.p.action_event_profiles[0].kind,'calibrated_single_application_v2');
});
