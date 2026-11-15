"""Shared assembly file boundary plus a real small-database CLI round trip."""
from __future__ import annotations

import copy
import json

import pytest
from test_periodic_actions import confirm_periodic, periodic_plan
from test_pipeline import fake_repo, stub_fetcher  # noqa: F401
from test_runtime_periodic_attack import periodic_db  # noqa: F401

from genshin_app.cli import main
from genshin_app.equipment import run_plan_calc
from genshin_app.equipment.files import input_view
from genshin_app.equipment.schema import new_plan
from genshin_app.gui.api import GuiApi

# ruff: noqa: F811


@pytest.fixture(autouse=True)
def _runtime_database_already_admitted(monkeypatch):
    monkeypatch.setattr(
        "genshin_app.cli._require_runtime_database",
        lambda _data_dir: {"can_start": True, "state": "test_ready"},
    )


@pytest.fixture
def transport(tmp_path, monkeypatch):
    from genshin_app.equipment import periodic_source_assembly as module

    plan, request = tmp_path / "input.plan.json", tmp_path / "request.json"
    plan.write_text(json.dumps(new_plan()), encoding="utf8")
    request.write_text('{"schema_version":1}', encoding="utf8")
    calls = []

    def assemble(value, settings, db):
        calls.append((copy.deepcopy(value), settings, db))
        draft = copy.deepcopy(value)
        draft["revision"] += 1
        return {"plan": draft, "assembly": {"status": "created", "profile_id": "example"},
            "report": {"state": "pending", "raw_source": "x" * 90_000,
                       "missing_inputs": [{"code": "review", "message": "待核实", "path": str(i)} for i in range(23)]}}

    monkeypatch.setattr(module, "assemble_periodic_source", assemble)
    args = ["--data-dir", str(tmp_path), "equipment", "assemble-periodic", "--plan", str(plan), "--request", str(request)]
    return plan, request, calls, args


def test_summary_saves_only_native_and_caps_missing_list(transport, tmp_path, capsys):
    original, _, calls, args = transport
    before = original.read_bytes()
    output = tmp_path / "多通道草稿.json"
    assert main([*args, "--output", str(output)]) == 0
    response = json.loads(capsys.readouterr().out)["data"]
    saved = json.loads(output.read_text("utf8"))
    assert saved["kind"] == "genshin-plan" and "report" not in saved
    assert response["summary_only"] and response["missing_input_count"] == 23
    assert len(response["missing_inputs"]) == 20 and "raw_source" not in json.dumps(response)
    assert original.read_bytes() == before and len(calls) == 1


def test_inplace_compares_original_sha_and_preserves_external_edit(transport, monkeypatch, capsys):
    from genshin_app.equipment import periodic_source_assembly as module

    original, _, calls, args = transport
    assert main([*args, "--output", str(original)]) == 1
    capsys.readouterr()
    assert calls == []
    prior = module.assemble_periodic_source

    def racing(*values):
        result = prior(*values)
        original.write_text('{"external_edit":true}', encoding="utf8")
        return result

    monkeypatch.setattr(module, "assemble_periodic_source", racing)
    assert main([*args, "--output", str(original), "--overwrite"]) == 1
    assert json.loads(capsys.readouterr().err)["reason"] == "file_conflict"
    assert original.read_text("utf8") == '{"external_edit":true}'
    assert not original.with_name(original.name + ".previous").exists()


@pytest.mark.parametrize("target", ["request.json", "request.json.previous", ".request.json.plan.lock",
    "genshin.db", "genshin.db-wal", ".genshin.db.write.lock", "input.plan.json.previous", ".input.plan.json.plan.lock"])
def test_shared_protection_runs_before_backend(transport, tmp_path, capsys, target):
    _, _, calls, args = transport
    path = tmp_path / target
    if not path.exists():
        path.write_bytes(b"protected")
    before = path.read_bytes()
    assert main([*args, "--output", str(path), "--overwrite"]) == 1
    assert json.loads(capsys.readouterr().err)["reason"] == "file_conflict"
    assert calls == [] and path.read_bytes() == before


