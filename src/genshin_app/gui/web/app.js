// 应用外壳：页签切换 + 桥连通 + 启动排轴编辑器（RT）
"use strict";

const $ = (s) => document.querySelector(s);

function setStatus(msg, details) {
  if (window.WS?.statusCenter?.publishLegacy) {
    window.WS.statusCenter.publishLegacy(msg, details);
    window.WS?.refresh?.();
    return;
  }
  const node = $("#status-bar"); node.innerHTML = "";
  const original = String(msg ?? "");
  const text = DS.prose(original, "操作信息包含未翻译内容，请展开查看原文");
  const body = document.createElement("span"); body.textContent = text; node.appendChild(body);
  if (text !== original) node.appendChild(DS.raw("操作信息", original));
  if (details != null) node.appendChild(DS.raw("操作详情", details));
  window.WS?.refresh?.();
}

async function callApi(method, ...args) {
  const sensitive = method === "agent_connect" && args[0] && typeof args[0] === "object"
    ? args[0] : null;
  try {
    if (JSONTransport.usesMethod(method)) {
      const encoded = JSONTransport.encode(args);
      const response = JSONTransport.parse(await window.pywebview.api.document_json_call(method, encoded.text));
      if (encoded.warnings.length) {
        response.transport_warnings = encoded.warnings;
        setStatus(encoded.warnings.join("；"));
      }
      return response;
    }
    const pending = window.pywebview.api[method](...args);
    if (sensitive && Object.prototype.hasOwnProperty.call(sensitive, "api_key")) {
      sensitive.api_key = "";
      args[0] = null;
    }
    return await pending;
  }
  catch (e) {
    if (sensitive) {
      // A bridge/serializer exception may contain the complete request object.
      // Never project that exception into renderer state for credential calls.
      return {
        code: 1,
        message: "Agent 凭据接口调用失败",
        details: {method, error: "agent_connect_failed"},
      };
    }
    return { code: 1, message: `操作接口调用失败：${e.message || e}`, details:{method, error:String(e.message || e)} };
  }
  finally {
    if (sensitive && Object.prototype.hasOwnProperty.call(sensitive, "api_key")) sensitive.api_key = "";
    if (method === "agent_connect") args[0] = null;
  }
}

function applyAppIdentity(identity) {
  window.APP_IDENTITY = identity && typeof identity === "object" ? {...identity} : null;
  const mode = String(identity?.mode || "");
  const channel = String(identity?.build_channel || mode);
  const development = channel === "development_acceptance";
  const providerAcceptance = channel === "provider_acceptance";
  const nativeAgentDevelopment = mode === "native_agent_development" || channel === "native_agent_development";
  const banner = $("#development-acceptance-banner");
  const badge = $("#app-build-badge");
  if (!development && !providerAcceptance && !nativeAgentDevelopment) {
    banner?.classList.add("hidden");
    badge?.classList.add("hidden");
    return;
  }
  const label = String(identity?.label || (nativeAgentDevelopment ? "DeepSeek原生Agent开发版" : "本地开发验收版"));
  const networkMode = String(identity?.network_mode || identity?.network_policy || "unknown");
  const hostNetworkPolicy = String(identity?.host_network_policy || "");
  const agentNetworkPolicy = String(identity?.agent_network_policy || "");
  const networkDisabled = networkMode === "disabled";
  const serviceSkipped = identity?.service_mode === "skipped_external_database";
  const nativeHostSummary = hostNetworkPolicy === "app_and_database_remote_disabled"
    ? "宿主App的更新检查和数据库联网已关闭。"
    : "宿主App使用受限网络策略。";
  const nativeAgentSummary = agentNetworkPolicy === "deepseek_official_llm_search_public_http_fetch"
    ? "原生Agent的模型请求与web_search访问受审DeepSeek官方服务；web_fetch可访问符合公开地址约束的HTTP(S)目标。"
    : "原生Agent仅可使用受审联网能力。";
  if (badge) {
    badge.textContent = label;
    badge.classList.remove("hidden");
  }
  if (banner) {
    if (nativeAgentDevelopment) {
      $("#development-acceptance-title").textContent =
        `${label} · 宿主受限，Agent联网研究开启`;
      $("#development-acceptance-message").textContent =
        nativeHostSummary + nativeAgentSummary +
        `联网操作可能产生真实费用。` +
        `API Key只能在Agent设置中输入。` +
        `${serviceSkipped ? "共享资料库以只读方式引用。" : ""}`;
    } else if (providerAcceptance) {
      $("#development-acceptance-title").textContent =
        `${label} · 真实网络请求与费用`;
      $("#development-acceptance-message").textContent =
        `连接、发送和联网研究会访问受审的 DeepSeek 官方端点，并可能产生真实费用。` +
        `API Key 只能在 Agent 设置中输入；App更新与数据库更新已关闭。` +
        `${serviceSkipped ? "共享资料库以只读方式引用。" : ""}`;
    } else {
      $("#development-acceptance-title").textContent =
        `${label} · ${networkDisabled ? "外网禁用" : "网络策略未确认"}`;
      $("#development-acceptance-message").textContent =
        `本轮仅用于无密钥验收，请勿输入真实 API Key。` +
        `${serviceSkipped ? "资料读取使用显式数据库；旧CLI Agent资料查询通道未启动。" : ""}`;
    }
    banner.classList.toggle("provider-live", providerAcceptance || nativeAgentDevelopment);
    banner.classList.toggle("network-unconfirmed", development && !networkDisabled);
    banner.classList.remove("hidden");
  }
}

