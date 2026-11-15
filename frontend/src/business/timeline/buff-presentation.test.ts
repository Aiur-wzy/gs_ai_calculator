import {describe,expect,it,vi} from "vitest";
import {buffPresentation} from "./buff-presentation";
import {buildTimelineProjection,groupCoverage} from "./index";
import {createTimelineLayout} from "./layout";
import {createTimelineDetail} from "./detail";
import {createTimelineControls} from "./controls";

const purpose={contract:"buff-presentation-v1",purpose:"calculation_sampling",reason:"本轴实际条件的计算采样"};
function buff(id:string,presentation?:any){return {id,name:"条件增益",start:1,end:1.000001,target:"a",scope:"team",
  extensions:{owner:"a",source_id:id,...(presentation===undefined?{}:{presentation})},effects:[]};}
function input(receipt=false){
  const buffs=[buff("sampling",purpose),buff("real")];
  return {template:{loop_duration:3,characters:[{id:"a",name:"测试角色"}],buffs,hits:[]},current:receipt,
    ...(receipt?{result:{hits:[],mechanics_trace:[],rotation_check:{event_log:[]},buff_state_timeline:{
      contract:"buff-state-timeline-v1",version:1,duration:3,execution_limit:3,notes:[],states:buffs.map(b=>({
        id:b.id,origin:"declared_interval",buff_id:b.id,buff:b,name_zh:b.name,owner:"a",beneficiary:"a",
        source_id:b.id,start:b.start,end:b.end,slices:[{start:b.start,end:b.end,status:"active",layers:1}],effects:[]}))}}}: {})};
}
function ports(projection:any){
  const view:any={projection,mode:"effects",characterFilter:"",sourceFilter:"",kindVisibility:{effects:{}},
    characterNames:new Map(),effectFocus:"all",renderControls:vi.fn(),updateDetailSelection:vi.fn(),paintProjection:vi.fn(),kinds:[]};
  const result:any={view,editor:{cancelDrag:vi.fn(),sel:()=>null},query:()=>null,document,events:window,
    projection:{groupCoverage},services:{}};
  view.rebuildRows=createTimelineLayout(result).rebuildRows;
  return result;
}
describe("explicit calculation sampling presentation",()=>{
  it.each([undefined,null,{},true,{hidden:true},{...purpose,contract:"future"},{...purpose,purpose:"hidden"},
    {...purpose,reason:""},{...purpose,reason:" \n "}])("leaves invalid or unknown marks visible (%j)",mark=>{
    expect(buffPresentation(buff("any",mark))).toBeNull();
    const value=input();value.template.buffs=[buff("calculation_sampling",mark)];
    expect(buildTimelineProjection(value).views.effects.events[0].kind).toBe("coverage");
  });
  it.each([false,true])("classifies both input and saved-receipt windows without dropping records (%s)",receipt=>{
    const value=input(receipt),before=JSON.stringify(value),projection=buildTimelineProjection(value);
    expect(projection.views.effects.events.map(e=>e.kind)).toEqual(["calculation_sampling","coverage"]);
    expect(projection.views.effects.coverage?.sampling_count).toBe(1);
    expect(projection.views.effects.events[0].detail.interpretation).toContain("并非游戏Buff");
    const p=ports(projection);p.view.rebuildRows();
    expect(p.view.rows.flatMap((row:any)=>row.events).map((e:any)=>e.kind)).toEqual(["coverage"]);
    p.view.showCalculationSampling=true;p.view.rebuildRows();
    expect(p.view.rows.flatMap((row:any)=>row.events)).toHaveLength(2);
    expect(JSON.stringify(value)).toBe(before);
  });
  it("keeps purpose and reason in a normal read-only detail surface",()=>{
    document.body.innerHTML='<section id="timeline-detail"></section>';
    const projection=buildTimelineProjection(input(true)),p=ports(projection);
    p.query=(s:string)=>document.querySelector(s);
    createTimelineDetail(p).showDetail(projection.views.effects.events[0]);
    expect(document.querySelector("#timeline-detail")?.textContent).toContain(purpose.reason);
    expect(document.querySelector("#timeline-detail")?.textContent).toContain("非游戏Buff寿命");
  });
  it("retains actual per-hit contribution and explicitly labels its sampling reason",()=>{
    const value:any=input(true);
    value.template.buffs=[]; // A saved result carries the source marker itself.
    value.result.hits=[{id:"h",character:"a",skill:"工程伤害",hit_name:"工程命中",t:1,expected:42,zones:{crit_rate:.15},
      matched_effect_refs:[{buff_id:"mechanics:h",effect_id:"manual:sampling:cr:0"}]}];
    value.result.mechanics_trace=[{type:"manual_effect_applied",event_id:"h",t:1,cycle:0,buff_id:"sampling",effect_id:"cr",value:.15,
      projected_effect_id:"manual:sampling:cr:0"}];
    const before=JSON.stringify(value),projection=buildTimelineProjection(value);
    const row=projection.views.damage.events.find(e=>e.kind==="calculation")!;
    expect(row.detail.applied_effects).toHaveLength(1);
    expect(row.detail.applied_effects[0].value).toBe(.15);
    expect(row.detail.applied_effects[0].presentation.reason).toBe(purpose.reason);
    document.body.innerHTML='<section id="timeline-detail"></section>';
    const p=ports(projection);p.view.mode="damage";p.query=(s:string)=>document.querySelector(s);
    createTimelineDetail(p).showDetail(row);
    expect(document.querySelector("#timeline-detail")?.textContent).toContain(purpose.reason);
    expect(JSON.stringify(value)).toBe(before);
  });
  it("puts the opt-in and count inside existing advanced filters",()=>{
    document.body.innerHTML='<div id="timeline-filters"></div>';
    const projection=buildTimelineProjection(input()),p=ports(projection);
    p.query=(s:string)=>document.querySelector(s);p.view.sources=new Map();p.view.modeScroll={};
    createTimelineControls(p).renderControls(projection.views.effects);
    const control=document.querySelector<HTMLInputElement>('details .tl-filter-panel input[aria-label="显示计算采样"]')!;
    expect(control).not.toBeNull();expect(control.checked).toBe(false);
    control.checked=true;control.dispatchEvent(new Event("change"));expect(p.view.showCalculationSampling).toBe(true);
  });
  it("keeps exact manual and declarative sources, explicit trace names and honest unknown fallback",()=>{
    const value:any=input(true);value.template.buffs=[];
    value.plan={plan_version:3,mechanics_model:{rules:[{id:"rule",name_zh:"声明式队友收益",owner:"b",source_id:"declared:rule",
      effects:[{id:"gain",benefit_type_id:"benefit.crit_rate.all",value:.2}]}]}};
    value.result.hits=[{id:"h",character:"a",skill:"工程伤害",t:1,expected:42,zones:{},
      matched_effect_refs:["manual:sampling:cr:0","rule:gain:0","manual:absent:other:0","manual:sampling:cr:1"]
        .map(effect_id=>({buff_id:"mechanics:h",effect_id}))}];
    value.result.mechanics_trace=[
      {type:"manual_effect_applied",event_id:"h",t:1,cycle:0,buff_id:"sampling",effect_id:"cr",value:.15,projected_effect_id:"manual:sampling:cr:0"},
      {type:"effect_applied",event_id:"h",t:1,cycle:0,rule_id:"rule",effect_id:"gain",value:.2,projected_effect_id:"rule:gain:0"},
      {type:"manual_effect_applied",event_id:"h",t:1,cycle:0,buff_id:"absent",effect_id:"other",value:.05,projected_effect_id:"manual:absent:other:0"},
      {type:"manual_effect_applied",event_id:"h",t:1,cycle:0,buff_id:"sampling",effect_id:"cr",source_name:"现场显式名称",value:.1,projected_effect_id:"manual:sampling:cr:1"}];
    const original=JSON.stringify(value),projection=buildTimelineProjection(value);
    const row=projection.views.damage.events.find(e=>e.kind==="calculation")!,effects=row.detail.applied_effects;
    expect(effects[0]).toMatchObject({owner:"a",source_id:"sampling",source_name:"条件增益",value:.15,presentation:{reason:purpose.reason}});
    expect(effects[1]).toMatchObject({owner:"b",source_id:"declared:rule",source_name:"声明式队友收益",value:.2});
    expect(effects[2].source_name).toBeUndefined();expect(effects[3].source_name).toBe("现场显式名称");
    document.body.innerHTML='<section id="timeline-detail"></section>';
    const p=ports(projection);p.view.mode="damage";p.view.characterNames=new Map([["a","提供者甲"],["b","提供者乙"]]);
    p.query=(s:string)=>document.querySelector(s);createTimelineDetail(p).showDetail(row);
    const text=document.querySelector("#timeline-detail")?.textContent;
    expect(text).toContain("提供者甲 · 条件增益");expect(text).toContain("提供者乙 · 声明式队友收益");
    expect(text).toContain("现场显式名称");expect(text).toContain("手工收益（来源未记录）");
    expect(text).toContain(purpose.reason);expect(JSON.stringify(value)).toBe(original);
  });
  it("does not add an empty sampling control or infer purpose from a one-microsecond window",()=>{
    const value=input();value.template.buffs=[buff("sampling")];
    const projection=buildTimelineProjection(value),p=ports(projection);
    expect(projection.views.effects.coverage?.sampling_count).toBeUndefined();
    document.body.innerHTML='<div id="timeline-filters"></div>';
    p.query=(s:string)=>document.querySelector(s);p.view.sources=new Map();p.view.modeScroll={};
    createTimelineControls(p).renderControls(projection.views.effects);
    expect(document.querySelector('[aria-label="显示计算采样"]')).toBeNull();
  });
  it("does not let the sampling toggle remove global layer states",()=>{
    const value:any=input(true);value.result.special_state_timeline={contract:"special-state-timeline-v1",version:1,status:"complete",states:[{
      id:"field",kind:"field_accumulator",status:"active",knowledge:"known",scope:"area",area_id:"arena",instance_id:"one",
      layers:3,start:0,end:2,start_boundary:{trace_index:0},cause:"count_successful_application"}],changes:[]};
    const p=ports(buildTimelineProjection(value));p.view.rebuildRows();
    expect(p.view.rows.flatMap((r:any)=>r.events).filter((e:any)=>e.kind==="field_layers")).toHaveLength(1);
    p.view.showCalculationSampling=true;p.view.rebuildRows();
    expect(p.view.rows.flatMap((r:any)=>r.events).filter((e:any)=>e.kind==="field_layers")).toHaveLength(1);
  });
  it("renders a supplied reason as text without HTML execution",()=>{
    const value:any=input(true),reason='<img src=x onerror="alert(1)">';
    value.result.buff_state_timeline.states[0].buff.extensions.presentation={...purpose,reason};
    document.body.innerHTML='<section id="timeline-detail"></section>';
    const projection=buildTimelineProjection(value),p=ports(projection);p.query=(s:string)=>document.querySelector(s);
    createTimelineDetail(p).showDetail(projection.views.effects.events[0]);
    expect(document.querySelector("#timeline-detail")?.textContent).toContain(reason);
    expect(document.querySelector("#timeline-detail img")).toBeNull();
  });
});
