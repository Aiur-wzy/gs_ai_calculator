"""App domain tools; DSH separately owns general files, Shell and permissions."""
from __future__ import annotations

import copy
import hashlib
import re
import sqlite3
import uuid
from functools import partial
from itertools import islice
from types import MappingProxyType

from genshin_app.app_fault_store import session_fault_context
from genshin_app.app_system_fault import is_domain_exception, system_failure
from genshin_app.authoring_assistance import authoring_request_schema
from genshin_app.calculation_diagnostics import (
    calculation_diagnostics_contract,
    inspect_calculation_diagnostics,
)
from genshin_app.calculation_review import MAX_FILE_BYTES, review_calculation
from genshin_app.calculation_review_batch import review_batch_contract
from genshin_app.equipment.attack_source_assembly import assemble_attack_source
from genshin_app.equipment.attributes import prepare_attribute_review
from genshin_app.equipment.composer import apply_data_update
from genshin_app.equipment.files import fingerprint, json_bytes
from genshin_app.equipment.panel_basis import character_panel_basis
from genshin_app.equipment.panel_reference import (
    REQUEST_SCHEMA as PANEL_REFERENCE_SCHEMA,
)
from genshin_app.equipment.panel_reference import (
    panel_reference_contract,
    reference_panel,
)
from genshin_app.equipment.periodic_source_assembly import assemble_periodic_source
from genshin_app.equipment.plan_description import (
    app_mechanics_capabilities as mechanics_capabilities,
)
from genshin_app.equipment.runtime_capabilities import (
    app_runtime_capabilities as runtime_capabilities,
)
from genshin_app.equipment.schema import PLAN_SCHEMA, PlanError, _check, _json_guard
from genshin_app.equipment.service import plan_capabilities
from genshin_app.paths import skill_dir
from genshin_app.public_appendix_query import (
    APPENDIX_BODY_SCHEMA,
    APPENDIX_CATEGORIES_SCHEMA,
)
from genshin_app.public_appendix_query import (
    query_appendix as _appendix_query,
)
from genshin_app.public_appendix_query import (
    query_appendix_categories as _appendix_category_index,
)
from genshin_app.whiteboard import whiteboard_capabilities
from genshin_app.workflow import audit_record_schema, new_audit, validate_audit
from genshin_db import queries
from genshin_db.context_query import (
    CONTEXT_CAPABILITIES,
    context_request_schema,
    query_context,
)
from genshin_db.equipment import EquipmentError
from genshin_db.runtime_queries import query_runtime_public as query_runtime
from genshin_db.runtime_request_contracts import runtime_request_schema

from .agent_hash_input import TOOL_DESCRIPTION as HASH_INPUT_DESCRIPTION
from .agent_hash_input import TOOL_SCHEMA as HASH_INPUT_SCHEMA
from .agent_hash_input import hash_input
from .agent_weapon_stats import TOOL_DESCRIPTION as WEAPON_STATS_DESCRIPTION
from .agent_weapon_stats import TOOL_SCHEMA as WEAPON_STATS_SCHEMA
from .agent_weapon_stats import query_weapon_stats
from .calculation_attempt_failure import CalculationOutputValidationFailure
from .character_appendix_index import (
    TOOL_DESCRIPTION as APPENDIX_INDEX_DESCRIPTION,
)
from .character_appendix_index import TOOL_SCHEMA as APPENDIX_INDEX_SCHEMA
from .character_appendix_index import (
    appendix_navigation_capabilities,
    get_character_appendix_index,
)
from .native_text_safety import sanitize_public_text
from .sessions import write_owned
from .skill_context import FOCUS_RESOURCES as _CURRENT_STAGE_SKILL

MAX_ARGUMENTS = 512 * 1024
MAX_RESPONSE = 256 * 1024
MAX_RESOURCE = 4 * 1024 * 1024
MAX_RESOURCES = 128
MAX_RESOURCE_TOTAL = 64 * 1024 * 1024
PHASE_INDEX = {phase: index for index, phase in enumerate((
    "loadout_draft", "action_checkpoint", "event_topology",
    "numeric_model", "calculation_closure"))}
TEXT = {"type": "string", "maxLength": 4096}
REVISION = {"type": "integer", "minimum": 0}
OBJECT = {"type": "object"}


def _schema(fields, required=()):
    return {"type": "object", "properties": fields, "required": list(required), "additionalProperties": False}


SPECS = {
    "gs_get_character_appendix_index": (APPENDIX_INDEX_DESCRIPTION, copy.deepcopy(APPENDIX_INDEX_SCHEMA)),
    "gs_hash_input": (HASH_INPUT_DESCRIPTION, copy.deepcopy(HASH_INPUT_SCHEMA)),
    "gs_weapon_stats": (WEAPON_STATS_DESCRIPTION, copy.deepcopy(WEAPON_STATS_SCHEMA)),
    "gs_reference_panel": ("按主属性与聚合有效词条复算只读参考面板、预算及差异；只作提醒，不改候选、不授接纳，成品面板无需逆推。百分比用ratio，词条均值需注明来源。",
        _schema({"request": copy.deepcopy(PANEL_REFERENCE_SCHEMA)}, ("request",))),
    "gs_read_context": (("按资源根的 JSON Pointer 读取候选、合同、报告或资料。原生能力的 /engine_v2 不含独立 CLI 返回包的 data.plan_contract。"
                         "先读 capability_index 发现精确入口；有 next_offset 或 reference 时可继续读取同一资源。"
                         "publication_usage 接收 request={entities:[{type,id,version_line?}],purpose:answer或calculation,scope?}，"
                         "只列本任务拟采用的实体，复用同任务的实际用户回答；普通资料浏览无需调用。"),
        _schema({"resource": TEXT, "pointer": TEXT, "offset": REVISION,
                 "request": {"type": "object"},
                 "limit": {"type": "integer", "minimum": 1, "maximum": 200}}, ("resource",))),
    "gs_read_skill": ("读取捆绑Skill及预定义参考资源；省略resource列出可用资源，不接受任意文件路径。",
        _schema({"resource": TEXT, "offset": REVISION, "max_chars": {"type": "integer", "minimum": 1}})),
    "gs_query_context": ("定向读取固定数据库中的角色、武器、圣遗物完整原文和所选参数；related_sources可加下级附录目录，目录不是正文。source-notebook:query-N可读无损小档及解释骨架。",
        _schema({"request": context_request_schema(), "related_sources": {"type": "boolean"}}, ("request",))),
    "gs_query_runtime": ("查询固定快照的运行资料与结构化关系；request.action必填。先用gs_read_context读取runtime_contracts的/actions/<action>精确合同；GUI方法不是runtime action。不执行游戏脚本或更改数据库。",
        _schema({"request": runtime_request_schema()}, ("request",))),
    "gs_query_appendix": (("按精确目标、分类、版本线与审核状态查询当前固定数据库快照中的资料附录，支持角色相关目标及武器等类型；"
                            "approved与pending必须分开查询，结果没有执行权。"),
        copy.deepcopy(APPENDIX_BODY_SCHEMA)),
    "gs_list_appendix_categories": (("按精确目标、版本线与审核状态列出附录分类和条目数；"
                                       "只用于发现，不返回标题、正文或执行资料。"),
        copy.deepcopy(APPENDIX_CATEGORIES_SCHEMA)),
    "gs_patch_candidate": ("修改当前候选游戏输入：默认patch支持JSON Pointer及selector={collection,id,id_field}稳定ID定位；mode=replace提交input，mode=import提交file_path及可选expected_file_sha。必须带当前修订和理由。原生输入是主要事实，App生成白板投影，无需手工双写。允许未闭合草稿；实际计算仍严格校验。系统身份、批准和结果不能伪造。输入变化使旧计算失效；无变化不增加修订。",
        _schema({"expected_revision": REVISION, "reason": TEXT, "mode": {"enum": ["patch", "replace", "import"]},
            "input": OBJECT, "file_path": TEXT, "expected_file_sha": TEXT, "expected_input_fingerprint": TEXT,
            "preview": {"type": "boolean"},
            "operations": {"type": "array", "minItems": 1, "maxItems": 128,
            "items": _schema({"op": {"enum": ["add", "replace", "remove"]}, "path": TEXT,
                "selector": OBJECT, "value": {}}, ("op", "path"))}},
            ("expected_revision", "reason"))),
    "gs_assemble_attack": ("复用受限战技伤害来源装配器补齐候选来源；不修改用户动作或自动批准来源。",
        _schema({"expected_revision": REVISION, "request": OBJECT, "reason": TEXT}, ("expected_revision", "request", "reason"))),
    "gs_assemble_periodic": ("复用受限多段来源装配器补齐候选，不能推断完整技能派发；不自动批准来源。",
        _schema({"expected_revision": REVISION, "request": OBJECT, "reason": TEXT}, ("expected_revision", "request", "reason"))),
    "gs_refresh_data_refs": ("将候选装备资料版本引用同步到本会话固定数据库快照并重新组合；遵循Skill与用户请求范围，不修改数据库，不自动确认手工效果或外部伤害。数据库基础模式的白值更新会进入候选差异，仍需重新计算并审核。",
        _schema({"expected_revision": REVISION, "reason": TEXT}, ("expected_revision", "reason"))),
    "gs_compose": ("重新组合候选并列出缺项，完整预览通过gs_read_context的report读取；不自动刷新人工或外部确认。",
        _schema({"expected_revision": REVISION}, ("expected_revision",))),
    "gs_calculate": ("用当前候选和固定数据库重新计算，保存真实引擎回执；部分执行/能量问题仍明确保留。",
        _schema({"expected_revision": REVISION}, ("expected_revision",))),
    "gs_review_result": ("分页审查最近一次当前候选的实际伤害、动作、Buff、采样、外部补充、资源累计/读数和机制流水；不凭空生成覆盖结论。",
        _schema({"expected_revision": REVISION, "request": OBJECT}, ("expected_revision",))),
    "gs_review_result_batch": ("在一次完整核验中审查当前候选的1至16个页面或视图。较大结果保存为有身份的历史投影，定向读取不重放；历史字段不能批准当前候选或对当前计算解旧指针。",
        _schema({"expected_revision": REVISION, "request": OBJECT}, ("expected_revision", "request"))),
    "gs_record_finding": ("登记可选工作资料：task_source保存原题原文，requirement保存要求及disposition，source_note/observation保存解释与预期，lookup/issue/decision兼容。record或records二选一支持批量，supersedes替代旧结论，input_refs为当前输入JSON Pointer；只作辅助，无执行或批准权。task_evidence资源读取当前差异；不能假装上网。",
        _schema({"record": audit_record_schema(), "records": {"type": "array", "items": audit_record_schema(), "minItems": 1}}, ())),
    "gs_transition_phase": ("在同一会话中转换工作阶段。前进只能进入相邻阶段且须通过阶段门；可回退重做。阶段摘要使用独立stage_revision防止旧指令覆盖。",
        _schema({"target_phase": {"enum": ["loadout_draft", "action_checkpoint", "event_topology",
                                                    "numeric_model", "calculation_closure"]},
                 "expected_revision": REVISION, "expected_stage_revision": REVISION, "reason": TEXT,
                 "energy_intent": {"enum": ["single_window_legal", "multi_round_legal", "sustainable_loop"]}},
                ("target_phase", "expected_revision", "expected_stage_revision", "reason"))),
    "gs_validate_topology": ("仅校验Agent声明的事件拓扑、来源盘点、调度形状和参数槽覆盖；不执行角色特有逻辑，不生成伤害或DPS。",
        _schema({"expected_revision": REVISION}, ("expected_revision",))),
    "gs_validate_execution_bindings": ("校验事件拓扑节点是否映射到当前候选中的强类型可执行输入对象；只证明引用覆盖和新鲜度，不核实角色特有事实。",
        _schema({"expected_revision": REVISION}, ("expected_revision",))),
    "gs_validate_agent_materialization": ("校验Agent自包含的通用机制材料化输入；或通过可选request生成通用机制、攻击组合及绑定刷新草稿。request.kind=catalog可查当前辅助能力。外层和request.expected_revision均填写当前会话候选修订；返回edit_request可直接经候选编辑工具采用，input_identity.revision是独立的文件内部修订。只读，不查询数据库，不核实角色特有事实，不应用、不确认来源、不改变接纳。",
        _schema({"expected_revision": REVISION, "request": authoring_request_schema()}, ("expected_revision",))),
    "gs_verify_public_data_lookup": (
        "让App按真实ID或精确中文名核对一项当前方案临时公共资料。查询失败只报告不可用，绝不当成数据库缺失；本工具不批准纠错。",
        _schema({
            "expected_revision": REVISION,
            "provisional_id": {"type": "string", "minLength": 1, "maxLength": 128},
            "database_id": {"anyOf": [
                {"type": "integer", "minimum": 1}, {"type": "null"},
            ]},
            "reason": TEXT,
        }, ("expected_revision", "provisional_id", "reason"))),
    "gs_attest_database_missing": (
        "在App查询明确未找到且Agent已提供至少一条核实来源后，登记当前方案的数据库缺失兜底；不冒充用户批准或公共数据库事实。",
        _schema({
            "expected_revision": REVISION,
            "provisional_id": {"type": "string", "minLength": 1, "maxLength": 128},
            "reason": TEXT,
        }, ("expected_revision", "provisional_id", "reason"))),
    "gs_request_public_data_patch_approval": (
        "为偏离数据库现存事实的精确差异创建用户确认问题。必须把返回的question原样交给用户问答工具；Agent不能自行批准。",
        _schema({
            "expected_revision": REVISION,
            "provisional_id": {"type": "string", "minLength": 1, "maxLength": 128},
            "reason": TEXT,
        }, ("expected_revision", "provisional_id", "reason"))),
}


SPECS["gs_diagnose_result"] = (
    "分组或分页查看当前计算问题。request.view=assistance只读给逐Q/资源/输入与执行软对照，无计算时也可看输入；view=energy_trials/input_trials在副本用真实引擎比较明确面板或输入补丁，observations可核对真实结果，不更新候选/批准。先按需读diagnostics_capabilities的auxiliary_views；建议不影响准入。旧groups/issues仍读取当前修订缓存。",
    _schema({"expected_revision": REVISION, "request": OBJECT}, ("expected_revision",)))
SPECS['gs_adopt_attributes'] = (
    '读取并核对属性事实后显式采用当前基底和分类，自动对齐组件/上下文指纹。必须说明依据；unknown不自动变已知。修改进入候选，需重新计算和用户审核。',
    _schema({'expected_revision':REVISION,'note':TEXT}, ('expected_revision','note')))
SPECS['gs_preview_attribute_recovery'] = (
    "对当前原生输入或尚未提交的patch生成无执行权属性账骨架；用于旧转换闭包在compose前被拒的恢复。只读，不采用来源、不修改候选或计算。",
    _schema({"expected_revision": REVISION,
             "operations": copy.deepcopy(SPECS['gs_patch_candidate'][1]['properties']['operations'])},
            ("expected_revision",)))
CALCULATION_TOOLS = (frozenset(SPECS) - {"gs_transition_phase", "gs_validate_topology",
                                        "gs_validate_execution_bindings",
                                        "gs_validate_agent_materialization",
                                        "gs_verify_public_data_lookup",
                                        "gs_attest_database_missing",
                                        "gs_request_public_data_patch_approval",
                                        "gs_query_appendix", "gs_list_appendix_categories",
                                        "gs_get_character_appendix_index"}) | {
    "gs_search_catalog", "gs_character_panel_basis"}
