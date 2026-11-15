"""Run only the named headless packaged qualification, never the GUI EXE.

The child owns the fixed suite and its isolation audit. This thin launcher
also injects public fake ambient values to prove they cannot select a real
provider, proxy, source fallback or user storage. It never reads a real Key.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from genshin_app.frozen_selfcheck_isolation import SelfcheckContext, clean_environment


def run_selfcheck(executable, sandbox_root, report):
    executable = Path(executable).resolve()
    if executable.name != "GenshinCalc.exe" or not executable.is_file():
        raise ValueError("only_packaged_GenshinCalc_cli_is_allowed")
    context = SelfcheckContext.create(sandbox_root, report, package_root=executable.parent, executable=executable)
    environment = clean_environment(context.root)
    # These are declared test sentinels, not account values. Passing them to
    # the frozen bootstrap exercises the production entry's pre-profile scrub.
    environment.update(DEEPSEEK_API_KEY="offline-selfcheck-fake-never-use",
        OPENAI_API_KEY="offline-selfcheck-fake-never-use", HTTP_PROXY="http://127.0.0.1:9",
        HTTPS_PROXY="http://127.0.0.1:9", PYTHONPATH=str(context.root.parent / "source-fallback-must-not-be-used"),
        DSH_HOME=str(context.root.parent / "ambient-dsh-home-must-not-be-used"))
    child = subprocess.run([str(executable), "internal", "verify-frozen", "--sandbox-root", str(context.root),
                            "--report", str(context.report)], cwd=executable.parent, env=environment,
                           capture_output=True, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0), check=False)
    if child.returncode != 0:
        raise RuntimeError("frozen_cli_selfcheck_failed: report=" + str(context.report))
    value = json.loads(context.report.read_text(encoding="utf-8"))
    if (value.get("status") != "verified" or value.get("execution_layer") != "frozen"
            or value.get("real_gui_tested") is not False or value.get("forbidden_operations_executed") != 0):
        raise RuntimeError("frozen_cli_selfcheck_receipt_invalid")
    for report_row in (value, value.get("result", {}).get("fresh_frozen_readback", {})):
        if any(report_row.get("python_isolation", {}).get("normal", {}).values()):
            raise RuntimeError("frozen_cli_selfcheck_forbidden_attempt")
    forbidden = {"DEEPSEEK_API_KEY", "OPENAI_API_KEY", "HTTP_PROXY", "HTTPS_PROXY", "PYTHONPATH"}
    if forbidden.intersection(value.get("python_effective_environment_keys", ())):
        raise RuntimeError("frozen_cli_selfcheck_environment_not_scrubbed")
    return {"status": "verified", "report": str(context.report), "actual_frozen_tested": True,
            "fake_ambient_regression": True, "real_gui_tested": False}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    parser.add_argument("--cli", required=True)
    parser.add_argument("--sandbox-root", required=True)
    parser.add_argument("--report", required=True)
    args = parser.parse_args(argv)
    print(json.dumps(run_selfcheck(args.cli, args.sandbox_root, args.report), ensure_ascii=False))


if __name__ == "__main__":
    main()
