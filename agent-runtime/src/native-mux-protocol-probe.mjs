/** Cross-language golden vectors and adversarial checks for mux v1. */
import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'

import {
  MAX_MUX_BODY_BYTES,
  MAX_MUX_HEADER_BYTES,
  MAX_MUX_PAYLOAD_BYTES,
  MAX_MUX_WIRE_BYTES,
  NativeMuxProtocolError,
  decodeMuxPacket,
  encodeMuxPacket,
  makeMuxHeader,
  validateMuxPacket,
} from './native-mux-protocol.mjs'

const fixture = JSON.parse(await readFile(
  new URL('../native-mux-v1-vectors.json', import.meta.url),
  'utf8',
))
assert.deepEqual(fixture.limits, {
  header_bytes: MAX_MUX_HEADER_BYTES,
  body_bytes: MAX_MUX_BODY_BYTES,
  payload_bytes: MAX_MUX_PAYLOAD_BYTES,
  wire_bytes: MAX_MUX_WIRE_BYTES,
})

function rawPacket(headerBytes, payload) {
  const wire = Buffer.allocUnsafe(8 + headerBytes.length + payload.length)
  wire.writeUInt32BE(4 + headerBytes.length + payload.length, 0)
  wire.writeUInt32BE(headerBytes.length, 4)
  headerBytes.copy(wire, 8)
  payload.copy(wire, 8 + headerBytes.length)
  return wire
}

function rejects(code, operation) {
  let caught
  try {
    operation()
  } catch (error) {
    caught = error
  }
  assert.ok(caught instanceof NativeMuxProtocolError)
  assert.equal(caught.code, code)
  return caught
}

for (const vector of fixture.vectors) {
  const payload = Buffer.from(vector.payload_base64, 'base64')
  const expected = Buffer.from(vector.packet_base64, 'base64')
  assert.deepEqual(encodeMuxPacket(vector.header, payload), expected)
  const decoded = decodeMuxPacket(expected)
  assert.deepEqual(decoded.header, vector.header)
  assert.deepEqual(decoded.payload, payload)
}

const legacy = fixture.vectors.find(vector => vector.name === 'python_legacy_frame')
const legacyPayload = Buffer.from(legacy.payload_base64, 'base64')
const legacyWire = Buffer.from(legacy.packet_base64, 'base64')
assert.deepEqual(encodeMuxPacket({ ...legacy.header }, Uint8Array.from(legacyPayload)), legacyWire)
const decodedLegacy = decodeMuxPacket(legacyWire)
assert.deepEqual(decodedLegacy.payload, legacyPayload)
assert.throws(() => { decodedLegacy.header.lane = 'control' }, TypeError)
assert.deepEqual(decodedLegacy.header, legacy.header)
assert.deepEqual(encodeMuxPacket(decodedLegacy.header, decodedLegacy.payload), legacyWire)

for (const [field, value, code] of [
  ['protocol', 'other', 'unknown_protocol'],
  ['version', 2, 'unknown_version'],
  ['lane', 'other', 'unknown_lane'],
  ['sender_role', 'other', 'unknown_sender_role'],
  ['priority', 'other', 'unknown_priority'],
]) {
  rejects(code, () => validateMuxPacket({ ...legacy.header, [field]: value }, legacyPayload))
}
rejects('unknown_header_field', () => validateMuxPacket(
  { ...legacy.header, extra: true }, legacyPayload,
))
const missing = { ...legacy.header }
delete missing.priority
rejects('missing_header_field', () => validateMuxPacket(missing, legacyPayload))

const headerLength = legacyWire.readUInt32BE(4)
const canonical = legacyWire.subarray(8, 8 + headerLength)
const duplicate = Buffer.concat([canonical.subarray(0, -1), Buffer.from(',"version":1}', 'utf8')])
rejects('duplicate_header_key', () => decodeMuxPacket(rawPacket(duplicate, legacyPayload)))
rejects('invalid_header_utf8', () => decodeMuxPacket(rawPacket(
  Buffer.from([0x7b, 0x22, 0xff, 0x22, 0x7d]), legacyPayload,
)))
rejects('noncanonical_header_json', () => decodeMuxPacket(rawPacket(
  Buffer.from(JSON.stringify(legacy.header, null, 1), 'utf8'), legacyPayload,
)))

const wrongBody = Buffer.from(legacyWire)
wrongBody.writeUInt32BE(legacyWire.length, 0)
rejects('mux_body_length_mismatch', () => decodeMuxPacket(wrongBody))
const wrongHeader = Buffer.from(legacyWire)
wrongHeader.writeUInt32BE(legacyWire.length, 4)
rejects('mux_header_length_mismatch', () => decodeMuxPacket(wrongHeader))
rejects('mux_packet_truncated', () => decodeMuxPacket(legacyWire.subarray(0, 7)))

