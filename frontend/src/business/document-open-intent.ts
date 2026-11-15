/** A short-lived UI effect fence, not a document or Agent state owner. */
export function createDocumentOpenIntents(capture:()=>Record<string,unknown>){
  let sequence=0;
  const equivalent=(a:Record<string,unknown>,b:Record<string,unknown>,allowed?:RegExp)=>
    Object.keys(a).every(key=>allowed?.test(key)||Object.is(a[key],b[key]));
  return Object.freeze({invalidate(){sequence++;},begin(){
    const request=++sequence;let base=capture();
    return Object.freeze({
      current:()=>request===sequence&&equivalent(base,capture()),
      // Only the explicit "save then continue" handler calls this after its
      // own successful save. It cannot authorize intervening input edits.
      acceptSavedIdentity:()=>{const next=capture();if(request!==sequence||!equivalent(base,next,/^(?:boardPath|boardSha|planPath|planSha|resolvedSha|boardRequest)$/))return false;base=next;return true;},
      acceptDiscardedDrafts:()=>{const next=capture();if(request!==sequence||!equivalent(base,next,/^(?:boardDraft|boardEditor|draft)$/))return false;base=next;return true;},
    });
  }});
}
