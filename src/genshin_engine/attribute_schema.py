"""Pure constant attribute accounting; execution/source authority stays outside."""
from __future__ import annotations

from genshin_engine.attribute_budget import (
    DEFAULT_LIMITS,
    attribute_budget_capabilities,
    limits_for_schema,
)
from genshin_engine.rotation_schema import arr, obj

CONTRACT = "attribute-ledger-v1"
LIMITS = {k: v for k, v in DEFAULT_LIMITS.items() if k not in ('dependency_depth', 'output_bytes')}
ID = {"type": "string", "minLength": 1, "maxLength": 128}
NUM = {"type": "number"}
BASIS = ("total", "nonextra", "base")
INCLUSION = {"enum": ["included", "excluded", "unknown"]}
COMPONENT = obj({key: ID for key in
                 ("source_fingerprint", "provider", "part_id", "effect_id", "recipient")},
                ['source_fingerprint', 'provider', 'part_id', 'effect_id', 'recipient'])
INCLUDED = obj({"component": COMPONENT, "value": NUM, "nonextra_inclusion": INCLUSION},
               ['component', 'value', 'nonextra_inclusion'])
INITIAL = obj({"actor": ID, "stat": {"const": "em"},
               **{key: {"type": "number", "minimum": 0} for key in BASIS},
               "included_components": arr(INCLUDED), "source": {"type": "string", "minLength": 1, "maxLength": 4096}},
              ["actor", "stat", "total", "included_components", "source"])
CONTRIBUTION = obj({"id": ID, "component": COMPONENT, "instance_id": ID, "layer_id": ID,
                    "stat": {"const": "em"},
                    # Opaque future expressions are retained but never executed in L1.
                    "value": {"oneOf": [NUM, {"type": "object"}]},
                    "nonextra_inclusion": INCLUSION,
                    "visibility": {"enum": ["attribute", "damage_context"]},
                    "constraints": arr({"enum": ["filter", "quota", "resource_gate", "hit_match"]})},
                   ['id', 'component', 'instance_id', 'layer_id', 'stat', 'value',
                    'nonextra_inclusion', 'visibility', 'constraints'])
FRAME = obj({"id": ID, "origin": {"type": "integer", "minimum": 0, "maximum": 1000000},
             "run_id": ID, "event_id": ID,
             "phase": {"enum": ["initial", "pre", "post", "reaction_created", "reaction_damage_pre", "capture", "trigger"]},
             "state_epoch": {"type": "integer", "minimum": 0, "maximum": 10**9},
             "t_us": {"type": "integer", "minimum": 0, "maximum": 10**15},
             "contribution_ids": arr(ID)},
            ['id', 'origin', 'run_id', 'event_id', 'phase', 'state_epoch', 't_us', 'contribution_ids'])
ATTRIBUTE_LEDGER_SCHEMA = obj({"contract": {"const": CONTRACT}, "initials": arr(INITIAL),
                              "contributions": arr(CONTRIBUTION), "frames": arr(FRAME)},
                             ['contract', 'initials', 'contributions', 'frames'])


def attribute_ledger_capabilities():
    return {"contract": CONTRACT, "schema": limits_for_schema(ATTRIBUTE_LEDGER_SCHEMA, DEFAULT_LIMITS),
            "limits": dict(LIMITS), "budget": attribute_budget_capabilities(), "stats": ["em"], "basis": list(BASIS),
            "expressions": "unsupported_in_l1", "frame_authority": "caller_supplied_frame",
            "purpose": ["ordinary"], "classification": "explicit_source_fact_not_inferred_from_value",
            "base": "独立显式基底；不等于用户给定total，不从total或nonextra推导。",
            "included_components": "只解释已含面板，不再次加算；按来源组件身份拒绝重复交付。",
            "execution": "纯读取常量贡献；不执行动作、触发、消费或数据库来源审核。"}
