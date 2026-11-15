"""Build help must work from the documented physical script entrypoint."""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("entry", ["direct_script", "package_module"])
def test_build_dist_help_bootstraps_its_own_package_imports(entry, tmp_path):
    environment = dict(os.environ)
    environment.pop("PYTHONPATH", None)
    if entry == "direct_script":
        # Isolated Python + an external CWD cannot accidentally expose repo ROOT.
        arguments = [sys.executable, "-I", str(ROOT / "scripts/build_dist.py"), "--help"]
        working = tmp_path
    else:
        # Both supported invocation forms must resolve the same qualified helper.
        arguments = [sys.executable, "-E", "-m", "scripts.build_dist", "--help"]
        working = ROOT
    # -I/-E intentionally ignore PYTHONUTF8. Import bootstrapping must not
    # depend on the Windows console code page used by argparse's help text.
    result = subprocess.run(arguments, cwd=working, env=environment,
        capture_output=True, timeout=30, check=False)
    assert result.returncode == 0, result.stderr
    assert b"--native-node" in result.stdout and b"--dist-root" in result.stdout
    assert result.stderr == b""
    # argparse help exits before build planning or publishing any artifact.
    assert list(tmp_path.iterdir()) == []
