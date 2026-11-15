from types import SimpleNamespace

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
