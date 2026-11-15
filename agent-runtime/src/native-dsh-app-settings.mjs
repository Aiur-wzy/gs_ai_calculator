/** Only the credential namespace is App-owned; generic settings stay DSH. */
import SettingsController from '@deepseek-ai/dsh-api-settings-controller'
import { symbols } from '@deepseek-ai/cordis'
import { ProfileConfigController } from './native-dsh-profile-config.mjs'

export const inject = ['settings', 'configEditor', 'typertGateway']
export async function apply(ctx) {
  // One narrow product policy around the public edit callback. Validation,
  // revisions, persistence, rollback and HMR remain the official ConfigEditor.
  // In particular, other DeepSeek options remain fully configurable.
  const editor = ctx.configEditor[symbols.original] ?? ctx.configEditor
  const priorEdit = editor.edit
  editor.edit = function (entry, change) {
    return priorEdit.call(this, entry, (current, inherited) => {
      const next = change(current, inherited)
      if (entry.options.id === 'llm-deepseek'
          && (next.baseURL ?? inherited.baseURL) !== 'https://api.deepseek.com/anthropic') {
        throw new Error('本App仅支持DeepSeek官方端点，不能把Windows保存的凭据发送到其他地址。')
      }
      return next
    })
  }
  ctx.effect(() => () => { editor.edit = priorEdit }, 'App: official provider endpoint policy')
  // The provider key is held in Windows/App memory, never in DSH's generic
  // credential editor. The full upstream config document and forms remain.
  await ctx.isolate('credentials').plugin(SettingsController)
  await ctx.plugin(ProfileConfigController)
}
