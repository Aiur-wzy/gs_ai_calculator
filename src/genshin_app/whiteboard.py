"""User action intentions, pending Agent handoff, and strictly bound result return.

A whiteboard is not an executable rotation. Unknown game data stays unknown until
an Agent supplies a native calculation; saved result checks establish consistency,
not the accuracy of the Agent's game model.
"""
from __future__ import annotations

import copy

from genshin_app.equipment.schema import validate_plan
from genshin_app.whiteboard_handoff import export_handoff, make_handoff
from genshin_app.whiteboard_identity import (
    board_fingerprint,
    intent_view,
    legacy_board_fingerprint,
    legacy_intent_view,
    v2_board_fingerprint,
    v2_intent_view,
)
from genshin_app.whiteboard_result_binding import (
    read_whiteboard_result,
    validate_result_intent,
    validate_whiteboard_action_admission_result,
    validate_whiteboard_capacity_result,
)
from genshin_app.whiteboard_revision import (
    prepare_whiteboard_edit,
    revision_transition_capabilities,
    validate_whiteboard_revision_transition,
)
from genshin_app.whiteboard_schema import (
    ACTION_TYPES,
    ELEMENTS,
    KIND,
    MAX_ACTIONS,
    MAX_BYTES,
    PANEL_KEYS,
    SCHEMA_VERSION,
    default_panel,
    new_whiteboard,
    pending_items,
    validate_whiteboard,
    whiteboard_response,
)
from genshin_app.whiteboard_store import (
    load_whiteboard,
    save_whiteboard,
)

__all__ = [
    "ELEMENTS",
    "PANEL_KEYS",
    "apply_whiteboard_equipment",
    "board_fingerprint",
    "default_panel",
    "export_handoff",
    "intent_view",
    "legacy_board_fingerprint",
    "legacy_intent_view",
    "load_whiteboard",
    "make_handoff",
    "new_whiteboard",
    "pending_items",
    "prepare_whiteboard_edit",
    "read_whiteboard_result",
    "revision_transition_capabilities",
    "save_whiteboard",
    "v2_board_fingerprint",
    "v2_intent_view",
    "validate_plan",
    "validate_result_intent",
    "validate_whiteboard",
    "validate_whiteboard_action_admission_result",
    "validate_whiteboard_capacity_result",
    "validate_whiteboard_revision_transition",
    "whiteboard_capabilities",
    "whiteboard_response",
]


