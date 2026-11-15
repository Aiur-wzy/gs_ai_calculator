"""App domain/research/paid-work requests from one authenticated DSH runtime."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from genshin_app.equipment.files import fingerprint
from genshin_db.locking import file_lock

from .native_app_call_queue import AppCallDeferred
from .native_dsh_budget_ledger import NativeDshBudgetLedger
from .native_dsh_domain_calls import NativeDshDomainCalls
from .native_dsh_mux_client import NativeDshMuxError
from .native_dsh_workspace import _conversation
from .native_dsh_workspace_compatibility import compatibility_issue
from .native_research_ledger import NativeResearchLedger
from .native_research_ledger import _args as validate_research_arguments
from .sessions import write_owned


def _strict_object(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise NativeDshMuxError("dsh_research_arguments_invalid")
        value[key] = item
    return value


class NativeDshHostRequests:
    def __init__(self, workspaces, *, data_dir: Path, budget_ledger=None):
        self.workspaces = workspaces
        self.data_dir = Path(data_dir).absolute()
        self.domain = NativeDshDomainCalls(workspaces.resolve_target)
        self.budgets = budget_ledger or NativeDshBudgetLedger(self.data_dir)
        workspaces.configure_budget_ledger(self.budgets)

    def _research_ledger(self, session_id):
        try:
            # Research remains useful if an old calculation is stale. This
            # never grants calculation/apply rights or switches database facts.
            reader = getattr(self.workspaces, "domain_research_ledger", None)
            ledger = reader(session_id) if reader is not None else None
            if ledger is not None:
                return ledger
        except Exception as error:
            if (compatibility_issue(error) is None and getattr(error, "reason", None)
                    not in {"native_app_binding_database_changed", "agent_database_changed"}):
                raise
        cid = _conversation(session_id)
        return NativeResearchLedger(self.data_dir / "native-dsh-research" / cid, conversation_id=cid)

    def domain_route_cached(self, session_id):
        return getattr(self.workspaces, "cached_domain_route", lambda _sid: None)(session_id)

    def domain_route_resolve(self, session_id):
        return getattr(self.workspaces, "resolve_domain_route", lambda _sid: None)(session_id)

    def domain_resource_snapshot(self, key):
        return getattr(self.workspaces, "domain_resource_snapshot",
            lambda _key: {"busy": False, "generation": 0})(key)

    def subscribe_domain_resource(self, key, callback):
        return getattr(self.workspaces, "subscribe_domain_resource", lambda *_args: None)(key, callback)

    def domain_operation_receipt(self, request):
        if request["target_kind"] != "dsh_app_call_request":
            return None
        return self.workspaces.domain_operation_receipt(request)

    @staticmethod
    def _research_identity(request):
        for key in ("turn", "step", "call_seq"):
            if type(request[key]) is not int or not 0 <= request[key] <= 2**53 - 1:
                raise NativeDshMuxError("dsh_research_fact_invalid")
        if type(request["call_id"]) is not str or not request["call_id"]:
            raise NativeDshMuxError("dsh_research_fact_invalid")
        identity = {key: request[key] for key in ("session_id", "turn", "step", "call_seq", "call_id")}
        return "dsh-call-" + fingerprint(identity), f"dsh-turn-{request['turn']}", f"dsh-step-{request['step']}-event-{request['call_seq']}"

    @staticmethod
    def _arguments(request):
        raw = request["arguments_json"]
        if type(raw) is not str or hashlib.sha256(raw.encode("utf-8")).hexdigest() != request["raw_arguments_hash"]:
            raise NativeDshMuxError("dsh_research_raw_arguments_mismatch")
        try:
            parsed = json.loads(raw, object_pairs_hook=_strict_object,
                parse_constant=lambda _: (_ for _ in ()).throw(ValueError("non-finite")))
        except (ValueError, UnicodeError, RecursionError):
            raise NativeDshMuxError("dsh_research_arguments_invalid") from None
        if parsed != request["arguments"]:
            raise NativeDshMuxError("dsh_research_arguments_mismatch")
        return validate_research_arguments(request["tool"], parsed)

    def _research(self, kind, request):
        common = {"session_id", "turn", "step", "call_seq", "call_id", "tool", "arguments",
                  "arguments_json", "raw_arguments_hash"}
        terminal = {"research_id", "status", "content_hash", "presentation_meta", "resource_ids"}
        if set(request) != common | (terminal if kind == "dsh_research_settle_request" else set()):
            raise NativeDshMuxError("dsh_research_request_invalid")
        call_id, turn, epoch = self._research_identity(request)
        arguments = self._arguments(request)
        ledger = self._research_ledger(request["session_id"])
        ledger.root.mkdir(parents=True, exist_ok=True)
        with file_lock(ledger.lock_path):
            record = ledger.read()
            prior = next((item for item in record["entries"] if item["call_id"] == call_id), None)
            if kind == "dsh_research_register_request":
                policy = self.budgets.turn_policy(request["session_id"], request["turn"])["effective"]
                rows = [item for item in record["entries"] if item["turn_id"] == turn]
                searches = [item for item in rows if item["tool"] == "web_search"]
                queries = sum(len(item["arguments"]["queries"]) for item in searches)
                if prior is None and (len(rows) >= policy["web_tool_calls"] or
                        request["tool"] == "web_search" and (len(searches) >= policy["web_search_calls"]
                            or queries + len(arguments["queries"]) > policy["web_search_queries"]) or
                        request["tool"] == "web_fetch" and len(rows) - len(searches) >= policy["web_fetch_calls"]):
                    raise NativeDshMuxError("dsh_research_budget_exhausted")
                self.budgets.admit_tool(session_id=request["session_id"], turn=request["turn"],
                    claim_id=fingerprint({"session_id": request["session_id"], "web_call_id": call_id}), category="web")
                return ledger.register(expected_revision=record["revision"], turn_id=turn, epoch_id=epoch,
                    workflow_stage="artifact", call_id=call_id, tool=request["tool"], arguments=arguments,
                    raw_arguments_hash=request["raw_arguments_hash"])
            if prior is None or any(prior[key] != value for key, value in {
                    "research_id": request["research_id"], "turn_id": turn, "epoch_id": epoch,
                    "tool": request["tool"], "arguments": arguments,
                    "raw_arguments_hash": request["raw_arguments_hash"]}.items()):
                raise NativeDshMuxError("dsh_research_settlement_identity_mismatch")
            return ledger.settle(**{key: request[key] for key in terminal})

    def __call__(self, kind, request):
        try:
            return self._dispatch(kind, request)
        except AppCallDeferred:
            raise  # the queue retains this unadmitted ticket; not a business failure
        except Exception as error:  # noqa: BLE001 - preserve a safe cross-process diagnosis
            from genshin_app.app_fault_store import _identities
            context = _identities({"request_id": request.get("request_id"), "session_id": request.get("session_id"),
                "turn_id": request.get("turn"), "step_id": request.get("step"), "call_id": request.get("call_id"),
                "document_id": request.get("document_id"), "candidate_revision": request.get("candidate_revision"),
                "stage_revision": request.get("stage_revision")}) if isinstance(request, dict) else {}
            safe_kind = _identities({"operation_id": kind}).get("operation_id", "unknown_host_request")
            diagnostic_id = fingerprint({"kind": safe_kind, "context": context})
            from .native_app_action_controller import NativeAppActionControllerError

            code = error.code if isinstance(error, (NativeDshMuxError, NativeAppActionControllerError)) else "dsh_domain_execution_failed"
            code = _identities({"operation_id": code}).get("operation_id", "dsh_domain_execution_failed")
            admission = "unknown"
            if kind == "dsh_app_call_request" and isinstance(request, dict):
                try:
                    admission = self.workspaces.operation_admission(request.get("session_id"), request.get("call_id"))
                except Exception:  # noqa: BLE001 - diagnosis never acquires write authority
                    admission = "unknown"
            from genshin_app.app_system_fault import safe_exception_diagnostic
            diagnostic = safe_exception_diagnostic(error)
            evidence = {"contract": "genshin-native-dsh-host-failure-v1", "diagnostic_id": diagnostic_id,
                "kind": safe_kind, "code": code, "error_type": diagnostic["exception_type"], "app_call_admission": admission,
                "frames": diagnostic["frames"], "context": context}
            try:
                write_owned(self.data_dir / "logs" / "native-dsh-host", diagnostic_id + ".json", evidence, 64 * 1024)
                storage = {"state": "persisted"}
            except Exception:  # noqa: BLE001 - preserve the original failure and do not issue a fake ID
                diagnostic_id = None
                storage = {"state": "failed", "reason": "diagnostic_persistence_failed"}
            details = {"diagnostic_id": diagnostic_id, "diagnostic_storage": storage,
                "error_type": diagnostic["exception_type"], "app_call_admission": admission}
            failure = error.failure if isinstance(error, NativeDshMuxError) else None
            if isinstance(failure, dict) and isinstance(failure.get("details"), dict):
                details = {**failure["details"], **details}
            outcome_unknown = (error.outcome_unknown if isinstance(error, NativeDshMuxError)
                               else admission != "not_recorded")
            details["execution_state"] = ("unknown_after_interruption" if outcome_unknown
                else "did_not_execute" if admission == "not_recorded" else admission)
            details["operation_may_have_completed"] = outcome_unknown
            if "busy" in code:
                details.setdefault("recovery", {"action": "wait_resource", "retry_same_request": False})
            elif outcome_unknown:
                details.setdefault("recovery", {"action": "inspect_settlement", "retry_same_request": False})
            elif "stale" in code:
                details.setdefault("recovery", {"action": "refresh_binding", "retry_same_request": False})
            details.setdefault("contract_ref", {"resource": "tool_contracts", "pointer":
                "/tools/" + request["tool"] if isinstance(request, dict) and isinstance(request.get("tool"), str) else "/tools"})
            raise NativeDshMuxError(code, failure={"code": code,
                "message": failure["message"] if isinstance(failure, dict) else "App这一步未能完成，原有候选仍保留，请先核对当前状态。",
                "details": details}, outcome_unknown=outcome_unknown) from None

    def _dispatch(self, kind, request):
        if kind in {"dsh_app_bind_request", "dsh_app_call_request"}:
            return self.domain(kind, request)
        contracts = {
            "dsh_research_register_request": "genshin-native-dsh-research-register-v1",
            "dsh_research_settle_request": "genshin-native-dsh-research-settle-v1",
            "dsh_budget_acquire_request": "genshin-native-dsh-budget-acquire-v1",
            "dsh_budget_settle_request": "genshin-native-dsh-budget-settle-v1",
            "dsh_budget_tool_request": "genshin-native-dsh-budget-tool-v1"}
        if (kind not in contracts or type(request) is not dict or request.get("contract") != contracts[kind]
                or type(request.get("request_id")) is not str or type(request.get("session_id")) is not str):
            raise NativeDshMuxError("dsh_host_request_invalid")
        body = {key: value for key, value in request.items() if key not in {"contract", "request_id"}}
        if kind.startswith("dsh_research_"):
            result = self._research(kind, body)
            contract = "genshin-native-dsh-research-result-v1"
        else:
            if kind == "dsh_budget_tool_request":
                if set(body) != {"session_id", "turn", "claim_id", "category"} or body["category"] != "generic":
                    raise NativeDshMuxError("dsh_budget_generic_tool_invalid")
                result = self.budgets.admit_tool(**body)
            else:
                result = self.budgets.acquire(body) if kind == "dsh_budget_acquire_request" else self.budgets.settle(body)
            contract = "genshin-native-dsh-budget-result-v1"
        return {"contract": contract, "request_id": request["request_id"],
                "session_id": request["session_id"], "result": result}
