"""Windows plan shortcuts: the link stores paths, never a copied calculation."""
from __future__ import annotations

import base64
import hashlib
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

from genshin_app.paths import is_frozen


def _launcher() -> tuple[Path, list[str]]:
    if is_frozen():
        target = Path(sys.executable).resolve().with_name("GenshinCalcGUI.exe")
        prefix = []
    else:
        target = Path(sys.executable).resolve().with_name("pythonw.exe")
        entry = Path(__file__).resolve().parents[2] / "app_entry.py"
        if not entry.is_file():
            raise FileNotFoundError("源码启动入口不存在，请用便携版 GenshinCalc.exe 生成快捷方式")
        prefix = [str(entry)]
    if not target.is_file():
        raise FileNotFoundError(f"图形启动程序不存在：{target}；请使用完整的当前版本")
    return target, prefix


def _windows_link(spec: dict) -> bytes:
    # Paths travel as JSON data, not interpolated PowerShell source or shell commands.
    script = r'''
$ErrorActionPreference = 'Stop'
$spec = ConvertFrom-Json -InputObject $env:GENSHIN_SHORTCUT_SPEC
$shell = New-Object -ComObject WScript.Shell
$link = $shell.CreateShortcut($spec.output)
$link.TargetPath = $spec.target
$link.Arguments = $spec.arguments
$link.WorkingDirectory = $spec.working_directory
$link.Description = $spec.description
$link.IconLocation = $spec.target + ',0'
$link.WindowStyle = 1
$link.Save()
$saved = $shell.CreateShortcut($spec.output)
if ($saved.TargetPath -ine $spec.target -or $saved.Arguments -cne $spec.arguments -or $saved.WorkingDirectory -ine $spec.working_directory) {
    throw 'Shortcut properties failed round-trip verification'
}
'''
    powershell = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32/WindowsPowerShell/v1.0/powershell.exe"
    with tempfile.TemporaryDirectory(prefix="genshin-shortcut-") as temporary:
        output = Path(temporary) / "open-plan.lnk"
        payload = {**spec, "output": str(output)}
        result = subprocess.run(
            [str(powershell), "-NoProfile", "-NonInteractive", "-EncodedCommand",
             base64.b64encode(script.encode("utf-16le")).decode("ascii")],
            env={**os.environ, "GENSHIN_SHORTCUT_SPEC": json.dumps(payload, ensure_ascii=False)},
            capture_output=True, timeout=30, check=False, creationflags=subprocess.CREATE_NO_WINDOW,
        )
        if result.returncode or not output.is_file():
            detail = result.stderr.decode("utf-8", errors="replace").strip()
            raise RuntimeError(f"创建 Windows 快捷方式失败：{detail or result.returncode}")
        if spec.get("app_user_model_id"):
            from .windows_shell_identity import set_shortcut_app_id
            set_shortcut_app_id(output, spec["app_user_model_id"])
        return output.read_bytes()


def create_shortcut(input_path, output_path, data_dir, *, overwrite=False) -> dict:
    from genshin_app.equipment.files import publish_files, read_input_bytes
    from genshin_app.equipment.schema import PlanError, validate_plan
    from genshin_app.query_cache import protect_inputs
    from genshin_engine import validate_template

    if sys.platform != "win32":
        raise PlanError("方案快捷方式目前仅支持 Windows；其他平台可使用 gui --open", "shortcut_request_invalid")
    source = Path(input_path).resolve()
    destination = Path(output_path).resolve()
    database_dir = Path(data_dir).resolve()
    if destination.suffix.lower() != ".lnk":
        raise PlanError("快捷方式输出必须使用 .lnk 扩展名", "shortcut_request_invalid", {"path": "/output", "expected_suffix": ".lnk"})
    protect_inputs([destination], [source, database_dir / "genshin.db"])
    raw = read_input_bytes(source)
    try:
        value = json.loads(raw.decode("utf-8-sig"))
    except (ValueError, UnicodeError, RecursionError) as exc:
        raise PlanError("关联方案需要有效的UTF-8 JSON", "input_json_invalid", {"path": "/input"}) from exc
    if isinstance(value, dict) and value.get("kind") == "genshin-whiteboard":
        from genshin_app.whiteboard import validate_whiteboard
        validate_whiteboard(value)
        input_kind = "whiteboard"
    elif isinstance(value, dict) and value.get("kind"):
        validate_plan(value)
        input_kind = "native_plan"
    else:
        validate_template(value)
        input_kind = "resolved_template"
    target, prefix = _launcher()
    protect_inputs([destination], [target])
    argv = [*prefix, "--data-dir", str(database_dir), "gui", "--open", str(source)]
    spec = {"target": str(target), "arguments": subprocess.list2cmdline(argv),
            "working_directory": str(target.parent), "description": "原神拉表：打开关联方案"}
    published = publish_files([(destination, _windows_link(spec))], overwrite=overwrite,
                              input_paths=[source, database_dir / "genshin.db", target])
    return {"path": str(destination), "input": str(source), "input_kind": input_kind,
            "input_sha256_at_creation": hashlib.sha256(raw).hexdigest(),
            "data_dir": str(database_dir), "launcher": str(target), "argv": argv,
            "behavior": "每次打开关联JSON的最新内容；独立窗口，不自动计算",
            "portability": "本机绝对路径；JSON、程序或数据库移动后重新生成",
            **published}
