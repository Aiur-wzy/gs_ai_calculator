"""COLLECT omissions are repaired only by publishing authenticated directories."""
from __future__ import annotations

import pytest

from scripts import native_agent_release as release


@pytest.fixture
def layout(tmp_path):
    stage, package = tmp_path / "stage", tmp_path / "package"
    source = stage / "agent-runtime/node_modules"
    target = package / "_internal/agent-runtime/node_modules"
    (source / "example/node_modules/empty").mkdir(parents=True)
    (source / "other-empty").mkdir()
    (source / "example/code.js").write_bytes(b"unchanged package code\n")
    (target / "example").mkdir(parents=True)
    (target / "example/code.js").write_bytes(b"unchanged package code\n")
    return stage, package, source, target


def publish(layout, *, count=4):
    stage, package, _source, _target = layout
    return release.preserve_packaged_native_dependency_directories(
        staging_root=stage, package_root=package, expected_directories=count)


def files(root):
    return {path.relative_to(root).as_posix(): path.read_bytes()
            for path in root.rglob("*") if path.is_file()}


def test_publishes_exact_empty_structure_with_zero_file_writes_and_is_idempotent(layout):
    _stage, _package, source, target = layout
    before = files(target)
    assert publish(layout) == {"contract": "genshin-native-directory-publication-v1",
        "directories": 4, "created_directories": 3, "files_written": 0}
    assert files(target) == before
    assert {p.relative_to(source) for p in source.rglob("*") if p.is_dir()} == {
        p.relative_to(target) for p in target.rglob("*") if p.is_dir()}
    assert release._validate_link_graph(target) == release._validate_link_graph(source)
    assert publish(layout)["created_directories"] == 0


@pytest.mark.parametrize("count", [0, 3, 5])
def test_source_count_drift_rejects_before_any_publication(layout, count):
    target = layout[3]
    with pytest.raises(release.NativeAgentReleaseError, match="source_count_mismatch"):
        publish(layout, count=count)
    assert not (target / "other-empty").exists()
    assert not (target / "example/node_modules").exists()


@pytest.mark.parametrize("count", [True, -1, None, "4"])
def test_invalid_expected_count_is_not_coerced(layout, count):
    with pytest.raises(release.NativeAgentReleaseError, match="directory_count_invalid"):
        publish(layout, count=count)


@pytest.mark.parametrize("which", ["source", "target"])
def test_reparse_directory_is_rejected_before_creating_anything(layout, monkeypatch, which):
    _stage, _package, source, target = layout
    poisoned = source / "example" if which == "source" else target / "example"
    original = release._is_reparse_point
    monkeypatch.setattr(release, "_is_reparse_point", lambda path: path == poisoned or original(path))
    with pytest.raises(release.NativeAgentReleaseError):
        publish(layout)
    assert not (target / "other-empty").exists()


@pytest.mark.parametrize("which", [0, 1])
def test_noncanonical_source_or_destination_is_not_an_escape_path(layout, which):
    selected = list(layout)
    selected[which] = selected[which] / ".." / selected[which].name
    with pytest.raises(release.NativeAgentReleaseError, match="publication_path_invalid"):
        publish(selected)


def test_source_and_destination_cannot_overlap(layout):
    stage = layout[0]
    with pytest.raises(release.NativeAgentReleaseError, match="roots_overlap"):
        release.preserve_packaged_native_dependency_directories(
            staging_root=stage, package_root=stage, expected_directories=4)


def test_file_collision_is_never_replaced(layout):
    target = layout[3]
    collision = target / "other-empty"
    collision.write_bytes(b"must remain a file")
    with pytest.raises(release.NativeAgentReleaseError, match="publication_path_invalid"):
        publish(layout)
    assert collision.read_bytes() == b"must remain a file"
    assert not (target / "example/node_modules").exists()


@pytest.mark.parametrize("corruption", ["missing", "modified", "extra_directory"])
def test_publisher_cannot_conceal_a_file_or_layout_failure(layout, corruption):
    source, target = layout[2:]
    if corruption == "missing":
        (target / "example/code.js").unlink()
    elif corruption == "modified":
        (target / "example/code.js").write_bytes(b"tampered")
    else:
        (target / "unexpected").mkdir()
    before = files(target)
    publish(layout)
    assert files(target) == before
    assert release._validate_link_graph(target) != release._validate_link_graph(source)


def test_layout_exception_keeps_original_code_and_path_free_metrics():
    error = release.NativeDependencyLayoutError("native_release_package_dependency_layout_mismatch",
        expected={"directories": 4, "tree_sha256": "a" * 64, "private_path": "never include"},
        actual={"directories": 1, "tree_sha256": "a" * 64})
    assert error.code == "native_release_package_dependency_layout_mismatch"
    assert error.diagnostic["different_fields"] == ["directories"]
    assert error.diagnostic["qualification"] is False
    assert "private_path" not in error.diagnostic["expected"]
