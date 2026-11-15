"""App-owned DSH Session bindings and isolated calculation workspaces.

Only document seeds, candidate owners, write capabilities and App operation
ledgers live here. DSH still owns all messages, Turns, history and questions.
A reserved document is not a chat: the first DSH Agent binding materializes
the candidate. Nothing is selected by whichever document happens to be open.
"""
from __future__ import annotations

import copy
import re
import secrets
import threading
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

from genshin_app.equipment.files import fingerprint
from genshin_app.equipment.schema import PlanError, validate_plan
from genshin_app.whiteboard import new_whiteboard, validate_whiteboard
from genshin_db.locking import file_lock

from .candidate_input_draft_store import CandidateInputDraftStore
from .native_app_action_controller import NativeAppActionController
from .native_app_action_ledger import NativeAppActionLedger
from .native_app_binding_provider import (
    BINDING_FILE,
    CandidateNativeAppBindingProviderV2,
)
from .native_app_call_ledger import NativeAppCallLedger
from .native_app_tool_composition import create_native_app_tool_host
from .native_candidate_application_backend import NativeCandidateApplicationBackend
from .native_candidate_application_port import NativeCandidateApplicationPort
from .native_candidate_projection_port import NativeCandidateProjectionPort
from .native_domain_session_registry import (
    NativeDomainSessionHandle,
    NativeDomainSessionRegistry,
)
from .native_dsh_domain_calls import DshDomainTarget, DshDomainUnavailable
from .native_dsh_mux_client import NativeDshMuxError
from .native_dsh_workspace_compatibility import (
    assert_workspace_compatible,
    compatibility_issue,
    current_workspace_identity,
)
from .native_dsh_workspace_projection import project_workspace_candidate
from .native_research_ledger import NativeResearchLedger
from .native_write_ledger import SessionWriteAuthority
from .sessions import MAX_NATIVE, read_owned, safe_path, write_owned

CONTRACT = "genshin-native-dsh-workspace-binding-v2"
_LEGACY_CONTRACT = "genshin-native-dsh-workspace-binding-v1"
_RECORD = "dsh-workspace.json"
_IDENTITY = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}\Z")
_RECORD_FIELDS = frozenset({"contract", "dsh_session_id", "conversation_id", "document_kind",
    "document_id", "document_origin", "workflow", "seed_fingerprint", "writer_fence", "writer_takeover",
    "compatibility_identity"})


def _identity(value):
    if type(value) is not str or _IDENTITY.fullmatch(value) is None:
        raise NativeDshMuxError("dsh_workspace_identity_invalid")
    return value


def _conversation(session_id):
    # A deterministic App identity makes a crash before publication recoverable.
    # It is not a DSH storage path, a model-selected filename or a history copy.
    return str(uuid.uuid5(uuid.NAMESPACE_URL, "genshin-dsh-domain:" + _identity(session_id)))


@dataclass
class _Workspace:
    conversation_id: str
    binding: object
    candidate_session: object
    research_ledger: NativeResearchLedger
    action_controller: NativeAppActionController
    app_call_ledger: NativeAppCallLedger
    tool_host: object = None
    domain_handle: NativeDomainSessionHandle | None = None
    resource_admission: object = None
    diagnostic_data_dir: Path | None = None

    @property
    def session_root(self):
        return self.candidate_session.root


