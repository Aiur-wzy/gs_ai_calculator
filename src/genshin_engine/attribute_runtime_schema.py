"""Opt-in native facts for the constant EM ledger; never native frame input."""
from __future__ import annotations

from genshin_engine.attribute_schema import CONTRACT, INCLUSION
from genshin_engine.rotation_schema import arr, obj

ID = {"type": "string", "minLength": 1, "maxLength": 128}
EVIDENCE = {"status": {"enum": ["confirmed", "assumed", "pending"]},
            "source": {"type": "string", "minLength": 1, "maxLength": 4096}}
COMPONENT = obj({key: ID for key in ("source_fingerprint", "provider", "part_id", "effect_id")},
                ['provider', 'part_id', 'effect_id'])
EXPLICIT_BASIS = obj({"mode": {"const": "explicit"}, "value": {"type": "number", "minimum": 0}, **EVIDENCE},
                     ['mode', 'value', 'status', 'source'])
NONEXTRA_BASIS = {"oneOf": [EXPLICIT_BASIS, obj({"mode": {"enum": ["panel_total", "unknown"]}, **EVIDENCE}, ['mode', 'status', 'source'])]}
BASE_BASIS = {"oneOf": [EXPLICIT_BASIS, obj({"mode": {"const": "unknown"}, **EVIDENCE}, ['mode', 'status', 'source'])]}
INCLUDED_COMPONENT = obj({"component": COMPONENT, "value": {"type": "number"}, "nonextra_inclusion": INCLUSION},
                         ['component', 'value', 'nonextra_inclusion'])
EFFECT_REF = {"oneOf": [obj({"kind": {"const": "rule"}, "rule_id": ID, "effect_id": ID}, ['kind', 'rule_id', 'effect_id']),
                        obj({"kind": {"const": "manual_buff"}, "buff_id": ID, "effect_id": ID}, ['kind', 'buff_id', 'effect_id'])]}
CLASSIFICATION = obj({"effect_ref": EFFECT_REF, "component": COMPONENT,
                      "nonextra_inclusion": INCLUSION, **EVIDENCE},
                     ['effect_ref', 'component', 'nonextra_inclusion', 'status', 'source'])
ATTRIBUTE_MODEL_SCHEMA = obj({"contract": {"enum": [CONTRACT, "attribute-ledger-v2"]},
                              "initial_basis": arr(obj({"actor": ID, "nonextra": NONEXTRA_BASIS, "base": BASE_BASIS,
                                                         "included_components": arr(INCLUDED_COMPONENT)},
                                                        ["actor", "included_components"])),
                              "effect_classifications": arr(CLASSIFICATION)},
                             ['contract', 'initial_basis', 'effect_classifications'])
