/** Reverse-RPC cancellation, correlation and durable-outcome admission. */
import assert from 'node:assert/strict'
import { NativeDshDomainTransport } from './native-dsh-domain-transport.mjs'
import { assertPinnedNodeVersion } from './runtime-version.mjs'

assertPinnedNodeVersion()
const packets = []
const transport = new NativeDshDomainTransport({ send: packet => { packets.push(packet) } })
const reply = (packet, kind, extra = {}) => {
  const request = JSON.parse(packet.payload)
  return { header: { lane: 'remote_unary', payload_kind: kind, correlation_id: packet.correlationId },
    payload: Buffer.from(JSON.stringify({ request_id: request.request_id, session_id: request.session_id, ...extra })) }
}
try {
  const before = new AbortController()
  before.abort()
  await assert.rejects(transport.bindSession('cancelled-before', before.signal))
  assert.equal(packets.length, 0)
  const one = transport.bindSession('session-one')
  const two = transport.bindSession('session-two')
  assert.equal(transport.snapshot().pending, 2)
  assert.throws(() => transport.accept(reply(packets[0], 'dsh_app_bind_result', {
    contract: 'genshin-native-dsh-domain-bound-v1', session_id: 'foreign-session', snapshot: {},
  })), error => error.code === 'domain_response_identity_invalid')
  transport.accept(reply(packets[1], 'dsh_app_bind_result', {
    contract: 'genshin-native-dsh-domain-bound-v1', snapshot: { scope: 'two' },
  }))
  transport.accept(reply(packets[0], 'dsh_app_bind_result', {
    contract: 'genshin-native-dsh-domain-bound-v1', snapshot: { scope: 'one' },
  }))
  assert.equal((await one).snapshot.scope, 'one')
  assert.equal((await two).snapshot.scope, 'two')
  const control = new AbortController()
  const active = transport.execute({ session_id: 'session-one', call_id: 'call-one' }, control.signal)
  const activePacket = packets.at(-1)
  const rejected = assert.rejects(active,
    error => error.code === 'domain_call_cancelled' && error.outcomeUnknown === true)
  control.abort()
  // Local cancellation asks the Host for durable outcome. It cannot settle
  // the call until that independent control acknowledgement arrives.
  await Promise.resolve()
  const cancelPacket = packets.at(-1)
  assert.equal(cancelPacket.payloadKind, 'dsh_app_call_cancel')
  const cancel = JSON.parse(cancelPacket.payload)
  transport.accept({ header: { lane: 'control', payload_kind: 'dsh_app_call_cancel_result',
    correlation_id: cancelPacket.correlationId }, payload: Buffer.from(JSON.stringify({
      contract: 'genshin-native-dsh-domain-cancel-result-v1', request_id: cancel.request_id,
      session_id: cancel.session_id, target_request_id: cancel.target_request_id,
      target_kind: cancel.target_kind, call_id: cancel.call_id,
      disposition: 'running', app_call_admission: 'inflight', execution_state: 'running',
      outcome_unknown: true, settlement_ref: null, diagnostic_id: null,
    })) })
  await rejected
  // An already claimed Host operation may settle after caller cancellation;
  // retire its late response without redirecting it to another pending call.
  assert.equal(transport.accept(reply(activePacket, 'dsh_app_call_result', {
    contract: 'genshin-native-dsh-domain-result-v1', status: 'settled_success', result: { done: true },
  })), true)
  const denied = transport.execute({ session_id: 'session-two', call_id: 'call-two' })
  const deniedRejection = assert.rejects(denied,
    error => error.code === 'stale_snapshot' && error.outcomeUnknown === false)
  transport.accept(reply(packets.at(-1), 'dsh_app_call_error', {
    code: 'stale_snapshot', message: 'No claim was made', details: {}, outcome_unknown: false,
  }))
  await deniedRejection
  for (const [method, kind, contract] of [
    ['researchRegister', 'dsh_research_register', 'genshin-native-dsh-research-result-v1'],
    ['researchSettle', 'dsh_research_settle', 'genshin-native-dsh-research-result-v1'],
    ['budgetAcquire', 'dsh_budget_acquire', 'genshin-native-dsh-budget-result-v1'],
    ['budgetSettle', 'dsh_budget_settle', 'genshin-native-dsh-budget-result-v1'],
  ]) {
    const request = transport[method]({ session_id: 'accounted-session', fixture: method })
    const packet = packets.at(-1)
    assert.equal(packet.payloadKind, kind + '_request')
    assert.throws(() => transport.accept(reply(packet, 'dsh_app_call_result', {
      contract: 'genshin-native-dsh-domain-result-v1', result: { fixture: method },
    })), error => error.code === 'domain_response_identity_invalid')
    transport.accept(reply(packet, kind + '_result', { contract, result: { fixture: method } }))
    assert.equal((await request).result.fixture, method)
  }
  const unproven = transport.execute({ session_id: 'session-two', call_id: 'call-three' })
  const interrupted = assert.rejects(unproven,
    error => error.code === 'domain_transport_closed' && error.outcomeUnknown === true)
  transport.close()
  await interrupted
  assert.equal(transport.snapshot().pending, 0)
  console.log(JSON.stringify({ contract: 'genshin-native-dsh-domain-transport-probe-v1', status: 'passed',
    independentSessions: true, mismatchedIdentityRejected: true, cancelBeforeSendNoDispatch: true,
    cancelAfterSendUnknownNoReplay: true, lateSettlementRetired: true,
    explicitPreflightRejectionKnown: true, disconnectAfterSendUnknown: true,
    researchAndBudgetUseExactTypedCorrelations: true }))
} finally { transport.close() }
