"""On-demand contracts projected from the actual domain tool producers."""
from __future__ import annotations

import copy

from genshin_app.equipment.files import fingerprint

RESOURCE_NAMES = ("capability_index", "tool_contracts", "runtime_contracts")


def _plan_contract_navigation(session):
    from genshin_app.equipment.schema import PLAN_SCHEMA

    def ref(resource, pointer):
        return {"resource": resource, "pointer": pointer}

    navigation = {
        "schema": ref("plan_schema", ""),
        "schema_scope": "outer_plan_shape; use native_details.authoring_sections for full models",
        "sections": {
            name: ref("plan_schema", "/properties/" + name.replace("~", "~0").replace("/", "~1"))
            for name in PLAN_SCHEMA["properties"]
        },
        "whiteboard_schema": ref("whiteboard_schema", ""),
        "native_details": {
            "available_when": {**ref("capabilities", "/kind"), "equals": "genshin-plan"},
            "references": {
                "expanded_schema": ref("capabilities", "/schema"),
                "semantic_constraints": ref("capabilities", "/semantic_constraints"),
                "examples": ref("capabilities", "/examples"),
                "engine_registry": ref("capabilities", "/engine_v2"),
            },
            "authoring_sections": {
                "rotation_model": ref("capabilities", "/schema/properties/rotation_model/anyOf/0"),
                "rotation_actions": ref("capabilities", "/schema/properties/rotation_model/anyOf/0/properties/actions/items"),
                "mechanics_model": ref("capabilities", "/schema/properties/mechanics_model/anyOf/0"),
                "effect_bindings": ref("capabilities", "/schema/properties/effect_bindings/items"),
                "mechanics_empty_model": ref("mechanics_capabilities", "/empty_model"),
            },
            "note": "仅原生计算表示提供这些扩展资料；白板仍可读独立plan_schema。原生和当前候选CLI的pointer相对资源根；独立capabilities命令返回data.plan_contract包装。",
        },
        "write_authority": "none",
        "note": "以上是只读格式导航，不是可写字段或权限清单；输出字段仍由引擎和Host生成。按实际工具合同提交输入。",
    }
    report = session.report
    if isinstance(report, dict):
        previews = {name: ref("report", "/" + name) for name in (
            "effect_requirements", "manual_dependency_previews") if name in report}
        if previews:
            navigation["composition_drafts"] = {
                "references": previews,
                "execution_authority": "none",
                "note": "当前候选compose报告中的proposed_binding和依赖预览只是待核实草稿，不是批准或完整执行输入。",
            }
    return navigation


def tool_contracts(session):
    from . import tools
    from .native_app_action_tools import definitions as action_definitions
    from .native_app_tool_catalog import native_app_tool_policy
    from .native_research_tools import definitions as research_definitions

    definitions = [*tools.definitions_for_session(session), *action_definitions(), *research_definitions()]
    return {"contract": "genshin-native-tool-contracts-v1", "tools": {
        row["name"]: {"contract_version": 1, "owner": "genshin_app", "callable_surface": "native_app_host",
            "availability": "available", "input_schema": copy.deepcopy(row["inputSchema"]),
            "schema_hash": fingerprint(row["inputSchema"]), "description": row["description"],
            **native_app_tool_policy(row["name"]),
            "result": {"success": "code=0,data", "failure": "code/reason/category/details/recovery；不等于已执行成功"},
            "recovery": {"action": "correct_arguments", "resource": "tool_contracts",
                         "pointer": "/tools/" + row["name"]}}
        for row in sorted(definitions, key=lambda item: item["name"])}}


def capability_index(session):
    from .tools import fixed_context_resource_names

    value = tool_contracts(session)
    resources = fixed_context_resource_names(session)
    return {"contract": "genshin-native-capability-index-v1",
            "workflow": session.workflow,
            "domain_tools": [{"name": name, "effect": contract["effect"],
                "contract_ref": {"resource": "tool_contracts", "pointer": "/tools/" + name}}
                for name, contract in value["tools"].items()],
            "resources": resources,
            "resource_refs": {name: {"resource": name, "pointer": ""} for name in resources},
            "fixed_resources_complete": True,
            "resources_exhaustive": False,
            "resources_are_permission_allowlist": False,
            "resource_availability": "固定名称来自实际getter和读取路由；入口存在不表示当前已生成数据或本工件适用，也不绕过实际读取校验。",
            "logical_resource_rules": "查询/记录返回的精确会话资源ID可直接作为resource；review-*由审查工具返回。它们不在固定列表逐一铺开，不接受任意文件路径。",
            "task_workspace": {"read": {"resource": "task_evidence"},
                "record_tool": "gs_record_finding", "record_fields": "record或records批量；supersedes保留替代历史",
                "cli": "workflow workspace --plan PLAN --audit AUDIT [--request REQUEST] [--task-file TEXT]",
                "authority": "advisory_only", "acceptance_effect": "none"},
            "publication_usage": {"read": {"resource": "publication_usage",
                "request": {"entities": [{"type": "character", "id": 1}],
                            "purpose": "calculation", "scope": "current-task"}},
                "request_note": "实体ID示例须替换为本任务拟采用的真实ID。普通浏览不需要确认。",
                "question_tool": "ask_user_question", "authority": "usage_notice_only", "acceptance_effect": "none"},
            "source_notebooks": {"read": {"resource": "source-notebook:query-N"},
                "query_option": "gs_query_context.related_sources=true",
                "cli": "db context --input REQUEST --related-sources --notebook-dir DIR",
                "raw": "完整原始查询投影；未请求和真正缺失分开", "execution_authority": "none"},
            "plan_contract": _plan_contract_navigation(session),
            "event_topology": {
                name: {"resource": resource, "pointer": pointer}
                for name, resource, pointer in (
                    ("document_schema", "event_topology_contracts", "/topology_schema"),
                    ("source_schema", "event_topology_capabilities", "/component_json_schema/$defs/source"),
                    ("node_schema", "event_topology_capabilities", "/component_json_schema/$defs/node"),
                    ("schedule_schema", "event_topology_capabilities", "/component_json_schema/$defs/schedule"),
                    ("schedule_templates", "event_topology_capabilities", "/component_contracts/schedule/variant_templates"),
                    ("execution_bindings_schema", "event_topology_contracts", "/execution_bindings_schema"),
                    ("execution_binding_item", "event_topology_contracts", "/execution_bindings_schema/$defs/binding"),
                    ("execution_targets", "event_topology_binding_capabilities", "/node_target_kinds"),
                    ("filling", "event_topology_contracts", "/filling"),
                    ("examples", "event_topology_contracts", "/examples"),
                )
            },
            "special_reactions": {
                name: {"resource": "special_reaction_capabilities", "pointer": pointer}
                for name, pointer in {
                    "catalog": "/engine_catalog/profiles",
                    "handler_registry": "/engine_catalog/handler_registry",
                    "qualification_schema": "/qualification_schema",
                    "model_schema": "/input_contract/schemas/model",
                    "event_schema": "/input_contract/schemas/event",
                    "party_capability_schema": "/input_contract/schemas/party_capability",
                    "direct_stellar_profile_schema": "/input_contract/schemas/direct_stellar_damage_profile",
                    "direct_lunar_profile_schema": "/input_contract/schemas/direct_lunar_damage_profile",
                    "filling": "/filling", "examples": "/examples",
                }.items()
            },
            "note": "此目录只说明合同；实际调用仍由当前绑定、工件、用户批准和Host精确校验。GUI方法不是runtime action。"}


def runtime_contracts():
    from genshin_db.runtime_request_contracts import runtime_contracts as producer
    return producer()
