/** Verify that the candidate DSH cohort can cold-read and resume an accepted old cohort Session. */
import assert from 'node:assert/strict'
import { mkdtemp, readFile, rm } from 'node:fs/promises'
import { join, resolve } from 'node:path'
import { pathToFileURL } from 'node:url'

const CURRENT_RUNTIME_ROOT = resolve(import.meta.dirname, '..', 'agent-runtime')
const OLD_COHORT = '0.2.1-rc.2'
const CURRENT_COHORT = '0.2.1-rc.5'
const SECRET = 'dsh-cohort-cold-compatibility-fixture'

function argument(name) {
  const index = process.argv.indexOf(name)
  if (index < 0 || index + 1 >= process.argv.length) {
    throw new Error(`missing_argument:${name}`)
  }
  return resolve(process.argv[index + 1])
}

async function json(path) {
  return JSON.parse(await readFile(path, 'utf8'))
}

function moduleUrl(root, relative) {
  return pathToFileURL(resolve(root, relative)).href
}

function listingResponse() {
  return Response.json({
    data: ['deepseek-flash', 'deepseek-v4-pro'].map(id => ({
      id,
      input_modalities: ['text'],
      output_modalities: ['text'],
      api_capabilities: { anthropic_messages: {} },
    })),
  })
}

function providerResponse(text) {
  const events = [
    { type: 'message_start', message: { usage: { input_tokens: 5, output_tokens: 0 } } },
    { type: 'content_block_start', index: 0, content_block: { type: 'text', text: '' } },
    { type: 'content_block_delta', index: 0, delta: { type: 'text_delta', text } },
    { type: 'content_block_stop', index: 0 },
    { type: 'message_delta', delta: { stop_reason: 'end_turn' }, usage: { output_tokens: 3 } },
    { type: 'message_stop' },
  ]
  const body = new TextEncoder().encode(events.map(event => (
    `event: ${event.type}\ndata: ${JSON.stringify(event)}\n\n`
  )).join(''))
  return new Response(new ReadableStream({
    start(controller) {
      controller.enqueue(body)
      controller.close()
    },
  }), { status: 200, headers: { 'content-type': 'text/event-stream' } })
}

const oldRuntimeRoot = argument('--old-runtime-root')
const stageParent = argument('--stage-parent')
const oldPackage = await json(join(oldRuntimeRoot, 'node_modules', '@deepseek-ai', 'dsh', 'package.json'))
const currentPackage = await json(join(
  CURRENT_RUNTIME_ROOT, 'node_modules', '@deepseek-ai', 'dsh', 'package.json',
))
assert.equal(oldPackage.version, OLD_COHORT)
assert.equal(currentPackage.version, CURRENT_COHORT)

const stageRoot = await mkdtemp(join(stageParent, 'dsh-cold-compat-'))
const profileRoot = join(stageRoot, 'profile')
const sessionRoot = join(profileRoot, 'sessions')
const storageRoot = join(profileRoot, 'storages')
const queryPath = join(profileRoot, 'session-query.sqlite')
const sessionId = 'rc2-to-rc5-cold-session'
const originalFetch = globalThis.fetch
let listingRequests = 0
let providerRequests = 0

try {
  globalThis.fetch = async (url, init) => {
    assert.equal(init?.headers?.['x-api-key'], SECRET)
    if (url === 'https://api.deepseek.com/models') {
      listingRequests += 1
      return listingResponse()
    }
    assert.equal(url, 'https://api.deepseek.com/anthropic/v1/messages')
    providerRequests += 1
    return providerResponse(`cohort answer ${providerRequests}`)
  }

  const oldKernelModule = await import(moduleUrl(oldRuntimeRoot, 'src/native-deepseek-kernel.mjs'))
  const oldLlm = await import(moduleUrl(
    oldRuntimeRoot, 'node_modules/@deepseek-ai/dsh-llm/lib/index.js',
  ))
  const oldKernel = await oldKernelModule.createNativeDeepSeekKernel({
    personaPrefix: 'DSH rc.2 cold compatibility producer.',
    profileRoot,
    sessionRoot,
    storageRoot,
    queryPath,
  })
  try {
    await oldKernel.secretController.set({
      provider: 'deepseek-official', credential_generation: 1, secret: SECRET,
    })
    const oldHandle = await oldKernel.createScopedAgent({
      sessionId, model: 'deepseek-flash', reasoningEffort: 'high', expectedTools: [],
    })
    try {
      assert.equal(oldHandle.resumed, false)
      oldHandle.agent.followup(oldLlm.createUserMessage({
        content: [{ type: 'text', text: 'write one rc.2 durable turn' }],
        source: { kind: 'user' },
      }))
      await oldHandle.agent.whenIdle()
    } finally {
      await oldHandle.dispose()
    }
  } finally {
    await oldKernel.dispose()
  }

  const currentKernelModule = await import(moduleUrl(
    CURRENT_RUNTIME_ROOT, 'src/native-deepseek-kernel.mjs',
  ))
  const currentLlm = await import(moduleUrl(
    CURRENT_RUNTIME_ROOT, 'node_modules/@deepseek-ai/dsh-llm/lib/index.js',
  ))
  const currentKernel = await currentKernelModule.createNativeDeepSeekKernel({
    personaPrefix: 'DSH rc.5 cold compatibility consumer.',
    profileRoot,
    sessionRoot,
    storageRoot,
    queryPath,
  })
  try {
    const cold = await currentKernel.readSessionHistory(sessionId, {
      turn: 1, limit: 100, include_raw_reasoning: true,
    })
    assert(cold.items.some(item => item.kind === 'message'
      && item.role === 'assistant' && item.text?.includes('cohort answer 1')))
    await currentKernel.secretController.set({
      provider: 'deepseek-official', credential_generation: 1, secret: SECRET,
    })
    const currentHandle = await currentKernel.createScopedAgent({
      sessionId, model: 'deepseek-flash', reasoningEffort: 'high', expectedTools: [],
    })
    try {
      assert.equal(currentHandle.resumed, true)
      const before = currentKernel.sessionProjection(currentHandle.agent)
      assert.equal(before.values.turnOutline.length, 1)
      currentHandle.agent.followup(currentLlm.createUserMessage({
        content: [{ type: 'text', text: 'continue the rc.2 Session under rc.5' }],
        source: { kind: 'user' },
      }))
      await currentHandle.agent.whenIdle()
      const after = currentKernel.sessionProjection(currentHandle.agent)
      assert.equal(after.values.turnOutline.length, 2)
    } finally {
      await currentHandle.dispose()
    }
  } finally {
    await currentKernel.dispose()
  }

  assert.equal(providerRequests, 2)
  assert.equal(listingRequests, 1)
  process.stdout.write(`${JSON.stringify({
    contract: 'genshin-dsh-cohort-cold-compatibility-v1',
    status: 'passed',
    producer: OLD_COHORT,
    consumer: CURRENT_COHORT,
    coldRead: true,
    resumed: true,
    continuedTurns: 2,
    providerRequests,
    modelListingRequests: listingRequests,
  })}\n`)
} finally {
  globalThis.fetch = originalFetch
  await rm(stageRoot, { recursive: true, force: true })
}
