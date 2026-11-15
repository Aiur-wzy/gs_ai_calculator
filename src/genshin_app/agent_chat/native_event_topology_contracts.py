"""Read-only topology/root binding documentation; original validators own admission."""
from __future__ import annotations

import copy

from genshin_app.equipment import event_topology as topology
from genshin_app.equipment import event_topology_binding as binding
from genshin_app.equipment.files import fingerprint


def _closed(properties, fields):
    return {"type": "object", "additionalProperties": False,
            "properties": properties, "required": list(fields)}


def _example():
    checkpoint = {"revision": 0, "fingerprint": "0" * 64, "action_ids": ["example.action"]}
    document = topology.new_event_topology("example.topology", action_checkpoint=checkpoint,
        note="工程格式例；实际提交须沿用当前候选的action_checkpoint，不证明角色事实。")
    document["declaration"]["completeness"] = "declared_complete"
    document["sources"] = [{
        "source_id": "example.source", "category": "scenario", "owner_ref": "example.actor",
        "description": "工程输入声明的一次动作及命中。", "disposition": "modeled",
        "node_ids": ["example.anchor", "example.damage"], "reason": None,
        "source_refs": [{"kind": "user_input", "reference": "工程格式例，须替换为当前事实来源。",
                         "fingerprint": None, "status": "declared"}],
    }]
    common = {"status": "bound", "source_ids": ["example.source"], "parameter_slots": [], "note": ""}
    document["nodes"] = [
        {**copy.deepcopy(common), "node_id": "example.anchor", "kind": "action_anchor",
         "schedule": {"kind": "point", "anchor": {"kind": "action", "ref_id": "example.action", "phase": "start"},
                      "offset_seconds": 0, "order": 0}},
        {**copy.deepcopy(common), "node_id": "example.damage", "kind": "damage",
         "schedule": {"kind": "point", "anchor": {"kind": "node", "ref_id": "example.anchor", "phase": "point"},
                      "offset_seconds": .2, "order": 1}},
    ]
    target = {"id": "example.hit", "t": 1.2, "character": "example.actor", "skill": "工程命中",
              "multiplier": 1, "element": "physical", "damage_system": "classic",
              "damage_type_ids": ["damage.elemental_skill"]}
    bindings = binding.new_event_topology_bindings(document)
    bindings["bindings"] = [{"binding_id": "example.binding", "node_id": "example.damage",
        "target_kind": "hit", "pointer": "/manual_template/hits/0",
        "target_fingerprint": fingerprint(target), "note": "绑定整个当前命中输入，非结果或ID叶节点。"}]
    return {"topology": document, "execution_bindings": bindings,
            "target_input_fragment": {"manual_template": {"hits": [target]}}}


