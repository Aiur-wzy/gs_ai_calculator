/** Independent integrity identity for the DSH client-shell execution closure. */
import { createHash } from 'node:crypto'
import { lstat, readFile, readdir, realpath } from 'node:fs/promises'
import { dirname, isAbsolute, join, relative, resolve, sep } from 'node:path'
import { fileURLToPath } from 'node:url'
import { auditNodeExecutable, auditRuntimeClosure, DSH_CLIENT_SHELL_RUNTIME_FILES } from './runtime-integrity.mjs'
import { PINNED_NODE_VERSION, assertPinnedNodeVersion } from './runtime-version.mjs'
import { dependencyPayloadInventory, projectDependencyShim } from './dependency-payloads.mjs'

export const DSH_CLIENT_SHELL_PHASE = 'dsh_client_shell'
export const DSH_CLIENT_SHELL_MANIFEST = 'genshin-native-dsh-client-shell-manifest-v1'
export const DSH_CLIENT_SHELL_ENTRY = 'src/native-dsh-runtime-entry.mjs'
export const DSH_CLIENT_SHELL_RELEASE = '0.2.2-rc.3'
const RUNTIME_ROOT = resolve(dirname(fileURLToPath(import.meta.url)), '..')
const hash = bytes => createHash('sha256').update(bytes).digest('hex')
function canonical(value) {
  if (Array.isArray(value)) return '[' + value.map(canonical).join(',') + ']'
  if (value !== null && typeof value === 'object') return '{' + Object.keys(value).sort()
    .map(key => JSON.stringify(key) + ':' + canonical(value[key])).join(',') + '}'
  const result = JSON.stringify(value)
  if (result === undefined) fail('dsh_shell_manifest_json')
  return result
}
const identity = value => hash(Buffer.from(canonical(value), 'utf8'))
const samePath = (a, b) => process.platform === 'win32' ? a.toLowerCase() === b.toLowerCase() : a === b
const fail = code => { throw Object.assign(new Error(code), { code }) }
const filename = value => typeof value === 'string' && /^(?!\/)(?!.*(?:^|\/)\.\.(?:\/|$))[A-Za-z0-9._@+/-]+$/u.test(value)
const FIELDS = ['contract', 'phase', 'app_version', 'runtime_generation', 'entry', 'node_version',
  'dsh_release', 'runtime_files', 'runtime_closure_hash', 'dependency_lock_hash', 'node_binary_sha256',
  'dependency_cohort', 'dependency_payloads', 'asset_files', 'asset_closure_hash', 'client_composition_hash',
  'client_accepted_hash', 'client_accepted_semantic_hash', 'manifest_hash']

async function trustedFile(root, name) {
  if (!filename(name)) fail('dsh_shell_asset_file_name')
  const target = resolve(root, ...name.split('/'))
  const suffix = relative(root, target)
  if (suffix.startsWith('..') || isAbsolute(suffix)) fail('dsh_shell_asset_escape')
  const info = await lstat(target).catch(() => fail('dsh_shell_asset_missing'))
  if (!info.isFile() || info.isSymbolicLink()) fail('dsh_shell_asset_type')
  const canonical = await realpath(target).catch(() => fail('dsh_shell_asset_missing'))
  if (!samePath(target, canonical)) fail('dsh_shell_asset_alias')
  return readFile(target)
}

async function descendants(root, prefix) {
  const target = join(root, ...prefix.split('/'))
  const info = await lstat(target).catch(() => fail('dsh_shell_asset_missing'))
  if (!info.isDirectory() || info.isSymbolicLink()) fail('dsh_shell_asset_type')
  const values = []
  for (const item of await readdir(target, { withFileTypes: true })) {
    const name = prefix ? prefix + '/' + item.name : item.name
    if (item.isDirectory()) values.push(...await descendants(root, name))
    else if (item.isFile()) values.push(name)
    else fail('dsh_shell_asset_type')
  }
  return values
}

async function dependencyCohort(root) {
  const manifest = JSON.parse(await trustedFile(root, 'package.json'))
  const cohort = {}
  for (const [name, version] of Object.entries(manifest.dependencies).sort(([a], [b]) => a.localeCompare(b))) {
    if (!name.startsWith('@deepseek-ai/')) continue
    if ((name === '@deepseek-ai/dsh' || name.startsWith('@deepseek-ai/dsh-')) && version !== DSH_CLIENT_SHELL_RELEASE) {
      fail('dsh_shell_dependency_cohort_mixed')
    }
    const path = join(root, 'node_modules', ...name.split('/'), 'package.json')
    const bytes = await readFile(path).catch(() => fail('dsh_shell_dependency_missing'))
    const actual = JSON.parse(bytes)
    if (actual.name !== name || actual.version !== version) fail('dsh_shell_dependency_identity')
    const packageRoot = await realpath(dirname(path))
    const files = {}
    for (const file of (await descendants(packageRoot, '')).sort()) {
      const raw = await trustedFile(packageRoot, file)
      const [releaseName, projected] = await projectDependencyShim(file, join(packageRoot, ...file.split('/')))
      if (Object.hasOwn(files, releaseName)) fail('dsh_dependency_shim_name_collision')
      files[releaseName] = hash(projected ?? raw)
    }
    cohort[name] = { version, package_json_sha256: hash(bytes), files }
  }
  return cohort
}

