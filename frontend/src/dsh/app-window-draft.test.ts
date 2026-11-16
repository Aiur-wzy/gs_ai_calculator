import {describe,it,expect,vi} from "vitest";
import type {DraftSnapshot,SessionInput,InputState,ComposerAttachment} from "@deepseek-ai/dsh-client-ui-conversation/client";
import {captureWindowDraft,restoreWindowDraft,type AttachmentPort} from "./app-window-draft";
const semantic:DraftSnapshot={text:"/goal /tmp/ref > quote",command:{token:"/goal "},references:[{source:"file",ref:"/tmp/ref",label:"ref",clipboardText:"/tmp/ref",offset:6,length:8}],quotes:[{offset:15,length:7,text:"quote"}]};
function fixture(draft:DraftSnapshot,files:File[]=[]){
 let state={draft:draft.text,draftRev:0,attachmentIds:files.map((_,i)=>`old-${i}`),phase:"plain",occurrences:[],queue:[]} as unknown as InputState;
 let snapshot=structuredClone(draft);const listeners=new Set<()=>void>();
 const objects=new Map<string,ComposerAttachment>(files.map((file,i)=>[`old-${i}`,{id:`old-${i}`,file} as ComposerAttachment]));
 const changed=()=>{for(const listener of listeners)listener();};
 const input={state:{getSnapshot:()=>state,subscribe:(callback:()=>void)=>{listeners.add(callback);return()=>listeners.delete(callback);}},get draftSnapshot(){return snapshot;},setDraft:(next:DraftSnapshot|string)=>{snapshot=typeof next==="string"?{text:next,references:[]}:structuredClone(next);state={...state,draft:snapshot.text,draftRev:state.draftRev+1};changed();},addAttachments:(ids:InputState["attachmentIds"])=>{state={...state,attachmentIds:[...state.attachmentIds,...ids]};changed();return true;},removeAttachment:(id:string)=>{state={...state,attachmentIds:state.attachmentIds.filter(x=>x!==id)};return true;}} as unknown as SessionInput;
 const attachments={resolveDraftAttachments:ids=>ids.map(id=>objects.get(id)!).filter(Boolean),createDrafts:(_session,created)=>created.map((file,i)=>{const entry={id:`new-${i}`,file} as ComposerAttachment;objects.set(entry.id,entry);return entry;}),releaseDraftAttachment:vi.fn(id=>{objects.delete(id);})} satisfies AttachmentPort;
 return {input,attachments,objects};
}
describe("native window semantic handoff",()=>{
 it("carries command, reference and quote identities without flattening",async()=>{
  const source=fixture(semantic);const wire=await captureWindowDraft(source.input,source.attachments);
  const target=fixture({text:"old",references:[]});await restoreWindowDraft(target.input,target.attachments,"test" as never,wire);
  expect(target.input.draftSnapshot).toEqual(semantic);expect(source.input.draftSnapshot).toEqual(semantic);
 });
 it("recreates binary attachment ownership without sharing browser ids",async()=>{
  const file=new File([new Uint8Array([0,128,255])],"test.bin",{type:"application/octet-stream",lastModified:123});
  Object.defineProperty(file,"arrayBuffer",{value:async()=>new Uint8Array([0,128,255]).buffer});
  const source=fixture(semantic,[file]);const wire=await captureWindowDraft(source.input,source.attachments);expect(wire.files[0].body).toBe("AID/");
  const target=fixture({text:"old",references:[]});await restoreWindowDraft(target.input,target.attachments,"test" as never,wire);
  expect(target.input.state.getSnapshot().attachmentIds).toEqual(["new-0"]);expect(target.objects.get("new-0")!.file.name).toBe("test.bin");expect(source.objects.has("old-0")).toBe(true);
 });
 it("rolls back destination text and attachments if admission refuses",async()=>{
  const prior={text:"destination draft",references:[]};const target=fixture(prior,[new File(["x"],"old.txt")]);
  const add=target.input.addAttachments;target.input.addAttachments=vi.fn().mockReturnValueOnce(false).mockImplementation(add);
  await expect(restoreWindowDraft(target.input,target.attachments,"test" as never,{semantic,files:[{name:"new.bin",type:"",modified:0,body:"AA=="}]})).rejects.toThrow("附件");
  expect(target.input.draftSnapshot).toEqual(prior);expect(target.input.state.getSnapshot().attachmentIds).toEqual(["old-0"]);expect(target.objects.has("old-0")).toBe(true);expect(target.objects.has("new-0")).toBe(false);
 });
 it("refuses an in-flight send without editing either side",async()=>{
  const target=fixture(semantic);const state=target.input.state.getSnapshot();Object.defineProperty(state,"phase",{value:"sending"});
  await expect(captureWindowDraft(target.input,target.attachments)).rejects.toThrow("提交");expect(target.input.draftSnapshot).toEqual(semantic);
 });
});



describe("semantic draft acknowledgement ignores only JSON object property order",()=>{
 it("accepts the same references and quotes reconstructed in a different property order",async()=>{
  const target=fixture({text:"prior",references:[]}),original=target.input.setDraft;
  const reorder=(item:any):any=>Array.isArray(item)?item.map(reorder):item&&typeof item==="object"?Object.fromEntries(Object.keys(item).reverse().map(key=>[key,reorder(item[key])])):item;
  target.input.setDraft=value=>original(reorder(value));
  await restoreWindowDraft(target.input,target.attachments,"same" as never,{semantic,files:[]});
  expect(target.input.draftSnapshot).toEqual(semantic);
 });
 it("uses JSON wire omission for optional undefined properties",async()=>{
  const target=fixture({text:"prior",references:[]}),original=target.input.setDraft;
  target.input.setDraft=value=>original(JSON.parse(JSON.stringify(value)));
  await restoreWindowDraft(target.input,target.attachments,"same" as never,{semantic:{text:"draft",references:[],quotes:undefined},files:[]});
  expect(target.input.draftSnapshot.text).toBe("draft");
 });
 it.each(["reference-value","reference-range","array-order","value-type"])("rejects changed %s and restores previous draft",async mutation=>{
  vi.useFakeTimers();
  try{
   const prior={text:"original draft",references:[]},target=fixture(prior),original=target.input.setDraft;
   const expected={text:"one two",references:[{source:"reference",ref:"one",label:"one",clipboardText:"one",offset:0,length:3},{source:"reference",ref:"two",label:"two",clipboardText:"two",offset:4,length:3}]};
   target.input.setDraft=value=>{const changed=structuredClone(value) as any;if(changed.text===expected.text){if(mutation==="reference-value")changed.references[0].ref="other";if(mutation==="reference-range")changed.references[0].length=2;if(mutation==="array-order")changed.references.reverse();if(mutation==="value-type")changed.references[0].length="3";}original(changed);};
   const pending=restoreWindowDraft(target.input,target.attachments,"same" as never,{semantic:expected,files:[]});
   const refused=expect(pending).rejects.toThrow("尚未完整恢复");
   await vi.advanceTimersByTimeAsync(10001);await refused;
   expect(target.input.draftSnapshot).toEqual(prior);
  }finally{vi.useRealTimers();}
 });
});
