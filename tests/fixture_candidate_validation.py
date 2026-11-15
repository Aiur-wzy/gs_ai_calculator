"""Read-only synthetic fixture schema checks, not an update/install path."""
from __future__ import annotations

import sqlite3
from contextlib import closing
from pathlib import Path

from genshin_db import raw_store
from genshin_db.display_names import _check_version as check_display_version
from genshin_db.display_names import display_complete
from genshin_db.equipment import equipment_datasets, validate_equipment
from genshin_db.pipeline import PipelineError, _evidence_json
from genshin_db.runtime_store import runtime_complete, runtime_datasets
from genshin_db.semantic_store import validate_component as validate_semantic_component


def _preflight(path):
    if not path.exists():
        return
    with closing(sqlite3.connect(path)) as conn, conn:
        if conn.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise PipelineError("在用库完整性检查失败")
        check_display_version(conn)
        validate_semantic_component(conn)
        for line in equipment_datasets(conn)["equipment"]["lines"].values():
            if line["state"] == "incompatible":
                raise PipelineError("装备结构版本不受支持，拒绝降级写入")
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        for line in runtime_datasets(conn)["runtime_config"]["lines"].values():
            if line["state"] == "incompatible":
                raise PipelineError("运行配置结构版本不受支持，拒绝降级写入")
        if "data_component" in tables and conn.execute("SELECT 1 FROM data_component WHERE component NOT IN ('equipment','runtime_config','mechanic_semantics')").fetchone():
            raise PipelineError("存在未识别数据组件，拒绝重建降级")


def _complete(path, commit, manifest, runtime_manifest, version_evidence=None):
    with closing(sqlite3.connect(path)) as conn, conn:
        conn.row_factory = sqlite3.Row
        # Missing semantics on a legacy DB is allowed; no implicit seed approval.
        validate_semantic_component(conn)
        if not display_complete(conn, "release"):
            return False
        state = equipment_datasets(conn)["equipment"]["lines"]["release"]
        if state["state"] != "ready" or state["upstream_commit"] != commit:
            return False
        validate_equipment(conn, "release")
        existing = {r[0]: {"sha256": r[1], "row_count": r[2]} for r in conn.execute(
            "SELECT source_file,sha256,row_count FROM source_manifest WHERE component='equipment' AND version_line='release'")}
        if not conn.execute("SELECT 1 FROM sqlite_master WHERE name='raw_table'").fetchone():
            return False
        if {r[0] for r in conn.execute("SELECT source_file FROM raw_table")} != {Path(rel).stem for rel in raw_store.RAW_SOURCES}:
            return False
        if existing != manifest or not runtime_complete(
            conn,
            version_line="release",
            upstream_commit=commit,
            manifest=runtime_manifest,
        ):
            return False
        if version_evidence is not None:
            runtime_state = runtime_datasets(conn)["runtime_config"]["lines"]["release"]
            normalization = runtime_state.get("normalization")
            normalization_version = (
                str(normalization.get("normalization_version"))
                if isinstance(normalization, dict)
                else "unavailable"
            )
            expected_identity = {
                "game_version": version_evidence.get("version") or "unknown",
                "upstream_commit": commit,
                "upstream_track": version_evidence.get("track") or "unknown",
                "upstream_updated_at": (
                    version_evidence.get("snapshot_updated_at") or "unknown"
                ),
                "upstream_version_evidence": _evidence_json(version_evidence),
                "indexer_version": str(runtime_state.get("indexer_version")),
                "normalization_version": normalization_version,
            }
            meta = dict(conn.execute("SELECT key,value FROM meta"))
            for key, expected in expected_identity.items():
                if meta.get(key) != expected or meta.get(f"{key}:release") != expected:
                    return False
        return True
