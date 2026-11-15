/** Deterministic Python<->Node raw mux carrier fixture; never production-owned. */
import { createHash } from 'node:crypto'
import { createConnection } from 'node:net'

import { NativeMuxPipeClient } from './native-mux-pipe-client.mjs'

const pipeName = process.argv[2]
if (typeof pipeName !== 'string' || pipeName.length === 0) {
  throw new Error('mux_pipe_fixture_name_required')
}

const socket = createConnection(pipeName)
await new Promise((resolve, reject) => {
  socket.once('connect', resolve)
  socket.once('error', reject)
})

const client = new NativeMuxPipeClient(socket, {
  senderRole: 'runtime',
  peerSenderRole: 'host',
  connectionGeneration: 1,
  connectionId: 'cross-language-connection',
  runtimeIncarnation: 'cross-language-runtime',
})

const outbound = Buffer.alloc(2 * 1024 * 1024 + 17, 0x4e)
client.enqueue({
  lane: 'legacy_turn',
  payloadKind: 'legacy_frame',
  payload: outbound,
  messageId: 'node-to-python',
})
await client.writeNext()

const inbound = await client.readNext({
  idleTimeoutMs: 10_000,
  partialFrameTimeoutMs: 10_000,
})
const expectedLength = 2 * 1024 * 1024 + 31
if (inbound.header.message_id !== 'python-to-node'
    || inbound.payload.length !== expectedLength
    || createHash('sha256').update(inbound.payload).digest('hex')
      !== createHash('sha256').update(Buffer.alloc(expectedLength, 0x48)).digest('hex')) {
  throw new Error('mux_pipe_fixture_payload_mismatch')
}
client.settleRead('accepted')

const snapshot = client.diagnosticSnapshot()
client.close()
process.stdout.write(`${JSON.stringify({
  contract: 'genshin-native-mux-pipe-cross-language-fixture-v1',
  status: 'passed',
  productionOwner: snapshot.productionOwner,
  sentFrames: snapshot.counters.sentFrames,
  acceptedFrames: snapshot.counters.acceptedFrames,
  outboundSha256: createHash('sha256').update(outbound).digest('hex'),
})}\n`)
