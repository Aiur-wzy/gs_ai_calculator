"""Pinned source fixtures cannot drift or fall back to a live checkout."""
from __future__ import annotations

import copy
import hashlib
import io
import json
import stat
import subprocess
import zipfile

import pytest

from tests import historical_runtime_sources as sources


def fixture():
    manifest = json.loads((sources._ROOT / "manifest.json").read_bytes())
    data = (sources._ROOT / manifest["archive"]["filename"]).read_bytes()
    return manifest, data


def archive(items, *, symlink=None):
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as writer:
        for name, data in items:
            info = zipfile.ZipInfo(name)
            info.create_system = 3
            info.external_attr = ((stat.S_IFLNK if name == symlink else stat.S_IFREG) | 0o644) << 16
            writer.writestr(info, data)
    return output.getvalue()


def reseal(manifest, data):
    value = copy.deepcopy(manifest)
    value["archive"].update(sha256=hashlib.sha256(data).hexdigest(), size_bytes=len(data))
    return value


def test_exact_eighteen_members_match_both_identities_and_original_resource():
    manifest, data = fixture()
    assert len(manifest["sources"]) == manifest["member_count"] == 18
    assert hashlib.sha256(data).hexdigest() == "3855f7867899f6247618662069bc2b09f0860b0c760f9839333576e844569c64"
    expected = {}
    for row in manifest["sources"]:
        value = sources.read_source_bytes(row["source_path"])
        assert hashlib.sha256(value).hexdigest() == row["workspace_sha256"]
        assert len(value) == row["workspace_bytes"]
        expected[row["source_path"]] = row["workspace_sha256"]
    from importlib import resources
    passive = json.loads(resources.files("genshin_runtime").joinpath("resources/passive_benefit_profiles.json").read_text("utf8"))
    assert passive["commit"] == sources.HISTORICAL_REVIEWED_COMMIT
    assert len(passive["files"]) == 6
    for row in passive["files"]:
        assert expected[row["source_path"]] == row["workspace_source_sha256"]
    assert {"ExcelBinOutput/AvatarSkillExcelConfigData.json", "ExcelBinOutput/AttackAttenuationExcelConfigData.json"} <= expected.keys()


def test_no_git_or_checkout_is_needed_and_missing_fixed_data_is_not_skipped(monkeypatch, tmp_path):
    monkeypatch.setattr(subprocess, "run", lambda *args, **kwargs: pytest.fail("Git/current checkout fallback"))
    value = sources.read_source_bytes("ExcelBinOutput/AvatarExcelConfigData.json")
    assert hashlib.sha256(value).hexdigest() == "bc9a4a11b7e0e40c6eadd75ca2c99752b17802521591a8df65f4995c5b0e4801"
    monkeypatch.setattr(sources, "_ROOT", tmp_path / "missing")
    with pytest.raises(FileNotFoundError):
        sources.read_source_bytes("ExcelBinOutput/AvatarExcelConfigData.json")


@pytest.mark.parametrize("path", [None, True, "", "/outside.json", "../outside.json", "a/../b", "a\\b", "C:/a", "a//b"])
def test_paths_cannot_escape_the_fixed_members(path):
    with pytest.raises(ValueError, match="historical_source_path_invalid"):
        sources.read_source_bytes(path)


def test_unknown_member_is_explicitly_unavailable():
    with pytest.raises(KeyError, match="historical_source_not_in_snapshot"):
        sources.read_source_bytes("ExcelBinOutput/NotInReviewedFixture.json")


@pytest.mark.parametrize("kind", ["manifest", "archive"])
def test_public_entry_checks_raw_pins_before_consuming_any_member(tmp_path, monkeypatch, kind):
    manifest, data = fixture()
    raw_manifest = (sources._ROOT / "manifest.json").read_bytes()
    if kind == "manifest":
        raw_manifest += b" "
    else:
        data += b"unexpected"
    (tmp_path / "manifest.json").write_bytes(raw_manifest)
    (tmp_path / manifest["archive"]["filename"]).write_bytes(data)
    monkeypatch.setattr(sources, "_ROOT", tmp_path)
    with pytest.raises(ValueError, match=f"historical_source_{kind}_identity"):
        sources.read_source_bytes(manifest["sources"][0]["source_path"])


@pytest.mark.parametrize("mutation", ["missing", "extra", "duplicate", "symlink", "blob"])
def test_structural_or_blob_fault_is_rejected_even_after_archive_resealing(mutation):
    manifest, data = fixture()
    with zipfile.ZipFile(io.BytesIO(data)) as reader:
        items = [(name, reader.read(name)) for name in reader.namelist()]
    selected = items[0][0]
    symlink = None
    if mutation == "missing":
        items.pop()
    elif mutation == "extra":
        items.append(("../outside.json", b"{}"))
    elif mutation == "duplicate":
        items.append(items[-1])
    elif mutation == "symlink":
        symlink = selected
    else:
        items[0] = (selected, items[0][1][:-1] + b"!")
    if mutation == "duplicate":
        with pytest.warns(UserWarning, match="Duplicate name"):
            changed = archive(items)
    else:
        changed = archive(items, symlink=symlink)
    with pytest.raises(ValueError, match="historical_source_(archive_members|member_type_size|blob_identity)"):
        sources._read_verified_member(changed, reseal(manifest, changed), selected)


@pytest.mark.parametrize("field,value,reason", [
    ("git_blob_sha256", "0"*64, "blob_identity"),
    ("workspace_sha256", "0"*64, "workspace_identity"),
    ("materialization", "silently_use_current", "representation"),
])
def test_each_declared_byte_identity_is_required(field, value, reason):
    manifest, data = fixture()
    changed = copy.deepcopy(manifest)
    changed["sources"][0][field] = value
    with pytest.raises(ValueError, match="historical_source_" + reason):
        sources._read_verified_member(data, changed, changed["sources"][0]["source_path"])
