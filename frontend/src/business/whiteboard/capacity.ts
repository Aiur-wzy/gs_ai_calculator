import {type WhiteboardPorts} from "./ports";
export function createWhiteboardCapacity(ports:WhiteboardPorts){
  const WB=ports.owner,UI=ports.ui,$=ports.query,setStatus=ports.status,services=ports.services;
const capacityIntents =(board:any)=>Array.isArray((board||WB.state.board)?.capacity_intents)?(board||WB.state.board).capacity_intents:[];
const capacityOwner =(actionId:any,except=-1)=>WB.capacityIntents().find((intent:any,index:any)=>index!==except&&intent.action_ids.includes(actionId));
const capacityContextKey =(value:any)=>services.capacity.contextKey(value);
const capacityContextConflict =(board:any)=>{
  const contexts=WB.capacityIntents(board).map((intent:any)=>intent.context).filter((value:any)=>value!==null);
  return new Set(contexts.map(WB.capacityContextKey)).size>1;
};
const closeCapacityEditor =()=>{const session=WB.state.capacitySession;if(!session)return;WB.state.capacitySession=null;session.close();};
const removeCapacityIntent =(index:any)=>{
  const intent=WB.capacityIntents()[index];if(!intent)return false;
  if(!ports.confirm(`删除次数意图 ${intent.id}？`))return false;
  WB.closeCapacityEditor();WB.change((board:any)=>{board.capacity_intents.splice(index,1);});setStatus("次数意图已删除；旧目录响应与旧计算结果已失效。");return true;
};
const capacityIntentMenuItems =(index:any)=>WB.capacityIntents()[index]?[{id:"edit-capacity-intent",label:"编辑次数意图",run:()=>WB.editCapacityIntent(index)},
  {type:"separator"},{id:"remove-capacity-intent",label:"删除次数意图",danger:true,run:()=>WB.removeCapacityIntent(index)}]:[];
const renderCapacityIntents =(box:any)=>{
  const intents=WB.capacityIntents(),unknown=intents.filter((intent:any)=>intent.context===null).length,
    section=WB.fold(`次数意图（${intents.length}）${unknown?` · ${unknown} 项待确认`:""}`,"capacityIntentsOpen");section.className+=" wb-capacity-intents";
  section.append(UI.el("h3",`技能次数意图（${intents.length}）`),
    UI.el("p",unknown?`次数场景未确认 · 尚有 ${unknown} 个次数意图未确认场景`:intents.length?"全部次数意图已明确场景":"次数场景未确认 · 当前没有次数意图",unknown?"wb-warning":"wb-note"));
  if(WB.capacityContextConflict())section.append(UI.el("p","多个次数意图的场景不一致，保存、目录与后续计算都会被阻断；请逐项统一或清除。","wb-danger"));
  intents.forEach((intent:any,index:any)=>{const actor=WB.actor(intent.actor),actions=intent.action_ids.map((id:any)=>WB.action(id)?.label||id),card=UI.el("article",null,"wb-capacity-intent"),
      more=UI.button("⋯",()=>WB.openCommandMenu(more,`${intent.id}操作`,WB.capacityIntentMenuItems(index)),"wb-menu-trigger");card.tabIndex=0;
    card.append(UI.el("strong",`${actor?.name||intent.actor} · ${intent.model_ref?.id||"模型待选"}`),
      UI.el("p",`${actions.join("、")||"动作待选"} · ${intent.context?"普通开放世界":"次数场景未确认"}`),more);
    WB.bindCommandMenu(card,`${intent.id}操作`,()=>WB.capacityIntentMenuItems(index));section.append(card);});
  const capacitySchema=[2,3].includes(WB.state.board.schema_version),add=UI.button("＋ 新建次数意图",()=>WB.editCapacityIntent());add.disabled=!capacitySchema||!WB.state.board.actions.some((action:any)=>action.action_type_id==="action.elemental_skill");section.append(add);
  if(!capacitySchema)section.append(UI.el("p","当前白板版本不支持次数意图；请重新加载后端升级结果。","wb-warning"));
  else if(!WB.state.board.actions.some((action:any)=>action.action_type_id==="action.elemental_skill"))section.append(UI.el("p","请先在动作轴添加至少一个元素战技动作。","wb-note"));
  box.append(section);
};
const editCapacityIntent =(index:any)=>{
  const board=WB.state.board,intents=WB.capacityIntents(),original=index===undefined?null:intents[index];
  if(![2,3].includes(board?.schema_version)){setStatus("当前白板版本不支持次数意图，请重新加载升级后的白板。");return null;}
  const eligibleActors=board.team.filter((actor:any)=>board.actions.some((action:any)=>action.character===actor.id&&action.action_type_id==="action.elemental_skill"));
  if(!eligibleActors.length){setStatus("请先为角色添加元素战技动作。");return null;}
  WB.closeCapacityEditor();const initialActor=original?.actor||(eligibleActors.some((actor:any)=>actor.id===WB.state.selectedActor)?WB.state.selectedActor:eligibleActors[0].id),
    draft=original?UI.clone(original):{id:UI.id("capacity-intent"),actor:initialActor,action_ids:[],version_line:board.version_line,
      model_ref:{id:"",revision:1},target:{character_id:null,skill_depot_id:null,skill_id:null,variant:"tap"},cooldown:null,initial_ready_in:null,context:null},
    baseline=WB.json(),d=WB.dialog(original?"编辑技能次数意图":"新建技能次数意图","从同一角色的元素战技动作中明确选择共用次数池，再从只读目录选择精确模型与目标。目录不写入容量值。");if(!d)return null;
  const actorBox=UI.el("div"),actionsBox=UI.el("fieldset",null,"wb-capacity-actions"),selected=UI.el("section",null,"capacity-selected"),
    scene=UI.el("section",null,"capacity-scene"),catalog=UI.el("section",null,"capacity-catalog-host"),errors=UI.el("div"),closed=()=>d.wbClosed||WB.state.board!==board||WB.json()!==baseline;
  let browser:any=null;
  const validIdentity=()=>typeof draft.model_ref?.id==="string"&&draft.model_ref.id.length>0&&Number.isInteger(draft.model_ref.revision)&&draft.model_ref.revision>=1&&
    Number.isInteger(draft.target?.character_id)&&Number.isInteger(draft.target?.skill_depot_id)&&Number.isInteger(draft.target?.skill_id)&&typeof draft.target?.variant==="string";
  const catalogDocument=()=>{const copy=UI.clone(board);copy.capacity_intents=(copy.capacity_intents||[]).filter((_intent:any,i:any)=>i!==index);
    if(validIdentity())copy.capacity_intents.push(UI.clone(draft));return copy;};
  const renderSelected=()=>{selected.innerHTML="";selected.append(UI.el("h4","当前模型引用"),UI.el("p",validIdentity()?`${draft.model_ref.id} / 修订 ${draft.model_ref.revision}；角色 ID ${draft.target.character_id}；技能组 ID ${draft.target.skill_depot_id}；技能 ID ${draft.target.skill_id}`:"尚未从目录选择模型。"));};
  const canQuery=()=>draft.action_ids.length>0&&!draft.action_ids.some((id:any)=>WB.capacityOwner(id,index));
  const refreshCatalog=()=>{browser.invalidate();if(canQuery())browser.reload();else catalog.append(UI.el("p","请先选择一个或多个未被其他次数意图占用的元素战技动作。","wb-note"));};
  const clearIdentity=()=>{draft.model_ref={id:"",revision:1};draft.target={character_id:null,skill_depot_id:null,skill_id:null,variant:"tap"};delete draft.accepted_fingerprint;renderSelected();};
  const renderActions=()=>{actionsBox.innerHTML="";actionsBox.append(UI.el("legend","共用此次数池的元素战技动作"));
    const actions=board.actions.filter((action:any)=>action.character===draft.actor&&action.action_type_id==="action.elemental_skill");
    for(const action of actions){const owner=WB.capacityOwner(action.id,index),line=UI.check(`${action.label||WB.typeName(action.action_type_id)} · 动作 ID ${action.id}${owner?` · 已属于 ${owner.id}`:""}`,draft.action_ids.includes(action.id),(value:any)=>{
        if(owner){setStatus("该动作已属于另一个次数意图，不能重复拥有。");return;}draft.action_ids=draft.action_ids.filter((id:any)=>id!==action.id);if(value)draft.action_ids.push(action.id);clearIdentity();errors.innerHTML="";refreshCatalog();});
      const control=line.children[1];if(owner){control.disabled=true;control.checked=false;}actionsBox.append(line);}
  };
  const renderActor=()=>{actorBox.innerHTML="";actorBox.append(UI.field("次数意图角色",UI.select(eligibleActors.map((actor:any)=>({value:actor.id,label:`${actor.name} · 角色 ID ${actor.db_character_id}`})),draft.actor,(value:any)=>{
      draft.actor=value;draft.action_ids=[];draft.version_line=board.version_line;clearIdentity();renderActions();errors.innerHTML="";refreshCatalog();})));};
  const setContext=(next:any)=>{if(services.capacity.sameContext(draft.context,next)){renderScene();return;}draft.context=next===null?null:UI.clone(next);clearIdentity();errors.innerHTML="";renderScene();refreshCatalog();};
  const renderScene=()=>{scene.innerHTML="";scene.append(UI.el("h4","次数场景"),UI.el("p",draft.context?"当前意图使用普通开放世界预设。":"次数场景未确认","capacity-scene-status"),
      UI.button("一键使用普通开放世界预设",()=>setContext(services.capacity.ordinaryContext())),
      UI.button("清除场景",()=>setContext(null)));};
  browser=services.capacity.catalogBrowser(catalog,{document:catalogDocument,actor:()=>draft.actor,memberIds:()=>draft.action_ids,method:"whiteboard_action_capacity_model_candidates",current:()=>!closed(),
    onSelect:(item:any)=>{services.capacity.applyCatalogSelection(draft,item);renderSelected();errors.innerHTML="";setStatus("已选择精确模型引用和目标；容量值仍由后端计算。");}});
  const close=()=>{browser.dispose();if(WB.state.capacitySession?.dialog===d)WB.state.capacitySession=null;WB.closeDialog(d);};
  WB.state.capacitySession={dialog:d,close};d.wbCleanup=()=>browser.dispose();
  const save=()=>{errors.innerHTML="";const actor=WB.actor(draft.actor),actions=draft.action_ids.map((id:any)=>WB.action(id));let message="";
    if(closed())message="白板已变化，请关闭后重新编辑次数意图。";
    else if(!actor)message="次数意图角色不存在。";
    else if(!draft.action_ids.length||new Set(draft.action_ids).size!==draft.action_ids.length)message="请至少选择一个不重复的元素战技动作。";
    else if(actions.some((action:any)=>!action||action.character!==draft.actor||action.action_type_id!=="action.elemental_skill"))message="所选动作必须存在、属于同一角色且均为元素战技。";
    else if(draft.action_ids.some((id:any)=>WB.capacityOwner(id,index)))message="一个动作不能由多个次数意图重复拥有。";
    else if(!validIdentity()||draft.target.character_id!==actor.db_character_id)message="请先从目录选择与当前角色匹配的精确模型引用和目标。";
    else {const variant=draft.target.variant;if(actions.some((action:any)=>action.variant&&action.variant!==variant))message="目录目标变体与所选动作变体不一致。";}
    const proposed=UI.clone(board);if(!message){proposed.capacity_intents=(proposed.capacity_intents||[]).filter((_intent:any,i:any)=>i!==index);proposed.capacity_intents.push(UI.clone(draft));if(WB.capacityContextConflict(proposed))message="多个非空次数场景必须完全一致；请统一或清除场景。";}
    if(message){errors.append(UI.el("p",message,"eq-error"));return false;}
    WB.closeDialog(d);WB.state.capacitySession=null;WB.change((current:any)=>{if(index===undefined)current.capacity_intents.push(UI.clone(draft));else current.capacity_intents[index]=UI.clone(draft);});setStatus("次数意图已保存到白板；重新计算后才会生成容量值。");return true;};
  renderActor();renderActions();renderSelected();renderScene();d.appBody.append(actorBox,actionsBox,selected,scene,UI.el("h4","容量模型目录"),UI.button("重新读取全部目录",refreshCatalog),catalog,errors,
    UI.button("保存次数意图",save));if(original)d.appBody.append(UI.button("删除次数意图",()=>WB.removeCapacityIntent(index),"wb-danger"));d.appBody.append(UI.button("返回且不保存",close));d.addEventListener("cancel",close);
  if(canQuery())browser.reload();else catalog.append(UI.el("p","请先选择一个或多个未被其他次数意图占用的元素战技动作。","wb-note"));return d;
};

  return {capacityIntents,capacityOwner,capacityContextKey,capacityContextConflict,closeCapacityEditor,removeCapacityIntent,capacityIntentMenuItems,renderCapacityIntents,editCapacityIntent};
}
