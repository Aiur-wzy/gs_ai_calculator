"""Opt-in concise CLI receipts preserve complete files and existing transports."""
from __future__ import annotations

import copy
import hashlib
import json

import pytest

from genshin_app.cli import main
from genshin_app.equipment import new_plan


@pytest.fixture(autouse=True)
def _runtime_database_already_admitted(monkeypatch):
    monkeypatch.setattr(
        "genshin_app.cli._require_runtime_database",
        lambda _data_dir: {"can_start": True, "state": "test_ready"},
    )


def call(args, capsys):
    code = main(args)
    captured = capsys.readouterr()
    return code, json.loads(captured.err if code else captured.out), captured


def assert_published_receipt(data, path):
    assert data["files"]["published_paths"] == [str(path.resolve())]
    assert data["files"]["file_shas"][str(path.resolve())] == hashlib.sha256(path.read_bytes()).hexdigest()


def make_plan(path, count=100):
    plan = new_plan()
    plan["manual_template"].update(
        characters=[{"id": "c", "level": 90, "stats": {"atk": 100}}],
        hits=[{"id": f"hit{i}", "damage_type_ids": ["damage.other"], "t": 1 + i / 1000,
               "character": "c", "skill": "独立摘要测试", "damage_system": "classic",
               "element": "physical", "multiplier": 1} for i in range(count)])
    path.write_text(json.dumps(plan, ensure_ascii=False), encoding="utf-8")
    return plan


def test_periodic_summary_preserves_partial_scope_without_raw_or_sequence(tmp_path, capsys, monkeypatch):
    from genshin_db import runtime_queries
    raw = "large-periodic-original" * 10000
    data = {"status": "ready", "mode": "selected", "snapshot": {}, "whole_source_status": "partial",
        "action_creation_association": "unproven", "dispatch_semantics_status": "not_proven",
        "source_graph": {"creation_bundle": {"raw": raw}}, "issues": [],
        "selected_candidates": [{"candidate_id": "periodic.example", "channel_kind": "periodic_callback_attack", "status": "ready",
            "projection": {"parameter": {"status": "ready", "value": 2.0214, "raw": raw},
                "attenuation": {"status": "ready", "group": "default", "profile": {"durabilitySequence": [1] * 10000}},
                "timer": {"status": "ready", "ticks": 250000, "seconds": .25}}}]}
    monkeypatch.setattr(runtime_queries, "query_runtime_public", lambda *args: copy.deepcopy(data))
    request, output = tmp_path / "periodic.request.json", tmp_path / "periodic.full.json"
    request.write_text(json.dumps({"action": "periodic_attack_profile", "character_id": 1, "slot": "burst"}), "utf8")
    code, receipt, captured = call(["--data-dir", str(tmp_path), "db", "runtime", "--input", str(request), "--output", str(output), "--summary"], capsys)
    assert code == 0
    summary = receipt["data"]
    assert summary["whole_source_status"] == "partial" and summary["dispatch_semantics_status"] == "not_proven"
    assert summary["selected_candidates"][0]["projection_summary"]["timer"]["ticks"] == 250000
    assert "large-periodic-original" not in captured.out and "durabilitySequence" not in captured.out
    assert len(captured.out.encode("utf8")) < 5000
    assert json.loads(output.read_text("utf8")) == data


def test_runtime_summary_has_selected_action_state_and_complete_file(tmp_path, capsys, monkeypatch):
    from genshin_db import runtime_queries

    data = {"status": "ready", "snapshot": {"snapshot_id": "snapshot", "upstream_commit": "commit",
            "normalization": {"large": "upstream-payload" * 10000}},
            "selection": {"character_id": 10000002, "skill_depot_id": 201, "skill_id": 10018,
                          "slot": "burst", "constellation": 0},
            "level": {"effective_level": 10, "constellation_applied": False},
            "action_dispatch": {"status": "pending", "reasons": ["not_compiled"]},
            "execution_support": "not_evaluated", "issues": [{"message": "pending detail"}] * 100,
            "talents": [{"operations_raw": "raw operation" * 10000}], "parameter_bindings": [1, 2]}
    original = copy.deepcopy(data)
    monkeypatch.setattr(runtime_queries, "query_runtime_public", lambda *args: data)
    request, output = tmp_path / "request.json", tmp_path / "facts.json"
    request.write_text('{"action":"action_facts","character_id":10000002,"slot":"burst"}', encoding="utf-8")
    before = request.read_bytes()
    args = ["--data-dir", str(tmp_path), "db", "runtime", "--input", str(request), "--output", str(output), "--summary"]
    code, payload, captured = call(args, capsys)
    assert code == 0, payload
    summary = payload["data"]
    assert len(captured.out.encode("utf-8")) < 3000
    assert "upstream-payload" not in captured.out and "raw operation" not in captured.out
    assert summary["selection"] == data["selection"]
    assert summary["action_dispatch"] == {"status": "pending", "reason_count": 1}
    assert summary["issue_count"] == 100 and summary["counts"]["parameter_bindings"] == 2
    assert json.loads(output.read_text("utf-8")) == original
    assert data == original and request.read_bytes() == before
    assert_published_receipt(summary, output)


