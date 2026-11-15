/** Official DSH Web composition and App-owned research lifecycle boundary. */
import { createHash } from 'node:crypto'

import * as ToolCallTimeoutPolicy from '@deepseek-ai/dsh-tool-call-timeout-policy'
import * as ToolWeb from '@deepseek-ai/dsh-tool-web'
import WebRuntime from '@deepseek-ai/dsh-web'
import {
  DEEPSEEK_SEARCH_API_PROVIDER_ID,
  DEEPSEEK_WEB_API_BASE_URL,
  DeepSeekSearchApiProvider,
} from '@deepseek-ai/dsh-web-deepseek-api'
import * as HttpFetch from '@deepseek-ai/dsh-web-fetch-http'

import { DEEPSEEK_CREDENTIAL_REF } from './memory-credentials.mjs'

export { DEEPSEEK_WEB_API_BASE_URL }
export const NATIVE_WEB_TOOLS = Object.freeze(['web_fetch', 'web_search'])
export const NATIVE_WEB_SEARCH_PROVIDER = 'deepseek-search-api'
export const NATIVE_WEB_FETCH_PROVIDER = 'http'

export const NATIVE_WEB_LIMITS = Object.freeze({
  searchMaxResults: 8,
  searchMaxQueries: 4,
  searchTimeoutMs: 30_000,
  fetchTimeoutMs: 30_000,
  fetchMaxOutputChars: 200_000,
  fetchMaxResponseBytes: 5_000_000,
  fetchMaxBodyChars: 100_000,
  fetchProviderTimeoutMs: 30_000,
  fetchMaxRedirects: 5,
})

const WEB_TOOL_NAMES = new Set(NATIVE_WEB_TOOLS)
const RESEARCH_ID = /^research_[A-Za-z0-9_-]{32}$/u
const SHA256 = /^[0-9a-f]{64}$/u

export class NativeWebStackError extends Error {
  constructor(code) {
    super(code)
    this.name = 'NativeWebStackError'
    this.code = code
  }
}

function fail(code) {
  throw new NativeWebStackError(code)
}

function exactObject(value, fields, code) {
  if (value === null || typeof value !== 'object' || Array.isArray(value)) fail(code)
  const keys = Reflect.ownKeys(value)
  if (keys.some(key => typeof key !== 'string')
      || keys.length !== fields.size
      || keys.some(key => !fields.has(key))) fail(code)
}

function validateResearchAck(value) {
  exactObject(value, new Set([
    'status', 'research_id', 'research_ledger_revision', 'arguments_hash',
    'raw_arguments_hash',
  ]), 'research_pre_registration_ack_invalid')
  if (!['registered', 'already_registered'].includes(value.status)
      || typeof value.research_id !== 'string'
      || !RESEARCH_ID.test(value.research_id)
      || !Number.isSafeInteger(value.research_ledger_revision)
      || value.research_ledger_revision < 0
      || typeof value.arguments_hash !== 'string'
      || !SHA256.test(value.arguments_hash)
      || typeof value.raw_arguments_hash !== 'string'
      || !SHA256.test(value.raw_arguments_hash)) {
    fail('research_pre_registration_ack_invalid')
  }
  return Object.freeze({
    status: value.status,
    researchId: value.research_id,
    researchLedgerRevision: value.research_ledger_revision,
    argumentsHash: value.arguments_hash,
    rawArgumentsHash: value.raw_arguments_hash,
  })
}

function canonicalJson(value) {
  if (value === null || typeof value === 'boolean' || typeof value === 'string') {
    return JSON.stringify(value)
  }
  if (typeof value === 'number') {
    if (!Number.isFinite(value)) fail('research_result_not_json')
    return JSON.stringify(value)
  }
  if (Array.isArray(value)) return `[${value.map(canonicalJson).join(',')}]`
  if (typeof value !== 'object') fail('research_result_not_json')
  const keys = Reflect.ownKeys(value)
  if (keys.some(key => typeof key !== 'string')) fail('research_result_not_json')
  keys.sort()
  return `{${keys.map(key => `${JSON.stringify(key)}:${canonicalJson(value[key])}`).join(',')}}`
}

function contentHash(content) {
  return createHash('sha256').update(canonicalJson(content), 'utf8').digest('hex')
}

