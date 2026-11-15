"""Reviewed static action-gate mappings; validation is not execution authority."""
from __future__ import annotations

from genshin_runtime.action_resource_semantics import (
    digest,
    integer,
    obj,
    require,
    snapshot,
    text,
)
from genshin_runtime.action_root_semantics import bounded

CONTRACT = "reviewed-static-action-admission-v1"
PROFILE_CONTRACT = "fresh-action-admission-profile-v1"
QUALIFICATION = "reviewed_static_action_gate_mapping"
SCOPE = "static_base_action_admission_only_not_hit_particle_icd_or_dynamic_resource"
UNKNOWNS = [
    "game_animation_dispatch",
    "hit_sequence",
    "particle_generation",
    "dynamic_cooldown_changes",
]
FIELD_AUTHORITY = {
    "cooldown.duration": "fresh_upstream",
    "cooldown.charges": "fresh_upstream",
    "energy.required": "fresh_upstream",
    "energy.amount": "fresh_upstream",
    "cooldown.group": "reviewed_local_semantics",
    "cooldown.start": "reviewed_local_semantics",
    "cooldown.recovery": "reviewed_local_semantics",
    "variant": "reviewed_local_semantics",
    "action_type_id": "reviewed_local_semantics",
}
MAX_FACTS = 32


def same(left, right):
    from genshin_runtime.mechanism_semantics import fingerprint

    return fingerprint(left) == fingerprint(right)


def ref(value):
    obj(value, {"id", "revision"})
    text(value["id"], 80)
    integer(value["revision"], 1, 2**31 - 1)


def validate_selection(value):
    bounded(value)
    obj(value, {"character_id", "skill_depot_id", "skill_id", "slot", "constellation"})
    for key in ("character_id", "skill_depot_id", "skill_id"):
        integer(value[key])
    require(value["slot"] == "burst", "动作接纳首片只支持元素爆发槽")
    integer(value["constellation"], 0, 0)


def validate_model(claim):
    bounded(claim)
    obj(claim, {"contract", "qualification", "snapshot", "target", "domain", "resource_binding",
                "field_authority", "unknowns", "prior_decision", "scope"})
    require(claim["contract"] == CONTRACT and claim["qualification"] == QUALIFICATION
            and claim["scope"] == SCOPE, "动作接纳模型合同、资格或范围错误")
    snapshot(claim["snapshot"])
    require(claim["snapshot"]["version_line"] == "release", "动作接纳首片只支持release资料线")

    target = claim["target"]
    obj(target, {"character_id", "skill_depot_id", "skill_id", "slot"})
    for key in ("character_id", "skill_depot_id", "skill_id"):
        integer(target[key])
    require(target["slot"] == "burst", "动作接纳首片目标只支持元素爆发")

    domain = claim["domain"]
    obj(domain, {"constellations", "variant", "action_type_id"})
    require(domain["constellations"] == [0] and domain["variant"] == "default"
            and domain["action_type_id"] == "action.elemental_burst",
            "动作接纳首片只支持C0、default固定耗能元素爆发")

    binding = claim["resource_binding"]
    obj(binding, {"resource_fingerprint", "cooldown_start", "cooldown_recovery", "cooldown_group",
                  "energy_mode", "energy_delivery"})
    digest(binding["resource_fingerprint"])
    text(binding["cooldown_group"], 256)
    require(binding["cooldown_start"] == "action_start"
            and binding["cooldown_recovery"] == "parallel"
            and binding["energy_mode"] == "fixed_equal_cost"
            and binding["energy_delivery"] == "action_start",
            "动作接纳首片资源门禁映射不支持")

    require(claim["field_authority"] == FIELD_AUTHORITY, "动作接纳字段权威集合错误")
    require(claim["unknowns"] == UNKNOWNS, "动作接纳未知边界必须完整且有序")
    decision = claim["prior_decision"]
    obj(decision, {"proposal_sha256", "decision_sha256", "report_sha256"})
    for value in decision.values():
        digest(value)


def evidence_for(claim):
    """Return the exact semantic evidence envelope for the reviewed claim."""
    validate_model(claim)
    target = claim["target"]
    decision = claim["prior_decision"]
    return [
        {"id": "action-admission-resource-profile", "kind": "runtime_node",
         "locator": ("action-resource-profile://release/"
                     f"{target['character_id']}/{target['skill_depot_id']}/{target['skill_id']}"),
         "sha256": claim["resource_binding"]["resource_fingerprint"]},
        {"id": "action-admission-review-decision", "kind": "reference_implementation",
         "locator": "action-admission-review://" + decision["proposal_sha256"],
         "sha256": decision["decision_sha256"]},
        {"id": "action-admission-independent-review", "kind": "reference_implementation",
         "locator": "action-admission-review-report://" + decision["decision_sha256"],
         "sha256": decision["report_sha256"]},
    ]


def selection_matches(claim, selection, variant, action_type_id):
    validate_model(claim)
    validate_selection(selection)
    return (claim["target"] == {key: selection[key] for key in ("character_id", "skill_depot_id", "skill_id", "slot")}
            and selection["constellation"] in claim["domain"]["constellations"]
            and variant == claim["domain"]["variant"]
            and action_type_id == claim["domain"]["action_type_id"])


__all__ = [
    "CONTRACT", "FIELD_AUTHORITY", "MAX_FACTS", "PROFILE_CONTRACT", "QUALIFICATION", "SCOPE", "UNKNOWNS",
    "evidence_for", "ref", "same", "selection_matches", "validate_model", "validate_selection",
]
