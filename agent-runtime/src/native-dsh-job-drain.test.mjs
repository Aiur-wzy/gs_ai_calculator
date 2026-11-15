import assert from 'node:assert/strict'
import test from 'node:test'
import { Context } from '@deepseek-ai/cordis'
import JobRegistry from '@deepseek-ai/dsh-jobs-local'
import { drainNativeJobs } from './native-dsh-job-drain.mjs'

test('a fulfilled live wait projection is an observation timeout, not shutdown completion', async () => {
  let observed = 0, kills = 0
  const job = { id: 'fake-job', status: 'running', owner: 'session-a' }
  const jobs = {
    list(owner) { return owner === 'session-a' ? [job] : [] },
    kill(id, owner) { assert.equal(id, job.id); assert.equal(owner, job.owner); kills++ },
    async wait() { observed++; return { ...job, status: observed < 3 ? 'stopping' : 'killed' } },
  }
  assert.deepEqual(await drainNativeJobs({ get: () => jobs }, [{ id: 'session-a' }]), { requested: 1, terminal: 1 })
  assert.equal(kills, 1)
  assert.equal(observed, 3)
})

test('actual DSH job cancellation waits for compliant producer settlement', async () => {
  const ctx = new Context()
  try {
    await ctx.plugin(JobRegistry)
    const detach = ctx.jobs.attachController('native-drain-engineering-test')
    let cancelRequested = false, settle
    const outcome = new Promise(resolve => { settle = resolve })
    const id = ctx.jobs.start({ kind: 'shell', label: 'offline synthetic producer',
      run() { return { done: outcome, cancel() { cancelRequested = true; setTimeout(() => settle({ status: 'killed' }), 5) } } } })
    const report = await drainNativeJobs(ctx, [])
    assert.equal(cancelRequested, true)
    assert.equal(ctx.jobs.get(id).status, 'killed')
    assert.equal(report.terminal, 1)
    detach()
  } finally { await ctx.fiber.dispose() }
})
