/** Offline adversarial proof for the App's official DSH Web composition. */
import { createHash } from 'node:crypto'
import { SessionId } from '@deepseek-ai/dsh-session'

import { createNativeDeepSeekKernel } from './native-deepseek-kernel.mjs'
import { createProductionCompositionProbeFixture } from './production-composition-probe-fixture.mjs'
import {
  DEEPSEEK_WEB_API_BASE_URL,
  NativeWebResearchLifecycle,
  NATIVE_WEB_TOOLS,
  setupNativeWebAgentScope,
} from './native-dsh-web-stack.mjs'

const SECRET = 'offline-web-secret-sentinel'
const SECRET_IN_ERROR = 'offline-callback-secret-sentinel'
const ENV_SECRET = 'offline-env-secret-must-not-be-used'
const originalFetch = globalThis.fetch
const originalSetTimeout = globalThis.setTimeout
const originalEnvironmentSecret = process.env.DEEPSEEK_API_KEY
const originalSearchProvider = process.env.DSH_WEB_SEARCH_PROVIDER
const originalFetchProvider = process.env.DSH_WEB_FETCH_PROVIDER
let kernel
let dshProfile

function assert(condition, code) {
  if (!condition) throw new Error(code)
}

function canonical(value) {
  if (value === null || typeof value !== 'object') return JSON.stringify(value)
  if (Array.isArray(value)) return `[${value.map(canonical).join(',')}]`
  return `{${Object.keys(value).sort().map(key => (
    `${JSON.stringify(key)}:${canonical(value[key])}`
  )).join(',')}}`
}

function sha(value) {
  return createHash('sha256').update(canonical(value), 'utf8').digest('hex')
}

function rawSha(value) {
  return createHash('sha256').update(value, 'utf8').digest('hex')
}

function probeToolBinding(event) {
  const argumentsJson = JSON.stringify(event.arguments)
  return Object.freeze({
    call_id: event.execution_call_id,
    tool: event.tool,
    arguments_json: argumentsJson,
    raw_arguments_hash: rawSha(argumentsJson),
  })
}

function callbacks({ preRegister, onResult } = {}) {
  let next = 0
  const terminals = []
  const fatals = []
  return {
    value: {
      async preRegister(event) {
        if (preRegister !== undefined) return preRegister(event)
        next += 1
        return {
          status: 'registered',
          research_id: `research_${String(next).padStart(32, 'a')}`,
          research_ledger_revision: next,
          arguments_hash: sha(event.arguments),
          raw_arguments_hash: event.raw_arguments_hash,
        }
      },
      async onResult(event) {
        terminals.push(event)
        return onResult?.(event)
      },
      onFatal(event) {
        fatals.push(event)
      },
    },
    terminals,
    fatals,
  }
}

let sessionIndex = 0
let callIndex = 0
async function webAgent(researchCallbacks) {
  sessionIndex += 1
  let lifecycle
  // Qualification uses the public registry directly; production Session
  // ownership remains exclusively in the official SessionController.
  const handle = await kernel.context.agents.create({
    sessionId: SessionId(`web-probe-${sessionIndex}`),
    agentOptions: { provider: 'deepseek-official', model: 'deepseek-flash', reasoningEffort: 'high' },
    async setup(agentCtx, agent) {
      lifecycle = await setupNativeWebAgentScope({ agentCtx, agent,
        callbacks: researchCallbacks, resolveToolBinding: probeToolBinding,
        budgetController: { admitWebTool() {} } })
    },
  })
  assert(NATIVE_WEB_TOOLS.every(name => kernel.context.tools.schemas(handle.agent).some(tool => tool.name === name)), 'probe_web_tools_missing')
  return { agent: handle.agent, dispose: () => handle.dispose(),
    waitForResearchCallbacks: () => lifecycle.whenSettled(),
    webResearchDiagnostics: () => lifecycle.diagnosticSnapshot() }
}

