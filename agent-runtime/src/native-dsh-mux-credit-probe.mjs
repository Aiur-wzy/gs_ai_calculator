/** Offline transport invariants; real carrier/write completion, never a model. */
import assert from 'node:assert/strict'
import { randomUUID } from 'node:crypto'
import { once } from 'node:events'
import { connect, createServer } from 'node:net'
import { join } from 'node:path'
import { tmpdir } from 'node:os'
import { NativeMuxPipeClient } from './native-mux-pipe-client.mjs'
import { NativeDshMuxGateway } from './native-dsh-mux-gateway.mjs'
import { assertPinnedNodeVersion } from './runtime-version.mjs'

assertPinnedNodeVersion()
const tick = () => new Promise(resolve => setImmediate(resolve))
const gate = () => {
  let resolve, reject
  const promise = new Promise((yes, no) => { resolve = yes; reject = no })
  return { promise, resolve, reject }
}
async function until(predicate) {
  const deadline = Date.now() + 10_000
  while (!predicate()) {
    assert.ok(Date.now() < deadline, 'offline credit probe did not progress')
    await tick()
  }
}
const encode = value => Buffer.from(JSON.stringify(value))

function fixture({ next, send } = {}) {
  const output = [], errors = [], sequences = new Map(), pulls = new Map()
  let active = 0, maximumActive = 0, disposed = 0
  const gateway = new NativeDshMuxGateway({
    handler: { fetch: async () => new Response('unused') },
    peer: { dispose: async () => { disposed += 1 } },
    wireStream: {
      open: async id => ({
        [Symbol.asyncIterator]() { return this },
        async next() {
          pulls.set(id, (pulls.get(id) ?? 0) + 1)
          active += 1
          maximumActive = Math.max(maximumActive, active)
          try { return await (next?.(id, pulls.get(id)) ?? { value: pulls.get(id), done: false }) }
          finally { active -= 1 }
        },
        return: async () => ({ done: true }),
      }),
      failure: error => ({ code: error.code ?? 'engineering_failure', message: 'offline test failure', details: {} }),
    },
    send: packet => { output.push(packet); return send?.(packet) ?? Promise.resolve() },
    onFailure: error => errors.push(error),
  })
  function accept(kind, value, id = 'a') {
    const control = kind === 'dsh_stream_cancel'
    gateway.accept({ header: { lane: control ? 'control' : 'remote_stream', payload_kind: kind,
      correlation_id: id, stream_id: control ? null : id,
      stream_seq: control ? null : (sequences.set(id, (sequences.get(id) ?? 0) + 1), sequences.get(id)) },
    payload: encode(value) })
  }
  const open = async id => {
    accept('dsh_stream_open', { endpoint: id, payload: {}, has_uplink: false }, id)
    await until(() => output.some(packet => packet.streamId === id && packet.payloadKind === 'dsh_stream_opened'))
  }
  const credit = (id = 'a', items = 1) => accept('dsh_stream_credit', { items }, id)
  const items = id => output.filter(packet => packet.streamId === id && packet.payloadKind === 'dsh_stream_item')
  return { gateway, output, errors, pulls, open, credit, accept, items,
    get maximumActive() { return maximumActive }, get disposed() { return disposed } }
}

async function readingDuplicate() {
  const pending = gate()
  const f = fixture({ next: () => pending.promise })
  try {
    await f.open('a'); f.credit(); await until(() => f.pulls.get('a') === 1)
    assert.throws(() => f.credit(), /dsh_mux_stream_credit_invalid/u)
    assert.equal(f.pulls.get('a'), 1)
    f.accept('dsh_stream_cancel', { stream_id: 'a' })
    pending.resolve({ done: false, value: 'cancelled read' })
    await f.gateway.whenIdle()
    assert.equal(f.items('a').length, 0)
    assert.equal(f.gateway.snapshot().activeStreams, 0)
  } finally { pending.resolve({ done: true }); await f.gateway.close() }
}

