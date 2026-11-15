(function (global) {
  "use strict";

  const TRAJECTORY = "agent-trajectory-v1";
  const CONTEXT = "agent-context-metrics-v1";
  const PERFORMANCE = "agent-performance-metrics-v1";
  const MAX_SPANS = 5000;
  const MAX_METRICS = 128;
  const MAX_TEXT = 1000;
  const DEFAULT_WINDOW = 200;
  const MAX_FILTER_TEXT = 256;
  const DURATION_FILTERS = new Set(["all", "lt1s", "1s-10s", "gte10s", "unknown"]);
  const CATEGORY_LABELS = Object.freeze({
    turn:"轮次", segment:"执行段", runtime:"运行时", provider:"模型",
    skill:"Skill", database:"数据库", web:"搜索", app_tool:"App 工具",
    engine:"计算引擎", candidate:"候选", app_action:"App 操作",
    checkpoint:"恢复点", document:"文档",
    user_wait:"等待用户", other:"其他",
  });
  const SOURCES = new Set(["host", "provider", "runtime", "app", "engine"]);
  const PRECISIONS = new Set(["exact", "bounded", "estimated", "unknown"]);
  const STATES = new Set([
    "preparing", "running", "waiting", "completed", "cancelled",
    "failed", "interrupted", "unknown",
  ]);
  const CATEGORIES = new Set([
    "runtime", "provider", "skill", "database", "web", "app_tool",
    "engine", "candidate", "app_action", "checkpoint", "document",
    "user_wait", "turn", "segment",
    "other",
  ]);
  const FORBIDDEN_KEYS = /(?:raw[_-]?reasoning|reasoning[_-]?(?:content|text)|secret|credential|api[_-]?key|(?:^|_)(?:path|file_path|local_path)$)/iu;
  const LOCAL_PATH = /^(?:[a-z]:[\\/]|[\\/]{1,2}|file:)/iu;

  const text = (value, limit = MAX_TEXT) =>
    typeof value === "string" ? value.slice(0, limit) : "";
  const integer = value => Number.isSafeInteger(value) && value >= 0 ? value : null;
  const freeze = value => {
    if (value && typeof value === "object" && !Object.isFrozen(value)) {
      for (const child of Object.values(value)) freeze(child);
      Object.freeze(value);
    }
    return value;
  };
  const reject = reason => freeze({ok:false, reason, projection:null});

  function hasForbiddenShape(value) {
    if (!value || typeof value !== "object") return false;
    const stack = [value];
    const seen = new Set();
    let scanned = 0;
    while (stack.length) {
      const row = stack.pop();
      if (seen.has(row) || ++scanned > 20_000) return true;
      seen.add(row);
      for (const [key, child] of Object.entries(row)) {
        if (FORBIDDEN_KEYS.test(key)) return true;
        if (child && typeof child === "object") stack.push(child);
      }
    }
    return false;
  }

  function parseEnvelope(value, contract, expectedSessionId) {
    if (!value || typeof value !== "object" || Array.isArray(value)
        || hasForbiddenShape(value) || value.contract !== contract) return null;
    const sessionId = text(value.session_id, 256);
    const revision = integer(value.projection_revision);
    const cursor = integer(value.source_cursor);
    if (!sessionId || expectedSessionId && sessionId !== expectedSessionId
        || revision === null || cursor === null) return null;
    if (!Array.isArray(value.partial_reasons) || value.partial_reasons.length > 32
        || value.partial_reasons.some(reason => !text(reason, 128))) return null;
    const completeness = value.completeness === "complete" ? "complete"
      : value.completeness === "partial" ? "partial" : null;
    if (!completeness || completeness === "complete" && value.partial_reasons.length
        || completeness === "partial" && !value.partial_reasons.length) return null;
    return {
      session_id:sessionId,
      projection_revision:revision,
      source_cursor:cursor,
      completeness,
      partial_reasons:[...value.partial_reasons],
    };
  }

  function parseTrajectory(value, expectedSessionId = null) {
    const base = parseEnvelope(value, TRAJECTORY, expectedSessionId);
    if (!base || !Array.isArray(value.spans) || value.spans.length > MAX_SPANS) {
      return reject("trajectory_invalid");
    }
    const ids = new Set();
    const spans = [];
    for (const raw of value.spans) {
      const id = text(raw?.span_id, 256);
      const parent = raw?.parent_span_id == null ? null : text(raw.parent_span_id, 256);
      const turn = text(raw?.turn_id, 256);
      const segment = raw?.segment_id == null ? null : text(raw.segment_id, 256);
      const category = text(raw?.category, 64);
      const state = text(raw?.state, 64);
      const source = text(raw?.timing_source, 32);
      const precision = text(raw?.timing_precision, 32);
      const seqStart = integer(raw?.seq_start);
      const seqEnd = integer(raw?.seq_end);
      const elapsed = raw?.elapsed_ms == null ? null : integer(raw.elapsed_ms);
      const resourceRef = raw?.resource_ref == null ? null : text(raw.resource_ref, 256);
      const workflowStage = raw?.workflow_stage == null ? null : text(raw.workflow_stage, 128);
      if (!id || ids.has(id) || parent === id || !turn || !CATEGORIES.has(category)
          || !STATES.has(state) || !SOURCES.has(source) || !PRECISIONS.has(precision)
          || seqStart === null || seqEnd === null || seqEnd < seqStart
          || elapsed === null && precision !== "unknown"
          || raw?.workflow_stage != null && !workflowStage
          || raw?.resource_ref != null && (!resourceRef || LOCAL_PATH.test(resourceRef))) {
        return reject("trajectory_span_invalid");
      }
      ids.add(id);
      spans.push(freeze({
        span_id:id,
        parent_span_id:parent,
        turn_id:turn,
        segment_id:segment,
        workflow_stage:workflowStage,
        category,
        state,
        label:text(raw.label),
        summary:text(raw.summary),
        result_code:raw.result_code == null ? null : text(raw.result_code, 128),
        resource_ref:resourceRef,
        seq_start:seqStart,
        seq_end:seqEnd,
        elapsed_ms:elapsed,
        timing_source:source,
        timing_precision:precision,
      }));
    }
    const byId = new Map(spans.map(row => [row.span_id, row]));
    for (const row of spans) {
      if (!row.parent_span_id) continue;
      const parent = byId.get(row.parent_span_id);
      if (!parent || parent.seq_start > row.seq_start || parent.seq_end < row.seq_end) {
        return reject("trajectory_parent_invalid");
      }
      const visited = new Set([row.span_id]);
      let ancestor = parent;
      while (ancestor) {
        if (visited.has(ancestor.span_id)) return reject("trajectory_parent_invalid");
        visited.add(ancestor.span_id);
        ancestor = ancestor.parent_span_id ? byId.get(ancestor.parent_span_id) : null;
      }
    }
    const totalSpans=value.total_spans===undefined?spans.length:integer(value.total_spans);
    const olderCursor=value.older_cursor==null?null:integer(value.older_cursor);
    const newerCursor=value.newer_cursor==null?null:integer(value.newer_cursor);
    if(totalSpans===null||totalSpans<spans.length
        ||typeof value.has_older!=="undefined"&&typeof value.has_older!=="boolean"
        ||typeof value.has_newer!=="undefined"&&typeof value.has_newer!=="boolean"
        ||value.older_cursor!=null&&olderCursor===null
        ||value.newer_cursor!=null&&newerCursor===null
        ||value.has_older===true&&olderCursor===null
        ||value.has_newer===true&&newerCursor===null)return reject("trajectory_pagination_invalid");
    return freeze({ok:true, reason:null, projection:{contract:TRAJECTORY, ...base, spans,total_spans:totalSpans,has_older:value.has_older===true,has_newer:value.has_newer===true,older_cursor:olderCursor,newer_cursor:newerCursor}});
  }

  function parseMetrics(value, contract, expectedSessionId = null) {
    const base = parseEnvelope(value, contract, expectedSessionId);
    if (!base || !Array.isArray(value.metrics) || value.metrics.length > MAX_METRICS) {
      return reject("metrics_invalid");
    }
    const ids = new Set();
    const metrics = [];
    for (const raw of value.metrics) {
      const id = text(raw?.metric_id, 128);
      const source = text(raw?.source, 32);
      const precision = text(raw?.precision, 32);
      const numeric = raw?.value === null ? null : Number(raw?.value);
      if (!id || ids.has(id) || !SOURCES.has(source) || !PRECISIONS.has(precision)
          || numeric === null && precision !== "unknown"
          || numeric !== null && !Number.isFinite(numeric)) return reject("metric_invalid");
      ids.add(id);
      metrics.push(freeze({
        metric_id:id,
        label:text(raw.label, 256),
        category:text(raw.category, 64),
        value:numeric,
        unit:text(raw.unit, 32),
        source,
        precision,
        note:text(raw.note, 512),
      }));
    }
    return freeze({ok:true, reason:null, projection:{contract, ...base, metrics}});
  }

  function parseBundle(value, expectedSessionId = null) {
    if (!value || typeof value !== "object" || Array.isArray(value) || hasForbiddenShape(value)) {
      return reject("telemetry_bundle_invalid");
    }
    const trajectory = parseTrajectory(value.trajectory, expectedSessionId);
    const sessionId = trajectory.projection?.session_id || expectedSessionId;
    const context = parseMetrics(value.context, CONTEXT, sessionId);
    const performance = parseMetrics(value.performance, PERFORMANCE, sessionId);
    if (!trajectory.ok || !context.ok || !performance.ok) {
      return reject(trajectory.reason || context.reason || performance.reason);
    }
    const rows = [trajectory.projection, context.projection, performance.projection];
    if (rows.some(row => row.projection_revision !== rows[0].projection_revision
        || row.source_cursor !== rows[0].source_cursor)) {
      return reject("telemetry_bundle_revision_mismatch");
    }
    return freeze({ok:true, reason:null, projection:{
      session_id:sessionId,
      projection_revision:rows[0].projection_revision,
      source_cursor:rows[0].source_cursor,
      trajectory:trajectory.projection,
      context:context.projection,
      performance:performance.projection,
    }});
  }

  const element = (document, tag, value = null, className = "") => {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (value !== null) node.textContent = String(value);
    return node;
  };

  function create(document, host, options = {}) {
    if (!document || !host) throw new TypeError("telemetry_host_invalid");
    let trajectory = null;
    let context = null;
    let performance = null;
    let filters = Object.freeze({
      category:"all", turn_id:null, workflow_stage:"all", source:"all",
      state:"all", duration:"all", query:"",
    });
    let selectedSpanId = null;
    let focusedSpanId = null;
    let windowOffset = 0;
    let windowLimit = DEFAULT_WINDOW;

    const render = () => {
      host.replaceChildren();
      const reasons = new Set([
        ...(trajectory?.partial_reasons || []),
        ...(context?.partial_reasons || []),
        ...(performance?.partial_reasons || []),
      ]);
      if (reasons.size) {
        host.append(element(document, "p", `部分数据不可用：${[...reasons].join("、")}`, "ac-note"));
      }
      const summary = element(document, "div", null, "ac-telemetry-summary");
      for (const metric of [...(performance?.metrics || []), ...(context?.metrics || [])]) {
        const card = element(document, "article", null, "ac-metric-card");
        card.dataset.metricId = metric.metric_id;
        card.append(
          element(document, "strong", metric.label || metric.metric_id),
          element(document, "span", metric.value === null ? "未知" : `${metric.value}${metric.unit ? ` ${metric.unit}` : ""}`),
          element(document, "small", `${metric.source} · ${metric.precision}`),
        );
        if (metric.note) card.append(element(document, "p", metric.note, "ac-note"));
        summary.append(card);
      }
      host.append(summary);
      const query = filters.query.toLocaleLowerCase("zh-CN");
      const filtered = (trajectory?.spans || []).filter(row => {
        if (filters.category !== "all" && row.category !== filters.category) return false;
        if (filters.turn_id && row.turn_id !== filters.turn_id) return false;
        if (filters.workflow_stage !== "all" && row.workflow_stage !== filters.workflow_stage) return false;
        if (filters.source !== "all" && row.timing_source !== filters.source) return false;
        if (filters.state !== "all" && row.state !== filters.state) return false;
        if (filters.duration === "unknown" && row.elapsed_ms !== null) return false;
        if (filters.duration === "lt1s" && !(row.elapsed_ms !== null && row.elapsed_ms < 1000)) return false;
        if (filters.duration === "1s-10s" && !(row.elapsed_ms !== null && row.elapsed_ms >= 1000 && row.elapsed_ms < 10000)) return false;
        if (filters.duration === "gte10s" && !(row.elapsed_ms !== null && row.elapsed_ms >= 10000)) return false;
        if (query && !`${row.label}\n${row.summary}\n${row.result_code || ""}\n${row.resource_ref || ""}`.toLocaleLowerCase("zh-CN").includes(query)) return false;
        return true;
      });
      const start = Math.min(windowOffset, Math.max(0, filtered.length - windowLimit));
      const visible = filtered.slice(start, start + windowLimit);
      const visibleIds = new Set(visible.map(row => row.span_id));
      if (!visibleIds.has(focusedSpanId)) {
        focusedSpanId = visibleIds.has(selectedSpanId) ? selectedSpanId : visible[0]?.span_id || null;
      }
      const lanes = element(document, "div", null, "ac-trajectory-host");
      lanes.dataset.total = String(filtered.length);
      lanes.dataset.offset = String(start);
      lanes.setAttribute?.("role", "list");
      lanes.setAttribute?.("aria-label", "Agent 执行轨迹");
      const laneTracks = new Map();
      const visibleRows = [];
      const updateRowSelection = () => {
        for (const entry of visibleRows) {
          entry.node.tabIndex = entry.span.span_id === focusedSpanId ? 0 : -1;
          entry.node.dataset.selected = entry.span.span_id === selectedSpanId ? "true" : "false";
          entry.node.setAttribute?.("aria-pressed", String(entry.span.span_id === selectedSpanId));
        }
      };
      const focusAt = index => {
        if (!visibleRows.length) return;
        const bounded = Math.max(0, Math.min(visibleRows.length - 1, index));
        focusedSpanId = visibleRows[bounded].span.span_id;
        updateRowSelection();
        visibleRows[bounded].node.focus?.();
      };
      for (const span of visible) {
        let track = laneTracks.get(span.category);
        if (!track) {
          const lane = element(document, "section", null, "ac-trajectory-lane");
          lane.dataset.category = span.category;
          lane.setAttribute?.("aria-label", CATEGORY_LABELS[span.category] || span.category);
          lane.append(element(document, "h4", CATEGORY_LABELS[span.category] || span.category, "ac-trajectory-lane-title"));
          track = element(document, "div", null, "ac-trajectory-lane-track");
          lane.append(track); lanes.append(lane); laneTracks.set(span.category, track);
        }
        const row = element(document, "article", null, "ac-trajectory-span");
        row.dataset.spanId = span.span_id;
        row.dataset.category = span.category;
        row.setAttribute?.("role", "button");
        row.tabIndex = span.span_id === focusedSpanId ? 0 : -1;
        row.dataset.selected = selectedSpanId === span.span_id ? "true" : "false";
        row.setAttribute?.("aria-pressed", String(selectedSpanId === span.span_id));
        row.append(
          element(document, "strong", span.label || span.category),
          element(document, "span", span.elapsed_ms === null ? "耗时未知" : `${span.elapsed_ms} ms`),
          element(document, "p", span.summary),
          element(document, "small", `${span.state} · ${span.timing_source}/${span.timing_precision}${span.workflow_stage ? ` · ${span.workflow_stage}` : ""}`),
        );
        if (span.result_code) row.append(element(document, "code", `结果：${span.result_code}`, "ac-trajectory-result"));
        if (span.resource_ref) row.append(element(document, "small", `资源：${span.resource_ref}`, "ac-trajectory-resource"));
        const inspect = () => {
          selectedSpanId = span.span_id;
          focusedSpanId = span.span_id;
          updateRowSelection();
          options.onInspect?.(span);
        };
        row.addEventListener?.("click", inspect);
        row.addEventListener?.("keydown", event => {
          if (event?.key === "Enter" || event?.key === " ") {
            event.preventDefault?.();
            inspect();
            return;
          }
          const currentIndex = visibleRows.findIndex(entry => entry.span.span_id === span.span_id);
          if (event?.key === "ArrowDown" || event?.key === "ArrowRight") {
            event.preventDefault?.(); focusAt(currentIndex + 1);
          } else if (event?.key === "ArrowUp" || event?.key === "ArrowLeft") {
            event.preventDefault?.(); focusAt(currentIndex - 1);
          } else if (event?.key === "Home") {
            event.preventDefault?.(); focusAt(0);
          } else if (event?.key === "End") {
            event.preventDefault?.(); focusAt(visibleRows.length - 1);
          }
        });
        row.addEventListener?.("focus", () => {
          focusedSpanId = span.span_id;
          updateRowSelection();
        });
        visibleRows.push({span, node:row});
        track.append(row);
      }
      updateRowSelection();
      host.append(lanes);
      if (trajectory?.has_older || trajectory?.has_newer) {
        const pager = element(document, "nav", null, "ac-trajectory-pager");
        if (trajectory.has_older) {
          const older = element(document, "button", "加载更早轨迹");
          older.type = "button";
          older.addEventListener?.("click", () => options.onPage?.({before_seq:trajectory.older_cursor,limit:500}));
          pager.append(older);
        }
        if (trajectory.has_newer) {
          const newer = element(document, "button", "加载更新轨迹");
          newer.type = "button";
          newer.addEventListener?.("click", () => options.onPage?.({after_seq:trajectory.newer_cursor,limit:500}));
          pager.append(newer);
        }
        host.append(pager);
      }
    };

    const applyBundle = projection => {
      trajectory = projection.trajectory;
      context = projection.context;
      performance = projection.performance;
      if (selectedSpanId && !trajectory.spans.some(row => row.span_id === selectedSpanId)) {
        selectedSpanId = null;
      }
      if (focusedSpanId && !trajectory.spans.some(row => row.span_id === focusedSpanId)) {
        focusedSpanId = null;
      }
      render();
    };

    return Object.freeze({
      updateBundle(value, sessionId = null) {
        const parsed = parseBundle(value, sessionId);
        if (parsed.ok) applyBundle(parsed.projection);
        return parsed;
      },
      updateTrajectory(value, sessionId = null) {
        const parsed = parseTrajectory(value, sessionId);
        if (parsed.ok) { trajectory = parsed.projection; render(); }
        return parsed;
      },
      updateContext(value, sessionId = null) {
        const parsed = parseMetrics(value, CONTEXT, sessionId);
        if (parsed.ok) { context = parsed.projection; render(); }
        return parsed;
      },
      updatePerformance(value, sessionId = null) {
        const parsed = parseMetrics(value, PERFORMANCE, sessionId);
        if (parsed.ok) { performance = parsed.projection; render(); }
        return parsed;
      },
      setFilter(value) {
        filters = Object.freeze({...filters, category:value === "all" || CATEGORIES.has(value) ? value : "all"});
        windowOffset = 0;
        render();
      },
      setFilters(patch = {}) {
        if (!patch || typeof patch !== "object" || Array.isArray(patch)) return false;
        const next = {...filters};
        if (Object.hasOwn(patch, "category")) next.category = patch.category === "all" || CATEGORIES.has(patch.category) ? patch.category : "all";
        if (Object.hasOwn(patch, "turn_id")) next.turn_id = patch.turn_id == null || patch.turn_id === "" || patch.turn_id === "all" ? null : text(patch.turn_id, 256) || null;
        if (Object.hasOwn(patch, "workflow_stage")) next.workflow_stage = patch.workflow_stage == null || patch.workflow_stage === "" || patch.workflow_stage === "all" ? "all" : text(patch.workflow_stage, 128) || "all";
        if (Object.hasOwn(patch, "source")) next.source = patch.source === "all" || SOURCES.has(patch.source) ? patch.source : "all";
        if (Object.hasOwn(patch, "state")) next.state = patch.state === "all" || STATES.has(patch.state) ? patch.state : "all";
        if (Object.hasOwn(patch, "duration")) next.duration = DURATION_FILTERS.has(patch.duration) ? patch.duration : "all";
        if (Object.hasOwn(patch, "query")) next.query = text(patch.query, MAX_FILTER_TEXT).trim();
        filters = Object.freeze(next);
        windowOffset = 0;
        render();
        return true;
      },
      setWindow(offset = 0, limit = DEFAULT_WINDOW) {
        const nextOffset = integer(offset);
        const nextLimit = integer(limit);
        if (nextOffset === null || nextLimit === null || nextLimit < 1 || nextLimit > 500) return false;
        windowOffset = nextOffset;
        windowLimit = nextLimit;
        render();
        return true;
      },
      clear() {
        trajectory = null;
        context = null;
        performance = null;
        selectedSpanId = null;
        focusedSpanId = null;
        windowOffset = 0;
        host.replaceChildren();
      },
      snapshot: () => freeze({
        trajectory, context, performance, filter:filters.category, filters,
        selected_span_id:selectedSpanId,
        focused_span_id:focusedSpanId,
        window_offset:windowOffset,
        window_limit:windowLimit,
      }),
    });
  }

  global.AgentTelemetry = Object.freeze({
    TRAJECTORY,
    CONTEXT,
    PERFORMANCE,
    parseTrajectory,
    parseMetrics,
    parseBundle,
    create,
  });
})(window);
