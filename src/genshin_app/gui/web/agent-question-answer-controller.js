(function (global) {
  "use strict";

  const MAX_ITEMS = 3;
  const MAX_OPTIONS = 32;
  const MAX_TEXT = 16000;
  const ID = /^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$/u;
  const required = (options, name) => {
    const value = options[name];
    if (typeof value !== "function") throw new TypeError(`question_answer_${name}_missing`);
    return value;
  };
  const integer = value => Number.isSafeInteger(value) && value >= 0 ? value : null;
  const identity = value => typeof value === "string" && ID.test(value) ? value : null;

  function normalizedAnswer(value) {
    if (!value || typeof value !== "object" || !Array.isArray(value.items)
        || value.items.length < 1 || value.items.length > MAX_ITEMS) return null;
    const items = [];
    const questionIds = new Set();
    for (const raw of value.items) {
      const questionId = identity(raw?.question_id);
      if (!questionId || questionIds.has(questionId) || typeof raw?.skipped !== "boolean"
          || !Array.isArray(raw?.value_ids) || raw.value_ids.length > MAX_OPTIONS) return null;
      const valueIds = raw.value_ids.map(identity);
      if (valueIds.some(item => !item) || new Set(valueIds).size !== valueIds.length) return null;
      const answerText = raw.text === null ? null : typeof raw.text === "string" ? raw.text : null;
      if (raw.text !== null && (answerText === null || answerText.length > MAX_TEXT)) return null;
      if (raw.skipped && (answerText !== null || valueIds.length)) return null;
      questionIds.add(questionId);
      items.push({question_id:questionId, text:answerText, value_ids:valueIds, skipped:raw.skipped});
    }
    return {items};
  }

  const fingerprint = answer => JSON.stringify(answer);
  const answerMatches = (left, right) => {
    const first = normalizedAnswer(left), second = normalizedAnswer(right);
    return first !== null && second !== null && fingerprint(first) === fingerprint(second);
  };

  function create(options = {}) {
    const api = required(options, "api");
    const getSession = required(options, "getSession");
    const hasCapability = required(options, "hasCapability");
    const loadFoundation = required(options, "loadFoundation");
    const schedule = required(options, "schedule");
    const notice = required(options, "notice");
    const errorText = required(options, "errorText");
    const inflight = new Map();
    const settled = new Map();

    const isCurrent = (session, epoch) =>
      getSession(session.session_id) === session && session.epoch === epoch;
    const durableAnswer = (session, request) => {
      const row = session.workbenchFoundation?.questions?.find(item =>
        (item.batch_id || item.question_id) === request.batch_id
      );
      if (!row || !["answered", "resuming", "settled"].includes(row.state)) return null;
      return answerMatches(row.answer, request.answer) ? row : false;
    };

    async function verify(session, request, epoch) {
      try {
        await loadFoundation(session, {force:true});
      } catch {
        return null;
      }
      if (!isCurrent(session, epoch)) return null;
      return durableAnswer(session, request);
    }

    async function execute(session, request, epoch, key, signature) {
      let response, transportError = null;
      try {
        response = await api("agent_submit_question_batch_answer", session.session_id, {
          batch_id:request.batch_id,
          answer:request.answer,
          expected_revision:request.projection_revision,
          mutation_id:request.mutation_id,
        });
      } catch (error) {
        transportError = error;
      }
      if (!isCurrent(session, epoch)) return false;
      if (transportError || !response || response.code) {
        // A lost command ACK is never retried.  Refresh the Host projection and
        // accept only exact durable evidence for the same answer.
        const row = await verify(session, request, epoch);
        if (row) {
          settled.set(key, {signature, mutationId:request.mutation_id});
          schedule(session);
          notice(row.state === "settled"
            ? "回答已由 App 处理，任务继续运行。"
            : "App 已保存相同回答，正在恢复 Agent。");
          return true;
        }
        if (row === false) notice("App 已保存这组问题的另一份回答；当前草稿不能覆盖它。", true);
        else notice(errorText(transportError || response, "App 未能接收问题回答；草稿仍保留。"), true);
        return false;
      }
      settled.set(key, {signature, mutationId:request.mutation_id});
      await verify(session, request, epoch);
      if (!isCurrent(session, epoch)) return false;
      schedule(session);
      notice(response.data?.question_state === "settled"
        ? "回答已送达 Agent，任务继续运行。"
        : "App 已安全保存回答，正在恢复 Agent。");
      return true;
    }

    function submit(raw) {
      const sessionId = identity(raw?.session_id), batchId = identity(raw?.batch_id);
      const projectionRevision = integer(raw?.projection_revision);
      const mutationId = identity(raw?.mutation_id), answer = normalizedAnswer(raw?.answer);
      const session = sessionId ? getSession(sessionId) : null;
      if (!session || !hasCapability("structured_question_batch") || !batchId
          || projectionRevision === null || !mutationId || answer === null) return Promise.resolve(false);
      const key = `${sessionId}:${batchId}`;
      const answerFingerprint = fingerprint(answer);
      // The durable conflict boundary is the semantic answer, not the client
      // mutation id.  A reconstructed GUI may generate a fresh mutation id
      // for the same already-accepted answer and must still be read-only.
      const signature = answerFingerprint;
      const completed = settled.get(key);
      if (completed) {
        if (completed.signature === signature) return Promise.resolve(true);
        notice("App 已接收这组问题的回答；不能再提交另一份答案。", true);
        return Promise.resolve(false);
      }
      const current = inflight.get(key);
      if (current) {
        if (current.signature === signature) return current.promise;
        notice("这组问题正在提交另一份回答。请等待 App 确认后再修改。", true);
        return Promise.resolve(false);
      }
      const request = {...raw, session_id:sessionId, batch_id:batchId,
        projection_revision:projectionRevision, mutation_id:mutationId, answer};
      const owner = {};
      const epoch = session.epoch;
      const promise = execute(session, request, epoch, key, signature).finally(() => {
        if (inflight.get(key)?.owner === owner) inflight.delete(key);
      });
      inflight.set(key, {owner, signature, promise});
      return promise;
    }

    return Object.freeze({submit, activeCount:() => inflight.size});
  }

  global.AgentQuestionAnswerController = Object.freeze({create, normalizedAnswer, answerMatches});
})(window);
