/** Reverse App execution port. DSH owns tools; Python owns durable domain writes. */
import { randomUUID } from 'node:crypto'

export class NativeDshDomainError extends Error {
  constructor(code, message = code, { outcomeUnknown = false, details = {} } = {}) {
    super(message)
    this.name = 'NativeDshDomainError'
    this.code = code
    this.outcomeUnknown = outcomeUnknown
    this.details = details
  }
}

const RPCS = {
  dsh_app_bind_request: ['dsh_app_bind', 'genshin-native-dsh-domain-bound-v1'],
  dsh_app_call_request: ['dsh_app_call', 'genshin-native-dsh-domain-result-v1'],
  dsh_research_register_request: ['dsh_research_register', 'genshin-native-dsh-research-result-v1'],
  dsh_research_settle_request: ['dsh_research_settle', 'genshin-native-dsh-research-result-v1'],
  dsh_budget_acquire_request: ['dsh_budget_acquire', 'genshin-native-dsh-budget-result-v1'],
  dsh_budget_settle_request: ['dsh_budget_settle', 'genshin-native-dsh-budget-result-v1'],
  dsh_budget_tool_request: ['dsh_budget_tool', 'genshin-native-dsh-budget-result-v1'],
}
const KINDS = new Set(Object.values(RPCS).flatMap(([base]) => [base + '_result', base + '_error']))
const record = value => value !== null && typeof value === 'object' && !Array.isArray(value)

export class NativeDshDomainTransport {
  #send
  #pending = new Map()
  #retired = new Set()
  #cancellations = new Map()
  #retainedReceipts
  #uncorrelatedReplies = 0
  #closed = false

  constructor({ send, retainedReceipts = 16_384 }) {
    if (typeof send !== 'function') throw new TypeError('domain_send_required')
    // Mirrors the App catalog's existing MAX_SEQUENTIAL_APP_CALLS capacity;
    // eviction drops receipt metadata, never a live operation or task.
    if (!Number.isSafeInteger(retainedReceipts) || retainedReceipts < 1) throw new TypeError('domain_retention_invalid')
    this.#send = send
    this.#retainedReceipts = retainedReceipts
  }

  #retire(id) {
    this.#retired.add(id)
    while (this.#retired.size > this.#retainedReceipts) this.#retired.delete(this.#retired.values().next().value)
  }

