/** Non-fixture production-capable entry; production selection stays Host-owned. */
import { connectInheritedMux } from './native-mux-bootstrap.mjs'
import { readNativeDshRuntimeConfig } from './native-dsh-runtime-config.mjs'
import { runNativeDshRuntime, nativeDshRuntimeErrorCode } from './native-dsh-runtime.mjs'

let carrier
try {
  ;({ carrier } = await connectInheritedMux())
  const config = await readNativeDshRuntimeConfig(process.argv.slice(2))
  await runNativeDshRuntime({ carrier, config })
} catch (error) {
  // No raw provider error, payload, path, secret or stack is printed here.
  console.error(nativeDshRuntimeErrorCode(error))
  process.exitCode = 1
} finally { carrier?.close() }
