"""CLI→real GuiApi→private pipe→DSH→review→save→fresh App reload."""
from __future__ import annotations

import json
import os
import uuid
from pathlib import Path

import pytest

from genshin_app import cli
from genshin_app.agent_chat.artifact_candidate import ArtifactCandidateSession
from genshin_app.agent_chat.native_dsh_host_requests import NativeDshHostRequests
from genshin_app.agent_chat.native_dsh_mux_process import NativeDshMuxProcess
from genshin_app.agent_chat.native_dsh_shell_host import NativeDshShellHost
from genshin_app.agent_chat.native_dsh_workspace import NativeDshWorkspaceRegistry
from genshin_app.equipment.files import fingerprint
from genshin_app.gui.api import GuiApi
from genshin_app.whiteboard import default_panel, new_whiteboard, save_whiteboard
from tests.dsh_fixture_environment import isolated_runtime_environment
from tests.test_event_topology_binding import _binding, _topology
from tests.test_queries import db_path as db_path  # noqa: PLC0414

ROOT = Path(__file__).resolve().parents[1]
pytestmark = pytest.mark.skipif(os.name != "nt" or os.environ.get("GENSHIN_NATIVE_DSH_MUX_OS_SMOKE") != "1",
    reason="explicit CLI/GuiApi real private-pipe qualification without a GUI window")


@pytest.mark.parametrize(("calculated", "partial_native", "parallel_reads", "receipt_failure"),
                         [(False, False, False, False), (True, False, False, False), (False, True, False, False),
                          (False, False, True, False), (True, False, False, True)],
                         ids=["draft", "formal_calculation", "unsealed_native_input_draft", "parallel_reads", "formal_receipt_failure"])
