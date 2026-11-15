import {assertAppDomainSuccess} from "./app-domain-errors";
import {parseTaskReview, type TaskReview} from "./app-task-review";

/** Read-only App candidate contract; generic DSH conversation facts stay in DSH. */
export type AppDomainBinding = Readonly<{
  dshSessionId: string;
  conversationId: string;
  projectionRevision: number;
  availableCommands: readonly Readonly<{commandId: string; enabled: boolean}>[];
  applicationState?:"none"|"prepared"|"baseline_advanced"|"committed"|"aborted"|"outcome_unknown";
}>;

/** Host-owned exact bindings. Absence must never be replaced with title matching. */
export interface AppDomainBindings {
  getSnapshot(dshSessionId?: string): AppDomainBinding | undefined;
  subscribe(listener: () => void): () => void;
  /** Durable DSH changes invalidate App read models without copying messages. */
  subscribeInvalidation?(listener: (dshSessionId: string) => void): () => void;
  retain?(dshSessionId: string): () => void;
  getError?(dshSessionId?: string): Error | undefined;
}

export interface AppCandidateGuiApi {
  agent_candidate(conversationId: string): Promise<unknown>;
  agent_candidate_read(conversationId: string, resource: "candidate" | "baseline" | "calculated", pointer: string, offset: number, limit: number): Promise<unknown>;
  dsh_client_candidate_command?(request:{conversation_id:string;command_id:string;expected_projection_revision:number;arguments:Record<string,unknown>}):Promise<unknown>;
  dsh_client_save_whiteboard_result?(request:{conversation_id:string;delivery_capability:string;expected_revision:number;expected_receipt:string}):Promise<unknown>;
  dsh_client_domain_approvals?(request:{conversation_id:string}):Promise<unknown>;
  dsh_client_domain_approval_decide?(request:{conversation_id:string;question_id:string;expected_candidate_revision:number;expected_stage_revision:number;decision:"approve"|"reject"}):Promise<unknown>;
  dsh_client_workspace_state?(request:{conversation_id:string;current_document_id:string|null;workspace:Record<string,unknown>;current_payload?:Record<string,unknown>|null}):Promise<unknown>;
  dsh_client_app_actions?(request:{conversation_id:string}):Promise<unknown>;
  dsh_client_app_action?(request:{conversation_id:string;command:"present"|"claim"|"settle"|"recover";arguments:Record<string,unknown>}):Promise<unknown>;
  dsh_client_open_candidate_input_draft?(request:{dsh_session_id:string}):Promise<unknown>;
  dsh_client_file_dialog?(request:{action:"open";document_kind:"candidate_input_draft";suggested_name:string|null}):Promise<unknown>;
  dsh_client_import_candidate_input_draft?(request:{dsh_session_id:string;path:string;expected_file_sha:string}):Promise<unknown>;
  dsh_client_request_candidate_input_draft?(request:{conversation_id:string;expected_revision:number}):Promise<unknown>;
  dsh_client_candidate_input_draft_preview_read?(request:{preview_id:string;resource:"source_document"|"candidate"|"evidence";pointer:string;offset:number;limit:number}):Promise<unknown>;
  dsh_client_whiteboard_projection?(request:{conversation_id:string;command:"query"|"recover";arguments:Record<string,unknown>}):Promise<unknown>;
  dsh_client_saved_whiteboard?(request:{conversation_id:string;command:"inspect"|"restore_new"|"current_delivery";arguments:Record<string,unknown>}):Promise<unknown>;
  dsh_client_session_status?(request:{dsh_session_id:string}):Promise<unknown>;
  dsh_client_incompatible_applications?(request:Record<string,never>):Promise<unknown>;
  dsh_client_abandon_incompatible_application?(request:{conversation_id:string;expected_fingerprint:string;decision:"abandon_recovery"}):Promise<unknown>;
}

export type CandidateSummary = Readonly<{
  kind: "native" | "whiteboard";
  revision: number;
  receipt: string | null;
  resourceIdentities?:Readonly<Record<"candidate"|"baseline"|"calculated",string>>;
  hasCalculation?:boolean;
  readyScope?:"draft_sync"|"formal_result"|"none";
  representation?:"whiteboard"|"native";
  applicationKind?:string|null;
  assessment?:"draft"|"formal"|"diagnostic"|"unknown";
  taskReview?: TaskReview;
  execution?: Readonly<{
    composition: string | null;
    status: string | null;
    completeWindow: boolean | null;
    reportedHits: number | null;
    elemental: string | null;
    failure: Readonly<{
      actionId: string | null;
      time: number | null;
      before: number | null;
      required: number | null;
      deficit: number | null;
    }> | null;
  }>;
  counts: Readonly<{characters?: number; actions?: number; duration?: number}>;
}>;

