import {type WhiteboardPorts} from "./ports";
export function createWhiteboardAdmission(ports:WhiteboardPorts){
  const WB=ports.owner,UI=ports.ui,$=ports.query,callApi=ports.api,setStatus=ports.status,services=ports.services;
const admissionIntents =(board:any)=>Array.isArray((board||WB.state.board)?.action_admission_intents)?(board||WB.state.board).action_admission_intents:[];
const admissionOwner =(actionId:any,except=-1)=>WB.admissionIntents().find((intent:any,index:any)=>index!==except&&intent.action_id===actionId);
const admissionContext =()=>({contract:"static-action-admission-context-v1",mode:"ordinary_open_world",external_cooldown_effects:"none",external_energy_cost_effects:"none"});
const closeAdmissionEditor =()=>{const session=WB.state.admissionSession;if(!session)return;WB.state.admissionSession=null;session.close();};
const removeAdmissionIntent =(index:any)=>{const intent=WB.admissionIntents()[index];if(!intent)return false;
  if(!ports.confirm(`删除动作接纳意图 ${intent.id}？`))return false;WB.closeAdmissionEditor();
  WB.change((board:any)=>board.action_admission_intents.splice(index,1));setStatus("动作接纳意图已删除；旧目录响应、确认与计算结果已失效。");return true;};
const admissionIntentMenuItems =(index:any)=>WB.admissionIntents()[index]?[{id:"edit-admission-intent",label:"编辑动作接纳意图",run:()=>WB.editAdmissionIntent(index)},
  {type:"separator"},{id:"remove-admission-intent",label:"删除动作接纳意图",danger:true,run:()=>WB.removeAdmissionIntent(index)}]:[];
const renderAdmissionIntents =(box:any)=>{
  const intents=WB.admissionIntents(),section=UI.el("section",null,"wb-capacity-intents"),pending=intents.filter((row:any)=>row.context===null||row.initial_ready_in===null).length;
  section.append(UI.el("h3",`动作接纳意图（${intents.length}）`),UI.el("p",pending?`尚有 ${pending} 项未明确场景或开场冷却；不会自动当作全部就绪`:intents.length?"全部意图已显式选择场景与开场状态":"尚未绑定受审动作门禁",pending?"wb-warning":"wb-note"));
  intents.forEach((intent:any,index:any)=>{const actor=WB.actor(intent.actor),action=WB.action(intent.action_id),card=UI.el("article",null,"wb-capacity-intent"),
      more=UI.button("⋯",()=>WB.openCommandMenu(more,`${intent.id}操作`,WB.admissionIntentMenuItems(index)),"wb-menu-trigger");card.tabIndex=0;
    card.append(UI.el("strong",`${actor?.name||intent.actor} · ${action?.label||intent.action_id}`),
      UI.el("p",`${intent.model_ref?.id||"模型待选"} / 修订 ${intent.model_ref?.revision??"待选"} · ${intent.context&&Array.isArray(intent.initial_ready_in)?"普通开放世界，开场显式就绪":"场景或开场状态待确认"}`),
      more);WB.bindCommandMenu(card,`${intent.id}操作`,()=>WB.admissionIntentMenuItems(index));section.append(card);});
  const eligible=WB.state.board.version_line==="release"&&WB.state.board.team.some((actor:any)=>actor.constellation===0&&WB.state.board.actions.some((action:any)=>action.character===actor.id&&action.action_type_id==="action.elemental_burst"&&action.variant==="default"&&!WB.admissionOwner(action.id)));
  const add=UI.button("＋ 新建动作接纳意图",()=>WB.editAdmissionIntent());add.disabled=WB.state.board.schema_version!==3||!eligible;section.append(add);
  if(WB.state.board.schema_version!==3)section.append(UI.el("p","当前白板尚未升级到版本 3；重新加载升级结果后才可保存动作接纳意图。","wb-warning"));
  else if(!eligible)section.append(UI.el("p","首片需要 release 资料线、0 命角色和已明确为“默认释放”的元素爆发动作。","wb-note"));box.append(section);
};
const admissionCatalogBrowser =(holder:any,options:any)=>{
  let active=true,serial=0;const groups:Array<[string,string,boolean]>=[
    ["exact","已审核 · 完全匹配",true],["partial","已审核 · 部分匹配",true],["pending","待审核或待处理",false],
    ["revoked","已撤销",false],["stale","资料已变化",false],["conflict","存在冲突",false],["not_evaluated","尚未评估",false]];
  const fail=(response:any)=>{holder.innerHTML="";const box=UI.el("section",null,"capacity-catalog-error");box.setAttribute("role","alert");
    box.append(UI.el("strong","动作接纳目录无法使用"),UI.el("p",response?.message||"请检查动作、角色和当前数据库。"));if(response?.details)box.append(services.display.raw("错误定位",response.details));holder.append(box);};
  const draw=(items:any,total:any)=>{holder.innerHTML="";holder.className="capacity-catalog";holder.append(UI.el("p",`目录共 ${total} 项，已完整读取 ${items.length} 项；目录不提供可直接执行的确认。`,"capacity-catalog-status"));
    for(const [key,label,open] of groups){const rows=items.filter((item:any)=>(item.catalog_group||"not_evaluated")===key),section=UI.el("details",null,`capacity-catalog-group capacity-catalog-${key}`);section.open=open;section.append(UI.el("summary",`${label}（${rows.length}）`));
      if(!rows.length)section.append(UI.el("p","当前分组没有项目。","wb-note"));for(const item of rows){const ref=item.model_ref||{},target=item.target||{},display=item.display||{},card=UI.el("article",null,"capacity-catalog-card"),selectable=["exact","partial"].includes(key)&&item.selection_status==="reference_selectable_for_fresh_preview";
        card.append(UI.el("strong",`${display.skill?.value||"技能名称待补"} · ${ref.id||"模型 ID 待补"} / 修订 ${ref.revision??"待补"}`),UI.el("p",`角色：${display.character?.value||target.character_id||"待补"}；技能组 ID ${target.skill_depot_id??"待补"}；技能 ID ${target.skill_id??"待补"}。`));
        if(selectable)card.append(UI.button("选择此模型引用",()=>options.onSelect(item)));const detail=UI.el("details",null,"capacity-catalog-advanced");detail.append(UI.el("summary","高级详情与原始 ID"),services.display.raw("完整只读目录记录",item));card.append(detail);section.append(card);}holder.append(section);}};
  const reload=async()=>{const attempt=++serial;holder.innerHTML="";holder.append(UI.el("p","正在完整读取动作接纳目录…"));let cursor:any=null,items:any[]=[],total=null;const seen=new Set();
    try{do{const response=await callApi("whiteboard_action_admission_model_candidates",options.document(),options.actor(),options.action(),cursor,16,"all");
        if(!active||attempt!==serial||!options.current())return false;if(response.code){fail(response);return false;}const page=response.data,reported=page.counts?.filtered_models;
        if(!Number.isInteger(reported)||reported<0||!Array.isArray(page.items)||typeof page.has_more!=="boolean"){fail({message:"目录响应缺少可信的分页字段。"});return false;}if(total===null)total=reported;else if(total!==reported){fail({message:"目录分页期间计数发生变化，请重新读取。"});return false;}
        items.push(...page.items);if(page.has_more){if(!page.items.length||typeof page.next_cursor!=="string"||!page.next_cursor||page.next_cursor===cursor||seen.has(page.next_cursor)){fail({message:"动作接纳目录分页游标无效。"});return false;}seen.add(page.next_cursor);cursor=page.next_cursor;}else{if(page.next_cursor!==null){fail({message:"动作接纳目录末页游标无效。"});return false;}cursor=null;}}
      while(cursor);if(items.length!==total){fail({message:`目录计数不一致：应有 ${total} 项，实际读取 ${items.length} 项。`});return false;}draw(items,total);return true;}
    catch(error:any){if(active&&attempt===serial&&options.current())fail({message:`目录读取失败：${error.message}`});return false;}};
  return {reload,invalidate(){serial++;holder.innerHTML="";},dispose(){active=false;serial++;holder.innerHTML="";}};
};
const editAdmissionIntent =(index:any)=>{
  const board=WB.state.board,intents=WB.admissionIntents(),original=index===undefined?null:intents[index];if(board?.schema_version!==3){setStatus("动作接纳意图需要版本 3 白板。");return null;}
  const eligibleActors=board.team.filter((actor:any)=>actor.constellation===0&&board.version_line==="release"&&board.actions.some((action:any)=>action.character===actor.id&&action.action_type_id==="action.elemental_burst"&&action.variant==="default"));
  if(!eligibleActors.length){setStatus("请先为 0 命角色添加并明确一个 default 元素爆发动作。");return null;}WB.closeAdmissionEditor();
  const initialActor=original?.actor||(eligibleActors.some((actor:any)=>actor.id===WB.state.selectedActor)?WB.state.selectedActor:eligibleActors[0].id),firstAction=board.actions.find((action:any)=>action.character===initialActor&&action.action_type_id==="action.elemental_burst"&&action.variant==="default"&&!WB.admissionOwner(action.id,index)),
    draft=original?UI.clone(original):{id:UI.id("admission-intent"),actor:initialActor,action_id:firstAction?.id||"",version_line:"release",selection:{character_id:eligibleActors.find((actor:any)=>actor.id===initialActor).db_character_id,skill_depot_id:null,skill_id:null,slot:"burst",constellation:0},variant:"default",model_ref:{id:"",revision:1},initial_ready_in:null,context:null},
    baseline=WB.json(),d=WB.dialog(original?"编辑动作接纳意图":"新建动作接纳意图","从动作轴选择一个明确的 default 元素爆发，再从完整只读目录选择受审模型。场景与开场状态不会默认填写。");if(!d)return null;
  const form=UI.el("div"),selected=UI.el("section"),scene=UI.el("section"),catalog=UI.el("section"),errors=UI.el("div"),closed=()=>d.wbClosed||WB.state.board!==board||WB.json()!==baseline;
  let browser:any=null;const validIdentity=()=>typeof draft.model_ref?.id==="string"&&draft.model_ref.id.length>0&&Number.isInteger(draft.model_ref.revision)&&Number.isInteger(draft.selection?.character_id)&&Number.isInteger(draft.selection?.skill_depot_id)&&Number.isInteger(draft.selection?.skill_id)&&draft.selection.slot==="burst"&&draft.selection.constellation===0;
  const clearIdentity=()=>{const actor=WB.actor(draft.actor);draft.model_ref={id:"",revision:1};draft.selection={character_id:actor?.db_character_id??null,skill_depot_id:null,skill_id:null,slot:"burst",constellation:0};};
  const renderSelected=()=>{selected.innerHTML="";selected.append(UI.el("h4","当前模型引用"),UI.el("p",validIdentity()?`${draft.model_ref.id} / 修订 ${draft.model_ref.revision}；技能组 ID ${draft.selection.skill_depot_id}；技能 ID ${draft.selection.skill_id}`:"尚未从目录选择模型。"));};
  const catalogDocument=()=>{const copy=UI.clone(board);copy.action_admission_intents=(copy.action_admission_intents||[]).filter((_row:any,i:any)=>i!==index);if(validIdentity())copy.action_admission_intents.push(UI.clone(draft));return copy;};
  const current=()=>!closed(),refresh=()=>{browser.invalidate();if(draft.action_id)browser.reload();};
  const renderForm=()=>{form.innerHTML="";form.append(UI.field("动作接纳角色",UI.select(eligibleActors.map((actor:any)=>({value:actor.id,label:`${actor.name} · 角色 ID ${actor.db_character_id}`})),draft.actor,(value:any)=>{draft.actor=value;draft.action_id="";clearIdentity();renderForm();renderSelected();refresh();})));
    const actions=board.actions.filter((action:any)=>action.character===draft.actor&&action.action_type_id==="action.elemental_burst"&&action.variant==="default"&&!WB.admissionOwner(action.id,index));if(original&&!actions.some((row:any)=>row.id===original.action_id)){const currentAction=WB.action(original.action_id);if(currentAction)actions.push(currentAction);}
    form.append(UI.field("绑定的元素爆发动作",UI.select([{value:"",label:"请选择动作"},...actions.map((action:any)=>({value:action.id,label:`${action.label} · ${action.start} 秒 · ${action.id}`}))],draft.action_id,(value:any)=>{draft.action_id=value;clearIdentity();renderSelected();refresh();})));};
  const renderScene=()=>{scene.innerHTML="";scene.append(UI.el("h4","场景与开场冷却"),UI.el("p",draft.context&&Array.isArray(draft.initial_ready_in)?"已显式选择普通开放世界，开场全部就绪。":"未确认；context 与 initial_ready_in 均保持 null，不会形成完整计算。",draft.context?"wb-note":"wb-warning"),
      UI.button("使用普通开放世界预设（开场全部就绪）",()=>{draft.context=WB.admissionContext();draft.initial_ready_in=[];renderScene();}),
      UI.button("清除为未知",()=>{draft.context=null;draft.initial_ready_in=null;renderScene();}));};
  browser=WB.admissionCatalogBrowser(catalog,{document:catalogDocument,actor:()=>draft.actor,action:()=>draft.action_id,current,onSelect:(item:any)=>{const target=item.target||{},actor=WB.actor(draft.actor);draft.model_ref=UI.clone(item.model_ref);draft.selection={character_id:target.character_id,skill_depot_id:target.skill_depot_id,skill_id:target.skill_id,slot:"burst",constellation:0};
      if(draft.selection.character_id!==actor?.db_character_id){clearIdentity();setStatus("目录模型角色与白板角色不一致，未采用。");}renderSelected();}});
  const close=()=>{browser.dispose();if(WB.state.admissionSession?.dialog===d)WB.state.admissionSession=null;WB.closeDialog(d);};WB.state.admissionSession={dialog:d,close};d.wbCleanup=()=>browser.dispose();
  const save=()=>{errors.innerHTML="";const actor=WB.actor(draft.actor),action=WB.action(draft.action_id);let message="";
    if(closed())message="白板已变化，请关闭后重新编辑。";else if(!actor||actor.constellation!==0||board.version_line!=="release")message="首片只支持 release 资料线的 0 命角色。";
    else if(!action||action.character!==draft.actor||action.action_type_id!=="action.elemental_burst"||action.variant!=="default")message="请选择属于该角色且已明确 default 变体的元素爆发动作。";
    else if(WB.admissionOwner(draft.action_id,index))message="一个动作不能由多个动作接纳意图重复拥有。";else if(!validIdentity()||draft.selection.character_id!==actor.db_character_id)message="请从目录选择与当前角色匹配的精确模型引用。";
    else if((draft.context===null)!==(draft.initial_ready_in===null))message="场景与开场冷却必须共同由显式预设写入或共同保持未知。";if(message){errors.append(UI.el("p",message,"eq-error"));return false;}
    WB.closeDialog(d);WB.state.admissionSession=null;WB.change((currentBoard:any)=>{if(index===undefined)currentBoard.action_admission_intents.push(UI.clone(draft));else currentBoard.action_admission_intents[index]=UI.clone(draft);});setStatus("动作接纳意图已保存；执行确认仍需 fresh preview，白板本身不会签发。" );return true;};
  renderForm();renderSelected();renderScene();d.appBody.append(form,selected,scene,UI.el("h4","动作接纳模型目录"),UI.button("重新读取全部目录",()=>browser.reload()),catalog,errors,UI.button("保存动作接纳意图",save));if(original)d.appBody.append(UI.button("删除动作接纳意图",()=>WB.removeAdmissionIntent(index),"wb-danger"));d.appBody.append(UI.button("返回且不保存",close));d.addEventListener("cancel",close);if(draft.action_id)browser.reload();return d;
};

  return {admissionIntents,admissionOwner,admissionContext,closeAdmissionEditor,removeAdmissionIntent,admissionIntentMenuItems,renderAdmissionIntents,admissionCatalogBrowser,editAdmissionIntent};
}
