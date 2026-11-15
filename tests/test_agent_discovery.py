"""Discovery is read-only, bounded, and never confers executable trust by name."""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
from pathlib import Path

import pytest

from genshin_app.agent_chat import discovery as module
from genshin_app.agent_chat import transport

PINNED = b"MZ-local-synthetic-pinned-binary"
PIN = hashlib.sha256(PINNED).hexdigest()


def write(path, content=b"untrusted entry"):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    return path


def discover(paths=(), *, common=(), env=None, **kwargs):
    environment = {"PATH": ";".join(map(str, paths)), "PATHEXT": ".EXE;.CMD;.BAT;.PS1"}
    environment.update(env or {})
    return module.discover_agents(codex_version="0.147.0", codex_sha256=PIN,
                                  env=environment, windows=True,
                                  common_directories=common, **kwargs)


def package(prefix, name="@openai/codex", command="codex", *, native=False, version="0.147.0"):
    root = prefix / "node_modules" / name
    write(root / "bin" / f"{command}.js", b"do not execute me")
    manifest = {"name": name, "version": version, "bin": {command: f"bin/{command}.js"}}
    write(root / "package.json", json.dumps(manifest).encode())
    if native:
        write(root / "node_modules/@openai/codex-win32-x64/vendor/x86_64-pc-windows-msvc/bin/codex.exe", PINNED)
    return root


@pytest.fixture(autouse=True)
def no_processes(monkeypatch):
    def reject(*args, **kwargs):
        pytest.fail("Discovery must never execute a CLI or wrapper")
    monkeypatch.setattr(subprocess, "run", reject)
    monkeypatch.setattr(subprocess, "Popen", reject)
    monkeypatch.setattr(os, "system", reject)


def test_all_path_matches_keep_identical_hash_installs_separate(tmp_path):
    one = write(tmp_path / "first/codex.exe", PINNED)
    two = write(tmp_path / "second/codex.exe", PINNED)
    result = discover([one.parent, two.parent])
    assert {item["path"] for item in result["candidates"]} == {str(one), str(two)}
    assert all(item["supported"] and item["version"] == "0.147.0" for item in result["candidates"])
    assert len({item["id"] for item in result["candidates"]}) == 2
    assert len(result["scan"]["path_directories"]) == 2


def test_path_dedup_retains_all_sources(tmp_path):
    exe = write(tmp_path / "bin/codex.exe", PINNED)
    calls = []
    result = discover([exe.parent, exe.parent / "."], common=[(exe.parent, "已知安装目录")],
                      digest_file=lambda path: calls.append(path) or PIN)
    assert len(result["candidates"]) == len(calls) == 1
    assert result["candidates"][0]["sources"] == ["PATH 第 1 项", "PATH 第 2 项", "已知安装目录"]
    assert len(result["scan"]["scanned_directories"]) == 1


def test_wrapper_is_never_executed_or_blessed_by_nearby_package(tmp_path):
    prefix = tmp_path / "npm"
    root = package(prefix, native=True)
    for extension in ("", ".cmd", ".ps1"):
        write(prefix / ("codex" + extension), b"throw 'must never execute'; exfiltrate secrets")
    other = write(prefix / "codex.exe", b"MZ-another-independent-install")
    result = discover([prefix])
    candidates = result["candidates"]
    assert len(candidates) == 5
    supported = [item for item in candidates if item["supported"]]
    assert len(supported) == 1
    assert supported[0]["path"].startswith(str(root))
    assert str(other) not in supported[0]["entry_paths"]
    assert all("exfiltrate" not in json.dumps(item) for item in candidates)
    assert all(not item["supported"] for item in candidates if item not in supported)


