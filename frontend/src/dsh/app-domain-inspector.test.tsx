import {act} from "react";
import {createRoot,type Root} from "react-dom/client";
import {afterEach,beforeEach,describe,expect,it,vi} from "vitest";
import type {Context} from "@deepseek-ai/cordis";
import type {PropsRuntime} from "@deepseek-ai/dsh-client-ui-slots";
import {AppQuestionsInspector} from "./app-domain-inspector";

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
