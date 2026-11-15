import {act} from "react";
import {createRoot,type Root} from "react-dom/client";
import {beforeEach,afterEach,describe,it,expect,vi} from "vitest";
import {CandidatePreview} from "./app-candidate-preview";
import {readCandidateDocument} from "./app-domain-candidate";
let root:Root|undefined;
beforeEach(()=>vi.stubGlobal("IS_REACT_ACT_ENVIRONMENT",true));
afterEach(async()=>{if(root)await act(()=>root!.unmount());root=undefined;document.body.replaceChildren();vi.unstubAllGlobals();});
describe("read-only candidate board",()=>{
  it("renders supplied equipment/panel and projected events without touching an editor",async()=>{
    const source={kind:"genshin-whiteboard",duration:18,team:[{id:"actor",name:"测试角色",level:90,weapon:{name:"测试武器"},artifact_sets:[{name:"测试套装",count:4}],panel:{atk:1234}}]};
    const editor={projectCandidate:vi.fn(()=>({duration:18,characters:[{id:"actor",label:"测试角色"}],views:{actions:{events:[{id:"action",character:"actor",label:"元素战技",start:2,detail:{marker:"source-detail"}}]},effects:{events:[]},damage:{events:[]}}}))};
    const host=document.createElement("div");document.body.append(host);root=createRoot(host);await act(()=>root!.render(<CandidatePreview document={source} kind="whiteboard" editor={editor}/>));
    expect(host.textContent).toContain("测试武器");expect(host.textContent).toContain("测试套装 4件");expect(host.querySelector('[role="button"]')?.getAttribute("aria-label")).toContain("2.00秒 元素战技");
    await act(()=>host.querySelector<SVGGElement>('[role="button"]')!.dispatchEvent(new MouseEvent("click",{bubbles:true})));expect(host.textContent).toContain("source-detail");
    await act(()=>[...host.querySelectorAll("button")].find(button=>button.textContent==="伤害事件")!.click());expect(host.textContent).toContain("不代表计算已完成");
    expect(source.team[0]?.panel.atk).toBe(1234);expect(editor.projectCandidate).toHaveBeenCalledOnce();
  });
  it("keeps a readable candidate when a partial projection fails",async()=>{
    const host=document.createElement("div");document.body.append(host);root=createRoot(host);await act(()=>root!.render(<CandidatePreview document={{team:[{name:"仍可读取"}]}} kind="whiteboard" editor={{projectCandidate:()=>{throw new Error("partial");}}}/>));
    expect(host.textContent).toContain("部分输入尚不能形成时间轴");expect(host.querySelector("svg")).not.toBeNull();
  });
});
describe("candidate snapshot references",()=>{
  const binding={dshSessionId:"session",conversationId:"conversation",projectionRevision:2,availableCommands:[]};
  function api(){return {agent_candidate:vi.fn(async()=>({code:0,data:{kind:"whiteboard",revision:3,receipt_fingerprint:null}})),agent_candidate_read:vi.fn(async(_id:string,resource:string,pointer:string,offset:number)=>({code:0,data:pointer===""?{resource,pointer,offset,kind:"object",total:1,items:[{key:"actions",value:{reference:{resource,pointer:"/actions"},kind:"array",length:2}}],next_offset:null}:{resource,pointer,offset,kind:"array",total:2,items:[{key:offset,value:{id:`a${offset}`,start:offset}}],next_offset:offset===0?1:null}}))};}
  it("hydrates exact Host references across pages and keeps prototype keys as data",async()=>{const host=api(),value=await readCandidateDocument(host,binding,"candidate",new AbortController().signal);expect(value.document.actions).toEqual([{id:"a0",start:0},{id:"a1",start:1}]);expect(host.agent_candidate).toHaveBeenCalledTimes(2);expect(host.agent_candidate_read).toHaveBeenCalledTimes(3);});
  it("does not follow an Agent-supplied reference to an unrelated pointer",async()=>{const host=api();host.agent_candidate_read.mockResolvedValueOnce({code:0,data:{resource:"candidate",pointer:"",offset:0,kind:"object",total:1,items:[{key:"actions",value:{reference:{resource:"candidate",pointer:"/private"},kind:"array",length:2}}],next_offset:null}});const value=await readCandidateDocument(host,binding,"candidate",new AbortController().signal);expect(host.agent_candidate_read).toHaveBeenCalledOnce();expect(value.document.actions).toHaveProperty("reference.pointer","/private");});
  it("rejects a snapshot changed while hydrating instead of mixing versions",async()=>{const host=api();host.agent_candidate.mockResolvedValueOnce({code:0,data:{kind:"whiteboard",revision:2,receipt_fingerprint:null}});await expect(readCandidateDocument(host,binding,"candidate",new AbortController().signal)).rejects.toThrow("Agent 已更新候选");});
  it("reads current calculated records under the same candidate receipt without granting review",async()=>{
    const host=api();host.agent_candidate.mockResolvedValue({code:0,data:{kind:"whiteboard",revision:3,receipt_fingerprint:"a".repeat(64),has_calculation:true}} as never);
    const original=host.agent_candidate_read;host.agent_candidate_read=vi.fn(async(id,resource,pointer,offset)=>resource==="calculated"?{code:0,data:{resource,pointer,offset,kind:"object",total:1,items:[{key:"result",value:{summary:{total_damage:123}}}],next_offset:null}}:original(id,resource,pointer,offset));
    const value=await readCandidateDocument(host,binding,"candidate",new AbortController().signal);expect(value.calculated).toEqual({result:{summary:{total_damage:123}}});expect(value.summary.hasCalculation).toBe(true);expect(binding.availableCommands).toEqual([]);expect(host.agent_candidate).toHaveBeenCalledTimes(2);
  });
});
