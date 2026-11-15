/**
 * Strict, transport-free primitives for the native pipe mux v1 envelope.
 *
 * Wire priority is untrusted metadata, not an authority grant.  A receiver
 * must derive effective scheduling priority from its own trusted classifier.
 */
import { createHash, timingSafeEqual } from 'node:crypto'
import { TextDecoder } from 'node:util'

import { canonicalEnvelopeJson } from './bridge-envelope.mjs'

export const MUX_PROTOCOL = 'genshin-native-pipe-mux'
export const MUX_VERSION = 1
export const JSON_SAFE_INTEGER_MAX = 2 ** 53 - 1
export const UINT32_MAX = 2 ** 32 - 1
export const MAX_MUX_HEADER_BYTES = 64 * 1024
export const MAX_MUX_BODY_BYTES = 128 * 1024 * 1024
// The body is uint32 header length + header + payload.  Reserving the full
// header budget gives one independently enforceable payload ceiling.
export const MAX_MUX_PAYLOAD_BYTES = MAX_MUX_BODY_BYTES - 4 - MAX_MUX_HEADER_BYTES
export const MAX_MUX_WIRE_BYTES = 4 + MAX_MUX_BODY_BYTES
// A future streaming reader MUST reject a body prefix above
// MAX_MUX_BODY_BYTES before reading or allocating that body.

export const MUX_LANES = Object.freeze([
  'control',
  'legacy_turn',
  'remote_unary',
  'remote_stream',
  'bundle',
])
export const MUX_SENDER_ROLES = Object.freeze(['host', 'runtime'])
export const MUX_PRIORITIES = Object.freeze(['normal', 'urgent', 'control'])
export const MUX_HEADER_FIELDS = Object.freeze([
  'protocol',
  'version',
  'lane',
  'sender_role',
  'connection_generation',
  'connection_id',
  'runtime_incarnation',
  'global_seq',
  'lane_seq',
  'message_id',
  'correlation_id',
  'stream_id',
  'stream_seq',
  'payload_kind',
  'payload_length',
  'payload_sha256',
  'priority',
])

const IDENTITY = /^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$/
const PAYLOAD_KIND = /^[A-Za-z][A-Za-z0-9_.:-]{0,63}$/
const SHA256 = /^[0-9a-f]{64}$/
const HEADER_FIELD_SET = new Set(MUX_HEADER_FIELDS)
const LANE_SET = new Set(MUX_LANES)
const SENDER_ROLE_SET = new Set(MUX_SENDER_ROLES)
const PRIORITY_SET = new Set(MUX_PRIORITIES)
const UTF8_DECODER = new TextDecoder('utf-8', { fatal: true, ignoreBOM: true })

export class NativeMuxProtocolError extends Error {
  constructor(code) {
    super(code)
    this.name = 'NativeMuxProtocolError'
    this.code = code
  }
}

function reject(code) {
  throw new NativeMuxProtocolError(code)
}

function plainObject(value) {
  if (value === null || typeof value !== 'object' || Array.isArray(value)) return false
  const prototype = Object.getPrototypeOf(value)
  if (prototype !== Object.prototype && prototype !== null) return false
  const ownKeys = Reflect.ownKeys(value)
  if (ownKeys.some(key => typeof key !== 'string')) return false
  return ownKeys.every((key) => {
    const descriptor = Object.getOwnPropertyDescriptor(value, key)
    return descriptor !== undefined
      && descriptor.enumerable === true
      && Object.hasOwn(descriptor, 'value')
  })
}

function boundedBytes(payload, maximumBytes) {
  if (!Buffer.isBuffer(payload) && !(payload instanceof Uint8Array)) {
    reject('invalid_payload_bytes')
  }
  if (payload.byteLength > maximumBytes) reject('mux_packet_too_large')
  return Buffer.from(payload)
}

function identity(value, code, { nullable = false } = {}) {
  if (nullable && value === null) return
  if (typeof value !== 'string' || !IDENTITY.test(value)) reject(code)
}

function positiveSafeInteger(value, code) {
  if (!Number.isSafeInteger(value) || value < 1) reject(code)
}

