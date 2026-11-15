import {useState} from "react";
import {Button,Modal} from "@deepseek-ai/dsh-client-ui-primitives";
import type {AppCandidateGuiApi,AppDomainBinding} from "./app-domain-candidate";
import type {AppEditorBridge} from "./app-editor-bridge";
import {CandidatePreview} from "./app-candidate-preview";
import {restoreSavedWhiteboard,unwrapSaved,validateSaved} from "./app-saved-whiteboard";

export function SavedWhiteboardControls({api,binding,editor}:{api:AppCandidateGuiApi;binding:AppDomainBinding;editor?:AppEditorBridge}){
  const [saved,setSaved]=useState<Record<string,unknown>>(),[busy,setBusy]=useState(false),[message,setMessage]=useState(""),[error,setError]=useState("");
  const [delivery,setDelivery]=useState<{capability:string;revision:number;receipt:string}>();
  const read=async()=>{if(!api.dsh_client_saved_whiteboard||busy)return;setBusy(true);setError("");try{const result=unwrapSaved(await api.dsh_client_saved_whiteboard({conversation_id:binding.conversationId,command:"inspect",arguments:{}}));if(result.status==="none"){setMessage("本会话还没有已同步的白板版本。");return;}if(result.status!=="available")throw new Error("saved_whiteboard_response_invalid");setSaved(validateSaved(result));setMessage("");}catch(reason){setMessage("暂时无法查看已同步版本，当前文档没有改动。");setError(reason instanceof Error?reason.message:"saved_whiteboard_failed");}finally{setBusy(false);}};
  const restore=async()=>{if(!saved||!editor?.application||busy)return;setBusy(true);setError("");try{const restored=await restoreSavedWhiteboard(api,binding,editor.application,saved);const capability=(restored.result.calculated_delivery as {delivery_capability?:unknown}|undefined)?.delivery_capability;if(typeof capability==="string")setDelivery({capability,revision:saved.revision as number,receipt:saved.receipt_fingerprint as string});setSaved(undefined);setMessage(restored.current?"已恢复此前同步的白板与结果，尚未保存文件。":"此前版本已恢复，当前文档又有后续修改，请以当前白板为准。");}catch(reason){setMessage("未能恢复此前版本，或恢复结果尚未核实。当前文档与历史记录均保留，不会自动覆盖或重试。");setError(reason instanceof Error?reason.message:"saved_whiteboard_failed");}finally{setBusy(false);}};
  const save=async()=>{if(!delivery||!api.dsh_client_save_whiteboard_result||busy)return;setBusy(true);setError("");try{const result=unwrapSaved(await api.dsh_client_save_whiteboard_result({conversation_id:binding.conversationId,delivery_capability:delivery.capability,expected_revision:delivery.revision,expected_receipt:delivery.receipt}));setMessage(result.cancelled?"已取消另存为，结果仍保留在白板中。":"计算结果已另存，可以在 App 中重新打开。");}catch(reason){setMessage("计算结果未完成另存，当前白板与结果已保留。");setError(reason instanceof Error?reason.message:"saved_whiteboard_save_failed");}finally{setBusy(false);}};
  return <section className="gs-saved-whiteboard"><div className="gs-candidate-toolbar"><Button size="sm" variant="outline" disabled={busy} onClick={()=>void read()}>查看此前已同步版本</Button>{delivery&&<Button size="sm" variant="outline" disabled={busy} onClick={()=>void save()}>恢复结果另存为…</Button>}</div>{message&&<p role={error?"alert":"status"}>{message}</p>}{error&&<details><summary>操作详情</summary><code>{error}</code></details>}
    <Modal open={!!saved} title="此前已同步的白板" description="这是以前已经审核并同步的版本。查看不会更改当前白板；只有再次确认后才恢复。当前文档有其他修改时会拒绝覆盖。" closeLabel="关闭已同步版本" onClose={()=>{if(!busy)setSaved(undefined);}} footer={<><Button variant="outline" disabled={busy} onClick={()=>setSaved(undefined)}>仅查看，返回</Button><Button disabled={busy||!editor?.application} onClick={()=>void restore()}>{busy?"正在恢复…":"确认恢复到正式白板"}</Button></>}>
      {saved&&<><p>候选版本 {String(saved.revision)} · {saved.application_kind==="whiteboard_calculated"?"含计算结果":"阶段草稿"}</p><CandidatePreview document={saved.board as Record<string,unknown>} kind="whiteboard" editor={editor}/></>}{error&&<p role="alert">{message}</p>}
    </Modal>
  </section>;
}
