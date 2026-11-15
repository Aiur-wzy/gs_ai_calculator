import type {Context} from "@deepseek-ai/cordis";
import type {} from "@deepseek-ai/dsh-client-ui-settings/client";
import {Button} from "@deepseek-ai/dsh-client-ui-primitives";
import {useEffect,useState} from "react";
import {callAppDocumentApi} from "./app-document-transport";

export interface AppRuntimeSettingsApi {
  agent_budget_settings():Promise<unknown>;
  agent_update_budget_settings(overrides:Record<string,unknown>,expected_generation:number):Promise<unknown>;
  agent_reset_budget_settings(expected_generation:number):Promise<unknown>;
}
type Row=Record<string,unknown>;
type Settings={generation:number;effective:Row;defaults:Row;overrides:Row;limits:Row;apply_scope:string};
const object=(value:unknown):Row=>{if(!value||typeof value!=="object"||Array.isArray(value))throw new Error("app_settings_response_invalid");return value as Row;};
function snapshot(value:unknown):Settings{const envelope=object(value);if(envelope.code!==0)throw new Error(typeof envelope.message==="string"?envelope.message:"设置未能更新");const row=object(envelope.data);if(!Number.isSafeInteger(row.generation)||(row.generation as number)<0)throw new Error("app_settings_response_invalid");return {generation:row.generation as number,effective:object(row.effective),defaults:object(row.defaults),overrides:object(row.overrides),limits:object(row.limits),apply_scope:String(row.apply_scope)};}
const LABELS:Record<string,string>={wall_clock_seconds:"运行时间（秒）",provider_steps:"模型步骤",app_tool_calls:"App 工具调用",web_tool_calls:"联网工具调用",total_tool_calls:"全部工具调用",web_search_calls:"搜索调用",web_search_queries:"搜索词数量",web_fetch_calls:"网页读取",per_request_output_tokens:"单次输出 Token",cumulative_output_tokens:"累计输出 Token",cumulative_input_soft_tokens:"累计输入提示线",cumulative_input_hard_tokens:"累计输入容量",cumulative_uncached_input_tokens:"未缓存输入 Token",read_tool_calls:"App 读取调用",mutation_tool_calls:"App 修改调用"};
function Fields({limits,defaults,draft,onChange,path=[]}:{limits:Row;defaults:Row;draft:Row;onChange:(path:string[],value:unknown,enabled:boolean)=>void;path?:string[]}){
  return <>{Object.entries(limits).map(([key,value])=>{
    const limit=object(value),fieldPath=[...path,key],label=LABELS[key]||key;
    if(typeof limit.type!=="string"&&!Object.hasOwn(limit,"adjustable"))return <details key={key}><summary>{label}</summary><Fields limits={limit} defaults={defaults[key]?object(defaults[key]):{}} draft={draft[key]?object(draft[key]):{}} path={fieldPath} onChange={onChange}/></details>;
    if(limit.adjustable===false)return null;
    const enabled=Object.hasOwn(draft,key),current=enabled?draft[key]:defaults[key],type=typeof limit.type==="string"?limit.type:"integer";
    const identifier=fieldPath.join("."),inputProps={"aria-label":label,disabled:!enabled};
    return <div className="gs-setting-field" key={key}>
      <label><input type="checkbox" aria-label={`自定义${label}`} checked={enabled} onChange={event=>onChange(fieldPath,current??(type==="boolean"?false:type==="integer"||type==="number"?0:""),event.target.checked)}/>{label}</label>
      {type==="boolean"?<input {...inputProps} type="checkbox" checked={current===true} onChange={event=>onChange(fieldPath,event.target.checked,true)}/>:Array.isArray(limit.values)?<select {...inputProps} value={String(current??"")} onChange={event=>onChange(fieldPath,event.target.value,true)}><option value="">沿用默认</option>{limit.values.map(value=><option key={String(value)} value={String(value)}>{String(value)}</option>)}</select>:<input {...inputProps} type={['integer','number'].includes(type)?"number":"text"} step={type==="number"?"any":1} min={typeof limit.min==="number"?limit.min:undefined} max={typeof limit.max==="number"?limit.max:undefined} value={current===undefined?"":String(current)} onChange={event=>onChange(fieldPath,event.target.type==="number"?event.target.value===""?"":Number(event.target.value):event.target.value,true)}/>}
      <details><summary>参数详情</summary><code>{identifier}</code><p>{enabled?"使用自定义值":"跟随维护的默认值"}</p></details>
    </div>;
  })}</>;
}
function setField(source:Row,path:string[],value:unknown,enabled:boolean):Row{
  const next={...source},key=path[0]!;if(path.length===1){if(enabled)next[key]=value;else delete next[key];}
  else{const child=setField(source[key]?object(source[key]):{},path.slice(1),value,enabled);if(Object.keys(child).length)next[key]=child;else delete next[key];}return next;
}
export function AppRuntimeSettings({api}:{api:AppRuntimeSettingsApi}){
  const [state,setState]=useState<Settings>(),[draft,setDraft]=useState<Row>({}),[busy,setBusy]=useState(false),[message,setMessage]=useState(""),[error,setError]=useState(false),[version,setVersion]=useState(0);
  useEffect(()=>{let live=true;setBusy(true);void callAppDocumentApi(api,"agent_budget_settings",[]).then(snapshot).then(value=>{if(live){setState(value);setDraft(value.overrides);setError(false);setMessage("");}}).catch(()=>{if(live){setMessage("暂时无法读取设置，请刷新后重试。");setError(true);}}).finally(()=>{if(live)setBusy(false);});return()=>{live=false;};},[api,version]);
  const save=async(reset=false)=>{if(!state||busy)return;setBusy(true);setMessage("");try{const method=reset?"agent_reset_budget_settings":"agent_update_budget_settings",value=snapshot(await callAppDocumentApi(api,method,reset?[state.generation]:[draft,state.generation]));setState(value);setDraft(value.overrides);setError(false);setMessage("设置已保存，将从下一轮任务生效。");}catch(reason){setMessage(reason instanceof Error?reason.message:"设置未能更新，请刷新后重试。");setError(true);}finally{setBusy(false);}};
  return <section className="gs-runtime-settings" aria-label="任务预算"><h2>任务预算</h2><p>默认预算根据完整排轴任务维护。仅勾选需要自定义的项；运行中的任务保持启动时的预算。</p>
    <form onSubmit={event=>{event.preventDefault();void save();}}><fieldset disabled={busy}>{state&&<Fields limits={state.limits} defaults={state.defaults} draft={draft} onChange={(path,value,enabled)=>setDraft(source=>setField(source,path,value,enabled))}/>}</fieldset><div className="gs-candidate-toolbar"><Button type="submit" disabled={busy||!state}>保存设置</Button><Button type="button" variant="outline" disabled={busy||!state} onClick={()=>void save(true)}>恢复维护默认值</Button><Button type="button" variant="outline" disabled={busy} onClick={()=>setVersion(value=>value+1)}>重新读取</Button></div></form>{message&&<p role={error?"alert":"status"}>{message}</p>}
  </section>;
}
export function createAppRuntimeSettingsPlugin(api:AppRuntimeSettingsApi){return {name:"genshin-runtime-settings",inject:["slots"],apply(ctx:Context){ctx.slots.inject("settings.section",()=>ctx.slots.register({name:"settings.section",id:"genshin-budget-settings",label:"任务预算",order:130},()=> <AppRuntimeSettings api={api}/>));}};}
