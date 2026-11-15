import {useEffect,useRef,useState} from "react";
import {Button} from "@deepseek-ai/dsh-client-ui-primitives";
import {appDomainIncompatibility,isAppDomainIncompatibility} from "./app-domain-errors";

function identity(value:unknown):string{return value===null?"没有记录":typeof value==="string"?value:JSON.stringify(value,null,2)??"没有记录";}

/** One App diagnosis card, never a DSH lifecycle or permission owner. */
export function AppDomainIncompatibilityNotice({error,compact=false}:{error:unknown;compact?:boolean}){
  const [copyState,setCopyState]=useState<"idle"|"copied"|"failed">("idle");
  const currentError=useRef(error);currentError.current=error;
  useEffect(()=>setCopyState("idle"),[error]);
  const facts=appDomainIncompatibility(error);
  if(!facts||!isAppDomainIncompatibility(error))return null;
  return <article className="gs-domain-card" role="status" aria-label="排轴版本兼容说明">
    <strong>{compact?"排轴候选不兼容":"这份排轴候选与当前版本不兼容"}</strong>
    <p>{facts.state==="missing"?"旧候选没有可核验的版本记录，不能继续计算、审核或应用。":"客户端、计算规则或拉表说明已发生变化，旧候选不能继续计算、审核或应用。"}聊天、历史和资料搜索仍可使用。</p>
    {error.operationMayHaveCompleted&&<p>先前操作的结果尚未确认，请保留现场并查看同步详情，不要重复提交。</p>}
    <details><summary>查看原因与适配提示</summary>
      <p>要继续这份排轴，请在新会话提供旧文件，让 Agent 按当前版本重新核查和计算。这里不会自动迁移或改写旧结果。</p>
      {facts.differences.length>0&&<ul>{facts.differences.map((row,index)=><li key={`${row.component}:${index}`}>{row.label}{row.recorded===null?"：旧记录缺失":"：版本不同"}</li>)}</ul>}
      <details><summary>技术详情</summary><code>{error.code}</code>{!facts.detailsValid&&<p>兼容详情格式未能核验，请保留错误码，避免依据不完整记录继续操作。</p>}{facts.differences.map((row,index)=><div key={`${row.component}:${index}`}><strong>{row.label}</strong><p>旧记录</p><pre>{identity(row.recorded)}</pre><p>当前版本</p><pre>{identity(row.current)}</pre></div>)}</details>
      {facts.migrationPrompt&&<><pre>{facts.migrationPrompt}</pre><Button size="sm" variant="outline" onClick={()=>{const target=error;void navigator.clipboard.writeText(facts.migrationPrompt!).then(()=>{if(currentError.current===target)setCopyState("copied");}).catch(()=>{if(currentError.current===target)setCopyState("failed");});}}>{copyState==="copied"?"已复制适配提示":"复制适配提示"}</Button>{copyState==="failed"&&<p role="alert">复制未完成，请选中上面的提示手动复制。</p>}</>}
    </details>
  </article>;
}
