import {bindingWithHostProjection} from "./app-domain-bridge";
import type {AppCandidateGuiApi,AppDomainBinding,CandidateSummary} from "./app-domain-candidate";
import type {AppEditorApplicationPort,AppEditorSnapshot} from "./app-editor-bridge";
import {assertAppDomainSuccess} from "./app-domain-errors";
type Row=Record<string,unknown>;
function object(value:unknown):Row {if(!value||typeof value!=="object"||Array.isArray(value))throw new Error("candidate_command_response_invalid");return value as Row;}
export function hasCandidateCommand(binding:AppDomainBinding,name:string){return binding.availableCommands.some(row=>row.commandId===name&&row.enabled);}
async function command(api:AppCandidateGuiApi,binding:AppDomainBinding,name:string,args:Row){
  if(!api.dsh_client_candidate_command||!hasCandidateCommand(binding,name))throw new Error("candidate_command_unavailable");
  const envelope=object(await api.dsh_client_candidate_command({conversation_id:binding.conversationId,command_id:name,expected_projection_revision:binding.projectionRevision,arguments:args}));
  assertAppDomainSuccess(envelope);
  if(envelope.code!==0)throw new Error("candidate_command_failed");
  const data=object(envelope.data);return {result:object(data.result),binding:bindingWithHostProjection(binding,data.projection)};
}
export type ReviewedCandidate={binding:AppDomainBinding;summary:CandidateSummary;base:AppEditorSnapshot|null;accepted:Row;target:Row;creation?:{seed:Row;absence:unknown};prepared?:boolean};
const fields=(review:ReviewedCandidate)=>({kind:review.summary.kind,expected_revision:review.summary.revision,expected_receipt:review.summary.receipt});
export async function reviewCandidate(api:AppCandidateGuiApi,editor:AppEditorApplicationPort,binding:AppDomainBinding,summary:CandidateSummary):Promise<ReviewedCandidate>{
  const base=editor.capture(summary.kind),absence=base?undefined:editor.absence(summary.kind);
  if(base)await editor.validateSnapshot?.(base);
  const response=await command(api,binding,base?"candidate.review":"candidate.review.new",{kind:summary.kind,document:base?.document??null,expected_revision:summary.revision,expected_receipt:summary.receipt});
  const accepted=response.result;
  if(accepted.kind!==summary.kind||accepted.revision!==summary.revision||accepted.receipt_fingerprint!==summary.receipt)throw new Error("candidate_review_identity_mismatch");
  const target=summary.kind==="native"?{kind:"native",plan:object(accepted.plan)}:{kind:"whiteboard",board:object(accepted.board),calculated:accepted.calculated_delivery?object(accepted.calculated_delivery).calculated??null:null};
  let creation:ReviewedCandidate["creation"];
  if(!base){const claim=object(accepted.document_creation);if(claim.kind!==summary.kind||claim.expected_absent!==true)throw new Error("candidate_creation_authority_invalid");creation={seed:object(claim.seed),absence};}
  return {binding:response.binding,summary,base,accepted,target,creation};
}
/** Exactly one prepare, Renderer mutation and ACK; never retries ambiguous effects. */
export async function applyReviewedCandidate(api:AppCandidateGuiApi,editor:AppEditorApplicationPort,review:ReviewedCandidate){
  let base=review.base;
  if(review.creation)base=editor.createDocument(review.summary.kind,review.creation.seed,review.creation.absence);
  if(!base||!editor.matches(base))throw new Error("candidate_formal_document_changed");
  const prepared=review.prepared?{binding:review.binding}:await command(api,review.binding,"candidate.apply.prepare",{...fields(review),document:object(review.target[review.summary.kind==="native"?"plan":"board"]),baseline_payload:base.applicationPayload,target_payload:review.target});
  if(!editor.matches(base))throw new Error("candidate_prepared_formal_document_changed");
  const actual=await editor.applyReviewed(review.accepted,base,review.binding.conversationId);
  if(!editor.matches(actual))throw new Error("candidate_applied_formal_document_changed");
  const acknowledged=await command(api,prepared.binding,"application.ack",{...fields(review),document:actual.document,actual_payload:actual.applicationPayload});
  const proof=object(acknowledged.result.proof);
  if(acknowledged.result.status!=="applied"||proof.kind!==review.summary.kind||proof.revision!==review.summary.revision||proof.receipt_fingerprint!==review.summary.receipt)throw new Error("candidate_application_ack_invalid");
  return {binding:acknowledged.binding,current:editor.matches(actual)};
}

export async function recoverCandidate(api:AppCandidateGuiApi,editor:AppEditorApplicationPort,binding:AppDomainBinding,summary:CandidateSummary):Promise<ReviewedCandidate>{
  const base=editor.capture(summary.kind);if(!base)throw new Error("candidate_recovery_document_required");
  const response=await command(api,binding,"application.recover",{current_document:base.document,current_payload:base.applicationPayload,discard_current:false});
  const accepted=response.result;
  if(accepted.kind!==summary.kind||accepted.revision!==summary.revision||accepted.receipt_fingerprint!==summary.receipt)throw new Error("candidate_recovery_identity_mismatch");
  const target=summary.kind==="native"?{kind:"native",plan:object(accepted.plan)}:{kind:"whiteboard",board:object(accepted.board),calculated:accepted.calculated_delivery?object(accepted.calculated_delivery).calculated??null:null};
  return {binding:response.binding,summary,base,accepted,target,prepared:true};
}
