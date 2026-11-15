// One timeline facade: input snapshots, view state and public compatibility names.
"use strict";
RT.duration=()=>{const t=RT.tpl();if(t.loop_duration)return t.loop_duration;
  return Math.max(10,Math.ceil(Math.max(...t.hits.map(h=>h.t),...(t.buffs||[]).map(b=>b.end),...(t.switches||[]).map(s=>s.t),0)+2));};
const timelinePorts={view:TV,editor:RT,query:selector=>$(selector),document,events:window,projection:TD,
  token:name=>window.ThemeController?.token?.(name),
  services:{get effects(){return window.EFX;},get display(){return window.DS;},get workspace(){return window.WS;},
    get rotation(){return window.RC;},get surface(){return window.AppSurfaceKernel;},get result(){return window.RS;}}};
Object.assign(TV,GenshinBusinessUI.createTimelineLayout(timelinePorts),GenshinBusinessUI.createTimelineControls(timelinePorts),
  GenshinBusinessUI.createTimelineSvg(timelinePorts),GenshinBusinessUI.createTimelineDetail(timelinePorts));
const viewEmptyDetail=GenshinBusinessUI.createTimelineDom(timelinePorts).viewEmptyDetail;
RT.renderTimeline=()=>{TV.refreshProjection();TV.paintProjection();};
TV.refreshProjection = () => {
  const template=RT.tpl(), plan=window.PL?.state.plan || null, result=window.RS?.state.result || null;
  // Only editor/result notifications enter here. Scroll, zoom and filtering use
  // the cached validity bit rather than serializing the full native plan again.
  const current=!!(result && window.RS?.isCurrent?.());
  const executionModel=window.RS?.state.snapshot?.executionModel || null;
  const executionTemplate=window.RS?.state.snapshot?.executionTemplate || null;
  const revision=RT.state.revision, old=TV.projectionKey;
  if(old && old.template===template && old.plan===plan && old.result===result && old.current===current
      && old.revision===revision && old.executionModel===executionModel)return;
  if(!TV.projection || TV.projectionTemplate!==template){
    TV.mode=plan?"actions":"damage"; TV.characterFilter=""; TV.sourceFilter="";
    TV.kindVisibility={}; TV.effectFocus="all";TV.damageFocus="damage"; TV.modeScroll={}; TV.detailSelection=null;TV.filterOpen=false;
    viewEmptyDetail();
  }
  TV.projectionTemplate=template;TV.dataInvalid=false;
  const detail=$("#timeline-detail");if(detail && TV.detailSelection){detail.textContent="数据已更新，请重新选择事件";TV.detailSelection=null;}
  TV.projectionKey={template,plan,result,current,revision,executionModel};
  // Resolve legacy numeric-template labels at the presentation boundary. The
  // pure projector receives explicit names and never reads global catalogs;
  // stable source input and IDs remain untouched.
  const displayTemplate=!plan&&template&&window.DS?{...template,
    characters:(template.characters||[]).map(actor=>({...actor,name:DS.character(actor.id)})),
    hits:(template.hits||[]).map((hit,index)=>({...hit,hit_name:DS.hit(hit,index)})),
    buffs:(template.buffs||[]).map(buff=>({...buff,name:DS.buff(buff)})),
  }:template;
  TV.projection=TD.build({template:displayTemplate,plan,result,executionModel,executionTemplate,current});
  TV.characterNames=new Map((TV.projection.characters||[]).map(c=>[c.id,c.label || c.id]));
  const rotation=(current&&(result?.result||result)?.rotation_check?.resolved_rotation_model)||plan?.rotation_model||{};
  TV.actionTypes=new Map((rotation.abilities||[]).map(ability=>[ability.id,ability.action_type_id]));
  TV.rebuildRows();
};

// 拖拽：移动中只做视觉预览（transform），松手才提交数值并重渲染
RT.updateTimelineSelection = () => {
  document.querySelectorAll("#timeline [data-select-kind]").forEach((el) => {
    el.classList.toggle("selected", el.dataset.selectKind === RT.sel()?.kind
      && Number(el.dataset.selectIndex) === RT.sel()?.index);
  });
};

// Thin compatibility facade: the TypeScript controller owns pointer lifetime.
let timelinePointerController = null;
RT.enableDrag = (el, baseSec, commit, visualEl, label, selection) => {
  timelinePointerController ||= window.GenshinBusinessUI.createTimelinePointerController({
    events:window,template:()=>RT.tpl(),select:value=>RT.select(value.kind,value.index,false),
    geometry:()=>({svg:$("#timeline"),viewport:$("#timeline-wrap"),duration:RT.duration(),pixelsPerSecond:RT.pxPerSec}),
    round:value=>RT.round2(value),status:message=>setStatus(message),repaint:()=>RT.renderAll(),
    active:cancel=>{RT.cancelDrag=cancel;},
  });
  return timelinePointerController.bind(el,baseSec,commit,visualEl,label,selection);
};
