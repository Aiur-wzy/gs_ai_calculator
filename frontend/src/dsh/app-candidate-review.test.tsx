import {act} from "react";
import {createRoot,type Root} from "react-dom/client";
import {afterEach,beforeEach,describe,expect,it,vi} from "vitest";
import {CandidateReviewControls} from "./app-candidate-review";
import type {AppCandidateGuiApi,AppDomainBinding,CandidateSummary} from "./app-domain-candidate";
import type {AppEditorBridge,AppEditorSnapshot} from "./app-editor-bridge";
let root:Root|undefined;
beforeEach(()=>vi.stubGlobal("IS_REACT_ACT_ENVIRONMENT",true));
afterEach(async()=>{if(root)await act(()=>root!.unmount());root=undefined;document.body.replaceChildren();vi.unstubAllGlobals();});
const receipt="b".repeat(64),summary:CandidateSummary={kind:"whiteboard",revision:2,receipt,counts:{characters:1,actions:1}};
const projected=(revision:number,commands:string[])=>({contract:"native-harness-projection-v3",projection_revision:revision,available_commands:commands.map(command_id=>({command_id,enabled:true,expected_projection_revision:revision}))});
async function fixture(enabled=true,calculated=false,show?:AppEditorBridge["show"]){
  const before:AppEditorSnapshot={kind:"whiteboard",document:{board_id:"b",team:[],actions:[]},applicationPayload:{kind:"whiteboard",board:{board_id:"b",team:[],actions:[]},calculated:null}};
  const board={board_id:"b",kind:"genshin-whiteboard",team:[{id:"a",name:"候选角色"}],actions:[{id:"a1",label:"候选动作"}]};
  const actual:AppEditorSnapshot={kind:"whiteboard",document:board,applicationPayload:{kind:"whiteboard",board,calculated:null}};
  const binding:AppDomainBinding={dshSessionId:"session",conversationId:"app",projectionRevision:1,availableCommands:enabled?[{commandId:"candidate.review",enabled:true}]:[]};
  const delivery=calculated?{delivery_capability:"fixture-capability",calculated:{result:{damage:123}}}:undefined;
  if(delivery)actual.applicationPayload.calculated=delivery.calculated;
  const call=vi.fn<NonNullable<AppCandidateGuiApi["dsh_client_candidate_command"]>>()
    .mockResolvedValueOnce({code:0,data:{result:{kind:"whiteboard",revision:2,receipt_fingerprint:receipt,board,...(delivery?{calculated_delivery:delivery}:{})},projection:projected(2,["candidate.apply.prepare"])}})
    .mockResolvedValueOnce({code:0,data:{result:{status:"prepared"},projection:projected(3,["application.ack"])}})
    .mockResolvedValueOnce({code:0,data:{result:{status:"applied",proof:{kind:"whiteboard",revision:2,receipt_fingerprint:receipt}},projection:projected(4,[])}});
  const save=vi.fn(async()=>({code:0,data:{cancelled:false,path:"user-chosen.calculated.json"}}));
  const api={agent_candidate:vi.fn(),agent_candidate_read:vi.fn(),dsh_client_candidate_command:call,dsh_client_save_whiteboard_result:save};
  const editor={root:document.createElement("div"),navigate:vi.fn(),show,document:()=>({document_kind:null,document:null}),setTheme:vi.fn(),application:{capture:()=>before,matches:()=>true,absence:()=>null,createDocument:()=>before,applyReviewed:vi.fn(async()=>actual)}} satisfies AppEditorBridge;
  const host=document.createElement("div");document.body.append(host);root=createRoot(host);await act(()=>root!.render(<CandidateReviewControls api={api} binding={binding} summary={summary} editor={editor}/>));
  return {host,call,editor,save};
}
describe("candidate review in the upstream Modal",()=>{
  it("opens a real review window and waits for user confirmation before Renderer application",async()=>{
    const show=vi.fn(async()=>{}),f=await fixture(true,false,show);await act(async()=>{f.host.querySelector("button")!.click();});
    const modal=document.querySelector('[role="dialog"]');expect(modal?.textContent).toContain("候选角色");expect(f.editor.application.applyReviewed).not.toHaveBeenCalled();
    expect(modal?.textContent).toContain("1 名角色 · 1 个动作");
    const body=modal?.querySelector(".gs-candidate-review-content"),footer=modal?.querySelector(".gs-candidate-review-actions");
    expect(body?.textContent).toContain("候选动作");expect(footer?.textContent).toContain("同意并同步");expect(body?.contains(footer??null)).toBe(false);
    expect(show).not.toHaveBeenCalled();
    await act(async()=>{[...document.querySelectorAll("button")].find(button=>button.textContent==="同意并同步")!.click();});
    expect(f.editor.application.applyReviewed).toHaveBeenCalledOnce();expect(document.querySelector('[role="dialog"]')).toBeNull();expect(f.host.textContent).toContain("已同步到正式白板，文件尚未保存");
    expect(f.call.mock.calls.map(call=>call[0].command_id)).toEqual(["candidate.review","candidate.apply.prepare","application.ack"]);
    expect(show).toHaveBeenCalledExactlyOnceWith("whiteboard");
    expect(show.mock.invocationCallOrder[0]).toBeGreaterThan(f.call.mock.invocationCallOrder[2]);
  });
  it("does not invent review permission from a readable candidate or a receipt",async()=>{
    const f=await fixture(false);expect(f.host.querySelector("button")).toBeNull();expect(f.call).not.toHaveBeenCalled();
  });
  it("allows an explicit decline without writing to the formal board",async()=>{
    const f=await fixture();await act(async()=>f.host.querySelector("button")!.click());
    await act(async()=>[...document.querySelectorAll("button")].find(button=>button.textContent==="不同意同步")!.click());
    expect(f.call.mock.calls.map(call=>call[0].command_id)).toEqual(["candidate.review"]);
    expect(f.editor.application.applyReviewed).not.toHaveBeenCalled();
    expect(document.querySelector('[role="dialog"]')).toBeNull();
    expect(f.host.textContent).toContain("本次未同意同步");
  });
  it("requests a user Save As dialog without sending a model-selected path",async()=>{
    const f=await fixture(true,true);await act(async()=>f.host.querySelector("button")!.click());
    await act(async()=>[...document.querySelectorAll("button")].find(button=>button.textContent==="同意并同步")!.click());
    expect(f.save).not.toHaveBeenCalled();await act(async()=>[...f.host.querySelectorAll("button")].find(button=>button.textContent==="计算结果另存为…")!.click());
    expect(f.save).toHaveBeenCalledExactlyOnceWith({conversation_id:"app",delivery_capability:"fixture-capability",expected_revision:2,expected_receipt:receipt});expect(f.host.textContent).toContain("user-chosen.calculated.json");
  });
});
