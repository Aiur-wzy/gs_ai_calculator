// 计算结果视图（G3）：一键计算（进程内引擎）+ 汇总/逐 hit 明细/覆盖率三视图 + 三格式导出
"use strict";

window.RS = { state: { result: null, snapshot: null, request: 0, pending: false } };

RS.reportOrigin=null;
RS.boardSnapshots=new WeakMap();
RS.exportFeedback=new WeakMap();
RS.renderExportFeedback=(node,record)=>{
  if(!node)return;
  node.hidden=!record;
  if(record){
    node.textContent=record.path?`${record.message} · ${record.path}`:record.message;
    node.setAttribute("role",record.state);
  }
};
RS.enterReport = previous => {
  if(RS.nextReportOrigin){RS.reportOrigin=RS.nextReportOrigin;RS.nextReportOrigin=null;return;}
  if(previous==="whiteboard")RS.reportOrigin={kind:"whiteboard"};
  if(previous==="rotation")RS.reportOrigin={kind:"editor"};
};
RS.sourceKind=()=>{
  const page=window.WS?.tab();
  if(page==="whiteboard")return "whiteboard";
  if(page==="rotation")return "editor";
  return RS.reportOrigin?.kind||(!window.WS&&window.RT?.tpl?.()?"editor":null);
};
RS.openSavedReport=async(path,{expectedFileSha,isCurrent=()=>true,onError=()=>{}}={})=>{
  const response=await callApi("open_saved_calculation",path,expectedFileSha??null);
  if(!isCurrent())return false;
  if(response.code){
    const message=response.message||"无法核验所选计算结果，原文件和当前文档已保留。";
    setStatus(message);onError(message);
    if(response.reason==="saved_calculation_version_incompatible"&&typeof response.details?.migration_prompt==="string")
      RS.showReportMigration(message,response.details.migration_prompt);
    return false;
  }
  const value=response.data, plan=value.plan;
  RS.reportOrigin={kind:"saved",result:value.result,snapshot:RS.packageSnapshot(value,plan?.manual_template?.meta?.name||"计算结果",value.path||path)};
  if(window.WS?.tab()==="result")RS.renderAll();else RS.nextReportOrigin=RS.reportOrigin;
  return true;
};
RS.showReportMigration=(message,prompt)=>{
  const dialog=window.AppDialogKernel?.create({title:"计算结果版本不兼容",description:message});
  if(!dialog)return;
  const detail=RS.el("details");
  const feedback=RS.el("p","hint");feedback.setAttribute("role","status");
  const copy=UI.button("复制兼容修改要求",async()=>{
    try{await navigator.clipboard.writeText(prompt);feedback.textContent="已复制，可粘贴到 AI 助手。";}
    catch{feedback.textContent="未能复制，请选中上面的文字手动复制。";}
  });
  detail.append(RS.el("summary",null,"查看兼容修改要求"),RS.el("p",null,prompt),copy,feedback);
  dialog.appBody.append(detail);dialog.setActions(UI.button("关闭",()=>dialog.finish("dismiss")));
};
RS.packageSnapshot=(value,name,path)=>({name:DS.prose(name,"未命名方案"),path,template:value.plan?.manual_template||value.plan||{characters:[]},
  fileSha:value.file_sha,resolvedTemplate:value.plan?.resolved_template,executionModel:value.report?.mechanics_model,executionTemplate:value.report?.mechanics_input_template,
  equipmentSummary:value.equipment_summary,calculatedAt:value.result_receipt?.calculated_at,reportHandle:value.report_handle,
  engineVersion:value.result_receipt?.engine_version,resultFingerprint:value.result_receipt?.result_fingerprint,
  paged:value.report_projection==="summary",exportAllowed:value.report_export_allowed!==false,
  package:value});
RS.view=()=>{
  const kind=RS.sourceKind();
  if(kind==="saved")return {...RS.reportOrigin,current:true,pending:false,readOnly:true};
  if(kind==="whiteboard"){
    const board=window.WB?.state.board, value=window.WB?.state.result, path=window.WB?.state.resultPath;
    if(!value||!board)return {kind,result:null,current:false,pending:false};
    let snapshot=RS.boardSnapshots.get(value);
    if(!snapshot||snapshot.path!==path||snapshot.title!==board.title){
      snapshot=RS.packageSnapshot(value,board.title,path);
      snapshot.name=typeof board.title==="string"&&board.title.trim()?board.title:"未命名白板";
      snapshot.title=board.title;RS.boardSnapshots.set(value,snapshot);
    }
    return {kind,result:value.result,snapshot,current:true,pending:false};
  }
  if(kind==="editor")return {kind,result:RS.state.result,snapshot:RS.state.snapshot,current:RS.isCurrent(),pending:RS.state.pending};
  return {kind:null,result:null,current:false,pending:false};
};

RS.inputJson = () => window.PL ? PL.currentInputJson() : JSON.stringify(RT.tpl());
RS.agentCandidateSessionId = () => {
  const agent = window.AC, session = agent?.selected?.();
  if (!session || session.connectionKind !== "native_api" || session.kind !== "native"
      || session.live !== true || session.applied !== true || !session.base
      || typeof session.session_id !== "string") return null;
  try {
    return agent.currentMatches?.(session.base) === true ? session.session_id : null;
  } catch {
    return null;
  }
};
RS.isCurrent = () => (!window.PL || PL.canCalculate()) && !!RS.state.result && !!RS.state.snapshot
  && RS.state.snapshot.revision === RT.state.revision
  && RS.state.snapshot.json === RS.inputJson();

RS.invalidate = () => {
  RS.state.request += 1;
  RS.state.pending = false;
  RS.renderAll();
  window.TV?.invalidateData?.();
};

RS.fmtInt = (v) => (v == null ? "—" : Math.round(v).toLocaleString("zh-CN"));
RS.fmtNum = (v) => (v == null ? "—" : Math.round(v * 1000) / 1000);
RS.fmtPct = (v) => `${(v * 100).toFixed(1)}%`;
RS.isVerifiedZero = (result) => result?.damage_reporting?.contract === "verified-empty-damage-v1"
  && result.damage_reporting.status === "verified_zero";
RS.hasExternal = result => !!(result?.external_hit_count||result?.external_damage?.items?.length);
RS.totalSummary = result => result.calculation_totals || result.summary;
RS.hasAppliedWhiteboardExport = (kind,fmt) => {
  const saved=window.WB?.state?.reusableResult;
  return kind==="whiteboard"&&saved?.owner==="native_dsh"&&!window.WB.unappliedEditor?.()
    &&(fmt==="json"?typeof window.AppAppliedResultSave==="function":typeof saved.deliveryCapability==="string");
};