@pytest.mark.parametrize("body", [b"x" * 32769, b"\xff", b"{"],
                         ids=["oversized", "invalid-utf8", "invalid-json"])
def test_bad_request_has_no_backend_call_or_partial_file(transport, tmp_path, capsys, body):
    _, request, calls, args = transport
    request.write_bytes(body)
    output = tmp_path / "not-published.json"
    assert main([*args, "--output", str(output)]) == 1
    capsys.readouterr()
    assert calls == [] and not output.exists()


def test_gui_public_api_and_capability_are_same_backend(tmp_path, monkeypatch):
    from genshin_app import equipment
    from genshin_app.equipment.periodic_source_assembly import (
        periodic_source_assembly_capabilities,
    )

    marker = {"plan": {}, "assembly": {}, "report": {"state": "pending"}}
    calls = []

    def assemble(*values):
        calls.append(values)
        return marker

    monkeypatch.setattr(equipment, "assemble_periodic_source", assemble)
    assert GuiApi(tmp_path).assemble_periodic_source({"p": 1}, {"r": 2}) == {"code": 0, "data": marker}
    assert calls == [({"p": 1}, {"r": 2}, tmp_path / "genshin.db")]
    assert equipment.plan_capabilities()["periodic_source_assembly_contract"] == periodic_source_assembly_capabilities()


def test_actual_cli_draft_reopens_and_existing_confirm_dispatches_fifteen_hits(periodic_db, tmp_path, capsys):
    from genshin_app.equipment.files import read_plan

    manual = periodic_plan(periodic_db)
    profile, source = manual["action_event_profiles"][0], manual["periodic_attack_sources"][0]
    mappings = {row["channel_id"]: row for row in source["channels"]}
    request = {"schema_version": 1, "profile_id": "assembled.q", "ability_ids": ["e"],
        **{key: copy.deepcopy(source[key]) for key in ("version_line", "selection", "origin_reference_id", "status", "source")},
        "damage_type_ids": ["damage.elemental_burst"], "scaling_stat": "atk", "lifecycle": copy.deepcopy(profile["lifecycle"]),
        "channels": [{**{key: copy.deepcopy(channel[key]) for key in
                        ("schedule", "contact_profile_id", "default_landed", "self_sampling")},
                      **{key: copy.deepcopy(mappings[channel["channel_id"]][key]) for key in
                         ("candidate_id", "counter_tag", "application_mode", "verification")}}
                     for channel in profile["channels"]]}
    plan = copy.deepcopy(manual)
    plan["manual_template"]["hits"] = []
    for field in ("action_event_profiles", "action_event_bindings", "periodic_attack_sources"):
        plan[field] = []
    original, request_path, output = [tmp_path / name for name in ("input.json", "request.json", "assembled.json")]
    original.write_text(json.dumps(plan), encoding="utf8")
    request_path.write_text(json.dumps(request), encoding="utf8")
    before = original.read_bytes()
    assert main(["--data-dir", str(periodic_db.parent), "equipment", "assemble-periodic",
                 "--plan", str(original), "--request", str(request_path), "--output", str(output)]) == 0
    summary = json.loads(capsys.readouterr().out)["data"]
    assert summary["state"] == "pending" and summary["assembly"]["created"]["hits"] == 2
    draft = read_plan(output)["plan"]
    assert original.read_bytes() == before and draft["rotation_model"] == manual["rotation_model"]
    assert draft["action_event_profiles"][0]["accepted_profile_fingerprint"] is None
    assert len(draft["manual_template"]["hits"]) == 2
    prior = copy.deepcopy(input_view(draft))
    calculated = run_plan_calc(confirm_periodic(draft, periodic_db), periodic_db)
    assert calculated["result"]["summary"]["total_damage"] == pytest.approx(1500.889481306076)
    assert [hit["t"] for hit in calculated["result"]["hits"]] == [i*10+x for i in range(3) for x in (.2,.45,.7,.95,1.1)]
    assert input_view(read_plan(output)["plan"]) == prior
