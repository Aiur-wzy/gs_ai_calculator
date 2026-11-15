"""Offline closure for reviewed action admission and its actual execution.

The database is deliberately not consulted here.  A historical result carries the
reviewed material that was used by the compiler, the compiled private marker, and
one bounded engine input.  Validation rebuilds every local fingerprint and replays
that input with the same engine before accepting the saved runtime receipts.

This is an internal-consistency proof.  It does not add the external trust root
which the action-admission design explicitly postpones.
"""

from __future__ import annotations

import copy
import re

from genshin_db.runtime_fact_inventory import (
    KNOWN_FACT_KINDS,
    MAX_CLASSIFICATION_FACTS,
)
from genshin_engine import action_admission_audit as audit
from genshin_runtime.action_admission_model import (
    FIELD_AUTHORITY,
    PROFILE_CONTRACT,
    UNKNOWNS,
    evidence_for,
    selection_matches,
    validate_model,
)
from genshin_runtime.action_root_semantics import bounded
from genshin_runtime.mechanism_semantics import (
    record_payload_fingerprint,
    record_review_fingerprint,
    validate_record,
)

from .action_admission_schema import CONTEXT
from .action_admissions import COMPILER, MARKER
from .files import fingerprint, input_view
from .snapshot_capture_receipt import EXECUTION_KEYS
from .verification_scope import verification_scope

CONTRACT = "action-admission-result-receipt-v1"
FIELD = "action_admission_execution_input"
MESSAGE = "动作接纳模型、静态证书、实际父动作或运行时回执不一致，请回App重新计算。"

_RECORD_KEYS = {
    "id",
    "ability_id",
    "actor",
    "compiler",
    "status",
    "selection",
    "variant",
    "action_type_id",
    "model_ref",
    "context",
    "issues",
    "profile",
    "required_confirmation",
    "source_fingerprint",
    "scope_fingerprint",
    "static_certificate",
    "compiled_values",
    "compilation_fingerprint",
}
_CERTIFICATE_KEYS = {
    "contract",
    "binding_id",
    "model_ref",
    "model_receipt",
    "snapshot_id",
    "resource_fingerprint",
    "source_fingerprint",
    "scope_fingerprint",
    "selection",
    "variant",
    "action_type_id",
    "field_authority",
    "compiled_values",
    "declared_not_verified",
    "ability_id",
    "action_ids",
    "initial_cooldown",
    "context",
    "aliases",
    "capacity_dependencies",
    "static_certificate_fingerprint",
}
_RUNTIME_KEYS = {
    "id",
    "run_id",
    "contract",
    "phase",
    "parent_run_id",
    "binding_id",
    "model_ref",
    "source_fingerprint",
    "static_certificate_fingerprint",
    "actor",
    "ability_id",
    "action_id",
    "action_type_id",
    "variant",
    "cycle",
    "source_cycle",
    "t",
    "gates",
    "energy_before",
    "energy_after",
    "energy_required",
    "energy_amount",
    "charges_before",
    "charges_after",
    "next_ready_at",
    "cooldown_group",
    "frontline_before",
    "frontline_after",
    "status",
}
_SUMMARY_KEYS = {
    "contract",
    "count_scope",
    "unreported_actions",
    "planned_actions",
    "reported_actions",
    "committed_actions",
    "rejected_actions",
    "not_committed_actions",
    "not_verified_actions",
    "receipts",
    "receipt_count",
    "receipts_fingerprint",
    "receipt_bytes",
    "limits",
}
_STATUSES = {"committed", "rejected", "not_committed", "not_verified"}


def _equal(actual, expected):
    if fingerprint(actual) != fingerprint(expected):
        raise ValueError(MESSAGE)


def _without(value, key):
    return {name: item for name, item in value.items() if name != key}


