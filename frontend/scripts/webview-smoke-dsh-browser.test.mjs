/** Actual production index/IIFE -> real GuiApi -> private pipe -> DSH -> engine.
 * Only provider HTTP and native picker selections are deterministic fixtures.
 * This never creates a window, launches the EXE or substitutes an editor.
 */
import assert from "node:assert/strict";
import {test} from "node:test";
import {spawn,execFileSync} from "node:child_process";
import {createInterface} from "node:readline";
import {randomUUID,webcrypto} from "node:crypto";
import {readFile,readdir,mkdtemp,rm} from "node:fs/promises";
import {resolve,join,sep} from "node:path";
import {fileURLToPath,pathToFileURL} from "node:url";
import {JSDOM,VirtualConsole,ResourceLoader} from "jsdom";
import {pinnedTestNode} from "./pinned-test-node.mjs";

const root=fileURLToPath(new URL("../../",import.meta.url));
const node=pinnedTestNode();
const web=join(root,"src/genshin_app/gui/web");
// Keep the real fixture's ~118s concurrent history inside the observation
// window; these values do not change any product UI or Agent defaults.
const FIXTURE_OBSERVATION_MS=180000,OBSERVATION_MS=360000,TEST_TIMEOUT_MS=420000;
async function until(predicate,message){const deadline=Date.now()+OBSERVATION_MS;while(Date.now()<=deadline){if(predicate())return;await new Promise(done=>setTimeout(done,10));}throw new Error(typeof message==="function"?message():message);}
function bridgeProcess(args){
  const child=spawn("python",["-m","tests.dsh_gui_projection_host",...args],{cwd:root,env:{...process.env,PYTHONIOENCODING:"utf-8",PYTHONUTF8:"1",GENSHIN_NATIVE_NODE:node},windowsHide:true,stdio:["pipe","pipe","pipe"]});
  const pending=new Map(),calls=[];let stderr="",closed=false,openSelections=[],calculatedSelection=null;
  child.stderr.on("data",data=>{stderr+=String(data);});
  createInterface({input:child.stdout}).on("line",line=>{
    let row;try{row=JSON.parse(line);}catch{stderr+=`\nUnexpected bridge output: ${line}`;return;}
    const entry=pending.get(row.id);if(!entry)return;pending.delete(row.id);
    entry.call.finishedAt=Date.now();if(row.ok){entry.call.result=row.value;entry.resolve(row.value);}else{entry.call.failure=row.code;entry.reject(new Error(row.code));}
  });
  child.on("exit",code=>{closed=true;for(const entry of pending.values())entry.reject(new Error(`GuiApi bridge exited ${code}: ${stderr.slice(-2500)}`));pending.clear();});
  return {calls,get stderr(){return stderr;},setPickerSelections(opens,calculated){openSelections=[...opens];calculatedSelection=calculated;},async request(method,...args){
    const actual=method==="document_json_call"?args[0]:method;
    const request=method==="document_json_call"?JSON.parse(args[1])[0]:args[0];
    if(actual==="dsh_client_file_dialog"&&request?.action==="open"){
      const selected=request.document_kind==="calculated"?calculatedSelection:openSelections.shift();
      assert.ok(selected,"Unplanned native picker request");await this.request("fixture_pick_open",selected);
    }
    return new Promise((done,reject)=>{
    if(closed){reject(new Error("GuiApi bridge already closed"));return;}
    const id=randomUUID(),call={method,args,startedAt:Date.now()};calls.push(call);pending.set(id,{resolve:done,reject,call});child.stdin.write(JSON.stringify({id,method,args})+"\n");
  });},async close(){if(!closed){await this.request("fixture_shutdown");child.stdin.end();await new Promise(done=>child.once("exit",done));}}};
}
class LocalAssets extends ResourceLoader{
  fetch(url){const parsed=new URL(url),path=parsed.protocol==="file:"?fileURLToPath(url):join(web,decodeURIComponent(parsed.pathname));if(!resolve(path).startsWith(resolve(web)+sep))throw new Error("Unreviewed browser resource");return readFile(path);}
}
function clickText(w,label,scope=w.document){const button=[...scope.querySelectorAll("button")].find(row=>row.textContent.trim()===label);assert.ok(button,`Missing button: ${label}`);button.click();return button;}
async function chooseBoardPage(w){await until(()=>[...w.document.querySelectorAll("button")].some(row=>row.textContent.includes("排轴白板")&&row.closest("#dsh-app-root")),"Business page command missing");const button=[...w.document.querySelectorAll("button")].find(row=>row.textContent.includes("排轴白板")&&row.closest("#dsh-app-root"));button.click();await until(()=>!w.document.getElementById("genshin-legacy-editor").hidden,"Business editor did not become visible");}
async function menu(w,command,group="文件"){const file=[...w.document.querySelectorAll("#workspace-menubar button")].find(row=>row.textContent.trim()===group);assert.ok(file);file.click();await until(()=>w.document.querySelector(`.workspace-menu-popup [data-command="${command}"]`),`Menu command ${command} unavailable`);w.document.querySelector(`.workspace-menu-popup [data-command="${command}"]`).click();}
async function openBoard(w,path){
  await chooseBoardPage(w);await menu(w,"open");await until(()=>w.document.querySelector("dialog[open] input"),"File open dialog missing");
  const dialog=w.document.querySelector("dialog[open]");dialog.querySelector("input").value=path;clickText(w,"打开",dialog);
  await until(()=>w.WB.state.path===path,()=>`Actual WB.load failed: ${w.document.body.textContent.slice(-1800)}`);
}
async function mount(bridge,errors){
  const virtual=new VirtualConsole();virtual.on("jsdomError",error=>errors.push(error));virtual.on("error",(...rows)=>errors.push(new Error(rows.map(row=>row?.stack||String(row)).join(" "))));
  const html=await readFile(join(web,"index.html"),"utf8");
  // Give JSDOM the same non-opaque browser storage semantics as a WebView
  // origin. All assets still come only from the exact production web root.
  const dom=new JSDOM(html,{url:"https://genshin-shell.invalid/index.html",resources:new LocalAssets(),runScripts:"dangerously",pretendToBeVisual:true,virtualConsole:virtual,beforeParse(w){
    Object.assign(w,{TextEncoder,TextDecoder,Request,Response,ResizeObserver:class{observe(){}unobserve(){}disconnect(){}},matchMedia:()=>({matches:false,addEventListener(){},removeEventListener(){}})});
    Object.defineProperty(w.crypto,"subtle",{value:webcrypto.subtle});Object.defineProperty(w.document,"fonts",{value:{addEventListener(){},removeEventListener(){},ready:Promise.resolve()}});
    w.Range.prototype.getBoundingClientRect=()=>({x:0,y:0,left:0,top:0,right:0,bottom:0,width:0,height:0,toJSON(){return this;}});w.Range.prototype.getClientRects=()=>[];w.HTMLElement.prototype.scrollIntoView=()=>{};
    w.HTMLDialogElement.prototype.showModal=function(){this.open=true;};w.HTMLDialogElement.prototype.close=function(){this.open=false;this.dispatchEvent(new w.Event("close"));};
    w.HTMLCanvasElement.prototype.getContext=()=>null;
    w.pywebview={api:new Proxy({}, {get(_target,method){if(typeof method!=="string"||method==="then")return undefined;return async(...args)=>w.JSON.parse(JSON.stringify(await bridge.request(method,...JSON.parse(JSON.stringify(args)))));}})};
  }});
  const w=dom.window;await until(()=>w.GenshinDshClient&&w.callApi,"Production scripts did not load");
  w.dispatchEvent(new w.Event("pywebviewready"));
  await until(()=>w.GenshinEditorBridge,()=>`App initialization failed: ${w.document.body.textContent.slice(-1500)}\n${bridge.stderr}\n${errors.map(row=>row.message).join("\n")}`);
  await w.GenshinDshClient.ready;await until(()=>w.document.querySelector('[contenteditable]'),()=>`Official composer missing: ${w.document.body.textContent.slice(-2200)}`);
  const originalCurrent=w.WB.current;w.__smokeOwnerRejections=[];
  w.WB.current=token=>{const accepted=originalCurrent(token);if(!accepted)w.__smokeOwnerRejections.push({at:Date.now(),sameBoard:token.board===w.WB.state.board,sameJson:token.json===w.WB.json(),sameRequest:token.request===w.WB.state.request,samePath:token.path===w.WB.state.path,sameSha:token.sha===w.WB.state.fileSha});return accepted;};
  return dom;
}

