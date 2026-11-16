import {type TimelineDomPorts,createTimelineDom} from "./dom";
import {isCoverageKind} from "./shared";
import {buildDisplayRows,foldSourceDirectories,packDisplayItems} from "./display-rows";
export function createTimelineLayout(ports:TimelineDomPorts){
  const TV=ports.view,RT=ports.editor,$=ports.query,TD=ports.projection,services=ports.services;
  const {ACTION_CAPTIONS,viewName}=createTimelineDom(ports);
  const LABEL_W=210,RULER_H=44;
const rebuildRows = () => {
  RT.cancelDrag?.();TV.cancelPan?.();
  const view=TV.projection?.views?.[TV.mode] || {events:[],notes:[]};
  const records=(view.events||[]).filter((e:any)=>Number.isFinite(e.start));
  // Area-scoped field observations are not owned by a party member. Keep
  // their state visible while filtering a member, without promising that
  // every attack from that member receives the settlement benefit.
  const selected=records.filter((e:any)=>e.kind!=="calculation_sampling"||TV.showCalculationSampling).filter((e:any)=>!TV.characterFilter || e.character===TV.characterFilter
    || TV.mode==="effects" && ["field_layers","settled_layers"].includes(e.kind)
      && e.character==null && e.detail?.scope==="area");
  const visibleForCharacter=selected;
  TV.sources=new Map(visibleForCharacter.map((e:any)=>[e.sourceKey,viewName(e.sourceLabel)]));
  if(TV.sourceFilter && !TV.sources.has(TV.sourceFilter))TV.sourceFilter="";
  TV.components=new Map(visibleForCharacter.filter((e:any)=>(!TV.sourceFilter || e.sourceKey===TV.sourceFilter)
    && typeof e.componentKey==="string" && e.componentKey).map((e:any)=>
    [e.componentKey,viewName(e.componentLabel)]));
  if(TV.componentFilter && !TV.components.has(TV.componentFilter))TV.componentFilter="";
  const visibility=TV.kindVisibility[TV.mode] ||= {};
  TV.kinds=[...new Set(records.map((e:any)=>e.kind))];
  const filtered:any[]=[];
  TV.filteredCount=0;
  for(const e of visibleForCharacter){
    if(e.kind==="calculation_sampling"&&(!TV.showCalculationSampling||TV.effectFocus&&TV.effectFocus!=="all"))continue;
    if(TV.mode==="effects" && (TV.effectFocus==="coverage" && !isCoverageKind(e.kind)
      || TV.effectFocus==="points" && isCoverageKind(e.kind)))continue;
    if(TV.mode==="damage" && TV.damageFocus!=="all" && e.kind==="calculation")continue;
    if(TV.sourceFilter && e.sourceKey!==TV.sourceFilter || visibility[e.kind]===false)continue;
    if(TV.componentFilter && e.componentKey!==TV.componentFilter)continue;
    TV.filteredCount++;
    // Actions share a character lane; their original source identities remain
    // available for filtering, details and selection. Other views keep sources.
    filtered.push(e);
  }
  const order=new Map<any,number>((TV.projection.characters||[]).map((c:any,i:any)=>[c.id,i]));
  const shared=(row:any)=>row.kind==="coverage" && row.events.some((e:any)=>e.detail?.owner!=null
    && e.characters?.some((id:any)=>id!==e.detail.owner));
  TV.expandedRows ||= new Set();
  TV.rows=buildDisplayRows(filtered,TV.mode,TV.expandedRows).sort((a,b)=>(TV.mode==="effects" ? Number(isCoverageKind(b.kind))-Number(isCoverageKind(a.kind)) : 0)
    ||(TV.mode==="effects" ? Number(shared(b))-Number(shared(a)) : 0)
    ||(order.get(a.character)??999)-(order.get(b.character)??999)
    ||String(a.sourceKey).localeCompare(String(b.sourceKey))||a.kind.localeCompare(b.kind));
  if(TV.mode==="effects")TV.rows=foldSourceDirectories(TV.rows,TV.expandedRows);
  // 同刻记录保留 TD 给出的执行顺序，ID 只是标识，不能当作时序。
  for(const row of TV.rows)row.events.sort((a:any,b:any)=>a.start-b.start);
  TV.packKey=null;
  TV.renderControls(view);
};

const packRows = (pixelsPerSecond:any) => {
  if(TV.packKey===pixelsPerSecond)return;
  TV.packKey=pixelsPerSecond;TV.markerCount=0;
  for(const row of TV.rows){
    const items=row.directory?[]:packDisplayItems(row.events,pixelsPerSecond,TV.mode);
    row.items=items;
    // Prefix maxima allow a binary seek even when an earlier long interval
    // spans many later point records. No event is dropped from a group.
    let end=-Infinity;row.ends=items.map(item=>end=Math.max(end,item.end,item.last??item.start));
    TV.markerCount+=items.length;
    row.actionLabels=TV.mode==="actions"?TV.packActionLabels(row.events,pixelsPerSecond):[];
  }
};

const actionCaption = (event:any) => {
  if(event.kind==="switch")return viewName(event.label).replace(/^切换至/,"切→");
  const type=TV.actionTypes?.get(event.detail?.ability_id);
  if(ACTION_CAPTIONS[type])return ACTION_CAPTIONS[type];
  const label=viewName(event.label),words={元素战技:"战技",元素爆发:"爆发",普通攻击:"普攻",重击:"重击",下落攻击:"下落",冲刺:"冲刺",跳跃:"跳跃",等待:"等待"};
  for(const [name,short] of Object.entries(words))if(label.includes(name))return short;
  return label.length>4?`${label.slice(0,3)}…`:label;
};

const packActionLabels = (events:any,pixelsPerSecond:any) => {
  const labels:any[]=[];
  for(const event of events){
    const caption=TV.actionCaption(event),previous=labels.at(-1);
    // Only annotation text is grouped. Every original action keeps its exact
    // time block and identity; clicking a grouped label lists those actions.
    if(previous && event.kind!=="switch" && previous.caption===caption
        && (event.start-previous.last)*pixelsPerSecond<32){
      previous.events.push(event);previous.last=event.start;
    }else labels.push({events:[event],caption,start:event.start,last:event.start});
  }
  const occupied:number[][][]=[[],[]],plotWidth=Math.max(1,TV.projection.duration*pixelsPerSecond);
  const priority=(label:any)=>["战技","爆发"].includes(label.caption)?0:label.events[0].kind==="switch"?2:1;
  for(const label of [...labels].sort((a,b)=>priority(a)-priority(b)||a.start-b.start)){
    label.text=label.caption+(label.events.length>1?`×${label.events.length}`:"");
    const width=label.text.length*11+8,center=(label.start+label.last)/2*pixelsPerSecond;
    label.x=Math.max(3,Math.min(center-width/2,plotWidth-width-3));label.width=width;
    label.level=occupied.findIndex(spans=>spans.every(span=>label.x+width+4<=span[0]||label.x>=span[1]+4));
    if(label.level>=0)occupied[label.level].push([label.x,label.x+width]);
  }
  return labels;
};

const visibleItems = (row:any, left:any, right:any) => {
  let lo=0,hi=row.ends.length;
  while(lo<hi){const mid=(lo+hi)>>1;if(row.ends[mid]<left)lo=mid+1;else hi=mid;}
  const items:any[]=[];
  for(let i=lo;i<row.items.length && row.items[i].start<=right;i++){
    if(Math.max(row.items[i].end,row.items[i].last??row.items[i].start)>=left)items.push(row.items[i]);
  }
  return items;
};


  return {rebuildRows,packRows,actionCaption,packActionLabels,visibleItems};
}
