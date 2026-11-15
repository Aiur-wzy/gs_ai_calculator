"""Bounded Gadget identity graph reads; mounted abilities are not dispatch events."""
from __future__ import annotations

import copy
import json
import re

from genshin_db.runtime_catalog import (
    RuntimeConfigError,
    dumps,
    escape,
    fingerprint,
    resolve_pointer,
)

GRAPH_SCOPE = "gadget_identity_v1"
GADGET_LIMITS = {"max_bytes": 256 * 1024, "max_mounts": 32, "max_nodes": 64,
                 "max_edges": 128, "max_issues": 128}
GADGET_PATH = "ExcelBinOutput/GadgetExcelConfigData.json"


def gadget_source_capabilities():
    return {"projection_version": 1, "graph_scope": GRAPH_SCOPE, "minimum_indexer_version": "5",
            "limits": dict(GADGET_LIMITS), "selection": "gadget_id为精确正整数；origin_reference_id可省略，提供时须为原legacy创建引用ID",
            "origin": "同时核对legacy引用、新object边、完整原父节点；modifier附完整owner Ability",
            "status": "ready仅证明直接ID/配置/明确挂载关系；缺abilities不等于空列表",
            "execution_support": "not_evaluated", "creation": "not_evaluated", "controller": "not_evaluated",
            "upgrade_reason": "gadget_index_upgrade_required",
            "notes": ["不倒查全部创建者，不展开挂载能力的内部引用",
                      "同名多配置或同名多Ability variant保持歧义，不选首项",
                      "原legacy闭包不改变；新图独立查询，挂载不表示执行"]}


def _validate(gadget_id, origin_reference_id):
    from genshin_db.runtime_queries import _integer
    _integer(gadget_id, "gadget_id")
    if origin_reference_id is not None and (not isinstance(origin_reference_id, str)
            or re.fullmatch(r"[0-9a-f]{64}", origin_reference_id) is None):
        raise ValueError("origin_reference_id必须为64位小写legacy引用SHA256")


def _failure(message):
    raise RuntimeConfigError(message, "dataset_invalid", 500)


def _reference(row):
    result = dict(row)
    for key in ("target_key_json", "scope_json", "candidate_ids_json"):
        result[key.removesuffix("_json")] = json.loads(result.pop(key))
    return result


