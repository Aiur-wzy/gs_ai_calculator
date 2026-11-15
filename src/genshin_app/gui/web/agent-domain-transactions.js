(function (global) {
  "use strict";

  const FILE_ACTIONS = new Set(["save_current", "save_as", "open_document", "export_result"]);
  const CANDIDATE_FILE_ACTIONS = new Set(["save_current", "save_as", "export_result"]);
  const LABELS = Object.freeze({
    save_current:"保存当前文档", save_as:"另存为", open_document:"打开文档",
    export_result:"打开导出界面", navigate_rotation:"转到高级编辑",
    navigate_result:"转到计算报告", navigate_whiteboard:"转到创作白板",
  });
  const first = (...values) => values.find(value => value !== null && value !== undefined);
  const result = (enabled, reason = "") => Object.freeze({enabled, reason});
  const issueText = action => {
    const issues = Array.isArray(action?.preconditions?.issues) ? action.preconditions.issues : [];
    const issue = issues.find(row => row && typeof row === "object") || issues.find(row => typeof row === "string");
    const value = typeof issue === "string" ? issue : issue?.message;
    return value ? String(value).slice(0, 500) : "";
  };

  function appActionEligibility(input = {}) {
    const action = input.action && typeof input.action === "object" ? input.action : {};
    const pre = action.preconditions && typeof action.preconditions === "object" ? action.preconditions : {};
    const navigation = !FILE_ACTIONS.has(action.action);
    if (pre.status === "blocked") return result(false, issueText(action) || "后端前置条件尚未满足。");
    if (pre.status === "stale") return result(false, issueText(action) || "此操作绑定的候选已经过期。");
    if (pre.status && pre.status !== "ready") return result(false, "操作前置条件状态无法确认。");
    if (navigation) return result(true);
    if (input.active === true) return result(false, "Agent运行期间文件操作已暂停；本轮结束后再处理。");
    if (input.session_live !== true || input.has_base !== true) return result(false, "历史会话没有可核验的实时冻结工作区，不能对当前文档执行文件操作。");
    const revision = first(action.candidate_revision, pre.candidate_revision);
    const receipt = first(action.receipt_fingerprint, pre.receipt_fingerprint);
    if (revision != null && revision !== input.candidate_revision) return result(false, "操作绑定的候选修订已变化。");
    if (receipt != null && receipt !== input.candidate_receipt) return result(false, "操作绑定的候选审核指纹已变化。");
    const requires = first(action.requires_applied_candidate, pre.requires_applied_candidate, CANDIDATE_FILE_ACTIONS.has(action.action));
    if (requires) {
      if (revision == null || !receipt) return result(false, "操作缺少候选修订或审核指纹，不能写入文件。");
      if (input.candidate_applied !== true) return result(false, "请先审核并应用此操作绑定的候选。");
      if (input.candidate_revision !== revision || input.applied_receipt !== receipt) return result(false, "当前已应用候选与操作绑定的候选不一致。");
    }
    if (input.document_matches !== true) return result(false, "当前文档已不同于此操作绑定的工作区，不能作用于其他文档。");
    const requested = action.workspace && typeof action.workspace === "object" ? action.workspace : {};
    const current = input.workspace && typeof input.workspace === "object" ? input.workspace : {};
    const capability = action.action === "open_document" ? "can_open" : action.action === "export_result" ? "can_export" : "can_save";
    if (requested[capability] === false || current[capability] === false) return result(false, `当前工作区不允许${LABELS[action.action] || "此操作"}。`);
    return result(true);
  }

  const hasUnsettledAppAction = rows => (Array.isArray(rows) ? rows : []).some(action =>
    action?.status === "claimed_for_external_execution" || action?.execution_outcome_unknown === true ||
    Boolean(action?.pendingSettlement) || action?.externalExecutionClaimed === true
  );
  const hasPendingApplication = session => Boolean(session?.pendingApplicationAck) || session?.applicationRecoveryPending === true;

  const transactionIdentity = value => {
    const text = String(value ?? "");
    if (!text || text.length > 512 || /[\u0000-\u001f]/u.test(text)) throw new TypeError("domain_transaction_identity_invalid");
    return text;
  };

  /**
   * Owns command admission only.  The injected operation remains responsible
   * for the existing Host CAS / present-claim-settle protocol.  Returning the
   * same promise for a duplicate click is deliberate: callers observe the one
   * authoritative outcome without issuing a second external command.
   */
  function createCommandGate(options = {}) {
    const active = new Map(), audit = []; let sequence = 0;
    const append = row => { audit.push(Object.freeze({...row, seq:++sequence})); if (audit.length > 128) audit.splice(0, audit.length - 128); };
    const run = (scope, command, operation) => {
      const key = transactionIdentity(scope), name = transactionIdentity(command);
      if (typeof operation !== "function") throw new TypeError("domain_transaction_operation_invalid");
      const current = active.get(key); if (current) { append({scope:key, command:name, state:"joined"}); return current.promise; }
      const owner = Object.freeze({scope:key, command:name, generation:sequence + 1});
      append({scope:key, command:name, state:"admitted"});
      const promise = Promise.resolve().then(() => operation(owner)).then(
        value => { append({scope:key, command:name, state:"settled"}); return value; },
        error => { append({scope:key, command:name, state:"failed"}); throw error; },
      ).finally(() => { if (active.get(key)?.owner === owner) active.delete(key); });
      active.set(key, {owner, promise}); return promise;
    };
    return Object.freeze({run, isActive:scope=>active.has(String(scope)), snapshot:()=>Object.freeze(audit.slice()), activeCount:()=>active.size});
  }

  function createController(options = {}) {
    const gate = options.gate || createCommandGate();
    const sessionId = session => transactionIdentity(session?.session_id || session?.id);
    const candidateScope = session => `candidate:${sessionId(session)}`;
    const actionScope = (session, action) => `app-action:${sessionId(session)}:${transactionIdentity(action?.action_id)}`;
    const runCandidate = (session, intent, operation) => gate.run(candidateScope(session), transactionIdentity(intent), operation);
    return Object.freeze({
      gate,
      runCandidate,
      reviewCandidate(session, operation) { return runCandidate(session, "review", operation); },
      applyCandidate(session, candidate, operation) {
        const revision = transactionIdentity(candidate?.revision); const receipt = transactionIdentity(candidate?.receipt_fingerprint);
        return runCandidate(session, `apply:${revision}:${receipt}`, operation);
      },
      runAppAction(session, action, intent, operation) {
        return gate.run(actionScope(session, action), transactionIdentity(intent || "execute"), operation);
      },
      ownsCandidate(session) { try { return gate.isActive(candidateScope(session)); } catch { return false; } },
      ownsAppAction(session, action) { try { return gate.isActive(actionScope(session, action)); } catch { return false; } },
    });
  }

  function appActionViewModel(action, eligibility, isTwoPhase) {
    const twoPhase = isTwoPhase === true;
    const canContinue = twoPhase && action.status === "presented" && !action.externalExecutionClaimed;
    const canRetrySettlement = twoPhase && action.status === "claimed_for_external_execution" && Boolean(action.pendingSettlement);
    const canRecoverUnknown = twoPhase && action.status === "claimed_for_external_execution" && action.execution_outcome_unknown === true && !action.pendingSettlement && !action.externalExecutionClaimed;
    const actionable = action.status === "queued" || canContinue || canRetrySettlement || canRecoverUnknown;
    const runLabel = action.status === "presented" ? (canContinue ? "继续处理" : "等待文件界面") : action.status === "claimed_for_external_execution" ? (canRetrySettlement ? "重试写回结果" : canRecoverUnknown ? "解除未知操作" : "等待结果写回") : "处理";
    return Object.freeze({twoPhase, canContinue, canRetrySettlement, canRecoverUnknown, actionable, runLabel,
      runDisabled:Boolean(action.localBusy)||!actionable||(!canRetrySettlement&&!canRecoverUnknown&&!eligibility.enabled),
      cancelDisabled:Boolean(action.localBusy)||action.status==="resolving"||action.status==="claimed_for_external_execution"||(action.status==="presented"&&Boolean(action.externalExecutionClaimed)),
    });
  }

  function createAppActionRenderer(options = {}) {
    const {host, summaryHost, sectionHost, nodes, element, button, eligibility, isTwoPhase, onExecute, onRecover} = options;
    if (!host || !summaryHost || !sectionHost || !(nodes instanceof Map) || typeof element !== "function" || typeof button !== "function") throw new TypeError("app_action_renderer_invalid");
    let signature = null;
    const statusLabels = options.statusLabels || {};
    const render = (session, rows = []) => {
      const values = Array.isArray(rows) ? rows : [];
      const models = values.map(action => { const allowed=eligibility(session,action),view=appActionViewModel(action,allowed,isTwoPhase(action)); return {action,allowed,view}; });
      const nextSignature = JSON.stringify(models.map(({action,allowed,view}) => [action.action_id,action.action,action.status,action.reason,action.result_message,Boolean(action.localBusy),action.execution_mode,action.ui_receipt,Boolean(action.externalExecutionClaimed),Boolean(action.pendingSettlement),action.execution_outcome_unknown===true,action.candidate_revision,action.stage_revision,action.receipt_fingerprint,action.origin_workspace?.display_name,action.requires_applied_candidate,action.preconditions?.status,allowed.enabled,allowed.reason,view.runLabel,view.runDisabled,view.cancelDisabled]));
      if (signature !== nextSignature || nodes.size !== values.length) {
        signature = nextSignature; nodes.clear(); host.replaceChildren();
        for (const {action,allowed,view} of models) {
          const card=element("article",null,`ac-app-action ac-app-action-${action.status||"queued"}`),head=element("div",null,"ac-app-action-head");
          head.append(element("strong",LABELS[action.action]||"App操作"),element("span",statusLabels[action.status]||action.status||"状态待确认"));card.append(head);
          if(action.reason)card.append(element("p",action.reason,"ac-app-action-reason"));if(action.suggested_name)card.append(element("p",`建议名称：${action.suggested_name}`,"ac-note"));
          if(Number.isInteger(action.candidate_revision)){const originName=action.origin_workspace?.display_name?` · ${action.origin_workspace.display_name}`:"",stage=Number.isInteger(action.stage_revision)?` · 阶段 ${action.stage_revision}`:"";card.append(element("p",`来源：候选修订 ${action.candidate_revision}${stage}${originName}`,"ac-note"));}
          if(action.result_message)card.append(element("p",action.result_message,"ac-note"));if(!allowed.enabled&&action.status==="queued")card.append(element("p",allowed.reason,"ac-note ac-warning"));
          if(["queued","resolving","presented","claimed_for_external_execution"].includes(action.status)){const controls=element("div",null,"ac-actions");if(view.canRecoverUnknown)card.append(element("p","原操作可能已经执行，但界面结果已丢失。解除只会关闭未知状态，不会再次执行文件或导航操作。","ac-note ac-warning"));const run=button(view.runLabel,()=>view.canRecoverUnknown?onRecover(session,action):onExecute(session,action,false));run.disabled=view.runDisabled;const cancel=button(action.status==="presented"&&!view.twoPhase?"取消请求":"不执行",()=>onExecute(session,action,true));cancel.disabled=view.cancelDisabled;controls.append(run,cancel);card.append(controls);}
          nodes.set(action.action_id,card);host.append(card);
        }
      }
      summaryHost.textContent=`需要你完成的操作（${values.length}）`;sectionHost.hidden=!values.length;
      return nextSignature;
    };
    return Object.freeze({render, clear(){signature=null;nodes.clear();host.replaceChildren();}, signature:()=>signature});
  }

  const requiredFunction = (options, name) => {
    const value = options[name];
    if (typeof value !== "function") throw new TypeError(`domain_transactions_${name}_missing`);
    return value;
  };

  /**
   * Sole Renderer-side executor for App action transactions.  The controller
   * receives only reviewed ports; it never reads the global AC facade.  File
   * and navigation side effects remain behind present -> claim -> settle and
   * a claimed or outcome-unknown action is never replayed automatically.
   */
  function createAppActionController(options = {}) {
    const window = options.window;
    const document = options.document;
    const UI = options.ui;
    const callApi = requiredFunction(options, "api");
    if (!window || !document || !UI) throw new TypeError("domain_transactions_renderer_ports_missing");
    const AC = {
      state:options.state,
      pendingAppFiles:options.pendingFiles || new Map(),
      active:requiredFunction(options,"active"),
      checkpointFrozen:requiredFunction(options,"checkpointFrozen"),
      capture:requiredFunction(options,"capture"),
      currentKind:requiredFunction(options,"currentKind"),
      clone:requiredFunction(options,"clone"),
      workspace:requiredFunction(options,"workspace"),
      same:requiredFunction(options,"same"),
      currentMatches:requiredFunction(options,"currentMatches"),
      appliedCandidate:requiredFunction(options,"appliedCandidate"),
      recordAppAction:requiredFunction(options,"recordAppAction"),
      notice:requiredFunction(options,"notice"),
      refresh:requiredFunction(options,"refresh"),
      errorText:requiredFunction(options,"errorText"),
      loadHistory:requiredFunction(options,"loadHistory"),
      reconcile:requiredFunction(options,"reconcile"),
      transport:options.transport,
      appActionLabels:options.labels || LABELS,
    };
    if (!AC.state || !AC.transport || typeof AC.transport.invalidate !== "function") throw new TypeError("domain_transactions_state_ports_missing");
    const transactions = options.transactions || createController();

    AC.isTwoPhaseAppAction=action=>action?.execution_mode==="two_phase";
    AC.appActionEligibility=(session,action)=>{
      if(AC.checkpointFrozen())return {enabled:false,reason:"文档恢复结果尚未核清，App 写入已冻结。"};
      return appActionEligibility({action,active:!!AC.active(),session_live:session?.live===true,has_base:!!session?.base,candidate_revision:session?.candidate?.revision,candidate_receipt:session?.candidate?.receipt_fingerprint,candidate_applied:AC.appliedCandidate(session,session?.candidate),applied_receipt:session?.appliedReceipt,document_matches:!session?.base||AC.currentMatches(session.base),workspace:AC.workspace(session?.base)});
    };
    AC.resolveAppAction=async(session,action,status,message)=>{
      const r=await callApi("agent_resolve_app_action",session.session_id,action.action_id,status,AC.workspace(session.base),message);
      if(r.code){await AC.reconcile(session);const current=session.appActions.find(row=>row.action_id===action.action_id)||action;if(["completed","cancelled","failed"].includes(current.status)){current.localBusy=false;AC.notice("App操作结果已从Host状态恢复；系统没有重放原操作。",current.status==="failed");AC.refresh();return true;}current.status=current.status==="presented"?"presented":"queued";current.localBusy=false;AC.notice(AC.errorText(r,"操作结果未能写回Agent会话，可重新处理。"),true);AC.refresh();return false;}
      const resolved=r.data?.action&&typeof r.data.action==="object"?r.data.action:r.data;AC.recordAppAction(session,resolved||{...action,status,result_message:message});action.localBusy=false;AC.notice(status==="presented"?"已呈现操作界面；文件尚未视为已打开、保存或导出。":status==="completed"?"App操作已完成并记录。":status==="cancelled"?"已取消这项App操作。":"App操作未完成，原因已记录。",status==="failed");AC.refresh();return true;
    };
    AC.twoPhaseCard=(session,action,data)=>{const card=data?.card&&typeof data.card==="object"?data.card:data?.action&&typeof data.action==="object"?data.action:null;if(card)AC.recordAppAction(session,card);return session.appActions.find(row=>row.action_id===action.action_id)||action;};
    AC.twoPhaseIdentity=action=>{const identity={};for(const key of ["action_id","action","reason","suggested_name","candidate_revision","stage_revision","receipt_fingerprint","requires_applied_candidate","execution_mode"])identity[key]=Object.hasOwn(action||{},key)?action[key]:null;return Object.freeze(identity);};
    AC.sameTwoPhaseIdentity=(action,expected)=>{const actual=AC.twoPhaseIdentity(action);return Object.keys(expected).every(key=>actual[key]===expected[key]);};
    AC.twoPhaseExecutionGuard=(session,action)=>{let currentDocument=null;try{currentDocument=AC.capture(session.kind||AC.currentKind());}catch{}const candidate=session.candidate;return Object.freeze({session,sessionId:session.session_id,sessionEpoch:session.epoch,selected:AC.state.selected,viewEpoch:AC.state.viewEpoch,tab:window.WS?.tab?.(),base:session.base,document:currentDocument,action:AC.twoPhaseIdentity(action),candidateRevision:candidate?.revision??null,candidateReceipt:candidate?.receipt_fingerprint??null,workspace:Object.freeze(AC.clone(AC.workspace(session.base)))});};
    AC.twoPhaseGuardError=(session,guard)=>{if(!guard||session!==guard.session||session.session_id!==guard.sessionId||AC.state.sessions.get(guard.sessionId)!==session)return "操作所属会话已经变化";if(session.epoch!==guard.sessionEpoch||AC.state.selected!==guard.selected||AC.state.viewEpoch!==guard.viewEpoch)return "操作期间会话选择已经变化";if(window.WS?.tab?.()!==guard.tab)return "操作期间当前页面已经变化";if(session.base!==guard.base)return "操作绑定的会话基线已经变化";if((session.candidate?.revision??null)!==guard.candidateRevision||(session.candidate?.receipt_fingerprint??null)!==guard.candidateReceipt)return "操作绑定的候选已经变化";const current=session.appActions.find(row=>row.action_id===guard.action.action_id);if(!current||!AC.sameTwoPhaseIdentity(current,guard.action))return "操作卡身份已经变化";try{if(!guard.document||!AC.same(guard.document,AC.capture(guard.document.kind)))return "操作期间当前文档已经变化";}catch{return "操作期间当前文档无法核验";}return null;};
    AC.twoPhaseCardError=(data,expected,{receipt=null,statuses=[]}={})=>{const card=data?.card&&typeof data.card==="object"?data.card:data?.action&&typeof data.action==="object"?data.action:null;if(!card)return "操作响应缺少卡片身份";if(!AC.sameTwoPhaseIdentity(card,expected))return "操作响应卡片身份不一致";if(statuses.length&&!statuses.includes(card.status))return "操作响应卡片状态不一致";if(receipt!==null&&card.ui_receipt!==receipt)return "操作响应界面回执不一致";return null;};
    AC.twoPhaseRequestError=(request,expected,workspace)=>{if(!request||typeof request!=="object")return "操作领取响应缺少执行请求";const allowed=new Set(["action_id","action","reason","suggested_name","document_kind","display_name","current_view","dirty_open_guard_required"]);if(Object.keys(request).some(key=>!allowed.has(key)))return "操作领取请求含有未授权字段";const expectedGuard=expected.dirty_open_guard_required===true||(expected.action==="open_document"&&workspace.dirty===true);if(request.action_id!==expected.action_id||request.action!==expected.action)return "操作领取请求身份不一致";if(request.reason!==(expected.reason??"")||request.suggested_name!==(expected.suggested_name??null))return "操作领取请求内容不一致";if(request.document_kind!==workspace.document_kind||request.display_name!==workspace.display_name||request.current_view!==workspace.current_view)return "操作领取请求工作区不一致";if(request.dirty_open_guard_required!==expectedGuard)return "操作领取请求的未保存保护不一致";return null;};
    AC.failTwoPhasePreparation=(action,message)=>{action.status="queued";action.localBusy=false;AC.notice(message,true);AC.refresh();return null;};
    AC.reconcileTwoPhaseWrite=async(session,action,previousStatus,response,fallback)=>{const refreshed=await AC.reconcile(session),current=session.appActions.find(row=>row.action_id===action.action_id)||action;current.localBusy=false;if(["accepted","timeout"].includes(refreshed?.status)&&current.status!==previousStatus&&current.status!=="resolving"){AC.notice("App操作状态已从Host恢复；系统没有重放上一个写命令。",current.status==="failed");AC.refresh();return null;}return AC.failTwoPhasePreparation(current,AC.errorText(response,fallback));};
    AC.claimTwoPhaseAppAction=async(session,action,guard)=>{
      const expected=guard.action,workspace=guard.workspace;let response=await callApi("agent_present_app_action",session.session_id,expected.action_id,workspace);
      if(response.code)return AC.reconcileTwoPhaseWrite(session,action,"resolving",response,"操作卡未能持久呈现；App没有执行任何操作。");
      const presented=response.data||{},presentedCard=presented.card&&typeof presented.card==="object"?presented.card:presented.action,uiReceipt=presented.ui_receipt||presentedCard?.ui_receipt;
      const presentError=presented.status!=="presented_for_user"?"操作卡没有进入已呈现状态":AC.twoPhaseCardError(presented,expected,{statuses:["presented"]})||(typeof uiReceipt!=="string"||!/^ui_[A-Za-z0-9_-]{43}$/.test(uiReceipt)?"操作卡缺少有效的界面回执":presentedCard.ui_receipt!==uiReceipt?"操作卡界面回执不一致":null);
      if(presentError)return AC.failTwoPhasePreparation(action,`${presentError}；App没有执行任何操作。`);
      action=AC.twoPhaseCard(session,action,presented);const beforeClaimError=AC.twoPhaseGuardError(session,guard);if(beforeClaimError)return AC.failTwoPhasePreparation(action,`${beforeClaimError}；操作尚未领取，App没有执行任何操作。`);
      response=await callApi("agent_claim_app_action",session.session_id,expected.action_id,uiReceipt,workspace);if(response.code)return AC.reconcileTwoPhaseWrite(session,action,"presented",response,"操作卡未能持久领取；App没有执行任何操作。");
      const data=response.data||{},claimedStatus=data.status,cardError=AC.twoPhaseCardError(data,expected,{receipt:uiReceipt,statuses:claimedStatus==="claimed_for_external_execution"?["presented"]:[]}),topReceiptError=Object.hasOwn(data,"ui_receipt")&&data.ui_receipt!==uiReceipt?"操作领取响应界面回执不一致":null,requestError=claimedStatus==="claimed_for_external_execution"?AC.twoPhaseRequestError(data.request,expected,workspace):data.request!=null?"非执行响应不得携带执行请求":null,safeWithoutExecution=new Set(["recovered_without_replay","already_settled","completed","cancelled","failed"]),statusError=claimedStatus!=="claimed_for_external_execution"&&!safeWithoutExecution.has(claimedStatus)?"操作领取响应状态无法核验":null;
      if(cardError||topReceiptError||requestError||statusError){action=session.appActions.find(row=>row.action_id===expected.action_id)||action;action.ui_receipt=uiReceipt;action.externalExecutionClaimed=true;action.status="claimed_for_external_execution";return {action,uiReceipt,request:{},workspace,authorizationError:cardError||topReceiptError||requestError||statusError};}
      action=AC.twoPhaseCard(session,action,data);if(data.status!=="claimed_for_external_execution"){action.localBusy=false;action.status=action.status||"failed";AC.notice(data.status==="recovered_without_replay"?"检测到上次执行结果未知；为避免重复文件或导航操作，本次没有自动重放。":"操作卡未进入可执行状态；App没有执行任何操作。",true);AC.refresh();return null;}
      action.ui_receipt=uiReceipt;action.externalExecutionClaimed=true;action.status="claimed_for_external_execution";return {action,uiReceipt,request:AC.clone(data.request),workspace};
    };
    AC.settleTwoPhaseAppAction=async(session,claimed,result)=>{let action=claimed.action;const status=["completed","cancelled","failed"].includes(result?.status)?result.status:"failed",message=String(result?.message||(status==="completed"?"App操作已完成。":status==="cancelled"?"用户取消了App操作。":"App操作未能完成。")).slice(0,1000),guardRequired=claimed.request?.dirty_open_guard_required===true||action.dirty_open_guard_required===true,dirtyOpenGuardPassed=result?.dirty_open_guard_passed===true||(guardRequired&&action.action==="open_document"&&status==="completed"),outcome={status,message,dirty_open_guard_passed:dirtyOpenGuardPassed};const response=await callApi("agent_settle_app_action",session.session_id,action.action_id,claimed.uiReceipt,outcome,claimed.workspace||AC.workspace(session.base));if(response.code){await AC.reconcile(session);action=session.appActions.find(row=>row.action_id===action.action_id)||action;if(["completed","cancelled","failed"].includes(action.status)){action.pendingSettlement=null;action.externalExecutionClaimed=false;action.execution_outcome_unknown=false;action.localBusy=false;AC.notice("App操作结果已从Host状态恢复；系统没有重放原操作。",action.status==="failed");AC.refresh();return true;}action.localBusy=false;action.status="claimed_for_external_execution";action.pendingSettlement=outcome;action.result_message="App操作已经发生，但结果尚未写回；为避免重复执行，本操作不会自动重放。";AC.notice(AC.errorText(response,action.result_message),true);AC.refresh();return false;}action=AC.twoPhaseCard(session,action,response.data||{});action.pendingSettlement=null;action.externalExecutionClaimed=false;action.execution_outcome_unknown=false;action.localBusy=false;const detached=response.data?.session_detached===true;if(detached){session.live=false;session.canResume=response.data?.can_resume===true;session.activation=response.data?.activation&&typeof response.data.activation==="object"?AC.clone(response.data.activation):{state:"cold",recovery:null};session.epoch++;AC.transport.invalidate(session);if(AC.state.active===session.session_id)AC.state.active=null;}AC.notice(detached?"已打开新文档；原会话已安全脱离。需要继续旧任务时，请重新打开原文档并显式恢复会话。":status==="completed"?"App操作已完成并记录。":status==="cancelled"?"已取消这项App操作。":"App操作未完成，原因已记录。",status==="failed");if(detached)await AC.loadHistory();AC.refresh();return true;};
    AC.recoverUnknownAppActionCore=async(session,action)=>{if(!session||action.localBusy||action.status!=="claimed_for_external_execution"||action.execution_outcome_unknown!==true||action.pendingSettlement)return false;const uiReceipt=action.ui_receipt;if(typeof uiReceipt!=="string"||!uiReceipt){AC.notice("未知操作缺少精确界面回执，无法安全解除。",true);return false;}action.localBusy=true;AC.refresh();const response=await callApi("agent_recover_app_action_without_replay",session.session_id,action.action_id,uiReceipt);if(response.code){await AC.reconcile(session);action=session.appActions.find(row=>row.action_id===action.action_id)||action;action.localBusy=false;if(action.execution_outcome_unknown!==true){AC.notice("未知App操作已从Host状态恢复；系统没有重放原操作。",action.status==="failed");AC.refresh();return true;}AC.notice(AC.errorText(response,"未知操作尚未解除；系统没有重放原操作。"),true);AC.refresh();return false;}action=AC.twoPhaseCard(session,action,response.data||{});action.pendingSettlement=null;action.externalExecutionClaimed=false;action.execution_outcome_unknown=false;action.localBusy=false;const detached=response.data?.session_detached===true;if(detached){session.live=false;session.canResume=response.data?.can_resume===true;session.activation=response.data?.activation&&typeof response.data.activation==="object"?AC.clone(response.data.activation):{state:"cold",recovery:null};session.epoch++;AC.transport.invalidate(session);if(AC.state.active===session.session_id)AC.state.active=null;}AC.notice(detached?"未知的打开操作已解除且没有重放；原会话已安全脱离，请回到原文档后显式恢复。":"未知操作已标记为失败并解除；文件或导航副作用没有重放。",true);if(detached)await AC.loadHistory();AC.refresh();return true;};
    AC.finishClaimedAppAction=async(session,claimed,result)=>{const pending=AC.pendingAppFiles.get(claimed.action.action_id);if(!pending||pending.finished)return false;pending.finished=true;AC.pendingAppFiles.delete(claimed.action.action_id);return AC.settleTwoPhaseAppAction(session,claimed,result);};
    AC.openClaimedFileAppAction=async(session,claimed,open)=>{const action=claimed.action,pending={session,action,claimed,finished:false,ready:false,buffered:null,controller:null};AC.pendingAppFiles.set(action.action_id,pending);const onResult=result=>{if(pending.finished)return;if(!pending.ready){pending.buffered=result;return;}AC.finishClaimedAppAction(session,claimed,result);};try{pending.controller=open({agentActionId:action.action_id,suggestedName:action.suggested_name||null,agentActionRequest:AC.clone(claimed.request),dirtyOpenGuardRequired:claimed.request?.dirty_open_guard_required===true,onAgentActionResult:onResult})||null;}catch(error){AC.pendingAppFiles.delete(action.action_id);return AC.settleTwoPhaseAppAction(session,claimed,{status:"failed",message:String(error?.message||"未能打开文件操作界面").slice(0,1000),dirty_open_guard_passed:false});}if(!pending.controller){AC.pendingAppFiles.delete(action.action_id);return AC.settleTwoPhaseAppAction(session,claimed,{status:"failed",message:"App没有确认文件操作界面已经呈现。",dirty_open_guard_passed:false});}pending.ready=true;action.status="presented";action.localBusy=false;AC.refresh();if(pending.buffered)await AC.finishClaimedAppAction(session,claimed,pending.buffered);return true;};
    AC.finishPresentedAppAction=async(session,action,result)=>{const pending=AC.pendingAppFiles.get(action.action_id);if(!pending||pending.finished)return false;pending.finished=true;AC.pendingAppFiles.delete(action.action_id);const status=["completed","cancelled","failed"].includes(result?.status)?result.status:"failed",fallback=status==="completed"?"文件操作已由App确认完成。":status==="cancelled"?"用户关闭了文件操作界面。":"App未能完成文件操作。";return AC.resolveAppAction(session,action,status,String(result?.message||fallback).slice(0,1000));};
    AC.presentFileAppAction=async(session,action,open,presentedMessage)=>{const pending={session,action,finished:false,presented:false,buffered:null,controller:null};AC.pendingAppFiles.set(action.action_id,pending);const onResult=result=>{if(pending.finished)return;if(!pending.presented){pending.buffered=result;return;}AC.finishPresentedAppAction(session,action,result);};try{pending.controller=open({agentActionId:action.action_id,suggestedName:action.suggested_name||null,onAgentActionResult:onResult})||null;}catch(error){AC.pendingAppFiles.delete(action.action_id);return AC.resolveAppAction(session,action,"failed",String(error?.message||"未能打开文件操作界面").slice(0,1000));}if(!pending.controller){AC.pendingAppFiles.delete(action.action_id);return AC.resolveAppAction(session,action,"failed","App没有确认文件操作界面已经呈现。");}if(!await AC.resolveAppAction(session,action,"presented",presentedMessage)){AC.pendingAppFiles.delete(action.action_id);return false;}pending.presented=true;if(pending.buffered)await AC.finishPresentedAppAction(session,action,pending.buffered);return true;};
    AC.openExportAction=(session,action,requestOptions)=>{
      if(typeof window.RS?.exportResult!=="function")throw new Error("当前App没有可用的导出接口");window.WS?.navigate?.("result");
      let dialog=null,settled=false;
      const report=(status,text)=>{if(settled)return false;settled=true;dialog.acFinished=true;requestOptions.onAgentActionResult({status,message:text});return true;};
      dialog=window.AppDialogKernel?.create?.({title:"导出计算结果",description:"选择格式后由App执行导出；只有App确认生成文件后，此请求才会完成。",
        className:"ac-export-dialog",onRequestClose:reason=>{if(!settled)report("cancelled",reason==="escape"?"用户关闭了导出界面。":"用户取消了导出请求。");return true;}});
      if(!dialog)return null;dialog.dataset.agentActionId=requestOptions.agentActionId;
      const message=UI.el("p",null,"ac-note"),buttons=[],finish=(status,text)=>{if(!report(status,text))return false;return dialog.finish(status);};
      dialog.appBody.append(message);
      for(const [format,label] of [["json","结构化结果"],["xlsx","结果快照表"],["formula","活公式工作簿"],["csv","文本表格"]]){
        const button=UI.button(label,async()=>{buttons.forEach(item=>item.disabled=true);dialog.setBusy(true);let exported=false;
          try{exported=await window.RS.exportResult(format);}catch(error){message.textContent=String(error?.message||"导出失败");}
          if(exported===true)finish("completed",`App已确认${label}导出完成。`);else{dialog.setBusy(false);buttons.forEach(item=>item.disabled=false);if(!message.textContent)message.textContent="App未确认生成文件；可改选其他格式或取消。";}});
        buttons.push(button);
      }
      const cancel=UI.button("取消",()=>finish("cancelled","用户取消了导出请求。"));buttons.push(cancel);dialog.setActions(...buttons);dialog.agentActionCancel=()=>finish("cancelled","用户取消了导出请求。");dialog.focusInitial(cancel);return dialog;
    };
    AC.executeTwoPhaseAppAction=async(session,action,cancel=false)=>{const eligibility=AC.appActionEligibility(session,action);if(!cancel&&!eligibility.enabled){AC.notice(eligibility.reason,true);AC.refresh();return;}const guard=AC.twoPhaseExecutionGuard(session,action);action.localBusy=true;action.status="resolving";AC.refresh();try{const claimed=await AC.claimTwoPhaseAppAction(session,action,guard);if(!claimed)return;action=claimed.action;const guardError=AC.twoPhaseGuardError(session,guard),finalEligibility=guardError||cancel?null:AC.appActionEligibility(session,action);if(claimed.authorizationError||guardError||!cancel&&!finalEligibility?.enabled){const reason=claimed.authorizationError||guardError||finalEligibility?.reason||"操作授权无法重新核验";await AC.settleTwoPhaseAppAction(session,claimed,{status:"failed",message:`领取后未执行App操作：${reason}。`,dirty_open_guard_passed:false});return;}if(cancel){await AC.settleTwoPhaseAppAction(session,claimed,{status:"cancelled",message:"用户未执行此操作。",dirty_open_guard_passed:false});return;}if(action.action==="save_current"){const workspace=AC.workspace(session.base),saved=workspace.document_kind==="whiteboard"?await window.WB?.save?.():await window.RT?.saveTemplate?.();await AC.settleTwoPhaseAppAction(session,claimed,{status:saved?"completed":"failed",message:saved?"当前文档已通过App原有保存与冲突保护写入。":"App未确认保存成功，当前文档仍按现有状态保留。",dirty_open_guard_passed:false});return;}if(action.action==="save_as"){const workspace=AC.workspace(session.base),open=opts=>workspace.document_kind==="whiteboard"?window.WB?.saveAs?.(opts):(window.WS?.navigate?.("rotation"),window.WS?.saveAs?.(opts));await AC.openClaimedFileAppAction(session,claimed,open);return;}if(action.action==="open_document"){await AC.openClaimedFileAppAction(session,claimed,opts=>window.WS?.openFile?.(opts));return;}if(action.action==="export_result"){if(!AC.workspace(session.base).can_export){await AC.settleTwoPhaseAppAction(session,claimed,{status:"failed",message:"当前没有可导出的已就绪计算结果。",dirty_open_guard_passed:false});return;}await AC.openClaimedFileAppAction(session,claimed,opts=>AC.openExportAction(session,action,opts));return;}const views={navigate_rotation:"rotation",navigate_result:"result",navigate_whiteboard:"whiteboard"},view=views[action.action];if(!view)throw new Error("未知App操作");if(typeof window.WS?.navigate!=="function"||typeof window.WS?.tab!=="function")throw new Error("当前App没有可核验的页面导航接口");await window.WS.navigate(view);if(window.WS.tab()!==view)throw new Error("App未确认已切换到目标页面");await AC.settleTwoPhaseAppAction(session,claimed,{status:"completed",message:`已转到${AC.appActionLabels[action.action]}对应页面。`,dirty_open_guard_passed:false});}catch(error){const current=session.appActions.find(row=>row.action_id===action.action_id)||action;if(current.externalExecutionClaimed&&current.ui_receipt)await AC.settleTwoPhaseAppAction(session,{action:current,uiReceipt:current.ui_receipt,request:{}},{status:"failed",message:String(error?.message||"App操作发生错误").slice(0,1000),dirty_open_guard_passed:false});else AC.failTwoPhasePreparation(current,String(error?.message||"App操作准备失败").slice(0,1000));}};
    AC.executeAppActionCore=async(session,action,cancel=false)=>{if(!session||action.localBusy)return;if(AC.isTwoPhaseAppAction(action)&&action.status==="claimed_for_external_execution"&&action.pendingSettlement){action.localBusy=true;AC.refresh();try{await AC.settleTwoPhaseAppAction(session,{action,uiReceipt:action.ui_receipt,request:{}},AC.clone(action.pendingSettlement));}finally{action.localBusy=false;AC.refresh();}return;}if(!["queued","presented"].includes(action.status))return;if(AC.isTwoPhaseAppAction(action)){await AC.executeTwoPhaseAppAction(session,action,cancel);return;}if(cancel){const pending=AC.pendingAppFiles.get(action.action_id);if(pending?.controller?.agentActionCancel){pending.controller.agentActionCancel();return;}action.localBusy=true;await AC.resolveAppAction(session,action,"cancelled","用户未执行此操作。");return;}if(action.status!=="queued")return;const eligibility=AC.appActionEligibility(session,action);if(!eligibility.enabled){AC.notice(eligibility.reason,true);AC.refresh();return;}action.localBusy=true;action.status="resolving";AC.refresh();try{if(action.action==="save_current"){const workspace=AC.workspace(session.base);if(!workspace.has_path){const open=opts=>workspace.document_kind==="whiteboard"?window.WB?.saveAs?.(opts):(window.WS?.navigate?.("rotation"),window.WS?.saveAs?.(opts));await AC.presentFileAppAction(session,action,open,"当前文档尚无保存位置，已呈现另存界面；正在等待用户保存或取消。");return;}const saved=workspace.document_kind==="whiteboard"?await window.WB?.save?.():await window.RT?.saveTemplate?.();await AC.resolveAppAction(session,action,saved?"completed":"failed",saved?"当前文档已通过App原有保存与冲突保护写入。":"App未确认保存成功，当前文档仍按现有状态保留。");return;}if(action.action==="save_as"){const workspace=AC.workspace(session.base),open=opts=>workspace.document_kind==="whiteboard"?window.WB?.saveAs?.(opts):(window.WS?.navigate?.("rotation"),window.WS?.saveAs?.(opts));await AC.presentFileAppAction(session,action,open,"已呈现另存界面；正在等待用户保存或取消。");return;}if(action.action==="open_document"){await AC.presentFileAppAction(session,action,opts=>window.WS?.openFile?.(opts),"已呈现打开界面；正在等待用户打开或取消。");return;}if(action.action==="export_result"){if(!AC.workspace(session.base).can_export){await AC.resolveAppAction(session,action,"failed","当前没有可导出的已就绪计算结果。");return;}await AC.presentFileAppAction(session,action,opts=>AC.openExportAction(session,action,opts),"已呈现带请求标识的导出界面；正在等待用户选择格式或取消。");return;}const views={navigate_rotation:"rotation",navigate_result:"result",navigate_whiteboard:"whiteboard"},view=views[action.action];if(!view)throw new Error("未知App操作");if(typeof window.WS?.navigate!=="function"||typeof window.WS?.tab!=="function")throw new Error("当前App没有可核验的页面导航接口");await window.WS.navigate(view);if(window.WS.tab()!==view)throw new Error("App未确认已切换到目标页面");await AC.resolveAppAction(session,action,"completed",`已转到${AC.appActionLabels[action.action]}对应页面。`);}catch(error){await AC.resolveAppAction(session,action,"failed",String(error?.message||"App操作发生错误").slice(0,1000));}};

    const execute=(session,action,cancel=false)=>transactions.runAppAction(session,action,cancel?"cancel":"execute",()=>AC.executeAppActionCore(session,action,cancel));
    const executeTwoPhase=(session,action,cancel=false)=>transactions.runAppAction(session,action,cancel?"cancel":"execute",()=>AC.executeTwoPhaseAppAction(session,action,cancel));
    const recoverUnknown=(session,action)=>transactions.runAppAction(session,action,"recover_unknown",()=>AC.recoverUnknownAppActionCore(session,action));
    return Object.freeze({
      eligibility:AC.appActionEligibility,execute,recoverUnknown,
      executeTwoPhase,
      pendingFiles:AC.pendingAppFiles,transactions,
    });
  }

  /** Sole owner of candidate prepare/apply/ACK/recovery transitions. */
  function createCandidateController(options = {}) {
    const window=options.window,callApi=requiredFunction(options,"api");
    if(!window)throw new TypeError("domain_transactions_candidate_window_missing");
    const AC={
      state:options.state,selected:requiredFunction(options,"selected"),active:requiredFunction(options,"active"),
      hasUnsettledAppAction:requiredFunction(options,"hasUnsettledAppAction"),notice:requiredFunction(options,"notice"),refresh:requiredFunction(options,"refresh"),
      capture:requiredFunction(options,"capture"),clone:requiredFunction(options,"clone"),workspace:requiredFunction(options,"workspace"),same:requiredFunction(options,"same"),
      sameContent:requiredFunction(options,"sameContent"),currentMatches:requiredFunction(options,"currentMatches"),
      reconcile:requiredFunction(options,"reconcile"),receiveCommand:requiredFunction(options,"receiveCommand"),errorText:requiredFunction(options,"errorText"),
      receiveProtocolText:requiredFunction(options,"receiveProtocolText"),recordAppAction:requiredFunction(options,"recordAppAction"),readModel:requiredFunction(options,"readModel"),
      publishCandidate:requiredFunction(options,"publishCandidate"),canApply:requiredFunction(options,"canApply"),loadHistory:requiredFunction(options,"loadHistory"),transport:options.transport,
    };
    if(!AC.state||!AC.transport||typeof AC.transport.invalidate!=="function")throw new TypeError("domain_transactions_candidate_state_ports_missing");
    const transactions=options.transactions||createController();
    const candidateIdentity=value=>Object.freeze({
      revision:Number.isSafeInteger(value?.revision)?value.revision:null,
      receipt:typeof value?.receipt_fingerprint==="string"?value.receipt_fingerprint:null,
    });
    const projectionRevision=session=>{
      const model=AC.readModel(session)||{};
      const projection=Number.isSafeInteger(model.projectionRevision)?model.projectionRevision:0;
      const store=Number.isSafeInteger(model.store_revision)?model.store_revision:projection;
      return Object.freeze({projection,store});
    };
    let candidateLoadGeneration=0;
    const candidateStamp=(session,candidate=session?.candidate,operation="read")=>{
      const identity=candidateIdentity(candidate),model=AC.readModel(session)||{},storeIdentity=candidateIdentity(model.candidate_ref),revisions=projectionRevision(session);
      return Object.freeze({
        operation,operationGeneration:operation==="load"?++candidateLoadGeneration:candidateLoadGeneration,
        session,sessionId:session?.session_id||null,sessionEpoch:session?.epoch,
        selectedId:AC.state.selected??AC.selected()?.session_id??null,viewEpoch:AC.state.viewEpoch,
        connectionGeneration:AC.state.connectionGeneration??0,
        revision:identity.revision,receipt:identity.receipt,
        storeCandidateRevision:storeIdentity.revision,storeCandidateReceipt:storeIdentity.receipt,
        projectionRevision:revisions.projection,storeRevision:revisions.store,
      });
    };
    const candidateStampCurrent=(stamp,{identity=true}={})=>{
      if(!stamp?.session||AC.state.closing)return false;
      const selected=AC.selected(),session=stamp.session,current=AC.state.sessions instanceof Map?AC.state.sessions.get(stamp.sessionId):selected;
      if(current!==session||selected!==session||session.session_id!==stamp.sessionId||session.epoch!==stamp.sessionEpoch)return false;
      if((AC.state.selected??selected?.session_id??null)!==stamp.selectedId||AC.state.viewEpoch!==stamp.viewEpoch)return false;
      if((AC.state.connectionGeneration??0)!==stamp.connectionGeneration)return false;
      if(stamp.operation==="load"&&stamp.operationGeneration!==candidateLoadGeneration)return false;
      const revisions=projectionRevision(session);
      if(revisions.projection!==stamp.projectionRevision||revisions.store!==stamp.storeRevision)return false;
      const storeIdentity=candidateIdentity(AC.readModel(session)?.candidate_ref);
      if(storeIdentity.revision!==stamp.storeCandidateRevision||storeIdentity.receipt!==stamp.storeCandidateReceipt)return false;
      if(identity){const currentIdentity=candidateIdentity(session.candidate);if(currentIdentity.revision!==stamp.revision||currentIdentity.receipt!==stamp.receipt)return false;}
      return true;
    };
    const loadCandidate=async session=>{
      if(!session||session!==AC.selected())return Object.freeze({ok:false,stale:true});
      const stamp=candidateStamp(session,session.candidate,"load");let response;
      try{response=await callApi("agent_candidate",session.session_id);}catch(error){response=error;}
      if(!candidateStampCurrent(stamp))return Object.freeze({ok:false,stale:true});
      if(!response||response.code)return Object.freeze({ok:false,stale:false,response});
      const incoming=response.data?.candidate||response.data;
      const identity=candidateIdentity(incoming);
      if(!incoming||typeof incoming!=="object"||identity.revision===null||!identity.receipt)return Object.freeze({ok:false,stale:false,invalid:true,response});
      if(stamp.revision!==null&&identity.revision<stamp.revision)return Object.freeze({ok:false,stale:true});
      if(stamp.storeCandidateRevision!==null&&(identity.revision!==stamp.storeCandidateRevision||identity.receipt!==stamp.storeCandidateReceipt))return Object.freeze({ok:false,stale:true});
      if(!candidateStampCurrent(stamp))return Object.freeze({ok:false,stale:true});
      if(!AC.publishCandidate(session,incoming))return Object.freeze({ok:false,stale:true});
      return Object.freeze({ok:true,candidate:AC.clone(session.candidate),stamp:candidateStamp(session,session.candidate,"loaded")});
    };
    const readCandidate=async(session,candidate,resource,pointer="",offset=0,limit=50)=>{
      const stamp=candidateStamp(session,candidate,"read");
      if(!candidateStampCurrent(stamp))return Object.freeze({ok:false,stale:true});
      let response;try{response=await callApi("agent_candidate_read",session.session_id,resource,pointer,offset,limit);}catch(error){response=error;}
      if(!candidateStampCurrent(stamp))return Object.freeze({ok:false,stale:true});
      return Object.freeze({ok:!response?.code,response});
    };

    AC.confirmCandidateApplied=async(session,candidate,base)=>{
      const request=()=>callApi("agent_candidate_applied",session.session_id,base.kind,base.document,candidate.revision,candidate.receipt_fingerprint,AC.workspace(base),base.applicationPayload);let response=await request();
      if(response.code){const refreshed=await AC.reconcile(session),transaction=["accepted","timeout"].includes(refreshed?.status)?session.applicationIntent:null,intent=transaction?.intent;if(transaction?.status==="committed"&&intent?.kind===base.kind&&intent?.revision===candidate.revision&&intent?.receipt_fingerprint===candidate.receipt_fingerprint)return {ok:true,data:{status:"applied",kind:base.kind,revision:candidate.revision,receipt_fingerprint:candidate.receipt_fingerprint,app_actions:session.appActions||[]}};return {ok:false,message:AC.errorText(response,"候选已经写入编辑器，但Host未确认应用回执。请保持当前文档不变并重新点击应用。")} ;}
      const data=response.data||{};if(data.status!=="applied"||data.kind!==base.kind||data.revision!==candidate.revision||data.receipt_fingerprint!==candidate.receipt_fingerprint)return {ok:false,message:"候选已经写入编辑器，但Host返回的应用回执与当前候选不一致。请保持当前文档不变并重新点击应用。"};return {ok:true,data};
    };
    AC.abortPreparedApplication=async(session,base)=>{const response=await callApi("agent_abort_candidate_application",session.session_id,base.document,AC.workspace(base),base.applicationPayload);if(response.code)return {ok:false,message:AC.errorText(response,"Host无法确认候选仍未应用；事务保持阻断状态。")} ;const receipt=AC.receiveCommand(session,response.data||{},0,{applicationSettlement:"abort"});if(!receipt.accepted)return {ok:false,message:AC.receiveProtocolText(receipt.reason)};session.pendingApplicationAck=null;session.applicationRecoveryPending=false;session.applicationIntent=null;return {ok:true,data:response.data||{}};};
    AC.releaseCommittedApplication=session=>{session.pendingApplicationAck=null;session.applicationRecoveryPending=false;session.applicationIntent=null;session.coldPendingApplication=false;session.applied=false;session.appliedReceipt=null;};
    AC.finishCandidateApplication=(session,candidate,base,appActions=[])=>{session.pendingApplicationAck=null;session.applicationRecoveryPending=false;session.applicationIntent=null;session.applied=true;session.appliedReceipt=candidate.receipt_fingerprint;session.title=base?.title||session.title;for(const action of appActions)AC.recordAppAction(session,action);AC.notice(candidate.application_kind==="whiteboard_draft"?"白板阶段草稿已应用，尚未保存或计算。可在同一聊天继续下一阶段；使用顶部“保存”留存白板。":base.kind==="whiteboard"?"已应用审核过的白板与对应计算结果，尚未保存。可继续同一聊天完善，或使用顶部“保存”留存白板。":"候选已应用为未保存输入。可继续同一聊天完善；请检查结果，使用顶部“保存”写入文件。");return true;};
    AC.postCandidateApplication=async kind=>{try{window.WS?.navigate?.(kind==="whiteboard"?"whiteboard":"rotation");if(kind==="native"&&window.PL?.state?.plan?.resolution?.state==="ready")await window.RS?.runCalc?.();}catch(error){AC.notice(`候选已经应用并确认，但自动打开或计算未完成：${String(error?.message||error).slice(0,500)}`,true);}};
    AC.retryPendingApplication=async()=>{const session=AC.selected(),pending=session?.pendingApplicationAck,intent=session?.applicationIntent?.intent,identity=intent||(pending?{kind:pending.base?.kind,revision:pending.revision,receipt_fingerprint:pending.receipt}:null);if(!session||!session.live||AC.state.busy||AC.active()||!identity)return;if(AC.hasUnsettledAppAction(session)){AC.notice("请先完成、写回或解除等待中的App操作，再补记候选应用回执。",true);return;}let base;try{base=AC.capture(identity.kind);}catch(error){AC.notice(error.message,true);return;}if(pending&&!AC.same(base,pending.base)){if(!AC.sameContent(base,pending.base)){AC.notice("当前文档内容已不同于待补记候选，不能提交旧回执。",true);return;}pending.base=base;session.base=base;}const candidate={...(session.candidate||{}),revision:identity.revision,receipt_fingerprint:identity.receipt_fingerprint};AC.state.busy="accept";AC.refresh();try{const acknowledged=await AC.confirmCandidateApplied(session,candidate,base);if(!acknowledged.ok){AC.notice(acknowledged.message,true);return;}if(!AC.currentMatches(base)){AC.releaseCommittedApplication(session);AC.notice("Host已确认候选应用，但确认期间当前文档发生变化；事务锁已解除，旧候选不会标记为当前方案。请基于当前文档新建任务。",true);return;}AC.finishCandidateApplication(session,candidate,base,acknowledged.data.app_actions||[]);await AC.postCandidateApplication(base.kind);}finally{AC.state.busy=null;AC.refresh();}};
    AC.cancelPendingApplication=async()=>{const session=AC.selected();if(!session||AC.state.busy||AC.active()||session.applicationRecoveryPending!==true)return;if(AC.hasUnsettledAppAction(session)){AC.notice("请先完成、写回或解除等待中的App操作，再取消候选应用事务。",true);return;}let base;try{base=AC.capture(session.kind);}catch(error){AC.notice(error.message,true);return;}AC.state.busy="abort-application";AC.refresh();try{const result=await AC.abortPreparedApplication(session,base);if(!result.ok){AC.notice(result.message,true);return;}session.base=base;session.live=true;AC.notice("未应用的候选事务已安全取消；当前原始文档已重新绑定。可继续聊天或重新审核候选。");}finally{AC.state.busy=null;AC.refresh();}};
    AC.recoverPendingCandidateApplication=async discardCurrent=>{
      discardCurrent=discardCurrent===true;const session=AC.selected();if(!session||AC.state.busy||AC.active()||session.applicationRecoveryPending!==true)return false;if(AC.hasUnsettledAppAction(session)){AC.notice("请先完成、写回或解除等待中的App操作，再恢复候选应用事务。",true);return false;}if(discardCurrent&&!window.confirm("放弃当前文档在候选写入后的所有未保存编辑，并恢复Host中已经审核的候选？此操作只允许作用于同一白板或方案。"))return false;
      let before;try{before=AC.capture(session.kind);}catch(error){AC.notice(error.message,true);return false;}const viewEpoch=AC.state.viewEpoch,sessionEpoch=session.epoch,tab=window.WS?.tab?.(),stableView=()=>!AC.state.closing&&viewEpoch===AC.state.viewEpoch&&sessionEpoch===session.epoch&&tab===window.WS?.tab?.(),unchanged=()=>stableView()&&AC.currentMatches(before);AC.state.busy="recover-application";AC.refresh();
      try{const response=await callApi("agent_recover_candidate_application",session.session_id,before.document,AC.workspace(before),before.applicationPayload,discardCurrent);if(response.code){AC.notice(AC.errorText(response,"Host无法从当前原始文档恢复候选；事务仍保持阻断状态。"),true);return false;}const data=response.data||{},transaction=data.application_intent,intent=transaction?.intent;if(!unchanged()){AC.notice("恢复候选期间当前文档或页签发生变化，候选未写入；事务仍保持阻断状态。",true);return false;}if(!transaction||!["prepared","baseline_advanced"].includes(transaction.status)||!intent){AC.notice("Host没有返回可核验的待恢复事务，候选未写入。",true);return false;}if(data.kind!==before.kind||intent.kind!==before.kind||data.revision!==intent.revision||data.receipt_fingerprint!==intent.receipt_fingerprint){AC.notice("Host返回的恢复目标与持久事务身份不一致，候选未写入。",true);return false;}const applicationKind=data.application_kind,candidate={...(session.candidate||{}),revision:data.revision,receipt_fingerprint:data.receipt_fingerprint,application_kind:applicationKind,issues:Array.isArray(data.issues)?data.issues:session.candidate?.issues||[]};
        if(before.kind==="whiteboard"){if(!data.board||data.board.board_id!==before.document.board_id||typeof window.WB?.applyAgentDraft!=="function"){AC.notice("恢复目标缺少同一白板，候选未写入。",true);return false;}if(applicationKind==="whiteboard_draft"){if(data.calculated_delivery!=null||!["build","staged"].includes(data.workflow)){AC.notice("白板草稿恢复包的阶段或计算载荷不一致，候选未写入。",true);return false;}window.WB.applyAgentDraft(AC.clone(data.board),candidate.issues);}else if(applicationKind==="whiteboard_calculated"){const delivery=data.calculated_delivery;if(!delivery||typeof delivery!=="object"||Object.hasOwn(delivery,"path")||Object.hasOwn(delivery,"file_sha")||typeof delivery.delivery_capability!=="string"||!/^wdc_v1_[A-Za-z0-9_-]{43}$/.test(delivery.delivery_capability)){AC.notice("恢复目标缺少Host重新签发的无路径计算交付凭证，候选未写入。",true);return false;}window.WB.applyAgentDraft(AC.clone(data.board),candidate.issues,AC.clone(delivery),{sessionId:session.session_id,deliveryCapability:delivery.delivery_capability});}else{AC.notice("恢复目标的白板应用类型无法识别，候选未写入。",true);return false;}}
        else{if(!data.plan||data.plan.plan_id!==before.document.plan_id){AC.notice("恢复目标缺少同一原生方案，候选未写入。",true);return false;}window.PL.replace(AC.clone(data.plan),before.path,before.sha,false);window.PL.state.savedJson=before.savedJson;window.PL.renderStatus();}
        const applied=AC.capture(before.kind);session.base=applied;session.live=true;session.canResume=false;session.applicationIntent=AC.clone(transaction);session.applicationRecoveryPending=true;session.coldPendingApplication=false;session.pendingApplicationAck={revision:candidate.revision,receipt:candidate.receipt_fingerprint,base:applied,originalBase:before};if(!AC.currentMatches(applied)){AC.notice("恢复目标写入后文档状态不稳定，Host回执尚未提交；请保持当前文档不变后补记。",true);return false;}const acknowledged=await AC.confirmCandidateApplied(session,candidate,applied);if(!acknowledged.ok){AC.notice(acknowledged.message,true);return false;}if(!stableView()||!AC.currentMatches(applied)){AC.releaseCommittedApplication(session);AC.notice("Host已确认恢复的候选应用，但确认期间当前文档或页签发生变化；事务锁已解除，旧候选不会标记为当前方案。请基于当前文档新建任务。",true);return false;}AC.finishCandidateApplication(session,candidate,applied,acknowledged.data.app_actions||[]);await AC.postCandidateApplication(applied.kind);return true;
      }catch(error){AC.notice(String(error?.message||"恢复候选应用失败；事务仍保持阻断状态。").slice(0,1000),true);return false;}finally{AC.state.busy=null;AC.refresh();}
    };
    AC.acceptCore=async(session,candidate,errorNode)=>{
      const fail=message=>{if(errorNode)errorNode.textContent=message;AC.notice(message,true);return false;};if(AC.active()||!AC.canApply(session,candidate))return fail("候选尚未满足应用条件，请等运行任务结束并处理阻断项。");if(candidate.revision!==session.candidate?.revision||candidate.receipt_fingerprint!==session.candidate?.receipt_fingerprint)return fail("候选已更新，请重新打开差异审核。");if(!candidate.receipt_fingerprint||candidate.revision==null)return fail("候选缺少可核验的修订或审核指纹，不能应用。");const pending=session.pendingApplicationAck;
      if(session.connectionKind==="native_api"&&pending&&pending.revision===candidate.revision&&pending.receipt===candidate.receipt_fingerprint){if(!AC.currentMatches(pending.base))return fail("候选已经写入过编辑器，但当前文档随后发生变化，不能补记旧回执。请基于当前方案新建任务。");AC.state.busy="accept";AC.refresh();try{const acknowledged=await AC.confirmCandidateApplied(session,candidate,pending.base);if(!acknowledged.ok)return fail(acknowledged.message);if(!AC.currentMatches(pending.base)){AC.releaseCommittedApplication(session);return fail("Host已确认候选应用，但确认期间当前文档发生变化；事务锁已解除，旧候选不会标记为当前方案。请基于当前文档新建任务。");}const finished=AC.finishCandidateApplication(session,candidate,pending.base,acknowledged.data.app_actions||[]);await AC.postCandidateApplication(pending.base.kind);return finished;}finally{AC.state.busy=null;AC.refresh();}}
      let before;try{before=AC.capture(session.kind);}catch(error){return fail(error.message);}if(session.base&&!AC.same(before,session.base))return fail("当前输入已不同于发起任务时的快照。请保留候选供参考，并基于当前方案新建任务。");const viewEpoch=AC.state.viewEpoch,sessionEpoch=session.epoch,tab=window.WS?.tab?.(),stableView=()=>!AC.state.closing&&viewEpoch===AC.state.viewEpoch&&sessionEpoch===session.epoch&&tab===window.WS?.tab?.(),unchanged=()=>stableView()&&AC.currentMatches(before);let prepared=false;
      const rejectPrepared=async message=>{if(!prepared||session.connectionKind!=="native_api")return fail(message);if(!AC.currentMatches(before)){await AC.reconcile(session);session.applicationRecoveryPending=true;return fail(`${message} Host已保留待恢复的候选应用事务；当前文档有变化，系统没有自动取消。`);}const aborted=await AC.abortPreparedApplication(session,before);if(!aborted.ok){session.applicationRecoveryPending=true;return fail(`${message} ${aborted.message}`);}return fail(message);};
      AC.state.busy="accept";AC.refresh();try{if(before.kind==="native"&&before.path&&before.sha){const disk=await callApi("inspect_plan_file",before.path);if(disk.code||disk.data.file_sha!==before.sha)return fail("磁盘方案已变化或无法核验，请先处理文件变化，再应用候选。当前输入与候选均保留。");if(!unchanged())return fail("文件核验期间文档或装备草稿发生变化，候选未应用。");}const r=await callApi("agent_accept",session.session_id,before.kind,before.document,candidate.revision,candidate.receipt_fingerprint,before.applicationPayload);if(r.code){if(AC.readModel(session)?.connectionKind==="native_api")await AC.reconcile(session);return fail(AC.errorText(r,"候选未通过应用校验。"));}prepared=AC.readModel(session)?.connectionKind==="native_api";if(prepared){session.applicationRecoveryPending=true;session.applicationIntent={status:"prepared"};}if(!unchanged())return await rejectPrepared("审核期间当前文档或装备草稿发生变化，候选未写入编辑器。请先处理草稿，再重新审核。");if(r.data.kind!==before.kind)return await rejectPrepared("候选工作区类型与当前文档不一致，未应用。");if(r.data.revision!==candidate.revision||r.data.receipt_fingerprint!==candidate.receipt_fingerprint)return await rejectPrepared("返回候选的修订或审核指纹不一致，未应用。");
        if(before.kind==="whiteboard"&&candidate.application_kind==="whiteboard_draft"){if(!["build","staged"].includes(r.data.workflow)||!r.data.board||r.data.revision!==candidate.revision||r.data.receipt_fingerprint!==candidate.receipt_fingerprint)return await rejectPrepared("白板草稿缺少一致的修订和审核指纹，未应用。");if(r.data.board.board_id!==before.document.board_id||typeof window.WB?.applyAgentDraft!=="function")return await rejectPrepared("候选白板身份或编辑接口不一致，未应用。");window.WB.applyAgentDraft(AC.clone(r.data.board),candidate.issues||[]);session.base=AC.capture("whiteboard");if(session.connectionKind==="native_api")session.pendingApplicationAck={revision:candidate.revision,receipt:candidate.receipt_fingerprint,base:session.base,originalBase:before};}
        else if(before.kind==="whiteboard"){if(r.data.workflow&&!['calculate','staged'].includes(r.data.workflow))return await rejectPrepared("返回结果并非当前计算任务，未应用。");if(!r.data.board)return await rejectPrepared("候选缺少白板，未应用。");if(r.data.board.board_id!==before.document.board_id)return await rejectPrepared("候选白板身份不一致，未应用。");let verifiedData;if(r.data.calculated_delivery&&typeof r.data.calculated_delivery==="object"){if(session.connectionKind!=="native_api")return fail("CLI候选不能绕过既有计算包校验，未应用。");if(Object.hasOwn(r.data.calculated_delivery,"path")||Object.hasOwn(r.data.calculated_delivery,"file_sha"))return await rejectPrepared("原生Agent返回了不应暴露的计算包路径，未应用。");if(typeof r.data.calculated_delivery.delivery_capability!=="string"||!/^wdc_v1_[A-Za-z0-9_-]{43}$/.test(r.data.calculated_delivery.delivery_capability))return await rejectPrepared("原生Agent候选缺少可用于后续换装重算的受验交付凭证，未应用。");verifiedData=AC.clone(r.data.calculated_delivery);}else{if(session.connectionKind==="native_api")return await rejectPrepared("原生Agent候选缺少Host已校验的计算结果，未应用。");if(!r.data.result_path)return fail("候选缺少已晋升保存的计算包，未应用。");const verified=await callApi("whiteboard_read_result",AC.clone(r.data.board),r.data.result_path);if(verified.code)return fail(AC.errorText(verified,"白板回传与用户意图不一致，未应用。"));verifiedData=verified.data;}if(!unchanged())return await rejectPrepared("结果校验期间白板或装备草稿发生变化，旧候选未应用。");const nativeDelivery=session.connectionKind==="native_api"?{sessionId:session.session_id,deliveryCapability:verifiedData.delivery_capability}:null;window.WB.applyAgentDraft(AC.clone(r.data.board),candidate.issues||[],verifiedData,nativeDelivery);session.base=AC.capture("whiteboard");if(session.connectionKind==="native_api")session.pendingApplicationAck={revision:candidate.revision,receipt:candidate.receipt_fingerprint,base:session.base,originalBase:before};}
        else{if(!r.data.plan)return await rejectPrepared("候选缺少原生方案，未应用。");window.PL.replace(r.data.plan,before.path,before.sha,false);window.PL.state.savedJson=before.savedJson;window.PL.renderStatus();session.base=AC.capture("native");if(session.connectionKind==="native_api")session.pendingApplicationAck={revision:candidate.revision,receipt:candidate.receipt_fingerprint,base:session.base,originalBase:before};}
        if(session.connectionKind==="native_api"){const appliedBase=session.base,acknowledged=await AC.confirmCandidateApplied(session,candidate,appliedBase);if(!acknowledged.ok)return fail(acknowledged.message);if(!stableView()||!AC.currentMatches(appliedBase)){AC.releaseCommittedApplication(session);return fail("Host已确认候选应用，但确认期间当前文档或页签发生变化；事务锁已解除，旧候选不会标记为当前方案。请基于当前文档新建任务。");}const finished=AC.finishCandidateApplication(session,candidate,appliedBase,acknowledged.data.app_actions||[]);await AC.postCandidateApplication(appliedBase.kind);return finished;}const finished=AC.finishCandidateApplication(session,candidate,session.base,r.data.app_actions||[]);await AC.postCandidateApplication(session.base.kind);return finished;
      }catch(error){return await rejectPrepared(String(error?.message||"候选应用失败。").slice(0,1000));}finally{AC.state.busy=null;AC.refresh();}
    };
    const selectedCommand=(intent,operation)=>{const session=AC.selected();return session?transactions.runCandidate(session,intent,operation):Promise.resolve(false);};
    const retryPending=()=>selectedCommand("retry_pending",()=>AC.retryPendingApplication());
    const cancelPending=()=>selectedCommand("cancel_pending",()=>AC.cancelPendingApplication());
    const recoverPending=discardCurrent=>selectedCommand(discardCurrent===true?"recover_pending_discard":"recover_pending",()=>AC.recoverPendingCandidateApplication(discardCurrent));
    const accept=(session,candidate,errorNode)=>transactions.applyCandidate(session,candidate,()=>AC.acceptCore(session,candidate,errorNode));
    return Object.freeze({loadCandidate,readCandidate,retryPending,cancelPending,recoverPending,accept,transactions});
  }

  global.AgentDomainTransactions = Object.freeze({
    FILE_ACTIONS, CANDIDATE_FILE_ACTIONS, LABELS, issueText,
    appActionEligibility, hasUnsettledAppAction, hasPendingApplication,
    createCommandGate, createController, appActionViewModel, createAppActionRenderer,
    createAppActionController, createCandidateController,
  });
})(window);
