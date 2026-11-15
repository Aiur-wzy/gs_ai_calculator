/** Real local-pipe, public-DSH Fetch/stream qualification; never calls a model. */
import assert from 'node:assert/strict'
import { randomUUID } from 'node:crypto'
import { once } from 'node:events'
import { connect, createServer } from 'node:net'
import { join } from 'node:path'
import { tmpdir } from 'node:os'
import { NativeMuxPipeClient } from './native-mux-pipe-client.mjs'
import {
  DSH_MUX_GATEWAY_CONTRACT, NativeDshMuxGateway, PRODUCTION_OWNER,
  encodeDshFetchPayload, decodeDshFetchPayload,
} from './native-dsh-mux-gateway.mjs'
import { createDshMuxFixtureHost } from './native-dsh-mux-fixture-host.mjs'
import { assertPinnedNodeVersion } from './runtime-version.mjs'

assertPinnedNodeVersion()
const EMPTY = Buffer.alloc(0)

async function withCarrier(operation, { owner = true } = {}) {
  const name = process.platform === 'win32'
    ? `\\\\.\\pipe\\genshin-dsh-mux-probe-${randomUUID()}`
    : join(tmpdir(), `genshin-mux-${randomUUID()}.sock`)
  const identity = {
    connectionGeneration: 1, connectionId: 'gateway-probe', runtimeIncarnation: 'gateway-runtime',
  }
  const server = createServer()
  server.listen(name)
  await once(server, 'listening')
  const accepted = once(server, 'connection')
  const hostSocket = connect(name)
  await once(hostSocket, 'connect')
  const [runtimeSocket] = await accepted
  const host = new NativeMuxPipeClient(hostSocket, {
    senderRole: 'host', peerSenderRole: 'runtime', ...identity,
  })
  const runtime = new NativeMuxPipeClient(runtimeSocket, {
    senderRole: 'runtime', peerSenderRole: 'host', ...identity,
  })
  const fixture = await createDshMuxFixtureHost({ owner })
  let write = Promise.resolve()
  let failure
  let closing = false
  const gateway = new NativeDshMuxGateway({
    ...fixture,
    send(packet) {
      runtime.enqueue(packet)
      write = write.then(() => runtime.writeNext())
      return write
    },
    onFailure(error) { failure = error; runtime.close() },
  })
  const pump = (async () => {
    try {
      while (!closing) {
        const packet = await runtime.readNext()
        gateway.accept(packet)
        runtime.settleRead('accepted')
      }
    } catch (error) { if (!closing) failure = error }
  })()
  const sequences = new Map()
  const send = async (kind, value, { stream = null, correlation = stream ?? randomUUID(), control = false } = {}) => {
    const sequence = stream === null ? null : (sequences.get(stream) ?? 0) + 1
    if (stream !== null) sequences.set(stream, sequence)
    host.enqueue({
      lane: control ? 'control' : stream === null ? 'remote_unary' : 'remote_stream',
      payloadKind: kind,
      payload: value instanceof Uint8Array ? value : Buffer.from(JSON.stringify(value)),
      messageId: randomUUID(), correlationId: correlation,
      streamId: stream, streamSeq: sequence, trustedClass: control ? 'control' : 'normal',
    })
    await host.writeNext()
    return { correlation, sequence }
  }
  const read = async kind => {
    const packet = await host.readNext({ idleTimeoutMs: 10_000 })
    host.settleRead('accepted')
    assert.equal(packet.header.payload_kind, kind,
      `${packet.header.payload_kind}: ${packet.payload.toString('utf8').slice(0, 1000)}`)
    return packet
  }
  try {
    await operation({ gateway, host, runtime, fixture, send, read })
    assert.equal(failure, undefined)
  } finally {
    closing = true
    await gateway.close()
    host.close()
    runtime.close()
    await pump
    await fixture.dispose()
    await new Promise(resolve => server.close(resolve))
  }
}

function rpcBody(id, endpoint, args) {
  return Buffer.from(JSON.stringify({ type: 'client-request', rpcId: id,
    method: endpoint, payload: { args } }))
}
function fetchPacket(id, endpoint, args) {
  return encodeDshFetchPayload({ method: 'POST', path: `/api/${endpoint}`,
    headers: [['content-type', 'application/json']] }, rpcBody(id, endpoint, args))
}

