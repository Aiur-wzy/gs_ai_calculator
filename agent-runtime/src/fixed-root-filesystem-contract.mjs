/** Machine-verifiable contract for the App-private, read-only DSH filesystem. */

export const FIXED_ROOT_FILESYSTEM_CONTRACT =
  'genshin-dsh-fixed-root-filesystem-v1'
export const FIXED_ROOT_FILESYSTEM_VERSION = 1
export const FIXED_ROOT_VIRTUAL_ROOT = '/__genshin_fixed_fs_v1__'
export const FIXED_ROOT_LEAF_PATTERN =
  /^[0-9a-f]{64}(?:\.[a-z0-9]{1,16})?$/u

export const FIXED_ROOT_SUPPORTED_METHODS = Object.freeze([
  'resolve',
  'processPath',
  'processPathFromHostPath',
  'fileUrl',
  'contains',
  'stat',
  'lstat',
  'readBytes',
])

export const FIXED_ROOT_REJECTED_METHODS = Object.freeze([
  'readText',
  'streamText',
  'readByteRange',
  'listDir',
  'writeText',
  'editText',
  'watch',
])

export const FIXED_ROOT_FORBIDDEN_CAPABILITIES = Object.freeze([
  'arbitrary-host-path',
  'directory-enumeration',
  'filesystem-write',
  'mcp',
  'sandbox-escalation',
  'shell',
  'subagent',
  'terminal',
])

export const FIXED_ROOT_POLICY = Object.freeze({
  productionOwner: false,
  rootOwner: 'trusted-host-constructor',
  capabilityState: 'ecmascript-private',
  sameProcessExtensions: 'trusted-computing-base',
  modelMayChooseRoot: false,
  guiMayChooseRoot: false,
  environmentMayChooseRoot: false,
  virtualAbsoluteNamespace: FIXED_ROOT_VIRTUAL_ROOT,
  realHostAbsolutePathsAcceptedByResolve: false,
  hostMapping: 'existing-direct-regular-single-link-files-only',
  directoryShape: 'flat',
  readOnly: true,
  sandboxMode: 'read-only',
  generalFileToolRegistered: false,
  hardlinksAccepted: false,
  symlinksAccepted: false,
  ordinaryWindowsJunctionsAccepted: false,
  unclassifiedWindowsReparsePoints: 'host-mirror-prevalidation-required',
  boundedReadRequired: true,
  sessionControllerIntegrated: false,
})

export function assertFixedRootFilesystemContract() {
  if (FIXED_ROOT_FILESYSTEM_VERSION !== 1
      || FIXED_ROOT_POLICY.productionOwner !== false
      || FIXED_ROOT_POLICY.readOnly !== true
      || FIXED_ROOT_POLICY.generalFileToolRegistered !== false
      || FIXED_ROOT_POLICY.capabilityState !== 'ecmascript-private'
      || FIXED_ROOT_POLICY.hardlinksAccepted !== false
      || FIXED_ROOT_POLICY.symlinksAccepted !== false
      || FIXED_ROOT_POLICY.boundedReadRequired !== true
      || FIXED_ROOT_POLICY.sessionControllerIntegrated !== false) {
    throw new Error('fixed_root_filesystem_contract_invalid')
  }
  return FIXED_ROOT_POLICY
}
