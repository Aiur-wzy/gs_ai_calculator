/** Adversarial qualification for the non-production raw mux socket carrier. */
import assert from 'node:assert/strict'
import { randomUUID } from 'node:crypto'
import { EventEmitter } from 'node:events'
import { once } from 'node:events'
import { connect as connectNet, createServer } from 'node:net'
import { performance } from 'node:perf_hooks'

import {
  MAX_MUX_BODY_BYTES,
  encodeMuxPacket,
  makeMuxHeader,
} from './native-mux-protocol.mjs'
import {
  HARD_MAX_MUX_PIPE_BUFFERED_BYTES,
  MAX_NODE_TIMER_TIMEOUT_MS,
  NativeMuxPipeClient,
  NativeMuxPipeClientError,
  PRODUCTION_OWNER,
} from './native-mux-pipe-client.mjs'
import { assertPinnedNodeVersion } from './runtime-version.mjs'

assertPinnedNodeVersion()

const CONNECTION = Object.freeze({
  connectionGeneration: 1,
  connectionId: 'carrier-probe-connection',
  runtimeIncarnation: 'carrier-probe-runtime',
})

class FakeSocket extends EventEmitter {
  paused = false
  destroyed = false
  writes = []
  writeImpl = null

  pause() {
    this.paused = true
    return this
  }

  resume() {
    this.paused = false
    return this
  }

  write(value, callback) {
    const bytes = Buffer.from(value)
    this.writes.push(bytes)
    if (this.writeImpl !== null) return this.writeImpl(bytes, callback)
    queueMicrotask(() => callback())
    return true
  }

  destroy() {
    if (this.destroyed) return this
    this.destroyed = true
    this.emit('close')
    return this
  }

  push(value) {
    this.emit('data', Buffer.from(value))
  }

  remoteEnd() {
    this.emit('end')
  }

  remoteClose() {
    if (this.destroyed) return
    this.destroyed = true
    this.emit('close')
  }
}

function carrier(socket = new FakeSocket(), overrides = {}) {
  return new NativeMuxPipeClient(socket, {
    senderRole: 'host',
    peerSenderRole: 'runtime',
    ...CONNECTION,
    ...overrides,
  })
}

function runtimeWire({
  globalSeq = 1,
  laneSeq = globalSeq,
  lane = 'legacy_turn',
  messageId = `runtime-message-${globalSeq}`,
  connectionId = CONNECTION.connectionId,
  payload = Buffer.from(`runtime-payload-${globalSeq}`, 'utf8'),
  streamId = null,
  streamSeq = null,
} = {}) {
  const header = makeMuxHeader({
    lane,
    sender_role: 'runtime',
    connection_generation: CONNECTION.connectionGeneration,
    connection_id: connectionId,
    runtime_incarnation: CONNECTION.runtimeIncarnation,
    global_seq: globalSeq,
    lane_seq: laneSeq,
    message_id: messageId,
    correlation_id: null,
    stream_id: streamId,
    stream_seq: streamSeq,
    payload_kind: lane === 'legacy_turn' ? 'legacy_frame' : 'control',
    payload,
    priority: lane === 'control' ? 'control' : 'normal',
  })
  return encodeMuxPacket(header, payload)
}

function outbound({
  lane = 'legacy_turn',
  messageId = 'host-message-1',
  payload = Buffer.from('host-payload', 'utf8'),
  trustedClass = 'normal',
} = {}) {
  return {
    lane,
    payloadKind: lane === 'legacy_turn' ? 'legacy_frame' : 'control',
    payload,
    messageId,
    correlationId: null,
    streamId: null,
    streamSeq: null,
    trustedClass,
  }
}

async function rejects(code, operation, causeCode = undefined) {
  let caught
  try {
    await operation()
  } catch (error) {
    caught = error
  }
  assert.ok(caught instanceof NativeMuxPipeClientError, `expected ${code}, received ${caught}`)
  assert.equal(caught.code, code)
  if (causeCode !== undefined) assert.equal(caught.causeCode, causeCode)
  return caught
}

function throws(code, operation) {
  assert.throws(operation, error => (
    error instanceof NativeMuxPipeClientError && error.code === code
  ))
}

