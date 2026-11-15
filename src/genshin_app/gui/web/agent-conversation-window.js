(function (global) {
  "use strict";

  // Renderer-only window state. Durable history, search and cursor ownership
  // stay with the Host/store; this module only bounds materialized DOM nodes.
  const DEFAULT_LIMIT = 200;
  const STEP = 100;
  const MAX_LIMIT = 500;
  const ID = /^[A-Za-z0-9._:-]{1,256}$/u;

  const cleanId = value => typeof value === "string" && ID.test(value) ? value : null;
  const integer = value => Number.isSafeInteger(value) && value >= 0 ? value : null;

  function create(options = {}) {
    const defaultLimit = integer(options.defaultLimit) || DEFAULT_LIMIT;
    const step = integer(options.step) || STEP;
    const maximum = integer(options.maximum) || MAX_LIMIT;
    if (defaultLimit < 1 || step < 1 || maximum < defaultLimit) {
      throw new TypeError("conversation_window_limits_invalid");
    }
    const limits = new Map();

    function select(sessionId, rows) {
      const id = cleanId(sessionId);
      if (!id || !Array.isArray(rows)) {
        return Object.freeze({rows:[], total:0, start:0, limit:defaultLimit, has_older:false});
      }
      const admitted = [];
      const seen = new Set();
      for (const row of rows) {
        const rowId = cleanId(row?.id ?? row?.message_id ?? row?.tool_id);
        if (!rowId || seen.has(rowId)) continue;
        seen.add(rowId);
        admitted.push(row);
      }
      const limit = Math.min(maximum, limits.get(id) || defaultLimit);
      const start = Math.max(0, admitted.length - limit);
      return Object.freeze({
        rows:Object.freeze(admitted.slice(start)),
        total:admitted.length,
        start,
        limit,
        has_older:start > 0,
      });
    }

    return Object.freeze({
      select,
      expand(sessionId) {
        const id = cleanId(sessionId);
        if (!id) return false;
        const before = Math.min(maximum, limits.get(id) || defaultLimit);
        const after = Math.min(maximum, before + step);
        limits.set(id, after);
        return after > before;
      },
      reset(sessionId = null) {
        if (sessionId === null) { limits.clear(); return true; }
        const id = cleanId(sessionId);
        return id ? limits.delete(id) : false;
      },
      limit(sessionId) {
        const id = cleanId(sessionId);
        return id ? Math.min(maximum, limits.get(id) || defaultLimit) : defaultLimit;
      },
    });
  }

  global.AgentConversationWindow = Object.freeze({DEFAULT_LIMIT, STEP, MAX_LIMIT, create});
})(window);