async function refreshPing() {
  try {
    const r = await window.pywebview.api.ping();
    $("#ping-status").textContent =
      r.code === 0 ? `已连接 · 版本 ${r.data.version}` : `连接异常：${DS.prose(r.message)}`;
    const identity = r.code === 0 ? r.data.app_identity : null;
    applyAppIdentity(identity);
    return identity;
  } catch (e) {
    $("#ping-status").textContent = "连接不可用，请重新打开应用";
    applyAppIdentity(null);
    return null;
  }
}

function bindTabs() {
  document.querySelectorAll(".tab").forEach((btn) => {
    btn.addEventListener("click", () => {
      if (btn.dataset.tab === "agent" && window.AgentWorkbenchApp) {
        window.AgentWorkbenchApp.open();
        return;
      }
      const previousTab = document.querySelector(".tab.active")?.dataset.tab;
      if(btn.dataset.tab==="result")window.RS?.enterReport(previousTab);
      if (btn.dataset.tab === "agent" && previousTab && previousTab !== "agent") {
        document.body.dataset.agentReturnTab = previousTab;
      }
      document.querySelectorAll(".tab").forEach((b) => b.classList.remove("active"));
      document.querySelectorAll(".panel").forEach((p) => p.classList.remove("active"));
      btn.classList.add("active");
      $(`#tab-${btn.dataset.tab}`).classList.add("active");
      document.dispatchEvent?.(new Event("workspacechange"));
      if (btn.dataset.tab === "rotation" && window.RT) RT.renderAll();
      if (btn.dataset.tab === "result" && window.RS) RS.renderAll();
      if (btn.dataset.tab === "whiteboard" && window.WB) WB.render();
      window.AgentWorkbenchApp?.documentChanged?.();
      window.WS?.refresh?.();
    });
  });
}

function bindSharedDisclosureSurfaces() {
  if (!window.AppSurfaceKernel?.bindDisclosure) return;
  const bindings = [
    {details: "#workspace-status-hub", panel: "#workspace-notice-list", label: "页面状态与最近动态"},
    {details: ".workspace-plan-state", panel: "#plan-status", label: "当前方案状态", align: "start"},
    {details: "#data-tools-panel", panel: ".data-tools-popover", label: "资料筛选与工具"},
    {details: ".axis-help", panel: ".axis-help-panel", label: "时间轴操作与说明", above: true},
  ];
  for (const item of bindings) {
    const details = $(item.details), panel = $(item.panel);
    if (!details || !panel || details.appSurfaceController) continue;
    AppSurfaceKernel.bindDisclosure(details, {panel, kind: "popover", ariaLabel: item.label, align: item.align, above: item.above,
      dismissOnOutside: true, dismissOnScroll: true});
  }
}

