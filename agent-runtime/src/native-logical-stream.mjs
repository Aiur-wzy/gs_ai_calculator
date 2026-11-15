/** Transport-free qualification model for large native Agent payloads. */
import { createHash, randomUUID } from 'node:crypto'
import { lstat, open as openFile, realpath, rename, unlink } from 'node:fs/promises'
import { join } from 'node:path'

export const LOGICAL_STREAM_CONTRACT = 'genshin-native-logical-byte-stream-v1'
export const PRODUCTION_OWNER = false
export const DEFAULT_LOGICAL_STREAM_MAX_BYTES = 300 * 1024 * 1024
// Production may explicitly select this streaming ceiling. The qualification
// default and old vectors stay unchanged; no buffer of this size is allocated.
export const HARD_MAX_LOGICAL_STREAM_BYTES = 4 * 1024 * 1024 * 1024
export const DEFAULT_LOGICAL_STREAM_CHUNK_BYTES = 8 * 1024 * 1024
export const DEFAULT_LOGICAL_STREAM_CREDIT_BYTES = 2 * DEFAULT_LOGICAL_STREAM_CHUNK_BYTES
export const DEFAULT_DIRECT_PAYLOAD_THRESHOLD_BYTES = 1024 * 1024
export const MIN_LOGICAL_STREAM_CHUNK_BYTES = 64 * 1024
export const MAX_LOGICAL_STREAM_CHUNK_BYTES = 16 * 1024 * 1024
export const MAX_LOGICAL_STREAM_METADATA_BYTES = 64 * 1024
// Qualification deployment choices only. DSH owns none of these values and
// production high water has not been measured. Re-tune only after telemetry.
export const DEFAULT_LOGICAL_STREAM_MAX_ACTIVE = 4
export const DEFAULT_LOGICAL_STREAM_MAX_RETAINED = 4096
export const DEFAULT_LOGICAL_STREAM_AGGREGATE_SPOOL_BYTES = 2 * 1024 * 1024 * 1024

const OPEN_KEYS = new Set([
  'contract', 'transfer_id', 'purpose', 'media_type', 'content_encoding',
  'maximum_bytes', 'declared_length', 'declared_sha256', 'chunk_bytes', 'metadata',
])
const COMMIT_KEYS = new Set([
  'transfer_id', 'chunk_count', 'total_length', 'sha256', 'terminal',
])

const ID_PATTERN = /^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$/u
const SHA256_PATTERN = /^[0-9a-f]{64}$/u

export class NativeLogicalStreamError extends Error {
  constructor(code, details = {}) {
    super(code)
    this.name = 'NativeLogicalStreamError'
    this.code = code
    this.details = details
  }
}

function reject(code) {
  throw new NativeLogicalStreamError(code)
}

function integer(value, { minimum, maximum, code }) {
  if (!Number.isSafeInteger(value) || value < minimum || value > maximum) reject(code)
  return value
}

function identifier(value, code) {
  if (typeof value !== 'string' || !ID_PATTERN.test(value)) reject(code)
  return value
}

function isPlainRecord(value) {
  if (value === null || typeof value !== 'object' || Array.isArray(value)) return false
  const prototype = Object.getPrototypeOf(value)
  return prototype === Object.prototype || prototype === null
}

function hasExactKeys(value, expected) {
  if (!isPlainRecord(value)) return false
  const keys = Reflect.ownKeys(value)
  if (keys.length !== expected.size || keys.some((key) => typeof key !== 'string')) return false
  return keys.every((key) => {
    if (!expected.has(key)) return false
    const descriptor = Object.getOwnPropertyDescriptor(value, key)
    return descriptor?.enumerable === true && Object.hasOwn(descriptor, 'value')
  })
}

function optionalSha256(value, code) {
  if (value === null) return null
  if (typeof value !== 'string' || !SHA256_PATTERN.test(value)) reject(code)
  return value
}

