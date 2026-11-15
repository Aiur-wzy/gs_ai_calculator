import {describe,it,expect,vi} from "vitest";
import {applyReviewedCandidate,recoverCandidate,reviewCandidate} from "./app-candidate-commands";
import type {AppCandidateGuiApi,AppDomainBinding,CandidateSummary} from "./app-domain-candidate";
import type {AppEditorApplicationPort,AppEditorSnapshot} from "./app-editor-bridge";
const receipt="a".repeat(64),summary:CandidateSummary={kind:"whiteboard",revision:3,receipt,counts:{characters:4,actions:20}};
function projection(revision:number,names:string[]){return {contract:"native-harness-projection-v3",projection_revision:revision,available_commands:names.map(command_id=>({command_id,enabled:true,expected_projection_revision:revision}))};}
function result(value:unknown,revision:number,names:string[]){return {code:0,data:{result:value,projection:projection(revision,names)}};}
function fixture(){
  const baseline:AppEditorSnapshot={kind:"whiteboard",document:{board_id:"board",revision:0},applicationPayload:{kind:"whiteboard",board:{board_id:"board",revision:0},calculated:null}};
  const actual:AppEditorSnapshot={kind:"whiteboard",document:{board_id:"board",revision:3},applicationPayload:{kind:"whiteboard",board:{board_id:"board",revision:3},calculated:null}};
  const binding:AppDomainBinding={dshSessionId:"session",conversationId:"conversation",projectionRevision:10,availableCommands:[{commandId:"candidate.review",enabled:true},{commandId:"candidate.review.new",enabled:true}]};
  const accepted={kind:"whiteboard",revision:3,receipt_fingerprint:receipt,board:actual.document};
  const call=vi.fn<NonNullable<AppCandidateGuiApi["dsh_client_candidate_command"]>>();
  call.mockResolvedValueOnce(result(accepted,11,["candidate.apply.prepare"]));
  const api={agent_candidate:vi.fn(),agent_candidate_read:vi.fn(),dsh_client_candidate_command:call};
  const editor={capture:vi.fn(()=>baseline),matches:vi.fn(()=>true),absence:vi.fn(()=>({stamp:"absent"})),createDocument:vi.fn(()=>baseline),applyReviewed:vi.fn(async()=>actual)} satisfies AppEditorApplicationPort;
  return {api,call,editor,binding,accepted,baseline,actual};
}
describe("Host-authorized candidate review and Renderer transaction",()=>{
  it.each(["kind","revision","receipt_fingerprint"])("rejects mismatched reviewed %s before creating or applying a document",async field=>{
    const f=fixture();f.call.mockReset().mockResolvedValueOnce(result({...f.accepted,[field]:"wrong"},11,["candidate.apply.prepare"]));
    await expect(reviewCandidate(f.api,f.editor,f.binding,summary)).rejects.toThrow("candidate_review_identity_mismatch");
    expect(f.editor.applyReviewed).not.toHaveBeenCalled();expect(f.editor.createDocument).not.toHaveBeenCalled();expect(f.call).toHaveBeenCalledOnce();
  });
  it("reviews only an explicitly enabled Host command and forwards exact CAS identities",async()=>{
    const f=fixture();await reviewCandidate(f.api,f.editor,f.binding,summary);
    expect(f.call).toHaveBeenCalledWith({conversation_id:"conversation",command_id:"candidate.review",expected_projection_revision:10,arguments:{kind:"whiteboard",document:f.baseline.document,expected_revision:3,expected_receipt:receipt}});
    await expect(reviewCandidate(f.api,f.editor,{...f.binding,availableCommands:[]},summary)).rejects.toThrow("command_unavailable");expect(f.call).toHaveBeenCalledOnce();
  });
  it("uses every returned projection token and ACKs actual Renderer payload",async()=>{
    const f=fixture(),review=await reviewCandidate(f.api,f.editor,f.binding,summary);
    f.call.mockResolvedValueOnce(result({status:"prepared"},12,["application.ack"])).mockResolvedValueOnce(result({status:"applied",proof:{kind:"whiteboard",revision:3,receipt_fingerprint:receipt}},13,[]));
    const completed=await applyReviewedCandidate(f.api,f.editor,review);
    expect(f.call.mock.calls[1]?.[0]).toMatchObject({command_id:"candidate.apply.prepare",expected_projection_revision:11,arguments:{document:f.actual.document,baseline_payload:f.baseline.applicationPayload,target_payload:review.target}});
    expect(f.call.mock.calls[2]?.[0]).toMatchObject({command_id:"application.ack",expected_projection_revision:12,arguments:{actual_payload:f.actual.applicationPayload}});
    expect(f.editor.applyReviewed).toHaveBeenCalledExactlyOnceWith(f.accepted,f.baseline,"conversation");expect(completed.current).toBe(true);
  });
  it("does not mutate Renderer or retry after an uncertain prepare error",async()=>{
    const f=fixture(),review=await reviewCandidate(f.api,f.editor,f.binding,summary);f.call.mockRejectedValueOnce(new Error("pipe_lost"));
    await expect(applyReviewedCandidate(f.api,f.editor,review)).rejects.toThrow("pipe_lost");expect(f.editor.applyReviewed).not.toHaveBeenCalled();expect(f.call).toHaveBeenCalledTimes(2);
  });
  it("stops if the formal document changes during prepare, leaving recovery to Host",async()=>{
    const f=fixture(),review=await reviewCandidate(f.api,f.editor,f.binding,summary);f.editor.matches.mockReturnValueOnce(true).mockReturnValueOnce(false);
    f.call.mockResolvedValueOnce(result({status:"prepared"},12,["application.ack"]));
    await expect(applyReviewedCandidate(f.api,f.editor,review)).rejects.toThrow("prepared_formal_document_changed");expect(f.editor.applyReviewed).not.toHaveBeenCalled();expect(f.call).toHaveBeenCalledTimes(2);
  });
  it("never repeats a successful Renderer side effect when ACK fails",async()=>{
    const f=fixture(),review=await reviewCandidate(f.api,f.editor,f.binding,summary);f.call.mockResolvedValueOnce(result({status:"prepared"},12,["application.ack"])).mockRejectedValueOnce(new Error("ack_lost"));
    await expect(applyReviewedCandidate(f.api,f.editor,review)).rejects.toThrow("ack_lost");expect(f.editor.applyReviewed).toHaveBeenCalledOnce();expect(f.call).toHaveBeenCalledTimes(3);
  });
  it("reviews an explicitly absent document without creating it until user confirms",async()=>{
    const f=fixture();f.editor.capture.mockReturnValue(null as unknown as AppEditorSnapshot);
    f.call.mockReset().mockResolvedValueOnce(result({...f.accepted,document_creation:{kind:"whiteboard",seed:f.baseline.document,expected_absent:true}},11,["candidate.apply.prepare"]));
    const review=await reviewCandidate(f.api,f.editor,f.binding,summary);expect(f.call.mock.calls[0]?.[0]).toMatchObject({command_id:"candidate.review.new",arguments:{document:null}});expect(f.editor.createDocument).not.toHaveBeenCalled();
    f.call.mockResolvedValueOnce(result({status:"prepared"},12,["application.ack"])).mockResolvedValueOnce(result({status:"applied",proof:{kind:"whiteboard",revision:3,receipt_fingerprint:receipt}},13,[]));
    await applyReviewedCandidate(f.api,f.editor,review);expect(f.editor.createDocument).toHaveBeenCalledExactlyOnceWith("whiteboard",f.baseline.document,{stamp:"absent"});
    expect(f.editor.createDocument.mock.invocationCallOrder[0]).toBeLessThan(f.call.mock.invocationCallOrder[1]!);
  });
  it("recovery is explicit, never discards current changes, and reuses the prepared intent",async()=>{
    const f=fixture(),binding={...f.binding,availableCommands:[{commandId:"application.recover",enabled:true}]};f.call.mockReset().mockResolvedValueOnce(result(f.accepted,12,["application.ack"]));
    const review=await recoverCandidate(f.api,f.editor,binding,summary);expect(f.call.mock.calls[0]?.[0]).toMatchObject({command_id:"application.recover",arguments:{discard_current:false,current_payload:f.baseline.applicationPayload}});
    f.call.mockResolvedValueOnce(result({status:"applied",proof:{kind:"whiteboard",revision:3,receipt_fingerprint:receipt}},13,[]));await applyReviewedCandidate(f.api,f.editor,review);expect(f.call.mock.calls.map(call=>call[0].command_id)).toEqual(["application.recover","application.ack"]);
  });
});
