from types import SimpleNamespace

import pytest

from scripts import packaging_windows_runtime


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
    with pytest.raises(ValueError, match="归一化档案"):
        packaging_windows_runtime.runtime_validation_datas(tmp_path)
    registry.write_text("{}", encoding="utf-8")
    assert packaging_windows_runtime.runtime_validation_datas(tmp_path) == [
        (str(path), "genshin_runtime/resources") for path in (recipes, registry)
    ]