function ownedBuffer(value, maximum) {
  if (!Buffer.isBuffer(value) && !(value instanceof Uint8Array)) {
    reject('logical_stream_chunk_not_bytes')
  }
  if (value.byteLength > maximum) reject('logical_stream_chunk_too_large')
  if (value.byteLength === 0) reject('logical_stream_chunk_empty')
  return Buffer.from(value)
}

function validUnicodeScalarString(value, codeName = 'logical_stream_metadata_invalid') {
  for (let index = 0; index < value.length; index += 1) {
    const code = value.charCodeAt(index)
    if (code >= 0xd800 && code <= 0xdbff) {
      const next = value.charCodeAt(index + 1)
      if (!(next >= 0xdc00 && next <= 0xdfff)) reject(codeName)
      index += 1
    } else if (code >= 0xdc00 && code <= 0xdfff) {
      reject(codeName)
    }
  }
  return value
}

function canonicalObjectKey(value) {
  const key = validUnicodeScalarString(value)
  if (/^(?:0|[1-9][0-9]*)$/u.test(key)) {
    if (key === '0' || key.length < 10 || (key.length === 10 && key <= '4294967294')) {
      reject('logical_stream_metadata_invalid')
    }
  }
  return key
}

function canonicalJsonValue(value, depth = 0) {
  if (depth > 32) reject('logical_stream_metadata_invalid')
  if (value === null || typeof value === 'boolean') return value
  if (typeof value === 'number') {
    if (!Number.isSafeInteger(value)) reject('logical_stream_metadata_invalid')
    return value
  }
  if (typeof value === 'string') return validUnicodeScalarString(value)
  if (Array.isArray(value)) {
    const ownKeys = Reflect.ownKeys(value)
    const expectedKeys = new Set([
      'length',
      ...Array.from({ length: value.length }, (_, index) => String(index)),
    ])
    if (ownKeys.length !== expectedKeys.size
      || ownKeys.some((key) => typeof key !== 'string' || !expectedKeys.has(key))) {
      reject('logical_stream_metadata_invalid')
    }
    const output = []
    for (let index = 0; index < value.length; index += 1) {
      const descriptor = Object.getOwnPropertyDescriptor(value, String(index))
      if (descriptor?.enumerable !== true || !Object.hasOwn(descriptor, 'value')) {
        reject('logical_stream_metadata_invalid')
      }
      output.push(canonicalJsonValue(descriptor.value, depth + 1))
    }
    return output
  }
  if (isPlainRecord(value)) {
    const output = Object.create(null)
    const keys = Reflect.ownKeys(value)
    if (keys.some((key) => typeof key !== 'string')) reject('logical_stream_metadata_invalid')
    keys.sort((left, right) => Buffer.compare(Buffer.from(left, 'utf8'), Buffer.from(right, 'utf8')))
    for (const key of keys) {
      canonicalObjectKey(key)
      const descriptor = Object.getOwnPropertyDescriptor(value, key)
      if (descriptor?.enumerable !== true || !Object.hasOwn(descriptor, 'value')) {
        reject('logical_stream_metadata_invalid')
      }
      output[key] = canonicalJsonValue(descriptor.value, depth + 1)
    }
    return output
  }
  reject('logical_stream_metadata_invalid')
}

function metadata(value) {
  if (!isPlainRecord(value)) reject('logical_stream_metadata_invalid')
  const normalized = canonicalJsonValue(value)
  const canonical = JSON.stringify(normalized)
  const encoded = Buffer.from(canonical, 'utf8')
  if (encoded.byteLength > MAX_LOGICAL_STREAM_METADATA_BYTES) {
    reject('logical_stream_metadata_too_large')
  }
  return { normalized, canonical }
}