export type CandidatePage = Readonly<{
  kind: "object" | "array" | "text" | "scalar";
  pointer: string;
  offset: number;
  total?: number;
  items: readonly Readonly<{key: string | number; value: unknown}>[];
  text?: string;
  value?: unknown;
  nextOffset: number | null;
  resourceIdentity?:string;
}>;

export class AppCandidateReadError extends Error {
  constructor(readonly code: string) {
    super(code === "candidate_changed"
      ? "Agent 已更新候选，请刷新后查看新版本。"
      : "暂时无法读取这份候选，请刷新后重试。");
    this.name = "AppCandidateReadError";
  }
}

function record(value: unknown): Record<string, unknown> {
  if (!value || typeof value !== "object" || Array.isArray(value)) throw new AppCandidateReadError("candidate_projection_invalid");
  return value as Record<string, unknown>;
}

function optionalRecord(value: unknown): Record<string, unknown> | null {
  return value && typeof value === "object" && !Array.isArray(value) ? value as Record<string, unknown> : null;
}

function executionSummary(value: unknown): CandidateSummary["execution"] {
  const feedback = optionalRecord(value);
  if (!feedback) return undefined;
  const failed = optionalRecord(optionalRecord(feedback.failure_summary)?.first_rejected_event);
  const numeric = (value: unknown) => typeof value === "number" && Number.isFinite(value) ? value : null;
  return {
    composition: typeof feedback.composition === "string" ? feedback.composition : null,
    status: typeof feedback.execution === "string" ? feedback.execution : null,
    completeWindow: typeof feedback.complete_input_window === "boolean" ? feedback.complete_input_window : null,
    reportedHits: numeric(feedback.reported_hits),
    elemental: typeof feedback.elemental === "string" ? feedback.elemental : null,
    failure: failed ? {
      actionId: typeof failed.action_id === "string" ? failed.action_id : null,
      time: numeric(failed.t),
      before: numeric(failed.before),
      required: numeric(failed.required),
      deficit: numeric(failed.deficit),
    } : null,
  };
}

function unwrap(value: unknown): Record<string, unknown> {
  assertAppDomainSuccess(value);
  const envelope = record(value);
  if (envelope.code !== 0) throw new AppCandidateReadError(typeof envelope.reason === "string" ? envelope.reason : "candidate_read_failed");
  return record(envelope.data);
}

function natural(value: unknown): number {
  if (!Number.isSafeInteger(value) || (value as number) < 0) throw new AppCandidateReadError("candidate_projection_invalid");
  return value as number;
}

function summary(value: unknown): CandidateSummary {
  const row = unwrap(value);
  if (row.kind !== "native" && row.kind !== "whiteboard") throw new AppCandidateReadError("candidate_projection_invalid");
  if (row.receipt_fingerprint !== null && (typeof row.receipt_fingerprint !== "string" || !/^[0-9a-f]{64}$/.test(row.receipt_fingerprint))) {
    throw new AppCandidateReadError("candidate_projection_invalid");
  }
  const rawCounts = row.summary == null ? {} : record(row.summary);
  const counts: {characters?: number; actions?: number; duration?: number} = {};
  for (const key of ["characters", "actions", "duration"] as const) {
    const value = rawCounts[key];
    if (typeof value === "number" && Number.isFinite(value) && value >= 0) counts[key] = value;
  }
  const ids=optionalRecord(row.resource_identities);
  const keys=["candidate","baseline","calculated"] as const;
  const resourceIdentities=ids&&keys.every(key=>typeof ids[key]==="string"&&/^[0-9a-f]{64}$/.test(ids[key] as string))?ids as CandidateSummary["resourceIdentities"]:undefined;
  return {kind: row.kind, revision: natural(row.revision), receipt: row.receipt_fingerprint,resourceIdentities,hasCalculation:row.has_calculation===true, counts,
    readyScope:["draft_sync","formal_result","none"].includes(String(row.ready_scope))?row.ready_scope as CandidateSummary["readyScope"]:undefined,
    representation:["whiteboard","native"].includes(String(row.representation))?row.representation as CandidateSummary["representation"]:undefined,
    applicationKind:typeof row.application_kind==="string"?row.application_kind:null,
    assessment:["draft","formal","diagnostic","unknown"].includes(String(row.assessment))?row.assessment as CandidateSummary["assessment"]:undefined,
    execution:executionSummary(row.execution_feedback),
    taskReview:parseTaskReview(row.task_review ?? optionalRecord(row.execution_feedback)?.task_review)};
}