// Timer overrides must remain in Node/libuv's actual setTimeout range. Values
// beyond it are rejected instead of being silently clamped to about 1 ms.
{
  throws('mux_pipe_partial_timeout_invalid', () => carrier(new FakeSocket(), {
    partialFrameTimeoutMs: MAX_NODE_TIMER_TIMEOUT_MS + 1,
  }))
  throws('mux_pipe_write_timeout_invalid', () => carrier(new FakeSocket(), {
    writeTimeoutMs: MAX_NODE_TIMER_TIMEOUT_MS + 1,
  }))
  const target = carrier()
  await rejects('mux_pipe_idle_timeout_invalid', () => target.readNext({
    idleTimeoutMs: MAX_NODE_TIMER_TIMEOUT_MS + 1,
  }))
  await rejects('mux_pipe_partial_timeout_invalid', () => target.readNext({
    partialFrameTimeoutMs: MAX_NODE_TIMER_TIMEOUT_MS + 1,
  }))
  target.enqueue(outbound({ messageId: 'timeout-bound-write' }))
  await rejects('mux_pipe_write_timeout_invalid', () => target.writeNext({
    timeoutMs: MAX_NODE_TIMER_TIMEOUT_MS + 1,
  }))
  target.close()
}

// The constructor cap and live receive-buffer cap are separate boundaries.
// Both must fail closed before an oversized aggregate can be retained.
{
  throws('mux_pipe_buffer_limit_too_large', () => carrier(new FakeSocket(), {
    maximumBufferedBytes: HARD_MAX_MUX_PIPE_BUFFERED_BYTES + 1,
  }))
  const socket = new FakeSocket()
  const target = carrier(socket, { maximumBufferedBytes: 8 })
  socket.push(Buffer.alloc(9, 0x62))
  const diagnostic = target.diagnosticSnapshot()
  assert.equal(diagnostic.poisoned, true)
  assert.equal(diagnostic.counters.poisonCause, 'mux_pipe_buffer_limit')
  assert.equal(diagnostic.bufferedBytes, 0)
  assert.equal(diagnostic.counters.peakBufferedBytes, 0)
}

// Every possible two-chunk split of one mux packet must reconstruct the exact
// packet. This covers splits inside both uint32 prefixes, header and payload.
const splitWire = runtimeWire({ payload: Buffer.from('split-boundary-payload', 'utf8') })
for (let split = 1; split < splitWire.length; split += 1) {
  const socket = new FakeSocket()
  const target = carrier(socket)
  const pending = target.readNext({ partialFrameTimeoutMs: 2_000 })
  socket.push(splitWire.subarray(0, split))
  socket.push(splitWire.subarray(split))
  const packet = await pending
  assert.equal(packet.header.message_id, 'runtime-message-1')
  assert.equal(packet.payload.toString('utf8'), 'split-boundary-payload')
  target.settleRead('accepted')
  target.close()
}

// Short one-byte chunks are aggregated without quadratic concatenation.
{
  const socket = new FakeSocket()
  const target = carrier(socket)
  const wire = runtimeWire({ payload: Buffer.alloc(1024, 0x61) })
  const pending = target.readNext({ partialFrameTimeoutMs: 2_000 })
  for (const byte of wire) socket.push(Buffer.from([byte]))
  const packet = await pending
  assert.equal(packet.payload.length, 1024)
  target.settleRead('accepted')
  assert.ok(target.diagnosticSnapshot().counters.peakPartialFrameBytes >= wire.length)
  target.close()
}

