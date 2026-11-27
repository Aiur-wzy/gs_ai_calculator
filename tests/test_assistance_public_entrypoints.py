"""Same optional diagnosis/real ER execution through actual CLI and native dispatch."""
import copy
import json
import uuid

from genshin_app.agent_chat.artifact_candidate import ArtifactCandidateSession
from genshin_app.cli import main
from genshin_app.equipment.files import fingerprint
from tests.test_energy_trials import plan, request


def _session(tmp_path):
    return ArtifactCandidateSession(str(uuid.uuid4()), tmp_path / "session", tmp_path / "absent.db", "native", plan())


def test_native_assistance_works_before_calculation_and_has_no_state_effect(tmp_path):
    current = _session(tmp_path)
    before = (copy.deepcopy(current.candidate), current.revision, copy.deepcopy(current.stage_state),
              (current.root / "checkpoint.json").read_bytes())
    result = current.call_tool("gs_diagnose_result", {"expected_revision": current.revision,
        "request": {"view": "assistance", "expected": [{"id": "unknown", "input_refs": ["/unknown"]}]}})
    assert result["code"] == 0, result
    assert result["data"]["authority"] == "advisory_only"
    assert (current.candidate, current.revision, current.stage_state, (current.root / "checkpoint.json").read_bytes()) == before
    assert current.calculated is None and current.report is None


def test_real_trial_uses_identical_core_through_cli_and_native_and_keeps_original(tmp_path, monkeypatch, capsys):
    current = _session(tmp_path)
    before = fingerprint(current.candidate)
    authority_before = copy.deepcopy(current.provisional_authority)
    query = {"view": "energy_trials", **request()}
    native = current.call_tool("gs_diagnose_result", {"expected_revision": current.revision, "request": query})
    assert native["code"] == 0, native
    assert current.calculated is None and fingerprint(current.candidate) == before
    assert current.provisional_authority == authority_before
    source, request_file = tmp_path / "plan.json", tmp_path / "request.json"
    source.write_text(json.dumps(current.candidate), encoding="utf-8")
    request_file.write_text(json.dumps(query), encoding="utf-8")
    monkeypatch.setattr("genshin_app.cli._require_runtime_database", lambda *_: {"can_start": True})
    assert main(["--data-dir", str(tmp_path), "equipment", "diagnose", "--plan", str(source),
                 "--request", str(request_file)]) == 0
    cli = json.loads(capsys.readouterr().out)["data"]
    assert native["data"]["evidence_selection"] == {
        "source": "current_input_only", "execution_evidence": "agent_diagnostic_missing"}
    assert cli == {key: value for key, value in native["data"].items() if key != "evidence_selection"}
    assert fingerprint(json.loads(source.read_text("utf-8"))) == before


def test_capabilities_exposes_actual_auxiliary_contracts(capsys):
    assert main(["capabilities", "--section", "calculation_diagnostics_contract", "--pointer", "/auxiliary_views"]) == 0
    value = json.loads(capsys.readouterr().out)["data"]["data"]
    assert set(value) == {"assistance", "energy_trials", "input_trials", "rotation_preflight"}
    assert value["energy_trials"]["contract"]["acceptance_effect"] == "none"
    assert value["rotation_preflight"]["request"] == '{view:"rotation_preflight"}'
    assert "engine check-rotation INPUT --preflight" in value["rotation_preflight"]["entrypoint"]


def test_input_trials_alias_uses_actual_cli_and_native_dispatch(tmp_path, monkeypatch, capsys):
    current = _session(tmp_path)
    query = {"view": "input_trials", **request()}
    before = fingerprint(current.candidate)
    native = current.call_tool("gs_diagnose_result", {"expected_revision": 0, "request": query})
    assert native["code"] == 0, native
    plan_file, query_file = tmp_path / "plan.json", tmp_path / "request.json"
    plan_file.write_text(json.dumps(current.candidate), encoding="utf-8")
    query_file.write_text(json.dumps(query), encoding="utf-8")
    monkeypatch.setattr("genshin_app.cli._require_runtime_database", lambda *_: {"can_start": True})
    assert main(["--data-dir", str(tmp_path), "equipment", "diagnose", "--plan", str(plan_file),
                 "--request", str(query_file)]) == 0
    cli = json.loads(capsys.readouterr().out)["data"]
    assert cli == {k: v for k, v in native["data"].items() if k != "evidence_selection"}
    assert fingerprint(current.candidate) == before and current.calculated is None


