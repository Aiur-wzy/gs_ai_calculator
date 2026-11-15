"""Atomically materialize reviewed action dispatch into a private work plan."""
from __future__ import annotations

import copy
import json
import sqlite3
from dataclasses import dataclass, field

from genshin_app.task_capability_producer import emit_task_missing
from genshin_db.equipment import EquipmentError
from genshin_db.runtime_catalog import fingerprint

from .schema import PlanError

_NATIVE_FRAGMENT_KEYS = (
    "action_event_profiles", "action_event_bindings", "prototype_hits",
    "attack_metadata_sources", "periodic_attack_sources",
)


@dataclass
class PreparedActionDispatch:
    records: list = field(default_factory=list)
    sources: list = field(default_factory=list)
    generated_profile_ids: set = field(default_factory=set)
    automatically_confirmed_profile_ids: list = field(default_factory=list)
    native_fragments: dict | None = None


class _Pending(ValueError):
    pass


def _issue(record, missing, code, message, path=None):
    path = path or "action_dispatch_sources." + record["ability_id"]
    issue = {"code": code, "path": path[:1024], "message": str(message)[:1024]}
    record["issues"].append(issue)
    emit_task_missing(
        missing, code, issue["path"], issue["message"],
        "review_action_dispatch",
        consumer="action_dispatch:" + record["ability_id"],
        category="core_data_missing",
        repair_hint="核对该能力的动作身份、受审派发档案、覆盖清单和当前来源后重新物化",
    )


def _record(row):
    return {
        "contract_id": "native-action-dispatch-source-v1",
        "ability_id": row["ability_id"],
        "selector": {"version_line": row["version_line"], **copy.deepcopy(row["selector"])},
        "declared_profile_id": row["profile_id"],
        "expected_profile_fingerprint": row["expected_profile_fingerprint"],
        "resolved_profile_id": None,
        "source_profile_fingerprint": None,
        "expected_components": [],
        "expected_occurrences": [],
        "coverage_status": "unknown",
        "declared_coverage_manifest_id": row.get("coverage_manifest_id"),
        "expected_coverage_manifest_fingerprint": row.get(
            "expected_coverage_manifest_fingerprint"),
        "coverage_manifest_id": None,
        "coverage_manifest_fingerprint": None,
        "coverage_manifest": None,
        "coverage_expectation": None,
        "coverage_issues": [],
        "target_contract": None,
        "status": "pending",
        "materialized": None,
        "native_fragments_fingerprint": None,
        "source_verification": None,
        "application_adapter_audit": [],
        "application_rule_audit": [],
        "issues": [],
    }


def _scenario(row):
    return {item["component_id"]: {key: copy.deepcopy(item[key])
                                   for key in ("landed", "order", "application_adapter") if key in item}
            for item in row["scenario_calibrations"]}


def _identity_checks(plan, row, profile, ability):
    selector = row["selector"]
    expected_type = {"normal": "action.normal_attack", "skill": "action.elemental_skill",
                     "burst": "action.elemental_burst"}[selector["slot"]]
    if ability["action_type_id"] != expected_type:
        raise _Pending("能力动作类别与精确技能槽不一致")
    if ability.get("variant", "default") != selector["variant"]:
        raise _Pending("能力变体与受审派发档案不一致")
    if profile["selector"] != {"version_line": row["version_line"], **selector}:
        raise _Pending("受审档案返回的完整动作身份与原生声明不一致")
    actor = ability["character"]
    characters = {item["id"] for item in plan["manual_template"]["characters"]}
    if actor not in characters:
        raise _Pending("能力角色不在当前伤害模板角色列表中")
    equipment = plan.get("equipment_inputs", {}).get(actor) or {}
    character_ref = equipment.get("character_ref")
    if not isinstance(character_ref, dict):
        raise _Pending("自动派发需要角色装备输入提供独立character_ref；不能用档案selector自证角色身份")
    if (character_ref["db_character_id"] != selector["character_id"]
            or character_ref["version_line"] != row["version_line"]):
        raise _Pending("能力所属角色与装备方案数据库角色身份不一致")
    return actor, character_ref["db_character_id"]