def _head(record, receipt, snapshot):
    validate_record(record, "fact")
    if record["review"]["status"] != "approved" or type(receipt) is not dict:
        raise ValueError(MESSAGE)
    expected = {
        "version_line": snapshot["version_line"],
        "record_kind": "fact",
        "record_id": record["id"],
        "revision": record["revision"],
        "head_updated_at": receipt.get("head_updated_at"),
        "payload_fingerprint": record_payload_fingerprint(record, "fact"),
        "review_fingerprint": record_review_fingerprint(record, "fact"),
    }
    if (
        type(expected["head_updated_at"]) is not str
        or not 1 <= len(expected["head_updated_at"]) <= 80
    ):
        raise ValueError(MESSAGE)
    _equal(receipt, expected)
    _equal(
        record["applicability"],
        {
            "version_line": snapshot["version_line"],
            "upstream_commit": snapshot["upstream_commit"],
            "normalization_fingerprint": fingerprint(snapshot["normalization"]),
        },
    )


def _verify_resource_profile(profile):
    """Rebuild the saved exact resource projection without reading the DB."""
    from genshin_db.runtime_action_resource import (
        RESOURCE_FIELDS,
        RESOURCE_LIMITS,
        _resource_projection,
    )

    if type(profile) is not dict:
        raise ValueError(MESSAGE)
    bounded(profile)
    if set(profile) != {
        "projection_version",
        "status",
        "scope",
        "snapshot",
        "selection",
        "identity_sources",
        "projection",
        "descriptions",
        "variant_applicability",
        "execution_support",
        "action_dispatch",
        "gameplay_interpretation",
        "limits",
        "notes",
        "resource_fingerprint",
        "content_fingerprint",
    }:
        raise ValueError(MESSAGE)
    content = profile.get("content_fingerprint")
    _equal(content, fingerprint(_without(profile, "content_fingerprint")))
    if (
        profile.get("projection_version") != 1
        or profile.get("status") != "ready"
        or profile.get("scope") != "base_skill_record"
        or profile.get("limits") != RESOURCE_LIMITS
    ):
        raise ValueError(MESSAGE)
    sources = profile.get("identity_sources")
    if type(sources) is not dict or type(sources.get("skill")) is not dict:
        raise ValueError(MESSAGE)
    projection = {
        name: _resource_projection(sources["skill"], name, field)
        for name, field in RESOURCE_FIELDS.items()
    }
    _equal(profile.get("projection"), projection)
    if (
        any(projection[key]["status"] != "ready" for key in RESOURCE_FIELDS)
        or projection["base_charge_count"]["value"] != 1
        or projection["base_energy_cost"]["value"] <= 0
    ):
        raise ValueError(MESSAGE)
    descriptions = profile.get("descriptions")
    if (
        type(descriptions) is not dict
        or descriptions.get("status") != "ready"
        or any(
            type(descriptions.get(key)) is not dict
            or descriptions[key].get("status") != "ready"
            or type(descriptions[key].get("text")) is not str
            or not descriptions[key]["text"]
            or (descriptions[key].get("text_origin") or {}).get("node_sha256")
            != fingerprint(descriptions[key]["text"])
            for key in ("name", "description")
        )
    ):
        raise ValueError(MESSAGE)
    selection = profile.get("selection")
    if type(selection) is not dict:
        raise ValueError(MESSAGE)
    canonical_selection = {
        key: value for key, value in selection.items() if key != "slot"
    }
    expected = fingerprint(
        {
            "projection_version": 1,
            "snapshot": profile.get("snapshot"),
            "selection": canonical_selection,
            "identity_sources": sources,
            "projection": projection,
            "descriptions": descriptions,
        }
    )
    _equal(profile.get("resource_fingerprint"), expected)


