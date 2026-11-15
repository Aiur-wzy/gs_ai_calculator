import {act} from "react";
import {createRoot,type Root} from "react-dom/client";
import {afterEach,beforeEach,describe,expect,it,vi} from "vitest";
import {OpenCandidateDraft} from "./app-candidate-draft-file";
import type {AppCandidateGuiApi} from "./app-domain-candidate";
import {selectedDraftNotice} from "./app-candidate-draft-selection";

let root:Root|undefined;
beforeEach(()=>vi.stubGlobal("IS_REACT_ACT_ENVIRONMENT",true));
afterEach(async()=>{if(root)await act(()=>root!.unmount());root=undefined;document.body.replaceChildren();delete window.GenshinCandidateDraftFiles;delete (window as any).AppDocumentOpenIntents;vi.unstubAllGlobals();});
async function mount(api:AppCandidateGuiApi,onImported=vi.fn()){
  const host=document.createElement("div");document.body.append(host);root=createRoot(host);
  await act(()=>root!.render(<OpenCandidateDraft api={api} sessionId="unstarted" onImported={onImported}/>));
  return {host,onImported};
}
async function click(text:string){const button=[...document.querySelectorAll("button")].find(node=>node.textContent===text);expect(button).toBeDefined();await act(async()=>{button!.click();await Promise.resolve();});}

