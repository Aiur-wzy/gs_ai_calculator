# PyInstaller specification for the external, one-file pair updater.
# The build script places this executable beside (never inside) GenshinCalc.
import sys
from pathlib import Path


project_root = Path(SPECPATH).resolve()
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))
from scripts.packaging_windows_runtime import (
    omit_windows_system_crt, runtime_validation_datas, verify_runtime_validation_archive,
)

source_root = project_root / "src"
entry_point = project_root / "updater_entry.py"
if not entry_point.is_file() or not source_root.is_dir():
    raise ValueError("updater source layout is incomplete")

a = Analysis(
    [str(entry_point)],
    pathex=[str(source_root)],
    binaries=[],
    datas=runtime_validation_datas(project_root),
    hiddenimports=[],
    hookspath=[],
    runtime_hooks=[],
    excludes=["webview"],
    noarchive=False,
)
a.binaries = omit_windows_system_crt(a.binaries)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="GenshinCalcUpdater",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=True,
    icon=None,
)
verify_runtime_validation_archive(Path(exe.name), project_root)
