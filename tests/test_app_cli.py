"""app CLI 测试：统一包络、退出码、capabilities 单源一致、engine calc、薄转发兼容。"""

import json
from pathlib import Path

import pytest

from genshin_app.cli import CLI_VERSION, COMMANDS, main

EXAMPLE = Path(__file__).resolve().parent.parent / "examples" / "freeze_team.json"


@pytest.fixture(autouse=True)
def _runtime_database_already_admitted(monkeypatch):
    monkeypatch.setattr(
        "genshin_app.cli._require_runtime_database",
        lambda _data_dir: {"can_start": True, "state": "test_ready"},
    )


def run_cli(argv, capsys):
    code = main(argv)
    out = capsys.readouterr()
    return code, out.out, out.err


def test_capabilities_lists_all_commands(capsys):
    code, out, _ = run_cli(["capabilities"], capsys)
    assert code == 0
    payload = json.loads(out)
    assert payload["code"] == 0 and payload["message"] == "ok"
    data = payload["data"]
    assert data["version"] == CLI_VERSION
    names = {c["name"] for c in data["commands"]}
    assert names == {"capabilities", "diagnostics show", "system check-update", "db update", "db status", "db activate-bundled", "db relabel", "db serve", "db localize", "db runtime",
                     "engine calc", "engine check-rotation", "engine hash-input", "gui", "equipment validate", "equipment compose", "db catalog", "db panel-basis",
                     "equipment calc", "equipment export", "equipment edit", "db context", "db appendix", "equipment panel-reference", "equipment workbook", "equipment deliver", "equipment review", "equipment review-batch", "equipment diagnose", "equipment adopt-attributes",
                     "equipment enable-mechanics", "equipment attribute-draft", "equipment assemble-attack", "equipment assemble-periodic",
                     "workflow init", "workflow bind", "workflow export", "workflow workspace", "session", "shortcut create",
                     "db semantics import", "db semantics record", "db semantics review",
                     "whiteboard new", "whiteboard validate", "whiteboard handoff", "whiteboard result",
                     "agent gui-workflow", "agent gui-webview-smoke",
                     "agent budget show", "agent budget set", "agent budget reset",
                     "agent candidate read", "agent candidate edit", "agent candidate compose", "agent candidate calculate",
                     "agent dsh-settings show", "agent dsh-settings set", "agent dsh-settings reset",
                     "equipment topology", "equipment materialization", "research schema", "research list",
                     "research append", "research bind", "research report"}
    # 单源一致：capabilities 命令数与 COMMANDS 定义一致
    assert len(data["commands"]) == len(COMMANDS)
    appendix = data["appendix_query_contract"]
    assert appendix["execution_authority"] == "none"
    assert all("status" in schema["required"] and set(schema["properties"]["status"]["enum"]) == {"approved", "pending"}
               for schema in appendix["request_schema"]["oneOf"])


def test_gui_workflow_example_reaches_handler_with_production_defaults(
    monkeypatch,
    capsys,
    tmp_path,
):
    """The documented no-effort example must survive parser and handler.

    This intentionally exercises ``main`` rather than inspecting the command
    table. Omission must reach the runner as None: it selects high for a new
    session and preserves the existing selection when resuming a session.
    """

    prompt = tmp_path / "task.txt"
    prompt.write_text("建立一份白板草稿", encoding="utf-8")
    published = tmp_path / "published"
    captured = {}

    def fake_workflow(**kwargs):
        captured.update(kwargs)
        return {
            "contract": "gui-native-agent-workflow-receipt-v1",
            "status": "draft_verified",
            "reload_verified": True,
        }

    monkeypatch.setattr(
        "genshin_app.agent_chat.dsh_gui_workflow_runner.run_gui_agent_workflow",
        fake_workflow,
    )
    code, out, err = run_cli(
        [
            "--data-dir",
            str(tmp_path / "profile"),
            "agent",
            "gui-workflow",
            "--prompt-file",
            str(prompt),
            "--output-dir",
            str(published),
        ],
        capsys,
    )

    assert code == 0 and err == ""
    assert json.loads(out)["data"]["status"] == "draft_verified"
    assert captured["effort"] is None
    assert captured["workflow"] == "artifact"
    assert captured["timeout_seconds"] == 1800
    assert captured["data_dir"] == str(tmp_path / "profile")
    assert captured["output_dir"] == str(published)