RS.el = (tag, cls, text) => {
  const e = document.createElement(tag);
  if (cls) e.className = cls;
  if (text !== undefined) e.textContent = text;
  return e;
};

RS.section = (title, className, open = false) => {
  const section = RS.el("details", `result-section ${className || ""}`.trim());
  section.open = open;
  section.appendChild(RS.el("summary", "result-section-title", title));
  const content = RS.el("div", "result-section-content");
  section.appendChild(content);
  return {section, content};
};

RS.whenOpened=(section,render)=>{
  let built=false;
  const load=()=>{if(built||!section.open)return;built=true;section.removeEventListener("toggle",load);render();};
  section.addEventListener("toggle",load);load();
};

RS.readPart=async(snapshot,section,offset=0)=>{
  const response=await callApi("read_viewed_report",snapshot.reportHandle,section,offset);
  if(response.code)throw new Error(response.message||"无法读取报告内容");
  return response.data;
};

RS.openPart=(section,snapshot,name,render)=>{
  const load=async()=>{
    section.content.replaceChildren(RS.el("p","hint","正在读取报告明细…"));
    try{
      const data=await RS.readPart(snapshot,name);
      if(section.content.isConnected===false)return;
      section.content.replaceChildren();render(data);
    }catch(error){
      if(section.content.isConnected===false)return;
      const notice=RS.el("p","hint",error.message);notice.setAttribute("role","alert");
      section.content.replaceChildren(notice,UI.button("重试读取",load));
    }
  };
  RS.whenOpened(section.section,load);
};

RS.trustState = (result, current) => {
  if (!current) return {kind:"stale", title:"结果已失效", message:"方案已发生变化；下方只保留上次结果供对照，请重新计算后再导出或据此判断。"};
  const receipt = RS.actionMaterializationReceipt(result);
  const materialization = receipt ? RS.materializationStatus(receipt) : null;
  if (["incomplete", "unknown"].includes(materialization)) return {
    kind:"diagnostic", title:"诊断结果", message:materialization === "incomplete"
      ? "部分动作尚未完整展开；总伤和每秒伤害可用于定位缺项，不能视为完整结论。"
      : "动作覆盖状态无法确认；请展开诊断详情核对后再采用结果。",
  };
  if (result?.has_presentation_diagnostics || result?.presentation_issues?.length || result?.upstream_semantics?.conditional_semantics) return {
    kind:"review", title:"结果需要核对", message:"数值已生成，但描述或上游游戏语义仍有待核对内容；详情保留在“诊断与来源”。",
  };
  const totals = RS.totalSummary(result);
  if (totals?.precision === "approximate") return {
    kind:"review", title:"当前结果（含近似值）",
    message:"结果已按当前方案接纳，但部分单事件使用了有来源的近似值；总伤、每秒伤害和相关分组均继承近似标记。",
  };
  return {kind:"current", title:"当前结果", message:"本结果与当前方案一致；完整性仍以各项计算与机制检查为准。"};
};

// The receipt is a calculation diagnostic, not a source for timeline events.
// Keep the lookup deliberately shallow so an unrelated object with a similarly
// named property cannot silently become the authoritative receipt.
RS.actionMaterializationReceipt = source => {
  if (!source || typeof source !== "object" || Array.isArray(source)) return null;
  const direct = source.action_materialization_receipt
    || source.action_automation_audit?.materialization_receipt;
  if (direct && typeof direct === "object" && !Array.isArray(direct)) return direct;
  for (const key of ["result", "report"]) {
    const nested = source[key];
    if (!nested || typeof nested !== "object" || Array.isArray(nested)) continue;
    const receipt = nested.action_materialization_receipt
      || nested.action_automation_audit?.materialization_receipt;
    if (receipt && typeof receipt === "object" && !Array.isArray(receipt)) return receipt;
  }
  return null;
};

RS.materializationItems = value => Array.isArray(value) ? value : [];
RS.materializationId = (value, keys) => {
  if (typeof value === "string" || typeof value === "number") return String(value);
  if (!value || typeof value !== "object") return "";
  for (const key of keys) if (value[key] != null && value[key] !== "") return String(value[key]);
  return "";
};
RS.materializationCount = (row, key) => {
  const value = row?.[key];
  if (Array.isArray(value)) return value.length;
  if (Number.isFinite(value) && value >= 0) return value;
  const summaryValue = row?.summary?.[key];
  if (Number.isFinite(summaryValue) && summaryValue >= 0) return summaryValue;
  return null;
};
RS.materializationStatus = receipt => {
  const status = String(receipt?.status || "").toLowerCase();
  const calculation = String(receipt?.calculation_class || "").toLowerCase();
  const actions = RS.materializationRows(receipt);
  const incompleteAction = actions.some(row => {
    const coverage = String(row?.coverage_status || row?.status || "").toLowerCase();
    const expectedComponents = RS.materializationCount(row, "expected_components");
    const materializedComponents = RS.materializationCount(row, "materialized_components");
    const expectedOccurrences = RS.materializationCount(row, "expected_occurrences");
    const materializedOccurrences = RS.materializationCount(row, "materialized_occurrences");
    return ["partial", "pending", "incomplete", "diagnostic", "failed", "unresolved"].includes(coverage)
      || RS.materializationItems(row?.unresolved_components).length > 0
      || RS.materializationItems(row?.unresolved_occurrences).length > 0
      || (expectedComponents != null && materializedComponents != null
        && expectedComponents !== materializedComponents)
      || (expectedOccurrences != null && materializedOccurrences != null
        && expectedOccurrences !== materializedOccurrences);
  });
  if (["partial", "pending", "incomplete", "failed", "unresolved"].includes(status)
      || incompleteAction) return "incomplete";
  const contract = receipt?.contract ?? receipt?.contract_id;
  const unknownAction = actions.some(row => ["unknown", "not_evaluated"].includes(
    String(row?.coverage_status || row?.status || "").toLowerCase()));
  if ((contract && contract !== "action-materialization-receipt-v1")
      || status === "unknown" || unknownAction) return "unknown";
  if (status === "diagnostic" || calculation.includes("diagnostic")
      || calculation.includes("incomplete")) return "incomplete";
  if (["complete", "ready", "passed"].includes(status)) return "complete";
  return "unknown";
};
RS.materializationRows = receipt => {
  for (const key of ["actions", "receipts", "records", "items"])
    if (Array.isArray(receipt?.[key])) return receipt[key];
  const keys = ["action_id", "source_action", "ability_id", "expected_components",
    "materialized_components", "unresolved_components", "expected_occurrences",
    "materialized_occurrences", "unresolved_occurrences"];
  return keys.some(key => Object.hasOwn(receipt || {}, key)) ? [receipt] : [];
};
RS.materializationResultLabel = receipt => {
  const state = receipt ? RS.materializationStatus(receipt) : null;
  if (state === "incomplete") return "计算完成（诊断结果：动作计算不完整）";
  if (state === "unknown") return "计算完成（诊断结果：动作覆盖未知）";
  return "计算完成";
};
RS.materializationIds = (row, key, idKeys) => RS.materializationItems(row?.[key])
  .map(value => RS.materializationId(value, idKeys)).filter(Boolean);
