/** Offline public DSH Fetch routes for bounded-memory private-pipe stress. */
import { setTimeout as delay } from 'node:timers/promises'
import { createDshMuxFixtureHost } from './native-dsh-mux-fixture-host.mjs'
import { NativeDshMuxGateway } from './native-dsh-mux-gateway.mjs'
import { connectInheritedMux } from './native-mux-bootstrap.mjs'

const { carrier } = await connectInheritedMux()
const host = await createDshMuxFixtureHost()
const metrics = { requests: 0, uploadBytes: 0, downloadBytes: 0, cancelled: 0 }
function generated(length, slow = false) {
  let sent = 0
  return new ReadableStream({
    async pull(controller) {
      if (sent === length) { controller.close(); return }
      if (slow) await delay(50)
      const size = Math.min(512 * 1024, length - sent)
      controller.enqueue(new Uint8Array(size).fill(79))
      sent += size
      metrics.downloadBytes += size
    },
    cancel() { metrics.cancelled += 1 },
  })
}
host.ctx.connection.fetch.register({ path: '/api/probe/blob', methods: ['POST'], requestBody: 'streaming',
  fetch(request) {
    metrics.requests += 1
    return new Response(request.body.pipeThrough(new TransformStream({
      transform(chunk, controller) { metrics.uploadBytes += chunk.length; controller.enqueue(chunk) },
    })), { headers: { 'content-type': 'application/octet-stream' } })
  } })
host.ctx.connection.fetch.register({ path: '/api/probe/generated', methods: ['GET'], requestBody: 'buffered',
  fetch(request) {
    metrics.requests += 1
    const url = new URL(request.url)
    const size = Number(url.searchParams.get('bytes'))
    return new Response(generated(size, url.searchParams.has('slow')),
      { headers: { 'content-type': 'application/octet-stream' } })
  } })
host.ctx.connection.fetch.register({ path: '/api/probe/metrics', methods: ['GET'], requestBody: 'buffered',
  fetch() { return Response.json(metrics) } })
let tail = Promise.resolve()
const gateway = new NativeDshMuxGateway({ ...host, spoolRoot: process.argv[2],
  assets: { boot: () => [], bundle: request => new Response(generated(Number(new URL(request.url).searchParams.get('bytes')))) },
  send(packet) { carrier.enqueue(packet); tail = tail.then(() => carrier.writeNext()); return tail },
  onFailure(error) { console.error(error.code ?? 'logical_fixture_failed'); carrier.close() },
})
try {
  while (true) {
    const packet = await carrier.readNext()
    if (packet.header.payload_kind === 'fixture_shutdown') {
      carrier.settleRead('accepted'); break
    }
    gateway.accept(packet)
    carrier.settleRead('accepted')
  }
} finally {
  await gateway.close()
  carrier.close()
  await host.dispose()
}
