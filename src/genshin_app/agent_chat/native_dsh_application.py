"""App process/credential owner around DSH's public client carrier.

The caller supplies a verified runtime process. This class never creates a
Session, resumes a Turn, starts a provider request or interprets model output.
"""
from __future__ import annotations

import threading

from .credential_generation import CredentialGenerationController
from .native_dsh_mux_client import NativeDshMuxError
from .native_dsh_shell_host import NativeDshShellHost


class NativeDshApplication(NativeDshShellHost):
    def __init__(self, process, *, credentials: CredentialGenerationController):
        self._process_owner = process
        self._credentials = credentials
        self._lifecycle_lock = threading.RLock()
        self._disposed = False
        self._shutdown_diagnostic = None
        self._retire_cli_link = None
        super().__init__(process.client, boot=process.client.boot, bundle=process.client.bundle)
        try:
            self.synchronize_credentials()
        except Exception:
            try:
                self.close()
            except Exception as cleanup_error:  # noqa: BLE001 - preserve the original startup failure
                self._shutdown_diagnostic = {"state": "startup_cleanup_failed",
                    "code": getattr(cleanup_error, "code", "dsh_startup_cleanup_failed")}
            raise

    def synchronize_credentials(self):
        """Read Windows only at the private handoff, never through the client."""
        secret = None
        payload = None
        try:
            with self._lifecycle_lock:
                if self._disposed:
                    raise NativeDshMuxError("dsh_shell_closed")
                status = self._credentials.status()
                if not status["available"]:
                    return {"state": "credential_store_unavailable"}
                secret, generation = self._credentials.resolve_for_provider()
                payload = {"provider": "deepseek-official", "credential_generation": generation}
                if secret is not None:
                    payload["secret"] = secret
                result = self._client.control("dsh_secret_set" if secret is not None else "dsh_secret_clear", payload)
                return {"state": "synchronized", "credential_generation": result["credential_generation"],
                        "configured": result["configured"]}
        finally:
            secret = None
            if payload is not None:
                payload.pop("secret", None)
            payload = None

    def runtime_status(self):
        with self._lifecycle_lock:
            if self._disposed:
                return {"state": "stopped"}
            diagnostics = getattr(self._process_owner, "diagnostics", None)
            evidence = diagnostics.snapshot() if diagnostics is not None else None
            if evidence is not None and (evidence.get("exit_code") is not None or evidence.get("codes")):
                codes = evidence.get("codes") or []
                return {"state": "failed", "code": codes[-1]["code"] if codes else "native_dsh_runtime_exited",
                        "process": evidence}
            try:
                return self._client.control("dsh_runtime_status")
            except NativeDshMuxError as error:
                # The diagnostic collector may have finished while the pipe
                # reported its close; re-read once, without storing stderr.
                evidence = diagnostics.snapshot() if diagnostics is not None else None
                codes = evidence.get("codes", []) if evidence is not None else []
                return {"state": "failed", "code": codes[-1]["code"] if codes else error.code,
                        **({"process": evidence} if evidence is not None else {})}

    def session_status(self, session_id):
        """Read DSH's public facts without activating or resuming a Session."""
        with self._lifecycle_lock:
            if self._disposed:
                raise NativeDshMuxError("dsh_shell_closed")
            return self._client.control("dsh_runtime_session_status", {"session_id": session_id})

    def attach_cli_retirement(self, retire):
        """Retire local App IPC before draining the upstream request carrier."""
        if not callable(retire):
            raise TypeError("CLI retirement must be callable")
        self._retire_cli_link = retire

    def close(self):
        with self._lifecycle_lock:
            if self._disposed:
                return
            self._disposed = True
            try:
                if self._retire_cli_link is not None:
                    self._retire_cli_link()
                drained = self._client.control("dsh_runtime_drain")
                if not isinstance(drained, dict) or drained.get("state") != "drained":
                    raise NativeDshMuxError("dsh_runtime_drain_ack_invalid")
                # Node idle does not prove a cancelled Host write settled.
                # Keep the reader/domain worker pump alive until actual calls
                # have persisted and attempted their exact response.
                self._client.wait_domain_idle()
                self._client.control("dsh_runtime_shutdown")
                self._shutdown_diagnostic = {"state": "drained_and_stopped"}
            except NativeDshMuxError as error:
                # A lost process cannot acknowledge shutdown. It is retired;
                # App operations are never replayed to obtain that ack.
                self._shutdown_diagnostic = {"state": "closed_with_unknown_outcome", "code": error.code}
            finally:
                super().close()
                self._process_owner.close()

    def shutdown_diagnostic(self):
        with self._lifecycle_lock:
            return None if self._shutdown_diagnostic is None else dict(self._shutdown_diagnostic)
