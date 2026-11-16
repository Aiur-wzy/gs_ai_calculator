import type {Context} from "@deepseek-ai/cordis";
import type {PropsRuntime} from "@deepseek-ai/dsh-client-ui-slots";
import type {} from "@deepseek-ai/dsh-client-ui-renderer/client";
import type {} from "@deepseek-ai/dsh-client-ui-layout/client";
import type {} from "@deepseek-ai/dsh-client-ui-session/client";
import type {} from "@deepseek-ai/dsh-client-ui-sidebar/client";
import type {} from "@deepseek-ai/dsh-client-ui-sidebar-right/client";
import type {} from "@deepseek-ai/dsh-client-ui-conversation/client";
import {Button} from "@deepseek-ai/dsh-client-ui-primitives";
import {TaskReviewDetails} from "./app-task-review";
import {OpenCandidateDraft} from "./app-candidate-draft-file";
import {createCandidateDraftFilePort,selectedDraftNotice} from "./app-candidate-draft-selection";
import {useCallback, useEffect, useState, useSyncExternalStore} from "react";
import {
  AppCandidateReadError,
  candidateReviewAvailable,
  childPointer,
  readCandidatePage,
  readCandidateDocument,
  peekCandidateDocument,
  type AppCandidateGuiApi,
  type AppDomainBinding,
  type AppDomainBindings,
  type CandidatePage,
  type CandidateSummary,
} from "./app-domain-candidate";
import stylesheet from "./app-domain-plugin.css?inline";
import {CandidatePreview} from "./app-candidate-preview";
import type {AppEditorBridge} from "./app-editor-bridge";
import {CandidateReviewControls} from "./app-candidate-review";
import {AppDomainApprovals} from "./app-domain-approvals";
import {AppFileActionCards} from "./app-file-action-cards";
import {useAppDomainRead} from "./app-domain-read";
import {parseAppActions} from "./app-file-actions";
import {parseDomainApprovals} from "./app-domain-approvals";
import {SavedWhiteboardControls} from "./app-saved-whiteboard-controls";
import {AppDomainHostError,assertAppDomainSuccess,isAppDomainIncompatibility} from "./app-domain-errors";
import {AppDomainIncompatibilityNotice} from "./app-domain-incompatibility";
import {AppIncompatibleApplications} from "./app-incompatible-applications";
import {AppOperationsInspector,AppDiagnosticsInspector,AppQuestionsInspector} from "./app-domain-inspector";
import {AppTaskCenter} from "./app-task-center";
import {useMainWindowCommands,acknowledgeMainCommand} from "./app-window-presentation";

const executionNumber = new Intl.NumberFormat("zh-CN", {maximumFractionDigits: 3});

function executionStatus(summary: CandidateSummary, applicationState?: AppDomainBinding["applicationState"]): string {
  if (applicationState === "committed") return summary.readyScope === "formal_result"
    ? "计算已接纳，此候选版本此前已同步。当前白板可能已继续修改，请以打开的文档为准。"
    : "此候选版本此前已同步；尚未获得正式计算结果。";
  if (summary.readyScope === "formal_result" && ["prepared", "baseline_advanced", "outcome_unknown"].includes(applicationState ?? "")) {
    return "计算已接纳；上次白板同步的结果尚未确认，请检查未完成的同步。";
  }
  if (summary.readyScope === "draft_sync") return "草稿可审核同步；尚未获得正式计算结果。";
  if (summary.readyScope === "formal_result") return "计算已接纳；同步到白板仍需你的审核。";
  if (summary.execution?.status === "complete") return "执行已结束，正式结果尚未接纳。";
  if (summary.execution?.status === "diagnostic") {
    const state = summary.execution.composition === "ready" ? "输入已组合，" : "";
    return state + (summary.execution.completeWindow === false
      ? "执行未完成，已保留诊断。" : "已保留诊断，可继续完善候选。");
  }
  return "当前候选可继续完善。";
}

function firstExecutionFailure(execution: CandidateSummary["execution"]): string | null {
  const failure = execution?.failure;
  if (!failure) return null;
  const at = failure.time === null ? "" : `${executionNumber.format(failure.time)} 秒，`;
  const action = failure.actionId ?? "动作";
  if (failure.before !== null && failure.required !== null && failure.deficit !== null && failure.deficit > 0) {
    return `${at}${action} 未通过。能量 ${executionNumber.format(failure.before)}／${executionNumber.format(failure.required)}，缺 ${executionNumber.format(failure.deficit)}。`;
  }
  return `${at}${action} 未通过。请查看计算诊断。`;
}
export const APP_CANDIDATE_PANEL = "genshin-candidate-workspace";
export const APP_CANDIDATE_INSPECTOR = "genshin-candidate-inspector";
export const APP_CANDIDATE_PLUGIN_ID = "@genshin-calc/dsh-client-domain";

