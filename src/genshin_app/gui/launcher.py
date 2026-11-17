"""GUI 启动器：pywebview 窗口 + 服务自动化（G1）。"""

from __future__ import annotations

import json
import logging
import sys
import threading
from collections.abc import Callable, Mapping
from pathlib import Path

from genshin_app.database_bootstrap import (
    DatabaseStartupError,
    prepare_database_startup,
)
from genshin_app.database_startup_recovery import DatabaseStartupRecovery
from genshin_app.gui.api import GuiApi
from genshin_app.gui.service_manager import ServiceManager
from genshin_app.paths import ensure_data_dir, is_frozen, web_dir

_EXTERNAL_DATABASE_ACCEPTANCE_MODES = frozenset(
    {
        "development_acceptance",
        "provider_acceptance",
        "native_agent_development",
    }
)


def _shutdown_agent_transports(api):
    """Retire both transports during migration, even if one cleanup fails."""
    close_dsh = getattr(api, "_close_dsh_shell", None)
    try:
        if callable(close_dsh):
            close_dsh()
    finally:
        try:
            if getattr(api, "_agent_windows", None) is not None:
                api._agent_windows.shutdown()
        finally:
            api.agent_shutdown()


_DATABASE_STARTUP_HTML = """<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>正在启动原神拉表</title><style>
:root{color-scheme:dark;font-family:"Microsoft YaHei UI","Segoe UI",sans-serif}
body{margin:0;min-height:100vh;display:grid;place-items:center;background:#111827;color:#e5e7eb}
main{width:min(34rem,calc(100vw - 3rem));padding:2rem;border:1px solid #334155;
border-radius:14px;background:#1e293b;box-shadow:0 18px 48px #0008}
h1{font-size:1.25rem;margin:0 0 .75rem}.row{display:flex;gap:.8rem;align-items:center}
.spinner{width:1rem;height:1rem;border:.18rem solid #475569;border-top-color:#60a5fa;
border-radius:50%;animation:spin .9s linear infinite;flex:none}@keyframes spin{to{transform:rotate(360deg)}}
p{line-height:1.65;margin:.65rem 0;color:#cbd5e1}code{font-family:Consolas,monospace;color:#fca5a5}
#detail{font-size:.84rem;color:#94a3b8}body.failed .spinner{display:none}body.failed h1{color:#fca5a5}
</style></head><body><main><div class="row"><span class="spinner"></span>
<h1 id="title">正在校验数据库</h1></div><p id="message">正在读取本地发行凭据。</p>
<p id="detail">校验大型数据库可能需要几十秒；完成前不会启动计算引擎或 Agent。</p>
<div id="recovery" hidden><p id="recovery-version"></p>
<button id="inspect" onclick="window.pywebview.api.inspect_recovery()">检查配套数据库</button>
<button id="confirm" hidden onclick="window.pywebview.api.confirm_recovery(window.confirmationDigest,true)">确认安装</button>
<button id="decline" hidden onclick="window.pywebview.api.decline_recovery()">暂不安装</button>
<button id="cancel" hidden onclick="window.pywebview.api.cancel_recovery()">取消</button>
<button id="retry" onclick="window.pywebview.api.retry_startup()">重新检查</button>
</div><details id="diagnostic" hidden><summary>查看并复制诊断详情</summary><textarea id="diagnostic-text" readonly rows="9" style="width:100%;box-sizing:border-box"></textarea>
<button onclick="document.getElementById('diagnostic-text').select();document.execCommand('copy')">复制</button></details>
</main><script>
window.databaseStartupStatus = function (value) {
  if (!value || typeof value !== 'object') return;
  document.getElementById('message').textContent = value.message || '正在校验数据库';
};
window.databaseStartupFailure = function (value) {
  const needsCheck = value.status === 'database_check_required';
  document.body.classList.toggle('failed', !needsCheck);
  document.querySelector('.spinner').hidden = true;
  document.getElementById('title').textContent = needsCheck ? '需要检查配套数据库' : '数据库未通过启动校验';
  document.getElementById('message').textContent = value.message || '应用未启动。';
  document.getElementById('detail').innerHTML = needsCheck
    ? '状态码：<code></code>。点击下方“检查配套数据库”，再决定是否更新。'
    : '错误码：<code></code>。请查看诊断详情；如有可用恢复操作，可在下方处理。';
  document.querySelector('#detail code').textContent = value.reason || 'database_startup_failed';
};
window.databaseRecoveryStatus = function(value) {
  document.getElementById('message').textContent = value.message || '';
  document.querySelector('.spinner').hidden = !value.busy;
  const titles = {database_check_required:'需要检查配套数据库',checking:'正在检查配套数据库',
    awaiting_consent:'等待确认数据库版本',installing:'正在安装配套数据库',complete:'数据库已准备就绪',
    declined:'已保留当前数据库',cancelled:'已取消本次操作'};
  if (titles[value.status]) {
    document.body.classList.remove('failed');
    document.getElementById('title').textContent = titles[value.status];
  }
  document.getElementById('recovery').hidden = !(value.repair_available || value.confirmation || value.busy || value.status === 'declined');
  document.getElementById('inspect').disabled = !!value.busy;
  document.getElementById('retry').disabled = !!value.busy;
  document.getElementById('cancel').hidden = !value.busy;
  document.getElementById('confirm').hidden = value.status !== 'awaiting_consent';
  document.getElementById('decline').hidden = value.status !== 'awaiting_consent';
  document.getElementById('confirm').disabled = !!value.busy;
  if (value.confirmation) {
    window.confirmationDigest = value.confirmation.confirmation_digest;
    document.getElementById('recovery-version').textContent =
      (value.confirmation.current_database_release_id || '未安装') + ' → ' + value.confirmation.target_database_release_id + '。' + value.confirmation.message;
  }
  if (value.diagnostic) {
    document.getElementById('diagnostic').hidden = false;
    document.getElementById('diagnostic-text').value = JSON.stringify(value.diagnostic,null,2);
  }
};
</script></body></html>"""


