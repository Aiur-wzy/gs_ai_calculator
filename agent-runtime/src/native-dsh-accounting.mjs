/** Thin Host research/accounting adapters; no generic Agent or retry owner. */
import { createHash, randomUUID } from 'node:crypto'
import * as ToolWeb from '@deepseek-ai/dsh-tool-web'
import { NATIVE_WEB_LIMITS, setupNativeWebAgentScope } from './native-dsh-web-stack.mjs'
import { NativeDshDomainError } from './native-dsh-domain-transport.mjs'

const hash = value => createHash('sha256').update(value, 'utf8').digest('hex')

async function toolFact(ctx, agent, callId, name, signal) {
  const observation = await ctx.sessionQuery.observeSession(agent.id, { projectionMode: 'none', signal })
  try {
    const event = observation.events.findLast(row => row.type === 'tool/call'
      && String(row.data.callId) === String(callId) && row.data.name === name)
    if (event === undefined) throw new NativeDshDomainError('research_call_fact_missing')
    return { turn: event.data.turn, step: event.data.step, call_seq: event.seq }
  } finally { observation[Symbol.dispose]() }
}

async function requestFacts(ctx, sessionId, purpose, signal) {
  const observation = await ctx.sessionQuery.observeSession(sessionId, { projectionMode: 'none', signal })
  try {
    const events = observation.events
    const boundary = events.findLast(row => row.type === 'turn/start' || row.type === 'turn/end')
    const open = boundary?.type === 'turn/start' ? boundary.data.turn : null
    const step = events.findLast(row => row.data?.turn === open && ['step/start', 'step/end'].includes(row.type))
    const openStep = step?.type === 'step/start' ? step.data.step : null
    if (purpose === 'main' && (open === null || openStep === null)) {
      throw new NativeDshDomainError('provider_request_fact_missing')
    }
    return { turn: open, step: openStep }
  } finally { observation[Symbol.dispose]() }
}

