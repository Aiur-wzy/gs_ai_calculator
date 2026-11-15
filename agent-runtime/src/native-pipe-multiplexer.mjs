/**
 * Transport-free state machines for the native pipe mux.
 *
 * This module owns no pipe handle and starts no reader or writer. Wire
 * priority is diagnostic metadata only; effective scheduling priority is
 * derived exclusively from the local trusted scheduling class.
 *
 * It is not a production carrier. A future carrier must perform bounded exact
 * reads/writes over the raw pipe, must not nest this 128 MiB mux frame inside
 * the legacy 1 MiB reader, and must freeze one post-handshake codec without
 * mid-stream fallback.
 */
import {
  JSON_SAFE_INTEGER_MAX,
  MAX_MUX_PAYLOAD_BYTES,
  MUX_LANES,
  NativeMuxProtocolError,
  decodeMuxPacket,
  encodeMuxPacket,
  makeMuxHeader,
} from './native-mux-protocol.mjs'

export const MUX_LANE_ORDER = Object.freeze([
  'control',
  'legacy_turn',
  'remote_unary',
  'remote_stream',
  'bundle',
])
if (JSON.stringify([...MUX_LANES].sort()) !== JSON.stringify([...MUX_LANE_ORDER].sort())) {
  throw new Error('native_mux_lane_contract_mismatch')
}

export const TRUSTED_SCHEDULING_CLASSES = Object.freeze(['normal', 'urgent', 'control'])
const TRUSTED_CLASS_SET = new Set(TRUSTED_SCHEDULING_CLASSES)
const WIRE_PRIORITY = Object.freeze({ normal: 'normal', urgent: 'urgent', control: 'control' })

// DSH rc.5 has no native Named-Pipe lane queue and this mux has no measured
// queue high-water yet. The App-owned transient queue uses the accepted GUI
// ring envelope and is intentionally smaller than the cumulative bridge
// connection allowance. Larger logical DSH values must later use
// stream/resource chunking rather than widening the 128 MiB packet codec.
export const OBSERVED_MUX_QUEUE_HIGH_WATER_ITEMS = null
export const OBSERVED_MUX_QUEUE_HIGH_WATER_BYTES = null
export const OBSERVED_GUI_PROJECTION_HIGH_WATER = 22_187
export const REFERENCE_BRIDGE_MAX_FRAMES = 524_288
export const REFERENCE_BRIDGE_MAX_BYTES = 2 * 1024 * 1024 * 1024
export const DEFAULT_LANE_MAX_ITEMS = 131_072
export const DEFAULT_LANE_MAX_BYTES = 512 * 1024 * 1024
export const DEFAULT_TOTAL_MAX_ITEMS = 131_072
export const DEFAULT_TOTAL_MAX_BYTES = 512 * 1024 * 1024
export const DEFAULT_CONNECTION_MAX_FRAMES = REFERENCE_BRIDGE_MAX_FRAMES
export const DEFAULT_CONNECTION_MAX_BYTES = REFERENCE_BRIDGE_MAX_BYTES
export const HARD_MAX_QUEUE_ITEMS = REFERENCE_BRIDGE_MAX_FRAMES
export const HARD_MAX_QUEUE_BYTES = REFERENCE_BRIDGE_MAX_BYTES
export const DEFAULT_DRR_QUANTUM_BYTES = 64 * 1024
export const DEFAULT_PRIORITY_BURST = 8
export const DEFAULT_MAX_STREAMS = DEFAULT_TOTAL_MAX_ITEMS
export const HARD_MAX_STREAMS = HARD_MAX_QUEUE_ITEMS
export const WRITE_OUTCOMES = Object.freeze(['sent', 'not_sent', 'outcome_unknown'])
export const READ_OUTCOMES = Object.freeze(['accepted', 'not_delivered', 'outcome_unknown'])
const WRITE_OUTCOME_SET = new Set(WRITE_OUTCOMES)
const READ_OUTCOME_SET = new Set(READ_OUTCOMES)
const MUX_WIRE_PREFIX_BYTES = 8 // uint32 body length + uint32 header length

