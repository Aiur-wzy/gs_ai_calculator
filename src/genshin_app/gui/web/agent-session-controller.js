(function (global) {
  "use strict";

  // Host-owned conversation lifecycle and Renderer-local viewed-session state.
  // The controller deliberately receives every dependency: it never reads
  // window.AC and therefore cannot silently become a second domain authority.
  function create(options = {}) {
    const state=options.state,nodes=options.nodes,eventStore=options.eventStore;
    if(!state||!nodes||!eventStore||typeof options.api!=="function"||typeof options.storeModel!=="function"||typeof options.isActive!=="function"||typeof options.createWorkbenchState!=="function")throw new TypeError("agent_session_controller_options_invalid");
    let historyRequestGeneration=0;
    const call=(name,...args)=>typeof options[name]==="function"?options[name](...args):undefined;
    const selected=()=>state.sessions.get(state.selected)||null;
    const connectionGeneration=()=>Number.isSafeInteger(state.connectionGeneration)?state.connectionGeneration:0;
    const beginHistoryRequest=()=>Object.freeze({requestGeneration:++historyRequestGeneration,connectionGeneration:connectionGeneration()});
    const historyRequestCurrent=stamp=>stamp?.requestGeneration===historyRequestGeneration&&stamp.connectionGeneration===connectionGeneration();
    const invalidateHistoryRequests=()=>{historyRequestGeneration++;};
    const session=id=>{let value=state.sessions.get(id);if(!value){const fallback={state:"ready",active:false,kind:null,workflow:null,title:null,model:null,effort:null,providerId:null,connectionKind:null,canResume:false,historyTruncated:false,unread:false,archived:false,metadataRevision:0,run_id:null};value={session_id:id,epoch:0,appActions:[],candidate:null,live:false,ui_state:{},workbench:options.createWorkbenchState(),_catalog:fallback};
      for(const key of Object.keys(fallback))Object.defineProperty(value,key,{enumerable:true,configurable:false,get:()=>{const model=options.storeModel(value),projected=model?.[key];if(["canResume","historyTruncated"].includes(key))return fallback[key]===true||projected===true;return projected===null||projected===undefined?fallback[key]:projected;},set:next=>{fallback[key]=next;}});
      for(const [key,read] of Object.entries({messages:model=>model?.messages||[],tools:model=>model?.tools||[],cursor:model=>model?.cursor||0,usage:model=>model?.usage||null,budgetStatuses:model=>model?.budgetStatuses||{},projectionRevision:model=>model?.projectionRevision||0}))Object.defineProperty(value,key,{enumerable:false,configurable:false,get:()=>read(options.storeModel(value))});
      state.sessions.set(id,value);}return value;};
    const active=()=>state.sessions.get(state.active)||[...state.sessions.values()].find(options.isActive)||null;
    const historyRow=id=>(state.history||[]).find(row=>(row.session_id||row.id)===id)||null;

    function clearViewedSession() {
      options.messageNodes?.clear?.();options.toolNodes?.clear?.();options.actionNodes?.clear?.();
      const conversationView=call("conversationView");
      if(conversationView)conversationView.activate?.(null);else nodes.messages?.replaceChildren?.();
      nodes.toolsBody?.replaceChildren?.();nodes.appActionsBody?.replaceChildren?.();
      call("historyWindowView")?.clear?.();if(nodes.historyWindowHost)nodes.historyWindowHost.hidden=true;
      call("telemetryView")?.clear?.();if(nodes.telemetryHost)nodes.telemetryHost.hidden=true;if(nodes.trajectoryList)nodes.trajectoryList.hidden=false;
      call("questionController")?.clear?.();call("inboxDock")?.clear?.();state.questionTakeover=false;if(nodes.inboxSection)nodes.inboxSection.hidden=true;
      call("checkpointView")?.clear?.();if(nodes.checkpointSection)nodes.checkpointSection.hidden=true;
    }

    async function selectSession(id) {
      const pending=call("pendingApplicationSession");if(pending&&id!==pending.session_id){if(nodes.history)nodes.history.value=state.selected||"";call("notice","请先回到待处理会话，补记或取消候选应用事务，再切换会话。",true);return false;}
      call("saveReadingAnchor",selected());state.selected=id||null;state.viewEpoch++;clearViewedSession();
      call("conversationView")?.activate?.(id||null);call("switchComposerContext",id);if(nodes.newMessages)nodes.newMessages.hidden=true;nodes.reviewSignature=null;call("renderModelControls");call("refresh");
      if(id){const value=session(id),viewEpoch=state.viewEpoch,current=()=>state.selected===id&&state.viewEpoch===viewEpoch&&state.sessions.get(id)===value;
        await call("poll",value,true);if(!current())return false;
        await call("loadHostOutline",value);if(!current())return false;
        await call("loadWorkbenchFoundation",value,{force:true});if(!current())return false;
        await call("bindCheckpoints",value);if(!current())return false;
        await markSessionRead(id);if(!current())return false;
        call("renderModelControls");call("restoreReadingAnchor",value);
      }return true;
    }

    function newTask() {
      if(state.busy)return false;const pending=call("pendingApplicationSession");if(pending){call("notice","请先补记或取消当前候选应用事务，再新建任务。",true);return false;}
      if(nodes.kind)nodes.kind.value=call("currentKind");if(nodes.workflowExplicit!==undefined)nodes.workflowExplicit=false;void selectSession(null);call("notice","请选择逐步构建白板，或完善排轴与计算。两种方式都按聊天要求修改候选，审核后才应用。");nodes.input?.focus?.();return true;
    }

    async function resumeSession(id=null) {
      const targetId=id||selected()?.session_id;if(!targetId)return false;
      let value=state.sessions.get(targetId);if(!value||value.live||state.busy||active())return false;
      if(state.connection!=="ready"||!call("cliConnected")){call("notice","请先连接创建该会话的Agent提供方，再恢复会话。",true);return false;}
      if(!call("sessionProviderMatches",value)){call("notice","当前连接不是创建该会话的Agent提供方。请切回原提供方后再恢复。",true);return false;}
      if(selected()?.session_id!==targetId&&await selectSession(targetId)!==true)return false;
      value=state.sessions.get(targetId);if(!value||value.live||state.busy||active())return false;
      let base;try{base=call("capture",value.kind);}catch(error){call("notice",error.message,true);return false;}
      value.lastAppliedRestorePending=false;state.busy="resume";const request=++state.request;call("refresh");call("notice","正在核对当前文档与本地会话检查点…");
      const response=await options.api("agent_resume",value.session_id,base.document,call("workspace",base),base.applicationPayload);if(request!==state.request)return false;state.busy=null;
      if(response.code){
        if(response.reason==="native_candidate_application_projection_required")return !!(await call("recoverCommittedApplication",value,base));
        if(response.reason==="native_last_applied_projection_required")return !!(await call("recoverLastAppliedProjection",value,base,false));
        if(response.reason==="native_last_applied_unsaved_target_restore_required"){value.lastAppliedRestorePending=true;call("notice",call("errorText",response,"检测到上次已应用但尚未保存的白板。请确认后再恢复；系统不会自动覆盖当前内容。"),true);call("refresh");return false;}
        if(response.reason==="native_candidate_application_recovery_required")value.applicationRecoveryPending=true;
        call("notice",call("errorText",response,"当前文档不能恢复这份会话。"),true);call("refresh");return false;
      }
      const receipt=call("receiveCommand",value,response.data||{},0);if(!receipt?.accepted){call("transportReject",value,{kind:"protocol",reason:receipt?.reason});call("notice",`${call("receiveProtocolText",receipt?.reason)}\n恢复命令可能已经执行，系统只会读取状态，不会重复发送。`,true);void call("poll",value,true);return false;}
      value.base=base;value.kind=base.kind;value.title=base.title;value.live=true;value.error=null;value.lastAppliedRestorePending=false;
      call("notice","会话已从本地检查点恢复；恢复过程没有向模型发送请求。输入新消息后才会继续推理。");call("refresh");return true;
    }

    async function loadHistory() {
      const stamp=beginHistoryRequest(),response=await options.api("agent_sessions");if(!historyRequestCurrent(stamp))return false;if(response.code){call("notice",call("errorText",response,"无法读取会话记录。"),true);return false;}
      if(!historyRequestCurrent(stamp))return false;
      state.workbenchCapabilities=call("workbenchCapabilities",response.data.workbench_capabilities);state.history=response.data.sessions||[];
      for(const row of state.history){const id=row.session_id||row.id;if(!id)continue;const value=session(id);
        for(const key of ["title","kind","workflow","updated_at","model","effort"])if(row[key]!=null)value[key]=row[key];value.unread=row.unread===true;value.archived=row.archived===true;value.metadataRevision=Number.isSafeInteger(row.metadata_revision)?row.metadata_revision:0;
        if(row.pending_application===true){value.coldPendingApplication=true;value.applicationRecoveryPending=true;value.applicationIntent={conversation_id:id,status:row.application_intent_status||"prepared",intent:null,recovery_required:true};}
        else if(!value.live&&!value.pendingApplicationAck){value.coldPendingApplication=false;value.applicationRecoveryPending=false;if(value.applicationIntent?.recovery_required===true)value.applicationIntent=null;}
        value.providerId=row.provider_id||value.providerId;value.connectionKind=row.connection_kind||value.connectionKind;value.canResume=row.can_resume===true;if(row.activation){value.activation=call("clone",row.activation);if(Object.hasOwn(row.activation,"recovery"))value.recovery=row.activation.recovery?call("clone",row.activation.recovery):null;}if(Object.hasOwn(row,"recovery"))value.recovery=row.recovery&&typeof row.recovery==="object"?call("clone",row.recovery):null;
        if(typeof row.active==="boolean"&&row.active){value.active=true;value.state=row.state||"running";state.active=id;call("schedule",value);}else if(!value.live&&row.state)value.state=row.state;
      }
      const history=nodes.history;if(history){history.replaceChildren();history.append(Object.assign(call("uiElement","option","选择本机会话记录"),{value:""}));for(const row of state.history){const option=call("uiElement","option",`${row.title||"计算补正"} · ${call("label",row.state)}${row.active?" · 运行中":""}`);option.value=row.session_id||row.id;history.append(option);}history.value=state.selected||"";}
      call("renderModelControls");call("refresh");call("renderSessionNavigation");const current=selected();if(current){void call("loadHostOutline",current);void call("loadWorkbenchFoundation",current,{force:true});void call("bindCheckpoints",current);}return true;
    }

    async function renameSession(id,title,metadataRevision) {
      if(!state.workbenchCapabilities.session_lifecycle){call("notice","当前 App 尚未开放会话重命名。",true);return false;}
      const response=await options.api("agent_rename_session",id,{title,expected_revision:metadataRevision,mutation_id:call("mutationId","session-rename")});
      if(response.code){call("notice",call("errorText",response,"会话未重命名；App 中的记录没有改变。"),true);await loadHistory();return false;}await loadHistory();call("notice","会话名称已保存到本机。");return true;
    }

    async function archiveSession(id,archived,metadataRevision) {
      if(!state.workbenchCapabilities.session_lifecycle){call("notice","当前 App 尚未开放会话归档。",true);return false;}
      const response=await options.api("agent_archive_session",id,{archived,expected_revision:metadataRevision,mutation_id:call("mutationId",archived?"session-archive":"session-unarchive")});
      if(response.code){call("notice",call("errorText",response,archived?"会话仍有未完成事项，未归档。":"会话未能取消归档。"),true);await loadHistory();return false;}await loadHistory();call("notice",archived?"会话已归档。":"会话已取消归档。");return true;
    }

    async function markSessionRead(id) {
      if(!id||!state.workbenchCapabilities.session_lifecycle||selected()?.session_id!==id)return false;const value=state.sessions.get(id);if(value?.markReadPending)return value.markReadPending;
      const viewEpoch=state.viewEpoch,stillViewed=()=>state.selected===id&&state.viewEpoch===viewEpoch&&state.sessions.get(id)===value;
      const mutationId=call("mutationId","session-mark-read"),run=(async()=>{for(let attempt=0;attempt<2;attempt++){if(!stillViewed())return false;const row=historyRow(id);if(!row||row.unread!==true)return true;const revision=Number.isSafeInteger(row.metadata_revision)?row.metadata_revision:0;const response=await options.api("agent_mark_session_read",id,{expected_revision:revision,mutation_id:mutationId});if(!stillViewed())return false;if(!response.code){const loaded=await loadHistory();return loaded===true&&stillViewed();}const loaded=await loadHistory();if(loaded!==true||!stillViewed())return false;if(response.reason==="native_session_metadata_stale")continue;call("notice",call("errorText",response,"App 未确认已读状态；未读标记保持不变。"),true);return false;}return false;})();
      if(value)value.markReadPending=run;try{return await run;}finally{if(value?.markReadPending===run)value.markReadPending=null;}
    }

    async function syncSelectedRead(id) {if(!id||selected()?.session_id!==id)return false;const loaded=await loadHistory();if(loaded!==true||selected()?.session_id!==id)return false;return markSessionRead(id);}

    function activateAcceptedSession({session: value, message} = {}) {
      if(!value?.session_id)throw new TypeError("agent_session_activation_invalid");
      const composer=call("composer");composer?.clearIf?.(message);
      state.selected=value.session_id;call("switchComposerContext",value.session_id);state.viewEpoch++;
      options.messageNodes?.clear?.();options.toolNodes?.clear?.();options.actionNodes?.clear?.();
      call("conversationView")?.activate?.(value.session_id);nodes.toolsBody?.replaceChildren?.();nodes.appActionsBody?.replaceChildren?.();
      return true;
    }

    function finishAcceptedSession({message} = {}) {
      const composer=call("composer");
      if(nodes.input?.value?.trim?.()===message){nodes.input.value="";composer?.save?.();composer?.autoGrow?.();}
      if(nodes.settings)nodes.settings.open=false;if(nodes.taskSettings)nodes.taskSettings.open=false;
      call("shellSetSection","settings",false,false);call("shellSetSection","details",false,true);
      return true;
    }

    async function forgetSession(id,metadataRevision=null) {
      if(!id||state.busy)return false;
      let row=historyRow(id);
      if(Number.isSafeInteger(metadataRevision)&&row&&Number.isSafeInteger(row.metadata_revision)&&row.metadata_revision!==metadataRevision){
        if(await loadHistory()!==true)return false;row=historyRow(id);
      }
      const value=state.sessions.get(id);if(!value)return false;
      const hostAllows=row?.can_forget!==false;
      const transactionBlocked=row?.pending_application===true||row?.pending_action===true||value.applicationRecoveryPending===true||value.pendingApplicationAck===true;
      if(!hostAllows||options.isActive(value)||transactionBlocked){call("notice",transactionBlocked?"请先处理这份会话中待完成的方案应用，再删除记录。":"这份会话正在运行或被 App 锁定，当前不能删除。",true);return false;}
      const title=String(row?.title||value.title||"未命名任务").slice(0,256);
      const prompt=`删除本机会话“${title}”？\n\n将删除消息、运行记录、未采用候选和会话临时交付；不会删除已保存的白板、导出文件或已晋升交付。文档恢复点仍按文档保留，来源会话会显示为已删除。`;
      if(!call("confirm",prompt))return false;
      const busyToken=`forget:${id}`;state.busy=busyToken;call("refresh");const response=await options.api("agent_forget",id);if(state.busy===busyToken)state.busy=null;
      if(response.code){call("notice",call("errorText",response,"记录未删除；App 中的记录没有改变。"),true);call("refresh");return false;}
      invalidateHistoryRequests();call("transportInvalidate",value,{drop:true});eventStore.drop(id);state.sessions.delete(id);call("dropComposerContext",id);if(state.selected===id)await selectSession(null);await loadHistory();call("notice",`会话“${title}”已删除。`);return true;
    }

    async function forget() {const value=selected();return value?forgetSession(value.session_id,historyRow(value.session_id)?.metadata_revision):false;}

    return Object.freeze({selected,session,active,historyRow,selectSession,newTask,resumeSession,loadHistory,renameSession,archiveSession,markSessionRead,syncSelectedRead,activateAcceptedSession,finishAcceptedSession,forgetSession,forget});
  }

  global.AgentSessionController=Object.freeze({create});
})(window);
