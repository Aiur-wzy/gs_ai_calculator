/** Cross-language fixture and adversarial checks for the in-memory mux core. */
import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'

import { decodeMuxPacket, encodeMuxPacket, makeMuxHeader } from './native-mux-protocol.mjs'
import {
  DEFAULT_CONNECTION_MAX_BYTES,
  DEFAULT_CONNECTION_MAX_FRAMES,
  DEFAULT_TOTAL_MAX_BYTES,
  DEFAULT_TOTAL_MAX_ITEMS,
  HARD_MAX_QUEUE_BYTES,
  HARD_MAX_QUEUE_ITEMS,
  NativeMuxReceiver,
  NativeMuxSendQueue,
  NativePipeMultiplexerError,
  OBSERVED_GUI_PROJECTION_HIGH_WATER,
  OBSERVED_MUX_QUEUE_HIGH_WATER_BYTES,
  OBSERVED_MUX_QUEUE_HIGH_WATER_ITEMS,
  REFERENCE_BRIDGE_MAX_BYTES,
  REFERENCE_BRIDGE_MAX_FRAMES,
} from './native-pipe-multiplexer.mjs'

const fixture = JSON.parse(await readFile(
  new URL('../native-pipe-mux-core-v1-vectors.json', import.meta.url),
  'utf8',
))

function rejects(code, operation) {
  let caught
  try {
    operation()
  } catch (error) {
    caught = error
  }
  assert.ok(caught instanceof NativePipeMultiplexerError)
  assert.equal(caught.code, code)
  return caught
}

function queue(overrides = {}) {
  return new NativeMuxSendQueue({
    senderRole: 'host',
    connectionGeneration: 1,
    connectionId: 'connection-1',
    runtimeIncarnation: 'runtime-1',
    ...overrides,
  })
}

function receiver(overrides = {}) {
  return new NativeMuxReceiver({
    senderRole: 'host',
    connectionGeneration: 1,
    connectionId: 'connection-1',
    runtimeIncarnation: 'runtime-1',
    ...overrides,
  })
}

function enqueue(target, row) {
  target.enqueue({
    lane: row.lane,
    payloadKind: row.payload_kind,
    payload: Buffer.from(row.payload, 'utf8'),
    messageId: row.message_id,
    correlationId: row.correlation_id ?? null,
    streamId: row.stream_id ?? null,
    streamSeq: row.stream_seq ?? null,
    trustedClass: row.trusted_class,
  })
}

function sendOne(target) {
  const prepared = target.prepareNext()
  assert.ok(prepared)
  const packet = decodeMuxPacket(prepared.wire)
  target.beginWrite()
  target.settleWrite('sent')
  return { wire: prepared.wire, header: packet.header, payload: packet.payload }
}

function wire({
  globalSeq,
  laneSeq,
  messageId,
  lane = 'legacy_turn',
  connectionId = 'connection-1',
  streamId = null,
  streamSeq = null,
}) {
  const payload = Buffer.from(`payload:${messageId}`, 'utf8')
  const header = makeMuxHeader({
    lane,
    sender_role: 'host',
    connection_generation: 1,
    connection_id: connectionId,
    runtime_incarnation: 'runtime-1',
    global_seq: globalSeq,
    lane_seq: laneSeq,
    message_id: messageId,
    correlation_id: null,
    stream_id: streamId,
    stream_seq: streamSeq,
    payload_kind: lane === 'legacy_turn' ? 'legacy_frame' : 'stream_data',
    payload,
    priority: 'control',
  })
  return encodeMuxPacket(header, payload)
}

assert.equal(OBSERVED_MUX_QUEUE_HIGH_WATER_ITEMS, null)
assert.equal(OBSERVED_MUX_QUEUE_HIGH_WATER_BYTES, null)
assert.equal(OBSERVED_GUI_PROJECTION_HIGH_WATER, 22_187)
assert.equal(DEFAULT_TOTAL_MAX_ITEMS, fixture.capacityEvidence.defaultQueueItems)
assert.equal(DEFAULT_TOTAL_MAX_BYTES, fixture.capacityEvidence.defaultQueueBytes)
assert.equal(DEFAULT_CONNECTION_MAX_FRAMES, REFERENCE_BRIDGE_MAX_FRAMES)
assert.equal(DEFAULT_CONNECTION_MAX_BYTES, REFERENCE_BRIDGE_MAX_BYTES)
assert.equal(HARD_MAX_QUEUE_ITEMS, DEFAULT_CONNECTION_MAX_FRAMES)
assert.equal(HARD_MAX_QUEUE_BYTES, DEFAULT_CONNECTION_MAX_BYTES)
assert.equal(HARD_MAX_QUEUE_ITEMS, fixture.capacityEvidence.hardSafetyItems)
assert.equal(HARD_MAX_QUEUE_BYTES, fixture.capacityEvidence.hardSafetyBytes)

const fixtureQueue = queue({
  priorityBurst: fixture.scheduler.priority_burst,
  drrQuantumBytes: fixture.scheduler.drr_quantum_bytes,
})
for (const row of fixture.messages) enqueue(fixtureQueue, row)
const fixtureSummary = []
const fixtureWires = []
while (fixtureQueue.queuedItems > 0) {
  const sent = sendOne(fixtureQueue)
  fixtureWires.push(sent.wire)
  fixtureSummary.push({
    message_id: sent.header.message_id,
    lane: sent.header.lane,
    global_seq: sent.header.global_seq,
    lane_seq: sent.header.lane_seq,
    priority: sent.header.priority,
    payload_hex: sent.payload.toString('hex'),
  })
}
assert.deepEqual(fixtureSummary, fixture.expected)

