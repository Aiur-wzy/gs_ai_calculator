"""Production CandidateSession binding for the DSH V2 native App service.

The service protocol intentionally does not accept a document or a filesystem
path from a model turn.  The App prepares one single-use create seed before it
asks the service to create a conversation.  Reopening uses an App-owned
storage configuration (or an injected configuration resolver) and verifies it
against a small, conversation-local binding record before loading the
candidate checkpoint.

CandidateSession remains the sole source of mutable domain truth.  This module
only binds that truth to the conversation identity and projects the narrow
runtime/checkpoint views required by ``NativeAppServiceV2``.
"""

from __future__ import annotations

import copy
import json
import os
import re
import stat
import threading
import uuid
from collections.abc import Callable, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from genshin_app.equipment.files import fingerprint, input_view
from genshin_app.equipment.schema import PlanError
from genshin_db.locking import file_lock

from . import tools
from .artifact_candidate import ArtifactCandidateSession
from .board_candidate import BoardCandidateSession
from .candidate import CandidateSession
from .native_app_action_tools import definitions as action_definitions
from .native_app_tool_catalog import build_native_app_tool_snapshot
from .native_app_tool_host import native_app_state_hash
from .native_research_tools import definitions as research_definitions
from .native_write_ledger import SessionWriteAuthority
from .sessions import safe_path, write_owned
from .staged_candidate import StagedCandidateSession

BINDING_CONTRACT = "genshin-native-app-binding-v1"
TOOL_MANIFEST_CONTRACT = "genshin-native-binding-tool-manifest-v1"
BINDING_FILE = "native-app-binding.json"
BINDING_LOCK_FILE = ".native-app-binding.lock"
MAX_BINDING_BYTES = 64 * 1024

_WORKFLOWS = frozenset({"build", "calculate", "staged", "artifact"})
_KINDS = frozenset({"native", "whiteboard"})
_HASH = re.compile(r"[0-9a-f]{64}\Z")
_IDENTITY = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}\Z")
_RECORD_KEYS = frozenset({
    "contract",
    "conversation_id",
    "session_id",
    "workflow",
    "document_kind",
    "document_id",
    "database_fingerprint",
    "database_location_hash",
    "delivery_location_hash",
    "session_root_hash",
    "record_hash",
})


def _fail(
    message: str,
    reason: str = "native_app_binding_invalid",
    details: Mapping[str, Any] | None = None,
) -> None:
    raise PlanError(message, reason, None if details is None else dict(details))


def _identity(value: object, name: str) -> str:
    if not isinstance(value, str) or _IDENTITY.fullmatch(value) is None:
        _fail(f"{name}不符合原生绑定身份语法")
    return value


def _digest(value: object, name: str) -> str:
    if not isinstance(value, str) or _HASH.fullmatch(value) is None:
        _fail(f"{name}必须是小写SHA-256")
    return value


def _workflow(value: object) -> str:
    if value not in _WORKFLOWS:
        _fail("原生绑定工作流不受支持", "native_app_binding_workflow_invalid")
    return str(value)


def _document_kind(value: object) -> str:
    if value not in _KINDS:
        _fail("原生绑定文档类型不受支持", "native_app_binding_document_invalid")
    return str(value)


def _validate_workflow_kind(workflow: str, kind: str) -> None:
    if workflow in {"build", "staged"} and kind != "whiteboard":
        _fail(
            "白板或分阶段工作流只能从白板文档创建",
            "native_app_binding_document_invalid",
        )


def _document_id(session: object) -> str:
    kind = _document_kind(getattr(session, "kind", None))
    document = getattr(session, "document", None)
    if not isinstance(document, dict):
        _fail("候选会话缺少受管文档", "native_app_binding_document_invalid")
    key = "board_id" if kind == "whiteboard" else "plan_id"
    return _identity(document.get(key), "document_id")


def _path_components(path: Path) -> list[Path]:
    return [*reversed(path.parents), path]


