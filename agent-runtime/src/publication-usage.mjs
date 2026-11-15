/** Consent refers only to ordinary DSH human answers and exact App-prepared questions. */
import { createHash } from 'node:crypto'
import { TOOL_OUTCOME_UNKNOWN } from '@deepseek-ai/dsh-session'

const CONTRACT = 'genshin-publication-usage-v1'
const APPROVE = '同意本任务使用'
const DECLINE = '不同意使用'
const scope = usage => [usage.task_id, usage.scope, usage.purpose]
const key = usage => JSON.stringify(scope(usage))
const readJson = value => {
  try { return JSON.parse(value) } catch { return null }
}
const messageValue = message => readJson(message?.content?.find(item => item.type === 'text')?.text)
const matchingQuestion = (expected, actual) => actual?.id === expected.id
  && actual.question === expected.question && actual.multi_select !== true
  && actual.options?.length === 2
  && actual.options[0]?.label === APPROVE && actual.options[1]?.label === DECLINE
const rawOptions = (prepared, items) => Array.isArray(items) ? items.flatMap(item => {
  const expected = prepared.get(item?.id)
  return expected && matchingQuestion(expected.question, item) ? [expected] : []
}) : []

function trustedAnswers({ events, inheritedEventCount }, usage) {
  const prepared = new Map(), calls = new Map(), questions = new Map(), answers = new Map()
  for (const event of events) {
    if (event.seq < inheritedEventCount) continue
    const { type, data } = event
    if (type === 'tool/call') {
      if (data.name === 'gs_read_context' && readJson(data.arguments)?.resource === 'publication_usage') {
        calls.set(data.callId, data.name)
      }
      if (data.name === 'ask_user_question') {
        const selected = rawOptions(prepared, readJson(data.arguments)?.questions)
        if (selected.length) questions.set(data.callId, { selected, event: event.seq })
      }
    } else if (type === 'tool/result' && data.message.isError !== true && data.error === undefined) {
      const id = data.message.toolCallId
      if (calls.has(id)) {
        calls.delete(id)
        const result = messageValue(data.message)?.data
        if (result?.contract === CONTRACT && result.question && key(result) === key(usage)
            && Array.isArray(result.question_entity_keys)) {
          prepared.set(result.question.id, { question: result.question, entities: result.question_entity_keys })
        }
      }
      const candidate = questions.get(id)
      if (candidate) {
        const result = messageValue(data.message)
        if (result?.pending !== true) {
          settle(candidate, result?.answers, { call_id: String(id), event: event.seq }, answers)
          questions.delete(id)
        }
      }
    } else if (type === 'tool/result' && data.error?.code !== TOOL_OUTCOME_UNKNOWN) {
      questions.delete(data.message.toolCallId)
      calls.delete(data.message.toolCallId)
    } else if (type === 'tool/ptc-dispatch' && data.name === 'ask_user_question' && data.isError !== true) {
      const selected = rawOptions(prepared, data.arguments?.questions)
      const result = messageValue(data)
      if (result?.pending === true && selected.length) {
        questions.set(data.subCallId, { selected, event: event.seq })
      } else settle({ selected }, result?.answers, { call_id: String(data.subCallId), event: event.seq }, answers)
    } else if (type === 'user/message' && data.source?.kind === 'user-question-reply') {
      const id = data.source.callId
      const candidate = questions.get(id)
      if (candidate) {
        settle(candidate, messageValue(data)?.answers,
          { call_id: String(id), event: event.seq }, answers)
        questions.delete(id)
      }
    }
  }
  for (const [id, value] of questions) {
    for (const item of value.selected) {
      for (const entity of item.entities) {
        if (!answers.has(entity)) answers.set(entity, { status: 'waiting_for_user', call_id: String(id),
          event: value.event, question_id: item.question.id })
      }
    }
  }
  return answers
}

function settle(request, answers, ref, output) {
  if (!Array.isArray(answers)) return
  for (const item of request.selected) {
    const matching = answers.filter(answer => answer?.id === item.question.id)
    const answer = matching.length === 1 ? matching[0] : null
    if (answer?.custom !== undefined || answer?.selected?.length !== 1) continue
    const status = answer.selected[0] === APPROVE ? 'confirmed'
      : answer.selected[0] === DECLINE ? 'declined' : null
    if (status) for (const entity of item.entities) {
      output.set(entity, { status, ...ref, question_id: item.question.id })
    }
  }
}

export function withPublicationAnswers(result, observation) {
  const usage = result?.data
  if (usage?.contract !== CONTRACT) return result
  const answers = trustedAnswers(observation, usage)
  const entities = usage.entities.map(row => ({ ...row,
    response: row.requires_usage_confirmation ? answers.get(row.usage_key) ?? { status: 'ask_user' }
      : { status: 'not_required' } }))
  const pending = entities.filter(row => row.response.status === 'ask_user')
  const keys = pending.map(row => row.usage_key)
  const waiting = entities.some(row => row.response.status === 'waiting_for_user')
  let question = null
  if (keys.length) {
    const fingerprint = createHash('sha256').update(JSON.stringify([...scope(usage),
      [...keys].sort()])).digest('hex').slice(0, 24)
    const names = pending.map(row => row.question_label).join('、')
    question = { ...usage.question, id: 'genshin-publication-' + fingerprint,
      question: usage.question_prompt.prefix + names + usage.question_prompt.suffix }
  }
  return { ...result, data: { ...usage, entities, question, question_entity_keys: keys,
    decision_source: 'dsh_session_user_question', waiting_for_user: waiting,
    next_step: question ? '把 question 原样传给现有 ask_user_question 的 questions；合并询问后再采用。'
      : waiting ? '已有资料使用问题等待答复。继续无关工作，已问过的范围无需再次提问。'
        : '只对实际已确认或本来无需确认的实体使用；拒绝或无答复均不能视作同意。' } }
}
