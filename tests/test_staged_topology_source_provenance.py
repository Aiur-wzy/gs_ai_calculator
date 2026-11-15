from __future__ import annotations

import copy
import uuid

import pytest

from genshin_app.agent_chat.staged_candidate import StagedCandidateSession
from genshin_app.agent_chat.tools import _query
from genshin_app.equipment.event_topology import AUTHORITY, CONTRACT_ID
from genshin_app.equipment.files import fingerprint
from genshin_app.whiteboard import new_whiteboard
from genshin_db import queries


@pytest.fixture
def staged(tmp_path, monkeypatch):
    db = tmp_path / "engineering.db"
    db.touch()
    meta = {"version_lines": ["release"], "datasets": {"equipment": {"lines": {
        "release": {"state": "ready", "schema_version": 1,
                    "upstream_commit": "a" * 40,
                    "built_at": "2026-10-05T00:00:00Z"}}}}}
    monkeypatch.setattr(queries, "get_meta", lambda _: copy.deepcopy(meta))
    monkeypatch.setattr(queries, "get_character_detail", lambda *a, **kw: {
        "character": {"id": 1, "name": "工程角色",
                      "weapon_type": "WEAPON_SWORD_ONE_HAND",
                      "atk_90": 300, "hp_90": 12000, "def_90": 800}})
    monkeypatch.setattr(queries, "get_weapon_detail", lambda *a, **kw: {
        "weapon": {"id": 101, "name": "工程剑",
                   "weapon_type": "WEAPON_SWORD_ONE_HAND"},
        "effects": [], "data_version": None})
    monkeypatch.setattr(queries, "get_weapon_stats", lambda *a, **kw: {
        "properties": [{"prop_type": "FIGHT_PROP_BASE_ATTACK", "value": 500,
                        "source_refs": []}],
        "warnings": [], "data_version": {"snapshot_id": "fixture"}})
    monkeypatch.setattr(queries, "get_artifact_set_detail", lambda *a, **kw: {
        "artifact_set": {"id": 201, "name": "工程套装"}})
    return StagedCandidateSession(uuid.uuid4().hex, tmp_path / "session", db,
                                  "whiteboard", new_whiteboard())


def _call(session, name, **args):
    return session.call_tool(name, args)


def _transition(session, target):
    return _call(session, "gs_transition_phase", target_phase=target,
                 expected_revision=session.revision,
                 expected_stage_revision=session.stage_state["stage_revision"],
                 reason="来源引用测试进入下一阶段")


def _prepare(staged, *, equipment=False):
    assert _call(staged, "gs_add_character", expected_revision=staged.revision,
                 reason="添加工程角色", db_character_id=1)["code"] == 0
    if equipment:
        changed = _call(staged, "gs_patch_whiteboard", expected_revision=staged.revision,
            reason="选择工程装备", operations=[
                {"op": "replace", "path": "/team/0/weapon", "value": {
                    "weapon_id": 101, "name": "工程剑", "level": 90,
                    "ascension": 6, "refinement": 1,
                }},
                {"op": "add", "path": "/team/0/artifact_sets/-", "value": {
                    "set_id": 201, "name": "工程套装", "count": 4,
                }},
            ])
        assert changed["code"] == 0, changed
    assert _transition(staged, "action_checkpoint")["code"] == 0
    action = {"id": "action.skill", "character": "character-1",
              "action_type_id": "action.elemental_skill", "label": "元素战技",
              "start": 1, "variant": "tap", "notes": ""}
    result = _call(staged, "gs_patch_whiteboard", expected_revision=staged.revision,
        reason="建立动作检查点", operations=[{
            "op": "add", "path": "/actions/-", "value": action,
        }])
    assert result["code"] == 0, result
    assert _transition(staged, "event_topology")["code"] == 0


def _context_query(staged, request):
    collection = next((name for name in ("characters", "weapons", "artifact_sets")
                       if request.get(name)), "database")
    row = request.get(collection, [{}])[0]
    return _query(staged, {"request": request},
                  lambda _db, selected: {
                      "items": [{"entity_type": collection, "id": row.get("id"),
                                 "request": copy.deepcopy(selected),
                                 "data": {"status": "fixture"}}],
                  }, "context")


def _topology(staged, sources):
    checkpoint = staged._current_action_checkpoint()
    action_id = checkpoint["action_ids"][0]
    source_ids = [source["source_id"] for source in sources]
    return {
        "contract_id": CONTRACT_ID,
        "schema_version": 1,
        "topology_id": "topology.source-provenance",
        "authority": AUTHORITY,
        "declaration": {"character_semantics": "agent_declared",
                        "completeness": "declared_complete",
                        "note": "已完成来源范围验证"},
        "action_checkpoint": checkpoint,
        "dependencies": [{
            "dependency_id": "database_snapshot", "kind": "database_snapshot",
            "fingerprint": fingerprint(staged.database_snapshot), "required": True,
        }],
        "sources": sources,
        "nodes": [{
            "node_id": "node.action", "kind": "action_anchor", "status": "bound",
            "schedule": {"kind": "point",
                         "anchor": {"kind": "action", "ref_id": action_id,
                                    "phase": "start"},
                         "offset_seconds": 0, "order": 0},
            "source_ids": source_ids, "parameter_slots": [], "note": "",
        }],
        "edges": [],
    }