const LABELS: Record<string, string> = {
  team: "角色与配装", characters: "角色面板", actions: "动作轴", events: "事件", buffs: "效果与增益",
  event_topology: "事件与关联", manual_template: "计算输入", extensions: "扩展资料", title: "标题",
  duration: "轴长", panel: "角色面板", weapon: "武器", artifacts: "圣遗物", rules: "规则", notes: "备注",
};

function AppDomainAttention({bindings,binding,api,onOpen}:{bindings:AppDomainBindings;binding:NonNullable<ReturnType<AppDomainBindings["getSnapshot"]>>;api:AppCandidateGuiApi;onOpen:()=>void}){
  const actions=useAppDomainRead(bindings,binding,"file-actions",async()=>api.dsh_client_app_actions?parseAppActions(await api.dsh_client_app_actions({conversation_id:binding.conversationId})):[]);
  const approvals=useAppDomainRead(bindings,binding,"domain-approvals",async()=>api.dsh_client_domain_approvals?parseDomainApprovals(await api.dsh_client_domain_approvals({conversation_id:binding.conversationId})):[]);
  const count=(actions.value?.filter(row=>['queued','presented','claimed_for_external_execution'].includes(row.status)).length||0)+(approvals.value?.length||0);
  return count?<Button size="sm" variant="outline" onClick={onOpen}>{count} 项 App 操作待处理</Button>:actions.error||approvals.error?<Button size="sm" variant="outline" onClick={onOpen}>查看 App 操作状态</Button>:null;
}

const pendingFileFocus=new WeakMap<AppDomainBindings,{conversationId:string;actionId:string}>();

function HeaderReview({api,binding,onDismiss,onOpenCandidate,refresh}:{api:AppCandidateGuiApi;binding:AppDomainBinding;onDismiss:(message?:string)=>void;onOpenCandidate:(actionId?:string)=>void;refresh?:()=>void}){
  const [summary,setSummary]=useState<CandidateSummary>(),[error,setError]=useState(false);
  useEffect(()=>{
    const abort=new AbortController();
    void readCandidatePage(api,binding,{resource:"candidate",pointer:"",offset:0},abort.signal).then(value=>{
      if(!abort.signal.aborted)setSummary(value.summary);
    }).catch(()=>{if(!abort.signal.aborted)setError(true);});
    return ()=>abort.abort();
  },[api,binding.conversationId,binding.dshSessionId]);
  if(error)return <><p role="alert">暂时无法读取待审核方案。请刷新候选版面。</p><Button size="sm" onClick={()=>{onDismiss();onOpenCandidate();}}>打开候选版面</Button></>;
  if(!summary)return <p role="status">正在读取待审核方案…</p>;
  return <CandidateReviewControls api={api} binding={binding} summary={summary} editor={(window as unknown as {GenshinEditorBridge?:AppEditorBridge}).GenshinEditorBridge}
    autoOpen onDismiss={onDismiss} onOpenCandidate={onOpenCandidate} onSettled={refresh}/>;
}

