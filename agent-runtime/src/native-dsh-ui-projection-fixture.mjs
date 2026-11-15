/** Test-only offline injection; all runtime/Controller/domain code is production. */
import { readFile } from 'node:fs/promises'
import { connectInheritedMux } from './native-mux-bootstrap.mjs'
import { readNativeDshRuntimeConfig } from './native-dsh-runtime-config.mjs'
import { runNativeDshRuntime, nativeDshRuntimeErrorCode } from './native-dsh-runtime.mjs'
import { createNativeDshDomainFixtureFetch } from './native-dsh-domain-fixture-fetch.mjs'

const { carrier } = await connectInheritedMux()
try {
  const script = JSON.parse(await readFile(process.argv[5], 'utf8'))
  const fake = createNativeDshDomainFixtureFetch(script)
  globalThis.fetch = (url, init) => String(url) === 'https://api.deepseek.com/v1/tools/search'
    ? Promise.resolve(Response.json({ results: [] })) : fake(url, init)
  await runNativeDshRuntime({ carrier, config: await readNativeDshRuntimeConfig(process.argv.slice(2, 5)) })
} catch (error) {
  console.error(nativeDshRuntimeErrorCode(error))
  process.exitCode = 1
} finally { carrier.close() }
