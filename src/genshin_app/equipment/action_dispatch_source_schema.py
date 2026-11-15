"""Native declarations for exact reviewed action-dispatch profiles.

The declaration names one existing rotation ability and one exact database
skill identity.  It deliberately contains no hit rows, multipliers, elements,
ICD answers or reaction answers; those remain owned by the reviewed dispatch
catalog and fresh runtime queries.
"""
from __future__ import annotations

import copy
import json
import math
from decimal import Decimal

from .action_event_schema import ID, SOURCE, _check, _object
from .application_evidence import APPLICATION_ADAPTER

HASH = {"type": "string", "pattern": r"^[0-9a-f]{64}$"}
NULLABLE_STAGE = {"anyOf": [{"type": "integer", "minimum": 1, "maximum": 64}, {"type": "null"}]}
SELECTOR = _object({
    "character_id": {"type": "integer", "minimum": 1, "maximum": 2**53 - 1},
    "skill_depot_id": {"type": "integer", "minimum": 1, "maximum": 2**53 - 1},
    "skill_id": {"type": "integer", "minimum": 1, "maximum": 2**53 - 1},
    "slot": {"enum": ["normal", "skill", "burst"]},
    "effective_level": {"type": "integer", "minimum": 1, "maximum": 15},
    "constellation": {"type": "integer", "minimum": 0, "maximum": 6},
    "variant": ID,
    "cast_stage": NULLABLE_STAGE,
})
CALIBRATION = _object({
    "component_id": ID,
    "landed": {"type": "boolean"},
    "order": {"type": "integer", "minimum": -10**9, "maximum": 10**9},
    "application_adapter": APPLICATION_ADAPTER,
}, ["component_id"])
SOURCE_SCHEMA = _object({
    "ability_id": ID,
    "version_line": {"enum": ["release", "beta"]},
    "selector": SELECTOR,
    "profile_id": ID,
    "expected_profile_fingerprint": HASH,
    "coverage_manifest_id": ID,
    "expected_coverage_manifest_fingerprint": HASH,
    "scenario_calibrations": {"type": "array", "maxItems": 64, "items": CALIBRATION},
    "status": {"enum": ["confirmed", "pending"]},
    "source": SOURCE,
}, ["ability_id", "version_line", "selector", "profile_id", "expected_profile_fingerprint",
    "scenario_calibrations", "status", "source"])
SOURCES_SCHEMA = {"type": "array", "maxItems": 32, "items": SOURCE_SCHEMA}
MAX_INPUT_BYTES = 128 * 1024


def action_dispatch_source_capabilities():
    return {
        "contract_id": "native-action-dispatch-source-v1",
        "field": "action_dispatch_sources",
        "plan_version": 3,
        "schema": copy.deepcopy(SOURCES_SCHEMA),
        "max_sources": 32,
        "selection": "exact_character_skill_depot_skill_slot_level_constellation_variant_and_cast_stage",
        "materialization": "private_atomic",
        "coverage_manifest": {
            "contract_id": "gameplay-action-manifest-v1",
            "acceptance_fields": ["coverage_manifest_id", "expected_coverage_manifest_fingerprint"],
            "semantics": "独立于派发档案的预期组件、实际发生次数与生命周期拓扑；缺失时覆盖状态为unknown",
        },
        "freshness": "current_runtime_snapshot_only",
        "outputs": ["action_event_profiles", "action_event_bindings", "attack_metadata_sources",
                    "periodic_attack_sources", "prototype_hits"],
        "safety": {
            "partial_materialization": False,
            "manual_reaction_answers": False,
            "cast_stage_without_native_binding": "pending",
            "unrepresentable_scenario_order": "pending",
            "unknown_or_stale_profile": "pending",
        },
        "application_adapter": {
            "contract_id": "application-adapter-v1",
            "declaration_schema": copy.deepcopy(APPLICATION_ADAPTER),
            "scope": "one_exact_fixed_or_periodic_component",
            "accepted_baseline": "sha256(reviewed_emission.application)",
            "mode": ["inherit", "sequence", "no_icd", "no_application", "pending"],
            "attempt_gate": ["inherit", "none", "every_n_landed_hits"],
            "attempt_gate_semantics": (
                "rare pre-ICD declaration that a landed contact does not attempt application; "
                "ordinary every-X-hit ICD belongs in icd_rule.timer_counter, with gate=none"),
            "icd_rule": {"modes": ["inherit", "timer_counter"],
                         "timer_counter_fields": ["hit_interval", "reset_seconds"],
                         "first_hit": 1,
                         "semantics": "timer OR landed-and-attempted hit counter"},
            "projection": "private_exact_component_only",
            "fixed_attempt_gate": ["inherit", "none"],
            "audit": ["base_rule", "effective_rule", "adapter_fingerprint",
                      "database_identity", "identity_divergence",
                      "base_icd_rule_fingerprint", "base_icd_rule",
                      "effective_icd_rule"],
        },
    }


