"""Finite elemental state machine consumed by the existing causal event queue.

Units here are explicitly supplied GU, never an interpretation of an unreviewed
upstream elementDurability. Unknown semantics stop authoritative calculation.
"""
from __future__ import annotations

import copy
import math
from decimal import Decimal

from genshin_engine.elemental_aura_kernel import (
    advance_elemental_state,
    prune_recent_elemental_applications,
    record_recent_elemental_application,
)
from genshin_engine.elemental_bloom_core import BloomCoreState
from genshin_engine.elemental_crystallize import CrystallizeState, CrystallizeWorldState
from genshin_engine.elemental_icd_kernel import admit, observe_icd, rule_signature
from genshin_engine.elemental_multi_contact import MultiAuraContact
from genshin_engine.elemental_multi_resolver import apply_multi_contact
from genshin_engine.elemental_schema import (
    DYNAMIC_SWIRL_RULESET,
    ELEMENTAL_SCHEMA,
    ELEMENTS,
    FREEZE_RULESET,
    MAX_ICD_SEQUENCE_LENGTH,
    MULTI_AURA_ELEMENTS,
    MULTI_AURA_RULESET,
    ORDINARY_SWIRL_RULESETS,
    REFERENCE_CONTRACT,
    SWIRL_RULESETS,
)
from genshin_engine.rotation_port import (
    TICKS,
)
from genshin_engine.rotation_port import (
    collect_schema_errors as _schema_errors,
)
from genshin_engine.rotation_port import (
    rotation_issue as _issue,
)
from genshin_engine.special_reaction_actual import (
    REVIEWED_SPECIAL_REACTION_PROFILES,
    SpecialReactionPending,
    SpecialReactionRuntime,
)

# Evidence and scope: docs/design/elemental-runtime-v1.md.
_REACTIONS = {
    ("hydro", "pyro"): ("vaporize", 1.5, .5),
    ("pyro", "hydro"): ("vaporize", 2., 2.),
    ("cryo", "pyro"): ("melt", 2., 2.),
    ("pyro", "cryo"): ("melt", 1.5, .5),
}
_TAX = .8
_ICD_TIME = 2_500_000
_EPS = 1e-12

# A world coordinator may replace an ordinary proposal with a special
# reaction, but routing must never become an opportunity to rewrite the
# application that produced the proposal.  Only the reaction identity and
# commit markers are transformations.  Every other proposal field is causal
# input and therefore immutable; special-reaction output may only append the
# reviewed audit metadata below.
_WORLD_SPECIAL_ROUTE_TRANSFORM_FIELDS = frozenset({
    "id", "type", "commit_state", "route",
})
_WORLD_SPECIAL_ROUTE_METADATA_FIELDS = frozenset({
    "initial_gauge_commit_policy",
    "mechanics_source",
    "ordinary_damage_suppressed",
    "ordinary_reaction_id",
    "ordinary_side_effects_committed",
    "packet_child_order",
    "profile",
    "qualification",
    "reaction_trigger_tags",
    "replaces",
    "role_provenance",
    "time_tick",
})
_WORLD_REACTION_ROUTE_BINDING_FIELDS = frozenset({
    "expected_proposal", "replacement", "decision",
})
_WORLD_SPECIAL_DECISION_FIELDS = frozenset({
    "type",
    "event_id",
    "t",
    "time_tick",
    "ordinary_reaction_id",
    "ordinary_reaction_type",
    "special_reaction_type",
    "decision",
    "reason",
    "status",
    "profile",
})


def _world_special_replacement_drift(proposal, replacement):
    """Return immutable proposal drift and unreviewed special metadata."""
    changed = sorted(
        field for field, value in proposal.items()
        if field not in _WORLD_SPECIAL_ROUTE_TRANSFORM_FIELDS
        and (field not in replacement or replacement[field] != value)
    )
    unexpected = sorted(
        set(replacement)
        - set(proposal)
        - _WORLD_SPECIAL_ROUTE_TRANSFORM_FIELDS
        - _WORLD_SPECIAL_ROUTE_METADATA_FIELDS
    )
    return changed, unexpected


def _world_route_decision_drift(proposal, replacement, decision, time_tick):
    """Return any mismatch between one route and its producer decision.

    ``SpecialReactionRuntime._decision`` is the source contract.  Keeping the
    comparison here explicit prevents an otherwise unused audit object from
    being attached to an unrelated proposal or replacement during the world
    commit replay.
    """
    route = replacement.get("route")
    if route == "ordinary":
        return {
            "missing_fields": [],
            "unexpected_fields": [],
            "changed_fields": ([] if decision is None else ["decision"]),
        }
    if route != "special" or not isinstance(decision, dict):
        return {
            "missing_fields": sorted(_WORLD_SPECIAL_DECISION_FIELDS),
            "unexpected_fields": [],
            "changed_fields": ["decision"],
        }

    expected_reason = (
        "explicit_switch_packet_and_present_capability"
        if "packet_child_order" in replacement
        else "explicit_switch_and_present_capability"
    )
    expected = {
        "type": "special_reaction_decision",
        "event_id": str(proposal.get("id"))+":special-decision",
        "t": time_tick / TICKS,
        "time_tick": time_tick,
        "ordinary_reaction_id": proposal.get("id"),
        "ordinary_reaction_type": proposal.get("type"),
        "special_reaction_type": replacement.get("type"),
        "decision": "replace",
        "reason": expected_reason,
        "status": "executed",
        "profile": copy.deepcopy(replacement.get("profile")),
    }
    missing = sorted(_WORLD_SPECIAL_DECISION_FIELDS-set(decision))
    unexpected = sorted(set(decision)-_WORLD_SPECIAL_DECISION_FIELDS)
    changed = sorted(
        field for field in _WORLD_SPECIAL_DECISION_FIELDS & set(decision)
        if type(decision[field]) is not type(expected[field])
        or decision[field] != expected[field]
    )
    expected_special_id = (
        str(proposal.get("id"))+":special:"+str(replacement.get("type"))
    )
    if replacement.get("id") != expected_special_id:
        changed.append("replacement.id")
    if ("time_tick" in replacement
            and (type(replacement["time_tick"]) is not int
                 or replacement["time_tick"] != time_tick)):
        changed.append("replacement.time_tick")
    return {
        "missing_fields": missing,
        "unexpected_fields": unexpected,
        "changed_fields": sorted(set(changed)),
    }


class ElementalPending(ValueError):
    def __init__(self, code, message, event_id, details=None):
        super().__init__(message)
        self.issue = _issue(code, message, event_id, severity="pending", **(details or {}))


def _icd_identity(icd):
    """Return (counter tag, rule group, legacy input).

    ``group`` is intentionally only a compatibility projection.  Keeping the
    projection in one helper prevents new runtime code from silently restoring
    the old coupling between counter identity and rule provenance.
    """
    if "counter_tag" in icd or "rule_group" in icd:
        return icd.get("counter_tag"), icd.get("rule_group"), False
    group = icd.get("group")
    return group, group, True


def _icd_signature(icd):
    if icd.get("kind") == "standard":
        return _ICD_TIME, ("timer_counter", 3)
    if icd.get("kind") == "sequence":
        reset = int(Decimal(str(icd["reset_seconds"])) * TICKS)
        return reset, tuple(icd["sequence"])
    if icd.get("kind") == "timer_counter":
        reset = int(Decimal(str(icd["reset_seconds"])) * TICKS)
        return reset, ("timer_counter", icd["hit_interval"])
    return None


def _icd_signature_audit(signature):
    """Return the normalized rule signature in JSON/audit friendly form."""
    reset_microseconds, rule = signature
    result = {
        "reset_microseconds": reset_microseconds,
        "reset_seconds": float(Decimal(reset_microseconds) / TICKS),
    }
    if rule and rule[0] == "timer_counter":
        result.update(kind="timer_counter", hit_interval=rule[1], first_hit=1)
    else:
        result.update(kind="sequence", sequence=list(rule))
    return result


