/** Cross-language primitives for the deliberately narrow bridge envelope. */
import { createHash } from 'node:crypto'

export const JSON_SAFE_INTEGER_MAX = 2 ** 53 - 1
export const MAX_FRAME_BYTES = 1024 * 1024
export const MAX_TOOL_ARGUMENT_BYTES = 256 * 1024

const PROTOCOL_KEY = /^[A-Za-z_][A-Za-z0-9_.:-]{0,127}$/

function assertUnicodeScalarString(value, label) {
  for (let index = 0; index < value.length; index += 1) {
    const unit = value.charCodeAt(index)
    if (unit >= 0xd800 && unit <= 0xdbff) {
      const next = value.charCodeAt(index + 1)
      if (!(next >= 0xdc00 && next <= 0xdfff)) {
        throw new TypeError(`${label} contains an unpaired UTF-16 surrogate`)
      }
      index += 1
    } else if (unit >= 0xdc00 && unit <= 0xdfff) {
      throw new TypeError(`${label} contains an unpaired UTF-16 surrogate`)
    }
  }
}

function canonicalPart(value, depth = 0) {
  if (depth > 64) throw new TypeError('Bridge JSON nesting exceeds the limit')
  if (value === null) return 'null'
  if (typeof value === 'boolean') return value ? 'true' : 'false'
  if (typeof value === 'string') {
    assertUnicodeScalarString(value, 'Bridge string')
    return JSON.stringify(value)
  }
  if (typeof value === 'number') {
    if (!Number.isSafeInteger(value)) {
      throw new TypeError('Bridge numbers must be JavaScript safe integers')
    }
    return JSON.stringify(value)
  }
  if (Array.isArray(value)) {
    const ownKeys = Reflect.ownKeys(value)
    if (ownKeys.some(key => typeof key !== 'string')) {
      throw new TypeError('Bridge arrays cannot contain symbol properties')
    }
    const elementKeys = ownKeys.filter(key => key !== 'length')
    if (elementKeys.length !== value.length) {
      throw new TypeError('Bridge arrays must be dense and cannot contain extra properties')
    }
    const encoded = []
    for (let index = 0; index < value.length; index += 1) {
      const key = String(index)
      const descriptor = Object.getOwnPropertyDescriptor(value, key)
      if (descriptor === undefined
          || descriptor.enumerable !== true
          || !Object.hasOwn(descriptor, 'value')) {
        throw new TypeError('Bridge array elements must be enumerable data properties')
      }
      encoded.push(canonicalPart(descriptor.value, depth + 1))
    }
    return `[${encoded.join(',')}]`
  }
  if (typeof value !== 'object') throw new TypeError('Value is outside bridge JSON')
  const prototype = Object.getPrototypeOf(value)
  if (prototype !== Object.prototype && prototype !== null) {
    throw new TypeError('Bridge objects must be plain JSON objects')
  }
  const ownKeys = Reflect.ownKeys(value)
  if (ownKeys.some(key => typeof key !== 'string')) {
    throw new TypeError('Bridge objects cannot contain symbol properties')
  }
  const keys = ownKeys.sort()
  const values = new Map()
  for (const key of keys) {
    if (!PROTOCOL_KEY.test(key)) {
      throw new TypeError('Bridge object keys must use the ASCII protocol grammar')
    }
    const descriptor = Object.getOwnPropertyDescriptor(value, key)
    if (descriptor === undefined
        || descriptor.enumerable !== true
        || !Object.hasOwn(descriptor, 'value')) {
      throw new TypeError('Bridge object fields must be enumerable data properties')
    }
    values.set(key, descriptor.value)
  }
  return `{${keys.map(key => `${JSON.stringify(key)}:${canonicalPart(values.get(key), depth + 1)}`).join(',')}}`
}

export function canonicalEnvelopeJson(value) {
  const text = canonicalPart(value)
  if (Buffer.byteLength(text, 'utf8') > MAX_FRAME_BYTES) {
    throw new TypeError('Bridge JSON exceeds the frame limit')
  }
  return text
}

export function canonicalPayloadHash(payload) {
  if (payload === null || Array.isArray(payload) || typeof payload !== 'object') {
    throw new TypeError('Bridge payload must be a JSON object')
  }
  return createHash('sha256').update(canonicalEnvelopeJson(payload), 'utf8').digest('hex')
}

export function rawJsonHash(raw, maximumBytes = MAX_TOOL_ARGUMENT_BYTES) {
  if (typeof raw !== 'string' || raw.length === 0) {
    throw new TypeError('Raw JSON must be a non-empty string')
  }
  assertUnicodeScalarString(raw, 'Raw JSON')
  const size = Buffer.byteLength(raw, 'utf8')
  if (!Number.isSafeInteger(maximumBytes) || maximumBytes < 1 || size > maximumBytes) {
    throw new TypeError('Raw JSON exceeds the argument limit')
  }
  return createHash('sha256').update(raw, 'utf8').digest('hex')
}
