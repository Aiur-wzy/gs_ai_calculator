import type {AppCandidateGuiApi,AppDomainBinding} from "./app-domain-candidate";
import type {AppEditorApplicationPort} from "./app-editor-bridge";
type Row=Record<string,unknown>;
const object=(value:unknown):Row=>{if(!value||typeof value!=="object"||Array.isArray(value))throw new Error("saved_whiteboard_response_invalid");return value as Row;};
export const unwrapSaved=(value:unknown):Row=>{const row=object(value);if(row.code!==0)throw new Error(typeof row.reason==="string"?row.reason:"saved_whiteboard_failed");return object(row.data);};
export function validateSaved(value:Row):Row{
  if(!Number.isSafeInteger(value.revision)||(value.revision as number)<0||typeof value.receipt_fingerprint!=="string"||!/^[0-9a-f]{64}$/.test(value.receipt_fingerprint)||object(value.board).board_id!==value.board_id)throw new Error("saved_whiteboard_identity_invalid");return value;
}
export async function restoreSavedWhiteboard(api:AppCandidateGuiApi,binding:AppDomainBinding,editor:AppEditorApplicationPort,saved:Row){
  validateSaved(saved);if(!api.dsh_client_saved_whiteboard)throw new Error("saved_whiteboard_unavailable");
  let base=editor.capture("whiteboard");
  if(!base){
    const absence=editor.absence("whiteboard");
    const result=unwrapSaved(await api.dsh_client_saved_whiteboard({conversation_id:binding.conversationId,command:"restore_new",arguments:{expected_absent:true,expected_revision:saved.revision,expected_receipt:saved.receipt_fingerprint}}));
    const creation=object(result.document_creation);validateSaved(result);
    if(result.status!=="restore_new_ready"||result.revision!==saved.revision||result.receipt_fingerprint!==saved.receipt_fingerprint||result.board_id!==saved.board_id||creation.kind!=="whiteboard"||creation.expected_absent!==true||object(creation.seed).board_id!==saved.board_id)throw new Error("saved_whiteboard_identity_invalid");
    base=editor.createDocument("whiteboard",object(creation.seed),absence);
    const actual=await editor.applyReviewed(result,base,binding.conversationId);return {result,current:editor.matches(actual)};
  }
  if(!api.dsh_client_whiteboard_projection)throw new Error("saved_whiteboard_recovery_unavailable");
  await editor.validateSnapshot?.(base);
  const query=unwrapSaved(await api.dsh_client_whiteboard_projection({conversation_id:binding.conversationId,command:"query",arguments:{current_document:base.document,current_payload:base.applicationPayload}}));
  const projection=object(query.projection);
  if(projection.revision!==saved.revision||projection.receipt_fingerprint!==saved.receipt_fingerprint||projection.board_id!==saved.board_id)throw new Error("saved_whiteboard_changed");
  if(query.status==="none")return {result:{...saved,status:"already_current"},current:editor.matches(base)};
  if(!["result_only","restore_unsaved_target"].includes(String(query.status)))throw new Error("saved_whiteboard_formal_conflict");
  if(!editor.matches(base))throw new Error("saved_whiteboard_formal_changed");
  const result=unwrapSaved(await api.dsh_client_whiteboard_projection({conversation_id:binding.conversationId,command:"recover",arguments:{current_document:base.document,current_payload:base.applicationPayload,allow_unsaved_target:query.status==="restore_unsaved_target"}}));
  if(result.status!=="recovered"||result.revision!==saved.revision||result.receipt_fingerprint!==saved.receipt_fingerprint||object(result.board).board_id!==saved.board_id)throw new Error("saved_whiteboard_identity_invalid");
  if(!editor.matches(base))throw new Error("saved_whiteboard_formal_changed");
  const actual=await editor.applyReviewed(result,base,binding.conversationId);return {result,current:editor.matches(actual)};
}
