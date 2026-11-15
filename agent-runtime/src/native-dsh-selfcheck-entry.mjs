/** Fixed offline transport preload; the normal production entry remains main. */
import fs from 'node:fs'
import { dirname, join, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'
import { installSelfcheckGuards } from './native-dsh-selfcheck-guards.mjs'

const marker = new URL(import.meta.url).searchParams.get('context')
if (marker === null) throw new Error('selfcheck_provider_context_required')
const context = JSON.parse(fs.readFileSync(fileURLToPath(marker), 'utf8'))
const root = resolve(context.sandbox_root), packaged = resolve(context.package_root)
if (context.contract !== 'genshin-offline-production-provider-v1' || process.argv.length !== 5
    || resolve(process.argv[1]) !== join(dirname(fileURLToPath(import.meta.url)), 'native-dsh-runtime-entry.mjs')
    || resolve(process.argv[1]) !== resolve(context.main_entry)
    || resolve(dirname(fileURLToPath(marker))) !== root) throw new Error('selfcheck_provider_context_invalid')
const guards = installSelfcheckGuards({ sandboxRoot: root, packageRoot: packaged })
let provider
const persist = (exitObserved = false) => fs.writeFileSync(join(root, 'node-isolation.json'), JSON.stringify({
  contract: 'genshin-frozen-selfcheck-node-isolation-v1', counters: guards.counts, denied: guards.denied,
  executed_forbidden_operations: 0, effective_environment_keys: Object.keys(process.env).sort(),
  main_entry: process.argv[1], provider_requests: provider?.requestCount ?? 0,
  process_exit_observed: exitObserved, observation: 'continuously_persisted_transport_and_guard_facts',
}))
for (const group of ['normal', 'negative']) guards.counts[group] = new Proxy(guards.counts[group], {
  set(target, key, value) { target[key] = value; persist(); return true },
})
guards.negativeProbe(() => fs.writeFileSync(join(root, '..', 'selfcheck-outside-denied.json'), 'never'))
guards.negativeProbe(() => guards.denyNetwork())
guards.negativeProbe(() => guards.denyCredential())
const { selfcheckProvider } = await import('./native-dsh-selfcheck-provider.mjs')
provider = selfcheckProvider(JSON.parse(fs.readFileSync(join(root, 'fixed-calls.json'), 'utf8')), guards)
globalThis.fetch = async (...args) => {
  try { return await provider(...args) } finally { persist() }
}
process.once('exit', () => {
  guards.assertClean()
  persist(true)
})
