/** Deterministic integrity evidence for the local native-Agent runtime. */
import { createHash } from 'node:crypto'
import { createReadStream } from 'node:fs'
import { lstat, readFile, readdir, realpath } from 'node:fs/promises'
import { dirname, isAbsolute, join, relative, resolve, sep } from 'node:path'
import { fileURLToPath } from 'node:url'

import { canonicalEnvelopeJson } from './bridge-envelope.mjs'

const RUNTIME_ROOT = resolve(dirname(fileURLToPath(import.meta.url)), '..')
const SHA256 = /^[0-9a-f]{64}$/u
const RELATIVE_FILE = /^(?!\/)(?!.*(?:^|\/)\.\.(?:\/|$))[A-Za-z0-9._@+/-]+$/u
const MAX_LOCAL_FILE_BYTES = 64 * 1024 * 1024
const SELFCHECK_ENTRY = 'src/native-dsh-selfcheck-entry.mjs'
const SELFCHECK_DEFERRED_IMPORTS = new Set([
  'src/native-dsh-selfcheck-provider.mjs', 'src/native-mux-bootstrap.mjs',
  'src/native-dsh-session-host.mjs', 'src/native-dsh-mux-gateway.mjs',
  'src/native-dsh-domain-transport.mjs', 'src/native-dsh-runtime-config.mjs',
])

export const DSH_CLIENT_SHELL_RUNTIME_FILES = Object.freeze([
  '.npmrc', 'package.json', 'pnpm-lock.yaml', 'pnpm-workspace.yaml',
  'patches/dsh-client-ui-conversation-0.2.2-rc.3-public-draft.patch',
  'src/native-app-tool-catalog.mjs', 'src/native-app-tool-schema.mjs', 'src/native-app-system-failure.mjs',
  'src/bridge-envelope.mjs', 'src/dsh-client-shell-runtime-contract.mjs',
  'src/dependency-payloads.mjs',
  'src/dsh-task-review.mjs',
  'src/fixed-root-filesystem-contract.mjs', 'src/fixed-root-filesystem.mjs',
  'src/memory-credentials.mjs',
  'src/native-deepseek-kernel.mjs', 'src/native-dsh-accounting.mjs', 'src/native-dsh-client-assets.mjs',
  'src/native-dsh-domain-tools.mjs', 'src/native-dsh-domain-transport.mjs',
  'src/native-dsh-app-context-projection.mjs',
  'src/native-dsh-input-snapshot.mjs',
  'src/native-dsh-full-profile.mjs', 'src/native-dsh-app-settings.mjs',
  'src/native-dsh-profile-config.mjs', 'src/native-dsh-task-transport.mjs', 'src/native-dsh-job-drain.mjs',
  'src/native-dsh-logical-transport.mjs', 'src/native-logical-stream.mjs',
  'src/native-dsh-profile-settings.mjs',
  'src/native-dsh-generic-capabilities.mjs', 'src/native-dsh-mux-gateway.mjs',
  'src/native-dsh-runtime-config.mjs', 'src/native-dsh-runtime-entry.mjs',
  'src/native-dsh-runtime.mjs', 'src/native-dsh-session-host.mjs',
  'src/native-dsh-selfcheck-entry.mjs', 'src/native-dsh-selfcheck-guards.mjs', 'src/native-dsh-selfcheck-provider.mjs',
  'src/native-dsh-settings.mjs', 'src/native-dsh-web-stack.mjs',
  'src/native-mux-bootstrap.mjs', 'src/native-mux-launch.mjs',
  'src/native-mux-pipe-client.mjs', 'src/native-mux-protocol.mjs',
  'src/native-pipe-multiplexer.mjs',
  'src/native-provider-admission.mjs', 'src/native-provider-proxy.mjs',
  'src/native-retry-admission.mjs', 'src/native-shell-credentials.mjs', 'src/native-protected-credentials.mjs',
  'src/production-composition-contract.mjs', 'src/publication-usage.mjs', 'src/runtime-integrity.mjs',
  'src/runtime-version.mjs', 'src/secret-control.mjs',
].sort())

export class RuntimeIntegrityError extends Error {
  constructor(code) {
    super(code)
    this.name = 'RuntimeIntegrityError'
    this.code = code
  }
}

function fail(code) {
  throw new RuntimeIntegrityError(code)
}