def _source(category, source_ref, *, source_id="source.fixture",
            disposition="modeled"):
    return {
        "source_id": source_id, "category": category,
        "owner_ref": "character-1", "description": "工程来源",
        "disposition": disposition, "node_ids": ["node.action"],
        "reason": "等待附录审核" if disposition == "pending" else None,
        "source_refs": [source_ref],
    }


def _write_topology(staged, topology):
    result = _call(staged, "gs_patch_candidate", expected_revision=staged.revision,
        reason="写入来源范围测试拓扑", operations=[{
            "op": "replace", "path": "/extensions/event_topology",
            "value": topology,
        }])
    assert result["code"] == 0, result
    return staged.topology_receipt()


def test_unrelated_query_cannot_be_copied_as_character_source(staged):
    _prepare(staged)
    unrelated = _context_query(staged, {
        "characters": [{"id": 2, "sections": ["character", "talents"]}],
    })
    receipt = _write_topology(staged, _topology(staged, [
        _source("talent", unrelated["source_ref"]),
    ]))
    assert receipt["status"] == "draft"
    assert receipt["stages"]["topology"]["status"] == "blocked"
    assert "event_topology_source_owner_mismatch" in {
        issue["code"] for issue in receipt["issues"]}


def test_database_appendix_requires_explicit_annotations_for_same_entity(staged):
    _prepare(staged)
    ordinary = _context_query(staged, {
        "characters": [{"id": 1, "sections": ["character", "talents"]}],
    })
    forged = {**ordinary["source_ref"], "kind": "database_appendix"}
    receipt = _write_topology(staged, _topology(staged, [
        _source("talent", forged),
    ]))
    assert receipt["status"] == "draft"
    assert "event_topology_source_kind_mismatch" in {
        issue["code"] for issue in receipt["issues"]}


def test_correct_character_weapon_and_artifact_queries_are_accepted(staged):
    _prepare(staged, equipment=True)
    character = _context_query(staged, {
        "characters": [{"id": 1, "sections": ["talents", "annotations"]}],
    })
    weapon = _context_query(staged, {
        "weapons": [{"id": 101, "sections": ["effects", "annotations"]}],
    })
    artifact = _context_query(staged, {
        "artifact_sets": [{"id": 201,
                           "sections": ["effects", "annotations"]}],
    })
    sources = [
        _source("talent", character["source_ref"],
                source_id="source.talent"),
        _source("weapon", weapon["source_ref"],
                source_id="source.weapon"),
        _source("artifact", artifact["source_ref"],
                source_id="source.artifact"),
    ]
    receipt = _write_topology(staged, _topology(staged, sources))
    assert receipt["status"] == "numeric_ready", {
        "issues": receipt["issues"],
        "board": staged._current_board()["team"],
        "descriptors": staged.stage_state["resource_descriptors"],
    }
    assert receipt["stages"]["topology"]["status"] == "passed"
    assert not {issue["code"] for issue in receipt["issues"]} & {
        "event_topology_source_metadata_missing",
        "event_topology_source_metadata_invalid",
        "event_topology_source_owner_mismatch",
        "event_topology_appendix_scope_invalid",
    }


def test_pending_appendix_has_provenance_but_cannot_become_modeled(staged):
    _prepare(staged)
    request = {"target_type": "skill", "target_id": 11,
               "status": "pending", "category": "talent",
               "version_line": "release"}
    with pytest.MonkeyPatch.context() as monkeypatch:
        monkeypatch.setattr(queries, "annotation_target_scope",
                            lambda *_args, **_kwargs: {
                                "target": {"target_type": "skill", "target_id": 11},
                                "character_ids": [1], "weapon_ids": [],
                                "artifact_set_ids": [],
                            })
        monkeypatch.setattr(queries, "list_annotations", lambda _db, **selected: {
            "total": 1, "page": selected["page"],
            "page_size": selected["page_size"], "items": [{
                "id": 7, "target_type": "skill", "target_id": 11,
                "version_line": "release", "category": "talent",
                "title": "候选天赋事实", "content": "等待审核。",
                "status": "pending",
            }],
        })
        appendix = _call(staged, "gs_query_appendix", **request)["data"]
    source_ref = appendix["source_ref"]

    modeled = _write_topology(staged, _topology(staged, [
        _source("talent", source_ref),
    ]))
    assert modeled["status"] == "draft"
    assert "event_topology_appendix_not_executable" in {
        issue["code"] for issue in modeled["issues"]}

    pending = _write_topology(staged, _topology(staged, [
        _source("talent", source_ref, disposition="pending"),
    ]))
    codes = {issue["code"] for issue in pending["issues"]}
    assert pending["status"] == "draft"
    assert "event_source_pending" in codes
    assert "event_topology_appendix_not_executable" not in codes


