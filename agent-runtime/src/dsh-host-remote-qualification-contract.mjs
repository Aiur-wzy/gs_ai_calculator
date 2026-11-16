/**
 * Machine-verifiable contract for the rc.3 carrier-neutral Host seams.
 *
 * This is qualification evidence only.  It does not mount a production
 * client owner, expose a GUI route, or authorize SessionController prompt.
 */
export const DSH_HOST_REMOTE_QUALIFICATION_CONTRACT =
  'genshin-dsh-host-remote-qualification-v1'
export const DSH_HOST_REMOTE_QUALIFICATION_VERSION = 1
export const DSH_HOST_REMOTE_RELEASE = '0.2.2-rc.3'
export const DSH_HOST_REMOTE_DEPENDENCY_IDENTITY = Object.freeze({
  node: '24.19.0',
  cordis: '4.1.0',
  dsh: DSH_HOST_REMOTE_RELEASE,
})

export const DSH_HOST_REMOTE_PUBLIC_EXPORTS = Object.freeze({
  '@deepseek-ai/dsh-client-connection': Object.freeze([
    'apply', 'serverResponseSchema',
  ]),
  '@deepseek-ai/dsh-api-gateway': Object.freeze([
    'TypertGatewayService',
  ]),
  '@deepseek-ai/dsh-api-remote-access': Object.freeze([
    'RemoteAccessService',
  ]),
  '@deepseek-ai/dsh-api-remotes': Object.freeze(['apply']),
  '@deepseek-ai/dsh-api-session-controller': Object.freeze([
    'SessionController',
  ]),
  '@deepseek-ai/dsh-typert-protocol': Object.freeze([
    'Remote', 'TypertRemoteService',
  ]),
  '@deepseek-ai/dsh-typert-registry': Object.freeze([
    'TypertRegistry',
  ]),
  '@deepseek-ai/dsh-fs-local': Object.freeze(['LocalFileSystem']),
})

export const DSH_HOST_REMOTE_ALLOWED_PROBE_ENDPOINTS = Object.freeze([
  '$events',
  'probe/count',
  'probe/echo',
  'remoteAccess/follow',
])

export const DSH_HOST_REMOTE_FORBIDDEN_CAPABILITIES = Object.freeze([
  'arbitrary-bundle',
  'arbitrary-filesystem-path',
  'browser-listener',
  'http-listener',
  'mcp',
  'plugin-manager',
  'sandbox',
  'shell',
  'subagent',
  'terminal',
  'websocket-listener',
])

export const DSH_HOST_REMOTE_QUALIFICATION = Object.freeze({
  productionOwner: false,
  transport: Object.freeze({
    channel: '/api',
    connection: 'carrier-neutral-public-api',
    httpListener: false,
    webSocketListener: false,
    webServerMounted: false,
  }),
  peer: Object.freeze({
    browserChoosesPrincipal: false,
    ownerCreatedByHost: true,
    staleGenerationRejected: true,
  }),
  clientModules: Object.freeze({
    acceptedManifest: '../frontend/dsh-client-accepted-manifest.json',
    composition: '../frontend/dsh-client-composition.json',
    probe: '../frontend/scripts/probe-dsh-client-composition.mjs',
    publicApi: 'ClientModuleRegistry.graph/fetchBundle',
    unreviewedBundleRejected: true,
  }),
  sessionController: Object.freeze({
    nativeOpen: false,
    requiresFsService: true,
  }),
  fsCandidate: Object.freeze({
    package: '@deepseek-ai/dsh-fs-local',
    status: 'not-qualified',
    reason: 'cwd-is-a-resolution-default-not-a-containment-boundary',
    replacementImplemented: false,
  }),
})

export function assertQualificationContract() {
  if (DSH_HOST_REMOTE_DEPENDENCY_IDENTITY.node !== '24.19.0'
      || DSH_HOST_REMOTE_DEPENDENCY_IDENTITY.cordis !== '4.1.0'
      || DSH_HOST_REMOTE_DEPENDENCY_IDENTITY.dsh !== DSH_HOST_REMOTE_RELEASE
      || DSH_HOST_REMOTE_QUALIFICATION.productionOwner !== false
      || DSH_HOST_REMOTE_QUALIFICATION.transport.httpListener !== false
      || DSH_HOST_REMOTE_QUALIFICATION.transport.webSocketListener !== false
      || DSH_HOST_REMOTE_QUALIFICATION.fsCandidate.status !== 'not-qualified'
      || DSH_HOST_REMOTE_QUALIFICATION.fsCandidate.replacementImplemented !== false) {
    throw new Error('dsh_host_remote_qualification_contract_invalid')
  }
  return DSH_HOST_REMOTE_QUALIFICATION
}
