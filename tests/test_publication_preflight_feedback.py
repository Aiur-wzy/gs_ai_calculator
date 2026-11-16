"""Public save feedback must distinguish no write from an uncertain commit."""
from pathlib import Path

import pytest

from genshin_app.equipment import files
from genshin_app.equipment.schema import PlanError
from genshin_app.gui.ports import document
from genshin_app.output_policy import ordinary_output_scope


def port(tmp_path):
    return document.DocumentGuiPort(tmp_path, tmp_path / "plans", tmp_path / "genshin.db", display_catalog=lambda _line: {})


def not_written(details):
    assert details["publication_state"] == "not_written"
    assert details["published_paths"] == details["unknown_paths"] == []


def test_invalid_template_reports_no_write_and_preserves_existing_file(tmp_path, monkeypatch):
    target = tmp_path / "existing.json"
    target.write_bytes(b"untouched")
    monkeypatch.setattr(files, "publish_files", lambda *_a, **_k: pytest.fail("invalid input reached publisher"))
    response = port(tmp_path).save_template(str(target), {"form": "unresolved"})
    assert response["code"] == 1
    not_written(response["details"])
    assert target.read_bytes() == b"untouched"
    assert sorted(p.name for p in tmp_path.iterdir()) == ["existing.json"]


def test_template_read_failure_is_before_publication(tmp_path, monkeypatch):
    service = port(tmp_path)
    template = service.new_template()["data"]["template"]
    monkeypatch.setattr(files, "file_sha", lambda _p: (_ for _ in ()).throw(PermissionError("cannot inspect target")))
    monkeypatch.setattr(files, "publish_files", lambda *_a, **_k: pytest.fail("publisher must not run"))
    response = service.save_template(str(tmp_path / "target.json"), template)
    assert response["reason"] == "template_save_preparation_failed"
    not_written(response["details"])
    assert list(tmp_path.iterdir()) == []


def test_protected_path_preflight_preserves_role_and_no_write(tmp_path):
    target = tmp_path / "protected.json"
    target.write_bytes(b"protected")
    with ordinary_output_scope(protected_files=[target]), pytest.raises(PlanError) as caught:
        files.publish_files([(target, b"forbidden")], overwrite=True)
    assert caught.value.reason == "file_conflict"
    assert caught.value.details["protected_role"] == "managed"
    not_written(caught.value.details)
    assert target.read_bytes() == b"protected"
    assert sorted(p.name for p in tmp_path.iterdir()) == ["protected.json"]


def test_output_backup_alias_preflight_reports_no_write(tmp_path):
    first = tmp_path / "first.json"
    backup = first.with_name(first.name + ".previous")
    with pytest.raises(PlanError) as caught:
        files.publish_files([(first, b"first"), (backup, b"second")])
    not_written(caught.value.details)
    assert set(caught.value.details["conflicting_roles"]) == {"output", "backup"}
    assert list(tmp_path.iterdir()) == []


def test_real_gui_protected_database_has_complete_no_write_feedback(tmp_path):
    database = tmp_path / "genshin.db"
    database.write_bytes(b"database-sentinel")
    service = port(tmp_path)
    response = service.save_template(str(database), service.new_template()["data"]["template"])
    assert response["reason"] == "file_conflict"
    not_written(response["details"])
    assert database.read_bytes() == b"database-sentinel"


def test_lost_replace_reply_remains_unknown(tmp_path, monkeypatch):
    target = tmp_path / "unknown.json"
    replace = files.os.replace
    def lost_reply(source, destination):
        replace(source, destination)
        if Path(destination) == target:
            raise OSError("rename completed but response was lost")
    monkeypatch.setattr(files.os, "replace", lost_reply)
    with pytest.raises(PlanError) as caught:
        files.publish_files([(target, b"written")])
    assert caught.value.details.get("publication_state") != "not_written"
    assert caught.value.details["published_paths"] == []
    assert caught.value.details["unknown_paths"] == [str(target.resolve())]
    assert target.read_bytes() == b"written"


def test_partial_commit_remains_partial_not_not_written(tmp_path, monkeypatch):
    first, second = tmp_path / "first.json", tmp_path / "second.json"
    replace = files.os.replace
    def refuse_second(source, destination):
        if Path(destination) == second:
            raise OSError("second output failed")
        return replace(source, destination)
    monkeypatch.setattr(files.os, "replace", refuse_second)
    with pytest.raises(PlanError) as caught:
        files.publish_files([(first, b"first"), (second, b"second")])
    assert caught.value.details.get("publication_state") != "not_written"
    assert caught.value.details["published_paths"] == [str(first.resolve())]
    assert caught.value.details["unknown_paths"] == [str(second.resolve())]
    assert first.read_bytes() == b"first" and not second.exists()


def test_gui_preserves_publisher_unknown_verbatim(tmp_path, monkeypatch):
    service = port(tmp_path)
    target = tmp_path / "unknown.json"
    details = {"published_paths": [], "unknown_paths": [str(target)], "cause": "lost reply"}
    def ambiguous(*_args, **_kwargs):
        target.write_bytes(b"possibly committed")
        raise PlanError("reply lost", "file_conflict", details)
    monkeypatch.setattr(files, "publish_files", ambiguous)
    response = service.save_template(str(target), service.new_template()["data"]["template"])
    assert response["details"] == details
    assert response["details"].get("publication_state") != "not_written"


def test_gui_unknown_exception_after_publisher_does_not_claim_no_write(tmp_path, monkeypatch):
    service = port(tmp_path)
    target = tmp_path / "unknown.json"
    def unexpected(*_args, **_kwargs):
        target.write_bytes(b"actually written")
        raise OSError("unclassified post-publish observation failure")
    monkeypatch.setattr(files, "publish_files", unexpected)
    response = service.save_template(str(target), service.new_template()["data"]["template"])
    assert response["code"] == 1
    assert response.get("details", {}).get("publication_state") != "not_written"
    assert target.read_bytes() == b"actually written"
