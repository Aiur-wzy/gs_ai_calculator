"""Real publication and document-port regressions on isolated sentinel files."""
from __future__ import annotations

import contextlib
import hashlib
import io
import json
import os
import threading
import zipfile
from pathlib import Path
from types import SimpleNamespace
from xml.etree import ElementTree as ET

import pytest

from genshin_app.equipment.files import file_sha, publish_files
from genshin_app.equipment.schema import PlanError
from genshin_app.output_policy import database_files, ordinary_output_scope


@pytest.mark.parametrize("index", range(len(database_files(Path("synthetic/genshin.db")))))
def test_all_database_roles_refuse_even_overwrite_and_cas(tmp_path, index):
    db = tmp_path / "profile" / "genshin.db"
    db.parent.mkdir()
    target = database_files(db)[index]
    target.write_bytes(b"role-sentinel")
    before = target.read_bytes()
    with ordinary_output_scope(database_path=db, data_dir=db.parent), pytest.raises(PlanError, match="普通输出") as caught:
        publish_files([(target, b"bad")], overwrite=True, expected_shas={target: file_sha(target)})
    assert caught.value.reason == "file_conflict"
    assert target.read_bytes() == before
    assert not target.with_name(target.name + ".previous").exists()
    assert not target.with_name(f".{target.name}.plan.lock").exists()


def test_side_effect_cannot_overwrite_input(tmp_path):
    output = tmp_path / "normal.json"
    original = output.with_name(output.name + ".previous")
    original.write_bytes(b"source")
    with pytest.raises(PlanError):
        publish_files([(output, b"new")], input_paths=[original])
    assert original.read_bytes() == b"source"
    assert not output.exists()


@pytest.mark.parametrize("left_role,right_role", [("lock", "lock"), ("lock", "backup"), ("backup", "backup")])
def test_side_effect_aliases_rejected_before_lock_acquisition(tmp_path, monkeypatch, left_role, right_role):
    from genshin_app.equipment import files
    first, second = tmp_path / "first.json", tmp_path / "second.json"
    def role(path, kind):
        return path.with_name(f".{path.name}.plan.lock" if kind == "lock" else path.name + ".previous")
    left, right = role(first, left_role), role(second, right_role)
    left.write_bytes(b"side-role-sentinel")
    os.link(left, right)
    def no_lock(*_args, **_kwargs):
        pytest.fail("alias collision must be rejected before any lock is acquired")
    monkeypatch.setattr(files, "file_lock", no_lock)
    with pytest.raises(PlanError) as caught:
        publish_files([(first, b"first"), (second, b"second")])
    assert caught.value.reason == "file_conflict"
    assert caught.value.details["published_paths"] == []
    assert left.read_bytes() == right.read_bytes() == b"side-role-sentinel"
    assert not first.exists() and not second.exists()


def test_lock_release_error_retains_committed_truth(tmp_path, monkeypatch):
    from genshin_app.equipment import files
    original_lock = files.file_lock
    @contextlib.contextmanager
    def release_failure(path):
        with original_lock(path):
            yield
        raise OSError("isolated lock release failure")
    monkeypatch.setattr(files, "file_lock", release_failure)
    output = tmp_path / "committed.json"
    with pytest.raises(PlanError) as caught:
        publish_files([(output, b"committed")])
    assert output.read_bytes() == b"committed"
    assert caught.value.details["published_paths"] == [str(output.resolve())]
    assert caught.value.details["pending_paths"] == []
    assert caught.value.details["unknown_paths"] == []


def test_final_sha_read_error_retains_committed_truth(tmp_path, monkeypatch):
    from genshin_app.equipment import files
    original_sha = files.file_sha
    output = tmp_path / "committed.json"
    def final_read_failure(path):
        if Path(path) == output and output.is_file() and output.read_bytes() == b"committed":
            raise OSError("isolated final observation failure")
        return original_sha(path)
    monkeypatch.setattr(files, "file_sha", final_read_failure)
    with pytest.raises(PlanError) as caught:
        publish_files([(output, b"committed")])
    assert output.read_bytes() == b"committed"
    assert caught.value.details["published_paths"] == [str(output.resolve())]
    assert not caught.value.details["pending_paths"]