def validate_elemental_model(model):
    issues = []
    try:
        _schema_errors(model, ELEMENTAL_SCHEMA, "mechanics_model.elemental_model", issues)
    except (TypeError, ValueError, OverflowError, RecursionError):
        issues.append(_issue("schema", "元素模型必须包含有限且可校验的 JSON 数值。", "mechanics_model.elemental_model"))
    if issues:
        return issues
    freeze = model["ruleset"] == FREEZE_RULESET
    multi = model["ruleset"] == MULTI_AURA_RULESET
    swirl = model["ruleset"] in SWIRL_RULESETS
    if swirl != ("swirl_damage" in model):
        issues.append(_issue("missing_information" if swirl else "unused_field",
                             "v2必须明确swirl_damage，v1不能填写该字段。", "elemental_model.swirl_damage"))
    if "swirl_damage" in model:
        damage = model["swirl_damage"]
        icd = damage["icd"]
        path = "elemental_model.swirl_damage"
        if not damage["source"].strip():
            issues.append(_issue("missing_information", "扩散伤害缺少适用范围与来源证据。", path))
        if icd["kind"] == "unknown":
            if set(icd) != {"kind"}:
                issues.append(_issue("unused_field", "未知伤害ICD不能同时填写序列或计时。", path))
        else:
            sequence, reset = icd.get("sequence", []), icd.get("reset_seconds", 0)
            if not 1 <= len(sequence) <= MAX_ICD_SEQUENCE_LENGTH:
                issues.append(_issue("icd_sequence", "伤害ICD必须明确1–64项二元序列。", path))
            if reset <= 0:
                issues.append(_issue("icd_reset", "伤害ICD必须明确有限正数计时。", path))
            elif (Decimal(str(reset)) * TICKS) != (Decimal(str(reset)) * TICKS).to_integral_value():
                issues.append(_issue("time_precision", "伤害ICD时间必须精确到微秒。", path))
    profiles = model["profiles"]
    named_rules = {}
    if len(profiles) > 256 or len({p["id"] for p in profiles}) != len(profiles):
        issues.append(_issue("duplicate_id", "附着档案 ID 必须唯一且最多 256 项。", "elemental_model.profiles"))
    for profile in profiles:
        path = "elemental_model.profiles." + profile["id"]
        if profile["application"] == "apply":
            if "gauge_units" not in profile or "icd" not in profile:
                issues.append(_issue("missing_information", "附着档案需要明确 GU 和 ICD，不能推测。", path))
        elif "gauge_units" in profile or "icd" in profile:
            issues.append(_issue("unused_field", "无附着或未知档案不能同时填写 GU/ICD。", path))
        icd = profile.get("icd", {})
        kind = icd.get("kind")
        counted = kind in ("standard", "sequence", "timer_counter")
        legacy = "group" in icd
        explicit = "counter_tag" in icd or "rule_group" in icd
        if counted:
            if legacy and explicit:
                issues.append(_issue("icd_identity", "新式计数标签/规则组不能与旧group混用。", path))
            elif explicit and not all(name in icd for name in ("counter_tag", "rule_group")):
                issues.append(_issue("icd_identity", "新式 ICD 必须同时声明counter_tag与rule_group。", path))
            elif not legacy and not explicit:
                issues.append(_issue("icd_group", "standard、timer-counter或有限sequence ICD必须声明counter_tag与rule_group；旧输入可仅用group。", path))
        elif legacy or explicit:
            issues.append(_issue("icd_group", "无ICD或未知ICD不能填写计数标签、规则组或旧group。", path))
        for field in ("group", "counter_tag", "rule_group"):
            if field in icd and not icd[field].strip():
                issues.append(_issue("icd_group", f"{field}不能为空白。", path))
        signature = None
        if kind == "sequence":
            sequence = icd.get("sequence", [])
            reset = icd.get("reset_seconds", 0)
            if not 1 <= len(sequence) <= MAX_ICD_SEQUENCE_LENGTH:
                issues.append(_issue("icd_sequence", "ICD 序列必须为 1–64 项二元整数。", path))
            if reset <= 0:
                issues.append(_issue("icd_reset", "序列 ICD 必须声明有限正数 reset_seconds。", path))
            else:
                ticks = Decimal(str(reset)) * TICKS
                if ticks != ticks.to_integral_value():
                    issues.append(_issue("time_precision", "ICD 重置时间必须精确到微秒。", path))
                elif 1 <= len(sequence) <= MAX_ICD_SEQUENCE_LENGTH:
                    signature = (int(ticks), tuple(sequence))
            if "hit_interval" in icd:
                issues.append(_issue("unused_field", "sequence ICD不能填写hit_interval。", path))
        elif kind == "timer_counter":
            reset = icd.get("reset_seconds", 0)
            interval = icd.get("hit_interval")
            if reset <= 0:
                issues.append(_issue("icd_reset", "timer_counter ICD必须声明有限正数 reset_seconds。", path))
            else:
                ticks = Decimal(str(reset)) * TICKS
                if ticks != ticks.to_integral_value():
                    issues.append(_issue("time_precision", "ICD 重置时间必须精确到微秒。", path))
                elif type(interval) is int and 1 <= interval <= 64:
                    signature = (int(ticks), ("timer_counter", interval))
            if type(interval) is not int or not 1 <= interval <= 64:
                issues.append(_issue("icd_hit_interval", "timer_counter命中周期必须是1–64整数。", path))
            if "sequence" in icd:
                issues.append(_issue("unused_field", "timer_counter ICD不能填写有限sequence。", path))
        else:
            if "reset_seconds" in icd or "sequence" in icd or "hit_interval" in icd:
                issues.append(_issue("unused_field", "只有 sequence/timer_counter ICD可填写规则参数。", path))
            if kind == "standard":
                signature = (_ICD_TIME, ("timer_counter", 3))
        counter_tag, rule_group, _ = _icd_identity(icd)
        if signature is not None and counter_tag and rule_group:
            named_rules.setdefault(rule_group, []).append({
                "profile_id": profile["id"],
                "profile_path": path,
                "rule_group": rule_group,
                "signature": _icd_signature_audit(signature),
                "declared_source": profile["source"],
            })
        if not profile["source"].strip():
            issues.append(_issue("missing_information", "附着档案缺少证据。", path))
    for rule_group, bindings in named_rules.items():
        signatures = {
            (binding["signature"]["reset_microseconds"], binding["signature"].get("kind"),
             tuple(binding["signature"].get("sequence", [])),
             binding["signature"].get("hit_interval"))
            for binding in bindings
        }
        if len(signatures) <= 1:
            continue
        first = bindings[0]
        conflicting = next(binding for binding in bindings if binding["signature"] != first["signature"])
        issues.append(_issue(
            "icd_rule_group_conflict", "同一规则组对应了不同的规范化ICD执行签名。",
            conflicting["profile_path"], rule_group=rule_group,
            profile_id=conflicting["profile_id"], conflicting_profile_id=first["profile_id"],
            signature=copy.deepcopy(conflicting["signature"]),
            conflicting_signature=copy.deepcopy(first["signature"]),
            conflicting_path=first["profile_path"], bindings=copy.deepcopy(bindings),
            source_paths=[binding["profile_path"] for binding in bindings]))
    target = model["target"]
    if freeze:
        for field in ("initial_state", "freeze"):
            if field not in target:
                issues.append(_issue("missing_information", "冻结参考模型需要明确 " + field, "elemental_model.target."+field, severity="pending"))
        if "initial_aura" in target:
            issues.append(_issue("duplicate_state", "参考模型使用initial_state，不能同时交付initial_aura。", "elemental_model.target"))
        acceptance = model.get("reference_acceptance", {})
        if (not acceptance.get("acknowledged") or not acceptance.get("source", "").strip()
                or any(acceptance.get(k) != REFERENCE_CONTRACT[k] for k in ("contract_id", "contract_fingerprint"))):
            issues.append(_issue("reference_not_accepted", "请明确确认当前版本的实验参考模型假设。", "elemental_model.reference_acceptance", severity="pending"))
        freezing = target.get("freeze", {})
        initial = target.get("initial_state", {})
        if (freezing.get("freezable") is not True or freezing.get("resistance") != 0
                or freezing.get("status") == "pending" or not freezing.get("source", "").strip()):
            issues.append(_issue("unsupported_freeze_target", "本片仅支持明确可冻结、零抗性的普通目标。", "elemental_model.target.freeze", severity="pending"))
        if target["knowledge"] != "known" or target["status"] == "pending" or initial.get("status") == "pending" or not initial.get("source", "").strip():
            issues.append(_issue("unknown_initial_state", "参考模型初态须明确且有来源。", "elemental_model.target.initial_state", severity="pending"))
        aura = initial.get("ordinary_aura")
        contacts = model.get("contact_profiles", [])
        if len(contacts) > 256 or len({c["id"] for c in contacts}) != len(contacts):
            issues.append(_issue("duplicate_id", "接触档案必须唯一且最多256项。", "elemental_model.contact_profiles"))
    elif multi:
        if "initial_auras" not in target:
            issues.append(_issue("missing_information", "v4多元素底需要显式initial_auras（可为空数组）。",
                                 "elemental_model.target.initial_auras"))
        if set(target) & {"initial_aura", "initial_state"}:
            issues.append(_issue("duplicate_state", "v4使用initial_auras，不能同时交付旧单底或冻结参考初态。",
                                 "elemental_model.target"))
        if set(model) & {"contact_profiles", "reference_acceptance"}:
            issues.append(_issue("unused_field", "冻结参考的接触/接纳字段不属v4多底规则。",
                                 "elemental_model"))
        freezing = target.get("freeze")
        if not freezing:
            issues.append(_issue("missing_information", "v4需要独立明确目标是否可冻结。",
                                 "elemental_model.target.freeze"))
        else:
            if (freezing.get("status") == "pending" or not freezing.get("source", "").strip()
                    or isinstance(freezing.get("resistance"), bool)
                    or not isinstance(freezing.get("resistance"), (int, float))
                    or not math.isfinite(freezing["resistance"])):
                issues.append(_issue("unknown_freeze_target", "v4的目标冻结类型和证据必须确定。",
                                     "elemental_model.target.freeze", severity="pending"))
            elif freezing.get("freezable") is True and freezing["resistance"] != 0:
                issues.append(_issue("unsupported_freeze_resistance", "v4可冻结对象尚未实现非零冻结抗性。",
                                     "elemental_model.target.freeze", severity="pending"))
            if "mode" in freezing and "unfreezable_resolution" in freezing:
                issues.append(_issue("freeze_resolution_duplicate", "旧mode迁移字段不能与unfreezable_resolution同时填写。",
                                     "elemental_model.target.freeze"))
            if freezing.get("freezable") is True and set(freezing) & {"mode", "unfreezable_resolution"}:
                issues.append(_issue("unused_field", "不可冻结处理不属于可冻结对象；真实Frozen状态必须走冻结模型。",
                                     "elemental_model.target.freeze"))
        if target["knowledge"] != "known" or target["status"] == "pending":
            issues.append(_issue("unknown_initial_state", "v4初始多底必须明确且有来源。",
                                 "elemental_model.target", severity="pending"))
        auras = target.get("initial_auras", [])
        elements = [item["element"] for item in auras]
        if len(elements) != len(set(elements)):
            issues.append(_issue("duplicate_aura", "v4每种元素最多一层初始底。",
                                 "elemental_model.target.initial_auras"))
        if len(elements) > 1 and set(elements) not in ({"hydro", "cryo"}, {"hydro", "electro"}):
            issues.append(_issue("unsupported_coexistence", "v4本轮只接纳水冰或水雷共存初态；其他多底组合待实现。",
                                 "elemental_model.target.initial_auras", severity="pending"))
        if set(elements) == {"hydro", "cryo"} and freezing:
            if freezing.get("freezable") is True:
                issues.append(_issue("freezable_frozen_state_required",
                                     "可冻结对象的水冰共存初态缺少接触先后，不能据此重建Frozen量与底层余量。",
                                     "elemental_model.target.initial_auras", severity="pending"))
            else:
                resolution = freezing.get("unfreezable_resolution", freezing.get("mode", "coexist"))
                if resolution != "coexist":
                    issues.append(_issue("freeze_resolution_conflict",
                                         "mutual_cancel的不可冻结目标不能以水冰共存作为初态。",
                                         "elemental_model.target.initial_auras"))
        for index, item in enumerate(auras):
            if (item["element"] not in MULTI_AURA_ELEMENTS
                    or not all(not isinstance(item[field], bool) and math.isfinite(item[field]) and item[field] > 0
                               for field in ("remaining_gu", "decay_gu_per_second"))
                    or item["remaining_gu"] > 3.2):
                issues.append(_issue("value", "v4初始底元素、底量或衰减超出已实现边界。",
                                     f"elemental_model.target.initial_auras.{index}"))
        aura = None
    else:
        if "initial_aura" not in target:
            issues.append(_issue("missing_information", "旧规则需要initial_aura。", "elemental_model.target"))
        if set(target) & {"initial_state", "freeze"} or set(model) & {"contact_profiles", "reference_acceptance"}:
            issues.append(_issue("unused_field", "冻结字段只能用于冻结参考规则。", "elemental_model"))
        aura = target.get("initial_aura")
        if target["knowledge"] == "unknown" and aura is not None:
            issues.append(_issue("unknown_state", "未知初态不能同时宣称已知元素底。", "elemental_model.target"))
    if aura and (not all(not isinstance(aura[k], bool) and math.isfinite(aura[k]) and aura[k] > 0 for k in ("remaining_gu", "decay_gu_per_second"))
                 or (swirl and aura["remaining_gu"] > 3.2)):
        issues.append(_issue("value", "初始普通底量/衰减必须有限，底量不得超过本规则支持的3.2 GU。", "elemental_model.target"))
    return issues