RS.materializationIdSummary = ids => ids.length > 8
  ? `${ids.slice(0, 8).join("、")}（另有 ${ids.length - 8} 项，见原始回执）` : ids.join("、");
RS.materializationRatio = (row, expectedKey, materializedKey) => {
  const expected = RS.materializationCount(row, expectedKey);
  const materialized = RS.materializationCount(row, materializedKey);
  return expected == null && materialized == null ? null : `${materialized ?? "未知"}/${expected ?? "未知"}`;
};

RS.renderActionMaterializationReceipt = receipt => {
  const state = RS.materializationStatus(receipt);
  const titles = {incomplete:"动作计算不完整", complete:"动作物化完成", unknown:"动作物化状态未知"};
  const box = RS.el("section", `result-block action-materialization-card ${state}`);
  box.setAttribute("role", state === "incomplete" ? "alert" : "status");
  box.appendChild(RS.el("h3", null, titles[state]));
  const explanation = state === "incomplete"
    ? "本次计算仍有未物化的动作分量或发生实例；结果仅用于诊断，展开下方回执定位缺项。"
    : state === "complete"
      ? "回执列出的受审动作分量已在本次输入中物化；这不证明数据库已经覆盖游戏内全部机制。"
      : "回执没有提供可确认的完整状态，不能据此认为动作计算完整。";
  box.appendChild(RS.el("p", "action-materialization-note", explanation));
  const rows = RS.materializationRows(receipt);
  const summary = receipt?.summary;
  if (summary && typeof summary === "object" && !Array.isArray(summary)) {
    const components = RS.materializationRatio(summary, "expected_components", "materialized_components");
    const occurrences = RS.materializationRatio(summary, "expected_occurrences", "materialized_occurrences");
    if (components || occurrences) box.appendChild(RS.el("p", "action-materialization-summary",
      [components ? `动作分量 ${components}` : "", occurrences ? `发生实例 ${occurrences}` : ""].filter(Boolean).join("；")));
  }
  for (const [index, row] of rows.entries()) {
    if (!row || typeof row !== "object" || Array.isArray(row)) continue;
    const action = RS.materializationId(row, ["action_id", "source_action", "source_action_id"])
      || RS.materializationId(row, ["ability_id", "profile_id"]) || `第 ${index + 1} 个动作`;
    const ability = RS.materializationId(row, ["ability_id"]);
    const profile = RS.materializationId(row, ["profile_id"]);
    const components = RS.materializationRatio(row, "expected_components", "materialized_components");
    const occurrences = RS.materializationRatio(row, "expected_occurrences", "materialized_occurrences");
    const unresolvedComponents = RS.materializationIds(row, "unresolved_components",
      ["component_id", "id"]);
    const unresolvedOccurrences = RS.materializationIds(row, "unresolved_occurrences",
      ["occurrence_id", "id", "component_id"]);
    const identity = [`来源动作 ${action}`];
    if (ability && ability !== action) identity.push(`能力 ${ability}`);
    if (profile && profile !== action) identity.push(`档案 ${profile}`);
    const facts = [components ? `分量 ${components}` : "", occurrences ? `实例 ${occurrences}` : "",
      unresolvedComponents.length ? `未解决分量 ${RS.materializationIdSummary(unresolvedComponents)}` : "",
      unresolvedOccurrences.length ? `未解决实例 ${RS.materializationIdSummary(unresolvedOccurrences)}` : ""].filter(Boolean);
    box.appendChild(RS.el("p", "action-materialization-row", `${identity.join(" · ")}：${facts.join("；") || "没有可显示的计数"}`));
  }
  if (!rows.length && !(summary && typeof summary === "object"))
    box.appendChild(RS.el("p", "action-materialization-row", "回执没有动作级计数。"));
  box.appendChild(UI.details("动作物化原始回执", receipt));
  return box;
};

// ---------- 计算与导出 ----------

RS.runCalc = async () => {
  if (!RT.tpl()) { setStatus("请先在排轴编辑器加载或新建模板"); return; }
  if (window.PL?.draftChanged()) { setStatus("请先应用或明确放弃装备草稿"); return; }
  RT.syncRevision();
  const snapshot = {
    json: RS.inputJson(), revision: RT.state.revision, path: RT.state.path,
  };
  snapshot.template = UI.clone(window.PL?.state.plan ? PL.input() : RT.tpl());
  snapshot.name = DS.prose((snapshot.template.manual_template || snapshot.template).meta?.name || "未命名方案", "方案中文名待补");
  const request = ++RS.state.request;
  RS.state.pending = true;
  RS.renderAll();
  setStatus("计算中…");
  const native = !!window.PL?.state.plan;
  const r = await callApi(
    native ? "run_plan_calc" : "run_calc",
    native ? UI.clone(PL.state.plan) : snapshot.template,
    ...(native ? [RS.agentCandidateSessionId()] : []),
  );
  if (request !== RS.state.request) return;
  RS.state.pending = false;
  RT.syncRevision();
  if (request !== RS.state.request || snapshot.revision !== RT.state.revision
      || snapshot.json !== RS.inputJson()) {
    setStatus("方案已变更，本次计算结果已丢弃，请重新计算");
    return;
  }
  RS.renderAll();
  if (r.code !== 0) { setStatus(`计算失败: ${r.message}`);
    if (native && r.details) {
      PL.state.plan.resolution = {state:"pending", missing_inputs:r.details}; PL.renderStatus();
      RS.renderAll(); window.TV?.invalidateData?.();
    }
    return {error:r}; }
  if (native && r.data.plan) { PL.state.plan.resolution = r.data.plan.resolution;
    PL.state.plan.resolved_template = r.data.plan.resolved_template;
    PL.state.appliedSummary=UI.clone(r.data.equipment_summary||null);PL.renderStatus();RT.renderTeamPanel(); }
  snapshot.equipmentSummary=r.data.equipment_summary?UI.clone(r.data.equipment_summary):null;
  if(native)snapshot.executionModel=UI.clone(r.data.report?.mechanics_model || snapshot.template.mechanics_model || null);
  if(native)snapshot.executionTemplate=UI.clone(r.data.report?.mechanics_input_template || null);
  if(native&&r.data.plan?.resolved_template)snapshot.resolvedTemplate=UI.clone(r.data.plan.resolved_template);
  snapshot.calculatedAt = new Date().toISOString();
  snapshot.reportHandle = r.data.report_handle;
  RS.state.snapshot = snapshot;
  RS.state.result = { ...r.data.result, gui_input: {
    template: snapshot.template, path: snapshot.path, revision: snapshot.revision,
    calculated_at: snapshot.calculatedAt,
  }, ...(native ? {result_receipt:r.data.result_receipt,presentation_issues:r.data.presentation_issues||[]} : {}) };
  const materializationReceipt = RS.actionMaterializationReceipt(r.data);
  if(materializationReceipt && !RS.state.result.action_materialization_receipt)
    RS.state.result.action_materialization_receipt = UI.clone(materializationReceipt);
  RS.renderAll();
  if(window.TD)RT.renderTimeline();
  const s = RS.totalSummary(RS.state.result);
  setStatus(`${RS.materializationResultLabel(materializationReceipt)}：总伤 ${RS.fmtInt(s.total_damage)}` +
    (s.dps != null ? `，每秒伤害 ${RS.fmtInt(s.dps)}` : ""));
  return r.data;
};

