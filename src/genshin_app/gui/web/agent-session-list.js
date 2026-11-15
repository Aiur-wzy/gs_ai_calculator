(function (global) {
  "use strict";

  const MAX_SESSIONS = 500;
  const MAX_TITLE = 256;
  const MAX_QUERY = 256;
  const MAX_RECENT_INPUT = 512;
  const STATES = new Set(["running", "action_required", "recovery_required", "failed", "completed", "idle", "cold", "archived"]);
  const LABELS = Object.freeze({running:"运行中",action_required:"需处理",recovery_required:"需恢复",failed:"失败",completed:"已完成",idle:"待命",cold:"冷会话",archived:"已归档"});

  const string = (value, limit) => typeof value === "string" ? value.slice(0, limit) : "";
  const integer = value => Number.isSafeInteger(value) && value >= 0 ? value : 0;
  const freeze = value => { if (value && typeof value === "object" && !Object.isFrozen(value)) { for (const child of Object.values(value)) freeze(child); Object.freeze(value); } return value; };

  function normalize(row) {
    if (!row || typeof row !== "object" || Array.isArray(row)) return null;
    const id = string(row.session_id || row.id, 256); if (!id) return null;
    const versionIncompatible = row.compatibility === "version_incompatible"
      && row.recovery_action === "agent_guided_current_file_compatibility";
    const state = STATES.has(row.conversation_state) ? row.conversation_state
      : row.active === true ? "running"
      : row.pending_application === true || row.pending_action === true ? "action_required"
      : row.can_resume === true ? "cold"
      : row.state === "error" ? "failed"
      : row.state === "completed" ? "completed" : "idle";
    const binding = row.document_binding && typeof row.document_binding === "object" && !Array.isArray(row.document_binding)
      && string(row.document_binding.document_id, 128)
      ? {
          document_id:string(row.document_binding.document_id, 128),
          document_revision:integer(row.document_binding.document_revision),
          document_lineage_epoch:integer(row.document_binding.document_lineage_epoch),
        }
      : null;
    return freeze({
      id, title:string(row.title, MAX_TITLE) || "未命名任务", state,
      updated_at:string(row.updated_at, 64), updated_ms:integer(Date.parse(row.updated_at)),
      unread:row.unread === true, archived:row.archived === true || state === "archived",
      can_resume:row.can_resume === true && !versionIncompatible, active:row.active === true, pending_action:row.pending_action === true,
      compatibility:versionIncompatible ? "version_incompatible" : null,
      recovery_action:versionIncompatible ? "agent_guided_current_file_compatibility" : null,
      read_only:row.read_only === true || versionIncompatible,
      model:string(row.model, 128), effort:string(row.effort || row.reasoning_effort, 32),
      recent_user_input:string(row.recent_user_input || row.recent_user_message, MAX_RECENT_INPUT),
      document_binding:binding,
      outline_revision:integer(row.outline_revision), metadata_revision:integer(row.metadata_revision),
      host_can_archive:typeof row.can_archive === "boolean" ? row.can_archive : null,
      host_can_forget:typeof row.can_forget === "boolean" ? row.can_forget : null,
      forget_blockers:Array.isArray(row.forget_blockers)?row.forget_blockers.filter(value=>typeof value==="string").slice(0,16):[],
    });
  }

  function dayBucket(row, now = Date.now()) {
    if (!row.updated_ms) return "older";
    const serial = value => { const date=new Date(value); return Date.UTC(date.getFullYear(),date.getMonth(),date.getDate()) / 86400000; };
    const delta = Math.max(0, serial(now) - serial(row.updated_ms));
    if (delta === 0) return "today";
    if (delta === 1) return "yesterday";
    return "older";
  }

  function project(rows, options = {}) {
    const query = string(options.query, MAX_QUERY).trim().toLocaleLowerCase(), now = Number.isFinite(options.now) ? options.now : Date.now();
    const viewed = string(options.viewed_session_id, 256), execution = string(options.execution_session_id, 256), owner = string(options.transaction_owner_id, 256), currentDocumentId=string(options.current_document_id,128);
    const unique = new Map();
    for (const raw of Array.isArray(rows) ? rows.slice(0, MAX_SESSIONS) : []) {
      const row = normalize(raw); if (!row || unique.has(row.id)) continue; unique.set(row.id, row);
    }
    const visible = [...unique.values()].filter(row => !query || `${row.title}\n${row.recent_user_input}\n${row.model}\n${row.document_binding?.document_id || ""}\n${LABELS[row.state] || row.state}`.toLocaleLowerCase().includes(query));
    visible.sort((a,b) => Number(b.active)-Number(a.active) || b.updated_ms-a.updated_ms || a.id.localeCompare(b.id));
    const groups = {today:[],yesterday:[],older:[]};
    for (const row of visible) groups[dayBucket(row, now)].push(freeze({...row, viewed:row.id === viewed, execution_owner:row.id === execution, transaction_owner:row.id === owner,
      document_detached:!!(currentDocumentId && row.document_binding && row.document_binding.document_id !== currentDocumentId),
      can_archive:row.archived || (row.host_can_archive !== false && row.id !== execution && row.id !== owner && !row.active),
      can_forget:row.host_can_forget !== false && row.id !== execution && row.id !== owner && !row.active && !row.pending_action}));
    return freeze({query, viewed_session_id:viewed || null, execution_session_id:execution || null, transaction_owner_id:owner || null,
      groups, count:visible.length, total:unique.size, partial:Array.isArray(rows) && rows.length > MAX_SESSIONS});
  }

  function node(document, tag, value = null, className = "") { const row=document.createElement(tag); if(className)row.className=className; if(value!=null)row.textContent=String(value); return row; }

  function create(document, host, callbacks = {}) {
    if (!document || !host) throw new TypeError("agent_session_list_host_invalid");
    const contextMenu=callbacks.contextMenu || global.AppContextMenu?.forDocument?.(document, global);
    if(!contextMenu)throw new TypeError("agent_session_context_menu_missing");
    const search=node(document,"input"); search.type="search"; search.placeholder="搜索会话"; search.setAttribute("aria-label","搜索本机会话");
    const summary=node(document,"p",null,"ac-session-summary"), list=node(document,"div",null,"ac-session-list");
    host.replaceChildren(search,summary,list); let rows=[],options={},view=null,editingId=null,editingText="";

    const lifecycle=()=>options.session_lifecycle_enabled!==false;
    const startRename=row=>{editingId=row.id;editingText=row.title;render();};
    const disabledReason=(row,action)=>{
      if(!lifecycle())return "当前 App 未开放会话管理";
      if(action==="resume")return row.compatibility==="version_incompatible"?"这份会话使用旧数据库版本，只能只读查看":view?.execution_session_id&&view.execution_session_id!==row.id?"请先完成正在运行的会话":row.archived?"请先取消归档":row.active?"会话正在运行":row.can_resume?"":"这份会话当前不需要恢复";
      if(action==="archive")return row.execution_owner?"运行中的会话不能归档":row.transaction_owner?"请先处理待完成的方案应用":row.can_archive?"":"App 暂不允许归档";
      if(action==="forget"){
        if(row.execution_owner||row.active||row.forget_blockers.includes("active_turn"))return "运行中的会话不能删除";
        if(row.transaction_owner||row.forget_blockers.includes("candidate_application_transaction"))return "请先处理待完成的方案应用";
        if(row.pending_action||row.forget_blockers.includes("app_action"))return "请先处理会话中的待办操作";
        if(row.forget_blockers.includes("document_restore"))return "请先完成或终止文档恢复";
        if(row.forget_blockers.includes("service_busy"))return "Agent 服务正在处理其他操作";
        return row.can_forget?"":"App 暂不允许删除";
      }
      return "";
    };
    const menuItems=row=>[
      {id:"open",label:row.viewed?"查看当前会话":"打开会话",enabled:typeof callbacks.onSelect==="function",run:()=>callbacks.onSelect?.(row.id)},
      {id:"resume",label:"恢复会话",enabled:lifecycle()&&row.can_resume&&!row.active&&!row.archived&&!view?.execution_session_id&&typeof callbacks.onResume==="function",disabledReason:disabledReason(row,"resume"),run:()=>callbacks.onResume?.(row.id)},
      {id:"rename",label:"重命名",enabled:lifecycle()&&typeof callbacks.onRename==="function",disabledReason:!lifecycle()?disabledReason(row,"rename"):"",run:()=>startRename(row)},
      {id:"archive",label:row.archived?"取消归档":"归档",enabled:lifecycle()&&row.can_archive&&typeof callbacks.onArchive==="function",disabledReason:disabledReason(row,"archive"),run:()=>callbacks.onArchive?.(row.id,!row.archived,row.metadata_revision)},
      null,
      {id:"forget",label:"删除本地会话记录",danger:true,enabled:lifecycle()&&row.can_forget&&typeof callbacks.onForget==="function",disabledReason:disabledReason(row,"forget"),run:()=>callbacks.onForget?.(row.id,row.metadata_revision)},
    ];

    const render=()=>{
      contextMenu.close();
      view=project(rows,{...options,query:search.value}); summary.textContent=view.partial?`显示 ${view.count} 项（会话列表已截断）`:`${view.count} 个会话`;
      const children=[]; let editorFocus=null;
      for(const [key,label] of [["today","今天"],["yesterday","昨天"],["older","更早"]]){
        const values=view.groups[key]; if(!values.length)continue; const section=node(document,"section",null,"ac-session-group"); section.append(node(document,"h4",label));
        for(const row of values){
          const card=node(document,"article",null,"ac-session-row"); card.dataset.sessionId=row.id; if(row.viewed)card.dataset.viewed="true"; if(row.execution_owner)card.dataset.executionOwner="true";
          const open=node(document,"button",null,"ac-session-open"); open.type="button"; open.setAttribute("aria-current",row.viewed?"true":"false"); open.setAttribute("aria-haspopup","menu"); open.addEventListener("click",()=>callbacks.onSelect?.(row.id));
          const title=node(document,"strong",row.title), meta=node(document,"span",`${LABELS[row.state]||row.state}${row.unread?" · 未读":""}${row.execution_owner?" · 正在执行":""}${row.transaction_owner?" · 有待完成的方案应用":""}${row.compatibility==="version_incompatible"?" · 版本不兼容":""}`); open.append(title,meta); card.append(open);
          if(row.recent_user_input)open.append(node(document,"small",row.recent_user_input,"ac-session-recent-input"));
          if(row.document_binding){const binding=node(document,"small",`${row.document_detached?"已脱离当前文档 · ":"绑定文档 · "}${row.document_binding.document_id} · 恢复版本 ${row.document_binding.document_lineage_epoch} / 文档版本 ${row.document_binding.document_revision}`,"ac-session-document-binding");binding.title=`技术信息：lineage ${row.document_binding.document_lineage_epoch} / revision ${row.document_binding.document_revision}`;open.append(binding);}

          const more=node(document,"button","⋯","ac-session-more"); more.type="button"; more.setAttribute("aria-label",`${row.title}的更多操作`); more.setAttribute("aria-haspopup","menu"); more.setAttribute("aria-expanded","false");
          more.addEventListener("click",event=>{event.preventDefault?.();event.stopPropagation?.();if(contextMenu.activeTarget()===card){contextMenu.close({restoreFocus:true});return;}contextMenu.open({target:card,trigger:more,returnFocus:more,items:menuItems(row),label:`${row.title}的操作`});});
          card.append(more);
          contextMenu.bind(card,{items:()=>menuItems(row),label:()=>`${row.title}的操作`,trigger:(event,keyboard)=>keyboard?open:card,returnFocus:()=>open,onError:error=>callbacks.onError?.(error,row.id)});

          if(row.compatibility==="version_incompatible"){
            card.dataset.versionIncompatible="true";
            const guidance=node(document,"details",null,"ac-session-compatibility");
            guidance.append(node(document,"summary","版本不兼容 · 只读查看"),node(document,"p","该会话绑定的是旧数据库版本，不能在当前版本继续计算。你可以复制下面的提示词，手动交给当前 Agent 检查当前文件；App 不会自动迁移、填入或发送。"));
            const prompt=string(callbacks.compatibilityPrompt,8192);
            if(prompt){
              guidance.append(node(document,"pre",prompt,"ac-session-compatibility-prompt"));
              const copy=node(document,"button","复制 Agent 提示词");copy.type="button";
              copy.addEventListener("click",async event=>{event.preventDefault?.();event.stopPropagation?.();await callbacks.onCopyCompatibilityPrompt?.(row.id);});
              guidance.append(copy);
            }
            card.append(guidance);
          }

          if(editingId===row.id&&lifecycle()&&typeof callbacks.onRename==="function"){
            const editor=node(document,"div",null,"ac-session-rename-editor"),input=node(document,"input");input.type="text";input.value=editingText;input.maxLength=MAX_TITLE;input.setAttribute("aria-label","新的会话名称");input.addEventListener("input",()=>editingText=input.value);
            const save=node(document,"button","保存"),cancel=node(document,"button","取消");save.type=cancel.type="button";
            const cancelEdit=()=>{editingId=null;editingText="";render();};
            const commit=async()=>{const value=input.value.trim();if(!value)return;const accepted=await callbacks.onRename(row.id,value,row.metadata_revision);if(accepted!==false)cancelEdit();};
            save.addEventListener("click",commit);cancel.addEventListener("click",cancelEdit);input.addEventListener("keydown",event=>{if(event.key==="Escape")cancelEdit();if(event.key==="Enter")void commit();});editor.append(input,save,cancel);card.append(editor);editorFocus=input;
          }
          section.append(card);
        }
        children.push(section);
      }
      list.replaceChildren(...children); editorFocus?.focus?.(); callbacks.onRender?.(view);
    };
    search.addEventListener("input",render);
    return Object.freeze({update(nextRows,nextOptions={}){rows=Array.isArray(nextRows)?nextRows:[];options={...nextOptions};if(editingId&&!rows.some(row=>(row?.session_id||row?.id)===editingId)){editingId=null;editingText="";}render();},focusSearch(){search.focus?.();},getView:()=>view,search,list,summary,closeMenu:()=>contextMenu.close()});
  }

  global.AgentSessionList = Object.freeze({project, create, MAX_SESSIONS});
})(window);
