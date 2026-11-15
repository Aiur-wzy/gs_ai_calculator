/**
 * Project Session Controller composition over the existing DSH kernel.
 * No alternative transcript, Turn reducer, filesystem tool or paid boot call.
 */
import * as ConnectionPlugin from '@deepseek-ai/dsh-client-connection'
import FileUploads from '@deepseek-ai/dsh-client-file-upload'
import * as RemotesPlugin from '@deepseek-ai/dsh-api-remotes'
import SessionController from '@deepseek-ai/dsh-api-session-controller'
import WorkspaceRegistry from '@deepseek-ai/dsh-workspace'
import { appendPluginRecord } from '@deepseek-ai/dsh-session'
import { join } from 'node:path'
import { mkdir } from 'node:fs/promises'
import { createHash } from 'node:crypto'
import { createNativeDeepSeekKernel, DEEPSEEK_PROVIDER_ID } from './native-deepseek-kernel.mjs'
import NativeShellCredentials from './native-shell-credentials.mjs'
import { createNativeProviderAdmission } from './native-provider-admission.mjs'
import { mountNativeDshClientAssets } from './native-dsh-client-assets.mjs'
import { mountNativeDshGenericCapabilities, NATIVE_GENERIC_MODULES } from './native-dsh-generic-capabilities.mjs'
import { mountNativeDshDomainTools } from './native-dsh-domain-tools.mjs'
import { mountNativeDshAccounting } from './native-dsh-accounting.mjs'
import { NativeTaskTransport } from './native-dsh-task-transport.mjs'
import { mountTaskReview } from './dsh-task-review.mjs'

export const NATIVE_DSH_SESSION_HOST_CONTRACT = 'genshin-native-dsh-session-host-v1'

function sessionBinding(handler, workspaceRoot, workspaceId) {
  return Object.freeze({
    requestBodyMode: request => handler.requestBodyMode(request),
    async fetch(request, peer) {
      if (request.method !== 'POST' || new URL(request.url).pathname !== '/api/session/create') {
        return handler.fetch(request, peer)
      }
      let body
      try { body = await request.clone().json() }
      catch { return handler.fetch(request, peer) }
      const parsed = ConnectionPlugin.clientRequestSchema.safeParse(body)
      const selected = parsed.success ? parsed.data.payload?.args?.request : undefined
      if (selected === null || typeof selected !== 'object' || Array.isArray(selected)) {
        return handler.fetch(request, peer)
      }
      // Upstream validates explicit workspace/cwd selections. Only supply a
      // default for callers which have not selected one; GUI is not a second
      // restricted working-directory policy.
      if (Object.hasOwn(selected, 'workspaceId') || Object.hasOwn(selected, 'cwd')) return handler.fetch(request, peer)
      // Preserve the public Workspace relationship: Controller.create joins
      // its sessionIds only for workspaceId (cwd alone is not equivalent).
      // This fixed App container is not a model filesystem capability.
      const { cwd: _selectedCwd, ...requestFields } = selected
      body.payload.args.request = { ...requestFields, workspaceId }
      return handler.fetch(new Request(request.url, {
        method: request.method, headers: request.headers,
        signal: request.signal, body: JSON.stringify(body),
      }), peer)
    },
  })
}