const evidence = {}
await withCarrier(async ({ send, read, fixture, gateway, runtime }) => {
  const id = randomUUID()
  const text = `中文-${'x'.repeat(2 * 1024 * 1024)}`
  await send('dsh_fetch', fetchPacket(id, 'probe/echo', { value: text }), { correlation: id })
  const result = decodeDshFetchPayload((await read('dsh_fetch_result')).payload)
  assert.equal(result.metadata.status, 200)
  const envelope = JSON.parse(result.body)
  assert.equal(envelope.rpcId, id)
  assert.equal(envelope.result.value.echoed, text)
  evidence.binaryFetch = { bytes: result.body.length, rpcCorrelationPreserved: true }

  const missing = randomUUID()
  await send('dsh_fetch', fetchPacket(missing, 'probe/missing', {}), { correlation: missing })
  assert.equal(decodeDshFetchPayload((await read('dsh_fetch_result')).payload).metadata.status, 404)

  const stream = 'count-stream'
  await send('dsh_stream_open', { endpoint: 'probe/count', payload: { args: { limit: 3 } },
    has_uplink: false }, { stream })
  await read('dsh_stream_opened')
  assert.equal(fixture.metrics.countItems, 0)
  for (let index = 0; index < 3; index += 1) {
    await send('dsh_stream_credit', { items: 1 }, { stream })
    assert.deepEqual(JSON.parse((await read('dsh_stream_item')).payload), { value: { index } })
    assert.equal(fixture.metrics.countItems, index + 1)
  }
  await send('dsh_stream_credit', { items: 1 }, { stream })
  await read('dsh_stream_end')
  evidence.pullBackpressure = true

  const duplex = 'duplex-stream'
  await send('dsh_stream_open', { endpoint: 'probe/duplex', payload: { args: {} }, has_uplink: true },
    { stream: duplex })
  await read('dsh_stream_opened')
  await send('dsh_stream_credit', { items: 1 }, { stream: duplex })
  const uplink = await send('dsh_stream_item', { value: { n: 7 } }, { stream: duplex })
  const ack = JSON.parse((await read('dsh_stream_ack')).payload)
  assert.equal(ack.sequence, uplink.sequence)
  assert.deepEqual(JSON.parse((await read('dsh_stream_item')).payload), { value: { echoed: { n: 7 } } })
  const end = await send('dsh_stream_end', {}, { stream: duplex })
  assert.equal(JSON.parse((await read('dsh_stream_ack')).payload).sequence, end.sequence)
  await send('dsh_stream_credit', { items: 1 }, { stream: duplex })
  await read('dsh_stream_end')
  assert.equal(fixture.metrics.uplinkItems, 1)
  evidence.duplexConsumedAck = true

  const events = 'event-stream'
  await send('dsh_stream_open', { endpoint: '$events', payload: { args: {} }, has_uplink: false },
    { stream: events })
  await read('dsh_stream_opened')
  await send('dsh_stream_credit', { items: 1 }, { stream: events })
  const generation = JSON.parse((await read('dsh_stream_item')).payload).value
  assert.equal(generation.type, 'ready')
  assert.equal(typeof generation.clientId, 'string')
  await send('dsh_stream_credit', { items: 1 }, { stream: events })
  // A pending iterator read cannot prevent a control-lane cancellation ACK.
  await send('dsh_stream_cancel', { stream_id: events }, { control: true })
  await read('dsh_stream_cancelled')
  assert.equal(gateway.snapshot().activeStreams, 0)
  evidence.eventGenerationAndCancel = true

  const hang = randomUUID()
  await send('dsh_fetch', fetchPacket(hang, 'probe/hang', {}), { correlation: hang })
  await send('dsh_fetch_cancel', { correlation_id: hang }, { control: true })
  await read('dsh_fetch_cancelled')
  await read('dsh_fetch_error')
  await gateway.whenIdle()
  assert.equal(fixture.metrics.cancelled, 1)
  assert.equal(gateway.snapshot().activeFetches, 0)
  evidence.fetchCancellation = true

  await send('dsh_stream_open', { endpoint: 'probe/failAfter', payload: { args: {} },
    has_uplink: false }, { stream: 'failure-stream' })
  await read('dsh_stream_opened')
  await send('dsh_stream_credit', { items: 1 }, { stream: 'failure-stream' })
  assert.deepEqual(JSON.parse((await read('dsh_stream_item')).payload), { value: { beforeFailure: true } })
  await send('dsh_stream_credit', { items: 1 }, { stream: 'failure-stream' })
  const mappedFailure = JSON.parse((await read('dsh_stream_error')).payload)
  assert.equal(typeof mappedFailure.code, 'string')
  assert.equal(mappedFailure.message, 'fixture-stream-failure')
  const afterFailure = randomUUID()
  await send('dsh_fetch', fetchPacket(afterFailure, 'probe/echo', { value: 'still-connected' }),
    { correlation: afterFailure })
  assert.equal(JSON.parse(decodeDshFetchPayload((await read('dsh_fetch_result')).payload).body)
    .result.value.echoed, 'still-connected')
  evidence.streamFailureIsolated = true
  evidence.telemetry = runtime.diagnosticSnapshot().counters
})

