/**
 * Carrier-only adapter to DSH's public Fetch and wireStream services.
 *
 * Qualification slice: this does not install a production composition, create
 * Sessions or reinterpret any DSH business event. The authenticated pipe owner
 * supplies the Peer and the one serialized send callback.
 */
import { randomUUID } from 'node:crypto'
import { TextDecoder } from 'node:util'
import { MAX_MUX_PAYLOAD_BYTES } from './native-mux-protocol.mjs'
import { NativeDshLogicalTransport, logicalRequestBody } from './native-dsh-logical-transport.mjs'

export const PRODUCTION_OWNER = false
export const DSH_MUX_GATEWAY_CONTRACT = 'genshin-native-dsh-mux-gateway-v1'
export const MAX_FETCH_METADATA_BYTES = 64 * 1024
const utf8 = new TextDecoder('utf-8', { fatal: true, ignoreBOM: true })

export class NativeDshMuxGatewayError extends Error {
  constructor(code) {
    super(code)
    this.name = 'NativeDshMuxGatewayError'
    this.code = code
  }
}
function fail(code) { throw new NativeDshMuxGatewayError(code) }
function record(value) {
  return value !== null && typeof value === 'object' && !Array.isArray(value)
}
function json(bytes) {
  try { return JSON.parse(utf8.decode(bytes)) } catch { fail('dsh_mux_json_invalid') }
}
function exact(value, keys) {
  if (!record(value) || Object.keys(value).length !== keys.length
      || keys.some(key => !Object.hasOwn(value, key))) fail('dsh_mux_payload_invalid')
}
function headerPairs(value) {
  if (!Array.isArray(value) || value.some(pair => !Array.isArray(pair)
      || pair.length !== 2 || pair.some(item => typeof item !== 'string'))) {
    fail('dsh_mux_headers_invalid')
  }
  try { return new Headers(value) } catch { fail('dsh_mux_headers_invalid') }
}
function bounded(bytes) {
  if (bytes.length > MAX_MUX_PAYLOAD_BYTES) fail('dsh_mux_payload_requires_logical_stream')
  return bytes
}

async function responsePayload(response, signal) {
  const metadata = { status: response.status, headers: [...response.headers] }
  const chunks = []
  let size = 4 + Buffer.byteLength(JSON.stringify(metadata), 'utf8')
  if (response.body !== null) {
    const reader = response.body.getReader()
    const cancelRead = () => { void reader.cancel().catch(() => {}) }
    signal.addEventListener('abort', cancelRead, { once: true })
    try {
      while (true) {
        signal.throwIfAborted()
        const item = await reader.read()
        if (item.done) break
        size += item.value.byteLength
        if (size > MAX_MUX_PAYLOAD_BYTES) {
          await reader.cancel()
          fail('dsh_mux_payload_requires_logical_stream')
        }
        chunks.push(Buffer.from(item.value))
      }
    } finally {
      signal.removeEventListener('abort', cancelRead)
      reader.releaseLock()
    }
  }
  signal.throwIfAborted()
  return encodeDshFetchPayload(metadata, Buffer.concat(chunks))
}

/** Metadata prefix followed by unencoded body bytes: no base64 copy/expansion. */
export function encodeDshFetchPayload(metadata, body = Buffer.alloc(0)) {
  const encoded = Buffer.from(JSON.stringify(metadata), 'utf8')
  if (encoded.length < 1 || encoded.length > MAX_FETCH_METADATA_BYTES) {
    fail('dsh_mux_fetch_metadata_invalid')
  }
  if (!(body instanceof Uint8Array)) fail('dsh_mux_fetch_body_invalid')
  if (4 + encoded.length + body.byteLength > MAX_MUX_PAYLOAD_BYTES) {
    fail('dsh_mux_payload_requires_logical_stream')
  }
  const prefix = Buffer.allocUnsafe(4)
  prefix.writeUInt32BE(encoded.length)
  return Buffer.concat([prefix, encoded, body])
}

export function decodeDshFetchPayload(bytes) {
  if (!(bytes instanceof Uint8Array) || bytes.byteLength < 5) {
    fail('dsh_mux_fetch_payload_invalid')
  }
  const raw = Buffer.from(bytes.buffer, bytes.byteOffset, bytes.byteLength)
  const length = raw.readUInt32BE(0)
  if (length < 1 || length > MAX_FETCH_METADATA_BYTES || 4 + length > raw.length) {
    fail('dsh_mux_fetch_metadata_invalid')
  }
  return { metadata: json(raw.subarray(4, 4 + length)), body: raw.subarray(4 + length) }
}