def test_replace_error_after_write_is_unknown_not_pending(tmp_path, monkeypatch):
    from genshin_app.equipment import files
    original_replace = files.os.replace
    output = tmp_path / "unknown.json"
    def unknown_replace(source, destination):
        original_replace(source, destination)
        if Path(destination) == output:
            raise OSError("filesystem reply lost after atomic replacement")
    monkeypatch.setattr(files.os, "replace", unknown_replace)
    with pytest.raises(PlanError) as caught:
        publish_files([(output, b"actually-written")])
    assert output.read_bytes() == b"actually-written"
    assert caught.value.details["published_paths"] == []
    assert caught.value.details["pending_paths"] == []
    assert caught.value.details["unknown_paths"] == [str(output.resolve())]


@pytest.mark.parametrize("failure", ["exception", "mismatch"])
def test_committed_result_readback_failure_preserves_external_edit(tmp_path, monkeypatch, failure):
    from genshin_app import whiteboard
    from genshin_app.agent_chat.native_candidate_transaction_contracts import (
        NativeServiceV2Error,
    )
    from genshin_app.agent_chat.native_whiteboard_delivery_consumers import (
        NativeWhiteboardDeliveryConsumers,
    )
    conversation, capability, receipt = "engineering", "wdc_v1_" + "a" * 43, "b" * 64
    session_root = tmp_path / "private-session"
    session_root.mkdir()
    source = session_root / "stored-result.json"
    source.write_bytes(b'{"engineering":true}')
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    board = {"board_id": "fixed-board"}
    delivery = {"file_sha": digest, "calculated": {"result": {}}}
    row = {"application_kind": "whiteboard_calculated", "revision": 1, "receipt_fingerprint": receipt,
           "document_digest": "c" * 64, "proof": {}}
    bundle = SimpleNamespace(session_root=session_root,
        binding=SimpleNamespace(candidate_session=SimpleNamespace(db_path=tmp_path / "isolated.db")))
    consumer = NativeWhiteboardDeliveryConsumers()
    consumer._lock = threading.RLock()
    consumer._whiteboard_deliveries = {hashlib.sha256(capability.encode("ascii")).hexdigest(): {
        "conversation_id": conversation, "candidate_revision": 1, "receipt_fingerprint": receipt,
        "durable_projection": True, "result_path": source.absolute(), "board_digest": row["document_digest"], "file_sha": digest}}
    # Fixture the already-committed authority record; publication and hostile
    # post-publication file access still execute the actual production method.
    consumer._candidate_review_target = lambda *_: (bundle, None)
    consumer._read_last_applied_whiteboard_projection = lambda *_: {}
    consumer._read_last_applied_whiteboard_projection_payload = lambda *_: (row, board, delivery, source)
    consumer._read_application_intent = lambda *_: {"intent": {"state": "committed"}}
    consumer._validate_application_intent_for_binding = lambda *_: None
    consumer._same_application_identity = lambda *_: True
    consumer._stable_error = lambda _error: "readback_failed"
    output = tmp_path / "user-result.json"
    def edit_before_verification(_board, path):
        assert Path(path).read_bytes() == source.read_bytes()
        Path(path).write_bytes(b"newer-external-user-content")
        if failure == "exception":
            raise OSError("external replacement made the JSON invalid")
        return {"file_sha": "d" * 64, "calculated": {}}
    monkeypatch.setattr(whiteboard, "read_whiteboard_result", edit_before_verification)
    with pytest.raises(NativeServiceV2Error) as caught:
        consumer.publish_committed_whiteboard_result(conversation, capability, output, 1, receipt)
    assert output.read_bytes() == b"newer-external-user-content"
    assert source.read_bytes() == b'{"engineering":true}'
    assert caught.value.details["published_paths"] == [str(output.resolve())]
    assert caught.value.details["publication_state"] == "committed_verification_failed"
    assert caught.value.details["requires_user_inspection"] is True


def test_database_hardlink_and_symlink_aliases(tmp_path):
    db = tmp_path / "genshin.db"
    db.write_bytes(b"sqlite-sentinel")
    alias = tmp_path / "look-like-document.json"
    os.link(db, alias)
    with ordinary_output_scope(database_path=db), pytest.raises(PlanError):
        publish_files([(alias, b"bad")], overwrite=True)
    assert db.read_bytes() == alias.read_bytes() == b"sqlite-sentinel"


def test_input_alias_not_waived_by_overwrite(tmp_path):
    source = tmp_path / "input.json"
    source.write_bytes(b"original")
    alias = tmp_path / "output.json"
    os.link(source, alias)
    with pytest.raises(PlanError):
        publish_files([(alias, b"computed")], overwrite=True, input_paths=[source])
    assert source.read_bytes() == b"original"


