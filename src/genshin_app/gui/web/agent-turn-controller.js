(function (global) {
  "use strict";

  // Owns turn/inbox command admission and cancellation fences.  Durable
  // session, document and runtime facts remain in the Host/store; every
  // dependency below is an explicit capability supplied by the App facade.
  const EDITABLE_INBOX = new Set([
    "queued", "held_action_required", "held_failure", "needs_rebind",
  ]);

  const required = (options, name) => {
    const value = options[name];
    if (typeof value !== "function") throw new TypeError(`turn_controller_${name}_missing`);
    return value;
  };

  function create(options = {}) {
    const api = required(options, "api");
    const state = required(options, "state");
    const nodes = required(options, "nodes");
    const notice = required(options, "notice");
    const refresh = required(options, "refresh");
    const selected = required(options, "selected");
    const active = required(options, "active");
    const isActive = required(options, "isActive");
    const loadFoundation = required(options, "loadFoundation");
    const foundation = required(options, "foundation");
    const schedule = required(options, "schedule");
    const mutationId = required(options, "mutationId");
    const errorText = required(options, "errorText");
    const inFlight = new Set();

    const canPrepareColdRuntime = runtimeState =>
      options.hasRuntimeCapability?.("runtime_prepare") === true
      && ["cold", "stopped"].includes(runtimeState)
      && (state().credential?.saved === true || state().hostUi?.credential_mode === "saved");

    async function prepareColdRuntime(purpose, fence = null) {
      const current = () => typeof fence !== "function" || fence() === true;
      const started = await api("agent_prepare_runtime", purpose);
      if (!current()) {
        const operationId = started?.data?.operation_id;
        if (typeof operationId === "string") {
          // The user cancelled before Host returned the operation identity.  A
          // late prepare response must never revive the send.  Cancellation is
          // best effort because the runtime may already have become ready.
          await api("agent_cancel_runtime_prepare", operationId);
        }
        return false;
      }
      if (started.code) {
        notice(errorText(started, "无法启动本地 AI 服务。没有发送模型请求。"), true);
        return false;
      }
      const operation = started.data;
      if (!operation || typeof operation.operation_id !== "string") {
        notice("本地 AI 服务的启动响应不完整。没有发送模型请求。", true);
        return false;
      }
      state().runtimePrepare = {
        operation_id:operation.operation_id,
        status:operation.status || "preparing",
        purpose,
      };
      refresh();
      while (state().runtimePrepare?.operation_id === operation.operation_id) {
        const waited = await api("agent_wait_runtime_prepare", operation.operation_id, 12000);
        if (!current() || state().runtimePrepare?.operation_id !== operation.operation_id) return false;
        if (waited.code) {
          notice(errorText(waited, "无法确认本地 AI 服务是否启动。系统不会继续发送模型请求。"), true);
          return false;
        }
        const result = waited.data || {};
        state().runtimePrepare = {...state().runtimePrepare, ...result};
        if (result.status === "ready") {
          const snapshot = await api("agent_runtime_status", 0, state().runtimeStatusGeneration);
          if (!current() || state().runtimePrepare?.operation_id !== operation.operation_id) return false;
          if (!snapshot.code) options.applyRuntimeStatus?.(snapshot.data);
          return true;
        }
        if (result.status === "failed") {
          notice(errorText({reason:result.failure_code}, "本地 AI 服务启动失败。没有发送模型请求。"), true);
          return false;
        }
        if (["cancelled", "cancelling"].includes(result.status)) {
          notice("已取消启动本地 AI 服务；没有发送模型请求。");
          return false;
        }
        if (result.timed_out !== true) {
          notice("本地 AI 服务的启动状态不完整。系统不会继续发送模型请求。", true);
          return false;
        }
        refresh();
      }
      return false;
    }

    async function inboxRequest(session, method, payload, fallback) {
      if (!session) return false;
      const response = await api(method, session.session_id, payload);
      if (response.code) {
        notice(errorText(response, fallback), true);
        await loadFoundation(session, {force:true});
        return false;
      }
      await loadFoundation(session, {force:true});
      schedule(session);
      return true;
    }

    async function enqueueActiveMessage() {
      if (options.checkpointFrozen?.() === true) {
        notice("文档恢复结果尚未核清，Agent 写入已冻结。", true);
        return false;
      }
      const session = selected(), executing = active();
      const message = String(nodes().input?.value || "").trim();
      if (!session || session !== executing || !isActive(session)) {
        notice("请先返回运行中的任务，再排队或引导消息。", true);
        return false;
      }
      if (!message) { notice("请输入要排队的后续要求。", true); return false; }
      if (state().workbenchCapabilities?.durable_inbox !== true) {
        notice("当前 App 尚未开放可靠的消息排队功能；不会丢失或改写输入。", true);
        return false;
      }
      const flightKey = `enqueue:${session.session_id}`;
      if (inFlight.has(flightKey)) {
        notice("上一条运行中消息仍在提交，请勿重复发送。", true);
        return false;
      }
      inFlight.add(flightKey);
      try {
        if (!await loadFoundation(session, {force:true})) return false;
        const projection = foundation(session), binding = projection?.document_binding;
        if (!binding) {
          notice("App 尚未确认当前文档，暂不能安全排队消息。", true);
          return false;
        }
        const openQuestion = projection.questions.find(row => row.state === "open");
        const requestedKind = openQuestion ? "queue" : nodes().inboxMode?.value || "queue";
        const target = requestedKind === "queue" ? null : options.workbenchTarget?.(projection);
        if (requestedKind !== "queue" && !target) {
          notice("当前执行段缺少可核验身份，不能执行引导或停止后发送；可以改用排队。", true);
          return false;
        }
        const request = {
          expected_revision:projection.projection_revision,
          mutation_id:mutationId("inbox-enqueue"),
          inbox_id:mutationId("inbox"),
          kind:requestedKind,
          message,
          document_binding:binding,
          target,
          client_request_id:mutationId("inbox-request"),
        };
        const accepted = await inboxRequest(
          session, "agent_enqueue_workbench_inbox", request,
          "消息未能加入 App 的待发送队列；输入仍保留。",
        );
        if (!accepted) return false;
        options.composer?.()?.clearIf?.(message);
        notice(requestedKind === "queue"
          ? "消息已加入队列，当前任务完成后再接纳。"
          : requestedKind === "steer"
            ? "引导消息已由 App 登记，正在等待当前处理步骤接收。"
            : "App 正在停止当前任务；确认停止后才会发送这条消息。");
        refresh();
        return true;
      } finally {
        inFlight.delete(flightKey);
      }
    }

    async function handleInboxAction({action, item, message = null} = {}) {
      if (options.checkpointFrozen?.() === true && action !== "delete") {
        notice("文档恢复结果尚未核清，消息晋升和修改已冻结。", true);
        return false;
      }
      const session = selected();
      if (!session || !item) return false;
      const flightKey = `inbox:${session.session_id}:${item.inbox_id}`;
      if (inFlight.has(flightKey)) return false;
      inFlight.add(flightKey);
      let current = null;
      try {
        await loadFoundation(session, {force:true});
        const projection = foundation(session);
        current = projection?.inbox.find(row => row.inbox_id === item.inbox_id);
        if (!current) { notice("队列项已经变化，请刷新后重试。", true); return false; }
        const common = {
          expected_revision:projection.projection_revision,
          mutation_id:mutationId(`inbox-${action}`),
          inbox_id:current.inbox_id,
          expected_item_revision:current.revision,
        };
        options.inboxDock?.()?.setBusy?.(current.inbox_id, true);
        if (action === "delete") return await inboxRequest(session, "agent_cancel_workbench_inbox", common, "无法取消这条队列消息。");
        if (action === "promote") {
          if (!projection.document_binding) { notice("当前文档绑定不可用，不能安全发送这条消息。", true); return false; }
          return await inboxRequest(session, "agent_promote_workbench_inbox", {...common, current_document_binding:projection.document_binding}, "无法安全接纳这条队列消息。");
        }
        if (action === "edit") {
          const changed = typeof message === "string" ? message.trim() : "";
          if (!changed || changed === current.message) return false;
          return await inboxRequest(session, "agent_revise_workbench_inbox", {...common, message:changed, document_binding:projection.document_binding, target:current.target}, "无法修改这条队列消息。");
        }
        if (action === "up" || action === "down") {
          const reorderable = projection.inbox.filter(row => EDITABLE_INBOX.has(row.state));
          const index = reorderable.findIndex(row => row.inbox_id === current.inbox_id);
          const next = index + (action === "up" ? -1 : 1);
          if (index < 0 || next < 0 || next >= reorderable.length) return false;
          [reorderable[index], reorderable[next]] = [reorderable[next], reorderable[index]];
          return await inboxRequest(session, "agent_reorder_workbench_inbox", {
            expected_revision:projection.projection_revision,
            mutation_id:common.mutation_id,
            inbox_ids:reorderable.map(row => row.inbox_id),
          }, "无法调整消息顺序。");
        }
        return false;
      } finally {
        if (current) options.inboxDock?.()?.setBusy?.(current.inbox_id, false);
        inFlight.delete(flightKey);
      }
    }

    async function send() {
      if (options.checkpointFrozen?.() === true) {
        notice("文档恢复结果尚未核清，Agent 写入已冻结；请先完成恢复核对。", true);
        return false;
      }
      if (active()) return enqueueActiveMessage();
      const localState = state();
      if (localState.busy) { notice("当前操作尚未结束，请稍后再发送。", true); return false; }
      const current = selected(), pendingOwner = options.pendingApplicationSession?.();
      if (pendingOwner) { notice("候选应用事务尚未完成。请先补记应用回执，或取消尚未应用的事务。", true); return false; }
      if (options.hasUnsettledAppAction?.(current)) { notice("请先完成、写回或解除等待中的App操作，再继续发送消息。", true); return false; }
      if (localState.connection !== "ready") { notice("请先连接并完成登录、模型与隔离核验。", true); return false; }
      if (options.cliConnected?.() !== true) { notice("当前查看的提供方尚未连接。请选择已连接项，或先连接当前选择。", true); return false; }
      if (current && options.sessionProviderMatches?.(current) !== true) { notice("当前连接不是创建该会话的Agent提供方。请切回原提供方后继续，或新建任务。", true); return false; }
      const runtimeState = options.workbenchProjection?.(current)?.axes?.runtime_state;
      const runtimeCold = canPrepareColdRuntime(runtimeState);
      if (runtimeState !== null && runtimeState !== undefined && runtimeState !== "ready" && !runtimeCold) {
        const credentialLost = ["cold", "stopped"].includes(runtimeState) && localState.hostUi?.credential_mode === "required";
        notice(credentialLost
          ? "本地 AI 服务已经停止，内存中的 API Key 已清除。请重新输入 Key 并验证 API 设置；系统不会自动恢复或重发模型请求。"
          : ["starting", "reconnecting", "stopping"].includes(runtimeState)
            ? "本地 Agent 运行时正在切换状态，请稍后再发送。"
            : "本地 Agent 运行时当前不可用。系统不会自动重发可能已接纳的模型请求。", true);
        return false;
      }
      const message = String(nodes().input?.value || "").trim();
      if (!message) { notice("请输入需要 Agent 完成的任务。", true); return false; }
      if (current && !current.live) { notice("此会话为历史记录。请新建任务，明确发送当前方案。", true); return false; }
      let base, continuation;
      if (!current) {
        try { base = options.capture?.(nodes().kind.value); }
        catch (error) { notice(error.message, true); return false; }
      }
      if (current) {
        try { continuation = options.capture?.(current.kind); }
        catch (error) { notice(error.message, true); return false; }
        if (current.base && options.same?.(continuation, current.base) !== true) {
          notice("当前输入已在聊天外变化。请基于当前输入新建任务，聊天内容会保留。", true);
          return false;
        }
      }
      const workflow = current?.workflow || (base.kind === "whiteboard" ? options.workflow?.() : "calculate");
      const model = localState.models.find(row => row.model === nodes().model.value);
      const selectedEffort = nodes().effort.value;
      const effort = options.noReasoningModel?.(model) ? null : selectedEffort;
      if (!current && options.validEffort?.(model, selectedEffort) !== true) {
        notice("请选择模型；模型支持推理档位时还需选择有效档位。", true);
        return false;
      }
      let request;
      if (runtimeCold) {
        localState.busy = "runtime-start";
        request = ++localState.request;
        refresh();
        notice(`${options.runtimePhaseText?.(localState.hostUi?.runtime_phase || "checking_runtime")}；完成前不会发送模型请求。`);
        const prepared = await prepareColdRuntime(current ? "send" : "create", () => request === localState.request);
        if (request !== localState.request) return false;
        localState.runtimePrepare = null;
        if (!prepared) { localState.busy = null; refresh(); return false; }
      }
      localState.busy = "send";
      request = request ?? ++localState.request;
      const clientId = (options.requestId || mutationId)("agent-request");
      if (current) current.epoch++;
      refresh();
      notice("本地 AI 服务已就绪，正在启动本轮任务…");
      const response = current
        ? await api("agent_send", current.session_id, message, clientId, ...(continuation ? [continuation.document, options.workspace?.(continuation), continuation.applicationPayload] : []))
        : await api("agent_start", {kind:base.kind, workflow, document:base.document, workspace:options.workspace?.(base), message, model:model.model, effort, client_request_id:clientId});
      if (request !== localState.request) return false;
      localState.busy = null;
      if (response.code) {
        notice(errorText(response, "任务未能启动。不会自动重试；可先查看会话记录，确认是否已开始。"), true);
        options.loadHistory?.(); refresh(); return false;
      }
      const id = response.data.session_id || current?.session_id;
      if (!id) { notice("任务响应缺少会话编号，请刷新历史确认，避免重复发送。", true); options.loadHistory?.(); refresh(); return false; }
      if (current && id !== current.session_id) {
        notice("任务响应会话身份不一致；系统不会重发命令，正在只读刷新原会话。", true);
        void options.poll?.(current, true); return false;
      }
      const session = options.getSession?.(id);
      if (!session) { notice("无法建立本地会话状态；系统不会重发命令。", true); options.loadHistory?.(); return false; }
      options.activateSession?.({session, current, base, workflow, model, effort, message});
      const received = options.receiveCommand?.(session, response.data, 0);
      if (!received?.accepted) {
        options.transport?.()?.rejectExternal?.(session, {kind:"protocol", reason:received?.reason});
        notice(`${options.receiveProtocolText?.(received?.reason)}\n任务命令可能已经被接纳；系统将只读刷新状态，避免重复发送。`, true);
        void options.poll?.(session, true); options.loadHistory?.(); return false;
      }
      session.live = true; session.error = null;
      // The model may become idle before the coordinator has sealed the
      // candidate and emitted the terminal Host projection.  This local
      // settlement fence keeps read-side polling alive through that gap.  It
      // grants no write authority and is cleared only when the transport sees
      // an explicit terminal state or a suspended user question.
      session.awaitingTurnSettlement = true;
      if (base) {
        session.base = base; session.kind = base.kind; session.workflow = workflow;
        session.title = base.title; session.model = model.model; session.effort = effort;
      }
      options.finishAcceptedSession?.({session, message});
      schedule(session); options.loadHistory?.();
      notice(options.isAuthoring?.(session)
        ? "Agent 正在分阶段完善白板。阶段草稿可审核，伤害结果只会在最终计算闭环后开放。"
        : "Agent 仅修改任务候选。完成后请审核差异，再决定是否应用。");
      return true;
    }

    async function stop(session = active()) {
      const localState = state();
      if (localState.busy === "runtime-start") {
        const operationId = localState.runtimePrepare?.operation_id;
        ++localState.request;
        localState.runtimePrepare = operationId
          ? {...localState.runtimePrepare, status:"cancelling", cancellation_requested:true}
          : null;
        localState.busy = null;
        refresh();
        if (!operationId) {
          notice("已取消本地 AI 服务启动后的发送；即使稍后启动完成，也不会发送模型请求。");
          return true;
        }
        const stopped = await api("agent_cancel_runtime_prepare", operationId), status = stopped.data?.status;
        localState.runtimePrepare = null;
        if (stopped.code) {
          notice(`${errorText(stopped, "无法确认本地 AI 服务是否已停止启动。")} 待发送消息已在本地取消，不会继续发送模型请求。`, true);
          refresh(); return false;
        }
        notice(status === "ready"
          ? "本地 AI 服务已在取消请求到达前启动完成，但待发送消息已取消；不会发送模型请求。"
          : "已取消本地 AI 服务启动后的发送；不会发送模型请求。");
        refresh(); return true;
      }
      if (!session || !isActive(session)) return true;
      if (localState.busy === "stop") return false;
      localState.busy = "stop"; session.epoch++; session.state = "stopping";
      options.transport?.()?.invalidate?.(session); refresh();
      const response = await api("agent_stop", session.session_id);
      localState.busy = null;
      if (response.code) {
        session.error = errorText(response, "停止请求未确认，请检查任务状态。");
        notice(session.error, true); schedule(session); refresh(); return false;
      }
      const received = options.receiveCommand?.(session, response.data || {}, 0);
      if (!received?.accepted) {
        options.transport?.()?.rejectExternal?.(session, {kind:"protocol", reason:received?.reason});
        notice(`${options.receiveProtocolText?.(received?.reason)}\n停止命令可能已经执行；系统只会刷新状态，不会重复停止命令。`, true);
        void options.poll?.(session, true); return false;
      }
      if (isActive(session)) {
        notice("正在等待 Agent 停止；关闭窗口前仍需确认进程已结束。");
        schedule(session); return false;
      }
      notice("Agent 已停止，已有消息和候选保留。"); refresh(); return true;
    }

    return Object.freeze({
      canPrepareColdRuntime,
      prepareColdRuntime,
      inboxRequest,
      enqueueActiveMessage,
      handleInboxAction,
      send,
      stop,
    });
  }

  global.AgentTurnController = Object.freeze({create});
})(window);
