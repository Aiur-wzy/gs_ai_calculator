/** Explicit candidate generation only; does not modify accepted release pins. */
import { readFile, writeFile, mkdir, lstat, realpath } from 'node:fs/promises'
import { dirname, join, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'
import { buildDshClientShellManifest, verifyDshClientShellManifest } from '../agent-runtime/src/dsh-client-shell-runtime-contract.mjs'

const MAX_METADATA_BYTES = 4 * 1024 * 1024
const fail = code => { throw Object.assign(new Error(code), { code }) }
const samePath = (left, right) => process.platform === 'win32'
  ? left.toLowerCase() === right.toLowerCase() : left === right

/** Same exact installed-receipt check as release_supply_chain inventory.
 * No YAML projection, resolver or permission is added by this early check.
 * Full payload/cohort/platform verification still runs afterwards.
 */
export async function assertInstalledPnpmLock(runtimeRoot) {
  const root = resolve(runtimeRoot)
  for (const path of [join(root, 'node_modules'), join(root, 'node_modules', '.pnpm')]) {
    const info = await lstat(path).catch(() => fail('supply_chain_pnpm_store_missing'))
    if (!info.isDirectory() || info.isSymbolicLink() || !samePath(await realpath(path), path)) {
      fail('supply_chain_pnpm_store_missing')
    }
  }
  const read = async path => {
    const info = await lstat(path).catch(() => fail('dsh_shell_installation_lock_missing'))
    if (!info.isFile() || info.isSymbolicLink() || !samePath(await realpath(path), path)) {
      fail('supply_chain_metadata_invalid')
    }
    if (info.size > MAX_METADATA_BYTES) fail('supply_chain_metadata_too_large')
    const bytes = await readFile(path)
    if (bytes.length > MAX_METADATA_BYTES) fail('supply_chain_metadata_too_large')
    return bytes
  }
  const source = await read(join(root, 'pnpm-lock.yaml'))
  const installed = await read(join(root, 'node_modules', '.pnpm', 'lock.yaml'))
  if (!source.equals(installed)) fail('supply_chain_pnpm_installation_lock_mismatch')
  return true
}

export async function generateCandidateManifest(args) {
  const [runtime, assets, node, output] = args
  if ([runtime, assets, node, output].some(value => typeof value !== 'string' || value.length === 0)) {
    throw new Error('usage: pinned-node generate_dsh_client_shell_manifest.mjs runtimeRoot assetsRoot nodeExe candidateOutput')
  }
  const options = { runtimeRoot: resolve(runtime), assetsRoot: resolve(assets), nodeExecutable: resolve(node) }
  // Do this before scanning the 35k-file payload or creating a candidate.
  await assertInstalledPnpmLock(options.runtimeRoot)
  const manifest = await buildDshClientShellManifest(options)
  await mkdir(dirname(resolve(output)), { recursive: true })
  await writeFile(resolve(output), JSON.stringify(manifest, null, 2) + '\n', { flag: 'wx' })
  const checked = await verifyDshClientShellManifest(JSON.parse(await readFile(resolve(output), 'utf8')), options)
  return { ...checked, output: resolve(output), runtimeFiles: Object.keys(manifest.runtime_files).length,
    assetFiles: Object.keys(manifest.asset_files).length, dependencyPackages: Object.keys(manifest.dependency_cohort).length }
}

if (process.argv[1] && samePath(resolve(process.argv[1]), fileURLToPath(import.meta.url))) {
  console.log(JSON.stringify(await generateCandidateManifest(process.argv.slice(2))))
}
