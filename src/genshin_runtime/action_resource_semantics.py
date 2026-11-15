"""Source-scoped action facts; schema validity never grants execution authority."""
from __future__ import annotations

import re

from genshin_runtime.mechanism_source_binding import validate_pointer

ACTION_FACT_KINDS = frozenset({"action_capacity_delta", "related_action_root", "action_capacity_model",
                               "action_admission_model"})
RELATED_LIMITS = {"default_mount": "unproven", "whole_avatar_coverage": "partial",
                  "capacity_closure": "pending", "action_dispatch": "pending",
                  "execution_authority": False}
DELTA_SCOPE = "capacity_delta_only_not_initial_uses_or_cooldown_or_dispatch"
SOURCE_CAPABILITY_CONTRACT = "genshin-runtime-source-capabilities-v1"
_LEGACY_SNAPSHOT_FIELDS = frozenset({
    "version_line", "state", "index_state", "schema_version", "upstream_commit",
    "snapshot_id", "execution_support", "built_at", "profile_id", "profile_version",
    "indexer_version", "manifest_fingerprint", "counts", "reference_states",
    "normalization",
})
_CAPABILITY_SNAPSHOT_FIELDS = _LEGACY_SNAPSHOT_FIELDS | {
    "normalization_state", "source_capability_contract",
    "source_capability_state", "source_capabilities",
}


def require(value, message):
    if not value:
        raise ValueError(message)


def obj(value, keys):
    require(type(value) is dict and set(value) == set(keys), "动作事实字段缺失或未知")


def integer(value, low=1, high=2**53 - 1):
    require(type(value) is int and low <= value <= high, "动作事实要求范围内严格整数")


def text(value, maximum=512):
    require(type(value) is str and 0 < len(value) <= maximum and value.strip() == value,
            "动作事实需要有限非空字符串")


def digest(value, length=64):
    require(type(value) is str and re.fullmatch(r"[0-9a-f]{" + str(length) + "}", value),
            "动作事实摘要格式错误")


def path(value):
    text(value)
    require(not any(c in value for c in ("\\", ":", "\x00"))
            and all(p not in ("", ".", "..") for p in value.split("/")), "动作事实路径不规范")


def snapshot(value):
    # Old reviewed facts must remain structurally readable so the fresh DB
    # consumer can classify them as stale.  The exact fresh-snapshot equality
    # check remains in the database profile resolver; accepting this legacy
    # envelope here does not grant it current execution authority.
    require(type(value) is dict and set(value) in (
        _LEGACY_SNAPSHOT_FIELDS, _CAPABILITY_SNAPSHOT_FIELDS,
    ), "动作事实快照字段缺失或未知")
    require(value["version_line"] in ("release", "beta") and value["state"] == value["index_state"] == "ready",
            "动作事实快照状态无效")
    integer(value["schema_version"], 1, 1)
    integer(value["profile_version"], 1)
    for key in ("snapshot_id", "manifest_fingerprint"):
        digest(value[key])
    digest(value["upstream_commit"], 40)
    for key in ("indexer_version", "built_at", "profile_id", "execution_support"):
        text(value[key], 128)
    for key in ("counts", "reference_states"):
        require(type(value[key]) is dict and len(value[key]) <= 64, "快照计数无效")
        for name, count in value[key].items():
            text(name, 128)
            integer(count, 0)
    norm = value["normalization"]
    obj(norm, {"mapping_fingerprint", "normalization_version", "profile_id", "upstream_commit"})
    digest(norm["mapping_fingerprint"])
    integer(norm["normalization_version"], 1)
    text(norm["profile_id"], 128)
    require(norm["upstream_commit"] == value["upstream_commit"], "归一化提交与快照不符")
    if set(value) == _CAPABILITY_SNAPSHOT_FIELDS:
        require(value["normalization_state"] == "ready",
                "动作事实只接受完成审核的归一化快照")
        require(value["source_capability_contract"] == SOURCE_CAPABILITY_CONTRACT,
                "动作事实来源能力合同无效")
        capabilities = value["source_capabilities"]
        require(type(capabilities) is dict and 1 <= len(capabilities) <= 64,
                "动作事实来源能力清单无效")
        all_ready = True
        for name, receipt in capabilities.items():
            text(name, 128)
            obj(receipt, {"state", "requirement"})
            require(receipt["state"] in ("ready", "unavailable")
                    and receipt["requirement"] in ("required", "optional"),
                    "动作事实来源能力状态无效")
            require(receipt["requirement"] != "required" or receipt["state"] == "ready",
                    "动作事实必需来源能力不可用")
            all_ready = all_ready and receipt["state"] == "ready"
        require(value["source_capability_state"] == ("ready" if all_ready else "degraded"),
                "动作事实来源能力汇总不一致")


