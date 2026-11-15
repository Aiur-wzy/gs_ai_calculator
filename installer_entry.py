"""Separate current-user setup UI for the signed product release service."""
from __future__ import annotations

import argparse
from pathlib import Path
from queue import Empty, Queue
from threading import Event, Thread


def _failure_reasons(error):
    from genshin_app.setup_frozen_qualification import installation_error_chain

    return list(dict.fromkeys(row.get("reason", row["type"]) for row in installation_error_chain(error)))


def main(argv=None):
    parser = argparse.ArgumentParser()
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--uninstall", action="store_true")
    mode.add_argument("--self-check", action="store_true")
    mode.add_argument("--verify-frozen-install", action="store_true")
    mode.add_argument("--verify-frozen-upgrade", action="store_true", help=argparse.SUPPRESS)
    for flag in ("--index", "--app-archive", "--database-archive", "--sandbox-root", "--report",
                 "--previous-index", "--previous-app-archive"):
        parser.add_argument(flag, type=Path, help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    inputs = (args.index, args.app_archive, args.database_archive, args.sandbox_root, args.report)
    previous = (args.previous_index, args.previous_app_archive)
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

    window = tk.Tk()
    window.title("卸载原神拉表" if args.uninstall else "安装原神拉表")
    window.minsize(460, 250)
    content = ttk.Frame(window, padding=20)
    content.pack(fill="both", expand=True)
    selected = tk.BooleanVar(value=False)
    status = tk.StringVar(value="正在检查发布版本…")
    progress = ttk.Progressbar(content, mode="determinate", maximum=100)
    events = Queue()
    cancelled = Event()
    operation = None
    active = False
    committing = False
    previews = {}
    local_files = None

    install_modes = []
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
        install.configure(state="disabled")
        cancel.configure(state="normal", text="取消")
        if not args.uninstall:
            offline.configure(state="disabled")

        def run():
            try:
                events.put((task, function()))
            except Exception as error:  # noqa: BLE001 - user-facing thread boundary
                events.put(("failed", _failure_reasons(error)))

        Thread(target=run, daemon=False).start()

    def get_previews():
        nonlocal operation
        operation = build_first_install_coordinator()
        if args.uninstall:
            return {False: operation.uninstall_preview()}
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
        choice = previews.get(False if local_files else selected.get())
        if choice is None or active:
            return
        if args.uninstall:
            status.set(f"将卸载程序 {choice['app_release_id']}。\n数据库、方案、聊天和设置会保留。")
            install.configure(state="normal")
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
            details.insert(0, "重新安装与现有资料匹配的程序。已有数据库、方案、聊天和设置会保留。")
        if sizes["database"]:
            details.append(f"数据库 {choice['database_release_id']} · {mib(sizes['database'])}")
        details.append(choice["install_directory"])
        status.set("\n".join(details))
        install.configure(state="normal")

    def begin_install():
        if active or not previews:
            return
        mode = False if local_files else selected.get()
        choice = previews[mode]
        if args.uninstall:
            if messagebox.askokcancel("卸载原神拉表", "请关闭原神拉表窗口后确认卸载。", parent=window):
                status.set("正在核对已安装的文件…")
                worker("uninstalled", lambda: operation.uninstall(
                    choice["confirmation_digest"], user_confirmed=True, cancel_check=cancelled.is_set,
                    progress=lambda value: events.put(("progress", value))))
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
        nonlocal active, committing, previews
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
                if not args.uninstall and operation is not None and state != "installed":
                    offline.configure(state="normal")
                if state == "preview":
                    previews = value
                    if previews.get(False, {}).get("mode") in {"repair_installed_app", "recover_interrupted_update"}:
                        selected.set(False)
                    update_choice()
                elif state == "installed":
                    progress.configure(value=100)
                    previews = {}
                    note = ("\n可从开始菜单打开原神拉表。" if value.get("shortcut") == "created"
                            else "\n可打开安装目录中的 GenshinCalcGUI.exe。")
                    action = "程序已更新，数据库和用户资料已保留。" if value.get("database_state") == "preserved" else "安装完成"
                    status.set(f"{action}\n{value['install_directory']}{note}")
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
                        note += "\n程序已从安装位置移除，仍有缓存副本待清理。"
                    if value.get("shortcut") in ("preserved_modified", "cleanup_pending"):
                        note += "\n开始菜单中仍有快捷方式，可自行移除。"
                    status.set(f"程序已卸载，用户资料保留在\n{value['preserved_data_directory']}{note}")
                else:
                    setup_mode = previews.get(False, {}).get("mode")
                    action = {"repair_installed_app": "更新", "recover_interrupted_update": "恢复"}.get(
                        setup_mode, "卸载" if args.uninstall else "安装")
                    reason = value[0]
                    if reason in {"product_first_install_cancelled", "product_uninstall_cancelled"}:
                        status.set(f"已取消{action}。")
                    elif reason == "database_legacy_instance_running":
                        status.set("请先关闭原神拉表、命令行或正在运行的更新器，再重试。")
                    elif reason == "product_repair_no_signed_app_successor":
                        status.set("当前数据库没有可用的签名程序更新，已保留现有安装。")
                    elif reason == "product_repair_database_not_installed":
                        status.set("当前只安装了程序。请先打开原神拉表，在资料库下载数据库或选择本地发行包，再更新程序。")
                    elif reason == "product_setup_pair_recovery_pending":
                        status.set("上次程序更新尚未收尾。请直接打开新版安装器，先处理上次更新。")
                    elif reason == "product_setup_database_recovery_pending":
                        status.set("上次数据库安装尚未收尾。请先在原神拉表内完成恢复，再执行此操作。")
                    else:
                        message = ("安装文件已就绪，但程序和数据库未通过安装验证。"
                                   if reason == "release_pair_helper_first_install_recovery_required"
                                   else f"{action}尚未完成。")
                        status.set(message + "\n" + " → ".join(value))
                    if previews:
                        install.configure(state="normal")
                    messagebox.showinfo(window.title(), status.get(), parent=window)
        except Empty:
            pass
        window.after(100, poll)

    install = ttk.Button(buttons, text="卸载" if args.uninstall else "安装", command=begin_install, state="disabled")
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
