"""Actual release factory/entry/App lifecycle; only provider transport is offline."""
from __future__ import annotations

import json
import os
import sqlite3
from pathlib import Path

import pytest

from genshin_app.agent_chat.dsh_gui_workflow_runner import run_gui_agent_workflow
from genshin_app.frozen_selfcheck_inputs import EXPECTED_DAMAGE, build_fixed_inputs
from genshin_app.frozen_selfcheck_runtime import (
    engineering_credentials,
    offline_production_provider,
    verify_fixed_tool_settlements,
)
from genshin_app.gui.api import GuiApi
from genshin_app.whiteboard import save_whiteboard
from tests.test_queries import db_path as db_path  # noqa: PLC0414

ROOT = Path(__file__).resolve().parents[1]
pytestmark = pytest.mark.skipif(os.name != "nt" or os.environ.get("GENSHIN_NATIVE_DSH_MUX_OS_SMOKE") != "1",
    reason="explicit actual release factory private-pipe qualification; no GUI window")


def test_actual_release_factory_completes_gui_calculation_review_save_fresh_and_shutdown(tmp_path, db_path, monkeypatch):
    from genshin_app.agent_chat import native_dsh_production as production
    from genshin_app.agent_chat.native_dsh_application import NativeDshApplication
    from genshin_app.version import NATIVE_AGENT_DSH_SHELL_MANIFEST_SHA256

    temporary = tmp_path / "temp"
    temporary.mkdir()
    monkeypatch.setenv("TEMP", str(temporary))
    monkeypatch.setenv("TMP", str(temporary))
    with sqlite3.connect(db_path) as connection:
        connection.row_factory = sqlite3.Row
        row = dict(connection.execute("SELECT * FROM character LIMIT 1").fetchone())
        row.update(id=90000002, name="第二工程身份")
        connection.execute("INSERT INTO character (" + ",".join(row) + ") VALUES (" + ",".join("?" for _ in row) + ")", tuple(row.values()))
    board, calls = build_fixed_inputs(tmp_path, db_path)
    source = tmp_path / "input.whiteboard.json"
    save_whiteboard(source, board)
    (tmp_path / "fixed-calls.json").write_text(json.dumps(calls), encoding="utf-8")
    applications = []
    real_factory = production.create_native_dsh_application

    def observe_factory(*args, **kwargs):
        assert kwargs.get("verification_mode", "release") == "release"
        assert kwargs["expected_manifest_sha256"] == NATIVE_AGENT_DSH_SHELL_MANIFEST_SHA256
        application = real_factory(*args, **kwargs)
        assert type(application) is NativeDshApplication
        applications.append(application)
        return application

    monkeypatch.setattr(production, "create_native_dsh_application", observe_factory)
    from genshin_app.gui import frontend_document_projection
    real_projection = frontend_document_projection.apply_frontend_document

    def observe_document_projection(**kwargs):
        value = real_projection(**kwargs)
        (tmp_path / "frontend-document-application.json").write_text(
            json.dumps({"request": kwargs, "response": value}, ensure_ascii=False),
            encoding="utf-8",
        )
        return value

    monkeypatch.setattr(frontend_document_projection, "apply_frontend_document", observe_document_projection)
    monkeypatch.setattr(production, "default_credential_store", lambda: pytest.fail("Windows credentials"))
    credentials = engineering_credentials()

    def api_factory(data_dir, **options):
        return GuiApi(data_dir, database_path=db_path, dsh_credentials=credentials, **options)

    api_factory.dsh_projection_provider_mode = "deterministic_provider_transport"
    data = tmp_path / "data"
    with offline_production_provider(root=tmp_path, data_dir=data, runtime_root=ROOT / "agent-runtime",
            assets_root=ROOT / "frontend", package_root=ROOT) as launch:
        receipt = run_gui_agent_workflow(data_dir=data, prompt="完成固定工程输入的实际计算、审核、保存。",
            output_dir=tmp_path / "flow", input_path=source,
            provider_mode="deterministic_provider_transport", api_factory=api_factory)
    assert receipt["status"] == "verified", receipt
    launch["tool_settlements"] = verify_fixed_tool_settlements(data, receipt["conversation_id"], calls)
    (tmp_path / "production-factory.json").write_text(json.dumps(launch, ensure_ascii=False, indent=2), encoding="utf-8")
    assert receipt["candidate"]["calculation_acceptance"]["status"] == "accepted"
    assert receipt["reload_verified"] and not receipt["real_gui_tested"]
    assert sum(receipt["app_tool_calls"].values()) == len(calls) == 13
    result = json.loads(Path(receipt["calculated"]["path"]).read_text("utf-8"))["calculated"]
    assert result["result"]["summary"]["total_damage"] == pytest.approx(EXPECTED_DAMAGE)
    assert len(applications) == 1
    assert applications[0].release_evidence == {"manifest_sha256": NATIVE_AGENT_DSH_SHELL_MANIFEST_SHA256,
                                               "verification_mode": "release"}
    assert applications[0].shutdown_diagnostic() == {"state": "drained_and_stopped"}
    isolation = json.loads((tmp_path / "node-isolation.json").read_text("utf-8"))
    assert not isolation["counters"]["normal"]
    assert isolation["main_entry"] == str(ROOT / "agent-runtime/src/native-dsh-runtime-entry.mjs")
    assert isolation["provider_requests"] == len(calls) + 1
    assert isolation["process_exit_observed"] is True
    assert launch["applications"][0]["process"]["exit_code"] == 0
    assert len(launch["launches"]) == 1
