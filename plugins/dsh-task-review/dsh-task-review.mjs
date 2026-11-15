/** Domain advice on public DSH lifecycle seams; never a second agent driver. */
import { createHash, randomUUID } from 'node:crypto'

export const TASK_REVIEW_SOURCE = 'genshin-task-review'
export const TASK_REVIEW_VERSION = '0.2.0'
const object = value => value && typeof value === 'object' && !Array.isArray(value) ? value : null
const stable = value => JSON.stringify(value, (_key, item) => object(item)
  ? Object.fromEntries(Object.entries(item).sort(([a], [b]) => a.localeCompare(b))) : item)

export function taskReview(value) {
  const row = object(value)
  if (row?.contract !== 'genshin-task-evidence-v1' || row.authority !== 'advisory_only'
      || row.acceptance_effect !== 'none' || typeof row.task_id !== 'string' || !row.task_id
      || !Array.isArray(row.differences)) return null
  return { ...row, differences: row.differences.filter(item => object(item)
    && typeof item.id === 'string' && typeof item.summary === 'string') }
}

/** Revisions, repeated reads and log order are not progress. */
export function taskReviewKey(value) {
  const row = taskReview(value)
  if (!row) return null
  return createHash('sha256').update(stable({ task: row.task_id, source: row.source_version ?? null,
    differences: row.differences.filter(item => item.actionable === true)
      .map(({ id, summary, recovery }) => ({ id, summary, recovery }))
      .sort((a, b) => a.id.localeCompare(b.id)),
  })).digest('hex')
}

export function taskReviewText(value, { cli = false } = {}) {
  const row = taskReview(value)
  if (!row) return ''
  const suggestions = row.differences.filter(item => item.actionable === true).slice(0, 12)
  if (!suggestions.length) return ''
  return '任务对照提醒（只供复核，不改变计算接纳、用户授权或任务范围）：\n'
    + suggestions.map(item => {
      const recovery = object(item.recovery)
      const next = typeof item.recovery === 'string' ? item.recovery
        : recovery ? (cli ? recovery.cli : recovery.tool) ?? recovery.instruction : undefined
      return `- ${item.summary}${next ? '\n  可用下一步：' + next : ''}`
    }).join('\n')
    + '\n先核对这些差异是否确实属于用户要求。仍属已授权工作且可处理时继续完善；'
    + '若只是问答、用户已经暂停或换题、确需用户批准、或本项不适用，则直接如实回复，不要为了消除提醒修改用户要求。'
    + '引擎接受只证明已提交输入；不要将未观察的游戏机制说成已完成。最后用玩家能理解的话说明完成部分、近似/缺项和用户下一步。'
}

/**
 * port.read returns {review, binding?, activity?, waitingForUser?}. It only reads
 * current App facts. An unavailable auxiliary port does not revoke core tools.
 * Official CLI binds a plan explicitly; ordinary CLI has no lifecycle hook.
 */
