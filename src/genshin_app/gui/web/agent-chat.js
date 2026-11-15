"use strict";
// Chat owns its requests and DOM. It never shares editor request counters or writes a live plan.
window.AC={ready:false};
// The transactional event store is the sole production authority for Host
// event facts and the acknowledged read cursor.  The session objects below
// retain only UI shell state and full domain-controller sidecars.
AC.eventStore=null;
AC.labels={disconnected:"未连接",detecting:"检测中",connecting:"连接与隔离核验中",connected:"已连接",ready:"就绪",starting:"准备中",preparing:"准备中",blocked:"推理未开放",running:"运行中",
  stopping:"停止中",cancelling:"停止中",completed:"本轮结束",complete:"本轮结束",finished:"本轮结束",stopped:"已停止",cancelled:"已停止",failed:"失败",interrupted:"已中断",error:"失败"};
AC.label=state=>AC.labels[state]||"状态待确认";
AC.clone=value=>JSONTransport.clone(value);
AC.documentAdapter=null;
AC.text=value=>typeof value==="string"?value:value==null?"—":typeof value==="object"?JSON.stringify(value,null,2):String(value);
AC.stableErrorCode=value=>typeof value==="string"&&/^[a-z][a-z0-9_.:-]{0,127}$/.test(value)?value:"";
AC.compatibilityRecoveryAction="agent_guided_current_file_compatibility";
AC.compatibilityPrompt="请使用当前版本的 App、数据库和引擎检查当前打开的方案文件是否能够读取和兼容。如果当前版本无法解析该文件，请明确报告并停止，不要承诺一定能够处理。若可读取，请保留用户明确输入的队伍、装备、面板、动作、约束和备注；不要直接沿用旧数据库查询、旧派生事件、Buff／反应／伤害、工具回执或旧“已通过”状态。先列出不兼容项、拟修改内容、风险和可能改变结果的部分，等待我确认后再创建兼容候选，并使用当前版本重新查询、绑定、计算和验证。不要覆盖原文件；完成候选后提醒我手动另存为新版副本。旧结果只作为历史参考。";
AC.compatibilityFailure=(code,details)=>["native_app_binding_database_changed","document_restore_application_payload_stale"].includes(code)||(details&&typeof details==="object"&&details.compatibility==="version_incompatible"&&details.recovery_action===AC.compatibilityRecoveryAction);
AC.copyCompatibilityPrompt=async()=>{const copy=window.navigator?.clipboard?.writeText;if(typeof copy!=="function")return false;try{await copy.call(window.navigator.clipboard,AC.compatibilityPrompt);return true;}catch{return false;}};
AC.runtimeFailureMessage=code=>{
  const prefix="runtime_official_turn_failed",cause=code.startsWith(`${prefix}:`)?code.slice(prefix.length+1):"";
  if(!cause)return "原生 Agent 运行失败。问题可能发生在模型、本地服务或 App 工具链；请查看状态详情。";
  if(cause.startsWith("native_app_settlement_")||cause.includes("tool_settlement"))return "App 工具已经进入执行流程，但结算或状态同步没有安全闭合。请先查看恢复状态，不要直接重复提交。";
  if(cause.startsWith("runtime_app_tool_"))return "App 工具执行链路失败，当前操作没有形成可确认的完整结果。";
  if(cause.startsWith("runtime_research_"))return "联网资料工具没有完成安全结算；已完成的资料记录会保留。";
  if(cause.startsWith("pipe_")||cause.startsWith("bridge_")||cause.startsWith("runtime_turn_")||cause==="event_sequence_mismatch")return "本地 AI 服务通信中断，本轮没有安全完成。系统不会自动重发模型请求。";
  return "原生 Agent 运行失败。请查看状态详情确认故障发生在模型、本地服务还是 App 工具。";
};
AC.errorDiagnostics=[];
AC.recordErrorDiagnostic=(human,code,raw,details=null)=>{const detail={human:String(human||"").slice(0,2000),code:String(code||"").slice(0,128),raw:String(raw||"").slice(0,8000),compatibility:AC.compatibilityFailure(code,details)};if(!detail.code&&!detail.raw)return null;AC.errorDiagnostics.push(detail);if(AC.errorDiagnostics.length>24)AC.errorDiagnostics.splice(0,AC.errorDiagnostics.length-24);return detail;};
AC.errorDiagnosticFor=message=>{const text=String(message||"");for(let index=AC.errorDiagnostics.length-1;index>=0;index--){const row=AC.errorDiagnostics[index];if(text===row.human||text.includes(row.human))return row;}return null;};
AC.renderErrorDiagnostic=message=>{const host=AC.nodes?.technicalDetails,body=AC.nodes?.technicalDetailsBody,guidance=AC.nodes?.compatibilityGuidance,prompt=AC.nodes?.compatibilityPrompt;if(!host||!body)return;const row=AC.errorDiagnosticFor(message);host.hidden=!row;if(!row){body.textContent="";if(guidance)guidance.hidden=true;return;}body.textContent=[row.code?`问题代码：${row.code}`:null,row.raw?`原始信息：${row.raw}`:null].filter(Boolean).join("\n");if(prompt)prompt.textContent=AC.compatibilityPrompt;if(guidance)guidance.hidden=row.compatibility!==true;};
AC.errorText=(value,fallback="Agent操作失败，请稍后重试。")=>{
  const row=value&&typeof value==="object"?value:{},code=AC.stableErrorCode(row.reason||row.error_code||row.error),messages={
    account_catalog_auth:"DeepSeek认证未通过，请检查已保存的API Key。",
    credential_missing:"尚未找到可用的DeepSeek API Key，请输入后重新连接。",
    credential_store_unavailable:"Windows安全凭据存储暂不可用。",
    account_catalog_network:"无法连接DeepSeek官方服务，请检查网络和代理。",
    account_catalog_http:"DeepSeek官方服务返回异常，请稍后重试。",
    account_catalog_rate_limit:"DeepSeek API请求过于频繁，请稍后重试。",
    native_turn_budget_invalid:"单轮预算设置不合法，请检查数值范围。",
    native_turn_budget_generation_conflict:"预算设置已被其他入口修改，请刷新后重试。",
    native_turn_budget_store_busy:"预算设置正在由其他入口修改，请稍后刷新。",
    native_turn_budget_store_invalid:"本机预算设置文件不合法，请按维护手册检查后重试。",
    native_turn_budget_store_write_failed:"单轮预算设置写入失败，请检查本机配置目录。",
    native_dsh_settings_invalid:"Agent运行参数不合法，请检查高级设置。",
    native_dsh_settings_generation_conflict:"Agent运行参数已被其他入口修改，请刷新后重试。",
    native_dsh_settings_store_busy:"Agent运行参数正在由其他入口修改，请稍后刷新。",
    native_dsh_settings_store_invalid:"本机Agent运行参数文件不合法，请按维护手册检查。",
    native_dsh_settings_store_write_failed:"Agent运行参数写入失败，请检查本机配置目录。",
    native_runtime_start_failed:"原生Agent运行时启动失败。",
    native_runtime_credential_required:"本地 AI 服务已经停止，且本次只使用了内存中的 API Key。请重新输入 Key 并验证 API 设置。",
    native_runtime_unhealthy:"本地 AI 服务启动后未通过健康检查；系统没有发送模型请求。",
    native_runtime_prepare_not_found:"本地 AI 服务的启动记录已经失效，请重新发送。",
    native_runtime_prepare_busy:"本地 AI 服务仍在结束启动操作，请稍后再试。",
    runtime_official_turn_failed:"原生 Agent 运行失败。问题可能发生在模型、本地服务或 App 工具链；请查看状态详情。",
    native_v2_not_ready:"原生Agent运行组件尚未就绪。",
    native_session_activation_blocked:AC.compatibilityFailure(code,row.details)?"这份历史会话与当前 App、数据库或引擎不兼容，不能继续恢复。":"这份会话创建时的环境或当前文档已经变化，不能继续恢复。",
    native_app_binding_database_changed:"这份历史会话使用的数据库版本与当前版本不同，不能继续恢复。",
    document_restore_application_payload_stale:"这个恢复点来自不同的数据库、引擎或文件结构版本，不能恢复为当前结果。",
    native_turn_error:"本轮原生Agent运行失败。",
    native_service_failed:"原生Agent服务发生错误。",
    native_agent_operation_failed:"原生Agent操作失败。"
  },raw=typeof row.message==="string"&&row.message.trim()?row.message.trim():"",runtimeTurnFailure=code.startsWith("runtime_official_turn_failed")?AC.runtimeFailureMessage(code):"",knownMessage=messages[code]||runtimeTurnFailure,humanRaw=/[\u3400-\u9fff]/u.test(raw),message=knownMessage||(humanRaw?raw:fallback||raw);
  AC.recordErrorDiagnostic(message,code,raw&&raw!==message?raw:"",row.details);
  return message;
};
AC.stateAdapter=null;
AC.storeSnapshot=session=>AC.stateAdapter?.storeSnapshot(session)||null;
AC.storeModel=session=>AC.stateAdapter?.storeModel(session)||null;
AC.readModel=session=>AC.stateAdapter?.readModel(session)||(session?{...session,messages:[],tools:[],usage:null,budgetStatuses:{},cursor:0,projectionRevision:0,store_revision:0}:null);
AC.ackCursor=session=>AC.stateAdapter?.ackCursor(session)||0;
AC.isActive=session=>AC.stateAdapter?.isActive(session)===true;
AC.sessionController=null;
AC.currentKind=()=>{const selected=window.WS?.tab?.(),tab=selected==="agent"?document.body?.dataset?.agentReturnTab:selected;return tab==="whiteboard"?"whiteboard":["rotation","result"].includes(tab)?"native":AC.nodes.kind?.value||"whiteboard";};
// A whiteboard stays in the staged authoring workflow until the user explicitly
// requests strict calculation.  Having one actor and one action is only enough
// to start an action checkpoint; it is not evidence that event topology,
// numeric bindings, energy or cooldowns are complete.
AC.defaultWorkflow=kind=>kind==="whiteboard"?"staged":"calculate";
AC.appliedCandidate=(session,candidate)=>!!session?.applied&&session.appliedReceipt===candidate?.receipt_fingerprint;
AC.workflowReviewView=null;
AC.workflowStages=AgentWorkflowReviewView.WORKFLOW_STAGES;
AC.stageAliases=AgentWorkflowReviewView.STAGE_ALIASES;
for(const name of ["firstDefined","stageId","receiptFrom","topologyReceipt","executionBindingReceipt","acceptanceValue","acceptanceStatus","stageStatusText","acceptanceText","energyText","topologyGateText","executionBindingFacts","bindingGateText","bindingIssueText","topologySummary","damageDisclosurePath","topologyValue"]){
  AC[name]=(...args)=>AgentWorkflowReviewView[name](...args);
}
AC.workflow=()=>AC.workflowReviewView?.workflow()||(AC.readModel(AC.selected())?.workflow||(AC.nodes.kind?.value==="whiteboard"?AC.nodes.workflow?.value||AC.defaultWorkflow("whiteboard"):"calculate"));
AC.isBuild=session=>AC.workflowReviewView?.isBuild(session)??(session?.kind==="whiteboard"&&session?.workflow==="build");
AC.isStaged=session=>AC.workflowReviewView?.isStaged(session)??(session?.kind==="whiteboard"&&session?.workflow==="staged");
AC.isAuthoring=session=>AC.workflowReviewView?.isAuthoring(session)??(AC.isBuild(session)||AC.isStaged(session));
AC.workflowState=(...args)=>AC.workflowReviewView?.workflowState(...args)||AgentWorkflowReviewView.workflowState(...args);
AC.renderStageTracker=(...args)=>AC.workflowReviewView?.renderStageTracker(...args);
AC.canApply=(...args)=>AC.workflowReviewView?.canApply(...args)??false;
AC.hasDraft=kind=>AC.documentAdapter?.hasDraft(kind)??false;
AC.capture=kind=>{if(!AC.documentAdapter)throw new Error("agent_document_adapter_not_ready");return AC.documentAdapter.capture(kind);};
// Only this bounded projection crosses the provider boundary. Paths, DOM
// handles and the live document remain private to the App.
AC.workspace=(base=null)=>AC.documentAdapter?.workspace(base,base?.kind||AC.currentKind());
AC.sameContent=(a,b)=>AC.documentAdapter?.sameContent(a,b)??false;
AC.same=(a,b)=>AC.documentAdapter?.same(a,b)??false;
AC.currentMatches=base=>AC.documentAdapter?.currentMatches(base)??false;
AC.workbenchState=(session=AC.selected())=>AC.stateAdapter?.workbenchState(session)||AC.state.workbench;
AC.pendingApplicationSession=()=>[...AC.state.sessions.values()].find(session=>AC.hasPendingApplication?.(session))||null;
AC.notice=(message,error=false)=>{if(AC.nodes.notice){AC.nodes.notice.textContent=message;AC.nodes.notice.classList.toggle("ac-error",error);if(AC.nodes.statusNotice){AC.nodes.statusNotice.textContent=error?"":message;AC.nodes.statusNotice.hidden=error||!message;}if(error)AC.renderErrorDiagnostic(message);else if(!AC.nodes.error?.textContent)AC.renderErrorDiagnostic(null);AgentWorkbenchView.renderAttentionShelf?.({nodes:AC.nodes});}};
AC.panelStorageKey=AgentWorkbenchShell.STORAGE_KEY;
AC.loadPanelPreferences=()=>AC.shell?.state||AC.state.panel;
AC.persistPanelPreferences=()=>AC.shell?.persist?.()||false;
AC.requestWorkspaceReflow=()=>{if(AC.panelPaintQueued)return;AC.panelPaintQueued=true;const paint=()=>{AC.panelPaintQueued=false;window.TV?.requestPaint?.();window.WB?.draw?.();};
  if(typeof window.requestAnimationFrame==="function")window.requestAnimationFrame(paint);else paint();
};
AC.panelLimits=()=>AC.shell?.limits?.()||{min:320,max:720};
AC.applyPanelLayout=(persist=false)=>AC.shell?.apply?.(persist);
AC.togglePanelSection=section=>AC.shell?.toggleSection?.(section);
AC.resizePanel=(width,persist=true)=>AC.shell?.resize?.(width,persist);
AC.beginPanelResize=event=>AC.shell?.beginResize?.(event);
AC.movePanelResize=event=>AC.shell?.moveResize?.(event);
AC.endPanelResize=event=>AC.shell?.endResize?.(event);
AC.panelResizeKey=event=>AC.shell?.resizeKey?.(event);
AC.composerContextKey=id=>id?`session:${id}`:`new:${AC.nodes.kind?.value||AC.currentKind()}`;
// Compatibility entry points only.  Renderer writes and Host reconciliation
// are exclusively owned by AgentApplicationRecovery.
AC.rehydrateCommittedApplication=(...args)=>AC.applicationRecoveryController?.recoverCommitted(...args)??Promise.resolve(false);
AC.rehydrateLastAppliedProjection=(...args)=>AC.applicationRecoveryController?.recoverLastApplied(...args)??Promise.resolve(false);
AC.providerSettings=null;
AC.transportController=null;AC.transport=null;
AC.receiveProtocolText=reason=>AC.transportController?.protocolText(reason);
AC.recordAppAction=(...args)=>AC.stateAdapter?.recordAppAction(...args)??false;
for(const name of ["receive","receiveCommand","schedule","poll","reconcile"])AC[name]=(...args)=>AC.transportController?.[name]?.(...args);
AC.readController=null;
for(const name of ["renderSessionNavigation","saveReadingAnchor","restoreReadingAnchor","clearHostOutline","loadHostOutline","loadConversationPage","renderHostSearchResults","loadHostSearchPage","scheduleHostSearch","locateHostTurn","loadTelemetry","applyTelemetryFilters","workbenchFoundation","workbenchTarget","presentWorkbenchFoundation","loadWorkbenchFoundation"])AC[name]=(...args)=>AC.readController?.[name]?.(...args);
AC.workbenchMutationId=prefix=>UI.id(prefix).replace(/[^A-Za-z0-9._:-]/g,"-").slice(0,128);
AC.questionAnswerController=null;
AC.submitQuestionBatch=(...args)=>AC.questionAnswerController?.submit(...args)??Promise.resolve(false);
AC.inboxRequest=(...args)=>AC.turnController?.inboxRequest?.(...args)??false;
AC.enqueueActiveMessage=(...args)=>AC.turnController?.enqueueActiveMessage?.(...args)??false;
AC.handleInboxAction=(...args)=>AC.turnController?.handleInboxAction?.(...args)??false;
AC.checkpointIntegration=null;
AC.checkpointFrozen=()=>AC.checkpointIntegration?.frozen?.()??false;
const checkpointPorts={bindCheckpoints:"bind",checkpointCreate:"createCheckpoint",checkpointRename:"renameCheckpoint",checkpointDelete:"deleteCheckpoint",checkpointRestore:"restoreCheckpoint",checkpointPreview:"previewCheckpoint"};
for(const [external,internal] of Object.entries(checkpointPorts))AC[external]=(...args)=>AC.checkpointIntegration?.[internal]?.(...args);
AC.canPrepareColdRuntime=runtimeState=>AC.turnController?.canPrepareColdRuntime?.(runtimeState)===true;
AC.prepareColdRuntime=(...args)=>AC.turnController?.prepareColdRuntime?.(...args)??false;
AC.send=(...args)=>AC.turnController?.send?.(...args)??false;
AC.stop=(...args)=>AC.turnController?.stop?.(...args)??true;
AC.exploreCandidate=(...args)=>AC.workflowReviewView?.exploreCandidate(...args)??false;
AC.draftFields=AgentWorkflowReviewView.DRAFT_FIELDS;
AC.draftLabel=(...args)=>AC.workflowReviewView?.draftLabel(...args);
AC.draftName=(...args)=>AC.workflowReviewView?.draftName(...args);
AC.draftValue=(...args)=>AC.workflowReviewView?.draftValue(...args);
AC.renderDraftCell=(...args)=>AC.workflowReviewView?.renderDraftCell(...args);
AC.renderDiff=(...args)=>AC.workflowReviewView?.renderDiff(...args);
AC.reviewCore=()=>AC.workflowReviewView?.review()??Promise.resolve(false);
AC.review=()=>AC.workflowReviewView?.review()??Promise.resolve(false);
AC.candidateController=null;
AC.retryPendingApplication=(...args)=>AC.candidateController?.retryPending(...args);
AC.cancelPendingApplication=(...args)=>AC.candidateController?.cancelPending(...args);
AC.recoverPendingCandidateApplication=(...args)=>AC.candidateController?.recoverPending(...args)??false;
AC.accept=(...args)=>AC.candidateController?.accept(...args)??false;
AC.appActionLabels={save_current:"保存当前文档",save_as:"另存为",open_document:"打开文档",export_result:"打开导出界面",
  navigate_rotation:"转到高级编辑",navigate_result:"转到计算报告",navigate_whiteboard:"转到创作白板"};
