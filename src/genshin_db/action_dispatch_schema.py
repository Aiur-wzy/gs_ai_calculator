"""Versioned reviewed action-dispatch profile contract.

The contract contains game facts and reviewed calibration, never per-rotation
calculation answers.  A profile is selected by an exact semantic action
identity.  Missing, stale, ambiguous, or partly reviewed rows are deliberately
not executable.
"""
from __future__ import annotations

import json
import math
import re
from decimal import Decimal, InvalidOperation

CONTRACT_ID = "action-dispatch-profile-v1"
SCHEMA_VERSION = 1
PROFILE_VERSION = 1
MAX_BUNDLE_BYTES = 2 * 1024 * 1024
MAX_PROFILES = 256
MAX_EMISSIONS = 64
MAX_SOURCE_REFS = 64
MAX_SECONDS = 3600

SLOTS = frozenset(("normal", "skill", "burst"))
RULESETS = frozenset(("gauge-swirl-dynamic-v3",))
PROFILE_STATUSES = frozenset(("confirmed", "pending"))
FIELD_STATUSES = frozenset(("confirmed", "pending"))
EVIDENCE_CATEGORIES = frozenset(
    ("database_proven", "calibration_required", "scenario_required", "unsupported")
)
APPLICATION_MODES = frozenset(("sequence", "no_icd", "no_application", "pending"))
SOURCE_KINDS = frozenset(("attack_metadata", "periodic_attack"))
EMISSION_KINDS = frozenset(("fixed", "periodic"))
SAMPLING_MODES = frozenset(("current", "action_start", "action_capture"))
LANDED_POLICIES = frozenset(("default", "scenario_required"))
DERIVED_COUNT_POLICY = "through_instance_end_inclusive"
REPLACEMENT_POLICY = "replace_previous"
APPLICATION_CADENCE_KIND = "every_n_landed_hits"
SOURCE_REF_KINDS = frozenset(
    ("database_pointer", "database_candidate", "calibration_record", "community_reference")
)

_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")
_HASH = re.compile(r"^[0-9a-f]{64}$")
_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


class ActionDispatchProfileError(ValueError):
    """A reviewed dispatch bundle violates its closed, bounded contract."""


def _error(path: str, message: str) -> None:
    raise ActionDispatchProfileError(f"{path}: {message}")


def _object(value, path: str, keys: set[str]) -> dict:
    if not isinstance(value, dict):
        _error(path, "must be an object")
    missing, extra = keys - value.keys(), value.keys() - keys
    if missing:
        _error(path, "missing fields: " + ", ".join(sorted(missing)))
    if extra:
        _error(path, "unknown fields: " + ", ".join(sorted(extra)))
    return value


def _object_optional(value, path: str, required: set[str], optional: set[str]) -> dict:
    if not isinstance(value, dict):
        _error(path, "must be an object")
    missing, extra = required - value.keys(), value.keys() - required - optional
    if missing:
        _error(path, "missing fields: " + ", ".join(sorted(missing)))
    if extra:
        _error(path, "unknown fields: " + ", ".join(sorted(extra)))
    return value


def _id(value, path: str) -> str:
    if not isinstance(value, str) or not _ID.fullmatch(value):
        _error(path, "must be a bounded stable identifier")
    return value


def _hash(value, path: str, *, nullable: bool = False):
    if value is None and nullable:
        return None
    if not isinstance(value, str) or not _HASH.fullmatch(value):
        _error(path, "must be a lowercase sha256 digest")
    return value


def _integer(value, path: str, low: int, high: int) -> int:
    if type(value) is not int or not low <= value <= high:
        _error(path, f"must be an integer in [{low}, {high}]")
    return value


