/** Actual DSH shell runtime over one authenticated private mux. */
import { randomUUID } from 'node:crypto'
import { join } from 'node:path'
import { createNativeDshSessionHost } from './native-dsh-session-host.mjs'
import { NativeDshMuxGateway } from './native-dsh-mux-gateway.mjs'
import { NativeDshDomainTransport } from './native-dsh-domain-transport.mjs'
import { NATIVE_DSH_RUNTIME_CONTRACT, NATIVE_DSH_RUNTIME_CONTROL_CONTRACT } from './native-dsh-runtime-config.mjs'
import { drainNativeJobs } from './native-dsh-job-drain.mjs'

const CONTROLS = new Set(['dsh_secret_set', 'dsh_secret_clear', 'dsh_runtime_status', 'dsh_runtime_session_status', 'dsh_runtime_client_create', 'dsh_runtime_client_close', 'dsh_runtime_drain', 'dsh_runtime_shutdown'])
const record = value => value !== null && typeof value === 'object' && !Array.isArray(value)
const decode = bytes => JSON.parse(new TextDecoder('utf8', { fatal: true }).decode(bytes))
const LOCAL_STARTUP_CODES = new Set(['fixed_root_filesystem_root_invalid', 'fixed_root_filesystem_qualification_hook_invalid'])
export const nativeDshRuntimeErrorCode = error => typeof error?.code === 'string'
  && /^[A-Za-z0-9_./:-]{1,160}$/u.test(error.code) ? error.code
  : LOCAL_STARTUP_CODES.has(error?.message) ? 'native_dsh_' + error.message
  : typeof error?.message === 'string' && /^native_dsh_[a-z0-9_]{1,140}$/u.test(error.message)
    ? error.message : 'native_dsh_runtime_failed'

