"""Fixed generic special-reaction execution, separate from App's 594 fixture.

The roster and capability tags are synthetic Engine inputs, not database
projections. Mechanic inputs remain explicit assumptions. No character logic,
candidate acceptance, game benchmark or combined damage total is produced.
"""
from __future__ import annotations

import copy
import math
import sys
from pathlib import Path

from .frozen_selfcheck_isolation import SelfcheckError

SOURCE = "synthetic_engine_input：固定通用引擎工程假设，不是角色技能或数据库资格投影。"
PROVIDER = "engineering.special.provider"
TRIGGER = "engineering.special.trigger"
ELEMENTS = {
    "stellar_superconduct": ("cryo", "electro", "superconduct"),
    "stellar_swirl": ("cryo", "anemo", "swirl"),
    "lunar_electro": ("hydro", "electro", "electro_charged"),
    "lunar_bloom": ("hydro", "dendro", "bloom"),
    "lunar_crystallize": ("hydro", "geo", "crystallize"),
}


def _require(condition, code, details=None):
    if not condition:
        raise SelfcheckError("selfcheck_special_" + code, details)


def _number(actual, expected, equation):
    valid = (not isinstance(actual, bool) and isinstance(actual, (int, float))
             and math.isfinite(actual) and math.isclose(actual, expected, rel_tol=1e-12, abs_tol=1e-8))
    _require(valid, "numeric_mismatch", {"actual": actual, "expected": expected, "equation": equation})
    return {"actual": actual, "expected": expected, "independent_equation": equation}


