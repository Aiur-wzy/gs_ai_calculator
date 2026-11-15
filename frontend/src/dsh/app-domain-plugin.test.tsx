import {readFile} from "node:fs/promises";
import {createRequire} from "node:module";
import {pathToFileURL} from "node:url";
import {resolve} from "node:path";
import {Script} from "node:vm";
import {Context} from "@deepseek-ai/cordis";
import Loader from "@deepseek-ai/cordis-plugin-loader";
import {getStaticModules} from "@deepseek-ai/dsh-client-web";
import type {ClientBundleRegistration} from "@deepseek-ai/dsh-client-modules/client";
import type {PropsRenderSlots} from "@deepseek-ai/dsh-client-ui-slots";
import type {SlotRegistry} from "@deepseek-ai/dsh-client-ui-renderer/client";
import {act} from "react";
import {createRoot, type Root} from "react-dom/client";
import {afterEach, describe, expect, it, vi} from "vitest";
import {
  candidateReviewAvailable,
  childPointer,
  readCandidatePage,
  type AppCandidateGuiApi,
  type AppDomainBinding,
  type AppDomainBindings,
} from "./app-domain-candidate";
import {APP_CANDIDATE_PANEL, APP_CANDIDATE_PLUGIN_ID, CandidateWorkspace} from "./app-domain-plugin";
import contract from "../../dsh-client-composition.json";
import {AppDomainHostError} from "./app-domain-errors";

const binding: AppDomainBinding = {
  dshSessionId: "dsh-session-a", conversationId: "app-conversation-a", projectionRevision: 1, availableCommands: [],
};

function fixture() {
  const api: AppCandidateGuiApi = {
    agent_candidate: vi.fn(async () => ({code: 0, data: {kind: "whiteboard", revision: 5, receipt_fingerprint: null,
      ready: true, can_accept: true, summary: {characters: 4, actions: 40, duration: 35}}})),
    agent_candidate_read: vi.fn(async (_id, resource, pointer, offset) => ({code: 0, data: {
      resource, pointer, offset, kind: "object", total: 2,
      items: [{key: "team", value: [{name: "神里绫华"}]}, {key: "title", value: "测试候选"}], next_offset: null,
    }})),
  };
  let current: AppDomainBinding | undefined = binding;
  const listeners = new Set<() => void>();
  const source: AppDomainBindings = {
    getSnapshot: () => current,
    subscribe(listener) { listeners.add(listener); return () => listeners.delete(listener); },
  };
  return {api, source, update(value: AppDomainBinding | undefined) { current = value; for (const listener of listeners) listener(); }};
}

let root: Root | undefined;
let container: HTMLDivElement | undefined;

async function mount(source: AppDomainBindings, api: AppCandidateGuiApi, sessionId?: string) {
  vi.stubGlobal("IS_REACT_ACT_ENVIRONMENT", true);
  container = document.createElement("div");
  document.body.append(container);
  root = createRoot(container);
  await act(async () => root!.render(<CandidateWorkspace bindings={source} api={api} dshSessionId={sessionId} />));
  return container;
}

afterEach(async () => {
  if (root) await act(async () => root!.unmount());
  root = undefined;
  container?.remove();
  container = undefined;
  vi.unstubAllGlobals();
});