def test_approved_appendix_can_support_agent_declared_modeled_source(staged, monkeypatch):
    _prepare(staged)
    monkeypatch.setattr(queries, "annotation_target_scope", lambda *_args, **_kwargs: {
        "target": {"target_type": "skill", "target_id": 11},
        "character_ids": [1], "weapon_ids": [], "artifact_set_ids": [],
    })
    monkeypatch.setattr(queries, "list_annotations", lambda _db, **selected: {
        "total": 1, "page": selected["page"],
        "page_size": selected["page_size"], "items": [{
            "id": 8, "target_type": "skill", "target_id": 11,
            "version_line": "release", "category": "talent",
            "title": "已审核天赋事实", "content": "已核对。",
            "status": "approved",
        }],
    })
    appendix = _call(staged, "gs_query_appendix", target_type="skill",
                     target_id=11, status="approved", category="talent",
                     version_line="release")["data"]
    receipt = _write_topology(staged, _topology(staged, [
        _source("talent", appendix["source_ref"]),
    ]))
    codes = {issue["code"] for issue in receipt["issues"]}
    assert receipt["status"] == "numeric_ready"
    assert "event_topology_appendix_not_executable" not in codes
    assert "event_topology_appendix_authority_invalid" not in codes


def test_appendix_from_another_character_cannot_support_current_owner(staged, monkeypatch):
    _prepare(staged)
    monkeypatch.setattr(queries, "annotation_target_scope", lambda *_args, **_kwargs: {
        "target": {"target_type": "skill", "target_id": 22},
        "character_ids": [2], "weapon_ids": [], "artifact_set_ids": [],
    })
    monkeypatch.setattr(queries, "list_annotations", lambda _db, **selected: {
        "total": 1, "page": selected["page"], "page_size": selected["page_size"],
        "items": [{"id": 9, "target_type": "skill", "target_id": 22,
                   "version_line": "release", "category": "talent",
                   "title": "其他角色资料", "content": "不得跨角色绑定。",
                   "status": "approved"}],
    })
    appendix = _call(staged, "gs_query_appendix", target_type="skill", target_id=22,
                     status="approved", category="talent",
                     version_line="release")["data"]
    receipt = _write_topology(staged, _topology(staged, [
        _source("talent", appendix["source_ref"]),
    ]))
    assert "event_topology_source_owner_mismatch" in {
        issue["code"] for issue in receipt["issues"]}


def test_empty_approved_appendix_query_cannot_support_modeled_source(staged, monkeypatch):
    _prepare(staged)
    monkeypatch.setattr(queries, "annotation_target_scope", lambda *_args, **_kwargs: {
        "target": {"target_type": "skill", "target_id": 11},
        "character_ids": [1], "weapon_ids": [], "artifact_set_ids": [],
    })
    monkeypatch.setattr(queries, "list_annotations", lambda _db, **selected: {
        "total": 0, "page": selected["page"], "page_size": selected["page_size"],
        "items": [],
    })
    appendix = _call(staged, "gs_query_appendix", target_type="skill", target_id=11,
                     status="approved", category="talent",
                     version_line="release")["data"]
    receipt = _write_topology(staged, _topology(staged, [
        _source("talent", appendix["source_ref"]),
    ]))
    assert "event_topology_appendix_authority_invalid" in {
        issue["code"] for issue in receipt["issues"]}


def test_appendix_query_cannot_masquerade_as_database_source(staged):
    _prepare(staged)
    appendix = _context_query(staged, {
        "characters": [{"id": 1, "sections": ["talents", "annotations"]}],
    })
    staged.stage_state["resource_descriptors"][appendix["resource"]][
        "query_kind"] = "appendix"
    receipt = _write_topology(staged, _topology(staged, [
        _source("talent", appendix["source_ref"]),
    ]))
    assert receipt["status"] == "draft"
    assert "event_topology_source_kind_mismatch" in {
        issue["code"] for issue in receipt["issues"]}


def test_legacy_resource_without_descriptor_fails_closed(staged):
    _prepare(staged)
    character = _context_query(staged, {
        "characters": [{"id": 1, "sections": ["talents"]}],
    })
    staged.stage_state["resource_descriptors"].pop(character["resource"])
    receipt = _write_topology(staged, _topology(staged, [
        _source("talent", character["source_ref"]),
    ]))
    assert receipt["status"] == "draft"
    assert "event_topology_source_metadata_missing" in {
        issue["code"] for issue in receipt["issues"]}


def test_descriptor_must_match_query_resource_and_stage(staged):
    _prepare(staged)
    character = _context_query(staged, {
        "characters": [{"id": 1, "sections": ["talents"]}],
    })
    descriptor = staged.stage_state["resource_descriptors"][character["resource"]]
    descriptor["fingerprint"] = "0" * 64
    receipt = _write_topology(staged, _topology(staged, [
        _source("talent", character["source_ref"]),
    ]))
    assert receipt["status"] == "draft"
    assert "event_topology_source_metadata_invalid" in {
        issue["code"] for issue in receipt["issues"]}