function reportAppStartupFailure(message, details) {
  setStatus(message, details);
  const editor = document.getElementById("genshin-legacy-editor");
  if (editor && !editor.hidden && editor.parentNode?.id !== "genshin-editor-parking") return;
  // When the DSH shell itself cannot boot, the editor footer is still parked
  // and cannot report the failure. Leave a readable error in the visible shell
  // without replacing its own diagnostic or pretending an import succeeded.
  const shell = document.getElementById("dsh-app-root");
  if (!shell) return;
  const notice = document.createElement("section");
  notice.setAttribute("role", "alert");
  const heading = document.createElement("p"); heading.textContent = message;
  notice.append(heading);
  if (details) {
    const diagnostic = document.createElement("pre");
    diagnostic.textContent = JSON.stringify(details, null, 2); notice.append(diagnostic);
  }
  shell.append(notice);
}

async function openStartupFile(showEditor) {
  let path = null;
  let phase = "startup_request";
  try {
    const r = await callApi("get_startup_file");
    path = r.code === 0 ? r.data?.path : null;
    if (r.code === 0 && path == null) return;
    // A legacy tab click only changes a hidden editor island. Wait for the
    // actual DSH layout owner, then reuse the mounted-page ACK before loading.
    // No file means no navigation and no new document on an ordinary launch.
    phase = "workbench_boot";
    const ready = window.GenshinDshClient?.ready;
    if (!ready || typeof ready.then !== "function") throw new Error("原生工作台尚未提供启动就绪状态");
    await ready;
    if (r.code !== 0) { setStatus(r.message, r.details); return; }
    const intent=window.AppDocumentOpenIntents.begin();
    phase = "file_inspection";
    const inspected=await callApi("inspect_plan_file",path);
    if (inspected.code !== 0) { setStatus(inspected.message, inspected.details); return; }
    phase = "document_import";
    const opened=await window.GenshinBusinessUI.openInspectedDocument(path,inspected.data,intent,{board:WB,editor:RT,report:RS,show:showEditor});
    setStatus(opened.message,opened.details);
  } catch (e) {
    const message = phase === "workbench_boot"
      ? "工作台未能启动，尚未读取方案文件。请保留诊断信息；这不表示方案已损坏。"
      : phase === "document_import" ? "方案导入未完成，请查看下方原因。"
      : "未能读取启动方案，请查看文件路径和下方原因。";
    reportAppStartupFailure(message, {phase, path, error:String(e.message || e)});
  }
}

