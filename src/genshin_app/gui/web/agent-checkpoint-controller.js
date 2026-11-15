(function (global) {
  "use strict";

  const TERMINAL = new Set(["committed", "aborted"]);
  const UNKNOWN = new Set(["recovery_required", "outcome_unknown"]);
  const ok = response => response && response.code === 0 && response.data && typeof response.data === "object";
  const clone = value => JSON.parse(JSON.stringify(value));

  function create(options = {}) {
    const api=options.api,adapter=options.adapter,capture=options.capture,applyTarget=options.applyTarget;
    if(typeof api!=="function"||!adapter||typeof adapter.applyRestoreTarget!=="function"||typeof capture!=="function"||typeof applyTarget!=="function")throw new TypeError("checkpoint_controller_options_invalid");
    let serial=0;
    const state={sessionId:null,kind:null,documentId:null,storeRevision:null,projection:null,busy:false,frozen:false,message:"",intent:null,bindingGeneration:0};
    const emit=()=>options.onState?.(snapshot());
    const identify=prefix=>`${prefix}_${Date.now().toString(36)}_${(++serial).toString(36)}`;
    const snapshot=()=>Object.freeze({...state,projection:state.projection?clone(state.projection):null,intent:state.intent?clone(state.intent):null});
    const operationStamp=(revision=state.storeRevision)=>Object.freeze({sessionId:state.sessionId,kind:state.kind,documentId:state.documentId,bindingGeneration:state.bindingGeneration,storeRevision:revision});
    const bindingCurrent=stamp=>!!stamp&&state.bindingGeneration===stamp.bindingGeneration&&state.sessionId===stamp.sessionId&&state.kind===stamp.kind&&state.documentId===stamp.documentId&&(typeof options.isBindingCurrent!=="function"||options.isBindingCurrent(stamp)!==false);
    const operationCurrent=stamp=>bindingCurrent(stamp)&&state.storeRevision===stamp.storeRevision;
    const nextStamp=(stamp,response)=>{if(!operationCurrent(stamp))return null;const next=response?.store_revision;if(!Number.isSafeInteger(next)||next<0||Number.isSafeInteger(stamp.storeRevision)&&next<stamp.storeRevision)return null;state.storeRevision=next;return operationStamp(next);};
    const setMessage=(message,frozen=false,stamp)=>{if(!operationCurrent(stamp))return false;state.message=String(message||"").slice(0,1000);if(frozen)state.frozen=true;emit();return true;};
    const fail=(response,fallback,stamp)=>{if(!operationCurrent(stamp))return false;options.onError?.(response,fallback);setMessage(fallback,false,stamp);return false;};
    const targetFor=(raw,kind)=>raw&&raw.document&&raw.application_payload?{kind,document:raw.document,applicationPayload:raw.application_payload}:null;
    const targetMatches=(base,target)=>!!base&&!!target&&base.kind===target.kind&&base.key===(target.kind==="whiteboard"?target.document?.board_id:target.document?.plan_id)&&base.json===options.stringify(target.document)&&base.payloadJson===options.stringify(target.applicationPayload);
    const observed=base=>({observed_document:base.document,observed_application_payload:base.applicationPayload});
    const receipt=(intent,key,prefix)=>typeof intent?.[key]==="string"&&intent[key]?intent[key]:identify(prefix);

    async function list(offset=0,append=false,suppliedStamp=null) {
      const stamp=suppliedStamp||operationStamp();if(!stamp.sessionId||!operationCurrent(stamp))return false;
      const response=await api("agent_list_document_checkpoints",stamp.sessionId,offset,50);if(!operationCurrent(stamp))return false;
      if(!ok(response))return fail(response,"无法读取文档恢复点。",stamp);
      const value=response.data;if(value.document_id!==stamp.documentId)return fail({reason:"checkpoint_document_identity_changed"},"恢复点属于另一份文档，已停止显示。",stamp);
      const advanced=nextStamp(stamp,value);if(!advanced)return false;
      if(append&&state.projection){const items=[...state.projection.items,...value.items];state.projection={...value,offset:0,limit:Math.max(value.limit,items.length),items};}else state.projection=value;emit();return value;
    }

    async function bind({sessionId,kind,documentId}) {
      state.bindingGeneration++;state.sessionId=sessionId;state.kind=kind;state.documentId=documentId;state.storeRevision=null;state.projection=null;state.busy=false;state.frozen=false;state.message="";state.intent=null;const stamp=operationStamp();emit();
      if(!sessionId||!documentId)return false;
      const loaded=await list(0,false,stamp);if(!loaded||!bindingCurrent(stamp))return false;return recover();
    }

    async function refresh() { return list(0,false); }
    async function loadMore(offset) { if(state.busy||state.frozen)return false;const stamp=operationStamp();state.busy=true;emit();try{return await list(offset,true,stamp);}finally{if(bindingCurrent(stamp)){state.busy=false;emit();}} }

    async function createCheckpoint(name) {
      if(state.busy||state.frozen)return false;const bindingStamp=operationStamp();let stamp=bindingStamp,base;try{base=capture(stamp.kind);}catch(error){return fail({message:error.message},error.message,stamp);}
      if(!operationCurrent(stamp))return false;state.busy=true;emit();try{const response=await api("agent_create_document_checkpoint",stamp.sessionId,{expected_store_revision:stamp.storeRevision,request_id:identify("checkpoint_create"),name,document:base.document,application_payload:base.applicationPayload});
        if(!operationCurrent(stamp))return false;
        if(!ok(response)){await list(0,false,stamp);if(!bindingCurrent(bindingStamp))return false;return fail(response,"无法建立文档恢复点；已刷新 App 状态。",operationStamp());}
        stamp=nextStamp(stamp,response.data);if(!stamp)return false;const status=response.data.status;const loaded=await list(0,false,stamp);if(!loaded||!bindingCurrent(bindingStamp))return false;setMessage(status==="already_created"?"恢复点已经存在。":"恢复点已建立。",false,operationStamp());return true;
      }finally{if(bindingCurrent(bindingStamp)){state.busy=false;emit();}}
    }

    async function renameCheckpoint(item,name) {
      if(state.busy||state.frozen)return false;const bindingStamp=operationStamp();let stamp=bindingStamp;state.busy=true;emit();try{const response=await api("agent_rename_document_checkpoint",stamp.sessionId,{checkpoint_id:item.checkpoint_id,name,expected_store_revision:stamp.storeRevision});if(!operationCurrent(stamp))return false;if(!ok(response)){await list(0,false,stamp);if(!bindingCurrent(bindingStamp))return false;return fail(response,"恢复点未能重命名；已刷新 App 状态。",operationStamp());}stamp=nextStamp(stamp,response.data);if(!stamp)return false;const loaded=await list(0,false,stamp);if(!loaded||!bindingCurrent(bindingStamp))return false;setMessage("恢复点名称已保存。",false,operationStamp());return true;}finally{if(bindingCurrent(bindingStamp)){state.busy=false;emit();}}
    }

    async function deleteCheckpoint(item) {
      if(state.busy||state.frozen)return false;const bindingStamp=operationStamp();let stamp=bindingStamp;state.busy=true;emit();try{const response=await api("agent_delete_document_checkpoint",stamp.sessionId,{checkpoint_id:item.checkpoint_id,expected_store_revision:stamp.storeRevision});if(!operationCurrent(stamp))return false;if(!ok(response)){await list(0,false,stamp);if(!bindingCurrent(bindingStamp))return false;return fail(response,"恢复点未删除；已刷新 App 状态。",operationStamp());}stamp=nextStamp(stamp,response.data);if(!stamp)return false;const loaded=await list(0,false,stamp);if(!loaded||!bindingCurrent(bindingStamp))return false;setMessage("恢复点已删除。",false,operationStamp());return true;}finally{if(bindingCurrent(bindingStamp)){state.busy=false;emit();}}
    }

    async function previewCheckpoint(item) {
      if(state.busy)return null;const stamp=operationStamp();const response=await api("agent_preview_document_checkpoint",stamp.sessionId,item.checkpoint_id,null);if(!operationCurrent(stamp))return null;if(!ok(response)){fail(response,"无法读取恢复差异。",stamp);return null;}return operationCurrent(stamp)?response.data:null;
    }

    async function abortPrepared(intent,current,stamp) {
      if(!intent||intent.state!=="prepared"||!operationCurrent(stamp))return false;
      const response=await api("agent_abort_document_restore",stamp.sessionId,{intent_id:intent.intent_id,expected_store_revision:stamp.storeRevision,receipt_id:receipt(intent,"abort_receipt_id","restore_abort"),...observed(current)});
      if(!operationCurrent(stamp)||!ok(response))return false;stamp=nextStamp(stamp,response.data);if(!stamp)return false;state.intent=null;
      const loaded=await list(0,false,stamp);if(!loaded||!bindingCurrent(stamp))return false;setMessage("未确认 Renderer 已应用目标；恢复事务已安全取消，可重新尝试。",false,operationStamp());return true;
    }

    async function freezeUnknown(intent,stamp,reason) {
      if(!operationCurrent(stamp))return false;state.intent=intent||state.intent;
      if(intent&&intent.state==="prepared"&&Number.isSafeInteger(stamp.storeRevision)){
        const marked=await api("agent_mark_document_restore_recovery_required",stamp.sessionId,{intent_id:intent.intent_id,expected_store_revision:stamp.storeRevision,reason});
        if(!operationCurrent(stamp))return false;
        if(ok(marked)){stamp=nextStamp(stamp,marked.data);if(!stamp)return false;state.intent=marked.data.intent||state.intent;}
      }
      setMessage("恢复结果尚不能安全判定。已冻结 Agent 写入和恢复点修改；请保留当前文档并进行人工核对。",true,stamp);return false;
    }

    async function finishIntent(intent,target,stamp,{expectedBase=null,allowApply=false,retries=0}={}) {
      if(!operationCurrent(stamp))return false;
      if(!intent||TERMINAL.has(intent.state)){state.intent=null;const loaded=await list(0,false,stamp);return Boolean(loaded&&bindingCurrent(stamp));}
      state.intent=clone(intent);
      if(UNKNOWN.has(intent.state)||intent.outcome_unknown_reason)return freezeUnknown(intent,stamp,"restore_status_outcome_unknown");
      let current;try{current=capture(stamp.kind);}catch(error){return freezeUnknown(intent,stamp,"renderer_capture_failed");}
      if(!operationCurrent(stamp))return false;const matches=targetMatches(current,target);
      if(intent.state==="prepared"){
        if(!matches){
          // A reload cannot prove that Renderer never applied the target.  A
          // prepared intent plus a non-target document may be "not started"
          // or "applied, crashed before ACK, then edited".  Never abort that
          // ambiguous window automatically.
          if(!allowApply||!expectedBase)return freezeUnknown(intent,stamp,"prepared_reload_outcome_unknown");
          let applied;try{applied=await adapter.applyRestoreTarget(target,{expectedBase,apply:async next=>{if(!operationCurrent(stamp))return false;const accepted=await applyTarget(next);return operationCurrent(stamp)&&accepted===true;}});}catch{return operationCurrent(stamp)?freezeUnknown(intent,stamp,"renderer_apply_outcome_unknown"):false;}
          if(!operationCurrent(stamp))return false;
          if(!applied.applied){let after;try{after=capture(stamp.kind);}catch{return freezeUnknown(intent,stamp,"renderer_capture_failed");}if(!operationCurrent(stamp))return false;if(targetMatches(after,target)){current=after;}else if(adapter.currentMatches(expectedBase)){const aborted=await abortPrepared(intent,after,stamp);if(!bindingCurrent(stamp))return false;if(aborted)return false;return operationCurrent(stamp)?freezeUnknown(intent,stamp,"restore_abort_unconfirmed"):false;}else return freezeUnknown(intent,stamp,"renderer_apply_unconfirmed");}
          else current=applied.snapshot;
        }
        if(!operationCurrent(stamp))return false;const ack=await api("agent_mark_document_restore_applied",stamp.sessionId,{intent_id:intent.intent_id,expected_store_revision:stamp.storeRevision,receipt_id:receipt(intent,"renderer_receipt_id","restore_renderer"),...observed(current)});
        if(!operationCurrent(stamp))return false;if(!ok(ack))return recoverAfterUncertain(retries,"Renderer ACK 未确认。",stamp);stamp=nextStamp(stamp,ack.data);if(!stamp)return false;intent=ack.data.intent||intent;state.intent=clone(intent);
      }
      if(intent.state==="renderer_applied"||String(ackStatus(intent)).includes("renderer_applied")){
        try{current=capture(stamp.kind);}catch{return freezeUnknown(intent,stamp,"renderer_capture_failed");}if(!operationCurrent(stamp))return false;if(!targetMatches(current,target))return freezeUnknown(intent,stamp,"renderer_target_changed_after_apply");
        const advanced=await api("agent_advance_document_restore_baseline",stamp.sessionId,{intent_id:intent.intent_id,expected_store_revision:stamp.storeRevision,receipt_id:receipt(intent,"baseline_receipt_id","restore_baseline"),...observed(current)});
        if(!operationCurrent(stamp))return false;if(!ok(advanced))return recoverAfterUncertain(retries,"App 未确认文档恢复进度。",stamp);stamp=nextStamp(stamp,advanced.data);if(!stamp)return false;intent=advanced.data.intent||intent;state.intent=clone(intent);
      }
      if(intent.state==="baseline_advanced"||String(ackStatus(intent)).includes("baseline_advanced")){
        try{current=capture(stamp.kind);}catch{return freezeUnknown(intent,stamp,"renderer_capture_failed");}if(!operationCurrent(stamp))return false;if(!targetMatches(current,target))return freezeUnknown(intent,stamp,"renderer_target_changed_before_commit");
        const committed=await api("agent_commit_document_restore",stamp.sessionId,{intent_id:intent.intent_id,expected_store_revision:stamp.storeRevision,receipt_id:receipt(intent,"commit_receipt_id","restore_commit"),...observed(current)});
        if(!operationCurrent(stamp))return false;if(!ok(committed))return recoverAfterUncertain(retries,"恢复提交结果未确认。",stamp);stamp=nextStamp(stamp,committed.data);if(!stamp)return false;state.intent=null;if(!operationCurrent(stamp))return false;options.onCommitted?.(current,committed.data);if(!operationCurrent(stamp))return false;const loaded=await list(0,false,stamp);if(!loaded||!bindingCurrent(stamp))return false;setMessage("文档已恢复；恢复前内容已自动保存为安全点。",false,operationStamp());return true;
      }
      return recoverAfterUncertain(retries,"恢复状态需要重新核对。",stamp);
    }

    const ackStatus=intent=>intent?.state||"";
    async function recoverAfterUncertain(retries,message,stamp) {
      if(!operationCurrent(stamp))return false;if(retries>=2){setMessage(`${message} 已冻结写入，避免重复执行。`,true,stamp);return false;}
      const response=await api("agent_document_restore_status",stamp.sessionId);if(!operationCurrent(stamp))return false;if(!ok(response)){setMessage(`${message} 无法读取 App 状态，已暂停写入。`,true,stamp);return false;}
      if(response.data.document_id!==stamp.documentId){setMessage(`${message} App 中的文档已经变化，已暂停写入。`,true,stamp);return false;}
      stamp=nextStamp(stamp,response.data);if(!stamp)return false;const intent=response.data.active_intent;if(!intent){state.intent=null;let current=null;try{current=capture(stamp.kind);}catch{}if(!operationCurrent(stamp))return false;if(current)options.onCommitted?.(current,{status:"terminal_recovered"});if(!operationCurrent(stamp))return false;const loaded=await list(0,false,stamp);if(!loaded||!bindingCurrent(stamp))return false;setMessage("App 已确认文档恢复结束。",false,operationStamp());return true;}
      const target=targetFor(response.data.renderer_target,stamp.kind);return finishIntent(intent,target,stamp,{retries:retries+1});
    }

    async function recover() {
      if(!state.sessionId)return false;const bindingStamp=operationStamp();let stamp=bindingStamp;const response=await api("agent_document_restore_status",stamp.sessionId);if(!operationCurrent(stamp))return false;if(!ok(response))return fail(response,"无法核对文档恢复状态。",stamp);if(response.data.document_id!==stamp.documentId)return fail({reason:"checkpoint_document_identity_changed"},"App 返回的是另一份文档的恢复状态，已停止恢复。",stamp);
      stamp=nextStamp(stamp,response.data);if(!stamp)return false;const intent=response.data.active_intent;if(!intent){state.intent=null;state.frozen=false;emit();return true;}
      state.busy=true;emit();try{return await finishIntent(intent,targetFor(response.data.renderer_target,stamp.kind),stamp,{retries:0});}finally{if(bindingCurrent(bindingStamp)){state.busy=false;emit();}}
    }

    async function restoreCheckpoint(item) {
      if(state.busy||state.frozen)return false;const bindingStamp=operationStamp();let stamp=bindingStamp,base;try{base=capture(stamp.kind);}catch(error){return fail({message:error.message},error.message,stamp);}
      if(!operationCurrent(stamp))return false;state.busy=true;emit();try{
        const response=await api("agent_prepare_document_restore",stamp.sessionId,{expected_store_revision:stamp.storeRevision,request_id:identify("restore_request"),checkpoint_id:item.checkpoint_id,...observed(base)});
        if(!operationCurrent(stamp))return false;if(!ok(response)){if(response.operation_may_have_completed!==true&&response.reason!=="document_transport")return fail(response,"恢复未开始；当前文档没有被修改。",stamp);return recoverAfterUncertain(0,"恢复准备结果未确认。",stamp);}stamp=nextStamp(stamp,response.data);if(!stamp)return false;if(response.data.status==="no_op"){setMessage("当前文档已经是这个恢复点，无需修改。",false,stamp);return true;}
        return finishIntent(response.data.intent,targetFor(response.data.renderer_target,stamp.kind),stamp,{expectedBase:base,allowApply:true,retries:0});
      }finally{if(bindingCurrent(bindingStamp)){state.busy=false;emit();}}
    }

    return Object.freeze({bind,refresh,loadMore,createCheckpoint,renameCheckpoint,deleteCheckpoint,previewCheckpoint,restoreCheckpoint,recover,snapshot});
  }

  global.AgentCheckpointController=Object.freeze({create});
})(window);
