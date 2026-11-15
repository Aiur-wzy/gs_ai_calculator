const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');
const helpers={module:{exports:{}},require,__dirname,console};
const harness=fs.readFileSync(path.join(__dirname,'gui_hit_parameters_review.test.cjs'),'utf8').split('\ntest(')[0];
vm.runInNewContext(harness+'\nmodule.exports={setup};',helpers);

test('renaming actor updates lifecycle owner and every nested selector without rewriting source identities',()=>{
  const {PL}=helpers.module.exports.setup(),plan=PL.state.plan;
  plan.mechanics_model.state_runtime={schema_version:1,lifecycles:[{
    id:'life-a',owner:'a',source_id:'source:a',start:{characters:['a','b']},
    end_trigger:{characters:['a']},hooks:{create:[{kind:'dispatch_rule',ref_id:'a'}]}}],
    timers:[{id:'timer-a',lifecycle_id:'life-a',source_id:'source:a',payload:{kind:'dispatch_rule',ref_id:'a'}}]};
  const source=JSON.stringify(plan.mechanics_model.state_runtime.timers);
  PL.renameCharacter('a','renamed');
  const state=plan.mechanics_model.state_runtime,life=state.lifecycles[0];
  assert.equal(life.owner,'renamed');assert.deepEqual(life.start.characters,['renamed','b']);
  assert.deepEqual(life.end_trigger.characters,['renamed']);assert.equal(life.source_id,'source:a');
  assert.equal(life.hooks.create[0].ref_id,'a');assert.equal(JSON.stringify(state.timers),source);
});
