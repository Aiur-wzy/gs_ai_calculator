/** Real Python<->Node pipe fixture; uses DSH public Remote methods, no provider. */
import { createConnection } from 'node:net'
import { NativeMuxPipeClient } from './native-mux-pipe-client.mjs'
import { NativeDshMuxGateway, DSH_MUX_GATEWAY_CONTRACT } from './native-dsh-mux-gateway.mjs'
import { createDshMuxFixtureHost } from './native-dsh-mux-fixture-host.mjs'
import { assertPinnedNodeVersion } from './runtime-version.mjs'
import { connectInheritedMux } from './native-mux-bootstrap.mjs'
import { mkdtemp, rm } from 'node:fs/promises'
import { join } from 'node:path'

assertPinnedNodeVersion()
const pipeName = process.argv[2]
const host = await createDshMuxFixtureHost()
const spoolRoot = await mkdtemp(join(process.cwd(), '.codex-tmp/mux-gateway-'))
let carrier
if (pipeName === undefined) {
  carrier = (await connectInheritedMux()).carrier
} else {
  const identity = JSON.parse(process.argv[3])
  const socket = createConnection(pipeName)
  await new Promise((resolve, reject) => {
    socket.once('connect', resolve)
    socket.once('error', reject)
  })
  carrier = new NativeMuxPipeClient(socket, {
    senderRole: 'runtime', peerSenderRole: 'host', ...identity,
  })
}
let tail = Promise.resolve()
let failure = null
const gateway = new NativeDshMuxGateway({
  ...host,
  spoolRoot,
  send(packet) {
    carrier.enqueue(packet)
    tail = tail.then(() => carrier.writeNext())
    return tail
  },
  onFailure(error) { failure = error; carrier.close() },
})
try {
  while (true) {
    const packet = await carrier.readNext()
    if (packet.header.lane === 'control' && packet.header.payload_kind === 'fixture_shutdown') {
      carrier.settleRead('accepted')
      await gateway.close()
      await gateway.whenIdle()
      break
    }
    gateway.accept(packet)
    carrier.settleRead('accepted')
  }
  if (failure !== null) throw failure
  await tail
  process.stdout.write(`${JSON.stringify({
    contract: DSH_MUX_GATEWAY_CONTRACT, status: 'passed', productionOwner: false,
    metrics: host.metrics, telemetry: carrier.diagnosticSnapshot(),
  })}\n`)
} finally {
  await gateway.close()
  carrier.close()
  await host.dispose()
  await rm(spoolRoot, { recursive: true, force: true })
}
