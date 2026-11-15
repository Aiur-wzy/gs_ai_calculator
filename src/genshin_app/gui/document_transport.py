"""Lossless JSON wire for GUI documents; ordinary APIs retain all validation."""
from __future__ import annotations

import json
import math

from genshin_app.calculation_capacity import GUI_DOCUMENT_TRANSPORT_BYTES

# Transport limits, not engine work or source-approval budgets.  Calculation
# artifacts keep their independent 96 MiB limit; the GUI envelope needs room
# for RPC metadata, the board snapshot, and recovery receipts around one such
# artifact.
MAX_BYTES = GUI_DOCUMENT_TRANSPORT_BYTES
MAX_DEPTH = 128
DOCUMENT_METHODS = frozenset({
    'equipment_reference_panel',
    'dsh_client_session_status',
    'dsh_client_saved_whiteboard',
    'agent_budget_settings', 'agent_update_budget_settings', 'agent_reset_budget_settings',
    'agent_dsh_settings', 'agent_update_dsh_settings', 'agent_reset_dsh_settings',
    'dsh_client_whiteboard_projection',
    'dsh_client_file_dialog',
    'dsh_client_workspace_state', 'dsh_client_app_actions', 'dsh_client_app_action',
    'dsh_client_domain_approvals', 'dsh_client_domain_approval_decide',
    'dsh_client_bind_document', 'dsh_client_domain_binding', 'dsh_client_candidate',
    'dsh_client_candidate_read', 'dsh_client_candidate_command',
    'dsh_client_whiteboard_apply_equipment', 'dsh_client_publish_whiteboard_result',
    'dsh_client_save_whiteboard_result',
    'dsh_client_publish_candidate_input_draft', 'dsh_client_open_candidate_input_draft',
    'dsh_client_candidate_input_draft_preview_read',
    'dsh_client_request_candidate_input_draft',
    'dsh_client_import_candidate_input_draft',
    'dsh_client_incompatible_applications', 'dsh_client_abandon_incompatible_application',
    'new_plan', 'enable_mechanics', 'assemble_attack_source', 'assemble_periodic_source',
    'validate_mechanics_model', 'export_formula_workbook', 'load_plan', 'save_plan',
    'compose_equipment_plan', 'prepare_attribute_review', 'preview_equipment_edit',
    'apply_data_update', 'detach_character', 'prepare_character_input', 'character_panel_basis',
    'run_plan_calc', 'inspect_calculation_diagnostics', 'export_plan_result', 'export_viewed_report', 'read_viewed_report', 'export_resolved_plan',
    'new_template', 'load_template', 'save_template', 'run_calc', 'check_rotation',
    'load_rotation_model', 'export_result', 'whiteboard_new', 'whiteboard_validate',
    'whiteboard_load', 'whiteboard_save', 'whiteboard_handoff', 'whiteboard_read_result', 'open_saved_calculation',
    'whiteboard_apply_equipment', 'action_capacity_model_candidates',
    'whiteboard_action_capacity_model_candidates', 'action_admission_model_candidates',
    'action_admission_preview', 'whiteboard_action_admission_model_candidates',
    'agent_start', 'agent_send', 'agent_resume', 'agent_poll', 'agent_wait_events',
    'agent_runtime_status', 'agent_wait_runtime_status',
    'agent_notify_system_resume', 'agent_prepare_runtime',
    'agent_runtime_prepare_status', 'agent_wait_runtime_prepare',
    'agent_cancel_runtime_prepare',
    'agent_candidate',
    'agent_accept', 'agent_candidate_applied', 'agent_abort_candidate_application',
    'agent_recover_candidate_application',
    'agent_recover_last_applied_whiteboard_projection',
    'agent_candidate_read',
    'agent_whiteboard_apply_equipment',
    'agent_publish_committed_whiteboard_result',
    'agent_resolve_app_action', 'agent_forget_credential',
    'agent_present_app_action', 'agent_claim_app_action', 'agent_settle_app_action',
    'agent_recover_app_action_without_replay',
    'agent_list_document_checkpoints', 'agent_document_restore_status',
    'agent_create_document_checkpoint', 'agent_preview_document_checkpoint',
    'agent_rename_document_checkpoint', 'agent_delete_document_checkpoint',
    'agent_prepare_document_restore', 'agent_mark_document_restore_applied',
    'agent_advance_document_restore_baseline', 'agent_commit_document_restore',
    'agent_abort_document_restore', 'agent_mark_document_restore_recovery_required',
    'agent_reconcile_document_restore',
})


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('JSON 文档含重复键')
        result[key] = value
    return result


def _reject_constant(_value):
    raise ValueError('JSON 文档包含非有限数值')


def _check_tree(value, depth=0):
    if depth > MAX_DEPTH:
        raise ValueError('JSON 文档嵌套超过传输上限')
    if isinstance(value, dict):
        for key, child in value.items():
            key.encode('utf8')
            _check_tree(child, depth + 1)
    elif isinstance(value, (list, tuple)):
        for child in value:
            _check_tree(child, depth + 1)
    elif isinstance(value, float) and not math.isfinite(value):
        raise ValueError('JSON 文档包含非有限数值')
    elif isinstance(value, str):
        value.encode('utf8')


def _check_text(text):
    if not isinstance(text, str):
        raise TypeError('文档参数必须为 JSON 文本')
    # A cheap character bound precedes the UTF-8 allocation.
    if len(text) > MAX_BYTES or len(text.encode('utf8')) > MAX_BYTES:
        raise ValueError('JSON 文档超过传输字节上限')
    # Reject deeply nested text before json.loads creates containers. This scan
    # recognizes strings/escapes only; JSON grammar remains json.loads' job.
    depth = 0
    quoted = escaped = False
    for char in text:
        if quoted:
            if escaped:
                escaped = False
            elif char == '\\':
                escaped = True
            elif char == '"':
                quoted = False
        elif char == '"':
            quoted = True
        elif char in '[{':
            depth += 1
            if depth > MAX_DEPTH:
                raise ValueError('JSON 文档嵌套超过传输上限')
        elif char in ']}':
            depth -= 1


def dispatch(api, method, args_text):
    """Return a JSON string, preserving Python number types through JS bridges."""
    called = False
    try:
        if not isinstance(method, str) or method not in DOCUMENT_METHODS:
            raise ValueError('该操作不属于文档传输接口')
        _check_text(args_text)
        args = json.loads(args_text, object_pairs_hook=_unique_object,
                          parse_constant=_reject_constant)
        if not isinstance(args, list):
            raise TypeError('文档接口参数必须为数组')
        _check_tree(args)
        called = True
        response = getattr(api, method)(*args)
        _check_tree(response)
        encoder = json.JSONEncoder(ensure_ascii=False, allow_nan=False, separators=(',', ':'))
        chunks, size = [], 0
        for chunk in encoder.iterencode(response):
            size += len(chunk.encode('utf8'))
            if size > MAX_BYTES:
                raise ValueError('JSON 文档超过传输字节上限')
            chunks.append(chunk)
        return ''.join(chunks)
    except (ValueError, TypeError, RecursionError) as exc:
        message = ('操作已提交，但无法完整返回结果；请先检查保存文件或任务状态，勿直接重复提交。'
                   if called else '文档尚未提交。')
        return json.dumps({'code': 1, 'reason': 'document_transport',
                           'operation_may_have_completed': called,
                           'message': f'文档传输失败：{str(exc)[:1024]}。{message}'}, ensure_ascii=True)
