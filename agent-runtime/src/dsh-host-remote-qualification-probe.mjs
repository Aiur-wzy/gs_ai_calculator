/** Offline proof for rc.3's carrier-neutral Host and Remote seams. */
import { randomUUID } from 'node:crypto'
import { createRequire } from 'node:module'
import { Server as NetServer } from 'node:net'
import { mkdir, mkdtemp, rm, writeFile } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import { join } from 'node:path'

import { Context } from '@deepseek-ai/cordis'
import * as ConnectionPlugin from '@deepseek-ai/dsh-client-connection'
import { serverResponseSchema } from '@deepseek-ai/dsh-client-connection'
import { TypertGatewayService } from '@deepseek-ai/dsh-api-gateway'
import { RemoteAccessService } from '@deepseek-ai/dsh-api-remote-access'
import * as RemotesPlugin from '@deepseek-ai/dsh-api-remotes'
import { SessionController } from '@deepseek-ai/dsh-api-session-controller'
import { LocalFileSystem } from '@deepseek-ai/dsh-fs-local'
import { Remote, TypertRemoteService } from '@deepseek-ai/dsh-typert-protocol'
import { TypertRegistry } from '@deepseek-ai/dsh-typert-registry'

import { NativeMemoryCredentials } from './memory-credentials.mjs'
import {
  DSH_HOST_REMOTE_ALLOWED_PROBE_ENDPOINTS,
  DSH_HOST_REMOTE_DEPENDENCY_IDENTITY,
  DSH_HOST_REMOTE_FORBIDDEN_CAPABILITIES,
  DSH_HOST_REMOTE_PUBLIC_EXPORTS,
  DSH_HOST_REMOTE_QUALIFICATION,
  DSH_HOST_REMOTE_QUALIFICATION_CONTRACT,
  DSH_HOST_REMOTE_QUALIFICATION_VERSION,
  DSH_HOST_REMOTE_RELEASE,
  assertQualificationContract,
} from './dsh-host-remote-qualification-contract.mjs'
import { assertPinnedNodeVersion } from './runtime-version.mjs'

const require = createRequire(import.meta.url)
const EMPTY_UPLINK = Object.freeze({
  async *[Symbol.asyncIterator]() {},
})

/** Process-local record support used only to qualify Connection without disk I/O. */
class ProbeCredentials extends NativeMemoryCredentials {
  constructor(ctx) {
    super(ctx)
    this._probeRecords = new Map()
  }

  readRecord(key) {
    return Promise.resolve(this._probeRecords.get(key))
  }

  describeRecord(key) {
    const record = this._probeRecords.get(key)
    return Promise.resolve(record === undefined
      ? { configured: false, writable: true }
      : { configured: true, kind: record.kind, writable: true })
  }

  listRecords() {
    return Promise.resolve([...this._probeRecords].map(([key, record]) => ({
      key, kind: record.kind,
    })))
  }

  async modifyRecord(key, mutate) {
    const current = this._probeRecords.get(key)
    const next = await mutate(current)
    if (next !== undefined) this._probeRecords.set(key, next)
    return next ?? current
  }

  deleteRecord(key) {
    this._probeRecords.delete(key)
    return Promise.resolve()
  }
}

function assert(condition, code) {
  if (!condition) throw new Error(code)
}

function plain(value) {
  return value !== null && typeof value === 'object' && !Array.isArray(value)
}

function publicPackage(name) {
  const manifest = require(`${name}/package.json`)
  assert(manifest.name === name, `s0_package_name:${name}`)
  assert(manifest.version === DSH_HOST_REMOTE_RELEASE, `s0_package_version:${name}`)
  assert(plain(manifest.exports) && Object.hasOwn(manifest.exports, '.'),
    `s0_package_root_export:${name}`)
  return manifest
}

async function auditPublicExports() {
  assertPinnedNodeVersion()
  const cordis = require('@deepseek-ai/cordis/package.json')
  assert(cordis.version === DSH_HOST_REMOTE_DEPENDENCY_IDENTITY.cordis,
    's0_cordis_version')
  const result = {}
  for (const [specifier, expected] of Object.entries(DSH_HOST_REMOTE_PUBLIC_EXPORTS)) {
    publicPackage(specifier)
    const module = await import(specifier)
    const missing = expected.filter(name => !(name in module))
    assert(missing.length === 0, `s0_public_export:${specifier}:${missing.join(',')}`)
    result[specifier] = [...expected]
  }
  return result
}

const probeInitializers = []
function markRemote(name, decorator) {
  decorator(ProbeRemoteService.prototype[name], {
    kind: 'method',
    name,
    static: false,
    private: false,
    addInitializer(initializer) {
      probeInitializers.push(initializer)
    },
  })
}

class ProbeRemoteService extends TypertRemoteService {
  constructor(ctx) {
    super(ctx, 's0ProbeRemote', { namespace: 'probe' })
    for (const initialize of probeInitializers) initialize.call(this)
  }

