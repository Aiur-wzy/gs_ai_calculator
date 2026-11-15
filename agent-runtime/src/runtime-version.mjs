/** Exact Node runtime identity for the current frozen Phase 0 cohort. */
export const PINNED_NODE_VERSION = '24.19.0'

export function assertPinnedNodeVersion() {
  if (process.versions.node !== PINNED_NODE_VERSION) {
    throw new Error('native_runtime_node_version_mismatch')
  }
}
