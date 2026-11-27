/** User-invoked CLI acceptance over the real DSH client and App controls. */
(function (global) {
  "use strict";
  const CONTRACT = "gui-native-agent-webview-smoke-receipt-v1";
  const sleep = milliseconds => new Promise(resolve => global.setTimeout(resolve, milliseconds));
  const text = (value, limit = 512) => String(value == null ? "" : value).slice(0, limit);
  const clone = value => JSON.parse(JSON.stringify(value));
  let state = Object.freeze({status:"idle",contract:CONTRACT}), running = null;
  let phase = "idle", progress = {}, currentSessionId = null;
  class SmokeError extends Error { constructor(code,message){super(message);this.code=code;} }
  function fail(code,message){throw new SmokeError(code,message);}
  function validateConfig(value) {
    if (!value || typeof value !== "object" || Array.isArray(value)) fail("webview_smoke_config_invalid", "WebView smoke配置无效。");
    const config = {
      prompt:text(value.prompt, 200000).trim(),
      inputPath:value.input_path == null ? null : text(value.input_path, 32768),
      boardPath:text(value.board_path, 32768),
      calculatedPath:text(value.calculated_path, 32768),
      workflow:text(value.workflow || "artifact", 32),
      model:value.model == null ? null : text(value.model, 256),
      effort:value.effort == null ? null : text(value.effort, 64),
      allowDraft:value.allow_draft === true,
      timeoutMs:Number(value.timeout_ms),
      providerMode:text(value.provider_mode || "saved_credential", 64),
      expectation:value.expectation && typeof value.expectation === "object" && !Array.isArray(value.expectation)
        ? clone(value.expectation) : {},
    };
    if (!config.prompt || !config.boardPath || !config.calculatedPath) fail("webview_smoke_config_invalid", "WebView smoke缺少任务或输出路径。");
    if (!["artifact", "build", "calculate", "staged"].includes(config.workflow)) fail("webview_smoke_config_invalid", "WebView smoke工作流无效。");
    if (!Number.isSafeInteger(config.timeoutMs) || config.timeoutMs < 1000 || config.timeoutMs > 24 * 60 * 60 * 1000) fail("webview_smoke_config_invalid", "WebView smoke时限无效。");
    if (!["saved_credential", "deterministic_provider_transport"].includes(config.providerMode)) fail("webview_smoke_config_invalid", "WebView smoke提供方模式无效。");
    const allowedExpectation = new Set(["title_contains","required_character_ids","required_action_type_ids","min_actions","min_revision_increment","require_calculated","min_result_hits"]);
    if (Object.keys(config.expectation).some(key => !allowedExpectation.has(key))) fail("webview_smoke_config_invalid", "WebView smoke语义验收包含未知字段。");
    for (const key of ["required_character_ids","required_action_type_ids"]) if (config.expectation[key] != null && !Array.isArray(config.expectation[key])) fail("webview_smoke_config_invalid", "WebView smoke语义验收列表无效。");
    return Object.freeze(config);
  }

  function verifySemanticExpectation(config, baseline, board, calculated) {
    const rule = config.expectation || {};
    const failSemantic = message => fail("webview_smoke_semantic_expectation_failed", message);
    if (rule.title_contains && !String(board.title || "").includes(rule.title_contains)) failSemantic("白板标题没有包含验收题面标记。");
    const characterIds = new Set((board.team || []).map(row => row?.db_character_id));
    for (const id of rule.required_character_ids || []) if (!characterIds.has(id)) failSemantic(`白板缺少题面角色 ${id}。`);
    if (Number.isInteger(rule.min_actions) && (board.actions || []).length < rule.min_actions) failSemantic("白板动作数低于验收要求。");
    const actionTypes = new Set((board.actions || []).map(row => row?.action_type_id));
    for (const id of rule.required_action_type_ids || []) if (!actionTypes.has(id)) failSemantic(`白板缺少题面动作类型 ${id}。`);
    if (Number.isInteger(rule.min_revision_increment) && Number(board.revision) < Number(baseline.revision) + rule.min_revision_increment) failSemantic("候选没有产生要求的白板修订增量。");
    if (rule.require_calculated === true && !calculated) failSemantic("题面要求正式计算结果，但本轮只得到草稿。");
    if (Number.isInteger(rule.min_result_hits)) {
      const hits = calculated?.result?.hits;
      if (!Array.isArray(hits) || hits.length < rule.min_result_hits) failSemantic("计算结果的伤害事件数低于验收要求。");
    }
    return true;
  }

  function receiptIdentity(value) {
    if (!value || typeof value !== "object" || Array.isArray(value)) return null;
    const result = {};
    for (const key of ["plan_id", "revision", "input_fingerprint", "database_fingerprint", "database_snapshot_fingerprint"])
      if (["string", "number"].includes(typeof value[key])) result[key] = value[key];
    return result;
  }


  async function waitFor(predicate,deadline,code,message) {
    while(Date.now()<=deadline){const value=await predicate();if(value)return value;await sleep(80);}
    fail(code,message);
  }
  function visible(node){return !!node&&!node.closest("[hidden]")&&!node.disabled;}
  function button(label,scope=global.document){return [...scope.querySelectorAll("button")].find(node=>visible(node)&&(label instanceof RegExp?label.test(node.textContent.trim()):node.textContent.trim()===label));}
  async function click(label,deadline,scope=global.document){const node=await waitFor(()=>button(label,scope),deadline,"webview_smoke_control_unavailable",`没有可用的用户操作：${label}`);node.click();return node;}
  async function api(method,...args){const value=await global.pywebview.api[method](...args);if(value&&typeof value.code==="number"){if(value.code!==0)fail(text(value.reason||"webview_smoke_app_failure"),"App未接纳本次验收操作。");return value.data;}return value;}
  async function sdk(endpoint,args){const result=await api("dsh_client_rpc",{request_id:global.crypto.randomUUID(),channel:"/api",endpoint,payload:{args}});if(result?.ok!==true)fail(text(result?.error?.code||"webview_smoke_rpc_failed"),"DSH只读资料查询失败。");return result.value;}
  async function menu(command,deadline,group="文件"){
    // Loading a document also refreshes the menubar. Reopen this harmless
    // presentation surface when that refresh closes it; never retry the command.
    const item=await waitFor(()=>{
      const current=global.document.querySelector(`.workspace-menu-popup [data-command="${command}"]`);
      if(current)return current;
      button(group,global.document.getElementById("workspace-menubar"))?.click();
      return global.document.querySelector(`.workspace-menu-popup [data-command="${command}"]`);
    },deadline,"webview_smoke_file_control_missing",`文件操作不可用：${command}`);
    if(item.disabled)fail("webview_smoke_file_control_disabled",`当前文档不允许文件操作：${command}`);
    item.click();
  }
  async function boardPage(deadline){await click(/排轴白板/,deadline,global.document.getElementById("dsh-app-root"));await waitFor(()=>!global.document.getElementById("genshin-legacy-editor")?.hidden,deadline,"webview_smoke_editor_not_visible","正式白板页面没有显示。");}
  async function openFile(path,deadline){
    const previous=global.WB.state.board;
    await boardPage(deadline);await menu("open",deadline);
    await waitFor(()=>global.WB.state.path===path&&global.WB.state.board!==previous,deadline,"webview_smoke_document_open_failed","用户文件打开未完成。");
  }
  async function officialChat(deadline){
    const workbench=await waitFor(()=>global.document.querySelector(".gs-editor-workbench"),deadline,"webview_smoke_editor_not_visible","没有显示正式白板工作台。");
    if(!workbench.querySelector(".gs-editor-assistant"))await click("打开 AI 小窗",deadline,workbench);
    const embedded=await waitFor(()=>workbench.querySelector(".gs-editor-assistant"),deadline,"webview_smoke_assistant_missing","打开 AI 小窗后没有显示官方聊天入口。");
    await click("完整页面",deadline,embedded);
    await waitFor(()=>global.document.querySelector("[data-conversation-content]")&&!global.document.querySelector(".gs-editor-assistant"),deadline,"webview_smoke_chat_not_visible","没有显示完整聊天页面。");
    const picker=[...global.document.querySelectorAll("button[aria-label]")].find(row=>visible(row)&&/^(Choose workspace|选择工作区)$/.test(row.getAttribute("aria-label")));
    if(picker){picker.click();const workspace=await waitFor(()=>[...global.document.querySelectorAll('[role="menuitem"],[role="option"]')].find(row=>row.textContent.includes("原神排轴")),deadline,"webview_smoke_workspace_missing","没有显示App固定工作区。");workspace.click();}
    const existing=await waitFor(()=>global.document.querySelector("[data-conversation-content]")?.dataset.conversationSession,deadline,"webview_smoke_session_missing","官方界面没有选择会话。");
    // Selecting the fixed Workspace already opens a blank official entry. Do
    // not race a second New Session against that entry's pending mount.
    const selected=await waitFor(async()=>{
      const listing=await sdk("session/list",{_request:{}});
      return listing?.items?.find(row=>row.sessionId===existing);
    },deadline,"webview_smoke_session_missing","当前官方会话尚未进入公开列表。");
    if(selected.blank!==true){
      await click(/^(新会话|New Session)$/,deadline);
      await waitFor(()=>{const sid=global.document.querySelector("[data-conversation-content]")?.dataset.conversationSession;return sid&&sid!==existing;},deadline,"webview_smoke_session_missing","官方新会话没有切换。");
    }
    currentSessionId=await waitFor(()=>global.document.querySelector("[data-conversation-content]")?.dataset.conversationSession,deadline,"webview_smoke_session_missing","官方新会话没有显示。");
    return currentSessionId;
  }
  async function modelChoice(config,deadline){
    if(!config.model&&!config.effort)return {model:null,effort:null};
    const catalog=await sdk("session/modelCatalog",{});
    const model=config.model?(catalog.groups||[]).flatMap(group=>group.models).find(row=>row.id===config.model):null;
    if(config.model&&!model)fail("webview_smoke_model_unavailable","指定模型不在官方可用目录中。");
    const trigger=()=>[...global.document.querySelectorAll("button[aria-label]")].find(row=>visible(row)&&/^(选择模型|请选择模型|Select model)/.test(row.getAttribute("aria-label")));
    async function open(){const node=await waitFor(trigger,deadline,"webview_smoke_model_control_missing","没有显示官方模型选择器。");node.click();return waitFor(()=>global.document.querySelector('[role="menu"][aria-label="模型与推理等级"],[role="menu"][aria-label="Model and reasoning effort"]'),deadline,"webview_smoke_model_menu_missing","模型菜单没有显示。");}
    if(model){phase="model_menu";let surface=await open();await click(/^(模型|Model)/,deadline,surface);const option=await waitFor(()=>[...global.document.querySelectorAll('[role="menuitemradio"]')].find(row=>visible(row)&&(row.textContent.includes(model.name)||row.textContent.includes(model.id))),deadline,"webview_smoke_model_unavailable","模型没有可选择的官方选项。");option.click();await sleep(100);}
    if(config.effort){phase="effort_menu";const surface=await open();await click(/^(推理等级|Reasoning effort)/,deadline,surface);const aliases={off:["off","关闭"],low:["low","低"],high:["high","高"],max:["max","最高"]};const labels=aliases[config.effort]||[config.effort.toLowerCase()];const option=await waitFor(()=>[...global.document.querySelectorAll('[role="menuitemradio"]')].find(row=>visible(row)&&labels.includes(row.textContent.trim().toLowerCase())),deadline,"webview_smoke_effort_unavailable","该模型没有指定推理档位。");option.click();await sleep(100);}
    return {model:config.model,effort:config.effort};
  }
  async function sendPrompt(prompt,deadline){
    const input=await waitFor(()=>[...global.document.querySelectorAll("[contenteditable]")].find(visible),deadline,"webview_smoke_composer_missing","没有显示官方输入框。");input.focus();
    const range=global.document.createRange();range.selectNodeContents(input);range.collapse(false);global.getSelection().removeAllRanges();global.getSelection().addRange(range);global.document.dispatchEvent(new global.Event("selectionchange"));await sleep(30);
    const paste=new global.Event("paste",{bubbles:true,cancelable:true});Object.defineProperty(paste,"clipboardData",{value:{types:["text/plain"],items:[],files:[],getData:type=>type==="text/plain"?prompt:""}});input.dispatchEvent(paste);await sleep(30);input.dispatchEvent(new global.KeyboardEvent("keydown",{key:"Enter",code:"Enter",bubbles:true,cancelable:true}));
    await waitFor(()=>global.document.getElementById("dsh-app-root").textContent.includes(prompt),deadline,"webview_smoke_user_message_missing","已发送的用户消息没有显示。");
  }
  async function waitTurn(deadline,previousTurn){
    while(Date.now()<=deadline){
      const status=await api("dsh_client_session_status",{dsh_session_id:currentSessionId});
      const projections=await sdk("session/projections",{request:{sessionId:currentSessionId}});
      const pending=projections?.values?.userQuestions?.active||[];
      if(pending.length||button(/^(允许一次|Allow once)$/))return {awaitingUser:true,questionCount:Math.max(1,pending.length)};
      progress.turn_wait={agent_running:status.agent_running,physical_admission:status.admission?.state||"unknown",last_turn:status.last_turn?.turn??null};
      if(status.last_turn&&status.last_turn.turn>previousTurn&&status.agent_running===false){
        if(status.last_turn.reason?.kind!=="completed")fail("webview_smoke_agent_failed",`模型轮未正常完成：${text(status.last_turn.reason?.kind)}`);
        return {awaitingUser:false};
      }
      await sleep(250);
    }
    fail("webview_smoke_turn_timeout","模型尚未完成本次用户指定的验收时限。");
  }
  async function applyCandidate(config,deadline){
    const info=await api("dsh_client_domain_binding",{dsh_session_id:currentSessionId});
    const conversationId=info?.binding?.conversation_id;if(!conversationId)fail("webview_smoke_binding_missing","当前会话没有准确的白板绑定。");
    const candidate=await api("dsh_client_candidate",{conversation_id:conversationId});
    if(candidate.kind!=="whiteboard"||!candidate.receipt_fingerprint)fail("webview_smoke_candidate_not_ready","模型轮已结束，但候选尚未完成可审核接纳。");
    const draft=candidate.has_calculation!==true;if(draft&&!config.allowDraft)fail("webview_smoke_draft_not_publishable","本轮只有草稿，本次要求完整计算。");
    await click("审核候选",deadline);
    await waitFor(()=>button("同意并同步"),deadline,"webview_smoke_review_not_rendered","没有显示真正的审核确认窗口。");await click("同意并同步",deadline);
    await waitFor(()=>global.WB.state.board&&(!draft?global.WB.state.result:true)&&global.WS.tab()==="whiteboard"&&!global.document.getElementById("genshin-legacy-editor")?.hidden,deadline,"webview_smoke_candidate_apply_timeout","审核后的正式白板未显示。");
    if(!draft){await click("计算结果另存为…",deadline);await waitFor(()=>global.document.body.textContent.includes(config.calculatedPath),deadline,"webview_smoke_calculated_save_failed","实际结果另存为没有返回所选目标路径。");}
    return {candidate,draft,revision:candidate.revision,receipt:candidate.receipt_fingerprint};
  }
  async function saveReload(config,application,baseline,deadline){
    phase="whiteboard_save_as";await menu("save-as",deadline);
    await waitFor(()=>global.WB.state.path===config.boardPath,deadline,"webview_smoke_board_save_failed","真实白板保存未完成。");
    const before=clone(global.WB.state.board),boardSha=global.WB.state.fileSha;
    phase="whiteboard_reopen";await openFile(config.boardPath,deadline);if(JSON.stringify(global.WB.state.board)!==JSON.stringify(before))fail("webview_smoke_board_reload_mismatch","重新加载后的白板与审核白板不同。");
    let calculation=null;
    if(!application.draft){phase="calculated_reimport";await menu("return",deadline,"计算与交接");
      await waitFor(()=>global.WB.state.result&&global.WB.state.mode==="damage",deadline,"webview_smoke_result_not_displayed","计算结果没有实际显示在白板中。");
      calculation=await api("whiteboard_read_result",clone(global.WB.state.board),config.calculatedPath);
    }
    const root=global.document.getElementById("tab-whiteboard");if(!root||root.hidden)fail("webview_smoke_result_not_displayed","白板DOM没有显示。");
    verifySemanticExpectation(config,baseline,global.WB.state.board,calculation?.calculated||null);
    return {status:application.draft?"draft_verified":"verified",board:{path:config.boardPath,file_sha:boardSha,board_id:before.board_id},calculated:calculation?{path:calculation.path,file_sha:calculation.file_sha,result_receipt_identity:receiptIdentity(calculation.calculated.result_receipt)}:null};
  }
  async function run(raw){
    const config=validateConfig(raw),deadline=Date.now()+config.timeoutMs;phase="gui_bootstrap";
    await waitFor(()=>global.GenshinDshClient&&global.GenshinEditorBridge&&global.WB,deadline,"webview_smoke_app_not_ready","生产DSH界面没有完成初始化。");await global.GenshinDshClient.ready;
    if(config.inputPath)await openFile(config.inputPath,deadline);else{await boardPage(deadline);await menu("new-board",deadline);await waitFor(()=>global.WB.state.board,deadline,"webview_smoke_document_open_failed","新建白板没有完成。");}
    const baseline=clone(global.WB.state.board);phase="official_composer";await officialChat(deadline);const selected=await modelChoice(config,deadline);
    const before=await api("dsh_client_session_status",{dsh_session_id:currentSessionId});await sendPrompt(config.prompt,deadline);progress.gui_user_controls="verified";phase="model_turn";
    const turn=await waitTurn(deadline,before.last_turn?.turn||0);
    if(turn.awaitingUser)return {contract:CONTRACT,status:"awaiting_user_answer",session_id:currentSessionId,provider_mode:config.providerMode,pending_question_count:turn.questionCount,real_webview_dom_observed:true,real_webview_dom_verified:false,files_published:false,reload_verified:false,display_verified:false,verification_layers:{backend_host_chain:"suspended_user_input",real_webview_dom:"observed_before_user_input"}};
    progress.backend_host_chain="verified";phase="candidate_review_application";const application=await applyCandidate(config,deadline);progress.candidate_review_application="verified";phase="save_reload";const files=await saveReload(config,application,baseline,deadline);phase="complete";
    return {contract:CONTRACT,...files,session_id:currentSessionId,provider_mode:config.providerMode,workflow:config.workflow,...selected,revision:application.revision,receipt_fingerprint:application.receipt,candidate_review_rendered:true,candidate_application_kind:application.draft?"whiteboard_draft":"whiteboard_calculated",files_published:!application.draft,reload_verified:true,display_verified:true,semantic_expectations_verified:Object.keys(config.expectation).length>0,semantic_expectation_status:Object.keys(config.expectation).length?"verified":"not_requested",real_webview_dom_verified:true,verification_layers:{gui_user_controls:"verified",backend_host_chain:"verified",candidate_review_application:"verified",file_hash_schema_identity:"verified",webview_reload:"verified",real_webview_dom:"verified"},dom:{bundle:"production_index",candidate_review:true,whiteboard_summary:true,damage_view:!application.draft}};
  }
  function start(config){if(running)return false;progress={};currentSessionId=null;state=Object.freeze({contract:CONTRACT,status:"running",real_webview_dom_verified:false});running=Promise.resolve().then(()=>run(config)).then(value=>{state=Object.freeze(value);return state;},error=>{state=Object.freeze({contract:CONTRACT,status:"failed",error_code:text(error.code||"webview_smoke_failed",128),error_message:text(error.message,1000),failure_stage:phase,session_id:currentSessionId,real_webview_dom_verified:false,files_published:false,reload_verified:false,display_verified:false,verification_layers:{real_webview_dom:"not_verified"}});return state;});return true;}
  global.GenshinWebviewSmoke=Object.freeze({CONTRACT,start,snapshot:()=>clone(state.status==="running"?{...state,phase,progress,...(currentSessionId?{session_id:currentSessionId}:{})}:state)});
})(window);