export class LogicalStreamCreditTracker {
  constructor({ transferId, maximumCreditBytes }) {
    this.transferId = identifier(transferId, 'logical_stream_transfer_id_invalid')
    this.maximum = integer(maximumCreditBytes, {
      minimum: 1,
      maximum: HARD_MAX_LOGICAL_STREAM_BYTES,
      code: 'logical_stream_credit_limit_invalid',
    })
    this.availableBytes = 0
    this.acceptedThroughSeq = 0
  }

  applyGrant({ transferId, acceptedThroughSeq, grantBytes }) {
    if (transferId !== this.transferId) reject('logical_stream_credit_transfer_mismatch')
    const sequence = integer(acceptedThroughSeq, {
      minimum: 1,
      maximum: Number.MAX_SAFE_INTEGER,
      code: 'logical_stream_credit_sequence_invalid',
    })
    if (sequence <= this.acceptedThroughSeq) reject('logical_stream_credit_replayed')
    if (sequence !== this.acceptedThroughSeq + 1) {
      reject('logical_stream_credit_sequence_mismatch')
    }
    const amount = integer(grantBytes, {
      minimum: 1,
      maximum: this.maximum,
      code: 'logical_stream_credit_invalid',
    })
    if (this.availableBytes + amount > this.maximum) reject('logical_stream_credit_overflow')
    this.availableBytes += amount
    this.acceptedThroughSeq = sequence
  }

  reserve(value) {
    const amount = integer(value, {
      minimum: 1,
      maximum: this.maximum,
      code: 'logical_stream_credit_invalid',
    })
    if (amount > this.availableBytes) reject('logical_stream_credit_exhausted')
    this.availableBytes -= amount
  }
}

export class LogicalStreamAssembler {
  static async create({
    spoolRoot,
    allowedPurposes,
    metadataKeysByPurpose = new Map(),
    maximumBytes = DEFAULT_LOGICAL_STREAM_MAX_BYTES,
    creditWindowBytes = DEFAULT_LOGICAL_STREAM_CREDIT_BYTES,
    minimumChunkBytes = MIN_LOGICAL_STREAM_CHUNK_BYTES,
    maximumChunkBytes = MAX_LOGICAL_STREAM_CHUNK_BYTES,
    maxActiveTransfers = DEFAULT_LOGICAL_STREAM_MAX_ACTIVE,
    maxRetainedTransferIds = DEFAULT_LOGICAL_STREAM_MAX_RETAINED,
    aggregateSpoolBytes = DEFAULT_LOGICAL_STREAM_AGGREGATE_SPOOL_BYTES,
  }) {
    const maximum = integer(maximumBytes, {
      minimum: 1,
      maximum: HARD_MAX_LOGICAL_STREAM_BYTES,
      code: 'logical_stream_maximum_invalid',
    })
    const minimumChunk = integer(minimumChunkBytes, {
      minimum: 1,
      maximum: MAX_LOGICAL_STREAM_CHUNK_BYTES,
      code: 'logical_stream_minimum_chunk_invalid',
    })
    const maximumChunk = integer(maximumChunkBytes, {
      minimum: minimumChunk,
      maximum: MAX_LOGICAL_STREAM_CHUNK_BYTES,
      code: 'logical_stream_maximum_chunk_invalid',
    })
    const credit = integer(creditWindowBytes, {
      minimum: 1,
      maximum: HARD_MAX_LOGICAL_STREAM_BYTES,
      code: 'logical_stream_credit_window_invalid',
    })
    const maxActive = integer(maxActiveTransfers, {
      minimum: 1,
      maximum: DEFAULT_LOGICAL_STREAM_MAX_RETAINED,
      code: 'logical_stream_active_limit_invalid',
    })
    const maxRetained = integer(maxRetainedTransferIds, {
      minimum: maxActive,
      maximum: DEFAULT_LOGICAL_STREAM_MAX_RETAINED,
      code: 'logical_stream_retained_limit_invalid',
    })
    const aggregateSpool = integer(aggregateSpoolBytes, {
      minimum: 1,
      maximum: Number.MAX_SAFE_INTEGER,
      code: 'logical_stream_aggregate_spool_limit_invalid',
    })
    if (!(allowedPurposes instanceof Set) || allowedPurposes.size === 0
      || [...allowedPurposes].some((item) => typeof item !== 'string' || !ID_PATTERN.test(item))) {
      reject('logical_stream_purposes_invalid')
    }
    if (!(metadataKeysByPurpose instanceof Map)) reject('logical_stream_metadata_policy_invalid')
    const normalizedMetadataPolicies = new Map()
    for (const purpose of allowedPurposes) {
      const keys = metadataKeysByPurpose.get(purpose) ?? new Set()
      if (!(keys instanceof Set) || [...keys].some((key) => typeof key !== 'string')) {
        reject('logical_stream_metadata_policy_invalid')
      }
      normalizedMetadataPolicies.set(purpose, new Set(keys))
    }
    let rootStats
    let resolvedRoot
    try {
      rootStats = await lstat(spoolRoot)
      resolvedRoot = await realpath(spoolRoot)
    } catch {
      reject('logical_stream_spool_root_invalid')
    }
    if (!rootStats.isDirectory() || rootStats.isSymbolicLink()) {
      reject('logical_stream_spool_root_invalid')
    }
    return new LogicalStreamAssembler({
      spoolRoot: resolvedRoot,
      allowedPurposes,
      metadataKeysByPurpose: normalizedMetadataPolicies,
      maximumBytes: maximum,
      creditWindowBytes: credit,
      minimumChunkBytes: minimumChunk,
      maximumChunkBytes: maximumChunk,
      maxActiveTransfers: maxActive,
      maxRetainedTransferIds: maxRetained,
      aggregateSpoolBytes: aggregateSpool,
    })
  }