def _source_claim(c):
    obj(c, {"schema_version", "operation", "amount", "character_id", "skill_depot_id", "target_skill_id",
            "constellation_rank", "snapshot", "normalization", "source_path", "source_sha256",
            "parent_canonical_sha256", "config_pointer", "config_node_sha256", "operation_pointer",
            "operation_node_sha256", "record_fingerprints", "target_skill_fingerprint", "scope",
            "raw_flag_interpretation"})
    integer(c["schema_version"], 1, 1)
    require(c["operation"] == "add" and c["scope"] == DELTA_SCOPE
            and c["raw_flag_interpretation"] == "source_scoped_evidence_does_not_decode_global_flag",
            "增量事实不能扩张到全源或恢复语义")
    integer(c["amount"], 0, 99)
    for key in ("character_id", "skill_depot_id", "target_skill_id"):
        integer(c[key])
    integer(c["constellation_rank"], 1, 6)
    snapshot(c["snapshot"])
    obj(c["normalization"], {"mapping_fingerprint", "additional_recipe_fingerprint"})
    for value in c["normalization"].values():
        digest(value)
    require(c["normalization"]["mapping_fingerprint"] == c["snapshot"]["normalization"]["mapping_fingerprint"],
            "次数事实映射不一致")
    path(c["source_path"])
    for key in ("source_sha256", "parent_canonical_sha256", "config_node_sha256",
                "operation_node_sha256", "target_skill_fingerprint"):
        digest(c[key])
    for key in ("config_pointer", "operation_pointer"):
        validate_pointer(c[key])
    require(c["config_pointer"] and c["operation_pointer"].startswith(c["config_pointer"] + "/"),
            "次数操作必须属于指定配置")
    obj(c["record_fingerprints"], {"avatar", "depot", "talent", "description"})
    for value in c["record_fingerprints"].values():
        digest(value)


def _delta(c):
    if type(c) is dict and c.get("contract") == "action-capacity-delta-v2":
        from genshin_runtime.action_capacity_rebinding import (
            validate_capacity_rebinding_claim,
        )
        validate_capacity_rebinding_claim(c)
        return
    obj(c, {"contract", "source_claim", "activation", "application", "evidence_route", "prior_review"})
    require(c["contract"] == "action-capacity-delta-v1"
            and c["application"] == "static_capacity_once_per_selected_constellation_slot",
            "次数事实合同不受支持")
    _source_claim(c["source_claim"])
    obj(c["activation"], {"kind", "rank", "talent_id", "depot_pointer"})
    a = c["activation"]
    require(a["kind"] == "constellation_slot", "仅支持原命座槽激活")
    integer(a["rank"], 1, 6)
    integer(a["talent_id"])
    validate_pointer(a["depot_pointer"])
    require(a["rank"] == c["source_claim"]["constellation_rank"]
            and a["depot_pointer"].endswith("/talents/" + str(a["rank"] - 1)), "激活槽不匹配")
    require(c["evidence_route"] in ("direct_skill_description", "same_parent_explicit_subskill_operation"),
            "次数事实证据路径未支持")
    review = c["prior_review"]
    obj(review, {"original_fact_sha256", "source_claim_sha256", "decision_sha256", "report_sha256",
                 "scope", "original_fact_id", "original_revision", "assumptions", "original_evidence_tier"})
    for key in ("original_fact_sha256", "source_claim_sha256", "decision_sha256", "report_sha256"):
        digest(review[key])
    from genshin_runtime.mechanism_semantics import fingerprint
    require(review["source_claim_sha256"] == fingerprint(c["source_claim"]), "原审claim未完整保留")
    require(review["scope"] == "exact_source_capacity_delta_only"
            and review["original_evidence_tier"] == "same_snapshot_raw_and_explicit_original_description",
            "原审核范围不能改变")
    text(review["original_fact_id"], 80)
    integer(review["original_revision"], 1, 2**31 - 1)
    require(type(review["assumptions"]) is list and 1 <= len(review["assumptions"]) <= 16,
            "必须保留原假设说明")
    for value in review["assumptions"]:
        text(value, 4096)


