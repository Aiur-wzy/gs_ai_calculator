/** DOM-only time-axis components. Input/selection remain owned by the supplied editor. */
import {TIMELINE_ACTION_HEIGHT,TIMELINE_VIEW_HEIGHT} from "./geometry";
export interface TimelineDomPorts {
  view:Record<string,any>; editor:Record<string,any>;
  query:(selector:string)=>any; document:Document;
  events:Pick<Window,"addEventListener"|"removeEventListener">;
  token?:(name:string)=>string|undefined;
  projection:Record<string,any>;
  services:{effects?:any;display?:any;workspace?:any;rotation?:any;surface?:any;result?:any};
}
export function createTimelineDom(ports:TimelineDomPorts){
  const TV=ports.view,$=ports.query;
const VIEW_ROW_H = TIMELINE_VIEW_HEIGHT;
const ACTION_ROW_H = TIMELINE_ACTION_HEIGHT;
const ACTION_CAPTIONS:Record<string,string> = {"action.elemental_skill":"战技","action.elemental_burst":"爆发",
  "action.normal_attack":"普攻","action.charged_attack":"重击","action.plunging_attack":"下落",
  "action.dash":"冲刺","action.jump":"跳跃","action.wait":"等待"};
const VIEW_KINDS:Record<string,any> = {
  action: {label:"技能释放", shape:"bar",colorToken:"color-axis-action",fallback:"#61af72"},
  switch: {label:"切换角色", shape:"diamond",colorToken:"color-axis-reaction",fallback:"#c678dd"},
  hit: {label:"命中生效", shape:"circle",colorToken:"color-axis-action",fallback:"#5b8cff"},
  activation: {label:"效果触发", shape:"diamond",colorToken:"color-axis-warning",fallback:"#d19a66"},
  coverage: {label:"持续Buff区间", shape:"bar",colorToken:"color-axis-buff",fallback:"#56b6c2"},
  source_directory: {label:"同名独立来源",shape:"bar",colorToken:"color-axis-buff",fallback:"#56b6c2"},
  calculation_sampling: {label:"计算采样窗口",shape:"bar",colorToken:"color-axis-label-muted",fallback:"#9aa3b5"},
  field_layers: {label:"领域累计层数",shape:"bar",colorToken:"color-axis-warning",fallback:"#d19a66"},
  settled_layers: {label:"领域结算增益",shape:"bar",colorToken:"color-axis-buff",fallback:"#56b6c2"},
  quota: {label:"次数变化", shape:"triangle",colorToken:"color-danger",fallback:"#e06c75"},
  resource: {label:"共享资源变化", shape:"triangle",colorToken:"color-axis-warning",fallback:"#f08c46"},
  reaction: {label:"反应记录", shape:"triangle",colorToken:"color-axis-reaction",fallback:"#da86ee"},
  damage: {label:"伤害事件", shape:"circle",colorToken:"color-axis-damage",fallback:"#ffb55a"},
  calculation: {label:"计算记录", shape:"square",colorToken:"color-axis-label-muted",fallback:"#9aa3b5"},
  external_damage: {label:"外部计算补充", shape:"diamond",colorToken:"color-axis-reaction",fallback:"#da86ee"},
  condition: {label:"条件与附着", shape:"square",colorToken:"color-axis-label-muted",fallback:"#9abbb6"},
};
const VIEW_NAMES:Record<string,string> = {actions:"动作轴", effects:"效果轴", damage:"伤害轴"};
const viewThemeColor = (token:string,fallback:string) => ports.token?.(token) || fallback;
const viewKind = (kind:any) => {
  const entry=VIEW_KINDS[kind] || {label:"其他记录",shape:"square",colorToken:"color-axis-label-muted",fallback:"#9aa3b5"};
  return {...entry,color:viewThemeColor(entry.colorToken,entry.fallback)};
};
const viewName = (text:any) => String(text || "来源未提供").replace(/^机制派生收益\s*[·：:]?\s*/, "");
const viewStatus = (status:any) => (({passed:"检查通过",calculated:"已计算",executed:"已执行",failed:"执行失败",invalid:"输入无效",
  not_verified:"未验证",not_calculated:"尚未计算",pending:"待完善",cancelled:"已取消",not_checked:"未检查"} as Record<string,string>)[status] || "状态待解释");
const viewEl = (tag:string, text?:any, cls?:string):any => { const n=ports.document.createElement(tag); if(text!=null)n.textContent=text;
  if(cls)n.className=cls; return n; };
const viewSeconds = (value:any) => Number(value.toFixed(4));
const viewTime = (event:any) => `${viewSeconds(event.start)}秒${Number.isFinite(event.end)&&event.end>event.start?`～${viewSeconds(event.end)}秒`:""}`;
const viewPermission = (mode:any) => mode==="actions" ? "选择动作可编辑"
  : TV.projection?.views?.[mode]?.events?.some((event:any)=>event.edit) ? "派生记录只读 · 手工输入可编辑" : "计算结果 · 只读";
const viewEmptyDetail = () => {
  const box=$("#timeline-detail");if(!box)return;
  box.innerHTML="";
  box.append(viewEl("div","事件详情","tl-detail-eyebrow"),viewEl("h4","选择轴上的记录"),
    viewEl("p","点击条块或时间点，在这里查看角色、来源与时间。密集标记可展开逐条查看。","tl-detail-hint"));
};



  return {VIEW_ROW_H,ACTION_ROW_H,ACTION_CAPTIONS,VIEW_KINDS,VIEW_NAMES,viewThemeColor,viewKind,viewName,viewStatus,viewEl,viewSeconds,viewTime,viewPermission,viewEmptyDetail};
}