def test_bad_trial_query_is_repairable_and_a_later_assistance_call_still_works(tmp_path):
    current = _session(tmp_path)
    invalid = current.call_tool("gs_diagnose_result", {"expected_revision": 0,
        "request": {"view": "energy_trials", "trials": [{"id": "bad", "stats": {"a": {"energy_recharge": 0}}}]}})
    assert invalid["code"] == 1 and invalid["category"] == "request_error"
    assert "system_fault" not in invalid
    assert current.call_tool("gs_diagnose_result", {"expected_revision": 0,
        "request": {"view": "assistance"}})["code"] == 0


def test_real_many_q_trials_use_supported_envelope_instead_of_old_256k_cutoff(tmp_path):
    current = _session(tmp_path)
    document = current.candidate
    rotation = document["rotation_model"]
    count = 10
    rotation["period"], document["manual_template"]["loop_duration"] = 4*count, 4*count
    evidence = {"status": "assumed", "source": "工程容量测试，无真实角色规则"}
    once = {"mode": "once"}
    prototype = document["manual_template"]["hits"][0]
    document["manual_template"]["hits"] = [{**copy.deepcopy(prototype), "id": "h" + str(i), "t": 4*i + .2} for i in range(count)]
    rotation["actions"] = [{"id": "q" + str(i), "ability_id": "q", "start": 4*i,
        "occupation_end": 4*i + .1, "order": i, "repeat": once, **evidence} for i in range(count)]
    rotation["events"] = [{"id": "spend" + str(i), "type": "energy_spend", "t": 4*i, "order": 20,
        "character": "a", "action_id": "q" + str(i), "required": 10, "amount": 10, "repeat": once, **evidence}
        for i in range(count)] + [{"id": "return" + str(i), "type": "energy_direct", "t": 4*i - 1, "order": 0,
        "amounts": {"a": 10}, "repeat": once, **evidence} for i in range(1, count)]
    document["mechanics_model"]["hit_events"] = [{"hit_id": "h" + str(i), "action_id": "q" + str(i), "order": 30+i}
        for i in range(count)]
    current.stage_state["energy_intent"] = "multi_round_legal"
    query = {"view": "energy_trials", "trials": [{"id": "trial" + str(i), "stats": {"a": {"energy_recharge": 2}}}
        for i in range(64)]}
    before = fingerprint(current.candidate)
    # Use the real native Host and durable ledger, rather than stopping at the
    # Python tool function; then carry the exact reply into the Node DSH tool.
    from tests.test_native_app_tool_host import call, host
    request_args = {"expected_revision": 0, "request": query}
    owner = host(tmp_path / "call-ledger", current, lambda _s, name, args: current.call_tool(name, args))
    terminal = call(owner, tool="gs_diagnose_result", raw=json.dumps(request_args))
    assert terminal["terminal_state"] == "settled_success", terminal
    result = terminal["result"]
    assert result["code"] == 0, result
    saved = json.loads(owner.ledger.read()["entries"]["call-1"]["result_json"])
    assert saved == result
    from tests.node_domain_reply import assert_node_domain_reply
    assert_node_domain_reply(tmp_path, owner.current_snapshot(), result, request_args)
    from genshin_app.equipment.files import json_bytes
    assert 256*1024 < len(json_bytes(result)) < 4*1024*1024
    assert len(result["data"]["trials"]) == 65 and fingerprint(current.candidate) == before


def test_over_capacity_advisory_reply_settles_as_explicit_failure_not_fake_success(tmp_path, monkeypatch):
    from genshin_app.agent_chat import tools
    from genshin_app.agent_chat.native_app_call_ledger import MAX_RESULT_BYTES
    from tests.test_native_app_tool_host import call, host
    current = _session(tmp_path)
    before = fingerprint(current.candidate)
    monkeypatch.setitem(tools.DISPATCH, "gs_diagnose_result", lambda *_: {"oversized": "x" * MAX_RESULT_BYTES})
    owner = host(tmp_path / "ledger", current, lambda _s, name, args: current.call_tool(name, args))
    args = {"expected_revision": 0, "request": {"view": "assistance"}}
    result = call(owner, tool="gs_diagnose_result", raw=json.dumps(args))
    assert result["terminal_state"] == "settled_failure"
    assert result["result"]["reason"] == "agent_response_limit"
    assert "system_fault" not in result["result"]
    assert fingerprint(current.candidate) == before
    assert json.loads(owner.ledger.read()["entries"]["call-1"]["result_json"]) == result["result"]
