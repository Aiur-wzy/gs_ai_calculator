import {describe,expect,it,vi} from "vitest";
import {restoreSavedWhiteboard} from "./app-saved-whiteboard";
import type {AppDomainBinding} from "./app-domain-candidate";
import type {AppEditorSnapshot} from "./app-editor-bridge";
const binding:AppDomainBinding={dshSessionId:"sid",conversationId:"cid",projectionRevision:4,availableCommands:[]};
const saved={board_id:"board",revision:3,receipt_fingerprint:"b".repeat(64),application_kind:"whiteboard_calculated",board:{board_id:"board",team:[],actions:[]},calculated:{result:{summary:{total_damage:123}}}};
const base:AppEditorSnapshot={kind:"whiteboard",document:saved.board,applicationPayload:{kind:"whiteboard",board:saved.board,calculated:null}};
function fixture(){const editor={capture:vi.fn(()=>null as AppEditorSnapshot|null),absence:vi.fn(()=>({epoch:2})),createDocument:vi.fn(()=>base),matches:vi.fn(()=>true),applyReviewed:vi.fn(async()=>base)};
  const restored={...saved,status:"restore_new_ready",document_creation:{kind:"whiteboard",seed:saved.board,expected_absent:true},calculated_delivery:{delivery_capability:"fresh",calculated:saved.calculated}};
  const api={agent_candidate:vi.fn(),agent_candidate_read:vi.fn(),dsh_client_saved_whiteboard:vi.fn(async()=>({code:0,data:restored})),dsh_client_whiteboard_projection:vi.fn(async(request:{command:string})=>({code:0,data:request.command==="query"?{status:"result_only",projection:{...saved}}:{...restored,status:"recovered"}}))};return {api,editor};}
describe("committed App whiteboard reopening",()=>{
  it("uses expected-absent Host evidence and the real Renderer create/apply port, without reaccepting the calculation",async()=>{
    const {api,editor}=fixture();await restoreSavedWhiteboard(api,binding,editor,saved);expect(api.dsh_client_saved_whiteboard).toHaveBeenCalledExactlyOnceWith({conversation_id:"cid",command:"restore_new",arguments:{expected_absent:true,expected_revision:3,expected_receipt:saved.receipt_fingerprint}});expect(editor.createDocument).toHaveBeenCalledExactlyOnceWith("whiteboard",saved.board,{epoch:2});expect(editor.applyReviewed).toHaveBeenCalledOnce();expect(api.dsh_client_whiteboard_projection).not.toHaveBeenCalled();
  });
  it("does not overwrite a document created while restore_new is in flight",async()=>{
    const {api,editor}=fixture();editor.createDocument.mockImplementation(()=>{throw new Error("absence_changed");});await expect(restoreSavedWhiteboard(api,binding,editor,saved)).rejects.toThrow("absence_changed");expect(editor.applyReviewed).not.toHaveBeenCalled();
  });
  it("queries the actual formal payload and only restores supported missing-result projections",async()=>{
    const {api,editor}=fixture();editor.capture.mockReturnValue(base);await restoreSavedWhiteboard(api,binding,editor,saved);expect(api.dsh_client_whiteboard_projection.mock.calls.map(([request])=>request.command)).toEqual(["query","recover"]);expect(api.dsh_client_whiteboard_projection.mock.calls[0]?.[0]).toMatchObject({arguments:{current_document:base.document,current_payload:base.applicationPayload}});expect(editor.createDocument).not.toHaveBeenCalled();expect(editor.applyReviewed).toHaveBeenCalledOnce();
  });
  it("refuses conflicts and does not automatically discard formal edits",async()=>{
    const {api,editor}=fixture();editor.capture.mockReturnValue(base);api.dsh_client_whiteboard_projection.mockResolvedValue({code:0,data:{status:"conflict",projection:saved}} as never);await expect(restoreSavedWhiteboard(api,binding,editor,saved)).rejects.toThrow("saved_whiteboard_formal_conflict");expect(editor.applyReviewed).not.toHaveBeenCalled();expect(api.dsh_client_whiteboard_projection).toHaveBeenCalledOnce();
  });
  it("does not reapply an already-current payload",async()=>{
    const {api,editor}=fixture();editor.capture.mockReturnValue(base);api.dsh_client_whiteboard_projection.mockResolvedValue({code:0,data:{status:"none",projection:saved}} as never);await restoreSavedWhiteboard(api,binding,editor,saved);expect(editor.applyReviewed).not.toHaveBeenCalled();
  });
  it("rejects stale or mismatched committed identity before Renderer writes",async()=>{
    const {api,editor}=fixture();api.dsh_client_saved_whiteboard.mockResolvedValue({code:0,data:{...saved,status:"restore_new_ready",revision:99,document_creation:{kind:"whiteboard",seed:saved.board,expected_absent:true}}} as never);await expect(restoreSavedWhiteboard(api,binding,editor,saved)).rejects.toThrow("saved_whiteboard_identity_invalid");expect(editor.createDocument).not.toHaveBeenCalled();
  });
});