def event_topology_contracts():
    """Describe existing formats, never generate nodes in the active candidate."""
    capabilities = topology.event_topology_capabilities()
    binding_capabilities = binding.event_topology_binding_capabilities()
    components = copy.deepcopy(capabilities["component_json_schema"])
    definitions = components["$defs"]
    identifier = copy.deepcopy(definitions["identifier"])
    digest = copy.deepcopy(definitions["source_ref"]["properties"]["fingerprint"]["anyOf"][0])
    text = {"type": "string", "maxLength": topology.MAX_TEXT}
    example = _example()
    draft = topology.new_event_topology("example.draft", action_checkpoint=example["topology"]["action_checkpoint"])
    root_schema = {
        **components, "$id": "urn:genshin:event-topology-v1:document",
        **_closed({
            "contract_id": {"const": topology.CONTRACT_ID},
            "schema_version": {"const": topology.SCHEMA_VERSION},
            "topology_id": identifier, "authority": {"const": topology.AUTHORITY},
            "declaration": _closed({"character_semantics": {"const": draft["declaration"]["character_semantics"]},
                "completeness": {"enum": [draft["declaration"]["completeness"], "declared_complete"]},
                "note": text}, draft["declaration"]),
            "action_checkpoint": _closed({"revision": {"type": "integer", "minimum": 0, "maximum": 2**53-1},
                "fingerprint": digest, "action_ids": {"type": "array", "minItems": 1,
                    "maxItems": topology.MAX_ACTIONS, "uniqueItems": True, "items": identifier}}, draft["action_checkpoint"]),
            "dependencies": {"type": "array", "maxItems": topology.MAX_DEPENDENCIES, "items": _closed({
                "dependency_id": identifier, "kind": {"enum": sorted(topology.DEPENDENCY_KINDS)},
                "fingerprint": digest, "required": {"type": "boolean"}},
                ("dependency_id", "kind", "fingerprint", "required"))},
            **{name: {"type": "array", "maxItems": maximum, "items": {"$ref": f"#/$defs/{component}"}}
               for name, maximum, component in (("sources", topology.MAX_SOURCES, "source"),
                    ("nodes", topology.MAX_NODES, "node"), ("edges", topology.MAX_EDGES, "edge"))},
        }, draft),
    }
    row_template = binding_capabilities["template"]
    row_schema = _closed({
        "binding_id": identifier, "node_id": identifier,
        "target_kind": {"enum": sorted(binding.TARGET_ROOTS)},
        "pointer": {"type": "string", "minLength": 1,
                    "maxLength": binding_capabilities["limits"]["pointer_chars"], "pattern": r"^/(?:[^~]|~[01])*$"},
        "target_fingerprint": digest, "note": {"type": "string", "maxLength": binding.MAX_TEXT},
    }, row_template)
    bindings_schema = {
        "$schema": components["$schema"], "$id": "urn:genshin:event-topology-execution-bindings-v1:document",
        **_closed({"contract_id": {"const": binding.CONTRACT_ID},
            "schema_version": {"const": binding.SCHEMA_VERSION}, "topology_id": identifier,
            "topology_fingerprint": digest, "bindings": {"type": "array", "maxItems": binding.MAX_BINDINGS,
                "items": {"$ref": "#/$defs/binding"}}}, example["execution_bindings"]),
        "$defs": {"binding": row_schema},
    }
    return {
        "contract": "genshin-native-event-topology-contracts-v1",
        "topology_schema": root_schema, "execution_bindings_schema": bindings_schema,
        "component_capabilities": capabilities,
        "binding_capabilities": binding_capabilities,
        "examples": {"draft": draft, "single_damage": example},
        "filling": {
            "cli": "equipment topology --plan PLAN [--input DOCUMENT] [--output JSON]；空骨架的current_action_checkpoint由App复用原生动作投影生成，不改输入。",
            "topology_path": "/extensions/event_topology",
            "execution_bindings_path": "/extensions/event_topology_bindings",
            "checkpoint": "先读当前event_topology或candidate里的action_checkpoint并原样保留；格式例的零指纹不是当前动作证明。",
            "components": {"resource": "event_topology_capabilities", "pointer": "/component_contracts",
                           "cli_pointer": "/plan_contract/event_topology_contracts/component_capabilities/component_contracts"},
            "schedule": "kind选point/periodic/explicit_offsets/pending；动作关系写anchor.kind/ref_id/phase，不写action_relative/instant/action_start。未知时点允许合同明确的null，但未闭合不能当numeric_ready。",
            "source": "disposition选modeled/not_applicable/pending；这是来源盘点，不是执行批准。modeled列真实node_ids，不适用或待核实给具体reason。",
            "execution_binding": "除action_anchor或excluded外的活跃节点须绑定当前强类型输入或已有受支持材料化；按target_kind允许的完整对象指针，不绑定label/ID叶节点或result。",
            "binding_capabilities": {"resource": "event_topology_binding_capabilities", "pointer": "",
                "cli_pointer": "/plan_contract/event_topology_contracts/binding_capabilities"},
            "hash": {"resource": "tool_contracts", "pointer": "/tools/gs_hash_input"},
            "fingerprints": "canonical_json分别计算完整当前topology和指针选中的整个执行输入对象；用data.content_hash填写topology_fingerprint/target_fingerprint。内容改变即重算，不用文档content_hash代替。",
            "qualification": "schema只说明形状，原验证器仍校验引用、时序、覆盖、新鲜度和既有执行对象；拓扑/绑定完整不证明角色事实、实际运行消费或正式接纳。",
            "example_scope": "single_damage是独立工程格式例。不能按它自动替用户生成节点、替代实际动作检查点或标全角色完整；读资源不写入当前候选。",
            "authority": "none",
        },
    }
