"""Explicit input/dependency contracts for release discovery and handoff."""
from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True, slots=True)
class ReleaseUpdateSettings:
    data_dir: Path
    database_path: Path
    app_identity: Mapping[str, Any] | None
    host_network_restricted: bool
    network_disabled: bool
    native_development: bool
    shared_read_only: bool


@dataclass(frozen=True, slots=True)
class CurrentReleaseIdentity:
    app_release_id: str
    app_version: str
    database_release_id: str
    release_pair_id: str
    pair_policy: str
    release_channel: str
    manifest_url: str


@dataclass(frozen=True, slots=True)
class ReleaseUpdatePorts:
    read_metadata: Callable[[Path], dict]
    current_release: Callable[[], CurrentReleaseIdentity]
    discover_app: Callable[[], dict | None]
    discover_pair: Callable[..., dict]
    build_coordinator: Callable[[Path], Any]


class ReleaseUpdateFailure(RuntimeError):
    def __init__(self, message: str, reason: str | None = None):
        super().__init__(message)
        self.message, self.reason = message, reason


def read_metadata(path):
    from genshin_db import queries

    return queries.get_meta(path)


def current_release():
    from genshin_app import version

    return CurrentReleaseIdentity(
        version.APP_RELEASE_ID, version.APP_VERSION, version.DATABASE_RELEASE_ID,
        version.RELEASE_PAIR_ID, version.DATABASE_PAIR_POLICY,
        version.DATABASE_RELEASE_CHANNEL, version.DATABASE_RELEASE_MANIFEST_URL,
    )


def discover_app():
    from genshin_app.version import check_update

    return check_update()


def discover_pair(**options):
    from genshin_app.release_pair_discovery import discover_latest_release_pair

    return discover_latest_release_pair(**options)


def build_coordinator(data_dir):
    from genshin_app.release_pair_coordinator import (
        build_default_release_pair_coordinator,
    )

    return build_default_release_pair_coordinator(data_dir)


def default_release_update_ports():
    return ReleaseUpdatePorts(read_metadata, current_release, discover_app, discover_pair, build_coordinator)