function terminalStatus(result) {
  if (!result.isError) return 'completed'
  const code = result.error?.info?.code
  return code === 'ABORTED' || code === 'ABORTED_BEFORE_DISPATCH'
    || code === 'WEB_ABORTED'
    ? 'cancelled'
    : 'failed'
}

function genericDenial(code, reason) {
  return Object.freeze({
    kind: 'deny',
    reason,
    info: Object.freeze({ name: 'NativeResearchError', code }),
  })
}

function assertCallbacks(value) {
  if (value === null || typeof value !== 'object' || Array.isArray(value)) {
    fail('research_callbacks_invalid')
  }
  const allowed = new Set(['preRegister', 'onResult', 'onFatal'])
  const keys = Reflect.ownKeys(value)
  if (keys.some(key => typeof key !== 'string' || !allowed.has(key))
      || !Object.hasOwn(value, 'preRegister')
      || !Object.hasOwn(value, 'onResult')
      || typeof value.preRegister !== 'function'
      || typeof value.onResult !== 'function'
      || (value.onFatal !== undefined && typeof value.onFatal !== 'function')) {
    fail('research_callbacks_invalid')
  }
  return value
}

/**
 * Per-Agent lifecycle for durable research registration and terminal settlement.
 *
 * `tools/result` is an observe-only DSH event, so asynchronous terminal writes
 * are owned by this queue.  The next Agent step and the App turn boundary both
 * call `whenSettled()`; a callback failure cancels the Agent and is surfaced
 * only through a stable code.
 */
