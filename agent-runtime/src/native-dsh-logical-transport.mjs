/** Streaming mux binding for the existing logical byte-stream contract. */
import { createHash, randomUUID } from 'node:crypto'
import { createReadStream } from 'node:fs'
import { mkdir, statfs } from 'node:fs/promises'
import { once } from 'node:events'
import { Readable } from 'node:stream'
import {
  LOGICAL_STREAM_CONTRACT, LogicalStreamAssembler, LogicalStreamCreditTracker,
  HARD_MAX_LOGICAL_STREAM_BYTES, DEFAULT_LOGICAL_STREAM_CHUNK_BYTES,
  DEFAULT_LOGICAL_STREAM_CREDIT_BYTES, DEFAULT_DIRECT_PAYLOAD_THRESHOLD_BYTES,
} from './native-logical-stream.mjs'

const KINDS = new Set(['logical_stream.open', 'logical_stream.chunk', 'logical_stream.commit',
  'logical_stream.accept', 'logical_stream.credit', 'logical_stream.committed',
  'logical_stream.cancel', 'logical_stream.cancelled', 'logical_stream.error'])
const json = raw => JSON.parse(new TextDecoder('utf8', { fatal: true }).decode(raw))
const failure = code => Object.assign(new Error(code), { code })
const record = value => value !== null && typeof value === 'object' && !Array.isArray(value)

/** An opaque, verified local artifact; wire metadata can never name its path. */
class LogicalBody {
  constructor(assembler, artifact) {
    this.assembler = assembler
    this.artifact = artifact
    this.length = artifact.totalLength
    this.sha256 = artifact.sha256
    this.readers = new Set()
    this.released = false
    this.releasing = null
  }
  stream() {
    if (this.released || this.releasing !== null) throw failure('dsh_logical_body_released')
    const reader = createReadStream(this.artifact.path, { highWaterMark: DEFAULT_LOGICAL_STREAM_CHUNK_BYTES })
    this.readers.add(reader)
    reader.once('close', () => this.readers.delete(reader))
    return reader
  }
  async release() {
    if (this.released) return
    if (this.releasing !== null) return this.releasing
    this.releasing = (async () => {
      const closed = [...this.readers].map(async reader => {
        const wait = once(reader, 'close').catch(() => {})
        reader.destroy()
        await wait
      })
      await Promise.all(closed)
      await this.assembler.releaseArtifact({ transferId: this.artifact.transferId })
      this.released = true
    })()
    try { return await this.releasing } finally { this.releasing = null }
  }
}

async function* bodyChunks(body, signal) {
  if (body === null) return
  const reader = body.getReader()
  const cancel = () => { void reader.cancel().catch(() => {}) }
  signal?.addEventListener('abort', cancel, { once: true })
  try {
    while (true) {
      signal?.throwIfAborted()
      const item = await reader.read()
      if (item.done) return
      yield Buffer.from(item.value.buffer, item.value.byteOffset, item.value.byteLength)
    }
  } finally {
    signal?.removeEventListener('abort', cancel)
    await reader.cancel().catch(() => {})
    reader.releaseLock()
  }
}