async function execute(handle, name, arguments_, { signal } = {}) {
  callIndex += 1
  const executionSignal = signal ?? new AbortController().signal
  const toolSet = await kernel.context.tools.resolve({agent:handle.agent,
    modelSelection:handle.agent.model.current(),signal:executionSignal})
  try {
    const batch = await kernel.context.tools.execute({
      calls: [{ type: 'tool-call', id: `call-${sessionIndex}-${callIndex}-${name}`,
        name, arguments: JSON.stringify(arguments_) }],
      agent: handle.agent, signal:executionSignal, toolSet,
    })
    return batch.results[0].result
  } finally {toolSet.dispose()}
}

function searchResponse() {
  return new Response(JSON.stringify({
    results: [{
      url: 'https://example.test/source',
      title: 'Fixture',
      snippet: 'Offline source.',
      date: '2026-10-23',
    }],
  }), { status: 200, headers: { 'content-type': 'application/json' } })
}

try {
  process.env.DEEPSEEK_API_KEY = ENV_SECRET
  process.env.DSH_WEB_SEARCH_PROVIDER = 'untrusted-search-provider'
  process.env.DSH_WEB_FETCH_PROVIDER = 'deepseek-fetch-api'
  // A registered outbound operation may never be treated as a quiet success.
  // Cancellation/transport integration must close this App-side row as
  // outcome_unknown before it finalizes the turn.
  {
    const handlers = new Map()
    const lifecycle = new NativeWebResearchLifecycle({
      agentCtx: {
        on(name, handler) {
          handlers.set(name, handler)
        },
      },
      agent: { cancel() {} },
      callbacks: callbacks().value,
      resolveToolBinding: probeToolBinding,
      budgetController: { admitWebTool() {} },
    })
    await handlers.get('tools/pre-execute')({
      token: 'orphan-token',
      callId: 'orphan-call',
      name: 'web_search',
      arguments: { queries: ['orphan'] },
    }, () => undefined)
    let code
    try {
      await lifecycle.whenSettled()
    } catch (error) {
      code = error.code
    }
    assert(code === 'research_terminal_missing'
      && lifecycle.diagnosticSnapshot().pendingRegistrations === 1,
    'orphan_registration_silently_succeeded')
  }

  dshProfile = await createProductionCompositionProbeFixture('native-web-profile')
  kernel = await createNativeDeepSeekKernel({
    personaPrefix: 'Genshin native Agent Web offline fixture.',
    ...dshProfile.paths,
  })
  assert(kernel.context.web.searchProviders.has('deepseek-search-api')
    && kernel.context.web.fetchProviders.has('http'), 'web_provider_catalog_missing_official_defaults')

  // No credential: pre-registration occurs, but the provider never dispatches.
  let outbound = 0
  globalThis.fetch = async () => {
    outbound += 1
    throw new Error('unexpected_network')
  }
  {
    const research = callbacks()
    const handle = await webAgent(research.value)
    try {
      const schemas = kernel.context.tools.schemas(handle.agent)
      assert(NATIVE_WEB_TOOLS.every(name => schemas.some(item => item.name === name)), 'web_catalog_missing')
      const result = await execute(handle, 'web_search', { queries: ['no key'] })
      await handle.waitForResearchCallbacks()
      assert(result.isError && outbound === 0, 'missing_key_dispatched')
      assert(research.terminals.length===1&&research.terminals[0].status==='failed', 'missing_key_not_settled')
    } finally {
      await handle.dispose()
    }
  }
  if (originalEnvironmentSecret === undefined) {
    Reflect.deleteProperty(process.env, 'DEEPSEEK_API_KEY')
  } else {
    process.env.DEEPSEEK_API_KEY = originalEnvironmentSecret
  }

  await kernel.secretController.set({
    provider: 'deepseek-official',
    credential_generation: 1,
    secret: SECRET,
  })

  // Durable pre-registration is an actual dispatch barrier: the provider is
  // not entered until the App callback returns a valid registration ACK.
  {
    const registrationGate = Promise.withResolvers()
    outbound = 0
    globalThis.fetch = async () => {
      outbound += 1
      return searchResponse()
    }
    const research = callbacks({
      async preRegister(event) {
        await registrationGate.promise
        return {
          status: 'registered',
          research_id: `research_${'d'.repeat(32)}`,
          research_ledger_revision: 1,
          arguments_hash: sha(event.arguments),
          raw_arguments_hash: event.raw_arguments_hash,
        }
      },
    })
    const handle = await webAgent(research.value)
    try {
      const pending = execute(handle, 'web_search', { queries: ['barrier'] })
      await new Promise(resolve => setImmediate(resolve))
      assert(outbound === 0, 'pre_registration_not_awaited')
      registrationGate.resolve()
      const result = await pending
      await handle.waitForResearchCallbacks()
      assert(!result.isError && outbound === 1, 'pre_registration_dispatch_failed:'+JSON.stringify({result,outbound}))
    } finally {
      await handle.dispose()
    }
  }

  // Duplicate query strings remain exact in the audit event and collapse only
  // inside the official tool; one unique query means one provider request.
  let searchRequests = 0
  let auditedArguments
  globalThis.fetch = async (url, init) => {
    searchRequests += 1
    assert(url === `${DEEPSEEK_WEB_API_BASE_URL}/search`, 'search_endpoint_changed')
    assert(init?.headers?.authorization === `Bearer ${SECRET}`, 'search_secret_missing')
    const body = JSON.parse(init.body)
    assert(body.query === 'duplicate query' && body.count === 10, 'search_body_changed')
    assert(!Object.hasOwn(body, 'ori_question'), 'original_question_sent')
    return searchResponse()
  }
  {
    const research = callbacks({
      preRegister(event) {
        auditedArguments = event.arguments
        return {
          status: 'registered',
          research_id: `research_${'b'.repeat(32)}`,
          research_ledger_revision: 2,
          arguments_hash: sha(event.arguments),
          raw_arguments_hash: event.raw_arguments_hash,
        }
      },
    })
    const handle = await webAgent(research.value)
    try {
      const result = await execute(handle, 'web_search', {
        queries: ['duplicate query', 'duplicate query'],
      })
      await handle.waitForResearchCallbacks()
      assert(!result.isError && searchRequests === 1, 'search_request_count')
      assert(auditedArguments.queries.length === 2, 'audit_arguments_deduplicated')
      const terminal = research.terminals[0]
      assert(terminal.status === 'completed'
        && terminal.presentation_meta.sources[0].publishedAt === '2026-10-23',
      'search_presentation_meta_lost')
    } finally {
      await handle.dispose()
    }
  }

  // Provider failure is terminal after one request; no retry or fallback runs.
  searchRequests = 0
  globalThis.fetch = async () => {
    searchRequests += 1
    return new Response(JSON.stringify({ error: 'fixture failure' }), {
      status: 503,
      headers: { 'content-type': 'application/json' },
    })
  }
  {
    const research = callbacks()
    const handle = await webAgent(research.value)
    try {
      const result = await execute(handle, 'web_search', { queries: ['one attempt'] })
      await handle.waitForResearchCallbacks()
      assert(result.isError && searchRequests === 1, 'search_retried_or_fell_back')
    } finally {
      await handle.dispose()
    }
  }

  // Lost registration ACK recovery is non-dispatchable.
  outbound = 0
  globalThis.fetch = async () => {
    outbound += 1
    return searchResponse()
  }
  {
    const research = callbacks({
      preRegister(event) {
        return {
          status: 'already_registered',
          research_id: `research_${'c'.repeat(32)}`,
          research_ledger_revision: 3,
          arguments_hash: sha(event.arguments),
          raw_arguments_hash: event.raw_arguments_hash,
        }
      },
    })
    const handle = await webAgent(research.value)
    try {
      const result = await execute(handle, 'web_search', { queries: ['replay'] })
      let settlementCode
      try {
        await handle.waitForResearchCallbacks()
      } catch (error) {
        settlementCode = error.code
      }
      assert(result.isError && outbound === 0
        && research.terminals.length === 0
        && settlementCode === 'research_terminal_missing'
        && handle.webResearchDiagnostics().pendingReconciliations === 1,
      'replay_was_dispatched')
    } finally {
      await handle.dispose()
    }
  }

  // A callback may reject sensitive input, but its source error is never copied
  // into the model-facing denial or diagnostics.
  {
    const research = callbacks({
      preRegister() {
        throw new Error(`rejected ${SECRET_IN_ERROR}`)
      },
    })
    const handle = await webAgent(research.value)
    try {
      const result = await execute(handle, 'web_search', { queries: ['sensitive'] })
      let code
      try {
        await handle.waitForResearchCallbacks()
      } catch (error) {
        code = error.code
      }
      const visible = JSON.stringify({
        result, code, diagnostics: handle.webResearchDiagnostics(), fatals: research.fatals,
      })
      assert(result.isError && code === 'research_pre_registration_callback_failed'
        && !visible.includes(SECRET_IN_ERROR), 'callback_secret_leaked')
    } finally {
      await handle.dispose()
    }
  }

  // Exercise the model-facing fetch success/presentation path with an
  // in-process provider fixture under the same fixed `http` registry slot.
  // No socket is opened.  Restore the official provider before testing its
  // pre-network SSRF and URL-policy rejections.
  let controlledFetches = 0
  const providers = kernel.context.web.fetchProviders
  const officialHttpProvider = providers.get('http')
  assert(officialHttpProvider !== undefined, 'official_http_provider_missing')
  providers.set('http', {
    id: 'http',
    available: () => true,
    async fetch(request, signal) {
      controlledFetches += 1
      assert(request.url === 'https://public.example.test/fixture',
        'controlled_fetch_target_changed')
      assert(!signal.aborted, 'controlled_fetch_unexpected_abort')
      return {
        url: request.url,
        statusCode: 200,
        body: { kind: 'text', content: 'controlled public page' },
        truncated: false,
      }
    },
  })
  {
    const research = callbacks()
    const handle = await webAgent(research.value)
    try {
      const ok = await execute(handle, 'web_fetch', {
        url: 'https://public.example.test/fixture',
      })
      await handle.waitForResearchCallbacks()
      assert(!ok.isError && controlledFetches === 1, 'controlled_fetch_failed')
      assert(research.terminals[0].presentation_meta.url
        === 'https://public.example.test/fixture'
        && research.terminals[0].presentation_meta.statusCode === 200,
      'controlled_fetch_presentation_meta_lost')
    } finally {
      await handle.dispose()
    }
  }
  providers.set('http', officialHttpProvider)
  {
    const research = callbacks()
    const handle = await webAgent(research.value)
    try {
      for (const url of [
        'http://127.0.0.1:1/private',
        'http://10.0.0.1/private',
        'http://[::1]/private',
        'http://user:password@public.example.test/private',
        'file:///private',
      ]) {
        const blocked = await execute(handle, 'web_fetch', { url })
        await handle.waitForResearchCallbacks()
        assert(blocked.isError && controlledFetches === 1,
          'ssrf_or_url_policy_not_blocked')
      }
    } finally {
      await handle.dispose()
    }
  }

  // Caller cancellation reaches the official provider and settles as cancelled.
  let cancelRequests = 0
  const requestStarted = Promise.withResolvers()
  globalThis.fetch = async (_url, init) => {
    cancelRequests += 1
    requestStarted.resolve()
    return new Promise((_resolve, reject) => {
      const abort = () => reject(new DOMException('aborted', 'AbortError'))
      if (init.signal.aborted) abort()
      else init.signal.addEventListener('abort', abort, { once: true })
    })
  }
  {
    const research = callbacks()
    const handle = await webAgent(research.value)
    try {
      const controller = new AbortController()
      const pending = execute(handle, 'web_search', { queries: ['cancel'] }, {
        signal: controller.signal,
      })
      await requestStarted.promise
      controller.abort(new Error('fixture_cancel'))
      const result = await pending
      await handle.waitForResearchCallbacks()
      assert(result.isError && ['ABORTED', 'WEB_ABORTED'].includes(result.error?.info?.code)
        && cancelRequests === 1 && research.terminals[0].status === 'cancelled'
        && research.terminals[0].presentation_meta === null
        && research.terminals[0].resource_ids.length === 0,
      'web_cancel_not_enforced')
    } finally {
      await handle.dispose()
    }
  }

  // Cooperative timeout wins over an abort-aware provider. Clamp timers only
  // inside this isolated probe so the production policy stays at 30 seconds.
  let timeoutRequests = 0
  globalThis.setTimeout = (callback, delay, ...args) => (
    originalSetTimeout(callback, Math.min(delay, 10), ...args)
  )
  globalThis.fetch = async (_url, init) => {
    timeoutRequests += 1
    return new Promise((_resolve, reject) => {
      const abort = () => reject(new DOMException('aborted', 'AbortError'))
      if (init.signal.aborted) abort()
      else init.signal.addEventListener('abort', abort, { once: true })
    })
  }
  {
    const research = callbacks()
    const handle = await webAgent(research.value)
    try {
      const result = await execute(handle, 'web_search', { queries: ['timeout'] })
      await handle.waitForResearchCallbacks()
      assert(result.isError && result.error?.info?.code === 'TOOL_TIMEOUT'
        && timeoutRequests === 1, 'web_timeout_not_enforced')
    } finally {
      await handle.dispose()
    }
  }
  globalThis.setTimeout = originalSetTimeout

  // A terminal persistence failure is fail-closed and emits only a stable code.
  globalThis.fetch = async () => searchResponse()
  {
    const research = callbacks({
      onResult() {
        throw new Error(`terminal failure ${SECRET_IN_ERROR}`)
      },
    })
    const handle = await webAgent(research.value)
    try {
      const result = await execute(handle, 'web_search', { queries: ['settlement'] })
      assert(!result.isError, 'fixture_search_failed')
      let code
      try {
        await handle.waitForResearchCallbacks()
      } catch (error) {
        code = error.code
      }
      const visible = JSON.stringify({ code, fatals: research.fatals })
      assert(code === 'research_result_callback_failed'
        && !visible.includes(SECRET_IN_ERROR), 'terminal_failure_not_closed')
    } finally {
      await handle.dispose()
    }
  }

  const report = {
    contract: 'genshin-native-dsh-web-stack-offline-v1',
    status: 'passed',
    officialDshWeb: true,
    tools: NATIVE_WEB_TOOLS,
    searchProvider: 'deepseek-search-api',
    fetchProvider: 'http',
    deepSeekFetchProviderMounted: false,
    sendOriginalQuestion: false,
    noCredentialNoDispatch: true,
    duplicateQueryProviderRequests: 1,
    providerFailureRequests: 1,
    controlledFetches,
    ssrfBlocked: true,
    timeoutEnforced: true,
    cancellationEnforced: true,
    retryOrFallback: false,
    callbackFailureFailClosed: true,
    externalNetworkUsed: false,
    inProcessFixtureOnly: true,
  }
  const output = JSON.stringify(report)
  assert(!output.includes(SECRET) && !output.includes(SECRET_IN_ERROR)
    && !output.includes(ENV_SECRET), 'report_secret_leak')
  process.stdout.write(`${output}\n`)
} finally {
  if (originalEnvironmentSecret === undefined) {
    Reflect.deleteProperty(process.env, 'DEEPSEEK_API_KEY')
  } else {
    process.env.DEEPSEEK_API_KEY = originalEnvironmentSecret
  }
  for (const [name, value] of [
    ['DSH_WEB_SEARCH_PROVIDER', originalSearchProvider],
    ['DSH_WEB_FETCH_PROVIDER', originalFetchProvider],
  ]) {
    if (value === undefined) Reflect.deleteProperty(process.env, name)
    else process.env[name] = value
  }
  globalThis.setTimeout = originalSetTimeout
  globalThis.fetch = originalFetch
  await kernel?.dispose().catch(() => {})
  await dshProfile?.cleanup().catch(() => {})
}
