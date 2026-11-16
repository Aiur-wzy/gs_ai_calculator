import {type DisplayRecord} from "./display-rows";
export interface EvidenceIndex {events:Map<string,DisplayRecord[]>;instances:Map<string,DisplayRecord[]>;buffs:Map<string,DisplayRecord[]>;rules:Map<string,DisplayRecord[]>;actions:Map<string,DisplayRecord[]>;}
const cache=new WeakMap<object,EvidenceIndex>();
const add=(map:Map<string,DisplayRecord[]>,key:unknown,event:DisplayRecord)=>{
  if(typeof key!=="string"||!key)return;const rows=map.get(key)||[];if(!rows.includes(event))rows.push(event);map.set(key,rows);
};
export function evidenceIndex(projection:any):EvidenceIndex {
  if(cache.has(projection))return cache.get(projection)!;
  const index:EvidenceIndex={events:new Map(),instances:new Map(),buffs:new Map(),rules:new Map(),actions:new Map()};
  for(const [mode,view] of Object.entries(projection.views||{}) as [string,any][]){for(const event of view.events||[]){
    const d=event.detail||{};if((d.cycle??d.source_cycle??0)!==0)continue;
    add(index.events,event.id,event);
    if(mode==="effects"){
      add(index.instances,d.instance_id,event);add(index.buffs,d.buff_id??d.buff?.id,event);add(index.rules,d.rule_id,event);
    }
    if(mode==="actions")add(index.actions,d.action_id,event);
  }}
  cache.set(projection,index);return index;
}
export function modifierEvidence(projection:any,modifier:any,hit:DisplayRecord={}):{events:DisplayRecord[];sources:any[];actions:DisplayRecord[];message?:string}{
  const index=evidenceIndex(projection),exec=modifier.execution||{},sample=modifier.sampling||{};
  let events:DisplayRecord[]=[];
  if(exec.instance_id)events=index.instances.get(exec.instance_id)||[];
  else if(modifier.buff_id)events=index.buffs.get(modifier.buff_id)||[];
  // A rule without an exact instance identifies sources, not proof of interval consumption.
  if(exec.rule_id)events=events.filter(e=>e.detail?.rule_id===exec.rule_id);
  if(hit.character!=null)events=events.filter(e=>e.character==null||e.character===hit.character);
  for(const key of ["target_id","area_id"])if(hit[key]!=null)events=events.filter(e=>e.detail?.[key]==null||e.detail[key]===hit[key]);
  const time=sample.sample_t??exec.sample_t;
  if(Number.isFinite(time))events=events.filter(e=>e.start<=time&&time<e.end);
  const sourceEvents=events.length?events:(exec.rule_id?index.rules.get(exec.rule_id)||[]:[]);
  const sources:any[]=[],seen=new Set<string>(),actions:DisplayRecord[]=[];
  for(const e of sourceEvents){
    const d=e.detail||{};
    for(const binding of d.source_bindings||[]){const key=JSON.stringify(binding);if(!seen.has(key)){seen.add(key);sources.push(binding);}}
    if(d.source){const key=JSON.stringify(d.source);if(!seen.has(key)){seen.add(key);sources.push(d.source);}}
    // Trigger navigation is only offered for a known consumed instance/window.
    if(events.includes(e))for(const a of index.actions.get(d.trigger_action?.id)||[])if(!actions.includes(a))actions.push(a);
  }
  return {events,sources,actions,...(!events.length?{message:"该结果未记录可唯一定位的 Buff 区间；不按名称或时间推断。"}:{})};
}
export interface TimelineSelection {contract:"timeline-selection-v1";owner:string;result:string;view:string;event:string;explanation?:string;}
export function selectionReference(owner:string,projection:any,event:any,view:string):TimelineSelection|null {
  if(!owner||!projection.explanationFingerprint||!event?.id)return null;
  return {contract:"timeline-selection-v1",owner,result:projection.explanationFingerprint,view,event:event.id,
    ...(event.detail?.explanation_ref?{explanation:event.detail.explanation_ref}:{})};
}
export function resolveSelection(owner:string,projection:any,ref:TimelineSelection):DisplayRecord|null {
  if(ref?.contract!=="timeline-selection-v1"||owner!==ref.owner||projection.explanationFingerprint!==ref.result)return null;
  const rows=(projection.views?.[ref.view]?.events||[]).filter((e:any)=>e.id===ref.event
    &&(!ref.explanation||e.detail?.explanation_ref===ref.explanation));
  return rows.length===1?rows[0]:null;
}
