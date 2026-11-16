/** Real installed Chromium geometry, real production index and isolated GuiApi.
 * No live provider requests or user data: deterministic offline provider fixture.
 * This does not certify Windows native dialogs or detached OS windows.
 */
import {spawn,execFileSync} from "node:child_process";
import {createInterface} from "node:readline";
import {createServer} from "node:http";
import {readFile,mkdir,writeFile,mkdtemp} from "node:fs/promises";
import {existsSync} from "node:fs";
import {appendFileSync} from "node:fs";
import {resolve,join,sep,extname} from "node:path";
import {fileURLToPath} from "node:url";
import {createHash,randomUUID} from "node:crypto";
const root=fileURLToPath(new URL("../../",import.meta.url)),web=join(root,"src/genshin_app/gui/web");
const output=join(root,"build/r20-next-implementation-20261116/geometry");await mkdir(output,{recursive:true});
const temp=await mkdtemp(join(output,"run-")),requests=[],errors=[],external=[];
const trace=(event)=>appendFileSync(join(temp,"api-trace.jsonl"),JSON.stringify({at:new Date().toISOString(),...event})+"\n");
const report={contract:"genshin-production-chromium-geometry-v1",transport:"cdp-binding-native-style-rpc",recorded_at:new Date().toISOString(),scope:"real Chromium + production DOM + isolated GuiApi; one fixed local provider reply, no live model calls",cases:[],errors,external,limits:["Windows WebView and native file dialogs remain user acceptance; headless checks do not certify them","Fixture has no real conversation history, candidate approval or result; these states do not certify real model or calculation behavior","Geometry flags are diagnostics; overlapping timeline records and intentional scroll containers are not automatically defects"]};
const startedAt=performance.now();report.timings={};
// Bind observations to actual served bytes, not a source-tree label. A later
// rebuild cannot inherit these screenshots as evidence for its new assets.
const servedAssets=new Map();
let candidateFixture="normal";const candidateWaiters=new Set(),fixtureRequests=[];
const heldPolls=new Map();let candidateConversation;
const releaseHeld=()=>{for(const resolve of heldPolls.values())resolve({released:true});heldPolls.clear();};
const releaseCandidate=()=>{for(const resume of candidateWaiters)resume();candidateWaiters.clear();};
let bridge,chrome,server,cdp,closing=false;
function apiBridge(setup){
  const node=process.env.GENSHIN_NATIVE_NODE||join(root,"agent-runtime/node.exe");
  const child=spawn("python",["-m","tests.dsh_gui_projection_host","--root",join(temp,"runtime"),"--db",setup.database,"--script",setup.script,"--input",setup.input,"--startup",setup.input],{cwd:root,windowsHide:true,env:{...process.env,PYTHONIOENCODING:"utf-8",PYTHONUTF8:"1",GENSHIN_NATIVE_NODE:node},stdio:["pipe","pipe","pipe"]});
  const pending=new Map();let stderr="";child.stderr.on("data",b=>stderr+=b);
  createInterface({input:child.stdout}).on("line",line=>{try{const data=JSON.parse(line),p=pending.get(data.id);if(p){pending.delete(data.id);p.call.durationMs=Date.now()-p.call.at;trace({id:data.id,settled:true,ok:data.ok,code:data.code,durationMs:p.call.durationMs});data.ok?p.resolve(data.value):p.reject(new Error(data.code));}}catch{stderr+=line;}});
  child.on("exit",code=>{for(const p of pending.values())p.reject(new Error(`Host exit ${code}: ${stderr.slice(-2000)}`));pending.clear();});
  return {child,get stderr(){return stderr;},call(method,...args){const call={method,at:Date.now()};requests.push(call);return new Promise((resolve,reject)=>{const id=randomUUID();trace({id,method,...(method==="dsh_client_bundle"?{bundle:args[0]}:{})});pending.set(id,{resolve,reject,call});child.stdin.write(JSON.stringify({id,method,args})+"\n");});},async close(){if(child.exitCode===null){await this.call("fixture_shutdown");child.stdin.end();}}};
}
async function connect(url){
  const ws=new WebSocket(url),pending=new Map(),listeners=new Map();let serial=0;
  await new Promise((resolve,reject)=>{ws.addEventListener("open",resolve,{once:true});ws.addEventListener("error",reject,{once:true});});
  ws.addEventListener("message",event=>{const data=JSON.parse(event.data);if(data.id){const p=pending.get(data.id);if(p){pending.delete(data.id);data.error?p.reject(new Error(JSON.stringify(data.error))):p.resolve(data.result);}}else for(const fn of listeners.get(data.method)||[])void fn(data.params,data.sessionId);});
  ws.addEventListener("close",()=>{for(const item of pending.values())item.reject(new Error("Test browser connection closed"));pending.clear();});
  return {ws,send(method,params={},sessionId){return new Promise((resolve,reject)=>{const id=++serial;const timeout=setTimeout(()=>{pending.delete(id);reject(new Error(`Test browser operation did not settle: ${method}`));},120000);pending.set(id,{resolve:value=>{clearTimeout(timeout);resolve(value);},reject:error=>{clearTimeout(timeout);reject(error);}});ws.send(JSON.stringify({id,method,params,...(sessionId?{sessionId}:{})}));});},on(method,fn){const list=listeners.get(method)||[];list.push(fn);listeners.set(method,list);}};
}
try{
  const setup=JSON.parse(execFileSync("python",["-m","tests.dsh_gui_projection_setup",temp],{cwd:root,encoding:"utf8",windowsHide:true}));
  report.timings.setupMs=performance.now()-startedAt;
  await writeFile(setup.script,JSON.stringify({calls:[],repeatCalls:false}));bridge=apiBridge(setup);
  const invokeApi=async(method,args)=>{
      // Explicit transport-only holds exercise >6 concurrent native-style RPCs.
      // They never change domain data or pretend to be model/engine work.
      if(method==="fixture_hold_poll")return new Promise(resolve=>heldPolls.set(String(args[0]),resolve));
      if(method==="fixture_hold_count")return heldPolls.size;
      if(method==="fixture_release_polls"){releaseHeld();return {released:true};}
      const domainMethod=method==="document_json_call"?args[0]:method;
      if(domainMethod==="dsh_client_candidate")candidateConversation=(method==="document_json_call"?JSON.parse(args[1])[0]:args[0]).conversation_id;
      if(method==="fixture_candidate_read_while_held"){
        if(heldPolls.size!==6||!candidateConversation)throw new Error("Transport starvation regression lacks six holds or a bound candidate");
        const value=JSON.parse(await bridge.call("document_json_call","dsh_client_candidate_read",JSON.stringify([{conversation_id:candidateConversation,resource:"candidate",pointer:"",offset:0,limit:50}])));
        return {heldPolls:heldPolls.size,code:value.code,kind:value.data?.kind,identity:value.data?.resource_identity};
      }
      let value;
      if(["dsh_client_candidate","dsh_client_candidate_read"].includes(domainMethod)&&candidateFixture!=="normal"){
        const mode=candidateFixture;fixtureRequests.push({method:domainMethod,mode});
        if(mode==="loading")await new Promise(resolve=>{candidateWaiters.add(resolve);});
        if(mode==="error"){
          const failure={code:1,reason:"geometry_fixture_candidate_unavailable",message:"几何验收模拟：暂时无法读取候选，请刷新后重试。正式白板保持不变。",operation_may_have_completed:false};
          value=method==="document_json_call"?JSON.stringify(failure):failure;
        }else value=await bridge.call(method,...args);
      }else value=await bridge.call(method,...args);
      return value;
  };
  server=createServer(async(req,res)=>{try{
    const path=new URL(req.url,"http://localhost").pathname;
    if(path==="/__api"&&req.method==="POST"){
      let body="";for await(const chunk of req)body+=chunk;
      const {method,args}=JSON.parse(body),value=await invokeApi(method,args);
      res.setHeader("Content-Type","application/json");res.end(JSON.stringify({value}));return;
    }
    const target=resolve(web,"."+decodeURIComponent(path==="/"?"/index.html":path));
    if(!target.startsWith(resolve(web)+sep))throw new Error("outside assets");
    const bytes=await readFile(target),sha256=createHash("sha256").update(bytes).digest("hex"),previous=servedAssets.get(path);
    if(previous&&previous.sha256!==sha256)throw new Error(`Production asset changed during probe: ${path}`);
    servedAssets.set(path,{path,bytes:bytes.length,sha256});
    res.setHeader("Content-Type",({".js":"text/javascript",".css":"text/css",".html":"text/html",".json":"application/json",".svg":"image/svg+xml"})[extname(target)]||"application/octet-stream");res.end(bytes);
  }catch(error){res.statusCode=500;res.end(JSON.stringify({error:error.message}));}});
  await new Promise(done=>server.listen(0,"127.0.0.1",done));const origin=`http://127.0.0.1:${server.address().port}`;
  const executable=[process.env.GENSHIN_CHROMIUM,"C:/Program Files/Google/Chrome/Application/chrome.exe","C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe"].find(path=>path&&existsSync(path));
  if(!executable)throw new Error("No installed Chromium; no browser download is permitted");report.browser=executable;
  chrome=spawn(executable,["--headless=new","--remote-debugging-port=0",`--user-data-dir=${join(temp,"chromium-profile")}`,"--no-first-run","--no-default-browser-check","--disable-background-networking","--disable-component-update","--disable-sync","--disable-gpu","about:blank"],{windowsHide:true,stdio:["ignore","ignore","pipe"]});
  const wsUrl=await new Promise((resolve,reject)=>{let text="";chrome.stderr.on("data",b=>{text+=b;const match=text.match(/DevTools listening on (ws:\/\/[^\s]+)/);if(match)resolve(match[1]);});chrome.once("exit",code=>reject(new Error(`Chromium exit ${code}: ${text.slice(-1200)}`)));setTimeout(()=>reject(new Error(`Chromium CDP unavailable: ${text.slice(-1200)}`)),20000).unref();});
  cdp=await connect(wsUrl);const target=await cdp.send("Target.createTarget",{url:"about:blank"}),{sessionId}=await cdp.send("Target.attachToTarget",{targetId:target.targetId,flatten:true});
  const send=(method,params)=>cdp.send(method,params,sessionId);
  cdp.on("Runtime.exceptionThrown",data=>errors.push(data.exceptionDetails?.exception?.description||data.exceptionDetails?.text));
  cdp.on("Fetch.requestPaused",async(data,id)=>{if(id!==sessionId)return;const url=data.request.url;if(url.startsWith(origin+"/")||url.startsWith("data:")||url.startsWith("blob:"))await send("Fetch.continueRequest",{requestId:data.requestId});else{external.push(url);await send("Fetch.failRequest",{requestId:data.requestId,errorReason:"BlockedByClient"});}});
  await send("Runtime.enable");await send("Page.enable");await send("Fetch.enable",{patterns:[{urlPattern:"*"}]});
  await send("Runtime.addBinding",{name:"__genshinProjectionInvoke"});
  cdp.on("Runtime.bindingCalled",async(data,id)=>{if(id!==sessionId||data.name!=="__genshinProjectionInvoke")return;const call=JSON.parse(data.payload);let reply;try{reply={id:call.id,value:await invokeApi(call.method,call.args)};}catch(error){reply={id:call.id,error:String(error.message||error)};}if(closing)return;try{await send("Runtime.evaluate",{expression:`window.__genshinProjectionSettle(${JSON.stringify(reply)})`});}catch(error){if(!closing)errors.push(String(error.message||error));}});
  await send("Page.addScriptToEvaluateOnNewDocument",{source:`(()=>{let next=0;const pending=new Map();window.__genshinProjectionSettle=data=>{const item=pending.get(data.id);if(!item)return;pending.delete(data.id);data.error?item.reject(new Error(data.error)):item.resolve(data.value);};window.pywebview={api:new Proxy({}, {get(_t,method){if(typeof method!=="string"||method==="then")return undefined;return (...args)=>new Promise((resolve,reject)=>{const id=++next;pending.set(id,{resolve,reject});window.__genshinProjectionInvoke(JSON.stringify({id,method,args}));});}})};})();`});
  const evaluate=async expression=>{const value=await send("Runtime.evaluate",{expression,awaitPromise:true,returnByValue:true});if(value.exceptionDetails)throw new Error(value.exceptionDetails.exception?.description||value.exceptionDetails.text);return value.result?.value;};
  const until=async(expression,message)=>{const end=Date.now()+90000;while(Date.now()<end){if(await evaluate(expression))return;await new Promise(r=>setTimeout(r,100));}throw new Error(message+": "+await evaluate("document.body.innerText.slice(-1800)"));};
  const navigationStarted=performance.now();
  await send("Page.navigate",{url:origin+"/index.html"});await until("!!(window.GenshinDshClient&&window.callApi)","Production scripts missing");
  report.timings.scriptsAfterNavigationMs=performance.now()-navigationStarted;
  // The real API is already available before app.js in this load order. Do
  // not inject another ready event to hide a missed-readiness startup race.
  await until("!!window.GenshinEditorBridge","GuiApi editor startup failed");
  report.timings.editorBridgeAfterNavigationMs=performance.now()-navigationStarted;
  await evaluate("window.GenshinDshClient.ready.then(()=>true)");
  report.timings.dshReadyAfterNavigationMs=performance.now()-navigationStarted;
  await until("!!window.WB?.state.board","Fixture startup board missing");
  // DSH's first-run notice is real and modal. Acknowledge it through its UI;
  // never measure pages behind a scrim or bypass it with storage mutations.
  await until("[...document.querySelectorAll('button')].some(e=>e.textContent.trim()==='继续')||!document.body.innerText.includes('预览版说明')","First-run notice missing controls");
  await evaluate("(()=>{if(document.body.innerText.includes('预览版说明')){const b=[...document.querySelectorAll('button')].find(e=>e.textContent.trim()==='继续');if(!b)throw new Error('First-run confirmation unreachable');b.click();}return true;})()");
  await until("!document.body.innerText.includes('预览版说明')","First-run notice did not close");
  const measurement=`(()=>{
    const visible=e=>{if(!e.getClientRects().length||getComputedStyle(e).visibility==='hidden'||e.closest('[hidden],[inert],[aria-hidden=true]'))return false;for(let p=e.parentElement;p;p=p.parentElement){if(p.tagName==='DETAILS'&&!p.open&&!p.querySelector(':scope > summary')?.contains(e))return false;}return true;};
    const box=e=>{const r=e.getBoundingClientRect();return {x:r.x,y:r.y,width:r.width,height:r.height};};
    const scrollParent=e=>{for(let p=e.parentElement;p;p=p.parentElement){const s=getComputedStyle(p);if((/auto|scroll/.test(s.overflowY)&&p.scrollHeight>p.clientHeight)||(/auto|scroll/.test(s.overflowX)&&p.scrollWidth>p.clientWidth))return {tag:p.tagName,...box(p)};}return null;};
    const modal=[...document.querySelectorAll('[role=dialog],dialog[open]')].filter(visible).at(-1),scope=modal||document;
    const controls=[...scope.querySelectorAll('button,input,select,textarea,[contenteditable=true]')].filter(visible).map(e=>({label:(e.getAttribute('aria-label')||e.textContent||e.placeholder||e.id||e.tagName).trim().slice(0,100),...box(e),scrollParent:scrollParent(e)}));
    const offscreen=controls.filter(r=>r.x<-2||r.y<-2||r.x+r.width>innerWidth+2||r.y+r.height>innerHeight+2);
    const modalEvidence=[...document.querySelectorAll('[role=dialog],dialog[open]')].map(e=>({tag:e.tagName,open:e.open,visible:visible(e),hiddenAncestor:e.closest('[hidden]')?.id||null,...box(e)}));
    const fonts=['body','#dsh-app-root','.gs-candidate-workspace','.wb-layout','.wb-team-card','dialog[open]'].flatMap(selector=>{const e=document.querySelector(selector);if(!e||!visible(e))return [];const s=getComputedStyle(e);return [{selector,fontFamily:s.fontFamily,fontSize:s.fontSize,lineHeight:s.lineHeight}];});
    return {viewport:{width:innerWidth,height:innerHeight},body:{width:document.documentElement.scrollWidth,height:document.documentElement.scrollHeight},modal:!!modal,modalEvidence,fonts,controls,offscreen,unreachable:offscreen.filter(r=>!r.scrollParent),timeline:document.querySelector('#timeline-wrap')&&visible(document.querySelector('#timeline-wrap'))?{...box(document.querySelector('#timeline-wrap')),background:getComputedStyle(document.querySelector('#timeline-wrap')).backgroundColor}:null,text:document.body.innerText.slice(-900)};
  })()`;
  async function record(name,width,theme){await new Promise(r=>setTimeout(r,180));const value=await evaluate(measurement);const image=await send("Page.captureScreenshot",{format:"png",captureBeyondViewport:false});const file=`${width}-${theme}-${name}.png`,bytes=Buffer.from(image.data,"base64");await writeFile(join(temp,file),bytes);await writeFile(join(output,file),bytes);report.cases.push({name,width,theme,...value,screenshot:file});if(name==="error-modal-fixture"&&!value.modal)throw new Error(`Shared modal exists but is not visible: ${JSON.stringify(value.modalEvidence)}`);}
  for(const width of [1360,780])for(const theme of ["light","dark"]){
    await send("Emulation.setDeviceMetricsOverride",{width,height:900,deviceScaleFactor:1,mobile:false});
    await evaluate(`document.dispatchEvent(new CustomEvent('genshin-theme-request',{detail:{preference:${JSON.stringify(theme)}}}));true`);
    for(const page of ["whiteboard","rotation","result","data"]){await evaluate(`window.GenshinEditorBridge.show(${JSON.stringify(page)}).then(()=>true)`);await record(page,width,theme);
      if(page==="whiteboard")await evaluate("(()=>{const layout=document.querySelector('.wb-layout');if(!layout?.classList.contains('workspace-right-hidden'))throw new Error('Unselected inspector consumes timeline space');if([...document.querySelectorAll('.wb-capacity-intents')].some(e=>e.tagName!=='DETAILS'||e.open))throw new Error('Advanced intent empty states are not collapsed');return true;})()");}
    await evaluate("document.dispatchEvent(new Event('genshin-open-assistant'));true");await record("chat-empty",width,theme);
    await evaluate("(()=>{const b=[...document.querySelectorAll('#dsh-app-root button')].find(e=>/候选工作区/.test(e.textContent+' '+e.getAttribute('aria-label')));if(!b)throw new Error('Candidate entry missing');b.click();return true;})()");await record("candidate-empty",width,theme);
    await evaluate("(()=>{const b=[...document.querySelectorAll('#dsh-app-root button')].find(e=>/任务中心/.test(e.textContent+' '+e.getAttribute('aria-label')));if(!b)throw new Error('Task entry missing');b.click();return true;})()");await record("tasks-empty",width,theme);
    await evaluate("(()=>{const b=[...document.querySelectorAll('#dsh-app-root button')].find(e=>/设置|Settings/.test(e.textContent+' '+e.getAttribute('aria-label')));if(!b)throw new Error('Settings entry missing');b.click();return true;})()");await record("settings",width,theme);
    await evaluate("(()=>{const official=[...document.querySelectorAll('[role=dialog][aria-modal=true]')].find(e=>e.getClientRects().length);if(!official)throw new Error('Official settings modal missing');const competing=window.AppDialogKernel.create({title:'不应盖住设置'});if(competing||window.AppDialogKernel.getLastCreateResult().reason!=='external_modal_active'||!official.isConnected)throw new Error('Official/legacy modal ownership conflict');return true;})()");
    await send("Input.dispatchKeyEvent",{type:"keyDown",key:"Escape",code:"Escape",windowsVirtualKeyCode:27});await send("Input.dispatchKeyEvent",{type:"keyUp",key:"Escape",code:"Escape",windowsVirtualKeyCode:27});
  }
  // These are explicitly test transport states. The candidate itself comes
  // from actual GuiApi binding, never a fabricated accepted calculation.
  await evaluate("window.GenshinEditorBridge.show('whiteboard').then(()=>true)");
  await evaluate("document.dispatchEvent(new Event('genshin-open-assistant'));true");
  const workspaceChoice=await evaluate("(()=>{const button=[...document.querySelectorAll('button[aria-label]')].find(e=>/^(Choose workspace|选择工作区)$/.test(e.getAttribute('aria-label')));if(!button)return false;button.click();return true;})()");
  if(workspaceChoice){
    await until("[...document.querySelectorAll('[role=menuitem],[role=option]')].some(e=>e.textContent.includes('原神排轴'))","Fixture workspace is not offered by official selector");
    await evaluate("[...document.querySelectorAll('[role=menuitem],[role=option]')].find(e=>e.textContent.includes('原神排轴')).click();true");
  }
  await until("!!document.querySelector('[data-conversation-content]')?.dataset.conversationSession","No selected session for candidate fixture");
  const before=await evaluate("window.WB.json()");
  // A durable official session is needed to retain the candidate's session
  // identity after leaving the unstarted hero. The provider is a local fixed
  // reply with an empty tool script: no network, calculation or tool writes.
  await evaluate("document.querySelector('[contenteditable]').focus();true");
  await send("Input.insertText",{text:"离线页面验收：仅输出固定测试回复，不修改白板。"});
  await send("Input.dispatchKeyEvent",{type:"keyDown",key:"Enter",code:"Enter",windowsVirtualKeyCode:13});
  await send("Input.dispatchKeyEvent",{type:"keyUp",key:"Enter",code:"Enter",windowsVirtualKeyCode:13});
  await until("document.body.innerText.includes('phase5b official answer')","Fixed offline provider reply did not complete");
  await evaluate("window.__heldProjectionPolls=Array.from({length:6},(_,index)=>window.pywebview.api.fixture_hold_poll(index));true");
  await until("window.pywebview.api.fixture_hold_count().then(count=>count===6)","Six transport holds did not start");
  await evaluate("[...document.querySelectorAll('#dsh-app-root button')].find(e=>/候选工作区/.test(e.textContent+' '+e.getAttribute('aria-label'))).click();true");
  const refresh="(()=>{const section=document.querySelector('section[aria-label=\"Agent 候选工作区\"]');const button=[...section.querySelectorAll('button')].find(e=>e.textContent.trim()==='刷新');if(!button||button.disabled)throw new Error('Candidate refresh unavailable');button.click();return true;})()";
  await until("document.querySelector('.gs-candidate-state')?.textContent.includes('候选版本')","Actual uncalculated candidate not loaded");
  report.transportRegression=await evaluate("window.pywebview.api.fixture_candidate_read_while_held()");
  if(report.transportRegression.heldPolls!==6||report.transportRegression.code!==0||report.transportRegression.kind!=="object"||!/^[0-9a-f]{64}$/.test(report.transportRegression.identity))throw new Error("Candidate read was starved by concurrent transport polls");
  await evaluate("window.pywebview.api.fixture_release_polls().then(()=>Promise.all(window.__heldProjectionPolls))");
  for(const width of [1360,780])for(const theme of ["light","dark"]){
    await send("Emulation.setDeviceMetricsOverride",{width,height:900,deviceScaleFactor:1,mobile:false});
    await evaluate(`document.dispatchEvent(new CustomEvent('genshin-theme-request',{detail:{preference:${JSON.stringify(theme)}}}));true`);
    await record("candidate-loaded-fixture",width,theme);
    candidateFixture="loading";await evaluate(refresh);
    await until("[...document.querySelectorAll('.gs-candidate-workspace [role=status]')].some(e=>e.textContent.includes('正在读取'))","Candidate loading indication missing");
    await record("candidate-loading-fixture",width,theme);
    candidateFixture="normal";releaseCandidate();releaseHeld();
    await until("![...document.querySelectorAll('.gs-candidate-workspace [role=status]')].some(e=>e.textContent.includes('正在读取'))","Candidate loading did not settle");
    candidateFixture="error";await evaluate(refresh);
    await until("document.querySelector('.gs-candidate-workspace [role=alert]')?.textContent.includes('几何验收模拟')","Recoverable candidate error missing");
    await record("candidate-error-fixture",width,theme);
    candidateFixture="normal";await evaluate(refresh);
    await until("!document.querySelector('.gs-candidate-workspace [role=alert]')","Candidate did not recover after refresh");
    await until("[...document.querySelectorAll('.gs-candidate-workspace button')].some(e=>e.textContent.trim()==='刷新'&&!e.disabled)&&![...document.querySelectorAll('.gs-candidate-workspace [role=status]')].some(e=>e.textContent.includes('正在读取'))","Candidate recovery did not finish loading");
    // Render the shared error surface through its existing UI facade. This
    // measures a fixture modal, not a claimed Windows save-dialog failure.
    await evaluate("(()=>{const opener=[...document.querySelectorAll('.gs-candidate-workspace button')].find(e=>e.textContent.trim()==='刷新');opener.focus();if(opener.disabled||document.activeElement!==opener)throw new Error('Modal fixture opener is not an interactive focused control');const dialog=window.AppDialogKernel.create({title:'操作未完成',description:'几何验收模拟：文件暂时无法打开。当前方案已保留。'});if(!dialog)throw new Error('Shared modal unavailable');dialog.appBody.append(window.UI.error({code:1,message:'请关闭此提示后继续查看当前方案。'}));dialog.setActions(window.UI.button('知道了',()=>dialog.finish('acknowledged')));if(!document.getElementById('genshin-application-root').inert||!dialog.contains(document.activeElement))throw new Error('Modal failed to isolate application and own focus');return true;})()");
    await until("!!document.querySelector('[role=dialog],dialog[open]')","Shared error modal missing");
    await record("error-modal-fixture",width,theme);
    await send("Input.dispatchKeyEvent",{type:"keyDown",key:"Escape",code:"Escape",windowsVirtualKeyCode:27});await send("Input.dispatchKeyEvent",{type:"keyUp",key:"Escape",code:"Escape",windowsVirtualKeyCode:27});
    await until("!document.querySelector('[role=dialog],dialog[open]')","Fixture error modal did not close");
    const closeEvidence=await evaluate("(()=>{const active=document.activeElement;return {inert:document.getElementById('genshin-application-root').inert,activeTag:active?.tagName,activeLabel:active?.textContent.trim(),activeAria:active?.getAttribute('aria-label'),activeClass:active?.className,modalCount:document.querySelectorAll('[role=dialog],dialog[open]').length};})()");
    report.cases.at(-1).closeEvidence=closeEvidence;
    if(closeEvidence.inert||closeEvidence.activeLabel!=='刷新')throw new Error('Modal did not restore application interactivity and focus: '+JSON.stringify(closeEvidence));
  }
  // Equivalent 200% browser reflow at a 1360x900 physical viewport. DPR alone
  // does not change CSS geometry; reduce the layout viewport as well. This is
  // not a claim that Windows display scaling or Ctrl+Plus was operated.
  report.shortViewportRegression=[];
  for(const theme of ["light","dark"]){
    await send("Emulation.setDeviceMetricsOverride",{width:680,height:450,deviceScaleFactor:2,mobile:false});
    await evaluate(`document.dispatchEvent(new CustomEvent('genshin-theme-request',{detail:{preference:${JSON.stringify(theme)}}}));window.GenshinEditorBridge.show('whiteboard').then(()=>{window.WB.setMode('actions');return true;})`);
    await new Promise(resolve=>setTimeout(resolve,600));
    const initial=await evaluate(`(()=>{const panel=document.querySelector('#tab-whiteboard');panel.scrollTop=0;const box=e=>{const r=e.getBoundingClientRect();return {x:r.x,y:r.y,width:r.width,height:r.height};};const fields=[...panel.querySelectorAll('.wb-header .eq-field')].map(e=>({field:box(e),children:[...e.children].map(c=>({tag:c.tagName,...box(c)}))}));for(let i=0;i<fields.length;i++)for(const a of fields[i].children)for(const other of fields.slice(i+1))for(const b of other.children){if(Math.min(a.x+a.width,b.x+b.width)>Math.max(a.x,b.x)+.5&&Math.min(a.y+a.height,b.y+b.height)>Math.max(a.y,b.y)+.5)throw new Error('Whiteboard metadata fields overlap');}if(panel.scrollWidth>panel.clientWidth+1)throw new Error('Whiteboard metadata creates horizontal overflow');return {fields,panel:box(panel),overflowY:getComputedStyle(panel).overflowY,scrollHeight:panel.scrollHeight,clientHeight:panel.clientHeight,scrollRange:panel.scrollHeight-panel.clientHeight};})()`);
    if(!/auto|scroll/.test(initial.overflowY)||initial.scrollRange<=0)throw new Error("Short whiteboard does not expose vertical scrolling");
    await send("Input.dispatchMouseEvent",{type:"mouseWheel",x:initial.panel.x+20,y:initial.panel.y+12,deltaX:0,deltaY:500});
    await new Promise(resolve=>setTimeout(resolve,350));
    const after=await evaluate(`(()=>{const panel=document.querySelector('#tab-whiteboard'),label=panel.querySelector('.tl-lane-label');if(!label)throw new Error('Whiteboard role label missing');const r=label.getBoundingClientRect(),p=panel.getBoundingClientRect(),hit=document.elementFromPoint(r.x+r.width/2,r.y+r.height/2);return {scrollTop:panel.scrollTop,label:{x:r.x,y:r.y,width:r.width,height:r.height},fullyVisible:r.top>=p.top&&r.bottom<=p.bottom&&r.left>=p.left&&r.right<=p.right,hit:hit===label||label.contains(hit),hitTag:hit?.tagName};})()`);
    if(after.scrollTop<=0||!after.hit||!after.fullyVisible)throw new Error("Mouse wheel cannot reveal the whiteboard role label above the footer");
    report.shortViewportRegression.push({theme,initial,after,cssViewport:{width:680,height:450},deviceScaleFactor:2});
    await record("whiteboard-short-viewport",680,theme);
  }
  if(await evaluate("window.WB.json()")!==before)throw new Error("Read-only geometry scenarios changed the formal whiteboard");
  // Record only states actually exercised; no synthetic game events or
  // result acceptance are injected to manufacture broader coverage.
  report.stateCoverage={empty:["native editor","report","chat","candidate","tasks"],loaded:["database catalog","whiteboard fixture","settings","uncalculated candidate from actual GuiApi"],loading:"explicit held candidate-read transport fixture; previous content retained",error:"explicit failed candidate-read transport + recovery; shared error modal fixture",review:"not covered: fixture has no calculated candidate approval",settings:"entry geometry only; credentials and provider network untouched",formalWhiteboardUnchanged:true,fixtureRequests};
  report.status=errors.length?"observed_errors":"completed";
}catch(error){report.status="failed";report.failure=error.stack;process.exitCode=1;}
finally{
  candidateFixture="normal";releaseCandidate();releaseHeld();
  report.assets=[...servedAssets.values()].sort((a,b)=>a.path.localeCompare(b.path));
  report.apiCalls=requests.reduce((m,row)=>(m[row.method]=(m[row.method]||0)+1,m),{});report.hostStderr=bridge?.stderr?.slice(-3000)||"";
  report.apiDurations=requests.reduce((m,row)=>{if(row.durationMs===undefined)return m;const entry=m[row.method]??={count:0,totalMs:0,maxMs:0};entry.count++;entry.totalMs+=row.durationMs;entry.maxMs=Math.max(entry.maxMs,row.durationMs);return m;},{});
  report.timings.totalMs=performance.now()-startedAt;
  const serialized=JSON.stringify(report,null,2);
  await writeFile(join(temp,"report.json"),serialized);
  await writeFile(join(output,"report.json"),serialized);
  closing=true;try{await cdp?.send("Browser.close");}catch{}cdp?.ws.close();if(chrome?.exitCode===null)chrome.kill();
  try{await bridge?.close();}catch{}server?.closeAllConnections();server?.close();
  console.log(JSON.stringify({status:report.status,cases:report.cases.length,errors:report.errors,report:join(output,"report.json"),failure:report.failure}));
}
