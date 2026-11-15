"""Exact application-service/legacy adapter comparison; no remote downloads."""
from __future__ import annotations

import copy
import json
from types import SimpleNamespace

import pytest

from genshin_app import release_pair_coordinator, release_pair_discovery, version
from genshin_app.gui.ports.release import ReleaseGuiPort
from genshin_app.release_update_contracts import (
    CurrentReleaseIdentity,
    ReleaseUpdatePorts,
    ReleaseUpdateSettings,
)
from genshin_app.release_update_service import ReleaseUpdateService
from genshin_db import queries
from tests.reference_release_gui_port_v1 import ReferenceReleaseGuiPort


def remote(**changes):
    return {
        "authoritative": True, "can_offer_update": True, "status": "available",
        "release_pair_id": "pair-next", "database_release_id": "db-next",
        "pair_manifest": {"app": {"app_release_id": "app-next"}},
        "database_manifest": {"database": {"game_version": "7.2"}},
        "confirmation_digest": "d" * 64, "source": "remote", "checked_at": "fixed",
        "artifacts": {"app": {"size_bytes": 1, "format": "zip", "url": "not projected"},
                      "database": {"size_bytes": 2, "format": "sqlite"}, "ignored": []},
        **changes,
    }


def run_adapter(adapter, tmp_path, monkeypatch, case):
    events = []
    discovery = case.get("remote", remote())

    def metadata(path):
        events.append(("metadata", str(path)))
        if case.get("metadata_error"):
            raise OSError("local metadata unavailable")
        return {"game_version:release": "7.1", "upstream_commit:release": "c" * 40}

    def discover(**kwargs):
        events.append(("discover", kwargs))
        if case.get("discovery_error"):
            raise release_pair_discovery.ReleasePairDiscoveryError("release_pair_discovery_url_invalid")
        return copy.deepcopy(discovery)

    def availability():
        events.append(("availability",))
        if case.get("availability_error"):
            raise ValueError("availability failed")
        return {"available": case.get("available", True), "reason": case.get("coordinator_reason")}

    def prepare(discovered, **kwargs):
        events.append(("prepare", copy.deepcopy(discovered), kwargs))
        if case.get("update_error"):
            error = RuntimeError("not projected")
            error.reason = case["update_error"]
            raise error
        return {"action": "handoff_launched", "requires_restart": True}

    def cancel():
        events.append(("cancel",))
        if case.get("cancel_error"):
            raise RuntimeError("not projected")
        return {"cancel_requested": True}

    def app_update():
        events.append(("app_update",))
        return {"version": "next", "url": "https://updates.example/app"}

    def build(_data):
        events.append(("build_coordinator",))
        if case.get("build_error"):
            raise ValueError("not projected")

    monkeypatch.setattr(queries, "get_meta", metadata)
    monkeypatch.setattr(release_pair_discovery, "discover_latest_release_pair", discover)
    monkeypatch.setattr(release_pair_coordinator, "build_default_release_pair_coordinator", build)
    monkeypatch.setattr(version, "check_update", app_update)
    coordinator = (None if case.get("no_coordinator") else SimpleNamespace(
        availability=availability, discover=discover, prepare_and_launch=prepare, cancel=cancel))
    port = adapter(tmp_path, tmp_path / "genshin.db", coordinator=coordinator,
                   app_identity=case.get("identity"), host_network_restricted=case.get("restricted", False),
                   network_disabled=case.get("disabled", False), native_development=case.get("native", False),
                   shared_read_only=case.get("read_only", False))

    def invoke(method, *args):
        try:
            return getattr(port, method)(*args)
        except Exception as error:  # noqa: BLE001 - compare first exception without changing its scope
            return {"exception_type": type(error).__name__, "exception_text": str(error)}

    results = [invoke("check_update"), invoke("check_update"),
               invoke("update_database", "d" * 64, True), invoke("check_database_update"),
               invoke("update_database", "d" * 64, False), invoke("update_database", "wrong", True),
               invoke("update_database", "d" * 64, True), invoke("cancel_database_update")]
    # JSON string equality also proves existing envelope/member insertion order.
    return json.dumps(results, ensure_ascii=False), events


@pytest.mark.parametrize("case", [
    {}, {"no_coordinator": True}, {"no_coordinator": True, "build_error": True},
    {"available": False, "coordinator_reason": "not-ready"}, {"metadata_error": True},
    {"discovery_error": True}, {"availability_error": True}, {"cancel_error": True},
    {"restricted": True, "disabled": True},
    {"restricted": True, "identity": {"mode": "provider_acceptance"}},
    {"restricted": True, "native": True, "identity": {"mode": "native_agent_development"}},
    {"read_only": True},
    {"remote": remote(authoritative=False)}, {"remote": remote(status="disabled", authoritative=False)},
    {"remote": remote(release_pair_id=version.RELEASE_PAIR_ID, database_release_id=version.DATABASE_RELEASE_ID)},
    *({"update_error": code} for code in (
        "release_pair_update_confirmation_required", "release_pair_update_confirmation_mismatch",
        "release_pair_artifact_download_cancelled", "release_pair_update_recovery_required",
        "release_pair_updater_launch_failed", "unknown-update-failure")),
])
def test_complete_envelopes_first_error_and_collaborator_order_match_reference(tmp_path, monkeypatch, case):
    (tmp_path / "genshin.db").write_bytes(b"read-only sentinel")
    before = (tmp_path / "genshin.db").read_bytes()
    expected = run_adapter(ReferenceReleaseGuiPort, tmp_path, monkeypatch, case)
    actual = run_adapter(ReleaseGuiPort, tmp_path, monkeypatch, case)
    assert actual == expected
    assert (tmp_path / "genshin.db").read_bytes() == before


def test_missing_database_discovery_matches_reference_without_remote_work(tmp_path, monkeypatch):
    assert run_adapter(ReleaseGuiPort, tmp_path, monkeypatch, {}) == run_adapter(
        ReferenceReleaseGuiPort, tmp_path, monkeypatch, {})


def test_application_service_has_narrow_injected_ports_and_owns_cached_discovery(tmp_path):
    calls = []
    settings = ReleaseUpdateSettings(tmp_path, tmp_path / "db", None, False, False, False, False)
    ports = ReleaseUpdatePorts(
        read_metadata=lambda _path: {},
        current_release=lambda: CurrentReleaseIdentity("app", "v", "db", "pair", "policy", "dev", ""),
        discover_app=lambda: calls.append("app") or {"version": "next"},
        discover_pair=lambda **_args: pytest.fail("missing database must not query remote"),
        build_coordinator=lambda _root: None,
    )
    service = ReleaseUpdateService(settings, ports=ports)
    assert service.check_update() == service.check_update() == {"version": "next"}
    assert calls == ["app"]
    assert service.check_database_update()["reason"] == "database_profile_missing"
