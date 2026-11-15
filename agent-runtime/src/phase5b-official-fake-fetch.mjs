/** Offline-only Anthropic Messages SSE fixture for the official DeepSeek adapter. */
import { setTimeout as delay } from 'node:timers/promises'
import { DEEPSEEK_OFFICIAL_MESSAGES_ROOT } from './native-deepseek-kernel.mjs'

function sseResponse(events, { signal, hang = false } = {}) {
  const encoder = new TextEncoder()
  const encoded = encoder.encode(events.map(event => (
    `event: ${event.type}\ndata: ${JSON.stringify(event)}\n\n`
  )).join(''))
  return new Response(new ReadableStream({
    start(controller) {
      controller.enqueue(encoded)
      if (!hang) {
        controller.close()
        return
      }
      const abort = () => controller.error(new DOMException('Aborted', 'AbortError'))
      if (signal?.aborted) abort()
      else signal?.addEventListener('abort', abort, { once: true })
    },
  }), { status: 200, headers: { 'content-type': 'text/event-stream' } })
}

function completeEvents() {
  return [
    { type: 'message_start', message: { usage: { input_tokens: 17, output_tokens: 0 } } },
    {
      type: 'content_block_start', index: 0,
      content_block: { type: 'thinking', thinking: '' },
    },
    {
      type: 'content_block_delta', index: 0,
      delta: { type: 'thinking_delta', thinking: 'phase5b offline reasoning' },
    },
    { type: 'content_block_stop', index: 0 },
    {
      type: 'content_block_start', index: 1,
      content_block: { type: 'text', text: '' },
    },
    {
      type: 'content_block_delta', index: 1,
      delta: { type: 'text_delta', text: 'phase5b official answer' },
    },
    { type: 'content_block_stop', index: 1 },
    {
      type: 'message_delta', delta: { stop_reason: 'end_turn' },
      usage: { output_tokens: 7 },
    },
    { type: 'message_stop' },
  ]
}

function compactionEvents() {
  return [
    {
      type: 'message_start',
      message: {
        usage: {
          input_tokens: 1_000,
          cache_read_input_tokens: 809_000,
          output_tokens: 0,
        },
      },
    },
    {
      type: 'content_block_start', index: 0,
      content_block: { type: 'text', text: '' },
    },
    {
      type: 'content_block_delta', index: 0,
      delta: { type: 'text_delta', text: 'Compact durable fixture checkpoint.' },
    },
    { type: 'content_block_stop', index: 0 },
    {
      type: 'message_delta', delta: { stop_reason: 'end_turn' },
      usage: { output_tokens: 12 },
    },
    { type: 'message_stop' },
  ]
}

function failedCompactionEvents() {
  return [
    {
      type: 'message_start',
      message: {
        usage: {
          input_tokens: 900,
          cache_read_input_tokens: 700_000,
          output_tokens: 0,
        },
      },
    },
    {
      type: 'content_block_start', index: 0,
      content_block: { type: 'text', text: '' },
    },
    {
      type: 'content_block_delta', index: 0,
      delta: { type: 'text_delta', text: 'Incomplete compacted checkpoint.' },
    },
    { type: 'content_block_stop', index: 0 },
    {
      type: 'message_delta', delta: { stop_reason: 'max_tokens' },
      usage: { output_tokens: 1_024 },
    },
    { type: 'message_stop' },
  ]
}

function cancellableCompactionEvents() {
  return [
    {
      type: 'message_start',
      message: {
        usage: {
          input_tokens: 800,
          cache_read_input_tokens: 600_000,
          output_tokens: 0,
        },
      },
    },
    {
      type: 'content_block_start', index: 0,
      content_block: { type: 'text', text: '' },
    },
    {
      type: 'content_block_delta', index: 0,
      delta: { type: 'text_delta', text: 'Partial checkpoint before cancellation.' },
    },
    { type: 'content_block_stop', index: 0 },
    {
      type: 'message_delta', delta: { stop_reason: 'end_turn' },
      usage: { output_tokens: 16 },
    },
    { type: 'message_stop' },
  ]
}

function hangingEvents() {
  return [
    { type: 'message_start', message: { usage: { input_tokens: 11, output_tokens: 0 } } },
    {
      type: 'content_block_start', index: 0,
      content_block: { type: 'thinking', thinking: '' },
    },
    {
      type: 'content_block_delta', index: 0,
      delta: { type: 'thinking_delta', thinking: 'phase5b cancellation reasoning' },
    },
  ]
}

