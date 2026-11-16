"""G5：version（自更新检测）与 paths（frozen 路径适配/首启初始化）测试。"""

from __future__ import annotations

import json
import sys

import pytest

from genshin_app import paths
from genshin_app.version import check_update, parse_version


class TestParseVersion:
    def test_basic(self):
        assert parse_version("0.1.0") == (0, 1, 0)
        assert parse_version("v1.2.3") == (1, 2, 3)
        assert (parse_version("0.2") > parse_version("0.1.9"))


class TestCheckUpdate:
    def _manifest(self, tmp_path, data: dict) -> str:
        f = tmp_path / "manifest.json"
        f.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        return f.as_uri()

    def test_empty_url_returns_none(self):
        assert check_update("0.1.0", "") is None

    def test_newer_version(self, tmp_path):
        url = self._manifest(tmp_path, {"version": "0.2.0", "url": "https://x/d",
                                        "notes": "修bug"})
        r = check_update("0.1.0", url)
        assert r is not None and r["version"] == "0.2.0"

    def test_same_or_older(self, tmp_path):
        url = self._manifest(tmp_path, {"version": "0.1.0", "url": "https://x/d"})
        assert check_update("0.1.0", url) is None
        assert check_update("9.9.9", url) is None

    def test_bad_manifest_silent(self, tmp_path):
        f = tmp_path / "bad.json"
        f.write_text("not json", encoding="utf-8")
        assert check_update("0.1.0", f.as_uri()) is None
        url = self._manifest(tmp_path, {"version": "0.2.0"})  # 缺 url 字段
        assert check_update("0.1.0", url) is None

    def test_unreachable_silent(self):
        assert check_update("0.1.0", "file:///nonexistent/manifest.json") is None


class TestPaths:
    def test_dev_defaults_do_not_follow_working_directory(self, monkeypatch, tmp_path):
        monkeypatch.delattr(sys, "frozen", raising=False)
        expected = paths.resource_dir() / "data"
        monkeypatch.chdir(tmp_path)
        assert not paths.is_frozen()
        assert paths.default_data_dir() == expected
        assert paths.default_data_dir().is_absolute()

    def test_explicit_data_dir_still_overrides_repository_default(self, monkeypatch, tmp_path):
        monkeypatch.delattr(sys, "frozen", raising=False)
        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr(paths, "bundled_db_path", lambda: tmp_path / "not-installed.db")
        explicit = paths.Path("chosen-data")
        assert paths.ensure_data_dir(explicit) == explicit
        assert (tmp_path / "chosen-data/plans").is_dir()

    def test_frozen_data_dir(self, monkeypatch, tmp_path):
        monkeypatch.setattr(sys, "frozen", True, raising=False)
        monkeypatch.setattr(sys, "_MEIPASS", str(tmp_path), raising=False)
        monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
        (tmp_path / "app").mkdir()
        (tmp_path / "installation.json").write_text(json.dumps({"contract": "genshin-product-first-install-v1",
            "scope": "current_user", "app_directory": "app", "data_directory": "data"}), encoding="utf-8")
        monkeypatch.setattr(sys, "executable", str(tmp_path / "app/GenshinCalcGUI.exe"))
        assert paths.is_frozen()
        assert paths.default_data_dir() == tmp_path / "data"
        assert paths.bundled_db_path() == tmp_path / "bundled" / "genshin.db"
        assert paths.web_dir().as_posix().endswith("genshin_app/gui/web")

    def test_ensure_data_dir_copies_bundled(self, monkeypatch, tmp_path):
        monkeypatch.setattr("genshin_app.database_legacy_processes.packaged_peer_processes", list)
        bundled_dir = tmp_path / "meipass" / "bundled"
        bundled_dir.mkdir(parents=True)
        fake_db = bundled_dir / "genshin.db"
        fake_db.write_bytes(b"sqlite")
        monkeypatch.setattr(sys, "frozen", True, raising=False)
        monkeypatch.setattr(sys, "_MEIPASS", str(tmp_path / "meipass"), raising=False)

        data_dir = paths.ensure_data_dir(tmp_path / "user_data")
        live = data_dir / "genshin.db"
        assert live.read_bytes() == b"sqlite"  # 首启复制预构建库
        for sub in ("build", "exports", "plans"):
            assert (data_dir / sub).is_dir()

        # 普通启动不修改已安装的活动库；App/DB 版本升级由
        # 安装器取得用户同意后调用受审发行迁移器。
        live.write_bytes(b"user-updated")
        paths.ensure_data_dir(data_dir)
        assert live.read_bytes() == b"user-updated"

    def test_ensure_data_dir_never_publishes_partial_bundled_copy(
        self, monkeypatch, tmp_path
    ):
        monkeypatch.setattr("genshin_app.database_legacy_processes.packaged_peer_processes", list)
        bundled_dir = tmp_path / "meipass" / "bundled"
        bundled_dir.mkdir(parents=True)
        (bundled_dir / "genshin.db").write_bytes(b"reviewed database")
        monkeypatch.setattr(sys, "frozen", True, raising=False)
        monkeypatch.setattr(
            sys, "_MEIPASS", str(tmp_path / "meipass"), raising=False
        )

        def interrupted_copy(_source, target):
            paths.Path(target).write_bytes(b"partial")
            raise OSError("injected copy interruption")

        monkeypatch.setattr(paths.shutil, "copyfile", interrupted_copy)
        data_dir = tmp_path / "user_data"
        with pytest.raises(OSError, match="injected copy interruption"):
            paths.ensure_data_dir(data_dir)

        assert not (data_dir / "genshin.db").exists()
        assert not list(data_dir.glob(".genshin.db.*.tmp"))