export function mountNativeDshAccounting(ctx, { transport }) {
  const research = new Map()
  // The public options carry the exact Session for both main and compaction
  // requests. No process-global active Session/Turn is consulted.
  ctx.on('llm/stream', async function* (options, next) {
    if (typeof options.sessionId !== 'string') throw new NativeDshDomainError('provider_session_identity_missing')
    const purpose = options.purpose ?? 'main'
    const facts = await requestFacts(ctx, options.sessionId, purpose, options.signal)
    const leaseId = randomUUID()
    const request = { session_id: options.sessionId, lease_id: leaseId, purpose, ...facts,
      provider: options.provider, model: options.model, reasoning_effort: options.reasoningEffort ?? null,
      max_tokens: options.maxTokens ?? null }
    const grant = (await transport.budgetAcquire(request, options.signal)).result
    if (grant?.lease_id !== leaseId || typeof grant.allowed !== 'boolean') {
      throw new NativeDshDomainError('provider_budget_ack_invalid')
    }
    if (!grant.allowed) throw new NativeDshDomainError(
      grant.reason?.code ?? 'provider_budget_exhausted', grant.reason?.message ?? 'App provider budget denied this request')
    let usage = null
    let finish = 'interrupted'
    try {
      for await (const chunk of next()) {
        if (chunk.type === 'usage') usage = structuredClone(chunk.usage)
        if (chunk.type === 'finish') finish = chunk.reason.kind
        yield chunk
      }
    } finally {
      // Settling the lease is independent of the cancelled provider signal.
      // Disconnect never causes automatic re-execution of a paid request.
      await transport.budgetSettle({ session_id: options.sessionId, lease_id: leaseId, ...facts,
        purpose, usage, finish: options.signal?.aborted ? 'aborted' : finish })
    }
  })

  ctx.on('agent/created', async ({ agent }) => {
    const scope = agent.ctx
    await scope.plugin(ToolWeb, {
      search: true, fetch: true, searchMaxResults: NATIVE_WEB_LIMITS.searchMaxResults,
      searchMaxQueries: NATIVE_WEB_LIMITS.searchMaxQueries, searchTimeoutMs: NATIVE_WEB_LIMITS.searchTimeoutMs,
      fetchTimeoutMs: NATIVE_WEB_LIMITS.fetchTimeoutMs, fetchMaxOutputChars: NATIVE_WEB_LIMITS.fetchMaxOutputChars,
    })
    scope.on('tools/pre-execute', async (execution, next) => {
      // App and web calls already reserve through their exact effect ledgers.
      // Standard DSH tools use the same generous total budget, with no new
      // per-tool cap or second execution/lifecycle manager.
      if (execution.name.startsWith('gs_') || ['web_search', 'web_fetch'].includes(execution.name)) return next()
      const facts = await requestFacts(ctx, agent.id, 'main', execution.signal)
      const claimId = hash(JSON.stringify({ session_id: agent.id, turn: facts.turn,
        step: facts.step,
        root_call_id: String(execution.rootCallId ?? execution.callId),
        call_id: String(execution.callId), tool: execution.name }))
      const response = await transport.admitTool({ session_id: agent.id, turn: facts.turn,
        claim_id: claimId, category: 'generic' }, execution.signal)
      if (!['reserved', 'already_reserved'].includes(response.result?.status)) {
        throw new NativeDshDomainError('generic_tool_budget_ack_invalid')
      }
      return next()
    })
    let currentTurn = null
    let current = null
    let pendingScope = Promise.resolve()
    const calls = new Map()
    const permits = new Map()
    const permitKey = (tool, args) => tool + ':' + JSON.stringify(args)
    const retire = async () => {
      if (current !== null) {
        await current.lifecycle.retire().catch(() => {})
        await current.fiber.dispose()
        current = null
      }
      // These are only in-flight correlation/admission receipts. The durable
      // Host ledger survives; a previous Turn must not retain a failed permit
      // or call identity in the next Turn's scope.
      calls.clear()
      permits.clear()
    }
    scope.on('session/event', (session, event) => {
      if (session.id !== agent.id || event.type !== 'turn/start') return
      // Routing key is copied from the committed DSH fact, not independently
      // advanced. Old-turn listeners immediately become inert.
      currentTurn = event.data.turn
    })
    scope.on('agent/pre-step', async ({ turn, signal }, next) => {
      if (current?.turn !== turn) {
        pendingScope = pendingScope.then(async () => {
          await retire()
          let lifecycle
          const fiber = await scope.plugin({ name: 'genshin-research-turn', async apply(turnScope) {
            lifecycle = await setupNativeWebAgentScope({
              agentCtx: turnScope, agent, installTools: false,
              isCurrentTurn: () => currentTurn === turn,
              resolveToolBinding: async event => {
                const facts = await toolFact(ctx, agent, event.execution_call_id, event.tool, signal)
                const raw = JSON.stringify(event.arguments)
                const value = { call_id: String(event.execution_call_id), tool: event.tool,
                  arguments_json: raw, raw_arguments_hash: hash(raw) }
                calls.set(value.call_id, { ...facts, ...value })
                return value
              },
              budgetController: { admitWebTool(tool, args) {
                const key = permitKey(tool, args)
                if (!(permits.get(key) > 0)) throw new NativeDshDomainError('research_budget_not_admitted')
                permits.set(key, permits.get(key) - 1)
              } },
              callbacks: {
                async preRegister(event) {
                  const fact = calls.get(event.call_id)
                  if (fact === undefined) throw new NativeDshDomainError('research_registration_fact_missing')
                  const response = await transport.researchRegister({ session_id: agent.id,
                    turn: fact.turn, step: fact.step, call_seq: fact.call_seq, ...event }, signal)
                  if (response.result?.status === 'registered') {
                    const key = permitKey(event.tool, event.arguments)
                    permits.set(key, (permits.get(key) ?? 0) + 1)
                  }
                  return response.result
                },
                async onResult(event) {
                  const fact = calls.get(event.call_id)
                  if (fact === undefined) throw new NativeDshDomainError('research_settlement_fact_missing')
                  await transport.researchSettle({ session_id: agent.id, turn: fact.turn,
                    step: fact.step, call_seq: fact.call_seq, ...event })
                  calls.delete(event.call_id)
                },
              },
            })
          } })
          current = { turn, fiber, lifecycle }
          research.set(agent.id, lifecycle)
        })
      }
      await pendingScope
      return next()
    }, { prepend: true })
    scope.on('agent/turn-stopping', async ({ turn }) => {
      if (current?.turn === turn) await current.lifecycle.whenSettled()
    })
    scope.effect(() => async () => {
      currentTurn = null
      await pendingScope.catch(() => {})
      await retire()
      research.delete(agent.id)
    }, 'App research ledger lifetime')
  }, { prepend: true })
  return Object.freeze({
    researchDiagnostics: sessionId => research.get(sessionId)?.diagnosticSnapshot() ?? null,
    async retireResearch() {
      await Promise.all([...research.values()].map(lifecycle => lifecycle.retire()))
      return [...research].map(([sessionId, lifecycle]) => ({ sessionId, ...lifecycle.diagnosticSnapshot() }))
    },
  })
}
