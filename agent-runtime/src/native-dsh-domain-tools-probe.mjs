/** Actual DSH loop/scopes with reverse transport and dynamic App catalogs. */
import assert from 'node:assert/strict'
import { createHash, randomUUID } from 'node:crypto'
import { mkdtemp, mkdir, rm } from 'node:fs/promises'
import { join } from 'node:path'
import { APP_TOOLS_SNAPSHOT_CONTRACT, hashAppToolsSnapshotBody, MAX_SEQUENTIAL_APP_CALLS, MAX_APP_TOOL_RESULT_BYTES } from './native-app-tool-catalog.mjs'
import { NativeDshDomainTransport } from './native-dsh-domain-transport.mjs'
import { createNativeDshSessionHost } from './native-dsh-session-host.mjs'
import { createNativeDshDomainFixtureFetch } from './native-dsh-domain-fixture-fetch.mjs'
import { assertPinnedNodeVersion } from './runtime-version.mjs'
import { withPublicationAnswers } from './publication-usage.mjs'

assertPinnedNodeVersion()
const root = await mkdtemp(join(process.cwd(), '.codex-tmp/domain-tools-'))
await mkdir(join(root, 'media'))
const originalFetch = globalThis.fetch
const modes = new Map()
const calls = []
const snapshots = new Map()
const hostFacts = new Map()
const taskSources = []
const publicationQuestion = {
  id: 'genshin-publication-fixture', header: '确认使用资料', question: '拟使用资料测试角色，仅供学习参考。',
  options: [{ label: '同意本任务使用' }, { label: '不同意使用' }],
}
const publicationQuery = { resource: 'publication_usage', request: {
  purpose: 'calculation', entities: [{ type: 'character', id: 2 }] } }
const answerText = value => [{ type: 'text', text: JSON.stringify(value) }]

