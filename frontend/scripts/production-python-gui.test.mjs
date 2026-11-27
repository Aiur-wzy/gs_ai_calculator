/** Actual production index/IIFE -> real GuiApi -> private pipe -> DSH -> engine.
 * Only provider HTTP and native picker selections are deterministic fixtures.
 * This never creates a window, launches the EXE or substitutes an editor.
 */
import assert from "node:assert/strict";
import {test} from "node:test";
import {spawn,execFileSync} from "node:child_process";
import {createInterface} from "node:readline";
import {randomUUID,webcrypto} from "node:crypto";
import {readFile,readdir,mkdtemp,rm,writeFile} from "node:fs/promises";
import {existsSync} from "node:fs";
import {resolve,join,sep} from "node:path";
import {fileURLToPath,pathToFileURL} from "node:url";
import {JSDOM,VirtualConsole,ResourceLoader} from "jsdom";
import {pinnedTestNode} from "./pinned-test-node.mjs";

const root=fileURLToPath(new URL("../../",import.meta.url));
const node=pinnedTestNode();
const web=join(root,"src/genshin_app/gui/web");
// Real concurrent runs have taken ~118s. This is an observation window for
// an isolated fixture, not a production Agent or UI execution budget.
const OBSERVATION_MS=360000,TEST_TIMEOUT_MS=420000;
async function until(predicate,message){const deadline=Date.now()+OBSERVATION_MS;while(Date.now()<=deadline){if(predicate())return;await new Promise(done=>setTimeout(done,10));}throw new Error(typeof message==="function"?message():message);}
function bridgeProcess(args){
  const child=spawn("python",["-m","tests.dsh_gui_projection_host",...args],{cwd:root,env:{...process.env,PYTHONIOENCODING:"utf-8",PYTHONUTF8:"1",GENSHIN_NATIVE_NODE:node},windowsHide:true,stdio:["pipe","pipe","pipe"]});
  const pending=new Map(),calls=[];let stderr="",closed=false;
  child.stderr.on("data",data=>{stderr+=String(data);});
  createInterface({input:child.stdout}).on("line",line=>{
    let row;try{row=JSON.parse(line);}catch{stderr+=`\nUnexpected bridge output: ${line}`;return;}
    const entry=pending.get(row.id);if(!entry)return;pending.delete(row.id);
    if(row.ok){entry.call.result=row.value;entry.resolve(row.value);}else{entry.call.failure=row.code;entry.reject(new Error(row.code));}
  });
  child.on("exit",code=>{closed=true;for(const entry of pending.values())entry.reject(new Error(`GuiApi bridge exited ${code}: ${stderr.slice(-2500)}`));pending.clear();});
  return {calls,get stderr(){return stderr;},request(method,...args){return new Promise((done,reject)=>{
    if(closed){reject(new Error("GuiApi bridge already closed"));return;}
    const id=randomUUID(),call={method,args};calls.push(call);pending.set(id,{resolve:done,reject,call});child.stdin.write(JSON.stringify({id,method,args})+"\n");
  });},async close(){if(!closed){await this.request("fixture_shutdown");child.stdin.end();await new Promise(done=>child.once("exit",done));}}};
}
class LocalAssets extends ResourceLoader{
  fetch(url){const parsed=new URL(url),path=parsed.protocol==="file:"?fileURLToPath(url):join(web,decodeURIComponent(parsed.pathname));if(!resolve(path).startsWith(resolve(web)+sep))throw new Error("Unreviewed browser resource");return readFile(path);}
}
function clickText(w,label,scope=w.document){const button=[...scope.querySelectorAll("button")].find(row=>row.textContent.trim()===label);assert.ok(button,`Missing button: ${label}`);button.click();return button;}
async function openFullChatFromEditor(w){
  const workbench=w.document.querySelector(".gs-editor-workbench");
  assert.ok(workbench,"Business workbench is unavailable");
  if(!workbench.querySelector(".gs-editor-assistant"))clickText(w,"打开 AI 小窗",workbench);
  await until(()=>workbench.querySelector(".gs-editor-assistant"),"Embedded assistant missing");
  clickText(w,"完整页面",workbench.querySelector(".gs-editor-assistant"));
  await until(()=>w.document.querySelector("[data-conversation-content]")&&!w.document.querySelector(".gs-editor-assistant"),"Full official chat did not open");
}
async function reopenConversation(w,sessionId){
  const row=`[role="treeitem"][data-row-key="session:${sessionId}"]`;
  await until(()=>w.document.querySelector(row),"Original DSH session is missing from the visible sidebar");
  w.document.querySelector(row).click();
  await until(()=>w.document.querySelector('[data-conversation-content]')?.dataset.conversationSession===sessionId,"The selected conversation did not open");
}
async function chooseBoardPage(w){await until(()=>[...w.document.querySelectorAll("button")].some(row=>row.textContent.includes("排轴白板")&&row.closest("#dsh-app-root")),"Business page command missing");const button=[...w.document.querySelectorAll("button")].find(row=>row.textContent.includes("排轴白板")&&row.closest("#dsh-app-root"));button.click();await until(()=>!w.document.getElementById("genshin-legacy-editor").hidden,"Business editor did not become visible");}
async function menu(w,command,group="文件"){const file=[...w.document.querySelectorAll("#workspace-menubar button")].find(row=>row.textContent.trim()===group);assert.ok(file);file.click();await until(()=>w.document.querySelector(`.workspace-menu-popup [data-command="${command}"]`),`Menu command ${command} unavailable`);w.document.querySelector(`.workspace-menu-popup [data-command="${command}"]`).click();}
async function openBoard(w,path,bridge){
  await bridge.request("fixture_pick_open",path);
  await chooseBoardPage(w);await menu(w,"open");
  await until(()=>w.WB.state.path===path,()=>`Actual native picker/WB.load failed: ${w.document.body.textContent.slice(-1800)}`);
  assert.equal(w.document.querySelector("#wb-path"),null,"Primary open must not ask for a typed path");
}
async function sendPrompt(w,prompt){
  const input=w.document.querySelector('[contenteditable]');assert.ok(input);input.focus();
  const range=w.document.createRange();range.selectNodeContents(input);range.collapse(false);w.getSelection().removeAllRanges();w.getSelection().addRange(range);w.document.dispatchEvent(new w.Event("selectionchange"));await new Promise(done=>setTimeout(done,30));
  const paste=new w.Event("paste",{bubbles:true,cancelable:true});Object.defineProperty(paste,"clipboardData",{value:{types:["text/plain"],items:[],files:[],getData:type=>type==="text/plain"?prompt:""}});input.dispatchEvent(paste);await new Promise(done=>setTimeout(done,30));input.dispatchEvent(new w.KeyboardEvent("keydown",{key:"Enter",code:"Enter",bubbles:true,cancelable:true}));
}
async function mount(bridge,errors,{waitForComposer=true,editorWidth=0}={}){
  const virtual=new VirtualConsole();virtual.on("jsdomError",error=>errors.push(error));virtual.on("error",(...rows)=>errors.push(new Error(rows.map(row=>row?.stack||String(row)).join(" "))));
  const html=await readFile(join(web,"index.html"),"utf8");
  // Give JSDOM the same non-opaque browser storage semantics as a WebView
  // origin. All assets still come only from the exact production web root.
  const dom=new JSDOM(html,{url:"https://genshin-shell.invalid/index.html",resources:new LocalAssets(),runScripts:"dangerously",pretendToBeVisual:true,virtualConsole:virtual,beforeParse(w){
    Object.assign(w,{TextEncoder,TextDecoder,Request,Response,ResizeObserver:class{observe(){}unobserve(){}disconnect(){}},matchMedia:()=>({matches:false,addEventListener(){},removeEventListener(){}})});
    Object.defineProperty(w.crypto,"subtle",{value:webcrypto.subtle});Object.defineProperty(w.document,"fonts",{value:{addEventListener(){},removeEventListener(){},ready:Promise.resolve()}});
    if(editorWidth)Object.defineProperty(w.HTMLElement.prototype,"clientWidth",{get(){return this.classList.contains("gs-editor-workbench")?editorWidth:0;}});
    w.Range.prototype.getBoundingClientRect=()=>({x:0,y:0,left:0,top:0,right:0,bottom:0,width:0,height:0,toJSON(){return this;}});w.Range.prototype.getClientRects=()=>[];w.HTMLElement.prototype.scrollIntoView=()=>{};
    w.HTMLDialogElement.prototype.showModal=function(){this.open=true;};w.HTMLDialogElement.prototype.close=function(){this.open=false;this.dispatchEvent(new w.Event("close"));};
    w.HTMLCanvasElement.prototype.getContext=()=>null;
    w.pywebview={api:new Proxy({}, {get(_target,method){if(typeof method!=="string"||method==="then")return undefined;return async(...args)=>w.JSON.parse(JSON.stringify(await bridge.request(method,...JSON.parse(JSON.stringify(args)))));}})};
  }});
  const w=dom.window;await until(()=>w.GenshinDshClient&&w.callApi,"Production scripts did not load");
  w.dispatchEvent(new w.Event("pywebviewready"));
  await until(()=>w.GenshinEditorBridge,()=>`App initialization failed: ${w.document.body.textContent.slice(-1500)}\n${bridge.stderr}\n${errors.map(row=>row.message).join("\n")}`);
  await w.GenshinDshClient.ready;
  if(waitForComposer)await until(()=>w.document.querySelector('[contenteditable]'),()=>`Official composer missing: ${w.document.body.textContent.slice(-2200)}`);
  return dom;
}