def test_runtime_without_summary_preserves_full_stdout(tmp_path, capsys):
    output = tmp_path / "runtime.json"
    code, payload, _ = call(["--data-dir", str(tmp_path), "db", "runtime", "--output", str(output)], capsys)
    assert code == 0 and "summary_only" not in payload["data"]
    published = payload["data"].pop("files")
    assert published["published_paths"] == [str(output.resolve())]
    assert payload["data"] == json.loads(output.read_text("utf-8"))


def test_attenuation_summary_distinguishes_source_ready_and_execution_pending(tmp_path, capsys, monkeypatch):
    from genshin_db import runtime_queries

    data = {"status": "ready", "group": "nonbinary", "snapshot": {"snapshot_id": "snapshot"},
            "execution": {"status": "pending", "semantics": "finite-binary-application-sequence-v1",
                          "attack_binding": "not_evaluated", "issues": [{"message": "detail" * 10000}]},
            "raw_record": {"durabilitySequence": [1, 0.1] * 10000, "damageSequence": [1] * 10000},
            "projection": {"durability_sequence": None, "reset_cycle_seconds_raw": 0.10000000149,
                           "reset_cycle_seconds_rounded": 0.1, "rounding_error_seconds": -1.49e-9}}
    monkeypatch.setattr(runtime_queries, "query_runtime_public", lambda *args: data)
    request, output = tmp_path / "request.json", tmp_path / "attenuation.json"
    request.write_text('{"action":"attenuation_profile","group":"nonbinary"}', encoding="utf-8")
    code, payload, captured = call(["--data-dir", str(tmp_path), "db", "runtime", "--input", str(request),
                                    "--output", str(output), "--summary"], capsys)
    summary = payload["data"]
    assert code == 0 and summary["status"] == "ready"
    assert summary["execution"] == {"status": "pending", "semantics": "finite-binary-application-sequence-v1",
                                    "attack_binding": "not_evaluated", "issue_count": 1}
    assert summary["group"] == "nonbinary"
    assert summary["projection_summary"]["durability_sequence_length"] == 20000
    assert summary["projection_summary"]["reset_cycle_seconds_rounded"] == 0.1
    assert len(captured.out.encode("utf8")) < 3000
    assert "detail" not in captured.out and "damageSequence" not in captured.out and "raw_record" not in captured.out
    assert json.loads(output.read_text("utf8")) == data
    assert_published_receipt(summary, output)


def test_action_parameter_summary_preserves_unverified_use_and_full_descriptions(tmp_path, capsys, monkeypatch):
    from genshin_db import runtime_queries

    data = {"status": "ready", "param_index": 0,
            "scalar_projection": {"status": "ready", "value": 4.305600166320801,
                                  "semantics": "exact-table-scalar", "issues": []},
            "parameter_descriptions": {"status": "partial", "matched": [{"text": "中文完整参数原文" * 10000}],
                                       "unresolved": [{"hash": "missing"}]},
            "gameplay_interpretation": "not_evaluated"}
    monkeypatch.setattr(runtime_queries, "query_runtime_public", lambda *args: data)
    request, output = tmp_path / "request.json", tmp_path / "parameter.json"
    request.write_text('{"action":"action_parameter","character_id":10000002,"slot":"skill","param_index":0}', encoding="utf-8")
    code, payload, captured = call(["--data-dir", str(tmp_path), "db", "runtime", "--input", str(request),
                                    "--output", str(output), "--summary"], capsys)
    summary = payload["data"]
    assert code == 0 and summary["scalar_projection"]["value"] == data["scalar_projection"]["value"]
    assert summary["gameplay_interpretation"] == "not_evaluated"
    assert summary["descriptions"]["matched_count"] == 1
    assert summary["descriptions"]["unresolved_count"] == 1
    assert summary["descriptions"]["status"] == "partial"
    assert summary["descriptions"]["matched"] == []
    assert summary["descriptions"]["more_available"] is True
    assert len(captured.out.encode("utf-8")) < 3000 and "中文完整参数原文" not in captured.out
    assert json.loads(output.read_text("utf-8")) == data
    assert_published_receipt(summary, output)


