"""Source checks never claim to be actual frozen-package qualification."""
from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

from genshin_app.frozen_selfcheck_inputs import EXPECTED_DAMAGE, build_fixed_inputs
from genshin_app.frozen_selfcheck_isolation import (
    DeniedCredentials,
    SelfcheckContext,
    SelfcheckError,
    clean_environment,
)
from tests.test_queries import db_path as db_path  # noqa: PLC0414


def test_environment_allowlist_drops_secret_proxy_python_and_user_roots(tmp_path):
    source = {"SYSTEMROOT": "C:/Windows", "DEEPSEEK_API_KEY": "never-used", "OPENAI_API_KEY": "never-used",
              "HTTP_PROXY": "never-used", "PYTHONPATH": "never-used", "APPDATA": "outside", "USERPROFILE": "outside"}
    result = clean_environment(tmp_path, source)
    assert result["SYSTEMROOT"] == source["SYSTEMROOT"]
    assert not set(result) & {"DEEPSEEK_API_KEY", "OPENAI_API_KEY", "HTTP_PROXY", "PYTHONPATH"}
    assert all(str(tmp_path.resolve()) in result[key] for key in ("APPDATA", "LOCALAPPDATA", "USERPROFILE", "TEMP", "TMP"))


def test_root_and_report_validation_are_prewrite(tmp_path):
    package = tmp_path / "package"
    package.mkdir()
    with pytest.raises(SelfcheckError):
        SelfcheckContext.create(package / "inside", package / "inside" / "report.json", package_root=package, executable=package / "app.exe")
    with pytest.raises(SelfcheckError):
        SelfcheckContext.create(tmp_path / "fresh", tmp_path / "elsewhere.json", package_root=package, executable=package / "app.exe")
    assert not (tmp_path / "fresh").exists()


def test_launcher_never_accepts_gui_executable(tmp_path, monkeypatch):
    from scripts import verify_frozen_cli
    gui = tmp_path / "GenshinCalcGUI.exe"
    gui.write_bytes(b"not-executed")
    monkeypatch.setattr(verify_frozen_cli.subprocess, "run", lambda *_args, **_kwargs: pytest.fail("GUI launch"))
    root = tmp_path / "fresh"
    with pytest.raises(ValueError, match="only_packaged"):
        verify_frozen_cli.run_selfcheck(gui, root, root / "report.json")
    assert not root.exists()


def test_fresh_root_race_rejects_before_environment_changes(tmp_path):
    package = tmp_path / "package"
    package.mkdir()
    root = tmp_path / "fresh"
    context = SelfcheckContext.create(root, root / "report.json", package_root=package, executable=package / "app.exe")
    root.mkdir()
    sentinel = root / "external.txt"
    sentinel.write_text("keep", encoding="utf-8")
    before = os.environ.copy()
    with pytest.raises(FileExistsError):
        context.prepare()
    assert os.environ == before
    assert sentinel.read_text(encoding="utf-8") == "keep"
    assert not (root / "temp").exists()


def test_launcher_uses_only_fake_ambient_sentinels_and_rejects_source_receipt(tmp_path, monkeypatch):
    from types import SimpleNamespace

    from scripts import verify_frozen_cli
    package = tmp_path / "package"
    package.mkdir()
    executable = package / "GenshinCalc.exe"
    executable.write_bytes(b"mocked-launcher-contract-only")
    root = tmp_path / "fresh"
    report = root / "report.json"
    monkeypatch.setenv("DEEPSEEK_API_KEY", "ambient-private-must-never-be-read")
    def inspect_launch(arguments, **options):
        assert arguments[:3] == [str(executable), "internal", "verify-frozen"]
        assert "timeout" not in options
        assert options["env"]["DEEPSEEK_API_KEY"] == "offline-selfcheck-fake-never-use"
        assert "ambient-private" not in json.dumps(options["env"])
        assert options["creationflags"] == getattr(subprocess, "CREATE_NO_WINDOW", 0)
        root.mkdir()
        report.write_text(json.dumps({"status": "verified", "execution_layer": "source", "real_gui_tested": False}), encoding="utf-8")
        return SimpleNamespace(returncode=0)
    monkeypatch.setattr(verify_frozen_cli.subprocess, "run", inspect_launch)
    with pytest.raises(RuntimeError, match="receipt_invalid"):
        verify_frozen_cli.run_selfcheck(executable, root, report)


def test_source_cli_is_rejected_before_normal_profile_factories(tmp_path, monkeypatch, capsys):
    import json

    from genshin_app import cli, paths
    def forbidden():
        pytest.fail("normal user path factory was reached")
    monkeypatch.setattr(paths, "default_data_dir", forbidden)
    sandbox = tmp_path / "must-not-be-created"
    assert cli.main(["internal", "verify-frozen", "--sandbox-root", str(sandbox), "--report", str(sandbox / "report.json")]) == 1
    result = json.loads(capsys.readouterr().err)
    assert result["reason"] == "selfcheck_requires_packaged_cli"
    assert result["details"]["execution_layer"] == "source"
    assert not sandbox.exists()


