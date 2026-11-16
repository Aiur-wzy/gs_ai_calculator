import {describe,expect,it,vi} from "vitest";
import {createWhiteboardFiles} from "./whiteboard/files";
import {createWorkspaceFileActions} from "./user-file-actions";

const unknownWrite={code:1,reason:"delivery_publication_failed",message:"结果未核实",details:{published_paths:[],unknown_paths:["target.json"]}};
function boardFixture(response:any){
  const state={board:{title:"test",board_id:"one"},path:"old.json",fileSha:"old"};
  const owner:any={state,token:()=>({board:state.board}),current:(token:any)=>token.board===state.board,
    unappliedEditor:()=>false,json:()=>JSON.stringify(state.board),render:vi.fn(),leave:vi.fn(async()=>true)};
  const api=vi.fn(async(method:string)=>method==="dsh_client_file_dialog"?{code:0,data:{path:"target.json",cancelled:false}}:
    method==="inspect_plan_file"?{code:0,data:{kind:"genshin-whiteboard"}}:response);
  const status=vi.fn(),onResult=vi.fn();
  const files=createWhiteboardFiles({owner,ui:{clone:structuredClone,error:vi.fn(),tabs:vi.fn()},query:()=>null,api,status,
    services:{},plansDir:()=>".",document:{},events:{},confirm:()=>true,clipboard:()=>null});
  Object.assign(owner,files);return {files,api,onResult,status,owner};
}
function workspaceFixture({pickerError=false,response=unknownWrite,writeError=false,written=false}={}){
  const input={value:"old.json"},template={},onResult=vi.fn(),status=vi.fn();
  const board={state:{board:null},json:()=>"",pickUserFile:vi.fn(async()=>{if(pickerError)throw new Error("picker unavailable");return {path:"target.json",cancelled:false};})};
  const editor={state:{path:"old.json"},tpl:()=>template,saveTemplate:vi.fn(async(options:any)=>{
    if(written)options.onWritten();if(writeError)throw new Error("transport interrupted");options.onResponse?.(response);return false;
  })};
  const workspace={isBoard:()=>false,isPlan:()=>true,refresh:vi.fn()};
  const files=createWorkspaceFileActions({board,editor,workspace,api:async()=>({code:0}),query:()=>input,serialize:JSON.stringify,status,error:vi.fn()});
  return {files,board,editor,workspace,input,onResult,status};
}
describe("ordinary file menus keep failure and uncertain publication distinct",()=>{
  it.each([
    ["saveAs",unknownWrite,"unknown"],
    ["saveAs",{code:1,reason:"invalid_whiteboard",message:"Invalid"},"failed"],
    ["openFile",{code:1,reason:"invalid_whiteboard",message:"Invalid"},"failed"],
  ] as const)("whiteboard %s classifies its real response",async(mode,response,status)=>{
    const fixture=boardFixture(response);
    expect(await fixture.files[mode]({onAgentActionResult:fixture.onResult})).toBe(false);
    expect(fixture.onResult).toHaveBeenLastCalledWith(expect.objectContaining({status}));
    expect(fixture.status).toHaveBeenCalled();
  });
  it.each(["openFile","saveAs"] as const)("handles %s picker failure before any file operation",async method=>{
    const fixture=workspaceFixture({pickerError:true});
    await expect(fixture.files[method]({onAgentActionResult:fixture.onResult})).resolves.toBe(false);
    expect(fixture.editor.saveTemplate).not.toHaveBeenCalled();
    expect(fixture.onResult).toHaveBeenLastCalledWith(expect.objectContaining({status:"failed"}));
    expect(fixture.workspace.refresh).toHaveBeenCalledOnce();
  });
  it.each([{writeError:false},{writeError:true}])("keeps ambiguous plan writes unknown: %j",async options=>{
    const fixture=workspaceFixture(options);
    expect(await fixture.files.saveAs({onAgentActionResult:fixture.onResult})).toBe(false);
    expect(fixture.onResult).toHaveBeenLastCalledWith(expect.objectContaining({status:"unknown"}));
    expect(fixture.input.value).toBe("old.json");
  });
  it("does not label a verified write failed when subsequent UI work throws",async()=>{
    const fixture=workspaceFixture({written:true,writeError:true});
    expect(await fixture.files.saveAs({onAgentActionResult:fixture.onResult})).toBe(true);
    expect(fixture.onResult).toHaveBeenLastCalledWith(expect.objectContaining({status:"completed"}));
    expect(fixture.input.value).toBe("target.json");
  });
  it("only an explicit picker cancellation produces a cancelled outcome",async()=>{
    const fixture=boardFixture(unknownWrite);
    fixture.api.mockResolvedValue({code:0,data:{path:"",cancelled:true}});
    expect(await fixture.files.saveAs({onAgentActionResult:fixture.onResult})).toBe(false);
    expect(fixture.onResult).toHaveBeenLastCalledWith(expect.objectContaining({status:"cancelled"}));
    expect(fixture.api).toHaveBeenCalledTimes(1);
  });
  it("labels a missing save response unknown rather than claiming no write",async()=>{
    const f=workspaceFixture();f.editor.saveTemplate.mockImplementation(async()=>false);
    expect(await f.files.saveAs({onAgentActionResult:f.onResult})).toBe(false);
    expect(f.onResult).toHaveBeenLastCalledWith(expect.objectContaining({status:"unknown"}));
  });
  it("marks unapplied local whiteboard changes as a definite pre-write rejection",async()=>{
    const f=boardFixture(unknownWrite);f.owner.unappliedEditor=()=>true;
    expect(await f.files.saveAs({onAgentActionResult:f.onResult})).toBe(false);
    expect(f.onResult).toHaveBeenLastCalledWith(expect.objectContaining({status:"failed"}));
    expect(f.api).toHaveBeenCalledTimes(1);
  });
  it.each([unknownWrite,{code:1,message:"尚未保存",details:{publication_state:"not_written",published_paths:[],unknown_paths:[]}}])("keeps the applied-result save message consistent with its publication proof",async response=>{
    const f=boardFixture(response),owner=f.owner;
    owner.state.reusableResult={owner:"native_dsh",sessionId:"fixture"};owner.state.result={};
    owner.agentPayloadIdentity=()=>({});owner.agentPayloadMatches=()=>true;
    (window as any).AppAppliedResultSave=async()=>response;
    try{
      expect(await f.files.saveAppliedResult()).toBe(false);
      expect(f.status.mock.calls.at(-1)?.[0]).toContain(response===unknownWrite?"暂时无法确认结果是否已保存":"尚未保存");
    }finally{delete (window as any).AppAppliedResultSave;}
  });
});