export class NativePipeMultiplexerError extends Error {
  constructor(code) {
    super(code)
    this.name = 'NativePipeMultiplexerError'
    this.code = code
  }
}

function reject(code) {
  throw new NativePipeMultiplexerError(code)
}

function positive(value, code) {
  if (!Number.isSafeInteger(value) || value < 1) reject(code)
  return value
}

function ownedPayload(payload) {
  if (!Buffer.isBuffer(payload) && !(payload instanceof Uint8Array)) {
    reject('invalid_mux_payload')
  }
  if (payload.byteLength > MAX_MUX_PAYLOAD_BYTES) reject('mux_payload_too_large')
  return Buffer.from(payload)
}

class LaneQueue {
  #items = []
  #head = 0

  get length() {
    return this.#items.length - this.#head
  }

  peek() {
    return this.length === 0 ? undefined : this.#items[this.#head]
  }

  push(value) {
    this.#items.push(value)
  }

  shift() {
    if (this.length === 0) return undefined
    const value = this.#items[this.#head]
    this.#head += 1
    if (this.#head >= 1024 && this.#head * 2 >= this.#items.length) {
      this.#items = this.#items.slice(this.#head)
      this.#head = 0
    }
    return value
  }

  clear() {
    this.#items = []
    this.#head = 0
  }
}

function blankLaneNumbers(value) {
  return Object.fromEntries(MUX_LANE_ORDER.map(lane => [lane, value]))
}

function copySchedule(state) {
  return {
    drrCursor: state.drrCursor,
    priorityCursor: state.priorityCursor,
    priorityBurst: state.priorityBurst,
    deficits: { ...state.deficits },
  }
}

export class NativeMuxSendQueue {
  #senderRole
  #connectionGeneration
  #connectionId
  #runtimeIncarnation
  #laneMaxItems
  #laneMaxBytes
  #totalMaxItems
  #totalMaxBytes
  #connectionMaxFrames
  #connectionMaxBytes
  #quantum
  #burstLimit
  #queues
  #laneBytes
  #totalItems = 0
  #totalBytes = 0
  #queuedWireBytes = 0
  #nextGlobalSeq = 1
  #nextLaneSeq = blankLaneNumbers(1)
  #schedule
  #inflight = null
  #sentFrames = 0
  #sentBytes = 0
  #messageIds = new Set()
  #poisoned = false

