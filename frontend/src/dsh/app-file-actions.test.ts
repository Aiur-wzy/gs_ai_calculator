import {describe,expect,it,vi} from "vitest";
import {AppActionExecutionPending,AppActionSettlementPending,executeAppAction,parseAppActions,recoverAppAction,settleAppAction,type AppAction} from "./app-file-actions";
import type {AppDomainBinding} from "./app-domain-candidate";
const binding:AppDomainBinding={dshSessionId:"session-one",conversationId:"conversation-one",projectionRevision:1,availableCommands:[]};
const receipt="ui_"+"a".repeat(43),card:AppAction={action_id:"action-one",action:"save_as",status:"queued",reason:"保存当前正式白板",suggested_name:"测试.whiteboard.json",ui_receipt:null};
const workspace={document_kind:"whiteboard",display_name:"测试白板",current_view:"whiteboard",dirty:true,has_path:false,can_save:true,can_open:true,can_export:false};
function fixture(){
  const files={capture:vi.fn(()=>({currentDocumentId:"board-one",workspace})),matches:vi.fn(()=>true),execute:vi.fn(async()=>({status:"completed" as const,message:"已保存",dirty_open_guard_passed:false}))};
  const api={agent_candidate:vi.fn(),agent_candidate_read:vi.fn(),dsh_client_workspace_state:vi.fn(async()=>({code:0,data:{status:"updated"}})),dsh_client_app_action:vi.fn(async(request:{command:string;arguments:Record<string,unknown>})=>{
    if(request.command==="present")return {code:0,data:{status:"presented_for_user",card:{...card,status:"presented",ui_receipt:receipt}}};
    if(request.command==="claim")return {code:0,data:{status:"claimed_for_external_execution",card:{...card,status:"presented",ui_receipt:receipt},request:{action_id:card.action_id,action:card.action,reason:card.reason,suggested_name:card.suggested_name,document_kind:workspace.document_kind,display_name:workspace.display_name,current_view:workspace.current_view,dirty_open_guard_required:false}}};
    return {code:0,data:{status:"completed",card:{...card,status:"completed",ui_receipt:receipt}}};
  })};return {api,files};
}
describe("App file-card imperative adapter",()=>{
  it("claims durably before the real file consumer, then settles and projects post-operation workspace",async()=>{
    const {api,files}=fixture();await executeAppAction(api,binding,files,card);
    expect(api.dsh_client_app_action.mock.calls.map(([request])=>request.command)).toEqual(["present","claim","settle"]);
    expect(api.dsh_client_app_action.mock.invocationCallOrder[1]).toBeLessThan(files.execute.mock.invocationCallOrder[0]!);
    expect(api.dsh_client_workspace_state).toHaveBeenCalledTimes(2);
    expect(files.execute).toHaveBeenCalledExactlyOnceWith(expect.objectContaining({action_id:card.action_id}),files.capture.mock.results[0]?.value,binding.conversationId,undefined);
  });
  it("does not invoke file UI after a changed document or failed claim",async()=>{
    const {api,files}=fixture();files.matches.mockReturnValueOnce(true).mockReturnValueOnce(false);
    await expect(executeAppAction(api,binding,files,card)).rejects.toThrow("app_action_document_changed");expect(files.execute).not.toHaveBeenCalled();
    const other=fixture();other.api.dsh_client_app_action.mockImplementation(async request=>request.command==="claim"?{code:1,reason:"claim_failed"} as never:{code:0,data:{status:"presented_for_user",card:{...card,status:"presented",ui_receipt:receipt}}});
    await expect(executeAppAction(other.api,binding,other.files,card)).rejects.toThrow("claim_failed");expect(other.files.execute).not.toHaveBeenCalled();
  });
  it("rejects Host request identity changes before executing any side effect",async()=>{
    const {api,files}=fixture(),normal=api.dsh_client_app_action.getMockImplementation()!;
    api.dsh_client_app_action.mockImplementation(async request=>{const response=await normal(request);if(request.command==="claim")response.data.request={...response.data.request,path:"model-selected-path.json"} as never;return response;});
    await executeAppAction(api,binding,files,card);expect(files.execute).not.toHaveBeenCalled();expect(api.dsh_client_app_action.mock.calls.at(-1)?.[0].arguments.outcome).toMatchObject({status:"failed"});
  });
  it("retries only settlement after a lost response, never the original file operation",async()=>{
    const {api,files}=fixture(),normal=api.dsh_client_app_action.getMockImplementation()!;let fail=true;
    api.dsh_client_app_action.mockImplementation(async request=>{if(request.command==="settle"&&fail){fail=false;throw new Error("response_lost");}return normal(request);});
    let pending:AppActionSettlementPending|undefined;try{await executeAppAction(api,binding,files,card);}catch(error){pending=error as AppActionSettlementPending;}
    expect(pending).toBeInstanceOf(AppActionSettlementPending);await settleAppAction(api,binding,pending!.action,pending!.receipt,pending!.outcome,files);
    expect(files.execute).toHaveBeenCalledOnce();expect(api.dsh_client_app_action.mock.calls.map(([request])=>request.command)).toEqual(["present","claim","settle","settle"]);
  });
  it("recovery never calls the Renderer consumer or creates a new claim",async()=>{
    const {api,files}=fixture();await recoverAppAction(api,binding,{...card,status:"claimed_for_external_execution",ui_receipt:receipt});expect(api.dsh_client_app_action).toHaveBeenCalledExactlyOnceWith({conversation_id:"conversation-one",command:"recover",arguments:{action_id:"action-one",ui_receipt:receipt}});expect(files.execute).not.toHaveBeenCalled();
  });
  it("preserves an unknown write claim instead of settling a lost file response as failed",async()=>{
    const {api,files}=fixture();files.execute.mockRejectedValueOnce(new Error("response_lost_after_write"));
    await expect(executeAppAction(api,binding,files,card)).rejects.toBeInstanceOf(AppActionExecutionPending);
    expect(files.execute).toHaveBeenCalledOnce();
    expect(api.dsh_client_app_action.mock.calls.map(([request])=>request.command)).toEqual(["present","claim"]);
  });
  it("refuses duplicate action identities",()=>{expect(()=>parseAppActions({code:0,data:{actions:[card,card]}})).toThrow("app_action_response_invalid");});
});