def _reject_unsafe_existing_path(path: Path, *, expect_file: bool | None) -> None:
    """Apply the same no-link/no-reparse trust rule as session-owned files."""

    for component in _path_components(path):
        if not component.exists() and not component.is_symlink():
            continue
        try:
            info = component.lstat()
        except OSError as error:
            raise PlanError(
                "App配置路径不可读取",
                "native_app_binding_storage_invalid",
            ) from error
        if component.is_symlink() or getattr(info, "st_file_attributes", 0) & getattr(
            stat, "FILE_ATTRIBUTE_REPARSE_POINT", 1024,
        ):
            _fail("App配置路径不允许链接或重解析点", "native_app_binding_storage_invalid")
    if expect_file is True and (not path.exists() or not path.is_file()):
        _fail("App配置的数据库文件不存在", "native_app_binding_storage_invalid")
    if expect_file is False and path.exists() and not path.is_dir():
        _fail("App配置的交付位置不是目录", "native_app_binding_storage_invalid")


def _database_path(value: object) -> Path:
    if not isinstance(value, (str, os.PathLike)):
        _fail("App必须提供数据库路径", "native_app_binding_storage_invalid")
    path = Path(value).absolute()
    _reject_unsafe_existing_path(path, expect_file=True)
    return path.resolve(strict=True)


def _delivery_path(value: object | None, *, database_path: Path) -> Path:
    path = (
        database_path.parent / "agent_deliveries"
        if value is None
        else Path(value).absolute()
        if isinstance(value, (str, os.PathLike))
        else None
    )
    if path is None:
        _fail("App交付目录配置不合法", "native_app_binding_storage_invalid")
    _reject_unsafe_existing_path(path, expect_file=False)
    return path.resolve(strict=False)


def _location_hash(path: Path) -> str:
    canonical = os.path.normcase(str(path.resolve(strict=False)))
    return fingerprint({"canonical_location": canonical})


def _root(session_root: object) -> Path:
    if not isinstance(session_root, (str, os.PathLike)):
        _fail("原生绑定会话目录不合法", "native_app_binding_storage_invalid")
    root = Path(session_root).absolute()
    if not root.is_dir():
        _fail("原生绑定会话目录不存在", "native_app_binding_storage_invalid")
    # safe_path validates the complete existing ancestor chain and the future
    # binding target without trusting a caller-selected child name.
    safe_path(root, BINDING_FILE)
    safe_path(root, BINDING_LOCK_FILE)
    return root


def _strict_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def _read_record(root: Path) -> dict[str, Any]:
    path = safe_path(root, BINDING_FILE)
    try:
        with path.open("rb") as stream:
            raw = stream.read(MAX_BINDING_BYTES + 1)
    except OSError as error:
        raise PlanError(
            "原生绑定记录不存在或不可读取",
            "native_app_binding_missing",
        ) from error
    if len(raw) > MAX_BINDING_BYTES:
        _fail("原生绑定记录超过大小限制")
    try:
        value = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=_strict_object,
            parse_constant=lambda _value: (_ for _ in ()).throw(ValueError("non-finite")),
        )
    except (UnicodeError, ValueError, RecursionError) as error:
        raise PlanError(
            "原生绑定记录不是严格JSON",
            "native_app_binding_invalid",
        ) from error
    return _validate_record(value)


def _validate_record(value: object) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != _RECORD_KEYS:
        _fail("原生绑定记录字段不合法")
    row = copy.deepcopy(value)
    if row.get("contract") != BINDING_CONTRACT:
        _fail("原生绑定记录版本不受支持")
    _identity(row.get("conversation_id"), "conversation_id")
    try:
        uuid.UUID(row.get("session_id"))
    except (AttributeError, TypeError, ValueError) as error:
        raise PlanError("候选会话身份不合法", "native_app_binding_invalid") from error
    workflow = _workflow(row.get("workflow"))
    kind = _document_kind(row.get("document_kind"))
    _validate_workflow_kind(workflow, kind)
    _identity(row.get("document_id"), "document_id")
    for name in (
        "database_fingerprint",
        "database_location_hash",
        "delivery_location_hash",
        "session_root_hash",
        "record_hash",
    ):
        _digest(row.get(name), name)
    body = {key: row[key] for key in row if key != "record_hash"}
    if fingerprint(body) != row["record_hash"]:
        _fail("原生绑定记录哈希不匹配")
    return row


@dataclass(frozen=True)
class NativeAppBindingStorageV2:
    """App-owned paths used to create or reopen one bound candidate."""

    database_path: Path
    delivery_root: Path

    @classmethod
    def normalize(
        cls,
        *,
        database_path: object,
        delivery_root: object | None = None,
    ) -> NativeAppBindingStorageV2:
        database = _database_path(database_path)
        delivery = _delivery_path(delivery_root, database_path=database)
        return cls(database_path=database, delivery_root=delivery)


