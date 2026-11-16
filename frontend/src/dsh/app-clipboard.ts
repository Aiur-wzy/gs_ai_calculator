/** WebView clipboard access may be absent or denied by the host. */
export async function copyAppText(value:string):Promise<boolean>{
  try{
    if(!navigator.clipboard?.writeText)return false;
    await navigator.clipboard.writeText(value);
    return true;
  }catch{
    return false;
  }
}
