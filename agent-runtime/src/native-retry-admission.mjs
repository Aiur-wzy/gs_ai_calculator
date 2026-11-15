/** D04 B admission only. Retry scheduling, state, waits and replay remain DSH. */
import {
  assistantStreamFirstTokenTime, assistantStreamHasVisibleContent, assistantStreamChunks,
} from '@deepseek-ai/dsh-llm'

export function attemptHasAcceptedOutput(stream) {
  if (!Array.isArray(stream)) return true
  if (assistantStreamHasVisibleContent(stream)
      || assistantStreamFirstTokenTime(stream) !== undefined) return true
  if (stream.some(record => record.type === 'tool-call-chunks')) return true
  return assistantStreamChunks(stream, 'block-start').some(chunk => chunk.blockType === 'tool-call')
    || assistantStreamChunks(stream, 'block-end').some(chunk => chunk.block.type === 'tool-call')
}

export function mountNativeRetryAdmission(ctx, {
  canRetryDomainAttempt = async () => true, onDecision = () => {},
} = {}) {
  // Mounted after dsh-llm-retry; prepend runs this admission check before its
  // public agent/request-error waterfall listener. No local retry loop.
  return ctx.on('agent/request-error', async (payload, next) => {
    const { agent, turn, step, retryPolicy, signal } = payload
    let observation
    let attempt
    let allowed = false
    let reason = 'attempt_not_proven'
    try {
      if (signal.aborted || retryPolicy?.mode !== 'normal') return undefined
      observation = await ctx.sessionQuery.observeSession(agent.id, { signal, projectionMode: 'none' })
      attempt = observation.events.findLast(event => event.type === 'assistant/attempt'
        && event.data.turn === turn && event.data.step === step)
      if (attempt !== undefined && !attemptHasAcceptedOutput(attempt.data.stream)) {
        // This only concerns the failing attempt. Earlier settled tools are
        // never replayed by DSH's same-step recovery and do not veto it.
        allowed = await canRetryDomainAttempt({ sessionId: agent.id, turn, step,
          attemptSeq: attempt.seq, signal }) === true
        reason = allowed ? 'eligible_current_attempt' : 'domain_outcome_unsettled'
      } else if (attempt !== undefined) reason = 'current_attempt_output_accepted'
    } finally { observation?.[Symbol.dispose]() }
    onDecision({ sessionId: agent.id, turn, step, attemptSeq: attempt?.seq ?? null, allowed, reason })
    return allowed ? next() : undefined
  }, { prepend: true })
}
