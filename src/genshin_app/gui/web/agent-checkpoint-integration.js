(function (global) {
  "use strict";

  // Presentation/integration boundary for document checkpoints.  The
  // AgentCheckpointController remains the sole owner of Host checkpoint
  // transactions; this module only binds that controller to the current App
  // document and the checkpoint view.
  const requiredObject = (value, name) => {
    if (!value || typeof value !== "object") throw new TypeError(`checkpoint_integration_${name}_missing`);
    return value;
  };
  const requiredFunction = (value, name) => {
    if (typeof value !== "function") throw new TypeError(`checkpoint_integration_${name}_missing`);
    return value;
  };

  function create(options = {}) {
    const agent = requiredObject(options.agent, "agent");
    const windowObject = requiredObject(options.window, "window");
    const documentObject = requiredObject(options.document, "document");
    const ui = requiredObject(options.ui, "ui");
    const api = requiredFunction(options.api, "api");
    const checkpoints = requiredObject(options.checkpoints, "checkpoints_module");
    const controllerModule = requiredObject(options.controller, "controller_module");
    requiredFunction(checkpoints.create, "view_factory");
    requiredFunction(checkpoints.parseDiff, "diff_parser");
    requiredFunction(controllerModule.create, "controller_factory");

    let view = null;
    let controller = null;
    let wiredHost = null;
    let bindGeneration = 0;

    const selectedSessionId = () => agent.selected?.()?.session_id || agent.state.selected || null;

    const frozen = () => controller?.snapshot?.().frozen === true
      && controller.snapshot().sessionId === agent.state.selected;

    const applyTarget = async target => {
      if (target?.kind === "whiteboard") {
        if (target.applicationPayload?.kind !== "whiteboard"
            || target.applicationPayload?.board?.board_id !== target.document?.board_id) return false;
        const calculated = target.applicationPayload.calculated;
        const calculation = calculated ? {calculated:agent.clone(calculated), path:null} : null;
        windowObject.WB.applyAgentDraft(agent.clone(target.document), [], calculation, null);
        return true;
      }
      if (target?.kind === "native") {
        if (target.applicationPayload?.kind !== "native"
            || target.applicationPayload?.plan?.plan_id !== target.document?.plan_id) return false;
        const before = agent.capture("native");
        windowObject.PL.replace(agent.clone(target.applicationPayload.plan), before.path, before.sha, false);
        windowObject.PL.state.savedJson = before.savedJson;
        windowObject.PL.renderStatus();
        return true;
      }
      return false;
    };

    const presentState = state => {
      const selected = agent.selected();
      const available = agent.state.workbenchCapabilities.document_checkpoints
        && selected && state?.sessionId === selected.session_id;
      if (agent.nodes.checkpointSection) agent.nodes.checkpointSection.hidden = !available;
      if (!available) return;
      if (state.projection) view?.update?.(state.projection, state.documentId);
      else view?.clear?.();
      view?.setBusy?.(state.busy === true);
      view?.setFrozen?.(state.frozen === true, state.message || "");
      if (!state.frozen) view?.setStatus?.(state.message || "");
      agent.refresh();
    };

    const bind = async session => {
      const generation = ++bindGeneration;
      if (!session || !agent.state.workbenchCapabilities.document_checkpoints || !controller) {
        view?.clear?.();
        if (agent.nodes.checkpointSection) agent.nodes.checkpointSection.hidden = true;
        return false;
      }
      if (selectedSessionId() !== session.session_id) return false;
      let base;
      try { base = agent.capture(session.kind || agent.currentKind()); }
      catch (error) {
        view?.clear?.();
        view?.setStatus?.(error.message);
        if (agent.nodes.checkpointSection) agent.nodes.checkpointSection.hidden = false;
        return false;
      }
      if (generation !== bindGeneration || selectedSessionId() !== session.session_id) return false;
      const existing = controller.snapshot();
      if (existing.sessionId === session.session_id && existing.documentId === base.key && existing.projection) return true;
      const result = await controller.bind({sessionId:session.session_id, kind:base.kind, documentId:base.key});
      return generation === bindGeneration && selectedSessionId() === session.session_id && result === true;
    };

    const createCheckpoint = async ({name}) => controller?.createCheckpoint?.(name);
    const renameCheckpoint = async ({item, name}) => controller?.renameCheckpoint?.(item, name);
    const deleteCheckpoint = async ({item}) => {
      if (!windowObject.confirm(`删除恢复点“${item.name}”？自动安全点正在用于文档恢复时，App 会拒绝删除。`)) return false;
      return controller?.deleteCheckpoint?.(item);
    };
    const restoreCheckpoint = async ({item}) => {
      if (!windowObject.confirm(`将当前文档恢复到“${item.name}”？当前内容会先自动建立安全点；此操作不调用模型。`)) return false;
      return controller?.restoreCheckpoint?.(item);
    };
    const previewCheckpoint = async ({item}) => {
      const sessionId = selectedSessionId(), before = controller?.snapshot?.();
      const raw = await controller?.previewCheckpoint?.(item);
      const after = controller?.snapshot?.();
      if (!raw || !sessionId || selectedSessionId() !== sessionId
          || before?.bindingGeneration !== after?.bindingGeneration
          || before?.sessionId !== after?.sessionId || before?.documentId !== after?.documentId) return false;
      const parsed = checkpoints.parseDiff(raw, item.checkpoint_id);
      if (!parsed.ok) {
        agent.notice(`恢复点差异响应无法核验（${parsed.reason}）。`, true);
        return false;
      }
      const value = parsed.projection;
      const dialog = windowObject.AppDialogKernel?.create?.({title:`恢复点差异 · ${item.name}`,
        description:"比较当前文档与所选恢复点；这里只读取差异，不会恢复或修改文件。",className:"ac-checkpoint-preview"}) || null;
      if (!dialog) { agent.notice("当前还有窗口正在处理，请先完成或关闭后再查看恢复点差异。", true); return false; }
      const section = (label, row) => {
        const box = ui.el("section");
        box.append(ui.el("h4", label), ui.el("p", row.changed
          ? `变更 ${row.changed_key_count} 个字段${row.truncated ? "（仅显示摘要）" : ""}` : "无变化", "ac-note"));
        if (row.added_keys.length) box.append(ui.el("p", `新增：${row.added_keys.join("、")}`));
        if (row.removed_keys.length) box.append(ui.el("p", `删除：${row.removed_keys.join("、")}`));
        if (row.changed_keys.length) box.append(ui.el("p", `修改：${row.changed_keys.join("、")}`));
        return box;
      };
      const close = () => dialog.finish?.("close");
      const compatibilityGuidance = () => {
        const details = ui.el("details", null, "ac-compatibility-guidance");
        const prompt = ui.el("pre", agent.compatibilityPrompt || "", "ac-compatibility-prompt");
        const copy = ui.button("复制给 Agent 的提示词", async event => {
          const button = event?.currentTarget || copy, copied = await agent.copyCompatibilityPrompt?.();
          button.textContent = copied ? "已复制" : "复制失败，请手动选择文本";
        }, "ac-compatibility-copy");
        details.append(ui.el("summary", "如何让 Agent 处理当前文件"), ui.el("p", "这不会自动恢复、创建会话或发送消息。请复制后由你主动发给 Agent。", "ac-note"), prompt, copy);
        return details;
      };
      const versionSummary = ui.el("p", `当前恢复版本 ${value.source_lineage_epoch} / 文档版本 ${value.source_revision} → 目标恢复版本 ${value.target_lineage_epoch} / 文档版本 ${value.target_revision}`, "ac-note");
      versionSummary.title = `技术信息：source lineage ${value.source_lineage_epoch} / revision ${value.source_revision}; target lineage ${value.target_lineage_epoch} / revision ${value.target_revision}`;
      dialog.appBody.append(
        versionSummary,
        section("文档", value.document), section("计算结果数据", value.application_payload),
        ui.el("p", value.application_payload_reusable
          ? "当前数据库、引擎与文件结构允许复用这份计算结果。"
          : "这个恢复点与当前数据库、引擎或文件结构版本不兼容，不能直接恢复。", "ac-warning"),
        ...(value.application_payload_reusable ? [] : [compatibilityGuidance()]),
      );
      dialog.setActions?.(ui.button("关闭", close));
      return true;
    };

    function wire(host) {
      if (wiredHost) {
        if (wiredHost !== host) throw new Error("checkpoint_integration_already_wired");
        return Object.freeze({view, controller});
      }
      wiredHost = requiredObject(host, "host");
      view = checkpoints.create(documentObject, host, {
        onCreate:createCheckpoint,
        onLoadMore:({offset}) => controller?.loadMore?.(offset),
        onPreview:previewCheckpoint,
        onRestore:restoreCheckpoint,
        onRename:renameCheckpoint,
        onDelete:deleteCheckpoint,
      });
      controller = controllerModule.create({
        api:(name, ...args) => api(name, ...args), adapter:agent.documentAdapter,
        capture:agent.capture, applyTarget, stringify:global.JSONTransport.stringify,
        isBindingCurrent:stamp => selectedSessionId() === stamp?.sessionId,
        onState:presentState,
        onError:(response, fallback) => agent.notice(agent.errorText(response, fallback), true),
        onCommitted:snapshot => {
          const session = agent.selected();
          if (session && session.session_id === controller?.snapshot?.().sessionId) session.base = snapshot;
          agent.documentChanged();
        },
      });
      return Object.freeze({view, controller});
    }

    return Object.freeze({
      wire, frozen, bind,
      createCheckpoint, renameCheckpoint, deleteCheckpoint, restoreCheckpoint, previewCheckpoint,
      view:() => view, controller:() => controller,
    });
  }

  global.AgentCheckpointIntegration = Object.freeze({create});
})(window);