def test_python_guard_denials_are_counted_and_negative_is_separate(tmp_path):
    package = tmp_path / "package"
    package.mkdir()
    root = tmp_path / "fresh"
    ctx = SelfcheckContext.create(root, root / "report.json", package_root=package, executable=package / "app.exe")
    with ctx.negative_probe():
        for event, arguments in [("open", (tmp_path / "outside", "w", 0)), ("socket.connect", (None, ("localhost", 80)))]:
            with pytest.raises(SelfcheckError):
                ctx.audit(event, arguments)
        with pytest.raises(SelfcheckError):
            DeniedCredentials(ctx).status()
    ctx.assert_normal_clean()
    assert ctx.counters["negative"] == {"outside_write": 1, "network": 1, "credential": 1}
    with pytest.raises(SelfcheckError):
        ctx.audit("open", (tmp_path / "outside", "r", 0))
    with pytest.raises(SelfcheckError):
        ctx.assert_normal_clean()


def test_actual_audit_hook_in_disposable_source_process(tmp_path):
    package = tmp_path / "package"
    package.mkdir()
    outside = tmp_path / "external-profile-sentinel"
    outside.write_bytes(b"unchanged")
    repository = Path(__file__).resolve().parents[1]
    script = r'''
import json,sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
from genshin_app.frozen_selfcheck_isolation import SelfcheckContext,SelfcheckError
root,package,outside=map(Path,sys.argv[2:])
context=SelfcheckContext.create(root,root/'report.json',package_root=package,executable=package/'app.exe')
context.prepare()
(root/'ordinary.txt').write_bytes(b'local')
with context.negative_probe():
 for operation in (lambda:outside.read_bytes(),lambda:outside.write_bytes(b'forbidden')):
  try: operation()
  except SelfcheckError: pass
  else: raise RuntimeError('audit_hook_did_not_deny')
context.assert_normal_clean()
print(json.dumps({'source_only':True,'counts':context.counters}))
'''
    child = subprocess.run([sys.executable, "-c", script, str(repository / "src"), str(tmp_path / "isolated"),
                            str(package), str(outside)], capture_output=True, text=True, encoding="utf-8", check=True)
    report = json.loads(child.stdout)
    assert report["source_only"] is True
    assert report["counts"] == {"normal": {}, "negative": {"outside_read": 1, "outside_write": 1}}
    assert outside.read_bytes() == b"unchanged"


def test_fixed_engineering_inputs_use_real_candidate_execution(tmp_path, db_path):
    # Extend only this fresh synthetic test DB, never the accepted live DB.
    with sqlite3.connect(db_path) as connection:
        connection.row_factory = sqlite3.Row
        row = dict(connection.execute("SELECT * FROM character LIMIT 1").fetchone())
        row.update(id=90000002, name="第二工程身份")
        connection.execute("INSERT INTO character (" + ",".join(row) + ") VALUES (" + ",".join("?" for _ in row) + ")", tuple(row.values()))
    board, calls = build_fixed_inputs(tmp_path, db_path)
    assert board["team"] == [] and board["actions"] == []
    assert calls[0]["tool"] == "gs_patch_whiteboard"
    operations = calls[0]["arguments"]["operations"]
    assert len(operations[0]["value"]) == 2
    assert sum(row["action_type_id"] == "action.elemental_burst" for row in operations[1]["value"]) == 2
    assert calls[-1]["tool"] == "gs_calculate"
    navigation = [call for call in calls if call["tool"] == "gs_get_character_appendix_index"]
    assert navigation == [{"tool": "gs_get_character_appendix_index",
                           "arguments": {"db_character_id": operations[0]["value"][0]["db_character_id"]}}]
    assert len(calls) == 13
    assert EXPECTED_DAMAGE == 594


def test_world_proof_really_advances_world_and_executes_melt():
    from genshin_app.frozen_selfcheck_inputs import verify_fixed_world
    receipt = verify_fixed_world()
    assert receipt["coordinator"] == "ElementalWorldRuntime"
    assert receipt["world_time_tick"] == 1_200_000
    assert len(receipt["applications"]) == 2


def test_source_node_isolation_and_fixed_provider(tmp_path):
    node = os.environ.get("GENSHIN_NATIVE_NODE")
    if not node:
        pytest.skip("requires explicit pinned Node for this source-only probe")
    repository = Path(__file__).resolve().parents[1]
    completed = subprocess.run([node, str(repository / "agent-runtime/src/native-dsh-selfcheck-probe.mjs"),
                                str(tmp_path / "sandbox"), str(repository)], capture_output=True, text=True, encoding="utf-8", check=True)
    receipt = json.loads(completed.stdout)
    assert receipt["status"] == "verified_source_probe"
    assert receipt["actual_frozen_tested"] is False
    assert not receipt["counters"]["normal"]


