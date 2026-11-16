import {act} from "react";
import {createRoot,type Root} from "react-dom/client";
import {afterEach,beforeEach,describe,expect,it,vi} from "vitest";
import type {Context} from "@deepseek-ai/cordis";
import type {PropsRuntime} from "@deepseek-ai/dsh-client-ui-slots";
import {AppOperationsInspector,AppQuestionsInspector} from "./app-domain-inspector";

let root:Root|undefined;
beforeEach(()=>vi.stubGlobal("IS_REACT_ACT_ENVIRONMENT",true));
afterEach(async()=>{if(root)await act(()=>root!.unmount());root=undefined;document.body.replaceChildren();vi.unstubAllGlobals();});
describe("official pending-interaction Inspector",()=>{
  async function mount(pending:unknown){
    const openSession=vi.fn(),ctx={uiWorkspace:{openSession}} as unknown as Context;
    const props={sessionId:"session-exact",useSessionStatus:(select:(map:Map<string,unknown>)=>unknown)=>select(new Map([["session-exact",{pendingInteraction:pending}]]))} as unknown as PropsRuntime<"sidebar.right.pane.tab">;
    const host=document.createElement("div");document.body.append(host);root=createRoot(host);await act(()=>root!.render(<AppQuestionsInspector ctx={ctx} props={props}/>));return {host,openSession};
  }
  it("renders exact public questions but only navigates to the official answer owner",async()=>{
    const {host,openSession}=await mount({kind:"question",key:"q",sessionId:"session-exact",questions:[{id:"one",question:"是否采用更高充能？",detail:"修改会影响暴击词条分配。"}]});
    expect(host.textContent).toContain("是否采用更高充能？");expect(host.textContent).toContain("修改会影响暴击词条分配。");expect(host.querySelectorAll("button")).toHaveLength(1);
    await act(()=>host.querySelector("button")!.click());expect(openSession).toHaveBeenCalledExactlyOnceWith("session-exact");expect(host.textContent).toContain("不会把普通批准作为 App 资料修改授权");
  });
  it("does not invent actions when no interaction is pending",async()=>{
    const {host}=await mount(undefined);expect(host.textContent).toContain("当前没有待回答的问题");expect(host.querySelector("button")).toBeNull();
  });
});

it("dismisses a file/approval Inspector dialog when its bound conversation changes",async()=>{
  const bindings={subscribe:()=>()=>{},getSnapshot:(id?:string)=>id?scopes[id]:undefined};
  const scopes={first:{dshSessionId:"first",conversationId:"app-a",projectionRevision:1,availableCommands:[]},
    second:{dshSessionId:"second",conversationId:"app-b",projectionRevision:2,availableCommands:[]}} as Record<string,{dshSessionId:string;conversationId:string;projectionRevision:number;availableCommands:[]}>;
  const decision=vi.fn();
  const api={agent_candidate:vi.fn(),agent_candidate_read:vi.fn(),dsh_client_domain_approval_decide:decision,
    dsh_client_domain_approvals:vi.fn(async({conversation_id}:{conversation_id:string})=>({code:0,data:{approvals:[{question_id:`q-${conversation_id}`,state:"pending",payload_fingerprint:"a".repeat(64),entity_type:"weapon",provisional_id:"p",expected_candidate_revision:1,expected_stage_revision:1,question:{id:`q-${conversation_id}`,question:`待确认 ${conversation_id}`}}]}}))};
  const host=document.createElement("div");document.body.append(host);root=createRoot(host);
  const render=(sessionId:string)=>root!.render(<AppOperationsInspector api={api} bindings={bindings} props={{sessionId} as PropsRuntime<"sidebar.right.pane.tab">}/>);
  await act(async()=>render("first"));
  await act(async()=>host.querySelector("button")!.click());
  expect(document.querySelector('[role="dialog"]')?.textContent).toContain("待确认 app-a");
  await act(async()=>render("second"));
  expect(document.querySelector('[role="dialog"]')).toBeNull();
  expect(host.textContent).toContain("待确认 app-b");expect(decision).not.toHaveBeenCalled();
});
