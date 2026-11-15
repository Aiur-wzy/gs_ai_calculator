"""Exact, non-executing catalog of versioned upstream runtime JSON sources."""
from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path

from genshin_db.equipment import EquipmentError
from genshin_db.raw_store import RAW_SOURCES

SCHEMA_VERSION = 1
PROFILE_ID = "upstream-combat-catalog-v1"
PROFILE_VERSION = 1
INDEXER_VERSION = "7"
SUPPORTED_INDEXER_VERSIONS = ("2", "3", "4", "5", "6", "7")
GADGET_EXCEL_PATH = "ExcelBinOutput/GadgetExcelConfigData.json"
MATERIAL_EXCEL_PATH = "ExcelBinOutput/MaterialExcelConfigData.json"
GLOBAL_COMBAT_PATH = "BinOutput/Common/ConfigGlobalCombat.json"
GADGET_NAME_MAX_LENGTH = 256
GADGET_ID_MAX = 2**53 - 1
MATERIAL_ID_MAX = 2**53 - 1
MAX_SOURCE_BYTES = 64 * 1024 * 1024
MAX_TOTAL_BYTES = 2 * 1024 * 1024 * 1024
MAX_SOURCE_FILES = 20000
DIRECTORY_FAMILIES = {
    "talent": "BinOutput/Talent", "ability": "BinOutput/Ability",
    "avatar": "BinOutput/Avatar", "gadget": "BinOutput/Gadget",
    "animator": "BinOutput/AnimatorConfig",
}
EXACT_FAMILIES = {
    "global_combat": [GLOBAL_COMBAT_PATH],
    "ability_path": ["BinOutput/AbilityPath/AbilityPathData.json"],
    "animation_pattern": ["BinOutput/AnimPattern/AnimPatternData.json"],
    "public_config": [f"ExcelBinOutput/{name}ExcelConfigData.json" for name in
                      ("AttackAttenuation", "ElementState", "ElementCoeff", "ReactionEnergy")],
    "excel": [p for p in RAW_SOURCES if not p.startswith("TextMap/")],
    "text_map": [p for p in RAW_SOURCES if p.startswith("TextMap/")],
    "gadget_excel": [GADGET_EXCEL_PATH],
    "material_excel": [MATERIAL_EXCEL_PATH],
}

# These source families improve automatic topology/timing reconstruction, but
# they are not a database identity or startup prerequisite.  Upstream mirrors
# do not always publish them for every snapshot.  Their absence must therefore
# be represented as an explicit unavailable capability instead of rejecting an
# otherwise self-consistent database build.
OPTIONAL_DIRECTORY_FAMILIES = frozenset({"gadget", "animator"})
OPTIONAL_EXACT_FAMILIES = frozenset({"global_combat", "animation_pattern"})
SOURCE_CAPABILITY_CONTRACT = "genshin-runtime-source-capabilities-v1"


class RuntimeConfigError(EquipmentError):
    """Compatible with existing database HTTP/CLI error handling."""


def _normalized(profile, payload, kind):
    try:
        return profile.normalize(payload, kind=kind)
    except (ValueError, TypeError, KeyError) as exc:
        raise RuntimeConfigError("运行配置字段映射失败: " + str(exc), "dataset_invalid", 500) from exc


def _raw_pointer(profile, payload, pointer, kind):
    try:
        return profile.raw_pointer(payload, pointer, kind=kind)
    except (ValueError, TypeError, KeyError, IndexError) as exc:
        raise RuntimeConfigError("运行配置字段映射指针无效: " + str(exc), "dataset_invalid", 500) from exc


