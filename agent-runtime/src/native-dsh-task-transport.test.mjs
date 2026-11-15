import assert from 'node:assert/strict'
import test from 'node:test'
import { NativeTaskTransport } from './native-dsh-task-transport.mjs'

function world() {
  const current = { turn: 1, step: 2 }
  const headers = { child: { origin: 'subagent', parentSession: 'root' }, root: {}, independent: { parentSession: 'root' } }
  const ctx = { sessions: { get() { return undefined } }, sessionQuery: {
    async observeSession(id) { return { header: headers[id], events: [
      { type: 'turn/start', data: { turn: current.turn } },
      { type: 'step/start', data: { turn: current.turn, step: current.step } },
    ], [Symbol.dispose]() {} } },
  } }
  return { ctx, current, headers }
}

test('provider call IDs may repeat in later child turns without colliding in the App ledger', async () => {
  const { ctx } = world()
  const calls = []
  const transport = new NativeTaskTransport(ctx, { async execute(body) { calls.push(body); return body } })
  const first = { session_id: 'child', turn: 1, step: 1, call_seq: 8, call_id: 'provider-reused-id' }
  await transport.execute(first)
  await transport.execute({ ...first, turn: 2, call_seq: 80 })
  await transport.execute(first)
  assert.notEqual(calls[0].call_id, calls[1].call_id)
  assert.equal(calls[0].call_id, calls[2].call_id)
  assert.ok(calls.every(row => row.session_id === 'root'))
})

test('provider and research settlements keep their acquired parent scope after parent advances', async () => {
  const { ctx, current } = world()
  const settlements = [], research = []
  const transport = new NativeTaskTransport(ctx, {
    async budgetAcquire(body) { return { result: { allowed: true } } },
    async budgetSettle(body) { settlements.push(body); return {} },
    async researchRegister(body) { return { result: { status: 'registered' } } },
    async researchSettle(body) { research.push(body); return {} },
  })
  const paid = { session_id: 'child', turn: 3, step: 4, lease_id: 'unique-lease' }
  const call = { session_id: 'child', turn: 3, step: 4, call_id: 'one', call_seq: 9 }
  await transport.budgetAcquire(paid)
  await transport.researchRegister(call)
  current.turn = 10
  current.step = 20
  await transport.budgetSettle(paid)
  await transport.researchSettle(call)
  assert.equal(settlements[0].session_id, 'root')
  assert.equal(settlements[0].turn, 1)
  assert.equal(settlements[0].step, 2)
  assert.equal(research[0].turn, 1)
  assert.equal(research[0].step, 2)
  assert.match(research[0].call_id, /^child-/u)
  assert.equal(transport.leases.size, 0)
  assert.equal(transport.research.size, 0)
})

test('only official subagent origin follows lineage and cyclic durable headers fail closed', async () => {
  const { ctx, headers } = world()
  const transport = new NativeTaskTransport(ctx, {})
  assert.equal(await transport.owner('independent'), 'independent')
  headers.root = { origin: 'subagent', parentSession: 'child' }
  await assert.rejects(transport.owner('child'), /native_dsh_task_lineage_cycle/u)
})
