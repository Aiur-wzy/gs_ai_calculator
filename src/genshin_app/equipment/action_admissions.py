"""Compile reviewed static action gate mappings into a detached rotation model."""
from __future__ import annotations

import copy
import sqlite3

from genshin_app.task_capability_producer import emit_task_missing
from genshin_db.equipment import EquipmentError
from genshin_db.runtime_action_admission import ActionAdmissionError
from genshin_db.runtime_catalog import fingerprint

from .action_admission_schema import CONTEXT, validate_action_admission_bindings
from .schema import PlanError

COMPILER = "reviewed-action-admission-compiler-v1"
MARKER = "static-action-admission-source-v1"


def capabilities():
    return {
        "compiler": COMPILER,
        "field": "action_admission_sources",
        "context_field": "action_admission_context",
        "max_bindings": 64,
        "native_declarations": ["variant", "action_type_id"],
        "owned_fields": [
            "cooldown.group", "cooldown.duration", "cooldown.start", "cooldown.recovery",
            "cooldown.charges", "energy.required", "energy.amount", "energy.delivery",
        ],
        "scope": "release/C0/default单能力、单次数、正值固定耗能元素爆发",
        "confirmation": "accepted_fingerprint",
        "initial_state": "exactly_one_explicit_initial_cooldowns_row_required",
        "context": copy.deepcopy(CONTEXT),
        "unsupported": ["elemental_skill", "switch", "aliases", "multi_charge", "capacity_combination"],
    }


def _reject(message, reason="action_admission_ownership", details=None):
    raise PlanError(message, reason, details)


