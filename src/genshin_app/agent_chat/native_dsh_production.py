"""Verify and launch the dedicated DSH client-shell composition.

Release trust is pinned outside the runtime tree. Source/CLI qualification can
explicitly use development verification with an isolated candidate manifest;
there is no automatic fallback to the retired V2 service or a PATH Node.
"""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

from genshin_app.paths import is_frozen, resource_dir

from .credential_generation import CredentialGenerationController
from .credential_store import default_credential_store
from .native_dsh_application import NativeDshApplication
from .native_dsh_host_requests import NativeDshHostRequests
from .native_dsh_mux_client import NativeDshMuxError
from .native_dsh_mux_process import NativeDshMuxProcess
from .native_runtime_environment import build_native_runtime_environment
from .runtime_integrity import verify_dsh_client_shell_manifest
from .sessions import safe_path

MANIFEST_FILE = "dsh-client-shell-runtime-manifest.json"
MAX_MANIFEST_BYTES = 16 * 1024 * 1024


def legacy_credential_import_allowed(profile: Path) -> bool:
    """Only a pre-existing profile may adopt the former shared Windows key.

    The non-secret decision survives restarts; a new installation must not
    import another installation's credential on its second launch.
    """
    marker = safe_path(profile, "legacy-credential-import.json")
    allowed = (profile / "runtime-profile" / "legacy-settings-migration.json").is_file()
    payload = {"contract": "genshin-legacy-credential-import-v1", "allowed": allowed}
    try:
        with marker.open("x", encoding="utf-8") as handle:
            json.dump(payload, handle)
    except FileExistsError:
        try:
            payload = json.loads(marker.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            raise NativeDshMuxError("dsh_credential_migration_marker_invalid") from None
    if (not isinstance(payload, dict) or set(payload) != {"contract", "allowed"}
            or payload["contract"] != "genshin-legacy-credential-import-v1"
            or type(payload["allowed"]) is not bool):
        raise NativeDshMuxError("dsh_credential_migration_marker_invalid")
    return payload["allowed"]


def _strict_object(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise NativeDshMuxError("dsh_shell_manifest_json")
        value[key] = item
    return value


def verify_native_dsh_production(*, runtime_root, assets_root, node_executable,
                                 manifest_path=None, verification_mode="release",
                                 expected_manifest_sha256=None):
    if verification_mode not in {"release", "development"}:
        raise NativeDshMuxError("dsh_shell_verification_mode_invalid")
    if verification_mode == "release" and (type(expected_manifest_sha256) is not str
            or re.fullmatch(r"[0-9a-f]{64}", expected_manifest_sha256) is None):
        raise NativeDshMuxError("dsh_shell_release_pin_required")
    runtime = Path(runtime_root).absolute()
    manifest_path = Path(manifest_path) if manifest_path is not None else runtime / MANIFEST_FILE
    if manifest_path.is_symlink():
        raise NativeDshMuxError("dsh_shell_manifest_path_invalid")
    try:
        with manifest_path.open("rb") as stream:
            raw = stream.read(MAX_MANIFEST_BYTES + 1)
    except OSError:
        raise NativeDshMuxError("dsh_shell_manifest_missing") from None
    if not raw or len(raw) > MAX_MANIFEST_BYTES:
        raise NativeDshMuxError("dsh_shell_manifest_size")
    if expected_manifest_sha256 is not None and hashlib.sha256(raw).hexdigest() != expected_manifest_sha256:
        raise NativeDshMuxError("dsh_shell_release_manifest_mismatch")
    try:
        manifest = json.loads(raw.decode("utf-8"), object_pairs_hook=_strict_object,
                              parse_constant=lambda _: (_ for _ in ()).throw(ValueError("non-finite")))
    except (ValueError, UnicodeError, RecursionError):
        raise NativeDshMuxError("dsh_shell_manifest_json") from None
    evidence = verify_dsh_client_shell_manifest(manifest, runtime_root=runtime,
        assets_root=assets_root, node_executable=node_executable)
    return {"manifest": manifest, "evidence": evidence,
            "manifest_sha256": hashlib.sha256(raw).hexdigest(), "verification_mode": verification_mode}


def create_native_dsh_application(data_dir: Path, *, workspaces, credentials=None,
                                  runtime_root=None, assets_root=None, node_executable=None,
                                  manifest_path=None, verification_mode="release",
                                  expected_manifest_sha256=None, runtime_environment=None):
    """Start a persistent idle frontend carrier; no model request is made."""
    resources = resource_dir()
    runtime = Path(runtime_root) if runtime_root is not None else resources / "agent-runtime"
    assets = Path(assets_root) if assets_root is not None else (
        resources / "agent-client-assets" if is_frozen() else resources / "frontend")
    node = Path(node_executable) if node_executable is not None else runtime / "node.exe"
    verified = verify_native_dsh_production(runtime_root=runtime, assets_root=assets,
        node_executable=node, manifest_path=manifest_path, verification_mode=verification_mode,
        expected_manifest_sha256=expected_manifest_sha256)
    profile_parent = Path(data_dir).absolute() / "native_agent_sessions_v2"
    profile = safe_path(profile_parent, "dsh-production-v1")
    mirror = safe_path(profile_parent, "dsh-file-preview")
    # These are App-selected private roots. They are never accepted from the
    # Renderer, a prompt, a tool call or an inherited working directory.
    profile.mkdir(parents=True, exist_ok=True)
    import_legacy = legacy_credential_import_allowed(profile)
    mirror.mkdir(parents=True, exist_ok=True)
    safe_path(profile, "sessions")
    supplied_environment = (None if runtime_environment is None else
        {key: value for key, value in runtime_environment.items()
         if key.upper() not in {"GENSHIN_NATIVE_DSH_PROFILE_ROOT", "DSH_HOME"}})
    environment = build_native_runtime_environment(supplied_environment)
    # The App owns this root. Replace ambient/injected profile suggestions
    # before the child can import any upstream module or resolve OS homedir.
    environment["GENSHIN_NATIVE_DSH_PROFILE_ROOT"] = str(profile.resolve())
    process = NativeDshMuxProcess(node=node, entry=runtime / verified["manifest"]["entry"], working=runtime,
        entry_args=(str(profile), str(mirror), str(assets.resolve())),
        domain_handler=NativeDshHostRequests(workspaces, data_dir=data_dir), runtime_environment=environment,
        diagnostics_root=profile / "process-diagnostics")
    try:
        application = NativeDshApplication(process, credentials=credentials or
            CredentialGenerationController(default_credential_store()),
            synchronize_legacy=import_legacy or credentials is not None)
        application.release_evidence = {key: verified[key] for key in (
            "manifest_sha256", "verification_mode")}
        enable_cli_link = getattr(workspaces, "enable_cli_link", None)
        if callable(enable_cli_link):
            enable_cli_link(settings_remote=application.dsh_client_rpc,
                            settings_cancel=application.dsh_client_request_cancel)
            application.attach_cli_retirement(workspaces.close_cli_link)
        return application
    except Exception:
        try:
            process.close()
        except Exception:  # noqa: BLE001 - cleanup must not replace the original constructor failure
            diagnostics = getattr(process, "diagnostics", None)
            record = getattr(diagnostics, "record_code", None)
            if callable(record):
                try:
                    record(b"native_dsh_startup_cleanup_failed")
                except Exception:  # noqa: BLE001, S110 - logging failure must not mask the primary error
                    pass
        raise
