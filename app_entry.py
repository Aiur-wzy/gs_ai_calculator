"""PyInstaller 打包入口（G5）：无参数 → 启动 GUI；带参数 → 走完整 CLI。

CLI入口保留控制台供Agent读取JSON；同源GUI入口使用无控制台打包，启动失败弹窗提示。
"""

import io
import sys
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
    code = main(argv)
    if code and hidden_console and sys.platform == "win32" and _gui_invocation(argv):
        import ctypes
        ctypes.windll.user32.MessageBoxW(None, output.getvalue()[-3000:], "原神拉表启动失败", 0x10)
    return code


if __name__ == "__main__":
    sys.exit(entry_main())
