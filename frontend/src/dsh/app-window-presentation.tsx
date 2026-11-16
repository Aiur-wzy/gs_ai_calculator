/** Native window presentation only. DSH remains the owner of all conversation state. */
import type {Context} from "@deepseek-ai/cordis";
import {Button} from "@deepseek-ai/dsh-client-ui-primitives";
import type {PropsRuntime} from "@deepseek-ai/dsh-client-ui-slots";
import {useEffect,useRef,useState,useSyncExternalStore} from "react";
import {flushSync} from "react-dom";
import {WindowDraftRestoreError,createWindowSessionController,type WindowSessionController,type WindowHandoff as Handoff} from "./app-window-session-controller";
import type {TimelineSelection} from "../business/timeline/evidence-index";

type SessionId=Awaited<ReturnType<Context["sessions"]["create"]>>;
export type CommandResult={command_id:string;status:"located"|"stale"|"failed";message:string};
export type WindowState={role:"main"|"detached";window_id?:string;generation:number;presentation:"main"|"opening"|"detached"|"returning";handoff:Handoff|null;error?:string|null;command_result?:CommandResult|null;closing?:boolean;selected_session_id?:string|null;selection_revision?:number;selection_handoff?:Handoff|null;return_source_id?:string|null};
type Command={command_id:string;command:string;session_id:string;conversation_id?:string;revision?:number;action_id?:string;page?:string;selection?:TimelineSelection};
type WindowApi={
  dsh_window_state():Promise<WindowState>;
  dsh_window_open?(request:Handoff):Promise<WindowState>;
  dsh_window_ready?(request:{generation:number}):Promise<WindowState>;
  dsh_window_return?(request:Handoff&{generation:number}):Promise<WindowState>;
  dsh_window_draft?(request:Handoff&{generation:number}):Promise<unknown>;
  dsh_window_poll?():Promise<WindowState&{commands:Command[]}>;
  dsh_window_acknowledge?(request:{command_id:string}&Partial<Omit<CommandResult,"command_id">>):Promise<unknown>;
  dsh_window_recover?():Promise<WindowState>;
  dsh_window_cancel_return?(request:{generation:number;source_window_id:string}):Promise<WindowState>;
  dsh_window_main_ready?(request:{generation:number}):Promise<WindowState>;
  dsh_window_select?(request:Handoff&{generation:number}):Promise<WindowState>;
  dsh_window_selection_ready?(request:{generation:number;selection_revision:number}):Promise<WindowState>;
  dsh_window_command?(request:Omit<Command,"command_id">&{generation:number}):Promise<unknown>;
};
const initial:WindowState={role:"main",generation:0,presentation:"main",handoff:null};
let current=initial;
const listeners=new Set<()=>void>();
type RestoreFailure={generation:number;message:string;cancelSafe:boolean;cancelling?:boolean};
let restoreFailure:RestoreFailure|null=null;
const recoveryListeners=new Set<()=>void>();
const setRestoreFailure=(failure:RestoreFailure|null)=>{restoreFailure=failure;for(const listener of recoveryListeners)listener();};
const useRestoreFailure=()=>useSyncExternalStore(callback=>{recoveryListeners.add(callback);return()=>recoveryListeners.delete(callback);},()=>restoreFailure);
export function WindowReturnRecovery(){
  const state=useWindowPresentation(),failure=useRestoreFailure(),[busy,setBusy]=useState(false),[error,setError]=useState("");
  if(detachedWindow()||state.presentation!=="returning"||failure?.generation!==state.generation)return null;
  const cancel=async()=>{
    if(busy||restoreFailure?.cancelling||!failure.cancelSafe||!state.return_source_id)return;setBusy(true);setError("");
    setRestoreFailure({...failure,cancelling:true});
    try{
      const next=await windowHost()!.dsh_window_cancel_return!({generation:state.generation,source_window_id:state.return_source_id});
      emit(next);setRestoreFailure(null);
    }catch(cause){
      setError(cause instanceof Error?cause.message:"原窗口暂时不可用，交接记录仍保留；请重试恢复主窗口。");
      if(restoreFailure?.generation===state.generation)setRestoreFailure({...failure,cancelling:false});
    }
    finally{setBusy(false);}
  };
  return <span className="gs-window-controls"><span role="alert">{error||failure.message}</span>
    <Button size="sm" variant="ghost" disabled={busy||failure.cancelling} onClick={()=>{if(!restoreFailure?.cancelling)setRestoreFailure(null);}}>重试恢复主窗口</Button>
    {failure.cancelSafe&&state.return_source_id&&windowHost()?.dsh_window_cancel_return&&<Button size="sm" variant="ghost" disabled={busy||failure.cancelling} onClick={()=>void cancel()}>取消返回，继续在独立窗口</Button>}
  </span>;
}
let mainCommands:Command[]=[];
const commandListeners=new Set<()=>void>();
export const useMainWindowCommands=()=>useSyncExternalStore(callback=>{commandListeners.add(callback);return()=>commandListeners.delete(callback);},()=>mainCommands);
export async function acknowledgeMainCommand(id:string,result?:Omit<CommandResult,"command_id">){
  await windowHost()?.dsh_window_acknowledge?.({command_id:id,...result});
  mainCommands=mainCommands.filter(item=>item.command_id!==id);for(const listener of commandListeners)listener();
}
const emit=(state:WindowState)=>{
  if(JSON.stringify(current)===JSON.stringify(state))return;
  current=state;
  document.body.dataset.agentPresentation=state.presentation;
  for(const listener of listeners)listener();
};
export const detachedWindow=()=>document.body.dataset.agentWindow==="detached";
export const windowHost=()=>((window as unknown as {pywebview?:{api?:WindowApi}}).pywebview?.api);
export const useWindowPresentation=()=>useSyncExternalStore(callback=>{listeners.add(callback);return()=>listeners.delete(callback);},()=>current);
export async function presentInMain(command:Omit<Command,"command_id">){
  const api=windowHost();
  if(!api?.dsh_window_command)throw new Error("主窗口连接不可用，请返回主窗口处理。");
  return api.dsh_window_command({...command,generation:current.generation});
}

