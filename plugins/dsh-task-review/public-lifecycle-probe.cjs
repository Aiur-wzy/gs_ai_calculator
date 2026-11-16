/** Public DSH runtime compatibility check. Local adapter only; never calls a model API. */
const assert = require('node:assert/strict')
const { createHash } = require('node:crypto')
const { createRequire } = require('node:module')
const { resolve, join } = require('node:path')
const { pathToFileURL } = require('node:url')
const { copyFile, mkdir, mkdtemp } = require('node:fs/promises')

async function main() {
  const packageBase = process.argv[2]
  assert.ok(packageBase, 'Pass the installed DSH package.json path (Electron ASAR is supported).')
  const fromDsh = createRequire(resolve(packageBase))
  const dshVersion = fromDsh('@deepseek-ai/dsh-agent/package.json').version
  const { Context } = fromDsh('@deepseek-ai/cordis')
  const { createUserMessage } = fromDsh('@deepseek-ai/dsh-llm')
  const { mountTaskReview } = await import(pathToFileURL(resolve(__dirname, 'dsh-task-review.mjs')))
  const ctx = new Context()
  const oldFetch = globalThis.fetch
  globalThis.fetch = () => { throw new Error('Network access is forbidden in this compatibility probe.') }
  let calls = 0, state = 0, blocked, release, cancelMode = false
  const requests = []
  const errors = []
  const snapshot = () => ({ binding: 'bound', activity: state, review: {
    contract: 'genshin-task-evidence-v1', authority: 'advisory_only', acceptance_effect: 'none',
    task_id: 'local-task', source_version: 'original-request', input_fingerprint: `input-${state}`,
    execution_intent: 'execute', latest_task_text_fingerprint: createHash('sha256')
      .update(JSON.stringify('Work on the local fixture.')).digest('hex'),
    differences: [{ id: 'energy', summary: '第二轮的回能尚未输入', actionable: true }],
  } })
  try {
    const modules = ['dsh-session', 'dsh-session-projection', 'dsh-system-prompt', 'dsh-tools',
      ...(dshVersion.startsWith('0.2.2-') ? ['dsh-tool-executor-native'] : []),
      'dsh-agent', 'dsh-llm', 'dsh-agent-loop']
    for (const name of modules) {
      const module = fromDsh(`@deepseek-ai/${name}`)
      await ctx.plugin(module.default ?? module)
    }
    ctx.on('agent/error', ({ error }) => errors.push(String(error)))
    ctx.llm.registerAdapter(['offline-lifecycle'], {
      providerInfo: id => ({ id, name: 'Offline lifecycle fixture' }),
      providerRetryPolicy: () => undefined,
      async prepareCall(provider, model) {
        return { model: { provider, id: model, name: model }, async *stream(options) {
          calls++
          requests.push(options)
          if (cancelMode) {
            state++
            blocked()
            await new Promise(resolve => {
              release = resolve
              if (options.signal.aborted) resolve()
              else options.signal.addEventListener('abort', resolve, { once: true })
            })
            options.signal.throwIfAborted()
          }
          if (calls === 1) state++
          yield { type: 'text-delta', index: 0, text: 'Local fixture response.' }
          yield { type: 'finish', reason: { kind: 'stop' } }
        } }
      },
    })
    mountTaskReview(ctx, { port: { read: async () => snapshot() }, context: true,
      eligible: agent => ctx.agents.get(agent.id) === agent && agent.session.header.origin !== 'subagent' })
    const handle = await ctx.agents.create({ sessionId: 'compat-lifecycle',
      agentOptions: { provider: 'offline-lifecycle', model: 'fixture' } })
    const { agent } = handle
    agent.followup(createUserMessage({ content: [{ type: 'text', text: 'Work on the local fixture.' }], source: { kind: 'user' } }))
    await agent.whenIdle()
    assert.deepEqual(errors, [])
    assert.equal(calls, 2, 'a changed fact should produce exactly one same-turn continuation')
    let events = agent.session.snapshotEvents(0)
    assert.equal(events.filter(e => e.type === 'turn/start').length, 1)
    assert.equal(events.filter(e => e.type === 'turn/end').length, 1)
    assert.ok(requests[1].messages.some(row => row.source?.kind === 'genshin-task-review'))
    assert.ok(requests[0].messages.some(row => JSON.stringify(row).includes('任务对照提醒')))

    // Unchanged facts do not reopen the completed task when the next message is just a question.
    agent.followup(createUserMessage({ content: [{ type: 'text', text: 'What happened?' }], source: { kind: 'user' } }))
    await agent.whenIdle()
    assert.equal(calls, 3)

    // The real driver's user cancellation wins; the plugin never reopens a cancelled turn.
    cancelMode = true
    const entered = new Promise(resolve => { blocked = resolve })
    agent.followup(createUserMessage({ content: [{ type: 'text', text: 'Start then cancel.' }], source: { kind: 'user' } }))
    await entered
    agent.cancel({ kind: 'user' })
    await agent.whenIdle()
    assert.equal(calls, 4)
    assert.equal(agent.status, 'idle')
    events = agent.session.snapshotEvents(0)
    assert.equal(events.filter(e => e.type === 'turn/start').length, 3)
    assert.equal(events.filter(e => e.type === 'turn/end').length, 3)
    assert.equal(agent.inbox.nextStep.length + agent.inbox.nextTurn.length, 0)
    await handle.dispose()
    assert.equal(ctx.agents.get(agent.id), undefined)
    // Import a copied package through the same public Loader used by the
    // product. The offline command fixture is never dispatched in this check.
    const { Loader } = fromDsh('@deepseek-ai/cordis-plugin-loader')
    await ctx.plugin(Loader, { baseUrl: pathToFileURL(resolve(packageBase)).href })
    const temporaryRoot = resolve(__dirname, '../../build/r18-next-development')
    await mkdir(temporaryRoot, { recursive: true })
    const standalone = await mkdtemp(join(temporaryRoot, 'dsh-task-review-isolated-'))
    for (const name of ['package.json', 'index.mjs', 'dsh-task-review.mjs', 'dsh-cli-task-review-plugin.mjs', 'compatibility.json']) {
      await copyFile(resolve(__dirname, name), join(standalone, name))
    }
    const entryId = await ctx.loader.create({ name: pathToFileURL(join(standalone, 'index.mjs')).href,
      config: { command: process.execPath, audit: join(standalone, 'unused.json') } })
    await ctx.loader.await()
    const entry = ctx.loader.resolve(entryId)
    assert.equal(typeof entry.moduleNamespace?.apply, 'function', 'Loader imported the isolated module')
    assert.equal(entry.fiber?.state, 2, 'Loader actually mounted the plugin (Cordis FiberState.ACTIVE)')
    ctx.loader.remove(entryId)
    console.log(JSON.stringify({ status: 'passed', dsh: dshVersion, provider: 'offline-adapter',
      lifecycle: ['created', 'context', 'pre-step', 'turn-stopping', 'same-turn-steer', 'unchanged-dedup', 'cancel', 'dispose'],
      isolatedLoader: true, providerCalls: calls, networkCalls: 0 }))
  } finally {
    release?.()
    await ctx.fiber.dispose()
    globalThis.fetch = oldFetch
  }
}
main().catch(error => { console.error(error.stack); process.exitCode = 1 })