@dataclass(frozen=True)
class _CreateSeed:
    conversation_id: str
    workflow: str
    document_kind: str
    document: dict[str, Any]
    storage: NativeAppBindingStorageV2


def _normalized_storage(value: object) -> NativeAppBindingStorageV2:
    if isinstance(value, NativeAppBindingStorageV2):
        return NativeAppBindingStorageV2.normalize(
            database_path=value.database_path,
            delivery_root=value.delivery_root,
        )
    if isinstance(value, Mapping) and set(value) == {"database_path", "delivery_root"}:
        return NativeAppBindingStorageV2.normalize(
            database_path=value["database_path"],
            delivery_root=value["delivery_root"],
        )
    _fail("App没有提供可恢复的绑定存储配置", "native_app_binding_config_missing")


def _workflow_stage(session: object) -> str:
    value = getattr(session, "authorization_stage", getattr(session, "workflow_stage", None))
    if value is None:
        value = getattr(session, "workflow", None)
    return _identity(value, "workflow_stage")


def _stage_revision(session: object) -> int:
    stage_state = getattr(session, "stage_state", None)
    value = stage_state.get("stage_revision", 0) if isinstance(stage_state, dict) else 0
    if type(value) is not int or value < 0 or value > 2**53 - 1:
        _fail("候选阶段修订不合法")
    return value


def _tool_manifest(session: object) -> dict[str, Any]:
    definitions = [
        *tools.definitions_for_session(session),
        *research_definitions(),
        *action_definitions(),
    ]
    detached: list[dict[str, Any]] = []
    names: set[str] = set()
    for value in definitions:
        if not isinstance(value, dict) or set(value) != {
            "type", "name", "description", "inputSchema",
        }:
            _fail("原生绑定工具目录结构不合法")
        row = copy.deepcopy(value)
        name = _identity(row.get("name"), "tool_name")
        if name in names or name in {"web_search", "web_fetch"}:
            _fail("原生绑定工具目录名称冲突")
        names.add(name)
        detached.append(row)
    detached.sort(key=lambda item: item["name"])
    return {
        "contract": TOOL_MANIFEST_CONTRACT,
        "workflow": _workflow(getattr(session, "workflow", None)),
        "workflow_stage": _workflow_stage(session),
        "app_tools": detached,
        # These are owned and versioned by the DSH composition.  Listing only
        # their identities here binds their presence without pretending they
        # are App-hosted tools or placing them in the App snapshot.
        "official_dsh_tools": ["web_fetch", "web_search"],
    }