class NativeDshWorkspaceRegistry:
    """One exact domain binding per DSH Session, with independent candidates.

    Files contain no Session/Turn/chat state. Preparing a seed is idempotent;
    replacing an existing Session's document requires an explicit new Session.
    Writer takeover journals its proposed fence before changing the durable
    lease, so either side of a crash can be completed without guessing.
    """

    def __init__(self, root: Path, *, database_path: Path, workflow="artifact",
                 provider=None, workspace_reader=None, action_callbacks=None, session_status_reader=None,
                 diagnostic_data_dir=None):
        self.root = Path(root).absolute()
        self.database_path = Path(database_path).resolve()
        self.diagnostic_data_dir = (Path(diagnostic_data_dir).absolute() if diagnostic_data_dir is not None
                                    else self.database_path.parent)
        self.delivery_root = self.root / "deliveries"
        self.workflow = workflow
        self._provider = provider or CandidateNativeAppBindingProviderV2()
        self._workspace_reader = workspace_reader
        self._action_callbacks = dict(action_callbacks or {})
        self._session_status_reader = session_status_reader
        self._lock = threading.RLock()
        # Routing is separate from the workspace authority lock: the mux
        # reader must never wait for candidate hydration or a file operation.
        self._route_lock = threading.Lock()
        self._domain_routes: dict[str, str] = {}
        self._route_controllers: dict[str, object] = {}
        self._workspaces: dict[str, _Workspace] = {}
        self._renderer_workspaces: dict[str, dict] = {}
        self._budget_ledger = None
        self._closed = False
        self._cli_link = None
        self._cli_link_enabled = False
        self._cli_settings_remote = None
        self._cli_settings_cancel = None
        self._cli_settings_calls = set()
        self.application_backend = NativeCandidateApplicationBackend(
            self.root, resolve_scope=self._conversation_scope, lock=self._lock,
            workspace_validator=self._validate_retirement_workspace)
        self.domain_registry = NativeDomainSessionRegistry(authority_lock=self._lock,
            access_validator=self._assert_action_access, read_access_validator=self._assert_read_access,
            single_binding_compatibility=False, candidate_lock_for_actions=True)
        self.application_port = NativeCandidateApplicationPort(self.domain_registry,
            backend=self.application_backend, restore_guard=self.application_backend.assert_restore_writable,
            timeline_sink=lambda *_args, **_kwargs: None)
        self.projection_port = NativeCandidateProjectionPort(self.domain_registry)
        self.application_backend._candidate_application_port = self.application_port

    def enable_cli_link(self, *, settings_remote=None, settings_cancel=None):
        """Production integration enables one local facade, never another owner."""
        with self._lock:
            self._ensure_open()
            self._cli_link_enabled = True
            self._cli_settings_remote = settings_remote
            self._cli_settings_cancel = settings_cancel

    def close_cli_link(self):
        """Retire IPC while Node can still cancel outstanding approval RPCs."""
        with self._lock:
            self._cli_link_enabled = False
            link, self._cli_link = self._cli_link, None
            pending, cancel = list(self._cli_settings_calls), self._cli_settings_cancel
        try:
            if callable(cancel):
                for request_id in pending:
                    cancel({"request_id": request_id})
        finally:
            if link is not None:
                link.close()

    def cli_connection(self, session_id=None):
        with self._lock:
            self._ensure_open()
            if not self._cli_link_enabled:
                return None
            if self._cli_link is None:
                from .native_cli_link import LiveCandidateCliLink, workspace_cli_call
                self._cli_link = LiveCandidateCliLink(self.root / ".cli-connections",
                    lambda operation, sid, request: workspace_cli_call(self, operation, sid, request),
                    diagnostic_data_dir=self.diagnostic_data_dir)
            if session_id is not None:
                session_id = _identity(session_id)
                if self.lookup_binding(session_id) is None:
                    raise NativeDshMuxError("dsh_domain_session_unbound")
            return str(self._cli_link.descriptor_for(session_id))

    def _assert_read_access(self, conversation_id):
        self._ensure_open()
        for sid, scope in self._workspaces.items():
            if scope.conversation_id == conversation_id:
                self._read(scope.session_root, sid)
                return
        raise NativeDshMuxError("dsh_domain_session_unbound")

    def _assert_action_access(self, conversation_id):
        self._assert_read_access(conversation_id)
        scope = next(scope for scope in self._workspaces.values() if scope.conversation_id == conversation_id)
        if scope.action_controller.agent_turn_active:
            raise NativeDshMuxError("dsh_domain_operation_busy")

    def _ensure_open(self):
        if self._closed:
            raise NativeDshMuxError("dsh_workspace_closed")

    def cached_domain_route(self, session_id):
        with self._route_lock:
            return None if self._closed else self._domain_routes.get(session_id)

    def resolve_domain_route(self, session_id):
        """Read an existing small binding; never activate or create a writer."""
        session_id = _identity(session_id)
        root = self._root(session_id)
        if not safe_path(root, _RECORD).is_file():
            return None
        row = self._read(root, session_id)
        with self._route_lock:
            if self._closed:
                raise NativeDshMuxError("dsh_workspace_closed")
            self._domain_routes[session_id] = row["conversation_id"]
        return row["conversation_id"]

    def domain_resource_snapshot(self, key):
        with self._route_lock:
            controller = self._route_controllers.get(key)
        return ({"busy": False, "generation": 0} if controller is None
                else controller.resource_snapshot())

    def subscribe_domain_resource(self, key, callback):
        with self._route_lock:
            controller = self._route_controllers.get(key)
        return None if controller is None else controller.subscribe_resource(callback)

    def domain_research_ledger(self, session_id):
        """Borrow the evidence ledger without hydrating candidate/DB state."""
        session_id = _identity(session_id)
        if not self.database_path.is_file():
            return None
        root = self._root(session_id)
        if not safe_path(root, _RECORD).is_file():
            return None
        row = self._read(root, session_id)
        return NativeResearchLedger(root, conversation_id=row["conversation_id"])

    def domain_operation_receipt(self, request):
        """Read an aged-out call's durable status; never activate/re-execute."""
        session_id = _identity(request["session_id"])
        root = self._root(session_id)
        if not safe_path(root, _RECORD).is_file():
            return None
        row = self._read(root, session_id)
        ledger = NativeAppCallLedger(root, conversation_id=row["conversation_id"])
        if not ledger.path.is_file():
            return None
        entry = ledger.read()["entries"].get(request["call_id"])
        if entry is None:
            return None
        if entry["identity"]["raw_arguments_hash"] != request["raw_arguments_hash"]:
            raise NativeDshMuxError("dsh_domain_cancel_target_mismatch", outcome_unknown=True)
        return {"state": entry["state"],
                "settlement_ref": "appcall." + fingerprint(entry["identity"])}

    def _root(self, session_id):
        return safe_path(self.root, _conversation(session_id))

    def _read(self, root, session_id):
        row = read_owned(root, _RECORD, 64 * 1024)
        legacy = type(row) is dict and row.get("contract") == _LEGACY_CONTRACT
        fields = _RECORD_FIELDS - {"compatibility_identity"} if legacy else _RECORD_FIELDS
        if (type(row) is not dict or set(row) != fields
                or row["contract"] != (_LEGACY_CONTRACT if legacy else CONTRACT)
                or row["dsh_session_id"] != session_id
                or row["conversation_id"] != _conversation(session_id)
                or row["document_kind"] not in {"native", "whiteboard"}
                or row["document_origin"] not in {"editor", "candidate_only"}
                or row["workflow"] not in {"artifact", "calculate", "build", "staged"}
                or type(row["seed_fingerprint"]) is not str
                or re.fullmatch(r"[0-9a-f]{64}", row["seed_fingerprint"]) is None):
            raise NativeDshMuxError("dsh_workspace_binding_invalid")
        _identity(row["document_id"])
        if row["writer_fence"] is not None:
            _identity(row["writer_fence"])
        pending = row["writer_takeover"]
        if pending is not None:
            if (type(pending) is not dict or set(pending) != {"previous", "proposed"}
                    or pending["previous"] != row["writer_fence"]):
                raise NativeDshMuxError("dsh_workspace_writer_invalid")
            _identity(pending["proposed"])
        assert_workspace_compatible(None if legacy else row["compatibility_identity"], self.database_path.parent)
        return row

    def _validate_retirement_workspace(self, root):
        """Narrow parser port; it cannot activate, migrate or take a writer."""
        row = read_owned(root, _RECORD, 64 * 1024)
        if type(row) is not dict:
            raise NativeDshMuxError("dsh_workspace_binding_invalid")
        session_id = _identity(row.get("dsh_session_id"))
        if Path(root).name != _conversation(session_id):
            raise NativeDshMuxError("dsh_workspace_binding_invalid")
        self._read(root, session_id)

    @staticmethod
    def _save(root, row):
        write_owned(root, _RECORD, row, 64 * 1024)

    def prepare_document(self, session_id, kind, document):
        """Reserve a document without creating any DSH message or history."""
        session_id = _identity(session_id)
        origin = "candidate_only" if kind is None and document is None else "editor"
        if kind is None and document is None:
            existing = self.lookup_binding(session_id)
            if existing is not None:
                return {"binding": existing, "existing": True}
        if kind is None and document is None:
            kind, document = "whiteboard", new_whiteboard()
        if kind not in {"native", "whiteboard"} or type(document) is not dict:
            raise NativeDshMuxError("dsh_workspace_document_invalid")
        document = copy.deepcopy(document)
        (validate_whiteboard if kind == "whiteboard" else validate_plan)(document)
        document_id = _identity(document["board_id" if kind == "whiteboard" else "plan_id"])
        with self._lock:
            self._ensure_open()
            root = self._root(session_id)
            root.mkdir(parents=True, exist_ok=True)
            with file_lock(safe_path(root, ".dsh-workspace.lock")):
                if safe_path(root, _RECORD).exists():
                    row = self._read(root, session_id)
                    if row["document_kind"] != kind or row["seed_fingerprint"] != fingerprint(document):
                        raise NativeDshMuxError("dsh_workspace_already_bound")
                    existing = True
                else:
                    identity = current_workspace_identity(self.database_path.parent)
                    seed_hash = fingerprint(document)
                    write_owned(root, "seed-" + seed_hash + ".json", document, MAX_NATIVE)
                    row = {"contract": CONTRACT, "dsh_session_id": session_id,
                        "conversation_id": _conversation(session_id), "document_kind": kind,
                        "document_id": document_id, "document_origin": origin, "workflow": self.workflow,
                        "seed_fingerprint": seed_hash, "writer_fence": None, "writer_takeover": None,
                        "compatibility_identity": identity}
                    self._save(root, row)
                    existing = False
            with self._route_lock:
                self._domain_routes[session_id] = row["conversation_id"]
            return {"binding": {"dsh_session_id": session_id,
                                "conversation_id": row["conversation_id"]}, "existing": existing}

    def prepare_prompt_document(self, session_id, kind, document):
        """Reuse a Session's bound seed; a follow-up never rebinds its document.

        The visible editor may have advanced after application, been edited by
        the user, or show another file. That must not prevent ordinary chat or
        replace the isolated candidate. Actual review/apply still uses current
        document snapshots and CAS, and explicit seed replacement stays strict.
        """
        session_id = _identity(session_id)
        with self._lock:
            existing = self.lookup_binding(session_id)
            if existing is not None:
                return {"binding": existing, "existing": True}
            return self.prepare_document(session_id, kind, document)

    def prepare_candidate_input_draft(self, session_id, draft):
        """User file import into a fresh, candidate-only workspace; no model call."""
        from genshin_db import queries

        from .candidate_input_draft import MAX_BYTES, validate_draft

        validate_draft(draft)
        assert_workspace_compatible(draft["compatibility"]["workspace_identity"], self.database_path.parent)
        if draft["compatibility"]["database_fingerprint"] != fingerprint(queries.get_meta(self.database_path)):
            raise NativeDshMuxError("candidate_input_draft_incompatible")
        with self._lock:
            if self.lookup_binding(session_id) is not None:
                raise NativeDshMuxError("candidate_input_draft_new_conversation_required")
            if self._session_status_reader is not None:
                status = self._session_status_reader(session_id)
                if self._turn_active(session_id) or status.get("last_turn") is not None:
                    raise NativeDshMuxError("candidate_input_draft_new_conversation_required")
            source = copy.deepcopy(draft["input"]["source_document"])
            if source["representation"] == "native":
                source["payload"].update(resolution={"state": "pending"}, resolved_template=None)
            root = self._root(session_id)
            if safe_path(root, "failed-import-binding.json").exists():
                self._archive_failed_input_import(session_id)
            try:
                prepared = self.prepare_document(session_id, source["representation"], source["payload"])
                row = self._read(root, session_id)
                row["document_origin"] = "candidate_only"
                self._save(root, row)
                write_owned(root, "candidate-input-import.json", draft, MAX_BYTES)
                scope = self.activate(session_id)
            except Exception:
                if session_id not in self._workspaces:
                    with self._route_lock:
                        self._domain_routes.pop(session_id, None)
                        self._route_controllers.pop(session_id, None)
                    self._archive_failed_input_import(session_id)
                raise
            return {**prepared, "summary": scope.candidate_session.summary(), "imported": True,
                    "message": "输入草稿已打开，尚未开始 AI 任务；正式白板未改变。"}

    def _archive_failed_input_import(self, session_id):
        """Move only this newly-created, unpublished private import; never user files."""
        root = self._root(session_id)
        if not root.exists():
            return
        record = safe_path(root, _RECORD)
        if record.exists():
            # Diagnostic data cannot be rediscovered as a live App binding.
            record.replace(safe_path(root, "failed-import-binding.json"))
        container = safe_path(self.root, ".failed-candidate-imports")
        container.mkdir(parents=True, exist_ok=True)
        destination = safe_path(container, str(uuid.uuid4()))
        if (root.resolve().parent != self.root.resolve()
                or destination.resolve().parent != container.resolve() or container.resolve().parent != self.root.resolve()):
            raise NativeDshMuxError("candidate_input_draft_import_archive_invalid")
        root.rename(destination)

    def _turn_active(self, session_id):
        if self._session_status_reader is None:
            return False  # isolated domain mode; production injects the DSH reader
        status = self._session_status_reader(session_id)
        if type(status) is not dict or status.get("session_id") != session_id:
            raise NativeDshMuxError("app_action_turn_state_unknown")
        running, admission = status.get("agent_running"), status.get("admission")
        if ((running is not None and type(running) is not bool)
                or type(admission) is not dict or admission.get("state") not in {"idle", "active", "queued"}):
            raise NativeDshMuxError("app_action_turn_state_unknown")
        return running is True or admission["state"] != "idle"

    def _install_writer(self, binding, root, row):
        pending = row["writer_takeover"]
        if pending is None:
            pending = {"previous": row["writer_fence"], "proposed": "wf_" + secrets.token_urlsafe(32)}
            row["writer_takeover"] = pending
            self._save(root, row)
        proposed = pending["proposed"]
        session = binding.candidate_session
        try:
            authority = SessionWriteAuthority(session, writer_fence=proposed,
                                              document_id=row["document_id"])
        except PlanError as error:
            if error.reason != "stale_context" or pending["previous"] is None:
                raise
            authority = SessionWriteAuthority(session, writer_fence=pending["previous"],
                                              document_id=row["document_id"])
            authority.transfer(expected_fence=pending["previous"], new_fence=proposed)
        row.update(writer_fence=proposed, writer_takeover=None)
        self._save(root, row)
        binding.install_writer_authority(authority)

    def _context(self, scope):
        checkpoint = scope.binding.domain_checkpoint()
        candidate = scope.candidate_session
        if self._workspace_reader is None:
            workspace = {"document_kind": candidate.kind,
                "display_name": candidate.document.get("title", "排轴方案"),
                "current_view": "whiteboard" if candidate.kind == "whiteboard" else "advanced",
                # Until the Renderer reports its actual document, a candidate
                # is not evidence that a saveable formal document is open.
                "dirty": True, "has_path": False, "can_save": False, "can_open": False,
                "can_export": False}
        else:
            workspace = self._workspace_reader(scope.conversation_id, scope.binding)
        binding = {key: checkpoint[key] for key in (
                    "document_id", "candidate_revision", "stage_revision", "receipt_fingerprint")}
        binding["applied_receipt_fingerprint"] = None
        visible = self._renderer_workspaces.get(scope.conversation_id)
        if visible is not None:
            workspace = copy.deepcopy(visible["workspace"])
            binding["document_id"] = visible["current_document_id"] or "no-active-document"
        proofs = self.application_backend._read_applied_proofs(candidate.root, scope.conversation_id)["records"]
        exact = [row for row in proofs if visible is not None
                 and visible.get("result_payload_digest") is not None
                 and row["application_payload_digest"] == visible["result_payload_digest"]
                 and row["kind"] == workspace["document_kind"]
                 and binding["document_id"] == checkpoint["document_id"]]
        if exact:
            # File operations consume the actual formal result, not the latest
            # isolated candidate. Never guess the most recent applied record:
            # the Renderer may have reopened a different historical result.
            # Multiple historical applications can prove the identical actual
            # payload. Choose a stable proof only after exact content matching;
            # the card's object identity remains the payload digest itself.
            proof = min(exact, key=lambda row: (row["revision"], row["receipt_fingerprint"]))
            binding.update(candidate_revision=proof["revision"], stage_revision=0,
                           receipt_fingerprint=proof["receipt_fingerprint"],
                           applied_receipt_fingerprint=proof["receipt_fingerprint"],
                           result_payload_digest=visible["result_payload_digest"])
        else:
            workspace["can_export"] = False
        return {"binding": binding, "workspace": workspace}

    def activate(self, session_id):
        """Materialize/open the App candidate before DSH's first model step."""
        session_id = _identity(session_id)
        with self._lock:
            self._ensure_open()
            scope = self._workspaces.get(session_id)
            if scope is not None:
                self._read(scope.session_root, session_id)
                return scope
            root = self._root(session_id)
            if not safe_path(root, _RECORD).is_file():
                raise NativeDshMuxError("dsh_domain_session_unbound")
            with file_lock(safe_path(root, ".dsh-workspace.lock")):
                row = self._read(root, session_id)
                cid, workflow = row["conversation_id"], row["workflow"]
                if safe_path(root, BINDING_FILE).is_file():
                    self._provider.configure_open(conversation_id=cid, database_path=self.database_path,
                                                  delivery_root=self.delivery_root)
                    binding = self._provider.open(conversation_id=cid, session_root=root, workflow=workflow)
                else:
                    seed = read_owned(root, "seed-" + row["seed_fingerprint"] + ".json", MAX_NATIVE)
                    if fingerprint(seed) != row["seed_fingerprint"]:
                        raise NativeDshMuxError("dsh_workspace_seed_changed")
                    self._provider.prepare_create(conversation_id=cid, workflow=workflow,
                        document_kind=row["document_kind"], document_seed=seed,
                        database_path=self.database_path, delivery_root=self.delivery_root)
                    binding = self._provider.create(conversation_id=cid, session_root=root, workflow=workflow)
                try:
                    pending_import = safe_path(root, "candidate-input-import.json")
                    if pending_import.exists() and not safe_path(root, "candidate-input-imported.json").exists():
                        from .candidate_input_draft import MAX_BYTES
                        from .candidate_input_draft_store import restore_candidate_input

                        imported = read_owned(root, "candidate-input-import.json", MAX_BYTES)
                        restore_candidate_input(binding.candidate_session, imported)
                        write_owned(root, "candidate-input-imported.json",
                                    {"artifact_id": imported["artifact_id"], "fingerprint": fingerprint(imported)}, 4096)
                    self._install_writer(binding, root, row)
                    scope = _Workspace(cid, binding, binding.candidate_session,
                        NativeResearchLedger(root, conversation_id=cid), None,
                        NativeAppCallLedger(root, conversation_id=cid),
                        diagnostic_data_dir=self.diagnostic_data_dir)
                    scope.action_controller = NativeAppActionController(
                        ledger=NativeAppActionLedger(root, conversation_id=cid),
                        context_reader=lambda: self._context(scope), callbacks=self._action_callbacks,
                        candidate_draft_store=CandidateInputDraftStore(binding.candidate_session,
                            lambda: current_workspace_identity(self.database_path.parent)),
                        diagnostic_data_dir=self.diagnostic_data_dir,
                        turn_active_reader=lambda: self._turn_active(session_id))
                    scope.resource_admission = lambda identity, effect: self._admit_tool(session_id, identity, effect)
                    scope.tool_host = create_native_app_tool_host(scope)
                    scope.tool_host.recover_interrupted_calls()
                    scope.domain_handle = NativeDomainSessionHandle(cid, root, binding,
                        scope.research_ledger, scope.action_controller, scope.app_call_ledger)
                    self.domain_registry.register(scope.domain_handle)
                    self._workspaces[session_id] = scope
                    with self._route_lock:
                        self._domain_routes[session_id] = cid
                        self._route_controllers[cid] = scope.action_controller
                except Exception:
                    binding.close()
                    raise
                return scope

    def configure_budget_ledger(self, ledger):
        """Trusted production composition only, never a model/UI command."""
        from .native_dsh_budget_ledger import NativeDshBudgetLedger

        if not isinstance(ledger, NativeDshBudgetLedger):
            raise TypeError("NativeDshBudgetLedger required")
        self._budget_ledger = ledger

    def _admit_tool(self, session_id, identity, effect):
        if self._budget_ledger is None:  # isolated domain qualification
            return
        turn = re.fullmatch(r"dsh-turn-([0-9]+)", identity["turn_id"])
        if turn is None or identity["conversation_id"] != _conversation(session_id):
            raise NativeDshMuxError("dsh_budget_tool_identity_invalid")
        self._budget_ledger.admit_tool(session_id=session_id, turn=int(turn[1]),
            claim_id=fingerprint(identity), category="read" if effect == "none" else "mutation")

    def resolve_target(self, session_id):
        try:
            if self.lookup_binding(session_id) is None:
                return DshDomainUnavailable(session_id, "dsh_document_not_bound",
                    "此会话尚未绑定白板；可继续交流，开始编辑前请打开或新建一份文档。")
            if not self.database_path.is_file():
                return DshDomainUnavailable(session_id, "database_missing",
                    "当前计算资料库文件不存在；可继续交流和查询资料，暂不能编辑计算候选。")
            scope = self.activate(session_id)
        except NativeDshMuxError as error:
            issue = compatibility_issue(error)
            if issue is None:
                raise
            return DshDomainUnavailable(session_id, issue["code"], issue["message"], issue["details"])
        except PlanError as error:
            if error.reason not in {"native_app_binding_database_changed", "agent_database_changed"}:
                raise
            return DshDomainUnavailable(session_id, error.reason,
                "这份候选使用旧版资料库，不能继续计算或应用。请在新会话中让 Agent 根据当前版本调整文件。")
        return DshDomainTarget(session_id, scope.conversation_id, scope.tool_host,
            execution_boundary=scope.action_controller.agent_call,
            model_metadata_reader=lambda: self._model_metadata(scope),
            tool_boundary=lambda tool: self._tool_boundary(scope, tool))

    @contextmanager
    def _tool_boundary(self, scope, tool):
        from .tools import NATIVE_V2_TOOL_POLICIES

        effect = NATIVE_V2_TOOL_POLICIES.get(tool, {}).get("effect")
        mutates_application_target = effect in {"candidate", "candidate_and_derived", "derived_compose",
                                               "derived_calculation", "stage_transition"}
        with scope.candidate_session._lock:
            # The Host samples full before/after state even for a pure read.
            # One coherent candidate lease prevents unrelated GUI edits from
            # being misclassified as a read's effect; it grants no writer right.
            if mutates_application_target:
                intent = self.application_backend._read_application_intent(
                    scope.candidate_session.root, scope.conversation_id)["intent"]
                if intent is not None and intent["state"] in {"prepared", "baseline_advanced"}:
                    raise NativeDshMuxError("dsh_candidate_application_pending")
            with scope.action_controller.agent_call():
                yield

    def _model_metadata(self, scope):
        from .native_prompt_v2 import (
            CONNECTION_KIND,
            RUNTIME_GENERATION,
            build_native_prompt_v2,
        )

        context = copy.deepcopy(scope.binding.runtime_context())
        from .task_report_actions import project_task_actions
        project_task_actions(context, scope)
        visible = self._renderer_workspaces.get(scope.conversation_id)
        bound_document_id = context["document"]["document_id"]
        if visible is not None and visible["current_document_id"] != bound_document_id:
            context["editor_document_notice"] = {
                "bound_document_id": bound_document_id,
                "visible_document_id": visible["current_document_id"],
                "instruction": "界面当前显示的文档与本会话候选不同。继续对话不会重绑候选；请说明操作对象。要处理另一份文档请让用户选择新会话，不能把原候选直接覆盖到它。",
            }
        unknown = [row for row in scope.app_call_ledger.read()["entries"].values()
            if row["state"] in {"inflight", "unknown_after_interruption"}]
        if unknown:
            context["unsettled_app_operations"] = {"count": len(unknown),
                "recent": [{"call_id": row["identity"]["call_id"], "tool": row["identity"]["tool_name"],
                    "state": row["state"], "reason": row["terminal_reason"]} for row in unknown[-8:]],
                "instruction": "这些操作的结果不确定。先读取当前候选和文件状态核对，不能直接重放旧调用或宣称成功；必要时向用户说明。"}
        context.pop("tool_manifest_hash", None)
        context.update(connection_kind=CONNECTION_KIND, runtime_generation=RUNTIME_GENERATION)
        dsh_session_id = read_owned(scope.candidate_session.root, _RECORD, 64 * 1024)["dsh_session_id"]
        connection = self.cli_connection(dsh_session_id)
        if connection is not None:
            from .native_cli_launch import cli_launch_descriptor
            context["candidate_cli"] = {"connection_file": connection,
                "session_id": read_owned(scope.candidate_session.root, _RECORD, 64 * 1024)["dsh_session_id"],
                "commands": ["agent candidate read", "agent candidate edit", "agent candidate compose", "agent candidate calculate"],
                "launch": cli_launch_descriptor(),
                "authority": "same live candidate; edits use revision CAS; no formal document sync or user approval"}
        prompt = build_native_prompt_v2(workflow=context["workflow"], runtime_context=context)
        resources = list(dict.fromkeys([prompt.required_skill_resource,
            *context.get("required_skill_resources_once", [])]))
        context["required_skill_resources_once"] = resources
        from .skill_context import current_focus_card
        focus_card = current_focus_card(context)
        return {"runtime_context": context, "prompt_bundle": {
            "system_prompt": prompt.system_prompt, "required_skill_resources": resources,
            "current_focus_card": focus_card}}

    def lookup_binding(self, session_id):
        """Read identity only; never activate a candidate or wake a model."""
        session_id = _identity(session_id)
        with self._lock:
            self._ensure_open()
            root = self._root(session_id)
            if not safe_path(root, _RECORD).is_file():
                return None
            row = self._read(root, session_id)
            return {"dsh_session_id": session_id, "conversation_id": row["conversation_id"]}

    def operation_admission(self, session_id, call_id):
        """Read an existing App ledger for diagnosis, without activating work."""
        with self._lock:
            scope = self._workspaces.get(session_id)
        if scope is None:
            return "unknown"
        entry = scope.app_call_ledger.read()["entries"].get(call_id)
        return "not_recorded" if entry is None else entry["state"]

    def _conversation_scope(self, conversation_id):
        """Resolve an App identity, never search by title or current selection."""
        conversation_id = _identity(conversation_id)
        with self._lock:
            self._ensure_open()
            root = safe_path(self.root, conversation_id)
            if not safe_path(root, _RECORD).is_file():
                raise NativeDshMuxError("dsh_domain_session_unbound")
            row = read_owned(root, _RECORD, 64 * 1024)
            session_id = _identity(row.get("dsh_session_id"))
            if conversation_id != _conversation(session_id):
                raise NativeDshMuxError("dsh_workspace_binding_invalid")
            self._read(root, session_id)
            return self.activate(session_id)

    def candidate_summary(self, conversation_id):
        self._conversation_scope(conversation_id)
        return self.projection_port.summary(conversation_id)

    def incompatible_applications(self):
        """List only stale App effects; DSH history has no approval authority."""
        with self._lock:
            self._ensure_open()
            return self.application_backend.incompatible_applications_repository.list_pending()

    def abandon_incompatible_application(self, **request):
        """User-only release of one exact stale intent, never a model tool."""
        with self._lock:
            self._ensure_open()
            return self.application_backend.incompatible_applications_repository.abandon(**request)

    def candidate_read(self, conversation_id, resource, pointer="", offset=0, limit=50):
        self._conversation_scope(conversation_id)
        return self.projection_port.read(conversation_id, resource, pointer, offset, limit)

    def domain_approvals(self, conversation_id):
        from .native_dsh_domain_approvals import approval_cards

        scope = self._conversation_scope(conversation_id)
        with scope.candidate_session._lock:
            return {"approvals": approval_cards(scope.candidate_session)}

    def update_workspace_state(self, conversation_id, **request):
        from .native_dsh_file_actions import workspace_state

        checked = workspace_state(request)
        with self._lock:
            self._conversation_scope(conversation_id)
            self._renderer_workspaces[conversation_id] = checked
        return {"status": "updated"}

    def app_actions(self, conversation_id):
        scope = self._conversation_scope(conversation_id)
        return {"actions": scope.action_controller.list_cards()}

    def app_action(self, conversation_id, command, arguments):
        from .native_dsh_file_actions import dispatch_action

        self._conversation_scope(conversation_id)
        with self.domain_registry.action_scope(conversation_id) as handle:
            return dispatch_action(handle.action_controller, command, arguments)

    def publish_candidate_input_draft(self, conversation_id, **arguments):
        self._conversation_scope(conversation_id)
        with self.domain_registry.action_scope(conversation_id) as handle:
            return handle.action_controller.publish_candidate_input_draft(**arguments)

    def request_candidate_input_draft(self, conversation_id, *, expected_revision):
        self._conversation_scope(conversation_id)
        with self.domain_registry.action_scope(conversation_id) as handle:
            return handle.action_controller.request_candidate_input_draft(expected_revision=expected_revision)

    def whiteboard_projection(self, conversation_id, command, arguments):
        if type(arguments) is not dict:
            raise NativeDshMuxError("dsh_whiteboard_projection_request_invalid")
        self._conversation_scope(conversation_id)
        fields = {"current_document", "current_payload"}
        if command == "query" and set(arguments) == fields:
            return self.application_backend.query_last_applied_whiteboard_projection(conversation_id, **arguments)
        if command == "recover" and set(arguments) == fields | {"allow_unsaved_target"}:
            with self.domain_registry.action_scope(conversation_id):
                return self.application_backend.recover_last_applied_whiteboard_projection(conversation_id, **arguments)
        raise NativeDshMuxError("dsh_whiteboard_projection_request_invalid")

    def saved_whiteboard(self, conversation_id, command, arguments):
        from .native_saved_whiteboard import saved_whiteboard

        self._conversation_scope(conversation_id)
        boundary = self.domain_registry.read_scope if command == "inspect" else self.domain_registry.action_scope
        with boundary(conversation_id) as handle:
            scope = self._conversation_scope(conversation_id)
            # Touch the live binding before issuing document delivery rights.
            handle.binding.domain_checkpoint()
            return saved_whiteboard(self.application_backend, scope, command, arguments)

    def decide_domain_approval(self, conversation_id, **request):
        from .native_dsh_domain_approvals import decide_approval

        self._conversation_scope(conversation_id)
        with self.domain_registry.action_scope(conversation_id) as handle, handle.binding.domain_write_boundary():
            return decide_approval(handle.binding.candidate_session, request)

    def domain_projection(self, session_id):
        binding = self.lookup_binding(session_id)
        if binding is None or not safe_path(self._root(session_id), BINDING_FILE).is_file():
            return None
        scope = self.activate(session_id)
        with scope.candidate_session._lock:
            checkpoint = scope.binding.domain_checkpoint()
            summary = scope.candidate_session.summary()
            intent = self.application_backend._read_application_intent(
                scope.candidate_session.root, scope.conversation_id)["intent"]
            application_state = "none" if intent is None or any(intent[key] != summary[key] for key in (
                "kind", "revision", "receipt_fingerprint")) else intent["state"]
            verified = summary.get("can_accept") is True and summary.get("receipt_fingerprint") is not None
            if verified and scope.candidate_session.calculated is not None:
                scope.candidate_session._validate_calculated(scope.candidate_session.calculated)
            commands = []
            if verified and application_state in {"none", "aborted"}:
                commands.extend({"command_id": name, "enabled": True,
                    "risk": "review", "scope": {}, "invocation_kind": "host_command", "reason_code": None}
                    for name in ("candidate.review", "candidate.apply.prepare"))
                row = self._read(scope.candidate_session.root, session_id)
                if (row["document_origin"] == "candidate_only" and not
                        self.application_backend._read_applied_proofs(
                            scope.candidate_session.root, scope.conversation_id)["records"]):
                    commands.append({"command_id": "candidate.review.new", "enabled": True,
                        "risk": "review", "scope": {}, "invocation_kind": "host_command", "reason_code": None})
            elif application_state in {"prepared", "baseline_advanced"}:
                commands.extend({"command_id": name, "enabled": True,
                    "risk": "app_side_effect", "scope": {}, "invocation_kind": "host_command", "reason_code": None}
                    for name in ("application.ack", "application.abort", "application.recover"))
            projection = project_workspace_candidate(checkpoint, summary, commands=commands,
                review_verified=verified, application_state=application_state)
        return {**binding, "projection": projection}

    def candidate_command(self, request):
        required = {"conversation_id", "command_id", "expected_projection_revision", "arguments"}
        if type(request) is not dict or set(request) != required or type(request["arguments"]) is not dict:
            raise NativeDshMuxError("dsh_candidate_command_invalid")
        with self._lock:
            scope = self._conversation_scope(request["conversation_id"])
            sid = self._read(scope.candidate_session.root,
                read_owned(scope.candidate_session.root, _RECORD, 64 * 1024)["dsh_session_id"])["dsh_session_id"]
            with scope.candidate_session._lock:
                projection = self.domain_projection(sid)["projection"]
                if request["expected_projection_revision"] != projection["projection_revision"]:
                    raise NativeDshMuxError("dsh_candidate_projection_stale")
                if not any(item["command_id"] == request["command_id"] and item["enabled"]
                           for item in projection["available_commands"]):
                    raise NativeDshMuxError("dsh_candidate_command_unavailable")
                args = request["arguments"]
                base = {"kind", "document", "expected_revision", "expected_receipt"}
                command = request["command_id"]
                if command == "candidate.review" and set(args) == base:
                    result = self.application_port.accept(scope.conversation_id, **args)
                elif command == "candidate.review.new" and set(args) == base and args["document"] is None:
                    # This is explicitly a review for creating a document. The
                    # immutable seed validates candidate lineage only; it has
                    # never been claimed as an existing Renderer document.
                    result = self.application_port.accept(scope.conversation_id,
                        **{**args, "document": scope.candidate_session.document})
                    result["document_creation"] = {"kind": args["kind"],
                        "seed": copy.deepcopy(scope.candidate_session.document), "expected_absent": True}
                elif command == "candidate.apply.prepare" and set(args) == base | {"baseline_payload", "target_payload"}:
                    result = self.application_port.prepare_candidate_application(scope.conversation_id, **args)
                elif command == "application.ack" and set(args) == base | {"actual_payload"}:
                    result = self.application_port.record_candidate_applied(scope.conversation_id, **args)
                elif command == "application.abort" and set(args) == (base - {"document"}) | {"current_document", "current_payload"}:
                    result = self.application_port.abort_candidate_application(scope.conversation_id, **args)
                elif command == "application.recover" and set(args) == {"current_document", "current_payload", "discard_current"}:
                    result = self.application_port.recover_candidate_application(scope.conversation_id, **args)
                else:
                    raise NativeDshMuxError("dsh_candidate_command_arguments_invalid")
                return {"result": result, "projection": self.domain_projection(sid)["projection"]}

    def close(self):
        self.close_cli_link()
        with self._lock:
            self._closed = True
            scopes, self._workspaces = self._workspaces, {}
            self._renderer_workspaces.clear()
            with self._route_lock:
                self._domain_routes.clear()
                self._route_controllers.clear()
        for scope in scopes.values():
            if scope.domain_handle is not None:
                self.domain_registry.unregister(scope.conversation_id, expected_handle=scope.domain_handle)
            scope.binding.close()