await withCarrier(async ({ send, read }) => {
  const id = randomUUID()
  await send('dsh_fetch', fetchPacket(id, 'probe/echo', { value: 'deny' }), { correlation: id })
  const response = decodeDshFetchPayload((await read('dsh_fetch_result')).payload)
  assert.equal(JSON.parse(response.body).result.error.code, 'remote-access/denied')
  await send('dsh_stream_open', { endpoint: 'probe/count', payload: { args: { limit: 1 } },
    has_uplink: false }, { stream: 'denied-stream' })
  assert.equal(JSON.parse((await read('dsh_stream_error')).payload).code, 'remote-access/denied')
  evidence.nonOwnerDeniedByDsh = true
}, { owner: false })

// Untrusted wire errors fail before invoking a Host service.
assert.throws(() => decodeDshFetchPayload(EMPTY), /dsh_mux_fetch_payload_invalid/u)
assert.throws(() => decodeDshFetchPayload(Buffer.from([0, 2, 0, 0, 0])), /dsh_mux_fetch_metadata_invalid/u)

async function adversarial() {
  const fixture = await createDshMuxFixtureHost()
  const output = []
  const gateway = new NativeDshMuxGateway({ ...fixture, send: async packet => { output.push(packet) } })
  let sequence = 0
  const packet = (kind, payload, stream = null, correlation = randomUUID()) => ({
    header: { lane: stream === null ? 'remote_unary' : 'remote_stream',
      payload_kind: kind, correlation_id: correlation, stream_id: stream,
      stream_seq: stream === null ? null : ++sequence },
    payload: payload instanceof Uint8Array ? payload : Buffer.from(JSON.stringify(payload)),
  })
  try {
    for (const path of ['https://other.invalid/api/probe/echo', '//other.invalid/api',
      '/api/../outside', '/api/%2e%2e/outside', '/api\\outside']) {
      assert.throws(() => gateway.accept(packet('dsh_fetch', encodeDshFetchPayload({
        method: 'POST', path, headers: [],
      }))), /dsh_mux_fetch_route_invalid/u)
    }
    const id = randomUUID()
    const valid = packet('dsh_fetch', fetchPacket(id, 'probe/echo', { value: 1 }), null, id)
    gateway.accept(valid)
    await gateway.whenIdle()
    assert.equal(output.length, 1)
    assert.throws(() => gateway.accept(valid), /dsh_mux_fetch_replayed/u)

    const earlyFetchId = randomUUID()
    gateway.accept({ header: { lane: 'control', payload_kind: 'dsh_fetch_cancel',
      correlation_id: randomUUID(), stream_id: null },
    payload: Buffer.from(JSON.stringify({ correlation_id: earlyFetchId })) })
    gateway.accept(packet('dsh_fetch', fetchPacket(earlyFetchId, 'probe/hang', {}), null, earlyFetchId))
    await gateway.whenIdle()
    assert.equal(output.at(-1).payloadKind, 'dsh_fetch_error')
    assert.equal(JSON.parse(output.at(-1).payload).code, 'dsh_mux_request_cancelled')
    assert.equal(gateway.snapshot().activeFetches, 0)

    gateway.accept({ header: { lane: 'control', payload_kind: 'dsh_stream_cancel',
      correlation_id: randomUUID(), stream_id: null },
    payload: Buffer.from(JSON.stringify({ stream_id: 'cancel-before-open' })) })
    gateway.accept(packet('dsh_stream_open', { endpoint: 'probe/count',
      payload: { args: { limit: 99 } }, has_uplink: false }, 'cancel-before-open'))
    await gateway.whenIdle()
    assert.equal(output.at(-1).payloadKind, 'dsh_stream_error')
    assert.equal(JSON.parse(output.at(-1).payload).code, 'dsh_mux_request_cancelled')
    assert.equal(gateway.snapshot().activeStreams, 0)
    assert.equal(fixture.metrics.countItems, 0)
    gateway.accept(packet('dsh_stream_credit', { items: 1 }, 'cancel-before-open'))
    gateway.accept(packet('dsh_stream_item', { value: 'late' }, 'cancel-before-open'))
    gateway.accept(packet('dsh_stream_end', {}, 'cancel-before-open'))
    assert.throws(() => gateway.accept(packet('dsh_stream_credit', { items: 1 }, 'unknown-stream')),
      /dsh_mux_stream_not_open/u)
    evidence.cancelBeforeDispatch = true

    const open = packet('dsh_stream_open', { endpoint: 'probe/duplex', payload: { args: {} },
      has_uplink: true }, 'uplink-window')
    gateway.accept(open)
    await gateway.whenIdle()
    gateway.accept(packet('dsh_stream_item', { value: 1 }, 'uplink-window'))
    assert.throws(() => gateway.accept(packet('dsh_stream_item', { value: 2 }, 'uplink-window')),
      /dsh_mux_uplink_window_exhausted/u)
    assert.throws(() => gateway.accept(open), /dsh_mux_stream_replayed/u)
    assert.equal(fixture.metrics.uplinkItems, 0)
    assert.throws(() => gateway.accept(packet('dsh_stream_credit', { items: 2 }, 'uplink-window')),
      /dsh_mux_stream_credit_invalid/u)

    gateway.accept(packet('dsh_stream_open', { endpoint: 'probe/count',
      payload: { args: { limit: 1 } }, has_uplink: false }, 'no-uplink'))
    await gateway.whenIdle()
    assert.throws(() => gateway.accept(packet('dsh_stream_item', { value: 1 }, 'no-uplink')),
      /dsh_mux_uplink_window_exhausted/u)
    await gateway.close()
    assert.throws(() => gateway.accept(valid), /dsh_mux_gateway_closed/u)
    assert.equal(gateway.snapshot().activeStreams, 0)
  } finally { await gateway.close(); await fixture.dispose() }

  let disposed = false
  let failed
  const sendFailure = new NativeDshMuxGateway({
    handler: { fetch: async () => new Response('ok') },
    wireStream: { open: async () => [], failure: () => ({ code: 'test', message: 'test', details: {} }) },
    peer: { async dispose() { disposed = true } },
    send: async () => { throw new Error('send-failed') },
    onFailure(error) { failed = error },
  })
  sendFailure.accept(packet('dsh_fetch', encodeDshFetchPayload({
    method: 'GET', path: '/api/raw', headers: [],
  })))
  await sendFailure.whenIdle()
  assert.equal(failed.message, 'send-failed')
  assert.equal(disposed, true)
  assert.equal(sendFailure.snapshot().closed, true)
  evidence.adversarialAdmissionAndFailure = true
}
await adversarial()
assert.equal(PRODUCTION_OWNER, false)
process.stdout.write(`${JSON.stringify({ contract: DSH_MUX_GATEWAY_CONTRACT,
  status: 'passed', productionOwner: false, ...evidence }, null, 2)}\n`)