export class NativeWebResearchLifecycle {
  constructor({ agentCtx, agent, callbacks, resolveToolBinding, budgetController, isCurrentTurn = () => true }) {
    this.agentCtx = agentCtx
    this.agent = agent
    this.callbacks = assertCallbacks(callbacks)
    if (typeof resolveToolBinding !== 'function') fail('research_tool_binding_resolver_invalid')
    if (budgetController === null || typeof budgetController !== 'object'
        || typeof budgetController.admitWebTool !== 'function') {
      fail('research_budget_controller_invalid')
    }
    this.resolveToolBinding = resolveToolBinding
    this.budgetController = budgetController
    this.isCurrentTurn = isCurrentTurn
    this.registrations = new Map()
    this.pending = new Set()
    this.fatalCode = undefined

    agentCtx.on('tools/pre-execute', async (exec, next) => {
      if (!WEB_TOOL_NAMES.has(exec.name) || !this.isCurrentTurn()) return next()
      // Durable pre-registration is the idempotency authority.  Admission is
      // charged only after it proves this is a new physical Web attempt;
      // already_registered reconciliation must never consume a second unit.
      const chargeRejectedAttempt = () => {
        try { this.budgetController.admitWebTool(exec.name, exec.arguments) } catch {
          // The original stable failure remains authoritative; the controller
          // still records an exhausted state when this failed attempt crosses it.
        }
      }
      let binding
      try {
        binding = await this.resolveToolBinding(Object.freeze({
          execution_call_id: exec.callId,
          tool: exec.name,
          arguments: exec.arguments,
        }))
        exactObject(binding, new Set([
          'call_id', 'tool', 'arguments_json', 'raw_arguments_hash',
        ]), 'research_tool_binding_invalid')
        if (binding.tool !== exec.name
            || typeof binding.call_id !== 'string'
            || typeof binding.arguments_json !== 'string'
            || !SHA256.test(binding.raw_arguments_hash)
            || createHash('sha256').update(binding.arguments_json, 'utf8').digest('hex')
              !== binding.raw_arguments_hash
            || canonicalJson(JSON.parse(binding.arguments_json))
              !== canonicalJson(exec.arguments)) {
          fail('research_tool_binding_invalid')
        }
      } catch {
        chargeRejectedAttempt()
        this.#fatal('research_tool_binding_invalid')
        return genericDenial(
          'RESEARCH_TOOL_BINDING_FAILED',
          'research tool identity was not bound to the exact model argument block',
        )
      }
      let rawAck
      try {
        rawAck = await this.callbacks.preRegister(Object.freeze({
          call_id: binding.call_id,
          tool: exec.name,
          arguments: exec.arguments,
          arguments_json: binding.arguments_json,
          raw_arguments_hash: binding.raw_arguments_hash,
        }))
      } catch {
        chargeRejectedAttempt()
        this.#fatal('research_pre_registration_callback_failed')
        return genericDenial(
          'RESEARCH_PRE_REGISTRATION_FAILED',
          'research pre-registration failed; the web request was not sent',
        )
      }
      let ack
      try {
        ack = validateResearchAck(rawAck)
        if (ack.argumentsHash !== contentHash(exec.arguments)) {
          fail('research_pre_registration_ack_invalid')
        }
        if (ack.rawArgumentsHash !== binding.raw_arguments_hash) {
          fail('research_pre_registration_ack_invalid')
        }
      } catch {
        chargeRejectedAttempt()
        this.#fatal('research_pre_registration_ack_invalid')
        return genericDenial(
          'RESEARCH_PRE_REGISTRATION_FAILED',
          'research pre-registration returned an invalid acknowledgement; the web request was not sent',
        )
      }
      this.registrations.set(exec.token, Object.freeze({
        ...ack,
        callId: binding.call_id,
        tool: exec.name,
        arguments: exec.arguments,
        argumentsJson: binding.arguments_json,
        rawArgumentsHash: binding.raw_arguments_hash,
        reconcileOnly: ack.status === 'already_registered',
      }))
      if (ack.status === 'already_registered') {
        return genericDenial(
          'RESEARCH_REPLAY_REQUIRES_RECONCILIATION',
          'research call was already registered; reconcile it before retrying',
        )
      }
      try {
        this.budgetController.admitWebTool(exec.name, exec.arguments)
      } catch {
        // Registration now owns a durable failed terminal.  Let DSH settle the
        // denial through tools/result; the frozen controller rejects the next
        // provider step without discarding the current receipt.
        return genericDenial(
          'RESEARCH_BUDGET_EXHAUSTED',
          'the frozen turn Web budget is exhausted',
        )
      }
      return next()
    }, { prepend: true })

    agentCtx.on('tools/result', (exec, result) => {
      if (!WEB_TOOL_NAMES.has(exec.name) || !this.isCurrentTurn()) return
      const registration = this.registrations.get(exec.token)
      if (registration === undefined) return
      if (registration.reconcileOnly) return
      this.registrations.delete(exec.token)
      let terminal
      try {
        const status = terminalStatus(result)
        terminal = Object.freeze({
          research_id: registration.researchId,
          call_id: registration.callId,
          tool: registration.tool,
          arguments: registration.arguments,
          arguments_json: registration.argumentsJson,
          raw_arguments_hash: registration.rawArgumentsHash,
          status,
          content_hash: contentHash(result.content),
          presentation_meta: status === 'completed' ? result.meta ?? null : null,
          resource_ids: Object.freeze([]),
        })
      } catch {
        this.#fatal('research_result_projection_failed')
        return
      }
      this.#enqueue(() => this.callbacks.onResult(terminal))
    })

    agentCtx.on('agent/pre-step', async (_payload, next) => {
      if (!this.isCurrentTurn()) return next()
      try {
        await this.whenSettled()
      } catch {
        return { kind: 'reject' }
      }
      return next()
    }, { prepend: true })
  }

  #fatal(code) {
    if (this.fatalCode !== undefined) return
    this.fatalCode = code
    try {
      Promise.resolve(this.callbacks.onFatal?.(Object.freeze({ code }))).catch(() => {})
    } catch {
      // Failure reporting is best effort and never includes the source error.
    }
    try {
      if (this.isCurrentTurn()) this.agent.cancel({ kind: 'hook', reason: code })
    } catch {
      // The owner still observes the stable failure through whenSettled().
    }
  }

  #enqueue(operation) {
    let tracked
    tracked = Promise.resolve()
      .then(operation)
      .catch(() => this.#fatal('research_result_callback_failed'))
      .finally(() => this.pending.delete(tracked))
    this.pending.add(tracked)
  }

  async whenSettled() {
    while (this.pending.size > 0) await Promise.all([...this.pending])
    if (this.fatalCode !== undefined) fail(this.fatalCode)
    // The App bridge owns crash/cancel reconciliation.  A registration with
    // no DSH result must force that path to record outcome_unknown; it may not
    // disappear behind an otherwise successful Agent turn.
    if (this.registrations.size > 0) fail('research_terminal_missing')
  }

  async retire() {
    await Promise.allSettled([...this.pending])
    for (const registration of this.registrations.values()) {
      if (registration.reconcileOnly) continue
      await this.callbacks.onResult({
        research_id: registration.researchId, call_id: registration.callId, tool: registration.tool,
        arguments: registration.arguments, arguments_json: registration.argumentsJson,
        raw_arguments_hash: registration.rawArgumentsHash, status: 'outcome_unknown',
        content_hash: null, presentation_meta: null, resource_ids: [],
      })
    }
    this.registrations.clear()
  }

  diagnosticSnapshot() {
    const pendingReconciliations = [...this.registrations.values()]
      .filter(value => value.reconcileOnly).length
    return Object.freeze({
      pendingCallbacks: this.pending.size,
      pendingRegistrations: this.registrations.size,
      pendingReconciliations,
      fatalCode: this.fatalCode,
    })
  }
}

