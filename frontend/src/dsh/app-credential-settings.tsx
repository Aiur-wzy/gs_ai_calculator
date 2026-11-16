import type {Context} from "@deepseek-ai/cordis";
import type {} from "@deepseek-ai/dsh-client-ui-settings/client";

/** Legacy bridge signatures remain available only for explicit old-profile import. */
export interface AppCredentialHostApi {
  dsh_client_credential_status(): Promise<unknown>;
  dsh_client_credential_save(request: {api_key: string; expected_generation: number}): Promise<unknown>;
  dsh_client_credential_delete(request: {expected_generation: number}): Promise<unknown>;
  dsh_client_runtime_status(): Promise<unknown>;
}

export function WindowsCredentialSettings() {
  return <section className="gs-credential-settings" aria-label="连接与凭据说明">
    <h2>连接与凭据</h2>
    <p>在左侧「模型」中添加提供商、API Key 或兼容服务地址，并选择要使用的模型。支持的连接方式由当前 DSH 版本提供。</p>
    <p>搜索服务在「网络搜索和读取」中单独设置。更换模型不会自动转用其他服务的密钥；未配置搜索时，仍可使用本地资料和计算工具。</p>
    <p>密钥和授权记录加密保存在本 App 的资料目录，仅当前 Windows 用户可以解密。保留用户资料时会一并保留；卸载时选择删除资料会一并删除。</p>
    <p>OAuth、云服务身份等连接需要完成各自的授权步骤。外部云凭据由原工具管理，本 App 卸载不会删除它们。</p>
    <p>打开设置和保存密钥不会调用模型。供应商、模型和推理档位在输入区选择；可用参数以所选服务为准。</p>
  </section>;
}

export function createAppCredentialSettingsPlugin(_api:AppCredentialHostApi) {
  return {name:"genshin-protected-credentials",inject:["slots"],apply(ctx:Context){
    ctx.slots.inject("settings.section",()=>ctx.slots.register({name:"settings.section",id:"genshin-credentials",label:"连接与凭据说明",order:120},()=> <WindowsCredentialSettings/>));
  }};
}
