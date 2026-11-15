"use strict";
window.PL = {state: {plan:null, draft:null, report:null, caps:null, request:0, draftRevision:0,
  fileSha:null, savedJson:null, sourceEpoch:0, opening:false}};
PL.input = (plan = PL.state.plan) => { if (!plan) return RT.tpl();
  const copy = UI.clone(plan); delete copy.resolution; delete copy.resolved_template; return copy; };
PL.inputText = value => window.JSONTransport ? JSONTransport.stringify(value) : JSON.stringify(value);
// All user open paths share this one effect sequence. The fence covers both
// editors, including unsaved equipment input; view-only state is excluded.
window.AppDocumentOpenIntents=GenshinBusinessUI.createDocumentOpenIntents(()=>({
  board:window.WB?.state?.board,boardJson:window.WB?.json?.(),boardPath:window.WB?.state?.path,boardSha:window.WB?.state?.fileSha,
  boardRequest:window.WB?.state?.request,boardDraft:PL.inputText(window.WB?.state?.equipmentDrafts||{}),
  boardEditor:window.WB?.state?.editorSession?.json?.(),
  plan:PL.state.plan,template:RT.tpl(),input:PL.currentInputJson(),draft:PL.state.draft?PL.inputText(PL.input(PL.state.draft)):null,
  planPath:RT.state.path,planSha:PL.state.fileSha,resolvedSha:RT.state.fileSha,editorRevision:RT.state.revision,request:PL.state.request,sourceEpoch:PL.state.sourceEpoch,
  owner:document.getElementById?.("genshin-legacy-editor"),ownerConnected:document.getElementById?.("genshin-legacy-editor")?.isConnected,
}));
window.addEventListener("pagehide",()=>AppDocumentOpenIntents.invalidate());
PL.currentInputJson = () => PL.inputText(PL.state.plan ? PL.input() : RT.tpl());
PL.renameCharacter = (oldId, newId) => {
  const plan=PL.state.plan;if(!plan)return;
  if(plan.equipment_inputs[oldId]){plan.equipment_inputs[newId]=plan.equipment_inputs[oldId];delete plan.equipment_inputs[oldId];}
  const walk=value=>{if(!value||typeof value!=="object")return;
    for(const [key,v] of Object.entries(value)){
      if(["character_id","provider_character_id","target_character_id","character","target"].includes(key)&&v===oldId)value[key]=newId;
      else if(typeof v==="object")walk(v);
    }};
  walk(plan.effect_bindings);walk(plan.manual_effect_dependencies);
  for(const field of ["passive_sources","mechanism_sources"])
    for(const source of plan[field]||[])if(source.actor===oldId){source.actor=newId;source.accepted_fingerprint=null;}
  for(const buff of plan.manual_template.buffs||[])if(buff.extensions?.owner===oldId)buff.extensions.owner=newId;
  if(plan.rotation_model){const model=plan.rotation_model;
    for(const c of model.characters||[])if(c.id===oldId)c.id=newId;
    if(model.initial_frontline===oldId)model.initial_frontline=newId;
    walk(model.abilities);walk(model.initial_cooldowns);walk(model.energy_recharge_windows);walk(model.events);
    for(const a of model.actions||[])if(a.switch_to===oldId)a.switch_to=newId;
    for(const e of model.events||[])if(e.amounts&&Object.hasOwn(e.amounts,oldId)){e.amounts[newId]=e.amounts[oldId];delete e.amounts[oldId];}
  }
  if(plan.mechanics_model){const rename=value=>{if(!value||typeof value!=="object")return;
    for(const [key,v] of Object.entries(value)){
      if(key==="owner"&&v===oldId)value[key]=newId;
      else if(["characters","beneficiaries"].includes(key)&&Array.isArray(v))value[key]=v.map(id=>id===oldId?newId:id);
      else if(["amounts","quota"].includes(key)&&v&&Object.hasOwn(v,oldId)){v[newId]=v[oldId];delete v[oldId];}
      if(typeof v==="object")rename(v);
    }};rename(plan.mechanics_model);
    const attributes=plan.mechanics_model.attribute_model;
    if(attributes){
      for(const basis of attributes.initial_basis||[])if(basis.actor===oldId)basis.actor=newId;
      const components=[...(attributes.effect_classifications||[]),
        ...(attributes.initial_basis||[]).flatMap(basis=>basis.included_components||[])];
      for(const row of components)if(row.component?.provider===oldId)row.component.provider=newId;
      if(plan.attribute_review)plan.attribute_review.context_fingerprint=null;
    }
  }
};
PL.draftChanged = () => !!PL.state.draft && PL.inputText(PL.input(PL.state.draft)) !== PL.state.draftInitialJson;
PL.dirty = () => PL.draftChanged() || (RT.tpl() && PL.state.savedJson !== PL.currentInputJson());
PL.canCalculate = () => !PL.draftChanged() && (!PL.state.plan || PL.state.plan.resolution?.state === "ready");
PL.manualCopy = async () => {
  if(PL.state.plan?.external_damage_supplements?.length){setStatus("含外部伤害补充的方案必须保留原生记录及复核依据，不能生成会丢失补充账的手工数值副本。请使用原生方案或完整交付。");return;}
  if(!PL.state.plan||!PL.canCalculate()){setStatus("请先应用草稿并完善方案");return;}
  if(!window.confirm("生成整方案的手工数值副本？该副本冻结当前装备、面板和派生效果，后续装备不会驱动它；原方案文件保留。"))return;
  const json=PL.currentInputJson(),request=++PL.state.request;
  const r=await callApi("run_plan_calc",UI.clone(PL.state.plan));
  if(request!==PL.state.request||json!==PL.currentInputJson())return;if(r.code){UI.error(r);return;}
  const template=UI.clone(r.data.plan.resolved_template);
  template.meta ||= {};template.meta.name=`${template.meta.name||"方案"} · 手工数值副本`;
  (template.meta.assumptions ||= []).push("整方案静态面板与派生效果冻结为手工数值副本，装备后续变化不驱动本副本。");
  PL.state.plan=null;PL.state.fileSha=null;PL.state.savedJson=null;PL.state.report=null;PL.state.appliedSummary=null;EQ.lastReport=null;
  RT.replaceTemplate(template,"");RT.renderAll();PL.renderStatus();setStatus("已打开未保存的手工数值副本，原方案文件保留");
};
PL.onEdit = () => { if (PL.state.plan && !PL.state.opening) {
  PL.state.plan.revision += 1; PL.state.plan.resolution = {state:"pending"};
  PL.state.plan.resolved_template = null; PL.state.appliedSummary=null; } PL.renderStatus(); };
