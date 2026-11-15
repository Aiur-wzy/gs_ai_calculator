import {act} from "react";
import {createRoot,type Root} from "react-dom/client";
import {afterEach,beforeEach,describe,expect,it,vi} from "vitest";
import {AppDomainApprovals,parseDomainApprovals} from "./app-domain-approvals";
import type {AppCandidateGuiApi,AppDomainBinding,AppDomainBindings} from "./app-domain-candidate";
let root:Root|undefined;
beforeEach(()=>vi.stubGlobal("IS_REACT_ACT_ENVIRONMENT",true));
afterEach(async()=>{if(root)await act(()=>root!.unmount());root=undefined;document.body.replaceChildren();vi.unstubAllGlobals();});
const binding:AppDomainBinding={dshSessionId:"session-one",conversationId:"conversation-one",projectionRevision:8,availableCommands:[]};
const pending={question_id:"correction-one",state:"pending",provisional_id:"weapon-temp-one",entity_type:"weapon",expected_candidate_revision:7,expected_stage_revision:3,payload_fingerprint:"a".repeat(64),question:{id:"correction-one",header:"武器资料需要确认",question:"是否采用校正后的基础攻击？",detail:"数据库：100；建议：110。来源：受审资料，仅用于本方案。"}};
function fixture(){
  let invalidate:(sid:string)=>void=()=>{};
  const bindings:AppDomainBindings={getSnapshot:()=>binding,subscribe:()=>()=>{},subscribeInvalidation(listener){invalidate=listener;return()=>{invalidate=()=>{};};}};
  const api={agent_candidate:vi.fn(),agent_candidate_read:vi.fn(),dsh_client_domain_approvals:vi.fn(async()=>({code:0,data:{approvals:[pending]}})),dsh_client_domain_approval_decide:vi.fn(async(_request:unknown)=>({code:0,data:{status:"approved",decision_receipt:"app-domain-decision-fixture"}}))};
  return {api,bindings,invalidate:(sid:string)=>invalidate(sid)};
}
async function mount(api:AppCandidateGuiApi,bindings:AppDomainBindings){const host=document.createElement("div");document.body.append(host);root=createRoot(host);await act(async()=>root!.render(<AppDomainApprovals api={api} binding={binding} bindings={bindings}/>));return host;}
describe("App public-data corrections",()=>{
  it("renders nothing when no App correction is pending",async()=>{const {api,bindings}=fixture();api.dsh_client_domain_approvals.mockResolvedValue({code:0,data:{approvals:[]}});const host=await mount(api,bindings);expect(host.textContent).toBe("");expect(api.dsh_client_domain_approval_decide).not.toHaveBeenCalled();});
  it("requires an explicit user decision on the complete App diff and sends exact revisions",async()=>{
    const {api,bindings}=fixture(),host=await mount(api,bindings);expect(api.dsh_client_domain_approval_decide).not.toHaveBeenCalled();
    await act(()=>host.querySelector("button")!.click());expect(document.body.textContent).toContain(pending.question.detail);expect(document.body.textContent).toContain("不会写回公共数据库");
    await act(async()=>[...document.querySelectorAll("button")].find(row=>row.textContent==="批准当前修正")!.click());
    expect(api.dsh_client_domain_approval_decide).toHaveBeenCalledExactlyOnceWith({conversation_id:"conversation-one",question_id:"correction-one",expected_candidate_revision:7,expected_stage_revision:3,decision:"approve"});
    expect(document.body.textContent).toContain("已批准这项资料修正，仅用于当前方案");
  });
  it("does not retry failed decisions or infer approval from durable DSH activity",async()=>{
    const {api,bindings,invalidate}=fixture();api.dsh_client_domain_approval_decide.mockResolvedValue({code:1,reason:"dsh_domain_approval_stale"} as never);
    const host=await mount(api,bindings);await act(()=>host.querySelector("button")!.click());await act(async()=>[...document.querySelectorAll("button")].find(row=>row.textContent==="批准当前修正")!.click());
    await act(async()=>invalidate("session-one"));expect(api.dsh_client_domain_approvals.mock.calls.length).toBeGreaterThan(1);expect(api.dsh_client_domain_approval_decide).toHaveBeenCalledOnce();expect(document.body.textContent).toContain("系统不会自动重复提交");
  });
  it("rejects mismatched question identities and duplicate pending requests",()=>{
    expect(()=>parseDomainApprovals({code:0,data:{approvals:[{...pending,question:{...pending.question,id:"other"}}]}})).toThrow("app_domain_approval_invalid");
    expect(()=>parseDomainApprovals({code:0,data:{approvals:[pending,pending]}})).toThrow("app_domain_approval_invalid");
  });
});
