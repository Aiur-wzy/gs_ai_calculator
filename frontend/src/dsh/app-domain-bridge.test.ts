import type {ISessions, SessionBinding} from "@deepseek-ai/dsh-api-session-controller/client";
import {describe, expect, it, vi} from "vitest";
import {createAppDomainBridge,createProductionAppDomainApi, type AppDomainGuiApi} from "./app-domain-bridge";

function deferred<T = unknown>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>(yes => { resolve = yes; });
  return {promise, resolve};
}

function response(id: string, revision = 1, conversation = `app-${id}`) {
  return {binding: {dsh_session_id: id, conversation_id: conversation, projection: {
    contract: "native-harness-projection-v3", projection_revision: revision,
    available_commands: [{command_id: "candidate.review", enabled: true, expected_projection_revision: revision}],
  }}};
}

function fixture(main: readonly string[] = ["A"]) {
  const listListeners = new Set<() => void>();
  let state: {byId: Record<string, {id: string; retainedBy: {mainView: number}; displayTitle: string}>} = {byId: {}};
  const build = (ids: readonly string[]) => ({byId: Object.fromEntries(["A", "B"].map(id => [id, {
    id, displayTitle: "identical title", retainedBy: {mainView: ids.includes(id) ? 1 : 0},
  }]))});
  state = build(main);
  const eventListeners = new Map<string, Set<() => void>>();
  const changes = new Map<string, unknown>();
  const bindings = new Map<string, SessionBinding>();
  for (const id of ["A", "B"]) {
    eventListeners.set(id, new Set());
    changes.set(id, {kind: "append", entries: [{type: "event"}]});
    bindings.set(id, {eventSource: {
      getSnapshot: () => ({change: changes.get(id)}),
      subscribe(listener: () => void) { eventListeners.get(id)!.add(listener); return () => { eventListeners.get(id)!.delete(listener); }; },
    }} as unknown as SessionBinding);
  }
  const sessions = {
    list: {getSnapshot: () => state, subscribe(listener: () => void) { listListeners.add(listener); return () => { listListeners.delete(listener); }; }},
    binding: (id: string) => bindings.get(id),
  } as unknown as Pick<ISessions, "list" | "binding">;
  const api: AppDomainGuiApi = {
    dsh_client_domain_binding: vi.fn(async ({dsh_session_id}) => response(dsh_session_id)),
    agent_candidate: vi.fn(async () => ({code: 0, data: {}})),
    agent_candidate_read: vi.fn(async () => ({code: 0, data: {}})),
  };
  return {sessions, api, eventListeners, listListeners,
    select(ids: readonly string[]) { state = build(ids); for (const listener of listListeners) listener(); },
    invalidate(id: string, transient = false) {
      changes.set(id, {kind: "append", entries: [{type: transient ? "transient" : "event"}]});
      for (const listener of eventListeners.get(id)!) listener();
    },
  };
}

