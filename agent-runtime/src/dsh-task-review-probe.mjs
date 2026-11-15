/** No provider calls: lifecycle, cancellation, advice dedup, and CLI port contract. */
import assert from 'node:assert/strict'
import { createHash } from 'node:crypto'
import { mountTaskReview, taskReviewKey, taskReviewText } from './dsh-task-review.mjs'
import { createCliTaskReviewPort } from './dsh-cli-task-review-plugin.mjs'
import { enableCliPtc, startReadonlyReview } from './dsh-task-review-experiments.mjs'
import { NativeTaskTransport } from './native-dsh-task-transport.mjs'
import { applyEntryPatches } from '@deepseek-ai/cordis-plugin-include'

const text = '按原题完成方案'
const facts = (overrides = {}) => ({ contract: 'genshin-task-evidence-v1', task_id: 'task-a',
  authority: 'advisory_only', acceptance_effect: 'none', input_fingerprint: 'input-a',
  execution_intent: 'execute', latest_task_text_fingerprint: createHash('sha256').update(JSON.stringify(text)).digest('hex'),
  source_version: 'source-a', differences: [{ id: 'r1', summary: '第二轮的回能尚未输入',
    actionable: true, recovery: { tool: 'gs_read_context', resource: 'task_evidence' } }], ...overrides })
const pluginEntry = { id: 'genshin-task-review', name: new URL('./dsh-cli-task-review-plugin.mjs', import.meta.url).href, config: {} }
const patchWarnings = []
assert.deepEqual(applyEntryPatches([], [{ insert: [pluginEntry] }], message => patchWarnings.push(message)), [pluginEntry])
assert.deepEqual(patchWarnings, [])
assert.deepEqual(applyEntryPatches([], [pluginEntry], message => patchWarnings.push(message)), [])
assert.match(patchWarnings[0], /not found/, 'a row without insert is only an existing-entry patch')
assert.equal(taskReviewKey(facts()), taskReviewKey(facts({ revision: 9, input_fingerprint: 'input-b' })))
assert.notEqual(taskReviewKey(facts()), taskReviewKey(facts({ source_version: 'source-b' })))
assert.notEqual(taskReviewKey(facts()), taskReviewKey(facts({ differences: [{ id: 'r1', actionable: true, summary: '回能已输入但前台条件需复核' }] })))
assert.equal(taskReviewKey(facts({ authority: 'approved' })), null)
assert.match(taskReviewText(facts()), /不改变计算接纳/)

async function harness() {
  const handlers = new Map(), outgoing = [], effects = []
  const scope = { on(name, callback) { handlers.set(name, callback); return () => handlers.delete(name) },
    effect(factory) { effects.push(factory()) }, systemPrompt: { context() {} } }
  const agent = { id: 'session', ctx: scope, status: 'running', inbox: { nextStep: [], nextTurn: [] },
    steer(message) { outgoing.push(message) } }
  const state = { review: facts(), activity: 0, binding: 'bound' }
  let reader = async () => structuredClone(state)
  const diagnostics = []
  const ctx = { on(name, handler) { assert.equal(name, 'agent/created'); return handler({ agent }) } }
  await mountTaskReview(ctx, { port: { read: (...args) => reader(...args) }, onDiagnostic: value => diagnostics.push(value) })
  const signal = new AbortController()
  const step = (turn, prompt = text) => handlers.get('agent/pre-step')({ turn, signal: signal.signal,
    messages: [{ source: { kind: 'user' }, content: [{ type: 'text', text: prompt }] }] }, async () => ({ kind: 'enter', messages: [] }))
  const stop = turn => handlers.get('agent/turn-stopping')({ turn, signal: signal.signal })
  return { handlers, outgoing, effects, agent, state, diagnostics, signal, step, stop, setReader(fn) { reader = fn } }
}
let h = await harness()
await h.step(1); await h.stop(1)
assert.equal(h.outgoing.length, 0, 'ordinary question must not create work')
h = await harness()
await h.step(1, '只问一下原因'); h.state.activity++; await h.stop(1)
assert.equal(h.outgoing.length, 0, 'an old execution intent must not turn a new question into work')
h = await harness(); await h.step(1)
h.state.activity++
await h.stop(1); await h.stop(1)
assert.equal(h.outgoing.length, 1, 'same facts only one steer')
assert.equal(h.outgoing[0].source.form, 'notice')
h.state.review.input_fingerprint = 'new-input'
h.state.activity++
await h.stop(1)
assert.equal(h.outgoing.length, 1, 'revision-only recompute is not progress')
h.state.review.differences[0].summary = '新输入的第一条能量事件缺少接收者'
await h.stop(1)
assert.equal(h.outgoing.length, 1, 'different issues still share a single same-turn reminder')
h.handlers.get('agent/inbox/inserted')({ message: { source: { kind: 'user' } } })
h.state.review.differences[0].summary = 'other'
await h.stop(1)
assert.equal(h.outgoing.length, 1, 'new user input wins')