@pytest.mark.parametrize("channel", [None, "application", "damage"])
def test_attenuation_channel_summary_counts_only_selected_sequence(channel):
    from genshin_app.cli import _runtime_output_summary

    request = {"action": "attenuation_profile", "group": "g"}
    data = {"status": "ready", "group": "g", "execution": {"status": "ready", "issues": []},
            "raw_record": {"durabilitySequence": [1] * 3, "damageSequence": [1] * 20000},
            "projection": {"reset_cycle_seconds_rounded": .5}}
    if channel is not None:
        request["channel"] = channel
    if channel == "damage":
        data["channel"] = channel
    original = copy.deepcopy(data)
    summary = _runtime_output_summary(data, request)
    expected = "damage_sequence_length" if channel == "damage" else "durability_sequence_length"
    assert summary["projection_summary"] == {"reset_cycle_seconds_rounded": .5,
                                             expected: 20000 if channel == "damage" else 3}
    if channel != "damage":
        assert summary == _runtime_output_summary(data, {"action": "attenuation_profile", "group": "g"})
        assert "channel" not in summary
    assert data == original and len(json.dumps(summary)) < 1200
    assert "raw_record" not in summary


@pytest.mark.parametrize("sequence_status", ["ready", "pending"])
def test_reaction_damage_summary_keeps_execution_boundaries_and_omits_large_raw(tmp_path, capsys, monkeypatch, sequence_status):
    from genshin_db import runtime_queries

    data = {"profile_version": 1, "status": "ready", "content_fingerprint": "f" * 64,
            "snapshot": {"snapshot_id": "s" * 64, "upstream_commit": "c" * 40, "normalization": {"huge": "N" * 10000}},
            "selection": {"reaction_type": "swirl", "element": "cryo", "branch": "ordinary", "scope": "origin_target"},
            "execution": {"sequence_status": sequence_status, "ability_execution": "pending", "owner_resolution": "not_evaluated",
                          "reaction_dispatch": "not_evaluated", "semantics": "source_relation_only"},
            "projection": {"group": "ElementReaction_Damage_Attenuation_1", "reset_cycle_seconds_rounded": .5,
                           "damage_sequence": [1] * 20000},
            "raw_nodes": {"attenuation": {"raw": {"damageSequence": [1] * 20000}}, "ordinary_modifier": {"raw": "RAW" * 10000}},
            "mapping_evidence": {"proof": "MAPPING" * 10000}, "sources": {"ability": {"raw": "SOURCE" * 10000}, "attenuation": {}},
            "separate_star_abilities": [{"raw": "STAR" * 10000}], "issues": [{"code": "ability_pending", "detail": "DETAIL" * 10000}]}
    original = copy.deepcopy(data)
    monkeypatch.setattr(runtime_queries, "query_runtime_public", lambda *args: data)
    request, output = tmp_path / "request.json", tmp_path / "reaction-profile.json"
    request.write_text('{"action":"reaction_damage_profile","element":"cryo"}', encoding="utf8")
    before = request.read_bytes()
    code, payload, captured = call(["--data-dir", str(tmp_path), "db", "runtime", "--input", str(request),
                                   "--output", str(output), "--summary"], capsys)
    summary = payload["data"]
    assert code == 0 and summary["status"] == "ready"
    assert summary["execution"] == {**data["execution"], "issue_count": 0}
    assert summary["selection"] == data["selection"]
    assert summary["projection_summary"]["damage_sequence_length"] == 20000
    assert summary["projection_summary"]["group"] == data["projection"]["group"]
    assert summary["source_count"] == 2 and summary["separate_star_ability_count"] == 1
    assert summary["issue_count"] == 1 and len(captured.out.encode("utf8")) < 3000
    assert not any(word in captured.out for word in ("RAW", "MAPPING", "SOURCE", "STAR", "DETAIL", "damageSequence"))
    assert json.loads(output.read_text("utf8")) == original
    assert data == original and request.read_bytes() == before
    assert_published_receipt(summary, output)


