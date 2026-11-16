/** Offline real pi-ai adapters; each URL receives only its own synthetic key. */
import assert from 'node:assert/strict'
import { randomUUID } from 'node:crypto'
import { mkdtemp,mkdir } from 'node:fs/promises'
import { resolve,join } from 'node:path'
import { createNativeDshSessionHost } from './native-dsh-session-host.mjs'
const base=resolve('.codex-tmp/provider-routes');await mkdir(base,{recursive:true});
const root=await mkdtemp(join(base,'profile-'));
const previous=globalThis.fetch;let host,peer;const requests=[];
const routes={
 'qualification-anthropic':{api:'anthropic-messages',baseURL:'https://anthropic.qualification.invalid/v1'},
 'qualification-completions':{api:'openai-completions',baseURL:'https://completions.qualification.invalid/v1'},
 'qualification-responses':{api:'openai-responses',baseURL:'https://responses.qualification.invalid/v1'},
 'qualification-google':{api:'google-generative-ai',baseURL:'https://google.qualification.invalid/v1beta'},
};
const event=(type,extra)=>({type,...extra});
const sse=rows=>new Response(rows.map(row=>(row.type?`event: ${row.type}\n`:'')+`data: ${JSON.stringify(row)}\n\n`).join(''),{headers:{'content-type':'text/event-stream'}});
globalThis.fetch=async(url,init)=>{
 const request=new Request(url,init),route=Object.entries(routes).find(([,v])=>request.url.startsWith(v.baseURL));
 assert.ok(route,'unexpected network endpoint '+request.url);const [provider,config]=route;
 const key=request.headers.get('x-api-key')??request.headers.get('authorization')??request.headers.get('x-goog-api-key')??new URL(request.url).searchParams.get('key');
 assert.ok(key?.endsWith('sentinel-'+provider),'credential was not scoped to route');
 requests.push({provider,path:new URL(request.url).pathname});
 const text='offline route completed';
 if(config.api==='anthropic-messages')return sse([
  event('message_start',{message:{id:'msg_fixture',type:'message',role:'assistant',model:'fixture',content:[],usage:{input_tokens:20,output_tokens:0}}}),
  event('content_block_start',{index:0,content_block:{type:'text',text:''}}),
  event('content_block_delta',{index:0,delta:{type:'text_delta',text}}),event('content_block_stop',{index:0}),
  event('message_delta',{delta:{stop_reason:'end_turn'},usage:{output_tokens:4}}),event('message_stop',{})]);
 if(config.api==='openai-completions')return sse([
  {id:'fixture',object:'chat.completion.chunk',created:0,model:'fixture',choices:[{index:0,delta:{role:'assistant',content:text},finish_reason:null}]},
  {id:'fixture',object:'chat.completion.chunk',created:0,model:'fixture',choices:[{index:0,delta:{},finish_reason:'stop'}],usage:{prompt_tokens:20,completion_tokens:4,total_tokens:24}}]);
 if(config.api==='google-generative-ai')return sse([{candidates:[{content:{role:'model',parts:[{text}]},finishReason:'STOP',index:0}],usageMetadata:{promptTokenCount:20,candidatesTokenCount:4,totalTokenCount:24}}]);
 const item={id:'msg_fixture',type:'message',role:'assistant',status:'completed',content:[{type:'output_text',text,annotations:[]}]};
 return sse([
  event('response.created',{response:{id:'resp_fixture',object:'response',created_at:0,status:'in_progress',model:'fixture',output:[]}}),
  event('response.output_item.added',{output_index:0,item:{...item,status:'in_progress',content:[]}}),
  event('response.content_part.added',{item_id:item.id,output_index:0,content_index:0,part:{type:'output_text',text:'',annotations:[]}}),
  event('response.output_text.delta',{item_id:item.id,output_index:0,content_index:0,delta:text}),
  event('response.output_text.done',{item_id:item.id,output_index:0,content_index:0,text}),
  event('response.content_part.done',{item_id:item.id,output_index:0,content_index:0,part:item.content[0]}),
  event('response.output_item.done',{output_index:0,item}),
  event('response.completed',{response:{id:'resp_fixture',object:'response',created_at:0,status:'completed',model:'fixture',output:[item],usage:{input_tokens:20,output_tokens:4,total_tokens:24}}})]);
};
const rpc=async(endpoint,args)=>{const response=await host.handler.fetch(new Request('https://genshin-shell.invalid/api/'+endpoint,{method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify({type:'client-request',rpcId:randomUUID(),method:endpoint,payload:{args}})}),peer);const result=(await response.json()).result;assert.equal(result.ok,true,JSON.stringify(result));return result.value;};
try{
 host=await createNativeDshSessionHost({profileRoot:root,mirrorRoot:join(root,'media')});peer=host.createPeer();
 const providers={};
 for(const [provider,config] of Object.entries(routes)){
  const ref=provider.replaceAll('-','_').toUpperCase();await host.context.credentials.set(ref,'sentinel-'+provider);
  providers[provider]={...config,apiKeyEnv:ref,models:[{id:'fixture',contextWindow:200000,maxTokens:2048,reasoning:false,input:['text']}]};
 }
 const describe=await rpc('genshinProfile/configDescribe',{request:{entry_id:'llm-pi-ai'}});
 await rpc('genshinProfile/configEdit',{request:{entry_id:'llm-pi-ai',expected_fingerprint:describe.entries[0].fingerprint,operations:[{op:'set',path:['providers'],value:providers}]}});
 for(const provider of Object.keys(routes)){
  const created=await rpc('session/create',{request:{sessionId:provider}});const agent=host.context.agents.get(created.sessionId);
  agent.model.select({provider,model:'fixture'});
  await rpc('session/prompt',{request:{sessionId:agent.id,requestId:randomUUID(),mode:'queue',content:[{type:'text',text:'Offline protocol qualification only.'}]}});
  await agent.whenIdle();const log=await host.context.sessionQuery.readSession(agent.id);
  const end=log.events.findLast(row=>row.type==='turn/end');assert.equal(end?.data.reason?.kind,'completed',JSON.stringify(end));
  assert.ok(JSON.stringify(log.events).includes('offline route completed'));
 }
 assert.equal(requests.length,4);
 console.log(JSON.stringify({contract:'genshin-provider-wire-routes-v1',status:'passed',protocols:Object.values(routes).map(v=>v.api),requests:requests.length,externalNetwork:false,keysIsolated:true}));
}finally{await peer?.dispose();await host?.dispose();globalThis.fetch=previous;}
