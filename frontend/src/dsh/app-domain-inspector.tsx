import {useCallback,useEffect,useRef,useState,useSyncExternalStore} from "react";
import {Button} from "@deepseek-ai/dsh-client-ui-primitives";
import type {PropsRuntime} from "@deepseek-ai/dsh-client-ui-slots";
import type {AppCandidateGuiApi,AppDomainBindings} from "./app-domain-candidate";
import type {AppEditorBridge} from "./app-editor-bridge";
import {AppDomainHostError,isAppDomainIncompatibility} from "./app-domain-errors";
import {AppDomainIncompatibilityNotice} from "./app-domain-incompatibility";
import {AppIncompatibleApplications} from "./app-incompatible-applications";
import {AppFileActionCards} from "./app-file-action-cards";
import {AppDomainApprovals} from "./app-domain-approvals";
import {SavedWhiteboardControls} from "./app-saved-whiteboard-controls";
import {copyAppText} from "./app-clipboard";
import type {Context} from "@deepseek-ai/cordis";
import type {PendingQuestion} from "@deepseek-ai/dsh-client-ui-user-questions/client";
import type {} from "@deepseek-ai/dsh-client-ui-approval/client";

type Props={api:AppCandidateGuiApi;bindings:AppDomainBindings;refresh?:()=>void;props:PropsRuntime<"sidebar.right.pane.tab">};
/** A read-only locator. DSH's existing composer is the only answer owner. */
export function AppQuestionsInspector({ctx,props}:{ctx:Context;props:PropsRuntime<"sidebar.right.pane.tab">}){
  const status=props.useSessionStatus(value=>value.get(props.sessionId)),pending=status?.pendingInteraction;
  const title=pending?.kind==="question"?"等待你的回答":pending?.kind==="approval"?"等待普通操作批准":pending?.kind==="plan-review"?"等待计划确认":"当前没有待回答的问题";
  const questions=pending?.kind==="question"||pending?.kind==="plan-review"?(pending as PendingQuestion).questions:undefined;
  return <section className="gs-candidate-workspace" aria-label="问题与批准"><h2>问题与批准</h2><p role="status">{title}</p>
    {questions?.map(question=><article className="gs-domain-card" key={question.id}><strong>{question.question}</strong>{question.detail&&<details><summary>问题详情</summary><p>{question.detail}</p></details>}</article>)}
    {pending&&<><p>请在会话输入区处理；这里不会替你选择答案，也不会把普通批准作为 App 资料修改授权。</p><Button size="sm" onClick={()=>ctx.uiWorkspace.openSession(props.sessionId)}>打开会话并处理</Button></>}
  </section>;
}
export function AppOperationsInspector({api,bindings,refresh,props}:Props){
  const subscribe=useCallback((listener:()=>void)=>bindings.subscribe(listener),[bindings]),get=useCallback(()=>bindings.getSnapshot(props.sessionId),[bindings,props.sessionId]);
  const binding=useSyncExternalStore(subscribe,get);useEffect(()=>bindings.retain?.(props.sessionId),[bindings,props.sessionId]);
  const getError=useCallback(()=>bindings.getError?.(props.sessionId),[bindings,props.sessionId]),bindingError=useSyncExternalStore(subscribe,getError);
  const editor=(window as unknown as {GenshinEditorBridge?:AppEditorBridge}).GenshinEditorBridge;
  const scope=binding?`${binding.dshSessionId}:${binding.conversationId}`:undefined;
  return <section className="gs-candidate-workspace" aria-label="文件与 App 操作"><h2>文件与 App 操作</h2><p>文件由你选择位置和确认保存。这里的待办来自 App，不会替代普通聊天里的问题与批准。</p>
    <AppIncompatibleApplications api={api} onSettled={refresh}/>
    {!binding?isAppDomainIncompatibility(bindingError)?<AppDomainIncompatibilityNotice key={props.sessionId} error={bindingError}/>:bindingError?<p role="alert">{bindingError.message}</p>:<p>此会话尚未绑定 App 候选；可以继续在聊天中描述任务。</p>:<>
      {api.dsh_client_app_actions&&api.dsh_client_app_action&&<AppFileActionCards key={`files:${scope}`} api={api} binding={binding} bindings={bindings} files={editor?.files} onSettled={refresh}/>}
      {api.dsh_client_domain_approvals&&api.dsh_client_domain_approval_decide&&<AppDomainApprovals key={`approvals:${scope}`} api={api} binding={binding} bindings={bindings} onSettled={refresh}/>}
      {api.dsh_client_saved_whiteboard&&<SavedWhiteboardControls key={`saved:${scope}`} api={api} binding={binding} editor={editor}/>}
    </>}
  </section>;
}
export function AppDiagnosticsInspector({bindings,props}:Omit<Props,"api"|"refresh">){
  const openError=props.useSession(value=>value.openError),promptError=props.useSession(value=>value.promptError),agentError=props.useSession(value=>value.lastAgentError);
  const subscribe=useCallback((listener:()=>void)=>bindings.subscribe(listener),[bindings]),get=useCallback(()=>bindings.getError?.(props.sessionId),[bindings,props.sessionId]);
  const appError=useSyncExternalStore(subscribe,get);useEffect(()=>bindings.retain?.(props.sessionId),[bindings,props.sessionId]);
  const [copyStatus,setCopyStatus]=useState<{text:string;copied:boolean}>();
  const incompatibility=isAppDomainIncompatibility(appError);
  const rows=[...(openError?[{label:"会话历史读取失败",code:openError.code,message:openError.message}]:[]),...(promptError?[{label:promptError.op==="send"?"消息发送失败":"停止操作未完成",code:promptError.error.code,message:promptError.error.message}]:[]),...(agentError?[{label:"本轮运行报告了错误",code:"agent_error",message:agentError}]:[]),...(appError?[{label:incompatibility?"排轴候选与当前版本不兼容":"App 文档或候选暂时不可用",code:appError instanceof AppDomainHostError?appError.code:"app_domain_error",message:appError.message}]:[])];
  const summary=JSON.stringify({dsh_session_id:props.sessionId,errors:rows},null,2);
  const currentSummary=useRef(summary);currentSummary.current=summary;
  const status=copyStatus?.text===summary?copyStatus:undefined;
  return <section className="gs-candidate-workspace" aria-label="会话诊断"><h2>会话诊断</h2><p>这里显示当前会话与 App 的错误事实，不复制推理、工具正文或密钥。</p>{incompatibility&&<AppDomainIncompatibilityNotice key={props.sessionId} error={appError}/>} {rows.length?rows.filter(row=>!incompatibility||row.code!==(appError as AppDomainHostError).code).map((row,index)=><article key={`${row.code}:${index}`} className="gs-domain-card"><strong>{row.label}</strong><p>{row.message}</p><details><summary>错误详情</summary><code>{row.code}</code></details></article>):<p role="status">当前没有已报告的会话或 App 错误。</p>}
    <Button size="sm" variant="outline" onClick={()=>void copyAppText(summary).then(copied=>{if(currentSummary.current===summary)setCopyStatus({text:summary,copied});})}>{status?.copied?"已复制诊断摘要":"复制诊断摘要"}</Button>
    {status?.copied===false&&<><p role="alert">剪贴板暂不可用，可选中下方内容手动复制。</p><pre style={{userSelect:"text"}}>{summary}</pre></>}
  </section>;
}