def test_document_cas_in_place_allowed_but_backup_collision_not(tmp_path):
    plan = tmp_path / "plan.json"
    plan.write_bytes(b"old")
    receipt = publish_files([(plan, b"new")], overwrite=True,
                            expected_shas={plan: file_sha(plan)}, input_paths=[plan])
    assert receipt["published_paths"] == [str(plan.resolve())]
    assert plan.read_bytes() == b"new"
    assert plan.with_name(plan.name + ".previous").read_bytes() == b"old"


def test_profile_private_roots_not_ordinary_outputs(tmp_path):
    with ordinary_output_scope(data_dir=tmp_path), pytest.raises(PlanError):
        publish_files([(tmp_path / "native_agent_sessions_v2" / "new.json", b"bad")])
    receipt = publish_files([(tmp_path / "ordinary" / "new.json", b"ok")])
    assert receipt["published_paths"]


def test_private_delivery_export_is_not_an_ordinary_save_target(tmp_path):
    with ordinary_output_scope(data_dir=tmp_path), pytest.raises(PlanError):
        publish_files([(tmp_path / "agent_deliveries" / "new.json", b"bad")])


def test_case_alias_of_managed_database_is_rejected(tmp_path):
    db = tmp_path / "genshin.db"
    db.write_bytes(b"db")
    with ordinary_output_scope(database_path=db), pytest.raises(PlanError):
        publish_files([(tmp_path / "GENSHIN.DB", b"bad")], overwrite=True)
    assert db.read_bytes() == b"db"


def test_sealed_package_rejects_new_file_and_resolved_alias(tmp_path, monkeypatch):
    import genshin_app.output_policy as policy
    from genshin_app import paths
    package = tmp_path / "package"
    package.mkdir()
    exe = package / "GenshinCalc.exe"
    exe.write_bytes(b"exe")
    monkeypatch.setattr(paths, "is_frozen", lambda: True)
    # This synthetic package exercises output protection after profile resolution.
    monkeypatch.setattr(paths, "default_data_dir", lambda: tmp_path / "data")
    monkeypatch.setattr(policy.sys, "executable", str(exe))
    monkeypatch.setattr(policy.sys, "_MEIPASS", str(package / "_internal"), raising=False)
    with pytest.raises(PlanError):
        publish_files([(package / "previously-unlisted.json", b"bad")])
    alias = tmp_path / "looks-like-export.json"
    os.link(exe, alias)
    with pytest.raises(PlanError):
        publish_files([(alias, b"bad")], overwrite=True)
    publish_files([(tmp_path / "outside.json", b"ok")])


