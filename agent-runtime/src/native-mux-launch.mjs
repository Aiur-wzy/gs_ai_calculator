/**
 * Qualification-only inherited launch contract for the native mux carrier.
 *
 * The production runtime still owns the distinct genshin-native-launch-v1
 * contract.  This module selects mux before the first pipe byte is decoded;
 * it never sniffs or falls back to the legacy codec.
 */
import { TextDecoder } from 'node:util'

import { canonicalEnvelopeJson } from './bridge-envelope.mjs'

export const MUX_LAUNCH_CONTRACT = 'genshin-native-mux-launch-v1'
export const MUX_TRANSPORT_CODEC = 'genshin-native-pipe-mux-v1'
export const MAX_MUX_LAUNCH_BYTES = 4096
export const PRODUCTION_OWNER = false

const JSON_SAFE_INTEGER_MAX = 2 ** 53 - 1
const PIPE_NAME = /^\\\\\.\\pipe\\genshin-native-[0-9a-f]{64}$/u
const NONCE = /^[A-Za-z0-9_-]{32,128}$/u
const IDENTITY = /^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$/u
const FIELDS = Object.freeze([
  'contract',
  'pipe_name',
  'nonce',
  'transport_codec',
  'connection_generation',
  'connection_id',
  'runtime_incarnation',
])

export class NativeMuxLaunchError extends Error {
  constructor(code) {
    super(code)
    this.name = 'NativeMuxLaunchError'
    this.code = code
  }
}

function fail(code) {
  throw new NativeMuxLaunchError(code)
}

function validate(value) {
  if (value === null || Array.isArray(value) || typeof value !== 'object') {
    fail('mux_launch_contract_shape')
  }
  const keys = Object.keys(value).sort()
  const fields = [...FIELDS].sort()
  if (keys.length !== fields.length || keys.some((key, index) => key !== fields[index])) {
    fail('mux_launch_contract_shape')
  }
  if (value.contract !== MUX_LAUNCH_CONTRACT) fail('mux_launch_contract_version')
  if (value.transport_codec !== MUX_TRANSPORT_CODEC) fail('mux_launch_transport_codec')
  if (typeof value.pipe_name !== 'string' || !PIPE_NAME.test(value.pipe_name)) {
    fail('mux_launch_pipe_name')
  }
  if (typeof value.nonce !== 'string' || !NONCE.test(value.nonce)) fail('mux_launch_nonce')
  if (!Number.isSafeInteger(value.connection_generation)
      || value.connection_generation < 1
      || value.connection_generation > JSON_SAFE_INTEGER_MAX) {
    fail('mux_launch_connection_generation')
  }
  if (typeof value.connection_id !== 'string' || !IDENTITY.test(value.connection_id)) {
    fail('mux_launch_connection_id')
  }
  if (typeof value.runtime_incarnation !== 'string'
      || !IDENTITY.test(value.runtime_incarnation)) {
    fail('mux_launch_runtime_incarnation')
  }
  return Object.freeze({
    pipeName: value.pipe_name,
    nonce: value.nonce,
    transportCodec: value.transport_codec,
    connectionGeneration: value.connection_generation,
    connectionId: value.connection_id,
    runtimeIncarnation: value.runtime_incarnation,
  })
}

function packetBody(value) {
  let body
  try {
    body = Buffer.from(canonicalEnvelopeJson(value), 'utf8')
  } catch {
    fail('mux_launch_contract_json')
  }
  if (body.length < 1 || body.length > MAX_MUX_LAUNCH_BYTES) {
    fail('mux_launch_contract_size')
  }
  return body
}

export function encodeMuxLaunchContract({
  pipeName,
  nonce,
  connectionGeneration,
  connectionId,
  runtimeIncarnation,
  transportCodec = MUX_TRANSPORT_CODEC,
}) {
  const value = {
    contract: MUX_LAUNCH_CONTRACT,
    pipe_name: pipeName,
    nonce,
    transport_codec: transportCodec,
    connection_generation: connectionGeneration,
    connection_id: connectionId,
    runtime_incarnation: runtimeIncarnation,
  }
  validate(value)
  const body = packetBody(value)
  const prefix = Buffer.allocUnsafe(4)
  prefix.writeUInt32BE(body.length)
  return Buffer.concat([prefix, body])
}

export function decodeMuxLaunchContract(packet) {
  if (!Buffer.isBuffer(packet) && !(packet instanceof Uint8Array)) {
    fail('mux_launch_contract_bytes')
  }
  const raw = Buffer.from(packet)
  if (raw.length < 5) fail('mux_launch_contract_truncated')
  const length = raw.readUInt32BE(0)
  if (length < 1 || length > MAX_MUX_LAUNCH_BYTES || raw.length !== length + 4) {
    fail('mux_launch_contract_length')
  }
  let text
  let value
  try {
    // Keep a leading BOM in the decoded text so canonical JSON validation
    // rejects it exactly as Python does.  TextDecoder otherwise strips the
    // marker and the two runtimes disagree about the same inherited bytes.
    text = new TextDecoder('utf-8', { fatal: true, ignoreBOM: true }).decode(raw.subarray(4))
    value = JSON.parse(text)
  } catch {
    fail('mux_launch_contract_json')
  }
  const checked = validate(value)
  if (packetBody(value).toString('utf8') !== text) fail('mux_launch_contract_noncanonical')
  return checked
}
