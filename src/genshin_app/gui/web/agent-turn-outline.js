(function (global) {
  "use strict";

  const OUTLINE_CONTRACT = "agent-turn-outline-v1";
  const SEARCH_CONTRACT = "agent-search-results-v1";
  const ANCHOR_KEY = "genshin.agent.reading-anchors.v1";
  const MAX_ITEMS = 500;
  const MAX_RESULTS = 200;
  const MAX_EXCERPT = 512;
  const MAX_QUERY = 256;
  const MAX_ANCHORS = 32;
  const MAX_STORAGE_BYTES = 32 * 1024;
  const TERMINAL = new Set(["completed","cancelled","failed","interrupted"]);
  const STATUS = new Set(["preparing","streaming","using_tool","awaiting_input_live","awaiting_input_suspended","resuming","cancelling",...TERMINAL]);

  const string=(value,limit)=>typeof value==="string"?value.slice(0,limit):"";
  const integer=value=>Number.isSafeInteger(value)&&value>=0?value:null;
  const freeze=value=>{if(value&&typeof value==="object"&&!Object.isFrozen(value)){for(const child of Object.values(value))freeze(child);Object.freeze(value);}return value;};
  const reject=reason=>freeze({ok:false,reason,projection:null});

  function parseOutline(value, expectedSessionId = null) {
    if(!value||typeof value!=="object"||Array.isArray(value)||value.contract!==OUTLINE_CONTRACT)return reject("outline_contract_invalid");
    const sessionId=string(value.session_id,256);if(!sessionId||expectedSessionId&&sessionId!==expectedSessionId)return reject("outline_session_invalid");
    const revision=integer(value.projection_revision),sourceCursor=integer(value.source_cursor);if(revision===null||sourceCursor===null)return reject("outline_revision_invalid");
    if(!Array.isArray(value.items)||value.items.length>MAX_ITEMS)return reject("outline_items_invalid");
    const ids=new Set(),items=[];let previousEnd=-1;
    for(const raw of value.items){
      if(!raw||typeof raw!=="object"||Array.isArray(raw))return reject("outline_item_invalid");
      const turnId=string(raw.turn_id,256),start=integer(raw.start_seq),end=integer(raw.end_seq),status=string(raw.status,64);
      if(!turnId||ids.has(turnId)||start===null||end===null||end<start||start<=previousEnd||!STATUS.has(status))return reject("outline_item_identity_invalid");
      if(!Array.isArray(raw.markers)||raw.markers.length>32||raw.markers.some(marker=>typeof marker!=="string"||marker.length>64))return reject("outline_markers_invalid");
      ids.add(turnId);previousEnd=end;items.push(freeze({turn_id:turnId,start_seq:start,end_seq:end,status,excerpt:string(raw.excerpt,MAX_EXCERPT),markers:[...raw.markers],unread:raw.unread===true,terminal:TERMINAL.has(status)}));
    }
    const completeness=value.completeness==="complete"?"complete":"partial";
    const totalTurns=value.total_turns===undefined?items.length:integer(value.total_turns);
    const olderCursor=value.older_cursor==null?null:integer(value.older_cursor),newerCursor=value.newer_cursor==null?null:integer(value.newer_cursor);
    if(totalTurns===null||totalTurns<items.length||typeof value.has_older!=="undefined"&&typeof value.has_older!=="boolean"||typeof value.has_newer!=="undefined"&&typeof value.has_newer!=="boolean"||value.older_cursor!=null&&olderCursor===null||value.newer_cursor!=null&&newerCursor===null)return reject("outline_pagination_invalid");
    return freeze({ok:true,reason:null,projection:{contract:OUTLINE_CONTRACT,session_id:sessionId,projection_revision:revision,source_cursor:sourceCursor,completeness,partial_reasons:Array.isArray(value.partial_reasons)?value.partial_reasons.filter(x=>typeof x==="string").slice(0,32):[],items,total_turns:totalTurns,has_older:value.has_older===true,has_newer:value.has_newer===true,older_cursor:olderCursor,newer_cursor:newerCursor}});
  }

  function parseSearch(value, expected = {}) {
    if(!value||typeof value!=="object"||Array.isArray(value)||value.contract!==SEARCH_CONTRACT)return reject("search_contract_invalid");
    const sessionId=string(value.session_id,256),revision=integer(value.projection_revision),sourceCursor=integer(value.source_cursor),digest=string(value.query_digest,128);
    if(!sessionId||expected.session_id&&sessionId!==expected.session_id||revision===null||sourceCursor===null||!digest||expected.query_digest&&digest!==expected.query_digest)return reject("search_identity_invalid");
    if(!Array.isArray(value.results)||value.results.length>MAX_RESULTS)return reject("search_results_invalid");
    const ids=new Set(),results=[];
    for(const raw of value.results){const id=string(raw?.stable_node_id,256),turnId=string(raw?.turn_id,256),seq=integer(raw?.source_seq);if(!id||!turnId||seq===null||ids.has(id))return reject("search_result_identity_invalid");ids.add(id);results.push(freeze({stable_node_id:id,turn_id:turnId,source_seq:seq,kind:string(raw.kind,64),excerpt:string(raw.excerpt,MAX_EXCERPT)}));}
    const nextCursor=value.next_cursor==null?null:(typeof value.next_cursor==="string"&&value.next_cursor.length>0&&value.next_cursor.length<=4096?value.next_cursor:integer(value.next_cursor));if(typeof value.has_more!=="undefined"&&typeof value.has_more!=="boolean"||value.next_cursor!=null&&nextCursor===null)return reject("search_pagination_invalid");
    return freeze({ok:true,reason:null,projection:{contract:SEARCH_CONTRACT,session_id:sessionId,projection_revision:revision,source_cursor:sourceCursor,query_digest:digest,completeness:value.completeness==="complete"?"complete":"partial",partial_reasons:Array.isArray(value.partial_reasons)?value.partial_reasons.filter(x=>typeof x==="string").slice(0,32):[],results,has_more:value.has_more===true,next_cursor:nextCursor}});
  }

  function createAnchorStore(storage) {
    let values={};
    try{const raw=storage?.getItem?.(ANCHOR_KEY);if(raw&&raw.length<=MAX_STORAGE_BYTES){const parsed=JSON.parse(raw);if(parsed?.version===1&&parsed.anchors&&typeof parsed.anchors==="object")for(const [id,row] of Object.entries(parsed.anchors).slice(0,MAX_ANCHORS)){const sessionId=string(id,256),nodeId=string(row?.stable_node_id,256),offset=Number(row?.offset_px);if(sessionId&&(nodeId||row?.at_bottom===true)&&Number.isFinite(offset)&&Math.abs(offset)<=1000000)values[sessionId]={stable_node_id:nodeId||null,offset_px:Math.round(offset),at_bottom:row.at_bottom===true,updated_at:integer(row.updated_at)||0};}}}catch{values={};}
    const persist=()=>{try{storage?.setItem?.(ANCHOR_KEY,JSON.stringify({version:1,anchors:values}));return true;}catch{return false;}};
    return Object.freeze({get:id=>values[string(id,256)]?freeze({...values[string(id,256)]}):null,set(id,row){const sessionId=string(id,256),nodeId=string(row?.stable_node_id,256),offset=Number(row?.offset_px);if(!sessionId||!Number.isFinite(offset)||Math.abs(offset)>1000000||!nodeId&&row?.at_bottom!==true)return false;values[sessionId]={stable_node_id:nodeId||null,offset_px:Math.round(offset),at_bottom:row?.at_bottom===true,updated_at:Date.now()};const ordered=Object.entries(values).sort((a,b)=>b[1].updated_at-a[1].updated_at).slice(0,MAX_ANCHORS);values=Object.fromEntries(ordered);return persist();},delete(id){delete values[string(id,256)];return persist();}});
  }

  async function queryDigest(query, cryptoApi = global.crypto) {
    const value=string(query,MAX_QUERY).trim();if(!value||!cryptoApi?.subtle?.digest||typeof global.TextEncoder!=="function")return null;
    try{const bytes=new global.TextEncoder().encode(value),digest=await cryptoApi.subtle.digest("SHA-256",bytes);return [...new Uint8Array(digest)].map(byte=>byte.toString(16).padStart(2,"0")).join("");}catch{return null;}
  }

  function node(document,tag,value=null,className=""){const row=document.createElement(tag);if(className)row.className=className;if(value!=null)row.textContent=String(value);return row;}
  function create(document,host,callbacks={}){
    if(!document||!host)throw new TypeError("agent_turn_outline_host_invalid");const list=node(document,"div",null,"ac-turn-outline"),notice=node(document,"p",null,"ac-note");host.replaceChildren(list,notice);let projection=null,selected=null;
    const render=()=>{const rows=[];if(projection?.has_older){const button=node(document,"button","加载更早轮次","ac-turn-outline-page");button.type="button";button.addEventListener("click",()=>callbacks.onPage?.({before_seq:projection.older_cursor,limit:MAX_ITEMS}));rows.push(button);}for(const turn of projection?.items||[]){const button=node(document,"button",null,"ac-turn-outline-item");button.type="button";button.dataset.turnId=turn.turn_id;button.setAttribute("aria-current",turn.turn_id===selected?"true":"false");button.append(node(document,"strong",turn.excerpt||turn.turn_id),node(document,"span",`${turn.status}${turn.unread?" · 未读":""}`));button.addEventListener("click",()=>callbacks.onLocate?.({turn_id:turn.turn_id,source_seq:turn.start_seq,stable_node_id:null}));rows.push(button);}if(projection?.has_newer){const button=node(document,"button","加载更新轮次","ac-turn-outline-page");button.type="button";button.addEventListener("click",()=>callbacks.onPage?.({after_seq:projection.newer_cursor,limit:MAX_ITEMS}));rows.push(button);}list.replaceChildren(...rows);notice.textContent=projection?.completeness==="partial"?"较早历史或部分投影尚未加载；定位时将由 Host 读取窗口。":"";};
    return Object.freeze({update(value,sessionId=null){const parsed=parseOutline(value,sessionId);if(!parsed.ok){projection=null;list.replaceChildren();notice.textContent="Turn 导航投影无法核验。";return parsed;}projection=parsed.projection;render();return parsed;},select(turnId){selected=string(turnId,256)||null;render();},showSearch(value,expected={}){const parsed=parseSearch(value,expected);if(!parsed.ok)return parsed;callbacks.onSearchResults?.(parsed.projection);return parsed;},getProjection:()=>projection,list,notice});
  }

  global.AgentTurnOutline=Object.freeze({OUTLINE_CONTRACT,SEARCH_CONTRACT,ANCHOR_KEY,parseOutline,parseSearch,queryDigest,createAnchorStore,create});
})(window);
