"""Fresh inherent sources, gated by actual calibrated attack parents and fixed EM."""
from __future__ import annotations

import copy
import json
import math
import re
import sqlite3

from genshin_app.task_capability_producer import emit_task_missing
from genshin_engine.beneficiary_selection import possible_recipients
from genshin_engine.elemental import validate_elemental_model
from genshin_engine.elemental_schema import DYNAMIC_SWIRL_RULESET, FREEZE_RULESET
from genshin_engine.registry import BENEFIT_TYPES
from genshin_engine.runtime_passive_attribute_compiler import (
    PROFILE_ID as ATTRIBUTE_PROFILE,
)
from genshin_engine.runtime_passive_attribute_compiler import (
    compile_passive_attribute_source,
    passive_attribute_compiler_caps,
)
from genshin_engine.runtime_passive_benefit_compiler import (
    passive_benefit_compiler_caps,
)

MAX_SOURCES = 32
MAX_RECEIPT_BYTES = 8 * 1024 * 1024
PART_IDS = ("passive.dmg_bonus.hydro", "passive.dmg_bonus.cryo")
IDENTITY_COVERAGE = "explicit_passive_sources_and_provider_anemo_parents"


def _obj(properties, required=None):
    return {"type": "object", "properties": properties, "additionalProperties": False,
            "required": list(properties) if required is None else required}


def _native_schema(schema):
    """The small legacy App schema walker uses enum/anyOf rather than const/type[]."""
    result = copy.deepcopy(schema)
    if "const" in result:
        value = result.pop("const")
        result.update(enum=[value], type="boolean" if type(value) is bool else "integer" if type(value) is int else "string")
    if isinstance(result.get("type"), list):
        kinds = result.pop("type")
        return {"anyOf": [{**_native_schema(result), "type": kind} for kind in kinds]}
    if "properties" in result:
        result["properties"] = {k: _native_schema(v) for k, v in result["properties"].items()}
    return result


ID = {"type": "string", "minLength": 1, "maxLength": 128}
PROGRESSION_SCHEMA = _obj({"ascension": {"anyOf": [{"type": "null"}, {"type": "integer", "minimum": 0, "maximum": 6}]},
    "status": {"enum": ["pending", "assumed", "confirmed"]}, "source": {"type": "string", "maxLength": 4096}})
PASSIVE_SOURCE_SCHEMA = _obj({"id": ID, "actor": ID, "proud_skill_id": {"type": "integer", "minimum": 1},
    "skill_depot_id": {"type": "integer", "minimum": 1},
    "constellation": {"anyOf": [{"type": "null"}, {"type": "integer", "minimum": 0, "maximum": 6}]},
    "scenario": {"anyOf": [_native_schema(passive_benefit_compiler_caps()["scenario_schema"]),
                            _native_schema(passive_attribute_compiler_caps()["scenario_schema"])]},
    "accepted_fingerprint": {"anyOf": [{"type": "null"}, {"type": "string", "pattern": r"^[0-9a-f]{64}$"}]}},
    ["id", "actor", "proud_skill_id", "constellation", "scenario", "accepted_fingerprint"])


def validate_passive_sources(plan):
    from .schema import PlanError

    if "passive_sources" in plan and plan["plan_version"] != 3:
        raise PlanError("固有天赋来源仅适用于原生方案v3")
    sources = plan.get("passive_sources", [])
    if len(sources) > MAX_SOURCES or len({s["id"] for s in sources}) != len(sources):
        raise PlanError("固有来源ID须唯一且最多32项")
    actors = {c["id"] for c in plan["manual_template"]["characters"]}
    for source in sources:
        if source["actor"] not in actors:
            raise PlanError("固有来源引用未知角色")
        for key in ("id", "actor"):
            if not source[key].strip() or len(source[key]) > 128:
                raise PlanError("固有来源身份文本为空或超过128字符")
        accepted = source["accepted_fingerprint"]
        if accepted is not None and not re.fullmatch(r"[0-9a-f]{64}", accepted):
            raise PlanError("固有来源确认须为null或64位小写SHA256")
        if len(source["scenario"]["source"]) > 4096 or len(source["scenario"].get("target_id") or "") > 128:
            raise PlanError("固有来源场景文本超过上限")
    for equipment in plan["equipment_inputs"].values():
        if "progression" in equipment and len(equipment["progression"]["source"]) > 4096:
            raise PlanError("突破依据超过4096字符")