def test_public_controller_edit_review_save_and_fresh_reload_without_screen(tmp_path, db_path, calculated, partial_native, parallel_reads, receipt_failure, monkeypatch, capsys):
    processes = []
    script = {"accounting": True, "calls": [{"tool": "gs_patch_whiteboard", "arguments": {
        "expected_revision": 0, "reason": "GUI投影文档闭环", "operations": [
            {"op": "replace", "path": "/title", "value": "新DSH链保存后能加载"}]}}]}
    if parallel_reads:
        script["calls"] = [[
            {"tool": "gs_read_context", "arguments": {"resource": "capability_index"}},
            {"tool": "gs_read_context", "arguments": {"resource": "tool_contracts", "pointer": "/tools/gs_weapon_stats"}},
        ]]
    input_path = None
    if calculated or partial_native:
        board, script["calls"] = calculation_fixture(tmp_path, db_path)
        if partial_native:
            script["calls"] = script["calls"][:1]
        input_path = tmp_path / "input.whiteboard.json"
        save_whiteboard(input_path, board)

    def factory(data_dir, **options):
        registry = NativeDshWorkspaceRegistry(data_dir / "workspaces", database_path=db_path)

        def start(_):
            profile, mirror = data_dir / "dsh-profile", data_dir / "mirror"
            profile.mkdir(exist_ok=True)
            mirror.mkdir(exist_ok=True)
            process = NativeDshMuxProcess(node=Path(os.environ["GENSHIN_NATIVE_NODE"]),
                entry=ROOT / "agent-runtime/src/native-dsh-domain-pipe-fixture.mjs", working=ROOT,
                runtime_environment=isolated_runtime_environment(profile),
                entry_args=(str(profile), str(mirror), json.dumps(script)),
                domain_handler=NativeDshHostRequests(registry, data_dir=data_dir))
            processes.append(process)
            return NativeDshShellHost(process.client, boot=process.client.boot, bundle=process.client.bundle)

        return GuiApi(data_dir, database_path=db_path, dsh_shell_factory=start, dsh_workspace_registry=registry, **options)

    factory.dsh_projection_provider_mode = "deterministic_provider_transport"
    monkeypatch.setattr("genshin_app.gui.api.GuiApi", factory)
    # This tiny engineering DB has separate component tests, not a published
    # App/DB release identity. This fixture seam does not disable product gates.
    monkeypatch.setattr(cli, "_require_runtime_database", lambda _: {"state": "engineering_fixture"})
    prompt_path = tmp_path / "task.txt"
    prompt_path.write_text("请完成用户工程动作的计算与保存。" if calculated else "请修改标题并保存草稿。", encoding="utf-8")
    if receipt_failure:
        from genshin_app import output_policy
        from genshin_app.equipment.schema import PlanError
        ordinary = output_policy.publish_ordinary_files
        def fail_only_terminal_report(outputs, database, **options):
            if any(Path(path).name == "gui-workflow-receipt.json" for path, _content in outputs):
                raise PlanError("受控模拟最终报告发布失败", "file_conflict", {
                    "published_paths": [], "pending_paths": [str(tmp_path / "result/gui-workflow-receipt.json")],
                    "unknown_paths": []})
            return ordinary(outputs, database, **options)
        monkeypatch.setattr(output_policy, "publish_ordinary_files", fail_only_terminal_report)
    try:
        arguments = ["--data-dir", str(tmp_path), "agent", "gui-workflow", "--prompt-file", str(prompt_path),
            "--output-dir", str(tmp_path / "result"), "--provider-mode", "deterministic_provider_transport"]
        if input_path:
            arguments.extend(["--input", str(input_path)])
        if not calculated:
            arguments.append("--allow-draft")
        assert cli.main(arguments) == 0
        result = json.loads(capsys.readouterr().out)["data"]
        assert result["status"] == ("verified" if calculated else "draft_verified"), result
        if receipt_failure:
            assert result["receipt_publication"]["status"] == "failed"
            assert result["receipt_publication"]["reason"] == "file_conflict"
            assert result["receipt_publication"]["details"]["published_paths"] == []
            assert not (tmp_path / "result/gui-workflow-receipt.json").exists()
        else:
            assert result["receipt_publication"]["status"] == "published"
            assert json.loads(Path(result["receipt_publication"]["path"]).read_text(encoding="utf-8")) == result
        assert result["reload_verified"] and not result["real_gui_tested"]
        assert "gs_read_skill" in result["request_tool_summaries"][0]["tools"]
        assert result["app_tool_calls"]
        if partial_native or parallel_reads:
            saved = json.loads(Path(result["input_draft"]["path"]).read_text(encoding="utf-8"))
            assert saved["input"]["candidate"]["representation"] == ("native" if partial_native else "whiteboard")
            if parallel_reads:
                assert result["app_tool_calls"]["gs_read_context"] == 2
            assert result["formal_applied"] is False and result["calculation_verified"] is False
            assert not (tmp_path / "result/result.whiteboard.json").exists()
        else:
            board = json.loads(Path(result["board"]["path"]).read_text(encoding="utf-8"))
            assert board["title"] == ("原生DSH正式计算链" if calculated else "新DSH链保存后能加载")
            assert result["production_frontend_document_tested"] is True
            assert result["document_application_scope"] == "production_frontend_document_application"
        if calculated:
            package = json.loads(Path(result["calculated"]["path"]).read_text(encoding="utf-8"))
            assert package["calculated"]["result"]["summary"]["total_damage"] > 0
            saved_result = package["calculated"]["result"]
            assert saved_result["damage_report_window"]["status"] == "complete"
            assert saved_result["damage_report_window"]["end"] == 1.2
            assert saved_result["damage_report_window"]["anchor"]["hit_id"] == "h"
            # The saved package and fresh App reload must preserve the full
            # execution audit while reporting only through the direct hit.
            assert saved_result["buff_state_timeline"]["duration"] == 2
            assert saved_result["damage_report_execution"]["rotation_model"]["period"] == 2
            explanation = saved_result["damage_explanations"]
            assert explanation["contract"] == "damage-explanation-v1"
            assert explanation["status"] == "complete"
            assert saved_result["damage_explanation_fingerprint"]
            assert all(hit["explanation_ref"] in explanation["events"] for hit in saved_result["hits"])
        assert result["assistant_reply"]
        assert len(processes) == 1  # fresh App file reload never starts another model
        assert not list(tmp_path.rglob("native-session-v2.json"))
    finally:
        for process in processes:
            process.close()


