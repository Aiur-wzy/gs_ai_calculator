"""Thin GUI envelopes over the release application service."""
from __future__ import annotations

from pathlib import Path

from genshin_app.release_update_contracts import (
    ReleaseUpdateFailure,
    ReleaseUpdateSettings,
)
from genshin_app.release_update_service import ReleaseUpdateService

from .responses import GuiResponses


class ReleaseGuiPort(GuiResponses):
    def __init__(self, data_dir, database_path, *, app_identity,
                 host_network_restricted, network_disabled, native_development,
                 shared_read_only, coordinator, database_setup=None):
        self._has_database_setup = database_setup is not None
        self._service = ReleaseUpdateService(ReleaseUpdateSettings(
            Path(data_dir), Path(database_path), app_identity,
            host_network_restricted, network_disabled, native_development,
            shared_read_only,
        ), coordinator=coordinator, database_setup=database_setup)

    @staticmethod
    def _failure(error: ReleaseUpdateFailure):
        result = {"code": 1, "message": error.message}
        if error.reason is not None:
            result["reason"] = error.reason
        return result

    def check_update(self) -> dict:
        try:
            return self._ok(self._service.check_update())
        except ReleaseUpdateFailure as error:
            return self._failure(error)

    def check_database_update(self) -> dict:
        try:
            return self._ok(self._service.check_database_update())
        except ReleaseUpdateFailure as error:
            return self._failure(error)

    def update_database(self, confirmation_digest: str = "", user_confirmed: bool = False) -> dict:
        try:
            return self._ok(self._service.update_database(confirmation_digest, user_confirmed))
        except ReleaseUpdateFailure as error:
            return self._failure(error)

    def cancel_database_update(self) -> dict:
        try:
            return self._ok(self._service.cancel_database_update())
        except ReleaseUpdateFailure as error:
            return self._failure(error)

    def database_update_progress(self) -> dict:
        return self._ok(self._service.download_progress())

    def choose_local_database_release(self, window) -> dict:
        if window is None or not self._has_database_setup:
            return self._err("当前不能从本地安装数据库。")
        import webview

        package = window.create_file_dialog(webview.FileDialog.OPEN, allow_multiple=False,
                                            file_types=("数据库发行包 (*.db;*.gz)",))
        if not package:
            return self._ok({"cancelled": True})
        signed = window.create_file_dialog(webview.FileDialog.OPEN, allow_multiple=False,
                                           file_types=("签名发行清单 (*.json)",))
        if not signed:
            return self._ok({"cancelled": True})
        return self.preview_local_database(Path(package[0]), Path(signed[0]))

    def preview_local_database(self, database, signed_document) -> dict:
        try:
            return self._ok(self._service.preview_local_database(database, signed_document))
        except ReleaseUpdateFailure as error:
            return self._failure(error)