def whiteboard_capabilities():
    from genshin_engine.attribute_budget import OPTIONS_SCHEMA
    return {"kind": KIND, "schema_version": SCHEMA_VERSION, "author_schema_revision": "3.1",
            "calculation_options_schema": copy.deepcopy(OPTIONS_SCHEMA),
            "max_bytes": MAX_BYTES, "max_actions": MAX_ACTIONS,
            "revision_policy": revision_transition_capabilities(),
            "max_duration": 3600, "default_board": new_whiteboard(), "default_panel": default_panel(),
            "action_types": [{"id": key, "name_zh": value} for key, value in ACTION_TYPES.items()],
            "panel_policy": "固定面板包含武器与套装常驻属性；null表示未知，不分配逐件词条",
            "default_chat_workflow": "artifact",
            "chat_workflows": {
                "artifact": {"transport": "dsh_client_rpc_and_stream", "authority": "DSH Session Controller",
                    "request_workflow": "artifact", "empty_board_allowed": True,
                    "focus_is_advisory": True,
                    "output": "Agent在独立候选中构建与计算；可查看和继续未完成草稿，正式应用需用户明确审核",
                    "calculation": "编辑和交流不要求已有角色或动作；严格计算按实际消费输入与通用规则单独验证"},
                "build": {"transport": "external_cli", "gui_method": "agent_start",
                    "request_workflow": "build", "empty_board_allowed": True,
                    "output": "待审核白板草稿；无需角色、动作或计算回执；用户应用后可在同一聊天继续构建",
                    "calculation": "草稿尚未计算；应用后明确切换计算补全任务"},
                "calculate": {"transport": "external_cli", "request_workflow": "calculate",
                    "requires": "至少一名角色和一个动作",
                    "output": "完整计算候选与真实回执；按Skill默认保留已有输入，用户明确要求时可调整角色和动作"}},
            "panel_basis": {"gui_method": "character_panel_basis", "supported_levels": [90, 100],
                "mode_field": "team[].panel_basis_mode", "modes": ["database", "manual"],
                "fields": ["base_atk", "base_hp", "base_def"],
                "base_attack": "角色白值加所选武器白值；未选武器或武器状态未知时总基础攻击为null",
                "persistence": "模式可选，数值快照保持；计算不隐式替换基础值，成品面板不自动叠加"},
            "variant_policy": "空字符串表示未指定，可依据资料补齐并说明；已有选择按Skill默认保留，用户明确要求时可修改候选",
            "capacity_intents": {
                "field": "capacity_intents", "schema_version": 3,
                "new_context": None,
                "catalog_gui_method": "whiteboard_action_capacity_model_candidates",
                "ownership": "每个元素战技动作最多属于一个意图；目录查询若触及既有意图必须精确选择完整动作集合",
                "context": "只允许null或当前普通开放世界静态合同；所有非null场景必须完全一致",
                "mixed_context": "null与已确认场景可作为待补草稿共存；交接不把已确认场景推广给其他意图",
                "handoff": "原样传播草稿意图，不生成action_capacity_sources、action_capacity_context、accepted_fingerprint或容量数值",
                "result_binding": {"status": "enforced_on_complete_result",
                    "draft_check": "候选尚未计算时只核对handoff保存的capacity_intents与白板业务指纹",
                    "complete_check": "完整结果逐项绑定action_capacity_sources、实际能力、冷却、初始队列、context，并重放fresh confirmation、执行证明与消费账"},
                "execution_authority": False,
            },
            "action_admission_intents": {
                "field": "action_admission_intents", "schema_version": 3,
                "scope": "release/C0/default单动作元素爆发首片",
                "new_context": None, "new_initial_ready_in": None,
                "ordinary_open_world_preset": {
                    "context": {
                        "contract": "static-action-admission-context-v1",
                        "mode": "ordinary_open_world",
                        "external_cooldown_effects": "none",
                        "external_energy_cost_effects": "none",
                    },
                    "initial_ready_in": [],
                    "application": "仅用户显式选择时同时写入",
                },
                "catalog_gui_method": "whiteboard_action_admission_model_candidates",
                "ownership": "每个元素爆发动作最多属于一个动作接纳意图",
                "handoff": "传播精确意图及无确认提案；不保存accepted_fingerprint或私有marker",
                "result_binding": "意图→动作→能力→来源→编译→运行时账本→committed回执一一绑定",
                "execution_authority": False,
            },
            "return_contract": "完整calculated.json与对应候选白板；保留whiteboard_handoff并核对候选白板与实际输入一致；新攻击列入候选白板，辅助动作仅切人/等待且显式声明",
            "equipment_recalculation": {"gui_method": "whiteboard_apply_equipment",
                "editable": ["team.panel", "team.weapon", "team.artifact_sets", "team.artifact_presence", "team.panel_basis_mode"],
                "baseline": "每次从原白板与原calculated.json重新校验；累计修改始终沿用该原始绑定，不以浏览器缓存作为凭据",
                "persistence": "仅返回内存结果；不修改原文件。待完善原生候选可另存供Agent补正",
                "pending": "无回传、动作或角色身份变更、规则未覆盖、人工依赖或外部补充值失效时交给Agent",
                "ready": "已重新计算；agent_followup仍可能有动作、能量或机制待核实项"},
            "editable": ["team", "panel", "duration", "actions"], "readonly": ["events", "damage"]}


def apply_whiteboard_equipment(board, previous_board=None, previous_result_path=None, *, db_path):
    from genshin_app.whiteboard_equipment import apply_whiteboard_equipment as apply
    return apply(board, previous_board, previous_result_path, db_path=db_path)