class _Reader:
    def __init__(self, conn, state):
        from genshin_db.runtime_queries import _Nodes
        self.conn, self.state, self.line = conn, state, state["version_line"]
        self.nodes = _Nodes(conn, self.line)
        self.entries, self.edges, self.issues = {}, {}, []
        self.node_bytes = 0

    def issue(self, code, path):
        if len(self.issues) >= GADGET_LIMITS["max_issues"]:
            self.limit("issues")
        self.issues.append({"code": code, "path": path})

    @staticmethod
    def limit(kind):
        raise RuntimeConfigError("Gadget完整来源超过查询限额；未截断原文", "response_too_large", 413, {"limit_kind": kind})

    def identity(self, row, raw):
        """Rebuild the accessed identity from source structure, not mutable indexes."""
        path, pointer, kind = row["source_path"], row["json_pointer"], row["definition_kind"]
        source, source_root = self.nodes.source(path)
        family = source["source_family"]
        parts = [part.replace("~1", "/").replace("~0", "~") for part in pointer[1:].split("/")] if pointer else []
        if pointer and pointer != "/" + "/".join(escape(p) for p in parts):
            _failure("Gadget来源定义的原指针不是规范JSON pointer")
        owner, variant = None, None
        if kind == "gadget_record":
            if (family != "gadget_excel" or path != GADGET_PATH or len(parts) != 1
                    or not parts[0].isdigit() or str(int(parts[0])) != parts[0]
                    or not isinstance(source_root, list) or not isinstance(raw, dict)
                    or type(raw.get("id")) is not int or not 1 <= raw["id"] <= 2**53-1):
                _failure("Gadget记录身份不对应原Excel行")
            key = raw["id"]
        elif kind in ("gadget_config", "gadget_config_entry"):
            if family != "gadget" or not isinstance(source_root, dict):
                _failure("Gadget配置身份不对应原文件")
            if kind == "gadget_config":
                if pointer:
                    _failure("Gadget文件定义不是原文件根")
                key = path
            else:
                if len(parts) != 1 or not isinstance(raw, dict) or not parts[0].strip():
                    _failure("Gadget具名配置身份不对应原根键")
                key = parts[0]
                owner = fingerprint([self.state["snapshot_id"], path, "", "gadget_config"])
        elif kind in ("ability", "modifier"):
            if (family != "ability" or not isinstance(source_root, list) or len(parts) < 2
                    or not parts[0].isdigit() or str(int(parts[0])) != parts[0]):
                _failure("Ability身份不对应原数组与variant")
            variant = parts[1]
            ability_pointer = "/" + "/".join(escape(p) for p in parts[:2])
            ability_raw = resolve_pointer(source_root, ability_pointer)
            view = self.nodes.normalization.normalize(ability_raw, kind="ability") if self.nodes.normalization else ability_raw
            if not isinstance(view, dict) or not isinstance(view.get("abilityName"), str):
                _failure("原Ability名称缺失")
            if kind == "ability":
                if len(parts) != 2:
                    _failure("Ability原指针不是完整variant")
                key = view["abilityName"]
            else:
                if len(parts) != 4 or not isinstance(view.get("modifiers"), dict) or parts[-1] not in view["modifiers"]:
                    _failure("modifier原指针不对应所属Ability的完整具名modifier")
                key = parts[-1]
                relative = "/modifiers/" + escape(key)
                if self.nodes.normalization:
                    relative = self.nodes.normalization.raw_pointer(ability_raw, relative, kind="ability")
                if pointer != ability_pointer + relative:
                    _failure("modifier原指针与规范归属不一致")
                owner = fingerprint([self.state["snapshot_id"], path, ability_pointer, "ability"])
        else:
            _failure("Gadget查询访问了非受支持种类定义")
        expected = {"definition_id": fingerprint([self.state["snapshot_id"], path, pointer, kind]),
                    "exact_key_json": dumps(key), "owner_definition_id": owner, "variant_key": variant,
                    "namespace_key": path.split("/")[2] if path.startswith("BinOutput/Ability/") else family,
                    "summary_json": dumps({"name": key, "kind": kind})}
        if any(row[k] != value for k, value in expected.items()):
            _failure("Gadget来源定义身份与完整原文/归属不一致")

    def entry(self, row, key="payload"):
        from genshin_db.runtime_queries import _definition_summary
        if row["version_line"] != self.line or row["snapshot_id"] != self.state["snapshot_id"]:
            raise RuntimeConfigError("Gadget节点跨快照", "mixed_snapshot", 500)
        ident = row["definition_id"]
        if ident not in self.entries:
            if len(self.entries) >= GADGET_LIMITS["max_nodes"]:
                self.limit("nodes")
            raw, origin = self.nodes.node(row)
            self.identity(row, raw)
            value = {"definition": _definition_summary(row), **origin,
                     "node_sha256": row["node_sha256"], "payload": raw}
            self.node_bytes += len(dumps(value).encode("utf8"))
            if self.node_bytes > GADGET_LIMITS["max_bytes"]:
                self.limit("node_bytes")
            self.entries[ident] = value
        value = self.entries[ident]
        return {**{k: v for k, v in value.items() if k != "payload"}, key: value["payload"]}

    def object_refs(self, row):
        from genshin_db.runtime_store import validate_definition_object_references
        raw = self.entry(row)["payload"]
        refs = validate_definition_object_references(self.conn, self.line, row, raw,
            normalization=self.nodes.normalization, indexer_version=self.nodes.indexer_version)
        for ref in refs:
            if ref["version_line"] != self.line or ref["snapshot_id"] != self.state["snapshot_id"]:
                raise RuntimeConfigError("Gadget引用跨快照", "mixed_snapshot", 500)
            self.edges[ref["reference_id"]] = _reference(ref)
            if len(self.edges) > GADGET_LIMITS["max_edges"]:
                self.limit("edges")
        return [self.edges[r["reference_id"]] for r in refs]

    def candidates(self, ref, kind):
        from genshin_db.runtime_queries import _row
        if ref["target_kind"] != kind:
            _failure("Gadget关系目标种类不符")
        candidates = []
        for ident in ref["candidate_ids"]:
            row = _row(self.conn, self.line, ident)
            if row["definition_kind"] != kind or json.loads(row["exact_key_json"]) != ref["target_key"]:
                _failure("Gadget关系的精确目标身份不符")
            candidates.append(self.entry(row))
        selected = next((r for r in candidates if r["definition"]["definition_id"] == ref["resolved_definition_id"]), None)
        if ref["resolution_status"] == "resolved" and (len(candidates) != 1 or selected is None):
            _failure("Gadget已解析边缺少唯一目标")
        return candidates, selected

    def gadget_view(self, raw):
        if self.nodes.normalization:
            return self.nodes.normalization.normalize({"entry": raw}, kind="gadget_config")["entry"]
        return raw

    def gadget_pointer(self, raw, pointer):
        if self.nodes.normalization:
            translated = self.nodes.normalization.raw_pointer({"entry": raw}, "/entry"+pointer, kind="gadget_config")
            return translated.removeprefix("/entry")
        return pointer

    def origin(self, ident, gadget_id, record_id):
        from genshin_db.runtime_queries import _references, _row
        if ident is None:
            return {"status": "not_requested", "legacy_reference": None, "object_reference": None,
                    "parent": None, "owner_ability": None, "operation": None}
        stored = self.conn.execute("SELECT * FROM runtime_reference WHERE version_line=? AND reference_id=?",
                                   (self.line, ident)).fetchone()
        if stored is None:
            raise RuntimeConfigError("原创建引用未收录", "not_found", 404, {"origin_reference_id": ident})
        parent_row = _row(self.conn, self.line, stored["from_definition_id"])
        if parent_row["definition_kind"] not in ("ability", "modifier"):
            raise RuntimeConfigError("来源不是受支持Ability/modifier创建引用", "unsupported_source", 409)
        parent = self.entry(parent_row)
        legacy = next((r for r in _references(self.conn, self.line, parent_row["definition_id"], self.nodes)
                       if r["reference_id"] == ident), None)
        links = [r for r in self.object_refs(parent_row) if r["legacy_reference_id"] == ident]
        if legacy is None or len(links) != 1:
            raise RuntimeConfigError("原引用没有唯一受支持Gadget创建关系", "unsupported_source", 409)
        link = links[0]
        if (link["relation_kind"] != "gadget_id_reference" or link["target_kind"] != "gadget_record"
                or link["json_pointer"] != legacy["json_pointer"] or link["source_path"] != legacy["source_path"]
                or link["from_definition_id"] != legacy["from_definition_id"]):
            _failure("新旧Gadget创建边身份不一致")
        if type(link["target_key"]) is not int or link["target_key"] != gadget_id:
            raise ValueError("origin_reference_id指向另一个gadget_id")
        if link["resolution_status"] != "resolved" or link["resolved_definition_id"] != record_id:
            _failure("已选Gadget记录与创建边的精确目标不一致")
        base = parent_row["json_pointer"]
        pointer = link["json_pointer"]
        if not pointer.startswith(base + "/"):
            _failure("Gadget创建指针不属于完整原父节点")
        relative = pointer[len(base):]
        raw_id = resolve_pointer(parent["payload"], relative)
        op_relative = relative.rsplit("/", 1)[0]
        raw_op = resolve_pointer(parent["payload"], op_relative)
        canonical = self.nodes.normalization.normalize(raw_op, kind="operation") if self.nodes.normalization else raw_op
        field = {"CreateGadget": "gadgetID", "TriggerBullet": "bulletID"}.get(canonical.get("$type"))
        if (field is None or canonical.get(field) != gadget_id or type(raw_id) is not int or raw_id != gadget_id
                or legacy["relation_kind"] != "external_asset" or legacy["target_key"] != gadget_id):
            _failure("Gadget创建操作/原ID与双图引用不一致")
        owner = None
        if parent_row["definition_kind"] == "modifier":
            owner_row = _row(self.conn, self.line, parent_row["owner_definition_id"])
            if (owner_row["definition_kind"] != "ability" or owner_row["source_path"] != parent_row["source_path"]
                    or not parent_row["json_pointer"].startswith(owner_row["json_pointer"] + "/")):
                _failure("modifier创建来源缺少精确owner Ability")
            owner = self.entry(owner_row)
            relative_modifier = parent_row["json_pointer"][len(owner_row["json_pointer"]):]
            if resolve_pointer(owner["payload"], relative_modifier) != parent["payload"]:
                _failure("modifier与完整owner Ability原文不一致")
        return {"status": "verified_relationship", "legacy_reference": legacy, "object_reference": link,
                "parent": parent, "owner_ability": owner, "operation": {"kind": canonical["$type"],
                    "json_pointer": base + op_relative, "raw": raw_op, "node_sha256": fingerprint(raw_op),
                    "id_pointer": pointer, "raw_id": raw_id}}


