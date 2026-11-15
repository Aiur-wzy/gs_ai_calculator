"""Public file ports expose unknown formats only as inert, bounded metadata."""
from __future__ import annotations

import hashlib
import io
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from genshin_app.agent_chat.candidate_input_draft import KIND, MAX_BYTES, decode_draft
from genshin_app.equipment.schema import PlanError
from genshin_app.gui.api import GuiApi


def future_file(tmp_path):
    # Deliberately not V1's shape: a future format's business input is unknown.
    value = {"kind": KIND, "schema_version": 2,
        "artifact_id": "a6b12545-0873-4c1f-abdb-1849ef9bcc6a", "created_at": "2026-11-06T00:00:00+00:00",
        "producer": {"app_version": "0.99.0", "app_release_id": "future-release", "api_key": "PRIVATE_SENTINEL"},
        "input": {"unknown_future_shape": {"api_key": "PRIVATE_SENTINEL", "accepted": True,
            "payload": "BUSINESS_PAYLOAD_SENTINEL", "writer_fence": "CONTROL_SENTINEL"}},
        "future_extension": {"raw_reasoning": "REASONING_SENTINEL"}}
    path = tmp_path / "future.gencdraft.json"
    raw = json.dumps(value, ensure_ascii=False).encode("utf-8")
    path.write_bytes(raw)
    return path, raw


@pytest.mark.parametrize("entry", ["picker", "selected"])
def test_public_future_draft_is_metadata_only_without_candidate_or_model(tmp_path, entry):
    path, raw = future_file(tmp_path)
    api = GuiApi(tmp_path / "profile", dsh_file_dialog=lambda _: str(path))
    if entry == "picker":
        response = json.loads(api.document_json_call("dsh_client_open_candidate_input_draft",
            json.dumps([{"dsh_session_id": "future-view"}])))
    else:
        response = json.loads(api.document_json_call("dsh_client_import_candidate_input_draft", json.dumps([
            {"dsh_session_id": "future-view", "path": str(path), "expected_file_sha": hashlib.sha256(raw).hexdigest()}])))
    assert response["code"] == 0
    data = response["data"]
    assert not data.get("imported", False)
    preview = data["preview"]
    assert preview["compatible"] is False and preview["read_only"] is True
    assert preview["metadata_only"] is True and preview["payload_interpreted"] is False
    assert not preview["can_apply"] and not preview["accepted"] and not preview["has_calculation"]
    assert preview["metadata"]["schema_version"] == 2
    assert preview["metadata"]["producer"] == {"app_version": "0.99.0", "app_release_id": "future-release"}
    assert "格式" in preview["message"] and "2" in preview["message"]
    for resource in ("source_document", "candidate", "evidence"):
        page = api.dsh_client_candidate_input_draft_preview_read({"preview_id": data["preview_id"],
            "resource": resource, "pointer": "", "offset": 0, "limit": 20})
        assert page["code"] == 0
        rows = {row["key"]: row["value"] for row in page["data"]["items"]}
        assert rows["payload_interpreted"] is False and rows["authority"] == "none"
        assert "metadata" in rows
        assert "SENTINEL" not in json.dumps(page, ensure_ascii=False)
    with pytest.raises(PlanError):
        api.dsh_client_candidate_input_draft_preview_read({"preview_id": data["preview_id"],
            "resource": "candidate", "pointer": "/input/unknown_future_shape", "offset": 0, "limit": 20})
    assert "SENTINEL" not in json.dumps(response, ensure_ascii=False)
    assert api._get_dsh_workspaces().lookup_binding("future-view") is None
    assert api._dsh_shell is None and api._agent_manager is None
    assert not list((tmp_path / "profile").rglob("candidate-checkpoint.json"))
    assert not list((tmp_path / "profile").rglob("*.db"))
    assert path.read_bytes() == raw
    api._get_dsh_workspaces().close()