def _em_writes(plan, template, actor):
    paths = []
    for buff in template.get("buffs", []):
        if buff.get("target") not in (None, actor):
            continue
        for effect in buff["effects"]:
            if effect["zone"] == "em" or effect["zone"] in ("stat_add", "stat_pct") and effect["stat"] == "em":
                paths.append(f"buffs.{buff['id']}.{effect['id']}")
    for rule in (plan.get("mechanics_model") or {}).get("rules", []):
        possible = possible_recipients(rule, [actor])
        if actor not in possible:
            continue
        for effect in rule["effects"]:
            info = BENEFIT_TYPES.get(effect["benefit_type_id"], {})
            if info.get("zone") == "em" or info.get("zone") in ("stat_add", "stat_pct") and info.get("stat") == "em":
                paths.append(f"rules.{rule['id']}.{effect['id']}")
    for binding in plan["effect_bindings"]:
        for part in binding["parts"]:
            rule, delivery = part.get("rule") or {}, part.get("delivery") or {}
            if (part["state"] != "excluded" and rule.get("kind") == "stat_scale"
                    and delivery.get("kind") == "panel" and delivery.get("stat") == "em"
                    and delivery.get("target_character_id") == actor):
                paths.append(f"effect_bindings.{binding['binding_id']}.parts.{part['part_id']}")
    return sorted(set(paths))


def _identities(plan, actor):
    hits = {h["id"]: h for h in plan["manual_template"]["hits"]}
    abilities = {a["id"]: a for a in (plan.get("rotation_model") or {}).get("abilities", [])}
    profiles = {p["id"]: p for p in plan.get("action_event_profiles", [])}
    rows = []
    for field in ("hit_parameter_sources", "action_resource_sources", "attack_metadata_sources"):
        for source in plan.get(field, []):
            owner = (hits.get(source.get("hit_id"), {}).get("character") if field == "hit_parameter_sources" else
                     abilities.get(source.get("ability_id"), {}).get("character") if field == "action_resource_sources" else
                     hits.get(profiles.get(source.get("action_event_profile_id"), {}).get("prototype_hit_id"), {}).get("character"))
            if owner == actor:
                rows.append({"origin": field, "version_line": source["version_line"], **source["selection"]})
    for binding in plan["effect_bindings"]:
        owner = binding["owner"]
        if binding["provider_character_id"] == actor and owner["kind"] == "character_skill":
            rows.append({"origin": "effect_bindings", "character_id": owner["character_id"], "version_line": owner["version_line"]})
    return rows


def _proud_id(ref):
    if not isinstance(ref, dict):
        return None
    if ref.get("table") == "ProudSkillExcelConfigData":
        return (ref.get("key") or {}).get("proudSkillId")
    if ref.get("kind") in ("inherent_proud_skill", "passive_source"):
        return ref.get("proud_skill_id")
    return None