def _time(value, path: str, *, nullable: bool = False, zero: bool = False):
    if value is None and nullable:
        return None
    if type(value) not in (int, float) or not math.isfinite(value):
        _error(path, "must be a finite number")
    if value < 0 if zero else value <= 0:
        _error(path, "must be non-negative" if zero else "must be positive")
    if value > MAX_SECONDS:
        _error(path, "exceeds the dispatch time limit")
    try:
        micros = Decimal(str(value)) * 1_000_000
    except InvalidOperation as exc:
        raise ActionDispatchProfileError(f"{path}: invalid decimal time") from exc
    if micros != micros.to_integral_value():
        _error(path, "must be exactly representable in microseconds")
    return value


def validate_selector(value, path: str = "selector") -> dict:
    keys = {"version_line", "character_id", "skill_depot_id", "skill_id", "slot",
            "effective_level", "constellation", "variant", "cast_stage"}
    row = _object(value, path, keys)
    if row["version_line"] not in ("release", "beta"):
        _error(path + ".version_line", "must be release or beta")
    _integer(row["character_id"], path + ".character_id", 1, 2**53 - 1)
    _integer(row["skill_depot_id"], path + ".skill_depot_id", 1, 2**53 - 1)
    _integer(row["skill_id"], path + ".skill_id", 1, 2**53 - 1)
    if row["slot"] not in SLOTS:
        _error(path + ".slot", "must be normal, skill, or burst")
    _integer(row["effective_level"], path + ".effective_level", 1, 15)
    _integer(row["constellation"], path + ".constellation", 0, 6)
    _id(row["variant"], path + ".variant")
    if row["cast_stage"] is not None:
        _integer(row["cast_stage"], path + ".cast_stage", 1, 64)
    return row


def _validate_source_ref(value, path: str) -> None:
    row = _object(value, path, {"kind", "reference", "fingerprint"})
    if row["kind"] not in SOURCE_REF_KINDS:
        _error(path + ".kind", "unsupported source reference kind")
    if not isinstance(row["reference"], str) or not row["reference"].strip() or len(row["reference"]) > 2048:
        _error(path + ".reference", "must be non-empty and at most 2048 characters")
    _hash(row["fingerprint"], path + ".fingerprint")


def _validate_field_evidence(value, path: str) -> None:
    row = _object(value, path, {"status", "category", "source_refs"})
    if row["status"] not in FIELD_STATUSES:
        _error(path + ".status", "must be confirmed or pending")
    if row["category"] not in EVIDENCE_CATEGORIES:
        _error(path + ".category", "unsupported evidence category")
    refs = row["source_refs"]
    if not isinstance(refs, list) or len(refs) > MAX_SOURCE_REFS:
        _error(path + ".source_refs", "must be a bounded list")
    for index, ref in enumerate(refs):
        _validate_source_ref(ref, f"{path}.source_refs[{index}]")
    if row["status"] == "confirmed" and row["category"] == "unsupported":
        _error(path, "unsupported evidence cannot be confirmed")
    if row["status"] == "confirmed" and row["category"] == "scenario_required":
        _error(path, "scenario-required evidence cannot be confirmed in a stored profile")
    if row["status"] == "confirmed" and not refs:
        _error(path, "confirmed evidence needs at least one source reference")


def _validate_attack_source(value, path: str) -> None:
    row = _object(value, path, {"kind", "candidate_id", "origin_reference_id", "source_fingerprint"})
    if row["kind"] not in SOURCE_KINDS:
        _error(path + ".kind", "unsupported source query kind")
    if row["candidate_id"] is not None:
        _id(row["candidate_id"], path + ".candidate_id")
    _hash(row["origin_reference_id"], path + ".origin_reference_id", nullable=True)
    _hash(row["source_fingerprint"], path + ".source_fingerprint", nullable=True)