function questionEvents() {
  const questions = [{
    id: 'rotation-mode',
    question: '请选择排轴模式。',
    header: '模式',
    options: [
      { label: '稳定循环', description: '优先保证完整循环。' },
      { label: '极限伤害', description: '优先提高单轮伤害。' },
    ],
  }, {
    id: 'notes',
    question: '是否补充说明？',
  }, {
    id: 'checks',
    question: '请选择需要复核的内容。',
    options: [
      { label: '元素附着', description: '复核元素底和附着器。' },
      { label: '能量循环', description: '复核产球、收球和充能。' },
    ],
    multi_select: true,
  }]
  return [
    { type: 'message_start', message: { usage: { input_tokens: 19, output_tokens: 0 } } },
    {
      type: 'content_block_start', index: 0,
      content_block: {
        type: 'tool_use', id: 'toolu-question-batch',
        name: 'ask_user_question', input: {},
      },
    },
    {
      type: 'content_block_delta', index: 0,
      delta: {
        type: 'input_json_delta', partial_json: JSON.stringify({ questions }),
      },
    },
    { type: 'content_block_stop', index: 0 },
    {
      type: 'message_delta', delta: { stop_reason: 'tool_use' },
      usage: { output_tokens: 9 },
    },
    { type: 'message_stop' },
  ]
}

function toolEvents(id, name, args) {
  return [
    { type: 'message_start', message: { usage: { input_tokens: 23, output_tokens: 0 } } },
    {
      type: 'content_block_start', index: 0,
      content_block: { type: 'tool_use', id, name, input: {} },
    },
    {
      type: 'content_block_delta', index: 0,
      delta: { type: 'input_json_delta', partial_json: JSON.stringify(args) },
    },
    { type: 'content_block_stop', index: 0 },
    {
      type: 'message_delta', delta: { stop_reason: 'tool_use' },
      usage: { output_tokens: 8 },
    },
    { type: 'message_stop' },
  ]
}

function formalGuiFixtureOperations(renderedRequest) {
  const match = renderedRequest.match(
    /PHASE5B_FORMAL_OPERATIONS:([A-Za-z0-9_-]+)/u,
  )
  if (!match) throw new Error('phase5b_formal_operations_missing')
  let value
  try {
    value = JSON.parse(Buffer.from(match[1], 'base64url').toString('utf8'))
  } catch {
    throw new Error('phase5b_formal_operations_invalid')
  }
  if (!Array.isArray(value) || value.length < 1 || value.length > 128) {
    throw new Error('phase5b_formal_operations_invalid')
  }
  return value
}