  echo(value) {
    return Object.freeze({ echoed: value })
  }

  async *count(limit, signal) {
    for (let index = 0; index < limit; index += 1) {
      signal.throwIfAborted()
      yield Object.freeze({ index })
    }
  }
}

markRemote('echo', Remote)
markRemote('count', Remote({ mode: 'stream' }))

function remotePayload(args = {}) {
  return Object.freeze({ args: Object.freeze({ ...args }) })
}

function rpcRequest(endpoint, payload) {
  const rpcId = randomUUID()
  return {
    rpcId,
    request: new Request(`https://genshin-shell.invalid/api/${endpoint}`, {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify({
        type: 'client-request',
        rpcId,
        method: endpoint,
        payload,
      }),
    }),
  }
}

async function unary(handler, peer, endpoint, args = {}) {
  const { rpcId, request } = rpcRequest(endpoint, remotePayload(args))
  const response = await handler.fetch(request, peer)
  if (response.status !== 200) return Object.freeze({ status: response.status })
  const envelope = serverResponseSchema.parse(await response.json())
  assert(envelope.rpcId === rpcId, 's0_rpc_correlation_changed')
  return Object.freeze({ status: response.status, result: envelope.result })
}

async function first(source) {
  const iterator = source[Symbol.asyncIterator]()
  const item = await iterator.next()
  assert(item.done !== true, 's0_stream_empty')
  return { iterator, value: item.value }
}

async function collect(source) {
  const values = []
  for await (const value of source) values.push(value)
  return values
}

function activeServices(ctx) {
  return Object.entries(ctx.reflect.props)
    .filter(([, descriptor]) => descriptor?.type === 'service')
    .map(([name]) => name)
    .sort()
}

async function qualifyHostRemote() {
  const ctx = new Context()
  const originalListen = NetServer.prototype.listen
  let listenerAttempts = 0
  NetServer.prototype.listen = function forbiddenListen(..._args) {
    listenerAttempts += 1
    throw new Error('s0_network_listener_forbidden')
  }
  try {
    await ctx.plugin(ProbeCredentials)
    await ctx.plugin(ConnectionPlugin)
    await ctx.plugin(TypertRegistry)
    await ctx.plugin(RemoteAccessService)
    await ctx.plugin(TypertGatewayService)
    await ctx.plugin(RemotesPlugin)
    await ctx.plugin(ProbeRemoteService)

    const services = activeServices(ctx)
    assert(!services.includes('webServer'), 's0_web_server_mounted')
    assert(listenerAttempts === 0, 's0_network_listener_attempted')

    const handler = ctx.connection.createSharedFetchHandler('/api')
    const owner = ctx.connection.createPeer({
      principal: Object.freeze({ kind: 'owner' }),
      interactions: 'answer',
    })
    const outsider = ctx.connection.createPeer({
      principal: Object.freeze({ kind: 's0-outsider', id: 's0-outsider' }),
      interactions: 'none',
    })

    const echo = await unary(handler, owner, 'probe/echo', { value: 'owner-ok' })
    assert(echo.result?.ok === true && echo.result.value?.echoed === 'owner-ok',
      's0_owner_unary_failed')

    const denied = await unary(handler, outsider, 'probe/echo', { value: 'denied' })
    assert(denied.result?.ok === false
      && denied.result.error?.code === 'remote-access/denied',
    's0_non_owner_not_denied')

    const missing = await unary(handler, owner, 'probe/missing')
    assert(missing.status === 404, 's0_arbitrary_method_not_rejected')

    const streamController = new AbortController()
    const stream = await ctx.typertGateway.wireStream.open(
      'probe/count', remotePayload({ limit: 3 }), EMPTY_UPLINK, owner,
      streamController.signal,
    )
    const count = await collect(stream)
    assert(JSON.stringify(count) === JSON.stringify([
      { index: 0 }, { index: 1 }, { index: 2 },
    ]), 's0_owner_stream_failed')

    let nonOwnerStreamCode
    try {
      await ctx.typertGateway.wireStream.open(
        'probe/count', remotePayload({ limit: 1 }), EMPTY_UPLINK, outsider,
        new AbortController().signal,
      )
    } catch (error) {
      nonOwnerStreamCode = error?.code
    }
    assert(nonOwnerStreamCode === 'remote-access/denied',
      's0_non_owner_stream_not_denied')

    const access = await ctx.typertGateway.wireStream.open(
      'remoteAccess/follow', remotePayload(), EMPTY_UPLINK, owner,
      new AbortController().signal,
    )
    const ownerAccess = await first(access)
    assert(ownerAccess.value?.type === 'baseline' && ownerAccess.value.owner === true,
      's0_owner_access_baseline_invalid')
    await ownerAccess.iterator.return()

    const oldEvents = await ctx.typertGateway.wireStream.open(
      '$events', remotePayload(), EMPTY_UPLINK, owner,
      new AbortController().signal,
    )
    const oldGeneration = await first(oldEvents)
    assert(oldGeneration.value?.type === 'ready'
      && typeof oldGeneration.value.clientId === 'string'
      && typeof oldGeneration.value.host?.home === 'string',
    's0_event_generation_invalid')

    await owner.dispose()
    const stale = await unary(handler, owner, 'probe/echo', { value: 'stale' })
    assert(stale.result?.ok === false
      && stale.result.error?.code === 'gateway/peer-disposed',
    's0_stale_generation_not_rejected')
    const oldClosed = await oldGeneration.iterator.next()
    assert(oldClosed.done === true, 's0_old_event_generation_not_closed')

    const replacement = ctx.connection.createPeer({
      principal: Object.freeze({ kind: 'owner' }),
      interactions: 'answer',
    })
    const nextEvents = await ctx.typertGateway.wireStream.open(
      '$events', remotePayload(), EMPTY_UPLINK, replacement,
      new AbortController().signal,
    )
    const newGeneration = await first(nextEvents)
    assert(newGeneration.value?.type === 'ready'
      && newGeneration.value.clientId !== oldGeneration.value.clientId,
    's0_event_generation_not_replaced')
    await newGeneration.iterator.return()
    await replacement.dispose()
    await outsider.dispose()

    return Object.freeze({
      ownerUnary: true,
      ownerStream: true,
      nonOwnerDenied: true,
      arbitraryMethodRejected: true,
      eventGeneration: true,
      staleGenerationRejected: true,
      webServerMounted: false,
      networkListenerAttempts: listenerAttempts,
      services,
    })
  } finally {
    NetServer.prototype.listen = originalListen
    await ctx.fiber.dispose().catch(() => {})
  }
}