def verify_saved_profile(profile):
    """Validate the full material captured by the fresh profile."""
    if type(profile) is not dict:
        raise ValueError(MESSAGE)
    bounded(profile)
    expected_keys = {
        "contract",
        "status",
        "reason",
        "selection",
        "variant",
        "action_type_id",
        "snapshot",
        "model_ref",
        "model_receipt",
        "model_record",
        "resource_profile",
        "field_authority",
        "compiled_values",
        "declared_not_verified",
        "competitor_inventory",
        "current_head_fingerprint",
        "source_fingerprint",
        "execution_authority",
    }
    if (
        set(profile) != expected_keys
        or profile["contract"] != PROFILE_CONTRACT
        or profile["status"] != "ready"
        or profile["reason"] is not None
        or profile["variant"] != "default"
        or profile["action_type_id"] != "action.elemental_burst"
        or profile["field_authority"] != FIELD_AUTHORITY
        or profile["declared_not_verified"] != UNKNOWNS
        or profile["execution_authority"] is not False
    ):
        raise ValueError(MESSAGE)
    snapshot, model = profile["snapshot"], profile["model_record"]
    _head(model, profile["model_receipt"], snapshot)
    _equal(model["payload"]["kind"], "action_admission_model")
    _equal(profile["model_ref"], {"id": model["id"], "revision": model["revision"]})
    claim = model["payload"]["claim"]
    validate_model(claim)
    _equal(model["evidence"], evidence_for(claim))
    _equal(claim["snapshot"], snapshot)
    if model["subject"] != {
        "target_type": "character",
        "target_id": profile["selection"]["character_id"],
    } or not selection_matches(
        claim, profile["selection"], profile["variant"], profile["action_type_id"]
    ):
        raise ValueError(MESSAGE)

    inventory = profile["competitor_inventory"]
    if (
        type(inventory) is not dict
        or set(inventory)
        != {"contract", "complete", "scope", "selection_key", "entries"}
        or inventory["contract"] != "current-action-admission-competitors-v1"
        or inventory["complete"] is not True
        or inventory["scope"] != "all_current_fact_heads_in_version_line"
        or type(inventory["entries"]) is not list
        or not 1 <= len(inventory["entries"]) <= MAX_CLASSIFICATION_FACTS
    ):
        raise ValueError(MESSAGE)
    target_key = {
        "target": copy.deepcopy(claim["target"]),
        "variant": claim["domain"]["variant"],
        "action_type_id": claim["domain"]["action_type_id"],
        "owned_fields": sorted(FIELD_AUTHORITY),
    }
    _equal(inventory["selection_key"], target_key)
    identities = [(row.get("id"), row.get("revision")) for row in inventory["entries"]]
    if identities != sorted(identities) or len({row[0] for row in identities}) != len(
        identities
    ):
        raise ValueError(MESSAGE)
    base_keys = {
        "record_kind",
        "id",
        "revision",
        "head_updated_at",
        "payload_fingerprint",
        "review_fingerprint",
        "fact_kind",
        "review_status",
        "status",
        "reason",
        "disposition",
    }
    for row in inventory["entries"]:
        if type(row) is not dict:
            raise ValueError(MESSAGE)
        admission = row.get("fact_kind") == "action_admission_model"
        if (
            set(row) != base_keys | ({"target_key_fingerprint"} if admission else set())
            or row.get("record_kind") != "fact"
            or type(row.get("id")) is not str
            or re.fullmatch(r"[a-z0-9][a-z0-9._-]{0,79}", row["id"]) is None
            or type(row.get("revision")) is not int
            or not 1 <= row["revision"] <= 2**31 - 1
            or type(row.get("head_updated_at")) is not str
            or not 1 <= len(row["head_updated_at"]) <= 80
            or len(row["head_updated_at"].encode("utf-8")) > 320
            or any(
                type(row.get(key)) is not str
                or re.fullmatch(r"[0-9a-f]{64}", row[key]) is None
                for key in ("payload_fingerprint", "review_fingerprint")
            )
            or row.get("fact_kind") not in KNOWN_FACT_KINDS
            or row.get("review_status")
            not in {"draft", "pending", "approved", "revoked"}
            or row.get("status")
            != ("ready" if row["review_status"] == "approved" else "pending")
            or row.get("reason")
            != (None if row["review_status"] == "approved" else "review_not_approved")
            or row.get("disposition")
            not in {"selected_model", "classified_non_competing"}
            or (
                admission
                and (
                    type(row.get("target_key_fingerprint")) is not str
                    or re.fullmatch(r"[0-9a-f]{64}", row["target_key_fingerprint"])
                    is None
                )
            )
        ):
            raise ValueError(MESSAGE)
    selected = [row for row in inventory["entries"] if row.get("id") == model["id"]]
    if (
        len(selected) != 1
        or selected[0].get("revision") != model["revision"]
        or selected[0].get("status") != "ready"
        or selected[0].get("reason") is not None
        or selected[0].get("disposition") != "selected_model"
        or selected[0].get("target_key_fingerprint") != fingerprint(target_key)
        or any(
            row.get("disposition") != "classified_non_competing"
            for row in inventory["entries"]
            if row is not selected[0]
        )
    ):
        raise ValueError(MESSAGE)
    _equal(profile["current_head_fingerprint"], fingerprint(inventory))

    resource = profile["resource_profile"]
    _verify_resource_profile(resource)
    selected_resource = resource["selection"]
    selection = profile["selection"]
    if (
        selected_resource["character_id"] != selection["character_id"]
        or selected_resource["skill_depot_id"] != selection["skill_depot_id"]
        or selected_resource["skill_id"] != selection["skill_id"]
        or "burst" not in selected_resource["matching_slots"]
        or resource["resource_fingerprint"]
        != claim["resource_binding"]["resource_fingerprint"]
    ):
        raise ValueError(MESSAGE)
    projection, binding = resource["projection"], claim["resource_binding"]
    cooldown = projection["base_cooldown_seconds"]["value"]
    charges = projection["base_charge_count"]["value"]
    energy = projection["base_energy_cost"]["value"]
    values = {
        "cooldown": {
            "group": binding["cooldown_group"],
            "duration": cooldown,
            "start": binding["cooldown_start"],
            "charges": charges,
            "recovery": binding["cooldown_recovery"],
        },
        "energy": {
            "required": energy,
            "amount": energy,
            "delivery": binding["energy_delivery"],
        },
    }
    _equal(profile["compiled_values"], values)
    material = {
        "model_receipt": profile["model_receipt"],
        "model_payload_fingerprint": fingerprint(model),
        "resource_fingerprint": resource["resource_fingerprint"],
        "current_head_fingerprint": profile["current_head_fingerprint"],
        "selection": selection,
        "variant": profile["variant"],
        "action_type_id": profile["action_type_id"],
        "compiled_values": values,
        "field_authority": FIELD_AUTHORITY,
        "declared_not_verified": claim["unknowns"],
    }
    _equal(profile["source_fingerprint"], fingerprint(material))


