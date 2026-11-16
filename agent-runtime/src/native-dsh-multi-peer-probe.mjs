/** Same public DSH Host, separate owner Peers, no provider request. */
import assert from 'node:assert/strict'
import { mkdtemp, mkdir } from 'node:fs/promises'
import { randomUUID } from 'node:crypto'
import { join, resolve } from 'node:path'
import { createNativeDshSessionHost } from './native-dsh-session-host.mjs'
import { NativeDshMuxGateway, encodeDshFetchPayload, decodeDshFetchPayload } from './native-dsh-mux-gateway.mjs'

const base = resolve(process.argv[2] ?? '.codex-tmp/multi-peer')
await mkdir(base, { recursive: true })
const root = await mkdtemp(join(base, 'profile-'))
const originalFetch = globalThis.fetch
globalThis.fetch = () => { throw new Error('no network in peer qualification') }
let host, gateway
const peers = [], streams = []
const rpc = async (peer, endpoint, args) => {
  const response = await host.handler.fetch(new Request(`https://genshin-shell.invalid/api/${endpoint}`, {
    method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify({
      type: 'client-request', rpcId: randomUUID(), method: endpoint, payload: { args },
    }),
  }), peer)
  return (await response.json()).result
}
const events = async peer => {
  const control = new AbortController()
  const source = await host.wireStream.open('$events', { args: {} },
    { async *[Symbol.asyncIterator]() {} }, peer, control.signal)
  const iterator = source[Symbol.asyncIterator]()
  const ready = (await iterator.next()).value
  assert.equal(ready.type, 'ready')
  const stream = { peer, control, iterator, clientId: ready.clientId }
  streams.push(stream)
  return stream
}
const nextKind = async (stream, type) => {
  for (;;) { const item = await stream.iterator.next(); if (item.done) throw new Error('stream ended');
    if (item.value.type === type) return item.value }
}
const reply = (stream, event, value) => rpc(stream.peer, '$events/result', {
  clientId: stream.clientId, eventId: event.eventId, outcome: { kind: 'result', value },
})
try {
  host = await createNativeDshSessionHost({ profileRoot: root, mirrorRoot: join(root, 'media') })
  const main = host.createPeer(), child = host.createPeer()
  peers.push(main, child)
  for(const peer of [main,child]){
    const accessAbort=new AbortController();
    const access=await host.wireStream.open('remoteAccess/follow',{args:{}},
      {async *[Symbol.asyncIterator](){}},peer,accessAbort.signal);
    const iterator=access[Symbol.asyncIterator]();
    const baseline=(await iterator.next()).value;
    assert.equal(baseline.type,'baseline');assert.equal(baseline.owner,true);
    accessAbort.abort();await iterator.return?.();
    const settings=await rpc(peer,'settings/describe',{});assert.equal(settings.ok,true);
  }

  const mainStream = await events(main), childStream = await events(child)
  const created = await rpc(main, 'session/create', { request: { sessionId: 'peer-shared-session' } })
  assert.equal(created.ok, true, JSON.stringify(created))
  const agent = host.context.agents.get(created.value.sessionId)
  const ask = id => host.context.userQuestions.ask({ agent,
    questions: [{ id, question: 'offline shared question', options: [{ label: 'yes' }] }] })
  const pending = ask('both')
  const [event1, event2] = await Promise.all([nextKind(mainStream, 'waterfall'), nextKind(childStream, 'waterfall')])
  assert.equal(event1.eventId, event2.eventId)
  const answer = { answers: [{ id: 'both', selected: ['yes'] }] }
  assert.equal((await reply(childStream, event2, answer)).ok, true)
  assert.deepEqual(await pending, answer)
  assert.equal((await nextKind(mainStream, 'cancel')).eventId, event1.eventId)
  await child.dispose()
  const later = ask('main-only')
  const next = await nextKind(mainStream, 'waterfall')
  const laterAnswer = { answers: [{ id: 'main-only', selected: ['yes'] }] }
  assert.equal((await reply(mainStream, next, laterAnswer)).ok, true)
  assert.deepEqual(await later, laterAnswer)
  const output = []
  const extra = host.createPeer()
  gateway = new NativeDshMuxGateway({ handler: host.handler, wireStream: host.wireStream,
    peer: main, send: async packet => { output.push(packet) } })
  gateway.addPeer('secondary', extra)
  const dispatch = async (peerId, endpoint, args) => {
    const identity = randomUUID()
    gateway.accept({ header: { lane: 'remote_unary', payload_kind: 'dsh_fetch', correlation_id: identity },
      payload: encodeDshFetchPayload({ method: 'POST', path: `/api/${endpoint}`,
        headers: [['content-type', 'application/json']], ...(peerId ? { peer_id: peerId } : {}) },
      Buffer.from(JSON.stringify({ type: 'client-request', rpcId: identity, method: endpoint, payload: { args } }))) })
    await gateway.whenIdle()
    return output.find(packet => packet.correlationId === identity)
  }
  assert.equal((await dispatch('secondary', 'session/list', { _request: {} })).payloadKind, 'dsh_fetch_result')
  await gateway.removePeer('secondary')
  assert.equal((await dispatch('secondary', 'session/list', { _request: {} })).payloadKind, 'dsh_fetch_error')
  const remaining = await dispatch(null, 'session/list', { _request: {} })
  assert.equal(remaining.payloadKind, 'dsh_fetch_result')
  const body = JSON.parse(decodeDshFetchPayload(remaining.payload).body)
  assert.equal(body.result.ok, true)
  assert.equal(host.context.agents.get(created.value.sessionId), agent)
  console.log(JSON.stringify({ contract: 'genshin-multi-peer-qualification-v1', status: 'passed',
    publicAccessBaselineForBothPeers: true, sharedSessionIdentity: true, questionBroadcastOneSettlement: true, mainReceivesAfterChildClose: true,
    gatewayRetiresOnlySelectedPeer: true, closedPeerCannotDispatch: true, networkCalls: 0 }))
} finally {
  for (const stream of streams) stream.control.abort()
  await gateway?.close()
  await Promise.allSettled(peers.map(peer => peer.dispose()))
  await host?.dispose()
  globalThis.fetch = originalFetch
}
