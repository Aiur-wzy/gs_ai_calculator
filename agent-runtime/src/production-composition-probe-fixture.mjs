/** Workspace-local durable-root fixture for offline Node composition probes. */
import { mkdtemp, rm } from 'node:fs/promises'
import { join } from 'node:path'

import { productionCompositionPathsFromProfileRoot } from './production-composition-contract.mjs'

export async function createProductionCompositionProbeFixture(label) {
  if (typeof label !== 'string' || !/^[a-z][a-z0-9-]{2,47}$/u.test(label)) {
    throw new TypeError('probe fixture label is invalid')
  }
  // Keep the root in the workspace: Windows native JSONL replacement can be
  // denied by the managed sandbox when the root lives in virtualized TEMP.
  const profileRoot = await mkdtemp(join(process.cwd(), `.${label}-`))
  let cleaned = false
  return Object.freeze({
    paths: productionCompositionPathsFromProfileRoot(profileRoot),
    async cleanup() {
      if (cleaned) return
      cleaned = true
      await rm(profileRoot, { recursive: true, force: true })
    },
  })
}