def _source_expectations_match(resolution, compiled):
    receipts = {row["component_id"]: row for row in
                (resolution.get("source_verification") or {}).get("receipts", [])}
    for expected in compiled["fragments"]["source_expectations"]:
        receipt = receipts.get(expected["component_id"])
        if receipt is None:
            return False
        for key in ("candidate_id", "origin_reference_id", "source_fingerprint", "interval_seconds"):
            if key in expected and receipt.get(key) != expected[key]:
                return False
    return True


def _append_unique(candidate, key, rows, identity):
    current = candidate.setdefault(key, [])
    occupied = {identity(item) for item in current}
    incoming = [identity(item) for item in rows]
    if len(incoming) != len(set(incoming)) or occupied & set(incoming):
        raise _Pending(f"自动物化的{key}与现有输入身份冲突")
    current.extend(copy.deepcopy(rows))


def _native_fragments(fragments):
    return {key: copy.deepcopy(fragments[key]) for key in _NATIVE_FRAGMENT_KEYS}


def _owned_fragments(fragments, record, *, normalize_confirmation=False):
    materialized = record.get("materialized") or {}
    profile_ids = set(materialized.get("profile_ids", []))
    hit_ids = set(materialized.get("prototype_hit_ids", []))
    profiles = [copy.deepcopy(row) for row in fragments.get("action_event_profiles", [])
                if row.get("id") in profile_ids]
    if normalize_confirmation:
        for profile in profiles:
            profile["accepted_profile_fingerprint"] = None
    return {
        "action_event_profiles": profiles,
        "action_event_bindings": [copy.deepcopy(row) for row in
            fragments.get("action_event_bindings", []) if row.get("profile_id") in profile_ids],
        "prototype_hits": [copy.deepcopy(row) for row in fragments.get("prototype_hits", [])
                           if row.get("id") in hit_ids],
        "attack_metadata_sources": [copy.deepcopy(row) for row in
            fragments.get("attack_metadata_sources", [])
            if row.get("action_event_profile_id") in profile_ids],
        "periodic_attack_sources": [copy.deepcopy(row) for row in
            fragments.get("periodic_attack_sources", [])
            if row.get("action_event_profile_id") in profile_ids],
    }


