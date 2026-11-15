"""Private, same-session full-parent bridge for finite reference particle dispatch."""
from __future__ import annotations

import copy
import re
from dataclasses import dataclass

from genshin_db.runtime_catalog import (
    RuntimeConfigError,
    dumps,
    excel_key,
    fingerprint,
    resolve_pointer,
)
from genshin_runtime.energy_trigger_identity import (
    LIMITS,
    build_bridge,
    energy_trigger_source_payload,
    family_for,
    recipe,
    reference_contract,
)


@dataclass(frozen=True)
class EnergyDispatchContext:
    public_projection: dict
    parameter_context: dict


def _invalid(message):
    raise RuntimeConfigError(message, "dataset_invalid", 500)


def _limit(kind):
    raise RuntimeConfigError("产球触发完整来源超限；未截断原文", "response_too_large", 413,
                             {"limit_kind": kind, "limits": dict(LIMITS)})


def _request(origin, identity):
    from genshin_db.runtime_queries import _integer
    from genshin_db.validation import version_line
    if not isinstance(origin, str) or re.fullmatch("[0-9a-f]{64}", origin) is None:
        raise ValueError("origin_reference_id必须为legacy引用SHA256")
    if not isinstance(identity, dict) or set(identity) != {"version_line", "snapshot_id", "selection", "param_index", "metadata_candidate_id"}:
        raise ValueError("parent_identity字段不完整或未知")
    selection = identity["selection"]
    if not isinstance(selection, dict) or set(selection) != {"character_id", "skill_depot_id", "skill_id", "constellation", "talent_level"}:
        raise ValueError("父动作选择字段不完整或未知")
    for key in ("character_id", "skill_depot_id", "skill_id"):
        _integer(selection[key], key)
    _integer(selection["constellation"], "constellation", 0, 6)
    _integer(selection["talent_level"], "talent_level", 1, 15)
    _integer(identity["param_index"], "param_index", 0, 63)
    version_line(identity["version_line"])
    for key, pattern in (("snapshot_id", "[0-9a-f]{64}"), ("metadata_candidate_id", r"attack\.[0-9a-f]{64}")):
        value = identity[key]
        if value is not None and (not isinstance(value, str) or re.fullmatch(pattern, value) is None):
            raise ValueError(key + "身份格式错误")
    return copy.deepcopy(identity)


