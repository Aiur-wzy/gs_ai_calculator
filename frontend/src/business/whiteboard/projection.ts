import {type WhiteboardPorts} from "./ports";
export function createWhiteboardProjection(ports:WhiteboardPorts){
  const WB=ports.owner,$=ports.query,services=ports.services;
const project =()=>{
  if(WB.state.mode==="actions")return {lanes:WB.state.board.team.map((c:any)=>({id:c.id,label:c.name})),events:WB.state.board.actions.map((a:any)=>({id:a.id,character:a.character,start:a.start,label:WB.actionName(a),action:a}))};
  if(!WB.state.result)return {lanes:[],events:[]};const calculated=WB.state.result;
  WB.state.resultProjection ||= services.projection.build({plan:calculated.plan,template:calculated.plan.resolved_template||calculated.plan.manual_template,result:calculated.result,
      executionModel:calculated.report?.mechanics_model,executionTemplate:calculated.report?.mechanics_input_template,current:true});
  let events=WB.state.resultProjection.views[WB.state.mode].events;const lanes:any[]=[];
  if(WB.state.mode==="effects"){
    events=services.projection.groupCoverage(events,WB.state.board.team);
    if(WB.state.effectFilter==="coverage")events=events.filter((e:any)=>e.kind==="coverage");
    if(WB.state.effectFilter==="points")events=events.filter((e:any)=>e.kind!=="coverage");
    // Interval lanes precede point lanes. Distinct overlapping states need their
    // own row; this layout never joins time windows or changes their evidence.
    const ends=new Map<any,any>(),mapped:any[]=[];
    for(const event of [...events.filter((e:any)=>e.kind==="coverage"),...events.filter((e:any)=>e.kind!=="coverage")]){
      const coverage=event.kind==="coverage",source=event.sourceKey||event.character||"team",key=`${coverage?"coverage":"points"}:${source}`;
      let slot=0;if(coverage){const rows=ends.get(key)||[];slot=rows.findIndex((end:any)=>end<=event.start);if(slot<0)slot=rows.length;rows[slot]=event.end;ends.set(key,rows);}
      const id=`${key}:${slot}`,label=event.sourceLabel||WB.actor(event.character)?.name||"全队";
      if(!lanes.some(l=>l.id===id))lanes.push({id,label,kind:coverage?"coverage":"points"});
      mapped.push({...event,lane:id});
    }
    return {lanes,events:mapped};
  }
  if(WB.state.mode==="damage"){
    const calculations=new Map<any,any>(events.filter((e:any)=>e.kind==="calculation").map((e:any)=>[`${e.detail?.hit_id}:${e.start}`,e]));
    events=events.filter((e:any)=>WB.state.damageFocus==="all"||e.kind!=="calculation").map((e:any)=>{
      const calculation=e.kind==="damage"?calculations.get(`${e.detail?.hit_id}:${e.start}`):null;
      return calculation?{...e,detail:{...e.detail,calculation:calculation.detail}}:e;
    });
  }
  for(const event of events){const id=event.sourceKey||event.character||"team";if(!lanes.some(l=>l.id===id))lanes.push({id,label:event.sourceLabel||WB.actor(event.character)?.name||"全队"});}
  return {lanes,events:events.map((e:any)=>({...e,lane:e.sourceKey||e.character||"team"}))};
};
const recipients =(event:any)=>{
  const names=event.beneficiary_names||event.characters?.map((id:any)=>WB.actor(id)?.name||"角色资料未匹配");
  return names?.length?names.join("、"):WB.actor(event.character)?.name||"按计算来源的目标条件生效";
};

  return {project,recipients};
}
