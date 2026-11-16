import {type WhiteboardPorts} from "./ports";
import {packDisplayItems,itemSelection,type DisplayRow} from "../timeline/display-rows";
import {isCoverageKind} from "../timeline/shared";
import {timelineLabelWidth,TIMELINE_ACTION_HEIGHT,TIMELINE_VIEW_HEIGHT,visibleRows,zoomScroll} from "../timeline/geometry";
import {createRowDisclosure,restoreRowDisclosureFocus} from "../timeline/row-disclosure";
import {compactLaneName} from "../timeline/display-names";
import {markerInk} from "../timeline/axis-contrast";
export function createWhiteboardSvg(ports:WhiteboardPorts){
  const WB=ports.owner,UI=ports.ui,$=ports.query,services=ports.services;
const zoom =(factor:any,anchor:any)=>{
  const wrap=WB.state.wrap,width=wrap?.clientWidth||700,x=anchor??width/2,old=WB.state.scale,next=Math.max(1,Math.min(40,old*factor));
   const left=WB.state.geometry?.left??0;WB.state.scale=next;WB.state.scroll=zoomScroll(WB.state.scroll,x,old,next,left);WB.render();
};
const svg =(tag:any,attrs:any,text:any)=>{const n=ports.document.createElementNS("http://www.w3.org/2000/svg",tag);for(const [key,value] of Object.entries(attrs))n.setAttribute(key,String(value));
  if(text!=null)n.textContent=text;return n;};
const restoreAxisFocus=(wrap:any,id:any)=>{
  if(!wrap)return;
  const node=id&&Array.from(wrap.querySelectorAll?.("[data-axis-event]")||[]).find((item:any)=>item.getAttribute("data-axis-event")===id) as any;
  (node||wrap).focus?.({preventScroll:true});
};
const toggleRow = (row: DisplayRow) => {
  WB.state.expandedRows ||= new Set();
  if(WB.state.expandedRows.has(row.key))WB.state.expandedRows.delete(row.key);else WB.state.expandedRows.add(row.key);
  WB.render();
};
const draw =(initialScroll?: {left:number;top:number})=>{
  const {wrap,svg,labelSvg}=WB.state;if(!wrap||!svg)return;const b=WB.state.board,left=0,available=Math.max(80,(wrap.clientWidth||700)-35),pps=available/b.duration*WB.state.scale,
    width=left+b.duration*pps+35,projection=WB.project(),lanes=projection.lanes.length?projection.lanes:[{id:"empty",label:"等待添加"}],laneHeight=WB.state.mode==="actions"?TIMELINE_ACTION_HEIGHT:TIMELINE_VIEW_HEIGHT,
    height=Math.max(220,46+lanes.length*laneHeight);
  const active=ports.document.activeElement,focusId=svg.contains?.(active)?active?.getAttribute("data-axis-event"):null;
  const focusScope=active?.hasAttribute("data-axis-row-key")?labelSvg?.contains?.(active)?"label":svg.contains?.(active)?"plot":null:null;
  WB.state.geometry={left,pps,width};svg.innerHTML="";svg.setAttribute("width",width);svg.setAttribute("height",height);
  if(initialScroll){wrap.scrollLeft=initialScroll.left;wrap.scrollTop=initialScroll.top;}
  const labelWidth=timelineLabelWidth(WB.state.labelsCollapsed);
  if(labelSvg){labelSvg.innerHTML="";labelSvg.setAttribute("width",labelWidth);labelSvg.setAttribute("height",height);labelSvg.style.transform=`translateY(${-wrap.scrollTop||0}px)`;}
  const [firstLane,lastLane]=visibleRows(lanes.length,wrap.scrollTop||0,wrap.clientHeight||600,laneHeight);
  const visibleStart=Math.max(0,(wrap.scrollLeft-left-150)/pps),visibleEnd=(wrap.scrollLeft+(wrap.clientWidth||700)+150-left)/pps;
  let step=1;while(step*pps<60)step=step<5?step*2:step*2.5;
  const laneFill=(lane:any,i:any)=>lane.kind==="coverage"
    ?WB.themeColor("color-axis-coverage-lane","#213036")
    :WB.themeColor(i%2?"color-axis-lane-odd":"color-axis-lane-even",i%2?"#1c2430":"#202937");
  lanes.forEach((lane:any,i:any)=>{if(i>=firstLane&&i<lastLane)svg.append(WB.svg("rect",{x:0,y:40+i*laneHeight,width,height:laneHeight-3,fill:laneFill(lane,i)}));});
  for(let t=Math.max(0,Math.floor(visibleStart/step)*step);t<=Math.min(b.duration,visibleEnd);t+=step){const x=left+t*pps;svg.append(WB.svg("line",{x1:x,y1:25,x2:x,y2:height,stroke:WB.themeColor("color-axis-grid","#303847")}),
    WB.svg("text",{x,y:18,fill:WB.themeColor("color-axis-label-muted","#adb9cc"),"font-size":11},`${WB.number(t)} 秒`));}
  const report=WB.state.result?.result?.damage_report_window;
  if(report?.contract==="damage-report-window-v1"&&Number.isFinite(report.end)&&report.end>=0&&report.end<=b.duration){
    const cutoff=left+report.end*pps,color=WB.themeColor("color-axis-selected","#ffffff");
    svg.append(WB.svg("rect",{x:cutoff,y:25,width:Math.max(0,(b.duration-report.end)*pps),height:height-25,fill:color,opacity:.06,"pointer-events":"none"}),
      WB.svg("line",{x1:cutoff,y1:25,x2:cutoff,y2:height,stroke:color,"stroke-dasharray":"5 4","pointer-events":"none"}));
    svg.append(WB.svg("text",{x:cutoff-4,y:height-6,fill:color,"font-size":11,"text-anchor":"end","pointer-events":"none"},`DPS截止 ${WB.number(report.end)} 秒`));
  }
  const visibleLaneIds=new Set(lanes.slice(firstLane,lastLane).map((l:any)=>l.id));
  const visible=projection.events.filter((e:any)=>visibleLaneIds.has(e.lane||e.character)&&e.start<=visibleEnd&&(e.end??e.start)>=visibleStart),slots=new Map(),crowded=new Map(),shown:any[]=[];
  if(WB.state.mode==="actions")shown.push(...visible);
  else {if(WB.state.axisPacked?.projection!==projection||WB.state.axisPacked.pps!==pps){
      WB.state.axisPacked={projection,pps,rows:projection.lanes.map((lane:any)=>({lane,items:lane.directory?[]:packDisplayItems(lane.events,pps,WB.state.mode)}))};}
    for(const {lane,items} of WB.state.axisPacked.rows){if(!visibleLaneIds.has(lane.id))continue;
      for(const item of items)if(item.start<=visibleEnd&&Math.max(item.end,item.last??item.start)>=visibleStart)
        shown.push({...itemSelection(item,lane),lane:lane.id,character:item.events[0].character,id:item.events[0].id,visualInterval:item.interval});}}
  for(const event of shown){
    const lane=lanes.findIndex((l:any)=>l.id===(event.lane||event.character)),y=40+Math.max(0,lane)*laneHeight,x=left+event.start*pps,
      actorIndex=b.team.findIndex((c:any)=>c.id===event.character),color=event.detail?.state_status==="exhausted"?WB.themeColor("color-axis-exhausted","#89919c"):WB.colors[Math.max(0,actorIndex)%4],selected=WB.state.mode==="actions"?event.id===WB.state.selectedAction:event.id===WB.state.selectedDerived?.id,
      coverage=isCoverageKind(event.kind)||event.visualInterval,range=`${WB.number(event.start)}${event.end>event.start?` → ${WB.number(event.end)}`:""} 秒`,
      description=`${event.sourceLabel||WB.actor(event.character)?.name||"全队"} · ${event.label} · ${range}${coverage?` · ${event.detail?.receipt_id?"实例角色范围":"接收角色"}：${WB.recipients(event)}`:""}${event.value!=null?` · ${WB.number(event.value)} 伤害`:""}`,
      g=WB.svg("g",{class:WB.state.mode==="actions"?"wb-action":coverage?"wb-derived wb-coverage":"wb-derived",tabindex:0,role:"button","aria-label":description,"aria-pressed":selected,"data-axis-event":event.id});
    g.append(WB.svg("title",{},description));
    if(WB.state.mode==="actions"){
      const ends=slots.get(lane)||[-Infinity,-Infinity],slot=ends.findIndex((end:any)=>end<x-5),cardWidth=Math.max(48,Math.min(98,event.label.length*12+16));
      if(slot<0){if(!crowded.has(lane))crowded.set(lane,{x,events:[]});crowded.get(lane).events.push(event);continue;}
      ends[slot]=x+cardWidth+5;slots.set(lane,ends);const top=y+8+slot*27;
      g.append(WB.svg("rect",{x:x-4,y:top,width:cardWidth,height:22,rx:5,fill:color,stroke:selected?WB.themeColor("color-axis-selected","#fff"):WB.themeColor("color-axis-event-border","#182230"),"stroke-width":selected?3:1}),
        WB.svg("text",{x:x+3,y:top+16,fill:markerInk(color),"font-size":12,"pointer-events":"none"},event.label.slice(0,7)));
      g.addEventListener("pointerdown",(e:any)=>{if(e.button===1){WB.beginPointer(e,null);return;}WB.beginPointer(e,event.id);});
      WB.bindCommandMenu(g,`${event.label}操作`,()=>WB.actionMenuItems(event.id));
    }else if(event.end>event.start){const barWidth=Math.max(3,(event.end-event.start)*pps);g.append(WB.svg("rect",{x,y:y+14,width:barWidth,height:28,rx:4,fill:color,opacity:0.78,stroke:selected?WB.themeColor("color-axis-selected","#fff"):color,"stroke-width":selected?2:1,...(event.state?.status==="mixed"?{"stroke-dasharray":"4 2"}:{})}));
      if(coverage){const label=WB.svg("svg",{x:x+1,y:y+15,width:Math.max(1,barWidth-2),height:26,overflow:"hidden","pointer-events":"none"});
        label.append(WB.svg("text",{x:5,y:18,fill:markerInk(color,0.78,laneFill(lanes[lane],lane)),"font-size":11},`${event.detail?.state_status==="exhausted"?"耗尽 · ":""}${event.events?`${event.events.length}项 · `:event.detail?.layers!=null?`${event.detail.layers}层 · `:""}${range}`));g.append(label);}}
    else if(event.events){g.append(WB.svg("rect",{x:x-8,y:y+14,width:Math.max(22,String(event.events.length).length*7+12),height:23,rx:5,fill:color,stroke:selected?WB.themeColor("color-axis-selected","#fff"):WB.themeColor("color-axis-event-border","#172330")}),
      WB.svg("text",{x:x-2,y:y+30,fill:markerInk(color),"font-size":11,"pointer-events":"none"},String(event.events.length)));}
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
  for(const [lane,group] of crowded){const marker=WB.svg("g",{class:"wb-derived",tabindex:0,role:"button","aria-label":`查看同区域 ${group.events.length} 个动作`}),y=40+lane*laneHeight+70;
    marker.append(WB.svg("rect",{x:Math.max(left,group.x)-4,y:y-15,width:120,height:20,fill:"transparent","pointer-events":"all"}));
    marker.append(WB.svg("text",{x:Math.max(left,group.x),y,fill:WB.themeColor("color-axis-warning","#f4cd84"),"font-size":11},`＋ ${group.events.length} 项动作`));
    const open=()=>WB.showCrowded(group.events);marker.addEventListener("pointerdown",(e:any)=>{if(e.button===0)e.stopPropagation();});
    marker.addEventListener("click",open);marker.addEventListener("keydown",(e:any)=>{if(e.key==="Enter"||e.key===" "){e.preventDefault();open();}});svg.append(marker);}
  // Source labels stay readable when panning every timeline mode.
  lanes.forEach((lane:any,i:any)=>{if(!labelSvg||i<firstLane||i>=lastLane)return;const y=40+i*laneHeight;
    if(lane.directory){const caption=`${lane.sourceCount} 个独立来源 · ${lane.expanded?"收起":"展开查看各自区间"}`;
      svg.append(createRowDisclosure(ports.document,lane,{x:(wrap.scrollLeft||0)+9,y:y+7,width:Math.min(Math.max(24,(wrap.clientWidth||700)-12),caption.length*13+20),
        height:34,className:"wb-source-directory",caption},control=>{WB.toggleRow(lane);restoreRowDisclosureFocus(WB.state.svg,control);}));}
    if(lane.expandable)labelSvg.append(createRowDisclosure(ports.document,lane,{x:0,y:y+3,width:18,height:34,className:"tl-row-disclosure"},
      control=>{WB.toggleRow(lane);restoreRowDisclosureFocus(WB.state.labelSvg,control);}));
    const labelX=lane.expandable?18:6;
    const label=WB.svg("svg",{x:labelX,y,width:labelWidth-labelX-3,height:laneHeight-3,overflow:"hidden",tabindex:0,role:"button","aria-label":lane.label});
    label.append(WB.svg("title",{},lane.label));
    const owner=WB.state.mode==="actions"?undefined:WB.actor(lane.character)?.name;
    const title=compactLaneName(lane.shortLabel||lane.label,owner,7);
    const qualifier=lane.directory?`${lane.sourceCount} 个来源`:lane.sourceNumber?`来源 ${lane.sourceNumber}${lane.instanceNumber?` / ${lane.instanceNumber}`:""}`:
      lane.instanceNumber?`实例 ${lane.instanceNumber}`:"";
    label.append(WB.svg("text",{x:0,y:20,class:"tl-lane-label","font-size":12},WB.state.labelsCollapsed?String(i+1):title));
    if((owner||qualifier)&&!WB.state.labelsCollapsed)label.append(WB.svg("text",{x:0,y:38,class:"tl-lane-subtitle","font-size":11},[owner,qualifier].filter(Boolean).join(" · ")));
    const open=()=>{if(lane.directoryMembers?.length||lane.events?.length)WB.selectEvent({id:lane.id,events:lane.directoryMembers||lane.events,label:lane.label,displayRow:lane});else {WB.state.selectedActor=lane.id;WB.render();services.workspace?.reveal?.("whiteboard","left");}};
    label.addEventListener("click",open);label.addEventListener("keydown",(e:any)=>{if(e.key==="Enter"||e.key===" "){e.preventDefault();open();}});labelSvg.append(label);});
  if(WB.state.mode==="actions"){const x=left+WB.state.cursor*pps;svg.append(WB.svg("line",{x1:x,y1:25,x2:x,y2:height,stroke:WB.themeColor("color-axis-cursor","#e5bd72"),"stroke-dasharray":"4 4"}));}
  if(WB.state.pointer?.id&&WB.state.pointer.next!=null){const x=left+WB.state.pointer.next*pps;svg.append(WB.svg("line",{x1:x,y1:25,x2:x,y2:height,stroke:WB.themeColor("color-axis-selected","#fff2b5"),"stroke-width":2}),
    WB.svg("text",{x:x+5,y:38,fill:WB.themeColor("color-axis-selected","#fff2b5"),"font-size":12},`${WB.number(WB.state.pointer.next)} 秒`));}
  if(focusScope)restoreRowDisclosureFocus(focusScope==="label"?labelSvg:svg,active);
  else if(focusId)restoreAxisFocus(wrap,focusId);
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
  WB.state.evidenceAction=false;
  if(!fromGroup)WB.state.derivedBack=null;if(event.events)WB.state.groupPage=0;
  if(WB.state.mode==="actions"){WB.state.selectedAction=event.id;WB.state.selectedActor=event.character;WB.state.cursor=event.start;}
  else {WB.state.selectedDerived=event;if(event.events)WB.state.derivedBack=null;}WB.render();services.workspace?.reveal?.("whiteboard","right");
};

  return {zoom,svg,draw,showCrowded,selectEvent,restoreAxisFocus,toggleRow};
}