PL.renderStatus = () => { const n = $("#plan-status"); if (!n) return;
  n.textContent = `${PL.state.plan ? `装备方案 · ${DS.label("states", PL.state.plan.resolution?.state || "pending")}` : "手工模板"}`
    + (PL.draftChanged() ? " · 装备草稿未应用（仅此会话，尚未写入文件）" : PL.dirty() ? " · 未保存" : " · 已保存");
  if(PL.draftChanged())n.append(UI.button("继续编辑装备草稿",()=>PL.begin(EQ.characterId),"mini")); window.WS?.refresh?.(); window.AgentWorkbenchApp?.documentChanged?.(); };
PL.dialog=(title,description)=>{const dialog=window.AppDialogKernel?.create?.({title,description,className:"plan-dialog",onRequestClose:reason=>reason!=="replaced"})||null;
  if(!dialog){const message="已有窗口正在处理或保留未确认内容，请先完成或关闭该窗口后再试。";
    if(window.WS?.report)WS.report({factKey:"plan.dialog.blocked",kind:"warning",message,scope:"local",projectToFooter:false});else setStatus(message);}return dialog;};
PL.confirmLeave = () => { if (!PL.dirty()) return true;
  return window.confirm("当前输入尚未保存。确定放弃这些修改并继续？\n如需保存，请取消并点击保存。"); };
