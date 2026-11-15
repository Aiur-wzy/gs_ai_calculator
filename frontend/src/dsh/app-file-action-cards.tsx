import {useEffect,useRef,useState} from "react";
import {Button,Modal} from "@deepseek-ai/dsh-client-ui-primitives";
import type {AppCandidateGuiApi,AppDomainBinding,AppDomainBindings} from "./app-domain-candidate";
import type {AppEditorFilePort,AppFilePhase} from "./app-editor-bridge";
import {useAppDomainRead} from "./app-domain-read";
import {APP_ACTION_LABELS,AppActionExecutionPending,AppActionSettlementPending,executeAppAction,parseAppActions,recoverAppAction,settleAppAction,type AppAction} from "./app-file-actions";

export function AppFileActionCards({api,binding,bindings,files,onSettled,focusActionId,onFocused}:{api:AppCandidateGuiApi;binding:AppDomainBinding;bindings:AppDomainBindings;files?:AppEditorFilePort;onSettled?:()=>void;focusActionId?:string;onFocused?:()=>void}){
  const state=useAppDomainRead(bindings,binding,"file-actions",async()=>parseAppActions(await api.dsh_client_app_actions!({conversation_id:binding.conversationId})));
  const [busy,setBusy]=useState<string>(),[message,setMessage]=useState(""),[error,setError]=useState(""),[pending,setPending]=useState<AppActionSettlementPending>(),[recovery,setRecovery]=useState<AppAction>();
  const running=useRef(false),[phase,setPhase]=useState<AppFilePhase>();
  const section=useRef<HTMLElement>(null);
  useEffect(()=>{
    if(!focusActionId||!section.current)return;
    const card=[...section.current.querySelectorAll<HTMLElement>("[data-app-action-id]")].find(element=>element.dataset.appActionId===focusActionId);
    if(card){card.focus();card.scrollIntoView?.({block:"nearest"});onFocused?.();}
  },[focusActionId,state.value,onFocused]);
  const perform=async(operation:()=>Promise<AppAction>,id:string)=>{
    if(running.current)return;
    running.current=true;setBusy(id);setPhase(undefined);setError("");setMessage("");
    try{
      const settled=await operation();setPending(undefined);setRecovery(undefined);
      setMessage(settled.result_message||actionStatus(settled));
    }catch(reason){
      if(reason instanceof AppActionSettlementPending){
        setPending(reason);setMessage(`${reason.outcome.message} 操作记录尚未更新，可只补记结果。`);
      }else if(reason instanceof AppActionExecutionPending){
        setPending(undefined);
        setMessage(reason.reason==="app_action_written_unverified"?"文件已写入，内容尚未核实。请先检查所选文件。":"暂无法确认文件操作结果。请先核对文件与操作记录，系统不会自动重做。");
      }else{
        setMessage("操作未完成，请展开详情查看原因，或刷新当前操作状态。");
      }
      setError(reason instanceof AppActionExecutionPending?reason.reason||reason.message:reason instanceof Error?reason.message:"app_action_failed");
    }finally{running.current=false;setBusy(undefined);setPhase(undefined);state.refresh();onSettled?.();}
  };
  const rows=state.value?.filter(row=>['queued','presented','claimed_for_external_execution'].includes(row.status))||[];
  const history=state.value?.filter(row=>['completed','cancelled','failed','expired'].includes(row.status)).slice().reverse()||[];
  if(!rows.length&&!history.length&&!state.error&&!message)return null;
  return <section ref={section} className="gs-app-file-actions" aria-label="App 文件与页面操作">
    {rows.map(action=><div className="gs-domain-card" key={action.action_id} data-app-action-id={action.action_id} tabIndex={-1}><strong>{APP_ACTION_LABELS[action.action]}</strong><p>{action.reason}</p>
      {busy===action.action_id?<p role="status">{phase==="choosing"?"等待选择保存位置…":phase==="writing"?"正在写入并核实文件…":"正在处理…"}</p>:
        action.legacy_read_only?<p>旧版本操作仅供查看；本版不会执行或重放。</p>:
        action.status==="claimed_for_external_execution"?<><p>{actionStatus(action)}</p>{action.draft_publication?.status==="verified"?
          <Button size="sm" variant="outline" disabled={!!busy} onClick={()=>void perform(()=>recoverAppAction(api,binding,action),action.action_id)}>补记已保存结果</Button>:
          <Button size="sm" variant="outline" disabled={!!busy} onClick={()=>setRecovery(action)}>核对未知操作</Button>}</>:
        <Button size="sm" variant="outline" disabled={!!busy||!files} onClick={()=>files&&void perform(()=>executeAppAction(api,binding,files,action,setPhase),action.action_id)}>处理这项操作</Button>}
      <DraftFileDetails action={action}/>
    </div>)}
    {(message||state.error)&&<p role={error||state.error?"alert":"status"}>{state.error?"暂时无法读取 App 操作，可刷新后重试。":message}</p>}
    {pending&&<Button size="sm" variant="outline" disabled={!!busy} onClick={()=>void perform(()=>settleAppAction(api,binding,pending.action,pending.receipt,pending.outcome,files),pending.action.action_id)}>只补记操作结果</Button>}
    {(error||state.error)&&<details><summary>操作详情</summary><code>{error||state.error}</code><Button size="sm" variant="outline" disabled={!!busy} onClick={state.refresh}>刷新状态</Button></details>}
    {history.length>0&&<details><summary>文件与页面操作记录（{history.length}）</summary>{history.map(action=><article key={action.action_id} className="gs-domain-card">
      <strong>{APP_ACTION_LABELS[action.action]}</strong><p>{action.result_message||actionStatus(action)}</p><DraftFileDetails action={action}/>
    </article>)}</details>}
    <Modal open={!!recovery} title="核对未确认的 App 操作" description="文件或页面操作可能已经发生。关闭未知状态不会再次执行原操作，也不会把它当成成功。" closeLabel="关闭未知操作确认" onClose={()=>{if(!busy)setRecovery(undefined);}}
      footer={<><Button variant="outline" disabled={!!busy} onClick={()=>setRecovery(undefined)}>返回核对</Button><Button disabled={!!busy} onClick={()=>recovery&&void perform(()=>recoverAppAction(api,binding,recovery),recovery.action_id)}>关闭未知状态，不重做操作</Button></>}>
      <p>{recovery?APP_ACTION_LABELS[recovery.action]:""}</p><p>{recovery?.reason}</p>
    </Modal>
  </section>;
}

function actionStatus(action:AppAction):string{
  if(action.draft_publication?.status==="verified")return action.status==="completed"?"候选输入草稿已保存。":"候选输入草稿已保存，操作记录待补记。";
  if(action.draft_publication?.status==="written_unverified")return "文件已写入，内容尚未核实。请先检查所选文件。";
  switch(action.status){
    case "completed":return "操作已完成。";
    case "cancelled":return "已取消操作。";
    case "failed":return "操作未完成。";
    case "expired":return "操作请求已失效。";
    default:return "暂无法确认上次操作结果，系统不会自动重复执行。";
  }
}

function DraftFileDetails({action}:{action:AppAction}){
  if(action.target?.kind!=="candidate_input_draft")return null;
  return <><small>候选版本 {String(action.target.candidate_revision)} · 仅保存输入，不更新正式白板</small>
    {action.draft_publication?.path&&<p>文件 <code className="gs-domain-prewrap">{action.draft_publication.path}</code></p>}</>;
}
