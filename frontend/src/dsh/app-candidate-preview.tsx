import {useEffect,useMemo,useState} from "react";
import {Button} from "@deepseek-ai/dsh-client-ui-primitives";
import type {AppEditorBridge} from "./app-editor-bridge";
type Row=Record<string,unknown>;
type Event={id:string;label?:string;character?:unknown;start:number;end?:number;detail?:unknown;value?:number};
type Projection={duration:number;characters:{id:unknown;label:string}[];views:Record<string,{events:Event[];notes?:string[]}>};
const rows=(value:unknown):Row[]=>Array.isArray(value)?value.filter(row=>row&&typeof row==="object"):[];
const object=(value:unknown):Row=>value&&typeof value==="object"&&!Array.isArray(value)?value as Row:{};
const text=(value:unknown)=>typeof value==="string"||typeof value==="number"?String(value):"未填写";

export function CandidatePreview({document:source,calculated,kind,editor}:{document:Row;calculated?:Row;kind:"native"|"whiteboard";editor?:Pick<AppEditorBridge,"projectCandidate">}) {
  const [view,setView]=useState("actions"),[selected,setSelected]=useState<Event>(),[zoom,setZoom]=useState(1);
  useEffect(()=>setSelected(undefined),[source]);
  const board=source.kind==="genshin-whiteboard"?source:object(object(object(source.extensions).whiteboard_handoff).board);
  const team=rows(board.team).length?rows(board.team):rows(object(source.manual_template).characters);
  const value=useMemo(()=>{
    try{return {projection:editor?.projectCandidate?.(source,kind,calculated) as Projection|undefined};}
    catch{return {error:"当前草稿的部分输入尚不能形成时间轴，可在资料视图继续查看原文。"};}
  },[source,kind,editor,calculated]);
  const projection=value.projection;
  const duration=projection&&Number.isFinite(projection.duration)&&projection.duration>0?projection.duration:typeof board.duration==="number"?board.duration:20;
  const events=(projection?.views?.[view]?.events||[]).filter(event=>Number.isFinite(event.start));
  const lanes=projection?.characters?.length?projection.characters:team.map((actor,index)=>({id:actor.id,label:text(actor.name||`角色 ${index+1}`)}));
  const extra=events.some(event=>!lanes.some(lane=>lane.id===event.character));
  const allLanes=extra?[...lanes,{id:null,label:"其他事件"}]:lanes;
  const width=900*zoom,height=Math.max(90,allLanes.length*48+30);
  return <div className="gs-candidate-preview">
    <div className="gs-candidate-team">{team.map((actor,index)=><article key={text(actor.id||index)}>
      <h3>{text(actor.name||actor.id)}</h3><p>{actor.level!=null?`${text(actor.level)}级 · `:""}{actor.constellation!=null?`${text(actor.constellation)}命`:""}</p>
      <p>武器：{text(object(actor.weapon).name||object(actor.weapon).weapon_id)}</p>
      <p>圣遗物：{rows(actor.artifact_sets).map(set=>`${text(set.name||set.set_id)} ${text(set.count)}件`).join("、")||"未填写"}</p>
      <details><summary>面板与配装</summary><pre>{JSON.stringify(actor,null,2)}</pre></details>
    </article>)}</div>
    {!team.length&&<p>候选中尚未添加角色。</p>}
    <div className="gs-candidate-toolbar" role="group" aria-label="候选时间轴视图">
      {[["actions","技能释放"],["effects","效果生效"],["damage","伤害事件"]].map(([id,label])=><Button size="sm" variant={view===id?"primary":"outline"} key={id} aria-pressed={view===id} onClick={()=>{setView(id!);setSelected(undefined);}}>{label}</Button>)}
      <label>缩放 <input aria-label="候选时间轴缩放" type="range" min="1" max="4" step=".25" value={zoom} onChange={event=>setZoom(Number(event.target.value))}/></label>
    </div>
    <p>只读候选预览，与当前正式白板分开显示。这里不会运行计算或推测缺少的伤害。</p>
    {calculated&&<p>候选计算总伤：{text(object(object(calculated.result).summary).total_damage)}。计算记录不等于审核许可，是否可同步以 App 当前检查为准。</p>}
    {value.error&&<p role="status">{value.error}</p>}
    <div className="gs-candidate-axis" tabIndex={0} aria-label="候选时间轴，可横向滚动">
      <svg width={width} height={height} viewBox={`0 0 ${width} ${height}`} role="img" aria-label={`${view==="actions"?"技能释放":view==="effects"?"效果生效":"伤害事件"}时间轴`}>
        {Array.from({length:7},(_,i)=>{const x=110+(width-125)*i/6;return <g key={i}><line x1={x} x2={x} y1={20} y2={height} className="gs-candidate-grid"/><text x={x} y={13}>{(duration*i/6).toFixed(1)}s</text></g>;})}
        {allLanes.map((lane,index)=><text key={index} x={4} y={46+index*48}>{lane.label}</text>)}
        {events.map((event,index)=>{const found=allLanes.findIndex(lane=>lane.id===event.character),lane=found<0?allLanes.length-1:found;
          const x=110+Math.max(0,event.start)/duration*(width-125),y=36+Math.max(0,lane)*48;
          const interval=typeof event.end==="number"&&event.end>event.start?Math.max(3,(event.end-event.start)/duration*(width-125)):5;
          return <g key={`${event.id}-${index}`} tabIndex={0} role="button" aria-label={`${event.start.toFixed(2)}秒 ${event.label||"事件"}`} onClick={()=>setSelected(event)} onKeyDown={e=>{if(e.key==="Enter"||e.key===" "){e.preventDefault();setSelected(event);}}}>
            <title>{`${event.start.toFixed(2)}s ${event.label||"事件"}`}</title><rect x={x} y={y} width={interval} height={18} rx={3} className={`gs-candidate-mark ${view}`} />
            {view==="damage"&&typeof event.value==="number"&&<text x={x+8} y={y+14}>{event.value.toLocaleString("zh-CN",{maximumFractionDigits:1})}</text>}
          </g>;
        })}
      </svg>
    </div>
    {!events.length&&<p>{view==="actions"?"尚未填写动作时点。":"当前草稿在此视图还没有可展示的显式事件；不代表计算已完成。"}</p>}
    {projection?.views?.[view]?.notes?.map((note,index)=><p key={index}>{note}</p>)}
    {selected&&<details open className="gs-candidate-event-detail"><summary>{selected.start.toFixed(2)}秒 · {selected.label||"事件"}</summary><pre>{JSON.stringify(selected.detail||selected,null,2)}</pre></details>}
  </div>;
}