SPECS.update({
    "gs_patch_whiteboard": ("修改白板共创草稿的队伍、装备、面板、动作、容量意图或动作接纳意图。允许空队伍和部分配置，但必须保持白板结构合法；身份和版本线固定。动作接纳首片只接受release、C0、default元素爆发；context与initial_ready_in默认null，只有用户明确选择普通开放世界预设才同时写固定场景与[]。数据库基础模式会按角色等级与武器自动同步白值，成品面板不会推算。修改仅进入待用户审核候选。",
        copy.deepcopy(SPECS["gs_patch_candidate"][1])),
    "gs_add_character": ("按已查询确认的数据库角色ID向共创白板添加角色。默认90级、0命；读取角色中文名和基础值，未指定武器时总基础攻击未知。成品面板保持null，不造数值。",
        _schema({"expected_revision": REVISION, "reason": TEXT,
                 "db_character_id": {"type": "integer", "minimum": 1, "maximum": 1_000_000_000},
                 "actor_id": TEXT, "level": {"type": "integer", "minimum": 1, "maximum": 100},
                 "constellation": {"type": "integer", "minimum": 0, "maximum": 6}},
                ("expected_revision", "reason", "db_character_id"))),
    "gs_character_panel_basis": ("读取固定数据库中90或100级角色基础属性及所选武器基础攻击；返回来源与缺项，不修改草稿、不估算成品面板。",
        _schema({"db_character_id": {"type": "integer", "minimum": 1, "maximum": 1_000_000_000},
                 "level": {"type": "integer", "minimum": 1, "maximum": 100},
                 "weapon": {"anyOf": [OBJECT, {"type": "null"}]}}, ("db_character_id",))),
    "gs_search_catalog": ("按中文名搜索当前固定版本线的公共角色、武器或套装，返回真实ID与中文名。不要凭记忆填写ID；同名多条时按ID与资料确认。",
        _schema({"kind": {"enum": ["character", "weapon", "artifact_set"]},
                 "query": {"type": "string", "minLength": 1, "maxLength": 128},
                 "page": {"type": "integer", "minimum": 1, "maximum": 10000},
                 "page_size": {"type": "integer", "minimum": 1, "maximum": 30}}, ("kind", "query"))),
})
BUILD_TOOLS = frozenset(("gs_read_context", "gs_read_skill", "gs_query_context", "gs_query_runtime",
                         "gs_record_finding", "gs_patch_whiteboard", "gs_add_character", "gs_character_panel_basis", "gs_weapon_stats", "gs_reference_panel", "gs_hash_input", "gs_search_catalog"))
STAGED_TOOLS = frozenset(SPECS)

# Native V2 does not infer read authority from a ``gs_`` prefix or a tool
# name.  This explicit policy is intersected with the real stage projection
# before any definition is disclosed to the sidecar.  ``session_resource_read``
# tools may materialize a bounded, App-owned query resource, but they never
# mutate the candidate or advance the workflow stage.
NATIVE_V2_READ_POLICIES = MappingProxyType({
    "gs_preview_attribute_recovery": "pure_read",
    "gs_hash_input": "pure_read",
    "gs_read_context": "pure_read",
    "gs_read_skill": "pure_read",
    "gs_query_context": "session_resource_read",
    "gs_query_runtime": "session_resource_read",
    "gs_query_appendix": "session_resource_read",
    "gs_list_appendix_categories": "pure_read",
    "gs_get_character_appendix_index": "session_resource_read",
    "gs_character_panel_basis": "pure_read",
    "gs_weapon_stats": "pure_read",
    "gs_reference_panel": "pure_read",
    "gs_search_catalog": "pure_read",
})

# Native V2 never derives authority from a tool-name prefix.  Every domain
# tool that may be disclosed by ``definitions_for_session`` has one explicit
# execution policy and one explicit effect class.  The unified native host
# intersects this table with the current-stage definition projection and
# rejects a missing or extra policy entry.  Effect classes describe App-owned
# state transitions; they are intentionally more precise than a read/write
# boolean so compose/calculation outputs cannot be confused with candidate
# edits.
NATIVE_V2_TOOL_POLICIES = MappingProxyType({
    "gs_preview_attribute_recovery": MappingProxyType({"policy": "domain_read", "effect": "none"}),
    "gs_hash_input": MappingProxyType({"policy": "domain_read", "effect": "none"}),
    "gs_read_context": MappingProxyType({"policy": "domain_read", "effect": "none"}),
    "gs_read_skill": MappingProxyType({"policy": "domain_read", "effect": "none"}),
    "gs_query_context": MappingProxyType({"policy": "database_query", "effect": "session_resource"}),
    "gs_query_runtime": MappingProxyType({"policy": "database_query", "effect": "session_resource"}),
    "gs_query_appendix": MappingProxyType({"policy": "database_query", "effect": "session_resource"}),
    "gs_list_appendix_categories": MappingProxyType({"policy": "database_read", "effect": "none"}),
    "gs_get_character_appendix_index": MappingProxyType({"policy": "database_query", "effect": "session_resource"}),
    "gs_patch_candidate": MappingProxyType({"policy": "candidate_write", "effect": "candidate"}),
    "gs_patch_whiteboard": MappingProxyType({"policy": "candidate_write", "effect": "candidate"}),
    "gs_add_character": MappingProxyType({"policy": "candidate_write", "effect": "candidate"}),
    "gs_assemble_attack": MappingProxyType({"policy": "candidate_write", "effect": "candidate"}),
    "gs_assemble_periodic": MappingProxyType({"policy": "candidate_write", "effect": "candidate"}),
    "gs_refresh_data_refs": MappingProxyType({"policy": "candidate_write", "effect": "candidate_and_derived"}),
    "gs_adopt_attributes": MappingProxyType({"policy": "candidate_write", "effect": "candidate_and_derived"}),
    "gs_compose": MappingProxyType({"policy": "derived_compute", "effect": "derived_compose"}),
    "gs_calculate": MappingProxyType({"policy": "derived_compute", "effect": "derived_calculation"}),
    "gs_diagnose_result": MappingProxyType({"policy": "domain_read", "effect": "none"}),
    "gs_review_result": MappingProxyType({"policy": "domain_read", "effect": "none"}),
    "gs_review_result_batch": MappingProxyType({"policy": "domain_read", "effect": "review_projection"}),
    "gs_record_finding": MappingProxyType({"policy": "session_record_write", "effect": "finding"}),
    "gs_character_panel_basis": MappingProxyType({"policy": "database_read", "effect": "none"}),
    "gs_weapon_stats": MappingProxyType({"policy": "database_read", "effect": "none"}),
    "gs_reference_panel": MappingProxyType({"policy": "database_read", "effect": "none"}),
    "gs_search_catalog": MappingProxyType({"policy": "database_read", "effect": "none"}),
    "gs_transition_phase": MappingProxyType({"policy": "stage_transition", "effect": "stage_transition"}),
    "gs_validate_topology": MappingProxyType({"policy": "derived_validation", "effect": "derived_validation"}),
    "gs_validate_execution_bindings": MappingProxyType({"policy": "derived_validation", "effect": "derived_validation"}),
    "gs_validate_agent_materialization": MappingProxyType({"policy": "derived_validation", "effect": "derived_validation"}),
    "gs_verify_public_data_lookup": MappingProxyType({"policy": "candidate_write", "effect": "candidate"}),
    "gs_attest_database_missing": MappingProxyType({"policy": "candidate_write", "effect": "candidate"}),
    "gs_request_public_data_patch_approval": MappingProxyType({"policy": "candidate_write", "effect": "candidate"}),
})

_STAGED_KNOWLEDGE_READS = frozenset({
    "gs_preview_attribute_recovery",
    "gs_hash_input",
    "gs_read_context",
    "gs_read_skill",
    "gs_query_context",
    "gs_query_runtime",
    "gs_query_appendix",
    "gs_list_appendix_categories",
    "gs_get_character_appendix_index",
    "gs_character_panel_basis",
    "gs_weapon_stats",
    "gs_reference_panel",
    "gs_search_catalog",
})
_STAGED_COMMON = _STAGED_KNOWLEDGE_READS | {
    "gs_record_finding",
    "gs_transition_phase",
}
_STAGED_PHASE_TOOLS = {
    "loadout_draft": _STAGED_COMMON | {
        "gs_patch_whiteboard", "gs_add_character",
    },
    "action_checkpoint": _STAGED_COMMON | {"gs_patch_whiteboard"},
    "event_topology": _STAGED_COMMON | {
        "gs_patch_candidate", "gs_validate_topology",
        "gs_verify_public_data_lookup", "gs_attest_database_missing",
        "gs_request_public_data_patch_approval",
    },
    "numeric_model": _STAGED_COMMON | {"gs_patch_candidate", "gs_validate_topology",
        "gs_validate_execution_bindings", "gs_assemble_attack",
        "gs_validate_agent_materialization",
        "gs_verify_public_data_lookup", "gs_attest_database_missing",
        "gs_request_public_data_patch_approval",
        "gs_assemble_periodic", "gs_refresh_data_refs", "gs_compose", "gs_adopt_attributes"},
    "calculation_closure": _STAGED_COMMON | {"gs_patch_candidate", "gs_validate_topology",
        "gs_validate_execution_bindings", "gs_assemble_attack",
        "gs_validate_agent_materialization",
        "gs_assemble_periodic", "gs_refresh_data_refs", "gs_compose", "gs_calculate", "gs_diagnose_result",
        "gs_adopt_attributes", "gs_review_result", "gs_review_result_batch"},
}

_STAGED_RECOMMENDED_TOOLS = {
    "loadout_draft": {
        "gs_read_context", "gs_read_skill", "gs_search_catalog",
        "gs_query_context", "gs_character_panel_basis",
        "gs_get_character_appendix_index", "gs_query_appendix", "gs_list_appendix_categories",
        "gs_patch_whiteboard", "gs_add_character", "gs_record_finding",
        "gs_transition_phase",
    },
    "action_checkpoint": {
        "gs_read_context", "gs_read_skill", "gs_query_context",
        "gs_query_runtime", "gs_query_appendix", "gs_list_appendix_categories",
        "gs_patch_whiteboard", "gs_record_finding", "gs_transition_phase",
    },
    "event_topology": {
        "gs_read_context", "gs_read_skill", "gs_query_context",
        "gs_query_runtime", "gs_query_appendix", "gs_list_appendix_categories",
        "gs_patch_candidate", "gs_validate_topology", "gs_record_finding",
        "gs_verify_public_data_lookup", "gs_attest_database_missing",
        "gs_request_public_data_patch_approval",
        "gs_transition_phase",
    },
    "numeric_model": {
        "gs_read_context", "gs_read_skill", "gs_query_context",
        "gs_query_runtime", "gs_query_appendix", "gs_patch_candidate",
        "gs_validate_topology", "gs_validate_execution_bindings",
        "gs_validate_agent_materialization",
        "gs_verify_public_data_lookup", "gs_attest_database_missing",
        "gs_request_public_data_patch_approval",
        "gs_assemble_attack", "gs_assemble_periodic", "gs_compose",
        "gs_record_finding", "gs_transition_phase",
    },
    "calculation_closure": {
        "gs_read_context", "gs_read_skill", "gs_query_context",
        "gs_query_runtime", "gs_query_appendix", "gs_calculate",
        "gs_diagnose_result", "gs_review_result", "gs_review_result_batch",
        "gs_record_finding",
    },
}


def _allowed(workflow):
    return (BUILD_TOOLS if workflow == "build" else CALCULATION_TOOLS if workflow == "calculate"
            else STAGED_TOOLS if workflow in {"staged", "artifact"} else frozenset())


def _allowed_session(session):
    if session.workflow == "artifact":
        allowed = set(STAGED_TOOLS)
        if session._is_board():
            # These consume native-plan structures, rather than a focus label.
            allowed -= {"gs_patch_candidate", "gs_assemble_attack", "gs_assemble_periodic",
                        "gs_refresh_data_refs", "gs_adopt_attributes", "gs_diagnose_result",
                        "gs_review_result", "gs_review_result_batch",
                        "gs_verify_public_data_lookup", "gs_attest_database_missing",
                        "gs_request_public_data_patch_approval",
                        "gs_validate_topology", "gs_validate_execution_bindings"}
        else:
            allowed -= {"gs_patch_whiteboard", "gs_add_character"}
        return frozenset(allowed)
    if session.workflow != "staged":
        return _allowed(session.workflow)
    allowed = _STAGED_PHASE_TOOLS.get(session.workflow_stage, frozenset())
    if session.workflow_stage in ("loadout_draft", "action_checkpoint") and not session._is_board():
        allowed = (allowed - {"gs_patch_whiteboard", "gs_add_character"}) | {"gs_patch_candidate"}
    return allowed


def definitions(workflow="calculate"):
    return [{"type": "function", "name": name, "description": description, "inputSchema": copy.deepcopy(schema)}
            for name, (description, schema) in SPECS.items() if name in _allowed(workflow)]


def definitions_for_session(session):
    """Return only the tools visible in the session's current stage.

    CLI threads historically receive the workflow superset once and rely on
    ``call_tool`` for the authoritative stage gate.  Stateless native API
    requests can do better: this projection removes undisclosed future-stage
    schemas from the model context while keeping the same server-side gate.
    """
    return [
        {"type": "function", "name": name,
         "description": ("调整建议工作重点；不控制可写范围或计算许可。prepare_calculation=true显式将已有角色/动作的白板准备为原生计算工件；energy_intent声明能量证明范围。"
                         if session.workflow == "artifact" and name == "gs_transition_phase" else description),
         "inputSchema": _tool_schema_for_session(session, name)}
        for name, (description, schema) in SPECS.items()
        if name in _allowed_session(session)
    ]


def _tool_schema_for_session(session, name):
    schema = copy.deepcopy(SPECS[name][1])
    if session.workflow == "artifact" and name == "gs_transition_phase":
        schema["properties"]["prepare_calculation"] = {"type": "boolean"}
    return schema


def recommended_tools_for_session(session):
    """Return a compact phase reading/execution guide, never an allowlist."""

    allowed = set(_allowed_session(session))
    if getattr(session, "workflow", None) not in {"staged", "artifact"}:
        return sorted(allowed)
    recommended = _STAGED_RECOMMENDED_TOOLS.get(session.workflow_stage, set())
    return sorted((recommended | {"gs_get_character_appendix_index"}) & allowed)


def _skill_resource_paths():
    """Return the complete, path-safe Markdown bundle exposed to an Agent.

    Stage disclosure is guidance about what to read next, not an information
    security boundary.  Every Markdown file deliberately shipped inside the
    dedicated ``skill/`` bundle is calculation documentation and remains
    available in every workflow stage.  Arbitrary paths, symlinks and
    non-Markdown files are still excluded here.
    """

    root = skill_dir().resolve()
    return {
        str(path.relative_to(root)).replace("\\", "/"): path
        for path in root.rglob("*.md")
        if not path.is_symlink() and root in path.resolve().parents
    }


def skill_resource_names():
    """Return stable public resource IDs for all bundled Skill Markdown."""

    return sorted(_skill_resource_paths())


def skill_access_descriptor():
    """Describe full on-demand access without injecting the resource index."""

    return {
        "policy": "all_bundled_markdown_on_demand",
        "index_tool": "gs_read_skill",
        "resource_count": len(skill_resource_names()),
    }


