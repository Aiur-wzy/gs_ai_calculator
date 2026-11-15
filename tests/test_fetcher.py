"""fetcher 模块测试：不依赖外网，用本地 git 仓库模拟上游。"""

import subprocess

import pytest

from genshin_db import fetcher


def _git(args: list[str], cwd) -> None:
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True)


@pytest.fixture
def fake_upstream(tmp_path, monkeypatch):
    """本地 fake 上游仓库（main 分支，一个提交），并指向 fetcher。"""
    repo = tmp_path / "fake_upstream"
    repo.mkdir()
    _git(["init", "-b", "main"], repo)
    _git(["config", "user.email", "test@example.com"], repo)
    _git(["config", "user.name", "Test User"], repo)
    (repo / "test.txt").write_text("hello", encoding="utf-8")
    _git(["add", "test.txt"], repo)
    _git(["commit", "-m", "initial"], repo)
    monkeypatch.setattr(fetcher, "UPSTREAM_URL", str(repo))
    return repo


def test_first_clone_changed(fake_upstream, tmp_path):
    cache_dir = tmp_path / "cache"
    result = fetcher.ensure_upstream(cache_dir)
    assert result.changed is True
    assert len(result.commit) == 40
    assert all(c in "0123456789abcdef" for c in result.commit)
    assert (cache_dir / "repo" / "test.txt").exists()
    assert result.snapshot_updated_at
    assert result.version_evidence["snapshot_commit"] == result.commit
    assert result.version_evidence["status"] == "unresolved"


def test_second_call_unchanged(fake_upstream, tmp_path):
    cache_dir = tmp_path / "cache"
    result1 = fetcher.ensure_upstream(cache_dir)
    result2 = fetcher.ensure_upstream(cache_dir)
    assert result2.changed is False
    assert result2.commit == result1.commit


def test_managed_cache_rejects_untracked_injection(fake_upstream, tmp_path):
    cache_dir = tmp_path / "cache"
    fetcher.ensure_upstream(cache_dir)
    (cache_dir / "repo" / "injected.json").write_text("{}", encoding="utf-8")

    with pytest.raises(fetcher.FetcherError, match="未提交或未跟踪"):
        fetcher.ensure_upstream(cache_dir)


def test_selected_ignored_file_cannot_impersonate_commit_source(fake_upstream, tmp_path):
    (fake_upstream / ".gitignore").write_text("ignored.json\n", encoding="utf-8")
    _git(["add", ".gitignore"], fake_upstream)
    _git(["commit", "-m", "ignore fixture"], fake_upstream)
    commit = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=fake_upstream,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    payload = b"{}"
    (fake_upstream / "ignored.json").write_bytes(payload)
    import hashlib

    manifest = {
        "files": {
            "ignored.json": {
                "sha256": hashlib.sha256(payload).hexdigest(),
                "size_bytes": len(payload),
            }
        },
        "fingerprint": "f" * 64,
    }
    with pytest.raises(fetcher.FetcherError, match="固定commit"):
        fetcher.verify_pinned_sources(fake_upstream, commit, manifest)


def test_new_commit_detected(fake_upstream, tmp_path):
    cache_dir = tmp_path / "cache"
    result1 = fetcher.ensure_upstream(cache_dir)
    (fake_upstream / "new.txt").write_text("new file", encoding="utf-8")
    _git(["add", "new.txt"], fake_upstream)
    _git(["commit", "-m", "new commit"], fake_upstream)
    result2 = fetcher.ensure_upstream(cache_dir)
    assert result2.changed is True
    assert result2.commit != result1.commit
    assert len(result2.commit) == 40


def test_git_failure_raises(tmp_path, monkeypatch):
    monkeypatch.setattr(fetcher, "UPSTREAM_URL", str(tmp_path / "not-exist-repo"))
    with pytest.raises(fetcher.FetcherError):
        fetcher.ensure_upstream(tmp_path / "cache")
