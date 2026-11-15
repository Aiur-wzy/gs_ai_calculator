"use strict";
window.REM={profile:"ordinary-reaction-flat-team-em-reference-v1"};
REM.renderRecord=(box,record,template)=>{
  if(!record){box.append(UI.el("p","尚无当前来源的精通编译预览，请预览或重新计算方案。"));return;}
  const ready=record.status==="ready"&&record.app_scope?.status==="ready";
  box.append(UI.el("p",ready?"反应触发精通：当前声明范围内就绪；完整来源仍部分支持。":"反应触发精通：来源未启用或仍待核实。"),
    UI.el("p","提供者在场对原目标触发合格反应后，给予全队精通；不追溯加成触发本次反应的伤害。再次触发刷新持续时间，已经给予的精通在提供者退场后继续计时。"));
  for(const rule of record.rules||[]){
    const value=rule.effects?.[0]?.value,duration=rule.timing?.duration;
    box.append(UI.el("p",`${DS.character(rule.owner,template)} · ${DS.prose(rule.name_zh,"反应精通")}：全队精通 +${Number.isFinite(value)?value:"待核实"}，持续 ${Number.isFinite(duration)?duration:"待核实"} 秒。`));
  }
  for(const issue of record.issues||[])box.append(UI.el("p",issue.message||"来源待完善","eq-issue"));
  box.append(UI.details("来源、数值与持续规则",{source:record.source_identity,rules:record.rules,provenance:record.provenance}),
    UI.details("适用范围及未支持分支",{scope:record.app_scope,branches:record.branches,coverage:record.coverage_certificate}));
};
REM.render=(box,binding)=>{
  const plan=EQ.plan(),record=(PL.state.report?.upstream_compilations||[]).find(r=>r.binding_id===binding.binding_id&&r.profile_id===REM.profile);
  box.append(UI.el("h5","反应触发的全队精通来源"));REM.renderRecord(box,record,plan?.manual_template);
  box.append(UI.el("p","此来源按实际反应触发，无需选择爆发动作。场景、提供者或目标改变后，请让Agent更新来源并重新核对；自动派生的规则不可直接编辑。"));
};
REM.renderResult=(section,result,template)=>{
  const records=result.reaction_em_compilations||[];if(!records.length)return;
  const box=UI.el("details"),rows=result.reaction_em_execution?.activations||[];
  box.append(UI.el("summary",`反应触发精通（${rows.length} 次实际触发，只读）`));
  for(const record of records)REM.renderRecord(box,record,template);
  const table=UI.el("table",null,"grid"),head=UI.el("tr"),labels={melt:"融化",vaporize:"蒸发",swirl:"扩散"};
  ["时刻 / 来源窗口","提供者","反应","目标","附着父发生 / 反应发生"].forEach(v=>head.append(UI.el("th",v)));table.append(head);
  for(const row of rows.slice(0,250)){const tr=UI.el("tr");[
    `${Number.isFinite(row.t)?row.t:"—"} 秒 / ${row.source_window===0?"完整输入":Number.isInteger(row.source_window)?`附加检查 ${row.source_window}`:"未知"}`,
    DS.character(row.owner,template),labels[row.reaction_type]||"未核实反应",row.target_id||"—",`${row.parent_run_id||"—"} / ${row.reaction_run_id||"—"}`
  ].forEach(v=>tr.append(UI.el("td",v)));table.append(tr);}
  const scroll=UI.el("div",null,"table-scroll");scroll.append(table);box.append(scroll);
  if(rows.length>250)box.append(UI.el("p",`已显示前250次，共${rows.length}次；完整记录见下方。`));
  box.append(UI.details("完整实际反应身份核验",result.reaction_em_execution));section.append(box);
};