describe("user-only candidate input file entry",()=>{
  it("drops an old preview and its delayed read when the DSH Session changes",async()=>{
    let resolve!:(value:any)=>void;
    const api={agent_candidate:vi.fn(),agent_candidate_read:vi.fn(),dsh_client_open_candidate_input_draft:vi.fn(async()=>({code:0,data:{preview:{compatible:false},preview_id:"old-a",migration_prompt:"A的旧草稿"}})),dsh_client_candidate_input_draft_preview_read:vi.fn(()=>new Promise<any>(done=>{resolve=done;}))};
    await mount(api);await click("打开候选输入草稿");await click("查看候选输入");
    await act(()=>root!.render(<OpenCandidateDraft api={api} sessionId="new-session"/>));
    expect(document.body.textContent).not.toContain("A的旧草稿");
    await act(async()=>{resolve({code:0,data:{resource:"candidate",pointer:"",offset:0,kind:"text",text:"A的延迟资料",next_offset:null}});await Promise.resolve();});
    expect(document.body.textContent).not.toContain("A的延迟资料");expect(document.querySelector('[aria-label="旧草稿输入内容"]')).toBeNull();
  });
  it.each(["fallback","notice"])("clears the previous incompatible preview on a new compatible %s import",async(mode)=>{
    let resolve!:(value:any)=>void;let opens=0;
    const api={agent_candidate:vi.fn(),agent_candidate_read:vi.fn(),dsh_client_open_candidate_input_draft:vi.fn(async()=>({code:0,data:++opens===1?{preview:{compatible:false},preview_id:"old",migration_prompt:"旧版不能留在新副本上"}:{preview:{compatible:true,message:"新版已导入"}}})),dsh_client_candidate_input_draft_preview_read:vi.fn(()=>new Promise<any>(done=>{resolve=done;}))};
    await mount(api);await click("打开候选输入草稿");await click("查看候选输入");
    if(mode==="fallback")await click("打开候选输入草稿");
    else await act(()=>selectedDraftNotice.publish("unstarted",{preview:{compatible:true,message:"新版已导入"}}));
    await act(async()=>{resolve({code:0,data:{resource:"candidate",pointer:"",offset:0,kind:"text",text:"过时的详情",next_offset:null}});await Promise.resolve();});
    expect(document.body.textContent).not.toContain("旧版不能留在新副本上");expect(document.body.textContent).not.toContain("过时的详情");
  });
  it("uses the shared selected-file navigator for normal open and explicit new copy",async()=>{
    const openSelected=vi.fn(async()=>({opened:true,reused:true,message:"已切换到已有预览"}));
    window.GenshinCandidateDraftFiles={openSelected};
    const api={agent_candidate:vi.fn(),agent_candidate_read:vi.fn(),dsh_client_open_candidate_input_draft:vi.fn(),
      dsh_client_file_dialog:vi.fn(async()=>({code:0,data:{cancelled:false,path:"user.json",file_sha:"a".repeat(64)}}))};
    await mount(api);await click("打开候选输入草稿");
    expect(openSelected).toHaveBeenLastCalledWith("user.json","a".repeat(64),{newCopy:false,targetSessionId:"unstarted",isCurrent:expect.any(Function)});
    await click("新开独立副本");expect(openSelected).toHaveBeenLastCalledWith("user.json","a".repeat(64),{newCopy:true,targetSessionId:"unstarted",isCurrent:expect.any(Function)});
    expect(api.dsh_client_open_candidate_input_draft).not.toHaveBeenCalled();
  });
  it("retains the initiating editor fence across the native candidate picker",async()=>{
    let current=true,resolve!:(value:any)=>void;(window as any).AppDocumentOpenIntents={begin:()=>({current:()=>current})};
    const openSelected=vi.fn();window.GenshinCandidateDraftFiles={openSelected};
    const api={agent_candidate:vi.fn(),agent_candidate_read:vi.fn(),dsh_client_open_candidate_input_draft:vi.fn(),dsh_client_file_dialog:vi.fn(()=>new Promise<any>(done=>{resolve=done;}))};
    const {host,onImported}=await mount(api);await click("打开候选输入草稿");current=false;
    await act(async()=>{resolve({code:0,data:{cancelled:false,path:"input.json",file_sha:"a".repeat(64)}});await Promise.resolve();});
    expect(openSelected).not.toHaveBeenCalled();expect(onImported).not.toHaveBeenCalled();expect(host.textContent).toContain("当前输入已变化");
  });
  it("opens only after a click and a cancelled picker starts no task",async()=>{
    const api={agent_candidate:vi.fn(),agent_candidate_read:vi.fn(),dsh_client_open_candidate_input_draft:vi.fn(async()=>({code:0,data:{cancelled:true}}))};
    const {onImported}=await mount(api);expect(api.dsh_client_open_candidate_input_draft).not.toHaveBeenCalled();
    await click("打开候选输入草稿");expect(api.dsh_client_open_candidate_input_draft).toHaveBeenCalledExactlyOnceWith({dsh_session_id:"unstarted"});
    expect(onImported).not.toHaveBeenCalled();expect(api.agent_candidate).not.toHaveBeenCalled();
  });
  it("notifies candidate refresh after input import without submitting a model message",async()=>{
    const api={agent_candidate:vi.fn(),agent_candidate_read:vi.fn(),dsh_client_open_candidate_input_draft:vi.fn(async()=>({code:0,data:{cancelled:false,imported:true,preview:{compatible:true,message:"输入草稿已打开，尚未开始 AI 任务。"}}}))};
    const {host,onImported}=await mount(api);await click("打开候选输入草稿");
    expect(onImported).toHaveBeenCalledOnce();expect(host.textContent).toContain("尚未开始 AI 任务");expect(api.agent_candidate).not.toHaveBeenCalled();
  });
  it("lets the user drill into a large read-only input and move forward/back through all pages",async()=>{
    const read=vi.fn(async(request:{resource:string;pointer:string;offset:number})=>({code:0,data:{resource:request.resource,pointer:request.pointer,offset:request.offset,
      ...(request.pointer===""?{kind:"object",total:1,items:[{key:"payload",value:{reference:{resource:request.resource,pointer:"/payload"},kind:"object",length:22}}],next_offset:null}:
        {kind:"object",total:22,items:Array.from({length:request.offset===0?20:2},(_,index)=>({key:"field"+(request.offset+index),value:request.offset+index})),next_offset:request.offset===0?20:null})}}));
    const api={agent_candidate:vi.fn(),agent_candidate_read:vi.fn(),dsh_client_open_candidate_input_draft:vi.fn(async()=>({code:0,data:{cancelled:false,preview:{compatible:false,message:"旧版仅供查看"},preview_id:"draftpreview_exact",migration_prompt:"请重新整理输入，不沿用旧批准"}})),dsh_client_candidate_input_draft_preview_read:read};
    const {onImported}=await mount(api);await click("打开候选输入草稿");expect(onImported).not.toHaveBeenCalled();
    await click("查看候选输入");await click("查看内容");
    expect(read.mock.calls.at(-1)?.[0]).toMatchObject({pointer:"/payload",offset:0});
    await click("下一页");expect(read.mock.calls.at(-1)?.[0]).toMatchObject({pointer:"/payload",offset:20});
    expect(document.querySelector('[aria-label="旧草稿输入内容"]')?.textContent).toContain("field21");
    await click("上一页");expect(read.mock.calls.at(-1)?.[0]).toMatchObject({pointer:"/payload",offset:0});
    await click("全部内容");expect(read.mock.calls.at(-1)?.[0]).toMatchObject({pointer:"",offset:0});
    expect(document.querySelector("input")).toBeNull();
  });
});