def _execution(package_root):
    spec = sys.modules[__name__].__spec__
    loader = getattr(spec, "loader", None)
    origin = getattr(spec, "origin", None)
    if package_root is None:
        return {"layer": "source_probe", "actual_frozen_tested": False,
                "helper_module": __name__, "module_loader": type(loader).__name__}
    root = Path(package_root).resolve(strict=True)
    _require(getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"), "helper_not_frozen")
    _require(type(loader).__name__ == "PyiFrozenLoader"
             and type(loader).__module__.endswith("pyimod02_importers"), "helper_loader_not_frozen")
    _require(isinstance(origin, str) and Path(origin).resolve().is_relative_to(root), "helper_outside_package")
    _require(Path(sys._MEIPASS).resolve().is_relative_to(root), "helper_bundle_outside_package")
    return {"layer": "frozen_cli_execution", "actual_frozen_tested": True, "source_fallback": False,
            "helper_module": __name__, "module_loader": type(loader).__name__,
            "helper_origin": Path(origin).resolve().relative_to(root).as_posix()}


def _model(family, *, pending_mechanics=False, pending_qualification=False):
    from genshin_engine.special_reactions import (
        REQUIRED_CAPABILITY_TAGS,
        REVIEWED_SPECIAL_REACTION_PROFILES,
    )

    reviewed = REVIEWED_SPECIAL_REACTION_PROFILES[family]
    parameters = copy.deepcopy(reviewed["parameters"])
    if family == "lunar_crystallize" and not pending_mechanics:
        parameters.update(harmony_attack_cooldown_seconds=.2, harmony_attack_cooldown_scope="area")
    mechanics = {"status": "pending" if pending_mechanics else "assumed", "source": SOURCE,
                 "profile_id": reviewed["profile_id"], "profile_revision": reviewed["profile_revision"],
                 "profile_content_hash": reviewed["profile_content_hash"], **parameters}
    if not pending_mechanics:
        mechanics["override_reason"] = SOURCE
    return {"schema_version": 1, "replacements": {family: {
        "enabled": True,
        # This confirms only the explicit synthetic roster/tag supplied to the
        # public Engine constructor. It grants no DB or App authority.
        "qualification": {"status": "pending" if pending_qualification else "confirmed",
            "provider_ids": [PROVIDER], "capability_tag": REQUIRED_CAPABILITY_TAGS[family], "source": SOURCE},
        "mechanics": mechanics}}}


def _runtime(family, **options):
    from genshin_engine.special_reactions import (
        REQUIRED_CAPABILITY_TAGS,
        SpecialReactionRuntime,
    )

    return SpecialReactionRuntime(_model(family, **options), party_member_ids=[PROVIDER, TRIGGER],
                                  party_capability_tags={PROVIDER: [REQUIRED_CAPABILITY_TAGS[family]]})


def _reaction(family, index=1):
    aura, trigger, ordinary = ELEMENTS[family]
    value = {"id": f"engineering.{family}.{index}", "type": ordinary, "aura_element": aura,
             "trigger_element": trigger, "owner": TRIGGER, "target_id": "engineering.enemy",
             "parent_event_id": f"engineering.parent.{index}", "commit_state": "proposal"}
    if family == "stellar_swirl":
        value.update(trigger_owner=TRIGGER, aura_owner=PROVIDER, contributor_ids=[PROVIDER, TRIGGER])
    if family == "lunar_crystallize":
        value["contributor_ids"] = [PROVIDER]
    return value


def _actor(identity, **values):
    return {"id": identity, "level": 90, "em": 0, "crit_rate": 0, "crit_dmg": 0,
            "source_refs": [{"kind": "synthetic_engine_input", "id": identity}], **values}


def _damage(receipt):
    return [row for row in receipt["generated_events"] if row["type"] == "special_reaction_damage_request"]


def _direct(family, expected, equation, *, layers=None):
    from genshin_engine.special_reactions import calculate_direct_special_damage

    value = calculate_direct_special_damage(family, _actor(PROVIDER), multiplier=2, scaling=100, layers=layers)
    _require(value["reaction_type"] == family and value["reaction_base"] is False, "direct_damage_identity")
    return {"scope": "standalone_direct_formula_not_an_ability_event", "input_multiplier": 2, "input_scaling": 100,
            "result": value, "check": _number(value["result"]["expected"], expected, equation)}


def _stellar_superconduct():
    runtime = _runtime("stellar_superconduct")
    established = runtime.replace(_reaction("stellar_superconduct"), time=0)
    _require(not _damage(established), "stellar_establishment_emitted_damage")
    runtime.record_stellar_superconduct_application(event_id="engineering.later-cryo", target_id="engineering.enemy",
        actor=PROVIDER, element="cryo", time=1_000_000, application_allowed=True)
    due = runtime.advance(4_000_000)
    settlements = [row for row in due if row.get("operation") == "settle_four_second_window"]
    _require(len(settlements) == 1 and settlements[0]["layers"] == 1, "stellar_settlement")
    factor = _number(settlements[0]["system_factor"], 1.45, "1.4 + 0.05 * one successful later application")
    return {"establishment": established, "settlement": settlements[0], "factor_check": factor,
            "direct_formula": _direct("stellar_superconduct", 290, "100 * 2 * (1.4 + 0.05 * 1)", layers=1)}


def _stellar_swirl():
    from genshin_engine.special_reactions import resolve_special_damage_event

    runtime = _runtime("stellar_swirl")
    first = runtime.replace_stellar_swirl_packet([_reaction("stellar_swirl")], time=0)
    requests = _damage(first)
    _require(len(requests) == 1 and requests[0]["reaction_type"] == "stellar_swirl_anemo", "swirl_immediate_request")
    # Flat inputs deliberately avoid the level table. Fixed role slots must
    # keep the weaker Anemo trigger in .6; global sorting would incorrectly 150.
    actors = [_actor(TRIGGER, stellar_mult=0, reaction_flat=100), _actor(PROVIDER, stellar_mult=0, reaction_flat=200)]
    immediate = resolve_special_damage_event(requests[0], actors)
    immediate_check = _number(immediate["damage"]["expected"], 120, "Anemo trigger 100 * .6 + Cryo applier 200 * .3")
    due = runtime.advance(3_000_000)
    damage = [row for row in due if row["type"] == "special_reaction_damage_request"]
    applications = [row for row in due if row["type"] == "special_reaction_application_request"]
    _require(len(damage) == len(applications) == 1 and damage[0]["reaction_type"] == "stellar_swirl_cryo_lv2", "swirl_deadline")
    _require(applications[0]["application_order"] == "after_damage" and applications[0]["gauge_units"] == 1,
             "swirl_post_damage_application")
    detonation = resolve_special_damage_event(damage[0], actors)
    return {"immediate": immediate, "immediate_check": immediate_check, "detonation": detonation,
            "detonation_check": _number(detonation["damage"]["expected"], 150, "Cryo applier 200 * .6 + Anemo trigger 100 * .3"),
            "post_damage_application": applications[0]}


def _lunar_electro():
    from genshin_engine.special_reactions import resolve_special_damage_event

    runtime = _runtime("lunar_electro")
    first = runtime.replace(_reaction("lunar_electro"), time=0)
    _require(not _damage(first), "lunar_electro_initial_damage")
    ticks = [row for row in runtime.advance(250_000) if row["type"] == "special_reaction_tick_request"]
    _require(len(ticks) == 1, "lunar_electro_first_tick")
    components = {}
    for element, remaining, owner in (("hydro", 1, PROVIDER), ("electro", .5, TRIGGER)):
        components[element] = {"remaining_gu": remaining, "contributor_ids": [owner],
            "contributor_allocations": [{"owner": owner, "remaining_gu": remaining, "last_application_at": 0,
                                         "source": SOURCE}], "owner_residual_status": "tracked", "decay_gu_per_second": .001}
    snapshot = {"knowledge": "known", "owner_residual_contract": "same-element-owner-residual-max-v1",
                "sample_time_tick": 250_000, "components": components}
    admitted = runtime.admit_lunar_electro_tick(ticks[0], snapshot)
    _require(admitted["admission"]["allowed"] is True, "lunar_electro_admission")
    after = admitted["aura_after"]["components"]
    hydro = _number(after["hydro"]["remaining_gu"], .6, "1 - .4 GU")
    electro = _number(after["electro"]["remaining_gu"], .1, ".5 - .4 GU")
    actors = [_actor(PROVIDER, lunar_mult=0, reaction_flat=100),
              _actor(TRIGGER, lunar_mult=0, reaction_flat=60, crit_rate=.5, crit_dmg=2)]
    damage = resolve_special_damage_event(admitted["damage_event"], actors)
    check = _number(damage["damage"]["expected"], 108, ".5 * (100*.6 + 60*.3) + .5 * (180*.6 + 100*.3)")
    return {"tick": ticks[0], "admission": admitted, "hydro_check": hydro, "electro_check": electro,
            "damage": damage, "damage_check": check,
            "direct_formula": _direct("lunar_electro", 600, "100 * 2 * 3")}


def _lunar_bloom():
    runtime = _runtime("lunar_bloom")
    first = runtime.replace(_reaction("lunar_bloom"), time=0)
    _require(not _damage(first) and sum(row["type"] == "special_reaction_core_request" for row in first["generated_events"]) == 1,
             "lunar_bloom_core")
    runtime.advance(2_500_000)
    before = runtime.state()["party"]["lunar_bloom_dew"]
    _require(before == 1, "lunar_bloom_dew_fill")
    consumed = runtime.consume_lunar_bloom(event_id="engineering.consume-dew", target_id="engineering.enemy",
                                          actor=PROVIDER, time=2_500_001)
    resource = consumed["resource_event"]
    _require(resource["status"] == "executed" and resource["after"] == 0 and resource["lunar_damage_emitted"] is False,
             "lunar_bloom_resource_consume")
    _require(not _damage(consumed), "lunar_bloom_invented_reaction_damage")
    return {"core": first, "dew_before": before, "consumption": consumed,
            "direct_formula": _direct("lunar_bloom", 200, "100 * 2 * 1")}


def _lunar_crystallize():
    from genshin_engine.special_reactions import resolve_special_damage_event

    runtime = _runtime("lunar_crystallize")
    receipts = [runtime.replace(_reaction("lunar_crystallize", index), time=(index-1)*1_000_000) for index in (1, 2, 3)]
    _require(not _damage(receipts[0]) and not _damage(receipts[1]), "lunar_crystallize_early_damage")
    requests = _damage(receipts[2])
    _require(len(requests) == 3, "lunar_crystallize_cage_count")
    damage = [resolve_special_damage_event(row, [_actor(PROVIDER, lunar_mult=0, reaction_flat=100)]) for row in requests]
    checks = [_number(row["damage"]["expected"], 60, "one explicitly supplied contributor: 100 * .6") for row in damage]
    return {"assumptions": {"harmony_attack_cooldown_seconds": .2, "harmony_attack_cooldown_scope": "area", "status": "assumed"},
            "replacement_receipts": receipts, "cage_damage": damage, "checks": checks,
            "cage_subtotal_check": _number(sum(row["damage"]["expected"] for row in damage), 180, "3 independently emitted cages * 60"),
            "direct_formula": _direct("lunar_crystallize", 320, "100 * 2 * 1.6")}


def _pending():
    from genshin_engine.special_reactions import SpecialReactionPending

    probes = (("lunar_crystallize", {"pending_mechanics": True}, "special_mechanics_pending"),
              ("stellar_superconduct", {"pending_qualification": True}, "special_qualification_pending"))
    rows = []
    for family, options, expected in probes:
        runtime = _runtime(family, **options)
        before = runtime.state()
        try:
            runtime.replace(_reaction(family), time=0)
        except SpecialReactionPending as error:
            _require(error.issue["code"] == expected, "pending_issue_mismatch", error.issue)
            _require(runtime.state() == before, "pending_mutated_state")
            _require(not any(row.get("type") == "special_reaction_damage_request" for row in runtime.trace), "pending_emitted_damage")
            rows.append({"family": family, "issue": error.issue, "state_unchanged": True,
                         "mechanic_input": runtime.model["replacements"][family]["mechanics"]})
        else:
            raise SelfcheckError("selfcheck_special_pending_was_executed", {"family": family})
    return rows


def verify_fixed_special_reactions(*, frozen_package_root=None):
    execution = _execution(frozen_package_root)
    return {"contract": "genshin-frozen-special-engineering-execution-v1", "status": "verified", "execution": execution,
            "input_scope": "synthetic_engine_input", "source": SOURCE, "mechanic_status": "assumed",
            "qualification_scope": "explicit_synthetic_roster_and_tags_not_a_database_projection",
            "app_formal_result": False, "benchmark_qualified": False, "included_in_594_total": False,
            "families": {"stellar_superconduct": _stellar_superconduct(), "stellar_swirl": _stellar_swirl(),
                         "lunar_electro": _lunar_electro(), "lunar_bloom": _lunar_bloom(),
                         "lunar_crystallize": _lunar_crystallize()}, "pending": _pending()}
