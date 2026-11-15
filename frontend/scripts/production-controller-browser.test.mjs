/** Actual production IIFE + official SessionController. Offline provider only.
 * This is a browser projection, never a desktop/WebView window.
 */
import assert from "node:assert/strict";
import {test} from "node:test";
import {readFile,mkdtemp,mkdir,rm} from "node:fs/promises";
import {resolve,join,sep} from "node:path";
import {fileURLToPath,pathToFileURL} from "node:url";
import {createRequire} from "node:module";
import {createHash,randomUUID,webcrypto} from "node:crypto";
import {JSDOM,VirtualConsole} from "jsdom";
import {createNativeDshSessionHost} from "../../agent-runtime/src/native-dsh-session-host.mjs";
import {createPhase5bOfficialFakeFetch} from "../../agent-runtime/src/phase5b-official-fake-fetch.mjs";
import {assertPinnedNodeVersion} from "../../agent-runtime/src/runtime-version.mjs";
const runtimeRequire=createRequire(new URL("../../agent-runtime/package.json",import.meta.url));
const {defineTool}=await import(pathToFileURL(runtimeRequire.resolve("@deepseek-ai/dsh-tools")).href);
const {TypertGatewayService}=await import(pathToFileURL(runtimeRequire.resolve("@deepseek-ai/dsh-api-gateway")).href);

