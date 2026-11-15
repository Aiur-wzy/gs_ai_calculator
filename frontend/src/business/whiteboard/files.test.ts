import {afterEach,describe,expect,it,vi} from "vitest";
import {createWhiteboardFiles} from "./files";

afterEach(()=>{delete window.GenshinCandidateDraftFiles;});
function fixture(kind="genshin-whiteboard"){
  const state={board:{board_id:"a",revision:0,notes:"initial"},request:0,path:"initial.json",fileSha:"initial-sha"};
  let resolve!:(value:unknown)=>void;
  const inspected=new Promise(resolveValue=>{resolve=resolveValue;});
  const owner={state,json:()=>JSON.stringify(state.board),
    token(){return {board:state.board,json:JSON.stringify(state.board),request:++state.request,path:state.path,sha:state.fileSha};},
    current(token:any){return token.board===state.board&&token.json===JSON.stringify(state.board)&&token.request===state.request&&token.path===state.path&&token.sha===state.fileSha;},
    leave:vi.fn(async()=>true),replace:vi.fn((data:any)=>{state.board=data.board;})};
  const api=vi.fn(async(name:string)=>name==="inspect_plan_file"?inspected:{code:0,data:{board:{board_id:"loaded",revision:0,notes:"from file"}}});
  const ui={error:vi.fn(),tabs:vi.fn()},status=vi.fn();
  const files=createWhiteboardFiles({owner,ui,api,status,query:vi.fn(),document:{},events:{},plansDir:()=>"plans",confirm:()=>true,clipboard:()=>null,services:{}});
  return {files,state,owner,api,status,ui,finish:()=>resolve({code:0,data:{kind,file_sha:"a".repeat(64)}})};
}
describe("whiteboard file-kind inspection keeps the original document fence",()=>{
  for(const kind of ["genshin-whiteboard","genshin-candidate-input-draft"])for(const mutation of ["new-document","same-id-revision","dirty-edit","new-request","saved-file"]){
    it(`rejects late ${kind} inspection after ${mutation}`,async()=>{
      const state=fixture(kind),openSelected=vi.fn(async()=>({opened:true,message:"candidate opened"}));window.GenshinCandidateDraftFiles={openSelected};
      const pending=state.files.load("user-selected.json");
      if(mutation==="new-document")state.state.board={board_id:"b",revision:0,notes:"new clean board"};
      if(mutation==="same-id-revision")state.state.board.revision=1;
      if(mutation==="dirty-edit")state.state.board.notes="new unsaved work";
      if(mutation==="new-request")state.owner.token();
      if(mutation==="saved-file")state.state.fileSha="a new saved SHA";
      const current=JSON.stringify(state.state.board);state.finish();
      expect(await pending).toBe(false);expect(JSON.stringify(state.state.board)).toBe(current);
      expect(state.owner.leave).not.toHaveBeenCalled();expect(state.owner.replace).not.toHaveBeenCalled();expect(openSelected).not.toHaveBeenCalled();
      expect(state.api.mock.calls.map(([name])=>name)).toEqual(["inspect_plan_file"]);
    });
  }
  it("keeps the existing dirty/leave decision before whiteboard_load",async()=>{
    const state=fixture();state.owner.leave.mockResolvedValue(false);
    const pending=state.files.load("user-selected.json");state.finish();
    expect(await pending).toBe(false);expect(state.owner.leave).toHaveBeenCalledOnce();
    expect(state.api.mock.calls.map(([name])=>name)).toEqual(["inspect_plan_file"]);expect(state.owner.replace).not.toHaveBeenCalled();
  });
  it("loads an unchanged whiteboard only after the existing leave guard",async()=>{
    const state=fixture();const pending=state.files.load("user-selected.json");state.finish();
    expect(await pending).toBe(true);expect(state.owner.leave).toHaveBeenCalledOnce();expect(state.owner.replace).toHaveBeenCalledOnce();
    expect(state.api.mock.calls.map(([name])=>name)).toEqual(["inspect_plan_file","whiteboard_load"]);
  });
});
