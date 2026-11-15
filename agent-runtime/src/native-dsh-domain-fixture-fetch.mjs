/** Offline official-provider adapter fixture that requests real scoped App tools. */
import { createPhase5bOfficialFakeFetch } from './phase5b-official-fake-fetch.mjs'
import { validateNativeAppToolArguments } from './native-app-tool-schema.mjs'

export function createNativeDshDomainFixtureFetch({ calls = [{ tool: 'gs_app_get_workspace', arguments: {} }], repeatCalls = true, onRequest } = {}) {
  const fallback = createPhase5bOfficialFakeFetch({ requireV2Context: false })
  let requests = 0
  const fetch = async (url, init) => {
    if (!String(url).endsWith('/v1/messages')) return fallback(url, init)
    const index = repeatCalls ? requests++ % (calls.length + 1) : requests++
    const body = JSON.parse(init.body)
    const declared = (body.tools ?? []).map(tool => ({ name: tool.name ?? tool.function?.name,
      inputSchema: tool.input_schema ?? tool.function?.parameters }))
    onRequest?.({ requestNumber: requests, tools: declared, body })
    if (index >= calls.length) return fallback(url, init)
    const batch = Array.isArray(calls[index]) ? calls[index] : [calls[index]]
    for (const call of batch) {
      const selected = declared.find(tool => tool.name === call.tool)
      if (selected === undefined) throw new Error(`fixture_tool_not_declared:${call.tool}`)
      validateNativeAppToolArguments(selected, call.arguments)
    }
    const events = [
      { type: 'message_start', message: { usage: { input_tokens: 10, output_tokens: 0 } } },
      ...batch.flatMap((call, position) => [
        { type: 'content_block_start', index: position, content_block: { type: 'tool_use',
          id: `scoped-app-call-${requests}-${position}`, name: call.tool, input: call.arguments } },
        { type: 'content_block_stop', index: position },
      ]),
      { type: 'message_delta', delta: { stop_reason: 'tool_use' }, usage: { output_tokens: batch.length } },
      { type: 'message_stop' },
    ]
    return new Response(events.map(event => `event: ${event.type}\ndata: ${JSON.stringify(event)}\n\n`).join(''),
      { headers: { 'content-type': 'text/event-stream' } })
  }
  Object.defineProperty(fetch, 'requestCount', { get: () => requests })
  return fetch
}
