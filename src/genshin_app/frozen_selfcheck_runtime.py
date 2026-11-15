"""Fixed offline provider transport for QA of the unmodified production factory.

This module is used only by the named selfcheck and isolated tests. It does not
select an alternative application/runtime entry or expose a user Agent mode.
"""
from __future__ import annotations

import contextlib
import hashlib
import json
import subprocess
from collections import Counter
from collections.abc import Mapping
from pathlib import Path

from .agent_chat import native_dsh_production, native_provider_proxy
from .agent_chat.credential_generation import CredentialGenerationController
from .frozen_selfcheck_frontend import (
    prepare_source_frontend_role,
    source_frontend_options_match,
    verify_source_frontend_role,
)
from .frozen_selfcheck_isolation import (
    SelfcheckContext,
    SelfcheckError,
    clean_environment,
    production_selfcheck_command,
)
from .paths import is_frozen


class _EngineeringCredentials:
    available = True

    def __init__(self):
        self.generation = 1
        self.secret = "offline-selfcheck-public-engineering-input"

    def read_versioned(self):
        return self.generation, self.secret

    def compare_and_swap(self, *, expected_generation, next_generation, secret):
        if expected_generation != self.generation:
            return False
        self.generation, self.secret = next_generation, secret
        return True


def engineering_credentials():
    """Exercise the real versioned controller without the Windows backend."""
    return CredentialGenerationController(_EngineeringCredentials())


def verify_fixed_tool_settlements(data_dir, conversation_id, calls):
    """Qualify this fixed QA script using actual durable tool settlements."""
    from .agent_chat.native_app_call_ledger import LEDGER_FILE, NativeAppCallLedger

    directory = Path(data_dir) / "native-dsh-workspaces" / conversation_id
    entries = list(NativeAppCallLedger(directory, conversation_id=conversation_id).read()["entries"].values())
    expected = Counter(call["tool"] for call in calls)
    actual = Counter(entry["identity"]["tool_name"] for entry in entries)
    if (len(entries) != len(calls) or actual != expected
            or any(entry["state"] != "settled_success" or json.loads(entry["result_json"]).get("code") != 0
                   for entry in entries)):
        raise SelfcheckError("selfcheck_fixed_tool_settlements_incomplete")
    return {"count": len(entries), "all_settled_success": True, "tool_counts": dict(sorted(actual.items())),
            "ledger_sha256": hashlib.sha256((directory / LEDGER_FILE).read_bytes()).hexdigest()}


@contextlib.contextmanager
def _candidate_listener_binding(context):
    if context is None:
        yield
        return
    if type(context) is not SelfcheckContext:
        raise SelfcheckError("selfcheck_context_invalid")
    from .agent_chat.native_cli_link import _LoopbackCandidateHTTPServer

    original_bind = _LoopbackCandidateHTTPServer.server_bind

    def bind(server):
        with context._candidate_listener_bind(server):
            return original_bind(server)

    _LoopbackCandidateHTTPServer.server_bind = bind
    try:
        yield
    finally:
        _LoopbackCandidateHTTPServer.server_bind = original_bind