def validate_action_dispatch_sources(plan):
    from .schema import PlanError

    if "action_dispatch_sources" not in plan:
        return
    if plan.get("plan_version") != 3:
        raise PlanError("动作派发来源只适用于原生v3", "action_dispatch_source_schema")
    rows = plan["action_dispatch_sources"]
    try:
        raw = json.dumps(rows, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode()
        if len(raw) > MAX_INPUT_BYTES:
            raise ValueError("动作派发来源超过128KiB")
        errors = _check(rows, SOURCES_SCHEMA, "action_dispatch_sources")
    except (TypeError, ValueError, OverflowError, RecursionError) as exc:
        raise PlanError(str(exc), "action_dispatch_source_schema") from exc
    if errors:
        raise PlanError("动作派发来源结构不合法", "action_dispatch_source_schema", errors[:128])
    abilities = [row["ability_id"] for row in rows]
    if len(abilities) != len(set(abilities)):
        raise PlanError("每个能力最多声明一项动作派发来源", "action_dispatch_source_schema")
    for index, row in enumerate(rows):
        if ("coverage_manifest_id" in row) != ("expected_coverage_manifest_fingerprint" in row):
            raise PlanError("游戏组件清单身份与接受指纹必须同时提供",
                            "action_dispatch_source_schema",
                            {"path": f"action_dispatch_sources[{index}]"})
        calibrations = row["scenario_calibrations"]
        components = [item["component_id"] for item in calibrations]
        if len(components) != len(set(components)):
            raise PlanError("同一派发来源的场景组件校准不可重复", "action_dispatch_source_schema",
                            {"path": f"action_dispatch_sources[{index}].scenario_calibrations"})
        if any(set(item) == {"component_id"} for item in calibrations):
            raise PlanError("场景校准必须至少声明landed、order或application_adapter", "action_dispatch_source_schema",
                            {"path": f"action_dispatch_sources[{index}].scenario_calibrations"})
        for calibration_index, item in enumerate(calibrations):
            adapter = item.get("application_adapter")
            if adapter is None:
                continue
            path = (f"action_dispatch_sources[{index}].scenario_calibrations"
                    f"[{calibration_index}].application_adapter")
            gate = adapter["attempt_gate"]
            icd_rule = adapter.get("icd_rule") or {
                "mode": "inherit", "accepted_base_rule_fingerprint": None,
                "hit_interval": None, "reset_seconds": None}
            if gate["mode"] == "every_n_landed_hits":
                if (gate["period_hits"] is None or gate["first_attempt_ordinal"] is None
                        or gate["first_attempt_ordinal"] > gate["period_hits"]):
                    raise PlanError("周期附着门控需要有效period_hits，且首次位置不能超过周期",
                                    "action_dispatch_source_schema", {"path": path + ".attempt_gate"})
            elif gate["period_hits"] is not None or gate["first_attempt_ordinal"] is not None:
                raise PlanError("inherit/none附着门控不得携带节律参数", "action_dispatch_source_schema",
                                {"path": path + ".attempt_gate"})
            if icd_rule["mode"] == "inherit":
                if any(icd_rule[field] is not None for field in (
                        "accepted_base_rule_fingerprint", "hit_interval", "reset_seconds")):
                    raise PlanError("inherit ICD规则适配不得携带覆盖参数",
                                    "action_dispatch_source_schema",
                                    {"path": path + ".icd_rule"})
            elif (icd_rule["accepted_base_rule_fingerprint"] is None
                  or icd_rule["hit_interval"] is None):
                raise PlanError("timer_counter适配必须接受新鲜数据库规则并声明命中周期",
                                "action_dispatch_source_schema", {"path": path + ".icd_rule"})
            reset = icd_rule["reset_seconds"]
            if (reset is not None and (
                    type(reset) not in (int, float) or not math.isfinite(reset)
                    or not 0 < reset <= 10**9
                    or (Decimal(str(reset)) * 1_000_000)
                    != (Decimal(str(reset)) * 1_000_000).to_integral_value())):
                raise PlanError("timer_counter重置时间必须是有限正数并精确到微秒",
                                "action_dispatch_source_schema", {"path": path + ".icd_rule"})
            mode, counter_tag = adapter["mode"], adapter["counter_tag"]
            if mode == "sequence" and counter_tag is None:
                raise PlanError("sequence适配必须引用一个计数标签", "action_dispatch_source_schema",
                                {"path": path + ".counter_tag"})
            if mode != "sequence" and counter_tag is not None:
                raise PlanError("只有sequence适配可以覆盖计数标签", "action_dispatch_source_schema",
                                {"path": path + ".counter_tag"})
            verification = adapter["verification"]
            modifies_mode = mode not in ("inherit", "pending")
            modifies_gate = gate["mode"] != "inherit"
            modifies_icd_rule = icd_rule["mode"] != "inherit"
            if (modifies_mode or modifies_gate or modifies_icd_rule) and verification is None:
                raise PlanError("修改附着模式、尝试门控或ICD规则必须提供逐组件结构化证据", "action_dispatch_source_schema",
                                {"path": path + ".verification"})
            if not modifies_mode and not modifies_gate and not modifies_icd_rule and verification is not None:
                raise PlanError("纯inherit适配不得替换受审基线证据", "action_dispatch_source_schema",
                                {"path": path + ".verification"})
            confirmed_fields = set((verification or {}).get("confirmed_fields", []))
            if modifies_gate and "attempt_gate" not in confirmed_fields:
                raise PlanError("尝试门控适配证据必须明确确认attempt_gate", "action_dispatch_source_schema",
                                {"path": path + ".verification.confirmed_fields"})
            if modifies_icd_rule and "icd_rule" not in confirmed_fields:
                raise PlanError("ICD规则适配证据必须明确确认icd_rule",
                                "action_dispatch_source_schema",
                                {"path": path + ".verification.confirmed_fields"})
            if adapter["status"] == "confirmed" and not adapter["source"].strip():
                raise PlanError("已确认附着适配必须说明采用依据", "action_dispatch_source_schema",
                                {"path": path + ".source"})
        if row["status"] == "confirmed" and not row["source"].strip():
            raise PlanError("已确认动作派发来源必须说明采用依据", "action_dispatch_source_schema")
