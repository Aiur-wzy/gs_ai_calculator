import {act} from "react";
import {createRoot,type Root} from "react-dom/client";
import {afterEach,beforeEach,describe,expect,it,vi} from "vitest";
import {WindowsCredentialSettings} from "./app-credential-settings";
let root:Root|undefined;
beforeEach(()=>vi.stubGlobal("IS_REACT_ACT_ENVIRONMENT",true));
afterEach(async()=>{if(root)await act(()=>root!.unmount());root=undefined;document.body.replaceChildren();vi.unstubAllGlobals();});
describe("App protected credential guidance",()=>{
  it("directs credentials and search to their upstream owners without creating a second secret editor",async()=>{
    const fetch=vi.fn(()=>{throw Error("settings must not request a model");});vi.stubGlobal("fetch",fetch);
    const host=document.createElement("div");document.body.append(host);root=createRoot(host);
    await act(async()=>{root!.render(<WindowsCredentialSettings/>);});
    expect(host.textContent).toContain("模型");expect(host.textContent).toContain("搜索服务");
    expect(host.textContent).toContain("加密保存在本 App 的资料目录");
    expect(host.textContent).toContain("卸载不会删除");
    expect(host.querySelector("input,form")).toBeNull();expect(fetch).not.toHaveBeenCalled();
  });
});
