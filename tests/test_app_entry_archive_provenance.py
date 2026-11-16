"""The outer executable script must match source even when every PYZ module does."""
import json
import marshal
from pathlib import Path
from types import SimpleNamespace

import pytest

from genshin_engine.runtime_dependencies import CONTRACT, FROZEN_MANIFEST
from scripts import engine_runtime_release as release


@pytest.fixture
def entry_archive(monkeypatch, tmp_path):
    source = tmp_path / "project/src"
    source.mkdir(parents=True)
    text = "def main():\n    with installation_runtime_lease(root):\n        return start()\n"
    (source.parent / "app_entry.py").write_text(text)
    package = tmp_path / "package"
    package.mkdir()
    manifest = package / "_internal/genshin_engine" / FROZEN_MANIFEST
    manifest.parent.mkdir(parents=True)
    manifest.write_text(json.dumps({"contract": CONTRACT, "modules": []}))
    archives = {}
    for name in ("GenshinCalc.exe", "GenshinCalcGUI.exe"):
        (package / name).write_bytes(b"inert archive fixture; never execute")
        # Old source path is intentionally ignored; executable guards are not.
        code = compile(text, "old-machine/build/app_entry.py", "exec", dont_inherit=True)
        archives[name] = SimpleNamespace(
            toc={"PYZ.pyz": (0, 0, 0, 0, "z"), "app_entry": (0, 0, 0, 0, "s")},
            open_embedded_archive=lambda _: SimpleNamespace(toc={}),
            extract=lambda _, code=code: marshal.dumps(code),
        )
    monkeypatch.setattr("PyInstaller.archive.readers.CArchiveReader", lambda path: archives[Path(path).name])
    monkeypatch.setattr(release, "engine_runtime_dependency_descriptor", lambda: {"contract": "fixture", "package_data": [], "native_extensions": [], "families": []})
    monkeypatch.setattr(release, "engine_runtime_module_closure", lambda _: {})
    monkeypatch.setattr(release, "app_runtime_module_closure", lambda _: {})
    monkeypatch.setattr(release, "changed_project_modules", lambda _: set())
    return source, package, archives


def test_both_final_scripts_have_current_semantics_without_executing(entry_archive):
    source, package, _ = entry_archive
    result = release.validate_engine_package_archive(package, source_root=source)
    assert result["execution_verified"] is False
    assert len(result["entries"]) == 2
    assert all(row["entry_script"]["script"] == "app_entry" for row in result["entries"])


@pytest.mark.parametrize("entry", ["GenshinCalc.exe", "GenshinCalcGUI.exe"])
@pytest.mark.parametrize("defect,reason", [
    ("missing", "app_package_entry_script_missing"),
    ("wrong_type", "app_package_entry_script_missing"),
    ("marshal", "app_package_entry_script_invalid"),
    ("stale", "app_package_entry_source_mismatch"),
])
def test_current_pyz_cannot_hide_missing_or_stale_entry_guard(entry_archive, entry, defect, reason):
    source, package, archives = entry_archive
    archive = archives[entry]
    if defect == "missing":
        del archive.toc["app_entry"]
    elif defect == "wrong_type":
        archive.toc["app_entry"] = (0, 0, 0, 0, "b")
    elif defect == "marshal":
        archive.extract = lambda _: b"not valid marshal"
    else:
        previous = compile("def main():\n    return start()\n", "app_entry.py", "exec", dont_inherit=True)
        archive.extract = lambda _: marshal.dumps(previous)
    with pytest.raises(release.EngineRuntimePackageError) as caught:
        release.validate_engine_package_archive(package, source_root=source)
    assert caught.value.reason == reason
    assert caught.value.details == {"entry": entry, "script": "app_entry"}
