/** Window-owned presentation leases; all draft and session state stays in DSH. */
import type {Context} from "@deepseek-ai/cordis";
import type {InputActions,ConversationController} from "@deepseek-ai/dsh-client-ui-conversation/client";
import {captureWindowDraft,restoreWindowDraft,type UiDraft} from "./app-window-draft";
import {createWindowInputFence} from "./app-window-input-fence";

export type SessionId=Awaited<ReturnType<Context["sessions"]["create"]>>;
export type WindowHandoff={session_id:string;draft?:UiDraft;drafts?:Record<string,UiDraft>};
type Reference=ReturnType<Context["sessions"]["retain"]>;
type Entry={reference:Reference;fence:ReturnType<typeof createWindowInputFence>;persist:Set<()=>void>};

/** Local transfer failure, never an assertion about the underlying Host task. */
export class WindowDraftRestoreError extends Error{
  constructor(message:string,readonly cancelSafe:boolean,options?:ErrorOptions){super(message,options);this.name="WindowDraftRestoreError";}
}

export function selectedWindowSession(ctx:Context):SessionId|undefined{
  const selected=Object.entries(ctx.sessions.list.getSnapshot().byId).filter(([,row])=>(row.retainedBy.mainView??0)>0);
  // replaceMain retains the next view before releasing the previous one.
  return selected.length===1?selected[0][0] as SessionId:undefined;
}

export function createWindowSessionController(ctx:Context,commit:(write:()=>void)=>void){
  const entries=new Map<SessionId,Entry>(),ownedDrafts=new Set<SessionId>();let locked=false,disposed=false,lastRegistered:SessionId|undefined;
  // Browser-only rollback copies, scoped to one native handoff generation.
  // A failed rollback must not turn partially restored input into a new baseline.
  let pendingRestore:{generation:number;previous:Map<SessionId,UiDraft>;attempted:Set<SessionId>}|undefined;
  const ensure=(id:SessionId)=>{
    if(disposed)throw new Error("聊天窗口已关闭，原窗口仍保留交接草稿。");
    let entry=entries.get(id);if(entry)return entry;
    const reference=ctx.sessions.retain(id,{source:"workspaceOperation"});
    // This is a local window lease, never Host task ownership. It keeps unsent
    // browser attachments alive across selection changes until handoff/close.
    void reference.ready.catch(()=>{});
    entry={reference,fence:createWindowInputFence(ctx.conversation.blocks,id,commit),persist:new Set()};
    entries.set(id,entry);entry.fence.presentation(locked,false);return entry;
  };
  const captureOne=async(id:SessionId)=>{
    const entry=ensure(id);for(const persist of entry.persist)persist();await entry.reference.ready;
    if(disposed)throw new Error("聊天窗口已关闭，原窗口仍保留交接草稿。");
    return captureWindowDraft(ctx.conversation.input.for(entry.reference.binding.ctx),ctx.conversation as ConversationController);
  };
  const api={
    register(id:SessionId,actions:Pick<InputActions,"persistDraft">){
      const entry=ensure(id),persist=()=>actions.persistDraft();entry.persist.add(persist);lastRegistered=id;
      ownedDrafts.add(id);
      return()=>{persist();entry.persist.delete(persist);};
    },
    selected:()=>selectedWindowSession(ctx)??lastRegistered,
    has:(id:SessionId)=>entries.has(id),
    async capture():Promise<WindowHandoff>{
      const id=api.selected();if(!id)throw new Error("聊天仍在载入，请稍后再移动窗口。");
      try{
        ensure(id);for(const entry of entries.values())entry.fence.begin();
        const drafts:Record<string,UiDraft>={};
        for(const entryId of entries.keys())drafts[entryId]=await captureOne(entryId);
        return {session_id:id,draft:drafts[id],drafts};
      }catch(error){api.releaseCapture();throw error;}
    },
    async selection(id:SessionId):Promise<WindowHandoff>{return {session_id:id,draft:await captureOne(id)};},
    async adopt(handoff:WindowHandoff,{seedOnly=false,preserveSelectedView=false,generation}:{seedOnly?:boolean;preserveSelectedView?:boolean;generation?:number}={}){
      const drafts={...handoff.drafts,...(handoff.draft?{[handoff.session_id]:handoff.draft}:{})};
      const transaction=generation!==undefined&&pendingRestore?.generation===generation
        ?pendingRestore:{generation:generation??-1,previous:new Map<SessionId,UiDraft>(),attempted:new Set<SessionId>()};
      if(generation!==undefined)pendingRestore=transaction;
      const {previous,attempted}=transaction;
      try{
        // Capture every destination before modifying any of them. A later failure
        // must not leave an earlier session with half of a rejected handoff.
        for(const key of Object.keys(drafts)){
          const id=key as SessionId;if(seedOnly&&ownedDrafts.has(id))continue;
          if(!previous.has(id))previous.set(id,await captureOne(id));
        }
        for(const [id] of previous){
          const entry=ensure(id);attempted.add(id);
          await restoreWindowDraft(ctx.conversation.input.for(entry.reference.binding.ctx),ctx.conversation as ConversationController,id,drafts[id]);
        }
        if(disposed)throw new Error("聊天窗口已关闭，原窗口仍保留交接草稿。");
        const selected=handoff.session_id as SessionId;
        ensure(selected);
        // Returning the same session restores the embedded input without opening
        // DSH's full conversation page or replacing the user's business page.
        if(!preserveSelectedView||selectedWindowSession(ctx)!==selected)ctx.uiWorkspace.openSession(selected);
        for(const id of previous.keys())ownedDrafts.add(id);
        if(pendingRestore===transaction)pendingRestore=undefined;
      }catch(error){
        let rolledBack=true;
        for(const id of [...attempted].reverse()){
          try{
            const entry=ensure(id);
            await restoreWindowDraft(ctx.conversation.input.for(entry.reference.binding.ctx),ctx.conversation as ConversationController,id,previous.get(id)!);
          }catch{rolledBack=false;}
        }
        const detail=error instanceof Error?error.message:String(error);
        throw new WindowDraftRestoreError(rolledBack
          ?`${detail} 目标窗口的原草稿已保留，可以重试。`
          :`${detail} 目标草稿尚未全部恢复，原窗口和交接记录仍保留；请重试恢复，不要重复发送。`,rolledBack,{cause:error});
      }
    },
    persist(){for(const entry of entries.values())for(const persist of entry.persist)persist();},
    async prepareExit(){
      api.persist();let unsent_count=0,sending=false,attachments=0;
      for(const id of Object.keys(ctx.sessions.list.getSnapshot().byId)){
        const binding=ctx.sessions.binding(id as SessionId);if(!binding)continue;
        const input=ctx.conversation.input.for(binding.ctx),state=input.state.getSnapshot();
        if(input.draftSnapshot.text.trim().length>0||state.attachmentIds.length>0)unsent_count++;
        attachments+=state.attachmentIds.length;sending||=!["plain","claimed"].includes(state.phase);
      }
      return {unsent_count,sending,attachments};
    },
    releaseCapture(){for(const entry of entries.values())entry.fence.release();},
    presentation(nextLocked:boolean,settled:boolean){locked=nextLocked;for(const entry of entries.values())entry.fence.presentation(locked,settled);},
    dispose(){disposed=true;for(const entry of entries.values()){entry.fence.dispose();entry.reference.release();}entries.clear();ownedDrafts.clear();pendingRestore=undefined;},
  };
  return api;
}
export type WindowSessionController=ReturnType<typeof createWindowSessionController>;
