import {notifySubscribers} from "@deepseek-ai/dsh-client-store";

// Transient presentation of a user file-open result, never Session/domain authority.
let current:Readonly<{sessionId:string;data:Record<string,unknown>}>|undefined;
const listeners=new Set<()=>void>();
export const selectedDraftNotice={
  publish(sessionId:string,data:Record<string,unknown>){current=Object.freeze({sessionId,data});notifySubscribers(listeners,"[genshin-draft-file-notice]");},
  snapshot(sessionId:string){return current?.sessionId===sessionId?current:undefined;},
  subscribe(listener:()=>void){listeners.add(listener);return()=>{listeners.delete(listener);};},
};

export type DraftOpenOptions=Readonly<{newCopy?:boolean;targetSessionId?:string;isCurrent?:()=>boolean}>;
export type DraftOpenResult=Readonly<{opened:boolean;message:string;reused?:boolean}>;
export type CandidateDraftFilePort={openSelected(path:string,fileSha:string,options?:DraftOpenOptions):Promise<DraftOpenResult>};
declare global {interface Window {GenshinCandidateDraftFiles?:CandidateDraftFilePort;}}

type OpenedPreview=Readonly<{sessionId:string;data:Record<string,unknown>}>;
type DraftFileNavigation={
  createSession():Promise<string>;
  sessionExists(sessionId:string):boolean;
  canUseUnstarted(sessionId:string):boolean;
  previewIsCurrent(preview:OpenedPreview):Promise<boolean>;
  importFile(sessionId:string,path:string,fileSha:string):Promise<Record<string,unknown>>;
  navigate(preview:OpenedPreview):void;
};

/** File-view deduplication only; DSH remains the Session and navigation owner. */
export function createCandidateDraftFilePort(navigation:DraftFileNavigation):CandidateDraftFilePort{
  const previews=new Map<string,OpenedPreview>();
  let pending=Promise.resolve();
  return {openSelected(path,fileSha,options={}){
    const open=async():Promise<DraftOpenResult>=>{
      const stale=()=>({opened:false,message:"打开期间当前输入已变化，未切换到旧文件；请重新打开。"});
      if(options.isCurrent?.()===false)return stale();
      if(!/^[0-9a-f]{64}$/.test(fileSha))throw new Error("候选输入草稿的文件校验信息无效，请重新选择。");
      const existing=previews.get(fileSha);
      if(!options.newCopy&&existing&&navigation.sessionExists(existing.sessionId)&&await navigation.previewIsCurrent(existing)){
        if(options.isCurrent?.()===false)return stale();
        navigation.navigate(existing);
        return {opened:true,reused:true,message:"已切换到这份草稿已有的工作副本；当前输入未被覆盖。"};
      }
      const target=options.targetSessionId;
      const alreadyUsed=target!==undefined&&[...previews.values()].some(row=>row.sessionId===target);
      const sessionId=!options.newCopy&&target!==undefined&&!alreadyUsed&&navigation.canUseUnstarted(target)?target:await navigation.createSession();
      if(options.isCurrent?.()===false)return stale();
      const data=await navigation.importFile(sessionId,path,fileSha);
      if(options.isCurrent?.()===false)return {opened:false,message:"文件副本已导入独立候选，但当前文档随后变化，未切换页面；可从会话列表查看副本。"};
      const preview=Object.freeze({sessionId,data});
      previews.set(fileSha,preview);navigation.navigate(preview);
      return {opened:true,reused:false,message:(data.preview as {message?:string})?.message||"候选输入草稿已打开；正式白板未改变。"};
    };
    // Serialise distinct user-open gestures, so double clicks share the first
    // completed preview instead of racing two Session creations.
    const result=pending.then(open);pending=result.then(()=>undefined,()=>undefined);return result;
  }};
}