  constructor({
    spoolRoot,
    allowedPurposes,
    metadataKeysByPurpose,
    maximumBytes,
    creditWindowBytes,
    minimumChunkBytes,
    maximumChunkBytes,
    maxActiveTransfers,
    maxRetainedTransferIds,
    aggregateSpoolBytes,
  }) {
    this.spoolRoot = spoolRoot
    this.allowedPurposes = new Set(allowedPurposes)
    this.metadataKeysByPurpose = new Map(metadataKeysByPurpose)
    this.maximumBytes = maximumBytes
    this.creditWindowBytes = creditWindowBytes
    this.minimumChunkBytes = minimumChunkBytes
    this.maximumChunkBytes = maximumChunkBytes
    this.maxActiveTransfers = maxActiveTransfers
    this.maxRetainedTransferIds = maxRetainedTransferIds
    this.aggregateSpoolLimit = aggregateSpoolBytes
    this.active = new Map()
    this.artifacts = new Map()
    this.retainedIds = new Set()
    this.aggregateSpoolBytes = 0
  }

  get activeTransfers() { return this.active.size }
  get retainedTransferIds() { return this.retainedIds.size }

  claim(transferId) {
    const state = this.active.get(transferId)
    if (!state) reject('logical_stream_transfer_not_active')
    if (state.busy) reject('logical_stream_operation_in_progress')
    state.busy = true
    return state
  }

  releaseClaim(state) {
    if (this.active.get(state.transferId) === state) state.busy = false
  }

  async abort(transferId, cause) {
    const state = this.active.get(transferId)
    if (!state) return
    const failures = []
    try { await state.handle.close() } catch { failures.push('close') }
    try { await unlink(state.partialPath) } catch (error) {
      if (error?.code !== 'ENOENT') failures.push('unlink')
    }
    if (!failures.includes('unlink')) {
      this.aggregateSpoolBytes -= state.spoolBytes
      this.active.delete(transferId)
    } else {
      state.busy = false
    }
    if (failures.length > 0) {
      throw new NativeLogicalStreamError('logical_stream_cleanup_failed', {
        transfer_id: transferId,
        cause,
        phases: failures,
      })
    }
  }

