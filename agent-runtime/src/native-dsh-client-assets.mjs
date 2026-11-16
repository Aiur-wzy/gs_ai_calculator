/** Official Client Module Loader in the same Cordis Context as the Session owner. */
import { createHash } from 'node:crypto'
import { symbols } from '@deepseek-ai/cordis'
import Loader from '@deepseek-ai/cordis-plugin-loader'
import { bootInjections } from '@deepseek-ai/dsh-client-modules'
import { readFile } from 'node:fs/promises'
import { dirname, join, relative, isAbsolute } from 'node:path'
import { fileURLToPath } from 'node:url'
import { mountNativeDshProfileSettings, nativeDshSettingsEntryId } from './native-dsh-profile-settings.mjs'

function same(left, right, code) {
  if (JSON.stringify(left) !== JSON.stringify(right)) throw new Error(code)
}
function digest(bytes) { return createHash('sha256').update(bytes).digest('hex') }

async function localEntry(name, local, baseUrl) {
  if (local === undefined) return name
  if (typeof local?.version !== 'string' || typeof local?.hostEntry !== 'string'
      || typeof baseUrl !== 'string' || !local.hostEntry.match(/^[A-Za-z0-9_.\/-]+$/u)
      || local.hostEntry.startsWith('/') || local.hostEntry.split('/').includes('..')) {
    throw new Error('native_dsh_client_assets_local_entry_invalid')
  }
  const base = new URL(baseUrl)
  if (base.protocol !== 'file:' || !base.pathname.endsWith('/')) {
    throw new Error('native_dsh_client_assets_local_base_invalid')
  }
  const entry = new URL(local.hostEntry, base)
  const suffix = relative(fileURLToPath(base), fileURLToPath(entry))
  if (suffix.startsWith('..') || isAbsolute(suffix)) throw new Error('native_dsh_client_assets_local_escape')
  const manifest = JSON.parse(await readFile(join(dirname(fileURLToPath(entry)), 'package.json'), 'utf8'))
  if (manifest.name !== name || manifest.version !== local.version || manifest.dsh?.client?.platform !== 'web') {
    throw new Error('native_dsh_client_assets_local_identity_mismatch')
  }
  return entry.href
}