def test_commit_rechecks_policy_after_preparation(tmp_path, monkeypatch):
    import genshin_app.output_policy as policy
    destination = tmp_path / "normal.json"
    original = policy.validate_output_paths
    calls = 0

    def changed(paths, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 4:  # initial, prepare, temporary, commit
            raise PlanError("目标角色已变化", "file_conflict")
        return original(paths, **kwargs)

    monkeypatch.setattr(policy, "validate_output_paths", changed)
    with pytest.raises(PlanError) as caught:
        publish_files([(destination, b"never")])
    assert caught.value.details["published_paths"] == []
    assert not destination.exists()
    assert not list(tmp_path.glob("*.tmp"))


def test_real_gui_resolved_save_protects_custom_database(tmp_path):
    from genshin_app.gui.ports.document import DocumentGuiPort
    db = tmp_path / "genshin.db"
    db.write_bytes(b"db")
    port = DocumentGuiPort(tmp_path, tmp_path / "plans", db, display_catalog=lambda _line: {})
    template = port.new_template()["data"]["template"]
    response = port.save_template(str(db), template)
    assert response["code"] == 1
    assert response["reason"] == "file_conflict"
    assert db.read_bytes() == b"db"
    target = tmp_path / "document.json"
    response = port.save_template(str(target), template)
    assert response["code"] == 0
    assert response["data"]["file_sha"] == file_sha(target)
    assert json.loads(target.read_text(encoding="utf-8")) == template


def test_engine_calculation_cannot_overwrite_its_real_input(tmp_path):
    from genshin_app.cli import _cmd_engine_calc
    source = tmp_path / "input.json"
    source.write_bytes(b"do-not-even-parse")
    with pytest.raises(PlanError):
        _cmd_engine_calc(SimpleNamespace(template=str(source), patch=None, output=str(source)))
    assert source.read_bytes() == b"do-not-even-parse"


def test_panel_reference_cli_reuses_service_without_candidate_mutation(tmp_path, monkeypatch, capsys):
    from genshin_app import cli
    from genshin_app.equipment import panel_reference
    called = []
    request = tmp_path / "request.json"
    output = tmp_path / "reference.json"
    request.write_text('{"engineering": true}', encoding="utf-8")
    value = {"contract": "engineering-reference", "advisory": True}
    def reference(db_path, payload, version_line="release"):
        called.append((db_path, payload, version_line))
        return value
    monkeypatch.setattr(panel_reference, "reference_panel", reference)
    monkeypatch.setattr(cli, "_require_runtime_database", lambda _path: {})
    arguments = ["--data-dir", str(tmp_path), "equipment", "panel-reference", "--input", str(request)]
    assert cli.main(arguments) == 0
    assert json.loads(capsys.readouterr().out)["data"] == value
    assert cli.main([*arguments, "--output", str(output)]) == 0
    assert json.loads(output.read_text(encoding="utf-8")) == value
    capsys.readouterr()
    assert len(called) == 2
    assert cli.main([*arguments, "--output", str(request), "--overwrite"]) == 1
    capsys.readouterr()
    assert len(called) == 2  # conflict rejected before running arithmetic
    assert json.loads(request.read_text(encoding="utf-8")) == {"engineering": True}


def _sheet_text(content):
    with zipfile.ZipFile(io.BytesIO(content)) as archive:
        for name in archive.namelist():
            if name.endswith((".xml", ".rels")):
                ET.fromstring(archive.read(name))
        return archive.read("xl/worksheets/sheet1.xml").decode("utf-8")


def _decode_st_xstring(text):
    import re
    return re.sub(r"_x([0-9A-Fa-f]{4})_", lambda match: chr(int(match.group(1), 16)), text)


@pytest.mark.parametrize("value", ["hit\x00next", "control\x01\x0b\x1f", "literal_x0000_", "_x005F__x0041_", "回车\r换行\n制表\t", "中文🧊<&>"])
def test_plain_and_formula_xlsx_lossless_strings(value):
    from genshin_app.export import _write_xlsx
    from genshin_app.formula_workbook import _Sheet, _xlsx
    buffer = io.BytesIO()
    _write_xlsx(buffer, [("表格", [[value]])])
    sheet = _Sheet("表格")
    sheet.append([value])
    for content in (buffer.getvalue(), _xlsx([sheet])):
        text = _sheet_text(content)
        root = ET.fromstring(text)
        ns = {"s": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
        encoded = root.find(".//s:t", ns).text
        assert _decode_st_xstring(encoded) == value


def test_formula_illegal_character_refuses_without_publishing(tmp_path):
    from genshin_app.formula_workbook import _Cell, _Sheet, _xlsx
    sheet = _Sheet("表格")
    sheet.append([_Cell(0, '="bad\x00"')])
    with pytest.raises(ValueError, match="XLSX公式"):
        _xlsx([sheet])


def test_real_export_and_formula_projection_keep_control_text(tmp_path):
    from formula_workbook_fixtures import envelope, probe_template

    from genshin_app.export import export_result
    from genshin_app.formula_workbook import build_formula_workbook
    template = probe_template()
    label = "真实投影\x00与字面_x0000_"
    template["hits"][0]["hit_name"] = label
    calculated = envelope(template)
    target = tmp_path / "result.xlsx"
    export_result(calculated["result"], "xlsx", target)
    for content in (target.read_bytes(), build_formula_workbook(calculated)):
        with zipfile.ZipFile(io.BytesIO(content)) as archive:
            decoded = []
            for name in archive.namelist():
                if name.endswith(".xml"):
                    root = ET.fromstring(archive.read(name))
                    decoded.extend(_decode_st_xstring(text.text or "") for text in root.iter()
                                   if text.tag.endswith("}t"))
            assert label in decoded


def test_partial_publication_reports_what_really_committed(tmp_path, monkeypatch):
    from genshin_app.equipment import files
    original = files.os.replace
    first, second = tmp_path / "one.json", tmp_path / "two.json"
    def fail_second(source, destination):
        if destination == second:
            raise OSError("disk unavailable")
        return original(source, destination)
    monkeypatch.setattr(files.os, "replace", fail_second)
    with pytest.raises(PlanError) as caught:
        publish_files([(first, b"one"), (second, b"two")])
    assert caught.value.details["published_paths"] == [str(first.resolve())]
    assert first.read_bytes() == b"one"
    assert not second.exists()
