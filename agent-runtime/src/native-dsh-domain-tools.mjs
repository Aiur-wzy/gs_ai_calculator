/** Scoped App catalog over public DSH agent creation and tool execution facts. */
import { createHash } from 'node:crypto'
import { symbols } from '@deepseek-ai/cordis'
import { compileAppToolDefinitions, validateAppToolsSnapshot, MAX_APP_TOOL_RESULT_BYTES } from './native-app-tool-catalog.mjs'
import { NativeDshDomainError } from './native-dsh-domain-transport.mjs'
import { nativeAppToolWireSchemas } from './native-app-tool-schema.mjs'
import { canonicalEnvelopeJson } from './bridge-envelope.mjs'
import { validateAppSystemFailure } from './native-app-system-failure.mjs'
import { captureNativeInputSnapshot } from './native-dsh-input-snapshot.mjs'
import { projectCurrentAppContext } from './native-dsh-app-context-projection.mjs'
import { withPublicationAnswers } from './publication-usage.mjs'

const TERMINAL = new Set(['settled_success', 'settled_failure', 'unknown_after_interruption'])
const IDENTITY = ['document_id', 'database_fingerprint', 'runtime_generation', 'writer_fence']
const PRIVATE_CONTEXT_KEYS = new Set(['writer_fence', 'fence_token', 'catalog_hash', 'tool_manifest_hash',
  'secret', 'api_key', 'access_token', 'credential', 'credentials'])
function publicContext(value) {
  if (Array.isArray(value)) return value.map(publicContext)
  if (value !== null && typeof value === 'object') return Object.fromEntries(Object.entries(value)
    .filter(([name]) => !PRIVATE_CONTEXT_KEYS.has(name.toLowerCase()))
    .map(([name, child]) => [name, publicContext(child)]))
  return value
}
const toolContractHash = (tool, policy) => createHash('sha256')
  .update(canonicalEnvelopeJson({ tool, policy })).digest('hex')
const FALLBACK_POLICY = '你是原神拉表App的中文专用Agent。使用实际发布的工具；工作重点只是阅读建议，不能代替输入完整性、通用规则校验、用户领域批准或正式计算接纳。每轮结束给出用户可读答复，不能用工具记录或裸结构替代。'

function exposeDomainFailure(error, tool) {
  if (!(error instanceof NativeDshDomainError)) return error
  const details = publicContext(error.details ?? {})
  const admission = details.app_call_admission ?? (error.outcomeUnknown ? 'unknown_after_interruption' : 'unknown')
  const payload = { ...details, code: error.code, message: error.message,
    app_call_admission: admission,
    execution_state: details.execution_state ?? (error.outcomeUnknown ? 'unknown_after_interruption'
      : admission === 'not_recorded' ? 'did_not_execute' : admission),
    operation_may_have_completed: details.operation_may_have_completed ?? (error.outcomeUnknown || admission === 'settled_success'),
    contract_ref: details.contract_ref ?? { resource: 'tool_contracts', pointer: `/tools/${tool}` },
    recovery: details.recovery ?? { action: error.outcomeUnknown ? 'inspect_settlement'
      : admission === 'not_recorded' ? 'refresh_binding' : 'correct_arguments', retry_same_request: false } }
  // DSH's public error channel renders Error.message and preserves isError.
  // Returning this envelope normally would incorrectly report tool success.
  error.message = JSON.stringify(payload)
  return error
}

