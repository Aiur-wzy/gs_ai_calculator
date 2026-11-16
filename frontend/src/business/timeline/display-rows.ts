/** Read-only presentation identities. Never deduplicates execution records. */
import {isCoverageKind} from "./shared";
import {sourceRowName} from "./display-names";
export type DisplayRecord=Record<string,any>;
export interface DisplayItem {events:DisplayRecord[];start:number;end:number;last?:number;interval:boolean;state?:DisplayRecord;}
export interface DisplayRow {key:string;id:string;character:any;sourceKey:any;sourceLabel:string;shortLabel:string;label:string;kind:string;events:DisplayRecord[];expandable:boolean;expanded:boolean;logicalEvents?:DisplayRecord[];directory?:boolean;directoryMembers?:DisplayRecord[];sourceCount?:number;sourceNumber?:number;instanceNumber?:number;}
const stable=(value:any):string=>JSON.stringify(value,(_key,item)=>item&&typeof item==="object"&&!Array.isArray(item)
  ?Object.fromEntries(Object.keys(item).sort().map(key=>[key,item[key]])):item);
const scope=(e:DisplayRecord)=>{
  const d=e.detail||{};
  return [d.target_id??d.hit?.target_id??null,d.area_id??d.hit?.area_id??null,d.scope??null,
    d.domain??null,d.field_id??null,d.cycle??d.source_cycle??0];
};
function family(e:DisplayRecord):any {
  const d=e.detail||{},bindings=d.source_bindings;
  // Only a saved binding's exact identity can associate distinct rule IDs.
  if(isCoverageKind(e.kind)&&bindings?.length===1&&bindings[0].binding_id){
    // A whole equipment binding is not a single effect. Keep its parts and
    // exact rule separate unless an explicit component relation is recorded.
    return ["binding",bindings[0].binding_id,bindings[0].part_id??d.rule_id??e.sourceKey];
  }
  return ["source",e.sourceKey??d.rule_id??d.buff_id??d.buff?.id??["unknown",e.id]];
}
export function displayRowKey(e:DisplayRecord,mode:string):string {
  if(mode==="actions")return stable(["actions",e.character]);
  const owner=e.detail?.owner??e.character??null;
  return stable([mode,owner,family(e),mode==="effects"&&!isCoverageKind(e.kind)?"points":e.kind,scope(e)]);
}
function memberKey(e:DisplayRecord):string {
  const d=e.detail||{};
  return stable([d.instance_id??d.receipt_id??d.buff_id??d.buff?.id??e.id,
    d.establishment_id??d.field_instance_id??null,e.character,scope(e)]);
}
export function buildDisplayRows(events:DisplayRecord[],mode:string,expanded:ReadonlySet<string>=new Set()):DisplayRow[]{
  const groups=new Map<string,DisplayRecord[]>();
  for(const e of events){if(!Number.isFinite(e.start))continue;const key=displayRowKey(e,mode);
    if(!groups.has(key))groups.set(key,[]);groups.get(key)!.push(e);}
  const rows:DisplayRow[]=[];
  for(const [key,records] of groups){
    const ordered=[...records].sort((a,b)=>a.start-b.start),first=ordered[0];
    const members=new Map<string,DisplayRecord[]>();
    for(const e of ordered){const k=memberKey(e);if(!members.has(k))members.set(k,[]);members.get(k)!.push(e);}
    const expandable=isCoverageKind(first.kind)&&members.size>1,isExpanded=expandable&&expanded.has(key);
    const add=(id:string,items:DisplayRecord[],instanceNumber?:number)=>{const suffix=instanceNumber?` · 实例 ${instanceNumber}`:"";rows.push({key,id,character:first.detail?.owner??first.character,
      sourceKey:first.sourceKey,sourceLabel:(first.sourceLabel||first.label||"来源未记录")+suffix,
      label:(first.sourceLabel||first.label||"来源未记录")+suffix,shortLabel:sourceRowName(first),
      kind:mode==="effects"&&!isCoverageKind(first.kind)?"points":first.kind,events:items,expandable,expanded:isExpanded,
      ...(isExpanded?{logicalEvents:ordered,instanceNumber}:{})});};
    if(isExpanded){let index=0;for(const items of members.values())add(`${key}:member:${index}`,items,++index);}
    else add(key,ordered);
  }
  return rows.sort((a,b)=>Number(isCoverageKind(b.kind))-Number(isCoverageKind(a.kind)));
}
/** A directory reduces scroll only. It makes no claim about shared source or uptime. */
export function foldSourceDirectories(rows:DisplayRow[],expanded:ReadonlySet<string>=new Set()):DisplayRow[]{
  const groups=new Map<string,DisplayRow[]>(),keys=new Map<DisplayRow,string>();
  for(const row of rows){if(!isCoverageKind(row.kind)||!row.events.length)continue;
    const logicalEvents=row.logicalEvents??row.events;
    const e=logicalEvents[0],name=e.detail?.source_name||e.sourceLabel;
    if(typeof name!=="string"||!name.trim())continue;
    const recipients=[...new Set(logicalEvents.map(e=>e.character??null))].sort();
    const key=stable(["source-directory",row.character,name,row.kind,recipients,scope(e)]);
    keys.set(row,key);if(!groups.has(key))groups.set(key,[]);groups.get(key)!.push(row);
  }
  const out:DisplayRow[]=[],seen=new Set<string>();
  for(const row of rows){const key=keys.get(row),members=key?groups.get(key)!:[];
    const sources=[...new Set(members.map(r=>r.key))],sourceCount=sources.length;
    if(!key||sourceCount<2){out.push(row);continue;}if(seen.has(key))continue;seen.add(key);
    const opened=expanded.has(key),records=members.flatMap(r=>r.events);
    out.push({...row,id:key,key,kind:"source_directory",directory:true,sourceCount,directoryMembers:records,
      label:`${row.sourceLabel} · ${sourceCount} 个独立来源`,sourceLabel:`${row.sourceLabel} · 同名条目（来源独立）`,
      instanceNumber:undefined,events:opened?[]:records,expandable:true,expanded:opened});
    if(opened){const numbers=new Map(sources.map((source,i)=>[source,i+1]));
      out.push(...members.map(r=>{const sourceNumber=numbers.get(r.key),suffix=` · 来源 ${sourceNumber}`;
        return {...r,label:r.label+suffix,sourceLabel:r.sourceLabel+suffix,sourceNumber};}));}
  }
  return out;
}
/** Sweep exact boundaries; gaps and states survive even when instances share a row. */
export function intervalItems(events:DisplayRecord[]):DisplayItem[]{
  const boundaries=new Map<number,{add:DisplayRecord[];remove:DisplayRecord[]}>();
  const stateAt=new Map<DisplayRecord,DisplayRecord>();
  const boundary=(t:number)=>{if(!boundaries.has(t))boundaries.set(t,{add:[],remove:[]});return boundaries.get(t)!;};
  for(const e of events){
    if(!(e.end>e.start))continue;
    const states=Array.isArray(e.detail?.states)?e.detail.states:[];
    const slices=states.filter((s:any)=>Number.isFinite(s.start)&&Number.isFinite(s.end)&&s.end>s.start);
    if(slices.length){for(const s of slices){const start=Math.max(e.start,s.start),end=Math.min(e.end,s.end);if(end<=start)continue;
      const token={event:e};stateAt.set(token,s);boundary(start).add.push(token);boundary(end).remove.push(token);}}
    else {const token={event:e};stateAt.set(token,e.detail||{});boundary(e.start).add.push(token);boundary(e.end).remove.push(token);}
  }
  const times=[...boundaries.keys()].sort((a,b)=>a-b),active=new Set<DisplayRecord>(),out:DisplayItem[]=[];
  for(let i=0;i<times.length-1;i++){const start=times[i],end=times[i+1],change=boundaries.get(start)!;
    for(const token of change.remove)active.delete(token);for(const token of change.add)active.add(token);
    if(!active.size||end<=start)continue;
    const tokens=[...active],records=[...new Set(tokens.map(x=>x.event))];
    const states=tokens.map(t=>stateAt.get(t)!);
    const summaries=states.map(s=>({status:s.status??s.state_status,layers:s.layers,
      remaining_quota:s.remaining_quota,resource_balance:s.resource_balance}));
    const reference=stable(summaries[0]),single=summaries.every(s=>stable(s)===reference);
    out.push({events:records,start,end,interval:true,state:single?{...states[0],start,end}:
      {status:"mixed",layers:null,remaining_quota:null,resource_balance:null,label:"多个实例 / 接收者",start,end}});
  }
  return out;
}
export function packDisplayItems(events:DisplayRecord[],pps:number,mode:string,pointPixels=24):DisplayItem[]{
  const intervals=events.filter(e=>e.end>e.start),points=events.filter(e=>!(e.end>e.start));
  // Actions/manual editable bars remain individually selectable, without factual merging.
  const items=mode==="effects"?intervalItems(intervals):intervals.map(e=>({events:[e],start:e.start,end:e.end,interval:true}));
  let point:DisplayItem|undefined;
  for(const e of [...points].sort((a,b)=>a.start-b.start)){
    if(point&&e.kind===point.events[0].kind&&(e.start-point.start)*pps<pointPixels){point.events.push(e);point.last=e.start;}
    else {point={events:[e],start:e.start,end:e.start,last:e.start,interval:false};items.push(point);}
  }
  return items.sort((a,b)=>a.start-b.start);
}
export function itemSelection(item:DisplayItem,row?:DisplayRow):DisplayRecord {
  const context=row?.expandable?{displayRow:row}:{};
  if(item.events.length>1)return {...item,label:item.interval?row?.sourceLabel||"持续效果":`${item.events.length} 条记录`,kind:"group",...context};
  const original=item.events[0];
  if(item.interval&&item.state?.states)return row?.expandable?{...original,...context}:original;
  if(item.interval&&item.state)return {...original,start:item.start,end:item.end,originalEvent:original,
    detail:{...original.detail,...item.state,state_status:item.state.status??original.detail?.state_status,states:[item.state]},...context};
  return row?.expandable?{...original,...context}:original;
}

export function recipientNames(event:DisplayRecord,nameOf:(id:string)=>string|undefined):string[] {
  if(event.beneficiary_names?.length)return [...new Set<string>(event.beneficiary_names)];
  const ids=event.characters?.length?event.characters:[event.character];
  return [...new Set<string>(ids.map((id:any)=>typeof id==="string"?nameOf(id):undefined).filter(Boolean))];
}

export function coverageHistory(selection:DisplayRecord):DisplayRecord {
  const original=selection.originalEvent;
  return original?.detail?.states?.length>1?original:selection;
}