export function createPhase5bOfficialFakeFetch({ onRequest, requireV2Context = true } = {}) {
  if (onRequest !== undefined && typeof onRequest !== 'function') {
    throw new TypeError('onRequest must be a function')
  }
  let requests = 0
  let modelListingRequests = 0
  let overflowMainRequests = 0
  const fetchImpl = async (url, init = {}) => {
    if (url === 'https://api.deepseek.com/models') {
      modelListingRequests += 1
      if (typeof init.headers?.['x-api-key'] !== 'string'
          || init.headers['x-api-key'].length < 1) {
        throw new Error('phase5b_offline_model_listing_invalid')
      }
      return Response.json({
        data: ['deepseek-flash', 'deepseek-v4-pro'].map(id => ({
          id,
          input_modalities: ['text'],
          output_modalities: ['text'],
          api_capabilities: { anthropic_messages: {} },
        })),
      })
    }
    requests += 1
    if (url !== `${DEEPSEEK_OFFICIAL_MESSAGES_ROOT}/v1/messages`
        || init.method !== 'POST'
        || typeof init.headers?.['x-api-key'] !== 'string'
        || init.headers['x-api-key'].length < 1
        || typeof init.body !== 'string') {
      throw new Error('phase5b_offline_request_invalid')
    }
    let body
    try {
      body = JSON.parse(init.body)
    } catch {
      throw new Error('phase5b_offline_request_invalid')
    }
    const renderedRequest = init.body
    const hasV2System = renderedRequest.includes(
      '你是原神拉表客户端内的专用排轴与计算 Agent',
    )
    const hasV2RuntimeContext = renderedRequest.includes(
      '<genshin_native_runtime_context>',
    )
    const hasPhase5dContextMarker = renderedRequest.includes('phase5d_test_marker')
    const isGuiWorkflowFixture = renderedRequest.includes(
      'phase5b-gui-workflow-fixture',
    )
    const isFormalGuiFixture = renderedRequest.includes(
      'phase5b-gui-formal-calculation-fixture',
    )
    const hasLegacyV1Text = renderedRequest.includes('本受限会话没有互联网')
      || renderedRequest.includes('这里没有shell、任意文件或互联网权限')
      || renderedRequest.includes('这里没有 shell、任意文件或互联网工具')
    const isCompaction = renderedRequest.includes(
      'Output only the checkpoint text: do not call any tool or take any other action.',
    )
    const hasContextGeneration = /context_generation=\d+/u.test(renderedRequest)
    const contextGenerationChanged = renderedRequest.includes(
      'context_generation_changed_since_attach=true',
    )
    onRequest?.(Object.freeze({
      requestNumber: requests,
      model: body.model,
      maxTokens: body.max_tokens,
      hasSystem: body.system !== undefined,
      hasTools: Array.isArray(body.tools) && body.tools.length > 0,
      hasV2System,
      hasV2RuntimeContext,
      hasPhase5dContextMarker,
      hasLegacyV1Text,
      isCompaction,
      hasContextGeneration,
      contextGenerationChanged,
    }))
    if (requireV2Context && (!hasV2System || !hasV2RuntimeContext
        || (!hasPhase5dContextMarker && !isGuiWorkflowFixture && !isFormalGuiFixture)
        || hasLegacyV1Text)) {
      throw new Error('phase5d_prompt_projection_invalid')
    }
    if (init.body.includes('phase5b-provider-idle-heartbeat-fixture')) {
      // Longer than the adversarial Python pipe read deadline, but shorter
      // than the reviewed runtime heartbeat timeout.  The Node outer control
      // loop must remain free to answer authenticated heartbeats meanwhile.
      await delay(9_500, undefined, { signal: init.signal })
    }
    if (init.body.includes('phase5b-provider-failure-fixture')) {
      return new Response(JSON.stringify({
        error: { type: 'server_error', message: 'offline fixture failure' },
      }), {
        status: 503,
        headers: { 'content-type': 'application/json' },
      })
    }
    if (init.body.includes('phase5b-question-fixture')) {
      return sseResponse(
        init.body.includes('tool_result') ? completeEvents() : questionEvents(),
        { signal: init.signal },
      )
    }
    if (isCompaction && init.body.includes('COMPACTION_CANCEL_HISTORY')) {
      return sseResponse(cancellableCompactionEvents(), {
        signal: init.signal,
        hang: true,
      })
    }
    if (isCompaction && init.body.includes('COMPACTION_FAILURE_HISTORY')) {
      return sseResponse(failedCompactionEvents(), { signal: init.signal })
    }
    if (isCompaction) {
      return sseResponse(compactionEvents(), { signal: init.signal })
    }
    if (init.body.includes('phase5b-overflow-fixture')) {
      overflowMainRequests += 1
      if (overflowMainRequests === 1) {
        return new Response(JSON.stringify({
          error: {
            type: 'invalid_request_error',
            code: 'context_window_exceeded',
            message: 'request exceeds the model context window',
          },
        }), {
          status: 400,
          headers: { 'content-type': 'application/json' },
        })
      }
      return sseResponse(completeEvents(), { signal: init.signal })
    }
    if (init.body.includes('phase5b-app-rollover-fixture')
        || isGuiWorkflowFixture) {
      if (requests === 1) {
        return sseResponse(toolEvents(
          'toolu-app-rollover-add',
          'gs_add_character',
          {
            expected_revision: 0,
            reason: '真实管道测试首次候选写入',
            db_character_id: 10000133,
          },
        ), { signal: init.signal })
      }
      if (requests === 2) {
        return sseResponse(toolEvents(
          'toolu-app-rollover-read',
          'gs_read_context',
          { resource: 'candidate_board' },
        ), { signal: init.signal })
      }
      return sseResponse(completeEvents(), { signal: init.signal })
    }
    if (isFormalGuiFixture) {
      if (requests === 1) {
        return sseResponse(toolEvents(
          'toolu-gui-formal-patch',
          'gs_patch_candidate',
          {
            expected_revision: 0,
            reason: '离线正式计算夹具补齐受审数值与事件输入',
            operations: formalGuiFixtureOperations(renderedRequest),
          },
        ), { signal: init.signal })
      }
      if (requests === 2) {
        return sseResponse(toolEvents(
          'toolu-gui-formal-refresh',
          'gs_refresh_data_refs',
          {
            expected_revision: 1,
            reason: '同步正式计算夹具的固定数据库资料引用',
          },
        ), { signal: init.signal })
      }
      if (requests === 3) {
        return sseResponse(toolEvents(
          'toolu-gui-formal-calculate',
          'gs_calculate',
          { expected_revision: 2 },
        ), { signal: init.signal })
      }
      return sseResponse(completeEvents(), { signal: init.signal })
    }
    const hang = init.body.includes('phase5b-cancel-fixture')
    return sseResponse(hang ? hangingEvents() : completeEvents(), {
      signal: init.signal,
      hang,
    })
  }
  Object.defineProperty(fetchImpl, 'requestCount', {
    get: () => requests,
    enumerable: true,
  })
  Object.defineProperty(fetchImpl, 'modelListingRequestCount', {
    get: () => modelListingRequests,
    enumerable: true,
  })
  return fetchImpl
}
