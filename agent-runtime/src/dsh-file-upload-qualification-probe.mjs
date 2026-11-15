/**
 * Qualification-only proof for rc.5's public FileUploads Host service.
 *
 * This probe deliberately mounts no production runtime or WebView owner.  It
 * proves the public package accepts a backpressured multi-chunk source,
 * delegates it without whole-file aggregation, and keeps staged receipts
 * scoped to one exact Session identity.
 */
import { createHash, randomUUID } from 'node:crypto'
import { createRequire } from 'node:module'

import { Context, Service } from '@deepseek-ai/cordis'
import * as ConnectionPlugin from '@deepseek-ai/dsh-client-connection'
import FileUploads from '@deepseek-ai/dsh-client-file-upload'
import { TypertRegistry } from '@deepseek-ai/dsh-typert-registry'

import { assertPinnedNodeVersion } from './runtime-version.mjs'
import { NativeMemoryCredentials } from './memory-credentials.mjs'

export const PRODUCTION_OWNER = false
export const QUALIFICATION_CONTRACT = 'genshin-dsh-file-upload-qualification-v1'

const require = createRequire(import.meta.url)

class ProbeCredentials extends NativeMemoryCredentials {
  constructor(ctx) {
    super(ctx)
    this.records = new Map()
  }

  readRecord(key) {
    return Promise.resolve(this.records.get(key))
  }

  describeRecord(key) {
    const record = this.records.get(key)
    return Promise.resolve(record === undefined
      ? { configured: false, writable: true }
      : { configured: true, kind: record.kind, writable: true })
  }

  listRecords() {
    return Promise.resolve([...this.records].map(([key, record]) => ({
      key,
      kind: record.kind,
    })))
  }

  async modifyRecord(key, mutate) {
    const current = this.records.get(key)
    const next = await mutate(current)
    if (next !== undefined) this.records.set(key, next)
    return next ?? current
  }

  deleteRecord(key) {
    this.records.delete(key)
    return Promise.resolve()
  }
}

function assert(condition, code) {
  if (!condition) throw new Error(code)
}

function packageIdentity(name) {
  const manifest = require(`${name}/package.json`)
  assert(manifest.name === name, `file_upload_package_name:${name}`)
  assert(manifest.version === '0.2.1-rc.5', `file_upload_package_version:${name}`)
  assert(manifest.exports?.['.'] !== undefined, `file_upload_public_root:${name}`)
  return Object.freeze({ name: manifest.name, version: manifest.version })
}

function sessionAgent(id, origin = 'user') {
  const session = Object.freeze({
    header: Object.freeze({ origin }),
  })
  return Object.freeze({ id, session })
}

class ProbeAgents extends Service {
  constructor(ctx) {
    super(ctx, 'agents')
    this.records = new Map()
  }

  get(id) {
    return this.records.get(id)
  }
}

class ProbeAttachments extends Service {
  constructor(ctx) {
    super(ctx, 'attachments')
    this.saved = []
    this.imageLimits = Object.freeze({
      maxImageBytes: 20 * 1024 * 1024,
      maxImagesPerMessage: 20,
      maxMessageImageBytes: 200 * 1024 * 1024,
      maxImagePixels: 64_000_000,
      maxImageDimension: 8192,
      mediaTypes: Object.freeze(['image/png', 'image/jpeg', 'image/webp', 'image/gif']),
    })
  }

  async saveFileStream({ data, signal, name }) {
    const digest = createHash('sha256')
    const chunkSizes = []
    let bytes = 0
    for await (const chunk of data) {
      signal?.throwIfAborted()
      assert(chunk instanceof Uint8Array, 'file_upload_chunk_type')
      // The sink observes each upstream chunk before asking for the next one;
      // retaining complete file bytes here would invalidate this probe.
      digest.update(chunk)
      chunkSizes.push(chunk.byteLength)
      bytes += chunk.byteLength
    }
    signal?.throwIfAborted()
    const sha256 = digest.digest('hex')
    const file = Object.freeze({
      attachmentId: `sha256:${sha256}`,
      name: name ?? 'upload.bin',
      bytes,
    })
    this.saved.push(Object.freeze({ file, chunkSizes: Object.freeze(chunkSizes) }))
    return file
  }

  isAttachmentError() {
    return false
  }
}

class ProbeCommands extends Service {
  constructor(ctx) {
    super(ctx, 'commands')
    this.receiptResolver = undefined
  }

  registerFileReceiptResolver(resolve) {
    assert(this.receiptResolver === undefined, 'file_upload_receipt_resolver_duplicate')
    this.receiptResolver = resolve
    return () => {
      if (this.receiptResolver === resolve) this.receiptResolver = undefined
    }
  }

  resolveReceipt(agent, receiptId) {
    return this.receiptResolver?.(agent, receiptId)
  }
}

