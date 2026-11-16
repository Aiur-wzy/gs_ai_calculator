/** Official settings own model routes and authorization, storage is App-protected. */
import SettingsController from '@deepseek-ai/dsh-api-settings-controller'
import { ProfileConfigController } from './native-dsh-profile-config.mjs'

export const inject = ['settings', 'configEditor', 'typertGateway']
export async function apply(ctx) {
  await ctx.plugin(SettingsController)
  await ctx.plugin(ProfileConfigController)
}