def _verify_records(records, rotation):
    if (
        type(records) is not list
        or not 1 <= len(records) <= 64
        or any(type(row) is not dict for row in records)
        or len({row.get("id") for row in records}) != len(records)
        or len({row.get("ability_id") for row in records}) != len(records)
    ):
        raise ValueError(MESSAGE)
    abilities = {row.get("id"): row for row in rotation.get("abilities", [])}
    actions = rotation.get("actions", [])
    owned = set()
    for record in records:
        if (
            set(record) != _RECORD_KEYS
            or record["status"] != "ready"
            or record["issues"] != []
        ):
            raise ValueError(MESSAGE)
        _equal(record["compiler"], COMPILER)
        _equal(
            record["compilation_fingerprint"],
            fingerprint(_without(record, "compilation_fingerprint")),
        )
        profile = record["profile"]
        verify_saved_profile(profile)
        aid, actor = record["ability_id"], record["actor"]
        ability = abilities.get(aid)
        if (
            type(ability) is not dict
            or aid in owned
            or actor != ability.get("character")
        ):
            raise ValueError(MESSAGE)
        owned.add(aid)
        _equal(record["selection"], profile["selection"])
        _equal(record["variant"], profile["variant"])
        _equal(record["action_type_id"], profile["action_type_id"])
        _equal(record["model_ref"], profile["model_ref"])
        _equal(record["context"], CONTEXT)

        action_ids = sorted(
            row["id"] for row in actions if row.get("ability_id") == aid
        )
        if not action_ids:
            raise ValueError(MESSAGE)
        values = profile["compiled_values"]
        group = values["cooldown"]["group"]
        initial = [
            row
            for row in rotation.get("initial_cooldowns", [])
            if row.get("character") == actor and row.get("group") == group
        ]
        if (
            len(initial) != 1
            or len(initial[0].get("ready_in", [])) > values["cooldown"]["charges"]
        ):
            raise ValueError(MESSAGE)
        aliases = [
            other["id"]
            for other in rotation.get("abilities", [])
            if other.get("id") != aid
            and other.get("character") == actor
            and isinstance(other.get("cooldown"), dict)
            and other["cooldown"].get("group") == group
        ]
        if aliases or any(
            event.get("type") == "cooldown_change"
            and event.get("character") == actor
            and event.get("group") == group
            for event in rotation.get("events", [])
        ):
            raise ValueError(MESSAGE)
        binding = {
            "id": record["id"],
            "ability_id": aid,
            "version_line": profile["snapshot"]["version_line"],
            "selection": copy.deepcopy(record["selection"]),
            "variant": record["variant"],
            "model_ref": copy.deepcopy(record["model_ref"]),
        }
        scope = {
            "compiler": COMPILER,
            "binding": binding,
            "ability": {
                key: copy.deepcopy(ability.get(key))
                for key in (
                    "id",
                    "character",
                    "action_type_id",
                    "variant",
                    "status",
                    "source",
                )
            },
            "action_ids": action_ids,
            "initial_cooldown": copy.deepcopy(initial[0]),
            "context": copy.deepcopy(CONTEXT),
            "aliases": [],
            "capacity_dependencies": [],
            "profile_source_fingerprint": profile["source_fingerprint"],
        }
        scope_fp = fingerprint(scope)
        source_fp = fingerprint(
            {
                "profile_source_fingerprint": profile["source_fingerprint"],
                "scope_fingerprint": scope_fp,
            }
        )
        _equal(record["scope_fingerprint"], scope_fp)
        _equal(record["source_fingerprint"], source_fp)
        _equal(record["required_confirmation"], {"accepted_fingerprint": source_fp})
        certificate = record["static_certificate"]
        if type(certificate) is not dict or set(certificate) != _CERTIFICATE_KEYS:
            raise ValueError(MESSAGE)
        expected_certificate = {
            "contract": "static-action-admission-certificate-v1",
            "binding_id": record["id"],
            "model_ref": copy.deepcopy(record["model_ref"]),
            "model_receipt": copy.deepcopy(profile["model_receipt"]),
            "snapshot_id": profile["snapshot"]["snapshot_id"],
            "resource_fingerprint": profile["resource_profile"]["resource_fingerprint"],
            "source_fingerprint": source_fp,
            "scope_fingerprint": scope_fp,
            "selection": copy.deepcopy(record["selection"]),
            "variant": record["variant"],
            "action_type_id": record["action_type_id"],
            "field_authority": copy.deepcopy(profile["field_authority"]),
            "compiled_values": copy.deepcopy(values),
            "declared_not_verified": copy.deepcopy(profile["declared_not_verified"]),
            "ability_id": aid,
            "action_ids": action_ids,
            "initial_cooldown": copy.deepcopy(initial[0]),
            "context": copy.deepcopy(CONTEXT),
            "aliases": [],
            "capacity_dependencies": [],
        }
        static_fp = fingerprint(expected_certificate)
        expected_certificate["static_certificate_fingerprint"] = static_fp
        _equal(certificate, expected_certificate)
        marker = {
            "contract": MARKER,
            "binding_id": record["id"],
            "model_ref": copy.deepcopy(record["model_ref"]),
            "source_fingerprint": source_fp,
            "static_certificate_fingerprint": static_fp,
        }
        _equal(ability.get("cooldown"), values["cooldown"])
        _equal(ability.get("energy"), values["energy"])
        _equal(ability.get("action_admission_source"), marker)
        _equal(
            record["compiled_values"],
            {**copy.deepcopy(values), "action_admission_source": marker},
        )
    marked = {row["id"] for row in rotation.get("abilities", []) if audit.bound(row)}
    if marked != owned:
        raise ValueError(MESSAGE)


