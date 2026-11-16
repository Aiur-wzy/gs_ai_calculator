import {useEffect,useMemo,useRef,useState} from "react";
import {Button} from "@deepseek-ai/dsh-client-ui-primitives";
import type {AppEditorBridge} from "./app-editor-bridge";
import {intervalItems} from "../business/timeline/display-rows";
import {selectionReference} from "../business/timeline/evidence-index";
import {timelineDocumentOwner} from "../business/timeline/navigation";
import {detachedWindow,presentInMain,useWindowPresentation} from "./app-window-presentation";
type Row=Record<string,unknown>;
type Event={id:string;label?:string;character?:unknown;start:number;end?:number;detail?:unknown;value?:number};
type Projection={duration:number;characters:{id:unknown;label:string}[];views:Record<string,{events:Event[];notes?:string[]}>};
type Mark={events:Event[];start:number;end:number;interval:boolean};
const rows=(value:unknown):Row[]=>Array.isArray(value)?value.filter(row=>row&&typeof row==="object"):[];
const object=(value:unknown):Row=>value&&typeof value==="object"&&!Array.isArray(value)?value as Row:{};
const text=(value:unknown)=>typeof value==="string"||typeof value==="number"?String(value):"未填写";
const when=(event:Event)=>`${event.start.toFixed(2)}秒${typeof event.end==="number"&&event.end>event.start?`至 ${event.end.toFixed(2)}秒`:""}`;

function candidateMarks(events:Event[],pixelsPerSecond:number):Mark[]{
  const intervals=intervalItems(events.filter(event=>Number.isFinite(event.end)&&event.end!>event.start)) as Mark[];
  const points:Mark[]=[];
  const pointWidth=8/pixelsPerSecond;
  for(const event of events.filter(item=>!Number.isFinite(item.end)||item.end!<=item.start).sort((a,b)=>a.start-b.start)){
    const previous=points.at(-1);
    if(previous&&event.start<=previous.end+pointWidth){previous.events.push(event);previous.end=event.start;}
    else points.push({events:[event],start:event.start,end:event.start,interval:false});
  }
  return [...intervals,...points];
}

