"""raw 层入库：扫描上游白名单 JSON 文件，原样写入 SQLite raw 层。"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

RAW_SOURCES: list[str] = [
    "ExcelBinOutput/AvatarExcelConfigData.json",
    "ExcelBinOutput/AvatarSkillExcelConfigData.json",
    "ExcelBinOutput/AvatarTalentExcelConfigData.json",
    "ExcelBinOutput/AvatarSkillDepotExcelConfigData.json",
    "ExcelBinOutput/ProudSkillExcelConfigData.json",
    "ExcelBinOutput/FetterInfoExcelConfigData.json",
    "ExcelBinOutput/AvatarCurveExcelConfigData.json",
    "ExcelBinOutput/AvatarPromoteExcelConfigData.json",
    "TextMap/TextMap_MediumCHS.json",  # 配置表哈希对应 Medium 版（2026-09-02 T3 验证）
    *[f"ExcelBinOutput/{name}ExcelConfigData.json" for name in (
        "Weapon", "WeaponCurve", "WeaponPromote", "EquipAffix", "WeaponCodex",
        "Reliquary", "ReliquarySet", "ReliquaryCodex", "ReliquaryMainProp",
        "ReliquaryAffix", "ReliquaryLevel",
    )],
]

CREATE_RAW_TABLE = """
CREATE TABLE raw_table (
    source_file TEXT PRIMARY KEY,
    data_json TEXT NOT NULL,
    imported_at TEXT NOT NULL
)
"""


class RawStoreError(Exception):
    """白名单文件缺失或 JSON 解析失败时抛出。"""


@dataclass
class RawStats:
    files: int
    rows: int
    table_counts: dict[str, int]
    manifest: dict = field(default_factory=dict)
    reused_files: int = 0
    rebuilt_files: int = 0
    deleted_sources: tuple[str, ...] = ()


def build_raw_layer(
    repo_dir: Path,
    db_path: Path,
    *,
    content_timestamp: str | None = None,
    reuse_from: Path | None = None,
    base_manifest: dict | None = None,
) -> RawStats:
    """Build the complete raw layer, optionally reusing unchanged payloads.

    Reuse is content-addressed: a row is reused only when the reviewed base
    manifest and the target bytes have the same SHA-256 and the base database
    contains the expected source row.  The target table is still complete and
    is written only to ``db_path``; ``reuse_from`` is opened read-only.
    """

    if (reuse_from is None) != (base_manifest is None):
        raise ValueError("reuse_from与base_manifest必须同时提供")
    base_payloads: dict[str, str] = {}
    if reuse_from is not None and Path(reuse_from).is_file():
        source = sqlite3.connect(Path(reuse_from).resolve().as_uri() + "?mode=ro", uri=True)
        try:
            if source.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='raw_table'"
            ).fetchone():
                base_payloads = dict(
                    source.execute("SELECT source_file,data_json FROM raw_table")
                )
        finally:
            source.close()

    payloads: list[tuple[str, str, int]] = []
    manifest = {}
    reused = 0
    rebuilt = 0
    for rel in RAW_SOURCES:
        path = repo_dir / rel
        if not path.exists():
            raise RawStoreError(f"白名单文件缺失: {rel}")
        try:
            original = path.read_bytes()
            digest = hashlib.sha256(original).hexdigest()
            stem = path.stem
            base_spec = (base_manifest or {}).get(rel)
            if (
                base_spec is not None
                and base_spec.get("sha256") == digest
                and stem in base_payloads
            ):
                payload = base_payloads[stem]
                # The row count is part of the reviewed source identity.  A
                # malformed base manifest must not let an arbitrary payload
                # bypass JSON validation.
                data = json.loads(payload)
                if len(data) != base_spec.get("row_count"):
                    raise RawStoreError(f"复用来源行数与base manifest不符: {rel}")
                reused += 1
            else:
                data = json.loads(original.decode("utf-8-sig"))
                payload = json.dumps(data, ensure_ascii=False)
                rebuilt += 1
        except (json.JSONDecodeError, UnicodeDecodeError) as e:
            raise RawStoreError(f"JSON 解析失败: {rel}: {e}") from e
        payloads.append((path.stem, payload, len(data)))
        manifest[rel] = {"sha256": digest, "row_count": len(data)}

    db_path.parent.mkdir(parents=True, exist_ok=True)
    imported_at = content_timestamp or datetime.now(UTC).isoformat()
    conn = sqlite3.connect(db_path)
    try:
        with conn:
            conn.execute("DROP TABLE IF EXISTS raw_table")
            conn.execute(CREATE_RAW_TABLE)
            conn.executemany(
                "INSERT INTO raw_table (source_file, data_json, imported_at) VALUES (?, ?, ?)",
                [(name, payload, imported_at) for name, payload, _ in payloads],
            )
    finally:
        conn.close()

    table_counts = {name: count for name, _, count in payloads}
    deleted = tuple(sorted(set(base_manifest or {}) - set(manifest)))
    return RawStats(files=len(payloads), rows=sum(table_counts.values()),
                    table_counts=table_counts, manifest=manifest,
                    reused_files=reused, rebuilt_files=rebuilt,
                    deleted_sources=deleted)


if __name__ == "__main__":
    stats = build_raw_layer(Path("data/upstream/repo"), Path("data/build/raw-check.db"))
    print(f"files: {stats.files}")
    print(f"rows: {stats.rows}")
    for name, count in stats.table_counts.items():
        print(f"  {name}: {count}")
