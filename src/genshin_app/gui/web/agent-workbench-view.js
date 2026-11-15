"use strict";

// Renderer-only workbench views.  The controller supplies already admitted
// facts and callbacks; this module never calls the Host, mutates a session, or
// owns a domain transaction.
(function (global) {
  const state = () => global.AgentWorkbenchSelectors;
  const ui = () => global.UI;

  function project(input) {
    return state().project(input);
  }

  const STATUS_ALERTS = new Set([
    "missing", "error", "offline", "rate_limited", "auth_failed", "unhealthy", "failed",
    "action_required", "recovery_required", "dirty", "candidate_ready", "conflict", "outcome_unknown",
    "held", "needs_rebind",
  ]);
  const ACTIVE_STATES = new Set([
    "starting", "reconnecting", "opening", "preparing", "streaming", "using_tool",
    "resuming", "promoting", "cancelling", "stopping",
  ]);
  const FAILURE_STATES = new Set(["error", "auth_failed", "unhealthy", "failed", "conflict", "outcome_unknown"]);

  function attentionItems(session, projection) {
    const axes = projection?.axes || {}, items = [], add = (id, priority, label) => {
      if (!items.some(item => item.id === id)) items.push({id, priority, label});
    };
    if (axes.conversation_state === "recovery_required"
        || ["conflict", "outcome_unknown"].includes(axes.document_state)
        || ["needs_rebind", "outcome_unknown"].includes(axes.inbox_state)) {
      add("recovery", 100, "需要恢复或确认结果");
    }
    if (["awaiting_input_live", "awaiting_input_suspended"].includes(axes.turn_state)) {
      add("question", 90, "Agent 正在等待你的回答");
    }
    for (const action of session?.appActions || []) {
      if (["queued", "resolving", "presented", "claimed_for_external_execution"].includes(action?.status)
          || action?.execution_outcome_unknown === true || action?.pendingSettlement) {
        add(`app:${action.action_id || items.length}`, action.execution_outcome_unknown === true ? 100 : 80, "有 App 操作需要处理");
      }
    }
    if (session?.candidate && !session?.applied && (session.candidate.ready === true || session.candidate.stale === true)) {
      add("candidate", 70, session.candidate.stale ? "候选已过期" : "方案可以审核");
    }
    return items.sort((left, right) => right.priority - left.priority);
  }

  function statusHubProjection(session, projection, busy = null) {
    const axes = projection?.axes || {}, items = attentionItems(session, projection);
    const values = Object.values(axes).filter(Boolean);
    const failure = values.some(value => FAILURE_STATES.has(value));
    const active = values.some(value => ACTIVE_STATES.has(value)) || busy === "detect" || busy === "connect";
    const headline = busy === "detect" ? "正在检测本地助手"
      : busy === "connect" ? "正在验证模型服务"
      : projection?.summary || "等待配置";
    return Object.freeze({headline, attention_count:items.length, attention:items,
      severity:failure ? "failure" : items.length ? "attention" : active ? "running" : "idle",
      visible:failure || active || items.length > 0});
  }

  function renderStatusAxes({host, projection}) {
    if (!host) return;
    const allRows = state().axisRows(projection);
    const rows = allRows;
    const signature = JSON.stringify(rows.map(row => [row.id, row.value, row.label]));
    if (host.acSignature !== signature) {
      host.acSignature = signature;
      host.replaceChildren();
      for (const row of rows) {
        const chip = ui().el("span", `${row.title} · ${row.label}`, "ac-state-chip");
        chip.dataset.axis = row.id;
        chip.dataset.state = row.value || "unknown";
        host.append(chip);
      }
    }
    // All seven authoritative axes remain available, but only inside the
    // on-demand status centre rather than as seven permanent page badges.
    host.hidden = false;
    const detail = [
      projection.runtime_health ? `本地服务 ${projection.runtime_health}` : null,
      projection.failure_code ? `问题代码 ${projection.failure_code}` : null,
    ].filter(Boolean).join(" · ");
    const detailSignature = `${signature}\n${detail}`;
    if (host.acDetailSignature !== detailSignature) {
      host.acDetailSignature = detailSignature;
      host.setAttribute("aria-label", `AI 助手提醒：${rows.map(row => `${row.title}${row.label}`).join("，")}`);
      host.title = detail;
    }
  }

  function renderAttentionShelf({nodes}) {
    if (!nodes?.actionShelf || !nodes.actionShelfBody) return Object.freeze({count:0, primary:null});
    const rows = [
      {id:"error", label:"运行失败", node:nodes.error, active:Boolean(nodes.error?.textContent)},
      {id:"notice_error", label:"操作失败", node:nodes.notice, active:Boolean(nodes.notice?.textContent) && nodes.notice?.classList?.contains?.("ac-error") === true},
      {id:"warning", label:"需要先处理一项阻断", node:nodes.contextWarning, active:Boolean(nodes.contextWarning?.textContent) && nodes.contextWarning?.dataset?.attentionEligible === "true"},
      {id:"question", label:"Agent 正在等待你的回答", node:nodes.questionHost, active:Boolean(nodes.questionHost?.children?.length)},
      {id:"app_action", label:"有 App 操作需要处理", node:nodes.appActions, active:Boolean(nodes.appActionsBody?.children?.length)},
      {id:"candidate", label:"方案可以审核", node:nodes.candidate, active:Boolean(nodes.candidate?.children?.length) && nodes.candidate?.dataset?.attentionEligible === "true"},
    ].filter(row => row.node);
    const active = rows.filter(row => row.active), primary = active[0] || null;
    nodes.actionShelfExpanded = active.length > 1 && nodes.actionShelfExpanded === true;
    for (const row of rows) {
      row.node.dataset.attentionActive = String(row.active);
      row.node.dataset.attentionPrimary = String(row === primary);
      row.node.hidden = !row.active || (!nodes.actionShelfExpanded && row !== primary);
    }
    nodes.actionShelf.hidden = active.length === 0;
    nodes.actionShelf.dataset.expanded = String(Boolean(nodes.actionShelfExpanded));
    nodes.actionShelfHeadline.textContent = primary?.label || "需要处理";
    nodes.actionShelfCount.textContent = active.length > 1 ? `共 ${active.length} 项` : "";
    nodes.actionShelfMore.hidden = active.length < 2;
    nodes.actionShelfMore.textContent = nodes.actionShelfExpanded ? "只看首要事项" : `还有 ${Math.max(0, active.length - 1)} 项`;
    return Object.freeze({count:active.length, primary:primary?.id || null});
  }

  function bindTabs({chatTab, trajectoryTab, onSelect}) {
    const tabs = [{view:"chat", node:chatTab}, {view:"trajectory", node:trajectoryTab}];
    for (let index = 0; index < tabs.length; index++) tabs[index].node.addEventListener("keydown", event => {
      let next = null;
      if (["ArrowRight", "ArrowDown"].includes(event.key)) next = (index + 1) % tabs.length;
      else if (["ArrowLeft", "ArrowUp"].includes(event.key)) next = (index - 1 + tabs.length) % tabs.length;
      else if (event.key === "Home") next = 0;
      else if (event.key === "End") next = tabs.length - 1;
      if (next === null) return;
      event.preventDefault();
      onSelect(tabs[next].view);
      tabs[next].node.focus();
    });
  }

  function render({
    nodes, session, projection, workbench, busy = null, labels = {},
    onAllTurns, onSelectTurn,
  }) {
    if (!nodes?.workbenchViews) return;
    renderStatusAxes({host: nodes.statusAxes, projection});
    const hub = statusHubProjection(session, projection, busy);
    nodes.status.textContent = hub.headline;
    if (nodes.statusDock) {
      const hasAlert = hub.severity === "failure";
      nodes.statusDock.dataset.state = hub.severity;
      nodes.statusDock.dataset.hasAlert = String(hasAlert);
      nodes.statusDock.dataset.hasAttention = String(hub.attention_count > 0);
      nodes.statusDock.hidden = !hub.visible && nodes.statusDock.dataset.activityVisible !== "true";
    }
    if (nodes.attentionCount) {
      nodes.attentionCount.hidden = hub.attention_count === 0;
      nodes.attentionCount.textContent = hub.attention_count ? `${hub.attention_count} 项需处理` : "";
    }
    if (nodes.statusAttentionGroup && nodes.statusAttentionList) {
      const attentionSignature = JSON.stringify(hub.attention.map(item => [item.id, item.label]));
      if (nodes.statusAttentionList.acSignature !== attentionSignature) {
        nodes.statusAttentionList.acSignature = attentionSignature;
        nodes.statusAttentionList.replaceChildren(...hub.attention.map(item => ui().el("li", item.label)));
      }
      nodes.statusAttentionGroup.hidden = hub.attention_count === 0;
    }
    nodes.chatTab.setAttribute("aria-selected", String(workbench.view === "chat"));
    nodes.trajectoryTab.setAttribute("aria-selected", String(workbench.view === "trajectory"));
    nodes.chatTab.tabIndex = workbench.view === "chat" ? 0 : -1;
    nodes.trajectoryTab.tabIndex = workbench.view === "trajectory" ? 0 : -1;
    nodes.chatView.hidden = workbench.view !== "chat";
    nodes.trajectoryView.hidden = workbench.view !== "trajectory";

    const outline = state().turnOutline(session);
    if (workbench.turn_id && !outline.some(row => row.id === workbench.turn_id)) workbench.turn_id = null;
    const outlineSignature = JSON.stringify([
      session?.session_id || null, workbench.turn_id,
      outline.map(turn => [turn.id, turn.label, turn.messages, turn.tools, turn.active]),
    ]);
    if (nodes.turnRail.acSignature !== outlineSignature) {
      nodes.turnRail.acSignature = outlineSignature;
      nodes.turnRail.replaceChildren();
      const allButton = ui().button("全部", onAllTurns, "ac-turn-button");
      allButton.setAttribute("aria-pressed", String(!workbench.turn_id));
      nodes.turnRail.append(allButton);
      for (const turn of outline) {
        const button = ui().button(`${turn.label} · ${turn.messages + turn.tools}`, () => onSelectTurn(turn.id), "ac-turn-button");
        button.setAttribute("aria-pressed", String(workbench.turn_id === turn.id));
        if (turn.active) button.dataset.active = "true";
        nodes.turnRail.append(button);
      }
    }
    nodes.turnRail.hidden = !outline.length;
    if (nodes.turnTools) nodes.turnTools.hidden = !outline.length;
    if (nodes.turnToolsSummary) {
      const selected = outline.find(row => row.id === workbench.turn_id);
      nodes.turnToolsSummary.textContent = selected?.label || (outline.length ? "全部轮次" : "无轮次");
    }
    nodes.search.value = workbench.query;

    // Hidden projections do no work.  This matters for long conversations and
    // also keeps screen readers from observing churn in an inactive view.
    if (workbench.view !== "trajectory") return;
    const sourceSignature = JSON.stringify([
      session?.session_id || null,
      Number.isSafeInteger(session?.projectionRevision) ? session.projectionRevision : 0,
      workbench.query || "", workbench.turn_id || null,
      session?.messages?.length || 0, session?.tools?.length || 0,
    ]);
    if (nodes.trajectorySourceSignature === sourceSignature) return;
    nodes.trajectorySourceSignature = sourceSignature;
    const rows = state().trajectory(session, workbench);
    const trajectorySignature = JSON.stringify([
      session?.session_id || null,
      Number.isSafeInteger(session?.projectionRevision) ? session.projectionRevision : 0,
      workbench.query || "", workbench.turn_id || null,
      rows.map(row => [row.id, row.seq, row.kind, row.state]),
    ]);
    if (nodes.trajectoryList.acSignature !== trajectorySignature) {
      nodes.trajectoryList.acSignature = trajectorySignature;
      nodes.trajectoryList.replaceChildren();
      if (!session) nodes.trajectoryList.append(ui().el("p", "选择或新建会话后，这里会按 App 已确认的消息与工具事件显示运行详情。", "ac-empty-state"));
      else if (!rows.length) nodes.trajectoryList.append(ui().el("p", workbench.query || workbench.turn_id ? "当前筛选下没有轨迹事件。" : "本会话尚无可展示的真实轨迹事件。", "ac-empty-state"));
      for (const row of rows) {
        const card = ui().el("article", null, `ac-trajectory-row ac-trajectory-${row.kind}`);
        const head = ui().el("div", null, "ac-trajectory-head");
        head.append(ui().el("strong", row.title), ui().el("span", row.seq === null ? "顺序未知" : `#${row.seq}`));
        card.append(head);
        if (row.state) card.append(ui().el("p", labels[row.state] || row.state, "ac-trajectory-state"));
        if (row.detail) card.append(ui().el("p", row.detail, "ac-trajectory-detail"));
        nodes.trajectoryList.append(card);
      }
    }
    nodes.trajectoryMeta.textContent = rows.length ? `显示 ${rows.length} 条真实事件；旧会话缺少顺序或运行记录时会明确标为未知。` : "";
  }

  global.AgentWorkbenchView = Object.freeze({project, attentionItems, statusHubProjection,
    renderStatusAxes, renderAttentionShelf, bindTabs, render});
})(window);