export function mountTaskReview(ctx, { port, context = false, cli = false, eligible = () => true,
  onDiagnostic = () => {}, record = () => {} }) {
  if (typeof port?.read !== 'function') throw new TypeError('task_review_port_required')
  return ctx.on('agent/created', async ({ agent }) => {
    if (!eligible(agent)) return
    const scope = agent.ctx
    const seen = new Set()
    let currentTurn = null, baseline = null, current = null, epoch = 0, turnEpoch = 0
    let remindedTurn = null, turnText = null, turnMessage = null
    let pendingApprovalCount = 0, disposed = false
    const pendingUserTools = new Set()
    let bindingState = null, readState = null
    const diagnose = (code, details = {}) => {
      const row = { sessionId: agent.id, version: TASK_REVIEW_VERSION, code, turn: currentTurn, ...details }
      try { onDiagnostic(row) } catch { /* Diagnostic reporting is advisory. */ }
      try { record(agent, row) } catch { /* Failed logging must not change DSH activity. */ }
    }
    diagnose('task_review_loaded')
    const read = async signal => {
      try {
        const result = await port.read(agent, signal)
        signal?.throwIfAborted()
        const binding = result?.binding ?? 'unavailable'
        if (binding !== bindingState) {
          bindingState = binding
          diagnose('task_review_binding', { binding })
        }
        const valid = result && taskReview(result.review)
        const readKey = valid ? result.review.source_version ?? result.review.task_id : 'unavailable'
        if (readKey !== readState) {
          readState = readKey
          diagnose('task_review_read', { binding, available: Boolean(valid) })
        }
        return valid ? result : null
      } catch (error) {
        signal?.throwIfAborted()
        diagnose('task_review_unavailable', { reason: error.code ?? error.name ?? 'Error' })
        return null
      }
    }
    scope.on('agent/inbox/inserted', ({ message }) => {
      if (message.source?.kind === 'user') epoch += 1
    })
    scope.on('tools/result', (exec, result) => {
      if (!['ask_user_question', 'gs_request_public_data_patch_approval', 'gs_app_request_action'].includes(exec.name)
          || result?.isError !== false) return
      const value = object(result.value)
      // A failed request or an already answered/completed operation is not a
      // pending user action. Observe the canonical outcome, never the tool name.
      if (value?.pending === true || ['queued_for_user', 'waiting_for_user'].includes(value?.status)) {
        pendingUserTools.add(exec.name)
      } else pendingUserTools.delete(exec.name)
    })
    scope.on('approval/request', async (_request, next) => {
      pendingApprovalCount += 1
      try { return await next() } finally { pendingApprovalCount -= 1 }
    })
    scope.on('agent/pre-step', async (payload, next) => {
      if (currentTurn !== payload.turn) {
        currentTurn = payload.turn
        turnEpoch = epoch
        const latest = (payload.messages ?? []).findLast(message => message.source?.kind === 'user')
        const message = latest?.content.filter(block => block.type === 'text').map(block => block.text).join('\n')
          .replaceAll('\r\n', '\n').replace(/^[ \t\n]+|[ \t\n]+$/gu, '')
        turnText = message ? createHash('sha256').update(JSON.stringify(message)).digest('hex') : null
        turnMessage = latest?.id ?? turnText
        pendingUserTools.clear()
        baseline = await read(payload.signal)
        current = baseline
      }
      return next()
    })
    if (context) {
      scope.systemPrompt.context({ name: 'genshin:task-evidence', order: 310, required: false,
        interpolate: false, text: () => current?.review ? taskReviewText(current.review, { cli }) : '' })
      scope.on('system-prompt/assemble', async (_value, assembly, next) => {
        current = await read(assembly.signal)
        const assembled = await next()
        if (assembled?.contexts?.some(entry => entry.name === 'genshin:task-evidence' && entry.text)) {
          diagnose('task_review_context_assembled', { projection: taskReviewKey(current?.review) })
        }
        return assembled
      })
    } else {
      // The native domain provider assembles this context itself. Observe the
      // same public waterfall without re-reading or rewriting that context.
      scope.on('system-prompt/assemble', async (_value, _assembly, next) => {
        const assembled = await next()
        if (assembled?.contexts?.some(entry => entry.name === 'genshin:app-domain' && entry.text)) {
          diagnose('task_review_domain_context_assembled')
        }
        return assembled
      }, { prepend: true })
    }
    // Registered after research accounting in the native composition. Each
    // listener is awaited by DSH before it checks inbox work again.
    scope.on('agent/turn-stopping', async ({ turn, signal }) => {
      const skip = reason => diagnose('task_review_stop_skipped', { reason })
      const hasLiveJobs = () => ctx.get?.('jobs')?.list(agent.id)
        .some(job => ['running', 'stopping'].includes(job.status)) === true
      if (disposed || signal?.aborted || turn !== currentTurn || pendingUserTools.size || pendingApprovalCount
          || agent.status !== 'running' || epoch !== turnEpoch
          || agent.inbox.nextStep.length || agent.inbox.nextTurn.length || hasLiveJobs()) return skip('cancelled_or_pending')
      if (remindedTurn === turn) return skip('already_reminded_this_turn')
      const atRead = epoch
      current = await read(signal)
      if (!current || disposed || signal?.aborted || atRead !== epoch || pendingUserTools.size
          || pendingApprovalCount || current.waitingForUser
          || current.binding === 'read_only' || hasLiveJobs()
          || agent.status !== 'running' || agent.inbox.nextStep.length || agent.inbox.nextTurn.length) return skip('review_unavailable_or_pending')
      const explicitlyExecuting = current.review.execution_intent === 'execute'
        && turnText !== null && current.review.latest_task_text_fingerprint === turnText
      if (!explicitlyExecuting) return skip('no_current_execution_intent')
      const changed = current.review.task_id !== baseline?.review?.task_id
        || current.review.source_version !== baseline?.review?.source_version
        || current.review.input_fingerprint !== baseline?.review?.input_fingerprint
        || current.activity !== baseline?.activity
      const key = taskReviewKey(current.review)
      const noticeIdentity = key && `${turnMessage}:${key}`
      if ((!changed && current.binding !== 'unbound') || !key || seen.has(noticeIdentity)
          || !current.review.differences.some(item => item.actionable === true
            && item.id !== 'system.task_review_not_declared')) return skip('no_new_actionable_difference')
      seen.add(noticeIdentity)
      if (seen.size > 64) seen.delete(seen.values().next().value)
      remindedTurn = turn
      agent.steer({ id: randomUUID(), role: 'user', source: { kind: TASK_REVIEW_SOURCE,
        form: 'notice', summary: '正在复核本次计算与任务要求的差异' },
        content: [{ type: 'text', text: taskReviewText(current.review, { cli }) }] })
      diagnose('task_review_stop_notice', { projection: key })
    })
    scope.effect(() => () => { disposed = true; seen.clear() }, 'App task review advice')
  })
}
