"use strict";
window.SI={};
SI.stats={base_atk:"基础攻击力",atk:"攻击力",hp:"生命值",def:"防御力",crit_rate:"暴击率",crit_dmg:"暴击伤害",energy_recharge:"元素充能效率",em:"元素精通","dmg_bonus:all":"伤害加成","dmg_bonus:physical":"物理伤害加成","dmg_bonus:pyro":"火元素伤害加成","dmg_bonus:hydro":"水元素伤害加成","dmg_bonus:cryo":"冰元素伤害加成","dmg_bonus:electro":"雷元素伤害加成","dmg_bonus:anemo":"风元素伤害加成","dmg_bonus:geo":"岩元素伤害加成","dmg_bonus:dendro":"草元素伤害加成"};
SI.ready=r=>r?.status==="ready"&&r.source_authority==="fresh_database_admission"&&r.declaration?.delivery==="already_in_given_panel"&&r.declaration.numeric_application===false;
SI.forRequirement=(report,req)=>{
  if(req?.resolved_by?.kind!=="static_inclusion")return null;
  const rows=(report.static_inclusion_compilations||[]).filter(r=>r.id===req.resolved_by.id&&r.binding_id===req.binding_id&&r.provider_character_id===req.provider_character_id&&typeof r.compilation_fingerprint==="string"&&r.compilation_fingerprint===req.resolved_by.compilation_fingerprint&&SI.ready(r));
  return rows.length===1?rows[0]:null;
};
SI.renderRecord=(box,record,template)=>{
  if(!record)return;
  const d=record.declaration||{},display=record.source_display||{},ready=SI.ready(record);
  box.append(UI.el("h5",`${DS.character(record.provider_character_id,template)} · ${display.name||"套装常驻属性"}`),
    UI.el("p",ready?"已计入初始面板 · 本次不重复加算":"静态来源待核实 · 尚未自动采用"),
    UI.el("p",`套装 ${d.required_count??"待核实"} 件效果；常驻属性没有独立触发时点或持续区间。四件套条件效果另行核对。`));
  for(const prop of d.properties||[]){
    const v=Number.isFinite(prop.value)?(prop.unit==="ratio"?`${Number((prop.value*100).toFixed(4))}%`:String(prop.value)):"待核实";
    box.append(UI.el("p",`${SI.stats[prop.stat]||"未识别属性"}：${v}${ready?"（已含）":"（待核实）"}`));
  }
  if(display.description)box.append(UI.el("p",display.description));
  box.append(UI.el("p","系统核对数据库来源与所选装备；给定面板的数值由用户确认。修改面板或装备后请重新计算。"));
  for(const issue of record.issues||[])box.append(UI.el("p",issue.message||"来源材料或采用条件尚未通过核对","eq-issue"));
  box.append(UI.details("来源、属性原值和采用依据（只读）",record));
};
SI.renderResult=(section,result,template)=>{
  const records=result.static_inclusion_compilations||[];if(!records.length)return;
  const box=UI.el("details");box.append(UI.el("summary",`面板已含来源（${records.filter(SI.ready).length} 项已采用，只读）`));
  for(const record of records)SI.renderRecord(box,record,template);
  section.append(box);
};
