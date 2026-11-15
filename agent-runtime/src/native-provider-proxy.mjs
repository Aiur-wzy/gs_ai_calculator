/** Controlled process-wide proxy installation for the native DeepSeek kernel. */
import { installProxyFromEnvironment } from '@deepseek-ai/dsh-http-proxy'

export const NATIVE_PROVIDER_PROXY_MODE = 'GENSHIN_NATIVE_PROVIDER_PROXY_MODE'
export const NATIVE_PROVIDER_PROXY_EXPLICIT = 'explicit'
const URL_FIELDS = Object.freeze(['HTTP_PROXY', 'HTTPS_PROXY'])
const OPTIONAL_FIELDS = Object.freeze(['NO_PROXY'])
const FORBIDDEN_FIELDS = new Set(['ALL_PROXY'])

export class NativeProviderProxyError extends Error {
  constructor(code) {
    super(code)
    this.name = 'NativeProviderProxyError'
    this.code = code
  }
}

function fail(code) {
  throw new NativeProviderProxyError(code)
}

function checkedProxyUrl(value) {
  if (typeof value !== 'string' || value.length < 1 || value !== value.trim()
      || /[\u0000-\u001f\u007f]/u.test(value)) {
    fail('native_provider_proxy_url_invalid')
  }
  let parsed
  try {
    parsed = new URL(value)
  } catch {
    fail('native_provider_proxy_url_invalid')
  }
  if (!['http:', 'https:'].includes(parsed.protocol)) {
    fail('native_provider_proxy_scheme_unsupported')
  }
  if (parsed.username !== '' || parsed.password !== '') {
    fail('native_provider_proxy_credentials_forbidden')
  }
  if (parsed.hostname === '' || !['', '/'].includes(parsed.pathname)
      || parsed.search !== '' || parsed.hash !== '') {
    fail('native_provider_proxy_url_invalid')
  }
  return value
}

function checkedNoProxy(value) {
  if (value === undefined) return undefined
  if (typeof value !== 'string' || value.length > 4096
      || /[;@/\\<>\u0000-\u001f\u007f]/u.test(value)) {
    fail('native_provider_proxy_bypass_invalid')
  }
  for (const raw of value.split(',')) {
    const entry = raw.trim().toLowerCase()
    // DSH splits NO_PROXY on commas and whitespace. Reject internal
    // whitespace here so validation cannot see one harmless-looking entry
    // that DSH later interprets as multiple bypass rules.
    if (/\s/u.test(entry)) fail('native_provider_proxy_bypass_invalid')
    const normalized = entry.replace(/^\*?\./u, '')
    const hostAndPort = normalized.split(':')
    const host = (hostAndPort.length === 2 ? hostAndPort[0] : normalized)
      // DSH removes the DNS root dot before suffix matching. Mirror that
      // behavior before enforcing the official-provider boundary.
      .replace(/\.+$/u, '')
    if (host === '*' || host === 'deepseek.com' || host.endsWith('.deepseek.com')) {
      fail('native_provider_proxy_official_bypass_forbidden')
    }
  }
  return value
}

/**
 * Snapshot the environment by the spelling of its own properties.
 *
 * Windows exposes `process.env` through a case-insensitive object: when the
 * child received only `HTTP_PROXY`, reading `process.env.http_proxy` still
 * returns that same value.  Alias checks therefore must inspect the names the
 * process actually received, rather than probing properties by every spelling.
 * Plain objects remain case-sensitive, so an explicitly supplied lowercase or
 * mixed-case alias is still visible here and is rejected below.
 */
function exactEnvironment(environment) {
  let names
  try {
    names = Object.getOwnPropertyNames(environment)
  } catch {
    fail('native_provider_proxy_environment_invalid')
  }
  const exact = new Map()
  const folded = new Map()
  for (const name of names) {
    let value
    try {
      value = environment[name]
    } catch {
      fail('native_provider_proxy_environment_invalid')
    }
    exact.set(name, value)
    const canonical = name.toUpperCase()
    const aliases = folded.get(canonical) ?? []
    aliases.push(name)
    folded.set(canonical, aliases)
  }
  return Object.freeze({ exact, folded })
}

function exactContractValue(snapshot, name, { required = false } = {}) {
  const aliases = snapshot.folded.get(name) ?? []
  if (aliases.length === 0) {
    if (required) fail('native_provider_proxy_environment_incomplete')
    return undefined
  }
  if (aliases.length !== 1 || aliases[0] !== name) {
    fail('native_provider_proxy_environment_invalid')
  }
  return snapshot.exact.get(name)
}

function lookupFrom(values) {
  return Object.freeze({
    get(name) {
      const value = values[name]
      return value === undefined ? undefined : Object.freeze({ value })
    },
  })
}

/**
 * Install the App-approved policy before any provider, Search, or Fetch call.
 * The returned summary and all failures omit proxy URLs by construction.
 */
export async function installNativeProviderProxy({
  environment = process.env,
  installer = installProxyFromEnvironment,
} = {}) {
  if (environment === null || typeof environment !== 'object' || Array.isArray(environment)) {
    throw new TypeError('environment must be an object')
  }
  if (typeof installer !== 'function') throw new TypeError('installer must be a function')
  const snapshot = exactEnvironment(environment)
  const mode = exactContractValue(snapshot, NATIVE_PROVIDER_PROXY_MODE)
  if (mode === undefined) {
    return Object.freeze({ enabled: false, dispose: async () => {} })
  }
  if (mode !== NATIVE_PROVIDER_PROXY_EXPLICIT) fail('native_provider_proxy_mode_invalid')
  for (const name of FORBIDDEN_FIELDS) {
    if ((snapshot.folded.get(name) ?? []).length > 0) {
      fail('native_provider_proxy_environment_invalid')
    }
  }
  const values = {}
  for (const name of URL_FIELDS) {
    values[name] = checkedProxyUrl(exactContractValue(snapshot, name, { required: true }))
  }
  for (const name of OPTIONAL_FIELDS) {
    const bypass = checkedNoProxy(exactContractValue(snapshot, name))
    if (bypass !== undefined) values[name] = bypass
  }

  let diagnostic = false
  let dispose
  try {
    dispose = await installer(lookupFrom(values), () => { diagnostic = true })
  } catch {
    fail('native_provider_proxy_install_failed')
  }
  if (diagnostic) {
    await dispose?.().catch(() => {})
    fail('native_provider_proxy_policy_rejected')
  }
  if (typeof dispose !== 'function') fail('native_provider_proxy_install_failed')
  return Object.freeze({ enabled: true, dispose })
}