@contextlib.contextmanager
def offline_production_provider(*, root, data_dir, runtime_root, assets_root, package_root,
                                selfcheck_context=None):
    """Insert only a fixed fetch preload into one exact production command.

    The production factory/verifier, entry/arguments, pipe, App lifecycle,
    credential controls, DSH loop and domain handlers stay real. The public
    runtime_environment port receives the fixed QA clean environment, so
    ordinary production user directories cannot select an ambient cache.
    """
    root, runtime, assets = (Path(item).resolve() for item in (root, runtime_root, assets_root))
    if selfcheck_context is not None and (type(selfcheck_context) is not SelfcheckContext
            or selfcheck_context.root != root
            or selfcheck_context.package_root != Path(package_root).resolve()):
        raise SelfcheckError("selfcheck_context_invalid")
    preload = runtime / "src/native-dsh-selfcheck-entry.mjs"
    ordinary, command = production_selfcheck_command(root=root, data_dir=Path(data_dir).resolve(),
        runtime_root=runtime, assets_root=assets, preload=preload)
    context = {"contract": "genshin-offline-production-provider-v1", "sandbox_root": str(root),
               "package_root": str(Path(package_root).resolve()), "main_entry": str(runtime / "src/native-dsh-runtime-entry.mjs")}
    marker = root / "production-provider-context.json"
    with marker.open("x", encoding="utf-8") as stream:
        json.dump(context, stream, ensure_ascii=False)
    original = subprocess.Popen
    original_proxy = native_provider_proxy.read_windows_native_provider_proxy_or_direct
    original_factory = native_dsh_production.create_native_dsh_application
    applications = []
    source_role = None if is_frozen() else prepare_source_frontend_role(
        root=root, runtime_root=runtime, package_root=package_root)
    evidence = {"contract": context["contract"], "launches": [], "frontend_projections": [],
               "main_entry": str(runtime / "src/native-dsh-runtime-entry.mjs")}

    def start(arguments, *args, **kwargs):
        if source_role is not None and source_frontend_options_match(source_role, arguments, args, kwargs):
            if evidence["frontend_projections"]:
                raise SelfcheckError("selfcheck_frontend_launch_count_invalid")
            evidence["frontend_projections"].append({"ordinary": list(source_role["ordinary"]),
                "actual": list(source_role["actual"]), "cwd": str(source_role["cwd"])})
            return original(list(source_role["actual"]), **kwargs)
        if (type(arguments) not in {list, tuple} or tuple(arguments) != ordinary
                or args or set(kwargs) - {"cwd", "env", "stdin", "stdout", "stderr", "creationflags"}
                or Path(kwargs.get("cwd", "")).resolve() != runtime):
            raise SelfcheckError("selfcheck_unexpected_process_command")
        environment = kwargs.get("env")
        if not isinstance(environment, Mapping) or any(
                type(key) is not str or type(value) is not str for key, value in environment.items()):
            raise SelfcheckError("selfcheck_process_environment_invalid")
        if any(key.upper() == "NODE_OPTIONS" for key in environment):
            raise SelfcheckError("selfcheck_node_options_forbidden")
        if evidence["launches"]:
            raise SelfcheckError("selfcheck_production_launch_count_invalid")
        evidence["launches"].append({"ordinary": list(ordinary), "actual": list(command),
                                     "cwd": str(runtime), "environment_keys": sorted(environment)})
        return original(list(command), *args, **kwargs)

    def observe_factory(*args, **kwargs):
        # Production retains standard Windows paths. Only this named QA
        # wrapper redirects user/cache roots through the existing public
        # factory port; no normal runtime or guard policy is weakened.
        kwargs["runtime_environment"] = clean_environment(
            root, kwargs.get("runtime_environment"))
        application = original_factory(*args, **kwargs)
        applications.append(application)
        return application

    subprocess.Popen = start
    native_dsh_production.create_native_dsh_application = observe_factory
    # The isolated provider has no network destination. Supply the ordinary
    # direct-mode setting at the OS-config port without reading user WinINET.
    native_provider_proxy.read_windows_native_provider_proxy_or_direct = dict
    completed = False
    try:
        with _candidate_listener_binding(selfcheck_context):
            yield evidence
            completed = True
    finally:
        subprocess.Popen = original
        native_dsh_production.create_native_dsh_application = original_factory
        native_provider_proxy.read_windows_native_provider_proxy_or_direct = original_proxy
        evidence["applications"] = [{"type": type(application).__name__,
            "release_evidence": application.release_evidence,
            "shutdown": application.shutdown_diagnostic(),
            "process": application._process_owner.diagnostics.snapshot()} for application in applications]
        if selfcheck_context is not None:
            evidence["local_candidate_ipc"] = selfcheck_context.local_candidate_ipc_evidence()
            if completed and (evidence["local_candidate_ipc"]["bind_calls"] != 1
                    or evidence["local_candidate_ipc"]["active_authorizations"] != 0
                    or len(evidence["local_candidate_ipc"]["listeners"]) != 1
                    or not evidence["local_candidate_ipc"]["listeners"][0]["closed"]):
                raise SelfcheckError("selfcheck_candidate_listener_not_closed")
        if completed and len(evidence["launches"]) != 1:
            raise SelfcheckError("selfcheck_production_launch_count_invalid")
        if completed and evidence["frontend_projections"]:
            evidence["frontend_isolation"] = verify_source_frontend_role(source_role)
