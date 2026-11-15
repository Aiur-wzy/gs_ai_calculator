"use strict";

// Reviewed action admission: discovery is read-only; values appear only after
// an exact reference enters a fresh server preview.
window.AA={pageSize:50,results:new WeakMap()};
AA.bindings=()=>PL.state.plan?.action_admission_sources||[];
AA.bound=id=>AA.bindings().some(row=>row.ability_id===id);
AA.context=()=>({contract:"static-action-admission-context-v1",mode:"ordinary_open_world",
  external_cooldown_effects:"none",external_energy_cost_effects:"none"});
AA.groups=[
  {key:"exact",label:"已审核 · 完全匹配",open:true,selectable:true},
  {key:"partial",label:"已审核 · 部分匹配",open:true,selectable:true},
  {key:"pending",label:"待审核或待处理",open:false},
  {key:"revoked",label:"已撤销",open:false},
  {key:"stale",label:"资料已变化",open:false},
  {key:"conflict",label:"当前模型冲突",open:false},
  {key:"not_evaluated",label:"尚未评估或不匹配",open:false},
];
AA.group=item=>AA.groups.some(group=>group.key===item?.catalog_group)?item.catalog_group:"not_evaluated";
AA.selectable=item=>["exact","partial"].includes(AA.group(item))&&item?.review?.status==="approved"&&
  item?.selection_status==="reference_selectable_for_fresh_preview"&&item?.execution_authority===false;
AA.reason=value=>({review_not_approved:"当前修订尚未审核通过",review_revoked:"当前修订已撤销",
  snapshot_or_evidence_changed:"模型绑定的快照或证据已变化",competing_current_models:"同一动作门禁存在多个当前模型",
  declared_input_mismatch:"模型与当前typed输入不匹配",model_contract_invalid:"模型合同无效"}[value]||"状态需查看原始记录");
