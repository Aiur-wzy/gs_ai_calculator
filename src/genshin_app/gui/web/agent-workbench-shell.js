"use strict";
(function (root) {
  const STORAGE_KEY = "genshin.agent.workbench.shell.v1";
  const LEGACY_STORAGE_KEY = "genshin.agent.layout.v1";
  const STORAGE_VERSION = 1;
  const MAX_STORAGE_CHARS = 4096;
  const DEFAULTS = Object.freeze({
    width: 400,
    inspectorWidth: 340,
    inspectorVisible: true,
    settingsVisible: false,
    detailsVisible: false,
    chatVisible: true,
    viewMode: "docked",
  });

  const bool = (value, fallback) => typeof value === "boolean" ? value : fallback;
  const finite = value => typeof value === "number" && Number.isFinite(value);
  const cloneDefaults = () => ({ ...DEFAULTS });
  const safeParse = raw => {
    if (typeof raw !== "string" || !raw || raw.length > MAX_STORAGE_CHARS) return null;
    try { return JSON.parse(raw); } catch { return null; }
  };
  const sanitize = (value, fallback = DEFAULTS) => {
    const source = value && typeof value === "object" ? value : {};
    const settingsVisible = bool(source.settingsVisible, fallback.settingsVisible);
    return {
      width: finite(source.width) ? source.width : fallback.width,
      inspectorWidth: finite(source.inspectorWidth) ? source.inspectorWidth : fallback.inspectorWidth,
      inspectorVisible: bool(source.inspectorVisible, fallback.inspectorVisible),
      settingsVisible,
      // Session history now lives in the global application sidebar and task
      // details live in the Inspector.  Never resurrect the retired drawer
      // from an older preference payload.
      detailsVisible: false,
      chatVisible: bool(source.chatVisible, fallback.chatVisible),
      // `focus` was the pre-R8 full-screen overlay.  Migrate it to the
      // in-workspace page instead of keeping a second modal presentation.
      viewMode: source.viewMode === "page" || source.viewMode === "focus" ? "page" : "docked",
    };
  };
  const readPreferences = storage => {
    let current = null;
    try { current = safeParse(storage?.getItem?.(STORAGE_KEY)); } catch {}
    if (current?.version === STORAGE_VERSION && current.preferences && typeof current.preferences === "object") {
      return sanitize(current.preferences);
    }
    let legacy = null;
    try { legacy = safeParse(storage?.getItem?.(LEGACY_STORAGE_KEY)); } catch {}
    if (legacy && typeof legacy === "object") {
      return sanitize({
        width: legacy.width,
        settingsVisible: legacy.settingsVisible,
        detailsVisible: false,
        chatVisible: legacy.chatVisible,
        viewMode: "docked",
      });
    }
    return cloneDefaults();
  };
  const writePreferences = (storage, state) => {
    const payload = JSON.stringify({ version: STORAGE_VERSION, preferences: sanitize(state) });
    if (payload.length > MAX_STORAGE_CHARS) return false;
    try { storage?.setItem?.(STORAGE_KEY, payload); return true; } catch { return false; }
  };
  const classToggle = (node, name, enabled) => node?.classList?.toggle?.(name, !!enabled);
  const styleProperty = (style, name, value) => {
    if (!style) return;
    if (typeof style.setProperty === "function") style.setProperty(name, value);
    else style[name] = value;
  };
  const FOCUSABLE = "button:not([disabled]),input:not([disabled]),select:not([disabled]),textarea:not([disabled]),a[href],summary,[tabindex]:not([tabindex='-1'])";

  function createController(options = {}) {
    const target = options.window || root;
    const document = options.document || target?.document;
    const storage = options.storage || target?.localStorage;
    const state = sanitize(options.initial || readPreferences(storage));
    const refs = {};
    let drag = null;
    let inspectorDrag = null;
    let mobileInspectorOpen = false;
    let bound = false;

    const limits = () => {
      const viewport = Number(target?.innerWidth) || Number(document?.documentElement?.clientWidth) || Number(document?.body?.clientWidth) || 1280;
      const available = Math.max(120, viewport - 24), min = Math.min(320, available);
      const max = Math.max(min, Math.min(880, viewport > 1179 ? viewport - 560 : available));
      return { min, max, viewport };
    };
    const clampWidth = width => {
      const range = limits();
      return Math.round(Math.min(range.max, Math.max(range.min, finite(Number(width)) ? Number(width) : DEFAULTS.width)));
    };
    const inspectorLimits = () => ({ min: 280, max: 400 });
    const clampInspectorWidth = width => {
      const range = inspectorLimits();
      return Math.round(Math.min(range.max, Math.max(range.min, finite(Number(width)) ? Number(width) : DEFAULTS.inspectorWidth)));
    };
    const persist = () => writePreferences(storage, state);
    const requestReflow = () => {
      const paint = () => options.onReflow?.();
      if (typeof target?.requestAnimationFrame === "function") target.requestAnimationFrame(paint);
      else paint();
    };
    const updateToggle = (toggle, visible, shown, hidden) => {
      if (!toggle) return;
      toggle.setAttribute?.("aria-pressed", String(!!visible));
      toggle.setAttribute?.("title", visible ? hidden : shown);
    };
    const moveRoot = targetNode => {
      if (!refs.root || !targetNode || refs.root.parentNode === targetNode || refs.root.parent === targetNode) return;
      if (typeof targetNode.append === "function") targetNode.append(refs.root);
      else if (typeof targetNode.appendChild === "function") targetNode.appendChild(refs.root);
    };
    const setPaneAccessibility = (pane, visible, label) => {
      if (!pane) return;
      pane.setAttribute?.("aria-hidden", String(!visible));
      pane.setAttribute?.("role", "region");
      pane.removeAttribute?.("aria-modal");
      if (label) pane.setAttribute?.("aria-label", label);
    };
    const apply = (save = false) => {
      state.width = clampWidth(state.width);
      state.inspectorWidth = clampInspectorWidth(state.inspectorWidth);
      styleProperty(refs.body?.style, "--agent-chat-width", `${state.width}px`);
      styleProperty(refs.root?.style, "--agent-inspector-width", `${state.inspectorWidth}px`);
      const page = state.viewMode === "page";
      const mobilePage = page && limits().viewport <= 959;
      if (!mobilePage) mobileInspectorOpen = false;
      if (refs.settingsPane) refs.settingsPane.hidden = !state.settingsVisible;
      if (refs.detailsPane) refs.detailsPane.hidden = !state.detailsVisible;
      if (refs.chatPane) refs.chatPane.hidden = !state.chatVisible;
      const inspectorShown = !page || (mobilePage ? mobileInspectorOpen : state.inspectorVisible);
      if (refs.inspectorPane) refs.inspectorPane.hidden = !inspectorShown;
      if (refs.inspectorScrim) refs.inspectorScrim.hidden = !mobilePage || !mobileInspectorOpen;
      setPaneAccessibility(refs.settingsPane, state.settingsVisible, "Agent 设置");
      setPaneAccessibility(refs.detailsPane, state.detailsVisible, "Agent 任务详情");
      setPaneAccessibility(refs.inspectorPane, inspectorShown, "Agent 活动、文件与详情");
      refs.chatPane?.setAttribute?.("aria-hidden", String(!state.chatVisible));
      classToggle(refs.root, "ac-settings-open", state.settingsVisible);
      classToggle(refs.root, "ac-details-open", state.detailsVisible);
      classToggle(refs.root, "ac-chat-hidden", !state.chatVisible);
      moveRoot(page ? refs.pageHost : refs.dockHost || refs.body);
      classToggle(refs.root, "ac-page-mode", page);
      classToggle(refs.root, "ac-docked-mode", !page);
      classToggle(refs.root, "ac-inspector-collapsed", page && !inspectorShown);
      classToggle(refs.root, "ac-inspector-mobile-open", mobilePage && mobileInspectorOpen);
      classToggle(refs.body, "agent-chat-page-mode", page);
      classToggle(refs.body, "agent-chat-content-hidden", !state.settingsVisible && !state.detailsVisible && !state.chatVisible);
      if (refs.root) {
        refs.root.setAttribute?.("role", page ? "region" : "complementary");
        refs.root.setAttribute?.("aria-label", page ? "AI 排轴助手工作区" : "AI 排轴助手侧边栏");
        refs.root.removeAttribute?.("aria-modal");
      }
      updateToggle(refs.settingsToggle, state.settingsVisible, "显示 Agent 设置", "隐藏 Agent 设置");
      updateToggle(refs.detailsToggle, state.detailsVisible, "显示任务详情", "隐藏任务详情");
      updateToggle(refs.chatToggle, state.chatVisible, "显示对话区", "隐藏对话区");
      updateToggle(refs.inspectorToggle, inspectorShown, "显示检查器", "隐藏检查器");
      if (refs.inspectorToggle) refs.inspectorToggle.textContent = inspectorShown ? "检查器" : "显示检查器";
      // Dock mode always exposes the compact status projection.  Controls
      // that only affect the full-page Inspector must not claim otherwise.
      if (refs.inspectorToggle) refs.inspectorToggle.hidden = !page;
      if (refs.detailsToggle) refs.detailsToggle.hidden = !page;
      if (refs.chatToggle) refs.chatToggle.textContent = state.chatVisible ? "隐藏对话" : "显示对话";
      if (refs.modeToggle) {
        refs.modeToggle.setAttribute?.("aria-pressed", String(page));
        refs.modeToggle.setAttribute?.("title", page ? "缩小为可调宽的侧边助手" : "在完整页面中打开助手");
        refs.modeToggle.textContent = page ? "侧边小窗" : "完整页面";
      }
      if (refs.resizer) {
        const range = limits();
        refs.resizer.setAttribute?.("role", "separator");
        refs.resizer.setAttribute?.("tabindex", "0");
        refs.resizer.setAttribute?.("aria-label", "调整 Agent 面板宽度");
        refs.resizer.setAttribute?.("aria-orientation", "vertical");
        refs.resizer.setAttribute?.("aria-valuemin", range.min);
        refs.resizer.setAttribute?.("aria-valuemax", range.max);
        refs.resizer.setAttribute?.("aria-valuenow", state.width);
        refs.resizer.setAttribute?.("aria-disabled", String(page));
      }
      if (refs.inspectorResizer) {
        const range = inspectorLimits();
        refs.inspectorResizer.hidden = !page || mobilePage || !inspectorShown;
        refs.inspectorResizer.setAttribute?.("role", "separator");
        refs.inspectorResizer.setAttribute?.("tabindex", "0");
        refs.inspectorResizer.setAttribute?.("aria-label", "调整 Agent 检查器宽度");
        refs.inspectorResizer.setAttribute?.("aria-orientation", "vertical");
        refs.inspectorResizer.setAttribute?.("aria-valuemin", range.min);
        refs.inspectorResizer.setAttribute?.("aria-valuemax", range.max);
        refs.inspectorResizer.setAttribute?.("aria-valuenow", state.inspectorWidth);
      }
      requestReflow();
      if (save) persist();
      return state;
    };
    const focusAfterHide = (pane, toggle) => {
      const active = document?.activeElement;
      if (active && pane?.contains?.(active)) toggle?.focus?.();
    };
    const setSection = (section, visible, save = true) => {
      if (section === "inspector" && state.viewMode === "page" && limits().viewport <= 959) {
        mobileInspectorOpen = !!visible;
        apply(false);
        if (mobileInspectorOpen) refs.inspectorFocus?.focus?.();
        return true;
      }
      const map = {
        settings: ["settingsVisible", refs.settingsPane, refs.settingsToggle],
        details: ["detailsVisible", refs.detailsPane, refs.detailsToggle],
        chat: ["chatVisible", refs.chatPane, refs.chatToggle],
        inspector: ["inspectorVisible", refs.inspectorPane, refs.inspectorToggle],
      };
      const entry = map[section];
      if (!entry) return false;
      const [key, pane, toggle] = entry;
      const next = !!visible;
      if (!next) focusAfterHide(pane, toggle);
      state[key] = next;
      if (next && section === "settings") state.detailsVisible = false;
      if (next && section === "details") state.settingsVisible = false;
      apply(save);
      if (next) (section === "settings" ? refs.settingsFocus : section === "details" ? refs.detailsFocus : section === "inspector" ? refs.inspectorFocus : refs.composerInput)?.focus?.();
      return true;
    };
    const toggleSection = section => {
      if (section === "inspector" && state.viewMode === "page" && limits().viewport <= 959) return setSection(section, !mobileInspectorOpen, false);
      const key = section === "settings" ? "settingsVisible" : section === "details" ? "detailsVisible" : section === "chat" ? "chatVisible" : section === "inspector" ? "inspectorVisible" : null;
      return key ? setSection(section, !state[key], true) : false;
    };
    const resize = (width, save = true) => { state.width = width; apply(save); return state.width; };
    const beginResize = event => {
      if (state.viewMode === "page" || (event?.button !== undefined && event.button !== 0)) return false;
      const x = Number(event?.clientX); if (!Number.isFinite(x)) return false;
      event?.preventDefault?.();
      drag = { x, width: state.width, pointerId: event?.pointerId };
      classToggle(refs.body, "agent-chat-resizing", true);
      try { refs.resizer?.setPointerCapture?.(event?.pointerId); } catch {}
      return true;
    };
    const moveResize = event => {
      const x = Number(event?.clientX);
      if (!drag || !Number.isFinite(x) || (drag.pointerId !== undefined && event?.pointerId !== undefined && drag.pointerId !== event.pointerId)) return false;
      event?.preventDefault?.(); resize(drag.width + drag.x - x, false); return true;
    };
    const endResize = event => {
      if (!drag || (drag.pointerId !== undefined && event?.pointerId !== undefined && drag.pointerId !== event.pointerId)) return false;
      const pointerId = event?.pointerId ?? drag.pointerId; drag = null;
      classToggle(refs.body, "agent-chat-resizing", false);
      try { refs.resizer?.releasePointerCapture?.(pointerId); } catch {}
      apply(true); return true;
    };
    const resizeKey = event => {
      if (state.viewMode === "page") return false;
      const range = limits(); let width = state.width;
      if (event?.key === "ArrowLeft") width += 16;
      else if (event?.key === "ArrowRight") width -= 16;
      else if (event?.key === "Home") width = DEFAULTS.width;
      else if (event?.key === "End") width = range.max;
      else return false;
      event?.preventDefault?.(); resize(width, true); return true;
    };
    const resizeInspector = (width, save = true) => { state.inspectorWidth = width; apply(save); return state.inspectorWidth; };
    const beginInspectorResize = event => {
      if (state.viewMode !== "page" || !state.inspectorVisible || (event?.button !== undefined && event.button !== 0)) return false;
      const x = Number(event?.clientX); if (!Number.isFinite(x)) return false;
      event?.preventDefault?.();
      inspectorDrag = { x, width: state.inspectorWidth, pointerId: event?.pointerId };
      classToggle(refs.body, "agent-inspector-resizing", true);
      try { refs.inspectorResizer?.setPointerCapture?.(event?.pointerId); } catch {}
      return true;
    };
    const moveInspectorResize = event => {
      const x = Number(event?.clientX);
      if (!inspectorDrag || !Number.isFinite(x) || (inspectorDrag.pointerId !== undefined && event?.pointerId !== undefined && inspectorDrag.pointerId !== event.pointerId)) return false;
      event?.preventDefault?.(); resizeInspector(inspectorDrag.width + inspectorDrag.x - x, false); return true;
    };
    const endInspectorResize = event => {
      if (!inspectorDrag || (inspectorDrag.pointerId !== undefined && event?.pointerId !== undefined && inspectorDrag.pointerId !== event.pointerId)) return false;
      const pointerId = event?.pointerId ?? inspectorDrag.pointerId; inspectorDrag = null;
      classToggle(refs.body, "agent-inspector-resizing", false);
      try { refs.inspectorResizer?.releasePointerCapture?.(pointerId); } catch {}
      apply(true); return true;
    };
    const inspectorResizeKey = event => {
      if (state.viewMode !== "page" || !state.inspectorVisible) return false;
      let width = state.inspectorWidth;
      if (event?.key === "ArrowLeft") width += 16;
      else if (event?.key === "ArrowRight") width -= 16;
      else if (event?.key === "Home") width = DEFAULTS.inspectorWidth;
      else if (event?.key === "End") width = inspectorLimits().max;
      else return false;
      event?.preventDefault?.(); resizeInspector(width, true); return true;
    };
    const setMode = (mode, save = true) => {
      state.viewMode = mode === "page" ? "page" : "docked";
      apply(save); refs.composerInput?.focus?.(); return state.viewMode;
    };
    const toggleMode = () => setMode(state.viewMode === "page" ? "docked" : "page", true);
    const setOpen = open => {
      const restoreFocus = !open && refs.root?.contains?.(document?.activeElement);
      if (refs.root) refs.root.hidden = !open;
      classToggle(refs.body, "agent-chat-open", open);
      refs.badge?.setAttribute?.("aria-expanded", String(!!open));
      if (open) apply(false);
      else if (restoreFocus) refs.badge?.focus?.();
    };
    const focusableNodes = scope => {
      if (!scope || typeof scope.querySelectorAll !== "function") return [];
      return Array.from(scope.querySelectorAll(FOCUSABLE)).filter(node =>
        node && node.hidden !== true && node.disabled !== true
        && node.getAttribute?.("aria-hidden") !== "true"
        && !node.closest?.("[hidden]")
      );
    };
    const trapFocus = event => {
      if (event?.key !== "Tab" || refs.root?.hidden === true) return false;
      // The page and dock are ordinary application regions rather than modal
      // dialogs.  Keep this hook for compatibility, but never trap users in a
      // panel as they move between the timeline and the assistant.
      const scope = null;
      const rows = focusableNodes(scope);
      if (!rows.length) return false;
      const current = rows.indexOf(document?.activeElement);
      if (event.shiftKey && current <= 0) {
        event.preventDefault?.(); rows.at(-1).focus?.(); return true;
      }
      if (!event.shiftKey && (current < 0 || current === rows.length - 1)) {
        event.preventDefault?.(); rows[0].focus?.(); return true;
      }
      return false;
    };
    const bind = nextRefs => {
      Object.assign(refs, nextRefs || {});
      if (!bound) {
        bound = true;
        refs.resizer?.addEventListener?.("pointerdown", beginResize);
        refs.resizer?.addEventListener?.("keydown", resizeKey);
        refs.resizer?.addEventListener?.("dblclick", () => resize(DEFAULTS.width, true));
        refs.inspectorResizer?.addEventListener?.("pointerdown", beginInspectorResize);
        refs.inspectorResizer?.addEventListener?.("keydown", inspectorResizeKey);
        refs.inspectorResizer?.addEventListener?.("dblclick", () => resizeInspector(DEFAULTS.inspectorWidth, true));
        refs.inspectorScrim?.addEventListener?.("click", () => setSection("inspector", false, false));
        target?.addEventListener?.("pointermove", moveResize);
        target?.addEventListener?.("pointermove", moveInspectorResize);
        target?.addEventListener?.("pointerup", endResize);
        target?.addEventListener?.("pointerup", endInspectorResize);
        target?.addEventListener?.("pointercancel", endResize);
        target?.addEventListener?.("pointercancel", endInspectorResize);
        target?.addEventListener?.("resize", () => apply(false));
        target?.addEventListener?.("keydown", event => {
          if (trapFocus(event)) return;
          if (event?.key !== "Escape") return;
          if (mobileInspectorOpen) { event.preventDefault?.(); setSection("inspector", false, false); }
          else if (state.detailsVisible) { event.preventDefault?.(); setSection("details", false, true); }
          else if (state.settingsVisible) { event.preventDefault?.(); setSection("settings", false, true); }
          else if (state.viewMode === "page") {
            event.preventDefault?.();
            if (typeof options.onRequestModeToggle === "function") options.onRequestModeToggle();
            else toggleMode();
          }
        });
      }
      apply(false);
      return controller;
    };
    const controller = Object.freeze({ state, refs, limits, inspectorLimits, apply, persist, bind, setSection, toggleSection, resize, beginResize, moveResize, endResize, resizeKey, resizeInspector, beginInspectorResize, moveInspectorResize, endInspectorResize, inspectorResizeKey, setMode, toggleMode, setOpen, trapFocus });
    return controller;
  }

  root.AgentWorkbenchShell = Object.freeze({
    STORAGE_KEY,
    LEGACY_STORAGE_KEY,
    STORAGE_VERSION,
    MAX_STORAGE_CHARS,
    DEFAULTS,
    sanitize,
    readPreferences,
    writePreferences,
    createController,
  });
})(globalThis);