export function parseInputPage(value: unknown, resource: string, pointer: string, offset: number): CandidatePage {
  const row = unwrap(value);
  if (row.resource !== resource || row.pointer !== pointer || row.offset !== offset) throw new AppCandidateReadError("candidate_page_mismatch");
  let nextOffset: number | null = null;
  if (row.next_offset !== undefined && row.next_offset !== null) {
    nextOffset = natural(row.next_offset);
    if (nextOffset <= offset) throw new AppCandidateReadError("candidate_page_mismatch");
  }
  const resourceIdentity=typeof row.resource_identity==="string"&&/^[0-9a-f]{64}$/.test(row.resource_identity)?row.resource_identity:undefined;
  const common = {pointer, offset, nextOffset, resourceIdentity};
  if (row.kind === "object" || row.kind === "array") {
    if (!Array.isArray(row.items)) throw new AppCandidateReadError("candidate_projection_invalid");
    const items = row.items.map(value => {
      const item = record(value);
      if ((typeof item.key !== "string" && typeof item.key !== "number") || !Object.hasOwn(item, "value")) {
        throw new AppCandidateReadError("candidate_projection_invalid");
      }
      return {key: item.key, value: item.value};
    });
    const total=natural(row.total);
    if(offset+items.length>total||(nextOffset!==null&&(nextOffset!==offset+items.length||nextOffset>=total))
      ||(row.kind==="array"&&items.some((item,index)=>item.key!==offset+index)))throw new AppCandidateReadError("candidate_page_mismatch");
    return {...common, kind: row.kind, total, items};
  }
  if (row.kind === "text" && typeof row.text === "string") {
    return {...common, kind: "text", text: row.text, total: natural(row.total), items: []};
  }
  if (row.kind === "scalar" && Object.hasOwn(row, "value")) return {...common, kind: "scalar", value: row.value, items: []};
  throw new AppCandidateReadError("candidate_projection_invalid");
}

/** Snapshot checks bind every page to the exact candidate revision, never a seal gate. */
export async function readCandidatePage(
  api: AppCandidateGuiApi,
  binding: AppDomainBinding,
  request: Readonly<{resource: "candidate" | "baseline"; pointer: string; offset: number}>,
  signal: AbortSignal,
): Promise<Readonly<{summary: CandidateSummary; page: CandidatePage}>> {
  signal.throwIfAborted();
  const before = summary(await api.agent_candidate(binding.conversationId));
  signal.throwIfAborted();
  const key=before.resourceIdentities?.[request.resource];
  const page = await loadResource(api,key&&JSON.stringify([binding.dshSessionId,binding.conversationId,key,request.pointer,request.offset]),async()=>parseInputPage(
    await api.agent_candidate_read(binding.conversationId, request.resource, request.pointer, request.offset, 50),request.resource, request.pointer, request.offset));
  signal.throwIfAborted();
  const after = summary(await api.agent_candidate(binding.conversationId));
  signal.throwIfAborted();
  if (!sameResource(before,after,request.resource)||(before.resourceIdentities&&page.resourceIdentity!==before.resourceIdentities[request.resource])) {
    throw new AppCandidateReadError("candidate_changed");
  }
  return {summary: after, page};
}

export function childPointer(pointer: string, key: string | number): string {
  return `${pointer}/${String(key).replaceAll("~", "~0").replaceAll("/", "~1")}`;
}

export function candidateReviewAvailable(binding: AppDomainBinding): boolean {
  return binding.availableCommands.some(command => ["candidate.review","candidate.review.new"].includes(command.commandId) && command.enabled === true);
}

function sameResource(a:CandidateSummary,b:CandidateSummary,resource:"candidate"|"baseline"|"calculated"){
  return a.resourceIdentities&&b.resourceIdentities?a.resourceIdentities[resource]===b.resourceIdentities[resource]:
    a.kind===b.kind&&a.revision===b.revision&&a.receipt===b.receipt;
}

type CachedResource={value:unknown;bytes:number};
const resourceCaches=new WeakMap<AppCandidateGuiApi,{pending:Map<string,Promise<unknown>>;complete:Map<string,CachedResource>;bytes:number}>();
const lastPreviewSummaries=new WeakMap<AppCandidateGuiApi,Map<string,CandidateSummary>>();
const previewCacheBytes=32*1024*1024;
const previewScope=(binding:AppDomainBinding)=>JSON.stringify([binding.dshSessionId,binding.conversationId]);
const previewKey=(binding:AppDomainBinding,resource:"candidate"|"baseline"|"calculated",summary:CandidateSummary)=>{
  const id=summary.resourceIdentities?.[resource];
  return id&&JSON.stringify([previewScope(binding),resource,id]);
};