def _limits(value, schema, path=""):
    # The legacy native schema walker does not implement these JSON Schema bounds.
    if isinstance(value, str):
        maximum = schema.get("maxLength", MAX_ARGUMENTS)
        if len(value) > maximum:
            raise PlanError(f"{path or '/'} 有 {len(value)} 个字符，上限为 {maximum}。", "agent_size_limit",
                            {"path": path, "actual": len(value), "maximum": maximum, "unit": "characters"})
    if isinstance(value, list):
        maximum = schema.get("maxItems", MAX_ARGUMENTS)
        if len(value) > maximum:
            raise PlanError(f"{path or '/'} 有 {len(value)} 项，上限为 {maximum}。", "agent_size_limit",
                            {"path": path, "actual": len(value), "maximum": maximum, "unit": "items"})
        for index, item in enumerate(value):
            _limits(item, schema.get("items", {}), _pointer(path, index))
    if isinstance(value, dict):
        for key, item in value.items():
            _limits(item, schema.get("properties", {}).get(key, {}), _pointer(path, key))


def _bounded_error_details(details):
    if details is None:
        return None
    size = len(json_bytes(details))
    if size <= 16000:
        return details
    keys = ("path", "parent_pointer", "resource", "pointer", "recovery", "limit", "actual", "maximum", "revision")
    kept = {key: details[key] for key in keys if key in details and len(json_bytes(details[key])) <= 1024} if isinstance(details, dict) else {}
    return {**kept, "message": "详细内容超过工具回执容量，请按给出的恢复指引处理。",
            "detail_omitted": True, "detail_bytes": size}


def _tokens(pointer):
    if not isinstance(pointer, str) or len(pointer) > 4096 or pointer and not pointer.startswith("/"):
        raise PlanError("需要合法JSON Pointer", "agent_tool_input")
    if pointer == "":
        return []
    values = pointer[1:].split("/")
    for value in values:
        import re
        if re.search(r"~(?![01])", value):
            raise PlanError("JSON Pointer转义不合法", "agent_tool_input")
    return [value.replace("~1", "/").replace("~0", "~") for value in values]


def _at(value, parts):
    for index, part in enumerate(parts):
        if isinstance(value, list):
            if part.isascii() and part.isdecimal() and len(part) <= len(str(len(value))):
                key = int(part)
                if str(key) == part and key < len(value):
                    value = value[key]
                    continue
        elif isinstance(value, dict) and part in value:
            value = value[part]
            continue
        parent = "".join(_pointer("", key) for key in parts[:index])
        message = "JSON数组位置不存在" if isinstance(value, list) else "JSON位置不存在"
        raise PlanError(message, "agent_tool_input",
                        {"path": _pointer(parent, part), "parent_pointer": parent})
    return value


def _pointer(parent, key):
    return parent + "/" + str(key).replace("~", "~0").replace("/", "~1")


def page(value, resource, pointer="", offset=0, limit=50, *, byte_budget=MAX_RESPONSE):
    try:
        value = _at(value, _tokens(pointer))
    except PlanError as error:
        if isinstance(error.details, dict) and "parent_pointer" in error.details:
            parent = error.details["parent_pointer"]
            raise PlanError(str(error), error.reason, {
                "path": error.details["path"],
                "repair_hint": "pointer 相对所选 resource 的根。先查看最近存在的父位置，或从 capability_index 找到准确入口。",
                "recovery": {"action": "read_parent_then_correct_pointer", "tool": "gs_read_context",
                             "resource": resource, "pointer": parent, "retry_same_request": False},
            }) from error
        raise
    response = {"resource": resource, "pointer": pointer, "offset": offset}

    def fits(payload):
        return len(json_bytes({"code": 0, "data": payload})) <= byte_budget

    if isinstance(value, (dict, list)):
        kind = "object" if isinstance(value, dict) else "array"
        total = len(value)
        entries = (() if offset >= total else islice(value.items(), offset, offset + limit)
                   if isinstance(value, dict) else enumerate(value[offset:offset + limit], start=offset))
        selected = []
        payload = {**response, "kind": kind, "total": total, "items": selected, "next_offset": max(total, 1000)}
        size = len(json_bytes({"code": 0, "data": payload}))
        for key, item in entries:
            if len(json_bytes(item)) > 16000:
                item = {"reference": {"resource": resource, "pointer": _pointer(pointer, key)},
                        "kind": "object" if isinstance(item, dict) else "array" if isinstance(item, list) else "text",
                        "length": len(item) if hasattr(item, "__len__") else None}
            row = {"key": key, "value": item}
            row_bytes = len(json_bytes(row)) + (1 if selected else 0)
            if size + row_bytes > byte_budget:
                break
            selected.append(row)
            size += row_bytes
        next_offset = offset + len(selected)
        payload["next_offset"] = next_offset if next_offset < total else None
        if (offset >= total or selected) and fits(payload):
            return payload
    if isinstance(value, str):
        total = len(value)

        def text_page(size):
            end = offset + size
            return {**response, "kind": "text", "total": total, "text": value[offset:end],
                    "next_offset": end if end < total else None}

        maximum = max(0, min(16000, total - offset))
        payload = text_page(maximum)
        if fits(payload):
            return payload
        low, high = 0, maximum
        while low < high:
            middle = (low + high + 1) // 2
            if fits(text_page(middle)):
                low = middle
            else:
                high = middle - 1
        if low:
            return text_page(low)
    elif not isinstance(value, (dict, list)):
        payload = {**response, "kind": "scalar", "value": value}
        if fits(payload):
            return payload
    raise PlanError("资料页超出回执容量，请缩短资源指针或读取更小的子树", "agent_response_limit")


def _staged_board_view(session, *, original=False):
    board = copy.deepcopy(session.document if original else session._current_board())
    phase = session.workflow_stage
    if phase in ("numeric_model", "calculation_closure"):
        return board
    identity = {key: board[key] for key in (
        "kind", "schema_version", "board_id", "version_line", "revision", "title", "duration")}
    if phase == "loadout_draft":
        identity["team"] = [{key: copy.deepcopy(actor[key]) for key in (
            "id", "db_character_id", "name", "level", "constellation", "weapon",
            "artifact_sets", "panel", "panel_basis_mode") if key in actor}
            for actor in board["team"]]
        identity["stage_projection"] = {
            "phase": phase, "hidden_actions": len(board["actions"]),
            "hidden_capacity_intents": len(board.get("capacity_intents", [])),
            "hidden_action_admission_intents": len(board.get("action_admission_intents", [])),
        }
        return identity
    identity["team"] = []
    for actor in board["team"]:
        item = {key: copy.deepcopy(actor[key]) for key in (
            "id", "db_character_id", "name", "level", "constellation")}
        weapon = actor.get("weapon")
        item["loadout_summary"] = {
            "weapon": ({key: copy.deepcopy(weapon[key]) for key in (
                "weapon_id", "refinement") if key in weapon}
                       if isinstance(weapon, dict) else None),
            "artifact_sets": [{key: copy.deepcopy(selected[key]) for key in (
                "set_id", "count") if key in selected}
                for selected in actor.get("artifact_sets", [])
                if isinstance(selected, dict)],
            "panel_basis_mode": actor.get("panel_basis_mode", "database"),
        }
        identity["team"].append(item)
    identity["actions"] = copy.deepcopy(board["actions"])
    identity["stage_projection"] = {
        "phase": phase,
        "hidden_loadout_details": True,
        "hidden_capacity_intents": len(board.get("capacity_intents", [])),
        "hidden_action_admission_intents": len(board.get("action_admission_intents", [])),
    }
    return identity


_STAGED_CONTEXT_BASE = {
    "workflow_stage", "candidate_board", "original_board", "database_snapshot", "findings",
    "calculation_attempt",
    # Knowledge and mechanics capability documents are available in every
    # phase.  The phase card recommends what to read next; it is not an
    # information-security boundary for calculation documentation.
    "context_capabilities", "runtime_capabilities", "mechanics_capabilities",
    "appendix_navigation_capabilities",
}
_STAGED_TOPOLOGY_CONTEXT = {
    "event_topology_capabilities", "event_topology", "event_topology_receipt",
}
_STAGED_BINDING_CONTEXT = {
    "event_topology_binding_capabilities", "event_topology_bindings",
    "event_topology_binding_receipt",
}
_STAGED_MATERIALIZATION_CONTEXT = {
    "agent_execution_materialization_capabilities",
    "agent_execution_materialization",
    "agent_execution_materialization_receipt",
}


def _stage_context_resources(session):
    phase = session.workflow_stage
    base = set(_STAGED_CONTEXT_BASE)
    return {
        # `capabilities` is the single whiteboard capability name in early
        # phases.  The legacy `whiteboard_capabilities` alias is deliberately
        # omitted so one large document is not advertised twice.
        "loadout_draft": base | {
            "capabilities", "whiteboard_schema",
        },
        "action_checkpoint": base | {
            "capabilities", "whiteboard_schema",
        },
        "event_topology": base | _STAGED_TOPOLOGY_CONTEXT,
        "numeric_model": base | _STAGED_TOPOLOGY_CONTEXT | _STAGED_BINDING_CONTEXT | _STAGED_MATERIALIZATION_CONTEXT | {
            "candidate", "baseline", "report", "capabilities", "plan_schema",
        },
        "calculation_closure": base | _STAGED_TOPOLOGY_CONTEXT | _STAGED_BINDING_CONTEXT | _STAGED_MATERIALIZATION_CONTEXT | {
            "candidate", "baseline", "report", "calculated", "capabilities",
            "plan_schema", "diagnostic_error",
            "diagnostics_capabilities", "calculation_review_batch_capabilities",
        },
    }.get(phase, base)


def _staged_context_capabilities(phase):
    """Return the complete read-only database surface in every phase."""
    del phase
    return copy.deepcopy(CONTEXT_CAPABILITIES)


def _staged_runtime_capabilities(phase):
    """Return the complete read-only mechanics surface in every phase."""
    del phase
    return runtime_capabilities()


def _staged_whiteboard_schema(phase, schema):
    if phase == "loadout_draft":
        keys = ("workflow", "kind", "schema_version", "identity_locked", "revision",
                "examples_are_placeholders", "actor", "weapon", "artifact_set", "limits",
                "panel_policy")
        projected = {key: copy.deepcopy(schema[key]) for key in keys if key in schema}
        projected["editable"] = ["title", "notes", "team"]
    elif phase == "action_checkpoint":
        keys = ("workflow", "kind", "schema_version", "identity_locked", "revision",
                "action", "limits", "actions_policy")
        projected = {key: copy.deepcopy(schema[key]) for key in keys if key in schema}
        projected["editable"] = ["duration", "actions"]
    else:
        return copy.deepcopy(schema)
    projected["workflow_stage"] = phase
    projected["projection_policy"] = "只包含当前阶段可写字段；后续意图结构暂不披露。"
    return projected


def _staged_whiteboard_capabilities(phase):
    public = whiteboard_capabilities()
    common = {key: copy.deepcopy(public[key]) for key in (
        "kind", "schema_version", "max_bytes") if key in public}
    if phase == "loadout_draft":
        common.update({
            "limits": {"team": 4},
            "default_panel": copy.deepcopy(public["default_panel"]),
            "panel_basis": copy.deepcopy(public["panel_basis"]),
            "editable": ["title", "notes", "team"],
        })
    elif phase == "action_checkpoint":
        common.update({
            "max_actions": public["max_actions"],
            "max_duration": public["max_duration"],
            "action_types": copy.deepcopy(public["action_types"]),
            "editable": ["duration", "actions"],
        })
    else:
        return public
    common.update(
        contract="staged-whiteboard-capabilities-v1",
        workflow_stage=phase,
        projection_policy="当前阶段能力投影；未列出的白板能力尚未披露。",
    )
    return common


_CONTEXT_BOARD_RESOURCES = ("candidate_board", "original_board")


def _capability_family(session):
    if session.workflow == "staged" and session.workflow_stage in {"loadout_draft", "action_checkpoint"}:
        return "whiteboard_stage"
    return ("whiteboard" if session.workflow == "build" or
            session.workflow == "artifact" and session._is_board() else "genshin-plan")


def _fixed_context_getters(session):
    """The one fixed-resource table; constructing it executes no getter."""
    from genshin_app.equipment.agent_execution_materialization import (
        agent_execution_materialization_capabilities,
        lint_agent_execution_materialization,
        new_agent_execution_materialization,
    )
    from genshin_app.equipment.event_topology import event_topology_capabilities
    from genshin_app.equipment.event_topology_binding import (
        event_topology_binding_capabilities,
    )
    from genshin_app.input_editing import edit_contract

    from .board_candidate import whiteboard_schema
    def materialization_document():
        topology = session._topology()
        existing = session.candidate.get("extensions", {}).get(
            "agent_execution_materialization")
        return (existing if isinstance(existing, dict) else
                new_agent_execution_materialization(
                    session.candidate, topology,
                    "materialization." + topology["topology_id"]))

    def materialization_receipt():
        topology = session._topology()
        return lint_agent_execution_materialization(
            session.candidate, topology, materialization_document())
    from .native_event_topology_contracts import event_topology_contracts
    from .native_special_reaction_contracts import special_reaction_capabilities

    staged_phase = session.workflow_stage if session.workflow == "staged" else None
    from genshin_app.task_evidence import session_review
    values = {"task_evidence": lambda: session_review(session, observe=True), "candidate": lambda: session.candidate, "frozen": lambda: session.document, "baseline": lambda: session.baseline,
              "report": lambda: session.report,
              "findings": lambda: ([row for row, phase in zip(
                  session.findings, session.stage_state["finding_stages"], strict=True)
                  if PHASE_INDEX[phase] <= PHASE_INDEX[session.workflow_stage]]
                  if session.workflow == "staged" else session.findings),
              "calculated": lambda: session.calculated, "database_snapshot": lambda: session.database_snapshot,
              "diagnostic_error": lambda: _current_diagnostic_error(session),
              "calculation_attempt": lambda: _latest_calculation_attempt(session),
              "default_assistance": lambda: (session.report or {}).get("default_assistance"),
              "diagnostics_capabilities": calculation_diagnostics_contract,
              "input_edit_contract": edit_contract,
              "calculation_review_batch_capabilities": review_batch_contract,
              "capabilities": (lambda: _staged_whiteboard_capabilities(staged_phase))
                  if _capability_family(session) == "whiteboard_stage"
                  else whiteboard_capabilities if _capability_family(session) == "whiteboard" else plan_capabilities,
              "whiteboard_capabilities": whiteboard_capabilities, "mechanics_capabilities": mechanics_capabilities,
              "special_reaction_capabilities": special_reaction_capabilities,
              "event_topology_contracts": event_topology_contracts,
              "whiteboard_schema": (lambda: _staged_whiteboard_schema(
                  staged_phase, whiteboard_schema())) if session.workflow == "staged" else whiteboard_schema,
              "context_capabilities": (lambda: _staged_context_capabilities(staged_phase))
                  if session.workflow == "staged" else lambda: CONTEXT_CAPABILITIES,
              "runtime_capabilities": (lambda: _staged_runtime_capabilities(staged_phase))
                  if session.workflow == "staged" else runtime_capabilities,
              "appendix_navigation_capabilities": appendix_navigation_capabilities,
              "plan_schema": lambda: PLAN_SCHEMA,
              "panel_reference_contract": panel_reference_contract,
              "workflow_stage": lambda: session.stage_context(),
              "event_topology": lambda: session._topology(),
              "event_topology_receipt": lambda: session.topology_receipt(),
              "event_topology_capabilities": event_topology_capabilities,
              "event_topology_bindings": lambda: session._topology_bindings(),
              "event_topology_binding_receipt": lambda: session.topology_binding_receipt(),
              "event_topology_binding_capabilities": event_topology_binding_capabilities,
              "agent_execution_materialization_capabilities": agent_execution_materialization_capabilities,
              "agent_execution_materialization": materialization_document,
              "agent_execution_materialization_receipt": materialization_receipt}
    return values