def _validate_native_verification(value, path: str) -> None:
    row = _object(value, path, {"status", "locator", "version", "reviewed_at", "component_scope",
                                "confirmed_fields"})
    if row["status"] != "confirmed":
        _error(path + ".status", "must be confirmed")
    for key in ("locator", "version", "component_scope"):
        if not isinstance(row[key], str) or not row[key].strip() or len(row[key]) > 2048:
            _error(path + "." + key, "must be non-empty and at most 2048 characters")
    if not isinstance(row["reviewed_at"], str) or not _DATE.fullmatch(row["reviewed_at"]):
        _error(path + ".reviewed_at", "must use YYYY-MM-DD")
    fields = row["confirmed_fields"]
    allowed = {"application_mode", "counter_tag", "shared_scope"}
    if (not isinstance(fields, list) or not 2 <= len(fields) <= 3 or len(fields) != len(set(fields))
            or not set(fields) <= allowed):
        _error(path + ".confirmed_fields", "must contain 2..3 unique native verification fields")


def _validate_application(value, path: str) -> None:
    row = _object_optional(value, path, {"mode", "counter_tag", "verification"}, {"cadence"})
    if row["mode"] not in APPLICATION_MODES:
        _error(path + ".mode", "unsupported application mode")
    if row["counter_tag"] is not None:
        _id(row["counter_tag"], path + ".counter_tag")
    if row["mode"] == "sequence" and row["counter_tag"] is None:
        _error(path, "sequence application requires a counter_tag")
    if row["mode"] != "sequence" and row["counter_tag"] is not None:
        _error(path, "only sequence application may have a counter_tag")
    if row["verification"] is not None:
        _validate_native_verification(row["verification"], path + ".verification")
        required = {"application_mode", "shared_scope"}
        if row["mode"] == "sequence":
            required.add("counter_tag")
        if not required <= set(row["verification"]["confirmed_fields"]):
            _error(path + ".verification", "does not confirm all fields required by the application mode")
    if row["mode"] in ("no_icd", "no_application") and row["verification"] is None:
        _error(path, "no_icd/no_application requires structured positive verification")
    cadence = row.get("cadence")
    if cadence is not None:
        cadence = _object(cadence, path + ".cadence",
            {"kind", "period_hits", "first_attempt_ordinal", "scope"})
        if cadence["kind"] != APPLICATION_CADENCE_KIND or cadence["scope"] != "channel_generation":
            _error(path + ".cadence", "unsupported application cadence")
        period = _integer(cadence["period_hits"], path + ".cadence.period_hits", 1, 256)
        first = _integer(cadence["first_attempt_ordinal"], path + ".cadence.first_attempt_ordinal", 1, 256)
        if first > period:
            _error(path + ".cadence", "first attempt must be within one cadence period")
        if row["mode"] not in ("sequence", "no_icd"):
            _error(path + ".cadence", "cadence requires an application-capable mode")


def _validate_prototype(value, path: str) -> None:
    row = _object(value, path, {"damage_type_ids", "damage_system", "scaling_stat", "count",
                                "skill_label", "hit_name", "description"})
    types = row["damage_type_ids"]
    if not isinstance(types, list) or not 1 <= len(types) <= 8 or len(types) != len(set(types)):
        _error(path + ".damage_type_ids", "must contain 1..8 unique damage type identifiers")
    for index, damage_type in enumerate(types):
        _id(damage_type, f"{path}.damage_type_ids[{index}]")
    if row["damage_system"] != "classic" or row["scaling_stat"] != "atk" or row["count"] != 1:
        _error(path, "v1 supports only one classic ATK-scaled hit per component")
    for key, limit in (("skill_label", 128), ("hit_name", 256), ("description", 2048)):
        if not isinstance(row[key], str) or (key == "skill_label" and not row[key].strip()) or len(row[key]) > limit:
            _error(path + "." + key, f"must be a bounded string (max {limit})")


