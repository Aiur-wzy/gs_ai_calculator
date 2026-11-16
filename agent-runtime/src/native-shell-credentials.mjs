/** Public DSH credentials backed by the selected App profile, encrypted on Windows. */
import { NativeMemoryCredentials } from './memory-credentials.mjs'
import { ProtectedCredentialStore } from './native-protected-credentials.mjs'
import { DEEPSEEK_CREDENTIAL_REF } from './memory-credentials.mjs'

export class NativeShellCredentials extends NativeMemoryCredentials {
  constructor(ctx, config = {}) {
    super(ctx)
    Object.defineProperty(this, '_store', { value: config.directory
      ? new ProtectedCredentialStore(config.directory, { codec: config.codec }) : null })
    // Cordis calls through a Proxy: use non-enumerable instance storage.
    Object.defineProperty(this, '_shellRecords', { value: new Map() })
    Object.defineProperty(this, '_shellRecordTails', { value: new Map() })
    ctx.effect(() => () => this._shellRecords.clear(), 'shell: discard Connection records')
    ctx.effect(() => () => this._store?.close(), 'shell: settle protected credential writes')
  }

  async resolve(ref) {
    if (!this._store) return super.resolve(ref)
    this._nativeResolveCount += 1
    const row = await this._store.read('ref', ref)
    return row?.value ? { value: row.value, source: 'app-protected-profile' } : undefined
  }
  async describe(ref) {
    if (!this._store) return super.describe(ref)
    const configured = (await this._store.read('ref', ref))?.value != null
    return { configured, writable: true, ...(configured ? { source: 'app-protected-profile' } : {}) }
  }
  async set(ref, value) {
    if (!this._store) return super.set(ref, value)
    await this._store.modify('ref', ref, () => ({ value }))
    this.notifyUpdated(ref)
  }
  async unset(ref) {
    if (!this._store) return super.unset(ref)
    await this._store.modify('ref', ref, () => ({ value: null }))
    this.notifyUpdated(ref)
  }
  async installLegacyCredential(generation, value) {
    if (!this._store) {
      if (value === null) await super.unset(DEEPSEEK_CREDENTIAL_REF)
      else await super.set(DEEPSEEK_CREDENTIAL_REF, value)
      return { configured: value !== null }
    }
    const row = await this._store.modify('ref', DEEPSEEK_CREDENTIAL_REF, current => {
      if (current && (current.legacyGeneration ?? -1) >= generation) return undefined
      // A newly saved generic credential must never be replaced by an old
      // Windows handoff. Only the first migration or a newer explicit legacy
      // edit can update the adopted generation.
      if (current && current.legacyGeneration === null) return { ...current, legacyGeneration: generation }
      return { value, legacyGeneration: generation }
    })
    this.notifyUpdated(DEEPSEEK_CREDENTIAL_REF)
    return { configured: row?.value != null }
  }
  async releaseMemory() { this._nativeSecret = undefined }
  async settleWrites() { await Promise.allSettled([...(this._store?.pending ?? [])]) }

  async readRecord(key) {
    if (this._store) return (await this._store.read('record', key))?.value ?? undefined
    return structuredClone(this._shellRecords.get(key))
  }
  async describeRecord(key) {
    const record = await this.readRecord(key)
    return { configured: record !== undefined, writable: true,
      ...(record === undefined ? {} : { kind: record.kind }) }
  }
  listRecords() {
    if (this._store) return this._store.listRecords()
    return Promise.resolve([...this._shellRecords].map(([key, record]) => ({ key, kind: record.kind })))
  }
  async modifyRecord(key, mutate) {
    if (this._store) {
      const row = await this._store.modify('record', key, async current => {
        const next = await mutate(structuredClone(current?.value ?? undefined))
        return next === undefined ? undefined : { value: next }
      })
      this.notifyRecordUpdated(key)
      return row?.value ?? undefined
    }
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
    if (this._store) {
      await this._store.modify('record', key, () => ({ value: null }))
      this.notifyRecordUpdated(key)
      return
    }
    await this._shellRecordTails.get(key)
    this._shellRecords.delete(key)
  }
}

export default NativeShellCredentials
