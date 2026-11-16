"""Separate current-user setup UI for the signed product release service."""
from __future__ import annotations

import argparse
from pathlib import Path
from queue import Empty, Queue
from threading import Event, Thread


def _failure_reasons(error):
    from genshin_app.setup_frozen_qualification import installation_error_chain

    return list(dict.fromkeys(row.get("reason", row["type"]) for row in installation_error_chain(error)))


def _preview_choice(previews, selected, *, local_files=False):
    mode = False if local_files or selected not in previews else selected
    return mode, previews.get(mode)


def _installation_completion(value):
    if value.get("source_directory"):
        headline = "迁移完成，新位置已生效。"
    elif value.get("database_state") == "preserved":
        headline = "程序已更新，数据库和用户资料已保留。"
    elif value.get("database_state") == "database_not_installed":
        headline = "程序已安装；使用计算功能前，请在资料库补装数据库。"
    else:
        headline = "程序和数据库安装完成。"
    note = "\n可打开安装目录中 app 子目录的 GenshinCalcGUI.exe。"
    if value.get("start_menu_names"):
        note = "\n开始菜单名称：" + "、".join(value["start_menu_names"])
    elif value.get("shortcut") == "created":
        note = "\n可从开始菜单打开原神拉表。"
    if value.get("registration_state") == "registration_pending":
        note += "\n系统入口尚待修复；重新打开安装器可以继续收尾。"
    if value.get("cleanup_pending"):
        note += "\n原位置仍有受管文件待清理。请关闭旧维护窗口后，从安装器继续收尾；不要再次启动旧位置。"
    return f"{headline}\n{value['install_directory']}{note}"


def _failure_message(reasons, action):
    groups = (
        ({"product_first_install_cancelled", "product_uninstall_cancelled"}, f"已取消{action}。"),
        ({"database_legacy_instance_running", "product_installation_running", "product_installation_maintenance_busy"},
         "请先关闭原神拉表、命令行和正在运行的更新器，等待后台任务退出后重试。"),
        ({"product_install_target_not_empty", "product_install_target_file_exists"},
         "所选目录已有其他文件。请新建专属空文件夹；这些文件没有被覆盖。"),
        ({"product_install_path_invalid", "product_install_protected_directory", "product_install_roots_overlap", "product_install_link_unsupported"},
         "请选择可写的专属目录，避开磁盘根目录、系统目录、目录联接，以及现有安装的内部或父目录。"),
        ({"product_install_location_changed", "product_first_install_confirmation_mismatch", "product_uninstall_confirmation_mismatch"},
         "目录、版本或保留资料选项已变化。请重新检查并确认本次操作。"),
        ({"product_install_existing_location_requires_migration"},
         "已发现另一处正式安装。请先在安装器中打开原位置，再选择新目录迁移，避免出现两份活动数据库。"),
        ({"product_migration_requires_app_update"},
         "当前旧版程序不支持自选资料目录。请先在原位置更新程序，再选择新目录迁移。"),
        ({"product_migration_recovery_pending", "product_installation_not_active", "product_migration_source_not_active"},
         "这处安装正在维护或已经迁移。请重新打开安装器，按提示继续上次维护，或打开新的安装位置。"),
        ({"product_migration_insufficient_space"},
         "目标磁盘空间不足以保留完整迁移副本。请释放空间或选择其他目录；原安装没有移动。"),
        ({"PermissionError"}, "当前用户无法写入所选位置。请选择有写入权限的专属目录，无需以管理员身份安装。"),
        ({"product_install_registration_invalid", "product_install_registration_missing", "product_uninstall_release_proof_missing",
          "product_uninstall_release_proof_invalid", "product_uninstall_plan_invalid", "product_start_menu_ownership_invalid"},
         "安装的归属记录无法核实，本次操作已停止。请保留安装目录和错误信息，使用有效安装器检查；不要手动删除整个目录。"),
        ({"product_install_recovery_conflict", "product_install_recovery_content_changed", "product_install_recovery_database_mismatch",
          "product_install_recovery_path_invalid", "product_install_recovery_proof_invalid", "product_migration_copy_mismatch",
          "product_migration_database_mismatch", "product_migration_recovery_conflict", "product_migration_recovery_identity_changed",
          "product_migration_recovery_path_invalid", "product_migration_recovery_proof_mismatch", "product_migration_source_changed",
          "product_migration_source_missing", "product_migration_target_changed"},
         "维护期间的文件或位置发生变化，自动处理已停止。请保留原目录、目标目录和错误信息，再从安装器恢复；不要同时运行两处程序。"),
        ({"product_repair_no_signed_app_successor"}, "当前数据库没有可用的已核验程序更新，现有安装保持不变。"),
        ({"product_repair_database_not_installed"}, "当前只安装了程序。请先在资料库补装数据库，再更新程序。"),
        ({"product_setup_pair_recovery_pending"}, "上次程序更新尚未收尾。请重新打开安装器，先处理上次更新。"),
        ({"product_setup_database_recovery_pending"}, "上次数据库安装尚未收尾。请先在 App 中完成恢复，再执行此操作。"),
        ({"product_start_menu_changed"}, "开始菜单文件在操作期间被更改。本次没有覆盖它，请检查入口后重试。"),
    )
    # Look through wrapper errors for the actionable cause, without dumping raw
    # exception messages, local paths or a stack trace into the setup window.
    for codes, message in groups:
        if codes.intersection(reasons):
            return message
    if "release_pair_helper_first_install_recovery_required" in reasons:
        return "安装文件已就绪，但程序或数据库尚未通过安装验证。请保留错误信息，再从安装器恢复。"
    return f"{action}尚未完成。请保留以下错误代码，重新打开安装器检查；不要手动搬动安装文件。"


