const { test } = require('node:test')
const assert = require('node:assert/strict')
const { pathToFileURL } = require('node:url')
const path = require('node:path')

const moduleUrl = pathToFileURL(path.resolve(__dirname, '../agent-runtime/src/native-dsh-domain-transport.mjs'))

async function fixture() {
  const { NativeDshDomainTransport } = await import(moduleUrl.href)
  const sent = []
  return { sent, transport: new NativeDshDomainTransport({ send: async value => { sent.push(value) } }) }
}

function acknowledgement(cancel, execution = 'did_not_execute') {
  const body = JSON.parse(cancel.payload)
  return { header: { lane: 'control', payload_kind: 'dsh_app_call_cancel_result', correlation_id: body.request_id },
    payload: Buffer.from(JSON.stringify({ contract: 'genshin-native-dsh-domain-cancel-result-v1',
      request_id: body.request_id, session_id: body.session_id, target_request_id: body.target_request_id,
      target_kind: body.target_kind, call_id: body.call_id,
      disposition: execution === 'did_not_execute' ? 'cancelled_before_admission' : 'running',
      app_call_admission: execution === 'did_not_execute' ? 'not_recorded' : 'unknown', execution_state: execution,
      outcome_unknown: execution !== 'did_not_execute', settlement_ref: null, diagnostic_id: null })) }
}

test('abort sends exact cancellation and uses Host proof of no admission', async () => {
  const { sent, transport } = await fixture()
  const controller = new AbortController()
  const result = transport.execute({ session_id: 'one', call_id: 'call-one', raw_arguments_hash: 'a'.repeat(64) }, controller.signal)
  const rejected = assert.rejects(result, error => error.code === 'domain_call_cancelled' && !error.outcomeUnknown)
  controller.abort()
  await Promise.resolve()
  assert.equal(sent.length, 2)
  const cancel = JSON.parse(sent[1].payload)
  assert.equal(sent[1].lane, 'control')
  assert.equal(sent[1].payloadKind, 'dsh_app_call_cancel')
  assert.equal(cancel.target_request_id, JSON.parse(sent[0].payload).request_id)
  assert.equal(cancel.raw_arguments_hash, 'a'.repeat(64))
  transport.accept(acknowledgement(sent[1]))
  await rejected
  transport.close()
})

test('running cancellation remains unknown and late response cannot revive it', async () => {
  const { sent, transport } = await fixture()
  const controller = new AbortController()
  const result = transport.bindSession('one', controller.signal)
  const rejected = assert.rejects(result, error => error.code === 'domain_call_cancelled' && error.outcomeUnknown)
  controller.abort()
  await Promise.resolve()
  transport.accept(acknowledgement(sent[1], 'running'))
  await rejected
  const original = JSON.parse(sent[0].payload)
  assert.equal(transport.accept({ header: { lane: 'remote_unary', payload_kind: 'dsh_app_bind_result',
    correlation_id: original.request_id }, payload: Buffer.from(JSON.stringify({
      contract: 'genshin-native-dsh-domain-bound-v1', request_id: original.request_id, session_id: 'one' })) }), true)
  assert.equal(transport.snapshot().pending, 0)
  transport.close()
})

test('settled cancellation never claims the operation did not execute', async () => {
  const { sent, transport } = await fixture()
  const controller = new AbortController()
  const result = transport.execute({ session_id: 'one', call_id: 'call-one', raw_arguments_hash: 'a'.repeat(64) }, controller.signal)
  const rejected = assert.rejects(result, error => !error.outcomeUnknown && /already settled/.test(error.message))
  controller.abort()
  await Promise.resolve()
  const ack = acknowledgement(sent[1])
  const body = JSON.parse(ack.payload)
  Object.assign(body, { disposition: 'already_settled', app_call_admission: 'settled_success',
    execution_state: 'settled_success', outcome_unknown: false })
  ack.payload = Buffer.from(JSON.stringify(body))
  transport.accept(ack)
  await rejected
  transport.close()
})

test('failed cancel transmission is unknown, never safe cancellation', async () => {
  const { NativeDshDomainTransport } = await import(moduleUrl.href)
  const transport = new NativeDshDomainTransport({ send: packet => {
    if (packet.lane === 'control') throw new Error('closed control pipe')
  } })
  const controller = new AbortController()
  const result = transport.execute({ session_id: 'one', call_id: 'call-one', raw_arguments_hash: 'a'.repeat(64) }, controller.signal)
  const rejected = assert.rejects(result, error => error.code === 'domain_call_cancelled' && error.outcomeUnknown)
  controller.abort()
  await rejected
  transport.close()
})

test('long-lived transport keeps bounded receipts and ignores aged late replies', async () => {
  const { NativeDshDomainTransport } = await import(moduleUrl.href)
  let last
  const transport = new NativeDshDomainTransport({ retainedReceipts: 2, send: async value => { last = value } })
  let first
  for (let i = 0; i < 8; i++) {
    const result = transport.bindSession('one')
    const body = JSON.parse(last.payload)
    const packet = { header: { lane: 'remote_unary', payload_kind: 'dsh_app_bind_result', correlation_id: body.request_id },
      payload: Buffer.from(JSON.stringify({ contract: 'genshin-native-dsh-domain-bound-v1',
        request_id: body.request_id, session_id: 'one' })) }
    first ??= packet
    transport.accept(packet)
    await result
  }
  assert.equal(transport.snapshot().pending, 0)
  assert.equal(transport.snapshot().retained, 2)
  assert.equal(transport.accept(first), true)
  transport.close()
})