class CandidateNativeAppBindingV2:
    """One live service binding backed by exactly one CandidateSession."""

    def __init__(
        self,
        *,
        conversation_id: str,
        workflow: str,
        record: Mapping[str, Any],
        candidate_session: CandidateSession,
    ) -> None:
        self._conversation_id = _identity(conversation_id, "conversation_id")
        self._workflow = _workflow(workflow)
        self._record = _validate_record(dict(record))
        self.candidate_session = candidate_session
        self._lock = threading.RLock()
        self._write_boundary_state = threading.local()
        self._authority: SessionWriteAuthority | None = None
        self._closed = False
        self._assert_bound_identity()

    def _assert_open(self) -> None:
        if self._closed or getattr(self.candidate_session, "closed", False):
            _fail("原生绑定已经关闭", "native_app_binding_closed")

    def _assert_database_current(self) -> None:
        try:
            with self.candidate_session._read_db(check=True):
                pass
        except PlanError as error:
            if error.reason == "agent_database_changed":
                raise PlanError(
                    "原生绑定数据库来源已经变化",
                    "native_app_binding_database_changed",
                ) from error
            raise

    def _assert_bound_identity(self) -> None:
        session = self.candidate_session
        if (
            self._record["conversation_id"] != self._conversation_id
            or self._record["workflow"] != self._workflow
            or getattr(session, "workflow", None) != self._workflow
            or self._record["session_id"] != getattr(session, "session_id", None)
            or self._record["document_kind"] != getattr(session, "kind", None)
            or self._record["document_id"] != _document_id(session)
            or self._record["database_fingerprint"]
            != fingerprint(getattr(session, "database_snapshot", None))
            or self._record["database_location_hash"]
            != _location_hash(Path(session.db_path))
            or self._record["delivery_location_hash"]
            != _location_hash(Path(session.delivery_root))
            or self._record["session_root_hash"]
            != _location_hash(Path(session.root))
        ):
            _fail("原生绑定与候选会话身份不一致", "native_app_binding_mismatch")

    def domain_checkpoint(self) -> dict[str, Any]:
        # Candidate writers already hold the session RLock when they ask for
        # post-write manifest/domain facts.  Keep the universal order
        # session -> binding to avoid an AB/BA cycle with that callback path.
        with self.candidate_session._lock, self._lock:
            self._assert_open()
            self._assert_bound_identity()
            self._assert_database_current()
            summary = self.candidate_session.summary()
            revision = getattr(self.candidate_session, "revision", None)
            if type(revision) is not int or not 0 <= revision <= 2**53 - 1:
                _fail("候选修订不合法")
            receipt = summary.get("receipt_fingerprint")
            if receipt is not None:
                _digest(receipt, "receipt_fingerprint")
            return {
                "document_id": self._record["document_id"],
                "candidate_revision": revision,
                "stage_revision": _stage_revision(self.candidate_session),
                "database_fingerprint": self._record["database_fingerprint"],
                "receipt_fingerprint": receipt,
            }

    def runtime_context(self) -> dict[str, Any]:
        with self.candidate_session._lock, self._lock:
            checkpoint = self.domain_checkpoint()
            summary = self.candidate_session.summary()
            definitions = tools.definitions_for_session(self.candidate_session)
            allowed_tools = sorted(_identity(row.get("name"), "tool_name") for row in definitions)
            app_tools = sorted(
                _identity(row.get("name"), "tool_name") for row in action_definitions()
            )
            research_tools = sorted({
                *(
                    _identity(row.get("name"), "tool_name")
                    for row in research_definitions()
                ),
                "web_fetch",
                "web_search",
            })
            recommended_tools = tools.recommended_tools_for_session(
                self.candidate_session
            )
            resources: list[str] = ["SKILL.md"]
            stage_context: dict[str, Any] | None = None
            if self._workflow in {"staged", "artifact"}:
                disclosure = (self.candidate_session.stage_context() if self._workflow == "artifact"
                              else tools.staged_disclosure(self.candidate_session) or {})
                recommended_tools = disclosure.get("recommended_tools", recommended_tools)
                resources.append("references/workflow/index.md" if self._workflow == "artifact"
                                 else "references/workflow/context-use.md")
                current_stage_skill = disclosure.get("current_stage_skill")
                if isinstance(current_stage_skill, str) and current_stage_skill not in resources:
                    resources.append(current_stage_skill)
                stage_context = {
                    "focus": self.candidate_session.workflow_stage,
                    "focus_is_authorization": self._workflow != "artifact",
                    "representation": self.candidate_session.stage_state["representation"],
                    "current_stage_skill": current_stage_skill,
                    "recommended_skill_resources": copy.deepcopy(
                        disclosure.get("recommended_skill_resources", [])
                    ),
                    "required_context_reads": copy.deepcopy(
                        disclosure.get("required_context_reads", [])
                    ),
                    "recommended_context_reads": copy.deepcopy(
                        disclosure.get("recommended_context_reads", [])
                    ),
                    "recommended_tools": copy.deepcopy(recommended_tools),
                }
                if "current_problem" in disclosure:
                    stage_context["current_problem"] = copy.deepcopy(disclosure["current_problem"])
                if "work_card_recommendation" in disclosure:
                    stage_context["work_card_recommendation"] = copy.deepcopy(disclosure["work_card_recommendation"])
            result = {
                "workflow": self._workflow,
                "workflow_stage": self.workflow_stage(),
                "required_skill_resources_once": resources,
                "skill_access": tools.skill_access_descriptor(),
                "document": {
                    "document_id": checkpoint["document_id"],
                    "document_kind": self._record["document_kind"],
                },
                "candidate": {
                    "revision": checkpoint["candidate_revision"],
                    "stage_revision": checkpoint["stage_revision"],
                    "has_calculation": bool(summary.get("has_calculation")),
                    "ready": bool(summary.get("ready")),
                    "representation": summary.get("representation"),
                    "application_kind": summary.get("application_kind"),
                    "ready_scope": summary.get("ready_scope", "none"),
                    "assessment": summary.get("assessment", "unknown"),
                    "receipt_fingerprint": checkpoint["receipt_fingerprint"],
                    "missing_inputs_total": int(summary.get("missing_inputs_total", 0)),
                    "issues_total": int(summary.get("issues_total", 0)),
                    "finding_count": int(summary.get("finding_count", 0)),
                },
                "database_fingerprint": checkpoint["database_fingerprint"],
                "tool_manifest_hash": self.tool_manifest_hash(),
                "allowed_domain_tools": allowed_tools,
                "tool_capabilities": {
                    "domain_tools": allowed_tools,
                    "app_tools": app_tools,
                    "research_tools": research_tools,
                    "recommended_for_stage": copy.deepcopy(recommended_tools),
                },
            }
            if stage_context is not None:
                result["stage"] = stage_context
            from .feedback_context import feedback_context
            result["candidate"].update(feedback_context(summary))
            execution = summary.get("execution_feedback")
            if isinstance(execution, dict):
                # The request context accepts integer counts and status text.
                # Fractional timing and energy stay in the diagnostic tool
                # response, whose wire contract carries exact finite numbers.
                result["candidate"]["execution_feedback"] = {
                    key: execution[key] for key in (
                        "composition", "composition_missing_count", "execution",
                        "complete_input_window", "elemental", "special_reactions",
                        "reported_hits", "declared_manual_hits",
                    ) if key in execution
                }
            return result

    def tool_manifest_hash(self) -> str:
        with self.candidate_session._lock, self._lock:
            self._assert_open()
            self._assert_bound_identity()
            return fingerprint(_tool_manifest(self.candidate_session))

    def app_state_hash(
        self,
        *,
        runtime_generation: str,
        writer_fence: str,
    ) -> str:
        """Hash the exact live App state under the candidate/binding lock.

        Cold UNKNOWN reconciliation must use the same complete vector as the
        tool Host.  Domain revision plus catalog hash alone cannot distinguish
        a committed write from a rollback that happened to retain revisions.
        """

        with self.candidate_session._lock, self._lock:
            self._assert_open()
            self._assert_bound_identity()
            self._assert_database_current()
            snapshot = build_native_app_tool_snapshot(
                self.candidate_session,
                document_id=self._record["document_id"],
                database_fingerprint=self._record["database_fingerprint"],
                tool_manifest_hash=self.tool_manifest_hash(),
                runtime_generation=_identity(
                    runtime_generation, "runtime_generation",
                ),
                writer_fence=_identity(writer_fence, "writer_fence"),
            )
            return native_app_state_hash(self.candidate_session, snapshot)

    def _checked_domain_tool(self, name: object, *, write: bool) -> str:
        name = _identity(name, "tool_name")
        policy = tools.NATIVE_V2_TOOL_POLICIES.get(name)
        visible = {
            row.get("name")
            for row in tools.definitions_for_session(self.candidate_session)
            if isinstance(row, dict)
        }
        if policy is None or name not in visible:
            _fail("领域工具不在当前阶段目录", "agent_tool_forbidden")
        effect = policy.get("effect")
        if not isinstance(effect, str) or (effect != "none") != write:
            _fail("领域工具与读写入口不匹配", "native_app_domain_route_invalid")
        return name

    def _current_writer_authority(self) -> SessionWriteAuthority:
        self._assert_open()
        authority = self._authority
        if authority is None:
            _fail(
                "原生会话尚未安装可用writer fence",
                "native_app_writer_not_installed",
            )
        # A fence is an injected capability.  Never discover a replacement
        # from persistence after this authority becomes stale: doing so would
        # let an old App process silently follow a writer takeover.
        authority.current_fence()
        return authority

    def install_writer_authority(self, authority: SessionWriteAuthority) -> None:
        """Attach the exact authority activated by the trusted coordinator.

        Cold resume must inject the object returned by the writer activation
        operation.  This binding deliberately cannot reconstruct a capability
        by reading the newest fence from disk.
        """

        if not isinstance(authority, SessionWriteAuthority):
            raise TypeError("authority must be SessionWriteAuthority")
        with self.candidate_session._lock, self._lock:
            self._assert_open()
            self._assert_bound_identity()
            self._assert_database_current()
            if (
                authority.session is not self.candidate_session
                or authority.lock is not self.candidate_session._lock
                or authority.document_id != self._record["document_id"]
                or authority.root != Path(self.candidate_session.root).absolute()
                or authority.db_path != Path(self.candidate_session.db_path).resolve()
            ):
                _fail("writer authority与原生绑定不一致", "stale_context")
            authority.current_fence()
            if self._authority is not None and self._authority is not authority:
                _fail("原生绑定已经安装其他writer authority", "stale_context")
            self._authority = authority

    @contextmanager
    def domain_write_boundary(self):
        """Hold one writer capability from App-call admission through settle."""

        with self.candidate_session._lock, self._lock:
            self._assert_open()
            self._assert_bound_identity()
            self._assert_database_current()
            authority = self._current_writer_authority()
            with authority.atomic_boundary():
                depth = getattr(self._write_boundary_state, "depth", 0)
                self._write_boundary_state.depth = depth + 1
                try:
                    yield
                finally:
                    if depth:
                        self._write_boundary_state.depth = depth
                    else:
                        del self._write_boundary_state.depth

    @contextmanager
    def calculation_session_boundary(self, plan: Mapping[str, Any]):
        """Lend the exact live CandidateSession to an App calculation/export.

        This is an in-process App capability, never a Renderer/model payload.
        The supplied plan must be the current native candidate byte-for-byte
        after excluding derived resolution caches.  Holding the writer
        boundary is intentional: calculation can publish the session-owned D2
        usage receipt even though it does not revise the candidate document.
        """

        if not isinstance(plan, Mapping):
            _fail(
                "当前计算方案不是有效的原生候选",
                "native_candidate_session_plan_invalid",
            )
        try:
            requested = copy.deepcopy(dict(plan))
        except (TypeError, ValueError, RecursionError) as error:
            raise PlanError(
                "当前计算方案无法安全复制",
                "native_candidate_session_plan_invalid",
            ) from error
        with self.domain_write_boundary():
            if self._record["document_kind"] != "native":
                _fail(
                    "当前原生会话不是排轴方案会话",
                    "native_candidate_session_kind_invalid",
                )
            candidate = getattr(self.candidate_session, "candidate", None)
            if not isinstance(candidate, dict):
                _fail(
                    "当前原生会话缺少可计算候选",
                    "native_candidate_session_plan_invalid",
                )
            try:
                same_plan = fingerprint(input_view(candidate)) == fingerprint(
                    input_view(requested)
                )
            except (TypeError, ValueError, RecursionError) as error:
                raise PlanError(
                    "当前计算方案无法核对",
                    "native_candidate_session_plan_invalid",
                ) from error
            if not same_plan:
                _fail(
                    "当前方案与原生Agent会话中的候选不一致，不能借用该会话的临时资料权限",
                    "native_candidate_session_plan_mismatch",
                )
            yield self.candidate_session

    def current_writer_fence(self) -> str:
        """Return the live App capability after checking its durable lease."""

        with self.candidate_session._lock, self._lock:
            self._assert_bound_identity()
            self._assert_database_current()
            return self._current_writer_authority().current_fence()

    def read_domain_tool(
        self,
        name: str,
        arguments: Mapping[str, Any],
    ) -> dict[str, Any]:
        """Execute a current-stage pure domain read without writer authority."""

        with self.candidate_session._lock, self._lock:
            self._assert_open()
            self._assert_bound_identity()
            name = self._checked_domain_tool(name, write=False)
            return tools.call_tool(
                self.candidate_session,
                name,
                copy.deepcopy(dict(arguments)),
            )

    def execute_domain_tool(
        self,
        name: str,
        arguments: Mapping[str, Any],
    ) -> dict[str, Any]:
        """Execute one stateful domain tool inside the writer/DB boundary."""

        if getattr(self._write_boundary_state, "depth", 0):
            with self.candidate_session._lock, self._lock:
                self._assert_open()
                self._assert_bound_identity()
                name = self._checked_domain_tool(name, write=True)
                return tools.call_tool(
                    self.candidate_session,
                    name,
                    copy.deepcopy(dict(arguments)),
                )
        with self.domain_write_boundary():
            return self.execute_domain_tool(name, arguments)

    def live_tool_binding(self, *, runtime_generation: str) -> dict[str, Any]:
        """Project the five live identities required by NativeAppToolHost."""

        runtime_generation = _identity(runtime_generation, "runtime_generation")
        with self.candidate_session._lock, self._lock:
            checkpoint = self.domain_checkpoint()
            return {
                "document_id": checkpoint["document_id"],
                "database_fingerprint": checkpoint["database_fingerprint"],
                "tool_manifest_hash": self.tool_manifest_hash(),
                "runtime_generation": runtime_generation,
                "writer_fence": self.current_writer_fence(),
            }

    def workflow_stage(self) -> str:
        with self.candidate_session._lock, self._lock:
            self._assert_open()
            return _workflow_stage(self.candidate_session)

    def install_writer_fence(self, writer_fence: str) -> None:
        writer_fence = _identity(writer_fence, "writer_fence")
        with self.candidate_session._lock, self._lock:
            self._assert_open()
            self._assert_bound_identity()
            self._assert_database_current()
            if self._authority is None:
                self._authority = SessionWriteAuthority(
                    self.candidate_session,
                    writer_fence=writer_fence,
                    document_id=self._record["document_id"],
                )
                return
            current = self._authority.current_fence()
            if current != writer_fence:
                self._authority.transfer(
                    expected_fence=current,
                    new_fence=writer_fence,
                )

    def close(self) -> None:
        with self.candidate_session._lock, self._lock:
            if self._closed:
                return
            try:
                self.candidate_session.close()
            finally:
                self._authority = None
                self._closed = True


