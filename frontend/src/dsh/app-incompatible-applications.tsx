import {useEffect,useRef,useState} from "react";
import {Button,Modal} from "@deepseek-ai/dsh-client-ui-primitives";
import type {AppCandidateGuiApi} from "./app-domain-candidate";
import {AppDomainHostError,assertAppDomainSuccess} from "./app-domain-errors";

export type IncompatibleApplication=Readonly<{conversationId:string;fingerprint:string;message:string;displayName?:string}>;
const CHANGED="genshin-incompatible-applications-changed";
const object=(value:unknown):Record<string,unknown>=>{if(!value||typeof value!=="object"||Array.isArray(value))throw new Error("app_incompatible_applications_invalid");return value as Record<string,unknown>;};
function unwrap(value:unknown):Record<string,unknown>{assertAppDomainSuccess(value);const envelope=object(value);if(envelope.code!==0)throw new Error("app_incompatible_applications_response_invalid");return object(envelope.data);}
export function parseIncompatibleApplications(value:unknown):IncompatibleApplication[]{
  const data=unwrap(value);if(!Array.isArray(data.items))throw new Error("app_incompatible_applications_invalid");
  const ids=new Set<string>();return data.items.map(value=>{
    const row=object(value);if(typeof row.conversation_id!=="string"||!/^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$/.test(row.conversation_id)||ids.has(row.conversation_id)||row.state!=="result_unknown"||typeof row.expected_fingerprint!=="string"||!/^[0-9a-f]{64}$/.test(row.expected_fingerprint))throw new Error("app_incompatible_applications_invalid");
    ids.add(row.conversation_id);return {conversationId:row.conversation_id,fingerprint:row.expected_fingerprint,message:typeof row.message==="string"?row.message:"旧版本同步未确认，正在占用正式白板同步入口。",...(typeof row.display_name==="string"&&row.display_name?{displayName:row.display_name}:{})};
  });
}
function errorCode(error:unknown):string{return error instanceof AppDomainHostError?error.code:error instanceof Error?error.message:"app_incompatible_application_failed";}

/** Global App recovery facts. Never a DSH approval, turn or task owner. */
export function AppIncompatibleApplications({api,onSettled}:{api:AppCandidateGuiApi;onSettled?:()=>void}){
  const [version,setVersion]=useState(0),[items,setItems]=useState<IncompatibleApplication[]>([]),[loading,setLoading]=useState(false),[error,setError]=useState(""),[message,setMessage]=useState("");
  const [selected,setSelected]=useState<IncompatibleApplication>(),[busy,setBusy]=useState(false),mutation=useRef(false);
  const [mustRefresh,setMustRefresh]=useState(false);
  const available=!!api.dsh_client_incompatible_applications&&!!api.dsh_client_abandon_incompatible_application;
  useEffect(()=>{const update=()=>setVersion(value=>value+1);document.addEventListener(CHANGED,update);return()=>document.removeEventListener(CHANGED,update);},[]);
  useEffect(()=>{
    if(!available)return;let live=true;setLoading(true);
    void api.dsh_client_incompatible_applications!({}).then(parseIncompatibleApplications).then(value=>{if(live){setItems(value);setError("");setMustRefresh(false);}}).catch(reason=>{if(live){setError(errorCode(reason));setMustRefresh(true);}}).finally(()=>{if(live)setLoading(false);});
    return()=>{live=false;};
  },[api,available,version]);
  const abandon=async()=>{
    if(!selected||mutation.current||mustRefresh||!api.dsh_client_abandon_incompatible_application)return;
    mutation.current=true;setBusy(true);setError("");setMessage("");
    try{
      const data=unwrap(await api.dsh_client_abandon_incompatible_application({conversation_id:selected.conversationId,expected_fingerprint:selected.fingerprint,decision:"abandon_recovery"}));
      if(data.conversation_id!==selected.conversationId||data.status!=="recovery_abandoned"||data.state!=="result_unknown"||data.decision_receipt!==selected.fingerprint||!["recorded","replay"].includes(String(data.disposition)))throw new Error("app_incompatible_application_receipt_invalid");
      setSelected(undefined);setMessage("已放弃恢复这次旧同步并解除占用。原同步结果仍记为未知，文件和当前白板未作修改。");document.dispatchEvent(new Event(CHANGED));onSettled?.();
    }catch(reason){setSelected(undefined);setMustRefresh(true);setError(errorCode(reason));setMessage("本次处理没有得到成功确认。请刷新旧同步状态后核对，系统不会自动重复提交；普通聊天仍可继续。");}
    finally{mutation.current=false;setBusy(false);}
  };
  if(!available||(!items.length&&!error&&!message))return null;
  return <section className="gs-domain-card" aria-label="旧版本同步处理"><details>
    <summary>{items.length?`${items.length} 项旧同步待处理` :"旧同步处理结果"}</summary>
    <p>旧版本一次未确认的同步可能占用新的正式白板同步入口。你可以保留它，也可以明确放弃恢复。这个操作不会确认原同步成功，不会修改文件或当前白板。</p>
    {items.map(row=><article className="gs-domain-card" key={row.conversationId}><strong>{row.displayName||"旧版本未确认的同步"}</strong><p>{row.message}</p><details><summary>技术详情</summary><code>{row.conversationId}</code><p>结果：仍未知</p></details><Button size="sm" variant="outline" disabled={busy||loading||mustRefresh} onClick={()=>{setSelected(row);setError("");}}>处理这次旧同步</Button></article>)}
    {message&&<p role={error?"alert":"status"}>{message}</p>}
    {error&&<><p role="alert">{error==="native_incompatible_application_stale"?"这次旧同步记录已变化，请刷新后重新核对。":error==="native_incompatible_application_not_eligible"?"这次同步不属于可放弃恢复的旧版本同步，已保留原记录。":"旧同步状态暂时无法确认；其他聊天与候选内容仍保留。"}</p><details><summary>操作详情</summary><code>{error}</code></details></>}
    <Button size="sm" variant="outline" disabled={busy||loading} onClick={()=>setVersion(value=>value+1)}>{loading?"正在读取…":"刷新旧同步状态"}</Button>
  </details><Modal open={!!selected} title="放弃恢复这次旧同步？" closeLabel="取消处理旧同步" onClose={()=>{if(!busy)setSelected(undefined);}}
    className="gs-domain-dialog" contentClassName="gs-domain-dialog-content"
    description="这只解除旧同步的占用，不代表原同步成功或失败。原结果仍记为未知，文件和当前白板不会改变。"
    footer={<div className="gs-domain-dialog-actions"><Button variant="outline" disabled={busy} onClick={()=>setSelected(undefined)}>保留并取消</Button><Button disabled={busy||mustRefresh||loading} onClick={()=>void abandon()}>{busy?"正在记录…":"确认放弃恢复并解除占用"}</Button></div>}>
    <p>放弃后，不能再用这条旧同步记录自动恢复。需要继续旧排轴时，请在新会话提供旧文件并重新计算。</p>
    {selected&&<p>{selected.displayName||"旧版本未确认的同步"}</p>}
    {mustRefresh&&<p role="alert">旧同步状态需要重新核对。请取消本次确认，刷新后再处理。</p>}
  </Modal></section>;
}
