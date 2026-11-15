import {test} from "node:test";
import assert from "node:assert/strict";
import {legacyDomainFixture} from "./legacy-domain-test-fixture.mjs";
test("candidate projection reuses TD and never changes the formal whiteboard",async t=>{
  const formal={team:[{name:"正式角色"}],actions:[]};
  const {context,close}=await legacyDomainFixture(["candidate-projection.js"],{WB:{state:{board:formal}}});t.after(close);
  const board={kind:"genshin-whiteboard",duration:18,team:[{id:"actor",name:"候选角色"}],actions:[{id:"a",character:"actor",action_type_id:"action.elemental_skill",start:2}]};
  const before=JSON.stringify(board),value=context.window.CandidateDocumentProjection.build(board);
  assert.equal(value.characters[0].label,"候选角色");assert.equal(value.views.actions.events[0].label,"元素战技");assert.equal(value.views.actions.events[0].start,2);assert.equal(JSON.stringify(board),before);assert.equal(formal.team[0].name,"正式角色");
});
test("candidate calculated projection displays Host damage records without applying or recalculating",async t=>{
  const formal={title:"正式版"};
  const {context,close}=await legacyDomainFixture(["candidate-projection.js"],{WB:{state:{board:formal}}});t.after(close);
  const board={kind:"genshin-whiteboard",duration:18,team:[{id:"actor",name:"候选角色"}],actions:[{id:"a",character:"actor",action_type_id:"action.elemental_skill",start:2}]},hit={id:"h",t:2.1,character:"actor",skill:"候选战技",hit_name:"命中",element:"cryo"};
  const calculated={plan:{manual_template:{characters:[{id:"actor"}],loop_duration:18,hits:[hit],buffs:[]}},result:{hits:[{...hit,expected:42}],summary:{total_damage:42}},report:{}};
  const before=JSON.stringify(calculated),value=context.window.CandidateDocumentProjection.build(board,calculated);
  assert.equal(value.views.damage.events.find(row=>row.kind==="damage").value,42);assert.equal(value.views.actions.events[0].start,2);assert.equal(JSON.stringify(calculated),before);assert.equal(formal.title,"正式版");
});