export async function auditDshClientShell({ runtimeRoot = RUNTIME_ROOT, assetsRoot, nodeExecutable = process.execPath } = {}) {
  if (typeof assetsRoot !== 'string' || !isAbsolute(assetsRoot)) fail('dsh_shell_asset_root')
  const root = resolve(runtimeRoot)
  const assets = resolve(assetsRoot)
  const closure = await auditRuntimeClosure({ phase: DSH_CLIENT_SHELL_PHASE, files: DSH_CLIENT_SHELL_RUNTIME_FILES, root })
  if (DSH_CLIENT_SHELL_RUNTIME_FILES.some(name => /(?:probe|fixture|fake|qualification|sidecar-dev)/u.test(name))) {
    fail('dsh_shell_nonproduction_closure')
  }
  const compositionBytes = await trustedFile(assets, 'dsh-client-composition.json')
  const acceptedBytes = await trustedFile(assets, 'dsh-client-accepted-manifest.json')
  const composition = JSON.parse(compositionBytes)
  const accepted = JSON.parse(acceptedBytes)
  if (composition.dshRelease !== DSH_CLIENT_SHELL_RELEASE || accepted.dshRelease !== DSH_CLIENT_SHELL_RELEASE
      || composition.dshCommit !== accepted.dshCommit
      || JSON.stringify(composition.dynamicModules) !== JSON.stringify(accepted.entryOrder)) {
    fail('dsh_shell_client_cohort')
  }
  const { semanticSha256, ...unsigned } = accepted
  if (hash(Buffer.from(JSON.stringify(unsigned))) !== semanticSha256) fail('dsh_shell_client_accepted_digest')
  const names = ['dsh-client-composition.json', 'dsh-client-accepted-manifest.json', 'package.json', 'pnpm-lock.yaml',
    ...await descendants(assets, 'dist')]
  for (const local of Object.values(composition.localModules ?? {})) {
    if (!filename(local.hostEntry)) fail('dsh_shell_local_module_path')
    const folder = dirname(local.hostEntry).split(sep).join('/')
    names.push(local.hostEntry, folder + '/package.json', ...await descendants(assets, folder + '/lib'))
  }
  const files = {}
  for (const name of [...new Set(names)].sort()) files[name] = hash(await trustedFile(assets, name))
  if (!Object.hasOwn(files, 'dist/asset-manifest.json') || !Object.hasOwn(files, 'dist/index.html')) {
    fail('dsh_shell_platform_build_missing')
  }
  const node = await auditNodeExecutable(nodeExecutable)
  return {
    contract: DSH_CLIENT_SHELL_MANIFEST, phase: DSH_CLIENT_SHELL_PHASE,
    app_version: '1.0.1', runtime_generation: DSH_CLIENT_SHELL_PHASE,
    entry: DSH_CLIENT_SHELL_ENTRY, node_version: PINNED_NODE_VERSION, dsh_release: DSH_CLIENT_SHELL_RELEASE,
    runtime_files: closure.files, runtime_closure_hash: closure.runtimeClosureHash,
    dependency_lock_hash: closure.dependencyLockHash, node_binary_sha256: node.nodeBinarySha256,
    dependency_cohort: await dependencyCohort(root), dependency_payloads: await dependencyPayloadInventory(root), asset_files: files,
    asset_closure_hash: identity({ contract: 'genshin-native-dsh-client-assets-closure-v1', file_hashes: Object.entries(files) }),
    client_composition_hash: hash(compositionBytes), client_accepted_hash: hash(acceptedBytes),
    client_accepted_semantic_hash: semanticSha256,
  }
}

export async function buildDshClientShellManifest(options) {
  assertPinnedNodeVersion()
  const body = await auditDshClientShell(options)
  return { ...body, manifest_hash: identity(body) }
}

export async function verifyDshClientShellManifest(manifest, options) {
  if (manifest === null || typeof manifest !== 'object' || Array.isArray(manifest)
      || Object.keys(manifest).sort().join('\0') !== [...FIELDS].sort().join('\0')) fail('dsh_shell_manifest_shape')
  const { manifest_hash: expectedHash, ...body } = manifest
  if (identity(body) !== expectedHash) fail('dsh_shell_manifest_hash')
  const actual = await auditDshClientShell(options)
  if (canonical(actual) !== canonical(body)) fail('dsh_shell_manifest_evidence_mismatch')
  return { contract: DSH_CLIENT_SHELL_MANIFEST, valid: true,
    runtime_closure_hash: actual.runtime_closure_hash, asset_closure_hash: actual.asset_closure_hash }
}