/** Clear an old move failure only after its handoff is acknowledged/cancelled.
 * Stable main/detached polling must not erase a new locally rejected action. */
function useWindowOperationError(state:WindowState){
  const [error,setError]=useState("");
  const prior=useRef(state),origin=useRef<Pick<WindowState,"generation"|"presentation">|undefined>(undefined);
  const report=(message:string)=>{origin.current={generation:current.generation,presentation:current.presentation};setError(message);};
  useEffect(()=>{
    const previous=prior.current;prior.current=state;
    const old=origin.current;
    if(!state.error&&["opening","returning"].includes(previous.presentation)
      &&["main","detached"].includes(state.presentation)
      &&old&&old.generation<=previous.generation&&["opening","returning"].includes(old.presentation)){
      origin.current=undefined;setError("");
    }
  },[state.generation,state.presentation,state.error]);
  return [error,report] as const;
}

const controllers=new WeakMap<Context,WindowSessionController>();
export function WindowControls({ctx,props}:{ctx:Context;props:PropsRuntime<"conversation.composer.dock">}){
  const state=useWindowPresentation(),[busy,setBusy]=useState(false),[error,setError]=useWindowOperationError(state);
  const controller=controllers.get(ctx);
  useEffect(()=>controller?.register(props.sessionId,props.inputActions),[controller,props.sessionId,props.inputActions]);
  useEffect(()=>{const failed=(event:Event)=>setError((event as CustomEvent<{message:string}>).detail.message);document.addEventListener("genshin-window-error",failed);return()=>document.removeEventListener("genshin-window-error",failed);},[]);
  useEffect(()=>{if(state.error)setError(state.error);},[state.error]);
  const move=async()=>{
    if(busy||!controller)return;setBusy(true);setError("");
    try{
      const api=windowHost();
      if(detachedWindow())emit(await api!.dsh_window_return!({...await controller.capture(),generation:state.generation}));
      else if(state.presentation!=="main"){emit(await api!.dsh_window_recover!());controller.releaseCapture();}
      else emit(await api!.dsh_window_open!(await controller.capture()));
    }catch(reason){controller.releaseCapture();setError(reason instanceof Error?reason.message:"窗口交接未完成，草稿仍保留。");}
    finally{setBusy(false);}
  };
  if(!windowHost()?.dsh_window_state)return null;
  if(!detachedWindow()&&state.presentation==="returning")return <><span className="gs-window-controls">正在恢复主窗口…</span><WindowReturnRecovery/></>;
  return <span className="gs-window-controls"><Button size="sm" variant="ghost" disabled={busy||state.presentation==="returning"} onClick={()=>void move()}>{busy?"正在移动…":state.presentation==="returning"?"正在恢复主窗口…":detachedWindow()?"返回主窗口":state.presentation==="opening"?"取消窗口交接":state.presentation==="detached"?"收回聊天":"独立窗口"}</Button>{error&&<span role="alert">{error}</span>}</span>;
}