export function peekCandidateDocument(api:AppCandidateGuiApi,binding:AppDomainBinding,resource:"candidate"|"baseline",includeCalculated:boolean){
  const previous=lastPreviewSummaries.get(api)?.get(previewScope(binding));
  if(!previous)return;
  const cache=resourceCaches.get(api)?.complete;
  const key=previewKey(binding,resource,previous);
  const document=key&&cache?.get(key)?.value;
  if(!document)return;
  const calculatedKey=resource==="candidate"&&includeCalculated&&previewKey(binding,"calculated",previous);
  const calculated=calculatedKey&&cache?.get(calculatedKey)?.value;
  return {summary:previous,document:record(document),calculated:calculated?record(calculated):undefined};
}
function loadResource<T>(api:AppCandidateGuiApi,key:string|undefined,read:()=>Promise<T>):Promise<T>{
  if(!key)return read();
  let cache=resourceCaches.get(api);if(!cache){cache={pending:new Map(),complete:new Map(),bytes:0};resourceCaches.set(api,cache);}
  const stored=cache.complete.get(key);
  if(stored){cache.complete.delete(key);cache.complete.set(key,stored);return Promise.resolve(stored.value as T);}
  const pending=cache.pending.get(key);if(pending)return pending as Promise<T>;
  const owned=cache;
  const load=read().then(value=>{
    const bytes=2*JSON.stringify(value).length;
    if(bytes<=previewCacheBytes){
      owned.complete.set(key,{value,bytes});owned.bytes+=bytes;
      for(const [old,entry] of owned.complete){if(owned.bytes<=previewCacheBytes&&owned.complete.size<=24)break;owned.complete.delete(old);owned.bytes-=entry.bytes;}
    }
    return value;
  }).finally(()=>owned.pending.delete(key));
  cache.pending.set(key,load);return load;
}

/** Read presentation inputs, following only exact Host-issued child references.
 * A single before/after revision check covers all pages; no model is called.
 */
export async function readCandidateDocument(api:AppCandidateGuiApi,binding:AppDomainBinding,resource:"candidate"|"baseline",signal:AbortSignal,includeCalculated=true) {
  const before=summary(await api.agent_candidate(binding.conversationId));signal.throwIfAborted();
  const materialize=async(pointer:string,targetResource:"candidate"|"baseline"|"calculated"=resource):Promise<unknown>=>{
    let offset=0,output:Record<string,unknown>|unknown[]|undefined,text="";
    while(true){
      const page=parseInputPage(await api.agent_candidate_read(binding.conversationId,targetResource,pointer,offset,50),targetResource,pointer,offset);
      if(before.resourceIdentities&&page.resourceIdentity!==before.resourceIdentities[targetResource])throw new AppCandidateReadError("candidate_changed");
      if(!before.resourceIdentities)signal.throwIfAborted();
      if(page.kind==="scalar")return page.value;
      if(page.kind==="text"){text+=page.text||"";if(page.nextOffset===null)return text;offset=page.nextOffset;continue;}
      output??=page.kind==="array"?[]:Object.create(null) as Record<string,unknown>;
      for(const item of page.items){
        let value=item.value;
        if(value&&typeof value==="object"&&!Array.isArray(value)){
          const row=value as {reference?:{resource?:unknown;pointer?:unknown};kind?:unknown};
          const child=childPointer(pointer,item.key);
          if(row.reference?.resource===targetResource&&row.reference.pointer===child&&["object","array","text"].includes(String(row.kind)))value=await materialize(child,targetResource);
        }
        Object.defineProperty(output,String(item.key),{value,writable:true,enumerable:true,configurable:true});
      }
      if(page.nextOffset===null)return output;
      offset=page.nextOffset;
    }
  };
  const load=(role:"candidate"|"baseline"|"calculated")=>loadResource(api,previewKey(binding,role,before),async()=>record(await materialize("",role)));
  const document=await load(resource);signal.throwIfAborted();
  const calculated=resource==="candidate"&&before.hasCalculation&&includeCalculated?await load("calculated"):undefined;signal.throwIfAborted();
  const after=summary(await api.agent_candidate(binding.conversationId));signal.throwIfAborted();
  if(!sameResource(before,after,resource)||(calculated&&!sameResource(before,after,"calculated")))throw new AppCandidateReadError("candidate_changed");
  if(after.resourceIdentities){
    let summaries=lastPreviewSummaries.get(api);
    if(!summaries){summaries=new Map();lastPreviewSummaries.set(api,summaries);}
    const key=previewScope(binding);
    summaries.delete(key);summaries.set(key,after);
    if(summaries.size>64)summaries.delete(summaries.keys().next().value!);
  }
  return {summary:after,document:record(document),calculated};
}
