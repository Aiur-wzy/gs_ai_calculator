/**
 * Ephemeral App-to-runtime credential generation gate.
 *
 * The App owns durable credentials and sends one authenticated secret control
 * frame after the private pipe handshake.  This module accepts only the fixed
 * DeepSeek provider, never serializes or reports a secret, and rejects stale
 * generations before touching the DSH credential service.
 */
import { DEEPSEEK_CREDENTIAL_REF, MAX_CREDENTIAL_CHARS } from './memory-credentials.mjs'

export const DEEPSEEK_PROVIDER_ID = 'deepseek-official'
export const MAX_CREDENTIAL_GENERATION = Number.MAX_SAFE_INTEGER

const SET_KEYS = new Set(['provider', 'credential_generation', 'secret'])
const CLEAR_KEYS = new Set(['provider', 'credential_generation'])

export class NativeSecretControlError extends Error {
  constructor(code) {
    super(code)
    this.name = 'NativeSecretControlError'
    this.code = code
  }
}

function fail(code) {
  throw new NativeSecretControlError(code)
}

function exactObject(value, expected, code) {
  if (value === null || typeof value !== 'object' || Array.isArray(value)) fail(code)
  const keys = Reflect.ownKeys(value)
  if (keys.some(key => typeof key !== 'string')
      || keys.length !== expected.size
      || keys.some(key => !expected.has(key))) fail(code)
}

function generation(value) {
  if (!Number.isSafeInteger(value) || value < 0
      || value > MAX_CREDENTIAL_GENERATION) fail('secret_generation_invalid')
  return value
}

function provider(value) {
  if (value !== DEEPSEEK_PROVIDER_ID) fail('secret_provider_unsupported')
  return value
}

function secret(value) {
  if (typeof value !== 'string'
      || value.length === 0
      || value.length > MAX_CREDENTIAL_CHARS
      || /[\s\u0000-\u001f\u007f]/u.test(value)) {
    fail('secret_value_invalid')
  }
  return value
}

function credentialService(value) {
  if (value === null || typeof value !== 'object'
      || typeof value.resolve !== 'function'
      || typeof value.set !== 'function'
      || typeof value.unset !== 'function') {
    fail('credential_service_unavailable')
  }
  return value
}

export class NativeSecretController {
  #credentials
  #generation
  #configured = false
  #disposed = false

  constructor(credentials) {
    this.#credentials = credentialService(credentials)
  }

  get status() {
    return Object.freeze({
      provider: DEEPSEEK_PROVIDER_ID,
      credential_generation: this.#generation ?? null,
      configured: this.#configured,
      disposed: this.#disposed,
    })
  }

  #admit(nextGeneration) {
    if (this.#disposed) fail('secret_controller_disposed')
    if (this.#generation !== undefined && nextGeneration < this.#generation) {
      fail('secret_generation_stale')
    }
  }

  async set(payload) {
    exactObject(payload, SET_KEYS, 'secret_set_payload_shape')
    provider(payload.provider)
    const nextGeneration = generation(payload.credential_generation)
    const nextSecret = secret(payload.secret)
    this.#admit(nextGeneration)
    if (typeof this.#credentials.installLegacyCredential === 'function') {
      const result = await this.#credentials.installLegacyCredential(nextGeneration, nextSecret)
      this.#generation = nextGeneration
      this.#configured = result.configured
      return Object.freeze({ disposition: 'synchronized', ...this.status })
    }
    if (nextGeneration === this.#generation) {
      const current = await this.#credentials.resolve(DEEPSEEK_CREDENTIAL_REF)
      if (!this.#configured || current?.value !== nextSecret) {
        fail('secret_generation_conflict')
      }
      return Object.freeze({ disposition: 'replay', ...this.status })
    }
    try {
      await this.#credentials.set(DEEPSEEK_CREDENTIAL_REF, nextSecret)
    } catch {
      fail('secret_install_failed')
    }
    this.#generation = nextGeneration
    this.#configured = true
    return Object.freeze({ disposition: 'installed', ...this.status })
  }

  async clear(payload) {
    exactObject(payload, CLEAR_KEYS, 'secret_clear_payload_shape')
    provider(payload.provider)
    const nextGeneration = generation(payload.credential_generation)
    this.#admit(nextGeneration)
    if (typeof this.#credentials.installLegacyCredential === 'function') {
      const result = await this.#credentials.installLegacyCredential(nextGeneration, null)
      this.#generation = nextGeneration
      this.#configured = result.configured
      return Object.freeze({ disposition: 'synchronized', ...this.status })
    }
    if (nextGeneration === this.#generation) {
      const current = await this.#credentials.resolve(DEEPSEEK_CREDENTIAL_REF)
      if (this.#configured || current !== undefined) fail('secret_generation_conflict')
      return Object.freeze({ disposition: 'replay', ...this.status })
    }
    try {
      await this.#credentials.unset(DEEPSEEK_CREDENTIAL_REF)
    } catch {
      fail('secret_clear_failed')
    }
    this.#generation = nextGeneration
    this.#configured = false
    return Object.freeze({ disposition: 'cleared', ...this.status })
  }

  async dispose() {
    if (this.#disposed) return
    this.#disposed = true
    this.#configured = false
    try {
      if (typeof this.#credentials.releaseMemory === 'function') await this.#credentials.releaseMemory()
      else await this.#credentials.unset(DEEPSEEK_CREDENTIAL_REF)
    } catch {
      // Runtime teardown is already fail-closed.  Do not surface provider or
      // secret-adjacent error text while dropping the final project reference.
    }
  }
}
