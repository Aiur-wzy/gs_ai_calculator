import type {Context} from "@deepseek-ai/cordis";
import type {} from "@deepseek-ai/dsh-client-ui-layout/client";
import type {} from "@deepseek-ai/dsh-client-ui-sidebar/client";
import type {} from "@deepseek-ai/dsh-client-ui-theme/client";
import type {} from "@deepseek-ai/dsh-client-ui-workspace/client";
import {useEffect, useRef,useState} from "react";
import {Button} from "@deepseek-ai/dsh-client-ui-primitives";
import {conversationPhase} from "@deepseek-ai/dsh-client-ui-conversation/client";
import type {FactoryComponentPropsOf,PropsRenderFactories,PropsRuntime} from "@deepseek-ai/dsh-client-ui-slots";
import type {AppEditorBridge} from "./app-editor-bridge";
import {useWindowPresentation,WindowRecoveryControl} from "./app-window-presentation";

const PAGES = [
  ["whiteboard", "排轴白板", "▦"], ["rotation", "计算模型（高级）", "⚙"],
  ["result", "计算报告", "≡"], ["data", "资料库", "▤"],
] as const;
type EditorPage=typeof PAGES[number][0];
declare module "@deepseek-ai/dsh-client-ui-slots" {
  interface SlotMap {"genshin.editor.assistant":{kind:"single";scope:"session-maybe"};"genshin.editor.assistant.actions":{kind:"single";scope:"session-maybe"};}
  interface SlotFactoryMap {"genshin.editor.workbench":{scope:"root";props:{page:EditorPage};children:{"genshin.editor.assistant":{kind:"single";scope:"session-maybe"};"genshin.editor.assistant.actions":{kind:"single";scope:"session-maybe"}}};}
}

function EmbeddedConversation(props:PropsRuntime<"genshin.editor.assistant">&PropsRenderFactories) {
  const session=props.useSession(value=>value),conversation=props.useConversation(value=>value);
  const blank=props.useSessions(value=>props.sessionId?value.byId[props.sessionId]?.blank:undefined);
  const phase=session&&conversation?conversationPhase(session,conversation):"blank";
  const settling=props.sessionId!==undefined&&phase==="blank"&&["cold","loading"].includes(session?.openState||"")&&blank!==true;
  return props.renderFactorySlot("conversation.content",{variant:"embedded",phase:settling?"settling":phase==="blank"?"hero":"active",hero:!settling&&phase==="blank"});
}

function UnstartedConversation({ctx,props}:{ctx:Context;props:PropsRuntime<"conversation.hero.options">}){
  const blank=props.useSession(value=>value.blank),[busy,setBusy]=useState(false),[message,setMessage]=useState("");
  if(!blank)return null;
  const remove=async()=>{
    if(busy)return;
    const current=ctx.sessions.binding(props.sessionId)?.session.getSnapshot();
    if(!current?.blank||current.running||current.pendingSubmissions.length){setMessage("消息正在提交或会话已经开始，请先等待当前操作完成。");return;}
    setBusy(true);setMessage("");
    try{await ctx.uiWorkspace.archiveSession(props.sessionId);}
    catch{setMessage("暂时无法移除此入口，请刷新会话状态后重试。");}
    finally{setBusy(false);}
  };
  return <span className="gs-unstarted-conversation"><span role="status">未开始</span><Button size="sm" variant="ghost" disabled={busy} aria-label="移除未开始入口" title="仅从会话列表移除这个空入口，白板和文件保持不变" onClick={()=>void remove()}>{busy?"正在移除…":"移除"}</Button>{message&&<span role="alert">{message}</span>}</span>;
}