describe("App candidate read port", () => {
  it("shows the existing full task review without reading a calculated package or creating a new GUI port",async()=>{
    const {api,source}=fixture();
    vi.mocked(api.agent_candidate).mockResolvedValue({code:0,data:{kind:"whiteboard",revision:5,
      receipt_fingerprint:null,summary:{characters:4},task_review:{contract:"genshin-task-evidence-v1",
        authority:"advisory_only",acceptance_effect:"none",status:"differences",
        task_sources:[{id:"original",description:"每组均需核对扩冰"}],requirements:[{id:"r17",description:"第六组扩冰",disposition:"pending"}],
        differences:Array.from({length:18},(_,i)=>({id:`r${i}`,summary:`核对第${i+1}组`,evidence_refs:[{reference:"input",pointer:`/actions/${i}`}]}))}}});
    const host=await mount(source,api);
    expect(host.textContent).toContain("18 项待核对");expect(host.textContent).toContain("核对第18组");
    expect(host.textContent).toContain("每组均需核对扩冰");
    expect(vi.mocked(api.agent_candidate_read).mock.calls.every(([,resource])=>resource==="candidate")).toBe(true);
  });
  it("shows compact approximations and pending work from the same report basis",async()=>{
    const {api,source}=fixture();
    vi.mocked(api.agent_candidate).mockResolvedValue({code:0,data:{kind:"whiteboard",revision:5,
      receipt_fingerprint:null,task_review:{contract:"genshin-task-evidence-v1",authority:"advisory_only",
        acceptance_effect:"none",status:"differences",report_basis:{calculation_currency:"calculation_input_unverified",
          approximations:["末跳时间近似"],not_completed:["第二轮附着待核"]}}}});
    const host=await mount(source,api);
    expect(host.textContent).toContain("末跳时间近似");
    expect(host.textContent).toContain("第二轮附着待核");
    expect(host.textContent).toContain("无法核实计算与保存的输入一致");
  });
  it("preserves Host compatibility details instead of converting them to a candidate JSON error",async()=>{
    const {api}=fixture(),details={contract:"genshin-native-domain-incompatibility-v1",state:"changed",differences:[],migration_prompt:"重新核查旧文件。"};
    vi.mocked(api.agent_candidate).mockResolvedValue({code:1,reason:"native_app_binding_compatibility_changed",message:"候选不兼容",details,operation_may_have_completed:false});
    await expect(readCandidatePage(api,binding,{resource:"candidate",pointer:"",offset:0},new AbortController().signal)).rejects.toMatchObject({name:"AppDomainHostError",code:"native_app_binding_compatibility_changed",details});expect(api.agent_candidate_read).not.toHaveBeenCalled();
  });
  it("keeps the same read-only candidate mounted during a permission refresh",async()=>{
    const {api,source,update}=fixture(),host=await mount(source,api);
    await act(()=>[...host.querySelectorAll("button")].find(row=>row.textContent==="伤害事件")!.click());
    let release!:()=>void;const waiting=new Promise<void>(resolve=>{release=resolve;});
    const original=api.agent_candidate;api.agent_candidate=vi.fn(async(id:string)=>{await waiting;return original(id);});
    await act(async()=>update({...binding,projectionRevision:2}));
    expect(host.textContent).toContain("当前显示上次读取的版本");expect([...host.querySelectorAll("button")].find(row=>row.textContent==="伤害事件")!.getAttribute("aria-pressed")).toBe("true");
    await act(async()=>release());
    expect([...host.querySelectorAll("button")].find(row=>row.textContent==="伤害事件")!.getAttribute("aria-pressed")).toBe("true");
  });
  it("reads an unsealed candidate through existing GUI APIs and exact Host binding", async () => {
    const {api} = fixture();
    const result = await readCandidatePage(api, binding, {resource: "candidate", pointer: "", offset: 0}, new AbortController().signal);
    expect(result.summary.revision).toBe(5);
    expect(result.summary.receipt).toBeNull();
    expect(api.agent_candidate).toHaveBeenCalledTimes(2);
    expect(api.agent_candidate_read).toHaveBeenCalledExactlyOnceWith("app-conversation-a", "candidate", "", 0, 50);
  });

  it("does not mistake ready/can_accept for Host review permission", async () => {
    const {api} = fixture();
    await readCandidatePage(api, binding, {resource: "candidate", pointer: "", offset: 0}, new AbortController().signal);
    expect(candidateReviewAvailable(binding)).toBe(false);
    expect(candidateReviewAvailable({...binding, availableCommands: [{commandId: "candidate.review", enabled: false}]})).toBe(false);
    expect(candidateReviewAvailable({...binding, availableCommands: [{commandId: "candidate.review", enabled: true}]})).toBe(true);
  });

  it("rejects mixed candidate revisions instead of displaying a partly stale page", async () => {
    const {api} = fixture();
    vi.mocked(api.agent_candidate).mockResolvedValueOnce({code: 0, data: {kind: "whiteboard", revision: 4, receipt_fingerprint: null}});
    await expect(readCandidatePage(api, binding, {resource: "candidate", pointer: "", offset: 0}, new AbortController().signal))
      .rejects.toMatchObject({code: "candidate_changed"});
  });

  it.each(["resource", "pointer", "offset", "next_offset"])("rejects a mismatched %s in a Host page", async field => {
    const {api} = fixture();
    vi.mocked(api.agent_candidate_read).mockResolvedValueOnce({code: 0, data: {
      resource: "candidate", pointer: "", offset: 0, kind: "object", total: 0, items: [], next_offset: null,
      ...{resource: {resource: "baseline"}, pointer: {pointer: "/other"}, offset: {offset: 50}, next_offset: {next_offset: 0}}[field],
    }});
    await expect(readCandidatePage(api, binding, {resource: "candidate", pointer: "", offset: 0}, new AbortController().signal))
      .rejects.toMatchObject({code: "candidate_page_mismatch"});
  });

  it("honors cancellation between requests and does not request a further page", async () => {
    const {api} = fixture();
    const controller = new AbortController();
    vi.mocked(api.agent_candidate).mockImplementationOnce(async () => {
      controller.abort();
      return {code: 0, data: {kind: "whiteboard", revision: 5, receipt_fingerprint: null}};
    });
    await expect(readCandidatePage(api, binding, {resource: "candidate", pointer: "", offset: 0}, controller.signal))
      .rejects.toMatchObject({name: "AbortError"});
    expect(api.agent_candidate_read).not.toHaveBeenCalled();
  });

  it("escapes JSON pointers without inventing an execution input", () => {
    expect(childPointer("/extensions", "a/b~c")).toBe("/extensions/a~1b~0c");
  });
});

