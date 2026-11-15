import {type WhiteboardPorts} from "./ports";
export function createWhiteboardFiles(ports:WhiteboardPorts){
  const WB=ports.owner,UI=ports.ui,$=ports.query,callApi=ports.api,setStatus=ports.status,services=ports.services;
const beginOpen=()=>{const shared=(window as any).AppDocumentOpenIntents;if(shared)return shared.begin();const token=WB.token();return {current:()=>WB.current(token),acceptSavedIdentity:()=>WB.current(token)};};
const newBoard =async(initial=false)=>{
  const intent=beginOpen();if(!initial&&!await WB.leave({openIntent:intent}))return false;if(!intent.current())return false;const r=await callApi("whiteboard_new");
  if(!intent.current())return false;if(r.code){UI.error(r);return false;}
  WB.replace(r.data,true);if(!initial)UI.tabs("whiteboard");return true;
};
const load =async (path:any,options:any={})=>{
  const intent=options.openIntent||beginOpen();if(!intent.current())return false;
  const inspected=await callApi("inspect_plan_file",path);
  if(!intent.current()){setStatus("检查文件期间当前白板已变化，没有载入或切换文件；请重新操作。");return false;}
  if(inspected?.code){UI.error(inspected);return false;}
  if(inspected?.data?.kind==="genshin-candidate-input-draft"){
    const opener=window.GenshinCandidateDraftFiles;
    if(!opener){setStatus("请在支持原生 AI 的客户端中打开候选输入草稿；正式白板未改变。");return false;}
    const opened=await opener.openSelected(path,inspected.data.file_sha,{isCurrent:()=>intent.current()});setStatus(opened.message);return opened.opened;
  }
  if(!await WB.leave({openIntent:intent}))return false;if(!intent.current())return false;const r=await callApi("whiteboard_load",path);
  if(!intent.current())return false;if(r.code){UI.error(r);return false;}WB.replace(r.data,true);UI.tabs("whiteboard");setStatus("已加载创作白板，动作草稿可继续编辑。");return true;
};
const defaultPath =()=>`${ports.plansDir()||"plans"}/${(WB.state.board.title||"排轴白板").replace(/[<>:"/\\|?*]/g,"_")}.whiteboard.json`;
const fileDialog =(mode:any,options:any={})=>{
  const save=mode==="save",d=WB.dialog(save?"另存创作白板":"打开创作白板",save?"保存可继续编辑的白板文件；不会改变当前动作和面板。":"输入 .whiteboard.json 文件路径。当前未保存内容会先询问处理方式。");
  if(!d){options.onAgentActionResult?.({status:"cancelled",message:"已有窗口尚未完成，未打开新的文件操作界面。"});return null;}
  const path=UI.input(save?(WB.state.path||WB.defaultPath()):"",()=>{});path.id="wb-path";
  services.workspace?.bindAgentFileDialog?.(d,options);
  d.appBody.append(UI.field("白板文件路径",path),UI.button(save?"另存为":"打开",async()=>{
    if(!path.value.trim()){setStatus("请输入白板文件路径。");return;}
    const value=path.value.trim();try{if(save){if(d.beginOperation)d.beginOperation();else d.setBusy?.(true);
      try{let written=false;const clean=await WB.save(value,{onWritten:()=>{written=true;}});if(written||clean){if(d.agentActionComplete)d.agentActionComplete(clean?"App已确认白板副本保存完成。":"所提交的白板副本已保存；当前草稿随后有变化，仍需保存。");else WB.closeDialog(d);}}
      finally{if(d.endOperation)d.endOperation();else d.setBusy?.(false);}}
      else{if(d.releaseForOperation){if(d.releaseForOperation()===false)return;}else WB.closeDialog(d);const loaded=await WB.load(value);if(loaded)d.agentActionComplete?.("App已确认打开所选白板。");else d.agentActionFail?.("App未确认打开所选白板，当前文档保持不变。");}}
    catch(error:any){const message=String(error?.message||`${save?"另存":"打开"}白板时发生错误`);setStatus(message);d.agentActionFail?.(message);}
  }),UI.button("返回白板",()=>d.agentActionCancel?d.agentActionCancel():WB.closeDialog(d)));d.addEventListener("cancel",()=>d.agentActionCancel?d.agentActionCancel():WB.closeDialog(d));return d;
};
const openFile =(options:any={})=>nativeFileAction("open",options);
const saveAs =(options:any={})=>nativeFileAction("save",options);
const save =async (path:any,options:any={})=>{
  if(WB.unappliedEditor()){setStatus("请先保存角色设置或明确放弃角色修改，再保存白板。");return false;}
  path=typeof path==="string"?path:($("#wb-path")?.value.trim()||WB.state.path||WB.defaultPath());
  const chatSnapshot=services.workbench?.beforeDocumentSave?.(),token=WB.token(),copy=UI.clone(WB.state.board);WB.state.activeSave=token;
  const r=await callApi("whiteboard_save",path,copy,path===WB.state.path?WB.state.fileSha:null);
  options.onResponse?.(r);
  if(r.code){UI.error(r);return false;}options.onWritten?.({path:r.data.path,fileSha:r.data.file_sha,current:WB.current(token)});if(!WB.current(token)){
    // A successful write advances the disk guard even when later edits keep the
    // document dirty. Never adopt a superseded save or another board's response.
    if(WB.state.activeSave===token&&token.board===WB.state.board&&token.path===WB.state.path&&token.sha===WB.state.fileSha&&token.json!==WB.json()){
      WB.state.path=r.data.path;WB.state.fileSha=r.data.file_sha;WB.render();}
    setStatus("所提交的白板副本已保存，但当前草稿随后有变化，仍需保存。");return false;}
  WB.state.path=r.data.path;WB.state.fileSha=r.data.file_sha;WB.state.savedJson=token.json;WB.state.pending=r.data.pending||[];if(r.data.revision_policy)WB.state.revisionPolicy=UI.clone(r.data.revision_policy);services.workbench?.documentSaved?.(chatSnapshot);WB.render();setStatus("创作白板已保存。",{path:r.data.path});return true;
};
const validate =async()=>{
  const token=WB.token(),r=await callApi("whiteboard_validate",UI.clone(WB.state.board));if(!WB.current(token))return;
  if(r.code){UI.error(r);return;}WB.state.pending=r.data.pending||[];if(r.data.revision_policy)WB.state.revisionPolicy=UI.clone(r.data.revision_policy);WB.render();setStatus("已检查白板结构；技能规则与可执行性仍需 Agent 核实。");
};
const handoff =()=>{
  if(WB.unappliedEditor()){setStatus("请先保存角色设置，再生成交接文件。");return;}
  const d=WB.dialog("保存交接文件给 Agent","将当前白板、待完善计算方案和中文任务说明保存到一个新目录。Agent 只补计算规则，保持你的动作、顺序和时点；若有冲突，另列建议。这里不会自动启动或发送给 Agent。");if(!d)return null;
  const path=UI.input(`${ports.plansDir()||"plans"}/白板交接-${Date.now()}`,()=>{}),out=UI.el("div");
  d.appBody.append(UI.field("交接输出目录（使用新目录）",path),UI.button("生成交接文件",async()=>{
    const token=WB.token(),r=await callApi("whiteboard_handoff",UI.clone(WB.state.board),path.value.trim());if(d.wbClosed)return;
    out.innerHTML="";if(r.code){out.append(UI.error(r));return;}
    if(!WB.current(token)){out.append(UI.el("p","已保存所提交的交接副本；白板随后有变化，请用新目录重新交接。","wb-warning"));return;}
    const data=r.data;out.append(UI.el("p","交接文件已生成。将下面的任务说明路径交给 Agent："));
    for(const [label,key] of [["任务说明","instructions_path"],["用户白板","board_path"],["待完善计算方案","native_path"]]){
      const field=UI.area(data[key]||"",()=>{});field.readOnly=true;field.className="wb-path";out.append(UI.field(label,field));
      if(key==="instructions_path")out.append(UI.button("复制任务说明路径",async()=>{
        try{if(!ports.clipboard()?.writeText)throw new Error("clipboard unavailable");await ports.clipboard().writeText(field.value);setStatus("任务说明路径已复制。");}
        catch{field.focus?.();field.select?.();setStatus("路径已选中，请按 Ctrl + C 复制。");}}));}
    out.append(UI.el("p","Agent 完成后，在白板中载入完整计算交付 calculated.json。动作、装备或面板不符的回传会被拒绝。","wb-note"));
  }),out,UI.button("返回白板",()=>WB.closeDialog(d)));
  d.addEventListener("cancel",()=>WB.closeDialog(d));
};
const importResultPathDialog =()=>{
  const d=WB.dialog("载入 Agent 计算结果","选择 Agent 输出的完整 calculated.json。App 会校验交付完整性和当前白板的动作、装备与面板。");if(!d)return null;
  const path=UI.input(WB.state.resultPath||"",()=>{}),out=UI.el("div");
  d.appBody.append(UI.field("完整计算交付文件路径",path),UI.button("校验并显示结果",async()=>{
    const token=WB.token(),r=await callApi("whiteboard_read_result",UI.clone(WB.state.board),path.value.trim());if(d.wbClosed||!WB.current(token))return;
    out.innerHTML="";if(r.code){out.append(UI.error(r));return;}
    WB.setAgentResult(r.data.calculated);WB.state.resultProjection=null;WB.state.resultPath=r.data.path;WB.state.staleResultPath=null;WB.state.mode="damage";WB.state.selectedDerived=null;
    WB.state.reusableResult={board:UI.clone(WB.state.board),path:r.data.path};WB.state.equipmentReport=null;
    WB.closeDialog(d);WB.render();setStatus("已载入经校验的计算回传。装备与面板修改可尝试应用重算；引擎无法完成的部分会列出交给 Agent。");
  }),out,UI.button("返回白板",()=>WB.closeDialog(d)));d.addEventListener("cancel",()=>WB.closeDialog(d));
};


  async function pickUserFile(request:any){
    if(WB.state.userFilePickerPending){setStatus("文件选择窗口已经打开，请先完成或取消。");return null;}
    if(services.dialog?.getActive?.()){setStatus("已有窗口尚未处理，请先关闭当前窗口。");return null;}
    WB.state.userFilePickerPending=true;
    try{const response=await callApi("dsh_client_file_dialog",request);
      if(response.code){UI.error(response);throw new Error(response.message||"文件选择失败");}
      const data=response.data;if(!data||typeof data.cancelled!=="boolean"||!data.cancelled&&(typeof data.path!=="string"||!data.path))throw new Error("文件选择响应无效");
      return data;
    }finally{WB.state.userFilePickerPending=false;}
  }
  async function nativeFileAction(mode:"open"|"save",options:any={}){
    const finish=(status:string,message:string)=>{options.onAgentActionResult?.({status,message});return status==="completed";};
    if(mode==="save"&&!WB.state.board)return finish("failed","请先新建或打开白板。");
    if(WB.state.userFilePickerPending)return finish("cancelled","文件选择窗口已经打开。");
    const intent=mode==="open"?beginOpen():null,token=mode==="save"?WB.token():null;
    try{const picked=await pickUserFile({action:mode,document_kind:"whiteboard",suggested_name:mode==="save"?`${WB.state.board?.title||"排轴白板"}.whiteboard.json`:null});
      if(!picked||picked.cancelled)return finish("cancelled","已取消文件操作，当前文档已保留。");
      if(!(intent?intent.current():WB.current(token))){setStatus("选择文件期间当前草稿发生变化，没有覆盖或打开文件；请重新操作。");return finish("cancelled","当前草稿已变化，文件操作未执行。");}
      let written=false;const ok=mode==="save"?await WB.save(picked.path,{onWritten:()=>{written=true;}}):await WB.load(picked.path,{openIntent:intent});
      if(written&&!ok)return finish("completed","所提交的白板副本已保存；当前草稿随后有变化，仍需保存。");
      return finish(ok?"completed":"cancelled",ok?mode==="save"?"App已确认白板副本保存完成。":"App已确认打开所选白板。":"App未确认文件操作完成，当前草稿已保留。");
    }catch(error){const message=`文件操作未完成：${error instanceof Error?error.message:String(error)}`;setStatus(message);return finish("failed",message);}
  }
  async function importResult(path?:string,expectedFileSha?:string){
    if(!WB.state.board){setStatus("请先打开结果对应的白板。");return false;}
    if(WB.state.userFilePickerPending)return false;
    const token=WB.token();
    try{const selected=typeof path==="string"?{path,cancelled:false}:await pickUserFile({action:"open",document_kind:"calculated",suggested_name:null});
      if(!selected||selected.cancelled)return false;
      if(!WB.current(token)){setStatus("选择结果期间当前白板发生变化，没有载入旧结果。");return false;}
      const response=await callApi("whiteboard_read_result",UI.clone(WB.state.board),selected.path,...(expectedFileSha?[expectedFileSha]:[]));
      if(!WB.current(token))return false;if(response.code){UI.error(response);return false;}
      WB.setAgentResult(response.data.calculated);WB.state.resultProjection=null;WB.state.resultPath=response.data.path;WB.state.staleResultPath=null;WB.state.mode="damage";WB.state.selectedDerived=null;
      WB.state.reusableResult={board:UI.clone(WB.state.board),path:response.data.path};WB.state.equipmentReport=null;WB.render();
      setStatus("已载入经校验的计算结果；修改装备或面板后可重新计算。");return true;
    }catch(error){setStatus(`结果未载入：${error instanceof Error?error.message:String(error)}`);return false;}
  }
  let savingAppliedResult=false;
  async function saveAppliedResult(){
    if(savingAppliedResult)return false;
    const applied=WB.state.reusableResult,board=WB.state.board,calculated=WB.state.result;
    const publish=(window as any).AppAppliedResultSave;
    if(!board||!calculated||applied?.owner!=="native_dsh"||!publish)return false;
    const identity=WB.agentPayloadIdentity();
    const isCurrent=()=>WB.agentPayloadMatches({...identity,documentRef:board})&&
      WB.state.reusableResult===applied&&WB.state.result===calculated&&!WB.unappliedEditor();
    if(!isCurrent()){setStatus("当前白板有未应用的修改，请先核对白板和结果。");return false;}
    savingAppliedResult=true;
    try{
      setStatus("请选择完整计算结果的保存位置。");
      const response=await publish(applied.sessionId,board,calculated,isCurrent);
      if(response?.code!==0){setStatus(response?.message||"结果没有保存，请核对当前白板和保存位置。",response?.details);return false;}
      if(response.data?.cancelled){setStatus("已取消计算结果另存为。");return false;}
      if(typeof response.data?.path!=="string"){setStatus("保存状态尚未确认，请先核对所选位置。");return false;}
      if(isCurrent()){WB.state.appliedResultFile={calculated,path:response.data.path};WB.render();}
      setStatus("计算结果已保存。可在白板查看文件位置。");
      return true;
    }catch(error){
      setStatus(`暂时无法确认结果是否已保存，请先核对所选文件。${error instanceof Error?` ${error.message}`:""}`);
      return false;
    }finally{savingAppliedResult=false;}
  }
  return {newBoard,load,defaultPath,fileDialog,openFile,saveAs,save,validate,handoff,importResult,importResultPathDialog,pickUserFile,saveAppliedResult};
}
