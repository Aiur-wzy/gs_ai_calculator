"""Early, fixed packaged CLI qualification. Never launches a GUI or reads a Key."""
from __future__ import annotations

import argparse
import hashlib
import json
import marshal
import os
import struct
import subprocess
import sys
from pathlib import Path

from .frozen_selfcheck_isolation import (
    DeniedCredentials,
    SelfcheckContext,
    SelfcheckError,
    clean_environment,
    inside,
)


def _sha(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def _import_layout(context):
    if not getattr(sys, "frozen", False) or not hasattr(sys, "_MEIPASS"):
        raise SelfcheckError("selfcheck_requires_packaged_cli", {"execution_layer": "source", "qualified": False})
    if not inside(sys._MEIPASS, context.package_root):
        raise SelfcheckError("selfcheck_module_root_outside_package")
    bad = [value for value in sys.path if value and not inside(value, context.package_root)]
    if bad:
        raise SelfcheckError("selfcheck_source_fallback_path", {"count": len(bad)})
    return {"layer": "frozen_cli_execution", "source_fallback": False,
            "module_loader": type(sys.modules[__name__].__loader__).__name__}


def _frozen_archive_identity(context):
    """Hash the exact active PYZ bytes, not a source inventory or rebuilt code.

    This is a narrow PyInstaller qualification seam, not a production import
    implementation. Unsupported loader metadata must fail qualification.
    """
    loader = sys.modules[__name__].__loader__
    archive = getattr(loader, "_pyz_archive", None)
    if (type(loader).__name__ != "PyiFrozenLoader" or archive is None
            or not type(loader).__module__.endswith("pyimod02_importers")):
        raise SelfcheckError("selfcheck_active_archive_not_frozen")
    filename = Path(archive._filename).resolve()
    start = archive._start_offset
    if not inside(filename, context.package_root) or type(start) is not int or start < 0:
        raise SelfcheckError("selfcheck_active_archive_outside_package")
    with filename.open("rb") as stream:
        stream.seek(start)
        header = stream.read(12)
        if len(header) != 12 or header[:4] != b"PYZ\0":
            raise SelfcheckError("selfcheck_active_archive_header_invalid")
        toc_offset = struct.unpack("!i", header[8:12])[0]
        if toc_offset < 12 or start + toc_offset >= filename.stat().st_size:
            raise SelfcheckError("selfcheck_active_archive_offset_invalid")
        stream.seek(start + toc_offset)
        toc = marshal.load(stream)
        if dict(toc) != archive.toc:
            raise SelfcheckError("selfcheck_active_archive_toc_mismatch")
        length = stream.tell() - start
        digest = hashlib.sha256()
        stream.seek(start)
        remaining = length
        while remaining:
            chunk = stream.read(min(1024 * 1024, remaining))
            if not chunk:
                raise SelfcheckError("selfcheck_active_archive_short_read")
            digest.update(chunk)
            remaining -= len(chunk)
    return {"executable_sha256": _sha(context.executable),
            "executable_bytes": context.executable.stat().st_size,
            "pyz": {"container": filename.relative_to(context.package_root).as_posix(),
                    "offset": start, "bytes": length, "sha256": digest.hexdigest(),
                    "identity_source": "active_PyiFrozenLoader"}}


def _negative_probes(context):
    import socket
    probes = [lambda: context.audit("open", (context.root.parent / "outside-denied", "w", 0)),
              lambda: context.audit("open", (context.root.parent / "outside-denied", "r", 0)),
              lambda: socket.create_connection(("127.0.0.1", 9)),
              lambda: DeniedCredentials(context).status()]
    with context.negative_probe():
        for operation in probes:
            try:
                operation()
            except SelfcheckError:
                continue
            raise SelfcheckError("selfcheck_negative_probe_not_denied")


def _startup(context):
    from genshin_app.database_bootstrap import prepare_database_startup
    from genshin_app.paths import ensure_data_dir
    directory = ensure_data_dir(context.root / "data")
    receipt = prepare_database_startup(directory)
    if not receipt.get("can_start"):
        raise SelfcheckError("selfcheck_normal_startup_refused", receipt)
    return directory, receipt


def _fresh_readback(context):
    from genshin_app.equipment.files import fingerprint
    from genshin_app.gui.api import GuiApi
    from genshin_app.gui.headless_document_client import GuiDocumentClient
    directory, startup = _startup(context)
    api = GuiApi(directory, dsh_credentials=DeniedCredentials(context))
    try:
        document = GuiDocumentClient(api)
        board = document.call("whiteboard_load", str(context.root / "flow" / "result.whiteboard.json"))["board"]
        loaded = document.call("whiteboard_read_result", board, str(context.root / "flow" / "result.calculated.json"))
        calculated = loaded["calculated"]
        from genshin_app.frozen_selfcheck_calculation import (
            verify_flow_budget,
            verify_flow_damage_explanations,
        )
        attribute_budget = verify_flow_budget(calculated)
        damage_explanations = verify_flow_damage_explanations(calculated)
        from genshin_app.frozen_selfcheck_inputs import EXPECTED_DAMAGE
        total = calculated["result"]["summary"]["total_damage"]
        if abs(total - EXPECTED_DAMAGE) > 1e-8:
            raise SelfcheckError("selfcheck_readback_numeric_mismatch")
        return {"status": "verified", "startup": startup, "board_fingerprint": fingerprint(board),
                "result_fingerprint": fingerprint(calculated["result"]), "receipt_fingerprint": fingerprint(calculated["result_receipt"]),
                "total_damage": total, "hit_count": len(calculated["result"]["hits"]),
                "attribute_budget": attribute_budget,
                "damage_explanations": damage_explanations,
                "actual_loader": "GuiApi.document_json_call/whiteboard_load/whiteboard_read_result"}
    finally:
        api._close_dsh_shell()


def _run(context):
    from genshin_app.package_integrity import verify_package_artifact
    from genshin_app.paths import resource_dir
    from genshin_app.version import (
        APP_RELEASE_ID,
        NATIVE_AGENT_DSH_SHELL_MANIFEST_SHA256,
    )
    from genshin_engine.runtime_dependencies import verify_frozen_engine_dependencies

    evidence = {"status": "running", "app_release_id": APP_RELEASE_ID, "execution": _import_layout(context)}
    evidence["binary_identity"] = _frozen_archive_identity(context)
    evidence["package_before"] = verify_package_artifact(context.package_root)
    evidence["engine_dependencies"] = verify_frozen_engine_dependencies()
    directory, evidence["startup"] = _startup(context)
    _negative_probes(context)
    from genshin_app.agent_chat.native_dsh_production import (
        verify_native_dsh_production,
    )
    runtime, assets = resource_dir() / "agent-runtime", resource_dir() / "agent-client-assets"
    verified = verify_native_dsh_production(runtime_root=runtime, assets_root=assets,
        node_executable=context.node, expected_manifest_sha256=NATIVE_AGENT_DSH_SHELL_MANIFEST_SHA256)
    if "src/native-dsh-selfcheck-entry.mjs" not in verified["manifest"]["runtime_files"]:
        raise SelfcheckError("selfcheck_entry_not_in_accepted_runtime")
    evidence["runtime_manifest_sha256"] = verified["manifest_sha256"]
    from genshin_app.frozen_selfcheck_inputs import (
        EXPECTED_DAMAGE,
        build_fixed_inputs,
        verify_fixed_world,
    )
    evidence["world_execution"] = verify_fixed_world()
    from genshin_app.frozen_selfcheck_special import verify_fixed_special_reactions
    evidence["special_engineering_execution"] = verify_fixed_special_reactions(frozen_package_root=context.package_root)
    from genshin_app.frozen_selfcheck_calculation import verify_fixed_calculations
    evidence["calculation_engineering_execution"] = verify_fixed_calculations(
        context.root / "calculation-probes", frozen_package_root=context.package_root)
    board, calls = build_fixed_inputs(context.root, directory / "genshin.db")
    from genshin_app.whiteboard import save_whiteboard
    source = context.root / "input.whiteboard.json"
    save_whiteboard(source, board, db_path=directory / "genshin.db")
    calls_path = context.root / "fixed-calls.json"
    calls_path.write_text(json.dumps(calls, ensure_ascii=False), encoding="utf-8")
    calls_sha = _sha(calls_path)
    from genshin_app.agent_chat.dsh_gui_workflow_runner import run_gui_agent_workflow
    from genshin_app.agent_chat.native_turn_budget import DEFAULT_BUDGET
    from genshin_app.frozen_selfcheck_runtime import (
        engineering_credentials,
        offline_production_provider,
        verify_fixed_tool_settlements,
    )
    from genshin_app.gui.api import GuiApi
    observation_interval = DEFAULT_BUDGET["wall_clock_seconds"]
    evidence["budget_basis"] = {"source": "native_turn_budget.DEFAULT_BUDGET.wall_clock_seconds",
        "startup_owner": "normal production factory and NativeDshMuxProcess defaults; no QA override",
        "workflow_observation_interval_seconds": observation_interval,
        "healthy_turn_wall_deadline": None, "fresh_process_wall_deadline": None}
    credentials = engineering_credentials()
    def factory(data_dir, **options):
        return GuiApi(data_dir, database_path=directory / "genshin.db", dsh_credentials=credentials, **options)
    factory.dsh_projection_provider_mode = "deterministic_provider_transport"
    with offline_production_provider(root=context.root, data_dir=directory, runtime_root=runtime,
            assets_root=assets, package_root=context.package_root,
            selfcheck_context=context) as production:
        task = run_gui_agent_workflow(data_dir=directory, prompt="完成固定工程输入的真实App计算、审核同步与双文件保存；这不是角色技能实验。",
            output_dir=context.root / "flow", input_path=source, timeout_seconds=observation_interval,
            provider_mode="deterministic_provider_transport", api_factory=factory)
        evidence["workflow"] = task
    evidence["production_factory"] = production
    if task.get("status") != "verified" or task.get("candidate", {}).get("calculation_acceptance", {}).get("status") != "accepted":
        raise SelfcheckError("selfcheck_gui_projection_not_accepted", task)
    evidence["tool_settlements"] = verify_fixed_tool_settlements(directory, task["conversation_id"], calls)
    publication = task.get("receipt_publication", {})
    receipt_path = context.root / "flow/gui-workflow-receipt.json"
    if (publication.get("status") != "published" or publication.get("path") != str(receipt_path)
            or not receipt_path.is_file() or json.loads(receipt_path.read_text(encoding="utf-8")) != task):
        raise SelfcheckError("selfcheck_workflow_receipt_not_published", publication)
    evidence["workflow_receipt_file"] = {"path": str(receipt_path), "file_sha": _sha(receipt_path)}
    if (len(production["applications"]) != 1 or _sha(calls_path) != calls_sha
            or production["applications"][0]["type"] != "NativeDshApplication"
            or production["applications"][0]["release_evidence"] != {
                "manifest_sha256": NATIVE_AGENT_DSH_SHELL_MANIFEST_SHA256, "verification_mode": "release"}
            or production["applications"][0]["shutdown"] != {"state": "drained_and_stopped"}
            or production["applications"][0]["process"].get("exit_code") != 0):
        raise SelfcheckError("selfcheck_fixed_composition_changed")
    # Read the real durable settlement, not only a public tool/call counter.
    # This does not activate another session or send a second model request.
    from genshin_app.agent_chat.native_app_call_ledger import NativeAppCallLedger
    from genshin_app.frozen_selfcheck_calculation import verify_appendix_index_response
    entries = NativeAppCallLedger(directory / "native-dsh-workspaces" / task["conversation_id"],
        conversation_id=task["conversation_id"]).read()["entries"]
    navigation = [entry for entry in entries.values()
                  if entry["identity"]["tool_name"] == "gs_get_character_appendix_index"]
    if (task.get("app_tool_calls", {}).get("gs_get_character_appendix_index") != 1
            or len(navigation) != 1 or navigation[0]["state"] != "settled_success"):
        raise SelfcheckError("selfcheck_appendix_navigation_not_settled")
    selected_id = next(call["arguments"]["db_character_id"] for call in calls
                       if call["tool"] == "gs_get_character_appendix_index")
    evidence["appendix_navigation"] = verify_appendix_index_response(json.loads(navigation[0]["result_json"]), selected_id)
    node_isolation = json.loads((context.root / "node-isolation.json").read_text(encoding="utf-8"))
    if (any(node_isolation.get("counters", {}).get("normal", {}).values())
            or node_isolation.get("process_exit_observed") is not True
            or node_isolation.get("main_entry") != str(runtime / "src/native-dsh-runtime-entry.mjs")
            or node_isolation.get("provider_requests") != len(calls) + 1):
        raise SelfcheckError("selfcheck_node_forbidden_attempt", node_isolation)
    evidence["node_isolation"] = node_isolation
    evidence["node_effective_environment_keys"] = node_isolation["effective_environment_keys"]
    from genshin_app.equipment.files import fingerprint
    saved_board = json.loads(Path(task["board"]["path"]).read_text(encoding="utf-8"))
    saved_package = json.loads(Path(task["calculated"]["path"]).read_text(encoding="utf-8"))
    saved_calculation = saved_package["calculated"]
    from genshin_app.frozen_selfcheck_calculation import (
        verify_flow_budget,
        verify_flow_damage_explanations,
    )
    evidence["dsh_app_attribute_budget"] = verify_flow_budget(saved_calculation)
    evidence["dsh_app_damage_explanations"] = verify_flow_damage_explanations(saved_calculation)
    expected_readback = {"board_fingerprint": fingerprint(saved_board),
        "result_fingerprint": fingerprint(saved_calculation["result"]),
        "receipt_fingerprint": fingerprint(saved_calculation["result_receipt"])}
    # An independently started, real frozen interpreter reads through the
    # ordinary App loader; the in-process fresh facade above is not sufficient.
    fresh_report = context.root / "fresh-readback.json"
    child = subprocess.run([str(context.executable), "internal", "verify-frozen-readback",
                            "--sandbox-root", str(context.root), "--report", str(fresh_report)],
                           cwd=context.root, env=clean_environment(context.root), capture_output=True,
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0), check=False)
    if child.returncode != 0 or not fresh_report.is_file():
        raise SelfcheckError("selfcheck_fresh_frozen_readback_failed", {"returncode": child.returncode})
    fresh = json.loads(fresh_report.read_text(encoding="utf-8"))
    if fresh.get("status") != "verified" or fresh.get("result", {}).get("total_damage") != EXPECTED_DAMAGE:
        raise SelfcheckError("selfcheck_fresh_readback_mismatch", fresh)
    if any(fresh["result"].get(key) != expected for key, expected in expected_readback.items()):
        raise SelfcheckError("selfcheck_fresh_canonical_content_mismatch")
    if fresh["result"].get("attribute_budget") != evidence["dsh_app_attribute_budget"]:
        raise SelfcheckError("selfcheck_fresh_attribute_budget_mismatch")
    if fresh["result"].get("damage_explanations") != evidence["dsh_app_damage_explanations"]:
        raise SelfcheckError("selfcheck_fresh_damage_explanations_mismatch")
    for key in ("board", "calculated"):
        if _sha(task[key]["path"]) != task[key]["file_sha"]:
            raise SelfcheckError("selfcheck_saved_file_changed_after_fresh_readback")
    if _sha(receipt_path) != evidence["workflow_receipt_file"]["file_sha"]:
        raise SelfcheckError("selfcheck_workflow_receipt_changed_after_fresh_readback")
    evidence["fresh_frozen_readback"] = fresh
    evidence["package_after"] = verify_package_artifact(context.package_root)
    if evidence["package_after"] != evidence["package_before"]:
        raise SelfcheckError("selfcheck_package_changed")
    context.assert_normal_clean()
    evidence["status"] = "verified"
    return evidence