def fixed_context_resource_names(session):
    """Discover actual fixed routes, not generated data or a permission list."""
    from .native_tool_contracts import RESOURCE_NAMES
    return sorted({*_fixed_context_getters(session), *RESOURCE_NAMES, *_CONTEXT_BOARD_RESOURCES,
                   *_request_context_getters()})


def _publication_usage(session, args):
    from genshin_app.publication_usage import prepare_publication_usage
    if args.get("pointer") or args.get("offset") or "limit" in args:
        raise PlanError("资料采用确认按本次选定范围一次返回，请省略分页参数", "invalid_publication_usage")
    return prepare_publication_usage(session.db_path, args.get("request"), task_id=session.session_id)


def _request_context_getters():
    return {"publication_usage": _publication_usage}


def _read_context(session, args):
    getter = _request_context_getters().get(args["resource"])
    if getter is not None:
        return getter(session, args)
    if "request" in args:
        raise PlanError("request 只用于 publication_usage，其他资料使用 pointer 读取", "agent_tool_input")
    if args["resource"].startswith("source-notebook:"):
        from genshin_app.task_evidence import source_notebook
        name = args["resource"].split(":", 1)[1]
        if name not in session.resources:
            raise PlanError("资料资源不存在，先查询取得query-N", "agent_resource_missing")
        from .native_tool_receipt import DIRECT_RESULT_MAX_BYTES
        return page(source_notebook(session.resources[name], reference=name), args["resource"],
                    args.get("pointer", ""), args.get("offset", 0), args.get("limit", 50),
                    byte_budget=DIRECT_RESULT_MAX_BYTES)
    from . import native_tool_contracts
    from .native_tool_receipt import DIRECT_RESULT_MAX_BYTES
    bounded_page = partial(page, byte_budget=DIRECT_RESULT_MAX_BYTES)
    location = (args.get("pointer", ""), args.get("offset", 0), args.get("limit", 50))
    name = args["resource"]
    if name.startswith("attempt-"):
        from .operation_attempts import read_attempt_resource
        return bounded_page(read_attempt_resource(session, name), name, *location)
    if name in native_tool_contracts.RESOURCE_NAMES:
        producer = getattr(native_tool_contracts, name)
        value = producer() if name == "runtime_contracts" else producer(session)
        return bounded_page(value, name, *location)
    if session.workflow == "artifact" and name in _CONTEXT_BOARD_RESOURCES:
        if session.kind != "whiteboard":
            raise PlanError("当前文档不是白板", "agent_resource_missing")
        board = session.document if name == "original_board" else session._current_board()
        return bounded_page(board, name, *location)
    if name.startswith("review-"):
        if session.workflow == "staged" and session.workflow_stage != "calculation_closure":
            raise PlanError("计算审查投影只在计算闭环阶段披露", "agent_stage_resource_unavailable")
        from .review_projection import read_page
        return read_page(session, name, args, bounded_page)
    if name in _CONTEXT_BOARD_RESOURCES:
        if session.kind != "whiteboard":
            raise PlanError("当前文档不是白板", "agent_resource_missing")
        if name == "original_board" and session.workflow != "staged":
            raise PlanError("当前工作流没有原始白板投影", "agent_resource_missing")
        board = (_staged_board_view(session, original=name == "original_board")
                 if session.workflow == "staged" else
                 session.candidate if session.workflow == "build" else session._candidate_board())
        return bounded_page(board, name, *location)
    if session.workflow == "staged":
        phase = session.workflow_stage
        phase_resources = _stage_context_resources(session)
        resource_phase = session.stage_state["resource_stages"].get(name)
        # Database query resources are read-only evidence.  They remain
        # readable after a phase rollback instead of becoming a hidden cache
        # that the model has to query again.
        cached_allowed = name in session.resources and resource_phase is not None
        if not (name == "task_evidence" or name in phase_resources or cached_allowed or name.startswith("review-")):
            raise PlanError("该资料尚未在当前阶段披露", "agent_stage_resource_unavailable",
                            {"workflow_stage": phase, "resource": name})
    from .native_static_capability_views import independent_capability_document
    value = independent_capability_document(name, _tokens(args.get("pointer", "")),
        representation=_capability_family(session))
    if value is None:
        values = _fixed_context_getters(session)
        value = values[name]() if name in values else session.resources.get(name)
    if value is None:
        raise PlanError("资料不存在；可读取candidate/frozen/capabilities/mechanics_capabilities/plan_schema/report/findings或查询资源ID", "agent_resource_missing")
    if name in {"capabilities", "whiteboard_capabilities"}:
        value = {**value, "callable_surface": "gui_only",
                 "native_contracts": {"resource": "tool_contracts", "pointer": "/tools"},
                 "runtime_actions": {"resource": "runtime_contracts", "pointer": "/actions"},
                 "surface_note": "本文的GUI/HTTP方法名不是可调用的gs工具或runtime action；原生接口以实际下发schema为准。"}
    return bounded_page(value, name, *location)


_LOADOUT_SKILL_RESOURCES = {
    "references/workflow/focus/loadout.md", "references/workflow/context-use.md",
    "references/workflow/whiteboard.md", "references/data/query.md",
    "references/app-mechanics/loadouts.md", "references/mechanics/artifacts.md",
}

_ACTION_SKILL_RESOURCES = {
    "references/workflow/focus/actions.md", "references/workflow/context-use.md",
    "references/app-mechanics/actions/cast-sequences.md", "references/workflow/coverage-and-review.md",
    "references/app-mechanics/actions/rotation-energy.md", "references/data/character-appendices.md",
    "references/data/database-readme.md",
}

_EVENT_SKILL_RESOURCES = {
    "references/workflow/focus/events.md", "references/workflow/context-use.md",
    "references/app-mechanics/actions/event-profiles.md",
    "references/data/character-appendices.md", "references/app-mechanics/elements/runtime.md",
    "references/app-mechanics/execution/materialization.md", "references/app-mechanics/actions/periodic.md",
    "references/data/database-readme.md",
}

_NUMERIC_SKILL_RESOURCES = {
    "references/workflow/focus/numbers.md", "references/workflow/index.md",
    "references/app-mechanics/resolved-template.md", "references/app-mechanics/execution/rules.md",
    "references/app-mechanics/loadouts.md",
    "references/app-mechanics/actions/rotation-energy.md", "references/mechanics/damage-formula.md",
    "references/mechanics/reactions.md", "references/app-mechanics/elements/runtime.md",
}


_STAGE_RECOMMENDED_SKILL_RESOURCES = {
    "loadout_draft": {
        *_LOADOUT_SKILL_RESOURCES,
    },
    "action_checkpoint": {*_ACTION_SKILL_RESOURCES},
    "event_topology": {*_EVENT_SKILL_RESOURCES},
    "numeric_model": _NUMERIC_SKILL_RESOURCES,
    "calculation_closure": {
        "references/workflow/focus/closure.md",
        "references/workflow/context-use.md",
        "references/app-mechanics/review/corrections.md",
        "references/outputs/app-and-excel.md",
        "references/outputs/excel.md",
        "references/runtime/software-entrypoints.md",
    },
}

_STAGE_READ_CHAINS = {
    "loadout_draft": {
        "required": ["workflow_stage", "capabilities", "whiteboard_schema",
                     "candidate_board"],
        "recommended": ["context_capabilities", "database_snapshot", "findings"],
    },
    "action_checkpoint": {
        "required": ["workflow_stage", "capabilities", "whiteboard_schema",
                     "candidate_board"],
        "recommended": ["context_capabilities", "runtime_capabilities", "findings"],
    },
    "event_topology": {
        "required": ["workflow_stage", "event_topology_capabilities",
                     "candidate_board", "event_topology",
                     "event_topology_receipt"],
        "recommended": ["context_capabilities", "runtime_capabilities",
                        "findings"],
    },
    "numeric_model": {
        "required": ["workflow_stage", "plan_schema", "event_topology",
                     "event_topology_receipt", "event_topology_binding_capabilities",
                     "agent_execution_materialization_capabilities"],
        "recommended": ["candidate_board", "candidate", "mechanics_capabilities",
                        "context_capabilities", "runtime_capabilities",
                        "event_topology_bindings", "event_topology_binding_receipt",
                        "agent_execution_materialization",
                        "agent_execution_materialization_receipt"],
    },
    "calculation_closure": {
        "required": ["workflow_stage", "event_topology_receipt",
                     "event_topology_binding_receipt",
                     "agent_execution_materialization_receipt", "report"],
        "recommended": ["candidate", "calculated", "diagnostic_error",
                        "diagnostics_capabilities",
                        "calculation_review_batch_capabilities"],
    },
}


def staged_read_chain(session):
    """Return an ordered, current-stage-only context reading plan."""
    phase = session.workflow_stage
    allowed = _stage_context_resources(session)
    chain = _STAGE_READ_CHAINS.get(phase, {"required": [], "recommended": []})
    return {
        "contract": "staged-context-read-chain-v1",
        "workflow_stage": phase,
        "required": [name for name in chain["required"] if name in allowed],
        "recommended": [name for name in chain["recommended"] if name in allowed],
    }


def staged_disclosure(session):
    """Return only names and counts needed to resume the trusted current phase."""
    if session.workflow != "staged":
        return None
    phase = session.workflow_stage
    descriptors = session.stage_state.setdefault("resource_descriptors", {})
    visible_names = sorted(session.stage_state["resource_stages"],
                           key=lambda name: ((0, int(name[6:])) if name.startswith("query-")
                                             and name[6:].isdigit() else (1, name)))
    visible_cached = [
        copy.deepcopy(descriptors[name])
        for name in visible_names
        for resource_phase in (session.stage_state["resource_stages"][name],)
        if resource_phase in PHASE_INDEX
        and name in session.resources and name in descriptors
    ]
    manifest_limit = 16
    manifest = visible_cached[-manifest_limit:]
    read_chain = staged_read_chain(session)
    result = {
        "allowed_tools": sorted(_allowed_session(session)),
        "recommended_tools": recommended_tools_for_session(session),
        "allowed_context_resources": sorted(_stage_context_resources(session)),
        "skill_access": skill_access_descriptor(),
        "recommended_skill_resources": sorted(
            _STAGE_RECOMMENDED_SKILL_RESOURCES.get(phase, set())
        ),
        "current_stage_skill": _CURRENT_STAGE_SKILL.get(phase),
        "required_context_reads": read_chain["required"],
        "recommended_context_reads": read_chain["recommended"],
        "visible_cached_resource_manifest": manifest,
        "visible_cached_resource_total": len(visible_cached),
        "visible_cached_resource_manifest_truncated": len(visible_cached) > len(manifest),
    }
    if phase == "event_topology":
        # ``event_topology`` is exposed as a bounded subtree resource, while
        # candidate mutation uses full-document JSON Pointers.  Publish that
        # coordinate-system boundary explicitly so an Agent never has to
        # infer private storage layout from a rejected write.
        result["write_contract"] = {
            "tool": "gs_patch_candidate",
            "candidate_root_pointer": "/extensions/event_topology",
            "read_resource": "event_topology",
            "read_pointer_scope": "subtree_relative",
            "replace_whole_document_supported": True,
        }
    return result


def _skill(session, args):
    del session
    resources = _skill_resource_paths()
    name = args.get("resource")
    if name is None:
        return {"resources": sorted(resources)}
    if name not in resources:
        raise PlanError("只能读取列出的捆绑Skill资源", "agent_resource_missing")
    path = resources[name]
    value = sanitize_public_text(path.read_text(encoding="utf-8"))
    offset, maximum = args.get("offset", 0), args.get("max_chars", len(value) or 1)
    if type(offset) is not int or offset < 0 or type(maximum) is not int or maximum < 1:
        raise PlanError("Skill分页参数必须为有效非负偏移和正整数长度", "agent_tool_input")
    from .native_tool_receipt import DIRECT_RESULT_MAX_BYTES
    byte_budget = min(MAX_RESPONSE, DIRECT_RESULT_MAX_BYTES)
    digest = hashlib.sha256(value.encode("utf-8")).hexdigest()
    count = min(maximum, max(0, len(value) - offset))
    def result(length, reason):
        end = offset + length
        return {"resource": name, "offset": offset, "text": value[offset:end],
                "delivered_chars": length, "total": len(value),
                "next_offset": end if end < len(value) else None, "content_hash": digest,
                "truncation_reason": reason if end < len(value) else None}
    chosen = result(count, "max_chars")
    # Adapt to the actual serialized transport, including JSON escaping and
    # metadata. No writing/file-size quota, arbitrary character cap or DSH
    # pruning threshold is used as a document-read limit.
    if len(json_bytes({"code": 0, "data": chosen})) > byte_budget:
        low, high = 0, count
        while low < high:
            mid = (low + high + 1) // 2
            if len(json_bytes({"code": 0, "data": result(mid, "response_bytes")})) <= byte_budget:
                low = mid
            else:
                high = mid - 1
        if low == 0 and count:
            raise PlanError("Skill响应封套无法容纳正文", "agent_response_limit")
        chosen = result(low, "response_bytes")
    return chosen


_EVENT_RUNTIME_ACTIONS = frozenset({
    "action_admission_profile", "action_admission_model_candidates",
    "action_capacity_profile", "action_capacity_model_candidates",
    "action_activation_catalog", "action_capacity_source_profile",
    "mechanism_catalog", "mechanism_profile", "action_facts",
    "action_resource_profile", "action_dispatch_profile", "attack_metadata_profile",
    "passive_source_profile", "gadget_source_profile", "energy_generation_profile",
    "limited_hit_benefit_profile", "periodic_attack_profile", "attenuation_profile",
})


def _strip_query_fields(value, forbidden):
    if isinstance(value, dict):
        return {key: _strip_query_fields(item, forbidden) for key, item in value.items()
                if key not in forbidden}
    if isinstance(value, list):
        return [_strip_query_fields(item, forbidden) for item in value]
    return value


_ACTION_CHARACTER_FIELDS = frozenset({
    "id", "version_line", "name", "rarity", "weapon_type", "element",
    "skill_depot_id", "promote_id",
})
_ACTION_SKILL_FIELDS = frozenset({
    "id", "version_line", "character_id", "slot", "name", "effect_type",
    "proud_skill_group_id", "desc_status", "desc_source", "source_id",
    "target_type", "target_id", "info_type",
})
_EVENT_QUERY_ALLOWED_FIELDS = frozenset({
    # Response envelope and stable provenance/identity.
    "contract_id", "schema_version", "profile_version", "version_line", "status",
    "entity_type", "id", "limits", "snapshot", "snapshot_id", "upstream_commit",
    "state", "items", "data", "request", "error", "code", "reason", "message",
    "content_fingerprint", "source_fingerprint", "reference", "reference_id",
    "origin_reference_id", "source_ref", "source_refs",
    # Topology-bearing containers. Unknown containers fail closed.
    "profile", "profiles", "selection", "selector", "candidates", "issues",
    "character", "skills", "talents", "extra_info", "annotations", "weapon",
    "effects", "artifact_set", "emissions", "components", "schedule", "lifecycle",
    "application", "trigger", "triggers", "source", "sources", "attack_source",
    "relations", "dependencies", "refresh", "replacement", "model_ref",
    # Stable identifiers and non-numeric topology labels.
    "profile_id", "component_id", "candidate_id", "source_id", "dependency_id",
    "mechanism_id", "action_id", "ability_id", "character_id", "skill_id",
    "skill_depot_id", "target_id", "owner_ref", "node_id", "edge_id", "ref_id",
    "kind", "type", "category", "slot", "name", "label", "effect_type",
    "target_type", "info_type", "variant", "mode", "phase", "channel", "group",
    "counter_tag", "element", "aura", "reaction", "replacement_policy",
    "refresh_policy", "persistence", "disposition", "required", "active",
    "continues_off_field", "description", "desc", "desc_template", "content",
    "note", "notes",
    # Scheduling is part of event shape. These exact names are intentionally
    # allowed; combat values such as damage, cost, cooldown, GU, ICD and
    # hit_count are absent from this allowlist.
    "time", "start", "end", "order", "count", "duration_seconds",
    "start_seconds", "end_seconds", "offset_seconds", "first_offset_seconds",
    "interval_seconds", "establishment_offset_seconds", "instance_end_offset_seconds",
    "expiry_offset_seconds", "refresh_offset_seconds", "tick_offsets_seconds",
})
_EVENT_QUERY_REDACT_TEXT_FIELDS = frozenset({
    "description", "desc", "desc_template", "content", "note", "notes",
})
_NUMERIC_TEXT = re.compile(r"[-+]?(?:\d+(?:\.\d+)?|\.\d+)(?:[%％])?")