def _related(c):
    obj(c, {"contract", "scope", "hash_algorithm", "snapshot", "selection", "nodes", "roles",
            "resource_role", "combat_config_hash", "mounts", "anchors", "limits_of_claim", "prior_material"})
    require(c["contract"] == "related-action-root-v1"
            and c["scope"] == "related_root_for_dependency_enumeration"
            and c["resource_role"] == "related_avatar_config" and c["hash_algorithm"] == "not_decoded",
            "弱关联不得声明默认基础挂载")
    from genshin_runtime.mechanism_semantics import fingerprint
    require(fingerprint(c["limits_of_claim"]) == fingerprint(RELATED_LIMITS), "弱关联权限边界错误")
    snapshot(c["snapshot"])
    obj(c["selection"], {"character_id", "skill_depot_id"})
    for value in c["selection"].values():
        integer(value)
    require(type(c["combat_config_hash"]) in (str, int), "不解释的不透明hash类型错误")
    obj(c["prior_material"], {"record_id", "revision", "record_sha256", "review_report_sha256"})
    text(c["prior_material"]["record_id"], 80)
    integer(c["prior_material"]["revision"], 1, 2**31 - 1)
    for key in ("record_sha256", "review_report_sha256"):
        digest(c["prior_material"][key])
    require(type(c["nodes"]) is list and 1 <= len(c["nodes"]) <= 32, "弱关联节点数量无效")
    ids, locations = set(), set()
    for node in c["nodes"]:
        obj(node, {"id", "definition", "source_path", "source_sha256", "json_pointer", "node_sha256"})
        text(node["id"], 80)
        path(node["source_path"])
        validate_pointer(node["json_pointer"])
        digest(node["source_sha256"])
        digest(node["node_sha256"])
        d = node["definition"]
        obj(d, {"definition_id", "definition_kind", "owner_definition_id", "source_path", "json_pointer",
                "variant_key", "namespace_key", "node_sha256", "exact_key"})
        require(d["definition_kind"] in ("excel_record", "talent_config", "ability", "modifier", "avatar_config"),
                "弱关联节点类别无效")
        require(all(d[key] == node[key] for key in ("source_path", "json_pointer", "node_sha256")),
                "弱关联索引身份不符")
        require(d["definition_id"] == fingerprint([c["snapshot"]["snapshot_id"], node["source_path"],
                node["json_pointer"], d["definition_kind"]]), "弱关联节点ID不符")
        if d["owner_definition_id"] is not None:
            digest(d["owner_definition_id"])
        for key in ("variant_key", "namespace_key"):
            if d[key] is not None:
                text(d[key], 512)
        location = (node["source_path"], node["json_pointer"], d["definition_kind"])
        require(node["id"] not in ids and location not in locations, "弱关联节点重复")
        ids.add(node["id"])
        locations.add(location)
    roles = c["roles"]
    obj(roles, {"avatar", "depot", "config", "peer_controls"})
    require(all(type(roles[k]) is str and roles[k] in ids for k in ("avatar", "depot", "config")),
            "弱关联角色节点缺失")
    require(type(roles["peer_controls"]) is list and len(roles["peer_controls"]) <= 4
            and all(type(x) is str and x in ids for x in roles["peer_controls"])
            and len(set(roles["peer_controls"])) == len(roles["peer_controls"]), "对照节点无效")
    require(type(c["mounts"]) is list and len(c["mounts"]) <= 64
            and type(c["anchors"]) is list and 1 <= len(c["anchors"]) <= 32, "弱关联引用数量无效")
    for reference in c["mounts"]:
        _reference(reference)
    for anchor in c["anchors"]:
        obj(anchor, {"slot", "skill", "proud", "talent", "proud_reference", "talent_reference", "mount_reference"})
        require(anchor["slot"] in ("normal", "skill", "burst")
                and all(type(anchor[k]) is str and anchor[k] in ids for k in ("skill", "proud", "talent")),
                "弱关联技能链无效")
        for key in ("proud_reference", "talent_reference", "mount_reference"):
            _reference(anchor[key])