/** Outside the hidden conversation content; owns no session or input controller. */
export function WindowRecoveryControl(){
  const state=useWindowPresentation(),[busy,setBusy]=useState(false),[error,setError]=useWindowOperationError(state);
  if(detachedWindow()||state.presentation==="main")return null;
  const recover=async()=>{
    if(busy)return;setBusy(true);setError("");
    try{emit(await windowHost()!.dsh_window_recover!());}
    catch(reason){setError(reason instanceof Error?reason.message:"聊天窗口尚未收回，请重试。");}
    finally{setBusy(false);}
  };
  if(state.presentation==="returning")return <><span className="gs-window-controls">正在恢复主窗口…</span><WindowReturnRecovery/></>;
  return <span className="gs-window-controls"><Button size="sm" variant="ghost" disabled={busy} onClick={()=>void recover()}>{busy?"正在收回聊天…":state.presentation==="opening"?"取消窗口交接":"收回聊天"}</Button>{error&&<span role="alert">{error}</span>}</span>;
}

declare global {interface Window {GenshinWindowPresentation?:{capture():Promise<Handoff>;releaseCapture():void;prepareExit():Promise<{unsent_count:number;attachments:number;sending:boolean}>;requestReturn():Promise<void>};}}

export function createWindowPresentationPlugin(){return {name:"genshin-native-window-presentation",inject:["slots","sessions","uiWorkspace","conversation","layout"],apply(ctx:Context){
  const controller=createWindowSessionController(ctx,flushSync);controllers.set(ctx,controller);
  // The official hero and active composer are mutually exclusive. The controller
  // is client-scoped and remains present even while neither slot is mounted.
  for(const name of ["conversation.hero.options","conversation.composer.dock"] as const)
    ctx.slots.inject(name,()=>ctx.slots.register({name,id:"genshin-native-window",order:80},props=><WindowControls ctx={ctx} props={props}/>));
  ctx.slots.inject("conversation.header.leading",()=>ctx.slots.register({name:"conversation.header.leading"},()=> <WindowRecoveryControl/>));
  ctx.effect(()=>{
    const previous=window.GenshinWindowPresentation;setRestoreFailure(null);let previousPresentation=current.presentation;
    const sync=()=>{const settled=!!current.error||(current.presentation==="main"&&previousPresentation!=="main")||(!detachedWindow()&&current.presentation==="detached")||(detachedWindow()&&current.presentation==="detached"&&previousPresentation==="returning");controller.presentation(detachedWindow()?current.presentation!=="detached":current.presentation!=="main",settled);previousPresentation=current.presentation;};
    listeners.add(sync);sync();
    window.GenshinWindowPresentation={capture:()=>controller.capture(),releaseCapture:()=>controller.releaseCapture(),prepareExit:()=>controller.prepareExit(),requestReturn:async()=>{try{emit(await windowHost()!.dsh_window_return!({...await controller.capture(),generation:current.generation}));}catch(error){controller.releaseCapture();throw error;}}};
    return()=>{listeners.delete(sync);window.GenshinWindowPresentation=previous;controllers.delete(ctx);controller.dispose();};
  });
  ctx.effect(()=>{
    const api=windowHost();if(!api?.dsh_window_state)return()=>{};
    let disposed=false,timer:ReturnType<typeof setTimeout>|undefined,lastAdopted=-1,lastAcknowledged=-1,lastSelection=-1,lastSelectionAck=-1,lastRequested="";
    const handled=new Set<string>();
    const poll=async()=>{
      try{
        const state=await(detachedWindow()?api.dsh_window_state():api.dsh_window_poll!());
        if(disposed)return;
        // Establish the official composer fence before any asynchronous restore,
        // including a reloaded main WebView whose previous state was "main".
        emit(state);
        if(restoreFailure&&restoreFailure.generation!==state.generation)setRestoreFailure(null);
        if(state.handoff&&state.generation!==lastAcknowledged&&((detachedWindow()&&state.presentation==="opening")||(!detachedWindow()&&state.presentation==="returning"))){
          if(lastAdopted!==state.generation){
            if(!detachedWindow()&&restoreFailure?.generation===state.generation)return;
            try{await controller.adopt(state.handoff,{preserveSelectedView:!detachedWindow(),generation:state.generation});lastAdopted=state.generation;}
            catch(error){
              if(disposed)return;
              if(!detachedWindow())setRestoreFailure({generation:state.generation,message:error instanceof Error?error.message:String(error),cancelSafe:error instanceof WindowDraftRestoreError&&error.cancelSafe});
              throw error;
            }
          }
          if(disposed)return;
          if(detachedWindow()){
            await window.GenshinDshClient?.ready;
            if(disposed)return;
            emit(await api.dsh_window_ready!({generation:state.generation}));
          }else emit(await api.dsh_window_main_ready!({generation:state.generation}));
          lastAcknowledged=state.generation;
        }else emit(state);
        if(detachedWindow()&&state.presentation==="detached"&&state.selected_session_id&&state.selection_revision!==undefined&&state.selection_revision!==lastSelectionAck){
          if(state.selection_revision!==lastSelection){
            controller.persist();await controller.adopt(state.selection_handoff??{session_id:state.selected_session_id},{seedOnly:true});lastSelection=state.selection_revision;
          }
          if(disposed)return;
          if(api.dsh_window_selection_ready)await api.dsh_window_selection_ready({generation:state.generation,selection_revision:state.selection_revision});
          lastSelectionAck=state.selection_revision;
        }else if(!detachedWindow()&&["opening","detached"].includes(state.presentation)){
          const selected=controller.selected();
          if(selected&&selected!==state.selected_session_id&&selected!==lastRequested&&api.dsh_window_select){
            try{await api.dsh_window_select({...await controller.selection(selected),generation:state.generation});lastRequested=selected;}catch(error){lastRequested="";throw error;}
          }else if(selected===state.selected_session_id)lastRequested="";
        }
        const commands=(state as WindowState&{commands?:Command[]}).commands||[];
        if(JSON.stringify(commands)!==JSON.stringify(mainCommands)){mainCommands=commands;for(const listener of commandListeners)listener();}
        for(const command of commands){
          if(!handled.has(command.command_id)){
            ctx.uiWorkspace.openSession(command.session_id as SessionId);
            if(command.command==="reveal_timeline_event"){
              let result:Omit<CommandResult,"command_id">;
              try{
                if(!command.selection||!window.GenshinEditorBridge?.revealTimelineEvent)throw new Error("主窗口暂时不能定位这段伤害；未修改白板。");
                result=await window.GenshinEditorBridge.revealTimelineEvent(command.selection);
              }catch(error){
                const message=error instanceof Error?error.message:String(error);
                result={status:(error as {code?:string})?.code==="timeline_selection_stale"?"stale":"failed",message};
                document.dispatchEvent(new CustomEvent("genshin-window-error",{detail:{message}}));
              }
              if(disposed)return;
              await acknowledgeMainCommand(command.command_id,result);
            }else if(command.command==="show_page"&&["whiteboard","rotation","result","data"].includes(command.page||"")){
              await window.GenshinEditorBridge?.show?.(command.page as "whiteboard"|"rotation"|"result"|"data");
              if(disposed)return;
              await acknowledgeMainCommand(command.command_id);
            }else ctx.layout.selectPanel("genshin-candidate-workspace" as Parameters<typeof ctx.layout.selectPanel>[0]);
            handled.add(command.command_id);
          }
        }
      }catch(reason){
        if(disposed)return;
        // Keep the current presentation intact; the owner can retry or recover it.
        document.dispatchEvent(new CustomEvent("genshin-window-error",{detail:{message:reason instanceof Error?reason.message:String(reason)}}));
      }finally{if(!disposed)timer=setTimeout(()=>void poll(),750);}
    };
    void poll();return()=>{disposed=true;if(timer)clearTimeout(timer);};
  });
}};}