async function qualifyFileUploads() {
  assertPinnedNodeVersion()
  const packages = Object.freeze([
    packageIdentity('@deepseek-ai/dsh-client-file-upload'),
    packageIdentity('@deepseek-ai/dsh-client-connection'),
  ])

  const ctx = new Context()

  try {
    await ctx.plugin(ProbeAgents)
    await ctx.plugin(ProbeAttachments)
    await ctx.plugin(ProbeCommands)
    await ctx.plugin(ProbeCredentials)
    await ctx.plugin(ConnectionPlugin)
    await ctx.plugin(TypertRegistry)
    assert(ctx.agents !== undefined, 'file_upload_agents_not_mounted')
    assert(ctx.attachments !== undefined, 'file_upload_attachments_not_mounted')
    assert(ctx.commands !== undefined, 'file_upload_commands_not_mounted')
    assert(ctx.connection !== undefined, 'file_upload_connection_not_mounted')
    await ctx.plugin(FileUploads)
    assert(ctx.fileUploads !== undefined, 'file_upload_service_not_mounted')

    const sessionA = `qualification-session-${randomUUID()}`
    const sessionB = `qualification-session-${randomUUID()}`
    const agentA = sessionAgent(sessionA)
    const agentB = sessionAgent(sessionB)
    ctx.agents.records.set(sessionA, agentA)
    ctx.agents.records.set(sessionB, agentB)

    const chunks = Object.freeze([
      new Uint8Array(129 * 1024).fill(0x11),
      new Uint8Array(257 * 1024).fill(0x22),
      new Uint8Array(65 * 1024).fill(0x33),
    ])
    let pulls = 0
    const data = Object.freeze({
      async *[Symbol.asyncIterator]() {
        for (const chunk of chunks) {
          pulls += 1
          yield chunk
        }
      },
    })

    const uploaded = await ctx.fileUploads.uploadStream({
      sessionId: sessionA,
      data,
      name: 'multi-chunk.bin',
    })
    assert(pulls === chunks.length, 'file_upload_source_not_fully_consumed')
    assert(ctx.attachments.saved.length === 1, 'file_upload_save_count')
    assert(JSON.stringify(ctx.attachments.saved[0].chunkSizes)
      === JSON.stringify(chunks.map(chunk => chunk.byteLength)),
    'file_upload_chunks_aggregated')
    assert(uploaded.file === ctx.attachments.saved[0].file, 'file_upload_ref_changed')
    assert(typeof uploaded.receiptId === 'string' && uploaded.receiptId.length > 0,
      'file_upload_receipt_missing')
    assert(ctx.commands.resolveReceipt(agentA, uploaded.receiptId) === uploaded.file,
      'file_upload_receipt_owner_missing')
    assert(ctx.commands.resolveReceipt(agentB, uploaded.receiptId) === undefined,
      'file_upload_receipt_cross_session')

    const binding = ctx.fileUploads.bindPrompt(
      agentA, [uploaded.receiptId], 'qualification-request-a',
    )
    binding.commit()
    ctx.fileUploads.retirePrompt(agentA, 'qualification-request-a')
    assert(ctx.commands.resolveReceipt(agentA, uploaded.receiptId) === undefined,
      'file_upload_receipt_not_retired')

    const abort = new AbortController()
    let abortPulls = 0
    const abortedData = Object.freeze({
      async *[Symbol.asyncIterator]() {
        abortPulls += 1
        yield new Uint8Array([1, 2, 3])
        abort.abort(new Error('qualification_abort'))
        abortPulls += 1
        yield new Uint8Array([4, 5, 6])
      },
    })
    let abortCode
    try {
      await ctx.fileUploads.uploadStream({
        sessionId: sessionA,
        data: abortedData,
        signal: abort.signal,
      })
    } catch (error) {
      abortCode = error?.code
    }
    assert(abortPulls === 2, 'file_upload_abort_source_shape')
    assert(abortCode === 'gateway/internal', 'file_upload_abort_not_settled')
    assert(ctx.attachments.saved.length === 1, 'file_upload_abort_published_receipt')

    let missingCode
    try {
      await ctx.fileUploads.uploadStream({
        sessionId: `missing-${randomUUID()}`,
        data: Object.freeze({ async *[Symbol.asyncIterator]() {} }),
      })
    } catch (error) {
      missingCode = error?.code
    }
    assert(missingCode === 'session/not-found', 'file_upload_missing_session_not_rejected')

    const subagent = sessionAgent(`qualification-subagent-${randomUUID()}`, 'subagent')
    ctx.agents.records.set(subagent.id, subagent)
    let subagentCode
    try {
      await ctx.fileUploads.uploadStream({
        sessionId: subagent.id,
        data: Object.freeze({ async *[Symbol.asyncIterator]() {} }),
      })
    } catch (error) {
      subagentCode = error?.code
    }
    assert(subagentCode === 'subagent/attachment-invalid',
      'file_upload_subagent_not_rejected')

    return Object.freeze({
      contract: QUALIFICATION_CONTRACT,
      productionOwner: PRODUCTION_OWNER,
      status: 'passed',
      packages,
      chunks: Object.freeze(chunks.map(chunk => chunk.byteLength)),
      uploadedBytes: uploaded.file.bytes,
      receiptScoped: true,
      cancellationSettled: true,
    })
  } finally {
    await ctx.fiber.dispose()
  }
}

const receipt = await qualifyFileUploads()
process.stdout.write(`${JSON.stringify(receipt)}\n`)
