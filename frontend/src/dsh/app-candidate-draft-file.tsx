import {useEffect,useRef,useState,useSyncExternalStore} from "react";
import {Button,Modal} from "@deepseek-ai/dsh-client-ui-primitives";
import {childPointer,parseInputPage,type AppCandidateGuiApi,type CandidatePage} from "./app-domain-candidate";
import {assertAppDomainSuccess} from "./app-domain-errors";
import {selectedDraftNotice} from "./app-candidate-draft-selection";
import {copyAppText} from "./app-clipboard";

/** User file UI, not a model filesystem or a second conversation owner. */
export function OpenCandidateDraft({api,sessionId,onImported,compact=false}:{api:AppCandidateGuiApi;sessionId:string;onImported?:()=>void;compact?:boolean}){
  type Resource="source_document"|"candidate"|"evidence";
  const [busy,setBusy]=useState(false),[message,setMessage]=useState(""),[legacy,setLegacy]=useState<{prompt:string;previewId:string}>(),[detail,setDetail]=useState<{resource:Resource;page:CandidatePage;history:number[]}>(),[reading,setReading]=useState(false);
  const readGeneration=useRef(0);
  const currentLegacy=useRef(legacy);currentLegacy.current=legacy;
  const [copyStatus,setCopyStatus]=useState<{previewId:string;copied:boolean}>();
  useEffect(()=>setCopyStatus(undefined),[legacy]);
  const [chooseOpen,setChooseOpen]=useState(false);
  const openGeneration=useRef(0);
  useEffect(()=>{setBusy(false);setMessage("");setLegacy(undefined);setDetail(undefined);setReading(false);return()=>{openGeneration.current++;readGeneration.current++;};},[sessionId]);
  const selected=useSyncExternalStore(selectedDraftNotice.subscribe,()=>selectedDraftNotice.snapshot(sessionId));
  useEffect(()=>{
    if(!selected)return;
    const data=selected.data,preview=data.preview as {compatible?:boolean;message?:string};
    setMessage(preview?.message||"输入草稿已打开，尚未开始 AI 任务。");
    readGeneration.current++;setDetail(undefined);setReading(false);
    setLegacy(preview?.compatible===false?{prompt:String(data.migration_prompt||"请按当前版本重新整理输入。"),previewId:String(data.preview_id)}:undefined);
  },[selected]);
  if(!api.dsh_client_open_candidate_input_draft)return null;
  const open=async(newCopy=false)=>{
    if(busy)return;setChooseOpen(false);setBusy(true);setMessage("");
    const generation=++openGeneration.current,intent=(window as unknown as {AppDocumentOpenIntents?:{begin():{current():boolean}}}).AppDocumentOpenIntents?.begin(),
      isCurrent=()=>generation===openGeneration.current&&(!intent||intent.current());
    try{
      if(window.GenshinCandidateDraftFiles&&api.dsh_client_file_dialog){
        const selected=await api.dsh_client_file_dialog({action:"open",document_kind:"candidate_input_draft",suggested_name:null});
        assertAppDomainSuccess(selected);
        const file=(selected as {data:{cancelled:boolean;path:string;file_sha:string}}).data;
        if(file.cancelled)return;
        if(!isCurrent()){if(generation===openGeneration.current)setMessage("选择文件期间当前输入已变化，没有打开旧文件；请重新操作。");return;}
        const result=await window.GenshinCandidateDraftFiles.openSelected(file.path,file.file_sha,{newCopy,targetSessionId:sessionId,isCurrent});
        if(generation===openGeneration.current){setMessage(result.message);if(result.opened)onImported?.();}return;
      }
      if(newCopy){setMessage("当前界面暂不支持新开独立副本，请使用完整客户端。");return;}
      const response=await api.dsh_client_open_candidate_input_draft!({dsh_session_id:sessionId});
      assertAppDomainSuccess(response);
      const data=(response as {data:Record<string,unknown>}).data;
      if(data.cancelled)return;
      if(!isCurrent()){if(generation===openGeneration.current)setMessage("文件请求已返回，但当前输入随后变化；未自动切换，请从候选列表核对。");return;}
      const preview=data.preview as {compatible?:boolean;message?:string};
      setMessage(preview?.message||"输入草稿已打开，尚未开始 AI 任务。");
      readGeneration.current++;setDetail(undefined);setReading(false);
      setLegacy(preview?.compatible===false?{prompt:String(data.migration_prompt||"请根据当前 App、数据库和拉表说明重新整理我提供的旧排轴文件。"),previewId:String(data.preview_id)}:undefined);
      if(preview?.compatible!==false)onImported?.();
    }catch(reason){if(generation===openGeneration.current)setMessage(reason instanceof Error?reason.message:"未能打开输入草稿，当前白板已保留。");}
    finally{if(generation===openGeneration.current)setBusy(false);}
  };
  const read=async(resource:Resource,pointer="",offset=0,history:number[]=[])=>{
    if(!legacy||!api.dsh_client_candidate_input_draft_preview_read)return;
    const generation=++readGeneration.current;setReading(true);
    try{const response=await api.dsh_client_candidate_input_draft_preview_read({preview_id:legacy.previewId,resource,pointer,offset,limit:20});assertAppDomainSuccess(response);const page=parseInputPage(response,resource,pointer,offset);if(generation===readGeneration.current)setDetail({resource,page,history});}
    catch{if(generation===readGeneration.current)setMessage("暂时无法展开旧草稿资料；源文件没有改动。");}
    finally{if(generation===readGeneration.current)setReading(false);}
  };
  const choices=<><Button size="sm" variant="outline" disabled={busy} onClick={()=>void open()}>{busy?"正在打开…":"打开候选输入草稿"}</Button><Button size="sm" variant="ghost" disabled={busy} onClick={()=>void open(true)}>新开独立副本</Button></>;
  return <span className="gs-draft-file-actions">{compact?<Button size="sm" variant="ghost" disabled={busy} onClick={()=>setChooseOpen(true)}>导入草稿</Button>:choices}{message&&<small role="status">{message}</small>}
    <Modal open={chooseOpen} title="导入候选草稿" description="打开保存的候选继续完善，或建立独立副本。正式白板不会因此被覆盖。" closeLabel="关闭导入草稿" onClose={()=>setChooseOpen(false)} className="gs-domain-dialog" contentClassName="gs-domain-dialog-content"><div className="gs-draft-file-choices">{choices}</div></Modal>
    <Modal open={!!legacy} title="旧输入草稿仅供查看" description="本文件没有获得当前版本的计算或应用资格。可复制以下要求，让 Agent 在新任务重新整理。" closeLabel="关闭旧草稿说明" onClose={()=>{readGeneration.current++;setReading(false);setLegacy(undefined);setDetail(undefined);}}
      className="gs-domain-dialog" contentClassName="gs-domain-dialog-content">
      <p style={{userSelect:"text"}}>{legacy?.prompt}</p><Button variant="outline" onClick={()=>{
        if(legacy)void copyAppText(legacy.prompt).then(copied=>{if(currentLegacy.current===legacy)setCopyStatus({previewId:legacy.previewId,copied});});
      }}>复制兼容修改要求</Button>
      {copyStatus&&copyStatus.previewId===legacy?.previewId&&<p role={copyStatus.copied?"status":"alert"}>{copyStatus.copied?"已复制兼容修改要求。":"剪贴板暂不可用，请选中上面的提示手动复制。"}</p>}
      <p><Button size="sm" disabled={reading} onClick={()=>void read("source_document")}>查看原始意图</Button><Button size="sm" disabled={reading} onClick={()=>void read("candidate")}>查看候选输入</Button><Button size="sm" disabled={reading} onClick={()=>void read("evidence")}>查看来源资料</Button></p>
      {reading&&<p role="status">正在读取…</p>}
      {detail&&<section aria-label="旧草稿输入内容" style={{maxHeight:"40vh",overflow:"auto",userSelect:"text"}}>
        <nav aria-label="资料位置"><Button size="sm" disabled={reading||!detail.page.pointer} onClick={()=>void read(detail.resource)}>全部内容</Button>{detail.page.pointer.split("/").slice(1).map((part,index,parts)=><Button size="sm" disabled={reading||index===parts.length-1} key={index} onClick={()=>void read(detail.resource,"/"+parts.slice(0,index+1).join("/"))}>{part.replaceAll("~1","/").replaceAll("~0","~")}</Button>)}</nav>
        {detail.page.kind==="object"||detail.page.kind==="array"?<dl>{detail.page.items.map(item=><div key={item.key}><dt>{String(item.key)}</dt><dd>{item.value!==null&&typeof item.value==="object"?<Button size="sm" disabled={reading} onClick={()=>void read(detail.resource,childPointer(detail.page.pointer,item.key))}>查看内容</Button>:<span>{String(item.value)}</span>}</dd></div>)}</dl>:<pre style={{whiteSpace:"pre-wrap"}}>{detail.page.kind==="text"?detail.page.text:JSON.stringify(detail.page.value,null,2)}</pre>}
        <footer><span>{detail.page.total!==undefined?`共 ${detail.page.total} 项`:""}</span>{detail.history.length>0&&<Button size="sm" disabled={reading} onClick={()=>void read(detail.resource,detail.page.pointer,detail.history.at(-1)!,detail.history.slice(0,-1))}>上一页</Button>}{detail.page.nextOffset!==null&&<Button size="sm" disabled={reading} onClick={()=>void read(detail.resource,detail.page.pointer,detail.page.nextOffset!,[...detail.history,detail.page.offset])}>下一页</Button>}</footer>
      </section>}
    </Modal>
  </span>;
}