function validatedHeader(header) {
  if (!plainObject(header)) reject('invalid_header_object')
  const fields = Object.keys(header)
  if (fields.some(field => !HEADER_FIELD_SET.has(field))) reject('unknown_header_field')
  if (fields.length !== MUX_HEADER_FIELDS.length
      || MUX_HEADER_FIELDS.some(field => !Object.hasOwn(header, field))) {
    reject('missing_header_field')
  }

  const value = Object.fromEntries(MUX_HEADER_FIELDS.map(field => [field, header[field]]))
  if (value.protocol !== MUX_PROTOCOL) reject('unknown_protocol')
  if (!Number.isInteger(value.version) || value.version !== MUX_VERSION) reject('unknown_version')
  if (typeof value.lane !== 'string' || !LANE_SET.has(value.lane)) reject('unknown_lane')
  if (typeof value.sender_role !== 'string' || !SENDER_ROLE_SET.has(value.sender_role)) {
    reject('unknown_sender_role')
  }
  if (typeof value.priority !== 'string' || !PRIORITY_SET.has(value.priority)) {
    reject('unknown_priority')
  }

  positiveSafeInteger(value.connection_generation, 'invalid_connection_generation')
  identity(value.connection_id, 'invalid_connection_id')
  identity(value.runtime_incarnation, 'invalid_runtime_incarnation')
  positiveSafeInteger(value.global_seq, 'invalid_global_seq')
  positiveSafeInteger(value.lane_seq, 'invalid_lane_seq')
  identity(value.message_id, 'invalid_message_id')
  identity(value.correlation_id, 'invalid_correlation_id', { nullable: true })
  identity(value.stream_id, 'invalid_stream_id', { nullable: true })

  if (value.lane === 'remote_stream') {
    if (value.stream_id === null) reject('stream_identity_required')
    positiveSafeInteger(value.stream_seq, 'invalid_stream_seq')
  } else if (value.stream_id !== null || value.stream_seq !== null) {
    reject('stream_identity_forbidden')
  }

  if (typeof value.payload_kind !== 'string' || !PAYLOAD_KIND.test(value.payload_kind)) {
    reject('invalid_payload_kind')
  }
  if (!Number.isInteger(value.payload_length)
      || value.payload_length < 0
      || value.payload_length > UINT32_MAX) {
    reject('invalid_payload_length')
  }
  if (value.payload_length > MAX_MUX_PAYLOAD_BYTES) reject('mux_packet_too_large')
  if (typeof value.payload_sha256 !== 'string' || !SHA256.test(value.payload_sha256)) {
    reject('invalid_payload_sha256')
  }
  return value
}

function assertNoDuplicateTopLevelKeys(text) {
  const keys = new Set()
  let depth = 0
  let inString = false
  let escaped = false
  let tokenStart = -1
  let expectingKey = false

  for (let index = 0; index < text.length; index += 1) {
    const char = text[index]
    if (inString) {
      if (escaped) {
        escaped = false
      } else if (char === '\\') {
        escaped = true
      } else if (char === '"') {
        inString = false
        if (depth === 1 && expectingKey) {
          let key
          try {
            key = JSON.parse(text.slice(tokenStart, index + 1))
          } catch {
            return
          }
          if (keys.has(key)) reject('duplicate_header_key')
          keys.add(key)
          expectingKey = false
        }
      }
      continue
    }
    if (char === '"') {
      inString = true
      tokenStart = index
    } else if (char === '{' || char === '[') {
      depth += 1
      if (depth === 1 && char === '{') expectingKey = true
    } else if (char === '}' || char === ']') {
      depth -= 1
    } else if (char === ',' && depth === 1) {
      expectingKey = true
    }
  }
}

function canonicalHeaderJson(header) {
  let text
  try {
    text = canonicalEnvelopeJson(header)
  } catch {
    reject('invalid_header_json')
  }
  const encoded = Buffer.from(text, 'utf8')
  if (encoded.length > MAX_MUX_HEADER_BYTES) reject('mux_header_too_large')
  return encoded
}

function digest(payload) {
  return createHash('sha256').update(payload).digest('hex')
}

