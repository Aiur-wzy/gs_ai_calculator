import type {ISessions, SessionBinding} from "@deepseek-ai/dsh-api-session-controller/client";
import {notifySubscribers} from "@deepseek-ai/dsh-client-store";
import type {} from "@deepseek-ai/dsh-client-ui-session/client";
import {AppCandidateReadError, type AppCandidateGuiApi, type AppDomainBinding, type AppDomainBindings} from "./app-domain-candidate";
import {callAppDocumentApi} from "./app-document-transport";
import {AppDomainHostError,assertAppDomainSuccess} from "./app-domain-errors";

type SessionId = Parameters<ISessions["binding"]>[0];

export interface AppDomainGuiApi extends AppCandidateGuiApi {
  /** Bare transport result: no App code/data wrapper on this exact read seam. */
  dsh_client_domain_binding(request: {dsh_session_id: string}): Promise<unknown>;
}

/** New production domain endpoints never instantiate the retired V2 manager. */
export interface AppDomainHostApi {
  dsh_client_domain_binding(request: {dsh_session_id: string}): Promise<unknown>;
  dsh_client_candidate(request: {conversation_id: string}): Promise<unknown>;
  dsh_client_candidate_read(request: {conversation_id: string; resource: "candidate" | "baseline" | "calculated"; pointer: string; offset: number; limit: number}): Promise<unknown>;
  dsh_client_candidate_command?:AppCandidateGuiApi["dsh_client_candidate_command"];
  dsh_client_save_whiteboard_result?:AppCandidateGuiApi["dsh_client_save_whiteboard_result"];
  dsh_client_domain_approvals?:AppCandidateGuiApi["dsh_client_domain_approvals"];
  dsh_client_domain_approval_decide?:AppCandidateGuiApi["dsh_client_domain_approval_decide"];
  dsh_client_workspace_state?:AppCandidateGuiApi["dsh_client_workspace_state"];
  dsh_client_app_actions?:AppCandidateGuiApi["dsh_client_app_actions"];
  dsh_client_app_action?:AppCandidateGuiApi["dsh_client_app_action"];
  dsh_client_open_candidate_input_draft?:AppCandidateGuiApi["dsh_client_open_candidate_input_draft"];
  dsh_client_file_dialog?:AppCandidateGuiApi["dsh_client_file_dialog"];
  dsh_client_import_candidate_input_draft?:AppCandidateGuiApi["dsh_client_import_candidate_input_draft"];
  dsh_client_request_candidate_input_draft?:AppCandidateGuiApi["dsh_client_request_candidate_input_draft"];
  dsh_client_candidate_input_draft_preview_read?:AppCandidateGuiApi["dsh_client_candidate_input_draft_preview_read"];
  dsh_client_whiteboard_projection?:AppCandidateGuiApi["dsh_client_whiteboard_projection"];
  dsh_client_saved_whiteboard?:AppCandidateGuiApi["dsh_client_saved_whiteboard"];
  dsh_client_session_status?:AppCandidateGuiApi["dsh_client_session_status"];
  dsh_client_incompatible_applications?:AppCandidateGuiApi["dsh_client_incompatible_applications"];
  dsh_client_abandon_incompatible_application?:AppCandidateGuiApi["dsh_client_abandon_incompatible_application"];
}

