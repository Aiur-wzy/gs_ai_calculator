import {act} from "react";
import {createRoot,type Root} from "react-dom/client";
import {afterEach,beforeEach,describe,expect,it,vi} from "vitest";
import {AppDomainHostError,appDomainIncompatibility,canContinueWithoutAppDomain} from "./app-domain-errors";
import {AppDomainIncompatibilityNotice} from "./app-domain-incompatibility";

export const migrationPrompt="请按当前版本重新核查我提供的旧文件，重新计算并提交候选，不要沿用旧审核凭据。";
function failure(reason="native_app_binding_compatibility_changed",overrides:Record<string,unknown>={}){
  return new AppDomainHostError(reason,"候选版本不兼容",{
    contract:"genshin-native-domain-incompatibility-v1",state:reason.endsWith("missing")?"missing":"changed",
    differences:[{component:"skill_bundle_hash",recorded:"a".repeat(64),current:"b".repeat(64)}],migration_prompt:migrationPrompt,...overrides,
  },false);
}
let root:Root|undefined;
beforeEach(()=>vi.stubGlobal("IS_REACT_ACT_ENVIRONMENT",true));
afterEach(async()=>{if(root)await act(()=>root!.unmount());root=undefined;document.body.replaceChildren();vi.unstubAllGlobals();});
async function mount(error:unknown){const host=document.createElement("div");document.body.append(host);root=createRoot(host);await act(()=>root!.render(<AppDomainIncompatibilityNotice error={error}/>));return host;}

describe("App domain compatibility facts and presentation",()=>{
  it.each(["native_app_binding_compatibility_missing","native_app_binding_compatibility_changed"])("permits generic DSH conversation only for explicit known %s",reason=>{
    const error=failure(reason);expect(canContinueWithoutAppDomain(error)).toBe(true);expect(appDomainIncompatibility(error)?.migrationPrompt).toBe(migrationPrompt);
    expect(canContinueWithoutAppDomain(new AppDomainHostError(reason,"",error.details,true))).toBe(false);
  });
  it("never treats unknown errors or suggestive diagnostics as a known domain absence",()=>{
    expect(canContinueWithoutAppDomain(failure("native_workspace_invalid"))).toBe(false);expect(appDomainIncompatibility(failure("database_missing"))).toBeUndefined();
  });
  it.each([{contract:"unknown"},{state:"missing"},{differences:[{component:"unknown",recorded:1,current:2}]},{differences:[{component:"skill_bundle_hash",current:"b"}]}])("does not offer a migration command from malformed details %j",details=>{
    const value=appDomainIncompatibility(failure(undefined,details));expect(value?.detailsValid).toBe(false);expect(value?.migrationPrompt).toBeUndefined();
  });
  it("keeps ordinary chat available and copies a user prompt without running any domain action",async()=>{
    const writeText=vi.fn(async()=>undefined);Object.defineProperty(navigator,"clipboard",{configurable:true,value:{writeText}});
    const host=await mount(failure());expect(host.textContent).toContain("聊天、历史和资料搜索仍可使用");expect(host.textContent).toContain("拉表说明");
    const buttons=[...host.querySelectorAll("button")];expect(buttons.map(value=>value.textContent)).toEqual(["复制适配提示"]);expect(writeText).not.toHaveBeenCalled();
    await act(async()=>{buttons[0]!.click();await Promise.resolve();});expect(writeText).toHaveBeenCalledExactlyOnceWith(migrationPrompt);expect(host.textContent).toContain("已复制适配提示");
    expect(host.textContent).not.toContain("自动适配");expect(host.querySelector("textarea")).toBeNull();
  });
  it("explains missing proof without stamping the old candidate and preserves an unknown operation warning",async()=>{
    const base=failure("native_app_binding_compatibility_missing",{differences:[{component:"app_artifact_identity_hash",recorded:null,current:"b".repeat(64)}]});
    const host=await mount(new AppDomainHostError(base.code,base.message,base.details,true));
    expect(host.textContent).toContain("旧候选没有可核验的版本记录");expect(host.textContent).toContain("旧记录缺失");expect(host.textContent).toContain("先前操作的结果尚未确认");
  });
  it("offers manual copy text if clipboard access fails",async()=>{
    Object.defineProperty(navigator,"clipboard",{configurable:true,value:{writeText:vi.fn(async()=>{throw new Error("denied");})}});
    const host=await mount(failure());await act(async()=>{host.querySelector("button")!.click();await Promise.resolve();});expect(host.textContent).toContain("请选中上面的提示手动复制");
  });
  it("does not mark another incompatibility prompt copied when an earlier clipboard operation resolves late",async()=>{
    let release!:()=>void;Object.defineProperty(navigator,"clipboard",{configurable:true,value:{writeText:vi.fn(()=>new Promise<void>(resolve=>{release=resolve;}))}});
    const host=await mount(failure());await act(()=>host.querySelector("button")!.click());
    await act(()=>root!.render(<AppDomainIncompatibilityNotice error={failure("native_app_binding_compatibility_missing")}/>));
    await act(async()=>{release();await Promise.resolve();});expect(host.querySelector("button")!.textContent).toBe("复制适配提示");
  });
});