def _parent_proof(plan, template, actor, query, metadata_records, event_records):
    """Use fresh compiler outputs, then cover every potentially relevant descriptor."""
    from .action_event_schema import APPLICATION_KIND, KIND
    from .files import fingerprint

    errors, parents, dependencies, allowed = [], [], [], {}
    model = plan.get("mechanics_model") or {}
    elemental = model.get("elemental_model") or {}
    apps = {p["id"]: p for p in elemental.get("profiles", [])}
    profiles = {p["id"]: p for p in plan.get("action_event_profiles", [])}
    metadata = {r["action_event_profile_id"]: r for r in metadata_records}
    patterns = {p["id"]: p for p in model.get("hit_patterns", [])}
    actions = {a["id"]: a for a in (plan.get("rotation_model") or {}).get("actions", [])}
    abilities = {a["id"]: a for a in (plan.get("rotation_model") or {}).get("abilities", [])}
    hits = {h["id"]: h for h in template["hits"]}
    selected = query["selection"]
    for record in event_records:
        if record.get("actor") != actor:
            continue
        profile = profiles[record["profile_id"]]
        # Only the reviewed single E path proves passive eligibility. Other
        # shapes are still checked below for every actual provider application.
        expected_kind = APPLICATION_KIND if elemental.get('ruleset') == DYNAMIC_SWIRL_RULESET else KIND
        if profile.get("kind") != expected_kind:
            continue
        app = apps.get(profile["application_profile_id"], {})
        if app.get("element") != "anemo":
            continue
        meta = metadata.get(record["profile_id"])
        selection = ((meta or {}).get("query") or {}).get("selection", {})
        if (record["status"] != "ready" or not meta or meta["status"] != "ready"
                or profile["accepted_profile_fingerprint"] != record.get("profile_fingerprint")
                or not record.get("profile_fingerprint") or not record.get("generated")
                or meta["actor"] != actor or meta["prototype_hit_id"] != profile["prototype_hit_id"]
                or meta["application_profile_id"] != profile["application_profile_id"]
                or meta.get("compiled_fields", {}).get("element") != "anemo"
                or selection.get("character_id") != selected["character_id"]
                or selection.get("skill_depot_id") != selected["skill_depot_id"]
                or selection.get("constellation") != 0 or "skill" not in selection.get("matching_slots", [])
                or meta.get("snapshot") != query.get("snapshot")):
            errors.append("提供者风附着缺少同快照已接受C0/E攻击元信息父链：" + record["profile_id"])
            continue
        dependencies.append({"profile_id": record["profile_id"], "profile_fingerprint": record["profile_fingerprint"],
            "source_dependency_fingerprint": record["source_dependency_fingerprint"],
            "metadata_dependency_fingerprint": meta["metadata_dependency_fingerprint"],
            "metadata_source_fingerprint": meta["source_fingerprint"]})
        for generated in record["generated"]:
            pattern = patterns.get(generated["pattern_id"])
            action = actions.get(generated["action_id"])
            ability = abilities.get(generated["ability_id"])
            expected_application = {"profile_id": app["id"], "target_id": elemental["target"]["id"], "landed": generated["landed"]}
            if (not pattern or not action or not ability or ability["character"] != actor
                    or ability["action_type_id"] != "action.elemental_skill"
                    or action["ability_id"] != ability["id"] or action["repeat"] != {"mode": "once"}
                    or pattern["action_id"] != action["id"] or pattern["prototype_hit_id"] != profile["prototype_hit_id"]
                    or pattern.get("application") != expected_application
                    or pattern["offsets"] != [profile["calibration"]["offset_seconds"]]
                    or pattern["orders"] != [generated["order"]]
                    or generated["hit_id"] != pattern["id"] + ":1"
                    or generated["prototype_hit_id"] != profile["prototype_hit_id"]):
                errors.append("生成命中与实际动作/附着身份不一致：" + generated["hit_id"])
                continue
            allowed[pattern["id"]] = fingerprint(pattern)
            parents.append({"profile_id": record["profile_id"], **copy.deepcopy(generated),
                            "application_profile_id": app["id"], "pattern_fingerprint": fingerprint(pattern)})
    for kind in ("hit_events", "hit_patterns", "application_events"):
        for descriptor in model.get(kind, []):
            action = actions.get(descriptor.get("action_id"), {})
            ability = abilities.get(action.get("ability_id"), {})
            hit = hits.get(descriptor.get("hit_id", descriptor.get("prototype_hit_id")), {})
            owner = hit.get("character", ability.get("character"))
            if owner != actor:
                continue
            application = descriptor.get("application") or {}
            profile = apps.get(application.get("profile_id"), {})
            if not application or profile.get("application") == "none":
                continue
            if not profile or profile.get("application") == "unknown":
                errors.append("提供者有未核实附着，不能排除风扩散资格")
            elif profile.get("element") == "anemo" and (kind != "hit_patterns" or allowed.get(descriptor["id"]) != fingerprint(descriptor)):
                errors.append("提供者混入无受审父链的风附着：" + descriptor.get("id", descriptor.get("hit_id", "unknown")))
    if not parents:
        errors.append("没有可证明C0/E父链的实际提供者风附着动作")
    return errors, parents, dependencies


