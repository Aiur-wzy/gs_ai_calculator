import type {Context} from "@deepseek-ai/cordis";
import type {} from "@deepseek-ai/dsh-client-ui-settings/client";
import {Button} from "@deepseek-ai/dsh-client-ui-primitives";
import {useEffect, useRef, useState} from "react";

export interface AppCredentialHostApi {
  dsh_client_credential_status(): Promise<unknown>;
  dsh_client_credential_save(request: {api_key: string; expected_generation: number}): Promise<unknown>;
  dsh_client_credential_delete(request: {expected_generation: number}): Promise<unknown>;
  dsh_client_runtime_status(): Promise<unknown>;
}
type CredentialState = {available:boolean; saved:boolean; status?:string; credential_generation:number|null; runtime_sync?:{state:string}};
function credentialState(value:unknown):CredentialState {
  const row=value as CredentialState|null;
  if(!row||typeof row.available!=="boolean"||typeof row.saved!=="boolean"
    ||(row.credential_generation===null?(row.saved||!["error","unavailable"].includes(row.status||"")):!Number.isSafeInteger(row.credential_generation)||row.credential_generation<0))throw new Error("credential_status_invalid");
  return row;
}

export function WindowsCredentialSettings({api}:{api:AppCredentialHostApi}) {
  const [state,setState]=useState<CredentialState>(),[key,setKey]=useState(""),[busy,setBusy]=useState(false),[message,setMessage]=useState(""),[failed,setFailed]=useState(false);
  const generation=useRef(0);
  const refresh=async()=>{
    const request=++generation.current;setBusy(true);setMessage("");
    try{const value=credentialState(await api.dsh_client_credential_status());if(request===generation.current){setState(value);setFailed(false);}}
    catch{if(request===generation.current){setFailed(true);setMessage("暂时无法读取 Windows 密钥状态，请刷新后重试。");}}
    finally{if(request===generation.current)setBusy(false);}
  };
  useEffect(()=>{void refresh();return()=>{generation.current++;};},[api]);
  const change=async(remove:boolean)=>{
    if(busy||!state||state.credential_generation===null)return;
    const request=++generation.current;
    const payload={api_key:key,expected_generation:state.credential_generation};setKey("");setBusy(true);setMessage("");
    try{
      const value=credentialState(await(remove?api.dsh_client_credential_delete({expected_generation:payload.expected_generation}):api.dsh_client_credential_save(payload)));
      if(request!==generation.current)return;
      setState(value);const syncFailed=value.runtime_sync?.state==="failed";setFailed(syncFailed);
      setMessage(syncFailed?"Windows 中的密钥已更新，但当前 AI 服务未同步。请重新打开应用后再发送消息。":remove?"已从 Windows 删除保存的密钥。":"密钥已安全保存到 Windows。以后打开应用可以继续使用。");
    }catch{if(request===generation.current){setFailed(true);setMessage("密钥未能更新。请刷新状态后重试；密钥内容不会显示在错误信息中。");}}
    finally{payload.api_key="";if(request===generation.current)setBusy(false);}
  };
  return <section className="gs-credential-settings" aria-label="DeepSeek 密钥">
    <h2>DeepSeek 密钥</h2>
    <p>密钥安全保存在 Windows 中。这里只管理本 App 的 DeepSeek 官方服务，不会读取或显示已保存的密钥。</p>
    <p role="status">{state?state.status==="error"?"无法读取 Windows 安全存储，请刷新状态后重试。":state.available?(state.saved?"已保存密钥":"尚未保存密钥"):"当前环境不支持 Windows 安全存储。":"正在读取状态…"}</p>
    <form onSubmit={event=>{event.preventDefault();void change(false);}}>
      <label>API Key<input type="password" autoComplete="new-password" aria-label="DeepSeek API Key" value={key} onChange={event=>setKey(event.target.value)} disabled={busy||!state?.available||state?.credential_generation==null} placeholder={state?.saved?"输入新密钥以替换":"输入 DeepSeek API Key"}/></label>
      <div className="gs-candidate-toolbar">
        <Button type="submit" disabled={busy||!state?.available||state?.credential_generation==null||!key.trim()}>安全保存</Button>
        <Button type="button" variant="outline" onClick={()=>void refresh()} disabled={busy}>刷新状态</Button>
        <Button type="button" variant="outline" onClick={()=>void change(true)} disabled={busy||!state?.saved}>删除密钥</Button>
      </div>
    </form>
    {message&&<p role={failed?"alert":"status"}>{message}</p>}
    <p>打开设置和保存密钥不会发起模型请求。模型与推理档位在消息输入区选择。</p>
  </section>;
}

export function createAppCredentialSettingsPlugin(api:AppCredentialHostApi) {
  return {name:"genshin-windows-credentials",inject:["slots"],apply(ctx:Context){
    ctx.slots.inject("settings.section",()=>ctx.slots.register({name:"settings.section",id:"genshin-deepseek",label:"DeepSeek 密钥",order:120},()=> <WindowsCredentialSettings api={api}/>));
  }};
}