PL.leave = (options={}) => { if (!PL.dirty()) return Promise.resolve(true);
  return new Promise(resolve=>{const dialog=PL.dialog("尚有未保存输入",PL.draftChanged()?"存在尚未应用的装备草稿。选择保存时将明确选择如何处理草稿。":"保存成功后继续，或明确放弃本次修改。");
    if(!dialog){resolve(false);return;}let settled=false;const done=value=>{if(settled)return false;settled=true;dialog.destroy?.("plan-leave-decision");resolve(value);return true;};
    dialog.onClosed?.(()=>done(false));
    dialog.appBody.append(UI.button("保存后继续",async()=>{if(await RT.saveTemplate()&&!PL.dirty())done(!options.openIntent||options.openIntent.acceptSavedIdentity());}),
      UI.button("放弃修改",()=>done(true)),UI.button("继续编辑",()=>done(false)));
    if(!dialog.onClosed)dialog.addEventListener("cancel",()=>done(false));}); };
PL.requestClose = () => { if(window.AgentWorkbenchApp?.ready)return window.AgentWorkbenchApp.requestClose();if (!PL.dirty()) return true;
  if (!PL.state.closing) { PL.state.closing=true;PL.leave().then(ok=>{PL.state.closing=false;if(ok)callApi("confirm_close");}); }
  return false; };
PL.replace = (plan, path = "", sha = null, saved = false, summary = null) => {
  PL.state.request++; PL.state.opening = true; PL.state.draft = null;
  PL.state.plan = plan; PL.state.fileSha = sha; PL.state.report = null; EQ.lastReport = null; EQ.busy = false;
  PL.state.appliedSummary=summary;PL.state.draftInitialJson=null;
  RT.replaceTemplate(plan.manual_template, path);
  PL.state.opening = false; PL.state.savedJson = saved ? PL.currentInputJson() : null;
  EQ.closeView(); PL.renderStatus(); RT.renderAll();
};
PL.startNew = async () => { const intent=AppDocumentOpenIntents.begin();if (!await PL.leave({openIntent:intent})||!intent.current()) return;
  const r = await callApi("new_plan");
  if (!intent.current()) return; if (r.code) { UI.error(r); return; }
  PL.replace(r.data.plan || r.data); setStatus("已创建装备方案，请填写初始面板并选择武器与圣遗物套装"); };
PL.begin = async (characterId) => {
  if (!RT.tpl()) { setStatus("请先新建或打开方案"); return false; }
  if (PL.state.draft) { EQ.openView(characterId || EQ.characterId); return true; }
  let plan = PL.state.plan;
  if (!plan) { const revision = RT.state.revision; const r = await callApi("new_plan", UI.clone(RT.tpl()));
    if (revision !== RT.state.revision) { setStatus("方案已变化，请重新打开装备编辑"); return false; }
    if (r.code) { UI.error(r); return false; } plan = r.data.plan || r.data; }
  PL.state.draft = UI.clone(plan); PL.state.draftInitialJson=PL.inputText(PL.input(plan));
  EQ.confirmedGear?.clear();
  PL.state.draftRevision = 0; PL.state.report = null; EQ.lastReport = null;
  PL.state.draftBase = {revision:RT.state.revision, epoch:PL.state.sourceEpoch, path:RT.state.path, sha:PL.state.fileSha};
  PL.state.request++; EQ.captureContext(); EQ.openView(characterId || plan.manual_template.characters[0]?.id);
  PL.renderStatus(); return true;
};
PL.touch = () => { PL.state.draftRevision++; PL.state.request++; PL.state.report = null;
  EQ.busy = false;
  if (PL.state.draft) { PL.state.draft.resolution = {state:"pending"}; PL.state.draft.resolved_template = null; }
  PL.renderStatus(); EQ.renderPreview(); RS.invalidate(); };
