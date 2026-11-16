/** Thin handoff over the patched public DSH semantic-draft contract. */
import type {ConversationController, DraftSnapshot, InputState, SessionInput} from "@deepseek-ai/dsh-client-ui-conversation/client";

type SessionId=Parameters<ConversationController["createDrafts"]>[0];
export type WireFile={name:string;type:string;modified:number;body:string};
export type UiDraft={semantic:DraftSnapshot;files:WireFile[]};
export type AttachmentPort=Pick<ConversationController,"resolveDraftAttachments"|"createDrafts"|"releaseDraftAttachment">;
const editable=(state:InputState)=>["plain","claimed"].includes(state.phase);
async function fileWire(file:File):Promise<WireFile>{
  const bytes=new Uint8Array(await file.arrayBuffer());let text="";
  for(let offset=0;offset<bytes.length;offset+=16384)text+=String.fromCharCode(...bytes.subarray(offset,offset+16384));
  return {name:file.name,type:file.type,modified:file.lastModified,body:btoa(text)};
}
function wireFile(file:WireFile):File{
  return new File([Uint8Array.from(atob(file.body),char=>char.charCodeAt(0))],file.name,{type:file.type,lastModified:file.modified});
}
export async function captureWindowDraft(input:SessionInput,attachments:AttachmentPort):Promise<UiDraft>{
  const before=input.state.getSnapshot();
  if(!editable(before))throw new Error("消息正在提交，请等接纳后再移动窗口。");
  const semantic=structuredClone(input.draftSnapshot);
  const originals=attachments.resolveDraftAttachments(before.attachmentIds);
  if(originals.length!==before.attachmentIds.length)throw new Error("附件尚未准备完整，原窗口继续保留草稿。");
  const files=await Promise.all(originals.map(item=>fileWire(item.file)));
  const after=input.state.getSnapshot();
  if(!editable(after)||after.draftRev!==before.draftRev||JSON.stringify(after.attachmentIds)!==JSON.stringify(before.attachmentIds))
    throw new Error("草稿在移动前发生了变化，请重新移动；当前内容已保留。");
  return {semantic,files};
}
/** Only the unsent JSON draft wire: object order is not semantic; array order is. */
function draftWireIdentity(value:DraftSnapshot):string{
  return JSON.stringify(value,(_key,item)=>item&&typeof item==="object"&&!Array.isArray(item)
    ?Object.fromEntries(Object.keys(item).sort().map(key=>[key,item[key]])):item);
}
/** Commands are re-resolved by DSH, never reconstructed with App-owned closures. */
async function awaitSemanticRestore(input:SessionInput,expected:DraftSnapshot){
  const matches=()=>draftWireIdentity(input.draftSnapshot)===draftWireIdentity(expected);
  if(matches())return;
  await new Promise<void>((resolve,reject)=>{
    let unsubscribe=()=>{};
    const timeout=setTimeout(()=>{unsubscribe();reject(new Error("引用或命令尚未完整恢复，原窗口仍保留草稿。"));},10000);
    const check=()=>{if(matches()){clearTimeout(timeout);unsubscribe();resolve();}};
    unsubscribe=input.state.subscribe(check);check();
  });
}
export async function restoreWindowDraft(input:SessionInput,attachments:AttachmentPort,sessionId:SessionId,draft:UiDraft){
  const before=input.state.getSnapshot();
  if(!editable(before))throw new Error("消息正在发送，暂时不能移动输入区。");
  const previous=structuredClone(input.draftSnapshot),previousIds=[...before.attachmentIds];
  // Build every browser file before touching the destination. Source remains until ACK.
  const files=draft.files.map(wireFile);
  const created=attachments.createDrafts(sessionId,files,true);
  try{
    if(created.length!==files.length)throw new Error("附件尚未完整恢复，原窗口仍保留文件。");
    for(const id of previousIds)input.removeAttachment(id);
    input.setDraft(draft.semantic);
    if(!input.addAttachments(created.map(item=>item.id)))throw new Error("附件尚未完成交接，原窗口仍保留文件。");
    await awaitSemanticRestore(input,draft.semantic);
    for(const id of previousIds)attachments.releaseDraftAttachment(id);
  }catch(error){
    for(const item of created){input.removeAttachment(item.id);attachments.releaseDraftAttachment(item.id);}
    input.setDraft(previous);input.addAttachments(previousIds);
    throw error;
  }
}
