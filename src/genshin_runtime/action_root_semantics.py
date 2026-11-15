"""Isolated source-material matcher. Caller records never carry DB authority."""
from __future__ import annotations

import copy
import json
import math
import re
from datetime import datetime

from genshin_db.runtime_catalog import extract_references, fingerprint
from genshin_runtime.mechanism_source_binding import validate_pointer
from genshin_runtime.normalization import for_commit

CONTRACT = "related-action-root-v1"
SCOPE = "related_root_for_dependency_enumeration"
MAX_BYTES = 2 * 1024 * 1024
MAX_WORK = 200000
MAX_DEPTH = 64
MAX_SAFE_BYTES = 8 * 1024 * 1024
REF_FIELDS = ("reference_id", "from_definition_id", "source_path", "json_pointer", "field_name",
              "relation_kind", "target_kind", "source_order", "presence_status", "extraction_rule_id")


class BaseBindingError(ValueError):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


def require(value, code, message):
    if not value:
        raise BaseBindingError(code, message)


def bounded(value):
    stack, active, count, estimate = [(value, 0, False)], set(), 0, 0
    pending_work = 1
    while stack:
        node, depth, leave = stack.pop()
        if leave:
            active.remove(id(node))
            continue
        pending_work -= 1
        count += 1
        require(count <= MAX_WORK and depth <= MAX_DEPTH, "binding_budget", "material traversal budget exceeded")
        kind = type(node)
        require(kind in (dict, list, str, bool, int, float, type(None)), "binding_shape", "strict JSON required")
        if kind in (dict, list):
            require(id(node) not in active, "binding_shape", "cyclic material")
            children = len(node) * (2 if kind is dict else 1)
            # Reserve every child, including dictionary keys and pending siblings,
            # before allocating any leave marker or child traversal entries.
            require(count + pending_work + children <= MAX_WORK
                    and (not children or depth + 1 <= MAX_DEPTH),
                    "binding_budget", "material traversal budget exceeded")
            estimate += 2 + 2 * len(node)
            require(estimate <= MAX_SAFE_BYTES, "binding_budget", "safe encoding bound exceeded")
            active.add(id(node))
            stack.append((node, depth, True))
            pending_work += children
            if kind is dict:
                require(all(type(k) is str for k in node), "binding_shape", "JSON keys must be strings")
                stack.extend((v, depth+1, False) for pair in node.items() for v in pair)
            else:
                stack.extend((v, depth+1, False) for v in node)
        elif kind is str:
            estimate += 6 * len(node) + 2
        elif kind is int:
            require(node.bit_length() <= 63, "binding_shape", "integer too large")
            estimate += 24
        elif kind is float:
            require(math.isfinite(node), "binding_shape", "nonfinite number")
            estimate += 32
        else:
            estimate += 5
        require(estimate <= MAX_SAFE_BYTES, "binding_budget", "safe encoding bound exceeded")
    try:
        encoded = json.dumps(value, ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(",", ":")).encode("utf8")
    except (ValueError, UnicodeError) as exc:
        raise BaseBindingError("binding_shape", "invalid JSON encoding") from exc
    require(len(encoded) <= MAX_BYTES, "binding_budget", "material byte budget exceeded")
    return {"visited_nodes": count, "safe_encoding_bytes": estimate, "serialized_bytes": len(encoded)}


def integer(value, low=1):
    return type(value) is int and low <= value <= 2**53-1


def same(a, b):
    return fingerprint(a) == fingerprint(b)


def structural_ref(ref):
    return {**{k: ref[k] for k in REF_FIELDS}, "target_key": ref.get("target_key", json.loads(ref.get("target_key_json", "null")))}


def binding_for(node):
    return {"id": node["id"], "definition": node["definition"],
            **{k: node[k] for k in ("source_path", "source_sha256", "json_pointer")},
            "node_sha256": fingerprint(node["raw"])}


def refs_for(node, snapshot):
    recipe = for_commit(snapshot["upstream_commit"])
    refs = extract_references({**node["definition"], "payload": node["raw"]}, normalization=recipe,
                              indexer_version=snapshot["indexer_version"])
    return [structural_ref(r) for r in refs]


