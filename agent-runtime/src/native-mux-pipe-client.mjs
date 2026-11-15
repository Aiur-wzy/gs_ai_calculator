/**
 * Non-production raw-socket carrier for the native pipe mux state machines.
 *
 * The caller owns authentication and supplies an already connected net.Socket.
 * This module owns no listener, launch contract, DSH service or production
 * authority. It performs exact raw mux framing and keeps the transport outcome
 * leases aligned with NativeMuxSendQueue and NativeMuxReceiver.
 */
import { performance } from 'node:perf_hooks'

import {
  MAX_MUX_BODY_BYTES,
  MAX_MUX_WIRE_BYTES,
} from './native-mux-protocol.mjs'
import {
  MUX_LANE_ORDER,
  NativeMuxReceiver,
  NativeMuxSendQueue,
} from './native-pipe-multiplexer.mjs'

export const PRODUCTION_OWNER = false
export const DEFAULT_MUX_PIPE_PARTIAL_FRAME_TIMEOUT_MS = 10 * 60_000
export const DEFAULT_MUX_PIPE_WRITE_TIMEOUT_MS = 10 * 60_000
// Node/libuv clamps larger setTimeout delays to approximately 1 ms. Reject
// them instead of silently turning an intentionally broad safety deadline
// into an immediate timeout.
export const MAX_NODE_TIMER_TIMEOUT_MS = 2_147_483_647
// No physical-carrier high-water has been measured yet. This is a bounded,
// deliberately broad qualification choice, not a DSH default or task budget.
export const HARD_MAX_MUX_PIPE_BUFFERED_BYTES = 2 * MAX_MUX_WIRE_BYTES
export const DEFAULT_MUX_PIPE_MAX_BUFFERED_BYTES = HARD_MAX_MUX_PIPE_BUFFERED_BYTES

const LENGTH_PREFIX_BYTES = 4

export class NativeMuxPipeClientError extends Error {
  constructor(code, causeCode = null) {
    super(code)
    this.name = 'NativeMuxPipeClientError'
    this.code = code
    this.causeCode = causeCode
  }
}
function fail(code, causeCode = null) {
  throw new NativeMuxPipeClientError(code, causeCode)
}

function positiveSafeInteger(value, code) {
  if (!Number.isSafeInteger(value) || value < 1) fail(code)
  return value
}

function timerTimeout(value, code) {
  const timeout = positiveSafeInteger(value, code)
  if (timeout > MAX_NODE_TIMER_TIMEOUT_MS) fail(code)
  return timeout
}

function optionalTimeout(value, code) {
  if (value === null) return null
  return timerTimeout(value, code)
}

function errorCode(error) {
  if (typeof error?.code === 'string' && error.code.length > 0) return error.code
  if (typeof error?.name === 'string' && error.name.length > 0) return error.name
  return 'unknown_error'
}

function blankLaneTelemetry() {
  return Object.fromEntries(MUX_LANE_ORDER.map(lane => [lane, {
    queuedItems: 0,
    queuedPayloadBytes: 0,
    peakQueuedItems: 0,
    peakQueuedPayloadBytes: 0,
    sentFrames: 0,
    sentPayloadBytes: 0,
    sentWireBytes: 0,
    acceptedFrames: 0,
    acceptedPayloadBytes: 0,
    acceptedWireBytes: 0,
  }]))
}

class SegmentQueue {
  #segments = []
  #head = 0
  #headOffset = 0
  #bytes = 0

