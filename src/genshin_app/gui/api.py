"""Stable pywebview Facade with explicit, single-owner domain ports.

The public method signatures and document transport remain unchanged. Agent,
documents, equipment, database browsing and release application each execute
in their own adapter, with narrow dependencies injected at construction. The
Facade owns window lifecycle and delegates; it does not duplicate transactions.
"""

from __future__ import annotations

import urllib.parse
import webbrowser
from collections.abc import Mapping
from pathlib import Path
from threading import Lock

from genshin_app.cli import CLI_VERSION
from genshin_app.gui.dsh_shell_api import DshShellApi
from genshin_app.gui.ports.agent import AgentGuiPort
from genshin_app.gui.ports.database import DatabaseGuiPort
from genshin_app.gui.ports.document import DocumentGuiPort
from genshin_app.gui.ports.equipment import EquipmentGuiPort
from genshin_app.gui.ports.release import ReleaseGuiPort
from genshin_app.presentation import display_contract


def _release_native_runtime_environment() -> dict[str, str]:
    """Resolve the release child's sanitized system and provider environment.

    The ordinary packaged GUI follows the same reviewed WinINET manual-proxy
    boundary as the real-provider development entry.  A disabled/missing
    manual proxy is explicit direct mode; every configured but unsupported
    shape fails with a stable, path-free reason instead of silently going
    direct or inheriting ambient proxy variables.
    """

    from genshin_app.agent_chat.native_provider_proxy import (
        NativeProviderProxyError,
        read_windows_native_provider_proxy_or_direct,
    )
    from genshin_app.agent_chat.native_runtime_environment import (
        build_native_runtime_environment,
    )
    from genshin_app.agent_chat.transport import TransportError

    try:
        proxy_environment = read_windows_native_provider_proxy_or_direct()
    except NativeProviderProxyError as error:
        raise TransportError(
            error.code,
            "Windows 系统代理配置不受支持；请使用直连或无凭据的 HTTP(S) 手动代理。",
        ) from None
    # First construct the narrow ambient system environment, then add only the
    # already validated proxy contract. The current DSH process validates and
    # narrows the mapping again before starting Node.
    environment = build_native_runtime_environment()
    environment.update(proxy_environment)
    return environment


def _default_agent_manager_factory(
    data_dir: Path,
    *,
    database_path: Path | None = None,
):
    """Build only the external CLI owner; native Agent uses the DSH shell API."""

    from genshin_app.agent_chat.manager import AgentManager

    return AgentManager(data_dir, database_path=database_path)


_APP_IDENTITY_FIELDS = frozenset({
    "contract",
    "mode",
    "build_channel",
    "label",
    "network_policy",
    "network_mode",
    "host_network_policy",
    "agent_network_policy",
    "profile_id",
    "credential_target",
    "release_plan_state",
    "service_mode",
    # Non-secret acceptance evidence.  Only reviewed launchers may set the
    # deterministic transport marker; product launchers omit it.
    "provider_transport",
})


def _normalize_app_identity(value: Mapping | None) -> dict[str, str] | None:
    """Detach public, non-secret build metadata for the renderer.

    ``None`` deliberately preserves the release ``ping`` payload byte-for-byte.
    Development launchers receive a strict field allow-list so a future caller
    cannot accidentally expose a path, environment snapshot or credential.
    """

    if value is None:
        return None
    if not isinstance(value, Mapping):
        raise TypeError("app_identity must be a mapping")
    unknown = set(value) - _APP_IDENTITY_FIELDS
    if unknown:
        raise ValueError(f"unknown app_identity fields: {', '.join(sorted(unknown))}")
    result: dict[str, str] = {}
    for key, item in value.items():
        if not isinstance(item, str) or not item or len(item) > 256:
            raise ValueError(f"app_identity.{key} must be a non-empty short string")
        if any(ord(char) < 32 or ord(char) == 127 for char in item):
            raise ValueError(f"app_identity.{key} contains a control character")
        result[key] = item
    return result


