/** Public DSH production modules; App contributes only retry admission. */
import * as Retry from '@deepseek-ai/dsh-llm-retry'
import GoalService from '@deepseek-ai/dsh-goal'
import * as GoalRoundDriver from '@deepseek-ai/dsh-goal-round-driver'
import * as GoalTool from '@deepseek-ai/dsh-tool-goal'
import PlanMode from '@deepseek-ai/dsh-plan-mode'
import Approval from '@deepseek-ai/dsh-user-approval'
import MessageFeedback from '@deepseek-ai/dsh-message-feedback'
import SessionReference from '@deepseek-ai/dsh-session-reference'
import SessionTitle from '@deepseek-ai/dsh-session-title'
import * as AskUserTool from '@deepseek-ai/dsh-tool-ask-user'
import { mountNativeRetryAdmission } from './native-retry-admission.mjs'

export const NATIVE_GENERIC_MODULES = Object.freeze([
  '@deepseek-ai/dsh-llm-retry', '@deepseek-ai/dsh-goal', '@deepseek-ai/dsh-goal-round-driver',
  '@deepseek-ai/dsh-tool-goal', '@deepseek-ai/dsh-plan-mode',
  '@deepseek-ai/dsh-user-approval', '@deepseek-ai/dsh-message-feedback',
  '@deepseek-ai/dsh-session-reference', '@deepseek-ai/dsh-session-title',
  '@deepseek-ai/dsh-tool-ask-user',
])
export const NATIVE_PLAN_GUIDANCE = '当前为方案讨论。先调查并完善方案，再通过 exit_plan_mode 展示完整计划并等待用户反馈。计划模式是协作指导，不代表候选审核、数据库纠错批准或正式白板应用授权。'

export async function mountNativeDshGenericCapabilities(ctx, retryAdmission) {
  const mounted = new Set(ctx.get('genshinFullProfile')?.rows().map(row => row.name) ?? [])
  const add = async (name, plugin, config) => {
    if (mounted.has(name)) return
    await ctx.plugin(plugin, config)
    mounted.add(name)
  }
  await add('@deepseek-ai/dsh-llm-retry', Retry)
  mountNativeRetryAdmission(ctx, retryAdmission)
  await add('@deepseek-ai/dsh-goal', GoalService)
  await add('@deepseek-ai/dsh-goal-round-driver', GoalRoundDriver)
  await add('@deepseek-ai/dsh-tool-goal', GoalTool)
  await add('@deepseek-ai/dsh-plan-mode', PlanMode, { section: NATIVE_PLAN_GUIDANCE })
  await add('@deepseek-ai/dsh-user-approval', Approval)
  // Deployment values copied from DSH's Web and base composition. No hidden
  // title-provider model is installed, and feedback remains local log-only.
  await add('@deepseek-ai/dsh-message-feedback', MessageFeedback, { maxNoteBytes: 8192 })
  await add('@deepseek-ai/dsh-session-reference', SessionReference)
  await add('@deepseek-ai/dsh-session-title', SessionTitle, { fallbackMaxWords: 5, fallbackMaxBytes: 40, maxTitleBytes: 80 })
  await add('@deepseek-ai/dsh-tool-ask-user', AskUserTool)
  for (const service of ['goals', 'planMode', 'approval', 'messageFeedback',
    'sessionReferenceResolver', 'sessionTitle']) {
    if (ctx.get(service) === undefined) throw new Error(`native_generic_capability_missing:${service}`)
  }
}
