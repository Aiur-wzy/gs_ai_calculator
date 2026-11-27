"""Admit the live database catalog and retain bounded, offline replay evidence."""

from __future__ import annotations

import json
import os
import sqlite3
from pathlib import Path

from genshin_app.paths import is_frozen
from genshin_db.equipment_queries import borrow_read_cache, borrow_read_connection
from genshin_db.release_integrity import meta_row_witness, verify_meta_row_witness
from genshin_db.release_profile import (
    load_database_release_manifest,
    load_release_pair_manifest,
    validate_database_release_manifest,
)
from genshin_engine.special_reaction_capabilities import (
    validate_reviewed_special_reaction_catalog,
)

from .files import fingerprint

META_KEY = "special_reaction_capability_catalog"
FIELD = "special_capability_source"
CONTRACT = "database-special-capability-source-v1"
MAX_CATALOG_BYTES = 256 * 1024
_INVALID = "无法验证星月资格目录的发行来源，请核对程序、数据库及保存结果的发行版本。"
_MISSING = object()


def catalog_summary(catalog):
    return {key: catalog[key] for key in (
        "collection_id", "revision", "source_upstream_commit", "source_target_version", "content_hash"
    )}


def _bounded_file(path):
    with Path(path).open("rb") as stream:
        raw = stream.read(4 * 1024 * 1024 + 1)
    if len(raw) > 4 * 1024 * 1024:
        raise ValueError(_INVALID)


def _catalog_from_proof(proof, *, live_pair=None):
    if (not isinstance(proof, dict)
            or set(proof) != {"contract", "manifest", "catalog_json", "meta_siblings", "signed_pair"}
            or proof["contract"] != CONTRACT):
        raise ValueError(_INVALID)
    manifest = validate_database_release_manifest(proof["manifest"])
    database = manifest["database"]
    table = (database.get("release_core") or {}).get("tables", {}).get("meta", {})
    raw = proof["catalog_json"]
    if (not isinstance(raw, str) or len(raw.encode("utf-8")) > MAX_CATALOG_BYTES
            or not verify_meta_row_witness(META_KEY, raw, proof["meta_siblings"], table)):
        raise ValueError(_INVALID)
    catalog = validate_reviewed_special_reaction_catalog(
        json.loads(raw), upstream_commit=database["upstream_commit"])
    version = database["game_version"]
    if catalog["source_target_version"] not in (version, database["upstream_track"] + version):
        raise ValueError(_INVALID)
    line = database["datasets"]["equipment"]["lines"].get(catalog["version_line"], {})
    if line.get("state") != "ready" or line.get("upstream_commit") != catalog["source_upstream_commit"]:
        raise ValueError(_INVALID)
    signed = proof["signed_pair"]
    if signed is None and is_frozen():
        from genshin_app.database_bootstrap import (
            DatabaseStartupError,
            matches_verified_bundled_catalog_source,
        )

        if not matches_verified_bundled_catalog_source(manifest, live_pair=live_pair):
            raise DatabaseStartupError("database_capability_source_untrusted", _INVALID)
    elif signed is not None:
        from genshin_app.product_release_index import validate_live_pair_proof
        from genshin_app.release_pair_trust import build_trust_verifier
        from genshin_app.version import DATABASE_RELEASE_TRUST_KEYS

        if not isinstance(signed, dict) or set(signed) != {"manifest", "proof"}:
            raise ValueError(_INVALID)
        validate_live_pair_proof(signed["proof"], signed["manifest"], manifest,
                                 trust_verifier=build_trust_verifier(DATABASE_RELEASE_TRUST_KEYS))
    return catalog


def load_database_capability_catalog(db_path):
    """Return one admitted snapshot from the caller's locked database read."""
    if not Path(db_path).is_file():
        return None, None
    connection = borrow_read_connection(db_path)
    cache = borrow_read_cache(db_path, META_KEY)
    if connection is None or cache is None:
        raise RuntimeError("数据库星月资格读取需要设备数据只读事务。")
    cached = cache.get("authority", _MISSING)
    if cached is not _MISSING:
        return cached
    try:
        if connection.execute("SELECT 1 FROM sqlite_schema WHERE type='table' AND name='meta'").fetchone() is None:
            return None, None
        raw, siblings = meta_row_witness(connection, META_KEY)
        if raw is None:
            cache["authority"] = (None, None)
            return cache["authority"]

        from genshin_app.database_bootstrap import (
            LIVE_MANIFEST_FILENAME,
            LIVE_PAIR_MANIFEST_FILENAME,
        )
        from genshin_app.release_pair_update import (
            LIVE_PAIR_PROOF_FILENAME,
            read_live_product_proof,
        )

        directory = Path(db_path).resolve().parent
        manifest_path = directory / LIVE_MANIFEST_FILENAME
        _bounded_file(manifest_path)
        manifest = load_database_release_manifest(manifest_path)
        signed = None
        live_pair = None
        has_product_proof = os.path.lexists(directory / LIVE_PAIR_PROOF_FILENAME)
        if has_product_proof or is_frozen():
            pair_path = directory / LIVE_PAIR_MANIFEST_FILENAME
            _bounded_file(pair_path)
            live_pair = load_release_pair_manifest(pair_path)
        if has_product_proof:
            signed = {"manifest": live_pair, "proof": read_live_product_proof(directory, live_pair, manifest)}
        proof = {"contract": CONTRACT, "manifest": manifest, "catalog_json": raw,
                 "meta_siblings": siblings, "signed_pair": signed}
        catalog = _catalog_from_proof(proof, live_pair=live_pair)
    except (OSError, sqlite3.Error, TypeError, ValueError, AttributeError, KeyError, RuntimeError) as error:
        from .schema import PlanError

        raise PlanError(_INVALID, "database_special_capability_catalog_invalid",
                        {"metadata_key": META_KEY, "source_error": getattr(error, "reason", type(error).__name__)}) from error
    cache["authority"] = (catalog, proof)
    return catalog, proof


def bind_saved_capability_catalog(result, receipt, session, *, result_fingerprint=None, report=None):
    """Restrict saved catalog authority to replay of already-bound results."""
    if FIELD not in result and FIELD not in receipt:
        if session.special_capability_proof_fingerprint is not None:
            session.bind_replay_capability_catalog(None, proof_fingerprint=None)
        if report is not None and "special_capability_catalog" in report:
            raise ValueError("保存报告缺少星月资格来源证明。请使用原数据库重新计算。")
        return
    try:
        proof = result[FIELD]
        if (receipt[FIELD] != fingerprint(proof)
                or receipt.get("result_fingerprint") != (result_fingerprint or fingerprint(result))):
            raise ValueError(_INVALID)
        key = receipt[FIELD]
        if session.special_capability_proof_fingerprint != key:
            catalog = _catalog_from_proof(proof)
            session.bind_replay_capability_catalog(catalog, proof_fingerprint=key)
        if report is not None and report.get("special_capability_catalog") != catalog_summary(
                json.loads(proof["catalog_json"])):
            raise ValueError(_INVALID)
    except (TypeError, ValueError, AttributeError, KeyError, OverflowError, RuntimeError) as error:
        raise ValueError("无法验证保存结果的星月资格来源。请用对应发行版本的程序和数据库重新核对。") from error
