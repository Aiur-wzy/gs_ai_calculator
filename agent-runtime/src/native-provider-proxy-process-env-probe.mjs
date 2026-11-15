/** Real child-process environment probe for the Windows proxy hand-off. */
import { proxyRouteFor } from '@deepseek-ai/dsh-http-proxy'

import {
  NativeProviderProxyError,
  installNativeProviderProxy,
} from './native-provider-proxy.mjs'

function assert(condition, code) {
  if (!condition) throw new Error(code)
}

const receivedNames = Object.getOwnPropertyNames(process.env)
const requiredNames = [
  'GENSHIN_NATIVE_PROVIDER_PROXY_MODE',
  'HTTP_PROXY',
  'HTTPS_PROXY',
  'NO_PROXY',
]
for (const name of requiredNames) assert(receivedNames.includes(name), 'proxy_child_name_missing')
assert(!receivedNames.includes('http_proxy'), 'proxy_child_lowercase_was_explicit')
assert(!receivedNames.includes('https_proxy'), 'proxy_child_lowercase_was_explicit')
assert(!receivedNames.includes('no_proxy'), 'proxy_child_lowercase_was_explicit')

if (process.platform === 'win32') {
  // This is the platform behavior that caused the original false rejection:
  // alias property reads succeed although no lowercase entry was received.
  assert(process.env.http_proxy === process.env.HTTP_PROXY,
    'windows_proxy_alias_lookup_not_reproduced')
  assert(process.env.https_proxy === process.env.HTTPS_PROXY,
    'windows_proxy_alias_lookup_not_reproduced')
  assert(process.env.no_proxy === process.env.NO_PROXY,
    'windows_proxy_alias_lookup_not_reproduced')
}

let installed
try {
  installed = await installNativeProviderProxy()
} catch (error) {
  if (error instanceof NativeProviderProxyError) throw new Error(error.code)
  throw error
}
assert(installed.enabled === true, 'proxy_child_not_installed')
assert(proxyRouteFor(new URL('https://api.deepseek.com/anthropic/v1/messages')).proxied,
  'proxy_child_official_route_not_proxied')
assert(!proxyRouteFor(new URL('http://127.0.0.2/internal')).proxied,
  'proxy_child_loopback_was_proxied')
await installed.dispose()
assert(!proxyRouteFor(new URL('https://api.deepseek.com/anthropic/v1/messages')).proxied,
  'proxy_child_not_disposed')

process.stdout.write(`${JSON.stringify({
  contract: 'genshin-native-provider-proxy-process-env-v1',
  status: 'passed',
  platform: process.platform,
  exactEnvironmentNames: true,
  windowsCaseInsensitiveAliasCovered: process.platform === 'win32',
  providerRequests: 0,
  networkUsed: false,
})}\n`)
