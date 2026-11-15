import {useEffect,useMemo,useRef,useState} from "react";
import {Button,Modal} from "@deepseek-ai/dsh-client-ui-primitives";
import type {AppCandidateGuiApi,AppDomainBinding,CandidateSummary} from "./app-domain-candidate";
import type {AppEditorBridge} from "./app-editor-bridge";
import {applyReviewedCandidate,hasCandidateCommand,recoverCandidate,reviewCandidate,type ReviewedCandidate} from "./app-candidate-commands";
import {CandidatePreview} from "./app-candidate-preview";
import {AppDomainHostError} from "./app-domain-errors";

type Row=Record<string,unknown>;
const row=(value:unknown):Row=>value&&typeof value==="object"&&!Array.isArray(value)?value as Row:{};
const named=(value:unknown)=>{const item=row(value);return String(item.name??item.label??item.title??item.action_id??item.event_id??item.id??"");};
function reviewRows(review:ReviewedCandidate,target:Row){
  const before=review.base?.document??review.creation?.seed??{};
  const fields=review.summary.kind==="whiteboard"
    ? [["角色与配装",before.team,target.team],["动作轴",before.actions,target.actions],["轴长",before.duration,target.duration],["标题",before.title,target.title],["备注",before.notes,target.notes]]
    : [["角色面板",row(before.manual_template).characters,row(target.manual_template).characters],["动作轴",row(before.rotation_model).actions,row(target.rotation_model).actions],["事件与机制",before.mechanics_model,target.mechanics_model],["扩展输入",before.extensions,target.extensions]];
  return fields.flatMap(([label,from,to])=>{
    if(JSON.stringify(from)===JSON.stringify(to))return [];
    if(Array.isArray(from)||Array.isArray(to)){
      const old=Array.isArray(from)?from:[],next=Array.isArray(to)?to:[];
      const key=(value:unknown,index:number)=>String(row(value).id??row(value).action_id??row(value).event_id??index);
      const a=new Map(old.map((value,index)=>[key(value,index),value])),b=new Map(next.map((value,index)=>[key(value,index),value]));
      const changed=[...new Set([...a.keys(),...b.keys()])].filter(id=>JSON.stringify(a.get(id))!==JSON.stringify(b.get(id)));
      return [{label:String(label),change:`${old.length} → ${next.length}`,examples:changed.slice(0,6).map(id=>[a.has(id)?b.has(id)?"修改":"移除":"新增",named(b.get(id)??a.get(id))||id].join(" "))}];
    }
    const change=[from,to].every(value=>value==null||["string","number","boolean"].includes(typeof value))?`${String(from??"空")} → ${String(to??"空")}`:"已修改";
    return [{label:String(label),change,examples:[]}];
  });
}

