(function (global) {
  "use strict";

  const CONTRACT = "agent-history-window-v1";
  const MAX_NODES = 500;
  const MAX_TEXT = 32_000;
  const MAX_LABEL = 1_000;
  const MAX_REASONS = 32;
  const KINDS = new Set([
    "message", "tool", "activity", "research", "question",
    "candidate", "app_action", "checkpoint", "notice",
  ]);
  const ROLES = new Set(["user", "assistant", "notice", "system"]);
  const CATEGORIES = new Set([
    "runtime", "provider", "web", "app_tool", "engine", "database",
    "skill", "user_wait", "candidate", "app_action", "checkpoint",
  ]);
  const LOCAL_PATH = /^(?:[a-z]:[\\/]|[\\/]{1,2}|file:)/iu;

  const isObject = value => Object.prototype.toString.call(value) === "[object Object]";
  const string = (value, limit) => typeof value === "string" && value.length <= limit ? value : null;
  const integer = value => Number.isSafeInteger(value) && value >= 0 ? value : null;
  const freeze = value => {
    if (value && typeof value === "object" && !Object.isFrozen(value)) {
      for (const child of Object.values(value)) freeze(child);
      Object.freeze(value);
    }
    return value;
  };
  const reject = reason => freeze({ok:false, reason, projection:null});

  function parse(value, expected = {}) {
    if (!isObject(value) || value.contract !== CONTRACT) return reject("history_window_contract_invalid");
    const sessionId = string(value.session_id, 256);
    const revision = integer(value.projection_revision);
    const sourceCursor = integer(value.source_cursor);
    if (!sessionId || expected.session_id && sessionId !== expected.session_id
        || revision === null || sourceCursor === null) {
      return reject("history_window_identity_invalid");
    }
    if (!isObject(value.anchor)) return reject("history_window_anchor_invalid");
    const anchorTurn = string(value.anchor.turn_id, 256);
    const anchorSeq = integer(value.anchor.source_seq);
    const anchorNode = value.anchor.stable_node_id === null
      ? null : string(value.anchor.stable_node_id, 256);
    if (!anchorTurn || anchorSeq === null || value.anchor.stable_node_id !== null && !anchorNode
        || expected.turn_id && anchorTurn !== expected.turn_id
        || integer(expected.source_seq) !== null && anchorSeq !== expected.source_seq
        || expected.stable_node_id && anchorNode !== expected.stable_node_id) {
      return reject("history_window_anchor_invalid");
    }
    if (!Array.isArray(value.partial_reasons) || value.partial_reasons.length > MAX_REASONS
        || value.partial_reasons.some(reason => !string(reason, 128))) {
      return reject("history_window_completeness_invalid");
    }
    const completeness = value.completeness === "complete" ? "complete"
      : value.completeness === "partial" ? "partial" : null;
    if (!completeness || !Array.isArray(value.nodes) || value.nodes.length > MAX_NODES) {
      return reject("history_window_nodes_invalid");
    }
    const ids = new Set();
    const nodes = [];
    let previousSeq = -1;
    for (const raw of value.nodes) {
      if (!isObject(raw)) return reject("history_window_node_invalid");
      const id = string(raw.stable_node_id, 256);
      const turnId = string(raw.turn_id, 256);
      const sourceSeq = integer(raw.source_seq);
      const kind = string(raw.kind, 64);
      const role = raw.role === null || raw.role === undefined ? null : string(raw.role, 32);
      const label = raw.label === null || raw.label === undefined ? "" : string(raw.label, MAX_LABEL);
      const text = raw.text === null || raw.text === undefined ? "" : string(raw.text, MAX_TEXT);
      const state = raw.state === null || raw.state === undefined ? null : string(raw.state, 64);
      const workflowStage = raw.workflow_stage === null || raw.workflow_stage === undefined
        ? null : string(raw.workflow_stage, 128);
      const segmentId = raw.segment_id == null ? null : string(raw.segment_id, 256);
      const operationId = raw.operation_id == null ? null : string(raw.operation_id, 256);
      const category = raw.category == null ? null : string(raw.category, 64);
      const resultCode = raw.result_code == null ? null : string(raw.result_code, 128);
      const resourceRef = raw.resource_ref == null ? null : string(raw.resource_ref, 256);
      const occurredAt = raw.occurred_at == null ? null : string(raw.occurred_at, 128);
      if (!id || ids.has(id) || !turnId || sourceSeq === null || sourceSeq < previousSeq
          || !KINDS.has(kind) || role !== null && !ROLES.has(role)
          || label === null || text === null || state === null && raw.state != null
          || workflowStage === null && raw.workflow_stage != null
          || raw.segment_id != null && !segmentId
          || raw.operation_id != null && !operationId
          || raw.category != null && (!category || !CATEGORIES.has(category))
          || raw.result_code != null && !resultCode
          || raw.resource_ref != null && (!resourceRef || LOCAL_PATH.test(resourceRef))
          || raw.occurred_at != null && !occurredAt) {
        return reject("history_window_node_invalid");
      }
      ids.add(id);
      previousSeq = sourceSeq;
      nodes.push(freeze({
        stable_node_id:id, turn_id:turnId, source_seq:sourceSeq,
        kind, category, role, label, text, state,
        segment_id:segmentId, operation_id:operationId,
        result_code:resultCode, resource_ref:resourceRef,
        workflow_stage:workflowStage, occurred_at:occurredAt,
      }));
    }
    if (anchorNode !== null && !ids.has(anchorNode)
        && !(completeness === "partial" && value.partial_reasons.includes("stable_anchor_not_loaded"))) {
      return reject("history_window_anchor_missing");
    }
    if (typeof value.has_older !== "boolean" || typeof value.has_newer !== "boolean") {
      return reject("history_window_pagination_invalid");
    }
    const olderCursor = value.older_cursor == null ? null : integer(value.older_cursor);
    const newerCursor = value.newer_cursor == null ? null : integer(value.newer_cursor);
    const totalNodes = value.total_nodes === undefined ? nodes.length : integer(value.total_nodes);
    if (value.older_cursor != null && olderCursor === null
        || value.newer_cursor != null && newerCursor === null
        || totalNodes === null || totalNodes < nodes.length
        || value.has_older && olderCursor === null
        || value.has_newer && newerCursor === null) {
      return reject("history_window_pagination_invalid");
    }
    return freeze({ok:true, reason:null, projection:{
      contract:CONTRACT,
      session_id:sessionId,
      projection_revision:revision,
      source_cursor:sourceCursor,
      completeness,
      partial_reasons:[...value.partial_reasons],
      anchor:{turn_id:anchorTurn, source_seq:anchorSeq, stable_node_id:anchorNode},
      has_older:value.has_older,
      has_newer:value.has_newer,
      older_cursor:olderCursor,
      newer_cursor:newerCursor,
      total_nodes:totalNodes,
      nodes,
    }});
  }

  const element = (document, tag, value = null, className = "") => {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (value !== null) node.textContent = String(value);
    return node;
  };

  function create(document, host, callbacks = {}) {
    if (!document || !host) throw new TypeError("history_window_host_invalid");
    let projection = null;
    const render = () => {
      host.replaceChildren();
      if (!projection) return;
      const heading = element(document, "header", null, "ac-history-window-head");
      heading.append(
        element(document, "strong", "已核验历史窗口"),
        element(document, "span", projection.completeness === "partial" ? "部分历史" : "完整窗口", "ac-note"),
      );
      const close = element(document, "button", "关闭历史窗口");
      close.type = "button";
      close.addEventListener("click", () => callbacks.onClose?.());
      heading.append(close);
      const body = element(document, "div", null, "ac-history-window-body");
      for (const row of projection.nodes) {
        const card = element(document, "article", null, `ac-history-node ac-history-${row.kind}`);
        card.dataset.stableNodeId = row.stable_node_id;
        card.dataset.turnId = row.turn_id;
        card.tabIndex = 0;
        const title = row.label || ({message:"消息", tool:"工具", activity:"活动", research:"资料", question:"问题", candidate:"候选", app_action:"App 操作", checkpoint:"恢复点", notice:"状态"}[row.kind] || row.kind);
        card.append(
          element(document, "strong", title),
          element(document, "small", `${row.role ? `${row.role} · ` : ""}#${row.source_seq}${row.workflow_stage ? ` · ${row.workflow_stage}` : ""}${row.state ? ` · ${row.state}` : ""}`),
        );
        if (row.text) card.append(element(document, "p", row.text));
        card.addEventListener("click", () => callbacks.onSelect?.(row));
        body.append(card);
      }
      host.append(heading, body);
      if (projection.has_older || projection.has_newer) {
        const pager = element(document, "nav", null, "ac-history-window-pager");
        if (projection.has_older) {
          const older = element(document, "button", "加载更早事件");
          older.type = "button";
          older.addEventListener("click", () => callbacks.onPage?.({
            turn_id:projection.anchor.turn_id,
            source_seq:projection.anchor.source_seq,
            stable_node_id:null,
            before_seq:projection.older_cursor,
            limit:MAX_NODES,
          }));
          pager.append(older);
        }
        if (projection.has_newer) {
          const newer = element(document, "button", "加载更新事件");
          newer.type = "button";
          newer.addEventListener("click", () => callbacks.onPage?.({
            turn_id:projection.anchor.turn_id,
            source_seq:projection.anchor.source_seq,
            stable_node_id:null,
            after_seq:projection.newer_cursor,
            limit:MAX_NODES,
          }));
          pager.append(newer);
        }
        host.append(pager);
      }
      const anchor = projection.anchor.stable_node_id
        ? Array.from(body.children).find(node => node.dataset?.stableNodeId === projection.anchor.stable_node_id)
        : null;
      anchor?.focus?.();
    };
    return Object.freeze({
      update(value, expected = {}) {
        const parsed = parse(value, expected);
        if (!parsed.ok) return parsed;
        projection = parsed.projection;
        render();
        return parsed;
      },
      clear() { projection = null; host.replaceChildren(); },
      snapshot: () => projection,
    });
  }

  global.AgentHistoryWindow = Object.freeze({CONTRACT, parse, create});
})(window);