def _parent_rows(check):
    return [
        row
        for row in check.get("event_log", [])
        if row.get("type") == "action" and "action_admission" in row
    ]


def _runtime_projection(check, trace):
    return {
        "summary": check.get("action_admission_execution"),
        "parents": _parent_rows(check),
        # A v2 admission owns fixed action-start energy as part of the same
        # atomic commit.  Preserve the companion ledger as actual execution
        # evidence instead of leaving it to a legacy resource-only proof.
        "automatic_energy_summary": check.get("automatic_energy_delivery"),
        "automatic_energy_audits": [
            row
            for row in check.get("event_log", [])
            if row.get("delivery") == "automatic_action_start"
        ],
        "trace_references": [
            row.get("action_admission_certificate")
            for row in trace
            if "action_admission_certificate" in row
        ],
        "status": check.get("status"),
        "issues": check.get("issues"),
    }


def _verify_runtime(rotation, check, trace):
    summary = check.get("action_admission_execution")
    if type(summary) is not dict or set(summary) not in (
        _SUMMARY_KEYS,
        _SUMMARY_KEYS | {"termination"},
    ):
        raise ValueError(MESSAGE)
    if (
        summary["contract"] != audit.CONTRACT
        or summary["count_scope"] != "reported_action_rows"
        or summary["limits"] != audit.capabilities()["limits"]
        or type(summary["receipts"]) is not list
    ):
        raise ValueError(MESSAGE)
    parents = _parent_rows(check)
    statuses = [row.get("action_admission", {}).get("status") for row in parents]
    if any(status not in _STATUSES for status in statuses):
        raise ValueError(MESSAGE)
    counts = {status: statuses.count(status) for status in _STATUSES}
    reported = len(parents)
    if (
        summary["reported_actions"] != reported
        or summary["committed_actions"] != counts["committed"]
        or summary["rejected_actions"] != counts["rejected"]
        or summary["not_committed_actions"] != counts["not_committed"]
        or summary["not_verified_actions"] != counts["not_verified"]
        or summary["receipt_count"] != len(summary["receipts"])
        or summary["receipt_count"] != counts["committed"]
        or summary["receipts_fingerprint"] != fingerprint(summary["receipts"])
        or summary["receipt_bytes"] != len(audit.canonical(summary["receipts"]))
        or summary["receipt_count"] > summary["limits"]["max_receipts"]
        or summary["receipt_bytes"] > summary["limits"]["max_receipt_bytes"]
        or summary["planned_actions"] < reported
        or summary["unreported_actions"] != summary["planned_actions"] - reported
    ):
        raise ValueError(MESSAGE)

    abilities = {row["id"]: row for row in rotation["abilities"]}
    receipts = summary["receipts"]
    by_run = {}
    for row in receipts:
        if (
            type(row) is not dict
            or set(row) != _RUNTIME_KEYS
            or row["status"] != "committed"
        ):
            raise ValueError(MESSAGE)
        if row["run_id"] in by_run:
            raise ValueError(MESSAGE)
        by_run[row["run_id"]] = row
        ability = abilities.get(row["ability_id"])
        if type(ability) is not dict or not audit.bound(ability):
            raise ValueError(MESSAGE)
        marker = audit.source(ability)
        expected_id = audit.certificate_id(row["action_id"])
        if (
            row["id"] != expected_id
            or row["run_id"] != f"{row['source_cycle']}:{expected_id}"
            or row["contract"] != audit.CONTRACT
            or row["phase"] != "action_admission"
            or row["binding_id"] != marker["binding_id"]
            or row["model_ref"] != marker["model_ref"]
            or row["source_fingerprint"] != marker["source_fingerprint"]
            or row["static_certificate_fingerprint"]
            != marker["static_certificate_fingerprint"]
            or row["actor"] != ability["character"]
            or row["action_type_id"] != ability["action_type_id"]
            or row["variant"] != ability.get("variant")
            or row["gates"]
            != {
                "frontline": "passed",
                "occupation": "passed",
                "energy": "passed",
                "cooldown": "passed",
            }
            or row["energy_required"] != ability["energy"]["required"]
            or row["energy_amount"] != ability["energy"]["amount"]
            or row["energy_after"]
            != max(0, row["energy_before"] - row["energy_amount"])
            or row["charges_after"] != row["charges_before"] - 1
            or row["cooldown_group"] != ability["cooldown"]["group"]
        ):
            raise ValueError(MESSAGE)

    committed_refs = []
    for parent in parents:
        admission = parent["action_admission"]
        ability = abilities.get(parent.get("ability_id"))
        if type(ability) is not dict or not audit.bound(ability):
            raise ValueError(MESSAGE)
        marker = audit.source(ability)
        base = {
            "contract": audit.CONTRACT,
            "binding_id": marker["binding_id"],
            "source_fingerprint": marker["source_fingerprint"],
            "static_certificate_fingerprint": marker["static_certificate_fingerprint"],
            "status": admission["status"],
        }
        if admission["status"] == "committed":
            receipt = by_run.get((admission.get("certificate") or {}).get("run_id"))
            if (
                receipt is None
                or receipt["parent_run_id"] != parent.get("run_id")
                or receipt["action_id"] != parent.get("action_id")
                or receipt["ability_id"] != parent.get("ability_id")
                or receipt["actor"] != parent.get("character")
                or receipt["cycle"] != parent.get("cycle")
                or receipt["source_cycle"] != parent.get("source_cycle")
                or receipt["t"] != parent.get("t")
                or receipt["energy_before"]
                != (parent.get("energy_admission") or {}).get("before")
                or receipt["energy_after"]
                != (parent.get("energy_admission") or {}).get("after")
                or receipt["charges_before"] != parent.get("charges_before")
                or receipt["next_ready_at"] != parent.get("next_ready_at")
                or receipt["frontline_before"] != parent.get("frontline")
                or receipt["frontline_after"] != parent.get("frontline_after")
            ):
                raise ValueError(MESSAGE)
            reference = {
                "contract": audit.CONTRACT,
                "certificate_id": receipt["id"],
                "run_id": receipt["run_id"],
            }
            base["certificate"] = reference
            committed_refs.append(reference)
        elif admission["status"] in ("rejected", "not_committed"):
            if type(admission.get("reason")) is not str or not admission["reason"]:
                raise ValueError(MESSAGE)
            base["reason"] = admission["reason"]
        _equal(admission, base)
    _equal(
        committed_refs,
        [
            {
                "contract": audit.CONTRACT,
                "certificate_id": row["id"],
                "run_id": row["run_id"],
            }
            for row in receipts
        ],
    )
    allowed = {fingerprint(row) for row in committed_refs}
    trace_refs = [
        row["action_admission_certificate"]
        for row in trace
        if "action_admission_certificate" in row
    ]
    if any(
        type(ref) is not dict or fingerprint(ref) not in allowed for ref in trace_refs
    ):
        raise ValueError(MESSAGE)


