/** Explicit display metadata only; it never controls calculation or proof data. */
export function buffPresentation(buff:any):{contract:string;purpose:"calculation_sampling";reason:string}|null {
  const value=buff?.extensions?.presentation;
  if(!value || typeof value!=="object" || Array.isArray(value) || value.contract!=="buff-presentation-v1"
    || value.purpose!=="calculation_sampling" || typeof value.reason!=="string" || !value.reason.trim())return null;
  return {contract:value.contract,purpose:value.purpose,reason:value.reason};
}
export function samplingCoverage(buff:any){
  const presentation=buffPresentation(buff);
  return presentation?{kind:"calculation_sampling",label:"计算采样窗口（非游戏Buff寿命）",
    presentation,interpretation:`作者标记的计算采样窗口，并非游戏Buff的实际持续时间；此标签不认证游戏机制。${presentation.reason}`} : null;
}
