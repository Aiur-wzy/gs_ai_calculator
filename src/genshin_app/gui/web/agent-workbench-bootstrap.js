(function (global) {
  "use strict";

  // Final composition root for the workbench.  It is intentionally the only
  // module allowed to know how the independently tested session, turn,
  // composer and shell controllers are wired together.  It owns no Host fact
  // and performs no request by itself.
  const requiredObject = (value, name) => {
    if (!value || typeof value !== "object") throw new TypeError(`workbench_bootstrap_${name}_missing`);
    return value;
  };
  const requiredFunction = (value, name) => {
    if (typeof value !== "function") throw new TypeError(`workbench_bootstrap_${name}_missing`);
    return value;
  };

  function createSessionController(options = {}) {
    const agent=requiredObject(options.agent,"agent"),windowObject=requiredObject(options.window,"window"),ui=requiredObject(options.ui,"ui");
    const api=requiredFunction(options.api,"api"),selectors=requiredObject(options.selectors,"selectors"),module=requiredObject(options.module,"session_controller_module");
    const createWorkbenchState=requiredFunction(selectors.createUiState,"workbench_state_factory");
    requiredFunction(module.create,"session_controller_factory");
    return module.create({
      state:agent.state,nodes:agent.nodes,eventStore:agent.eventStore,
      messageNodes:agent.messageNodes,toolNodes:agent.toolNodes,actionNodes:agent.actionNodes,
      api:(name,...args)=>api(name,...args),storeModel:session=>agent.storeModel(session),
      isActive:session=>agent.isActive(session),createWorkbenchState:()=>createWorkbenchState(),
      pendingApplicationSession:()=>agent.pendingApplicationSession?.(),saveReadingAnchor:session=>agent.saveReadingAnchor?.(session),
      historyWindowView:()=>agent.historyWindowView,telemetryView:()=>agent.telemetryView,conversationView:()=>agent.conversationView,
      questionController:()=>agent.questionController,inboxDock:()=>agent.inboxDock,checkpointView:()=>agent.checkpointView,
      switchComposerContext:id=>agent.composer?.switchContext?.(agent.composerContextKey(id)),
      renderModelControls:()=>agent.renderModelControls?.(),refresh:()=>agent.refresh?.(),poll:(...args)=>agent.poll?.(...args),
      loadHostOutline:session=>agent.loadHostOutline?.(session),loadWorkbenchFoundation:(...args)=>agent.loadWorkbenchFoundation?.(...args),
      bindCheckpoints:session=>agent.bindCheckpoints?.(session),restoreReadingAnchor:session=>agent.restoreReadingAnchor?.(session),
      notice:(...args)=>agent.notice?.(...args),currentKind:()=>agent.currentKind?.(),cliConnected:()=>agent.cliConnected?.(),
      sessionProviderMatches:session=>agent.sessionProviderMatches?.(session),capture:kind=>agent.capture?.(kind),workspace:base=>agent.workspace?.(base),
      recoverCommittedApplication:(...args)=>agent.applicationRecoveryController?.recoverCommitted?.(...args),
      recoverLastAppliedProjection:(...args)=>agent.applicationRecoveryController?.recoverLastApplied?.(...args),
      receiveCommand:(...args)=>agent.receiveCommand?.(...args),transportReject:(...args)=>agent.transport?.rejectExternal?.(...args),
      receiveProtocolText:reason=>agent.receiveProtocolText?.(reason),workbenchCapabilities:value=>agent.workbenchCapabilities?.(value),
      clone:value=>agent.clone(value),schedule:session=>agent.schedule?.(session),renderSessionNavigation:()=>agent.renderSessionNavigation?.(),
      mutationId:kind=>agent.workbenchMutationId?.(kind),errorText:(...args)=>agent.errorText(...args),label:value=>agent.label(value),
      uiElement:(...args)=>ui.el(...args),confirm:message=>windowObject.confirm(message),
      transportInvalidate:(...args)=>agent.transport?.invalidate?.(...args),
      dropComposerContext:id=>agent.composer?.dropContext?.(agent.composerContextKey(id)),
      composer:()=>agent.composer,shellSetSection:(...args)=>agent.shell?.setSection?.(...args),
    });
  }

  function createReadController(options = {}) {
    const agent=requiredObject(options.agent,"agent"),windowObject=requiredObject(options.window,"window"),ui=requiredObject(options.ui,"ui");
    const api=requiredFunction(options.api,"api"),module=requiredObject(options.module,"read_controller_module");
    const outline=requiredObject(options.outline,"turn_outline_module"),foundation=requiredObject(options.foundation,"foundation_module");
    requiredFunction(module.create,"read_controller_factory");
    return module.create({
      api:(name,...args)=>api(name,...args),state:()=>agent.state,selected:()=>agent.selected(),readModel:session=>agent.readModel(session),
      errorText:(...args)=>agent.errorText(...args),notice:(...args)=>agent.notice(...args),capabilities:()=>agent.state.workbenchCapabilities,nodes:()=>agent.nodes,
      sessionNavigation:()=>agent.sessionNavigation,pendingApplicationSession:()=>agent.pendingApplicationSession(),
      currentDocumentId:()=>agent.currentKind()==="whiteboard"?windowObject.WB?.state?.board?.board_id:windowObject.PL?.state?.plan?.plan_id,
      isActive:session=>agent.isActive(session),hasPendingApplication:session=>agent.hasPendingApplication?.(session)===true,
      hasUnsettledAppAction:session=>agent.hasUnsettledAppAction?.(session)===true,turnOutline:()=>agent.turnOutline,
      conversationView:()=>agent.conversationView,historyWindowView:()=>agent.historyWindowView,telemetryView:()=>agent.telemetryView,
      questionController:()=>agent.questionController,inboxDock:()=>agent.inboxDock,readingAnchors:()=>agent.readingAnchors,
      workbenchState:session=>agent.workbenchState(session),renderWorkbench:session=>agent.renderWorkbench(session),renderMessages:session=>agent.renderMessages(session),
      clone:agent.clone,element:(...args)=>ui.el(...args),button:(...args)=>ui.button(...args),queryDigest:value=>outline.queryDigest(value),
      parseSearch:(...args)=>outline.parseSearch(...args),parseFoundation:(...args)=>foundation.parseProjection(...args),
      setTimer:(callback,delay)=>windowObject.setTimeout(callback,delay),clearTimer:timer=>windowObject.clearTimeout(timer),
      nextFrame:callback=>typeof windowObject.requestAnimationFrame==="function"?windowObject.requestAnimationFrame(callback):callback(),
    });
  }

  function create(options = {}) {
    const agent = requiredObject(options.agent, "agent");
    const windowObject = requiredObject(options.window, "window");
    const documentObject = requiredObject(options.document, "document");
    const ui = requiredObject(options.ui, "ui");
    const api = requiredFunction(options.api, "api");
    const modules = requiredObject(options.modules, "modules");
    const composerModule = requiredObject(modules.composer, "composer_module");
    const turnModule = requiredObject(modules.turnController, "turn_controller_module");
    const conversationWindowModule = requiredObject(modules.conversationWindow, "conversation_window_module");
    requiredFunction(composerModule.createController, "composer_factory");
    requiredFunction(turnModule.create, "turn_controller_factory");
    requiredFunction(conversationWindowModule.create, "conversation_window_factory");

    let result = null, failure = null;

    function wire() {
      // A duplicate init can happen when a WebView reload callback and the
      // normal DOM-ready path race.  Return the exact same composition rather
      // than adding a second listener or command owner.
      if (result) return result;
      if (failure) throw new Error("workbench_bootstrap_failed", {cause:failure});
      const nodes = requiredObject(agent.nodes, "nodes");
      const state = requiredObject(agent.state, "state");
      requiredObject(agent.shell, "shell");
      requiredObject(agent.sessionController, "session_controller");
      for (const name of ["input", "send", "activeSend", "stop", "sendMode", "composerContext", "composerRoot", "root", "resizer", "inspectorResizer", "inspectorScrim", "controls", "detailsPane", "chatPane", "inspectorPane", "settingsToggle", "detailsToggle", "chatToggle", "inspectorToggle", "modeToggle"])
        if (!nodes[name]) throw new TypeError(`workbench_bootstrap_node_${name}_missing`);

      try {
      const composer = composerModule.createController({
        storage:windowObject.localStorage,
        document:documentObject,
        input:nodes.input,
        sendButton:nodes.send,
        activeSendButton:nodes.activeSend,
        stopButton:nodes.stop,
        sendModeSelect:nodes.sendMode,
        contextHost:nodes.composerContext,
        contextSummaryHost:nodes.composerContextSummary,
        root:nodes.composerRoot,
        contextKey:agent.composerContextKey(),
        onSend:(...args)=>agent.send(...args),
        onActiveSend:(...args)=>agent.enqueueActiveMessage(...args),
        onStop:()=>agent.stop(),
        onStorageWarning:message=>agent.notice(message,true),
      });
      const turnController = turnModule.create({
        api:(name,...args)=>api(name,...args), state:()=>state, nodes:()=>nodes,
        notice:(...args)=>agent.notice(...args), refresh:()=>agent.refresh(),
        selected:()=>agent.selected(), active:()=>agent.active(), isActive:session=>agent.isActive(session),
        loadFoundation:(...args)=>agent.loadWorkbenchFoundation(...args), foundation:session=>agent.workbenchFoundation(session),
        schedule:session=>agent.schedule(session), mutationId:prefix=>agent.workbenchMutationId(prefix),
        requestId:prefix=>ui.id(prefix).replace(/[^a-zA-Z0-9_-]/g,"-"), errorText:(...args)=>agent.errorText(...args),
        hasRuntimeCapability:name=>agent.hasRuntimeCapability(name), applyRuntimeStatus:value=>agent.applyRuntimeStatus(value),
        checkpointFrozen:()=>agent.checkpointFrozen?.(), workbenchTarget:value=>agent.workbenchTarget(value),
        composer:()=>composer, inboxDock:()=>agent.inboxDock,
        pendingApplicationSession:()=>agent.pendingApplicationSession(),
        hasUnsettledAppAction:session=>agent.hasUnsettledAppAction(session), cliConnected:()=>agent.cliConnected(),
        sessionProviderMatches:session=>agent.sessionProviderMatches(session), workbenchProjection:session=>agent.workbenchProjection(session),
        capture:kind=>agent.capture(kind), same:(left,right)=>agent.same(left,right), workflow:()=>agent.workflow(),
        noReasoningModel:model=>agent.noReasoningModel(model), validEffort:(model,effort)=>agent.validEffort(model,effort),
        workspace:base=>agent.workspace(base), runtimePhaseText:value=>agent.runtimePhaseText(value),
        loadHistory:()=>agent.loadHistory(), poll:(...args)=>agent.poll(...args), getSession:id=>agent.session(id),
        receiveCommand:(...args)=>agent.receiveCommand(...args), transport:()=>agent.transport,
        receiveProtocolText:reason=>agent.receiveProtocolText(reason), isAuthoring:session=>agent.isAuthoring(session),
        activateSession:payload=>agent.sessionController.activateAcceptedSession(payload),
        finishAcceptedSession:payload=>agent.sessionController.finishAcceptedSession(payload),
      });
      const conversationWindow = conversationWindowModule.create();
      const toolWindow = conversationWindowModule.create({defaultLimit:100,step:50,maximum:300});
      // Publish controllers only after every factory has succeeded.  From
      // this point composition is the sole owner; a later shell/startup fault
      // is sticky and cannot install a second set of command listeners.
      agent.composer = composer; agent.turnController = turnController;
      agent.conversationWindow = conversationWindow; agent.toolWindow = toolWindow;
      agent.shell.bind({
        root:nodes.root, body:documentObject.body, badge:nodes.badge, resizer:nodes.resizer,
        pageHost:nodes.pageHost, dockHost:documentObject.body,
        settingsPane:nodes.controls, detailsPane:nodes.detailsPane, chatPane:nodes.chatPane,
        inspectorPane:nodes.inspectorPane, inspectorResizer:nodes.inspectorResizer, inspectorScrim:nodes.inspectorScrim,
        settingsToggle:nodes.settingsToggle, detailsToggle:nodes.detailsToggle,
        chatToggle:nodes.chatToggle, inspectorToggle:nodes.inspectorToggle, modeToggle:nodes.modeToggle,
        settingsFocus:nodes.settingsSummary, detailsFocus:nodes.taskSummary, inspectorFocus:nodes.inspectorActivityTab, composerInput:nodes.input,
      });
      agent.installResumeSupervisor();
      agent.applyPanelLayout();
      agent.renderSettings();
      agent.refresh();

      result = Object.freeze({composer, turnController, conversationWindow, toolWindow});
      return result;
      } catch (error) {
        failure = error;
        throw error;
      }
    }

    return Object.freeze({wire, snapshot:()=>result});
  }

  function createProduction(options = {}) {
    const agent = requiredObject(options.agent, "agent");
    const windowObject = requiredObject(options.window, "window");
    const documentObject = requiredObject(options.document, "document");
    const ui = requiredObject(options.ui, "ui");
    const api = options.api
      ? requiredFunction(options.api, "api")
      : (...args) => requiredFunction(windowObject.callApi, "api")(...args);
    const supplied = options.modules || {};
    const modules = {
      layout:supplied.layout || global.AgentWorkbenchLayout,
      store:supplied.store || global.AgentWorkbenchStore,
      stateAdapter:supplied.stateAdapter || global.AgentWorkbenchStateAdapter,
      selectors:supplied.selectors || global.AgentWorkbenchSelectors,
      client:supplied.client || global.AgentWorkbenchClient,
      transport:supplied.transport || global.AgentWorkbenchTransportController,
      documentAdapter:supplied.documentAdapter || global.AgentDocumentAdapter,
      shell:supplied.shell || global.AgentWorkbenchShell,
      activity:supplied.activity || global.AgentActivityFeedback,
      provider:supplied.provider || global.AgentProviderSettings,
      session:supplied.session || global.AgentSessionController,
      read:supplied.read || global.AgentWorkbenchReadController,
      turnOutline:supplied.turnOutline || global.AgentTurnOutline,
      questionInbox:supplied.questionInbox || global.AgentQuestionInbox,
      sessionList:supplied.sessionList || global.AgentSessionList,
      workbenchView:supplied.workbenchView || global.AgentWorkbenchView,
      conversationView:supplied.conversationView || global.AgentConversationView,
      historyWindow:supplied.historyWindow || global.AgentHistoryWindow,
      telemetry:supplied.telemetry || global.AgentTelemetry,
      checkpoints:supplied.checkpoints || global.AgentCheckpoints,
      checkpointController:supplied.checkpointController || global.AgentCheckpointController,
      checkpointIntegration:supplied.checkpointIntegration || global.AgentCheckpointIntegration,
      workflowReview:supplied.workflowReview || global.AgentWorkflowReviewView,
      recovery:supplied.recovery || global.AgentApplicationRecovery,
      transactions:supplied.transactions || global.AgentDomainTransactions,
      questionAnswer:supplied.questionAnswer || global.AgentQuestionAnswerController,
      lifecycle:supplied.lifecycle || global.AgentAppLifecycleController,
      presentation:supplied.presentation || global.AgentWorkbenchPresentationController,
      composer:supplied.composer || global.AgentComposer,
      turnController:supplied.turnController || global.AgentTurnController,
      conversationWindow:supplied.conversationWindow || global.AgentConversationWindow,
    };
    for (const [name, module] of Object.entries(modules)) requiredObject(module, `${name}_module`);
    // Bootstrap privately owns construction of the mutable production shell.
    // The compatibility facade receives read-only references; controllers may
    // mutate the contained state, but no other composition can replace it.
    const shellState=modules.stateAdapter.createShell(modules.selectors);
    for(const name of ["state","nodes","messageNodes","toolNodes","actionNodes","pendingAppFiles"]){
      if(Object.hasOwn(agent,name))throw new TypeError(`workbench_bootstrap_${name}_already_owned`);
      Object.defineProperty(agent,name,{enumerable:false,configurable:false,get:()=>shellState[name]});
    }
    let result = null, failure = null;
    const publish = (target, names) => { for (const name of names) agent[name] = (...args) => target[name](...args); };
    agent.workbenchState=(session=agent.selected?.())=>agent.stateAdapter?.workbenchState(session)||agent.state.workbench;
    agent.runtimeBudgetView=session=>{
      const rows=Object.values(agent.readModel(session)?.budgetStatuses||{}).map(row=>({...row,...agent.runtimeBudgetSpecs[row.status]})),threshold=rows.filter(row=>row.group==="threshold").sort((a,b)=>b.rank-a.rank||b.seq-a.seq)[0],context=rows.filter(row=>row.group==="context").sort((a,b)=>b.rank-a.rank||b.seq-a.seq);
      const shown=[...(threshold?[threshold]:[]),...context];if(!shown.length)return null;const strongest=shown.reduce((best,row)=>row.rank>best.rank?row:best,shown[0]);
      return {level:strongest.level,compaction:shown.some(row=>row.compaction===true),text:shown.map(row=>`${row.label}：${row.message}`).join("\n")};
    };
    agent.workbenchCapabilities=value=>{
      const row=value&&typeof value==="object"&&!Array.isArray(value)?value:{};
      return {turn_outline:row.turn_outline===true,conversation_search:row.conversation_search===true,history_window:row.history_window===true,telemetry_bundle:row.telemetry_bundle===true,
        workbench_foundation:row.workbench_foundation===true,structured_question_batch:row.structured_question_batch===true,durable_inbox:row.durable_inbox===true,document_checkpoints:row.document_checkpoints===true,session_lifecycle:row.session_lifecycle===true};
    };
    const openWorkbench=()=>{if(!agent.state.selected){agent.nodes.kind.value=agent.currentKind();agent.nodes.workflowExplicit=false;agent.composer?.switchContext?.(agent.composerContextKey());}agent.state.open=true;agent.shell?.setOpen?.(true);agent.refresh();if(!agent.state.detected)agent.detect();agent.loadHistory();agent.loadBudget();agent.loadDshSettings();};
    const returnFromAgentPage=()=>{if(windowObject.WS?.tab?.()!=="agent")return;const preferred=documentObject.body?.dataset?.agentReturnTab,tab=preferred&&preferred!=="agent"?preferred:"rotation";windowObject.WS?.navigate?.(tab);};
    agent.openDock=()=>{agent.shell?.setMode?.("docked",true);returnFromAgentPage();openWorkbench();};
    agent.openPage=()=>{
      if(windowObject.WS?.tab?.()!=="agent"){
        const previous=windowObject.WS?.tab?.();
        if(previous&&previous!=="agent"&&documentObject.body?.dataset)documentObject.body.dataset.agentReturnTab=previous;
        windowObject.WS?.navigate?.("agent");
        if(windowObject.WS?.tab?.()!=="agent")return false;
        // Navigation clicks the Agent tab, whose listener performs the actual
        // activation. Avoid opening and refreshing the same workbench twice.
        if(agent.shell?.state?.viewMode==="page"&&agent.state.open)return true;
      }
      agent.shell?.setMode?.("page",true);openWorkbench();return true;
    };
    agent.open=()=>agent.openDock();
    agent.toggleWorkbenchMode=()=>{const next=agent.shell?.toggleMode?.()||"docked";if(next==="page"){openWorkbench();windowObject.WS?.navigate?.("agent");}else{returnFromAgentPage();openWorkbench();}return next;};
    agent.close=()=>{const page=agent.shell?.state?.viewMode==="page";agent.state.open=false;agent.composer?.save?.();agent.shell?.setOpen?.(false);agent.refresh();if(page)returnFromAgentPage();};
    agent.toggle=()=>agent.state.open?agent.close():agent.open();
    agent.startCalculation=()=>{if(agent.state.busy||agent.active())return false;agent.newTask();agent.nodes.kind.value="whiteboard";agent.nodes.workflow.value="calculate";agent.nodes.workflowExplicit=true;agent.refresh();agent.notice("已显式切换到严格计算新任务。计算会检查通用逻辑、数值、绑定、动作、冷却与能量；失败只返回诊断，不会应用为正式结果。");return true;};
    const publicApi=Object.freeze({
      init:()=>wire(),get ready(){return agent.ready===true;},
      open:()=>agent.open?.(),openDock:()=>agent.openDock?.(),openPage:()=>agent.openPage?.(),toggle:()=>agent.toggle?.(),requestClose:()=>agent.requestClose?.(),newTask:()=>agent.newTask?.(),startCalculation:()=>agent.startCalculation?.(),
      beforeDocumentSave:(...args)=>agent.beforeDocumentSave?.(...args)??null,documentSaved:(...args)=>agent.documentSaved?.(...args),documentChanged:(...args)=>agent.documentChanged?.(...args),
      topologyReceipt:(...args)=>modules.workflowReview.topologyReceipt(...args),executionBindingReceipt:(...args)=>modules.workflowReview.executionBindingReceipt(...args),
      workflowState:(...args)=>agent.workflowReviewView?.workflowState(...args)||modules.workflowReview.workflowState(...args),
      bindingIssueText:(...args)=>modules.workflowReview.bindingIssueText(...args),stageStatusText:(...args)=>modules.workflowReview.stageStatusText(...args),
      bindingGateText:(...args)=>modules.workflowReview.bindingGateText(...args),acceptanceText:(...args)=>modules.workflowReview.acceptanceText(...args),
      renderStageTracker:(...args)=>agent.workflowReviewView?.renderStageTracker(...args)||null,
    });
    windowObject.AgentWorkbenchApp=publicApi;

    function wire() {
      if (result) return result;
      if (failure) throw new Error("workbench_bootstrap_failed", {cause:failure});
      try {
        const root = documentObject.getElementById("agent-chat"), badge = documentObject.getElementById("agent-chat-toggle");
        if (!root || !badge) throw new TypeError("workbench_bootstrap_mount_missing");

        agent.eventStore = modules.store.createTransactionalLegacyStore();
        agent.stateAdapter = modules.stateAdapter.create({state:agent.state,eventStore:agent.eventStore,selectors:modules.selectors,clone:agent.clone});
        agent.documentAdapter = modules.documentAdapter.create({transport:global.JSONTransport,whiteboard:()=>windowObject.WB,plan:()=>windowObject.PL,rotation:()=>windowObject.RT,result:()=>windowObject.RS,workspace:()=>windowObject.WS});
        const layout = modules.layout.create({
          agent,window:windowObject,document:documentObject,ui,root,badge,nodes:agent.nodes,
          onCliSelected:value=>{agent.state.cliId=value;agent.renderCliDetail?.();agent.renderModelControls?.();agent.refresh?.();},
          onModelSelected:value=>{agent.state.model=value;agent.state.effort=agent.defaultEffort?.(agent.state.models.find(model=>model.model===value));agent.renderEfforts?.();agent.refresh?.();},
          onEffortSelected:value=>{agent.state.effort=value;agent.refresh?.();},
          onSearchInput:value=>{const session=agent.selected?.();const workbench=agent.workbenchState(session);if(workbench)workbench.query=value;agent.renderWorkbench?.(session);agent.renderMessages?.(session);agent.scheduleHostSearch?.(session,value);},
          onKindSelected:()=>{if(!agent.state.selected)agent.composer?.switchContext?.(agent.composerContextKey());agent.refresh?.();},
        });
        const nodes = layout.nodes;
        if(agent.nodes!==nodes)Object.assign(agent.nodes,nodes);
        agent.shell = modules.shell.createController({window:windowObject,document:documentObject,storage:windowObject.localStorage,onReflow:agent.requestWorkspaceReflow,onRequestModeToggle:()=>agent.toggleWorkbenchMode?.()});
        agent.state.panel = agent.shell.state;
        agent.openInspector = (view="activity") => {
          nodes.setInspectorView?.(view);
          agent.shell.setSection("inspector", true, true);
          return view;
        };

        agent.presentationController = modules.presentation.create({
          agent,window:windowObject,document:documentObject,ui,
          openExternal:async request=>{const response=await api("open_external_link",request?.url);if(response?.code)throw new Error(agent.errorText(response,"无法使用系统浏览器打开链接。"));return true;},
        });
        publish(agent.presentationController,["renderAppActions","workbenchProjection","setWorkbenchView","setWorkbenchTurn","renderStatusAxes","feedbackQuestionOpen","observeSessionFeedback","refreshActivityFeedback","renderWorkbench","renderTimelineNode","renderMessages"]);
        agent.refresh=(...args)=>agent.presentationController.refresh(...args);
        agent.beforeDocumentSave=(...args)=>agent.presentationController.beforeDocumentSave(...args);
        agent.documentSaved=(...args)=>agent.presentationController.documentSaved(...args);
        agent.documentChanged=(...args)=>agent.presentationController.documentChanged(...args);

        if (modules.activity?.create) agent.activityFeedback=modules.activity.create({document:documentObject,host:nodes.activityFeedbackHost,summaryHost:nodes.status,dockHost:nodes.statusDock,groupHost:nodes.statusRunningGroup,liveHost:nodes.activityLive,toastHost:nodes.toastHost,visibilityHost:root,storage:windowObject.localStorage,onOpenSession:id=>{agent.selectSession(id);agent.openDock();},onPreferences:preferences=>{root.dataset.detailLevel=preferences.details;root.dataset.motion=preferences.motion;root.dataset.notificationPolicy=preferences.notifications;nodes.toastHost.dataset.motion=preferences.motion;nodes.feedbackDetails.value=preferences.details;nodes.feedbackMotion.value=preferences.motion;nodes.feedbackNotifications.value=preferences.notifications;}});
        const preferences=agent.activityFeedback?.preferences?.()||{details:"standard",motion:"system",notifications:"all"};
        nodes.feedbackDetails.value=preferences.details;nodes.feedbackMotion.value=preferences.motion;nodes.feedbackNotifications.value=preferences.notifications;

        agent.providerSettings=modules.provider.create({state:agent.state,nodes,ui,window:windowObject,document:documentObject,api:(name,...args)=>api(name,...args),clone:agent.clone,stableErrorCode:agent.stableErrorCode,errorText:agent.errorText,notice:(...args)=>agent.notice(...args),active:()=>agent.active(),selected:()=>agent.selected(),readModel:session=>agent.readModel(session),isActive:session=>agent.isActive(session),schedule:session=>agent.schedule(session),refresh:()=>agent.refresh(),workbenchProjection:session=>agent.workbenchProjection(session),text:agent.text,transport:()=>agent.transport});
        Object.assign(agent,agent.providerSettings);

        agent.transportController=modules.transport.create({
          client:modules.client,eventStore:agent.eventStore,sidecars:agent.stateAdapter,api:(name,...args)=>api(name,...args),state:()=>agent.state,clone:agent.clone,
          ackCursor:session=>agent.ackCursor(session),readModel:session=>agent.readModel(session),isActive:session=>agent.isActive(session),refresh:()=>agent.refresh(),currentMatches:base=>agent.currentMatches(base),selected:()=>agent.selected(),errorText:agent.errorText,
          connectionPayloadMatches:value=>agent.connectionPayloadMatches(value),mergeHostUi:(...args)=>agent.mergeHostUi(...args),connectionHostUiFields:agent.connectionHostUiFields,
          testMode:()=>windowObject.__GENSHIN_AGENT_TEST__===true,markTransportSuccess:(...args)=>agent.activityFeedback?.markTransportSuccess?.(...args),observeSessionFeedback:(...args)=>agent.observeSessionFeedback?.(...args),
          hasWorkbenchFoundation:()=>agent.state.workbenchCapabilities.workbench_foundation===true,loadWorkbenchFoundation:session=>agent.loadWorkbenchFoundation?.(session),syncSelectedRead:id=>agent.syncSelectedRead?.(id),loadHistory:()=>agent.loadHistory?.(),
          setTimer:(callback,delay)=>windowObject.setTimeout(callback,delay),clearTimer:timer=>windowObject.clearTimeout(timer),
        });
        agent.transport=agent.transportController.transport;agent.receiveProtocolText=reason=>agent.transportController.protocolText(reason);
        publish(agent.transportController,["receive","receiveCommand","schedule","poll","reconcile"]);

        agent.checkpointIntegration=modules.checkpointIntegration.create({agent,window:windowObject,document:documentObject,ui,api:(name,...args)=>api(name,...args),checkpoints:modules.checkpoints,controller:modules.checkpointController});
        agent.checkpointFrozen=()=>agent.checkpointIntegration.frozen();
        const checkpointPorts={bindCheckpoints:"bind",checkpointCreate:"createCheckpoint",checkpointRename:"renameCheckpoint",checkpointDelete:"deleteCheckpoint",checkpointRestore:"restoreCheckpoint",checkpointPreview:"previewCheckpoint"};
        for(const [external,internal] of Object.entries(checkpointPorts))agent[external]=(...args)=>agent.checkpointIntegration[internal](...args);

        agent.readController=createReadController({agent,window:windowObject,ui,api:(name,...args)=>api(name,...args),module:modules.read,outline:modules.turnOutline,foundation:modules.questionInbox});
        publish(agent.readController,["renderSessionNavigation","saveReadingAnchor","restoreReadingAnchor","clearHostOutline","loadHostOutline","loadConversationPage","loadReasoning","renderHostSearchResults","loadHostSearchPage","scheduleHostSearch","locateHostTurn","loadTelemetry","applyTelemetryFilters","workbenchFoundation","workbenchTarget","presentWorkbenchFoundation","loadWorkbenchFoundation"]);

        agent.sessionController=createSessionController({agent,window:windowObject,ui,api:(name,...args)=>api(name,...args),module:modules.session,selectors:modules.selectors});
        Object.assign(agent,agent.sessionController);
        agent.sessionNavigation=modules.sessionList.create(documentObject,nodes.sessionNavigationHost,{onSelect:id=>agent.selectSession(id),onResume:id=>agent.resumeSession(id),onRename:agent.renameSession,onArchive:agent.archiveSession,onForget:agent.forgetSession,compatibilityPrompt:agent.compatibilityPrompt,onCopyCompatibilityPrompt:async()=>{const copied=await agent.copyCompatibilityPrompt?.();agent.notice?.(copied?"兼容处理提示词已复制。":"无法访问系统剪贴板，请展开后手动复制提示词。",copied!==true);return copied;},onError:error=>agent.notice?.(String(error?.message||error),true)});
        const attachSessionNavigation = () => windowObject.WS?.attachContextHistory?.(nodes.sessionNavigationHost,{owner:"agent"});
        attachSessionNavigation();
        windowObject.addEventListener?.("workspace:sidebar-mounted",attachSessionNavigation,{once:true});
        agent.turnOutline=modules.turnOutline.create(documentObject,nodes.hostOutlineHost,{onLocate:agent.locateHostTurn,onPage:request=>{const session=agent.selected();if(session)agent.loadHostOutline(session,request);},onSearchResults:agent.renderHostSearchResults});
        agent.clearHostOutline();agent.readingAnchors=modules.turnOutline.createAnchorStore(windowObject.localStorage);
        agent.inboxDock=modules.questionInbox.createInboxDock(documentObject,nodes.inboxHost,{onAction:agent.handleInboxAction});
        ({view:agent.checkpointView,controller:agent.checkpointController}=agent.checkpointIntegration.wire(nodes.checkpointHost));
        modules.workbenchView.bindTabs({chatTab:nodes.chatTab,trajectoryTab:nodes.trajectoryTab,onSelect:agent.setWorkbenchView});
        agent.conversationView=modules.conversationView.create(documentObject,nodes.messages,{controlsHost:nodes.conversationHealth,parsePage:modules.historyWindow.parse,
          includeRow:row=>row?.kind==="message",renderNode:row=>agent.renderTimelineNode(row),
          onStatusDetails:()=>agent.openInspector?.("activity"),onPage:(request,context)=>{const session=agent.state.sessions.get(context.session_id);return agent.loadConversationPage(session,request,context);},onActionIndex:index=>{const session=agent.selected();if(session)session.timelineActionIndex=index;}});
        if(agent.state.selected)agent.conversationView.activate(agent.state.selected);
        agent.historyWindowView=modules.historyWindow.create(documentObject,nodes.historyWindowHost,{onClose:()=>{agent.historyWindowView.clear();nodes.historyWindowHost.hidden=true;},onSelect:row=>agent.turnOutline?.select?.(row.turn_id),onPage:request=>agent.locateHostTurn(request)});
        agent.telemetryView=modules.telemetry.create(documentObject,nodes.telemetryHost,{onInspect:span=>{const rows=[span.label||span.category,`状态：${span.state}`,`类别／来源：${span.category} · ${span.timing_source}/${span.timing_precision}`,`耗时：${span.elapsed_ms===null?"未知":`${span.elapsed_ms} ms`}`,`Turn：${span.turn_id}`,span.segment_id?`执行段：${span.segment_id}`:null,span.workflow_stage?`阶段：${span.workflow_stage}`:null,span.result_code?`结果码：${span.result_code}`:null,span.resource_ref?`资源：${span.resource_ref}`:null,span.summary||null].filter(Boolean);nodes.telemetryInspector.textContent=rows.join("\n");},onPage:request=>{const session=agent.selected();if(session)agent.loadTelemetry(session,request);}});

        agent.domainTransactions=modules.transactions.createController();
        agent.workflowReviewView=modules.workflowReview.create({window:windowObject,document:documentObject,ui,state:agent.state,nodes,selected:()=>agent.selected(),readModel:session=>agent.readModel(session),active:()=>agent.active(),refresh:()=>agent.refresh(),notice:(...args)=>agent.notice(...args),errorText:(...args)=>agent.errorText(...args),clone:agent.clone,text:agent.text,defaultWorkflow:kind=>agent.defaultWorkflow(kind),checkpointFrozen:()=>agent.checkpointFrozen(),hasUnsettledAppAction:session=>agent.hasUnsettledAppAction(session),appliedCandidate:(session,candidate)=>agent.appliedCandidate(session,candidate),reviewCandidate:(session,operation)=>agent.domainTransactions.reviewCandidate(session,operation),loadCandidate:session=>agent.candidateController.loadCandidate(session),readCandidate:(...args)=>agent.candidateController.readCandidate(...args),onApply:(session,candidate,error)=>agent.accept(session,candidate,error),catalogName:(group,id,session)=>{const row=windowObject.DS?.catalogs?.get(session?.base?.document?.version_line||"release")?.[group]?.find(item=>item.id===id);return row?.display_name||row?.name;},actionTypeName:id=>windowObject.WB?.typeName?.(id)});
        agent.applicationRecoveryController=modules.recovery.create({state:agent.state,api:(name,...args)=>api(name,...args),selected:()=>agent.selected(),active:()=>agent.active(),checkpointFrozen:()=>agent.checkpointFrozen(),hasUnsettledAppAction:session=>agent.hasUnsettledAppAction(session),hasPendingApplication:session=>agent.hasPendingApplication(session),pendingApplicationSession:()=>agent.pendingApplicationSession(),capture:kind=>agent.capture(kind),workspace:base=>agent.workspace(base),currentMatches:base=>agent.currentMatches(base),clone:agent.clone,stringify:global.JSONTransport.stringify,whiteboard:()=>windowObject.WB,currentTab:()=>windowObject.WS?.tab?.(),confirm:message=>windowObject.confirm(message),notice:(...args)=>agent.notice(...args),refresh:()=>agent.refresh(),errorText:(...args)=>agent.errorText(...args),receiveCommand:(...args)=>agent.receiveCommand(...args),receiveProtocolText:reason=>agent.receiveProtocolText(reason),rejectExternal:(...args)=>agent.transport.rejectExternal(...args),reconcile:session=>agent.reconcile(session)});
        agent.candidateController=modules.transactions.createCandidateController({window:windowObject,state:agent.state,api:(name,...args)=>api(name,...args),transactions:agent.domainTransactions,selected:()=>agent.selected(),active:()=>agent.active(),hasUnsettledAppAction:session=>agent.hasUnsettledAppAction(session),notice:(...args)=>agent.notice(...args),refresh:()=>agent.refresh(),capture:kind=>agent.capture(kind),clone:agent.clone,workspace:base=>agent.workspace(base),same:(a,b)=>agent.same(a,b),sameContent:(a,b)=>agent.sameContent(a,b),currentMatches:base=>agent.currentMatches(base),reconcile:session=>agent.reconcile(session),receiveCommand:(...args)=>agent.receiveCommand(...args),errorText:(...args)=>agent.errorText(...args),receiveProtocolText:reason=>agent.receiveProtocolText(reason),recordAppAction:(session,action)=>agent.stateAdapter.recordAppAction(session,action),publishCandidate:(session,candidate)=>agent.stateAdapter.publishCandidate(session,candidate),readModel:session=>agent.readModel(session),canApply:(session,candidate)=>agent.canApply(session,candidate),loadHistory:()=>agent.loadHistory(),transport:agent.transport});
        agent.appActionController=modules.transactions.createAppActionController({window:windowObject,document:documentObject,ui,state:agent.state,pendingFiles:agent.pendingAppFiles,api:(name,...args)=>api(name,...args),transactions:agent.domainTransactions,active:()=>agent.active(),checkpointFrozen:()=>agent.checkpointFrozen(),capture:kind=>agent.capture(kind),currentKind:()=>agent.currentKind(),clone:agent.clone,workspace:base=>agent.workspace(base),same:(a,b)=>agent.same(a,b),currentMatches:base=>agent.currentMatches(base),appliedCandidate:(session,candidate)=>agent.appliedCandidate(session,candidate),recordAppAction:(session,action)=>agent.stateAdapter.recordAppAction(session,action),notice:(...args)=>agent.notice(...args),refresh:()=>agent.refresh(),errorText:(...args)=>agent.errorText(...args),loadHistory:()=>agent.loadHistory(),reconcile:session=>agent.reconcile(session),transport:agent.transport,labels:agent.appActionLabels});
        agent.appActionRenderer=modules.transactions.createAppActionRenderer({host:nodes.appActionsBody,summaryHost:nodes.appActionsSummary,sectionHost:nodes.appActions,nodes:agent.actionNodes,element:ui.el,button:ui.button,eligibility:agent.appActionEligibility,isTwoPhase:agent.isTwoPhaseAppAction,statusLabels:agent.appActionStatus,onExecute:agent.executeAppAction,onRecover:agent.recoverUnknownAppAction});
        agent.questionAnswerController=modules.questionAnswer.create({api:(name,...args)=>api(name,...args),getSession:id=>agent.state.sessions.get(id),hasCapability:name=>agent.state.workbenchCapabilities?.[name]===true,loadFoundation:(...args)=>agent.loadWorkbenchFoundation(...args),schedule:session=>agent.schedule(session),notice:(...args)=>agent.notice(...args),errorText:(...args)=>agent.errorText(...args)});
        agent.questionController=modules.questionInbox.createQuestionController(documentObject,nodes.questionHost,{storage:windowObject.localStorage,onSubmit:agent.submitQuestionBatch,onTakeover:(takeover,question)=>{agent.state.questionTakeover=takeover===true;nodes.composerRoot.dataset.question=question?.state||"none";nodes.input.disabled=takeover===true;agent.refresh();},onMinimize:()=>agent.notice("问题已收起。普通输入只会加入待发送队列，不会被当作问题答案。")});
        agent.lifecycleController=modules.lifecycle.create({state:()=>agent.state,api:(name,...args)=>api(name,...args),leaveWhiteboard:()=>!windowObject.WB||windowObject.WB.leave(),leavePlan:()=>!windowObject.PL||windowObject.PL.leave(),active:()=>agent.active(),confirm:message=>windowObject.confirm(message),stopRuntimeStatusMonitor:()=>agent.stopRuntimeStatusMonitor(),invalidateTransport:options=>agent.transport.invalidateAll(options),clearStore:()=>agent.eventStore.clear(),clearTimer:timer=>windowObject.clearTimeout(timer),sessions:()=>agent.state.sessions.values(),notice:(...args)=>agent.notice(...args),errorText:(...args)=>agent.errorText(...args),reopen:()=>agent.open()});

        // The existing small composer/turn composition remains a tested
        // sub-composition, but only this production root is allowed to invoke it.
        agent.ready=true;
        const commandComposition=create({agent,window:windowObject,document:documentObject,ui,api:(name,...args)=>api(name,...args),modules:{composer:modules.composer,turnController:modules.turnController,conversationWindow:modules.conversationWindow}});
        const commands=commandComposition.wire();
        result=Object.freeze({layout,commands,providerSettings:agent.providerSettings,sessionController:agent.sessionController,transportController:agent.transportController,readController:agent.readController,checkpointIntegration:agent.checkpointIntegration,presentationController:agent.presentationController,workflowReviewView:agent.workflowReviewView,applicationRecoveryController:agent.applicationRecoveryController,candidateController:agent.candidateController,appActionController:agent.appActionController,questionAnswerController:agent.questionAnswerController,lifecycleController:agent.lifecycleController});
        return result;
      } catch(error) {
        agent.ready=false;failure=error;throw error;
      }
    }
    return Object.freeze({wire,snapshot:()=>result,publicApi});
  }

  global.AgentWorkbenchBootstrap = Object.freeze({createSessionController,createReadController,create,createProduction});
})(window);