export async function mountNativeDshClientAssets({ context: ctx, contract, acceptedManifest, localModuleBaseUrl, profileRoot }) {
  if (typeof contract?.productionOwner !== 'boolean' || contract?.dshRelease !== '0.2.2-rc.3'
      || acceptedManifest?.contract !== 'genshin-dsh-client-accepted-manifest-v1'
      || contract.dshRelease !== acceptedManifest.dshRelease
      || contract.dshCommit !== acceptedManifest.dshCommit) throw new Error('native_dsh_client_assets_contract_invalid')
  same(contract.dynamicModules, acceptedManifest.entryOrder, 'native_dsh_client_assets_allowlist_invalid')
  same(contract.platformModules, acceptedManifest.platformModules, 'native_dsh_client_assets_platform_mismatch')
  const { semanticSha256, ...unsignedManifest } = acceptedManifest
  if (digest(JSON.stringify(unsignedManifest)) !== semanticSha256) {
    throw new Error('native_dsh_client_assets_manifest_digest_invalid')
  }
  if (Object.keys(contract.localModules ?? {}).some(name => !contract.dynamicModules.includes(name))) {
    throw new Error('native_dsh_client_assets_local_allowlist_invalid')
  }
  // Cordis creates a traceable Proxy on every read; compare the public
  // original symbol rather than Proxy object identity.
  const original = value => value?.[symbols.original] ?? value
  const priorAgents = original(ctx.agents)
  const priorSessions = original(ctx.sessions)
  const priorController = original(ctx.sessionController)
  const fullProfile = ctx.get('genshinFullProfile')
  if (fullProfile === undefined && (priorController !== undefined || ctx.get('connection') !== undefined
      || ctx.get('typertGateway') !== undefined)) {
    throw new Error('native_dsh_client_assets_host_already_mounted')
  }
  // Resolve Host and client metadata from this kernel's package tree. An
  // identically-versioned second install still creates distinct Error/class
  // identities and breaks upstream nominal checks during Session recovery.
  if (fullProfile === undefined) await ctx.plugin(Loader, { baseUrl: import.meta.url })
  const entries = []
  for (const name of contract.dynamicModules) {
    const specifier = await localEntry(name, contract.localModules?.[name], localModuleBaseUrl)
    entries.push({ id: nativeDshSettingsEntryId(name), name: specifier, packageName: name,
      ...(name === '@deepseek-ai/dsh-client-locale' ? { config: { preference: 'zh' } } : {}),
      ...(name === '@deepseek-ai/dsh-client-ui-chat'
        ? { config: { transcriptView: 'compact', collapseTiming: 'completion', foldedReplies: 'final' } } : {}),
      ...(name === '@deepseek-ai/dsh-api-session-controller' ? { config: { nativeOpen: false } } : {}),
    })
  }
  const settings = fullProfile === undefined
    ? await mountNativeDshProfileSettings({ context: ctx, profileRoot, entries })
    : await fullProfile.extendClientRows(entries.map(({ packageName: _name, ...row }) => row))
  await ctx.loader.await()
  if (ctx.clientModules === undefined) throw new Error('native_dsh_client_assets_registry_missing')
  if (original(ctx.agents) !== priorAgents || original(ctx.sessions) !== priorSessions) {
    throw new Error('native_dsh_client_assets_owner_replaced')
  }
  // FiberState.FAILED is the public enum value 3 in the pinned Cordis 4.0.5
  // declarations (the const enum has no JavaScript runtime export).
  const failed = [...ctx.loader.entries()].filter(entry => entry.fiber?.state === 3)
  if (failed.length > 0) throw new Error(`native_dsh_client_assets_failed_entries:${
    failed.map(entry => entry.options.name).join(',')}`)
  const graph = ctx.clientModules.graph()
  same(graph.release, { version: contract.dshRelease, commit: contract.dshCommit },
    'native_dsh_client_assets_release_mismatch')
  const actualModules = [...graph.entries.map(entry => entry.id)].sort()
  const expectedModules = [...contract.dynamicModules].sort()
  if (JSON.stringify(actualModules) !== JSON.stringify(expectedModules)) {
    throw Object.assign(new Error('native_dsh_client_assets_graph_mismatch'), {
      details: { actual: actualModules, expected: expectedModules },
    })
  }
  const known = new Map(acceptedManifest.entries.map(entry => [entry.id, entry]))
  if (known.size !== acceptedManifest.entries.length || known.size !== graph.entries.length) {
    throw new Error('native_dsh_client_assets_manifest_entries_invalid')
  }
  for (const entry of graph.entries) {
    const expected = known.get(entry.id)
    if (expected === undefined) throw new Error('native_dsh_client_assets_entry_unreviewed')
    if (JSON.stringify([...(entry.inject ?? [])]) !== JSON.stringify(expected.inject)) {
      throw Object.assign(new Error('native_dsh_client_assets_inject_mismatch'), {
        details: { id: entry.id, actual: entry.inject ?? [], expected: expected.inject },
      })
    }
    same([...(entry.external ?? [])], expected.external, 'native_dsh_client_assets_external_mismatch')
    same(entry.immediately === true, expected.immediately, 'native_dsh_client_assets_immediate_mismatch')
    const response = await ctx.clientModules.fetchBundle(new Request(
      new URL(entry.url, 'https://genshin-shell.invalid/'),
    ))
    if (response.status !== 200) throw new Error('native_dsh_client_assets_bundle_missing')
    const source = await response.text()
    const trailer = `//# sourceMappingURL=??${entry.id}/client.js.map&rev=${entry.rev}\n`
    if (!source.endsWith(trailer)) throw new Error('native_dsh_client_assets_trailer_mismatch')
    const executable = source.slice(0, -trailer.length)
    if (digest(executable) !== expected.executableSha256
        || Buffer.byteLength(executable) !== expected.executableBytes) {
      throw new Error('native_dsh_client_assets_executable_changed')
    }
  }
  const injections = bootInjections(graph)
  const script = injections.find(row => row.kind === 'script')
  if (script === undefined || digest(script.text) !== acceptedManifest.bootstrapScriptSha256) {
    throw new Error('native_dsh_client_assets_bootstrap_changed')
  }
  return Object.freeze({
    boot: () => structuredClone(injections),
    bundle: request => ctx.clientModules.fetchBundle(request instanceof Request
      ? request : new Request(new URL(request, 'https://genshin-shell.invalid/'))),
    graph: () => ctx.clientModules.graph(),
    loaderStates: () => [...ctx.loader.entries()].map(entry => ({ name: entry.options.name,
      state: entry.fiber?.state ?? null })),
    settings,
    // Deployment description only. Release trust and App permissions remain
    // independently enforced by the Host and the accepted graph.
    productionOwner: contract.productionOwner,
  })
}