def test_future_metadata_is_safe_for_real_gui_json_transport_even_with_unknown_unicode(tmp_path):
    path, raw = future_file(tmp_path)
    value = json.loads(raw)
    value["producer"]["app_version"] = "\ud800"
    value["created_at"] = "\udc00"
    value["input"]["future_text"] = "\ud800"
    raw = json.dumps(value, ensure_ascii=True).encode("utf-8")
    path.write_bytes(raw)
    api = GuiApi(tmp_path / "profile")
    response = json.loads(api.document_json_call("dsh_client_import_candidate_input_draft", json.dumps([
        {"dsh_session_id": "future-view", "path": str(path), "expected_file_sha": hashlib.sha256(raw).hexdigest()}])))
    assert response["code"] == 0
    metadata = response["data"]["preview"]["metadata"]
    assert "created_at" not in metadata
    assert metadata["producer"] == {"app_release_id": "future-release"}
    assert response["data"]["preview"]["payload_interpreted"] is False
    assert api._get_dsh_workspaces().lookup_binding("future-view") is None
    assert api._dsh_shell is None and path.read_bytes() == raw
    api._get_dsh_workspaces().close()


@pytest.mark.parametrize("opaque_name", ["OPAQUE_FUTURE_PAYLOAD_NAME", "\ud800"])
def test_ordinary_inspect_then_selected_import_does_not_interpret_future_business_name(tmp_path, opaque_name):
    path, raw = future_file(tmp_path)
    value = json.loads(raw)
    value["manual_template"] = {"meta": {"name": opaque_name}}
    raw = json.dumps(value, ensure_ascii=True).encode("utf-8")
    path.write_bytes(raw)
    api = GuiApi(tmp_path / "profile")
    # The ordinary kind/SHA probe is the existing lightweight GUI method;
    # the subsequent domain file command uses the lossless document wire.
    inspected = api.inspect_plan_file(str(path))
    assert inspected["code"] == 0
    assert inspected["data"] == {"kind": KIND, "file_sha": hashlib.sha256(raw).hexdigest(), "display_name": None}
    response = json.loads(api.document_json_call("dsh_client_import_candidate_input_draft", json.dumps([
        {"dsh_session_id": "future-view", "path": str(path), "expected_file_sha": inspected["data"]["file_sha"]}])))
    assert response["code"] == 0 and response["data"]["preview"]["metadata_only"]
    assert "OPAQUE_FUTURE_PAYLOAD_NAME" not in json.dumps(response)
    assert api._get_dsh_workspaces().lookup_binding("future-view") is None
    assert api._dsh_shell is None and path.read_bytes() == raw
    api._get_dsh_workspaces().close()


def test_future_preview_remains_sha_bound_and_cannot_be_saved_as_current(tmp_path):
    path, raw = future_file(tmp_path)
    api = GuiApi(tmp_path / "profile", dsh_file_dialog=lambda _: str(path))
    changed = api.dsh_client_import_candidate_input_draft({"dsh_session_id": "future-view", "path": str(path),
        "expected_file_sha": "f"*64})
    assert changed["code"] == 1 and changed["reason"] == "candidate_input_draft_file_changed"
    refused = api.dsh_client_file_dialog({"action": "save", "document_kind": "candidate_input_draft", "suggested_name": path.name})
    assert refused["code"] == 1 and refused["reason"] == "candidate_input_draft_destination_invalid"
    with pytest.raises(PlanError):
        decode_draft(raw)
    assert api._get_dsh_workspaces().lookup_binding("future-view") is None
    assert path.read_bytes() == raw
    api._get_dsh_workspaces().close()


@pytest.mark.parametrize("entry", ["picker", "selected"])
def test_future_file_changed_between_selection_reads_does_not_create_preview_or_candidate(tmp_path, monkeypatch, entry):
    import genshin_app.agent_chat.candidate_input_draft_store as store

    path, raw = future_file(tmp_path)
    api = GuiApi(tmp_path / "profile", dsh_file_dialog=lambda _: str(path))
    original_loader, calls = store.load_candidate_input_draft_preview, []
    def changing_loader(selected):
        loaded = original_loader(selected)
        calls.append(loaded["file_sha"])
        if len(calls) == 1:
            path.write_bytes(raw + b" ")
        return loaded
    monkeypatch.setattr(store, "load_candidate_input_draft_preview", changing_loader)
    if entry == "picker":
        result = api.dsh_client_open_candidate_input_draft({"dsh_session_id": "future-view"})
    else:
        result = api.dsh_client_import_candidate_input_draft({"dsh_session_id": "future-view", "path": str(path),
            "expected_file_sha": hashlib.sha256(raw).hexdigest()})
    assert result["code"] == 1 and result["reason"] == "candidate_input_draft_file_changed"
    assert len(calls) == 2 and calls[0] != calls[1]
    assert not getattr(api, "_dsh_draft_previews", {})
    assert api._get_dsh_workspaces().lookup_binding("future-view") is None
    assert api._dsh_shell is None
    api._get_dsh_workspaces().close()


