"""Narrow startup-only bridge: inspect, consent, cancel and retry admission.

No paths or database contents are accepted from JavaScript.  No engine, data
service, Agent, credentials or remote discovery is constructed by this port.
"""
from __future__ import annotations

import copy
import logging
import threading
from pathlib import Path
from uuid import uuid4

from genshin_app.bundled_database_activation import (
    activate_bundled_database,
    inspect_bundled_activation,
    write_activation_report,
)
from genshin_app.database_transaction import write_startup_diagnostic


class DatabaseStartupRecovery:
    def __init__(self, data_dir: Path, *, publish, retry):
        self._data_dir, self._publish, self._retry = Path(data_dir), publish, retry
        self._attempt = uuid4().hex
        self._mutex = threading.Lock()
        self._cancel = threading.Event()
        self._busy = False
        self._admitting = False
        self._thread = None
        self._confirmation = None
        self._release_committed = False
        self._status = {"status": "checking", "busy": False}

    def _update(self, status, **details):
        with self._mutex:
            self._status = {"status": status, "busy": self._busy, **details}
        self._notify()

    def _notify(self):
        try:
            self._publish(self.get_status())
        except Exception:  # noqa: BLE001 - GUI delivery must not control DB commit
            logging.getLogger(__name__).debug("启动状态暂未送达界面；可通过状态查询恢复。")

    def _begin_admission(self):
        with self._mutex:
            self._admitting = True

    def _end_admission(self):
        with self._mutex:
            self._admitting = False
        self._notify()

    def _admission_cancelled(self):
        return self._cancel.is_set()

    def _admission_aborted(self):
        if self._release_committed:
            self._record("admission", "ready_not_opened")
            self._update("complete", message="数据库变更已完成；已取消进入应用。", repair_available=True)
        else:
            self._record("admission", "cancelled")
            self._update("cancelled", message="已取消进入应用，可稍后重新检查。", repair_available=True)

    def get_status(self):
        with self._mutex:
            return {**copy.deepcopy(self._status), "busy": self._busy or self._admitting}

    def _record(self, stage, status, *, reason=None, details=None):
        return write_startup_diagnostic(self._data_dir, attempt_id=self._attempt,
            stage=stage, status=status, reason=reason, details=details)

    def _failed(self, error, *, repair_available):
        reason = getattr(error, "reason", "database_startup_failed")
        diagnostic = self._record("admission", "failed", reason=reason)
        version_check = repair_available and reason == "database_live_manifest_not_bundled_release"
        self._update("database_check_required" if version_check else "failed", reason=reason,
            message=("当前数据库与此程序要求的版本不同。请检查配套数据库，查看版本并选择是否安装；确认前不会修改当前资料。"
                     if version_check else str(error)),
            repair_available=repair_available, diagnostic=diagnostic)

    def _launch(self, function):
        with self._mutex:
            if self._busy or self._admitting:
                return {"accepted": False, "reason": "database_startup_operation_busy"}
            self._busy = True
            self._cancel.clear()

        def run():
            try:
                function()
            except Exception as error:  # noqa: BLE001 - worker boundary preserves originating reason
                reason = getattr(error, "reason", "database_startup_recovery_failed")
                diagnostic = self._record("recovery", "failed", reason=reason)
                self._update("cancelled" if reason == "database_activation_cancelled" else "failed",
                    reason=reason, message=str(error), repair_available=True, diagnostic=diagnostic)
            finally:
                with self._mutex:
                    self._busy = False
                    self._status["busy"] = False
                self._notify()

        # Keep the owner alive until it has durably settled the transaction.
        self._thread = threading.Thread(target=run, name="genshin-database-recovery", daemon=False)
        self._thread.start()
        return {"accepted": True}

    def inspect_recovery(self):
        def inspect():
            self._update("checking", message="正在核对程序随附数据库与当前版本。")
            confirmation = inspect_bundled_activation(self._data_dir)
            if self._cancel.is_set():
                self._record("confirmation", "cancelled")
                self._update("cancelled", message="已取消检查，数据库未被修改。", repair_available=True)
                return
            self._confirmation = confirmation
            self._record("confirmation", "awaiting_consent", details=confirmation)
            self._update("awaiting_consent", confirmation=confirmation,
                message="请确认是否安装当前程序随附的数据库。")
        return self._launch(inspect)

    def confirm_recovery(self, confirmation_digest="", user_confirmed=False):
        confirmation = self._confirmation
        if (user_confirmed is not True or confirmation is None
                or confirmation_digest != confirmation["confirmation_digest"]):
            return {"accepted": False, "reason": "database_startup_confirmation_required"}

        def activate():
            self._record("activation", "started", details=confirmation)
            self._update("installing", message="正在安装数据库；提交后将完成校验。")
            try:
                result = activate_bundled_database(self._data_dir, consent_source="user_repair",
                    confirmation_digest=confirmation_digest, cancel_event=self._cancel,
                    progress=lambda value: self._update("installing", **value))
            except Exception as error:
                try:
                    write_activation_report(self._data_dir, status="failed", consent_source="user_repair", error=error)
                except OSError:
                    pass
                raise
            try:
                write_activation_report(self._data_dir, status="activated", consent_source="user_repair", result=result)
            except OSError:
                pass  # A successful committed release is not undone by logging.
            self._release_committed = True
            self._record("activation", "complete", details={"activation_state": "complete"})
            self._update("complete", message="数据库已就绪，正在进入应用。")
            self._retry()
        return self._launch(activate)

    def decline_recovery(self):
        with self._mutex:
            if self._busy:
                self._cancel.set()
                return {"accepted": True, "message": "已请求取消；已提交的操作将先完成校验。"}
            self._confirmation = None
        self._record("confirmation", "declined")
        self._update("declined", message="已保留原数据库；当前程序需要配套数据库，尚未启动。", repair_available=True)
        return {"accepted": True}

    def cancel_recovery(self):
        self._cancel.set()
        return {"accepted": True, "message": "已请求取消；请等待当前安全步骤结束。"}

    def retry_startup(self):
        return self._launch(self._retry)

    def _can_close(self):
        with self._mutex:
            busy = self._busy or self._admitting
            completed = self._status.get("status") == "complete" and not self._admitting
        if completed:
            self._cancel.set()
            return True
        if busy:
            self._cancel.set()
            return False
        return True

    def _join(self):
        thread = self._thread
        if thread is not None and thread is not threading.current_thread():
            thread.join()
