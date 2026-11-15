import {type TimelineDomPorts,createTimelineDom} from "./dom";
import {isCoverageKind} from "./shared";
export function createTimelineSvg(ports:TimelineDomPorts){
  const TV=ports.view,RT=ports.editor,$=ports.query,TD=ports.projection,services=ports.services;
  const {VIEW_ROW_H,ACTION_ROW_H,viewThemeColor,viewKind,viewName,viewStatus,viewSeconds,viewTime}=createTimelineDom(ports);
  const LABEL_W=210,RULER_H=44;
  const svgEl=(tag:string,attrs:Record<string,any>={}):any=>{const node=ports.document.createElementNS("http://www.w3.org/2000/svg",tag);for(const [key,value] of Object.entries(attrs))node.setAttribute(key,String(value));return node;};
const color = (character:any) => {
  const index=(TV.projection.characters||[]).findIndex((c:any)=>c.id===character);
  const slot=index>=0?index%6:0;
  return viewThemeColor(`color-axis-character-${slot+1}`,
    ["#5b8cff","#e06c75","#61af72","#d19a66","#c678dd","#56b6c2"][slot] || "#9aa3b5");
};

const updateDetailSelection = () => {
  const selected=TV.detailSelection;
  const ids=new Set((selected?.events || (selected?[selected]:[])).map((event:any)=>event.id));
  for(const {node,events} of TV.markers || []){
    const active=events.some((event:any)=>ids.has(event.id));
    node.classList.toggle("tl-event-selected",active);node.setAttribute("aria-pressed",String(active));
  }
};

const paintProjection = () => {
  if(!TV.projection)return;
  TV.paintQueued=false;
  const template=RT.tpl(), svg=$("#timeline"), wrap=$("#timeline-wrap");
  TV.markers=[];
  // Read the container before replacing content; CSS reserves scrollbar space.
  const width=TV.prepare(template, Math.max(0.001,TV.projection.duration || 10));
  RT.pxPerSec=TV.pixelsPerSecond;
  TV.packRows(TV.pixelsPerSecond);
  const rowHeight=TV.mode==="actions"?ACTION_ROW_H:VIEW_ROW_H;
  const height=Math.max(100,RULER_H+TV.rows.length*rowHeight+16);
  svg.setAttribute("width",width);svg.setAttribute("height",height);
  const nodes:any[]=[], labels=svgEl("g",{class:"tl-label-overlay"}), ruler=svgEl("g",{class:"tl-ruler-overlay"});
  const viewportWidth=wrap.clientWidth || 800, viewportHeight=wrap.clientHeight || 500;
  const top=wrap.scrollTop || 0, left=wrap.scrollLeft || 0;
  const startTime=Math.max(0,(left-24)/TV.pixelsPerSecond);
  const endTime=(left+Math.max(1,viewportWidth-LABEL_W)+24)/TV.pixelsPerSecond;
  const X=(sec:any)=>LABEL_W+sec*TV.pixelsPerSecond;
  const first=Math.max(0,Math.floor((top-RULER_H)/rowHeight)-2);
  const last=Math.min(TV.rows.length,Math.ceil((top+viewportHeight-RULER_H)/rowHeight)+2);
  labels.appendChild(svgEl("rect",{x:0,y:top,width:LABEL_W-8,height:viewportHeight,class:"tl-overlay-bg"}));
  ruler.appendChild(svgEl("rect",{x:left,y:0,width:viewportWidth,height:RULER_H,class:"tl-overlay-bg"}));
  const step=TV.tickStep(TV.pixelsPerSecond);
  for(let s=Math.max(0,Math.ceil(startTime/step)*step);s<=Math.min(TV.duration,endTime);s+=step){
    const time=Number(s.toPrecision(12));
    nodes.push(svgEl("line",{x1:X(time),y1:top+RULER_H-8,x2:X(time),y2:top+viewportHeight,class:"tl-tick"}));
    const text=svgEl("text",{x:X(time)+3,y:20,class:"tl-tick-label"});text.textContent=`${time}秒`;ruler.appendChild(text);
  }
  let drawn=0;
  for(let index=first;index<last;index++){
    const row=TV.rows[index], y=RULER_H+index*rowHeight;
    const owner=TV.characterNames.get(row.character), name=(owner && !row.sourceLabel.startsWith(owner)?`${owner} · `:"")
      +row.sourceLabel+` · ${viewKind(row.kind).label}`;
    const actionLane=TV.mode==="actions";
    const sourceName=owner && row.sourceLabel.startsWith(`${owner} · `)?row.sourceLabel.slice(owner.length+3):row.sourceLabel;
    const primary=actionLane?(owner||"全队"):sourceName;
    const secondary=actionLane?"技能释放 · 切换角色":`${owner||"全队"} · ${row.stateStatus?TD.stateName(row.stateStatus):viewKind(row.kind).label}`;
    labels.appendChild(svgEl("circle",{cx:10,cy:y+20,r:4,fill:TV.color(row.character),class:"tl-character-color"}));
    const text=svgEl("text",{x:20,y:y+18,class:"tl-lane-label"});text.textContent=primary.length>14?`${primary.slice(0,13)}…`:primary;
    const title=svgEl("title");title.textContent=actionLane?secondary+` · ${primary}`:name;text.appendChild(title);labels.appendChild(text);
    const sub=svgEl("text",{x:20,y:y+34,class:"tl-lane-subtitle"});sub.textContent=secondary;labels.appendChild(sub);
    nodes.push(svgEl("line",{x1:left+LABEL_W-4,y1:y,x2:left+viewportWidth,y2:y,class:"tl-lane-line"}));
    const items=TV.visibleItems(row,startTime,endTime);
    for(let i=0;i<items.length;i++){
      const item=items[i], event=item.events[0], count=item.events.length;
      const kind=viewKind(event.kind), color=event.detail?.state_status==="exhausted"?viewThemeColor("color-axis-exhausted","#89919c"):TV.mode==="actions" && event.kind==="action"?TV.color(event.character):kind.color, group=svgEl("g",{class:`tl-view-event tl-${event.kind}`,
        "data-event-id":event.id,"data-event-count":count,role:"button",tabindex:0});
      const sec=item.interval?item.start:(item.start+(item.last??item.start))/2;
      const x=X(sec), cy=y+22;
      let shape;
      if(item.interval){shape=svgEl("rect",{x:X(item.start),y:y+10,width:Math.max(2,(item.end-item.start)*TV.pixelsPerSecond),
        height:24,rx:4,fill:color});}
      else if(kind.shape==="circle")shape=svgEl("circle",{cx:x,cy,r:count>1?8:5,fill:color});
      else if(kind.shape==="diamond")shape=svgEl("polygon",{points:`${x},${cy-7} ${x+7},${cy} ${x},${cy+7} ${x-7},${cy}`,fill:color});
      else if(kind.shape==="triangle")shape=svgEl("polygon",{points:`${x},${cy-7} ${x+7},${cy+6} ${x-7},${cy+6}`,fill:color});
      else shape=svgEl("rect",{x:x-5,y:cy-5,width:10,height:10,fill:color});
      const statuses:string[]=[...new Set<string>(item.events.map((e:any)=>e.detail?.execution_status).filter(Boolean))];
      const failed=statuses.some(s=>["failed","invalid","cancelled"].includes(s));
      if(failed){shape.setAttribute("stroke",viewThemeColor("color-danger","#ff7070"));shape.setAttribute("stroke-width",2);}
      if(statuses.some(s=>!["passed","calculated","executed"].includes(s))){shape.setAttribute("stroke-dasharray","3 2");shape.setAttribute("opacity",0.7);}
      group.appendChild(shape);
      const nextX=i+1<items.length?X(items[i+1].start):left+viewportWidth;
      const available=item.interval?(item.end-item.start)*TV.pixelsPerSecond:nextX-x-14;
      let label=count>1?`×${count}`:viewName(event.label);
      if(count===1 && event.detail?.execution_status && !["passed","calculated","executed"].includes(event.detail.execution_status))label+=`（${viewStatus(event.detail.execution_status)}）`;
      const damageValue=TV.mode==="damage" && item.events.every((e:any)=>["damage","external_damage"].includes(e.kind)&&Number.isFinite(e.value))
        ?Math.round(item.events.reduce((sum:any,e:any)=>sum+e.value,0)).toLocaleString("zh-CN"):null;
      if(count>1 && !item.interval){const text=svgEl("text",{x,y:cy+3,class:"tl-view-count","text-anchor":"middle"});
        text.textContent=count>99?"99+":String(count);group.appendChild(text);
        if(damageValue!==null && available>=(damageValue.length+2)*7){
          const total=svgEl("text",{x:x+12,y:cy+4,class:"tl-view-label"});total.textContent=`Σ ${damageValue}`;group.appendChild(total);
        }
      }
      else {
        const capacity=Math.max(0,Math.floor(available/11));let visibleLabel="";
        if(damageValue!==null){
          // 数值完整优先；不足以显示完整数值时，只留标记与完整提示。
          if(capacity>=damageValue.length){
            const room=capacity-damageValue.length-1;
            const name=room>=2?(label.length<=room?label:`${label.slice(0,room-1)}…`):"";
            visibleLabel=damageValue+(name?` ${name}`:"");
          }
        }else if(item.interval && isCoverageKind(event.kind) && available>=64){
          // The lane already names this effect; the bar communicates duration.
          const range=`${event.detail?.state_status==="exhausted"?"耗尽 ":""}${viewSeconds(item.start)}–${viewSeconds(item.end)}秒${count>1?` ×${count}`:""}`;
          if(range.length<=capacity)visibleLabel=range;
          else if(count>1)visibleLabel=`×${count}`;
        }
        if(visibleLabel){const text=svgEl("text",{x:item.interval?X(item.start)+5:x+10,y:cy+4,class:"tl-view-label"});
          text.textContent=visibleLabel;group.appendChild(text);}
      }
      const title=svgEl("title");title.textContent=count>1?`${TV.mode==="actions"?owner||"动作":row.sourceLabel} · ${count}条记录，点击逐条查看`
        :`${viewName(event.label)} · ${event.start}秒${item.interval?`～${event.end}秒`:""}`;group.appendChild(title);
      if(count===1 && TV.mode==="effects" && services.effects){
        const description=services.effects.describe(event,{characterName:(id:any)=>TV.characterNames?.get(id)||"未指定角色"});
        title.textContent=[description.title,description.source,description.duration,...description.values].filter(Boolean).join("\n");
      }
      if(damageValue!==null)title.textContent+=` · ${count>1?"本组期望伤害合计":"期望伤害"}：${damageValue}`;
      if(statuses.length)title.textContent+=` · ${statuses.map(viewStatus).join("、")}`;
      group.setAttribute("aria-label",title.textContent);
      TV.markers.push({node:group,events:item.events});
      const selected=count>1?item:event;
      group.addEventListener("click",()=>TV.showDetail(selected));
      group.addEventListener("keydown",(e:any)=>{if(e.key==="Enter" || e.key===" "){e.preventDefault();TV.showDetail(selected);}});
      if(count===1 && event.edit && ["hit","buff"].includes(event.edit.kind))TV.bindEditable(group,event);
      nodes.push(group);drawn++;
    }
    if(actionLane)for(const caption of row.actionLabels){
      if(caption.level<0 || caption.x+caption.width<left || caption.x>left+viewportWidth-LABEL_W)continue;
      const x=LABEL_W+caption.x,cy=y+51+caption.level*21,center=X((caption.start+caption.last)/2);
      const group=svgEl("g",{class:"tl-action-caption",role:"button",tabindex:0,"aria-label":`${caption.text} · ${viewTime(caption.events[0])}${caption.events.length>1?`，${caption.events.length}个动作，点击逐条查看`:""}`});
      group.appendChild(svgEl("line",{x1:center,y1:y+35,x2:x+caption.width/2,y2:cy-12,class:"tl-action-leader"}));
      group.appendChild(svgEl("rect",{x,y:cy-13,width:caption.width,height:19,rx:4,class:"tl-action-badge"}));
      const text=svgEl("text",{x:x+caption.width/2,y:cy,"text-anchor":"middle",class:"tl-action-text"});text.textContent=caption.text;group.appendChild(text);
      const selected=caption.events.length===1?caption.events[0]:{events:caption.events};
      group.addEventListener("click",()=>TV.showDetail(selected));
      group.addEventListener("keydown",(e:any)=>{if(e.key==="Enter"||e.key===" "){e.preventDefault();TV.showDetail(selected);}});
      TV.markers.push({node:group,events:caption.events});nodes.push(group);
    }
  }
  if(!TV.rows.length){const hint=svgEl("text",{x:LABEL_W+12,y:70,class:"tl-hint"});
    hint.textContent=template?"当前视图与筛选没有记录":"加载或新建模板开始排轴";nodes.push(hint);}
  const corner=svgEl("g",{class:"tl-corner-overlay"});corner.appendChild(svgEl("rect",{x:0,y:0,width:LABEL_W-8,height:RULER_H,class:"tl-overlay-bg"}));
  const title=svgEl("text",{x:6,y:23,class:"tl-lane-label"});title.textContent=TV.mode==="actions"?"出场角色":"效果来源 / 角色";corner.appendChild(title);
  // Build off-DOM and swap once, instead of exposing an empty auto-sized SVG.
  if(svg.replaceChildren)svg.replaceChildren(...nodes,labels,ruler,corner);
  else {svg.innerHTML="";svg.append(...nodes,labels,ruler,corner);}
  TV.labels=labels;TV.ruler=ruler;TV.corner=corner;TV.updateOverlays();
  TV.updateDetailSelection();
  const stats=$("#timeline-stats");if(stats){stats.textContent=`${TV.filteredCount} 条记录 · ${TV.rows.length} 条泳道 · ${viewSeconds(TV.projection.duration||10)}秒`;
    stats.title=`聚合后 ${TV.markerCount} 个标记；当前视口绘制 ${drawn} 个。缩放或筛选不改变计算结果。`;}
};

const bindEditable = (group:any,event:any) => {
  const edit=event.edit, target=edit.kind==="hit"?RT.tpl()?.hits?.[edit.index]:RT.tpl()?.buffs?.[edit.index];
  if(!target)return;
  group.addEventListener("pointerdown",(e:any)=>{if(e.button===0)TV.showDetail(event);});
  const start=edit.kind==="hit"?target.t:target.start;
  RT.enableDrag(group,start,(delta:any)=>{
    if(edit.kind==="hit")target.t=RT.round2(Math.max(0,Math.min(TV.duration,target.t+delta)));
    else {const duration=target.end-target.start, next=RT.round2(Math.max(0,Math.min(TV.duration-duration,target.start+delta)));
      target.start=next;target.end=RT.round2(next+duration);}
  },group,null,edit);
};


  return {color,updateDetailSelection,paintProjection,bindEditable};
}
