(function (global) {
  "use strict";

  // Single presentation/document-session bridge for the Agent workbench.  It
  // projects Store/Host facts into the UI but owns no provider, candidate,
  // App-action, checkpoint or document mutation transaction.
  const requiredObject = (value, name) => {
    if (!value || typeof value !== "object") throw new TypeError(`workbench_presentation_${name}_missing`);
    return value;
  };
  const requiredFunction = (value, name) => {
    if (typeof value !== "function") throw new TypeError(`workbench_presentation_${name}_missing`);
    return value;
  };

  function create(options = {}) {
    const AC = requiredObject(options.agent, "agent");
    const windowObject = requiredObject(options.window, "window");
    const documentObject = requiredObject(options.document, "document");
    const ui = requiredObject(options.ui, "ui");
    const openExternal = requiredFunction(options.openExternal, "open_external");

    const renderAppActions = session => {
      const rows = (session?.appActions || []).filter(action =>
        ["queued", "resolving", "presented", "claimed_for_external_execution"].includes(action?.status)
        || action?.execution_outcome_unknown === true || action?.pendingSettlement || action?.externalExecutionClaimed);
      return AC.appActionRenderer?.render(session, rows);
    };

    const workbenchProjection = session => {
      const model = AC.readModel(session); let documentDirty = null;
      try { documentDirty = model?.kind === "native" ? Boolean(windowObject.PL?.dirty?.()) : Boolean(windowObject.WB?.dirty?.()); } catch {}
      const unsettled = AC.hasUnsettledAppAction(session), pending = AC.hasPendingApplication(session);
      const unknown = (session?.appActions || []).some(action => action.execution_outcome_unknown === true
        || action.status === "claimed_for_external_execution" && !action.pendingSettlement);
      const connectionMatches = AC.cliConnected() && AC.sessionProviderMatches(session);
      const selectedModel = model?.model
        ? {model:model.model, supportedReasoningEfforts:model.effort == null ? [] : [{reasoningEffort:model.effort}], defaultReasoningEffort:model.effort}
        : AC.state.models.find(row => row.model === AC.state.model);
      const selectionReady = connectionMatches && Boolean(selectedModel
        && AC.validEffort(selectedModel, model?.model ? model.effort : AC.state.effort));
      const selectedConnection = connectionMatches ? AC.state.connection : "disconnected";
      const input = {client:{connection:selectedConnection, credential:AC.state.credential, selection_ready:selectionReady, connection_matches:connectionMatches}, host:AC.state.hostUi, facts:{
        unsettledAppAction:unsettled, pendingApplication:pending, applicationOutcomeUnknown:unknown,
        applyingCandidate:["accept", "recover-application", "abort-application"].includes(AC.state.busy),
        documentConflict:Boolean(session?.base && !AC.currentMatches(session.base)), documentDirty,
      }, ui_state:AC.workbenchState(session)};
      return global.AgentWorkbenchSelectors.selectWorkbench(AC.storeSnapshot(session), input)?.projection
        || global.AgentWorkbenchView.project({...input, session:model});
    };

    const renderWorkbench = session => global.AgentWorkbenchView.render({
      nodes:AC.nodes, session:AC.readModel(session), projection:workbenchProjection(session),
      workbench:AC.workbenchState(session), busy:AC.state.busy, labels:AC.labels,
      onAllTurns:() => { AC.workbenchState(session).turn_id = null; renderWorkbench(session); renderMessages(session); },
      onSelectTurn:turnId => setWorkbenchTurn(turnId),
    });
    const setWorkbenchView = view => {
      if (!["chat", "trajectory"].includes(view)) return;
      AC.workbenchState().view = view;
      const session = AC.selected(); renderWorkbench(session);
      if (view === "trajectory" && session) void AC.loadTelemetry(session);
    };
    const setWorkbenchTurn = turnId => {
      const workbench = AC.workbenchState();
      workbench.turn_id = workbench.turn_id === turnId ? null : turnId;
      renderWorkbench(AC.selected()); renderMessages(AC.selected());
    };
    const renderStatusAxes = (session, projection = workbenchProjection(session)) =>
      global.AgentWorkbenchView.renderStatusAxes({host:AC.nodes.statusAxes, projection});
    const feedbackQuestionOpen = session => {
      const model = AC.readModel(session), turnState = model?.ui_state?.turn_state, foundation = AC.workbenchFoundation(session);
      return turnState === "awaiting_input_live" || turnState === "awaiting_input_suspended"
        || foundation?.questions?.some(row => row.state === "open") === true;
    };
    const observeSessionFeedback = (session, {transition=false, state=null, questionOpen=null}={}) => {
      if (!session || !AC.activityFeedback) return false;
      const model = AC.readModel(session), selected = AC.selected() === session;
      return AC.activityFeedback.observeSession({session_id:session.session_id, title:model?.title || session.title || "Agent 任务",
        state:state || model?.state || "idle", question_open:questionOpen === null ? feedbackQuestionOpen(session) : questionOpen,
        selected, panel_open:AC.state.open, transition});
    };
    const refreshActivityFeedback = (active, activeModel, projection, runtimeStarting, status) => {
      if (!AC.activityFeedback) return;
      const sessionId = active?.session_id || "runtime", failedTransport = !!active?.pollFailureActive || Number(active?.pollErrors || 0) > 0;
      const runningTool = [...(activeModel?.tools || [])].reverse().find(row =>
        ["queued", "pending", "starting", "running", "streaming", "using_tool", "in_progress"].includes(row?.state));
      const turnState = activeModel?.ui_state?.turn_state || projection?.axes?.turn_state || activeModel?.state;
      const state = failedTransport ? "reconnecting" : runtimeStarting ? "starting" : runningTool ? "using_tool" : turnState || "idle";
      const phase = failedTransport ? "event_stream_reconnect" : runtimeStarting ? AC.state.hostUi?.runtime_phase || "checking_runtime"
        : runningTool ? `tool:${runningTool.name || "unknown"}:${runningTool.state || "active"}` : turnState || state;
      const label = failedTransport ? "正在重新连接 Agent" : runtimeStarting ? AC.runtimePhaseText(AC.state.hostUi?.runtime_phase || "checking_runtime")
        : runningTool ? `正在运行 ${runningTool.name || "工具"}` : status;
      AC.activityFeedback.observeActivity({session_id:sessionId, state, phase, label,
        cursor:active ? AC.ackCursor(active) : AC.state.runtimeStatusCursor, active:!!active || runtimeStarting});
      for (const value of AC.state.sessions.values()) observeSessionFeedback(value);
    };

    const renderTimelineNode = row => {
      const labels = {message:"消息", tool:"工具调用", activity:"处理过程", research:"联网资料", candidate:"候选方案", app_action:"App操作", question:"等待用户", checkpoint:"文档恢复点", notice:"状态"};
      const categories = {message:"provider", tool:"app_tool", activity:"runtime", research:"web", candidate:"candidate", app_action:"app_action", question:"user_wait", checkpoint:"checkpoint", notice:"runtime"};
      const reasoning = row.kind === "message" && String(row.operation_id || row.stable_node_id || "").startsWith("reasoning:");
      const wireReasoning = node => {
        if (!reasoning || row.text || !node?.querySelector) return node;
        const disclosure = node.querySelector("details");
        disclosure?.addEventListener?.("toggle", () => {
          if (disclosure.open) void AC.loadReasoning?.(row);
        }, {once:true});
        return node;
      };
      if (global.AgentTimelineNodeRenderer) {
        try {
          return wireReasoning(global.AgentTimelineNodeRenderer.renderNode(documentObject,
            {...row, label:row.label || labels[row.kind] || "过程", category:row.category || categories[row.kind] || "runtime"}, {
              projection:"chat",
              copyText:value => windowObject.navigator?.clipboard?.writeText?.(value),
              confirmExternal:request => windowObject.confirm(`使用系统浏览器打开以下链接？\n${request?.url || ""}`),
              openExternal,
            }));
        } catch {}
      }
      if (row.kind === "message") return wireReasoning(global.AgentMessageRenderer.createMessage(documentObject, {role:row.role, text:row.text || "", reasoning:row.reasoning === true || reasoning}).article);
      const card = ui.el("article", null, `ac-timeline-node ac-timeline-${row.kind}`), head = ui.el("header", null, "ac-timeline-node-head");
      head.append(ui.el("strong", row.label || ({tool:"工具", research:"搜索与资料", candidate:"候选", app_action:"App操作", question:"问题", checkpoint:"恢复点", notice:"状态", activity:"过程"}[row.kind] || "过程")),
        ui.el("small", `${row.state ? `${AC.labels[row.state] || row.state} · ` : ""}#${row.source_seq}`));
      card.append(head); if (row.text) card.append(ui.el("p", row.text, "ac-timeline-node-text")); return card;
    };

    const renderMessages = session => {
      const model = AC.readModel(session), box = AC.nodes.messages, legacyBox = AC.conversationView?.list || box;
      const nearBottom = box.scrollHeight - box.scrollTop - box.clientHeight < 70; let changed = false;
      if (session && AC.state.workbenchCapabilities.history_window && AC.conversationView?.hasTimeline?.(session.session_id)) {
        AC.nodes.olderMessages.hidden = true; AC.nodes.newMessages.hidden = true; AC.nodes.tools.hidden = true;
        renderAppActions(session); return;
      }
      const filteredMessages = (model?.messages || []).filter(message => global.AgentWorkbenchSelectors.visible(message, AC.workbenchState(session)));
      const messageWindow = AC.conversationWindow.select(session?.session_id || "", filteredMessages), visibleMessages = messageWindow.rows;
      const filteredTools = (model?.tools || []).filter(tool => global.AgentWorkbenchSelectors.visible(tool, AC.workbenchState(session)));
      const toolWindow = AC.toolWindow.select(session?.session_id || "", filteredTools), visibleTools = toolWindow.rows;
      const keptMessages = new Set(visibleMessages.map(message => message.id)), keptTools = new Set(visibleTools.map(tool => tool.id));
      for (const [id, node] of AC.messageNodes) if (!keptMessages.has(id)) { node.remove(); AC.messageNodes.delete(id); }
      for (const [id, node] of AC.toolNodes) if (!keptTools.has(id)) { node.remove(); AC.toolNodes.delete(id); }
      for (const message of visibleMessages) {
        let node = AC.messageNodes.get(message.id);
        if (!node) {
          const renderer = global.AgentMessageRenderer.createMessage(documentObject, {role:message.role, text:message.text, streaming:message.streaming === true, reasoning:message.reasoning === true});
          node = renderer.article; node.acMessage = renderer; node.acText = message.text; node.acStreaming = message.streaming === true;
          node.dataset.stableNodeId = message.id; AC.messageNodes.set(message.id, node); legacyBox.append(node); changed = true;
        }
        node.hidden = false;
        if (node.acText !== message.text || node.acStreaming !== (message.streaming === true)) {
          node.acText = message.text; node.acStreaming = message.streaming === true;
          node.acMessage.setText(message.text, {streaming:node.acStreaming}); changed = true;
        }
      }
      for (const tool of visibleTools) {
        let node = AC.toolNodes.get(tool.id);
        if (!node) { node = ui.el("p"); AC.toolNodes.set(tool.id, node); AC.nodes.toolsBody.append(node); }
        node.hidden = false;
        const text = `${tool.name}${tool.state ? ` · ${AC.labels[tool.state] || tool.state}` : ""}${tool.message ? `\n${tool.message}` : ""}`;
        if (node.textContent !== text) node.textContent = text;
      }
      if (AC.nodes.olderMessages) {
        AC.nodes.olderMessages.hidden = !messageWindow.has_older;
        AC.nodes.olderMessages.textContent = messageWindow.has_older ? `显示更早消息（当前 ${visibleMessages.length}/${messageWindow.total}）` : "";
      }
      AC.nodes.toolsSummary.textContent = `使用的资料与计算步骤（${visibleTools.length}/${filteredTools.length}）`;
      AC.nodes.tools.hidden = !filteredTools.length; renderAppActions(session);
      if (changed && nearBottom) { box.scrollTop = box.scrollHeight; AC.nodes.newMessages.hidden = true; }
      else if (changed) AC.nodes.newMessages.hidden = false;
    };

    const refresh = () => {
      if (!AC.ready) return;
      const n = AC.nodes, session = AC.selected(), active = AC.active(), busy = !!AC.state.busy;
      const unsettledAppAction = AC.hasUnsettledAppAction(session), pendingApplication = AC.hasPendingApplication(session);
      const checkpointFrozen = AC.checkpointFrozen?.() === true, blockingCommit = unsettledAppAction || pendingApplication || checkpointFrozen;
      const model = AC.readModel(session), activeModel = AC.readModel(active), projection = workbenchProjection(session);
      const runtimeStarting = AC.state.busy === "runtime-start";
      const status = active ? AC.label(activeModel?.state) : AC.state.busy === "detect" ? "检测中"
        : runtimeStarting ? AC.runtimePhaseText(AC.state.hostUi?.runtime_phase || "checking_runtime") : projection.summary;
      const unreadCount = (AC.state.history || []).filter(row => row?.unread === true).length;
      AC.activityFeedback?.setUnreadCount?.(unreadCount);
      refreshActivityFeedback(active, activeModel, active ? workbenchProjection(active) : projection, runtimeStarting, status);
      const badgeLabel = active ? `助手 · ${status}` : session?.candidate?.ready && !session.applied ? "助手 · 有方案待确认" : "打开助手";
      n.badge.textContent = unreadCount ? `${badgeLabel} · ${unreadCount} 未读` : badgeLabel;
      n.badge.setAttribute("aria-expanded", String(AC.state.open)); n.status.textContent = status;
      n.connect.disabled = busy || !!active || AC.state.connection === "ready" && AC.state.connectedCliId === n.cli.value
        || !AC.state.candidates.some(candidate => candidate.id === n.cli.value && candidate.supported === true);
      n.detect.disabled = busy || !!active;
      if (n.resume) n.resume.disabled = busy || !!active || !session || session.live || model?.canResume !== true
        || AC.state.connection !== "ready" || !AC.cliConnected() || !AC.sessionProviderMatches(session);
      if (n.deleteCredential) n.deleteCredential.disabled = busy || !!active;
      n.model.disabled = busy || !!session || !!active || !AC.cliConnected();
      const selectedModel = AC.visibleModels().find(row => row.model === (model?.model || AC.state.model));
      n.effort.disabled = n.model.disabled || AC.noReasoningModel(selectedModel); n.cli.disabled = busy || !!active; n.kind.disabled = !!session || busy;
      if (!session && !n.workflowExplicit) n.workflow.value = AC.defaultWorkflow(n.kind.value);
      if (n.kind.value !== "whiteboard" && !session) n.workflow.value = "calculate";
      n.workflow.disabled = !!session || busy || n.kind.value !== "whiteboard"; if (session) n.workflow.value = model?.workflow || "calculate";
      const runtimeState = projection.axes.runtime_state;
      const runtimeBlocked = runtimeState !== null && runtimeState !== "ready" && !AC.canPrepareColdRuntime(runtimeState);
      const sendDisabled = busy || !!active || blockingCommit || runtimeBlocked || AC.state.connection !== "ready"
        || !AC.cliConnected() || !AC.sessionProviderMatches(session) || !!session && !session.live;
      const sendLabel = session ? (AC.isAuthoring(session) ? "继续分阶段完善" : "继续排轴与计算") : "发送任务";
      const activeViewed = !!active && active === session, foundation = AC.workbenchFoundation(session);
      const openQuestion = foundation?.questions?.find(row => row.state === "open") || null;
      if (openQuestion && n.inboxMode) n.inboxMode.value = "queue";
      if (n.inboxMode) n.inboxMode.disabled = !!openQuestion || !activeViewed || AC.state.questionTakeover;
      if (n.inboxControls) n.inboxControls.hidden = !activeViewed || AC.state.questionTakeover;
      if (n.input) n.input.disabled = AC.state.questionTakeover || !!active && !activeViewed;
      const activeMode = openQuestion ? "queue" : n.inboxMode?.value || "queue";
      const activeSendLabel = activeMode === "queue" ? "加入队列" : activeMode === "steer" ? "引导当前任务" : "停止后发送";
      if (n.inboxControlsSummary) n.inboxControlsSummary.textContent = activeMode === "queue" ? "排队" : activeMode === "steer" ? "引导" : "停止后发送";
      const activeSendDisabled = busy || !activeViewed || AC.state.questionTakeover
        || !AC.state.workbenchCapabilities.durable_inbox || !foundation?.document_binding;
      AC.composer?.setPrimary?.({active:!!active || runtimeStarting, sendDisabled, activeSendDisabled,
        stopDisabled:AC.state.busy === "stop" || AC.state.runtimePrepare?.status === "cancelling", sendLabel, activeSendLabel,
        stopLabel:AC.state.runtimePrepare?.status === "cancelling" ? "取消中…" : AC.state.busy === "stop" ? "停止中…" : "停止"});
      n.forget.disabled = !session || AC.isActive(session) || busy;
      n.newTask.disabled = busy || !!AC.pendingApplicationSession(); n.activeLink.hidden = !active || active === session;
      n.activeLink.textContent = active ? `返回运行中任务 · ${activeModel?.title || "计算补正"}` : "返回运行中任务";
      const kind = model?.kind || n.kind.value;
      const title = model?.title || (kind === "whiteboard" ? windowObject.WB?.state.board?.title : windowObject.PL?.state.plan?.manual_template?.meta?.name) || "尚未打开输入";
      const building = AC.isAuthoring(model) || !session && ["build", "staged"].includes(AC.workflow());
      const stageState = AC.workflowState(model, session?.candidate, session?.base?.document
        || (kind === "whiteboard" ? windowObject.WB?.state.board : windowObject.PL?.state.plan), kind === "whiteboard" ? windowObject.WB?.state.result : null);
      n.headerTitle.textContent = model?.title || title || "AI 排轴助手";
      n.headerStage.textContent = AC.workflowStages[stageState.index].label;
      n.headerModel.textContent = selectedModel?.displayName || model?.model || AC.state.model || "未选择模型";
      n.workflowNote.textContent = building
        ? "允许 Agent 从空白开始，按明确要求修改角色、装备、面板和动作，再依次完善动作检查点、事件拓扑和数值范式。每轮审核后应用到白板；同一聊天保留上下文，能量在严格计算前显示为未检查。"
        : "执行严格计算并验证通用元素逻辑、数值完整性、绑定、动作、冷却和能量；失败只返回不可应用的诊断。";
      n.context.textContent = `任务：${building ? "分阶段完善白板" : "严格计算与补正"}\n当前阶段：${AC.workflowStages[stageState.index].label}\n处理对象：${kind === "whiteboard" ? "创作白板" : "原生方案"} · ${title}${session ? "（冻结快照）" : "（发送时冻结）"}${model?.model ? `\n模型：${model.model}${model.effort ? ` · ${model.effort}` : ""}` : ""}`;
      const contextDocument = session?.base?.document || (kind === "whiteboard" ? windowObject.WB?.state.board : windowObject.PL?.state.plan);
      const template = contextDocument?.manual_template || contextDocument;
      const teamCount = Array.isArray(template?.team) ? template.team.length : Array.isArray(template?.characters) ? template.characters.length : null;
      const actionCount = Array.isArray(template?.actions) ? template.actions.length : null;
      const revision = Number.isSafeInteger(contextDocument?.revision) ? contextDocument.revision : null;
      let dirty = false; try { dirty = kind === "whiteboard" ? !!windowObject.WB?.dirty?.() : !!windowObject.PL?.dirty?.(); } catch {}
      AC.composer?.setContextChips?.([{kind:"document", label:kind === "whiteboard" ? "白板" : "原生方案", value:title},
        {kind:"stage", label:"阶段", value:AC.workflowStages[stageState.index].label},
        ...(revision !== null && revision !== 0 ? [{kind:"revision", label:"文档版本", value:String(revision)}] : []),
        ...((teamCount !== null || actionCount !== null) && (teamCount !== 0 || actionCount !== 0) ? [{kind:"scope", label:"范围", value:`${teamCount ?? "?"} 名角色 · ${actionCount ?? "?"} 个动作`}] : []),
        ...(session?.candidate ? [{kind:"candidate", label:"候选", value:`方案版本 ${session.candidate.revision ?? "未知"}`}] : []),
        {kind:dirty ? "dirty" : "clean", label:"文档", value:dirty ? "有未保存修改" : "已保存"}]);
      const usage = model?.usage, usageDetails = usage ? [
        `输入 ${usage.input_tokens ?? "—"}`,
        `输出 ${usage.output_tokens ?? "—"}`,
        `合计 ${usage.total_tokens ?? "—"}`,
        ...(Number.isSafeInteger(usage.reasoning_tokens) ? [`推理 ${usage.reasoning_tokens}`] : []),
        ...(Number.isSafeInteger(usage.cached_input_tokens) ? [`缓存输入 ${usage.cached_input_tokens}`] : []),
      ] : [];
      n.usage.textContent = usage ? `本会话API用量：${usageDetails.join(" · ")} token` : ""; n.usage.hidden = !usage;
      const budgetView = AC.runtimeBudgetView(session); n.budgetProgress.textContent = budgetView?.text || ""; n.budgetProgress.hidden = !budgetView; n.budgetProgress.setAttribute("data-level", budgetView?.level || "none"); n.budgetProgress.setAttribute("data-compaction", String(budgetView?.compaction === true));
      n.stageFlow.replaceChildren(AC.renderStageTracker(stageState, true));
      if (n.taskSummary) n.taskSummary.textContent = `任务与历史 · ${kind === "whiteboard" ? "白板" : "原生方案"} · ${title}`;
      const contextBlocker = checkpointFrozen ? "文档恢复的最终状态尚不能安全判定。所有 Agent 与 App 写入已冻结；请保留当前文档并完成恢复核对。"
        : pendingApplication ? "候选应用事务尚未完成。已写入文档的候选需补记回执；尚未写入的事务可在确认当前仍是原文档后取消。"
        : unsettledAppAction ? "有App操作仍在等待结果。请先完成并写回，或对结果未知的操作执行“解除未知操作”，再继续发送消息。"
        : !AC.sessionProviderMatches(session) ? "当前连接不是创建该会话的Agent提供方；旧会话保持只读。请切回原提供方后继续或恢复。"
        : AC.hasDraft(kind) ? "有未应用的装备草稿，请先处理后再发送或应用候选。"
        : session?.base && !AC.currentMatches(session.base) ? "当前输入已变化；此任务仍处理原快照，候选不能覆盖当前修改。"
        : session && !session.live ? "这是冷会话记录；可先连接原提供方，再点击恢复会话。恢复本身不会发起模型请求。"
        : session?.recovery?.message ? session.recovery.message : "";
      const contextInformation = model?.historyTruncated ? "较早的聊天内容已按容量限制清理；当前候选和冻结输入仍保留。" : "";
      n.contextWarning.textContent = contextBlocker || contextInformation;
      n.contextWarning.dataset.attentionEligible = String(Boolean(contextBlocker));
      if (n.statusContextDetail) { n.statusContextDetail.textContent = contextInformation; n.statusContextDetail.hidden = !contextInformation; }
      n.error.textContent = session?.error || ""; AC.renderErrorDiagnostic?.(session?.error || n.notice?.textContent || ""); AC.renderSessionNavigation(); renderWorkbench(session); renderMessages(session);
      const candidate = session?.candidate, candidateState = AC.workflowState(model, candidate);
      const signature = JSON.stringify([session?.session_id, candidate?.revision, candidate?.ready, candidate?.stale,
        candidate?.receipt_fingerprint, candidateState.stage, candidateState.stageStatus, candidateState.calculationAcceptance,
        session?.applied, model?.workflow, !!active, session?.applicationRecoveryPending === true, session?.lastAppliedRestorePending === true]);
      if (n.reviewSignature !== signature) {
        n.reviewSignature = signature; n.candidate.replaceChildren();
        if (candidate) {
          n.candidate.append(ui.el("strong", session.applied ? "阶段候选已应用" : candidate.stale ? "候选已过期"
            : candidateState.diagnostic ? "严格计算未通过 · 仅诊断" : candidate.ready ? `${AC.workflowStages[candidateState.index].label}候选可审核` : "候选仍有待完善项"),
          ui.el("p", `方案版本 ${candidate.revision ?? "未知"} · ${candidate.issues?.length || 0} 项提示。${candidateState.diagnostic ? "不能应用为正式结果，请让 Agent 修复阻断项后重算。" : building ? "审核后可应用草稿并在同一聊天进入下一阶段。" : "只有通过计算检查的结果才能应用。"}`),
          ui.button(candidateState.diagnostic ? "查看拒绝原因" : "查看阶段差异与检查", AC.review));
          if (AC.isBuild(session) && session.applied) n.candidate.append(ui.button("显式进入严格计算新任务", AC.startCalculation));
        }
        if (session?.applicationRecoveryPending === true) {
          const controls = ui.el("div", null, "ac-actions"); controls.append(ui.button("恢复并重新应用候选", () => AC.recoverPendingCandidateApplication(false)));
          if (session.live && (session.pendingApplicationAck || session.applicationIntent?.intent)) controls.append(ui.button("补记当前文档回执", AC.retryPendingApplication));
          controls.append(ui.button("放弃当前编辑并恢复候选", () => AC.recoverPendingCandidateApplication(true)), ui.button("取消未应用事务", AC.cancelPendingApplication));
          n.candidate.append(ui.el("p", session.live ? "检测到尚未完成的方案应用。当前仍是原文档时可从 App 恢复并重新应用；已经显示候选时可补记确认结果。若确认失败后又编辑了文档，可显式放弃这些编辑并恢复 App 保存的候选。只有 App 确认原文档未变化时才允许取消。" : "这份已停止的会话仍有尚未完成的方案应用。App 完全重启后，可恢复并重新应用审核过的候选；若同一文档已继续编辑，可显式放弃这些编辑后恢复。当前仍是原文档时也可请求安全取消。完成前不能新建或切换其他会话。", "ac-warning"), controls);
        }
        if (session?.lastAppliedRestorePending === true) n.candidate.append(ui.el("p", "App 保存着上次已应用但尚未写入白板文件的版本。系统不会自动覆盖当前白板；确认恢复后，当前文件路径和原保存状态仍会保留，恢复内容显示为未保存。", "ac-warning"), ui.button("确认恢复上次未保存白板", () => AC.rehydrateLastAppliedProjection(session, null, true)));
      }
      const candidateActionable = Boolean(candidate && !session?.applied && (candidate.ready || candidate.stale || candidateState.diagnostic))
        || session?.applicationRecoveryPending === true || session?.lastAppliedRestorePending === true;
      n.candidate.dataset.attentionEligible = String(candidateActionable);
      n.candidate.hidden = !candidateActionable;
      global.AgentWorkbenchView.renderAttentionShelf({nodes:n});
    };

    const beforeDocumentSave = (kind = "whiteboard") => {
      let base; try { base = AC.capture(kind); } catch { return null; }
      return {base, sessions:[...AC.state.sessions.values()].filter(session => session.kind === kind && session.live
        && session.base && AC.same(session.base, base)).map(session => ({session, base:session.base}))};
    };
    const documentSaved = snapshot => {
      if (!snapshot) return; let current; try { current = AC.capture(snapshot.base.kind); } catch { return; }
      if (!AC.sameContent(snapshot.base, current)) return;
      for (const entry of snapshot.sessions) if (entry.session.base === entry.base) {
        const pending = entry.session.pendingApplicationAck; if (pending?.base === entry.base) pending.base = current; entry.session.base = current;
      }
      refresh();
    };
    const documentChanged = () => refresh();

    return Object.freeze({renderAppActions, workbenchProjection, setWorkbenchView, setWorkbenchTurn,
      renderStatusAxes, feedbackQuestionOpen, observeSessionFeedback, refreshActivityFeedback,
      renderWorkbench, renderTimelineNode, renderMessages, refresh,
      beforeDocumentSave, documentSaved, documentChanged});
  }

  global.AgentWorkbenchPresentationController = Object.freeze({create});
})(window);