export function createProductionAppDomainApi(host: AppDomainHostApi): AppDomainGuiApi {
  return {
    dsh_client_domain_binding: request => callAppDocumentApi(host,"dsh_client_domain_binding",[request]),
    agent_candidate: conversationId => callAppDocumentApi(host,"dsh_client_candidate",[{conversation_id: conversationId}]),
    agent_candidate_read: (conversationId, resource, pointer, offset, limit) => callAppDocumentApi(host,"dsh_client_candidate_read",[{conversation_id: conversationId, resource, pointer, offset, limit}]),
    ...(host.dsh_client_candidate_command?{dsh_client_candidate_command:request=>callAppDocumentApi(host,"dsh_client_candidate_command",[request])}:{}),
    ...(host.dsh_client_save_whiteboard_result?{dsh_client_save_whiteboard_result:request=>callAppDocumentApi(host,"dsh_client_save_whiteboard_result",[request])}:{}),
    ...(host.dsh_client_domain_approvals?{dsh_client_domain_approvals:request=>callAppDocumentApi(host,"dsh_client_domain_approvals",[request])}:{}),
    ...(host.dsh_client_domain_approval_decide?{dsh_client_domain_approval_decide:request=>callAppDocumentApi(host,"dsh_client_domain_approval_decide",[request])}:{}),
    ...(host.dsh_client_workspace_state?{dsh_client_workspace_state:request=>callAppDocumentApi(host,"dsh_client_workspace_state",[request])}:{}),
    ...(host.dsh_client_app_actions?{dsh_client_app_actions:request=>callAppDocumentApi(host,"dsh_client_app_actions",[request])}:{}),
    ...(host.dsh_client_app_action?{dsh_client_app_action:request=>callAppDocumentApi(host,"dsh_client_app_action",[request])}:{}),
    ...(host.dsh_client_open_candidate_input_draft?{dsh_client_open_candidate_input_draft:request=>callAppDocumentApi(host,"dsh_client_open_candidate_input_draft",[request])}:{}),
    ...(host.dsh_client_file_dialog?{dsh_client_file_dialog:request=>callAppDocumentApi(host,"dsh_client_file_dialog",[request])}:{}),
    ...(host.dsh_client_import_candidate_input_draft?{dsh_client_import_candidate_input_draft:request=>callAppDocumentApi(host,"dsh_client_import_candidate_input_draft",[request])}:{}),
    ...(host.dsh_client_request_candidate_input_draft?{dsh_client_request_candidate_input_draft:request=>callAppDocumentApi(host,"dsh_client_request_candidate_input_draft",[request])}:{}),
    ...(host.dsh_client_candidate_input_draft_preview_read?{dsh_client_candidate_input_draft_preview_read:request=>callAppDocumentApi(host,"dsh_client_candidate_input_draft_preview_read",[request])}:{}),
    ...(host.dsh_client_whiteboard_projection?{dsh_client_whiteboard_projection:request=>callAppDocumentApi(host,"dsh_client_whiteboard_projection",[request])}:{}),
    ...(host.dsh_client_saved_whiteboard?{dsh_client_saved_whiteboard:request=>callAppDocumentApi(host,"dsh_client_saved_whiteboard",[request])}:{}),
    ...(host.dsh_client_session_status?{dsh_client_session_status:request=>callAppDocumentApi(host,"dsh_client_session_status",[request])}:{}),
    ...(host.dsh_client_incompatible_applications?{dsh_client_incompatible_applications:request=>callAppDocumentApi(host,"dsh_client_incompatible_applications",[request])}:{}),
    ...(host.dsh_client_abandon_incompatible_application?{dsh_client_abandon_incompatible_application:request=>callAppDocumentApi(host,"dsh_client_abandon_incompatible_application",[request])}:{}),
  };
}

type Entry = {
  users: number;
  value?: AppDomainBinding;
  error?: Error;
  dshBinding?: SessionBinding;
  releaseEvents?: () => void;
  dirty: boolean;
  pending?: Promise<void>;
};

function object(value: unknown): Record<string, unknown> {
  if (!value || typeof value !== "object" || Array.isArray(value)) throw new AppCandidateReadError("domain_binding_invalid");
  return value as Record<string, unknown>;
}

function identity(value: unknown): string {
  if (typeof value !== "string" || !/^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$/.test(value)) throw new AppCandidateReadError("domain_binding_invalid");
  return value;
}

function normalize(value: unknown, sessionId: string): AppDomainBinding | undefined {
  assertAppDomainSuccess(value);
  const root = object(value);
  if (root.binding === null) return undefined;
  const binding = object(root.binding);
  if (identity(binding.dsh_session_id) !== sessionId) throw new AppCandidateReadError("domain_binding_mismatch");
  const projection = object(binding.projection);
  if (projection.contract !== "native-harness-projection-v3" || !Number.isSafeInteger(projection.projection_revision)
    || (projection.projection_revision as number) < 0 || !Array.isArray(projection.available_commands)) {
    throw new AppCandidateReadError("domain_projection_invalid");
  }
  const ids = new Set<string>();
  const commands = projection.available_commands.map(value => {
    const command = object(value);
    const commandId = identity(command.command_id);
    if (ids.has(commandId) || typeof command.enabled !== "boolean"
      || command.expected_projection_revision !== projection.projection_revision) throw new AppCandidateReadError("domain_projection_invalid");
    ids.add(commandId);
    return Object.freeze({commandId, enabled: command.enabled});
  });
  const candidate=projection.candidate===undefined?undefined:object(projection.candidate);
  const applicationState=candidate?.application_state;
  if(applicationState!==undefined&&!["none","prepared","baseline_advanced","committed","aborted","outcome_unknown"].includes(String(applicationState)))throw new AppCandidateReadError("domain_projection_invalid");
  return Object.freeze({dshSessionId: sessionId, conversationId: identity(binding.conversation_id),
    ...(applicationState!==undefined?{applicationState:applicationState as AppDomainBinding["applicationState"]}:{}),
    projectionRevision: projection.projection_revision as number, availableCommands: Object.freeze(commands)});
}

export function bindingWithHostProjection(binding:AppDomainBinding,projection:unknown):AppDomainBinding {
  return normalize({binding:{dsh_session_id:binding.dshSessionId,conversation_id:binding.conversationId,projection}},binding.dshSessionId)!;
}

