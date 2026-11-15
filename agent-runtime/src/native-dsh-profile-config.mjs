/** Thin JSON transport to the complete, official profile ConfigEditor.
 * DSH owns schema validation, profile locking, persistence, rollback and HMR.
 * No configuration file is parsed/written here and no shadow config is kept.
 */
import { createHash } from 'node:crypto'
import { Remote, RemoteError, TypertRemoteService, createTypertRemoteInfo } from '@deepseek-ai/dsh-typert-protocol'
import { redactSecrets } from '@deepseek-ai/dsh-settings'
import { z } from 'zod'

const object = value => value !== null && typeof value === 'object' && !Array.isArray(value)
const describeInput = z.strictObject({ entry_id: z.string().min(1).optional() })
const path = z.array(z.string().min(1).refine(value => !['__proto__', 'prototype', 'constructor'].includes(value))).min(1)
const editInput = z.strictObject({ entry_id: z.string().min(1), expected_fingerprint: z.string().regex(/^[0-9a-f]{64}$/),
  operations: z.array(z.discriminatedUnion('op', [
    z.strictObject({ op: z.literal('set'), path, value: z.json() }),
    z.strictObject({ op: z.literal('unset'), path }),
  ])).min(1) })
const reject = (message, details = {}) => { throw new RemoteError('settings/rejected', message, details) }

function stable(value) {
  if (value === undefined) return 'undefined'
  if (value === null || ['string', 'boolean'].includes(typeof value)) return JSON.stringify(value)
  if (typeof value === 'number' && Number.isFinite(value)) return JSON.stringify(value)
  if (Array.isArray(value)) return '[' + value.map(stable).join(',') + ']'
  if (object(value) && [Object.prototype, null].includes(Object.getPrototypeOf(value))) {
    return '{' + Object.keys(value).sort().map(key => JSON.stringify(key) + ':' + stable(value[key])).join(',') + '}'
  }
  throw new TypeError('profile_config_not_json')
}
function fingerprint(id, current, inherited) {
  return createHash('sha256').update(stable({ id, current, inherited })).digest('hex')
}
function view(row) {
  const { entry, inherited, override } = row, schema = entry.fiber?.runtime?.Config
  const base = { entry_id: entry.options.id, module: entry.options.name ?? null, state: entry.fiber?.state ?? null,
    owner: 'dsh_config_editor', applies: 'official_loader_lifecycle' }
  if (schema === undefined || typeof schema.toJSON !== 'function') {
    return { ...base, editable_json: false, reason: 'schema_unavailable', schema: null }
  }
  try {
    const current = entry.options.config ?? {}, raw = redactSecrets(schema, current)
    const inheritedView = redactSecrets(schema, inherited), overrideView = redactSecrets(schema, override)
    // JSON round-trip removes only absent/undefined object properties. Non-JSON
    // expressions are not silently reconstructed as editable JSON.
    stable(current); stable(inherited); stable(override)
    return JSON.parse(JSON.stringify({ ...base, editable_json: true, schema: schema.toJSON(),
      fingerprint: fingerprint(entry.options.id, current, inherited), raw: raw.value,
      inherited: inheritedView.value, override: overrideView.value, secrets: raw.secrets }))
  } catch {
    return { ...base, editable_json: false, reason: 'non_json_configuration', schema: null }
  }
}
function changePaths(current, operations) {
  const next = structuredClone(current)
  for (const operation of operations) {
    let parent = next
    for (const key of operation.path.slice(0, -1)) {
      if (!Object.hasOwn(parent, key)) {
        if (operation.op === 'unset') { parent = null; break }
        parent[key] = {}
      }
      if (!object(parent[key]) && !Array.isArray(parent[key])) reject('目标配置路径不能穿过一个普通值。')
      parent = parent[key]
    }
    if (parent === null) continue
    const key = operation.path.at(-1)
    if (Array.isArray(parent)) {
      if (!/^(0|[1-9][0-9]*)$/.test(key) || Number(key) > parent.length) reject('配置数组下标不合法。')
      const index = Number(key)
      if (operation.op === 'unset') { if (index < parent.length) parent.splice(index, 1) }
      else parent[index] = structuredClone(operation.value)
    } else if (operation.op === 'unset') delete parent[key]
    else parent[key] = structuredClone(operation.value)
  }
  return next
}
function prepareEdit(ctx, input) {
  const rows = ctx.configEditor.configuration().filter(row => row.entry.options.id === input.entry_id)
  if (rows.length !== 1) reject('未找到唯一且由活动 profile 管理的配置条目。', { entry_id: input.entry_id })
  const row = rows[0], described = view(row)
  if (!described.editable_json) reject('此条目不能通过 JSON 参数编辑；请使用官方配置文件编辑入口。', { entry_id: input.entry_id })
  const overlaps = (left, right) => left.slice(0, Math.min(left.length, right.length)).every((key, index) => key === right[index])
  if (input.operations.some(operation => described.secrets.some(secret => overlaps(operation.path, secret.path)))) {
    reject('秘密字段不通过通用配置接口修改，请使用 App 的 Windows 凭据设置。', { entry_id: input.entry_id })
  }
  return { row, described }
}

