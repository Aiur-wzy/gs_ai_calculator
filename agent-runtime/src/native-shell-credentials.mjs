/** Process-local Connection records; durable model secrets remain App-owned. */
import { NativeMemoryCredentials } from './memory-credentials.mjs'

export class NativeShellCredentials extends NativeMemoryCredentials {
  constructor(ctx) {
    super(ctx)
    // Cordis calls through a Proxy: use non-enumerable instance storage.
    Object.defineProperty(this, '_shellRecords', { value: new Map() })
    Object.defineProperty(this, '_shellRecordTails', { value: new Map() })
    ctx.effect(() => () => this._shellRecords.clear(), 'shell: discard Connection records')
  }

  readRecord(key) { return Promise.resolve(structuredClone(this._shellRecords.get(key))) }
  describeRecord(key) {
    const record = this._shellRecords.get(key)
    return Promise.resolve({ configured: record !== undefined, writable: true,
      ...(record === undefined ? {} : { kind: record.kind }) })
  }
  listRecords() {
    return Promise.resolve([...this._shellRecords].map(([key, record]) => ({ key, kind: record.kind })))
  }
  async modifyRecord(key, mutate) {
    const task = (this._shellRecordTails.get(key) ?? Promise.resolve()).then(async () => {
      const current = this._shellRecords.get(key)
      const next = await mutate(structuredClone(current))
      if (next !== undefined) this._shellRecords.set(key, structuredClone(next))
      return structuredClone(next ?? current)
    })
    this._shellRecordTails.set(key, task.catch(() => {}))
    return task
  }
  async deleteRecord(key) {
    await this._shellRecordTails.get(key)
    this._shellRecords.delete(key)
  }
}

export default NativeShellCredentials