export function CandidateReviewControls({api,binding,summary,currentRead=true,calculated,editor,onSettled,onOpenCandidate,onDismiss,autoOpen=false}:{api:AppCandidateGuiApi;binding:AppDomainBinding;summary:CandidateSummary;currentRead?:boolean;calculated?:Record<string,unknown>;editor?:AppEditorBridge;onSettled?:()=>void;onOpenCandidate?:(actionId?:string)=>void;onDismiss?:(message?:string)=>void;autoOpen?:boolean}) {
  const [review,setReview]=useState<ReviewedCandidate>(),[busy,setBusy]=useState(false),[message,setMessage]=useState(""),[code,setCode]=useState(""),[applied,setApplied]=useState(false);
  const [blockedAction,setBlockedAction]=useState<string>(),[navigationError,setNavigationError]=useState("");
  const [delivery,setDelivery]=useState<{capability:string;revision:number;receipt:string}>(),[savedPath,setSavedPath]=useState("");
  const inFlight=useRef(false),openedAutomatically=useRef(false);
  const canReview=["candidate.review","candidate.review.new"].some(name=>hasCandidateCommand(binding,name));
  const canRecover=hasCandidateCommand(binding,"application.recover");
  const application=editor?.application;
  useEffect(()=>{if(binding.applicationState==="committed"&&!applied){setCode("");setMessage("此版本此前已同步。当前正式白板可能已切换或继续修改，请以当前打开的文档为准。");}},[binding.applicationState,applied]);
  const reportError=(reason:unknown,fallback:string)=>{setCode(reason instanceof AppDomainHostError?reason.code:reason instanceof Error?reason.message:fallback);setBlockedAction(reason instanceof AppDomainHostError&&reason.code==="native_app_action_busy"?String(row(reason.details).action_id||"")||undefined:undefined);};
  const begin=async(recover=false)=>{
    if(!application||inFlight.current)return;inFlight.current=true;setBusy(true);setMessage("");setCode("");setBlockedAction(undefined);
    try{setReview(await(recover?recoverCandidate:reviewCandidate)(api,application,binding,summary));}
    catch(reason){setMessage("暂时无法打开候选审核。当前文档和候选均已保留，请刷新候选状态后继续。");reportError(reason,"candidate_review_failed");}
    finally{inFlight.current=false;setBusy(false);onSettled?.();}
  };
  useEffect(()=>{
    if(autoOpen&&currentRead&&canReview&&application&&!openedAutomatically.current){
      openedAutomatically.current=true;void begin();
    }
  },[autoOpen,currentRead,canReview,application]);
  const showApplied=async()=>{
    setNavigationError("");
    const page=summary.kind==="native"?"rotation":"whiteboard";
    try{if(editor?.show)await editor.show(page);else document.dispatchEvent(new CustomEvent("genshin-open-editor",{detail:{page}}));}
    catch{setNavigationError("已同步。暂未切换到正式文档，请从左侧打开或重试查看。");}
  };
  const apply=async()=>{
    if(!application||!review||inFlight.current)return;inFlight.current=true;setBusy(true);setMessage("");setCode("");setBlockedAction(undefined);
    try{const result=await applyReviewedCandidate(api,application,review);setApplied(true);
      const capability=(review.accepted.calculated_delivery as {delivery_capability?:unknown}|undefined)?.delivery_capability;
      if(review.summary.kind==="whiteboard"&&typeof capability==="string"&&review.summary.receipt)setDelivery({capability,revision:review.summary.revision,receipt:review.summary.receipt});
      setReview(undefined);
      setMessage(result.current?`已同步到${review.summary.kind==="native"?"计算模型":"正式白板"}，文件尚未保存。`:"候选已同步；同步后正式文档又有修改，请以当前文档为准。");
      if(result.current&&editor?.show){
        // Opening the formal page is independent of the committed application.
        // A failed navigation keeps the review receipt and offers View again.
        await showApplied();
      }}
    catch(reason){
      const code=reason instanceof AppDomainHostError?reason.code:"";
      setMessage(code==="native_app_action_busy"?"还有未完成的文件操作。请先核对该操作的结果，再同步当前候选。":"同步未完成或结果尚未确认。当前候选已保留；若出现恢复入口，请先核对同步状态，系统不会自动重复提交。");
      reportError(reason,"candidate_application_failed");
    }
    finally{inFlight.current=false;setBusy(false);onSettled?.();}
  };
  const saveResult=async()=>{
    if(!delivery||!api.dsh_client_save_whiteboard_result||busy)return;setBusy(true);setCode("");
    try{
      const response=await api.dsh_client_save_whiteboard_result({conversation_id:binding.conversationId,delivery_capability:delivery.capability,expected_revision:delivery.revision,expected_receipt:delivery.receipt}) as {code?:number;data?:{cancelled?:boolean;path?:unknown};reason?:unknown};
      if(response?.code!==0)throw new Error(typeof response?.reason==="string"?response.reason:"candidate_save_failed");
      if(response.data?.cancelled){setMessage("已取消另存为，计算结果仍保留在白板中。");return;}
      if(typeof response.data?.path!=="string")throw new Error("candidate_save_receipt_invalid");
      setSavedPath(response.data.path);setMessage("计算结果已通过另存为保存，可以在 App 中重新打开。");
    }catch(reason){setMessage("计算结果没有完成保存，当前白板和结果仍然保留。可选择新的文件名后重试。");setCode(reason instanceof Error?reason.message:"candidate_save_failed");}
    finally{setBusy(false);}
  };
  const target=review?(review.summary.kind==="native"?review.accepted.plan:review.accepted.board) as Record<string,unknown>:undefined;
  const changes=useMemo(()=>review&&target?reviewRows(review,target):[],[review,target]);
  const native=review?.summary.kind==="native";
  const characters=native?row(target?.manual_template).characters:target?.team;
  const actions=native?row(target?.rotation_model).actions:target?.actions;
  const counts=[Array.isArray(characters)?`${characters.length} 名角色`:null,Array.isArray(actions)?`${actions.length} 个动作`:null].filter(Boolean);
  const acceptedCalculated=review?row(row(review.accepted.calculated_delivery).calculated):{};
  const isFormal=review?.summary.readyScope==="formal_result";
  const closeReview=(notice="")=>{
    if(inFlight.current)return;
    setReview(undefined);setCode("");setBlockedAction(undefined);setMessage(notice);
    onDismiss?.(notice);
  };
  const blockedButton=blockedAction&&onOpenCandidate&&<Button size="sm" variant="outline" onClick={()=>{setReview(undefined);onOpenCandidate(blockedAction);}}>查看未完成的文件操作</Button>;
  return <div className="gs-candidate-review">
    <div className="gs-candidate-toolbar">
      {canReview&&!autoOpen&&<Button size="sm" onClick={()=>void begin()} disabled={!currentRead||busy||!application||!api.dsh_client_candidate_command}>审核并同步到正式白板</Button>}
      {canRecover&&<Button size="sm" variant="outline" onClick={()=>void begin(true)} disabled={!currentRead||busy||!application}>检查未完成的同步</Button>}
      {applied&&<Button size="sm" variant="outline" onClick={()=>void showApplied()}>{summary.kind==="native"?"查看计算模型":"查看正式白板"}</Button>}
      {delivery&&api.dsh_client_save_whiteboard_result&&<Button size="sm" variant="outline" disabled={busy} onClick={()=>void saveResult()}>计算结果另存为…</Button>}
    </div>
    {message&&<p role={code?"alert":"status"}>{message}</p>}
    {autoOpen&&busy&&!review&&<p role="status">正在打开候选审核…</p>}
    {!review&&blockedButton}
    {navigationError&&<p role="status">{navigationError}</p>}
    {code&&<details><summary>操作详情</summary><code>{code}</code></details>}
    {savedPath&&<p>结果文件：<code>{savedPath}</code></p>}
    <Modal open={!!review} title={review?.creation?"审核并新建正式文档":"审核候选修改"} closeLabel="关闭审核" onClose={()=>closeReview()}
      className="gs-candidate-review-dialog" contentClassName="gs-candidate-review-content"
      description={isFormal?"这份候选已有正式计算结果。同步后可在 App 中查看，并可另存文件。":"这份候选可以同步为可编辑草稿，尚无可交付的正式计算结果。"}
      footer={<div className="gs-candidate-review-actions"><Button variant="ghost" onClick={()=>closeReview("本次未同意同步。候选已保留，正式文档保持原样。")} disabled={busy}>不同意同步</Button><Button variant="outline" onClick={()=>closeReview("候选已保留。请在输入框告诉 Agent 需要改哪里。")} disabled={busy}>返回对话继续修改</Button><Button onClick={()=>void apply()} disabled={busy||!!code}>{busy?"正在同步…":"同意并同步"}</Button></div>}>
      {review&&target&&<><p>{review.creation?"将新建未保存的文档":`对照当前正式文档「${named(review.base?.document)||"未命名"}」`} · 候选版本 {review.summary.revision}{counts.length?` · ${counts.join(" · ")}`:""}</p>
        {changes.length>0&&<ul aria-label="相对正式文档的主要变化">{changes.map(change=><li key={change.label}>{change.label}　{change.change}{change.examples.length>0&&<p>{change.examples.join("、")}</p>}</li>)}</ul>}
        <details><summary>版本与校验详情</summary><code>{review.summary.receipt}</code>{typeof row(acceptedCalculated.result_receipt).database_fingerprint==="string"&&<p>资料库指纹　<code>{String(row(acceptedCalculated.result_receipt).database_fingerprint)}</code></p>}</details>
        <CandidatePreview document={target} calculated={isFormal?(Object.keys(acceptedCalculated).length?acceptedCalculated:calculated):undefined} kind={review.summary.kind} editor={editor}/></>}
      {code&&<p role="alert">{message}</p>}{blockedButton}
    </Modal>
  </div>;
}