def test_multiple_products_extensions_and_regular_editor_not_agent(tmp_path):
    commands = {"claude.EXE", "gemini.cmd", "opencode.ps1", "aider.bat", "goose.com",
                "agent", "cursor-agent.cmd", "cursor.exe", "codearts.cmd", "kiro-cli.exe"}
    for name in commands:
        write(tmp_path / name)
    result = discover([tmp_path], env={"PATHEXT": ".EXE;.CMD;.BAT;.PS1;.COM"})
    assert len(result["candidates"]) == len(commands) - 1
    assert not any(Path(item["path"]).name == "cursor.exe" for item in result["candidates"])
    assert {item["provider_id"] for item in result["candidates"]} == {
        "claude", "gemini", "opencode", "aider", "goose", "cursor", "codearts", "kiro"}
    assert all(not item["supported"] for item in result["candidates"])
    assert all("重名" in item["reason"] for item in result["candidates"]
               if item["provider_id"] in {"cursor", "codearts"})


def test_npm_metadata_version_is_hint_not_execution_authority(tmp_path):
    package(tmp_path, "@google/gemini-cli", "gemini", version="1.2.3")
    result = discover([tmp_path])
    candidate, = result["candidates"]
    assert candidate["provider_id"] == "gemini"
    assert candidate["version"] == "1.2.3"
    assert candidate["version_source"] == "package_metadata"
    assert candidate["kind"] == "npm_wrapper"
    assert not candidate["supported"] and candidate["sha256"] is None


@pytest.mark.parametrize("bad_manifest", [
    b"not json", b"[]", b'{"name":"not-the-registered-package"}',
    b'{"name":"@openai/codex","bin":{"codex":"../../../../outside.js"}}',
    b'{"name":"@openai/codex","bin":{"codex":"C:/outside.js"}}',
    b'{"name":"@openai/codex","bin":{"codex":"//server/share/a.js"}}',
    b'{"name":"@openai/codex","bin":[]}',
])
def test_malformed_or_escaping_package_does_not_abort_or_authorize(tmp_path, bad_manifest):
    root = package(tmp_path)
    (root / "package.json").write_bytes(bad_manifest)
    write(tmp_path / "claude.cmd")
    result = discover([tmp_path])
    assert [item["provider_id"] for item in result["candidates"]] == ["claude"]
    assert result["scan"]["issues"] and not result["scan"]["complete"]


def test_oversized_package_is_not_read(tmp_path, monkeypatch):
    root = package(tmp_path)
    manifest = root / "package.json"
    manifest.write_bytes(b"x" * (module.MAX_MANIFEST_BYTES + 1))
    original = Path.open
    def guarded(path, *args, **kwargs):
        if path == manifest:
            pytest.fail("Oversized metadata must not be opened")
        return original(path, *args, **kwargs)
    monkeypatch.setattr(Path, "open", guarded)
    result = discover([tmp_path])
    assert result["scan"]["issues"] and not result["candidates"]


def test_unreadable_directory_isolated_from_other_paths(tmp_path, monkeypatch):
    bad = tmp_path / "denied"
    bad.mkdir()
    good = write(tmp_path / "good/codex.exe", PINNED)
    original = os.scandir
    def guarded(path):
        if Path(path) == bad:
            raise PermissionError("do not reveal environment details")
        return original(path)
    monkeypatch.setattr(os, "scandir", guarded)
    result = discover([bad, good.parent])
    assert len(result["candidates"]) == 1 and result["candidates"][0]["supported"]
    assert result["scan"]["issues"][0]["path"] == str(bad)


def test_bad_path_unc_and_pathext_report_incomplete_without_stopping(tmp_path):
    write(tmp_path / "qwen.cmd")
    result = discover(["\\\\unreachable.invalid\\share", "bad\x00path", tmp_path],
                      env={"PATHEXT": ".CMD;../bad"})
    assert len(result["candidates"]) == 1
    assert len(result["scan"]["issues"]) == 3
    assert not result["scan"]["complete"]


