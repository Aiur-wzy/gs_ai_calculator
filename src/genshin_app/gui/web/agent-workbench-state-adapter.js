(function (global) {
  "use strict";

  // The adapter is the only production repository for workbench shell state
  // and mutable domain sidecars.  Host event facts and cursors remain owned by
  // AgentWorkbenchStore; controllers submit reviewed mutations through here.
  function createShell(selectors) {
    if (!selectors || typeof selectors.createUiState !== "function") throw new TypeError("workbench_state_selectors_missing");
    return {
      state:{open:false,connection:"disconnected",busy:null,candidates:[],models:[],cliId:"",connectedCliId:null,scanEpoch:0,scan:null,providers:[],model:"",effort:"",
        credential:null,capabilities:{},runtimeStatusCursor:0,runtimeStatusGeneration:0,runtimeStatusMonitor:0,runtimePrepare:null,resumeSupervisor:null,budget:null,budgetDraft:null,budgetLoading:false,budgetSaving:false,budgetEpoch:0,dshSettings:null,dshSettingsDraft:"",dshSettingsLoading:false,dshSettingsSaving:false,dshSettingsEpoch:0,dshSettingsError:"",sessions:new Map(),selected:null,active:null,history:[],closing:false,request:0,viewEpoch:0,connectionGeneration:0,
        hostUi:{},workbenchCapabilities:{turn_outline:false,conversation_search:false,history_window:false,telemetry_bundle:false,workbench_foundation:false,structured_question_batch:false,durable_inbox:false,document_checkpoints:false,session_lifecycle:false},workbench:selectors.createUiState(),questionTakeover:false,panel:{width:400,settingsVisible:false,detailsVisible:false,chatVisible:true,viewMode:"docked"}},
      nodes:{},messageNodes:new Map(),toolNodes:new Map(),actionNodes:new Map(),pendingAppFiles:new Map(),
    };
  }

  function create(options = {}) {
    const state=options.state,eventStore=options.eventStore,selectors=options.selectors,clone=options.clone;
    if(!state||!eventStore||typeof eventStore.snapshot!=="function"||!selectors||typeof selectors.selectSession!=="function"||typeof clone!=="function")throw new TypeError("workbench_state_adapter_ports_missing");
    const storeSnapshot=session=>session?.session_id?eventStore.snapshot(session.session_id):null;
    const storeModel=session=>session?.session_id?selectors.selectSession(storeSnapshot(session)):null;
    const readModel=session=>{
      if(!session)return null;const projected=storeModel(session);
      if(!projected)return {...session,messages:[],tools:[],usage:null,budgetStatuses:{},cursor:0,projectionRevision:0,store_revision:0};
      const result={...session,...projected,
        candidate:session.candidate,appActions:session.appActions||[],applicationIntent:session.applicationIntent,
        activation:session.activation,recovery:session.recovery,live:session.live,base:session.base,
        applied:session.applied,appliedReceipt:session.appliedReceipt,pendingApplicationAck:session.pendingApplicationAck,
        applicationRecoveryPending:session.applicationRecoveryPending===true||projected.applicationRecoveryPending===true,
        coldPendingApplication:session.coldPendingApplication===true||projected.coldPendingApplication===true,
        ui_state:{...(session.ui_state||{}),...(projected.ui_state||{})},workbench:session.workbench,
        error:session.error,pollFailureActive:session.pollFailureActive,
      };
      for(const key of ["kind","workflow","title","model","effort","providerId","connectionKind","run_id"])if(result[key]===null||result[key]===undefined)result[key]=session[key];
      return result;
    };
    const ackCursor=session=>storeModel(session)?.cursor||0;
    // Provider streaming can become idle before the Host has sealed and
    // published the turn's final candidate.  Keep the session active across
    // that settlement window; otherwise the transport scheduler stops on the
    // first idle projection and never observes the authoritative terminal
    // envelope.  The flag is owned by the GUI transport controller and is
    // cleared only by an explicit terminal/question projection.
    const isActive=session=>{const model=readModel(session);return session?.awaitingTurnSettlement===true||!!model?.active||["running","starting","preparing","stopping","cancelling"].includes(model?.state);};
    const workbenchState=(session=null)=>{
      if(!session)return state.workbench;
      if(!session.workbench)session.workbench=selectors.createUiState();
      return session.workbench;
    };
    const actionPatch=value=>{
      const action=value?.action&&typeof value.action==="object"?value.action:value;
      if(!action||typeof action.action_id!=="string")throw new Error("app_action_identity_missing");
      const patch={action_id:action.action_id};
      for(const key of ["action","suggested_name","status","candidate_revision","stage_revision","receipt_fingerprint","requires_applied_candidate","execution_mode","ui_receipt","dirty_open_guard_required","execution_outcome_unknown"])if(Object.hasOwn(action,key))patch[key]=action[key];
      for(const key of ["reason","result_message"])if(Object.hasOwn(action,key))patch[key]=String(action[key]||"");
      if(Object.hasOwn(action,"workspace"))patch.workspace=action.workspace&&typeof action.workspace==="object"?clone(action.workspace):null;
      for(const key of ["origin_binding","current_binding","origin_workspace"])if(Object.hasOwn(action,key))patch[key]=action[key]&&typeof action[key]==="object"?clone(action[key]):null;
      if(Object.hasOwn(action,"preconditions"))patch.preconditions=action.preconditions&&typeof action.preconditions==="object"?clone(action.preconditions):null;
      return patch;
    };
    const patchActionList=(rows,value)=>{const patch=actionPatch(value),old=rows.find(row=>row.action_id===patch.action_id);if(old)Object.assign(old,patch);else rows.push({action:null,reason:"",suggested_name:null,status:"queued",result_message:"",workspace:null,preconditions:null,...patch});return patch;};
    const recordAppAction=(session,value)=>{const action=value?.action&&typeof value.action==="object"?value.action:value;if(!session||!action||typeof action.action_id!=="string")return false;patchActionList(session.appActions||(session.appActions=[]),action);return true;};
    const publishCandidate=(session,value)=>{if(!session)return false;session.candidate=value==null?null:clone(value);return true;};
    const sidecarSnapshot=session=>({candidate:session.candidate,appActions:(session.appActions||[]).map(clone),applicationIntent:session.applicationIntent,activation:session.activation,recovery:session.recovery,canResume:session.canResume,pendingApplicationAck:session.pendingApplicationAck,applicationRecoveryPending:session.applicationRecoveryPending===true,coldPendingApplication:session.coldPendingApplication===true,applied:session.applied,appliedReceipt:session.appliedReceipt,error:session.pollFailureActive?null:session.error,pollFailureActive:false});
    const prepareDomainSidecar=(session,data,context={},ports={})=>{
      try{
        const draft=sidecarSnapshot(session),deferred=data.app_actions_deferred===true;
        if(Object.hasOwn(data,"app_actions")&&!deferred){const local=new Map(draft.appActions.map(row=>[row.action_id,row]));draft.appActions=[];for(const value of data.app_actions||[]){patchActionList(draft.appActions,value);const row=draft.appActions.at(-1),old=local.get(row.action_id);if(!data.reset_required&&old&&row.status===old.status)for(const key of ["pendingSettlement","externalExecutionClaimed","localBusy"])if(Object.hasOwn(old,key))row[key]=old[key];}}
        const candidateEvents=[];for(const event of data.events||[]){if(event.type==="app_action")patchActionList(draft.appActions,event.action||event);else if(event.type==="candidate"&&Object.hasOwn(event,"candidate"))candidateEvents.push(event.candidate);else if(event.type==="error")draft.error=ports.errorText?.(event,"本轮运行失败，请查看记录后重试。")||"本轮运行失败，请查看记录后重试。";}
        const identity=value=>value&&typeof value==="object"?`${value.revision??""}:${value.receipt_fingerprint??""}`:String(value);
        if(Object.hasOwn(data,"candidate")){for(const value of candidateEvents)if(identity(value)!==identity(data.candidate))throw new Error("candidate_identity_conflict");draft.candidate=data.candidate==null?null:clone(data.candidate);}else if(candidateEvents.length){const first=candidateEvents[0];for(const value of candidateEvents)if(identity(value)!==identity(first))throw new Error("candidate_identity_conflict");draft.candidate=clone(first);}
        if(data.activation&&typeof data.activation==="object"){draft.activation=clone(data.activation);if(Object.hasOwn(data.activation,"recovery"))draft.recovery=data.activation.recovery?clone(data.activation.recovery):null;}
        if(Object.hasOwn(data,"recovery"))draft.recovery=data.recovery&&typeof data.recovery==="object"?clone(data.recovery):null;
        if(Object.hasOwn(data,"can_resume"))draft.canResume=data.can_resume===true;
        if(Object.hasOwn(data,"application_intent")){
          const transaction=data.application_intent&&typeof data.application_intent==="object"?clone(data.application_intent):null,status=transaction?.status,pending=session.pendingApplicationAck,intent=transaction?.intent,prior=session.applicationIntent,priorStatus=prior?.status,priorIntent=prior?.intent,pendingStatuses=["prepared","baseline_advanced"],priorPending=session.applicationRecoveryPending===true||pendingStatuses.includes(priorStatus),sameIdentity=(left,right)=>Boolean(left&&right)&&left.kind===right.kind&&left.revision===right.revision&&left.receipt_fingerprint===right.receipt_fingerprint;
          if(pending&&["prepared","baseline_advanced","committed","aborted"].includes(status)){const expectedKind=pending.base?.kind||session.kind;if(!intent||intent.kind!==expectedKind||intent.revision!==pending.revision||intent.receipt_fingerprint!==pending.receipt)throw new Error("application_intent_pending_identity_mismatch");}
          if(!pending&&priorPending){if(pendingStatuses.includes(status)){if(priorIntent&&!sameIdentity(priorIntent,intent))throw new Error("application_intent_recovery_identity_mismatch");if(!priorIntent&&intent)throw new Error("application_intent_recovery_identity_unverified");}else{const explicitAbort=context.applicationSettlement==="abort"&&status==="aborted";if(!explicitAbort)throw new Error("application_intent_pending_transition_unverified");if(priorIntent&&!sameIdentity(priorIntent,intent))throw new Error("application_intent_recovery_identity_mismatch");}}
          draft.applicationIntent=transaction;draft.coldPendingApplication=transaction?.recovery_required===true&&pendingStatuses.includes(status);draft.applicationRecoveryPending=pendingStatuses.includes(status);
          if(status==="committed"&&pending){const appliedBase=pending.base||session.base;if(appliedBase&&ports.currentMatches?.(appliedBase)){draft.applied=true;draft.appliedReceipt=intent.receipt_fingerprint;}draft.pendingApplicationAck=null;}
          if(status==="aborted"&&pending){draft.pendingApplicationAck=null;draft.applied=false;draft.appliedReceipt=null;}
        }
        if(draft.applied&&draft.candidate?.receipt_fingerprint&&draft.candidate.receipt_fingerprint!==draft.appliedReceipt)draft.applied=false;
        return Object.freeze({ok:true,draft});
      }catch(error){return Object.freeze({ok:false,reason:String(error?.message||"domain_sidecar_invalid").slice(0,128)});}
    };
    const commitDomainSidecar=(session,draft)=>{for(const key of ["candidate","appActions","applicationIntent","activation","recovery","canResume","pendingApplicationAck","applicationRecoveryPending","coldPendingApplication","applied","appliedReceipt","error","pollFailureActive"])session[key]=draft[key];};
    return Object.freeze({storeSnapshot,storeModel,readModel,ackCursor,isActive,workbenchState,actionPatch,patchActionList,recordAppAction,publishCandidate,prepareDomainSidecar,commitDomainSidecar});
  }

  global.AgentWorkbenchStateAdapter=Object.freeze({createShell,create});
})(window);