class ElementalRuntime:
    """Single-target state using ``actor + counter_tag + target`` counters.

    Time is integer microseconds.  ``rule_group`` selects the normalized
    sequence but never participates in counter identity; casts and action type
    names never create or merge counters implicitly.
    """

    def __init__(self, model, *, special_runtime=None, crystallize_world=None,
                 bloom_core_state=None):
        self.model = copy.deepcopy(model)
        self.target = model["target"]["id"]
        self.profiles = {p["id"]: p for p in self.model["profiles"]}
        # Rule lookup is intentionally independent from the shared counter
        # identity.  Validation guarantees that a named rule group has one
        # normalized signature; the runtime then executes that rule by name.
        self.icd_rules = {}
        for profile in self.profiles.values():
            icd = profile.get("icd", {})
            if icd.get("kind") in ("standard", "sequence", "timer_counter"):
                _, rule_group, _ = _icd_identity(icd)
                self.icd_rules.setdefault(rule_group, _icd_signature(icd))
        self.freeze_state = None
        self.multi_state = None
        self.crystallize_state = None
        if (crystallize_world is not None
                and not isinstance(crystallize_world, CrystallizeWorldState)):
            raise ValueError("crystallize_world必须是CrystallizeWorldState或None。")
        if crystallize_world is not None and model["ruleset"] != MULTI_AURA_RULESET:
            raise ValueError("共享结晶战场只能接入gauge-multi-aura-v4。")
        if special_runtime is not None and not isinstance(
                special_runtime, SpecialReactionRuntime):
            raise ValueError("special_runtime必须是SpecialReactionRuntime或None。")
        if special_runtime is not None and model["ruleset"] != MULTI_AURA_RULESET:
            raise ValueError("星月反应运行时只能接入gauge-multi-aura-v4。")
        if (bloom_core_state is not None
                and not isinstance(bloom_core_state, BloomCoreState)):
            raise ValueError("bloom_core_state必须是BloomCoreState或None。")
        if bloom_core_state is not None and model["ruleset"] != MULTI_AURA_RULESET:
            raise ValueError("共享草原核战场只能接入gauge-multi-aura-v4。")
        self.special_runtime = special_runtime
        lunar_bloom_enabled = bool(
            special_runtime
            and (special_runtime.model.get("replacements", {}).get("lunar_bloom") or {}).get("enabled"))
        self.bloom_core_state = (
            bloom_core_state
            if bloom_core_state is not None
            else (BloomCoreState() if lunar_bloom_enabled else None)
        )
        self.generated_events = []
        if model["ruleset"] == FREEZE_RULESET:
            from genshin_engine.elemental_freeze import FreezeState
            self.freeze_state = FreezeState(model["target"]["initial_state"])
        elif model["ruleset"] == MULTI_AURA_RULESET:
            from genshin_engine.elemental_multiaura import MultiAuraState
            freeze = model["target"].get("freeze", {})
            freezable = freeze.get("freezable")
            legacy_mode = freeze.get("mode")
            if freezable:
                resolution, resolution_origin = None, "not_applicable"
            elif "unfreezable_resolution" in freeze:
                resolution = freeze["unfreezable_resolution"]
                resolution_origin = "manual_override"
            elif legacy_mode is not None:
                resolution = legacy_mode
                resolution_origin = "legacy_mode_migration"
            else:
                resolution = "coexist"
                resolution_origin = "default_scenario"
            self.multi_state = MultiAuraState(model["target"].get("initial_auras", ()),
                                              freezable=freezable,
                                              unfreezable_resolution=resolution,
                                              resolution_origin=resolution_origin,
                                              legacy_mode=legacy_mode)
            self.crystallize_state = CrystallizeState(
                self.target, world=crystallize_world)
        self.aura = copy.deepcopy(model["target"].get("initial_aura"))
        self.time = 0
        self.recent_elemental_applications = []
        if model["ruleset"] == MULTI_AURA_RULESET:
            for initial in model["target"].get("initial_auras", ()):
                owners = list(initial.get("contributor_ids", ()))
                if not owners and initial.get("source_owner"):
                    owners = [initial["source_owner"]]
                for owner in owners:
                    self.recent_elemental_applications.append({
                        "time_tick": 0,
                        "element": initial["element"],
                        "character": owner,
                        "target_id": self.target,
                        "area_id": "battlefield.primary",
                        "source": "declared_initial_aura_at_rotation_start",
                    })
        self.icd = {}
        self.counter_bindings = {}
        self.damage_icd = {}
        self.trace = []
        self.window_state = None

    def _advance_elemental_state(self, time):
        return advance_elemental_state(self, time, epsilon=_EPS)

    def prune_recent_elemental_applications(self):
        return self._prune_recent_elemental_applications()

    def _prune_recent_elemental_applications(self):
        return prune_recent_elemental_applications(self)

    def _record_recent_elemental_application(self, row):
        return record_recent_elemental_application(self, row)

    def _lunar_electro_aura_snapshot(self):
        if not self.multi_state:
            raise ElementalPending(
                "lunar_electro_multi_aura_required",
                "月感电跳数需要gauge-multi-aura-v4的实时水雷分层状态。",
                f"{self.target}:lunar-electro:aura")
        components = {}
        for element in ("hydro", "electro"):
            aura = self.multi_state.auras.get(element)
            if aura is None:
                continue
            allocations = self.multi_state.contributor_allocations(element)
            if allocations is None:
                raise ElementalPending(
                    "lunar_electro_owner_residual_unproven",
                    "月感电需要按元素与提供者保留实时剩余元素量；历史附着名单不能替代残量账。",
                    f"{self.target}:lunar-electro:aura",
                    {"element": element,
                     "owner_residual_status": aura.get("owner_residual_status")})
            contributors = [row["owner"] for row in allocations
                            if row["remaining_gu"] > _EPS]
            components[element] = {
                "remaining_gu": aura["remaining_gu"],
                "contributor_ids": contributors,
                "contributor_allocations": allocations,
                "owner_residual_status": aura.get("owner_residual_status"),
                "decay_gu_per_second": aura["decay_gu_per_second"],
            }
        return {"knowledge": self.model["target"]["knowledge"],
                "owner_residual_contract": "same-element-owner-residual-max-v1",
                "sample_time_tick": self.time,
                "components": components}

    def _lunar_electro_aura_snapshot_at(self, time_tick):
        """Return the live owner-residual ledger at one world boundary.

        The world coordinator advances every target to the same boundary before
        it asks the area thundercloud to admit any tick.  Requiring an exact
        clock match here prevents a caller from reusing an earlier or later
        target snapshot as if it were live state.
        """
        if type(time_tick) is not int or time_tick != self.time:
            raise ElementalPending(
                "lunar_electro_target_clock_mismatch",
                "月感电只能读取与当前目标时钟完全一致的实时水雷残量。",
                f"{self.target}:lunar-electro:aura",
                {"requested_time_tick": time_tick,
                 "target_time_tick": self.time},
            )
        return self._lunar_electro_aura_snapshot()

    def _commit_lunar_electro_tick_receipt(self, tick, admitted):
        """Commit one already-previewed thundercloud admission to this target.

        ``SpecialReactionRuntime`` owns tick admission and contributor choice;
        this method owns only the target-local Hydro/Electro residual commit.
        Both the legacy single-target path and the world transaction use this
        seam so their gauge semantics cannot drift apart.
        """
        if (not isinstance(tick, dict)
                or tick.get("type") != "special_reaction_tick_request"
                or tick.get("reaction_type") != "lunar_electro"):
            raise ValueError("只能提交月感电状态机生成的跳数请求。")
        if not isinstance(admitted, dict):
            raise TypeError("月感电跳数预演回执必须是对象。")
        if tick.get("target_id") != self.target:
            raise ElementalPending(
                "lunar_electro_target_unbound",
                "月感电雷暴云目标没有绑定到当前元素运行时。",
                tick.get("event_id", f"{self.target}:lunar-electro:tick"),
                {"tick_target_id": tick.get("target_id"),
                 "elemental_target_id": self.target},
            )
        if tick.get("time_tick") != self.time:
            raise ElementalPending(
                "lunar_electro_target_clock_mismatch",
                "月感电跳数时点与目标实时元素状态不一致。",
                tick.get("event_id", f"{self.target}:lunar-electro:tick"),
                {"tick_time_tick": tick.get("time_tick"),
                 "target_time_tick": self.time},
            )

        admission = admitted.get("admission")
        expected_before = admitted.get("aura_before")
        expected_after = admitted.get("aura_after")
        damage = admitted.get("damage_event")
        if (not isinstance(admission, dict)
                or admission.get("target_id") != self.target
                or admission.get("time_tick") != self.time
                or type(admission.get("allowed")) is not bool):
            raise RuntimeError("月感电跳数预演回执与目标或时点不一致。")

        actual_before = self._lunar_electro_aura_snapshot_at(self.time)
        if actual_before != expected_before:
            raise RuntimeError("月感电预演与提交前的实时提供者残量不一致。")

        if admission["allowed"]:
            consume = admission.get("consumed_gu")
            if (not isinstance(consume, dict)
                    or set(consume) != {"hydro", "electro"}):
                raise RuntimeError("月感电允许回执缺少完整水雷消费提案。")
            for element in ("hydro", "electro"):
                actual = self.multi_state.consume(element, consume[element])
                if abs(actual-consume[element]) > _EPS:
                    raise RuntimeError("月感电活底核验与元素状态消费不一致。")
            if damage is None:
                raise RuntimeError("允许的月感电跳数必须提供伤害请求。")
        else:
            consumed = admission.get("consumed_gu")
            if consumed != {"hydro": 0, "electro": 0} or damage is not None:
                raise RuntimeError("拒绝的月感电跳数必须保持零消费且不生成伤害。")

        actual_after = self._lunar_electro_aura_snapshot_at(self.time)
        if actual_after != expected_after:
            raise RuntimeError("月感电提供者残量提案与元素状态原子提交不一致。")
        rows = [copy.deepcopy(admission)]
        if damage is not None:
            rows.append(copy.deepcopy(damage))
        return rows

    def _restore_crystallize_state(self, snapshot):
        """Restore without detaching an explicitly shared battlefield."""
        if self.crystallize_state is None or snapshot is None:
            self.crystallize_state = snapshot
        else:
            self.crystallize_state.restore(snapshot)

    def _restore_bloom_core_state(self, snapshot):
        """Restore without detaching an explicitly shared core battlefield."""
        if self.bloom_core_state is None or snapshot is None:
            self.bloom_core_state = snapshot
        else:
            self.bloom_core_state.restore(snapshot)

    def _transaction_snapshot(self):
        return {
            "time": self.time,
            "aura": copy.deepcopy(self.aura),
            "freeze_state": copy.deepcopy(self.freeze_state),
            "multi_state": copy.deepcopy(self.multi_state),
            "crystallize_state": copy.deepcopy(self.crystallize_state),
            "special_runtime": (self.special_runtime.transaction_snapshot()
                                if self.special_runtime is not None else None),
            "bloom_core_state": copy.deepcopy(self.bloom_core_state),
            "generated_length": len(self.generated_events),
            "recent_elemental_applications": copy.deepcopy(
                self.recent_elemental_applications),
            "trace_length": len(self.trace),
        }

    def _restore_transaction(self, snapshot):
        self.time = snapshot["time"]
        self.aura = snapshot["aura"]
        self.freeze_state = snapshot["freeze_state"]
        self.multi_state = snapshot["multi_state"]
        self._restore_crystallize_state(snapshot["crystallize_state"])
        if self.special_runtime is not None:
            self.special_runtime.restore_transaction(snapshot["special_runtime"])
        self._restore_bloom_core_state(snapshot["bloom_core_state"])
        del self.generated_events[snapshot["generated_length"]:]
        self.recent_elemental_applications = snapshot["recent_elemental_applications"]
        del self.trace[snapshot["trace_length"]:]

    def advance(self, time):
        if type(time) is not int or time < self.time:
            raise ValueError("元素状态不能倒退，改轴需要重新运行。")
        snapshot = self._transaction_snapshot()
        try:
            while True:
                deadlines = []
                if self.special_runtime:
                    deadline = self.special_runtime.next_deadline()
                    if deadline is not None and deadline <= time:
                        deadlines.append(deadline)
                if self.bloom_core_state:
                    deadline = self.bloom_core_state.next_deadline()
                    if deadline is not None and deadline <= time:
                        deadlines.append(deadline)
                boundary = min(deadlines) if deadlines else time
                self._advance_elemental_state(boundary)
                if self.special_runtime:
                    due = self.special_runtime.advance(boundary)
                    for event in due:
                        self.generated_events.append(copy.deepcopy(event))
                        if event.get("type") != "special_reaction_tick_request":
                            continue
                        if event.get("reaction_type") != "lunar_electro":
                            continue
                        if event.get("target_id") != self.target:
                            raise SpecialReactionPending(
                                "lunar_electro_target_unbound",
                                "月感电雷暴云目标没有绑定到当前元素运行时。",
                                event["event_id"],
                                {"tick_target_id": event.get("target_id"),
                                 "elemental_target_id": self.target})
                        admitted = self.special_runtime.admit_lunar_electro_tick(
                            event, self._lunar_electro_aura_snapshot())
                        self.generated_events.extend(
                            self._commit_lunar_electro_tick_receipt(event, admitted))
                if self.bloom_core_state:
                    self.generated_events.extend(copy.deepcopy(
                        self.bloom_core_state.advance(boundary)))
                if boundary == time:
                    break
            return
        except SpecialReactionPending as exc:
            self._restore_transaction(snapshot)
            raise ElementalPending(
                exc.issue["code"], str(exc), exc.issue["event_id"],
                {key: copy.deepcopy(value) for key, value in exc.issue.items()
                 if key not in {"code", "message", "path", "severity"}}) from exc
        except Exception:
            self._restore_transaction(snapshot)
            raise

    def next_state_deadline(self, horizon_ticks=None):
        """Return the next versioned elemental state deadline, if any.

        This is the queue-facing extension point.  Callers do not need to know
        whether the active deadline belongs to the legacy reference ruleset or
        the v4 multi-aura Frozen child state.
        """
        candidates = []
        if self.freeze_state:
            deadline = self.freeze_state.next_deadline(horizon_ticks)
            if deadline:
                candidates.append({**deadline, "state_kind": "freeze_reference"})
        if self.multi_state:
            deadline = self.multi_state.next_deadline(horizon_ticks)
            if deadline:
                candidates.append({**deadline, "state_kind": "multi_aura_freeze"})
            crystallize_deadline = self.crystallize_state.next_deadline()
            if (crystallize_deadline is not None
                    and (horizon_ticks is None or crystallize_deadline <= horizon_ticks)):
                candidates.append({
                    "time": crystallize_deadline,
                    "revision": (self.crystallize_state.revision
                                 + self.crystallize_state.world.revision),
                    "reason": "crystallize_state_deadline",
                    "state_kind": "ordinary_crystallize",
                })
        if self.special_runtime:
            deadline = self.special_runtime.next_deadline()
            if deadline is not None and (horizon_ticks is None or deadline <= horizon_ticks):
                candidates.append({"time": deadline, "revision": len(self.special_runtime.trace),
                                   "reason": "special_reaction_deadline",
                                   "state_kind": "special_reaction"})
        if self.bloom_core_state:
            deadline = self.bloom_core_state.next_deadline()
            if deadline is not None and (horizon_ticks is None or deadline <= horizon_ticks):
                candidates.append({"time": deadline, "revision": self.bloom_core_state.revision,
                                   "reason": "bloom_core_expiry",
                                   "state_kind": "bloom_core"})
        return min(candidates, key=lambda row: (row["time"], row["revision"], row["state_kind"])) if candidates else None

    def drain_generated_events(self):
        rows = copy.deepcopy(self.generated_events)
        self.generated_events = []
        return rows

    def pickup_crystallize_shard(self, *, event_id: str, shard_id: str,
                                 character: str, target_id: str, time: int,
                                 source: str) -> dict:
        """Consume one real ordinary shard and expose the resulting shield.

        Time passage is observed first, like an elemental application.  The
        pickup itself is atomic: a missing/expired shard cannot partially
        remove another shard or replace an existing shield.
        """
        if self.model["ruleset"] != MULTI_AURA_RULESET or self.crystallize_state is None:
            raise ElementalPending(
                "crystallize_pickup_runtime_unavailable",
                "结晶晶片拾取需要gauge-multi-aura-v4普通结晶状态机。",
                event_id,
            )
        for name, value in (("event_id", event_id), ("shard_id", shard_id),
                            ("character", character), ("target_id", target_id),
                            ("source", source)):
            if not isinstance(value, str) or not value:
                raise ValueError(f"结晶晶片拾取{name}必须是非空字符串。")
        if target_id != self.target:
            raise ElementalPending(
                "crystallize_pickup_target_mismatch",
                "结晶晶片只能由其所属目标的元素状态机拾取。",
                event_id,
                {"target_id": target_id, "runtime_target_id": self.target},
            )
        self.advance(time)
        state_before = copy.deepcopy(self.crystallize_state)
        shard = next((row for row in self.crystallize_state.world.shards
                      if row["id"] == shard_id), None)
        if shard is not None and shard["target_id"] != target_id:
            raise ElementalPending(
                "crystallize_pickup_shard_target_mismatch",
                "结晶晶片属于其他具体目标，跨目标拾取已拒绝。",
                event_id,
                {"shard_id": shard_id, "shard_target_id": shard["target_id"],
                 "operation_target_id": target_id},
            )
        try:
            receipt = self.crystallize_state.pickup(
                shard_id=shard_id, character=character, time=time)
        except ValueError as exc:
            self._restore_crystallize_state(state_before)
            raise ElementalPending(
                "crystallize_shard_unavailable",
                "结晶晶片不存在、已经失效或已被拾取。",
                event_id,
                {"shard_id": shard_id, "character": character,
                 "target_id": target_id},
            ) from exc
        receipt.update({
            "event_id": event_id,
            "time_tick": time,
            "ruleset": MULTI_AURA_RULESET,
            "source": source,
            "status": "executed",
        })
        self.trace.append(copy.deepcopy(receipt))
        return receipt

    def register_special_application(self, request, *, target_id):
        """Register one reviewed runtime-generated elemental application.

        The mechanics dispatcher calls this before it places the request back
        on the ordinary ``elemental_application`` queue.  This keeps the
        generated Cryo application on the same ICD/reaction transaction path
        as authored applications without allowing arbitrary runtime profiles.
        """
        if not isinstance(request, dict):
            raise TypeError("星月派生附着请求必须是对象。")
        if request.get("type") != "special_reaction_application_request":
            raise ValueError("只能登记星月运行时生成的附着请求。")
        if request.get("status") != "ready_for_elemental_runtime":
            raise ElementalPending(
                "special_application_not_ready",
                "星月派生附着尚未准备进入元素运行时。",
                request.get("event_id", "special_reaction_application_request"))
        if target_id != self.target:
            raise ValueError("星月派生附着选择的目标与元素运行时不一致。")
        declared_target = request.get("target_id")
        if declared_target not in (None, target_id):
            raise ValueError("星月派生附着请求绑定了其他目标。")
        if (declared_target is None
                and (request.get("target_selection_policy") != "event_time_entities_in_area"
                     or not isinstance(request.get("area_id"), str)
                     or not request["area_id"])):
            raise ElementalPending(
                "special_application_target_selection_pending",
                "区域派生附着需要在事件时刻完成目标选择。",
                request.get("event_id", "special_reaction_application_request"))
        reaction_type = request.get("reaction_type")
        if reaction_type != "stellar_swirl":
            raise ValueError("当前受审派生附着仅包含星扩散风旋爆发。")
        reviewed = REVIEWED_SPECIAL_REACTION_PROFILES[reaction_type]
        audit = request.get("profile")
        if (not isinstance(audit, dict)
                or audit.get("profile_id") != reviewed["profile_id"]
                or audit.get("profile_revision") != reviewed["profile_revision"]
                or audit.get("profile_content_hash") != reviewed["profile_content_hash"]
                or audit.get("native_confidence") not in {
                    "reviewed_native", "assumed_override"}):
            raise ElementalPending(
                "special_application_profile_unproven",
                "派生附着没有匹配受审的星扩散运行时档案。",
                request.get("event_id", "special_reaction_application_request"))
        expected_gu = reviewed["parameters"]["ice_application_gu"]
        gauge = request.get("gauge_units")
        if (request.get("element") != "cryo"
                or isinstance(gauge, bool) or not isinstance(gauge, (int, float))
                or not math.isfinite(gauge) or gauge != expected_gu
                or request.get("icd") != {"kind": "none"}
                or request.get("application_order") != "after_damage"
                or request.get("direct_hit") is not False
                or request.get("counts_toward_stellar_superconduct") is not True):
            raise ElementalPending(
                "special_application_contract_mismatch",
                "风旋爆发派生附着与受审的1U冰、伤害后、无ICD契约不一致。",
                request.get("event_id", "special_reaction_application_request"))
        for key in ("event_id", "owner", "action_id", "source"):
            if not isinstance(request.get(key), str) or not request[key]:
                raise ElementalPending(
                    "special_application_causality_missing",
                    "派生附着缺少事件、角色、动作或来源因果。",
                    request.get("event_id", "special_reaction_application_request"),
                    {"missing_field": key})
        profile_id = (
            "@special-application:" + audit["profile_content_hash"][:16]
            + ":cryo:1u:no-icd")
        profile = {
            "id": profile_id,
            "element": "cryo",
            "application": "apply",
            "gauge_units": float(gauge),
            "icd": {"kind": "none"},
            "status": ("confirmed" if audit["native_confidence"] == "reviewed_native"
                       else "assumed"),
            "source": request["source"],
            "generated_by": reaction_type,
            "profile_content_hash": audit["profile_content_hash"],
        }
        old = self.profiles.get(profile_id)
        if old is not None and old != profile:
            raise ValueError("同一派生附着档案ID出现了不一致定义。")
        self.profiles[profile_id] = copy.deepcopy(profile)
        return {
            "profile_id": profile_id,
            "target_id": target_id,
            "landed": True,
            "attempted": True,
            "area_id": request["area_id"],
            "atomic_parent_id": request["event_id"],
            "direct_hit": False,
            "counts_toward_stellar_superconduct": True,
            "generated_application_request_id": request["event_id"],
            "parent_damage_event_id": request.get("parent_damage_event_id"),
            "application_order": request["application_order"],
        }

    def state(self):
        def icd_state(key, value):
            row = {"actor": key[0], "group": key[1], "counter_tag": key[1],
                   "rule_group": value["rule_group"], "target_id": key[2],
                   "hits": value["hits"], "attempts": value["hits"],
                   "next_sequence_index": value["hits"],
                   "sequence_length": value["sequence_length"],
                   "anchor_t": value["anchor"] / TICKS,
                   "reset_at": (value["anchor"] + value["reset_ticks"]) / TICKS,
                   "reset_seconds": value["reset_ticks"] / TICKS,
                   "timer_remaining": max(
                       0, value["anchor"] + value["reset_ticks"] - self.time) / TICKS}
            rule_groups = value.get("rule_groups") or [value["rule_group"]]
            if len(rule_groups) > 1:
                row["rule_groups"] = copy.deepcopy(rule_groups)
            interval = value.get("hit_interval")
            if interval is not None:
                row.update(kind="timer_counter", hit_interval=interval,
                           next_sequence_index=None,
                           next_attempt_number=value["hits"] + 1,
                           next_counter_position=value["hits"] % interval + 1)
            return row

        state = {"target_id": self.target, "t": self.time / TICKS,
                "knowledge": self.model["target"]["knowledge"], "aura": copy.deepcopy(self.aura),
                "icd": [icd_state(key, value) for key, value in sorted(self.icd.items())]}
        if self.model["ruleset"] in SWIRL_RULESETS:
            state["reaction_damage_icd"] = [
                {"actor": key[0], "target_id": key[1], "reaction_type": key[2], "reaction_element": key[3],
                 "attempts": value["hits"], "timer_remaining": max(0, value["anchor"] + value["reset_ticks"] - self.time) / TICKS}
                for key, value in sorted(self.damage_icd.items())]
        if self.freeze_state:
            state.pop("aura")
            state.update(self.freeze_state.state())
        elif self.multi_state:
            state.pop("aura")
            state.update(self.multi_state.state())
            state["crystallize"] = self.crystallize_state.state()
            state["recent_elemental_applications"] = [
                {**copy.deepcopy(row), "t": row["time_tick"]/TICKS}
                for row in self.recent_elemental_applications
            ]
        if self.special_runtime:
            state["special_reactions"] = self.special_runtime.state()
        if self.bloom_core_state:
            state["bloom_cores"] = self.bloom_core_state.state()
        return state

    def boundary(self, time, next_cycle):
        self.advance(time)
        state = self.state()
        if next_cycle == 1:
            self.window_state = copy.deepcopy(state)
        state.pop("t")
        # Expired ICD is behaviorally fresh on the next hit; historical counts
        # must not prevent proof of an otherwise identical boundary state.
        state["icd"] = [s for s in state["icd"] if s["timer_remaining"] > 0]
        for icd in state["icd"]:
            # Rule-group names are provenance aliases.  The counter tag and
            # normalized execution fields completely determine future
            # behavior, so aliases must not create false cycle divergence.
            icd.pop("rule_group", None)
            icd.pop("rule_groups", None)
        if "reaction_damage_icd" in state:
            state["reaction_damage_icd"] = [s for s in state["reaction_damage_icd"] if s["timer_remaining"] > 0]
        for field in ("aura", "ordinary_aura", "frozen"):
            if state.get(field):
                state[field].pop("source", None)
        for aura in state.get("auras", []):
            aura.pop("source", None)
        return state

    def _rule_signature(self, rule_group, icd):
        return rule_signature(self, rule_group, icd, signature=_icd_signature)

    def rule_signature(self, rule_group, icd):
        return self._rule_signature(rule_group, icd)

    def multi_reaction(self, *args, **kwargs):
        return self._multi_reaction(*args, **kwargs)

    def multi_proposal(self, *args, **kwargs):
        return self._multi_proposal(*args, **kwargs)

    def route_special_proposal(self, *args, **kwargs):
        return self._route_special_proposal(*args, **kwargs)

    def special_enabled(self, family):
        return self._special_enabled(family)

    def require_crystallize_trigger_sample(self, row):
        return self._require_crystallize_trigger_sample(row)

    def create_bloom_core(self, reaction, row):
        return self._create_bloom_core(reaction, row)

    def _admit(self, actor, profile):
        return admit(self, actor, profile, identity=_icd_identity, pending_error=ElementalPending)

    def _observe_icd(self, actor, profile):
        return observe_icd(self, actor, profile, identity=_icd_identity)

    def attempt(self, *, event_id, action_id, actor, time, application,
                damage_element=None, stable_event_id=None, contact=None,
                trigger_stat_sample=None, _preview=False):
        self.advance(time)
        # ``advance`` is an observed passage of game time and therefore stays
        # committed even when the event itself cannot be interpreted.  Every
        # mutation caused by this application is transactional: a pending or
        # invalid event must not consume an ICD position or leave a partial
        # aura/freeze transition that changes later events after the missing
        # fact is repaired.
        event_state = self._transaction_snapshot()
        icd = copy.deepcopy(self.icd)
        counter_bindings = copy.deepcopy(self.counter_bindings)
        icd_rules = copy.deepcopy(self.icd_rules)

        def rollback_event():
            self._restore_transaction(event_state)
            self.icd = icd
            self.counter_bindings = counter_bindings
            self.icd_rules = icd_rules

        profile = self.profiles[application["profile_id"]]
        application_attempted = application.get("attempted", True)
        direct_hit = application.get("direct_hit", True)
        stellar_layer_eligible = application.get(
            "counts_toward_stellar_superconduct", direct_hit)
        packet_size = application.get("application_packet_size", 1)
        if not isinstance(direct_hit, bool):
            raise TypeError("direct_hit必须是布尔值。")
        if not isinstance(stellar_layer_eligible, bool):
            raise TypeError("counts_toward_stellar_superconduct必须是布尔值。")
        if type(packet_size) is not int or packet_size < 1:
            raise ValueError("application_packet_size必须是正整数。")
        row = {"type": "elemental_application", "event_id": event_id, "action_id": action_id,
               "character": actor, "target_id": self.target, "t": time / TICKS,
               "profile_id": profile["id"], "profile_status": profile["status"],
               "source": profile["source"], "ruleset": self.model["ruleset"],
               "element": profile["element"], "before": self.state(), "reaction": None,
               "area_id": application.get("area_id", "battlefield.primary"),
               "atomic_parent_id": (application.get("atomic_parent_id")
                                    or stable_event_id or event_id),
               "application_packet_size": packet_size,
               "direct_hit": direct_hit,
               "counts_toward_stellar_superconduct": stellar_layer_eligible,
               **({"trigger_stat_sample": copy.deepcopy(trigger_stat_sample)}
                  if trigger_stat_sample is not None else {}),
               "application_attempted": application_attempted,
               "application_allowed": None, "reaction_occurred": False,
               "icd_audit": self._observe_icd(actor, profile)}
        for key in (
            "generated_application_request_id",
            "parent_damage_event_id",
            "application_order",
        ):
            if key in application:
                row[key] = copy.deepcopy(application[key])
        if self.model["ruleset"] in SWIRL_RULESETS:
            row.update(stable_event_id=stable_event_id or event_id, run_id=event_id)
        if self.multi_state:
            row["freeze_scenario"] = copy.deepcopy(self.multi_state.freeze_contract)
        if self.freeze_state:
            row.pop("reaction")
            row.update(reactions=[], layer_consumptions=[], package_id=(stable_event_id or event_id)+":reaction_package")
        elif self.multi_state:
            row.update(reactions=[], package_id=(stable_event_id or event_id)+":reaction_package")
        try:
            if self.freeze_state and not _preview and application["landed"]:
                preview = self.preview_copy()
                receipt = preview.attempt(event_id=event_id, action_id=action_id, actor=actor, time=time,
                                          application=application, damage_element=damage_element,
                                          stable_event_id=stable_event_id,
                                          trigger_stat_sample=trigger_stat_sample,
                                          _preview=True)
                if self.freeze_state.frozen or receipt["after"].get("frozen"):
                    self.guard_contact(contact, event_id)
            if application["target_id"] != self.target:
                raise ValueError("附着引用了未声明目标。")
            if damage_element is not None and profile["element"] != damage_element:
                raise ValueError("附着档案元素与命中元素不一致；多分量附着需独立事件。")
            if not application["landed"]:
                row.update(allowed=False, reason="missed")
            elif not application_attempted:
                # This contact landed and therefore remains a damage/contact
                # event.  The controller's cadence explicitly says that this
                # ordinal has no elemental-application attempt.  Do not call
                # _admit: skipped attempts must neither start nor advance ICD.
                row.update(allowed=False, application_allowed=False,
                           reason="application_schedule_blocked")
            elif profile["status"] == "pending":
                raise ElementalPending("unknown_application", "本次附着档案尚未确定。", event_id)
            elif profile["application"] == "none":
                row.update(allowed=False, reason="no_application")
            else:
                if self.model["target"]["knowledge"] != "known" or self.model["target"]["status"] == "pending":
                    raise ElementalPending("unknown_initial_aura", "目标初态尚未核实，不能推测为无元素底。", event_id)
                if profile["application"] == "unknown" or profile["status"] == "pending":
                    raise ElementalPending("unknown_application", "本次附着档案尚未确定。", event_id)
                supported = (("cryo", "hydro", "anemo") if self.freeze_state
                             else (*MULTI_AURA_ELEMENTS, "anemo", "geo") if self.multi_state
                             else (*ELEMENTS, "anemo") if self.model["ruleset"] in ORDINARY_SWIRL_RULESETS
                             else ELEMENTS)
                if profile["element"] not in supported:
                    raise ElementalPending("unsupported_element", "本规则尚未实现该元素的状态语义。", event_id,
                                           {"element": profile["element"]})
                allowed, reason, icd_audit = self._admit(actor, profile)
                row.update(allowed=allowed, application_allowed=allowed, reason=reason,
                           application_gu=profile["gauge_units"], icd_audit=icd_audit)
                if allowed:
                    if (packet_size != 1 and profile["element"] == "anemo"
                            and self._special_enabled("stellar_swirl")):
                        raise ElementalPending(
                            "stellar_swirl_packet_dispatch_required",
                            "多目标星扩散必须由同一父命中包的原子批处理入口提交，"
                            "单目标attempt不会逐child近似结算。",
                            event_id,
                            {"application_packet_size": packet_size,
                             "atomic_parent_id": row["atomic_parent_id"],
                             "area_id": row["area_id"]})
                    self._apply(profile, row)
                    self._record_recent_elemental_application(row)
                    if (profile["element"] in ("cryo", "electro")
                            and self._special_enabled("stellar_superconduct")):
                        try:
                            counted = self.special_runtime.record_stellar_superconduct_application(
                                event_id=event_id,
                                target_id=self.target,
                                actor=actor,
                                element=profile["element"],
                                time=time,
                                application_allowed=True,
                                direct_hit=stellar_layer_eligible,
                                atomic_parent_id=row["atomic_parent_id"],
                                area_id=row["area_id"],
                            )
                        except SpecialReactionPending as exc:
                            raise ElementalPending(
                                exc.issue["code"], str(exc), event_id,
                                {"special_reaction_issue": copy.deepcopy(exc.issue)}) from exc
                        row["stellar_superconduct_application"] = copy.deepcopy(
                            counted["event"])
                        self.generated_events.extend(copy.deepcopy(
                            counted["generated_events"]))
        except ElementalPending as exc:
            rollback_event()
            row.update(allowed=None, application_allowed=None, reaction_occurred=False,
                       reason=exc.issue["code"], after=self.state(), status="pending")
            self.trace.append(row)
            raise
        except Exception:
            rollback_event()
            raise
        row["application_allowed"] = row.get("allowed")
        row["reaction_occurred"] = bool(row.get("reaction") or row.get("reactions"))
        row.update(after=self.state(), status="executed")
        self.trace.append(row)
        return copy.deepcopy(row)

    def preview_copy(self):
        preview = copy.copy(self)
        preview.profiles = copy.deepcopy(self.profiles)
        preview.freeze_state = copy.deepcopy(self.freeze_state)
        preview.multi_state = copy.deepcopy(self.multi_state)
        preview.crystallize_state = copy.deepcopy(self.crystallize_state)
        preview.special_runtime = copy.deepcopy(self.special_runtime)
        preview.bloom_core_state = copy.deepcopy(self.bloom_core_state)
        preview.generated_events = copy.deepcopy(self.generated_events)
        preview.recent_elemental_applications = copy.deepcopy(
            self.recent_elemental_applications)
        preview.icd = copy.deepcopy(self.icd)
        preview.counter_bindings = copy.deepcopy(self.counter_bindings)
        preview.icd_rules = copy.deepcopy(self.icd_rules)
        preview.trace = []
        return preview

    def guard_contact(self, contact, event_id):
        known = contact or {}
        if known.get("kind") == "profile":
            known = next((p for p in self.model.get("contact_profiles", []) if p["id"] == known["profile_id"]), {})
            safe = known.get("freeze_interaction") == "none"
        else:
            safe = known.get("kind") == "none"
        if not safe or known.get("status") not in ("confirmed", "assumed") or not known.get("source", "").strip():
            raise ElementalPending("freeze_contact_unknown", "存在冻结或本次会造冻，接触的碎冰/削冻性质尚未确认安全。", event_id,
                                   {"contact": copy.deepcopy(contact), "state": self.state()})

    def _apply(self, profile, row):
        if self.multi_state:
            self._apply_multi(profile, row)
            return
        if self.freeze_state:
            reactions, layers = self.freeze_state.apply(profile["element"], profile["gauge_units"], row["run_id"])
            for reaction in reactions:
                suffix = ":reaction:"+reaction["type"]+(":"+reaction["reaction_element"] if reaction["type"] == "swirl" else "")
                reaction.update(id=row["stable_event_id"]+suffix, run_id=row["run_id"]+suffix,
                                parent_event_id=row["stable_event_id"], parent_run_id=row["run_id"],
                                action_id=row["action_id"], owner=row["character"], target_id=self.target,
                                trigger_element=profile["element"], trigger_gu=profile["gauge_units"],
                                source=row["source"], ruleset=FREEZE_RULESET, package_id=row["package_id"])
            row.update(reactions=reactions, layer_consumptions=layers, outcome="reaction" if reactions else "aura_updated")
            return
        element, gu = profile["element"], profile["gauge_units"]
        if element == "anemo":
            if self.aura is None:
                row["outcome"] = "anemo_without_aura"
                return
            supported_auras = ELEMENTS if self.model["ruleset"] == DYNAMIC_SWIRL_RULESET else ("cryo", "hydro")
            if self.aura["element"] not in supported_auras:
                raise ElementalPending("unsupported_swirl_aura", "普通扩散v2只支持冰/水单底，未假装其他状态可用。", row["event_id"],
                                       {"aura_element": self.aura["element"]})
            consumed = min(self.aura["remaining_gu"], .5 * gu)
            row["reaction"] = self._reaction(row, "swirl", consumed, gu)
            row["reaction"]["reaction_element"] = self.aura["element"]
            self.aura["remaining_gu"] -= consumed
            if self.aura["remaining_gu"] <= _EPS:
                self.aura = None
            row["outcome"] = "reaction"
            return
        gauge = gu * _TAX
        rate = gauge / (2.5 * gu + 7)
        if self.aura is None:
            self.aura = {"element": element, "remaining_gu": gauge,
                         "decay_gu_per_second": rate, "source": row["event_id"]}
            row["outcome"] = "aura_applied"
        elif self.aura["element"] == element:
            if gauge > self.aura["remaining_gu"]:
                self.aura["remaining_gu"] = gauge
                self.aura["source"] = row["event_id"]
                # Pyro refresh differs since 3.0. Hydro/Cryo retain the original rate.
                if element == "pyro":
                    self.aura["decay_gu_per_second"] = rate
            row["outcome"] = "aura_refreshed"
        else:
            rule = _REACTIONS.get((self.aura["element"], element))
            if rule is None:
                raise ElementalPending("unsupported_reaction", "本规则尚未实现此元素组合，未假装无反应。", row["event_id"],
                                       {"aura_element": self.aura["element"], "trigger_element": element})
            kind, factor, cost = rule
            consumed = min(self.aura["remaining_gu"], gu * cost)
            row["reaction"] = {**self._reaction(row, kind, consumed, gu), "factor": factor}
            self.aura["remaining_gu"] -= consumed
            if self.aura["remaining_gu"] <= _EPS:
                self.aura = None  # Surplus trigger gauge does not create a new aura.
            row["outcome"] = "reaction"

    def _special_enabled(self, reaction_type):
        if not self.special_runtime:
            return False
        entry = self.special_runtime.model.get("replacements", {}).get(reaction_type)
        return bool(entry and entry.get("enabled"))

    def _require_crystallize_trigger_sample(self, row):
        sample = row.get("trigger_stat_sample")
        valid = (
            isinstance(sample, dict)
            and sample.get("contract") == "ordinary-crystallize-trigger-stat-v1"
            and sample.get("character") == row["character"]
            and sample.get("phase") == "reaction_created"
            and sample.get("time_tick") == self.time
            and type(sample.get("level")) is int
            and 1 <= sample["level"] <= 100
            and not isinstance(sample.get("em"), bool)
            and isinstance(sample.get("em"), (int, float))
            and math.isfinite(sample["em"])
            and sample["em"] >= 0
            and isinstance(sample.get("source"), str)
            and bool(sample["source"].strip())
        )
        if not valid:
            raise ElementalPending(
                "crystallize_shield_sample_missing",
                "普通结晶造片需要在反应发生时取样触发者等级与当前元素精通；缺失时不生成空数值护盾。",
                row["event_id"],
                {"character": row["character"],
                 "required_contract": "ordinary-crystallize-trigger-stat-v1"},
            )
        return copy.deepcopy(sample)

    @staticmethod
    def _reaction_contributors(actor, aura):
        values = []
        if isinstance(aura, dict):
            values.extend(aura.get("contributor_ids", []))
            if not values and aura.get("source_owner"):
                values.append(aura["source_owner"])
        values.append(actor)
        return list(dict.fromkeys(value for value in values
                                  if isinstance(value, str) and value))[:4]

    def _multi_proposal(self, row, kind, aura_element, consumed, gu, *,
                        aura=None, index=0, trigger_gu_before=None,
                        trigger_gu_after=None):
        proposal = self._multi_reaction(
            row, kind, aura_element, consumed, gu, index=index,
            trigger_gu_before=trigger_gu_before,
            trigger_gu_after=trigger_gu_after,
            aura_owner=(aura or {}).get("source_owner"))
        proposal.update(
            commit_state="proposal",
            area_id=row["area_id"],
            atomic_parent_id=row["atomic_parent_id"],
            package_id=row["package_id"],
            application_packet_size=row.get("application_packet_size", 1),
            contributor_ids=self._reaction_contributors(row["character"], aura),
        )
        return proposal

    def _route_special_proposal(self, proposal, row):
        # Multi-target consumers must collect every proposal in one parent
        # packet before they can ask an area state machine to replace it.  The
        # world coordinator therefore previews all children, routes the whole
        # packet once, then replays each target commit with a pre-routed result.
        # Bindings are deliberately private and short lived: ordinary callers
        # still use ``special_runtime.replace`` below, while the packet path
        # must prove the complete proposal is byte-for-byte equivalent to its
        # preview rather than trusting only an event ID.
        preview_proposals = getattr(
            self, "_world_reaction_preview_proposals", None)
        if preview_proposals is not None:
            preview_proposals.append(copy.deepcopy(proposal))
            return {
                **copy.deepcopy(proposal),
                "commit_state": "committed",
                "route": "ordinary",
                "initial_gauge_commit_policy": (
                    "commit_elemental_proposal_once"),
            }
        bindings = getattr(self, "_world_reaction_replacement_bindings", None)
        if bindings is not None:
            binding = bindings.get(proposal.get("id"))
            if binding is None:
                raise ElementalPending(
                    "world_reaction_replacement_missing",
                    "世界事务没有为本次普通反应提案提供预路由结果。",
                    row["event_id"],
                    {"proposal_id": proposal.get("id")},
                )
            if binding["used"]:
                raise ElementalPending(
                    "world_reaction_replacement_reused",
                    "同一预路由反应结果不能提交两次。",
                    row["event_id"],
                    {"proposal_id": proposal.get("id")},
                )
            if proposal != binding["expected_proposal"]:
                raise ElementalPending(
                    "world_reaction_proposal_changed",
                    "目标状态在预览与提交之间发生变化，完整反应提案不再一致。",
                    row["event_id"],
                    {"proposal_id": proposal.get("id")},
                )
            replacement = binding["replacement"]
            route = (replacement.get("route")
                     if isinstance(replacement, dict) else None)
            canonical_ordinary = {
                **copy.deepcopy(proposal),
                "commit_state": "committed",
                "route": "ordinary",
                "initial_gauge_commit_policy": (
                    "commit_elemental_proposal_once"),
            }
            valid_ordinary = (
                route == "ordinary"
                and replacement == canonical_ordinary
            )
            changed_fields, unexpected_fields = (
                _world_special_replacement_drift(proposal, replacement)
                if isinstance(replacement, dict) else ([], [])
            )
            valid_special = (
                route == "special"
                and isinstance(replacement.get("id"), str)
                and bool(replacement["id"])
                and replacement["id"] != proposal.get("id")
                and isinstance(replacement.get("type"), str)
                and bool(replacement["type"])
                and replacement["type"] != proposal.get("type")
                and replacement.get("ordinary_reaction_id")
                == proposal.get("id")
                and replacement.get("replaces") == proposal.get("type")
                and replacement.get("commit_state") == "committed"
                and replacement.get("initial_gauge_commit_policy")
                == "commit_elemental_proposal_once"
                and not changed_fields
                and not unexpected_fields
            )
            if (not isinstance(replacement, dict)
                    or not (valid_ordinary or valid_special)):
                raise ElementalPending(
                    "world_reaction_replacement_invalid",
                    "预路由的普通或特殊结果与当前反应提案的身份或提交契约不一致。",
                    row["event_id"],
                    {
                        "proposal_id": proposal.get("id"),
                        "changed_proposal_fields": changed_fields,
                        "unexpected_replacement_fields": unexpected_fields,
                    },
                )
            decision = binding.get("decision")
            decision_drift = _world_route_decision_drift(
                proposal, replacement, decision, self.time)
            if any(decision_drift.values()):
                raise ElementalPending(
                    "world_reaction_decision_invalid",
                    "预路由决策没有精确绑定当前普通提案与提交结果。",
                    row["event_id"],
                    {
                        "proposal_id": proposal.get("id"),
                        **decision_drift,
                    },
                )
            binding["used"] = True
            if decision is not None:
                row.setdefault("special_reaction_decisions", []).append(
                    copy.deepcopy(decision))
            return copy.deepcopy(replacement)
        if self.special_runtime is None:
            return {
                **copy.deepcopy(proposal),
                "commit_state": "committed",
                "route": "ordinary",
                "initial_gauge_commit_policy": "commit_elemental_proposal_once",
            }
        try:
            receipt = self.special_runtime.replace(proposal, time=self.time)
        except SpecialReactionPending as exc:
            raise ElementalPending(
                exc.issue["code"], str(exc), row["event_id"],
                {"special_reaction_issue": copy.deepcopy(exc.issue)}) from exc
        row.setdefault("special_reaction_decisions", []).append(
            copy.deepcopy(receipt["decision"]))
        self.generated_events.extend(copy.deepcopy(receipt["generated_events"]))
        return receipt["reaction"]

    def _attempt_with_world_reaction_routes(
        self,
        *,
        reaction_routes,
        **attempt,
    ):
        """Commit one target attempt using complete world-routed proposals.

        This is an internal two-phase-commit seam for
        :class:`ElementalWorldRuntime`.  It intentionally does not own the
        cross-target rollback: callers must invoke it inside the world's
        ``execute_atomic`` boundary.  The empty route list is meaningful: it
        proves that preview produced no reaction proposal and makes an
        unexpected commit-time proposal fail closed.  Every expected proposal
        must otherwise be consumed exactly once, and
        :meth:`_route_special_proposal` compares the complete proposal before
        accepting either an ordinary commit or a special replacement.
        """
        if self.special_runtime is not None:
            raise RuntimeError("世界预路由提交要求目标运行时不单独绑定special_runtime。")
        if hasattr(self, "_world_reaction_replacement_bindings"):
            raise RuntimeError("目标运行时不允许嵌套预路由提交。")
        if not isinstance(reaction_routes, list):
            raise TypeError("reaction_routes必须是预路由绑定列表。")
        bindings = {}
        for index, item in enumerate(reaction_routes):
            if not isinstance(item, dict):
                raise TypeError(f"reaction_routes[{index}]必须是对象。")
            missing_binding_fields = sorted(
                _WORLD_REACTION_ROUTE_BINDING_FIELDS-set(item))
            unexpected_binding_fields = sorted(
                set(item)-_WORLD_REACTION_ROUTE_BINDING_FIELDS)
            if missing_binding_fields or unexpected_binding_fields:
                raise ElementalPending(
                    "world_reaction_route_binding_invalid",
                    "世界预路由绑定字段不完整或包含未消费数据。",
                    attempt.get("event_id", "world-reaction-commit"),
                    {
                        "route_index": index,
                        "missing_binding_fields": missing_binding_fields,
                        "unexpected_binding_fields": unexpected_binding_fields,
                    },
                )
            expected = item.get("expected_proposal")
            replacement = item.get("replacement")
            if (not isinstance(expected, dict)
                    or expected.get("commit_state") != "proposal"):
                raise ValueError(
                    f"reaction_routes[{index}]缺少未提交的完整普通反应提案。")
            proposal_id = expected.get("id")
            if not isinstance(proposal_id, str) or not proposal_id:
                raise ValueError(f"reaction_routes[{index}]提案缺少唯一ID。")
            if proposal_id in bindings:
                raise ValueError("reaction_routes提案ID不得重复。")
            if not isinstance(replacement, dict):
                raise TypeError(f"reaction_routes[{index}].replacement必须是对象。")
            bindings[proposal_id] = {
                "expected_proposal": copy.deepcopy(expected),
                "replacement": copy.deepcopy(replacement),
                "decision": copy.deepcopy(item.get("decision")),
                "used": False,
            }
        self._world_reaction_replacement_bindings = bindings
        try:
            receipt = self.attempt(**attempt)
            unused = sorted(proposal_id for proposal_id, binding in bindings.items()
                            if not binding["used"])
            if unused:
                raise ElementalPending(
                    "world_reaction_replacement_unused",
                    "本次目标提交没有产生预览阶段声明的全部反应提案。",
                    attempt.get("event_id", "world-reaction-commit"),
                    {"unused_proposal_ids": unused},
                )
            return receipt
        finally:
            del self._world_reaction_replacement_bindings

    def _preview_with_world_reaction_routes(self, **attempt):
        """Preview one application and return every replaceable proposal.

        The preview runtime must be a disposable copy.  This hook records the
        exact proposal at the routing seam before later reaction annotations
        are attached, avoiding a lossy reconstruction from the final receipt.
        """
        if self.special_runtime is not None:
            raise RuntimeError("世界反应预览要求目标运行时不单独绑定special_runtime。")
        if (hasattr(self, "_world_reaction_preview_proposals")
                or hasattr(self, "_world_reaction_replacement_bindings")):
            raise RuntimeError("目标运行时不允许嵌套世界反应预览。")
        proposals: list[dict] = []
        self._world_reaction_preview_proposals = proposals
        try:
            receipt = self.attempt(**attempt)
            return {
                "receipt": receipt,
                "proposals": copy.deepcopy(proposals),
            }
        finally:
            del self._world_reaction_preview_proposals

    def _attempt_with_world_reaction_replacements(
        self,
        *,
        reaction_replacements,
        **attempt,
    ):
        """Compatibility wrapper for the original special-only packet seam."""
        if not isinstance(reaction_replacements, list) or not reaction_replacements:
            raise ValueError("reaction_replacements必须包含至少一个预路由绑定。")
        if any(not isinstance(item, dict)
               or not isinstance(item.get("replacement"), dict)
               or item["replacement"].get("route") != "special"
               for item in reaction_replacements):
            raise ValueError("reaction_replacements只接受特殊反应替换结果。")
        return self._attempt_with_world_reaction_routes(
            reaction_routes=reaction_replacements,
            **attempt,
        )

    def _create_bloom_core(self, reaction, row):
        if self.bloom_core_state is None:
            return None
        core_request = next((event for event in reversed(self.generated_events)
                             if event.get("type") == "special_reaction_core_request"
                             and event.get("parent_reaction_id") == reaction["id"]), None)
        core_id = ((core_request or {}).get("event_id")
                   or reaction["id"]+":core")
        receipt = self.bloom_core_state.create(
            time=self.time,
            event_id=core_id,
            target_id=self.target,
            area_id=row["area_id"],
            owner=row["character"],
            parent_reaction_id=reaction["id"],
            contributor_ids=copy.deepcopy(reaction.get("contributor_ids")
                                          or [row["character"]]),
            source=row["source"],
        )
        created = {
            "type": ("ordinary_bloom_core_created" if receipt["core"]["core_variant"] == "ordinary"
                     else "bloom_replacement_core_created"),
            "event_id": core_id+":created",
            "t": self.time/TICKS,
            "time_tick": self.time,
            "reaction_type": "bloom",
            "parent_reaction_id": reaction["id"],
            "core": receipt["core"],
            "status": "executed",
        }
        self.generated_events.append(created)
        self.generated_events.extend(copy.deepcopy(receipt["generated_events"]))
        return created

    def _apply_multi(self, profile, row):
        return apply_multi_contact(MultiAuraContact(
            self, profile, row, epsilon=_EPS,
            reaction_table=_REACTIONS, pending_error=ElementalPending))

    def _multi_reaction(self, row, kind, aura_element, consumed, gu, *, index=0,
                        trigger_gu_before=None, trigger_gu_after=None, aura_owner=None):
        return {
            "id": row["stable_event_id"] + f":reaction:{index}",
            "run_id": row["run_id"] + f":reaction:{index}",
            "type": kind,
            "trigger_element": row["element"],
            "aura_element": aura_element,
            "reaction_element": aura_element,
            "consumed_gu": consumed,
            "trigger_gu": gu,
            "owner": row["character"],
            "trigger_owner": row["character"],
            "aura_owner": aura_owner,
            "target_id": self.target,
            "parent_event_id": row["stable_event_id"],
            "parent_run_id": row["run_id"],
            "action_id": row["action_id"],
            "ruleset": MULTI_AURA_RULESET,
            "source": row["source"],
            "selection_policy": "fixed_reaction_priority_v1",
            "route_id": row.get("reaction_priority", {}).get("route_id", "freeze-resolution"),
            "route_index": index,
            "trigger_gu_before": gu if trigger_gu_before is None else trigger_gu_before,
            "trigger_gu_after": 0.0 if trigger_gu_after is None else trigger_gu_after,
        }

    def _reaction(self, row, kind, consumed, gu):
        reaction = {"id": row["event_id"] + ":reaction", "type": kind,
                    "trigger_element": row["element"], "aura_element": self.aura["element"],
                    "consumed_gu": consumed, "trigger_gu": gu, "owner": row["character"],
                    "target_id": self.target, "parent_event_id": row["event_id"]}
        if self.model["ruleset"] in SWIRL_RULESETS:
            reaction.update(id=row["stable_event_id"] + ":reaction:0", run_id=row["run_id"] + ":reaction:0",
                            parent_event_id=row["stable_event_id"], parent_run_id=row["run_id"],
                            action_id=row["action_id"], reaction_element=self.aura["element"],
                            ruleset=self.model["ruleset"], source=row["source"])
        return reaction

    def attempt_reaction_damage(self, reaction):
        """Distinct damage channel: a blocked attempt never undoes its reaction."""
        if self.model["ruleset"] not in SWIRL_RULESETS or reaction["type"] != "swirl":
            raise ValueError("只有v2普通扩散可尝试派生伤害。")
        source = self.model["swirl_damage"]
        suffix = (":reaction_damage:swirl:"+reaction["reaction_element"]
                  if self.freeze_state or self.multi_state
                  else f":reaction_damage:{reaction.get('route_index', 0)}")
        row = {"type": "reaction_damage_attempt", "event_id": reaction["parent_event_id"] + suffix,
               "run_id": reaction["parent_run_id"] + suffix, "t": self.time / TICKS,
               "reaction_id": reaction["id"], "reaction_run_id": reaction["run_id"],
               "parent_event_id": reaction["parent_event_id"], "parent_run_id": reaction["parent_run_id"],
               "action_id": reaction["action_id"], "character": reaction["owner"], "target_id": self.target,
               "reaction_type": "swirl", "reaction_element": reaction["reaction_element"],
               "channel": "damage", "damage_scope": "origin_target", "source": source["source"], "ruleset": self.model["ruleset"]}
        icd = source["icd"]
        if source["status"] == "pending" or icd["kind"] == "unknown":
            row.update(allowed=None, status="pending", reason="unknown_reaction_damage")
            self.trace.append(row)
            raise ElementalPending("unknown_reaction_damage", "普通扩散伤害来源或ICD尚未核实。", row["event_id"])
        key = (reaction["owner"], self.target, "swirl", reaction["reaction_element"])
        reset = int(Decimal(str(icd["reset_seconds"])) * TICKS)
        state = self.damage_icd.get(key)
        if state is None or self.time >= state["anchor"] + reset:
            reason = "first_damage_attempt" if state is None else "damage_icd_timer_reset"
            state = self.damage_icd[key] = {"anchor": self.time, "hits": 1, "reset_ticks": reset}
        else:
            state["hits"] += 1
            reason = "damage_icd_sequence"
        index = state["hits"] - 1
        allowed = index < len(icd["sequence"]) and bool(icd["sequence"][index])
        row.update(allowed=allowed, reason=reason if allowed else "damage_icd_blocked", status="executed",
                   shared_key=list(key), sequence_index=index, coefficient=int(allowed),
                   anchor_t=state["anchor"] / TICKS, reset_at=(state["anchor"] + reset) / TICKS)
        self.trace.append(row)
        return copy.deepcopy(row)
