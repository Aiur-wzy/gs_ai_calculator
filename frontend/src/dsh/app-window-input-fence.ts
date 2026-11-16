/** UI-only handoff ownership. DSH still owns submissions and running tasks. */
import type {ConversationController} from "@deepseek-ai/dsh-client-ui-conversation/client";

type Blocks=ConversationController["blocks"];
type SessionId=Parameters<Blocks["set"]>[0];
type Block=ReturnType<ReturnType<Blocks["storeFor"]>["getSnapshot"]>;
const moving="正在移动聊天窗口，草稿已保留…";
const detached="聊天位于独立窗口，可使用「收回聊天」返回。";
export function createWindowInputFence(blocks:Blocks,sessionId:SessionId,commit:(write:()=>void)=>void){
  let pending=false,presentationLocked=false,owned=false,previous:Block;
  const update=()=>{
    const store=blocks.storeFor(sessionId),current=store.getSnapshot();
    if(pending||presentationLocked){
      if(!owned){previous=current;owned=true;}
      // Never replace a later block installed by another DSH UI plugin.
      if(current!==undefined&&current.reason!==moving&&current.reason!==detached&&current!==previous)return;
      blocks.set(sessionId,{reason:presentationLocked&&!pending?detached:moving});
    }else if(owned){
      if(current?.reason===moving||current?.reason===detached)blocks.set(sessionId,previous);
      owned=false;previous=undefined;
    }
  };
  return {
    begin(){
      if(pending)throw new Error("窗口正在交接，请等待当前操作完成。");
      pending=true;commit(update);
    },
    release(){pending=false;commit(update);},
    presentation(locked:boolean,settled:boolean){presentationLocked=locked;if(settled)pending=false;update();},
    dispose(){pending=false;presentationLocked=false;update();},
  };
}
