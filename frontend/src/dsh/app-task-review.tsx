type Reference = Readonly<{label:string;position?:string}>;
type TaskRecord = Readonly<{id:string;description:string;disposition:string;references:readonly Reference[];part?:string;freshness?:string;observations:readonly string[]}>;
/** Advisory task facts; never used to enable/disable candidate commands. */
export type TaskReview = Readonly<{
  status:string; total:number;
  differences:readonly Readonly<{id:string;summary:string;references:readonly Reference[];next?:string}>[];
  requirements:readonly TaskRecord[]; notes:readonly TaskRecord[]; sources:readonly TaskRecord[];
  approximations:readonly string[]; unimplemented:readonly string[];
  calculationCurrency?:string; calculationStatus?:string; validationSummary?:string;
}>;
const object=(value:unknown):Record<string,unknown>|undefined=>value&&typeof value==="object"&&!Array.isArray(value)?value as Record<string,unknown>:undefined;
const text=(value:unknown)=>typeof value==="string"?value:undefined;
const descriptions=(value:unknown)=>Array.isArray(value)?value.filter((item):item is string=>typeof item==="string"):[];
function references(value:unknown):Reference[]{
  if(!Array.isArray(value))return [];
  return value.flatMap(item=>{
    if(typeof item==="string")return [{label:item}];
    const row=object(item);if(!row)return [];
    const label=text(row.reference)??text(row.id)??text(row.label);
    return label?[{label,position:text(row.pointer)}]:[];
  });
}
function records(value:unknown):TaskRecord[]{
  if(!Array.isArray(value))return [];
  return value.flatMap(item=>{
    const row=object(item);if(!row||typeof row.description!=="string")return [];
    return [{id:text(row.id)??"",description:row.description,disposition:text(row.disposition)??"unknown",
      part:text(object(row.source_part)?.label),freshness:text(row.freshness),
      observations:Array.isArray(row.observation_links)?row.observation_links.flatMap(value=>{
        const label=text(object(value)?.status);return label?[label]:[];
      }):[],
      references:[...references(row.source_refs),...references([object(row.source_part)?.source_ref]),
        ...references(row.input_refs),...references(row.adopted_refs)]}];
  });
}
export function parseTaskReview(value:unknown):TaskReview|undefined {
  const row=object(value);
  if(row?.contract!=="genshin-task-evidence-v1"||row.authority!=="advisory_only"||row.acceptance_effect!=="none")return;
  const requirements=records(row.requirements),notes=records(row.notes);
  const basis=object(row.report_basis);
  const differences=Array.isArray(row.differences)?row.differences.flatMap(item=>{
    const entry=object(item);return entry&&typeof entry.id==="string"&&typeof entry.summary==="string"
      ?[{id:entry.id,summary:entry.summary,references:references(entry.evidence_refs),next:text(object(entry.recovery)?.instruction)}]:[];
  }):[];
  return {status:text(row.status)??"unknown",
    total:Number.isSafeInteger(row.difference_count)&&Number(row.difference_count)>=differences.length?Number(row.difference_count):differences.length,
    differences,requirements,notes,sources:records(row.task_sources),
    calculationCurrency:text(basis?.calculation_currency),calculationStatus:text(basis?.calculation_status),validationSummary:text(basis?.validation_summary),
    approximations:requirements.length||notes.length
      ?[...requirements,...notes].filter(r=>r.disposition==="approximation").map(r=>r.description):descriptions(basis?.approximations),
    unimplemented:requirements.length?requirements.filter(r=>r.disposition==="pending"||r.disposition==="not_implemented").map(r=>r.description)
      :descriptions(basis?.not_completed)};
}
const dispositionLabels:Record<string,string>={pending:"尚待处理",not_implemented:"尚未完成",approximation:"采用近似",adopted:"作者已采用",
  implemented_elsewhere:"已在其他位置处理",not_applicable:"作者说明本场景不适用",user_excluded:"记录为用户明确不计"};