AA.applySelection=(row,item)=>{
  if(!AA.selectable(item)||!item.model_ref||!item.target||!item.domain)throw Error("只能选择已审核且当前完全或部分匹配的动作接纳模型。");
  if(!Array.isArray(item.domain.constellations)||!item.domain.constellations.includes(0))throw Error("当前首片只接受C0模型。");
  row.model_ref=UI.clone(item.model_ref);
  row.selection={character_id:item.target.character_id,skill_depot_id:item.target.skill_depot_id,
    skill_id:item.target.skill_id,slot:item.target.slot,constellation:0};
  row.accepted_fingerprint=null;
  return row;
};
AA.catalogError=response=>{
  if(["action_admission_catalog_changed","snapshot_changed"].includes(response?.reason))return "动作接纳目录在读取期间已更新，请从第一页重新读取。";
  if(response?.reason==="action_admission_catalog_cursor")return "动作接纳目录分页已失效，请从第一页重新读取。";
  return response?.message||"动作接纳目录读取失败。";
};
AA.catalogBrowser=(holder,options)=>{
  let active=true,serial=0;
  const fail=response=>{holder.innerHTML="";const error=UI.el("section",null,"capacity-catalog-error");error.setAttribute("role","alert");
    error.append(UI.el("strong","动作接纳目录无法使用"),UI.el("p",AA.catalogError(response)));
    if(response?.details)error.append(DS.raw("错误定位",response.details));holder.append(error);};
  const draw=(items,total,counts)=>{holder.innerHTML="";holder.className="capacity-catalog";
    holder.append(UI.el("p",`目录共 ${total} 项，已完整读取 ${items.length} 项。目录不包含可执行冷却或耗能值。`,"capacity-catalog-status"));
    for(const group of AA.groups){const rows=items.filter(item=>AA.group(item)===group.key),section=UI.el("details",null,`capacity-catalog-group capacity-catalog-${group.key}`);
      section.open=group.open;section.append(UI.el("summary",`${group.label}（${rows.length}）`));
      if(!rows.length)section.append(UI.el("p","当前分组没有项目。","wb-note"));
      for(const item of rows){const ref=item.model_ref||{},target=item.target||{},display=item.display||{},card=UI.el("article",null,"capacity-catalog-card");
        card.append(UI.el("strong",`${display.skill?.value||"技能名称待补"} · ${ref.id||"模型ID待补"} / 修订 ${ref.revision??"待补"}`),
          UI.el("p",`角色：${display.character?.value||target.character_id||"待补"}；动作：元素爆发 · 默认形态；状态：${group.label}。`));
        if(item.status_reason)card.append(UI.el("p",AA.reason(item.status_reason),group.key==="exact"||group.key==="partial"?"hint":"eq-error"));
        if(group.selectable&&AA.selectable(item)){const choose=UI.button("选择此模型",()=>options.onSelect(item));choose.setAttribute("aria-label",`选择动作接纳模型 ${ref.id||"未知ID"}`);card.append(choose);}
        const advanced=UI.el("details",null,"capacity-catalog-advanced");advanced.append(UI.el("summary","高级详情与原始 ID"),
          UI.el("p",`模型 ID：${ref.id||"待补"}；修订：${ref.revision??"待补"}；角色 ID：${target.character_id??"待补"}；技能组 ID：${target.skill_depot_id??"待补"}；技能 ID：${target.skill_id??"待补"}。`),
          DS.raw("完整只读目录记录",item));card.append(advanced);section.append(card);}
      holder.append(section);}
    holder.append(UI.el("p",`分组计数：完全匹配 ${counts.exact??0}，部分匹配 ${counts.partial??0}，待处理 ${counts.pending??0}，撤销 ${counts.revoked??0}，陈旧 ${counts.stale??0}，冲突 ${counts.conflict??0}，未评估 ${counts.not_evaluated??0}。`,"hint"));
  };
  const countMeta=page=>{const counts=page?.counts;
    if(!counts||typeof counts!=="object"||Array.isArray(counts)||!Number.isSafeInteger(counts.filtered_models)||counts.filtered_models<0)return null;
    if(Object.hasOwn(counts,"returned")&&counts.returned!==page.items.length)return null;
    if(Object.entries(counts).some(([,value])=>!Number.isSafeInteger(value)||value<0))return null;
    return {total:counts.filtered_models,key:JSON.stringify(Object.entries(counts).filter(([key])=>key!=="returned").sort(([a],[b])=>a.localeCompare(b))),counts};};
  const reload=async()=>{const attempt=++serial;holder.innerHTML="";const loading=UI.el("p","正在读取全部动作接纳目录…","capacity-catalog-status");loading.setAttribute("role","status");holder.append(loading);
    let cursor=null,total=null,countKey=null,items=[],counts=null;const cursors=new Set(),identities=new Set();
    try{do{if(!active||attempt!==serial||!options.current())return false;
      const response=await callApi("action_admission_model_candidates",options.document(),options.actor(),options.ability(),cursor,16,"all");
      if(!active||attempt!==serial||!options.current())return false;if(response.code){fail(response);return false;}
      const page=response.data;if(!page||!Array.isArray(page.items)||typeof page.has_more!=="boolean"){fail({message:"动作接纳目录响应结构无效。"});return false;}
      const meta=countMeta(page);if(!meta){fail({message:"动作接纳目录计数元数据无效。"});return false;}
      if(total===null){total=meta.total;countKey=meta.key;counts=meta.counts;}else if(meta.total!==total||meta.key!==countKey){fail({message:"动作接纳目录分页计数不一致。"});return false;}
      for(const item of page.items){const key=`${item?.model_ref?.id}\u0000${item?.model_ref?.revision}`;if(identities.has(key)){fail({message:"动作接纳目录返回重复模型身份。"});return false;}identities.add(key);items.push(item);}
      if(page.has_more){if(!page.items.length||typeof page.next_cursor!=="string"||!page.next_cursor||page.next_cursor===cursor||cursors.has(page.next_cursor)){fail({message:"动作接纳目录分页游标无效。"});return false;}cursors.add(page.next_cursor);cursor=page.next_cursor;}
      else{if(page.next_cursor!==null){fail({message:"动作接纳目录末页游标无效。"});return false;}cursor=null;}
    }while(cursor!==null);
    if(total!==items.length){fail({message:`动作接纳目录计数不一致：应有 ${total} 项，实际读取 ${items.length} 项。`});return false;}
    draw(items,total,counts||{});return true;
    }catch(error){if(active&&attempt===serial&&options.current())fail({message:`动作接纳目录读取失败：${error.message}`});return false;}};
  return {reload,invalidate(){serial++;holder.innerHTML="";},dispose(){active=false;serial++;holder.innerHTML="";}};
};
AA.preview=(box,record,clear=true)=>{
  if(clear)box.innerHTML="";
  if(!record){box.append(UI.el("p","请选择模型，再读取fresh预览。"));return;}
  const profile=record.profile||{},values=record.compiled_values||profile.compiled_values||{},cooldown=values.cooldown||{},energy=values.energy||{},resource=profile.resource_profile||{};
  box.append(UI.el("h4",`动作门禁预览 · ${record.status==="ready"?"已确认":"待确认"}`),
    UI.el("p",`来源：${record.model_ref?.id||"待补"} / 修订 ${record.model_ref?.revision??"待补"}；绑定能力 ${record.ability_id||"待补"}。`),
    UI.el("p",`基础冷却：${Number.isFinite(cooldown.duration)?`${cooldown.duration} 秒`:"待核实"}；基础次数：${cooldown.charges??"待核实"}；冷却起点：${cooldown.start==="action_start"?"动作开始":"待核实"}；恢复方式：${cooldown.recovery==="parallel"?"独立恢复":"待核实"}。`),
    UI.el("p",`元素爆发耗能门槛与扣除：${Number.isFinite(energy.amount)?energy.amount:"待核实"}；交付时点：${energy.delivery==="action_start"?"动作开始":"待核实"}。`),
    UI.el("p","这些值来自选定模型后的fresh预览；目录本身没有返回它们，也没有执行权。"));
  const descriptions=resource.descriptions;
  if(descriptions)box.append(UI.details("数据库技能原文",descriptions));
  if(profile.declared_not_verified?.length)box.append(UI.el("p",`仍未验证：${profile.declared_not_verified.join("、")}。`,"hint"));
  for(const issue of record.issues||[])box.append(UI.el("p",issue.message||"存在待补输入","eq-error"));
  box.append(DS.raw("完整fresh动作接纳预览",record));
};
AA.request=(state,accepted)=>({contract:"action-admission-plan-preview-request-v1",actor_id:state.actor,
  ability_id:state.ability,binding_id:state.row.id,model_ref:UI.clone(state.row.model_ref),selection:UI.clone(state.row.selection),
  context:UI.clone(state.context),initial_ready_in:UI.clone(state.initial),takeover:state.takeover,
  accepted_fingerprint:accepted});