function EditorWorkbench({editor,props}:{editor:AppEditorBridge;props:FactoryComponentPropsOf<"genshin.editor.workbench">}) {
  const presentation=useWindowPresentation();
  const [visible,setVisible]=useState(()=>{try{return localStorage.getItem("genshin.editor.assistant.hidden")==="0";}catch{return false;}});
  const [width,setWidth]=useState(()=>{try{const stored=Number(localStorage.getItem("genshin.editor.assistant.width"));return Number.isFinite(stored)&&stored>=300&&stored<=900?stored:400;}catch{return 400;}});
  const drag=useRef<{id:number;x:number;width:number}>();
  const workbench=useRef<HTMLDivElement>(null);
  const business=useRef<HTMLElement>(null);
  const [available,setAvailable]=useState(0),[narrowChat,setNarrowChat]=useState(false);
  useEffect(()=>{
    const node=workbench.current;
    if(!node)return;
    const measure=()=>{const width=node.clientWidth;if(width>0)setAvailable(width);};
    measure();
    if(typeof ResizeObserver==="function"){
      const observer=new ResizeObserver(measure);observer.observe(node);
      return ()=>observer.disconnect();
    }
    window.addEventListener("resize",measure);
    return ()=>window.removeEventListener("resize",measure);
  },[]);
  const narrow=available>0&&available<720;
  const showing=presentation.presentation==="main"&&(narrow?narrowChat:visible);
  const maximum=available?Math.max(300,Math.min(900,available-360-5)):900;
  const effectiveWidth=Math.min(width,maximum);
  const resize=(value:number)=>{const next=Math.max(300,Math.min(900,value));setWidth(next);try{localStorage.setItem("genshin.editor.assistant.width",String(next));}catch{/* Session-local layout remains usable. */}};
  const toggle=()=>{if(narrow){setNarrowChat(value=>!value);return;}setVisible(value=>{try{localStorage.setItem("genshin.editor.assistant.hidden",value?"1":"0");}catch{/* Session-local only. */}return !value;});};
  useEffect(()=>{
    const listener=(event:Event)=>{event.preventDefault();toggle();};
    document.addEventListener("genshin-toggle-assistant",listener);
    return ()=>document.removeEventListener("genshin-toggle-assistant",listener);
  },[narrow]);
  useEffect(()=>{
    const reveal=(event:Event)=>{if((event as CustomEvent<{page:string}>).detail?.page===props.page)setNarrowChat(false);};
    document.addEventListener("genshin-open-editor",reveal);
    return ()=>document.removeEventListener("genshin-open-editor",reveal);
  },[props.page]);
  useEffect(()=>{
    if(business.current&&!business.current.hidden&&business.current.contains(editor.root)&&!editor.root.hidden){
      document.dispatchEvent(new CustomEvent("genshin-editor-visible",{detail:{page:props.page}}));
    }
  },[editor,props.page,narrow,showing]);
  return <div ref={workbench} className="gs-editor-workbench" data-layout={narrow?"narrow":"wide"} data-embedded-assistant={showing?"open":"closed"} style={{gridTemplateColumns:showing&&!narrow?`minmax(0,1fr) 5px ${effectiveWidth}px`:"minmax(0,1fr)"}}>
    <section ref={business} className="gs-editor-business" hidden={narrow&&showing}><div className="gs-editor-toolbar">{presentation.presentation!=="main"?<WindowRecoveryControl/>:<Button size="sm" variant="ghost" aria-expanded={showing} onClick={toggle}>{showing?"隐藏 AI 小窗":"打开 AI 小窗"}</Button>}{presentation.error&&<span role="alert">{presentation.error}</span>}</div><EditorIsland editor={editor} page={props.page}/></section>
    {showing&&<>{!narrow&&<div className="gs-editor-assistant-resizer" role="separator" aria-label="调整 AI 小窗宽度" aria-orientation="vertical" aria-valuemin={300} aria-valuemax={maximum} aria-valuenow={effectiveWidth} tabIndex={0}
      onPointerDown={event=>{if(event.button!==0)return;event.preventDefault();drag.current={id:event.pointerId,x:event.clientX,width:effectiveWidth};event.currentTarget.setPointerCapture?.(event.pointerId);}}
      onPointerMove={event=>{if(drag.current?.id===event.pointerId)resize(Math.min(maximum,drag.current.width+drag.current.x-event.clientX));}}
      onPointerUp={event=>{drag.current=undefined;event.currentTarget.releasePointerCapture?.(event.pointerId);}} onPointerCancel={()=>{drag.current=undefined;}}
      onKeyDown={event=>{if(["ArrowLeft","ArrowRight","Home","End"].includes(event.key)){event.preventDefault();resize(event.key==="Home"?300:event.key==="End"?maximum:Math.min(maximum,effectiveWidth+(event.key==="ArrowLeft"?20:-20)));}}}/>}
      <aside className="gs-editor-assistant" aria-label="AI 助手小窗"><header><strong>AI 助手</strong><props.SessionProvider empty={()=>null}>{props.renderSlot("genshin.editor.assistant.actions",{})}</props.SessionProvider><Button size="sm" variant="ghost" onClick={()=>document.dispatchEvent(new Event("genshin-open-assistant"))}>完整页面</Button><Button size="sm" variant="ghost" aria-label="关闭 AI 小窗" onClick={toggle}>×</Button></header>
        <div className="gs-editor-assistant-content"><props.SessionProvider empty={()=>props.renderSlot("genshin.editor.assistant",{})}>{props.renderSlot("genshin.editor.assistant",{})}</props.SessionProvider></div>
      </aside></>}
  </div>;
}

