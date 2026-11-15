/** Offline proof that the pinned official DSH DeepSeek adapter owns SSE. */
import { Context } from '@deepseek-ai/cordis'
import LlmRuntime from '@deepseek-ai/dsh-llm'
import * as DeepSeekApiKey from '@deepseek-ai/dsh-llm-deepseek-api-key'
import {
  DSH_LAUNCH_ENVIRONMENT_KEY,
  createLaunchEnvironmentSnapshot,
} from '@deepseek-ai/dsh-launch-environment'

import {
  DEEPSEEK_CREDENTIAL_REF,
  NativeMemoryCredentials,
} from './memory-credentials.mjs'

const SECRET = 'offline-official-adapter-sentinel'
const OFFICIAL_ROOT = 'https://api.deepseek.com/anthropic'
const originalFetch = globalThis.fetch
let ctx
let calls = 0
let requestFacts

function assert(condition, code) {
  if (!condition) throw new Error(code)
}

function sse(events) {
  const encoded = new TextEncoder().encode(events.map(event => (
    `event: ${event.type}\ndata: ${JSON.stringify(event)}\n\n`
  )).join(''))
  return new Response(new ReadableStream({
    start(controller) {
      controller.enqueue(encoded)
      controller.close()
    },
  }), {
    status: 200,
    headers: { 'content-type': 'text/event-stream' },
  })
}

try {
  globalThis.fetch = async (url, init) => {
    calls += 1
    assert(url === `${OFFICIAL_ROOT}/v1/messages`, 'official_endpoint_changed')
    assert(init?.method === 'POST' && init?.redirect === 'error', 'request_policy_changed')
    assert(init?.headers?.['x-api-key'] === SECRET, 'memory_credential_not_used')
    const body = JSON.parse(init.body)
    requestFacts = {
      model: body.model,
      system: body.system,
      effort: body.output_config?.effort,
      thinking: body.thinking?.type,
      stream: body.stream,
      messageCount: body.messages?.length,
    }
    return sse([
      {
        type: 'message_start',
        message: { usage: { input_tokens: 11, output_tokens: 0 } },
      },
      {
        type: 'content_block_start', index: 0,
        content_block: { type: 'thinking', thinking: '' },
      },
      {
        type: 'content_block_delta', index: 0,
        delta: { type: 'thinking_delta', thinking: 'offline reasoning' },
      },
      { type: 'content_block_stop', index: 0 },
      {
        type: 'content_block_start', index: 1,
        content_block: { type: 'text', text: '' },
      },
      {
        type: 'content_block_delta', index: 1,
        delta: { type: 'text_delta', text: 'offline answer' },
      },
      { type: 'content_block_stop', index: 1 },
      {
        type: 'message_delta',
        delta: { stop_reason: 'end_turn' },
        usage: { output_tokens: 7 },
      },
      { type: 'message_stop' },
    ])
  }

  ctx = new Context()
  // An empty launch environment plus an explicit official root proves that
  // ambient API keys and base URLs cannot change this route.
  ctx.provide(DSH_LAUNCH_ENVIRONMENT_KEY, createLaunchEnvironmentSnapshot([]))
  await ctx.plugin(LlmRuntime)
  await ctx.plugin(NativeMemoryCredentials)
  await ctx.plugin(DeepSeekApiKey, {
    baseURL: OFFICIAL_ROOT,
    reasoningEffort: 'high',
  })
  await ctx.credentials.set(DEEPSEEK_CREDENTIAL_REF, SECRET)

  const chunks = []
  for await (const chunk of ctx.llm.stream({
    provider: 'deepseek-official',
    model: 'deepseek-flash',
    reasoningEffort: 'high',
    system: 'offline system',
    messages: [{ role: 'user', content: [{ type: 'text', text: 'offline prompt' }] }],
    signal: new AbortController().signal,
  })) chunks.push(chunk)

  assert(calls === 1, 'unexpected_provider_request_count')
  assert(requestFacts?.model === 'deepseek-flash', 'model_not_forwarded')
  assert(requestFacts?.system === 'offline system', 'system_not_forwarded')
  assert(requestFacts?.effort === 'high', 'reasoning_effort_not_forwarded')
  assert(requestFacts?.stream === true && requestFacts?.messageCount === 1,
    'messages_request_shape_changed')
  assert(chunks.some(chunk => chunk.type === 'reasoning-delta'
    && chunk.text === 'offline reasoning'), 'reasoning_stream_missing')
  assert(chunks.some(chunk => chunk.type === 'text-delta'
    && chunk.text === 'offline answer'), 'text_stream_missing')
  assert(chunks.some(chunk => chunk.type === 'usage'
    && chunk.usage.inputTokens === 11 && chunk.usage.outputTokens === 7),
  'usage_stream_missing')
  assert(chunks.at(-1)?.type === 'finish'
    && chunks.at(-1)?.reason?.kind === 'stop', 'finish_stream_missing')

  const report = {
    contract: 'genshin-native-official-deepseek-adapter-offline-v1',
    status: 'passed',
    providerRequests: calls,
    requestFacts,
    chunkTypes: chunks.map(chunk => chunk.type),
    officialAdapterOwnsSse: true,
    networkUsed: false,
  }
  const output = JSON.stringify(report)
  assert(!output.includes(SECRET), 'secret_leaked_into_probe_output')
  process.stdout.write(`${output}\n`)
} finally {
  await ctx?.fiber.dispose().catch(() => {})
  globalThis.fetch = originalFetch
}