def compile_action_admissions(plan, db_path, missing):
    validate_action_admission_bindings(plan)
    bindings = plan.get("action_admission_sources", [])
    if not bindings:
        return None, [], []
    rotation = copy.deepcopy(plan.get("rotation_model"))
    if rotation is None:
        for binding in bindings:
            emit_task_missing(
                missing, "missing_input",
                "action_admission_sources." + binding["id"],
                "动作接纳来源需要已有动作模型", "review_action_admission",
                consumer="action_admission:" + binding["id"],
                category="core_data_missing",
                repair_hint="先建立当前方案动作模型，再重新编译该动作接纳来源",
            )
        return None, [{"id": binding["id"], "ability_id": binding["ability_id"], "status": "pending",
                       "compiler": COMPILER, "issues": [{"code": "missing_input", "message": "动作模型缺失"}]}
                      for binding in bindings], []

    abilities = {ability["id"]: ability for ability in rotation["abilities"]}
    actors = {actor["id"]: actor for actor in rotation["characters"]}
    template_actors = {actor["id"] for actor in plan["manual_template"]["characters"]}
    actions = rotation["actions"]
    context = plan.get("action_admission_context")
    records, sources = [], []

    for binding in bindings:
        aid = binding["ability_id"]
        ability = abilities[aid]
        actor = ability["character"]
        record = {
            "id": binding["id"],
            "ability_id": aid,
            "actor": actor,
            "compiler": COMPILER,
            "status": "pending",
            "selection": copy.deepcopy(binding["selection"]),
            "variant": binding["variant"],
            "action_type_id": ability["action_type_id"],
            "model_ref": copy.deepcopy(binding["model_ref"]),
            "context": copy.deepcopy(context),
            "issues": [],
            "profile": None,
            "required_confirmation": None,
            "source_fingerprint": None,
            "scope_fingerprint": None,
            "static_certificate": None,
            "compiled_values": None,
        }
        records.append(record)

        def issue(code, message, *, current=record, source=binding):
            current["issues"].append({"code": code, "message": message})
            emit_task_missing(
                missing, code, "action_admission_sources." + source["id"],
                message, "review_action_admission",
                consumer="action_admission:" + source["id"],
                category=("knowledge_missing" if code in {
                    "action_admission_context_pending", "source_changed"
                } else "core_data_missing"),
                repair_hint="核对当前动作、场景、来源和初始恢复状态后重新编译",
            )

        if actor not in actors or actor not in template_actors:
            _reject("动作接纳角色不在动作模型或伤害模板中", "character_identity", aid)
        selected_actions = sorted((row for row in actions if row["ability_id"] == aid), key=lambda row: row["id"])
        if not selected_actions:
            issue("missing_input", "动作接纳能力尚没有实际动作")
        if context != CONTEXT:
            issue("action_admission_context_pending", "请选择明确的普通开放世界动作接纳场景")

        try:
            from genshin_db.runtime_action_admission import get_action_admission_profile

            profile = get_action_admission_profile(
                db_path, binding["version_line"], selection=binding["selection"],
                variant=binding["variant"], action_type_id=ability["action_type_id"],
                model_ref=binding["model_ref"])
        except (ActionAdmissionError, EquipmentError, ValueError, OSError, sqlite3.Error) as exc:
            issue(getattr(exc, "code", "action_admission_pending"), str(exc))
            continue
        record["profile"] = profile
        if profile["status"] != "ready":
            issue("action_admission_pending", "当前受审动作接纳模型不可用：" + str(profile["reason"]))
            continue

        values = profile["compiled_values"]
        group = values["cooldown"]["group"]
        aliases = [other["id"] for other in rotation["abilities"] if other["id"] != aid
                   and other.get("character") == actor and isinstance(other.get("cooldown"), dict)
                   and other["cooldown"].get("group") == group]
        if aliases:
            _reject("动作接纳首片不支持共享冷却组或能力别名", "action_admission_alias", aliases)
        if any(event.get("type") == "cooldown_change" and event.get("character") == actor
               and event.get("group") == group for event in rotation.get("events", [])):
            _reject("动作接纳首片不支持受管组的动态冷却修改", "action_admission_scope")
        initial = [row for row in rotation.get("initial_cooldowns", [])
                   if row.get("character") == actor and row.get("group") == group]
        if len(initial) != 1:
            issue("action_admission_initial_pending", "受管冷却组必须且只能有一条显式initial_cooldowns；[]才表示全部就绪")
            initial_row = None
        else:
            initial_row = copy.deepcopy(initial[0])
            if len(initial_row["ready_in"]) > values["cooldown"]["charges"]:
                _reject("动作接纳初始恢复队列超过基础次数", "action_admission_initial")

        scope = {
            "compiler": COMPILER,
            "binding": {key: copy.deepcopy(value) for key, value in binding.items() if key != "accepted_fingerprint"},
            "ability": {key: copy.deepcopy(ability.get(key)) for key in ("id", "character", "action_type_id", "variant", "status", "source")},
            "action_ids": [row["id"] for row in selected_actions],
            "initial_cooldown": initial_row,
            "context": copy.deepcopy(context),
            "aliases": [],
            "capacity_dependencies": [],
            "profile_source_fingerprint": profile["source_fingerprint"],
        }
        scope_fp = fingerprint(scope)
        source_fp = fingerprint({"profile_source_fingerprint": profile["source_fingerprint"],
                                 "scope_fingerprint": scope_fp})
        record.update(scope_fingerprint=scope_fp, source_fingerprint=source_fp,
                      required_confirmation={"accepted_fingerprint": source_fp})
        if binding["accepted_fingerprint"] != source_fp:
            issue("source_changed", "请核对当前模型、基础资源、动作集合、场景与初始恢复队列后确认")
        if record["issues"]:
            continue

        certificate = {
            "contract": "static-action-admission-certificate-v1",
            "binding_id": binding["id"],
            "model_ref": copy.deepcopy(binding["model_ref"]),
            "model_receipt": copy.deepcopy(profile["model_receipt"]),
            "snapshot_id": profile["snapshot"]["snapshot_id"],
            "resource_fingerprint": profile["resource_profile"]["resource_fingerprint"],
            "source_fingerprint": source_fp,
            "scope_fingerprint": scope_fp,
            "selection": copy.deepcopy(binding["selection"]),
            "variant": binding["variant"],
            "action_type_id": ability["action_type_id"],
            "field_authority": copy.deepcopy(profile["field_authority"]),
            "compiled_values": copy.deepcopy(values),
            "declared_not_verified": copy.deepcopy(profile["declared_not_verified"]),
            "ability_id": aid,
            "action_ids": [row["id"] for row in selected_actions],
            "initial_cooldown": initial_row,
            "context": copy.deepcopy(context),
            "aliases": [],
            "capacity_dependencies": [],
        }
        static_fp = fingerprint(certificate)
        certificate["static_certificate_fingerprint"] = static_fp
        marker = {"contract": MARKER, "binding_id": binding["id"],
                  "model_ref": copy.deepcopy(binding["model_ref"]), "source_fingerprint": source_fp,
                  "static_certificate_fingerprint": static_fp}
        ability.update(cooldown=copy.deepcopy(values["cooldown"]), energy=copy.deepcopy(values["energy"]),
                       action_admission_source=marker)
        record.update(status="ready", static_certificate=certificate,
                      compiled_values={**copy.deepcopy(values), "action_admission_source": marker})
        record["compilation_fingerprint"] = fingerprint(record)
        sources.append({"kind": "action_admission", "id": binding["id"], "ability_id": aid,
                        "source_fingerprint": source_fp, "static_certificate_fingerprint": static_fp,
                        "compilation_fingerprint": record["compilation_fingerprint"]})
    return rotation, records, sources


__all__ = ["COMPILER", "MARKER", "capabilities", "compile_action_admissions"]
