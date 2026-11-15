(function (global) {
  "use strict";

  // One bounded Renderer projection over the Host-owned durable timeline.
  // This module never manufactures domain facts and never owns a network
  // cursor: every older/newer request names an exact Host turn and source
  // cursor, while the DOM remains a small window over the verified pages.
  const DEFAULT_DOM_LIMIT = 240;
  const DEFAULT_CACHE_LIMIT = 1200;
  const PAGE_LIMIT = 500;
  const NEAR_BOTTOM_PX = 80;
  const ID = /^[A-Za-z0-9][A-Za-z0-9._:-]{0,255}$/u;
  const TERMINAL_INDEX = Object.freeze({
    candidate:new Set(["applied", "aborted", "rejected", "expired"]),
    app_action:new Set(["completed", "cancelled", "failed", "expired"]),
    question:new Set(["settled", "cancelled", "expired", "orphaned"]),
    checkpoint:new Set(["committed", "aborted", "deleted"]),
  });
  const SUCCESS_PROCESS_KINDS = new Set(["tool", "research"]);
  const SUCCESS_PROCESS_STATES = new Set(["completed", "settled_success"]);

  const cleanId = value => typeof value === "string" && ID.test(value) ? value : null;
  const integer = value => Number.isSafeInteger(value) && value >= 0 ? value : null;
  const reject = reason => Object.freeze({ok:false, reason, projection:null});
  const element = (document, tag, value = null, className = "") => {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (value !== null) node.textContent = String(value);
    return node;
  };
  const sameNode = (left, right) => {
    const keys = [
      "stable_node_id", "turn_id", "source_seq", "kind", "role", "label",
      "text", "state", "workflow_stage", "category", "segment_id",
      "operation_id", "result_code", "resource_ref", "occurred_at",
    ];
    return keys.every(key => (left?.[key] ?? null) === (right?.[key] ?? null));
  };
  const isSuccessfulProcess = row => SUCCESS_PROCESS_KINDS.has(row?.kind)
    && SUCCESS_PROCESS_STATES.has(row?.state)
    && (row?.result_code == null || row.result_code === "code_0");
  const sameProcessScope = (left, right) => left?.turn_id === right?.turn_id
    && (left?.segment_id ?? null) === (right?.segment_id ?? null)
    && (left?.workflow_stage ?? null) === (right?.workflow_stage ?? null);

  function processRuns(rows) {
    const result = []; let pending = [];
    const flush = () => {
      if (pending.length > 1) result.push(Object.freeze({kind:"process", rows:Object.freeze([...pending])}));
      else if (pending.length === 1) result.push(Object.freeze({kind:"node", rows:Object.freeze([...pending])}));
      pending = [];
    };
    for (const row of rows) {
      if (isSuccessfulProcess(row) && (!pending.length || sameProcessScope(pending.at(-1), row))) {
        pending.push(row); continue;
      }
      flush();
      if (isSuccessfulProcess(row)) pending.push(row);
      else result.push(Object.freeze({kind:"node", rows:Object.freeze([row])}));
    }
    flush();
    return Object.freeze(result);
  }

  function checkedOutline(value, sessionId) {
    if (!value || typeof value !== "object" || value.session_id !== sessionId
        || !Array.isArray(value.items)) return null;
    const rows = [], seen = new Set(); let previous = -1;
    for (const item of value.items) {
      const id = cleanId(item?.turn_id), start = integer(item?.start_seq), end = integer(item?.end_seq);
      if (!id || seen.has(id) || start === null || end === null || end < start || start <= previous) return null;
      seen.add(id); previous = end; rows.push(Object.freeze({turn_id:id, start_seq:start, end_seq:end}));
    }
    return Object.freeze(rows);
  }

  function fallbackNode(document, row) {
    if (row.kind === "message" && global.AgentMessageRenderer?.createMessage) {
      const controller = global.AgentMessageRenderer.createMessage(document, {
        role:row.role === "user" ? "user" : "assistant",
        text:row.text || "",
        reasoning:String(row.operation_id || row.stable_node_id || "").startsWith("reasoning:"),
      });
      return controller.article;
    }
    const card = element(document, "article", null, `ac-timeline-node ac-timeline-${row.kind}`);
    card.append(
      element(document, "strong", row.label || row.kind),
      element(document, "small", `${row.state || "已记录"} · #${row.source_seq}`),
    );
    if (row.text) card.append(element(document, "p", row.text));
    return card;
  }

  function create(document, host, options = {}) {
    if (!document || !host || typeof document.createElement !== "function") {
      throw new TypeError("conversation_view_host_invalid");
    }
    const domLimit = integer(options.domLimit) || DEFAULT_DOM_LIMIT;
    const cacheLimit = integer(options.cacheLimit) || DEFAULT_CACHE_LIMIT;
    if (domLimit < 20 || cacheLimit < domLimit || cacheLimit > 10000) {
      throw new TypeError("conversation_view_limits_invalid");
    }
    const parsePage = options.parsePage || global.AgentHistoryWindow?.parse;
    if (typeof parsePage !== "function") throw new TypeError("conversation_view_parser_missing");
    const renderNode = typeof options.renderNode === "function"
      ? options.renderNode : row => fallbackNode(document, row);
    const includeRow = options.includeRow == null ? (() => true) : options.includeRow;
    if (typeof includeRow !== "function") throw new TypeError("conversation_view_filter_invalid");
    const schedule = typeof options.schedule === "function"
      ? options.schedule : callback => {
        if (typeof global.requestAnimationFrame === "function") global.requestAnimationFrame(callback);
        else callback();
      };
    const sessions = new Map(); let activeId = null;
    const controlsHost = options.controlsHost || null;

    const status = element(document, controlsHost ? "button" : "p", "尚未读取历史。", "ac-conversation-status ac-note");
    if (controlsHost) { status.type = "button"; status.addEventListener?.("click", () => options.onStatusDetails?.()); }
    status.setAttribute?.("role", "status");
    const older = element(document, "button", "加载更早事件", "ac-conversation-page ac-conversation-older");
    older.type = "button";
    const list = element(document, "div", null, "ac-conversation-timeline");
    list.setAttribute?.("role", "feed");
    // The parent chat surface is a polite live log.  Re-announcing every
    // virtualized row whenever the viewport moves is both noisy and wrong:
    // the concise status/unread controls own announcements, while this feed
    // remains available to ordinary browse/navigation commands.
    list.setAttribute?.("aria-live", "off");
    list.setAttribute?.("aria-busy", "false");
    const newer = element(document, "button", "加载更新事件", "ac-conversation-page ac-conversation-newer");
    newer.type = "button";
    const unread = element(document, "button", "有新事件 · 查看最新", "ac-conversation-unread");
    unread.type = "button"; unread.hidden = true;
    if (controlsHost) {
      controlsHost.replaceChildren(status, unread);
      host.replaceChildren(older, list, newer);
    } else host.replaceChildren(status, older, list, newer, unread);

    const stateFor = sessionId => {
      let state = sessions.get(sessionId);
      if (!state) {
        state = {
          sessionId, nodes:new Map(), order:[], outline:[], coverage:new Map(),
          domNodes:new Map(), processGroups:new Map(), totalNodes:null,
          visibleIds:[], completeness:"partial", partialReasons:["timeline_not_loaded"],
          loading:null, initialized:false, unread:0,
        };
        sessions.set(sessionId, state);
        while (sessions.size > 8) sessions.delete(sessions.keys().next().value);
      }
      return state;
    };
    const current = () => activeId ? stateFor(activeId) : null;
    const nearBottom = () => Number(host.scrollHeight || 0) - Number(host.scrollTop || 0)
      - Number(host.clientHeight || 0) <= NEAR_BOTTOM_PX;
    const captureAnchor = () => {
      const top = Number(host.scrollTop || 0);
      const child = Array.from(list.children || []).find(node =>
        Number(node.offsetTop || 0) + Number(node.offsetHeight || 0) >= top
      );
      return child?.dataset?.stableNodeId
        ? {id:child.dataset.stableNodeId, delta:top - Number(child.offsetTop || 0)} : null;
    };

    function actionIndex(state = current()) {
      if (!state) return Object.freeze([]);
      return Object.freeze(state.order.map(id => state.nodes.get(id)).filter(row => {
        const terminal = TERMINAL_INDEX[row.kind];
        return terminal && !terminal.has(row.state || "");
      }).map(row => Object.freeze({
        stable_node_id:row.stable_node_id,
        turn_id:row.turn_id,
        source_seq:row.source_seq,
        kind:row.kind,
        state:row.state,
        label:row.label,
      })));
    }

    function visibleBounds(state) {
      if (!state?.visibleIds.length || !state.order.length) return {first:-1, last:-1};
      return {
        first:state.order.indexOf(state.visibleIds[0]),
        last:state.order.indexOf(state.visibleIds.at(-1)),
      };
    }

    function hasCached(state, direction) {
      const bounds = visibleBounds(state);
      return direction === "older" ? bounds.first > 0
        : bounds.last >= 0 && bounds.last < state.order.length - 1;
    }

    function boundaryRequest(direction, state = current()) {
      if (!state || !state.order.length) return null;
      const row = state.nodes.get(direction === "older" ? state.order[0] : state.order.at(-1));
      const coverage = state.coverage.get(row.turn_id);
      if (direction === "older" && coverage?.has_older) {
        return {turn_id:row.turn_id, source_seq:coverage.anchor_seq, stable_node_id:null, before_seq:row.source_seq, limit:PAGE_LIMIT};
      }
      if (direction === "newer" && coverage?.has_newer) {
        return {turn_id:row.turn_id, source_seq:coverage.anchor_seq, stable_node_id:null, after_seq:row.source_seq, limit:PAGE_LIMIT};
      }
      const index = state.outline.findIndex(item => item.turn_id === row.turn_id);
      const adjacent = state.outline[index + (direction === "older" ? -1 : 1)];
      if (!adjacent) return null;
      return direction === "older"
        ? {turn_id:adjacent.turn_id, source_seq:adjacent.start_seq, stable_node_id:null, before_seq:adjacent.end_seq + 1, limit:PAGE_LIMIT}
        : {turn_id:adjacent.turn_id, source_seq:adjacent.start_seq, stable_node_id:null, after_seq:Math.max(0, adjacent.start_seq - 1), limit:PAGE_LIMIT};
    }

    function updateControls(state = current()) {
      if (!state) {
        status.textContent = "尚未选择会话。"; older.hidden = true; newer.hidden = true; unread.hidden = true;
        status.title = "尚未选择会话。";
        return;
      }
      const reasons = state.partialReasons.length ? ` · 部分数据：${state.partialReasons.join("、")}` : "";
      const verbose = state.loading
        ? `正在读取${state.loading === "older" ? "更早" : state.loading === "newer" ? "更新" : "最近"}历史…`
        : `已缓存 ${state.order.length} 项 · 当前显示 ${state.visibleIds.length} 项${reasons}`;
      status.textContent = controlsHost
        ? state.loading ? "正在加载历史" : state.completeness === "partial" || state.partialReasons.length ? "历史未完整加载" : "已同步"
        : verbose;
      status.title = verbose;
      status.dataset.completeness = state.completeness;
      older.hidden = !hasCached(state, "older") && !boundaryRequest("older", state);
      newer.hidden = !hasCached(state, "newer") && !boundaryRequest("newer", state);
      older.disabled = Boolean(state.loading); newer.disabled = Boolean(state.loading);
      list.setAttribute?.("aria-busy", String(Boolean(state.loading)));
      unread.hidden = state.unread === 0; unread.textContent = controlsHost ? `${state.unread} 项新事件` : `有 ${state.unread} 项新事件 · 查看最新`;
    }

    const reconcileChildren = children => {
      // Real WebView DOM can retain overlapping keyed nodes, preserving text
      // selection, focus and assistive-technology identity.  Tiny VM test
      // doubles may not implement the mutation primitives, so they use the
      // equivalent bounded replacement fallback.
      if (typeof list.insertBefore !== "function" || typeof list.removeChild !== "function") {
        list.replaceChildren(...children); return;
      }
      for (let index = 0; index < children.length; index++) {
        const wanted = children[index], current = list.children[index] || null;
        if (current !== wanted) list.insertBefore(wanted, current);
      }
      while (list.children.length > children.length) {
        list.removeChild(list.children[list.children.length - 1]);
      }
    };

    function render(state = current(), {direction="replace", wasNearBottom=nearBottom()} = {}) {
      if (!state || state.sessionId !== activeId) return;
      const previousIds = state.visibleIds.slice();
      const anchor = captureAnchor();
      const oldHeight = Number(host.scrollHeight || 0), oldTop = Number(host.scrollTop || 0);
      const rows = state.order;
      let start;
      if (!previousIds.length || direction === "replace" || direction === "append" && wasNearBottom) {
        start = Math.max(0, rows.length - domLimit);
      } else {
        const first = rows.indexOf(previousIds[0]);
        if (direction === "prepend") {
          const room = Math.max(1, previousIds.length < domLimit
            ? domLimit - previousIds.length : Math.floor(domLimit / 2));
          start = Math.max(0, (first < 0 ? 0 : first) - room);
        } else if (direction === "shift-older") {
          start = Math.max(0, (first < 0 ? 0 : first) - Math.max(1, domLimit - 1));
        } else if (direction === "shift-newer") {
          const last = rows.indexOf(previousIds.at(-1));
          start = Math.min(Math.max(0, rows.length - domLimit), Math.max(0, last));
        } else start = Math.max(0, first);
        if (start + domLimit > rows.length) start = Math.max(0, rows.length - domLimit);
      }
      const ids = rows.slice(start, start + domLimit), materialized = [];
      for (const id of ids) {
        const row = state.nodes.get(id);
        // Keep the complete Host timeline in the bounded cache and action
        // index, but only materialize rows assigned to the conversation
        // projection.  Activity, tool, checkpoint and candidate facts remain
        // available to the Inspector without fragmenting the chat transcript.
        if (!includeRow(row)) continue;
        let node = state.domNodes.get(id);
        if (!node) {
          node = renderNode(row, document);
          if (node && typeof node === "object") state.domNodes.set(id, node);
        }
        if (!node || typeof node !== "object") continue;
        node.dataset ||= {}; node.dataset.stableNodeId = id;
        node.dataset.turnId = row.turn_id; node.dataset.sourceSeq = String(row.source_seq);
        node.setAttribute?.("role", "article");
        if (Number.isSafeInteger(state.totalNodes) && state.totalNodes > 0
            && row.source_seq >= 1 && row.source_seq <= state.totalNodes) {
          node.setAttribute?.("aria-posinset", String(row.source_seq));
          node.setAttribute?.("aria-setsize", String(state.totalNodes));
        } else {
          node.removeAttribute?.("aria-posinset");
          node.removeAttribute?.("aria-setsize");
        }
        materialized.push({id, row, node});
      }
      const children = [], activeGroups = new Set(), byRow = new Map(materialized.map(item => [item.row, item]));
      for (const run of processRuns(materialized.map(item => item.row))) {
        if (run.kind === "node") {
          const item = byRow.get(run.rows[0]);
          if (item) children.push(item.node);
          continue;
        }
        const members = run.rows.map(row => byRow.get(row)).filter(Boolean);
        if (members.length < 2) { if (members[0]) children.push(members[0].node); continue; }
        const first = members[0], last = members.at(-1);
        const key = `${first.row.turn_id}\n${first.id}\n${last.id}\n${members.length}`;
        activeGroups.add(key);
        let group = state.processGroups.get(key);
        if (!group) {
          const labels = members.map(item => item.row.label).filter(Boolean);
          const preview = `${labels.slice(0, 2).join("、")}${labels.length > 2 ? "等" : ""}`;
          group = element(document, "article", null, "ac-timeline-node ac-turn-success-process");
          const disclosure = element(document, "details", null, "ac-timeline-process-group");
          disclosure.append(element(document, "summary", `已完成 ${members.length} 个资料与工具步骤${preview ? ` · ${preview}` : ""}`), ...members.map(item => item.node));
          group.append(disclosure);
          group.title = labels.join("、");
          group.dataset.memberCount = String(members.length);
          group.dataset.stableNodeId = first.id;
          group.dataset.turnId = first.row.turn_id;
          group.dataset.sourceSeq = String(first.row.source_seq);
          group.dataset.endSourceSeq = String(last.row.source_seq);
          group.setAttribute?.("aria-label", `已完成 ${members.length} 个资料与工具步骤`);
          state.processGroups.set(key, group);
        }
        if (Number.isSafeInteger(state.totalNodes) && state.totalNodes > 0) {
          group.setAttribute?.("aria-posinset", String(first.row.source_seq));
          group.setAttribute?.("aria-setsize", String(state.totalNodes));
        } else {
          group.removeAttribute?.("aria-posinset");
          group.removeAttribute?.("aria-setsize");
        }
        children.push(group);
      }
      for (const key of state.processGroups.keys()) if (!activeGroups.has(key)) state.processGroups.delete(key);
      reconcileChildren(children); state.visibleIds = ids;
      schedule(() => {
        if (state.sessionId !== activeId) return;
        if (anchor && !wasNearBottom) {
          const node = Array.from(list.children || []).find(child => child.dataset?.stableNodeId === anchor.id);
          if (node) host.scrollTop = Number(node.offsetTop || 0) + anchor.delta;
          else if (direction === "prepend" || direction === "shift-older") {
            host.scrollTop = oldTop + Math.max(0, Number(host.scrollHeight || 0) - oldHeight);
          }
        } else if (direction === "prepend" || direction === "shift-older") {
          host.scrollTop = oldTop + Math.max(0, Number(host.scrollHeight || 0) - oldHeight);
        } else if (wasNearBottom) host.scrollTop = Number(host.scrollHeight || 0);
      });
      if (direction === "shift-newer" && ids.at(-1) === rows.at(-1)) state.unread = 0;
      updateControls(state); options.onActionIndex?.(actionIndex(state));
    }

    function updateCoverage(state, page, direction, rows) {
      const turnId = page.anchor.turn_id, existing = state.coverage.get(turnId);
      const first = rows[0]?.source_seq ?? null, last = rows.at(-1)?.source_seq ?? null;
      let value;
      if (!existing || direction === "replace") {
        value = {anchor_seq:page.anchor.source_seq, min_seq:first, max_seq:last,
          has_older:page.has_older, has_newer:page.has_newer};
      } else if (direction === "prepend") {
        value = {...existing, anchor_seq:page.anchor.source_seq,
          min_seq:first ?? existing.min_seq, has_older:page.has_older};
      } else {
        value = {...existing, anchor_seq:page.anchor.source_seq,
          max_seq:last ?? existing.max_seq, has_newer:page.has_newer};
      }
      state.coverage.set(turnId, value);
    }

    function trimCache(state, direction) {
      const overflow = state.order.length - cacheLimit;
      if (overflow <= 0) return;
      const protectedIds = new Set(state.visibleIds), source = direction === "prepend"
        ? [...state.order].reverse() : [...state.order];
      const remove = source.filter(id => !protectedIds.has(id)).slice(0, overflow);
      const removedRows = remove.map(id => state.nodes.get(id)).filter(Boolean);
      remove.forEach(id => { state.nodes.delete(id); state.domNodes.delete(id); });
      state.order = state.order.filter(id => state.nodes.has(id));
      for (const removed of removedRows) {
        const coverage = state.coverage.get(removed.turn_id);
        if (!coverage) continue;
        const remaining = state.order.map(id => state.nodes.get(id))
          .filter(row => row.turn_id === removed.turn_id);
        if (!remaining.length) continue;
        const first = remaining[0].source_seq, last = remaining.at(-1).source_seq;
        if (removed.source_seq < first) coverage.has_older = true;
        if (removed.source_seq > last) coverage.has_newer = true;
        coverage.min_seq = first; coverage.max_seq = last;
      }
    }

    async function request(direction) {
      const state = current();
      if (direction === "older" && hasCached(state, "older")) {
        render(state, {direction:"shift-older", wasNearBottom:false}); return true;
      }
      if (direction === "newer" && hasCached(state, "newer")) {
        render(state, {direction:"shift-newer", wasNearBottom:false}); return true;
      }
      const payload = direction === "latest" ? (() => {
        const turn = state?.outline.at(-1); if (!turn) return null;
        return {turn_id:turn.turn_id, source_seq:turn.start_seq, stable_node_id:null, before_seq:turn.end_seq + 1, limit:PAGE_LIMIT};
      })() : boundaryRequest(direction, state);
      if (!state || !payload || state.loading || typeof options.onPage !== "function") return false;
      state.loading = direction; updateControls(state);
      let accepted = false;
      try {
        accepted = await options.onPage(
          Object.freeze({...payload}),
          Object.freeze({session_id:state.sessionId, direction}),
        ) !== false;
        return accepted;
      } catch {
        return false;
      } finally {
        if (!accepted && direction === "latest") state.initialized = false;
        if (state.loading === direction) { state.loading = null; updateControls(state); }
      }
    }

    older.addEventListener?.("click", () => request("older"));
    newer.addEventListener?.("click", () => request("newer"));
    unread.addEventListener?.("click", () => {
      const state = current(); if (!state) return; state.unread = 0; render(state, {direction:"append", wasNearBottom:true});
    });

    return Object.freeze({
      activate(sessionId) {
        const id = cleanId(sessionId); activeId = id; list.replaceChildren();
        if (!id) { updateControls(null); return false; }
        const state = stateFor(id); state.visibleIds = [];
        render(state, {direction:"replace", wasNearBottom:true}); return true;
      },
      setOutline(value, sessionId = activeId, update = {}) {
        const id = cleanId(sessionId), rows = id ? checkedOutline(value, id) : null;
        if (!id || !rows) return reject("conversation_outline_invalid");
        const state = stateFor(id);
        if (update.merge === true) {
          const byId = new Map([...state.outline, ...rows].map(row => [row.turn_id, row]));
          const merged = checkedOutline({session_id:id, items:[...byId.values()].sort((left, right) => left.start_seq - right.start_seq)}, id);
          if (!merged) return reject("conversation_outline_invalid");
          state.outline = merged;
        } else state.outline = rows;
        if (id === activeId) updateControls(state);
        return Object.freeze({ok:true, projection:rows});
      },
      async ensureLatest() {
        const state = current();
        if (!state || state.initialized || !state.outline.length) return false;
        state.initialized = true; return request("latest");
      },
      updatePage(value, expected = {}, update = {}) {
        const id = cleanId(expected.session_id || activeId);
        if (!id) return reject("conversation_session_invalid");
        const parsed = parsePage(value, {...expected, session_id:id});
        if (!parsed?.ok) return parsed || reject("conversation_page_invalid");
        const page = parsed.projection, state = stateFor(id);
        state.totalNodes = Number.isSafeInteger(page.total_nodes) && page.total_nodes >= 0
          ? page.total_nodes : state.totalNodes;
        const direction = update.direction || (state.order.length ? "append" : "replace");
        const wasNearBottom = id === activeId ? nearBottom() : false;
        const incoming = [];
        for (const raw of page.nodes) {
          const stableId = cleanId(raw.stable_node_id || raw.node_id);
          if (!stableId) return reject("conversation_node_identity_invalid");
          const row = raw.stable_node_id === stableId ? raw : Object.freeze({...raw, stable_node_id:stableId});
          const existing = state.nodes.get(stableId);
          if (existing && !sameNode(existing, row)) {
            const reasoningHydration = String(existing.operation_id || existing.stable_node_id || "").startsWith("reasoning:")
              && !existing.text && typeof row.text === "string" && row.text.length > 0
              && sameNode({...existing, text:row.text}, row);
            if (!reasoningHydration) return reject("conversation_node_conflict");
            state.domNodes.delete(stableId);
          }
          incoming.push(row);
        }
        for (const row of incoming) state.nodes.set(row.stable_node_id, row);
        state.order = [...state.nodes.values()].sort((a,b) => a.source_seq - b.source_seq)
          .map(row => row.stable_node_id);
        updateCoverage(state, page, direction, incoming);
        trimCache(state, direction);
        if (direction === "replace") state.partialReasons = [...new Set(page.partial_reasons || [])].slice(0, 32);
        else state.partialReasons = [...new Set([...state.partialReasons.filter(reason => reason !== "timeline_not_loaded"), ...(page.partial_reasons || [])])].slice(0, 32);
        state.completeness = page.completeness === "partial" || state.partialReasons.length ? "partial" : "complete";
        state.loading = null; state.initialized = true;
        if (direction === "append" && !wasNearBottom && id === activeId) {
          state.unread += page.nodes.filter(row => !state.visibleIds.includes(row.stable_node_id)).length;
        }
        if (id === activeId) render(state, {direction, wasNearBottom});
        return Object.freeze({ok:true, reason:null, projection:page, cached:state.order.length, action_index:actionIndex(state)});
      },
      requestOlder:() => request("older"),
      requestNewer:() => request("newer"),
      requestLatest:() => request("latest"),
      render:() => render(current(), {direction:"replace", wasNearBottom:nearBottom()}),
      hasTimeline(sessionId = activeId) { const state = cleanId(sessionId) ? sessions.get(sessionId) : null; return Boolean(state?.order.length); },
      actionIndex(sessionId = activeId) { return actionIndex(cleanId(sessionId) ? sessions.get(sessionId) : null); },
      snapshot(sessionId = activeId) {
        const state = cleanId(sessionId) ? sessions.get(sessionId) : null;
        return state ? Object.freeze({session_id:state.sessionId, cached:state.order.length, materialized:state.visibleIds.length, completeness:state.completeness, partial_reasons:Object.freeze([...state.partialReasons]), loading:state.loading, unread:state.unread}) : null;
      },
      clear(sessionId = activeId) {
        const id = cleanId(sessionId); if (id) sessions.delete(id);
        if (id && id === activeId) { list.replaceChildren(); activeId = null; updateControls(null); }
      },
      host, list, status, older, newer, unread,
    });
  }

  global.AgentConversationView = Object.freeze({
    DEFAULT_DOM_LIMIT, DEFAULT_CACHE_LIMIT, PAGE_LIMIT, NEAR_BOTTOM_PX,
    create,
  });
})(window);
