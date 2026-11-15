import type {ShellCarrierPort, ShellUnaryRequest} from "./shell-carrier";
import {assertAppDomainSuccess,canContinueWithoutAppDomain} from "./app-domain-errors";

export type EditorDocument = Readonly<{document_kind: "whiteboard" | "native" | null; document: unknown}>;
export interface AppEditorBridge {
  readonly root: HTMLElement;
  navigate(page: "whiteboard" | "rotation" | "result" | "data"): void;
  show?(page: "whiteboard" | "rotation" | "result" | "data"): Promise<void>;
  document(): EditorDocument;
  setTheme(preference: string, resolved: string): void;
  projectCandidate?(document: Record<string,unknown>,kind:"native"|"whiteboard",calculated?:Record<string,unknown>):unknown;
  application?:AppEditorApplicationPort;
  files?:AppEditorFilePort;
}
export type AppEditorFileSnapshot=Readonly<{currentDocumentId:string|null;workspace:Record<string,unknown>;currentPayload?:Record<string,unknown>|null;[key:string]:unknown}>;
export type AppWorkspaceStateRequest=Readonly<{conversation_id:string;current_document_id:string|null;workspace:Record<string,unknown>;current_payload?:Record<string,unknown>|null}>;
export function appFileWorkspaceState(conversationId:string,snapshot:AppEditorFileSnapshot):AppWorkspaceStateRequest{
  return {conversation_id:conversationId,current_document_id:snapshot.currentDocumentId,workspace:snapshot.workspace,
    ...(snapshot.workspace.can_export?{current_payload:snapshot.currentPayload??null}:{})};
}
export type AppFileOutcome=Readonly<{status:"completed"|"cancelled"|"failed";message:string;dirty_open_guard_passed:boolean;read_only_open?:boolean}>;
export type AppFilePhase="choosing"|"writing";
export interface AppEditorFilePort {
  capture():AppEditorFileSnapshot;
  matches(snapshot:AppEditorFileSnapshot):boolean;
  execute(request:Record<string,unknown>,snapshot:AppEditorFileSnapshot,conversationId:string,onPhase?:(phase:AppFilePhase)=>void):Promise<AppFileOutcome>;
}
export type AppEditorSnapshot=Readonly<{kind:"native"|"whiteboard";document:Record<string,unknown>;applicationPayload:Record<string,unknown>;[key:string]:unknown}>;
export interface AppEditorApplicationPort {
  capture(kind:"native"|"whiteboard"):AppEditorSnapshot|null;
  validateSnapshot?(snapshot:AppEditorSnapshot):Promise<void>;
  matches(snapshot:AppEditorSnapshot):boolean;
  absence(kind:"native"|"whiteboard"):unknown;
  createDocument(kind:"native"|"whiteboard",seed:Record<string,unknown>,expectedAbsence:unknown):AppEditorSnapshot;
  applyReviewed(reviewed:Record<string,unknown>,expectedBase:AppEditorSnapshot,conversationId:string):Promise<AppEditorSnapshot>;
}
export interface DocumentBindingApi {
  dsh_client_bind_document(request: EditorDocument & {dsh_session_id: string}): Promise<unknown>;
  dsh_client_workspace_state?(request:AppWorkspaceStateRequest):Promise<unknown>;
}

async function bindDocument(api: DocumentBindingApi, sessionId: string, document: EditorDocument, signal?: AbortSignal): Promise<string> {
  const pending = api.dsh_client_bind_document({...document, dsh_session_id: sessionId});
  const value = await (signal ? new Promise<unknown>((resolve, reject) => {
    const abort = () => reject(signal.reason ?? new DOMException("Aborted", "AbortError"));
    signal.addEventListener("abort", abort, {once: true});
    pending.then(resolve, reject).finally(() => signal.removeEventListener("abort", abort));
    if (signal.aborted) abort();
  }) : pending);
  assertAppDomainSuccess(value);
  const row = value as {binding?: {dsh_session_id?: unknown; conversation_id?: unknown}} | null;
  if (row?.binding?.dsh_session_id !== sessionId || typeof row.binding.conversation_id !== "string" || !row.binding.conversation_id) {
    throw new Error("app_document_binding_receipt_invalid");
  }
  return row.binding.conversation_id;
}

/** Domain-only preflight on the public transport hook; DSH still owns prompt admission and echoes. */
export function withDocumentBinding(port: ShellCarrierPort, api: DocumentBindingApi, editor: Pick<AppEditorBridge, "document"> & Partial<Pick<AppEditorBridge,"files">>): ShellCarrierPort {
  return {
    async unary(request: ShellUnaryRequest, signal?: AbortSignal) {
      if (request.endpoint === "session/prompt") {
        const payload = request.payload as {args?:{request?: {sessionId?: unknown}}} | null;
        const sessionId = payload?.args?.request?.sessionId;
        if (typeof sessionId !== "string" || !sessionId) throw new Error("app_document_binding_session_required");
        signal?.throwIfAborted();
        // The Host is idempotent: existing sessions keep their original document.
        // This snapshot is never used to rebind a cold-restored conversation.
        let conversationId:string;
        try{conversationId=await bindDocument(api, sessionId, editor.document(), signal);}
        catch(reason){
          // A known unavailable App domain must not disable ordinary DSH
          // conversation/search. Node's exact resolver omits domain tools and
          // reports the cause; unknown identity/transport failures still stop.
          if(canContinueWithoutAppDomain(reason)){
            signal?.throwIfAborted();return port.unary(request,signal);
          }
          throw reason;
        }
        if(api.dsh_client_workspace_state&&editor.files){
          const current=editor.files.capture(),response=await api.dsh_client_workspace_state(appFileWorkspaceState(conversationId,current)) as {code?:unknown};
          if(response?.code!==0)throw new Error("app_workspace_state_not_accepted");
        }
        signal?.throwIfAborted();
      }
      return port.unary(request, signal);
    },
    stream: (request, signal) => port.stream(request, signal),
    fetchBundle: url => port.fetchBundle(url),
  };
}
