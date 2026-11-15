"""Shared previous→next document revision contract for Agent and App acceptance.

Renderer counters are proposals.  These checks do not confer file write or
candidate approval authority: the owning Store/Application still performs its
file SHA or live-document CAS before committing the proposed value.
"""
from __future__ import annotations

import copy

from genshin_app.whiteboard_identity import board_fingerprint
from genshin_app.whiteboard_schema import MAX_REVISION, _fail, validate_whiteboard
from genshin_runtime.diagnostics import diagnosed

REVISION_CONTRACT = "whiteboard-intent-revision-v1"
IDENTITY_FIELDS = ("kind", "schema_version", "board_id", "version_line")
_ABSENT = object()


def revision_transition_capabilities():
    return {
        "contract_id": REVISION_CONTRACT,
        "minimum": 0, "maximum": MAX_REVISION, "proposal_step": 1,
        "identity_fields": list(IDENTITY_FIELDS),
        "content_excludes": ["revision"],
        "renderer_authority": "optimistic_proposal_only",
        "acceptance_authority": "python_domain_validation_and_owner_cas",
        "batched_proposals": True,
        "business_fingerprint": "unchanged; title and revision remain non-business metadata",
    }


def _content(board):
    return {key: value for key, value in board.items() if key != "revision"}


def _validate_identity(previous, candidate):
    validate_whiteboard(previous)
    validate_whiteboard(candidate)
    for field in IDENTITY_FIELDS:
        if previous[field] != candidate[field]:
            _fail("不能用一次白板编辑替换文档身份或资料版本线", field,
                  "whiteboard_identity_conflict")


@diagnosed('whiteboard', 'prepare-whiteboard-edit-v1', result_metadata=lambda _arguments, result: {
    "board_id": result["board_id"], "revision": result["revision"],
    "intent_fingerprint": board_fingerprint(result)})
def prepare_whiteboard_edit(previous, candidate):
    """Return a detached, single-edit proposal with the canonical next revision."""
    _validate_identity(previous, candidate)
    prepared = copy.deepcopy(candidate)
    prepared["revision"] = previous["revision"] + (_content(previous) != _content(candidate))
    validate_whiteboard(prepared)
    return prepared


@diagnosed('whiteboard', 'validate-whiteboard-revision-transition-v1', result_metadata=lambda _arguments, result: {
    "board_id": result["board_id"], "revision": result["next_revision"],
    "intent_fingerprint": result["after_board_fingerprint"]})
def validate_whiteboard_revision_transition(
    previous, candidate, *, allow_revision_jump=True,
):
    """Validate a proposal; batched GUI edits may jump, but never go backwards.

    An edit followed by undo can leave identical content with a higher revision.
    Title and editor preference changes advance document revision even though
    the existing numerical business fingerprint deliberately excludes them.
    """
    _validate_identity(previous, candidate)
    before, after = previous["revision"], candidate["revision"]
    changed = _content(previous) != _content(candidate)
    if after < before or (changed and after == before):
        _fail("白板内容已变化但方案版本未前进，或提交了较旧版本；请重新载入再编辑",
              "revision", "whiteboard_revision_conflict")
    if not allow_revision_jump and after != before + changed:
        _fail("单次白板编辑的方案版本不一致", "revision", "whiteboard_revision_conflict")
    return {
        "contract_id": REVISION_CONTRACT,
        "board_id": candidate["board_id"],
        "previous_revision": before, "next_revision": after, "changed": changed,
        "fields": sorted(key for key in candidate.keys() | previous.keys() if key != "revision"
                         and candidate.get(key, _ABSENT) != previous.get(key, _ABSENT)),
        "before_board_fingerprint": board_fingerprint(previous),
        "after_board_fingerprint": board_fingerprint(candidate),
    }
