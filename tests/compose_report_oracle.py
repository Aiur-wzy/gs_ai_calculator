"""Preserve whole historical envelopes across the approved metadata boundary."""
from __future__ import annotations

import copy
import hashlib
import importlib
import json

from genshin_app.equipment import composer
from genshin_app.equipment.files import fingerprint
from genshin_app.equipment.schema import COMPOSER_VERSION


def _independent_report_hash(value):
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True,
                         allow_nan=False, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _changed_report_paths(left, right, path=""):
    assert type(left) is type(right), path
    if isinstance(left, dict):
        changed = {path + "/" + key for key in left.keys() ^ right.keys()}
        return changed.union(*(_changed_report_paths(left[key], right[key], path + "/" + key)
                               for key in left.keys() & right.keys()))
    if isinstance(left, list):
        assert len(left) == len(right), path
        return set().union(*(_changed_report_paths(a, b, path + "/" + str(i))
                             for i, (a, b) in enumerate(zip(left, right))))
    return {path} if left != right else set()


_VERSION_BOUNDARY_PATHS = {
    "/automatic_baseline_fingerprint",
    "/calculation_override_basis/automatic_baseline_fingerprint",
    "/calculation_override_basis/engine_contract_fingerprint",
    "/composer_version",
    "/plan/resolution/automatic_baseline_fingerprint",
    "/plan/resolution/calculation_override_basis/automatic_baseline_fingerprint",
    "/plan/resolution/calculation_override_basis/engine_contract_fingerprint",
    "/plan/resolution/composer_version",
    "/plan/resolution/task_capability/derivation/effective_graph_fingerprint",
    "/task_capability/derivation/effective_graph_fingerprint",
}

# R13 made the new-plan policy explicit. These are its exact propagated identity
# fields, not permission to erase metadata, numerical results or source order.
_DIRECT_ENERGY_POLICY_BOUNDARY_PATHS = {
    "/plan/direct_energy_policy",
    "/input_fingerprint",
    "/context_fingerprint",
    "/equipment_summary/context/input_fingerprint",
    "/calculation_override_basis/plan_input_fingerprint",
    "/plan/resolution/input_fingerprint",
    "/plan/resolution/context_fingerprint",
    "/plan/resolution/calculation_override_basis/plan_input_fingerprint",
}


def assert_pre_extraction_envelope(report, plan, database, expected, monkeypatch):
    """Preserve the independently replayed 4cb10a8/1.31 numerical envelope.

    Replaying accepted 4cb10a8 proves all three original hashes. A second
    current execution with only the documented factory default omitted and
    version emitters at 1.31 reproduces those same hashes, byte for byte.
    The actual current result is still independently recomposed unchanged;
    only the listed identity fields may differ from the historical envelope.
    R17's two advisory projections are checked separately, then removed by
    exact name; no historical fingerprint, numeric field or other output is
    rewritten to accommodate the additive feedback.
    """
    assert COMPOSER_VERSION == "1.33.0"
    assert report["composer_version"] == "1.33.0"
    assert report["plan"]["resolution"]["composer_version"] == "1.33.0"
    assert plan["direct_energy_policy"] == "energy-direct-base-er-v1"
    assert report["plan"]["direct_energy_policy"] == "energy-direct-base-er-v1"
    assert report == composer.compose_equipment_plan(plan, database)
    def historical_projection(value):
        projected = copy.deepcopy(value)
        assistance = projected.pop("default_assistance")
        panels = projected.pop("panel_assistance")
        assert assistance["authority"] == "advisory_only"
        assert assistance["acceptance_effect"] == "none"
        assert assistance["candidate_effect"] == "none"
        assert panels == assistance["panels"]
        return projected
    historical_plan = copy.deepcopy(plan)
    historical_plan.pop("direct_energy_policy")
    # Never replace the historical digest with a newly captured output.
    with monkeypatch.context() as metadata_only:
        for module in ("compose_admission", "compose_effective", "compose_post_materialization"):
            metadata_only.setattr(importlib.import_module("genshin_app.equipment." + module),
                                  "COMPOSER_VERSION", "1.31.0")
        legacy_metadata_report = composer.compose_equipment_plan(historical_plan, database)
    legacy_projection = historical_projection(legacy_metadata_report)
    assert fingerprint(legacy_projection) == expected
    assert _independent_report_hash(legacy_projection) == expected
    allowed = _VERSION_BOUNDARY_PATHS | _DIRECT_ENERGY_POLICY_BOUNDARY_PATHS
    if report["state"] == "ready":
        assert report["plan"]["resolved_template"]["meta"]["equipment_resolution"]["composer_version"] == "1.33.0"
        allowed.update({"/equipment_summary/context/output_fingerprint", "/output_fingerprint",
                        "/plan/resolution/output_fingerprint",
                        "/plan/resolved_template/meta/equipment_resolution/composer_version"})
    assert _changed_report_paths(historical_projection(report), legacy_projection) == allowed
