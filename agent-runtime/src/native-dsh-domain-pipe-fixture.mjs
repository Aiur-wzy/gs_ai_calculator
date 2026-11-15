/** Real public Session+App tools over inherited private mux; no network. */
import { readFile } from 'node:fs/promises'
import { join } from 'node:path'
import { createNativeDshSessionHost } from './native-dsh-session-host.mjs'
import { NativeDshMuxGateway } from './native-dsh-mux-gateway.mjs'
import { NativeDshDomainTransport } from './native-dsh-domain-transport.mjs'
import { connectInheritedMux } from './native-mux-bootstrap.mjs'
import { createNativeDshDomainFixtureFetch } from './native-dsh-domain-fixture-fetch.mjs'

const { carrier } = await connectInheritedMux()
const fixture = process.argv[4] === undefined ? {} : JSON.parse(process.argv[4])
const fake = createNativeDshDomainFixtureFetch(fixture)
globalThis.fetch = (url, init) => String(url) === 'https://api.deepseek.com/v1/tools/search'
  ? Promise.resolve(Response.json({ results: [{ url: 'https://example.test/source', title: 'Offline evidence',
    snippet: 'Private-pipe research fixture', date: '2026-11-05' }] })) : fake(url, init)
let host, gateway
let tail = Promise.resolve()
const send = packet => {
  carrier.enqueue(packet)
  tail = tail.then(() => carrier.writeNext())
  return tail
}
const domainTransport = new NativeDshDomainTransport({ send })
try {
  host = await createNativeDshSessionHost({ profileRoot: process.argv[2], mirrorRoot: process.argv[3], domainTransport,
    accounting: fixture.accounting === true,
    clientAssets: {
      contract: JSON.parse(await readFile(new URL('../../frontend/dsh-client-composition.json', import.meta.url))),
      acceptedManifest: JSON.parse(await readFile(new URL('../../frontend/dsh-client-accepted-manifest.json', import.meta.url))),
      localModuleBaseUrl: new URL('../../frontend/', import.meta.url).href,
    } })
  await host.kernel.secretController.set({ provider: 'deepseek-official', credential_generation: 1,
    secret: 'domain-tools-pipe-offline-sentinel' })
  gateway = new NativeDshMuxGateway({ handler: host.handler, wireStream: host.wireStream,
    spoolRoot: join(process.argv[2], 'logical-spool'),
    assets: host.assets, domainTransport, peer: host.createPeer(), send,
    onFailure() { carrier.close() } })
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
  domainTransport.close()
  await gateway?.close()
  carrier.close()
  await host?.dispose()
}
