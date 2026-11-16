/** Optional official DSH plugin. No global profile edits and no model route. */
import { execFile } from 'node:child_process'
import { promisify } from 'node:util'
import { isAbsolute } from 'node:path'
import { access } from 'node:fs/promises'
import { mountTaskReview, taskReview } from './dsh-task-review.mjs'

const execute = promisify(execFile)
export const name = 'genshin-task-review'
export const inject = ['systemPrompt', 'tools', 'agents']

export function createCliTaskReviewPort(config, run = execute, exists = access) {
  if (typeof config?.command !== 'string' || !isAbsolute(config.command)
      || config.plan !== undefined && (typeof config.plan !== 'string' || !isAbsolute(config.plan))
      || typeof config.audit !== 'string' || !isAbsolute(config.audit)
      || config.calculated !== undefined && (typeof config.calculated !== 'string' || !isAbsolute(config.calculated))
      || !Array.isArray(config.args ?? []) || (config.args ?? []).some(arg => typeof arg !== 'string')) {
    throw new TypeError('cli_task_review_requires_explicit_command_and_audit')
  }
  const present = path => path ? exists(path).then(() => path, error => {
    if (error.code === 'ENOENT') return null
    throw error
  }) : null
  return { async read(_agent, signal) {
    // A fresh task has no plan yet. The bound paths are a destination, not a
    // demand that an empty or fabricated plan be written before asking questions.
    const plan = await present(config.plan)
    const calculated = plan && await present(config.calculated)
    const args = [...(config.args ?? []), 'workflow', 'workspace', '--audit', config.audit]
    if (plan) args.push('--plan', plan)
    if (calculated) args.push('--calculated', calculated)
    const read = async argv => run(config.command, argv, {
      cwd: config.cwd, signal, windowsHide: true, shell: false,
      // Shared transport capacity, not a model/workflow budget.
      maxBuffer: 128 * 1024 * 1024, encoding: 'utf8',
    })
    let result
    try { result = await read(args) } catch (error) {
      // An old calculated file can exist while the caller has not bound the
      // new plan yet. Fall back only for this read-only, identified condition.
      signal?.throwIfAborted()
      let reason
      try { reason = JSON.parse(error.stderr).reason } catch { /* Preserve the actual failure. */ }
      if (!calculated || reason !== 'audit_plan_unbound') throw error
      result = await read(args.slice(0, -2))
    }
    const envelope = JSON.parse(result.stdout.replace(/^\uFEFF/u, ''))
    if (envelope.code !== undefined && envelope.code !== 0) throw new Error(envelope.message ?? envelope.reason ?? 'task_review_query_failed')
    const data = envelope.data ?? envelope
    const review = taskReview(data.task_review ?? data.review ?? data)
    return { review, binding: data.task_review?.binding ?? review?.binding ?? (plan ? 'bound' : 'unbound') }
  } }
}

export function apply(ctx, config) {
  const port = createCliTaskReviewPort(config)
  ctx.systemPrompt.section({ name: 'genshin:cli-workspace', order: 105, interpolate: false,
    text: '本会话可选 App 任务对照绑定：' + JSON.stringify({ plan: config.plan ?? null, audit: config.audit, calculated: config.calculated ?? null,
      command: config.command, args: config.args ?? [], cwd: config.cwd ?? null })
      + '\n文件尚未创建时照常工作；这些路径不表示已有方案或参考答案。原生输入可通过当前App CLI和文件工具建立。'
      + '任务对照使用同一 workflow workspace 服务；工作记录只作辅助，缺少记录不阻止计算，也不能赋予用户批准。'
      + '要保存本轮原题，先用 workflow workspace --audit AUDIT --task-file TASK_TEXT；已有方案可显式 workflow bind --audit AUDIT --plan PLAN。'
      + '明确执行任务可以用 decision 记录 execution_intent=execute，source_refs 引用本轮原题记录 ID。'
      + '用户另指定路径时遵从用户，并明确本插件仍绑定原路径；不静默监控其他文件。' })
  // The ordinary CLI service remains fully usable without this optional hook.
  return mountTaskReview(ctx, { port, context: true, cli: true, eligible: agent => (ctx.agents.get(agent.id) === agent && agent.session.header.origin !== 'subagent'),
    onDiagnostic: row => ctx.logger?.debug?.('App task review %s', JSON.stringify(row)) })
}
