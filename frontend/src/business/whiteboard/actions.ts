import {type WhiteboardPorts} from "./ports";
export function createWhiteboardActions(ports:WhiteboardPorts){
  const WB=ports.owner,UI=ports.ui,setStatus=ports.status;
const sort =()=>WB.state.board.actions.sort((a:any,b:any)=>a.start-b.start);
const clampTime =(value:any)=>Math.round(Math.max(0,Math.min(WB.state.board.duration-0.001,value))*1000)/1000;
const setTime =(id:any,value:any)=>{
  const a=WB.action(id),rounded=Math.round(value*1000)/1000;
  if(!a||!Number.isFinite(value)||value<0||rounded>=WB.state.board.duration){setStatus("动作时间需在轴内（含零，不含轴末）。");return false;}
  WB.change(()=>{a.start=rounded;WB.sort();});return true;
};
const moveOrder =(id:any,direction:any)=>{
  const actions=WB.state.board.actions,i=actions.findIndex((a:any)=>a.id===id),j=i+direction;
  if(i<0||j<0||j>=actions.length)return;
  WB.change(()=>{[actions[i].start,actions[j].start]=[actions[j].start,actions[i].start];[actions[i],actions[j]]=[actions[j],actions[i]];});
};
const addAction =(type:any)=>{
  const c=WB.actor(WB.state.selectedActor);if(!c){setStatus("请先在左侧添加并选择角色。");return;}
  const action={id:UI.id("action"),character:c.id,action_type_id:type,label:WB.typeName(type),
    start:WB.clampTime(WB.state.cursor),variant:"",notes:""};
  WB.change((board:any)=>{board.actions.push(action);WB.sort();WB.state.selectedAction=action.id;WB.state.mode="actions";WB.state.addOpen=false;});
};
const removeAction =(id:any)=>{if(!WB.action(id))return;
  if(WB.capacityOwner?.(id)){setStatus("该动作已属于次数意图，请先删除或修改对应次数意图。");return;}
  if(WB.admissionOwner?.(id)){setStatus("该动作已属于动作接纳意图，请先删除或修改对应意图。");return;}
  WB.change((board:any)=>{board.actions=board.actions.filter((a:any)=>a.id!==id);WB.state.selectedAction=null;});};
const duplicateAction =(id:any)=>{const original=WB.action(id);if(!original)return;
  WB.change((board:any)=>{const next={...UI.clone(original),id:UI.id("action"),start:WB.clampTime(original.start+0.1)};
    board.actions.push(next);WB.sort();WB.state.selectedAction=next.id;});};
const actionMenuItems =(id:any)=>{
  const action=WB.action(id),actions=WB.state.board?.actions||[],index=actions.findIndex((row:any)=>row.id===id),
    owner=action&&(WB.capacityOwner?.(id)||WB.admissionOwner?.(id));
  if(!action)return [];
  return [
    {id:"edit-action",label:"编辑动作",run:()=>WB.selectEvent({id:action.id,character:action.character,start:action.start})},
    {id:"move-action-earlier",label:"提前一位",enabled:index>0,disabledReason:index<=0?"已经是第一个动作":"",run:()=>WB.moveOrder(id,-1)},
    {id:"move-action-later",label:"延后一位",enabled:index>=0&&index<actions.length-1,disabledReason:index===actions.length-1?"已经是最后一个动作":"",run:()=>WB.moveOrder(id,1)},
    {id:"duplicate-action",label:"复制动作",run:()=>WB.duplicateAction(id)},
    {type:"separator"},
    {id:"remove-action",label:"删除动作",danger:true,enabled:!owner,disabledReason:owner?"请先删除或修改引用此动作的意图":"",run:()=>WB.removeAction(id)},
  ];
};
const setDuration =(value:any)=>{
  if(!Number.isFinite(value)||value<=0||value>3600){setStatus("轴长需大于零且不超过 3600 秒。");return false;}
  if(WB.state.board.damage_report?.mode==="explicit"&&WB.state.board.damage_report.end>value){setStatus("新轴长短于你指定的DPS截止；请先调整截止时间，不会自动改写。 ");return false;}
  if(WB.state.board.actions.some((a:any)=>a.start>=value)){setStatus("新轴长会截掉已有动作，请先调整末尾动作；不会自动删除或缩放动作。");return false;}
  WB.change((board:any)=>{board.duration=value;WB.state.cursor=WB.clampTime(WB.state.cursor);});return true;
};

  return {sort,clampTime,setTime,moveOrder,addAction,removeAction,duplicateAction,actionMenuItems,setDuration};
}