def _scope(plan, template, binding, query, metadata, events):
    actor, scenario = binding["actor"], binding["scenario"]
    gear = plan["equipment_inputs"].get(actor, {})
    char = next(c for c in template["characters"] if c["id"] == actor)
    progression = gear.get("progression") or {}
    elemental = (plan.get("mechanics_model") or {}).get("elemental_model") or {}
    errors = []
    dynamic = scenario['profile_id'] == ATTRIBUTE_PROFILE
    if gear.get("mode") != "panel" or gear.get("panel_policy") != "static_included":
        errors.append("固有增伤首片只接受明确给定且已含常驻属性的提供者面板")
    if binding["constellation"] != 0 or char["level"] != 90 or progression.get("ascension") != 6:
        errors.append("固有增伤执行首片仅接受C0、90级和显式突破6")
    if progression.get("status") not in ("assumed", "confirmed") or not progression.get("source", "").strip():
        errors.append("提供者突破声明尚待核实或缺少依据")
    if query.get("unlock", {}).get("status") != "eligible_under_declared_progression":
        errors.append("当前明确突破未通过所选固有来源门槛")
    em = char["stats"].get("em")
    if type(em) not in (int, float) or not math.isfinite(em) or em < 0:
        errors.append("提供者需要有限非负的固定精通面板")
    writes = _em_writes(plan, template, actor)
    if writes and not dynamic:
        errors.append("存在潜在精通写入或已知比例转化，固定NONEXTRA相等口径不成立")
    if elemental.get("ruleset") != (DYNAMIC_SWIRL_RULESET if dynamic else FREEZE_RULESET) or validate_elemental_model(elemental):
        errors.append("动态属性固有来源需要明确v3普通扩散模型" if dynamic else "固有增伤需要已接受的普通冻结参考模型")
    causal = None
    if dynamic:
        if ((plan.get('mechanics_model') or {}).get('attribute_model') or {}).get('contract') != 'attribute-ledger-v2':
            errors.append('动态来源要求显式attribute-ledger-v2；不会升级旧属性合同或猜测初始NONEXTRA')
        from .passive_attribute_scope import inspect_nonextra_sampling
        causal = inspect_nonextra_sampling(plan, template, actor)
        errors.extend(row['message'] for row in causal['issues'])
    if scenario.get("target_id") != (elemental.get("target") or {}).get("id") or scenario.get("target_id") is None:
        errors.append("固有来源原目标与实际参考模型不一致")
    targets = {d.get("application", {}).get("target_id") for k in ("hit_events", "hit_patterns", "application_events")
               for d in (plan.get("mechanics_model") or {}).get(k, []) if d.get("application")}
    if targets - {(elemental.get("target") or {}).get("id")}:
        errors.append("固有增伤不支持另一目标或传播来源")
    if any(h["damage_system"] not in ("classic", "transformative") for h in template["hits"]):
        errors.append("固有增伤首片不支持月曜或星烁伤害系统")
    identities = _identities(plan, actor)
    for identity in identities:
        if (identity["character_id"] != query["selection"]["character_id"]
                or identity["version_line"] != query["snapshot"]["version_line"]
                or identity.get("constellation", 0) != binding["constellation"]
                or identity.get("skill_depot_id", query["selection"]["skill_depot_id"]) != query["selection"]["skill_depot_id"]):
            errors.append("固有来源与当前角色的明确角色/资料线/命座/技能组冲突")
    for other in plan.get("passive_sources", []):
        if other["id"] != binding["id"] and other["proud_skill_id"] == binding["proud_skill_id"]:
            errors.append("同一固有增伤存在重复来源或多提供者竞争")
    for other in plan["effect_bindings"]:
        if any(_proud_id(r) == binding["proud_skill_id"] for r in other["source_refs"]):
            errors.append("装备/手工效果分项已声明同一固有来源")
    for buff in plan["manual_template"].get("buffs", []):
        ext = buff.get("extensions") or {}
        if any(_proud_id(r) == binding["proud_skill_id"] for r in ext.get("source_refs", [])) or ext.get("source_id", "").startswith(binding["id"] + ":"):
            errors.append("手工增益已交付同一固有来源")
    parent_errors, parents, dependencies = _parent_proof(plan, template, actor, query, metadata, events)
    errors.extend(parent_errors)
    receivers = sorted(c["id"] for c in template["characters"])
    proof = {"status": "pending" if errors else "ready", "identity_coverage": IDENTITY_COVERAGE,
        "target_id": scenario.get("target_id"), "provider_parent_events": parents,
        "checked_receivers": receivers, "progression": copy.deepcopy(progression), "identities": identities,
        "dynamic_write_scan": writes, "known_conversion_scan": writes, "issues": errors}
    acceptance = {"character_ref": gear.get("character_ref"), "progression": progression,
        "constellation": binding["constellation"], "level": char["level"], "panel_em": em,
        "checked_receivers": receivers, "parent_dependencies": sorted(dependencies, key=lambda r: r["profile_id"])}
    if dynamic:
        from .files import context_view, fingerprint
        proof['attribute_sampling_qualification'] = causal
        acceptance['attribute_input_fingerprint'] = fingerprint(context_view({
            'mechanics_model': plan['mechanics_model'], 'manual_buffs': template.get('buffs', [])}))
    return proof, acceptance


