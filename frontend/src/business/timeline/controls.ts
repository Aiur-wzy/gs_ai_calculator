import {type TimelineDomPorts,createTimelineDom} from "./dom";
import {isCoverageKind} from "./shared";
export function createTimelineControls(ports:TimelineDomPorts){
  const TV=ports.view,RT=ports.editor,$=ports.query,TD=ports.projection,services=ports.services;
  const {VIEW_NAMES,viewKind,viewEl,viewPermission,viewEmptyDetail}=createTimelineDom(ports);
  const LABEL_W=210,RULER_H=44;
  const viewRestoreFilterFocus=(label:string)=>{
    const selector=`[aria-label="${label}"]`;
    (TV.filterPanel?.querySelector?.(selector) ?? $("#timeline-filters")?.querySelector?.(selector))?.focus?.();
  };
const changeMode = (mode:any) => {
  if(!VIEW_NAMES[mode] || TV.mode===mode)return;
  const wrap=$("#timeline-wrap"); TV.modeScroll[TV.mode]=wrap.scrollTop || 0;
  TV.mode=mode; TV.sourceFilter=""; TV.componentFilter=""; TV.rebuildRows();
  // 不把上一视图的伤害数值或计算记录留在效果/动作详情中。
  TV.detailSelection=null;
  viewEmptyDetail();
  wrap.scrollTop=TV.modeScroll[mode] || 0;
  TV.paintProjection();
};

const renderControls = (view:any) => {
  const modes=$("#timeline-modes"), filters=$("#timeline-filters"), legend=$("#timeline-legend"), note=$("#timeline-note");
  if(modes){modes.innerHTML="";for(const [id,name] of Object.entries(VIEW_NAMES)){
    const button=viewEl("button",name);button.type="button";button.setAttribute("aria-pressed",String(TV.mode===id));
    button.addEventListener("click",()=>TV.changeMode(id));modes.appendChild(button);
  }modes.appendChild(viewEl("span",viewPermission(TV.mode),"tl-mode-permission"));}
  if(filters){
    // Rebuilding the controls must retire the old overlay instance first.  The
    // filter choices themselves remain in TV, so an open menu can be restored
    // without leaving document-level dismissal handlers behind.
    const reopen=!!TV.filterOpen;
    TV.filterBinding?.destroy();
    TV.filterBinding=null;
    filters.innerHTML="";
    const labels=viewEl("button",TV.labelsCollapsed?"展开轴标签":"收起轴标签");labels.type="button";
    labels.addEventListener("click",()=>{TV.labelsCollapsed=!TV.labelsCollapsed;TV.rebuildRows();TV.paintProjection();});filters.appendChild(labels);
    if(TV.mode==="effects")for(const [value,label] of [["all","全部效果"],["coverage","持续Buff"],["points","命中与触发"]]){
      const button=viewEl("button",label);button.type="button";button.setAttribute("aria-pressed",String((TV.effectFocus||"all")===value));
      button.addEventListener("click",()=>{TV.effectFocus=value;TV.rebuildRows();TV.paintProjection();});filters.appendChild(button);
    }
    if(TV.mode==="damage")for(const [value,label] of [["damage","伤害事件"],["all","含计算记录"]]){
      const button=viewEl("button",label);button.type="button";button.setAttribute("aria-pressed",String((TV.damageFocus||"damage")===value));
      button.addEventListener("click",()=>{TV.damageFocus=value;TV.rebuildRows();TV.paintProjection();});filters.appendChild(button);
    }
    const select=(parent:any,label:any, entries:any, selected:any, change:any)=>{
      const box=viewEl("label",label), control=viewEl("select");control.setAttribute("aria-label",label);
      for(const [value,text] of entries){const option=viewEl("option",text);option.value=value;control.appendChild(option);}
      control.value=selected;control.addEventListener("change",()=>{change(control.value);TV.rebuildRows();TV.paintProjection();viewRestoreFilterFocus(label);});
      box.appendChild(control);parent.appendChild(box);
    };
    select(filters,"角色",[["","全部角色"],...[...TV.characterNames]],TV.characterFilter,
      (value:any)=>{TV.characterFilter=value;TV.sourceFilter="";TV.componentFilter="";});
    const hidden=TV.kinds.filter((kind:any)=>TV.kindVisibility[TV.mode][kind]===false).length;
    const active=Number(!!TV.characterFilter)+Number(!!TV.sourceFilter)+Number(!!TV.componentFilter)+hidden
      +Number(TV.mode==="effects"&&!!TV.showCalculationSampling);
    const filter=viewEl("details",null,"tl-filter-menu"),summary=viewEl("summary",`筛选${active?` · ${active}`:""}`);
    const panel=viewEl("div",null,"tl-filter-panel");panel.id="timeline-filter-options";
    filter.open=false;summary.setAttribute("aria-expanded","false");summary.setAttribute("aria-controls",panel.id);
    filter.append(summary,panel);filters.appendChild(filter);TV.filterDetails=filter;TV.filterPanel=panel;
    const setOpen=(opened:boolean)=>{filter.open=opened;TV.filterOpen=opened;summary.setAttribute("aria-expanded",String(opened));};
    const close=()=>{
      if(TV.filterBinding){TV.filterBinding.close("close-button");return;}
      setOpen(false);summary.focus?.();
    };
    panel.appendChild(viewEl("div","筛选记录","tl-filter-heading"));
    if(TV.mode==="effects"&&view.coverage?.sampling_count){
      const label=viewEl("label"),check=viewEl("input");check.type="checkbox";
      check.checked=!!TV.showCalculationSampling;check.setAttribute("aria-label","显示计算采样");
      check.addEventListener("change",()=>{TV.showCalculationSampling=check.checked;if(check.checked)TV.effectFocus="all";
        TV.rebuildRows();TV.paintProjection();viewRestoreFilterFocus("显示计算采样");});
      label.append(check,viewEl("span",`显示作者标记的计算采样（${view.coverage.sampling_count}项，非游戏Buff寿命）`));panel.appendChild(label);
    }
    select(panel,"来源",[["","全部来源"],...[...TV.sources]],TV.sourceFilter,(value:any)=>{TV.sourceFilter=value;TV.componentFilter="";});
    if(TV.mode==="damage"&&TV.components?.size)select(panel,"伤害组件",[["","全部组件"],...[...TV.components]],TV.componentFilter||"",
      (value:any)=>{TV.componentFilter=value;});
    const kinds=viewEl("div",null,"tl-filter-kinds");
    for(const kind of TV.kinds){if(kind==="calculation_sampling")continue;
      const label=viewEl("label"), check=viewEl("input");check.type="checkbox";
      check.checked=TV.kindVisibility[TV.mode][kind]!==false;check.setAttribute("aria-label",viewKind(kind).label);
      check.addEventListener("change",()=>{TV.kindVisibility[TV.mode][kind]=check.checked;TV.rebuildRows();TV.paintProjection();viewRestoreFilterFocus(viewKind(kind).label);});
      label.append(check,viewEl("span",viewKind(kind).label));kinds.appendChild(label);
    }
    panel.appendChild(kinds);
    if(legend)panel.appendChild(legend);
    const reset=viewEl("button","重置筛选"),done=viewEl("button","收起");reset.type=done.type="button";
    reset.addEventListener("click",()=>{TV.characterFilter="";TV.sourceFilter="";TV.componentFilter="";TV.kindVisibility[TV.mode]={};TV.effectFocus="all";TV.damageFocus="damage";TV.showCalculationSampling=false;TV.rebuildRows();TV.paintProjection();viewRestoreFilterFocus("来源");});
    done.addEventListener("click",close);const footer=viewEl("div",null,"tl-filter-footer");footer.append(reset,done);panel.appendChild(footer);
    const binding=services.surface?.bindDisclosure?.(filter,{panel,ariaLabel:"时间轴显示筛选",
      initialFocus:()=>panel.querySelector?.("select"),onRequestClose:()=>{TV.filterOpen=false;return true;}});
    TV.filterBinding=binding;
    filter.addEventListener("toggle",()=>{
      if(TV.filterDetails!==filter)return;
      TV.filterOpen=filter.open;
      if(!binding)summary.setAttribute("aria-expanded",String(filter.open));
    });
    if(binding){if(reopen)TV.filterOpen=!!binding.open();}
    else {
      setOpen(reopen);
      filter.addEventListener("keydown",(e:any)=>{if(e.key==="Escape"&&filter.open){e.preventDefault();e.stopPropagation();close();}});
      if(!TV.filterOutsideBound){TV.filterOutsideBound=true;ports.events.addEventListener("pointerdown",e=>{
        const open=TV.filterDetails;if(open?.open && !open.contains?.(e.target)){open.open=false;TV.filterOpen=false;
          open.querySelector?.("summary")?.setAttribute("aria-expanded","false");}
      });}
    }
  }
  if(legend){legend.innerHTML="";const symbols:Record<string,string>={bar:"━",circle:"●",diamond:"◆",triangle:"▲",square:"■"};
    const shown=(kind:any)=>TV.kindVisibility[TV.mode][kind]!==false
      && !(kind==="calculation_sampling"&&(!TV.showCalculationSampling||TV.effectFocus&&TV.effectFocus!=="all"))
      && !(TV.mode==="effects"&&(TV.effectFocus==="coverage"&&!isCoverageKind(kind)||TV.effectFocus==="points"&&isCoverageKind(kind)))
      && !(TV.mode==="damage"&&TV.damageFocus!=="all"&&kind==="calculation");
    for(const kind of TV.kinds)if(shown(kind)){const k=viewKind(kind);
      const item=viewEl("span"),symbol=viewEl("span",symbols[k.shape],"tl-legend-symbol");symbol.style.color=k.color;
      item.append(symbol,viewEl("span",` ${k.label}`));legend.appendChild(item);}
    if(TV.mode==="effects" && TV.effectFocus!=="points"){
      const exhausted=viewEl("span","━ 次数或共享库存耗尽（仍在计时）");exhausted.style.color="var(--color-axis-exhausted)";legend.appendChild(exhausted);}
    legend.appendChild(viewEl("span","×数量：密集记录，点击逐条查看"));
    if(TV.mode==="damage")legend.appendChild(viewEl("span","Σ：密集组期望伤害合计。计算记录解释同次伤害，不是额外游戏事件"));
  }
  if(note){note.textContent=[...(TV.mode==="effects"?[view.coverage?.message,"持续Buff按来源显示起止区间；点击查看接收角色与刷新记录。无独立时长的自身条件增益只保留在逐击明细。"]:[]),
    ...(view.notes||[]).map((n:any)=>typeof n==="string"?n:n.message || n.note || "")].filter(Boolean).join("；");
    if(TV.mode==="effects" && view.coverage?.excluded?.length){const details=viewEl("details"),list=viewEl("ul");
      details.appendChild(viewEl("summary",`未显示为持续条的来源（${view.coverage.excluded.length}）`));
      for(const item of view.coverage.excluded)list.appendChild(viewEl("li",`${/[\u3400-\u9fff]/u.test(item.name_zh||"")?item.name_zh:"增益来源"}：${item.message||"没有可靠的有限持续时间"}`));
      details.appendChild(list);note.appendChild(details);}}
  const empty=$("#timeline-empty-state");if(empty){empty.innerHTML="";
    const needsCalc=["not_calculated","stale"].includes(view.coverage?.origin);
    const absent=!(view.events||[]).some((event:any)=>isCoverageKind(event.kind));
    empty.hidden=!(TV.mode==="effects"&&TV.effectFocus!=="points"&&(needsCalc||absent)&&view.coverage);
    if(!empty.hidden){empty.appendChild(viewEl("span",view.coverage.message));
      if(needsCalc){const button=viewEl("button","计算当前方案，展开持续状态");button.type="button";button.disabled=!!services.result?.state.pending;
        button.addEventListener("click",()=>services.result?.runCalc?.());empty.appendChild(button);}
      else if(view.coverage.excluded?.length)empty.appendChild(viewEl("span",`有 ${view.coverage.excluded.length} 个来源未绘制；原因见「操作与说明」。`));}}
};


  return {changeMode,renderControls};
}