def prepare_action_dispatch_sources(plan, db_path, missing):
    """Resolve and materialize every declaration, or commit none of them."""
    result = PreparedActionDispatch()
    rows = plan.get("action_dispatch_sources", [])
    if not rows:
        return result
    rotation = plan.get("rotation_model") or {}
    abilities = {row.get("id"): row for row in rotation.get("abilities", [])
                 if isinstance(row, dict) and isinstance(row.get("id"), str)}
    bound = {row["ability_id"] for row in plan.get("action_event_bindings", [])}
    stages = []
    from genshin_app.equipment.action_dispatch_materialization import (
        compile_action_dispatch_inputs,
    )
    from genshin_db.runtime_action_dispatch import get_runtime_action_dispatch_profile

    for index, row in enumerate(rows):
        record = _record(row)
        result.records.append(record)
        try:
            if row["status"] != "confirmed":
                raise _Pending("动作派发来源仍为pending，不能自动生成命中")
            ability = abilities.get(row["ability_id"])
            if ability is None:
                raise _Pending("动作派发来源引用的能力不存在")
            if row["ability_id"] in bound:
                raise _Pending("该能力已有手工动作档案绑定，不能重复自动派发")
            matched_actions = [action for action in rotation.get("actions", [])
                               if action.get("ability_id") == row["ability_id"]]
            if not matched_actions:
                raise _Pending("动作派发来源没有对应的实际动作实例")
            if (any("landed" in item for item in row["scenario_calibrations"])
                    and len(matched_actions) != 1):
                raise _Pending("当前landed场景校准只在能力恰有一个动作实例时可无损表达；多实例需要逐action/ordinal绑定")
            selector = row["selector"]
            resolution = get_runtime_action_dispatch_profile(
                db_path, version_line=row["version_line"], **selector)
            record["source_verification"] = copy.deepcopy(resolution.get("source_verification"))
            coverage_manifest = resolution.get("coverage_manifest") or {}
            coverage_expectation = resolution.get("coverage_expectation")
            pinned_coverage = row.get("coverage_manifest_id") is not None
            if pinned_coverage and (resolution.get("coverage_status") != "ready"
                    or row["coverage_manifest_id"] != coverage_manifest.get("manifest_id")
                    or row["expected_coverage_manifest_fingerprint"]
                    != resolution.get("coverage_manifest_fingerprint")):
                raise _Pending("独立游戏组件清单身份或接受指纹已变化，需要重新核对")
            if pinned_coverage:
                record.update(coverage_status="ready",
                    coverage_manifest_id=coverage_manifest["manifest_id"],
                    coverage_manifest_fingerprint=resolution["coverage_manifest_fingerprint"],
                    coverage_manifest=copy.deepcopy(resolution["coverage_manifest"]),
                    coverage_expectation=copy.deepcopy(coverage_expectation),
                    coverage_issues=copy.deepcopy(resolution.get("coverage_issues", [])))
                record["expected_occurrences"] = [item["occurrence_id"] for item in
                    coverage_expectation.get("occurrences", [])]
            if not resolution.get("profile"):
                codes = ", ".join(issue.get("code", "unknown") for issue in resolution.get("issues", []))
                raise _Pending("新鲜动作档案解析未就绪：" + (codes or "unknown"))
            profile = resolution["profile"]
            profile_fp = fingerprint(profile)
            record.update(resolved_profile_id=profile["profile_id"], source_profile_fingerprint=profile_fp,
                          expected_components=[item["component_id"] for item in profile["emissions"]])
            if profile["profile_id"] != row["profile_id"]:
                raise _Pending("精确选择返回的档案ID与原生声明不一致")
            if profile_fp != row["expected_profile_fingerprint"]:
                raise _Pending("受审动作档案内容指纹已变化，需要重新核对")
            actor, actor_character_id = _identity_checks(plan, row, profile, ability)
            prefix = "dispatch." + fingerprint({"ability_id": row["ability_id"],
                "profile_id": profile["profile_id"], "selector": profile["selector"]})[:32]
            accepted_resolution = copy.deepcopy(resolution)
            if not pinned_coverage:
                accepted_resolution.update(coverage_status="unknown", coverage_manifest=None,
                    coverage_manifest_fingerprint=None, coverage_expectation=None,
                    coverage_issues=[])
            compiled = compile_action_dispatch_inputs(accepted_resolution,
                ability_id=row["ability_id"], actor=actor,
                actor_character_id=actor_character_id,
                ability_action_type_id=ability["action_type_id"],
                ability_variant=ability.get("variant", "default"),
                id_prefix=prefix, scenario_calibration=_scenario(row))
            record["source_verification"] = copy.deepcopy(compiled.get("source_verification"))
            record["application_adapter_audit"] = copy.deepcopy(
                compiled.get("application_adapter_audit", []))
            record["application_rule_audit"] = copy.deepcopy(
                compiled.get("application_rule_audit", []))
            record["target_contract"] = compiled.get("target_contract")
            if compiled.get("status") != "ready" or not compiled.get("fragments"):
                codes = ", ".join(issue.get("code", "unknown") for issue in compiled.get("issues", []))
                raise _Pending("受审档案当前不能无损映射到原生输入：" + (codes or "unknown"))
            if not _source_expectations_match(resolution, compiled):
                raise _Pending("物化片段与新鲜候选、来源指纹或周期间隔回执不一致")
            materialized_components = [item["component_id"] for item in
                                       compiled["fragments"]["prototype_requirements"]]
            if materialized_components != record["expected_components"]:
                raise _Pending("物化组件清单与受审档案完整发射清单不一致")
            record["native_fragments_fingerprint"] = fingerprint(
                _native_fragments(compiled["fragments"]))
            stages.append((record, compiled["fragments"]))
        except (EquipmentError, OSError, sqlite3.Error, KeyError, TypeError, ValueError) as exc:
            if isinstance(exc, PlanError):
                raise
            _issue(record, missing, "action_dispatch_source_pending", exc,
                   f"action_dispatch_sources[{index}]")

    if len(json.dumps(result.records, ensure_ascii=False, allow_nan=False,
                      separators=(",", ":")).encode()) > 2 * 1024 * 1024:
        for record in result.records:
            record.update(status="pending", materialized=None, source_verification=None)
            record["issues"] = [{"code": "action_dispatch_receipt_limit",
                "path": "action_dispatch_sources", "message": "动作派发回执超过2MiB，全部自动物化已撤回"}]
        emit_task_missing(
            missing, "action_dispatch_receipt_limit", "action_dispatch_sources",
            "动作派发回执超过2MiB，全部自动物化已撤回",
            "review_action_dispatch", consumer="action_dispatch:batch",
            category="engine_capability_missing", impact="critical",
            blocking_scope="engine_capability", retryable=False,
            repair_hint="缩小当前派发批次或扩展受审回执承载能力",
        )
        return result
    if len(stages) != len(rows):
        for record in result.records:
            if not record["issues"]:
                record["issues"].append({"code": "action_dispatch_peer_pending",
                    "path": "action_dispatch_sources." + record["ability_id"],
                    "message": "同批其他动作档案待处理，本项也未写入私有工作计划"})
        return result

    candidate = copy.deepcopy(plan)
    try:
        for record, fragments in stages:
            _append_unique(candidate, "action_event_profiles", fragments["action_event_profiles"],
                           lambda item: item["id"])
            _append_unique(candidate, "action_event_bindings", fragments["action_event_bindings"],
                           lambda item: item["ability_id"])
            candidate.setdefault("action_event_overrides", [])
            _append_unique(candidate["manual_template"], "hits", fragments["prototype_hits"],
                           lambda item: item["id"])
            _append_unique(candidate, "attack_metadata_sources", fragments["attack_metadata_sources"],
                           lambda item: item["action_event_profile_id"])
            _append_unique(candidate, "periodic_attack_sources", fragments["periodic_attack_sources"],
                           lambda item: item["action_event_profile_id"])
        from .schema import validate_plan
        validate_plan(candidate)
    except (PlanError, _Pending, KeyError, TypeError, ValueError) as exc:
        for record in result.records:
            record["issues"].append({"code": "action_dispatch_atomic_materialization_failed",
                "path": "action_dispatch_sources", "message": str(exc)[:1024]})
        emit_task_missing(
            missing, "action_dispatch_atomic_materialization_failed",
            "action_dispatch_sources", str(exc)[:1024],
            "review_action_dispatch", consumer="action_dispatch:batch",
            category="engine_capability_missing", impact="high",
            blocking_scope="engine_capability",
            repair_hint="处理整批身份冲突或补齐通用动作派发物化能力后重新组合",
        )
        return result

    plan.clear()
    plan.update(candidate)
    result.native_fragments = {key: [copy.deepcopy(row) for _, fragments in stages
                                     for row in fragments[key]]
                               for key in _NATIVE_FRAGMENT_KEYS}
    for record, fragments in stages:
        profile_ids = [item["id"] for item in fragments["action_event_profiles"]]
        hit_ids = [item["id"] for item in fragments["prototype_hits"]]
        record.update(status="ready", materialized={"profile_ids": profile_ids, "prototype_hit_ids": hit_ids,
            "component_ids": copy.deepcopy(record["expected_components"]),
            "occurrence_ids": copy.deepcopy(record["expected_occurrences"]),
            "ability_id": record["ability_id"], "target_contract": record["target_contract"]})
        result.generated_profile_ids.update(profile_ids)
        result.sources.append({"kind": "action_dispatch_profile", "ability_id": record["ability_id"],
            "profile_id": record["resolved_profile_id"],
            "source_profile_fingerprint": record["source_profile_fingerprint"],
            "coverage_manifest_fingerprint": record["coverage_manifest_fingerprint"],
            "source_verification": copy.deepcopy(record["source_verification"])})
    return result