def _proof(result):
    check = result["rotation_check"]
    records = result["action_admission_compilations"]
    execution = result[FIELD]
    summary = check["action_admission_execution"]
    automatic = {
        "summary": check.get("automatic_energy_delivery"),
        "audits": [
            row
            for row in check.get("event_log", [])
            if row.get("delivery") == "automatic_action_start"
        ],
    }
    return {
        "contract": CONTRACT,
        "approval_scope": "historical_internal_consistency_recalculation_requires_fresh_database",
        "compilations_fingerprint": fingerprint(records),
        "compiled_rotation_model_fingerprint": fingerprint(execution["rotation_model"]),
        "execution_input_fingerprint": fingerprint(execution),
        "runtime_fingerprint": fingerprint(
            _runtime_projection(check, result.get("mechanics_trace", []))
        ),
        "automatic_energy_fingerprint": fingerprint(automatic),
        "receipts_fingerprint": summary["receipts_fingerprint"],
        "static_certificate_fingerprints": [
            row["static_certificate"]["static_certificate_fingerprint"]
            for row in records
        ],
    }


def _has(result, receipt):
    check = result.get("rotation_check") or {}
    rotation = check.get("resolved_rotation_model") or {}
    return bool(
        result.get("action_admission_compilations")
        or FIELD in result
        or "action_admissions" in receipt
        or check.get("action_admission_execution")
        or any(audit.bound(row) for row in rotation.get("abilities", []))
        or any("action_admission" in row for row in check.get("event_log", []))
        or any(
            "action_admission_certificate" in row
            for row in result.get("mechanics_trace", [])
        )
    )