def dumps(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def fingerprint(value):
    return hashlib.sha256(dumps(value).encode("utf-8")).hexdigest()


def _object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise RuntimeConfigError(f"运行配置含重复JSON键: {key}", "dataset_invalid", 500)
        result[key] = value
    return result


def _walk_values(value):
    """Traverse in the same recursive order without constructing unused pointers."""
    yield value
    if isinstance(value, dict):
        for item in value.values():
            yield from _walk_values(item)
    elif isinstance(value, list):
        for item in value:
            yield from _walk_values(item)


def parse_source(payload):
    def bad_constant(value):
        raise ValueError(f"non-finite number: {value}")
    try:
        value = json.loads(payload.decode("utf-8-sig") if isinstance(payload, bytes) else payload,
                           object_pairs_hook=_object, parse_constant=bad_constant)
        for node in _walk_values(value):
            if isinstance(node, float) and not math.isfinite(node):
                raise ValueError("non-finite number")
        return value
    except (ValueError, UnicodeError, RecursionError) as exc:
        raise RuntimeConfigError(f"运行配置JSON无效: {exc}", "dataset_invalid", 500) from exc


def escape(token):
    return str(token).replace("~", "~0").replace("/", "~1")


def walk(value, pointer=""):
    yield pointer, value
    if isinstance(value, dict):
        for key, item in value.items():
            yield from walk(item, pointer + "/" + escape(key))
    elif isinstance(value, list):
        for i, item in enumerate(value):
            yield from walk(item, pointer + f"/{i}")


def resolve_pointer(value, pointer):
    if not isinstance(pointer, str) or (pointer and not pointer.startswith("/")):
        raise ValueError("json_pointer 必须为空或以 / 开头")
    for token in pointer.split("/")[1:]:
        if "~" in token.replace("~0", "").replace("~1", ""):
            raise ValueError("非法JSON pointer转义")
        token = token.replace("~1", "/").replace("~0", "~")
        if isinstance(value, list):
            if not token.isascii() or not token.isdecimal() or (len(token) > 1 and token[0] == "0"):
                raise ValueError("非法JSON数组索引")
            value = value[int(token)]
        else:
            value = value[token]
    return value


def inspect_runtime_source_layout(repo_dir: Path, *, profile=None):
    """Report source-family capabilities without reading source payloads.

    Core families still fail closed.  Families explicitly listed as optional
    are reported as ``unavailable`` when absent; this keeps the evidence honest
    while allowing the remaining, independently useful runtime catalog to be
    built.  A missing optional family is never reported as ready.
    """

    if profile not in (None, PROFILE_ID):
        raise ValueError("不支持的runtime来源profile")
    root = Path(repo_dir).resolve()
    families = []
    missing = []
    required_missing = []
    optional_missing = []
    for family, directory in DIRECTORY_FAMILIES.items():
        path = root / directory
        count = sum(1 for item in path.rglob("*.json") if item.is_file()) if path.is_dir() else 0
        optional = family in OPTIONAL_DIRECTORY_FAMILIES
        state = "ready" if count else ("unavailable" if optional else "missing")
        entry = {
            "family": family,
            "requirement": "directory",
            "availability_requirement": "optional" if optional else "required",
            "paths": [directory],
            "json_file_count": count,
            "state": state,
        }
        families.append(entry)
        if state == "missing":
            required_missing.append(directory)
            missing.append(directory)
        elif state == "unavailable":
            optional_missing.append(directory)
            missing.append(directory)
    for family, paths in EXACT_FAMILIES.items():
        if family == "global_combat" and INDEXER_VERSION != "7":
            continue
        if family == "gadget_excel" and INDEXER_VERSION not in ("5", "6", "7"):
            continue
        if family == "material_excel" and INDEXER_VERSION not in ("6", "7"):
            continue
        absent = [relative for relative in paths if not (root / relative).is_file()]
        optional = family in OPTIONAL_EXACT_FAMILIES
        entry = {
            "family": family,
            "requirement": "exact_files",
            "availability_requirement": "optional" if optional else "required",
            "paths": list(paths),
            "json_file_count": len(paths) - len(absent),
            "state": "ready" if not absent else ("unavailable" if optional else "missing"),
            "missing_paths": absent,
        }
        families.append(entry)
        missing.extend(absent)
        (optional_missing if optional else required_missing).extend(absent)
    capabilities = {
        entry["family"]: {
            "state": entry["state"],
            "requirement": entry["availability_requirement"],
        }
        for entry in families
    }
    value = {
        "contract": "genshin-runtime-source-layout-v1",
        "capability_contract": SOURCE_CAPABILITY_CONTRACT,
        "profile_id": PROFILE_ID,
        "profile_version": PROFILE_VERSION,
        "indexer_version": INDEXER_VERSION,
        "compatible": not required_missing,
        "complete": not missing,
        "missing_paths": sorted(missing),
        "required_missing_paths": sorted(required_missing),
        "optional_missing_paths": sorted(optional_missing),
        "capability_state": "ready" if not missing else (
            "degraded" if not required_missing else "unavailable"
        ),
        "capabilities": capabilities,
        "families": families,
    }
    return {**value, "fingerprint": fingerprint(value)}


def enumerate_runtime_sources(repo_dir: Path, *, profile=None):
    if profile not in (None, PROFILE_ID):
        raise ValueError("不支持的runtime来源profile")
    root = Path(repo_dir).resolve()
    layout = inspect_runtime_source_layout(root, profile=profile)
    if not layout["compatible"]:
        raise RuntimeConfigError(
            "必需runtime来源族缺失或精确来源缺失: "
            + ", ".join(layout["required_missing_paths"]),
            "source_missing",
            500,
        )
    selected = {}
    for family, directory in DIRECTORY_FAMILIES.items():
        paths = sorted((root / directory).rglob("*.json"))
        if not paths:
            if family in OPTIONAL_DIRECTORY_FAMILIES:
                continue
            raise RuntimeConfigError(f"必需runtime来源族缺失: {directory}", "source_missing", 500)
        for path in paths:
            selected[path.relative_to(root).as_posix()] = family
    for family, paths in EXACT_FAMILIES.items():
        if family == "global_combat" and INDEXER_VERSION != "7":
            continue
        if family == "gadget_excel" and INDEXER_VERSION not in ("5", "6", "7"):
            continue
        if family == "material_excel" and INDEXER_VERSION not in ("6", "7"):
            continue
        for relative in paths:
            if not (root / relative).is_file():
                if family in OPTIONAL_EXACT_FAMILIES:
                    continue
                raise RuntimeConfigError(f"必需runtime来源缺失: {relative}", "source_missing", 500)
            selected[relative] = family
    if len(selected) > MAX_SOURCE_FILES:
        raise RuntimeConfigError("runtime来源文件数超限", "source_limit", 500)
    files, total, folded = {}, 0, set()
    for relative, family in sorted(selected.items()):
        path = root / relative
        if not path.resolve().is_relative_to(root) or relative.casefold() in folded:
            raise RuntimeConfigError(f"来源路径越界或冲突: {relative}", "source_path_invalid", 500)
        folded.add(relative.casefold())
        size = path.stat().st_size
        total += size
        if size > MAX_SOURCE_BYTES or total > MAX_TOTAL_BYTES:
            raise RuntimeConfigError("runtime来源字节数超限", "source_limit", 500)
        payload = path.read_bytes()
        if len(payload) != size:
            raise RuntimeConfigError("来源读取期间发生变化", "source_changed", 500)
        files[relative] = {"family": family, "sha256": hashlib.sha256(payload).hexdigest(), "size_bytes": size}
    value = {"profile_id": PROFILE_ID, "profile_version": PROFILE_VERSION,
             "indexer_version": INDEXER_VERSION, "files": files, "total_bytes": total,
             "capability_contract": SOURCE_CAPABILITY_CONTRACT,
             "capability_state": layout["capability_state"],
             "capabilities": layout["capabilities"]}
    return {**value, "fingerprint": fingerprint(value)}


def snapshot_id(version_line, commit, manifest):
    return fingerprint({"version_line": version_line, "upstream_commit": commit,
                        "schema_version": SCHEMA_VERSION, "manifest": manifest["fingerprint"]})


def excel_key(path, row, index, *, indexer_version=None):
    indexer_version = INDEXER_VERSION if indexer_version is None else indexer_version
    if indexer_version not in SUPPORTED_INDEXER_VERSIONS:
        raise RuntimeConfigError("运行配置索引器版本不受支持", "unsupported_schema", 409)
    stem = Path(path).stem
    if stem == "AttackAttenuationExcelConfigData" and indexer_version in ("4", "5", "6", "7"):
        group = row.get("group")
        if not isinstance(group, str) or not group.strip() or len(group) > 128:
            raise RuntimeConfigError(f"衰减配置group必须为1至128字符的非空字符串: {path}/{index}")
        return {"group": group}
    keys = {"EquipAffixExcelConfigData": ("affixId",), "ProudSkillExcelConfigData": ("proudSkillId",),
            "AvatarTalentExcelConfigData": ("talentId",), "ReliquarySetExcelConfigData": ("setId",),
            "AttackAttenuationExcelConfigData": ("attenuationGroup",)}
    for names in (keys.get(stem, ()), ("id",), ("Id",), ("level",)):
        if names and all(k in row for k in names):
            return {k: row[k] for k in names}
    return {"source_index": index}


def extract_definitions(source_path, family, data, snapshot, *, normalization=None, indexer_version=None):
    indexer_version = INDEXER_VERSION if indexer_version is None else indexer_version
    if indexer_version not in SUPPORTED_INDEXER_VERSIONS:
        raise RuntimeConfigError("运行配置索引器版本不受支持", "unsupported_schema", 409)
    if indexer_version in ("4", "5", "6", "7") and Path(source_path).stem == "AttackAttenuationExcelConfigData" and not isinstance(data, list):
        raise RuntimeConfigError(f"衰减配置根必须为数组: {source_path}")
    result = []

    def add(kind, key, pointer, payload, owner=None, variant=None):
        ident = fingerprint([snapshot, source_path, pointer, kind])
        result.append({"definition_id": ident, "definition_kind": kind, "exact_key_json": dumps(key),
                       "owner_definition_id": owner, "source_path": source_path, "json_pointer": pointer,
                       "variant_key": variant, "namespace_key": source_path.split("/")[2] if source_path.startswith("BinOutput/Ability/") else family,
                       "node_sha256": fingerprint(payload), "summary_json": dumps({"name": key, "kind": kind}),
                       "payload": payload})
        return ident

    if family == "global_combat":
        if indexer_version != "7" or source_path != GLOBAL_COMBAT_PATH or type(data) is not dict:
            raise RuntimeConfigError("公共战斗原配置需要索引7、精确来源路径及对象根")
        add("global_combat_config", source_path, "", data)
    elif family == "text_map":
        if not isinstance(data, dict):
            raise RuntimeConfigError(f"TextMap根形状错误: {source_path}")
    elif family == "talent":
        if not isinstance(data, dict) or any(not isinstance(v, list) for v in data.values()):
            raise RuntimeConfigError(f"Talent根形状错误: {source_path}")
        for key, operations in data.items():
            add("talent_config", key, "/" + escape(key), operations)
    elif family == "ability":
        if not isinstance(data, list):
            raise RuntimeConfigError(f"Ability根形状错误: {source_path}")
        for i, entry in enumerate(data):
            if not isinstance(entry, dict):
                raise RuntimeConfigError(f"Ability条目形状错误: {source_path}/{i}")
            for variant, payload in entry.items():
                view = _normalized(normalization, payload, "ability") if normalization else payload
                if not isinstance(view, dict) or not isinstance(view.get("abilityName"), str):
                    raise RuntimeConfigError(f"Ability定义缺少名称: {source_path}/{i}/{variant}")
                pointer = f"/{i}/" + escape(variant)
                owner = add("ability", view["abilityName"], pointer, payload, variant=variant)
                modifiers = view.get("modifiers", {})
                if not isinstance(modifiers, dict):
                    raise RuntimeConfigError(f"modifier容器形状错误: {source_path}{pointer}")
                for name in modifiers:
                    relative = "/modifiers/" + escape(name)
                    if normalization:
                        relative = _raw_pointer(normalization, payload, relative, "ability")
                    add("modifier", name, pointer + relative, resolve_pointer(payload, relative), owner, variant)
    elif indexer_version in ("6", "7") and family == "material_excel":
        if source_path != MATERIAL_EXCEL_PATH or not isinstance(data, list):
            raise RuntimeConfigError("MaterialExcel来源路径或根形状错误")
        identifiers = set()
        for i, row in enumerate(data):
            if not isinstance(row, dict) or type(row.get("id")) is not int or not 0 < row["id"] <= MATERIAL_ID_MAX:
                raise RuntimeConfigError(f"MaterialExcel必须有正整数ID: {source_path}/{i}")
            if row["id"] in identifiers:
                raise RuntimeConfigError(f"MaterialExcel ID重复: {source_path}/{i}")
            identifiers.add(row["id"])
            add("material_record", row["id"], f"/{i}", row)
    elif indexer_version in ("5", "6", "7") and family == "gadget_excel":
        if source_path != GADGET_EXCEL_PATH or not isinstance(data, list):
            raise RuntimeConfigError("GadgetExcel来源路径或根形状错误")
        identifiers = set()
        for i, row in enumerate(data):
            if not isinstance(row, dict) or type(row.get("id")) is not int or not 0 < row["id"] <= GADGET_ID_MAX:
                raise RuntimeConfigError(f"GadgetExcel必须有正整数ID: {source_path}/{i}")
            if row["id"] in identifiers:
                raise RuntimeConfigError(f"GadgetExcel ID重复: {source_path}/{i}")
            identifiers.add(row["id"])
            add("gadget_record", row["id"], f"/{i}", row)
    elif indexer_version in ("5", "6", "7") and family == "gadget":
        if not isinstance(data, dict):
            raise RuntimeConfigError(f"Gadget根必须为对象: {source_path}")
        owner = add("gadget_config", source_path, "", data)
        for name, payload in data.items():
            if not isinstance(name, str) or not name.strip() or len(name) > GADGET_NAME_MAX_LENGTH or not isinstance(payload, dict):
                raise RuntimeConfigError(f"Gadget配置名称或对象无效: {source_path}/{name}")
            add("gadget_config_entry", name, "/" + escape(name), payload, owner)
    elif family in ("excel", "public_config") and isinstance(data, list):
        attenuation_groups = set()
        for i, row in enumerate(data):
            if not isinstance(row, dict):
                raise RuntimeConfigError(f"Excel条目形状错误: {source_path}/{i}")
            key = excel_key(source_path, row, i, indexer_version=indexer_version)
            if Path(source_path).stem == "AttackAttenuationExcelConfigData" and indexer_version in ("4", "5", "6", "7"):
                if key["group"] in attenuation_groups:
                    raise RuntimeConfigError(f"衰减配置group重复: {source_path}/{i}")
                attenuation_groups.add(key["group"])
            add("excel_record", key, f"/{i}", row)
    else:
        # These assets have several shapes. The complete root is retained; no guessed avatar-name identity.
        if not isinstance(data, (dict, list)):
            raise RuntimeConfigError(f"runtime根形状错误: {source_path}")
        add(family + "_config", source_path, "", data)
    return result


ABILITY_OPS = {"AddAbility", "ModifyAbility", "TriggerAbility", "UnlockTalentParam", "RemoveAbility"}
MODIFIER_OPS = {"ApplyModifier", "AttachModifier", "RemoveModifier", "RemoveUniqueModifier",
                "AttachModifierToSelfGlobalValueMixin", "AttachModifierToTargetMixin"}


def extract_references(definition, *, normalization=None, indexer_version=INDEXER_VERSION):
    """Only explicit structural forms are interpreted. Unknown bodies remain intact."""
    result = []
    raw_payload = definition["payload"]
    kind = definition["definition_kind"]
    if kind in ("gadget_record", "gadget_config_entry", "material_record", "global_combat_config"):
        return []  # Object graph is a separate opt-in API; legacy traversal stays unchanged.
    # Avatar/Gadget use a separate two-field scope inside the normalizer. Other
    # source families stay raw; equal tokens do not prove a shared namespace.
    if kind not in ("ability", "modifier", "talent_config", "avatar_config", "gadget_config"):
        normalization = None
    payload = _normalized(normalization, raw_payload, kind) if normalization else raw_payload
    # Modifiers are walked separately; their owner body must not duplicate edges.
    for pointer, node in walk(payload):
        if kind == "ability" and pointer.startswith("/modifiers/"):
            continue
        if not isinstance(node, dict):
            continue
        if indexer_version != "2" and kind == "gadget_config" and pointer == "":
            continue  # Gadget root keys are configuration identities.
        if indexer_version != "2" and pointer.rsplit("/", 1)[-1] in ("abilitySpecials", "modifiers", "properties", "globalValues"):
            # Names in these maps are identifiers, not schema reference fields.
            # Retain the old traversal when validating an indexer-2 snapshot.
            continue
        op = node.get("$type")
        candidates = []
        if "openConfig" in node and kind == "excel_record" and pointer == "":
            candidates.append(("openConfig", "talent_config", node["openConfig"], "open_config"))
        if "abilityName" in node and (isinstance(op, str) and op in ABILITY_OPS or "/abilities/" in pointer or kind == "excel_record" and pointer == "" and definition["source_path"].endswith("/AvatarSkillExcelConfigData.json")):
            candidates.append(("abilityName", "ability", node["abilityName"], "ability_operation"))
        if "modifierName" in node and isinstance(op, str) and (op in MODIFIER_OPS or op.endswith("Mixin")):
            candidates.append(("modifierName", "modifier", node["modifierName"], "modifier_operation"))
        if "modifierNameSteps" in node and op == "AttachModifierToSelfGlobalValueMixin" and isinstance(node["modifierNameSteps"], list):
            candidates += [(f"modifierNameSteps/{i}", "modifier", value, "modifier_step") for i, value in enumerate(node["modifierNameSteps"])]
        for field, target in (("abilityName", "ability"), ("modifierName", "modifier")):
            is_definition_name = field == "abilityName" and pointer == "" and kind == "ability"
            if field in node and not is_definition_name and not any(c[0] == field for c in candidates):
                candidates.append((field, target, node[field], "unsupported_reference_form"))
        for operation, field in (("CreateGadget", "gadgetID"), ("TriggerBullet", "bulletID"), ("GenerateElemBall", "configID")):
            if op == operation and field in node:
                candidates.append((field, "gadget_config", node[field], "external_asset"))
        for field, target, value, relation in candidates:
            relative = pointer + "/" + field
            if normalization:
                relative = _raw_pointer(normalization, raw_payload, relative, kind)
            field_pointer = definition["json_pointer"] + relative
            presence = "null" if value is None else "empty" if value == "" else "present"
            result.append({"reference_id": fingerprint([definition["definition_id"], field_pointer]),
                           "from_definition_id": definition["definition_id"], "source_path": definition["source_path"],
                           "json_pointer": field_pointer, "field_name": field, "relation_kind": relation,
                           "target_kind": target, "target_key_json": dumps(value),
                           "scope_json": dumps({"owner_definition_id": definition.get("owner_definition_id") or (definition["definition_id"] if kind == "ability" else None)}),
                           "source_order": len(result), "presence_status": presence,
                           "resolution_status": "source_empty" if presence != "present" else relation if relation in ("unsupported_reference_form", "external_asset") else "unresolved",
                           "resolved_definition_id": None, "candidate_ids_json": "[]", "extraction_rule_id": indexer_version})
    return result


def gadget_entry_view(payload, normalization=None):
    """Use the existing two-field Gadget scope without changing global mappings."""
    return _normalized(normalization, {"entry": payload}, "gadget_config")["entry"] if normalization else payload


def extract_object_references(definition, *, normalization=None, indexer_version=None):
    """Exact object identities, never creation timing or execution semantics."""
    version = INDEXER_VERSION if indexer_version is None else indexer_version
    if version not in SUPPORTED_INDEXER_VERSIONS:
        raise RuntimeConfigError("运行配置索引器版本不受支持", "unsupported_schema", 409)
    if version not in ("5", "6", "7"):
        return []
    kind, raw = definition["definition_kind"], definition["payload"]
    result = []

    def add(relative, field, value, target, relation, *, present=True, legacy=False):
        pointer = definition["json_pointer"] + relative
        presence = "missing" if not present else "null" if value is None else "empty" if value == "" else "present"
        valid = type(value) is int and 0 < value <= GADGET_ID_MAX if target == "gadget_record" else isinstance(value, str) and bool(value.strip()) and len(value) <= GADGET_NAME_MAX_LENGTH
        status = "source_empty" if presence != "present" else "unresolved" if valid else "invalid_reference_form"
        result.append({"reference_id": fingerprint(["object-reference-v1", definition["definition_id"], pointer, relation]),
            "from_definition_id": definition["definition_id"], "source_path": definition["source_path"],
            "json_pointer": pointer, "field_name": field, "relation_kind": relation, "target_kind": target,
            "target_key_json": dumps(value), "scope_json": dumps({"graph_scope": "gadget_identity_v1",
                "owner_definition_id": definition.get("owner_definition_id") or (definition["definition_id"] if kind == "ability" else None)}),
            "source_order": len(result), "presence_status": presence, "resolution_status": status,
            "resolved_definition_id": None, "candidate_ids_json": "[]", "extraction_rule_id": version,
            "legacy_reference_id": fingerprint([definition["definition_id"], pointer]) if legacy else None})

    if kind in ("ability", "modifier"):
        view = _normalized(normalization, raw, kind) if normalization else raw
        for pointer, node in walk(view):
            if kind == "ability" and pointer.startswith("/modifiers/") or not isinstance(node, dict):
                continue
            field = {"CreateGadget": "gadgetID", "TriggerBullet": "bulletID"}.get(node.get("$type"))
            if field and field in node:
                relative = pointer + "/" + field
                if normalization:
                    relative = _raw_pointer(normalization, raw, relative, kind)
                add(relative, field, node[field], "gadget_record", "gadget_id_reference", legacy=True)
    elif kind == "gadget_record":
        add("/jsonName", "jsonName", raw.get("jsonName"), "gadget_config_entry", "gadget_config_name", present="jsonName" in raw)
    elif kind == "gadget_config_entry":
        view = gadget_entry_view(raw, normalization)
        mounts = view.get("abilities")
        if isinstance(mounts, list):
            for index, mount in enumerate(mounts):
                if not isinstance(mount, dict) or "abilityName" not in mount:
                    continue  # Query reports malformed/absent list entries from the complete raw container.
                relative = f"/abilities/{index}/abilityName"
                if normalization:
                    relative = _raw_pointer(normalization, {"entry": raw}, "/entry" + relative, "gadget_config")[len("/entry"):]
                add(relative, "abilityName", mount["abilityName"], "ability", "gadget_ability_mount")
    return result


def extract_material_references(definition, *, normalization=None, indexer_version=None):
    """Indexer 6 opt-in material identities; no generator or item-use semantics."""
    version = INDEXER_VERSION if indexer_version is None else indexer_version
    if version not in SUPPORTED_INDEXER_VERSIONS:
        raise RuntimeConfigError("运行配置索引器版本不受支持", "unsupported_schema", 409)
    kind, raw = definition["definition_kind"], definition["payload"]
    if version not in ("6", "7") or kind not in ("ability", "modifier"):
        return []
    view = _normalized(normalization, raw, kind) if normalization else raw
    result = []
    for pointer, node in walk(view):
        if (kind == "ability" and pointer.startswith("/modifiers/")
                or not isinstance(node, dict) or node.get("$type") != "GenerateElemBall"):
            continue
        present, value = "configID" in node, node.get("configID")
        relative = pointer + "/configID"
        if normalization:
            # A missing leaf has no legacy identity. Preserve its normalized
            # audit path under the verified raw operation, without inventing a
            # source field token or asking raw_pointer to resolve absent data.
            relative = (_raw_pointer(normalization, raw, relative, kind) if present else
                        _raw_pointer(normalization, raw, pointer, kind) + "/configID")
        full_pointer = definition["json_pointer"] + relative
        presence = "missing" if not present else "null" if value is None else "empty" if value == "" else "present"
        valid = type(value) is int and 0 < value <= MATERIAL_ID_MAX
        status = "source_empty" if presence != "present" else "unresolved" if valid else "invalid_reference_form"
        relation = "element_ball_material"
        result.append({
            "reference_id": fingerprint(["material-reference-v1", definition["definition_id"], full_pointer, relation]),
            "from_definition_id": definition["definition_id"], "source_path": definition["source_path"],
            "json_pointer": full_pointer, "field_name": "configID", "relation_kind": relation,
            "target_kind": "material_record", "target_key_json": dumps(value),
            "scope_json": dumps({"graph_scope": "energy_material_v1", "owner_definition_id":
                definition.get("owner_definition_id") or (definition["definition_id"] if kind == "ability" else None)}),
            "source_order": len(result), "presence_status": presence, "resolution_status": status,
            "resolved_definition_id": None, "candidate_ids_json": "[]", "extraction_rule_id": version,
            "legacy_reference_id": fingerprint([definition["definition_id"], full_pointer]) if present else None,
        })
    return result


def extract_parameter_bindings(definition, *, normalization=None):
    if definition["definition_kind"] != "talent_config":
        return []
    result = []
    raw_payload = definition["payload"]
    payload = _normalized(normalization, raw_payload, "talent_config") if normalization else raw_payload
    for i, op in enumerate(payload):
        if isinstance(op, dict) and op.get("$type") == "ModifyAbility":
            pointer = definition["json_pointer"] + f"/{i}"
            reference_pointer = f"/{i}/abilityName"
            expression = op.get("paramDelta")
            if normalization:
                if "abilityName" in op:
                    reference_pointer = _raw_pointer(normalization, raw_payload, reference_pointer, "talent_config")
                if "paramDelta" in op:
                    expression = resolve_pointer(raw_payload, _raw_pointer(normalization, raw_payload, f"/{i}/paramDelta", "talent_config"))
            result.append({"binding_id": fingerprint([definition["definition_id"], pointer]),
                           "origin_definition_id": definition["definition_id"], "operation_pointer": pointer,
                           "operation_index": i, "operation_type": op["$type"],
                           "target_reference_id": fingerprint([definition["definition_id"], definition["json_pointer"] + reference_pointer]) if "abilityName" in op else None,
                           "parameter_name": op.get("paramSpecial"), "raw_expression_json": dumps(expression),
                           "extraction_status": "present" if all(key in op for key in ("paramDelta", "paramSpecial", "abilityName")) else "missing_field"})
    return result
