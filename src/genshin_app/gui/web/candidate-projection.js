// Pure App document projection shared by the DSH candidate view. Never assign
// WB/RT/PL state and never run calculation while rendering a candidate.
window.CandidateDocumentProjection=Object.freeze({
  build(source,calculated){
    const plan=calculated?.plan||source?.plan||source||{},board=source?.kind==="genshin-whiteboard"?source:plan.extensions?.whiteboard_handoff?.board;
    const template=plan.resolved_template||plan.manual_template||{characters:board?.team||[],loop_duration:board?.duration,hits:[],buffs:[]};
    const projection=TD.build({plan,template,result:calculated,
      executionModel:calculated?.report?.mechanics_model,executionTemplate:calculated?.report?.mechanics_input_template,current:!!calculated});
    if(board){
      projection.duration=board.duration;
      projection.characters=(board.team||[]).map((actor,index)=>({id:actor.id,label:actor.name||`角色 ${index+1}`}));
      projection.views.actions.events=(board.actions||[]).filter(action=>Number.isFinite(action.start)).map(action=>({
        id:action.id,character:action.character,start:action.start,
        label:action.label||({"action.elemental_skill":"元素战技","action.elemental_burst":"元素爆发","action.normal_attack":"普通攻击","action.charged_attack":"重击","action.plunging_attack":"下落攻击","action.switch":"切换角色","action.dash":"冲刺","action.jump":"跳跃","action.wait":"等待"}[action.action_type_id]||"动作"),
        detail:JSON.parse(JSON.stringify(action)),
      }));
    }
    return projection;
  },
});