function sha256(value) {
  return createHash('sha256').update(value).digest('hex')
}

function samePath(left, right) {
  const normalize = value => process.platform === 'win32' ? value.toLowerCase() : value
  return normalize(resolve(left)) === normalize(resolve(right))
}

function pathInside(parent, child) {
  const suffix = relative(parent, child)
  return suffix === '' || (!suffix.startsWith('..') && !isAbsolute(suffix))
}

function validateFileList(value) {
  if (!Array.isArray(value) || value.length === 0) fail('runtime_integrity_file_list')
  const checked = value.map(item => {
    if (typeof item !== 'string' || !RELATIVE_FILE.test(item) || item.includes('\\')) {
      fail('runtime_integrity_file_name')
    }
    return item
  })
  if (JSON.stringify(checked) !== JSON.stringify([...new Set(checked)].sort())) {
    fail('runtime_integrity_file_order')
  }
  if (!checked.includes('pnpm-lock.yaml') || !checked.includes('src/runtime-integrity.mjs')) {
    fail('runtime_integrity_file_list')
  }
  return checked
}

async function assertRoot(root) {
  const absolute = resolve(root)
  const info = await lstat(absolute).catch(() => fail('runtime_integrity_root_missing'))
  if (!info.isDirectory() || info.isSymbolicLink()) fail('runtime_integrity_root_alias')
  const canonical = await realpath(absolute).catch(() => fail('runtime_integrity_root_missing'))
  if (!samePath(absolute, canonical)) fail('runtime_integrity_root_alias')
  return absolute
}

async function assertKnownCriticalEntries(root, admitted) {
  const names = await readdir(join(root, 'src')).catch(() => fail('runtime_integrity_src_missing'))
  const sidecars = names.filter(
    name => /^native-(?:.*-)?sidecar-entry\.mjs$/u.test(name),
  ).sort()
  const contracts = names.filter(name => /^phase.*-runtime-contract\.mjs$/u.test(name)).sort()
  const admittedNames = admitted.map(name => name.split('/').at(-1))
  const admittedSidecars = admittedNames.filter(
    name => /^native-(?:.*-)?sidecar-entry\.mjs$/u.test(name),
  ).sort()
  const admittedContracts = admittedNames.filter(
    name => /^phase.*-runtime-contract\.mjs$/u.test(name),
  ).sort()
  const same = (left, right) => JSON.stringify(left) === JSON.stringify(right)
  // Both development and release admit only the current execution closure.
  // Retired sidecars cannot return through an old full-phase whitelist.
  if (!same(sidecars, admittedSidecars)) {
    fail('runtime_integrity_unknown_sidecar_entry')
  }
  if (!same(contracts, admittedContracts)) {
    fail('runtime_integrity_unknown_contract_entry')
  }
}

