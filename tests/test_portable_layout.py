from __future__ import annotations

import hashlib
import json
import os

import pytest

from genshin_app import paths, version
from genshin_app.portable_layout import (
    PORTABLE_MARKER,
    PORTABLE_MARKER_BYTES,
    resolve_portable_data,
)
from genshin_app.product_install_contract import FirstInstallError
from genshin_db.release_profile import (
    create_release_pair_manifest,
    write_release_pair_manifest,
)
from scripts.release_supply_chain import write_package_file_manifest
from tests.test_database_startup_gate import _identity, _manifest


def make_portable(tmp_path):
    root = tmp_path / "便携 副本"
    app = root / "app"
    bundled = app / "_internal/bundled"
    bundled.mkdir(parents=True)
    (app / "GenshinCalcGUI.exe").write_bytes(b"test fixture, never execute")
    (app / PORTABLE_MARKER).write_bytes(PORTABLE_MARKER_BYTES)
    (bundled / "genshin.db").write_bytes(b"current bundled database")
    manifest = _manifest(_identity(), channel="stable")
    (bundled / "database-release-manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    inventory = write_package_file_manifest(app)
    pair = create_release_pair_manifest(manifest, release_pair_id=version.RELEASE_PAIR_ID,
        pair_sequence=version.RELEASE_PAIR_SEQUENCE, pair_policy="exact", app_release_id=version.APP_RELEASE_ID,
        app_version=version.APP_VERSION, app_artifact_manifest_sha256=inventory["manifest_sha256"])
    write_release_pair_manifest(bundled / "release-pair-manifest.json", pair)
    return root, app


def frozen(monkeypatch, app):
    monkeypatch.setattr(paths.sys, "frozen", True, raising=False)
    monkeypatch.setattr(paths.sys, "_MEIPASS", str(app / "_internal"), raising=False)
    monkeypatch.setattr(paths.sys, "executable", str(app / "GenshinCalcGUI.exe"))
    monkeypatch.setattr("genshin_app.database_legacy_processes.packaged_peer_processes", list)


def test_bare_portable_uses_own_database_from_any_cwd_not_old_stable(tmp_path, monkeypatch):
    root, app = make_portable(tmp_path)
    old = tmp_path / "LocalAppData" / "GenshinCalcStable/data"
    old.mkdir(parents=True)
    (old / "genshin.db").write_bytes(b"old stable sentinel")
    (old / "session.json").write_bytes(b"old sessions")
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "LocalAppData"))
    unrelated = tmp_path / "other cwd"
    unrelated.mkdir()
    monkeypatch.chdir(unrelated)
    frozen(monkeypatch, app)
    assert paths.default_data_dir() == root / "data"
    assert paths.ensure_data_dir() == root / "data"
    assert (root / "data/genshin.db").read_bytes() == b"current bundled database"
    assert not (root / "data/session.json").exists()
    assert not (unrelated / "data").exists()
    assert (old / "genshin.db").read_bytes() == b"old stable sentinel"
    assert (old / "session.json").read_bytes() == b"old sessions"


def test_existing_portable_database_is_not_replaced_by_startup(tmp_path, monkeypatch):
    root, app = make_portable(tmp_path)
    (root / "data").mkdir()
    (root / "data/genshin.db").write_bytes(b"existing unapproved database")
    frozen(monkeypatch, app)
    paths.ensure_data_dir()
    assert (root / "data/genshin.db").read_bytes() == b"existing unapproved database"
    assert not (root / "data/database-live-receipt.json").exists()


def test_explicit_data_dir_keeps_precedence(tmp_path, monkeypatch):
    root, app = make_portable(tmp_path)
    frozen(monkeypatch, app)
    explicit = tmp_path / "user chosen"
    assert paths.ensure_data_dir(explicit) == explicit
    assert (explicit / "genshin.db").read_bytes() == b"current bundled database"
    assert not (root / "data").exists()