  constructor({
    senderRole,
    connectionGeneration,
    connectionId,
    runtimeIncarnation,
    laneMaxItems = DEFAULT_LANE_MAX_ITEMS,
    laneMaxBytes = DEFAULT_LANE_MAX_BYTES,
    totalMaxItems = DEFAULT_TOTAL_MAX_ITEMS,
    totalMaxBytes = DEFAULT_TOTAL_MAX_BYTES,
    drrQuantumBytes = DEFAULT_DRR_QUANTUM_BYTES,
    priorityBurst = DEFAULT_PRIORITY_BURST,
    connectionMaxFrames = DEFAULT_CONNECTION_MAX_FRAMES,
    connectionMaxBytes = DEFAULT_CONNECTION_MAX_BYTES,
  }) {
    this.#laneMaxItems = positive(laneMaxItems, 'invalid_lane_item_limit')
    this.#laneMaxBytes = positive(laneMaxBytes, 'invalid_lane_byte_limit')
    this.#totalMaxItems = positive(totalMaxItems, 'invalid_total_item_limit')
    this.#totalMaxBytes = positive(totalMaxBytes, 'invalid_total_byte_limit')
    this.#quantum = positive(drrQuantumBytes, 'invalid_drr_quantum')
    this.#burstLimit = positive(priorityBurst, 'invalid_priority_burst')
    this.#connectionMaxFrames = positive(connectionMaxFrames, 'invalid_connection_frame_limit')
    this.#connectionMaxBytes = positive(connectionMaxBytes, 'invalid_connection_byte_limit')
    if (this.#laneMaxItems > HARD_MAX_QUEUE_ITEMS
        || this.#laneMaxBytes > HARD_MAX_QUEUE_BYTES
        || this.#totalMaxItems > HARD_MAX_QUEUE_ITEMS
        || this.#totalMaxBytes > HARD_MAX_QUEUE_BYTES
        || this.#connectionMaxFrames > HARD_MAX_QUEUE_ITEMS
        || this.#connectionMaxBytes > HARD_MAX_QUEUE_BYTES) {
      reject('mux_queue_limit_too_large')
    }

    makeMuxHeader({
      lane: 'control',
      sender_role: senderRole,
      connection_generation: connectionGeneration,
      connection_id: connectionId,
      runtime_incarnation: runtimeIncarnation,
      global_seq: 1,
      lane_seq: 1,
      message_id: 'mux-constructor-check',
      correlation_id: null,
      stream_id: null,
      stream_seq: null,
      payload_kind: 'control',
      payload: Buffer.alloc(0),
      priority: 'control',
    })
    this.#senderRole = senderRole
    this.#connectionGeneration = connectionGeneration
    this.#connectionId = connectionId
    this.#runtimeIncarnation = runtimeIncarnation
    this.#queues = Object.fromEntries(MUX_LANE_ORDER.map(lane => [lane, new LaneQueue()]))
    this.#laneBytes = blankLaneNumbers(0)
    this.#schedule = {
      drrCursor: 0,
      priorityCursor: 0,
      priorityBurst: 0,
      deficits: blankLaneNumbers(0),
    }
  }

  get queuedItems() { return this.#totalItems }
  get queuedBytes() { return this.#totalBytes }
  get poisoned() { return this.#poisoned }
  get retainedMessageIds() { return this.#messageIds.size }
  get reservedConnectionBytes() { return this.#sentBytes + this.#queuedWireBytes }

  #poisonAndClear() {
    this.#poisoned = true
    this.#inflight = null
    for (const queue of Object.values(this.#queues)) queue.clear()
    this.#laneBytes = blankLaneNumbers(0)
    this.#totalItems = 0
    this.#totalBytes = 0
    this.#queuedWireBytes = 0
    this.#messageIds.clear()
  }

  poison() {
    this.#poisonAndClear()
  }

  enqueue({
    lane,
    payloadKind,
    payload,
    messageId,
    correlationId = null,
    streamId = null,
    streamSeq = null,
    trustedClass = 'normal',
  }) {
    if (this.#poisoned) reject('mux_connection_poisoned')
    if (!MUX_LANES.includes(lane)) reject('unknown_mux_lane')
    if (!TRUSTED_CLASS_SET.has(trustedClass)) reject('untrusted_scheduling_class')
    const raw = ownedPayload(payload)
    const admissionHeader = makeMuxHeader({
      lane,
      sender_role: this.#senderRole,
      connection_generation: this.#connectionGeneration,
      connection_id: this.#connectionId,
      runtime_incarnation: this.#runtimeIncarnation,
      // Any accepted frame is bounded by this connection's total frame cap.
      // Reserving that digit width remains valid across later lane scheduling.
      global_seq: this.#connectionMaxFrames,
      lane_seq: this.#connectionMaxFrames,
      message_id: messageId,
      correlation_id: correlationId,
      stream_id: streamId,
      stream_seq: streamSeq,
      payload_kind: payloadKind,
      payload: raw,
      priority: WIRE_PRIORITY[trustedClass],
    })
    const reservedWireBytes = MUX_WIRE_PREFIX_BYTES
      + Buffer.byteLength(JSON.stringify(admissionHeader), 'utf8')
      + raw.length
    if (this.#messageIds.has(messageId)) reject('mux_message_id_reused')
    const queue = this.#queues[lane]
    if (queue.length + 1 > this.#laneMaxItems) reject('mux_lane_item_limit')
    if (this.#laneBytes[lane] + raw.length > this.#laneMaxBytes) reject('mux_lane_byte_limit')
    if (this.#totalItems + 1 > this.#totalMaxItems) reject('mux_total_item_limit')
    if (this.#totalBytes + raw.length > this.#totalMaxBytes) reject('mux_total_byte_limit')
    if (this.#sentFrames + this.#totalItems + 1 > this.#connectionMaxFrames) {
      reject('mux_connection_frame_limit')
    }
    if (this.#sentBytes + this.#queuedWireBytes + reservedWireBytes
        > this.#connectionMaxBytes) {
      reject('mux_connection_byte_limit')
    }
    queue.push(Object.freeze({
      lane,
      payloadKind,
      payload: raw,
      messageId,
      correlationId,
      streamId,
      streamSeq,
      trustedClass,
      cost: Math.max(1, raw.length),
      reservedWireBytes,
    }))
    this.#laneBytes[lane] += raw.length
    this.#totalItems += 1
    this.#totalBytes += raw.length
    this.#queuedWireBytes += reservedWireBytes
    this.#messageIds.add(messageId)
  }

  #selectPriority(state) {
    if (state.priorityBurst >= this.#burstLimit) return undefined
    for (const trustedClass of ['control', 'urgent']) {
      for (let offset = 0; offset < MUX_LANE_ORDER.length; offset += 1) {
        const index = (state.priorityCursor + offset) % MUX_LANE_ORDER.length
        const lane = MUX_LANE_ORDER[index]
        const head = this.#queues[lane].peek()
        if (head?.trustedClass === trustedClass) {
          state.priorityCursor = (index + 1) % MUX_LANE_ORDER.length
          state.priorityBurst += 1
          return lane
        }
      }
    }
    return undefined
  }

  #selectDrr(state) {
    const nonempty = MUX_LANE_ORDER.filter(lane => this.#queues[lane].length > 0)
    if (nonempty.length === 0) return undefined
    const rounds = Math.min(...nonempty.map((lane) => {
      const remaining = this.#queues[lane].peek().cost - state.deficits[lane]
      return Math.max(0, Math.ceil(remaining / this.#quantum))
    }))
    if (rounds > 0) {
      for (const lane of nonempty) state.deficits[lane] += rounds * this.#quantum
    }
    for (let offset = 0; offset < MUX_LANE_ORDER.length; offset += 1) {
      const index = (state.drrCursor + offset) % MUX_LANE_ORDER.length
      const lane = MUX_LANE_ORDER[index]
      const head = this.#queues[lane].peek()
      if (head !== undefined && head.cost <= state.deficits[lane]) {
        state.deficits[lane] -= head.cost
        state.drrCursor = (index + 1) % MUX_LANE_ORDER.length
        state.priorityBurst = 0
        return lane
      }
    }
    reject('mux_scheduler_invariant')
  }

  prepareNext() {
    if (this.#poisoned) reject('mux_connection_poisoned')
    if (this.#inflight !== null) reject('mux_writer_already_reserved')
    const proposed = copySchedule(this.#schedule)
    const lane = this.#selectPriority(proposed) ?? this.#selectDrr(proposed)
    if (lane === undefined) return null
    const pending = this.#queues[lane].peek()
    if (this.#nextGlobalSeq > JSON_SAFE_INTEGER_MAX
        || this.#nextLaneSeq[lane] > JSON_SAFE_INTEGER_MAX) {
      reject('mux_sequence_exhausted')
    }
    if (this.#sentFrames + 1 > this.#connectionMaxFrames) {
      reject('mux_connection_frame_limit')
    }
    const globalSeq = this.#nextGlobalSeq
    const laneSeq = this.#nextLaneSeq[lane]
    const header = makeMuxHeader({
      lane,
      sender_role: this.#senderRole,
      connection_generation: this.#connectionGeneration,
      connection_id: this.#connectionId,
      runtime_incarnation: this.#runtimeIncarnation,
      global_seq: globalSeq,
      lane_seq: laneSeq,
      message_id: pending.messageId,
      correlation_id: pending.correlationId,
      stream_id: pending.streamId,
      stream_seq: pending.streamSeq,
      payload_kind: pending.payloadKind,
      payload: pending.payload,
      priority: WIRE_PRIORITY[pending.trustedClass],
    })
    const wire = encodeMuxPacket(header, pending.payload)
    if (this.#sentBytes + wire.length > this.#connectionMaxBytes) {
      this.#poisonAndClear()
      reject('mux_connection_admission_invariant')
    }
    const prepared = Object.freeze({
      wire,
      lane,
      messageId: pending.messageId,
      globalSeq,
      laneSeq,
    })
    this.#inflight = { pending, prepared, proposedSchedule: proposed, started: false }
    return prepared
  }

  beginWrite() {
    if (this.#poisoned) reject('mux_connection_poisoned')
    if (this.#inflight === null) reject('mux_writer_not_reserved')
    if (this.#inflight.started) reject('mux_write_already_started')
    const inflight = this.#inflight
    const queue = this.#queues[inflight.pending.lane]
    if (queue.peek() !== inflight.pending) {
      this.#poisonAndClear()
      reject('mux_queue_invariant')
    }
    queue.shift()
    const size = inflight.pending.payload.length
    this.#laneBytes[inflight.pending.lane] -= size
    this.#totalItems -= 1
    this.#totalBytes -= size
    this.#queuedWireBytes -= inflight.pending.reservedWireBytes
    this.#schedule = inflight.proposedSchedule
    if (queue.length === 0) this.#schedule.deficits[inflight.pending.lane] = 0
    this.#nextGlobalSeq += 1
    this.#nextLaneSeq[inflight.pending.lane] += 1
    this.#sentFrames += 1
    this.#sentBytes += inflight.prepared.wire.length
    inflight.started = true
  }

  settleWrite(outcome) {
    if (this.#inflight === null) reject('mux_writer_not_reserved')
    if (!WRITE_OUTCOME_SET.has(outcome)) reject('invalid_mux_write_outcome')
    const inflight = this.#inflight
    if (outcome === 'outcome_unknown') {
      this.#poisonAndClear()
      return
    }
    if (outcome === 'not_sent') {
      if (inflight.started) {
        this.#poisonAndClear()
        reject('mux_write_started_cannot_retry')
      }
      this.#inflight = null
      return
    }
    if (!inflight.started) reject('mux_write_not_started')
    this.#inflight = null
  }
}

export class NativeMuxReceiver {
  #senderRole
  #connectionGeneration
  #connectionId
  #runtimeIncarnation
  #maxStreams
  #connectionMaxFrames
  #connectionMaxBytes
  #nextGlobalSeq = 1
  #nextLaneSeq = blankLaneNumbers(1)
  #nextStreamSeq = new Map()
  #messageIds = new Set()
  #acceptedFrames = 0
  #acceptedBytes = 0
  #pendingRead = null
  #poisoned = false

  constructor({
    senderRole,
    connectionGeneration,
    connectionId,
    runtimeIncarnation,
    maxStreams = DEFAULT_MAX_STREAMS,
    connectionMaxFrames = DEFAULT_CONNECTION_MAX_FRAMES,
    connectionMaxBytes = DEFAULT_CONNECTION_MAX_BYTES,
  }) {
    this.#maxStreams = positive(maxStreams, 'invalid_stream_limit')
    this.#connectionMaxFrames = positive(connectionMaxFrames, 'invalid_connection_frame_limit')
    this.#connectionMaxBytes = positive(connectionMaxBytes, 'invalid_connection_byte_limit')
    if (this.#maxStreams > HARD_MAX_STREAMS) reject('mux_stream_limit_too_large')
    if (this.#connectionMaxFrames > HARD_MAX_QUEUE_ITEMS
        || this.#connectionMaxBytes > HARD_MAX_QUEUE_BYTES) {
      reject('mux_connection_limit_too_large')
    }
    makeMuxHeader({
      lane: 'control',
      sender_role: senderRole,
      connection_generation: connectionGeneration,
      connection_id: connectionId,
      runtime_incarnation: runtimeIncarnation,
      global_seq: 1,
      lane_seq: 1,
      message_id: 'mux-receiver-check',
      correlation_id: null,
      stream_id: null,
      stream_seq: null,
      payload_kind: 'control',
      payload: Buffer.alloc(0),
      priority: 'control',
    })
    this.#senderRole = senderRole
    this.#connectionGeneration = connectionGeneration
    this.#connectionId = connectionId
    this.#runtimeIncarnation = runtimeIncarnation
  }

  get poisoned() { return this.#poisoned }
  get retainedMessageIds() { return this.#messageIds.size }
  get trackedStreams() { return this.#nextStreamSeq.size }

  poison() {
    this.#poisoned = true
    this.#pendingRead = null
    this.#messageIds.clear()
    this.#nextStreamSeq.clear()
  }

  prepareWire(wire) {
    if (this.#poisoned) reject('mux_connection_poisoned')
    if (this.#pendingRead !== null) reject('mux_reader_already_reserved')
    try {
      if (!Buffer.isBuffer(wire) && !(wire instanceof Uint8Array)) reject('invalid_mux_wire')
      const wireBytes = wire.byteLength
      if (this.#acceptedFrames + 1 > this.#connectionMaxFrames) {
        reject('mux_connection_frame_limit')
      }
      if (this.#acceptedBytes + wireBytes > this.#connectionMaxBytes) {
        reject('mux_connection_byte_limit')
      }
      const packet = decodeMuxPacket(wire)
      const header = packet.header
      if (header.sender_role !== this.#senderRole) reject('mux_sender_role_mismatch')
      if (header.connection_generation !== this.#connectionGeneration) {
        reject('mux_connection_generation_mismatch')
      }
      if (header.connection_id !== this.#connectionId) reject('mux_connection_id_mismatch')
      if (header.runtime_incarnation !== this.#runtimeIncarnation) {
        reject('mux_runtime_incarnation_mismatch')
      }
      const lane = header.lane
      if (this.#nextGlobalSeq > JSON_SAFE_INTEGER_MAX) reject('mux_sequence_exhausted')
      if (header.global_seq !== this.#nextGlobalSeq) reject('mux_global_sequence_mismatch')
      if (this.#nextLaneSeq[lane] > JSON_SAFE_INTEGER_MAX) reject('mux_sequence_exhausted')
      if (header.lane_seq !== this.#nextLaneSeq[lane]) reject('mux_lane_sequence_mismatch')
      if (this.#messageIds.has(header.message_id)) reject('mux_message_id_reused')
      if (lane === 'remote_stream') {
        let expectedStream
        if (!this.#nextStreamSeq.has(header.stream_id)) {
          if (this.#nextStreamSeq.size >= this.#maxStreams) reject('mux_stream_limit')
          expectedStream = 1
        } else {
          expectedStream = this.#nextStreamSeq.get(header.stream_id)
        }
        if (expectedStream > JSON_SAFE_INTEGER_MAX) reject('mux_sequence_exhausted')
        if (header.stream_seq !== expectedStream) reject('mux_stream_sequence_mismatch')
      }
      this.#pendingRead = { packet, wireBytes }
      return packet
    } catch (error) {
      if (error instanceof NativeMuxProtocolError
          || error instanceof NativePipeMultiplexerError) {
        this.poison()
      }
      throw error
    }
  }

  settleRead(outcome) {
    if (this.#pendingRead === null) reject('mux_reader_not_reserved')
    if (!READ_OUTCOME_SET.has(outcome)) reject('invalid_mux_read_outcome')
    const pending = this.#pendingRead
    this.#pendingRead = null
    if (outcome === 'outcome_unknown') {
      this.poison()
      return
    }
    if (outcome === 'not_delivered') return
    const header = pending.packet.header
    const lane = header.lane
    this.#nextGlobalSeq += 1
    this.#nextLaneSeq[lane] += 1
    this.#messageIds.add(header.message_id)
    if (lane === 'remote_stream') {
      this.#nextStreamSeq.set(header.stream_id, header.stream_seq + 1)
    }
    this.#acceptedFrames += 1
    this.#acceptedBytes += pending.wireBytes
  }
}