PL.preview = async (update = false) => {
  const draft = PL.state.draft; if (!draft) return false;
  if(EQ.validateWorkbench&&!EQ.validateWorkbench())return false;
  const req = ++PL.state.request, json = JSON.stringify(draft), rev = PL.state.draftRevision;
  EQ.busy = true; EQ.renderPreview();
  const r = EQ.previewEdit ? await EQ.previewEdit(draft,update) : await callApi(update ? "apply_data_update" : "compose_equipment_plan", UI.clone(draft));
  if (req !== PL.state.request || rev !== PL.state.draftRevision || json !== JSON.stringify(PL.state.draft)) return false;
  EQ.busy = false;
  if (r.code) { EQ.error = r; EQ.renderPreview(); UI.error(r); return false; }
  EQ.error = null; PL.state.draft = r.data.plan; PL.state.report = r.data; EQ.lastReport = r.data;
  if(r.data.equipment_summary)r.data.equipment_summary.context.kind="draft";
  EQ.render(); return true;
};
PL.apply = async () => {
  if (!PL.state.draft) return true;
  if (PL.state.draftBase.revision !== RT.state.revision) { setStatus("原方案已变化，请返回查看当前输入，再明确放弃旧草稿并重新编辑"); return false; }
  const base=PL.state.draftBase;
  if(base.path&&base.sha){const req=++PL.state.request,draft=PL.state.draft;
    const disk=await callApi("inspect_plan_file",base.path);
    if(req!==PL.state.request||draft!==PL.state.draft)return false;
    if(disk.code||disk.data.file_sha!==base.sha){PL.sourceChanged();setStatus("磁盘方案已变化或无法核验；请重载或另存当前输入后重新核对，装备草稿仍保留。");return false;}}
  if (!await PL.preview()) return false;
  const plan = PL.state.draft; plan.revision += 1;
  const path = PL.state.plan ? RT.state.path : "", sha = PL.state.plan ? PL.state.fileSha : null;
  const saved = PL.state.savedJson;
  const summary=PL.state.report?.equipment_summary,context=EQ.returnContext;
  if(summary){summary.context.kind="applied";summary.context.revision=plan.revision;
    summary.context.input_fingerprint=null;}
  PL.replace(plan, path, sha,false,summary); PL.state.savedJson = saved;EQ.restoreContext(context);
  setStatus(plan.resolution?.state === "ready" ? "装备已应用，正在重新计算" : "已应用待完善方案，可保存并继续处理待办");
  const calculation=plan.resolution?.state === "ready" ? await RS.runCalc() : null;
  if(PL.state.plan===plan&&plan.resolution?.state!=="ready")EQ.showFollowup?.(plan,plan.resolution);
  else if(PL.state.plan===plan)EQ.reportCalculation?.(plan,calculation);
  return true;
};
PL.cancel = async () => {
  const base = PL.state.draftBase; PL.state.request++; PL.state.draft = null; PL.state.report = null;
  EQ.busy = false; EQ.error = null; EQ.lastReport = null; EQ.closeView(); PL.renderStatus();
  if (base?.epoch !== PL.state.sourceEpoch) PL.sourceChanged();
  if (base?.path && base.sha) { const r = await callApi("load_plan", base.path);
    if (RT.state.path === base.path && (!r.data || r.data.file_sha !== base.sha)) {
      PL.sourceChanged(); setStatus("磁盘方案已变化，请重载或另存；旧结果保持历史状态"); } }
  RS.renderAll();
};
PL.back = () => {
  if(!PL.draftChanged()){PL.state.request++;PL.state.draft=null;PL.state.report=null;EQ.busy=false;EQ.lastReport=null;}
  EQ.closeView();EQ.restoreContext();PL.renderStatus();RT.renderAll();RS.renderAll();
  if(PL.draftChanged())setStatus("已返回；装备草稿保留在本次会话，尚未应用或写入文件");
};
PL.discard = async () => {
  if(PL.draftChanged()&&!window.confirm("放弃本次尚未应用的装备修改？当前已应用方案和磁盘文件不变。"))return false;
  const context=EQ.returnContext;await PL.cancel();EQ.restoreContext(context);RT.renderAll();return true;
};
PL.sourceChanged = () => { PL.state.sourceEpoch++; PL.state.request++;
  if(window.RC){RC.invalidate();RC.render();}
  if (PL.state.plan) { PL.state.plan.resolution = {state:"pending"}; PL.state.plan.resolved_template = null; }
  if(PL.state.appliedSummary){PL.state.appliedSummary.context.state="pending";
    PL.state.appliedSummary.characters.forEach(c=>{if(["equipment","panel"].includes(c.input_mode))c.source_status="stale";});}
  PL.state.report = null; EQ.lastReport = null; EQ.busy = false; RS.invalidate(); PL.renderStatus(); };