def _selected(value, fields):
    if not isinstance(value, dict):
        return value
    return {key: copy.deepcopy(item) for key, item in value.items() if key in fields}


def _query_envelope(value):
    """Retain source identity without carrying a raw future-stage snapshot body."""
    if not isinstance(value, dict):
        return value
    projected = {key: copy.deepcopy(value[key]) for key in (
        "contract_id", "schema_version", "profile_version", "version_line",
        "status", "entity_type", "id", "limits",
    ) if key in value}
    snapshot = value.get("snapshot")
    if isinstance(snapshot, dict):
        projected["snapshot"] = {key: copy.deepcopy(snapshot[key]) for key in (
            "version_line", "snapshot_id", "upstream_commit", "state",
        ) if key in snapshot}
    return projected


def _action_context_projection(value):
    """Expose action identities, never descriptions, params or combat numbers."""
    if not isinstance(value, dict):
        return value
    projected = _query_envelope(value)
    rows = []
    for row in value.get("items", []):
        if not isinstance(row, dict):
            continue
        item = {key: copy.deepcopy(row[key]) for key in (
            "entity_type", "id", "status", "request",
        ) if key in row}
        if isinstance(row.get("error"), dict):
            item["error"] = {key: copy.deepcopy(row["error"][key]) for key in (
                "code", "reason", "message",
            ) if key in row["error"]}
        data = row.get("data")
        if isinstance(data, dict):
            detail = {}
            if isinstance(data.get("character"), dict):
                detail["character"] = _selected(data["character"], _ACTION_CHARACTER_FIELDS)
            for section in ("skills", "extra_info"):
                if isinstance(data.get(section), list):
                    detail[section] = [_selected(entry, _ACTION_SKILL_FIELDS)
                                       for entry in data[section] if isinstance(entry, dict)]
            selection = data.get("selection")
            if isinstance(selection, dict):
                detail["selection"] = {
                    "requested": copy.deepcopy(selection.get("requested")),
                    "not_requested": copy.deepcopy(selection.get("not_requested", [])),
                }
            item["data"] = detail
        rows.append(item)
    projected["items"] = rows
    projected["stage_projection"] = {
        "phase": "action_checkpoint",
        "scope": "action_identity_and_variant_skeleton",
        "omitted": ["descriptions", "parameters", "emissions", "prototype", "lifecycle"],
    }
    return projected


def _action_runtime_projection(value):
    """Expose identity plus independently reviewed timing, never combat semantics."""
    if not isinstance(value, dict):
        return value
    projected = _query_envelope(value)
    for key in ("selector", "matched_profile_ids", "coverage_status"):
        if key in value:
            projected[key] = copy.deepcopy(value[key])
    profile = value.get("profile")
    if isinstance(profile, dict):
        projected["profile"] = {key: copy.deepcopy(profile[key]) for key in (
            "profile_id", "status", "selector",
        ) if key in profile}
        review = profile.get("review") if isinstance(profile.get("review"), dict) else {}
        if profile.get("status") == "confirmed" and review.get("status") == "reviewed":
            projected["profile"]["review"] = {key: copy.deepcopy(review[key]) for key in (
                "status", "date", "reviewer",
            ) if key in review}
            timing = []
            for emission in profile.get("emissions", []):
                if not isinstance(emission, dict) or not isinstance(emission.get("schedule"), dict):
                    continue
                evidence = emission.get("evidence")
                timing_evidence = (evidence.get("timing") if isinstance(evidence, dict) else None)
                if (not isinstance(timing_evidence, dict)
                        or timing_evidence.get("status") != "confirmed"):
                    continue
                schedule = emission["schedule"]
                timing.append({
                    "component_id": emission.get("component_id"),
                    "kind": emission.get("kind"),
                    "schedule": {key: copy.deepcopy(schedule[key]) for key in (
                        "offset_seconds", "first_offset_seconds", "interval_seconds",
                        "precision_seconds",
                    ) if key in schedule},
                })
            lifecycle = profile.get("lifecycle")
            lifecycle_evidence = (lifecycle.get("evidence")
                                  if isinstance(lifecycle, dict) else None)
            lifecycle_timing = ({key: copy.deepcopy(lifecycle[key]) for key in (
                "establishment_offset_seconds", "instance_end_offset_seconds",
                "precision_seconds",
            ) if key in lifecycle} if isinstance(lifecycle_evidence, dict)
                and lifecycle_evidence.get("status") == "confirmed" else {})
            if timing or lifecycle_timing:
                projected["profile"]["reviewed_timing"] = {
                    "status": "reviewed",
                    "emissions": timing,
                    "lifecycle_timing": lifecycle_timing,
                }
            else:
                projected["profile"]["reviewed_timing"] = {
                    "status": "pending",
                    "reason": "confirmed_timing_evidence_unavailable",
                    "next_source": "references/data/character-appendices.md",
                }
        else:
            projected["profile"]["reviewed_timing"] = {
                "status": "pending",
                "reason": "reviewed_dispatch_timing_unavailable",
                "next_source": "references/data/character-appendices.md",
            }
    elif "profile" in value:
        projected["profile"] = None
    issues = value.get("issues")
    if isinstance(issues, list):
        projected["issues"] = [
            {key: (_NUMERIC_TEXT.sub("<数值留待数值阶段>", row[key])
                   if key == "message" and isinstance(row[key], str)
                   else copy.deepcopy(row[key]))
             for key in ("code", "message", "path") if key in row}
            for row in issues if isinstance(row, dict)
        ]
    projected["stage_projection"] = {
        "phase": "action_checkpoint",
        "scope": "action_identity_and_reviewed_timing",
        "omitted": ["multipliers", "application", "periodic_counts", "full_lifecycle",
                    "prototype", "numeric_combat_values"],
    }
    return projected


def _event_shape_projection(value, *, field=None):
    """Project an explicit topology allowlist; unknown future fields fail closed."""
    if isinstance(value, dict):
        return {
            key: _event_shape_projection(item, field=key.lower())
            for key, item in value.items()
            if key.lower() in _EVENT_QUERY_ALLOWED_FIELDS
        }
    if isinstance(value, list):
        return [_event_shape_projection(item, field=field) for item in value]
    if isinstance(value, str) and field in _EVENT_QUERY_REDACT_TEXT_FIELDS:
        return _NUMERIC_TEXT.sub("<数值留待数值阶段>", value)
    return copy.deepcopy(value)


def _event_query_projection(value):
    projected = _event_shape_projection(value)
    if isinstance(projected, dict):
        projected["stage_projection"] = {
            "phase": "event_topology",
            "scope": "event_schedule_relation_and_source_shape",
            "omitted": ["multipliers", "parameters", "execution_fragments", "prototype"],
        }
    return projected


def _project_staged_query(session, query_kind, value):
    del query_kind
    # Stage disclosure controls the recommended order of reading, never the
    # contents of a read-only database result.  A single stage-independent
    # scrub still removes explicitly private/internal fields should an
    # upstream adapter accidentally return them outside its closed schema.
    if getattr(session, "workflow", None) not in {"staged", "artifact"}:
        return value

    def public(item, parent=None):
        if isinstance(item, list):
            return [public(row, parent) for row in item]
        if not isinstance(item, dict):
            return item
        projected = {}
        for key, child in item.items():
            normalized = str(key).casefold()
            if normalized.startswith(("secret", "private_")) or normalized in {
                "api_key", "access_token", "authorization", "password",
            }:
                continue
            if parent == "review" and normalized in {
                "notes", "comment", "internal_notes",
            }:
                continue
            projected[key] = public(child, normalized)
        return projected

    return public(value)


def _validate_staged_query(session, query_kind, request):
    del session, query_kind, request
    # Each query implementation validates its own closed request schema.
    # Stages govern candidate writes and validation gates, not read-only
    # knowledge acquisition.


def _descriptor_value(value):
    if value is None or isinstance(value, (str, int, float, bool)):
        text = str(value).lower() if isinstance(value, bool) else str(value)
        return text[:128]
    if isinstance(value, list) and len(value) <= 16 and all(
            item is None or isinstance(item, (str, int, float, bool)) for item in value):
        return ",".join(_descriptor_value(item) for item in value)[:128]
    return None


def _query_descriptor(resource, stage, query_kind, request, value):
    """Persist bounded selection metadata, never a copy of the query body."""
    action = request.get("action") if query_kind == "runtime" else None
    action = action[:128] if isinstance(action, str) else None
    entities, selectors = [], []
    if query_kind == "appendix_index":
        entities = [f"character:{request['db_character_id']}"]
        selectors = [f"version_line={request['version_line']}"]
    elif query_kind == "appendix":
        entities = [f"{request['target_type']}:{request['target_id']}"[:128]]
        selectors = [f"{key}={_descriptor_value(request[key])}"[:128] for key in (
            "status", "category", "version_line", "page", "page_size")]
    elif query_kind == "context":
        names = {"characters": "character", "weapons": "weapon",
                 "artifact_sets": "artifact_set"}
        for collection, kind in names.items():
            for row in request.get(collection, [])[:16]:
                if not isinstance(row, dict):
                    continue
                entity = _descriptor_value(row.get("id"))
                if entity is None:
                    continue
                identity = f"{kind}:{entity}"[:128]
                entities.append(identity)
                for key in ("sections", "levels", "refinement", "set_thresholds"):
                    selected = _descriptor_value(row.get(key)) if key in row else None
                    if selected is not None:
                        selectors.append(f"{identity}.{key}={selected}"[:128])
    else:
        entity_fields = ("character_id", "skill_depot_id", "skill_id", "weapon_id",
                         "artifact_set_id", "affix_id", "gadget_id", "origin_reference_id",
                         "mechanism_id")
        for key in entity_fields:
            selected = _descriptor_value(request.get(key)) if key in request else None
            if selected is not None:
                entities.append(f"{key}={selected}"[:128])
        for key in sorted(request):
            if key in {"action", "version_line", "snapshot_id", *entity_fields}:
                continue
            selected = _descriptor_value(request[key])
            if selected is not None:
                selectors.append(f"{key}={selected}"[:128])
    return {
        "id": resource,
        "stage": stage,
        "query_kind": query_kind,
        "action": action,
        "entities": entities[:16],
        "selectors": selectors[:32],
        "fingerprint": fingerprint(value),
        "bytes": len(json_bytes(value)),
    }


def _query(session, args, function, query_kind=None):
    # Preserve the original internal helper signature used by focused runtime
    # tests and small embedders that do not implement a staged session.
    if query_kind is None:
        query_kind = "runtime" if function is query_runtime else "context"
    workflow = getattr(session, "workflow", None)

    def source_reference(name, value):
        reference = {"kind": "database_appendix" if query_kind == "appendix" else "database",
                     "reference": name, "fingerprint": fingerprint(value), "status": "verified",
                     "integrity_status": "verified", "execution_authority": "none"}
        if query_kind == "appendix":
            reference["knowledge_review_status"] = args["request"].get("status") or "mixed_or_unspecified"
        return reference
    _validate_staged_query(session, query_kind, args["request"])
    with session._read_db():
        value = function(session.db_path, args["request"])
        if query_kind == "context" and args.get("related_sources"):
            from genshin_app.public_data_query import related_sources
            value = related_sources(session.db_path, value)
    value = _project_staged_query(session, query_kind, value)
    encoded_value = json_bytes(value)
    if len(encoded_value) > MAX_RESOURCE:
        raise PlanError("本次查询过大，请缩小资料范围", "agent_size_limit")
    for resource, existing in session.resources.items():
        if json_bytes(existing) == encoded_value:
            response = page(existing, resource)
            if workflow in {"staged", "artifact"}:
                descriptors = session.stage_state.setdefault("resource_descriptors", {})
                descriptor = _query_descriptor(resource, session.workflow_stage, query_kind, args["request"], value)
                if descriptors.get(resource) != descriptor:
                    previous = copy.deepcopy(session.stage_state)
                    descriptors[resource] = descriptor
                    session.stage_state["resource_stages"][resource] = session.workflow_stage
                    try:
                        session.save()
                    except Exception:
                        session.stage_state = previous
                        raise
                response["source_ref"] = source_reference(resource, existing)
            response["notebook_ref"] = {"resource": "source-notebook:" + resource}
            return response
    if len(session.resources) >= MAX_RESOURCES:
        raise PlanError("本会话查询资料已达128份，请复用已保存资料或开始新会话", "agent_resource_limit")
    if sum(len(json_bytes(item)) for item in session.resources.values()) + len(json_bytes(value)) > MAX_RESOURCE_TOTAL:
        raise PlanError("本会话资料超过64MiB，请复用已有资料", "agent_resource_limit")
    index = 1
    while "query-" + str(index) in session.resources:
        index += 1
    name = "query-" + str(index)
    write_owned(session.root, name + ".json", value, MAX_RESOURCE)
    session.resources[name] = value
    if workflow in {"staged", "artifact"}:
        session.stage_state["resource_stages"][name] = session.workflow_stage
        session.stage_state.setdefault("resource_descriptors", {})[name] = _query_descriptor(
            name, session.workflow_stage, query_kind, args["request"], value)
    try:
        session.save()
    except Exception:
        del session.resources[name]
        if workflow in {"staged", "artifact"}:
            session.stage_state["resource_stages"].pop(name, None)
            session.stage_state.setdefault("resource_descriptors", {}).pop(name, None)
        raise
    response = page(value, name)
    if workflow in {"staged", "artifact"}:
        # The fingerprint covers the complete saved resource, not merely this
        # first page.  It lets event-topology provenance be issued by the App
        # instead of asking the model to invent or calculate a digest.
        response["source_ref"] = source_reference(name, value)
    response["notebook_ref"] = {"resource": "source-notebook:" + name}
    return response


def _starts_with(parts, prefix):
    return len(parts) >= len(prefix) and tuple(parts[:len(prefix)]) == tuple(prefix)