async function sendingCase(mode) {
  const pending = gate()
  const f = fixture({
    next: (_id, n) => mode === 'end' && n === 2 ? { done: true }
      : mode === 'next_failure' && n === 2 ? Promise.reject(new Error('controlled next failure'))
        : { done: false, value: n },
    send: packet => packet.payloadKind === 'dsh_stream_item' && packet.streamId === 'a'
      && JSON.parse(packet.payload).value === 1 ? pending.promise : undefined,
  })
  try {
    await f.open('a'); f.credit(); await until(() => f.items('a').length === 1)
    f.credit() // One legal lookahead while the previous local send is pending.
    assert.equal(f.pulls.get('a'), 1)
    assert.throws(() => f.credit(), /dsh_mux_stream_credit_invalid/u)
    assert.throws(() => f.credit('a', 2), /dsh_mux_stream_credit_invalid/u)
    assert.equal(f.pulls.get('a'), 1)
    if (mode === 'cancel') f.accept('dsh_stream_cancel', { stream_id: 'a' })
    if (mode === 'uplink_end') f.accept('dsh_stream_end', {})
    if (mode === 'close') await f.gateway.close()
    if (mode === 'pause') await f.gateway.pauseAdmissions()
    if (mode === 'cross_stream') {
      await f.open('b'); f.credit('b')
      await until(() => f.items('b').length === 1)
      assert.equal(f.pulls.get('a'), 1)
      assert.equal(f.pulls.get('b'), 1)
    }
    if (mode === 'send_failure') pending.reject(new Error('controlled send failure'))
    else pending.resolve()
    await f.gateway.whenIdle()
    if (['cancel', 'close', 'pause', 'send_failure'].includes(mode)) {
      assert.equal(f.pulls.get('a'), 1)
      assert.equal(f.gateway.snapshot().activeStreams, 0)
    } else {
      assert.equal(f.pulls.get('a'), 2)
      assert.equal(f.maximumActive, 1)
      if (mode === 'end' || mode === 'next_failure') {
        const terminal = mode === 'end' ? 'dsh_stream_end' : 'dsh_stream_error'
        assert.equal(f.output.filter(p => p.payloadKind === terminal).length, 1)
        f.credit() // A retired stream's late credit is harmless.
        assert.equal(f.pulls.get('a'), 2)
      } else assert.equal(f.items('a').length, 2)
    }
    for (const id of mode === 'cross_stream' ? ['a', 'b'] : ['a']) {
      const stream = f.output.filter(p => p.streamId === id)
      assert.deepEqual(stream.map(p => p.streamSeq), stream.map((_p, index) => index + 1))
    }
    assert.deepEqual(f.errors, [])
  } finally { pending.resolve(); await f.gateway.close() }
}

async function synchronousReentry() {
  let f, queued = false
  f = fixture({ send: packet => {
    if (packet.payloadKind === 'dsh_stream_item' && !queued) { queued = true; f.credit() }
  } })
  try {
    await f.open('a'); f.credit(); await f.gateway.whenIdle()
    assert.equal(f.items('a').length, 2)
    assert.equal(f.pulls.get('a'), 2)
    assert.equal(f.maximumActive, 1)
    assert.deepEqual(f.errors, [])
  } finally { await f.gateway.close() }
}

async function terminalSendFailure() {
  const pending = gate()
  const f = fixture({ send: packet => {
    if (packet.payloadKind === 'dsh_stream_item') return pending.promise
    if (packet.payloadKind === 'dsh_stream_error') throw new Error('carrier already failed')
  } })
  try {
    await f.open('a'); f.credit(); await until(() => f.items('a').length === 1)
    f.credit(); pending.reject(new Error('carrier send failed'))
    await f.gateway.whenIdle(); await until(() => f.disposed === 1)
    assert.equal(f.pulls.get('a'), 1)
    assert.equal(f.errors.length, 1)
    assert.equal(f.gateway.snapshot().closed, true)
    assert.equal(f.gateway.snapshot().activeStreams, 0)
  } finally { pending.resolve(); await f.gateway.close() }
}

async function reentrantSendFailure() {
  let f
  f = fixture({ send: packet => {
    if (packet.payloadKind === 'dsh_stream_item') {
      f.credit()
      throw new Error('synchronous send failure after reentrant credit')
    }
  } })
  try {
    await f.open('a'); f.credit(); await f.gateway.whenIdle()
    assert.equal(f.pulls.get('a'), 1)
    assert.equal(f.output.filter(p => p.payloadKind === 'dsh_stream_error').length, 1)
    assert.equal(f.gateway.snapshot().activeStreams, 0)
    assert.deepEqual(f.errors, [])
  } finally { await f.gateway.close() }
}

