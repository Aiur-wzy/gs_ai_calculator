/**
 * Process-local credential provider for the Genshin native Agent sidecar.
 *
 * The App owns durable storage in Windows Credential Manager.  This provider
 * deliberately holds only the one currently injected API key and never reads
 * argv, process.env or a file.  Disposing the Cordis fiber, clearing the key,
 * or replacing it releases the previous immutable string reference as far as
 * JavaScript permits; V8 does not offer guaranteed string zeroisation.
 */
import { CredentialProvider, credentialRef } from '@deepseek-ai/dsh-credentials'

export const DEEPSEEK_CREDENTIAL_REF = credentialRef('DEEPSEEK_API_KEY')
export const MAX_CREDENTIAL_CHARS = 512

function assertSupportedRef(ref) {
  if (ref !== DEEPSEEK_CREDENTIAL_REF) {
    throw new Error('credential_reference_not_allowed')
  }
}

function validateSecret(value) {
  if (typeof value !== 'string'
      || value.length === 0
      || value.length > MAX_CREDENTIAL_CHARS
      || /[\s\u0000-\u001f\u007f]/u.test(value)) {
    throw new Error('credential_value_invalid')
  }
  return value
}

/** Complete CredentialProvider surface with exactly one writable reference. */
export class NativeMemoryCredentials extends CredentialProvider {
  constructor(ctx) {
    super(ctx)
    // Cordis services are exposed through a Proxy, so ECMAScript private
    // fields cannot be used by methods invoked through ctx.credentials.
    Object.defineProperty(this, '_nativeSecret', {
      value: undefined, writable: true, configurable: false, enumerable: false,
    })
    Object.defineProperty(this, '_nativeResolveCount', {
      value: 0, writable: true, configurable: false, enumerable: false,
    })
    ctx.effect(() => () => {
      this._nativeSecret = undefined
    }, 'native-agent: clear in-memory credential')
  }

  get resolveCount() {
    return this._nativeResolveCount
  }

  resolve(ref) {
    this._nativeResolveCount += 1
    if (ref !== DEEPSEEK_CREDENTIAL_REF || this._nativeSecret === undefined) {
      return Promise.resolve(undefined)
    }
    return Promise.resolve({ value: this._nativeSecret, source: 'app-memory' })
  }

  describe(ref) {
    const supported = ref === DEEPSEEK_CREDENTIAL_REF
    const configured = supported && this._nativeSecret !== undefined
    return Promise.resolve({
      configured,
      ...(configured ? { source: 'app-memory' } : {}),
      writable: supported,
    })
  }

  set(ref, value) {
    assertSupportedRef(ref)
    const next = validateSecret(value)
    const changed = this._nativeSecret !== next
    this._nativeSecret = next
    if (changed) this.notifyUpdated(ref)
    return Promise.resolve()
  }

  unset(ref) {
    assertSupportedRef(ref)
    const changed = this._nativeSecret !== undefined
    // Best effort: strings are immutable, so dropping the final project-owned
    // reference is the strongest zeroisation guarantee JavaScript can provide.
    this._nativeSecret = undefined
    if (changed) this.notifyUpdated(ref)
    return Promise.resolve()
  }

  readRecord(_key) {
    return Promise.resolve(undefined)
  }

  describeRecord(_key) {
    return Promise.resolve({ configured: false, writable: false })
  }

  listRecords() {
    return Promise.resolve([])
  }

  modifyRecord(_key, _mutate) {
    return Promise.reject(new Error('credential_records_not_supported'))
  }

  deleteRecord(_key) {
    return Promise.reject(new Error('credential_records_not_supported'))
  }

}

export default NativeMemoryCredentials