// The adapter also runs over actual connected net.Socket instances on the
// Windows Named Pipe transport. The probe listener is test-only; production
// pipe creation and ACL ownership remain on the Python host.
let actualNamedPipe = false
if (process.platform === 'win32') {
  const pipeName = `\\\\.\\pipe\\genshin-native-mux-client-probe-${process.pid}-${randomUUID().replaceAll('-', '')}`
  const server = createServer()
  server.listen(pipeName)
  await once(server, 'listening')
  const accepted = once(server, 'connection')
  const runtimeSocket = connectNet(pipeName)
  await once(runtimeSocket, 'connect')
  const [hostSocket] = await accepted
  const host = carrier(hostSocket)
  const runtime = new NativeMuxPipeClient(runtimeSocket, {
    senderRole: 'runtime',
    peerSenderRole: 'host',
    ...CONNECTION,
  })
  host.enqueue(outbound({ messageId: 'actual-pipe-host' }))
  const inbound = runtime.readNext({ partialFrameTimeoutMs: 2_000 })
  await host.writeNext({ timeoutMs: 2_000 })
  assert.equal((await inbound).header.message_id, 'actual-pipe-host')
  runtime.settleRead('accepted')

  runtime.enqueue({
    ...outbound({ messageId: 'actual-pipe-runtime' }),
    payload: Buffer.from('runtime-response', 'utf8'),
  })
  const response = host.readNext({ partialFrameTimeoutMs: 2_000 })
  await runtime.writeNext({ timeoutMs: 2_000 })
  assert.equal((await response).payload.toString('utf8'), 'runtime-response')
  host.settleRead('accepted')
  host.close()
  runtime.close()
  await new Promise(resolve => server.close(resolve))
  actualNamedPipe = true
}

// A single socket chunk may contain more than one mux frame; the unused bytes
// remain framed for the next read.
{
  const socket = new FakeSocket()
  const target = carrier(socket)
  const first = runtimeWire({ globalSeq: 1, laneSeq: 1, messageId: 'multi-1' })
  const second = runtimeWire({ globalSeq: 2, laneSeq: 2, messageId: 'multi-2' })
  const firstPending = target.readNext()
  socket.push(Buffer.concat([first, second]))
  assert.equal((await firstPending).header.message_id, 'multi-1')
  target.settleRead('accepted')
  assert.equal((await target.readNext()).header.message_id, 'multi-2')
  target.settleRead('accepted')
  assert.equal(target.diagnosticSnapshot().counters.framedFrames, 2)
  target.close()
}

// A partial next frame coalesced behind an unsettled prior frame must not spend
// its absolute deadline while local delivery backpressure intentionally pauses
// the socket.
{
  const socket = new FakeSocket()
  const target = carrier(socket)
  const first = runtimeWire({ globalSeq: 1, laneSeq: 1, messageId: 'held-1' })
  const second = runtimeWire({ globalSeq: 2, laneSeq: 2, messageId: 'held-2' })
  const firstPending = target.readNext({ partialFrameTimeoutMs: 30 })
  socket.push(Buffer.concat([first, second.subarray(0, 6)]))
  await firstPending
  await new Promise(resolve => setTimeout(resolve, 50))
  target.settleRead('accepted')
  const secondPending = target.readNext({ partialFrameTimeoutMs: 30 })
  socket.push(second.subarray(6))
  assert.equal((await secondPending).header.message_id, 'held-2')
  target.settleRead('accepted')
  target.close()
}

// Oversized prefixes are rejected before a body allocation occurs.
{
  const socket = new FakeSocket()
  const target = carrier(socket)
  const prefix = Buffer.alloc(4)
  prefix.writeUInt32BE(MAX_MUX_BODY_BYTES + 1)
  const pending = target.readNext()
  socket.push(prefix)
  await rejects('mux_pipe_body_too_large', () => pending, 'mux_packet_too_large')
  const diagnostic = target.diagnosticSnapshot()
  assert.equal(diagnostic.counters.bodyAllocations, 0)
  assert.equal(diagnostic.poisoned, true)
}

// A complete final frame buffered before EOF remains deliverable. Clean and
// genuinely partial EOF are distinct and poison only when the reader needs
// bytes which can no longer arrive.
{
  const socket = new FakeSocket()
  const target = carrier(socket)
  target.enqueue(outbound({
    lane: 'control',
    messageId: 'remote-close-unsent',
    payload: Buffer.alloc(1024 * 1024, 0x65),
    trustedClass: 'control',
  }))
  const pending = target.readNext()
  socket.push(splitWire)
  socket.remoteClose()
  const packet = await pending
  assert.equal(packet.header.message_id, 'runtime-message-1')
  const closedWithFinalFrame = target.diagnosticSnapshot()
  assert.equal(closedWithFinalFrame.deliveryPending, true)
  assert.equal(closedWithFinalFrame.queue.currentItems, 0)
  assert.equal(closedWithFinalFrame.queue.currentPayloadBytes, 0)
  assert.equal(closedWithFinalFrame.queue.currentWireBytes, 0)
  assert.equal(closedWithFinalFrame.lanes.control.queuedItems, 0)
  assert.equal(closedWithFinalFrame.lanes.control.queuedPayloadBytes, 0)
  target.settleRead('accepted')
  await rejects('mux_pipe_unexpected_eof', () => target.readNext())
}

