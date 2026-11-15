from pathlib import Path

import pytest

from genshin_app import database_bootstrap as bootstrap
from genshin_app import version


def _freeze(monkeypatch, tmp_path):
    resources = tmp_path / "internal"
    executable = tmp_path / "package" / "GenshinCalcGUI.exe"
    monkeypatch.setattr(bootstrap, "is_frozen", lambda: True)
    monkeypatch.setattr(bootstrap, "resource_dir", lambda: resources)
    monkeypatch.setattr(bootstrap.sys, "executable", str(executable))
    return resources, executable.parent


def test_current_build_pins_drive_development_frozen_policy(monkeypatch, tmp_path):
    resources, package = _freeze(monkeypatch, tmp_path)
    assert version.DATABASE_RELEASE_CHANNEL == "dev"
    assert version.DATABASE_PAIR_POLICY == "current_only"

    root, policy, package_root, frozen = bootstrap._resolve_startup_flavor(
        resource_root=None,
        pair_policy=None,
        app_package_root=None,
    )

    assert (root, policy, package_root, frozen) == (
        resources,
        "current_only",
        package,
        True,
    )


def test_stable_frozen_build_is_pinned_to_exact(monkeypatch, tmp_path):
    resources, package = _freeze(monkeypatch, tmp_path)
    monkeypatch.setattr(bootstrap, "DATABASE_RELEASE_CHANNEL", "stable")
    monkeypatch.setattr(bootstrap, "DATABASE_PAIR_POLICY", "exact")

    assert bootstrap._resolve_startup_flavor(
        resource_root=None,
        pair_policy=None,
        app_package_root=None,
    ) == (resources, "exact", package, True)


@pytest.mark.parametrize(
    ("channel", "policy"),
    [
        ("stable", "current_only"),
        ("dev", "exact"),
        ("nightly", "current_only"),
    ],
)
def test_frozen_build_rejects_crossed_or_unknown_flavor_pins(
    monkeypatch,
    tmp_path,
    channel,
    policy,
):
    _freeze(monkeypatch, tmp_path)
    monkeypatch.setattr(bootstrap, "DATABASE_RELEASE_CHANNEL", channel)
    monkeypatch.setattr(bootstrap, "DATABASE_PAIR_POLICY", policy)

    with pytest.raises(bootstrap.DatabaseStartupError) as failure:
        bootstrap._resolve_startup_flavor(
            resource_root=None,
            pair_policy=None,
            app_package_root=None,
        )
    assert failure.value.reason == "frozen_release_flavor_invalid"
    assert failure.value.details == {
        "database_release_channel": channel,
        "database_pair_policy": policy,
    }


@pytest.mark.parametrize(
    "overrides",
    [
        {"resource_root": Path("other-root")},
        {"pair_policy": "exact"},
        {"app_package_root": Path("other-package")},
    ],
)
def test_frozen_startup_rejects_call_site_trust_root_overrides(
    monkeypatch,
    tmp_path,
    overrides,
):
    _freeze(monkeypatch, tmp_path)
    values = {
        "resource_root": None,
        "pair_policy": None,
        "app_package_root": None,
        **overrides,
    }

    with pytest.raises(bootstrap.DatabaseStartupError) as failure:
        bootstrap._resolve_startup_flavor(**values)
    assert failure.value.reason == "frozen_database_startup_override_forbidden"


def test_frozen_startup_rejects_artifacts_from_other_build_channel(monkeypatch):
    monkeypatch.setattr(bootstrap, "DATABASE_RELEASE_CHANNEL", "dev")

    with pytest.raises(bootstrap.DatabaseStartupError) as failure:
        bootstrap._require_frozen_artifact_flavor(
            frozen=True,
            bundled_manifest={"channel": "stable"},
            bundled_pair={"channel": "dev"},
            live_manifest={"channel": "dev"},
            live_pair={"channel": "stable"},
        )
    assert failure.value.reason == "frozen_release_artifact_flavor_mismatch"
    assert failure.value.details == {
        "expected_channel": "dev",
        "mismatched_artifacts": ["bundled_manifest", "live_pair"],
    }


def test_nonfrozen_test_injection_does_not_claim_build_channel_authority():
    bootstrap._require_frozen_artifact_flavor(
        frozen=False,
        bundled_manifest={"channel": "stable"},
        bundled_pair={"channel": "dev"},
        live_manifest=None,
        live_pair=None,
    )
