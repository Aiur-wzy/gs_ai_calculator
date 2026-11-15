from __future__ import annotations

import json

import pytest

from genshin_db import runtime_catalog as catalog


def _write(root, relative, value):
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")


def _complete_layout(root):
    for directory in catalog.DIRECTORY_FAMILIES.values():
        _write(root, f"{directory}/Fixture.json", {})
    for paths in catalog.EXACT_FAMILIES.values():
        for relative in paths:
            _write(root, relative, {})


def test_layout_reports_optional_families_as_unavailable_without_blocking(tmp_path):
    _complete_layout(tmp_path)
    (tmp_path / "BinOutput/Gadget/Fixture.json").unlink()
    (tmp_path / "BinOutput/AnimatorConfig/Fixture.json").unlink()
    (tmp_path / catalog.GLOBAL_COMBAT_PATH).unlink()
    (tmp_path / "BinOutput/AnimPattern/AnimPatternData.json").unlink()

    report = catalog.inspect_runtime_source_layout(tmp_path)

    assert report["compatible"] is True
    assert report["complete"] is False
    assert report["capability_state"] == "degraded"
    assert report["missing_paths"] == [
        "BinOutput/AnimPattern/AnimPatternData.json",
        "BinOutput/AnimatorConfig",
        "BinOutput/Common/ConfigGlobalCombat.json",
        "BinOutput/Gadget",
    ]
    assert report["contract"] == "genshin-runtime-source-layout-v1"
    assert len(report["fingerprint"]) == 64
    assert report["required_missing_paths"] == []
    assert report["optional_missing_paths"] == report["missing_paths"]
    assert report["capabilities"]["gadget"] == {
        "state": "unavailable", "requirement": "optional"
    }
    manifest = catalog.enumerate_runtime_sources(tmp_path)
    assert manifest["capability_state"] == "degraded"
    assert all(
        spec["family"] not in {"gadget", "animator", "global_combat", "animation_pattern"}
        for spec in manifest["files"].values()
    )


def test_layout_still_rejects_missing_required_family(tmp_path):
    _complete_layout(tmp_path)
    (tmp_path / "BinOutput/Talent/Fixture.json").unlink()

    report = catalog.inspect_runtime_source_layout(tmp_path)

    assert report["compatible"] is False
    assert report["capability_state"] == "unavailable"
    assert report["required_missing_paths"] == ["BinOutput/Talent"]
    with pytest.raises(catalog.RuntimeConfigError) as caught:
        catalog.enumerate_runtime_sources(tmp_path)
    assert caught.value.reason == "source_missing"


def test_layout_ready_is_stable_and_enumeration_still_owns_payload_validation(tmp_path):
    _complete_layout(tmp_path)

    first = catalog.inspect_runtime_source_layout(tmp_path)
    second = catalog.inspect_runtime_source_layout(tmp_path)

    assert first == second
    assert first["compatible"] is True
    assert first["missing_paths"] == []
    # The layout preflight only proves presence.  Existing enumeration remains
    # responsible for source size, path and payload constraints.
    manifest = catalog.enumerate_runtime_sources(tmp_path)
    assert manifest["profile_id"] == catalog.PROFILE_ID
