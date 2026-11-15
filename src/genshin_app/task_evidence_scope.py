"""Currency of optional task evidence across database and calculation changes."""
from __future__ import annotations

import sqlite3
from pathlib import Path

from genshin_app.equipment.files import file_sha, fingerprint, input_view


def database_snapshot(path):
    """Use the same live metadata as native sessions, without startup side effects."""
    from genshin_db import queries
    from genshin_db.equipment_queries import equipment_read_session

    path = Path(path)
    if not path.is_file():
        return {"state": "missing"}
    try:
        with equipment_read_session(path):
            return queries.get_meta(path)
    except (OSError, sqlite3.Error, ValueError, TimeoutError):
        return None


def evidence_scope(snapshot, *, database_path=None):
    from genshin_app.equipment.energy_policy import POLICY
    from genshin_app.equipment.schema import COMPOSER_VERSION
    from genshin_app.version import APP_RELEASE_ID, APP_VERSION
    from genshin_engine.elemental_schema import FREEZE_RULESET

    release = None
    if database_path is not None and snapshot is not None and snapshot.get("state") != "missing":
        from genshin_app.database_bootstrap import LIVE_MANIFEST_FILENAME
        manifest = Path(database_path).parent / LIVE_MANIFEST_FILENAME
        try:
            if manifest.is_file() and manifest.stat().st_size <= 4 * 1024 * 1024:
                release = file_sha(manifest)
        except OSError:
            snapshot = None
    return {"database_fingerprint": fingerprint({"metadata": snapshot, "release": release}) if snapshot is not None else None,
            "policy_fingerprint": fingerprint({"app_version": APP_VERSION, "app_release": APP_RELEASE_ID,
                "composer_version": COMPOSER_VERSION, "direct_energy_policy": POLICY, "freeze_ruleset": FREEZE_RULESET})}


def scope_verified(scope):
    return isinstance(scope, dict) and bool(scope.get("database_fingerprint") and scope.get("policy_fingerprint"))


def calculation_currency(calculated, plan, scope, *, trusted_result=False):
    """Separate known historical evidence from results lacking current proof."""
    if plan is None or not isinstance(calculated, dict) or not isinstance(calculated.get("plan"), dict):
        return "no_current_calculation"
    saved_input = fingerprint(input_view(calculated["plan"]))
    if calculated["plan"] is not plan and saved_input != fingerprint(input_view(plan)):
        return "no_current_calculation"
    receipt = calculated.get("result_receipt")
    result = calculated.get("result")
    saved = receipt.get("task_evidence_scope") if isinstance(receipt, dict) else None
    if (not scope_verified(saved) or not scope_verified(scope) or not isinstance(result, dict)
            or not receipt.get("result_fingerprint") or result.get("task_evidence_scope") != saved):
        return "calculation_scope_unverified"
    if saved.get("database_fingerprint") != scope["database_fingerprint"]:
        return "calculation_database_changed"
    if saved.get("policy_fingerprint") != scope["policy_fingerprint"]:
        return "calculation_policy_changed"
    if receipt.get("input_fingerprint") != saved_input:
        return "calculation_input_unverified"
    if not trusted_result:
        try:
            if receipt["result_fingerprint"] != fingerprint(result):
                return "calculation_result_mismatch"
        except (ValueError, TypeError, OverflowError, RecursionError):
            return "calculation_result_mismatch"
    return "current"