AA.edit=index=>{
  if(PL.state.draft){setStatus("请先应用或放弃装备草稿");return;}const original=PL.state.plan;
  if(original?.plan_version!==3){setStatus("受审动作接纳需要原生版本3方案");return;}
  const abilities=(original.rotation_model?.abilities||[]).filter(a=>a.action_type_id==="action.elemental_burst"&&a.variant==="default"),existing=AA.bindings()[index];
  const first=existing?abilities.find(a=>a.id===existing.ability_id):abilities.find(a=>!AA.bound(a.id));
  if(!first){setStatus("请先建立default元素爆发能力与实际动作");return;}
  const gear=original.equipment_inputs?.[first.character],charId=gear?.character_ref?.db_character_id;
  const loneInitial=(original.rotation_model.initial_cooldowns||[]).filter(row=>row.character===first.character);
  const state={actor:first.character,ability:first.id,row:existing?UI.clone(existing):{id:UI.id("admission"),ability_id:first.id,version_line:"release",
      selection:{character_id:charId??null,skill_depot_id:null,skill_id:null,slot:"burst",constellation:0},variant:"default",model_ref:{id:"",revision:1},accepted_fingerprint:null},
    takeover:false,context:original.action_admission_context?UI.clone(original.action_admission_context):null,
    initial:loneInitial.length===1?UI.clone(loneInitial[0].ready_in):null};
  let closed=false,revision=0,request=0,shown=null,browser=null,latest=null;
  const baseline=PL.currentInputJson(),epoch=RC.sourceEpoch(),path=RT.state.path,sha=PL.state.fileSha;
  const current=()=>!closed&&PL.state.plan===original&&PL.currentInputJson()===baseline&&RC.sourceEpoch()===epoch&&RT.state.path===path&&PL.state.fileSha===sha&&!PL.state.draft;
  const form=UI.el("section"),selected=UI.el("section"),scene=UI.el("section"),catalog=UI.el("section"),errors=UI.el("section"),preview=UI.el("section");
  const invalidate=()=>{if(closed)return true;closed=true;request++;browser?.dispose();return true;};
  const dialog=window.AppDialogKernel?.create?.({title:"受审动作接纳来源",
    description:"先选模型引用，再进行 fresh 预览。目录不返回冷却或耗能值；确认指纹也不会从目录生成。",
    className:"capacity-catalog-dialog",onRequestClose:invalidate})||null;
  if(!dialog){setStatus("当前还有窗口正在处理，请先完成或关闭后再编辑动作接纳来源");return;}
  const close=()=>{invalidate();return dialog.finish?.("cancel")??true;};
  const identityReady=()=>typeof state.row.model_ref?.id==="string"&&state.row.model_ref.id&&Number.isInteger(state.row.model_ref.revision)&&Number.isInteger(state.row.selection?.character_id)&&Number.isInteger(state.row.selection?.skill_depot_id)&&Number.isInteger(state.row.selection?.skill_id);
  const clearIdentity=()=>{state.row.model_ref={id:"",revision:1};state.row.selection={character_id:original.equipment_inputs?.[state.actor]?.character_ref?.db_character_id??null,skill_depot_id:null,skill_id:null,slot:"burst",constellation:0};state.row.accepted_fingerprint=null;shown=null;latest=null;};
  const changed=(refresh=false)=>{revision++;request++;shown=null;latest=null;state.row.accepted_fingerprint=null;preview.innerHTML="";errors.innerHTML="";renderSelected();if(refresh){browser.invalidate();browser.reload();}};
  const renderSelected=()=>{selected.innerHTML="";selected.append(UI.el("h4","当前模型引用"),UI.el("p",identityReady()?`${state.row.model_ref.id} / 修订 ${state.row.model_ref.revision}；技能 ID ${state.row.selection.skill_id}`:"尚未从目录选择模型。"));};
  const renderScene=()=>{scene.innerHTML="";scene.append(UI.el("h4","动作门禁场景"),UI.el("p",state.context?"普通开放世界；无外部减冷却和耗能修改。":"场景未确认；3A默认保持空。","capacity-scene-status"),
    UI.button("使用普通开放世界预设",()=>{state.context=AA.context();changed();renderScene();}),
    UI.button("清除场景",()=>{state.context=null;changed();renderScene();}));};
  const renderForm=()=>{form.innerHTML="";form.append(UI.el("h4","能力与开场状态"));
    form.append(UI.field("元素爆发能力",UI.select(abilities.map(a=>({value:a.id,label:`${RC.abilityName(a)} · ${a.id}`})),state.ability,value=>{
      const ability=abilities.find(a=>a.id===value);state.ability=value;state.actor=ability.character;state.row.ability_id=value;clearIdentity();state.initial=null;changed(true);renderForm();})));
    form.append(UI.check("允许受审动作接纳接管现有冷却、耗能、旧资源及手工扣能",state.takeover,value=>{state.takeover=value;changed();}));
    form.append(UI.check("明确开场冷却全部就绪",Array.isArray(state.initial)&&state.initial.length===0,value=>{state.initial=value?[]:null;changed();renderForm();}));
    form.append(UI.field("若开场未恢复，剩余秒数（空白表示未知）",UI.input(state.initial?.[0]??"",value=>{state.initial=value===""?null:[Number(value)];changed();},true)));
  };
  const catalogDocument=()=>{const plan=UI.clone(original);plan.action_admission_sources=(plan.action_admission_sources||[]).filter((_row,i)=>i!==index);if(identityReady())plan.action_admission_sources.push(UI.clone(state.row));if(!plan.action_admission_sources.length)delete plan.action_admission_sources;return plan;};
  browser=AA.catalogBrowser(catalog,{document:catalogDocument,actor:()=>state.actor,ability:()=>state.ability,current,onSelect:item=>{AA.applySelection(state.row,item);changed();setStatus("已选择模型引用；尚未写入方案，也未取得执行权。");}});
  const run=async mode=>{if(!current()){setStatus("方案已变化，请重新打开动作接纳编辑");return;}errors.innerHTML="";
    if(!identityReady()){errors.append(UI.el("p","请先从动作接纳目录选择模型。","eq-error"));return;}
    if(mode==="confirm"&&!shown){errors.append(UI.el("p","请先读取fresh预览并核对。","eq-error"));return;}
    const accepted=mode==="preview"?null:mode==="confirm"?shown:state.row.accepted_fingerprint;
    const attempt=++request,version=revision,valid=()=>attempt===request&&version===revision&&current();let response;
    try{response=await callApi("action_admission_preview",UI.clone(original),AA.request(state,accepted));}
    catch(error){if(valid())errors.append(UI.el("p",`读取失败：${error.message}`,"eq-error"));return;}
    if(!valid()){setStatus("旧动作接纳响应已忽略，请重新读取");return;}if(response.code){errors.append(UI.error(response));return;}
    const data=response.data,record=data.compilation,fp=record?.required_confirmation?.accepted_fingerprint;
    AA.preview(preview,record);latest=data;const fresh=typeof fp==="string"&&/^[0-9a-f]{64}$/.test(fp)?fp:null;
    if(mode==="preview"){shown=fresh;return;}
    if(mode==="confirm"){if(!fresh||fresh!==shown||record.status!=="ready"){shown=fresh;state.row.accepted_fingerprint=null;errors.append(UI.el("p","来源尚未就绪或已变化，请核对新预览后再次确认。","eq-error"));return;}state.row.accepted_fingerprint=fresh;setStatus("已确认fresh动作门禁来源；应用时仍会再次核验。");return;}
    if(!fresh||record.status!=="ready"||state.row.accepted_fingerprint!==fresh){errors.append(UI.el("p","当前来源未确认或已变化；请重新预览并确认。","eq-error"));return;}
    const proposal=latest.proposal;for(const key of ["action_admission_sources","action_resource_sources","action_capacity_sources","rotation_model"])original[key]=UI.clone(proposal[key]);
    original.action_admission_context=UI.clone(proposal.action_admission_context);close();RC.touch();setStatus("动作接纳来源已应用；请重新计算并查看运行时接纳回执。");
  };
  renderForm();renderSelected();renderScene();dialog.appBody.append(UI.button("← 返回动作与能量",close),form,selected,scene,
    UI.el("h4","动作接纳模型目录"),UI.button("重新读取全部目录",()=>browser.reload()),catalog,errors,preview,
    UI.button("读取fresh预览",()=>run("preview")),UI.button("确认所示来源",()=>run("confirm")),UI.button("应用动作接纳",()=>run("apply")),UI.button("返回且不应用",close));
  dialog.focusInitial?.();browser.reload();
};
AA.render=box=>{if(PL.state.plan?.plan_version!==3)return;const bindings=AA.bindings(),section=UI.el("details",null,"result-block");
  section.append(UI.el("summary",`受审动作门禁来源（${bindings.length}）`),
    UI.el("p","模型把数据库基础资源与受审动作语义编译为门禁；只有运行时通过前台、占用、能量和冷却检查的动作才获得接纳证书。"),
    UI.el("p",bindings.length&&!PL.state.plan.action_admission_context?"场景未确认；当前绑定只能保存和预览。":bindings.length?"已显式选择普通开放世界场景。":"尚未绑定动作门禁。","capacity-scene-status"),
    UI.button("绑定元素爆发动作门禁",()=>AA.edit()));
  bindings.forEach((row,index)=>section.append(UI.el("p",`${RC.abilityName(RC.model()?.abilities.find(a=>a.id===row.ability_id)||{})} · ${row.model_ref.id} / 修订 ${row.model_ref.revision}`),UI.button("核对或修改",()=>AA.edit(index))));box.append(section);};