def _validate_schedule(value, kind: str, path: str) -> None:
    if kind == "fixed":
        row = _object(value, path, {"offset_seconds", "precision_seconds"})
        _time(row["offset_seconds"], path + ".offset_seconds", zero=True)
    else:
        row = _object_optional(value, path,
            {"first_offset_seconds", "interval_seconds", "count", "precision_seconds"}, {"derive_count"})
        _time(row["first_offset_seconds"], path + ".first_offset_seconds", zero=True)
        _time(row["interval_seconds"], path + ".interval_seconds")
        if row["count"] is None:
            if row.get("derive_count") != DERIVED_COUNT_POLICY:
                _error(path + ".derive_count", "count=null requires explicit inclusive instance-end derivation")
        else:
            _integer(row["count"], path + ".count", 1, 256)
            if "derive_count" in row:
                _error(path + ".derive_count", "must be omitted when count is explicit")
    precision = row["precision_seconds"]
    if type(precision) not in (int, float) or not math.isfinite(precision) or not 0 <= precision <= 1:
        _error(path + ".precision_seconds", "must be a finite number in [0, 1]")


def _validate_landed(value, path: str) -> None:
    row = _object(value, path, {"policy", "default"})
    if row["policy"] not in LANDED_POLICIES:
        _error(path + ".policy", "unsupported landed policy")
    if row["default"] is not None and type(row["default"]) is not bool:
        _error(path + ".default", "must be boolean or null")
    if row["policy"] == "default" and row["default"] is None:
        _error(path, "default landed policy requires a boolean")
    if row["policy"] == "scenario_required" and row["default"] is not None:
        _error(path, "scenario-required landed policy cannot contain a default")


def _validate_emission(value, path: str) -> None:
    keys = {"component_id", "kind", "attack_source", "prototype", "application", "schedule", "landed",
            "self_sampling", "evidence"}
    row = _object(value, path, keys)
    _id(row["component_id"], path + ".component_id")
    if row["kind"] not in EMISSION_KINDS:
        _error(path + ".kind", "must be fixed or periodic")
    _validate_attack_source(row["attack_source"], path + ".attack_source")
    _validate_prototype(row["prototype"], path + ".prototype")
    _validate_application(row["application"], path + ".application")
    if row["kind"] != "periodic" and row["application"].get("cadence") is not None:
        _error(path + ".application.cadence", "compact cadence is only valid for periodic emissions")
    _validate_schedule(row["schedule"], row["kind"], path + ".schedule")
    _validate_landed(row["landed"], path + ".landed")
    if row["self_sampling"] not in SAMPLING_MODES:
        _error(path + ".self_sampling", "unsupported sampling mode")
    evidence = _object(row["evidence"], path + ".evidence", {"dispatch", "timing", "damage", "application"})
    for name, field in evidence.items():
        _validate_field_evidence(field, path + ".evidence." + name)


def _validate_lifecycle(value, path: str) -> None:
    keys = {"establishment_offset_seconds", "instance_end_offset_seconds", "precision_seconds",
            "owner_and_target_survive", "no_interrupt", "no_refresh_replacement_or_early_despawn",
            "evidence"}
    row = _object_optional(value, path, keys, {"replacement_policy", "no_early_despawn"})
    _time(
        row["establishment_offset_seconds"],
        path + ".establishment_offset_seconds",
        nullable=True,
        zero=True,
    )
    _time(row["instance_end_offset_seconds"], path + ".instance_end_offset_seconds", nullable=True)
    precision = row["precision_seconds"]
    if precision is not None and (type(precision) not in (int, float) or not math.isfinite(precision) or not 0 <= precision <= 1):
        _error(path + ".precision_seconds", "must be null or a finite number in [0, 1]")
    for key in ("owner_and_target_survive", "no_interrupt", "no_refresh_replacement_or_early_despawn"):
        if row[key] is not None and type(row[key]) is not bool:
            _error(path + "." + key, "must be boolean or null")
    replacement = row.get("replacement_policy")
    if replacement is None:
        if "no_early_despawn" in row:
            _error(path + ".no_early_despawn", "cannot be declared without a replacement policy")
    elif (replacement != REPLACEMENT_POLICY
          or row["no_refresh_replacement_or_early_despawn"] is not False
          or row.get("no_early_despawn") is not True):
        _error(path, "replace_previous requires replacement=true and independently confirmed no early despawn")
    _validate_field_evidence(row["evidence"], path + ".evidence")