  get bytes() { return this.#bytes }

  get firstArrivalAt() {
    return this.#bytes === 0 ? null : this.#segments[this.#head].arrivalAt
  }

  push(value, arrivalAt) {
    const bytes = Buffer.isBuffer(value) ? value : Buffer.from(value)
    if (bytes.length === 0) return
    this.#segments.push({ bytes, arrivalAt })
    this.#bytes += bytes.length
  }

  copyInto(target, offset, maximum) {
    let copied = 0
    while (copied < maximum && this.#bytes > 0) {
      const segment = this.#segments[this.#head].bytes
      const available = segment.length - this.#headOffset
      const take = Math.min(available, maximum - copied)
      segment.copy(target, offset + copied, this.#headOffset, this.#headOffset + take)
      copied += take
      this.#headOffset += take
      this.#bytes -= take
      if (this.#headOffset === segment.length) {
        this.#head += 1
        this.#headOffset = 0
      }
    }
    if (this.#head >= 1024 && this.#head * 2 >= this.#segments.length) {
      this.#segments = this.#segments.slice(this.#head)
      this.#head = 0
    }
    if (this.#bytes === 0) {
      this.#segments = []
      this.#head = 0
      this.#headOffset = 0
    }
    return copied
  }

  clear() {
    this.#segments = []
    this.#head = 0
    this.#headOffset = 0
    this.#bytes = 0
  }
}

/**
 * Exact mux framing over one already connected net.Socket-compatible object.
 *
 * readNext() returns one receiver-prepared packet. The caller must then invoke
 * settleRead(). A not_delivered outcome retains the exact wire and readNext()
 * presents it again before any later socket bytes can be consumed.
 */
export class NativeMuxPipeClient {
  #socket
  #sendQueue
  #receiver
  #segments = new SegmentQueue()
  #maximumBufferedBytes
  #partialFrameTimeoutMs
  #writeTimeoutMs
  #clock
  #readBusy = false
  #writeBusy = false
  #activeDelivery = null
  #redeliveryWire = null
  #dataWaiter = null
  #activeWrite = null
  #failure = null
  #closed = false
  #closing = false
  #inputEnded = false
  #socketClosed = false
  #partialFrameBytes = 0
  #partialStartedAt = null
  #outbound = new Map()
  #startedWireBytes = 0
  #laneTelemetry = blankLaneTelemetry()
  #telemetry = {
    bodyAllocations: 0,
    allocatedBodyBytes: 0,
    framedFrames: 0,
    framedWireBytes: 0,
    sentFrames: 0,
    sentPayloadBytes: 0,
    sentWireBytes: 0,
    acceptedFrames: 0,
    acceptedPayloadBytes: 0,
    acceptedWireBytes: 0,
    redeliveries: 0,
    notDelivered: 0,
    readWaits: 0,
    readWaitMs: 0,
    writeWaits: 0,
    writeWaitMs: 0,
    backpressureWrites: 0,
    peakBufferedBytes: 0,
    peakPartialFrameBytes: 0,
    peakQueuedItems: 0,
    peakQueuedPayloadBytes: 0,
    peakQueuedWireBytes: 0,
    rejected: {},
    poisonCause: null,
    poisonCauseCode: null,
    bufferedBytesAtFailure: 0,
    partialFrameBytesAtFailure: 0,
  }
  #onDataBound
  #onErrorBound
  #onEndBound
  #onCloseBound

  constructor(socket, {
    senderRole,
    peerSenderRole,
    connectionGeneration,
    connectionId,
    runtimeIncarnation,
    maximumBufferedBytes = DEFAULT_MUX_PIPE_MAX_BUFFERED_BYTES,
    partialFrameTimeoutMs = DEFAULT_MUX_PIPE_PARTIAL_FRAME_TIMEOUT_MS,
    writeTimeoutMs = DEFAULT_MUX_PIPE_WRITE_TIMEOUT_MS,
    sendQueueOptions = {},
    receiverOptions = {},
    clock = () => performance.now(),
  } = {}) {
    if (socket === null
        || typeof socket?.on !== 'function'
        || typeof socket?.write !== 'function'
        || typeof socket?.pause !== 'function'
        || typeof socket?.resume !== 'function'
        || typeof socket?.destroy !== 'function') {
      fail('mux_pipe_socket_invalid')
    }
    if (typeof clock !== 'function') fail('mux_pipe_clock_invalid')
    this.#maximumBufferedBytes = positiveSafeInteger(
      maximumBufferedBytes, 'mux_pipe_buffer_limit_invalid',
    )
    if (this.#maximumBufferedBytes > HARD_MAX_MUX_PIPE_BUFFERED_BYTES) {
      fail('mux_pipe_buffer_limit_too_large')
    }
    this.#partialFrameTimeoutMs = timerTimeout(
      partialFrameTimeoutMs, 'mux_pipe_partial_timeout_invalid',
    )
    this.#writeTimeoutMs = timerTimeout(
      writeTimeoutMs, 'mux_pipe_write_timeout_invalid',
    )
    this.#clock = clock
    this.#socket = socket
    this.#sendQueue = new NativeMuxSendQueue({
      ...sendQueueOptions,
      senderRole,
      connectionGeneration,
      connectionId,
      runtimeIncarnation,
    })
    this.#receiver = new NativeMuxReceiver({
      ...receiverOptions,
      senderRole: peerSenderRole,
      connectionGeneration,
      connectionId,
      runtimeIncarnation,
    })

    this.#onDataBound = chunk => this.#onData(chunk)
    this.#onErrorBound = error => this.#poison('mux_pipe_socket_error', errorCode(error))
    this.#onEndBound = () => this.#markInputEnded()
    this.#onCloseBound = () => {
      this.#socketClosed = true
      if (!this.#closing && this.#failure === null && this.#activeWrite !== null) {
        this.#poison('mux_pipe_unexpected_close')
      } else if (!this.#closing && this.#failure === null) {
        // No future outbound frame can be written once the transport closed.
        // Release that ownership immediately, while deliberately preserving
        // the receive side so a complete final frame can still be settled.
        this.#releaseOutboundState()
        // A final complete frame may already be buffered when the peer closes.
        // Preserve it for delivery; EOF is reported only after that buffer is
        // drained, while a genuinely partial frame is classified by #fill.
        this.#markInputEnded()
      }
      if (this.#closing || this.#failure !== null) this.#closed = true
    }
    socket.pause()
    socket.on('data', this.#onDataBound)
    socket.on('error', this.#onErrorBound)
    socket.on('end', this.#onEndBound)
    socket.on('close', this.#onCloseBound)
  }

  get poisoned() { return this.#failure !== null }
  get closed() { return this.#closed }

  #recordRejection(code) {
    this.#telemetry.rejected[code] = (this.#telemetry.rejected[code] ?? 0) + 1
  }

  #queuedWireBytes() {
    if (this.#sendQueue.queuedItems === 0) return 0
    return Math.max(0, this.#sendQueue.reservedConnectionBytes - this.#startedWireBytes)
  }

  #updateQueuePeaks() {
    this.#telemetry.peakQueuedItems = Math.max(
      this.#telemetry.peakQueuedItems, this.#sendQueue.queuedItems,
    )
    this.#telemetry.peakQueuedPayloadBytes = Math.max(
      this.#telemetry.peakQueuedPayloadBytes, this.#sendQueue.queuedBytes,
    )
    this.#telemetry.peakQueuedWireBytes = Math.max(
      this.#telemetry.peakQueuedWireBytes, this.#queuedWireBytes(),
    )
  }

  #clearQueueTelemetry() {
    this.#outbound.clear()
    for (const lane of MUX_LANE_ORDER) {
      this.#laneTelemetry[lane].queuedItems = 0
      this.#laneTelemetry[lane].queuedPayloadBytes = 0
    }
  }

  #releaseOutboundState() {
    this.#sendQueue.poison()
    this.#clearQueueTelemetry()
  }

  #releaseInboundState() {
    this.#receiver.poison()
    this.#segments.clear()
    this.#activeDelivery = null
    this.#redeliveryWire = null
    this.#partialFrameBytes = 0
    this.#partialStartedAt = null
  }

  #releaseRetainedState() {
    // Closing a carrier is also a memory-ownership boundary.  The mux state
    // machines intentionally retain queued payloads and an unsettled inbound
    // wire, so a transport which merely destroys the socket can otherwise
    // keep queue-cap-sized buffers alive after it is unusable.
    this.#releaseOutboundState()
    this.#releaseInboundState()
  }

  #poison(code, causeCode = null) {
    if (this.#failure !== null) return this.#failure
    if (this.#activeWrite !== null && code !== 'mux_pipe_write_outcome_unknown') {
      causeCode = causeCode ?? code
      code = 'mux_pipe_write_outcome_unknown'
    }
    this.#recordRejection(code)
    this.#failure = new NativeMuxPipeClientError(code, causeCode)
    this.#telemetry.poisonCause = code
    this.#telemetry.poisonCauseCode = causeCode
    this.#telemetry.bufferedBytesAtFailure = this.#segments.bytes
    this.#telemetry.partialFrameBytesAtFailure = (
      this.#partialFrameBytes + this.#segments.bytes
    )
    this.#releaseRetainedState()
    if (this.#dataWaiter !== null) {
      const waiter = this.#dataWaiter
      this.#dataWaiter = null
      clearTimeout(waiter.timer)
      this.#telemetry.readWaitMs += Math.max(0, this.#clock() - waiter.startedAt)
      waiter.reject(this.#failure)
    }
    if (this.#activeWrite !== null) this.#activeWrite.fail(this.#failure)
    try {
      this.#socket.destroy()
    } catch {
      // The connection is already poisoned; destroy errors cannot restore it.
    }
    return this.#failure
  }

  #assertUsable() {
    if (this.#failure !== null) throw this.#failure
    if (this.#closed || this.#closing) fail('mux_pipe_closed')
  }

  #markInputEnded() {
    if (this.#inputEnded) return
    this.#inputEnded = true
    this.#socket.pause()
    if (this.#dataWaiter !== null) {
      const waiter = this.#dataWaiter
      this.#dataWaiter = null
      clearTimeout(waiter.timer)
      this.#telemetry.readWaitMs += Math.max(0, this.#clock() - waiter.startedAt)
      waiter.resolve()
    }
  }

  #onData(chunk) {
    if (this.#failure !== null || this.#closed || this.#closing) return
    let bytes
    try {
      bytes = Buffer.isBuffer(chunk) ? chunk : Buffer.from(chunk)
    } catch {
      this.#poison('mux_pipe_chunk_invalid')
      return
    }
    if (bytes.length === 0) return
    this.#socket.pause()
    if (this.#segments.bytes + bytes.length > this.#maximumBufferedBytes) {
      this.#poison('mux_pipe_buffer_limit')
      return
    }
    this.#segments.push(bytes, this.#clock())
    this.#telemetry.peakBufferedBytes = Math.max(
      this.#telemetry.peakBufferedBytes, this.#segments.bytes,
    )
    if (this.#dataWaiter !== null) {
      const waiter = this.#dataWaiter
      this.#dataWaiter = null
      clearTimeout(waiter.timer)
      this.#telemetry.readWaitMs += Math.max(0, this.#clock() - waiter.startedAt)
      waiter.resolve()
    }
  }

  async #awaitData(deadline, timeoutCode, fatal) {
    if (this.#segments.bytes > 0) return
    this.#assertUsable()
    if (this.#inputEnded) {
      throw this.#poison(fatal ? 'mux_pipe_truncated' : 'mux_pipe_unexpected_eof')
    }
    if (this.#dataWaiter !== null) fail('mux_pipe_reader_busy')
    const remaining = deadline === null ? null : deadline - this.#clock()
    if (remaining !== null && remaining <= 0) {
      if (fatal) throw this.#poison(timeoutCode)
      this.#recordRejection(timeoutCode)
      throw new NativeMuxPipeClientError(timeoutCode)
    }
    this.#telemetry.readWaits += 1
    const startedAt = this.#clock()
    const pending = new Promise((resolve, reject) => {
      const timer = remaining === null ? null : setTimeout(() => {
        if (this.#dataWaiter === null) return
        const waiter = this.#dataWaiter
        this.#dataWaiter = null
        this.#socket.pause()
        this.#telemetry.readWaitMs += Math.max(0, this.#clock() - waiter.startedAt)
        if (fatal) reject(this.#poison(timeoutCode))
        else {
          this.#recordRejection(timeoutCode)
          reject(new NativeMuxPipeClientError(timeoutCode))
        }
      }, Math.max(1, Math.ceil(remaining)))
      timer?.unref?.()
      this.#dataWaiter = { resolve, reject, timer, startedAt }
    })
    this.#socket.resume()
    await pending
  }

  async #fill(target, offset, length, state) {
    let copied = 0
    while (copied < length) {
      if (this.#segments.bytes === 0) {
        const firstBytePending = state.startedAt === null
        await this.#awaitData(
          firstBytePending ? state.idleDeadline : state.partialDeadline,
          firstBytePending ? 'mux_pipe_read_idle_timeout' : 'mux_pipe_partial_frame_timeout',
          !firstBytePending,
        )
        // An EOF wake carries no bytes. Re-enter #awaitData before assigning
        // the first-byte timestamp so a clean EOF remains distinct from a
        // frame which actually began and was then truncated.
        if (this.#segments.bytes === 0) continue
      }
      if (state.startedAt === null) {
        // Bytes coalesced behind a prior, unsettled frame were received while
        // this carrier deliberately held backpressure. Do not charge that
        // local delivery delay to the next frame's slowloris deadline.
        state.startedAt = Math.max(
          state.readStartedAt,
          this.#segments.firstArrivalAt ?? this.#clock(),
        )
        state.partialDeadline = state.startedAt + state.partialTimeoutMs
        this.#partialStartedAt = state.startedAt
      }
      const count = this.#segments.copyInto(target, offset + copied, length - copied)
      copied += count
      state.bytes += count
      this.#partialFrameBytes = state.bytes
      this.#telemetry.peakPartialFrameBytes = Math.max(
        this.#telemetry.peakPartialFrameBytes, state.bytes,
      )
    }
  }

  async #readWire({ idleTimeoutMs, partialFrameTimeoutMs }) {
    const now = this.#clock()
    const state = {
      startedAt: null,
      partialDeadline: null,
      idleDeadline: idleTimeoutMs === null ? null : now + idleTimeoutMs,
      partialTimeoutMs: partialFrameTimeoutMs,
      readStartedAt: now,
      bytes: 0,
    }
    this.#partialFrameBytes = 0
    this.#partialStartedAt = null
    const prefix = Buffer.allocUnsafe(LENGTH_PREFIX_BYTES)
    await this.#fill(prefix, 0, prefix.length, state)
    const bodyLength = prefix.readUInt32BE(0)
    if (bodyLength > MAX_MUX_BODY_BYTES) {
      this.#recordRejection('mux_pipe_body_too_large')
      throw this.#poison('mux_pipe_body_too_large', 'mux_packet_too_large')
    }
    const wire = Buffer.allocUnsafe(LENGTH_PREFIX_BYTES + bodyLength)
    this.#telemetry.bodyAllocations += 1
    this.#telemetry.allocatedBodyBytes += bodyLength
    prefix.copy(wire, 0)
    await this.#fill(wire, LENGTH_PREFIX_BYTES, bodyLength, state)
    this.#partialFrameBytes = 0
    this.#partialStartedAt = null
    this.#telemetry.framedFrames += 1
    this.#telemetry.framedWireBytes += wire.length
    return wire
  }

  enqueue(input) {
    this.#assertUsable()
    if (this.#socketClosed) fail('mux_pipe_socket_closed')
    let payloadBytes
    try {
      payloadBytes = input?.payload?.byteLength
      this.#sendQueue.enqueue(input)
    } catch (error) {
      this.#recordRejection(errorCode(error))
      if (this.#sendQueue.poisoned) {
        throw this.#poison('mux_pipe_send_state_invalid', errorCode(error))
      }
      throw error
    }
    const meta = Object.freeze({ lane: input.lane, payloadBytes })
    this.#outbound.set(input.messageId, meta)
    const lane = this.#laneTelemetry[input.lane]
    lane.queuedItems += 1
    lane.queuedPayloadBytes += payloadBytes
    lane.peakQueuedItems = Math.max(lane.peakQueuedItems, lane.queuedItems)
    lane.peakQueuedPayloadBytes = Math.max(
      lane.peakQueuedPayloadBytes, lane.queuedPayloadBytes,
    )
    this.#updateQueuePeaks()
  }

  async writeNext({ timeoutMs = this.#writeTimeoutMs } = {}) {
    this.#assertUsable()
    if (this.#socketClosed) fail('mux_pipe_socket_closed')
    optionalTimeout(timeoutMs, 'mux_pipe_write_timeout_invalid')
    if (this.#writeBusy) fail('mux_pipe_writer_busy')
    this.#writeBusy = true
    let prepared = null
    let started = false
    const startedAt = this.#clock()
    try {
      prepared = this.#sendQueue.prepareNext()
      if (prepared === null) return null
      const meta = this.#outbound.get(prepared.messageId)
      if (meta === undefined) fail('mux_pipe_outbound_telemetry_invariant')
      this.#sendQueue.beginWrite()
      started = true
      this.#startedWireBytes += prepared.wire.length
      const lane = this.#laneTelemetry[meta.lane]
      lane.queuedItems -= 1
      lane.queuedPayloadBytes -= meta.payloadBytes
      this.#telemetry.writeWaits += 1
      await new Promise((resolve, reject) => {
        let settled = false
        const finish = (error = null) => {
          if (settled) return
          settled = true
          clearTimeout(timer)
          this.#activeWrite = null
          if (error === null) resolve()
          else reject(error)
        }
        const timer = timeoutMs === null ? null : setTimeout(() => {
          finish(new NativeMuxPipeClientError('mux_pipe_write_timeout'))
        }, timeoutMs)
        timer?.unref?.()
        this.#activeWrite = { fail: error => finish(error) }
        try {
          const accepted = this.#socket.write(prepared.wire, error => {
            if (error) finish(new NativeMuxPipeClientError(
              'mux_pipe_write_failed', errorCode(error),
            ))
            else finish()
          })
          if (accepted === false) this.#telemetry.backpressureWrites += 1
        } catch (error) {
          finish(new NativeMuxPipeClientError(
            'mux_pipe_write_failed', errorCode(error),
          ))
        }
      })
      this.#sendQueue.settleWrite('sent')
      this.#outbound.delete(prepared.messageId)
      this.#telemetry.sentFrames += 1
      this.#telemetry.sentPayloadBytes += meta.payloadBytes
      this.#telemetry.sentWireBytes += prepared.wire.length
      lane.sentFrames += 1
      lane.sentPayloadBytes += meta.payloadBytes
      lane.sentWireBytes += prepared.wire.length
      return prepared
    } catch (error) {
      if (started) {
        if (!this.#sendQueue.poisoned) this.#sendQueue.settleWrite('outcome_unknown')
        throw this.#poison('mux_pipe_write_outcome_unknown', errorCode(error))
      }
      if (this.#sendQueue.poisoned) {
        throw this.#poison('mux_pipe_send_state_invalid', errorCode(error))
      }
      throw error
    } finally {
      this.#telemetry.writeWaitMs += Math.max(0, this.#clock() - startedAt)
      this.#writeBusy = false
    }
  }

  async readNext({
    idleTimeoutMs = null,
    partialFrameTimeoutMs = this.#partialFrameTimeoutMs,
  } = {}) {
    this.#assertUsable()
    optionalTimeout(idleTimeoutMs, 'mux_pipe_idle_timeout_invalid')
    timerTimeout(partialFrameTimeoutMs, 'mux_pipe_partial_timeout_invalid')
    if (this.#readBusy) fail('mux_pipe_reader_busy')
    if (this.#activeDelivery !== null) fail('mux_pipe_read_unsettled')
    this.#readBusy = true
    try {
      const redelivery = this.#redeliveryWire !== null
      const wire = redelivery
        ? this.#redeliveryWire
        : await this.#readWire({ idleTimeoutMs, partialFrameTimeoutMs })
      let packet
      try {
        packet = this.#receiver.prepareWire(wire)
      } catch (error) {
        throw this.#poison('mux_pipe_receive_invalid', errorCode(error))
      }
      if (redelivery) this.#telemetry.redeliveries += 1
      this.#redeliveryWire = null
      this.#activeDelivery = { wire, packet }
      return packet
    } catch (error) {
      if (error instanceof NativeMuxPipeClientError
          && error.code === 'mux_pipe_read_idle_timeout') {
        throw error
      }
      if (this.#failure !== null) throw this.#failure
      throw this.#poison('mux_pipe_read_failed', errorCode(error))
    } finally {
      this.#readBusy = false
    }
  }

  settleRead(outcome) {
    this.#assertUsable()
    if (this.#activeDelivery === null) fail('mux_pipe_read_not_reserved')
    const active = this.#activeDelivery
    try {
      this.#receiver.settleRead(outcome)
    } catch (error) {
      throw this.#poison('mux_pipe_read_settle_failed', errorCode(error))
    }
    this.#activeDelivery = null
    if (outcome === 'not_delivered') {
      this.#telemetry.notDelivered += 1
      this.#redeliveryWire = active.wire
      return
    }
    if (outcome === 'outcome_unknown') {
      throw this.#poison('mux_pipe_read_outcome_unknown')
    }
    const lane = this.#laneTelemetry[active.packet.header.lane]
    this.#telemetry.acceptedFrames += 1
    this.#telemetry.acceptedPayloadBytes += active.packet.payload.length
    this.#telemetry.acceptedWireBytes += active.wire.length
    lane.acceptedFrames += 1
    lane.acceptedPayloadBytes += active.packet.payload.length
    lane.acceptedWireBytes += active.wire.length
  }

  diagnosticSnapshot() {
    return Object.freeze({
      productionOwner: PRODUCTION_OWNER,
      closed: this.#closed,
      closing: this.#closing,
      inputEnded: this.#inputEnded,
      socketClosed: this.#socketClosed,
      poisoned: this.#failure !== null,
      bufferedBytes: this.#segments.bytes,
      partialFrameBytes: this.#partialFrameBytes,
      partialStartedAt: this.#partialStartedAt,
      readerBusy: this.#readBusy,
      writerBusy: this.#writeBusy,
      deliveryPending: this.#activeDelivery !== null,
      redeliveryPending: this.#redeliveryWire !== null,
      queue: Object.freeze({
        currentItems: this.#sendQueue.queuedItems,
        currentPayloadBytes: this.#sendQueue.queuedBytes,
        currentWireBytes: this.#queuedWireBytes(),
        peakItems: this.#telemetry.peakQueuedItems,
        peakPayloadBytes: this.#telemetry.peakQueuedPayloadBytes,
        peakWireBytes: this.#telemetry.peakQueuedWireBytes,
      }),
      lanes: Object.freeze(Object.fromEntries(MUX_LANE_ORDER.map(lane => [
        lane, Object.freeze({ ...this.#laneTelemetry[lane] }),
      ]))),
      counters: Object.freeze({
        ...this.#telemetry,
        rejected: Object.freeze({ ...this.#telemetry.rejected }),
      }),
    })
  }

  close() {
    if (this.#closed || this.#closing) return
    if (this.#readBusy || this.#writeBusy || this.#activeDelivery !== null) {
      this.#poison('mux_pipe_close_during_io')
      return
    }
    this.#closing = true
    if (this.#dataWaiter !== null) {
      const waiter = this.#dataWaiter
      this.#dataWaiter = null
      clearTimeout(waiter.timer)
      waiter.reject(new NativeMuxPipeClientError('mux_pipe_closed'))
    }
    this.#releaseRetainedState()
    this.#socket.pause()
    try {
      this.#socket.destroy()
    } finally {
      this.#closed = true
      this.#closing = false
    }
  }
}

// This qualification carrier never becomes authoritative without an explicit
// production owner switch and codec activation contract.