def test_gui_webview_smoke_parser_passes_semantic_expectation_to_handler(
    monkeypatch,
    capsys,
    tmp_path,
):
    prompt = tmp_path / "task.txt"
    prompt.write_text("完成指定角色的一次正式计算", encoding="utf-8")
    expectation = tmp_path / "expectation.json"
    expectation.write_text(
        json.dumps(
            {
                "required_character_ids": [10000133],
                "min_actions": 1,
                "min_revision_increment": 1,
                "require_calculated": True,
                "min_result_hits": 1,
            }
        ),
        encoding="utf-8",
    )
    captured = {}

    def fake_webview(**kwargs):
        captured.update(kwargs)
        return {
            "contract": "gui-native-agent-webview-smoke-receipt-v1",
            "status": "verified",
        }

    monkeypatch.setattr(
        "genshin_app.gui.webview_smoke.run_gui_webview_smoke",
        fake_webview,
    )
    code, out, err = run_cli(
        [
            "--data-dir",
            str(tmp_path / "paired-profile"),
            "agent",
            "gui-webview-smoke",
            "--prompt-file",
            str(prompt),
            "--output-dir",
            str(tmp_path / "published"),
            "--expectation-file",
            str(expectation),
        ],
        capsys,
    )

    assert code == 0 and err == ""
    assert json.loads(out)["data"]["status"] == "verified"
    assert captured["effort"] == "high"
    assert captured["expectation"] == {
        "required_character_ids": [10000133],
        "min_actions": 1,
        "min_revision_increment": 1,
        "require_calculated": True,
        "min_result_hits": 1,
    }


def test_envelope_error_on_missing_db(capsys, tmp_path):
    code, _, err = run_cli(["--data-dir", str(tmp_path), "db", "status"], capsys)
    assert code == 1
    payload = json.loads(err)
    assert payload["code"] == 1 and "数据库不存在" in payload["message"]


def test_engine_calc_stdout_full(capsys):
    code, out, _ = run_cli(["engine", "calc", str(EXAMPLE)], capsys)
    assert code == 0
    data = json.loads(out)["data"]
    assert data["summary"]["total_damage"] > 0
    assert len(data["hits"]) == 4


def test_engine_calc_output_file(capsys, tmp_path):
    out_file = tmp_path / "result.json"
    code, out, _ = run_cli(
        ["engine", "calc", str(EXAMPLE), "--output", str(out_file)], capsys)
    assert code == 0
    data = json.loads(out)["data"]
    assert data["output"] == str(out_file) and data["summary"]["total_damage"] > 0
    full = json.loads(out_file.read_text(encoding="utf-8"))
    assert len(full["hits"]) == 4  # 文件内是全量结果


def test_engine_calc_with_patch(capsys, tmp_path):
    patch_file = tmp_path / "p.json"
    patch_file.write_text(json.dumps(
        [{"op": "replace", "path": "/characters/0/stats/atk", "value": 2200}]),
        encoding="utf-8")
    code, out, _ = run_cli(
        ["engine", "calc", str(EXAMPLE), "--patch", str(patch_file)], capsys)
    assert code == 0
    data = json.loads(out)["data"]
    assert data["hits"][1]["zones"]["base"] == pytest.approx(2.02 * (2200 + 900 * 0.4 + 800))


def test_engine_calc_invalid_template(capsys, tmp_path):
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps({"version": 1, "form": "unresolved"}), encoding="utf-8")
    code, _, err = run_cli(["engine", "calc", str(bad)], capsys)
    assert code == 1
    assert "resolved" in json.loads(err)["message"]


def test_engine_calc_missing_file(capsys, tmp_path):
    code, _, err = run_cli(["engine", "calc", str(tmp_path / "none.json")], capsys)
    assert code == 1 and json.loads(err)["reason"] == "input_file_not_found"
    assert json.loads(err)["recovery"]["action"] == "select_input_file"


def test_pretty_flag(capsys):
    code, out, _ = run_cli(["--pretty", "capabilities"], capsys)
    assert code == 0 and "\n  " in out  # 缩进 JSON


def test_legacy_entry_forward(capsys):
    from genshin_db.__main__ import main as legacy_main

    assert legacy_main(["capabilities"]) == 0
    data = json.loads(capsys.readouterr().out)["data"]
    assert data["cli"] == "genshin_app"


@pytest.mark.parametrize("argv", [
    [], ["not-a-command"], ["db"], ["engine", "calc"],
    ["capabilities", "--unknown"], ["db", "serve", "--port", "abc"],
])
def test_usage_errors_are_json(argv, capsys):
    code, out, err = run_cli(argv, capsys)
    assert code == 1
    assert out == ""
    payload = json.loads(err)
    assert payload["code"] == 1 and payload["message"]


def test_pretty_usage_error(capsys):
    code, out, err = run_cli(["--pretty", "engine", "calc"], capsys)
    assert code == 1 and not out
    assert "\n  " in err and json.loads(err)["code"] == 1


def test_help_remains_readable(capsys):
    with pytest.raises(SystemExit) as exc:
        main(["--help"])
    assert exc.value.code == 0
    captured = capsys.readouterr()
    assert "usage:" in captured.out and not captured.err


def test_serve_rejection_is_failure(monkeypatch, capsys, tmp_path):
    monkeypatch.setattr("genshin_db.service.serve", lambda *args, **kwargs: 1)
    code, out, err = run_cli(["--data-dir", str(tmp_path), "db", "serve"], capsys)
    assert code == 1 and not out
    assert "已有实例" in json.loads(err)["message"]
