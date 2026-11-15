/** Public surface replacement spike: source evidence, replay and other owners. */
import assert from 'node:assert/strict'
import { Session } from '@deepseek-ai/dsh-session'
import { createUserMessage } from '@deepseek-ai/dsh-llm'
import { joinContextSections } from '@deepseek-ai/dsh-system-prompt'
import { APP_CONTEXT_SECTION, retireStaleAppContexts } from './native-dsh-app-context-projection.mjs'

const session = Session.create('app-context-public-projection')
const log = []
const append = session.append.bind(session)
session.append = (...args) => { const event = append(...args); log.push(event); return event }
const addSnapshot = (text, extra = true) => {
  const sections = [...(extra ? [{ name: 'dsh:workspace', text: 'Owner facts must remain exact.' }] : []),
    { name: APP_CONTEXT_SECTION, text }]
  return session.append('user/message', createUserMessage({
    content: [{ type: 'text', text: joinContextSections(sections) }],
    source: { kind: 'runtime-context', form: 'snapshot', sections },
  }), { surfaceOp: 'append' })
}
const surface = () => session.surface.nodes.map(seq => log[seq])
const user = session.append('user/message', createUserMessage({ source: { kind: 'human' }, content: [{ type: 'text', text: 'Original user instruction' }] }), { surfaceOp: 'append' })
const old = addSnapshot('revision 0')
addSnapshot('revision 1', false)
const fresh = addSnapshot('revision 2')
const raw = JSON.stringify(log)
assert.deepEqual(retireStaleAppContexts(session, surface(), 'revision 2'), { retired: 2, retained: 1 })
assert.equal(JSON.stringify(log.slice(0, 4)), raw)
assert.deepEqual(session.deriveEventMessage(user), user.data)
assert.ok(session.surface.nodes.includes(fresh.seq))
assert.equal(session.surface.nodes.includes(old.seq), false)
const messages = session.deriveMessages()
assert.equal(messages.filter(message => message.source?.sections?.some(s => s.name === APP_CONTEXT_SECTION)).length, 1)
assert.equal(messages.filter(message => message.source?.sections?.some(s => s.name === 'dsh:workspace')).length, 2)
assert.deepEqual(retireStaleAppContexts(session, surface(), 'revision 2'), { retired: 0, retained: 1 })
// Cold replay requires no plugin schema or private interpreter.
const cold = Session.create(session.id, log, session.header)
assert.deepEqual(cold.deriveMessages(), session.deriveMessages())
assert.deepEqual(retireStaleAppContexts(session, surface(), 'revision 3'), { retired: 1, retained: 0 })
assert.equal(session.deriveMessages().some(message => message.source?.sections?.some(s => s.name === APP_CONTEXT_SECTION)), false)
// Simulate DSH compaction using its same public surface contract. Old App
// snapshots outside the resulting surface must never be resurrected.
const nodes = [...session.surface.nodes]
session.append('user/message', createUserMessage({ source: { kind: 'compaction' }, content: [{ type: 'text', text: 'DSH compacted history' }] }),
  { surfaceOp: { op: 'replace', startSeq: nodes[0], endSeq: nodes.at(-1) }, sourceEventSeqs: nodes })
assert.deepEqual(retireStaleAppContexts(session, surface(), 'revision 3'), { retired: 0, retained: 0 })
assert.deepEqual(Session.create(session.id, log, session.header).deriveMessages(), session.deriveMessages())
console.log(JSON.stringify({ publicSurfaceReplacement: true, onlyAppSectionRetired: true,
  rawEvidencePreserved: true, coldReplay: true, compactionDoesNotResurrectState: true }))