async function until(predicate,message){for(let i=0;i<1000;i++){if(predicate())return;await new Promise(resolve=>setTimeout(resolve,10));}throw new Error(typeof message==="function"?message():message);}
function uplinkQueue(){
  const items=[];let waiter,ended=false;
  return {offer(value){return new Promise(resolve=>{if(ended){resolve();return;}if(waiter){const receive=waiter;waiter=undefined;receive({value,done:false});resolve();}else items.push({value,resolve});});},
    end(){ended=true;waiter?.({done:true});waiter=undefined;for(const item of items.splice(0))item.resolve();},
    [Symbol.asyncIterator](){return this;},async next(){const item=items.shift();if(item){item.resolve();return {value:item.value,done:false};}if(ended)return {done:true};return new Promise(resolve=>{waiter=resolve;});},async return(){this.end();return {done:true};}};
}
test("actual browser submits through the real SessionController and shares identity across full/embedded hosts",{timeout:60000},async()=>{
  assertPinnedNodeVersion();
  const frontend=fileURLToPath(new URL("../",import.meta.url)),temp=await mkdtemp(join(frontend,".controller-browser-"));
  const originalFetch=globalThis.fetch,fake=createPhase5bOfficialFakeFetch({requireV2Context:false});let providerRequests=0;
  globalThis.fetch=(url,init={})=>{
    if(String(url).endsWith("/v1/messages")){
      providerRequests++;
      if(init.body?.includes('"tool_use_id":"toolu-question-batch"')&&!init.body.includes('"tool_use_id":"browser-approval-call"')){
        const events=[{type:"message_start",message:{usage:{input_tokens:20,output_tokens:0}}},{type:"content_block_start",index:0,content_block:{type:"tool_use",id:"browser-approval-call",name:"fixture_browser_approval",input:{}}},{type:"content_block_stop",index:0},{type:"message_delta",delta:{stop_reason:"tool_use"},usage:{output_tokens:1}},{type:"message_stop"}];
        return Promise.resolve(new Response(events.map(event=>`event: ${event.type}\ndata: ${JSON.stringify(event)}\n\n`).join(""),{headers:{"content-type":"text/event-stream"}}));
      }
    }
    return fake(url,init);
  };
  let host,dom;
  const streams=new Map(),requests=new Map(),resources=new Map(),binds=[],calls=[],errors=[];
  const registerRemoteEvents=TypertGatewayService.prototype.registerRemoteEvents;
  TypertGatewayService.prototype.registerRemoteEvents=function(source,hostInfo){return registerRemoteEvents.call(this,signal=>{signal.addEventListener("abort",()=>errors.push(new Error(`Forwarded event source ended: ${signal.reason?.stack||String(signal.reason)}`)),{once:true});return source(signal);},hostInfo);};
  try{
    const mirrorRoot=join(temp,"media");await mkdir(mirrorRoot);
    const contract=JSON.parse(await readFile(new URL("../dsh-client-composition.json",import.meta.url),"utf8"));
    const acceptedManifest=JSON.parse(await readFile(new URL("../dsh-client-accepted-manifest.json",import.meta.url),"utf8"));
    host=await createNativeDshSessionHost({profileRoot:temp,mirrorRoot,clientAssets:{contract,acceptedManifest,localModuleBaseUrl:new URL("../",import.meta.url).href}});
    const approvalOutcomes=[],approvalFacts=[];
    host.context.tools.register(defineTool({name:"fixture_browser_approval",description:"Offline browser projection: request permission without side effects",parameters:{},execute:async(_args,exec)=>{approvalFacts.push({live:host.context.typertGateway.hasLiveClient(),streams:[...streams.values()].map(row=>({endpoint:row.endpoint,aborted:row.control.signal.aborted,reads:row.reads,...(row.endpoint==="$events"?{frames:row.frames,sends:row.sends,cancel:row.cancel}: {})}))});const result=await host.context.approval.request({agent:exec.agent,toolName:"fixture_browser_approval",callId:exec.callId,reason:"一次离线界面权限测试，不会修改任何文件。",signal:exec.signal});approvalOutcomes.push(result);return result;},output:{schema:{type:"string"},render:(_args,result)=>[{type:"text",text:result}]}}));
    await host.kernel.secretController.set({provider:"deepseek-official",credential_generation:1,secret:"offline-browser-only-sentinel"});
    const mountBrowser=async()=>{
    const peer=host.createPeer();
    const console=new VirtualConsole();console.on("jsdomError",error=>errors.push(error));for(const level of ["error","warn"])console.on(level,(...values)=>errors.push(new Error(values.map(value=>value?.stack||String(value)).join(" "))));
    dom=new JSDOM('<!doctype html><html><head></head><body><div id="genshin-editor-parking"></div><div id="dsh-app-root"></div></body></html>',{runScripts:"dangerously",url:"https://dsh-shell.invalid/",pretendToBeVisual:true,virtualConsole:console});
    const w=dom.window;
    Object.assign(w,{TextEncoder,TextDecoder,Request,Response,ResizeObserver:class{observe(){}unobserve(){}disconnect(){}},matchMedia:()=>({matches:false,addEventListener(){},removeEventListener(){}})});
    // JSDOM has no layout. Browser Range geometry is required by the official
    // contenteditable selection/composer implementation, not App state.
    w.Range.prototype.getBoundingClientRect=()=>({x:0,y:0,left:0,top:0,right:0,bottom:0,width:0,height:0,toJSON(){return this;}});
    w.Range.prototype.getClientRects=()=>[];
    w.HTMLElement.prototype.scrollIntoView=()=>{};
    Object.defineProperty(w.crypto,"subtle",{value:webcrypto.subtle});Object.defineProperty(w.document,"fonts",{value:{addEventListener(){},removeEventListener(){},ready:Promise.resolve()}});
    const editor=w.document.createElement("div");editor.textContent="formal editor";editor.hidden=true;w.document.getElementById("genshin-editor-parking").append(editor);
    w.GenshinEditorBridge={root:editor,navigate(){},document:()=>({document_kind:null,document:null}),setTheme(){}};
    w.pywebview={api:{
      async dsh_client_boot(){return {injections:host.assets.boot()};},
      async dsh_client_rpc(request){
        const control=new AbortController();requests.set(request.request_id,control);calls.push(request);
        try{const response=await host.handler.fetch(new Request(`https://genshin-shell.invalid${request.channel}/${request.endpoint}`,{method:"POST",headers:{"content-type":"application/json"},body:JSON.stringify({type:"client-request",rpcId:request.request_id,method:request.endpoint,payload:request.payload}),signal:control.signal}),peer);const value=await response.json();request.result=value.result;assert.equal(value.rpcId,request.request_id);if(!value.result.ok)errors.push(new Error(`${request.endpoint}: ${JSON.stringify(value.result.error)}`));return value.result;}
        finally{requests.delete(request.request_id);}
      },
      async dsh_client_request_cancel({request_id}){requests.get(request_id)?.abort();return {cancelled:true};},
      async dsh_client_stream_open(request){
        const control=new AbortController(),uplink=uplinkQueue();if(!request.has_uplink)uplink.end();
        let source;try{source=await host.wireStream.open(request.endpoint,JSON.parse(JSON.stringify(request.payload)),uplink,peer,control.signal);}catch(error){errors.push(new Error(`Stream ${request.endpoint}: ${error.stack||error}`));throw error;}
        streams.set(request.stream_id,{control,uplink,iterator:source[Symbol.asyncIterator](),cursor:0,last:null,endpoint:request.endpoint,reads:0,frames:[],sends:[]});return {stream_id:request.stream_id,cursor:0};
      },
      async dsh_client_stream_read({stream_id,cursor}){const stream=streams.get(stream_id);if(stream.last&&cursor===stream.cursor-1)return stream.last;
        assert.equal(cursor,stream.cursor);stream.reads++;const item=await stream.iterator.next();if(stream.endpoint==="$events")stream.frames.push(item);stream.cursor++;stream.last={items:item.done?[]:[item.value],cursor:stream.cursor,done:!!item.done};return stream.last;},
      async dsh_client_stream_cancel(request){const stream=streams.get(request.stream_id);if(stream)stream.cancel=request;stream?.control.abort();stream?.uplink.end();await stream?.iterator.return?.();return {cancelled:true};},
      async dsh_client_stream_send({stream_id,sequence,items,done}){const stream=streams.get(stream_id);stream.sends.push({sequence,items,done});for(const item of items)await stream.uplink.offer(JSON.parse(JSON.stringify(item)));if(done)stream.uplink.end();return {sequence};},
      async dsh_client_bundle({url}){const response=await host.assets.bundle(url);const bytes=Buffer.from(await response.arrayBuffer()),resource_id=randomUUID();resources.set(resource_id,bytes);return {status:response.status,contentType:response.headers.get("content-type"),resource:{resource_id,byte_length:bytes.length,sha256:createHash("sha256").update(bytes).digest("hex")}};},
      async dsh_client_resource_read({resource_id,offset,length}){const bytes=resources.get(resource_id),end=Math.min(bytes.length,offset+length);return {resource_id,offset,body_base64:bytes.subarray(offset,end).toString("base64"),eof:end===bytes.length};},
      async dsh_client_resource_release({resource_id}){resources.delete(resource_id);return {released:true};},
      async dsh_client_bind_document(request){binds.push(request);return {binding:{dsh_session_id:request.dsh_session_id,conversation_id:"app-browser-conversation"},existing:binds.length>1};},
      async dsh_client_domain_binding({dsh_session_id}){return {binding:binds.some(row=>row.dsh_session_id===dsh_session_id)?{dsh_session_id,conversation_id:"app-browser-conversation",projection:{contract:"native-harness-projection-v3",projection_revision:1,available_commands:[]}}:null};},
      async dsh_client_candidate(){return {code:0,data:{kind:"whiteboard",revision:0,receipt_fingerprint:null,summary:{characters:0,actions:0}}};},
      async dsh_client_candidate_read({resource,pointer,offset}){assert.equal(pointer,"");assert.equal(offset,0);return {code:0,data:{resource,pointer,offset,kind:"object",total:4,items:[{key:"board_id",value:"fixture-domain-board"},{key:"title",value:"可查看的未封口候选"},{key:"team",value:[]},{key:"actions",value:[]}],next_offset:null}};},
    }};
    // A real pywebview/Named Pipe boundary serializes in both directions.
    // Without this, strict plain-object checks would see the other realm's
    // Object.prototype and report a fixture-only protocol violation.
    const implementations=w.pywebview.api;
    w.pywebview.api=Object.fromEntries(Object.entries(implementations).map(([name,fn])=>[name,async(...args)=>w.JSON.parse(JSON.stringify(await fn(...JSON.parse(JSON.stringify(args)))))]));
    const script=w.document.createElement("script");script.textContent=await readFile(new URL("../dist/production/dsh-production.js",import.meta.url),"utf8");w.document.head.append(script);
    await w.GenshinDshClient.ready;
    return w;
    };
    let w=await mountBrowser();
    await until(()=>w.document.querySelector('[contenteditable]'),()=>`Composer not available: ${w.document.body.textContent.slice(-2200)}\nCalls: ${calls.map(row=>row.endpoint).join(",")}\n${errors.map(error=>error.message).join("\n")}`);
    // Exercise the public workspace picker. A registered App container is not
    // a selected Session; never create one by calling the Host behind the UI.
    const workspaceChip=[...w.document.querySelectorAll('button[aria-label]')].find(button=>/^(Choose workspace|选择工作区)$/.test(button.getAttribute("aria-label")));
    if(workspaceChip){
      workspaceChip.click();
      await until(()=>[...w.document.querySelectorAll('[role="menuitem"], [role="option"]')].some(item=>item.textContent.includes("原神排轴")),()=>`Workspace picker missing: ${w.document.body.innerHTML.slice(-7000)}`);
      [...w.document.querySelectorAll('[role="menuitem"], [role="option"]')].find(item=>item.textContent.includes("原神排轴")).click();
      await until(()=>!/(Choose a workspace to start|选择工作区以开始)/.test(w.document.body.textContent),()=>`Workspace selection not applied: ${w.document.body.textContent.slice(-2500)}`);
    }
    await until(()=>{
      const created=calls.filter(row=>row.endpoint==="session/create").at(-1)?.result?.value?.sessionId;
      return created&&w.document.querySelector('[data-conversation-content]')?.dataset.conversationSession===created&&w.document.querySelector('[contenteditable]');
    },()=>`Workspace composer did not bind the public created Session: ${w.document.body.innerHTML.slice(-3000)}`);
    const beforeRemovalCreates=calls.filter(row=>row.endpoint==="session/create").length;
    const remove=w.document.querySelector('button[aria-label="移除未开始入口"]');assert.ok(remove,"The selected provisional entry must be labelled unstarted and removable");remove.click();
    await until(()=>!w.document.querySelector('[data-row-key^="session:"]'),"Unstarted entry was not removed from the list");
    await new Promise(resolve=>setTimeout(resolve,100));assert.equal(calls.filter(row=>row.endpoint==="session/create").length,beforeRemovalCreates,"Removing an entry must not silently create a replacement");
    const create=[...w.document.querySelectorAll("button")].find(button=>/^(新会话|New Session)$/.test(button.textContent.trim()));assert.ok(create);create.click();
    await until(()=>{
      const created=calls.filter(row=>row.endpoint==="session/create").at(-1)?.result?.value?.sessionId;
      return calls.filter(row=>row.endpoint==="session/create").length>beforeRemovalCreates&&w.document.querySelector('[data-conversation-content]')?.dataset.conversationSession===created&&w.document.querySelector('[contenteditable]');
    },"Explicit New Session did not create a replacement composer");
    const input=w.document.querySelector('[contenteditable]');
    await until(()=>w.document.querySelector('[data-row-key^="session:"]'),"Explicit unstarted entry did not appear in the sidebar");
    const initialRows=[...w.document.querySelectorAll('[data-row-key^="session:"]')];
    // rc.5 intentionally renders the selected provisional blank. This is
    // recorded as an open product decision, never claimed as zero-history UX.
    assert.equal(initialRows.length,1);assert.ok(initialRows[0].textContent.includes("新会话"));
    const prompt="production browser exact visible user request phase5b-question-fixture";input.focus();
    const range=w.document.createRange();range.selectNodeContents(input);range.collapse(false);w.getSelection().removeAllRanges();w.getSelection().addRange(range);
    w.document.dispatchEvent(new w.Event("selectionchange"));await new Promise(resolve=>setTimeout(resolve,30));
    const paste=new w.Event("paste",{bubbles:true,cancelable:true});Object.defineProperty(paste,"clipboardData",{value:{types:["text/plain"],items:[],files:[],getData:type=>type==="text/plain"?prompt:""}});input.dispatchEvent(paste);
    await new Promise(resolve=>setTimeout(resolve,30));
    input.dispatchEvent(new w.KeyboardEvent("keydown",{key:"Enter",code:"Enter",bubbles:true,cancelable:true}));
    await until(()=>calls.some(row=>row.endpoint==="session/prompt"),()=>`Prompt not submitted: ${w.document.body.textContent.slice(-2000)}\nRPCs: ${calls.map(row=>row.endpoint+((row.endpoint==="session/create")?JSON.stringify({payload:row.payload,result:row.result}):"")).join(",")}\n${errors.map(error=>error.message).join("\n")}`);
    const sent=calls.find(row=>row.endpoint==="session/prompt"),sid=sent.payload.args.request.sessionId;
    assert.equal(binds.length,1);assert.equal(binds[0].dsh_session_id,sid);
    await until(()=>w.document.querySelector('button[role="radio"][aria-label="稳定循环"]'),()=>`Official question not displayed: ${w.document.body.textContent.slice(-3000)}\n${errors.map(error=>error.message).join("\n")}`);
    w.document.querySelector('button[role="radio"][aria-label="稳定循环"]').click();
    await new Promise(resolve=>setTimeout(resolve,20));
    [...w.document.querySelectorAll("button")].find(button=>/^(Next|下一题)$/.test(button.textContent.trim())).click();
    await until(()=>w.document.body.textContent.includes("是否补充说明？"),"Second official question did not appear");
    [...w.document.querySelectorAll("button")].find(button=>/^(Skip|跳过)$/.test(button.textContent.trim())).click();
    await until(()=>w.document.querySelector('button[role="checkbox"][aria-label="元素附着"]'),"Multi-select official question did not appear");
    w.document.querySelector('button[role="checkbox"][aria-label="元素附着"]').click();await new Promise(resolve=>setTimeout(resolve,20));w.document.querySelector('button[role="checkbox"][aria-label="能量循环"]').click();
    await new Promise(resolve=>setTimeout(resolve,20));
    const submit=[...w.document.querySelectorAll("button")].find(button=>/^(Submit|提交)$/.test(button.textContent.trim()));
    assert.ok(submit,`Official question submit unavailable: ${w.document.body.textContent.slice(-2000)}`);submit.click();
    await until(()=>[...w.document.querySelectorAll("button")].some(button=>/^(Allow once|允许一次)$/.test(button.textContent.trim()))||w.document.body.textContent.includes("phase5b official answer"),()=>`Official ordinary approval not shown: ${w.document.body.textContent.slice(-2500)}`);
    if(![...w.document.querySelectorAll("button")].some(button=>/^(Allow once|允许一次)$/.test(button.textContent.trim()))){const log=await host.context.sessionQuery.readSession(sid);throw new Error(`Approval did not wait: ${JSON.stringify(approvalFacts)}\n${errors.map(error=>error.message).join("\n")}\n${JSON.stringify(log.events.filter(row=>row.type.includes("tool")||row.type.includes("approval"))).slice(-4000)}`);}
    [...w.document.querySelectorAll("button")].find(button=>/^(Allow once|允许一次)$/.test(button.textContent.trim())).click();
    await until(()=>w.document.body.textContent.includes("phase5b official answer"),`Visible final answer not received: ${w.document.body.textContent.slice(-2000)}`);
    assert.deepEqual(approvalOutcomes,["allowed-once"]);
    assert.ok(w.document.body.textContent.includes(prompt));assert.equal(w.document.querySelectorAll("[data-conversation-content]").length,1);
    assert.ok(w.document.body.textContent.includes("新会话"),"The public locale Host default must be Chinese");
    const messages=await host.context.agents.get(sid).whenIdle();void messages;
    [...w.document.querySelectorAll("button")].find(button=>button.textContent.includes("候选工作区")).click();
    await until(()=>w.document.body.textContent.includes("候选版本 0"),()=>`Full candidate lost the public selected Session: ${w.document.body.textContent.slice(-2500)}`);
    assert.ok(w.document.body.textContent.includes("尚未获得 App 的审核许可"));assert.equal(w.document.querySelector('[data-conversation-content]'),null);
    w.document.querySelector(`[data-row-key="session:${sid}"]`).click();
    await until(()=>w.document.querySelector('[data-conversation-content]'),"Return from candidate to official conversation failed");
    const full=w.document.querySelector("[data-conversation-content]");assert.equal(full.dataset.conversationSession,sid);
    [...w.document.querySelectorAll("button")].find(button=>button.textContent.includes("排轴白板")).click();
    await until(()=>w.document.querySelector(".gs-editor-assistant [data-conversation-content]"),"Embedded Conversation not mounted");
    assert.equal(w.document.querySelectorAll("[data-conversation-content]").length,1);assert.equal(w.document.querySelector(".gs-editor-assistant [data-conversation-content]").dataset.conversationSession,sid);assert.ok(w.document.body.textContent.includes(prompt));assert.ok(w.document.body.textContent.includes("phase5b official answer"));
    [...w.document.querySelectorAll(".gs-editor-assistant button")].find(button=>button.textContent==="完整页面").click();
    await until(()=>!w.document.querySelector(".gs-editor-assistant"),"Embedded host was not released");
    assert.equal(w.document.querySelectorAll("[data-conversation-content]").length,1);assert.equal(w.document.querySelector("[data-conversation-content]").dataset.conversationSession,sid);assert.ok(w.document.body.textContent.includes(prompt));assert.ok(w.document.body.textContent.includes("phase5b official answer"));
    assert.equal(binds.length,1);assert.equal(resources.size,0);assert.equal(w.process,undefined);assert.equal(w.require,undefined);
    // Restart both the browser and official Host composition against the same
    // persisted profile, then open the old Session from the public sidebar.
    // No replayed prompt, provider answer or App seed binding is permitted.
    const completedProviderRequests=providerRequests;
    await w.GenshinDshClient.dispose();w.close();for(const stream of streams.values()){stream.control.abort();stream.uplink.end();}streams.clear();
    await host.dispose();host=await createNativeDshSessionHost({profileRoot:temp,mirrorRoot,clientAssets:{contract,acceptedManifest,localModuleBaseUrl:new URL("../",import.meta.url).href}});
    await host.kernel.secretController.set({provider:"deepseek-official",credential_generation:1,secret:"offline-browser-only-sentinel"});
    w=await mountBrowser();
    await until(()=>w.document.querySelector('[data-row-key^="workspace:"]'),"Restored Workspace not present");
    const group=[...w.document.querySelectorAll('[data-row-key^="workspace:"]')].find(row=>row.textContent.includes("原神排轴"));
    if(group?.getAttribute("aria-expanded")==="false")group.click();
    await until(()=>w.document.querySelector(`[data-row-key="session:${sid}"]`),()=>`Persisted Session missing from sidebar: ${w.document.body.textContent.slice(-2200)}`);
    w.document.querySelector(`[data-row-key="session:${sid}"]`).click();
    await until(()=>w.document.body.textContent.includes(prompt)&&w.document.body.textContent.includes("phase5b official answer"),()=>`Persisted conversation not restored: ${w.document.body.textContent.slice(-3000)}\n${errors.map(error=>error.message).join("\n")}`);
    assert.equal(w.document.querySelector('[data-conversation-content]').dataset.conversationSession,sid);assert.equal(w.document.querySelectorAll('[data-conversation-content]').length,1);
    assert.equal(providerRequests,completedProviderRequests);assert.equal(binds.length,1);assert.equal(calls.filter(row=>row.endpoint==="session/prompt").length,1);
    assert.equal(errors.filter(error=>/Uncaught|slot entry crashed/.test(error.message)).length,0,errors.map(error=>error.message).join("\n"));
  }finally{
    await dom?.window.GenshinDshClient?.dispose();dom?.window.close();for(const stream of streams.values())stream.control.abort();await host?.dispose();globalThis.fetch=originalFetch;
    TypertGatewayService.prototype.registerRemoteEvents=registerRemoteEvents;
    if(!resolve(temp).startsWith(resolve(frontend)+sep+".controller-browser-"))throw new Error("unsafe_test_cleanup_path");await rm(temp,{recursive:true,force:true});
  }
});
