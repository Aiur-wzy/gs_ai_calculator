/** Display-only exact entity aliases. Neither strings nor aliases establish provenance. */
import {zh} from "./shared";
const aliases:Record<string,{full:string;short:string}>={
  "artifact_set:15002":{full:"翠绿之影",short:"风套"},
  "artifact_set:10007":{full:"教官",short:"教官"},
  "weapon:14403":{full:"祭礼残章",short:"祭礼残章"},
};
export function displayName(full:unknown,entity?:{entity_type?:string;database_id?:string|number}):{short:string;full:string}{
  const name=String(full||"来源未记录"),alias=entity?aliases[`${entity.entity_type}:${entity.database_id}`]:undefined;
  // A stale/wrong ID-name pair must never apply a new entity's nickname.
  return {short:alias?.full===name?alias.short:name,full:name};
}
/** Saved entity identity supplies a display hint, never an execution identity. */
export function eventDisplayName(event:any):{short:string;full:string} {
  const full=String(event.sourceLabel||event.label||"来源未记录"),bindings=event.detail?.source_bindings;
  if(bindings?.length!==1)return {short:full,full};
  const owner=bindings[0].owner||{},id=owner.kind==="artifact_set"?owner.set_id:owner.kind==="weapon"?owner.weapon_id:null;
  const alias=id==null?null:aliases[`${owner.kind}:${id}`];
  if(!alias)return {short:full,full};
  if(owner.name_zh&&owner.name_zh!==alias.full)return {short:full,full};
  const effect=String(event.detail?.source_name||event.label||"");
  return {short:effect.includes(alias.full)?effect.replace(alias.full,alias.short):`${alias.short} · ${effect}`,full};
}
export function sourceRowName(event:any):string {
  const {short,full}=eventDisplayName(event);
  if(short!==full)return short;
  const recorded=[event.detail?.source_name,event.detail?.buff?.name].find(name=>typeof name==="string"&&zh(name));
  return recorded?.trim()||full;
}

export function compactLaneName(name:string,owner:string|undefined,max=8):string {
  const prefix=owner?`${owner} · `:"";
  const characters=Array.from(prefix&&name.startsWith(prefix)?name.slice(prefix.length):name);
  if(characters.length<=max)return characters.join("");
  // Keep the distinguishing suffix of mechanically related sources (e.g. variants and damage types).
  return `${characters.slice(0,max-4).join("")}…${characters.slice(-3).join("")}`;
}
export function appendFullName(parent:HTMLElement,doc:Document,full:string):void {
  const details=doc.createElement("details"),summary=doc.createElement("summary");details.className="tl-name-details";
  summary.textContent="完整名称";const text=doc.createElement("p");text.textContent=full;details.append(summary,text);parent.append(details);
}