for (const mode of ['cancel', 'question', 'approval', 'unbound', 'read-only', 'queued', 'disposed', 'race']) {
  h = await harness(); await h.step(1); h.state.activity++
  if (mode === 'cancel') h.signal.abort()
  if (mode === 'question') h.handlers.get('tools/result')({ name: 'ask_user_question' }, { isError: false, value: { pending: true } })
  if (mode === 'approval') h.state.waitingForUser = true
  if (mode === 'unbound') { h.state.binding = 'unbound'; h.state.review.execution_intent = null }
  if (mode === 'read-only') h.state.binding = 'read_only'
  if (mode === 'queued') h.agent.inbox.nextTurn.push({})
  if (mode === 'disposed') h.effects.forEach(dispose => dispose())
  if (mode === 'race') h.setReader(async () => {
    h.handlers.get('agent/inbox/inserted')({ message: { source: { kind: 'user' } } }); return h.state
  })
  await h.stop(1)
  assert.equal(h.outgoing.length, 0, mode)
}
h = await harness(); h.state.binding = 'unbound'
await h.step(1); await h.stop(1)
assert.equal(h.outgoing.length, 1, 'explicit execution can be reviewed before a plan exists')
for (const [name, result] of [
  ['ask_user_question', { isError: true }],
  ['ask_user_question', { isError: false, value: { answers: [] } }],
  ['gs_app_request_action', { isError: true }],
  ['gs_app_request_action', { isError: false, value: { status: 'completed' } }],
]) {
  h = await harness(); await h.step(1); h.state.activity++
  h.handlers.get('tools/result')({ name }, result)
  await h.stop(1)
  assert.equal(h.outgoing.length, 1, 'failed/settled user operation must not block advice')
}
h = await harness(); await h.step(1); h.state.activity++
let finishApproval
const inApproval = h.handlers.get('approval/request')({}, () => new Promise(resolve => { finishApproval = resolve }))
await h.stop(1); assert.equal(h.outgoing.length, 0)
finishApproval('allowed-once'); await inApproval
await h.stop(1); assert.equal(h.outgoing.length, 1, 'settled official approval is not pending')
await h.step(2)
h.state.review.source_version = 'new-user-requirements'
await h.stop(2)
assert.equal(h.outgoing.length, 2, 'new original requirements invalidate previous advice dedup')
h = await harness(); await h.step(1); h.state.activity++
h.setReader(async () => { throw new Error('auxiliary fault') })
await h.stop(1)
assert.ok(h.diagnostics.some(row => row.code === 'task_review_unavailable'))
assert.equal(h.outgoing.length, 0)

let executed
const port = createCliTaskReviewPort({ command: 'C:\\Python\\python.exe', args: ['-m', 'genshin_app'],
  plan: 'D:\\task\\plan.json', audit: 'D:\\task\\audit.json' }, async (...args) => {
  executed = args; return { stdout: JSON.stringify({ code: 0, data: facts() }) }
}, async () => {})
assert.equal((await port.read({}, new AbortController().signal)).review.task_id, 'task-a')
assert.equal(executed[2].shell, false)
assert.deepEqual(executed[1].slice(0, 4), ['-m', 'genshin_app', 'workflow', 'workspace'])
assert.equal(executed[2].windowsHide, true)
const noPlan = createCliTaskReviewPort({ command: 'C:\\Python\\python.exe', audit: 'D:\\task\\audit.json' },
  async (...args) => { executed = args; return { stdout: JSON.stringify({ code: 0, data: { task_review: facts({ binding: 'unbound' }) } }) } })
assert.equal((await noPlan.read({})).binding, 'unbound')
assert.ok(!executed[1].includes('--plan'))
assert.throws(() => createCliTaskReviewPort({ command: 'python' }), /explicit/)
assert.deepEqual(await startReadonlyReview({}, {}), { enabled: false, started: false })
let selected
const ptc = { ctx: { tools: { presentAs(mode) { selected = mode; return () => { selected = 'native' } } } } }
enableCliPtc(ptc); assert.equal(selected, undefined)
const dispose = enableCliPtc(ptc, { enabled: true }); assert.equal(selected, 'ptc'); dispose(); assert.equal(selected, 'native')
assert.throws(() => enableCliPtc(ptc, { enabled: true, nativeDomain: true }), /not_supported/)
let observed = 0, bound = false, sourceWarning = false
const attributed = new NativeTaskTransport({ sessionQuery: { async observeSession() {
  if (++observed === 1) return { header: {}, [Symbol.dispose]() {} }
  throw new Error('optional-source-failure')
} }, logger: { warn() { sourceWarning = true } } }, { async bindSession(id, _signal, source) {
  assert.equal(id, 'real-root'); assert.equal(source, undefined); bound = true; return { bound: true }
} })
await attributed.bindSession('real-root')
assert.ok(bound && sourceWarning, 'optional original-text evidence failure retains core binding')
await mountTaskReview({ on(_name, handler) { return handler({agent:{}}) } }, {
  port: { read() { throw new Error('child should not read shared CLI plan') } }, eligible: () => false,
})
console.log(JSON.stringify({ status: 'passed', checks: ['semantic-dedup', 'ordinary-question', 'cancel', 'user-race',
  'true-approval', 'question', 'scope-disposal', 'auxiliary-failure', 'CLI-port', 'optional-PTC-disabled', 'review-disabled'] }))
