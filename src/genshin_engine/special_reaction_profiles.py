"""Read-only profile catalog and explicit direct-damage input validation.

Catalog evidence never materializes a character action or mutates world state.
"""
from __future__ import annotations

import copy
import hashlib
import json
from functools import lru_cache
from importlib import resources
from typing import Any

from genshin_engine.special_reaction_contracts import (
    _MECHANICS_KEYS,
    _PROFILE_META_KEYS,
    DIRECT_ABILITY_BINDING_KEYS,
    DIRECT_LUNAR_ELEMENTS,
    DIRECT_LUNAR_SCALING_STATS,
    DIRECT_STELLAR_ELEMENT_BY_REACTION,
    DIRECT_STELLAR_SCALING_STATS,
    OPTIONAL_MECHANICS_KEYS,
    SPECIAL_REACTION_TYPES,
    _finite_number,
    _issue,
)


def _profile_hash(parameters: dict) -> str:
    payload = json.dumps(parameters, ensure_ascii=False, sort_keys=True,
                         separators=(",", ":"), allow_nan=False).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def direct_lunar_damage_profile_content_hash(profile: dict) -> str:
    """Return the immutable hash for one explicit direct Lunar ability profile.

    The hash deliberately covers the numeric payload and its evidence.  The
    runtime therefore cannot accept a reviewed profile and then receive a
    different multiplier, scaling stat, hit count, or component beside it.
    """
    if not isinstance(profile, dict):
        # This public validator deliberately reports every malformed profile
        # through the same ValueError contract, including a wrong root type.
        raise ValueError("直伤月曜能力profile必须是对象。")  # noqa: TRY004
    payload = copy.deepcopy(profile)
    payload.pop("profile_content_hash", None)
    return _profile_hash(payload)


def direct_stellar_damage_profile_content_hash(profile: dict) -> str:
    """Return the immutable hash for one Direct Stellar ability profile."""
    if not isinstance(profile, dict):
        raise ValueError("直伤星烁能力profile必须是对象。")  # noqa: TRY004
    payload = copy.deepcopy(profile)
    payload.pop("profile_content_hash", None)
    return _profile_hash(payload)


@lru_cache(maxsize=1)
def _load_reviewed_direct_special_damage_profiles() -> dict[tuple[str, int], dict]:
    """Load the private exact allow-list for confirmed special damage.

    An empty reviewed catalog is intentional: it lets explicit assumptions use
    the engine contract while preventing an arbitrary 64-hex string from being
    promoted to a database-reviewed profile before the corresponding ability
    row has passed data review.
    """
    path = resources.files("genshin_db").joinpath(
        "resources", "mechanics", "direct-special-damage-profiles.json")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError("无法读取运行时数据库中的星月能力直伤profile目录。") from exc
    if (not isinstance(data, dict)
            or set(data) != {"schema_version", "collection_id", "revision", "status", "profiles"}
            or data.get("schema_version") != 1
            or data.get("collection_id")
            != "native.direct-special-damage-ability-profiles.v1"
            or type(data.get("revision")) is not int or data["revision"] < 1
            or data.get("status") != "reviewed"
            or not isinstance(data.get("profiles"), list)):
        raise RuntimeError("运行时数据库中的星月能力直伤profile目录标识无效。")
    result: dict[tuple[str, int], dict] = {}
    for row in data["profiles"]:
        if not isinstance(row, dict):
            raise RuntimeError("星月能力直伤profile目录条目必须是对象。")  # noqa: TRY004
        profile_id, revision = row.get("profile_id"), row.get("profile_revision")
        if (not isinstance(profile_id, str) or not profile_id.strip()
                or type(revision) is not int or revision < 1
                or (profile_id, revision) in result):
            raise RuntimeError("星月能力直伤profile目录包含无效或重复身份。")
        result[(profile_id, revision)] = copy.deepcopy(row)
    return result


