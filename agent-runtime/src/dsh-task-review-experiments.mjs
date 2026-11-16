/** Explicit experiments over upstream public APIs, disabled unless invoked. */
import { randomUUID } from 'node:crypto'
import { PtcExecutor } from '@deepseek-ai/dsh-tool-executor-ptc'

export function enableCliPtc(agent, { enabled = false, nativeDomain = false } = {}) {
  if (!enabled) return () => {}
  if (nativeDomain) throw new Error('native_domain_ptc_call_identity_not_supported')
  // Official runtime owns batching, admission, cancellation, and partial writes.
  return agent.ctx.tools.setExecutor(new PtcExecutor(agent.ctx))
}

/** A snapshot reviewer cannot read/write tools or spawn another reviewer. */
export async function startReadonlyReview(ctx, { enabled = false, parent, evidence,
  signal, maxTokens } = {}) {
  if (!enabled) return { enabled: false, started: false }
  if (!parent || !signal || !Number.isSafeInteger(maxTokens) || maxTokens <= 0
      || typeof evidence !== 'string' || !evidence) throw new TypeError('explicit_review_evidence_and_budget_required')
  signal.throwIfAborted()
  const childId = randomUUID()
  const dispose = ctx.on('agent/created', ({ agent }) => {
    if (agent.id !== childId) return
    agent.ctx.tools.guard(() => '只读证据审查仅检查已提供材料，不执行工具或修改方案。')
  }, { prepend: true })
  try {
    const activation = await ctx.subagents.startActivation({ provider: 'spawn', childId,
      label: '可选只读证据复核', signal, delivery: 'parent',
        parent, agentOptions: { maxTokens },
        prompt: [{ type: 'text', text: '只审查以下材料是否自洽。列明有证据的差异和未知范围；'
          + '不得修改方案、授予批准、推定游戏事实或把建议当作计算门。\n' + evidence }],
      })
    // The official activation manages the child's result, accounting and cancel.
    void activation.result.finally(dispose).catch(() => {})
    return activation
  } catch (error) { dispose(); throw error }
}
