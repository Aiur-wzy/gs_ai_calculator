/** Offline compatibility probe for the official DeepSeek credential seam. */
import { Context } from '@deepseek-ai/cordis'
import LlmRuntime from '@deepseek-ai/dsh-llm'
import { credentialKey, credentialRef } from '@deepseek-ai/dsh-credentials'
import * as DeepSeekApiKey from '@deepseek-ai/dsh-llm-deepseek-api-key'
import {
  DSH_LAUNCH_ENVIRONMENT_KEY,
  createLaunchEnvironmentSnapshot,
} from '@deepseek-ai/dsh-launch-environment'
import {
  DEEPSEEK_CREDENTIAL_REF,
  NativeMemoryCredentials,
} from './memory-credentials.mjs'

const FIXTURE_SECRET = 'fixture-memory-key-never-print'
const AMBIENT_SECRET = 'fixture-ambient-key-must-not-win'
const originalFetch = globalThis.fetch
let fetchCalls = 0
let ctx

function assert(condition, code) {
  if (!condition) throw new Error(code)
}

try {
  globalThis.fetch = async () => {
    fetchCalls += 1
    throw new Error('network_forbidden_in_credential_probe')
  }

  ctx = new Context()
  ctx.provide(DSH_LAUNCH_ENVIRONMENT_KEY, createLaunchEnvironmentSnapshot([{
    source: 'process',
    values: { DEEPSEEK_API_KEY: AMBIENT_SECRET },
  }]))
  await ctx.plugin(LlmRuntime)
  await ctx.plugin(NativeMemoryCredentials)
  // rc.5 follows the official `/models` listing for the built-in catalog.
  // This credential-only probe deliberately selects a non-official fixture
  // endpoint so it continues proving the no-network credential seam rather
  // than exercising the separately tested discovery client.
  await ctx.plugin(DeepSeekApiKey, { baseURL: 'https://fixture.invalid' })

  const missing = await ctx.llm.listModels('deepseek-official')
  assert(missing.length === 0, 'credential_service_must_block_environment_fallback')

  await ctx.credentials.set(DEEPSEEK_CREDENTIAL_REF, FIXTURE_SECRET)
  assert(!Object.keys(ctx.credentials).includes('_nativeSecret'),
    'credential_was_enumerable')
  assert((await ctx.credentials.describe(DEEPSEEK_CREDENTIAL_REF)).configured === true,
    'credential_description_missing')
  let unsupportedRefRejected = false
  try {
    await ctx.credentials.set(credentialRef('OTHER_API_KEY'), 'safe-fixture')
  } catch (error) {
    unsupportedRefRejected = error instanceof Error
      && error.message === 'credential_reference_not_allowed'
  }
  assert(unsupportedRefRejected, 'unsupported_credential_reference_was_accepted')
  let recordWriteRejected = false
  try {
    await ctx.credentials.modifyRecord(
      credentialKey('genshin-agent', 'probe'), async () => ({ kind: 'api-key' }),
    )
  } catch (error) {
    recordWriteRejected = error instanceof Error
      && error.message === 'credential_records_not_supported'
  }
  assert(recordWriteRejected, 'credential_record_write_was_accepted')
  const first = await ctx.llm.listModels('deepseek-official')
  const second = await ctx.llm.listModels('deepseek-official')
  const modelIds = first.map(item => item.id).sort()
  assert(JSON.stringify(modelIds) === JSON.stringify(['deepseek-flash', 'deepseek-v4-pro']),
    'unexpected_deepseek_catalog')
  assert(JSON.stringify(first) === JSON.stringify(second), 'catalog_changed_between_reads')

  const resolved = await Promise.all(modelIds.map(model => (
    ctx.llm.resolveModelInfo('deepseek-official', model)
  )))
  for (const model of resolved) {
    const efforts = model.reasoning?.efforts?.map(item => item.id)
    assert(JSON.stringify(efforts) === JSON.stringify(['off', 'low', 'high', 'max']),
      'unexpected_reasoning_efforts')
    assert(model.reasoning?.defaultEffort === 'high', 'unexpected_default_reasoning_effort')
  }

  const resolvesBeforeClear = ctx.credentials.resolveCount
  assert(resolvesBeforeClear >= 3, 'credential_was_not_resolved_per_catalog_operation')
  await ctx.credentials.unset(DEEPSEEK_CREDENTIAL_REF)
  const afterClear = await ctx.llm.listModels('deepseek-official')
  assert(afterClear.length === 0, 'cleared_credential_remained_visible')
  assert(ctx.credentials.resolveCount > resolvesBeforeClear, 'clear_did_not_force_fresh_resolution')
  assert(fetchCalls === 0, 'credential_catalog_probe_used_network')

  const report = {
    contract: 'genshin-native-memory-credentials-v1',
    status: 'passed',
    provider: 'deepseek-official',
    modelIds,
    reasoningEfforts: ['off', 'low', 'high', 'max'],
    defaultReasoningEffort: 'high',
    resolutionCount: ctx.credentials.resolveCount,
    environmentFallbackBlocked: true,
    unsupportedReferencesRejected: true,
    recordWritesRejected: true,
    networkCalls: fetchCalls,
  }
  const output = JSON.stringify(report)
  assert(!output.includes(FIXTURE_SECRET) && !output.includes(AMBIENT_SECRET),
    'credential_leaked_into_probe_output')
  process.stdout.write(`${output}\n`)
} catch (error) {
  const message = error instanceof Error ? error.message : 'credential_probe_failed'
  if (message.includes(FIXTURE_SECRET) || message.includes(AMBIENT_SECRET)) {
    throw new Error('credential_probe_failed_with_redacted_detail')
  }
  throw error
} finally {
  await ctx?.fiber.dispose()
  globalThis.fetch = originalFetch
}
