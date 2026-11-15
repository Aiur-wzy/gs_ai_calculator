(function (global) {
  "use strict";

  // Pure DOM factory for the Agent workbench.  It owns no Host state, starts
  // no request and creates no domain controller.  Every behaviour crosses an
  // injected callback so the composition root remains the only command owner.
  function create(options = {}) {
    const agent = options.agent, windowObject = options.window, documentObject = options.document, ui = options.ui;
    if (!agent || !windowObject || !documentObject || !ui) throw new TypeError("workbench_layout_dependencies_missing");
    const root = options.root || documentObject.getElementById("agent-chat");
    const badge = options.badge || documentObject.getElementById("agent-chat-toggle");
    if (!root || !badge) throw new TypeError("workbench_layout_mount_missing");
    // Build into a private node table. Publishing it on the compatibility
    // facade is a composition concern and therefore belongs to Bootstrap.
    const n = options.nodes || {};
    n.root = root; n.badge = badge;
    n.pageHost = documentObject.getElementById("agent-page-host");
    n.pageTab = documentObject.getElementById("agent-page-tab");
    n.badge.addEventListener("click", () => agent.openDock?.());
    n.pageTab?.addEventListener("click", () => agent.openPage?.());

    n.resizer = ui.el("div", null, "ac-panel-resizer"); n.resizer.tabIndex = 0;
    n.resizer.setAttribute("role", "separator"); n.resizer.setAttribute("aria-orientation", "vertical");
    n.resizer.setAttribute("aria-label", "调整排轴区与 Agent 工作台宽度"); root.append(n.resizer);

    const heading = ui.el("header", null, "ac-heading");
    n.headerTitle = ui.el("h3", "AI 排轴助手"); n.headerMeta = ui.el("div", null, "ac-heading-meta");
    n.headerModel = ui.el("span", "未选择模型", "ac-heading-chip"); n.headerStage = ui.el("span", "待开始", "ac-heading-chip");
    n.headerMeta.append(n.headerModel, n.headerStage); n.status = ui.el("span", null, "ac-status");
    n.settingsToggle = ui.button("设置", () => agent.togglePanelSection("settings"), "ac-pane-toggle"); n.settingsToggle.setAttribute("aria-controls", "agent-settings-pane");
    n.detailsToggle = ui.button("任务", () => agent.openInspector?.("details"), "ac-pane-toggle"); n.detailsToggle.setAttribute("aria-controls", "agent-inspector-details");
    n.chatToggle = ui.button("对话", () => agent.togglePanelSection("chat"), "ac-pane-toggle"); n.chatToggle.setAttribute("aria-controls", "agent-conversation-pane");
    n.inspectorToggle = ui.button("检查器", () => agent.togglePanelSection("inspector"), "ac-pane-toggle"); n.inspectorToggle.setAttribute("aria-controls", "agent-inspector-pane"); n.inspectorToggle.setAttribute("aria-pressed", "true");
    n.modeToggle = ui.button("完整页面", () => agent.toggleWorkbenchMode?.(), "ac-pane-toggle");
    n.moreMenu = ui.el("details", null, "ac-heading-more"); n.moreSummary = ui.el("summary", "更多");
    const moreBody = ui.el("div"); moreBody.append(n.settingsToggle, n.chatToggle); n.moreMenu.append(n.moreSummary, moreBody);
    const paneToggles = ui.el("nav", null, "ac-pane-toggles"); paneToggles.setAttribute("aria-label", "AI 助手显示区域");
    paneToggles.append(n.detailsToggle, n.inspectorToggle, n.modeToggle, n.moreMenu);
    n.close = ui.button("×", () => agent.close(), "ac-hide"); n.close.setAttribute("aria-label", "关闭 AI 助手");
    heading.append(n.headerTitle, n.headerMeta, paneToggles, n.close); root.append(heading);

    // A single compact read model over the existing Host/store facts.  The
    // children below remain presentation targets only; no second workflow or
    // transaction state is created here.
    n.statusDock = ui.el("section", null, "ac-status-dock"); n.statusDock.setAttribute("aria-label", "AI 助手当前状态");
    n.statusDot = ui.el("span", null, "ac-status-dot"); n.statusDot.setAttribute("aria-hidden", "true");
    n.statusDock.append(n.statusDot, n.status);
    n.attentionCount = ui.el("span", null, "ac-attention-count"); n.attentionCount.hidden = true; n.statusDock.append(n.attentionCount);
    n.statusHub = ui.el("details", null, "ac-status-hub");
    n.statusHubSummary = ui.el("summary", "状态详情"); n.statusHubSummary.setAttribute("aria-haspopup", "dialog"); n.statusHubSummary.setAttribute("aria-expanded", "false");
    n.statusHubPanel = ui.el("section", null, "ac-status-hub-panel"); n.statusHubPanel.setAttribute("aria-label", "状态详情");
    n.statusAttentionGroup = ui.el("section", null, "ac-status-hub-group ac-status-attention-group"); n.statusAttentionGroup.hidden = true;
    n.statusAttentionList = ui.el("ul", null, "ac-status-attention-list"); n.statusAttentionGroup.append(ui.el("h4", "需要处理"), n.statusAttentionList);
    n.statusServiceGroup = ui.el("section", null, "ac-status-hub-group"); n.statusServiceGroup.append(ui.el("h4", "服务与任务"));
    n.statusAxes = ui.el("div", null, "ac-state-axes"); n.statusAxes.setAttribute("aria-label", "账号、服务、任务与文档状态");
    n.statusContextDetail = ui.el("p", null, "ac-note ac-status-context-detail");
    n.statusServiceGroup.append(n.statusAxes, n.statusContextDetail); n.statusHubPanel.append(n.statusServiceGroup);
    n.statusHub.append(n.statusHubSummary, n.statusHubPanel); n.statusDock.append(n.statusHub);

    n.activityFeedbackHost = ui.el("section", null, "ac-activity-feedback"); n.activityFeedbackHost.setAttribute("aria-label", "Agent 实时工作状态");
    n.statusRunningGroup = ui.el("section", null, "ac-status-hub-group ac-status-running-group"); n.statusRunningGroup.hidden = true; n.statusRunningGroup.append(ui.el("h4", "当前运行"), n.activityFeedbackHost);
    if (typeof n.statusHubPanel.replaceChildren === "function") n.statusHubPanel.replaceChildren(n.statusAttentionGroup, n.statusRunningGroup, n.statusServiceGroup);
    else n.statusHubPanel.append(n.statusAttentionGroup, n.statusRunningGroup);
    n.activityLive = ui.el("span", null, "ac-live-region"); n.activityLive.setAttribute("role", "status");
    n.activityLive.setAttribute("aria-live", "polite"); n.activityLive.setAttribute("aria-atomic", "true");
    n.toastHost = ui.el("aside", null, "ac-agent-toasts"); n.toastHost.setAttribute("aria-label", "Agent 后台通知");
    root.append(n.activityLive); documentObject.body.append(n.toastHost);
    const surfaceKernel = windowObject.AppSurfaceKernel;
    n.moreSurfaceBinding = surfaceKernel?.bindDisclosure?.(n.moreMenu, {panel:moreBody, kind:"popover", ariaLabel:"AI 助手显示选项"}) || null;
    n.statusSurfaceBinding = surfaceKernel?.bindDisclosure?.(n.statusHub, {panel:n.statusHubPanel, kind:"popover", ariaLabel:"AI 助手状态详情"}) || null;
    const closeStatusHub = restoreFocus => {
      if (!n.statusHub.open) return;
      n.statusHub.open = false;
      n.statusHubSummary.setAttribute("aria-expanded", "false");
      if (restoreFocus) n.statusHubSummary.focus?.();
    };
    if (!n.statusSurfaceBinding) n.statusHub.addEventListener("toggle", () => n.statusHubSummary.setAttribute("aria-expanded", String(Boolean(n.statusHub.open))));
    documentObject.addEventListener?.("pointerdown", event => {
      if (!n.statusSurfaceBinding && n.statusHub.open && !n.statusHub.contains?.(event.target)) closeStatusHub(false);
      for (const [details,binding] of [[n.searchTools,n.searchSurfaceBinding],[n.turnTools,n.turnSurfaceBinding]])
        if (!binding && details?.open && !details.contains?.(event.target)) details.open = false;
    });
    documentObject.addEventListener?.("keydown", event => {
      if (event.key !== "Escape") return;
      if (!n.statusSurfaceBinding && n.statusHub.open) { event.preventDefault?.(); closeStatusHub(true); return; }
      for (const [details,binding] of [[n.searchTools,n.searchSurfaceBinding],[n.turnTools,n.turnSurfaceBinding]])
        if (!binding && details?.open) { event.preventDefault?.(); details.open = false; details.children?.[0]?.focus?.(); break; }
    });

    n.settings = ui.el("details", null, "ac-settings"); n.settings.open = true;
    n.settingsSummary = ui.el("summary", "连接与偏好");
    n.settings.append(n.settingsSummary, ui.el("p", "在这里选择模型服务、调整工作预算和显示偏好。API Key 只会按你的选择交给 Windows 安全保存。", "ac-note"));
    n.feedbackDetails = ui.select([{value:"compact",label:"紧凑"},{value:"standard",label:"标准"},{value:"full",label:"完整"}], "standard", value => agent.activityFeedback?.setPreference?.("details", value));
    n.feedbackMotion = ui.select([{value:"system",label:"跟随系统"},{value:"reduce",label:"减少动态"},{value:"full",label:"完整动态"}], "system", value => agent.activityFeedback?.setPreference?.("motion", value));
    n.feedbackNotifications = ui.select([{value:"all",label:"全部后台状态"},{value:"important",label:"仅失败与待回答"},{value:"off",label:"关闭"}], "all", value => agent.activityFeedback?.setPreference?.("notifications", value));
    n.feedbackSettings = ui.el("section", null, "ac-feedback-settings");
    n.feedbackSettings.append(ui.el("h4", "工作台显示与通知"), ui.field("工作详情", n.feedbackDetails), ui.field("动效偏好", n.feedbackMotion), ui.field("App 内通知", n.feedbackNotifications), ui.el("p", "这些偏好只保存在本机；通知仅显示在 App 内，不调用 Windows 系统通知。", "ac-note"));
    n.settings.append(n.feedbackSettings);
    n.cli = ui.select([], "", value => options.onCliSelected?.(value));
    n.cliPath = ui.el("p", null, "ac-path"); n.cliInfo = ui.el("div", null, "ac-cli-info");
    n.apiKey = ui.input("", () => {}); n.apiKey.type = "password"; n.apiKey.autocomplete = "off";
    n.apiKey.placeholder = "首次输入；以后可从Windows安全读取"; n.apiKey.setAttribute("aria-label", "DeepSeek API Key");
    n.apiKeyField = ui.field("DeepSeek API Key", n.apiKey); n.apiKeyField.hidden = true;
    n.rememberKey = ui.el("input"); n.rememberKey.type = "checkbox"; n.rememberKey.checked = true;
    n.rememberKey.setAttribute("aria-label", "安全保存到Windows凭据库");
    const rememberLabel = ui.el("label", null, "ac-check"); rememberLabel.append(n.rememberKey, ui.el("span", "账号校验通过后安全保存到 Windows 凭据库"));
    n.deleteCredential = ui.button("删除已保存Key", () => agent.forgetCredential?.()); n.credentialStatus = ui.el("p", null, "ac-note");
    n.credentialControls = ui.el("div", null, "ac-credential-controls"); n.credentialControls.hidden = true;
    n.credentialControls.append(rememberLabel, n.deleteCredential, n.credentialStatus);
    n.discovery = ui.el("details", null, "ac-discovery"); n.discoverySummary = ui.el("summary", "本机可用的助手"); n.discoveryBody = ui.el("div");
    n.discovery.append(n.discoverySummary, n.discoveryBody);
    n.detect = ui.button("重新检测", () => agent.detect?.()); n.connect = ui.button("验证 API 设置", () => agent.connect?.());
    const tools = ui.el("div", null, "ac-actions"); tools.append(n.detect, n.connect);
    n.model = ui.select([], "", value => options.onModelSelected?.(value));
    n.effort = ui.select([], "", value => options.onEffortSelected?.(value));
    n.modelCapacity = ui.el("p", null, "ac-note"); n.modelCapacity.hidden = true;
    n.controls = ui.el("aside", null, "ac-controls ac-drawer"); n.controls.id = "agent-settings-pane"; n.controls.setAttribute("aria-label", "Agent 设置"); root.append(n.controls);
    n.connectionInfo = ui.el("p", null, "ac-note");
    n.settings.append(ui.field("连接方式", n.cli), n.cliPath, n.apiKeyField, n.credentialControls, tools, n.discovery, n.cliInfo, n.connectionInfo);
    n.budgetDetails = ui.el("details", null, "ac-budget"); n.budgetSummary = ui.el("summary", "单轮预算"); n.budgetBody = ui.el("div"); n.budgetInputs = {};
    n.budgetDetails.append(n.budgetSummary, n.budgetBody); n.settings.append(n.modelCapacity, n.budgetDetails);
    n.dshSettingsDetails = ui.el("details", null, "ac-budget"); n.dshSettingsSummary = ui.el("summary", "Agent运行参数"); n.dshSettingsBody = ui.el("div");
    n.dshSettingsDetails.append(n.dshSettingsSummary, n.dshSettingsBody); n.settings.append(n.dshSettingsDetails); n.controls.append(n.settings);

    n.detailsPane = ui.el("aside", null, "ac-details-pane ac-drawer"); n.detailsPane.id = "agent-task-details-pane"; n.detailsPane.setAttribute("aria-label", "会话历史与任务"); root.append(n.detailsPane);
    n.taskSettings = ui.el("details", null, "ac-task-settings"); n.taskSummary = ui.el("summary", "会话与任务"); n.taskSettings.open = true;
    n.taskSettings.append(n.taskSummary); n.detailsPane.append(n.taskSettings);
    const history = ui.el("div", null, "ac-history");
    n.history = ui.select([], "", value => agent.selectSession?.(value)); n.historyLegacyField = ui.field("兼容会话选择", n.history); n.historyLegacyField.hidden = true;
    // Kept as detached compatibility nodes until the presentation controller
    // stops projecting their disabled state. Session commands now belong to
    // each row's context menu; document creation remains in the File menu.
    n.resume = ui.button("恢复会话", () => agent.resumeSession?.()); n.resume.hidden = true;
    n.newTask = ui.button("新任务", () => agent.newTask?.()); n.newTask.hidden = true;
    n.forget = ui.button("删除记录", () => agent.forget?.()); n.forget.hidden = true;
    n.sessionNavigationHost = ui.el("div", null, "ac-session-navigation");
    history.append(n.sessionNavigationHost, n.historyLegacyField); n.taskSettings.append(history);
    n.hostOutlineSection = ui.el("section", null, "ac-host-outline-section"); n.hostOutlineSection.append(ui.el("h4", "本次任务步骤"));
    n.hostOutlineHost = ui.el("div"); n.hostOutlineSection.append(n.hostOutlineHost); n.taskSettings.append(n.hostOutlineSection);
    n.inboxSection = ui.el("section", null, "ac-inbox-section"); n.inboxSection.hidden = true; n.inboxHost = ui.el("div", null, "ac-inbox-dock");
    n.inboxSection.append(n.inboxHost); n.taskSettings.append(n.inboxSection);
    n.checkpointSection = ui.el("section", null, "ac-checkpoint-section"); n.checkpointSection.hidden = true; n.checkpointHost = ui.el("div", null, "ac-checkpoint-dock");
    n.checkpointSection.append(n.checkpointHost); n.taskSettings.append(n.checkpointSection);

    n.chatPane = ui.el("div", null, "ac-chat-pane"); n.chatPane.id = "agent-conversation-pane"; root.append(n.chatPane);
    n.activeLink = ui.button("返回运行中任务", () => { const active = agent.active?.(); if (active) agent.selectSession?.(active.session_id); }, "ac-active-link"); n.chatPane.append(n.activeLink);
    // These tabs remain as a compatibility projection for the existing
    // workbench controller.  The visible process UI now lives in the right
    // Inspector, so changing its activity tab never replaces the chat.
    n.workbenchViews = ui.el("div", null, "ac-workbench-views ac-legacy-workbench-views"); n.workbenchViews.setAttribute("role", "tablist"); n.workbenchViews.setAttribute("aria-label", "兼容会话投影"); n.workbenchViews.hidden = true;
    n.chatTab = ui.button("对话", () => agent.setWorkbenchView?.("chat"), "ac-workbench-tab"); n.chatTab.id = "agent-chat-tab"; n.chatTab.setAttribute("role", "tab"); n.chatTab.setAttribute("aria-controls", "agent-chat-view");
    n.trajectoryTab = ui.button("运行详情", () => agent.setWorkbenchView?.("trajectory"), "ac-workbench-tab"); n.trajectoryTab.id = "agent-trajectory-tab"; n.trajectoryTab.setAttribute("role", "tab"); n.trajectoryTab.setAttribute("aria-controls", "agent-trajectory-view");
    n.workbenchViews.append(n.chatTab, n.trajectoryTab);
    n.search = ui.input("", () => {}); n.search.type = "search"; n.search.placeholder = "搜索当前会话和已保存的历史"; n.search.setAttribute("aria-label", "搜索当前 AI 助手会话");
    n.search.addEventListener("input", () => options.onSearchInput?.(n.search.value));
    n.conversationTools = ui.el("nav", null, "ac-conversation-tools"); n.conversationTools.setAttribute("aria-label", "会话视图、搜索和历史状态");
    n.searchTools = ui.el("details", null, "ac-conversation-search"); n.searchToolsSummary = ui.el("summary", "搜索");
    n.hostSearchResults = ui.el("section", null, "ac-host-search-results"); n.hostSearchResults.hidden = true;
    n.turnRail = ui.el("nav", null, "ac-turn-rail"); n.turnRail.setAttribute("aria-label", "会话轮次");
    n.searchToolsPanel = ui.el("div", null, "ac-conversation-search-panel"); n.searchToolsPanel.append(n.search, n.hostSearchResults);
    n.searchTools.append(n.searchToolsSummary, n.searchToolsPanel);
    n.turnTools = ui.el("details", null, "ac-conversation-turns"); n.turnToolsSummary = ui.el("summary", "全部轮次"); n.turnTools.append(n.turnToolsSummary, n.turnRail);
    n.searchSurfaceBinding = surfaceKernel?.bindDisclosure?.(n.searchTools, {panel:n.searchToolsPanel, kind:"popover", ariaLabel:"搜索当前会话与历史", initialFocus:n.search}) || null;
    n.turnSurfaceBinding = surfaceKernel?.bindDisclosure?.(n.turnTools, {panel:n.turnRail, kind:"popover", ariaLabel:"会话轮次"}) || null;
    n.conversationHealth = ui.el("div", null, "ac-conversation-health"); n.conversationHealth.setAttribute("aria-label", "历史同步状态");
    n.conversationTools.append(n.workbenchViews, n.searchTools, n.turnTools, n.conversationHealth); n.chatPane.append(n.conversationTools);
    n.kind = ui.select([{value:"whiteboard",label:"当前创作白板"},{value:"native",label:"当前原生方案"}], agent.currentKind(), () => { n.workflowExplicit = false; options.onKindSelected?.(); });
    n.workflow = ui.select([{value:"staged",label:"分阶段构建与计算"},{value:"build",label:"仅构建白板草稿"},{value:"calculate",label:"直接严格计算与补正"}], agent.defaultWorkflow(n.kind.value), () => { n.workflowExplicit = true; agent.refresh?.(); });
    n.workflowNote = ui.el("p", null, "ac-note"); n.context = ui.el("p", null, "ac-context"); n.usage = ui.el("p", null, "ac-note ac-usage"); n.usage.hidden = true;
    n.budgetProgress = ui.el("p", null, "ac-budget-progress"); n.budgetProgress.hidden = true; n.budgetProgress.setAttribute("role", "status"); n.budgetProgress.setAttribute("aria-live", "polite");
    n.stageFlow = ui.el("div", null, "ac-stage-host");
    n.taskSettings.append(ui.field("任务工作区", n.kind), ui.field("处理方式", n.workflow), n.workflowNote, n.stageFlow, n.context, n.usage, n.budgetProgress);
    n.chatView = ui.el("section", null, "ac-projection-view ac-chat-view"); n.chatView.id = "agent-chat-view"; n.chatView.tabIndex = 0; n.chatView.setAttribute("role", "tabpanel"); n.chatView.setAttribute("aria-labelledby", n.chatTab.id); n.chatPane.append(n.chatView);
    n.olderMessages = ui.button("显示更早消息", () => { const session = agent.selected?.(); if (!session || !agent.conversationWindow?.expand?.(session.session_id)) return; const before = n.messages.scrollHeight; agent.renderMessages?.(session); const restore = () => { n.messages.scrollTop += Math.max(0, n.messages.scrollHeight - before); }; if (typeof windowObject.requestAnimationFrame === "function") windowObject.requestAnimationFrame(restore); else restore(); }, "ac-load-older");
    n.olderMessages.hidden = true; n.chatView.append(n.olderMessages);
    n.messages = ui.el("div", null, "ac-messages"); n.messages.setAttribute("role", "log"); n.messages.setAttribute("aria-live", "polite"); n.messages.setAttribute("aria-relevant", "additions"); n.messages.setAttribute("aria-label", "Agent 对话记录；流式正文完成前不会逐字朗读"); n.chatView.append(n.messages);
    n.newMessages = ui.button("有新消息 · 滚动到底部", () => { n.messages.scrollTop = n.messages.scrollHeight; n.newMessages.hidden = true; }, "ac-new-messages"); n.newMessages.hidden = true; n.chatView.append(n.newMessages);
    n.historyWindowHost = ui.el("section", null, "ac-history-window"); n.historyWindowHost.hidden = true; n.historyWindowHost.setAttribute("aria-label", "App 已确认的历史窗口"); n.chatView.append(n.historyWindowHost);
    n.tools = ui.el("details", null, "ac-tools"); n.toolsSummary = ui.el("summary", "工具与计算过程"); n.toolsBody = ui.el("div"); n.tools.append(n.toolsSummary, n.toolsBody);

    n.trajectoryView = ui.el("section", null, "ac-projection-view ac-trajectory-view ac-legacy-trajectory-view"); n.trajectoryView.id = "agent-trajectory-view"; n.trajectoryView.tabIndex = 0; n.trajectoryView.setAttribute("role", "tabpanel"); n.trajectoryView.setAttribute("aria-labelledby", n.trajectoryTab.id); n.trajectoryView.hidden = true;
    n.trajectoryMeta = ui.el("p", null, "ac-note ac-trajectory-meta"); n.telemetryStatus = ui.el("p", null, "ac-note ac-telemetry-status"); n.telemetryStatus.hidden = true; n.telemetryToolbar = ui.el("div", null, "ac-telemetry-toolbar");
    const telemetryCategories = [{value:"all",label:"全部类别"},{value:"turn",label:"任务轮次"},{value:"segment",label:"处理步骤"},{value:"runtime",label:"本地 AI 服务"},{value:"provider",label:"模型服务"},{value:"skill",label:"拉表说明"},{value:"database",label:"资料库"},{value:"web",label:"联网查询"},{value:"app_tool",label:"App 操作"},{value:"engine",label:"计算引擎"},{value:"candidate",label:"待应用方案"},{value:"app_action",label:"需要用户操作"},{value:"checkpoint",label:"版本记录"},{value:"document",label:"文档"},{value:"user_wait",label:"等待回答"},{value:"other",label:"其他"}];
    n.telemetryFilter = ui.select(telemetryCategories, "all", () => agent.applyTelemetryFilters?.()); n.telemetryFilter.setAttribute("aria-label", "筛选运行轨迹类别");
    n.telemetryTurnFilter = ui.el("input"); n.telemetryTurnFilter.type = "search"; n.telemetryTurnFilter.placeholder = "轮次 ID（高级）"; n.telemetryTurnFilter.setAttribute("aria-label", "按任务轮次筛选"); n.telemetryTurnFilter.addEventListener("input", () => agent.applyTelemetryFilters?.());
    n.telemetryStageFilter = ui.el("input"); n.telemetryStageFilter.type = "search"; n.telemetryStageFilter.placeholder = "处理阶段"; n.telemetryStageFilter.setAttribute("aria-label", "按处理阶段筛选"); n.telemetryStageFilter.addEventListener("input", () => agent.applyTelemetryFilters?.());
    n.telemetrySourceFilter = ui.select([{value:"all",label:"全部来源"},{value:"host",label:"App 已确认"},{value:"provider",label:"模型服务"},{value:"runtime",label:"本地 AI 服务"},{value:"app",label:"App"},{value:"engine",label:"计算引擎"}], "all", () => agent.applyTelemetryFilters?.()); n.telemetrySourceFilter.setAttribute("aria-label", "按事件来源筛选");
    n.telemetryStateFilter = ui.select([{value:"all",label:"全部状态"},{value:"preparing",label:"准备中"},{value:"running",label:"运行中"},{value:"waiting",label:"等待中"},{value:"completed",label:"已完成"},{value:"cancelled",label:"已取消"},{value:"failed",label:"失败"},{value:"interrupted",label:"已中断"},{value:"unknown",label:"未知"}], "all", () => agent.applyTelemetryFilters?.()); n.telemetryStateFilter.setAttribute("aria-label", "按事件状态筛选");
    n.telemetryDurationFilter = ui.select([{value:"all",label:"全部耗时"},{value:"lt1s",label:"少于 1 秒"},{value:"1s-10s",label:"1–10 秒"},{value:"gte10s",label:"至少 10 秒"},{value:"unknown",label:"耗时未知"}], "all", () => agent.applyTelemetryFilters?.()); n.telemetryDurationFilter.setAttribute("aria-label", "按耗时筛选");
    n.telemetryQuery = ui.el("input"); n.telemetryQuery.type = "search"; n.telemetryQuery.placeholder = "搜索步骤、结果码或资源引用"; n.telemetryQuery.setAttribute("aria-label", "搜索运行轨迹"); n.telemetryQuery.addEventListener("input", () => agent.applyTelemetryFilters?.());
    n.telemetryInspector = ui.el("output", null, "ac-telemetry-inspector"); n.telemetryInspector.setAttribute("aria-live", "polite"); n.telemetryInspector.textContent = "选择步骤后查看来源、耗时、结果与摘要。";
    n.telemetryToolbar.append(n.telemetryFilter, n.telemetryTurnFilter, n.telemetryStageFilter, n.telemetrySourceFilter, n.telemetryStateFilter, n.telemetryDurationFilter, n.telemetryQuery, n.telemetryInspector);
    n.telemetryHost = ui.el("div", null, "ac-telemetry-bundle"); n.telemetryHost.hidden = true; n.trajectoryList = ui.el("div", null, "ac-trajectory-list");
    // The old view node intentionally remains empty: controllers may continue
    // to store their legacy selected-view state without hiding the visible
    // Inspector.  All actual trajectory nodes have one canonical DOM owner.
    n.chatPane.append(n.trajectoryView);

    n.inspectorResizer = ui.el("div", null, "ac-inspector-resizer"); n.inspectorResizer.tabIndex = 0; root.append(n.inspectorResizer);
    n.inspectorPane = ui.el("aside", null, "ac-inspector-pane"); n.inspectorPane.id = "agent-inspector-pane"; n.inspectorPane.setAttribute("aria-label", "Agent 活动、文件与详情");
    n.inspectorTabs = ui.el("nav", null, "ac-inspector-tabs"); n.inspectorTabs.setAttribute("role", "tablist"); n.inspectorTabs.setAttribute("aria-label", "检查器内容");
    n.inspectorActivityTab = ui.button("活动", () => n.setInspectorView?.("activity"), "ac-inspector-tab"); n.inspectorActivityTab.id = "agent-inspector-activity-tab"; n.inspectorActivityTab.setAttribute("role", "tab");
    n.inspectorFilesTab = ui.button("文件", () => n.setInspectorView?.("files"), "ac-inspector-tab"); n.inspectorFilesTab.id = "agent-inspector-files-tab"; n.inspectorFilesTab.setAttribute("role", "tab");
    n.inspectorDetailsTab = ui.button("详情", () => n.setInspectorView?.("details"), "ac-inspector-tab"); n.inspectorDetailsTab.id = "agent-inspector-details-tab"; n.inspectorDetailsTab.setAttribute("role", "tab");
    n.inspectorTabs.append(n.inspectorActivityTab, n.inspectorFilesTab, n.inspectorDetailsTab);
    n.inspectorActivity = ui.el("section", null, "ac-inspector-panel ac-inspector-activity"); n.inspectorActivity.id = "agent-inspector-activity"; n.inspectorActivity.setAttribute("role", "tabpanel"); n.inspectorActivity.setAttribute("aria-labelledby", n.inspectorActivityTab.id);
    n.inspectorFiles = ui.el("section", null, "ac-inspector-panel ac-inspector-files"); n.inspectorFiles.id = "agent-inspector-files"; n.inspectorFiles.setAttribute("role", "tabpanel"); n.inspectorFiles.setAttribute("aria-labelledby", n.inspectorFilesTab.id);
    n.inspectorDetails = ui.el("section", null, "ac-inspector-panel ac-inspector-details"); n.inspectorDetails.id = "agent-inspector-details"; n.inspectorDetails.setAttribute("role", "tabpanel"); n.inspectorDetails.setAttribute("aria-labelledby", n.inspectorDetailsTab.id);
    n.activityDetails = ui.el("section", null, "ac-inspector-activity-details"); n.activityDetails.append(n.trajectoryMeta, n.telemetryStatus, n.telemetryToolbar, n.telemetryHost, n.trajectoryList);
    n.inspectorActivity.append(n.statusDock, n.statusRunningGroup, n.tools, n.activityDetails);
    n.inspectorFileSummary = ui.el("p", "当前白板、计算结果、候选和恢复点会集中显示在这里。", "ac-note");
    n.inspectorFilesHost = ui.el("div", null, "ac-inspector-files-host"); n.inspectorFiles.append(n.inspectorFileSummary, n.inspectorFilesHost, n.checkpointSection);
    n.inspectorDetailsHost = ui.el("div", null, "ac-inspector-details-host"); n.inspectorDetails.append(ui.el("p", "选择消息、运行步骤或结果后查看完整说明。", "ac-note"), n.inspectorDetailsHost, n.taskSettings);
    n.inspectorPane.append(n.inspectorTabs, n.inspectorActivity, n.inspectorFiles, n.inspectorDetails); root.append(n.inspectorPane);
    n.inspectorScrim = ui.el("button", null, "ac-inspector-scrim"); n.inspectorScrim.type = "button"; n.inspectorScrim.hidden = true; n.inspectorScrim.setAttribute("aria-label", "关闭 Agent 检查器"); root.append(n.inspectorScrim);
    const inspectorPanels = {activity:n.inspectorActivity, files:n.inspectorFiles, details:n.inspectorDetails};
    const inspectorTabs = {activity:n.inspectorActivityTab, files:n.inspectorFilesTab, details:n.inspectorDetailsTab};
    n.setInspectorView = view => {
      const selected = Object.prototype.hasOwnProperty.call(inspectorPanels, view) ? view : "activity";
      n.inspectorPane.dataset.view = selected;
      for (const [key,panel] of Object.entries(inspectorPanels)) {
        panel.hidden = key !== selected;
        inspectorTabs[key].setAttribute("aria-selected", String(key === selected));
        inspectorTabs[key].tabIndex = key === selected ? 0 : -1;
      }
      return selected;
    };
    n.setInspectorView("activity");
    n.actionShelf = ui.el("section", null, "ac-action-shelf"); n.actionShelf.hidden = true; n.actionShelf.setAttribute("aria-label", "需要处理");
    n.actionShelfHead = ui.el("div", null, "ac-action-shelf-head"); n.actionShelfHeadline = ui.el("strong", "需要处理");
    n.actionShelfCount = ui.el("span", null, "ac-action-shelf-count");
    n.actionShelfMore = ui.button("查看全部", () => {
      n.actionShelfExpanded = !n.actionShelfExpanded;
      n.actionShelf.dataset.expanded = String(Boolean(n.actionShelfExpanded));
      for (const child of n.actionShelfBody.children || []) child.hidden = child.dataset?.attentionActive !== "true" || (!n.actionShelfExpanded && child.dataset?.attentionPrimary !== "true");
      n.actionShelfMore.textContent = n.actionShelfExpanded ? "只看首要事项" : "查看全部";
    }, "ac-action-shelf-more"); n.actionShelfHead.append(n.actionShelfHeadline, n.actionShelfCount, n.actionShelfMore);
    n.actionShelfBody = ui.el("div", null, "ac-action-shelf-body"); n.actionShelf.append(n.actionShelfHead, n.actionShelfBody); n.chatPane.append(n.actionShelf);
    n.contextWarning = ui.el("p", null, "ac-warning"); n.actionShelfBody.append(n.contextWarning);
    n.questionHost = ui.el("div", null, "ac-question-host"); n.actionShelfBody.append(n.questionHost);
    n.appActions = ui.el("details", null, "ac-app-actions"); n.appActions.open = true; n.appActionsSummary = ui.el("summary", "需要你完成的操作"); n.appActionsBody = ui.el("div"); n.appActions.append(n.appActionsSummary, n.appActionsBody); n.actionShelfBody.append(n.appActions);
    n.candidate = ui.el("section", null, "ac-candidate"); n.error = ui.el("p", null, "ac-error"); n.notice = ui.el("p", null, "ac-notice"); n.notice.setAttribute("role", "status");
    n.statusNotice = ui.el("p", null, "ac-notice ac-status-notice"); n.statusNotice.setAttribute("role", "status");
    n.technicalDetails = ui.el("details", null, "ac-technical-details"); n.technicalDetails.hidden = true;
    n.technicalDetailsBody = ui.el("pre", null, "ac-technical-details-body"); n.technicalDetails.append(ui.el("summary", "技术详情"), n.technicalDetailsBody);
    n.compatibilityGuidance = ui.el("details", null, "ac-compatibility-guidance"); n.compatibilityGuidance.hidden = true;
    n.compatibilityPrompt = ui.el("pre", null, "ac-compatibility-prompt"); n.compatibilityPrompt.textContent = agent.compatibilityPrompt || "";
    n.compatibilityCopy = ui.button("复制给 Agent 的提示词", async event => {
      const button = event?.currentTarget || n.compatibilityCopy, copied = await agent.copyCompatibilityPrompt?.();
      button.textContent = copied ? "已复制" : "复制失败，请手动选择文本";
    }, "ac-compatibility-copy");
    n.compatibilityGuidance.append(ui.el("summary", "如何让 Agent 处理当前文件"), ui.el("p", "这不会自动迁移、创建会话或发送消息。请复制后由你主动发给 Agent。", "ac-note"), n.compatibilityPrompt, n.compatibilityCopy);
    n.technicalDetails.append(n.compatibilityGuidance);
    n.actionShelfBody.append(n.candidate, n.error, n.notice); n.inspectorDetails.append(n.statusNotice, n.technicalDetails);
    n.composerRoot = ui.el("section", null, "ac-composer"); n.composerRoot.setAttribute("aria-label", "给 AI 助手发送消息");
    n.composerContextDetails = ui.el("details", null, "ac-composer-context-details"); n.composerContextSummary = ui.el("summary", "本次发送上下文");
    n.composerContext = ui.el("div", null, "ac-composer-context"); n.composerContext.setAttribute("aria-label", "本次消息使用的文档");
    n.composerContextDetails.append(n.composerContextSummary, n.composerContext); n.composerRoot.append(n.composerContextDetails);
    const compact = ui.el("div", null, "ac-composer-controls"), modelField = ui.field("模型", n.model), effortField = ui.field("推理", n.effort); modelField.classList.add("ac-compact-field"); effortField.classList.add("ac-compact-field");
    n.sendMode = ui.select([{value:"enter",label:"Enter 发送"},{value:"ctrl_enter",label:"Ctrl+Enter 发送"}], "enter", () => {}); const sendModeField = ui.field("发送键", n.sendMode); sendModeField.classList.add("ac-compact-field", "ac-send-mode-field"); compact.append(modelField, effortField, sendModeField);
    n.composerOptions = ui.el("details", null, "ac-composer-options"); n.modelPickerSummary = ui.el("summary", null, "ac-model-picker-summary"); n.modelPickerText = ui.el("span", "选择模型 · 推理", "ac-model-picker-text"); n.modelPickerSummary.append(n.modelPickerText); n.composerOptions.append(n.modelPickerSummary, compact);
    n.refreshModelPicker = () => {
      const model = n.model?.value || "选择模型", effort = n.effort?.value || "推理";
      n.modelPickerText.textContent = `${model} · ${effort}`;
      n.modelPickerSummary.setAttribute("title", `${model} · 推理档位 ${effort}`);
    };
    n.model.addEventListener("change", n.refreshModelPicker); n.effort.addEventListener("change", n.refreshModelPicker);
    n.inboxControls = ui.el("details", null, "ac-inbox-compose-controls"); n.inboxControlsSummary = ui.el("summary", "排队");
    n.inboxMode = ui.select([{value:"queue",label:"排队（默认）"},{value:"steer",label:"引导当前任务"},{value:"stop_and_send",label:"停止后发送"}], "queue", () => agent.refresh?.()); n.inboxControls.append(n.inboxControlsSummary, ui.field("运行中消息方式", n.inboxMode)); n.inboxControls.hidden = true;
    n.input = ui.el("textarea"); n.input.rows = 2; n.input.placeholder = "输入任务或后续要求；Shift＋Enter 换行"; n.input.setAttribute("aria-label", "给 Agent 的任务"); n.input.setAttribute("aria-describedby", "agent-composer-help"); n.composerRoot.append(n.input);
    n.send = ui.button("发送", (...args) => agent.send?.(...args), "primary ac-composer-primary"); n.activeSend = ui.button("加入队列", (...args) => agent.enqueueActiveMessage?.(...args), "primary ac-composer-active-send"); n.activeSend.hidden = true;
    n.stop = ui.button("停止", () => agent.stop?.(), "primary ac-composer-primary ac-stop-primary"); n.stop.hidden = true;
    const footer = ui.el("div", null, "ac-composer-actions"); footer.append(n.composerOptions, n.inboxControls, n.send, n.activeSend, n.stop); n.composerRoot.append(footer);
    n.composerHelp = ui.el("p", "Enter 发送 · Shift＋Enter 换行", "ac-note"); n.composerHelp.id = "agent-composer-help"; n.composerRoot.append(n.composerHelp); n.chatPane.append(n.composerRoot);

    const views = Object.freeze({
      mount:Object.freeze({root,badge}),
      panes:Object.freeze({settings:n.controls,details:n.detailsPane,chat:n.chatPane,inspector:n.inspectorPane}),
      projections:Object.freeze({chat:n.chatView,trajectory:n.trajectoryView,activity:n.inspectorActivity,files:n.inspectorFiles,details:n.inspectorDetails}),
      overlays:Object.freeze({activity:n.activityFeedbackHost,toasts:n.toastHost}),
    });
    return Object.freeze({nodes:n,views});
  }

  global.AgentWorkbenchLayout = Object.freeze({create});
})(window);