def load_reviewed_direct_special_damage_profiles() -> dict[tuple[str, int], dict]:
    """Return a detached view of the reviewed direct-damage catalog."""
    return copy.deepcopy(_load_reviewed_direct_special_damage_profiles())


def _validate_reviewed_direct_profile_registration(profile: dict, *, path: str) -> list[dict]:
    if profile.get("status") != "confirmed":
        return []
    key = (profile.get("profile_id"), profile.get("profile_revision"))
    reviewed = _load_reviewed_direct_special_damage_profiles().get(key)
    if reviewed != profile:
        return [_issue(
            "reviewed_profile",
            "confirmed能力直伤profile必须与运行时数据库受审目录中的完整条目一致。",
            path,
            profile_id=profile.get("profile_id"),
            profile_revision=profile.get("profile_revision"),
        )]
    return []


def _validate_direct_ability_binding(binding: Any, *, path: str) -> list[dict]:
    """Validate the finite v2 identity that ties a profile to one ability component."""
    if not isinstance(binding, dict) or set(binding) != DIRECT_ABILITY_BINDING_KEYS:
        return [_issue(
            "fields", "ability_binding必须完整绑定角色、技能、组件、变体、有效等级和数据库指纹。",
            path, expected_fields=sorted(DIRECT_ABILITY_BINDING_KEYS),
        )]
    issues: list[dict] = []
    for name in ("character_id", "ability_id", "component_id", "variant_id"):
        if not isinstance(binding.get(name), str) or not binding[name].strip():
            issues.append(_issue("value", f"{name}必须是非空字符串。", path+"."+name))
    level = binding.get("effective_level")
    if type(level) is not int or not 1 <= level <= 20:
        issues.append(_issue(
            "value", "effective_level必须是1至20的整数。", path+".effective_level"))
    fingerprint = binding.get("database_fingerprint")
    if (not isinstance(fingerprint, str) or len(fingerprint) != 64
            or any(char not in "0123456789abcdef" for char in fingerprint)):
        issues.append(_issue(
            "value", "database_fingerprint必须是64位小写SHA-256。",
            path+".database_fingerprint"))
    return issues