def _event_timing_pair(session, operations):
    """Require one atomic board/native start edit for each event-stage time move."""
    board_prefix = ("extensions", "whiteboard_handoff", "board", "actions")
    native_prefix = ("rotation_model", "actions")
    timing = {}
    for operation in operations:
        parts = _tokens(operation["path"])
        if (_starts_with(parts, board_prefix) and len(parts) == 6 and parts[4].isdigit()
                and parts[5] == "start"):
            key = ("board", int(parts[4]))
        elif (_starts_with(parts, native_prefix) and len(parts) == 4 and parts[2].isdigit()
              and parts[3] == "start"):
            key = ("native", int(parts[2]))
        else:
            continue
        if key in timing or operation["op"] != "replace" or "value" not in operation:
            raise PlanError("事件拓扑阶段的动作时点必须用单次replace成对同步",
                            "agent_stage_patch_forbidden",
                            {"workflow_stage": session.workflow_stage,
                             "path": operation["path"]})
        timing[key] = operation["value"]
    if not timing:
        return

    handoff = session.candidate["extensions"]["whiteboard_handoff"]
    board_actions = handoff["board"]["actions"]
    native_actions = session.candidate["rotation_model"]["actions"]
    native_index = {row["id"]: index for index, row in enumerate(native_actions)}
    mappings = handoff["action_map"]
    for side, index in tuple(timing):
        if side != "board":
            continue
        if index >= len(board_actions) or index >= len(mappings):
            raise PlanError("事件拓扑阶段的白板动作映射不存在",
                            "agent_stage_patch_forbidden", {"board_action_index": index})
        mapping = mappings[index]
        if mapping.get("board_action_id") != board_actions[index].get("id"):
            raise PlanError("事件拓扑阶段的白板动作映射已失配",
                            "agent_stage_patch_forbidden", {"board_action_index": index})
        mapped_index = native_index.get(mapping.get("native_action_id"))
        if mapped_index is None:
            raise PlanError("事件拓扑阶段的原生动作映射不存在",
                            "agent_stage_patch_forbidden", {"board_action_index": index})
        native_key = ("native", mapped_index)
        if native_key not in timing or timing[native_key] != timing[("board", index)]:
            raise PlanError("动作时点必须在原生动作与whiteboard_handoff.board中同批同步",
                            "agent_stage_patch_forbidden",
                            {"board_action_index": index, "native_action_index": mapped_index})
    for side, index in tuple(timing):
        if side != "native":
            continue
        mapped_id = native_actions[index]["id"] if index < len(native_actions) else None
        board_index = next((position for position, row in enumerate(mappings)
                            if row.get("native_action_id") == mapped_id), None)
        if board_index is None or ("board", board_index) not in timing:
            raise PlanError("动作时点必须在原生动作与whiteboard_handoff.board中同批同步",
                            "agent_stage_patch_forbidden",
                            {"native_action_index": index})


_NUMERIC_ROOTS = frozenset({
    # Strongly typed executable inputs.  These roots may be completed in the
    # numeric phase, but loadout identity, the action skeleton and topology
    # structure remain owned by their earlier phases.
    "effect_bindings", "rotation_tuning", "manual_effect_dependencies",
    "mechanics_model", "attribute_review", "elemental_sources",
    "reaction_damage_sources", "hit_parameter_sources", "action_resource_sources",
    "action_dispatch_sources", "action_admission_sources", "action_admission_context",
    "action_capacity_sources", "action_capacity_context", "attack_metadata_sources",
    "periodic_attack_sources", "periodic_refresh_triggers", "passive_sources",
    "energy_generation_sources", "limited_hit_benefit_sources", "mechanism_sources",
    "external_damage_supplements", "action_event_profiles", "action_event_bindings",
    "action_event_overrides", "gadget_sources", "reaction_benefit_sources",
    "snapshot_capture_sources", "target_condition_sources",
    # D2 proposals and their App-issued receipts are native-only inputs.  The
    # loadout representation decision remains fail-closed; after handoff the
    # Agent can author and verify the generic registry in event/numeric work.
    "provisional_entities",
    # Unified reviewed overrides are authored only after topology exists and
    # numeric bindings are being completed.  The closed schema and Composer
    # still decide whether a concrete declaration is valid/executable.
    "calculation_event_overrides",
})
_NUMERIC_ROTATION_CHARACTER_FIELDS = frozenset({
    "element", "energy_recharge", "energy_capacity", "initial_energy", "status", "source",
})
_NUMERIC_ROTATION_ABILITY_FIELDS = frozenset({
    "cooldown", "energy", "rules", "status", "source",
})
# Event expansion may reveal a more accurate occupation end or switch point.
# Numeric/closure stages may fill these timing leaves and the initial
# frontline required by the generic simulator, but action identity, order and
# start time remain owned by the action/event stages.
_NUMERIC_ROTATION_ROOT_FIELDS = frozenset({"initial_frontline"})
_NUMERIC_ROTATION_ACTION_FIELDS = frozenset({
    "occupation_end", "switch_allowed_at", "status", "source",
})
_ABSENT = object()


def _operation_value(operation):
    return _ABSENT if operation["op"] == "remove" else operation.get("value", _ABSENT)


def _numeric_panel_paths(session, operations):
    """Return synchronized board/template/rotation panel leaf paths.

    Final ER and panel allocation belongs to the numeric model, but the native
    plan must never silently diverge from the retained user-facing board.  A
    panel edit is therefore one atomic three-view edit (two views for stats
    other than ER).  This is a write-integrity rule, not a gameplay inference.
    """
    board = session._current_board()
    native_characters = session.candidate.get("manual_template", {}).get("characters", [])
    rotation_characters = session.candidate.get("rotation_model", {}).get("characters", [])
    native_by_id = {row.get("id"): index for index, row in enumerate(native_characters)
                    if isinstance(row, dict)}
    rotation_by_id = {row.get("id"): index for index, row in enumerate(rotation_characters)
                      if isinstance(row, dict)}
    groups = {}

    def register(identity, view, path, value):
        key = tuple(identity)
        bucket = groups.setdefault(key, {})
        if view in bucket:
            raise PlanError("数值阶段的面板字段不能在同批重复修改",
                            "agent_stage_patch_forbidden", {"path": path})
        bucket[view] = (path, value)

    panel_paths = set()
    for operation in operations:
        parts = _tokens(operation["path"])
        value = _operation_value(operation)
        if (_starts_with(parts, ("extensions", "whiteboard_handoff", "board", "team"))
                and len(parts) in (7, 8) and parts[4].isdigit() and parts[5] == "panel"):
            board_index = int(parts[4])
            if board_index >= len(board["team"]):
                raise PlanError("数值阶段的白板面板角色不存在", "agent_stage_patch_forbidden",
                                {"path": operation["path"]})
            actor_id = board["team"][board_index]["id"]
            suffix = parts[6:]
            if len(suffix) == 2 and suffix[0] != "dmg_bonus":
                raise PlanError("数值阶段只能修改面板数值叶子", "agent_stage_patch_forbidden",
                                {"path": operation["path"]})
            register((actor_id, *suffix), "board", operation["path"], value)
            panel_paths.add(operation["path"])
        elif (_starts_with(parts, ("manual_template", "characters"))
              and len(parts) in (5, 6) and parts[2].isdigit() and parts[3] == "stats"):
            index = int(parts[2])
            if index >= len(native_characters):
                raise PlanError("数值阶段的原生面板角色不存在", "agent_stage_patch_forbidden",
                                {"path": operation["path"]})
            suffix = parts[4:]
            if len(suffix) == 2 and suffix[0] != "dmg_bonus":
                raise PlanError("数值阶段只能修改面板数值叶子", "agent_stage_patch_forbidden",
                                {"path": operation["path"]})
            register((native_characters[index]["id"], *suffix), "native",
                     operation["path"], value)
            panel_paths.add(operation["path"])
        elif (_starts_with(parts, ("rotation_model", "characters")) and len(parts) == 4
              and parts[2].isdigit() and parts[3] == "energy_recharge"):
            index = int(parts[2])
            if index >= len(rotation_characters):
                raise PlanError("数值阶段的能量角色不存在", "agent_stage_patch_forbidden",
                                {"path": operation["path"]})
            register((rotation_characters[index]["id"], "energy_recharge"), "rotation",
                     operation["path"], value)
            panel_paths.add(operation["path"])

    for (actor_id, *suffix), views in groups.items():
        required = {"board", "native"} | ({"rotation"} if suffix == ["energy_recharge"] else set())
        if set(views) != required:
            raise PlanError("面板数值必须在白板与原生输入中同批同步",
                            "agent_stage_patch_forbidden", {
                                "actor_id": actor_id, "field": ".".join(suffix),
                                "required_views": sorted(required), "provided_views": sorted(views),
                            })
        values = [item[1] for item in views.values()]
        # A board null paired with native removal represents one shared unknown.
        normalized = [None if value is _ABSENT else value for value in values]
        if any(value != normalized[0] for value in normalized[1:]):
            raise PlanError("白板与原生面板的同步值必须一致", "agent_stage_patch_forbidden", {
                "actor_id": actor_id, "field": ".".join(suffix),
            })
        if actor_id not in native_by_id or actor_id not in rotation_by_id:
            raise PlanError("面板角色没有完整原生映射", "agent_stage_patch_forbidden",
                            {"actor_id": actor_id})
    return panel_paths


def _numeric_patch_allowed(parts, panel_paths, raw_path):
    if raw_path in panel_paths:
        return True
    if not parts:
        return False
    if parts[0] in _NUMERIC_ROOTS:
        return True
    if parts[0] == "manual_template":
        return (_starts_with(parts, ("manual_template", "hits"))
                or _starts_with(parts, ("manual_template", "buffs"))
                or _starts_with(parts, ("manual_template", "enemy"))
                or _starts_with(parts, ("manual_template", "type_definitions"))
                or _starts_with(parts, ("manual_template", "extensions"))
                or _starts_with(parts, ("manual_template", "meta", "assumptions")))
    if parts[0] == "rotation_model":
        if len(parts) == 2 and parts[1] in _NUMERIC_ROTATION_ROOT_FIELDS:
            return True
        if len(parts) >= 2 and parts[1] in {
                "events", "initial_cooldowns", "action_hit_links", "max_cycles"}:
            return True
        if len(parts) == 4 and parts[1] == "actions" and parts[2].isdigit():
            return parts[3] in _NUMERIC_ROTATION_ACTION_FIELDS
        if len(parts) >= 4 and parts[1] == "characters" and parts[2].isdigit():
            return parts[3] in _NUMERIC_ROTATION_CHARACTER_FIELDS
        if len(parts) >= 4 and parts[1] == "abilities" and parts[2].isdigit():
            return parts[3] in _NUMERIC_ROTATION_ABILITY_FIELDS
        return False
    if parts[0] == "extensions":
        if _starts_with(parts, ("extensions", "event_topology_bindings")):
            return True
        if _starts_with(parts, ("extensions", "agent_execution_materialization")):
            return True
        if (_starts_with(parts, ("extensions", "event_topology", "nodes"))
                and len(parts) >= 6 and parts[3].isdigit()
                and parts[4] == "parameter_slots"):
            return True
        return _starts_with(parts, ("extensions", "whiteboard_handoff", "completion_notes"))
    return False


def _validate_numeric_action_evidence(operations):
    """Require one explicit evidence pair for every action status decision."""
    evidence = {}
    for operation in operations:
        parts = _tokens(operation["path"])
        if (len(parts) != 4 or parts[:2] != ["rotation_model", "actions"]
                or not parts[2].isdigit() or parts[3] not in {"status", "source"}):
            continue
        evidence.setdefault(parts[2], {})[parts[3]] = operation
    for index, fields in evidence.items():
        if set(fields) != {"status", "source"}:
            raise PlanError(
                "动作审核状态与证据来源必须在同一批修改中提交",
                "agent_stage_patch_forbidden",
                {"action_index": int(index), "required_fields": ["status", "source"]},
            )
        status = _operation_value(fields["status"])
        source = _operation_value(fields["source"])
        if status not in {"assumed", "confirmed"}:
            raise PlanError(
                "动作审核状态只接受明确标注的assumed或confirmed",
                "agent_stage_patch_forbidden",
                {"action_index": int(index)},
            )
        if not isinstance(source, str) or not source.strip():
            raise PlanError(
                "动作审核状态必须同时提供非空证据来源",
                "agent_stage_patch_forbidden",
                {"action_index": int(index)},
            )


def _validate_staged_patch(session, operations):
    """Enforce the write surface independently from prompt/Skill compliance."""
    if session.workflow == "artifact":
        return
    if session.workflow != "staged":
        return
    phase = session.workflow_stage
    panel_paths = (_numeric_panel_paths(session, operations)
                   if phase in ("numeric_model", "calculation_closure") else set())
    if phase in ("numeric_model", "calculation_closure"):
        _validate_numeric_action_evidence(operations)
    for operation in operations:
        parts = _tokens(operation["path"])
        allowed = False
        board_prefix = ("extensions", "whiteboard_handoff", "board")
        if phase == "loadout_draft":
            allowed = ((parts in (["title"], ["notes"])
                        or _starts_with(parts, ("team",))
                        or _starts_with(parts, ("provisional_entities",)))
                       if session._is_board()
                       else (_starts_with(parts, board_prefix)
                             and (parts[3:] in (["title"], ["notes"])
                                  or _starts_with(parts[3:], ("team",))
                                  or _starts_with(
                                      parts[3:], ("provisional_entities",)))))
        elif phase == "action_checkpoint":
            allowed = ((parts == ["duration"] or _starts_with(parts, ("actions",)))
                       if session._is_board()
                       else (_starts_with(parts, board_prefix)
                             and (parts[3:] == ["duration"]
                                  or _starts_with(parts[3:], ("actions",)))))
        elif phase == "event_topology":
            allowed = (_starts_with(parts, ("extensions", "event_topology"))
                       or _starts_with(parts, ("provisional_entities",))
                       or (_starts_with(parts, ("extensions", "whiteboard_handoff", "board", "actions"))
                           and len(parts) == 6 and parts[4].isdigit() and parts[5] == "start")
                       or (_starts_with(parts, ("rotation_model", "actions"))
                           and len(parts) == 4 and parts[2].isdigit() and parts[3] == "start"))
        elif phase in ("numeric_model", "calculation_closure"):
            allowed = _numeric_patch_allowed(parts, panel_paths, operation["path"])
        if not allowed:
            details = {"workflow_stage": phase, "path": operation["path"]}
            if phase == "event_topology":
                details.update({
                    "allowed_write_roots": [
                        "/extensions/event_topology",
                        "/provisional_entities",
                    ],
                    "repair_hint": (
                        "event_topology读取结果是子树；gs_patch_candidate写入必须使用"
                        "完整候选路径/extensions/event_topology"
                    ),
                })
            raise PlanError("当前阶段不能修改该候选路径", "agent_stage_patch_forbidden",
                            details)
    if phase == "event_topology":
        _event_timing_pair(session, operations)


def _apply_patch_operations(candidate, operations):
    """Apply already-authorized JSON Pointer operations to one copied value."""
    for operation in operations:
        parts = _tokens(operation["path"])
        if not parts or parts[0] in (
                "resolution", "resolved_template", "revision", "plan_id", "kind",
                "plan_version", "board_id", "schema_version", "version_line"):
            raise PlanError("不能修改系统身份、缓存或整个根对象", "agent_patch_forbidden")
        if operation["op"] != "remove" and "value" not in operation:
            raise PlanError("添加或替换需要value", "agent_tool_input")
        parent, key = _at(candidate, parts[:-1]), parts[-1]
        if isinstance(parent, list):
            if key == "-" and operation["op"] == "add":
                index = len(parent)
            elif key.isdigit() and str(int(key)) == key:
                index = int(key)
            else:
                raise PlanError("数组修改位置不合法", "agent_tool_input")
            if index > len(parent) or operation["op"] != "add" and index == len(parent):
                raise PlanError("数组修改位置不存在", "agent_tool_input")
            if operation["op"] == "add":
                parent.insert(index, copy.deepcopy(operation["value"]))
            elif operation["op"] == "remove":
                parent.pop(index)
            else:
                parent[index] = copy.deepcopy(operation["value"])
        elif isinstance(parent, dict):
            if operation["op"] != "add" and key not in parent:
                raise PlanError("修改位置不存在", "agent_tool_input")
            if operation["op"] == "remove":
                del parent[key]
            else:
                parent[key] = copy.deepcopy(operation["value"])
        else:
            raise PlanError("修改父节点不是对象或数组", "agent_tool_input")