def test_common_dirs_case_insensitive_environment_and_versioned_desktop(tmp_path):
    user = tmp_path / "user"
    local = tmp_path / "local"
    program_files = tmp_path / "Program Files"
    write(user / ".codeartsdoer/installers/codearts.cmd")
    write(local / "cursor-agent/agent.exe")
    pinned = write(local / "OpenAI/Codex/bin/de8a38d2100ae498/codex.exe", PINNED)
    write(program_files / "Kiro-Cli/kiro-cli.exe")
    result = discover(common=None, env={"UserProfile": str(user), "LocalAppData": str(local),
                                       "ProgramFiles": str(program_files)})
    assert {item["provider_id"] for item in result["candidates"]} == {"codearts", "cursor", "codex", "kiro"}
    assert next(item for item in result["candidates"] if item["path"] == str(pinned))["supported"]
    assert result["scan"]["complete"]


def test_native_fingerprint_rejects_metadata_spoof(tmp_path):
    root = package(tmp_path, native=True)
    native = next(root.rglob("codex.exe"))
    native.write_bytes(b"MZ-not-the-verified-program")
    candidate, = discover([tmp_path])["candidates"]
    assert candidate["version"] == "0.147.0"  # only package-reported
    assert candidate["version_source"] == "package_metadata"
    assert not candidate["supported"]
    assert candidate["status"] == "unverified"


def test_directory_limits_are_explicit(tmp_path, monkeypatch):
    write(tmp_path / "codex.exe", PINNED)
    monkeypatch.setattr(module, "MAX_DIRECTORY_ENTRIES", 0)
    result = discover([tmp_path])
    assert not result["candidates"] and not result["scan"]["complete"]
    assert "上限" in result["scan"]["issues"][0]["message"]


def test_registry_contract_and_transport_bridge(monkeypatch):
    def fake(**kwargs):
        assert kwargs["codex_version"] == transport.SUPPORTED_VERSION
        assert kwargs["codex_sha256"] == transport.SUPPORTED_SHA256
        return {"sentinel": True}
    monkeypatch.setattr(module, "discover_agents", fake)
    assert transport.detect_cli() == {"sentinel": True}
    assert len(module.PROVIDERS) == 15
    assert "cursor" not in next(p.commands for p in module.PROVIDERS if p.id == "cursor")


def test_metadata_hardlink_rejected_without_reading(tmp_path):
    root = package(tmp_path)
    original = root / "package.json"
    hardlink = tmp_path / "private-metadata.json"
    try:
        os.link(original, hardlink)
    except OSError as exc:
        pytest.skip(f"当前测试卷或ACL不允许创建硬链接：{exc}")
    result = discover([tmp_path])
    assert not result["candidates"]
    assert "普通小文件" in result["scan"]["issues"][0]["message"]


def test_missing_package_entry_reports_incomplete(tmp_path):
    root = package(tmp_path)
    (root / "bin/codex.js").unlink()
    result = discover([tmp_path])
    assert not result["candidates"]
    assert "缺少" in result["scan"]["issues"][0]["message"]


def test_directory_named_as_command_is_not_candidate(tmp_path):
    (tmp_path / "codex.exe").mkdir()
    result = discover([tmp_path])
    assert not result["candidates"]
    assert "普通文件" in result["scan"]["issues"][0]["message"]


def test_package_metadata_symlink_escape_never_read(tmp_path, monkeypatch):
    root = package(tmp_path / "npm")
    original = root / "package.json"
    outside = tmp_path / "outside.json"
    outside.write_bytes(original.read_bytes())
    original.unlink()
    try:
        original.symlink_to(outside)
    except OSError:
        pytest.skip("Windows symlink privilege unavailable")
    real_open = Path.open
    def guarded(path, *args, **kwargs):
        if path in {original, outside}:
            pytest.fail("Out-of-scope metadata must not be opened")
        return real_open(path, *args, **kwargs)
    monkeypatch.setattr(Path, "open", guarded)
    result = discover([tmp_path / "npm"])
    assert not result["candidates"]
    assert "安装范围外" in result["scan"]["issues"][0]["message"]
