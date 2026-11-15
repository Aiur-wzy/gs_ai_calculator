(function (global) {
  "use strict";

  // This renderer consumes only Host-reviewed display facts.  It deliberately
  // has no candidate/App-action mutation methods: buttons report an explicit
  // action identity to the owning controller, which remains the sole writer.
  const KINDS = new Set([
    "message", "tool", "activity", "process_group", "research", "question",
    "candidate", "app_action", "checkpoint", "notice",
  ]);
  const ROLES = new Set(["user", "assistant", "notice", "system"]);
  const MAX = Object.freeze({text:32_000, label:1_000, identity:256, list:32, children:200, depth:3});
  const TYPE_LABELS = Object.freeze({
    tool:"工具调用", activity:"处理过程", process_group:"处理过程",
    research:"联网资料", question:"等待用户", candidate:"候选方案",
    app_action:"App 操作", checkpoint:"文档恢复点", notice:"状态",
  });
  const STATE_LABELS = Object.freeze({
    preparing:"准备中", running:"执行中", streaming:"生成中", waiting:"等待中",
    open:"等待回答", accepted:"已接纳", presented:"等待处理", claimed:"执行中",
    completed:"已完成", settled_success:"已完成", recovered:"已恢复",
    settled_failure:"未完成", failed:"失败", error:"错误", interrupted:"已中断",
    cancelled:"已取消", blocked:"受阻", suspended:"已暂停",
    outcome_unknown:"结果待确认", unknown_after_interruption:"结果待确认",
    finish_tool_calls:"正在执行工具", finish_stop:"生成结束",
  });
  const TOOL_LABELS = Object.freeze({
    gs_read_skill:"读取拉表说明", gs_search_catalog:"搜索资料库",
    gs_query_context:"查询角色与机制资料", gs_read_context:"读取资料内容",
    gs_patch_candidate:"修改候选方案", gs_patch_whiteboard:"修改白板",
    gs_transition_phase:"切换工作阶段", gs_record_finding:"记录核查结论",
    gs_validate_execution_bindings:"检查事件绑定", gs_validate_agent_materialization:"检查动作展开",
    gs_calculate:"执行计算", web_search:"联网搜索", web_fetch:"读取网页资料",
  });

  const isObject = value => Object.prototype.toString.call(value) === "[object Object]";
  const string = (value, limit) => typeof value === "string" && value.length <= limit ? value : null;
  const optional = (value, limit, code = "timeline_text_invalid") => {
    if (value == null) return null;
    const result = string(value, limit);
    if (result === null) throw new TypeError(code);
    return result;
  };
  const integer = value => Number.isSafeInteger(value) && value >= 0 ? value : null;
  const freeze = value => {
    if (value && typeof value === "object" && !Object.isFrozen(value)) {
      for (const child of Object.values(value)) freeze(child);
      Object.freeze(value);
    }
    return value;
  };

  function stringList(value, limit = MAX.list) {
    if (value == null) return [];
    if (!Array.isArray(value) || value.length > limit) throw new TypeError("timeline_detail_list_invalid");
    return value.map(item => {
      const checked = string(item, 512);
      if (!checked) throw new TypeError("timeline_detail_list_invalid");
      return checked;
    });
  }

  function normalizeDetails(value = {}) {
    if (!isObject(value)) throw new TypeError("timeline_details_invalid");
    const result = {
      source:optional(value.source, 128, "timeline_details_invalid"),
      elapsed_ms:value.elapsed_ms == null ? null : integer(value.elapsed_ms),
      input_summary:optional(value.input_summary, MAX.text, "timeline_details_invalid"),
      result_summary:optional(value.result_summary, MAX.text, "timeline_details_invalid"),
      recovery_state:optional(value.recovery_state, 128, "timeline_details_invalid"),
      root_cause:optional(value.root_cause, MAX.text, "timeline_details_invalid"),
      failure_stage:optional(value.failure_stage, 128, "timeline_details_invalid"),
      provider_disposition:optional(value.provider_disposition, 256, "timeline_details_invalid"),
      query:optional(value.query, 4096, "timeline_details_invalid"),
      domains:stringList(value.domains),
      sources:stringList(value.sources),
      revision:value.revision == null ? null : integer(value.revision),
      diff_count:value.diff_count == null ? null : integer(value.diff_count),
      can_apply:value.can_apply == null ? null : value.can_apply === true,
      integrity:optional(value.integrity, 128, "timeline_details_invalid"),
      reason:optional(value.reason, MAX.text, "timeline_details_invalid"),
      duration:optional(value.duration, 256, "timeline_details_invalid"),
      value:optional(value.value, 256, "timeline_details_invalid"),
      process_summary:optional(value.process_summary, MAX.text, "timeline_details_invalid"),
      recovery_actions:[],
      children:[],
    };
    if (value.elapsed_ms != null && result.elapsed_ms === null
        || value.revision != null && result.revision === null
        || value.diff_count != null && result.diff_count === null
        || value.can_apply != null && typeof value.can_apply !== "boolean") {
      throw new TypeError("timeline_details_invalid");
    }
    if (value.recovery_actions != null) {
      if (!Array.isArray(value.recovery_actions) || value.recovery_actions.length > 8) {
        throw new TypeError("timeline_recovery_actions_invalid");
      }
      result.recovery_actions = value.recovery_actions.map(raw => {
        if (!isObject(raw)) throw new TypeError("timeline_recovery_action_invalid");
        const id = string(raw.action_id, 128), label = string(raw.label, 256);
        if (!id || !label || raw.enabled != null && typeof raw.enabled !== "boolean") {
          throw new TypeError("timeline_recovery_action_invalid");
        }
        return {action_id:id, label, enabled:raw.enabled !== false};
      });
    }
    if (value.children != null) {
      if (!Array.isArray(value.children) || value.children.length > MAX.children) {
        throw new TypeError("timeline_children_invalid");
      }
      result.children = [...value.children];
    }
    return freeze(result);
  }

  function normalizeNode(value) {
    if (!isObject(value)) throw new TypeError("timeline_node_invalid");
    const stableId = string(value.stable_node_id || value.node_id, MAX.identity);
    const turnId = string(value.turn_id, MAX.identity);
    const kind = string(value.kind, 64);
    const category = string(value.category, 64);
    const sourceSeq = integer(value.source_seq);
    const role = value.role == null ? null : string(value.role, 32);
    if (!stableId || !turnId || !KINDS.has(kind) || !category || sourceSeq === null
        || role !== null && !ROLES.has(role)) throw new TypeError("timeline_node_invalid");
    const label = string(value.label, MAX.label);
    const nodeText = optional(value.text, MAX.text);
    if (!label || value.text != null && nodeText === null) throw new TypeError("timeline_node_invalid");
    return freeze({
      stable_node_id:stableId,
      turn_id:turnId,
      segment_id:optional(value.segment_id, MAX.identity, "timeline_node_invalid"),
      operation_id:optional(value.operation_id, MAX.identity, "timeline_node_invalid"),
      source_seq:sourceSeq,
      kind,
      category,
      role,
      label,
      text:nodeText,
      state:optional(value.state, 128, "timeline_node_invalid"),
      result_code:optional(value.result_code, 128, "timeline_node_invalid"),
      resource_ref:optional(value.resource_ref, MAX.identity, "timeline_node_invalid"),
      workflow_stage:optional(value.workflow_stage, 128, "timeline_node_invalid"),
      occurred_at:optional(value.occurred_at, 128, "timeline_node_invalid"),
    });
  }

  const element = (document, tag, value = null, className = "") => {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (value !== null) node.textContent = String(value);
    return node;
  };
  const stateLabel = value => value == null ? "状态未知" : STATE_LABELS[value] || "状态待确认";
  const nodeLabel = node => TOOL_LABELS[node.label]
    || (/^gs_[a-z0-9_]+$/u.test(node.label) ? TYPE_LABELS[node.kind] || "App 工具" : node.label);

  function appendField(document, host, label, value, className = "") {
    const row = element(document, "div", null, `ac-timeline-field ${className}`.trim());
    row.append(
      element(document, "dt", label),
      element(document, "dd", value == null || value === "" ? "未提供" : value),
    );
    host.append(row);
  }

  function appendMetadata(document, host, node, details) {
    const values = [
      details.source || node.category,
      node.workflow_stage,
      details.elapsed_ms === null ? null : `${details.elapsed_ms} ms`,
      `#${node.source_seq}`,
    ].filter(Boolean);
    host.append(element(document, "small", values.join(" · "), "ac-timeline-meta"));
  }

  function isFailure(node, details) {
    return ["failed", "error", "interrupted", "outcome_unknown"].includes(node.state)
      || Boolean(node.result_code && node.result_code !== "code_0")
      || Boolean(details.root_cause);
  }

  function appendError(document, host, node, details, options) {
    // The Host projects a successful numeric tool settlement (result code 0)
    // as the bounded identity string `code_0`.  It remains useful provenance,
    // but it is not a failure signal.  A genuinely failed state must still
    // render diagnostics even if a malformed upstream record pairs it with 0.
    if (!isFailure(node, details)) return;
    const panel = element(document, "section", null, "ac-timeline-error");
    panel.setAttribute("role", "alert");
    panel.append(element(document, "strong", "失败与恢复"));
    const fields = element(document, "dl");
    appendField(document, fields, "首因", details.root_cause || node.text || node.result_code || "未知");
    appendField(document, fields, "失败阶段", details.failure_stage || node.workflow_stage || "未知");
    appendField(document, fields, "Provider 请求", details.provider_disposition || "未知，不能据此判断是否计费");
    appendField(document, fields, "结果码", node.result_code || "未提供");
    panel.append(fields);
    if (details.recovery_actions.length) {
      const actions = element(document, "div", null, "ac-timeline-recovery-actions");
      for (const action of details.recovery_actions) {
        const button = element(document, "button", action.label);
        button.type = "button";
        button.disabled = !action.enabled;
        button.dataset.actionId = action.action_id;
        button.addEventListener("click", () => options.onRecoveryAction?.({
          action_id:action.action_id,
          stable_node_id:node.stable_node_id,
          turn_id:node.turn_id,
        }));
        actions.append(button);
      }
      panel.append(actions);
    }
    host.append(panel);
  }

  function renderTool(document, body, node, details) {
    const fields = element(document, "dl", null, "ac-timeline-tool-fields");
    appendField(document, fields, "来源", details.source || node.category);
    appendField(document, fields, "耗时", details.elapsed_ms === null ? "未知" : `${details.elapsed_ms} ms`);
    appendField(document, fields, "参数摘要", details.input_summary || "未提供受审摘要");
    appendField(document, fields, "结果摘要", details.result_summary || node.text || "未提供受审摘要");
    appendField(document, fields, "恢复状态", details.recovery_state || "无恢复状态");
    body.append(fields);
  }

  function renderResearch(document, body, node, details) {
    const fields = element(document, "dl", null, "ac-timeline-research-fields");
    appendField(document, fields, "查询", details.query || node.text || "未提供");
    appendField(document, fields, "检索域", details.domains.length ? details.domains.join("、") : "未限定");
    appendField(document, fields, "来源", details.sources.length ? details.sources.join("、") : "尚无来源摘要");
    body.append(fields);
  }

  function renderDomain(document, body, node, details) {
    const fields = element(document, "dl", null, "ac-timeline-domain-fields");
    if (node.kind === "candidate") {
      appendField(document, fields, "Revision", details.revision === null ? "未提供" : details.revision);
      appendField(document, fields, "差异数量", details.diff_count === null ? "未提供" : details.diff_count);
      appendField(document, fields, "可应用", details.can_apply === null ? "尚未核验" : details.can_apply ? "是" : "否");
    } else if (node.kind === "app_action") {
      appendField(document, fields, "操作状态", stateLabel(node.state));
      appendField(document, fields, "请求原因", details.reason || node.text || "未提供");
    } else if (node.kind === "question") {
      appendField(document, fields, "问题状态", stateLabel(node.state));
      appendField(document, fields, "问题", node.text || "未提供");
    } else if (node.kind === "checkpoint") {
      appendField(document, fields, "完整性", details.integrity || "尚未核验");
      appendField(document, fields, "来源", details.source || node.resource_ref || "未提供");
    }
    body.append(fields);
  }

  function renderProcessGroup(document, body, node, details, options, depth) {
    const group = element(document, "details", null, "ac-timeline-process-group");
    group.open = ["running", "failed", "waiting"].includes(node.state);
    group.append(element(
      document,
      "summary",
      details.process_summary || node.text || `${details.children.length} 个步骤`,
    ));
    if (depth >= MAX.depth) {
      group.append(element(document, "p", "过程嵌套过深，已停止展开。", "ac-note"));
    } else {
      for (const child of details.children) {
        const childNode = child?.node || child;
        const childDetails = child?.details || {};
        group.append(renderNode(document, childNode, {...options, details:childDetails}, depth + 1));
      }
    }
    body.append(group);
  }

  function renderNode(document, rawNode, options = {}, depth = 0) {
    if (!document || typeof document.createElement !== "function") {
      throw new TypeError("timeline_document_invalid");
    }
    const node = normalizeNode(rawNode);
    const resolved = typeof options.resolveDetails === "function"
      ? options.resolveDetails(node) : options.details;
    const details = normalizeDetails(resolved || {});
    if (node.kind === "message") {
      if (!global.AgentMessageRenderer?.createMessage) {
        throw new TypeError("timeline_message_renderer_unavailable");
      }
      const controller = global.AgentMessageRenderer.createMessage(document, {
        role:node.role === "user" ? "user" : "assistant",
        text:node.text || "",
        reasoning:String(node.operation_id || node.stable_node_id || "").startsWith("reasoning:"),
        copyText:options.copyText,
        copyCode:options.copyCode,
        confirmExternal:options.confirmExternal,
        openExternal:options.openExternal,
      });
      controller.article.dataset.stableNodeId = node.stable_node_id;
      controller.article.dataset.turnId = node.turn_id;
      controller.article.dataset.sourceSeq = String(node.source_seq);
      controller.article.append(element(
        document,
        "small",
        [node.workflow_stage, `#${node.source_seq}`].filter(Boolean).join(" · "),
        "ac-timeline-meta",
      ));
      return controller.article;
    }

    const article = element(document, "article", null, `ac-timeline-node ac-timeline-${node.kind}`);
    article.dataset.stableNodeId = node.stable_node_id;
    article.dataset.turnId = node.turn_id;
    article.dataset.sourceSeq = String(node.source_seq);
    article.dataset.category = node.category;
    if (node.state) article.dataset.state = node.state;
    const header = element(document, "header", null, "ac-timeline-node-header");
    const title = element(document, "strong", nodeLabel(node));
    if (title.textContent !== node.label) title.setAttribute("title", node.label);
    const status = element(document, "span", stateLabel(node.state), "ac-status-chip");
    if (node.state) status.setAttribute("title", `技术状态：${node.state}`);
    header.append(title, status);
    appendMetadata(document, header, node, details);
    const body = element(document, "div", null, "ac-timeline-node-body");
    if (node.kind === "tool") renderTool(document, body, node, details);
    else if (node.kind === "research") renderResearch(document, body, node, details);
    else if (["candidate", "app_action", "question", "checkpoint"].includes(node.kind)) {
      renderDomain(document, body, node, details);
    } else if (["activity", "process_group"].includes(node.kind) && details.children.length) {
      renderProcessGroup(document, body, node, details, options, depth);
    } else if (node.text) {
      body.append(element(document, "p", node.text));
    }
    appendError(document, body, node, details, options);
    const compact = options.projection !== "trajectory"
      && ["tool", "research"].includes(node.kind) && !isFailure(node, details);
    if (compact) {
      article.dataset.density = "summary";
      const summaryText = details.result_summary || node.text || details.query || "已完成";
      header.append(element(document, "span", summaryText, "ac-timeline-summary-text"));
      const disclosure = element(document, "details", null, "ac-timeline-node-details");
      disclosure.append(element(document, "summary", "查看详情"), body);
      article.append(header, disclosure);
    } else article.append(header, body);
    article.tabIndex = 0;
    article.addEventListener("click", () => options.onSelect?.(node));
    return article;
  }

  global.AgentTimelineNodeRenderer = Object.freeze({
    KINDS:Object.freeze([...KINDS]),
    MAX,
    normalizeNode,
    normalizeDetails,
    stateLabel,
    renderNode,
  });
})(window);