async function realPipe() {
  const name = process.platform === 'win32' ? `\\\\.\\pipe\\r9-credit-${randomUUID()}`
    : join(tmpdir(), `r9-credit-${randomUUID()}.sock`)
  const server = createServer()
  server.listen(name); await once(server, 'listening')
  const accepted = once(server, 'connection'), hostSocket = connect(name)
  await once(hostSocket, 'connect')
  const [socket] = await accepted
  const identity = { connectionGeneration: 1, connectionId: 'credit-green', runtimeIncarnation: 'credit-green-runtime' }
  let hold = false, completeWrite = null, closing = false, failure = null, pulls = 0
  const write = socket.write.bind(socket)
  socket.write = (bytes, callback) => {
    if (!hold) return write(bytes, callback)
    hold = false
    return write(bytes, error => { completeWrite = () => callback(error) })
  }
  const host = new NativeMuxPipeClient(hostSocket, { senderRole: 'host', peerSenderRole: 'runtime', ...identity })
  const runtime = new NativeMuxPipeClient(socket, { senderRole: 'runtime', peerSenderRole: 'host', ...identity })
  let tail = Promise.resolve()
  const gateway = new NativeDshMuxGateway({ handler: { fetch: async () => new Response('unused') },
    peer: { dispose: async () => {} },
    wireStream: { open: async () => ({ [Symbol.asyncIterator]() { return this },
      next: async () => ++pulls <= 2 ? { value: pulls, done: false } : { done: true },
      return: async () => ({ done: true }) }),
    failure: error => ({ code: error.code ?? 'engineering_failure', message: 'offline', details: {} }) },
    send: packet => { if (packet.payloadKind === 'dsh_stream_item' && pulls === 1) hold = true
      runtime.enqueue(packet); tail = tail.then(() => runtime.writeNext()); return tail },
    onFailure: error => { failure = error },
  })
  const pump = (async () => { try { while (!closing) { const packet = await runtime.readNext()
    gateway.accept(packet); runtime.settleRead('accepted') } } catch (error) { if (!closing) failure = error } })()
  let sequence = 0
  async function send(kind, value, control = false) {
    host.enqueue({ lane: control ? 'control' : 'remote_stream', payloadKind: kind, payload: encode(value), messageId: randomUUID(),
      correlationId: control ? 'unrelated-control' : 'a', streamId: control ? null : 'a',
      streamSeq: control ? null : ++sequence, trustedClass: control ? 'control' : 'normal' })
    await host.writeNext()
  }
  async function read(kind) { const p = await host.readNext({ idleTimeoutMs: 10_000 })
    host.settleRead('accepted'); assert.equal(p.header.payload_kind, kind); return JSON.parse(p.payload) }
  try {
    await send('dsh_stream_open', { endpoint: 'fixed', payload: {}, has_uplink: false }); await read('dsh_stream_opened')
    await send('dsh_stream_credit', { items: 1 }); assert.equal((await read('dsh_stream_item')).value, 1)
    await until(() => completeWrite !== null)
    await send('dsh_stream_credit', { items: 1 })
    // Observe actual inbound credit admission at the carrier while the local
    // output callback is held, without assuming send is a remote ACK.
    await until(() => runtime.diagnosticSnapshot().counters.acceptedFrames >= 3 || failure !== null)
    assert.equal(failure, null); assert.equal(pulls, 1)
    await send('dsh_fetch_cancel', { correlation_id: 'unused-fetch' }, true)
    await until(() => runtime.diagnosticSnapshot().counters.acceptedFrames >= 4 || failure !== null)
    completeWrite()
    await read('dsh_fetch_cancelled')
    assert.equal((await read('dsh_stream_item')).value, 2)
    await send('dsh_stream_credit', { items: 1 }); await read('dsh_stream_end')
    assert.equal(pulls, 3); assert.equal(failure, null)
    return { real_named_pipe: true, real_carrier_writeNext: true, visible_before_callback: true,
      control_priority_interleaving: true, next_calls: pulls }
  } finally {
    completeWrite?.(); closing = true; await gateway.close(); host.close(); runtime.close()
    await pump; await new Promise(resolve => server.close(resolve))
  }
}

const cases = {
  reading_duplicate: readingDuplicate,
  pending_duplicate: () => sendingCase('pending'),
  cancel: () => sendingCase('cancel'), close: () => sendingCase('close'), pause: () => sendingCase('pause'),
  send_failure: () => sendingCase('send_failure'), next_failure: () => sendingCase('next_failure'),
  end: () => sendingCase('end'), cross_stream: () => sendingCase('cross_stream'),
  uplink_end: () => sendingCase('uplink_end'),
  synchronous_reentry: synchronousReentry, terminal_send_failure: terminalSendFailure,
  reentrant_send_failure: reentrantSendFailure, real_pipe: realPipe,
}
const selected = process.argv[2]
assert.ok(Object.hasOwn(cases, selected), 'explicit known credit probe case required')
const evidence = await cases[selected]()
console.log(JSON.stringify({ contract: 'genshin-native-dsh-mux-credit-probe-v1', status: 'passed', case: selected,
  single_pending_window: true, no_concurrent_next: true, provider_used: false, ...evidence }))