def main(argv, *, readback=False):
    parser = argparse.ArgumentParser(prog="internal verify-frozen", allow_abbrev=False)
    parser.add_argument("--sandbox-root", required=True)
    parser.add_argument("--report", required=True)
    context = None
    prepared = False
    try:
        args = parser.parse_args(argv)
        if not getattr(sys, "frozen", False):
            raise SelfcheckError("selfcheck_requires_packaged_cli", {"execution_layer": "source", "qualified": False})
        context = SelfcheckContext.create(args.sandbox_root, args.report, package_root=Path(sys.executable).parent,
                                          executable=sys.executable, fresh=not readback)
        marker = context.root / ".frozen-selfcheck-context.json"
        marker_value = {"contract": "genshin-frozen-selfcheck-context-v1", "sandbox_root": str(context.root),
                        "package_root": str(context.package_root), "executable_sha256": _sha(context.executable)}
        if readback and (not marker.is_file() or json.loads(marker.read_text(encoding="utf-8")) != marker_value):
            raise SelfcheckError("selfcheck_readback_requires_parent_created_context")
        context.prepare()
        prepared = True
        if not readback:
            with marker.open("x", encoding="utf-8") as stream:
                json.dump(marker_value, stream)
        result = _fresh_readback(context) if readback else _run(context)
        if readback:
            result["execution"] = _import_layout(context)
        context.assert_normal_clean()
        report = {"contract": "genshin-frozen-cli-selfcheck-v1", "status": "verified", "execution_layer": "frozen",
                  "result": result, "python_isolation": context.counters, "forbidden_operations_executed": 0,
                  "python_local_candidate_ipc": context.local_candidate_ipc_evidence(),
                  "python_effective_environment_keys": sorted(os.environ), "real_gui_tested": False}
        context.write_report(report)
        print(json.dumps({"code": 0, "message": "ok", "data": {"status": "verified", "report": str(context.report)}}))
        return 0
    except (Exception, SystemExit) as error:  # noqa: BLE001 - isolated CLI boundary, no GUI fallback
        if isinstance(error, SystemExit) and error.code == 0:
            return 0
        reason = getattr(error, "reason", getattr(error, "code", "selfcheck_failed"))
        if not isinstance(reason, str):
            reason = "selfcheck_arguments_invalid"
        details = getattr(error, "details", None)
        if details is None and not isinstance(error, (SelfcheckError, SystemExit)):
            details = {"exception_type": type(error).__name__, "message": str(error)[:2048]}
        failure = {"code": 1, "message": str(reason), "reason": reason, "details": details}
        if prepared and context is not None and context.root.is_dir() and not context.report.exists():
            context.write_report({"contract": "genshin-frozen-cli-selfcheck-v1", "status": "failed", "failure": failure,
                                  "python_isolation": context.counters,
                                  "python_local_candidate_ipc": context.local_candidate_ipc_evidence(),
                                  "real_gui_tested": False})
        print(json.dumps(failure, ensure_ascii=False), file=sys.stderr)
        return 1