const fixtureReceiver = receiver()
for (const packet of fixtureWires) {
  fixtureReceiver.prepareWire(packet)
  fixtureReceiver.settleRead('accepted')
}

const lease = queue()
enqueue(lease, {
  lane: 'legacy_turn', payload_kind: 'legacy_frame', payload: 'lease',
  message_id: 'lease-1', trusted_class: 'normal',
})
const firstPrepared = lease.prepareNext()
rejects('mux_writer_already_reserved', () => lease.prepareNext())
lease.settleWrite('not_sent')
const secondPrepared = lease.prepareNext()
assert.deepEqual(secondPrepared, firstPrepared)
lease.beginWrite()
lease.settleWrite('sent')
assert.equal(lease.queuedItems, 0)

const ambiguous = queue()
enqueue(ambiguous, {
  lane: 'legacy_turn', payload_kind: 'legacy_frame', payload: 'ambiguous',
  message_id: 'ambiguous-1', trusted_class: 'normal',
})
ambiguous.prepareNext()
ambiguous.beginWrite()
ambiguous.settleWrite('outcome_unknown')
assert.equal(ambiguous.poisoned, true)
assert.equal(ambiguous.retainedMessageIds, 0)
rejects('mux_connection_poisoned', () => ambiguous.prepareNext())

const fifo = queue({ priorityBurst: 1, drrQuantumBytes: 4 })
enqueue(fifo, {
  lane: 'legacy_turn', payload_kind: 'legacy_frame', payload: 'normal',
  message_id: 'fifo-normal', trusted_class: 'normal',
})
enqueue(fifo, {
  lane: 'legacy_turn', payload_kind: 'legacy_frame', payload: 'urgent',
  message_id: 'fifo-urgent', trusted_class: 'urgent',
})
assert.equal(sendOne(fifo).header.message_id, 'fifo-normal')
assert.equal(sendOne(fifo).header.message_id, 'fifo-urgent')

const tooSmall = queue({ connectionMaxBytes: 100 })
rejects('mux_connection_byte_limit', () => enqueue(tooSmall, {
  lane: 'control', payload_kind: 'control', payload: 'x',
  message_id: 'too-large-for-connection', trusted_class: 'control',
}))
assert.equal(tooSmall.queuedItems, 0)
assert.equal(tooSmall.retainedMessageIds, 0)
assert.equal(tooSmall.prepareNext(), null)

const criticalRows = [
  {
    lane: 'legacy_turn', payload_kind: 'legacy_frame', payload: 'first'.repeat(20),
    message_id: 'critical-first', trusted_class: 'normal',
  },
  {
    lane: 'bundle', payload_kind: 'bundle_chunk', payload: 'second'.repeat(20),
    message_id: 'critical-second', trusted_class: 'normal',
  },
]
const sizing = queue()
for (const row of criticalRows) enqueue(sizing, row)
const exactCapacity = sizing.reservedConnectionBytes
const critical = queue({ connectionMaxBytes: exactCapacity })
for (const row of criticalRows) enqueue(critical, row)
assert.equal(critical.reservedConnectionBytes, exactCapacity)
rejects('mux_connection_byte_limit', () => enqueue(critical, {
  ...criticalRows[0], message_id: 'critical-third',
}))
const criticalFirst = critical.prepareNext()
critical.settleWrite('not_sent')
assert.equal(critical.reservedConnectionBytes, exactCapacity)
assert.deepEqual(critical.prepareNext(), criticalFirst)
critical.beginWrite()
critical.settleWrite('sent')
assert.ok(critical.reservedConnectionBytes <= exactCapacity)
sendOne(critical)
assert.equal(critical.queuedItems, 0)

const legacyBytes = Buffer.from([0, 0xff, 0x80, 0x7f])
const opaque = queue()
opaque.enqueue({
  lane: 'legacy_turn', payloadKind: 'legacy_frame', payload: legacyBytes,
  messageId: 'opaque-legacy', trustedClass: 'normal',
})
assert.deepEqual(sendOne(opaque).payload, legacyBytes)

const delivery = receiver()
const deliveryWire = wire({ globalSeq: 1, laneSeq: 1, messageId: 'delivery-1' })
delivery.prepareWire(deliveryWire)
delivery.settleRead('not_delivered')
delivery.prepareWire(deliveryWire)
delivery.settleRead('accepted')
rejects('mux_global_sequence_mismatch', () => delivery.prepareWire(deliveryWire))
assert.equal(delivery.poisoned, true)

const cross = receiver()
rejects('mux_connection_id_mismatch', () => cross.prepareWire(wire({
  globalSeq: 1,
  laneSeq: 1,
  messageId: 'cross-1',
  connectionId: 'connection-other',
})))
assert.equal(cross.poisoned, true)

const stream = receiver({ maxStreams: 2 })
stream.prepareWire(wire({
  globalSeq: 1, laneSeq: 1, messageId: 'stream-message-1',
  lane: 'remote_stream', streamId: 'stream-1', streamSeq: 1,
}))
stream.settleRead('accepted')
assert.equal(stream.retainedMessageIds, 1)
assert.equal(stream.trackedStreams, 1)
rejects('mux_stream_sequence_mismatch', () => stream.prepareWire(wire({
  globalSeq: 2, laneSeq: 2, messageId: 'stream-message-2',
  lane: 'remote_stream', streamId: 'stream-1', streamSeq: 3,
})))
assert.equal(stream.poisoned, true)
assert.equal(stream.retainedMessageIds, 0)
assert.equal(stream.trackedStreams, 0)

process.stdout.write(`${JSON.stringify({
  contract: fixture.contract,
  status: 'passed',
  messages: fixture.messages.length,
  productionOwner: false,
})}\n`)
