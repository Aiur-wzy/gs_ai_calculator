import {act} from "react";
import {createRoot,type Root} from "react-dom/client";
import {afterEach,beforeEach,describe,expect,it,vi} from "vitest";
import {AppRuntimeSettings,createAppRuntimeSettingsPlugin,type AppRuntimeSettingsApi} from "./app-runtime-settings";
let root:Root|undefined;
beforeEach(()=>vi.stubGlobal("IS_REACT_ACT_ENVIRONMENT",true));
afterEach(async()=>{if(root)await act(()=>root!.unmount());root=undefined;document.body.replaceChildren();vi.unstubAllGlobals();});
const budget={generation:4,defaults:{wall_clock_seconds:7200,retry_count:0},effective:{wall_clock_seconds:7200,retry_count:0},overrides:{},limits:{wall_clock_seconds:{min:60,max:86400,adjustable:true},retry_count:{min:0,max:0,adjustable:false}},apply_scope:"next_turn"};
const dsh={generation:3,defaults:{compaction:{auto:true,headroomTokens:65536,modelPolicies:[]}},effective:{compaction:{auto:true,headroomTokens:65536,modelPolicies:[]}},overrides:{},limits:{compaction:{auto:{type:"boolean"},headroomTokens:{type:"integer",min:0,max:9007199254740991},modelPolicies:{type:"model_policy_array"}}},apply_scope:"next_runtime"};
function fixture(){return {agent_budget_settings:vi.fn(async()=>({code:0,data:budget})),agent_update_budget_settings:vi.fn(async(_overrides:Record<string,unknown>,_generation:number)=>({code:0,data:{...budget,generation:5}})),agent_reset_budget_settings:vi.fn(async(_generation:number)=>({code:0,data:{...budget,generation:5}})),agent_dsh_settings:vi.fn(async()=>({code:0,data:dsh})),agent_update_dsh_settings:vi.fn(async(_overrides:Record<string,unknown>,_generation:number)=>({code:0,data:{...dsh,generation:4}})),agent_reset_dsh_settings:vi.fn(async(_generation:number)=>({code:0,data:{...dsh,generation:4}}))};}
async function mount(api:AppRuntimeSettingsApi){const host=document.createElement("div");document.body.append(host);root=createRoot(host);await act(async()=>root!.render(<AppRuntimeSettings api={api}/>));return host;}
function enter(input:HTMLInputElement|HTMLTextAreaElement,value:string){const proto=input.tagName==="TEXTAREA"?HTMLTextAreaElement.prototype:HTMLInputElement.prototype;Object.getOwnPropertyDescriptor(proto,"value")!.set!.call(input,value);input.dispatchEvent(new Event("input",{bubbles:true}));}
describe("App advanced runtime settings",()=>{
  it("uses maintained defaults and only writes explicitly selected budget overrides with generation CAS",async()=>{
    const api=fixture(),host=await mount(api);expect(api.agent_budget_settings).toHaveBeenCalledOnce();expect(host.querySelector('[aria-label="运行时间（秒）"]')?.getAttribute("max")).toBe("86400");expect(host.textContent).not.toContain("retry_count");
    await act(()=>host.querySelector<HTMLInputElement>('[aria-label="自定义运行时间（秒）"]')!.click());await act(()=>enter(host.querySelector('[aria-label="运行时间（秒）"]')!,"10800"));await act(async()=>host.querySelector("form")!.dispatchEvent(new Event("submit",{bubbles:true,cancelable:true})));
    expect(api.agent_update_budget_settings).toHaveBeenCalledExactlyOnceWith({wall_clock_seconds:10800},4);expect(host.textContent).toContain("下一轮任务生效");expect(api.agent_dsh_settings).not.toHaveBeenCalled();
  });
  it("registers only App budget controls; official DSH ConfigEditor owns DSH configuration",()=>{
    const entries:Array<{id:string;label:string}>=[];
    createAppRuntimeSettingsPlugin(fixture()).apply({slots:{inject:(_name:string,callback:()=>unknown)=>callback(),register:(entry:{id:string;label:string})=>entries.push(entry)}} as never);
    expect(entries).toEqual([{name:"settings.section",id:"genshin-budget-settings",label:"任务预算",order:130}]);
  });
  it("resets only after a real user click and does not retry generation conflicts",async()=>{
    const api=fixture(),host=await mount(api);api.agent_reset_budget_settings.mockResolvedValue({code:1,message:"设置已在其他窗口更新，请重新读取"} as never);
    await act(async()=>[...host.querySelectorAll("button")].find(row=>row.textContent==="恢复维护默认值")!.click());expect(api.agent_reset_budget_settings).toHaveBeenCalledExactlyOnceWith(4);expect(host.querySelector('[role="alert"]')?.textContent).toContain("其他窗口更新");expect(api.agent_update_budget_settings).not.toHaveBeenCalled();
  });
});
