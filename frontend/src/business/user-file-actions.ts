/** User-only native file effects. No model path tool, retry or document store. */
export interface UserFilePorts {
  board:Record<string,any>;editor:Record<string,any>;workspace:Record<string,any>;
  api:(method:string,...args:any[])=>Promise<any>;
  query:(selector:string)=>any;serialize:(value:any)=>string;status:(message:string)=>void;error:(response:any)=>void;
}
type OpenIntent={current():boolean};
type OpenRoute={opened:boolean;message:string;readOnly?:boolean};
type OpenPorts={board:Record<string,any>;editor:Record<string,any>;report?:Record<string,any>;show:(page:string)=>void|Promise<void>};
/** Route a user-selected file after inspection. The document loaders retain their own dirty guards. */
export async function openInspectedDocument(path:string,file:Record<string,any>,intent:OpenIntent,ports:OpenPorts):Promise<OpenRoute>{
  const isCurrent=()=>intent.current();
  if(!isCurrent())return {opened:false,message:"检查文件期间当前草稿发生变化，没有替换文档。"};
  if(file.kind==="genshin-candidate-input-draft"){
    const opener=window.GenshinCandidateDraftFiles;
    if(!opener)return {opened:false,message:"请在支持原生 AI 的客户端中打开候选输入草稿。"};
    const opened=await opener.openSelected(path,file.file_sha,{isCurrent});
    return {...opened,readOnly:true};
  }
  let page:string,message:string,opened:boolean,openError:string|undefined;
  const readOnly=file.kind==="historical_calculation_snapshot";
  if(readOnly){
    page="result";message="已打开保存的计算报告。";
    opened=await ports.report?.openSavedReport(path,{expectedFileSha:file.file_sha,isCurrent,onError:(message:string)=>{openError=message;}})??false;
  }else if(file.kind==="genshin-whiteboard"){
    page="whiteboard";message="已打开所选白板。";opened=await ports.board.load(path,{openIntent:intent});
  }else{
    page="rotation";message="已打开所选计算方案。";opened=await ports.editor.loadTemplate(path,{openIntent:intent});
  }
  if(!opened)return {opened:false,message:openError??"未打开所选文件，当前文档已保留。"};
  try{await ports.show(page);}
  catch{return {opened:true,readOnly,message:`${message}未能切换页面，请从左侧手动打开对应工作区。`};}
  return {opened:true,readOnly,message};
}

export function createWorkspaceFileActions(ports:UserFilePorts){
  const {board,editor,workspace,api,query,status,error}=ports;
  const cancelled=(options:any,message:string)=>{options.onAgentActionResult?.({status:"cancelled",message});return false;};
  const failed=(options:any,message:string)=>{status(message);options.onAgentActionResult?.({status:"failed",message});return false;};
  const completed=(options:any,message:string)=>{options.onAgentActionResult?.({status:"completed",message});return true;};
  const snapshot=()=>({board:board.state.board,boardJson:board.json(),template:editor.tpl(),path:editor.state.path,
    input:editor.tpl()?ports.serialize(editor.tpl()):null});
  const current=(base:ReturnType<typeof snapshot>)=>base.board===board.state.board&&base.boardJson===board.json()
    &&base.template===editor.tpl()&&base.path===editor.state.path&&base.input===(editor.tpl()?ports.serialize(editor.tpl()):null);
  async function openFile(options:any={}){
    const base=snapshot(),intent=(window as any).AppDocumentOpenIntents?.begin()||{current:()=>current(base)},isCurrent=()=>intent.current(),selected=await board.pickUserFile({action:"open",document_kind:workspace.isBoard()?"whiteboard":"native",suggested_name:null});
    if(!selected||selected.cancelled)return cancelled(options,"已取消打开，当前文档已保留。");
    if(!isCurrent())return failed(options,"选择文件期间当前草稿发生变化，请重新选择；没有替换文档。");
    try{
      const inspected=await api("inspect_plan_file",selected.path);
      if(inspected.code){error(inspected);return failed(options,"所选文件无法打开，当前文档已保留。");}
      if(!isCurrent())return failed(options,"检查文件期间当前草稿发生变化，没有替换文档。");
      const host=window as any;
      const opened=await openInspectedDocument(selected.path,inspected.data,intent,{board,editor,report:host.RS,show:page=>host.GenshinEditorBridge?.show?host.GenshinEditorBridge.show(page):workspace.navigate(page)});
      status(opened.message);
      return opened.opened?completed(options,opened.message):cancelled(options,opened.message);
    }catch(cause){return failed(options,`打开未完成：${cause instanceof Error?cause.message:String(cause)}`);}
    finally{workspace.refresh();}
  }
  async function saveAs(options:any={}){
    if(workspace.isBoard())return board.saveAs(options);
    if(!workspace.isPlan()||!editor.tpl())return failed(options,"当前没有可另存的计算方案。");
    const base=snapshot(),selected=await board.pickUserFile({action:"save",document_kind:"native",suggested_name:null});
    if(!selected||selected.cancelled)return cancelled(options,"已取消另存。");
    if(!current(base))return failed(options,"选择保存位置期间当前草稿发生变化，请重新另存。");
    const input=query("#tpl-path"),original=input.value;input.value=selected.path;let saved=false,written=false;
    try{saved=await editor.saveTemplate({onWritten:()=>{written=true;}});return saved||written?completed(options,saved?"计算方案副本已保存。":"所提交的方案副本已保存；当前文档已变化，请核对当前草稿。"):failed(options,"未完成另存，请查看状态详情。");}
    catch(cause){return failed(options,`另存未完成：${cause instanceof Error?cause.message:String(cause)}`);}
    finally{if(!saved&&!written&&editor.tpl()===base.template)input.value=original;workspace.refresh();}
  }
  return {openFile,saveAs};
}
