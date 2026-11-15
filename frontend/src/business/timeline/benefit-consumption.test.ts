import {describe,expect,it,vi} from "vitest";
import {buildTimelineProjection} from "./index";
import {createTimelineDetail} from "./detail";

function fixture(refs:any=[{buff_id:"mechanics:h",effect_id:"regular:value:0"}]){
  return {template:{loop_duration:3,characters:[{id:"a",name:"工程角色"}],hits:[],buffs:[]},current:true,
    result:{hits:[{id:"h",t:1,character:"a",skill:"工程技能",expected:42,zones:{},
      ...(refs===undefined?{}:{matched_effect_refs:refs})}],rotation_check:{event_log:[]},mechanics_trace:[
        {type:"effect_applied",event_id:"h",t:1,cycle:0,rule_id:"regular",effect_id:"value",projected_effect_id:"regular:value:0",value:0},
        {type:"effect_applied",event_id:"h",t:1,cycle:0,rule_id:"other",effect_id:"value",projected_effect_id:"other:value:0",value:1.5}]}};
}
function detail(input:any){const p=buildTimelineProjection(input);return p.views.damage.events.find(e=>e.kind==="calculation")!;}
function render(row:any){
  document.body.innerHTML='<section id="timeline-detail"></section>';
  const ports:any={view:{mode:"damage",characterNames:new Map(),updateDetailSelection:vi.fn()},
    editor:{sel:()=>null},query:(s:string)=>document.querySelector(s),document,events:window,projection:{},services:{}};
  createTimelineDetail(ports).showDetail(row);return document.querySelector("#timeline-detail")!;
}
describe("formula consumption rather than mere effect materialization",()=>{
  it("uses final compiled identities and keeps zero-valued consumed inputs",()=>{
    const value=fixture(),original=JSON.stringify(value),row=detail(value);
    expect(row.detail.applied_effects.map((r:any)=>[r.projected_effect_id,r.value])).toEqual([["regular:value:0",0]]);
    expect(row.detail.materialized_effects.map((r:any)=>[r.projected_effect_id,r.consumption_status])).toEqual([["other:value:0","not_consumed"]]);
    expect(row.detail.effects).toHaveLength(2);expect(row.detail.hit.expected).toBe(42);
    expect(JSON.stringify(value)).toBe(original);
    const box=render(row);expect(box.textContent).toContain("本次公式实际消费的收益（1 项）");
    expect(box.textContent).toContain("本次公式未消费");
    const summaries=[...box.querySelectorAll("summary")];
    expect(summaries.find(s=>s.textContent?.startsWith("运行时已物化"))?.parentElement?.hasAttribute("open")).toBe(false);
  });
  it("treats missing legacy references as unknown and explicit empty references as none consumed",()=>{
    const legacy:any=fixture();delete legacy.result.hits[0].matched_effect_refs;
    const missing=detail(legacy),none=detail(fixture([]));
    expect(missing.detail.applied_effects).toHaveLength(0);
    expect(missing.detail.materialized_effects.every((r:any)=>r.consumption_status==="unknown")).toBe(true);
    expect(none.detail.applied_effects).toHaveLength(0);
    expect(none.detail.materialized_effects.every((r:any)=>r.consumption_status==="not_consumed")).toBe(true);
    expect(render(missing).textContent).toContain("消费记录不足，未核实");
    expect(render(missing).textContent).not.toContain("本次公式实际消费的收益");
  });
  it("does not match an uncompiled rule effect or infer eligibility from damage categories",()=>{
    const value:any=fixture([{buff_id:"mechanics:h",effect_id:"other:value:0"}]);
    delete value.result.mechanics_trace[0].projected_effect_id;
    value.result.hits[0].damage_system="classic";
    const row=detail(value);
    expect(row.detail.applied_effects[0].projected_effect_id).toBe("other:value:0");
    expect(row.detail.materialized_effects[0].consumption_status).toBe("unknown");
  });
  it("does not choose one owner for ambiguous compiled IDs",()=>{
    const row=detail(fixture([{buff_id:"one",effect_id:"regular:value:0"},{buff_id:"two",effect_id:"regular:value:0"}]));
    expect(row.detail.applied_effects).toHaveLength(0);
    expect(row.detail.materialized_effects[0].consumption_status).toBe("unknown");
  });
  it("labels consumed inputs as the automatic baseline when fixed values override the result",()=>{
    const value:any=fixture();value.result.hits[0].calculation_override={automatic_expected:42,effective_expected:99};
    const box=render(detail(value));expect(box.textContent).toContain("自动计算基线实际消费的收益");
    expect(box.textContent).toContain("不用于反推补正后的有效结果");
  });
});
