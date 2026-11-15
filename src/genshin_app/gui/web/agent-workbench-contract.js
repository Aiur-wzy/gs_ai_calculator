"use strict";

// Versioned, read-only adapters for Renderer event inputs.  LegacyAgentPollV1
// is intentionally partial: it preserves source sequence numbers but never
// invents modern event, turn, segment, or projection identities.
(function (global) {
  const CONTRACT = "legacy_agent_poll_v1";
  const MAX = Object.freeze({events:1000, messages:500, tools:300, budgets:128, actions:100, partialReasons:64, visibleText:6 * 1024 * 1024});
  const SESSION_ID = /^[A-Za-z0-9._:-]{1,128}$/;
  const FORBIDDEN = new Set(["api_key", "authorization", "cookie", "credential", "secret", "raw_reasoning"]);
  const UNPROJECTED = new Set(["workspace", "origin_workspace", "current_binding", "path", "absolute_path", "cwd", "calculated", "diff", "document", "payload", "request"]);
  const EVENT_TYPES = new Set(["message", "delta", "tool", "usage", "budget_status", "candidate", "app_action", "status", "error"]);
  const BASELINE_COMPLETENESS = new Set(["complete", "partial", "unavailable"]);
  const BASELINE_FAMILIES = ["messages", "tools", "usage", "budget_statuses", "app_action_refs"];
  const isObject = value => Object.prototype.toString.call(value) === "[object Object]";
  const safeInt = value => Number.isSafeInteger(value) && value >= 0;
  const own = (value, key) => isObject(value) && Object.hasOwn(value, key);
  const clipped = (value, limit) => typeof value === "string" && value.length <= limit ? value : null;
  const optionalString = (value, limit, diagnostics, name) => {
    if (value === undefined || value === null) return null;
    const result = clipped(value, limit);
    if (result === null) diagnostics.push(`optional_field_dropped:${name}`);
    return result;
  };
  const terminalTurnState = value => ({
    completed:"completed", cancelled:"cancelled", stopped:"cancelled",
    error:"failed", failed:"failed", interrupted:"interrupted",
  })[value] || null;
  const deepFreeze = value => {
    if (!value || typeof value !== "object" || Object.isFrozen(value)) return value;
    for (const child of Object.values(value)) deepFreeze(child);
    return Object.freeze(value);
  };
  const rejected = (code, diagnostics = []) => deepFreeze({
    ok:false, contract_version:CONTRACT, partial:true, rejection_code:code,
    diagnostics:[...diagnostics],
  });

  function validateApplicationIntent(value, expectedSession) {
    const status = value.status, intent = value.intent;
    if (value.conversation_id !== expectedSession) return "application_intent_conversation_invalid";
    const recovery = value.recovery_required === true, deferred = value.deferred_while_active === true;
    if (status === "none") {
      if (intent !== null) return "application_intent_none_invalid";
      if (recovery) return "application_intent_recovery_state_invalid";
      return null;
    }
    if (deferred) return "application_intent_deferred_state_invalid";
    if (recovery) {
      if (!["prepared", "baseline_advanced"].includes(status) || intent !== null) return "application_intent_recovery_state_invalid";
      return null;
    }
    if (!isObject(intent)) return "application_intent_identity_missing";
    if (intent.state !== status) return "application_intent_state_mismatch";
    if (!["native", "whiteboard"].includes(intent.kind)) return "application_intent_kind_invalid";
    if (!safeInt(intent.revision) || intent.revision < 1) return "application_intent_revision_invalid";
    if (typeof intent.receipt_fingerprint !== "string" || !/^[A-Za-z0-9._:-]{1,256}$/.test(intent.receipt_fingerprint)) return "application_intent_receipt_invalid";
    return null;
  }

  function containsForbidden(value, depth = 0, budget = {nodes:0}) {
    // Complexity limits are a security boundary. Failing open would let a
    // protected key hide just beyond the inspected range and reach the full
    // domain sidecar even though the Store retains only a compact reference.
    if (depth > 8 || budget.nodes > 2048) return true;
    if (!value || typeof value !== "object") return false;
    budget.nodes++;
    if (budget.nodes > 2048) return true;
    if (Array.isArray(value)) return value.some(child => containsForbidden(child, depth + 1, budget));
    if (!isObject(value)) return false;
    for (const [key, child] of Object.entries(value)) {
      const normalized = key.toLocaleLowerCase("en-US");
      if (FORBIDDEN.has(normalized)) return true;
      if (UNPROJECTED.has(normalized)) continue;
      if (containsForbidden(child, depth + 1, budget)) return true;
    }
    return false;
  }

  function usage(value, diagnostics, name = "usage") {
    if (value === undefined || value === null) return null;
    if (!isObject(value)) throw new Error(`${name}_invalid`);
    const result = {};
    for (const key of ["input_tokens", "output_tokens", "total_tokens", "cached_input_tokens", "reasoning_tokens", "prompt_tokens", "completion_tokens"]) {
      if (!own(value, key)) continue;
      if (!safeInt(value[key]) || value[key] > 1_000_000_000_000) throw new Error(`${name}_${key}_invalid`);
      result[key] = value[key];
    }
    if (!Object.keys(result).length) diagnostics.push(`optional_field_dropped:${name}`);
    return result;
  }

  function candidateRef(value, diagnostics) {
    if (value === undefined || value === null) return null;
    if (!isObject(value)) throw new Error("candidate_invalid");
    if (containsForbidden(value)) throw new Error("candidate_protected_field");
    const revision = safeInt(value.revision) ? value.revision : null;
    const receipt = optionalString(value.receipt_fingerprint, 256, diagnostics, "candidate.receipt_fingerprint");
    const issuesCount = Array.isArray(value.issues) ? value.issues.length : safeInt(value.issues_count) ? value.issues_count : null;
    return {revision, ready:value.ready === true, stale:value.stale === true, receipt_fingerprint:receipt, issues_count:issuesCount};
  }

  function actionRef(value, diagnostics) {
    if (!isObject(value)) throw new Error("app_action_invalid");
    if (containsForbidden(value)) throw new Error("app_action_protected_field");
    return {
      action_id:optionalString(value.action_id, 256, diagnostics, "app_action.action_id"),
      action:optionalString(value.action, 128, diagnostics, "app_action.action"),
      status:optionalString(value.status, 128, diagnostics, "app_action.status"),
      execution_mode:optionalString(value.execution_mode, 64, diagnostics, "app_action.execution_mode"),
      candidate_revision:safeInt(value.candidate_revision) ? value.candidate_revision : null,
      stage_revision:safeInt(value.stage_revision) ? value.stage_revision : null,
      execution_outcome_unknown:value.execution_outcome_unknown === true,
    };
  }

  function identityFor(type, event, payload) {
    if (["message", "delta"].includes(type)) return optionalIdentity("message_id", event.message_id);
    if (type === "tool") return optionalIdentity("tool_id", event.tool_id || event.id);
    if (type === "app_action") return optionalIdentity("action_id", payload?.action_id);
    return {};
  }
  const optionalIdentity = (key, value) => typeof value === "string" && value.length > 0 && value.length <= 256 ? {[key]:value} : {};

  function normalizeEvent(event, diagnostics, textBudget) {
    if (!isObject(event) || containsForbidden(event)) throw new Error("event_invalid_or_protected");
    if (!Number.isSafeInteger(event.seq) || event.seq <= 0) throw new Error("event_seq_invalid");
    const type = clipped(event.type, 64);
    if (!type) throw new Error("event_type_invalid");
    const runId = optionalString(event.run_id, 256, diagnostics, `event.${event.seq}.run_id`);
    let payload = null;
    if (type === "message" || type === "delta") {
      const role = event.role === "user" || event.role === "assistant" ? event.role : null;
      if (event.role !== undefined && role === null) throw new Error("message_role_invalid");
      const key = type === "delta" ? "delta" : own(event, "message") ? "message" : "text";
      const text = clipped(event[key], 100000);
      if (text === null) throw new Error("message_text_invalid");
      textBudget.value += text.length;
      payload = {role, text};
    } else if (type === "tool") {
      const name = optionalString(event.name || event.tool_name, 256, diagnostics, `event.${event.seq}.tool_name`);
      const state = optionalString(event.state || event.status, 64, diagnostics, `event.${event.seq}.tool_state`);
      const message = optionalString(event.message, 1000, diagnostics, `event.${event.seq}.tool_message`);
      textBudget.value += (name?.length || 0) + (message?.length || 0);
      payload = {name, state, message};
    } else if (type === "usage") payload = usage(event.usage || event, diagnostics, `event.${event.seq}.usage`);
    else if (type === "budget_status") {
      payload = {
        status:optionalString(event.status, 128, diagnostics, `event.${event.seq}.budget_status`),
        level:optionalString(event.level, 64, diagnostics, `event.${event.seq}.budget_level`),
        percent:Number.isFinite(event.percent) && event.percent >= 0 && event.percent <= 100 ? event.percent : null,
        message:optionalString(event.message, 1000, diagnostics, `event.${event.seq}.budget_message`),
      };
      textBudget.value += payload.message?.length || 0;
    } else if (type === "candidate") payload = candidateRef(event.candidate, diagnostics);
    else if (type === "app_action") payload = actionRef(event.action || event, diagnostics);
    else if (type === "status" || type === "error") {
      payload = {
        state:optionalString(event.state, 64, diagnostics, `event.${event.seq}.state`),
        code:optionalString(event.reason || event.error_code || event.error, 128, diagnostics, `event.${event.seq}.code`),
        message:optionalString(event.message, 1000, diagnostics, `event.${event.seq}.message`),
      };
      textBudget.value += payload.message?.length || 0;
    } else diagnostics.push(`unknown_event_type:${type}`);
    const sourceIdentity = identityFor(type, event, payload);
    return {
      source_seq:event.seq, source_type:type, run_id:runId,
      identity_status:Object.keys(sourceIdentity).length ? "present" : "missing",
      source_identity:sourceIdentity,
      semantic_payload:EVENT_TYPES.has(type) ? payload : null,
    };
  }

  function baselineMessage(value, diagnostics, textBudget) {
    if (!isObject(value) || containsForbidden(value)) throw new Error("baseline_message_invalid_or_protected");
    const role = value.role === "user" || value.role === "assistant" ? value.role : null;
    if (value.role !== undefined && role === null) throw new Error("baseline_message_role_invalid");
    const text = clipped(own(value, "message") ? value.message : value.text, 100000);
    if (text === null) throw new Error("baseline_message_text_invalid");
    textBudget.value += text.length;
    return {
      message_id:optionalString(value.message_id || value.id, 256, diagnostics, "baseline.message_id"),
      role, text,
      run_id:optionalString(value.run_id, 256, diagnostics, "baseline.run_id"),
      last_source_seq:safeInt(value.last_source_seq) ? value.last_source_seq : safeInt(value.seq) ? value.seq : null,
    };
  }

  function baselineTool(value, diagnostics, textBudget) {
    if (!isObject(value) || containsForbidden(value)) throw new Error("baseline_tool_invalid_or_protected");
    const toolId = optionalString(value.tool_id || value.id, 256, diagnostics, "baseline.tool_id");
    if (!toolId) throw new Error("baseline_tool_identity_missing");
    const name = optionalString(value.name || value.tool_name, 256, diagnostics, "baseline.tool_name");
    const state = optionalString(value.state || value.status, 64, diagnostics, "baseline.tool_state");
    const message = optionalString(value.message, 1000, diagnostics, "baseline.tool_message");
    textBudget.value += (name?.length || 0) + (message?.length || 0);
    return {
      tool_id:toolId, name, state, message,
      run_id:optionalString(value.run_id, 256, diagnostics, "baseline.tool_run_id"),
      last_source_seq:safeInt(value.last_source_seq) ? value.last_source_seq : safeInt(value.seq) ? value.seq : null,
    };
  }

  function baselineBudget(value, diagnostics, textBudget) {
    if (!isObject(value) || containsForbidden(value)) throw new Error("baseline_budget_invalid_or_protected");
    const status = optionalString(value.status, 128, diagnostics, "baseline.budget_status");
    if (!status) throw new Error("baseline_budget_identity_missing");
    const message = optionalString(value.message, 1000, diagnostics, "baseline.budget_message");
    textBudget.value += message?.length || 0;
    return {
      status,
      level:optionalString(value.level, 64, diagnostics, "baseline.budget_level"),
      percent:Number.isFinite(value.percent) && value.percent >= 0 && value.percent <= 100 ? value.percent : null,
      message,
      run_id:optionalString(value.run_id, 256, diagnostics, "baseline.budget_run_id"),
      last_source_seq:safeInt(value.last_source_seq) ? value.last_source_seq : safeInt(value.seq) ? value.seq : null,
    };
  }

  function baselineCompleteness(value) {
    if (typeof value === "string" && BASELINE_COMPLETENESS.has(value)) return value;
    if (!isObject(value)) throw new Error("baseline_completeness_invalid");
    const result = {};
    for (const family of BASELINE_FAMILIES) {
      if (!own(value, family)) continue;
      if (!BASELINE_COMPLETENESS.has(value[family])) throw new Error("baseline_completeness_invalid");
      result[family] = value[family];
    }
    if (!Object.keys(result).length) throw new Error("baseline_completeness_invalid");
    return result;
  }

  function normalizeBaseline(value, envelope, diagnostics, textBudget) {
    if (value === undefined || value === null) {
      diagnostics.push("reset_baseline_legacy_partial");
      return {
        baseline_seq:safeInt(envelope.snapshot_seq) ? envelope.snapshot_seq : envelope.next_seq,
        completeness:"partial", partial_reasons:["legacy_reset_baseline"],
        messages:(envelope.messages || []).map(row => baselineMessage(row, diagnostics, textBudget)),
        tools:[], usage:usage(envelope.usage, diagnostics), budget_statuses:[],
        app_action_refs:(envelope.app_actions || []).map(row => actionRef(row, diagnostics)),
      };
    }
    if (!isObject(value) || containsForbidden(value)) throw new Error("baseline_invalid_or_protected");
    if (!safeInt(value.baseline_seq) || value.baseline_seq > envelope.next_seq) throw new Error("baseline_seq_invalid");
    if (safeInt(envelope.snapshot_seq) && value.baseline_seq !== envelope.snapshot_seq) throw new Error("baseline_snapshot_mismatch");
    for (const [key, limit] of [["messages", MAX.messages], ["tools", MAX.tools], ["budget_statuses", MAX.budgets], ["app_action_refs", MAX.actions]]) {
      if (!Array.isArray(value[key]) || value[key].length > limit) throw new Error(`baseline_${key}_invalid`);
    }
    if (!Array.isArray(value.partial_reasons) || value.partial_reasons.length > MAX.partialReasons) throw new Error("baseline_partial_reasons_invalid");
    const reasons = value.partial_reasons.map(reason => {
      if (typeof reason !== "string" || !/^[A-Za-z0-9._:-]{1,128}$/.test(reason)) throw new Error("baseline_partial_reason_invalid");
      return reason;
    });
    return {
      baseline_seq:value.baseline_seq,
      completeness:baselineCompleteness(value.completeness),
      partial_reasons:[...new Set(reasons)],
      messages:value.messages.map(row => baselineMessage(row, diagnostics, textBudget)),
      tools:value.tools.map(row => baselineTool(row, diagnostics, textBudget)),
      usage:usage(value.usage, diagnostics, "baseline.usage"),
      budget_statuses:value.budget_statuses.map(row => baselineBudget(row, diagnostics, textBudget)),
      app_action_refs:value.app_action_refs.map(row => actionRef(row, diagnostics)),
    };
  }

  function adaptLegacyAgentPollV1(input = {}) {
    const envelope = input.envelope;
    const expected = input.expected_session_id;
    const after = input.requested_after_seq;
    const diagnostics = [];
    if (!SESSION_ID.test(expected || "")) return rejected("expected_session_id_invalid");
    if (!safeInt(after)) return rejected("requested_cursor_invalid");
    if (!isObject(envelope)) return rejected("envelope_invalid");
    if (containsForbidden(Object.fromEntries(Object.entries(envelope).filter(([key]) => !["events", "messages", "candidate", "app_actions"].includes(key))))) return rejected("envelope_protected_field");
    if (!SESSION_ID.test(envelope.session_id || "") || envelope.session_id !== expected) return rejected("session_id_mismatch");
    // The production transactional path requires the Host to echo the exact
    // cursor used to build this response.  This closes the ambiguity between
    // a late response and the caller's current cursor.  Shadow/legacy callers
    // may omit the field for one compatibility cycle, but a present value is
    // always validated and may never disagree with the request.
    if (own(envelope, "requested_after_seq") && !safeInt(envelope.requested_after_seq)) return rejected("requested_cursor_echo_invalid");
    if (own(envelope, "requested_after_seq") && envelope.requested_after_seq !== after) return rejected("requested_cursor_mismatch");
    if (input.require_requested_after_seq === true && !own(envelope, "requested_after_seq")) return rejected("requested_cursor_missing");
    if (!safeInt(envelope.next_seq)) return rejected("next_seq_invalid");
    if (typeof envelope.reset_required !== "boolean") return rejected("reset_required_invalid");
    if (own(envelope, "app_actions_deferred") && typeof envelope.app_actions_deferred !== "boolean") return rejected("app_actions_deferred_invalid");
    if (!envelope.reset_required && envelope.next_seq < after) return rejected("cursor_regression");
    for (const key of ["events", "messages", "app_actions"]) if (own(envelope, key) && !Array.isArray(envelope[key])) return rejected(`${key}_invalid`);
    if (own(envelope, "application_intent") && !isObject(envelope.application_intent)) return rejected("application_intent_invalid");
    for (const key of ["activation", "recovery"]) {
      if (own(envelope, key) && envelope[key] !== null && !isObject(envelope[key])) return rejected(`${key}_invalid`);
    }
    if (isObject(envelope.application_intent)) {
      const application = envelope.application_intent;
      if (!["none", "prepared", "baseline_advanced", "committed", "aborted"].includes(application.status)) return rejected("application_intent_status_invalid");
      for (const key of ["recovery_required", "deferred_while_active"]) {
        if (own(application, key) && typeof application[key] !== "boolean") return rejected(`application_intent_${key}_invalid`);
      }
      const semanticError = validateApplicationIntent(application, expected);
      if (semanticError) return rejected(semanticError);
    }
    const rawEvents = envelope.events || [], rawMessages = envelope.messages || [], rawActions = envelope.app_actions || [];
    if (rawEvents.length > MAX.events) return rejected("events_limit");
    if (rawMessages.length > MAX.messages) return rejected("messages_limit");
    if (rawActions.length > MAX.actions) return rejected("app_actions_limit");
    if (envelope.reset_required && own(envelope, "snapshot_seq") && !safeInt(envelope.snapshot_seq)) return rejected("snapshot_seq_invalid");
    if (envelope.reset_required && safeInt(envelope.snapshot_seq) && envelope.snapshot_seq > envelope.next_seq) return rejected("snapshot_seq_ahead");
    try {
      const textBudget = {value:0}, events = [];
      const candidateEvents = rawEvents.filter(event => isObject(event) && event.type === "candidate");
      // A candidate body is a domain transaction input.  Two sources with the
      // same revision/receipt can still disagree on readiness or review data;
      // choosing either would split the Store reference from the sidecar.
      if (candidateEvents.length > 1 || own(envelope, "candidate") && candidateEvents.length) throw new Error("candidate_source_conflict");
      let previous = 0;
      for (const event of rawEvents) {
        const row = normalizeEvent(event, diagnostics, textBudget);
        if (row.source_seq <= previous) throw new Error("event_sequence_not_strict");
        if (row.source_seq > envelope.next_seq) throw new Error("event_sequence_ahead");
        previous = row.source_seq; events.push(row);
      }
      const baseline = envelope.reset_required ? normalizeBaseline(envelope.baseline, envelope, diagnostics, textBudget) : null;
      if (input.require_event_coverage === true) {
        // A committed ACK may only advance across a fully represented source
        // interval.  The structured reset baseline already represents every
        // seq through baseline_seq; its events are a strict tail.  Legacy
        // resets without an explicit baseline retain their historical
        // overlapping snapshot/event shape for one compatibility cycle.
        const coverageStart = envelope.reset_required && own(envelope, "baseline")
          ? baseline.baseline_seq
          : envelope.reset_required ? null : after;
        if (coverageStart !== null) {
          if (!events.length) {
            if (envelope.next_seq !== coverageStart) throw new Error("event_coverage_gap");
          } else {
            for (let index = 0; index < events.length; index += 1) {
              if (events[index].source_seq !== coverageStart + index + 1) throw new Error("event_coverage_gap");
            }
            if (events.at(-1).source_seq !== envelope.next_seq) throw new Error("event_coverage_gap");
          }
        }
      }
      const baselineMessages = baseline ? baseline.messages : rawMessages.map(value => baselineMessage(value, diagnostics, textBudget));
      if (textBudget.value > MAX.visibleText) throw new Error("visible_text_limit");
      const terminalStatus = optionalString(envelope.terminal_status, 64, diagnostics, "session.terminal_status");
      const sessionState = optionalString(envelope.state, 64, diagnostics, "session.state");
      const projectedTurnState = optionalString(envelope.turn_state, 64, diagnostics, "session.turn_state");
      const session = {
        run_id:optionalString(envelope.run_id, 256, diagnostics, "session.run_id"),
        state:sessionState,
        terminal_status:terminalStatus,
        active:envelope.active === true,
        kind:optionalString(envelope.kind, 64, diagnostics, "session.kind"),
        workflow:optionalString(envelope.workflow, 64, diagnostics, "session.workflow"),
        title:optionalString(envelope.title, 1000, diagnostics, "session.title"),
        model:optionalString(envelope.model, 256, diagnostics, "session.model"),
        effort:optionalString(envelope.effort, 64, diagnostics, "session.effort"),
        provider_id:optionalString(envelope.provider_id, 256, diagnostics, "session.provider_id"),
        connection_kind:optionalString(envelope.connection_kind, 64, diagnostics, "session.connection_kind"),
        can_resume:envelope.can_resume === true,
        history_truncated:envelope.history_truncated === true,
        conversation_state:optionalString(envelope.conversation_state, 64, diagnostics, "session.conversation_state"),
        // The durable terminal is authoritative over a stale/missing UI axis.
        // Native Router terminal envelopes intentionally need not repeat
        // turn_state, so retaining the prior `using_tool` value would leave
        // the GUI permanently active after a completed turn.
        turn_state:terminalTurnState(terminalStatus) || terminalTurnState(sessionState) || projectedTurnState,
        document_state:optionalString(envelope.document_state, 64, diagnostics, "session.document_state"),
        inbox_state:optionalString(envelope.inbox_state, 64, diagnostics, "session.inbox_state"),
        ...(own(envelope, "terminal_reason") ? {
          terminal_reason:optionalString(envelope.terminal_reason, 128, diagnostics, "session.terminal_reason"),
        } : {}),
      };
      const connection = {
        credential_state:optionalString(envelope.credential_state, 64, diagnostics, "connection.credential_state"),
        provider_state:optionalString(envelope.provider_state, 64, diagnostics, "connection.provider_state"),
        connection_state:optionalString(envelope.connection_state, 64, diagnostics, "connection.state"),
        runtime_incarnation:optionalString(envelope.runtime_incarnation, 256, diagnostics, "connection.runtime_incarnation"),
        runtime_state:optionalString(envelope.runtime_state, 64, diagnostics, "connection.runtime_state"),
        runtime_health:optionalString(envelope.runtime_health, 64, diagnostics, "connection.runtime_health"),
        failure_code:optionalString(envelope.failure_code || envelope.runtime_failure_code, 128, diagnostics, "connection.failure_code"),
      };
      const application = isObject(envelope.application_intent) ? envelope.application_intent : null;
      if (application && containsForbidden(application)) throw new Error("application_protected_field");
      const projection = {
        source:{session_id:expected, requested_after_seq:own(envelope, "requested_after_seq") ? envelope.requested_after_seq : after, next_seq:envelope.next_seq, snapshot_seq:envelope.reset_required ? (baseline?.baseline_seq ?? (safeInt(envelope.snapshot_seq) ? envelope.snapshot_seq : envelope.next_seq)) : null, reset_required:envelope.reset_required, partial:true},
        session, connection, events, baseline_messages:baselineMessages,
        baseline,
        usage:usage(envelope.usage, diagnostics),
        candidate_ref:candidateRef(envelope.candidate, diagnostics),
        app_action_refs:rawActions.map(value => actionRef(value, diagnostics)),
        application_ref:application ? {
          status:optionalString(application.status, 128, diagnostics, "application.status"),
          recovery_required:application.recovery_required === true,
          deferred_while_active:application.deferred_while_active === true || envelope.app_actions_deferred === true,
        } : null,
      };
      return deepFreeze({ok:true, contract_version:CONTRACT, partial:true, diagnostics:[...new Set(diagnostics)], projection});
    } catch (error) {
      return rejected(typeof error?.message === "string" && /^[a-z0-9_.:-]+$/.test(error.message) ? error.message : "projection_invalid", diagnostics);
    }
  }

  global.AgentWorkbenchContract = Object.freeze({
    version:CONTRACT,
    adaptLegacyAgentPollV1,
  });
})(window);
