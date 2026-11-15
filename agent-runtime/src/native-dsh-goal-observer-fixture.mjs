/** Offline provider only: real DSH goal tools/driver, follow stream and App owner. */
import { readFile } from 'node:fs/promises'
import { connectInheritedMux } from './native-mux-bootstrap.mjs'
import { readNativeDshRuntimeConfig } from './native-dsh-runtime-config.mjs'
import { runNativeDshRuntime, nativeDshRuntimeErrorCode } from './native-dsh-runtime.mjs'
import { validateNativeAppToolArguments } from './native-app-tool-schema.mjs'

const { carrier } = await connectInheritedMux()
try {
  const { mode } = JSON.parse(await readFile(process.argv[5], 'utf8'))
  let requestIndex = 0
  const create = { tool: 'create_goal', arguments: { objective: 'Complete the offline observer fixture', max_goal_rounds: mode === 'round_limit' ? 1 : 4 } }
  const patch = (revision, title) => ({ tool: 'gs_patch_whiteboard', arguments: { expected_revision: revision,
    reason: 'Offline real DSH goal continuation', operations: [{ op: 'replace', path: '/title', value: title }] } })
  const get = { tool: 'get_goal', arguments: {} }
  const steps = mode === 'complete' ? [create, patch(0, 'Goal first turn'), null, patch(1, 'Goal completed second turn'), get, 'complete', null]
    : mode === 'fail_for_resume' ? [create, patch(0, 'Preserved before provider error'), 'provider_error']
    : mode === 'resume_goal' ? [get, 'resume', patch(1, 'Cold resumed same candidate'), get, 'complete', null]
    : mode === 'paused' ? [create, get, 'pause', null]
    : mode === 'round_limit' ? [create, patch(0, 'Goal reaches official round limit'), null, null]
    : mode === 'no_goal' ? [patch(0, 'Ordinary no-goal turn'), null]
    : undefined
  if (!steps) throw new Error('goal_observer_fixture_mode_invalid')
  const refsIn = value => {
    const found = []
    const visit = row => {
      if (row === null || row === undefined) return
      if (Array.isArray(row)) { row.forEach(visit); return }
      if (typeof row === 'string') {
        if (row.startsWith('{')) { try { visit(JSON.parse(row)) } catch {} }
        return
      }
      if (typeof row !== 'object') return
      if (row.goal?.id && Number.isInteger(row.goal.revision)) found.push(row.goal)
      Object.values(row).forEach(visit)
    }
    visit(value)
    return found
  }
  globalThis.fetch = async (url, init) => {
    if (!String(url).endsWith('/v1/messages')) throw new Error('unexpected_network_in_goal_observer_fixture')
    const body = JSON.parse(init.body), index = requestIndex++
    let call = steps[index]
    if (call === 'provider_error') {
      const events = [{ type: 'message_start', message: { usage: { input_tokens: 10, output_tokens: 0 } } },
        { type: 'content_block_start', index: 0, content_block: { type: 'tool_use', id: 'invalid-provider-call', name: 'gs_patch_whiteboard', input: {} } },
        { type: 'content_block_delta', index: 0, delta: { type: 'input_json_delta', partial_json: '{"expected_revision":1,' } },
        { type: 'content_block_stop', index: 0 },
        { type: 'message_delta', delta: { stop_reason: 'tool_use' }, usage: { output_tokens: 1 } }, { type: 'message_stop' }]
      return new Response(events.map(event => `event: ${event.type}\ndata: ${JSON.stringify(event)}\n\n`).join(''),
        { headers: { 'content-type': 'text/event-stream' } })
    }
    if (typeof call === 'string') {
      const ref = refsIn(body.messages).at(-1)
      if (!ref) throw new Error('goal_observer_fixture_ref_missing')
      call = { tool: 'update_goal', arguments: { goal_id: ref.id, revision: ref.revision, action: call } }
    }
    const tools = (body.tools ?? []).map(tool => ({ name: tool.name, inputSchema: tool.input_schema }))
    if (call) {
      const selected = tools.find(tool => tool.name === call.tool)
      if (!selected) throw new Error('goal_observer_fixture_tool_missing')
      validateNativeAppToolArguments(selected, call.arguments)
    }
    const events = [{ type: 'message_start', message: { usage: { input_tokens: 10, output_tokens: 0 } } },
      { type: 'content_block_start', index: 0, content_block: call
        ? { type: 'tool_use', id: 'goal-observer-tool-' + index, name: call.tool, input: call.arguments }
        : { type: 'text', text: 'Goal observer fixture closing reply ' + index } },
      { type: 'content_block_stop', index: 0 },
      { type: 'message_delta', delta: { stop_reason: call ? 'tool_use' : 'end_turn' }, usage: { output_tokens: 1 } },
      { type: 'message_stop' }]
    return new Response(events.map(event => `event: ${event.type}\ndata: ${JSON.stringify(event)}\n\n`).join(''),
      { headers: { 'content-type': 'text/event-stream' } })
  }
  await runNativeDshRuntime({ carrier, config: await readNativeDshRuntimeConfig(process.argv.slice(2, 5)) })
} catch (error) {
  console.error(nativeDshRuntimeErrorCode(error))
  process.exitCode = 1
} finally { carrier.close() }