def validate_direct_lunar_damage_profile(profile: dict) -> list[dict]:
    """Validate one finite database/assumption profile for Direct Lunar damage."""
    path = "ability_profile"
    if not isinstance(profile, dict):
        return [_issue("type", "直伤月曜能力profile必须是对象。", path)]
    legacy_expected = {
        "profile_id", "profile_revision", "profile_content_hash", "authority",
        "status", "source", "source_refs", "parameters",
    }
    expected = legacy_expected | {"ability_binding"}
    issues: list[dict] = []
    if set(profile) == legacy_expected:
        issues.append(_issue(
            "ability_binding_required",
            "旧v1直伤月曜profile仅可识别为待迁移资料；执行必须补齐v2 ability_binding。",
            path+".ability_binding",
        ))
    elif set(profile) != expected:
        issues.append(_issue(
            "fields", "直伤月曜能力profile只能包含固定审计字段。", path,
            expected_fields=sorted(expected), actual_fields=sorted(profile)))
        return issues
    if not isinstance(profile.get("profile_id"), str) or not profile["profile_id"].strip():
        issues.append(_issue("value", "profile_id必须是非空字符串。", path+".profile_id"))
    if type(profile.get("profile_revision")) is not int or profile["profile_revision"] < 1:
        issues.append(_issue("value", "profile_revision必须是正整数。", path+".profile_revision"))
    content_hash = profile.get("profile_content_hash")
    if (not isinstance(content_hash, str) or len(content_hash) != 64
            or any(char not in "0123456789abcdef" for char in content_hash)):
        issues.append(_issue("value", "profile_content_hash必须是64位小写SHA-256。",
                             path+".profile_content_hash"))
    authority = profile.get("authority")
    status = profile.get("status")
    if authority not in ("reviewed_database_projection", "explicit_assumption"):
        issues.append(_issue(
            "authority", "authority必须为受审数据库投影或显式假设。", path+".authority"))
    if status not in ("confirmed", "assumed"):
        issues.append(_issue("status", "直伤月曜能力profile不得以pending进入运行时。",
                             path+".status"))
    if status == "confirmed" and authority != "reviewed_database_projection":
        issues.append(_issue(
            "authority", "confirmed直伤只接受受审数据库投影。", path+".authority"))
    if status == "assumed" and authority != "explicit_assumption":
        issues.append(_issue(
            "authority", "assumed直伤必须明确标记为显式假设。", path+".authority"))
    if not isinstance(profile.get("source"), str) or not profile["source"].strip():
        issues.append(_issue("value", "source必须是非空字符串。", path+".source"))
    refs = profile.get("source_refs")
    if (not isinstance(refs, list) or not refs
            or any(not isinstance(ref, dict) or not ref for ref in refs)):
        issues.append(_issue("value", "source_refs必须是非空对象数组。", path+".source_refs"))
    parameters = profile.get("parameters")
    parameter_keys = {
        "reaction_type", "element", "multiplier", "scaling_stat", "count", "component",
    }
    if not isinstance(parameters, dict) or set(parameters) != parameter_keys:
        issues.append(_issue(
            "fields", "parameters必须完整声明反应、元素、倍率、结算属性、次数和分项。",
            path+".parameters", expected_fields=sorted(parameter_keys)))
    else:
        reaction_type = parameters.get("reaction_type")
        if reaction_type not in DIRECT_LUNAR_ELEMENTS:
            issues.append(_issue(
                "value", "显式直伤接口只接收月感电、月绽放或月结晶。",
                path+".parameters.reaction_type",
                supported_reaction_types=sorted(DIRECT_LUNAR_ELEMENTS),
            ))
        else:
            expected_element = DIRECT_LUNAR_ELEMENTS[reaction_type]
            if parameters.get("element") != expected_element:
                issues.append(_issue(
                    "value", "直伤月曜元素必须与反应类型一致。",
                    path+".parameters.element", reaction_type=reaction_type,
                    expected_element=expected_element,
                ))
        if not _finite_number(parameters.get("multiplier"), positive=True):
            issues.append(_issue("value", "multiplier必须是有限正数。",
                                 path+".parameters.multiplier"))
        if parameters.get("scaling_stat") not in DIRECT_LUNAR_SCALING_STATS:
            issues.append(_issue(
                "value", "scaling_stat必须为atk、hp、def或em。",
                path+".parameters.scaling_stat"))
        if type(parameters.get("count")) is not int or not 1 <= parameters["count"] <= 100:
            issues.append(_issue("value", "count必须是1至100的整数。",
                                 path+".parameters.count"))
        if not isinstance(parameters.get("component"), str) or not parameters["component"].strip():
            issues.append(_issue("value", "component必须是非空字符串。",
                                 path+".parameters.component"))
    if "ability_binding" in profile:
        issues.extend(_validate_direct_ability_binding(
            profile["ability_binding"], path=path+".ability_binding"))
        if (isinstance(parameters, dict)
                and isinstance(profile.get("ability_binding"), dict)
                and profile["ability_binding"].get("component_id")
                != parameters.get("component")):
            issues.append(_issue(
                "binding", "能力绑定的component_id必须与数值分项component一致。",
                path+".ability_binding.component_id"))
    # Confirmation is a catalog property, including for the legacy v1 shape.
    # A self-declared source string and a content hash prove immutability, not
    # that the database row has actually passed project review.
    issues.extend(_validate_reviewed_direct_profile_registration(profile, path=path))
    if not issues and profile["profile_content_hash"] != direct_lunar_damage_profile_content_hash(profile):
        issues.append(_issue("hash", "直伤月曜能力profile哈希与内容不匹配。",
                             path+".profile_content_hash"))
    return issues


