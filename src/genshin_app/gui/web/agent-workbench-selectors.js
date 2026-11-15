"use strict";

// Pure read models over an immutable AgentWorkbenchStore session snapshot.
// This module owns no transport, acknowledgement, command, DOM, candidate or
// App-action authority.  Candidate and action data remain compact references.
(function (global) {
  const AXES = Object.freeze({
    credential_state: new Set(["missing", "available", "saved", "error"]),
    provider_state: new Set(["unverified", "verified", "offline", "rate_limited", "auth_failed", "error"]),
    runtime_state: new Set(["cold", "starting", "ready", "reconnecting", "unhealthy", "stopping", "stopped", "failed"]),
    conversation_state: new Set(["cold", "opening", "idle", "running", "action_required", "recovery_required", "archived"]),
    turn_state: new Set(["none", "preparing", "streaming", "using_tool", "awaiting_input_live", "awaiting_input_suspended", "resuming", "cancelling", "completed", "cancelled", "failed", "interrupted"]),
    document_state: new Set(["clean", "dirty", "candidate_ready", "reviewing", "applying", "restore_preview", "restoring_checkpoint", "conflict", "outcome_unknown"]),
    inbox_state: new Set(["empty", "queued", "held", "needs_rebind", "promoting", "outcome_unknown"]),
  });
  const LABELS = Object.freeze({
    credential_state: {missing:"未配置", available:"本次可用", saved:"已安全保存", error:"凭据异常"},
    provider_state: {unverified:"未核验", verified:"已核验", offline:"网络离线", rate_limited:"服务限流", auth_failed:"认证失败", error:"服务异常"},
    runtime_state: {cold:"待命", starting:"启动中", ready:"就绪", reconnecting:"重新连接中", unhealthy:"运行异常", stopping:"停止中", stopped:"已停止", failed:"启动失败"},
    conversation_state: {cold:"尚未打开", opening:"打开中", idle:"待命", running:"处理中", action_required:"需要你处理", recovery_required:"需要恢复", archived:"已归档"},
    turn_state: {none:"无运行轮次", preparing:"准备请求", streaming:"生成中", using_tool:"使用工具", awaiting_input_live:"等待回答", awaiting_input_suspended:"已安全暂停", resuming:"继续中", cancelling:"取消中", completed:"已完成", cancelled:"已取消", failed:"失败", interrupted:"已中断"},
    document_state: {clean:"已保存", dirty:"有未保存修改", candidate_ready:"有方案待确认", reviewing:"确认中", applying:"应用中", restore_preview:"恢复预览", restoring_checkpoint:"恢复中", conflict:"文档发生冲突", outcome_unknown:"结果尚未确认"},
    inbox_state: {empty:"无排队消息", queued:"已排队", held:"暂缓发送", needs_rebind:"需要重绑定", promoting:"正在接纳", outcome_unknown:"结果未知"},
  });
  const AXIS_META = Object.freeze([
    ["credential_state", "账号"], ["provider_state", "模型服务"],
    ["runtime_state", "本地 AI 服务"], ["conversation_state", "会话"],
    ["turn_state", "当前回复"], ["document_state", "当前文档"],
    ["inbox_state", "后续消息"],
  ]);
  const own = (value, key) => Boolean(value && typeof value === "object" && Object.hasOwn(value, key));
  const admitted = (axis, value) => typeof value === "string" && AXES[axis]?.has(value) ? value : null;
  const hostAxis = (axis, ...sources) => {
    for (const source of sources) if (own(source, axis)) return admitted(axis, source[axis]);
    return null;
  };
  const hostText = (key, ...sources) => {
    for (const source of sources) if (own(source, key)) return typeof source[key] === "string" && source[key] ? source[key] : null;
    return null;
  };
  const turnFromLegacy = session => {
    if (!session) return "none";
    const state = String(session.state || "").toLowerCase();
    if (["starting", "preparing"].includes(state)) return "preparing";
    if (state === "running") return "streaming";
    if (["stopping", "cancelling"].includes(state)) return "cancelling";
    if (["completed", "complete", "finished"].includes(state)) return "completed";
    if (["stopped", "cancelled"].includes(state)) return "cancelled";
    if (["failed", "error"].includes(state)) return "failed";
    if (state === "interrupted") return "interrupted";
    return session.active ? "streaming" : "none";
  };
  const conversationFromVisibleFacts = (session, facts) => {
    if (!session) return "idle";
    if (session.applicationRecoveryPending || session.coldPendingApplication || session.lastAppliedRestorePending) return "recovery_required";
    if (facts.unsettledAppAction || facts.pendingApplication || session.candidate && !session.applied) return "action_required";
    if (session.active) return "running";
    if (!session.live) return "cold";
    return "idle";
  };
  const documentFromVisibleFacts = (session, facts) => {
    if (facts.applicationOutcomeUnknown) return "outcome_unknown";
    if (facts.documentConflict) return "conflict";
    if (facts.applyingCandidate) return "applying";
    if (session?.candidate && !session.applied) return "candidate_ready";
    if (facts.documentDirty === true) return "dirty";
    if (facts.documentDirty === false) return "clean";
    return null;
  };
  function actionAvailability(projection, client = {}) {
    const axes=projection?.axes||{},terminal=["none","completed","cancelled","failed","interrupted"].includes(axes.turn_state),configured=axes.provider_state==="verified"&&client.selection_ready!==false&&!["missing","error"].includes(axes.credential_state),runtimeUsable=axes.runtime_state===null||axes.runtime_state==="ready"||axes.runtime_state==="cold"&&client.runtime_lazy_start===true,domainClear=!["action_required","recovery_required"].includes(axes.conversation_state)&&!["applying","conflict","outcome_unknown"].includes(axes.document_state)&&!["held","needs_rebind","promoting","outcome_unknown"].includes(axes.inbox_state);
    return Object.freeze({send:configured&&runtimeUsable&&terminal&&domainClear,stop:!terminal,queue:false,steer:false,checkpoint:false});
  }
  function label(axis, value) {
    if (value === null || value === undefined) return "未知";
    return LABELS[axis]?.[value] || "未知";
  }
  function project(input = {}) {
    const client = input.client && typeof input.client === "object" ? input.client : {};
    const session = input.session && typeof input.session === "object" ? input.session : null;
    const host = input.host && typeof input.host === "object" ? input.host : {};
    const sessionHost = session?.ui_state && typeof session.ui_state === "object" ? session.ui_state : {};
    const facts = input.facts && typeof input.facts === "object" ? input.facts : {};
    const connectionSources = client.connection_matches === false ? [] : [host];
    const credential = client.connection_matches === false ? null
      : hostAxis("credential_state", ...connectionSources)
        || (client.credential?.status === "saved" || client.credential?.saved === true ? "saved"
          : client.credential?.status === "error" ? "error"
          : client.connection === "ready" ? "available" : null);
    const provider = hostAxis("provider_state", ...connectionSources)
      || (client.connection === "ready" ? "verified"
        : client.connection === "connecting" ? "unverified"
        : client.connection === "error" ? "error" : "unverified");
    const runtime = hostAxis("runtime_state", ...connectionSources);
    const conversation = hostAxis("conversation_state", sessionHost, host) || conversationFromVisibleFacts(session, facts);
    const turn = hostAxis("turn_state", sessionHost, host) || turnFromLegacy(session);
    const document = hostAxis("document_state", sessionHost, host) || documentFromVisibleFacts(session, facts);
    const inbox = hostAxis("inbox_state", sessionHost, host);
    const axes = {credential_state:credential,provider_state:provider,runtime_state:runtime,conversation_state:conversation,turn_state:turn,document_state:document,inbox_state:inbox};
    const summary = turn && !["none", "completed", "cancelled"].includes(turn)
      ? label("turn_state", turn)
      : conversation === "action_required" ? "有待处理事项"
      : provider === "verified" && client.selection_ready !== false && (runtime === "ready" || runtime === null || runtime === "cold" && client.runtime_lazy_start === true) ? "已配置，可发送"
      : provider === "verified" && client.selection_ready === false ? "请选择模型"
      : provider === "verified" ? "服务已核验" : "等待配置";
    const projection = {axes,summary,runtime_incarnation:hostText("runtime_incarnation", ...connectionSources),runtime_health:hostText("runtime_health", ...connectionSources),failure_code:hostText("failure_code", ...connectionSources)||hostText("runtime_failure_code", ...connectionSources)};
    projection.actions = actionAvailability(projection, client);
    return projection;
  }
  function axisRows(projection) {
    const axes = projection?.axes || {};
    return AXIS_META.map(([id, title]) => ({id, title, value: admitted(id, axes[id]), label: label(id, axes[id])}));
  }
  const itemTurnId = item => typeof item?.run_id === "string" && item.run_id ? item.run_id : "history";
  function turnOutline(session) {
    const order=[],counts=new Map(),items=[...(session?.messages||[]).map(item=>["messages",item]),...(session?.tools||[]).map(item=>["tools",item])];
    items.sort((a,b)=>Number.isFinite(a[1]?.seq)&&Number.isFinite(b[1]?.seq)?a[1].seq-b[1].seq:Number.isFinite(a[1]?.seq)?1:Number.isFinite(b[1]?.seq)?-1:0);
    for(const [kind,item] of items){const id=itemTurnId(item);if(!counts.has(id)){counts.set(id,{id,messages:0,tools:0});order.push(id);}counts.get(id)[kind]++;}
    return order.map((id,index)=>({...counts.get(id),label:id==="history"?"历史记录":`轮次 ${index+1}`,active:id!=="history"&&id===session?.run_id&&session?.active===true}));
  }
  const SEARCH_CACHE = new WeakMap();
  const searchableText = item => {
    if (!item || typeof item !== "object") return "";
    const revision = Number.isSafeInteger(item.content_revision) ? item.content_revision : null;
    const cached = revision === null ? null : SEARCH_CACHE.get(item);
    if (cached?.revision === revision) return cached.text;
    const text = [item.text,item.name,item.message,item.role].filter(value=>typeof value==="string").join("\n").toLocaleLowerCase("zh-CN");
    if (revision !== null) SEARCH_CACHE.set(item,{revision,text});
    return text;
  };
  function visible(item, uiState = {}) {
    const selectedTurn=uiState.turn_id||null;if(selectedTurn&&itemTurnId(item)!==selectedTurn)return false;
    const query=String(uiState.query||"").trim().toLocaleLowerCase("zh-CN");return !query||searchableText(item).includes(query);
  }
  function trajectory(session, uiState = {}) {
    const rows=[];let sourceOrder=0;
    for(const message of session?.messages||[])if(visible(message,uiState))rows.push({id:`message:${message.id}`,seq:Number.isFinite(message.seq)?message.seq:null,run_id:itemTurnId(message),kind:"message",title:message.reasoning?"推理过程":message.role==="user"?"用户消息":"Agent 回复",detail:message.text||"",state:null,reasoning:message.reasoning===true,sourceOrder:sourceOrder++});
    for(const tool of session?.tools||[])if(visible(tool,uiState))rows.push({id:`tool:${tool.id}`,seq:Number.isFinite(tool.seq)?tool.seq:null,run_id:itemTurnId(tool),kind:"tool",title:tool.name||"工具",detail:tool.message||"",state:tool.state||null,sourceOrder:sourceOrder++});
    rows.sort((a,b)=>a.seq===null&&b.seq===null?a.sourceOrder-b.sourceOrder:a.seq===null?1:b.seq===null?-1:a.seq-b.seq||a.id.localeCompare(b.id));
    return rows.map(({sourceOrder:_sourceOrder,...row})=>row);
  }
  const createUiState = () => ({view:"chat", query:"", turn_id:null});
  const deepFreeze = value => {
    if (!value || typeof value !== "object" || Object.isFrozen(value)) return value;
    for (const child of Object.values(value)) deepFreeze(child);
    return Object.freeze(value);
  };
  const safeInt = value => Number.isSafeInteger(value) && value >= 0;
  const stringOrNull = (value, limit) => typeof value === "string" && value.length <= limit ? value : null;
  const messageCache = new WeakMap(), toolCache = new WeakMap();
  const sessionCache = new WeakMap(), chatCache = new WeakMap(), outlineCache = new WeakMap();
  const trajectoryCache = new WeakMap();

  function resolveSnapshot(value, sessionId = null) {
    if (!value || typeof value !== "object" || !Object.isFrozen(value)) return null;
    if (value.sessions && typeof value.sessions === "object") {
      if (typeof sessionId !== "string" || !sessionId) return null;
      value = value.sessions[sessionId] || null;
    }
    return value && typeof value === "object" && Object.isFrozen(value)
      && value.contract_version === "legacy_agent_poll_v1"
      && safeInt(value.snapshot_revision) ? value : null;
  }

  function messageRow(row) {
    if (!row || typeof row !== "object") return null;
    const cached = messageCache.get(row); if (cached) return cached;
    const id = stringOrNull(row.message_id, 256); if (!id) return null;
    const result = deepFreeze({
      key:`message:${id}`, id, message_id:id,
      role:row.role === "user" || row.role === "assistant" ? row.role : null,
      text:stringOrNull(row.text, 100000) ?? "",
      reasoning:id.startsWith("reasoning:"),
      run_id:stringOrNull(row.run_id, 256),
      seq:safeInt(row.last_source_seq) ? row.last_source_seq : null,
      content_revision:safeInt(row.content_revision) ? row.content_revision : 0,
      source:row.source === "baseline" ? "baseline" : "event",
    });
    messageCache.set(row, result); return result;
  }

  function toolRow(row) {
    if (!row || typeof row !== "object") return null;
    const cached = toolCache.get(row); if (cached) return cached;
    const id = stringOrNull(row.tool_id, 256); if (!id) return null;
    const result = deepFreeze({
      key:`tool:${id}`, id, tool_id:id,
      name:stringOrNull(row.name, 256), state:stringOrNull(row.state, 64),
      message:stringOrNull(row.message, 1000), run_id:stringOrNull(row.run_id, 256),
      seq:safeInt(row.last_source_seq) ? row.last_source_seq : null,
      content_revision:safeInt(row.content_revision) ? row.content_revision : 0,
      source:row.source === "baseline" ? "baseline" : "event",
    });
    toolCache.set(row, result); return result;
  }

  function candidateRef(value) {
    if (!value || typeof value !== "object") return null;
    return deepFreeze({
      revision:safeInt(value.revision) ? value.revision : null,
      ready:value.ready === true, stale:value.stale === true,
      receipt_fingerprint:stringOrNull(value.receipt_fingerprint, 256),
      issues_count:safeInt(value.issues_count) ? value.issues_count : null,
    });
  }

  function actionRef(value) {
    if (!value || typeof value !== "object") return null;
    const actionId = stringOrNull(value.action_id, 256); if (!actionId) return null;
    return deepFreeze({
      key:`app-action:${actionId}`, action_id:actionId,
      action:stringOrNull(value.action, 128), status:stringOrNull(value.status, 128),
      execution_mode:stringOrNull(value.execution_mode, 64),
      candidate_revision:safeInt(value.candidate_revision) ? value.candidate_revision : null,
      stage_revision:safeInt(value.stage_revision) ? value.stage_revision : null,
      execution_outcome_unknown:value.execution_outcome_unknown === true,
    });
  }

  function applicationRef(value) {
    if (!value || typeof value !== "object") return null;
    return deepFreeze({
      status:stringOrNull(value.status, 128),
      recovery_required:value.recovery_required === true,
      deferred_while_active:value.deferred_while_active === true,
    });
  }

  function baselineRef(value) {
    if (!value || typeof value !== "object") return null;
    const completeness = typeof value.completeness === "string"
      ? value.completeness
      : value.completeness && typeof value.completeness === "object"
        ? Object.fromEntries(["messages", "tools", "usage", "budget_statuses", "app_action_refs"]
          .filter(key => typeof value.completeness[key] === "string")
          .map(key => [key, value.completeness[key]])) : null;
    return deepFreeze({
      baseline_seq:safeInt(value.baseline_seq) ? value.baseline_seq : null,
      completeness,
      partial_reasons:(value.partial_reasons || []).filter(reason => typeof reason === "string").slice(0, 64),
    });
  }

  function selectSession(value, sessionId = null) {
    const snapshot = resolveSnapshot(value, sessionId); if (!snapshot) return null;
    const cached = sessionCache.get(snapshot); if (cached) return cached;
    const source = snapshot.source || {}, raw = snapshot.session || {}, connection = snapshot.connection || {};
    const messages = (snapshot.messages || []).map(messageRow).filter(Boolean);
    const tools = (snapshot.tools || []).map(toolRow).filter(Boolean);
    const actions = (snapshot.app_action_refs || []).map(actionRef).filter(Boolean);
    const candidate = candidateRef(snapshot.candidate_ref), application = applicationRef(snapshot.application_ref);
    const budgetRows = (snapshot.budget_statuses || []).map(row => deepFreeze({
      status:stringOrNull(row?.status, 128), level:stringOrNull(row?.level, 64),
      percent:Number.isFinite(row?.percent) ? row.percent : null,
      message:stringOrNull(row?.message, 1000), run_id:stringOrNull(row?.run_id, 256),
      seq:safeInt(row?.last_source_seq) ? row.last_source_seq : null,
    })).filter(row => row.status);
    const budgetStatuses = Object.fromEntries(budgetRows.map(row => [row.status, row]));
    const result = deepFreeze({
      session_id:stringOrNull(source.session_id, 128), cursor:safeInt(source.next_seq) ? source.next_seq : 0,
      store_revision:snapshot.store_revision, snapshot_revision:snapshot.snapshot_revision,
      projectionRevision:snapshot.snapshot_revision, partial:snapshot.partial === true,
      run_id:stringOrNull(raw.run_id, 256), state:stringOrNull(raw.state, 64),
      terminal_status:stringOrNull(raw.terminal_status, 64), active:raw.active === true,
      kind:stringOrNull(raw.kind, 64), workflow:stringOrNull(raw.workflow, 64), title:stringOrNull(raw.title, 1000),
      model:stringOrNull(raw.model, 256), effort:stringOrNull(raw.effort, 64),
      providerId:stringOrNull(raw.provider_id, 256), connectionKind:stringOrNull(raw.connection_kind, 64),
      canResume:raw.can_resume === true, historyTruncated:raw.history_truncated === true,
      messages, tools, usage:snapshot.usage || null,
      budget_statuses:budgetRows, budgetStatuses,
      candidate_ref:candidate, candidate,
      app_action_refs:actions, appActions:actions,
      application_ref:application, baseline_ref:baselineRef(snapshot.baseline_ref),
      applicationRecoveryPending:application?.recovery_required === true,
      coldPendingApplication:application?.recovery_required === true,
      ui_state:deepFreeze({
        conversation_state:stringOrNull(raw.conversation_state, 64),
        turn_state:stringOrNull(raw.turn_state, 64),
        document_state:stringOrNull(raw.document_state, 64),
        inbox_state:stringOrNull(raw.inbox_state, 64),
      }),
      diagnostics:(snapshot.diagnostics || []).filter(value => typeof value === "string").slice(0, 256),
    });
    sessionCache.set(snapshot, result); return result;
  }

  function selectChat(value, sessionId = null) {
    const snapshot = resolveSnapshot(value, sessionId); if (!snapshot) return null;
    const cached = chatCache.get(snapshot); if (cached) return cached;
    const session = selectSession(snapshot);
    const result = deepFreeze({
      session_id:session.session_id, snapshot_revision:session.snapshot_revision,
      messages:session.messages, tools:session.tools,
    });
    chatCache.set(snapshot, result); return result;
  }

  function selectTurnOutline(value, sessionId = null) {
    const snapshot = resolveSnapshot(value, sessionId); if (!snapshot) return Object.freeze([]);
    const cached = outlineCache.get(snapshot); if (cached) return cached;
    const rows = deepFreeze(turnOutline(selectSession(snapshot)));
    outlineCache.set(snapshot, rows); return rows;
  }

  function selectTrajectory(value, uiState = {}, sessionId = null) {
    const snapshot = resolveSnapshot(value, sessionId); if (!snapshot) return Object.freeze([]);
    const query = String(uiState?.query || "").slice(0, 1000), turnId = stringOrNull(uiState?.turn_id, 256);
    const key = JSON.stringify([query, turnId]); let cache = trajectoryCache.get(snapshot);
    if (!cache) { cache = new Map(); trajectoryCache.set(snapshot, cache); }
    if (cache.has(key)) return cache.get(key);
    const rows = deepFreeze(trajectory(selectSession(snapshot), {query, turn_id:turnId}));
    if (cache.size >= 32) cache.delete(cache.keys().next().value);
    cache.set(key, rows); return rows;
  }

  function selectWorkbench(value, input = {}, sessionId = null) {
    const snapshot = resolveSnapshot(value, sessionId); if (!snapshot) return null;
    const session = selectSession(snapshot), connection = snapshot.connection || {};
    const projection = deepFreeze(project({
      client:input.client || {}, session,
      host:{...(input.host || {}), ...connection}, facts:input.facts || {},
    }));
    return deepFreeze({
      session, projection, axes:projection.axes, actions:projection.actions,
      axis_rows:axisRows(projection),
      turn_outline:selectTurnOutline(snapshot),
      trajectory:selectTrajectory(snapshot, input.ui_state || {}),
      candidate_ref:session.candidate_ref, app_action_refs:session.app_action_refs,
      application_ref:session.application_ref, baseline_ref:session.baseline_ref,
    });
  }

  const namespace = Object.freeze({
    createUiState, project, actionAvailability, label, axisRows, turnOutline,
    visible, trajectory,
    selectSession, selectChat, selectTurnOutline, selectTrajectory, selectWorkbench,
  });
  global.AgentWorkbenchSelectors = namespace;
  // One compatibility cycle for external editor modules that still read the
  // historic namespace.  There is no second implementation or state owner.
  global.AgentWorkbench = namespace;
})(window);