function CandidateHeaderAction({bindings,api,sessionId,onOpen,refresh}:{bindings:AppDomainBindings;api:AppCandidateGuiApi;sessionId:string;onOpen:()=>void;refresh?:()=>void}) {
  const subscribe=useCallback((listener:()=>void)=>bindings.subscribe(listener),[bindings]);
  const snapshot=useCallback(()=>bindings.getSnapshot(sessionId),[bindings,sessionId]);
  const binding=useSyncExternalStore(subscribe,snapshot);
  const getError=useCallback(()=>bindings.getError?.(sessionId),[bindings,sessionId]);
  const error=useSyncExternalStore(subscribe,getError);
  const [reviewSession,setReviewSession]=useState<string>();
  const [reviewNotice,setReviewNotice]=useState<{sessionId:string;message:string}>();
  useEffect(()=>bindings.retain?.(sessionId),[bindings,sessionId]);
  if(isAppDomainIncompatibility(error))return <Button size="sm" variant="outline" onClick={onOpen}>查看兼容说明</Button>;
  if(!binding||binding.dshSessionId!==sessionId)return null;
  const review=candidateReviewAvailable(binding),pending=binding.applicationState==="prepared"||binding.applicationState==="baseline_advanced";
  const showWorkspace=(actionId?:string)=>{if(actionId)pendingFileFocus.set(bindings,{conversationId:binding.conversationId,actionId});setReviewSession(undefined);onOpen();};
  return <><Button size="sm" variant={review?"primary":"ghost"} onClick={()=>{setReviewNotice(undefined);if(review)setReviewSession(sessionId);else showWorkspace();}}>{review?"审核候选":pending?"检查同步":"候选版面"}</Button>
    {review&&<Button size="sm" variant="ghost" onClick={()=>showWorkspace()}>候选版面</Button>}
    {reviewSession===sessionId&&<HeaderReview key={sessionId} api={api} binding={binding} onDismiss={message=>{setReviewSession(undefined);if(message)setReviewNotice({sessionId,message});}} onOpenCandidate={showWorkspace} refresh={refresh}/>}
    {reviewNotice?.sessionId===sessionId&&<small role="status">{reviewNotice.message}</small>}
    {(api.dsh_client_app_actions||api.dsh_client_domain_approvals)&&<AppDomainAttention api={api} bindings={bindings} binding={binding} onOpen={onOpen}/>}</>;
}

function display(value: unknown): string {
  return typeof value === "string" ? value : JSON.stringify(value, null, 2) ?? "未提供";
}

function label(value: string): string {
  return Object.hasOwn(LABELS, value) ? LABELS[value] : value.replaceAll("~1", "/").replaceAll("~0", "~");
}