def main(argv=None):
    parser = argparse.ArgumentParser()
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--uninstall", action="store_true")
    mode.add_argument("--self-check", action="store_true")
    mode.add_argument("--verify-frozen-install", action="store_true")
    mode.add_argument("--verify-frozen-upgrade", action="store_true", help=argparse.SUPPRESS)
    mode.add_argument("--verify-frozen-lifecycle", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--install-root", type=Path, help="本次安装或维护的专属目录")
    for flag in ("--index", "--app-archive", "--database-archive", "--sandbox-root", "--report",
                 "--previous-index", "--previous-app-archive"):
        parser.add_argument(flag, type=Path, help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    inputs = (args.index, args.app_archive, args.database_archive, args.sandbox_root, args.report)
    previous = (args.previous_index, args.previous_app_archive)
    if args.verify_frozen_lifecycle:
        if not all(inputs) or any(previous):
            parser.error("lifecycle qualification requires one signed index, both release archives, sandbox root, and report")
        from genshin_app.setup_frozen_qualification import verify_frozen_lifecycle

        return verify_frozen_lifecycle(index=args.index, app_archive=args.app_archive,
                                       database_archive=args.database_archive, sandbox_root=args.sandbox_root,
                                       report=args.report)
    if args.verify_frozen_install:
        if not all(inputs) or any(previous):
            parser.error("install qualification requires one signed index, both release archives, sandbox root, and report")
        from genshin_app.setup_frozen_qualification import verify_frozen_install

        return verify_frozen_install(index=args.index, app_archive=args.app_archive,
                                     database_archive=args.database_archive, sandbox_root=args.sandbox_root,
                                     report=args.report)
    if args.verify_frozen_upgrade:
        if not all(inputs + previous):
            parser.error("upgrade qualification requires both signed indexes and App archives, the shared database archive, sandbox root, and report")
        from genshin_app.setup_frozen_qualification import verify_frozen_upgrade

        return verify_frozen_upgrade(previous_index=args.previous_index, previous_app_archive=args.previous_app_archive,
                                     index=args.index, app_archive=args.app_archive, database_archive=args.database_archive,
                                     sandbox_root=args.sandbox_root, report=args.report)
    if any(inputs + previous):
        parser.error("qualification inputs are only accepted in a named frozen qualification mode")
    if args.self_check:
        return 0

    import tkinter as tk
    from tkinter import filedialog, messagebox, ttk

    from genshin_app.product_first_install import build_first_install_coordinator
    from genshin_app.product_install_contract import FirstInstallError
    from genshin_app.windows_product_registration import set_process_app_id

    set_process_app_id(role="uninstall" if args.uninstall else "setup")
    window = tk.Tk()
    window.title("卸载原神拉表" if args.uninstall else "安装原神拉表")
    window.minsize(570, 340)
    content = ttk.Frame(window, padding=20)
    content.pack(fill="both", expand=True)
    selected = tk.BooleanVar(value=False)
    keep_user_data = tk.BooleanVar(value=True)
    root_text = tk.StringVar(value=str(args.install_root) if args.install_root else "")
    status = tk.StringVar(value="正在检查发布版本…")
    progress = ttk.Progressbar(content, mode="determinate", maximum=100)
    events = Queue()
    cancelled = Event()
    operation = None
    active = False
    committing = False
    previews = {}
    local_files = None
    completed_root = None

    install_modes = []
    path_frame = ttk.Frame(content)
    path_frame.pack(fill="x", pady=(0, 12))
    ttk.Label(path_frame, text="安装目录（程序、数据库和资料都保存在这里）").pack(anchor="w")
    path_entry = ttk.Entry(path_frame, textvariable=root_text)
    path_entry.pack(side="left", fill="x", expand=True)
    browse = ttk.Button(path_frame, text="浏览…", command=lambda: choose_directory())
    browse.pack(side="left", padx=6)
    check_path = ttk.Button(path_frame, text="检查目录", command=lambda: change_directory(root_text.get()))
    check_path.pack(side="left")
    if args.uninstall:
        path_entry.configure(state="readonly")
        browse.configure(state="disabled")
        check_path.configure(state="disabled")
        keep_choice = ttk.Checkbutton(content, text="保留我的方案、聊天和设置（数据库仍会卸载）", variable=keep_user_data,
                                      command=lambda: refresh_uninstall())
        keep_choice.pack(anchor="w")
    if not args.uninstall:
        for label, value in (("安装程序和数据库（推荐）", False), ("只安装程序，稍后下载数据库", True)):
            button = ttk.Radiobutton(content, text=label, value=value, variable=selected,
                                     command=lambda: update_choice())
            button.pack(anchor="w")
            install_modes.append(button)
    ttk.Label(content, textvariable=status, wraplength=410, justify="left").pack(anchor="w", pady=16)
    progress.pack(fill="x")
    buttons = ttk.Frame(content)
    buttons.pack(anchor="e", pady=16)

    def worker(task, function):
        nonlocal active, committing
        active = True
        committing = False
        cancelled.clear()
        finished_actions.pack_forget()
        install.configure(state="disabled")
        cancel.configure(state="normal", text="取消")
        path_entry.configure(state="disabled")
        browse.configure(state="disabled")
        check_path.configure(state="disabled")
        for button in install_modes:
            button.configure(state="disabled")
        if args.uninstall:
            keep_choice.configure(state="disabled")
        if not args.uninstall:
            offline.configure(state="disabled")

        def run():
            try:
                events.put((task, function()))
            except Exception as error:  # noqa: BLE001 - user-facing thread boundary
                events.put(("failed", _failure_reasons(error)))

        Thread(target=run, daemon=False).start()

    def get_previews(root=None, keep=True):
        nonlocal operation
        operation = None
        operation = build_first_install_coordinator(install_root=root or args.install_root)
        if args.uninstall:
            return {False: operation.uninstall_preview(keep_user_data=keep)}
        maintenance = operation.maintenance_preview()
        if maintenance is not None:
            return {False: maintenance}
        recovery = operation.interrupted_update_preview()
        if recovery is not None:
            return {False: recovery}
        try:
            first = operation.preview()
        except FirstInstallError as error:
            if error.reason != "product_first_install_existing_profile":
                raise
            return {False: operation.repair_preview()}
        return {False: first, True: first if first.get("mode") == "reinstall" else operation.preview(app_only=True)}

    def refresh_uninstall():
        if not active:
            keep = keep_user_data.get()
            root = root_text.get()
            worker("preview", lambda: get_previews(root, keep))

    def choose_directory():
        if active:
            return
        chosen = filedialog.askdirectory(parent=window, title="选择专属安装目录（可先新建空文件夹）")
        if chosen:
            root_text.set(chosen)
            change_directory(chosen)

    def change_directory(value):
        nonlocal previews, local_files
        if active or not value.strip():
            return
        target = Path(value)
        previews = {}
        local_files = None
        selected.set(False)
        if operation and (operation.layout.app / "GenshinCalcGUI.exe").exists() and target.absolute() != operation.layout.root:
            status.set("正在核对现有安装的迁移范围…")
            worker("preview", lambda: {False: operation.migration_preview(target)})
        else:
            status.set("正在检查所选目录…")
            worker("preview", lambda: get_previews(target))

    def choose_offline():
        nonlocal local_files, previews
        paths = []
        for title, types in (("选择签名更新索引", (("JSON", "*.json"),)),
                             ("选择应用包", (("ZIP", "*.zip"),)),
                             ("选择数据库包", (("数据库", "*.gz *.db *.sqlite"),))):
            value = filedialog.askopenfilename(parent=window, title=title, filetypes=types)
            if not value:
                return
            paths.append(value)
        local_files = paths
        previews = {}
        selected.set(False)
        status.set("正在验证离线发行索引…")
        worker("preview", lambda: {False: operation.preview(local_index=paths[0])})

    def update_choice():
        mode, choice = _preview_choice(previews, selected.get(), local_files=bool(local_files))
        selected.set(mode)
        for value, button in enumerate(install_modes):
            button.configure(state="normal" if not active and not local_files and bool(value) in previews else "disabled")
        if choice is None or active:
            install.configure(state="disabled")
            return
        if args.uninstall:
            personal = "方案、聊天和设置会保留。" if choice["keep_user_data"] else "本安装内的方案、聊天和设置也会删除。"
            status.set(f"将卸载程序 {choice['app_release_id']} 和数据库。\n{personal}\n外部另存文件不会删除。")
            install.configure(state="normal")
            return
        if choice.get("mode") in {"recover_installation", "recover_uninstall", "recover_migration"}:
            status.set("上次维护尚未完成。请关闭原神拉表后继续收尾；不会重新开始一份安装。")
            install.configure(text="继续上次维护", state="normal")
            selected.set(False)
            return
        if choice.get("mode") == "migrate_installation":
            status.set(f"迁移现有安装和用户资料：\n{choice['source']}\n→ {choice['target']}\n"
                       f"需复制 {choice['copy_bytes'] / 1024**2:.1f} MiB。外部文件留在原处。\n请先关闭所有原神拉表窗口和命令行。")
            install.configure(text="确认迁移", state="normal")
            return
        if choice.get("mode") == "recover_interrupted_update":
            window.title("恢复上次原神拉表更新")
            for button in install_modes:
                button.pack_forget()
            offline.pack_forget()
            pending = (f"操作编号 {choice['operation_id']}\n"
                       f"{choice['previous_pair_id']} → {choice['target_pair_id']}\n")
            if choice["update_state"] == "handoff_ready":
                status.set(pending + "上次更新已准备但尚未安装。关闭原神拉表后，可以清理暂存的更新。"
                           "安装器会先核对当前安装，并保留现有程序、数据库和用户资料。")
                install.configure(text="清理尚未执行的更新", state="normal")
            else:
                status.set(pending + "上次更新尚未安全收尾。关闭原神拉表后，安装器可以核对记录并恢复。")
                install.configure(text="恢复上次更新", state="normal")
            return
        sizes = choice["download_bytes"]
        def mib(value):
            return f"{value / (1024 * 1024):.1f} MiB"
        if choice.get("mode") == "repair_installed_app":
            window.title("更新已安装的原神拉表")
            for button in install_modes:
                button.pack_forget()
            offline.pack_forget()
            status.set(f"程序 {choice['installed_app_release_id']} → {choice['app_release_id']} · {mib(sizes['app'])}\n"
                       f"保留数据库 {choice['database_release_id']}、设置、会话和方案。\n"
                       "请先关闭所有原神拉表窗口和命令行进程，再点「更新程序」。")
            install.configure(text="更新程序", state="normal")
            return
        details = [f"程序 {choice['app_release_id']} · {mib(sizes['app'])}"]
        if choice.get("mode") == "local_pair":
            details.insert(0, "使用本地发行包，安装前校验全部文件。")
        if choice.get("mode") == "reinstall":
            install.configure(text="重新安装")
            details.insert(0, "重新安装程序和数据库；此前选择保留的个人资料仍会保留。")
        if sizes["database"]:
            details.append(f"数据库 {choice['database_release_id']} · {mib(sizes['database'])}")
        details.append(choice["install_directory"])
        status.set("\n".join(details))
        install.configure(state="normal")

    def begin_install():
        if active or not previews:
            return
        mode, choice = _preview_choice(previews, selected.get(), local_files=bool(local_files))
        if choice is None:
            update_choice()
            return
        if args.uninstall:
            if messagebox.askokcancel("卸载原神拉表", "请关闭原神拉表窗口后确认卸载。", parent=window):
                status.set("正在核对已安装的文件…")
                worker("uninstalled", lambda: operation.uninstall(
                    choice["confirmation_digest"], user_confirmed=True, cancel_check=cancelled.is_set,
                    keep_user_data=choice["keep_user_data"],
                    progress=lambda value: events.put(("progress", value))))
            return
        if choice.get("mode") == "migrate_installation":
            if messagebox.askokcancel("迁移安装", "确认将程序、数据库和本安装用户资料迁移到所选目录？", parent=window):
                worker("installed", lambda: operation.migrate(choice["target"], choice["confirmation_digest"],
                    user_confirmed=True, cancel_check=cancelled.is_set,
                    progress=lambda value: events.put(("progress", value))))
            return
        if choice.get("mode") == "recover_installation":
            worker("installed", lambda: operation.recover_installation(choice["confirmation_digest"], user_confirmed=True))
            return
        if choice.get("mode") == "recover_uninstall":
            worker("uninstalled", lambda: operation.uninstall(choice["confirmation_digest"], user_confirmed=True,
                keep_user_data=choice["keep_user_data"], progress=lambda value: events.put(("progress", value))))
            return
        if choice.get("mode") == "recover_migration":
            from genshin_app.installation_migration import recover_migration
            worker("installed", lambda: recover_migration(operation, choice["confirmation_digest"], user_confirmed=True))
            return
        if choice.get("mode") == "recover_interrupted_update":
            detail = ("确认清理尚未执行的更新暂存文件，保留当前安装？"
                      if choice["update_state"] == "handoff_ready" else "确认恢复尚未完成的更新？")
            if messagebox.askokcancel("恢复上次更新", detail, parent=window):
                worker("recovered", lambda: operation.recover_interrupted_update(
                    choice["confirmation_digest"], user_confirmed=True,
                    progress=lambda value: events.put(("progress", value))))
            return
        if choice.get("mode") == "repair_installed_app":
            if messagebox.askokcancel("更新原神拉表", f"更新到 {choice['app_release_id']}，保留当前数据库和用户资料？",
                                       parent=window):
                status.set("正在下载并验证签名程序…")
                worker("installed", lambda: operation.repair_install(
                    choice["confirmation_digest"], user_confirmed=True, cancel_check=cancelled.is_set,
                    progress=lambda value: events.put(("progress", value))))
            return
        local_options = ({"local_index": local_files[0], "local_artifacts": {"app": local_files[1], "database": local_files[2]}}
                         if local_files else {})
        status.set("正在验证本地安装文件…" if local_options else "正在下载并验证安装文件…")
        worker("installed", lambda: operation.install(
            app_only=mode, user_confirmed=True, confirmation_digest=choice["confirmation_digest"],
            cancel_check=cancelled.is_set, progress=lambda value: events.put(("progress", value)), **local_options))

    def request_cancel():
        if active:
            if not committing:
                cancelled.set()
                status.set("已请求取消，正在等待当前操作结束…")
                cancel.configure(state="disabled")
        else:
            window.destroy()

    def poll():
        nonlocal active, committing, previews, completed_root
        try:
            while True:
                state, value = events.get_nowait()
                if state == "progress":
                    if value.get("phase") == "committing":
                        committing = True
                        cancel.configure(state="disabled")
                        status.set("正在安全收尾，请稍候…")
                    elif "bytes" in value and value.get("total_bytes"):
                        progress.configure(value=100 * value["bytes"] / value["total_bytes"])
                    continue
                active = False
                cancel.configure(state="normal", text="关闭")
                path_entry.configure(state="readonly" if args.uninstall else "normal")
                if not args.uninstall:
                    browse.configure(state="normal")
                    check_path.configure(state="normal")
                else:
                    keep_choice.configure(state="normal")
                for button in install_modes:
                    button.configure(state="normal")
                if not args.uninstall and operation is not None and state != "installed":
                    offline.configure(state="normal")
                if state == "preview":
                    previews = value
                    root_text.set(previews.get(False, {}).get("install_directory", previews.get(False, {}).get("target", root_text.get())))
                    if previews.get(False, {}).get("mode") in {"repair_installed_app", "recover_interrupted_update"}:
                        selected.set(False)
                    update_choice()
                elif state == "installed":
                    progress.configure(value=100)
                    previews = {}
                    status.set(_installation_completion(value))
                    completed_root = Path(value["install_directory"])
                    finished_actions.pack(fill="x", pady=(0, 8))
                elif state == "recovered":
                    progress.configure(value=100)
                    previews = {}
                    if value.get("action") == "discarded_prepared_update":
                        status.set("已清理尚未执行的更新。现有安装和用户资料已保留。\n"
                                   "如需更新或卸载，请重新打开安装器。")
                    else:
                        status.set("上次更新已完成恢复。可以重新打开原神拉表。")
                elif state == "uninstalled":
                    previews = {}
                    note = ""
                    if value.get("cleanup_pending"):
                        note += "\n仍有维护文件或其他受管文件待清理。请关闭此窗口后，用外部安装器继续收尾。"
                    if value.get("shortcut") in ("preserved_modified", "cleanup_pending"):
                        note += "\n开始菜单中仍有快捷方式，可自行移除。"
                    personal = (f"用户资料保留在\n{value['preserved_data_directory']}" if value.get("preserved_data_directory")
                                else "本安装的用户资料已按选择清理。")
                    headline = ("卸载尚未完全完成，请保留此安装目录继续收尾。"
                                if value.get("remaining") or value.get("database_removed") is not True
                                else "程序和数据库已卸载。")
                    status.set(f"{headline}\n{personal}{note}")
                else:
                    setup_mode = previews.get(False, {}).get("mode")
                    action = {"repair_installed_app": "更新", "recover_interrupted_update": "恢复", "migrate_installation": "迁移",
                              "recover_migration": "恢复迁移", "recover_uninstall": "卸载", "recover_installation": "恢复安装"}.get(
                        setup_mode, "卸载" if args.uninstall else "安装")
                    status.set(_failure_message(value, action) + "\n错误代码：" + " → ".join(value))
                    if previews:
                        install.configure(state="normal")
                    messagebox.showinfo(window.title(), status.get(), parent=window)
        except Empty:
            pass
        window.after(100, poll)

    install = ttk.Button(buttons, text="卸载" if args.uninstall else "安装", command=begin_install, state="disabled")
    def open_finished(kind):
        if completed_root is None:
            return
        import os
        import subprocess
        try:
            if kind == "directory":
                os.startfile(str(completed_root))
            else:
                command = ([str(completed_root / "app/GenshinCalcGUI.exe")] if kind == "app" else
                           [str(completed_root / "maintenance/GenshinCalcSetup.exe"), "--uninstall", "--install-root", str(completed_root)])
                subprocess.Popen(command, cwd=completed_root)
        except OSError:
            messagebox.showerror("无法打开", "目标文件无法打开。请从安装目录检查文件，或重新运行安装器修复。", parent=window)

    finished_actions = ttk.Frame(content)
    for label, kind in (("打开原神拉表", "app"), ("打开安装目录", "directory"), ("卸载入口", "uninstall")):
        ttk.Button(finished_actions, text=label, command=lambda selected_kind=kind: open_finished(selected_kind)).pack(side="left", padx=3)
    if not args.uninstall:
        offline = ttk.Button(buttons, text="使用离线发行包", command=choose_offline, state="disabled")
        offline.pack(side="left")
    install.pack(side="left", padx=8)
    cancel = ttk.Button(buttons, text="取消", command=request_cancel)
    cancel.pack(side="left")
    window.protocol("WM_DELETE_WINDOW", request_cancel)
    worker("preview", get_previews)
    window.after(100, poll)
    window.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