def make_action_admission_receipt(plan, report, result, *, _session):
    records = report.get("action_admission_compilations")
    if not records:
        if _has(result, {}):
            raise ValueError(MESSAGE)
        return None
    execution = {
        "template": report["mechanics_input_template"],
        "rotation_model": result["rotation_check"]["resolved_rotation_model"],
        "mechanics_model": report["mechanics_model"],
        "matches": report["mechanics_matches"],
    }
    _session.scan_receipt_materials(
        inputs=(records, execution), outputs=(result["rotation_check"],)
    )
    result["action_admission_compilations"] = copy.deepcopy(records)
    result[FIELD] = copy.deepcopy(execution)
    _verify_records(records, execution["rotation_model"])
    _verify_runtime(
        execution["rotation_model"],
        result["rotation_check"],
        result.get("mechanics_trace", []),
    )
    return _proof(result)


@verification_scope
def validate_action_admission_result_receipt(result, receipt, *, _session=None):
    if not _has(result, receipt):
        return
    try:
        records, execution, check = (
            result["action_admission_compilations"],
            result[FIELD],
            result["rotation_check"],
        )
        proof = receipt["action_admissions"]
        if (
            type(execution) is not dict
            or set(execution) != EXECUTION_KEYS
            or type(execution["matches"]) is not list
        ):
            raise ValueError(MESSAGE)
        _session.scan_receipt_materials(
            inputs=(records, execution, proof), outputs=(check,)
        )
        _verify_records(records, execution["rotation_model"])
        _equal(check["resolved_rotation_model"], execution["rotation_model"])
        _equal(check["input_fingerprint"], fingerprint(execution))
        _verify_runtime(
            execution["rotation_model"], check, result.get("mechanics_trace", [])
        )
        _equal(proof, _proof(result))
        _equal(receipt["result_fingerprint"], fingerprint(result))

        replay = _session.replay(execution, _result_fields=(),
                                 _response_fields=("rotation_check", "mechanics_trace"))
        if replay.get("result") is None or replay.get("resolved_template") is None:
            raise ValueError(MESSAGE)
        _equal(
            _runtime_projection(
                replay["rotation_check"], replay.get("mechanics_trace", [])
            ),
            _runtime_projection(check, result.get("mechanics_trace", [])),
        )
        _equal(fingerprint(replay["resolved_template"]), receipt["output_fingerprint"])
    except (
        KeyError,
        TypeError,
        AttributeError,
        IndexError,
        ArithmeticError,
        RecursionError,
    ) as exc:
        raise ValueError(MESSAGE) from exc


