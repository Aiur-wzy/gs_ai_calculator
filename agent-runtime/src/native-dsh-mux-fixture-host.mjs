/** Offline DSH public composition used by transport qualification only. */
import { Context } from '@deepseek-ai/cordis'
import * as ConnectionPlugin from '@deepseek-ai/dsh-client-connection'
import { TypertGatewayService } from '@deepseek-ai/dsh-api-gateway'
import { RemoteAccessService } from '@deepseek-ai/dsh-api-remote-access'
import * as RemotesPlugin from '@deepseek-ai/dsh-api-remotes'
import { Remote, TypertRemoteService } from '@deepseek-ai/dsh-typert-protocol'
import { TypertRegistry } from '@deepseek-ai/dsh-typert-registry'
import { NativeMemoryCredentials } from './memory-credentials.mjs'

const initializers = []
class FixtureCredentials extends NativeMemoryCredentials {
  records = new Map()
  readRecord(key) { return Promise.resolve(this.records.get(key)) }
  describeRecord(key) {
    const value = this.records.get(key)
    return Promise.resolve({ configured: value !== undefined, writable: true,
      ...(value === undefined ? {} : { kind: value.kind }) })
  }
  listRecords() {
    return Promise.resolve([...this.records].map(([key, value]) => ({ key, kind: value.kind })))
  }
  async modifyRecord(key, mutate) {
    const current = this.records.get(key)
    const next = await mutate(current)
    if (next !== undefined) this.records.set(key, next)
    return next ?? current
  }
  deleteRecord(key) { this.records.delete(key); return Promise.resolve() }
}
class FixtureRemote extends TypertRemoteService {
  metrics = { countItems: 0, uplinkItems: 0, cancelled: 0 }

  constructor(ctx) {
    super(ctx, 'muxFixtureRemote', { namespace: 'probe' })
    for (const initialize of initializers) initialize.call(this)
  }

  echo(value) { return { echoed: value } }

  async *count(limit, signal) {
    for (let index = 0; index < limit; index += 1) {
      signal.throwIfAborted()
      this.metrics.countItems += 1
      yield { index }
    }
  }

  async *duplex(signal) {
    const invocation = this.ctx.invocation
    for await (const value of invocation.uplink()) {
      signal.throwIfAborted()
      this.metrics.uplinkItems += 1
      yield { echoed: value }
    }
  }

  async *failAfter() {
    yield { beforeFailure: true }
    throw new Error('fixture-stream-failure')
  }

  async hang(signal) {
    await new Promise((resolve, reject) => {
      if (signal.aborted) { reject(signal.reason); return }
      signal.addEventListener('abort', () => {
        this.metrics.cancelled += 1
        reject(signal.reason)
      }, { once: true })
    })
    return null
  }
}
for (const [name, stream] of [['echo', false], ['count', true], ['duplex', true],
  ['failAfter', true], ['hang', false]]) {
  const decorate = stream ? Remote({ mode: 'stream' }) : Remote
  decorate(FixtureRemote.prototype[name], {
    kind: 'method', name, static: false, private: false,
    addInitializer(initialize) { initializers.push(initialize) },
  })
}

export async function createDshMuxFixtureHost({ owner = true } = {}) {
  const ctx = new Context()
  try {
    await ctx.plugin(FixtureCredentials)
    await ctx.plugin(ConnectionPlugin)
    await ctx.plugin(TypertRegistry)
    await ctx.plugin(RemoteAccessService)
    await ctx.plugin(TypertGatewayService)
    await ctx.plugin(RemotesPlugin)
    await ctx.plugin(FixtureRemote)
    const peer = ctx.connection.createPeer({
      principal: owner ? { kind: 'owner' } : { kind: 'qualification-outsider', id: 'outsider' },
      interactions: owner ? 'answer' : 'none',
    })
    return {
      ctx, peer,
      handler: ctx.connection.createSharedFetchHandler('/api'),
      wireStream: ctx.typertGateway.wireStream,
      metrics: ctx.muxFixtureRemote.metrics,
      dispose: () => ctx.fiber.dispose(),
    }
  } catch (error) {
    await ctx.fiber.dispose()
    throw error
  }
}