for(const scenario of ["baseline","file-wait","ordinary-permissions"]) test(`production IIFE edits, calculates, reviews, applies and saves through actual Python GUI methods (${scenario})`,{timeout:TEST_TIMEOUT_MS},async()=>{
  const fileWait=scenario==="file-wait",permissionWait=scenario==="ordinary-permissions";
  const candidateDraft=scenario==="baseline";
  const frontend=fileURLToPath(new URL("../",import.meta.url)),temp=await mkdtemp(join(frontend,".python-gui-"));let bridge,dom,passed=false;const errors=[];
  try{
    const setup=JSON.parse(execFileSync("python",["-m","tests.dsh_gui_projection_setup",temp],{cwd:root,encoding:"utf8",windowsHide:true}));
    const script=JSON.parse(await readFile(setup.script,"utf8"));
    const calculation=script.calls.at(-1);
    assert.equal(calculation.tool,"gs_calculate");
    const calculationRevision=calculation.arguments.expected_revision;
    if(candidateDraft){
      script.calls.push({tool:"gs_app_request_action",arguments:{action:"export_candidate_input_draft",expected_revision:calculationRevision,reason:"先保存这份候选输入，随后审核并同步正式白板",suggested_name:"工程候选.gencdraft.json"}},
        {tool:"ask_user_question",arguments:{questions:[{id:"candidate-draft",question:"可以保留计算结果供接下来查看吗？",options:[{label:"同意查看",description:"完成本轮对话；正式白板仍需在 App 审核"}]}]}});
    }
    if(fileWait){
      script.calls.unshift({tool:"gs_app_request_action",arguments:{action:"save_as",reason:"请先保存当前正式白板，再回答问题继续计算",suggested_name:"工程白板.json"}},
        {tool:"ask_user_question",arguments:{questions:[{id:"file-saved",question:"请先在 App 操作卡中保存白板，再确认继续计算。",options:[{label:"继续计算",description:"文件已核对，继续当前同一轮任务"}]}]}});
    }
    const deniedFile=join(temp,"permission-denied.txt"),allowedFile=join(temp,"permission-allowed.txt");
    if(permissionWait){
      const call=(path,description)=>({tool:"pwsh",arguments:{
        description,command:`Set-Content -LiteralPath '${path.replaceAll("'","''")}' -Value 'offline-approved-write' -NoNewline`,
        sandbox_permissions:"danger-full-access",justification:"仅在本次隔离测试目录写入一个验证文件，核对官方权限提示与用户决定。"}});
      script.calls.push(call(deniedFile,"核对用户拒绝后不会写文件"),call(allowedFile,"核对用户批准后完成隔离写入"));
    }
    await writeFile(setup.script,JSON.stringify(script));
    const bridgeArgs=["--root",join(temp,"runtime"),"--db",setup.database,"--script",setup.script,"--input",setup.input];
    bridge=bridgeProcess(bridgeArgs);
    dom=await mount(bridge,errors);const w=dom.window;console.info("joint: production client and business editor initialized");
    await openBoard(w,setup.input,bridge);assert.equal(w.WB.state.board.team.length,1);assert.equal(w.WB.state.board.actions.length,1);console.info("joint: actual user file-open loaded engineering board");
    await openFullChatFromEditor(w);
    const workspace=[...w.document.querySelectorAll('button[aria-label]')].find(row=>/^(Choose workspace|选择工作区)$/.test(row.getAttribute("aria-label")));
    if(workspace){workspace.click();await until(()=>[...w.document.querySelectorAll('[role="menuitem"], [role="option"]')].some(row=>row.textContent.includes("原神排轴")),"Default App workspace missing");[...w.document.querySelectorAll('[role="menuitem"], [role="option"]')].find(row=>row.textContent.includes("原神排轴")).click();}
    await until(()=>w.document.querySelector('[data-conversation-content]')?.dataset.conversationSession&&w.document.querySelector('[contenteditable]'),"Session composer not selected");
    const sessionId=w.document.querySelector('[data-conversation-content]').dataset.conversationSession;
    const prompt="请为当前用户工程白板补全事件和数值并计算，完成后汇报，然后由我审核同步。";
    await sendPrompt(w,prompt);
    if(permissionWait){
      await until(()=>w.document.querySelector('[data-approval-key]'),()=>`Official permission request missing: ${w.document.body.textContent.slice(-2500)}\n${bridge.stderr}`);
      const first=w.document.querySelector('[data-approval-key]'),firstKey=first.dataset.approvalKey;
      assert.equal(existsSync(deniedFile),false);assert.equal(existsSync(allowedFile),false);
      assert.equal((await w.callApi("dsh_client_session_status",{dsh_session_id:sessionId})).agent_running,true);
      const {binding}=await w.callApi("dsh_client_domain_binding",{dsh_session_id:sessionId});
      const candidate=await w.callApi("dsh_client_candidate",{conversation_id:binding.conversation_id});
      assert.equal(candidate.code,0);assert.equal(candidate.data.has_calculation,true);
      clickText(w,"拒绝",first);
      await until(()=>w.document.querySelector('[data-approval-key]')?.dataset.approvalKey!==firstKey&&w.document.querySelector('[data-approval-key]'),()=>`Second ordinary permission request missing after rejection: ${w.document.body.textContent.slice(-2500)}\n${bridge.stderr}`);
      assert.equal(existsSync(deniedFile),false);assert.equal(existsSync(allowedFile),false);
      clickText(w,"允许一次",w.document.querySelector('[data-approval-key]'));
      await until(()=>existsSync(allowedFile),()=>`Approved exact command did not create its isolated file: ${w.document.body.textContent.slice(-2500)}\n${bridge.stderr}`);
      assert.equal(existsSync(deniedFile),false);assert.equal(await readFile(allowedFile,"utf8"),"offline-approved-write");
      assert.equal(w.WB.state.result,null,"Ordinary tool approval must not apply the candidate's already accepted calculation");
      console.info("joint: official permission UI rejected one command and approved the next, same Turn continued");
    }
    if(fileWait){
      await until(()=>w.document.querySelector('[role="radio"][aria-label="继续计算"]'),()=>`Official DSH question did not enter a live wait: ${w.document.body.textContent.slice(-2500)}\n${bridge.stderr}`);
      const waiting=await w.callApi("dsh_client_session_status",{dsh_session_id:sessionId});
      assert.equal(waiting.agent_running,true,"This must exercise a live model Turn, not a completed-turn file card");
      clickText(w,"候选版面");
      await until(()=>[...w.document.querySelectorAll("button")].some(row=>row.textContent.trim()==="处理这项操作"),()=>`File card hidden during the live question: ${w.document.body.textContent.slice(-2500)}`);
      clickText(w,"处理这项操作");
      const savedBeforeCalculation=join(temp,"runtime/output/result.whiteboard.json");
      await until(()=>w.WB.state.path===savedBeforeCalculation,()=>`Same-Turn file card did not save: ${w.document.body.textContent.slice(-2500)}\n${bridge.stderr}`);
      const before=JSON.parse(await readFile(savedBeforeCalculation,"utf8"));
      assert.equal(before.board_id,w.WB.state.board.board_id);
      assert.equal(w.WB.state.result,null,"File processing must not pretend the unfinished candidate was calculated");
      // Saving uses the real editor and can bring the formal workspace forward.
      // Reopen its retained official conversation, then answer through DSH UI.
      await reopenConversation(w,sessionId);
      await until(()=>w.document.querySelector('[role="radio"][aria-label="继续计算"]'),"Waiting official question was not restored after file handling");
      const option=w.document.querySelector('[role="radio"][aria-label="继续计算"]');option.click();
      await until(()=>w.document.querySelector('[role="radio"][aria-label="继续计算"][aria-checked="true"]'),"Question choice did not update");
      w.document.querySelector('[role="radio"][aria-label="继续计算"]').dispatchEvent(new w.KeyboardEvent("keydown",{key:"Enter",code:"Enter",bubbles:true,cancelable:true}));
      console.info("joint: file card settled during a live official question, same Turn continued");
    }
    const draft=join(temp,"runtime/output/result.gencdraft.json");
    if(candidateDraft){
      const choice='[role="radio"][aria-label="同意查看"]';
      await until(()=>w.document.querySelector(choice),()=>`Ordinary question after actual candidate calculation missing: ${w.document.body.textContent.slice(-2500)}\n${bridge.stderr}`);
      assert.equal((await w.callApi("dsh_client_session_status",{dsh_session_id:sessionId})).agent_running,true);
      const official=w.WB.json();
      await until(()=>[...w.document.querySelectorAll("button")].some(row=>row.textContent.trim()==="候选版面"),"Candidate workspace command missing");
      clickText(w,"候选版面");
      await until(()=>[...w.document.querySelectorAll("button")].some(row=>row.textContent.trim()==="处理这项操作"),"The model's candidate draft card is unavailable");
      clickText(w,"处理这项操作");
      await until(()=>existsSync(draft)&&w.document.body.textContent.includes("候选输入草稿已保存"),()=>`Draft card did not complete: ${w.document.body.textContent.slice(-2600)}`);
      const input=JSON.parse(await readFile(draft,"utf8"));
      assert.equal(input.source.candidate_revision,calculationRevision);
      assert.equal(input.input.source_document.payload.team[0].name,"工程角色");
      assert.equal(w.WB.json(),official);
      const publication=bridge.calls.find(row=>row.method==="document_json_call"&&row.args[0]==="dsh_client_publish_candidate_input_draft");
      assert.ok(publication?.result,"Candidate save must use the actual GuiApi publication route");
      assert.equal(JSON.parse(publication.result).code,0);
      const publishRequest=JSON.parse(publication.args[1])[0];
      const {binding}=await w.callApi("dsh_client_domain_binding",{dsh_session_id:sessionId});
      assert.equal(publishRequest.conversation_id,binding.conversation_id);
      const actions=await w.callApi("dsh_client_app_actions",{conversation_id:binding.conversation_id});
      const action=actions.data.actions.find(row=>row.action_id===publishRequest.action_id);
      assert.equal(action.status,"completed");assert.notEqual(action.target.workspace_id,publishRequest.conversation_id);
      console.info("joint: model-requested candidate input saved before approval without modifying the formal board");
      await reopenConversation(w,sessionId);
      await until(()=>w.document.querySelector(choice),"Live DSH question did not survive the candidate file save");
      w.document.querySelector(choice).click();
      await until(()=>w.document.querySelector(choice+'[aria-checked="true"]'),"Ordinary answer selection did not update");
      w.document.querySelector(choice).dispatchEvent(new w.KeyboardEvent("keydown",{key:"Enter",code:"Enter",bubbles:true,cancelable:true}));
      await until(()=>w.document.body.textContent.includes("phase5b official answer"),"The ordinary answer did not finish the same model Turn");
      assert.equal(w.WB.json(),official,"A normal DSH answer must not approve formal candidate application");
      assert.equal(w.WB.state.result,null);
    }
    await until(()=>[...w.document.querySelectorAll("button")].some(row=>row.textContent.trim()==="审核候选"),()=>`Review not offered: ${w.document.body.textContent.slice(-3500)}\n${JSON.stringify(bridge.calls.filter(row=>row.failure||row.method==="dsh_client_bind_document")).slice(-2000)}\n${bridge.stderr}`);
    assert.ok(w.document.body.textContent.includes(prompt));console.info("joint: official model loop completed actual App calculation");
    clickText(w,"审核候选");
    await until(()=>[...w.document.querySelectorAll('[role="dialog"] button')].some(row=>row.textContent.trim()==="同意并同步"),()=>`Direct session review modal did not open: ${w.document.body.textContent.slice(-2500)}`);
    const review=w.document.querySelector('[role="dialog"]');
    assert.ok(review.textContent.includes(`候选版本 ${calculationRevision}`)&&review.textContent.includes("工程角色")&&review.textContent.includes("1 名角色 · 1 个动作"));assert.equal(w.WB.state.result,null);
    const sync=clickText(w,"同意并同步",review);sync.click();
    await until(()=>w.WB.state.result?.result?.summary?.total_damage>0||w.WB.state.result?.summary?.total_damage>0,()=>`Real Renderer application incomplete: ${w.document.body.textContent.slice(-2800)}`);
    const formal=w.document.getElementById("tab-whiteboard");
    await until(()=>w.WS.tab()==="whiteboard"&&!w.document.getElementById("genshin-legacy-editor").hidden&&!formal.hidden&&[...formal.querySelectorAll("button")].some(row=>row.textContent.trim()==="计算结果另存为…"),()=>`Durable ACK did not open the formal board with calculated Save As: ${w.document.getElementById("dsh-app-root").textContent.slice(-2000)}`);
    console.info("joint: one confirmation completed Renderer CAS, durable ACK and formal editor navigation");
    const candidateCommands=bridge.calls.filter(row=>row.method==="document_json_call"&&row.args[0]==="dsh_client_candidate_command").map(row=>JSON.parse(row.args[1])[0].command_id);
    assert.deepEqual(candidateCommands,["candidate.review","candidate.apply.prepare","application.ack"],"An immediate duplicate click must not submit another transaction");
    clickText(w,"计算结果另存为…",formal);await until(()=>formal.textContent.includes("结果文件："),()=>`Calculated save failed: ${w.document.body.textContent.slice(-2200)}`);
    const savesBefore=bridge.calls.filter(row=>row.method==="document_json_call"&&row.args[0]==="whiteboard_save"&&row.result!==undefined).length;
    await menu(w,"save-as");
    const saved=join(temp,"runtime/output/result.whiteboard.json");
    await until(()=>w.WB.state.path===saved&&bridge.calls.filter(row=>row.method==="document_json_call"&&row.args[0]==="whiteboard_save"&&row.result!==undefined).length>savesBefore,()=>`Real native Save As failed: ${w.document.body.textContent.slice(-2000)}`);
    assert.equal(w.document.querySelector("#wb-path"),null,"Primary Save As must not display a typed path form");
    const fresh=await bridge.request("fixture_fresh_read");assert.equal(fresh.board.code,0);assert.equal(fresh.calculated.code,0);assert.ok(fresh.calculated.data.calculated.result.summary.total_damage>0);
    if(candidateDraft){
      const fingerprint=fresh.calculated.data.calculated.result_receipt.result_fingerprint;
      await w.GenshinEditorBridge.show("result");
      assert.equal(w.RS.view().kind,"whiteboard");
      assert.ok(w.document.querySelector("#result-body").textContent.includes(fresh.board.data.board.title));
      let lastRequest;
      for(const format of ["csv","xlsx"]){
        const button=w.document.querySelector(`#btn-exp-${format}`);assert.equal(button.disabled,false);
        const path=join(temp,`runtime/output/result.${format}`);
        button.click();
        await until(()=>existsSync(path)&&!w.RS.state.exportPending,()=>`Formal ${format} export failed: ${w.document.body.textContent.slice(-2200)}`);
        const exported=bridge.calls.filter(row=>row.method==="document_json_call"&&row.args[0]==="dsh_client_publish_whiteboard_result").at(-1);
        assert.equal(JSON.parse(exported.result).data.result_fingerprint,fingerprint);
        lastRequest=JSON.parse(exported.args[1])[0];
        const content=await readFile(path);assert.ok(format==="csv"?content.toString("utf8").includes(fingerprint):content.subarray(0,2).toString()==="PK");
      }
      const rejectedPath=join(temp,"runtime/output/wrong-result.xlsx");
      const mismatch=await w.callApi("dsh_client_publish_whiteboard_result",{...lastRequest,output_path:rejectedPath,
        expected_result_fingerprint:fingerprint==="a".repeat(64)?"b".repeat(64):"a".repeat(64)});
      assert.equal(mismatch.reason,"native_whiteboard_delivery_capability_mismatch");assert.equal(existsSync(rejectedPath),false);
      await chooseBoardPage(w);
      console.info("joint: the formal report exported CSV and XLSX directly from its applied result");
    }
    // After an accepted candidate changed the actual formal document, a second
    // ordinary prompt must retain its original Session binding and conversation.
    await openFullChatFromEditor(w);
    await until(()=>w.document.querySelector('[contenteditable]')&&!w.document.querySelector('.gs-editor-assistant'),"Full chat not restored for second message");
    assert.equal(w.document.querySelector('[data-conversation-content]').dataset.conversationSession,sessionId);
    const secondPrompt="请只解释刚才已经保存的工程计算结果，不修改白板，不重新运行计算。";
    const boardBefore=w.WB.json(),answersBefore=w.document.body.textContent.split("phase5b official answer").length;
    await sendPrompt(w,secondPrompt);
    await until(()=>w.document.body.textContent.includes(secondPrompt)&&w.document.body.textContent.split("phase5b official answer").length>answersBefore,
      ()=>`Second user/final conversation missing: ${w.document.getElementById("dsh-app-root").textContent.slice(-2500)}\n${bridge.stderr}`);
    assert.equal(w.WB.json(),boardBefore,"An explanatory reply must not change the formal board");
    const status=await w.callApi("dsh_client_session_status",{dsh_session_id:sessionId});
    assert.equal(status.accounting.provider_requests,8);if(!fileWait)assert.ok(status.accounting.tool_capacity_reservations.mutation>=4);
    console.info("joint: second ordinary message and final reply persisted without another calculation");
    [...w.document.querySelectorAll("button")].find(row=>row.textContent.includes("任务中心")&&row.closest("#dsh-app-root")).click();
    await until(()=>w.document.querySelector('section[aria-label="任务中心"]')?.textContent.includes("本轮结束"),()=>`Task Center did not use the real final turn: ${w.document.getElementById("dsh-app-root").textContent.slice(-2200)}`);
    assert.ok(w.document.querySelector('section[aria-label="任务中心"]').textContent.includes("候选此前已同步"));
    assert.equal(bridge.calls.filter(row=>row.method==="document_json_call"&&row.args[0]==="dsh_client_bind_document").length,2);
    // The baseline restarts the Host and DSH sidecar. The other cases reopen
    // the renderer within the original Host to exercise both file lifetimes.
    await w.GenshinDshClient.dispose();w.close();dom=undefined;
    if(candidateDraft){await bridge.close();bridge=bridgeProcess(bridgeArgs);}
    dom=await mount(bridge,errors,{waitForComposer:false,editorWidth:candidateDraft?540:0});const reopened=dom.window;
    console.info(`joint: reopened production renderer (${candidateDraft?"new Host":"existing Host"})`);
    const calculated=join(temp,"runtime/output/result.calculated.json");
    await bridge.request("fixture_pick_open",calculated);
    await chooseBoardPage(reopened);await menu(reopened,"open");
    await until(()=>reopened.RS.view().kind==="saved"&&reopened.WS.tab()==="result",()=>`Independent report open failed: ${reopened.document.body.textContent.slice(-2100)}`);
    assert.equal(reopened.WB.state.board,null);assert.equal(reopened.PL.state.plan,null);
    console.info("joint: the ordinary file opener displayed the saved package as an independent report");
    await openBoard(reopened,saved,bridge);
    if(candidateDraft){
      await until(()=>reopened.document.querySelector('.gs-editor-workbench[data-layout="narrow"]'),"Narrow workbench layout was not applied");
      clickText(reopened,"打开 AI 小窗");
      await until(()=>reopened.document.querySelector('.gs-editor-business')?.hidden,"Narrow chat did not park the business page");
      await reopened.GenshinEditorBridge.show("whiteboard");
      assert.equal(reopened.document.querySelector('.gs-editor-business').hidden,false,"A confirmed business navigation must reveal its page above the narrow chat");
    }
    await bridge.request("fixture_pick_open",calculated);
    await menu(reopened,"return","计算与交接");
    await until(()=>reopened.WB.state.result,()=>`Fresh GUI did not accept calculated result: ${reopened.document.body.textContent.slice(-2000)}`);assert.equal(reopened.WB.state.mode,"damage");assert.ok(reopened.document.getElementById("tab-whiteboard").textContent.includes("工程角色"));
    assert.equal(bridge.calls.filter(row=>row.method==="document_json_call"&&row.args[0]==="dsh_client_bind_document").length,candidateDraft?0:2,"File import must not rebind or send a new model prompt");
    if(candidateDraft){
      const official=reopened.WB.json();
      const savedIdentity=JSON.parse(await readFile(draft,"utf8")).compatibility.workspace_identity;
      assert.deepEqual(await bridge.request("fixture_workspace_identity"),savedIdentity,"App/Skill identity changed between the two Hosts; use one unchanged source cohort for the cold-import test");
      await bridge.request("fixture_pick_open",draft);await menu(reopened,"open");
      await until(()=>reopened.document.querySelector('section[aria-label="Agent 候选工作区"]')?.textContent.includes("输入草稿已打开"),()=>`Cold candidate draft import failed: ${reopened.document.body.textContent.slice(-2300)}\n${bridge.stderr}`);
      assert.equal(reopened.WB.json(),official,"Opening a candidate input must keep the formal board intact");
      console.info("joint: ordinary candidate draft open restored input without changing the formal board");
      await bridge.request("fixture_pick_open",setup.native_input);
      await chooseBoardPage(reopened);await menu(reopened,"open");
      await until(()=>reopened.WS.tab()==="rotation"&&reopened.PL.state.plan?.manual_template?.meta?.name==="工程计算方案",()=>`Ordinary native plan open failed: ${reopened.document.body.textContent.slice(-2300)}`);
      assert.equal(reopened.WB.json(),official,"Opening a calculation plan must not replace the formal board");
    }
    assert.equal(errors.filter(row=>/Uncaught|slot entry crashed/.test(row.message)).length,0,errors.map(row=>row.message).join("\n"));
    passed=true;
  }catch(error){
    console.error(error);
    console.error(`Joint projection diagnostics: ${bridge?.stderr||""}\n${errors.map(row=>row.message).join("\n")}\n${JSON.stringify(bridge?.calls.filter(row=>row.failure)).slice(-2200)}`);
    console.error(`Retained failed joint test root: ${temp}`);
    console.error("Failed DSH RPC",JSON.stringify(bridge?.calls.filter(row=>row.method==="dsh_client_rpc"&&row.result?.ok===false).map(row=>({endpoint:row.args[0].endpoint,error:row.result.error?.code,http:row.result.error?.details?.status})).slice(-12)));
    for(const file of await readdir(temp,{recursive:true}))if(file.endsWith("native-app-call-ledger.json")){
      const ledger=JSON.parse(await readFile(join(temp,file),"utf8"));console.error(`Domain call summary: ${JSON.stringify(Object.values(ledger.entries).map(row=>{const result=JSON.parse(row.result_json||"{}");return {tool:row.identity?.tool_name,state:row.state,reason:result.reason||row.terminal_reason,revision:result.data?.revision};}))}`);
    }
    for(const file of await readdir(temp,{recursive:true}))if(file.includes("process-diagnostics")&&file.endsWith(".json"))console.error(file,await readFile(join(temp,file),"utf8"));
    throw error;
  }finally{
    await dom?.window.GenshinDshClient?.dispose();dom?.window.close();await bridge?.close();
    if(!resolve(temp).startsWith(resolve(frontend)+sep+".python-gui-"))throw new Error("unsafe_test_cleanup_path");if(passed)await rm(temp,{recursive:true,force:true});
  }
});


