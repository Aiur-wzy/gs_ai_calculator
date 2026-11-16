from __future__ import annotations

import json
import shutil
from pathlib import Path
from types import SimpleNamespace

import pytest

from scripts import verify_portable_startup as probe
from tests.test_portable_layout import make_portable


def test_default_path_probe_has_no_data_override_and_reads_back_real_identity(tmp_path, monkeypatch):
    root, app = make_portable(tmp_path)
    report = tmp_path / "reports/portable.json"
    expected = probe.sha(app / "_internal/bundled/genshin.db")
    monkeypatch.setattr(probe, "load_database_release_manifest", lambda _:
                        {"database": {"sha256": expected}})
    def frozen_cli(command, **kwargs):
        assert command[0] == str(app / "GenshinCalc.exe")
        assert "--data-dir" not in command
        assert not Path(kwargs["cwd"]).is_relative_to(root)
        assert "PYTHONPATH" not in kwargs["env"]
        if command[1:3] == ["whiteboard", "new"]:
            (root / "data").mkdir()
            shutil.copy2(app / "_internal/bundled/genshin.db", root / "data/genshin.db")
            shutil.copy2(app / "_internal/bundled/release-pair-manifest.json", root / "data/release-pair-live-manifest.json")
            data = {}
        else:
            assert command[1:3] == ["db", "catalog"]
            assert json.loads(Path(command[-1]).read_text("utf-8"))["query"] == "凯亚"
            data = {"items": [{"id": 10000015, "name": "凯亚", "publication": {"entity_status": "released"}}]}
        return SimpleNamespace(returncode=0, stdout=json.dumps({"code": 0, "data": data}).encode(), stderr=b"")
    monkeypatch.setattr(probe.subprocess, "run", frozen_cli)
    result = probe.qualify(app, report)
    assert result["status"] == "verified" and result["database_sha256"] == expected
    assert (root / "data/genshin.db").exists()
    assert json.loads(report.read_text("utf-8"))["explicit_data_dir"] is False
    assert set(result["command_seconds"]) == {"initialization", "catalog_first", "catalog_warm"}


def test_probe_never_clears_existing_profile(tmp_path, monkeypatch):
    root, app = make_portable(tmp_path)
    (root / "data").mkdir()
    (root / "data/session.json").write_text("preserve")
    monkeypatch.setattr(probe.subprocess, "run", lambda *_a, **_k: pytest.fail("must not launch"))
    with pytest.raises(ValueError, match="never overwrite"):
        probe.qualify(app, tmp_path / "report.json")
    assert (root / "data/session.json").read_text() == "preserve"


def test_probe_preserves_failure_for_inspection(tmp_path, monkeypatch):
    _root, app = make_portable(tmp_path)
    monkeypatch.setattr(probe.subprocess, "run", lambda *_a, **_k:
                        SimpleNamespace(returncode=2, stdout=b"", stderr=b"failed startup"))
    report = tmp_path / "failure.json"
    with pytest.raises(ValueError, match="CLI failed"):
        probe.qualify(app, report)
    assert json.loads(report.read_text("utf-8"))["status"] == "failed"
    assert report.with_suffix(".initialization.stderr.log").read_bytes() == b"failed startup"


@pytest.mark.parametrize("reply", [{}, {"code": 0, "data": {"items": []}},
    {"code": 0, "data": {"items": [{"id": 10000015, "name": "凯亚", "publication": {"entity_status": "unknown"}}]}}])
def test_zero_process_exit_without_verified_catalog_is_not_a_pass(tmp_path, monkeypatch, reply):
    root, app = make_portable(tmp_path)
    expected = probe.sha(app / "_internal/bundled/genshin.db")
    monkeypatch.setattr(probe, "load_database_release_manifest", lambda _: {"database": {"sha256": expected}})
    def run(command, **_kwargs):
        if command[1] == "whiteboard":
            (root / "data").mkdir()
            shutil.copy2(app / "_internal/bundled/genshin.db", root / "data/genshin.db")
            shutil.copy2(app / "_internal/bundled/release-pair-manifest.json", root / "data/release-pair-live-manifest.json")
            response = {"code": 0, "data": {}}
        else:
            response = reply
        return SimpleNamespace(returncode=0, stdout=json.dumps(response).encode(), stderr=b"")
    monkeypatch.setattr(probe.subprocess, "run", run)
    report = tmp_path / "rejected.json"
    with pytest.raises(ValueError):
        probe.qualify(app, report)
    assert json.loads(report.read_text("utf-8"))["status"] == "failed"
