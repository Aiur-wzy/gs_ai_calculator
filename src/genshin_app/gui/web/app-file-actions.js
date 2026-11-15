// Pure App file/navigation consumer. No Session, message or permission reducer.
// A trusted user click claims the Host card before this port is invoked.
window.AppEditorFileActions=Object.freeze({create({root,show}){
  const adapter=AgentDocumentAdapter.create({transport:JSONTransport,whiteboard:()=>window.WB,plan:()=>window.PL,rotation:()=>window.RT,result:()=>window.RS,workspace:()=>window.WS});
  const kind=()=>WS.isPlan?.()&&PL.state.plan?"native":WS.isBoard?.()&&WB.state.board?"whiteboard":WB.state.board?"whiteboard":PL.state.plan?"native":"whiteboard";
  const exists=value=>value==="whiteboard"?!!WB.state.board:!!PL.state.plan;
  const capture=()=>{
    const selected=kind(),base=exists(selected)?adapter.capture(selected):null,workspace=UI.clone(adapter.workspace(base,selected));
    workspace.display_name=workspace.display_name.replace(/[\\/<>:"|?*\u0000-\u001f]/g,"_").trim()||"未命名方案";
    return {currentDocumentId:base?.key||null,workspace,currentPayload:workspace.can_export?base?.applicationPayload||null:null,kind:selected,base,empty:{board:WB.state.board,plan:PL.state.plan,boardEpoch:WB.state.request,planEpoch:PL.state.sourceEpoch}};
  };
  const matches=snapshot=>snapshot.base?adapter.currentMatches(snapshot.base):!WB.state.board&&!PL.state.plan&&snapshot.empty.boardEpoch===WB.state.request&&snapshot.empty.planEpoch===PL.state.sourceEpoch;
  const outcome=(status,message,guard=false)=>({status,message,dirty_open_guard_passed:guard});
  const confirmedNoWrite=response=>{
    if(!response||response.code===0)return false;
    const details=response.details;
    // A request-classified error can still follow a completed replace or a
    // lost response. Only the publisher's explicit side-effect account or a
    // known pre-publication validation error proves that nothing was written.
    if(details&&(Array.isArray(details.published_paths)||Array.isArray(details.unknown_paths))){
      return Array.isArray(details.published_paths)&&details.published_paths.length===0&&
        Array.isArray(details.unknown_paths)&&details.unknown_paths.length===0;
    }
    return details?.publication_state==="not_written"||["invalid_plan","invalid_whiteboard","candidate_input_draft_filename",
      "candidate_input_draft_target_invalid","dsh_domain_session_unbound"].includes(response.reason);
  };
  const unconfirmed=response=>{throw new Error(response?.details?.publication_state==="written_unverified"?"app_action_written_unverified":response?.reason||"app_action_execution_outcome_unknown");};
  const pick=async(action,documentKind,suggested)=>{
    const response=await callApi("dsh_client_file_dialog",{action,document_kind:documentKind,suggested_name:suggested||null});
    if(response.code)throw new Error("用户文件选择器没有完成，请重新查看 App 状态。");
    if(response.data?.cancelled)return null;
    if(typeof response.data?.path!=="string"||!response.data.path)throw new Error("文件选择器未返回有效路径。");
    return response.data.path;
  };
  return Object.freeze({capture,matches,async execute(request,snapshot,conversationId,onPhase){
    if(!matches(snapshot))return outcome("failed","当前文档已变化，未执行原操作。");
    const action=request.action,selected=snapshot.kind;
    const openIntent=action==="open_document"?AppDocumentOpenIntents.begin():null;
    if(action==="export_candidate_input_draft"){
      const target=request.target;
      if(target?.kind!=="candidate_input_draft"||typeof target.workspace_id!=="string"||typeof request.ui_receipt!=="string"||typeof conversationId!=="string"||!conversationId)return outcome("failed","候选草稿保存目标不一致，未执行保存。");
      onPhase?.("choosing");
      const picked=await callApi("dsh_client_file_dialog",{action:"save",document_kind:"candidate_input_draft",suggested_name:request.suggested_name||"候选输入草稿.gencdraft.json"});
      if(picked.code)return outcome("failed",picked.message||"无法选择草稿保存位置，未写入文件。");
      if(picked.data?.cancelled)return outcome("cancelled","已取消保存，草稿仍保留在候选区。");
      if(!matches(snapshot))return outcome("failed","选择保存位置期间正式文档已变化，未写入文件。请回到原会话重新保存草稿。");
      if(typeof picked.data?.path!=="string"||!picked.data.path.toLowerCase().endsWith(".gencdraft.json"))return outcome("failed","请选择以 .gencdraft.json 结尾的草稿文件名，未写入文件。");
      onPhase?.("writing");
      const saved=await callApi("dsh_client_publish_candidate_input_draft",{conversation_id:conversationId,
        action_id:request.action_id,ui_receipt:request.ui_receipt,output_path:picked.data.path,
        expected_file_sha:picked.data.file_sha??null,overwrite:picked.data.file_sha!=null});
      if(saved.code||!saved.data?.saved){if(confirmedNoWrite(saved))return outcome("failed",saved.message||"草稿未保存，请按文件冲突或输入提示修正。");return unconfirmed(saved);}
      return outcome("completed","候选输入草稿已保存；此文件只保留输入，不包含计算接纳或应用凭据。");
    }
    if(action.startsWith("navigate_")){
      const page={navigate_rotation:"rotation",navigate_result:"result",navigate_whiteboard:"whiteboard"}[action];
      if(!page)return outcome("failed","当前不支持这项页面操作。");
      try{await show(page);return outcome("completed","已打开对应工作区。");}
      catch{return outcome("failed","未能切换页面，请从左侧手动打开对应工作区。");}
    }
    if(action==="open_document"){
      let path;
      try{path=await pick("open",selected,request.suggested_name);}catch(error){return outcome("failed",error.message);}
      if(!path)return outcome("cancelled","用户取消了打开文件。");
      if(!matches(snapshot)||!openIntent.current())return outcome("failed","选择文件期间文档已变化，未打开新文件。");
      const inspected=await callApi("inspect_plan_file",path);if(inspected.code)return outcome("failed","所选文件未通过检查，当前文档已保留。");
      if(!matches(snapshot)||!openIntent.current())return outcome("failed","检查文件期间文档已变化，未打开新文件。");
      const opened=await window.GenshinBusinessUI.openInspectedDocument(path,inspected.data,openIntent,{board:WB,editor:RT,report:RS,show});
      return opened.opened?{...outcome("completed",opened.message,!opened.readOnly),...(opened.readOnly?{read_only_open:true}:{})}:outcome("cancelled",opened.message);
    }
    if(action==="save_current"||action==="save_as"){
      if(!snapshot.currentDocumentId)return outcome("failed","当前没有可保存的正式文档，请先审核并同步候选。");
      let path=action==="save_current"&&snapshot.workspace.has_path?null:await pick("save",selected,request.suggested_name);
      if(path===null&&!(action==="save_current"&&snapshot.workspace.has_path))return outcome("cancelled","用户取消了另存为。");
      if(!matches(snapshot))return outcome("failed","选择保存位置期间文档已变化，未保存原文档。");
      if(selected==="whiteboard"){let written=false,response=null;const clean=await WB.save(path||undefined,{onWritten:()=>{written=true;},onResponse:value=>{response=value;}});if(written||clean)return outcome("completed",clean?"App已确认白板保存完成。":"所提交的白板副本已保存；当前草稿随后有变化，仍需保存。");return response&&!confirmedNoWrite(response)?unconfirmed():outcome("failed","白板未保存，请查看文件冲突或输入提示。");}
      const input=document.getElementById("tpl-path"),previousPath=input.value;if(path)input.value=path;
      let saved=false,written=false,response=null;try{saved=await RT.saveTemplate({onWritten:()=>{written=true;},onResponse:value=>{response=value;}});if(saved||written)return outcome("completed",saved?"App已确认方案保存完成。":"所提交的方案副本已保存；当前文档已变化，请核对当前草稿。");return response&&!confirmedNoWrite(response)?unconfirmed():outcome("failed","方案未保存，请查看文件冲突或输入提示。");}
      finally{if(!saved&&!written&&PL.state.plan===snapshot.base?.documentRef)input.value=previousPath;}
    }
    if(action==="export_result"){
      const source=selected==="whiteboard"?"whiteboard":"editor";
      RS.nextReportOrigin={kind:source};
      try{await show("result");}finally{RS.nextReportOrigin=null;}
      if(!matches(snapshot))return outcome("failed","当前文档已变化，未导出原结果。");
      if(RS.sourceKind()!==source){RS.reportOrigin={kind:source};RS.renderAll();}
      let deliveryStatus;
      const exported=await RS.exportResult("json",status=>{deliveryStatus=status;});
      return exported?outcome("completed","计算结果已导出到你选择的位置。"):
        deliveryStatus==="cancelled"?outcome("cancelled","你已取消结果导出。"):
        deliveryStatus==="unknown"?unconfirmed():outcome("failed","App 未确认导出完成，请检查当前报告和文件。 ");
    }
    return outcome("failed","当前不支持这项 App 操作。");
  }});
}});