  async #request(kind, body, signal) {
    if (this.#closed) throw new NativeDshDomainError('domain_transport_closed')
    signal?.throwIfAborted()
    const requestId = randomUUID()
    const payload = { ...body, request_id: requestId }
    let resolve, reject
    const answer = new Promise((yes, no) => { resolve = yes; reject = no })
    // Install before send: an in-process or pipe peer can answer immediately.
    const pending = { kind, body: payload, resolve, reject, sent: false, abort: undefined, signal }
    this.#pending.set(requestId, pending)
    pending.abort = () => {
      if (pending.sent && ['dsh_app_call_request', 'dsh_app_bind_request'].includes(kind)) {
        this.#cancelDomain(requestId, pending)
        return
      }
      if (!this.#pending.delete(requestId)) return
      this.#retire(requestId)
      reject(new NativeDshDomainError('domain_call_cancelled', 'App operation cancelled; inspect its durable state before repeating', {
        outcomeUnknown: pending.sent && kind !== 'dsh_app_bind_request',
      }))
    }
    signal?.addEventListener('abort', pending.abort, { once: true })
    try {
      signal?.throwIfAborted()
      pending.sent = true
      await this.#send({ lane: 'remote_unary', payloadKind: kind,
        payload: Buffer.from(JSON.stringify(payload), 'utf8'), messageId: randomUUID(),
        correlationId: requestId, streamId: null, streamSeq: null, trustedClass: 'normal' })
    } catch (error) {
      if (this.#pending.delete(requestId)) {
        this.#retire(requestId)
        reject(new NativeDshDomainError('domain_transport_send_failed', error.message, {
          outcomeUnknown: pending.sent && kind !== 'dsh_app_bind_request',
        }))
      }
    }
    try { return await answer }
    finally { signal?.removeEventListener('abort', pending.abort) }
  }

  #cancelDomain(targetId, pending) {
    if (pending.cancelling || !this.#pending.has(targetId)) return
    pending.cancelling = true
    const id = randomUUID()
    const call = pending.kind === 'dsh_app_call_request'
    const body = { contract: 'genshin-native-dsh-domain-cancel-v1', request_id: id,
      session_id: pending.body.session_id, target_request_id: targetId, target_kind: pending.kind,
      call_id: call ? pending.body.call_id : null,
      raw_arguments_hash: call ? pending.body.raw_arguments_hash : null, reason: 'user_cancel' }
    this.#cancellations.set(id, { body, pending })
    // A local AbortSignal is not proof that the Host removed the queued call.
    // This separate control correlation survives retirement of the tool RPC.
    Promise.resolve().then(() => this.#send({ lane: 'control', payloadKind: 'dsh_app_call_cancel',
      payload: Buffer.from(JSON.stringify(body), 'utf8'), messageId: randomUUID(),
      correlationId: id, streamId: null, streamSeq: null, trustedClass: 'control' })).catch(() => {
      this.#cancellations.delete(id)
      this.#retire(id)
      this.#finishAbort(body, pending, { outcome_unknown: true,
        code: 'domain_cancel_transport_failed' })
    })
  }

  #finishAbort(body, pending, result) {
    if (!this.#pending.delete(body.target_request_id)) return
    this.#retire(body.target_request_id)
    const message = result.execution_state === 'did_not_execute'
      ? 'App operation cancelled before admission; it did not execute'
      : ['settled_success', 'settled_failure'].includes(result.execution_state)
        ? 'App operation already settled; inspect its recorded result, do not repeat the operation'
        : 'App operation cancelled; inspect its durable state before repeating'
    pending.reject(new NativeDshDomainError('domain_call_cancelled',
      message,
      { outcomeUnknown: result.outcome_unknown, details: result }))
  }

  #acceptCancellation({ header, payload }) {
    const id = header.correlation_id
    const selected = this.#cancellations.get(id)
    if (selected === undefined) {
      // A completed receipt can age out during a long-lived desktop session.
      // An uncorrelated late reply grants no capability and is safely ignored.
      this.#uncorrelatedReplies++
      return true
    }
    let value
    try { value = JSON.parse(new TextDecoder('utf8', { fatal: true }).decode(payload)) }
    catch { throw new NativeDshDomainError('domain_cancel_response_invalid') }
    const { body, pending } = selected
    if (!record(value) || value.request_id !== id || value.session_id !== body.session_id) {
      throw new NativeDshDomainError('domain_cancel_response_identity_invalid')
    }
    if (typeof value.code === 'string' && value.outcome_unknown === true) {
      // A rejected cancellation cannot prove whether the original effect ran.
      this.#cancellations.delete(id)
      this.#retire(id)
      this.#finishAbort(body, pending, value)
      return true
    }
    const fields = new Set(['contract', 'request_id', 'session_id', 'target_request_id', 'target_kind', 'call_id',
      'disposition', 'app_call_admission', 'execution_state', 'outcome_unknown', 'settlement_ref', 'diagnostic_id'])
    if (Object.keys(value).length !== fields.size || Object.keys(value).some(key => !fields.has(key))
        || value.contract !== 'genshin-native-dsh-domain-cancel-result-v1'
        || value.target_request_id !== body.target_request_id || value.target_kind !== body.target_kind
        || value.call_id !== body.call_id || typeof value.outcome_unknown !== 'boolean'
        || !['cancelled_before_admission', 'running', 'already_settled', 'not_observed'].includes(value.disposition)
        || !['did_not_execute', 'running', 'settled_success', 'settled_failure', 'unknown_after_interruption'].includes(value.execution_state)
        || !['not_recorded', 'inflight', 'settled_success', 'settled_failure', 'unknown_after_interruption', 'unknown'].includes(value.app_call_admission)
        || !(value.settlement_ref === null || typeof value.settlement_ref === 'string')
        || !(value.diagnostic_id === null || typeof value.diagnostic_id === 'string')
        || (value.disposition === 'cancelled_before_admission' && value.execution_state !== 'did_not_execute')
        || (value.disposition === 'running' && value.execution_state !== 'running')
        || (value.disposition === 'not_observed' && !value.outcome_unknown)
        || (['settled_success', 'settled_failure'].includes(value.execution_state)
          && (value.app_call_admission !== value.execution_state || value.outcome_unknown))
        || (['running', 'unknown_after_interruption'].includes(value.execution_state) && !value.outcome_unknown)
        || (value.execution_state === 'did_not_execute' && (value.app_call_admission !== 'not_recorded' || value.outcome_unknown))) {
      throw new NativeDshDomainError('domain_cancel_response_invalid')
    }
    this.#cancellations.delete(id)
    this.#retire(id)
    this.#finishAbort(body, pending, value)
    return true
  }

  bindSession(sessionId, signal, taskSource) {
    return this.#request('dsh_app_bind_request', {
      contract: 'genshin-native-dsh-domain-bind-v1', session_id: sessionId,
      ...(taskSource ? { task_source: taskSource } : {}),
    }, signal)
  }

  execute(body, signal) {
    return this.#request('dsh_app_call_request', {
      ...body, contract: 'genshin-native-dsh-domain-call-v1',
    }, signal)
  }

  researchRegister(body, signal) {
    return this.#request('dsh_research_register_request', { ...body,
      contract: 'genshin-native-dsh-research-register-v1' }, signal)
  }

  researchSettle(body, signal) {
    return this.#request('dsh_research_settle_request', { ...body,
      contract: 'genshin-native-dsh-research-settle-v1' }, signal)
  }

  budgetAcquire(body, signal) {
    return this.#request('dsh_budget_acquire_request', { ...body,
      contract: 'genshin-native-dsh-budget-acquire-v1' }, signal)
  }

  budgetSettle(body, signal) {
    return this.#request('dsh_budget_settle_request', { ...body,
      contract: 'genshin-native-dsh-budget-settle-v1' }, signal)
  }

  admitTool(body, signal) {
    return this.#request('dsh_budget_tool_request', { ...body,
      contract: 'genshin-native-dsh-budget-tool-v1' }, signal)
  }

  accept({ header, payload }) {
    if (header.lane === 'control' && header.payload_kind === 'dsh_app_call_cancel_result') {
      return this.#acceptCancellation({ header, payload })
    }
    if (header.lane !== 'remote_unary' || !KINDS.has(header.payload_kind)) return false
    const id = header.correlation_id
    const pending = this.#pending.get(id)
    if (pending === undefined) {
      this.#uncorrelatedReplies++
      return true
    }
    let value
    try { value = JSON.parse(new TextDecoder('utf8', { fatal: true }).decode(payload)) }
    catch { throw new NativeDshDomainError('domain_response_json_invalid') }
    const [base, contract] = RPCS[pending.kind]
    if (!record(value) || value.request_id !== id || value.session_id !== pending.body.session_id
        || ![`${base}_result`, `${base}_error`].includes(header.payload_kind)) {
      throw new NativeDshDomainError('domain_response_identity_invalid')
    }
    if (header.payload_kind.endsWith('_error')) {
      if (typeof value.code !== 'string' || typeof value.message !== 'string'
          || !record(value.details) || typeof value.outcome_unknown !== 'boolean') {
        throw new NativeDshDomainError('domain_error_invalid')
      }
    } else if (value.contract !== contract) {
      throw new NativeDshDomainError('domain_response_contract_invalid')
    }
    this.#pending.delete(id)
    this.#retire(id)
    for (const selected of this.#cancellations.values()) {
      if (selected.pending === pending) selected.pending = null
    }
    if (header.payload_kind.endsWith('_error')) pending.reject(new NativeDshDomainError(
      value.code, value.message, { outcomeUnknown: value.outcome_unknown, details: value.details },
    ))
    else pending.resolve(value)
    return true
  }

  close() {
    if (this.#closed) return
    this.#closed = true
    for (const [id, pending] of this.#pending) {
      this.#retire(id)
      pending.reject(new NativeDshDomainError('domain_transport_closed', 'App transport closed', {
        outcomeUnknown: pending.sent && pending.kind !== 'dsh_app_bind_request',
      }))
    }
    this.#pending.clear()
    for (const id of this.#cancellations.keys()) this.#retire(id)
    this.#cancellations.clear()
  }

  snapshot() { return { pending: this.#pending.size, closed: this.#closed,
    retained: this.#retired.size, pendingCancellations: this.#cancellations.size,
    uncorrelatedReplies: this.#uncorrelatedReplies } }
}