_EDITOR_CLOSE_SCRIPT = (
    "typeof window.WB?.requestClose === 'function' ? window.WB.requestClose() : "
    "(typeof window.PL?.requestClose === 'function' ? window.PL.requestClose() : true)"
)


def install_close_guard(window, api: GuiApi) -> None:
    """Ask the editor outside the native closing callback's UI thread."""
    lock = threading.Lock()
    checking = False

    def request_close():
        nonlocal checking
        try:
            if window.evaluate_js(_EDITOR_CLOSE_SCRIPT) is True and not api._closing_approved:
                result = api.confirm_close()
                if isinstance(result, dict) and result.get("code"):
                    logging.getLogger(__name__).warning("应用关闭检查未完成: %s", result.get("message"))
                    window.create_confirmation_dialog("暂未退出应用", str(result.get("message") or "关闭检查尚未完成，请保存输入后重试。"))
        except Exception:
            if api._closing_approved:
                return
            logging.getLogger(__name__).exception("无法核对未保存输入，关闭请求未获批准")
            if api._editor_initialized:
                window.create_confirmation_dialog("无法核对未保存输入", "界面响应异常，本次关闭已取消。请先保存输入后再关闭。")
            else:
                api.confirm_close()
        finally:
            with lock:
                checking = False

    def before_close():
        nonlocal checking
        if api._closing_approved:
            return True
        with lock:
            if not checking:
                checking = True
                # WinForms closing runs on the UI thread. Waiting for the WebView
                # script there also blocks its completion callback on that thread.
                threading.Thread(target=request_close, name="genshin-close-check", daemon=True).start()
        return False

    window.events.closing += before_close


