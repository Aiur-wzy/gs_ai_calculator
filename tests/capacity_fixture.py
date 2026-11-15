"""Engineering fixtures only; never grant real gameplay or read a real database."""
import copy

from genshin_db.runtime_action_sources import _exact
from genshin_db.runtime_queries import _Nodes, _read
from genshin_runtime.action_capacity_model import (
    CONTEXT_CONTRACT,
    CONTRACT,
    SCOPE,
    evidence_for,
)
from genshin_runtime.mechanism_semantics import fingerprint, validate_record
from tests.test_action_resource_sources import CID, DID, SKILL, SOURCE


def context():
    return {"contract": CONTEXT_CONTRACT, "mode": "ordinary_open_world", "external_capacity_effects": "none", "window_changes": "none"}


def selection(constellation=1):
    return {"character_id": CID, "skill_depot_id": DID, "skill_id": SKILL, "constellation": constellation,
            "character_level": 90, "ascension": 6, "base_level": 10, "weapon": {"id": 11401, "level": 1, "refinement": 1},
            "artifacts": "all_empty", "team": "self_only", "variant": "tap"}


def model_record(db, delta, status="approved"):
    with _read(db, "release") as (conn, state):
        reader = _Nodes(conn, "release")
        rows = [(name, _exact(conn, reader, "release", table, {"id": ident})) for name, table, ident in (
            ("avatar", "Avatar", CID), ("depot", "AvatarSkillDepot", DID), ("skill", "AvatarSkill", SKILL), ("weapon", "Weapon", 11401))]
        paths = sorted({row["source_path"] for _, row in rows} | {SOURCE, "TextMap/TextMap_MediumCHS.json"})
        sources = []
        for i, path in enumerate(paths):
            origin, raw = reader.source(path)
            sources.append({"id": f"raw{i}", "source_path": path, "source_sha256": origin["source_sha256"], "canonical_sha256": fingerprint(raw)})
        nodes = [{"id": name, "definition_id": row["definition"]["definition_id"], "definition_kind": "excel_record",
                  **{k: row[k] for k in ("source_path", "source_sha256", "json_pointer", "node_sha256")}} for name, row in rows]
        _, texts = reader.source("TextMap/TextMap_MediumCHS.json")
        from genshin_runtime.action_capacity_model import text_references
        inventory = [{"node_id": name, "field_pointer": pointer, "text_id": text_id,
                      "status": "present" if str(text_id) in texts else "missing",
                      "text_sha256": fingerprint(texts[str(text_id)]) if str(text_id) in texts else None,
                      "source_path": "TextMap/TextMap_MediumCHS.json"}
                     for name, pointer, text_id in text_references({name: row["record"] for name, row in rows})]

    selected = selection()
    domain = {k: copy.deepcopy(selected[k]) for k in ("character_level", "ascension", "base_level", "weapon", "artifacts", "team", "variant")}
    domain.update(constellations=[0, 1], context=context())
    claim = {"contract": CONTRACT, "qualification": "reviewed_game_resource_model", "snapshot": state,
             "target": {"character_id": CID, "skill_depot_id": DID, "skill_id": SKILL, "avatar": "avatar", "depot": "depot", "skill": "skill", "depot_pointer": "/skills/1"},
             "domain": domain, "materials": {"sources": sources, "nodes": nodes, "talent_parent": SOURCE, "weapon": "weapon"},
             "text_inventory": inventory, "base": {"node_id": "skill", "pointer": "/maxChargeNum", "value": 1},
             "delta_refs": [{"id": delta["id"], "revision": delta["revision"]}],
             "reviewed_cases": [{"constellation": 0, "capacity": 1}, {"constellation": 1, "capacity": 2}],
             "prior_decision": {**{k: "a" * 64 for k in ("proposal_sha256", "selection_sha256", "decision_sha256", "report_sha256", "freeze_sha256", "snapshot_id")},
                                "variant_mapping": {"prior_contract": "finite-game-capacity-review-proposal-v1", "prior_value": "press", "canonical_value": "tap", "meaning": "点按"}},
             "scope": SCOPE}
    evidence = evidence_for(claim)
    record = {"id": "fixture.capacity.model", "revision": 1, "subject": {"target_type": "character", "target_id": CID},
              "payload": {"id": "fixture.capacity.model", "revision": 1, "kind": "action_capacity_model", "claim": claim,
                          "evidence_tier": "experimental_reference", "evidence_refs": [x["id"] for x in evidence]},
              "evidence": evidence, "applicability": copy.deepcopy(delta["applicability"]),
              "review": {"status": status, "reviewer": "synthetic-only" if status in ("approved", "revoked") else None,
                         "reviewed_at": "2026-10-08T00:00:00+00:00" if status in ("approved", "revoked") else None}}
    validate_record(record, "fact")
    return record