function EditorIsland({editor, page}: {editor: AppEditorBridge; page: typeof PAGES[number][0]}) {
  const host = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const node = host.current!;
    node.append(editor.root);
    editor.root.hidden = false;
    editor.navigate(page);
    return () => { editor.root.hidden = true; document.getElementById("genshin-editor-parking")?.append(editor.root); };
  }, [editor, page]);
  return <div ref={host} className="gs-editor-host" style={{height: "100%", minHeight: 0, overflow: "hidden"}} />;
}

/** Business pages only. The DSH root, navigation, Chat and Theme remain upstream-owned. */
export function createAppEditorPlugin(editor: AppEditorBridge) {
  return {
    name: "genshin-editor-islands", inject: ["slots", "layout", "theme","uiWorkspace","sessions"],
    apply(ctx: Context) {
      // One-time legacy preference input; all ongoing preference and DOM work
      // goes through the upstream ThemeRuntime, never a parallel controller.
      try {
        if(!window.localStorage.getItem("genshin.theme.dsh-migrated.v1")){
          const preference=window.localStorage.getItem("genshin.theme.v1");
          if(preference&&["system","light","dark"].includes(preference)&&ctx.theme.getTheme().preference==="system")ctx.theme.setTheme(preference);
          window.localStorage.setItem("genshin.theme.dsh-migrated.v1","1");
        }
      } catch { /* Restricted storage leaves the DSH Host preference authoritative. */ }
      const sync = () => { const theme = ctx.theme.getTheme(); editor.setTheme(theme.preference, theme.active.colorScheme); };
      sync();
      ctx.on("theme/change", sync);
      ctx.slots.registerFactory({name:"genshin.editor.workbench",scope:"root",children:{"genshin.editor.assistant":{kind:"single",scope:"session-maybe"},"genshin.editor.assistant.actions":{kind:"single",scope:"session-maybe"}}},props=><EditorWorkbench editor={editor} props={props}/>);
      ctx.slots.inject("genshin.editor.assistant",()=>ctx.slots.register({name:"genshin.editor.assistant"},EmbeddedConversation));
      ctx.slots.inject("conversation.hero.options",()=>ctx.slots.register({name:"conversation.hero.options",id:"genshin-unstarted-conversation",order:300},(props:PropsRuntime<"conversation.hero.options">)=><UnstartedConversation ctx={ctx} props={props}/>));
      ctx.effect(() => {
        const change = (event: Event) => { const preference = (event as CustomEvent<{preference: string}>).detail?.preference;
          if (["system", "light", "dark"].includes(preference)) ctx.theme.setTheme(preference); };
        document.addEventListener("genshin-theme-request", change);
        return () => document.removeEventListener("genshin-theme-request", change);
      });
      for (const [page, title, icon] of PAGES) {
        const id = `genshin-${page}`;
        ctx.slots.inject("main", () => ctx.slots.register({name: "main", key: id}, (props:PropsRenderFactories) => props.renderFactorySlot("genshin.editor.workbench",{page})));
        ctx.slots.inject("sidebar.panellist", () => ctx.slots.register({name: "sidebar.panellist", id, order: 100 + PAGES.findIndex(row => row[0] === page), label: title}, () => <span aria-hidden="true">{icon}</span>));
      }
      ctx.slots.inject("sidebar.brand.name", () => ctx.slots.register({name: "sidebar.brand.name"}, () => <span>原神拉表</span>));
      ctx.effect(() => {
        const open = () => ctx.layout.selectPanel(null);
        document.addEventListener("genshin-open-assistant", open);
        const showEditor=(event:Event)=>{
          const detail=(event as CustomEvent<{page:string;onUnavailable?:()=>void}>).detail;
          if(!PAGES.some(row=>row[0]===detail?.page)){detail?.onUnavailable?.();return;}
          try{ctx.layout.selectPanel(`genshin-${detail.page}` as Parameters<Context["layout"]["selectPanel"]>[0]);}
          catch{detail.onUnavailable?.();}
        };
        document.addEventListener("genshin-open-editor",showEditor);
        return () => {document.removeEventListener("genshin-open-assistant", open);document.removeEventListener("genshin-open-editor",showEditor);};
      });
    },
  };
}
