import {Context} from "@deepseek-ai/cordis";
import {act,type ReactNode} from "react";
import {createRoot} from "react-dom/client";
import {describe,it,expect,vi} from "vitest";
import {createWindowPresentationPlugin} from "./app-window-presentation";
import {createAppEditorPlugin} from "./app-editor-plugin";

// The shipped browser bundle has a module-loader factory. This test exercises
// Cordis injection; the unrelated visual phase helper is not used here.
vi.mock("@deepseek-ai/dsh-client-ui-conversation/client",()=>({conversationPhase:()=>"blank"}));

describe("actual Cordis scopes used by App slot callbacks",()=>{
  it("hero and active window actions share one continuously available controller",async()=>{
    const ctx=new Context(),slots=new Map<string,(props:unknown)=>ReactNode>();
    let block:unknown;const input={draftSnapshot:{text:"retain me",references:[]},state:{getSnapshot:()=>({phase:"plain",draftRev:1,attachmentIds:[]})}};
    const services={
      slots:{inject:(_name:unknown,callback:()=>unknown)=>callback(),register:(row:{name:string},component:(props:unknown)=>ReactNode)=>{slots.set(row.name,component);return()=>{};}},
      sidebarRightTabs:{register:()=>()=>{}},uiWorkspace:{openSession(){}},layout:{selectPanel(){}},
      workspaces:{list:{getSnapshot:()=>({state:"idle",archivedSessionIds:[]})}},
      sessions:{list:{getSnapshot:()=>({byId:{test:{retainedBy:{mainView:1}}}})},retain:()=>({ready:Promise.resolve(),binding:{ctx:{}},release(){}}),binding:()=>({ctx:{}})},
      conversation:{blocks:{storeFor:()=>({getSnapshot:()=>block}),set:(_id:unknown,value:unknown)=>{block=value;}},input:{for:()=>input},resolveDraftAttachments:()=>[]},
    };
    for(const [key,value] of Object.entries(services))ctx.provide(key as never,value as never);
    const opened=vi.fn(async()=>{throw new Error("fixture refuses window; source must stay editable");});
    const state={role:"main",generation:0,presentation:"main",handoff:null};
    vi.stubGlobal("pywebview",{api:{dsh_window_state:async()=>state,dsh_window_poll:async()=>({...state,commands:[]}),dsh_window_open:opened}});
    const plugin=await ctx.plugin(createWindowPresentationPlugin());
    vi.stubGlobal("IS_REACT_ACT_ENVIRONMENT",true);
    const element=document.createElement("div"),root=createRoot(element);document.body.append(element);
    try{
      await act(async()=>root.render(slots.get("conversation.hero.options")!({sessionId:"test",inputActions:{persistDraft(){}}})));
      const button=[...element.querySelectorAll("button")].find(item=>item.textContent==="独立窗口")!;
      expect(button).toBeDefined();await act(async()=>button.click());
      expect(opened).toHaveBeenCalledWith({session_id:"test",draft:{semantic:{text:"retain me",references:[]},files:[]},drafts:{test:{semantic:{text:"retain me",references:[]},files:[]}}});
      expect(window.GenshinWindowPresentation).toBeDefined();
      await act(async()=>root.render(null));
      expect((await window.GenshinWindowPresentation!.prepareExit()).unsent_count).toBe(1);
      await act(async()=>root.render(slots.get("conversation.composer.dock")!({sessionId:"test",inputActions:{persistDraft(){}}})));
      expect(block).toBeUndefined();
    }finally{await act(async()=>root.unmount());element.remove();await plugin.dispose();await ctx.fiber.dispose();vi.unstubAllGlobals();}
  });

  it("keeps recovery outside hidden conversation content even without a composer",async()=>{
    const ctx=new Context(),slots=new Map<string,(props:unknown)=>ReactNode>();
    const services={slots:{inject:(_name:unknown,run:()=>unknown)=>run(),register:(row:{name:string},component:(props:unknown)=>ReactNode)=>{slots.set(row.name,component);return()=>{};}},sessions:{list:{getSnapshot:()=>({byId:{}})}},conversation:{},uiWorkspace:{},layout:{}};
    for(const [key,value] of Object.entries(services))ctx.provide(key as never,value as never);
    const state={role:"main",generation:1,presentation:"detached",handoff:null};
    const recover=vi.fn(async()=>({...state,presentation:"main"}));
    vi.stubGlobal("pywebview",{api:{dsh_window_state:async()=>state,dsh_window_poll:async()=>({...state,commands:[]}),dsh_window_recover:recover}});
    vi.stubGlobal("IS_REACT_ACT_ENVIRONMENT",true);
    const plugin=await ctx.plugin(createWindowPresentationPlugin()),view=document.createElement("div"),root=createRoot(view);document.body.append(view);
    try{
      await act(async()=>root.render(<><header>{slots.get("conversation.header.leading")!({})}</header><div data-conversation-content hidden/></>));
      const button=view.querySelector("header button")!;expect(button.textContent).toBe("收回聊天");expect(button.closest("[data-conversation-content]")).toBeNull();
      expect(window.GenshinWindowPresentation).toBeDefined();
      await act(async()=>button.dispatchEvent(new MouseEvent("click",{bubbles:true})));expect(recover).toHaveBeenCalledOnce();
    }finally{await act(async()=>root.unmount());view.remove();await plugin.dispose();await ctx.fiber.dispose();vi.unstubAllGlobals();}
  });

  it("editor callbacks receive the sessions service needed by remove-unstarted",async()=>{
    const ctx=new Context();const required=["slots","layout","theme","uiWorkspace","sessions"];
    for(const name of required)ctx.provide(name as never,{} as never);
    const editor=createAppEditorPlugin({} as never);const read:string[]=[];
    const plugin=await ctx.plugin({inject:editor.inject,apply(scoped:Context){
      for(const name of required){Reflect.get(scoped,name);read.push(name);}
    }});
    expect(read).toEqual(required);await plugin.dispose();await ctx.fiber.dispose();
  });
});
