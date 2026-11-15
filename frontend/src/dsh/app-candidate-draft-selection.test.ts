import {describe,expect,it,vi} from "vitest";
import {createCandidateDraftFilePort} from "./app-candidate-draft-selection";

const sha="a".repeat(64),changedSha="b".repeat(64);
function fixture(){
  let count=0;
  const adapter={createSession:vi.fn(async()=>`session-${++count}`),sessionExists:vi.fn(()=>true),
    canUseUnstarted:vi.fn(()=>true),previewIsCurrent:vi.fn(async()=>true),
    importFile:vi.fn(async(sessionId:string,_path:string,fileSha:string)=>({imported:true,binding:{conversation_id:`candidate-${sessionId}`},preview:{compatible:true,message:"已打开输入草稿"},fileSha})),
    navigate:vi.fn()};
  return {adapter,port:createCandidateDraftFilePort(adapter)};
}
describe("same-file candidate preview navigation",()=>{
  it("does not import or navigate if the initiating document changed while creating a Session",async()=>{
    const {port,adapter}=fixture();let release!:(value:string)=>void;let current=true;
    adapter.createSession.mockImplementationOnce(()=>new Promise<string>(resolve=>{release=resolve;}));
    const opened=port.openSelected("input.json",sha,{isCurrent:()=>current});await Promise.resolve();current=false;release("created-before-change");
    expect((await opened).opened).toBe(false);expect(adapter.importFile).not.toHaveBeenCalled();expect(adapter.navigate).not.toHaveBeenCalled();
  });
  it("reports a completed isolated import honestly without late navigation",async()=>{
    const {port,adapter}=fixture();let release!:(value:any)=>void;let current=true;
    adapter.importFile.mockImplementationOnce(()=>new Promise(resolve=>{release=resolve;}));
    const opened=port.openSelected("input.json",sha,{isCurrent:()=>current});await Promise.resolve();await Promise.resolve();
    current=false;release({imported:true});const result=await opened;
    expect(result.opened).toBe(false);expect(result.message).toContain("已导入独立候选");expect(adapter.navigate).not.toHaveBeenCalled();expect(adapter.importFile).toHaveBeenCalledOnce();
  });
  it("focuses the existing valid SHA preview without another import or Session",async()=>{
    const {port,adapter}=fixture();await port.openSelected("first.json",sha);
    expect(await port.openSelected("same-content-elsewhere.json",sha)).toMatchObject({opened:true,reused:true});
    expect(adapter.createSession).toHaveBeenCalledOnce();expect(adapter.importFile).toHaveBeenCalledOnce();
    expect(adapter.navigate.mock.calls.map(([row])=>row.sessionId)).toEqual(["session-1","session-1"]);
  });
  it("serializes duplicate open gestures before creating the preview",async()=>{
    const {port,adapter}=fixture();await Promise.all([port.openSelected("first.json",sha),port.openSelected("first.json",sha)]);
    expect(adapter.createSession).toHaveBeenCalledOnce();expect(adapter.importFile).toHaveBeenCalledOnce();
  });
  it("treats changed SHA as a new independent input, never replacing the old one",async()=>{
    const {port,adapter}=fixture();await port.openSelected("input.json",sha);await port.openSelected("input.json",changedSha);
    expect(adapter.createSession).toHaveBeenCalledTimes(2);
    expect(adapter.importFile.mock.calls.map(([id,,hash])=>[id,hash])).toEqual([["session-1",sha],["session-2",changedSha]]);
  });
  it("supports an explicitly requested new copy with independent Session/control identity",async()=>{
    const {port,adapter}=fixture();await port.openSelected("input.json",sha);
    expect(await port.openSelected("input.json",sha,{newCopy:true,targetSessionId:"existing"})).toMatchObject({reused:false});
    expect(adapter.createSession).toHaveBeenCalledTimes(2);expect(adapter.previewIsCurrent).not.toHaveBeenCalled();
    expect(adapter.importFile.mock.calls[1]?.[0]).toBe("session-2");
  });
  it.each(["session","preview"])("does not focus a removed/archived or expired %s",async(kind)=>{
    const {port,adapter}=fixture();await port.openSelected("input.json",sha);
    if(kind==="session")adapter.sessionExists.mockReturnValue(false);else adapter.previewIsCurrent.mockResolvedValue(false);
    expect(await port.openSelected("input.json",sha)).toMatchObject({reused:false});
    expect(adapter.importFile).toHaveBeenCalledTimes(2);
  });
  it("uses an explicitly selected unused entry, and reuses its preview rather than filling a second entry",async()=>{
    const {port,adapter}=fixture();await port.openSelected("input.json",sha,{targetSessionId:"blank-one"});
    await port.openSelected("input.json",sha,{targetSessionId:"blank-two"});
    expect(adapter.createSession).not.toHaveBeenCalled();expect(adapter.importFile).toHaveBeenCalledOnce();
    expect(adapter.importFile.mock.calls[0]?.[0]).toBe("blank-one");
    expect(adapter.navigate.mock.calls.at(-1)?.[0].sessionId).toBe("blank-one");
  });
  it("does not hide an unknown validation failure by creating another task or retrying",async()=>{
    const {port,adapter}=fixture();await port.openSelected("input.json",sha);
    adapter.previewIsCurrent.mockRejectedValueOnce(new Error("unknown transport outcome"));
    await expect(port.openSelected("input.json",sha)).rejects.toThrow("unknown transport outcome");
    expect(adapter.importFile).toHaveBeenCalledOnce();expect(adapter.createSession).toHaveBeenCalledOnce();
  });
  it("does not turn an uncertain Session catalog into a new duplicate workspace",async()=>{
    const {port,adapter}=fixture();await port.openSelected("input.json",sha);
    adapter.sessionExists.mockImplementationOnce(()=>{throw new Error("session list still synchronizing");});
    await expect(port.openSelected("input.json",sha)).rejects.toThrow("still synchronizing");
    expect(adapter.createSession).toHaveBeenCalledOnce();expect(adapter.importFile).toHaveBeenCalledOnce();
  });
});
