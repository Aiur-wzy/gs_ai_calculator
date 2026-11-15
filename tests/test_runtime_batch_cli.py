"""CLI and HTTP delivery keep batch status and actual byte limits explicit."""
import json
import threading
import urllib.error
import urllib.request

import pytest

pytestmark = pytest.mark.usefixtures("isolated_cli_database_admission")

from genshin_app.cli import main
from genshin_db import runtime_queries, service
from genshin_db.runtime_batch import BATCH_LIMITS
from tests.test_runtime_attenuation import (  # noqa: F401
    attenuation_db,
    fake_repo,
    stub_fetcher,
)

# ruff: noqa: F811

def write_request(path, groups):
    data = {"action": "batch", "items": [
        {"id": f"item-{index}", "request": {"action": "attenuation_profile", "group": group}}
        for index, group in enumerate(groups)]}
    path.write_text(json.dumps(data), encoding="utf8")
    return data


def test_summary_and_per_item_files_preserve_pending_without_raw_echo(attenuation_db, tmp_path, capsys):
    source, directory = tmp_path / "request.json", tmp_path / "facts"
    write_request(source, ["default", "nonbinary"])
    assert main(["--data-dir", str(tmp_path), "db", "runtime", "--input", str(source),
                 "--output-dir", str(directory), "--summary"]) == 0
    captured = capsys.readouterr()
    output = json.loads(captured.out)["data"]
    assert not captured.err and output["status"] == "complete"
    assert output["items"][1]["result"]["execution"]["status"] == "pending"
    assert '"raw_record"' not in captured.out and '"durability_sequence"' not in captured.out
    assert "index" not in output["files"]
    manifest = json.loads((directory / "index.json").read_text("utf8"))
    assert len(manifest["items"]) == 2


@pytest.mark.parametrize("mode", ["file", "directory"])
def test_partial_has_nonzero_exit_but_complete_delivered_data(attenuation_db, tmp_path, capsys, mode):
    source, target = tmp_path / "request.json", tmp_path / ("batch.json" if mode == "file" else "facts")
    write_request(source, ["default", "missing", "nonbinary"])
    flag = "--output" if mode == "file" else "--output-dir"
    assert main(["--data-dir", str(tmp_path), "db", "runtime", "--input", str(source), flag,
                 str(target), "--summary"]) == 1
    captured = capsys.readouterr()
    error = json.loads(captured.err)
    assert not captured.out and error["reason"] == "batch_partial"
    assert error["details"]["summary"]["counts"] == {"requested": 3, "ok": 2, "error": 1, "skipped": 0}
    delivered = json.loads((target if mode == "file" else target / "index.json").read_text("utf8"))
    assert delivered["status"] == "partial" and len(delivered["items"]) == 3


@pytest.mark.parametrize("mode", ["file", "directory"])
def test_existing_output_rejected_before_expensive_query(tmp_path, capsys, monkeypatch, mode):
    source, target = tmp_path / "request.json", tmp_path / ("batch.json" if mode == "file" else "facts")
    write_request(source, ["default"])
    existing = target if mode == "file" else target / "index.json"
    existing.parent.mkdir(parents=True, exist_ok=True)
    existing.write_text("user content", encoding="utf8")

    def forbidden(*args, **kwargs):
        pytest.fail("existing output detected only after query")
    monkeypatch.setattr(runtime_queries, "query_runtime_public", forbidden)
    assert main(["--data-dir", str(tmp_path), "db", "runtime", "--input", str(source),
                 "--output" if mode == "file" else "--output-dir", str(target)]) == 1
    assert json.loads(capsys.readouterr().err)["reason"] == "file_conflict"
    assert existing.read_text("utf8") == "user content"


def test_single_pretty_output_checks_actual_serialized_bytes_before_publication(tmp_path, capsys, monkeypatch):
    source, target = tmp_path / "request.json", tmp_path / "batch.json"
    write_request(source, ["default"])
    monkeypatch.setattr(runtime_queries, "query_runtime_public", lambda *a, **k: {"status": "complete", "nested": [["x"]] * 1000})
    monkeypatch.setitem(BATCH_LIMITS, "delivery_bytes", 10000)
    assert main(["--data-dir", str(tmp_path), "db", "runtime", "--input", str(source), "--output", str(target)]) == 1
    assert json.loads(capsys.readouterr().err)["reason"] == "response_too_large"
    assert not target.exists()


def test_http_batch_checks_actual_transport_envelope(attenuation_db, monkeypatch):
    server = service.GenshinHTTPServer(attenuation_db.parent, port=0)
    worker = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": .01}, daemon=True)
    worker.start()
    try:
        monkeypatch.setattr(service, "query_runtime_public", lambda *a, **k: {"payload": ["字"] * 1000})
        monkeypatch.setitem(BATCH_LIMITS, "delivery_bytes", 5000)
        request = urllib.request.Request(server.base_url + "/v1/runtime/query", data=json.dumps({
            "action": "batch", "items": [{"id": "a", "request": {"action": "attenuation_profile", "group": "default"}}]
        }).encode(), method="POST")
        with pytest.raises(urllib.error.HTTPError) as exc:
            urllib.request.urlopen(request, timeout=3)
        with exc.value as response:
            error = json.loads(response.read())
        assert response.status == 413 and error["reason"] == "response_too_large"
    finally:
        server.shutdown()
        worker.join(3)
        server.server_close()
