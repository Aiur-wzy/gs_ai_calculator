(function(global){
  "use strict";

  const CONTRACT="genshin-agent-ui-projection-v2-foundation";
  const DRAFT_KEY="genshin.agent.question-drafts.v2";
  const MAX_ROWS=2048,MAX_BATCH=3,MAX_OPTIONS=32,MAX_TEXT=16000,MAX_MESSAGE=256*1024,MAX_SUMMARY=1000,MAX_DRAFTS=32,MAX_STORAGE_CHARS=256*1024;
  const ID=/^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$/u;
  const QUESTION_KINDS=new Set(["confirmation","single","multiple","text"]);
  const QUESTION_STATES=new Set(["open","answer_submitting","answered","cancelled","expired","orphaned","resuming","settled"]);
  const TERMINAL_QUESTIONS=new Set(["cancelled","expired","orphaned","settled"]);
  const INBOX_KINDS=new Set(["queue","steer","stop_and_send"]);
  const INBOX_STATES=new Set(["queued","cancelling","held_action_required","held_failure","needs_rebind","promoting","admitted","consumed","cancelled","failed","outcome_unknown"]);
  const TURN_STATES=new Set(["preparing","streaming","using_tool","awaiting_input_live","awaiting_input_suspended","resuming","cancelling","completed","cancelled","failed","interrupted"]);
  const SEGMENT_STATES=new Set(["preparing","streaming","using_tool","awaiting_input_live","suspended","cancelling","completed","cancelled","failed","interrupted"]);
  const EDITABLE_INBOX=new Set(["queued","held_action_required","held_failure","needs_rebind"]);
  const BUSY_INBOX=new Set(["cancelling","promoting"]);
  const freeze=value=>Object.freeze(value);
  const string=(value,limit=MAX_TEXT)=>typeof value==="string"?value.slice(0,limit):"";
  const optionalString=(value,limit)=>value===null||value===undefined?null:typeof value==="string"?value.slice(0,limit):null;
  const integer=value=>Number.isSafeInteger(value)&&value>=0?value:null;
  const identity=value=>typeof value==="string"&&ID.test(value)?value:null;
  const reject=reason=>freeze({ok:false,reason,projection:null});
  const node=(document,tag,value=null,className="")=>{const item=document.createElement(tag);if(className)item.className=className;if(value!==null)item.textContent=String(value);return item;};
  const randomId=prefix=>{const raw=global.crypto?.randomUUID?.().replaceAll("-","")||`${Date.now().toString(36)}${Math.random().toString(36).slice(2)}`;return `${prefix}-${raw}`.slice(0,120);};

  function plainRecord(value){return value&&typeof value==="object"&&!Array.isArray(value);}
  function safeShape(value,depth=0,budget={nodes:0}){
    if(++budget.nodes>50000||depth>12)return false;
    if(value===null||["string","number","boolean"].includes(typeof value))return true;
    if(Array.isArray(value))return value.length<=MAX_ROWS&&value.every(item=>safeShape(item,depth+1,budget));
    if(!plainRecord(value))return false;
    for(const [key,item] of Object.entries(value)){
      if(/(?:api.?key|authorization|credential|password|secret|token|raw_reasoning|(?:^|_)path$)/iu.test(key))return false;
      if(!safeShape(item,depth+1,budget))return false;
    }
    return true;
  }

  function parseOption(raw){
    if(!plainRecord(raw))return null;
    const valueId=identity(raw.value_id),label=string(raw.label,MAX_SUMMARY),description=optionalString(raw.description,MAX_SUMMARY);
    if(!valueId||!label||description===null&&raw.description!==null&&raw.description!==undefined)return null;
    return freeze({value_id:valueId,label,description});
  }

  function parseQuestionItem(raw){
    if(!plainRecord(raw))return null;
    const questionId=identity(raw.question_id),kind=string(raw.kind,32),prompt=string(raw.prompt),detail=optionalString(raw.detail,MAX_TEXT),header=optionalString(raw.header,MAX_SUMMARY),intent=raw.intent===null||raw.intent===undefined?null:identity(raw.intent);
    if(!questionId||!QUESTION_KINDS.has(kind)||!prompt||typeof raw.allow_skip!=="boolean"||detail===null&&raw.detail!==null&&raw.detail!==undefined||header===null&&raw.header!==null&&raw.header!==undefined||intent===null&&raw.intent!==null&&raw.intent!==undefined||!Array.isArray(raw.options)||raw.options.length>MAX_OPTIONS)return null;
    const options=raw.options.map(parseOption);if(options.some(item=>!item)||new Set(options.map(item=>item.value_id)).size!==options.length)return null;
    if(["confirmation","text"].includes(kind)&&options.length||["single","multiple"].includes(kind)&&!options.length)return null;
    return freeze({question_id:questionId,kind,prompt,detail,header,intent,options:freeze(options),allow_skip:raw.allow_skip});
  }

  function parseAnswer(raw,items){
    if(raw===null||raw===undefined)return null;
    if(!plainRecord(raw)||!Array.isArray(raw.items)||raw.items.length!==items.length)return false;
    const answers=[];
    for(let index=0;index<items.length;index++){
      const item=items[index],answer=raw.items[index];
      if(!plainRecord(answer)||answer.question_id!==item.question_id||typeof answer.skipped!=="boolean"||!Array.isArray(answer.value_ids)||answer.value_ids.length>MAX_OPTIONS)return false;
      const textValue=answer.text===null?null:string(answer.text),ids=answer.value_ids.map(identity);
      if(ids.some(value=>!value)||new Set(ids).size!==ids.length||answer.skipped&&(!item.allow_skip||textValue!==null||ids.length))return false;
      answers.push(freeze({question_id:item.question_id,text:textValue,value_ids:freeze(ids),skipped:answer.skipped}));
    }
    return freeze({items:freeze(answers)});
  }

  function normalizeLegacyQuestion(raw){
    const item=parseQuestionItem({question_id:raw.question_id,kind:raw.kind,prompt:raw.prompt,detail:null,header:null,intent:null,options:(raw.options||[]).map(option=>({...option,description:null})),allow_skip:false});
    if(!item)return null;
    const answer=raw.answer===null||raw.answer===undefined?null:parseAnswer({items:[{question_id:item.question_id,text:raw.answer.text??null,value_ids:raw.answer.value_ids||[],skipped:false}]},[item]);
    return {batch_id:item.question_id,items:[item],answer};
  }

  function parseQuestion(raw){
    if(!plainRecord(raw))return null;
    const batchId=identity(raw.batch_id||raw.question_id),turnId=identity(raw.turn_id),segmentId=identity(raw.segment_id),callId=raw.kind==="batch"?identity(raw.call_id):identity(raw.call_id)||`legacy:${batchId}`,state=string(raw.state,64),mode=string(raw.mode,32),revision=integer(raw.revision);
    if(!batchId||!turnId||!segmentId||!callId||!QUESTION_STATES.has(state)||!new Set(["live","suspended"]).has(mode)||revision===null)return null;
    let items,answer;
    if(raw.kind==="batch"){
      if(!Array.isArray(raw.items)||raw.items.length<1||raw.items.length>MAX_BATCH)return null;
      items=raw.items.map(parseQuestionItem);if(items.some(item=>!item)||new Set(items.map(item=>item.question_id)).size!==items.length)return null;
      answer=parseAnswer(raw.answer,items);if(answer===false)return null;
    }else{
      const legacy=normalizeLegacyQuestion(raw);if(!legacy)return null;items=legacy.items;answer=legacy.answer;
    }
    const receipt=raw.answer_receipt===null||raw.answer_receipt===undefined?null:identity(raw.answer_receipt);if(receipt===null&&raw.answer_receipt!==null&&raw.answer_receipt!==undefined)return null;
    return freeze({question_id:batchId,batch_id:batchId,turn_id:turnId,segment_id:segmentId,call_id:callId,kind:"batch",mode,state,items:freeze(items),answer,answer_receipt:receipt,revision,created_seq:integer(raw.created_seq),updated_seq:integer(raw.updated_seq)});
  }

  function parseBinding(raw){
    if(!plainRecord(raw))return null;
    const documentId=identity(raw.document_id),lineage=integer(raw.document_lineage_epoch),documentRevision=integer(raw.document_revision),candidateRevision=integer(raw.candidate_revision),stageRevision=integer(raw.stage_revision);
    if(!documentId||[lineage,documentRevision,candidateRevision,stageRevision].includes(null))return null;
    return freeze({document_id:documentId,document_lineage_epoch:lineage,document_revision:documentRevision,candidate_revision:candidateRevision,stage_revision:stageRevision});
  }

  function parseTarget(raw,required){
    if(raw===null&&!required)return null;if(!plainRecord(raw))return false;
    const turnId=identity(raw.turn_id),segmentId=identity(raw.segment_id),epochId=identity(raw.epoch_id),stageEpoch=integer(raw.stage_epoch),incarnation=identity(raw.runtime_incarnation);
    if(!turnId||!segmentId||!epochId||stageEpoch===null||!incarnation)return false;
    return freeze({turn_id:turnId,segment_id:segmentId,epoch_id:epochId,stage_epoch:stageEpoch,runtime_incarnation:incarnation});
  }

  function parseInboxItem(raw){
    if(!plainRecord(raw))return null;
    const id=identity(raw.inbox_id),kind=string(raw.kind,32),state=string(raw.state,64),revision=integer(raw.revision),message=string(raw.message,MAX_MESSAGE),requestId=identity(raw.client_request_id),binding=raw.document_binding===undefined?null:parseBinding(raw.document_binding),target=parseTarget(raw.target??null,kind!=="queue");
    if(!id||!INBOX_KINDS.has(kind)||!INBOX_STATES.has(state)||revision===null||!message||!requestId||raw.document_binding!==undefined&&!binding||target===false)return null;
    const receipt=raw.admission_receipt===null||raw.admission_receipt===undefined?null:identity(raw.admission_receipt),reason=raw.reason===null||raw.reason===undefined?null:identity(raw.reason);
    if(receipt===null&&raw.admission_receipt!==null&&raw.admission_receipt!==undefined||reason===null&&raw.reason!==null&&raw.reason!==undefined)return null;
    let runtimeAck=null;if(raw.runtime_ack!==null&&raw.runtime_ack!==undefined){const ack=raw.runtime_ack;if(!plainRecord(ack)||!identity(ack.status)||!identity(ack.message_id)||!identity(ack.dsh_message_id))return null;runtimeAck=freeze({status:ack.status,message_id:ack.message_id,dsh_message_id:ack.dsh_message_id});}
    return freeze({inbox_id:id,kind,state,revision,message,reason,client_request_id:requestId,document_binding:binding,target,admission_receipt:receipt,runtime_ack:runtimeAck,created_seq:integer(raw.created_seq),updated_seq:integer(raw.updated_seq)});
  }

  function parseTurn(raw){
    if(!plainRecord(raw))return null;const turnId=identity(raw.turn_id),state=string(raw.state,64);if(!turnId||!TURN_STATES.has(state))return null;
    return freeze({turn_id:turnId,state,predecessor_inbox_id:raw.predecessor_inbox_id===null||raw.predecessor_inbox_id===undefined?null:identity(raw.predecessor_inbox_id),created_seq:integer(raw.created_seq),updated_seq:integer(raw.updated_seq),terminal_seq:raw.terminal_seq===null||raw.terminal_seq===undefined?null:integer(raw.terminal_seq)});
  }

  function parseSegment(raw){
    if(!plainRecord(raw))return null;const turnId=identity(raw.turn_id),segmentId=identity(raw.segment_id),epochId=identity(raw.epoch_id),incarnation=identity(raw.runtime_incarnation),stageEpoch=integer(raw.stage_epoch),state=string(raw.state,64);if(!turnId||!segmentId||!epochId||!incarnation||stageEpoch===null||!SEGMENT_STATES.has(state))return null;
    return freeze({turn_id:turnId,segment_id:segmentId,epoch_id:epochId,runtime_incarnation:incarnation,stage_epoch:stageEpoch,state,created_seq:integer(raw.created_seq),updated_seq:integer(raw.updated_seq),terminal_seq:raw.terminal_seq===null||raw.terminal_seq===undefined?null:integer(raw.terminal_seq)});
  }

  function parseProjection(value,expectedSessionId=null){
    if(!plainRecord(value)||!safeShape(value)||value.contract!==CONTRACT)return reject("workbench_projection_invalid");
    const sessionId=identity(value.conversation_id),sourceCursor=integer(value.source_cursor),revision=integer(value.projection_revision);
    if(!sessionId||expectedSessionId&&sessionId!==expectedSessionId||sourceCursor===null||revision===null||!Array.isArray(value.turns)||value.turns.length>MAX_ROWS||!Array.isArray(value.segments)||value.segments.length>MAX_ROWS||!Array.isArray(value.questions)||value.questions.length>MAX_ROWS||!Array.isArray(value.inbox)||value.inbox.length>MAX_ROWS)return reject("workbench_projection_identity_invalid");
    const turns=value.turns.map(parseTurn),segments=value.segments.map(parseSegment),questions=value.questions.map(parseQuestion),inbox=value.inbox.map(parseInboxItem),documentBinding=value.document_binding===null||value.document_binding===undefined?null:parseBinding(value.document_binding);
    if(value.document_binding!==null&&value.document_binding!==undefined&&!documentBinding)return reject("workbench_document_binding_invalid");
    if(turns.some(item=>!item)||segments.some(item=>!item)||questions.some(item=>!item)||inbox.some(item=>!item)||new Set(turns.map(item=>item.turn_id)).size!==turns.length||new Set(segments.map(item=>item.segment_id)).size!==segments.length||new Set(questions.map(item=>item.batch_id)).size!==questions.length||new Set(inbox.map(item=>item.inbox_id)).size!==inbox.length)return reject("workbench_projection_rows_invalid");
    return freeze({ok:true,reason:null,projection:freeze({contract:CONTRACT,session_id:sessionId,source_cursor:sourceCursor,projection_revision:revision,document_binding:documentBinding,turns:freeze(turns),segments:freeze(segments),questions:freeze(questions),inbox:freeze(inbox)})});
  }

  function sanitizeDraft(raw){
    if(!plainRecord(raw))return {page:0,mutation_id:null,items:{},legacy:{text:"",value_ids:[]}};
    const items={};if(plainRecord(raw.items))for(const [id,value] of Object.entries(raw.items).slice(0,MAX_BATCH)){if(!identity(id)||!plainRecord(value))continue;items[id]={text:string(value.text),value_ids:Array.isArray(value.value_ids)?value.value_ids.map(identity).filter(Boolean).slice(0,MAX_OPTIONS):[],skipped:value.skipped===true};}
    return {page:Math.max(0,Math.min(MAX_BATCH-1,integer(raw.page)||0)),mutation_id:identity(raw.mutation_id),items,legacy:{text:string(raw.text),value_ids:Array.isArray(raw.value_ids)?raw.value_ids.map(identity).filter(Boolean).slice(0,MAX_OPTIONS):[]}};
  }

  function createDraftStore(storage){
    let state={version:2,drafts:{}},lastUpdated=0;
    try{const raw=JSON.parse(storage?.getItem?.(DRAFT_KEY)||"null");if(raw?.version===2&&plainRecord(raw.drafts))state={version:2,drafts:raw.drafts};}catch{}
    const persist=()=>{try{let encoded=JSON.stringify(state);if(encoded.length>MAX_STORAGE_CHARS){const rows=Object.entries(state.drafts).sort((a,b)=>(a[1].updated_at||0)-(b[1].updated_at||0));while(rows.length&&encoded.length>MAX_STORAGE_CHARS){const [key]=rows.shift();delete state.drafts[key];encoded=JSON.stringify(state);}}storage?.setItem?.(DRAFT_KEY,encoded);return true;}catch{return false;}};
    return freeze({get:key=>sanitizeDraft(state.drafts[key]),set:(key,value)=>{const clean=sanitizeDraft(value);lastUpdated=Math.max(Date.now(),lastUpdated+1);state.drafts[key]={page:clean.page,mutation_id:clean.mutation_id,items:clean.items,updated_at:lastUpdated};const keys=Object.keys(state.drafts).sort((a,b)=>(state.drafts[b].updated_at||0)-(state.drafts[a].updated_at||0));for(const old of keys.slice(MAX_DRAFTS))delete state.drafts[old];return persist();},delete:key=>{delete state.drafts[key];return persist();}});
  }

  function validateItemAnswer(item,draft){
    if(draft.skipped)return item.allow_skip?{question_id:item.question_id,text:null,value_ids:[],skipped:true}:null;
    const textValue=string(draft.text).trim()||null,ids=[...new Set((draft.value_ids||[]).filter(value=>item.options.some(option=>option.value_id===value)||item.kind==="confirmation"&&["confirm","cancel"].includes(value)))];
    if(item.kind==="text")return textValue?{question_id:item.question_id,text:textValue,value_ids:[],skipped:false}:null;
    if(item.kind==="confirmation")return textValue===null&&ids.length===1&&["confirm","cancel"].includes(ids[0])?{question_id:item.question_id,text:null,value_ids:ids,skipped:false}:null;
    if(item.kind==="single"&&ids.length>1)return null;if(!ids.length&&!textValue)return null;
    return {question_id:item.question_id,text:textValue,value_ids:ids,skipped:false};
  }

  function createQuestionController(document,host,options={}){
    if(!document||!host)throw new TypeError("question_host_invalid");const drafts=createDraftStore(options.storage);let sessionId=null,projection=null,minimized=false,busy=false,page=0;
    const currentQuestion=()=>projection?.questions.find(row=>!TERMINAL_QUESTIONS.has(row.state))||null,draftKey=question=>`${sessionId}:${question.batch_id}:${question.revision}`;
    const readDraft=question=>{const value=drafts.get(draftKey(question));if(!Object.keys(value.items).length&&question.items.length===1&&(value.legacy.text||value.legacy.value_ids.length))value.items[question.items[0].question_id]={...value.legacy,skipped:false};return value;};
    const saveDraft=(question,value)=>drafts.set(draftKey(question),value);
    const render=()=>{
      const question=currentQuestion();host.replaceChildren();if(!question){options.onTakeover?.(false,null);return;}
      const draft=readDraft(question);page=Math.min(question.items.length-1,Math.max(0,page||draft.page||0));const item=question.items[page],itemDraft=draft.items[item.question_id]||{text:"",value_ids:[],skipped:false};
      const card=node(document,"section",null,"ac-question-card");card.dataset.questionId=question.batch_id;card.dataset.questionItemId=item.question_id;card.dataset.state=question.state;card.append(node(document,"h4",item.header||`Agent 需要你的确认${question.items.length>1?`（${page+1}/${question.items.length}）`:""}`));
      if(minimized){const minimizedState=question.state==="resuming"?"Agent 正在继续执行":question.state==="answer_submitting"?"回答已安全保存":question.mode==="suspended"?"任务已安全暂停":"任务正在等待";card.append(node(document,"p",`${question.items.length} 个问题 · ${minimizedState}`,"ac-note"));const reopen=node(document,"button",question.state==="open"?"回答问题":"查看状态");reopen.type="button";reopen.addEventListener("click",()=>{minimized=false;render();});card.append(reopen);host.append(card);options.onTakeover?.(false,question);return;}
      card.append(node(document,"p",item.prompt,"ac-question-prompt"));if(item.detail)card.append(node(document,"p",item.detail,"ac-note"));const statusText=question.state==="resuming"?"回答已确认，Agent 正在新的执行段中继续；不会重复发送原始提示。":question.state==="answer_submitting"?"回答已由 Host 安全保存，正在准备恢复 Agent…":question.mode==="suspended"?"任务已安全暂停；关闭或重启 App 后仍可继续回答。":"Agent 正在等待此批回答。";const status=node(document,"p",statusText,"ac-note");card.append(status);
      let textInput=null;const selected=new Set(itemDraft.value_ids||[]),disabled=busy||question.state!=="open";
      if(item.kind==="text"){textInput=node(document,"textarea");textInput.value=itemDraft.text||"";textInput.maxLength=MAX_TEXT;textInput.disabled=disabled;textInput.setAttribute("aria-label",item.prompt);textInput.addEventListener("input",()=>{itemDraft.text=textInput.value;itemDraft.skipped=false;draft.items[item.question_id]=itemDraft;draft.page=page;saveDraft(question,draft);});card.append(textInput);}else{
        const group=node(document,"fieldset",null,"ac-question-options"),legend=node(document,"legend","选择回答");group.append(legend);const sourceOptions=item.kind==="confirmation"?[{value_id:"confirm",label:"确认",description:null},{value_id:"cancel",label:"取消",description:null}]:item.options;
        for(const option of sourceOptions){const label=node(document,"label",null,"ac-question-option"),control=node(document,"input");control.type=item.kind==="multiple"?"checkbox":"radio";control.name=`question-${question.batch_id}-${item.question_id}`;control.value=option.value_id;control.checked=selected.has(option.value_id);control.disabled=disabled;control.addEventListener("change",()=>{if(item.kind!=="multiple")selected.clear();if(control.checked)selected.add(option.value_id);else selected.delete(option.value_id);itemDraft.value_ids=[...selected];itemDraft.skipped=false;draft.items[item.question_id]=itemDraft;draft.page=page;saveDraft(question,draft);});label.append(control,node(document,"span",option.label));if(option.description)label.append(node(document,"small",option.description,"ac-question-option-description"));group.append(label);}card.append(group);
        if(["single","multiple"].includes(item.kind)){const customLabel=node(document,"label",null,"ac-question-custom");customLabel.append(node(document,"span","其他答案（可选）"));textInput=node(document,"textarea");textInput.value=itemDraft.text||"";textInput.maxLength=MAX_TEXT;textInput.disabled=disabled;textInput.addEventListener("input",()=>{itemDraft.text=textInput.value;itemDraft.skipped=false;draft.items[item.question_id]=itemDraft;draft.page=page;saveDraft(question,draft);});customLabel.append(textInput);card.append(customLabel);}}
      if(item.allow_skip){const skip=node(document,"label",null,"ac-question-skip"),control=node(document,"input");control.type="checkbox";control.checked=itemDraft.skipped===true;control.disabled=disabled;control.addEventListener("change",()=>{itemDraft.skipped=control.checked;if(control.checked){itemDraft.text="";itemDraft.value_ids=[];}draft.items[item.question_id]=itemDraft;draft.page=page;saveDraft(question,draft);render();});skip.append(control,node(document,"span","跳过此题"));card.append(skip);}
      const navigation=node(document,"div",null,"ac-question-navigation");if(page>0){const previous=node(document,"button","上一题");previous.type="button";previous.disabled=busy;previous.addEventListener("click",()=>{page--;draft.page=page;saveDraft(question,draft);render();});navigation.append(previous);}if(page<question.items.length-1){const next=node(document,"button","下一题");next.type="button";next.disabled=busy;next.addEventListener("click",()=>{page++;draft.page=page;saveDraft(question,draft);render();});navigation.append(next);}if(question.items.length>1)navigation.append(node(document,"span",`${page+1} / ${question.items.length}`,"ac-note"));card.append(navigation);
      const controls=node(document,"div",null,"ac-actions"),submit=node(document,"button",busy?"提交中…":"提交整批回答"),minimize=node(document,"button","稍后回答");submit.type=minimize.type="button";submit.disabled=disabled;minimize.disabled=busy;submit.addEventListener("click",async()=>{if(busy||question.state!=="open")return;const latest=readDraft(question),answers=question.items.map(row=>validateItemAnswer(row,latest.items[row.question_id]||{}));if(answers.some(row=>!row)){status.textContent="请完成所有问题，或对允许跳过的问题选择跳过。";return;}latest.mutation_id=latest.mutation_id||randomId("question-answer");saveDraft(question,latest);busy=true;render();let accepted=false;try{accepted=await options.onSubmit?.({session_id:sessionId,batch_id:question.batch_id,expected_revision:question.revision,projection_revision:projection.projection_revision,source_cursor:projection.source_cursor,mutation_id:latest.mutation_id,answer:{items:answers}});if(accepted!==false)drafts.delete(draftKey(question));}finally{if(accepted===false)busy=false;render();}});minimize.addEventListener("click",()=>{minimized=true;render();options.onMinimize?.(question.batch_id);});controls.append(submit,minimize);card.append(controls);host.append(card);options.onTakeover?.(question.state==="open",question);
    };
    return freeze({update:(raw,expected)=>{const parsed=parseProjection(raw,expected);if(!parsed.ok)return parsed;const old=currentQuestion()?.batch_id;projection=parsed.projection;sessionId=projection.session_id;const next=currentQuestion()?.batch_id;if(old!==next){page=0;minimized=false;}if(!next||currentQuestion()?.state!=="open")busy=false;render();return parsed;},clear:()=>{projection=null;sessionId=null;page=0;busy=false;minimized=false;host.replaceChildren();options.onTakeover?.(false,null);},render,setBusy:value=>{busy=value===true;render();},current:currentQuestion});
  }

  function createInboxDock(document,host,options={}){
    if(!document||!host)throw new TypeError("inbox_host_invalid");
    let projection=null,busyIds=new Set(),editingId=null,editingText="",menuStatus="";
    const commandMenu=options.contextMenu||global.AppContextMenu?.forDocument?.(document,global)||null;
    const label={queue:"排队",steer:"引导当前任务",stop_and_send:"停止后发送"},stateLabel={queued:"等待处理",cancelling:"正在停止当前任务",held_action_required:"等待先处理 App 操作",held_failure:"因失败暂缓",needs_rebind:"需重新绑定当前文档",promoting:"正在接纳",admitted:"已接纳",consumed:"已消费",cancelled:"已取消",failed:"失败",outcome_unknown:"结果未知"};
    const current=id=>projection?.inbox.find(item=>item.inbox_id===id&&EDITABLE_INBOX.has(item.state))||null;
    const unavailable=id=>busyIds.has(id)||BUSY_INBOX.has(projection?.inbox.find(item=>item.inbox_id===id)?.state);
    const rejectChanged=()=>{menuStatus="消息队列已经变化，请重新打开操作菜单。";render();return false;};
    const dispatch=(id,action,message=null)=>{const item=current(id);if(!item)return rejectChanged();if(unavailable(id)){menuStatus="这条消息正在处理，请等待当前操作完成。";render();return false;}menuStatus="";return options.onAction?.({action,item,...(message===null?{}:{message}),projection_revision:projection.projection_revision,source_cursor:projection.source_cursor});};
    const beginEdit=id=>{const item=current(id);if(!item)return rejectChanged();if(unavailable(id)){menuStatus="这条消息正在处理，请等待当前操作完成。";render();return false;}menuStatus="";editingId=id;editingText=item.message;render();return true;};
    const render=()=>{
      host.replaceChildren();const active=(projection?.inbox||[]).filter(item=>!new Set(["consumed","cancelled"]).has(item.state));
      host.append(node(document,"h4",`消息队列（${active.length}）`));if(menuStatus)host.append(node(document,"p",menuStatus,"ac-note"));
      if(!active.length){host.append(node(document,"p","没有等待处理的消息。","ac-note"));return;}
      for(const item of active){
        const card=node(document,"article",null,"ac-inbox-item");card.dataset.inboxId=item.inbox_id;card.dataset.state=item.state;card.append(node(document,"strong",label[item.kind]||item.kind));
        const editing=editingId===item.inbox_id;if(editing){const editor=node(document,"textarea",null,"ac-inbox-editor");editor.value=editingText;editor.maxLength=MAX_MESSAGE;editor.setAttribute("aria-label","修改排队消息");editor.addEventListener("input",()=>{editingText=editor.value;});card.append(editor);}else card.append(node(document,"p",item.message));
        card.append(node(document,"p",`${stateLabel[item.state]||item.state}${item.reason?` · ${item.reason}`:""}`,BUSY_INBOX.has(item.state)?"ac-note ac-running":"ac-note"));
        if(item.state==="outcome_unknown")card.append(node(document,"p","Host 无法证明外部接纳结果；不会自动重放此消息。","ac-warning"));
        if(item.state==="needs_rebind")card.append(node(document,"p","文档已恢复到新 lineage，晋升前必须重新核对此消息的文档上下文。","ac-warning"));
        const controls=node(document,"div",null,"ac-actions"),busy=unavailable(item.inbox_id);
        if(EDITABLE_INBOX.has(item.state)){
          if(editing){
            const save=node(document,"button","保存修改"),cancel=node(document,"button","取消");save.type=cancel.type="button";save.disabled=busy||!editingText.trim()||editingText.trim()===item.message;cancel.disabled=busy;
            save.addEventListener("click",async()=>{const message=editingText.trim(),id=item.inbox_id;if(!message||message===item.message)return;const accepted=await dispatch(id,"edit",message);if(accepted!==false){editingId=null;editingText="";render();}});
            cancel.addEventListener("click",()=>{editingId=null;editingText="";render();});controls.append(save,cancel);
          }else if(commandMenu){
            const menuItems=()=>[{id:`edit-${item.inbox_id}`,label:"编辑消息",enabled:!unavailable(item.inbox_id),disabledReason:"消息正在处理",run:()=>beginEdit(item.inbox_id)},
              {id:`move-up-${item.inbox_id}`,label:"上移",enabled:!unavailable(item.inbox_id),disabledReason:"消息正在处理",run:()=>dispatch(item.inbox_id,"up")},
              {id:`move-down-${item.inbox_id}`,label:"下移",enabled:!unavailable(item.inbox_id),disabledReason:"消息正在处理",run:()=>dispatch(item.inbox_id,"down")},
              {type:"separator"},{id:`delete-${item.inbox_id}`,label:"删除消息",danger:true,enabled:!unavailable(item.inbox_id),disabledReason:"消息正在处理",run:()=>dispatch(item.inbox_id,"delete")}];
            const promote=node(document,"button","立即发送"),more=node(document,"button","⋯");promote.type=more.type="button";promote.disabled=more.disabled=busy;more.setAttribute("aria-label","更多消息操作");
            promote.addEventListener("click",()=>dispatch(item.inbox_id,"promote"));more.addEventListener("click",event=>{event.preventDefault?.();event.stopPropagation?.();commandMenu.open({target:more,trigger:more,returnFocus:more,label:"消息操作",items:menuItems()});});
            card.tabIndex=0;commandMenu.bind(card,{label:"消息操作",items:menuItems});controls.append(promote,more);
          }else for(const [copy,action] of [["编辑","edit"],["删除","delete"],["上移","up"],["下移","down"],["立即发送","promote"]]){const button=node(document,"button",copy);button.type="button";button.disabled=busy;button.addEventListener("click",()=>action==="edit"?beginEdit(item.inbox_id):dispatch(item.inbox_id,action));controls.append(button);}
        }
        card.append(controls);host.append(card);
      }
    };
    return freeze({update:raw=>{const parsed=parseProjection(raw);if(!parsed.ok)return parsed;projection=parsed.projection;menuStatus="";if(editingId&&!projection.inbox.some(item=>item.inbox_id===editingId&&EDITABLE_INBOX.has(item.state))){editingId=null;editingText="";}render();return parsed;},clear:()=>{projection=null;busyIds.clear();editingId=null;editingText="";menuStatus="";render();},setBusy:(id,value)=>{if(value)busyIds.add(id);else busyIds.delete(id);render();},render,projection:()=>projection});
  }

  global.AgentQuestionInbox=freeze({CONTRACT,DRAFT_KEY,parseProjection,createDraftStore,createQuestionController,createInboxDock,validateItemAnswer});
})(window);
