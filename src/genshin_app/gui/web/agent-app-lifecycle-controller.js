(function (global) {
  "use strict";

  const required = (options, name) => {
    const value = options[name];
    if (typeof value !== "function") throw new TypeError(`app_lifecycle_${name}_missing`);
    return value;
  };

  function create(options = {}) {
    const state = required(options, "state");
    const api = required(options, "api");
    const leaveWhiteboard = required(options, "leaveWhiteboard");
    const leavePlan = required(options, "leavePlan");
    const active = required(options, "active");
    const confirm = required(options, "confirm");
    const stopRuntimeStatusMonitor = required(options, "stopRuntimeStatusMonitor");
    const invalidateTransport = required(options, "invalidateTransport");
    const clearStore = required(options, "clearStore");
    const clearTimer = required(options, "clearTimer");
    const sessions = required(options, "sessions");
    const notice = required(options, "notice");
    const errorText = required(options, "errorText");
    const reopen = required(options, "reopen");
    let pending = null;
    let shutdownComplete = false;
    let cleanupComplete = false;
    let closeComplete = false;

    function cleanup(local) {
      if (cleanupComplete) return;
      // Fence every poll owner before the native close await yields back to the
      // event loop.  The transport/store cleanup is intentionally owned here.
      stopRuntimeStatusMonitor();
      invalidateTransport({drop:true});
      clearStore();
      for (const session of sessions()) {
        if (session.timer !== null && session.timer !== undefined) clearTimer(session.timer);
        session.timer = null;
        session.active = false;
        session.epoch++;
      }
      local.request++;
      cleanupComplete = true;
    }

    async function execute() {
      const local = state();
      try {
        if (!await leaveWhiteboard()) return false;
        if (!await leavePlan()) return false;
        if (!shutdownComplete) {
          if ((active() || ["send", "runtime-start"].includes(local.busy))
              && !confirm("Agent 正在启动或运行。停止本 App 的 Agent 任务并关闭窗口？已保存的消息与候选会保留。取消将继续运行。")) return false;
          const stopped = await api("agent_shutdown");
          if (stopped.code) {
            notice(errorText(stopped, "Agent 进程尚未确认退出，窗口保持打开。"), true);
            reopen();
            return false;
          }
          shutdownComplete = true;
        }
        cleanup(local);
        const closed = await api("confirm_close");
        if (closed.code) {
          notice(errorText(closed, "窗口关闭未被确认；Agent 已停止，窗口保持打开。"), true);
          reopen();
          return false;
        }
        closeComplete = true;
        return true;
      } catch (error) {
        notice(errorText(error, "关闭流程未完成，窗口保持打开。"), true);
        reopen();
        return false;
      }
    }

    function requestClose() {
      const local = state();
      if (closeComplete || local.closing || pending) return false;
      local.closing = true;
      const owner = {};
      pending = {owner, promise:null};
      const promise = execute().finally(() => {
        if (pending?.owner === owner) pending = null;
        local.closing = false;
      });
      pending.promise = promise;
      return false;
    }

    return Object.freeze({requestClose, settle:() => pending?.promise || Promise.resolve(closeComplete), isClosing:() => pending !== null});
  }

  global.AgentAppLifecycleController = Object.freeze({create});
})(window);