def get_runtime_gadget_source_profile(db_path, *, gadget_id, origin_reference_id=None,
                                     version_line="release", snapshot_id=None):
    from genshin_db.runtime_queries import _definition_summary, _lookup, _read, _row

    _validate(gadget_id, origin_reference_id)
    with _read(db_path, version_line, snapshot_id) as (conn, state):
        if int(state["indexer_version"]) < 5:
            raise RuntimeConfigError("Gadget精确身份查询需要同快照更新运行索引", "gadget_index_upgrade_required", 409,
                                     {"stored_indexer_version": state["indexer_version"], "required_indexer_version": "5"})
        reader = _Reader(conn, state)
        rows = _lookup(conn, version_line, "gadget_record", gadget_id, GADGET_PATH)
        if len(rows) != 1:
            raise RuntimeConfigError("Gadget精确ID不存在或不唯一", "not_found" if not rows else "ambiguous_reference",
                                     404 if not rows else 409, {"gadget_id": gadget_id})
        row = rows[0]
        record = reader.entry(row, "record")
        if type(record["record"].get("id")) is not int or record["record"]["id"] != gadget_id:
            _failure("Gadget原行ID与索引不一致")
        origin = reader.origin(origin_reference_id, gadget_id, row["definition_id"])
        refs = [r for r in reader.object_refs(row) if r["relation_kind"] == "gadget_config_name"]
        config = {"status": "pending", "reference": None, "candidates": [], "selected": None, "file_owner": None}
        mounts, abilities_status, field_status = [], "not_available", "not_available"
        if len(refs) > 1:
            _failure("Gadget行存在多个jsonName索引关系")
        if not refs:
            reader.issue("gadget_config_name_missing", "record.jsonName")
        else:
            ref = refs[0]
            config["reference"] = ref
            candidates, selected = reader.candidates(ref, "gadget_config_entry")
            config.update(candidates=candidates, selected=selected)
            config["status"] = "ready" if selected else ref["resolution_status"]
            if selected is None:
                reader.issue("gadget_config_" + ref["resolution_status"], "config")
            else:
                chosen_row = _row(conn, version_line, selected["definition"]["definition_id"])
                file_row = _row(conn, version_line, chosen_row["owner_definition_id"])
                if (file_row["definition_kind"] != "gadget_config" or file_row["json_pointer"] != ""
                        or file_row["source_path"] != chosen_row["source_path"]):
                    _failure("Gadget子配置缺少精确文件根归属")
                file_raw, _ = reader.nodes.node(file_row)
                reader.identity(file_row, file_raw)
                config["file_owner"] = _definition_summary(file_row)  # no sibling traversal or complete file echo
                raw = selected["payload"]
                view = reader.gadget_view(raw)
                mount_refs = reader.object_refs(chosen_row)
                if not isinstance(view, dict) or "abilities" not in view:
                    field_status = "absent"
                    abilities_status = "missing"
                    reader.issue("gadget_mount_list_missing", "config.abilities")
                elif not isinstance(view["abilities"], list):
                    field_status = "null" if view["abilities"] is None else "invalid_type"
                    abilities_status = "invalid"
                    reader.issue("gadget_mount_list_invalid", "config.abilities")
                else:
                    field_status = "present" if view["abilities"] else "empty"
                    if len(view["abilities"]) > GADGET_LIMITS["max_mounts"]:
                        reader.limit("mounts")
                    abilities_status = "ready"
                    raw_list_pointer = reader.gadget_pointer(raw, "/abilities")
                    raw_list = resolve_pointer(raw, raw_list_pointer)
                    for i, item in enumerate(view["abilities"]):
                        pointer = chosen_row["json_pointer"] + raw_list_pointer + "/" + str(i)
                        matching = [r for r in mount_refs if r["relation_kind"] == "gadget_ability_mount"
                                    and r["json_pointer"].rsplit("/", 1)[0] == pointer]
                        value = {"mount_pointer": pointer, "raw": raw_list[i], "status": "pending",
                                 "reference": None, "candidates": [], "selected": None}
                        if len(matching) > 1:
                            _failure("Gadget挂载项对应多个名称引用")
                        if not matching:
                            reader.issue("gadget_mount_name_missing", pointer)
                        else:
                            reference = matching[0]
                            candidates, selected_ability = reader.candidates(reference, "ability")
                            value.update(reference=reference, candidates=candidates, selected=selected_ability,
                                         status="ready" if selected_ability else reference["resolution_status"])
                            if selected_ability is None:
                                reader.issue("gadget_mount_" + reference["resolution_status"], pointer)
                        mounts.append(value)
                    if any(m["status"] != "ready" for m in mounts):
                        abilities_status = "pending"
        snapshot = copy.deepcopy(state)
        result = {"projection_version": 1, "graph_scope": GRAPH_SCOPE,
                  "status": "pending" if reader.issues else "ready", "snapshot": snapshot, "selection": {"gadget_id": gadget_id},
                  "origin": origin, "record": record, "config": config, "mounted_abilities": mounts,
                  "mount_list_status": abilities_status,
                  "mount_list_field_status": field_status,
                  "sources": [{k: value[k] for k in ("definition", "source_path", "source_sha256", "json_pointer", "node_sha256")}
                              for _, value in sorted(reader.entries.items())],
                  "coverage": {"identity_chain": config["status"], "ability_mounts": abilities_status,
                               "recursive_ability_execution": "not_evaluated"},
                  "execution": {"status": "not_evaluated", "creation": "not_evaluated", "controller": "not_evaluated"},
                  "execution_support": "not_evaluated", "issues": reader.issues, "limits": dict(GADGET_LIMITS)}
        semantic_snapshot = {key: snapshot[key] for key in
            ("version_line", "snapshot_id", "upstream_commit", "indexer_version", "normalization") if key in snapshot}
        dependency = {key: result[key] for key in ("projection_version", "graph_scope", "selection", "origin", "record", "config", "mounted_abilities")}
        result["source_fingerprint"] = fingerprint({**dependency, "snapshot": semantic_snapshot})
        result["content_fingerprint"] = fingerprint(result)
        if len(dumps(result).encode("utf8")) > GADGET_LIMITS["max_bytes"]:
            reader.limit("response_bytes")
        return copy.deepcopy(result)