def match_related_root_material(material, record, current_head):
    """Matching does not trust a caller's approved flag or supplied head as authority."""
    usage = bounded([material, record, current_head])
    try:
        claim = record["payload"]["claim"]
        from genshin_runtime.action_resource_semantics import validate_action_claim
        validate_action_claim("related_action_root", claim)
        require(type(record["id"]) is str and re.fullmatch(r"[a-z0-9][a-z0-9._-]{0,79}", record["id"]),
                "binding_fact", "record ID invalid")
        require(set(record) == {"id", "revision", "subject", "payload", "evidence", "applicability", "review"},
                "binding_fact", "unknown or missing record fields")
        require(record["payload"]["kind"] == "related_action_root" and claim["contract"] == CONTRACT
                and claim["scope"] == SCOPE and claim["hash_algorithm"] == "not_decoded",
                "binding_fact", "unsupported fact semantics")
        require(set(record["payload"]) == {"id", "revision", "kind", "claim", "evidence_tier", "evidence_refs"}
                and set(claim) == {"contract", "scope", "hash_algorithm", "snapshot", "selection", "nodes", "roles",
                                   "resource_role", "combat_config_hash", "mounts", "anchors", "limits_of_claim", "prior_material"}, "binding_fact", "fact shape is not closed")
        require(integer(record["revision"]) and same(record["revision"], record["payload"]["revision"])
                and record["id"] == record["payload"]["id"], "binding_fact", "record identity mismatch")
        require(set(record["review"]) == {"status", "reviewer", "reviewed_at"}
                and record["review"]["status"] in ("draft", "pending", "approved", "revoked"), "binding_fact", "review invalid")
        if record["review"]["status"] in ("approved", "revoked"):
            require(type(record["review"]["reviewer"]) is str and bool(record["review"]["reviewer"])
                    and type(record["review"]["reviewed_at"]) is str, "binding_fact", "review metadata missing")
            datetime.fromisoformat(record["review"]["reviewed_at"])
        else:
            require(record["review"]["reviewer"] is None and record["review"]["reviewed_at"] is None,
                    "binding_fact", "pending record has review metadata")
        require(same(claim["snapshot"], material["snapshot"]), "binding_stale", "snapshot or normalization changed")
        snapshot = material["snapshot"]
        expected_app = {"version_line": snapshot["version_line"], "upstream_commit": snapshot["upstream_commit"],
                        "normalization_fingerprint": fingerprint(snapshot["normalization"])}
        require(same(record["applicability"], expected_app), "binding_stale", "record applicability changed")
        recipe = for_commit(snapshot["upstream_commit"])
        require(recipe is not None and recipe.mapping_fingerprint == snapshot["normalization"]["mapping_fingerprint"], "binding_stale", "mapping changed")
        require(type(material["nodes"]) is list and 1 <= len(material["nodes"]) <= 32,
                "binding_budget", "node count exceeds supported scope")
        nodes = {n["id"]: n for n in material["nodes"]}
        require(len(nodes) == len(material["nodes"]), "binding_material", "duplicate node identities")
        require(all(type(k) is str and re.fullmatch(r"[a-z0-9][a-z0-9._-]{0,79}", k) for k in nodes),
                "binding_material", "node label invalid")
        require(same([binding_for(n) for n in material["nodes"]], claim["nodes"]), "binding_stale", "complete bound node material changed")
        for node in nodes.values():
            require(set(node) == {"id", "definition", "source_path", "source_sha256", "json_pointer", "raw"},
                    "binding_material", "node shape invalid")
            definition = node["definition"]
            validate_pointer(node["json_pointer"])
            require(type(node["source_path"]) is str and 0 < len(node["source_path"]) <= 512
                    and not any(c in node["source_path"] for c in ("\\", ":", "\x00"))
                    and all(p not in ("", ".", "..") for p in node["source_path"].split("/")),
                    "binding_material", "source path invalid")
            require(type(node["source_sha256"]) is str and re.fullmatch(r"[a-f0-9]{64}", node["source_sha256"])
                    and definition["definition_kind"] in ("excel_record", "talent_config", "ability", "modifier", "avatar_config"),
                    "binding_material", "node kind or source digest invalid")
            expected_id = fingerprint([snapshot["snapshot_id"], node["source_path"], node["json_pointer"], definition["definition_kind"]])
            require(definition["definition_id"] == expected_id and definition["source_path"] == node["source_path"]
                    and definition["json_pointer"] == node["json_pointer"]
                    and definition["node_sha256"] == fingerprint(node["raw"]), "binding_material", "definition identity invalid")
        evidence = record["evidence"]
        require(type(evidence) is list and 1 <= len(evidence) <= 64
                and record["payload"]["evidence_tier"] in ("upstream_structural", "primary_test", "experimental_reference"),
                "binding_fact", "evidence scope invalid")
        evidence_map = {e["id"]: e for e in evidence}
        require(len(evidence_map) == len(evidence) and set(nodes) == set(evidence_map)
                and type(record["payload"]["evidence_refs"]) is list
                and len(record["payload"]["evidence_refs"]) == len(evidence)
                and set(record["payload"]["evidence_refs"]) == set(evidence_map), "binding_fact", "source evidence incomplete or duplicate")
        for ident, node in nodes.items():
            expected = {"id": ident, "kind": "runtime_node", "locator": node["source_path"] + "#" + node["json_pointer"],
                        "sha256": fingerprint(node["raw"])}
            require(same(evidence_map[ident], expected), "binding_fact", "source evidence identity differs")
        roles = claim["roles"]
        require(set(roles) == {"avatar", "depot", "config", "peer_controls"}
                and set(claim["selection"]) == {"character_id", "skill_depot_id"}, "binding_fact", "unknown role or selection")
        avatar, depot, config = (nodes[roles[k]] for k in ("avatar", "depot", "config"))
        cid, did = claim["selection"]["character_id"], claim["selection"]["skill_depot_id"]
        require(integer(cid) and integer(did) and same(avatar["raw"]["id"], cid) and same(depot["raw"]["id"], did),
                "binding_relation", "strict actor/depot identities required")
        require(same(record["subject"], {"target_type": "character", "target_id": cid}), "binding_relation", "wrong subject")
        depots = [avatar["raw"]["skillDepotId"], *avatar["raw"].get("candSkillDepotIds", [])]
        require(all(integer(v, 0) for v in depots) and did in depots, "binding_relation", "depot not owned by avatar")
        require(config["definition"]["definition_kind"] == "avatar_config" and config["json_pointer"] == ""
                and claim["resource_role"] == "related_avatar_config", "binding_relation", "wrong resource kind or role")
        require(same(avatar["raw"].get("combatConfigHash"), claim["combat_config_hash"]), "binding_stale", "opaque combat hash changed")
        config_refs = refs_for(config, snapshot)
        require(len(config_refs) <= 64 and same(config_refs, claim["mounts"]), "binding_stale", "complete mount directory changed")
        require(same([structural_ref(r) for r in material["config_references"]], config_refs), "binding_material", "saved reference body differs from raw")
        require(type(claim["anchors"]) is list and 1 <= len(claim["anchors"]) <= 32,
                "binding_fact", "finite nonempty anchors required")
        for anchor in claim["anchors"]:
            require(set(anchor) == {"slot", "skill", "proud", "talent", "proud_reference", "talent_reference", "mount_reference"},
                    "binding_fact", "unknown anchor fields")
            skill, proud, talent = (nodes[anchor[k]] for k in ("skill", "proud", "talent"))
            slot = anchor["slot"]
            require(slot in ("normal", "skill", "burst"), "binding_relation", "anchor slot unsupported")
            slots = {"normal": depot["raw"]["skills"][0], "skill": depot["raw"]["skills"][1], "burst": depot["raw"]["energySkill"]}
            require(integer(slots[slot]) and same(slots[slot], skill["raw"]["id"]), "binding_relation", "anchor skill not selected depot slot")
            require(integer(skill["raw"]["proudSkillGroupId"]) and same(skill["raw"]["proudSkillGroupId"], proud["raw"]["proudSkillGroupId"]),
                    "binding_relation", "anchor parameter group differs")
            p_ref, t_ref, m_ref = (anchor[k] for k in ("proud_reference", "talent_reference", "mount_reference"))
            require(any(same(p_ref, r) for r in refs_for(proud, snapshot))
                    and p_ref["target_kind"] == "talent_config"
                    and same(p_ref["target_key"], talent["definition"]["exact_key"]), "binding_relation", "proud config edge changed")
            require(any(same(t_ref, r) for r in refs_for(talent, snapshot))
                    and any(same(m_ref, r) for r in config_refs)
                    and t_ref["target_kind"] == m_ref["target_kind"] == "ability"
                    and same(t_ref["target_key"], m_ref["target_key"]), "binding_relation", "anchor exact ability edge changed")
        require(type(roles["peer_controls"]) is list and len(roles["peer_controls"]) <= 4
                and len(set(roles["peer_controls"])) == len(roles["peer_controls"]), "binding_fact", "peer control list invalid")
        for control_id in roles["peer_controls"]:
            control = nodes[control_id]
            require(control["definition"]["definition_kind"] == "avatar_config" and control_id != roles["config"],
                    "binding_relation", "invalid peer control")
        review = record["review"]["status"]
        head_matches = (type(current_head) is dict and set(current_head) == {"record_id", "revision", "record_fingerprint"}
                        and current_head["record_id"] == record["id"] and integer(current_head["revision"])
                        and same(current_head["revision"], record["revision"])
                        and current_head["record_fingerprint"] == fingerprint({"record": record}))
        selected = review == "approved" and head_matches
        unresolved = [r["reference_id"] for r in material["config_references"] if r["resolution_status"] != "resolved"]
        result = {"contract": CONTRACT, "material_status": "matched", "review_status": review,
                  "head_status": "matched_caller_material" if head_matches else "missing_or_changed",
                  "binding_status": "reviewed_material_match" if selected else "pending_review_or_head",
                  "related_config": binding_for(config) if selected else None,
                  "default_mount": "unproven",
                  "verification_scope": "materialized_consistency_no_source_authority", "requires_fresh_registry_admission": True,
                  "action_dispatch": "pending", "capacity_closure_status": "pending", "whole_avatar_coverage": "partial",
                  "execution_authority": False, "hash_algorithm": "not_decoded", "unresolved_mounted_reference_ids": unresolved,
                  "input_usage": usage}
        bounded(result)
        return copy.deepcopy(result)
    except BaseBindingError:
        raise
    except (KeyError, IndexError, TypeError, ValueError) as exc:
        raise BaseBindingError("binding_material", "malformed bound material") from exc
