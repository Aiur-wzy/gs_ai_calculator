/** Offline adversarial probe for the App-approved DSH proxy hand-off. */
import { proxyRouteFor } from '@deepseek-ai/dsh-http-proxy'

import {
  NATIVE_PROVIDER_PROXY_EXPLICIT,
  NATIVE_PROVIDER_PROXY_MODE,
  NativeProviderProxyError,
  installNativeProviderProxy,
} from './native-provider-proxy.mjs'

function assert(condition, code) {
  if (!condition) throw new Error(code)
}

async function rejected(environment, code) {
  try {
    await installNativeProviderProxy({ environment })
  } catch (error) {
    assert(error instanceof NativeProviderProxyError && error.code === code, `${code}_unstable`)
    assert(!String(error).includes('secret') && !String(error).includes('127.0.0.1:9876'),
      `${code}_leaked`)
    return
  }
  throw new Error(`${code}_accepted`)
}

const direct = await installNativeProviderProxy({
  environment: { HTTPS_PROXY: 'http://ambient.invalid:1' },
  installer: async () => { throw new Error('ambient_proxy_inherited') },
})
assert(direct.enabled === false, 'ambient_proxy_enabled')
await direct.dispose()

const environment = {
  [NATIVE_PROVIDER_PROXY_MODE]: NATIVE_PROVIDER_PROXY_EXPLICIT,
  HTTP_PROXY: 'http://127.0.0.1:9876',
  HTTPS_PROXY: 'http://127.0.0.1:9876',
  NO_PROXY: 'localhost',
}
const installed = await installNativeProviderProxy({ environment })
assert(installed.enabled === true, 'explicit_proxy_not_installed')
const route = proxyRouteFor(new URL('https://api.deepseek.com/anthropic/v1/messages'))
assert(route.proxied === true, 'official_endpoint_not_proxied')
assert(route.proxy === environment.HTTPS_PROXY, 'proxy_route_changed')
assert(proxyRouteFor(new URL('http://127.0.0.2/internal')).proxied === false,
  'loopback_was_proxied')
await installed.dispose()
assert(proxyRouteFor(new URL('https://api.deepseek.com/anthropic/v1/messages')).proxied === false,
  'proxy_not_disposed')

await rejected({
  ...environment,
  HTTPS_PROXY: 'http://user:secret@127.0.0.1:9876',
}, 'native_provider_proxy_credentials_forbidden')
await rejected({
  ...environment,
  HTTPS_PROXY: 'socks5://127.0.0.1:9876',
}, 'native_provider_proxy_scheme_unsupported')
await rejected({
  ...environment,
  HTTPS_PROXY: 'http://127.0.0.1:9876/proxy.pac',
}, 'native_provider_proxy_url_invalid')
await rejected({
  ...environment,
  all_proxy: 'http://127.0.0.1:9876',
}, 'native_provider_proxy_environment_invalid')
await rejected({
  ...environment,
  Http_Proxy: environment.HTTP_PROXY,
}, 'native_provider_proxy_environment_invalid')
await rejected({
  ...environment,
  http_proxy: environment.HTTP_PROXY,
}, 'native_provider_proxy_environment_invalid')
await rejected({
  ...environment,
  No_Proxy: environment.NO_PROXY,
}, 'native_provider_proxy_environment_invalid')
await rejected({
  ...environment,
  NO_PROXY: '*.deepseek.com',
}, 'native_provider_proxy_official_bypass_forbidden')
await rejected({
  ...environment,
  NO_PROXY: 'api.deepseek.com.',
}, 'native_provider_proxy_official_bypass_forbidden')
await rejected({
  ...environment,
  NO_PROXY: '*.deepseek.com.',
}, 'native_provider_proxy_official_bypass_forbidden')
await rejected({
  ...environment,
  NO_PROXY: 'api.deepseek.com.:443',
}, 'native_provider_proxy_official_bypass_forbidden')
await rejected({
  ...environment,
  NO_PROXY: 'localhost api.deepseek.com',
}, 'native_provider_proxy_bypass_invalid')

let rejectedDiagnosticDisposed = false
try {
  await installNativeProviderProxy({
    environment,
    installer: async (_lookup, report) => {
      report('HTTP_PROXY rejected: http://user:secret@127.0.0.1:9876')
      return async () => { rejectedDiagnosticDisposed = true }
    },
  })
} catch (error) {
  assert(error.code === 'native_provider_proxy_policy_rejected', 'diagnostic_code_changed')
  assert(!String(error).includes('user:secret'), 'diagnostic_leaked')
}
assert(rejectedDiagnosticDisposed, 'rejected_policy_not_disposed')

try {
  await installNativeProviderProxy({
    environment,
    installer: async () => { throw new Error('http://user:secret@127.0.0.1:9876') },
  })
} catch (error) {
  assert(error.code === 'native_provider_proxy_install_failed', 'install_code_changed')
  assert(!String(error).includes('user:secret'), 'install_failure_leaked')
}

process.stdout.write(`${JSON.stringify({
  contract: 'genshin-native-provider-proxy-offline-v1',
  status: 'passed',
  actualDshInstaller: true,
  globalFetchDispatcherCovered: true,
  providerRequests: 0,
  networkUsed: false,
})}\n`)