async function initializeEditor() {
  try {
  const appIdentity = await refreshPing();
  const networkMode = String(appIdentity?.network_mode || appIdentity?.network_policy || "");
  const networkDisabled = networkMode === "disabled";
  const providerOnly = ["provider_acceptance", "native_agent_development"].includes(appIdentity?.mode) && networkMode === "deepseek_provider_only";
  const hostNetworkRestricted = providerOnly ||
    appIdentity?.host_network_policy === "app_and_database_remote_disabled";
  if ((networkDisabled || hostNetworkRestricted) && window.DV) {
    // Acceptance launchers suppress ordinary App/database update traffic.
    // The full native Agent separately permits reviewed DeepSeek Search and
    // public-address HTTP(S) Fetch; development acceptance permits no provider traffic.
    DV.checkDatabaseUpdate = async () => {
      const node = $("#db-update-status");
      if (node) node.textContent = providerOnly
        ? "真实API联调：App与数据库更新已禁用"
        : "开发验收模式：外网更新检查已禁用";
    };
  }
  bindTabs();
  bindSharedDisclosureSurfaces();
  await RT.init();
  await callApi("editor_initialized");
  RS.init();
  $("#timeline-calculate")?.addEventListener("click", () => RS.runCalc());
  $("#timeline-team")?.addEventListener("click", e => {
    if(window.WS){WS.toggle("rotation","left");return;}
    const collapsed = $("#rotation-main").classList.toggle("team-collapsed");
    e.currentTarget.setAttribute("aria-pressed", String(!collapsed));
    window.TV?.requestPaint?.();
  });
  if (window.PL) await PL.init();
  if (window.ED) await ED.init();
  if (window.RC) await RC.init();
  if (window.MC) await MC.init();
  await DV.init();
  window.WS?.init?.();
  if (window.WB) await WB.init();
  // App-specific editor adapter only: no legacy Agent Session/message owner.
  const editorRoot=document.getElementById("genshin-legacy-editor");
  let pendingNavigation;
  const showEditor=page=>new Promise((resolve,reject)=>{
    pendingNavigation?.fail("页面已切换。");
    const isVisible=()=>!editorRoot.closest("[hidden]")&&WS.tab()===page;
    if(isVisible()){resolve();return;}
    const finish=error=>{
      if(pendingNavigation!==request)return;
      pendingNavigation=undefined;clearTimeout(timer);
      document.removeEventListener("genshin-editor-visible",visible);
      document.removeEventListener("genshin-open-assistant",interrupted);
      if(error)reject(new Error(error));else resolve();
    };
    const visible=event=>{if(event.detail?.page===page&&isVisible())finish();};
    const interrupted=()=>finish("已返回对话。");
    const request={fail:finish};pendingNavigation=request;
    const timer=setTimeout(()=>finish("对应页面没有打开，请从左侧手动切换。"),15000);
    document.addEventListener("genshin-editor-visible",visible);
    document.addEventListener("genshin-open-assistant",interrupted);
    document.dispatchEvent(new CustomEvent("genshin-open-editor",{detail:{page,onUnavailable:()=>finish("当前工作区不支持此页面。")}}));
  });
  window.AppAppliedResultSave=async(conversationId,board,calculated,isCurrent=()=>true)=>{
    let response;
    try{response=await callApi("dsh_client_saved_whiteboard",{conversation_id:conversationId,command:"current_delivery",arguments:{board,calculated}});}
    catch(error){return {code:1,message:error instanceof Error?error.message:"无法读取当前计算交付，尚未执行保存。",details:{publication_state:"not_written",published_paths:[],unknown_paths:[]}};}
    // This read-only delivery lookup happens before any picker or file writer.
    // Mark that boundary explicitly so callers do not confuse it with a lost
    // acknowledgement after an actual write.
    if(response.code!==0)return {...response,details:{...(response.details||{}),publication_state:"not_written",published_paths:[],unknown_paths:[]}};
    if(!isCurrent())return {code:1,message:"白板或计算结果已变化，未打开保存对话框。",details:{publication_state:"not_written",published_paths:[],unknown_paths:[]}};
    const data=response.data;
    return callApi("dsh_client_save_whiteboard_result",{conversation_id:conversationId,delivery_capability:data.delivery_capability,
      expected_revision:data.revision,expected_receipt:data.receipt_fingerprint});
  };
  window.GenshinEditorBridge=Object.freeze({
    root:editorRoot,
    navigate(page){WS.navigate(page);},
    show:showEditor,
    document(){
      if(WS.isBoard()&&window.WB?.state?.board)return {document_kind:"whiteboard",document:UI.clone(WB.state.board)};
      if(WS.isPlan()&&window.PL?.state?.plan)return {document_kind:"native",document:UI.clone(PL.state.plan)};
      return {document_kind:null,document:null};
    },
    setTheme(preference,resolved){editorRoot.dataset.theme=preference;editorRoot.dataset.themeResolved=resolved;window.ThemeController?.accept?.({preference,resolved});},
    projectCandidate(document,kind,calculated,metadata){return window.CandidateDocumentProjection.build(document,calculated,metadata);},
    async revealTimelineEvent(selection){
      const business=window.GenshinBusinessUI;
      const navigator=business.createTimelineNavigator({
        capture(){
          const board=window.WB?.state.board,boardResult=window.WB?.state.result;
          const boardOwner=board&&business.timelineDocumentOwner(board,"whiteboard");
          if(boardOwner&&boardResult&&selection?.owner===boardOwner){
            if(WB.state.resultProjectionOwner!==boardResult){WB.state.resultProjection=null;WB.state.resultProjectionOwner=boardResult;}
            WB.state.resultProjection ||= TD.build({plan:boardResult.plan,
              template:boardResult.plan?.resolved_template||boardResult.plan?.manual_template,result:boardResult.result,
              executionModel:boardResult.report?.mechanics_model,executionTemplate:boardResult.report?.mechanics_input_template,current:true});
            return {owner:boardOwner,projection:WB.state.resultProjection,document:board,result:boardResult,page:"whiteboard"};
          }
          const plan=window.PL?.state.plan,planOwner=plan&&business.timelineDocumentOwner(plan,"native");
          if(planOwner&&selection?.owner===planOwner&&window.RS?.isCurrent?.()){
            TV.refreshProjection();return {owner:planOwner,projection:TV.projection,document:plan,result:RS.state.result,page:"rotation"};
          }
          return null;
        },
        show:showEditor,
        select(snapshot,event){
          if(snapshot.page==="whiteboard"){
            WB.state.mode="damage";WB.state.damageFocus="all";WB.state.evidenceAction=false;WB.state.evidenceBack=null;WB.state.derivedBack=null;
            WB.state.scroll=Math.max(0,event.start*(WB.state.geometry?.pps||1)-80);WB.selectEvent(event);
            const rows=WB.project().lanes,row=rows.findIndex(item=>item.events?.some(member=>member.id===event.id));
            if(WB.state.wrap&&row>=0){WB.state.wrap.scrollTop=row*business.TIMELINE_VIEW_HEIGHT;WB.draw();}
          }else{
            TV.mode="damage";TV.characterFilter="";TV.sourceFilter="";TV.componentFilter="";TV.kindVisibility.damage={};TV.damageFocus="all";
            TV.rebuildRows();TV.paintProjection();const wrap=$("#timeline-wrap"),row=TV.rows.findIndex(item=>item.events?.some(member=>member.id===event.id));
            if(wrap){wrap.scrollLeft=Math.max(0,event.start*TV.pixelsPerSecond-80);if(row>=0)wrap.scrollTop=row*business.TIMELINE_VIEW_HEIGHT;TV.paintProjection();}
            TV.showDetail(event);
          }
        },
      });
      try{const response=await navigator.reveal(selection);setStatus(response.message);return response;}
      catch(error){setStatus(error.message||"无法定位这段伤害，白板未被修改。");throw error;}
    },
    application:window.CandidateEditorApplication.create(),
    files:window.AppEditorFileActions.create({root:editorRoot,show:showEditor}),
  });
  window.AgentWorkbenchApp=Object.freeze({
    open(){document.dispatchEvent(new Event("genshin-open-assistant"));},
    toggle(){if(document.dispatchEvent(new Event("genshin-toggle-assistant",{cancelable:true})))document.dispatchEvent(new Event("genshin-open-assistant"));},
  });
  // The retained business header must target the single DSH presentation.
  // Its former listener lived in the retired legacy Agent script.
  $("#agent-chat-toggle")?.addEventListener("click",()=>window.AgentWorkbenchApp.open());
  document.dispatchEvent(new Event("genshin-editor-ready"));
  // Run last so initialization cannot overwrite a file-loading error message.
  await openStartupFile(showEditor);
  } catch (e) {
    reportAppStartupFailure("界面初始化失败，请重新打开应用；若仍失败请保留以下详情", {error:String(e.message || e)});
  }
}
// WebView may inject its API and emit readiness before a cached app script is
// evaluated. Both load orders enter the same promise, so the later event cannot
// bind controls twice or consume the startup document a second time.
let editorInitialization = null;
function ensureEditorInitialized() {
  if (editorInitialization) return editorInitialization;
  if (typeof window.pywebview?.api?.ping !== "function") return;
  editorInitialization = initializeEditor();
  return editorInitialization;
}
window.addEventListener("pywebviewready", ensureEditorInitialized, {once:true});
void ensureEditorInitialized();
