import {readFileSync} from "node:fs";
import {resolve} from "node:path";
import vm from "node:vm";
import {describe,it,expect,vi} from "vitest";
import {createTimelineNavigator,timelineDocumentOwner} from "./navigation";
import {selectionReference} from "./evidence-index";
import {TIMELINE_VIEW_HEIGHT} from "./geometry";
const app=readFileSync(resolve(process.cwd(),"../src/genshin_app/gui/web/app.js"),"utf8");
const method=app.slice(app.indexOf("    async revealTimelineEvent(selection){"),app.indexOf("    application:window.CandidateEditorApplication"));
function fixture(){
  const event={id:"hit-row",start:5,detail:{explanation_ref:"hit:original"}},projection={explanationFingerprint:"result-sha",views:{damage:{events:[event]}}};
  const board={board_id:"board",revision:4,team:[]},plan={plan_id:"plan",revision:7},result={plan,result:{summary:{total_damage:42}}};
  const WB={state:{board,result,mode:"actions",geometry:{pps:100},wrap:{scrollTop:0}},selectEvent:vi.fn(),project:()=>({lanes:[{events:[event]}]}),draw:vi.fn()};
  const TV={projection,kindVisibility:{},rows:[{events:[event]}],pixelsPerSecond:100,refreshProjection:vi.fn(),rebuildRows:vi.fn(),paintProjection:vi.fn(),showDetail:vi.fn()};
  const wrap={scrollTop:0,scrollLeft:0},RS={state:{result},isCurrent:vi.fn(()=>true)};
  const globals:any={WB,TV,PL:{state:{plan}},RS,TD:{build:()=>projection},showEditor:vi.fn(async()=>{}),setStatus:vi.fn(),$:(selector:string)=>selector==="#timeline-wrap"?wrap:null};
  globals.window={...globals,GenshinBusinessUI:{createTimelineNavigator,timelineDocumentOwner,TIMELINE_VIEW_HEIGHT}};
  const bridge=vm.runInNewContext(`({${method}})`,globals);
  return {bridge,globals,WB,TV,RS,projection,event,board,plan,result,wrap};
}
describe("actual legacy EditorBridge read-only navigation",()=>{
  it("reveals an exact whiteboard result without replacing the board or calculation",async()=>{
    const f=fixture(),before=JSON.stringify({board:f.board,result:f.result});
    await f.bridge.revealTimelineEvent(selectionReference("whiteboard:board:4",f.projection,f.event,"damage"));
    expect(f.globals.showEditor).toHaveBeenCalledWith("whiteboard");expect(f.WB.selectEvent).toHaveBeenCalledWith(f.event);
    expect(f.WB.state.mode).toBe("damage");expect(f.WB.draw).toHaveBeenCalledOnce();expect(JSON.stringify({board:f.board,result:f.result})).toBe(before);
  });
  it("reveals an exact native result and clears only display filters",async()=>{
    const f=fixture(),before=JSON.stringify(f.plan);
    await f.bridge.revealTimelineEvent(selectionReference("native:plan:7",f.projection,f.event,"damage"));
    expect(f.globals.showEditor).toHaveBeenCalledWith("rotation");expect(f.TV.showDetail).toHaveBeenCalledWith(f.event);expect(f.wrap.scrollLeft).toBe(420);
    expect(JSON.stringify(f.plan)).toBe(before);expect(f.WB.selectEvent).not.toHaveBeenCalled();
  });
  it("refuses stale native calculation before page navigation",async()=>{
    const f=fixture();f.RS.isCurrent.mockReturnValue(false);
    await expect(f.bridge.revealTimelineEvent(selectionReference("native:plan:7",f.projection,f.event,"damage"))).rejects.toMatchObject({code:"timeline_selection_stale"});
    expect(f.globals.showEditor).not.toHaveBeenCalled();expect(f.TV.showDetail).not.toHaveBeenCalled();
  });
  it("refuses replacement document during asynchronous page navigation",async()=>{
    const f=fixture();f.globals.showEditor.mockImplementation(async()=>{f.WB.state.board={...f.board};});
    await expect(f.bridge.revealTimelineEvent(selectionReference("whiteboard:board:4",f.projection,f.event,"damage"))).rejects.toMatchObject({code:"timeline_selection_stale"});
    expect(f.WB.selectEvent).not.toHaveBeenCalled();
  });
});
