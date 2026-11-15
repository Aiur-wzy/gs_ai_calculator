/** Authenticated inherited-launch Session Controller fixture; no network. */
import { createNativeDshSessionHost } from './native-dsh-session-host.mjs'
import { NativeDshMuxGateway } from './native-dsh-mux-gateway.mjs'
import { connectInheritedMux } from './native-mux-bootstrap.mjs'
import { createPhase5bOfficialFakeFetch } from './phase5b-official-fake-fetch.mjs'
import { readFile } from 'node:fs/promises'
import { join } from 'node:path'

const { carrier } = await connectInheritedMux()
globalThis.fetch = createPhase5bOfficialFakeFetch({ requireV2Context: false })
let host
let gateway
let tail = Promise.resolve()
try {
  host = await createNativeDshSessionHost({ profileRoot: process.argv[2], mirrorRoot: process.argv[3],
    clientAssets: {
      contract: JSON.parse(await readFile(new URL('../../frontend/dsh-client-composition.json', import.meta.url))),
      acceptedManifest: JSON.parse(await readFile(new URL('../../frontend/dsh-client-accepted-manifest.json', import.meta.url))),
      localModuleBaseUrl: new URL('../../frontend/', import.meta.url).href,
    } })
  await host.kernel.secretController.set({ provider: 'deepseek-official', credential_generation: 1,
    secret: 'session-controller-pipe-offline-sentinel' })
  gateway = new NativeDshMuxGateway({ handler: host.handler, wireStream: host.wireStream, assets: host.assets,
    spoolRoot: join(process.argv[2], 'logical-spool'),
    peer: host.createPeer(),
    send(packet) {
      carrier.enqueue(packet)
      tail = tail.then(() => carrier.writeNext())
      return tail
    },
    onFailure() { carrier.close() },
  })
  while (true) {
    const packet = await carrier.readNext()
    if (packet.header.lane === 'control' && packet.header.payload_kind === 'fixture_shutdown') {
      carrier.settleRead('accepted')
      break
    }
    gateway.accept(packet)
    carrier.settleRead('accepted')
  }
} finally {
  await gateway?.close()
  carrier.close()
  await host?.dispose()
}
