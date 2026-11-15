/** Actual installed payload identity; no package resolver or plugin execution. */
import { createHash } from 'node:crypto'
import { createReadStream } from 'node:fs'
import { lstat, readFile, readdir, realpath } from 'node:fs/promises'
import { isAbsolute, join, relative, resolve, sep } from 'node:path'

export const DEPENDENCY_PAYLOAD_CONTRACT = 'genshin-node-dependency-payloads-v1'
export const DEPENDENCY_METADATA_POLICY = 'pnpm-release-metadata-v2'
const fail = code => { throw Object.assign(new Error(code), { code }) }
const hash = bytes => createHash('sha256').update(bytes).digest('hex')
const canonical = value => Array.isArray(value) ? '[' + value.map(canonical).join(',') + ']'
  : value !== null && typeof value === 'object' ? '{' + Object.keys(value).sort()
    .map(key => JSON.stringify(key) + ':' + canonical(value[key])).join(',') + '}' : JSON.stringify(value)

async function hashFile(path) {
  const digest = createHash('sha256')
  for await (const chunk of createReadStream(path)) digest.update(chunk)
  return digest.digest('hex')
}

const shimMarker = '# cmd-shim-target='
export async function projectDependencyShim(name, path) {
  if (name.split('/').at(-2) !== '.bin') return [name, null]
  if (name.slice(-4).toLowerCase() === '.cmd') return [name.slice(0, -4) + '.cmd', null]
  if (name.endsWith('.ps1')) return [name, null]
  const raw = await readFile(path)
  const offset = raw.indexOf(shimMarker)
  if (offset < 0) return [name, null]
  const target = raw.subarray(offset).toString('latin1')
  const prefix = raw.subarray(0, offset)
  if (raw.indexOf(shimMarker, offset + shimMarker.length) >= 0 || !/^#!\/bin\/sh\r?\n/.test(prefix.toString('latin1'))
      || prefix.at(-1) !== 10 || !/^# cmd-shim-target=(?:[A-Za-z]:[/\\]|\/|\\\\)[^\0\r\n]+(?:\r?\n)?$/.test(target)) {
    fail('dsh_dependency_shim_marker_invalid')
  }
  return [name, prefix]
}

export async function dependencyPayloadInventory(runtimeRoot) {
  const root = resolve(runtimeRoot, 'node_modules')
  const info = await lstat(root)
  if (!info.isDirectory() || info.isSymbolicLink()) fail('dsh_dependency_payload_root')
  const files = {}, aliases = {}, directories = new Set([''])
  let totalBytes = 0
  const walk = async prefix => {
    const folder = prefix ? join(root, ...prefix.split('/')) : root
    for (const item of (await readdir(folder, { withFileTypes: true })).sort((a, b) => a.name.localeCompare(b.name))) {
      const name = prefix ? prefix + '/' + item.name : item.name
      if (name === '.pnpm-workspace-state-v1.json') {
        if (!item.isFile()) fail('dsh_dependency_metadata_type')
        continue // Install-only cache: intentionally absent in release trees.
      }
      const path = join(folder, item.name)
      if (item.isSymbolicLink()) {
        const target = await realpath(path)
        const suffix = relative(root, target)
        if (suffix.startsWith('..') || isAbsolute(suffix)) fail('dsh_dependency_alias_escape')
        aliases[name] = suffix.split(sep).join('/')
      } else if (item.isDirectory()) {
        directories.add(name)
        await walk(name)
      } else if (item.isFile()) {
        const [releaseName, shimBytes] = await projectDependencyShim(name, path)
        if (Object.hasOwn(files, releaseName)) fail('dsh_dependency_shim_name_collision')
        if (name === '.modules.yaml') {
          const data = JSON.parse(await readFile(path, 'utf8'))
          if (data === null || typeof data !== 'object' || Array.isArray(data)) fail('dsh_dependency_metadata_type')
          delete data.storeDir
          delete data.virtualStoreDir
          delete data.prunedAt
          const bytes = Buffer.from(canonical(data) + '\n')
          files[name] = hash(bytes)
          totalBytes += bytes.length
        } else if (shimBytes !== null) {
          files[releaseName] = hash(shimBytes)
          totalBytes += shimBytes.length
        } else {
          files[releaseName] = await hashFile(path)
          totalBytes += (await lstat(path)).size
        }
      } else fail('dsh_dependency_payload_type')
    }
  }
  await walk('')
  for (const target of Object.values(aliases)) {
    if (!Object.hasOwn(files, target) && !directories.has(target)) fail('dsh_dependency_alias_uninventoried')
  }
  if (!Object.hasOwn(files, '.modules.yaml')) fail('dsh_dependency_metadata_missing')
  return { contract: DEPENDENCY_PAYLOAD_CONTRACT, metadata_policy: DEPENDENCY_METADATA_POLICY,
    files: Object.fromEntries(Object.entries(files).sort(([a], [b]) => a < b ? -1 : a > b ? 1 : 0)),
    aliases: Object.fromEntries(Object.entries(aliases).sort(([a], [b]) => a < b ? -1 : a > b ? 1 : 0)),
    total_bytes: totalBytes }
}