function verifyPublicationAnswerBoundaries(usage) {
  const observation = { inheritedEventCount: 0, events: [
    { type: 'tool/call', data: { name: 'gs_read_context', arguments: JSON.stringify(publicationQuery), callId: 'read' } },
    { type: 'tool/result', data: { message: { toolCallId: 'read', content: answerText({ data: usage }) } } },
    { type: 'tool/call', data: { name: 'ask_user_question',
      arguments: JSON.stringify({ questions: [usage.question] }), callId: 'ask' } },
    { type: 'tool/result', data: { message: { toolCallId: 'ask', content: answerText({ pending: true }) } } },
  ].map((event, seq) => ({ ...event, seq })) }
  const project = () => withPublicationAnswers({ data: usage }, observation).data.entities[0].response.status
  assert.equal(project(), 'waiting_for_user')
  const answers = [{ id: usage.question.id, selected: ['不同意使用'] }]
  const content = answerText({ answers })
  observation.events.push({ seq: 4, type: 'user/message', data: { source: { kind: 'user' }, content } })
  assert.equal(project(), 'waiting_for_user', 'plain text cannot spoof a structured DSH answer')
  observation.events.push({ seq: 5, type: 'user/message',
    data: { source: { kind: 'user-question-reply', callId: 'ask' }, content } })
  assert.equal(project(), 'declined')
  observation.inheritedEventCount = 6
  assert.equal(project(), 'ask_user', 'a fork cannot inherit usage permission')
}
function focusCard(sessionId) {
  const text = 'Focus card: ' + (hostFacts.get(sessionId) ?? 'Initial loadout guidance')
  const hash = createHash('sha256').update(text).digest('hex')
  return { focus: 'loadout_draft', resource: 'references/workflow/focus/loadout.md', text,
    content_hash: hash, page_hash: hash, next_offset: null, focus_is_authorization: false }
}
const fixed = ['gs_app_get_workspace', 'gs_app_request_action', 'gs_list_research_calls', 'gs_report_research_usage']
const policies = {
  gs_app_get_workspace: { policy: 'app_workspace_read', effect: 'external_action_ledger' },
  gs_app_request_action: { policy: 'app_action_request', effect: 'external_action_ledger' },
  gs_list_research_calls: { policy: 'research_ledger_read', effect: 'external_research_ledger' },
  gs_report_research_usage: { policy: 'research_ledger_write', effect: 'external_research_ledger' },
  gs_patch_candidate: { policy: 'candidate_write', effect: 'candidate' },
  gs_read_context: { policy: 'domain_read', effect: 'none' },
}
function snapshot(id, revision = 0) {
  const names = [...fixed, ...(['scope-one', 'unbound-session', 'task-review-session'].includes(id) ? ['gs_patch_candidate'] : []),
    ...(id === 'publication-session' ? ['gs_read_context'] : [])].sort()
  const body = { contract: APP_TOOLS_SNAPSHOT_CONTRACT, workflow: 'staged', workflow_stage: 'loadout_draft',
    document_id: `document-${id}`, candidate_revision: revision, stage_revision: 0,
    database_fingerprint: 'a'.repeat(64), tool_manifest_hash: 'b'.repeat(64), runtime_generation: 'dsh_v2',
    writer_fence: `writer-${id}`, max_app_calls: MAX_SEQUENTIAL_APP_CALLS,
    tools: names.map(name => ({ type: 'function', name, description: `Probe ${name}`,
      inputSchema: name === 'gs_read_context'
        ? { type: 'object', properties: { resource: { type: 'string' }, request: { type: 'object' } }, required: ['resource'], additionalProperties: false }
        : { type: 'object', properties: {}, additionalProperties: false } })),
    policies: Object.fromEntries(names.map(name => [name, policies[name]])) }
  return { ...body, catalog_hash: hashAppToolsSnapshotBody(body) }
}
let transport
transport = new NativeDshDomainTransport({ async send(packet) {
  const request = JSON.parse(packet.payload)
  let kind, value
  if (packet.payloadKind === 'dsh_app_bind_request') {
    if (request.task_source) taskSources.push(request.task_source)
    kind = 'dsh_app_bind_result'
    if (!snapshots.has(request.session_id)) snapshots.set(request.session_id, snapshot(request.session_id))
    value = { contract: 'genshin-native-dsh-domain-bound-v1', request_id: request.request_id,
      session_id: request.session_id, snapshot: snapshots.get(request.session_id),
      runtime_context: { public_fact: hostFacts.get(request.session_id) ?? 'Read current artifacts', writer_fence: 'NEVER_MODEL_FENCE' },
      prompt_bundle: { system_prompt: 'Host artifact policy sentinel', required_skill_resources: ['SKILL.md'],
        current_focus_card: focusCard(request.session_id) } }
    if (request.session_id === 'task-review-session') value.runtime_context.candidate = {
      task_review: { contract: 'genshin-task-evidence-v1', task_id: request.session_id,
        authority: 'advisory_only', acceptance_effect: 'none', input_fingerprint: 'input',
        execution_intent: 'execute', latest_task_text_fingerprint: createHash('sha256')
          .update(JSON.stringify('Use App scoped tools')).digest('hex'),
        differences: [{ id: 'task-q', summary: '第二轮回能尚待核对', actionable: true }] },
    }
    if (request.session_id === 'bad-advice-session') value.prompt_bundle.current_focus_card.page_hash = 'bad'
    if (request.session_id === 'unbound-session' && modes.get(request.session_id) !== 'bound') {
      value = { ...value, binding_state: 'unbound', snapshot: null,
        binding_issue: { code: 'document_binding_required', message: 'Choose a document', details: {} } }
    }
    if (request.session_id === 'denied-session') {
      kind = 'dsh_app_bind_error'
      value = { request_id: request.request_id, session_id: request.session_id, code: 'binding_denied',
        message: 'Fixture denies unbound Session', details: {}, outcome_unknown: false }
    }
  } else {
    calls.push(request)
    assert.equal(request.raw_arguments_hash, createHash('sha256').update(request.arguments_json).digest('hex'))
    assert.equal(request.document_id, `document-${request.session_id}`)
    assert.equal(request.writer_fence, `writer-${request.session_id}`)
    const selected = snapshots.get(request.session_id)
    assert.equal(request.catalog_hash, selected.catalog_hash)
    kind = 'dsh_app_call_result'
    const next = snapshot(request.session_id, selected.candidate_revision + 1)
    snapshots.set(request.session_id, next)
    value = { contract: 'genshin-native-dsh-domain-result-v1', request_id: request.request_id,
      session_id: request.session_id, call_id: request.call_id, call_seq: request.call_seq,
      status: 'settled_success', result: { accepted: true, revision: next.candidate_revision }, next_snapshot: next }
    if (request.session_id === 'publication-session') {
      const selected = JSON.parse(request.arguments_json).request
      value.result = { code: 0, data: { contract: 'genshin-publication-usage-v1', task_id: 'publication-session',
        scope: selected.scope ?? 'current-task', purpose: selected.purpose,
        question: publicationQuestion, question_prompt: { prefix: '使用', suffix: '？' },
        entities: selected.entities.map(({ type, id }) => ({ entity: { type, id }, name: '测试角色' + id,
          entity_status: 'unknown', question_label: '测试角色' + id + '（目前无法核实）',
          usage_key: type + ':' + id, requires_usage_confirmation: true })) } }
    }
    if (modes.get(request.session_id) === 'unknown') {
      kind = 'dsh_app_call_error'
      value = { request_id: request.request_id, session_id: request.session_id, code: 'host_disconnected_after_claim',
        message: 'Fixture unknown outcome', details: {}, outcome_unknown: true }
    }
    if (modes.get(request.session_id) === 'failure') {
      value.status = 'settled_failure'
      value.result = { code: 1, reason: 'runtime_request_invalid', message: 'request.action is required',
        category: 'request_error', path: '/request/action',
        contract_ref: { resource: 'runtime_contracts', pointer: '/actions' },
        recovery: { action: 'correct_arguments', retry_same_request: false } }
    }
    if (modes.get(request.session_id) === 'system_failure') {
      value.status = 'settled_failure'
      value.result = { code: 1, reason: 'app_component_unavailable', message: '依赖组件不可用',
        failure_kind: 'system', retryable: false,
        system_fault: { contract: 'genshin-app-system-failure-v1', component: 'python_engine_runtime',
          cause_code: 'module_unavailable', operation: request.tool, diagnostic_id: 'a47a2090-670e-41ae-80a5-76dbd65cfe61' },
        recovery: { action: 'report_system_fault', retry_same_request: false } }
    }
    if (['oversized', 'oversized_failure'].includes(modes.get(request.session_id))) {
      value.result = { code: 0, data: 'x'.repeat(MAX_APP_TOOL_RESULT_BYTES + 1) }
      if (modes.get(request.session_id) === 'oversized_failure') value.status = 'settled_failure'
    }
    if (modes.get(request.session_id) === 'contract_changed') {
      next.tools.find(tool => tool.name === 'gs_patch_candidate').description = 'New contract, not published to this step'
      const { catalog_hash: _before, ...changed } = next
      next.catalog_hash = hashAppToolsSnapshotBody(changed)
    }
  }
  transport.accept({ header: { lane: 'remote_unary', payload_kind: kind, correlation_id: packet.correlationId },
    payload: Buffer.from(JSON.stringify(value)) })
} })
let host, peer
async function rpc(endpoint, request, ok = true) {
  const response = await host.handler.fetch(new Request(`https://genshin-shell.invalid/api/session/${endpoint}`, {
    method: 'POST', headers: { 'content-type': 'application/json' },
    body: JSON.stringify({ type: 'client-request', rpcId: randomUUID(), method: `session/${endpoint}`,
      payload: { args: { request } } }),
  }), peer)
  const envelope = await response.json()
  assert.equal(envelope.result.ok, ok, JSON.stringify(envelope.result))
  return envelope.result
}
async function prompt(id) {
  await rpc('prompt', { sessionId: id, requestId: randomUUID(), mode: 'queue', content: [{ type: 'text', text: 'Use App scoped tools' }] })
  await host.context.agents.get(id).whenIdle()
}
const evidence = {}
const actualRequests = []
try {
  globalThis.fetch = createNativeDshDomainFixtureFetch({ calls: [
    { tool: 'gs_app_get_workspace', arguments: {} }, { tool: 'gs_patch_candidate', arguments: {} },
  ], onRequest: request => actualRequests.push(request) })
  host = await createNativeDshSessionHost({ profileRoot: root, mirrorRoot: join(root, 'media'), domainTransport: transport })
  peer = host.createPeer()
  await host.kernel.secretController.set({ provider: 'deepseek-official', credential_generation: 1, secret: 'domain-fixture' })
  await rpc('create', { sessionId: 'scope-one' })
  await rpc('create', { sessionId: 'scope-two' })
  assert.equal(host.context.tools.get('gs_app_get_workspace'), undefined)
  assert.ok(host.context.tools.get('gs_patch_candidate', host.context.agents.get('scope-one')))
  assert.equal(host.context.tools.get('gs_patch_candidate', host.context.agents.get('scope-two')), undefined)
  assert.equal(host.domainTools.snapshot('scope-one').tools.length, 5)
  assert.equal(host.domainTools.snapshot('scope-two').tools.length, 4)
  evidence.perSessionCatalogNoGlobalTools = true
  await rpc('create', { sessionId: 'denied-session' })
  assert.ok(host.context.agents.get('denied-session'))
  assert.equal(host.domainTools.snapshot('denied-session'), null)
  assert.equal(globalThis.fetch.requestCount, 0)
  evidence.firstBindingFailureKeepsGenericChatAndNoDomainAuthority = true
  await rpc('create', { sessionId: 'unbound-session' })
  const unboundAgent = host.context.agents.get('unbound-session')
  assert.equal(host.domainTools.snapshot('unbound-session'), null)
  assert.equal(host.context.tools.get('gs_app_get_workspace', unboundAgent), undefined)
  evidence.explicitUnboundDoesNotBlockGenericChatOrGrantDomainTools = true
  await prompt('scope-one')
  assert.equal(calls.length, 2, JSON.stringify((await host.context.sessionQuery.readSession('scope-one')).events
    .filter(event => event.type === 'turn/end' || event.type === 'llm/failure')))
  assert.equal(host.domainTools.snapshot('scope-one').candidate_revision, 2)
  const log = await host.context.sessionQuery.readSession('scope-one')
  for (const call of calls) {
    const fact = log.events.find(item => item.seq === call.call_seq)
    assert.equal(fact.type, 'tool/call')
    assert.equal(call.turn, fact.data.turn)
    assert.equal(call.step, fact.data.step)
    assert.equal(call.call_id, String(fact.data.callId))
  }
  assert.ok(log.events.some(event => event.type === 'assistant/message'))
  const assembled = await host.context.systemPrompt.assemble({ scope: host.context.agents.get('scope-one') })
  assert.ok(JSON.stringify(assembled).includes('Host artifact policy sentinel'))
  assert.ok(JSON.stringify(assembled).includes('Focus card: Initial loadout guidance'))
  assert.ok(JSON.stringify(assembled).includes('Read current artifacts'))
  assert.equal(JSON.stringify(assembled).includes('NEVER_MODEL_FENCE'), false)
  evidence.scopedAppPolicyAndPublicContextWithoutFence = true
  assert.ok(taskSources.some(item => item.session_id === 'scope-one' && item.message_id
    && Number.isSafeInteger(item.event_seq) && item.text === 'Use App scoped tools'))
  evidence.realUserTextSourceFromDshLog = true
  evidence.actualPublicToolCallFactsAndDynamicSnapshot = true
  await prompt('scope-one')
  assert.notEqual(calls[2].turn, calls[0].turn)
  assert.equal(calls.length, 4)
  evidence.secondTurnUsesNewDshTurn = true
  modes.set('scope-one', 'unknown')
  await prompt('scope-one')
  const unknown = calls.at(-1)
  assert.equal(host.domainTools.canRetryDomainAttempt({ sessionId: unknown.session_id, turn: unknown.turn, step: unknown.step }), false)
  assert.equal(host.domainTools.canRetryDomainAttempt({ sessionId: unknown.session_id, turn: unknown.turn + 1, step: unknown.step }), true)
  evidence.unknownOutcomeDoesNotPoisonOtherTurns = true
  modes.delete('scope-one')
  const nextRevision = snapshots.get('scope-one').candidate_revision + 7
  snapshots.set('scope-one', snapshot('scope-one', nextRevision))
  hostFacts.set('scope-one', 'User approved an external App correction')
  const beforeRefresh = calls.length
  await prompt('scope-one')
  assert.equal(calls[beforeRefresh].candidate_revision, nextRevision)
  const refreshed = await host.context.systemPrompt.assemble({ scope: host.context.agents.get('scope-one') })
  assert.ok(JSON.stringify(refreshed).includes('User approved an external App correction'))
  assert.ok(JSON.stringify(refreshed).includes('Focus card: User approved an external App correction'))
  assert.equal(JSON.stringify(refreshed).includes('Focus card: Initial loadout guidance'), false)
  evidence.currentCardReplacesSameAcceptedPolicyViewWithoutAnotherContextOwner = true
  assert.equal(JSON.stringify(refreshed).includes('NEVER_MODEL_FENCE'), false)
  evidence.externalUserDomainChangeRefreshesDuringAssembly = true
  modes.set('unbound-session', 'bound')
  const beforeAttach = calls.length
  const beforeAttachRequests = actualRequests.length
  await prompt('unbound-session')
  assert.ok(actualRequests[beforeAttachRequests].tools.some(tool => tool.name === 'gs_app_get_workspace'),
    'The first request after binding must publish App tools, without a Web/second-message workaround')
  assert.ok(calls.slice(beforeAttach).some(call => call.session_id === 'unbound-session'
    && call.tool === 'gs_app_get_workspace'))
  assert.ok(host.domainTools.snapshot('unbound-session'))
  evidence.firstBoundProviderRequestHasActualAppSchemas = true
  const scopedAgent = host.context.agents.get('scope-one')
  const admittedAssembly = async (signal = new AbortController().signal) => {
    const toolSet = await host.context.tools.resolve({ agent: scopedAgent,
      modelSelection: scopedAgent.model.current(), signal })
    try { return await host.context.systemPrompt.assemble({ scope: scopedAgent, signal, toolSet }) }
    finally { toolSet.dispose() }
  }
  const definitionBefore = host.context.tools.get('gs_patch_candidate', scopedAgent)
  const stable = await admittedAssembly()
  await admittedAssembly()
  assert.equal(host.context.tools.get('gs_patch_candidate', scopedAgent), definitionBefore)
  hostFacts.set('scope-one', 'Not accepted until another assembly')
  const admissionView = host.context.systemPrompt.refreshContext(stable, { scope: scopedAgent })
  assert.equal(admissionView.contexts.find(item => item.name === 'genshin:app-domain').text,
    stable.contexts.find(item => item.name === 'genshin:app-domain').text)
  const source = snapshots.get('scope-one')
  const { catalog_hash: _previousHash, ...removed } = source
  removed.tools = removed.tools.filter(tool => tool.name !== 'gs_patch_candidate')
  removed.policies = Object.fromEntries(Object.entries(removed.policies).filter(([name]) => name !== 'gs_patch_candidate'))
  snapshots.set('scope-one', { ...removed, catalog_hash: hashAppToolsSnapshotBody(removed) })
  const without = await admittedAssembly()
  assert.equal(without.tools.some(tool => tool.name === 'gs_patch_candidate'), false)
  assert.equal(host.context.tools.get('gs_patch_candidate', scopedAgent), undefined)
  const genericNames = stable.tools.filter(tool => !tool.name.startsWith('gs_')).map(tool => tool.name)
  assert.ok(genericNames.length > 0)
  assert.deepEqual(without.tools.filter(tool => !tool.name.startsWith('gs_')).map(tool => tool.name), genericNames)
  assert.ok(host.context.tools.get('gs_app_get_workspace', host.context.agents.get('scope-two')))
  const cancelled = new AbortController()
  cancelled.abort()
  await assert.rejects(admittedAssembly(cancelled.signal))
  snapshots.set('scope-one', source)
  await admittedAssembly()
  assert.ok(host.context.tools.get('gs_patch_candidate', scopedAgent))
  evidence.immutableAdmissionViewAndSameHashRegistration = true
  evidence.removalAndCancelledAssemblyPreserveOtherScopesAndGenericTools = true
  modes.set('scope-one', 'failure')
  await prompt('scope-one')
  const failureLog = await host.context.sessionQuery.readSession('scope-one')
  assert.ok(failureLog.events.some(item => item.type === 'tool/result'
    && JSON.stringify(item.data).includes('runtime_request_invalid')
    && JSON.stringify(item.data).includes('"isError":true')))
  evidence.structuredDomainFailureRemainsDshToolError = true
  modes.set('scope-one', 'system_failure')
  await prompt('scope-one')
  const faults = (await host.context.sessionQuery.readSession('scope-one')).events.filter(event =>
    event.type === 'tool/result' && JSON.stringify(event.data).includes('report_system_fault'))
  assert.ok(faults.length > 0)
  assert.ok(faults.every(event => JSON.stringify(event.data).includes('"isError":true')))
  assert.equal(faults.some(event => JSON.stringify(event.data).includes('correct_arguments')), false)
  evidence.systemFaultUsesDshErrorChannelWithOriginalRecovery = true
  modes.set('scope-one', 'oversized')
  await prompt('scope-one')
  const oversizedLog = await host.context.sessionQuery.readSession('scope-one')
  assert.ok(oversizedLog.events.some(item => item.type === 'tool/result'
    && JSON.stringify(item.data).includes('domain_tool_result_requires_resource_projection')
    && JSON.stringify(item.data).includes('settled_success')
    && JSON.stringify(item.data).includes('read_resource')))
  evidence.completedOperationWithOversizedProjectionIsNotUnknownOrRetryable = true
  modes.set('scope-one', 'oversized_failure')
  await prompt('scope-one')
  const failedProjection = await host.context.sessionQuery.readSession('scope-one')
  assert.ok(failedProjection.events.some(item => item.type === 'tool/result'
    && JSON.stringify(item.data).includes('domain_tool_result_requires_resource_projection')
    && JSON.stringify(item.data).includes('settled_failure')
    && JSON.stringify(item.data).includes('diagnostic_error')))
  modes.set('scope-one', 'contract_changed')
  globalThis.fetch = createNativeDshDomainFixtureFetch({ calls: [[
    { tool: 'gs_patch_candidate', arguments: {} }, { tool: 'gs_patch_candidate', arguments: {} },
  ]] })
  const contractCalls = calls.length
  await prompt('scope-one')
  assert.equal(calls.length, contractCalls + 1, 'Second exclusive tool must not use an unpublished changed contract')
  const changedLog = await host.context.sessionQuery.readSession('scope-one')
  assert.ok(changedLog.events.some(item => item.type === 'tool/result'
    && JSON.stringify(item.data).includes('stale_tool_snapshot')
    && JSON.stringify(item.data).includes('did_not_execute')))
  evidence.changedSameNameToolContractCannotExecuteUnderUnpublishedSchema = true
  const appSnapshots = event => event.type === 'user/message'
    && event.data.source?.kind === 'runtime-context'
    && event.data.source.sections?.some(section => section.name === 'genshin:app-domain')
  const currentSurface = await host.context.sessionQuery.readSurface('scope-one')
  assert.ok(currentSurface.events.filter(appSnapshots).length <= 1,
    'The model surface must retain at most one current App snapshot')
  assert.ok(changedLog.events.filter(appSnapshots).length > 3,
    'The raw DSH log must retain original App facts for diagnosis')
  assert.ok(changedLog.events.some(event => event.type === 'user/message'
    && typeof event.surfaceOp === 'object' && event.sourceEventSeqs?.length === 1))
  evidence.publicAppContextProjectionPreservesRawEvidence = true
  globalThis.fetch = createNativeDshDomainFixtureFetch({ calls: [] })
  await prompt('denied-session')
  const generic = await host.context.sessionQuery.readSession('denied-session')
  assert.ok(generic.events.some(item => item.type === 'assistant/message'))
  assert.equal(calls.some(item => item.session_id === 'denied-session'), false)
  evidence.genericPromptWorksAfterInitialDomainBindingFailure = true
  await rpc('create', { sessionId: 'bad-advice-session' })
  assert.ok(host.domainTools.snapshot('bad-advice-session'))
  assert.ok(host.context.tools.get('gs_app_get_workspace', host.context.agents.get('bad-advice-session')))
  evidence.optionalFocusFaultKeepsValidDomainTools = true
  globalThis.fetch = createNativeDshDomainFixtureFetch({ calls: [{ tool: 'gs_patch_candidate', arguments: {} }], repeatCalls: false })
  await rpc('create', { sessionId: 'task-review-session' })
  await prompt('task-review-session')
  assert.equal(globalThis.fetch.requestCount, 3, 'one ordinary response then one public steer, no private loop')
  const reviewLog = await host.context.sessionQuery.readSession('task-review-session')
  assert.equal(reviewLog.events.filter(event => event.type === 'user/message'
    && event.data.source?.kind === 'genshin-task-review').length, 1)
  assert.equal(reviewLog.events.filter(event => event.type === 'turn/start').length, 1)
  assert.equal(reviewLog.events.filter(event => event.type === 'turn/end').length, 1)
  const reviewStatus = (await host.sessionStatus('task-review-session')).task_review_plugin
  assert.ok(reviewStatus.task_review_loaded)
  assert.equal(reviewStatus.task_review_binding.binding, 'bound')
  assert.ok(reviewStatus.task_review_domain_context_assembled)
  assert.equal(reviewStatus.task_review_stop_notice.turn, 1)
  assert.ok(reviewLog.events.filter(event => event.type === 'assistant/message').length >= 2)
  assert.ok(taskSources.filter(item => item.session_id === 'task-review-session').every(item => item.text === 'Use App scoped tools'))
  evidence.softReviewUsesOneDshTurnAndKeepsOriginalReply = true
  await rpc('create', { sessionId: 'publication-session' })
  const publicationAgent = host.context.agents.get('publication-session')
  let presentedQuestion
  publicationAgent.ctx.on('user-questions/request', request => {
    assert.equal(request.questions.length, 1)
    presentedQuestion = request.questions[0]
    return Promise.resolve({ answers: [{ id: presentedQuestion.id, selected: ['同意本任务使用'] }] })
  }, { prepend: true })
  globalThis.fetch = createNativeDshDomainFixtureFetch({ calls: [{ tool: 'gs_read_context', arguments: publicationQuery }],
    repeatCalls: false })
  await prompt('publication-session')
  const usageReads = async () => (await host.context.sessionQuery.readSession('publication-session')).events
    .filter(event => event.type === 'tool/result' && !event.data.message.isError)
    .map(event => {
      try { return JSON.parse(event.data.message.content.find(item => item.type === 'text')?.text)?.data }
      catch { return null }
    }).filter(data => data?.contract === 'genshin-publication-usage-v1')
  const firstNotice = (await usageReads()).at(-1)
  assert.equal(firstNotice.entities[0].response?.status, 'ask_user', JSON.stringify(firstNotice))
  verifyPublicationAnswerBoundaries(firstNotice)
  globalThis.fetch = createNativeDshDomainFixtureFetch({ calls: [
    { tool: 'ask_user_question', arguments: { questions: [firstNotice.question] } },
    { tool: 'gs_read_context', arguments: { ...publicationQuery,
      request: { ...publicationQuery.request, entities: [{ type: 'character', id: 2 }, { type: 'weapon', id: 3 }] } } },
  ], repeatCalls: false })
  await prompt('publication-session')
  assert.equal(presentedQuestion.id, firstNotice.question.id)
  const subsequentNotice = (await usageReads()).at(-1)
  assert.equal(subsequentNotice.entities[0].response.status, 'confirmed')
  assert.ok(subsequentNotice.entities[0].response.call_id)
  assert.equal(subsequentNotice.entities[1].response.status, 'ask_user')
  assert.match(subsequentNotice.question.question, /测试角色3/)
  assert.doesNotMatch(subsequentNotice.question.question, /测试角色2/)
  evidence.publicationUsageReusesActualDshAnswerAndOnlyAsksForExpansion = true
  globalThis.fetch = createNativeDshDomainFixtureFetch({ calls: [] })
  await host.kernel.flushSessions()
  const beforeCold = await host.context.sessionQuery.readSurface('scope-one')
  await peer.dispose()
  await host.dispose()
  host = await createNativeDshSessionHost({ profileRoot: root, mirrorRoot: join(root, 'media'), domainTransport: transport })
  peer = host.createPeer()
  const restoredSurface = await host.context.sessionQuery.readSurface('scope-one')
  assert.deepEqual(restoredSurface.events, beforeCold.events)
  await host.kernel.secretController.set({ provider: 'deepseek-official', credential_generation: 1, secret: 'domain-fixture' })
  globalThis.fetch = createNativeDshDomainFixtureFetch({ calls: [{ tool: 'gs_read_context', arguments: publicationQuery }],
    repeatCalls: false })
  await prompt('publication-session')
  const resumedNotice = (await usageReads()).at(-1)
  assert.equal(resumedNotice.entities[0].response.status, 'confirmed')
  assert.equal(resumedNotice.question, null)
  evidence.publicationUsageKeepsDeclinesPendingAndForksSeparate = true
  globalThis.fetch = createNativeDshDomainFixtureFetch({ calls: [] })
  await prompt('scope-one')
  const resumedSurface = await host.context.sessionQuery.readSurface('scope-one')
  assert.equal(resumedSurface.events.filter(appSnapshots).length, 1)
  assert.ok(resumedSurface.events.some(event => event.type === 'tool/result'))
  evidence.publicAppContextProjectionColdHostResume = true
  console.log(JSON.stringify({ contract: 'genshin-native-dsh-domain-tools-probe-v1', status: 'passed', ...evidence }))
} finally {
  transport.close()
  await peer?.dispose()
  await host?.dispose()
  globalThis.fetch = originalFetch
  await rm(root, { recursive: true, force: true })
}