def authorize_materialized_profiles(plan, action_records, generated_profile_ids):
    """Adopt fresh semantic fingerprints only for profiles generated this call.

    The reviewed dispatch row plus its exact fresh source receipts are the
    authority.  Manual profiles and profiles with any substantive compiler
    diagnostic remain subject to the ordinary explicit confirmation flow.
    """
    generated = set(generated_profile_ids)
    profiles = {row["id"]: row for row in plan.get("action_event_profiles", [])}
    adopted = []
    for record in action_records:
        profile_id = record.get("profile_id")
        confirmation = record.get("required_confirmation")
        codes = {issue.get("code") for issue in record.get("issues", [])}
        if (profile_id not in generated or not isinstance(confirmation, dict)
                or set(confirmation) != {"accepted_profile_fingerprint"}
                or confirmation["accepted_profile_fingerprint"] != record.get("profile_fingerprint")
                or "action_event_confirmation" not in codes
                or codes - {"action_event_confirmation", "periodic_action_group_pending"}):
            continue
        profiles[profile_id].update(copy.deepcopy(confirmation))
        adopted.append(profile_id)
    return sorted(adopted)


def build_action_dispatch_materialization(plan, prepared):
    """Freeze the private native fragments needed by offline verifiers.

    This is an execution receipt, not a user-plan patch.  It is built only
    after the generated profiles have passed the ordinary action-event
    compiler and received their fresh semantic fingerprint.
    """
    if not isinstance(prepared.native_fragments, dict):
        raise PlanError("动作派发私有物化片段不存在")
    native = copy.deepcopy(prepared.native_fragments)
    profile_ids = set(prepared.generated_profile_ids)
    hit_ids = {hit_id for record in prepared.records if record.get("status") == "ready"
               for hit_id in (record.get("materialized") or {}).get("prototype_hit_ids", [])}
    profiles = native["action_event_profiles"]
    current_profiles = {row.get("id"): row for row in plan.get("action_event_profiles", [])
                        if row.get("id") in profile_ids}
    for profile in profiles:
        current = current_profiles.get(profile.get("id"))
        expected = copy.deepcopy(current)
        if expected is None:
            raise PlanError("动作派发私有物化档案缺失")
        accepted = expected.pop("accepted_profile_fingerprint", None)
        baseline = copy.deepcopy(profile)
        baseline.pop("accepted_profile_fingerprint", None)
        if expected != baseline or not isinstance(accepted, str):
            raise PlanError("动作派发档案在原生编译外发生变化")
        profile["accepted_profile_fingerprint"] = accepted
    bindings = native["action_event_bindings"]
    hits = native["prototype_hits"]
    attack = native["attack_metadata_sources"]
    periodic = native["periodic_attack_sources"]
    for record in prepared.records:
        if record.get("status") != "ready" or fingerprint(
                _owned_fragments(native, record, normalize_confirmation=True)) != record.get(
                    "native_fragments_fingerprint"):
            raise PlanError("动作派发私有物化片段与原编译语义不一致")
    if ({row.get("id") for row in profiles} != profile_ids
            or {row.get("profile_id") for row in bindings} != profile_ids
            or {row.get("id") for row in hits} != hit_ids
            or {row.get("action_event_profile_id") for row in [*attack, *periodic]} != profile_ids):
        raise PlanError("动作派发私有物化清单不完整")
    payload = {
        "contract_id": "action-dispatch-materialization-v1",
        "action_dispatch_compilation_fingerprint": fingerprint(prepared.records),
        "action_event_profiles": profiles,
        "action_event_bindings": bindings,
        "action_event_overrides": [],
        "prototype_hits": hits,
        "attack_metadata_sources": attack,
        "periodic_attack_sources": periodic,
    }
    if len(json.dumps(payload, ensure_ascii=False, allow_nan=False,
                      separators=(",", ":")).encode()) > 2 * 1024 * 1024:
        raise PlanError("动作派发私有物化清单超过2MiB")
    return {**payload, "content_fingerprint": fingerprint(payload)}