AC.appActionStatus={queued:"等待你处理",resolving:"正在处理",presented:"操作界面已呈现",claimed_for_external_execution:"已领取，等待App结果",completed:"已完成",cancelled:"已取消",failed:"未完成"};
AC.fileAppActions=AgentDomainTransactions.FILE_ACTIONS;
AC.candidateFileActions=AgentDomainTransactions.CANDIDATE_FILE_ACTIONS;
AC.isTwoPhaseAppAction=action=>action?.execution_mode==="two_phase";
AC.hasUnsettledAppAction=session=>AgentDomainTransactions.hasUnsettledAppAction(session?.appActions);
AC.hasPendingApplication=session=>AgentDomainTransactions.hasPendingApplication(session);
AC.hasBlockingCommit=session=>AC.hasUnsettledAppAction(session)||AC.hasPendingApplication(session);
AC.actionIssueText=AgentDomainTransactions.issueText;
// Treat old action records conservatively.  New backends provide a signed
// candidate revision/receipt plus a computed precondition status; the local
// check still proves that the accepted candidate is the document on screen.
AC.appActionController=null;
AC.appActionEligibility=(...args)=>AC.appActionController?.eligibility(...args)||{enabled:false,reason:"App操作控制器尚未就绪。"};
AC.recoverUnknownAppAction=(...args)=>AC.appActionController?.recoverUnknown(...args)??false;
AC.executeTwoPhaseAppAction=(...args)=>AC.appActionController?.executeTwoPhase(...args)??false;
AC.executeAppAction=(...args)=>AC.appActionController?.execute(...args)??false;
AC.presentationController=null;
for(const name of ["renderAppActions","workbenchProjection","setWorkbenchView","setWorkbenchTurn","renderStatusAxes","feedbackQuestionOpen","observeSessionFeedback","refreshActivityFeedback","renderWorkbench","renderTimelineNode","renderMessages"]){
  AC[name]=(...args)=>AC.presentationController?.[name]?.(...args);
}
AC.refresh=(...args)=>AC.presentationController?.refresh?.(...args);
AC.beforeDocumentSave=(...args)=>AC.presentationController?.beforeDocumentSave?.(...args)??null;
AC.documentSaved=(...args)=>AC.presentationController?.documentSaved?.(...args);
AC.documentChanged=(...args)=>AC.presentationController?.documentChanged?.(...args);
AC.lifecycleController=null;
AC.requestClose=()=>AC.lifecycleController?.requestClose()??false;
AC.init=()=>{
  return AC.bootstrap.wire();
};
AC.bootstrap=AgentWorkbenchBootstrap.createProduction({agent:AC,window,document,ui:UI});
