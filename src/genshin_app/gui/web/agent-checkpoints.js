(function (global) {
  "use strict";

  const LIST_CONTRACT = "genshin-agent-document-checkpoint-v1";
  const DIFF_CONTRACT = "agent-document-checkpoint-diff-v1";
  const ID = /^checkpoint_[A-Za-z0-9_-]{16,64}$/u;
  const DOCUMENT_ID = /^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$/u;
  const FORBIDDEN_KEY = /(?:secret|credential|api[_-]?key|raw[_-]?reasoning|(?:^|_)(?:path|file_path|local_path)$)/iu;
  const LOCAL_PATH = /^(?:[a-z]:[\\/]|[\\/]{1,2}|file:)/iu;
  const MAX_ITEMS = 256;
  const text = (value, limit = 1000) => typeof value === "string" ? value.slice(0, limit) : "";
  const integer = value => Number.isSafeInteger(value) && value >= 0 ? value : null;
  const freeze = value => { if (value && typeof value === "object" && !Object.isFrozen(value)) { for (const child of Object.values(value)) freeze(child); Object.freeze(value); } return value; };
  const reject = reason => freeze({ok:false, reason, projection:null});

  function safeShape(value) {
    if (!value || typeof value !== "object") return true;
    const stack=[value],seen=new Set();let count=0;
    while(stack.length){const row=stack.pop();if(seen.has(row)||++count>20_000)return false;seen.add(row);for(const [key,child] of Object.entries(row)){if(FORBIDDEN_KEY.test(key))return false;if(typeof child==="string"&&LOCAL_PATH.test(child))return false;if(child&&typeof child==="object")stack.push(child);}}
    return true;
  }

  function parseList(value, expectedDocumentId = null) {
    if(!value||typeof value!=="object"||Array.isArray(value)||!safeShape(value)||value.contract!==LIST_CONTRACT||value.type!=="document_restore_point")return reject("checkpoint_list_invalid");
    const documentId=text(value.document_id,128),lineage=integer(value.document_lineage_epoch),revision=integer(value.document_revision),storeRevision=integer(value.store_revision),offset=integer(value.offset),limit=integer(value.limit),total=integer(value.total),next=value.next_offset===null?null:integer(value.next_offset);
    if(!DOCUMENT_ID.test(documentId)||expectedDocumentId&&documentId!==expectedDocumentId||[lineage,revision,storeRevision,offset,limit,total].includes(null)||limit<1||limit>MAX_ITEMS||next!==null&&next<=offset||!Array.isArray(value.items)||value.items.length>limit||offset+value.items.length>total)return reject("checkpoint_list_identity_invalid");
    const ids=new Set(),items=[];
    for(const raw of value.items){const id=text(raw?.checkpoint_id,96),name=text(raw?.name,256),kind=text(raw?.document_kind,32),itemDocument=text(raw?.document_id,128),itemLineage=integer(raw?.document_lineage_epoch),itemRevision=integer(raw?.revision),created=text(raw?.created_at,64),reason=text(raw?.reason,128),sourceState=text(raw?.source_state,32)||"unknown",source=raw?.source&&typeof raw.source==="object"&&!Array.isArray(raw.source)?raw.source:{};if(!ID.test(id)||ids.has(id)||!name||!DOCUMENT_ID.test(itemDocument)||itemDocument!==documentId||!['whiteboard','native'].includes(kind)||itemLineage===null||itemRevision===null||!created||typeof raw?.automatic!=="boolean"||!["available","archived","tombstone","unattributed","unknown"].includes(sourceState))return reject("checkpoint_item_invalid");const sourceConversation=source.conversation_id==null?null:text(source.conversation_id,128),sourceTurn=source.turn_id==null?null:text(source.turn_id,128);if(sourceConversation!==null&&!DOCUMENT_ID.test(sourceConversation)||sourceTurn!==null&&!DOCUMENT_ID.test(sourceTurn))return reject("checkpoint_source_invalid");ids.add(id);items.push(freeze({checkpoint_id:id,name,document_kind:kind,document_lineage_epoch:itemLineage,revision:itemRevision,created_at:created,automatic:raw.automatic,reason,source_state:sourceState,source_conversation_id:sourceConversation,source_turn_id:sourceTurn}));}
    return freeze({ok:true,reason:null,projection:{contract:LIST_CONTRACT,document_id:documentId,document_lineage_epoch:lineage,document_revision:revision,store_revision:storeRevision,offset,limit,total,next_offset:next,items}});
  }

  function parseDiff(value, expectedCheckpointId = null) {
    if(!value||typeof value!=="object"||Array.isArray(value)||!safeShape(value)||value.contract!==DIFF_CONTRACT)return reject("checkpoint_diff_invalid");
    const checkpointId=text(value.checkpoint_id,96),sourceLineage=integer(value.source_lineage_epoch),sourceRevision=integer(value.source_revision),targetLineage=integer(value.target_lineage_epoch),targetRevision=integer(value.target_revision);
    if(!ID.test(checkpointId)||expectedCheckpointId&&checkpointId!==expectedCheckpointId||[sourceLineage,sourceRevision,targetLineage,targetRevision].includes(null)||value.large_content_inlined!==false||typeof value.application_payload_reusable!=="boolean")return reject("checkpoint_diff_identity_invalid");
    const section=raw=>{if(!raw||typeof raw!=="object"||typeof raw.changed!=="boolean"||typeof raw.truncated!=="boolean"||integer(raw.changed_key_count)===null)return null;const result={changed:raw.changed,changed_key_count:raw.changed_key_count,truncated:raw.truncated};for(const name of ['added_keys','removed_keys','changed_keys']){if(!Array.isArray(raw[name])||raw[name].length>32||raw[name].some(key=>!text(key,128)))return null;result[name]=[...raw[name]];}return freeze(result);};
    const document=section(value.document),payload=section(value.application_payload);if(!document||!payload)return reject("checkpoint_diff_rows_invalid");
    const match=value.fingerprint_match;if(!match||typeof match!=="object"||['database','engine','schema'].some(key=>typeof match[key]!=="boolean"))return reject("checkpoint_diff_fingerprint_invalid");
    return freeze({ok:true,reason:null,projection:{contract:DIFF_CONTRACT,checkpoint_id:checkpointId,source_lineage_epoch:sourceLineage,source_revision:sourceRevision,target_lineage_epoch:targetLineage,target_revision:targetRevision,document,application_payload:payload,fingerprint_match:{database:match.database,engine:match.engine,schema:match.schema},application_payload_reusable:value.application_payload_reusable}});
  }

  const node=(document,tag,value=null,className="")=>{const item=document.createElement(tag);if(className)item.className=className;if(value!==null)item.textContent=String(value);return item;};
  function create(document, host, options={}) {
    if(!document||!host)throw new TypeError("checkpoint_host_invalid");
    let projection=null,busy=false,frozen=false,status="",createOpen=false,editing=null;
    const commandMenu=options.contextMenu||global.AppContextMenu?.forDocument?.(document,global)||null;
    const invoke=(name,value)=>{if(!busy&&!frozen)return options[name]?.(value);};
    const withCurrent=(checkpointId,run)=>{const item=projection?.items.find(row=>row.checkpoint_id===checkpointId);
      if(!item){status="恢复点列表已经变化，请重新打开操作菜单。";render();return false;}return run(item);};
    const editor=(initial,onSave,onCancel,label)=>{
      const row=node(document,"div",null,"ac-checkpoint-editor"),input=node(document,"input"),save=node(document,"button","保存"),cancel=node(document,"button","取消");
      input.type="text";input.maxLength=256;input.value=initial;input.setAttribute?.("aria-label",label);save.type=cancel.type="button";
      save.addEventListener("click",()=>{const value=input.value.trim();if(value)onSave(value);});cancel.addEventListener("click",onCancel);row.append(input,save,cancel);return row;
    };
    const render=()=>{
      host.replaceChildren();
      const header=node(document,"div",null,"ac-checkpoint-header"),title=node(document,"h4",projection?`恢复点（${projection.total}）`:"文档恢复点"),createButton=node(document,"button","建立恢复点");
      createButton.type="button";createButton.disabled=busy||frozen||!projection;createButton.addEventListener("click",()=>{createOpen=true;render();});header.append(title,createButton);host.append(header);
      if(status)host.append(node(document,"p",status,frozen?"ac-error":"ac-note"));
      if(createOpen&&projection)host.append(editor("",name=>{createOpen=false;render();invoke("onCreate",{name,document_id:projection.document_id,store_revision:projection.store_revision});},()=>{createOpen=false;render();},"恢复点名称"));
      if(!projection){host.append(node(document,"p","当前文档尚未开放可核验的恢复点。","ac-note"));return;}
      for(const item of projection.items){
        const card=node(document,"article",null,"ac-checkpoint-card");card.dataset.checkpointId=item.checkpoint_id;
        if(editing===item.checkpoint_id)card.append(editor(item.name,name=>{editing=null;render();invoke("onRename",{item,name,store_revision:projection.store_revision});},()=>{editing=null;render();},"新的恢复点名称"));else card.append(node(document,"strong",item.name));
        const kindLabel=item.document_kind==="whiteboard"?"白板":"原生方案",version=node(document,"p",`${kindLabel} · 恢复版本 ${item.document_lineage_epoch} · 文档版本 ${item.revision}${item.automatic?" · 自动安全点":""}`,"ac-note");
        version.title=`技术信息：${item.document_kind} · lineage ${item.document_lineage_epoch} · revision ${item.revision}`;
        card.append(version,node(document,"p",item.created_at,"ac-note"));
        const sourceText=item.source_state==="tombstone"?"来源对话已删除；恢复点仍按文档保留。":item.source_state==="archived"?"来源对话已归档。":item.source_state==="available"?"来源对话可用。":item.source_state==="unattributed"?"系统恢复点，无对话来源。":"来源状态尚未核验。";card.append(node(document,"p",sourceText,item.source_state==="tombstone"?"ac-warning":"ac-note"));
        const controls=node(document,"div",null,"ac-actions"),preview=node(document,"button","查看差异"),restore=node(document,"button","恢复"),rename=node(document,"button","重命名"),remove=node(document,"button","删除");
        for(const button of [preview,restore,rename,remove]){button.type="button";button.disabled=busy||frozen;}
        preview.addEventListener("click",()=>invoke("onPreview",{item,store_revision:projection.store_revision}));restore.addEventListener("click",()=>invoke("onRestore",{item,store_revision:projection.store_revision}));
        rename.addEventListener("click",()=>{if(!busy&&!frozen){editing=item.checkpoint_id;render();}});remove.addEventListener("click",()=>invoke("onDelete",{item,store_revision:projection.store_revision}));
        if(commandMenu){
          const menuItems=()=>[{id:`rename-${item.checkpoint_id}`,label:"重命名",enabled:!busy&&!frozen,disabledReason:busy||frozen?"当前操作尚未完成":"",
            run:()=>withCurrent(item.checkpoint_id,current=>{editing=current.checkpoint_id;render();})},
          {type:"separator"},{id:`delete-${item.checkpoint_id}`,label:"删除恢复点",danger:true,enabled:!busy&&!frozen,disabledReason:busy||frozen?"当前操作尚未完成":"",
            run:()=>withCurrent(item.checkpoint_id,current=>invoke("onDelete",{item:current,store_revision:projection.store_revision}))}];
          const more=node(document,"button","⋯");more.type="button";more.disabled=busy||frozen;more.setAttribute?.("aria-label",`${item.name}的更多操作`);
          more.addEventListener("click",event=>{event.preventDefault?.();event.stopPropagation?.();commandMenu.open({target:more,trigger:more,returnFocus:more,label:"恢复点操作",items:menuItems()});});
          card.tabIndex=0;commandMenu.bind(card,{label:"恢复点操作",items:menuItems});controls.append(preview,restore,more);
        }else controls.append(preview,restore,rename,remove);
        card.append(controls);host.append(card);
      }
      if(projection.next_offset!==null){const more=node(document,"button","加载更多恢复点");more.type="button";more.disabled=busy||frozen;more.addEventListener("click",()=>invoke("onLoadMore",{offset:projection.next_offset,limit:projection.limit,store_revision:projection.store_revision}));host.append(more);}
    };
    return Object.freeze({
      update(value,documentId=null){const parsed=parseList(value,documentId);projection=parsed.ok?parsed.projection:null;createOpen=false;editing=null;render();return parsed;},
      append(value,documentId=null){const parsed=parseList(value,documentId||projection?.document_id||null);if(!parsed.ok||!projection||parsed.projection.offset!==projection.items.length||parsed.projection.document_id!==projection.document_id)return parsed.ok?reject("checkpoint_page_sequence_invalid"):parsed;const ids=new Set(projection.items.map(row=>row.checkpoint_id)),items=[...projection.items,...parsed.projection.items.filter(row=>!ids.has(row.checkpoint_id))];projection=freeze({...parsed.projection,offset:0,limit:Math.min(MAX_ITEMS,Math.max(projection.limit,items.length)),items:items.slice(0,MAX_ITEMS)});render();return freeze({ok:true,reason:null,projection});},
      setBusy(value){busy=value===true;render();},setFrozen(value,message=""){frozen=value===true;status=text(message,1000);render();},setStatus(message=""){status=text(message,1000);render();},
      clear(){projection=null;busy=false;frozen=false;status="";createOpen=false;editing=null;host.replaceChildren();},snapshot:()=>projection
    });
  }

  global.AgentCheckpoints=Object.freeze({LIST_CONTRACT,DIFF_CONTRACT,parseList,parseDiff,create});
})(window);