def test_equipment_summary_omits_events_and_equipment_but_file_is_complete(tmp_path, capsys):
    path, output = tmp_path / "plan.json", tmp_path / "result.json"
    make_plan(path)
    before = path.read_bytes()
    code, payload, captured = call(["--data-dir", str(tmp_path), "equipment", "calc", "--plan", str(path),
                                   "--output", str(output), "--summary"], capsys)
    assert code == 0, payload
    summary = payload["data"]
    full = json.loads(output.read_text("utf-8"))
    # The public short receipt now includes bounded diagnostics and scope.
    # Check the actual contract rather than the retired pre-assistance 4KB shape.
    assert len(captured.out.encode("utf-8")) < output.stat().st_size
    assert summary['default_assistance']['authority'] == 'advisory_only'
    assert summary['default_assistance']['presentation']['details_ref'] == {'resource': 'default_assistance'}
    assert summary['validation_scope'] == full['result']['validation_scope']
    assert output.stat().st_size > 20000
    assert summary["summary"] == {key: full["result"]["summary"][key] for key in ("total_damage", "dps")}
    assert summary["hit_count"] == len(full["result"]["hits"]) == 100
    assert summary["missing_input_count"] == 0
    assert "equipment_summary" in full and "equipment_summary" not in summary
    assert "result" not in summary
    assert path.read_bytes() == before
    assert_published_receipt(summary, output)


def test_equipment_without_summary_retains_complete_stdout(tmp_path, capsys):
    path, output = tmp_path / "plan.json", tmp_path / "result.json"
    make_plan(path, count=2)
    code, payload, _ = call(["--data-dir", str(tmp_path), "equipment", "calc", "--plan", str(path),
                            "--output", str(output)], capsys)
    assert code == 0 and "summary_only" not in payload["data"]
    full = json.loads(output.read_text("utf-8"))
    for key in ("result", "result_receipt", "equipment_summary"):
        assert payload["data"][key] == full[key]


@pytest.mark.parametrize("command", [["db", "runtime"], ["equipment", "calc", "--plan", "nonexistent.json"]])
def test_summary_without_output_fails_before_read_or_query(tmp_path, capsys, monkeypatch, command):
    from genshin_db import runtime_queries

    def forbidden(*args):
        pytest.fail("invalid flag combination must fail before querying")

    monkeypatch.setattr(runtime_queries, "query_runtime_public", forbidden)
    code, payload, captured = call(["--data-dir", str(tmp_path), *command, "--summary"], capsys)
    assert code == 1 and "--output" in payload["message"]
    assert captured.out == ""
    assert list(tmp_path.iterdir()) == []


def test_pending_calc_summary_writes_no_result(tmp_path, capsys):
    path, output = tmp_path / "pending.json", tmp_path / "result.json"
    plan = make_plan(path, count=1)
    plan["manual_template"]["hits"][0]["damage_type_ids"] = None
    path.write_text(json.dumps(plan), encoding="utf-8")
    before = path.read_bytes()
    code, payload, captured = call(["--data-dir", str(tmp_path), "equipment", "calc", "--plan", str(path),
                                   "--output", str(output), "--summary"], capsys)
    assert code == 1 and payload["reason"] == "missing_input"
    assert not output.exists() and captured.out == ""
    assert path.read_bytes() == before


@pytest.mark.parametrize("command", ["runtime", "calc"])
def test_summary_protects_input_even_from_publication_backup(tmp_path, capsys, command):
    output = tmp_path / "output.json"
    protected = tmp_path / "output.json.previous"
    output.write_text("previous result", encoding="utf-8")
    if command == "runtime":
        protected.write_text('{"action":"status"}', encoding="utf-8")
        args = ["db", "runtime", "--input", str(protected)]
    else:
        make_plan(protected, count=1)
        args = ["equipment", "calc", "--plan", str(protected)]
    before = protected.read_bytes(), output.read_bytes()
    code, payload, captured = call(["--data-dir", str(tmp_path), *args,
                                   "--output", str(output), "--summary", "--overwrite"], capsys)
    assert code == 1 and payload["reason"] == "file_conflict"
    assert captured.out == "" and (protected.read_bytes(), output.read_bytes()) == before


def test_capabilities_exposes_summary_only_for_two_supported_commands(capsys):
    code, payload, _ = call(["capabilities"], capsys)
    assert code == 0
    supported = {command["name"] for command in payload["data"]["commands"]
                 if any(arg["name"] == "--summary" for arg in command["args"])}
    assert supported == {"db runtime", "equipment calc"}