  async abortAll({ reason = 'runtime_shutdown' } = {}) {
    identifier(reason, 'logical_stream_cancel_reason_invalid')
    const busy = [...this.active.values()].filter((state) => state.busy)
      .map((state) => state.transferId).sort()
    if (busy.length > 0) {
      throw new NativeLogicalStreamError('logical_stream_operation_in_progress', {
        transfer_ids: busy,
      })
    }
    for (const state of this.active.values()) state.busy = true
    const transferIds = [...this.active.keys()]
    const failures = []
    for (const transferId of transferIds) {
      try { await this.abort(transferId, reason) } catch (error) {
        failures.push(error.details ?? { transfer_id: transferId })
      }
    }
    if (failures.length > 0) {
      throw new NativeLogicalStreamError('logical_stream_cleanup_failed', {
        cause: reason,
        failures,
      })
    }
    return {
      kind: 'logical_stream.abort_all.settled',
      contract: LOGICAL_STREAM_CONTRACT,
      aborted: transferIds.length,
    }
  }

  async releaseArtifact({ transferId }) {
    identifier(transferId, 'logical_stream_transfer_id_invalid')
    const artifact = this.artifacts.get(transferId)
    if (!artifact) reject('logical_stream_artifact_not_active')
    try { await unlink(artifact.path) } catch {
      throw new NativeLogicalStreamError('logical_stream_cleanup_failed', {
        transfer_id: transferId,
        cause: 'artifact_release',
        phases: ['unlink'],
      })
    }
    this.artifacts.delete(transferId)
    this.aggregateSpoolBytes -= artifact.totalLength
    return {
      kind: 'logical_stream.artifact.released',
      contract: LOGICAL_STREAM_CONTRACT,
      transfer_id: transferId,
    }
  }

  async open(message) {
    if (!isPlainRecord(message)) reject('logical_stream_open_invalid')
    if (!hasExactKeys(message, OPEN_KEYS)) reject('logical_stream_open_shape_invalid')
    if (message.contract !== LOGICAL_STREAM_CONTRACT) reject('logical_stream_contract_mismatch')
    const transferId = identifier(message.transfer_id, 'logical_stream_transfer_id_invalid')
    if (this.retainedIds.has(transferId)) reject('logical_stream_transfer_id_reused')
    if (this.active.size >= this.maxActiveTransfers) reject('logical_stream_active_limit')
    if (this.retainedIds.size >= this.maxRetainedTransferIds) {
      reject('logical_stream_retained_limit')
    }
    const purpose = identifier(message.purpose, 'logical_stream_purpose_invalid')
    if (!this.allowedPurposes.has(purpose)) reject('logical_stream_purpose_not_allowed')
    if (typeof message.media_type !== 'string') {
      reject('logical_stream_media_type_invalid')
    }
    validUnicodeScalarString(message.media_type, 'logical_stream_media_type_invalid')
    const mediaTypeBytes = Buffer.byteLength(message.media_type, 'utf8')
    if (mediaTypeBytes < 1 || mediaTypeBytes > 256) reject('logical_stream_media_type_invalid')
    if (message.content_encoding !== 'identity') reject('logical_stream_encoding_unsupported')
    const maximumBytes = integer(message.maximum_bytes, {
      minimum: 1,
      maximum: this.maximumBytes,
      code: 'logical_stream_maximum_exceeded',
    })
    const declaredLength = message.declared_length === null
      ? null
      : integer(message.declared_length, {
        minimum: 0,
        maximum: maximumBytes,
        code: 'logical_stream_declared_length_invalid',
      })
    const declaredSha256 = optionalSha256(
      message.declared_sha256,
      'logical_stream_declared_sha256_invalid',
    )
    const chunkBytes = integer(message.chunk_bytes, {
      minimum: this.minimumChunkBytes,
      maximum: this.maximumChunkBytes,
      code: 'logical_stream_chunk_size_invalid',
    })
    const { normalized: copiedMetadata, canonical: metadataJson } = metadata(message.metadata)
    const allowedMetadataKeys = this.metadataKeysByPurpose.get(purpose)
    if (Object.keys(copiedMetadata).some((key) => !allowedMetadataKeys.has(key))) {
      reject('logical_stream_metadata_key_not_allowed')
    }
    const creditWindowBytes = Math.min(this.creditWindowBytes, maximumBytes)
    const partialPath = join(this.spoolRoot, `${randomUUID().replaceAll('-', '')}.part`)
    let handle
    try {
      handle = await openFile(partialPath, 'wx')
    } catch {
      reject('logical_stream_spool_create_failed')
    }
    if (this.retainedIds.has(transferId)
      || this.active.size >= this.maxActiveTransfers
      || this.retainedIds.size >= this.maxRetainedTransferIds) {
      const failures = []
      try { await handle.close() } catch { failures.push('close') }
      try { await unlink(partialPath) } catch { failures.push('unlink') }
      if (failures.length > 0) {
        throw new NativeLogicalStreamError('logical_stream_cleanup_failed', {
          transfer_id: transferId,
          cause: 'open_race',
          phases: failures,
        })
      }
      if (this.retainedIds.has(transferId)) reject('logical_stream_transfer_id_reused')
      if (this.active.size >= this.maxActiveTransfers) reject('logical_stream_active_limit')
      reject('logical_stream_retained_limit')
    }
    this.active.set(transferId, {
      transferId,
      purpose,
      mediaType: message.media_type,
      maximumBytes,
      declaredLength,
      declaredSha256,
      chunkBytes,
      creditWindowBytes,
      metadataJson,
      partialPath,
      handle,
      digest: createHash('sha256'),
      nextStreamSeq: 2,
      totalLength: 0,
      chunkCount: 0,
      spoolBytes: 0,
      shortChunkSeen: false,
      busy: false,
    })
    this.retainedIds.add(transferId)
    return {
      kind: 'logical_stream.accept',
      contract: LOGICAL_STREAM_CONTRACT,
      transfer_id: transferId,
      accepted_through_seq: 1,
      grant_bytes: creditWindowBytes,
      production_owner: PRODUCTION_OWNER,
    }
  }