def calculation_fixture(tmp_path, db_path):
    """Only test inputs are scripted; the real App must calculate/publish."""
    board = new_whiteboard()
    board.update(title="原生DSH正式计算链", duration=2)
    panel = default_panel()
    panel.update(hp=12000, atk=100, crit_rate=0.5, crit_dmg=0.5, em=0, energy_recharge=1.8)
    panel["def"] = 500
    board["team"] = [{"id": "a", "db_character_id": 90000001, "name": "工程角色", "level": 90,
        "constellation": 0, "weapon": None, "artifact_sets": [], "panel_basis_mode": "manual",
        "panel": panel, "notes": "用户给定工程面板"}]
    board["actions"] = [{"id": "e", "character": "a", "action_type_id": "action.elemental_skill",
        "label": "工程战技", "start": 1, "variant": "tap", "notes": "用户给定动作"}]
    dry = ArtifactCandidateSession(uuid.uuid4().hex, tmp_path / "fixture-input-validation", db_path,
        "whiteboard", board)
    prepare = {"tool": "gs_transition_phase", "arguments": {"target_phase": "numeric_model",
        "expected_revision": 0, "expected_stage_revision": 0, "reason": "准备用户动作的计算输入",
        "prepare_calculation": True, "energy_intent": "single_window_legal"}}
    assert dry.call_tool(prepare["tool"], prepare["arguments"])["code"] == 0
    hit = {"id": "h", "t": 1.2, "character": "a", "skill": "工程战技", "hit_name": "工程命中",
        "damage_type_ids": ["damage.elemental_skill"], "damage_system": "classic", "element": "physical", "multiplier": 1}
    topology = _topology()
    topology["dependencies"] = [{"dependency_id": "database_snapshot", "kind": "database_snapshot",
        "fingerprint": fingerprint(dry.database_snapshot), "required": True}]
    topology["action_checkpoint"] = dry._current_action_checkpoint()
    topology["sources"][0]["owner_ref"] = "a"
    for node in topology["nodes"]:
        if node["schedule"]["anchor"]["kind"] == "action":
            node["schedule"]["anchor"]["ref_id"] = "e"
    binding_plan = {"manual_template": {"hits": [hit]}}
    bindings = _binding(topology, plan=binding_plan)
    patch = {"tool": "gs_patch_candidate", "arguments": {"expected_revision": 1,
        "reason": "填入明确工程输入并绑定动作与伤害", "operations": [
            {"op": "replace", "path": "/manual_template/hits", "value": [hit]},
            # This hit settles the fixture's single user skill during its cast.
            # New whiteboards request last-active-damage reporting, so the
            # author must identify that real direct settlement explicitly.
            {"op": "add", "path": "/rotation_model/damage_report/active_hit_ids", "value": ["h"]},
            {"op": "add", "path": "/rotation_model/initial_frontline", "value": "a"},
            {"op": "replace", "path": "/rotation_model/characters/0/element", "value": "geo"},
            {"op": "replace", "path": "/rotation_model/characters/0/energy_capacity", "value": 60},
            {"op": "replace", "path": "/rotation_model/characters/0/initial_energy", "value": 60},
            {"op": "replace", "path": "/rotation_model/characters/0/status", "value": "assumed"},
            {"op": "replace", "path": "/rotation_model/characters/0/source", "value": "用户工程输入"},
            {"op": "replace", "path": "/rotation_model/abilities/0/status", "value": "assumed"},
            {"op": "replace", "path": "/rotation_model/abilities/0/source", "value": "用户工程输入"},
            {"op": "replace", "path": "/rotation_model/actions/0/occupation_end", "value": 1.3},
            {"op": "replace", "path": "/rotation_model/actions/0/switch_allowed_at", "value": 1.3},
            {"op": "replace", "path": "/rotation_model/actions/0/status", "value": "assumed"},
            {"op": "replace", "path": "/rotation_model/actions/0/source", "value": "用户工程输入"},
            {"op": "replace", "path": "/mechanics_model", "value": {"schema_version": 1, "rules": [],
                "hit_events": [{"hit_id": "h", "action_id": "e", "order": 10}], "hit_patterns": [],
                "condition_events": [], "initial_state": {"rules": []}}},
            {"op": "replace", "path": "/extensions/event_topology", "value": topology},
            {"op": "replace", "path": "/extensions/event_topology_bindings", "value": bindings},
        ]}}
    assert dry.call_tool(patch["tool"], patch["arguments"])["code"] == 0
    refresh = {"tool": "gs_refresh_data_refs", "arguments": {"expected_revision": 2, "reason": "同步当前数据库引用"}}
    refreshed = dry.call_tool(refresh["tool"], refresh["arguments"])
    assert refreshed["code"] == 0, refreshed
    # Refreshing already-current data is idempotent; use the resulting
    # candidate revision rather than assume every tool call writes a revision.
    calculation_revision = dry.revision
    computed = dry.call_tool("gs_calculate", {"expected_revision": calculation_revision})
    assert computed["code"] == 0, (computed, dry.topology_receipt(),
        (dry.calculated or {}).get("result", {}).get("calculation_coverage"))
    return board, [prepare, patch, refresh,
                   {"tool": "gs_calculate", "arguments": {"expected_revision": calculation_revision}}]