/**
 * Read-only App projection bridge. DSH's public mainView retention selects the
 * foreground; only Host resolves Session→conversation. Event notifications
 * invalidate App facts without retaining or copying the DSH event contents.
 */
export function createAppDomainBridge(api: AppDomainGuiApi, sessions: Pick<ISessions, "list" | "binding">) {
  const entries = new Map<string, Entry>();
  const listeners = new Set<() => void>();
  const invalidations = new Set<(dshSessionId: string) => void>();
  let selected: string | undefined;
  let disposed = false;
  const notify = () => notifySubscribers(listeners, "[genshin-app-domain]");

  const refresh = (sessionId: string, entry: Entry): void => {
    if (disposed || entries.get(sessionId) !== entry) return;
    entry.dirty = true;
    if (entry.pending) return;
    entry.pending = (async () => {
      while (entry.dirty && !disposed && entries.get(sessionId) === entry) {
        entry.dirty = false;
        try {
          const value = normalize(await api.dsh_client_domain_binding({dsh_session_id: sessionId}), sessionId);
          if (disposed || entries.get(sessionId) !== entry) return;
          if (JSON.stringify(value) !== JSON.stringify(entry.value) || entry.error) {
            entry.value = value;
            entry.error = undefined;
            notify();
          }
        } catch (error) {
          if (disposed || entries.get(sessionId) !== entry) return;
          entry.value = undefined;
          entry.error = error instanceof AppCandidateReadError||error instanceof AppDomainHostError ? error : new AppCandidateReadError("domain_binding_read_failed");
          notify();
        }
      }
    })().finally(() => {
      entry.pending = undefined;
      if (entry.dirty && !disposed && entries.get(sessionId) === entry) refresh(sessionId, entry);
    });
  };

  const observe = (sessionId: string, entry: Entry): boolean => {
    const binding = sessions.binding(sessionId as SessionId);
    if (binding === entry.dshBinding) return false;
    entry.releaseEvents?.();
    entry.dshBinding = binding;
    entry.releaseEvents = binding?.eventSource.subscribe(() => {
      const change = binding.eventSource.getSnapshot().change;
      // Live reasoning deltas never cause extra App projection calls. Durable
      // changes only invalidate; their payloads do not enter this bridge.
      if (change.kind === "append" && change.entries.every(row => row.type === "transient")) return;
      notifySubscribers(new Set([...invalidations].map(listener=>()=>listener(sessionId))),"[genshin-app-domain-invalidation]");
      refresh(sessionId, entry);
    });
    return true;
  };

  const retain = (sessionId: string): (() => void) => {
    identity(sessionId);
    if (disposed) return () => undefined;
    let entry = entries.get(sessionId);
    if (!entry) {
      entry = {users: 0, dirty: false};
      entries.set(sessionId, entry);
    }
    const owned = entry;
    entry.users += 1;
    observe(sessionId, entry);
    refresh(sessionId, entry);
    let released = false;
    return () => {
      if (released) return;
      released = true;
      owned.users -= 1;
      if (owned.users === 0 && entries.get(sessionId) === owned) {
        owned.releaseEvents?.();
        entries.delete(sessionId);
      }
    };
  };

  let releaseSelection: (() => void) | undefined;
  const selectionChanged = () => {
    const state = sessions.list.getSnapshot();
    const main = Object.values(state.byId).filter(row => (row.retainedBy.mainView ?? 0) > 0);
    const next = main.length === 1 ? main[0].id as string : undefined;
    if (next !== selected) {
      releaseSelection?.();
      selected = next;
      releaseSelection = next === undefined ? undefined : retain(next);
      notify();
    }
    for (const [sessionId, entry] of entries) if (observe(sessionId, entry)) refresh(sessionId, entry);
  };
  const stopSelection = sessions.list.subscribe(selectionChanged);
  selectionChanged();

  const bindings: AppDomainBindings = Object.freeze({
    getSnapshot: (sessionId = selected) => sessionId === undefined ? undefined : entries.get(sessionId)?.value,
    getError: (sessionId = selected) => sessionId === undefined ? undefined : entries.get(sessionId)?.error,
    subscribe(listener: () => void) { listeners.add(listener); return () => { listeners.delete(listener); }; },
    subscribeInvalidation(listener: (dshSessionId: string) => void) { invalidations.add(listener); return () => { invalidations.delete(listener); }; },
    retain,
  });
  return Object.freeze({
    bindings,
    api,
    refresh(sessionId = selected) {
      if (sessionId === undefined) return;
      const entry = entries.get(sessionId);
      if (entry) refresh(sessionId, entry);
    },
    dispose() {
      if (disposed) return;
      disposed = true;
      stopSelection();
      releaseSelection?.();
      for (const entry of entries.values()) entry.releaseEvents?.();
      entries.clear();
      listeners.clear();
      invalidations.clear();
    },
  });
}