describe("candidate workspace", () => {
  for(const [scope,message] of [["draft_sync","草稿可审核同步；尚未获得正式计算结果。"],["formal_result","计算已接纳；同步到白板仍需你的审核。"]]){
    it(`renders Host readiness scope ${scope} without granting new review authority`,async()=>{
      const {api,source}=fixture();const original=await api.agent_candidate("app-conversation-a") as {code:number;data:Record<string,unknown>};
      vi.mocked(api.agent_candidate).mockResolvedValue({...original,data:{...original.data,ready_scope:scope}});
      const view=await mount(source,api);expect(view.textContent).toContain(message);expect(view.textContent).toContain("当前仅供查看");
    });
  }
  it("shows saved energy diagnostics without enabling review", async () => {
    const {api, source} = fixture();
    const original = await api.agent_candidate("app-conversation-a") as {code:number;data:Record<string,unknown>};
    vi.mocked(api.agent_candidate).mockResolvedValue({...original, data:{...original.data, execution_feedback:{
      composition:"ready", execution:"diagnostic", complete_input_window:false,
      elemental:"declared_without_trace", reported_hits:86,
      failure_summary:{first_rejected_event:{action_id:"r2-q", t:28, before:28.8, required:60, deficit:31.2}},
    }}});
    const view = await mount(source, api);
    expect(view.textContent).toContain("执行未完成，已保留诊断");
    expect(view.textContent).toContain("能量 28.8／60，缺 31.2");
    expect(view.textContent).toContain("86 条已执行伤害记录");
    expect(view.textContent).toContain("当前未取得动态流水");
    expect(view.textContent).toContain("尚未获得 App 的审核许可");
  });
  it("shows incompatibility without an unbound explanation or any review/apply command",async()=>{
    const {api,source,update}=fixture();update(undefined);const error=new AppDomainHostError("native_app_binding_compatibility_missing","无旧版本证明",{contract:"genshin-native-domain-incompatibility-v1",state:"missing",differences:[],migration_prompt:"新会话重新核查旧文件。"},false);source.getError=()=>error;
    const host=await mount(source,api,"dsh-session-a");expect(host.textContent).toContain("旧候选没有可核验的版本记录");expect(host.textContent).not.toContain("尚未绑定候选白板");expect([...host.querySelectorAll("button")].map(value=>value.textContent)).toEqual(["复制适配提示"]);expect(api.agent_candidate).not.toHaveBeenCalled();
  });
  it("shows candidate/baseline separation, revision and bounded field navigation", async () => {
    const {api, source} = fixture();
    const view = await mount(source, api);
    await act(async()=>[...view.querySelectorAll("button")].find(node=>node.textContent==="资料与原始输入")!.click());
    expect(view.textContent).toContain("Agent 候选工作区");
    expect(view.textContent).toContain("候选版本 5 · 4 名角色 · 40 个动作 · 35 秒");
    expect(view.textContent).toContain("当前仅供查看");
    expect(view.textContent).toContain("本会话的正式白板基线");
    const open = [...view.querySelectorAll("button")].find(node => node.textContent === "查看内容")!;
    await act(async () => open.click());
    expect(api.agent_candidate_read).toHaveBeenLastCalledWith("app-conversation-a", "candidate", "/team", 0, 50);
  });

  it("keeps model text inert and handles prototype-named fields", async () => {
    const {api, source} = fixture();
    vi.mocked(api.agent_candidate_read).mockResolvedValue({code: 0, data: {
      resource: "candidate", pointer: "", offset: 0, kind: "object", total: 2,
      items: [{key: "constructor", value: "<script>window.compromised = true</script>"}, {key: "__proto__", value: "plain"}], next_offset: null,
    }});
    const view = await mount(source, api);
    await act(async()=>[...view.querySelectorAll("button")].find(node=>node.textContent==="资料与原始输入")!.click());
    expect(view.textContent).toContain("constructor");
    expect(view.textContent).toContain("<script>");
    expect(view.querySelector("script")).toBeNull();
  });

  it("does not read another Session's candidate when Host binding mismatches the slot", async () => {
    const {api, source} = fixture();
    const view = await mount(source, api, "other-dsh-session");
    expect(view.textContent).toContain("当前会话尚未绑定候选白板");
    expect(api.agent_candidate).not.toHaveBeenCalled();
  });

  it("resets navigation on an exact Host binding change and ignores old content", async () => {
    const {api, source, update} = fixture();
    const view = await mount(source, api);
    await act(async()=>[...view.querySelectorAll("button")].find(node=>node.textContent==="资料与原始输入")!.click());
    await act(async () => [...view.querySelectorAll("button")].find(node => node.textContent === "查看内容")!.click());
    await act(async () => update({...binding, dshSessionId: "dsh-session-b", conversationId: "app-conversation-b"}));
    expect(api.agent_candidate_read).toHaveBeenLastCalledWith("app-conversation-b", "candidate", "", 0, 50);
  });
});

