"use strict";

// DOM-free transport owner for the Agent workbench.  It owns only read-side
// event delivery state: cursor, wait generation, fallback mode and backoff.
// Provider turns and domain transactions remain owned by the existing Host
// commands/controllers and are never retried here.
(function (global) {
  const DEFAULT_WAIT_MS = 12000;
  const UNSUPPORTED = new Set(["agent_wait_events_unsupported"]);
  const safeCursor = value => Number.isSafeInteger(value) && value >= 0;

  function create(options = {}) {
    if (typeof options.request !== "function" || typeof options.receive !== "function") throw new TypeError("workbench_client_callbacks_required");
    const setTimer = options.setTimer || global.setTimeout.bind(global);
    const clearTimer = options.clearTimer || global.clearTimeout.bind(global);
    const records = new Map();

    const initialCursor = session => {
      const injected = typeof options.initialCursor === "function" ? options.initialCursor(session) : session?.cursor;
      return safeCursor(injected) ? injected : 0;
    };

    const ensure = (sessionId, initialCursor = 0) => {
      let record = records.get(sessionId);
      if (!record) {
        record = {
          sessionId, cursor:safeCursor(initialCursor) ? initialCursor : 0,
          subscriptionGeneration:1, requestToken:0, inFlight:false,
          pendingForce:false, pendingContext:null, timer:null,
          fallback:false, errors:0, lastMode:null, lastTimedOut:false,
        };
        records.set(sessionId, record);
      }
      return record;
    };
    const snapshot = record => Object.freeze({
      cursor:record.cursor, subscription_generation:record.subscriptionGeneration,
      in_flight:record.inFlight, timer:record.timer, fallback:record.fallback,
      errors:record.errors, mode:record.lastMode,
    });
    const publish = (session, record) => options.onState?.(session, snapshot(record));
    const current = (record, token, generation, context) => (
      records.get(record.sessionId) === record
      && record.requestToken === token
      && record.subscriptionGeneration === generation
      && (typeof context?.guard !== "function" || context.guard())
    );
    const cancelTimer = (session, record) => {
      if (record.timer !== null) clearTimer(record.timer);
      record.timer = null;
      publish(session, record);
    };
    const nextDelay = (session, record, context) => {
      if (typeof options.delay === "function") return Math.max(0, Number(options.delay(session, snapshot(record), context)) || 0);
      if (record.errors) return Math.min(15000, 1500 * 2 ** Math.min(record.errors, 4));
      return record.lastMode === "wait" ? 25 : 650;
    };

    function schedule(session, context = {}) {
      const id = session?.session_id;
      if (typeof id !== "string" || !id) return false;
      const record = ensure(id, initialCursor(session));
      cancelTimer(session, record);
      if (typeof options.isActive === "function" && !options.isActive(session)) return false;
      const delay = nextDelay(session, record, context);
      record.timer = setTimer(() => {
        record.timer = null; publish(session, record);
        void poll(session, context, false);
      }, delay);
      publish(session, record);
      return true;
    }

    const failure = (session, record, detail) => {
      record.errors++;
      options.onFailure?.(session, Object.freeze({...detail, errors:record.errors}), snapshot(record));
      publish(session, record);
    };

    async function poll(session, context = {}, force = false) {
      const id = session?.session_id;
      if (typeof id !== "string" || !id) return Object.freeze({status:"invalid_session"});
      // A caller can become stale before it reaches the transport owner (for
      // example after a provider switch).  Fail closed before issuing a Host
      // read; the post-response fence below still protects requests whose
      // generation changes while they are in flight.
      if (typeof context?.guard === "function" && !context.guard()) {
        return Object.freeze({status:"stale"});
      }
      const record = ensure(id, initialCursor(session));
      cancelTimer(session, record);
      if (record.inFlight) {
        if (force) { record.pendingForce = true; record.pendingContext = context; }
        publish(session, record);
        return Object.freeze({status:"in_flight"});
      }
      record.inFlight = true; record.pendingForce = false; record.pendingContext = null;
      const token = ++record.requestToken, generation = record.subscriptionGeneration;
      // The cursor used to issue this request is part of the admission
      // contract.  It must not be reconstructed from a mutable session after
      // an overlapping command response has completed.
      const requestCursor = record.cursor;
      const preferWait = context.preferWait === true && record.fallback !== true && (typeof options.isActive !== "function" || options.isActive(session));
      let mode = preferWait ? "wait" : "poll", response;
      record.lastMode = mode; record.lastTimedOut = false; publish(session, record);
      try {
        const waitTimeoutMs = Number.isSafeInteger(context.waitTimeoutMs) && context.waitTimeoutMs >= 0
          ? context.waitTimeoutMs : DEFAULT_WAIT_MS;
        response = mode === "wait"
          ? await options.request("agent_wait_events", id, requestCursor, generation, waitTimeoutMs)
          : await options.request("agent_poll", id, requestCursor);
        if (!current(record, token, generation, context)) return Object.freeze({status:"stale"});
        if (mode === "wait" && response?.code && UNSUPPORTED.has(response.reason)) {
          record.fallback = true; mode = "poll"; record.lastMode = mode; publish(session, record);
          response = await options.request("agent_poll", id, requestCursor);
          if (!current(record, token, generation, context)) return Object.freeze({status:"stale"});
        }
        if (response?.code) {
          failure(session, record, {kind:"transport", response, mode});
          return Object.freeze({status:"error", mode});
        }
        const envelope = response?.data;
        if (mode === "wait" && !Number.isSafeInteger(envelope?.subscription_generation)) {
          failure(session, record, {kind:"protocol", reason:"subscription_generation_invalid", mode});
          return Object.freeze({status:"protocol_error", mode});
        }
        if (mode === "wait" && envelope.subscription_generation !== generation) {
          return Object.freeze({status:"stale_generation", mode});
        }
        const received = options.receive(session, envelope, {
          mode, subscriptionGeneration:generation, requestedAfterSeq:requestCursor,
        });
        if (!received?.accepted) {
          if (received?.protocol) failure(session, record, {kind:"protocol", reason:received.reason, mode});
          return Object.freeze({status:received?.protocol ? "protocol_error" : "stale", mode});
        }
        // ACK advances only after the complete envelope has been accepted by
        // the single production reducer.  A rejected batch keeps this cursor.
        const next = received?.ack_cursor;
        if (!safeCursor(next)) {
          failure(session, record, {kind:"protocol", reason:"receipt_ack_cursor_invalid", mode});
          return Object.freeze({status:"protocol_error", mode});
        }
        record.cursor = next;
        record.errors = 0; record.lastTimedOut = envelope?.wait_timed_out === true;
        options.onAccepted?.(session, envelope, snapshot(record));
        publish(session, record);
        return Object.freeze({status:record.lastTimedOut ? "timeout" : "accepted", mode, cursor:record.cursor});
      } catch (error) {
        if (!current(record, token, generation, context)) return Object.freeze({status:"stale"});
        failure(session, record, {kind:"exception", error, mode});
        return Object.freeze({status:"error", mode});
      } finally {
        if (records.get(id) === record && record.requestToken === token && record.subscriptionGeneration === generation) {
          record.inFlight = false; publish(session, record);
          if (record.pendingForce) {
            const pending = record.pendingContext || context; record.pendingForce = false; record.pendingContext = null;
            // A queued force request inherits its own provider/view generation
            // fence.  If that authority changed while the first wait was in
            // flight, drop the queued read instead of issuing a doomed Host
            // request and rejecting it only after network completion.
            if (typeof pending.guard !== "function" || pending.guard()) void poll(session, pending, true);
          } else if (typeof context.guard !== "function" || context.guard()) schedule(session, context);
        }
      }
    }

    function commitExternal(session, receipt) {
      const id = session?.session_id;
      const cursor = receipt?.accepted === true ? receipt.ack_cursor : null;
      if (typeof id !== "string" || !id || !safeCursor(cursor)) return false;
      const record = ensure(id, cursor);
      cancelTimer(session, record);
      // Any accepted command/poll outside this client supersedes an in-flight
      // wait, even when its cursor happens to be unchanged.
      record.cursor = cursor; record.subscriptionGeneration++; record.requestToken++;
      record.inFlight = false; record.pendingForce = false; record.pendingContext = null;
      record.errors = 0; publish(session, record);
      return true;
    }

    function rejectExternal(session, detail = {}) {
      const id = session?.session_id;
      if (typeof id !== "string" || !id) return false;
      const record = ensure(id, initialCursor(session));
      failure(session, record, detail);
      return true;
    }

    function invalidate(sessionOrId, {drop = false} = {}) {
      const id = typeof sessionOrId === "string" ? sessionOrId : sessionOrId?.session_id;
      if (typeof id !== "string" || !id) return false;
      const record = records.get(id); if (!record) return false;
      cancelTimer(sessionOrId && typeof sessionOrId === "object" ? sessionOrId : null, record);
      record.subscriptionGeneration++; record.requestToken++; record.inFlight = false;
      record.pendingForce = false; record.pendingContext = null;
      if (drop) records.delete(id); else publish(sessionOrId && typeof sessionOrId === "object" ? sessionOrId : null, record);
      return true;
    }
    function invalidateAll({drop = false} = {}) {
      for (const id of [...records.keys()]) invalidate(id, {drop});
    }
    const inspect = sessionId => records.has(sessionId) ? snapshot(records.get(sessionId)) : null;
    return Object.freeze({poll, schedule, commitExternal, rejectExternal, invalidate, invalidateAll, inspect});
  }

  global.AgentWorkbenchClient = Object.freeze({create});
})(window);