def run_gui(
    data_dir: Path,
    open_path: Path | None = None,
    *,
    database_path: Path | None = None,
    app_identity: Mapping | None = None,
    agent_manager_factory=None,
    dsh_shell_factory=None,
    dsh_result_save_dialog=None,
    dsh_file_dialog=None,
    main_window_callback: Callable[[object, GuiApi], None] | None = None,
    window_hidden: bool = False,
    emit_startup_status: bool = True,
) -> int:
    import webview  # 延迟导入：无 GUI 环境下 CLI 仍可用

    from genshin_app.windows_product_registration import set_process_app_id
    set_process_app_id()

    data_dir = Path(data_dir)
    profile_database = (data_dir / "genshin.db").resolve()
    selected_database = (
        Path(database_path).expanduser().resolve()
        if database_path is not None
        else profile_database
    )
    external_database = database_path is not None and selected_database != profile_database
    identity = dict(app_identity) if app_identity is not None else None
    if external_database:
        if is_frozen():
            raise DatabaseStartupError(
                "external_database_forbidden_in_formal_app",
                "正式版不允许绕过已配对的应用数据库",
            )
        mode = identity.get("mode") if identity is not None else None
        if mode not in _EXTERNAL_DATABASE_ACCEPTANCE_MODES:
            raise DatabaseStartupError(
                "external_database_acceptance_capability_required",
                "显式外部数据库仅允许受审的开发验收入口使用",
            )
        # An isolated development profile must never trigger ensure_data_dir's
        # bundled-database bootstrap.  That would silently copy the roughly
        # 700 MiB shared catalog before the external path is even considered.
        data_dir.mkdir(parents=True, exist_ok=True)
        for directory in ("build", "exports", "plans"):
            (data_dir / directory).mkdir(exist_ok=True)
        identity = identity or {}
        identity["service_mode"] = "skipped_external_database"
        identity["release_plan_state"] = "external_database_acceptance_bypass"

        # Explicit development/acceptance databases are a separate read-only
        # seam.  They are never copied, migrated or admitted as the App's live
        # paired database.
        api = GuiApi(
            data_dir,
            startup_path=open_path,
            agent_manager_factory=agent_manager_factory,
            dsh_shell_factory=dsh_shell_factory,
            dsh_result_save_dialog=dsh_result_save_dialog,
            dsh_file_dialog=dsh_file_dialog,
            database_path=selected_database,
            app_identity=identity,
        )
        try:
            if emit_startup_status:
                print("数据服务: 已跳过（显式外部数据库；旧CLI Agent资料查询通道不可用）")
            window = _create_main_window(
                webview,
                api,
                open_path,
                identity,
                hidden=window_hidden,
            )
            api._window = window
            install_close_guard(window, api)
            if main_window_callback is None:
                webview.start(storage_path=str(data_dir / "webview-profile"), private_mode=False)
            else:
                webview.start(lambda: main_window_callback(window, api), storage_path=str(data_dir / "webview-profile"), private_mode=False)
        finally:
            _shutdown_agent_transports(api)
        return 0

    return _run_paired_database_gui(
        webview,
        data_dir,
        open_path,
        app_identity=identity,
        agent_manager_factory=agent_manager_factory,
        dsh_shell_factory=dsh_shell_factory,
        dsh_result_save_dialog=dsh_result_save_dialog,
        dsh_file_dialog=dsh_file_dialog,
        main_window_callback=main_window_callback,
        window_hidden=window_hidden,
        emit_startup_status=emit_startup_status,
    )


def _main_title(open_path: Path | None, identity: Mapping | None) -> str:
    title = f"原神拉表 · {Path(open_path).name}" if open_path is not None else "原神拉表"
    label = identity.get("label") if identity is not None else None
    if isinstance(label, str) and label:
        title = f"{title} · {label}"
    return title


def _create_main_window(
    webview,
    api: GuiApi,
    open_path: Path | None,
    identity: Mapping | None,
    *,
    hidden: bool = False,
):
    from genshin_app.gui.agent_window import AgentWindowController

    api._agent_windows = AgentWindowController(api, webview, web_dir())
    return webview.create_window(
        _main_title(open_path, identity),
        url=str(web_dir() / "index.html"),
        js_api=api,
        width=1440,
        height=900,
        min_size=(1024, 640),
        text_select=True,
        hidden=hidden,
    )


def _splash_script(function: str, value: Mapping) -> str:
    # JSON is bridge data rather than HTML interpolation.  Escape the two JS
    # line separators for WebView engines which still parse them as source.
    payload = json.dumps(dict(value), ensure_ascii=False, separators=(",", ":"))
    payload = payload.replace("\u2028", "\\u2028").replace("\u2029", "\\u2029")
    return f"window.{function}({payload})"