@pytest.mark.parametrize("attack", ["removed", "malformed", "path", "not-listed", "manifest-hash"])
def test_bad_or_missing_marker_never_falls_back_to_stable(tmp_path, monkeypatch, attack):
    root, app = make_portable(tmp_path)
    marker = app / PORTABLE_MARKER
    if attack == "removed":
        marker.unlink()
    elif attack == "malformed":
        marker.write_bytes(b"broken")
    elif attack == "path":
        marker.write_text('{"data_directory":"../../old-stable"}', encoding="utf-8")
    elif attack == "not-listed":
        package = app / "PACKAGE-FILES.sha256"
        package.write_bytes(b"".join(row for row in package.read_bytes().splitlines(keepends=True)
                                      if not row.endswith(b"  portable-layout.json\n")))
    else:
        package = app / "PACKAGE-FILES.sha256"
        package.write_bytes(package.read_bytes().replace(hashlib.sha256(PORTABLE_MARKER_BYTES).hexdigest().encode(), b"a" * 64))
    frozen(monkeypatch, app)
    with pytest.raises(FirstInstallError):
        paths.default_data_dir()
    assert not (root / "data").exists()


def test_invalid_installation_registration_cannot_be_masked_by_portable_marker(tmp_path, monkeypatch):
    root, app = make_portable(tmp_path)
    (root / "installation.json").write_text("{}", encoding="utf-8")
    frozen(monkeypatch, app)
    with pytest.raises(FirstInstallError, match="product_install_registration_invalid"):
        paths.default_data_dir()


def test_valid_registration_precedes_portable_marker(tmp_path, monkeypatch):
    root, app = make_portable(tmp_path)
    (app / PORTABLE_MARKER).write_bytes(b"not relevant to installed layout")
    (root / "installation.json").write_text(json.dumps({"contract": "genshin-product-first-install-v1",
        "scope": "current_user", "app_directory": "app", "data_directory": "data"}), encoding="utf-8")
    frozen(monkeypatch, app)
    assert paths.default_data_dir() == root / "data"


@pytest.mark.parametrize("member", ["data", "app", "marker"])
def test_reparse_paths_rejected_without_following_other_profile(tmp_path, member):
    root, app = make_portable(tmp_path)
    outside = tmp_path / "outside"
    outside.mkdir()
    if member == "data":
        target = root / "data"
        destination = outside
    elif member == "app":
        destination = root / "actual-app"
        app.rename(destination)
        target = app
    else:
        target = app / PORTABLE_MARKER
        destination = outside / "marker.json"
        target.rename(destination)
    try:
        target.symlink_to(destination, target_is_directory=member != "marker")
    except OSError as error:
        if os.name == "nt":
            pytest.skip(f"symlink creation unavailable: {error}")
        raise
    with pytest.raises(FirstInstallError):
        resolve_portable_data(app / "GenshinCalcGUI.exe")
    assert not (outside / "genshin.db").exists()


@pytest.mark.parametrize("layout", ["renamed-full-package", "independent-app"])
def test_unregistered_or_renamed_packages_cannot_read_stable_profile(tmp_path, monkeypatch, layout):
    root, app = make_portable(tmp_path)
    (app / PORTABLE_MARKER).unlink()
    if layout == "renamed-full-package":
        renamed = root / "renamed-app"
        app.rename(renamed)
        app = renamed
    else:
        import shutil
        shutil.rmtree(app / "_internal/bundled")
    frozen(monkeypatch, app)
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "old-stable-profile"))
    with pytest.raises(FirstInstallError, match="portable_layout_missing"):
        paths.default_data_dir()
    assert not (tmp_path / "old-stable-profile").exists()


@pytest.mark.parametrize("member", ["marker", "database"])
def test_hardlink_to_external_file_is_rejected(tmp_path, member):
    root, app = make_portable(tmp_path)
    if member == "marker":
        path = app / PORTABLE_MARKER
        outside = tmp_path / "external-marker"
        path.rename(outside)
    else:
        (root / "data").mkdir()
        path = root / "data/genshin.db"
        outside = tmp_path / "external-database"
        outside.write_bytes(b"unrelated data")
    os.link(outside, path)
    before = outside.read_bytes()
    with pytest.raises(FirstInstallError):
        resolve_portable_data(app / "GenshinCalcGUI.exe")
    assert outside.read_bytes() == before