const candidateViewStates=new WeakMap<AppDomainBindings,Map<string,{resource:"candidate"|"baseline";presentation:"preview"|"data";pointer:string;offset:number;includeCalculated:boolean}>>();
function CandidateWorkspace({bindings, api, dshSessionId, compact = false,refresh}: {
  bindings: AppDomainBindings;
  api: AppCandidateGuiApi;
  dshSessionId?: string;
  compact?: boolean;
  refresh?:()=>void;
}) {
  const subscribe = useCallback((listener: () => void) => bindings.subscribe(listener), [bindings]);
  const getBinding = useCallback(() => {
    const binding = bindings.getSnapshot(dshSessionId);
    return dshSessionId !== undefined && binding?.dshSessionId !== dshSessionId ? undefined : binding;
  }, [bindings, dshSessionId]);
  const binding = useSyncExternalStore(subscribe, getBinding);
  const openedDraft=useSyncExternalStore(selectedDraftNotice.subscribe,()=>binding?selectedDraftNotice.snapshot(binding.dshSessionId):undefined);
  const getBindingError = useCallback(() => bindings.getError?.(dshSessionId), [bindings, dshSessionId]);
  const bindingError = useSyncExternalStore(subscribe, getBindingError);
  useEffect(() => dshSessionId === undefined ? undefined : bindings.retain?.(dshSessionId), [bindings, dshSessionId]);
  const [resource, setResource] = useState<"candidate" | "baseline">("candidate");
  const [presentation,setPresentation]=useState<"preview"|"data">("preview");
  const [snapshot,setSnapshot]=useState<{identity:string;scope:string;document:Record<string,unknown>;calculated?:Record<string,unknown>;summary:CandidateSummary}>();
  const [pointer, setPointer] = useState("");
  const [offset, setOffset] = useState(0);
  const [includeCalculated,setIncludeCalculated]=useState(false),[viewScope,setViewScope]=useState("");
  const [reload, setReload] = useState(0);
  const [loaded, setLoaded] = useState<{
    identity: string;
    page: CandidatePage;
    summary: CandidateSummary;
  }>();
  const [error, setError] = useState<Error>();
  const [loading, setLoading] = useState(false);
  const [draftFileBusy,setDraftFileBusy]=useState(false),[draftFileMessage,setDraftFileMessage]=useState("");
  const identity = binding ? `${binding.dshSessionId}:${binding.conversationId}:${binding.projectionRevision}:${resource}` : "";
  const scope=binding?`${binding.dshSessionId}:${binding.conversationId}:${resource}`:"";
  const workspaceKey=binding?`${binding.dshSessionId}:${binding.conversationId}:${compact?"side":"main"}`:"";
  useEffect(() => {
    const restored=candidateViewStates.get(bindings)?.get(workspaceKey);
    setResource(restored?.resource||"candidate");setPresentation(restored?.presentation||"preview");
    setPointer(restored?.pointer||"");setOffset(restored?.offset||0);setIncludeCalculated(restored?.includeCalculated||false);
    setViewScope(workspaceKey);
  }, [bindings,workspaceKey]);
  useEffect(()=>{
    if(!workspaceKey||viewScope!==workspaceKey)return;
    let saved=candidateViewStates.get(bindings);if(!saved){saved=new Map();candidateViewStates.set(bindings,saved);}
    saved.delete(workspaceKey);saved.set(workspaceKey,{resource,presentation,pointer,offset,includeCalculated});
    if(saved.size>64)saved.delete(saved.keys().next().value!);
  },[bindings,workspaceKey,viewScope,resource,presentation,pointer,offset,includeCalculated]);
  useEffect(() => {
    if (!binding||viewScope!==workspaceKey||presentation!=="data") return;
    const abort = new AbortController();
    setLoading(true);
    setError(undefined);
    void readCandidatePage(api, binding, {resource, pointer, offset}, abort.signal).then(value => {
      if (!abort.signal.aborted) setLoaded({identity, ...value});
    }).catch(reason => {
      if (!abort.signal.aborted) setError(reason instanceof AppCandidateReadError||reason instanceof AppDomainHostError ? reason : new AppCandidateReadError("candidate_read_failed"));
    }).finally(() => { if (!abort.signal.aborted) setLoading(false); });
    return () => abort.abort();
  }, [api, binding, identity, offset, pointer, reload, resource,presentation,viewScope,workspaceKey]);
  useEffect(()=>{
    if(!binding||viewScope!==workspaceKey||presentation!=="preview")return;
    const abort=new AbortController();setLoading(true);setError(undefined);
    void readCandidateDocument(api,binding,resource,abort.signal,includeCalculated).then(value=>{
      if(!abort.signal.aborted)setSnapshot({identity,scope,...value});
    }).catch(reason=>{if(!abort.signal.aborted)setError(reason instanceof Error?reason:new AppCandidateReadError("candidate_read_failed"));})
      .finally(()=>{if(!abort.signal.aborted)setLoading(false);});
    return()=>abort.abort();
  },[api,binding,identity,presentation,resource,reload,includeCalculated,viewScope,workspaceKey]);

  const value = loaded?.identity === identity&&loaded.page.pointer===pointer&&loaded.page.offset===offset ? loaded : undefined;
  // A permission/projection refresh must not unmount the same candidate's
  // review controls and lose the just-issued Save-As delivery capability.
  // Old pixels remain explicitly stale; only fresh Host facts enable review.
  const visibleSnapshot=snapshot?.scope===scope?snapshot:binding&&viewScope===workspaceKey?
    peekCandidateDocument(api,binding,resource,includeCalculated):undefined;
  const visibleSummary=presentation==="preview"?visibleSnapshot?.summary:value?.summary;
  const executionFailure=firstExecutionFailure(visibleSummary?.execution);
  const currentRead=presentation==="preview"?snapshot?.identity===identity:!!value;
  const open = (next: string) => { setPointer(next); setOffset(0); };
  const changeResource = (next: "candidate" | "baseline") => { setResource(next); open(""); };
  const parts = pointer.split("/").slice(1);
  const [localFileFocus,setLocalFileFocus]=useState<string>();
  const windowCommands=useMainWindowCommands();
  const [windowNotice,setWindowNotice]=useState("");
  useEffect(()=>{
    if(compact||!binding||!currentRead||loading)return;
    for(const command of windowCommands){
      // Page/timeline commands have their own async handler and result ACK.
      // Do not remove them while that handler is still locating the target.
      if(!["open_candidate","review_candidate","file_action"].includes(command.command))continue;
      if(command.session_id!==binding.dshSessionId)continue;
      if(command.conversation_id&&command.conversation_id!==binding.conversationId){setWindowNotice("请求所属候选已变化，请重新选择会话；没有进行同步或文件操作。");}
      else if(command.command==="file_action")setLocalFileFocus(command.action_id);
      else if(command.command==="review_candidate")setWindowNotice(command.revision===visibleSummary?.revision?"已从独立聊天窗口定位到此候选。请核对版面与差异后审核同步。":"候选已经更新，请重新核对当前版本后审核；尚未进行同步。");
      void acknowledgeMainCommand(command.command_id);
    }
  },[binding,currentRead,loading,compact,windowCommands,visibleSummary?.revision]);
  const incomingFocus=pendingFileFocus.get(bindings);
  const fileFocus=localFileFocus??(binding?.conversationId===incomingFocus?.conversationId?incomingFocus?.actionId:undefined);
  const clearFileFocus=()=>{setLocalFileFocus(undefined);if(incomingFocus)pendingFileFocus.delete(bindings);};
  return <section className={`gs-candidate-workspace${compact ? " is-compact" : ""}`} aria-label={compact ? "候选详情" : "Agent 候选工作区"}>
    <header>
      <h2>{compact ? "候选详情" : "Agent 候选工作区"}</h2>
      <p>这里显示 Agent 的候选版本。只有经你审核并应用后，修改才会进入正式白板。</p>
    </header>
    {windowNotice&&<p role="status">{windowNotice}</p>}
    {openedDraft?.data.imported===true&&<p role="status">输入草稿已打开，可在本会话继续完善。正式白板保持原样。</p>}
    <AppIncompatibleApplications api={api} onSettled={refresh}/>
    {!binding ? bindingError ? isAppDomainIncompatibility(bindingError)?<AppDomainIncompatibilityNotice key={`${dshSessionId}:${bindingError.code}`} error={bindingError}/>:<div role="alert"><p>{bindingError.message}</p><details><summary>操作详情</summary><code>{bindingError instanceof AppDomainHostError?bindingError.code:bindingError instanceof AppCandidateReadError?bindingError.code:"app_domain_read_failed"}</code>{bindingError instanceof AppDomainHostError&&bindingError.operationMayHaveCompleted&&<p>先前操作可能已经提交，请先刷新状态，避免重复操作。</p>}</details></div> : <p role="status">当前会话尚未绑定候选白板。请先在会话中开始或打开任务。</p> : <>
      {api.dsh_client_domain_approvals&&api.dsh_client_domain_approval_decide&&<AppDomainApprovals key={`${binding.dshSessionId}:${binding.conversationId}`} api={api} bindings={bindings} binding={binding} onSettled={refresh}/>}
      {api.dsh_client_app_actions&&api.dsh_client_app_action&&<AppFileActionCards key={`files:${binding.dshSessionId}:${binding.conversationId}`} api={api} bindings={bindings} binding={binding} files={(window as unknown as {GenshinEditorBridge?:AppEditorBridge}).GenshinEditorBridge?.files} onSettled={refresh} focusActionId={fileFocus} onFocused={clearFileFocus}/>}
      {api.dsh_client_saved_whiteboard&&<SavedWhiteboardControls key={`saved:${binding.dshSessionId}:${binding.conversationId}`} api={api} binding={binding} editor={(window as unknown as {GenshinEditorBridge?:AppEditorBridge}).GenshinEditorBridge}/>}
      <div className="gs-candidate-toolbar">
        <Button size="sm" type="button" variant="outline" aria-pressed={presentation==="preview"} onClick={()=>setPresentation("preview")}>候选版面</Button>
        <Button size="sm" type="button" variant="outline" aria-pressed={presentation==="data"} onClick={()=>setPresentation("data")}>资料与原始输入</Button>
        <Button size="sm" type="button" variant="outline" aria-pressed={resource === "candidate"}
          onClick={() => changeResource("candidate")}>Agent 候选</Button>
        <Button size="sm" type="button" variant="outline" aria-pressed={resource === "baseline"}
          onClick={() => changeResource("baseline")}>本会话的正式白板基线</Button>
        <Button size="sm" type="button" onClick={() => setReload(value => value + 1)} disabled={loading}>刷新</Button>
        {resource==="candidate"&&api.dsh_client_request_candidate_input_draft&&<Button size="sm" type="button" variant="outline" disabled={draftFileBusy||loading||!currentRead||!visibleSummary||!!error} onClick={()=>{
          if(!visibleSummary||draftFileBusy)return;setDraftFileBusy(true);setDraftFileMessage("");
          void api.dsh_client_request_candidate_input_draft!({conversation_id:binding.conversationId,expected_revision:visibleSummary.revision}).then(response=>{assertAppDomainSuccess(response);setDraftFileMessage("已请求保存输入草稿，请在文件操作卡选择保存位置。");refresh?.();setReload(value=>value+1);}).catch(reason=>setDraftFileMessage(reason instanceof Error?reason.message:"暂时无法请求保存草稿。")).finally(()=>setDraftFileBusy(false));
        }}>{draftFileBusy?"正在准备…":"保存输入草稿"}</Button>}
      </div>
      {draftFileMessage&&<p role="status">{draftFileMessage}</p>}
      <p className="gs-candidate-state" role="status">
        {visibleSummary ? `候选版本 ${visibleSummary.revision}` : "正在读取候选"}
        {visibleSummary?.counts.characters !== undefined ? ` · ${visibleSummary.counts.characters} 名角色` : ""}
        {visibleSummary?.counts.actions !== undefined ? ` · ${visibleSummary.counts.actions} 个动作` : ""}
        {visibleSummary?.counts.duration !== undefined ? ` · ${visibleSummary.counts.duration} 秒` : ""}
      </p>
      {(visibleSummary?.readyScope || visibleSummary?.execution) && <p className="gs-candidate-state">{executionStatus(visibleSummary,binding.applicationState)}</p>}
      {executionFailure && <p>{executionFailure}</p>}
      <TaskReviewDetails review={visibleSummary?.taskReview}/>
      {visibleSummary?.execution?.status === "diagnostic" && visibleSummary.execution.reportedHits !== null
        && <p>诊断包含 {visibleSummary.execution.reportedHits} 条已执行伤害记录。</p>}
      {visibleSummary?.execution?.elemental === "trace_saved" && <p>已保存动态元素流水。</p>}
      {visibleSummary?.execution?.elemental === "declared_without_trace" && <p>已声明元素模型，当前未取得动态流水。</p>}
      <p>{binding.applicationState==="committed"?"App 已确认本版本已同步。":candidateReviewAvailable(binding) ? "App 已开放该候选的审核入口。" : "当前仅供查看；尚未获得 App 的审核许可。"}</p>
      {resource==="candidate"&&visibleSummary&&!isAppDomainIncompatibility(error)&&<CandidateReviewControls key={`${binding.dshSessionId}:${binding.conversationId}:${visibleSummary.revision}:${visibleSummary.receipt}`} api={api} binding={binding} summary={visibleSummary} currentRead={currentRead&&!loading&&!error} calculated={visibleSnapshot?.calculated} editor={(window as unknown as {GenshinEditorBridge?:AppEditorBridge}).GenshinEditorBridge} onSettled={refresh} onOpenCandidate={setLocalFileFocus}/>}
      {presentation==="data"&&<nav className="gs-candidate-breadcrumb" aria-label="候选数据位置">
        <Button size="sm" type="button" onClick={() => open("")} disabled={!pointer}>全部内容</Button>
        {parts.map((part, index) => <Button size="sm" type="button" key={index}
          onClick={() => open(`/${parts.slice(0, index + 1).join("/")}`)} disabled={index === parts.length - 1}>
          {label(part)}
        </Button>)}
      </nav>}
      {loading && <p role="status">{visibleSnapshot&&presentation==="preview"?"正在读取最新候选；当前显示上次读取的版本。":"正在读取…"}</p>}
      {error&&(isAppDomainIncompatibility(error)?<AppDomainIncompatibilityNotice key={`${scope}:${error.code}`} error={error}/>:<div role="alert"><p>{error.message}{visibleSnapshot?" 当前显示上一版。":""}</p><details><summary>诊断详情</summary><code>{error instanceof AppCandidateReadError ? error.code : error instanceof AppDomainHostError?error.code:"candidate_read_failed"}</code></details></div>)}
      {presentation==="preview" ? visibleSnapshot&&!isAppDomainIncompatibility(error)&&<>
        {resource==="candidate"&&visibleSnapshot.summary.hasCalculation&&!includeCalculated&&<Button size="sm" variant="outline" onClick={()=>setIncludeCalculated(true)}>加载已计算的效果和伤害</Button>}
        <CandidatePreview document={visibleSnapshot.document} calculated={visibleSnapshot.calculated} hasCalculation={resource==="candidate"&&visibleSnapshot.summary.hasCalculation} kind={visibleSnapshot.summary.kind} sessionId={binding.dshSessionId} conversationId={binding.conversationId} editor={(window as unknown as {GenshinEditorBridge?:AppEditorBridge}).GenshinEditorBridge}/>
      </> : value && !loading && <>
        {value.page.kind === "object" || value.page.kind === "array" ? <dl className="gs-candidate-fields">
          {value.page.items.map(item => <div key={item.key}>
            <dt>{label(String(item.key))}</dt>
            <dd>{item.value !== null && typeof item.value === "object" ? <Button size="sm" type="button"
              onClick={() => open(childPointer(pointer, item.key))}>查看内容</Button> : <span>{display(item.value)}</span>}</dd>
          </div>)}
        </dl> : <pre>{value.page.kind === "text" ? value.page.text : display(value.page.value)}</pre>}
        {value.page.items.length === 0 && ["object", "array"].includes(value.page.kind) && <p>此处暂无内容。</p>}
        <footer className="gs-candidate-pagination">
          <span>{value.page.total !== undefined ? `共 ${value.page.total} 项` : ""}{offset ? ` · 从第 ${offset + 1} 项起` : ""}</span>
          {offset > 0 && <Button size="sm" type="button" onClick={() => setOffset(0)}>回到开头</Button>}
          {value.page.nextOffset !== null && <Button size="sm" type="button" onClick={() => setOffset(value.page.nextOffset!)}>继续查看</Button>}
        </footer>
      </>}
    </>}
  </section>;
}

