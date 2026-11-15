(function (global) {
  "use strict";

  const STORAGE_KEY = "genshin.agent.workbench.feedback.v1";
  const ACTIVE_STATES = new Set([
    "starting", "reconnecting", "opening", "preparing", "streaming",
    "using_tool", "resuming", "promoting", "cancelling", "stopping",
  ]);
  const TERMINALS = new Set(["completed", "complete", "finished", "failed", "error", "cancelled", "interrupted"]);
  const DETAILS = new Set(["compact", "standard", "full"]);
  const MOTION = new Set(["system", "reduce", "full"]);
  const NOTIFICATIONS = new Set(["all", "important", "off"]);
  const MAX_TOASTS = 3;
  const ACTIVITY_DELAY_MS = 250;
  const TICK_MS = 1000;
  const ANNOUNCE_THROTTLE_MS = 750;

  const defaults = Object.freeze({details:"standard", motion:"system", notifications:"all"});
  const safeText = (value, limit = 256) => typeof value === "string" ? value.slice(0, limit) : "";
  const safeInteger = value => Number.isSafeInteger(value) && value >= 0 ? value : null;
  const elapsedText = milliseconds => {
    const seconds = Math.max(0, Math.floor(milliseconds / 1000));
    if (seconds < 60) return `${seconds}秒`;
    const minutes = Math.floor(seconds / 60), rest = seconds % 60;
    return `${minutes}分${String(rest).padStart(2, "0")}秒`;
  };
  const timestampText = milliseconds => {
    if (!Number.isFinite(milliseconds) || milliseconds <= 0) return "尚无成功记录";
    try { return new Date(milliseconds).toLocaleTimeString("zh-CN", {hour:"2-digit", minute:"2-digit", second:"2-digit"}); }
    catch { return "时间不可用"; }
  };
  const element = (document, tag, value = null, className = "") => {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (value !== null) node.textContent = String(value);
    return node;
  };

  function readPreferences(storage) {
    let value = null;
    try { const raw = storage?.getItem?.(STORAGE_KEY); value = typeof raw === "string" && raw.length <= 4096 ? JSON.parse(raw) : null; } catch {}
    return Object.freeze({
      details:DETAILS.has(value?.details) ? value.details : defaults.details,
      motion:MOTION.has(value?.motion) ? value.motion : defaults.motion,
      notifications:NOTIFICATIONS.has(value?.notifications) ? value.notifications : defaults.notifications,
    });
  }

  function create(options = {}) {
    const document = options.document || global.document;
    const host = options.host, summaryHost = options.summaryHost, dockHost = options.dockHost;
    const groupHost = options.groupHost || null;
    const liveHost = options.liveHost, toastHost = options.toastHost;
    const visibilityHost = options.visibilityHost || host;
    if (!document || !host || !liveHost || !toastHost) throw new TypeError("agent_activity_feedback_host_invalid");
    const storage = options.storage || global.localStorage;
    const clock = typeof options.clock === "function" ? options.clock : () => Date.now();
    const setTimer = typeof options.setTimer === "function" ? options.setTimer : (callback, delay) => global.setTimeout(callback, delay);
    const clearTimer = typeof options.clearTimer === "function" ? options.clearTimer : timer => global.clearTimeout(timer);
    const onOpenSession = typeof options.onOpenSession === "function" ? options.onOpenSession : () => {};
    const onPreferences = typeof options.onPreferences === "function" ? options.onPreferences : () => {};
    let preferences = readPreferences(storage);
    let activity = null, delayTimer = null, tickTimer = null, visible = false;
    let lastAnnouncement = "", lastAnnouncementAt = 0, pendingAnnouncement = null, announceTimer = null;
    let unreadCount = 0, toastSequence = 0;
    const toastTimers = new Map();
    const sessionStates = new Map(), lastTransportSuccess = new Map();

    const persist = () => {
      try { storage?.setItem?.(STORAGE_KEY, JSON.stringify(preferences)); return true; }
      catch { return false; }
    };
    const applyPreferences = () => {
      host.dataset.detailLevel = preferences.details;
      host.dataset.motion = preferences.motion;
      host.dataset.notificationPolicy = preferences.notifications;
      onPreferences(preferences);
    };
    const announceNow = value => {
      const message = safeText(value, 500);
      if (!message || message === lastAnnouncement) return;
      lastAnnouncement = message; lastAnnouncementAt = clock(); liveHost.textContent = message;
    };
    const announce = value => {
      const now = clock(), remaining = ANNOUNCE_THROTTLE_MS - (now - lastAnnouncementAt);
      if (!lastAnnouncement || remaining <= 0) { if (announceTimer !== null) clearTimer(announceTimer); announceTimer = null; pendingAnnouncement = null; announceNow(value); return; }
      pendingAnnouncement = value;
      if (announceTimer !== null) return;
      announceTimer = setTimer(() => { announceTimer = null; const next = pendingAnnouncement; pendingAnnouncement = null; announceNow(next); }, remaining);
    };
    const clearActivityTimers = () => {
      if (delayTimer !== null) clearTimer(delayTimer);
      if (tickTimer !== null) clearTimer(tickTimer);
      delayTimer = tickTimer = null;
    };
    const syncDock = active => {
      if (!dockHost) return;
      dockHost.dataset.activityVisible = String(active === true);
      if (groupHost) groupHost.hidden = active !== true;
      const attention = dockHost.dataset.hasAttention === "true";
      const alert = dockHost.dataset.hasAlert === "true";
      dockHost.hidden = !active && !attention && !alert;
    };
    const renderActivity = () => {
      if (tickTimer !== null) { clearTimer(tickTimer); tickTimer = null; }
      if (!activity || !visible) { host.hidden = true; host.replaceChildren(); return; }
      const now = clock(), runElapsed = now - activity.runStartedAt, phaseElapsed = now - activity.phaseStartedAt;
      host.hidden = false;
      host.dataset.state = activity.state;
      host.dataset.visible = "true";
      const title = element(document, "strong", activity.label || "Agent 正在工作");
      const facts = [`本轮已用 ${elapsedText(runElapsed)}`];
      if (preferences.details !== "compact") facts.push(
        `当前阶段 ${elapsedText(phaseElapsed)}`,
        activity.cursor === null ? "同步位置未知" : `同步位置 #${activity.cursor}`,
      );
      if (activity.lastSuccessAt || activity.state === "reconnecting") facts.push(`最近成功 ${timestampText(activity.lastSuccessAt)}`);
      if (preferences.details === "full") facts.push(`状态 ${activity.state}`, `阶段 ${activity.phase}`, `会话 ${activity.sessionId}`);
      host.replaceChildren(title, element(document, "span", facts.join(" · "), "ac-activity-facts"));
      if (summaryHost) summaryHost.textContent = `${activity.label || "Agent 正在工作"} · ${elapsedText(runElapsed)}`;
      syncDock(true);
      tickTimer = setTimer(() => { tickTimer = null; renderActivity(); }, TICK_MS);
    };
    const scheduleVisible = () => {
      if (visible || delayTimer !== null) return;
      delayTimer = setTimer(() => {
        delayTimer = null;
        if (!activity) return;
        visible = true; host.dataset.visible = "true"; visibilityHost.dataset.activityVisible = "true"; renderActivity();
      }, ACTIVITY_DELAY_MS);
    };
    const markTransportSuccess = (sessionId, at = clock()) => {
      const id = safeText(sessionId, 256), timestamp = safeInteger(at);
      if (!id || timestamp === null) return false;
      lastTransportSuccess.set(id, timestamp);
      if (activity?.sessionId === id) activity.lastSuccessAt = timestamp;
      return true;
    };
    const observeActivity = value => {
      const sessionId = safeText(value?.session_id, 256) || "global";
      const state = safeText(value?.state, 64);
      const phase = safeText(value?.phase, 128) || state;
      const active = value?.active === true || ACTIVE_STATES.has(state);
      const cursor = value?.cursor == null ? null : safeInteger(value.cursor);
      if (!active) {
        const prior = activity;
        activity = null; visible = false; clearActivityTimers(); host.hidden = true; host.dataset.visible = "false"; visibilityHost.dataset.activityVisible = "false"; host.replaceChildren(); syncDock(false);
        if (prior && TERMINALS.has(state)) announce(state === "failed" || state === "error" ? "Agent 运行失败" : "Agent 本轮已结束");
        return Object.freeze({active:false, visible:false});
      }
      const now = clock(), key = `${sessionId}:${state}:${phase}`;
      if (!activity || activity.sessionId !== sessionId) {
        clearActivityTimers(); visible = false;
        activity = {sessionId, key, state, phase, label:safeText(value?.label, 256), cursor, runStartedAt:safeInteger(value?.started_at_ms) ?? now, phaseStartedAt:now, lastSuccessAt:lastTransportSuccess.get(sessionId) || null};
      } else {
        if (activity.key !== key) { activity.key = key; activity.state = state; activity.phase = phase; activity.phaseStartedAt = now; announce(safeText(value?.label, 256) || phase); }
        activity.label = safeText(value?.label, 256) || activity.label;
        activity.cursor = cursor;
        activity.lastSuccessAt = lastTransportSuccess.get(sessionId) || activity.lastSuccessAt;
      }
      syncDock(true);
      if (visible) renderActivity(); else scheduleVisible();
      return Object.freeze({active:true, visible, run_started_at:activity.runStartedAt, phase_started_at:activity.phaseStartedAt});
    };

    const shouldNotify = type => preferences.notifications === "all" || preferences.notifications === "important" && type !== "completed";
    const pushToast = ({session_id, title, type, message}) => {
      if (!shouldNotify(type)) return false;
      const toast = element(document, "button", null, `ac-agent-toast ac-agent-toast-${type}`);
      toast.type = "button"; toast.dataset.toastId = `toast-${++toastSequence}`; toast.dataset.sessionId = session_id;
      toast.append(element(document, "strong", title || "Agent 任务"), element(document, "span", message));
      let timer = null;
      toast.addEventListener("click", () => { const activeTimer = toastTimers.get(toast); if (activeTimer !== undefined) { clearTimer(activeTimer); toastTimers.delete(toast); } onOpenSession(session_id); toast.remove(); });
      toastHost.append(toast);
      while (toastHost.children.length > MAX_TOASTS) { const oldest = toastHost.children[0], oldestTimer = toastTimers.get(oldest); if (oldestTimer !== undefined) clearTimer(oldestTimer); toastTimers.delete(oldest); oldest.remove(); }
      timer = setTimer(() => { toastTimers.delete(toast); toast.remove(); }, 8000);
      toastTimers.set(toast, timer);
      announce(`${title || "Agent 任务"}：${message}`);
      return true;
    };
    const observeSession = value => {
      const sessionId = safeText(value?.session_id, 256), state = safeText(value?.state, 64);
      if (!sessionId || !state) return false;
      const questionOpen = value?.question_open === true;
      const next = {state, questionOpen};
      const prior = sessionStates.get(sessionId); sessionStates.set(sessionId, next);
      if ((!prior && value?.transition !== true) || (value?.selected === true && value?.panel_open === true)) return false;
      const background = value?.selected !== true || value?.panel_open !== true;
      if (!background) return false;
      let type = null, message = "";
      if (!prior?.questionOpen && questionOpen) { type = "waiting"; message = "正在等待你的回答"; }
      else if (prior?.state !== state && ["failed", "error", "interrupted"].includes(state)) { type = "failed"; message = state === "interrupted" ? "任务已中断" : "任务运行失败"; }
      else if (prior?.state !== state && ["completed", "complete", "finished"].includes(state)) { type = "completed"; message = "任务已完成"; }
      if (!type) return false;
      return pushToast({session_id:sessionId, title:safeText(value?.title, 120) || "Agent 任务", type, message});
    };
    const setUnreadCount = value => { unreadCount = Math.max(0, safeInteger(value) ?? 0); return unreadCount; };
    const setPreference = (name, value) => {
      const next = {...preferences};
      if (name === "details" && DETAILS.has(value)) next.details = value;
      else if (name === "motion" && MOTION.has(value)) next.motion = value;
      else if (name === "notifications" && NOTIFICATIONS.has(value)) next.notifications = value;
      else return false;
      preferences = Object.freeze(next); persist(); applyPreferences(); return true;
    };
    const destroy = () => {
      activity = null; visible = false; clearActivityTimers();
      if (announceTimer !== null) clearTimer(announceTimer);
      announceTimer = null; for (const timer of toastTimers.values()) clearTimer(timer); toastTimers.clear();
      toastHost.replaceChildren(); host.replaceChildren(); visibilityHost.dataset.activityVisible = "false"; syncDock(false);
    };
    applyPreferences(); host.hidden = true; host.dataset.visible = "false"; visibilityHost.dataset.activityVisible = "false"; syncDock(false);
    return Object.freeze({
      observeActivity, observeSession, markTransportSuccess, setUnreadCount,
      setPreference, preferences:() => preferences,
      snapshot:() => Object.freeze({preferences, activity:activity ? {...activity, visible} : null, unread_count:unreadCount, last_transport_success:Object.fromEntries(lastTransportSuccess)}),
      pushToast, destroy,
    });
  }

  global.AgentActivityFeedback = Object.freeze({
    STORAGE_KEY, ACTIVE_STATES, ACTIVITY_DELAY_MS, ANNOUNCE_THROTTLE_MS,
    defaults, readPreferences, elapsedText, create,
  });
})(window);
