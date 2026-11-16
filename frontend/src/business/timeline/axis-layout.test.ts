import {describe,it,expect,vi} from "vitest";
import {createTimelineSvg} from "./svg";
import {createTimelineDetail} from "./detail";
import {createTimelineLayout} from "./layout";
import {packDisplayItems,buildDisplayRows} from "./display-rows";
import {createWhiteboardProjection} from "../whiteboard/projection";
import {createWhiteboardPointer} from "../whiteboard/pointer";
describe("axis separation and read-only projection",()=>{
  it("shows the full history of a selected Buff fragment and keeps both disclosure controls in sync",()=>{
    document.body.innerHTML='<div><div id="timeline-wrap"><svg id="timeline"></svg></div></div><aside id="timeline-detail"></aside>';
    const states=[{start:1,end:3,activation_trace_index:0,activation_time:1,rule_expires_at:5,status:"active",layers:1},
      {start:3,end:7,activation_trace_index:1,activation_time:3,rule_expires_at:7,status:"active",layers:1}];
    const e={id:"refreshed",kind:"coverage",start:1,end:7,character:"a",label:"增益区间",sourceKey:"r",sourceLabel:"角色甲 · 星烁伤害加成 · 华彩·星烁",
      detail:{owner:"a",rule_id:"r",source_name:"华彩·星烁",states,receipt_id:"refreshed-receipt"}};
    const other={...e,start:4,id:"other",character:"b",detail:{owner:"a",rule_id:"r",states:[],receipt_id:"other-receipt"}};
    const TV:any={projection:{duration:10,characters:[{id:"a"},{id:"b"}],views:{effects:{events:[e,other]}}},
      mode:"effects",kindVisibility:{},characterNames:new Map([['a','角色甲'],['b','角色乙']]),
      prepare:()=>{TV.pixelsPerSecond=50;TV.duration=10;return 500;},
      tickStep:()=>1,updateOverlays:()=>{},renderControls:()=>{}};
    const ports:any={view:TV,editor:{tpl:()=>({}),sel:()=>null},query:(s:string)=>document.querySelector(s),document,
      services:{},projection:{stateName:String,endReason:String}};
    Object.assign(TV,createTimelineSvg(ports),createTimelineDetail(ports),createTimelineLayout(ports));
    TV.rebuildRows();TV.paintProjection();
    const laneLabel=document.querySelector('.tl-label-pane text[role="button"]')!;
    expect(laneLabel.childNodes[0]?.textContent).toBe("华彩·星烁");
    expect(laneLabel.getAttribute("aria-label")).toContain("角色甲 · 星烁伤害加成 · 华彩·星烁");
    const grouped=document.querySelector('#timeline [data-event-count="2"]')!;
    grouped.dispatchEvent(new MouseEvent("click"));
    const inspector=document.querySelector('#timeline-detail')!;
    expect(inspector.textContent).toContain("Buff 区间");expect(inspector.textContent).not.toContain("密集时间点");
    const receiverLabels=Array.from(inspector.querySelectorAll(".tl-detail-record"),button=>button.textContent);
    expect(receiverLabels).toEqual([expect.stringContaining("接收：角色甲"),expect.stringContaining("接收：角色乙")]);
    const marker=document.querySelector('#timeline [data-event-id="refreshed"]')!;
    marker.dispatchEvent(new MouseEvent("click"));
    expect(inspector.textContent).toContain("当前选中一个状态片段");
    expect(inspector.textContent).toContain("激活/刷新明细（2 次）");
    const button=inspector.querySelector<HTMLButtonElement>('[data-inspector-row-toggle]')!;
    expect(button.textContent).toBe("展开实例行");
    button.click();expect(TV.rows).toHaveLength(2);expect(button.textContent).toBe("合并实例行");
    expect(button.getAttribute("aria-expanded")).toBe("true");
    const chevron=document.querySelector<SVGElement>('.tl-label-pane [data-axis-control]')!;
    expect(chevron.getAttribute("aria-expanded")).toBe("true");
    chevron.focus();chevron.dispatchEvent(new KeyboardEvent("keydown",{key:" ",bubbles:true,cancelable:true}));
    expect(TV.rows).toHaveLength(1);expect(button.textContent).toBe("展开实例行");
    const renewed=document.querySelector<SVGElement>('.tl-label-pane [data-axis-control]')!;
    expect(renewed.getAttribute("aria-expanded")).toBe("false");expect(document.activeElement).toBe(renewed);
    expect(e.detail.states).toEqual(states);
  });
  it("puts accessible source labels outside the scrollable time SVG",()=>{
    document.body.innerHTML='<div><div id="timeline-wrap"><svg id="timeline"></svg></div></div><span id="timeline-stats"></span>';
    const e={id:"effect",kind:"coverage",start:1,end:5,character:"a",label:"完整效果名称",sourceKey:"r",sourceLabel:"完整效果名称",detail:{owner:"a",rule_id:"r"}};
    const TV:any={projection:{duration:10,characters:[{id:"a"}]},mode:"effects",characterNames:new Map([["a","角色全名"]]),
      rows:buildDisplayRows([e],"effects"),showDetail:vi.fn(),filteredCount:1,
      prepare:()=>{TV.pixelsPerSecond=50;TV.duration=10;return 500;},packRows:()=>{TV.rows[0].items=packDisplayItems([e],50,"effects");},
      tickStep:()=>1,visibleItems:(row:any)=>row.items,updateOverlays:()=>{},updateDetailSelection:()=>{}};
    const ports:any={view:TV,editor:{tpl:()=>({})},query:(s:string)=>document.querySelector(s),document,services:{},projection:{}};
    Object.assign(TV,createTimelineSvg(ports));TV.paintProjection();
    const label=document.querySelector('.tl-label-pane [role="button"]')!;
    expect(label.getAttribute("aria-label")).toContain("完整效果名称");
    expect(document.querySelector('#timeline .tl-lane-label')).toBeNull();expect(TV.labelWidth).toBe(0);
    label.dispatchEvent(new KeyboardEvent("keydown",{key:"Enter"}));expect(TV.showDetail.mock.calls[0][0].events).toEqual([e]);
    const marker=document.querySelector('#timeline [data-event-id="effect"]')!;marker.dispatchEvent(new MouseEvent("click"));
    expect(TV.showDetail.mock.calls.at(-1)[0].id).toBe("effect");
    TV.paintProjection();expect(document.querySelectorAll(".tl-axis-shell")).toHaveLength(1);
  });
  it("reuses only the current calculated object and exposes optional sampling records",()=>{
    const events=[{id:"e",kind:"coverage",character:"a",sourceKey:"r",start:0,end:3},{id:"s",kind:"calculation_sampling",start:1,end:2}];
    const build=vi.fn(()=>({views:{effects:{events}},explanationFingerprint:"result"}));
    const owner:any={state:{mode:"effects",effectFilter:"all",board:{team:[],actions:[]},result:{plan:{},result:{}}}};
    const project=createWhiteboardProjection({owner,services:{projection:{build}}} as any).project;
    const first=project();expect(project()).toBe(first);expect(build).toHaveBeenCalledTimes(1);expect(first.events).toHaveLength(1);
    owner.state.showCalculationSampling=true;expect(project().events).toHaveLength(2);
    owner.state.result={plan:{},result:{}};project();expect(build).toHaveBeenCalledTimes(2);
  });
  it("cancels an editable action drag when zoom/layout scale changes",()=>{
    const move=vi.fn(),board={revision:1};const owner:any={state:{board,mode:"actions",geometry:{pps:10},wrap:{scrollLeft:0,setPointerCapture(){},releasePointerCapture(){}}},
      action:()=>({start:2}),clampTime:(t:number)=>t,setTime:move,draw(){},render(){},number:String};
    const pointer=createWhiteboardPointer({owner,status:()=>{},services:{}} as any);
    const e:any={button:0,pointerId:1,clientX:100,preventDefault(){},stopPropagation(){}};pointer.beginPointer(e,"a");
    owner.state.geometry.pps=20;pointer.movePointer({...e,clientX:150});pointer.endPointer(e);
    expect(owner.state.pointer).toBeNull();expect(move).not.toHaveBeenCalled();
  });
});
