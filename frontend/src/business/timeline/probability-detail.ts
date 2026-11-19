/** Describe stored outcomes; never sample, average balances, or infer success. */
export function probabilityFields(detail:any):[string,string][] {
  if(detail?.type==="expected_discrete_count")return [
    ["计算口径","累计期望次数向下取整"],
    ["累计期望次数",String(detail.expected_count)],
    ["累计应用次数",String(detail.applied_count)],
    ["本次新增触发",String(detail.new_activations)],
  ];
  const shared=detail?.probability?.contract==="shared-resource-probability-v1"?detail.probability:null;
  const trial=detail?.trial?.contract==="probabilistic-resource-v1"?detail.trial:null;
  const value=shared||trial;
  if(!value||!["custom","expected"].includes(value.mode))return [];
  const expected=value.mode==="expected";
  const fields:[string,string][]=[["计算口径",expected?
    "按期望次数计算；离散收益默认累计后向下取整，不是随机模拟":
    "指定触发情景；不是概率期望，也不是实战保证"]];
  const chance=shared?.chance??trial?.parameters?.chance;
  if(Number.isFinite(chance)&&chance>=0&&chance<=1)
    fields.push(["单次触发概率",`${Number((chance*100).toFixed(6))}%`]);
  const sampled=shared?shared.sampled:trial.executed;
  if(expected){
    if(Number.isFinite(value.eligible_count))fields.push(["符合条件次数",String(value.eligible_count)]);
    if(Number.isFinite(value.expected_count))fields.push(["期望触发次数",String(value.expected_count)]);
  }else fields.push(["本次判定",sampled===false?"未判定（未执行或未满足条件）":
    sampled===true&&typeof value.outcome==="boolean"?(value.outcome?"按触发处理":"按未触发处理"):"记录不完整"]);
  for(const [key,label] of [["executed_units","实际执行量"],["consumed_units","实际消费"],
      ["created_units","实际生成"],["overflow_units","溢出数量"]])
    if(Number.isFinite(value[key])&&value[key]>=0)fields.push([label,String(value[key])]);
  if(shared?.kind==="waive_spend"&&sampled===true&&value.outcome===true)
    fields.push(["免消耗说明","本次执行成功，消费为零；不会增加消费累计，仍遵守成功触发冷却"]);
  return fields;
}
