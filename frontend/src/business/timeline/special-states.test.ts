import {describe,expect,it,vi} from "vitest";
import {buildTimelineProjection} from "./index";
import {createTimelineDetail} from "./detail";
import {createTimelineLayout} from "./layout";
import {groupCoverage} from "./index";

const receipt=()=>({contract:"special-state-timeline-v1",version:1,status:"complete",states:[
  {id:"a",kind:"field_accumulator",status:"active",knowledge:"known",scope:"area",area_id:"arena",instance_id:"first",layers:0,start:1,end:2,
    start_boundary:{trace_index:1},cause:"establish_or_refresh_field",end_reason:"layer_change"},
  {id:"b",kind:"field_accumulator",status:"active",knowledge:"known",scope:"area",area_id:"arena",instance_id:"first",layers:1,start:2,end:5,
    start_boundary:{trace_index:2},cause:"count_successful_application",end_reason:"settlement_clear"},
  {id:"c",kind:"settled_damage_bonus",status:"active",knowledge:"known",scope:"area",area_id:"arena",instance_id:"first",layers:1,start:5,end:9,
    start_boundary:{trace_index:3},system_factor:1.45,cause:"settle_four_second_window",end_reason:"buff_expiry"},
],changes:[]});
const fixture=()=>({template:{loop_duration:10,characters:[],hits:[],buffs:[]},current:true,
  result:{hits:[],rotation_check:{event_log:[]},mechanics_trace:[],special_state_timeline:receipt()}});

describe("committed stellar layer intervals",()=>{
  it("keeps accumulation and last settlement separate without changing input",()=>{
    const input=fixture(),before=JSON.stringify(input),out=buildTimelineProjection(input);
    expect(out.views.effects.events.map(e=>[e.kind,e.label,e.start,e.end])).toEqual([
      ["field_layers","领域累计 0 层",1,2],["field_layers","领域累计 1 层",2,5],["settled_layers","结算增益 1 层",5,9]]);
    expect(JSON.stringify(input)).toBe(before);
    expect(out.views.damage.events).toHaveLength(0);
  });
  it("does not synthesize layers from old special traces or stale results",()=>{
    const input:any=fixture();delete input.result.special_state_timeline;
    input.result.special_reactions={trace:[{reaction_type:"stellar_superconduct",operation:"count_successful_application",layers:8,t:2}]};
    expect(buildTimelineProjection(input).views.effects.notes.join()).toContain("未保存领域层数分段");
    expect(buildTimelineProjection({...fixture(),current:false}).views.effects.events).toHaveLength(0);
  });
  it("never treats unknown layers as a known active zero",()=>{
    const input:any=fixture();input.result.special_state_timeline.status="partial";
    input.result.special_state_timeline.states=[{...receipt().states[0],layers:null,knowledge:"unknown"}];
    const view=buildTimelineProjection(input).views.effects;
    expect(view.events).toHaveLength(0);expect(view.notes.join()).toContain("未知状态未按零层");
  });
  it("uses the same theme-aware detail surface with explicit layer purpose",()=>{
    document.body.innerHTML='<section id="timeline-detail"></section>';
    const describe=vi.fn(),view:any={mode:"effects",characterNames:new Map(),updateDetailSelection:vi.fn()};
    const ports:any={view,editor:{sel:()=>null},query:(s:string)=>document.querySelector(s),document,events:window,
      projection:{},services:{effects:{describe},display:{renderEffectDescription:vi.fn()}}};
    const row=buildTimelineProjection(fixture()).views.effects.events.find(e=>e.kind==="settled_layers")!;
    createTimelineDetail(ports).showDetail(row);
    expect(document.querySelector('#timeline-detail')?.textContent).toContain("最近完成结算，供伤害读取");
    expect(document.querySelector('#timeline-detail')?.textContent).toContain("1.45");
    expect(describe).not.toHaveBeenCalled();
  });
  it("retains global field observations when a member is selected without retaining other member effects",()=>{
    const projection=buildTimelineProjection(fixture());
    projection.views.effects.events.push({id:"other",kind:"hit",character:"b",sourceKey:"b",sourceLabel:"其他角色",
      label:"其他角色命中",start:1,detail:{}});
    projection.views.effects.events.push({id:"own",kind:"hit",character:"a",sourceKey:"a",sourceLabel:"所选角色",
      label:"所选角色命中",start:1,detail:{}});
    const before=JSON.stringify(projection),view:any={projection,mode:"effects",characterFilter:"a",sourceFilter:"",
      kindVisibility:{effects:{}},characterNames:new Map(),effectFocus:"coverage",renderControls:vi.fn()};
    const ports:any={view,editor:{cancelDrag:vi.fn()},query:()=>null,document,events:window,
      projection:{groupCoverage},services:{}};
    createTimelineLayout(ports).rebuildRows();
    expect(view.rows.flatMap((row:any)=>row.events).map((row:any)=>row.kind)).toEqual([
      "field_layers","field_layers","settled_layers"]);
    view.effectFocus="points";createTimelineLayout(ports).rebuildRows();
    expect(view.rows.flatMap((row:any)=>row.events).map((row:any)=>row.id)).toEqual(["own"]);
    expect(JSON.stringify(projection)).toBe(before);
  });
  it("narrows component choices by member and skill and clears stale component filters",()=>{
    const projection=buildTimelineProjection(fixture());
    projection.views.damage.events=[
      {id:"a.e.1",kind:"damage",character:"a",sourceKey:"a.e",sourceLabel:"战技",componentKey:"a.e.1",componentLabel:"首次伤害",label:"1",start:1,detail:{}},
      {id:"a.e.2",kind:"damage",character:"a",sourceKey:"a.e",sourceLabel:"战技",componentKey:"a.e.2",componentLabel:"后续伤害",label:"2",start:2,detail:{}},
      {id:"a.q.1",kind:"damage",character:"a",sourceKey:"a.q",sourceLabel:"爆发",componentKey:"a.q.1",componentLabel:"爆发伤害",label:"3",start:3,detail:{}},
      {id:"b.e.1",kind:"damage",character:"b",sourceKey:"b.e",sourceLabel:"其他角色战技",componentKey:"b.e.1",componentLabel:"其他伤害",label:"4",start:4,detail:{}}];
    const view:any={projection,mode:"damage",characterFilter:"a",sourceFilter:"a.e",componentFilter:"a.e.2",
      kindVisibility:{damage:{}},characterNames:new Map(),damageFocus:"damage",renderControls:vi.fn()};
    const ports:any={view,editor:{cancelDrag:vi.fn()},query:()=>null,document,events:window,projection:{},services:{}};
    const layout=createTimelineLayout(ports);
    layout.rebuildRows();
    expect([...view.components]).toEqual([["a.e.1","首次伤害"],["a.e.2","后续伤害"]]);
    expect(view.rows.flatMap((row:any)=>row.events).map((row:any)=>row.id)).toEqual(["a.e.2"]);
    view.sourceFilter="a.q";layout.rebuildRows();
    expect(view.componentFilter).toBe("");
    expect([...view.components.keys()]).toEqual(["a.q.1"]);
    expect(view.rows.flatMap((row:any)=>row.events).map((row:any)=>row.id)).toEqual(["a.q.1"]);
    view.characterFilter="b";layout.rebuildRows();
    expect(view.sourceFilter).toBe("");expect([...view.components.keys()]).toEqual(["b.e.1"]);
  });
});
