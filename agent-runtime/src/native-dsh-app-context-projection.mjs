/** Retire only this plugin's stale context via DSH's public surface contract.
 * The append-only log remains complete; no dialogue/tool/compaction policy is
 * implemented here. Other owners' context sections are preserved verbatim.
 */
import { joinContextSections } from '@deepseek-ai/dsh-system-prompt'

export const APP_CONTEXT_SECTION = 'genshin:app-domain'

export function retireStaleAppContexts(session, surfaceEvents, currentText) {
  const snapshots = surfaceEvents.flatMap(event => {
    if (event.type !== 'user/message') return []
    const message = session.deriveEventMessage(event)
    const source = message?.source
    if (source?.kind !== 'runtime-context' || source.form !== 'snapshot'
        || !Array.isArray(source.sections)
        || !source.sections.some(section => section.name === APP_CONTEXT_SECTION)) return []
    return [{ event, message }]
  })
  const current = snapshots.findLast(({ message }) => message.source.sections
    .some(section => section.name === APP_CONTEXT_SECTION && section.text === currentText))
  let retired = 0
  for (const { event, message } of snapshots) {
    if (event === current?.event) continue
    const sections = message.source.sections.filter(section => section.name !== APP_CONTEXT_SECTION)
    const text = joinContextSections(sections)
      || 'Earlier App state superseded; consult the latest App context.'
    session.append('user/message', { ...message,
      content: [{ type: 'text', text }], source: { ...message.source, sections },
    }, { surfaceOp: { op: 'replace', startSeq: event.seq, endSeq: event.seq },
      sourceEventSeqs: [event.seq] })
    retired++
  }
  return { retired, retained: current === undefined ? 0 : 1 }
}

export async function projectCurrentAppContext(ctx, agent, text, signal) {
  signal?.throwIfAborted()
  const surface = await ctx.sessionQuery.readSurface(agent.id)
  signal?.throwIfAborted()
  return retireStaleAppContexts(agent.session, surface.events, text)
}
