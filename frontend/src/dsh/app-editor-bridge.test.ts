import {describe, expect, it, vi} from "vitest";
import {withDocumentBinding} from "./app-editor-bridge";
import type {ShellCarrierPort} from "./shell-carrier";

const request = {channel:"/api", endpoint:"session/prompt", payload:{args:{request:{sessionId:"exact-session",content:[]}}}};
function setup() {
  const unary = vi.fn(async () => ({ok:true as const,value:{accepted:true}}));
  const port: ShellCarrierPort = {unary,stream:vi.fn(),fetchBundle:vi.fn()};
  const api = {dsh_client_bind_document:vi.fn(async () => ({binding:{dsh_session_id:"exact-session",conversation_id:"exact-app-conversation"},existing:false}))};
  const editor = {document:vi.fn(() => ({document_kind:"whiteboard" as const,document:{team:[]}}))};
  return {unary,api,editor,bridge:withDocumentBinding(port,api,editor)};
}
describe("App document seed on the public DSH transport", () => {
  it("binds an exact Session before upstream prompt admission", async () => {
    const s=setup(); await s.bridge.unary(request);
    expect(s.api.dsh_client_bind_document).toHaveBeenCalledWith({dsh_session_id:"exact-session",document_kind:"whiteboard",document:{team:[]}});
    expect(s.api.dsh_client_bind_document.mock.invocationCallOrder[0]).toBeLessThan(s.unary.mock.invocationCallOrder[0]!);
  });
  it("passes existing Session preservation to Host instead of changing chat state", async () => {
    const s=setup();s.api.dsh_client_bind_document.mockResolvedValue({binding:{dsh_session_id:"exact-session",conversation_id:"exact-app-conversation"},existing:true});
    await s.bridge.unary(request);expect(s.unary).toHaveBeenCalledOnce();
  });
  it("does not bind reads, cold history, model changes or cancellation", async () => {
    const s=setup();await s.bridge.unary({...request,endpoint:"session/read"});expect(s.editor.document).not.toHaveBeenCalled();expect(s.unary).toHaveBeenCalledOnce();
  });
  it("refuses a wrong or missing binding receipt without sending a model prompt", async () => {
    const s=setup();s.api.dsh_client_bind_document.mockResolvedValue({binding:{dsh_session_id:"other",conversation_id:"exact-app-conversation"},existing:true});
    await expect(s.bridge.unary(request)).rejects.toThrow("receipt_invalid");expect(s.unary).not.toHaveBeenCalled();
  });
  it("does not dispatch after preflight failure", async () => {
    const s=setup();s.api.dsh_client_bind_document.mockRejectedValue(new Error("snapshot_unavailable"));
    await expect(s.bridge.unary(request)).rejects.toThrow("snapshot_unavailable");expect(s.unary).not.toHaveBeenCalled();
  });
  it("interrupts a pending seed binding immediately on user cancellation", async () => {
    const s=setup(),abort=new AbortController();let settle!: (value: never) => void;
    s.api.dsh_client_bind_document.mockImplementation(() => new Promise(resolve => {settle=resolve;}));
    const pending=s.bridge.unary(request,abort.signal);abort.abort();
    await expect(pending).rejects.toMatchObject({name:"AbortError"});expect(s.unary).not.toHaveBeenCalled();settle(undefined as never);
  });
  it("retains a typed domain failure and only permits explicitly unavailable App domains to continue ordinary chat",async()=>{
    const s=setup();s.api.dsh_client_bind_document.mockResolvedValue({code:1,reason:"agent_database_changed",message:"这份候选使用旧资料，请新建会话调整。",operation_may_have_completed:false} as never);
    await s.bridge.unary(request);expect(s.unary).toHaveBeenCalledOnce();
    s.api.dsh_client_bind_document.mockResolvedValue({code:1,reason:"dsh_workspace_binding_invalid",message:"文档绑定无效",operation_may_have_completed:false} as never);
    await expect(s.bridge.unary(request)).rejects.toMatchObject({name:"AppDomainHostError",code:"dsh_workspace_binding_invalid",message:"文档绑定无效"});expect(s.unary).toHaveBeenCalledOnce();
  });
  it("does not bypass an outcome-unknown domain operation even for a normally recoverable reason",async()=>{
    const s=setup();s.api.dsh_client_bind_document.mockResolvedValue({code:1,reason:"database_missing",message:"资料不可用，操作结果未知",operation_may_have_completed:true} as never);
    await expect(s.bridge.unary(request)).rejects.toMatchObject({code:"database_missing",operationMayHaveCompleted:true});expect(s.unary).not.toHaveBeenCalled();
  });
  it.each(["native_app_binding_compatibility_missing","native_app_binding_compatibility_changed"])("keeps %s limited to the App domain and sends the original prompt once",async reason=>{
    const s=setup(),workspace=vi.fn();const bridge=withDocumentBinding({unary:s.unary,stream:vi.fn(),fetchBundle:vi.fn()}, {...s.api,dsh_client_workspace_state:workspace},{...s.editor,files:{capture:vi.fn(),matches:vi.fn(),execute:vi.fn()}});
    s.api.dsh_client_bind_document.mockResolvedValue({code:1,reason,message:"候选与当前版本不兼容",details:{contract:"genshin-native-domain-incompatibility-v1"},operation_may_have_completed:false} as never);
    await bridge.unary(request);expect(s.unary).toHaveBeenCalledExactlyOnceWith(request,undefined);expect(workspace).not.toHaveBeenCalled();
  });
});
