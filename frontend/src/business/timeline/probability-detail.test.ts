import {describe,expect,it,vi} from "vitest";
import {buildTimelineProjection} from "./index";
import {createTimelineDetail} from "./detail";
import {probabilityFields} from "./probability-detail";

const probability=()=>({contract:"shared-resource-probability-v1",mode:"custom",kind:"waive_spend",
  chance:.5,sampled:true,outcome:true,executed_units:2,consumed_units:0,created_units:0,overflow_units:0});
const fixture=()=>({current:true,template:{loop_duration:10,characters:[],hits:[],buffs:[]},
  result:{hits:[],rotation_check:{event_log:[]},mechanics_trace:[],shared_pools:{trace:[
    {type:"operation",pool_id:"p",operation_id:"spend",operation:"spend",t:1,cycle:0,actor:"a",
      before:2,after:2,accepted:0,qualified:true,succeeded:true,reason:"waived",probability:probability()},
    {type:"operation",pool_id:"p",operation_id:"spend",operation:"spend",t:2,cycle:0,actor:"a",
      before:0,after:0,accepted:0,qualified:true,succeeded:false,reason:"insufficient",
      probability:{...probability(),sampled:false,outcome:null,executed_units:0}},
  ]}}});

describe("probability scenario evidence",()=>{
  it("shows expected trial labels and cumulative floor evidence",()=>{
    const input:any=fixture();
    input.result.special_reactions={trace:[{type:"stellar_resource_receipt",event_id:"refund",owner:"a",t:1,
      trial:{contract:"probabilistic-resource-v1",mode:"expected",executed:true,expected_count:.34}}]};
    input.result.mechanics_trace=[{type:"expected_discrete_count",rule_id:"buff",event_id:"count",t:2,
      expected_count:1.36,applied_count:1,new_activations:0,rounding:"floor"}];
    const events=buildTimelineProjection(input).views.effects.events;
    expect(events.some(e=>e.label.includes("期望触发 0.34"))).toBe(true);
    const rounded=events.find(e=>e.label.includes("累计期望 1.36"))!;
    expect(probabilityFields(rounded.detail)).toContainEqual(["累计应用次数","1"]);
  });
  it("shows expected counts without inventing a sampled outcome",()=>{
    const fields=probabilityFields({probability:{...probability(),mode:"expected",outcome:null,
      eligible_count:1,expected_count:.34,consumed_units:.66}});
    expect(fields).toContainEqual(["期望触发次数","0.34"]);
    expect(fields).toContainEqual(["实际消费","0.66"]);
    expect(fields.some(([label])=>label==="本次判定")).toBe(false);
    expect(fields[0][1]).toContain("不是随机模拟");
  });
  it("shows successful zero-debit operations and real shortages without hiding them",()=>{
    const input=fixture(),before=JSON.stringify(input);
    const events=buildTimelineProjection(input).views.effects.events.filter(e=>e.kind==="resource");
    expect(events).toHaveLength(2);
    expect(events[0].label).toContain("免消耗");
    expect(events[1].label).toContain("库存不足");
    expect(JSON.stringify(input)).toBe(before);
    document.body.innerHTML='<section id="timeline-detail"></section>';
    const view:any={mode:"effects",characterNames:new Map(),updateDetailSelection:vi.fn()};
    const ports:any={view,editor:{sel:()=>null},query:(s:string)=>document.querySelector(s),document,events:window,projection:{},services:{}};
    createTimelineDetail(ports).showDetail(events[0]);
    expect(document.querySelector('#timeline-detail')?.textContent).toContain("不是概率期望");
    expect(document.querySelector('#timeline-detail')?.textContent).toContain("仍遵守成功触发冷却");
    createTimelineDetail(ports).showDetail(events[1]);
    expect(document.querySelector('#timeline-detail')?.textContent).toContain("未判定");
  });
  it("separates periodic trial points from inventory intervals",()=>{
    const input:any=fixture();
    input.result.special_reactions={trace:[{type:"stellar_resource_receipt",event_id:"0:spend",owner:"a",
      t:3,area_id:"arena",trial:{contract:"probabilistic-resource-v1",mode:"custom",parameters:{chance:.34},
        outcome:true,executed:true,executed_units:2,consumed_units:2,created_units:1}}]};
    const events=buildTimelineProjection(input).views.effects.events;
    const trial=events.find(e=>e.label.includes("资源判定"))!;
    expect(trial.character).toBe("a");expect(trial.start).toBe(3);
    const fields=Object.fromEntries(probabilityFields(trial.detail));
    expect(fields["单次触发概率"]).toBe("34%");
    expect(fields["实际消费"]).toBe("2");expect(fields["实际生成"]).toBe("1");
    expect(buildTimelineProjection({...input,current:false}).views.effects.events).toHaveLength(0);
  });
  it("does not describe unknown contracts or absent outcomes as success",()=>{
    expect(probabilityFields({probability:{...probability(),contract:"future"}})).toEqual([]);
    expect(probabilityFields({probability:{...probability(),mode:"future"}})).toEqual([]);
    expect(Object.fromEntries(probabilityFields({probability:{...probability(),outcome:undefined}}))["本次判定"]).toBe("记录不完整");
  });
});
