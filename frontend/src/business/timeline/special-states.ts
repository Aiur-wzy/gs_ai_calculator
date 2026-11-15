import {copy,finite,key,list} from "./shared";
import {type TimelineContext} from "./context";

/** Read the engine's committed intervals; never rebuild a field in the GUI. */
export function projectSpecialStates(context:TimelineContext) {
  const {r,put,note,source}=context, receipt=r?.special_state_timeline;
  if(!receipt){
    if(list(r?.special_reactions?.trace).some(row=>row.reaction_type==="stellar_superconduct"))
      note("effects","此结果未保存领域层数分段；重新计算后可查看。现有状态点不代表全程层数覆盖。");
    return;
  }
  if(receipt.contract!=="special-state-timeline-v1"||receipt.version!==1){
    note("effects","领域层数记录版本不受支持，未猜测其区间。");return;
  }
  const names:Record<string,string>={field_accumulator:"领域累计",settled_damage_bonus:"结算增益"};
  for(const state of list(receipt.states)){
    if(!names[state.kind]||state.status!=="active"||state.knowledge!=="known"
      ||!Number.isInteger(state.layers)||!finite(state.start)||!finite(state.end)||state.end<=state.start)continue;
    put("effects",{id:key("special-state",state.id),kind:state.kind==="field_accumulator"?"field_layers":"settled_layers",
      character:null,...source(null,state.kind,state.area_id,`星超导 · ${names[state.kind]}`),
      label:`${names[state.kind]} ${state.layers} 层`,start:state.start,end:state.end,
      detail:{...copy(state),special_state_contract:receipt.contract,source_name:"星超导领域",
        input_order:state.start_boundary?.trace_index,
        interpretation:state.kind==="field_accumulator"
          ?"本段是当前四秒窗口正在累计的层数，尚不是本次伤害使用的结算层数。"
          :"本段是最近一次完成结算的增益层数；领域累计清零或领域结束不会自动提前清除该增益。"}});
  }
  if(receipt.status==="partial")note("effects","部分领域状态资料不完整；只显示有来源的区间，未知状态未按零层处理。");
}
