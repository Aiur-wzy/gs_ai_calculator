/** Cross-language golden-vector and rejection probe for bridge hashing. */
import { readFile } from 'node:fs/promises'

import {
  canonicalEnvelopeJson,
  canonicalPayloadHash,
  rawJsonHash,
} from './bridge-envelope.mjs'

const vectors = JSON.parse(await readFile(
  new URL('../bridge-vectors.json', import.meta.url),
  'utf8',
))

for (const vector of vectors.payloads) {
  if (canonicalPayloadHash(vector.value) !== vector.sha256) {
    throw new Error(`Payload hash mismatch for ${vector.name}`)
  }
}
for (const vector of vectors.raw_arguments) {
  if (rawJsonHash(vector.raw) !== vector.sha256) {
    throw new Error(`Raw argument hash mismatch for ${vector.name}`)
  }
}

const rejected = [
  () => canonicalEnvelopeJson({ value: 1.5 }),
  () => canonicalEnvelopeJson({ value: Number.MAX_SAFE_INTEGER + 1 }),
  () => canonicalEnvelopeJson({ 中文: 'key' }),
  () => canonicalEnvelopeJson({ value: '\ud800' }),
  () => rawJsonHash('\ud800'),
  () => canonicalEnvelopeJson(new Array(1)),
  () => {
    const value = []
    value.extra = true
    return canonicalEnvelopeJson(value)
  },
  () => canonicalEnvelopeJson({ [Symbol('hidden')]: true }),
  () => {
    const value = {}
    Object.defineProperty(value, 'hidden', { value: true, enumerable: false })
    return canonicalEnvelopeJson(value)
  },
  () => {
    const value = {}
    Object.defineProperty(value, 'getter', {
      enumerable: true,
      get() { throw new Error('getter_must_not_execute') },
    })
    return canonicalEnvelopeJson(value)
  },
]
for (const operation of rejected) {
  let didReject = false
  try {
    operation()
  } catch (error) {
    didReject = error instanceof TypeError
  }
  if (!didReject) throw new Error('Bridge rejection vector was accepted')
}

process.stdout.write(`${JSON.stringify({
  contract: vectors.contract,
  status: 'passed',
  payloadVectors: vectors.payloads.length,
  rawArgumentVectors: vectors.raw_arguments.length,
  rejectionVectors: rejected.length,
})}\n`)
