import {act} from "react";
import {createRoot,type Root} from "react-dom/client";
import {afterEach,beforeEach,describe,expect,it,vi} from "vitest";
import {WindowsCredentialSettings,type AppCredentialHostApi} from "./app-credential-settings";
let root:Root|undefined;
beforeEach(()=>vi.stubGlobal("IS_REACT_ACT_ENVIRONMENT",true));
afterEach(async()=>{if(root)await act(()=>root!.unmount());root=undefined;document.body.replaceChildren();vi.unstubAllGlobals();});
function fixture(){
  const api={dsh_client_credential_status:vi.fn(async()=>({available:true,saved:false,status:"ready",credential_generation:7})),
    dsh_client_credential_save:vi.fn(async(_request:{api_key:string;expected_generation:number})=>({available:true,saved:true,status:"ready",credential_generation:8,runtime_sync:{state:"pending_runtime"}})),
    dsh_client_credential_delete:vi.fn(async()=>({available:true,saved:false,status:"ready",credential_generation:9,runtime_sync:{state:"pending_runtime"}})),
    dsh_client_runtime_status:vi.fn(async()=>({state:"stopped"}))};
  return api;
}
async function mount(api:AppCredentialHostApi){const host=document.createElement("div");document.body.append(host);root=createRoot(host);await act(async()=>{root!.render(<WindowsCredentialSettings api={api}/>);});return host;}
function enter(input:HTMLInputElement,value:string){Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,"value")!.set!.call(input,value);input.dispatchEvent(new Event("input",{bubbles:true}));}
describe("App Windows credential settings",()=>{
  it("reads only local status and leaves stored secrets masked/absent",async()=>{const api=fixture(),host=await mount(api);expect(api.dsh_client_credential_status).toHaveBeenCalledOnce();expect(api.dsh_client_runtime_status).not.toHaveBeenCalled();expect(api.dsh_client_credential_save).not.toHaveBeenCalled();expect(host.querySelector("input")?.type).toBe("password");expect(host.textContent).toContain("尚未保存密钥");});
  it("uses the exact Host generation and clears the input on dispatch",async()=>{
    const api=fixture(),host=await mount(api);let captured:unknown;api.dsh_client_credential_save.mockImplementation(async request=>{captured={...request};return {available:true,saved:true,status:"ready",credential_generation:8,runtime_sync:{state:"pending_runtime"}};});
    await act(()=>enter(host.querySelector("input")!,"fixture-key"));await act(async()=>{host.querySelector("form")!.dispatchEvent(new Event("submit",{bubbles:true,cancelable:true}));});
    expect(captured).toEqual({api_key:"fixture-key",expected_generation:7});expect(host.querySelector("input")?.value).toBe("");expect(host.textContent).toContain("密钥已安全保存到 Windows");expect(host.textContent).not.toContain("fixture-key");
  });
  it("distinguishes durable save from failed live-runtime synchronization",async()=>{
    const api=fixture();api.dsh_client_credential_save.mockResolvedValue({available:true,saved:true,status:"ready",credential_generation:8,runtime_sync:{state:"failed"}});
    const host=await mount(api);await act(()=>enter(host.querySelector("input")!,"fixture-key"));await act(async()=>{host.querySelector("form")!.dispatchEvent(new Event("submit",{bubbles:true,cancelable:true}));});
    expect(host.querySelector('[role="alert"]')?.textContent).toContain("Windows 中的密钥已更新");expect(host.textContent).toContain("当前 AI 服务未同步");
  });
  it("does not expose provider exception text or secret arguments",async()=>{
    const api=fixture();api.dsh_client_credential_save.mockRejectedValue(new Error("fixture-key secret-rejected"));const host=await mount(api);
    await act(()=>enter(host.querySelector("input")!,"fixture-key"));await act(async()=>{host.querySelector("form")!.dispatchEvent(new Event("submit",{bubbles:true,cancelable:true}));});
    expect(host.querySelector('[role="alert"]')?.textContent).toContain("密钥未能更新");expect(host.innerHTML).not.toContain("secret-rejected");expect(host.innerHTML).not.toContain("fixture-key");
  });
});
