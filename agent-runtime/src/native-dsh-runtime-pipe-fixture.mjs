/** Offline provider injection around the actual production runner. */
import { connectInheritedMux } from './native-mux-bootstrap.mjs'
import { readNativeDshRuntimeConfig } from './native-dsh-runtime-config.mjs'
import { runNativeDshRuntime, nativeDshRuntimeErrorCode } from './native-dsh-runtime.mjs'
import { createPhase5bOfficialFakeFetch } from './phase5b-official-fake-fetch.mjs'

const { carrier } = await connectInheritedMux()
try {
  globalThis.fetch = createPhase5bOfficialFakeFetch({ requireV2Context: false })
  const config = await readNativeDshRuntimeConfig(process.argv.slice(2))
  await runNativeDshRuntime({ carrier, config })
} catch (error) {
  console.error(nativeDshRuntimeErrorCode(error))
  process.exitCode = 1
} finally { carrier.close() }