@verification_scope
def validate_action_admission_receipt(plan, result, report, receipt, *, _session=None):
    if not (
        plan.get("action_admission_sources")
        or report.get("action_admission_compilations")
        or _has(result, receipt)
    ):
        return
    try:
        from genshin_app.rotation_check import resolve_rotation_model

        from .action_admission_schema import validate_action_admission_bindings

        validate_action_admission_bindings(plan)
        validate_action_admission_result_receipt(result, receipt, _session=_session)
        records = result["action_admission_compilations"]
        _equal(records, report["action_admission_compilations"])
        _equal(records, plan["resolution"]["action_admission_compilations"])
        bindings = plan["action_admission_sources"]
        if (
            len(bindings) != len(records)
            or plan.get("action_admission_context") != CONTEXT
        ):
            raise ValueError(MESSAGE)
        for binding, record in zip(bindings, records, strict=True):
            expected = {
                "id": record["id"],
                "ability_id": record["ability_id"],
                "version_line": record["profile"]["snapshot"]["version_line"],
                "selection": record["selection"],
                "variant": record["variant"],
                "model_ref": record["model_ref"],
                "accepted_fingerprint": record["source_fingerprint"],
            }
            _equal(binding, expected)
        _verify_records(records, report["compiled_rotation_model"])
        _equal(
            report["compiled_rotation_model_fingerprint"],
            fingerprint(report["compiled_rotation_model"]),
        )
        resolved, panels = resolve_rotation_model(
            report["mechanics_input_template"], report["compiled_rotation_model"]
        )
        _equal(
            result[FIELD],
            {
                "template": report["mechanics_input_template"],
                "rotation_model": resolved,
                "mechanics_model": report["mechanics_model"],
                "matches": report["mechanics_matches"],
            },
        )
        _equal(panels, result["rotation_check"]["panel_bindings"])
        _equal(receipt["input_fingerprint"], fingerprint(input_view(plan)))
        _equal(input_view(plan), input_view(report["plan"]))
    except (
        KeyError,
        TypeError,
        AttributeError,
        IndexError,
        ArithmeticError,
        RecursionError,
    ) as exc:
        raise ValueError(MESSAGE) from exc


__all__ = [
    "CONTRACT",
    "FIELD",
    "make_action_admission_receipt",
    "validate_action_admission_receipt",
    "validate_action_admission_result_receipt",
    "verify_saved_profile",
]