def _preview_attribute_recovery(session, args):
    from genshin_app.equipment.attribute_recovery import preview_attribute_recovery

    session._expect(args["expected_revision"])
    if getattr(session, '_is_board', lambda: False)():
        raise PlanError("请先把白板准备为原生输入；本工具不修改文档", "agent_native_artifact_required")
    candidate = copy.deepcopy(session.candidate)
    if args.get('operations'):
        _apply_patch_operations(candidate, args['operations'])
    return preview_attribute_recovery(candidate)


def _synchronize_database_entity_refs(previous, candidate):
    from genshin_app.input_editing import synchronize_database_refs
    synchronize_database_refs(previous, candidate)


def _patch_native_staged_board(session, args):
    """Edit the retained board, then atomically regenerate its native handoff."""
    from genshin_app.whiteboard import make_handoff, validate_whiteboard

    from .board_candidate import BoardCandidateSession

    previous_board = copy.deepcopy(session._candidate_board())
    board = copy.deepcopy(previous_board)
    prefix = ("extensions", "whiteboard_handoff", "board")
    translated = []
    for operation in args["operations"]:
        parts = _tokens(operation["path"])
        if not _starts_with(parts, prefix) or len(parts) == len(prefix):
            raise PlanError("原生回退阶段只能修改保留的白板字段",
                            "agent_stage_patch_forbidden",
                            {"workflow_stage": session.workflow_stage,
                             "path": operation["path"]})
        translated.append({
            **operation,
            "path": "/" + "/".join(
                part.replace("~", "~0").replace("/", "~1")
                for part in parts[len(prefix):]),
        })
    _apply_patch_operations(board, translated)
    _synchronize_database_entity_refs(previous_board, board)
    validate_whiteboard(board)
    # Reuse the whiteboard catalog and panel-basis normalization with the
    # retained board as its comparison source. No session field is mutated
    # until replace_candidate commits the complete regenerated plan.
    with session._read_db():
        BoardCandidateSession._normalize_catalog(
            session, board, previous_candidate=previous_board)
        BoardCandidateSession._sync_basis(
            session, board, previous_candidate=previous_board)
        validate_whiteboard(board)
        candidate = make_handoff(board, session.db_path)
    candidate["plan_id"] = session.candidate["plan_id"]
    candidate["revision"] = session.candidate["revision"]
    return session.replace_candidate(
        candidate, args["expected_revision"], args["reason"])


def _patch(session, args):
    session._expect(args["expected_revision"])
    if session.workflow == "artifact":
        from genshin_app.input_editing import edit_candidate
        return edit_candidate(session, args)
    _validate_staged_patch(session, args["operations"])
    if (session.workflow == "staged" and not session._is_board()
            and session.workflow_stage in ("loadout_draft", "action_checkpoint")):
        return _patch_native_staged_board(session, args)
    candidate = copy.deepcopy(session.candidate)
    _apply_patch_operations(candidate, args["operations"])
    _synchronize_database_entity_refs(session.candidate, candidate)
    return session.replace_candidate(candidate, args["expected_revision"], args["reason"])


def _assembly(session, args, function):
    session._expect(args["expected_revision"])
    with session._read_db():
        value = function(session.candidate, args["request"], session.db_path)
    return session.replace_candidate(value["plan"], args["expected_revision"], args["reason"])


def _refresh_data_refs(session, args):
    session._expect(args["expected_revision"])
    with session._read_db():
        value = apply_data_update(session.candidate, session.db_path)
        session.replace_candidate(value["plan"], args["expected_revision"], args["reason"])
        # replace_candidate owns the new revision and discards all input caches.
        # Recompose that exact revision so report input fingerprints remain true.
        return session.compose(session.revision)


def _adopt_attributes(session, args):
    session._expect(args['expected_revision'])
    with session._read_db():
        report = prepare_attribute_review(session.candidate, args['note'], session.db_path)
        session.replace_candidate(report['plan'], args['expected_revision'], args['note'])
        return session.compose(session.revision)


def _record(session, args, *, task_source_origin=None):
    from genshin_app.task_evidence import prepare_record, session_review
    from genshin_app.task_evidence_scope import evidence_scope
    if ("record" in args) == ("records" in args):
        raise PlanError("record与records必须且只能提供一个", "agent_tool_input")
    incoming = args.get("records", [args.get("record")])
    if not isinstance(incoming, list) or not incoming or any(not isinstance(r, dict) for r in incoming):
        raise PlanError("records必须为非空记录对象列表", "agent_tool_input")
    snapshot = getattr(session, "database_snapshot", None)
    if snapshot is not None and hasattr(session, "_db_snapshot"):
        from genshin_app.task_evidence import session_evidence_scope
        scope = session_evidence_scope(session)
    else:
        scope = evidence_scope(snapshot)
    incoming = [prepare_record(r, session.candidate, session.resources, task_source_origin=task_source_origin,
                               scope=scope) for r in incoming]
    if len(session.findings) + len(incoming) > 2000:
        raise PlanError("会话记录超过2000项，可通过supersedes整理旧结论并保留原始记录", "agent_size_limit")
    board_mode = session.workflow == "build" or (
        session.workflow in {"staged", "artifact"} and session._is_board())
    audit = (new_audit(session.candidate) if not board_mode else
             {"schema_version": 1, "plan_id": session.candidate["board_id"],
              "input_fingerprint": session._document_fingerprint(session.candidate), "records": []})
    audit["records"] = [*session.findings, *incoming]
    validate_audit(audit)
    if len(json_bytes(audit["records"])) > 4 * 1024 * 1024:
        raise PlanError("会话记录超过4MiB，请将大原文保存为可读资料引用", "agent_size_limit")
    previous = session.findings
    previous_stages = (copy.deepcopy(session.stage_state["finding_stages"])
                       if session.workflow in {"staged", "artifact"} else None)
    session.findings = copy.deepcopy(audit["records"])
    if session.workflow in {"staged", "artifact"}:
        session.stage_state["finding_stages"].extend([session.workflow_stage] * len(incoming))
    try:
        session.save()
    except Exception:
        session.findings = previous
        if session.workflow in {"staged", "artifact"}:
            session.stage_state["finding_stages"] = previous_stages
        raise
    return {"record_count": len(session.findings), "task_review": session_review(session)}


def _current_diagnostic_error(session):
    from .operation_attempts import current_identity, read_latest_attempt
    latest = read_latest_attempt(session)
    if latest and not latest["matches_current_input"]:
        raise PlanError("输入、数据库或计算策略已变化；最近失败只是历史记录，请查看calculation_attempt后重新核对当前输入。",
                        "agent_diagnostic_stale")
    if latest and latest["matches_current_input"] and latest.get("state") == "failed":
        if isinstance(latest.get("diagnostic_payload"), dict):
            return copy.deepcopy(latest["diagnostic_payload"])
        evidence = latest.get("evidence")
        if isinstance(evidence, dict) and isinstance(evidence.get("reason"), str):
            return copy.deepcopy(evidence)
    if latest is not None:
        raise PlanError("最近一次操作没有当前失败诊断；历史失败可通过其原引用读取", "agent_diagnostic_missing")
    cached = getattr(session, "_diagnostic_cache", None)
    if cached is None:
        raise PlanError("当前输入没有可读的失败诊断；先查看calculation_attempt确认最近尝试是否属于当前输入", "agent_diagnostic_missing")
    with session._read_db(check=False):
        database_current = fingerprint(session._db_snapshot()) == fingerprint(session.database_snapshot)
    if (cached['revision'] != session.revision or cached['candidate_fingerprint'] != fingerprint(session.candidate)
            or cached.get('input_identity') != current_identity(session) or not database_current):
        raise PlanError("候选已修改，旧失败诊断失效；请重新计算", "agent_diagnostic_stale")
    return copy.deepcopy(cached['value'])


def _assistance_evidence(session):
    """Current execution only; retained historical errors never mix with a new plan."""
    if session.calculated is not None:
        return _calculated_package(session), {"source": "current_calculation"}
    latest = _latest_calculation_attempt(session)
    selection = {"source": "current_input_only", "execution_evidence": "not_available"}
    if latest is not None:
        selection["attempt_ref"] = latest["details_ref"]
        if not latest["matches_current_input"]:
            selection["execution_evidence"] = "historical_input_or_policy"
            return {"plan": session.candidate}, selection
        if latest.get("state") != "failed":
            return {"plan": session.candidate}, selection
    try:
        value = _current_diagnostic_error(session)
    except PlanError as error:
        if error.reason not in {"agent_diagnostic_missing", "agent_diagnostic_stale"}:
            raise
        selection["execution_evidence"] = error.reason
        return {"plan": session.candidate}, selection
    # An arbitrary error's details may resemble a trace. It is not evidence
    # that the engine executed; generic failure facts remain in the attempt.
    if value.get("reason") == "mechanics_pending":
        return value, {**selection, "source": "current_execution_diagnostic",
                       "execution_evidence": "recorded_failed_execution"}
    return {"plan": session.candidate}, {**selection, "execution_evidence": "operation_failure_only"}


def _latest_calculation_attempt(session):
    from .operation_attempts import read_latest_attempt
    return read_latest_attempt(session)


def _observed_operation(session, args, operation, execute):
    from .operation_attempts import (
        attempt_projection,
        diagnostic_call,
        finish_attempt,
        start_attempt,
    )
    session._expect(args["expected_revision"])
    attempt = diagnostic_call(session, start_attempt, operation, args)
    session._operation_phase = "input_preflight" if operation == "gs_calculate" else "composition"
    try:
        result = execute(session, args)
    except Exception as error:
        if attempt is not None:
            diagnostic_call(session, finish_attempt, attempt, error=error)
        raise
    if attempt is not None:
        diagnostic_call(session, finish_attempt, attempt)
    if isinstance(result, dict):
        result["latest_attempt"] = diagnostic_call(session, attempt_projection)
    return result


def _calculate(session, args):
    return _observed_operation(session, args, "gs_calculate", _calculate_impl)


def _compose(session, args):
    return _observed_operation(session, args, "gs_compose",
                               lambda current, arguments: current.compose(arguments["expected_revision"]))


def _calculate_impl(session, args):
    from .operation_attempts import current_identity
    session._expect(args["expected_revision"])
    try:
        result = session.calculate(args["expected_revision"])
        session._diagnostic_cache = None
        return result
    except ValueError as exc:
        from genshin_app.calculation_verification_failure import (
            verification_failure_artifact,
        )
        artifact = verification_failure_artifact(exc, session.candidate)
        if artifact is not None:
            candidate_fp = fingerprint(session.candidate)
            attempt_id = uuid.uuid4().hex
            session._diagnostic_cache = {'revision': session.revision, 'attempt_id': attempt_id,
                'candidate_fingerprint': candidate_fp, 'input_identity': current_identity(session), 'value': artifact}
            raise PlanError('计算核验容量不足，未形成接纳结果；当前输入和未封口诊断已保留。',
                'calculation_verification_incomplete', {
                    'resource': 'diagnostic_error', 'revision': session.revision,
                    'attempt_id': attempt_id, 'candidate_fingerprint': candidate_fp,
                    'diagnostic_fingerprint': fingerprint(artifact), 'diagnostic': artifact,
                    'execution_status': 'verification_incomplete',
                    'repair_hint': '读取诊断中的角色、路径、使用量及限额；这是App内部核验容量，不是属性预算或游戏来源缺失。',
                    'recovery': {'action': 'report_capacity_boundary', 'retry_same_request': False},
                }) from exc
        if not isinstance(exc, PlanError):
            raise
        if exc.reason in {'mechanics_pending', 'agent_calculation_rejected'}:
            # Only an executed diagnostic or a published, rejected result
            # supersedes the prior attempt. Input preflight is read-only.
            session._diagnostic_cache = None
        if exc.reason == 'mechanics_pending':
            value = {'code': 1, 'reason': exc.reason, 'message': str(exc), 'details': exc.details}
            # Retain one bounded original error independently of a compose report.
            # A failed or unsupported diagnostic never grants calculation validity.
            detail = exc.details
            if (len(json_bytes(value)) <= MAX_FILE_BYTES and isinstance(detail, dict)
                    and isinstance(detail.get('status'), str) and isinstance(detail.get('issues'), list)
                    and isinstance(detail.get('mechanics_trace'), list)):
                session._diagnostic_cache = {'revision': session.revision, 'attempt_id': uuid.uuid4().hex,
                    'candidate_fingerprint': fingerprint(session.candidate),
                    'input_identity': current_identity(session), 'value': copy.deepcopy(value)}
        raise


def _calculation_preflight_details(details, revision):
    """Bound fresh rejected inputs without pretending an old report is fresh."""
    from genshin_app.diagnostic_facts import diagnostic_facts
    from genshin_app.task_capability import normalize_task_issue

    missing = details["missing_inputs"]
    rows = []
    specific_hint = None
    for item in missing[:10]:
        row = normalize_task_issue(item)
        row["message"] = row["message"][:512]
        row["repair_hint"] = row["repair_hint"][:512]
        facts = diagnostic_facts(item)
        if len(json_bytes(facts)) <= 4000:
            row.update(facts)
        else:
            row["details_truncated"] = True
        if len(json_bytes([*rows, row])) > 12000:
            break
        rows.append(row)
        if (specific_hint is None and isinstance(item, dict)
                and isinstance(item.get("repair_hint"), str) and item["repair_hint"].strip()):
            specific_hint = row["repair_hint"]
    recovery = {"action": "compose_then_read_report", "tool": "gs_compose",
                "expected_revision": revision, "resource": "report", "retry_same_request": False}
    if rows and rows[0]["code"] == "special_reaction_ruleset":
        from genshin_app.domain_errors import tool_failure_guidance
        recovery = tool_failure_guidance(rows[0]["code"], details=rows[0])["recovery"]
    if specific_hint is None and recovery.get("tool") == "gs_compose":
        specific_hint = "按缺项位置补齐输入。需要完整组合报告时，先调用gs_compose，再用gs_read_context读取report。"
    return {
        "state": details.get("state"),
        "missing_inputs": rows,
        "missing_inputs_total": len(missing),
        "missing_inputs_truncated": len(rows) < len(missing),
        "report_updated": False,
        "message": "本次输入预检失败未更新候选、计算或报告；已有报告不代表本次失败。",
        "repair_hint": specific_hint or "按回执中的缺项位置和恢复步骤补齐输入。",
        "recovery": recovery,
    }


def _calculated_package(session):
    from genshin_app.equipment.delivery import (
        PACKAGE_KIND,
        PACKAGE_SCHEMA,
        validate_delivery_calculated,
    )
    checked = validate_delivery_calculated(session.calculated)
    return {"package_schema": PACKAGE_SCHEMA, "kind": PACKAGE_KIND,
            "calculated": session.calculated, "integrity": checked["integrity"]}