export async function createNativeDshSessionHost({
  profileRoot, mirrorRoot, dshSettings, model = 'deepseek-flash', reasoningEffort = 'high', clientAssets,
  retryAdmission, providerRetryPolicy = { mode: 'normal' },
  domainTransport,
  providerCapacity = 1,
  accounting = false,
} = {}) {
  const kernel = await createNativeDeepSeekKernel({
    profileRoot, sessionRoot: join(profileRoot, 'sessions'),
    storageRoot: join(profileRoot, 'storages'), queryPath: join(profileRoot, 'session-query.sqlite'),
    dshSettings, credentialsPlugin: NativeShellCredentials, providerRetryPolicy, model, reasoningEffort,
  })
  const ctx = kernel.context
  const taskTransport = domainTransport === undefined ? undefined : new NativeTaskTransport(ctx, domainTransport)
  const admission = createNativeProviderAdmission(providerCapacity)
  try {
    const workspaceRoot = kernel.fullProfile.workspace
    await mkdir(workspaceRoot, { recursive: true })
    ctx.effect(() => () => admission.close(), 'shell: provider capacity')
    ctx.on('llm/stream', (options, next) => admission.wrap(options, next), { prepend: true })
    // Default model, attachment store and writable sandboxed filesystem are
    // already supplied by the official base. The media mirror is not ctx.fs.
    await ctx.plugin(WorkspaceRegistry)
    // A Workspace registration is an App container, not a Session/history.
    // Without it the full DSH Composer correctly waits at "Choose workspace"
    // even though direct Controller/create qualification succeeds.
    const appWorkspace = await ctx.workspaceRegistry.create(workspaceRoot, '原神排轴')
    const domainTools = taskTransport === undefined ? undefined
      : mountNativeDshDomainTools(ctx, { transport: taskTransport })
    const accounts = accounting ? mountNativeDshAccounting(ctx, { transport: taskTransport }) : undefined
    if (domainTools) mountTaskReview(ctx, {
      port: { read: agent => domainTools.advisorySnapshot(agent.id) },
      eligible: agent => ctx.agents.roots().includes(agent),
      record: (agent, row) => appendPluginRecord(agent.session, 'plugin:genshin/task-review', row),
    })
    await mountNativeDshGenericCapabilities(ctx, {
      ...retryAdmission,
      canRetryDomainAttempt: async request => (domainTools?.canRetryDomainAttempt(request) ?? true)
        && (await retryAdmission?.canRetryDomainAttempt?.(request) ?? true),
    })
    // The Loader is the sole owner of every dual-face Host/client package.
    // Manually mounting these and then loading their client graph would leave
    // duplicate Host fibers FAILED even while existing services kept working.
    let assets
    if (clientAssets !== undefined) {
      assets = await mountNativeDshClientAssets({ ...clientAssets, context: ctx, profileRoot })
    } else {
      await ctx.plugin(ConnectionPlugin)
      await ctx.plugin(FileUploads)
      await ctx.plugin(RemotesPlugin)
      await ctx.plugin(SessionController, { nativeOpen: false })
    }
    for (const service of SessionController.inject) {
      if (ctx.get(service) === undefined) throw new Error(`native_dsh_session_host_missing:${service}`)
    }
    if (ctx.get('sessionController') === undefined) throw new Error('native_dsh_session_controller_missing')
    // DSH's Remote event queue deliberately retains unanswered waterfalls.
    // Only authenticated owner streams can answer it (public hasLiveClient
    // predicate); without that UI, do not create an invisible approval wait.
    // This is transport availability, never an App candidate/D2/file grant.
    ctx.on('approval/request', (_request, next) => ctx.typertGateway.hasLiveClient()
      ? next() : Promise.resolve('unavailable'), { prepend: true })
    // This instance describes the selected, validated client composition.
    // It never grants a peer or App domain capability.
    const productionOwner = assets?.productionOwner ?? false
    const composition = {
      contract: NATIVE_DSH_SESSION_HOST_CONTRACT, productionOwner, dshRelease: '0.2.1-rc.5',
      baseCompositionHash: kernel.productionCompositionReport().composition_hash,
      hostModules: Object.freeze([...new Set([
        ...kernel.compositionRows.filter(row => !row.disabled && row.name?.startsWith('@'))
          .map(row => row.name),
        ...NATIVE_GENERIC_MODULES,
        ...(assets?.graph().entries.map(row => row.id) ?? []),
      ])].sort()),
      transportOwner: assets === undefined ? 'manual-qualification-host' : 'single-cordis-loader',
      nativeOpen: false, providerCapacity,
      retryPolicy: structuredClone(providerRetryPolicy),
      fileSystem: 'dsh-fs-sandbox',
      workspaceBinding: 'dsh-workspace-selection',
      workspaceId: appWorkspace.id,
      clientAcceptedManifest: clientAssets?.acceptedManifest.semanticSha256 ?? null,
      clientModules: Object.freeze(assets?.graph().entries.map(entry => entry.id) ?? []),
      appDomainTools: domainTools === undefined ? 'not-bound' : 'public-agent-created-scoped-v1',
      accounting: accounts === undefined ? 'not-bound' : 'host-provider-research-lease-v1',
    }
    const compositionIdentity = Object.freeze({ ...composition,
      sha256: createHash('sha256').update(JSON.stringify(composition)).digest('hex') })
    return Object.freeze({
      contract: NATIVE_DSH_SESSION_HOST_CONTRACT,
      productionOwner,
      kernel, context: ctx, admission, assets, compositionIdentity, domainTools, accounts,
      async sessionStatus(sessionId) {
        if (typeof sessionId !== 'string' || sessionId.trim().length === 0) {
          throw new Error('native_dsh_session_identity_required')
        }
        const observation = await ctx.sessionQuery.observeSession(sessionId, { projectionMode: 'none' })
        try {
          const end = observation.events.findLast(event => event.type === 'turn/end')
          const agent = ctx.agents.list().find(item => item.id === sessionId)
          const taskReview = {}
          for (const event of observation.events) {
            if (event.type === 'plugin:genshin/task-review') {
              const { code, binding, available, version, reason, turn } = event.data
              taskReview[code] = { binding, available, version, reason, turn }
            }
          }
          return { session_id: sessionId,
            last_turn: end === undefined ? null : structuredClone(end.data),
            agent_running: agent === undefined ? null : agent.status === 'running',
            admission: admission.sessionSnapshot(sessionId),
            task_review_plugin: taskReview }
        } finally { observation[Symbol.dispose]() }
      },
      handler: sessionBinding(ctx.connection.createSharedFetchHandler('/api'), workspaceRoot, appWorkspace.id),
      wireStream: ctx.typertGateway.wireStream,
      createPeer: () => ctx.connection.createPeer({ principal: { kind: 'owner' }, interactions: 'answer' }),
      dispose: () => kernel.dispose(),
    })
  } catch (error) {
    await kernel.dispose()
    throw error
  }
}
