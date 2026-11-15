import type {AppCandidateGuiApi,AppDomainBinding} from "./app-domain-candidate";
import type {AppEditorFilePort,AppFileOutcome,AppFilePhase} from "./app-editor-bridge";
import {appFileWorkspaceState} from "./app-editor-bridge";

export const APP_ACTION_LABELS:Record<string,string>={save_current:"保存当前正式文件",save_as:"正式文档另存为",open_document:"打开文件",export_result:"导出计算结果",export_candidate_input_draft:"保存候选输入草稿",navigate_rotation:"查看计算模型",navigate_result:"查看计算报告",navigate_whiteboard:"查看排轴白板"};
export type AppAction=Readonly<{action_id:string;action:string;reason:string;suggested_name:string|null;status:string;ui_receipt:string|null;target?:Readonly<Record<string,unknown>>;legacy_read_only?:boolean;result_message?:string;execution_outcome_unknown?:boolean;draft_publication?:Readonly<{status:"verified"|"written_unverified";path?:string}>}>;
const record=(value:unknown):Record<string,unknown>=>{if(!value||typeof value!=="object"||Array.isArray(value))throw new Error("app_action_response_invalid");return value as Record<string,unknown>;};
const unwrap=(value:unknown)=>{const row=record(value);if(row.code!==0)throw new Error(typeof row.reason==="string"?row.reason:"app_action_failed");return record(row.data);};
export function parseAppAction(value:unknown):AppAction{
  const row=record(value);if(typeof row.action_id!=="string"||!row.action_id||typeof row.action!=="string"||!Object.hasOwn(APP_ACTION_LABELS,row.action)||typeof row.reason!=="string"||!['queued','presented','claimed_for_external_execution','completed','cancelled','failed','expired'].includes(String(row.status))||(row.ui_receipt!==null&&typeof row.ui_receipt!=="string")||(row.suggested_name!==null&&typeof row.suggested_name!=="string"))throw new Error("app_action_response_invalid");
  return row as AppAction;
}
export function parseAppActions(value:unknown):AppAction[]{const data=unwrap(value);if(!Array.isArray(data.actions))throw new Error("app_action_response_invalid");const rows=data.actions.map(parseAppAction),ids=new Set(rows.map(row=>row.action_id));if(ids.size!==rows.length)throw new Error("app_action_response_invalid");return rows;}
const targetKey=(value:unknown)=>value&&typeof value==="object"?JSON.stringify(value,Object.keys(value).sort()):String(value);
function sameAction(value:unknown,expected:AppAction,receipt?:string):AppAction{const card=parseAppAction(value);if(card.action_id!==expected.action_id||card.action!==expected.action||card.reason!==expected.reason||card.suggested_name!==expected.suggested_name||targetKey(card.target)!==targetKey(expected.target)||(receipt!==undefined&&card.ui_receipt!==receipt))throw new Error("app_action_identity_mismatch");return card;}
export class AppActionSettlementPending extends Error{constructor(readonly action:AppAction,readonly receipt:string,readonly outcome:AppFileOutcome){super("app_action_settlement_pending");}}
export class AppActionExecutionPending extends Error{constructor(readonly action:AppAction,readonly receipt:string,readonly reason?:string){super("app_action_execution_outcome_unknown");}}
async function command(api:AppCandidateGuiApi,binding:AppDomainBinding,name:"present"|"claim"|"settle"|"recover",args:Record<string,unknown>){if(!api.dsh_client_app_action)throw new Error("app_action_unavailable");return unwrap(await api.dsh_client_app_action({conversation_id:binding.conversationId,command:name,arguments:args}));}
export async function settleAppAction(api:AppCandidateGuiApi,binding:AppDomainBinding,action:AppAction,receipt:string,outcome:AppFileOutcome,files?:AppEditorFilePort){
  try{
    if(files&&api.dsh_client_workspace_state&&!outcome.read_only_open){const current=files.capture();unwrap(await api.dsh_client_workspace_state(appFileWorkspaceState(binding.conversationId,current)));}
    const result=await command(api,binding,"settle",{action_id:action.action_id,ui_receipt:receipt,outcome});return sameAction(result.card,action,receipt);
  }
  catch{throw new AppActionSettlementPending(action,receipt,outcome);}
}
/** Existing Host ledger is the one-shot authority; this is an imperative adapter. */
export async function executeAppAction(api:AppCandidateGuiApi,binding:AppDomainBinding,files:AppEditorFilePort,initial:AppAction,onPhase?:(phase:AppFilePhase)=>void){
  if(initial.legacy_read_only)throw new Error("app_action_legacy_read_only");
  if(!api.dsh_client_workspace_state)throw new Error("app_workspace_state_unavailable");
  const snapshot=files.capture();unwrap(await api.dsh_client_workspace_state(appFileWorkspaceState(binding.conversationId,snapshot)));
  if(!files.matches(snapshot))throw new Error("app_action_document_changed");
  let action=initial;
  if(action.status==="queued"){
    const presented=await command(api,binding,"present",{action_id:action.action_id});action=sameAction(presented.card,initial);
    if(presented.status!=="presented_for_user"||action.status!=="presented")throw new Error("app_action_not_presented");
  }
  const receipt=action.ui_receipt;if(action.status!=="presented"||typeof receipt!=="string"||!/^ui_[A-Za-z0-9_-]{43}$/.test(receipt))throw new Error("app_action_receipt_invalid");
  if(!files.matches(snapshot))throw new Error("app_action_document_changed");
  const claim=await command(api,binding,"claim",{action_id:action.action_id,ui_receipt:receipt});const claimedCard=sameAction(claim.card,initial,receipt);
  if(claim.status!=="claimed_for_external_execution"){if(claim.request!=null)throw new Error("app_action_request_invalid");return claimedCard;}
  let outcome:AppFileOutcome,request:Record<string,unknown>;
  try{
    request=record(claim.request);const keys=["action_id","action","reason","suggested_name","document_kind","display_name","current_view","dirty_open_guard_required","target"];
    if(Object.keys(request).some(key=>!keys.includes(key))||request.action_id!==action.action_id||request.action!==action.action||request.reason!==action.reason||request.suggested_name!==action.suggested_name||targetKey(request.target)!==targetKey(action.target)||request.document_kind!==snapshot.workspace.document_kind||request.display_name!==snapshot.workspace.display_name||request.current_view!==snapshot.workspace.current_view||typeof request.dirty_open_guard_required!=="boolean"||!files.matches(snapshot))throw new Error("app_action_request_invalid");
  }catch{return settleAppAction(api,binding,action,receipt,{status:"failed",message:"操作请求未通过核对，没有执行文件操作。",dirty_open_guard_passed:false},files);}
  try{
    outcome=await files.execute(action.action==="export_candidate_input_draft"?{...request!,ui_receipt:receipt}:request!,snapshot,binding.conversationId,onPhase);
    if(!outcome||!['completed','cancelled','failed'].includes(outcome.status)||typeof outcome.message!=="string"||typeof outcome.dirty_open_guard_passed!=="boolean")throw new Error("app_action_outcome_invalid");
  }catch(error){
    // The consumer may have written before its response was lost. Preserve the
    // Host's exact durable claim: neither failed nor completed is now proven.
    throw new AppActionExecutionPending(action,receipt,error instanceof Error?error.message:undefined);
  }
  return settleAppAction(api,binding,action,receipt,outcome,files);
}
export async function recoverAppAction(api:AppCandidateGuiApi,binding:AppDomainBinding,action:AppAction){
  if(!action.ui_receipt)throw new Error("app_action_receipt_invalid");
  const result=await command(api,binding,"recover",{action_id:action.action_id,ui_receipt:action.ui_receipt});
  return sameAction(result.card,action,action.ui_receipt);
}
