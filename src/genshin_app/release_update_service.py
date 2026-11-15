"""Release application service; the existing PairCoordinator owns installation."""
from __future__ import annotations

import sqlite3
from copy import deepcopy
from dataclasses import replace
from threading import Lock

from .release_pair_discovery import ReleasePairDiscoveryError
from .release_update_contracts import (
    ReleaseUpdateFailure,
    ReleaseUpdateSettings,
    default_release_update_ports,
)
from .release_update_projection import (
    current_release_status,
    discovered_database_status,
    host_restriction_reason,
    read_only_update_failure,
    unavailable_database_status,
    update_failure_message,
)


class ReleaseUpdateService:
    def __init__(self, settings: ReleaseUpdateSettings, *, coordinator=None, ports=None, database_setup=None):
        self.settings, self.ports = settings, ports or default_release_update_ports()
        self._database_setup = database_setup
        self._checks = Lock()
        self._local_selection = None
        self._last_check = None
        self._app_update_cache = self._discovery = None
        self._coordinator_error = None
        if coordinator is None:
            try:
                coordinator = self.ports.build_coordinator(settings.data_dir)
            except Exception as error:  # noqa: BLE001 - status remains available without an installer
                self._coordinator_error = getattr(error, "reason", type(error).__name__)
        self._coordinator = coordinator

    def check_update(self):
        if self.settings.host_network_restricted:
            return {"disabled": True, "reason": host_restriction_reason(self.settings)}
        if self.ports.current_release().release_channel == "stable":
            return self.check_database_update()
        if self._app_update_cache is None:
            self._app_update_cache = self.ports.discover_app() or {}
        return self._app_update_cache

    def _current_database_status(self, identity):
        meta = {}
        try:
            if self.settings.database_path.exists():
                meta = self.ports.read_metadata(self.settings.database_path)
        except (sqlite3.Error, OSError, ValueError):
            pass  # An unreadable local database never borrows remote facts.
        return current_release_status(identity, self.settings, meta)

    def check_database_update(self):
        # Concurrent startup and menu checks can observe one shared discovery.
        previous = self._last_check
        with self._checks:
            if self._last_check is not previous and self._last_check is not None:
                return deepcopy(self._last_check)
            result = self._check_database_update()
            self._last_check = deepcopy(result)
            return result

    def _check_database_update(self):
        identity = self.ports.current_release()
        if self._database_setup is not None and not self.settings.host_network_restricted:
            current = self._current_database_status(identity)
            current.update(current_database_release_id=None, current_release_pair_id=None,
                           initialized=False, status="database_not_installed", requires_restart=True,
                           local_database_setup_available=True)
            try:
                option = self._database_setup.preview()
            except (RuntimeError, OSError, ValueError) as error:
                return {**current, "available": False, "update_action_available": False,
                        "reason": getattr(error, "reason", "database_download_unavailable"),
                        "message": "数据库尚未安装。暂时无法取得可验证的数据库下载选项。"}
            return {**current, "available": True, "authoritative": True, "coordinator_available": True,
                    "update_action_available": True, "update_options": [option],
                    "message": "应用已安装。安装数据库后即可查询资料和计算。"}
        if identity.release_channel == "stable":
            from genshin_db.release_profile import (
                load_database_release_manifest,
                load_release_pair_manifest,
            )

            from .release_pair_update import (
                LIVE_DATABASE_MANIFEST_FILENAME,
                LIVE_PAIR_MANIFEST_FILENAME,
                LIVE_PAIR_PROOF_FILENAME,
                ReleasePairUpdateError,
                read_live_product_proof,
            )

            root = self.settings.data_dir
            if (root / LIVE_PAIR_PROOF_FILENAME).exists():
                try:
                    pair = load_release_pair_manifest(root / LIVE_PAIR_MANIFEST_FILENAME)
                    database = load_database_release_manifest(root / LIVE_DATABASE_MANIFEST_FILENAME)
                    if (read_live_product_proof(root, pair, database) is None
                            or pair["app"]["app_release_id"] != identity.app_release_id):
                        raise ReleasePairUpdateError("product_release_live_pair_proof_invalid")
                except (OSError, ValueError, ReleasePairUpdateError) as error:
                    raise ReleaseUpdateFailure("无法核实当前安装版本。请查看启动诊断。",
                                               getattr(error, "reason", None)) from error
                identity = replace(identity, release_pair_id=pair["release_pair_id"],
                                   database_release_id=database["database_release_id"])
        current = self._current_database_status(identity)
        exists = self.settings.database_path.exists()
        if self.settings.host_network_restricted or not exists:
            return unavailable_database_status(current, self.settings, initialized=exists)
        coordinator = self._coordinator
        state = (coordinator.availability() if coordinator is not None else {
            "available": False, "reason": self._coordinator_error or "release_pair_update_coordinator_unavailable",
        })
        try:
            discovered = coordinator.discover() if coordinator is not None else self.ports.discover_pair(
                url=identity.manifest_url or None,
                cache_path=self.settings.data_dir / "cache/release-pair-latest.json",
                channel=identity.release_channel,
            )
            result = discovered_database_status(discovered, state, identity, current)
            self._discovery = discovered if discovered.get("authoritative") is True else None
            return result
        except (ReleasePairDiscoveryError, OSError, ValueError) as error:
            self._discovery = None
            raise ReleaseUpdateFailure(f"检查数据更新失败: {error}") from None

    def update_database(self, confirmation_digest="", user_confirmed=False):
        if self.settings.host_network_restricted or self.settings.shared_read_only:
            raise ReleaseUpdateFailure(*read_only_update_failure(self.settings))
        if self._database_setup is not None:
            try:
                local = self._local_selection
                options = ({"local_database": local[1], "local_document": local[2]}
                           if local and local[0] == confirmation_digest else {})
                return self._database_setup.install(confirmation_digest, user_confirmed=user_confirmed, **options)
            except (OSError, RuntimeError, ValueError) as error:
                raise ReleaseUpdateFailure("数据库安装未完成。请重新检查可用版本或继续恢复。",
                                           getattr(error, "reason", "database_setup_failed")) from error
        coordinator, discovery = self._coordinator, self._discovery
        if coordinator is None or coordinator.availability().get("available") is not True:
            raise ReleaseUpdateFailure(
                "数据库不能脱离应用单独更新。当前尚未配置已验证的应用与数据库联合更新包；在用数据库未被修改。",
                "database_pair_update_coordinator_required")
        if discovery is None:
            raise ReleaseUpdateFailure("请先重新检查版本，再确认本次应用与数据库联合更新。",
                                       "release_pair_update_discovery_required")
        if user_confirmed is not True:
            raise ReleaseUpdateFailure("用户尚未确认联合更新。", "release_pair_update_confirmation_required")
        options = discovery.get("update_options")
        authorized = (any(item["confirmation_digest"] == confirmation_digest for item in options)
                      if isinstance(options, list) else confirmation_digest == discovery.get("confirmation_digest"))
        if not confirmation_digest or not authorized:
            raise ReleaseUpdateFailure("远端版本说明已经变化，请重新检查并确认。",
                                       "release_pair_update_confirmation_mismatch")
        try:
            return coordinator.prepare_and_launch(discovery, confirmed_document_sha256=confirmation_digest,
                                                  user_confirmed=user_confirmed)
        except Exception as error:  # noqa: BLE001 - stable updater boundary
            reason = getattr(error, "reason", "release_pair_update_failed")
            raise ReleaseUpdateFailure(update_failure_message(reason), reason) from None

    def preview_local_database(self, database, signed_document):
        if self.settings.host_network_restricted or self.settings.shared_read_only or self._database_setup is None:
            raise ReleaseUpdateFailure("当前不能从本地安装数据库。", "database_setup_unavailable")
        try:
            option = self._database_setup.preview(local_document=signed_document)
        except (OSError, RuntimeError, ValueError) as error:
            raise ReleaseUpdateFailure("无法验证本地版本清单。请选择与当前应用兼容的签名清单。",
                                       getattr(error, "reason", "database_setup_manifest_invalid")) from error
        self._local_selection = (option["confirmation_digest"], database, signed_document)
        return option

    def cancel_database_update(self):
        if self._database_setup is not None:
            return self._database_setup.cancel()
        if self._coordinator is None:
            raise ReleaseUpdateFailure("当前没有正在运行的联合更新下载。", "release_pair_update_coordinator_required")
        try:
            return self._coordinator.cancel()
        except Exception as error:  # noqa: BLE001 - stable updater boundary
            raise ReleaseUpdateFailure("无法取消当前联合更新阶段。",
                                       getattr(error, "reason", "release_pair_update_cancel_failed")) from None

    def download_progress(self):
        if self._database_setup is not None:
            return self._database_setup.progress()
        progress = getattr(self._coordinator, "download_progress", None)
        return progress() if progress is not None else None