export function CandidatePreview({document:source,calculated,hasCalculation,kind,editor,sessionId,conversationId}:{document:Row;calculated?:Row;hasCalculation?:boolean;kind:"native"|"whiteboard";editor?:Pick<AppEditorBridge,"projectCandidate"|"revealTimelineEvent">;sessionId?:string;conversationId?:string}) {
  const [view,setView]=useState("actions"),[selected,setSelected]=useState<Event>(),[group,setGroup]=useState<Mark>(),[zoom,setZoom]=useState(1);
  const [memberPage,setMemberPage]=useState(0);
  const [navigationMessage,setNavigationMessage]=useState(""),[navigationBusy,setNavigationBusy]=useState(false);
  const [navigationCommand,setNavigationCommand]=useState<string>(),windowState=useWindowPresentation();
  const navigationEpoch=useRef(0);
  useEffect(()=>{navigationEpoch.current++;setNavigationMessage("");setNavigationCommand(undefined);setNavigationBusy(false);},[selected,view,source,calculated]);
  useEffect(()=>{setSelected(undefined);setGroup(undefined);setNavigationMessage("");setNavigationCommand(undefined);},[source,calculated]);
  useEffect(()=>{const result=windowState.command_result;if(navigationCommand&&result?.command_id===navigationCommand){setNavigationMessage(result.message);setNavigationCommand(undefined);}},[windowState.command_result,navigationCommand]);
  const board=source.kind==="genshin-whiteboard"?source:object(object(object(source.extensions).whiteboard_handoff).board);
  const team=rows(board.team).length?rows(board.team):rows(object(source.manual_template).characters);
  const value=useMemo(()=>{
    try{return {projection:editor?.projectCandidate?.(source,kind,calculated,{hasCalculation}) as Projection|undefined};}
    catch{return {error:"当前草稿的部分输入尚不能形成时间轴，可在资料视图继续查看原文。"};}
  },[source,kind,editor,calculated,hasCalculation]);
  const projection=value.projection;
  const duration=projection&&Number.isFinite(projection.duration)&&projection.duration>0?projection.duration:typeof board.duration==="number"?board.duration:20;
  const width=900*zoom,pixelsPerSecond=(width-125)/duration;
  const tracks=useMemo(()=>{
    const lanes=projection?.characters?.length?projection.characters:team.map((actor,index)=>({id:actor.id,label:text(actor.name||`角色 ${index+1}`)}));
    const tracks=lanes.map(({id,label})=>({id,label,events:[] as Event[]}));
    const other={id:null,label:"其他事件",events:[] as Event[]};
    for(const event of projection?.views?.[view]?.events||[]){
      if(Number.isFinite(event.start))(tracks.find(lane=>lane.id===event.character)??other).events.push(event);
    }
    if(other.events.length)tracks.push(other);
    return tracks.map(lane=>({...lane,marks:candidateMarks(lane.events,pixelsPerSecond)}));
  },[projection,view,pixelsPerSecond,source]);
  const laneHeight=48,height=Math.max(90,tracks.length*laneHeight+30);
  const selectMark=(mark:Mark)=>{
    setGroup(mark.events.length>1?mark:undefined);setMemberPage(0);
    setSelected(mark.events.length===1?mark.events[0]:undefined);
  };
  const owner=timelineDocumentOwner(source,kind);
  const selection=view==="damage"&&owner&&selected&&projection?selectionReference(owner,projection,selected,view):null;
  const locate=async()=>{
    if(!selection||navigationBusy)return;setNavigationBusy(true);setNavigationMessage("");
    const epoch=navigationEpoch.current;
    try{
      if(detachedWindow()){
        if(!sessionId)throw new Error("缺少会话身份，无法向主窗口定位。");
        const reply=await presentInMain({command:"reveal_timeline_event",session_id:sessionId,conversation_id:conversationId,selection}) as {command_id?:string};
        if(epoch!==navigationEpoch.current)return;
        if(reply?.command_id)setNavigationCommand(reply.command_id);
        setNavigationMessage("已请求主窗口定位；仅定位相同方案和结果，不导入候选或修改白板。");
      }else{
        if(!editor?.revealTimelineEvent)throw new Error("主窗口定位入口暂不可用。");
        const response=await editor.revealTimelineEvent(selection);if(epoch===navigationEpoch.current)setNavigationMessage(response.message);
      }
    }catch(error){if(epoch===navigationEpoch.current)setNavigationMessage(error instanceof Error?error.message:"无法定位，白板未被修改。");}
    finally{if(epoch===navigationEpoch.current)setNavigationBusy(false);}
  };
  return <div className="gs-candidate-preview">
    <div className="gs-candidate-team">{team.map((actor,index)=><article key={text(actor.id||index)}>
      <h3>{text(actor.name||actor.id)}</h3><p>{actor.level!=null?`${text(actor.level)}级 · `:""}{actor.constellation!=null?`${text(actor.constellation)}命`:""}</p>
      <p>武器：{text(object(actor.weapon).name||object(actor.weapon).weapon_id)}</p>
      <p>圣遗物：{rows(actor.artifact_sets).map(set=>`${text(set.name||set.set_id)} ${text(set.count)}件`).join("、")||"未填写"}</p>
      <details><summary>面板与配装</summary><pre>{JSON.stringify(actor,null,2)}</pre></details>
    </article>)}</div>
    {!team.length&&<p>候选中尚未添加角色。</p>}
    <div className="gs-candidate-toolbar" role="group" aria-label="候选时间轴视图">
      {[["actions","技能释放"],["effects","效果生效"],["damage","伤害事件"]].map(([id,label])=><Button size="sm" variant={view===id?"primary":"outline"} key={id} aria-pressed={view===id} onClick={()=>{setView(id!);setSelected(undefined);setGroup(undefined);}}>{label}</Button>)}
      <label>缩放 <input aria-label="候选时间轴缩放" type="range" min="1" max="4" step=".25" value={zoom} onChange={event=>setZoom(Number(event.target.value))}/></label>
    </div>
    <p>只读候选预览，与当前正式白板分开显示。这里不会运行计算或推测缺少的伤害。</p>
    {calculated&&<p>候选计算总伤：{text(object(object(calculated.result).summary).total_damage)}。计算记录不等于审核许可，是否可同步以 App 当前检查为准。</p>}
    {value.error&&<p role="status">{value.error}</p>}
    <div className="gs-candidate-axis" tabIndex={0} aria-label="候选时间轴，可横向滚动">
      <svg width={width} height={height} viewBox={`0 0 ${width} ${height}`} role="group" aria-label={`${view==="actions"?"技能释放":view==="effects"?"效果生效":"伤害事件"}时间轴`}>
        {Array.from({length:7},(_,i)=>{const x=110+(width-125)*i/6;return <g key={i}><line x1={x} x2={x} y1={20} y2={height} className="gs-candidate-grid"/><text x={x} y={13}>{(duration*i/6).toFixed(1)}s</text></g>;})}
        {tracks.map((lane,laneIndex)=><g key={laneIndex}><text x={4} y={46+laneIndex*laneHeight}>{lane.label}</text>
          {lane.marks.map((mark,index)=>{
            const event=mark.events[0]!,count=mark.events.length;
            const label=count===1?`${when(event)} ${event.label||"事件"}`:`${mark.start.toFixed(2)}秒 · ${count} 条记录`;
            const x=110+Math.max(0,mark.start)*pixelsPerSecond,y=31+laneIndex*laneHeight+(mark.interval?0:19);
            const barWidth=(mark.end-mark.start)*pixelsPerSecond+(mark.interval?0:8);
            const next=lane.marks[index+1];
            const damageLabel=view==="damage"&&count===1&&typeof event.value==="number"?event.value.toLocaleString("zh-CN",{maximumFractionDigits:1}):"";
            const labelSpace=mark.interval?barWidth:(next?next.start-mark.end:duration-mark.end)*pixelsPerSecond-8;
            return <g key={index} tabIndex={0} role="button" aria-label={label} aria-pressed={mark===group || !!selected&&mark.events.includes(selected)}
              onClick={()=>selectMark(mark)} onKeyDown={e=>{if(e.key==="Enter"||e.key===" "){e.preventDefault();selectMark(mark);}}}>
              <title>{label}{damageLabel?` · ${damageLabel}`:""}</title><rect x={x} y={y} width={barWidth} height={mark.interval?16:12} rx={2} className={`gs-candidate-mark ${view}`} />
              {count>1&&barWidth>=18&&<text x={x+3} y={y+10} className="gs-candidate-mark-count">×{count}</text>}
              {damageLabel&&labelSpace>=7*damageLabel.length+5&&<text x={x+(mark.interval?3:10)} y={y+10} className={mark.interval?"gs-candidate-mark-count":undefined}>{damageLabel}</text>}
            </g>;
          })}
        </g>)}
      </svg>
    </div>
    {!tracks.some(lane=>lane.marks.length)&&<p>{view==="actions"?"尚未填写动作时点。":hasCalculation&&!calculated?"当前尚未载入已计算的事件；请先加载已有的效果和伤害记录。":"当前草稿在此视图还没有可展示的显式事件；不代表计算已完成。"}</p>}
    {projection?.views?.[view]?.notes?.map((note,index)=><p key={index}>{note}</p>)}
    {group&&<section aria-label="聚合的候选时间轴记录">
      <p>{group.events.length} 条记录，选择一项查看原始时点与详情。</p>
      <div className="gs-candidate-toolbar">
        {group.events.slice(memberPage*40,(memberPage+1)*40).map((event,index)=><Button key={index} size="sm" variant="outline" aria-pressed={selected===event} onClick={()=>setSelected(event)}>{when(event)} · {event.label||"事件"}</Button>)}
        {memberPage>0&&<Button size="sm" variant="ghost" onClick={()=>setMemberPage(memberPage-1)}>上一页</Button>}
        {(memberPage+1)*40<group.events.length&&<Button size="sm" variant="ghost" onClick={()=>setMemberPage(memberPage+1)}>下一页</Button>}
      </div>
    </section>}
    {selected&&<details open key={`${selected.id}:${selected.start}`} className="gs-candidate-event-detail"><summary>{when(selected)} · {selected.label||"事件"}</summary>
      {view==="damage"&&(selection?<Button size="sm" disabled={navigationBusy} onClick={()=>void locate()}>{navigationBusy?"正在请求定位…":"在主窗口定位这段伤害"}</Button>:<p>此记录缺少完整方案或结果身份，不能跨窗口精确定位。</p>)}
      {view==="damage"&&typeof selected.value==="number"&&<p>本次伤害 {selected.value.toLocaleString("zh-CN",{maximumFractionDigits:1})}</p>}
      {navigationMessage&&<p role="status">{navigationMessage}</p>}<pre>{JSON.stringify(selected.detail||selected,null,2)}</pre></details>}
  </div>;
}