PL.init = async () => { const r = await callApi("plan_capabilities");
  if (!r.code) PL.state.caps = r.data; else setStatus(r.message);
  $("#btn-new-plan").addEventListener("click", PL.startNew);
  $("#btn-equipment").addEventListener("click", () => PL.begin());
  $("#btn-manual-copy")?.addEventListener("click",PL.manualCopy);
  window.addEventListener("keydown",e=>{if(e.key!=="Escape"||e.defaultPrevented||document.querySelector("dialog[open]"))return;
    if(EQ.picking){e.preventDefault();EQ.returnFromData();}else if(EQ.showing){e.preventDefault();PL.back();}});
  $("#btn-resolved").addEventListener("click", async () => {
    if (!PL.state.plan || !PL.canCalculate()) { setStatus("当前方案未就绪或仍有装备草稿"); return; }
    const input=PL.currentInputJson(), plan=PL.state.plan;
    const name = RT.tpl().meta?.name || "plan";
    const selected=await WB.pickUserFile({action:"save",document_kind:"native",suggested_name:`${name}.resolved.json`});
    if(!selected||selected.cancelled)return;
    if(PL.state.plan!==plan||PL.currentInputJson()!==input){setStatus("方案已变化，请重新导出数值模板");return;}
    const r = await callApi("export_resolved_plan", UI.clone(plan), selected.path);
    if (r.code) UI.error(r); else setStatus("数值快照已导出", {path:r.data.path});
  });
  window.addEventListener("beforeunload", e => { if (PL.dirty()) { e.preventDefault(); e.returnValue = ""; } });
  PL.renderStatus();
};

const legacySave = RT.saveTemplate;
RT.refreshPlans = async () => { const r=await callApi("list_plan_details");if(r.code){setStatus(r.message);return;}
  RT.state.plansDir=r.data.dir;const sel=$("#plan-select");sel.innerHTML="";
  const empty=UI.el("option","— 方案库 —");empty.value="";sel.append(empty);
  r.data.items.forEach((item,i)=>{const name=DS.prose(item.display_name || item.name.replace(/(?:\.plan)?\.json$/i,""),`方案 ${i+1}（中文名待补）`);
    const option=UI.el("option",`${name} · ${item.error?"文件错误":item.kind==="genshin-whiteboard"?"创作白板":item.kind?"装备方案":"手工模板"}`);
    option.value=item.name;sel.append(option);}); };
RT.loadTemplate = async (path,options={}) => {
  const intent=options.openIntent||AppDocumentOpenIntents.begin(),current=()=>intent.current(),stale=()=>{setStatus("打开期间当前输入或文件身份已变化，未替换文档；请重新打开。");return false;};
  if(!current())return stale();
  const kind = await callApi("inspect_plan_file", path); if (!current()) return stale();
  if (kind.code) { UI.error(kind); return; }
  if(kind.data.kind==="genshin-candidate-input-draft"){
    const opener=window.GenshinCandidateDraftFiles;
    if(!opener){setStatus("请在支持原生 AI 的客户端中打开候选输入草稿；正式文档未改变。");return;}
    const opened=await opener.openSelected(path,kind.data.file_sha,{isCurrent:current});setStatus(opened.message);return opened.opened;
  }
  if (!await PL.leave({openIntent:intent})) return false;
  if(!current())return stale();
  if (kind.data.kind === "genshin-whiteboard") {
    if (!window.WB) { setStatus("当前界面不支持创作白板，请使用新版客户端"); return; }
    return await WB.load(path,{openIntent:intent});
  }
  if (kind.data.kind) { const r = await callApi("load_plan", path); if (!current()) return stale();
    if (r.code) { UI.error(r); return; } PL.replace(r.data.plan, r.data.path, r.data.file_sha, true,r.data.report?.equipment_summary);
    setStatus("已加载装备方案");return true; }
  else { const r=await callApi("load_template",path);if(!current())return stale();
    if(r.code){UI.error(r);return;}
    PL.state.plan = null; PL.state.draft = null; PL.state.fileSha = null;PL.state.appliedSummary=null;
    RT.replaceTemplate(r.data.template,r.data.path,r.data.file_sha??null);PL.state.savedJson = PL.currentInputJson();
    EQ.closeView();PL.renderStatus();RT.renderAll();setStatus("已加载数值快照：装备来源未附带",{path:r.data.path});return true; }
};
PL.chooseDraftSave = () => new Promise(resolve=>{const dialog=PL.dialog("装备草稿尚未应用","选择写入文件的内容。仅保存当前方案时，草稿继续保留在本次会话。");
  if(!dialog){resolve("cancel");return;}let settled=false;const done=value=>{if(settled)return false;settled=true;dialog.destroy?.("plan-draft-decision");resolve(value);return true;};
  dialog.onClosed?.(()=>done("cancel"));
  dialog.appBody.append(UI.button("应用草稿并保存",()=>done("apply")),UI.button("仅保存当前已应用方案",()=>done("applied")),
    UI.button("继续编辑",()=>done("cancel")));if(!dialog.onClosed)dialog.addEventListener("cancel",()=>done("cancel"));});
