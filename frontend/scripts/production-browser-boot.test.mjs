import assert from "node:assert/strict";
import {readFile} from "node:fs/promises";
import {randomUUID,createHash,webcrypto} from "node:crypto";
import {test} from "node:test";
import {JSDOM,VirtualConsole} from "jsdom";
import {bootInjections} from "@deepseek-ai/dsh-client-modules";
import {composeReviewedRegistry,readCompositionContract} from "./dsh-client-review-lib.mjs";

test("production browser bundle boots one real DSH owner without Node globals and preserves editor slots",{timeout:30000},async()=>{
  const contract=await readCompositionContract();
  const {ctx,graph}=await composeReviewedRegistry(contract);
  const errors=[];
  const console=new VirtualConsole();console.on("jsdomError",error=>errors.push(error));console.on("error",(...values)=>errors.push(new Error(values.map(value=>value?.stack||String(value)).join(" "))));
  const dom=new JSDOM('<!doctype html><html><head></head><body><div id="genshin-editor-parking"></div><div id="dsh-app-root"></div></body></html>',{runScripts:"dangerously",url:"https://dsh-shell.invalid/",pretendToBeVisual:true,virtualConsole:console});
  const w=dom.window,resources=new Map(),navigation=[];
  Object.assign(w,{TextEncoder,TextDecoder,Request,Response,ResizeObserver:class{observe(){}unobserve(){}disconnect(){}},matchMedia:()=>({matches:false,addEventListener(){},removeEventListener(){}})});
  Object.defineProperty(w.crypto,"subtle",{value:webcrypto.subtle});
  Object.defineProperty(w.document,"fonts",{value:{addEventListener(){},removeEventListener(){},ready:Promise.resolve()},configurable:true});
  const editor=w.document.createElement("div");editor.textContent="business editor sentinel";editor.hidden=true;
  w.document.getElementById("genshin-editor-parking").append(editor);
  w.GenshinEditorBridge={root:editor,navigate:page=>navigation.push(page),document:()=>({document_kind:null,document:null}),setTheme(){}};
  w.pywebview={api:{
    async dsh_client_boot(){return {injections:bootInjections(graph)};},
    async dsh_client_rpc(){return {ok:false,error:{code:"fixture_remote_unavailable",message:"No provider is contacted by this browser-only test.",details:{}}};},
    async dsh_client_request_cancel(){return {cancelled:true};},
    async dsh_client_stream_open(request){return {stream_id:request.stream_id,cursor:0};},
    async dsh_client_stream_read(request){return {items:[],cursor:request.cursor+1,done:true};},
    async dsh_client_stream_cancel(){return {cancelled:true};},
    async dsh_client_stream_send(request){return {sequence:request.sequence};},
    async dsh_client_bundle({url}){
      const response=await ctx.clientModules.fetchBundle(new Request(new URL(url,"https://dsh-shell.invalid/")));
      const body=Buffer.from(await response.arrayBuffer()),id=randomUUID();resources.set(id,body);
      return {status:response.status,contentType:response.headers.get("content-type"),resource:{resource_id:id,byte_length:body.length,sha256:createHash("sha256").update(body).digest("hex")}};
    },
    async dsh_client_resource_read({resource_id,offset,length}){const bytes=resources.get(resource_id),end=Math.min(bytes.length,offset+length);return {resource_id,offset,body_base64:bytes.subarray(offset,end).toString("base64"),eof:end===bytes.length};},
    async dsh_client_resource_release({resource_id}){resources.delete(resource_id);return {released:true};},
    async dsh_client_domain_binding(){return {binding:null};},
    async dsh_client_candidate(){return {code:1,message:"No candidate in blank fixture"};},
    async dsh_client_candidate_read(){return {code:1,message:"No candidate in blank fixture"};},
    async dsh_client_bind_document({dsh_session_id}){return {binding:{dsh_session_id,conversation_id:"fixture-conversation"},existing:false};},
    async dsh_client_credential_status(){return {available:true,saved:false,status:"ready",credential_generation:0};},
    async dsh_client_credential_save(){throw new Error("This browser boot fixture must not save a real key");},
    async dsh_client_credential_delete(){throw new Error("This browser boot fixture must not delete a real key");},
    async dsh_client_runtime_status(){return {state:"stopped"};},
  }};
  try{
    assert.equal(w.process,undefined);assert.equal(w.require,undefined);
    const script=w.document.createElement("script");script.textContent=await readFile(new URL("../dist/production/dsh-production.js",import.meta.url),"utf8");w.document.head.append(script);
    assert.ok(w.GenshinDshClient,errors.map(error=>`${error.message}\n${error.detail?.stack||""}`).join("\n"));await w.GenshinDshClient.ready;
    // Give the single upstream React root its first commit; no second createRoot.
    for(let i=0;i<80&&!w.document.body.textContent.includes("排轴白板");i++)await new Promise(resolve=>setTimeout(resolve,10));
    assert.equal(w.document.getElementById("dsh-app-root").dataset.ready,"true");
    const buttons=[...w.document.querySelectorAll("button")];
    const board=buttons.find(button=>button.textContent.includes("排轴白板"));
    assert.equal(w.document.querySelectorAll("[data-conversation-content]").length,1,"The main DSH host mounts one Conversation factory");
    assert.ok(board,"App business pages must be registered in the real DSH sidebar");board.click();
    for(let i=0;i<80&&!navigation.length;i++)await new Promise(resolve=>setTimeout(resolve,10));
    assert.deepEqual(navigation,["whiteboard"]);assert.equal(editor.hidden,false);
    for(let i=0;i<80&&!w.document.querySelector(".gs-editor-assistant [data-conversation-content]");i++)await new Promise(resolve=>setTimeout(resolve,10));
    assert.equal(w.document.querySelectorAll("[data-conversation-content]").length,1,`Editor embedded mode must not mount a second simultaneous Conversation\n${errors.map(error=>error.message).join("\n")}\n${w.document.body.textContent.slice(-1500)}`);
    assert.ok(w.document.querySelector(".gs-editor-assistant [data-conversation-content]"),errors.map(error=>error.message).join("\n"));
    const expand=[...w.document.querySelectorAll(".gs-editor-assistant button")].find(button=>button.textContent==="完整页面");assert.ok(expand);expand.click();
    for(let i=0;i<80&&w.document.querySelector(".gs-editor-assistant");i++)await new Promise(resolve=>setTimeout(resolve,10));
    assert.equal(w.document.querySelectorAll("[data-conversation-content]").length,1);assert.equal(w.document.querySelector(".gs-editor-assistant"),null);
    [...w.document.querySelectorAll("button")].find(button=>button.textContent.includes("排轴白板")).click();
    for(let i=0;i<80&&!w.document.querySelector(".gs-editor-assistant");i++)await new Promise(resolve=>setTimeout(resolve,10));
    const candidate=[...w.document.querySelectorAll("button")].find(button=>button.textContent.includes("候选工作区"));
    assert.ok(candidate);candidate.click();
    for(let i=0;i<80&&!editor.hidden;i++)await new Promise(resolve=>setTimeout(resolve,10));
    assert.equal(editor.hidden,true,"DSH panel switch parks the existing editor DOM instead of cloning a second editor");
    const settings=[...w.document.querySelectorAll("button")].find(button=>/设置|Settings/.test(button.textContent));
    assert.ok(settings,"The upstream settings-general shell must be included");
    settings.click();
    for(let i=0;i<80&&!w.document.body.textContent.includes("DeepSeek 密钥");i++)await new Promise(resolve=>setTimeout(resolve,10));
    const account=[...w.document.querySelectorAll("button")].find(button=>button.textContent.includes("DeepSeek 密钥"));
    assert.ok(account,"App Windows credentials must be reachable through the upstream settings section");account.click();
    for(let i=0;i<80&&!w.document.querySelector('[aria-label="DeepSeek API Key"]');i++)await new Promise(resolve=>setTimeout(resolve,10));
    assert.ok(w.document.querySelector('[aria-label="DeepSeek API Key"]'));
    assert.equal(w.process,undefined);assert.equal(w.require,undefined);assert.equal(resources.size,0);
    assert.equal(errors.filter(error=>/Uncaught|node:module is not/.test(error.message)).length,0,errors.map(error=>error.message).join("\n"));
  }finally{await w.GenshinDshClient?.dispose();w.close();await ctx.fiber.dispose();}
});
