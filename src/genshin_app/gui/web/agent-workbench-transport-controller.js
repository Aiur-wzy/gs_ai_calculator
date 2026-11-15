(function (global) {
  "use strict";

  const requireFunction = (options, name) => {
    if (typeof options[name] !== "function") throw new TypeError(`workbench_transport_${name}_missing`);
    return options[name];
  };

  function create(options = {}) {
    const client = options.client;
    if (!client || typeof client.create !== "function"
      || !options.eventStore || typeof options.eventStore.stage !== "function"
      || typeof options.eventStore.commit !== "function") {
      throw new TypeError("workbench_transport_dependencies_missing");
    }
    const api = requireFunction(options, "api");
    const state = requireFunction(options, "state");
    const ackCursor = requireFunction(options, "ackCursor");
    const readModel = requireFunction(options, "readModel");
    const isActive = requireFunction(options, "isActive");
    const refresh = requireFunction(options, "refresh");
    const currentMatches = requireFunction(options, "currentMatches");
    const selected = requireFunction(options, "selected");
    const errorText = requireFunction(options, "errorText");
    const connectionPayloadMatches = requireFunction(options, "connectionPayloadMatches");
    const mergeHostUi = requireFunction(options, "mergeHostUi");
    const setTimer = options.setTimer || global.setTimeout.bind(global);
    const clearTimer = options.clearTimer || global.clearTimeout.bind(global);
    const eventStore = options.eventStore;
    const sidecars = options.sidecars;
    if (!sidecars || typeof sidecars.recordAppAction !== "function"
      || typeof sidecars.prepareDomainSidecar !== "function"
      || typeof sidecars.commitDomainSidecar !== "function") {
      throw new TypeError("workbench_transport_sidecar_adapter_missing");
    }
    let transport = null;
    const reconciliations = new Map();

    const protocolText = reason => errorText(
      {reason:`native_agent_protocol_${reason || "invalid"}`},
      "App 返回的任务状态无法核验；已保留当前消息并降低同步频率。",
    );

    function recordAppAction(session, value) {
      return sidecars.recordAppAction(session, value);
    }

    function prepareEnvelope(session, data, context = {}) {
      if (context.production === true && options.testMode?.() !== true) return data;
      const requested = Number.isSafeInteger(context.requestedAfterSeq)
        ? context.requestedAfterSeq : ackCursor(session);
      const commandReset = context.production === true && requested === 0 && ackCursor(session) > 0;
      return {
        ...(data || {}),
        session_id:data?.session_id || session.session_id,
        requested_after_seq:Object.hasOwn(data || {}, "requested_after_seq")
          ? data.requested_after_seq : requested,
        reset_required:Object.hasOwn(data || {}, "reset_required")
          ? data.reset_required : commandReset,
      };
    }

    function prepareDomainSidecar(session, data, context = {}) {
      return sidecars.prepareDomainSidecar(session, data, context, {currentMatches, errorText});
    }

    function commitDomainSidecar(session, draft) {
      sidecars.commitDomainSidecar(session, draft);
    }

    function receive(session, data, context = {}) {
      const envelope = prepareEnvelope(session, data, context);
      const requested = Number.isSafeInteger(context.requestedAfterSeq)
        ? context.requestedAfterSeq : envelope?.requested_after_seq;
      const domain = prepareDomainSidecar(session, envelope || {}, context);
      if (!domain.ok) return Object.freeze({accepted:false, reason:domain.reason, protocol:true});
      let transaction;
      try {
        transaction = eventStore.stage({
          expected_session_id:session.session_id,
          requested_after_seq:requested,
          envelope,
        });
      } catch {
        return Object.freeze({accepted:false, reason:"event_store_stage_exception", protocol:true});
      }
      if (!transaction.ok) return Object.freeze({
        accepted:false,
        reason:transaction.rejection_code || "event_store_stage_failed",
        protocol:true,
        diagnostics:transaction.diagnostics || [],
      });
      let receipt;
      try { receipt = eventStore.commit(transaction); }
      catch { return Object.freeze({accepted:false, reason:"event_store_commit_exception", protocol:true}); }
      if (!receipt.ok) return Object.freeze({
        accepted:false,
        reason:receipt.rejection_code || "event_store_commit_failed",
        protocol:true,
        diagnostics:receipt.diagnostics || [],
      });
      // Domain projection is published only after the one-shot Store CAS.
      commitDomainSidecar(session, domain.draft);
      const accepted = Object.freeze({
        accepted:true, reason:null, protocol:false,
        ack_cursor:receipt.ack_cursor,
        store_revision:receipt.store_revision,
        snapshot_revision:receipt.snapshot_revision,
        snapshot:receipt.snapshot,
      });
      options.markTransportSuccess?.(session.session_id, Date.now());
      try {
        const localState = state();
        const updatesCurrentConnection = session.live === true && connectionPayloadMatches(envelope);
        if (updatesCurrentConnection) {
          localState.hostUi = mergeHostUi(localState.hostUi, envelope, options.connectionHostUiFields || []);
        }
        if (updatesCurrentConnection && envelope.connection_state) localState.connection = envelope.connection_state;
        const model = readModel(session);
        if (model?.active) localState.active = session.session_id;
        else if (localState.active === session.session_id) localState.active = null;
      } catch {
        session.error = "事件已安全接纳，但界面投影刷新失败；请重新打开面板。";
      }
      const feedbackEvents = Array.isArray(envelope?.events) ? envelope.events : [];
      const questionTransition = feedbackEvents.some(event => event?.type === "workbench.question_opened");
      const terminalEvent = [...feedbackEvents].reverse().find(event => [
        "turn.completed", "turn.cancelled", "turn.error", "turn.interrupted",
      ].includes(event?.type));
      const terminalStates = new Set([
        "completed", "failed", "stopped", "cancelled", "interrupted", "error",
      ]);
      const suspendedStates = new Set([
        "awaiting_user_answer", "awaiting_input_live", "awaiting_input_suspended",
      ]);
      const projectedState = typeof envelope?.terminal_status === "string" && envelope.terminal_status
        ? envelope.terminal_status
        : typeof envelope?.state === "string" ? envelope.state : null;
      const explicitSettlement = terminalEvent || questionTransition
        || terminalStates.has(projectedState) || suspendedStates.has(projectedState);
      if (explicitSettlement) session.awaitingTurnSettlement = false;
      else if (envelope?.active === true || readModel(session)?.active === true) session.awaitingTurnSettlement = true;
      if (questionTransition || terminalEvent) {
        const stateByType = {
          "turn.completed":"completed", "turn.cancelled":"cancelled",
          "turn.error":"failed", "turn.interrupted":"interrupted",
        };
        options.observeSessionFeedback?.(session, {
          transition:true,
          state:terminalEvent ? stateByType[terminalEvent.type] : null,
          questionOpen:questionTransition ? true : null,
        });
      }
      if (context.transport !== true) transport.commitExternal(session, accepted);
      try { refresh(); }
      catch { session.error = "事件已安全接纳，但界面渲染失败；请重新打开面板。"; }
      if (selected() === session && options.hasWorkbenchFoundation?.() === true) {
        void options.loadWorkbenchFoundation?.(session);
      }
      const durableTypes = new Set([
        "turn.completed", "turn.cancelled", "turn.error", "turn.interrupted",
        "app_action.event", "workbench.question_opened",
      ]);
      const durableActivity = feedbackEvents.some(event => durableTypes.has(event?.type));
      if (durableActivity && selected() === session) void options.syncSelectedRead?.(session.session_id);
      else if (durableActivity) void options.loadHistory?.();
      return accepted;
    }

    const receiveCommand = (session, data, requestedAfterSeq = 0, context = {}) => receive(
      session, data, {...context, production:true, requestedAfterSeq},
    );
    const transportContext = (session, {preferWait = true} = {}) => {
      const epoch = session.epoch, connectionGeneration = state().connectionGeneration;
      return {
        preferWait:preferWait && readModel(session)?.connectionKind === "native_api",
        waitTimeoutMs:12000,
        guard:() => epoch === session.epoch && connectionGeneration === state().connectionGeneration,
      };
    };

    transport = client.create({
      request:(method, ...args) => api(method, ...args),
      receive:(session, envelope, context) => receive(session, envelope, {
        transport:true, production:true, requestedAfterSeq:context.requestedAfterSeq,
      }),
      initialCursor:session => ackCursor(session),
      isActive:session => isActive(session),
      setTimer,
      clearTimer,
      delay:(session, snapshot) => snapshot.errors
        ? Math.min(15000, 1500 * 2 ** Math.min(snapshot.errors, 4))
        : snapshot.mode === "wait" ? 25 : state().open ? 650 : 1500,
      onState:(session, snapshot) => {
        if (!session) return;
        session.pollErrors = snapshot.errors;
        session.polling = snapshot.in_flight;
        session.timer = snapshot.timer;
        session.transportMode = snapshot.mode;
        session.transportFallback = snapshot.fallback;
        session.subscriptionGeneration = snapshot.subscription_generation;
      },
      onFailure:(session, detail) => {
        session.pollFailureActive = true;
        session.error = detail.kind === "protocol"
          ? protocolText(detail.reason)
          : detail.response
            ? errorText(detail.response, "暂时无法读取任务状态，输入和候选仍保留。")
            : "读取Agent状态时发生本地传输错误；输入和候选仍保留。";
        refresh();
      },
    });

    const schedule = session => transport.schedule(session, transportContext(session));
    const poll = (session, force = false) => transport.poll(session, transportContext(session), force);
    // Reconciliation is the only read-after-unknown-outcome entry point for
    // domain transactions.  It supersedes any long wait, uses an immediate
    // poll, and never retries the write whose acknowledgement was lost.
    const reconcile = session => {
      const id = session?.session_id, epoch = session?.epoch, connectionGeneration = state().connectionGeneration;
      if (typeof id !== "string" || !id) return Promise.resolve(Object.freeze({status:"invalid_session"}));
      const current = reconciliations.get(id);
      if (current && current.epoch === epoch && current.connectionGeneration === connectionGeneration) return current.promise;
      const context = transportContext(session, {preferWait:false});
      if (!context.guard()) return Promise.resolve(Object.freeze({status:"stale"}));
      transport.invalidate(session);
      if (!context.guard()) return Promise.resolve(Object.freeze({status:"stale"}));
      const promise = Promise.resolve(transport.poll(session, context, true)).finally(() => {
        if (reconciliations.get(id)?.promise === promise) reconciliations.delete(id);
      });
      reconciliations.set(id, {epoch, connectionGeneration, promise});
      return promise;
    };

    return Object.freeze({
      transport,
      protocolText,
      recordAppAction,
      receive,
      receiveCommand,
      schedule,
      poll,
      reconcile,
    });
  }

  global.AgentWorkbenchTransportController = Object.freeze({create});
})(window);