describe("App exact domain binding bridge", () => {
  it("routes global incompatible-application decisions only to the explicit App document endpoints",async()=>{
    const list=vi.fn(async()=>({code:0,data:{items:[]}})),abandon=vi.fn(async()=>({code:0,data:{}}));
    const api=createProductionAppDomainApi({dsh_client_domain_binding:vi.fn(),dsh_client_candidate:vi.fn(),dsh_client_candidate_read:vi.fn(),dsh_client_incompatible_applications:list,dsh_client_abandon_incompatible_application:abandon});
    const request={conversation_id:"old",expected_fingerprint:"a".repeat(64),decision:"abandon_recovery" as const};
    await api.dsh_client_incompatible_applications!({});await api.dsh_client_abandon_incompatible_application!(request);
    expect(list).toHaveBeenCalledExactlyOnceWith({});expect(abandon).toHaveBeenCalledExactlyOnceWith(request);
  });
  it("selects the unique public mainView holder and resolves its mapping only through Host", async () => {
    const {api, sessions} = fixture();
    const bridge = createAppDomainBridge(api, sessions);
    await vi.waitFor(() => expect(bridge.bindings.getSnapshot()?.conversationId).toBe("app-A"));
    expect(api.dsh_client_domain_binding).toHaveBeenCalledExactlyOnceWith({dsh_session_id: "A"});
    expect(bridge.bindings.getSnapshot()?.availableCommands).toEqual([{commandId: "candidate.review", enabled: true}]);
    bridge.dispose();
  });

  it.each([{main: []}, {main: ["A", "B"]}])("does not guess selection from titles or list order when mainView is $main", ({main}) => {
    const {api, sessions} = fixture(main);
    const bridge = createAppDomainBridge(api, sessions);
    expect(bridge.bindings.getSnapshot()).toBeUndefined();
    expect(api.dsh_client_domain_binding).not.toHaveBeenCalled();
    bridge.dispose();
  });

  it("rejects cross-Session Host responses instead of exposing the other candidate", async () => {
    const {api, sessions} = fixture();
    vi.mocked(api.dsh_client_domain_binding).mockResolvedValueOnce(response("B"));
    const bridge = createAppDomainBridge(api, sessions);
    await vi.waitFor(() => expect(bridge.bindings.getError?.()).toMatchObject({code: "domain_binding_mismatch"}));
    expect(bridge.bindings.getSnapshot()).toBeUndefined();
    bridge.dispose();
  });
  it("preserves typed App domain failures without turning them into JSON-shape errors",async()=>{
    const {api,sessions}=fixture();vi.mocked(api.dsh_client_domain_binding).mockResolvedValueOnce({code:1,reason:"agent_database_changed",message:"这份候选使用旧版资料库，请在新会话调整文件。",details:{},operation_may_have_completed:false});
    const bridge=createAppDomainBridge(api,sessions);await vi.waitFor(()=>expect(bridge.bindings.getError?.()).toMatchObject({name:"AppDomainHostError",code:"agent_database_changed",message:"这份候选使用旧版资料库，请在新会话调整文件。"}));expect(bridge.bindings.getSnapshot()).toBeUndefined();bridge.dispose();
  });

  it("rejects command permits from a different projection revision", async () => {
    const {api, sessions} = fixture();
    const value = response("A");
    value.binding.projection.available_commands[0].expected_projection_revision = 99;
    vi.mocked(api.dsh_client_domain_binding).mockResolvedValueOnce(value);
    const bridge = createAppDomainBridge(api, sessions);
    await vi.waitFor(() => expect(bridge.bindings.getError?.()).toMatchObject({code: "domain_projection_invalid"}));
    bridge.dispose();
  });

  it("does not let an old response overwrite a newer foreground binding", async () => {
    const {api, sessions, select} = fixture();
    const old = deferred();
    vi.mocked(api.dsh_client_domain_binding).mockImplementationOnce(() => old.promise);
    const bridge = createAppDomainBridge(api, sessions);
    select(["B"]);
    await vi.waitFor(() => expect(bridge.bindings.getSnapshot()?.dshSessionId).toBe("B"));
    old.resolve(response("A", 999));
    await Promise.resolve();
    expect(bridge.bindings.getSnapshot()?.dshSessionId).toBe("B");
    expect(bridge.bindings.getSnapshot("A")).toBeUndefined();
    bridge.dispose();
  });

  it("does not attach a late old-session incompatibility to another selected conversation",async()=>{
    const {api,sessions,select}=fixture(),old=deferred();vi.mocked(api.dsh_client_domain_binding).mockImplementationOnce(()=>old.promise);
    const bridge=createAppDomainBridge(api,sessions);select(["B"]);await vi.waitFor(()=>expect(bridge.bindings.getSnapshot()?.dshSessionId).toBe("B"));
    old.resolve({code:1,reason:"native_app_binding_compatibility_changed",message:"旧候选不兼容",details:{contract:"genshin-native-domain-incompatibility-v1",state:"changed",differences:[]},operation_may_have_completed:false});
    await Promise.resolve();expect(bridge.bindings.getSnapshot()?.dshSessionId).toBe("B");expect(bridge.bindings.getError?.()).toBeUndefined();expect(bridge.bindings.getSnapshot("A")).toBeUndefined();bridge.dispose();
  });

  it("ignores transient reasoning and coalesces durable invalidations during a read", async () => {
    const {api, sessions, invalidate} = fixture();
    const pending = deferred();
    vi.mocked(api.dsh_client_domain_binding).mockImplementationOnce(() => pending.promise);
    const bridge = createAppDomainBridge(api, sessions);
    for (let index = 0; index < 100; index++) invalidate("A", true);
    expect(api.dsh_client_domain_binding).toHaveBeenCalledTimes(1);
    for (let index = 0; index < 20; index++) invalidate("A");
    expect(api.dsh_client_domain_binding).toHaveBeenCalledTimes(1);
    pending.resolve(response("A"));
    await vi.waitFor(() => expect(api.dsh_client_domain_binding).toHaveBeenCalledTimes(2));
    bridge.dispose();
  });

  it("allows an Inspector to read its exact Session without changing the foreground", async () => {
    const {api, sessions, eventListeners} = fixture();
    const bridge = createAppDomainBridge(api, sessions);
    const release = bridge.bindings.retain!("B");
    await vi.waitFor(() => expect(bridge.bindings.getSnapshot("B")?.conversationId).toBe("app-B"));
    expect(bridge.bindings.getSnapshot()?.dshSessionId).toBe("A");
    release();
    expect(eventListeners.get("B")!.size).toBe(0);
    expect(bridge.bindings.getSnapshot("B")).toBeUndefined();
    bridge.dispose();
  });

  it("clears old review permissions after Host reports no binding or an error", async () => {
    const {api, sessions, invalidate} = fixture();
    const bridge = createAppDomainBridge(api, sessions);
    await vi.waitFor(() => expect(bridge.bindings.getSnapshot()).toBeDefined());
    vi.mocked(api.dsh_client_domain_binding).mockResolvedValueOnce({binding: null});
    invalidate("A");
    await vi.waitFor(() => expect(bridge.bindings.getSnapshot()).toBeUndefined());
    vi.mocked(api.dsh_client_domain_binding).mockRejectedValueOnce(new Error("offline"));
    invalidate("A");
    await vi.waitFor(() => expect(bridge.bindings.getError?.()).toMatchObject({code: "domain_binding_read_failed"}));
    expect(bridge.bindings.getSnapshot()).toBeUndefined();
    bridge.dispose();
  });

  it("disposes subscriptions without waiting on a Host read or publishing a late result", async () => {
    const {api, sessions, eventListeners, listListeners} = fixture();
    const pending = deferred();
    vi.mocked(api.dsh_client_domain_binding).mockImplementationOnce(() => pending.promise);
    const bridge = createAppDomainBridge(api, sessions);
    const listener = vi.fn();
    bridge.bindings.subscribe(listener);
    bridge.dispose();
    pending.resolve(response("A"));
    await Promise.resolve();
    expect(listener).not.toHaveBeenCalled();
    expect(eventListeners.get("A")!.size).toBe(0);
    expect(listListeners.size).toBe(0);
    expect(bridge.bindings.getSnapshot()).toBeUndefined();
  });
});
