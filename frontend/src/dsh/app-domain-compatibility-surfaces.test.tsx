import {act} from "react";
import {createRoot,type Root} from "react-dom/client";
import {afterEach,beforeEach,describe,expect,it,vi} from "vitest";
import type {Context} from "@deepseek-ai/cordis";
import type {PropsRuntime} from "@deepseek-ai/dsh-client-ui-slots";
import type {AppCandidateGuiApi,AppDomainBindings} from "./app-domain-candidate";
import {AppDomainHostError} from "./app-domain-errors";
import {AppOperationsInspector,AppDiagnosticsInspector} from "./app-domain-inspector";
import {CandidateHeaderAction} from "./app-domain-plugin";
import {AppTaskCenter} from "./app-task-center";

const error=new AppDomainHostError("native_app_binding_compatibility_changed","候选使用其他版本",{contract:"genshin-native-domain-incompatibility-v1",state:"changed",differences:[{component:"app_artifact_identity_hash",recorded:"a".repeat(64),current:"b".repeat(64)}],migration_prompt:"请在当前版本重新核查旧文件。"},false);
const bindings:AppDomainBindings={getSnapshot:()=>undefined,getError:()=>error,subscribe:()=>()=>undefined,retain:()=>()=>undefined};
const api:AppCandidateGuiApi={agent_candidate:vi.fn(),agent_candidate_read:vi.fn()};
let root:Root|undefined;
beforeEach(()=>vi.stubGlobal("IS_REACT_ACT_ENVIRONMENT",true));
afterEach(async()=>{if(root)await act(()=>root!.unmount());root=undefined;document.body.replaceChildren();vi.unstubAllGlobals();vi.clearAllMocks();});
async function mount(child:React.ReactNode){const host=document.createElement("div");document.body.append(host);root=createRoot(host);await act(()=>root!.render(child));return host;}
const props={sessionId:"exact-session",useSession:(select:(state:Record<string,unknown>)=>unknown)=>select({})} as unknown as PropsRuntime<"sidebar.right.pane.tab">;

describe("domain compatibility across official DSH slots",()=>{
  it("keeps the header diagnosis reachable without suggesting review",async()=>{
    const open=vi.fn(),host=await mount(<CandidateHeaderAction bindings={bindings} api={api} sessionId="exact-session" onOpen={open}/>);
    expect(host.textContent).toBe("查看兼容说明");await act(()=>host.querySelector("button")!.click());expect(open).toHaveBeenCalledOnce();expect(api.agent_candidate).not.toHaveBeenCalled();
  });
  it.each(["operations","diagnostics"])("renders a meaningful %s Inspector without pretending the document is simply unbound",async kind=>{
    const host=await mount(kind==="operations"?<AppOperationsInspector api={api} bindings={bindings} props={props}/>:<AppDiagnosticsInspector bindings={bindings} props={props}/>);
    expect(host.querySelectorAll('[aria-label="排轴版本兼容说明"]')).toHaveLength(1);expect(host.textContent).toContain("聊天、历史和资料搜索仍可使用");expect(host.textContent).not.toContain("此会话尚未绑定 App 候选");expect([...host.querySelectorAll("button")].some(value=>value.textContent==="复制适配提示")).toBe(true);
  });
  it("shows the App compatibility issue beside the truthful DSH round status instead of declaring the round failed",async()=>{
    const summary={id:"exact-session",blank:false,running:false,displayTitle:"旧排轴",updatedAt:1},list={phase:"ready",ids:[summary.id],byId:{[summary.id]:summary}},statuses=new Map(),workspaces={archivedSessionIds:[]};
    const mainProps={useSessions:(select:(value:unknown)=>unknown)=>select(list),useSessionStatus:(select:(value:unknown)=>unknown)=>select(statuses),useWorkspaces:(select:(value:unknown)=>unknown)=>select(workspaces)} as unknown as PropsRuntime<"main">;
    const openSession=vi.fn(),ctx={uiWorkspace:{openSession}} as unknown as Context;
    const host=await mount(<AppTaskCenter ctx={ctx} api={api} bindings={bindings} props={mainProps}/>);
    expect(host.textContent).toContain("排轴候选不兼容，聊天、历史和资料搜索仍可继续");expect(host.textContent).toContain("尚无已核实轮次结果");expect(host.textContent).not.toContain("本轮失败");
    await act(()=>[...host.querySelectorAll("button")].find(value=>value.textContent==="打开会话")!.click());expect(openSession).toHaveBeenCalledExactlyOnceWith("exact-session");
  });
});