RT.saveTemplate = async (options = {}) => {
  if (PL.draftChanged()) { const choice=await PL.chooseDraftSave();if(choice==="cancel")return false;
    if(choice==="apply"&&!await PL.apply())return false; }
  if (!PL.state.plan) { const template=RT.tpl(),json=PL.currentInputJson(),saving=legacySave(options),request=RT.state.saveRequest;
    if (!await saving || request!==RT.state.acceptedSaveRequest || RT.tpl()!==template || PL.state.plan) return false;
    PL.state.savedJson = json; PL.renderStatus(); return true; }
  let path = $("#tpl-path").value.trim();
  if (!path) path = `${RT.state.plansDir}/${RT.tpl().meta?.name || "新装备方案"}.plan.json`;
  const chatSnapshot=window.AgentWorkbenchApp?.beforeDocumentSave?.("native"),source=PL.state.plan,request=++RT.state.saveRequest;
  const plan = UI.clone(source), json = PL.currentInputJson();
  const sha = path === RT.state.path ? PL.state.fileSha : null;
  const r = await callApi("save_plan", path, plan, sha);
  if (r.code) { UI.error(r); return false; }
  options.onWritten?.({path:r.data.path,fileSha:r.data.file_sha,current:PL.state.plan===source&&json===PL.currentInputJson()});
  if (request<RT.state.acceptedSaveRequest || PL.state.plan!==source) {
    setStatus("所提交的装备方案副本已保存，当前文档已切换或已有更新的保存结果，未改动当前文件路径。",{path:r.data.path});
    return false;
  }
  RT.state.acceptedSaveRequest=request;
  { RT.state.path = r.data.path; $("#tpl-path").value = r.data.path;
    PL.state.fileSha = r.data.file_sha; PL.state.savedJson = json;
    const base=PL.state.draftBase;
    if(PL.state.draft&&base?.revision===RT.state.revision&&json===PL.currentInputJson()){
      base.path=r.data.path;base.sha=r.data.file_sha;}
    if(json===PL.currentInputJson())window.AgentWorkbenchApp?.documentSaved?.(chatSnapshot);
    PL.renderStatus(); }
  setStatus(json===PL.currentInputJson()?"装备方案已保存":"所提交的装备方案副本已保存；后续修改仍需保存。", {path:r.data.path}); RT.refreshPlans(); return true;
};
RT.newTemplate = async () => { const intent=AppDocumentOpenIntents.begin();if (!await PL.leave({openIntent:intent})||!intent.current()) return;
  const r=await callApi("new_template");if(!intent.current())return;
  if(r.code){UI.error(r);return;}
  PL.state.plan = null; PL.state.draft = null; PL.state.fileSha = null;PL.state.appliedSummary=null;
  RT.replaceTemplate(r.data.template,"");PL.state.savedJson=null;EQ.closeView();PL.renderStatus();RT.renderAll();
  setStatus("已创建新手工模板（保存时写入方案库）"); };
