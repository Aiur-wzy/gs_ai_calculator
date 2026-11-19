/** Read-only raw JSON presentation; no inferred game mechanics or global editor access. */
export const actionNames:Record<string,string> = {"action.elemental_skill":"元素战技", "action.elemental_burst":"元素爆发",
  "action.normal_attack":"普通攻击", "action.charged_attack":"重击", "action.plunging_attack":"下落攻击",
  "action.dash":"冲刺", "action.jump":"跳跃", "action.switch":"切换角色", "action.wait":"等待"};
export const damageNames:Record<string,string> = {"damage.elemental_skill":"元素战技", "damage.elemental_burst":"元素爆发",
  "damage.normal_attack":"普通攻击", "damage.charged_attack":"重击", "damage.plunging_attack":"下落攻击",
  "damage.reaction":"反应伤害"};
export const reactionNames:Record<string,string> = {amplify:"增幅反应", swirl:"扩散", vaporize:"蒸发", melt:"融化", overload:"超载", superconduct:"超导",
  electro_charged:"感电", frozen:"冻结", freeze:"冻结", shatter:"碎冰", burning:"燃烧", bloom:"绽放",
  hyperbloom:"超绽放", burgeon:"烈绽放", aggravate:"超激化", spread:"蔓激化", crystallize:"结晶",
  stellar_superconduct:"星超导", stellar_swirl:"星扩散", stellar_swirl_anemo:"星扩散·风",
  stellar_swirl_cryo_lv2:"星扩散·1级风旋爆发", stellar_swirl_cryo_lv3:"星扩散·2级风旋爆发",
  lunar_electro:"月感电", lunar_bloom:"月绽放", lunar_crystallize:"月结晶"};
export const elementNames:Record<string,string> = {pyro:"火", hydro:"水", anemo:"风", electro:"雷", dendro:"草", cryo:"冰", geo:"岩", physical:"物理"};
export const list = (value:unknown): any[] => Array.isArray(value) ? value : [];
export const isCoverageKind = (kind:unknown) => ["coverage","field_layers","settled_layers","resource_inventory","resource_mode"].includes(String(kind));
export const finite = (value:unknown): value is number => typeof value === "number" && Number.isFinite(value);
export const copy = <T>(value:T):T => value == null ? value : JSON.parse(JSON.stringify(value));
export const key = (...parts:any[]) => JSON.stringify(parts);
export const zh = (value:unknown) => /[\u3400-\u9fff]/u.test(String(value || ""));
export const clean = (value:unknown) => String(value || "").replace(/^机制派生(?:收益)?[·：:\s]*/u, "");
export const chinese = (value:unknown, fallback:string) => zh(clean(value)) ? clean(value) : fallback;
export const reactionName = (reaction:any) => reaction?.type === "swirl" && elementNames[reaction.reaction_element]
  ? `${elementNames[reaction.reaction_element]}扩散`
  : reactionNames[reaction?.type] || "反应";
export const firstWindow = (item:any) => item?.repeat?.mode !== "cycles" || list(item.repeat.cycles).includes(0);
export const projection = (buff:any) => String(buff.id || "").startsWith("mechanics:")
  || buff.source === "声明式机制逐事件投影" || /^机制派生收益/u.test(buff.name || "");
export const stateName = (status:string) => (({active:"规则有效", exhausted:"次数耗尽", declared:"手工声明"} as Record<string,string>)[status] || "已记录状态");
export const endReason = (reason:string) => (({refresh:"激活或刷新",layer_expiry:"部分层数到期",quota_change:"次数变化",
  natural_expiry:"自然到期",execution_limit:"执行检查中断",window_end:"到达轴末"} as Record<string,string>)[reason] || "记录结束");

