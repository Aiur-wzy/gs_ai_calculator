(function (global) {
  "use strict";

  // Sole owner of durable application-projection recovery.  CandidateController
  // owns prepared/baseline-advanced candidate transactions; this controller
  // only restores a committed/cleared application projection and then asks the
  // Host to bind the cold conversation to the exact Renderer snapshot.
  function create(options = {}) {
    const requiredObject=(value,name)=>{if(!value||typeof value!=="object")throw new TypeError(`application_recovery_${name}_missing`);return value;};
    const requiredFunction=(value,name)=>{if(typeof value!=="function")throw new TypeError(`application_recovery_${name}_missing`);return value;};
    const state=requiredObject(options.state,"state"),api=requiredFunction(options.api,"api"),selected=requiredFunction(options.selected,"selected"),active=requiredFunction(options.active,"active"),
      checkpointFrozen=requiredFunction(options.checkpointFrozen,"checkpoint_frozen"),hasUnsettledAppAction=requiredFunction(options.hasUnsettledAppAction,"app_action_guard"),
      hasPendingApplication=requiredFunction(options.hasPendingApplication,"pending_application_guard"),pendingApplicationSession=requiredFunction(options.pendingApplicationSession,"pending_application_owner"),
      capture=requiredFunction(options.capture,"capture"),workspace=requiredFunction(options.workspace,"workspace"),currentMatches=requiredFunction(options.currentMatches,"document_cas"),
      clone=requiredFunction(options.clone,"clone"),stringify=requiredFunction(options.stringify,"stringify"),whiteboard=requiredFunction(options.whiteboard,"whiteboard"),
      currentTab=requiredFunction(options.currentTab,"current_tab"),confirm=requiredFunction(options.confirm,"confirm"),notice=requiredFunction(options.notice,"notice"),
      refresh=requiredFunction(options.refresh,"refresh"),errorText=requiredFunction(options.errorText,"error_text"),receiveCommand=requiredFunction(options.receiveCommand,"receive_command"),
      receiveProtocolText=requiredFunction(options.receiveProtocolText,"protocol_text"),rejectExternal=requiredFunction(options.rejectExternal,"reject_external"),reconcile=requiredFunction(options.reconcile,"reconcile");
    const inflight=new Map();
    let generation=0;

    const keyFor=session=>typeof session?.session_id==="string"&&session.session_id?session.session_id:null;
    const ownsSelected=session=>selected()===session&&state.selected===session.session_id;
    const blockedByOtherApplication=session=>{const owner=pendingApplicationSession();return !!owner&&(owner!==session||hasPendingApplication(session));};
    const admissible=(session,before=null)=>!!keyFor(session)&&!session.live&&!state.busy&&!active()&&!state.closing&&ownsSelected(session)
      &&session.kind==="whiteboard"&&(!before||before.kind==="whiteboard")&&!checkpointFrozen()&&!hasUnsettledAppAction(session)
      &&!hasPendingApplication(session)&&!blockedByOtherApplication(session);
    const deliverySafe=delivery=>!!delivery&&typeof delivery==="object"&&!Object.hasOwn(delivery,"path")&&!Object.hasOwn(delivery,"file_sha")
      &&typeof delivery.delivery_capability==="string"&&/^wdc_v1_[A-Za-z0-9_-]{43}$/.test(delivery.delivery_capability);

    function start(session,mode,operation) {
      const key=keyFor(session);if(!key)return Promise.resolve(false);
      const current=inflight.get(key);
      if(current){
        if(current.mode===mode)return current.promise;
        notice("同一会话已有恢复操作正在核验；未启动第二个恢复写入。",true);return Promise.resolve(false);
      }
      const owner={mode,token:++generation,promise:null};
      owner.promise=(async()=>operation(owner))().finally(()=>{if(inflight.get(key)===owner)inflight.delete(key);});
      inflight.set(key,owner);return owner.promise;
    }

    function operationFence(session,before,owner,request,viewEpoch,sessionEpoch,tab) {
      return inflight.get(session.session_id)===owner&&owner.token<=generation&&!state.closing&&request===state.request
        &&viewEpoch===state.viewEpoch&&sessionEpoch===session.epoch&&tab===currentTab()&&ownsSelected(session)&&!active()
        &&!checkpointFrozen()&&!hasUnsettledAppAction(session)&&!hasPendingApplication(session)&&!pendingApplicationSession()
        &&currentMatches(before);
    }

    async function bindRecovered(session,applied,owner,fence,onAccepted,resumeFailure) {
      if(!fence(applied)){notice("计算结果写入后当前白板状态不稳定；会话尚未绑定。",true);return false;}
      const resumed=await api("agent_resume",session.session_id,applied.document,workspace(applied),applied.applicationPayload);
      if(resumed.code){
        session.live=false;session.canResume=true;
        notice(errorText(resumed,resumeFailure),true);
        await reconcile(session);return false;
      }
      if(!fence(applied)){session.live=false;session.canResume=true;notice("App 确认恢复期间当前白板或页签发生变化；旧会话未绑定到当前内容，请基于当前白板新建任务。",true);return false;}
      const receipt=receiveCommand(session,resumed.data||{},0);
      if(!receipt?.accepted){
        rejectExternal(session,{kind:"protocol",reason:receipt?.reason});
        notice(`${receiveProtocolText(receipt?.reason)}\nApp 可能已恢复会话；系统只会读取状态，不会重复发送恢复命令。`,true);
        await reconcile(session);return false;
      }
      // Commit the recovered binding to the UI immediately.  The outer
      // finally performs the second paint after releasing the busy gate, but
      // controls such as the explicit unsaved-target confirmation must stop
      // being actionable as soon as Host acceptance is admitted.
      onAccepted();refresh();return true;
    }

    function recoverCommitted(session,before) {
      return start(session,"committed",async owner=>{
        if(!admissible(session,before))return false;
        const viewEpoch=state.viewEpoch,sessionEpoch=session.epoch,tab=currentTab(),request=++state.request;
        const fence=base=>operationFence(session,base,owner,request,viewEpoch,sessionEpoch,tab);
        state.busy="resume";refresh();notice("正在从 App 恢复已应用方案的计算结果…");
        try{
          const recovered=await api("agent_recover_candidate_application",session.session_id,before.document,workspace(before),before.applicationPayload);
          if(recovered.code){notice(errorText(recovered,"App 无法恢复这份已应用计算结果。"),true);return false;}
          const data=recovered.data||{},transaction=data.application_intent,intent=transaction?.intent,delivery=data.calculated_delivery;
          if(!fence(before)){notice("恢复计算结果期间当前白板或页签发生变化，未覆盖当前内容。",true);return false;}
          if(transaction?.status!=="committed"||intent?.state!=="committed"||data.status!=="committed"||data.kind!=="whiteboard"||intent.kind!=="whiteboard"||data.revision!==intent.revision||data.receipt_fingerprint!==intent.receipt_fingerprint||data.application_kind!=="whiteboard_calculated"){
            notice("App 返回的已应用计算结果与保存记录不一致，未恢复。",true);return false;
          }
          if(!data.board||data.board.board_id!==before.document.board_id||!deliverySafe(delivery)){
            notice("App 没有返回同一白板的可验证计算结果，未恢复。",true);return false;
          }
          const renderer=whiteboard();
          if(stringify(data.board)!==before.json||typeof renderer?.restoreAgentResultOnly!=="function"||!renderer.restoreAgentResultOnly(clone(delivery),{sessionId:session.session_id,deliveryCapability:delivery.delivery_capability})){
            notice("App 返回的计算结果不能在不替换当前白板的情况下恢复。",true);return false;
          }
          const applied=capture("whiteboard");
          return bindRecovered(session,applied,owner,fence,()=>{
            session.base=applied;session.kind="whiteboard";session.title=applied.title;session.live=true;session.canResume=false;session.error=null;session.applied=true;session.appliedReceipt=intent.receipt_fingerprint;
            notice("已从 App 恢复经过验证的计算结果并重新绑定会话；过程没有向模型发送请求。输入新消息后才会继续推理。");
          },"计算结果已恢复，但 App 尚未确认会话绑定；系统已只读核对 App 状态，请保持白板不变后重试恢复。");
        }catch(error){notice(String(error?.message||"恢复已应用计算结果失败。").slice(0,1000),true);return false;}
        finally{if(inflight.get(session.session_id)===owner){state.busy=null;refresh();}}
      });
    }

    function recoverLastApplied(session,knownBase=null,allowUnsavedTarget=false) {
      const mode=allowUnsavedTarget===true?"restore_unsaved_target":"result_only";
      return start(session,mode,async owner=>{
        if(typeof allowUnsavedTarget!=="boolean"||!admissible(session,knownBase))return false;
        let before=knownBase;
        try{before=before&&currentMatches(before)?before:capture("whiteboard");}catch(error){notice(error.message,true);return false;}
        if(!admissible(session,before))return false;
        if(allowUnsavedTarget&&!confirm("恢复上次已应用但尚未保存的白板？当前白板编辑会被替换，已保存文件本身不会被修改。"))return false;
        // Confirmation is not authority to overwrite a document that changed
        // while the dialog was open.
        if(!admissible(session,before)||!currentMatches(before))return false;
        const viewEpoch=state.viewEpoch,sessionEpoch=session.epoch,tab=currentTab(),request=++state.request;
        const fence=base=>operationFence(session,base,owner,request,viewEpoch,sessionEpoch,tab);
        state.busy="resume";refresh();notice(allowUnsavedTarget?"正在恢复上次已应用但尚未保存的白板…":"正在恢复上次已应用白板的计算结果…");
        try{
          const recovered=await api("agent_recover_last_applied_whiteboard_projection",session.session_id,before.document,workspace(before),before.applicationPayload,allowUnsavedTarget);
          if(recovered.code){notice(errorText(recovered,"App 无法恢复上次已应用的白板内容。"),true);return false;}
          const data=recovered.data||{},projection=data.projection,delivery=data.calculated_delivery,expectedMode=mode;
          if(!fence(before)){notice("恢复期间当前白板或页签发生变化，未覆盖当前内容。",true);return false;}
          if(data.status!=="recovered"||data.kind!=="whiteboard"||data.recovery_mode!==expectedMode||!projection||data.application_kind!==projection.application_kind||data.revision!==projection.revision||data.receipt_fingerprint!==projection.receipt_fingerprint||typeof projection.board_id!=="string"||data.board?.board_id!==projection.board_id||before.document.board_id!==projection.board_id){
            notice("App 返回的已应用白板版本信息不一致，未恢复。",true);return false;
          }
          const calculated=data.application_kind==="whiteboard_calculated";
          if(expectedMode==="result_only"&&!calculated){notice("App 返回的内容不含可恢复的计算结果，未恢复。",true);return false;}
          if(calculated&&(!deliverySafe(delivery)||typeof delivery.calculated!=="object"||delivery.calculated===null)){
            notice("App 没有返回可验证的计算结果，未恢复。",true);return false;
          }
          if(!calculated&&delivery!=null){notice("App 为白板草稿返回了意外计算结果，未恢复。",true);return false;}
          const renderer=whiteboard();
          if(expectedMode==="result_only"){
            if(stringify(data.board)!==before.json||typeof renderer?.restoreAgentResultOnly!=="function"||!renderer.restoreAgentResultOnly(clone(delivery),{sessionId:session.session_id,deliveryCapability:delivery.delivery_capability})){
              notice("计算结果不能在保留当前白板编辑状态的情况下恢复。",true);return false;
            }
          }else{
            if(typeof renderer?.applyAgentDraft!=="function"){notice("当前白板编辑器不支持恢复已应用草稿。",true);return false;}
            renderer.applyAgentDraft(clone(data.board),[],calculated?clone(delivery):null,calculated?{sessionId:session.session_id,deliveryCapability:delivery.delivery_capability}:null);
          }
          const applied=capture("whiteboard");
          return bindRecovered(session,applied,owner,fence,()=>{
            session.base=applied;session.kind="whiteboard";session.title=applied.title;session.live=true;session.canResume=false;session.error=null;session.lastAppliedRestorePending=false;
            notice(expectedMode==="result_only"?"已恢复上次经过验证的计算结果并重新绑定会话；过程没有调用模型。":"已恢复上次已应用但尚未保存的白板并重新绑定会话；当前白板保持未保存状态，过程没有调用模型。");
          },"白板内容已恢复，但 App 尚未确认会话绑定；系统已只读核对 App 状态，请保持白板不变后重试恢复。");
        }catch(error){notice(String(error?.message||"恢复上次已应用白板失败。").slice(0,1000),true);return false;}
        finally{if(inflight.get(session.session_id)===owner){state.busy=null;refresh();}}
      });
    }

    return Object.freeze({recoverCommitted,recoverLastApplied,owns:session=>inflight.has(keyFor(session)),snapshot:()=>Object.freeze([...inflight.entries()].map(([sessionId,value])=>Object.freeze({session_id:sessionId,mode:value.mode,token:value.token})))});
  }

  global.AgentApplicationRecovery=Object.freeze({create});
})(window);