test("CLI WebView smoke driver uses real DSH UI, engine, review and Save As",{timeout:TEST_TIMEOUT_MS},async()=>{
  const frontend=fileURLToPath(new URL("../",import.meta.url)),temp=await mkdtemp(join(frontend,".webview-smoke-dsh-"));let bridge,dom,passed=false,smokeStartedAt;const errors=[];
  try{
    const setup=JSON.parse(execFileSync("python",["-m","tests.dsh_gui_projection_setup",temp],{cwd:root,encoding:"utf8",windowsHide:true}));
    bridge=bridgeProcess(["--root",join(temp,"runtime"),"--db",setup.database,"--script",setup.script,"--input",setup.input]);
    const output=join(temp,"runtime/output");
    bridge.setPickerSelections([setup.input,join(output,"result.whiteboard.json")],join(output,"result.calculated.json"));
    dom=await mount(bridge,errors);const w=dom.window;
    smokeStartedAt=Date.now();assert.equal(w.GenshinWebviewSmoke.start({prompt:"请为当前用户工程白板完善事件与数值并完成原生计算，随后由我审核同步。",input_path:setup.input,board_path:join(output,"result.whiteboard.json"),calculated_path:join(output,"result.calculated.json"),workflow:"calculate",model:"deepseek-flash",effort:"high",timeout_ms:FIXTURE_OBSERVATION_MS,provider_mode:"deterministic_provider_transport",expectation:{require_calculated:true,min_actions:1,min_result_hits:1}}),true);
    await until(()=>w.GenshinWebviewSmoke.snapshot().status!=="running",()=>`Smoke still active ${JSON.stringify(w.GenshinWebviewSmoke.snapshot())}`);
    const receipt=w.GenshinWebviewSmoke.snapshot();assert.equal(receipt.status,"verified",JSON.stringify(receipt));
    assert.equal(receipt.real_webview_dom_verified,true);assert.equal(receipt.files_published,true);assert.equal(receipt.dom.damage_view,true);
    const fresh=await bridge.request("fixture_fresh_read");assert.equal(fresh.board.code,0);assert.equal(fresh.calculated.code,0);assert.ok(fresh.calculated.data.calculated.result.summary.total_damage>0);
    const method=row=>row.method==="document_json_call"?row.args[0]:row.method;
    assert.equal(bridge.calls.filter(row=>method(row)==="dsh_client_save_whiteboard_result").length,1);
    assert.equal(bridge.calls.filter(row=>method(row)==="dsh_client_bind_document").length,1);
    assert.equal(errors.filter(row=>/Uncaught|slot entry crashed/.test(row.message)).length,0,errors.map(row=>row.message).join("\n"));passed=true;
  }catch(error){const outcome=row=>{let result=row.result;if(typeof result==='string'){try{result=JSON.parse(result);}catch{}}return {method:row.method==='document_json_call'?row.args[0]:row.method,startedAfterMs:row.startedAt-smokeStartedAt,finishedAfterMs:row.finishedAt===undefined?null:row.finishedAt-smokeStartedAt,failure:row.failure,code:result?.code,reason:result?.reason,message:result?.message};};
    console.error(`Smoke fixture root: ${temp}\n${bridge?.stderr||""}\n${errors.map(row=>row.message).join("\n")}\n${JSON.stringify(dom?.window.GenshinWebviewSmoke?.snapshot())}\nWB:${JSON.stringify({path:dom?.window.WB?.state.path,fileSha:dom?.window.WB?.state.fileSha,mode:dom?.window.WB?.state.mode,request:dom?.window.WB?.state.request,hasResult:!!dom?.window.WB?.state.result,userFilePickerPending:dom?.window.WB?.state.userFilePickerPending})}\nOwner rejections:${JSON.stringify(dom?.window.__smokeOwnerRejections)}\nDOM:${dom?.window.document.body.textContent.slice(-4500)}\nDocument calls:${JSON.stringify(bridge?.calls.filter(row=>row.method==='document_json_call'||row.method==='fixture_pick_open').map(outcome))}`);throw error;}
  finally{await dom?.window.GenshinDshClient?.dispose();dom?.window.close();await bridge?.close();if(!resolve(temp).startsWith(resolve(frontend)+sep+".webview-smoke-dsh-"))throw new Error("unsafe_test_cleanup_path");if(passed)await rm(temp,{recursive:true,force:true});}
});