/** Mount the one fixed official search provider and the safe public fetcher. */
export async function mountNativeWebRuntime(ctx) {
  if (ctx === null || typeof ctx !== 'object'
      || ctx.credentials === undefined || ctx.tools === undefined) {
    fail('native_web_context_invalid')
  }
  if (DEEPSEEK_SEARCH_API_PROVIDER_ID !== NATIVE_WEB_SEARCH_PROVIDER
      || DEEPSEEK_WEB_API_BASE_URL !== 'https://api.deepseek.com/v1/tools') {
    fail('native_web_upstream_contract_changed')
  }

  await ctx.plugin(WebRuntime, {
    searchProvider: NATIVE_WEB_SEARCH_PROVIDER,
    fetchProvider: NATIVE_WEB_FETCH_PROVIDER,
  })
  ctx.web.registerSearchProvider(new DeepSeekSearchApiProvider(() => ({
    baseURL: DEEPSEEK_WEB_API_BASE_URL,
    count: NATIVE_WEB_LIMITS.searchMaxResults,
    apiKeyEnv: DEEPSEEK_CREDENTIAL_REF,
    resolveApiKey: async () => (
      await ctx.credentials.resolve(DEEPSEEK_CREDENTIAL_REF)
    )?.value,
    // Deliberately omit originalQuestion: sendOriginalQuestion=false.
  })))
  await ctx.plugin(HttpFetch, {
    maxResponseBytes: NATIVE_WEB_LIMITS.fetchMaxResponseBytes,
    maxBodyChars: NATIVE_WEB_LIMITS.fetchMaxBodyChars,
    timeoutMs: NATIVE_WEB_LIMITS.fetchProviderTimeoutMs,
    maxRedirects: NATIVE_WEB_LIMITS.fetchMaxRedirects,
    userAgent: 'genshin-calc-native-agent/0.1',
  })
  await ctx.plugin(ToolCallTimeoutPolicy)

  return Object.freeze({
    searchProvider: NATIVE_WEB_SEARCH_PROVIDER,
    fetchProvider: NATIVE_WEB_FETCH_PROVIDER,
    deepSeekFetchProviderMounted: false,
    limits: NATIVE_WEB_LIMITS,
  })
}

/** Register the same two official model-facing tools in one Agent scope. */
export async function setupNativeWebAgentScope({
  agentCtx, agent, callbacks, resolveToolBinding, budgetController,
  installTools = true, isCurrentTurn,
}) {
  if (agentCtx === null || typeof agentCtx !== 'object'
      || agent === null || typeof agent !== 'object') {
    fail('native_web_agent_scope_invalid')
  }
  const lifecycle = new NativeWebResearchLifecycle({
    agentCtx, agent, callbacks, resolveToolBinding, budgetController, isCurrentTurn,
  })
  if (!installTools) return lifecycle
  await agentCtx.plugin(ToolWeb, {
    search: true,
    fetch: true,
    searchMaxResults: NATIVE_WEB_LIMITS.searchMaxResults,
    searchMaxQueries: NATIVE_WEB_LIMITS.searchMaxQueries,
    searchTimeoutMs: NATIVE_WEB_LIMITS.searchTimeoutMs,
    fetchTimeoutMs: NATIVE_WEB_LIMITS.fetchTimeoutMs,
    fetchMaxOutputChars: NATIVE_WEB_LIMITS.fetchMaxOutputChars,
  })
  return lifecycle
}
