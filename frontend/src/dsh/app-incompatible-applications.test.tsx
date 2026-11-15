import {act} from "react";
import {createRoot,type Root} from "react-dom/client";
import {afterEach,beforeEach,describe,expect,it,vi} from "vitest";
import type {AppCandidateGuiApi,AppDomainBindings} from "./app-domain-candidate";
import {AppIncompatibleApplications,parseIncompatibleApplications} from "./app-incompatible-applications";
import {CandidateWorkspace} from "./app-domain-plugin";

const id="old-conversation",fingerprint="a".repeat(64),row={conversation_id:id,expected_fingerprint:fingerprint,state:"result_unknown",message:"旧同步结果未确认。",display_name:"旧版本排轴同步"};
const response=(items:unknown[])=>({code:0,data:{items}});
const success={code:0,data:{conversation_id:id,status:"recovery_abandoned",state:"result_unknown",decision_receipt:fingerprint,disposition:"recorded"}};
let root:Root|undefined;
beforeEach(()=>vi.stubGlobal("IS_REACT_ACT_ENVIRONMENT",true));
afterEach(async()=>{if(root)await act(()=>root!.unmount());root=undefined;document.body.replaceChildren();vi.unstubAllGlobals();});
function fixture(){
  const list=vi.fn(async()=>response([row])),abandon=vi.fn(async()=>success);
  const api:AppCandidateGuiApi={agent_candidate:vi.fn(),agent_candidate_read:vi.fn(),dsh_client_incompatible_applications:list,dsh_client_abandon_incompatible_application:abandon};
  return {api,list,abandon};
}
async function mount(child:React.ReactNode){const host=document.createElement("div");document.body.append(host);root=createRoot(host);await act(async()=>root!.render(child));return host;}
const button=(label:string)=>[...document.querySelectorAll("button")].find(value=>value.textContent===label)!;
async function click(label:string){await act(async()=>{button(label).click();await Promise.resolve();});}

describe("explicit user abandonment of incompatible App synchronization",()=>{
  it.each([{...row,state:"prepared"},{...row,state:"committed"},{...row,expected_fingerprint:"wrong"},{...row,conversation_id:"../other"}])("rejects unqualified or malformed list rows %j",value=>{expect(()=>parseIncompatibleApplications(response([value]))).toThrow("invalid");});
  it("does not display an empty global recovery section",async()=>{
    const s=fixture();s.list.mockResolvedValue(response([]));const host=await mount(<AppIncompatibleApplications api={s.api}/>);expect(host.textContent).toBe("");expect(s.abandon).not.toHaveBeenCalled();
  });
  it("makes old global occupancy discoverable from a new candidate with no Session binding",async()=>{
    const s=fixture(),bindings:AppDomainBindings={getSnapshot:()=>undefined,subscribe:()=>()=>undefined};const host=await mount(<CandidateWorkspace bindings={bindings} api={s.api}/>);
    expect(s.list).toHaveBeenCalledExactlyOnceWith({});expect(host.textContent).toContain("1 项旧同步待处理");expect(host.textContent).toContain("尚未绑定候选白板");expect(s.api.agent_candidate).not.toHaveBeenCalled();expect(s.abandon).not.toHaveBeenCalled();
  });
  it("requires the separate human confirmation and treats cancel as a side-effect-free close",async()=>{
    const s=fixture();await mount(<AppIncompatibleApplications api={s.api}/>);await click("处理这次旧同步");
    expect(document.querySelector('[role="dialog"]')?.textContent).toContain("原结果仍记为未知，文件和当前白板不会改变");expect(s.abandon).not.toHaveBeenCalled();await click("保留并取消");expect(document.querySelector('[role="dialog"]')).toBeNull();expect(s.abandon).not.toHaveBeenCalled();
  });
  it("submits the exact CAS decision once and preserves unknown status after confirmation",async()=>{
    const s=fixture();let release!:(value:typeof success)=>void;s.abandon.mockImplementation(()=>new Promise(resolve=>{release=resolve;}));const settled=vi.fn();await mount(<AppIncompatibleApplications api={s.api} onSettled={settled}/>);await click("处理这次旧同步");
    await act(()=>{button("确认放弃恢复并解除占用").click();button("确认放弃恢复并解除占用").click();});
    expect(s.abandon).toHaveBeenCalledExactlyOnceWith({conversation_id:id,expected_fingerprint:fingerprint,decision:"abandon_recovery"});s.list.mockResolvedValue(response([]));
    await act(async()=>{release(success);await Promise.resolve();});expect(document.body.textContent).toContain("原同步结果仍记为未知");expect(settled).toHaveBeenCalledOnce();expect(document.querySelector('[role="dialog"]')).toBeNull();
  });
  it.each(["native_incompatible_application_stale","native_incompatible_application_not_eligible"])("keeps %s local and requires fresh read before another confirmation",async reason=>{
    const s=fixture();s.abandon.mockResolvedValue({code:1,reason,message:"记录需要重新核对",operation_may_have_completed:false} as never);await mount(<AppIncompatibleApplications api={s.api}/>);await click("处理这次旧同步");await click("确认放弃恢复并解除占用");
    expect(document.body.textContent).toContain("普通聊天仍可继续");expect(button("处理这次旧同步").disabled).toBe(true);expect(s.abandon).toHaveBeenCalledOnce();await click("刷新旧同步状态");expect(button("处理这次旧同步").disabled).toBe(false);expect(s.abandon).toHaveBeenCalledOnce();
  });
  it("does not report success for a forged receipt or resolved status",async()=>{
    const s=fixture();s.abandon.mockResolvedValue({...success,data:{...success.data,state:"success",decision_receipt:"b".repeat(64)}});await mount(<AppIncompatibleApplications api={s.api}/>);await click("处理这次旧同步");await click("确认放弃恢复并解除占用");expect(document.body.textContent).toContain("没有得到成功确认");expect(document.body.textContent).not.toContain("已放弃恢复这次旧同步并解除占用");
  });
  it("drops a late global list response after the view is no longer mounted",async()=>{
    const s=fixture();let release!:(value:ReturnType<typeof response>)=>void;s.list.mockImplementation(()=>new Promise(resolve=>{release=resolve;}));const host=await mount(<AppIncompatibleApplications api={s.api}/>);
    await act(()=>root!.render(<p>另一个页面</p>));await act(async()=>{release(response([row]));await Promise.resolve();});expect(host.textContent).toBe("另一个页面");expect(s.abandon).not.toHaveBeenCalled();
  });
});