const calculationLabels:Record<string,string>={current:"现有计算证据与本次输入及资料一致。",no_current_calculation:"当前没有与输入匹配的计算结果。",
  calculation_database_changed:"现有计算来自旧资料库，需重新核对。",calculation_policy_changed:"现有计算使用旧执行规则，需重新核对。",
  calculation_scope_unverified:"计算缺少当前资料范围的核对依据。",calculation_input_unverified:"无法核实计算与保存的输入一致。",
  calculation_result_mismatch:"计算内容与保存记录不一致。"};
const freshnessLabels:Record<string,string>={stale:" · 需重新核对",unverified:" · 资料版本或执行规则尚未核实"};
const observationLabels:Record<string,string>={observed_match:"观察符合预期",observed_mismatch:"观察与预期不同",unknown:"尚未取得观察证据"};
function ReferenceList({items}:{items:readonly Reference[]}){
  return items.length?<ul>{items.map((item,i)=><li key={i}><code>{item.label}{item.position?` ${item.position}`:""}</code></li>)}</ul>:<p>尚未提供可定位的来源或输入位置。</p>;
}
export function TaskReviewDetails({review}:{review?:TaskReview}){
  if(!review||review.status==="no_task_source")return null;
  const byId=new Map([...review.requirements,...review.notes].map(row=>[row.id,row]));
  return <details className="gs-candidate-task-review">
    <summary>{review.status==="unavailable"?"任务对照暂时无法读取":review.total?`与要求对照：${review.total} 项待核对`
      :review.approximations.length?`采用了 ${review.approximations.length} 项近似`:"查看任务要求与近似说明"}</summary>
    <p>这里只核对已经记录的要求。计算通过不代表游戏机制全部算全；这些提醒不影响查看、编辑或审核。</p>
    {review.calculationCurrency&&calculationLabels[review.calculationCurrency]&&<p>{review.calculationCurrency==="current"&&review.calculationStatus==="diagnostic"
      ?"现有结果供诊断，当前计算仍有必需项目待核对。":calculationLabels[review.calculationCurrency]}</p>}
    {review.validationSummary&&<p>{review.validationSummary}</p>}
    {review.differences.map(item=>{
      const record=byId.get(item.id);
      return <details key={item.id}><summary>{item.summary}</summary>
        {record&&<p>{record.description}（{dispositionLabels[record.disposition]??"尚未说明处理结论"}）</p>}
        {item.next&&<p>{item.next}</p>}
        <strong>依据及对应位置</strong><ReferenceList items={record?.references.length?record.references:item.references}/>
      </details>;
    })}
    {review.total>review.differences.length&&<p>当前仅取得 {review.differences.length} 项摘要，共 {review.total} 项。请刷新候选查看完整对照。</p>}
    {review.approximations.length>0&&<><strong>采用的近似</strong><ul>{review.approximations.map((value,i)=><li key={i}>{value}</li>)}</ul></>}
    {review.unimplemented.length>0&&<><strong>尚未完成的要求</strong><ul>{review.unimplemented.map((value,i)=><li key={i}>{value}</li>)}</ul></>}
    {review.requirements.length>0&&<details><summary>查看已记录要求的处理情况</summary><ul>{review.requirements.map((row,i)=><li key={row.id||i}>{row.description}：{dispositionLabels[row.disposition]??"尚未说明"}{freshnessLabels[row.freshness??""]}</li>)}</ul></details>}
    {review.notes.some(row=>row.part)&&<details><summary>查看来源效果及执行观察</summary>
      {review.notes.filter(row=>row.part).map(row=><section key={row.id}><strong>{row.part}</strong><p>{row.description}</p>
        <p>{dispositionLabels[row.disposition]??"尚未说明处理结论"}{freshnessLabels[row.freshness??""]}</p>
        {row.observations.length>0&&<p>{row.observations.map(state=>observationLabels[state]??"尚未取得观察证据").join("、")}</p>}
        <ReferenceList items={row.references}/></section>)}
    </details>}
    {review.sources.length>0&&<details><summary>查看本任务的原文</summary>{review.sources.map((row,i)=><p key={row.id||i} style={{whiteSpace:"pre-wrap"}}>{row.description}</p>)}</details>}
    {review.total===0&&review.status!=="unavailable"&&<p>已记录的项目没有待核对项；未记录或未检查的部分仍需 Agent 说明。</p>}
  </details>;
}