const boundaryBodyPrefix = Buffer.alloc(4)
boundaryBodyPrefix.writeUInt32BE(MAX_MUX_BODY_BYTES)
rejects('mux_packet_truncated', () => decodeMuxPacket(boundaryBodyPrefix))
const oversizedBodyPrefix = Buffer.alloc(4)
oversizedBodyPrefix.writeUInt32BE(MAX_MUX_BODY_BYTES + 1)
rejects('mux_packet_too_large', () => decodeMuxPacket(oversizedBodyPrefix))
const uint32BodyPrefix = Buffer.alloc(4)
uint32BodyPrefix.writeUInt32BE(0xffffffff)
rejects('mux_packet_too_large', () => decodeMuxPacket(uint32BodyPrefix))

rejects('invalid_header_json', () => decodeMuxPacket(rawPacket(
  Buffer.alloc(MAX_MUX_HEADER_BYTES, 0x20), Buffer.alloc(0),
)))
rejects('mux_header_too_large', () => decodeMuxPacket(rawPacket(
  Buffer.alloc(MAX_MUX_HEADER_BYTES + 1, 0x20), Buffer.alloc(0),
)))
rejects('payload_length_mismatch', () => validateMuxPacket(
  { ...fixture.vectors[2].header, payload_length: MAX_MUX_PAYLOAD_BYTES }, Buffer.alloc(0),
))
rejects('mux_packet_too_large', () => validateMuxPacket(
  { ...fixture.vectors[2].header, payload_length: MAX_MUX_PAYLOAD_BYTES + 1 }, Buffer.alloc(0),
))

const wrongLength = { ...legacy.header, payload_length: legacyPayload.length + 1 }
const wrongLengthBytes = Buffer.from(JSON.stringify(
  Object.fromEntries(Object.entries(wrongLength).sort(([left], [right]) => (
    left < right ? -1 : (left > right ? 1 : 0)
  ))),
), 'utf8')
rejects('payload_length_mismatch', () => decodeMuxPacket(rawPacket(wrongLengthBytes, legacyPayload)))
const wrongDigest = { ...legacy.header, payload_sha256: '0'.repeat(64) }
const wrongDigestBytes = Buffer.from(JSON.stringify(
  Object.fromEntries(Object.entries(wrongDigest).sort(([left], [right]) => (
    left < right ? -1 : (left > right ? 1 : 0)
  ))),
), 'utf8')
rejects('payload_sha256_mismatch', () => decodeMuxPacket(rawPacket(wrongDigestBytes, legacyPayload)))

const stream = fixture.vectors.find(vector => vector.name === 'node_remote_stream')
const streamPayload = Buffer.from(stream.payload_base64, 'base64')
rejects('stream_identity_required', () => validateMuxPacket(
  { ...stream.header, stream_id: null }, streamPayload,
))
rejects('stream_identity_forbidden', () => validateMuxPacket(
  { ...legacy.header, stream_id: 'stream-1', stream_seq: 1 }, legacyPayload,
))

const mutable = Buffer.from('mutable')
const built = makeMuxHeader({
  lane: 'bundle',
  sender_role: 'runtime',
  connection_generation: 5,
  connection_id: 'connection-5',
  runtime_incarnation: 'runtime-5',
  global_seq: 8,
  lane_seq: 2,
  message_id: 'message-8',
  correlation_id: 'bundle-1',
  stream_id: null,
  stream_seq: null,
  payload_kind: 'bundle_chunk',
  payload: mutable,
  priority: 'normal',
})
const owned = validateMuxPacket(built, mutable)
mutable.fill(0)
assert.equal(owned.payload.toString('utf8'), 'mutable')

const secret = Buffer.from('MUX_SECRET_MUST_NOT_APPEAR', 'ascii')
const secretHeader = makeMuxHeader({
  lane: 'legacy_turn',
  sender_role: 'host',
  connection_generation: 1,
  connection_id: 'connection-secret',
  runtime_incarnation: 'runtime-secret',
  global_seq: 1,
  lane_seq: 1,
  message_id: 'message-secret',
  correlation_id: null,
  stream_id: null,
  stream_seq: null,
  payload_kind: 'legacy_frame',
  payload: secret,
  priority: 'normal',
})
const error = rejects('payload_sha256_mismatch', () => validateMuxPacket(
  { ...secretHeader, payload_sha256: '0'.repeat(64) }, secret,
))
assert.equal(`${String(error)}\n${error.stack}\n${JSON.stringify(error)}`.includes(secret.toString()), false)

process.stdout.write(`${JSON.stringify({
  contract: fixture.contract,
  status: 'passed',
  vectors: fixture.vectors.length,
  producers: [...new Set(fixture.vectors.map(vector => vector.producer))].sort(),
})}\n`)
