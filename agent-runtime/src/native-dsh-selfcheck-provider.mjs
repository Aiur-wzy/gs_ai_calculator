/** Fixed in-memory SSE transport; no upstream fallback or arbitrary URL fetch. */
import { validateNativeAppToolArguments } from './native-app-tool-schema.mjs'

const MODEL_URL = 'https://api.deepseek.com/models'
const MESSAGES_URL = 'https://api.deepseek.com/anthropic/v1/messages'
function sse(events) {
  return new Response(events.map(event => `event: ${event.type}\ndata: ${JSON.stringify(event)}\n\n`).join(''),
    { headers: { 'content-type': 'text/event-stream' } })
}

export function selfcheckProvider(calls, guards) {
  if (!Array.isArray(calls) || calls.length === 0) throw new Error('selfcheck_fixed_calls_required')
  let requests = 0
  const fetch = async (input, options = {}) => {
    const url = String(input)
    if (url === MODEL_URL) return Response.json({ data: [
      { id: 'deepseek-flash', input_modalities: ['text'], output_modalities: ['text'], api_capabilities: { anthropic_messages: {} } },
    ] })
    if (url !== MESSAGES_URL || options.method !== 'POST' || typeof options.body !== 'string') return guards.denyNetwork()
    const request = JSON.parse(options.body), index = requests++
    const declared = (request.tools ?? []).map(tool => ({ name: tool.name ?? tool.function?.name,
      inputSchema: tool.input_schema ?? tool.function?.parameters }))
    if (index > calls.length) throw new Error('selfcheck_unexpected_extra_provider_turn')
    const blocks = index === calls.length ? [
      { type: 'content_block_start', index: 0, content_block: { type: 'thinking', thinking: '固定离线工程输入已完成App计算核对。' } },
      { type: 'content_block_stop', index: 0 },
      { type: 'content_block_start', index: 1, content_block: { type: 'text', text: '已完成工程候选的实际计算；请审核同步并保存。此为离线系统验收，不是游戏机制实验。' } },
      { type: 'content_block_stop', index: 1 },
    ] : [calls[index]].flat().flatMap((call, position) => {
      const selected = declared.find(tool => tool.name === call.tool)
      if (selected === undefined) throw new Error(`selfcheck_tool_not_declared:${call.tool}`)
      validateNativeAppToolArguments(selected, call.arguments)
      return [
        { type: 'content_block_start', index: position, content_block: { type: 'tool_use',
          id: `selfcheck-call-${index}-${position}`, name: call.tool, input: call.arguments } },
        { type: 'content_block_stop', index: position },
      ]
    })
    return sse([
      { type: 'message_start', message: { usage: { input_tokens: 25, output_tokens: 0 } } }, ...blocks,
      { type: 'message_delta', delta: { stop_reason: index === calls.length ? 'end_turn' : 'tool_use' }, usage: { output_tokens: 15 } },
      { type: 'message_stop' },
    ])
  }
  Object.defineProperty(fetch, 'requestCount', { get: () => requests })
  return fetch
}
