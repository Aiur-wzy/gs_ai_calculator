"""PyInstaller 打包入口（G5）：无参数 → 启动 GUI；带参数 → 走完整 CLI。

CLI入口保留控制台供Agent读取JSON；同源GUI入口使用无控制台打包，启动失败弹窗提示。
"""

import io
import sys
from contextlib import nullcontext
from pathlib import Path

if not getattr(sys, "frozen", False):
    sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

from genshin_app.cli import main


def _gui_invocation(argv):
    """Find the command after global options, without mistaking a path for it."""
    index = 0
    while index < len(argv):
        arg = argv[index]
        if arg == "--data-dir":
            index += 2
        elif arg == "--pretty" or arg.startswith("--data-dir="):
            index += 1
        else:
            return arg == "gui"
    return False


def entry_main():
    # Keep interactive GUI failures visible, but never block a background CLI
    # command (including a losing db serve child) with a modal message box.
    argv = sys.argv[1:] or ["gui"]
    hidden_console = sys.stdout is None or sys.stderr is None
    if hidden_console:
        output = io.StringIO()
        sys.stdout = sys.stderr = output
    from genshin_app.installation_location import resolve_runtime_root
    from genshin_app.installation_runtime import installation_runtime_lease
    from genshin_app.product_install_contract import FirstInstallError
    try:
        installation_root = resolve_runtime_root(sys.executable) if getattr(sys, "frozen", False) else None
        # Covers CLI, GUI and child services until their normal shutdown joins
        # complete. Hidden/minimized windows never release the running lease.
        with installation_runtime_lease(installation_root) if installation_root is not None else nullcontext():
            code = main(argv)
    except FirstInstallError as error:
        message = ("便携包目录或配套资料不完整，尚未启动。请将完整便携包解压到新的目录，保留 app 和 data 的相邻布局；不要只复制 EXE。"
                   if error.reason.startswith("portable_layout_") else
                   "应用正在维护或安装位置已变化，尚未启动。请完成安装/迁移后重新打开。")
        print(f"{message} 详情：{error}", file=sys.stderr)
        code = 2
    if code and hidden_console and sys.platform == "win32" and _gui_invocation(argv):
        import ctypes
        ctypes.windll.user32.MessageBoxW(None, output.getvalue()[-3000:], "原神拉表启动失败", 0x10)
    return code


if __name__ == "__main__":
    sys.exit(entry_main())