def compile_passive_sources(plan, template, db_path, *, attack_metadata_compilations=(), action_event_compilations=(), missing):
    bindings = plan.get("passive_sources", [])
    if not bindings:
        return None, [], []
    from genshin_db.runtime_passive_source import get_runtime_passive_source_profile
    from genshin_engine.runtime_passive_benefit_compiler import (
        compile_passive_benefit_source,
    )

    from .files import fingerprint
    from .schema import PlanError

    records, stages, sources, cache = [], [], [], {}
    receipt_bytes = 2  # Canonical UTF-8 JSON array brackets; commas counted below.
    for binding in bindings:
        dynamic = binding['scenario']['profile_id'] == ATTRIBUTE_PROFILE
        caps = passive_attribute_compiler_caps() if dynamic else passive_benefit_compiler_caps()
        record = {"id": binding["id"], "actor": binding["actor"], "proud_skill_id": binding["proud_skill_id"],
            "compiler": caps["compiler"], "profile_id": binding["scenario"]["profile_id"],
            "status": "pending", "source": None, "source_fingerprint": None, "unlock": None,
            "unlock_fingerprint": None, "whole_source_status": "partial", "branches": [], "coverage": None,
            "execution_binding": None, "stat_basis": None, "scope": None, "scope_fingerprint": None,
            "required_confirmation": None, "accepted_fingerprint": binding["accepted_fingerprint"],
            "rule_ids": [], "parts": [], "rules": [], "initial_rules": [], "issues": []}
        records.append(record)

        def issue(code, message, *, binding=binding, record=record):
            item = {"code": code, "path": "passive_sources." + binding["id"], "message": str(message)[:1024]}
            record["issues"].append(item)
            emit_task_missing(
                missing, code, item["path"], item["message"],
                "review_passive_source",
                consumer="passive_source:" + binding["id"],
                category="knowledge_missing",
                blocking_scope="agent_materialization",
                repair_hint="核对固有天赋来源、解锁状态、父动作范围、属性取样和确认指纹",
            )

        try:
            gear = plan["equipment_inputs"].get(binding["actor"], {})
            ref = gear.get("character_ref")
            if not ref:
                issue("passive_identity_pending", "固有来源需要提供者明确的数据库角色引用")
                continue
            character = next(c for c in template["characters"] if c["id"] == binding["actor"])
            arguments = {"character_id": ref["db_character_id"], "proud_skill_id": binding["proud_skill_id"],
                "version_line": ref["version_line"], "character_level": character["level"]}
            if binding.get("skill_depot_id") is not None:
                arguments["skill_depot_id"] = binding["skill_depot_id"]
            progression = gear.get("progression") or {}
            if progression.get("ascension") is not None:
                arguments["ascension"] = progression["ascension"]
            key = fingerprint(arguments)
            if key not in cache:
                cache[key] = get_runtime_passive_source_profile(db_path, **arguments)
            query = copy.deepcopy(cache[key])
            record.update(source=query, source_fingerprint=query.get("source_fingerprint"),
                          unlock=copy.deepcopy(query.get("unlock")), unlock_fingerprint=query.get("unlock_fingerprint"))
            compiler = compile_passive_attribute_source if dynamic else compile_passive_benefit_source
            compiled = compiler(query, owner=binding["actor"], source_id=binding["id"], scenario=binding["scenario"])
            if dynamic:
                record['scenario'] = copy.deepcopy(binding['scenario'])
                record['attribute_evidence'] = copy.deepcopy(compiled.get('attribute_evidence'))
            for field in ("branches", "coverage", "coverage_certificate", "execution_binding", "raw_sources", "provenance", "whole_source_status",
                          "projection_status", "semantic_assumptions", "gameplay_semantics_status", "assumptions_fingerprint", "execution_fingerprint"):
                if field in compiled:
                    record[field] = copy.deepcopy(compiled[field])
            record["parts"] = copy.deepcopy(compiled.get("parts", []))
            record["rules"] = copy.deepcopy(compiled.get("rules", []))
            record["initial_rules"] = copy.deepcopy(compiled.get("initial_rules", []))
            record["rule_ids"] = [r["id"] for r in compiled.get("rules", [])]
            if compiled.get("status") != "ready":
                for found in compiled.get("issues", []):
                    issue("passive_source_pending", found.get("message", found.get("code", "来源尚待核实")))
                if not record["issues"]:
                    issue("passive_source_pending", "固有来源尚未通过局部语义编译")
                continue
            if {p["part_id"] for p in compiled["parts"]} != set(PART_IDS) or len(compiled["rules"]) != 2:
                issue("passive_delivery_invalid", "当前固有来源必须原子交付两个独立元素部件")
                continue
            proof, context = _scope(plan, template, binding, query, attack_metadata_compilations, action_event_compilations)
            record.update(scope=proof, scope_fingerprint=fingerprint(proof))
            record["stat_basis"] = {"raw_property": "FIGHT_PROP_NONEXTRA_ELEMENT_MASTERY", "execution_stat": "em",
                "mode": binding["scenario"]["stat_basis"], "status": binding["scenario"]["status"], "source": binding["scenario"]["source"],
                "panel_value": character["stats"].get("em"), "gameplay_interpretation": "declared_not_verified",
                "dynamic_write_scan": proof["dynamic_write_scan"], "known_conversion_scan": proof["known_conversion_scan"]}
            for error in proof["issues"]:
                issue("passive_scope_pending", error)
            if dynamic:
                for found in proof['attribute_sampling_qualification']['issues']:
                    issue(found['code'], found['message'])
            if record["issues"]:
                continue
            native_model = plan["mechanics_model"]
            known_rules = {r["id"] for r in native_model["rules"]}
            known_sources = {r["source_id"] for r in native_model["rules"]}
            if known_rules & set(record["rule_ids"]) or known_sources & {r["source_id"] for r in compiled["rules"]}:
                issue("passive_duplicate_delivery", "固有来源与已有规则重复交付")
                continue
            acceptance = {"compiler": caps, "source_fingerprint": query["source_fingerprint"],
                "unlock_fingerprint": query["unlock_fingerprint"], "actor": binding["actor"], "scenario": binding["scenario"],
                "context": context, "rules": compiled["rules"], "parts": compiled["parts"]}
            if dynamic:
                acceptance['attribute_evidence'] = compiled['attribute_evidence']
            accepted = fingerprint(acceptance)
            record["required_confirmation"] = {"accepted_fingerprint": accepted}
            if binding["accepted_fingerprint"] != accepted:
                issue("passive_confirmation_required", "请接受本次固有来源、属性账读取及已核对父链范围" if dynamic else "请接受本次固有来源、固定精通口径与已核对父链范围")
                continue
            record["status"] = "ready"
            stages.append(compiled)
            sources.append({"kind": "passive_source", "id": binding["id"], "source_fingerprint": query["source_fingerprint"],
                            "accepted_fingerprint": accepted, "scope_fingerprint": record["scope_fingerprint"]})
        except (ValueError, OSError, sqlite3.Error, KeyError, TypeError) as exc:
            if isinstance(exc, PlanError):
                raise
            issue("passive_source_pending", exc)
        finally:
            receipt_bytes += len(json.dumps(record, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode("utf8"))
            receipt_bytes += int(len(records) > 1)
            if receipt_bytes > MAX_RECEIPT_BYTES:
                raise PlanError("固有来源完整回执超过8MiB，未交付部分模型", "passive_receipt_limit")
    if len(json.dumps(records, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode()) > MAX_RECEIPT_BYTES:
        raise PlanError("固有来源完整回执超过8MiB，未交付部分模型", "passive_receipt_limit")
    if any(record["status"] != "ready" for record in records):
        for record in records:
            if record["status"] == "ready":
                record["status"] = "pending"
                record["issues"].append({"code": "passive_peer_pending", "message": "其他固有来源尚未就绪，整组未交付"})
            record["preview_parts"], record["preview_rules"] = record["parts"], record["rules"]
            record.update(parts=[], rules=[], initial_rules=[], rule_ids=[])
        if len(json.dumps(records, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode("utf8")) > MAX_RECEIPT_BYTES:
            raise PlanError("固有来源完整回执超过8MiB，未交付部分模型", "passive_receipt_limit")
        return None, records, []
    model = copy.deepcopy(plan["mechanics_model"])
    for compiled in stages:
        model["rules"].extend(copy.deepcopy(compiled["rules"]))
        model["initial_state"]["rules"].extend(copy.deepcopy(compiled["initial_rules"]))
    return model, records, sources