def validate_direct_stellar_damage_profile(profile: dict) -> list[dict]:
    """Validate one ability-bound Direct Stellar-Superconduct profile.

    The profile owns only the ability multiplier and identity.  Settled layers
    and the Stellar system factor are intentionally absent and must be sampled
    from the runtime at the event timestamp.
    """
    path = "ability_profile"
    if not isinstance(profile, dict):
        return [_issue("type", "直伤星烁能力profile必须是对象。", path)]
    expected = {
        "profile_id", "profile_revision", "profile_content_hash", "authority",
        "status", "source", "source_refs", "parameters", "ability_binding",
    }
    issues: list[dict] = []
    if set(profile) != expected:
        return [_issue(
            "fields", "直伤星烁能力profile只能包含固定审计字段。", path,
            expected_fields=sorted(expected), actual_fields=sorted(profile))]
    if not isinstance(profile.get("profile_id"), str) or not profile["profile_id"].strip():
        issues.append(_issue("value", "profile_id必须是非空字符串。", path+".profile_id"))
    if type(profile.get("profile_revision")) is not int or profile["profile_revision"] < 1:
        issues.append(_issue("value", "profile_revision必须是正整数。", path+".profile_revision"))
    content_hash = profile.get("profile_content_hash")
    if (not isinstance(content_hash, str) or len(content_hash) != 64
            or any(char not in "0123456789abcdef" for char in content_hash)):
        issues.append(_issue(
            "value", "profile_content_hash必须是64位小写SHA-256。",
            path+".profile_content_hash"))
    authority, status = profile.get("authority"), profile.get("status")
    if authority not in ("reviewed_database_projection", "explicit_assumption"):
        issues.append(_issue(
            "authority", "authority必须为受审数据库投影或显式假设。",
            path+".authority"))
    if status not in ("confirmed", "assumed"):
        issues.append(_issue(
            "status", "直伤星烁能力profile不得以pending进入运行时。", path+".status"))
    if status == "confirmed" and authority != "reviewed_database_projection":
        issues.append(_issue(
            "authority", "confirmed直伤只接受受审数据库投影。", path+".authority"))
    if status == "assumed" and authority != "explicit_assumption":
        issues.append(_issue(
            "authority", "assumed直伤必须明确标记为显式假设。", path+".authority"))
    if not isinstance(profile.get("source"), str) or not profile["source"].strip():
        issues.append(_issue("value", "source必须是非空字符串。", path+".source"))
    refs = profile.get("source_refs")
    if (not isinstance(refs, list) or not refs
            or any(not isinstance(ref, dict) or not ref for ref in refs)):
        issues.append(_issue(
            "value", "source_refs必须是非空对象数组。", path+".source_refs"))
    parameters = profile.get("parameters")
    parameter_keys = {
        "reaction_type", "element", "multiplier", "scaling_stat", "count", "component",
    }
    if not isinstance(parameters, dict) or set(parameters) != parameter_keys:
        issues.append(_issue(
            "fields", "parameters必须完整声明反应、元素、倍率、结算属性、次数和分项。",
            path+".parameters", expected_fields=sorted(parameter_keys)))
    else:
        if parameters.get("reaction_type") not in DIRECT_STELLAR_ELEMENT_BY_REACTION:
            issues.append(_issue(
                "value", "能力直伤支持stellar_superconduct或stellar_swirl。",
                path+".parameters.reaction_type"))
        if parameters.get("element") not in DIRECT_STELLAR_ELEMENT_BY_REACTION.get(parameters.get("reaction_type"), ()):
            issues.append(_issue(
                "value", "星超导直伤元素为cryo/electro；星扩散直伤元素为cryo/anemo。",
                path+".parameters.element"))
        if not _finite_number(parameters.get("multiplier"), positive=True):
            issues.append(_issue(
                "value", "multiplier必须是有限正数。", path+".parameters.multiplier"))
        if parameters.get("scaling_stat") not in DIRECT_STELLAR_SCALING_STATS:
            issues.append(_issue(
                "value", "scaling_stat必须为atk、hp、def或em。",
                path+".parameters.scaling_stat"))
        if type(parameters.get("count")) is not int or not 1 <= parameters["count"] <= 100:
            issues.append(_issue(
                "value", "count必须是1至100的整数。", path+".parameters.count"))
        if not isinstance(parameters.get("component"), str) or not parameters["component"].strip():
            issues.append(_issue(
                "value", "component必须是非空字符串。", path+".parameters.component"))
    binding = profile.get("ability_binding")
    issues.extend(_validate_direct_ability_binding(
        binding, path=path+".ability_binding"))
    if (isinstance(parameters, dict) and isinstance(binding, dict)
            and binding.get("component_id") != parameters.get("component")):
        issues.append(_issue(
            "binding", "能力绑定的component_id必须与数值分项component一致。",
            path+".ability_binding.component_id"))
    issues.extend(_validate_reviewed_direct_profile_registration(profile, path=path))
    if (not issues and profile["profile_content_hash"]
            != direct_stellar_damage_profile_content_hash(profile)):
        issues.append(_issue(
            "hash", "直伤星烁能力profile哈希与内容不匹配。",
            path+".profile_content_hash"))
    return issues


