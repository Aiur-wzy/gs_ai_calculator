import {readFileSync} from "node:fs";
import vm from "node:vm";
import {resolve,join} from "node:path";
import {describe,expect,it,vi} from "vitest";
import {confirmedNoFileWrite} from "./file-write-outcome";

const web=resolve(__dirname,"../../../src/genshin_app/gui/web");
function fixture(response:unknown){
  const snapshot={name:"offline",reportHandle:"fixture-report"},result={},realm:any=vm.createContext({console});
  realm.window=realm;realm.UI={clone:structuredClone,error:()=>{}};realm.JSONTransport={};
  realm.GenshinBusinessUI={confirmedNoFileWrite};
  realm.WS={refresh(){},isPlan:()=>true,isBoard:()=>false};
  realm.WB={state:{},pickUserFile:async()=>({path:"fixture.calculated.json",file_sha:null})};
  realm.PL={state:{plan:{}}};realm.RT={};realm.setStatus=()=>{};
  realm.AgentDocumentAdapter={create:()=>({currentMatches:()=>true})};
  realm.callApi=async(method:string)=>{expect(method).toBe("export_viewed_report");return response;};
  vm.runInContext(readFileSync(join(web,"result.js"),"utf8"),realm);
  realm.RS.view=()=>({current:true,pending:false,kind:"editor",result,snapshot});
  realm.RS.sourceKind=()=>"editor";realm.RS.renderAll=()=>{};realm.RS.hasAppliedWhiteboardExport=()=>false;
  vm.runInContext(readFileSync(join(web,"app-file-actions.js"),"utf8"),realm);
  return {...realm.AppEditorFileActions.create({root:{},show:async()=>{}}),realm,snapshot,result};
}
describe("actual result export and file card agree about write uncertainty",()=>{
  it.each([
    {code:1,reason:"publication_failed",details:{published_paths:[],unknown_paths:["target.json"]}},
    {code:1,reason:"publication_failed",details:{published_paths:["target.json"],unknown_paths:[]}},
    {code:1,reason:"publication_failed",details:{publication_state:"written_unverified"}},
  ])("does not settle ambiguous publisher response as a retryable failure: %j",async response=>{
    await expect(fixture(response).execute({action:"export_result"},{kind:"native",base:{}})).rejects.toThrow("app_action_execution_outcome_unknown");
  });
  it("settles a proven validation failure without leaving an unknown write",async()=>{
    const outcome=await fixture({code:1,reason:"invalid_plan",details:{published_paths:[],unknown_paths:[]}})
      .execute({action:"export_result"},{kind:"native",base:{}});
    expect(outcome.status).toBe("failed");
  });
  it.each(["not-current","pending-export","zero-csv","external-csv","missing-handle"])("explicitly reports the %s preflight rejection before writing",async kind=>{
    const f=fixture({code:0,data:{path:"target.json"}}),outcome=vi.fn(),write=vi.fn();
    f.realm.callApi=write;
    if(kind==="not-current")f.realm.RS.view=()=>({current:false,pending:false,kind:"editor",result:f.result,snapshot:f.snapshot});
    if(kind==="pending-export")f.realm.RS.state.exportPending=true;
    if(kind==="zero-csv")f.realm.RS.isVerifiedZero=()=>true;
    if(kind==="external-csv")f.realm.RS.hasExternal=()=>true;
    if(kind==="missing-handle")delete (f.snapshot as any).reportHandle;
    expect(await f.realm.RS.exportResult(kind.includes("csv")?"csv":"json",outcome)).toBe(false);
    expect(outcome).toHaveBeenLastCalledWith("failed");expect(write).not.toHaveBeenCalled();
  });
  it.each(["throw","unavailable","cancelled","changed-view"])("distinguishes picker %s from an uncertain write",async kind=>{
    const f=fixture({code:0,data:{path:"target.json"}}),outcome=vi.fn(),write=vi.fn();f.realm.callApi=write;
    f.realm.WB.pickUserFile=async()=>{
      if(kind==="throw")throw Error("picker unavailable");if(kind==="unavailable")return null;
      if(kind==="changed-view")f.realm.RS.view=()=>({current:false});
      return {path:"target.json",cancelled:kind==="cancelled"};
    };
    expect(await f.realm.RS.exportResult("json",outcome)).toBe(false);
    expect(outcome).toHaveBeenLastCalledWith(kind==="cancelled"?"cancelled":"failed");expect(write).not.toHaveBeenCalled();
  });
  it("retains completed after a confirmed write even if feedback rendering fails",async()=>{
    const f=fixture({code:0,data:{path:"target.json"}}),outcome=vi.fn();
    f.realm.setStatus=(message:string)=>{if(message==="结果已导出")throw Error("render unavailable");};
    expect(await f.realm.RS.exportResult("json",outcome)).toBe(true);expect(outcome.mock.calls.map(([value])=>value)).toEqual(["completed","completed"]);
  });
});

describe("actual AppAppliedResultSave preserves the pre-publication boundary",()=>{
  function prepare(callApi:any){
    const source=readFileSync(join(web,"app.js"),"utf8"),start=source.indexOf("  window.AppAppliedResultSave="),end=source.indexOf("  window.GenshinEditorBridge=",start);
    expect(start).toBeGreaterThan(0);expect(end).toBeGreaterThan(start);
    const realm:any=vm.createContext({callApi});realm.window=realm;
    vm.runInContext(source.slice(start,end),realm);return realm.AppAppliedResultSave;
  }
  it.each(["read-rejected","read-threw","document-changed"])("proves no write after %s instead of creating an unknown publication",async condition=>{
    const call=vi.fn(async(_method:string)=>{if(condition==="read-threw")throw Error("read interrupted");return condition==="read-rejected"?{code:1,message:"not available"}:{code:0,data:{delivery_capability:"receipt"}};});
    const response=await prepare(call)("session",{}, {},()=>condition!=="document-changed");
    expect(call).toHaveBeenCalledOnce();expect(call.mock.calls[0][0]).toBe("dsh_client_saved_whiteboard");
    expect(confirmedNoFileWrite(response)).toBe(true);
  });
  it("does not rewrite an uncertain receipt once the actual publisher was called",async()=>{
    const unknown={code:1,details:{publication_state:"written_unverified",published_paths:[],unknown_paths:["target.json"]}};
    const call=vi.fn().mockResolvedValueOnce({code:0,data:{delivery_capability:"capability",revision:3,receipt_fingerprint:"receipt"}}).mockResolvedValueOnce(unknown);
    expect(await prepare(call)("session",{}, {},()=>true)).toBe(unknown);expect(call).toHaveBeenCalledTimes(2);expect(confirmedNoFileWrite(unknown)).toBe(false);
  });
});
