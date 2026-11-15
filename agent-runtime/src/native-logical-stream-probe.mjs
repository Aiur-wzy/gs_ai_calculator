/** Cross-language fixture and adversarial checks for logical byte streams. */
import assert from 'node:assert/strict'
import { createHash, randomUUID } from 'node:crypto'
import { mkdir, readFile, rm, unlink } from 'node:fs/promises'
import { dirname, join, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

import {
  DEFAULT_LOGICAL_STREAM_CHUNK_BYTES,
  DEFAULT_LOGICAL_STREAM_CREDIT_BYTES,
  DEFAULT_LOGICAL_STREAM_MAX_BYTES,
  HARD_MAX_LOGICAL_STREAM_BYTES,
  LOGICAL_STREAM_CONTRACT,
  LogicalStreamAssembler,
  LogicalStreamCreditTracker,
  NativeLogicalStreamError,
  PRODUCTION_OWNER,
} from './native-logical-stream.mjs'

const runtimeRoot = resolve(dirname(fileURLToPath(import.meta.url)), '..')
const repositoryRoot = resolve(runtimeRoot, '..')
const fixture = JSON.parse(await readFile(
  join(runtimeRoot, 'native-logical-stream-v1-vectors.json'),
  'utf8',
))
const buildRoot = resolve(repositoryRoot, 'build-py')
const spoolRoot = resolve(buildRoot, `logical-stream-node-${randomUUID().replaceAll('-', '')}`)
if (dirname(spoolRoot) !== buildRoot) throw new Error('logical_stream_probe_root_escape')

async function rejects(code, operation) {
  let caught
  try {
    await operation()
  } catch (error) {
    caught = error
  }
  assert.ok(caught instanceof NativeLogicalStreamError)
  assert.equal(caught.code, code)
}

await mkdir(buildRoot, { recursive: true })
await mkdir(spoolRoot)
try {
  const policy = fixture.policy
  const assembler = await LogicalStreamAssembler.create({
    spoolRoot,
    allowedPurposes: new Set(['dsh_fetch_request']),
    metadataKeysByPurpose: new Map([
      ['dsh_fetch_request', new Set(['method', 'route'])],
    ]),
    maximumBytes: policy.maximum_bytes,
    creditWindowBytes: policy.credit_window_bytes,
    minimumChunkBytes: policy.minimum_chunk_bytes,
    maximumChunkBytes: policy.maximum_chunk_bytes,
  })
  const accepted = await assembler.open(fixture.open)
  assert.equal(accepted.grant_bytes, fixture.expected.initial_credit)
  assert.equal(accepted.production_owner, false)
  const credits = []
  for (const row of fixture.chunks) {
    const receipt = await assembler.appendChunk({
      transferId: fixture.open.transfer_id,
      streamSeq: row.stream_seq,
      payload: Buffer.from(row.payload_hex, 'hex'),
    })
    credits.push(receipt.grant_bytes)
  }
  assert.deepEqual(credits, fixture.expected.chunk_credits)
  const artifact = await assembler.commit({
    transferId: fixture.open.transfer_id,
    streamSeq: fixture.commit.stream_seq,
    message: fixture.commit.payload,
  })
  assert.equal(
    (await readFile(artifact.path)).toString('hex'),
    fixture.expected.payload_hex,
  )
  assert.equal(assembler.activeTransfers, 0)
  assert.equal(assembler.retainedTransferIds, 1)
  assert.equal(artifact.metadataJson, fixture.expected.metadata_json)
  assert.equal(assembler.aggregateSpoolBytes, 10)
  await assembler.releaseArtifact({ transferId: fixture.open.transfer_id })
  assert.equal(assembler.aggregateSpoolBytes, 0)
  await rejects('logical_stream_transfer_id_reused', () => assembler.open(fixture.open))

  const maxAssembler = await LogicalStreamAssembler.create({
    spoolRoot,
    allowedPurposes: new Set(['dsh_fetch_request']),
    metadataKeysByPurpose: new Map([
      ['dsh_fetch_request', new Set(['method', 'route'])],
    ]),
  })
  const maxOpen = {
    ...fixture.open,
    transfer_id: 'max-contract-node',
    maximum_bytes: DEFAULT_LOGICAL_STREAM_MAX_BYTES,
    declared_length: DEFAULT_LOGICAL_STREAM_MAX_BYTES,
    declared_sha256: null,
    chunk_bytes: DEFAULT_LOGICAL_STREAM_CHUNK_BYTES,
  }
  const maxAccepted = await maxAssembler.open(maxOpen)
  assert.equal(maxAccepted.grant_bytes, DEFAULT_LOGICAL_STREAM_CREDIT_BYTES)
  await maxAssembler.cancel({ transferId: maxOpen.transfer_id })
  const productionCeiling = await LogicalStreamAssembler.create({ spoolRoot,
    allowedPurposes: new Set(['dsh_fetch_request']), maximumBytes: HARD_MAX_LOGICAL_STREAM_BYTES,
    aggregateSpoolBytes: HARD_MAX_LOGICAL_STREAM_BYTES * 2 })
  const declared = await productionCeiling.open({ ...maxOpen,
    transfer_id: 'explicit-four-gib', maximum_bytes: HARD_MAX_LOGICAL_STREAM_BYTES,
    declared_length: HARD_MAX_LOGICAL_STREAM_BYTES, metadata: {} })
  assert.equal(declared.grant_bytes, DEFAULT_LOGICAL_STREAM_CREDIT_BYTES)
  assert.equal(productionCeiling.aggregateSpoolBytes, 0)
  await productionCeiling.cancel({ transferId: 'explicit-four-gib' })
  await rejects('logical_stream_maximum_exceeded', () => maxAssembler.open({
    ...maxOpen,
    transfer_id: 'too-large-contract-node',
    maximum_bytes: DEFAULT_LOGICAL_STREAM_MAX_BYTES + 1,
  }))

  const badAssembler = await LogicalStreamAssembler.create({
    spoolRoot,
    allowedPurposes: new Set(['dsh_fetch_request']),
    metadataKeysByPurpose: new Map([
      ['dsh_fetch_request', new Set(['method', 'route'])],
    ]),
    maximumBytes: 32,
    creditWindowBytes: 8,
    minimumChunkBytes: 1,
    maximumChunkBytes: 16,
  })
  const badOpen = {
    ...fixture.open,
    transfer_id: 'bad-sequence-node',
    declared_length: null,
    declared_sha256: null,
  }
  await badAssembler.open(badOpen)
  await rejects('logical_stream_sequence_mismatch', () => badAssembler.appendChunk({
    transferId: badOpen.transfer_id,
    streamSeq: 3,
    payload: Buffer.from('a'),
  }))
  assert.equal(badAssembler.activeTransfers, 0)

  const tracker = new LogicalStreamCreditTracker({
    transferId: 'credit-transfer-node',
    maximumCreditBytes: 8,
  })
  tracker.applyGrant({
    transferId: 'credit-transfer-node', acceptedThroughSeq: 1, grantBytes: 8,
  })
  tracker.reserve(4)
  assert.equal(tracker.availableBytes, 4)
  await rejects('logical_stream_credit_exhausted', () => tracker.reserve(5))
  tracker.applyGrant({
    transferId: 'credit-transfer-node', acceptedThroughSeq: 2, grantBytes: 4,
  })
  await rejects('logical_stream_credit_replayed', () => tracker.applyGrant({
    transferId: 'credit-transfer-node', acceptedThroughSeq: 2, grantBytes: 1,
  }))
  await rejects('logical_stream_credit_sequence_mismatch', () => tracker.applyGrant({
    transferId: 'credit-transfer-node', acceptedThroughSeq: 4, grantBytes: 1,
  }))
  await rejects('logical_stream_credit_transfer_mismatch', () => tracker.applyGrant({
    transferId: 'other-transfer-node', acceptedThroughSeq: 3, grantBytes: 1,
  }))
  await rejects('logical_stream_credit_overflow', () => tracker.applyGrant({
    transferId: 'credit-transfer-node', acceptedThroughSeq: 3, grantBytes: 1,
  }))

  await rejects('logical_stream_open_shape_invalid', () => maxAssembler.open({
    ...maxOpen,
    transfer_id: 'extra-key-node',
    extra: true,
  }))
  await rejects('logical_stream_metadata_invalid', () => maxAssembler.open({
    ...maxOpen,
    transfer_id: 'invalid-metadata-node',
    metadata: { method: undefined },
  }))
  await rejects(fixture.rejections[0].code, () => maxAssembler.open({
    ...maxOpen,
    transfer_id: 'array-index-key-node',
    metadata: fixture.rejections[0].metadata,
  }))
  await rejects('logical_stream_media_type_invalid', () => maxAssembler.open({
    ...maxOpen,
    transfer_id: 'wide-media-node',
    media_type: '界'.repeat(100),
  }))

  const concurrencyOpen = {
    ...fixture.open,
    transfer_id: 'singleflight-node',
    declared_length: null,
    declared_sha256: null,
  }
  await badAssembler.open(concurrencyOpen)
  badAssembler.active.get('singleflight-node').busy = true
  await rejects('logical_stream_operation_in_progress', () => badAssembler.cancel({
    transferId: 'singleflight-node',
  }))
  badAssembler.active.get('singleflight-node').busy = false
  await badAssembler.cancel({ transferId: 'singleflight-node' })

  const idempotentOpen = { ...concurrencyOpen, transfer_id: 'missing-partial-node' }
  await badAssembler.open(idempotentOpen)
  const idempotentState = badAssembler.active.get('missing-partial-node')
  await idempotentState.handle.close()
  await unlink(idempotentState.partialPath)
  await badAssembler.cancel({ transferId: 'missing-partial-node' })

  const cleanupOpen = { ...concurrencyOpen, transfer_id: 'cleanup-node' }
  await badAssembler.open(cleanupOpen)
  const cleanupState = badAssembler.active.get('cleanup-node')
  await cleanupState.handle.close()
  cleanupState.handle = { close: async () => { throw new Error('synthetic close failure') } }
  let cleanupError
  try {
    await badAssembler.cancel({ transferId: 'cleanup-node' })
  } catch (error) {
    cleanupError = error
  }
  assert.ok(cleanupError instanceof NativeLogicalStreamError)
  assert.equal(cleanupError.code, 'logical_stream_cleanup_failed')
  assert.deepEqual(cleanupError.details.phases, ['close'])

  assert.equal(
    createHash('sha256').update(Buffer.from(fixture.expected.payload_hex, 'hex')).digest('hex'),
    fixture.commit.payload.sha256,
  )

  process.stdout.write(`${JSON.stringify({
    contract: LOGICAL_STREAM_CONTRACT,
    status: 'passed',
    chunks: fixture.chunks.length,
    productionOwner: PRODUCTION_OWNER,
    maximumLogicalBytes: DEFAULT_LOGICAL_STREAM_MAX_BYTES,
  })}\n`)
} finally {
  const cleanupRoot = resolve(spoolRoot)
  if (dirname(cleanupRoot) !== buildRoot) throw new Error('logical_stream_probe_cleanup_escape')
  await rm(cleanupRoot, { recursive: true, force: false })
}
