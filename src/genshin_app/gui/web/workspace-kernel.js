(function (global) {
  "use strict";

  const valueOf = (value, context) => typeof value === "function" ? value(context) : value;
  const text = value => String(value == null ? "" : value);

  function createCommandRegistry(options = {}) {
    const definitions = new Map();
    const report = typeof options.report === "function" ? options.report : () => {};

    function register(definition) {
      if (!definition || typeof definition !== "object") throw new TypeError("command_definition_invalid");
      const id = text(definition.id).trim();
      if (!id) throw new TypeError("command_id_required");
      if (definitions.has(id)) throw new TypeError(`command_id_duplicate:${id}`);
      const stored = Object.freeze({...definition,id});
      definitions.set(id, stored);
      return () => definitions.delete(id);
    }

    function resolve(id, context = {}) {
      const definition = definitions.get(String(id));
      if (!definition) return null;
      const visible = valueOf(definition.visible, context) !== false;
      const enabled = visible && valueOf(definition.enabled, context) !== false;
      return Object.freeze({
        id:definition.id,
        group:text(definition.group || "other"),
        section:text(definition.section || "main"),
        order:Number.isFinite(definition.order) ? definition.order : 0,
        label:text(valueOf(definition.label, context) || definition.id),
        shortLabel:text(valueOf(definition.shortLabel, context) || valueOf(definition.label, context) || definition.id),
        shortcut:text(valueOf(definition.shortcut, context)),
        level:text(definition.level || "secondary"),
        danger:definition.danger === true,
        visible,
        enabled,
        disabledReason:enabled ? "" : text(valueOf(definition.disabledReason, context) || "当前条件不允许执行此操作。"),
        run:definition.run,
      });
    }

    function list(context = {}, {includeHidden = false} = {}) {
      return Array.from(definitions.keys(), id => resolve(id, context))
        .filter(command => command && (includeHidden || command.visible))
        .sort((a,b) => a.order-b.order || a.id.localeCompare(b.id));
    }

    async function invoke(id, context = {}) {
      const command = resolve(id, context);
      if (!command || !command.visible || !command.enabled || typeof command.run !== "function") {
        report({
          factKey:`command.${id}.unavailable`,
          kind:"warning",
          message:command?.disabledReason || "此操作不适用于当前页面。",
          details:{command_id:String(id)},
        });
        return Object.freeze({ok:false,status:"disabled",command});
      }
      try {
        const value = await command.run(context, command);
        return Object.freeze({ok:true,status:value === false ? "cancelled" : "success",command,value});
      } catch (error) {
        report({
          factKey:`command.${id}.failed`,
          kind:"error",
          message:"操作未完成，请检查提示后重试。",
          details:{command_id:String(id),error:String(error?.message || error)},
        });
        return Object.freeze({ok:false,status:"failed",command,error});
      }
    }

    return Object.freeze({register,resolve,list,invoke,has:id => definitions.has(String(id)),size:() => definitions.size});
  }

  function createStatusCenter(options = {}) {
    const documentObject = options.document || global.document;
    const limit = Math.max(4, Number(options.limit) || 24);
    const records = new Map();
    let sequence = 0;

    function node(id) { return documentObject?.getElementById?.(id) || documentObject?.querySelector?.(`#${id}`) || null; }
    function prose(value) {
      const original = text(value);
      return typeof global.DS?.prose === "function" ? global.DS.prose(original, "操作信息包含未翻译内容，请展开查看原文") : original;
    }
    function visibleRecords() { return Array.from(records.values()).sort((a,b) => b.sequence-a.sequence); }

    function renderFooter(record) {
      if (record.projectToFooter === false) return;
      const target = node("status-bar");
      if (!target) return;
      target.replaceChildren?.();
      if (!target.replaceChildren) target.textContent = "";
      const body = documentObject.createElement("span");
      body.textContent = prose(record.message);
      body.className = "workspace-notice-summary";
      target.appendChild?.(body);
      target.dataset && (target.dataset.noticeKind = record.kind);
      target.title = record.details == null ? "" : "展开页面状态可查看技术详情";
    }

    function renderCenter() {
      const items = visibleRecords();
      const count = node("workspace-notice-count");
      if (count) count.textContent = items.length ? `${items.length} 条动态` : "无新动态";
      const list = node("workspace-notice-list");
      if (!list) return;
      list.replaceChildren?.();
      if (!list.replaceChildren) list.textContent = "";
      for (const record of items.slice(0, 8)) {
        const row = documentObject.createElement("article");
        row.className = `workspace-notice workspace-notice-${record.kind}`;
        row.dataset && (row.dataset.factKey = record.factKey);
        const message = documentObject.createElement("div");
        message.textContent = prose(record.message);
        row.appendChild?.(message);
        if (record.details != null) {
          const details = documentObject.createElement("details");
          const summary = documentObject.createElement("summary");
          summary.textContent = "技术详情";
          const pre = documentObject.createElement("pre");
          try { pre.textContent = JSON.stringify(record.details,null,2); }
          catch { pre.textContent = text(record.details); }
          details.append?.(summary,pre);row.appendChild?.(details);
        }
        list.appendChild?.(row);
      }
    }

    function publish(input = {}) {
      const factKey = text(input.factKey || input.fact_key || "legacy.operation") || "legacy.operation";
      const record = Object.freeze({
        factKey,
        kind:["success","warning","error","unknown","info"].includes(input.kind) ? input.kind : "info",
        message:text(input.message || "操作状态已更新"),
        details:input.details == null ? null : input.details,
        scope:text(input.scope || "application"),
        projectToFooter:input.projectToFooter !== false,
        sequence:++sequence,
      });
      records.delete(factKey);
      records.set(factKey, record);
      while (records.size > limit) records.delete(records.keys().next().value);
      renderFooter(record);renderCenter();
      return record;
    }

    function publishLegacy(message, details) {
      const raw = text(message);
      const kind = /失败|错误|异常|不可用|未完成/.test(raw) ? "error"
        : /警告|需要|尚未|不允许|失效/.test(raw) ? "warning"
        : /完成|成功|已保存|已加载|已连接/.test(raw) ? "success" : "info";
      const factKey = ["error","unknown"].includes(kind) ? `legacy.error.${raw}`
        : kind === "warning" ? `legacy.warning.${raw}` : "legacy.operation";
      return publish({factKey,kind,message:raw,details,scope:"operation"});
    }

    function remove(factKey) { const removed=records.delete(String(factKey));if(removed)renderCenter();return removed; }
    function clear() { records.clear();renderCenter(); }
    return Object.freeze({publish,publishLegacy,remove,clear,snapshot:visibleRecords,render:renderCenter});
  }

  function createAttentionShelf(options = {}) {
    const documentObject = options.document || global.document;
    let items = [];
    function render() {
      const shelf = documentObject?.getElementById?.("workspace-attention-shelf");
      const message = documentObject?.getElementById?.("workspace-attention-message");
      const count = documentObject?.getElementById?.("workspace-attention-count");
      if (!shelf || !message || !count) return;
      const ordered = [...items].sort((a,b) => (Number(b.priority)||0)-(Number(a.priority)||0));
      shelf.hidden = ordered.length === 0;
      message.textContent = ordered[0]?.message || "";
      count.textContent = ordered.length > 1 ? `另有 ${ordered.length-1} 项` : "";
    }
    function set(next) { items=Array.isArray(next)?next.filter(item=>item&&item.message):[];render(); }
    return Object.freeze({set,clear:()=>set([]),snapshot:()=>items.map(item=>({...item})),render});
  }

  global.AppWorkspaceKernel = Object.freeze({createCommandRegistry,createStatusCenter,createAttentionShelf});
})(window);