class _Reader:
    def __init__(self, conn, state, query):
        from genshin_db.runtime_queries import _Nodes
        self.conn, self.state, self.line = conn, state, state["version_line"]
        self.nodes = _Nodes(conn, self.line)
        self.entries, self.refs = {}, {}
        for value in [query["origin"]["parent"], query["origin"]["owner"], *query["material"]["candidates"]]:
            if value is not None:
                self.entries[value["definition"]["definition_id"]] = copy.deepcopy(value)
        self.query_entries = set(self.entries)

    def entry(self, ident):
        from genshin_db.runtime_queries import _definition_summary, _row
        if ident in self.entries:
            return self.entries[ident]
        if len(self.entries) >= LIMITS["max_nodes"]:
            _limit("nodes")
        row = _row(self.conn, self.line, ident)
        if row["snapshot_id"] != self.state["snapshot_id"]:
            raise RuntimeConfigError("触发父链跨快照", "mixed_snapshot", 500)
        raw, source = self.nodes.node(row)
        kind, path, ptr = row["definition_kind"], row["source_path"], row["json_pointer"]
        if kind in ("ability", "modifier"):
            from genshin_db.runtime_gadget_source import _Reader as IdentityReader
            IdentityReader.identity(self, row, raw)
        else:
            source_row, root = self.nodes.source(path)
            family = source_row["source_family"]
            if kind == "excel_record":
                if family != "excel" or not isinstance(root, list) or not re.fullmatch(r"/(0|[1-9][0-9]*)", ptr):
                    _invalid("参数入口不是精确Excel原行")
                key = excel_key(path, raw, int(ptr[1:]), indexer_version=self.nodes.indexer_version)
            elif kind == "talent_config":
                if family != "talent" or not isinstance(raw, list) or ptr.count("/") != 1:
                    _invalid("参数写者不是精确Talent完整列表")
                key = ptr[1:].replace("~1", "/").replace("~0", "~")
            elif kind == "avatar_config":
                if family != "avatar" or ptr != "" or not isinstance(raw, dict):
                    _invalid("挂载来源不是Avatar完整原配置")
                key = path
            else:
                _invalid("产球触发访问未知定义类型")
            expected = {"definition_id": fingerprint([self.state["snapshot_id"], path, ptr, kind]),
                "exact_key_json": dumps(key), "owner_definition_id": None, "variant_key": None,
                "namespace_key": family, "summary_json": dumps({"name": key, "kind": kind})}
            if any(row[k] != v for k, v in expected.items()):
                _invalid("触发来源索引身份与原文不一致")
        value = {"definition": _definition_summary(row), **source, "node_sha256": row["node_sha256"], "payload": raw}
        self.entries[ident] = value
        if len(dumps(list(self.entries.values())).encode("utf8")) > LIMITS["max_bytes"]:
            _limit("node_bytes")
        return value

    def references(self, ident):
        from genshin_db.runtime_queries import _references
        # Inspect all edge identities before the raw/reference validator can expand them.
        rows = self.conn.execute("SELECT reference_id,target_kind,target_key_json FROM runtime_reference "
            "WHERE version_line=? AND from_definition_id=? LIMIT ?", (self.line, ident, LIMITS["max_edges"] + 1)).fetchall()
        unseen = {r["reference_id"] for r in rows} - self.refs.keys()
        if len(rows) > LIMITS["max_edges"] or len(self.refs) + len(unseen) > LIMITS["max_edges"]:
            _limit("edges")
        for row in rows:
            candidates = self.conn.execute("SELECT definition_id FROM runtime_definition WHERE version_line=? "
                "AND definition_kind=? AND exact_key_json=? LIMIT ?",
                (self.line, row["target_kind"], row["target_key_json"], LIMITS["max_candidates"] + 1)).fetchall()
            if len(candidates) > LIMITS["max_candidates"]:
                _limit("reference_candidates")
        for ref in _references(self.conn, self.line, ident, self.nodes):
            self.refs[ref["reference_id"]] = ref
        return [self.refs[r["reference_id"]] for r in rows]

    def mounting(self, owner, attack, family, skill_id):
        if not family["mount_required"]:
            return None
        if resolve_pointer(owner["payload"], family["skill_id_path"]) != skill_id:
            return None
        # Candidate discovery is a set, but visiting it inserts public source
        # entries/references. Keep the legacy primary-key scan order explicit
        # so an optional physical index cannot change confirmation identities.
        rows = self.conn.execute("SELECT r.from_definition_id FROM runtime_reference r JOIN runtime_definition d "
            "ON d.definition_id=r.from_definition_id AND d.version_line=r.version_line "
            "WHERE r.version_line=? AND r.resolved_definition_id=? AND d.definition_kind='avatar_config' "
            "ORDER BY r.reference_id LIMIT ?",
            (self.line, owner["definition"]["definition_id"], LIMITS["max_candidates"] + 1)).fetchall()
        if len(rows) > LIMITS["max_candidates"]:
            _limit("mount_candidates")
        matches = []
        for ident in dict.fromkeys(r["from_definition_id"] for r in rows):
            entry = self.entry(ident)
            refs = self.references(ident)
            desired = {owner["definition"]["definition_id"], attack["definition"]["definition_id"]}
            selected = [r for r in refs if r["resolved_definition_id"] in desired and r["resolution_status"] == "resolved"]
            if {r["resolved_definition_id"] for r in selected} != desired:
                continue
            mount = recipe()["mount"]
            try:
                mounted_raw = resolve_pointer(entry["payload"], mount["new_pointer"])
            except (KeyError, IndexError, TypeError):
                # The catalog can understand another raw spelling while this
                # finite reference recipe has not admitted its control shape.
                continue
            if mounted_raw != mount["new_raw"]:
                continue
            matches.append({"status": "ready", "entry_id": ident,
                "exact_reference_ids": sorted(r["reference_id"] for r in selected),
                "identity_scope": "same_config_membership_and_exact_skill_id_not_combat_hash_mapping"})
        return matches[0] if len(matches) == 1 else None


