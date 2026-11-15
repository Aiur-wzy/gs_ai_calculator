import {type WhiteboardPorts} from "./ports";
export function createWhiteboardEquipment(ports:WhiteboardPorts){
  const WB=ports.owner,UI=ports.ui,$=ports.query,callApi=ports.api,setStatus=ports.status,services=ports.services;
const pick =({title,kind,weaponType="",selected,choose}:any)=>{
  const line=WB.state.board.version_line,board=WB.state.board,d=WB.dialog(title);if(!d)return null;
  const search=UI.input("",()=>{}),list=UI.el("div",null,"wb-catalog"),nav=UI.el("div",null,"wb-toolbar");
  let page=1,request=0;const run=async()=>{
    const req=++request;list.innerHTML="";list.append(UI.el("p","正在查询资料…"));
    const name=search.value.trim(),method=({character:"list_characters",weapon:"list_weapons",set:"list_artifact_sets"} as Record<string,string>)[kind],
      args=kind==="character"?[name,"",0,line,page,50]:kind==="weapon"?[name,weaponType,0,"codex",line,page,50]:[name,0,"codex",line,page,50];
    const r=await callApi(method,...args);if(d.wbClosed||req!==request||board!==WB.state.board)return;list.innerHTML="";nav.innerHTML="";
    if(r.code){list.append(UI.error(r));return;}list.append(UI.el("p",`共 ${r.data.total} 项 · 第 ${page} 页`,"wb-note"));
    const elements:Record<string,string>={pyro:"火",hydro:"水",electro:"雷",cryo:"冰",anemo:"风",geo:"岩",dendro:"草",Fire:"火",Water:"水",Electric:"雷",Ice:"冰",Wind:"风",Rock:"岩",Grass:"草"};
    for(const item of r.data.items||[]){const element=item.element&&(elements[item.element]||(/^[火水雷冰风岩草]$/u.test(item.element)?item.element:"元素待补")),b=UI.button(`${item.name||"中文名待补"}${element?` · ${element}`:""}${item.rarity?` · ${item.rarity}星`:""}${selected===item.id?" · 已选择":""}`,async()=>{
      if(d.wbClosed||d.wbSelecting||board!==WB.state.board)return;await choose(item,d);});list.append(b);}
    const prev=UI.button("上一页",()=>{page--;run();}),next=UI.button("下一页",()=>{page++;run();});prev.disabled=page===1;next.disabled=page*50>=r.data.total;nav.append(prev,next);
  };
  const submit=()=>{page=1;run();};search.addEventListener("keydown",(e:any)=>{if(e.key==="Enter")submit();});
  d.appBody.append(UI.field("按中文名搜索",search),UI.button("搜索",submit),list,nav,UI.button("返回",()=>WB.closeDialog(d)));
  d.addEventListener("cancel",()=>WB.closeDialog(d));run();return d;
};
const addCharacter =()=>{
  if(WB.state.board.team.length>=4){setStatus("当前白板最多配置四名角色。");return;}
  WB.pick({title:"选择队伍角色",kind:"character",choose:async(item:any,d:any)=>{
    if(WB.state.board.team.some((c:any)=>c.db_character_id===item.id)){setStatus("该角色已在队伍中。");return;}
    if(WB.state.board.team.length>=4){setStatus("队伍已满。");return;}
    const board=WB.state.board,line=board.version_line,c={id:UI.id("actor"),db_character_id:item.id,character_ref:{kind:"database",entity_type:"character",database_id:item.id,version_line:line},name:item.name,level:90,constellation:0,weapon:null,artifact_sets:[],panel_basis_mode:"database",
      panel:Object.fromEntries([...WB.panelFields.map(([key]:any)=>[key,null]),...["base_atk","base_hp","base_def"].map(key=>[key,null]),["dmg_bonus",{}]]),notes:""};
    d.wbSelecting=true;setStatus("正在读取角色 90 级基础属性…");let message="已加入角色并填入可确认的基础白值；请选择武器并填写成品面板。";
    try{
      const r=await callApi("character_panel_basis",c.db_character_id,c.level,line,null);
      if(!r.code)for(const [key] of services.equipment.baseFields){const value=r.data.panel_base?.[key];c.panel[key]=typeof value==="number"&&Number.isFinite(value)?value:null;}
      else message=`已加入角色，基础属性待补：${r.message||"资料读取失败"}。可在装备与面板中重新读取。`;
    }catch{message="已加入角色，基础资料读取失败；可在装备与面板中重新读取。";}
    finally{d.wbSelecting=false;}
    if(d.wbClosed||board!==WB.state.board||line!==board.version_line)return;
    if(board.team.length>=4||board.team.some((actor:any)=>actor.db_character_id===item.id))return;
    WB.closeDialog(d);WB.change((board:any)=>{board.team.push(c);WB.state.selectedActor=c.id;});
    setStatus(message);
  }});
};
const editCharacter =(id:any)=>{
  const original=WB.actor(id);if(!original)return;const board=WB.state.board,base=JSON.stringify(original),saved=WB.state.equipmentDrafts?.[id],
    retained=saved?.base===base?saved:null,d=WB.dialog(`${original.name} · 装备与初始面板`);if(!d)return null;
  const body=UI.el("div"),error=UI.el("div");
  d.classList.add("wb-equipment-dialog");let latestMeta={equipmentChanged:retained?.equipmentChanged||false,panelConfirmed:retained?.panelConfirmed||false};
  const editor=services.equipment.mount(body,{actorDraft:retained?.draft||original,versionLine:board.version_line,weaponShape:"whiteboard",
    requirePanelConfirmation:retained?.equipmentChanged,panelConfirmed:retained?.panelConfirmed,
    isCurrent:()=>!d.wbClosed&&board===WB.state.board&&JSON.stringify(WB.actor(id))===base,
    onChange:(_:any,meta:any)=>{latestMeta=meta;error.textContent="";}});
  WB.state.editorSession={dialog:d,base,json:()=>JSON.stringify(editor.draft())};
  d.wbCleanup=()=>{if(!d.wbDiscard&&board===WB.state.board&&JSON.stringify(editor.draft())!==base){
      (WB.state.equipmentDrafts||={})[id]={base,draft:editor.draft(),equipmentChanged:latestMeta.equipmentChanged,panelConfirmed:latestMeta.panelConfirmed};
    }else if(WB.state.equipmentDrafts)delete WB.state.equipmentDrafts[id];editor.destroy();};
  const returnBack=()=>{WB.closeDialog(d);WB.render();},heading=UI.el("div",null,"ew-dialog-heading");
  heading.append(UI.button("← 返回白板",returnBack),UI.el("p","返回保留本次草稿；应用前不改变当前角色与计算结果。","ew-note"));
  const apply=async (calculate:any)=>{
    if(board!==WB.state.board||JSON.stringify(WB.actor(id))!==base){error.textContent="角色资料已变化，请返回后重新打开。";return;}
    const issues=editor.validate();if(issues.length){error.textContent=issues.join(" ");return;}
    const copy=editor.draft(),settingsJson=JSON.stringify(copy),candidate=UI.clone(board),token=WB.token();candidate.team[candidate.team.findIndex((c:any)=>c.id===id)]=copy;
    error.textContent="正在检查角色设置…";const r=await callApi("whiteboard_validate",candidate);if(d.wbClosed||!WB.current(token))return;
    if(settingsJson!==JSON.stringify(editor.draft())){error.textContent="角色设置在检查期间变化，请再次保存以检查最新内容。";return;}
    if(r.code){error.innerHTML="";error.append(UI.error(r));return;}
    d.wbDiscard=true;WB.closeDialog(d);WB.change((current:any)=>{current.team[current.team.findIndex((c:any)=>c.id===id)]=copy;});
    if(calculate)await WB.recalculateEquipment();else setStatus("角色设置已应用到白板。可继续编辑，或使用“重新计算装备与面板”检查结果。");
  };
  const actions=UI.el("div",null,"ew-actions");actions.append(UI.button("应用并计算",()=>apply(true),"primary"),UI.button("保存角色设置",()=>apply(false)),
    UI.button("放弃本次修改",()=>{d.wbDiscard=true;WB.closeDialog(d);WB.render();},"danger"));
  d.appBody.append(heading,body,error,actions);d.addEventListener("cancel",(event:any)=>{event.preventDefault();returnBack();});
};
const recalculateEquipment =async()=>{
  if(WB.unappliedEditor()){setStatus("请先保存角色设置，再重新计算。");return;}
  const token=WB.token(),baseline=WB.state.reusableResult;
  WB.state.equipmentReport={state:"working",missing_inputs:[]};WB.render();
  const r=baseline?.deliveryCapability&&baseline?.sessionId
    ?baseline.owner==="native_dsh"
      ?await callApi("dsh_client_whiteboard_apply_equipment",{conversation_id:baseline.sessionId,delivery_capability:baseline.deliveryCapability,board:UI.clone(WB.state.board)})
      :await callApi("agent_whiteboard_apply_equipment",baseline.sessionId,baseline.deliveryCapability,UI.clone(WB.state.board))
    :await callApi("whiteboard_apply_equipment",UI.clone(WB.state.board),baseline?.board||null,baseline?.path||null);
  if(!WB.current(token))return;
  if(r.code){WB.state.equipmentReport={state:"pending",missing_inputs:[{message:r.message||"本次重算失败，请交给 Agent 核对。"}]};WB.render();UI.error(r);return;}
  WB.state.equipmentReport=r.data;WB.state.pending=r.data.missing_inputs||[];
  if(r.data.state==="ready"&&r.data.calculated){WB.setAgentResult(r.data.calculated);WB.state.resultProjection=null;WB.state.resultPath=null;WB.state.staleResultPath=null;
    WB.state.selectedDerived=null;setStatus("已按当前装备与面板重新计算。计算结果尚未保存为交付文件。");}
  else{WB.setAgentResult(null);WB.state.resultProjection=null;setStatus("当前设置已应用；部分计算需要 Agent 二次处理，请查看白板中的具体待完善项目。");}
  WB.render();
};
const saveEquipmentCandidate =()=>{
  const report=WB.state.equipmentReport;if(!report?.plan)return;const board=WB.state.board,json=WB.json(),
    d=WB.dialog("保存待完善方案给 Agent","保留当前已复用的规则、装备与面板，以及需要补正的计算项。把保存的方案交给 Agent，要求保持动作、顺序和时点，并完成检查、补充和重新计算。");if(!d)return null;
  const path=UI.input(`${ports.plansDir()||"plans"}/白板换装待完善-${Date.now()}.plan.json`,()=>{}),out=UI.el("div");
  d.appBody.append(UI.field("新方案文件路径",path),UI.button("保存待完善方案",async()=>{
    if(board!==WB.state.board||json!==WB.json()||report!==WB.state.equipmentReport){out.textContent="白板已经变化，请返回后重新计算并生成最新候选。";return;}
    const destination=path.value.trim();if(!destination){out.textContent="请输入新方案文件路径。";return;}
    const r=await callApi("save_plan",destination,UI.clone(report.plan),null);if(d.wbClosed)return;out.innerHTML="";
    if(r.code){out.append(UI.error(r));return;}
    out.append(UI.el("p","已保存待完善方案。将此文件路径与下方问题交给 Agent；这里不会自动发送。"));
    const saved=UI.area(r.data.path||destination,()=>{});saved.readOnly=true;out.append(UI.field("交给 Agent 的方案路径",saved));
    for(const issue of report.missing_inputs||[])out.append(UI.el("p",issue.message||"需要核对的计算规则"));
  }),out,UI.button("返回白板",()=>WB.closeDialog(d)));d.addEventListener("cancel",()=>WB.closeDialog(d));
};
const removeCharacter =(id:any)=>{
  const actor=WB.actor(id);if(!actor)return;
  if((WB.state.board.capacity_intents||[]).some((intent:any)=>intent.actor===id)){setStatus("该角色仍有次数意图，请先删除对应次数意图。");return;}
  if((WB.state.board.action_admission_intents||[]).some((intent:any)=>intent.actor===id)){setStatus("该角色仍有动作接纳意图，请先删除对应意图。");return;}
  if(WB.state.board.actions.some((a:any)=>a.character===id)){setStatus("该角色仍有动作，请先移除或转交这些动作，再移除角色。");return;}
  if(!ports.confirm(`从白板移除${actor.name}？`))return;
  WB.change((board:any)=>{board.team=board.team.filter((c:any)=>c.id!==id);WB.state.selectedActor=board.team[0]?.id||null;});
};
const characterMenuItems =(id:any)=>{
  const actor=WB.actor(id),board=WB.state.board;if(!actor||!board)return [];
  const blocked=(board.capacity_intents||[]).some((intent:any)=>intent.actor===id)
    ||(board.action_admission_intents||[]).some((intent:any)=>intent.actor===id)
    ||board.actions.some((action:any)=>action.character===id);
  return [
    {id:"select-character",label:"在队伍中选择",run:()=>{WB.state.selectedActor=id;WB.render();}},
    {id:"edit-character",label:"装备与面板",run:()=>WB.editCharacter(id)},
    {type:"separator"},
    {id:"remove-character",label:"移除角色",danger:true,enabled:!blocked,disabledReason:blocked?"请先处理该角色的动作或次数／接纳意图":"",run:()=>WB.removeCharacter(id)},
  ];
};

  return {pick,addCharacter,editCharacter,recalculateEquipment,saveEquipmentCandidate,removeCharacter,characterMenuItems};
}