/** Public Cordis client plugin; composed alongside DSH, never a replacement Chat. */
export function createAppDomainClientPlugin(options: {bindings: AppDomainBindings; api: AppCandidateGuiApi;refresh?:()=>void}) {
  return {
    name: APP_CANDIDATE_PLUGIN_ID,
    inject: ["slots", "sidebarRightTabs","uiWorkspace","layout","sessions","workspaces","conversation"],
    apply(ctx: Context) {
      ctx.effect(()=>{
        const previous=window.GenshinCandidateDraftFiles;
        type SessionId=Awaited<ReturnType<typeof ctx.sessions.create>>;
        const sessionExists=(sessionId:string)=>{
          const workspace=ctx.workspaces.list.getSnapshot();
          if(workspace.state!=="idle")throw new Error("会话列表仍在同步，请稍后重新打开；当前工作副本未改变。");
          return !!ctx.sessions.list.getSnapshot().byId[sessionId as SessionId]
            &&!workspace.archivedSessionIds.includes(sessionId as SessionId);
        };
        const port=createCandidateDraftFilePort({
          createSession:()=>ctx.sessions.create(),sessionExists,
          canUseUnstarted:sessionId=>sessionExists(sessionId)&&ctx.sessions.list.getSnapshot().byId[sessionId as SessionId]?.blank===true&&!options.bindings.getSnapshot(sessionId),
          async previewIsCurrent({data}){
            const compatible=(data.preview as {compatible?:boolean})?.compatible;
            const source=data.binding as {conversation_id?:string}|undefined;
            const response=compatible&&source?.conversation_id?await options.api.agent_candidate(source.conversation_id):
              typeof data.preview_id==="string"&&options.api.dsh_client_candidate_input_draft_preview_read?
                await options.api.dsh_client_candidate_input_draft_preview_read({preview_id:data.preview_id,resource:"candidate",pointer:"",offset:0,limit:1}):undefined;
            if(!response)return false;
            const row=response as {code?:number;reason?:string};
            if(row.code===1&&["candidate_input_draft_preview_expired","dsh_domain_session_unbound","native_app_binding_compatibility_changed","native_app_binding_compatibility_missing"].includes(row.reason||""))return false;
            assertAppDomainSuccess(response);return row.code===0;
          },
          async importFile(sessionId,path,fileSha){
            if(!options.api.dsh_client_import_candidate_input_draft)throw new Error("当前客户端不支持候选输入草稿，请使用新版客户端。");
            const response=await options.api.dsh_client_import_candidate_input_draft({dsh_session_id:sessionId,path,expected_file_sha:fileSha});
            assertAppDomainSuccess(response);return (response as {data:Record<string,unknown>}).data;
          },
          navigate({sessionId,data}){
            selectedDraftNotice.publish(sessionId,data);ctx.uiWorkspace.openSession(sessionId as SessionId);options.refresh?.();
            if(data.imported)ctx.layout.selectPanel(APP_CANDIDATE_PANEL as Parameters<Context["layout"]["selectPanel"]>[0]);
          },
        });
        window.GenshinCandidateDraftFiles=port;
        return()=>{if(window.GenshinCandidateDraftFiles===port)window.GenshinCandidateDraftFiles=previous;};
      });
      ctx.effect(() => {
        const style = document.createElement("style");
        style.dataset.plugin = APP_CANDIDATE_PLUGIN_ID;
        style.dataset.pluginCss = `${APP_CANDIDATE_PLUGIN_ID}/candidate.css`;
        style.textContent = stylesheet;
        document.head.append(style);
        return () => style.remove();
      });
      ctx.slots.inject("main", () => ctx.slots.register({name: "main", key: APP_CANDIDATE_PANEL},
        () => <CandidateWorkspace {...options} />));
      ctx.slots.inject("main",()=>ctx.slots.register({name:"main",key:"genshin-task-center"},(props:PropsRuntime<"main">)=><AppTaskCenter ctx={ctx} {...options} props={props}/>));
      ctx.slots.inject("sidebar.panellist",()=>ctx.slots.register({name:"sidebar.panellist",id:"genshin-task-center",order:290,label:"任务中心"},()=> <span aria-hidden="true">☷</span>));
      ctx.slots.inject("sidebar.panellist", () => ctx.slots.register({
        name: "sidebar.panellist", id: APP_CANDIDATE_PANEL, order: 300, label: "候选工作区",
      }, () => <span aria-hidden="true">▤</span>));
      ctx.slots.inject("conversation.session.header.actions",()=>ctx.slots.register({name:"conversation.session.header.actions",id:APP_CANDIDATE_PLUGIN_ID,order:300},
        (props:PropsRuntime<"conversation.session.header.actions">)=><CandidateHeaderAction api={options.api} bindings={options.bindings} sessionId={props.sessionId} refresh={options.refresh} onOpen={()=>ctx.layout.selectPanel(APP_CANDIDATE_PANEL as Parameters<Context["layout"]["selectPanel"]>[0])}/>));
      ctx.slots.inject("genshin.editor.assistant.actions",()=>ctx.slots.register({name:"genshin.editor.assistant.actions"},
        (props:PropsRuntime<"genshin.editor.assistant.actions">)=>props.sessionId?<><CandidateHeaderAction api={options.api} bindings={options.bindings} sessionId={props.sessionId} refresh={options.refresh} onOpen={()=>ctx.layout.selectPanel(APP_CANDIDATE_PANEL as Parameters<Context["layout"]["selectPanel"]>[0])}/></>:null));
      ctx.slots.inject("conversation.hero.options",()=>ctx.slots.register({name:"conversation.hero.options",id:"genshin-open-candidate-input-draft",order:310},
        (props:PropsRuntime<"conversation.hero.options">)=><OpenCandidateDraft compact api={options.api} sessionId={props.sessionId} onImported={()=>{options.refresh?.();ctx.layout.selectPanel(APP_CANDIDATE_PANEL as Parameters<Context["layout"]["selectPanel"]>[0]);}}/>));
      ctx.effect(() => ctx.sidebarRightTabs.register({
        id: APP_CANDIDATE_PLUGIN_ID, kind: APP_CANDIDATE_INSPECTOR,
        title: () => "候选详情", priority: "extension",
        guide: [{id: APP_CANDIDATE_INSPECTOR, order: 300, title: () => "候选详情",
          description: () => "查看 Agent 候选与正式白板基线"}],
      }));
      ctx.slots.inject("sidebar.right.pane.tab", () => ctx.slots.register({
        name: "sidebar.right.pane.tab", key: APP_CANDIDATE_PLUGIN_ID,
      }, (props: PropsRuntime<"sidebar.right.pane.tab">) => <CandidateWorkspace {...options} dshSessionId={props.sessionId} compact />));
      for(const [kind,title,description]of [["genshin-app-operations","文件与 App 操作","查看文件操作、资料修正与此前同步版本"],["genshin-session-questions","问题与批准","定位本会话待回答的问题和普通批准"],["genshin-app-diagnostics","会话诊断","查看本会话和 App 的错误详情"]] as const){
        ctx.effect(()=>ctx.sidebarRightTabs.register({id:kind,kind,title:()=>title,priority:"extension",guide:[{id:kind,order:kind==="genshin-app-operations"?310:320,title:()=>title,description:()=>description}]}));
        ctx.slots.inject("sidebar.right.pane.tab",()=>ctx.slots.register({name:"sidebar.right.pane.tab",key:kind},(props:PropsRuntime<"sidebar.right.pane.tab">)=>kind==="genshin-app-operations"?<AppOperationsInspector {...options} props={props}/>:kind==="genshin-session-questions"?<AppQuestionsInspector ctx={ctx} props={props}/>:<AppDiagnosticsInspector bindings={options.bindings} props={props}/>));
      }
    },
  };
}

export {CandidateWorkspace,CandidateHeaderAction};