def _diagnose(session, args):
    session._expect(args["expected_revision"])
    request = args.get("request") or {}
    if request.get("view") in {"assistance", "energy_trials", "input_trials"}:
        from genshin_app.calculation_assistance import diagnose_assistance
        value, selection = _assistance_evidence(session)
        result = diagnose_assistance(value, request, plan=session.candidate, db_path=session.db_path,
            candidate_session=session, energy_intent=getattr(session, "stage_state", {}).get("energy_intent"))
        return {**result, "evidence_selection": selection}
    value = (_calculated_package(session) if session.calculated is not None
             else _current_diagnostic_error(session))
    if session.calculated is None:
        latest = _latest_calculation_attempt(session)
        cached = getattr(session, '_diagnostic_cache', None)
        value['diagnostic_attempt_id'] = (latest['attempt_id'] if latest and latest['matches_current_input']
                                          else cached['attempt_id'] if cached else None)
    try:
        return inspect_calculation_diagnostics(value, args.get('request'))
    except PlanError as error:
        if (session.calculated is not None or error.reason != "invalid_diagnostic_input"
                or value.get("code") != 1 or value.get("reason") == "mechanics_pending"
                or "calculated" in value or "result_receipt" in value):
            raise
        latest = _latest_calculation_attempt(session)
        raise PlanError("本次记录是操作失败，尚无可聚合的引擎执行诊断；请读取当前失败原文或计算尝试。",
            "agent_diagnostic_view_unavailable", {
                "original_reason": value.get("reason"), "resource": "diagnostic_error",
                "engine_execution": latest.get("engine_execution", "unknown") if latest else "unknown",
                "attempt_ref": latest.get("details_ref") if latest else None,
                "recovery": {"action": "read_operation_failure", "tool": "gs_read_context",
                    "arguments": {"resource": "diagnostic_error", "pointer": ""},
                    "retry_same_request": False}}) from error


def _review(session, args):
    session._expect(args["expected_revision"])
    if session.calculated is None:
        raise PlanError("当前候选尚无有效计算", "agent_calculation_required")
    return review_calculation(_calculated_package(session), args.get("request"))


def _review_batch(session, args):
    from genshin_app.calculation_review_batch import _review_complete_calculated

    from .review_projection import finish
    session._expect(args["expected_revision"])
    if session.calculated is None:
        raise PlanError("当前候选尚无有效计算", "agent_calculation_required")
    return _review_complete_calculated(session.calculated, args["request"],
        _finish=lambda output, budget: finish(session, output, budget,
            maximum_response=MAX_RESPONSE, maximum_resources=MAX_RESOURCES,
            maximum_total=MAX_RESOURCE_TOTAL))


def _version_line(session):
    return (session.candidate["version_line"] if session.workflow == "build" or (
                session.workflow in {"staged", "artifact"} and session._is_board())
            else session.candidate["manual_template"].get("meta", {}).get("version_line", "release"))


def _transition_phase(session, args):
    if session.workflow == "artifact":
        return session.transition_phase(args["target_phase"], args["expected_revision"],
            args["expected_stage_revision"], args["reason"], energy_intent=args.get("energy_intent"),
            prepare_calculation=args.get("prepare_calculation", False))
    if "prepare_calculation" in args:
        raise PlanError("此选项属于候选工件流程", "agent_tool_input")
    return session.transition_phase(args["target_phase"], args["expected_revision"],
        args["expected_stage_revision"], args["reason"], energy_intent=args.get("energy_intent"))


def _validate_topology(session, args):
    session._expect(args["expected_revision"])
    from .staged_candidate import compact_topology_receipt
    return compact_topology_receipt(session.validate_topology())


def _validate_execution_bindings(session, args):
    session._expect(args["expected_revision"])
    from .staged_candidate import compact_topology_binding_receipt
    return compact_topology_binding_receipt(session.validate_topology_bindings())


def _validate_agent_materialization(session, args):
    session._expect(args["expected_revision"])
    if "request" in args:
        from genshin_app.authoring_assistance import authoring_assistance
        request = args["request"]
        if request.get("expected_revision") != session.revision:
            raise PlanError("辅助请求与当前候选修订不一致，请读取当前修订后重试", "agent_stale_revision",
                            {"path": "/request/expected_revision", "actual": request.get("expected_revision"),
                             "expected": session.revision})
        # The session CAS advances across board edits, native conversion and
        # source refresh. The file revision has its own history. Keep the
        # native input and its fingerprint exact; translate only the adapter's
        # revision envelope when calling the same helper used by the file CLI.
        native_request = {**request, "expected_revision": session.candidate["revision"]}
        result = authoring_assistance(
            native_request, plan=session.candidate, composition_report=session.report)
        if result.get("edit_request") is not None:
            result["edit_request"] = {**result["edit_request"], "expected_revision": session.revision}
        return {**result, "candidate_revision": session.revision}
    if session.workflow == "artifact" and session._is_board():
        raise PlanError("当前还是白板，尚无原生机制输入可校验；可用request.kind=catalog查看作者辅助，继续编辑白板或准备原生输入。",
                        "agent_native_artifact_required")
    from genshin_app.equipment.agent_execution_materialization import (
        lint_agent_execution_materialization,
    )
    topology = session._topology()
    document = session.candidate.get("extensions", {}).get(
        "agent_execution_materialization")
    if not isinstance(document, dict):
        raise PlanError("当前候选尚未提交Agent执行材料化输入",
                        "agent_materialization_required")
    return lint_agent_execution_materialization(session.candidate, topology, document)


def _verify_public_data_lookup(session, args):
    return session.verify_provisional_lookup(
        provisional_id=args["provisional_id"],
        expected_revision=args["expected_revision"],
        database_id=args.get("database_id"),
        reason=args["reason"],
    )


def _attest_database_missing(session, args):
    return session.attest_provisional_database_missing(
        provisional_id=args["provisional_id"],
        expected_revision=args["expected_revision"],
        reason=args["reason"],
    )


def _request_public_data_patch_approval(session, args):
    return session.request_provisional_public_patch_approval(
        provisional_id=args["provisional_id"],
        expected_revision=args["expected_revision"],
        reason=args["reason"],
    )


def _query_appendix_tool(session, args):
    request = {
        "target_type": args["target_type"],
        "target_id": args["target_id"],
        "status": args["status"],
        "category": args["category"],
        "version_line": args["version_line"],
        "page": args.get("page", 1),
        "page_size": args.get("page_size", 10),
    }
    return _query(session, {"request": request}, _appendix_query, "appendix")


def _list_appendix_categories_tool(session, args):
    request = {
        "target_type": args["target_type"],
        "target_id": args["target_id"],
        "status": args["status"],
        "version_line": args["version_line"],
    }
    with session._read_db():
        return _appendix_category_index(session.db_path, request)


def _weapon_stats(session, args):
    with session._read_db():
        return query_weapon_stats(session.db_path, args["weapon_id"], args["level"],
            args["ascension"], _version_line(session),
            database_fingerprint=fingerprint(session.database_snapshot))


def _basis(session, args):
    from genshin_app.public_data_query import validate_panel_request
    request = validate_panel_request({**args, "version_line": _version_line(session)})
    with session._read_db():
        return character_panel_basis(session.db_path, **request)


def _reference_panel(session, args):
    with session._read_db():
        result = reference_panel(session.db_path, args["request"], _version_line(session))
        result["comparison_binding"] = {"candidate_revision": session.revision,
            "candidate_input_fingerprint": fingerprint(session.candidate),
            "database_fingerprint": fingerprint(session.database_snapshot),
            "compared_panel": "explicit_request.current_panel", "stale_after_input_or_database_change": True}
        return result


def _add_character(session, args):
    return session.add_character(args["db_character_id"], args["expected_revision"], args["reason"],
                                 actor_id=args.get("actor_id"), level=args.get("level", 90),
                                 constellation=args.get("constellation", 0))


def _search_catalog(session, args):
    from genshin_db.catalog_query import query_catalog
    with session._read_db():
        return query_catalog(session.db_path, {**args, "version_line": _version_line(session)})


DISPATCH = {
    "gs_get_character_appendix_index": lambda s, a: get_character_appendix_index(
        s, a, query=_query, page=page, version_line=_version_line),
    "gs_preview_attribute_recovery": _preview_attribute_recovery,
    "gs_hash_input": lambda _session, args: hash_input(args["kind"], args["value"], maximum_bytes=MAX_ARGUMENTS),
    "gs_read_context": _read_context, "gs_read_skill": _skill,
    "gs_query_context": lambda s, a: _query(s, a, query_context, "context"),
    "gs_query_runtime": lambda s, a: _query(s, a, query_runtime, "runtime"),
    "gs_query_appendix": _query_appendix_tool,
    "gs_list_appendix_categories": _list_appendix_categories_tool,
    "gs_patch_candidate": _patch,
    "gs_assemble_attack": lambda s, a: _assembly(s, a, assemble_attack_source),
    "gs_assemble_periodic": lambda s, a: _assembly(s, a, assemble_periodic_source),
    "gs_refresh_data_refs": _refresh_data_refs,
    "gs_compose": _compose,
    "gs_calculate": _calculate,
    "gs_diagnose_result": _diagnose,
    "gs_adopt_attributes": _adopt_attributes,
    "gs_review_result": _review, "gs_record_finding": _record,
    "gs_review_result_batch": _review_batch,
    "gs_patch_whiteboard": _patch, "gs_add_character": _add_character, "gs_character_panel_basis": _basis,
    "gs_search_catalog": _search_catalog, "gs_weapon_stats": _weapon_stats, "gs_reference_panel": _reference_panel,
    "gs_transition_phase": _transition_phase, "gs_validate_topology": _validate_topology,
    "gs_validate_execution_bindings": _validate_execution_bindings,
    "gs_validate_agent_materialization": _validate_agent_materialization,
    "gs_verify_public_data_lookup": _verify_public_data_lookup,
    "gs_attest_database_missing": _attest_database_missing,
    "gs_request_public_data_patch_approval": _request_public_data_patch_approval,
}


def validate_tool_arguments_for_session(session, name, args):
    """Apply the existing App permission and schema gate without executing.

    This is the public preflight used by the native V2 bridge.  It deliberately
    does not grant authority: :func:`call_tool` repeats this function immediately
    before dispatch, so a stale stage or changed schema still fails closed.
    """
    if name not in DISPATCH or name not in _allowed_session(session):
        raise PlanError("未授权的Agent工具", "agent_tool_forbidden")
    if name == "gs_review_result_batch":
        # Request traversal belongs to the shared bounded batch operation; do
        # not run the legacy protocol-accepting JSON walker over its request.
        if (type(args) is not dict or set(args) != {"expected_revision", "request"}
                or type(args["expected_revision"]) is not int
                or args["expected_revision"] < 0
                or type(args["request"]) is not dict):
            raise PlanError("工具参数不合法", "agent_tool_input")
        return copy.deepcopy(args)
    _json_guard(args)
    if len(json_bytes(args)) > MAX_ARGUMENTS:
        raise PlanError("工具输入超过512KiB", "agent_size_limit")
    schema = _tool_schema_for_session(session, name)
    _limits(args, schema)
    errors = []
    _check(args, schema, "request", errors)
    if errors:
        raise PlanError("工具参数不合法", "agent_tool_input", errors[:30])
    return copy.deepcopy(args)


@session_fault_context
def call_tool(session, name, args):
    try:
        args = validate_tool_arguments_for_session(session, name, args)
        if name == "gs_review_result_batch":
            # Its finalizer checks code/data before publication and returns the
            # already-built response, with no post-commit packaging to fail.
            return DISPATCH[name](session, args)
        data = DISPATCH[name](session, args)
        if session.workflow in {"staged", "artifact"} and name in {
                "gs_patch_candidate", "gs_patch_whiteboard", "gs_add_character",
                "gs_transition_phase", "gs_assemble_attack", "gs_assemble_periodic",
                "gs_refresh_data_refs", "gs_compose", "gs_calculate", "gs_adopt_attributes",
                "gs_verify_public_data_lookup", "gs_attest_database_missing",
                }:
            data = session.compact_tool_response(data)
        response = {"code": 0, "data": data}
        response_limit = MAX_RESPONSE
        if name == "gs_diagnose_result" and (args.get("request") or {}).get("view") in {"assistance", "energy_trials", "input_trials"}:
            from genshin_app.calculation_assistance import MAX_RESPONSE_BYTES
            response_limit = MAX_RESPONSE_BYTES
        if len(json_bytes(response)) > response_limit:
            raise PlanError("返回过大，请通过定向分页读取完整资料", "agent_response_limit")
        return response
    except CalculationOutputValidationFailure as exc:
        return copy.deepcopy(exc.response)
    except (PlanError, ValueError, TypeError, KeyError, OSError, sqlite3.Error, EquipmentError, queries.NotFoundError) as exc:
        from genshin_engine.execution_control import ExecutionCancelled
        if isinstance(exc, ExecutionCancelled):
            from genshin_app.execution_cancellation import cancellation_response
            return cancellation_response(exc, operation=name)
        if not is_domain_exception(exc):
            return system_failure(exc, component="python_engine_runtime" if isinstance(exc, ImportError) else "app_domain_tools", operation=name)
        from genshin_app.domain_errors import (
            classify_domain_failure,
            tool_failure_guidance,
        )

        details = getattr(exc, "details", None)
        from genshin_app.app_system_fault import domain_exception_reason
        reason = domain_exception_reason(exc)
        # Classify the original structured failure before replacing megabytes
        # of trace with a resource pointer. That pointer is not the failure.
        classification = classify_domain_failure(reason, operation=name, details=details)
        guidance = tool_failure_guidance(reason, operation=name, details=details)
        # Keep a reference only when that operation has actually saved evidence.
        if (name == 'gs_calculate' and reason == 'missing_input'
                and isinstance(details, dict) and isinstance(details.get('missing_inputs'), list)):
            details = _calculation_preflight_details(details, session.revision)
            compact_guidance = tool_failure_guidance(reason, operation=name, details=details)
            # normalize_task_issue deliberately drops producer metadata; retain
            # the exact comparison from the original failure in its short view.
            if "failure_summary" in guidance:
                compact_guidance["failure_summary"] = guidance["failure_summary"]
            guidance = compact_guidance
        elif (name == 'gs_calculate' and reason == 'mechanics_pending'
                and getattr(session, '_diagnostic_cache', None) is not None):
            details = {"message": "完整失败保留在当前修订；用gs_diagnose_result聚合，或读取diagnostic_error原文。",
                       "resource": "diagnostic_error", "revision": session.revision,
                       "execution_status": session._diagnostic_cache['value']['details']['status'],
                       "issue_codes": sorted({str(row.get('code', 'unknown'))[:128]
                           for row in session._diagnostic_cache['value']['details'].get('issues', [])
                           if isinstance(row, dict)})[:32],
                       "issue_count": len(session._diagnostic_cache['value']['details'].get('issues', [])),
                       "attempt_id": session._diagnostic_cache['attempt_id']}
        elif name == "gs_calculate" and reason == "agent_calculation_rejected" and isinstance(details, dict):
            if len(json_bytes(details)) > 16000:
                details = {key: value for key, value in details.items() if key != "issues"}
                details["issue_resource"] = "calculated"
        else:
            details = _bounded_error_details(details)
        attempt = None
        if name in {"gs_calculate", "gs_compose"}:
            from .operation_attempts import attempt_projection
            latest = attempt_projection(session)
            if latest and latest.get("matches_current_input") and latest.get("reason") == reason:
                attempt = latest
        return {"code": 1, "message": sanitize_public_text(str(exc)[:4096]), "reason": reason,
                **classification, **guidance, "details": details,
                **({"latest_attempt": attempt, "user_action": None} if attempt else {})}
    except Exception as exc:  # noqa: BLE001 - unexpected program faults cross one Host boundary
        return system_failure(exc, component="python_engine_runtime" if isinstance(exc, ImportError) else "app_domain_tools", operation=name)
