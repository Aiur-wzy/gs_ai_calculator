"use strict";
// Metadata owns native declarations; all numeric projection remains in Python.
window.AM={};
AM.caps=()=>PL.state.caps?.attack_metadata_contract;
AM.elements={cryo:"冰",hydro:"水",anemo:"风",pyro:"火",electro:"雷",geo:"岩",dendro:"草",physical:"物理"};
AM.applicationModes=[{value:"pending",label:"待核实（阻止确定性计算）"},{value:"sequence",label:"有限序列（计数标签 + 规则组）"},
  {value:"no_icd",label:"每次均附着（无 ICD）"},{value:"no_application",label:"明确不施加元素"}];
AM.mode=value=>{const raw=typeof value==="string"?value:value?.application_mode?.value||value?.application_mode;return raw==="unknown"||!raw?"pending":raw;};
AM.modeText=value=>({sequence:"有限序列",no_icd:"每次均附着（无 ICD）",no_application:"明确不施加元素",pending:"待核实"})[AM.mode(value)]||"待核实";
AM.records=report=>report?.attack_metadata_compilations||[];
AM.identity=projection=>projection?.application_identity||{status:"pending",tag:null,evidence:"attenuation_tag_missing"};
AM.sourceText=source=>typeof source==="string"?source:source?.source_path?`${source.source_path}${source.json_pointer||""}`:"未提供";
AM.identityText=projection=>{
  const identity=AM.identity(projection);
  if(identity.status==="ready")return `数据库确认 · 附着计数标签（ICD Tag）：${identity.tag}`;
  return "待核实 · 当前攻击组件没有可靠 attenuationTag；须查询这一具体攻击段，不能按普攻/战技/爆发类别代填";
};
AM.compact=value=>value===null||value===undefined||value===""?"未提供":typeof value==="object"?JSON.stringify(value):String(value);
AM.renderResearchReport=(box,completed)=>{
  if(!completed||typeof completed!=="object")return false;
  const list=(value,format=value=>AM.compact(value))=>Array.isArray(value)&&value.length?value.map(format).join("；"):"无";
  const decisions={adopted:"adopted（已采纳）",rejected:"rejected（已拒绝）",inconclusive:"inconclusive（未定）"};
  const statuses={proposed:"proposed（拟议）",applied:"applied（已回填）",deferred:"deferred（延期）"};
  const source=row=>`${AM.compact(row?.locator)}（版本 ${AM.compact(row?.version)}；访问 ${AM.compact(row?.accessed_at)}；支持字段 ${list(row?.supported_fields)}；处理 ${decisions[row?.decision]||AM.compact(row?.decision)}；理由 ${AM.compact(row?.reason)}）`;
  const patch=row=>`${AM.compact(row?.target)}（字段 ${list(row?.fields)}；证据 ${list(row?.evidence_locators)}；状态 ${statuses[row?.status]||AM.compact(row?.status)}；说明 ${AM.compact(row?.note)}）`;
  const authority=completed.execution_authority==="none"?"none":"无效值（按无执行权处理）";
  box.append(UI.el("h4","已保存的 Agent 查证记录（无执行权）"),
    UI.el("p",`报告版本：${AM.compact(completed.report_version)}；执行权：${authority}。该记录只说明查证过程，不能代替 verification 或授予计算执行权。`),
    UI.el("p",`实际检索：${list(completed.actual_queries)}`),
    UI.el("p",`已查来源：${list(completed.sources_checked,source)}`),
    UI.el("p",`采纳结论：${list(completed.adopted_findings)}`),
    UI.el("p",`拒绝结论：${list(completed.rejected_findings)}`),
    UI.el("p",`仍未确定：${list(completed.remaining_unknowns)}`,Array.isArray(completed.remaining_unknowns)&&completed.remaining_unknowns.length?"eq-error":null),
    UI.el("p",`公共数据回填计划：${list(completed.database_patch_plan,patch)}`),
    UI.details("查看完整查证记录",completed));
  return true;
};
AM.renderVerificationTask=(box,task,nextAction)=>{
  if(!task)return;
  const identity=task.attack_identity||{},identityOrder=["character_id","actor","slot","skill_id","ability_name","candidate_id","action_event_profile_id","profile_id","channel_id","component_profile_id","prototype_hit_id","parent_pointer","constellation","version_line"];
  const identityText=identityOrder.filter(key=>Object.hasOwn(identity,key)).map(key=>`${key}=${AM.compact(identity[key])}`).join("；")||"未提供";
  const known=task.known_fields||{},knownText=Object.keys(known).map(key=>`${key}=${AM.compact(known[key])}`).join("；")||"无";
  const missing=(task.missing_fields||[]).join("、")||"无",conflicts=(task.conflicts||[]).map(AM.compact).join("；")||"无";
  const checks=(task.local_checks||[]).map(AM.compact).join("；")||"无";
  const questions=(task.targeted_research_questions||[]).join("；")||"无",keywords=(task.keywords||[]).join("、")||"无";
  const patches=(task.database_patch_targets||[]).join("、")||"无";
  const reportTemplate=task.research_report_template;
  box.append(UI.el("h4","逐攻击段核实工作单"),UI.el("p",`精确攻击身份：${identityText}`),
    UI.el("p",`下一步：${nextAction||task.next_action||task.acceptance_required||"按缺失字段完成逐段核实。"}`),
    UI.el("p",`已知：${knownText}`),UI.el("p",`缺失：${missing}`,missing==="无"?null:"eq-error"),
    UI.el("p",`冲突：${conflicts}`,conflicts==="无"?null:"eq-error"),UI.el("p",`本地检查：${checks}`),
    UI.el("p",`定向检索问题：${questions}`),UI.el("p",`检索关键词：${keywords}`),
    UI.el("p",`数据库回填目标：${patches}`));
  const uses=Array.isArray(task.uses)?task.uses:[];
  if(uses.length){box.append(UI.el("h4",`具体使用点（${uses.length}）`));uses.forEach((use,index)=>{
    const order=["actor","character_id","slot","skill_id","candidate_id","ability_id","action_id","hit_id","pattern_id","application_event_id","action_event_profile_id","channel_id","component","component_id","variant","constellation","target","target_id","version_line"];
    const body=order.filter(key=>Object.hasOwn(use,key)).map(key=>`${key}=${AM.compact(use[key])}`).join("；")||AM.compact(use);
    box.append(UI.el("p",`使用点 ${index+1}：${body}`));
  });}else if(Object.hasOwn(task,"uses"))box.append(UI.el("p","具体使用点：后端未找到引用；需先补齐档案到动作/命中/附着事件的精确映射。","eq-error"));
  if(task.unresolved_identity_fields?.length)box.append(UI.el("p",`未解析身份字段：${task.unresolved_identity_fields.join("、")}`,"eq-error"));
  AM.renderResearchReport(box,task.research_report);
  if(reportTemplate&&typeof reportTemplate==="object"){
    box.append(UI.el("h4","后端 research_report_template"),UI.el("p","模板用于记录实际检索与回填计划；填写模板不会获得执行权，也不能替代逐分量 verification。"));
    Object.entries(reportTemplate).forEach(([key,value])=>box.append(UI.el("p",`${key}：${AM.compact(value)}`)));
  }
  box.append(UI.details("原始核实工作单载荷",task));
};
AM.renderReceipt=(box,report)=>{
  AM.records(report).forEach(row=>{
    const projection=row.projection||{},card=UI.el("section",null,"eq-card"),gauge=projection.gauge||{},identity=row.application_identity||AM.identity(projection),rule=projection.attenuation?.profile?.projection,mode=AM.mode(row);
    card.append(UI.el("h4","数据库攻击元信息"),UI.el("p",`${row.status==="ready"?"范围内资料已装配":"资料待完善"}；整技能仅部分覆盖，仍按动作档案校准派发。`),
      UI.el("p",`元素：${AM.elements[projection.element?.value]||"未知"}；原始附着量：${mode==="no_application"?"不适用":gauge.value??"未知"} GU；倍率参数：${projection.parameter?.value??"未知"}。`),
      UI.el("p",`附着模式：${AM.modeText(mode)}${row.application_mode?.deprecated_alias?"；旧 unknown 已规范为 pending":""}。`),
      UI.el("p",mode==="sequence"?`附着计数标签（ICD Tag）：${identity.tag||"待核实"}；身份依据：${identity.status==="database_confirmed"?"数据库确认":identity.status==="declared"?"方案有证据声明":"待核实"}。`:
        mode==="pending"?"附着计数标签（ICD Tag）：待核实；Agent须查询这一具体攻击段。":"附着计数标签（ICD Tag）：不适用。"),
      UI.el("p",mode==="sequence"?`附着规则组：${projection.attenuation?.group||row.compiled_fields?.rule_group||"待核实"}；有限序列与重置由该规则组提供，规则组本身不是共享计数身份。`:
        mode==="no_icd"?"附着规则组：不适用；可靠证据声明每次命中均允许附着。":mode==="no_application"?"附着规则组：不适用；该攻击组件明确不尝试附着。":"附着规则组：待核实。"),
      ...(mode==="sequence"?[UI.el("p",`规则参数：重置 ${rule?.reset_cycle_seconds_rounded??"待核实"} 秒；有限序列 ${Array.isArray(rule?.durability_sequence)?rule.durability_sequence.join("、"):"待核实"}。`),
        UI.el("p",`计数标签来源：${AM.sourceText(identity.source)}；规则组来源：${AM.sourceText(projection.attenuation?.profile?.source)}。`)]:[]),
      UI.el("p","附着量尚未扣底税；底税由元素引擎处理一次。同技能不同段、召唤物和命座追加攻击都可能使用不同计数标签，需分别核实。"));
    AM.renderVerificationTask(card,row.verification_task,identity.next_action);
    AE.issues(card,row.issues,"攻击来源待办");card.append(UI.details("完整父链、分支、单位与参数来源",row));box.append(card);
  });
};
AM.migration=(plan,profile)=>{
  const hit=plan.manual_template.hits.find(h=>h.id===profile.prototype_hit_id),rows=[];
  if(hit&&Object.hasOwn(hit,"element"))rows.push("原型的手工元素");
  if(hit&&Object.hasOwn(hit,"multiplier"))rows.push("原型的手工倍率");
  if(plan.hit_parameter_sources?.some(r=>r.hit_id===profile.prototype_hit_id))rows.push("该原型的独立倍率来源");
  if(plan.mechanics_model?.elemental_model?.profiles.some(r=>r.id===profile.application_profile_id))rows.push("该输出槽位的手工附着档案（含元素、GU和ICD）");
  if(plan.elemental_sources?.some(r=>r.profile_id===profile.application_profile_id))rows.push("该附着档案的独立数据库来源");
  return rows;
};
AM.edit=profileId=>{
  if(!AM.caps()){setStatus("当前版本尚无攻击元信息接口");return;}
  const s=AE.session("选择数据库攻击来源");if(!s)return;
  const profile=s.local.action_event_profiles.find(p=>p.id===profileId),original=s.original;
  if(!profile){s.close();return;}
  const hit=original.manual_template.hits.find(h=>h.id===profile.prototype_hit_id),existing=original.attack_metadata_sources?.find(r=>r.action_event_profile_id===profileId);
  if(!hit){s.close();setStatus("请先关联有效命中原型");return;}
  const oldParameter=original.hit_parameter_sources?.find(r=>r.hit_id===hit.id),oldElement=original.elemental_sources?.find(r=>r.profile_id===profile.application_profile_id);
  const ref=original.equipment_inputs?.[hit.character]?.character_ref;
  const row=UI.clone(existing||{action_event_profile_id:profileId,version_line:oldParameter?.version_line||ref?.version_line||"release",
    selection:oldParameter?.selection||{character_id:ref?.db_character_id??null,slot:"skill",constellation:null},candidate_id:null,
    application_mode:"pending",purpose:"single_stat_damage_multiplier",status:"pending",source:""});
  let rememberedGroup=row.counter_tag||row.shared_group||oldElement?.counter_tag||oldElement?.shared_group||"";row.application_mode=AM.mode(row);
  delete row.shared_group;if(row.application_mode==="sequence")row.counter_tag=rememberedGroup;else delete row.counter_tag;
  const verification=UI.clone(row.verification||{locator:"",version:"",reviewed_at:"",component_scope:""});delete row.verification;
  let candidates=[],query=null,request=0,readSelection=null,removing=false,restoreElement="";
  const queryBox=UI.el("div"),candidateBox=UI.el("div"),modeBox=UI.el("div"),migration=AM.migration(original,profile);
  const selector=()=>JSON.stringify([row.version_line,row.selection]);
  const changed=(selection=false)=>{request++;if(selection){candidates=[];query=null;readSelection=null;row.candidate_id=null;drawCandidates();}s.changed(profile);};
  const selectionValid=()=>{
    if(!Number.isInteger(row.selection.character_id)||row.selection.character_id<1)throw Error("请明确数据库角色身份。");
    if(!Number.isInteger(row.selection.constellation)||row.selection.constellation<0||row.selection.constellation>6)throw Error("请填写实际命座，不能为通过支持范围擅自改成零命。");
    if(row.selection.talent_level!==undefined&&(!Number.isInteger(row.selection.talent_level)||row.selection.talent_level<1||row.selection.talent_level>15))throw Error("请填写实际有效天赋等级；零命缺省为10级。");
    if(row.selection.skill_depot_id!==undefined&&(!Number.isInteger(row.selection.skill_depot_id)||row.selection.skill_depot_id<1))throw Error("技能组身份须为有效整数或留空。");
  };
  const drawCandidates=()=>{
    candidateBox.innerHTML="";
    if(!query){candidateBox.append(UI.el("p","先读取当前技能的攻击候选；已保存的候选也会在计算时重新核对。"));return;}
    const options=[{value:"",label:"尚未选择，保持待完善"},...candidates.map((c,i)=>({value:c.candidate_id,
      label:`候选 ${i+1} · ${AM.elements[c.projection?.element?.value]||"元素待核实"} · ${c.projection?.gauge?.value??"?"} GU · ${AM.identity(c.projection).status==="ready"?"计数标签已确认":"计数标签待核实"}`}))];
    candidateBox.append(UI.field("本次采用的精确攻击候选",UI.select(options,row.candidate_id||"",v=>{row.candidate_id=v||null;
      const identity=AM.identity(candidates.find(c=>c.candidate_id===row.candidate_id)?.projection);if(AM.mode(row)==="sequence"&&identity.status==="ready"){row.counter_tag=identity.tag;rememberedGroup=identity.tag;}
      changed();drawCandidates();drawMode();})),
      UI.el("p","互斥条件两侧可能属于同一个元信息候选；选择候选不表示它们都会出伤，也不会自动确定点按或长按。"));
    const selected=candidates.find(c=>c.candidate_id===row.candidate_id);
    if(selected){candidateBox.append(UI.el("p",AM.identityText(selected.projection),AM.identity(selected.projection).status==="ready"?null:"eq-error"));
      if(selected.projection?.attack_tag?.value)candidateBox.append(UI.el("p",`攻击标签 attackTag：${selected.projection.attack_tag.value}；它不是计数标签，不得用于填充 ICD Tag。`));
      AE.issues(candidateBox,selected.issues,"候选待办");candidateBox.append(UI.details("所选候选的原始身份与完整投影",selected));}
    AE.issues(candidateBox,query.issues,"本次查询待办");candidateBox.append(UI.details("本次数据库候选与父链证据",query));
  };
  const drawMode=()=>{modeBox.innerHTML="";const mode=AM.mode(row);
    modeBox.append(UI.field("元素附着模式",UI.select(AM.applicationModes,mode,v=>{row.application_mode=v;
      if(v==="sequence")row.counter_tag=rememberedGroup;else delete row.counter_tag;if(v==="pending")row.status="pending";changed();drawMode();})));
    if(mode==="sequence"){const selected=candidates.find(c=>c.candidate_id===row.candidate_id),databaseIdentity=AM.identity(selected?.projection);
      modeBox.append(UI.field("附着计数标签（ICD Tag）",UI.input(row.counter_tag||"",v=>{row.counter_tag=v;rememberedGroup=v;changed();})),
        UI.el("p","规则组提供重置时间与序列，计数标签决定哪些具体攻击共享计数；attackTag 不能代替 attenuationTag。"));
      if(selected&&databaseIdentity.status!=="ready"&&row.status!=="confirmed")modeBox.append(UI.el("p","数据库没有该攻击分量的 attenuationTag。只有带版本、来源定位和采纳理由的逐段证据并标为已确认，才能执行；假设状态会保持待核实。","eq-error"));}
    else if(mode==="no_icd")modeBox.append(UI.el("p","只有已确认的逐攻击组件正向证据能执行无 ICD；编译后每次命中均允许附着，不使用计数标签或规则组。",row.status==="confirmed"?null:"eq-error"));
    else if(mode==="no_application")modeBox.append(UI.el("p","只有已确认的逐攻击组件正向证据能声明该攻击不尝试给目标附着；这与缺少附着资料不同。",row.status==="confirmed"?null:"eq-error"));
    else modeBox.append(UI.el("p","待核实会阻止确定性计算。Agent须查询该角色这一具体攻击段，不能按普攻、战技或爆发类别猜测。","eq-error"));
  };
  const read=async()=>{
    try{selectionValid();}catch(error){queryBox.innerHTML="";queryBox.append(UI.el("p",error.message,"eq-error"));return;}
    const stamp=selector(),attempt=++request;queryBox.innerHTML="";
    const payload={action:"attack_metadata_profile",version_line:row.version_line,...UI.clone(row.selection)};let response;
    try{response=await callApi("query_runtime",payload);}catch(error){if(s.current()&&attempt===request)queryBox.append(UI.el("p",`读取失败：${error.message}`,"eq-error"));return;}
    if(!s.current()||attempt!==request||selector()!==stamp){setStatus("旧攻击候选响应已忽略，请重新读取");return;}
    if(response.code){queryBox.append(UI.error(response));return;}
    if(!Array.isArray(response.data?.candidates)||response.data.candidates.length>32||response.data.candidates.some(c=>typeof c.candidate_id!=="string")||new Set(response.data.candidates.map(c=>c.candidate_id)).size!==response.data.candidates.length){
      queryBox.append(UI.el("p","接口候选列表不完整或超限，未采用。","eq-error"));return;}
    query=UI.clone(response.data);candidates=query.candidates;readSelection=stamp;
    if(row.candidate_id&&!candidates.some(c=>c.candidate_id===row.candidate_id)){row.candidate_id=null;s.changed(profile);queryBox.append(UI.el("p","原候选已不属于当前选择，请重新选择。","eq-error"));}
    drawCandidates();
  };
  s.form.append(UI.el("p",AE.name(profile,original)),UI.el("p","一条综合来源提供倍率位置、元素、原始GU及衰减配置；使用动作档案的一次综合确认。原生不保存自动数值或派生附着档案。"),
    UI.field("资料线",UI.select([{value:"release",label:"主要资料线（沿用当前库）"},{value:"beta",label:"第二资料线"}],row.version_line,v=>{row.version_line=v;changed(true);})),
    UI.field("数据库角色身份",UI.input(row.selection.character_id,v=>{row.selection.character_id=v;changed(true);},true)),
    UI.field("实际命座（当前自动范围为零命）",UI.input(row.selection.constellation,v=>{row.selection.constellation=v;changed(true);},true)),
    UI.field("有效天赋等级（零命留空默认10级）",UI.input(row.selection.talent_level,v=>{if(v===null)delete row.selection.talent_level;else row.selection.talent_level=v;changed(true);},true)),
    UI.field("技能组身份（有多个技能组时必填）",UI.input(row.selection.skill_depot_id,v=>{if(v===null)delete row.selection.skill_depot_id;else row.selection.skill_depot_id=v;changed(true);},true)),
    UI.el("p",row.selection.skill_id?`沿用精确技能身份 ${row.selection.skill_id}；当前只支持元素战技槽。`:"查询元素战技槽；不会改变角色命座、动作或天赋设置。"),
    UI.button("读取攻击候选",read),queryBox,candidateBox,modeBox,
    UI.el("p","数据库未确认 attenuationTag 时，Agent 必须查询该角色这一具体攻击段并写明依据；同技能不同段、召唤物、命座追加攻击不可沿用动作类别默认值。"),
    UI.field("单攻击力倍率与共享关系的核实状态",UI.select(["pending","assumed","confirmed"].map(v=>({value:v,label:RC.status(v)})),row.status,v=>{row.status=v;changed();drawMode();})),
    UI.field("用途与逐攻击段附着依据（版本、来源定位、检索日期与采纳理由）",UI.area(row.source,v=>{row.source=v;changed();})),
    UI.field("逐段证据定位（URL、本地资料路径或原始 pointer）",UI.input(verification.locator,v=>{verification.locator=v;changed();})),
    UI.field("适用游戏版本或固定提交",UI.input(verification.version,v=>{verification.version=v;changed();})),
    UI.field("检索/复核日期（YYYY-MM-DD）",UI.input(verification.reviewed_at,v=>{verification.reviewed_at=v;changed();})),
    UI.field("具体攻击分量与共享范围",UI.area(verification.component_scope,v=>{verification.component_scope=v;changed();})));
  s.form.append(UI.el("h4","应用时接管的字段"),UI.el("p",migration.length?migration.join("；")+"。它们将从原生移除，由本次新鲜来源在执行副本交付。":"该档案当前没有重复的手工来源。"));
  if(existing){
    s.form.append(UI.button("改为移除该攻击来源",()=>{removing=true;request++;s.changed(profile);s.actions.innerHTML="";AE.controls(s);queryBox.innerHTML="";queryBox.append(UI.el("p","将仅移除此综合来源并补回你明确选择的原型元素。倍率及附着来源需在原有编辑器补齐，档案保持待完善；不复制上次计算结果。"));}),
      UI.field("移除来源时补回的原型元素",UI.select([{value:"",label:"请选择，只有移除时需要"},...Object.entries(AM.elements).map(([value,label])=>({value,label}))],"",v=>{restoreElement=v;changed();})));
  }
  s.transform=plan=>{
    syncVerification();
    const sources=UI.clone(original.attack_metadata_sources||[]).filter(r=>r.action_event_profile_id!==profileId);
    if(!removing)sources.push(UI.clone(row));plan.attack_metadata_sources=sources;
    const target=plan.manual_template.hits.find(h=>h.id===profile.prototype_hit_id);
    if(removing){if(restoreElement)target.element=restoreElement;return;}
    delete target.element;delete target.multiplier;
    if(plan.hit_parameter_sources)plan.hit_parameter_sources=plan.hit_parameter_sources.filter(r=>r.hit_id!==profile.prototype_hit_id);
    if(plan.elemental_sources)plan.elemental_sources=plan.elemental_sources.filter(r=>r.profile_id!==profile.application_profile_id);
    const elemental=plan.mechanics_model?.elemental_model;if(elemental)elemental.profiles=elemental.profiles.filter(r=>r.id!==profile.application_profile_id);
  };
  const syncVerification=()=>{
    const mode=AM.mode(row),selected=candidates.find(c=>c.candidate_id===row.candidate_id),identity=AM.identity(selected?.projection);
    const needsEvidence=(mode==="sequence"&&identity.status!=="ready")||mode==="no_icd"||mode==="no_application";
    const complete=["locator","version","reviewed_at","component_scope"].every(key=>(verification[key]||"").trim());
    delete row.verification;if(needsEvidence&&row.status==="confirmed"&&complete)row.verification={status:"confirmed",...UI.clone(verification),
      confirmed_fields:mode==="sequence"?["application_mode","counter_tag","shared_scope"]:["application_mode","shared_scope"]};
    return {needsEvidence,complete};
  };
  s.commit=(plan,proposed)=>{
    plan.attack_metadata_sources=UI.clone(proposed.attack_metadata_sources);
    const target=plan.manual_template.hits.find(h=>h.id===profile.prototype_hit_id),next=proposed.manual_template.hits.find(h=>h.id===target.id);
    delete target.element;delete target.multiplier;if(Object.hasOwn(next,"element"))target.element=next.element;
    if(Object.hasOwn(next,"multiplier"))target.multiplier=next.multiplier;
    for(const key of ["hit_parameter_sources","elemental_sources"])if(Object.hasOwn(proposed,key))plan[key]=UI.clone(proposed[key]);
    if(plan.mechanics_model?.elemental_model)plan.mechanics_model.elemental_model.profiles=UI.clone(proposed.mechanics_model.elemental_model.profiles);
  };
  s.validate=()=>{
    if(removing){if(!restoreElement)throw Error("移除前请明确选择原型元素；不会从旧计算结果自动补回。");return;}
    selectionValid();if(!row.source.trim())throw Error("请填写用途与附着模式依据；不确定时选择待核实。");
    const mode=AM.mode(row);if(mode==="sequence"&&!(row.counter_tag||"").trim())throw Error("有限序列模式必须声明附着计数标签（ICD Tag）。");
    if(mode==="pending"&&row.status!=="pending")throw Error("待核实附着模式必须保持待核实状态。");
    if(readSelection!==selector())throw Error("请先读取当前技能的精确攻击候选，再预览或应用。");
    const selected=candidates.find(c=>c.candidate_id===row.candidate_id),identity=AM.identity(selected?.projection);
    if(mode==="sequence"&&identity.status==="ready"&&row.counter_tag.trim()!==identity.tag)throw Error(`计数标签必须与数据库确认的 attenuationTag（${identity.tag}）一致；不能用 attackTag 或动作类别替代。`);
    const {needsEvidence,complete}=syncVerification();
    if(needsEvidence&&row.status==="confirmed"&&!complete)throw Error("已确认的外部结论必须填写证据定位、适用版本、复核日期、具体攻击分量与共享范围。");
  };
  s.validateReport=(report,proposed)=>{
    const expected=proposed.attack_metadata_sources||[],records=AM.records(report);
    if(expected.length!==records.length||expected.some(source=>!records.some(r=>r.action_event_profile_id===source.action_event_profile_id)))throw Error("接口没有返回完整攻击来源回执，未应用。");
  };
  drawCandidates();drawMode();AE.controls(s,profileId);
};
