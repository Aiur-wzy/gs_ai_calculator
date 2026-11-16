import {describe,it,expect,vi} from "vitest";
import {createTimelineNavigator,timelineDocumentOwner,type TimelineNavigationSnapshot} from "./navigation";
import {selectionReference} from "./evidence-index";
function fixture(){
  const event={id:"damage-row",detail:{explanation_ref:"hit:exact"}},document={plan_id:"p",revision:3},result={};
  const projection={explanationFingerprint:"receipt",views:{damage:{events:[event]}}};
  const snapshot:TimelineNavigationSnapshot={owner:timelineDocumentOwner(document,"native")!,projection,document,result,page:"rotation"};
  const selection=selectionReference(snapshot.owner,projection,event,"damage")!;
  const ports={capture:vi.fn(()=>snapshot),show:vi.fn(async()=>{}),select:vi.fn()};
  return {snapshot,selection,ports,event};
}
describe("exact read-only cross-window timeline navigation",()=>{
  it("locates matching current results and does not mutate inputs",async()=>{
    const {snapshot,selection,ports,event}=fixture(),before=JSON.stringify(snapshot);
    expect(await createTimelineNavigator(ports).reveal(selection)).toMatchObject({status:"located"});
    expect(ports.show).toHaveBeenCalledWith("rotation");expect(ports.select).toHaveBeenCalledWith(snapshot,event);
    expect(JSON.stringify(snapshot)).toBe(before);
  });
  it.each([{owner:"foreign"},{result:"old"},{event:"unknown"},{explanation:"wrong"},{view:"effects"}])("rejects foreign or stale selection %j before changing page",async(change)=>{
    const {selection,ports}=fixture();await expect(createTimelineNavigator(ports).reveal({...selection,...change})).rejects.toMatchObject({code:"timeline_selection_stale"});
    expect(ports.show).not.toHaveBeenCalled();expect(ports.select).not.toHaveBeenCalled();
  });
  it("rejects ambiguous IDs instead of picking the first",async()=>{
    const {snapshot,selection,ports,event}=fixture();snapshot.projection.views.damage.events.push({...event});
    await expect(createTimelineNavigator(ports).reveal(selection)).rejects.toThrow("同一份方案");expect(ports.select).not.toHaveBeenCalled();
  });
  it.each(["result","document","revision"])("refuses %s changed while opening the page",async(change)=>{
    const {snapshot,selection,ports}=fixture();ports.show.mockImplementation(async()=>{
      if(change==="revision")snapshot.owner="native:p:4";else snapshot[change as "document"|"result"]={};
    });
    // A fresh capture record mirrors the adapter; changing model objects cannot alter the saved snapshot.
    ports.capture.mockImplementation(()=>({...snapshot}));
    await expect(createTimelineNavigator(ports).reveal(selection)).rejects.toThrow("已变化");expect(ports.select).not.toHaveBeenCalled();
  });
  it("requires actual document ID and revision; no display-name fallback",()=>{
    expect(timelineDocumentOwner({title:"p",revision:0},"whiteboard")).toBeNull();
    expect(timelineDocumentOwner({board_id:"b"},"whiteboard")).toBeNull();
    expect(timelineDocumentOwner({board_id:"b",revision:-1},"whiteboard")).toBeNull();
    expect(timelineDocumentOwner({board_id:"b",revision:0},"whiteboard")).toBe("whiteboard:b:0");
  });
});