describe("public DSH slot composition", () => {
  it("loads App bytes from the official Registry and removes domain slots on Cordis unload", async () => {
    // Materialize the real public renderer bundle against DSH's sole platform
    // table. The factory registration is the documented client bundle format;
    // no source deep import or alternate React/Slot implementation is used.
    let registration: ClientBundleRegistration | undefined;
    const path = createRequire(import.meta.url).resolve("@deepseek-ai/dsh-client-ui-renderer/client");
    new Script(await readFile(path, "utf8")).runInNewContext({
      window: {__ModuleLoader__: {load(row: ClientBundleRegistration) { registration = row; }}},
    });
    const platform = getStaticModules();
    const module = registration!.factory(Object.assign((key: string) => platform[key], {async: async (key: string) => platform[key]}));
    const Registry = module.SlotRegistry as new (ctx: Context) => SlotRegistry;
    const ctx = new Context();
    await ctx.plugin(Registry);
    const registrations = new Map<string, unknown>();
    ctx.provide("sidebarRightTabs", {register(value: {id: string}) {
      registrations.set(value.id, value);
      return () => registrations.delete(value.id);
    }} as unknown as Context["sidebarRightTabs"]);
    ctx.provide("uiWorkspace",{openSession(){},archiveSession:async()=>{}} as unknown as Context["uiWorkspace"]);
    ctx.provide("layout",{selectPanel:vi.fn()} as unknown as Context["layout"]);
    const releaseRoot = ctx.slots.register({name: "root", children: {
      main: {kind: "keyed", scope: "root"}, "sidebar.panellist": {kind: "list", scope: "root"},
      "sidebar.right.pane.tab": {kind: "keyed", scope: "session", inject: {hooks: {tabInfo: () => () => {
        throw new Error("fixture_does_not_render_tabs");
      }}}},
    }}, (props: PropsRenderSlots<"main" | "sidebar.panellist" | "sidebar.right.pane.tab">) => { void props.renderSlot; return null; });
    const {api} = fixture();
    const domainApi = {dsh_client_candidate: vi.fn(({conversation_id}: {conversation_id: string}) => api.agent_candidate(conversation_id)),
      dsh_client_candidate_read: vi.fn(({conversation_id,resource,pointer,offset,limit}: {conversation_id: string;resource:"candidate"|"baseline";pointer:string;offset:number;limit:number}) => api.agent_candidate_read(conversation_id,resource,pointer,offset,limit)),
      dsh_client_domain_binding: vi.fn(async () => ({binding: null}))};
    ctx.provide("sessions", {list: {getSnapshot: () => ({byId: {}}), subscribe: () => () => undefined}, binding: () => undefined} as unknown as Context["sessions"]);
    // The real client composition provides this public store. The domain
    // plugin uses it to avoid reopening an archived draft Session.
    ctx.provide("workspaces", {list: {getSnapshot: () => ({state: "idle", archivedSessionIds: []}), subscribe: () => () => undefined}} as unknown as Context["workspaces"]);
    const host = new Context();
    await host.plugin(Loader, {baseUrl: import.meta.url});
    for (const name of contract.dynamicModules.filter(name => name !== "@deepseek-ai/dsh-client-modules" && name !== APP_CANDIDATE_PLUGIN_ID)) {
      await host.loader.create({name});
    }
    const localHost = resolve(process.cwd(), "packages/dsh-client-domain/host.mjs");
    await host.loader.create({name: pathToFileURL(localHost).href});
    await host.loader.create({name: "@deepseek-ai/dsh-client-modules"});
    await host.loader.await();
    const graph = host.clientModules.graph();
    const entry = graph.entries.find(row => row.id === APP_CANDIDATE_PLUGIN_ID)!;
    expect(entry).toBeDefined();
    expect(entry.inject).toContain("@deepseek-ai/dsh-client-ui-sidebar-right");
    const response = await host.clientModules.fetchBundle(new Request(new URL(entry.url, "https://dsh-shell.invalid")));
    expect(response.status).toBe(200);
    let domain: ClientBundleRegistration | undefined;
    new Script(await response.text()).runInNewContext({document, window: {
      __ModuleLoader__: {load(row: ClientBundleRegistration) { domain = row; }}, pywebview: {api: domainApi},
      addEventListener: window.addEventListener.bind(window), removeEventListener: window.removeEventListener.bind(window),
    }});
    const required: string[] = [];
    let conversationRegistration:ClientBundleRegistration|undefined;
    new Script(await readFile(createRequire(import.meta.url).resolve("@deepseek-ai/dsh-client-ui-conversation/client"),"utf8")).runInNewContext({document,window:{__ModuleLoader__:{load(row:ClientBundleRegistration){conversationRegistration=row;}}}});
    const conversationModule=conversationRegistration!.factory(Object.assign((key:string)=>platform[key],{async:async(key:string)=>platform[key]}));
    const lookup=(key:string)=>key==="@deepseek-ai/dsh-client-ui-conversation/client"?conversationModule:platform[key];
    const domainModule = domain!.factory(Object.assign((key: string) => { required.push(key); return lookup(key); },
      {async: async (key: string) => lookup(key)}));
    expect(required.sort()).toEqual(["@deepseek-ai/dsh-client-store", "@deepseek-ai/dsh-client-ui-conversation/client", "@deepseek-ai/dsh-client-ui-primitives", "react", "react/jsx-runtime"]);
    expect(domainModule.inject).toEqual(["slots", "sidebarRightTabs", "sessions"]);
    expect(typeof domainModule.apply).toBe("function");
    const plugin = await ctx.plugin(domainModule as unknown as {inject: string[]; apply(ctx: Context): void});
    await vi.waitFor(() => expect(ctx.slots.entriesOfSlot("main")).toHaveLength(2));
    expect(ctx.slots.entriesOfSlot("main").map(row => row.options.key)).toEqual([APP_CANDIDATE_PANEL,"genshin-task-center"]);
    expect(ctx.slots.entriesOfSlot("sidebar.right.pane.tab").map(row => row.options.key)).toEqual([APP_CANDIDATE_PLUGIN_ID,"genshin-app-operations","genshin-session-questions","genshin-app-diagnostics"]);
    expect(registrations.has(APP_CANDIDATE_PLUGIN_ID)).toBe(true);
    expect(ctx.slots.entriesOfSlot("conversation.messages" as never)).toEqual([]);
    await plugin.dispose();
    expect(ctx.slots.entriesOfSlot("main")).toEqual([]);
    expect(ctx.slots.entriesOfSlot("sidebar.right.pane.tab")).toEqual([]);
    expect(registrations.size).toBe(0);
    expect(document.querySelector(`style[data-plugin="${APP_CANDIDATE_PLUGIN_ID}"]`)).toBeNull();
    releaseRoot();
    await ctx.fiber.dispose();
    await host.fiber.dispose();
  }, 30_000);
});