def _validate_dataset(value, path: str) -> None:
    row = _object(value, path, {"version_line", "upstream_commit", "normalization_profile_id", "snapshot_id"})
    if row["version_line"] not in ("release", "beta"):
        _error(path + ".version_line", "must be release or beta")
    if not isinstance(row["upstream_commit"], str) or not row["upstream_commit"].strip() or len(row["upstream_commit"]) > 128:
        _error(path + ".upstream_commit", "must be a non-empty bounded dataset revision")
    _id(row["normalization_profile_id"], path + ".normalization_profile_id")
    if row["snapshot_id"] is not None:
        _id(row["snapshot_id"], path + ".snapshot_id")


def _validate_review(value, path: str) -> None:
    row = _object(value, path, {"status", "date", "reviewer", "notes"})
    if row["status"] not in ("reviewed", "pending"):
        _error(path + ".status", "must be reviewed or pending")
    if not isinstance(row["date"], str) or not _DATE.fullmatch(row["date"]):
        _error(path + ".date", "must use YYYY-MM-DD")
    for key, limit in (("reviewer", 128), ("notes", 2048)):
        if not isinstance(row[key], str) or not row[key].strip() or len(row[key]) > limit:
            _error(path + "." + key, f"must be non-empty and at most {limit} characters")


def validate_profile(value, path: str = "profile") -> dict:
    keys = {"profile_id", "status", "selector", "ruleset", "emissions", "lifecycle", "dataset", "review"}
    row = _object(value, path, keys)
    _id(row["profile_id"], path + ".profile_id")
    if row["status"] not in PROFILE_STATUSES:
        _error(path + ".status", "must be confirmed or pending")
    selector = validate_selector(row["selector"], path + ".selector")
    if row["ruleset"] not in RULESETS:
        _error(path + ".ruleset", "unsupported elemental ruleset")
    emissions = row["emissions"]
    if not isinstance(emissions, list) or not 1 <= len(emissions) <= MAX_EMISSIONS:
        _error(path + ".emissions", "must contain 1 to 64 emissions")
    for index, emission in enumerate(emissions):
        _validate_emission(emission, f"{path}.emissions[{index}]")
    component_ids = [item["component_id"] for item in emissions]
    if len(component_ids) != len(set(component_ids)):
        _error(path + ".emissions", "component_id values must be unique")
    _validate_lifecycle(row["lifecycle"], path + ".lifecycle")
    _validate_dataset(row["dataset"], path + ".dataset")
    _validate_review(row["review"], path + ".review")
    if row["dataset"]["version_line"] != selector["version_line"]:
        _error(path, "selector and dataset version_line must match")
    if row["status"] == "confirmed" and row["review"]["status"] != "reviewed":
        _error(path, "confirmed profile requires reviewed status")
    return row


def validate_bundle(value) -> dict:
    row = _object(value, "bundle", {"schema_version", "profile_version", "contract_id", "profiles"})
    if row["schema_version"] != SCHEMA_VERSION or row["profile_version"] != PROFILE_VERSION:
        _error("bundle", "unsupported schema or profile version")
    if row["contract_id"] != CONTRACT_ID:
        _error("bundle.contract_id", "unsupported contract")
    profiles = row["profiles"]
    if not isinstance(profiles, list) or len(profiles) > MAX_PROFILES:
        _error("bundle.profiles", "must be a bounded list")
    for index, profile in enumerate(profiles):
        validate_profile(profile, f"bundle.profiles[{index}]")
    profile_ids = [profile["profile_id"] for profile in profiles]
    if len(profile_ids) != len(set(profile_ids)):
        _error("bundle.profiles", "profile_id values must be unique")
    try:
        encoded = json.dumps(row, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode("utf8")
    except (TypeError, ValueError, OverflowError, RecursionError) as exc:
        raise ActionDispatchProfileError("bundle: must be finite JSON") from exc
    if len(encoded) > MAX_BUNDLE_BYTES:
        _error("bundle", "exceeds the 2 MiB resource limit")
    return row
