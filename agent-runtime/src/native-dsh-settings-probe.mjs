import assert from 'node:assert/strict'

import {
  NATIVE_DSH_DEFAULTS,
  NATIVE_DSH_DEFAULT_SOURCES,
  NATIVE_DSH_REVIEWED_MODEL_IDS,
  resolveNativeDshSettings,
} from './native-dsh-settings.mjs'
assert.equal(NATIVE_DSH_DEFAULTS.compaction.maxTokens, undefined)
assert.equal(NATIVE_DSH_DEFAULT_SOURCES.compaction.maxTokens.kind, 'dsh_derived_default')
assert.equal(resolveNativeDshSettings().compaction.maxTokens, 65_536)
assert.equal(resolveNativeDshSettings({
  compaction: { headroomTokens: 80_000 },
}).compaction.maxTokens, 80_000)

// This contract only validates the one-time legacy migration input. The
// active production composition/defaults are owned by the official base.
const migrated = resolveNativeDshSettings({compaction: {headroomTokens: 80_000},
  agentLoop: {maxParallelToolCalls: 12}, persistence: {listConcurrency: 4}})
assert.equal(migrated.agentLoop.maxParallelToolCalls, 12)
assert.equal(migrated.persistence.listConcurrency, 4)

assert.throws(() => resolveNativeDshSettings({
  compaction: {
    summarizationProvider: 'unreviewed-provider',
    summarizationModel: 'model',
  },
}), /not reviewed/u)
assert.throws(() => resolveNativeDshSettings({
  compaction: {
    summarizationProvider: 'deepseek-official',
    summarizationModel: 'not-installed',
  },
}), /not an installed reviewed model/u)
assert.throws(() => resolveNativeDshSettings({
  compaction: {
    modelPolicies: [{ provider: 'deepseek-official', model: 'not-installed' }],
  },
}), /not an installed reviewed model/u)
assert.throws(() => resolveNativeDshSettings({
  toolResultPruner: { thresholdChars: 100, headChars: 90, tailChars: 90 },
}), /exceeds/u)

console.log(JSON.stringify({
  contract: 'genshin-native-dsh-settings-probe-v1',
  status: 'passed',
  reviewedModels: NATIVE_DSH_REVIEWED_MODEL_IDS,
}))
