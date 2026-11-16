import {useState} from "react";
import {Button,Modal} from "@deepseek-ai/dsh-client-ui-primitives";
import type {AppCandidateGuiApi,AppDomainBinding,AppDomainBindings} from "./app-domain-candidate";
import {useAppDomainRead} from "./app-domain-read";

export type DomainApproval=Readonly<{questionId:string;entityType:string;provisionalId:string;candidateRevision:number;stageRevision:number;fingerprint:string;question:string;detail:string;header:string}>;
const object=(value:unknown):Record<string,unknown>=>{if(!value||typeof value!=="object"||Array.isArray(value))throw new Error("app_domain_approval_invalid");return value as Record<string,unknown>;};
const text=(value:unknown)=>{if(typeof value!=="string"||!value)throw new Error("app_domain_approval_invalid");return value;};
const natural=(value:unknown)=>{if(!Number.isSafeInteger(value)||(value as number)<0)throw new Error("app_domain_approval_invalid");return value as number;};
export function parseDomainApprovals(value:unknown):DomainApproval[]{
  const envelope=object(value);if(envelope.code!==0)throw new Error(typeof envelope.reason==="string"?envelope.reason:"app_domain_approval_read_failed");
  const data=object(envelope.data);if(!Array.isArray(data.approvals))throw new Error("app_domain_approval_invalid");const ids=new Set<string>();
  return data.approvals.map(value=>{const row=object(value),question=object(row.question),id=text(row.question_id);
    if(ids.has(id)||question.id!==id||!["pending","bound"].includes(String(row.state))||typeof row.payload_fingerprint!=="string"||!/^[0-9a-f]{64}$/.test(row.payload_fingerprint))throw new Error("app_domain_approval_invalid");ids.add(id);
    return {questionId:id,entityType:text(row.entity_type),provisionalId:text(row.provisional_id),candidateRevision:natural(row.expected_candidate_revision),stageRevision:natural(row.expected_stage_revision),fingerprint:row.payload_fingerprint,question:text(question.question),detail:typeof question.detail==="string"?question.detail:"",header:typeof question.header==="string"?question.header:"公共资料修正"};
  });
}

export function AppDomainApprovals({api,binding,bindings,onSettled}:{api:AppCandidateGuiApi;binding:AppDomainBinding;bindings:AppDomainBindings;onSettled?:()=>void}){
  const state=useAppDomainRead(bindings,binding,"domain-approvals",async()=>parseDomainApprovals(await api.dsh_client_domain_approvals!({conversation_id:binding.conversationId})));
  const [selected,setSelected]=useState<DomainApproval>(),[busy,setBusy]=useState(false),[message,setMessage]=useState(""),[error,setError]=useState("");
  const decide=async(decision:"approve"|"reject")=>{
    if(!selected||busy||!api.dsh_client_domain_approval_decide)return;setBusy(true);setError("");
    try{
      const response=object(await api.dsh_client_domain_approval_decide({conversation_id:binding.conversationId,question_id:selected.questionId,expected_candidate_revision:selected.candidateRevision,expected_stage_revision:selected.stageRevision,decision}));
      if(response.code!==0)throw new Error(typeof response.reason==="string"?response.reason:"app_domain_approval_decision_failed");
      const result=object(response.data);if(typeof result.decision_receipt!=="string"||!result.decision_receipt)throw new Error("app_domain_approval_receipt_invalid");
      setSelected(undefined);setMessage(decision==="approve"?"已批准这项资料修正，仅用于当前方案。":"已拒绝这项资料修正。");state.refresh();onSettled?.();
    }catch(reason){setError(reason instanceof Error?reason.message:"app_domain_approval_decision_failed");setMessage("确认结果尚未核实，请刷新后查看状态。系统不会自动重复提交。");}
    finally{setBusy(false);}
  };
  if(!api.dsh_client_domain_approvals||!api.dsh_client_domain_approval_decide)return null;
  if(!state.value?.length&&!state.error&&!message)return null;
  return <section className="gs-domain-approvals" aria-label="资料修正确认">
    {state.value?.map(row=><div className="gs-domain-card" key={row.questionId}><strong>{row.header}</strong><p>{row.question}</p><Button size="sm" variant="outline" disabled={busy} onClick={()=>{setSelected(row);setError("");}}>核对修正内容</Button></div>)}
    {(state.error||message)&&<p role={state.error||error?"alert":"status"}>{state.error?"暂时无法读取资料修正请求，可刷新后重试。":message}</p>}
    {(state.error||error)&&<details><summary>操作详情</summary><code>{state.error||error}</code><Button size="sm" variant="outline" disabled={busy} onClick={state.refresh}>刷新状态</Button></details>}
    <Modal open={!!selected} title="确认当前方案的资料修正" closeLabel="关闭资料修正确认" onClose={()=>{if(!busy)setSelected(undefined);}}
      className="gs-domain-dialog" contentClassName="gs-domain-dialog-content"
      description="这是 App 资料修正授权，不是普通聊天中的回答。请核对差异和来源；不会写回公共数据库。"
      footer={<div className="gs-domain-dialog-actions"><Button variant="outline" disabled={busy} onClick={()=>void decide("reject")}>拒绝修正</Button><Button disabled={busy} onClick={()=>void decide("approve")}>{busy?"正在记录…":"批准当前修正"}</Button></div>}>
      {selected&&<><h3>{selected.question}</h3><p className="gs-domain-prewrap">{selected.detail||"未提供详细差异，请暂不批准并向 Agent 要求说明。"}</p><dl><dt>临时资料</dt><dd>{selected.entityType} · {selected.provisionalId}</dd><dt>绑定版本</dt><dd>候选 {selected.candidateRevision} · 阶段 {selected.stageRevision}</dd><dt>内容标识</dt><dd><code>{selected.fingerprint}</code></dd></dl></>}
      {error&&<p role="alert">{message}</p>}
    </Modal>
  </section>;
}
