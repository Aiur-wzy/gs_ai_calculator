/** Thin task attribution over DSH's durable parentSession metadata. */
import { createHash } from 'node:crypto'
import { appendPluginRecord } from '@deepseek-ai/dsh-session'

const digest = value => createHash('sha256').update(value).digest('hex')
const callIdentity = body => JSON.stringify([body.session_id, body.turn ?? null,
  body.step ?? null, body.call_seq ?? null, body.call_id])

export class NativeTaskTransport {
  constructor(ctx, transport) {
    this.ctx = ctx
    this.transport = transport
    this.leases = new Map()
    this.research = new Map()
  }

  async owner(actor) {
    let id = actor
    const seen = new Set()
    while (true) {
      if (seen.has(id)) throw new Error('native_dsh_task_lineage_cycle')
      seen.add(id)
      const observation = await this.ctx.sessionQuery.observeSession(id, { projectionMode: 'none' })
      let header
      try { header = observation.header }
      finally { observation[Symbol.dispose]() }
      if (header.origin !== 'subagent' || typeof header.parentSession !== 'string') return id
      id = header.parentSession
    }
  }

  async attribution(body) {
    const owner = await this.owner(body.session_id)
    if (owner === body.session_id) return { body, actor: body.session_id, owner }
    const observation = await this.ctx.sessionQuery.observeSession(owner, { projectionMode: 'none' })
    let turn, step
    try {
      const boundary = observation.events.findLast(row => row.type === 'turn/start' || row.type === 'turn/end')
      turn = boundary?.data.turn
      step = observation.events.findLast(row => row.data?.turn === turn
        && (row.type === 'step/start' || row.type === 'step/end'))?.data.step
    } finally { observation[Symbol.dispose]() }
    if (!Number.isSafeInteger(turn) || !Number.isSafeInteger(step)) throw new Error('native_dsh_task_parent_scope_unavailable')
    const mapped = { ...body, session_id: owner,
      ...(Object.hasOwn(body, 'turn') ? { turn } : {}),
      ...(Object.hasOwn(body, 'step') ? { step } : {}),
      ...(Object.hasOwn(body, 'call_id') ? { call_id: 'child-' + digest(callIdentity(body)) } : {}),
    }
    const session = this.ctx.sessions.get(body.session_id)
    if (session !== undefined && (body.call_id !== undefined || body.lease_id !== undefined)) {
      appendPluginRecord(session, 'plugin:genshin/task-attribution', {
        actor_session_id: body.session_id, owner_session_id: owner,
        actor_turn: body.turn ?? null, actor_step: body.step ?? null,
        owner_turn: turn, owner_step: step,
        actor_call_id: body.call_id ?? null, owner_call_id: mapped.call_id ?? null,
        lease_id: body.lease_id ?? null,
      })
    }
    return { body: mapped, actor: body.session_id, owner }
  }

  async bindSession(actor, signal) {
    const owner = await this.owner(actor)
    let taskSource, observation
    try {
      observation = await this.ctx.sessionQuery.observeSession(owner, { signal, projectionMode: 'none' })
      const event = observation.events.findLast(row => row.type === 'user/message'
        && row.data?.source?.kind === 'user')
      if (event) {
        const text = event.data.content.filter(block => block.type === 'text').map(block => block.text).join('\n')
        if (text) taskSource = { session_id: owner, event_seq: event.seq, message_id: event.data.id, text }
      }
    } catch {
      // Ownership lookup above remains authoritative. This second read only
      // supplies auxiliary original-text evidence and cannot revoke tools.
      signal?.throwIfAborted()
      this.ctx.logger?.warn?.('App task source evidence unavailable; core binding retained')
    } finally { observation?.[Symbol.dispose]() }
    signal?.throwIfAborted()
    const response = await this.transport.bindSession(owner, signal, taskSource)
    return { ...response, session_id: actor }
  }

  async execute(body, signal) {
    const mapped = await this.attribution(body)
    const response = await this.transport.execute(mapped.body, signal)
    return { ...response, session_id: body.session_id, call_id: body.call_id, call_seq: body.call_seq }
  }

  async budgetAcquire(body, signal) {
    const mapped = await this.attribution(body)
    const result = await this.transport.budgetAcquire(mapped.body, signal)
    if (result.result?.allowed) this.leases.set(body.lease_id, mapped.body)
    return result
  }

  async budgetSettle(body) {
    const frozen = this.leases.get(body.lease_id)
    const request = frozen === undefined ? body : { ...body,
      session_id: frozen.session_id, turn: frozen.turn, step: frozen.step }
    const response = await this.transport.budgetSettle(request)
    this.leases.delete(body.lease_id)
    return response
  }

  async admitTool(body, signal) {
    const mapped = await this.attribution(body)
    return this.transport.admitTool(mapped.body, signal)
  }

  async researchRegister(body, signal) {
    const mapped = await this.attribution(body)
    const response = await this.transport.researchRegister(mapped.body, signal)
    if (response.result?.status === 'registered') this.research.set(callIdentity(body), mapped.body)
    return response
  }

  async researchSettle(body) {
    const key = callIdentity(body)
    const frozen = this.research.get(key)
    const request = frozen === undefined ? body : { ...body,
      session_id: frozen.session_id, turn: frozen.turn, step: frozen.step, call_id: frozen.call_id }
    const response = await this.transport.researchSettle(request)
    this.research.delete(key)
    return response
  }
}