RS.exportResult = async (fmt,onOutcome) => {
  if(RS.state.exportPending)return false;
  const view=RS.view(),{kind,result,snapshot}=view;
  if (!view.current || view.pending || snapshot?.exportAllowed===false) {
    setStatus("结果未就绪或已失效，请重新计算后导出"); return false;
  }
  if(fmt === "csv" && RS.isVerifiedZero(result)) {
    setStatus("已验证零伤结果需保留伤害报告证明，请导出 JSON 或 Excel；CSV 不支持。"); return false;
  }
  if(fmt === "csv" && RS.hasExternal(result)) {
    setStatus("含外部计算值的结果须保留引擎与补充明细，请导出 JSON 或 Excel；CSV 不支持。"); return false;
  }
  RS.state.exportPending=true;RS.renderAll();
  const feedback=(message,state="status",path)=>{
    if(snapshot)RS.exportFeedback.set(snapshot,{message,state,path});
    setStatus(message,path?{path}:undefined);RS.renderAll();
  };
  try{
    let response;
    const application=kind==="whiteboard"?WB.state.reusableResult:null;
    if(!snapshot.reportHandle&&RS.hasAppliedWhiteboardExport(kind,fmt)){
      const board=WB.state.board,calculated=WB.state.result,identity=WB.agentPayloadIdentity?.();
      const unchanged=()=>{
        const active=RS.view();
        return active.current&&active.kind===kind&&active.snapshot===snapshot&&WB.state.board===board
          &&WB.state.result===calculated&&WB.state.reusableResult===application&&!WB.unappliedEditor?.()
          &&(!identity||WB.agentPayloadMatches?.({...identity,documentRef:board}));
      };
      if(fmt==="json"){
        feedback("正在准备完整计算结果…");
        response=await window.AppAppliedResultSave(application.sessionId,board,calculated,unchanged);
      }
      else{
        const selected=await WB.pickUserFile({action:"export",document_kind:`report_${fmt}`,suggested_name:`${snapshot.name}.${fmt}`});
        if(!selected||selected.cancelled){feedback("已取消导出");onOutcome?.("cancelled");return false;}
        if(!unchanged()){feedback("选择位置期间白板或报告发生变化，未导出","alert");return false;}
        feedback("正在写出当前白板的计算结果…");
        response=await callApi("dsh_client_publish_whiteboard_result",{
          conversation_id:application.sessionId,delivery_capability:application.deliveryCapability,
          output_path:selected.path,format:fmt,expected_file_sha:selected.file_sha??null,
          expected_result_fingerprint:snapshot.resultFingerprint,
        });
      }
    }else{
      if(!snapshot.reportHandle&&(kind!=="editor"||window.PL?.state.plan)){setStatus("这份结果没有对应的导出凭据，请重新打开已保存的完整结果");return false;}
      const suffix=fmt==="json"&&snapshot.reportHandle?"calculated.json":fmt;
      const selected=await WB.pickUserFile({action:"export",document_kind:`report_${fmt}`,suggested_name:`${snapshot.name}.${suffix}`});
      if(!selected||selected.cancelled){feedback("已取消导出");onOutcome?.("cancelled");return false;}
      const active=RS.view();
      if(!active.current||active.kind!==kind||active.snapshot!==snapshot||active.result!==result){feedback("报告已变化，未导出，请重新选择","alert");return false;}
      feedback("正在写出计算结果…");
      response=snapshot.reportHandle
        ?await callApi("export_viewed_report",snapshot.reportHandle,fmt,selected.path,selected.file_sha??null)
        :await callApi("export_result",result,fmt,snapshot.name,selected.path);
    }
    if(response.code){UI.error(response);feedback(response.message||"导出未完成，请查看状态详情。","alert");return false;}
    if(response.data?.cancelled||response.data?.status==="cancelled"){feedback("已取消导出");onOutcome?.("cancelled");return false;}
    if(!response.data?.path){feedback("App 未确认写出结果，请先核对所选文件","alert");onOutcome?.("unknown");return false;}
    onOutcome?.("completed");feedback("结果已导出","status",response.data.path);return true;
  }catch(error){feedback(`导出未完成，请先核对目标文件。${error?.message||""}`,"alert");onOutcome?.("unknown");return false;}
  finally{RS.state.exportPending=false;RS.renderAll();window.WS?.refresh?.();}
};

// ---------- 渲染 ----------

