/** Offline probe for the private credential generation gate. */
import assert from 'node:assert/strict'

import { Context } from '@deepseek-ai/cordis'

import {
  DEEPSEEK_CREDENTIAL_REF,
  NativeMemoryCredentials,
} from './memory-credentials.mjs'
import {
  DEEPSEEK_PROVIDER_ID,
  NativeSecretControlError,
  NativeSecretController,
} from './secret-control.mjs'

const FIRST = 'SENTINEL_PHASE5_KEY_ONE'
const SECOND = 'SENTINEL_PHASE5_KEY_TWO'
const ctx = new Context()
let controller

function expectCode(code, operation) {
  return assert.rejects(operation, error => (
    error instanceof NativeSecretControlError
      && error.code === code
      && !String(error).includes(FIRST)
      && !String(error).includes(SECOND)
  ))
}

try {
  await ctx.plugin(NativeMemoryCredentials)
  controller = new NativeSecretController(ctx.credentials)

  const installed = await controller.set({
    provider: DEEPSEEK_PROVIDER_ID,
    credential_generation: 3,
    secret: FIRST,
  })
  assert.deepEqual(installed, {
    disposition: 'installed',
    provider: DEEPSEEK_PROVIDER_ID,
    credential_generation: 3,
    configured: true,
    disposed: false,
  })
  assert.equal((await ctx.credentials.resolve(DEEPSEEK_CREDENTIAL_REF)).value, FIRST)

  assert.equal((await controller.set({
    provider: DEEPSEEK_PROVIDER_ID,
    credential_generation: 3,
    secret: FIRST,
  })).disposition, 'replay')
  await expectCode('secret_generation_conflict', () => controller.set({
    provider: DEEPSEEK_PROVIDER_ID,
    credential_generation: 3,
    secret: SECOND,
  }))
  await expectCode('secret_generation_stale', () => controller.clear({
    provider: DEEPSEEK_PROVIDER_ID,
    credential_generation: 2,
  }))

  assert.equal((await controller.set({
    provider: DEEPSEEK_PROVIDER_ID,
    credential_generation: 4,
    secret: SECOND,
  })).disposition, 'installed')
  assert.equal((await ctx.credentials.resolve(DEEPSEEK_CREDENTIAL_REF)).value, SECOND)

  assert.equal((await controller.clear({
    provider: DEEPSEEK_PROVIDER_ID,
    credential_generation: 5,
  })).disposition, 'cleared')
  assert.equal(await ctx.credentials.resolve(DEEPSEEK_CREDENTIAL_REF), undefined)
  assert.equal((await controller.clear({
    provider: DEEPSEEK_PROVIDER_ID,
    credential_generation: 5,
  })).disposition, 'replay')

  await expectCode('secret_set_payload_shape', () => controller.set({
    provider: DEEPSEEK_PROVIDER_ID,
    credential_generation: 6,
    secret: FIRST,
    extra: true,
  }))
  await expectCode('secret_provider_unsupported', () => controller.clear({
    provider: 'other-provider',
    credential_generation: 6,
  }))
  await expectCode('secret_value_invalid', () => controller.set({
    provider: DEEPSEEK_PROVIDER_ID,
    credential_generation: 6,
    secret: 'bad key',
  }))

  await controller.dispose()
  assert.equal(await ctx.credentials.resolve(DEEPSEEK_CREDENTIAL_REF), undefined)
  await expectCode('secret_controller_disposed', () => controller.clear({
    provider: DEEPSEEK_PROVIDER_ID,
    credential_generation: 6,
  }))

  const report = {
    contract: 'genshin-native-secret-control-v1',
    status: 'passed',
    generation: 5,
    configured: false,
    exactReplay: true,
    staleRejected: true,
    conflictingGenerationRejected: true,
    secretEchoed: false,
  }
  const output = JSON.stringify(report)
  assert.equal(output.includes(FIRST) || output.includes(SECOND), false)
  process.stdout.write(`${output}\n`)
} finally {
  await controller?.dispose()
  await ctx.fiber.dispose()
}
