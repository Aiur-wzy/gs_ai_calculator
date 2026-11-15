(function (global) {
  "use strict";

  // Host-owned, read-only workbench orchestration.  This controller may read
  // bounded projections and present them, but it never starts a provider
  // request, submits an answer, mutates a document, or executes an App action.
  const MAX_ID = 256;
  const MAX_OUTLINE_PAGE = 500;
  const MAX_HISTORY_PAGE = 500;
  const MAX_TELEMETRY_PAGE = 500;
  const SEARCH_PAGE = 50;
  const SEARCH_WINDOW = 400;
  const MAX_QUERY = 256;
  const ID = /^[^\u0000-\u001f\u007f]{1,256}$/u;

  const requiredFunction = (options, name) => {
    const value = options[name];
    if (typeof value !== "function") throw new TypeError(`workbench_read_${name}_missing`);
    return value;
  };
  const optionalFunction = (options, name, fallback = () => undefined) =>
    typeof options[name] === "function" ? options[name] : fallback;
  const object = value => value && typeof value === "object" && !Array.isArray(value) ? value : null;
  const integer = value => Number.isSafeInteger(value) && value >= 0 ? value : null;
  const stableId = value => typeof value === "string" && value.length <= MAX_ID && ID.test(value) ? value : null;

  function boundedPage(value, maximum) {
    if (value == null) return null;
    const row = object(value); if (!row) return false;
    const result = {};
    const before = Object.hasOwn(row, "before_seq") ? integer(row.before_seq) : null;
    const after = Object.hasOwn(row, "after_seq") ? integer(row.after_seq) : null;
    if (Object.hasOwn(row, "before_seq") && before === null
        || Object.hasOwn(row, "after_seq") && after === null
        || before !== null && after !== null) return false;
    if (before !== null) result.before_seq = before;
    if (after !== null) result.after_seq = after;
    if (Object.hasOwn(row, "limit")) {
      const limit = integer(row.limit);
      if (limit === null || limit < 1 || limit > maximum) return false;
      result.limit = limit;
    }
    return result;
  }

  function boundedLocator(value, maximum = MAX_HISTORY_PAGE) {
    const row = object(value); if (!row) return null;
    const turnId = stableId(row.turn_id), sourceSeq = integer(row.source_seq);
    const nodeId = row.stable_node_id == null ? null : stableId(row.stable_node_id);
    if (!turnId || sourceSeq === null || row.stable_node_id != null && !nodeId) return null;
    const page = boundedPage(row, maximum); if (page === false) return null;
    const reasoning = row.include_raw_reasoning === true;
    return {turn_id:turnId, source_seq:sourceSeq, stable_node_id:nodeId, ...(page || {}), ...(reasoning ? {include_raw_reasoning:true} : {})};
  }

  function activeTarget(projection) {
    if (!projection) return null;
    const terminal = new Set(["suspended", "completed", "cancelled", "failed", "interrupted"]);
    const segments = Array.isArray(projection.segments) ? projection.segments : [];
    const segment = [...segments].reverse().find(row => row && typeof row === "object"
      && !terminal.has(row.state) && stableId(row.segment_id) && stableId(row.turn_id));
    if (!segment || !stableId(segment.epoch_id) || !stableId(segment.runtime_incarnation)
        || !Number.isSafeInteger(segment.stage_epoch) || segment.stage_epoch < 0) return null;
    return Object.freeze({
      turn_id:segment.turn_id,
      segment_id:segment.segment_id,
      epoch_id:segment.epoch_id,
      stage_epoch:segment.stage_epoch,
      runtime_incarnation:segment.runtime_incarnation,
    });
  }

  function create(options = {}) {
    const api = requiredFunction(options, "api");
    const state = requiredFunction(options, "state");
    const selected = requiredFunction(options, "selected");
    const readModel = requiredFunction(options, "readModel");
    const errorText = requiredFunction(options, "errorText");
    const notice = requiredFunction(options, "notice");
    const capabilities = optionalFunction(options, "capabilities", () => state().workbenchCapabilities || {});
    const nodes = optionalFunction(options, "nodes", () => ({}));
    const controller = optionalFunction(options, "sessionNavigation", () => null);
    const pendingApplicationSession = optionalFunction(options, "pendingApplicationSession", () => null);
    const currentDocumentId = optionalFunction(options, "currentDocumentId", () => null);
    const isActive = optionalFunction(options, "isActive", () => false);
    const hasPendingApplication = optionalFunction(options, "hasPendingApplication", () => false);
    const hasUnsettledAppAction = optionalFunction(options, "hasUnsettledAppAction", () => false);
    const turnOutline = optionalFunction(options, "turnOutline", () => null);
    const conversationView = optionalFunction(options, "conversationView", () => null);
    const historyWindowView = optionalFunction(options, "historyWindowView", () => null);
    const telemetryView = optionalFunction(options, "telemetryView", () => null);
    const questionController = optionalFunction(options, "questionController", () => null);
    const inboxDock = optionalFunction(options, "inboxDock", () => null);
    const workbenchState = optionalFunction(options, "workbenchState", () => ({}));
    const renderWorkbench = optionalFunction(options, "renderWorkbench");
    const renderMessages = optionalFunction(options, "renderMessages");
    const clone = optionalFunction(options, "clone", value => value);
    const element = optionalFunction(options, "element", () => null);
    const button = optionalFunction(options, "button", () => null);
    const digest = requiredFunction(options, "queryDigest");
    const parseSearch = requiredFunction(options, "parseSearch");
    const parseFoundation = requiredFunction(options, "parseFoundation");
    const setTimer = optionalFunction(options, "setTimer", (callback, delay) => global.setTimeout(callback, delay));
    const clearTimer = optionalFunction(options, "clearTimer", timer => global.clearTimeout(timer));
    const nextFrame = optionalFunction(options, "nextFrame", callback => {
      if (typeof global.requestAnimationFrame === "function") global.requestAnimationFrame(callback);
      else callback();
    });
    const generations = new WeakMap();

    const identity = session => {
      const id = stableId(session?.session_id);
      if (!id) return null;
      const sessions = state()?.sessions;
      if (sessions instanceof Map && sessions.get(id) !== session) return null;
      return id;
    };
    const generationBucket = session => {
      let bucket = generations.get(session);
      if (!bucket) { bucket = new Map(); generations.set(session, bucket); }
      return bucket;
    };
    const invalidate = (session, channel) => {
      if (!session || typeof session !== "object") return;
      const bucket = generationBucket(session);
      bucket.set(channel, (bucket.get(channel) || 0) + 1);
    };
    const begin = (session, channel) => {
      const sessionId = identity(session), current = state();
      if (!sessionId || selected() !== session || !current) return null;
      const bucket = generationBucket(session), generation = (bucket.get(channel) || 0) + 1;
      bucket.set(channel, generation);
      return Object.freeze({
        session, sessionId, channel, generation,
        sessionEpoch:session.epoch,
        viewEpoch:current.viewEpoch,
        connectionGeneration:current.connectionGeneration,
      });
    };
    const live = stamp => {
      if (!stamp) return false;
      const current = state(), bucket = generations.get(stamp.session);
      return identity(stamp.session) === stamp.sessionId
        && selected() === stamp.session
        && stamp.session.epoch === stamp.sessionEpoch
        && current?.viewEpoch === stamp.viewEpoch
        && current?.connectionGeneration === stamp.connectionGeneration
        && bucket?.get(stamp.channel) === stamp.generation;
    };
    const request = async (stamp, name, ...args) => {
      if (!live(stamp)) return null;
      try { return await api(name, ...args); }
      catch (error) { return {code:1, message:String(error?.message || error || "App 读取失败")}; }
    };

    const clearHostOutline = message => {
      const outline = turnOutline(); if (!outline) return false;
      outline.list?.replaceChildren?.();
      if (outline.notice) outline.notice.textContent = message || "App 尚未提供可核验的任务步骤。";
      return true;
    };

    const renderSessionNavigation = () => {
      const target = controller(); if (!target) return false;
      const current = state(), pending = pendingApplicationSession(), documentId = currentDocumentId();
      const rows = (current.history || []).slice(0, 500).map(row => {
        const id = stableId(row?.session_id || row?.id), session = id ? current.sessions?.get?.(id) : null;
        const model = readModel(session);
        return {...row, session_id:id, title:row.title || model?.title || "未命名任务",
          active:row.active === true || isActive(session),
          pending_application:row.pending_application === true || hasPendingApplication(session),
          pending_action:hasUnsettledAppAction(session),
          can_resume:row.can_resume === true || model?.canResume === true,
          model:row.model || model?.model || "", effort:row.effort || model?.effort || ""};
      }).filter(row => row.session_id);
      const signature = JSON.stringify([
        rows.map(row => [row.session_id,row.title,row.state,row.updated_at,row.active,row.pending_application,row.pending_action,row.can_resume,row.model,row.effort,row.unread,row.archived,row.metadata_revision,row.can_archive,row.can_forget,row.recent_user_input,row.document_binding?.document_id,row.document_binding?.document_revision,row.document_binding?.document_lineage_epoch]),
        current.selected,current.active,pending?.session_id,documentId,capabilities().session_lifecycle,
      ]);
      const n = nodes(); if (n.sessionNavigationSignature === signature) return false;
      n.sessionNavigationSignature = signature;
      target.update(rows, {viewed_session_id:current.selected,execution_session_id:current.active,
        transaction_owner_id:pending?.session_id || null,current_document_id:documentId || null,
        session_lifecycle_enabled:capabilities().session_lifecycle});
      return true;
    };

    const saveReadingAnchor = session => {
      const store = options.readingAnchors?.(), n = nodes(), box = n.messages;
      const content = conversationView()?.list || box;
      if (!store || !session || !box) return false;
      const atBottom = box.scrollHeight - box.scrollTop - box.clientHeight < 70;
      if (atBottom) return store.set(session.session_id, {stable_node_id:null,offset_px:0,at_bottom:true});
      const children = Array.from(content?.children || []);
      const anchor = children.find(node => !node.hidden
        && Number(node.offsetTop || 0) + Number(node.offsetHeight || 0) >= Number(box.scrollTop || 0));
      const stableNodeId = stableId(anchor?.dataset?.stableNodeId);
      if (!stableNodeId) return false;
      return store.set(session.session_id, {stable_node_id:stableNodeId,
        offset_px:Number(box.scrollTop || 0) - Number(anchor.offsetTop || 0),at_bottom:false});
    };

    const restoreReadingAnchor = session => {
      const store = options.readingAnchors?.(), anchor = store?.get?.(session?.session_id), n = nodes(), box = n.messages;
      const content = conversationView()?.list || box;
      if (!anchor || !box) return false;
      const sessionId = session?.session_id, sessionEpoch = session?.epoch, viewEpoch = state().viewEpoch;
      nextFrame(() => {
        if (selected() !== session || identity(session) !== sessionId || session.epoch !== sessionEpoch
            || state().viewEpoch !== viewEpoch) return;
        if (anchor.at_bottom) { box.scrollTop = box.scrollHeight; return; }
        const node = Array.from(content?.children || []).find(row => row.dataset?.stableNodeId === anchor.stable_node_id);
        if (node) box.scrollTop = Number(node.offsetTop || 0) + Number(anchor.offset_px || 0);
      });
      return true;
    };

    const loadHostOutline = async (session, pageRequest = null) => {
      if (!session || selected() !== session) return false;
      if (!capabilities().turn_outline) {
        clearHostOutline("当前 App 尚未开放任务步骤；这里只保留已加载事件的本地筛选，不会猜造较早历史。");
        return false;
      }
      const page = boundedPage(pageRequest, MAX_OUTLINE_PAGE);
      if (pageRequest != null && page === false) return false;
      const stamp = begin(session, "outline"); if (!stamp) return false;
      session.hostOutlineRequest = stamp.generation;
      clearHostOutline("正在读取 App 保存的任务步骤…");
      const response = page ? await request(stamp, "agent_turn_outline", stamp.sessionId, page)
        : await request(stamp, "agent_turn_outline", stamp.sessionId);
      if (!response || !live(stamp)) return false;
      if (response.code) { clearHostOutline(errorText(response, "无法读取 App 保存的任务步骤。")); return false; }
      const outline = turnOutline(), parsed = outline?.update?.(response.data, stamp.sessionId);
      if (!parsed?.ok) { clearHostOutline(errorText({reason:parsed?.reason}, "App 返回的任务步骤无法核验。")); return false; }
      const previousCursor = session.conversationTimelineCursor || 0;
      session.conversationTimelineCursor = parsed.projection.source_cursor;
      const view = conversationView();
      view?.setOutline?.(parsed.projection, stamp.sessionId, {merge:Boolean(page)});
      if (!page && capabilities().history_window) {
        if (previousCursor > 0 && parsed.projection.source_cursor > previousCursor) void view?.requestLatest?.();
        else if (!view?.hasTimeline?.(stamp.sessionId)) void view?.ensureLatest?.();
      }
      return true;
    };

    const loadConversationPage = async (session, locator, context = {}) => {
      if (!session || selected() !== session || !capabilities().history_window) return false;
      const requestLocator = boundedLocator(locator);
      if (!requestLocator || context.session_id && context.session_id !== session.session_id) return false;
      const direction = ["older","newer","latest"].includes(context.direction) ? context.direction : "replace";
      const stamp = begin(session, `conversation:${direction}`); if (!stamp) return false;
      const response = await request(stamp, "agent_load_turn_window", stamp.sessionId, requestLocator);
      if (!response || !live(stamp)) return false;
      if (response.code) { notice(errorText(response, "无法读取 App 保存的会话记录。"), true); return false; }
      const view = conversationView();
      const mergeDirection = direction === "older" ? "prepend"
        : direction === "newer" || direction === "latest"
          ? (view?.hasTimeline?.(stamp.sessionId) ? "append" : "replace") : "replace";
      const parsed = view?.updatePage?.(response.data?.window, requestLocator, {direction:mergeDirection});
      if (!parsed?.ok) { notice(errorText({reason:parsed?.reason}, "App 返回的会话记录无法核验。"), true); return false; }
      renderMessages(session); return true;
    };

    const loadReasoning = async row => {
      const session = selected();
      if (!session || !row || row.kind !== "message" || !String(row.operation_id || "").startsWith("reasoning:")
          || typeof row.text === "string" && row.text.length) return false;
      const locator = boundedLocator({
        turn_id:row.turn_id,
        source_seq:row.source_seq,
        stable_node_id:row.stable_node_id,
        limit:MAX_HISTORY_PAGE,
        include_raw_reasoning:true,
      });
      if (!locator) return false;
      const stamp = begin(session, "reasoning"); if (!stamp) return false;
      const response = await request(stamp, "agent_load_turn_window", stamp.sessionId, locator);
      if (!response || !live(stamp)) return false;
      if (response.code) { notice(errorText(response, "无法读取这段推理过程。"), true); return false; }
      const parsed = conversationView()?.updatePage?.(response.data?.window, locator, {direction:"append"});
      if (!parsed?.ok) { notice(errorText({reason:parsed?.reason}, "推理过程无法核验。"), true); return false; }
      renderMessages(session); return true;
    };

    const renderHostSearchResults = projection => {
      const host = nodes().hostSearchResults; if (!host) return false;
      host.replaceChildren();
      if (!projection?.results?.length) { host.hidden = true; return true; }
      host.hidden = false;
      const summary = element("p", `已从 App 历史中加载 ${projection.results.length} 项${projection.has_more ? "（还有结果）" : ""}${projection.completeness === "partial" ? "（结果可能不完整）" : ""}`, "ac-note");
      if (summary) host.append(summary);
      for (const row of projection.results.slice(-SEARCH_WINDOW)) {
        const action = button(row.excerpt || `${row.kind} · #${row.source_seq}`, () => locateHostTurn(row), "ac-host-search-result");
        if (!action) continue; action.dataset ||= {}; action.dataset.stableNodeId = row.stable_node_id; host.append(action);
      }
      if (projection.has_more && projection.next_cursor !== null) {
        const more = button("加载更多搜索结果", () => {
          const session = selected(); if (session) void loadHostSearchPage(session, {cursor:projection.next_cursor,append:true});
        }, "ac-host-search-more");
        if (more) host.append(more);
      }
      return true;
    };

    const loadHostSearchPage = async (session, options = {}) => {
      const search = session?.hostSearchState;
      if (!session || selected() !== session || !search) return false;
      const cursor = options.cursor == null ? null : String(options.cursor);
      if (options.cursor != null && (!cursor || cursor.length > 4096)) return false;
      const stamp = begin(session, "search-page"); if (!stamp) return false;
      search.loading = true; search.page_generation = stamp.generation;
      const payload = {query:String(search.query || "").slice(0, MAX_QUERY),query_digest:search.digest,limit:SEARCH_PAGE};
      if (cursor !== null) payload.cursor = cursor;
      const response = await request(stamp, "agent_search_conversation", stamp.sessionId, payload);
      if (search.page_generation === stamp.generation) search.loading = false;
      if (!response || !live(stamp) || session.hostSearchState !== search
          || session.hostSearchRequest !== search.request) return false;
      if (response.code) {
        if (options.append !== true) { session.hostSearchProjection = null; renderHostSearchResults(null); }
        return false;
      }
      const parsed = parseSearch(response.data, {session_id:stamp.sessionId,query_digest:search.digest});
      if (!parsed.ok) {
        if (options.append !== true) { session.hostSearchProjection = null; renderHostSearchResults(null); }
        return false;
      }
      const page = parsed.projection, previous = options.append === true ? session.hostSearchProjection : null;
      const seen = new Set(), merged = [];
      for (const row of [...(previous?.results || []), ...page.results]) {
        if (!stableId(row.stable_node_id) || seen.has(row.stable_node_id)) continue;
        seen.add(row.stable_node_id); merged.push(row);
      }
      session.hostSearchProjection = {...page, results:merged.slice(-SEARCH_WINDOW)};
      renderHostSearchResults(session.hostSearchProjection); return true;
    };

    const scheduleHostSearch = (session, query) => {
      if (session?.hostSearchTimer) clearTimer(session.hostSearchTimer);
      const value = String(query || "").trim().slice(0, MAX_QUERY);
      if (!session || selected() !== session || !capabilities().conversation_search || !value) {
        if (session) {
          invalidate(session, "search-digest"); invalidate(session, "search-page");
          session.hostSearchTimer = null; session.hostSearchState = null; session.hostSearchProjection = null;
        }
        renderHostSearchResults(null); return false;
      }
      const stamp = begin(session, "search-digest"); if (!stamp) return false;
      const searchRequest = (session.hostSearchRequest || 0) + 1;
      session.hostSearchRequest = searchRequest;
      session.hostSearchTimer = setTimer(async () => {
        session.hostSearchTimer = null;
        const queryDigest = await digest(value);
        if (!queryDigest || !live(stamp) || session.hostSearchRequest !== searchRequest) return;
        session.hostSearchProjection = null;
        session.hostSearchState = {query:value,digest:queryDigest,request:searchRequest,loading:false};
        await loadHostSearchPage(session);
      }, 250);
      return true;
    };

    const locateHostTurn = async locator => {
      const session = selected(); if (!session) return false;
      const requestLocator = boundedLocator(locator); if (!requestLocator) return false;
      const paging = Object.hasOwn(requestLocator, "before_seq") || Object.hasOwn(requestLocator, "after_seq");
      const model = readModel(session);
      const loaded = [...(model?.messages || []), ...(model?.tools || [])]
        .some(row => row.turn_id === requestLocator.turn_id || row.run_id === requestLocator.turn_id);
      if (loaded && !paging) {
        workbenchState(session).turn_id = requestLocator.turn_id;
        turnOutline()?.select?.(requestLocator.turn_id);
        renderWorkbench(session); renderMessages(session); return true;
      }
      if (!capabilities().history_window) {
        notice("该轮次正文尚未加载，当前 App 也没有开放历史窗口读取。", true); return false;
      }
      notice("正在从 App 读取该轮次附近的历史记录…");
      const stamp = begin(session, "locate"); if (!stamp) return false;
      const response = await request(stamp, "agent_load_turn_window", stamp.sessionId, requestLocator);
      if (!response || !live(stamp)) return false;
      if (response.code) { notice(errorText(response, "无法读取该Turn附近的历史窗口。"), true); return false; }
      const projection = response.data?.window;
      if (!projection || projection.contract !== "agent-history-window-v1" || projection.session_id !== stamp.sessionId) {
        notice("App 返回的历史记录无法核验，未加入对话视图。", true); return false;
      }
      const parsed = historyWindowView()?.update?.(projection, requestLocator);
      if (!parsed?.ok) { notice(errorText({reason:parsed?.reason}, "App 返回的历史记录无法核验，未加入对话视图。"), true); return false; }
      session.historyWindow = clone(parsed.projection);
      const host = nodes().historyWindowHost; if (host) host.hidden = false;
      notice(parsed.projection.completeness === "partial"
        ? "已显示 App 可以确认的部分历史；缺失内容不会被猜造。" : "已显示 App 确认的历史记录。");
      return true;
    };

    const telemetryFallback = message => {
      const n = nodes(), view = telemetryView(), existing = view?.snapshot?.()?.trajectory;
      if (!existing) { view?.clear?.(); if (n.telemetryHost) n.telemetryHost.hidden = true; if (n.trajectoryList) n.trajectoryList.hidden = false; }
      if (n.telemetryStatus) { n.telemetryStatus.textContent = message; n.telemetryStatus.hidden = false; }
    };

    const loadTelemetry = async (session, pageRequest = null) => {
      if (!session || selected() !== session || !capabilities().telemetry_bundle || !telemetryView()) return false;
      const page = boundedPage(pageRequest, MAX_TELEMETRY_PAGE);
      if (pageRequest != null && page === false) return false;
      const stamp = begin(session, "telemetry"); if (!stamp) return false;
      const n = nodes();
      if (n.telemetryStatus) { n.telemetryStatus.textContent = "正在读取 App 确认的运行详情与指标…"; n.telemetryStatus.hidden = false; }
      const response = page ? await request(stamp, "agent_telemetry_bundle", stamp.sessionId, page)
        : await request(stamp, "agent_telemetry_bundle", stamp.sessionId);
      if (!response || !live(stamp)) return false;
      if (response.code) { telemetryFallback("完整轨迹暂不可用；当前显示兼容事件轨迹。"); return false; }
      const parsed = telemetryView().updateBundle(response.data, stamp.sessionId);
      if (!parsed.ok) { telemetryFallback(errorText({reason:parsed.reason}, "App 返回的运行详情无法核验；已拒绝跨会话或不完整数据。")); return false; }
      if (n.telemetryHost) n.telemetryHost.hidden = false;
      if (n.trajectoryList) n.trajectoryList.hidden = true;
      if (n.telemetryStatus) {
        const projection = parsed.projection;
        const partial = [...new Set([...(projection.trajectory.partial_reasons || []),
          ...(projection.context.partial_reasons || []), ...(projection.performance.partial_reasons || [])])];
        n.telemetryStatus.textContent = partial.length
          ? `已显示 App 可以确认的运行详情与指标；部分数据未知：${partial.join("、")}`
          : "已显示 App 确认的完整运行详情与指标。";
        n.telemetryStatus.hidden = false;
      }
      return true;
    };

    const applyTelemetryFilters = () => telemetryView()?.setFilters?.({
      category:nodes().telemetryFilter?.value || "all",
      turn_id:nodes().telemetryTurnFilter?.value?.trim() || null,
      workflow_stage:nodes().telemetryStageFilter?.value?.trim() || "all",
      source:nodes().telemetrySourceFilter?.value || "all",
      state:nodes().telemetryStateFilter?.value || "all",
      duration:nodes().telemetryDurationFilter?.value || "all",
      query:nodes().telemetryQuery?.value || "",
    }) ?? false;

    const workbenchFoundation = session => session?.workbenchFoundation || null;

    const presentWorkbenchFoundation = (session, raw) => {
      const sessionId = identity(session) || stableId(session?.session_id);
      if (!sessionId) return {ok:false, reason:"foundation_session_invalid", projection:null};
      const parsed = parseFoundation(raw, sessionId);
      if (!parsed.ok) { session.workbenchFoundationError = parsed.reason; return parsed; }
      session.workbenchFoundation = parsed.projection; session.workbenchFoundationError = null;
      if (selected() === session) {
        questionController()?.update?.(raw, sessionId);
        inboxDock()?.update?.(raw);
        const section = nodes().inboxSection;
        if (section) section.hidden = !parsed.projection.inbox.some(row => !["consumed", "cancelled"].includes(row.state));
      }
      return parsed;
    };

    const loadWorkbenchFoundation = async (session, options = {}) => {
      if (!session || selected() !== session || !capabilities().workbench_foundation) return false;
      const now = Date.now();
      if (session.workbenchFoundationLoading) return false;
      if (options.force !== true && session.workbenchFoundationLoadedAt
          && now - session.workbenchFoundationLoadedAt < 400) return true;
      const stamp = begin(session, "foundation"); if (!stamp) return false;
      session.workbenchFoundationRequest = stamp.generation; session.workbenchFoundationLoading = true;
      const response = await request(stamp, "agent_workbench_projection", stamp.sessionId);
      if (session.workbenchFoundationRequest === stamp.generation) session.workbenchFoundationLoading = false;
      if (!response || !live(stamp)) return false;
      session.workbenchFoundationLoadedAt = Date.now();
      if (response.code) {
        session.workbenchFoundationError = errorText(response, "无法读取结构化问题与消息队列。");
        notice(session.workbenchFoundationError, true); return false;
      }
      const parsed = presentWorkbenchFoundation(session, response.data);
      if (!parsed.ok) { notice(errorText({reason:parsed.reason}, "App 返回的工作台状态无法核验。"), true); return false; }
      if (capabilities().history_window
          && parsed.projection.source_cursor > (session.conversationTimelineCursor || 0)) void loadHostOutline(session);
      return true;
    };

    return Object.freeze({
      renderSessionNavigation, saveReadingAnchor, restoreReadingAnchor,
      clearHostOutline, loadHostOutline, loadConversationPage,
      loadReasoning,
      renderHostSearchResults, loadHostSearchPage, scheduleHostSearch, locateHostTurn,
      loadTelemetry, applyTelemetryFilters,
      workbenchFoundation, workbenchTarget:activeTarget,
      presentWorkbenchFoundation, loadWorkbenchFoundation,
      invalidateSession(session) {
        for (const channel of ["outline","conversation:older","conversation:newer","conversation:latest","conversation:replace","reasoning","search-digest","search-page","locate","telemetry","foundation"]) invalidate(session, channel);
      },
    });
  }

  global.AgentWorkbenchReadController = Object.freeze({
    create, activeTarget, boundedPage, boundedLocator,
    limits:Object.freeze({outline:MAX_OUTLINE_PAGE,history:MAX_HISTORY_PAGE,telemetry:MAX_TELEMETRY_PAGE,search:SEARCH_PAGE,searchWindow:SEARCH_WINDOW}),
  });
})(window);