@pytest.mark.parametrize("bad_json", [
    b'{"kind":"genshin-candidate-input-draft","schema_version":2,"input":{"x":NaN}}',
    b'{"kind":"genshin-candidate-input-draft","schema_version":2,"input":{"x":1e999}}',
    b'{"kind":"genshin-candidate-input-draft","schema_version":2,"input":{"x":1,"x":2}}',
])
def test_future_metadata_does_not_bypass_strict_json_syntax(tmp_path, bad_json):
    path = tmp_path / "bad.gencdraft.json"
    path.write_bytes(bad_json)
    api = GuiApi(tmp_path / "profile")
    result = api.dsh_client_import_candidate_input_draft({"dsh_session_id": "future-view", "path": str(path),
        "expected_file_sha": hashlib.sha256(bad_json).hexdigest()})
    assert result["code"] == 1 and not getattr(api, "_dsh_draft_previews", {})
    assert api._get_dsh_workspaces().lookup_binding("future-view") is None
    assert path.read_bytes() == bad_json
    api._get_dsh_workspaces().close()


@pytest.mark.parametrize("version", [True, 0, -1, "2"])
def test_invalid_version_cannot_get_future_metadata_preview(tmp_path, version):
    path, raw = future_file(tmp_path)
    value = json.loads(raw)
    value["schema_version"] = version
    path.write_text(json.dumps(value), encoding="utf-8")
    api = GuiApi(tmp_path / "profile")
    response = api.dsh_client_import_candidate_input_draft({"dsh_session_id": "future-view", "path": str(path),
        "expected_file_sha": hashlib.sha256(path.read_bytes()).hexdigest()})
    assert response["code"] == 1
    assert api._get_dsh_workspaces().lookup_binding("future-view") is None
    api._get_dsh_workspaces().close()


def test_inspection_rejects_oversized_stat_before_opening_file(tmp_path, monkeypatch):
    assert MAX_BYTES == 128*1024*1024
    target = tmp_path / "too-large.json"
    target.write_bytes(b"{}")
    api = GuiApi(tmp_path / "profile")
    original_stat, original_open = Path.stat, Path.open
    opened = []
    def stat(path, *args, **kwargs):
        return SimpleNamespace(st_size=MAX_BYTES+1) if path == target else original_stat(path, *args, **kwargs)
    def open_file(path, *args, **kwargs):
        if path == target:
            opened.append(True)
            return io.BytesIO(b"{}")
        return original_open(path, *args, **kwargs)
    monkeypatch.setattr(Path, "stat", stat)
    monkeypatch.setattr(Path, "open", open_file)
    response = api.inspect_plan_file(str(target))
    assert response["code"] == 1
    assert opened == []


def test_inspection_bounds_read_even_if_file_grows_after_stat(tmp_path, monkeypatch):
    target = tmp_path / "growing.json"
    target.write_bytes(b"{}")
    api = GuiApi(tmp_path / "profile")
    original_open = Path.open
    reads = []
    class OversizeBytes:
        def __len__(self):
            return MAX_BYTES+1
    class GrowingStream(io.BytesIO):
        def read(self, size=-1):
            reads.append(size)
            # The old unbounded reader appears small, making the regression real.
            return b"{}" if size == -1 else OversizeBytes()
    def open_file(path, *args, **kwargs):
        return GrowingStream() if path == target else original_open(path, *args, **kwargs)
    monkeypatch.setattr(Path, "open", open_file)
    response = api.inspect_plan_file(str(target))
    assert response["code"] == 1
    assert reads == [MAX_BYTES+1]