def _run_paired_database_gui(
    webview,
    data_dir: Path,
    open_path: Path | None,
    *,
    app_identity: Mapping | None,
    agent_manager_factory,
    dsh_shell_factory=None,
    dsh_result_save_dialog=None,
    dsh_file_dialog=None,
    main_window_callback: Callable[[object, GuiApi], None] | None,
    window_hidden: bool,
    emit_startup_status: bool,
) -> int:
    """Show startup progress while deep admission runs off the GUI thread.

    ``webview.start(callback)`` schedules the callback after the native event
    loop and startup page are live.  Only that callback may construct the data
    service, GuiApi, calculation engine entrypoints and Agent manager seam.
    """

    splash = None
    runtime: dict[str, object] = {
        "manager": None,
        "api": None,
        "error": None,
    }

    def show_progress(value: Mapping) -> None:
        try:
            splash.evaluate_js(_splash_script("databaseStartupStatus", value))
        except Exception:
            logging.getLogger(__name__).debug(
                "数据库启动页状态更新失败",
                exc_info=True,
            )

    def bootstrap() -> None:
        manager = None
        api = None
        recovery._begin_admission()
        try:
            show_progress(
                {
                    "message": "正在准备本地数据库；首次安装可能需要复制大型发行文件",
                }
            )
            from genshin_app.product_database_setup import build_database_setup
            database_setup = build_database_setup(data_dir)
            if database_setup is not None:
                database_setup.validate_app_only()
                prepared_data_dir = data_dir
                prepared_data_dir.mkdir(parents=True, exist_ok=True)
                gate = {"state": "database_not_installed"}
            else:
                prepared_data_dir = ensure_data_dir(data_dir)
                gate = prepare_database_startup(prepared_data_dir, progress=show_progress)
            if recovery._admission_cancelled():
                runtime["error"] = None
                recovery._admission_aborted()
                return
            identity = dict(app_identity) if app_identity is not None else {}
            if is_frozen():
                from genshin_app.installation_location import resolve_runtime_root
                from genshin_app.managed_installation import (
                    refresh_registered_installation,
                )
                installation_root = resolve_runtime_root(sys.executable)
                if installation_root is not None:
                    # Keep registration details on the host.
                    refresh_registered_installation(installation_root)
            identity["release_plan_state"] = str(gate["state"])
            identity["service_mode"] = "database_setup" if database_setup else "local_database_service"
            manager = None if database_setup else ServiceManager(prepared_data_dir)
            runtime["manager"] = manager
            show_progress(
                {
                    "message": (
                        "应用校验完成，可以进入数据库安装。"
                        if database_setup else "数据库已通过应用校验，服务子进程会独立复核数据库。"
                    ),
                }
            )
            try:
                info = manager.ensure_running() if manager is not None else None
                if emit_startup_status and info:
                    print(
                        f"数据服务: {info['base_url']}"
                        f"（{'本进程拉起' if info['owned'] else '复用已有实例'}）"
                    )
            except RuntimeError as error:
                # Editing remains available without the legacy service.  This
                # is after database admission and does not bypass the engine's
                # direct database contract.
                if emit_startup_status:
                    print(f"警告：{error}")
            api = GuiApi(
                prepared_data_dir,
                startup_path=open_path,
                agent_manager_factory=agent_manager_factory,
                dsh_shell_factory=dsh_shell_factory,
                dsh_result_save_dialog=dsh_result_save_dialog,
                dsh_file_dialog=dsh_file_dialog,
                app_identity=identity,
                database_setup=database_setup,
            )
            runtime["api"] = api
            window = _create_main_window(
                webview,
                api,
                open_path,
                identity,
                hidden=window_hidden,
            )
            api._window = window
            install_close_guard(window, api)
            recovery._end_admission()
            splash.destroy()
            if main_window_callback is not None:
                main_window_callback(window, api)
            runtime["error"] = None
        except Exception as error:  # noqa: BLE001 - cross-thread startup boundary
            runtime["error"] = error
            if api is not None:
                _shutdown_agent_transports(api)
                runtime["api"] = None
            if manager is not None:
                manager.stop()
                runtime["manager"] = None
            reason = getattr(error, "reason", "database_startup_failed")
            message = str(error) if str(error) else "数据库校验失败，应用未启动。"
            recovery._failed(error, repair_available=is_frozen() and isinstance(error, DatabaseStartupError))
            try:
                splash.evaluate_js(
                    _splash_script(
                        "databaseStartupFailure",
                        {"reason": reason, "message": recovery.get_status().get("message", message),
                         "status": recovery.get_status()["status"]},
                    )
                )
            except Exception:
                logging.getLogger(__name__).exception("无法显示数据库启动失败信息")
        finally:
            recovery._end_admission()

    recovery = DatabaseStartupRecovery(data_dir,
        publish=lambda value: splash.evaluate_js(_splash_script("databaseRecoveryStatus", value)), retry=bootstrap)
    splash = webview.create_window(
        "原神拉表 · 正在校验数据库", html=_DATABASE_STARTUP_HTML,
        js_api=recovery, width=680, height=480, min_size=(520, 300),
        resizable=True, text_select=True, hidden=window_hidden,
    )
    events = getattr(splash, "events", None)
    if events is not None:
        events.closing += recovery._can_close

    try:
        webview.start(bootstrap, storage_path=str(data_dir / "webview-profile"), private_mode=False)
    finally:
        recovery.cancel_recovery()
        recovery._join()
        try:
            api = runtime.get("api")
            if api is not None:
                _shutdown_agent_transports(api)
        finally:
            manager = runtime.get("manager")
            if manager is not None:
                manager.stop()

    error = runtime.get("error")
    if error is not None:
        if recovery.get_status()["status"] in {"declined", "cancelled", "complete"}:
            return 0
        if isinstance(error, DatabaseStartupError):
            raise error
        raise error
    return 0
