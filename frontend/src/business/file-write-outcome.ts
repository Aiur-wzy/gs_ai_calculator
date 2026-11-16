/** A failed write is safely retryable only when its receipt confirms no publication. */
export function confirmedNoFileWrite(response:unknown):boolean {
  if(!response||typeof response!=="object"||Array.isArray(response))return false;
  const row=response as Record<string,unknown>;
  if(row.code===0||typeof row.code!=="number")return false;
  if(row.details!=null&&(typeof row.details!=="object"||Array.isArray(row.details)))return false;
  const details=row.details&&typeof row.details==="object"&&!Array.isArray(row.details)?row.details as Record<string,unknown>:undefined;
  if(details&&Object.hasOwn(details,"publication_state")&&details.publication_state!=="not_written")return false;
  if(details&&(Object.hasOwn(details,"published_paths")||Object.hasOwn(details,"unknown_paths"))){
    return Array.isArray(details.published_paths)&&details.published_paths.length===0&&
      Array.isArray(details.unknown_paths)&&details.unknown_paths.length===0&&
      (details.publication_state==null||details.publication_state==="not_written");
  }
  return details?.publication_state==="not_written"||["invalid_plan","invalid_whiteboard","candidate_input_draft_filename",
    "candidate_input_draft_target_invalid","dsh_domain_session_unbound"].includes(String(row.reason));
}