class GuiApi(DshShellApi):
    """Stable external method signatures; domain work belongs to explicit ports."""

    @property
    def _agent_manager(self):
        return self._agent_port._agent_manager

    @_agent_manager.setter
    def _agent_manager(self, value):
        self._agent_port._agent_manager = value

    def __init__(self, data_dir: Path, plans_dir: Path | None = None, *,
                 startup_path: Path | None = None,
                 agent_manager_factory=None,
                 database_path: Path | None = None,
                 app_identity: Mapping | None = None,
                 external_url_opener=None,
                 release_pair_coordinator=None,
                 database_setup=None,
                 dsh_shell_factory=None,
                 dsh_domain_binding_resolver=None,
                 dsh_workspace_registry=None,
                 dsh_credentials=None,
                 dsh_result_save_dialog=None, dsh_file_dialog=None):
        self.data_dir = Path(data_dir)
        self.plans_dir = Path(plans_dir) if plans_dir else self.data_dir / "plans"
        self._database_path_explicit = database_path is not None
        self._external_url_opener = external_url_opener or webbrowser.open_new_tab
        self._database_path = (
            Path(database_path).expanduser().resolve()
            if database_path is not None
            else self.data_dir / "genshin.db"
        )
        self._app_identity = _normalize_app_identity(app_identity)
        identity = self._app_identity or {}
        self._acceptance_mode = identity.get("mode") in {
            "development_acceptance",
            "provider_acceptance",
            "native_agent_development",
        }
        self._development_network_disabled = (
            identity.get("mode") == "development_acceptance"
            and identity.get(
                "network_mode",
                identity.get("network_policy"),
            ) == "disabled"
        )
        self._native_agent_development_mode = (
            identity.get("mode") == "native_agent_development"
        )
        # Development/acceptance launchers share the source database read-only
        # and suppress unrelated App/update traffic.  Provider-enabled modes
        # still permit the native Agent's reviewed DeepSeek Search and bounded
        # public-address Fetch through their separately composed manager.
        self._acceptance_host_network_restricted = self._acceptance_mode
        # Any explicit database belongs to an isolated acceptance/development
        # source outside this profile.  Treat it as read-only at the DB bridge
        # itself, even if a future launcher mislabels its UI identity.
        self._shared_database_read_only = self._database_path_explicit
        self._database_setup = database_setup
        self._release_port = ReleaseGuiPort(self.data_dir, self._database_path,
            app_identity=self._app_identity, host_network_restricted=self._acceptance_host_network_restricted,
            network_disabled=self._development_network_disabled, native_development=self._native_agent_development_mode,
            shared_read_only=self._shared_database_read_only, coordinator=release_pair_coordinator,
            database_setup=database_setup)
        self._window = None
        self._closing_approved = False
        self._editor_initialized = False
        # Keep the path as bridge data, never interpolate it into JavaScript.
        # Missing/invalid files are reported by the ordinary GUI loading route.
        self._startup_path = str(Path(startup_path).expanduser().resolve()) if startup_path is not None else None
        self._startup_lock = Lock()
        self._agent_port = AgentGuiPort(self.data_dir, self._database_path,
            manager_factory=agent_manager_factory,
            default_manager_factory=lambda *args, **kwargs: _default_agent_manager_factory(*args, **kwargs))
        self._database_port = DatabaseGuiPort(self._database_path)
        self._document_port = DocumentGuiPort(self.data_dir, self.plans_dir, self._database_path,
            display_catalog=self._database_port.get_display_catalog)
        self._equipment_port = EquipmentGuiPort(self.data_dir, self._database_path,
            manager_provider=self._agent_port._get_agent_manager)
        self._dsh_shell_factory = dsh_shell_factory
        self._dsh_shell = None
        self._dsh_shell_closed = False
        self._dsh_shell_lock = Lock()
        self._dsh_domain_binding_resolver = dsh_domain_binding_resolver
        self._dsh_workspace_registry = dsh_workspace_registry
        self._dsh_workspace_lock = Lock()
        self._dsh_credentials = dsh_credentials
        self._dsh_credentials_lock = Lock()
        self._dsh_result_save_dialog = dsh_result_save_dialog
        self._dsh_file_dialog = dsh_file_dialog

    def get_startup_file(self) -> dict:
        """Claim this window's initial file once, including concurrent bridge calls."""
        with self._startup_lock:
            path = self._startup_path
            self._startup_path = None
        return self._ok({"path": path})

    def editor_initialized(self) -> dict:
        self._editor_initialized = True
        return self._ok({"initialized": True})

    def confirm_close(self) -> dict:
        if self._agent_manager is not None:
            result = self.agent_shutdown()
            if result.get("code"):
                return result
        self._close_dsh_shell()
        self._closing_approved = True
        if self._window is not None:
            self._window.destroy()
        return self._ok({"closed": True})

    def _agent_call(self, method, *args, **kwargs):
        return self._agent_port._agent_call(method, *args, **kwargs)

    def _get_agent_manager(self):
        return self._agent_port._get_agent_manager()

    def agent_detect(self):
        return self._agent_port.agent_detect()

    def agent_connect(self, connection):
        return self._agent_port.agent_connect(connection)

    def agent_forget_credential(self, provider_id):
        return self._agent_port.agent_forget_credential(provider_id)

    def agent_budget_settings(self):
        return self._agent_port.agent_budget_settings()

    def agent_update_budget_settings(self, overrides, expected_generation=None):
        return self._agent_port.agent_update_budget_settings(overrides, expected_generation)

    def agent_reset_budget_settings(self, expected_generation=None):
        return self._agent_port.agent_reset_budget_settings(expected_generation)

    def agent_dsh_settings(self):
        return self._agent_port.agent_dsh_settings()

    def agent_update_dsh_settings(self, overrides, expected_generation=None):
        return self._agent_port.agent_update_dsh_settings(overrides, expected_generation)

    def agent_reset_dsh_settings(self, expected_generation=None):
        return self._agent_port.agent_reset_dsh_settings(expected_generation)

    def agent_start(self, request):
        return self._agent_port.agent_start(request)

    def agent_send(self, session_id, message, client_request_id=None, current_document=None, workspace=None, current_payload=None):
        return self._agent_port.agent_send(session_id, message, client_request_id, current_document, workspace, current_payload)

    def agent_resume(self, session_id, current_document, workspace=None, current_payload=None):
        return self._agent_port.agent_resume(session_id, current_document, workspace, current_payload)

    def agent_poll(self, session_id, after_seq=0):
        return self._agent_port.agent_poll(session_id, after_seq)

    def agent_wait_events(self, session_id, after_seq=0, subscription_generation=0, timeout_ms=12000):
        return self._agent_port.agent_wait_events(session_id, after_seq, subscription_generation, timeout_ms)

    def agent_turn_outline(self, session_id, request=None):
        return self._agent_port.agent_turn_outline(session_id, request)

    def agent_search_conversation(self, session_id, request):
        return self._agent_port.agent_search_conversation(session_id, request)

    def agent_load_turn_window(self, session_id, request):
        return self._agent_port.agent_load_turn_window(session_id, request)

    def agent_telemetry_bundle(self, session_id, request=None):
        return self._agent_port.agent_telemetry_bundle(session_id, request)

    def agent_workbench_projection(self, session_id):
        return self._agent_port.agent_workbench_projection(session_id)

    def agent_submit_question_batch_answer(self, session_id, request):
        return self._agent_port.agent_submit_question_batch_answer(session_id, request)

    def agent_enqueue_workbench_inbox(self, session_id, request):
        return self._agent_port.agent_enqueue_workbench_inbox(session_id, request)

    def agent_revise_workbench_inbox(self, session_id, request):
        return self._agent_port.agent_revise_workbench_inbox(session_id, request)

    def agent_cancel_workbench_inbox(self, session_id, request):
        return self._agent_port.agent_cancel_workbench_inbox(session_id, request)

    def agent_reorder_workbench_inbox(self, session_id, request):
        return self._agent_port.agent_reorder_workbench_inbox(session_id, request)

    def agent_promote_workbench_inbox(self, session_id, request):
        return self._agent_port.agent_promote_workbench_inbox(session_id, request)

    def agent_list_document_checkpoints(self, session_id, offset=0, limit=50):
        return self._agent_port.agent_list_document_checkpoints(session_id, offset, limit)

    def agent_document_restore_status(self, session_id):
        return self._agent_port.agent_document_restore_status(session_id)

    def agent_create_document_checkpoint(self, session_id, request):
        return self._agent_port.agent_create_document_checkpoint(session_id, request)

    def agent_preview_document_checkpoint(self, session_id, checkpoint_id, from_checkpoint_id=None):
        return self._agent_port.agent_preview_document_checkpoint(session_id, checkpoint_id, from_checkpoint_id)

    def agent_rename_document_checkpoint(self, session_id, request):
        return self._agent_port.agent_rename_document_checkpoint(session_id, request)

    def agent_delete_document_checkpoint(self, session_id, request):
        return self._agent_port.agent_delete_document_checkpoint(session_id, request)

    def agent_prepare_document_restore(self, session_id, request):
        return self._agent_port.agent_prepare_document_restore(session_id, request)

    def agent_mark_document_restore_applied(self, session_id, request):
        return self._agent_port.agent_mark_document_restore_applied(session_id, request)

    def agent_advance_document_restore_baseline(self, session_id, request):
        return self._agent_port.agent_advance_document_restore_baseline(session_id, request)

    def agent_commit_document_restore(self, session_id, request):
        return self._agent_port.agent_commit_document_restore(session_id, request)

    def agent_abort_document_restore(self, session_id, request):
        return self._agent_port.agent_abort_document_restore(session_id, request)

    def agent_mark_document_restore_recovery_required(self, session_id, request):
        return self._agent_port.agent_mark_document_restore_recovery_required(session_id, request)

    def agent_reconcile_document_restore(self, session_id, request):
        return self._agent_port.agent_reconcile_document_restore(session_id, request)

    def agent_runtime_status(self, after_seq=0, subscription_generation=0):
        return self._agent_port.agent_runtime_status(after_seq, subscription_generation)

    def agent_wait_runtime_status(self, after_seq=0, subscription_generation=0, timeout_ms=12000):
        return self._agent_port.agent_wait_runtime_status(after_seq, subscription_generation, timeout_ms)

    def agent_notify_system_resume(self):
        return self._agent_port.agent_notify_system_resume()

    def agent_prepare_runtime(self, purpose='send'):
        return self._agent_port.agent_prepare_runtime(purpose)

    def agent_runtime_prepare_status(self, operation_id):
        return self._agent_port.agent_runtime_prepare_status(operation_id)

    def agent_wait_runtime_prepare(self, operation_id, timeout_ms=12000):
        return self._agent_port.agent_wait_runtime_prepare(operation_id, timeout_ms)

    def agent_cancel_runtime_prepare(self, operation_id):
        return self._agent_port.agent_cancel_runtime_prepare(operation_id)

    def agent_stop(self, session_id):
        return self._agent_port.agent_stop(session_id)

    def agent_candidate(self, session_id):
        return self._agent_port.agent_candidate(session_id)

    def agent_accept(self, session_id, kind, document, expected_revision, expected_receipt, baseline_payload=None):
        return self._agent_port.agent_accept(session_id, kind, document, expected_revision, expected_receipt, baseline_payload)

    def agent_candidate_applied(self, session_id, kind, document, expected_revision, expected_receipt, workspace=None, actual_payload=None):
        return self._agent_port.agent_candidate_applied(session_id, kind, document, expected_revision, expected_receipt, workspace, actual_payload)

    def agent_abort_candidate_application(self, session_id, current_document, workspace=None, current_payload=None):
        return self._agent_port.agent_abort_candidate_application(session_id, current_document, workspace, current_payload)

    def agent_recover_candidate_application(self, session_id, current_document, workspace=None, current_payload=None, discard_current=False):
        return self._agent_port.agent_recover_candidate_application(session_id, current_document, workspace, current_payload, discard_current)

    def agent_recover_last_applied_whiteboard_projection(self, session_id, current_document, workspace=None, current_payload=None, allow_unsaved_target=False):
        return self._agent_port.agent_recover_last_applied_whiteboard_projection(session_id, current_document, workspace, current_payload, allow_unsaved_target)

    def agent_candidate_read(self, session_id, resource, pointer='', offset=0, limit=50):
        return self._agent_port.agent_candidate_read(session_id, resource, pointer, offset, limit)

    def agent_whiteboard_apply_equipment(self, session_id, delivery_capability, board):
        return self._agent_port.agent_whiteboard_apply_equipment(session_id, delivery_capability, board)

    def agent_publish_committed_whiteboard_result(self, session_id, delivery_capability, output_path, expected_revision, expected_receipt):
        return self._agent_port.agent_publish_committed_whiteboard_result(session_id, delivery_capability, output_path, expected_revision, expected_receipt)

    def agent_resolve_app_action(self, session_id, action_id, status, workspace=None, message=None):
        return self._agent_port.agent_resolve_app_action(session_id, action_id, status, workspace, message)

    def agent_present_app_action(self, session_id, action_id, workspace=None):
        return self._agent_port.agent_present_app_action(session_id, action_id, workspace)

    def agent_claim_app_action(self, session_id, action_id, ui_receipt, workspace=None):
        return self._agent_port.agent_claim_app_action(session_id, action_id, ui_receipt, workspace)

    def agent_settle_app_action(self, session_id, action_id, ui_receipt, outcome, workspace=None):
        return self._agent_port.agent_settle_app_action(session_id, action_id, ui_receipt, outcome, workspace)

    def agent_recover_app_action_without_replay(self, session_id, action_id, ui_receipt):
        return self._agent_port.agent_recover_app_action_without_replay(session_id, action_id, ui_receipt)

    def agent_sessions(self):
        return self._agent_port.agent_sessions()

    def agent_rename_session(self, session_id, request):
        return self._agent_port.agent_rename_session(session_id, request)

    def agent_archive_session(self, session_id, request):
        return self._agent_port.agent_archive_session(session_id, request)

    def agent_mark_session_read(self, session_id, request):
        return self._agent_port.agent_mark_session_read(session_id, request)

    def agent_forget(self, session_id):
        return self._agent_port.agent_forget(session_id)

    def agent_shutdown(self):
        return self._agent_port.agent_shutdown()

    @property
    def _db_path(self) -> Path:
        return self._database_path

    @staticmethod
    def _ok(data: dict) -> dict:
        return {"code": 0, "data": data}

    @staticmethod
    def _err(message: str) -> dict:
        return {"code": 1, "message": message}

    def ping(self) -> dict:
        data = {"app": "genshin_app", "version": CLI_VERSION}
        if self._app_identity is not None:
            data["app_identity"] = dict(self._app_identity)
        return self._ok(data)

    def open_external_link(self, url: str) -> dict:
        """Open one user-confirmed public HTTP(S) link in the system browser.

        The Renderer owns the visible confirmation dialog.  This bridge is a
        second, narrow trust boundary: it refuses local/custom schemes,
        embedded credentials, control characters and oversized targets, and
        never feeds the URL to a shell command.
        """

        if (
            not isinstance(url, str)
            or not 1 <= len(url) <= 4096
            or any(ord(char) < 32 or ord(char) == 127 for char in url)
        ):
            return self._err("外部链接不合法")
        try:
            parsed = urllib.parse.urlsplit(url)
        except ValueError:
            return self._err("外部链接不合法")
        if (
            parsed.scheme.lower() not in {"http", "https"}
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
        ):
            return self._err("仅允许打开不含凭据的 HTTP(S) 链接")
        try:
            opened = bool(self._external_url_opener(url))
        except Exception:  # noqa: BLE001 - OS browser integration boundary
            return self._err("系统浏览器未能打开此链接")
        if not opened:
            return self._err("系统浏览器未接受此链接")
        return self._ok({"status": "opened"})

    def document_json_call(self, method: str, args_text: str) -> str:
        """Preserve numeric tokens; never bypass the called document API checks."""
        from .document_transport import dispatch
        return dispatch(self, method, args_text)

    def check_update(self) -> dict:
        return self._release_port.check_update()

    def get_enums(self) -> dict:
        """编辑器下拉枚举，唯一源 = 引擎 constants（防前后端漂移）。"""
        from genshin_db.validation import TARGET_TYPES
        from genshin_engine import constants
        from genshin_engine.registry import get_registry

        return self._ok({
            "damage_systems": list(constants.DAMAGE_SYSTEMS),
            "buff_zones": list(constants.BUFF_ZONES),
            "additive_zones": list(constants.ADDITIVE_ZONES),
            "multiplicative_zones": list(constants.MULTIPLICATIVE_ZONES),
            "enemy_zones": list(constants.ENEMY_ZONES),
            "buff_scopes": list(constants.BUFF_SCOPES),
            "reaction_types": {
                "classic": ["amplify", *constants.AGGRAVATE_FACTOR.keys()],
                "transformative": list(constants.TRANSFORMATIVE_FACTOR.keys()),
                "lunar": list(constants.LUNAR_FACTOR.keys()),
                "stellar": [*constants.STELLAR_FACTOR.keys(), "stellar_superconduct"],
            },
            "elements": list(constants.ELEMENTS),
            "scaling_stats": list(constants.SCALING_STATS),
            "engine_v2": get_registry(),
            "annotation_target_types": list(TARGET_TYPES),
            "display": display_contract(),
        })

    def get_display_catalog(self, version_line: str='release') -> dict:
        return self._database_port.get_display_catalog(version_line)

    def _equipment_call(self, method: str, *args, **kwargs) -> dict:
        return self._equipment_port._equipment_call(method, *args, **kwargs)

    def _candidate_session_boundary(self, session_id, plan):
        return self._equipment_port._candidate_session_boundary(session_id, plan)

    def plan_capabilities(self) -> dict:
        return self._equipment_port.plan_capabilities()

    def action_capacity_model_candidates(self, plan: dict, actor_id: str, ability_ids: list[str], cursor=None, page_size: int=16, view: str='all') -> dict:
        return self._equipment_port.action_capacity_model_candidates(plan, actor_id, ability_ids, cursor, page_size, view)

    def action_admission_model_candidates(self, plan: dict, actor_id: str, ability_id: str, cursor=None, page_size: int=16, view: str='all') -> dict:
        return self._equipment_port.action_admission_model_candidates(plan, actor_id, ability_id, cursor, page_size, view)

    def action_admission_preview(self, plan: dict, request: dict) -> dict:
        return self._equipment_port.action_admission_preview(plan, request)

    def whiteboard_action_admission_model_candidates(self, board: dict, actor_id: str, action_id: str, cursor=None, page_size: int=16, view: str='all') -> dict:
        return self._equipment_port.whiteboard_action_admission_model_candidates(board, actor_id, action_id, cursor, page_size, view)

    def whiteboard_action_capacity_model_candidates(self, board: dict, actor_id: str, action_ids: list[str], cursor=None, page_size: int=16, view: str='all') -> dict:
        return self._equipment_port.whiteboard_action_capacity_model_candidates(board, actor_id, action_ids, cursor, page_size, view)

    def _whiteboard_call(self, method, *args, **kwargs):
        return self._document_port._whiteboard_call(method, *args, **kwargs)

    def whiteboard_capabilities(self):
        return self._document_port.whiteboard_capabilities()

    def whiteboard_new(self):
        return self._document_port.whiteboard_new()

    def whiteboard_validate(self, board):
        return self._document_port.whiteboard_validate(board)

    def whiteboard_load(self, path):
        return self._document_port.whiteboard_load(path)

    def whiteboard_save(self, path, board, expected_file_sha=None):
        return self._document_port.whiteboard_save(path, board, expected_file_sha)

    def whiteboard_handoff(self, board, output_dir):
        return self._document_port.whiteboard_handoff(board, output_dir)

    def whiteboard_read_result(self, board, path, expected_file_sha=None):
        return self._document_port.whiteboard_read_result(board, path, expected_file_sha)

    def open_saved_calculation(self, path, expected_file_sha=None):
        return self._document_port.open_saved_calculation(path, expected_file_sha, reports=self._equipment_port.viewed_reports)

    def whiteboard_apply_equipment(self, board, previous_board=None, previous_result_path=None):
        return self._document_port.whiteboard_apply_equipment(board, previous_board, previous_result_path)

    def new_plan(self, template: dict | None=None) -> dict:
        return self._equipment_port.new_plan(template)

    def enable_mechanics(self, plan: dict) -> dict:
        return self._equipment_port.enable_mechanics(plan)

    def assemble_attack_source(self, plan: dict, request: dict) -> dict:
        return self._equipment_port.assemble_attack_source(plan, request)

    def assemble_periodic_source(self, plan: dict, request: dict) -> dict:
        return self._equipment_port.assemble_periodic_source(plan, request)

    def mechanics_capabilities(self) -> dict:
        return self._equipment_port.mechanics_capabilities()

    def validate_mechanics_model(self, model: dict) -> dict:
        return self._equipment_port.validate_mechanics_model(model)

    def query_context(self, request: dict) -> dict:
        return self._equipment_port.query_context(request)

    def equipment_reference_panel(self, request: dict, version_line: str = "release") -> dict:
        return self._equipment_port.equipment_reference_panel(request, version_line)

    def query_runtime(self, request: dict) -> dict:
        return self._equipment_port.query_runtime(request)

    def export_formula_workbook(self, plan: dict, name: str='', session_id: str | None=None) -> dict:
        return self._equipment_port.export_formula_workbook(plan, name, session_id)

    def load_plan(self, path: str) -> dict:
        return self._equipment_port.load_plan(path)

    def save_plan(self, path: str, plan: dict, expected_file_sha: str | None=None) -> dict:
        return self._equipment_port.save_plan(path, plan, expected_file_sha)

    def compose_equipment_plan(self, plan: dict) -> dict:
        return self._equipment_port.compose_equipment_plan(plan)

    def prepare_attribute_review(self, plan: dict, note: str) -> dict:
        return self._equipment_port.prepare_attribute_review(plan, note)

    def preview_equipment_edit(self, plan: dict, previous_plan: dict) -> dict:
        return self._equipment_port.preview_equipment_edit(plan, previous_plan)

    def apply_data_update(self, plan: dict) -> dict:
        return self._equipment_port.apply_data_update(plan)

    def detach_character(self, plan: dict, character_id: str) -> dict:
        return self._equipment_port.detach_character(plan, character_id)

    def prepare_character_input(self, db_character_id: int, level: int=90, version_line: str='release') -> dict:
        return self._equipment_port.prepare_character_input(db_character_id, level, version_line)

    def character_panel_basis(self, db_character_id: int, level: int=90, version_line: str='release', weapon: dict | None=None) -> dict:
        return self._equipment_port.character_panel_basis(db_character_id, level, version_line, weapon)

    def run_plan_calc(self, plan: dict, session_id: str | None=None) -> dict:
        return self._equipment_port.run_plan_calc(plan, session_id)

    def inspect_calculation_diagnostics(self, value: dict, request: dict | None=None) -> dict:
        return self._equipment_port.inspect_calculation_diagnostics(value, request)

    def export_plan_result(self, plan: dict, fmt: str, name: str='', session_id: str | None=None) -> dict:
        return self._equipment_port.export_plan_result(plan, fmt, name, session_id)

    def export_viewed_report(self, handle: str, fmt: str, path: str, expected_file_sha=None) -> dict:
        return self._equipment_port.export_viewed_report(handle, fmt, path, expected_file_sha)

    def read_viewed_report(self, handle: str, section: str, offset: int = 0) -> dict:
        return self._equipment_port.read_viewed_report(handle, section, offset)

    def export_resolved_plan(self, plan: dict, path: str) -> dict:
        return self._equipment_port.export_resolved_plan(plan, path)

    def inspect_plan_file(self, path: str) -> dict:
        return self._document_port.inspect_plan_file(path)

    def list_plan_details(self) -> dict:
        return self._document_port.list_plan_details()

    def list_plans(self) -> dict:
        return self._document_port.list_plans()

    def new_template(self) -> dict:
        return self._document_port.new_template()

    def load_template(self, path: str) -> dict:
        return self._document_port.load_template(path)

    def save_template(self, path: str, template: dict, expected_file_sha=None) -> dict:
        return self._document_port.save_template(path, template, expected_file_sha)

    def run_calc(self, template: dict) -> dict:
        return self._document_port.run_calc(template)

    def rotation_capabilities(self) -> dict:
        return self._document_port.rotation_capabilities()

    def check_rotation(self, document: dict) -> dict:
        return self._document_port.check_rotation(document)

    def load_rotation_model(self, path: str) -> dict:
        return self._document_port.load_rotation_model(path)

    def export_result(self, result: dict, fmt: str, name: str='', selected_path=None) -> dict:
        return self._document_port.export_result(result, fmt, name, selected_path)

    # ---------- G4 数据浏览与追加描述（共用 genshin_db.queries） ----------

    def db_overview(self) -> dict:
        return self._database_port.db_overview()

    def _equipment_query(self, method: str, *args, **kwargs) -> dict:
        return self._database_port._equipment_query(method, *args, **kwargs)

    def db_capabilities(self) -> dict:
        return self._database_port.db_capabilities()

    def list_weapons(self, name='', weapon_type='', rarity=0, catalog_scope='codex', version_line='release', page=1, page_size=50) -> dict:
        return self._database_port.list_weapons(name, weapon_type, rarity, catalog_scope, version_line, page, page_size)

    def weapon_detail(self, weapon_id, version_line='release') -> dict:
        return self._database_port.weapon_detail(weapon_id, version_line)

    def weapon_stats(self, weapon_id, level, ascension, version_line='release') -> dict:
        return self._database_port.weapon_stats(weapon_id, level, ascension, version_line)

    def list_artifact_sets(self, name='', rarity=0, catalog_scope='codex', version_line='release', page=1, page_size=50) -> dict:
        return self._database_port.list_artifact_sets(name, rarity, catalog_scope, version_line, page, page_size)

    def artifact_set_detail(self, set_id, version_line='release') -> dict:
        return self._database_port.artifact_set_detail(set_id, version_line)

    def list_artifacts(self, name='', set_id=None, equip_type='', rarity=0, catalog_scope='codex', version_line='release', page=1, page_size=50) -> dict:
        return self._database_port.list_artifacts(name, set_id, equip_type, rarity, catalog_scope, version_line, page, page_size)

    def artifact_detail(self, artifact_id, version_line='release') -> dict:
        return self._database_port.artifact_detail(artifact_id, version_line)

    def artifact_main_props(self, depot_id, prop_type='', version_line='release', page=1, page_size=50) -> dict:
        return self._database_port.artifact_main_props(depot_id, prop_type, version_line, page, page_size)

    def artifact_substats(self, depot_id, prop_type='', version_line='release', page=1, page_size=50) -> dict:
        return self._database_port.artifact_substats(depot_id, prop_type, version_line, page, page_size)

    def artifact_main_stats(self, artifact_id, level, main_prop_id, version_line='release') -> dict:
        return self._database_port.artifact_main_stats(artifact_id, level, main_prop_id, version_line)

    def equip_affix_detail(self, affix_id, version_line='release') -> dict:
        return self._database_port.equip_affix_detail(affix_id, version_line)

    def check_database_update(self) -> dict:
        return self._release_port.check_database_update()

    def update_database(self, confirmation_digest: str='', user_confirmed: bool=False) -> dict:
        return self._release_port.update_database(confirmation_digest, user_confirmed)

    def cancel_database_update(self) -> dict:
        return self._release_port.cancel_database_update()

    def database_update_progress(self) -> dict:
        return self._release_port.database_update_progress()

    def choose_local_database_release(self) -> dict:
        return self._release_port.choose_local_database_release(self._window)

    def list_characters(self, name: str='', element: str='', rarity: int=0, version_line: str='release', page: int=1, page_size: int=50) -> dict:
        return self._database_port.list_characters(name, element, rarity, version_line, page, page_size)

    def character_detail(self, char_id: int, version_line: str='release') -> dict:
        return self._database_port.character_detail(char_id, version_line)

    def search_data(self, q: str, version_line: str='release') -> dict:
        return self._database_port.search_data(q, version_line)

    def list_annotations(self, target_type: str='', target_id: int=0, status: str='all', version_line: str='release', page: int=1, page_size: int=100) -> dict:
        return self._database_port.list_annotations(target_type, target_id, status, version_line, page, page_size)

    def create_annotation(self, target_type: str, target_id: int, content: str, category: str='', title: str='', version_line: str | None=None) -> dict:
        return self._database_port.create_annotation(target_type, target_id, content, category, title, version_line)

    def update_annotation(self, ann_id: int, fields: dict) -> dict:
        return self._database_port.update_annotation(ann_id, fields)

    def delete_annotation(self, ann_id: int) -> dict:
        return self._database_port.delete_annotation(ann_id)

    @staticmethod
    def _deprecated_database_write(operation: str) -> dict:
        return DatabaseGuiPort._deprecated_database_write(operation)