export function mountNativeDshDomainTools(ctx, { transport }) {
  if (typeof transport?.bindSession !== 'function' || typeof transport?.execute !== 'function') {
    throw new TypeError('domain_transport_required')
  }
  const sessions = new Map()
  // rc.3 freezes registrations in this public entry before assembling prompt
  // text. Refresh the App-owned catalog first, then delegate the entire tool
  // admission/execution lifecycle to the official implementation. There is no
  // awaitable pre-resolve event in this release; do not patch its private loop.
  const registry = ctx.tools[symbols.original] ?? ctx.tools
  const originalResolve = registry.resolve
  const resolveWithDomain = async function (context) {
    const scoped = sessions.get(context.agent.id)
    if (scoped) await scoped.prepare(context.signal)
    context.signal.throwIfAborted()
    return originalResolve.call(this, context)
  }
  registry.resolve = resolveWithDomain
  ctx.effect(() => () => {
    if (registry.resolve === resolveWithDomain) registry.resolve = originalResolve
  }, 'App: refresh domain before official tool admission')
  const knownUnknown = new Set()
  const pendingCalls = new Map()
  const key = (sessionId, turn, step) => JSON.stringify([sessionId, turn, step])

  const created = ctx.on('agent/created', async ({ agent, signal }) => {
    let bindingState = 'unbound'
    let bindingIssue = null
    let snapshot = null
    let disposeTools = []
    const scope = agent.ctx
    let runtimeContext = {}
    let promptBundle
    let published = null
    let registeredHash = null
    let assembling = false
    let activity = 0
    let adviceIssue = null
    const policyFor = bundle => {
      if (bundle === undefined) return FALLBACK_POLICY
      if (typeof bundle.system_prompt !== 'string' || bundle.system_prompt.length === 0
          || !Array.isArray(bundle.required_skill_resources)
          || bundle.required_skill_resources.some(name => typeof name !== 'string'
            || !/^(?:SKILL\.md|references\/[A-Za-z0-9_.\/-]+\.md)$/u.test(name) || name.split('/').includes('..'))) {
        throw new NativeDshDomainError('domain_prompt_bundle_invalid')
      }
      let text = bundle.system_prompt
        + '\n首次需要时按需读取以下捆绑规范：' + bundle.required_skill_resources.join('、')
      const card = bundle.current_focus_card
      adviceIssue = null
      if (card !== undefined && card !== null) {
        if (typeof card.resource !== 'string' || !/^references\/workflow\/focus\/[a-z]+\.md$/u.test(card.resource)
            || typeof card.text !== 'string' || typeof card.focus !== 'string'
            || card.focus_is_authorization !== false
            || !/^[0-9a-f]{64}$/u.test(card.content_hash)
            || createHash('sha256').update(card.text, 'utf8').digest('hex') !== card.page_hash
            || card.next_offset !== null && (!Number.isSafeInteger(card.next_offset) || card.next_offset < 0)) {
          // A reading suggestion has no authority. Keep the valid policy and
          // actual tool catalog available when this optional projection fails.
          adviceIssue = { code: 'domain_focus_card_invalid', message: '当前阅读提示暂不可用；可按需读取Skill，编辑与计算工具仍可使用。' }
          return text
        }
        adviceIssue = null
        text += `\n当前工作重点提示（不是权限门；仅本张卡已投影，主题资料仍按需读取）：${card.resource} @ ${card.content_hash}\n`
          + card.text
        if (card.next_offset !== null) text += `\n本卡按实际响应字节分页，后文可按需用gs_read_skill offset=${card.next_offset}读取。`
      }
      return text
    }
    scope.systemPrompt.section({ name: 'genshin:app-policy', order: 100,
      text: () => published?.policy ?? FALLBACK_POLICY, interpolate: false })
    scope.systemPrompt.context({ name: 'genshin:app-domain', order: 300, required: true, interpolate: false,
      // Admission refreshContext() must see the same accepted view as tools.
      text: () => published?.context ?? '{}',
    })
    const contextText = () => {
      const context = structuredClone(runtimeContext)
      // Actual schemas are published with this same assembly. Do not repeat
      // their names in three separate context sections on every model step.
      if (context.tool_capabilities?.domain_tools && context.allowed_domain_tools) {
        delete context.tool_capabilities.domain_tools
      }
      return JSON.stringify({
        binding_state: bindingState,
        binding_issue: publicContext(bindingIssue),
        advice_issue: adviceIssue,
        app_context: context,
        current_candidate: snapshot === null ? null : {
          workflow: snapshot.workflow, document_id: snapshot.document_id,
          candidate_revision: snapshot.candidate_revision, stage_revision: snapshot.stage_revision,
          database_fingerprint: snapshot.database_fingerprint,
          ...(context.allowed_domain_tools ? {} : { allowed_domain_tools: snapshot.tools.map(tool => tool.name) }),
        },
      })
    }
    const clearTools = () => {
      disposeTools.forEach(dispose => dispose())
      disposeTools = []
      registeredHash = null
      snapshot = null
    }
    const install = (incoming, { freshBinding = false } = {}) => {
      const next = validateAppToolsSnapshot(incoming)
      if (snapshot !== null && IDENTITY.some(field => next[field] !== snapshot[field])) {
        throw new NativeDshDomainError('domain_snapshot_binding_changed')
      }
      // Concurrent pure reads can settle out of order. Never roll a live
      // catalog back to a stale candidate, and reject divergent same-revision.
      if (snapshot !== null && (next.stage_revision < snapshot.stage_revision
          || next.candidate_revision < snapshot.candidate_revision)) {
        if (freshBinding) throw new NativeDshDomainError('domain_snapshot_rollback')
        return
      }
      if (!freshBinding && snapshot !== null && next.stage_revision === snapshot.stage_revision
          && next.candidate_revision === snapshot.candidate_revision
          && next.catalog_hash !== snapshot.catalog_hash) {
        throw new NativeDshDomainError('domain_snapshot_same_revision_conflict')
      }
      if (next.catalog_hash === snapshot?.catalog_hash) return
      snapshot = next
    }
    const refresh = incoming => {
      const state = incoming.binding_state ?? 'bound'
      if (!['bound', 'read_only', 'unbound'].includes(state)
          || state === 'unbound' && incoming.snapshot !== null) {
        throw new NativeDshDomainError('domain_binding_state_invalid')
      }
      if (state === 'unbound') clearTools()
      else install(incoming.snapshot, { freshBinding: true })
      bindingState = state
      bindingIssue = incoming.binding_issue ?? null
      runtimeContext = publicContext(incoming.runtime_context ?? {})
      promptBundle = incoming.prompt_bundle
      policyFor(promptBundle)
    }
    const invoke = async (tool, args, exec) => {
      if (exec.agent?.id !== agent.id) throw new NativeDshDomainError('domain_tool_scope_invalid')
      const observation = await ctx.sessionQuery.observeSession(agent.id, {
        signal: exec.signal, projectionMode: 'none',
      })
      let event
      try {
        event = observation.events.findLast(item => item.type === 'tool/call'
          && String(item.data.callId) === String(exec.callId) && item.data.name === tool)
      } finally { observation[Symbol.dispose]() }
      if (event === undefined) throw new NativeDshDomainError('domain_tool_call_fact_missing')
      const { turn, step } = event.data
      const attemptKey = key(agent.id, turn, step)
      const selected = snapshot
      if (selected === null) throw new NativeDshDomainError('domain_binding_unavailable')
      if (!published?.names.includes(tool)) throw new NativeDshDomainError('domain_tool_not_published')
      const selectedDefinition = selected.tools.find(row => row.name === tool)
      if (selectedDefinition === undefined || published.contractHashes[tool] !==
          toolContractHash(selectedDefinition, selected.policies[tool])) {
        throw exposeDomainFailure(new NativeDshDomainError('stale_tool_snapshot',
          '工具合同已变化，本次调用未执行；请在下一步重新读取当前工具。',
          { details: { app_call_admission: 'not_recorded', execution_state: 'did_not_execute',
            recovery: { action: 'refresh_binding', retry_same_request: false } } }), tool)
      }
      pendingCalls.set(attemptKey, (pendingCalls.get(attemptKey) ?? 0) + 1)
      // One encoding becomes the ledger's exact raw argument identity.
      const argumentsJson = JSON.stringify(args)
      try {
        const inputSnapshot = await captureNativeInputSnapshot(ctx, tool, args, exec)
        const response = await transport.execute({ session_id: agent.id,
          turn, step, call_id: String(exec.callId), call_seq: event.seq, tool,
          arguments_json: argumentsJson,
          raw_arguments_hash: createHash('sha256').update(argumentsJson, 'utf8').digest('hex'),
          ...(inputSnapshot === undefined ? {} : { input_file_snapshot: inputSnapshot }),
          ...Object.fromEntries(['catalog_hash', 'tool_manifest_hash', 'candidate_revision',
            'stage_revision', ...IDENTITY].map(field => [field, selected[field]])),
        }, exec.signal)
        if (response.call_id !== String(exec.callId) || response.call_seq !== event.seq
            || !TERMINAL.has(response.status) || !Object.hasOwn(response, 'result')) {
          throw new NativeDshDomainError('domain_tool_settlement_invalid', undefined, { outcomeUnknown: true })
        }
        if (response.status === 'unknown_after_interruption') {
          throw new NativeDshDomainError('domain_tool_outcome_unknown', undefined, { outcomeUnknown: true })
        }
        if (response.next_snapshot !== null && response.next_snapshot !== undefined) install(response.next_snapshot)
        if (response.runtime_context !== undefined) runtimeContext = publicContext(response.runtime_context)
        if (response.prompt_bundle !== undefined) { policyFor(response.prompt_bundle); promptBundle = response.prompt_bundle }
        if (Buffer.byteLength(JSON.stringify(response.result), 'utf8') > MAX_APP_TOOL_RESULT_BYTES) {
          // The operation is durably settled; only its model-facing projection
          // is rejected. Keep the current snapshot and never replay the write.
          const succeeded = response.status === 'settled_success'
          throw new NativeDshDomainError('domain_tool_result_requires_resource_projection',
            succeeded ? 'App操作已成功结算，但返回过大；读取当前候选或资料，不要重复执行该操作。'
              : 'App操作已失败结算，但诊断返回过大；读取当前候选和诊断，不要重复提交旧请求。',
            { details: { app_call_admission: response.status, execution_state: response.status,
              operation_may_have_completed: succeeded, retryable: false,
              recovery: { action: 'read_resource', resource: succeeded ? 'candidate' : 'diagnostic_error', retry_same_request: false } } })
        }
        if (response.status === 'settled_failure') {
          validateAppSystemFailure(response.result)
          throw new NativeDshDomainError(response.result.reason ?? 'domain_tool_failed',
            response.result.message ?? 'App操作未完成，请按具体错误修正。',
            { details: { ...response.result, app_call_admission: 'settled_failure', execution_state: 'settled_failure' } })
        }
        if (selected.policies[tool]?.effect === 'candidate'
            || ['gs_compose', 'gs_calculate', 'gs_record_finding'].includes(tool)) activity += 1
        if (tool === 'gs_read_context' && args.resource === 'publication_usage') {
          try {
            const facts = await ctx.sessionQuery.observeSession(agent.id, { signal: exec.signal, projectionMode: 'none' })
            try { return withPublicationAnswers(response.result, facts) }
            finally { facts[Symbol.dispose]() }
          } catch (error) {
            exec.signal?.throwIfAborted()
            ctx.logger?.debug?.('publication usage answer history unavailable: %s', error.code ?? error.name)
            return { ...response.result, data: { ...response.result.data,
              decision_source: 'dsh_user_question_history_unavailable', question: null,
              next_step: '暂时无法核对已有的用户答复。先检查会话已有提问和回复，再采用该批资料；无关工作可以继续。' } }
          }
        }
        return response.result
      } catch (error) {
        if (error.outcomeUnknown === true) knownUnknown.add(attemptKey)
        throw exposeDomainFailure(error, tool)
      } finally {
        const remaining = pendingCalls.get(attemptKey) - 1
        if (remaining === 0) pendingCalls.delete(attemptKey)
        else pendingCalls.set(attemptKey, remaining)
      }
    }
    const publish = () => {
      const definitions = snapshot === null ? [] : compileAppToolDefinitions(snapshot, invoke)
      const schemas = nativeAppToolWireSchemas(definitions)
      if (registeredHash !== snapshot?.catalog_hash) {
        disposeTools.forEach(dispose => dispose())
        disposeTools = []
        try {
          for (const definition of definitions) {
            disposeTools.push(scope.tools.register(definition))
          }
        } catch (error) {
          clearTools()
          throw error
        }
        registeredHash = snapshot?.catalog_hash ?? null
      }
      const context = contextText()
      const policy = policyFor(promptBundle)
      published = Object.freeze({ context, policy, names: Object.freeze(schemas.map(item => item.name)),
        schemas: Object.freeze(schemas),
        contractHashes: Object.freeze(Object.fromEntries((snapshot?.tools ?? []).map(row =>
          [row.name, toolContractHash(row, snapshot.policies[row.name])]))),
        viewHash: createHash('sha256').update(JSON.stringify({ context, policy, schemas })).digest('hex') })
      return published
    }
    const refreshPublished = async requestSignal => {
      try {
        refresh(await transport.bindSession(agent.id, requestSignal))
        requestSignal?.throwIfAborted()
        return publish()
      } catch (error) {
        requestSignal?.throwIfAborted()
        clearTools()
        bindingState = 'unbound'
        const detail = publicContext(error.details ?? {})
        const programFault = detail.failure_kind === 'system' && validateAppSystemFailure(detail)
        bindingIssue = { ...(programFault ? detail : {}), code: error.code ?? 'domain_refresh_failed',
          message: programFault ? detail.message : 'App领域资料刷新失败；可继续普通对话，不能编辑或计算。' }
        runtimeContext = {}
        promptBundle = undefined
        return publish()
      }
    }
    await refreshPublished(signal)
    // The presentation waterfall owns text only in rc.3. Definitions already
    // passed through official resolve() above, so never replace its schemas.
    scope.on('system-prompt/assemble', async (_assembly, context, next) => {
      if (assembling) throw new NativeDshDomainError('domain_assembly_reentrant')
      assembling = true
      try {
        context.signal?.throwIfAborted()
        const assembly = await next()
        const view = context.toolSet === undefined ? await refreshPublished(context.signal) : published
        context.signal?.throwIfAborted()
        await projectCurrentAppContext(ctx, agent, view.context, context.signal)
        return { ...assembly,
          sections: assembly.sections.map(section => section.name === 'genshin:app-policy'
            ? { ...section, text: view.policy } : section),
          contexts: assembly.contexts.map(entry => entry.name === 'genshin:app-domain'
            ? { ...entry, text: view.context } : entry),
        }
      } finally {
        assembling = false
      }
    }, { prepend: true })
    sessions.set(agent.id, { prepare: refreshPublished, get snapshot() { return structuredClone(snapshot) },
      get advice() { return { binding: bindingState, activity,
        review: structuredClone(runtimeContext.task_review ?? runtimeContext.candidate?.task_review
          ?? runtimeContext.feedback_context?.task_review ?? null),
        waitingForUser: runtimeContext.task_action_projection === 'unavailable'
          || runtimeContext.waiting_for_user === true || runtimeContext.pending_user_approval === true } },
      get bindingState() { return bindingState }, get viewHash() { return published?.viewHash ?? null } })
    scope.effect(() => () => {
      disposeTools.forEach(dispose => dispose())
      sessions.delete(agent.id)
    }, 'App domain tool catalog')
  }, { prepend: true })
  return Object.freeze({
    canRetryDomainAttempt: ({ sessionId, turn, step }) => {
      const selected = key(sessionId, turn, step)
      return !knownUnknown.has(selected) && !pendingCalls.has(selected)
    },
    snapshot: sessionId => sessions.get(sessionId)?.snapshot ?? null,
    advisorySnapshot: sessionId => sessions.get(sessionId)?.advice ?? null,
    dispose: created,
  })
}