async function qualifyFixedRootFs() {
  const scratch = await mkdtemp(join(tmpdir(), 'genshin-dsh-s0-fs-'))
  const privateRoot = join(scratch, 'private-session-root')
  const outsidePath = join(scratch, 'outside-private-root.txt')
  const sentinel = `outside-${randomUUID()}`
  const ctx = new Context()
  try {
    await mkdir(privateRoot, { recursive: true })
    await writeFile(outsidePath, sentinel, 'utf8')
    await ctx.plugin(LocalFileSystem, { cwd: privateRoot })

    assert(SessionController.inject.includes('fs'),
      's0_session_controller_fs_dependency_missing')
    const absolute = await ctx.fs.resolve(outsidePath)
    const relative = await ctx.fs.resolve('../outside-private-root.txt')
    const absoluteText = await ctx.fs.readText(absolute)
    const relativeText = await ctx.fs.readText(relative)
    const services = activeServices(ctx)
    const forbiddenMounted = services.filter(name => (
      ['sandbox', 'shell', 'terminal', 'tools'].includes(name)
    ))
    assert(absoluteText === sentinel && relativeText === sentinel,
      's0_fs_escape_evidence_changed')
    assert(ctx.fs.sandboxMode === undefined, 's0_fs_unexpected_sandbox_mode')
    assert(forbiddenMounted.length === 0, 's0_fs_mounted_forbidden_service')

    return Object.freeze({
      candidate: '@deepseek-ai/dsh-fs-local',
      qualified: false,
      sessionControllerRequiresFs: true,
      cwdIsContainmentBoundary: false,
      absoluteOutsideReadable: true,
      relativeEscapeReadable: true,
      sandboxMode: null,
      forbiddenMountedServices: forbiddenMounted,
      replacementImplemented: false,
      services,
    })
  } finally {
    await ctx.fiber.dispose().catch(() => {})
    await rm(scratch, { recursive: true, force: true }).catch(() => {})
  }
}

assertQualificationContract()
const publicExports = await auditPublicExports()
const hostRemote = await qualifyHostRemote()
const fixedRootFs = await qualifyFixedRootFs()
assert(fixedRootFs.qualified === false
  && DSH_HOST_REMOTE_QUALIFICATION.fsCandidate.status === 'not-qualified',
's0_fs_contract_outcome_mismatch')

process.stdout.write(`${JSON.stringify({
  contract: DSH_HOST_REMOTE_QUALIFICATION_CONTRACT,
  contractVersion: DSH_HOST_REMOTE_QUALIFICATION_VERSION,
  status: 'passed',
  productionOwner: false,
  dshRelease: DSH_HOST_REMOTE_RELEASE,
  dependencyIdentity: DSH_HOST_REMOTE_DEPENDENCY_IDENTITY,
  publicExports,
  allowedProbeEndpoints: DSH_HOST_REMOTE_ALLOWED_PROBE_ENDPOINTS,
  forbiddenCapabilities: DSH_HOST_REMOTE_FORBIDDEN_CAPABILITIES,
  hostRemote,
  fixedRootFs,
  clientModules: DSH_HOST_REMOTE_QUALIFICATION.clientModules,
}, null, 2)}\n`)