{
  const cleanSocket = new FakeSocket()
  const clean = carrier(cleanSocket)
  const cleanPending = clean.readNext()
  cleanSocket.remoteEnd()
  await rejects('mux_pipe_unexpected_eof', () => cleanPending)

  const partialSocket = new FakeSocket()
  const partial = carrier(partialSocket)
  const partialPending = partial.readNext()
  partialSocket.push(splitWire.subarray(0, 6))
  partialSocket.remoteEnd()
  await rejects('mux_pipe_truncated', () => partialPending)
  assert.ok(partial.diagnosticSnapshot().counters.partialFrameBytesAtFailure > 0)
}

// Continuous tiny progress cannot refresh the absolute partial-frame deadline.
{
  const socket = new FakeSocket()
  const target = carrier(socket)
  const wire = runtimeWire({ payload: Buffer.alloc(256, 0x62) })
  const startedAt = performance.now()
  const pending = target.readNext({ partialFrameTimeoutMs: 45 })
  let offset = 0
  const ticker = setInterval(() => {
    if (offset < wire.length) socket.push(wire.subarray(offset, offset += 1))
  }, 8)
  await rejects('mux_pipe_partial_frame_timeout', () => pending)
  clearInterval(ticker)
  assert.ok(performance.now() - startedAt < 300)
  assert.equal(target.diagnosticSnapshot().poisoned, true)
}

// A no-byte idle timeout consumes no frame and is safely retryable; it is a
// different clock from an in-progress partial frame.
{
  const socket = new FakeSocket()
  const target = carrier(socket)
  const keepAlive = setTimeout(() => {}, 500)
  await rejects(
    'mux_pipe_read_idle_timeout',
    () => target.readNext({ idleTimeoutMs: 25 }),
  )
  clearTimeout(keepAlive)
  assert.equal(target.poisoned, false)
  const retry = target.readNext()
  socket.push(runtimeWire())
  await retry
  target.settleRead('accepted')
  target.close()
}

// Only one physical read may be in flight.
{
  const socket = new FakeSocket()
  const target = carrier(socket)
  const pending = target.readNext()
  await rejects('mux_pipe_reader_busy', () => target.readNext())
  socket.push(runtimeWire())
  await pending
  target.settleRead('accepted')
  target.close()
}

// not_delivered retains and re-presents the identical wire before consuming a
// later frame from the socket.
{
  const socket = new FakeSocket()
  const target = carrier(socket)
  const first = runtimeWire({ globalSeq: 1, laneSeq: 1, messageId: 'retry-1' })
  const second = runtimeWire({ globalSeq: 2, laneSeq: 2, messageId: 'retry-2' })
  const pending = target.readNext()
  socket.push(Buffer.concat([first, second]))
  const firstPacket = await pending
  target.settleRead('not_delivered')
  assert.equal(target.diagnosticSnapshot().redeliveryPending, true)
  const retried = await target.readNext()
  assert.deepEqual(retried.header, firstPacket.header)
  assert.deepEqual(retried.payload, firstPacket.payload)
  target.settleRead('accepted')
  assert.equal((await target.readNext()).header.message_id, 'retry-2')
  target.settleRead('accepted')
  const diagnostic = target.diagnosticSnapshot()
  assert.equal(diagnostic.counters.notDelivered, 1)
  assert.equal(diagnostic.counters.redeliveries, 1)
  target.close()
}