@lru_cache(maxsize=1)
def load_reviewed_special_reaction_profiles() -> dict:
    """Load and verify the compact reviewed profile set from the runtime DB."""
    path = resources.files("genshin_db").joinpath(
        "resources", "mechanics", "special-reaction-profiles.json")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError("无法读取运行时数据库中的星月反应profile。") from exc
    if (not isinstance(data, dict) or data.get("schema_version") != 1
            or data.get("collection_id") != "native.special-reactions.v1"
            or data.get("revision") != 1 or data.get("status") != "reviewed"):
        raise RuntimeError("运行时数据库中的星月反应profile集合标识无效。")
    profiles = data.get("profiles")
    if not isinstance(profiles, dict) or set(profiles) != set(SPECIAL_REACTION_TYPES):
        raise RuntimeError("运行时数据库必须完整包含五类受审星月反应profile。")
    for reaction_type, row in profiles.items():
        expected_fields = {
            "profile_id", "profile_revision", "profile_content_hash",
            "confirmed_ready", "parameters", "evidence_refs",
        }
        if not isinstance(row, dict) or set(row) != expected_fields:
            raise RuntimeError(f"{reaction_type}受审profile字段不完整。")
        parameter_keys = _MECHANICS_KEYS[reaction_type] - _PROFILE_META_KEYS - OPTIONAL_MECHANICS_KEYS.get(reaction_type, frozenset())
        parameters = row.get("parameters")
        evidence = row.get("evidence_refs")
        if (row.get("profile_id") != f"native.{reaction_type}.v1"
                or row.get("profile_revision") != 1
                or not isinstance(parameters, dict) or set(parameters) != parameter_keys
                or not isinstance(evidence, list) or not evidence
                or any(not isinstance(item, dict) or not item for item in evidence)):
            raise RuntimeError(f"{reaction_type}受审profile内容无效。")
        calculated_hash = _profile_hash({
            "reaction_type": reaction_type,
            "parameters": parameters,
            "evidence_refs": evidence,
        })
        if row.get("profile_content_hash") != calculated_hash:
            raise RuntimeError(f"{reaction_type}受审profile哈希不匹配。")
        if row.get("confirmed_ready") is not all(
                value is not None for value in parameters.values()):
            raise RuntimeError(f"{reaction_type}受审profile的confirmed_ready标记错误。")
    return copy.deepcopy(profiles)


REVIEWED_SPECIAL_REACTION_PROFILES = load_reviewed_special_reaction_profiles()