AA.renderResult=(box,result,template)=>{const prior=AA.results.get(box);if(prior){prior();AA.results.delete(box);}const records=result?.action_admission_compilations||[],check=result?.rotation_check||{},audit=check.action_admission_execution;
  if(!records.length&&!audit)return;const section=UI.el("details",null,"result-block");section.append(UI.el("summary","静态动作来源与运行时接纳"));
  for(const record of records){const detail=UI.el("details");detail.append(UI.el("summary",`静态来源 ${record.id} · ${record.status==="ready"?"就绪":"待处理"}`));AA.preview(detail,record,false);section.append(detail);}
  if(audit){section.append(UI.el("p",`受管动作 ${audit.reported_actions??"待核实"} 个：接纳 ${audit.committed_actions??"待核实"}，游戏门禁拒绝 ${audit.rejected_actions??"待核实"}，提交失败 ${audit.not_committed_actions??"待核实"}，后续未验证 ${audit.not_verified_actions??"待核实"}。`));
    const rows=UI.el("div");for(const event of check.event_log||[]){const admission=event.action_admission;if(!admission)continue;const detail=UI.el("details");
      detail.append(UI.el("summary",`${event.time??event.t??"待核实"} 秒 · ${DS.character(event.character,template)} · ${event.action_id||event.id||"动作"} · ${{committed:"已接纳",rejected:"门禁拒绝",not_committed:"未提交",not_verified:"后续未验证"}[admission.status]||"待核实"}`),
        UI.el("p",`来源绑定：${admission.binding_id||"待核实"}；原因：${admission.reason||"无"}。`),DS.raw("完整动作接纳父记录",event));rows.append(detail);}section.append(rows,DS.raw("运行时动作接纳总表",audit));}
  box.append(section);AA.results.set(box,()=>{section.innerHTML="";section.remove();});};
