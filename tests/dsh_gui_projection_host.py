"""Test-only JSON-lines bridge to the real GuiApi, without a window.

The external JS test owns the browser realm. This bridge does not interpret
messages, simulate candidates or calculate results; it only invokes the same
GUI methods. Provider HTTP and user file-picker choices are explicit fixtures.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from genshin_app.agent_chat.credential_generation import CredentialGenerationController
from genshin_app.agent_chat.native_dsh_application import NativeDshApplication
from genshin_app.agent_chat.native_dsh_host_requests import NativeDshHostRequests
from genshin_app.agent_chat.native_dsh_mux_process import NativeDshMuxProcess
from genshin_app.agent_chat.native_dsh_workspace import NativeDshWorkspaceRegistry
from genshin_app.agent_chat.native_dsh_workspace_compatibility import (
    current_workspace_identity,
)
from genshin_app.gui.api import GuiApi
from genshin_app.gui.document_transport import DOCUMENT_METHODS
from tests.dsh_fixture_environment import isolated_runtime_environment
from tests.test_native_dsh_application import Store

ROOT = Path(__file__).resolve().parents[1]
RAW_METHODS = frozenset({"dsh_client_boot", "dsh_client_rpc", "dsh_client_request_cancel",
    "dsh_client_stream_open", "dsh_client_stream_read", "dsh_client_stream_send", "dsh_client_stream_cancel",
    "dsh_client_bundle", "dsh_client_resource_read", "dsh_client_resource_release", "dsh_client_runtime_status",
    "dsh_client_credential_status", "whiteboard_capabilities", "action_admission_capabilities"})
INITIALIZATION_METHODS = frozenset({"ping", "editor_initialized", "get_startup_file", "get_enums", "get_display_catalog",
    "check_database_update",
    "plan_capabilities", "rotation_capabilities", "mechanics_capabilities", "db_capabilities", "db_overview",
    "list_characters", "list_weapons", "list_artifact_sets", "list_artifacts", "list_plan_details", "list_plans",
    "character_detail", "weapon_detail", "artifact_detail", "search_data", "query_runtime", "list_annotations",
    "inspect_plan_file"})


def main():
    # The test wire is UTF-8 regardless of the Windows console code page.
    sys.stdin.reconfigure(encoding="utf-8")
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", required=True)
    parser.add_argument("--db", required=True)
    parser.add_argument("--script", required=True)
    parser.add_argument("--input")
    parser.add_argument("--startup")
    args = parser.parse_args()
    root = Path(args.root).resolve()
    root.mkdir(parents=True, exist_ok=True)
    credentials = CredentialGenerationController(Store())
    credentials.save("sk-offline-projection-fixture-not-a-real-key", expected_generation=0)
    registry = NativeDshWorkspaceRegistry(root / "workspaces", database_path=Path(args.db).resolve())
    hosts = NativeDshHostRequests(registry, data_dir=root)
    processes = []

    def start(_):
        profile, mirror = root / "profile", root / "mirror"
        profile.mkdir(exist_ok=True)
        mirror.mkdir(exist_ok=True)
        process = NativeDshMuxProcess(node=Path(os.environ["GENSHIN_NATIVE_NODE"]),
            entry=ROOT / "agent-runtime/src/native-dsh-ui-projection-fixture.mjs", working=ROOT,
            runtime_environment=isolated_runtime_environment(profile),
            entry_args=(str(profile), str(mirror), str(ROOT / "frontend"), str(Path(args.script).resolve())),
            domain_handler=hosts, diagnostics_root=root / "process-diagnostics")
        processes.append(process)
        return NativeDshApplication(process, credentials=credentials)

    output = root / "output"
    output.mkdir(exist_ok=True)
    open_path = [str(Path(args.input).resolve()) if args.input else str(output / "result.whiteboard.json")]

    def pick_file(request):
        if request["action"] == "open":
            return open_path[0]
        return str(output / {"whiteboard": "result.whiteboard.json", "candidate_input_draft": "result.gencdraft.json",
                            "report_csv": "result.csv", "report_xlsx": "result.xlsx"}.get(
            request["document_kind"], "result.json"))

    api = GuiApi(root, database_path=Path(args.db).resolve(),
        app_identity={"mode": "development_acceptance", "network_mode": "disabled"},
        startup_path=Path(args.startup).resolve() if args.startup else None, dsh_shell_factory=start,
        dsh_workspace_registry=registry, dsh_credentials=credentials,
        dsh_file_dialog=pick_file, dsh_result_save_dialog=lambda _: str(output / "result.calculated.json"))
    writer = threading.Lock()

    def reply(value):
        with writer:
            sys.stdout.write(json.dumps(value, ensure_ascii=True, allow_nan=False) + "\n")
            sys.stdout.flush()

    def dispatch(message):
        try:
            method = message["method"]
            if method == "fixture_workspace_identity":
                result = current_workspace_identity(Path(args.db).resolve().parent)
            elif method == "fixture_pick_open":
                selected = Path(message["args"][0]).resolve()
                if not selected.is_relative_to(root.parent):
                    raise ValueError("fixture input outside test root")
                open_path[0] = str(selected)
                result = {"selected": True}
            elif method == "fixture_fresh_read":
                fresh = GuiApi(root, database_path=Path(args.db).resolve())
                try:
                    result = fresh.whiteboard_load(str(output / "result.whiteboard.json"))
                    if result["code"] == 0 and (output / "result.calculated.json").is_file():
                        returned = fresh.whiteboard_read_result(result["data"]["board"], str(output / "result.calculated.json"))
                        result = {"board": result, "calculated": returned}
                finally:
                    fresh._close_dsh_shell()
            elif method == "document_json_call":
                if message["args"][0] not in DOCUMENT_METHODS:
                    raise ValueError("unreviewed document operation")
                if (message["args"][0] == "dsh_client_publish_whiteboard_result"
                        and json.loads(message["args"][1])[0].get("format") in ("csv", "xlsx")):
                    from unittest.mock import patch

                    with (patch("genshin_app.equipment.run_plan_calc", side_effect=AssertionError("fresh calculation during export")),
                          patch("genshin_engine.mechanics.execute_mechanics", side_effect=AssertionError("mechanism replay during export"))):
                        result = api.document_json_call(*message["args"])
                else:
                    result = api.document_json_call(*message["args"])
            elif method in RAW_METHODS or method in INITIALIZATION_METHODS or method in DOCUMENT_METHODS:
                result = getattr(api, method)(*message["args"])
            else:
                raise ValueError("unreviewed projection operation")
            reply({"id": message["id"], "ok": True, "value": result})
        except Exception as error:  # noqa: BLE001 - typed test transport failure only
            reply({"id": message["id"], "ok": False,
                "code": getattr(error, "code", getattr(error, "reason", "projection_call_failed"))})

    with ThreadPoolExecutor(max_workers=16, thread_name_prefix="test-gui-api") as executor:
        try:
            for line in sys.stdin:
                message = json.loads(line)
                if message.get("method") == "fixture_shutdown":
                    api._close_dsh_shell()
                    reply({"id": message["id"], "ok": True, "value": {"closed": True}})
                    break
                executor.submit(dispatch, message)
        finally:
            api._close_dsh_shell()
            for process in processes:
                process.close()


if __name__ == "__main__":
    main()