RS.renderAll = () => {
  const report=RS.view(),{current,result:res,snapshot}=report;
  const formulaButton=$("#btn-exp-formula");
  if(formulaButton)formulaButton.disabled=report.kind!=="editor"||!window.PL?.state.plan || !current || RS.state.pending;
  ["#btn-exp-json", "#btn-exp-xlsx", "#btn-exp-csv"].forEach((id) => {
    const externalOnly = id === "#btn-exp-csv" && RS.hasExternal(res);
    const proofOnly = id === "#btn-exp-csv" && (RS.isVerifiedZero(res) || externalOnly);
    const available=(!!snapshot?.reportHandle&&snapshot.exportAllowed!==false)||(report.kind==="editor"&&!window.PL?.state.plan)
      ||RS.hasAppliedWhiteboardExport(report.kind,id.slice(9));
    $(id).disabled = !current || !available || report.pending || RS.state.exportPending || proofOnly;
    $(id).title = externalOnly ? "外部伤害须保留完整明细，请导出 JSON 或 Excel" : proofOnly ? "零伤证明需保留完整记录，请导出 JSON 或 Excel" : "";
  });
  $("#btn-calc").disabled = report.kind!=="editor"||RS.state.pending;
  if($("#timeline-calculate"))$("#timeline-calculate").disabled=RS.state.pending;
  // Keep the result page cheap while the user works on the timeline.
  if($("#tab-result")?.classList.contains?.("active")===false)return;
  const body = $("#result-body");
  const exportFeedback=snapshot&&RS.exportFeedback.get(snapshot);
  if(body._result===res && body._snapshot===snapshot && body._kind===report.kind
      && body._plan===window.PL?.state.plan && body._external===window.PL?.state.plan?.external_damage_supplements
      && body._externalCaps===window.PL?.state.caps?.external_damage_contract
      && body._current===current && body._pending===RS.state.pending){
    RS.renderExportFeedback(body._exportFeedbackNode,exportFeedback);return;
  }
  body._plan=window.PL?.state.plan;body._external=window.PL?.state.plan?.external_damage_supplements;
  body._externalCaps=window.PL?.state.caps?.external_damage_contract;
  body._result=res;body._snapshot=snapshot;body._kind=report.kind;
  body._current=current;body._pending=RS.state.pending;
  body._exportFeedbackNode=null;
  body.innerHTML = "";
  if (!res) {
    if(report.kind==="editor")window.XD?.render(body);
    const message=report.pending?"正在计算当前方案…":report.kind==="whiteboard"?"当前白板没有可用计算结果。完成计算并同步后，回到这里查看。":report.kind==="editor"?"当前计算模型尚未计算。点击「计算」查看结果。":"先从白板、计算模型或已保存结果打开一份报告。";
    body.appendChild(RS.el("div", "placeholder",message));
    if(!report.kind){
      const select=kind=>{RS.reportOrigin={kind};RS.renderAll();window.WS?.refresh?.();};
      if(window.WB?.state.board)body.appendChild(UI.button("查看当前白板报告",()=>select("whiteboard")));
      if(window.RT?.tpl?.())body.appendChild(UI.button("查看当前计算模型报告",()=>select("editor")));
    }
    return;
  }
  const trust=RS.trustState(res,current),source=RS.el("section",`result-trust ${trust.kind}`);
  source.setAttribute("role",trust.kind==="current"?"status":"alert");
  source.appendChild(RS.el("strong",null,trust.title));
  const kindLabel={whiteboard:"正式白板",editor:"计算模型",saved:"已保存结果"}[report.kind];
  const status=report.readOnly&&trust.kind==="current"?"已核验保存时的计算结果。":trust.message;
  source.appendChild(RS.el("span",null,`${report.readOnly?"只读结果。 ":""}${status} 来源：${kindLabel} · ${snapshot.name}`));
  if(report.readOnly&&snapshot.engineVersion)source.appendChild(RS.el("span","hint",`计算版本 ${snapshot.engineVersion}`));
  if(report.readOnly&&snapshot.path&&snapshot.fileSha&&window.WB?.state.board){
    source.appendChild(UI.button("核对并导入到当前白板",()=>WB.importResult(snapshot.path,snapshot.fileSha)));
  }
  body.appendChild(source);
  const provisionalUsage=res.provisional_entity_usage_receipt;
  if(provisionalUsage?.entities?.length){
    const notice=RS.el("section","result-trust review");notice.setAttribute("role","status");
    notice.appendChild(RS.el("strong",null,"当前结果使用临时公共资料（近似）"));
    notice.appendChild(RS.el("span",null,`共 ${provisionalUsage.entities.length} 项，仅对当前方案有效；数据库、来源或方案变化后必须重新核对。`));
    body.appendChild(notice);
  }
  if(res.uses_provisional_data&&!provisionalUsage?.entities?.length)
    body.appendChild(RS.el("p","hint","此历史计算使用临时公共资料，只能查看。具体来源见诊断。"));
  body.appendChild(RS.renderSummary(RS.totalSummary(res),res.validation_scope));
  body._exportFeedbackNode=RS.el("p","hint");
  RS.renderExportFeedback(body._exportFeedbackNode,exportFeedback);body.appendChild(body._exportFeedbackNode);
  if(RS.isVerifiedZero(res))body.appendChild(RS.renderDamageReporting(res.damage_reporting));

  const damage=RS.section(`伤害明细（${res.reported_hit_count??res.hits?.length??0}）`,"result-section-damage");
  if(snapshot.paged)RS.openPart(damage,snapshot,"damage",extra=>{
    window.XD?.renderResult(damage.content,{...res,...extra});
    damage.content.appendChild(RS.renderHits([],{total:res.reported_hit_count,read:offset=>RS.readPart(snapshot,"hits",offset)}));
  });
  else RS.whenOpened(damage.section,()=>{
    window.XD?.renderResult(damage.content,res);
    damage.content.appendChild(RS.renderHits(res.hits||[]));
  });
  body.appendChild(damage.section);

  const effects=RS.section("效果与覆盖","result-section-effects");
  const renderEffects=rows=>{
    effects.content.appendChild(RS.renderBuffStates(current,{...report,result:rows}));
    if(rows.coverage_interpretation?.kind==="hit_projection")effects.content.appendChild(UI.el("p",rows.coverage_interpretation.message,"hint"));
    else effects.content.appendChild(RS.renderCoverage(rows.buffs_coverage||[]));
  };
  if(snapshot.paged)RS.openPart(effects,snapshot,"effects",rows=>renderEffects({...res,...rows}));
  else RS.whenOpened(effects.section,()=>renderEffects(res));
  body.appendChild(effects.section);

  const mechanics=RS.section("循环与机制","result-section-mechanics");
  const renderMechanics=rows=>{
    if (window.RC) mechanics.content.appendChild(RC.renderResult(rows.rotation_check, RS.resultTemplate()));
    if (window.MC && rows.mechanics_trace)
      mechanics.content.appendChild(MC.renderTrace(rows, RS.resultTemplate()));
    window.CAP?.renderResult(mechanics.content,rows,RS.resultTemplate());
    window.AA?.renderResult?.(mechanics.content,rows,RS.resultTemplate());
  };
  if(snapshot.paged)RS.openPart(mechanics,snapshot,"mechanics",rows=>{
    renderMechanics(rows);
    if(rows.mechanics_status)mechanics.content.appendChild(RS.el("p","hint",`机制执行 ${window.MC?.label(rows.mechanics_status)||rows.mechanics_status}`));
    if(rows.mechanics_issues?.length)mechanics.content.appendChild(UI.details("机制问题定位",rows.mechanics_issues));
    if(rows.elemental?.evidence_tier==="experimental_reference")mechanics.content.appendChild(UI.details("元素参考模型与采用依据",rows.elemental));
    for(const {key,label,total} of rows.detail_sections||[]){
      const detail=RS.section(`${label}（${total}）`,"result-ledger-page");
      mechanics.content.appendChild(detail.section);
      RS.whenOpened(detail.section,()=>RS.renderPagedLedger(detail.content,snapshot,key));
    }
  });
  else RS.whenOpened(mechanics.section,()=>renderMechanics(res));
  body.appendChild(mechanics.section);

  const diagnostics=RS.section("诊断与来源","result-section-diagnostics");
  const renderDiagnostics=(res,sourceTemplate=snapshot.template,sourceEquipment=snapshot.equipmentSummary)=>{
  const materializationReceipt=RS.actionMaterializationReceipt(res),provisionalUsage=res.provisional_entity_usage_receipt;
  if(report.kind==="editor")window.XD?.render(diagnostics.content);
  if(materializationReceipt)diagnostics.content.appendChild(RS.renderActionMaterializationReceipt(materializationReceipt));
  if(provisionalUsage?.entities?.length)diagnostics.content.appendChild(UI.details("临时公共资料的来源与使用回执",provisionalUsage));
  if(res.upstream_semantics?.conditional_semantics){
    diagnostics.content.appendChild(RS.el("div","result-source","上游结构可执行；游戏语义包含显式适配假设，尚未完全验证。"));
    diagnostics.content.appendChild(UI.details("上游执行范围与来源指纹",res.upstream_semantics));
  }
  if(res.presentation_issues?.length)diagnostics.content.appendChild(UI.details("中文描述待补（含字段定位）",res.presentation_issues));
  const input = RS.el("details", "result-input");
  input.appendChild(RS.el("summary", null, `原始数据 · 计算输入：${snapshot.name}`));
  input.appendChild(RS.el("div", "hint", `计算时间：${snapshot.calculatedAt||"未记录"}`
    + (snapshot.path ? ` · 文件：${snapshot.path}` : " · 未保存方案")));
  if(snapshot.resultFingerprint)input.appendChild(RS.el("div","hint",`计算结果校验码 ${snapshot.resultFingerprint}`));
  RS.whenOpened(input,()=>input.appendChild(RS.el("pre", "zones", JSON.stringify(sourceTemplate, null, 2))));
  diagnostics.content.appendChild(input);
  if(window.EQ?.summaryCard){const equipment=RS.el("section","result-block");
    equipment.appendChild(RS.el("h3",null,"本次计算使用的角色与装备"));
    const summary=sourceEquipment;
    const template=sourceTemplate;
    const chars=summary?.characters||((template.manual_template||template).characters||[]).map(c=>
      EQ.summaryCharacter(c.id,template,{characters:[]}));
    chars.forEach(c=>equipment.appendChild(EQ.summaryCard(c,DS.prose(c.display_name,"角色中文名待补"),true)));
    if(summary)equipment.appendChild(UI.details("本次装备输入绑定",summary.context));
    diagnostics.content.appendChild(equipment);}
  if (res.assumptions && res.assumptions.length) {
    const box = RS.el("div", "result-block");
    box.appendChild(RS.el("h3", null, "口径假设"));
    const ul = RS.el("ul", "assumptions");
    res.assumptions.forEach((a) => { const li = RS.el("li"); li.appendChild(DS.textBlock(a)); ul.appendChild(li); });
    box.appendChild(ul);
    diagnostics.content.appendChild(box);
  }
  };
  if(snapshot.paged)RS.openPart(diagnostics,snapshot,"diagnostics",details=>
    renderDiagnostics(details.result,details.template||snapshot.template,details.equipment_summary));
  else RS.whenOpened(diagnostics.section,()=>renderDiagnostics(res));
  body.appendChild(diagnostics.section);
};