const initializers = []
export class ProfileConfigController extends TypertRemoteService {
  static inject = ['configEditor', 'typert', 'typertGateway', 'agents', 'approval']
  constructor(ctx) {
    super(ctx, 'genshinProfile')
    for (const initialize of initializers) initialize.call(this)
    ctx.effect(() => ctx.typert.remotes.register(PROFILE_CONFIG_REMOTE), 'App: complete DSH ConfigEditor transport')
  }
  configDescribe(request) {
    const input = describeInput.parse(request)
    const rows = this.ctx.configEditor.configuration().filter(row => input.entry_id === undefined || row.entry.options.id === input.entry_id)
    if (input.entry_id !== undefined && rows.length !== 1) reject('未找到唯一且由活动 profile 管理的配置条目。', { entry_id: input.entry_id })
    return { owner: 'dsh_config_editor', scope: 'complete_profile_configuration', entries: rows.map(view) }
  }
  async configEdit(request) {
    const input = editInput.parse(request)
    const { row } = prepareEdit(this.ctx, input)
    try {
      await this.ctx.configEditor.edit(row.entry, (current, inherited) => {
        if (fingerprint(input.entry_id, current, inherited) !== input.expected_fingerprint) {
          throw new RemoteError('settings/conflict', '配置已变化，请重新读取后再修改。', { entry_id: input.entry_id })
        }
        return changePaths(current, input.operations)
      })
    } catch (error) {
      if (error instanceof RemoteError) throw error
      throw new RemoteError('settings/rejected', error instanceof Error ? error.message : '官方配置编辑器拒绝了修改。',
        { entry_id: input.entry_id }, { cause: error })
    }
    return this.configDescribe({ entry_id: input.entry_id })
  }
  async configRequestEdit(request, agentId) {
    // The per-Session CLI owner supplies agentId from its authenticated scope.
    // It is deliberately not a configurable field inside the model's request.
    const input = structuredClone(editInput.parse(request)), { described } = prepareEdit(this.ctx, input)
    const agent = typeof agentId === 'string' ? this.ctx.agents.get(agentId) : undefined
    if (!agent || !this.ctx.agents.roots().includes(agent) || !this.ctx.typertGateway.hasLiveClient()) {
      reject('修改 DSH 配置需要当前执行中的会话和已连接的用户界面。请在 GUI 中确认本次操作；空闲时可使用官方设置页面。',
        { entry_id: input.entry_id, approval_outcome: 'unavailable' })
    }
    if (described.fingerprint !== input.expected_fingerprint) {
      throw new RemoteError('settings/conflict', '配置已变化，请重新读取后再申请本次修改。', { entry_id: input.entry_id })
    }
    const requestHash = createHash('sha256').update(stable({ agentId, request: input })).digest('hex')
    const changes = input.operations.map(operation => `${operation.op === 'set' ? '设置' : '删除'} ${operation.path.join('.')} ${operation.op === 'set' ? '= ' + JSON.stringify(operation.value) : ''}`).join('\n')
    const reason = `CLI 请求修改 DSH 配置「${input.entry_id}」。\n${changes}\n配置基线：${input.expected_fingerprint}\n本次请求摘要：${requestHash}\n仅批准以上精确修改；执行前仍会核对配置版本。`
    let outcome
    try {
      outcome = await this.ctx.approval.request({ agent, toolName: 'profile_config_edit', reason,
        signal: this.ctx.invocation?.signal })
    } catch (error) {
      if (error instanceof Error && error.message.startsWith('approval.request() outside an open turn:')) {
        throw new RemoteError('settings/rejected', '当前会话没有可用于此次确认的活动轮次。请使用 GUI 官方设置，或在执行中的会话里重新申请。',
          { entry_id: input.entry_id, approval_outcome: 'unavailable', request_fingerprint: requestHash }, { cause: error })
      }
      // A real audit/persistence fault is a system fault, not user rejection.
      throw error
    }
    if (outcome !== 'allowed-once') {
      reject(outcome === 'rejected' ? '用户拒绝了这次配置修改，配置未改变。' : '本次配置修改未得到用户批准，配置未改变。',
        { entry_id: input.entry_id, approval_outcome: outcome, request_fingerprint: requestHash })
    }
    const result = await this.configEdit(input)
    return { ...result, approval: { outcome: 'allowed-once', request_fingerprint: requestHash } }
  }
}
for (const name of ['configDescribe', 'configEdit', 'configRequestEdit']) Remote(ProfileConfigController.prototype[name], {
  kind: 'method', name, static: false, private: false, addInitializer(value) { initializers.push(value) },
})

const codec = (typeSymbol, factory) => ({ mode: 'strict', typeSymbol, create: factory })
export const PROFILE_CONFIG_REMOTE = createTypertRemoteInfo({ package: '@genshin-calc/native-profile-config',
  methods: Object.fromEntries([['configDescribe', describeInput], ['configEdit', editInput], ['configRequestEdit', editInput]].map(([name, schema]) =>
    [`genshinProfile/${name}`, { service: 'genshinProfile', invocation: { kind: 'direct' },
      parameters: [{ name: 'request', source: 'json', codec: codec(`genshinProfile/${name}:request`, () => schema),
        strictCodec: codec(`genshinProfile/${name}:request`, () => schema) },
        ...(name === 'configRequestEdit' ? [{ name: 'agentId', source: 'json',
          codec: codec(`genshinProfile/${name}:agentId`, () => z.string().min(1)),
          strictCodec: codec(`genshinProfile/${name}:agentId`, () => z.string().min(1)) }] : [])],
      result: codec(`genshinProfile/${name}:result`, () => z.json()) }])),
}, z)
