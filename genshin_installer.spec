# One-file setup build. Runtime and game data are downloaded from the signed index.
import sys
from pathlib import Path

project_root = Path(SPECPATH).resolve()
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))
from scripts.packaging_windows_runtime import omit_windows_system_crt, runtime_validation_datas
from scripts.verify_frozen_setup_archive import verify_archive

a = Analysis(
    [str(project_root / "installer_entry.py")],
    pathex=[str(project_root / "src")],
    binaries=[], datas=runtime_validation_datas(project_root), hiddenimports=[], hookspath=[], runtime_hooks=[],
    excludes=["webview", "genshin_engine", "genshin_app.agent_chat"],
    noarchive=False,
)

a.binaries = omit_windows_system_crt(a.binaries)
pyz = PYZ(a.pure)
exe = EXE(
    pyz, a.scripts, a.binaries, a.datas, [],
    name="GenshinCalcSetup", debug=False, bootloader_ignore_signals=False,
    strip=False, upx=False, console=False, icon=None,
)
verify_archive(Path(exe.name), project_root)