RS.renderDamageReporting = (proof) => {
  const box = RS.el("section", "result-block");
  box.appendChild(RS.el("h3", null, "伤害报告证明"));
  box.appendChild(RS.el("p", null, `完整输入窗口内 ${proof.planned_contacts} 个已声明接触全部未命中；引擎总伤害和每秒伤害均为 0。`));
  box.appendChild(RS.el("p", "hint", `窗口 [${proof.scope.start_tick / 1000000}, ${proof.scope.end_tick / 1000000}) 秒；实际未命中 ${proof.missed_contacts} 次，伤害事件 ${proof.reported_damage_events} 个。`));
  box.appendChild(RS.el("p", "hint", "动作、扣能与收益激活仍按实际流水保留；没有补造零伤命中。命中声明或动作改变后须重新计算，循环是否成立另行检查。"));
  return box;
};

RS.renderSummary = (s,scope) => {
  const box = RS.el("div", "result-block");
  box.appendChild(RS.el("h3", null, s.external_count ? "汇总（含外部计算补充）" : "汇总"));
  const cards = RS.el("div", "stat-cards");
  const mk = (label, value) => {
    const c = RS.el("div", "stat-card");
    c.appendChild(RS.el("div", "stat-value", value));
    c.appendChild(RS.el("div", "stat-label", label));
    return c;
  };
  cards.appendChild(mk("总伤害", RS.fmtInt(s.total_damage)));
  cards.appendChild(mk("每秒伤害", s.dps != null ? RS.fmtInt(s.dps) : "—"));
  if(s.external_count){
    cards.appendChild(mk("引擎计算伤害", RS.fmtInt(s.engine_total_damage)));
    cards.appendChild(mk("外部计算伤害", RS.fmtInt(s.external_total_damage)));
    cards.appendChild(mk("外部补充项数", s.external_count));
  }
  if(s.precision === "approximate"){
    cards.appendChild(mk("结果精度", "近似"));
    cards.appendChild(mk("近似事件", s.approximate_event_count || 0));
    box.appendChild(RS.el("p", "hint", "近似项已传播到相关角色、技能、总伤害与每秒伤害；展开外部伤害明细可查看方法和不确定性。"));
  }
  box.appendChild(cards);
  box.appendChild(RS.el("p", "hint", scope?.summary || "按保存输入计算；旧结果未保存统一验证范围，不代表游戏机制完整。"));

  const groups = RS.el("div", "stat-groups");
  groups.appendChild(RS.miniTable("按角色", s.by_character, "character"));
  groups.appendChild(RS.miniTable("按技能", s.by_skill, "skill"));
  box.appendChild(groups);
  return box;
};

