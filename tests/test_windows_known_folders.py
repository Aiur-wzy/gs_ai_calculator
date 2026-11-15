from __future__ import annotations

import os

import pytest

from genshin_app import windows_known_folders as folders


@pytest.mark.skipif(os.name != "nt", reason="Windows effective ACL probe")
def test_effective_write_probe_rejects_user_writable_file_and_directory(tmp_path):
    target = tmp_path / "updater.exe"
    target.write_bytes(b"fixture")
    for item in (tmp_path, target):
        with pytest.raises(folders.WindowsPathAuthorityError) as failure:
            folders.reject_effective_write_access(item, reason="writable")
        assert failure.value.reason == "writable"


def test_reparse_chain_rejects_parent_alias(tmp_path):
    real = tmp_path / "real"
    real.mkdir()
    (real / "payload").write_text("fixture", encoding="utf-8")
    alias = tmp_path / "alias"
    try:
        alias.symlink_to(real, target_is_directory=True)
    except OSError:
        pytest.skip("symlink creation unavailable")
    with pytest.raises(folders.WindowsPathAuthorityError) as failure:
        folders.reject_reparse_chain(alias / "payload", reason="reparse")
    assert failure.value.reason == "reparse"


def test_direct_existing_path_resolves_without_environment(tmp_path, monkeypatch):
    target = tmp_path / "direct"
    target.mkdir()
    monkeypatch.setenv("PROGRAMDATA", str(tmp_path / "attacker"))
    assert folders.reject_reparse_chain(target, reason="invalid") == target.resolve()
