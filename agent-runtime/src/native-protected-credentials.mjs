/** Windows protected persistence behind the public DSH credential seam. */
import { createHash } from 'node:crypto'
import { mkdir, readFile, readdir } from 'node:fs/promises'
import { join, resolve } from 'node:path'
import koffi from 'koffi'
import { withFileLock, writeFileAtomic } from '@deepseek-ai/dsh-atomic-write'
import { credentialRef, parseCredentialKey } from '@deepseek-ai/dsh-credentials'
import { parseCredentialsDocument } from '@deepseek-ai/dsh-credentials-local'

const CONTRACT = 'genshin-protected-credential-v1'
const RECORD_BYTES = 16 * 1024 * 1024
const LOCK_WAIT_MS = 15 * 60 * 1000
const fail = code => { throw new Error(code) }

/** No process command line or plaintext temporary file ever carries a secret. */
export function windowsDataProtection() {
  if (process.platform !== 'win32') fail('credential_windows_protection_unavailable')
  const blob = koffi.struct({ cbData: 'uint32_t', pbData: 'void *' })
  const crypt = koffi.load('Crypt32.dll')
  const kernel = koffi.load('Kernel32.dll')
  const protect = crypt.func('__stdcall', 'CryptProtectData', 'bool', [
    koffi.pointer(blob), 'str16', 'void *', 'void *', 'void *', 'uint32_t', koffi.out(koffi.pointer(blob)),
  ])
  const unprotect = crypt.func('__stdcall', 'CryptUnprotectData', 'bool', [
    koffi.pointer(blob), 'void *', 'void *', 'void *', 'void *', 'uint32_t', koffi.out(koffi.pointer(blob)),
  ])
  const free = kernel.func('__stdcall', 'LocalFree', 'void *', ['void *'])
  const transform = (input, encrypt) => {
    const source = Buffer.from(input)
    const output = {}
    try {
      const ok = (encrypt ? protect : unprotect)(
        { cbData: source.length, pbData: source }, null, null, null, null, 1, output,
      )
      if (!ok || !Number.isSafeInteger(output.cbData) || output.cbData > RECORD_BYTES) {
        fail('credential_windows_protection_failed')
      }
      return Buffer.from(koffi.decode(output.pbData, 'uint8_t', output.cbData))
    } catch {
      fail('credential_windows_protection_failed')
    } finally {
      source.fill(0)
      if (output.pbData) free(output.pbData)
    }
  }
  return Object.freeze({ protect: bytes => transform(bytes, true), unprotect: bytes => transform(bytes, false) })
}

function address(space, key) {
  try { return space === 'ref' ? credentialRef(key) : parseCredentialKey(key) }
  catch { fail('credential_address_invalid') }
}
function validate(space, key, value) {
  if (value === null) return
  try {
    const document = { version: 1, refs: {}, records: {} }
    document[space === 'ref' ? 'refs' : 'records'][key] = value
    parseCredentialsDocument(JSON.stringify(document), 'protected-credentials')
  } catch { fail('credential_value_invalid') }
}

/** Per-address locks also cover the upstream OAuth read/refresh/write callback. */
export class ProtectedCredentialStore {
  constructor(directory, { codec } = {}) {
    if (typeof directory !== 'string' || !directory) fail('credential_store_path_required')
    this.directory = resolve(directory)
    this.codec = codec
    this.closed = false
    this.pending = new Set()
  }
  path(space, key) {
    address(space, key)
    const digest = createHash('sha256').update(`${space}\0${key}`).digest('hex')
    return join(this.directory, `${space}-${digest}.dpapi`)
  }
  protection() { return this.codec ??= windowsDataProtection() }
  async read(space, key) {
    const path = this.path(space, key)
    let raw, plaintext
    try {
      raw = await readFile(path)
      if (!raw.length || raw.length > RECORD_BYTES) fail('credential_store_invalid')
      plaintext = await this.protection().unprotect(raw)
      const row = JSON.parse(Buffer.from(plaintext).toString('utf8'))
      if (row.contract !== CONTRACT || row.space !== space || row.key !== key
          || !Object.hasOwn(row, 'value')
          || (row.legacyGeneration !== null && (!Number.isSafeInteger(row.legacyGeneration) || row.legacyGeneration < 0))) {
        fail('credential_store_invalid')
      }
      validate(space, key, row.value)
      return row
    } catch (error) {
      if (error.code === 'ENOENT') return undefined
      fail('credential_store_read_failed')
    } finally { plaintext?.fill?.(0) }
  }
  async modify(space, key, mutate) {
    if (this.closed) fail('credential_store_closed')
    const path = this.path(space, key)
    const operation = (async () => {
      await mkdir(this.directory, { recursive: true, mode: 0o700 })
      return withFileLock(path, async () => {
        const current = await this.read(space, key)
        const next = await mutate(current)
        if (next === undefined) return current
        validate(space, key, next.value)
        let bytes
        try {
          bytes = Buffer.from(JSON.stringify({ contract: CONTRACT, space, key,
            value: next.value, legacyGeneration: next.legacyGeneration ?? current?.legacyGeneration ?? null }))
          if (bytes.length > RECORD_BYTES / 2) fail('credential_record_too_large')
          const protectedBytes = await this.protection().protect(bytes)
          await writeFileAtomic(path, protectedBytes, { mode: 0o600, dirMode: 0o700 })
        } catch { fail('credential_store_write_failed') }
        finally { bytes?.fill(0) }
        return next
      }, { waitMs: LOCK_WAIT_MS })
    })()
    this.pending.add(operation)
    try { return await operation }
    finally { this.pending.delete(operation) }
  }
  async listRecords() {
    let entries
    try { entries = await readdir(this.directory, { withFileTypes: true }) }
    catch (error) { if (error.code === 'ENOENT') return []; fail('credential_store_read_failed') }
    const values = []
    for (const entry of entries) {
      if (!entry.isFile() || !/^record-[a-f0-9]{64}\.dpapi$/u.test(entry.name)) continue
      let plaintext
      try {
        const raw = await readFile(join(this.directory, entry.name))
        if (!raw.length || raw.length > RECORD_BYTES) fail('credential_store_invalid')
        plaintext = await this.protection().unprotect(raw)
        const row = JSON.parse(Buffer.from(plaintext).toString('utf8'))
        if (row.contract !== CONTRACT || row.space !== 'record'
            || this.path('record', row.key) !== join(this.directory, entry.name)) fail('credential_store_invalid')
        validate('record', row.key, row.value)
        if (row.value !== null) values.push({ key: row.key, kind: row.value.kind })
      } catch { fail('credential_store_read_failed') }
      finally { plaintext?.fill?.(0) }
    }
    return values
  }
  async close() {
    this.closed = true
    await Promise.allSettled([...this.pending])
  }
}