RS.activeSnapshot=()=>window.WS?.tab()==="result"?RS.view().snapshot:RS.state.snapshot;
RS.resultTemplate = () => RS.activeSnapshot()?.template?.manual_template || RS.activeSnapshot()?.template || RT.tpl();
RS.resultBuffTemplate = () => RS.activeSnapshot()?.resolvedTemplate || RS.resultTemplate();
RS.skillGroups = obj => {
  const labels=DS.labels.skills || {}, known=new Set(Object.values(labels).filter(DS.hasChinese)), groups=new Map();
  const chars=RS.resultTemplate()?.characters || [];
  for(const [key,value] of Object.entries(obj)){
    const owner=chars.find(c=>key.startsWith(c.id+".")||key.startsWith(c.id+":"));
    const skill=owner?key.slice(owner.id.length+1):key, label=DS.skill(skill);
    const confirmed=known.has(label)&&(Object.hasOwn(labels,skill)||skill===label);
    const groupKey=confirmed?JSON.stringify(["known",owner?.id??null,label]):JSON.stringify(["raw",key]);
    let row=groups.get(groupKey);
    if(!row){row={name:owner?`${DS.character(owner.id,RS.resultTemplate(),RS.activeSnapshot()?.template)} · ${label}`:label,value:0,raw_keys:[]};groups.set(groupKey,row);}
    row.value+=value;row.raw_keys.push(key);
  }
  return [...groups.values()];
};
RS.miniTable = (title, obj, kind) => {
  const wrap = RS.el("div");
  wrap.appendChild(RS.el("div", "sub-hdr", title));
  const tb = RS.el("table", "grid");
  const total = Object.values(obj).reduce((a, b) => a + b, 0);
  tb.innerHTML = "<tr><th>名称</th><th>期望伤害</th><th>占比</th></tr>";
  const rows=kind==="skill"?RS.skillGroups(obj):Object.entries(obj).map(([key,value])=>({value,name:DS.character(key,RS.resultTemplate(),RS.activeSnapshot()?.template)}));
  rows.sort((a,b)=>b.value-a.value).forEach(row => {
    const tr = tb.insertRow();
    [row.name, RS.fmtInt(row.value), RS.fmtPct(total ? row.value / total : 0)].forEach((x) => {
      tr.insertCell().textContent = x;
    });
  });
  wrap.appendChild(tb);
  if(kind==="skill"){
    const detail=RS.el("details"),raw=RS.el("pre","zones",JSON.stringify(rows.map(row=>({name:row.name,value:row.value,raw_keys:row.raw_keys})),null,2));
    detail.appendChild(RS.el("summary",null,"技能汇总原始键（仅合并同一角色范围内已注册的同名中文分类）"));
    detail.appendChild(raw);wrap.appendChild(detail);
  }
  return wrap;
};

RS.renderHits = (hits,remote) => {
  const box = RS.el("div", "result-block");
  const total=remote?.total??hits.length;
  box.appendChild(RS.el("h3", null, `引擎逐伤害事件明细（${total}）`));
  const wrap = RS.el("div", "table-scroll");
  const tb = RS.el("table", "grid"),header=RS.el("tr");
  for(const label of ["时刻（秒）","角色","技能","段名","体系","段数","期望伤害","暴击伤害","快照时刻","生效增益","乘区"])
    header.appendChild(RS.el("th",null,label));
  const pageSize=100,controls=RS.el("div","result-pagination");let page=0;
  let request=0;
  const status=RS.el("span"),prev=UI.button("上一页",()=>{page--;showPage();}),next=UI.button("下一页",()=>{page++;showPage();});
  status.setAttribute("role","status");controls.append(prev,status,next);box.appendChild(controls);
  function showPage(){
    const begin=page*pageSize;
    if(!remote){renderPage(hits.slice(begin,begin+pageSize));return;}
    const current=++request;
    prev.disabled=next.disabled=true;status.textContent="正在读取明细…";
    remote.read(begin).then(data=>{
      if(current!==request||box.isConnected===false)return;
      renderPage(data.items);
    }).catch(error=>{
      if(current!==request||box.isConnected===false)return;
      status.replaceChildren(RS.el("span",null,error.message),UI.button("重试读取",showPage));
      prev.disabled=page===0;next.disabled=begin+pageSize>=total;
    });
  }
  function renderPage(rows){
  tb.replaceChildren(header);
  const begin=page*pageSize;
  status.textContent=`第 ${page+1} / ${Math.max(1,Math.ceil(total/pageSize))} 页`;
  prev.disabled=page===0;next.disabled=begin+pageSize>=total;
  rows.forEach((h, index) => {
    const i=begin+index;
    const tr = tb.insertRow();
    [h.t, DS.character(h.character, RS.resultTemplate(), RS.activeSnapshot()?.template), DS.skill(h.skill), DS.hit(h,i,RS.resultTemplate()), DS.label("damage_systems", h.damage_system), h.count,
     RS.fmtInt(h.expected), h.crit != null ? RS.fmtInt(h.crit) : "—",
     h.snapshot_at != null ? h.snapshot_at : "—",
    ].forEach((x) => { tr.insertCell().textContent = x; });
    tr.insertCell().textContent = h.buffs.map(id => DS.buff(id, RS.resultBuffTemplate())).join("、") || "—";
    const zc = tr.insertCell();
    const det = RS.el("details");
    det.appendChild(RS.el("summary", null, `${Object.keys(h.zones).length} 区`));
    RS.whenOpened(det,()=>{
      const pre = RS.el("pre", "zones");
      pre.textContent = Object.entries(h.zones).map(([k, v]) => `${DS.label("result_zones", k)} = ${RS.fmtNum(v)}`).join("\n");
      det.appendChild(pre);det.appendChild(DS.raw("本次伤害事件", h));
    });
    zc.appendChild(det);
  });
  }
  showPage();
  wrap.appendChild(tb);
  box.appendChild(wrap);
  return box;
};

RS.renderPagedLedger=(content,snapshot,key)=>{
  let offset=0;
  const load=async()=>{
    content.replaceChildren(RS.el("p","hint","正在读取流水…"));
    try{
      const data=await RS.readPart(snapshot,key,offset);
      if(content.isConnected===false)return;
      const range=RS.el("p","hint",data.total?`第 ${offset+1}–${offset+data.items.length} 条，共 ${data.total} 条`:"此结果没有对应记录");
      const previous=UI.button("上一页",()=>{offset-=100;void load();});
      const next=UI.button("下一页",()=>{offset=data.next_offset;void load();});
      previous.disabled=offset===0;next.disabled=data.next_offset===null;
      content.replaceChildren(range,previous,next,RS.el("pre","zones",JSON.stringify(data.items,null,2)));
    }catch(error){
      if(content.isConnected!==false)content.replaceChildren(RS.el("p","hint",error.message),UI.button("重试读取",load));
    }
  };
  void load();
};