function equalDigest(left, right) {
  return timingSafeEqual(Buffer.from(left, 'ascii'), Buffer.from(right, 'ascii'))
}

export function makeMuxHeader({
  lane,
  sender_role: senderRole,
  connection_generation: connectionGeneration,
  connection_id: connectionId,
  runtime_incarnation: runtimeIncarnation,
  global_seq: globalSeq,
  lane_seq: laneSeq,
  message_id: messageId,
  correlation_id: correlationId,
  stream_id: streamId,
  stream_seq: streamSeq,
  payload_kind: payloadKind,
  payload,
  priority,
}) {
  const raw = boundedBytes(payload, MAX_MUX_PAYLOAD_BYTES)
  return validatedHeader({
    protocol: MUX_PROTOCOL,
    version: MUX_VERSION,
    lane,
    sender_role: senderRole,
    connection_generation: connectionGeneration,
    connection_id: connectionId,
    runtime_incarnation: runtimeIncarnation,
    global_seq: globalSeq,
    lane_seq: laneSeq,
    message_id: messageId,
    correlation_id: correlationId,
    stream_id: streamId,
    stream_seq: streamSeq,
    payload_kind: payloadKind,
    payload_length: raw.length,
    payload_sha256: digest(raw),
    priority,
  })
}

export function validateMuxPacket(header, payload) {
  const raw = boundedBytes(payload, MAX_MUX_PAYLOAD_BYTES)
  const value = validatedHeader(header)
  if (value.payload_length !== raw.length) reject('payload_length_mismatch')
  if (!equalDigest(value.payload_sha256, digest(raw))) reject('payload_sha256_mismatch')
  return Object.freeze({ header: Object.freeze(value), payload: raw })
}

export function encodeMuxPacket(header, payload) {
  const packet = validateMuxPacket(header, payload)
  const encodedHeader = canonicalHeaderJson(packet.header)
  const bodyLength = 4 + encodedHeader.length + packet.payload.length
  if (encodedHeader.length < 1) reject('mux_header_length_mismatch')
  if (bodyLength > MAX_MUX_BODY_BYTES) reject('mux_packet_too_large')
  const wire = Buffer.allocUnsafe(8 + encodedHeader.length + packet.payload.length)
  wire.writeUInt32BE(bodyLength, 0)
  wire.writeUInt32BE(encodedHeader.length, 4)
  encodedHeader.copy(wire, 8)
  packet.payload.copy(wire, 8 + encodedHeader.length)
  return wire
}

export function decodeMuxPacket(wire) {
  const raw = boundedBytes(wire, MAX_MUX_WIRE_BYTES)
  if (raw.length < 4) reject('mux_packet_truncated')
  const bodyLength = raw.readUInt32BE(0)
  if (bodyLength > MAX_MUX_BODY_BYTES) reject('mux_packet_too_large')
  if (raw.length < 8) reject('mux_packet_truncated')
  if (bodyLength !== raw.length - 4) reject('mux_body_length_mismatch')
  const headerLength = raw.readUInt32BE(4)
  if (headerLength > MAX_MUX_HEADER_BYTES) reject('mux_header_too_large')
  if (headerLength < 1 || headerLength > bodyLength - 4) reject('mux_header_length_mismatch')
  const payloadLength = bodyLength - 4 - headerLength
  if (payloadLength > MAX_MUX_PAYLOAD_BYTES) reject('mux_packet_too_large')

  const headerEnd = 8 + headerLength
  const encodedHeader = raw.subarray(8, headerEnd)
  let text
  try {
    text = UTF8_DECODER.decode(encodedHeader)
  } catch {
    reject('invalid_header_utf8')
  }
  assertNoDuplicateTopLevelKeys(text)
  let header
  try {
    header = JSON.parse(text)
  } catch {
    reject('invalid_header_json')
  }
  if (!plainObject(header)) reject('invalid_header_object')
  if (!canonicalHeaderJson(header).equals(encodedHeader)) reject('noncanonical_header_json')
  return validateMuxPacket(header, raw.subarray(headerEnd))
}
