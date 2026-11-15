"""Frozen pre-service release adapter, used only for exact behavior comparison."""
from __future__ import annotations

import sqlite3
from pathlib import Path

from genshin_app.gui.ports.responses import GuiResponses
from genshin_db import queries


class ReferenceReleaseGuiPort(GuiResponses):
    def __init__(self, data_dir, database_path, *, app_identity,
                 host_network_restricted, network_disabled, native_development,
                 shared_read_only, coordinator):
        self.data_dir, self._db_path = Path(data_dir), Path(database_path)
        self._app_identity = app_identity
        self._acceptance_host_network_restricted = host_network_restricted
        self._development_network_disabled = network_disabled
        self._native_agent_development_mode = native_development
        self._shared_database_read_only = shared_read_only
        self._update_cache = self._database_update_discovery = None
        self._release_pair_coordinator_error = None
        if coordinator is None:
            try:
                from genshin_app.release_pair_coordinator import (
                    build_default_release_pair_coordinator,
                )
                coordinator = build_default_release_pair_coordinator(self.data_dir)
            except Exception as error:  # noqa: BLE001 - unavailable updater is reported by discovery
                self._release_pair_coordinator_error = getattr(error, "reason", type(error).__name__)
        self._release_pair_coordinator = coordinator

    def check_update(self) -> dict:
        """G5 自更新检测：有新版返回 {version,url,notes}，否则空 dict（进程内缓存一次）。"""
        if self._acceptance_host_network_restricted:
            reason = (
                "development_acceptance_network_disabled"
                if self._development_network_disabled
                else (
                    "native_agent_development_host_network_restricted"
                    if self._native_agent_development_mode
                    else "provider_acceptance_host_network_restricted"
                )
            )
            return self._ok({
                "disabled": True,
                "reason": reason,
            })
        if self._update_cache is None:
            from genshin_app.version import check_update
            self._update_cache = check_update() or {}
        return self._ok(self._update_cache)

    def check_database_update(self) -> dict:
        """Discover one reviewed project App/database release pair.

        Raw game-data commits are deliberately not update candidates.  The
        discovery layer accepts only a fully bound project PairManifest and
        DatabaseReleaseManifest, preserves its ETag cache, and exposes an
        update only after its trust verifier has accepted the document.
        """
        from genshin_app.version import (
            APP_RELEASE_ID,
            APP_VERSION,
            DATABASE_PAIR_POLICY,
            DATABASE_RELEASE_CHANNEL,
            DATABASE_RELEASE_ID,
            DATABASE_RELEASE_MANIFEST_URL,
            RELEASE_PAIR_ID,
        )

        actual_game_version = None
        actual_upstream_commit = None
        try:
            actual_meta = queries.get_meta(self._db_path) if self._db_path.exists() else {}
            actual_game_version = actual_meta.get("game_version:release") or actual_meta.get(
                "game_version"
            )
            actual_upstream_commit = actual_meta.get(
                "upstream_commit:release"
            ) or actual_meta.get("upstream_commit")
        except (sqlite3.Error, OSError, ValueError):
            # Discovery remains useful as a repair/status surface when the
            # local database cannot be opened.  The missing facts stay null;
            # they are never substituted from the remote candidate.
            pass
        current = {
            "current_app_version": APP_VERSION,
            "current_app_release_id": APP_RELEASE_ID,
            "current_release_pair_id": RELEASE_PAIR_ID,
            "current_database_release_id": DATABASE_RELEASE_ID,
            "actual_database_game_version": actual_game_version,
            "actual_database_upstream_commit": actual_upstream_commit,
            "pair_policy": DATABASE_PAIR_POLICY,
            "release_channel": DATABASE_RELEASE_CHANNEL,
            "pair_status": (self._app_identity or {}).get(
                "release_plan_state", "not_checked"
            ),
        }

        if self._acceptance_host_network_restricted:
            provider_mode = not self._development_network_disabled
            return self._ok({
                "available": False,
                "initialized": self._db_path.exists(),
                "disabled": True,
                "coordinator_available": False,
                "update_action_available": False,
                "requires_restart": True,
                **current,
                "reason": (
                    "native_agent_development_host_network_restricted"
                    if self._native_agent_development_mode
                    else (
                        "provider_acceptance_host_network_restricted"
                        if provider_mode
                        else "development_acceptance_network_disabled"
                    )
                ),
                "message": (
                    (
                        "宿主App与数据库联网已禁用；原生Agent仍可使用受审的"
                        "DeepSeek Search和公开地址Fetch"
                    )
                    if self._native_agent_development_mode
                    else (
                        "真实DeepSeek联调仅允许Agent访问受审provider端点；"
                        "数据库远端检查已禁用"
                    )
                    if provider_mode
                    else "本地开发验收模式已禁用外网数据检查"
                ),
            })
        if not self._db_path.exists():
            return self._ok({
                "available": False,
                "initialized": False,
                "disabled": True,
                "coordinator_available": False,
                "update_action_available": False,
                "requires_restart": True,
                **current,
                "reason": "database_profile_missing",
                "message": "数据库尚未初始化；请安装与当前应用匹配的完整版本包",
            })

        from genshin_app.release_pair_discovery import (
            ReleasePairDiscoveryError,
            discover_latest_release_pair,
        )
        coordinator = self._release_pair_coordinator
        coordinator_state = (
            coordinator.availability()
            if coordinator is not None
            else {
                "available": False,
                "reason": self._release_pair_coordinator_error
                or "release_pair_update_coordinator_unavailable",
            }
        )
        try:
            discovered = (
                coordinator.discover()
                if coordinator is not None
                else discover_latest_release_pair(
                    url=DATABASE_RELEASE_MANIFEST_URL or None,
                    cache_path=self.data_dir / "cache" / "release-pair-latest.json",
                    channel=DATABASE_RELEASE_CHANNEL,
                )
            )
            remote_pair = discovered.get("release_pair_id")
            remote_database = discovered.get("database_release_id")
            remote_pair_manifest = discovered.get("pair_manifest")
            remote_database_manifest = discovered.get("database_manifest")
            remote_app = (
                remote_pair_manifest.get("app", {}).get("app_release_id")
                if isinstance(remote_pair_manifest, dict)
                else None
            )
            remote_game_version = (
                remote_database_manifest.get("database", {}).get("game_version")
                if isinstance(remote_database_manifest, dict)
                else None
            )
            authoritative = discovered.get("authoritative") is True
            available = (
                authoritative
                and discovered.get("can_offer_update") is True
                and (
                    remote_pair != RELEASE_PAIR_ID
                    or remote_database != DATABASE_RELEASE_ID
                )
            )
            status = discovered.get("status")
            coordinator_available = coordinator_state.get("available") is True
            update_action_available = available and coordinator_available
            if status == "disabled":
                message = "尚未配置应用与数据库联合更新源"
            elif not authoritative:
                message = "未发现可验证的应用与数据库联合更新"
            elif available:
                message = (
                    "发现已验证的应用与数据库联合更新；确认后将下载完整版本并重启"
                    if coordinator_available
                    else "发现已验证的应用与数据库联合更新，但外置安装协调器不可用"
                )
            else:
                message = "当前应用与数据库版本对已是最新"
            artifacts = discovered.get("artifacts")
            artifact_summary = None
            if isinstance(artifacts, dict):
                artifact_summary = {
                    kind: {
                        "size_bytes": value.get("size_bytes"),
                        "format": value.get("format"),
                    }
                    for kind, value in artifacts.items()
                    if isinstance(value, dict)
                }
            self._database_update_discovery = (
                discovered if authoritative else None
            )
            return self._ok({
                "available": available,
                "initialized": True,
                "disabled": status == "disabled",
                "coordinator_available": coordinator_available,
                "update_action_available": update_action_available,
                "requires_restart": True,
                "status": status,
                "reason": discovered.get("reason"),
                "coordinator_reason": coordinator_state.get("reason"),
                "message": message,
                "authoritative": authoritative,
                "source": discovered.get("source"),
                "checked_at": discovered.get("checked_at"),
                "confirmation_digest": discovered.get("confirmation_digest"),
                "artifacts": artifact_summary,
                **current,
                "remote_app_release_id": remote_app,
                "remote_release_pair_id": remote_pair,
                "remote_database_release_id": remote_database,
                "remote_game_version": remote_game_version,
            })
        except (ReleasePairDiscoveryError, OSError, ValueError) as e:
            self._database_update_discovery = None
            return self._err(f"检查数据更新失败: {e}")

    def update_database(
        self,
        confirmation_digest: str = "",
        user_confirmed: bool = False,
    ) -> dict:
        """Hand one exact, user-confirmed App/database pair to the updater."""
        if self._acceptance_host_network_restricted or self._shared_database_read_only:
            provider_mode = (
                (self._app_identity or {}).get("mode") in {
                    "provider_acceptance",
                    "native_agent_development",
                }
            )
            return {
                "code": 1,
                "message": (
                    "真实DeepSeek开发联调以只读方式使用共享数据库"
                    if provider_mode
                    else "本地开发验收模式禁止联网更新共享数据库"
                ),
                "reason": (
                    "native_agent_development_database_read_only"
                    if self._native_agent_development_mode
                    else (
                        "provider_acceptance_database_read_only"
                        if provider_mode
                        else "development_acceptance_database_read_only"
                    )
                ),
            }
        coordinator = self._release_pair_coordinator
        discovery = self._database_update_discovery
        if coordinator is None or coordinator.availability().get("available") is not True:
            return {
                "code": 1,
                "message": (
                    "数据库不能脱离应用单独更新。当前尚未配置已验证的应用与数据库"
                    "联合更新包；在用数据库未被修改。"
                ),
                "reason": "database_pair_update_coordinator_required",
            }
        if discovery is None:
            return {
                "code": 1,
                "message": "请先重新检查版本，再确认本次应用与数据库联合更新。",
                "reason": "release_pair_update_discovery_required",
            }
        if user_confirmed is not True:
            return {
                "code": 1,
                "message": "用户尚未确认联合更新。",
                "reason": "release_pair_update_confirmation_required",
            }
        if (
            not confirmation_digest
            or confirmation_digest != discovery.get("confirmation_digest")
        ):
            return {
                "code": 1,
                "message": "远端版本说明已经变化，请重新检查并确认。",
                "reason": "release_pair_update_confirmation_mismatch",
            }
        try:
            result = coordinator.prepare_and_launch(
                discovery,
                confirmed_document_sha256=confirmation_digest,
                user_confirmed=user_confirmed,
            )
            return self._ok(result)
        except Exception as error:  # noqa: BLE001 - stable updater envelope
            reason = getattr(error, "reason", "release_pair_update_failed")
            messages = {
                "release_pair_update_confirmation_required": "用户尚未确认联合更新。",
                "release_pair_update_confirmation_mismatch": (
                    "远端版本说明已经变化，请重新检查并确认。"
                ),
                "release_pair_artifact_download_cancelled": "联合更新下载已取消。",
                "release_pair_update_recovery_required": (
                    "上次联合更新尚待恢复或清理，请先查看更新诊断。"
                ),
                "release_pair_updater_launch_failed": (
                    "外置更新器未能启动，当前应用和数据库没有被替换。"
                ),
            }
            return {
                "code": 1,
                "message": messages.get(
                    reason,
                    "联合更新准备失败，当前应用和数据库没有被替换。",
                ),
                "reason": reason,
            }

    def cancel_database_update(self) -> dict:
        coordinator = self._release_pair_coordinator
        if coordinator is None:
            return {
                "code": 1,
                "message": "当前没有正在运行的联合更新下载。",
                "reason": "release_pair_update_coordinator_required",
            }
        try:
            return self._ok(coordinator.cancel())
        except Exception as error:  # noqa: BLE001 - stable updater envelope
            return {
                "code": 1,
                "message": "无法取消当前联合更新阶段。",
                "reason": getattr(error, "reason", "release_pair_update_cancel_failed"),
            }

