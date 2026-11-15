"use strict";
(function (root) {
  const STORAGE_KEY = "genshin.agent.composer.v1";
  const STORAGE_VERSION = 1;
  const MAX_DRAFTS = 32;
  const MAX_DRAFT_CHARS = 256 * 1024;
  const MAX_TOTAL_CHARS = 2 * 1024 * 1024;
  const MAX_STORAGE_CHARS = 3 * 1024 * 1024;
  const SEND_MODES = Object.freeze(["enter", "ctrl_enter"]);
  const safeString = (value, limit) => typeof value === "string" ? value.slice(0, limit) : "";
  const safeKey = value => {
    const key = safeString(value, 192);
    return key && !/[\u0000-\u001f\u007f]/u.test(key) ? key : "new:whiteboard";
  };
  const emptyStore = () => ({ sendMode: "enter", drafts: [] });
  const safeParse = raw => {
    if (typeof raw !== "string" || !raw || raw.length > MAX_STORAGE_CHARS) return null;
    try { return JSON.parse(raw); } catch { return null; }
  };
  const sanitizeStore = value => {
    if (!value || typeof value !== "object" || value.version !== STORAGE_VERSION) return emptyStore();
    const sendMode = SEND_MODES.includes(value.preference?.sendMode) ? value.preference.sendMode : "enter";
    const rows = Array.isArray(value.drafts) ? value.drafts : [];
    const byKey = new Map();
    for (const row of rows) {
      if (!row || typeof row !== "object" || typeof row.key !== "string" || typeof row.text !== "string") continue;
      if (!row.key || row.key.length > 192 || /[\u0000-\u001f\u007f]/u.test(row.key) || row.text.length > MAX_DRAFT_CHARS) continue;
      const updatedAt = Number.isSafeInteger(row.updatedAt) && row.updatedAt >= 0 ? row.updatedAt : 0;
      const previous = byKey.get(row.key);
      if (!previous || previous.updatedAt <= updatedAt) byKey.set(row.key, { key: row.key, text: row.text, updatedAt });
    }
    let total = 0;
    const drafts = [...byKey.values()].sort((a, b) => b.updatedAt - a.updatedAt || a.key.localeCompare(b.key)).filter(row => {
      if (total + row.text.length > MAX_TOTAL_CHARS) return false;
      total += row.text.length; return true;
    }).slice(0, MAX_DRAFTS);
    return { sendMode, drafts };
  };
  const readStore = storage => {
    try { return sanitizeStore(safeParse(storage?.getItem?.(STORAGE_KEY))); } catch { return emptyStore(); }
  };
  const serialize = state => ({
    version: STORAGE_VERSION,
    preference: { sendMode: SEND_MODES.includes(state.sendMode) ? state.sendMode : "enter" },
    drafts: state.drafts.map(row => ({ key: row.key, text: row.text, updatedAt: row.updatedAt })),
  });
  const writeStore = (storage, state) => {
    const payload = JSON.stringify(serialize(state));
    if (payload.length > MAX_STORAGE_CHARS) return false;
    try { storage?.setItem?.(STORAGE_KEY, payload); return true; } catch { return false; }
  };
  const prepareOptimisticEcho = ({ clientRequestId, message, hostSupportsClientRequestId } = {}) => {
    if (hostSupportsClientRequestId !== true || typeof clientRequestId !== "string" || !clientRequestId || typeof message !== "string" || !message.trim()) return null;
    return Object.freeze({ kind: "local_user_echo", clientRequestId, message, status: "awaiting_host_acceptance" });
  };

  function createController(options = {}) {
    const storage = options.storage || root.localStorage;
    const document = options.document || root.document;
    const input = options.input;
    const sendButton = options.sendButton;
    const activeSendButton = options.activeSendButton;
    const stopButton = options.stopButton;
    const sendModeSelect = options.sendModeSelect;
    const contextHost = options.contextHost;
    const contextSummaryHost = options.contextSummaryHost;
    const rootNode = options.root;
    const state = readStore(storage);
    let currentKey = safeKey(options.contextKey || "new:whiteboard");
    let composing = false;
    const clock = typeof options.now === "function" ? options.now : () => Date.now();
    const find = key => state.drafts.find(row => row.key === key);
    const compact = () => {
      state.drafts.sort((a, b) => b.updatedAt - a.updatedAt || a.key.localeCompare(b.key));
      let total = 0;
      state.drafts = state.drafts.filter(row => {
        if (row.text.length > MAX_DRAFT_CHARS || total + row.text.length > MAX_TOTAL_CHARS) return false;
        total += row.text.length; return true;
      }).slice(0, MAX_DRAFTS);
    };
    const save = () => {
      if (!input) return false;
      const text = String(input.value ?? "");
      if (text.length > MAX_DRAFT_CHARS) {
        options.onStorageWarning?.(`当前未发送内容超过 ${MAX_DRAFT_CHARS.toLocaleString("zh-CN")} 字，仍保留在本窗口，但不会写入本地草稿。`);
        return false;
      }
      const old = find(currentKey), updatedAt = Number(clock()) || 0;
      if (text) {
        if (old) Object.assign(old, { text, updatedAt });
        else state.drafts.push({ key: currentKey, text, updatedAt });
      } else if (old) state.drafts.splice(state.drafts.indexOf(old), 1);
      compact();
      return writeStore(storage, state);
    };
    const autoGrow = () => {
      if (!input?.style) return;
      input.style.height = "auto";
      const min = Number(options.minHeight) || 64, max = Number(options.maxHeight) || 220;
      const measured = Number(input.scrollHeight);
      const height = Number.isFinite(measured) && measured > 0 ? Math.min(max, Math.max(min, measured)) : min;
      input.style.height = `${height}px`;
      input.style.overflowY = measured > max ? "auto" : "hidden";
    };
    const load = () => {
      if (!input) return "";
      input.value = find(currentKey)?.text || "";
      autoGrow();
      return input.value;
    };
    const switchContext = nextKey => {
      save(); currentKey = safeKey(nextKey); load(); return currentKey;
    };
    const clearIf = message => {
      if (!input || input.value.trim() !== String(message ?? "").trim()) return false;
      input.value = ""; save(); autoGrow(); return true;
    };
    const dropContext = key => {
      const safe = safeKey(key), row = find(safe);
      if (row) state.drafts.splice(state.drafts.indexOf(row), 1);
      if (safe === currentKey && input) input.value = "";
      writeStore(storage, state); autoGrow(); return !!row;
    };
    const setSendMode = mode => {
      state.sendMode = SEND_MODES.includes(mode) ? mode : "enter";
      if (sendModeSelect) sendModeSelect.value = state.sendMode;
      writeStore(storage, state);
      return state.sendMode;
    };
    const shouldSend = event => {
      if (event?.key !== "Enter" || event?.shiftKey || event?.altKey || event?.isComposing || composing || event?.keyCode === 229) return false;
      if (event.ctrlKey || event.metaKey) return true;
      return state.sendMode === "enter";
    };
    const onKeyDown = event => {
      if (!shouldSend(event)) return;
      if (stopButton && !stopButton.hidden && !activeSendButton) return;
      event.preventDefault?.();
      if (stopButton && !stopButton.hidden) {
        if (!activeSendButton?.disabled) options.onActiveSend?.();
        return;
      }
      if (!sendButton?.disabled) options.onSend?.();
    };
    const setContextChips = items => {
      if (!contextHost) return;
      contextHost.replaceChildren?.();
      const admitted = [];
      for (const item of (Array.isArray(items) ? items : []).slice(0, 8)) {
        if (!item || typeof item !== "object") continue;
        const label = safeString(item.label, 32), value = safeString(item.value, 96);
        if (!label && !value) continue;
        admitted.push({kind:safeString(item.kind || "context", 24), label, value});
        const chip = document?.createElement?.("span"); if (!chip) continue;
        chip.className = "ac-context-chip";
        chip.dataset.kind = safeString(item.kind || "context", 24);
        chip.textContent = label && value ? `${label}：${value}` : label || value;
        if (item.title) chip.setAttribute?.("title", safeString(item.title, 256));
        contextHost.append?.(chip);
      }
      if (contextSummaryHost) {
        const important = admitted.filter(item => !["revision", "scope"].includes(item.kind));
        const preferred = (important.length ? important : admitted).slice(0, 3);
        contextSummaryHost.textContent = preferred.length
          ? preferred.map(item => item.value || item.label).join(" · ")
          : "本次发送上下文";
        contextSummaryHost.title = admitted.map(item => item.label && item.value
          ? `${item.label}：${item.value}` : item.label || item.value).join("；");
      }
    };
    const setPrimary = ({ active = false, sendDisabled = false, activeSendDisabled = false, stopDisabled = false, sendLabel = "发送", activeSendLabel = "加入队列", stopLabel = "停止" } = {}) => {
      if (rootNode) rootNode.dataset.mode = active ? "stop" : "send";
      if (sendButton) { sendButton.hidden = !!active; sendButton.disabled = !!sendDisabled; sendButton.textContent = sendLabel; }
      if (activeSendButton) { activeSendButton.hidden = !active; activeSendButton.disabled = !!activeSendDisabled; activeSendButton.textContent = activeSendLabel; }
      if (stopButton) { stopButton.hidden = !active; stopButton.disabled = !!stopDisabled; stopButton.textContent = stopLabel; }
    };
    if (input) {
      input.addEventListener?.("compositionstart", () => { composing = true; });
      input.addEventListener?.("compositionend", () => { composing = false; });
      input.addEventListener?.("input", () => { save(); autoGrow(); });
      input.addEventListener?.("keydown", onKeyDown);
    }
    sendModeSelect?.addEventListener?.("change", () => setSendMode(sendModeSelect.value));
    if (sendModeSelect) sendModeSelect.value = state.sendMode;
    load();
    const controller = Object.freeze({ state, save, load, autoGrow, switchContext, clearIf, dropContext, setSendMode, shouldSend, setContextChips, setPrimary, focus: () => input?.focus?.(), contextKey: () => currentKey });
    return controller;
  }

  root.AgentComposer = Object.freeze({
    STORAGE_KEY,
    STORAGE_VERSION,
    MAX_DRAFTS,
    MAX_DRAFT_CHARS,
    MAX_TOTAL_CHARS,
    SEND_MODES,
    sanitizeStore,
    readStore,
    writeStore,
    prepareOptimisticEcho,
    createController,
  });
})(globalThis);