@pytest.mark.skipif(os.name != "nt" or os.environ.get("GENSHIN_NATIVE_DSH_MUX_OS_SMOKE") != "1",
                    reason="explicit real private-pipe source qualification; no GUI or frozen claim")
def test_fixed_offline_case_uses_real_dsh_gui_engine_and_files(tmp_path, db_path, monkeypatch):
    from genshin_app.agent_chat.dsh_gui_workflow_runner import run_gui_agent_workflow
    from genshin_app.frozen_selfcheck_runtime import (
        engineering_credentials,
        offline_production_provider,
    )
    from genshin_app.gui.api import GuiApi
    from genshin_app.whiteboard import save_whiteboard
    with sqlite3.connect(db_path) as connection:
        connection.row_factory = sqlite3.Row
        row = dict(connection.execute("SELECT * FROM character LIMIT 1").fetchone())
        row.update(id=90000002, name="第二工程身份")
        connection.execute("INSERT INTO character (" + ",".join(row) + ") VALUES (" + ",".join("?" for _ in row) + ")", tuple(row.values()))
    board, calls = build_fixed_inputs(tmp_path, db_path)
    source = tmp_path / "input.whiteboard.json"
    save_whiteboard(source, board)
    (tmp_path / "fixed-calls.json").write_text(json.dumps(calls), encoding="utf-8")
    repository = Path(__file__).resolve().parents[1]
    temporary = tmp_path / "temp"
    temporary.mkdir()
    monkeypatch.setenv("TEMP", str(temporary))
    monkeypatch.setenv("TMP", str(temporary))
    credentials = engineering_credentials()
    def factory(data_dir, **options):
        return GuiApi(data_dir, database_path=db_path, dsh_credentials=credentials, **options)
    factory.dsh_projection_provider_mode = "deterministic_provider_transport"
    with offline_production_provider(root=tmp_path, data_dir=tmp_path / "data",
            runtime_root=repository / "agent-runtime", assets_root=repository / "frontend", package_root=repository) as production:
        receipt = run_gui_agent_workflow(data_dir=tmp_path / "data", prompt="执行固定离线工程输入并保存。",
            input_path=source, output_dir=tmp_path / "flow", provider_mode="deterministic_provider_transport", api_factory=factory)
    assert production["applications"][0]["release_evidence"]["verification_mode"] == "release"
    assert production["applications"][0]["shutdown"] == {"state": "drained_and_stopped"}
    assert production["applications"][0]["process"]["exit_code"] == 0
    assert receipt["status"] == "verified", receipt
    assert receipt["receipt_publication"] == {"status": "published", "path": str(tmp_path / "flow/gui-workflow-receipt.json")}
    assert json.loads(Path(receipt["receipt_publication"]["path"]).read_text(encoding="utf-8")) == receipt
    assert receipt["candidate"]["calculation_acceptance"]["status"] == "accepted"
    calculated = json.loads(Path(receipt["calculated"]["path"]).read_text(encoding="utf-8"))["calculated"]
    from genshin_app.frozen_selfcheck_calculation import (
        verify_flow_budget,
        verify_flow_damage_explanations,
    )
    assert verify_flow_budget(calculated)["requested"] == {"frames": 2048}
    explanation = verify_flow_damage_explanations(calculated)
    assert explanation["all_hit_references_closed"] is True
    assert explanation["ordinary_scaling"] == [100] * 5
    assert receipt["app_tool_calls"]["gs_adopt_attributes"] == 1
    assert calculated["result"]["summary"]["total_damage"] == pytest.approx(EXPECTED_DAMAGE)
    assert len(calculated["result"]["hits"]) == 5
    assert receipt["app_tool_calls"]["gs_read_context"] == 6
    assert receipt["app_tool_calls"]["gs_get_character_appendix_index"] == 1
    assert receipt["app_tool_calls"]["gs_patch_whiteboard"] == 1
    assert sum(receipt["app_tool_calls"].values()) == 13
    from genshin_app.agent_chat.native_app_call_ledger import NativeAppCallLedger
    from genshin_app.frozen_selfcheck_calculation import verify_appendix_index_response
    entries = NativeAppCallLedger(tmp_path / "data/native-dsh-workspaces" / receipt["conversation_id"],
        conversation_id=receipt["conversation_id"]).read()["entries"]
    navigation = [entry for entry in entries.values()
                  if entry["identity"]["tool_name"] == "gs_get_character_appendix_index"]
    assert len(navigation) == 1 and navigation[0]["state"] == "settled_success"
    expected_id = calls[0]["arguments"]["operations"][0]["value"][0]["db_character_id"]
    assert verify_appendix_index_response(json.loads(navigation[0]["result_json"]), expected_id)["status"] == "verified"
    isolation = json.loads((tmp_path / "node-isolation.json").read_text(encoding="utf-8"))
    assert not isolation["counters"]["normal"]
    assert isolation["process_exit_observed"] is True