function assertLocalImportClosure({ root, filename, source, admitted }) {
  // The named offline selfcheck must install its guards before DSH evaluation.
  // Its six reviewed deferred loads remain enumerable members of this closure;
  // no ambient or computed loader is admitted by this exception.
  const dynamic = /\bimport(?:\s|\/\*[\s\S]*?\*\/|\/\/[^\r\n]*(?:\r?\n|$))*\(/gu
  for (const match of source.matchAll(dynamic)) {
    const literal = /^import\s*\(\s*(['"])(\.[A-Za-z0-9._/-]*)\1\s*\)/u.exec(source.slice(match.index))
    if (filename !== SELFCHECK_ENTRY || !/\bawait\s*$/u.test(source.slice(0, match.index)) || !literal) {
      fail('runtime_integrity_dynamic_import')
    }
    const target = resolve(dirname(join(root, ...filename.split('/'))), literal[2])
    if (!pathInside(root, target)) fail('runtime_integrity_import_escape')
    const imported = relative(root, target).split(sep).join('/')
    if (!admitted.has(imported) || !SELFCHECK_DEFERRED_IMPORTS.has(imported)) fail('runtime_integrity_unlisted_import')
  }
  if (/\b(?:createRequire|require|getBuiltinModule)\s*(?:\?\.)?\s*\(/u.test(source)
      || /\bprocess\s*(?:\?\.)?\s*(?:\.\s*getBuiltinModule|\[\s*['"]getBuiltinModule['"]\s*\])/u
        .test(source)) {
    fail('runtime_integrity_ambient_loader')
  }
  const pattern = /\bfrom\s*['"](\.[^'"]+)['"]|\bimport\s*['"](\.[^'"]+)['"]/gu
  for (const match of source.matchAll(pattern)) {
    const specifier = match[1] ?? match[2]
    const target = resolve(dirname(join(root, ...filename.split('/'))), specifier)
    if (!pathInside(root, target)) fail('runtime_integrity_import_escape')
    const imported = relative(root, target).split(sep).join('/')
    if (!admitted.has(imported)) fail('runtime_integrity_unlisted_import')
  }
  // Public profile Loader entries can be expressed as local module URLs
  // instead of ES imports. They are executable closure members too.
  const moduleUrls = /\bnew\s+URL\s*\(\s*['"](\.[^'"]+\.mjs)['"]\s*,\s*import\.meta\.url\s*\)/gu
  for (const match of source.matchAll(moduleUrls)) {
    const target = resolve(dirname(join(root, ...filename.split('/'))), match[1])
    if (!pathInside(root, target)) fail('runtime_integrity_import_escape')
    if (!admitted.has(relative(root, target).split(sep).join('/'))) fail('runtime_integrity_unlisted_import')
  }
}

/** Hash the complete project-owned file closure for one executable sidecar. */
export async function auditRuntimeClosure({ phase, files, root = RUNTIME_ROOT }) {
  if (phase !== 'dsh_client_shell') fail('runtime_integrity_phase')
  const checkedFiles = validateFileList(files)
  const absolute = await assertRoot(root)
  await assertKnownCriticalEntries(absolute, checkedFiles)
  const hashes = {}
  const sources = new Map()
  for (const filename of checkedFiles) {
    const target = resolve(absolute, ...filename.split('/'))
    if (!pathInside(absolute, target)) fail('runtime_integrity_file_escape')
    const info = await lstat(target).catch(() => fail('runtime_integrity_file_missing'))
    if (!info.isFile() || info.isSymbolicLink() || info.size > MAX_LOCAL_FILE_BYTES) {
      fail('runtime_integrity_file_type')
    }
    const canonical = await realpath(target).catch(() => fail('runtime_integrity_file_missing'))
    if (!samePath(target, canonical) || !pathInside(absolute, canonical)) {
      fail('runtime_integrity_file_alias')
    }
    const content = await readFile(target)
    hashes[filename] = sha256(content)
    if (filename.endsWith('.mjs')) sources.set(filename, content.toString('utf8'))
  }
  const admitted = new Set(checkedFiles)
  for (const [filename, source] of sources) {
    assertLocalImportClosure({ root: absolute, filename, source, admitted })
  }
  const dependencyLockHash = hashes['pnpm-lock.yaml']
  const runtimeClosureHash = sha256(Buffer.from(canonicalEnvelopeJson({
    contract: 'genshin-native-runtime-closure-v1',
    phase,
    file_hashes: Object.entries(hashes),
  }), 'utf8'))
  return Object.freeze({
    contract: 'genshin-native-runtime-closure-v1',
    phase,
    files: Object.freeze(hashes),
    dependencyLockHash,
    runtimeClosureHash,
  })
}

/** Hash the exact Node executable admitted into the release contract. */
export async function auditNodeExecutable(path = process.execPath) {
  if (typeof path !== 'string' || !isAbsolute(path)) fail('runtime_integrity_node_path')
  const target = resolve(path)
  const info = await lstat(target).catch(() => fail('runtime_integrity_node_missing'))
  if (!info.isFile() || info.isSymbolicLink()) fail('runtime_integrity_node_type')
  const canonical = await realpath(target).catch(() => fail('runtime_integrity_node_missing'))
  if (!samePath(target, canonical)) fail('runtime_integrity_node_alias')
  const hash = createHash('sha256')
  await new Promise((resolveRead, rejectRead) => {
    const stream = createReadStream(target)
    stream.on('data', chunk => hash.update(chunk))
    stream.once('error', () => rejectRead(new RuntimeIntegrityError('runtime_integrity_node_read')))
    stream.once('end', resolveRead)
  })
  const nodeBinarySha256 = hash.digest('hex')
  if (!SHA256.test(nodeBinarySha256)) fail('runtime_integrity_node_hash')
  return Object.freeze({
    path: canonical,
    nodeVersion: process.versions.node,
    nodeBinarySha256,
  })
}

export { RUNTIME_ROOT }
