import type {Context} from "@deepseek-ai/cordis";
import type {SessionSummary} from "@deepseek-ai/dsh-api-session-controller/client";
import type {SessionStatus} from "@deepseek-ai/dsh-client-ui-session/client";
import type {} from "@deepseek-ai/dsh-client-ui-workspace/client";
import type {PropsRuntime} from "@deepseek-ai/dsh-client-ui-slots";
import {useCallback,useEffect,useRef,useState,useSyncExternalStore} from "react";
import {Button} from "@deepseek-ai/dsh-client-ui-primitives";
import type {AppCandidateGuiApi,AppDomainBinding,AppDomainBindings} from "./app-domain-candidate";
import {assertAppDomainSuccess,isAppDomainIncompatibility} from "./app-domain-errors";
import {useAppDomainRead} from "./app-domain-read";
import {parseAppActions} from "./app-file-actions";
import {parseDomainApprovals} from "./app-domain-approvals";

type Row=Record<string,unknown>;
export type TaskFacts=Readonly<{sessionId:string;lastTurn:{turn:number;reason:Row}|null;agentRunning:boolean|null;admission:Row;accounting:Row;reviewPlugin?:Row}>;
const object=(value:unknown):Row=>{if(!value||typeof value!=="object"||Array.isArray(value))throw new Error("app_task_status_invalid");return value as Row;};
export function taskFacts(value:unknown,id:string):TaskFacts{
  assertAppDomainSuccess(value);const row=object(value);if(row.session_id!==id||(row.agent_running!==null&&typeof row.agent_running!=="boolean"))throw new Error("app_task_status_invalid");
  const last=row.last_turn===null?null:object(row.last_turn);
  if(last&&(!Number.isSafeInteger(last.turn)||(last.turn as number)<0||typeof object(last.reason).kind!=="string"))throw new Error("app_task_status_invalid");
  return {sessionId:id,lastTurn:last as TaskFacts["lastTurn"],agentRunning:row.agent_running as boolean|null,admission:object(row.admission),accounting:object(row.accounting),
    reviewPlugin:row.task_review_plugin?object(row.task_review_plugin):undefined};
}
export function taskRoundLabel(summary:Pick<SessionSummary,"blank"|"running">,status:SessionStatus|undefined,facts:TaskFacts|undefined){
  const pending=status?.pendingInteraction?.kind;
  if(pending==="question")return "等待回答";
  if(pending==="approval")return "等待普通批准";
  if(pending==="plan-review")return "等待计划确认";
  if(facts?.admission.state==="queued")return "等待运行资源";
  if(status?.running??facts?.agentRunning??summary.running)return "运行中";
  const values=(summary as SessionSummary).projectionValues as Record<string,unknown>|undefined,inbox=values?.inbox as Record<string,unknown>|undefined;
  if(Array.isArray(inbox?.["next-turn"])&&inbox["next-turn"].length)return "消息排队中";
  if(summary.blank)return "未开始";
  if(!facts?.lastTurn)return "尚无已核实轮次结果";
  return ({completed:"本轮结束",error:"本轮失败",aborted:"本轮已停止",blocked:"本轮受阻",interrupted:"上次运行中断","max-tokens":"本轮输出达到限制",forked:"分支记录已结束"} as Record<string,string>)[String(facts.lastTurn.reason.kind)]||"本轮已结束，详情待核对";
}
function TaskAppStatus({api,bindings,binding}:{api:AppCandidateGuiApi;bindings:AppDomainBindings;binding:AppDomainBinding}){
  const actions=useAppDomainRead(bindings,binding,"file-actions",async()=>api.dsh_client_app_actions?parseAppActions(await api.dsh_client_app_actions({conversation_id:binding.conversationId})):[]);
  const approvals=useAppDomainRead(bindings,binding,"domain-approvals",async()=>api.dsh_client_domain_approvals?parseDomainApprovals(await api.dsh_client_domain_approvals({conversation_id:binding.conversationId})):[]);
  const files=actions.value?.filter(row=>['queued','presented','claimed_for_external_execution'].includes(row.status)).length;
  return <p>{binding.applicationState==="committed"?"候选此前已同步":binding.availableCommands.some(row=>row.enabled&&["candidate.review","candidate.review.new"].includes(row.commandId))?"候选可审核":['prepared','baseline_advanced'].includes(binding.applicationState||"")?"同步状态需要核对":"候选仍在完善"}{files?` · ${files} 项 App 操作`:""}{approvals.value?.length?` · ${approvals.value.length} 项资料修正确认`:""}{actions.error||approvals.error?" · 部分 App 待办暂不可读":""}</p>;
}
function TaskRow({ctx,api,bindings,summary,status,facts,error}:{ctx:Context;api:AppCandidateGuiApi;bindings:AppDomainBindings;summary:SessionSummary;status?:SessionStatus;facts?:TaskFacts;error?:string}){
  const subscribe=useCallback((listener:()=>void)=>bindings.subscribe(listener),[bindings]),get=useCallback(()=>bindings.getSnapshot(summary.id),[bindings,summary.id]);
  const binding=useSyncExternalStore(subscribe,get);useEffect(()=>bindings.retain?.(summary.id),[bindings,summary.id]);
  const getError=useCallback(()=>bindings.getError?.(summary.id),[bindings,summary.id]),bindingError=useSyncExternalStore(subscribe,getError);
  const reason=facts?.lastTurn?.reason;
  return <article className="gs-domain-card"><header><strong>{summary.blank?"未开始的会话":summary.displayTitle}</strong><span>{taskRoundLabel(summary,status,facts)}</span></header>
    {binding&&<TaskAppStatus api={api} bindings={bindings} binding={binding}/>}
    {isAppDomainIncompatibility(bindingError)&&<p>排轴候选不兼容，聊天、历史和资料搜索仍可继续。请在会话中的兼容说明查看适配提示。</p>}
    {error&&<p role="alert">这一会话的详细状态暂时不可读，其他会话仍可查看。</p>}
    <div className="gs-candidate-toolbar"><Button size="sm" variant="outline" onClick={()=>ctx.uiWorkspace.openSession(summary.id)}>打开会话{status?.pendingInteraction?"并处理待办":""}</Button></div>
    {facts&&<details><summary>轮次、用量与资源详情</summary><p>模型轮结束不代表候选已计算、审核或同步。</p><pre>{JSON.stringify({last_turn:facts.lastTurn,admission:facts.admission,accounting:facts.accounting},null,2)}</pre>{reason?.kind==="error"&&<p role="alert">本轮记录了错误，请在会话诊断中查看完整原因。</p>}</details>}
    {facts?.reviewPlugin&&<details><summary>任务对照扩展</summary>
      <p>{facts.reviewPlugin.task_review_loaded?"已有扩展加载记录。":"尚无扩展加载记录。"}</p>
      {Boolean(facts.reviewPlugin.task_review_binding)&&<p>工作资料绑定状态已记录。</p>}
      {Boolean(facts.reviewPlugin.task_review_context_assembled||facts.reviewPlugin.task_review_domain_context_assembled)&&<p>已有模型上下文组装记录。</p>}
      <pre>{JSON.stringify(facts.reviewPlugin,null,2)}</pre>
    </details>}
  </article>;
}
const taskViews=new WeakMap<AppDomainBindings,{page:number;cache:Map<string,{key:string;facts?:TaskFacts;error?:string}>}>();
/** Cross-Session read model only; DSH owns all lifecycle and queue truth. */
export function AppTaskCenter({ctx,api,bindings,props}:{ctx:Context;api:AppCandidateGuiApi;bindings:AppDomainBindings;props:PropsRuntime<"main">}){
  let saved=taskViews.get(bindings);if(!saved){saved={page:0,cache:new Map()};taskViews.set(bindings,saved);}
  const list=props.useSessions(value=>value),statuses=props.useSessionStatus(value=>value),workspaces=props.useWorkspaces(value=>value),[page,setPage]=useState(saved.page),[version,setVersion]=useState(0);
  const cache=useRef(saved.cache),[,repaint]=useState(0);
  useEffect(()=>{const view=taskViews.get(bindings);if(view)view.page=page;},[bindings,page]);
  const rows=list.ids.map(id=>list.byId[id]!).filter(row=>!row.origin&&!workspaces.archivedSessionIds.includes(row.id)).sort((a,b)=>b.updatedAt-a.updatedAt),visible=rows.slice(page*20,page*20+20);
  const key=visible.map(row=>`${row.id}:${row.updatedAt}:${row.running}:${statuses.get(row.id)?.running}:${statuses.get(row.id)?.pendingInteraction?.key||""}`).join("|");
  useEffect(()=>{
    if(!api.dsh_client_session_status)return;let live=true,next=0;
    const jobs=visible.map(row=>({id:row.id,key:`${row.updatedAt}:${row.running}:${statuses.get(row.id)?.running}:${statuses.get(row.id)?.pendingInteraction?.key||""}:${version}`}));
    const worker=async()=>{while(live&&next<jobs.length){const job=jobs[next++]!;if(cache.current.get(job.id)?.key===job.key)continue;
      try{const facts=taskFacts(await api.dsh_client_session_status!({dsh_session_id:job.id}),job.id);if(live){cache.current.delete(job.id);cache.current.set(job.id,{key:job.key,facts});if(cache.current.size>128)cache.current.delete(cache.current.keys().next().value!);repaint(value=>value+1);}}
      catch(reason){if(live){cache.current.set(job.id,{key:job.key,error:reason instanceof Error?reason.message:"app_task_status_failed"});repaint(value=>value+1);}}
    }};
    // Mirrors pinned DSH persistedReadConcurrency=4; this is a bounded UI
    // read pool, not an Agent/task scheduler or a provider-work budget.
    void Promise.all(Array.from({length:Math.min(4,jobs.length)},worker));return()=>{live=false;};
  },[api,key,version]);
  useEffect(()=>{if(page>0&&page*20>=rows.length)setPage(0);},[page,rows.length]);
  return <section className="gs-candidate-workspace gs-task-center" aria-label="任务中心"><header><h2>任务中心</h2><p>集中查看运行、资源排队、问题与 App 候选。这里不会启动或自动恢复任何模型任务。</p></header><div className="gs-candidate-toolbar"><span>{rows.length} 个会话</span><Button size="sm" variant="outline" onClick={()=>setVersion(value=>value+1)}>刷新详细状态</Button></div>
    {list.phase!=="ready"&&<p role="status">正在同步会话目录…</p>}{!rows.length&&<p>还没有会话。可以新建白板，也可以直接向 AI 描述目标。</p>}
    {visible.map(row=><TaskRow key={row.id} ctx={ctx} api={api} bindings={bindings} summary={row} status={statuses.get(row.id)} facts={cache.current.get(row.id)?.facts} error={cache.current.get(row.id)?.error}/>)}
    <div className="gs-candidate-toolbar"><Button size="sm" variant="outline" disabled={!page} onClick={()=>setPage(value=>value-1)}>上一页</Button><span>第 {page+1} 页</span><Button size="sm" variant="outline" disabled={(page+1)*20>=rows.length} onClick={()=>setPage(value=>value+1)}>下一页</Button></div>
  </section>;
}
