import {describe,it,expect,vi} from "vitest";
import type {Context} from "@deepseek-ai/cordis";
import {createWindowSessionController,selectedWindowSession} from "./app-window-session-controller";

function fixture(){
  let selected="a";
  const drafts=new Map<string,{text:string;references:never[]}>([["a",{text:"A draft",references:[]}],["b",{text:"B draft",references:[]}]]);
  const blocks=new Map(),released:string[]=[],persists:string[]=[];
  const inputs=new Map([...drafts.keys()].map(id=>[id,{
    get draftSnapshot(){return drafts.get(id)!;},
    state:{getSnapshot:()=>({draftRev:1,phase:"plain",attachmentIds:[]}),subscribe:()=>()=>{}},
    setDraft:(value:{text:string;references:never[]})=>drafts.set(id,value),addAttachments:()=>true,
  }]));
  const ctx={sessions:{
    list:{getSnapshot:()=>({byId:Object.fromEntries([...drafts.keys()].map(id=>[id,{retainedBy:{mainView:id===selected?1:0}}]))})},
    retain:(id:string)=>({ready:Promise.resolve(),binding:{ctx:{id}},release:()=>released.push(id)}),
    binding:(id:string)=>({ctx:{id}}),
  },conversation:{input:{for:({id}:{id:string})=>inputs.get(id)},blocks:{storeFor:(id:string)=>({getSnapshot:()=>blocks.get(id)}),set:(id:string,value:unknown)=>blocks.set(id,value)},resolveDraftAttachments:()=>[],createDrafts:()=>[],releaseDraftAttachment:vi.fn()},uiWorkspace:{openSession:(id:string)=>{selected=id;}}} as unknown as Context;
  const controller=createWindowSessionController(ctx,write=>write());
  return {ctx,controller,drafts,blocks,released,persists,inputs,select:(id:string)=>{selected=id;},register:(id:string)=>controller.register(id as never,{persistDraft:()=>persists.push(id)})};
}

describe("single window controller across hero, active and session changes",()=>{
  it("returns the same session to its original business page without revealing full chat",async()=>{
    const f=fixture();f.register("a");let panel="genshin-whiteboard";
    const open=vi.spyOn(f.ctx.uiWorkspace,"openSession").mockImplementation(()=>{panel="chat";});
    await f.controller.adopt({session_id:"a",draft:{semantic:{text:"draft from child",references:[]},files:[]}},{preserveSelectedView:true});
    expect(f.drafts.get("a")!.text).toBe("draft from child");
    expect(panel).toBe("genshin-whiteboard");expect(open).not.toHaveBeenCalled();
  });
  it("still selects a different returning session and reveals a child session normally",async()=>{
    const main=fixture(),openMain=vi.spyOn(main.ctx.uiWorkspace,"openSession");
    await main.controller.adopt({session_id:"b"},{preserveSelectedView:true});
    expect(openMain).toHaveBeenCalledWith("b");expect(selectedWindowSession(main.ctx)).toBe("b");
    const child=fixture(),openChild=vi.spyOn(child.ctx.uiWorkspace,"openSession");
    await child.controller.adopt({session_id:"a"});
    expect(openChild).toHaveBeenCalledWith("a");
  });
  it("retains every unsent draft across slot unmounts and returns all touched sessions",async()=>{
    const f=fixture(),unmount=f.register("a");unmount();f.select("b");f.register("b");
    expect(await f.controller.prepareExit()).toEqual({unsent_count:2,sending:false,attachments:0});
    const handoff=await f.controller.capture();expect(handoff.session_id).toBe("b");
    expect(handoff.drafts!.a.semantic.text).toBe("A draft");expect(handoff.drafts!.b.semantic.text).toBe("B draft");
    expect(f.blocks.get("a")).toBeDefined();expect(f.blocks.get("b")).toBeDefined();
    f.controller.releaseCapture();expect(f.blocks.get("a")).toBeUndefined();f.controller.dispose();expect(f.released.sort()).toEqual(["a","b"]);
  });
  it("does not replace a child's existing draft with the main window's stale seed",async()=>{
    const f=fixture();f.register("a");
    await f.controller.adopt({session_id:"a",draft:{semantic:{text:"stale main",references:[]},files:[]}},{seedOnly:true});
    expect(f.drafts.get("a")!.text).toBe("A draft");
    await f.controller.adopt({session_id:"b",draft:{semantic:{text:"new main seed",references:[]},files:[]}},{seedOnly:true});
    expect(f.drafts.get("b")!.text).toBe("new main seed");expect(selectedWindowSession(f.ctx)).toBe("b");
  });
  it("restores a cleared draft too, so returning cannot revive the old main text",async()=>{
    const f=fixture();f.register("a");
    await f.controller.adopt({session_id:"b",drafts:{a:{semantic:{text:"",references:[]},files:[]},b:{semantic:{text:"latest B",references:[]},files:[]}}});
    expect(f.drafts.get("a")!.text).toBe("");expect(f.drafts.get("b")!.text).toBe("latest B");
  });
  it("retries an unsuccessful seed instead of treating its retained session as restored",async()=>{
    const f=fixture(),input=f.inputs.get("b")!,original=input.addAttachments;
    input.addAttachments=vi.fn().mockReturnValueOnce(false).mockImplementation(original);
    const seed={session_id:"b",draft:{semantic:{text:"must arrive",references:[]},files:[]}};
    await expect(f.controller.adopt(seed,{seedOnly:true})).rejects.toThrow("附件");
    expect(f.drafts.get("b")!.text).toBe("B draft");expect(selectedWindowSession(f.ctx)).toBe("a");
    await f.controller.adopt(seed,{seedOnly:true});expect(f.drafts.get("b")!.text).toBe("must arrive");
  });
});