  async appendChunk({ transferId, streamSeq, payload }) {
    identifier(transferId, 'logical_stream_transfer_id_invalid')
    const state = this.claim(transferId)
    try {
      if (!Number.isSafeInteger(streamSeq) || streamSeq !== state.nextStreamSeq) {
        reject('logical_stream_sequence_mismatch')
      }
      const raw = ownedBuffer(payload, state.chunkBytes)
      if (state.shortChunkSeen) reject('logical_stream_chunk_after_final')
      if (raw.byteLength > state.creditWindowBytes) reject('logical_stream_credit_exhausted')
      if (state.totalLength + raw.byteLength > state.maximumBytes) {
        reject('logical_stream_length_exceeded')
      }
      if (state.declaredLength !== null) {
        const remaining = state.declaredLength - state.totalLength
        if (remaining <= 0) reject('logical_stream_declared_length_exceeded')
        const expected = Math.min(state.chunkBytes, remaining)
        if (raw.byteLength !== expected) reject('logical_stream_declared_chunk_size_mismatch')
      }
      const maximumChunks = Math.ceil(state.maximumBytes / state.chunkBytes)
      if (state.chunkCount + 1 > maximumChunks) reject('logical_stream_chunk_count_exceeded')
      if (this.aggregateSpoolBytes + raw.byteLength > this.aggregateSpoolLimit) {
        reject('logical_stream_aggregate_spool_limit')
      }
      this.aggregateSpoolBytes += raw.byteLength
      state.spoolBytes += raw.byteLength
      const { bytesWritten } = await state.handle.write(raw, 0, raw.byteLength, null)
      if (bytesWritten !== raw.byteLength) reject('logical_stream_spool_short_write')
      await state.handle.sync()
      state.digest.update(raw)
      state.totalLength += raw.byteLength
      state.chunkCount += 1
      if (raw.byteLength < state.chunkBytes) state.shortChunkSeen = true
      state.nextStreamSeq += 1
      return {
        kind: 'logical_stream.credit',
        contract: LOGICAL_STREAM_CONTRACT,
        transfer_id: transferId,
        accepted_through_seq: streamSeq,
        grant_bytes: raw.byteLength,
      }
    } catch (error) {
      await this.abort(
        transferId,
        error instanceof NativeLogicalStreamError
          ? error.code
          : 'logical_stream_spool_write_failed',
      )
      if (error instanceof NativeLogicalStreamError) throw error
      reject('logical_stream_spool_write_failed')
    } finally {
      this.releaseClaim(state)
    }
  }

