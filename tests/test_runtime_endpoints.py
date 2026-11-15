"""Read-only runtime public transport, including legacy DB behavior."""
from __future__ import annotations

import json

import pytest

pytestmark = pytest.mark.usefixtures("isolated_cli_database_admission")

from test_service import _get, _post, server  # noqa: F401


def test_runtime_query_is_read_only_without_write_token(server):  # noqa: F811
    status, output = _post(server, "/v1/runtime/query", {"action": "status"})
    assert status == 200 and output["data"]["state"] == "missing"
    assert _get(server, "/v1/runtime/status")[1]["data"] == output["data"]
    status, output = _post(server, "/v1/runtime/query", {"action": "assemble", "affix_id": 2150071})
    assert status == 409 and output["reason"] == "dataset_unavailable"
    assert _post(server, "/v1/runtime/query", {"action": "status", "sql": "select *"})[0] == 400
    assert _post(server, "/v1/runtime/query", {"action": "assemble", "affix_id": True})[0] == 400
    assert _get(server, "/v1/runtime/status?version_line=release&unknown=1")[0] == 400
    assert _post(server, "/v1/runtime/fragment", {"action": "status"})[0] == 400
    caps = _get(server, "/v1/meta/capabilities")[1]["data"]
    assert "assemble" in caps["runtime"]["actions"]
    assert any(e["path"] == "/v1/runtime/query" and e["auth"] == "none" for e in caps["endpoints"])


def test_runtime_cli_saves_result_and_preserves_input(tmp_path, capsys):
    from genshin_app.cli import main
    request = tmp_path / "request.json"
    request.write_text('{"action":"status"}', encoding="utf-8")
    output = tmp_path / "result.json"
    assert main(["--data-dir", str(tmp_path), "db", "runtime", "--input", str(request), "--output", str(output)]) == 0
    assert json.loads(capsys.readouterr().out)["data"]["state"] == "missing"
    assert json.loads(output.read_text(encoding="utf-8"))["state"] == "missing"
    assert main(["--data-dir", str(tmp_path), "db", "runtime", "--input", str(request), "--output", str(request), "--overwrite"]) == 1
    assert request.read_text(encoding="utf-8") == '{"action":"status"}'
