from __future__ import annotations

import hashlib
from types import SimpleNamespace

import pytest

from tests import pinned_native_node as helper


def provision(root, relative):
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"controlled unit test executable; never executed")
    return path


def test_explicit_node_wins_and_bad_explicit_path_does_not_fallback(tmp_path):
    source = provision(tmp_path, "agent-runtime/node.exe")
    assert helper.node_candidate(root=tmp_path, environment={}) == source
    missing = tmp_path / "missing-explicit.exe"
    env = {"GENSHIN_NATIVE_NODE": str(missing)}
    assert helper.node_candidate(root=tmp_path, environment=env) == missing
    with pytest.raises(Exception, match="runtime_integrity_node_(?:type|missing)"):
        helper.pinned_native_node(root=tmp_path, environment=env)


def test_source_before_exact_current_release_without_discovering_old_or_path_nodes(tmp_path):
    provision(tmp_path, "dist/release-0.46.0-r6/GenshinCalc/_internal/agent-runtime/node.exe")
    assert helper.node_candidate(root=tmp_path, environment={}) is None
    current = provision(tmp_path, f"dist/release-{helper.APP_RELEASE_ID.removeprefix('app-')}/GenshinCalc/_internal/agent-runtime/node.exe")
    assert helper.node_candidate(root=tmp_path, environment={}) == current
    source = provision(tmp_path, "agent-runtime/node.exe")
    assert helper.node_candidate(root=tmp_path, environment={}) == source
    assert helper.node_candidate(root=tmp_path, environment={"PATH": str(current.parent)}) == source


def test_wrong_sha_is_rejected_before_any_version_process(tmp_path, monkeypatch):
    provision(tmp_path, "agent-runtime/node.exe")
    monkeypatch.setattr(helper.subprocess, "run", lambda *_a, **_k: pytest.fail("untrusted binary executed"))
    with pytest.raises(RuntimeError, match="pinned_test_node_sha_mismatch"):
        helper.pinned_native_node(root=tmp_path, environment={})


@pytest.mark.parametrize("stdout,stderr", [("v0.0.1\n", ""), ("v24.19.0\n", "unexpected stderr")])
def test_trusted_payload_still_requires_exact_measured_version(tmp_path, monkeypatch, stdout, stderr):
    source = provision(tmp_path, "agent-runtime/node.exe")
    monkeypatch.setattr(helper, "NATIVE_AGENT_NODE_BINARY_SHA256", hashlib.sha256(source.read_bytes()).hexdigest())
    monkeypatch.setattr(helper.subprocess, "run", lambda *_a, **_k: SimpleNamespace(stdout=stdout, stderr=stderr))
    with pytest.raises(RuntimeError, match="pinned_test_node_version_mismatch"):
        helper.pinned_native_node(root=tmp_path, environment={})


def test_no_available_node_is_explicit_optional_or_required(tmp_path):
    assert helper.pinned_native_node(root=tmp_path, environment={}, required=False) is None
    with pytest.raises(RuntimeError, match="pinned_test_node_not_provisioned"):
        helper.pinned_native_node(root=tmp_path, environment={})


def test_real_source_node_is_verified_without_using_any_historical_release():
    node = helper.pinned_native_node(environment={})
    assert node == helper.ROOT / "agent-runtime/node.exe"


def test_alias_is_rejected_before_executing(tmp_path, monkeypatch):
    source = provision(tmp_path, "real/node.exe")
    alias = tmp_path / "alias.exe"
    try:
        alias.symlink_to(source)
    except OSError:
        pytest.skip("Windows symlink privilege unavailable")
    monkeypatch.setattr(helper.subprocess, "run", lambda *_a, **_k: pytest.fail("alias executed"))
    with pytest.raises(Exception, match="runtime_integrity_node_(?:type|alias)"):
        helper.pinned_native_node(root=tmp_path, environment={"GENSHIN_NATIVE_NODE": str(alias)})
