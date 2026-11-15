"""V4 native baseline facts; resolved panels stay the single initial authority."""
from __future__ import annotations

import copy
import math

from genshin_engine.attribute_budget import (
    declaration_capacity_issue,
    limits_for_schema,
    normalize_attribute_budget,
)
from genshin_engine.attribute_ledger import (
    AttributeLedgerError,
    _bounded_text,
    _canonical,
    attribute_schema_error,
)
from genshin_engine.attribute_runtime_schema import (
    BASE_BASIS,
    CLASSIFICATION,
    ID,
    INCLUDED_COMPONENT,
    NONEXTRA_BASIS,
)
from genshin_engine.attribute_stats import BASE_STATS, CONTRACT, STATS
from genshin_engine.rotation import _schema_errors
from genshin_engine.rotation_schema import arr, obj

PANEL_BASIS = obj({"actor": ID, "stat": {"enum": list(BASE_STATS)},
                  "included_components": arr(INCLUDED_COMPONENT), "nonextra": NONEXTRA_BASIS},
                 ["actor", "stat", "included_components"])
EXPLICIT_BASIS = obj({"actor": ID, "stat": {"enum": ["em", "energy_recharge"]},
                     "included_components": arr(INCLUDED_COMPONENT), "base": BASE_BASIS, "nonextra": NONEXTRA_BASIS},
                    ["actor", "stat", "included_components"])
NATIVE_SCHEMA_V4 = obj({"contract": {"const": CONTRACT},
                        "initial_basis": arr({"oneOf": [PANEL_BASIS, EXPLICIT_BASIS]}),
                        "effect_classifications": arr(CLASSIFICATION)},
                       ["contract", "initial_basis", "effect_classifications"])


def prepare_initials(template, model, *, energy_baselines=None, limits=None):
    limits = normalize_attribute_budget(limits)['effective']
    encoded = _canonical(model)
    if len(encoded) > limits['declaration_bytes']:
        issue = declaration_capacity_issue('declaration_bytes', len(encoded), limits['declaration_bytes'], 'attribute_model')
        raise AttributeLedgerError(issue['code'], issue['message'], issue['path'], status=issue['severity'],
            **{k: v for k, v in issue.items() if k not in ('code', 'message', 'path', 'severity')})
    _bounded_text(model, max_depth=limits['declaration_depth'])
    errors = []
    _schema_errors(model, limits_for_schema(NATIVE_SCHEMA_V4, limits), "attribute_model", errors)
    if errors:
        raise attribute_schema_error(errors)
    chars = {c["id"]: c for c in template["characters"]}
    if len(chars) != len(template["characters"]):
        raise AttributeLedgerError("attribute_basis_reference", "角色ID重复")
    declarations = {}
    for row in model["initial_basis"]:
        key = (row["actor"], row["stat"])
        if row["actor"] not in chars or key in declarations:
            raise AttributeLedgerError("attribute_basis_reference", "基底角色不存在或属性重复")
        declarations[key] = row

    def number(value):
        if type(value) not in (int, float) or not math.isfinite(value) or value < 0:
            raise AttributeLedgerError("attribute_value_invalid", "初始属性必须有限且非负")
        return value

    result = []
    for actor, char in chars.items():
        panel = dict(char["stats"])
        if energy_baselines is not None and actor in energy_baselines:
            panel["energy_recharge"] = energy_baselines[actor]
        for stat in STATS:
            if stat not in panel:
                # A missing optional baseline is not manufactured as zero.
                continue
            row = declarations.get((actor, stat), {})
            components = [{**copy.deepcopy(c), "component": {**c["component"], "recipient": actor}}
                          for c in row.get("included_components", [])]
            if any(c["component"]["provider"] not in chars for c in components):
                raise AttributeLedgerError("attribute_component_reference", "已含组件提供者不在队伍")
            initial = {"actor": actor, "stat": stat, "total": number(panel[stat]),
                       "included_components": components, "source": "当前解析面板／实际动作ER；已含组件不再加算"}
            if stat in BASE_STATS and "base_" + stat in panel:
                initial["base"] = number(panel["base_" + stat])
            for basis in ("nonextra",) if stat in BASE_STATS else ("nonextra", "base"):
                fact = row.get(basis, {})
                if fact.get("status") == "pending" or fact.get("mode", "unknown") == "unknown":
                    continue
                if fact["mode"] == "panel_total":
                    if any(c["nonextra_inclusion"] == "unknown" or c["nonextra_inclusion"] == "excluded" and c["value"] != 0
                           for c in components):
                        raise AttributeLedgerError("attribute_panel_total_conflict", "面板组成不支持全额NONEXTRA声明")
                    initial[basis] = initial["total"]
                else:
                    initial[basis] = number(fact["value"])
            result.append(initial)
    return result