export async function runNativeDshRuntime({ carrier, config, retryAdmission } = {}) {
  if (typeof carrier?.readNext !== 'function' || typeof carrier?.enqueue !== 'function') {
    throw new TypeError('native_dsh_runtime_carrier_required')
  }
  let host, gateway
  let tail = Promise.resolve()
  let controls = Promise.resolve()
  const controlTasks = new Set()
  let draining = false
  let drained = false
  let stopping = false
  let drainPromise
  const send = packet => {
    carrier.enqueue(packet)
    tail = tail.then(() => carrier.writeNext())
    return tail
  }
  const domainTransport = new NativeDshDomainTransport({ send })
  const reply = (header, payloadKind, value) => send({ lane: 'control', payloadKind,
    payload: Buffer.from(JSON.stringify(value), 'utf8'), messageId: randomUUID(),
    correlationId: header.correlation_id, streamId: null, streamSeq: null, trustedClass: 'control' })
  try {
    host = await createNativeDshSessionHost({ ...config, domainTransport, retryAdmission, accounting: true })
    // Process shutdown is a resource admission boundary, not a replacement
    // Agent/Turn state machine. Existing requests are cancelled through DSH.
    host.context.on('agent/created', () => {
      if (draining) throw new Error('native_dsh_runtime_draining')
    }, { prepend: true })
    host.context.on('agent/pre-step', (_payload, next) => draining ? { kind: 'reject' } : next(), { prepend: true })
    host.context.on('llm/stream', async function* (_options, next) {
      if (draining) throw new Error('native_dsh_runtime_draining')
      yield* next()
    }, { prepend: true })
    gateway = new NativeDshMuxGateway({ handler: host.handler, wireStream: host.wireStream,
      spoolRoot: join(config.profileRoot, 'logical-spool'),
      assets: host.assets, domainTransport, peer: host.createPeer(), send,
      onFailure() { carrier.close() } })
    const drain = () => {
      if (drainPromise !== undefined) return drainPromise
      draining = true
      drainPromise = (async () => {
        await gateway.pauseAdmissions()
        const agents = host.context.agents.list()
        for (const agent of agents) {
          host.context.goals.disarm(agent)
          agent.cancel({ kind: 'parent' }, { keepInbox: true })
        }
        // The read pump remains live while provider-finally and research
        // callbacks obtain Host settlement acknowledgements.
        const jobs = await drainNativeJobs(host.context, agents)
        await Promise.allSettled(agents.map(agent => agent.whenIdle()))
        const research = await host.accounts.retireResearch()
        // OAuth refresh owns its official read/refresh/write callback. A
        // returned rotated token must be persisted before maintenance ends.
        await host.context.credentials.settleWrites?.()
        await host.kernel.flushSessions()
        drained = true
        return { state: 'drained', agents: agents.length, jobs, research,
          provider_admission: host.admission.snapshot(),
          domain_settlement: 'host_barrier_required' }
      })()
      return drainPromise
    }
    while (true) {
      let packet
      try { packet = await carrier.readNext() }
      catch (error) { if (stopping) break; throw error }
      const { header, payload } = packet
      if (header.lane === 'control' && CONTROLS.has(header.payload_kind)) {
        if (typeof header.correlation_id !== 'string') throw new Error('native_dsh_runtime_control_identity_required')
        const operation = header.payload_kind
        // Buffer bytes can be dropped immediately after decoding; neither key
        // nor credential payload is ever appended to a Session or a log.
        const value = decode(payload)
        const task = controls.then(async () => {
          try {
            let result
            if (operation === 'dsh_secret_set') result = await host.kernel.secretController.set(value)
            else if (operation === 'dsh_secret_clear') result = await host.kernel.secretController.clear(value)
            else if (operation === 'dsh_runtime_client_create') {
              if (draining || !record(value) || Object.keys(value).length !== 2
                  || !/^[A-Za-z0-9_-]{1,128}$/u.test(value.peer_id ?? '')
                  || !['answer', 'none'].includes(value.interactions)) throw new Error('native_dsh_peer_payload_invalid')
              const peer = host.createPeer(value.interactions)
              try { gateway.addPeer(value.peer_id, peer) } catch (error) { await peer.dispose(); throw error }
              result = { peer_id: value.peer_id, interactions: value.interactions, state: 'ready' }
            }
            else if (operation === 'dsh_runtime_client_close') {
              if (!record(value) || Object.keys(value).length !== 1 || typeof value.peer_id !== 'string') {
                throw new Error('native_dsh_peer_payload_invalid')
              }
              result = { peer_id: value.peer_id, closed: await gateway.removePeer(value.peer_id) }
            }
            else if (operation === 'dsh_runtime_session_status') {
              if (!record(value) || Object.keys(value).length !== 1 || !Object.hasOwn(value, 'session_id')) {
                throw new Error('native_dsh_session_status_payload_invalid')
              }
              result = await host.sessionStatus(value.session_id)
            }
            else {
              if (!record(value) || Object.keys(value).length !== 0) throw new Error('runtime_control_empty_payload_required')
              if (operation === 'dsh_runtime_drain') result = await drain()
              else if (operation === 'dsh_runtime_shutdown') {
                if (!drained) throw new Error('native_dsh_runtime_drain_required')
                result = { state: 'stopping' }
              } else result = {
                contract: NATIVE_DSH_RUNTIME_CONTRACT,
                composition_identity: host.compositionIdentity,
                credentials: host.kernel.secretController.status,
                provider_admission: host.admission.snapshot(),
                lifecycle: drained ? 'drained' : draining ? 'draining' : 'ready',
                agents: host.context.agents.list().map(agent => ({session_id:agent.id,
                  status:agent.status, subagent:agent.session.header.origin === 'subagent'})),
              }
            }
            await reply(header, 'dsh_runtime_control_result', {
              contract: NATIVE_DSH_RUNTIME_CONTROL_CONTRACT, operation, result,
            })
            if (operation === 'dsh_runtime_shutdown') { stopping = true; carrier.close() }
            return operation === 'dsh_runtime_shutdown'
          } catch (error) {
            const code = nativeDshRuntimeErrorCode(error)
            await reply(header, 'dsh_runtime_control_error', { code, message: code, details: {} })
            return false
          }
        })
        controls = task.catch(() => {})
        controlTasks.add(task)
        task.catch(() => carrier.close()).finally(() => controlTasks.delete(task))
        carrier.settleRead('accepted')
        continue
      }
      gateway.accept(packet)
      carrier.settleRead('accepted')
    }
  } finally {
    domainTransport.close()
    await gateway?.close()
    // Closing the trusted credential seam precedes disposal of its owner.
    await host?.kernel.secretController.dispose()
    await host?.dispose()
    carrier.close()
    await Promise.allSettled([...controlTasks])
  }
}
