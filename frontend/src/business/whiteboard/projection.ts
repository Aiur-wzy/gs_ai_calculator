import {type WhiteboardPorts} from "./ports";
import {buildDisplayRows,foldSourceDirectories} from "../timeline/display-rows";
import {isCoverageKind} from "../timeline/shared";
export function createWhiteboardProjection(ports:WhiteboardPorts){
  const WB=ports.owner,$=ports.query,services=ports.services;
  const rowsCache=new WeakMap<object,Map<string,any>>();
const project =()=>{
  if(WB.state.mode==="actions")return {lanes:WB.state.board.team.map((c:any)=>({id:c.id,label:c.name})),events:WB.state.board.actions.map((a:any)=>({id:a.id,character:a.character,start:a.start,label:WB.actionName(a),action:a}))};
  if(!WB.state.result)return {lanes:[],events:[]};const calculated=WB.state.result;
  if(WB.state.resultProjectionOwner!==calculated){WB.state.resultProjection=null;WB.state.resultProjectionOwner=calculated;}
  WB.state.resultProjection ||= services.projection.build({plan:calculated.plan,template:calculated.plan.resolved_template||calculated.plan.manual_template,result:calculated.result,
      executionModel:calculated.report?.mechanics_model,executionTemplate:calculated.report?.mechanics_input_template,current:true});
  const projection=WB.state.resultProjection;WB.state.expandedRows ||= new Set();
  const cacheKey=JSON.stringify([WB.state.mode,WB.state.effectFilter,WB.state.showCalculationSampling,WB.state.damageFocus,[...WB.state.expandedRows]]);
  let cached=rowsCache.get(projection);if(!cached){cached=new Map();rowsCache.set(projection,cached);}
  if(cached.has(cacheKey))return cached.get(cacheKey);
  let events=projection.views[WB.state.mode].events;
  if(WB.state.mode==="effects"){
    events=events.filter((e:any)=>e.kind!=="calculation_sampling"||WB.state.showCalculationSampling);
    if(WB.state.effectFilter==="coverage")events=events.filter((e:any)=>isCoverageKind(e.kind));
    if(WB.state.effectFilter==="points")events=events.filter((e:any)=>!isCoverageKind(e.kind));
  }
  if(WB.state.mode==="damage"){
    const calculations=new Map<any,any>(events.filter((e:any)=>e.kind==="calculation").map((e:any)=>[`${e.detail?.hit_id}:${e.start}`,e]));
    events=events.filter((e:any)=>WB.state.damageFocus==="all"||e.kind!=="calculation").map((e:any)=>{
      const calculation=e.kind==="damage"?calculations.get(`${e.detail?.hit_id}:${e.start}`):null;
      return calculation?{...e,detail:{...e.detail,calculation:calculation.detail}}:e;
    });
  }
  const baseRows=buildDisplayRows(events,WB.state.mode,WB.state.expandedRows);
  const lanes=WB.state.mode==="effects"?foldSourceDirectories(baseRows,WB.state.expandedRows):baseRows;
  const output={lanes,events:lanes.flatMap(row=>row.events.map(e=>({...e,lane:row.id})))};
  if(cached.size>12)cached.clear();cached.set(cacheKey,output);return output;
};
const recipients =(event:any)=>{
  const names=event.beneficiary_names||event.characters?.map((id:any)=>WB.actor(id)?.name||"角色资料未匹配");
  return names?.length?names.join("、"):WB.actor(event.character)?.name||"按计算来源的目标条件生效";
};

  return {project,recipients};
}