  async commit({ transferId, streamSeq, message }) {
    identifier(transferId, 'logical_stream_transfer_id_invalid')
    const state = this.claim(transferId)
    try {
      if (!Number.isSafeInteger(streamSeq) || streamSeq !== state.nextStreamSeq) {
        reject('logical_stream_sequence_mismatch')
      }
      if (!hasExactKeys(message, COMMIT_KEYS)) reject('logical_stream_commit_shape_invalid')
      if (message.transfer_id !== transferId) reject('logical_stream_commit_invalid')
      if (message.terminal !== true) reject('logical_stream_commit_not_terminal')
      const chunkCount = integer(message.chunk_count, {
        minimum: 0,
        maximum: state.nextStreamSeq,
        code: 'logical_stream_chunk_count_invalid',
      })
      const totalLength = integer(message.total_length, {
        minimum: 0,
        maximum: state.maximumBytes,
        code: 'logical_stream_total_length_invalid',
      })
      const sha256 = optionalSha256(message.sha256, 'logical_stream_sha256_invalid')
      if (sha256 === null) reject('logical_stream_sha256_invalid')
      const actualSha256 = state.digest.digest('hex')
      if (chunkCount !== state.chunkCount) reject('logical_stream_chunk_count_mismatch')
      if (totalLength !== state.totalLength) reject('logical_stream_total_length_mismatch')
      if (sha256 !== actualSha256) reject('logical_stream_sha256_mismatch')
      if (state.declaredLength !== null && totalLength !== state.declaredLength) {
        reject('logical_stream_declared_length_mismatch')
      }
      if (state.declaredSha256 !== null && sha256 !== state.declaredSha256) {
        reject('logical_stream_declared_sha256_mismatch')
      }
      await state.handle.sync()
      await state.handle.close()
      const readyPath = state.partialPath.replace(/\.part$/u, '.ready')
      await rename(state.partialPath, readyPath)
      const artifact = Object.freeze({
        transferId,
        path: readyPath,
        totalLength,
        sha256,
        chunkCount,
        purpose: state.purpose,
        mediaType: state.mediaType,
        metadataJson: state.metadataJson,
      })
      this.active.delete(transferId)
      this.artifacts.set(transferId, artifact)
      return artifact
    } catch (error) {
      await this.abort(
        transferId,
        error instanceof NativeLogicalStreamError
          ? error.code
          : 'logical_stream_spool_commit_failed',
      )
      if (error instanceof NativeLogicalStreamError) throw error
      reject('logical_stream_spool_commit_failed')
    } finally {
      this.releaseClaim(state)
    }
  }

  async cancel({ transferId, reason = 'cancelled' }) {
    identifier(transferId, 'logical_stream_transfer_id_invalid')
    identifier(reason, 'logical_stream_cancel_reason_invalid')
    const state = this.claim(transferId)
    try {
      await this.abort(transferId, reason)
    } finally {
      this.releaseClaim(state)
    }
    return {
      kind: 'logical_stream.cancelled',
      contract: LOGICAL_STREAM_CONTRACT,
      transfer_id: transferId,
      reason,
    }
  }
}