RS.renderCoverage = (cov) => {
  const box = RS.el("div", "result-block");
  box.appendChild(RS.el("h3", null, `增益覆盖率（${cov.length}）`));
  if (!cov.length) { box.appendChild(RS.el("div", "hint", "模板没有增益或尚未设置循环时长")); return box; }
  const tb = RS.el("table", "grid");
  tb.innerHTML = "<tr><th>增益名称</th><th>来源</th><th>覆盖率</th><th></th></tr>";
  cov.forEach((c) => {
    const tr = tb.insertRow();
    [DS.buff(c.id, RS.resultBuffTemplate()), DS.prose(c.source || "—"), RS.fmtPct(c.uptime)].forEach((x) => {
      tr.insertCell().textContent = x;
    });
    const barCell = tr.insertCell();
    const bar = RS.el("div", "uptime-bar");
    const fill = RS.el("div", "uptime-fill");
    fill.style.width = `${Math.min(100, c.uptime * 100)}%`;
    bar.appendChild(fill);
    barCell.appendChild(bar);
  });
  box.appendChild(tb);
  return box;
};

// State intervals and hit-projection ratios are different records. Only the
// former can be navigated as persistent Buffs, including exhausted states.
RS.renderBuffStates = (current,report={kind:"editor",snapshot:RS.state.snapshot,result:RS.state.result}) => {
  const box=RS.el("section","result-block result-buff-states");
  box.appendChild(RS.el("h3",null,"持续 Buff 状态"));
  const snapshot=report.snapshot;
  if(!current){box.appendChild(RS.el("p","hint","计算结果已失效，请计算当前方案后查看持续 Buff。"));
    const button=RS.el("button",null,"计算当前方案");button.type="button";button.addEventListener("click",RS.runCalc);box.appendChild(button);return box;}
  if(!window.TD){box.appendChild(RS.el("p","hint","请在效果轴查看持续状态。"));return box;}
  const projection=TD.build({template:RS.resultTemplate(),plan:snapshot?.package?.plan||(snapshot?.template?.plan_version?snapshot.template:null),
    result:report.result,executionModel:snapshot?.executionModel,executionTemplate:snapshot?.executionTemplate,current:true});
  const info=projection.views.effects.coverage,events=TD.groupCoverage(projection.views.effects.events.filter(e=>e.kind==="coverage"),projection.characters);
  box.appendChild(RS.el("p","hint",info.message));
  if(info.origin==="engine")box.appendChild(RS.el("p","hint","实例角色范围用于追踪状态与次数；敌方减抗等效果可作用于队友对同一目标的伤害，具体范围见区间详情。"));
  if(events.length){const table=RS.el("table","grid"),wrap=RS.el("div","table-scroll");
    const header=RS.el("tr");
    for(const label of ["来源 / 增益","实例角色范围","时间区间（秒）","状态",...(report.readOnly?[]:["定位"])])header.append(RS.el("th",null,label));
    const pageSize=100;let offset=0;
    const previous=UI.button("上一页",()=>{offset-=pageSize;drawPage();}),next=UI.button("下一页",()=>{offset+=pageSize;drawPage();});
    const pageLabel=RS.el("span",null);pageLabel.setAttribute("role","status");
    if(events.length>pageSize){const controls=RS.el("div","result-page-controls");controls.append(previous,pageLabel,next);box.appendChild(controls);}
    function drawPage(){
      table.replaceChildren(header);
      const end=Math.min(offset+pageSize,events.length);previous.disabled=offset===0;next.disabled=end===events.length;
      pageLabel.textContent=`第 ${offset+1}–${end} 条，共 ${events.length} 条`;
      for(const event of events.slice(offset,end)){const row=table.insertRow();
        [event.sourceLabel,event.beneficiary_names.join("、"),`${RS.fmtNum(event.start)} → ${RS.fmtNum(event.end)}`,
          TD.stateName(event.detail.origin==="declared_interval"||event.detail.buff?"declared":event.detail.state_status||"active")].forEach(value=>{row.insertCell().textContent=value;});
        if(!report.readOnly)row.insertCell().appendChild(UI.button("查看区间",()=>RS.openBuffState(event,report.kind)));}
    }
    drawPage();
    wrap.appendChild(table);box.appendChild(wrap);}
  if(info.excluded?.length){const details=RS.el("details"),list=RS.el("ul");
    details.appendChild(RS.el("summary",null,`未绘制的来源（${info.excluded.length}）`));
    for(const item of info.excluded)list.appendChild(RS.el("li",null,`${DS.prose(item.name_zh,"增益来源")}：${item.message || "未提供可靠的有限持续时间"}`));
    details.appendChild(list);box.appendChild(details);}
  return box;
};
RS.openBuffState = (event,kind="editor") => {
  if(kind==="whiteboard"){
    if(!WB.state.result){setStatus("白板结果已失效");return;}
    window.UI?.tabs?.("whiteboard");WB.setMode("effects");const match=WB.project().events.find(row=>row.id===event.id);
    if(match)WB.selectEvent(match);return;
  }
  if(!RS.isCurrent()){setStatus("结果已失效，请重新计算");return;}
  window.UI?.tabs?.("rotation");
  if(!window.TV?.refreshProjection)return;
  TV.refreshProjection();TV.changeMode("effects");TV.characterFilter="";TV.sourceFilter=event.sourceKey;
  TV.kindVisibility.effects={};TV.effectFocus="coverage";TV.rebuildRows();TV.paintProjection();
  const displayed=TV.rows.flatMap(row=>row.events).find(row=>row.id===event.id) || event;
  const rowIndex=TV.rows.findIndex(row=>row.events.includes(displayed)),wrap=$("#timeline-wrap");
  if(wrap){wrap.scrollLeft=Math.max(0,event.start*TV.pixelsPerSecond-20);wrap.scrollTop=Math.max(0,rowIndex*44);TV.paintProjection();}
  TV.showDetail(displayed);
};

RS.init = () => {
  $("#btn-calc").addEventListener("click", RS.runCalc);
  $("#btn-exp-json").addEventListener("click", () => RS.exportResult("json"));
  $("#btn-exp-xlsx").addEventListener("click", () => RS.exportResult("xlsx"));
  $("#btn-exp-csv").addEventListener("click", () => RS.exportResult("csv"));
  RS.renderAll();
};
