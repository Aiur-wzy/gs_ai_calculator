import {resolveSelection,type TimelineSelection} from "./evidence-index";

/** Identity for navigation only; never an edit, import or calculation authority. */
export function timelineDocumentOwner(document:Record<string,unknown>,kind:"native"|"whiteboard"):string|null {
  const id=document?.[kind==="whiteboard"?"board_id":"plan_id"],revision=document?.revision;
  if(typeof id!=="string"||!id||!Number.isSafeInteger(revision)||Number(revision)<0)return null;
  return `${kind}:${id}:${revision}`;
}
export interface TimelineNavigationSnapshot {
  owner:string;projection:any;document:object;result:object;page:"whiteboard"|"rotation";
}
export interface TimelineNavigationPorts {
  capture():TimelineNavigationSnapshot|null;
  show(page:"whiteboard"|"rotation"):Promise<void>;
  select(snapshot:TimelineNavigationSnapshot,event:any):void;
}
export function createTimelineNavigator(ports:TimelineNavigationPorts) {
  const stale=(message:string)=>Object.assign(new Error(message),{code:"timeline_selection_stale"});
  return {async reveal(selection:TimelineSelection):Promise<{status:"located";message:string}> {
    const before=ports.capture();
    if(selection?.view!=="damage"||!before||!resolveSelection(before.owner,before.projection,selection))
      throw stale("无法定位：主窗口没有同一份方案和同一版伤害结果。未导入候选，也未修改白板。");
    await ports.show(before.page);
    const after=ports.capture(),event=after&&resolveSelection(after.owner,after.projection,selection);
    if(!after||after.document!==before.document||after.result!==before.result||after.page!==before.page||!event)
      throw stale("无法定位：打开页面时方案或计算结果已变化，请重新选择伤害。白板未被修改。");
    ports.select(after,event);
    return {status:"located",message:"已在主窗口定位这段伤害；未修改方案、审核状态或计算结果。"};
  }};
}
