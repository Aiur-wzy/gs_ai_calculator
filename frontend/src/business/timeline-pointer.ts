/** One pointer lifecycle; DOM effects and document commits are explicit ports. */
export type TimelineSelection=Readonly<{kind:string;index:number}>;
export interface TimelinePointerPorts {
  events:Pick<Window,"addEventListener"|"removeEventListener">;
  template():unknown;
  select(selection:TimelineSelection):void;
  geometry():{svg:SVGSVGElement;viewport:HTMLElement;duration:number;pixelsPerSecond:number};
  round(value:number):number;
  status(message:string):void;
  repaint():void;
  active(cancel:(()=>void)|null):void;
}

export function createTimelinePointerController(ports:TimelinePointerPorts){
  let cancelActive:(()=>void)|undefined;
  const cancel=()=>cancelActive?.();
  function bind(element:Element,baseSec:number,commit:(delta:number)=>void,visual:Element,
    label:((seconds:number)=>string)|null,selection:TimelineSelection){
    let cancelBinding:(()=>void)|undefined;
    const down=(event:Event)=>{
      const e=event as PointerEvent;if(!ports.template()||e.button!==0)return;
      e.preventDefault();e.stopPropagation();cancel();ports.select(selection);
      const {svg,viewport,duration,pixelsPerSecond}=ports.geometry(),template=ports.template();
      try{svg.setPointerCapture(e.pointerId);}catch{/* Synthetic pointers need no capture. */}
      const x=e.clientX,scroll=viewport.scrollLeft||0;
      const delta=(ev:PointerEvent)=>(ev.clientX-x+(viewport.scrollLeft||0)-scroll)/pixelsPerSecond;
      let settled=false;
      const cleanup=()=>{
        if(settled)return false;settled=true;
        ports.events.removeEventListener("pointermove",move);ports.events.removeEventListener("pointerup",up);
        ports.events.removeEventListener("pointercancel",abort);svg.removeEventListener("lostpointercapture",abort);
        visual.removeAttribute("transform");cancelActive=undefined;cancelBinding=undefined;ports.active(null);
        if(svg.hasPointerCapture(e.pointerId))svg.releasePointerCapture(e.pointerId);return true;
      };
      const abort=(event?:Event)=>{
        if(event&&(event as PointerEvent).pointerId!==e.pointerId)return;
        if(cleanup())ports.status("已取消拖拽，数值未改变");
      };
      const move=(event:Event)=>{
        const ev=event as PointerEvent;if(settled||ev.pointerId!==e.pointerId)return;
        const d=delta(ev);visual.setAttribute("transform",`translate(${d*pixelsPerSecond},0)`);
        if(label)ports.status(`${label(ports.round(Math.max(0,Math.min(duration,baseSec+d))))}（预览）`);
      };
      const up=(event:Event)=>{
        const ev=event as PointerEvent;if(ev.pointerId!==e.pointerId)return;const d=delta(ev);
        if(!cleanup()||ports.template()!==template)return;
        if(Math.abs(d)>.001){commit(d);ports.status("已调整时间；请重新计算结果");}ports.repaint();
      };
      cancelActive=()=>abort();cancelBinding=cancelActive;ports.active(cancelActive);
      ports.events.addEventListener("pointermove",move);ports.events.addEventListener("pointerup",up);
      ports.events.addEventListener("pointercancel",abort);svg.addEventListener("lostpointercapture",abort);
    };
    element.addEventListener("pointerdown",down);
    return()=>{element.removeEventListener("pointerdown",down);cancelBinding?.();};
  }
  return Object.freeze({bind,cancel,dispose:cancel});
}
