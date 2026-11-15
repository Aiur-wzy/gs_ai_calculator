import {type WhiteboardPorts} from "./ports";
export function createWhiteboardSvg(ports:WhiteboardPorts){
  const WB=ports.owner,UI=ports.ui,$=ports.query,services=ports.services;
const zoom =(factor:any,anchor:any)=>{
  const wrap=WB.state.wrap,width=wrap?.clientWidth||700,x=anchor??width/2,old=WB.state.scale,next=Math.max(1,Math.min(40,old*factor));
  const left=WB.state.geometry?.left||125;WB.state.scale=next;WB.state.scroll=Math.max(0,(WB.state.scroll+x-left)*next/old-x+left);WB.render();
};
const svg =(tag:any,attrs:any,text:any)=>{const n=ports.document.createElementNS("http://www.w3.org/2000/svg",tag);for(const [key,value] of Object.entries(attrs))n.setAttribute(key,String(value));
  if(text!=null)n.textContent=text;return n;};
const restoreAxisFocus=(wrap:any,id:any)=>{
  if(!wrap)return;
  const node=id&&Array.from(wrap.querySelectorAll?.("[data-axis-event]")||[]).find((item:any)=>item.getAttribute("data-axis-event")===id) as any;
  (node||wrap).focus?.({preventScroll:true});
};
const draw =()=>{
  const {wrap,svg}=WB.state;if(!wrap||!svg)return;const b=WB.state.board,left=WB.state.mode==="effects"?205:125,available=Math.max(260,(wrap.clientWidth||700)-left-35),pps=available/b.duration*WB.state.scale,
    width=left+b.duration*pps+35,projection=WB.project(),lanes=projection.lanes.length?projection.lanes:[{id:"empty",label:"等待添加"}],laneHeight=WB.state.mode==="actions"?108:62,
    height=Math.max(220,46+lanes.length*laneHeight);
  const active=ports.document.activeElement,focusId=svg.contains?.(active)?active.getAttribute?.("data-axis-event"):null;
  WB.state.geometry={left,pps,width};svg.innerHTML="";svg.setAttribute("width",width);svg.setAttribute("height",height);
  const visibleStart=Math.max(0,(wrap.scrollLeft-left-150)/pps),visibleEnd=(wrap.scrollLeft+(wrap.clientWidth||700)+150-left)/pps;
  let step=1;while(step*pps<60)step=step<5?step*2:step*2.5;
  const laneFill=(lane:any,i:any)=>lane.kind==="coverage"
    ?WB.themeColor("color-axis-coverage-lane","#213036")
    :WB.themeColor(i%2?"color-axis-lane-odd":"color-axis-lane-even",i%2?"#1c2430":"#202937");
  lanes.forEach((lane:any,i:any)=>svg.append(WB.svg("rect",{x:0,y:40+i*laneHeight,width,height:laneHeight-3,fill:laneFill(lane,i)})));
  for(let t=Math.max(0,Math.floor(visibleStart/step)*step);t<=Math.min(b.duration,visibleEnd);t+=step){const x=left+t*pps;svg.append(WB.svg("line",{x1:x,y1:25,x2:x,y2:height,stroke:WB.themeColor("color-axis-grid","#303847")}),
    WB.svg("text",{x,y:18,fill:WB.themeColor("color-axis-label-muted","#adb9cc"),"font-size":11},`${WB.number(t)} 秒`));}
  const report=WB.state.result?.result?.damage_report_window;
  if(report?.contract==="damage-report-window-v1"&&Number.isFinite(report.end)&&report.end>=0&&report.end<=b.duration){
    const cutoff=left+report.end*pps,color=WB.themeColor("color-axis-selected","#ffffff");
    svg.append(WB.svg("rect",{x:cutoff,y:25,width:Math.max(0,(b.duration-report.end)*pps),height:height-25,fill:color,opacity:.06,"pointer-events":"none"}),
      WB.svg("line",{x1:cutoff,y1:25,x2:cutoff,y2:height,stroke:color,"stroke-dasharray":"5 4","pointer-events":"none"}));
    svg.append(WB.svg("text",{x:cutoff-4,y:height-6,fill:color,"font-size":11,"text-anchor":"end","pointer-events":"none"},`DPS截止 ${WB.number(report.end)} 秒`));
  }
  const visible=projection.events.filter((e:any)=>e.start<=visibleEnd&&(e.end??e.start)>=visibleStart),cap=800,slots=new Map(),crowded=new Map(),shown:any[]=[];
  if(WB.state.mode==="actions")shown.push(...visible);
  else {const buckets=new Map();for(const event of visible){
    if(event.end>event.start){shown.push(event);continue;}
    const key=`${event.lane||event.character}:${Math.floor(event.start*pps/18)}`;
    if(!buckets.has(key)){const group={...event,events:[]};buckets.set(key,group);shown.push(group);}buckets.get(key).events.push(event);
  }for(let i=0;i<shown.length;i++){const group:any=shown[i];if(!group.events)continue;
    if(group.events.length===1)shown[i]=group.events[0];else shown[i]={...group,id:`group:${group.events[0].id}`,kind:"group",label:`${group.events.length} 条相邻记录`,value:null};}}
  for(const event of shown.slice(0,cap)){
    const lane=lanes.findIndex((l:any)=>l.id===(event.lane||event.character)),y=40+Math.max(0,lane)*laneHeight,x=left+event.start*pps,
      actorIndex=b.team.findIndex((c:any)=>c.id===event.character),color=event.detail?.state_status==="exhausted"?WB.themeColor("color-axis-exhausted","#89919c"):WB.colors[Math.max(0,actorIndex)%4],selected=WB.state.mode==="actions"?event.id===WB.state.selectedAction:event.id===WB.state.selectedDerived?.id,
      coverage=event.kind==="coverage",range=`${WB.number(event.start)}${event.end>event.start?` → ${WB.number(event.end)}`:""} 秒`,
      description=`${event.sourceLabel||WB.actor(event.character)?.name||"全队"} · ${event.label} · ${range}${coverage?` · ${event.detail?.receipt_id?"实例角色范围":"接收角色"}：${WB.recipients(event)}`:""}${event.value!=null?` · ${WB.number(event.value)} 伤害`:""}`,
      g=WB.svg("g",{class:WB.state.mode==="actions"?"wb-action":coverage?"wb-derived wb-coverage":"wb-derived",tabindex:0,role:"button","aria-label":description,"aria-pressed":selected,"data-axis-event":event.id});
    g.append(WB.svg("title",{},description));
    if(WB.state.mode==="actions"){
      const ends=slots.get(lane)||[-Infinity,-Infinity,-Infinity],slot=ends.findIndex((end:any)=>end<x-5),cardWidth=Math.max(48,Math.min(98,event.label.length*12+16));
      if(slot<0){if(!crowded.has(lane))crowded.set(lane,{x,events:[]});crowded.get(lane).events.push(event);continue;}
      ends[slot]=x+cardWidth+5;slots.set(lane,ends);const top=y+8+slot*27;
      g.append(WB.svg("rect",{x:x-4,y:top,width:cardWidth,height:22,rx:5,fill:color,stroke:selected?WB.themeColor("color-axis-selected","#fff"):WB.themeColor("color-axis-event-border","#182230"),"stroke-width":selected?3:1}),
        WB.svg("text",{x:x+3,y:top+16,fill:WB.themeColor("color-axis-event-text","#14202a"),"font-size":12,"pointer-events":"none"},event.label.slice(0,7)));
      g.addEventListener("pointerdown",(e:any)=>{if(e.button===1){WB.beginPointer(e,null);return;}WB.beginPointer(e,event.id);});
      WB.bindCommandMenu(g,`${event.label}操作`,()=>WB.actionMenuItems(event.id));
    }else if(event.end>event.start){const barWidth=Math.max(3,(event.end-event.start)*pps);g.append(WB.svg("rect",{x,y:y+14,width:barWidth,height:28,rx:4,fill:color,opacity:0.78,stroke:selected?WB.themeColor("color-axis-selected","#fff"):color,"stroke-width":selected?2:1}));
      if(coverage){const label=WB.svg("svg",{x:x+1,y:y+15,width:Math.max(1,barWidth-2),height:26,overflow:"hidden","pointer-events":"none"});
        label.append(WB.svg("text",{x:5,y:18,fill:WB.themeColor("color-axis-event-text","#12242b"),"font-size":11},`${event.detail?.state_status==="exhausted"?"耗尽 · ":""}${range}`));g.append(label);}}
    else if(event.events){g.append(WB.svg("rect",{x:x-8,y:y+14,width:Math.max(22,String(event.events.length).length*7+12),height:23,rx:5,fill:color,stroke:selected?WB.themeColor("color-axis-selected","#fff"):WB.themeColor("color-axis-event-border","#172330")}),
      WB.svg("text",{x:x-2,y:y+30,fill:WB.themeColor("color-axis-event-text","#14202a"),"font-size":11,"pointer-events":"none"},String(event.events.length)));}
    else{g.append(WB.svg("path",{d:`M ${x} ${y+15} l 7 11 l -7 11 l -7 -11 z`,fill:event.kind==="external_damage"?WB.themeColor("color-axis-reaction","#eaa8c9"):color,stroke:selected?WB.themeColor("color-axis-selected","#fff"):"none"}));}
    if(WB.state.mode!=="actions")g.addEventListener("pointerdown",(e:any)=>{
      // Let the following native click select this read-only event. Starting a
      // blank-space drag would redraw its node on pointerup before click fires.
      if(e.button===0){e.stopPropagation();WB.state.dragMoved=false;}
    });
    if(WB.state.mode==="damage"&&event.value!=null)g.append(WB.svg("text",{x:x+9,y:y+32,fill:WB.themeColor("color-axis-label","#dce7f4"),"font-size":11},WB.number(event.value)));
    g.addEventListener("click",(e:any)=>{e.stopPropagation();if(WB.state.dragMoved)return;WB.selectEvent(event);});
    g.addEventListener("keydown",(e:any)=>{if(e.key==="Enter"||e.key===" "){e.preventDefault();WB.selectEvent(event);}});svg.append(g);
  }
  for(const [lane,group] of crowded){const marker=WB.svg("g",{class:"wb-derived",tabindex:0,role:"button","aria-label":`查看同区域 ${group.events.length} 个动作`}),y=40+lane*laneHeight+99;
    marker.append(WB.svg("text",{x:Math.max(left,group.x),y,fill:WB.themeColor("color-axis-warning","#f4cd84"),"font-size":11},`＋ ${group.events.length} 项动作`));
    const open=()=>WB.showCrowded(group.events);marker.addEventListener("click",open);marker.addEventListener("keydown",(e:any)=>{if(e.key==="Enter"||e.key===" "){e.preventDefault();open();}});svg.append(marker);}
  // Source labels stay readable when panning every timeline mode.
  lanes.forEach((lane:any,i:any)=>{const y=40+i*laneHeight,pinned=true,labelX=wrap.scrollLeft;
    if(pinned)svg.append(WB.svg("rect",{x:labelX,y,width:left-5,height:laneHeight-3,fill:laneFill(lane,i)}));
    const label=WB.svg("svg",{x:labelX+8,y,width:left-20,height:laneHeight-3,overflow:"hidden"});
    label.append(WB.svg("title",{},lane.label));
    if(lane.kind){const [owner,...parts]=lane.label.split(" · ");label.append(WB.svg("text",{x:0,y:17,fill:WB.themeColor("color-axis-label","#d1dce9"),"font-size":12},owner),
      WB.svg("text",{x:0,y:34,fill:WB.themeColor("color-axis-label","#d1dce9"),"font-size":12},parts.join(" · ")),
      WB.svg("text",{x:0,y:51,fill:lane.kind==="coverage"?WB.themeColor("color-axis-buff","#92d8b6"):WB.themeColor("color-axis-label-muted","#879cb7"),"font-size":10},lane.kind==="coverage"?"持续 Buff":"命中与触发"));}
    else label.append(WB.svg("text",{x:0,y:31,fill:WB.themeColor("color-axis-label","#d1dce9"),"font-size":12},lane.label.length>14?`${lane.label.slice(0,13)}…`:lane.label));svg.append(label);});
  if(shown.length>cap)svg.append(WB.svg("text",{x:wrap.scrollLeft+150,y:height-9,fill:WB.themeColor("color-axis-warning","#f2c678"),"font-size":12},`当前窗口共 ${shown.length} 项，先显示 ${cap} 项；放大后可查看更多。`));
  if(WB.state.mode==="actions"){const x=left+WB.state.cursor*pps;svg.append(WB.svg("line",{x1:x,y1:25,x2:x,y2:height,stroke:WB.themeColor("color-axis-cursor","#e5bd72"),"stroke-dasharray":"4 4"}));}
  if(WB.state.pointer?.id&&WB.state.pointer.next!=null){const x=left+WB.state.pointer.next*pps;svg.append(WB.svg("line",{x1:x,y1:25,x2:x,y2:height,stroke:WB.themeColor("color-axis-selected","#fff2b5"),"stroke-width":2}),
    WB.svg("text",{x:x+5,y:38,fill:WB.themeColor("color-axis-selected","#fff2b5"),"font-size":12},`${WB.number(WB.state.pointer.next)} 秒`));}
  if(focusId)restoreAxisFocus(wrap,focusId);
};
const showCrowded =(events:any)=>{
  const d=WB.dialog("密集动作","只调整卡片显示位置，不改变动作时点。选择一项后在右侧编辑；全部动作也可在顺序表查找。");if(!d)return null;
  const body=UI.el("div",null,"wb-catalog");let page=0;
  const render=()=>{body.innerHTML="";for(const event of events.slice(page*40,page*40+40))body.append(UI.button(`${WB.number(event.start)} 秒 · ${WB.actor(event.character)?.name||"角色"} · ${event.label}`,()=>{
    WB.closeDialog(d);WB.selectEvent(event);}));const controls=UI.el("div",null,"wb-toolbar"),prev=UI.button("前 40 项",()=>{page--;render();}),next=UI.button("后 40 项",()=>{page++;render();});
    prev.disabled=page===0;next.disabled=(page+1)*40>=events.length;controls.append(prev,next);body.append(controls);};
  d.appBody.append(body,UI.button("返回白板",()=>WB.closeDialog(d)));d.addEventListener("cancel",()=>WB.closeDialog(d));render();
};
const selectEvent =(event:any,fromGroup=false)=>{
  if(!fromGroup)WB.state.derivedBack=null;if(event.events)WB.state.groupPage=0;
  if(WB.state.mode==="actions"){WB.state.selectedAction=event.id;WB.state.selectedActor=event.character;WB.state.cursor=event.start;}
  else {WB.state.selectedDerived=event;if(event.events)WB.state.derivedBack=null;}WB.render();services.workspace?.reveal?.("whiteboard","right");
};

  return {zoom,svg,draw,showCrowded,selectEvent,restoreAxisFocus};
}