def _parameters(db_path, identity, state):
    from genshin_db.equipment_queries import borrow_read_cache
    from genshin_db.runtime_action_parameter import get_action_parameter_context
    target = identity["selection"]
    cache = borrow_read_cache(db_path, "attack-metadata-fresh-v1")
    args = {**target, "version_line": identity["version_line"], "snapshot_id": state["snapshot_id"]}
    canonical_key = ("parameters", fingerprint(args))
    if canonical_key in cache:
        return copy.deepcopy(cache[canonical_key])
    if sum(k[0] == "parameters" for k in cache) >= 32:
        _limit("cached_selections")
    value = get_action_parameter_context(db_path, **args)
    cache[canonical_key] = copy.deepcopy(value)
    return value


def get_energy_dispatch_context(db_path, *, origin_reference_id, parent_identity):
    from genshin_db.equipment_queries import borrow_read_cache
    from genshin_db.runtime_energy_generation import (
        get_runtime_energy_generation_profile,
    )
    from genshin_db.runtime_queries import _read
    identity = _request(origin_reference_id, parent_identity)
    with _read(db_path, identity["version_line"], identity["snapshot_id"]) as (conn, state):
        cache = borrow_read_cache(db_path, "energy-dispatch-fresh-v1")
        key = (state["snapshot_id"], origin_reference_id)
        if key not in cache:
            if len(cache) >= 32:
                _limit("cached_origins")
            cache[key] = get_runtime_energy_generation_profile(db_path, origin_reference_id=origin_reference_id,
                version_line=identity["version_line"], snapshot_id=state["snapshot_id"])
        query = copy.deepcopy(cache[key])
        context = _parameters(db_path, identity, state)
        facts = context["facts"]
        reader = _Reader(conn, state, query)
        issues, bridge = [], {"status": "pending", "trigger_family": None, "variant": None}
        def issue(code):
            issues.append({"code": code, "blocks_trigger": True})
        if state["upstream_commit"] != recipe()["commit"]:
            issue("energy_trigger_mapping_unreviewed")
        if identity["selection"]["constellation"] != 0 or facts["level"]["effective_level"] != 10 or "skill" not in facts["selection"]["matching_slots"]:
            issue("energy_trigger_parent_scope_unsupported")
        owner = query["origin"]["owner"] or query["origin"]["parent"]
        operation_relative = query["origin"]["operation"]["raw_pointer"][len(owner["json_pointer"]):]
        valid = []
        # Only explicit skill-level parameter targets are eligible parent attacks.
        for talent in facts["talents"]:
            if talent["role"] != "skill_level":
                continue
            entry = reader.entry(talent["definition"]["definition_id"])
            for binding in facts["parameter_bindings"]:
                if (binding["role"] != "skill_level" or binding["raw_expression"] != f'%{identity["param_index"] + 1}'
                        or binding["target"]["resolution_status"] != "resolved" or len(binding["target"]["candidate_ids"]) != 1):
                    continue
                target = reader.entry(binding["target"]["candidate_ids"][0])
                family = family_for(owner["payload"], target["payload"], operation_relative)
                if family is None or family["parameter_special"] != binding["parameter_name"]:
                    continue
                operations = entry["payload"]
                if not any(operations == t["raw"] for t in recipe()["fixture_evidence"]["talents"]):
                    issue("energy_trigger_complete_talent_unreviewed")
                    continue
                related = [(i, op) for i, op in enumerate(operations) if isinstance(op, dict)
                    and op.get("IIBMNPFLNMJ") == target["definition"]["exact_key"]
                    and op.get("PEOJKHKHAKL") == family["parameter_special"]]
                expected = {"$type": "BIAHEMMGABC", "GBPFBNHLOFD": True,
                    "IIBMNPFLNMJ": target["definition"]["exact_key"], "PEOJKHKHAKL": family["parameter_special"],
                    "PCABAADALND": f'%{identity["param_index"] + 1}'}
                if len(related) != 1 or related[0][1] != expected:
                    issue("energy_trigger_parameter_writer_unsupported")
                    continue
                valid.append((family, target, entry, related[0][0]))
        if len(valid) != 1:
            issue("energy_trigger_exact_parent_bridge_missing")
        elif not issues:
            family, attack, talent, writer = valid[0]
            for name in ("avatar", "depot", "skill", "proud_skill"):
                reader.entry(facts["records"][name]["definition"]["definition_id"])
            proud = reader.entry(facts["records"]["proud_skill"]["definition"]["definition_id"])
            for entry in (owner, query["origin"]["parent"], attack, talent):
                reader.references(entry["definition"]["definition_id"])
            mounting = reader.mounting(owner, attack, family, identity["selection"]["skill_id"])
            if family["mount_required"] and mounting is None:
                issue("energy_trigger_mount_identity_pending")
            if family["metadata_required"]:
                from genshin_db.runtime_attack_metadata import (
                    get_attack_metadata_context,
                )
                args = {**identity["selection"], "version_line": identity["version_line"], "snapshot_id": state["snapshot_id"]}
                metadata = get_attack_metadata_context(db_path, candidate_id=identity["metadata_candidate_id"], **args).public_projection
                selected = metadata["selected_candidate"]
                if (metadata["status"] != "ready" or selected is None
                        or selected["projection"]["parameter"]["param_index"] != identity["param_index"]
                        or selected["parent"]["owner_definition_id"] != attack["definition"]["definition_id"]):
                    issue("energy_trigger_metadata_parent_mismatch")
            elif identity["metadata_candidate_id"] is not None:
                issue("energy_trigger_scalar_bridge_requires_no_metadata")
            bridge = build_bridge(family, owner=owner, attack=attack, talent=talent, proud=proud,
                param_index=identity["param_index"], writer_index=writer,
                metadata_candidate_id=identity["metadata_candidate_id"], mounting=mounting)
        if query["status"] != "ready":
            issue("energy_generation_units_pending")
        if issues:
            bridge["status"] = "pending"
        result = {"projection_version": 1, "graph_scope": "energy_trigger_source_v1", "snapshot": copy.deepcopy(state),
            "parent_identity": identity, "selection": copy.deepcopy(facts["selection"]),
            "status": "pending" if issues else "ready", "source_status": "pending" if issues else "ready",
            "whole_source_status": "partial", "execution_support": "not_evaluated", "source_query": query,
            "source_bundle": {"entries": [v for k, v in reader.entries.items() if k not in reader.query_entries],
                "references": list(reader.refs.values()), "parent_bridge": bridge,
                "source_closure": {"status": "partial", "boundaries": ["full_ability_execution", "combat_config_hash_mapping",
                    "external_controller_changes", "hitlag"], "related_unresolved": []}},
            "reference_contract": reference_contract(), "issues": issues, "limits": dict(LIMITS)}
        result["semantic_dependency"] = energy_trigger_source_payload(result)
        result["source_fingerprint"] = fingerprint(result["semantic_dependency"])
        result["content_fingerprint"] = fingerprint(result)
        if len(dumps(result).encode("utf8")) > LIMITS["max_bytes"]:
            _limit("response_bytes")
        return EnergyDispatchContext(copy.deepcopy(result), copy.deepcopy(context))
