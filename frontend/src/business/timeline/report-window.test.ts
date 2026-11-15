import {describe,expect,it} from "vitest";
import {buildTimelineProjection} from "./index";

function input(){
  const template={loop_duration:2,characters:[{id:"a",name:"工程角色"}],buffs:[],hits:[
    {id:"direct",t:2,character:"a",skill:"直接命中",element:"cryo"},
    {id:"tail",t:9,character:"a",skill:"后台尾段",element:"cryo"}]};
  const rotation={period:10,abilities:[{id:"e",character:"a",action_type_id:"action.elemental_skill"}],
    actions:[{id:"cast",ability_id:"e",start:0,occupation_end:1,order:0,repeat:{mode:"once"}}]};
  const model={rules:[],hit_patterns:[],hit_events:[{hit_id:"direct",action_id:"cast"},{hit_id:"tail",action_id:"cast"}]};
  return {template,plan:{plan_version:3,rotation_model:rotation,mechanics_model:model},current:true,
    result:{damage_report_window:{contract:"damage-report-window-v1",end:2,status:"complete"},
      hits:[{...template.hits[0],expected:45}],mechanics_trace:[
        {type:"hit",event_id:"direct",t:2,cycle:0},{type:"hit",event_id:"tail",t:9,cycle:0}]}};
}

describe("damage report is separate from full timeline",()=>{
  it("keeps the full action/state view but excludes late damage previews",()=>{
    const out=buildTimelineProjection(input());
    expect(out.duration).toBe(10);
    expect(out.views.damage.events.filter(row=>row.kind==="damage").map(row=>row.start)).toEqual([2]);
    expect(out.views.effects.events.some(row=>row.start===9)).toBe(true);
    expect(out.reportWindow.end).toBe(2);
  });
  it("includes a hit exactly on the execution/report boundary without epsilon",()=>{
    const data=input();data.result.damage_report_window.end=10;data.template.hits[0].t=10;data.result.hits[0].t=10;
    data.result.mechanics_trace[0].t=10;
    const out=buildTimelineProjection(data);
    expect(out.views.damage.events.some(row=>row.start===10&&row.value===45)).toBe(true);
    expect(out.duration).toBe(10);
  });
  it("does not mutate either the live result or source input",()=>{
    const data=input(),before=JSON.stringify(data);buildTimelineProjection(data);expect(JSON.stringify(data)).toBe(before);
  });
});