// Receiver validation failures poison both mux state machines and the socket.
{
  const socket = new FakeSocket()
  const target = carrier(socket)
  const pending = target.readNext()
  socket.push(runtimeWire({ connectionId: 'wrong-connection' }))
  await rejects(
    'mux_pipe_receive_invalid', () => pending, 'mux_connection_id_mismatch',
  )
  assert.equal(socket.destroyed, true)
  await rejects('mux_pipe_receive_invalid', async () => target.readNext())
}

// Backpressure is not a failed write; callback completion settles it as sent.
{
  const socket = new FakeSocket()
  socket.writeImpl = (_bytes, callback) => {
    setImmediate(callback)
    return false
  }
  const target = carrier(socket)
  target.enqueue(outbound())
  const sent = await target.writeNext({ timeoutMs: 2_000 })
  assert.equal(sent.messageId, 'host-message-1')
  const diagnostic = target.diagnosticSnapshot()
  assert.equal(diagnostic.counters.backpressureWrites, 1)
  assert.equal(diagnostic.counters.sentFrames, 1)
  assert.equal(diagnostic.queue.currentItems, 0)
  target.close()
}

// Once beginWrite has occurred, a synchronous throw or connection close is an
// outcome-unknown failure. A callback timeout has the same semantics. The frame
// is never requeued.
{
  const throwSocket = new FakeSocket()
  throwSocket.writeImpl = () => { throw new Error('sync-write') }
  const throwing = carrier(throwSocket)
  throwing.enqueue(outbound({ messageId: 'sync-throw' }))
  await rejects(
    'mux_pipe_write_outcome_unknown', () => throwing.writeNext(), 'mux_pipe_write_failed',
  )
  assert.equal(throwing.diagnosticSnapshot().queue.currentItems, 0)

  const closeSocket = new FakeSocket()
  closeSocket.writeImpl = () => {
    queueMicrotask(() => closeSocket.remoteClose())
    return true
  }
  const closing = carrier(closeSocket)
  closing.enqueue(outbound({ messageId: 'close-before-callback' }))
  await rejects(
    'mux_pipe_write_outcome_unknown', () => closing.writeNext(), 'mux_pipe_unexpected_close',
  )
  assert.equal(closing.poisoned, true)

  const timeoutSocket = new FakeSocket()
  timeoutSocket.writeImpl = () => true
  const timingOut = carrier(timeoutSocket)
  timingOut.enqueue(outbound({ messageId: 'write-timeout' }))
  const keepAlive = setTimeout(() => {}, 500)
  await rejects(
    'mux_pipe_write_outcome_unknown',
    () => timingOut.writeNext({ timeoutMs: 25 }),
    'mux_pipe_write_timeout',
  )
  clearTimeout(keepAlive)
  assert.equal(timingOut.diagnosticSnapshot().queue.currentItems, 0)
}

// Only one physical writer may be active.
{
  const socket = new FakeSocket()
  let release
  socket.writeImpl = (_bytes, callback) => {
    release = callback
    return true
  }
  const target = carrier(socket)
  target.enqueue(outbound({ messageId: 'writer-1' }))
  target.enqueue(outbound({ messageId: 'writer-2' }))
  const first = target.writeNext({ timeoutMs: 2_000 })
  await rejects('mux_pipe_writer_busy', () => target.writeNext())
  release()
  await first
  socket.writeImpl = null
  await target.writeNext({ timeoutMs: 2_000 })
  target.close()
}

// Queue, lane, framing and accepted-delivery high-water telemetry is exposed
// without granting production authority.
{
  const socket = new FakeSocket()
  const target = carrier(socket)
  target.enqueue(outbound({
    lane: 'control', messageId: 'telemetry-control',
    payload: Buffer.alloc(32), trustedClass: 'control',
  }))
  target.enqueue(outbound({
    lane: 'bundle', messageId: 'telemetry-bundle', payload: Buffer.alloc(64),
  }))
  const queued = target.diagnosticSnapshot()
  assert.equal(queued.productionOwner, false)
  assert.equal(queued.queue.currentItems, 2)
  assert.equal(queued.queue.currentPayloadBytes, 96)
  assert.ok(queued.queue.peakWireBytes > 96)
  assert.equal(queued.lanes.control.peakQueuedItems, 1)
  assert.equal(queued.lanes.bundle.peakQueuedPayloadBytes, 64)
  await target.writeNext()
  await target.writeNext()

  const pending = target.readNext()
  socket.push(runtimeWire({
    lane: 'control', laneSeq: 1, messageId: 'telemetry-inbound',
  }))
  await pending
  target.settleRead('accepted')
  const final = target.diagnosticSnapshot()
  assert.equal(final.counters.sentFrames, 2)
  assert.equal(final.counters.acceptedFrames, 1)
  assert.equal(final.lanes.control.sentFrames, 1)
  assert.equal(final.lanes.bundle.sentFrames, 1)
  assert.equal(final.lanes.control.acceptedFrames, 1)
  assert.ok(final.counters.readWaits >= 1)
  assert.ok(final.counters.writeWaits >= 2)
  target.close()
}

