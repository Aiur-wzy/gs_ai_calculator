from pathlib import Path
from types import SimpleNamespace

import pytest

from scripts import packaging_windows_runtime, verify_frozen_setup_archive


def test_windows_packaging_uses_os_crt_and_leaves_nested_payload_intact(monkeypatch):
    monkeypatch.setattr(packaging_windows_runtime, "os", SimpleNamespace(name="nt"))
    source = "redist"
    remove = [
        ("UCRTBASE.DLL", source, "BINARY"),
        ("api-ms-win-crt-runtime-l1-1-0.dll", source, "BINARY"),
        ("api-ms-win-eventing-provider-l1-1-0.dll", source, "BINARY"),
    ]
    keep = [
        ("vcruntime140.dll", source, "BINARY"),
        ("vcruntime140_1.dll", source, "BINARY"),
        ("tk86t.dll", source, "BINARY"),
        ("agent-runtime/node_modules/native/ucrtbase.dll", source, "BINARY"),
        ("agent-runtime\\node_modules\\native\\api-ms-win-crt-runtime-l1-1-0.dll", source, "BINARY"),
    ]
    assert packaging_windows_runtime.omit_windows_system_crt(remove + keep) == keep


def test_release_packages_require_the_runtime_normalization_registry(tmp_path):
    resources = tmp_path / "src/genshin_runtime/resources"
    resources.mkdir(parents=True)
    registry = resources / "profiles.json"
    recipes = resources / "action_dispatch_profiles.json"
    recipes.write_text("{}", encoding="utf-8")
    with pytest.raises(ValueError, match="runtime_validation_registry_missing"):
        packaging_windows_runtime.runtime_validation_datas(tmp_path)
    registry.write_text("{}", encoding="utf-8")
    assert packaging_windows_runtime.runtime_validation_datas(tmp_path) == [
        (str(path), "genshin_runtime/resources") for path in (recipes, registry)
    ]


def test_setup_archive_checks_new_maintenance_transaction_modules():
    common = set(verify_frozen_setup_archive.COMMON_MODULES)
    setup = common | set(verify_frozen_setup_archive.ENTRY_MODULES["GenshinCalcSetup.exe"])
    assert {"genshin_app.installation_location", "genshin_app.installation_inventory"} <= common
    assert {"genshin_app.managed_installation", "genshin_app.installation_migration",
            "genshin_app.windows_product_registration", "genshin_app.windows_shell_identity"} <= setup


@pytest.mark.parametrize("entry_name", ["GenshinCalcSetup.exe", "GenshinCalcUpdater.exe"])
@pytest.mark.parametrize("fault,expected_error", [
    ("none", None),
    ("missing_resource", "runtime_validation_resource_missing:action_dispatch_profiles.json"),
    ("wrong_resource", "runtime_validation_resource_mismatch:action_dispatch_profiles.json"),
    ("extra_resource", "runtime_validation_resource_inventory_mismatch"),
    ("missing_module", "setup_archive_module_missing"),
    ("wrong_entry", "setup_archive_module_mismatch"),
])
def test_static_qualification_opens_actual_pyinstaller_archives(
    tmp_path, entry_name, fault, expected_error,
):
    from PyInstaller.archive.writers import CArchiveWriter, ZlibArchiveWriter

    script_name = "installer_entry" if entry_name == "GenshinCalcSetup.exe" else "updater_entry"
    entry_source = tmp_path / f"{script_name}.py"
    entry_source.write_text("VALUE = 1\n", encoding="utf-8")
    replacement = tmp_path / "replacement.py"
    replacement.write_text("VALUE = 2\n", encoding="utf-8")
    source_root = tmp_path / "src"
    resources = source_root / "genshin_runtime/resources"
    resources.mkdir(parents=True)
    profile = resources / "profiles.json"
    profile.write_bytes(b'{"normalization": 1}\n')
    dispatch = resources / "action_dispatch_profiles.json"
    dispatch.write_bytes(b'{"dispatch": 1}\n')
    altered_resource = tmp_path / "altered.json"
    altered_resource.write_bytes(b'{"dispatch": 2}\n')
    data_entries = [
        (f"genshin_runtime/resources/{file.name}", str(file), True, "x")
        for file in (profile, dispatch) if file != dispatch or fault != "missing_resource"
    ]
    if fault in {"wrong_resource", "extra_resource"}:
        name = dispatch.name if fault == "wrong_resource" else "unknown.json"
        if fault == "wrong_resource":
            data_entries.pop()
        data_entries.append((f"genshin_runtime/resources/{name}", str(altered_resource), True, "x"))

    modules = verify_frozen_setup_archive.COMMON_MODULES + verify_frozen_setup_archive.ENTRY_MODULES[entry_name]
    compiled_modules = {}
    pyz_entries = []
    for name in modules:
        source = source_root / Path(*name.split(".")).with_suffix(".py")
        source.parent.mkdir(parents=True, exist_ok=True)
        module_code = "VALUE = 3\n"
        source.write_text(module_code, encoding="utf-8")
        if fault != "missing_module" or name != "genshin_db.runtime_store":
            pyz_entries.append((name, str(source), "PYMODULE"))
            compiled_modules[name] = compile(module_code, str(source), "exec")
    inner = tmp_path / "PYZ.pyz"
    ZlibArchiveWriter(inner, pyz_entries, code_dict=compiled_modules)
    outer = tmp_path / entry_name
    entry = replacement if fault == "wrong_entry" else entry_source
    CArchiveWriter(outer, [
        ("PYZ.pyz", str(inner), False, "z"),
        (script_name, str(entry), True, "s"),
        *data_entries,
    ], pylib_name="python312.dll")

    if expected_error:
        with pytest.raises(ValueError, match=expected_error):
            verify_frozen_setup_archive.verify_archive(outer, tmp_path)
    else:
        proof = verify_frozen_setup_archive.verify_archive(outer, tmp_path)
        assert proof["status"] == "verified"
        assert proof["entry"] == script_name
        assert proof["modules"] == sorted(modules)
        assert set(proof["runtime_validation_resource_sha256"]) == {
            "genshin_runtime/resources/profiles.json", "genshin_runtime/resources/action_dispatch_profiles.json",
        }