StorageResolver = Callable[[str], NativeAppBindingStorageV2 | Mapping[str, Any]]


class CandidateNativeAppBindingProviderV2:
    """App-configured production provider for build/calculate/staged sessions.

    ``prepare_create`` is deliberately separate from the service ``create``
    method: it is called by trusted App code while the document is already in
    memory.  The service-facing method then consumes that seed exactly once.
    A fresh provider process may call ``configure_open`` or inject
    ``storage_resolver``; no document seed is used while reopening.
    """

    def __init__(
        self,
        *,
        storage_resolver: StorageResolver | None = None,
        session_id_factory: Callable[[], str] | None = None,
    ) -> None:
        if storage_resolver is not None and not callable(storage_resolver):
            raise TypeError("storage_resolver must be callable")
        if session_id_factory is not None and not callable(session_id_factory):
            raise TypeError("session_id_factory must be callable")
        self._storage_resolver = storage_resolver
        self._session_id_factory = session_id_factory or (lambda: str(uuid.uuid4()))
        self._lock = threading.RLock()
        self._pending: dict[str, _CreateSeed] = {}
        self._storage: dict[str, NativeAppBindingStorageV2] = {}

    def prepare_create(
        self,
        *,
        conversation_id: str,
        workflow: str,
        document_kind: str,
        document_seed: Mapping[str, Any],
        database_path: object,
        delivery_root: object | None = None,
    ) -> None:
        conversation_id = _identity(conversation_id, "conversation_id")
        workflow = _workflow(workflow)
        document_kind = _document_kind(document_kind)
        _validate_workflow_kind(workflow, document_kind)
        if not isinstance(document_seed, Mapping):
            _fail("App文档种子必须是JSON对象", "native_app_binding_document_invalid")
        try:
            document = copy.deepcopy(dict(document_seed))
        except (TypeError, ValueError, RecursionError) as error:
            raise PlanError(
                "App文档种子无法安全复制",
                "native_app_binding_document_invalid",
            ) from error
        storage = NativeAppBindingStorageV2.normalize(
            database_path=database_path,
            delivery_root=delivery_root,
        )
        seed = _CreateSeed(
            conversation_id=conversation_id,
            workflow=workflow,
            document_kind=document_kind,
            document=document,
            storage=storage,
        )
        with self._lock:
            if conversation_id in self._pending:
                _fail("此会话已经有待消费的文档种子", "native_app_binding_seed_exists")
            self._pending[conversation_id] = seed
            self._storage[conversation_id] = storage

    def configure_open(
        self,
        *,
        conversation_id: str,
        database_path: object,
        delivery_root: object | None = None,
    ) -> None:
        conversation_id = _identity(conversation_id, "conversation_id")
        storage = NativeAppBindingStorageV2.normalize(
            database_path=database_path,
            delivery_root=delivery_root,
        )
        with self._lock:
            self._storage[conversation_id] = storage

    def _storage_for_open(self, conversation_id: str) -> NativeAppBindingStorageV2:
        with self._lock:
            configured = self._storage.get(conversation_id)
        if configured is not None:
            return _normalized_storage(configured)
        if self._storage_resolver is None:
            _fail(
                "App未提供此会话的数据库和交付配置",
                "native_app_binding_config_missing",
            )
        return _normalized_storage(self._storage_resolver(conversation_id))

    @staticmethod
    def _new_session(
        *,
        session_id: str,
        root: Path,
        seed: _CreateSeed,
    ) -> CandidateSession:
        session_class: type[CandidateSession]
        if seed.workflow == "build":
            session_class = BoardCandidateSession
        elif seed.workflow == "staged":
            session_class = StagedCandidateSession
        elif seed.workflow == "artifact":
            session_class = ArtifactCandidateSession
        else:
            session_class = CandidateSession
        return session_class(
            session_id,
            root,
            seed.storage.database_path,
            seed.document_kind,
            seed.document,
            delivery_root=seed.storage.delivery_root,
        )

    @staticmethod
    def _record(
        *,
        conversation_id: str,
        root: Path,
        session: CandidateSession,
    ) -> dict[str, Any]:
        body = {
            "contract": BINDING_CONTRACT,
            "conversation_id": conversation_id,
            "session_id": session.session_id,
            "workflow": session.workflow,
            "document_kind": session.kind,
            "document_id": _document_id(session),
            "database_fingerprint": fingerprint(session.database_snapshot),
            "database_location_hash": _location_hash(session.db_path),
            "delivery_location_hash": _location_hash(session.delivery_root),
            "session_root_hash": _location_hash(root),
        }
        return _validate_record({**body, "record_hash": fingerprint(body)})

    def create(
        self,
        *,
        conversation_id: str,
        session_root: Path,
        workflow: str,
    ) -> CandidateNativeAppBindingV2:
        conversation_id = _identity(conversation_id, "conversation_id")
        workflow = _workflow(workflow)
        root = _root(session_root)
        with self._lock:
            seed = self._pending.pop(conversation_id, None)
        if seed is None:
            _fail(
                "App没有为此会话准备一次性文档种子",
                "native_app_binding_seed_missing",
            )
        if seed.workflow != workflow:
            _fail("创建工作流与App种子不一致", "native_app_binding_mismatch")
        with file_lock(safe_path(root, BINDING_LOCK_FILE)):
            if safe_path(root, BINDING_FILE).exists() or safe_path(root, "checkpoint.json").exists():
                _fail("原生绑定或候选会话已经存在", "native_app_binding_exists")
            try:
                session_id = str(uuid.UUID(self._session_id_factory()))
            except (AttributeError, TypeError, ValueError) as error:
                raise PlanError(
                    "候选会话身份生成器返回无效UUID",
                    "native_app_binding_invalid",
                ) from error
            session = self._new_session(session_id=session_id, root=root, seed=seed)
            record = self._record(
                conversation_id=conversation_id,
                root=root,
                session=session,
            )
            write_owned(root, BINDING_FILE, record, MAX_BINDING_BYTES)
        return CandidateNativeAppBindingV2(
            conversation_id=conversation_id,
            workflow=workflow,
            record=record,
            candidate_session=session,
        )

    def open(
        self,
        *,
        conversation_id: str,
        session_root: Path,
        workflow: str,
    ) -> CandidateNativeAppBindingV2:
        conversation_id = _identity(conversation_id, "conversation_id")
        workflow = _workflow(workflow)
        root = _root(session_root)
        storage = self._storage_for_open(conversation_id)
        with file_lock(safe_path(root, BINDING_LOCK_FILE)):
            record = _read_record(root)
            if (
                record["conversation_id"] != conversation_id
                or record["workflow"] != workflow
                or record["session_root_hash"] != _location_hash(root)
                or record["database_location_hash"] != _location_hash(storage.database_path)
                or record["delivery_location_hash"] != _location_hash(storage.delivery_root)
            ):
                _fail("App恢复配置与持久绑定不一致", "native_app_binding_mismatch")
            session = CandidateSession.load(
                record["session_id"],
                root,
                storage.database_path,
                storage.delivery_root,
            )
            binding = CandidateNativeAppBindingV2(
                conversation_id=conversation_id,
                workflow=workflow,
                record=record,
                candidate_session=session,
            )
            binding._assert_database_current()
        return binding


__all__ = [
    "BINDING_CONTRACT",
    "BINDING_FILE",
    "TOOL_MANIFEST_CONTRACT",
    "CandidateNativeAppBindingProviderV2",
    "CandidateNativeAppBindingV2",
    "NativeAppBindingStorageV2",
]
