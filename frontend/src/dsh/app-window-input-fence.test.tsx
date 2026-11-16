import {describe,it,expect,vi} from "vitest";
import {useSyncExternalStore} from "react";
import {flushSync} from "react-dom";
import {createRoot} from "react-dom/client";
import type {ConversationController} from "@deepseek-ai/dsh-client-ui-conversation/client";
import {createWindowInputFence} from "./app-window-input-fence";

function fixture(){
  let value:{reason:string}|undefined;const listeners=new Set<()=>void>();
  const store={getSnapshot:()=>value,subscribe:(listener:()=>void)=>{listeners.add(listener);return()=>listeners.delete(listener);}};
  const blocks={storeFor:()=>store,set:(_id:unknown,next:typeof value)=>{value=next;for(const listener of listeners)listener();}} as unknown as ConversationController["blocks"];
  return {blocks,store,fence:createWindowInputFence(blocks,"session" as never,flushSync)};
}

describe("window source input fence",()=>{
  it("blocks edits and clicks immediately before asynchronous capture and until ACK",async()=>{
    const {store,fence}=fixture(),send=vi.fn(),edit=vi.fn();
    function Composer(){const block=useSyncExternalStore(store.subscribe,store.getSnapshot);return <><textarea aria-label="draft" disabled={!!block} onChange={edit}/><button disabled={!!block} onClick={send}>Send</button></>;}
    const view=document.createElement("div"),root=createRoot(view);document.body.append(view);flushSync(()=>root.render(<Composer/>));
    fence.begin();
    // Simulates immediate typing/clicking before Host open/return has resolved.
    expect(view.querySelector("textarea")!.disabled).toBe(true);
    view.querySelector("button")!.click();expect(send).not.toHaveBeenCalled();
    await Promise.resolve();expect(store.getSnapshot()).toBeDefined();
    fence.presentation(true,false);expect(store.getSnapshot()).toBeDefined();
    fence.presentation(true,true);expect(store.getSnapshot()).toBeDefined();
    fence.presentation(false,true);
    flushSync(()=>{});expect(store.getSnapshot()).toBeUndefined();flushSync(()=>root.unmount());view.remove();
  });
  it("restores editing on capture failure and retains an unrelated prior blocker",()=>{
    const {blocks,store,fence}=fixture();
    fence.begin();fence.release();expect(store.getSnapshot()).toBeUndefined();
    blocks.set("session" as never,{reason:"choose model"});fence.begin();fence.release();expect(store.getSnapshot()?.reason).toBe("choose model");
  });
  it("does not erase a later block from another plugin",()=>{
    const {blocks,store,fence}=fixture();fence.begin();
    blocks.set("session" as never,{reason:"permission revoked"});fence.release();expect(store.getSnapshot()?.reason).toBe("permission revoked");
  });
});
