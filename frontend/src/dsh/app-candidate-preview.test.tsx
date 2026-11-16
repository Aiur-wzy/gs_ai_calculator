import {act} from "react";
import {createRoot,type Root} from "react-dom/client";
import {beforeEach,afterEach,describe,it,expect,vi} from "vitest";
import {CandidatePreview} from "./app-candidate-preview";
import {readCandidateDocument} from "./app-domain-candidate";
import * as windowPresentation from "./app-window-presentation";
let root:Root|undefined;
beforeEach(()=>vi.stubGlobal("IS_REACT_ACT_ENVIRONMENT",true));
afterEach(async()=>{if(root)await act(()=>root!.unmount());root=undefined;document.body.replaceChildren();delete document.body.dataset.agentWindow;vi.restoreAllMocks();vi.unstubAllGlobals();});
describe("read-only candidate board",()=>{
  it("keeps overlapping effects and same-time hits available to mouse and keyboard",async()=>{
    const source={kind:"genshin-whiteboard",team:[{id:"a",name:"角色"}]};
    const editor={projectCandidate:()=>({duration:20,characters:[{id:"a",label:"角色"}],views:{effects:{events:[
      {id:"long",character:"a",start:1,end:15,label:"长增益",detail:{example:"长增益来源"}},
      {id:"short",character:"a",start:4,end:5,label:"短增益",detail:{example:"短增益来源"}},
      {id:"trigger",character:"a",start:4,label:"同时触发"},
    ]},damage:{events:[{id:"hit-1",character:"a",start:4,value:100,label:"第一段"},{id:"hit-2",character:"a",start:4,value:200,label:"第二段"}]}}})};
    const host=document.createElement("div");document.body.append(host);root=createRoot(host);
    await act(()=>root!.render(<CandidatePreview document={source} kind="whiteboard" editor={editor}/>));
    const clickButton=(label:string)=>act(()=>[...host.querySelectorAll("button")].find(button=>button.textContent?.includes(label))!.click());
    const marks=()=>[...host.querySelectorAll<SVGGElement>('svg g[role="button"]')];
    const group=()=>marks().find(mark=>mark.getAttribute("aria-label")?.includes("2 条记录"))!;
    await clickButton("效果生效");
    expect(host.querySelector('svg')?.getAttribute('role')).toBe('group');
    expect(marks()).toHaveLength(4);
    const trigger=marks().find(mark=>mark.getAttribute("aria-label")?.includes("同时触发"))!;
    expect(trigger.querySelector('rect')?.getAttribute('y')).not.toBe(group().querySelector('rect')?.getAttribute('y'));
    await act(()=>group().dispatchEvent(new MouseEvent("click",{bubbles:true})));
    await clickButton("长增益");expect(host.querySelector(".gs-candidate-event-detail")?.textContent).toContain("长增益来源");
    await clickButton("短增益");expect(host.querySelector(".gs-candidate-event-detail")?.textContent).toContain("短增益来源");
    await clickButton("伤害事件");expect(marks()).toHaveLength(1);
    await act(()=>group().dispatchEvent(new KeyboardEvent("keydown",{key:"Enter",bubbles:true,cancelable:true})));
    await clickButton("第一段");expect(host.querySelector(".gs-candidate-event-detail")?.textContent).toContain("本次伤害 100");
    await clickButton("第二段");expect(host.querySelector(".gs-candidate-event-detail")?.textContent).toContain("本次伤害 200");
  });
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
  it("passes known calculation availability separately from unloaded result data",async()=>{
    const source={kind:"genshin-whiteboard"},editor={projectCandidate:vi.fn(()=>({duration:20,characters:[],views:{actions:{events:[]},damage:{events:[]}}}))};
    const host=document.createElement("div");document.body.append(host);root=createRoot(host);
    await act(()=>root!.render(<CandidatePreview document={source} kind="whiteboard" hasCalculation editor={editor}/>));
    expect(editor.projectCandidate).toHaveBeenLastCalledWith(source,"whiteboard",undefined,{hasCalculation:true});
    await act(()=>[...host.querySelectorAll("button")].find(button=>button.textContent==="伤害事件")!.click());
    expect(host.textContent).toContain("尚未载入已计算的事件");expect(host.textContent).not.toContain("不代表计算已完成");
    await act(()=>root!.render(<CandidatePreview document={source} kind="whiteboard" hasCalculation={false} editor={editor}/>));
    expect(host.textContent).toContain("不代表计算已完成");
  });
  it.each([false,true])("uses exact damage/result/owner reference; rejected=%s is shown without modifying candidate",async(rejected)=>{
    const source={kind:"genshin-whiteboard",board_id:"b",revision:5,team:[{id:"actor",name:"角色"}]},before=JSON.stringify(source);
    const editor={projectCandidate:()=>({duration:18,characters:[{id:"actor",label:"角色"}],explanationFingerprint:"result-sha",views:{actions:{events:[]},damage:{events:[{id:"row-id",character:"actor",start:2,detail:{explanation_ref:"hit:original"}}]}}}),
      revealTimelineEvent:vi.fn(async()=>{if(rejected)throw new Error("同一版结果未在主窗口打开");return {status:"located" as const,message:"已定位；未修改方案"};})};
    const host=document.createElement("div");document.body.append(host);root=createRoot(host);await act(()=>root!.render(<CandidatePreview document={source} kind="whiteboard" editor={editor}/>));
    await act(()=>[...host.querySelectorAll("button")].find(button=>button.textContent==="伤害事件")!.click());
    await act(()=>host.querySelector<SVGGElement>('g[role="button"]')!.dispatchEvent(new MouseEvent("click",{bubbles:true})));
    await act(async()=>{[...host.querySelectorAll("button")].find(button=>button.textContent==="在主窗口定位这段伤害")!.click();await Promise.resolve();});
    expect(editor.revealTimelineEvent).toHaveBeenCalledWith({contract:"timeline-selection-v1",owner:"whiteboard:b:5",result:"result-sha",view:"damage",event:"row-id",explanation:"hit:original"});
    expect(host.textContent).toContain(rejected?"同一版结果未在主窗口打开":"已定位；未修改方案");expect(JSON.stringify(source)).toBe(before);
  });
  it("reports missing identity rather than guessing the matching damage name",async()=>{
    const editor={projectCandidate:()=>({duration:18,characters:[],views:{damage:{events:[{id:"row",start:2,label:"同名伤害"}]}}}),revealTimelineEvent:vi.fn()};
    const host=document.createElement("div");document.body.append(host);root=createRoot(host);await act(()=>root!.render(<CandidatePreview document={{kind:"genshin-whiteboard"}} kind="whiteboard" editor={editor}/>));
    await act(()=>[...host.querySelectorAll("button")].find(button=>button.textContent==="伤害事件")!.click());
    await act(()=>host.querySelector<SVGGElement>('g[role="button"]')!.dispatchEvent(new MouseEvent("click",{bubbles:true})));
    expect(host.textContent).toContain("缺少完整方案或结果身份");expect(host.textContent).not.toContain("在主窗口定位这段伤害");expect(editor.revealTimelineEvent).not.toHaveBeenCalled();
  });
  it("shows the matching main-window acknowledgement, not another command's outcome",async()=>{
    document.body.dataset.agentWindow="detached";
    const state:windowPresentation.WindowState={role:"detached",generation:1,presentation:"detached",handoff:null};
    vi.spyOn(windowPresentation,"useWindowPresentation").mockImplementation(()=>state);
    const send=vi.spyOn(windowPresentation,"presentInMain").mockResolvedValue({command_id:"request-1"});
    const source={kind:"genshin-whiteboard",board_id:"b",revision:5},editor={projectCandidate:()=>({duration:18,characters:[],explanationFingerprint:"result-sha",views:{actions:{events:[]},damage:{events:[{id:"row",start:2}]}}})};
    const host=document.createElement("div");document.body.append(host);root=createRoot(host);
    const render=()=>root!.render(<CandidatePreview document={source} kind="whiteboard" editor={editor} sessionId="dsh-session" conversationId="app-conversation"/>);
    await act(render);await act(()=>[...host.querySelectorAll("button")].find(button=>button.textContent==="伤害事件")!.click());
    await act(()=>host.querySelector<SVGGElement>('g[role="button"]')!.dispatchEvent(new MouseEvent("click",{bubbles:true})));
    await act(async()=>{[...host.querySelectorAll("button")].find(button=>button.textContent==="在主窗口定位这段伤害")!.click();await Promise.resolve();});
    expect(send).toHaveBeenCalledWith(expect.objectContaining({command:"reveal_timeline_event",session_id:"dsh-session",conversation_id:"app-conversation"}));
    expect(host.textContent).toContain("已请求主窗口定位");
    state.command_result={command_id:"request-other",status:"located",message:"其他命令的成功"};await act(render);
    expect(host.textContent).not.toContain("其他命令的成功");
    state.command_result={command_id:"request-1",status:"stale",message:"结果已经变化，未修改白板"};await act(render);
    expect(host.textContent).toContain("结果已经变化，未修改白板");
  });
  it("does not show an old locating response under a newly selected hit",async()=>{
    let finish!:(value:{status:"located";message:string})=>void;
    const source={kind:"genshin-whiteboard",board_id:"b",revision:5},editor={projectCandidate:()=>({duration:18,characters:[],explanationFingerprint:"result-sha",views:{actions:{events:[]},damage:{events:[{id:"first",start:2},{id:"second",start:3}]}}}),revealTimelineEvent:()=>new Promise<{status:"located";message:string}>(resolve=>{finish=resolve;})};
    const host=document.createElement("div");document.body.append(host);root=createRoot(host);await act(()=>root!.render(<CandidatePreview document={source} kind="whiteboard" editor={editor}/>));
    await act(()=>[...host.querySelectorAll("button")].find(button=>button.textContent==="伤害事件")!.click());
    const marks=host.querySelectorAll<SVGGElement>('g[role="button"]');
    await act(()=>marks[0]!.dispatchEvent(new MouseEvent("click",{bubbles:true})));
    await act(async()=>{[...host.querySelectorAll("button")].find(button=>button.textContent==="在主窗口定位这段伤害")!.click();await Promise.resolve();});
    await act(()=>marks[1]!.dispatchEvent(new MouseEvent("click",{bubbles:true})));
    await act(async()=>{finish({status:"located",message:"第一段已经定位"});await Promise.resolve();});
    expect(host.querySelector(".gs-candidate-event-detail")?.textContent).toContain("3.00秒");expect(host.textContent).not.toContain("第一段已经定位");
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
