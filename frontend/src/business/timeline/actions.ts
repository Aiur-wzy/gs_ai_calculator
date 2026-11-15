import {list,finite,copy,key,firstWindow} from "./shared";
import {type TimelineContext} from "./context";
export function projectActions(context:TimelineContext) {
    const {t,r,actions,abilities,characterName,within,note,put,source,actionLabel,actionLogs,cancelled,plan}=context;
    for (const [index, a] of actions.entries()) {
      if (!firstWindow(a) || !within(a.start)) continue;
      const ability = abilities.get(a.ability_id) || {}, row = actionLogs.get(a.id);
      const isSwitch = ability.action_type_id === "action.switch";
      const label = actionLabel(ability);
      const detail:any = {action_id:a.id, ability_id:a.ability_id, input_status:a.status, source:a.source,
        input_order:a.order,
        execution_status:row?.status || (r ? "not_verified" : "not_calculated"), declared_occupation_end:a.occupation_end,
        execution:copy(row || null), switch_to:a.switch_to};
      let end = finite(row?.occupation_end) ? row.occupation_end : a.occupation_end;
      if (cancelled.has(a.id)) { end = Math.min(end, cancelled.get(a.id)); detail.cancelled_at = end; }
      put("actions", {id:key("action", a.id), kind:isSwitch ? "switch" : "action", character:ability.character ?? null,
        ...source(ability.character, "action", ability.id || a.ability_id, label),
        label:isSwitch ? `切换至${characterName(a.switch_to)}` : label, start:a.start,
        ...(finite(end) && !isSwitch ? {end} : {}), detail, edit:{kind:"action", index}});
    }
    if (!actions.length) {
      note("actions", "没有技能释放动作模型；命中时点不能替代施放动作。");
      list(t.switches).forEach((sw, index) => put("actions", {id:key("switch", index), kind:"switch", character:sw.character,
        ...source(sw.character, "action", "switch", "切换角色"), label:`切换至${characterName(sw.character)}`,
        start:sw.t, detail:{origin:"manual_switch"}, ...(!plan ? {edit:{kind:"switch", index}} : {})}));
    }

}
