"use strict";
// Adapter between the shared editor and native plans. No calculated panel is inferred.
EQ.gearKey=input=>JSON.stringify([input?.weapon?Object.fromEntries(Object.entries(input.weapon).filter(([k])=>k!=="name")):null,
  (input?.artifact_sets||[]).map(s=>({set_id:s.set_id,entity_ref:s.entity_ref??null,count:s.count})),input?.artifact_presence??null]);
EQ.confirmedGear=new Map();
EQ.initialInput=id=>{try{return JSON.parse(PL.state.draftInitialJson||"null")?.equipment_inputs?.[id];}catch{return null;}};
EQ.cleanPanel=value=>Object.fromEntries(Object.entries(value||{}).filter(([,v])=>v!=null).map(([k,v])=>[k,k==="dmg_bonus"?Object.fromEntries(Object.entries(v).filter(([,n])=>n!=null)):v]));
EQ.validateWorkbench=()=>{
  const errors=EQ.workbench?.validate()||[];
  for(const [id,input] of Object.entries(EQ.plan()?.equipment_inputs||{})){
    if(input.mode!=="panel"||EQ.gearKey(input)===EQ.gearKey(EQ.initialInput(id)))continue;
    if(EQ.confirmedGear.get(id)!==EQ.gearKey(input))errors.push(`${DS.character(id,EQ.plan().manual_template,EQ.plan())}：请核对换装后的面板。`);
  }
  if(errors.length){EQ.error={message:[...new Set(errors)].join("\n")};EQ.renderPreview();setStatus(errors[0]);return false;}return true;
};
EQ.previewEdit=async(draft,update)=>{
  let candidate=UI.clone(draft);
  if(update){const refreshed=await callApi("apply_data_update",candidate);if(refreshed.code)return refreshed;candidate=refreshed.data.plan;}
  const previous=JSON.parse(PL.state.draftInitialJson||"null");
  if(previous){previous.resolution={state:"pending"};previous.resolved_template=null;}
  return callApi("preview_equipment_edit",candidate,previous);
};
const legacyEquipmentRender=EQ.render,legacyEquipmentPreview=EQ.renderPreview,legacyEquipmentClose=EQ.closeView;
EQ.closeView=()=>{EQ.workbench?.destroy();EQ.workbench=null;legacyEquipmentClose();};
EQ.render=()=>{
  const basisSnapshot=EQ.workbench?.basisSnapshot?.();
  EQ.workbench?.destroy();EQ.workbench=null;
  if(!window.EW||!EQ.plan()||(EQ.input()&&!EQ.isPanel())){EQ.workbenchActive=false;return legacyEquipmentRender();}
  EQ.workbenchActive=true;const box=$("#equipment-editor"),scroll=box.scrollTop,plan=EQ.plan(),id=EQ.characterId,
    char=plan.manual_template.characters.find(c=>c.id===id);if(!char)return;
  const input=EQ.input()||EQ.blankPanelInput(),line=input.data_version?.version_line||DV.state.versionLine;
  box.innerHTML="";const heading=UI.el("div",null,"eq-heading");
  heading.append(UI.button("← 返回",PL.back,"eq-back"),UI.el("h3","装备与初始面板"));box.append(heading);
  const toolbar=UI.el("div",null,"eq-toolbar");toolbar.append(UI.field("当前角色",UI.select(DS.characters(plan.manual_template,plan),id,value=>{EQ.characterId=value;EQ.section="gear";EQ.render();})));
  toolbar.append(UI.button("配装与面板",()=>{EQ.section="gear";EQ.render();},["gear","basis"].includes(EQ.section)?"active":""));
  const advanced=UI.el("details",null,"ew-native-advanced");advanced.append(UI.el("summary","高级规则与资料"));
  for(const [section,label] of [["effects","效果处理"],["passives","固有天赋"],["tuning","轴微调与增益关联"]])advanced.append(UI.button(label,()=>{EQ.section=section;EQ.render();}));
  advanced.append(UI.button("查看并应用新资料",()=>PL.preview(true)));toolbar.append(advanced);box.append(toolbar);
  box.append(UI.el("p","修改先保留为草稿。应用后重新核算；保存文件请使用顶部“保存”。返回会保留草稿。","ew-note"));
  const content=UI.el("div",null,"ew-native-content");box.append(content);
  if(["gear","basis"].includes(EQ.section)){
    const binding=UI.el("details",null,"ew-native-binding");binding.append(UI.el("summary","角色资料绑定"),UI.field("数据库角色",UI.select([{value:"",label:"尚未绑定"},
      ...(DS.catalogs.get(line)?.characters||[]).map(c=>({value:String(c.id),label:c.display_name||c.name}))],String(input.character_ref?.db_character_id||""),value=>{
      const next=plan.equipment_inputs[id] ||= EQ.blankPanelInput();next.character_ref=value?{db_character_id:Number(value),version_line:line}:null;
      next.character_entity_ref=value?{kind:"database",entity_type:"character",database_id:Number(value),version_line:line}:null;
      delete next.progression;delete next.artifact_presence;next.panel_basis_mode=value?"database":"manual";for(const [key] of EW.baseFields)delete char.stats[key];PL.touch();EQ.render();})));
    if(input.character_entity_ref?.kind==="provisional")binding.append(UI.el("p","当前角色使用仅限本方案的临时资料。数据库核对与来源授权失效时会拒绝计算；正式结果将明确标注为近似。","ew-note"));
    content.append(binding);
    EQ.workbench=EW.mount(content,{actorDraft:{id,name:DS.character(id,plan.manual_template,plan),db_character_id:input.character_ref?.db_character_id,character_ref:input.character_entity_ref,
      level:char.level,constellation:input.progression?.constellation,ascension:input.progression?.ascension,talent_base_levels:input.progression?.talent_base_levels,artifact_presence:input.artifact_presence,weapon:input.weapon,artifact_sets:input.artifact_sets,panel:char.stats,panel_basis_mode:input.panel_basis_mode,notes:input.source_refs?.find(s=>s.kind==="manual")?.note||""},
      versionLine:line,dataVersion:input.data_version,weaponShape:"native",showConstellation:true,basisSnapshot,
      isCurrent:()=>EQ.plan()===plan&&EQ.characterId===id&&EQ.showing,
      requirePanelConfirmation:EQ.gearKey(input)!==EQ.gearKey(EQ.initialInput(id)),panelConfirmed:EQ.confirmedGear.get(id)===EQ.gearKey(input),
      onChange:(actor,meta)=>{
        const next=plan.equipment_inputs[id] ||= EQ.blankPanelInput();char.level=actor.level;char.stats=EQ.cleanPanel(actor.panel);next.panel_basis_mode=actor.panel_basis_mode;
        if(actor.character_ref)next.character_entity_ref=UI.clone(actor.character_ref);
        next.weapon=actor.weapon?Object.fromEntries(Object.entries(actor.weapon).filter(([key])=>["weapon_id","entity_ref","level","ascension","refinement","refinements","selected_by"].includes(key))):null;
        next.artifact_sets=(actor.artifact_sets||[]).map(s=>({set_id:s.set_id,entity_ref:s.entity_ref??null,count:s.count}));
        if(actor.artifact_presence)next.artifact_presence=UI.clone(actor.artifact_presence);else delete next.artifact_presence;
        if(meta.kind==="identity"){
          const p=next.progression ||= {ascension:null,status:"pending",source:"用户角色成长输入"};
          p.ascension=actor.ascension??null;p.status=p.ascension==null?"pending":"confirmed";p.source="用户角色成长输入";
          if(actor.constellation==null)delete p.constellation;else p.constellation=actor.constellation;
          if(actor.talent_base_levels)p.talent_base_levels=UI.clone(actor.talent_base_levels);
        }
        if(meta.dataVersion)next.data_version=meta.dataVersion;
        if(meta.kind==="notes")next.source_refs=[...(next.source_refs||[]).filter(s=>s.kind!=="manual"),{kind:"manual",note:actor.notes}];
        if(meta.panelConfirmed)EQ.confirmedGear.set(id,EQ.gearKey(next));else if(meta.equipmentChanged)EQ.confirmedGear.delete(id);
        EQ.error=null;PL.touch();
      }});
  }else if(EQ.section==="effects")EF.render(content);
  else if(EQ.section==="passives"&&window.PB)PB.render(content);
  else EF.renderTuning(content);
  const preview=UI.el("div");preview.id="equipment-preview";box.append(preview);
  const footer=UI.el("div",null,"eq-toolbar eq-actions ew-actions"),more=UI.el("details",null,"ew-overflow ew-footer-more");
  more.append(UI.el("summary","更多"),UI.button("保存副本交给 Agent",()=>EQ.saveAgentCopy()));
  footer.append(UI.button("应用并计算",()=>PL.apply(),"primary"),UI.button("预览变化",()=>PL.preview()),UI.button("放弃本次修改",PL.discard,"danger"),more);box.append(footer);
  EQ.renderPreview();box.scrollTop=scroll;
};
EQ.issueText=issue=>typeof issue==="string"?issue:(issue.message||issue.reason||issue.detail||"存在尚未确认的计算输入，请由 Agent 复核来源与规则。");
EQ.issueRows=report=>{
  const value=report?.missing_inputs||report?.issues||[],issues=Array.isArray(value)?value:(value.missing_inputs||value.issues||[value]);return [...new Set(issues.map(EQ.issueText))];
};
EQ.renderPreview=()=>{
  if(!EQ.workbenchActive)return legacyEquipmentPreview();const box=$("#equipment-preview");if(!box)return;box.innerHTML="";
  if(EQ.busy){box.append(UI.el("p","正在核对装备、面板与计算依赖…","ew-note"));return;}
  if(EQ.error){box.append(UI.el("p",EQ.error.message||"输入无法应用，请检查数值。","ew-agent-report"));
    if(EQ.error.details)box.append(UI.details("需要填写的属性与错误定位",EQ.error.details));return;}
  const report=PL.state.report;if(!report){box.append(UI.el("p","应用后会检查计算所需输入。缺少规则、人工效果复核或外部数值时，将明确提示交给 Agent。","ew-note"));return;}
  const issues=EQ.issueRows(report),summary=report.state==="ready"?"输入已就绪，可应用并计算。游戏机制与循环是否完整仍以计算检查为准。":
    `${issues[0]||"当前配置还有无法自动完成的计算项。"}${issues.length>1?`（另有 ${issues.length-1} 项）`:""}`;
  box.append(UI.el("p",summary,"ew-agent-report"));
  if(report.result_fidelity==="approximate_with_provisional_entities")box.append(UI.el("p","当前配装使用仅限本方案的临时公共资料；正式结果会标注近似，并保留数据库核对、来源与授权回执。","ew-agent-report"));
  if(issues.length){const issueDetails=UI.el("details",null,"ew-issue-details"),list=UI.el("ul");issues.forEach(message=>list.append(UI.el("li",message)));
    issueDetails.append(UI.el("summary",`查看全部待处理项（${issues.length}）`),list);box.append(issueDetails);}
  box.append(UI.details("计算缺项与来源定位",report.missing_inputs||[]),UI.details("面板与装备核对明细",report.equipment_summary||report.contribution_report||{}));
};
EQ.agentTask=(plan,report,path)=>`请补全本方案并重新计算。\n${path?`方案文件：${path}\n`:""}保留用户已选择的武器、圣遗物套装、初始面板以及动作顺序和时点。初始面板已包含装备与两件套常驻属性，请勿重复叠加。核对改装后的效果来源、手工规则及外部伤害补充；不得直接刷新旧确认来绕过失效检查。必要的排轴改动另给建议。\n待处理：\n${EQ.issueRows(report).map(s=>`- ${s}`).join("\n")||"- 检查动作、元素能量与机制完整性，确认缺项后交付可重算结果。"}`;
EQ.reportCalculation=(plan,calculation)=>{
  if(!calculation)return;
  if(calculation.error){EQ.showFollowup(plan,{missing_inputs:[{message:calculation.error.message||"计算失败，请让 Agent 核对输入与规则。"}]});return;}
  const result=calculation.result||{},check=result.rotation_check||{},missing=[];
  for(const [key,label] of [["action_check","动作与冷却"],["energy_check","元素能量循环"]])if(check[key]?.status!=="passed")missing.push({message:`${label}尚未全部验证通过。请交给 Agent 核对，已计算伤害不代表循环已通过。`});
  if(result.mechanics_status&&!['ready','passed'].includes(result.mechanics_status))missing.push({message:"机制执行仍有待核实项，请让 Agent 检查机制诊断与轴覆盖完整性。"});
  if(missing.length)EQ.showFollowup(plan,{missing_inputs:missing},"",true);
};
EQ.saveAgentCopy=async()=>{
  if(PL.state.draft&&!await PL.preview())return false;
  const source=PL.state.draft||PL.state.plan;if(!source)return false;
  const path=window.prompt("保存待复核原生方案副本（请输入一个新文件路径）",`${RT.state.plansDir||"plans"}/装备修改待Agent复核-${Date.now()}.plan.json`);if(!path)return false;
  const report=UI.clone(PL.state.report||source.resolution||{}),plan=UI.clone(source),json=JSON.stringify(PL.input(source)),request=PL.state.request,
    r=await callApi("save_plan",path,plan,null);
  if(r.code){UI.error(r);setStatus(r.message||"副本保存失败");return false;}
  if(source!==(PL.state.draft||PL.state.plan)||json!==JSON.stringify(PL.input(source))||request!==PL.state.request){
    setStatus("已保存所提交的历史副本；当前方案已有变化，请重新检查当前配置。",{path:r.data.path});return true;}
  EQ.showFollowup(plan,report,r.data.path);setStatus("已另存交给 Agent 的原生方案，当前文件仍待保存",{path:r.data.path});return true;
};
EQ.showFollowup=(plan,report,path="",calculated=false)=>{
  EQ.followupDialog?.finish?.("replaced",{restoreFocus:false});
  const current=PL.state.draft||PL.state.plan,json=JSON.stringify(PL.input(current));
  let dialog=null;
  dialog=window.AppDialogKernel?.create?.({title:"需要 Agent 完善计算",
    description:calculated?"新配置已完成数值计算，但以下检查尚未通过，不能视为已验证的可执行 DPS。可复制说明交给 Agent 继续处理。":"本次配置已保留。以下内容可选中复制，交给 Agent 补齐后重新计算；此前伤害结果不代表新配置。",
    className:"equipment-agent-followup",onRequestClose:()=>{if(EQ.followupDialog===dialog)EQ.followupDialog=null;return true;}});
  if(!dialog){setStatus("当前还有一项需要先处理，请关闭后再查看 Agent 任务说明");return null;}
  const text=UI.el("textarea");text.readOnly=true;text.rows=12;text.value=EQ.agentTask(plan,report,path);text.setAttribute("aria-label","交给 Agent 的任务说明");text.style.width="100%";
  text.style.background="var(--bg)";text.style.color="var(--text)";text.style.padding="10px";text.style.lineHeight="1.6";
  const list=UI.el("ul");EQ.issueRows(report).forEach(message=>list.append(UI.el("li",message)));
  dialog.appBody.append(list,UI.details("全部缺项与来源定位",report.missing_inputs||report.issues||[]),text);
  const select=UI.button("选中任务说明",()=>{text.focus();text.select();}),save=UI.button("保存方案副本",()=>{
      if(current!==(PL.state.draft||PL.state.plan)||json!==JSON.stringify(PL.input(current))){setStatus("方案已变化，请返回后重新核对当前计算缺项。");return;}
      return EQ.saveAgentCopy();}),back=UI.button("返回方案",()=>dialog.finish("back"));
  dialog.setActions(select,save,back);
  EQ.followupDialog=dialog;
  dialog.focusInitial(back);return dialog;
};
