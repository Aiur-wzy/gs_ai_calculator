"""Public file edits use the actual shared prepare/identity/CAS services."""
import json

import pytest

from genshin_app.cli import main
from genshin_app.equipment.files import fingerprint, input_view
from genshin_app.input_editing import prepare_input_edit
from tests.test_energy_trials import plan


def files(tmp_path, request):
    source, query = tmp_path / "plan.json", tmp_path / "edit.json"
    source.write_text(json.dumps(plan()), encoding="utf-8")
    query.write_text(json.dumps(request), encoding="utf-8")
    return source, query


def call(source, request, tmp_path, capsys, *extra):
    result = main(["--data-dir", str(tmp_path), "equipment", "edit", "--plan", str(source),
        "--request", str(request), *map(str, extra)])
    captured = capsys.readouterr()
    return result, json.loads(captured.err if result else captured.out)


def test_stable_id_file_edit_matches_shared_native_input_core_and_invalidates_derived_cache(tmp_path, capsys):
    request = {"operations": [{"op": "replace", "selector": {"collection": "/rotation_model/actions", "id": "q2"},
        "path": "/start", "value": 4.1}]}
    source, query = files(tmp_path, request)
    original = json.loads(source.read_text("utf-8"))
    prepared = prepare_input_edit(original, request)
    destination = tmp_path / "next.plan.json"
    code, response = call(source, query, tmp_path, capsys, "--output", destination)
    assert code == 0, response
    actual = json.loads(destination.read_text("utf-8"))
    prepared["revision"] = original["revision"] + 1
    assert actual == prepared
    assert response["data"]["writes"] and response["data"]["execution_status"] == "not_checked"
    assert json.loads(source.read_text("utf-8")) == original


def test_inplace_edit_requires_explicit_overwrite_and_source_sha_conflict_is_not_ignored(tmp_path, capsys):
    source, query = files(tmp_path, {"operations": [{"op": "replace", "path": "/manual_template/characters/0/stats/energy_recharge", "value": 2}]})
    before = source.read_bytes()
    code, _ = call(source, query, tmp_path, capsys, "--output", source)
    assert code == 1 and source.read_bytes() == before
    code, failure = call(source, query, tmp_path, capsys, "--output", source, "--overwrite", "--expected-file-sha", "f" * 64)
    assert code == 1 and failure["reason"] == "file_conflict" and source.read_bytes() == before
    code, _ = call(source, query, tmp_path, capsys, "--output", source, "--overwrite")
    assert code == 0 and json.loads(source.read_text("utf-8"))["revision"] == 1


def test_preview_of_unfinished_draft_is_allowed_without_executing(tmp_path, capsys):
    source, query = files(tmp_path, {"operations": [{"op": "replace", "path": "/rotation_model/actions/1/ability_id", "value": "not-created-yet"}]})
    before = source.read_bytes()
    code, response = call(source, query, tmp_path, capsys, "--preview", "--output", source)
    assert code == 0, response
    assert not response["data"]["writes"] and source.read_bytes() == before


@pytest.mark.parametrize("pointer,value", [("/plan_id", "another-id"), ("/provisional_entities/0/approval", {})])
def test_file_editor_cannot_create_authority_or_override_system_identity(tmp_path, capsys, pointer, value):
    source, query = files(tmp_path, {"operations": [{"op": "replace", "path": pointer, "value": value}]})
    before = fingerprint(input_view(json.loads(source.read_text("utf-8"))))
    code, _ = call(source, query, tmp_path, capsys, "--output", tmp_path / "next.json")
    assert code == 1 and fingerprint(input_view(json.loads(source.read_text("utf-8")))) == before
    assert not (tmp_path / "next.json").exists()


@pytest.mark.parametrize("invalid", [None, "false", 0, [], {}])
def test_explicit_nonboolean_preview_is_rejected_even_without_output(tmp_path, capsys, invalid):
    source, query = files(tmp_path, {"operations": [], "preview": invalid})
    before = source.read_bytes()
    code, failure = call(source, query, tmp_path, capsys)
    assert code == 1 and failure["reason"] == "input_edit_request"
    assert source.read_bytes() == before