def _reference(ref):
    obj(ref, {"reference_id", "from_definition_id", "source_path", "json_pointer", "field_name",
              "relation_kind", "target_kind", "source_order", "presence_status", "extraction_rule_id", "target_key"})
    digest(ref["reference_id"])
    digest(ref["from_definition_id"])
    path(ref["source_path"])
    validate_pointer(ref["json_pointer"])
    integer(ref["source_order"], 0)
    for key in ("field_name", "relation_kind", "target_kind", "presence_status", "extraction_rule_id"):
        text(ref[key], 128)
    require(ref["target_kind"] in ("ability", "talent_config") and ref["presence_status"] == "present",
            "弱关联只支持明确的能力/天赋引用")
    text(ref["target_key"], 512)


def validate_action_claim(kind, claim):
    if kind == "action_admission_model":
        from genshin_runtime.action_admission_model import validate_model
        validate_model(claim)
    elif kind == "action_capacity_model":
        from genshin_runtime.action_capacity_model import validate_model
        validate_model(claim)
    elif kind == "action_capacity_delta":
        _delta(claim)
    elif kind == "related_action_root":
        _related(claim)
    else:
        raise ValueError("动作事实类型未支持")


def validate_action_record_scope(record):
    """Tie schema-valid action payloads to the existing review wrapper."""
    fact = record["payload"]
    c = fact["claim"]
    if fact["kind"] == "action_capacity_delta":
        identity, snap = c["source_claim"], c["source_claim"]["snapshot"]
        cid = identity["character_id"]
    elif fact["kind"] == "action_capacity_model":
        from genshin_runtime.action_capacity_model import evidence_for
        cid, snap = c["target"]["character_id"], c["snapshot"]
        require(record["evidence"] == evidence_for(c)
                and fact["evidence_refs"] == [item["id"] for item in record["evidence"]], "容量模型证据封套不符")
    elif fact["kind"] == "action_admission_model":
        from genshin_runtime.action_admission_model import evidence_for
        cid, snap = c["target"]["character_id"], c["snapshot"]
        require(record["evidence"] == evidence_for(c)
                and fact["evidence_refs"] == [item["id"] for item in record["evidence"]], "动作接纳模型证据封套不符")
    else:
        cid, snap = c["selection"]["character_id"], c["snapshot"]
        require(set(fact["evidence_refs"]) == {n["id"] for n in c["nodes"]}, "弱关联完整节点证据缺失")
    from genshin_runtime.mechanism_semantics import fingerprint
    require(record["subject"] == {"target_type": "character", "target_id": cid}, "动作事实主体不符")
    require(record["applicability"] == {"version_line": snap["version_line"],
            "upstream_commit": snap["upstream_commit"], "normalization_fingerprint": fingerprint(snap["normalization"])},
            "动作事实适用版本不符")
