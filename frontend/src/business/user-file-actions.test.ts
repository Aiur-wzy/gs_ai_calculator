import {afterEach,describe,expect,it,vi} from "vitest";
import {createWorkspaceFileActions,openInspectedDocument} from "./user-file-actions";

afterEach(()=>{delete window.GenshinCandidateDraftFiles;});
describe("ordinary file open routes candidate drafts",()=>{
  it("routes by kind through the candidate port and never loads/replaces the formal board",async()=>{
    const opened=vi.fn(async()=>({opened:true,message:"候选输入草稿已打开"}));window.GenshinCandidateDraftFiles={openSelected:opened};
    const formal={kind:"genshin-whiteboard",title:"用户尚未保存的白板"};
    const board={state:{board:formal},json:()=>JSON.stringify(formal),pickUserFile:vi.fn(async()=>({path:"C:/user/custom-name.json",cancelled:false})),load:vi.fn()};
    const editor={tpl:()=>null,state:{path:null},loadTemplate:vi.fn()};
    const workspace={isBoard:()=>true,refresh:vi.fn(),navigate:vi.fn()};
    const api=vi.fn(async()=>({code:0,data:{kind:"genshin-candidate-input-draft",file_sha:"a".repeat(64)}}));
    const files=createWorkspaceFileActions({board,editor,workspace,api,query:vi.fn(),serialize:JSON.stringify,status:vi.fn(),error:vi.fn()});
    expect(await files.openFile()).toBe(true);
    expect(opened).toHaveBeenCalledExactlyOnceWith("C:/user/custom-name.json","a".repeat(64),{isCurrent:expect.any(Function)});
    expect(board.load).not.toHaveBeenCalled();expect(editor.loadTemplate).not.toHaveBeenCalled();expect(board.state.board).toBe(formal);
  });
  it("does not fall back to a native plan loader if the candidate UI port is absent",async()=>{
    const board={state:{board:null},json:()=>"",pickUserFile:async()=>({path:"draft.json"}),load:vi.fn()};
    const editor={tpl:()=>null,state:{path:null},loadTemplate:vi.fn()},status=vi.fn();
    const files=createWorkspaceFileActions({board,editor,workspace:{isBoard:()=>false,refresh:vi.fn()},api:async()=>({code:0,data:{kind:"genshin-candidate-input-draft",file_sha:"a".repeat(64)}}),query:vi.fn(),serialize:JSON.stringify,status,error:vi.fn()});
    expect(await files.openFile()).toBe(false);expect(editor.loadTemplate).not.toHaveBeenCalled();expect(status).toHaveBeenCalledWith(expect.stringContaining("支持原生 AI 的客户端"),undefined);
  });
});

it("keeps an already opened native plan when its page fails to mount",async()=>{
  const editor={loadTemplate:vi.fn(async()=>true)},board={load:vi.fn()},report={openSavedReport:vi.fn()};
  const show=vi.fn(async()=>{throw new Error("mount_failed");});
  const opened=await openInspectedDocument("C:/user/current-plan.json",{kind:"native_plan"},{current:()=>true},{board,editor,report,show});
  expect(opened.opened).toBe(true);expect(opened.message).toContain("已打开所选计算方案");expect(opened.message).toContain("未能切换页面");
  expect(editor.loadTemplate).toHaveBeenCalledOnce();expect(show).toHaveBeenCalledExactlyOnceWith("rotation");
  expect(board.load).not.toHaveBeenCalled();expect(report.openSavedReport).not.toHaveBeenCalled();
});

it.each(["genshin-whiteboard","genshin-plan"])("preserves the structured %s loader failure without navigating",async kind=>{
  const failure={code:1,message:"所选文件缺少必要的数据，当前方案已保留。",details:{reason:"fixture_schema_invalid",field:"actions"}};
  const load=vi.fn(async(_path,options)=>{options.onError(failure);return false;}),show=vi.fn();
  const opened=await openInspectedDocument("broken.json",{kind},{current:()=>true},{board:{load},editor:{loadTemplate:load},show});
  expect(opened).toEqual({opened:false,failed:true,message:failure.message,details:failure.details});
  expect(show).not.toHaveBeenCalled();expect(load).toHaveBeenCalledOnce();
});

it("distinguishes a declined open from a reported loader error",async()=>{
  const opened=await openInspectedDocument("cancelled.json",{kind:"genshin-whiteboard"},{current:()=>true},{board:{load:async()=>false},editor:{},show:vi.fn()});
  expect(opened.opened).toBe(false);expect(opened.failed).toBeUndefined();expect(opened.details).toBeUndefined();
});

it("keeps an inspected-file rejection and its details in the status and the completion callback",async()=>{
  const failure={code:1,message:"所选文件不是支持的计算方案。",details:{reason:"invalid_plan_file"}};
  const board={state:{board:null},json:()=>"",pickUserFile:async()=>({path:"wrong.json"}),load:vi.fn()};
  const editor={tpl:()=>null,state:{path:null},loadTemplate:vi.fn()},status=vi.fn(),onAgentActionResult=vi.fn();
  const files=createWorkspaceFileActions({board,editor,workspace:{isBoard:()=>false,refresh:vi.fn()},
    api:async()=>failure,query:vi.fn(),serialize:JSON.stringify,status,error:vi.fn()});
  expect(await files.openFile({onAgentActionResult})).toBe(false);
  expect(status).toHaveBeenCalledExactlyOnceWith(failure.message,failure.details);
  expect(onAgentActionResult).toHaveBeenCalledExactlyOnceWith({status:"failed",message:failure.message});
  expect(editor.loadTemplate).not.toHaveBeenCalled();
});
