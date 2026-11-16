"""Current signed-product discovery and recovery contracts; no downloads."""
from __future__ import annotations

import copy
from types import SimpleNamespace

import pytest

from genshin_app import release_pair_coordinator, release_pair_discovery, version
from genshin_app.gui.ports.release import ReleaseGuiPort
from genshin_app.release_update_contracts import (
    CurrentReleaseIdentity,
    ReleaseUpdateFailure,
    ReleaseUpdatePorts,
    ReleaseUpdateSettings,
)
from genshin_app.release_update_service import ReleaseUpdateService
from genshin_db import queries
from tests.release_update_fixtures import reviewed_discovery


def remote(**changes):
    return reviewed_discovery(**changes)


def run_adapter(adapter, tmp_path, monkeypatch, case):
    monkeypatch.setattr(version, "DATABASE_RELEASE_CHANNEL", "stable")
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
    return results, events


@pytest.mark.parametrize("case", [
    {}, {"no_coordinator": True}, {"no_coordinator": True, "build_error": True},
    {"available": False, "coordinator_reason": "not-ready"}, {"metadata_error": True},
    {"discovery_error": True}, {"availability_error": True}, {"cancel_error": True},
    {"restricted": True, "disabled": True},
    {"restricted": True, "identity": {"mode": "provider_acceptance"}},
    {"restricted": True, "native": True, "identity": {"mode": "native_agent_development"}},
    {"read_only": True},
    {"remote": remote(authoritative=False)}, {"remote": remote(status="disabled", authoritative=False)},
    {"remote": remote(release_pair_id=version.RELEASE_PAIR_ID, database_release_id=version.DATABASE_RELEASE_ID,
                      update_options=[], can_offer_update=False)},
    {"remote": remote(update_options=[])},
    *({"update_error": code} for code in (
        "release_pair_update_confirmation_required", "release_pair_update_confirmation_mismatch",
        "release_pair_artifact_download_cancelled", "release_pair_update_recovery_required",
        "release_pair_updater_launch_failed", "unknown-update-failure")),
])
def test_stable_update_reports_recoverable_outcomes_and_never_mutates_database(tmp_path, monkeypatch, case):
    (tmp_path / "genshin.db").write_bytes(b"read-only sentinel")
    before = (tmp_path / "genshin.db").read_bytes()
    rows, events = run_adapter(ReleaseGuiPort, tmp_path, monkeypatch, case)
    assert all("exception_type" not in row for row in rows)
    assert not any(event[0] == "app_update" for event in events)
    assert rows[0] == rows[1]
    prepares = [event for event in events if event[0] == "prepare"]
    if case.get("restricted"):
        assert rows[0]["data"]["disabled"] is True
        assert rows[3]["data"]["update_action_available"] is False
        assert not any(event[0] == "discover" for event in events)
    elif case.get("discovery_error") or case.get("availability_error"):
        reason = ("release_pair_discovery_url_invalid" if case.get("discovery_error")
                  else "release_pair_update_check_failed")
        assert rows[0]["code"] == rows[3]["code"] == 1
        assert rows[0]["reason"] == reason
        assert rows[6]["reason"] == ("release_pair_update_discovery_required"
                                       if case.get("discovery_error") else reason)
    else:
        status = rows[0]["data"]
        assert status["confirmation_digest"] is None  # choose an exact signed combination
        assert "url" not in status["artifacts"]["app"]
        if case.get("metadata_error"):
            assert status["actual_database_game_version"] is None
        else:
            assert status["actual_database_game_version"] == "7.1"
        if case.get("no_coordinator") or case.get("available") is False:
            assert status["update_action_available"] is False
            assert rows[6]["reason"] == "database_pair_update_coordinator_required"
        elif case.get("read_only"):
            assert rows[6]["reason"] == "development_acceptance_database_read_only"
        elif not status["available"]:
            assert status["update_action_available"] is False
            assert rows[6]["reason"] == "release_pair_update_discovery_required"
        else:
            assert len(prepares) == 2  # after each explicit successful check
            assert rows[4]["reason"] == "release_pair_update_confirmation_required"
            assert rows[5]["reason"] == "release_pair_update_confirmation_mismatch"
            if case.get("update_error"):
                assert rows[2]["code"] == rows[6]["code"] == 1
                assert rows[6]["reason"] == case["update_error"]
            else:
                assert rows[2]["data"]["action"] == rows[6]["data"]["action"] == "handoff_launched"
            assert all(event[2] == {"confirmed_document_sha256": "d" * 64, "user_confirmed": True}
                       for event in prepares)
    if case.get("restricted") or case.get("read_only") or case.get("no_coordinator") or case.get("available") is False:
        assert prepares == []
    if case.get("cancel_error"):
        assert rows[7]["reason"] == "release_pair_update_cancel_failed"
    assert (tmp_path / "genshin.db").read_bytes() == before


def test_missing_database_discovery_never_queries_remote(tmp_path, monkeypatch):
    rows, events = run_adapter(ReleaseGuiPort, tmp_path, monkeypatch, {})
    assert rows[0]["data"]["reason"] == rows[3]["data"]["reason"] == "database_profile_missing"
    assert not any(event[0] in {"discover", "app_update", "prepare"} for event in events)


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


@pytest.mark.parametrize("failure", ["availability", "discovery", "database_removed", "no_options", "untrusted"])
def test_failed_or_inapplicable_refresh_retires_previous_confirmation(tmp_path, failure):
    database = tmp_path / "db"
    database.write_bytes(b"unchanged")
    state = {"failure": None}
    handed_off = []

    def availability():
        if state["failure"] == "availability":
            raise OSError("coordinator inaccessible")
        return {"available": True}

    def discover():
        if state["failure"] == "discovery":
            raise RuntimeError("service inaccessible")
        return reviewed_discovery(**({"update_options": []} if state["failure"] == "no_options" else
                                     {"authoritative": False} if state["failure"] == "untrusted" else {}))

    coordinator = SimpleNamespace(availability=availability, discover=discover,
                                  prepare_and_launch=lambda *args, **kw: handed_off.append((args, kw)))
    ports = ReleaseUpdatePorts(lambda _p: {}, lambda: CurrentReleaseIdentity(
        "app", "v", "db", "pair", "policy", "stable", ""),
        lambda: pytest.fail("stable must use signed combinations"),
        lambda **_kw: pytest.fail("coordinator owns discovery"), lambda _p: coordinator)
    service = ReleaseUpdateService(ReleaseUpdateSettings(tmp_path, database, None, False, False, False, False),
                                   coordinator=coordinator, ports=ports)
    assert service.check_update()["update_action_available"] is True
    state["failure"] = failure
    if failure == "database_removed":
        database.unlink()
    if failure in {"availability", "discovery"}:
        with pytest.raises(ReleaseUpdateFailure, match="无法完成更新检查"):
            service.check_update()
    else:
        assert service.check_update()["update_action_available"] is False
    state["failure"] = None  # even recovery does not reauthorize the old offer
    with pytest.raises(ReleaseUpdateFailure) as rejected:
        service.update_database("d" * 64, True)
    assert rejected.value.reason == "release_pair_update_discovery_required"
    assert handed_off == []