def action_dispatch_execution_plan(plan, report):
    """Rebuild the private native plan from a frozen, integrity-checked receipt."""
    from .files import input_view

    # Validate against the complete native document (which includes the
    # required resolution fields), then return only its executable input view.
    work = copy.deepcopy(plan)
    manifest = report.get("action_dispatch_materialization")
    if manifest is None:
        return copy.deepcopy(input_view(work))
    payload = {key: copy.deepcopy(value) for key, value in manifest.items()
               if key != "content_fingerprint"}
    if (payload.get("contract_id") != "action-dispatch-materialization-v1"
            or manifest.get("content_fingerprint") != fingerprint(payload)
            or payload.get("action_dispatch_compilation_fingerprint")
            != fingerprint(report.get("action_dispatch_compilations", []))):
        raise ValueError("动作派发私有物化清单与编译回执不一致")
    ready = [record for record in report.get("action_dispatch_compilations", [])
             if record.get("status") == "ready"]
    profile_ids = {profile_id for record in ready
                   for profile_id in (record.get("materialized") or {}).get("profile_ids", [])}
    hit_ids = {hit_id for record in ready
               for hit_id in (record.get("materialized") or {}).get("prototype_hit_ids", [])}
    if ({row.get("id") for row in payload.get("action_event_profiles", [])} != profile_ids
            or {row.get("profile_id") for row in payload.get("action_event_bindings", [])} != profile_ids
            or payload.get("action_event_overrides") != []
            or {row.get("id") for row in payload.get("prototype_hits", [])} != hit_ids
            or {row.get("action_event_profile_id") for row in [
                *payload.get("attack_metadata_sources", []),
                *payload.get("periodic_attack_sources", [])]} != profile_ids):
        raise ValueError("动作派发私有物化清单缺少预期组件")
    for record in ready:
        if fingerprint(_owned_fragments(payload, record, normalize_confirmation=True)) != record.get(
                "native_fragments_fingerprint"):
            raise ValueError("动作派发私有物化清单与原编译语义不一致")
    try:
        _append_unique(work, "action_event_profiles", payload["action_event_profiles"],
                       lambda item: item["id"])
        _append_unique(work, "action_event_bindings", payload["action_event_bindings"],
                       lambda item: item["ability_id"])
        work.setdefault("action_event_overrides", [])
        _append_unique(work["manual_template"], "hits", payload["prototype_hits"],
                       lambda item: item["id"])
        _append_unique(work, "attack_metadata_sources", payload["attack_metadata_sources"],
                       lambda item: item["action_event_profile_id"])
        _append_unique(work, "periodic_attack_sources", payload["periodic_attack_sources"],
                       lambda item: item["action_event_profile_id"])
        from .schema import validate_plan
        validate_plan(work)
    except (PlanError, _Pending, KeyError, TypeError, ValueError) as exc:
        raise ValueError("动作派发私有物化清单无法重建") from exc
    return copy.deepcopy(input_view(work))
