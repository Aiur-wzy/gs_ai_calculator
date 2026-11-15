"""Real CLI transport faults belong to the App profile, never external DBs."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys

import pytest

from genshin_app.agent_chat import tools
from genshin_app.agent_chat.native_cli_link import (
    LiveCandidateCliLink,
    workspace_cli_call,
)
from genshin_app.agent_chat.native_dsh_workspace import NativeDshWorkspaceRegistry
from genshin_app.app_fault_store import fault_scope, read_system_diagnostic
from genshin_app.app_system_fault import system_failure
from genshin_app.equipment.schema import PlanError
from tests.test_common_input_editing import plan
from tests.test_queries import db_path as db_path  # noqa: PLC0414


def cli(arguments):
    result = subprocess.run([sys.executable, "-m", "genshin_app", *arguments],
        capture_output=True, text=True, encoding="utf-8", check=False, timeout=30,
        env={**os.environ, "PYTHONUTF8": "1"})
    return result.returncode, json.loads(result.stdout if result.returncode == 0 else result.stderr)


@pytest.mark.parametrize("has_outer_owner", [False, True])
def test_live_cli_nested_diagnostic_keeps_existing_owner_and_supports_fresh_cli_lookup(
    tmp_path, db_path, monkeypatch, has_outer_owner,
):
    app = tmp_path / "app-profile"
    owner = tmp_path / "outer-owner" if has_outer_owner else app
    external = tmp_path / "external-database"
    external.mkdir()
    database = external / "genshin.db"
    shutil.copyfile(db_path, database)
    before = database.read_bytes()
    registry = NativeDshWorkspaceRegistry(app / "workspaces", database_path=database,
        diagnostic_data_dir=app)
    registry.prepare_document("profile-owner", None, None)
    registry.activate("profile-owner")

    def failing_read(_session, _request):
        try:
            raise TypeError("private fixture payload sk-engineering-canary must never enter persisted diagnostics")
        except TypeError as error:
            failure = system_failure(error, component="app_domain_tools", operation="gs_read_context")
        raise PlanError("controlled domain system fault", failure["reason"], failure)

    monkeypatch.setattr(tools, "_read_context", failing_read)

    def dispatch(operation, session_id, request):
        if has_outer_owner:
            with fault_scope(owner, request_id="existing-owner-request"):
                return workspace_cli_call(registry, operation, session_id, request)
        return workspace_cli_call(registry, operation, session_id, request)

    link = LiveCandidateCliLink(app / "connections", dispatch, diagnostic_data_dir=app)
    query = tmp_path / "read.json"
    query.write_text(json.dumps({"resource": "candidate"}), encoding="utf-8")
    try:
        code, response = cli(["agent", "candidate", "read", "--connection", str(link.descriptor_for("profile-owner")),
            "--session-id", "profile-owner", "--request", str(query)])
        assert code == 1, response
        fault = response["details"]
        assert fault["diagnostic_storage"]["state"] == "persisted"
        identifier = fault["system_fault"]["diagnostic_id"]
        record = read_system_diagnostic(owner, identifier)
        assert record["context"]["session_id"] == "profile-owner"
        if has_outer_owner:
            assert record["context"]["request_id"] == "existing-owner-request"
            assert not (app / "logs").exists()
        lookup_code, lookup = cli(["--data-dir", str(owner), "diagnostics", "show", "--id", identifier])
        assert lookup_code == 0 and lookup["data"] == record
        assert "private fixture" not in json.dumps(record)
        assert "sk-engineering-canary" not in json.dumps({"response": response, "record": record})
        assert not (external / "logs").exists() and database.read_bytes() == before
    finally:
        link.close()
        registry.close()


def test_live_cli_uncaught_program_error_has_valid_profile_fault_and_public_readback(tmp_path, db_path, monkeypatch):
    app = tmp_path / "app-profile"
    external = tmp_path / "external-database"
    external.mkdir()
    database = external / "genshin.db"
    shutil.copyfile(db_path, database)
    before = database.read_bytes()
    registry = NativeDshWorkspaceRegistry(app / "workspaces", database_path=database,
        diagnostic_data_dir=app)
    registry.prepare_document("program-error", None, None)
    registry.activate("program-error")
    registry.enable_cli_link()

    def failing_read(_session, _request):
        raise RuntimeError("private unexpected payload sk-engineering-canary")

    monkeypatch.setattr(tools, "_read_context", failing_read)
    query = tmp_path / "read.json"
    query.write_text(json.dumps({"resource": "candidate"}), encoding="utf-8")
    try:
        code, response = cli(["agent", "candidate", "read", "--connection", registry.cli_connection("program-error"),
            "--session-id", "program-error", "--request", str(query)])
        assert code == 1, response
        assert response["reason"] == "app_internal_error"
        identifier = response["details"]["system_fault"]["diagnostic_id"]
        assert identifier and response["details"]["diagnostic_storage"]["state"] == "persisted"
        read_code, read = cli(["--data-dir", str(app), "diagnostics", "show", "--id", identifier])
        assert read_code == 0, read
        assert read["data"]["component"] == "app_domain_tools"
        assert read["data"]["operation"] == "cli.read"
        assert read["data"]["context"]["session_id"] == "program-error"
        assert "private unexpected" not in json.dumps(read)
        assert "sk-engineering-canary" not in json.dumps({"response": response, "record": read})
        assert not (external / "logs").exists() and database.read_bytes() == before
    finally:
        registry.close()


@pytest.mark.parametrize("tamper_cache", [False, True])
def test_real_cli_calculation_retains_only_the_exact_producer_validation_fault(
    tmp_path, db_path, monkeypatch, tamper_cache,
):
    app = tmp_path / "app-profile"
    registry = NativeDshWorkspaceRegistry(app / "workspaces", database_path=db_path,
        diagnostic_data_dir=app)
    registry.prepare_document("calculation-fault", "native", plan())
    session = registry.activate("calculation-fault").candidate_session
    registry.enable_cli_link()
    observed = []

    def reject_generated_output(_value):
        raise PlanError("controlled generated output rejected", "invalid_generated_result",
                        [{"path": "generated.hits", "code": "controlled_output_contract"}])

    monkeypatch.setattr(session, "_validate_calculated", reject_generated_output)
    original = session.calculate

    def observe_producer(revision):
        from genshin_app.agent_chat.calculation_attempt_failure import (
            CalculationOutputValidationFailure,
        )

        try:
            return original(revision)
        except CalculationOutputValidationFailure as error:
            observed.append(error.response["system_fault"]["diagnostic_id"])
            if tamper_cache:
                session._diagnostic_cache = None
            raise

    monkeypatch.setattr(session, "calculate", observe_producer)
    query = tmp_path / "calculate.json"
    query.write_text(json.dumps({"expected_revision": 0}), encoding="utf-8")
    try:
        code, response = cli(["agent", "candidate", "calculate", "--connection", registry.cli_connection("calculation-fault"),
            "--session-id", "calculation-fault", "--request", str(query)])
        assert code == 1 and len(observed) == 1, response
        identifier = response["details"]["system_fault"]["diagnostic_id"]
        assert (identifier == observed[0]) is (not tamper_cache)
        read_code, read = cli(["--data-dir", str(app), "diagnostics", "show", "--id", identifier])
        assert read_code == 0
        assert read["data"]["operation"] == ("cli.calculate" if tamper_cache else "gs_calculate.validate_generated_result")
        assert session.calculated is None and session.revision == 0
        records = list((app / "logs/app-system-faults").glob("*.json"))
        assert len(records) == (2 if tamper_cache else 1)
        assert not (db_path.parent / "logs").exists()
    finally:
        registry.close()


def test_real_cli_rejects_malformed_system_diagnostic_id_without_leaking_or_replaying(tmp_path, db_path, monkeypatch):
    from genshin_app import app_system_fault

    app = tmp_path / "app-profile"
    registry = NativeDshWorkspaceRegistry(app / "workspaces", database_path=db_path,
        diagnostic_data_dir=app)
    registry.prepare_document("malformed-fault", None, None)
    registry.activate("malformed-fault")
    registry.enable_cli_link()
    seen = []

    def fail(_session, _request):
        seen.append(True)
        raise RuntimeError("must not leak malformed producer text")

    def malformed(*args, **kwargs):
        response = system_failure(*args, **kwargs)
        response["system_fault"]["diagnostic_id"] = None
        return response

    monkeypatch.setattr(tools, "_read_context", fail)
    monkeypatch.setattr(app_system_fault, "system_failure", malformed)
    query = tmp_path / "read.json"
    query.write_text(json.dumps({"resource": "candidate"}), encoding="utf-8")
    try:
        code, response = cli(["agent", "candidate", "read", "--connection", registry.cli_connection("malformed-fault"),
            "--session-id", "malformed-fault", "--request", str(query)])
        assert code == 1 and response["reason"] == "cli_operation_unknown", response
        assert seen == [True]
        assert "must not leak" not in json.dumps(response)
        assert not (db_path.parent / "logs").exists()
    finally:
        registry.close()
