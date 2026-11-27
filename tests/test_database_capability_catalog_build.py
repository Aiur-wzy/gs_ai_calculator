from contextlib import closing
import json
import sqlite3

import pytest

from genshin_db.capability_catalog_build import (
    CATALOG_META_KEY,
    CATALOG_REVIEW_META_KEY,
    _SOURCES,
    _fingerprint,
    publish_capability_catalog,
)

BASE = "1" * 40
TARGET = "2" * 40
SOURCE_FILES = {name: {"sha256": "a" * 64} for name in _SOURCES}


def _database(path, commit, *, desc="队员的普通反应转化为星扩散",
              desc_hash=7001, present=True, discovered=False):
    conn = sqlite3.connect(path)
    conn.executescript("""
        CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT);
        CREATE TABLE raw_table (source_file TEXT PRIMARY KEY, data_json TEXT);
    """)
    conn.execute("INSERT INTO meta VALUES ('upstream_commit',?)", (commit,))
    characters = [{"id": 10, "skillDepotId": 20}]
    depots = [{"id": 20, "inherentProudSkillOpens": [{"proudSkillGroupId": 30}]}] if present else []
    skills = [{"proudSkillId": 31, "proudSkillGroupId": 30, "descTextMapHash": desc_hash,
               "openConfig": "StableUnlock", "proudSkillType": 2}]
    text = {str(desc_hash): desc}
    if discovered:
        characters.append({"id": 11, "skillDepotId": 21})
        depots.append({"id": 21, "inherentProudSkillOpens": [{"proudSkillGroupId": 32}]})
        skills.append({"proudSkillId": 33, "proudSkillGroupId": 32, "descTextMapHash": 7003})
        text["7003"] = "队员的反应转化为<color=#112233>星扩散</color>"
    for file, rows in zip(_SOURCES, (characters, depots, skills, text), strict=True):
        conn.execute("INSERT INTO raw_table VALUES (?,?)",
                     (file.rsplit("/", 1)[1].removesuffix(".json"), json.dumps(rows)))
    conn.commit()
    return conn


def _seed(conn):
    entry = {"character_id": 10, "required_skill_depot_id": 20,
             "display_name": "工程样例", "form_selection_required": False,
             "runtime_profile_id": "native.special-reactions.v1", "runtime_support_status": "supported",
             "capability_tags": ["native_reaction_qualification.stellar_swirl"],
             "evidence": {"proud_skill_id": 31, "proud_skill_group_id": 30, "desc_text_map_hash": "7001"}}
    entry["entry_fingerprint"] = _fingerprint(entry)
    catalog = {"schema_version": 1, "collection_id": "native.special-reaction-capabilities.v1",
               "revision": 8, "status": "reviewed", "version_line": "release", "source_upstream_commit": BASE,
               "source_target_version": "OSCBWin7.4.50",
               "source_files": {p: row["sha256"] for p, row in SOURCE_FILES.items()}, "entries": [entry]}
    catalog["content_hash"] = _fingerprint(catalog)
    with conn:
        conn.execute("INSERT INTO meta VALUES (?,?)", (CATALOG_META_KEY, json.dumps(catalog)))


@pytest.mark.parametrize("description,present,expected,status", [
    ("队员的普通反应转化为{LINK#N12}星扩散{/LINK}", True, "unchanged_qualification_evidence", "supported"),
    ("队员的普通反应转化为星超导", True, "target_qualification_evidence_changed", "pending"),
    ("队员的普通反应转化为星扩散", False, "target_binding_missing", "pending"),
])
def test_capability_catalog_comes_from_matching_source_evidence(tmp_path, description, present, expected, status):
    base = tmp_path / "base.db"
    with closing(_database(base, BASE)) as conn:
        _seed(conn)
    with closing(_database(tmp_path / "target.db", TARGET, desc=description,
                           desc_hash=7002, present=present, discovered=True)) as conn:
        result = publish_capability_catalog(conn, base, "OSCBWin7.4.51", SOURCE_FILES)
        entries = dict(conn.execute("SELECT key,value FROM meta"))
        review = json.loads(entries[CATALOG_REVIEW_META_KEY])
        catalog = json.loads(entries[CATALOG_META_KEY])
        assert result[status] == 1 and result["discoveries"] == 1
        assert review["rows"][0]["reason"] == expected
        assert review["discoveries"][0]["character_id"] == 11
        assert review["discoveries"][0]["status"] == "pending"
        assert catalog["revision"] == 9 and catalog["source_upstream_commit"] == TARGET
        assert catalog["content_hash"] == _fingerprint({k: v for k, v in catalog.items() if k != "content_hash"})
        assert len(catalog["entries"]) == 1
        row = catalog["entries"][0]
        assert row["runtime_support_status"] == status
        assert row["evidence"]["desc_text_map_hash"] == ("7002" if present else "7001")