export class NativeDshLogicalTransport {
  static async create({ spoolRoot, send, onArtifact, onCancel = () => {},
    isCancelled = () => false, onError = () => {}, allowedPurposes = new Set(['dsh_fetch_request']),
    maximumBytes = HARD_MAX_LOGICAL_STREAM_BYTES, aggregateSpoolBytes } = {}) {
    await mkdir(spoolRoot, { recursive: true })
    const disk = await statfs(spoolRoot)
    const free = Number(disk.bavail) * Number(disk.bsize)
    const quota = Math.min(aggregateSpoolBytes ?? maximumBytes * 2,
      Math.max(1, free - 512 * 1024 * 1024))
    const assembler = await LogicalStreamAssembler.create({ spoolRoot, allowedPurposes,
      metadataKeysByPurpose: new Map([...allowedPurposes].map(purpose => [purpose,
        new Set(['fetch_metadata', 'result_kind'])])), maximumBytes, aggregateSpoolBytes: quota })
    return new NativeDshLogicalTransport({ assembler, send, onArtifact, onCancel,
      isCancelled, maximumBytes, onError })
  }
  constructor(options) {
    Object.assign(this, options)
    this.incoming = new Map()
    this.outgoing = new Map()
    this.retired = new Set()
    this.bodies = new Set()
    this.tasks = new Set()
    this.sequences = new Map()
    this.closed = false
  }
  emit(header, kind, value, sequence = null) {
    const control = kind === 'logical_stream.cancel'
    const next = (this.sequences.get(header.stream_id) ?? 0) + 1
    if (!control) this.sequences.set(header.stream_id, next)
    return this.send({ lane: control ? 'control' : 'remote_stream',
      payloadKind: kind, payload: value instanceof Uint8Array ? value : Buffer.from(JSON.stringify(value)),
      messageId: randomUUID(), correlationId: header.correlation_id,
      streamId: control ? null : header.stream_id, streamSeq: control ? null : next,
      trustedClass: control ? 'control' : 'normal' })
  }
  accept(packet) {
    const { header, payload } = packet
    const kind = header.payload_kind
    if (!KINDS.has(kind)) return false
    const id = kind === 'logical_stream.cancel' ? json(payload).transfer_id : header.stream_id
    if (typeof id !== 'string' || typeof header.correlation_id !== 'string') {
      throw failure('dsh_logical_identity_invalid')
    }
    if (['logical_stream.accept', 'logical_stream.credit', 'logical_stream.committed',
      'logical_stream.cancelled', 'logical_stream.error'].includes(kind)) {
      const state = this.outgoing.get(id)
      if (state === undefined) {
        if (this.retired.has(id)) return true
        throw failure('dsh_logical_response_unknown')
      }
      if (state.header.correlation_id !== header.correlation_id) throw failure('dsh_logical_correlation_invalid')
      const value = json(payload)
      if (value.transfer_id !== id) throw failure('dsh_logical_transfer_invalid')
      if (kind === 'logical_stream.error' || kind === 'logical_stream.cancelled') {
        state.error = failure(value.code ?? 'dsh_logical_cancelled')
      } else if (kind === 'logical_stream.committed') {
        if (JSON.stringify(value) !== JSON.stringify(state.commit)) throw failure('dsh_logical_receipt_invalid')
        state.committed = true
      } else state.credit.applyGrant({ transferId: id,
        acceptedThroughSeq: value.accepted_through_seq, grantBytes: value.grant_bytes })
      state.wake?.()
      return true
    }
    if (kind === 'logical_stream.cancel') {
      this.retired.add(id) // Control may overtake its normal-lane open.
      const state = this.incoming.get(id)
      if (state !== undefined && state.header.correlation_id !== header.correlation_id) {
        throw failure('dsh_logical_correlation_invalid')
      }
      if (state !== undefined) state.cancelled = true
      this.onCancel(header.correlation_id)
      const task = (state?.tail ?? Promise.resolve()).then(async () => {
        if (this.assembler.active.has(id)) await this.assembler.cancel({ transferId: id })
        this.incoming.delete(id)
        await this.emit({ ...header, stream_id: id },
          'logical_stream.cancelled', { transfer_id: id, reason: 'cancelled' })
      })
      this.track(task)
      return true
    }
    if (this.retired.has(id)) return true
    let state = this.incoming.get(id)
    if (kind === 'logical_stream.open') {
      if (state !== undefined || header.stream_seq !== 1) throw failure('dsh_logical_open_replayed')
      state = { header, tail: Promise.resolve(), cancelled: false }
      this.incoming.set(id, state)
    } else if (state === undefined) throw failure('dsh_logical_transfer_unknown')
    if (state.header.correlation_id !== header.correlation_id || state.header.lane !== header.lane) {
      throw failure('dsh_logical_correlation_invalid')
    }
    state.tail = state.tail.then(async () => {
      if (state.cancelled || this.closed) return
      if (kind === 'logical_stream.open') {
        const value = json(payload)
        if (value.transfer_id !== id || this.isCancelled(header.correlation_id)) {
          throw failure('dsh_logical_cancelled')
        }
        await this.emit(header, 'logical_stream.accept', await this.assembler.open(value), 1)
      } else if (kind === 'logical_stream.chunk') {
        const receipt = await this.assembler.appendChunk({ transferId: id,
          streamSeq: header.stream_seq, payload })
        await this.emit(header, 'logical_stream.credit', receipt, header.stream_seq)
      } else {
        const commit = json(payload)
        const artifact = await this.assembler.commit({ transferId: id,
          streamSeq: header.stream_seq, message: commit })
        const metadata = JSON.parse(artifact.metadataJson)
        const body = new LogicalBody(this.assembler, artifact)
        this.bodies.add(body)
        this.retired.add(id)
        this.incoming.delete(id)
        await this.emit(header, 'logical_stream.committed', commit, header.stream_seq)
        this.track(Promise.resolve().then(() => this.onArtifact({ header, purpose: artifact.purpose,
          metadata, body })).finally(async () => {
          await body.release()
          this.bodies.delete(body)
        }))
      }
    }).catch(async error => {
      state.cancelled = true
      this.retired.add(id)
      if (this.assembler.active.has(id)) await this.assembler.abort(id, 'transfer_failed')
      this.incoming.delete(id)
      if (!this.closed) await this.emit(header, 'logical_stream.error', {
        transfer_id: id, code: typeof error.code === 'string' ? error.code : 'dsh_logical_transfer_failed',
      }, header.stream_seq)
    })
    this.track(state.tail)
    return true
  }
  track(task) {
    this.tasks.add(task)
    task.catch(error => this.onError(error)).finally(() => this.tasks.delete(task))
  }
  async wait(state, predicate) {
    while (!predicate()) {
      if (state.error !== undefined) throw state.error
      if (this.closed) throw failure('dsh_logical_closed')
      await new Promise(resolve => { state.wake = resolve })
      state.wake = null
    }
    if (state.error !== undefined) throw state.error
  }
  async sendSource({ header, purpose, metadata, source, signal, declaredLength = null }) {
    const id = randomUUID()
    const transferHeader = { ...header, stream_id: id }
    const state = { header: transferHeader, credit: new LogicalStreamCreditTracker({ transferId: id,
      maximumCreditBytes: DEFAULT_LOGICAL_STREAM_CREDIT_BYTES }), committed: false, wake: null }
    this.outgoing.set(id, state)
    const abort = () => {
      state.error = failure('dsh_logical_cancelled')
      state.wake?.()
      void this.emit(transferHeader, 'logical_stream.cancel', { transfer_id: id, reason: 'cancelled' }).catch(() => {})
    }
    signal?.addEventListener('abort', abort, { once: true })
    let sequence = 1, total = 0, count = 0
    const digest = createHash('sha256')
    try {
      signal?.throwIfAborted()
      await this.emit(transferHeader, 'logical_stream.open', { contract: LOGICAL_STREAM_CONTRACT,
        transfer_id: id, purpose, media_type: 'application/octet-stream', content_encoding: 'identity',
        maximum_bytes: this.maximumBytes, declared_length: declaredLength, declared_sha256: null,
        chunk_bytes: DEFAULT_LOGICAL_STREAM_CHUNK_BYTES, metadata }, sequence)
      await this.wait(state, () => state.credit.acceptedThroughSeq >= 1)
      let buffer = Buffer.allocUnsafe(DEFAULT_LOGICAL_STREAM_CHUNK_BYTES), used = 0
      const flush = async () => {
        if (used === 0) return
        total += used
        if (total > this.maximumBytes) throw failure('dsh_logical_length_exceeded')
        const chunk = buffer.subarray(0, used)
        await this.wait(state, () => state.credit.availableBytes >= used)
        state.credit.reserve(used)
        digest.update(chunk)
        await this.emit(transferHeader, 'logical_stream.chunk', chunk, ++sequence)
        await this.wait(state, () => state.credit.acceptedThroughSeq >= sequence)
        count += 1
        buffer = Buffer.allocUnsafe(DEFAULT_LOGICAL_STREAM_CHUNK_BYTES)
        used = 0
      }
      for await (const input of source) {
        signal?.throwIfAborted()
        const raw = Buffer.from(input.buffer, input.byteOffset, input.byteLength)
        for (let offset = 0; offset < raw.length;) {
          const length = Math.min(buffer.length - used, raw.length - offset)
          raw.copy(buffer, used, offset, offset + length)
          offset += length; used += length
          if (used === buffer.length) await flush()
        }
      }
      await flush()
      state.commit = { transfer_id: id, chunk_count: count, total_length: total,
        sha256: digest.digest('hex'), terminal: true }
      await this.emit(transferHeader, 'logical_stream.commit', state.commit, ++sequence)
      await this.wait(state, () => state.committed)
      return { length: total, sha256: state.commit.sha256 }
    } catch (error) {
      if (!this.closed) await this.emit(transferHeader, 'logical_stream.cancel', {
        transfer_id: id, reason: 'transfer_failed',
      }).catch(() => {})
      throw error
    } finally {
      signal?.removeEventListener('abort', abort)
      this.outgoing.delete(id)
      this.retired.add(id)
    }
  }
  /** Small raw envelope fastpath; only the bounded prefix is buffered. */
  async sendResponse({ header, kind, response, signal, emitRaw }) {
    const metadata = { status: response.status, headers: [...response.headers] }
    const iterator = bodyChunks(response.body, signal)[Symbol.asyncIterator]()
    const prefix = []
    let size = 0
    try {
      while (size <= DEFAULT_DIRECT_PAYLOAD_THRESHOLD_BYTES) {
        const item = await iterator.next()
        if (item.done) return emitRaw(metadata, Buffer.concat(prefix, size))
        prefix.push(item.value); size += item.value.length
      }
      async function* source() { yield* prefix; while (true) {
        const item = await iterator.next(); if (item.done) return; yield item.value
      } }
      return await this.sendSource({ header, purpose: kind, metadata: {
        fetch_metadata: metadata, result_kind: kind,
      }, source: source(), signal })
    } finally { await iterator.return?.() }
  }
  snapshot() { return { active: this.assembler.activeTransfers, bytes: this.assembler.aggregateSpoolBytes,
    incoming: this.incoming.size, outgoing: this.outgoing.size } }
  async close() {
    if (this.closed) return
    this.closed = true
    for (const state of this.outgoing.values()) { state.error = failure('dsh_logical_closed'); state.wake?.() }
    await Promise.allSettled([...this.incoming.values()].map(state => state.tail))
    await this.assembler.abortAll()
    await Promise.allSettled([...this.bodies].map(body => body.release()))
    this.incoming.clear()
  }
}

export const logicalRequestBody = body => Readable.toWeb(body.stream())
