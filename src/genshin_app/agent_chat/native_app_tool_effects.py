"""Single authority for native App-tool state effects.

The host executes effects while the bridge independently verifies their wire
settlements.  Keeping separate copies of this table makes a newly added tool
safe in one boundary and unsafe in the other.  Both boundaries therefore
import this immutable contract.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from typing import Any

STAGED_PHASES = (
    "loadout_draft",
    "action_checkpoint",
    "event_topology",
    "numeric_model",
    "calculation_closure",
)

STAGE_TRANSITION_FIELDS = frozenset({
    "workflow_stage",
    "stage_revision",
    "tool_manifest_hash",
})

STAGE_TRANSITION_CONTRACT = "genshin-native-stage-transition-v1"

DOMAIN_EFFECTS = frozenset({
    "none",
    "session_resource",
    "finding",
    "candidate",
    "candidate_and_derived",
    "derived_compose",
    "derived_calculation",
    "review_projection",
    "derived_validation",
    "stage_transition",
})

EXTERNAL_EFFECTS = frozenset({
    "external_research_ledger",
    "external_action_ledger",
})

WRITE_EFFECTS = frozenset({
    "session_resource",
    "finding",
    "candidate",
    "candidate_and_derived",
    "derived_compose",
    "derived_calculation",
    "review_projection",
    "derived_validation",
    "stage_transition",
})

ALLOWED_CHANGES: dict[str, frozenset[str]] = {
    "none": frozenset(),
    "session_resource": frozenset({"resources_hash", "stage_state_hash"}),
    "finding": frozenset({"findings_hash", "stage_state_hash"}),
    "candidate": frozenset({
        "candidate_revision", "candidate_hash", "derived_hash", "stage_state_hash",
    }),
    "candidate_and_derived": frozenset({
        "candidate_revision", "candidate_hash", "derived_hash", "stage_state_hash",
    }),
    "derived_compose": frozenset({"derived_hash", "stage_state_hash"}),
    "derived_calculation": frozenset({"derived_hash", "stage_state_hash"}),
    "review_projection": frozenset({
        "derived_hash", "resources_hash", "stage_state_hash",
    }),
    "derived_validation": frozenset({"derived_hash", "stage_state_hash"}),
    "stage_transition": frozenset({
        "workflow_stage", "candidate_revision", "stage_revision", "candidate_hash",
        "resources_hash", "findings_hash", "derived_hash", "stage_state_hash",
    }),
    "external_research_ledger": frozenset(),
    "external_action_ledger": frozenset(),
}

# Every App-owned write is also bound to a canonical projection of the whole
# durable candidate checkpoint.  Fine-grained hashes above remain the effect
# mask authority; the aggregate hash closes recovery gaps when a newly added
# durable field has not yet received its own projection field.
for _effect in WRITE_EFFECTS:
    ALLOWED_CHANGES[_effect] = ALLOWED_CHANGES[_effect] | frozenset({
        "durable_checkpoint_hash",
    })

REQUIRES_CHANGE = frozenset({
    "finding", "candidate", "candidate_and_derived", "stage_transition",
})

ALL_EFFECTS = frozenset(ALLOWED_CHANGES)

# A calculation attempt can intentionally publish a bounded diagnostic while
# refusing result acceptance.  That is an observed, effect-masked settlement,
# not an interrupted write.  Keep this list narrow: ordinary failed writes
# still become outcome-unknown whenever they changed App-owned state.
_SETTLED_FAILURE_COMMITS: dict[str, tuple[str, frozenset[str]]] = {
    "gs_calculate": (
        "derived_calculation",
        frozenset({"mechanics_pending", "agent_calculation_rejected"}),
    ),
}


def _verification_failure_evidence(result, diagnostic_cache, require_cached_verification, candidate_state):
    """Only the exact small cached unsealed attempt may settle derived changes."""
    details = result.get('details')
    if not isinstance(details, Mapping):
        return False
    artifact = details.get('diagnostic')
    if (not isinstance(artifact, dict) or artifact.get('kind') != 'genshin-calculation-diagnostic'
            or artifact.get('sealed') is not False or artifact.get('accepted_result') is not False
            or any(key in artifact for key in ('result', 'result_receipt', 'calculated', 'summary'))):
        return False
    diagnostic = artifact.get('diagnostic')
    if (not isinstance(diagnostic, dict) or diagnostic.get('contract') != 'execution-verification-diagnostic-v1'
            or diagnostic.get('sealed') is not False or diagnostic.get('accepted_result') is not False
            or diagnostic.get('status') != 'verification_incomplete'
            or not isinstance(diagnostic.get('issues'), list) or not diagnostic['issues']
            or any(not isinstance(issue, dict) or not str(issue.get('code', '')).startswith('verification_')
                   or issue.get('severity') != 'pending' for issue in diagnostic['issues'])
            or any(key in diagnostic for key in ('result', 'result_receipt', 'calculated', 'summary'))):
        return False
    candidate_fp, attempt = details.get('candidate_fingerprint'), details.get('attempt_id')
    if (details.get('resource') != 'diagnostic_error' or type(details.get('revision')) is not int
            or details['revision'] < 0 or not isinstance(candidate_fp, str)
            or re.fullmatch('[0-9a-f]{64}', candidate_fp) is None
            or not isinstance(attempt, str) or re.fullmatch('[0-9a-f]{32}', attempt) is None
            or not isinstance(artifact.get('source'), dict)
            or artifact['source'].get('input_fingerprint') != candidate_fp):
        return False
    try:
        raw = json.dumps(artifact, ensure_ascii=False, sort_keys=True, allow_nan=False, separators=(',', ':')).encode('utf8')
    except (TypeError, ValueError, RecursionError, UnicodeError):
        return False
    if len(raw) > 16000 or hashlib.sha256(raw).hexdigest() != details.get('diagnostic_fingerprint'):
        return False
    if require_cached_verification:
        if not isinstance(diagnostic_cache, dict) or not isinstance(candidate_state, Mapping):
            return False
        return (diagnostic_cache.get('revision') == details['revision']
                and candidate_state.get('revision') == details['revision']
                and candidate_state.get('fingerprint') == candidate_fp
                and diagnostic_cache.get('candidate_fingerprint') == candidate_fp
                and diagnostic_cache.get('attempt_id') == attempt
                and diagnostic_cache.get('value') == artifact)
    return True


def allows_settled_failure_commit(
    tool_name: object,
    effect: object,
    result: Mapping[str, Any],
    *, diagnostic_cache=None, require_cached_verification=False, candidate_state=None,
) -> bool:
    """Return whether one failed tool may retain its declared derived state.

    This does not broaden an effect mask.  The Host separately proves that all
    observed changes are allowed for ``effect`` before using this exception.
    """

    if result.get('failure_kind') == 'system':
        from .calculation_attempt_failure import permits_output_validation_failure
        return (tool_name == 'gs_calculate' and effect == 'derived_calculation'
                and permits_output_validation_failure(result, diagnostic_cache=diagnostic_cache,
                    require_cached_verification=require_cached_verification, candidate_state=candidate_state))
    if result.get('reason') == 'calculation_verification_incomplete':
        return (tool_name == 'gs_calculate' and effect == 'derived_calculation'
                and result.get('code') not in (None, 0, False)
                and _verification_failure_evidence(result, diagnostic_cache, require_cached_verification, candidate_state))
    policy = _SETTLED_FAILURE_COMMITS.get(tool_name) if isinstance(tool_name, str) else None
    return (
        policy is not None
        and effect == policy[0]
        and result.get("reason") in policy[1]
        and result.get("code") not in (None, 0, False)
    )


def stage_transition_proof(
    effect: str,
    before: Mapping[str, Any],
    after: Mapping[str, Any],
    *,
    tool_name: str | None = None,
) -> dict[str, Any] | None:
    """Return the sole valid phase-transition consequence for one tool.

    Candidate tools may invalidate later workflow work, but they may never
    advance the workflow.  An explicit stage tool may move in either
    direction.  Merely adding stage fields to a broad effect mask would lose
    that distinction, so both Host and bridge derive and verify this proof.
    """

    if not isinstance(before, Mapping) or not isinstance(after, Mapping):
        return None
    source = before.get("workflow_stage")
    target = after.get("workflow_stage")
    pre_revision = before.get("stage_revision")
    post_revision = after.get("stage_revision")
    pre_manifest = before.get("tool_manifest_hash")
    post_manifest = after.get("tool_manifest_hash")
    if before.get("workflow") == after.get("workflow") == "artifact":
        if (effect == "candidate" and tool_name == "gs_patch_whiteboard"
                and source == target == "artifact"
                and type(pre_revision) is int and type(post_revision) is int
                and post_revision == pre_revision + 1
                and type(before.get("candidate_revision")) is int
                and type(after.get("candidate_revision")) is int
                and after["candidate_revision"] == before["candidate_revision"] + 1
                and isinstance(pre_manifest, str) and isinstance(post_manifest, str)
                and pre_manifest != post_manifest
                and _representation_catalog(before) == "whiteboard"
                and _representation_catalog(after) == "native"):
            # This is a change of input representation, not a workflow grant.
            # The Host also checks the actual candidate/stage-state transition;
            # the bridge independently derives the public catalog consequence.
            return {
                "contract": STAGE_TRANSITION_CONTRACT,
                "kind": "artifact_representation",
                "from_stage": source, "to_stage": target,
                "pre_stage_revision": pre_revision, "post_stage_revision": post_revision,
                "pre_tool_manifest_hash": pre_manifest, "post_tool_manifest_hash": post_manifest,
            }
        # Advisory focus changes are durable domain facts, not permission
        # transitions. Explicit representation/energy preparation increments
        # the semantic generation; ordinary focus updates leave it untouched.
        if (effect != "stage_transition" or source != target or source != "artifact"
                or type(pre_revision) is not int or type(post_revision) is not int
                or post_revision not in (pre_revision, pre_revision + 1)
                or before.get("stage_state_hash") == after.get("stage_state_hash")):
            return None
        return {
            "contract": STAGE_TRANSITION_CONTRACT,
            "kind": "artifact_focus" if post_revision == pre_revision else "artifact_preparation",
            "from_stage": source, "to_stage": target,
            "pre_stage_revision": pre_revision, "post_stage_revision": post_revision,
            "pre_tool_manifest_hash": pre_manifest, "post_tool_manifest_hash": post_manifest,
        }
    stage_identity_changed = (
        source != target
        or pre_revision != post_revision
        or pre_manifest != post_manifest
    )
    if not stage_identity_changed:
        return None
    if (
        source not in STAGED_PHASES
        or target not in STAGED_PHASES
        or type(pre_revision) is not int
        or type(post_revision) is not int
        or post_revision != pre_revision + 1
        or not isinstance(pre_manifest, str)
        or not isinstance(post_manifest, str)
        or source == target
    ):
        return None
    if effect == "stage_transition":
        kind = (
            "explicit_forward"
            if STAGED_PHASES.index(target) > STAGED_PHASES.index(source)
            else "explicit_backward"
        )
    elif effect in {"candidate", "candidate_and_derived"}:
        if STAGED_PHASES.index(target) >= STAGED_PHASES.index(source):
            return None
        kind = "automatic_invalidation"
    else:
        return None
    return {
        "contract": STAGE_TRANSITION_CONTRACT,
        "kind": kind,
        "from_stage": source,
        "to_stage": target,
        "pre_stage_revision": pre_revision,
        "post_stage_revision": post_revision,
        "pre_tool_manifest_hash": pre_manifest,
        "post_tool_manifest_hash": post_manifest,
    }


def _representation_catalog(snapshot: Mapping[str, Any]) -> str | None:
    """Identify the two existing artifact input catalogs, never a JSON flag."""
    definitions = snapshot.get("tools")
    if not isinstance(definitions, list) or any(not isinstance(row, Mapping) for row in definitions):
        return None
    names = {row.get("name") for row in definitions}
    if {"gs_patch_whiteboard", "gs_add_character"} <= names and "gs_patch_candidate" not in names:
        return "whiteboard"
    if "gs_patch_candidate" in names and not {"gs_patch_whiteboard", "gs_add_character"} & names:
        return "native"
    return None


def artifact_representation_state_matches(before: object, after: object) -> bool:
    """A candidate import may only change representation/generation and clear its basis.

    Focus, history, energy intent, resource generations and any future fields
    must remain identical. Unexpected mutations still take the UNKNOWN path.
    """
    if (not isinstance(before, dict) or not isinstance(after, dict)
            or before.get("contract") != "artifact-candidate-workflow-v1"
            or before.get("representation") != "whiteboard"
            or type(before.get("stage_revision")) is not int):
        return False
    expected = {**before, "representation": "native", "stage_revision": before["stage_revision"] + 1,
                "calculation_basis": None}
    try:
        return (json.dumps(after, ensure_ascii=False, sort_keys=True, allow_nan=False, separators=(",", ":"))
                == json.dumps(expected, ensure_ascii=False, sort_keys=True, allow_nan=False, separators=(",", ":")))
    except (TypeError, ValueError, RecursionError, UnicodeError):
        return False


def allowed_changes_for_transition(
    effect: str,
    transition: Mapping[str, Any] | None,
) -> frozenset[str]:
    """Return base changes plus a verified deterministic stage consequence."""

    base = ALLOWED_CHANGES.get(effect, frozenset())
    if transition is None:
        return base
    if transition.get("kind") == "artifact_representation":
        return base | frozenset({"stage_revision", "tool_manifest_hash"})
    return base | STAGE_TRANSITION_FIELDS


def is_known_effect(value: object) -> bool:
    return isinstance(value, str) and value in ALL_EFFECTS