/** A single outstanding uplink item; ACK means DSH actually consumed it. */
class Uplink {
  #pending = null
  #waiter = null
  #ended = false
  #reading = false

  offer(value, acknowledge) {
    if (this.#ended || this.#pending !== null) fail('dsh_mux_uplink_window_exhausted')
    if (this.#waiter !== null) {
      const waiter = this.#waiter
      this.#waiter = null
      waiter({ value, done: false })
      acknowledge()
    } else this.#pending = { value, acknowledge }
  }

  end() {
    if (this.#pending !== null) fail('dsh_mux_uplink_window_exhausted')
    this.#ended = true
    this.#waiter?.({ done: true })
    this.#waiter = null
  }

  [Symbol.asyncIterator]() { return this }

  async next() {
    if (this.#reading) fail('dsh_mux_uplink_reader_busy')
    if (this.#pending !== null) {
      const pending = this.#pending
      this.#pending = null
      pending.acknowledge()
      return { value: pending.value, done: false }
    }
    if (this.#ended) return { done: true }
    this.#reading = true
    try { return await new Promise(resolve => { this.#waiter = resolve }) }
    finally { this.#reading = false }
  }

  async return() { this.abort(); return { done: true } }

  abort() {
    this.#pending = null
    this.#ended = true
    this.#waiter?.({ done: true })
    this.#waiter = null
  }
}

export class NativeDshMuxGateway {
  #handler
  #wireStream
  #peer
  #send
  #streams = new Map()
  #fetches = new Map()
  #usedFetches = new Set()
  #usedStreams = new Set()
  #cancelledFetches = new Set()
  #cancelledStreams = new Set()
  #tasks = new Set()
  #closed = false
  #onFailure
  #assets
  #domainTransport
  #logicalReady
  #accepting = true

  constructor({ handler, wireStream, peer, send, assets, domainTransport, spoolRoot, onFailure = () => {} }) {
    if (typeof handler?.fetch !== 'function'
        || typeof wireStream?.open !== 'function' || typeof wireStream?.failure !== 'function'
        || typeof peer?.dispose !== 'function' || typeof send !== 'function') {
      fail('dsh_mux_gateway_dependencies_invalid')
    }
    this.#handler = handler
    this.#wireStream = wireStream
    this.#peer = peer
    this.#send = send
    this.#onFailure = onFailure
    this.#assets = assets
    this.#domainTransport = domainTransport
    this.#logicalReady = spoolRoot === undefined ? Promise.resolve(null)
      : NativeDshLogicalTransport.create({ spoolRoot, send,
        onError: error => { this.#onFailure(error); void this.close() },
        isCancelled: id => this.#cancelledFetches.has(id),
        onCancel: id => { this.#cancelledFetches.add(id); this.#fetches.get(id)?.abort() },
        onArtifact: async ({ header, purpose, metadata, body }) => {
          const original = { ...header, lane: 'remote_unary', stream_id: null, stream_seq: null }
          try {
            if (purpose !== 'dsh_fetch_request' || metadata.result_kind !== 'dsh_fetch') {
              fail('dsh_mux_logical_purpose_invalid')
            }
            return await this.#runFetch(original, metadata.fetch_metadata, body)
          } catch (error) {
            await this.#emit(original, 'dsh_fetch_error', this.#wireStream.failure(error))
          }
        } })
  }

  #task(operation) {
    const task = Promise.resolve().then(operation)
    this.#tasks.add(task)
    task.catch(error => {
      this.#onFailure(error)
      void this.close()
    }).finally(() => this.#tasks.delete(task))
    return task
  }

  async #respond(header, kind, response, signal) {
    const logical = await this.#logicalReady
    if (logical === null) return this.#emit(header, kind, await responsePayload(response, signal))
    return logical.sendResponse({ header, kind, response, signal,
      emitRaw: (metadata, body) => this.#emit(header, kind, encodeDshFetchPayload(metadata, body)) })
  }

  #runFetch(header, metadata, body) {
    if (!this.#accepting) return this.#task(() => this.#emit(header, 'dsh_fetch_error', {
      code: 'dsh_mux_runtime_draining', message: 'App runtime is closing', details: {},
    }))
    const id = header.correlation_id
    if (this.#usedFetches.has(id)) fail('dsh_mux_fetch_replayed')
    if (this.#cancelledFetches.has(id)) {
      this.#usedFetches.add(id)
      return this.#task(() => this.#emit(header, 'dsh_fetch_error', {
        code: 'dsh_mux_request_cancelled', message: 'Request cancelled before dispatch', details: {},
      }))
    }
    exact(metadata, ['method', 'path', 'headers'])
    const { method, path, headers } = metadata
    if (typeof method !== 'string' || !/^[A-Z]+$/u.test(method)
        || typeof path !== 'string' || !/^\/api(?:\/|\?|$)/u.test(path)
        || path.includes('\\') || path.includes('#')) fail('dsh_mux_fetch_route_invalid')
    const url = new URL(path, 'https://genshin-shell.invalid')
    if (url.pathname !== '/api' && !url.pathname.startsWith('/api/')) fail('dsh_mux_fetch_route_invalid')
    const streamed = typeof body?.stream === 'function'
    const length = body.length
    if (['GET', 'HEAD'].includes(method) && length > 0) fail('dsh_mux_fetch_body_forbidden')
    const control = new AbortController()
    const request = new Request(url, { method, headers: headerPairs(headers), signal: control.signal,
      ...(['GET', 'HEAD'].includes(method) ? {} : streamed
        ? { body: logicalRequestBody(body), duplex: 'half' } : { body }) })
    this.#usedFetches.add(id)
    this.#fetches.set(id, control)
    return this.#task(async () => {
      try {
        const response = await this.#handler.fetch(request, this.#peer)
        await this.#respond(header, 'dsh_fetch_result', response, control.signal)
      } catch (error) {
        await this.#emit(header, 'dsh_fetch_error', this.#wireStream.failure(error))
      } finally { this.#fetches.delete(id) }
    })
  }

  #emit(header, payloadKind, payload, state = null) {
    if (this.#closed) return Promise.resolve()
    return this.#send({
      lane: header.lane,
      payloadKind,
      payload: bounded(payload instanceof Uint8Array
        ? payload : Buffer.from(JSON.stringify(payload), 'utf8')),
      messageId: randomUUID(),
      correlationId: header.correlation_id,
      streamId: header.stream_id,
      streamSeq: state === null ? null : ++state.outSequence,
      trustedClass: header.lane === 'control' ? 'control' : 'normal',
    })
  }

  #streamError(header, error, state) {
    state.pendingCredit = null
    state.control.abort()
    state.uplink.abort()
    this.#streams.delete(header.stream_id)
    return this.#emit(header, 'dsh_stream_error', this.#wireStream.failure(error), state)
  }

  #pullStream(header, state) {
    state.pullBusy = true
    this.#task(async () => {
      try {
        const iterator = await state.ready
        if (state.control.signal.aborted) return
        const item = await iterator.next()
        if (state.control.signal.aborted) return
        if (item.done) {
          state.pendingCredit = null
          this.#streams.delete(header.stream_id)
          state.uplink.abort()
          await this.#emit(header, 'dsh_stream_end', {}, state)
        } else {
          // The peer can consume a physically visible item before the local
          // write callback settles. Buffer one next credit, but never start
          // another iterator read until this send has successfully settled.
          state.pullSending = true
          await this.#emit(header, 'dsh_stream_item',
            Object.hasOwn(item, 'value') ? { value: item.value } : {}, state)
        }
      } catch (error) {
        state.pendingCredit = null
        if (!state.control.signal.aborted) await this.#streamError(header, error, state)
      } finally {
        state.pullSending = false
        state.pullBusy = false
        const next = state.pendingCredit
        state.pendingCredit = null
        if (next !== null && !this.#closed && !state.control.signal.aborted
            && this.#streams.get(header.stream_id) === state) this.#pullStream(next, state)
      }
    })
  }

  /** Admission returns immediately so a pending stream never blocks cancel. */
  accept({ header, payload }) {
    if (this.#closed) fail('dsh_mux_gateway_closed')
    if (typeof header?.correlation_id !== 'string') fail('dsh_mux_correlation_required')
    if (header.payload_kind.startsWith('logical_stream.')) {
      this.#task(async () => {
        const logical = await this.#logicalReady
        if (logical === null || !logical.accept({ header, payload })) fail('dsh_mux_logical_unavailable')
      })
      return
    }
    if (this.#domainTransport?.accept({ header, payload }) === true) return
    if (!this.#accepting && ['dsh_fetch', 'dsh_boot_request', 'dsh_bundle_request', 'dsh_stream_open']
      .includes(header.payload_kind)) {
      const stream = header.payload_kind === 'dsh_stream_open'
      this.#task(() => this.#emit(header, stream ? 'dsh_stream_error'
        : header.lane === 'bundle' ? 'dsh_asset_error' : 'dsh_fetch_error', {
        code: 'dsh_mux_runtime_draining', message: 'App runtime is closing', details: {},
      }, stream ? { outSequence: 0 } : null))
      return
    }
    if (header.lane === 'bundle' && ['dsh_boot_request', 'dsh_bundle_request'].includes(header.payload_kind)) {
      const value = json(payload)
      const boot = header.payload_kind === 'dsh_boot_request'
      exact(value, boot ? [] : ['url'])
      if (!boot && typeof value.url !== 'string') fail('dsh_mux_bundle_url_invalid')
      const id = header.correlation_id
      if (this.#usedFetches.has(id)) fail('dsh_mux_fetch_replayed')
      this.#usedFetches.add(id)
      const control = new AbortController()
      if (this.#cancelledFetches.has(id)) control.abort()
      this.#fetches.set(id, control)
      this.#task(async () => {
        try {
          control.signal.throwIfAborted()
          if (this.#assets === undefined) fail('dsh_mux_assets_unavailable')
          if (boot) await this.#emit(header, 'dsh_boot_result', { injections: await this.#assets.boot() })
          else {
            const response = await this.#assets.bundle(new Request(
              new URL(value.url, 'https://genshin-shell.invalid/'), { signal: control.signal },
            ))
            await this.#respond(header, 'dsh_bundle_result', response, control.signal)
          }
        } catch (error) {
          await this.#emit(header, 'dsh_asset_error', this.#wireStream.failure(error))
        } finally { this.#fetches.delete(id) }
      })
      return
    }
    if (header.lane === 'remote_unary' && header.payload_kind === 'dsh_fetch') {
      const decoded = decodeDshFetchPayload(payload)
      this.#runFetch(header, decoded.metadata, decoded.body)
      return
    }
    if (header.lane === 'control' && header.payload_kind === 'dsh_fetch_cancel') {
      const value = json(payload)
      exact(value, ['correlation_id'])
      if (typeof value.correlation_id !== 'string' || value.correlation_id.length === 0) {
        fail('dsh_mux_correlation_required')
      }
      this.#cancelledFetches.add(value.correlation_id)
      this.#fetches.get(value.correlation_id)?.abort()
      this.#task(() => this.#emit(header, 'dsh_fetch_cancelled', value))
      return
    }
    if (header.lane === 'control' && header.payload_kind === 'dsh_stream_cancel') {
      const value = json(payload)
      exact(value, ['stream_id'])
      if (typeof value.stream_id !== 'string' || value.stream_id.length === 0) {
        fail('dsh_mux_stream_identity_required')
      }
      this.#cancelledStreams.add(value.stream_id)
      const state = this.#streams.get(value.stream_id)
      if (state !== undefined) {
        state.pendingCredit = null
        state.control.abort()
        state.uplink.abort()
        this.#streams.delete(value.stream_id)
        // Do not wait on .return(): an in-flight next() may be pending.
        void state.ready.then(iterator => iterator.return?.()).catch(() => {})
      }
      this.#task(() => this.#emit(header, 'dsh_stream_cancelled', value))
      return
    }
    if (header.lane !== 'remote_stream' || typeof header.stream_id !== 'string') {
      fail('dsh_mux_packet_unsupported')
    }
    const id = header.stream_id
    if (header.payload_kind === 'dsh_stream_open') {
      if (this.#cancelledStreams.has(id)) {
        if (this.#usedStreams.has(id)) fail('dsh_mux_stream_replayed')
        this.#usedStreams.add(id)
        const state = { outSequence: 0 }
        this.#task(() => this.#emit(header, 'dsh_stream_error', {
          code: 'dsh_mux_request_cancelled', message: 'Stream cancelled before dispatch', details: {},
        }, state))
        return
      }
      const value = json(payload)
      exact(value, ['endpoint', 'payload', 'has_uplink'])
      if (typeof value.endpoint !== 'string' || value.endpoint.length === 0
          || typeof value.has_uplink !== 'boolean') fail('dsh_mux_stream_open_invalid')
      if (this.#usedStreams.has(id)) fail('dsh_mux_stream_replayed')
      this.#usedStreams.add(id)
      const state = {
        control: new AbortController(), uplink: new Uplink(), outSequence: 0,
        pullBusy: false, pullSending: false, pendingCredit: null, ready: null,
      }
      if (!value.has_uplink) state.uplink.end()
      this.#streams.set(id, state)
      state.ready = Promise.resolve().then(() => this.#wireStream.open(
        value.endpoint, value.payload, state.uplink, this.#peer, state.control.signal,
      )).then(source => source[Symbol.asyncIterator]())
      // Opening errors belong to DSH's stable wire failure mapper.
      this.#task(async () => {
        try {
          await state.ready
          if (!state.control.signal.aborted) await this.#emit(header, 'dsh_stream_opened', {}, state)
        } catch (error) {
          if (!state.control.signal.aborted) await this.#streamError(header, error, state)
        }
      })
      return
    }
    const state = this.#streams.get(id)
    if (state === undefined || state.control.signal.aborted) {
      // Controls overtake normal-lane frames. Previously queued credit/uplink
      // for a retired stream is harmless; it must not poison other Sessions.
      if (this.#usedStreams.has(id) || this.#cancelledStreams.has(id)) return
      fail('dsh_mux_stream_not_open')
    }
    if (header.payload_kind === 'dsh_stream_credit') {
      const value = json(payload)
      exact(value, ['items'])
      if (value.items !== 1) fail('dsh_mux_stream_credit_invalid')
      if (state.pullBusy) {
        if (!state.pullSending || state.pendingCredit !== null) fail('dsh_mux_stream_credit_invalid')
        state.pendingCredit = header
        return
      }
      this.#pullStream(header, state)
      return
    }
    if (header.payload_kind === 'dsh_stream_item') {
      const value = json(payload)
      if (!record(value) || Object.keys(value).some(key => key !== 'value')) {
        fail('dsh_mux_uplink_item_invalid')
      }
      state.uplink.offer(value.value, () => this.#task(() => this.#emit(
        header, 'dsh_stream_ack', { sequence: header.stream_seq }, state,
      )))
      return
    }
    if (header.payload_kind === 'dsh_stream_end') {
      exact(json(payload), [])
      state.uplink.end()
      this.#task(() => this.#emit(header, 'dsh_stream_ack', { sequence: header.stream_seq }, state))
      return
    }
    fail('dsh_mux_packet_unsupported')
  }

  async whenIdle() {
    while (this.#tasks.size > 0) await Promise.allSettled([...this.#tasks])
  }

  async pauseAdmissions() {
    this.#accepting = false
    for (const control of this.#fetches.values()) control.abort()
    for (const state of this.#streams.values()) {
      state.pendingCredit = null
      state.control.abort()
      state.uplink.abort()
      void state.ready.then(iterator => iterator.return?.()).catch(() => {})
    }
    this.#streams.clear()
    await (await this.#logicalReady)?.close()
  }

  snapshot() {
    return Object.freeze({ productionOwner: PRODUCTION_OWNER, closed: this.#closed,
      activeFetches: this.#fetches.size, activeStreams: this.#streams.size,
      pendingTasks: this.#tasks.size })
  }

  async close() {
    if (this.#closed) return
    this.#closed = true
    this.#domainTransport?.close()
    for (const control of this.#fetches.values()) control.abort()
    for (const state of this.#streams.values()) {
      state.pendingCredit = null
      state.control.abort()
      state.uplink.abort()
      void state.ready.then(iterator => iterator.return?.()).catch(() => {})
    }
    this.#fetches.clear()
    this.#streams.clear()
    await (await this.#logicalReady)?.close()
    await this.#peer.dispose()
  }
}