// Closing is a memory-ownership boundary. Queued outbound payloads and an
// exact inbound redelivery wire must not remain retained after the transport
// becomes unusable.
{
  const socket = new FakeSocket()
  const target = carrier(socket)
  target.enqueue(outbound({
    lane: 'control',
    messageId: 'close-queued-control',
    payload: Buffer.alloc(1024 * 1024, 0x63),
    trustedClass: 'control',
  }))
  target.enqueue(outbound({
    messageId: 'close-queued-turn',
    payload: Buffer.alloc(1024 * 1024, 0x64),
  }))
  assert.equal(target.diagnosticSnapshot().queue.currentItems, 2)
  target.close()
  const closed = target.diagnosticSnapshot()
  assert.equal(closed.closed, true)
  assert.equal(closed.poisoned, false)
  assert.equal(closed.queue.currentItems, 0)
  assert.equal(closed.queue.currentPayloadBytes, 0)
  assert.equal(closed.queue.currentWireBytes, 0)
  assert.equal(closed.lanes.control.queuedItems, 0)
  assert.equal(closed.lanes.legacy_turn.queuedItems, 0)
}

{
  const socket = new FakeSocket()
  const target = carrier(socket)
  const pending = target.readNext()
  socket.push(runtimeWire({ messageId: 'close-redelivery' }))
  await pending
  target.settleRead('not_delivered')
  assert.equal(target.diagnosticSnapshot().redeliveryPending, true)
  target.close()
  const closed = target.diagnosticSnapshot()
  assert.equal(closed.poisoned, false)
  assert.equal(closed.deliveryPending, false)
  assert.equal(closed.redeliveryPending, false)
}

// A local close or socket error while a delivery lease is outstanding poisons
// the lease and releases its full wire/packet reference. A clean peer close is
// different: the already delivered complete final frame remains settleable,
// as exercised above.
{
  const socket = new FakeSocket()
  const target = carrier(socket)
  const pending = target.readNext()
  socket.push(runtimeWire({ messageId: 'close-active-delivery' }))
  await pending
  assert.equal(target.diagnosticSnapshot().deliveryPending, true)
  target.close()
  const closed = target.diagnosticSnapshot()
  assert.equal(closed.poisoned, true)
  assert.equal(closed.counters.poisonCause, 'mux_pipe_close_during_io')
  assert.equal(closed.deliveryPending, false)
  assert.equal(closed.redeliveryPending, false)
}

{
  const socket = new FakeSocket()
  const target = carrier(socket)
  const pending = target.readNext()
  socket.push(runtimeWire({ messageId: 'error-active-delivery' }))
  await pending
  const reset = new Error('peer reset')
  reset.code = 'ECONNRESET'
  socket.emit('error', reset)
  const failed = target.diagnosticSnapshot()
  assert.equal(failed.poisoned, true)
  assert.equal(failed.counters.poisonCause, 'mux_pipe_socket_error')
  assert.equal(failed.counters.poisonCauseCode, 'ECONNRESET')
  assert.equal(failed.deliveryPending, false)
  assert.equal(failed.redeliveryPending, false)
}

assert.equal(PRODUCTION_OWNER, false)
process.stdout.write(`${JSON.stringify({
  contract: 'genshin-native-mux-pipe-client-probe-v1',
  status: 'passed',
  productionOwner: PRODUCTION_OWNER,
  splitPoints: splitWire.length - 1,
  actualNamedPipe,
})}\n`)
